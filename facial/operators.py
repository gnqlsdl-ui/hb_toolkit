"""Operators for the facial edge-bone and DSP generator.

Edge bones
    Each guide set (lips, eyelid .L/.R) is four guides on the mesh — two
    corners, Upper and Lower — plus an optional Pivot. ``hb.facial_build``
    walks the mesh edges corner -> Upper -> corner and corner -> Lower ->
    corner and puts a DEF bone on each point (every vertex, or an even count).
    With "MCH From Pivot" each DEF sits on the tail of an MCH bone that starts
    at the pivot, like a hand-made lip setup.

DSP bones
    A DSP bone sits on one mesh vertex (Copy Location to a one-vertex group)
    and is used as a control's ``custom_shape_transform`` so the widget rides
    the skin. Only the control -> vertex pairs are stored (on the mesh object,
    ``obj["hb_dsp"]``); ``hb.facial_dsp_build`` creates the groups, bones and
    constraints from them and can be re-run at any time.

Everything generated is tagged with a custom property so rebuilding removes
only what the tool made. Bones made by hand are never touched.
"""

import bmesh
import bpy
from bpy.props import EnumProperty
from bpy.types import Operator
from mathutils import Vector

from ..utils.bone_utils import get_active_armature, split_side, flip_side
from ..utils.mesh_path import MeshGraph, resample, centroid
from .properties import GUIDE_SET_ITEMS


ROLE_PROP = "hb_role"          # guide role: corner_a / corner_b / upp / low / pivot
SET_PROP = "hb_facial_set"     # guide set id on guides
GEN_PROP = "hb_facial_gen"     # set id on generated edge bones
DSP_PROP = "hb_facial_dsp"     # flag on generated DSP bones
DSP_KEY = "hb_dsp"             # mesh object property: {control: vertex index}

GUIDE_COLL = "Facial_Guide"
DEF_COLL = "Facial_DEF"
MCH_COLL = "Facial_MCH"
DSP_COLL = "Facial_DSP"

GUIDE_LEN = 0.004
DSP_PREFIX = "DSP-"

ROLES = ("corner_a", "corner_b", "upp", "low", "pivot")

SETS = {
    'LIPS': dict(
        base="Lips", center=True,
        guides=dict(corner_a="Guide-Lip_Corner.L", corner_b="Guide-Lip_Corner.R",
                    upp="Guide-Lip_Upper", low="Guide-Lip_Lower",
                    pivot="Guide-Lip_Pivot"),
        corners=("Lips_corn.L", "Lips_corn.R"),
    ),
}
for _side in (".L", ".R"):
    SETS['LID' + _side.replace(".", "_")] = dict(
        base="Lid", center=False, side=_side,
        guides=dict(corner_a=f"Guide-Eyelid_In{_side}",
                    corner_b=f"Guide-Eyelid_Out{_side}",
                    upp=f"Guide-Eyelid_Upper{_side}",
                    low=f"Guide-Eyelid_Lower{_side}",
                    pivot=f"Guide-Eyelid_Pivot{_side}"),
        corners=(f"Lid_in{_side}", f"Lid_out{_side}"),
    )


# ----------------------------------------------------------------------
# Small helpers
# ----------------------------------------------------------------------
def _bone_coll(arm, name):
    coll = arm.data.collections_all.get(name)
    return coll or arm.data.collections.new(name)


def _set_only_coll(arm, edit_bone, name):
    for c in list(edit_bone.collections):
        c.unassign(edit_bone)
    _bone_coll(arm, name).assign(edit_bone)


class _EditMode:
    """Context manager: make ``arm`` active in EDIT mode, then restore."""

    def __init__(self, context, arm):
        self.context, self.arm = context, arm

    def __enter__(self):
        vl = self.context.view_layer
        self.prev_active = vl.objects.active
        self.prev_mode = self.prev_active.mode if self.prev_active else 'OBJECT'
        if self.prev_active is not None and self.prev_active.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        vl.objects.active = self.arm
        bpy.ops.object.mode_set(mode='EDIT')
        return self.arm.data.edit_bones

    def __exit__(self, *exc):
        bpy.ops.object.mode_set(mode='OBJECT')
        vl = self.context.view_layer
        vl.objects.active = self.prev_active
        if self.prev_active is not None and self.prev_mode != 'OBJECT':
            try:
                bpy.ops.object.mode_set(mode=self.prev_mode)
            except RuntimeError:
                pass
        return False


