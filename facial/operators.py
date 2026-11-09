"""Operators for the facial template generator.

Workflow (one box per step in the Facial tab):

0. Add Markers     Empties marking the root and tip of STR-FACE_UPP and
                   STR-FACE_LOW (drawn as preview lines by overlay.py).
   Build Base      Head bone -> FACE-ROOT, STR/MSTR/TGT-FACE_UPP/LOW,
                   MCH_SKULL, DEF-JAW/MCH-JAW, MSTR-EYE.L/R, MSTR-MOUTH
                   (rain facial rig layout scaled to the Head bone; each
                   squash group is then fitted to its markers if present).
1. Register Edges  Edit Mode: select the lip loop / each eyelid loop and
                   register it into its slot (stored on the mesh object).
   Generate        Guides (corners, Upper, Lower, Pivot) from the loops,
                   refits MSTR-EYE / MSTR-MOUTH, and for the lips builds the
                   Guide_Mesh-Lips ribbon with one HK- empty per point.
2. Build MCH       MCH bones from each pivot to each point, with parents and
                   constraints (lips: Stretch To the HK- empties, corners
                   follow the jaw 50%).
3. Build All       DEF bones on the MCH tails, plus DSP bones for every
                   stored control -> vertex pair.

Everything generated is tagged (``hb_facial_gen`` on bones,
``hb_facial_obj`` on objects) so each step can be re-run and only replaces
what the tool made. Hand-made bones are never touched: a set whose names are
already taken by untagged bones is skipped whole.
"""

import bmesh
import bpy
from bpy.props import EnumProperty
from bpy.types import Operator
from mathutils import Matrix, Vector

from ..utils.bone_utils import get_active_armature, split_side
from ..utils.mesh_path import MeshGraph, edge_loop, split_loop, resample, centroid
from . import template as T


ROLE_PROP = "hb_role"          # guide role: corner_a / corner_b / upp / low / pivot
SET_PROP = "hb_facial_set"     # set id (LIPS / EYE_L / EYE_R) on generated bones
GEN_PROP = "hb_facial_gen"     # stage on generated bones: BASE / GUIDE / MCH / DEF
OBJ_PROP = "hb_facial_obj"     # tag on generated objects (ribbon, empties)
DSP_PROP = "hb_facial_dsp"     # flag on generated DSP bones
EDGE_KEY = "hb_facial_edges"   # mesh object property: {slot: [edge indices]}
DSP_KEY = "hb_dsp"             # mesh object property: {control: vertex index}

GUIDE_COLL = "Facial_Guide"
DSP_COLL = "Facial_DSP"
GUIDE_LEN = 0.004
DSP_PREFIX = "DSP-"
HOOK_PREFIX = "HK-"
ZIP_PREFIX = "HK-Zip-"
RIBBON_NAME = "Guide_Mesh-Lips"
RIBBON_OFFSET = 0.001          # inner row of the ribbon, towards the pivot

ROLES = ("corner_a", "corner_b", "upp", "low", "pivot")

SLOT_ITEMS = [
    ('LIPS', "Lips", "Lip loop (upper and lower line, both corners)"),
    ('EYE_L', "Eye .L", "Left eyelid loop"),
    ('EYE_R', "Eye .R", "Right eyelid loop"),
]

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
    SETS['EYE' + _side.replace(".", "_")] = dict(
        base="Lid", center=False, side=_side,
        guides=dict(corner_a=f"Guide-Eyelid_In{_side}",
                    corner_b=f"Guide-Eyelid_Out{_side}",
                    upp=f"Guide-Eyelid_Upper{_side}",
                    low=f"Guide-Eyelid_Lower{_side}",
                    pivot=f"Guide-Eyelid_Pivot{_side}"),
        corners=(f"Lid_in{_side}", f"Lid_out{_side}"),
    )


# ----------------------------------------------------------------------
# Generic helpers
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


def _tagged(arm, value=None, set_id=None):
    out = []
    for b in arm.data.bones:
        if GEN_PROP not in b:
            continue
        if value is not None and b[GEN_PROP] != value:
            continue
        if set_id is not None and b.get(SET_PROP) != set_id:
            continue
        out.append(b.name)
    return out


def _tag(arm, names, stage, set_id=None):
    for n in names:
        b = arm.data.bones[n]
        b[GEN_PROP] = stage
        if set_id is not None:
            b[SET_PROP] = set_id


def _remove_bones(ebs, names):
    for n in names:
        b = ebs.get(n)
        if b is not None:
            ebs.remove(b)


def _remove_objects(tag_values):
    for obj in [o for o in bpy.data.objects if o.get(OBJ_PROP) in tag_values]:
        data = obj.data
        bpy.data.objects.remove(obj, do_unlink=True)
        if data is not None and data.users == 0 and isinstance(data, bpy.types.Mesh):
            bpy.data.meshes.remove(data)


