"""Operators for the roll mechanism generator.

Workflow (two buttons):

* ``HB_OT_roll_create_guides`` stamps the main ``Guide-{seg}`` bone at the
  3D cursor. The user rotates / rolls it onto the mesh.
* ``HB_OT_roll_fit_guides`` creates the Roll_Back/Front/In/Out (+ GRP)
  guides around that oriented main bone.
* ``HB_OT_roll_build`` reads the placed guides and builds Root / Ctrl /
  System / DEF. Chaining follows *guide parenting*: a child ``Root`` is
  parented to the parent segment's ``DEF-`` bone.
* ``HB_OT_roll_clear_build`` deletes the generated bones and leaves only
  the ``Guide-`` bones.

``HB_OT_roll_mirror`` mirrors a built side onto the opposite side.
"""

import bpy
from bpy.types import Operator
from bpy.props import StringProperty
from mathutils import Vector

from ..utils.bone_utils import get_active_armature, split_side, flip_side
from . import template as T


WGTS_COLLECTION = "WGTS"

# Bone custom-property keys.
ROLE_PROP = "hb_role"        # per guide: its function ("root"/"main"/...)
TYPE_PROP = "hb_type"        # on the DEF-main bone: mechanism generator id
COMPONENT_PROP = "component"  # free-text instance label
DEF_MAIN_PROP = "def_main"    # flags the DEF-generating main guide


# ----------------------------------------------------------------------
# Guide parsing helpers
# ----------------------------------------------------------------------
def parse_guide_name(name: str):
    """Return ``(segment, side)`` for any guide bone name, else ``None``."""
    base, side = split_side(name)
    if base.startswith("GRP_Guide-"):
        return base[len("GRP_Guide-"):], side
    if base.startswith("Guide-"):
        rest = base[len("Guide-"):]
        for suf in ("_Roll_Back", "_Roll_Front", "_Roll_In", "_Roll_Out"):
            if rest.endswith(suf):
                return rest[: -len(suf)], side
        return rest, side
    return None


_CTRL_SUFFIXES = ("_Roll_Back", "_Roll_Front", "_Twist", "_FK")
_SYS_SUFFIXES = ("_Roll_Back", "_Roll_Front", "_Roll_In", "_Roll_Out",
                 "_Rot_y_Local", "_Rot_y")


def parse_component_name(name: str):
    """Return ``(segment, side)`` for a guide or generated component bone."""
    if name.endswith(".001"):
        name = name[:-4]
    parsed = parse_guide_name(name)
    if parsed:
        return parsed
    base, side = split_side(name)
    prefixes = (
        "Root-", "GRP_Ctrl-", "GRP_System-", "GRP_DEF-", "DEF-",
        "Transfer_System-", "Ctrl-", "System-",
    )
    for prefix in prefixes:
        if not base.startswith(prefix):
            continue
        rest = base[len(prefix):]
        if prefix == "Ctrl-":
            for suf in _CTRL_SUFFIXES:
                if rest.endswith(suf):
                    return rest[: -len(suf)], side
        elif prefix == "System-":
            for suf in _SYS_SUFFIXES:
                if rest.endswith(suf):
                    return rest[: -len(suf)], side
        return rest, side
    return None


def role_root_of(bone):
    """Climb the parent chain to the guide tagged ``hb_role == 'root'``."""
    cur = bone
    while cur is not None:
        if cur.get(ROLE_PROP) == T.ROLE_ROOT:
            return cur
        cur = cur.parent
    return None


def collect_target_segments(arm, context):
    """Return a sorted list of ``(segment, side)`` from selected bones.

    Identity is read from the bone name, falling back to the name of the
    role-tagged ``root`` guide so that renamed (but tagged) guides still
    resolve to their segment.
    """
    mode = arm.mode
    if mode == 'EDIT':
        bones = [b for b in arm.data.edit_bones if b.select]
    elif mode == 'POSE':
        bones = [pb.bone for pb in (context.selected_pose_bones or [])]
    else:
        bones = []
    segs = set()
    for b in bones:
        parsed = parse_component_name(b.name)
        if not parsed:
            root = role_root_of(b)
            if root is not None:
                parsed = parse_guide_name(root.name)
        if parsed:
            segs.add(parsed)
    return sorted(segs)


# Required guide roles (build fails without these) and optional ones.
# GRP_Guide (role root) is optional so Clear Build can strip it and leave
# only the Guide- bones; Build still finds the rest by name.
_ROLE_TEMPLATES = (
    (T.ROLE_BACK, T.GUIDE_BACK),
    (T.ROLE_FRONT, T.GUIDE_FRONT),
    (T.ROLE_IN, T.GUIDE_IN),
    (T.ROLE_MAIN, T.GUIDE_MAIN),
)
_ROLE_TEMPLATES_OPT = (
    (T.ROLE_ROOT, T.GUIDE_ROOT),
    (T.ROLE_OUT, T.GUIDE_OUT),
)
_ALL_GUIDE_ROLES = (T.ROLE_BACK, T.ROLE_FRONT, T.ROLE_IN, T.ROLE_OUT,
                    T.ROLE_MAIN)