def _world(arm, bone_name):
    return arm.matrix_world @ arm.data.bones[bone_name].head_local


def _side_of(name):
    return split_side(name)[1]


def _tagged(arm, prop, value=None):
    return [b.name for b in arm.data.bones
            if prop in b and (value is None or b[prop] == value)]


# ----------------------------------------------------------------------
# Edge-bone planning
# ----------------------------------------------------------------------
def _point_names_vertex_mode(spec, which, n_points, mid):
    """One name per path point (corners included) for Every Vertex mode."""
    base = spec["base"]
    ca, cb = spec["corners"]
    names = [None] * n_points
    names[0], names[-1] = ca, cb
    if spec["center"]:
        names[mid] = f"{base}_{which}"
        sa, sb = _side_of(ca), _side_of(cb)
        for k, i in enumerate(range(mid - 1, 0, -1), 1):
            names[i] = f"{base}_{which}{k}{sa}"
        for k, i in enumerate(range(mid + 1, n_points - 1), 1):
            names[i] = f"{base}_{which}{k}{sb}"
    else:
        for k, i in enumerate(range(1, n_points - 1), 1):
            names[i] = f"{base}_{which}{k}{spec['side']}"
    return names


def _plan_line(spec, which, pts, mid, props):
    """Return [(name, world_point)] for one edge line (upper or lower)."""
    if props.count_mode == 'VERTEX':
        names = _point_names_vertex_mode(spec, which, len(pts), mid)
        return list(zip(names, pts))

    base = spec["base"]
    ca, cb = spec["corners"]
    out = [(ca, pts[0]), (cb, pts[-1])]
    if spec["center"]:
        n = props.lip_count
        out.append((f"{base}_{which}", pts[mid]))
        for half, corner in ((pts[:mid + 1][::-1], ca), (pts[mid:], cb)):
            side = _side_of(corner)
            for k, p in enumerate(resample(half, n + 2)[1:-1], 1):
                out.append((f"{base}_{which}{k}{side}", p))
    else:
        n = props.lid_count
        for k, p in enumerate(resample(pts, n + 2)[1:-1], 1):
            out.append((f"{base}_{which}{k}{spec['side']}", p))
    return out


def plan_set(arm, graph, set_id, props):
    """Plan one guide set. Returns ``(points, pivot, report_lines)``.

    ``points`` is a list of ``(name, world_point)`` with corners only once.
    Raises ``KeyError`` when a required guide is missing.
    """
    spec = SETS[set_id]
    g = spec["guides"]
    missing = [g[r] for r in ("corner_a", "corner_b", "upp", "low")
               if g[r] not in arm.data.bones]
    if missing:
        raise KeyError(", ".join(missing))
    vi = {r: graph.nearest(_world(arm, g[r]))
          for r in ("corner_a", "corner_b", "upp", "low")}
    pivot = _world(arm, g["pivot"]) if g["pivot"] in arm.data.bones else None

    points, seen, report = [], set(), []
    for which in ("upp", "low"):
        path, mid, on_bnd = graph.trace(vi["corner_a"], vi[which], vi["corner_b"])
        pts = [graph.co[i] for i in path]
        report.append(f"{set_id} {which}: {len(path)} verts on "
                      f"{'boundary' if on_bnd else 'edge path'}")
        for name, p in _plan_line(spec, which, pts, mid, props):
            if name not in seen:
                seen.add(name)
                points.append((name, p.copy()))
    return points, pivot, report


def _present_sets(arm):
    return [sid for sid, spec in SETS.items()
            if all(spec["guides"][r] in arm.data.bones
                   for r in ("corner_a", "corner_b", "upp", "low"))]