def _check_mesh(props):
    mesh = props.mesh
    if mesh is None:
        return "Set the Mesh first"
    if mesh.mode == 'EDIT':
        return f"Leave Edit Mode on {mesh.name} first"
    return None


# ----------------------------------------------------------------------
# 0. Base
# ----------------------------------------------------------------------
def head_xform(head_matrix, head_length):
    """Armature-space matrix mapping the rain layout onto the Head bone."""
    a, b = Vector(T.REFERENCE[0]), Vector(T.REFERENCE[1])
    m0 = Matrix.Translation(a) @ bpy.types.Bone.MatrixFromAxisRoll(b - a, 0.0).to_4x4()
    return head_matrix @ Matrix.Scale(head_length / (b - a).length, 4) @ m0.inverted()


def marker_positions(arm):
    """{marker: armature-space position} for the marker empties that exist."""
    inv = arm.matrix_world.inverted()
    return {n: inv @ bpy.data.objects[n].matrix_world.translation
            for n in T.MARKERS if n in bpy.data.objects}


def _fit_group_to_markers(ebs, str_bone, a1, b1, members):
    """Move/rotate/scale a bone group so ``str_bone`` runs from a1 to b1."""
    a0, b0 = ebs[str_bone].head.copy(), ebs[str_bone].tail.copy()
    d0, d1 = b0 - a0, b1 - a1
    if d0.length < 1e-9 or d1.length < 1e-9:
        return
    rot = d0.rotation_difference(d1).to_matrix().to_4x4()
    g = (Matrix.Translation(a1) @ rot
         @ Matrix.Scale(d1.length / d0.length, 4) @ Matrix.Translation(-a0))
    for n in members:
        if n in ebs:
            ebs[n].transform(g)


class HB_OT_facial_add_markers(Operator):
    """Add the marker empties that place STR-FACE_UPP / STR-FACE_LOW
(root and tip of each). Existing markers are not moved"""

    bl_idname = "hb.facial_add_markers"
    bl_label = "Add Markers"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        arm = get_active_armature(context)
        return arm is not None and bool(context.scene.hb_facial.head_bone)

    def execute(self, context):
        props = context.scene.hb_facial
        arm = get_active_armature(context)
        hb = arm.data.bones.get(props.head_bone)
        if hb is None:
            self.report({'ERROR'}, f"Head bone '{props.head_bone}' not found")
            return {'CANCELLED'}
        coll = bpy.data.collections.get(T.MARKER_COLL)
        if coll is None:
            coll = bpy.data.collections.new(T.MARKER_COLL)
            context.scene.collection.children.link(coll)
        xf = arm.matrix_world @ head_xform(hb.matrix_local, hb.length)
        made = 0
        for name, p in T.MARKERS.items():
            if name in bpy.data.objects:
                continue
            e = bpy.data.objects.new(name, None)
            e.empty_display_type = 'SPHERE'
            e.empty_display_size = hb.length * 0.02
            e.show_name = True
            e.show_in_front = True
            e[OBJ_PROP] = "MARKER"
            e.location = xf @ Vector(p)
            coll.objects.link(e)
            made += 1
        props.show_preview = True
        self.report({'INFO'}, f"Added {made} markers in '{T.MARKER_COLL}' "
                    "- move them, then Build Base")
        return {'FINISHED'}