def find_guide_bones(arm, seg, side):
    """Return the dict of guide EditBones for a segment, or None.

    The GRP root is located by name when present; the other guides are
    matched by ``hb_role`` among the root's children (rename-safe). Missing
    roles fall back to the name templates. ``root`` (GRP_Guide) and ``out``
    are optional so Clear Build can strip GRP_Guide and still rebuild.
    """
    ebs = arm.data.edit_bones
    root = ebs.get(T.GUIDE_ROOT.format(seg=seg, side=side))
    found = {}
    if root is not None:
        found[T.ROLE_ROOT] = root
        if root.get(ROLE_PROP) != T.ROLE_ROOT:
            root[ROLE_PROP] = T.ROLE_ROOT
        rname = root.name
        for child in ebs:
            if child.parent is None or child.parent.name != rname:
                continue
            role = child.get(ROLE_PROP)
            if role in _ALL_GUIDE_ROLES:
                found.setdefault(role, child)
    # Required guides: fill gaps from the legacy name templates, stamp role.
    for role, tmpl in _ROLE_TEMPLATES:
        if role in found:
            continue
        eb = ebs.get(tmpl.format(seg=seg, side=side))
        if eb is None:
            return None
        eb[ROLE_PROP] = role
        found[role] = eb
    # Optional guides: include by name if present, else leave absent.
    for role, tmpl in _ROLE_TEMPLATES_OPT:
        if role in found:
            continue
        eb = ebs.get(tmpl.format(seg=seg, side=side))
        if eb is not None:
            eb[ROLE_PROP] = role
            found[role] = eb
    return found


def parent_segment_from_guides(arm, seg, side):
    """Walk up the guide's parent chain to find a parent guide segment.

    Returns the parent segment name (same side) or None for a root segment.
    """
    ebs = arm.data.edit_bones
    # The limb chain is defined on the main guide (Guide-{seg}); the GRP_Guide
    # root is typically left unparented, so walk the main guide first.
    for start_name in (T.GUIDE_MAIN.format(seg=seg, side=side),
                       T.GUIDE_ROOT.format(seg=seg, side=side)):
        start = ebs.get(start_name)
        if start is None:
            continue
        p = start.parent
        while p is not None:
            parsed = _guide_identity(p)
            if parsed and parsed[0] != seg and parsed[1] == side:
                return parsed[0]
            p = p.parent
    return None


def _guide_identity(bone):
    """Return ``(segment, side)`` for a guide bone via name, then role tag.

    Falls back to the bone's role-tagged ``root`` so that renamed but tagged
    guides still resolve to their owning segment.
    """
    parsed = parse_guide_name(bone.name)
    if parsed:
        return parsed
    if bone.get(ROLE_PROP):
        root = role_root_of(bone)
        if root is not None:
            return parse_guide_name(root.name)
    return None


# ----------------------------------------------------------------------
# Widget helpers
# ----------------------------------------------------------------------
def _ensure_wgts_collection(context):
    coll = bpy.data.collections.get(WGTS_COLLECTION)
    if coll is None:
        coll = bpy.data.collections.new(WGTS_COLLECTION)
        context.scene.collection.children.link(coll)
        layer = context.view_layer.layer_collection.children.get(WGTS_COLLECTION)
        if layer:
            layer.exclude = True
    return coll


def _ensure_widget_mesh(context, name, verts, edges):
    obj = bpy.data.objects.get(name)
    if obj is not None:
        return obj
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata([Vector(v) for v in verts], edges, [])
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    obj.hide_viewport = True
    obj.hide_render = True
    _ensure_wgts_collection(context).objects.link(obj)
    return obj


def _widget_object_for(context, widget):
    if widget["mesh"] == T.WGT_CIRCLE_NAME:
        return _ensure_widget_mesh(context, T.WGT_CIRCLE_NAME,
                                   T.WGT_CIRCLE_VERTS, T.WGT_CIRCLE_EDGES)
    if widget["mesh"] == T.WGT_TWIST_NAME:
        return _ensure_widget_mesh(context, T.WGT_TWIST_NAME,
                                   T.WGT_TWIST_VERTS, T.WGT_TWIST_EDGES)
    return _ensure_widget_mesh(context, T.WGT_ARROW_NAME,
                               T.WGT_ARROW_VERTS, T.WGT_ARROW_EDGES)


def _ensure_bone_collection(arm, name):
    coll = arm.data.collections.get(name)
    if coll is None:
        coll = arm.data.collections.new(name)
    return coll


def _remove_generated_bones(arm, seg, side):
    """Delete generated bones for a segment, leaving the Guide- set intact."""
    ebs = arm.data.edit_bones
    for name in T.generated_names(seg, side):
        old = ebs.get(name)
        if old:
            ebs.remove(old)


_ROLL_GUIDE_TEMPLATES = (
    T.GUIDE_ROOT, T.GUIDE_BACK, T.GUIDE_FRONT, T.GUIDE_IN, T.GUIDE_OUT,
)
_ROLL_GUIDE_ROLES = (
    T.ROLE_ROOT, T.ROLE_BACK, T.ROLE_FRONT, T.ROLE_IN, T.ROLE_OUT,
)