# ----------------------------------------------------------------------
# Guides
# ----------------------------------------------------------------------
def _auto_lid_points(graph, side, cursor):
    """Corner/upper/lower/pivot from the eye-hole rim nearest the cursor."""
    sign = 1.0 if side == ".L" else -1.0
    loops = []
    for loop in graph.boundary_loops():
        pts = [graph.co[i] for i in loop]
        c = centroid(pts)
        if c.x * sign > 0.0 and len(loop) >= 6:
            loops.append(((c - cursor).length, pts, c))
    if not loops:
        return None
    _d, pts, c = min(loops, key=lambda t: t[0])
    inner = min(pts, key=lambda p: abs(p.x))
    outer = max(pts, key=lambda p: abs(p.x))
    radius = (outer - inner).length * 0.5
    return dict(corner_a=inner, corner_b=outer,
                upp=max(pts, key=lambda p: p.z),
                low=min(pts, key=lambda p: p.z),
                pivot=c + Vector((0.0, radius, 0.0)))


def _cursor_guide_points(set_id, cursor):
    if set_id == 'LIPS':
        off = dict(corner_a=(0.025, 0, 0), corner_b=(-0.025, 0, 0),
                   upp=(0, -0.003, 0.002), low=(0, -0.003, -0.002),
                   pivot=(0, 0.08, 0.015))
    else:
        s = 1.0 if set_id.endswith("_L") else -1.0
        off = dict(corner_a=(-0.012 * s, 0, 0), corner_b=(0.012 * s, 0, 0),
                   upp=(0, 0, 0.006), low=(0, 0, -0.006),
                   pivot=(0, 0.012, 0))
    return {r: cursor + Vector(v) for r, v in off.items()}


class HB_OT_facial_add_guides(Operator):
    """Add the guide bones of a facial set. Existing guides are left as they are.
Eyelid guides snap to the eye-hole rim nearest the 3D cursor when the mesh has one"""

    bl_idname = "hb.facial_add_guides"
    bl_label = "Add Guides"
    bl_options = {'REGISTER', 'UNDO'}

    guide_set: EnumProperty(items=GUIDE_SET_ITEMS)

    @classmethod
    def poll(cls, context):
        return get_active_armature(context) is not None

    def execute(self, context):
        props = context.scene.hb_facial
        arm = get_active_armature(context)
        spec = SETS[self.guide_set]
        cursor = context.scene.cursor.location.copy()
        pts = None
        source = "3D cursor"
        if self.guide_set != 'LIPS' and props.mesh:
            pts = _auto_lid_points(MeshGraph(props.mesh), spec["side"], cursor)
            if pts:
                source = f"eye-hole rim on {props.mesh.name}"
        if pts is None:
            pts = _cursor_guide_points(self.guide_set, cursor)

        inv = arm.matrix_world.inverted()
        made, kept = [], []
        with _EditMode(context, arm) as ebs:
            parent = ebs.get(props.parent_bone) if props.parent_bone else None
            for role in ROLES:
                name = spec["guides"][role]
                if name in ebs:
                    kept.append(name)
                    continue
                b = ebs.new(name)
                b.head = inv @ pts[role]
                length = GUIDE_LEN * (3 if role == "pivot" else 1)
                b.tail = b.head + Vector((0.0, 0.0, length))
                b.roll = 0.0
                b.use_deform = False
                b.parent = parent
                _set_only_coll(arm, b, GUIDE_COLL)
                made.append((name, role))
        for name, role in made:
            arm.data.bones[name][ROLE_PROP] = role
            arm.data.bones[name][SET_PROP] = self.guide_set
        msg = f"Added {len(made)} guides from {source}"
        if kept:
            msg += f" ({len(kept)} already existed, not moved)"
        self.report({'INFO'}, msg)
        return {'FINISHED'}