class HB_OT_facial_build_base(Operator):
    """Build the base face bones (rain layout) under the Head bone.
Re-running replaces only the base bones this tool made"""

    bl_idname = "hb.facial_build_base"
    bl_label = "Build Base"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        arm = get_active_armature(context)
        return arm is not None and bool(context.scene.hb_facial.head_bone)

    def execute(self, context):
        props = context.scene.hb_facial
        arm = get_active_armature(context)
        if props.head_bone not in arm.data.bones:
            self.report({'ERROR'}, f"Head bone '{props.head_bone}' not found")
            return {'CANCELLED'}
        old = set(_tagged(arm, 'BASE'))
        clash = [n for n in T.BASE_BONES if n in arm.data.bones and n not in old]
        if clash:
            self.report({'ERROR'}, "Hand-made bones already use base names: "
                        + ", ".join(clash[:5]))
            return {'CANCELLED'}

        markers = marker_positions(arm)
        with _EditMode(context, arm) as ebs:
            # Removing a base bone unparents its children (generated MCH,
            # hand-made controls...). Remember them and re-attach below.
            orphans = {b.name: b.parent.name for b in ebs
                       if b.parent is not None and b.parent.name in old
                       and b.name not in old}
            _remove_bones(ebs, old)
            head = ebs[props.head_bone]
            xform = head_xform(head.matrix, head.length)
            for name, d in T.BASE_BONES.items():
                b = ebs.new(name)
                b.head, b.tail, b.roll = Vector(d["head"]), Vector(d["tail"]), 0.0
                b.transform(xform)
                b.use_deform = d.get("deform", False)
                b.bbone_segments = d.get("bbone", 1)
                _set_only_coll(arm, b, d["coll"])
            for name, d in T.BASE_BONES.items():
                par = d["parent"]
                if par == "@HEAD":
                    par = props.head_bone
                ebs[name].parent = ebs[par] if par else None
                ebs[name].use_connect = False
            for child, par in orphans.items():
                if child in ebs and par in ebs:
                    ebs[child].parent = ebs[par]
            # Markers override the Head-based layout of each squash group.
            fitted = []
            for str_bone, root, tip, members in T.MARKER_GROUPS:
                if root in markers and tip in markers:
                    _fit_group_to_markers(ebs, str_bone, markers[root], markers[tip], members)
                    fitted.append(str_bone)

        _tag(arm, T.BASE_BONES, 'BASE')
        for name, d in T.BASE_BONES.items():
            pb = arm.pose.bones[name]
            for kind, target in d.get("constraints", ()):
                if kind == "ARMATURE":
                    c = pb.constraints.new('ARMATURE')
                    t = c.targets.new()
                    t.target, t.subtarget, t.weight = arm, target, 1.0
                elif kind == "STRETCH_TO":
                    c = pb.constraints.new('STRETCH_TO')
                    c.target, c.subtarget = arm, target
                    c.volume, c.keep_axis = 'VOLUME_XZX', 'SWING_Y'
                    c.rest_length = arm.data.bones[name].length
        msg = f"Built {len(T.BASE_BONES)} base bones (replaced {len(old)})"
        msg += f"; from markers: {', '.join(fitted)}" if fitted else "; no markers, Head layout"
        self.report({'INFO'}, msg)
        return {'FINISHED'}


# ----------------------------------------------------------------------
# 1. Edges -> guides, ribbon
# ----------------------------------------------------------------------
def edge_map(mesh_obj):
    data = mesh_obj.get(EDGE_KEY) if mesh_obj else None
    return {k: [int(i) for i in v] for k, v in data.items()} if data else {}


class HB_OT_facial_register_edges(Operator):
    """Register the selected edges (Edit Mode) as this slot's loop"""

    bl_idname = "hb.facial_register_edges"
    bl_label = "Register Edges"
    bl_options = {'REGISTER', 'UNDO'}

    slot: EnumProperty(items=SLOT_ITEMS)

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == 'MESH' and obj.mode == 'EDIT'

    def execute(self, context):
        obj = context.active_object
        bm = bmesh.from_edit_mesh(obj.data)
        sel = [e.index for e in bm.edges if e.select]
        if not sel:
            self.report({'WARNING'}, "Select the loop's edges first")
            return {'CANCELLED'}
        mapping = edge_map(obj)
        mapping[self.slot] = sel
        obj[EDGE_KEY] = mapping
        props = context.scene.hb_facial
        if props.mesh is None:
            props.mesh = obj
        self.report({'INFO'}, f"{self.slot}: {len(sel)} edges on {obj.name}")
        return {'FINISHED'}


class HB_OT_facial_clear_edges(Operator):
    """Forget this slot's registered edges"""

    bl_idname = "hb.facial_clear_edges"
    bl_label = "Clear Edges"
    bl_options = {'REGISTER', 'UNDO'}

    slot: EnumProperty(items=SLOT_ITEMS)

    def execute(self, context):
        mesh = context.scene.hb_facial.mesh
        mapping = edge_map(mesh)
        if mapping.pop(self.slot, None) is None:
            return {'CANCELLED'}
        mesh[EDGE_KEY] = mapping
        return {'FINISHED'}


def _upper_first(mesh, loop, arc1, arc2, co):
    """Return ``(upper_arc, lower_arc)``.

    Eye holes (boundary loops): the higher arc is the upper lid.
    Closed lip lines are different: with the mouth shut the upper lip's
    inner edge can sit *below* the lower lip's, so instead look at the mesh
    next to each arc (two edge rings, not crossing the loop): the arc whose
    neighborhood rises is the upper lip.
    """
    graph = MeshGraph(mesh)
    inner1, inner2 = arc1[1:-1] or arc1, arc2[1:-1] or arc2
    mean_z = lambda ids: sum(graph.co[i].z for i in ids) / len(ids)
    if all(graph.boundary[i] for i in loop):
        return (arc1, arc2) if mean_z(inner1) >= mean_z(inner2) else (arc2, arc1)
    blocked = set(loop)

    def rise(inner):
        seen, front = set(inner), set(inner)
        for _ in range(2):
            front = {w for u in front for w, _b in graph.neighbors[u]
                     if w not in seen and w not in blocked}
            seen |= front
        ring = [i for i in seen if i not in blocked]
        return mean_z(ring) - mean_z(inner) if ring else 0.0

    return (arc1, arc2) if rise(inner1) >= rise(inner2) else (arc2, arc1)