def _remove_roll_guides(arm, seg, side):
    """Delete GRP_Guide and Roll_* guides, leaving the main Guide- bone."""
    ebs = arm.data.edit_bones
    main = find_main_guide(arm, seg, side)
    if main is not None:
        main.parent = None
    names = [tmpl.format(seg=seg, side=side) for tmpl in _ROLL_GUIDE_TEMPLATES]
    guides = find_guide_bones(arm, seg, side)
    if guides:
        for role in _ROLL_GUIDE_ROLES:
            eb = guides.get(role)
            if eb is not None:
                names.append(eb.name)
    for name in dict.fromkeys(names):
        old = ebs.get(name)
        if old:
            ebs.remove(old)


def _cursor_in_armature(arm, context):
    """3D cursor location in the armature's local space."""
    return arm.matrix_world.inverted() @ context.scene.cursor.location


def _main_frame(main):
    """Return ``(joint, seg_dir, up, side, length)`` from a placed main guide.

    ``up`` is the bone's local Z (roll), ``side`` is local X. ``seg_dir`` is
    head -> tail. Returns ``None`` if the bone is degenerate.
    """
    joint = main.head.copy()
    vec = main.tail - main.head
    length = vec.length
    if length < 1e-6:
        return None
    mat = main.matrix.to_3x3()
    return (joint, vec / length, mat.col[2].normalized(),
            mat.col[0].normalized(), length)


def _ensure_guide_bone(ebs, name, role, comp_name):
    """Get or create a non-deform guide bone and stamp its role / component."""
    eb = ebs.get(name)
    if eb is None:
        eb = ebs.new(name)
        eb.use_deform = False
    eb[ROLE_PROP] = role
    if comp_name:
        eb[COMPONENT_PROP] = comp_name
    return eb


def _assign_guide_collection(arm, names, seg):
    coll = _ensure_bone_collection(arm, f"{seg}_Guides")
    bpy.ops.object.mode_set(mode='OBJECT')
    for n in names:
        b = arm.data.bones.get(n)
        if b:
            coll.assign(b)
    bpy.ops.object.mode_set(mode='EDIT')


def find_main_guide(arm, seg, side):
    """Return the main Guide- EditBone for a segment, or None."""
    ebs = arm.data.edit_bones
    eb = ebs.get(T.GUIDE_MAIN.format(seg=seg, side=side))
    if eb is not None:
        return eb
    for b in ebs:
        if b.get(ROLE_PROP) != T.ROLE_MAIN:
            continue
        if parse_guide_name(b.name) == (seg, side):
            return b
    return None


def place_roll_guides_on_main(arm, main, seg, side):
    """Create or reposition GRP / Roll_* guides around ``main``'s frame.

    Offsets follow ``GUIDE_RATIOS`` scaled by the main guide's length, so the
    cross-section matches the bone the user just oriented.
    """
    frame = _main_frame(main)
    if frame is None:
        return []
    joint, seg_dir, up, side_dir, length = frame
    r = T.GUIDE_RATIOS
    nup = -up
    comp_name = main.get(COMPONENT_PROP, "")
    ebs = arm.data.edit_bones

    specs = (
        (T.GUIDE_ROOT, T.ROLE_ROOT,
         joint, joint + seg_dir * (r["grp_len"] * length), up),
        (T.GUIDE_BACK, T.ROLE_BACK,
         joint + up * (r["roll_offset"] * length),
         joint + up * (r["roll_offset"] * length) + nup * (r["back_len"] * length),
         -seg_dir),
        (T.GUIDE_FRONT, T.ROLE_FRONT,
         joint + nup * (r["roll_offset"] * length),
         joint + nup * (r["roll_offset"] * length) + nup * (r["front_len"] * length),
         -seg_dir),
        (T.GUIDE_IN, T.ROLE_IN,
         joint + side_dir * (r["in_head"] * length),
         joint + side_dir * (r["in_head"] * length) + nup * (r["handle_len"] * length),
         -seg_dir),
        (T.GUIDE_OUT, T.ROLE_OUT,
         joint - side_dir * (r["out_head"] * length),
         joint - side_dir * (r["out_head"] * length) + nup * (r["handle_len"] * length),
         -seg_dir),
    )

    created = []
    bones = {}
    for tmpl, role, head, tail, z_target in specs:
        name = tmpl.format(seg=seg, side=side)
        eb = _ensure_guide_bone(ebs, name, role, comp_name)
        eb.head = head
        eb.tail = tail
        eb.align_roll(z_target)
        bones[role] = eb
        created.append(name)

    grp = bones[T.ROLE_ROOT]
    grp.parent = None
    for role in (T.ROLE_BACK, T.ROLE_FRONT, T.ROLE_IN, T.ROLE_OUT):
        bones[role].parent = grp
        bones[role].use_connect = False
    main.parent = grp
    main.use_connect = False
    created.append(main.name)
    return created