class HB_OT_facial_mirror_guides(Operator):
    """Copy every .L facial guide to .R (mirrored on X). Existing .R guides are overwritten"""

    bl_idname = "hb.facial_mirror_guides"
    bl_label = "Mirror Guides .L -> .R"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return get_active_armature(context) is not None

    def execute(self, context):
        arm = get_active_armature(context)
        sources = [b.name for b in arm.data.bones
                   if SET_PROP in b and _side_of(b.name) == ".L"]
        if not sources:
            self.report({'WARNING'}, "No .L facial guides found")
            return {'CANCELLED'}
        done = []
        with _EditMode(context, arm) as ebs:
            for name in sources:
                base, side = split_side(name)
                dst_name = base + flip_side(side)
                src = ebs[name]
                dst = ebs.get(dst_name) or ebs.new(dst_name)
                dst.head = Vector((-src.head.x, src.head.y, src.head.z))
                dst.tail = Vector((-src.tail.x, src.tail.y, src.tail.z))
                dst.roll = -src.roll
                dst.use_deform = False
                dst.parent = src.parent
                _set_only_coll(arm, dst, GUIDE_COLL)
                done.append((name, dst_name))
        for src, dst in done:
            sb, db = arm.data.bones[src], arm.data.bones[dst]
            db[ROLE_PROP] = sb.get(ROLE_PROP, "")
            set_id = sb.get(SET_PROP, "")
            db[SET_PROP] = set_id[:-1] + "R" if set_id.endswith("_L") else set_id
        self.report({'INFO'}, f"Mirrored {len(done)} guides")
        return {'FINISHED'}


# ----------------------------------------------------------------------
# Edge bones
# ----------------------------------------------------------------------
def _remove_bones(ebs, names):
    for n in names:
        b = ebs.get(n)
        if b is not None:
            ebs.remove(b)


class HB_OT_facial_build(Operator):
    """Build DEF (and MCH) bones along the mesh edges between each set's guides.
Re-running replaces only the bones this tool made"""

    bl_idname = "hb.facial_build"
    bl_label = "Build Edge Bones"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return (get_active_armature(context) is not None
                and context.scene.hb_facial.mesh is not None)

    def execute(self, context):
        props = context.scene.hb_facial
        arm = get_active_armature(context)
        set_ids = _present_sets(arm)
        if not set_ids:
            self.report({'WARNING'}, "No complete guide set (corners + Upper + Lower)")
            return {'CANCELLED'}

        graph = MeshGraph(props.mesh)
        plans, lines = {}, []
        for sid in set_ids:
            try:
                plans[sid] = plan_set(arm, graph, sid, props)
                lines += plans[sid][2]
            except (KeyError, ValueError) as err:
                lines.append(f"{sid}: skipped ({err})")

        inv = arm.matrix_world.inverted()
        built, blocked = [], []
        with _EditMode(context, arm) as ebs:
            old = [n for sid in plans for n in _tagged(arm, GEN_PROP, sid)]
            _remove_bones(ebs, old)
            parent = ebs.get(props.parent_bone) if props.parent_bone else None
            for sid, (points, pivot, _r) in plans.items():
                use_mch = props.make_mch and pivot is not None
                # A set is built whole or not at all: never mix generated
                # bones into a set that hand-made bones already occupy.
                clash = [n for n, _p in points
                         if "DEF-" + n in ebs or (use_mch and "MCH-" + n in ebs)]
                if clash:
                    blocked.append((sid, clash[0], len(clash)))
                    continue
                for name, p in points:
                    def_name, mch_name = "DEF-" + name, "MCH-" + name
                    head = inv @ p
                    owner = parent
                    if use_mch:
                        m = ebs.new(mch_name)
                        m.head = inv @ pivot
                        m.tail = head
                        m.parent = parent
                        m.use_deform = False
                        _set_only_coll(arm, m, MCH_COLL)
                        built.append((mch_name, sid))
                        owner = m
                    d = ebs.new(def_name)
                    d.head = head
                    d.tail = head + Vector((0.0, 0.0, props.def_length))
                    d.roll = 0.0
                    d.parent = owner
                    d.use_deform = True
                    _set_only_coll(arm, d, DEF_COLL)
                    built.append((def_name, sid))
        for name, sid in built:
            arm.data.bones[name][GEN_PROP] = sid

        for line in lines:
            print("[hb facial]", line)
        msg = f"Built {len(built)} bones (replaced {len(old)})"
        for sid, first, count in blocked:
            msg += (f"; {sid} skipped: {count} names already used by "
                    f"hand-made bones (e.g. {first})")
        self.report({'WARNING'} if blocked else {'INFO'}, msg)
        return {'FINISHED'}