def analyze_slot(arm, mesh, slot):
    """Split a slot's registered loop into upper / lower arcs.

    Corners, the upper arc and the centers follow the guide bones when they
    exist, otherwise sensible defaults. Returns a dict with world positions.
    """
    spec = SETS[slot]
    g = spec["guides"]
    loop, closed = edge_loop(mesh, edge_map(mesh)[slot])
    if not closed:
        raise ValueError(f"{slot}: register a closed loop")
    mw = mesh.matrix_world
    co = {i: mw @ mesh.data.vertices[i].co for i in loop}

    def guide(role):
        return _world(arm, g[role]) if g[role] in arm.data.bones else None

    def nearest(ids, p):
        return min(ids, key=lambda i: (co[i] - p).length_squared)

    if guide("corner_a") is not None and guide("corner_b") is not None:
        ca, cb = nearest(loop, guide("corner_a")), nearest(loop, guide("corner_b"))
    elif spec["center"]:
        ca = max(loop, key=lambda i: co[i].x)
        cb = min(loop, key=lambda i: co[i].x)
    else:
        ca = min(loop, key=lambda i: abs(co[i].x))
        cb = max(loop, key=lambda i: abs(co[i].x))
    if ca == cb:
        raise ValueError(f"{slot}: both corners land on the same vertex")
    arc1, arc2 = split_loop(loop, ca, cb)

    up_guide = guide("upp")
    if up_guide is not None:
        dist = lambda arc: min((co[i] - up_guide).length for i in arc[1:-1] or arc)
        upper, lower = (arc1, arc2) if dist(arc1) <= dist(arc2) else (arc2, arc1)
    else:
        upper, lower = _upper_first(mesh, loop, arc1, arc2, co)

    def mid_of(arc, role, default_key):
        inner = arc[1:-1] or arc
        p = guide(role)
        return nearest(inner, p) if p is not None else min(inner, key=default_key)

    if spec["center"]:
        upp_mid = mid_of(upper, "upp", lambda i: abs(co[i].x))
        low_mid = mid_of(lower, "low", lambda i: abs(co[i].x))
    else:
        upp_mid = mid_of(upper, "upp", lambda i: -co[i].z)
        low_mid = mid_of(lower, "low", lambda i: co[i].z)

    loop_center = centroid([co[i] for i in loop])
    width = (co[ca] - co[cb]).length
    pivot = guide("pivot")
    if pivot is None:
        if slot == 'LIPS' and T.LIP_UPPER_PARENT in arm.data.bones:
            pivot = _world(arm, T.LIP_UPPER_PARENT)
        else:
            pivot = loop_center + Vector((0.0, width * (1.0 if slot == 'LIPS' else 0.5), 0.0))

    return dict(
        spec=spec, co=co, upper=upper, lower=lower,
        upp_mid=upper.index(upp_mid), low_mid=lower.index(low_mid),
        guides=dict(corner_a=co[ca], corner_b=co[cb], upp=co[upp_mid],
                    low=co[low_mid], pivot=pivot),
    )


def line_points(spec, which, pts, mid, props):
    """Ordered [(name, point)] from corner_a to corner_b for one line."""
    base = spec["base"]
    ca, cb = spec["corners"]
    if props.count_mode == 'VERTEX':
        names = [None] * len(pts)
        names[0], names[-1] = ca, cb
        if spec["center"]:
            names[mid] = f"{base}_{which}"
            sa, sb = _side_of(ca), _side_of(cb)
            for k, i in enumerate(range(mid - 1, 0, -1), 1):
                names[i] = f"{base}_{which}{k}{sa}"
            for k, i in enumerate(range(mid + 1, len(pts) - 1), 1):
                names[i] = f"{base}_{which}{k}{sb}"
        else:
            for k, i in enumerate(range(1, len(pts) - 1), 1):
                names[i] = f"{base}_{which}{k}{spec['side']}"
        return list(zip(names, pts))

    if not spec["center"]:
        inner = resample(pts, props.lid_count + 2)[1:-1]
        return ([(ca, pts[0])]
                + [(f"{base}_{which}{k}{spec['side']}", p) for k, p in enumerate(inner, 1)]
                + [(cb, pts[-1])])
    n = props.lip_count
    sa, sb = _side_of(ca), _side_of(cb)
    half_a = resample(pts[:mid + 1][::-1], n + 2)[1:-1]   # center -> corner a
    half_b = resample(pts[mid:], n + 2)[1:-1]             # center -> corner b
    out = [(ca, pts[0])]
    out += [(f"{base}_{which}{k}{sa}", p) for k, p in reversed(list(enumerate(half_a, 1)))]
    out.append((f"{base}_{which}", pts[mid]))
    out += [(f"{base}_{which}{k}{sb}", p) for k, p in enumerate(half_b, 1)]
    out.append((cb, pts[-1]))
    return out