# ----------------------------------------------------------------------
# Create Guides
# ----------------------------------------------------------------------
class HB_OT_roll_create_guides(Operator):
    bl_idname = "hb.roll_create_guides"
    bl_label = "Add Component"
    bl_description = ("Create the main Guide- bone at the 3D cursor. Rotate "
                      "and roll it to match the mesh, then Place Roll Guides")
    bl_options = {'REGISTER', 'UNDO'}

    comp_type: StringProperty(
        name="Component Type",
        description="Mechanism type stamped onto the generated main guide "
                    "('hb_type'); also drives DEF generation",
        default="footbank",
    )

    @classmethod
    def poll(cls, context):
        return get_active_armature(context) is not None

    def execute(self, context):
        arm = get_active_armature(context)
        props = context.scene.hb_roll
        seg = props.guide_segment.strip()
        side = props.guide_side
        size = props.size
        comp_name = props.component_name.strip()
        if not seg:
            self.report({'WARNING'}, "Enter a segment name first")
            return {'CANCELLED'}

        origin = _cursor_in_armature(arm, context)
        main_name = T.GUIDE_MAIN.format(seg=seg, side=side)

        prev_mode = arm.mode
        bpy.ops.object.mode_set(mode='EDIT')
        ebs = arm.data.edit_bones

        if ebs.get(main_name) is not None:
            bpy.ops.object.mode_set(mode=prev_mode)
            self.report({'WARNING'}, f"{main_name} already exists")
            return {'CANCELLED'}

        # Default main: head at the cursor, along -Z, length = Size.
        # The user then rotates / rolls this bone before Place Roll Guides.
        main = ebs.new(main_name)
        main.head = origin.copy()
        main.tail = origin + Vector((0.0, 0.0, -1.0)) * size
        main.roll = 0.0
        main.use_deform = False
        main[ROLE_PROP] = T.ROLE_MAIN
        main[DEF_MAIN_PROP] = True
        main[TYPE_PROP] = self.comp_type
        if comp_name:
            main[COMPONENT_PROP] = comp_name

        for eb in ebs:
            hit = eb.name == main_name
            eb.select = eb.select_head = eb.select_tail = hit
        ebs.active = main
        _assign_guide_collection(arm, [main_name], seg)

        self.report({'INFO'},
                    f"Created {main_name} at cursor - rotate/roll, then Place Roll Guides")
        return {'FINISHED'}


# ----------------------------------------------------------------------
# Place Roll Guides (from oriented main)
# ----------------------------------------------------------------------
class HB_OT_roll_fit_guides(Operator):
    bl_idname = "hb.roll_fit_guides"
    bl_label = "Place Roll Guides"
    bl_description = ("Create or reposition Roll_Back/Front/In/Out around the "
                      "selected main Guide- bone using its rotation and roll")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return get_active_armature(context) is not None

    def execute(self, context):
        arm = get_active_armature(context)
        prev_mode = arm.mode
        bpy.ops.object.mode_set(mode='EDIT')

        targets = collect_target_segments(arm, context)
        if not targets:
            bpy.ops.object.mode_set(mode=prev_mode)
            self.report({'WARNING'}, "Select the main Guide- bone first")
            return {'CANCELLED'}

        placed = []
        missing = []
        created_names = []
        for seg, side in targets:
            main = find_main_guide(arm, seg, side)
            if main is None:
                missing.append(f"{seg}{side}")
                continue
            names = place_roll_guides_on_main(arm, main, seg, side)
            if not names:
                missing.append(f"{seg}{side}")
                continue
            _assign_guide_collection(arm, names, seg)
            created_names.extend(names)
            placed.append(f"{seg}{side}")

        ebs = arm.data.edit_bones
        for eb in ebs:
            hit = eb.name in created_names
            eb.select = eb.select_head = eb.select_tail = hit

        if prev_mode != 'EDIT':
            bpy.ops.object.mode_set(mode=prev_mode)
        if missing and not placed:
            self.report({'WARNING'}, "No main Guide- found: " + ", ".join(missing))
            return {'CANCELLED'}
        self.report({'INFO'}, f"Placed roll guides: {', '.join(placed)}")
        return {'FINISHED'}