class HB_OT_facial_clear(Operator):
    """Delete the edge bones this tool generated. Guides stay"""

    bl_idname = "hb.facial_clear"
    bl_label = "Clear Edge Bones"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return get_active_armature(context) is not None

    def execute(self, context):
        arm = get_active_armature(context)
        names = _tagged(arm, GEN_PROP)
        with _EditMode(context, arm) as ebs:
            _remove_bones(ebs, names)
        self.report({'INFO'}, f"Removed {len(names)} generated bones")
        return {'FINISHED'}


# ----------------------------------------------------------------------
# DSP
# ----------------------------------------------------------------------
def dsp_map(mesh_obj):
    data = mesh_obj.get(DSP_KEY)
    return {k: int(v) for k, v in data.items()} if data else {}


def _write_dsp_map(mesh_obj, mapping):
    mesh_obj[DSP_KEY] = mapping


class HB_OT_facial_dsp_store(Operator):
    """Store the active (or only selected) vertex for the control named in the panel"""

    bl_idname = "hb.facial_dsp_store"
    bl_label = "Store Vertex"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return (obj is not None and obj.type == 'MESH' and obj.mode == 'EDIT'
                and bool(context.scene.hb_facial.dsp_control))

    def execute(self, context):
        obj = context.active_object
        props = context.scene.hb_facial
        bm = bmesh.from_edit_mesh(obj.data)
        active = bm.select_history.active
        if not isinstance(active, bmesh.types.BMVert):
            sel = [v for v in bm.verts if v.select]
            if len(sel) != 1:
                self.report({'WARNING'}, "Select one vertex (or make one active)")
                return {'CANCELLED'}
            active = sel[0]
        mapping = dsp_map(obj)
        mapping[props.dsp_control] = active.index
        _write_dsp_map(obj, mapping)
        self.report({'INFO'}, f"{props.dsp_control} -> vertex {active.index}")
        return {'FINISHED'}


class HB_OT_facial_dsp_store_nearest(Operator):
    """Store, for every selected pose bone, the mesh vertex nearest its rest head"""

    bl_idname = "hb.facial_dsp_store_nearest"
    bl_label = "Store Nearest (Selected)"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        arm = get_active_armature(context)
        return (arm is not None and arm.mode == 'POSE'
                and context.scene.hb_facial.mesh is not None)

    def execute(self, context):
        props = context.scene.hb_facial
        arm = get_active_armature(context)
        bones = [pb for pb in (context.selected_pose_bones or [])
                 if not pb.name.startswith(DSP_PREFIX)]
        if not bones:
            self.report({'WARNING'}, "Select control bones")
            return {'CANCELLED'}
        graph = MeshGraph(props.mesh)
        mapping = dsp_map(props.mesh)
        for pb in bones:
            mapping[pb.name] = graph.nearest(_world(arm, pb.name))
        _write_dsp_map(props.mesh, mapping)
        self.report({'INFO'}, f"Stored {len(bones)} controls on {props.mesh.name}")
        return {'FINISHED'}