def slot_lines(arm, mesh, slot, props):
    """(analysis, upper line, lower line); lines are [(name, point)]."""
    info = analyze_slot(arm, mesh, slot)
    co, spec = info["co"], info["spec"]
    upp = line_points(spec, "upp", [co[i] for i in info["upper"]], info["upp_mid"], props)
    low = line_points(spec, "low", [co[i] for i in info["lower"]], info["low_mid"], props)
    return info, upp, low


def _build_ribbon(context, arm, upp, low, pivot):
    """Ribbon mesh along the lip lines + one vertex-parented HK- empty per point."""
    _remove_objects({"LIPS_RIBBON", "LIPS_HOOK"})
    order = list(upp) + [p for p in low[1:-1]]          # corners only once
    index = {name: i for i, (name, _p) in enumerate(order)}
    n = len(order)
    verts = [p.copy() for _n, p in order]
    for _n, p in order:
        inward = (pivot - p)
        inward = inward.normalized() * RIBBON_OFFSET if inward.length else Vector((0, RIBBON_OFFSET, 0))
        verts.append(p + inward)
    faces = []
    for line in (upp, low):
        names = [nm for nm, _p in line]
        for a, b in zip(names, names[1:]):
            ia, ib = index[a], index[b]
            faces.append((ia, ib, ib + n, ia + n))

    me = bpy.data.meshes.new(RIBBON_NAME)
    me.from_pydata([tuple(v) for v in verts], [], faces)
    me.update()
    rib = bpy.data.objects.new(RIBBON_NAME, me)
    rib[OBJ_PROP] = "LIPS_RIBBON"
    coll = arm.users_collection[0] if arm.users_collection else context.scene.collection
    coll.objects.link(rib)
    rib.modifiers.new("Armature", 'ARMATURE').object = arm

    # Zip groups: each point + the nearest point on the opposite line.
    upp_names = {nm for nm, _p in upp}
    for nm, p in order:
        other = low if nm in upp_names else upp
        group = [index[nm]]
        if nm not in (upp[0][0], upp[-1][0]):
            mate = min(other[1:-1] or other, key=lambda t: (t[1] - p).length_squared)[0]
            group.append(index[mate])
        rib.vertex_groups.new(name=ZIP_PREFIX + nm).add(group, 1.0, 'REPLACE')

    for nm, _p in order:
        e = bpy.data.objects.new(HOOK_PREFIX + nm, None)
        e.empty_display_type = 'PLAIN_AXES'
        e.empty_display_size = 0.002
        e[OBJ_PROP] = "LIPS_HOOK"
        coll.objects.link(e)
        e.parent = rib
        e.parent_type = 'VERTEX'
        i = index[nm]
        e.parent_vertices = (i, i, i)
        e.location = (0.0, 0.0, 0.0)
    return len(order)


class HB_OT_facial_generate(Operator):
    """Create the guides from the registered loops (existing guides are kept),
refit MSTR-EYE / MSTR-MOUTH and build the lip ribbon with its HK- empties"""

    bl_idname = "hb.facial_generate"
    bl_label = "Generate Guides"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return get_active_armature(context) is not None

    def execute(self, context):
        props = context.scene.hb_facial
        arm = get_active_armature(context)
        err = _check_mesh(props)
        if err:
            self.report({'ERROR'}, err)
            return {'CANCELLED'}
        mesh = props.mesh
        slots = [s for s in SETS if s in edge_map(mesh)]
        if not slots:
            self.report({'WARNING'}, "Register at least one loop first")
            return {'CANCELLED'}

        try:
            infos = {s: analyze_slot(arm, mesh, s) for s in slots}
        except ValueError as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}

        inv = arm.matrix_world.inverted()
        made = []
        base = set(_tagged(arm, 'BASE'))
        with _EditMode(context, arm) as ebs:
            for slot, info in infos.items():
                for role in ROLES:
                    name = SETS[slot]["guides"][role]
                    if name in ebs:
                        continue
                    b = ebs.new(name)
                    b.head = inv @ info["guides"][role]
                    b.tail = b.head + Vector((0.0, 0.0, GUIDE_LEN * (3 if role == "pivot" else 1)))
                    b.roll = 0.0
                    b.use_deform = False
                    _set_only_coll(arm, b, GUIDE_COLL)
                    made.append((name, role, slot))
        for name, role, slot in made:
            arm.data.bones[name][ROLE_PROP] = role
        _tag(arm, [m[0] for m in made], 'GUIDE')
        for name, _r, slot in made:
            arm.data.bones[name][SET_PROP] = slot

        # Refit eye / mouth masters from the (possibly edited) guides.
        refit = {}
        for slot in slots:
            g = SETS[slot]["guides"]
            if slot == 'LIPS':
                refit[T.MOUTH_MASTER] = (_world(arm, g["upp"]) + _world(arm, g["low"])) * 0.5
            else:
                refit[T.EYE_MASTER.format(side=SETS[slot]["side"])] = _world(arm, g["pivot"])
        refit = {k: v for k, v in refit.items() if k in base}
        if refit:
            with _EditMode(context, arm) as ebs:
                for name, p in refit.items():
                    b = ebs[name]
                    vec = b.tail - b.head
                    b.head = inv @ p
                    b.tail = b.head + vec

        msg = f"Added {len(made)} guides"
        if refit:
            msg += f", refit {', '.join(refit)}"
        if 'LIPS' in slots:
            info, upp, low = slot_lines(arm, mesh, 'LIPS', props)
            count = _build_ribbon(context, arm, upp, low, _world(arm, SETS['LIPS']['guides']['pivot']))
            msg += f", ribbon + {count} HK- empties"
        self.report({'INFO'}, msg)
        return {'FINISHED'}