# ----------------------------------------------------------------------
# Build
# ----------------------------------------------------------------------
class HB_OT_roll_build(Operator):
    bl_idname = "hb.roll_build"
    bl_label = "Build"
    bl_description = ("Build the roll (twist) network from the placed Guide- "
                      "bones of the selected segment(s)")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return get_active_armature(context) is not None

    def execute(self, context):
        arm = get_active_armature(context)
        props = context.scene.hb_roll
        size = props.size

        prev_mode = arm.mode
        bpy.ops.object.mode_set(mode='EDIT')

        targets = collect_target_segments(arm, context)
        if not targets:
            bpy.ops.object.mode_set(mode=prev_mode)
            self.report({'WARNING'}, "Select Guide- bone(s) of the segment(s) to build")
            return {'CANCELLED'}

        # Chaining follows guide parenting.
        parent_seg = {}
        for seg, side in targets:
            if find_guide_bones(arm, seg, side):
                parent_seg[(seg, side)] = parent_segment_from_guides(arm, seg, side)

        # Build roots first so chained children can find their parents.
        order = sorted(targets, key=lambda x: parent_seg.get(x) is not None)

        created_all = []
        skipped = []
        for seg, side in order:
            guides = find_guide_bones(arm, seg, side)
            if not guides:
                skipped.append(f"{seg}{side}")
                continue
            pseg = parent_seg.get((seg, side))
            # Component label flows from the placed guides onto every bone the
            # build stamps out, so a built segment stays grouped/identifiable.
            comp = (guides["main"].get(COMPONENT_PROP)
                    or guides["root"].get(COMPONENT_PROP))
            created = self._build_segment_bones(arm, seg, side, guides, pseg, size)
            created_all.append((seg, side, pseg, created, comp))

        bpy.ops.object.mode_set(mode='OBJECT')
        for seg, side, pseg, created, comp in created_all:
            self._apply_constraints(arm, seg, side, is_root=(pseg is None))
            self._apply_drivers(arm, seg, side, is_root=(pseg is None))
            self._assign_collection(arm, seg, side, created)
            self._apply_colors(arm, seg, side, is_root=(pseg is None))
            self._tag_component(arm, created, comp)
            if props.create_widgets:
                self._apply_widgets(context, arm, seg, side, size, is_root=(pseg is None))

        # Reselect built bones (handy for an immediate mirror).
        bpy.ops.object.mode_set(mode='EDIT')
        for eb in arm.data.edit_bones:
            eb.select = eb.select_head = eb.select_tail = False
        for created in (c[3] for c in created_all):
            for n in created:
                eb = arm.data.edit_bones.get(n)
                if eb:
                    eb.select = eb.select_head = eb.select_tail = True
        if prev_mode != 'EDIT':
            bpy.ops.object.mode_set(mode=prev_mode)

        if skipped:
            self.report({'WARNING'}, "Missing guide set(s): " + ", ".join(skipped))
        seg_names = ", ".join(f"{s}{sd}" for s, sd, _, _, _ in created_all)
        self.report({'INFO'}, f"Built roll network: {seg_names or 'none'}")
        return {'FINISHED'}

    # -- bone creation -------------------------------------------------
    def _build_segment_bones(self, arm, seg, side, guides, pseg, size):
        ebs = arm.data.edit_bones
        main = guides["main"]
        joint = main.head.copy()
        main_tail = main.tail.copy()
        # Frame: the segment axis comes from the main guide (head -> tail), but
        # the cross-section axes (up/side) are taken from where the roll guides
        # actually sit, NOT from the main guide's roll. This makes the build
        # match the placed cross-section in all three axes regardless of how the
        # main guide happens to be rolled:
        #   seg  = main head -> tail
        #   up   = toward the Back guide   (Roll_Back/Front offset + roll plane)
        #   side = toward the Out guide    (In -> Out axis)
        seg_vec = main_tail - joint
        seg_dir = seg_vec.normalized() if seg_vec.length > 1e-6 else Vector((0, 1, 0))

        def _ortho(vec):
            """Project ``vec`` perpendicular to the segment axis and normalize."""
            v = vec - seg_dir * vec.dot(seg_dir)
            return v.normalized() if v.length > 1e-6 else vec.normalized()

        # Roll_Out guide head (falls back to the legacy In-guide tail).
        out_head = (guides["out"].head.copy() if "out" in guides
                    else guides["in"].tail.copy())

        up = _ortho(guides["back"].head - joint)
        if "out" in guides:
            side_dir = _ortho(guides["out"].head - joint)
        else:
            side_dir = _ortho(joint - guides["in"].head)

        anchors = {
            T.ANCHOR_JOINT: joint,
            T.ANCHOR_BACK: guides["back"].head.copy(),
            T.ANCHOR_FRONT: guides["front"].head.copy(),
            T.ANCHOR_IN_HEAD: guides["in"].head.copy(),
            T.ANCHOR_IN_TAIL: guides["in"].tail.copy(),
            T.ANCHOR_OUT: out_head,
        }
        dirs = {T.DIR_SEG: seg_dir, T.DIR_UP: up, T.DIR_NUP: -up, T.DIR_SIDE: side_dir}

        specs = T.generated_bones(is_root=(pseg is None))

        _remove_generated_bones(arm, seg, side)

        created = []
        for spec in specs:
            name = spec["name"].format(seg=seg, side=side)
            eb = ebs.new(name)
            head = anchors[spec["anchor"]].copy()
            length = spec["length"]
            if length == T.LEN_MAIN:
                tail = main_tail.copy()
            elif length == T.LEN_TO_OUT:
                tail = out_head.copy()
            elif length == T.LEN_IN:
                tail = guides["in"].tail.copy()
            else:
                tail = head + dirs[spec["dir"]] * (length * size)
            eb.head = head
            eb.tail = tail
            # Roll matters: the Transformation constraints work in LOCAL space,
            # so a bone's local axes (set by roll) must match the reference.
            # Segment-aligned bones point local Z at `up`; the perpendicular
            # helper/control bones point local Z at -seg.
            if spec["dir"] == T.DIR_SEG:
                eb.align_roll(up)
            else:
                eb.align_roll(-seg_dir)
            eb.use_deform = bool(spec.get("deform", False))
            created.append(name)

        for spec in specs:
            eb = ebs.get(spec["name"].format(seg=seg, side=side))
            par = spec["parent"]
            if par is None:
                eb.parent = None
            elif par == "@def":
                eb.parent = ebs.get(
                    "DEF-{seg}{side}".format(seg=pseg, side=side)) if pseg else None
            elif par == "@ctrl":
                eb.parent = ebs.get(
                    T.CTRL_TARGETS["fk"].format(seg=pseg, side=side)) if pseg else None
            else:
                eb.parent = ebs.get(par.format(seg=seg, side=side))
        return created

    # -- constraints ---------------------------------------------------
    def _apply_constraints(self, arm, seg, side, is_root):
        for spec in T.generated_bones(is_root=is_root):
            cons = spec["constraints"]
            if not cons:
                continue
            pb = arm.pose.bones.get(spec["name"].format(seg=seg, side=side))
            if pb is None:
                continue
            for c in list(pb.constraints):
                pb.constraints.remove(c)
            for ctype, cfg in cons:
                self._add_constraint(arm, pb, seg, side, ctype, cfg)

    def _add_constraint(self, arm, pb, seg, side, ctype, cfg):
        subtarget = T.CTRL_TARGETS[cfg["target"]].format(seg=seg, side=side)
        if ctype == "TRANSFORM":
            c = pb.constraints.new('TRANSFORM')
            c.target = arm
            c.subtarget = subtarget
            c.map_from = 'ROTATION'
            c.map_to = 'ROTATION'
            c.target_space = 'LOCAL'
            c.owner_space = 'LOCAL'
            c.mix_mode_rot = 'ADD'
            c.map_to_x_from = 'X'
            c.map_to_y_from = 'Y'
            c.map_to_z_from = 'Z'
            fa = cfg["from_axis"].lower()
            ta = cfg["to_axis"].lower()
            # Route the output axis from the (possibly different) input axis.
            setattr(c, f"map_to_{ta}_from", cfg["from_axis"].upper())
            frm = cfg["from"]
            to = cfg["to"]
            # Place each value in the min slot (negative input) or max slot
            # (positive input). from/to may differ in sign (inverted pass).
            slot = "max" if frm >= 0 else "min"
            setattr(c, f"from_{slot}_{fa}_rot", frm)
            setattr(c, f"to_{slot}_{ta}_rot", to)
            return
        c = pb.constraints.new(ctype)
        c.target = arm
        c.subtarget = subtarget
        c.target_space = cfg["space"]
        c.owner_space = cfg["space"]
        if "use" in cfg:
            c.use_x, c.use_y, c.use_z = cfg["use"]

    # -- drivers + custom properties -----------------------------------
    def _apply_drivers(self, arm, seg, side, is_root):
        """Stamp FK custom properties, rotation modes and the Rot_y_Local driver."""
        for spec in T.generated_bones(is_root=is_root):
            pb = arm.pose.bones.get(spec["name"].format(seg=seg, side=side))
            if pb is None:
                continue
            for prop in spec.get("props", ()):  # custom properties on the bone
                pb[prop["name"].format(seg=seg, side=side)] = float(prop["default"])
            rmode = spec.get("rotation_mode")
            if rmode:
                pb.rotation_mode = rmode
            drv = spec.get("driver")
            if drv:
                self._make_driver(arm, pb, seg, side, drv)

    @staticmethod
    def _make_driver(arm, pb, seg, side, drv):
        path = drv["data_path"]
        idx = drv["index"]
        pb.driver_remove(path, idx)
        fc = pb.driver_add(path, idx)
        d = fc.driver
        d.type = 'SCRIPTED'
        prop_name = drv["prop"].format(seg=seg, side=side)
        var_name = "".join(c if c.isalnum() else "_" for c in prop_name)
        var = d.variables.new()
        var.name = var_name
        var.type = 'SINGLE_PROP'
        tg = var.targets[0]
        tg.id_type = 'OBJECT'
        tg.id = arm
        prop_bone = T.CTRL_TARGETS[drv["prop_bone"]].format(seg=seg, side=side)
        tg.data_path = 'pose.bones["%s"]["%s"]' % (prop_bone, prop_name)
        d.expression = "%s*%s" % (var_name, drv["factor"])

    # -- component tag -------------------------------------------------
    @staticmethod
    def _tag_component(arm, created, comp):
        if not comp:
            return
        for n in created:
            b = arm.data.bones.get(n)
            if b:
                b[COMPONENT_PROP] = comp

    # -- collections ---------------------------------------------------
    def _assign_collection(self, arm, seg, side, created):
        coll = _ensure_bone_collection(arm, seg)
        for n in created:
            b = arm.data.bones.get(n)
            if b:
                coll.assign(b)

    # -- widgets -------------------------------------------------------
    def _apply_widgets(self, context, arm, seg, side, size, is_root):
        for spec in T.generated_bones(is_root=is_root):
            widget = spec["widget"]
            if not widget:
                continue
            pb = arm.pose.bones.get(spec["name"].format(seg=seg, side=side))
            if pb is None:
                continue
            pb.custom_shape = _widget_object_for(context, widget)
            pb.use_custom_shape_bone_size = widget.get("bone_size", True)
            pb.custom_shape_scale_xyz = tuple(s * size for s in widget["scale"])
            pb.custom_shape_translation = widget["translation"]
            pb.custom_shape_rotation_euler = widget["rotation"]
            cstf_key = widget.get("cstf")
            if cstf_key:
                cstf_name = T.CSTF_TARGETS[cstf_key].format(seg=seg, side=side)
                pb.custom_shape_transform = arm.pose.bones.get(cstf_name)

    # -- bone colors ---------------------------------------------------
    def _apply_colors(self, arm, seg, side, is_root):
        for spec in T.generated_bones(is_root=is_root):
            color = spec.get("color")
            if not color:
                continue
            b = arm.data.bones.get(spec["name"].format(seg=seg, side=side))
            if b is None:
                continue
            b.color.palette = 'CUSTOM'
            b.color.custom.normal = color[0]
            b.color.custom.select = color[1]
            b.color.custom.active = color[2]