class HB_OT_facial_dsp_build(Operator):
    """Create a one-vertex group, a DSP bone (Copy Location to it) and the
custom shape transform for every stored control. Re-running replaces them"""

    bl_idname = "hb.facial_dsp_build"
    bl_label = "Build DSP"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        props = context.scene.hb_facial
        return (get_active_armature(context) is not None
                and props.mesh is not None and DSP_KEY in props.mesh)

    def execute(self, context):
        props = context.scene.hb_facial
        arm = get_active_armature(context)
        mesh = props.mesh
        mapping = dsp_map(mesh)
        nverts = len(mesh.data.vertices)
        todo = {c: v for c, v in mapping.items()
                if c in arm.data.bones and 0 <= v < nverts}
        missing = sorted(set(mapping) - set(todo))

        old = _tagged(arm, DSP_PROP)
        inv = arm.matrix_world.inverted()
        mw = mesh.matrix_world
        built, blocked = [], []
        with _EditMode(context, arm) as ebs:
            _remove_bones(ebs, old)
            parent = ebs.get(props.parent_bone) if props.parent_bone else None
            for ctrl, vi in todo.items():
                name = DSP_PREFIX + ctrl
                if name in ebs:
                    blocked.append(name)
                    continue
                b = ebs.new(name)
                b.head = inv @ (mw @ mesh.data.vertices[vi].co)
                b.tail = b.head + Vector((0.0, 0.0, props.def_length))
                b.roll = 0.0
                b.parent = parent
                b.use_deform = False
                _set_only_coll(arm, b, DSP_COLL)
                built.append((name, ctrl, vi))

        for name, ctrl, vi in built:
            vg = mesh.vertex_groups.get(name)
            if vg is not None:
                mesh.vertex_groups.remove(vg)
            mesh.vertex_groups.new(name=name).add([vi], 1.0, 'REPLACE')
            arm.data.bones[name][DSP_PROP] = 1
            pb = arm.pose.bones[name]
            con = pb.constraints.new('COPY_LOCATION')
            con.name = "HB_DSP"
            con.target = mesh
            con.subtarget = name
            arm.pose.bones[ctrl].custom_shape_transform = pb

        msg = f"Built {len(built)} DSP bones (replaced {len(old)})"
        if missing:
            msg += f"; {len(missing)} stored controls not found (e.g. {missing[0]})"
        if blocked:
            msg += f"; {len(blocked)} skipped, name used by a hand-made bone"
        self.report({'WARNING'} if (missing or blocked) else {'INFO'}, msg)
        return {'FINISHED'}


class HB_OT_facial_dsp_clear(Operator):
    """Delete the generated DSP bones and their vertex groups. Stored vertices are kept"""

    bl_idname = "hb.facial_dsp_clear"
    bl_label = "Clear DSP"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return get_active_armature(context) is not None

    def execute(self, context):
        props = context.scene.hb_facial
        arm = get_active_armature(context)
        names = _tagged(arm, DSP_PROP)
        for pb in arm.pose.bones:
            if pb.custom_shape_transform and pb.custom_shape_transform.name in names:
                pb.custom_shape_transform = None
        with _EditMode(context, arm) as ebs:
            _remove_bones(ebs, names)
        if props.mesh is not None:
            for n in names:
                vg = props.mesh.vertex_groups.get(n)
                if vg is not None:
                    props.mesh.vertex_groups.remove(vg)
        self.report({'INFO'}, f"Removed {len(names)} DSP bones")
        return {'FINISHED'}


class HB_OT_facial_dsp_forget(Operator):
    """Remove the stored vertex of the control named in the panel"""

    bl_idname = "hb.facial_dsp_forget"
    bl_label = "Forget Control"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        props = context.scene.hb_facial
        return props.mesh is not None and bool(props.dsp_control)

    def execute(self, context):
        props = context.scene.hb_facial
        mapping = dsp_map(props.mesh)
        if mapping.pop(props.dsp_control, None) is None:
            self.report({'WARNING'}, f"{props.dsp_control} is not stored")
            return {'CANCELLED'}
        _write_dsp_map(props.mesh, mapping)
        self.report({'INFO'}, f"Forgot {props.dsp_control}")
        return {'FINISHED'}


_classes = (
    HB_OT_facial_add_guides,
    HB_OT_facial_mirror_guides,
    HB_OT_facial_build,
    HB_OT_facial_clear,
    HB_OT_facial_dsp_store,
    HB_OT_facial_dsp_store_nearest,
    HB_OT_facial_dsp_build,
    HB_OT_facial_dsp_clear,
    HB_OT_facial_dsp_forget,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