# ----------------------------------------------------------------------
# 2. MCH
# ----------------------------------------------------------------------
def _parent_for(arm_bones, slot, name, spec, fallback):
    if slot == 'LIPS':
        if name in spec["corners"]:
            want = T.JAW_HALF
        elif "_low" in name:
            want = T.LIP_LOWER_PARENT
        else:
            want = T.LIP_UPPER_PARENT
    else:
        want = T.EYE_MASTER.format(side=spec["side"])
    if want in arm_bones:
        return want
    return fallback if fallback in arm_bones else None


class HB_OT_facial_build_mch(Operator):
    """Build the MCH bones (pivot -> each point) with parents and constraints.
Re-running replaces the MCH and DEF bones this tool made"""

    bl_idname = "hb.facial_build_mch"
    bl_label = "Build MCH"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return get_active_armature(context) is not None

    def execute(self, context):
        props = context.scene.hb_facial
        arm = get_active_armature(context)
        err = _check_mesh(props)
        if err:
            self.report({'ERROR'}, err)
            return {'CANCELLED'}
        mesh = props.mesh
        plans, notes = {}, []
        for slot, spec in SETS.items():
            if slot not in edge_map(mesh):
                continue
            if any(spec["guides"][r] not in arm.data.bones for r in ROLES):
                notes.append(f"{slot}: run Generate Guides first")
                continue
            try:
                _info, upp, low = slot_lines(arm, mesh, slot, props)
            except ValueError as e:
                notes.append(str(e))
                continue
            seen, points = set(), []
            for nm, p in upp + low:
                if nm not in seen:
                    seen.add(nm)
                    points.append((nm, p))
            plans[slot] = (points, _world(arm, spec["guides"]["pivot"]))
        if not plans:
            self.report({'WARNING'}, "; ".join(notes) or "Nothing to build")
            return {'CANCELLED'}

        inv = arm.matrix_world.inverted()
        old = {s: set(_tagged(arm, 'MCH', s)) | set(_tagged(arm, 'DEF', s)) for s in plans}
        bones = arm.data.bones
        built, skipped = {}, []
        need_jaw_half = 'LIPS' in plans and T.JAW_BONE in bones
        with _EditMode(context, arm) as ebs:
            for slot in plans:
                _remove_bones(ebs, old[slot])
            if need_jaw_half:
                _remove_bones(ebs, [T.JAW_HALF])
                jaw = ebs[T.JAW_BONE]
                h = ebs.new(T.JAW_HALF)
                h.head, h.tail, h.roll = jaw.head.copy(), jaw.tail.copy(), jaw.roll
                h.parent = jaw.parent
                h.use_deform = False
                _set_only_coll(arm, h, T.COLL_MCH)
            names = set(ebs.keys())
            for slot, (points, pivot) in plans.items():
                spec = SETS[slot]
                clash = [n for n, _p in points if "MCH-" + n in names or "DEF-" + n in names]
                if clash:
                    skipped.append((slot, clash[0], len(clash)))
                    continue
                built[slot] = []
                for nm, p in points:
                    m = ebs.new("MCH-" + nm)
                    m.head, m.tail, m.roll = inv @ pivot, inv @ p, 0.0
                    par = _parent_for(ebs, slot, nm, spec, props.parent_bone)
                    m.parent = ebs[par] if par else None
                    m.use_deform = False
                    _set_only_coll(arm, m, T.COLL_MCH)
                    built[slot].append(m.name)

        if need_jaw_half:
            _tag(arm, [T.JAW_HALF], 'MCH', 'LIPS')
            c = arm.pose.bones[T.JAW_HALF].constraints.new('COPY_TRANSFORMS')
            c.name = "Follow_Jaw_50"
            c.target, c.subtarget = arm, T.JAW_BONE
            c.owner_space = c.target_space = 'LOCAL'
            c.influence = 0.5
        hooks_missing = 0
        for slot, names in built.items():
            _tag(arm, names, 'MCH', slot)
            if slot != 'LIPS':
                continue
            for n in names:
                hook = bpy.data.objects.get(HOOK_PREFIX + n[len("MCH-"):])
                if hook is None:
                    hooks_missing += 1
                    continue
                c = arm.pose.bones[n].constraints.new('STRETCH_TO')
                c.name = "Stretch_HK"
                c.target = hook
                c.volume = 'NO_VOLUME'
                c.rest_length = arm.data.bones[n].length

        msg = f"Built MCH: " + ", ".join(f"{s} {len(n)}" for s, n in built.items())
        for slot, first, count in skipped:
            msg += f"; {slot} skipped ({count} names used by hand-made bones, e.g. {first})"
        if hooks_missing:
            msg += f"; {hooks_missing} lip MCH without HK- empty (run Generate Guides)"
        if notes:
            msg += "; " + "; ".join(notes)
        self.report({'WARNING'} if (skipped or hooks_missing or notes) else {'INFO'}, msg)
        return {'FINISHED'}