# ----------------------------------------------------------------------
# Clear Build (keep guides)
# ----------------------------------------------------------------------
class HB_OT_roll_clear_build(Operator):
    bl_idname = "hb.roll_clear_build"
    bl_label = "Clear Build"
    bl_description = ("Delete generated Root / Ctrl / System / DEF / GRP bones "
                      "of the selected component(s) and leave only Guide- bones")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return get_active_armature(context) is not None

    def execute(self, context):
        arm = get_active_armature(context)
        prev_mode = arm.mode
        bpy.ops.object.mode_set(mode='EDIT')

        targets = collect_target_segments(arm, context)
        if not targets:
            bpy.ops.object.mode_set(mode=prev_mode)
            self.report({'WARNING'},
                        "Select Guide- (or built) bone(s) of the component(s)")
            return {'CANCELLED'}

        kept = []
        ebs = arm.data.edit_bones
        for seg, side in targets:
            _remove_generated_bones(arm, seg, side)
            grp = ebs.get(T.GUIDE_ROOT.format(seg=seg, side=side))
            if grp:
                ebs.remove(grp)
            for eb in ebs:
                parsed = parse_guide_name(eb.name)
                if parsed == (seg, side):
                    kept.append(eb.name)

        for eb in arm.data.edit_bones:
            hit = eb.name in kept
            eb.select = eb.select_head = eb.select_tail = hit
        if prev_mode != 'EDIT':
            bpy.ops.object.mode_set(mode=prev_mode)

        names = ", ".join(f"{s}{sd}" for s, sd in targets)
        self.report({'INFO'}, f"Cleared build, guides kept: {names}")
        return {'FINISHED'}