# ----------------------------------------------------------------------
# 3. Build All (DEF + DSP)
# ----------------------------------------------------------------------
def dsp_map(mesh_obj):
    data = mesh_obj.get(DSP_KEY) if mesh_obj else None
    return {k: int(v) for k, v in data.items()} if data else {}


def build_def(context, arm, def_length):
    """DEF bone on the tail of every generated MCH point bone."""
    mch = [n for n in _tagged(arm, 'MCH') if n != T.JAW_HALF]
    old = _tagged(arm, 'DEF')
    sets = {n: arm.data.bones[n].get(SET_PROP) for n in mch}
    made = []
    with _EditMode(context, arm) as ebs:
        _remove_bones(ebs, old)
        for n in mch:
            name = "DEF-" + n[len("MCH-"):]
            if name in ebs:
                continue
            m = ebs[n]
            d = ebs.new(name)
            d.head = m.tail.copy()
            d.tail = d.head + Vector((0.0, 0.0, def_length))
            d.roll = 0.0
            d.parent = m
            d.use_deform = True
            _set_only_coll(arm, d, T.COLL_DEF)
            made.append((name, sets[n]))
    for name, s in made:
        _tag(arm, [name], 'DEF', s)
    return len(made), len(old)


def build_dsp(context, arm, mesh, length, fallback_parent):
    """One-vertex group + DSP bone (Copy Location) per stored control."""
    mapping = dsp_map(mesh)
    nverts = len(mesh.data.vertices)
    todo = {c: v for c, v in mapping.items() if c in arm.data.bones and 0 <= v < nverts}
    missing = sorted(set(mapping) - set(todo))
    old = [b.name for b in arm.data.bones if DSP_PROP in b]
    # A DSP bone lives under the control's own parent so it follows the
    # same face region before Copy Location takes over.
    parents = {c: (arm.data.bones[c].parent.name if arm.data.bones[c].parent
                   else fallback_parent) for c in todo}
    inv = arm.matrix_world.inverted()
    mw = mesh.matrix_world
    built, blocked = [], []
    with _EditMode(context, arm) as ebs:
        _remove_bones(ebs, old)
        for ctrl, vi in todo.items():
            name = DSP_PREFIX + ctrl
            if name in ebs:
                blocked.append(name)
                continue
            b = ebs.new(name)
            b.head = inv @ (mw @ mesh.data.vertices[vi].co)
            b.tail = b.head + Vector((0.0, 0.0, length))
            b.roll = 0.0
            par = parents[ctrl]
            b.parent = ebs.get(par) if par else None
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
        con.target, con.subtarget = mesh, name
        arm.pose.bones[ctrl].custom_shape_transform = pb
    return len(built), len(old), missing, blocked


class HB_OT_facial_build_all(Operator):
    """Build the DEF bones on every generated MCH, then the DSP bones for every
stored control. Re-running replaces only what this tool made"""

    bl_idname = "hb.facial_build_all"
    bl_label = "Build All"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return get_active_armature(context) is not None

    def execute(self, context):
        props = context.scene.hb_facial
        arm = get_active_armature(context)
        if not _tagged(arm, 'MCH'):
            self.report({'WARNING'}, "Run Build MCH first")
            return {'CANCELLED'}
        made, replaced = build_def(context, arm, props.def_length)
        msg = f"DEF {made} (replaced {replaced})"
        level = {'INFO'}
        if props.mesh is not None and dsp_map(props.mesh):
            n, old, missing, blocked = build_dsp(context, arm, props.mesh,
                                                 props.def_length, props.parent_bone)
            msg += f", DSP {n} (replaced {old})"
            if missing:
                msg += f"; {len(missing)} stored controls not found (e.g. {missing[0]})"
                level = {'WARNING'}
            if blocked:
                msg += f"; {len(blocked)} DSP names used by hand-made bones"
                level = {'WARNING'}
        self.report(level, msg)
        return {'FINISHED'}


class HB_OT_facial_dsp_build(Operator):
    """Build only the DSP bones for every stored control"""

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
        n, old, missing, blocked = build_dsp(context, arm, props.mesh,
                                             props.def_length, props.parent_bone)
        msg = f"Built {n} DSP bones (replaced {old})"
        if missing:
            msg += f"; {len(missing)} stored controls not found (e.g. {missing[0]})"
        if blocked:
            msg += f"; {len(blocked)} skipped, name used by a hand-made bone"
        self.report({'WARNING'} if (missing or blocked) else {'INFO'}, msg)
        return {'FINISHED'}


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
        obj[DSP_KEY] = mapping
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
        from mathutils import kdtree
        props = context.scene.hb_facial
        arm = get_active_armature(context)
        bones = [pb for pb in (context.selected_pose_bones or [])
                 if not pb.name.startswith(DSP_PREFIX)]
        if not bones:
            self.report({'WARNING'}, "Select control bones")
            return {'CANCELLED'}
        mesh = props.mesh
        mw = mesh.matrix_world
        kd = kdtree.KDTree(len(mesh.data.vertices))
        for v in mesh.data.vertices:
            kd.insert(mw @ v.co, v.index)
        kd.balance()
        mapping = dsp_map(mesh)
        for pb in bones:
            mapping[pb.name] = kd.find(_world(arm, pb.name))[1]
        mesh[DSP_KEY] = mapping
        self.report({'INFO'}, f"Stored {len(bones)} controls on {mesh.name}")
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
        props.mesh[DSP_KEY] = mapping
        return {'FINISHED'}


# ----------------------------------------------------------------------
# Clear
# ----------------------------------------------------------------------
CLEAR_ITEMS = [
    ('MARKER', "Markers", "Marker empties"),
    ('BASE', "Base", "Base face bones"),
    ('GUIDE', "Guides", "Guide bones, lip ribbon and HK- empties"),
    ('MCH', "MCH", "Generated MCH bones (and the DEF bones on them)"),
    ('DEF', "DEF + DSP", "Generated DEF and DSP bones and the DSP vertex groups"),
]


class HB_OT_facial_clear(Operator):
    """Delete what one step generated. Hand-made bones are never touched"""

    bl_idname = "hb.facial_clear"
    bl_label = "Clear"
    bl_options = {'REGISTER', 'UNDO'}

    stage: EnumProperty(items=CLEAR_ITEMS)

    @classmethod
    def poll(cls, context):
        return get_active_armature(context) is not None

    def execute(self, context):
        props = context.scene.hb_facial
        if self.stage == 'MARKER':
            _remove_objects({"MARKER"})
            coll = bpy.data.collections.get(T.MARKER_COLL)
            if coll is not None and not coll.all_objects:
                bpy.data.collections.remove(coll)
            return {'FINISHED'}
        arm = get_active_armature(context)
        names = set(_tagged(arm, self.stage))
        if self.stage == 'MCH':
            names |= set(_tagged(arm, 'DEF'))
        dsp = []
        if self.stage == 'DEF':
            dsp = [b.name for b in arm.data.bones if DSP_PROP in b]
            for pb in arm.pose.bones:
                if pb.custom_shape_transform and pb.custom_shape_transform.name in dsp:
                    pb.custom_shape_transform = None
            names |= set(dsp)
        with _EditMode(context, arm) as ebs:
            _remove_bones(ebs, names)
        if self.stage == 'GUIDE':
            _remove_objects({"LIPS_RIBBON", "LIPS_HOOK"})
        if dsp and props.mesh is not None:
            for n in dsp:
                vg = props.mesh.vertex_groups.get(n)
                if vg is not None:
                    props.mesh.vertex_groups.remove(vg)
        self.report({'INFO'}, f"Removed {len(names)} bones")
        return {'FINISHED'}


_classes = (
    HB_OT_facial_add_markers,
    HB_OT_facial_build_base,
    HB_OT_facial_register_edges,
    HB_OT_facial_clear_edges,
    HB_OT_facial_generate,
    HB_OT_facial_build_mch,
    HB_OT_facial_build_all,
    HB_OT_facial_dsp_build,
    HB_OT_facial_dsp_store,
    HB_OT_facial_dsp_store_nearest,
    HB_OT_facial_dsp_forget,
    HB_OT_facial_clear,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