class HB_OT_roll_clear_roll_guides(Operator):
    bl_idname = "hb.roll_clear_roll_guides"
    bl_label = "Clear Roll Guides"
    bl_description = ("Delete Roll_Back/Front/In/Out and GRP_Guide of the "
                      "selected component(s). Keeps the main Guide- bone "
                      "(also strips a previous Build so you can Place again)")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return get_active_armature(context) is not None

    def execute(self, context):
        arm = get_active_armature(context)
        prev_mode = arm.mode
        bpy.ops.object.mode_set(mode='EDIT')

        targets = collect_target_segments(arm, context)
        if not targets:
            bpy.ops.object.mode_set(mode=prev_mode)
            self.report({'WARNING'},
                        "Select Guide- (or built) bone(s) of the component(s)")
            return {'CANCELLED'}

        kept = []
        for seg, side in targets:
            _remove_generated_bones(arm, seg, side)
            _remove_roll_guides(arm, seg, side)
            main = find_main_guide(arm, seg, side)
            if main is not None:
                kept.append(main.name)

        for eb in arm.data.edit_bones:
            hit = eb.name in kept
            eb.select = eb.select_head = eb.select_tail = hit
        if prev_mode != 'EDIT':
            bpy.ops.object.mode_set(mode=prev_mode)

        names = ", ".join(f"{s}{sd}" for s, sd in targets)
        self.report({'INFO'}, f"Cleared roll guides, main kept: {names}")
        return {'FINISHED'}


# ----------------------------------------------------------------------
# Mirror
# ----------------------------------------------------------------------
class HB_OT_roll_mirror(Operator):
    bl_idname = "hb.roll_mirror"
    bl_label = "Mirror"
    bl_description = ("Mirror the selected bones to the opposite side "
                      "(symmetrize: flips names, subtargets and shapes)")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return get_active_armature(context) is not None

    def execute(self, context):
        arm = get_active_armature(context)
        props = context.scene.hb_roll
        prev_mode = arm.mode
        bpy.ops.object.mode_set(mode='EDIT')

        selected = [b.name for b in arm.data.edit_bones if b.select]
        if not selected:
            bpy.ops.object.mode_set(mode=prev_mode)
            self.report({'WARNING'}, "Select the bones to mirror first")
            return {'CANCELLED'}

        # Determine source/destination side from the selection.
        src_side = ""
        for n in selected:
            _, s = split_side(n)
            if s:
                src_side = s
                break
        dst_side = flip_side(src_side)

        bpy.ops.armature.symmetrize(direction=props.mirror_direction)

        # symmetrize copies custom_shape_transform pointers without flipping
        # the side. Re-point them on the destination side by name (reading
        # the freshly mirrored values back is unreliable within this call).
        bpy.ops.object.mode_set(mode='OBJECT')
        if dst_side:
            self._fix_shape_transforms(arm, dst_side)

        if prev_mode != 'OBJECT':
            bpy.ops.object.mode_set(mode=prev_mode)
        self.report({'INFO'}, f"Mirrored {len(selected)} bone(s)")
        return {'FINISHED'}

    # Maps a control suffix to its custom_shape_transform target (CSTF key).
    _CSTF_BY_SUFFIX = (
        ("_Roll_Back", "roll_in"),
        ("_Roll_Front", "roll_in"),
        ("_Twist", "grp_system"),
        ("_FK", "def"),
    )

    @classmethod
    def _fix_shape_transforms(cls, arm, dst_side):
        """Re-point each destination-side control widget at its own target bone.

        symmetrize copies custom_shape_transform pointers without flipping the
        side, so set them by name per control type (FK->DEF, Twist->Root,
        Roll->System_Roll_In).
        """
        for pb in arm.pose.bones:
            base, side = split_side(pb.name)
            if side != dst_side or not base.startswith("Ctrl-"):
                continue
            inner = base[len("Ctrl-"):]
            seg = cstf_key = None
            for suf, key in cls._CSTF_BY_SUFFIX:
                if inner.endswith(suf):
                    seg = inner[: -len(suf)]
                    cstf_key = key
                    break
            if seg is None:
                continue
            tgt_name = T.CSTF_TARGETS[cstf_key].format(seg=seg, side=dst_side)
            tgt = arm.pose.bones.get(tgt_name)
            if tgt:
                pb.custom_shape_transform = tgt


# ----------------------------------------------------------------------
# Component tagging (edit-mode custom properties)
# ----------------------------------------------------------------------
def selected_bone_items(arm, context):
    """Return the editable bone datablocks selected in the current mode.

    Custom properties set on these transfer to the final ``Bone`` data.
    """
    if arm.mode == 'EDIT':
        return [eb for eb in arm.data.edit_bones if eb.select]
    if arm.mode == 'POSE':
        return [pb.bone for pb in (context.selected_pose_bones or [])]
    return [b for b in arm.data.bones if b.select]


def active_bone_item(arm, context):
    """Return the active bone datablock for the current mode, or ``None``."""
    if arm.mode == 'EDIT':
        return arm.data.edit_bones.active
    if arm.mode == 'POSE':
        pb = context.active_pose_bone
        return pb.bone if pb else None
    return arm.data.bones.active


class HB_OT_roll_set_component(Operator):
    bl_idname = "hb.roll_set_component"
    bl_label = "Assign Component"
    bl_description = ("Write the component name into the 'component' custom "
                      "property of every selected bone")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return get_active_armature(context) is not None

    def execute(self, context):
        arm = get_active_armature(context)
        name = context.scene.hb_roll.component_name.strip()
        if not name:
            self.report({'WARNING'}, "Enter a component name first")
            return {'CANCELLED'}
        bones = selected_bone_items(arm, context)
        if not bones:
            self.report({'WARNING'}, "Select at least one bone")
            return {'CANCELLED'}
        for b in bones:
            b[COMPONENT_PROP] = name
        self.report({'INFO'},
                    f"Set component '{name}' on {len(bones)} bone(s)")
        return {'FINISHED'}


class HB_OT_roll_mark_def_main(Operator):
    bl_idname = "hb.roll_mark_def_main"
    bl_label = "Mark DEF Main"
    bl_description = ("Flag the selected bone(s) as the component root that "
                      "generates the DEF bone: sets 'def_main' and writes the "
                      "chosen mechanism type into 'hb_type'")
    bl_options = {'REGISTER', 'UNDO'}

    clear: bpy.props.BoolProperty(
        name="Clear",
        description="Remove the DEF-main flag and type instead of setting it",
        default=False,
    )

    @classmethod
    def poll(cls, context):
        return get_active_armature(context) is not None

    def execute(self, context):
        arm = get_active_armature(context)
        comp_type = context.scene.hb_roll.component_type
        bones = selected_bone_items(arm, context)
        if not bones:
            self.report({'WARNING'}, "Select at least one bone")
            return {'CANCELLED'}
        count = 0
        for b in bones:
            if self.clear:
                removed = False
                for key in (DEF_MAIN_PROP, TYPE_PROP):
                    if key in b:
                        del b[key]
                        removed = True
                count += int(removed)
            else:
                b[DEF_MAIN_PROP] = True
                b[TYPE_PROP] = comp_type
                count += 1
        verb = "Cleared" if self.clear else "Marked"
        self.report({'INFO'}, f"{verb} DEF-main on {count} bone(s)")
        return {'FINISHED'}


class HB_OT_roll_clear_component(Operator):
    bl_idname = "hb.roll_clear_component"
    bl_label = "Clear Component"
    bl_description = ("Remove the 'component', 'def_main' and 'hb_type' custom "
                      "properties from the selected bones (keeps 'hb_role')")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return get_active_armature(context) is not None

    def execute(self, context):
        arm = get_active_armature(context)
        bones = selected_bone_items(arm, context)
        if not bones:
            self.report({'WARNING'}, "Select at least one bone")
            return {'CANCELLED'}
        for b in bones:
            for key in (COMPONENT_PROP, DEF_MAIN_PROP, TYPE_PROP):
                if key in b:
                    del b[key]
        self.report({'INFO'}, f"Cleared tags from {len(bones)} bone(s)")
        return {'FINISHED'}


_classes = (HB_OT_roll_create_guides, HB_OT_roll_fit_guides,
            HB_OT_roll_build, HB_OT_roll_clear_build,
            HB_OT_roll_clear_roll_guides, HB_OT_roll_mirror,
            HB_OT_roll_set_component, HB_OT_roll_mark_def_main,
            HB_OT_roll_clear_component)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
