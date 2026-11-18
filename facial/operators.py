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
                   Ribbon_Mesh-Lips (HK- empty per point) and
                   Ribbon_Mesh-Eyelid.L/R (HK- vertex group per point).
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
from bpy.props import BoolProperty, EnumProperty, StringProperty
from bpy.types import Operator
from mathutils import Matrix, Vector

from ..utils.bone_utils import get_active_armature, split_side
from ..utils.mesh_path import MeshGraph, edge_loop, split_loop, resample, centroid
from . import template as T
from .parts import (brow_constraints, build_parts, mesh_chain_points, part_bone_names,
                    resolve_points)
from . import markers as M


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
RIBBON_LIPS = "Ribbon_Mesh-Lips"
RIBBON_EYELID = "Ribbon_Mesh-Eyelid{side}"
RIBBON_OFFSET = 0.0013         # second row of the ribbon, straight forward (-Y), as storm
RIBBON_TAGS = {"LIPS_RIBBON", "LIPS_HOOK", "EYE_RIBBON.L", "EYE_RIBBON.R"}
MASK_GROUP = "MSK-line"

ROLES = ("corner_a", "corner_b", "upp", "low", "pivot")

SLOT_ITEMS = [
    ('LIPS', "입술", "입술 루프 (위/아래 라인, 양쪽 입꼬리)"),
    ('EYE_L', "눈 .L", "왼쪽 눈꺼풀 루프"),
    ('EYE_R', "눈 .R", "오른쪽 눈꺼풀 루프"),
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


def facial_rig(context):
    """The rig to build into: the active armature (remembered in the panel),
    otherwise the remembered one - so buttons work while a marker is active."""
    props = context.scene.hb_facial
    arm = get_active_armature(context)
    if arm is not None:
        if props.dsp_armature != arm:
            try:                      # not allowed while drawing / in poll
                props.dsp_armature = arm
            except (AttributeError, RuntimeError):
                pass
        return arm
    arm = props.dsp_armature
    return arm if arm is not None and arm.name in context.view_layer.objects else None


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


def obj_world(obj):
    """World position of an object, also when it isn't evaluated (markers in
    a viewport-disabled collection keep a stale, zero matrix_world)."""
    m = obj.matrix_basis
    p = obj.parent
    if p is not None:
        m = obj_world_matrix(p) @ obj.matrix_parent_inverse @ m
    return m.translation.copy()


def obj_world_matrix(obj):
    m = obj.matrix_basis
    if obj.parent is not None and obj.parent_type == 'OBJECT':
        m = obj_world_matrix(obj.parent) @ obj.matrix_parent_inverse @ m
    elif obj.parent is not None:
        return obj.matrix_world.copy()
    return m


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


def _clear_constraints(arm, names):
    """Drop old constraints on rebuilt bones.

    Deleting a bone and creating one with the same name in the same Edit
    Mode session keeps Blender's pose channel - constraints included - so
    without this every rebuild would stack another copy of each constraint.
    """
    for n in names:
        pb = arm.pose.bones.get(n)
        if pb is not None:
            for c in list(pb.constraints):
                pb.constraints.remove(c)


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
        return "먼저 메쉬를 지정하세요"
    if mesh.mode == 'EDIT':
        return f"먼저 {mesh.name}의 에디트 모드를 종료하세요"
    return None


# ----------------------------------------------------------------------
# 0. Base
# ----------------------------------------------------------------------
def head_xform(head_matrix, head_length):
    """Armature-space matrix mapping the rain layout onto the Head bone."""
    a, b = Vector(T.REFERENCE[0]), Vector(T.REFERENCE[1])
    m0 = Matrix.Translation(a) @ bpy.types.Bone.MatrixFromAxisRoll(b - a, 0.0).to_4x4()
    return head_matrix @ Matrix.Scale(head_length / (b - a).length, 4) @ m0.inverted()


def head_landmarks(obj, z_floor):
    """Chin / crown z, front / back y and cranium half width of a head mesh.

    Only vertices above ``z_floor`` count (keeps the body out). The chin is
    the lowest centre-line vertex that is well in front (60% of the way to
    the face front), so the neck below it is ignored.
    """
    mw = obj.matrix_world
    pts = [p for p in (mw @ v.co for v in obj.data.vertices) if p.z > z_floor]
    if len(pts) < 10:
        return None
    front = min(p.y for p in pts)
    cx = sum(p.x for p in pts) / len(pts)
    mid = [p for p in pts if abs(p.x - cx) < 0.01 and p.y < front * 0.6]
    if not mid:
        return None
    chin, top = min(p.z for p in mid), max(p.z for p in pts)
    band = [p for p in pts if chin + 0.70 * (top - chin) < p.z < chin + 0.85 * (top - chin)]
    hw = max(abs(p.x - cx) for p in band) if band else None
    if not hw or top - chin < 1e-4:
        return None
    return dict(chin=chin, top=top, front=front, back=max(p.y for p in pts),
                half_width=hw, center_x=cx)


def landmark_map(r, c):
    """World-point mapper: rain head landmarks ``r`` -> character ``c``."""
    sz = (c["top"] - c["chin"]) / (r["top"] - r["chin"])
    sy = (c["back"] - c["front"]) / (r["back"] - r["front"])
    sx = c["half_width"] / r["half_width"]

    def f(p):
        p = Vector(p)
        return Vector((c["center_x"] + p.x * sx,
                       c["front"] + (p.y - r["front"]) * sy,
                       c["chin"] + (p.z - r["chin"]) * sz))
    return f


def marker_defaults(arm, props, hb):
    """Function rain point -> world default spot for a marker.

    Fitted to the head mesh when the Mesh is set (and readable), otherwise
    the rain layout scaled to the Head bone.
    """
    if props.mesh is not None and props.mesh.mode != 'EDIT':
        floor = (arm.matrix_world @ hb.head_local).z - 0.5 * hb.length
        lm = head_landmarks(props.mesh, floor)
        if lm is not None:
            return landmark_map(T.RAIN_LANDMARKS, lm), "머리 메쉬"
    xf = arm.matrix_world @ head_xform(hb.matrix_local, hb.length)
    return (lambda p: xf @ Vector(p)), "머리 본"


def migrate_markers():
    """Rename markers made by older versions to their current names."""
    for old, new in T.LEGACY_MARKERS.items():
        obj = bpy.data.objects.get(old)
        if obj is not None and obj.get(M.VERSION_KEY, 0) >= M.MARKER_VERSION:
            continue              # made with the current naming: keep its name
        if obj is not None and new not in bpy.data.objects:
            obj.name = new
            obj[OBJ_PROP] = "MARKER"


def marker_positions(arm):
    """{marker: armature-space position} for the marker empties that exist."""
    migrate_markers()
    inv = arm.matrix_world.inverted()
    world = {n: obj_world(bpy.data.objects[n])
             for n in T.MARKERS if n in bpy.data.objects}
    # A missing .R marker mirrors its .L marker (Mirror Markers not pressed).
    head = bpy.context.scene.hb_facial.head_bone
    x0 = _world(arm, head).x if head in arm.data.bones else 0.0
    for n in T.MARKERS:
        if n.endswith(".R") and n not in world and n[:-2] + ".L" in world:
            p = world[n[:-2] + ".L"]
            world[n] = Vector((2.0 * x0 - p.x, p.y, p.z))
    return {n: inv @ p for n, p in world.items()}


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


def _move_heads_to(ebs, members, target):
    """Translate ``members`` so the first one's head sits on ``target``."""
    if not members or members[0] not in ebs:
        return False
    delta = target - ebs[members[0]].head
    for n in members:
        if n in ebs:
            ebs[n].translate(delta)
    return True


def mesh_center(obj):
    """World-space center of an object's bounding box (an eyeball's center)."""
    corners = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
    return sum(corners, Vector()) / 8.0


def eye_mesh_center(props, side):
    """World center of the registered eye mesh for ``.L`` / ``.R``, or None."""
    obj = props.eye_mesh_l if side == ".L" else props.eye_mesh_r
    return mesh_center(obj) if obj is not None else None


def eye_center(props, side):
    """Eyeball center used for MSTR-EYE and the eyelid pivot.

    The MK-Eye marker wins (hand-fixed position), then the eye mesh center;
    None when neither exists.
    """
    mk = bpy.data.objects.get(f"MK-Eye{side}")
    if mk is not None:
        return obj_world(mk)
    return eye_mesh_center(props, side)


def snap_to_surface(obj, world):
    """Closest point on ``obj``'s surface to ``world`` (unchanged without a mesh)."""
    if obj is None or obj.type != 'MESH' or obj.mode == 'EDIT':
        return world
    inv = obj.matrix_world.inverted()
    ok, loc, _n, _i = obj.closest_point_on_mesh(inv @ world)
    return obj.matrix_world @ loc if ok else world


def _new_marker(context, name, location, head_length):
    return M.new_marker(context, name, location, head_length)


def _head_length(arm, props):
    if arm is not None and props.head_bone in arm.data.bones:
        return arm.data.bones[props.head_bone].length
    return 0.2


class HB_OT_facial_mirror_markers(Operator):
    """모든 .R 마커를 해당 .L 마커에서 머리 본의 X를 기준으로 미러하여
생성(또는 업데이트)합니다"""

    bl_idname = "hb.facial_mirror_markers"
    bl_label = "마커 미러 .L -> .R"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        props = context.scene.hb_facial
        arm = facial_rig(context)
        M.convert_markers(context, _head_length(arm, props))
        x0 = 0.0
        if arm is not None and props.head_bone in arm.data.bones:
            x0 = _world(arm, props.head_bone).x
        made = moved = 0
        for name in T.MARKERS:
            if not name.endswith(".L"):
                continue
            src = bpy.data.objects.get(name)
            if src is None:
                continue
            p = obj_world(src)
            world = Vector((2.0 * x0 - p.x, p.y, p.z))
            dst_name = name[:-2] + ".R"
            dst = bpy.data.objects.get(dst_name)
            if dst is None:
                _new_marker(context, dst_name, world, _head_length(arm, props))
                made += 1
            else:
                dst.matrix_world.translation = world
                moved += 1
        if not made and not moved:
            self.report({'WARNING'}, ".L 마커가 없습니다: 먼저 마커 추가를 실행하세요")
            return {'CANCELLED'}
        self.report({'INFO'}, f".L -> .R 미러 완료: {made}개 생성, {moved}개 업데이트")
        return {'FINISHED'}


PART_MESH_PROPS = (("BROW", "brow_mesh"), ("TONGUE", "tongue_mesh"),
                   ("TEETH_UPP", "teeth_upp_mesh"), ("TEETH_LOW", "teeth_low_mesh"))


def part_mesh_spots(props):
    """({marker: world spot}, [chain ids]) for chains with a part mesh set."""
    spots, chains = {}, []
    for chain, prop in PART_MESH_PROPS:
        obj = getattr(props, prop)
        pts = mesh_chain_points(obj, chain) if obj is not None else None
        if pts:
            for (name, _p), w in zip(T.CHAINS[chain]["markers"], pts):
                spots[name] = w
            chains.append(chain)
    for side in (".L", ".R"):                       # eyeballs: center + pupil
        obj = props.eye_mesh_l if side == ".L" else props.eye_mesh_r
        if obj is not None:
            spots[f"MK-Eye{side}"] = mesh_center(obj)
            spots[f"MK-Eye_Pupil{side}"] = eye_pupil(obj)
            if "eye" not in chains:
                chains.append("eye")
    rim = eyelid_rim_spots(props, {s: eye_center(props, s) for s in (".L", ".R")})
    if rim:                                          # eyelid opening from the eye hole
        spots.update(rim)
        chains.append("eyelid")
    return spots, chains


def eye_pupil(obj):
    """Pupil center of an eyeball mesh: the middle of its front cap, pushed
    out to the eyeball surface (front = -Y)."""
    c = mesh_center(obj)
    mw = obj.matrix_world
    pts = [mw @ v.co for v in obj.data.vertices]
    front, back = min(p.y for p in pts), max(p.y for p in pts)
    cap = [p for p in pts if p.y < front + 0.12 * (back - front)] or pts
    d = (sum(cap, Vector()) / len(cap)) - c
    if d.length < 1e-9:
        d = Vector((0.0, -1.0, 0.0))
    radius = sum((p - c).length for p in cap) / len(cap)   # front surface distance
    return c + d.normalized() * radius


# Part mesh slot -> (name must contain one of, must not contain any of).
_DETECT_RULES = {
    "eye_mesh_l": (("eye",), ("cornea", "spec", "high", "lash", "lid", "brow", "mask",
                              "iris", "pupil", "light", "lattice", "guide", "wgt")),
    "eye_mesh_r": (("eye",), ("cornea", "spec", "high", "lash", "lid", "brow", "mask",
                              "iris", "pupil", "light", "lattice", "guide", "wgt")),
    "brow_mesh": (("brow",), ("scalp", "fur", "lash", "guide", "wgt", "hair")),
    "teeth_upp_mesh": (("teeth", "tooth"), ("gum", "guide", "wgt")),
    "teeth_low_mesh": (("teeth", "tooth"), ("gum", "guide", "wgt")),
    "tongue_mesh": (("tongue",), ("guide", "wgt")),
}
_SIDE_KEYS = {
    "eye_mesh_l": ("_l", ".l", "left"), "eye_mesh_r": ("_r", ".r", "right"),
    "teeth_upp_mesh": ("up", "top", "upper"), "teeth_low_mesh": ("lw", "low", "bot", "lower"),
}


def detect_part_meshes(scene):
    """{slot prop: mesh object} guessed from object names."""
    meshes = [o for o in scene.objects if o.type == 'MESH']
    found = {}
    for prop, (want, avoid) in _DETECT_RULES.items():
        cands = []
        for o in meshes:
            n = o.name.lower()
            if not any(w in n for w in want) or any(a in n for a in avoid):
                continue
            keys = _SIDE_KEYS.get(prop)
            if keys and not any(n.endswith(k) or k in n.split("_")[-1] for k in keys):
                continue
            cands.append(o)
        if cands:
            found[prop] = min(cands, key=lambda o: len(o.name))   # plainest name
    return found


class HB_OT_facial_detect_meshes(Operator):
    """오브젝트 이름으로 눈 / 눈썹 / 치아 / 혀 메쉬 슬롯을 채웁니다
(예: Geo_eye_L, Geo_chloe_brow, Geo_chloe_teeth_Up, Geo_chloe_tongue)"""

    bl_idname = "hb.facial_detect_meshes"
    bl_label = "파츠 메쉬 자동 감지"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        props = context.scene.hb_facial
        found = detect_part_meshes(context.scene)
        for prop, obj in found.items():
            setattr(props, prop, obj)
        missing = [p for p in _DETECT_RULES if p not in found]
        msg = ", ".join(f"{p.replace('_mesh', '')}={o.name}" for p, o in found.items())
        if missing:
            msg += f"; 찾지 못함: {', '.join(p.replace('_mesh', '') for p in missing)}"
        self.report({'WARNING'} if missing else {'INFO'}, msg or "찾은 메쉬가 없습니다")
        return {'FINISHED'}


class HB_OT_facial_replace_part_markers(Operator):
    """눈 / 눈썹 / 치아 / 혀 마커를 해당 파츠 메쉬 위로 옮깁니다
(없는 마커는 생성하고, 기존 .R 체인 마커는 다시 미러합니다)"""

    bl_idname = "hb.facial_replace_part_markers"
    bl_label = "파츠 마커 메쉬에 재배치"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        props = context.scene.hb_facial
        arm = facial_rig(context)
        spots, chains = part_mesh_spots(props)
        if not spots:
            self.report({'WARNING'}, "먼저 파츠 메쉬를 지정하세요 (자동 감지)")
            return {'CANCELLED'}
        M.convert_markers(context, _head_length(arm, props))
        x0 = 0.0
        size = 0.2
        if arm is not None and props.head_bone in arm.data.bones:
            x0 = _world(arm, props.head_bone).x
            size = arm.data.bones[props.head_bone].length
        moved = 0
        for name, w in spots.items():
            targets = [(name, w)]
            if (name.endswith(".L") and T.flip(name) in bpy.data.objects
                    and T.flip(name) not in spots):
                targets.append((T.flip(name), Vector((2.0 * x0 - w.x, w.y, w.z))))
            for n, p in targets:
                obj = bpy.data.objects.get(n)
                if obj is None:
                    _new_marker(context, n, p, size)
                else:
                    obj.matrix_world.translation = p
                moved += 1
        self.report({'INFO'}, f"마커 {moved}개 배치: {', '.join(c.lower() for c in chains)}")
        return {'FINISHED'}


class HB_OT_facial_select_markers(Operator):
    """이 마커들을 선택합니다 (Shift: 선택에 추가)"""

    bl_idname = "hb.facial_select_markers"
    bl_label = "마커 선택"
    bl_options = {'REGISTER', 'UNDO'}

    names: StringProperty(description="쉼표로 구분한 마커 이름입니다")
    extend: BoolProperty(default=False, options={'SKIP_SAVE'})

    def invoke(self, context, event):
        self.extend = event.shift
        return self.execute(context)

    def execute(self, context):
        objs = [bpy.data.objects[n] for n in self.names.split(",")
                if n in bpy.data.objects and n in context.view_layer.objects]
        if not objs:
            self.report({'WARNING'}, "마커가 없습니다: 마커 추가를 실행하세요")
            return {'CANCELLED'}
        active = context.view_layer.objects.active
        if active is not None and active.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        if not self.extend:
            for o in context.selected_objects:
                o.select_set(False)
        for o in objs:
            o.hide_select = False
            o.select_set(True)
        context.view_layer.objects.active = objs[-1]
        return {'FINISHED'}


class HB_OT_facial_markers_only(Operator):
    """토글: 뷰포트에서 마커 오브젝트만 선택할 수 있게 합니다 (다시 토글할
때까지 다른 오브젝트는 모두 선택할 수 없습니다)"""

    bl_idname = "hb.facial_markers_only"
    bl_label = "마커만 선택"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        scene = context.scene
        lock = M.LOCK_KEY not in scene
        n = M.lock_others(scene, lock)
        self.report({'INFO'}, (f"마커만 선택: 오브젝트 {n}개 잠금" if lock
                               else f"오브젝트 {n}개 선택 가능 상태로 복원"))
        return {'FINISHED'}


class HB_OT_facial_add_markers(Operator):
    """중앙 및 .L 마커 엠프티(얼굴 스쿼시 라인, 두개골, 코, 눈썹, 귀, 치아, 혀)를
기본 위치에 추가합니다. 기존 마커는 이동하지 않습니다. .R 마커는
마커 미러로 만듭니다"""

    bl_idname = "hb.facial_add_markers"
    bl_label = "마커 추가"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        arm = facial_rig(context)
        return arm is not None and bool(context.scene.hb_facial.head_bone)

    def execute(self, context):
        props = context.scene.hb_facial
        arm = facial_rig(context)
        hb = arm.data.bones.get(props.head_bone)
        if hb is None:
            self.report({'ERROR'}, f"머리 본 '{props.head_bone}'을(를) 찾을 수 없습니다")
            return {'CANCELLED'}
        to_world, source = marker_defaults(arm, props, hb)
        migrate_markers()
        converted = M.convert_markers(context, hb.length)
        # Default spot per marker; a registered part mesh overrides its chain.
        defaults = {n: to_world(p) for n, p in T.MARKERS.items()}
        spots, chains = part_mesh_spots(props)
        defaults.update(spots)
        # eyesocket ring from the eyelid markers (placed ones win), storm ratios
        from .features import eyesocket_spots
        lid = {}
        for role in ("In", "Upp", "Out", "Low"):
            mk = f"MK-Eyelid_{role}.L"
            obj = bpy.data.objects.get(mk)
            lid[role] = obj_world(obj) if obj else defaults[mk].copy()
        defaults.update(eyesocket_spots(lid, props.mesh, ".L"))
        from_mesh = [c.lower() for c in chains]
        # A missing marker in a set some of whose markers already exist (maybe
        # placed by hand) keeps that set's offset from the default spots.
        made = 0
        for group in T.MARKER_SETS + [(n,) for n in T.POINT_MARKERS]:
            exist = [n for n in group if n in bpy.data.objects]
            missing = [n for n in group if n not in bpy.data.objects and not n.endswith(".R")]
            if not missing:
                continue
            offset = Vector()
            if exist:
                offset = sum((obj_world(bpy.data.objects[n]) - defaults[n]
                              for n in exist), Vector()) / len(exist)
            for n in missing:
                pos = defaults[n] + offset
                if n in T.SURFACE_MARKERS:
                    pos = snap_to_surface(props.mesh, pos)
                _new_marker(context, n, pos, hb.length)
                made += 1
        props.show_preview = True
        msg = f"마커 {made}개 추가 (중앙 + .L), {source}에 맞춤"
        if converted:
            msg += f"; 이전 엠프티 {converted}개를 색상 마커로 변환"
        if from_mesh:
            msg += f"; {', '.join(from_mesh)}: 파츠 메쉬에서 배치"
        self.report({'INFO'}, msg + " - 위치를 잡은 뒤 마커 미러로 .R을 만드세요")
        return {'FINISHED'}


class HB_OT_facial_build_base(Operator):
    """머리 본 아래에 베이스 얼굴 본(rain 레이아웃)을 생성합니다.
다시 실행하면 이 도구가 만든 베이스 본만 교체합니다"""

    bl_idname = "hb.facial_build_base"
    bl_label = "베이스 생성"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        arm = facial_rig(context)
        return arm is not None and bool(context.scene.hb_facial.head_bone)

    def execute(self, context):
        props = context.scene.hb_facial
        arm = facial_rig(context)
        if props.head_bone not in arm.data.bones:
            self.report({'ERROR'}, f"머리 본 '{props.head_bone}'을(를) 찾을 수 없습니다")
            return {'CANCELLED'}
        old = set(_tagged(arm, 'BASE'))
        names = list(T.BASE_BONES) + part_bone_names(props.brow_count)
        clash = [n for n in names if n in arm.data.bones and n not in old]
        if clash:
            self.report({'ERROR'}, "직접 만든 본이 이미 베이스 본 이름을 사용 중입니다: "
                        + ", ".join(clash[:5]))
            return {'CANCELLED'}

        markers = marker_positions(arm)
        # Chain points (world) for the parts: markers, mirrored .L, or defaults.
        hb = arm.data.bones[props.head_bone]
        to_world, _src = marker_defaults(arm, props, hb)
        mirror_x = _world(arm, props.head_bone).x

        def marker_world(n):
            o = bpy.data.objects.get(n)
            return obj_world(o) if o is not None else None

        chain_pts = {c: {s: resolve_points(c, s, marker_world, to_world, mirror_x)
                         for s in ((".L", ".R") if T.CHAINS[c]["sided"] else ("",))}
                     for c in T.CHAINS}
        scale = hb.length / (Vector(T.REFERENCE[1]) - Vector(T.REFERENCE[0])).length
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
            # Markers override the Head-based layout: first each squash line
            # (moves its whole group), then the point markers, then the eyes.
            fitted = []
            for str_bone, root, tip, members in T.MARKER_GROUPS:
                if root in markers and tip in markers:
                    _fit_group_to_markers(ebs, str_bone, markers[root], markers[tip], members)
                    fitted.append(str_bone)
            for mk, members in T.POINT_MARKERS.items():
                if mk.startswith("MK-Eye"):
                    continue          # eyes: handled below (marker > eye mesh)
                if mk in markers and _move_heads_to(ebs, members, markers[mk]):
                    fitted.append(members[0])
            inv = arm.matrix_world.inverted()
            for side in (".L", ".R"):
                c = eye_center(props, side)
                name = T.EYE_MASTER.format(side=side)
                if c is not None and _move_heads_to(ebs, (name,), inv @ c):
                    src = "마커" if f"MK-Eye{side}" in bpy.data.objects else "눈 메쉬"
                    fitted.append(f"{name} ({src})")
            # The skull / jaw rotate the mouth open around their X: keep it the
            # character's side axis (a twisted X swings the lip corners sideways
            # when the jaw opens: an L / R mismatch in every open pose).
            side = inv.to_3x3() @ Vector((1.0, 0.0, 0.0))
            for name in T.LEVEL_ROLL:
                if name in ebs and ebs[name].length > 1e-9:
                    eb = ebs[name]
                    eb.align_roll(side.cross(eb.vector.normalized()))
            # Parts (brow curve, tongue chain, teeth arches) from chain points.
            part_names = build_parts(ebs, arm, chain_pts, scale, props.brow_count,
                                     lambda b, coll: _set_only_coll(arm, b, coll))
            for child, par in orphans.items():
                if child in ebs and par in ebs:
                    ebs[child].parent = ebs[par]

        _tag(arm, list(T.BASE_BONES) + part_names, 'BASE')
        _clear_constraints(arm, list(T.BASE_BONES) + part_names)
        brow_constraints(arm)                      # storm brow stretch / follow
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
        msg = (f"베이스 본 {len(T.BASE_BONES)}개 + 파츠 본 {len(part_names)}개 생성 "
               f"(교체 {len(old)}개)")
        msg += f"; 마커 적용: {', '.join(fitted)}" if fitted else "; 마커 없음, 머리 본 레이아웃"
        self.report({'INFO'}, msg)
        return {'FINISHED'}


# ----------------------------------------------------------------------
# 1. Edges -> guides, ribbon
# ----------------------------------------------------------------------
def edge_map(mesh_obj):
    data = mesh_obj.get(EDGE_KEY) if mesh_obj else None
    return {k: [int(i) for i in v] for k, v in data.items()} if data else {}


class HB_OT_facial_register_edges(Operator):
    """선택한 엣지(에디트 모드)를 이 슬롯의 루프로 등록합니다"""

    bl_idname = "hb.facial_register_edges"
    bl_label = "엣지 등록"
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
            self.report({'WARNING'}, "먼저 루프의 엣지를 선택하세요")
            return {'CANCELLED'}
        mapping = edge_map(obj)
        mapping[self.slot] = sel
        obj[EDGE_KEY] = mapping
        props = context.scene.hb_facial
        if props.mesh is None:
            props.mesh = obj
        self.report({'INFO'}, f"{self.slot}: {obj.name}에 엣지 {len(sel)}개 등록")
        return {'FINISHED'}


class HB_OT_facial_clear_edges(Operator):
    """이 슬롯에 등록된 엣지를 삭제합니다"""

    bl_idname = "hb.facial_clear_edges"
    bl_label = "엣지 삭제"
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


def eyelid_marker(slot, role):
    """World position of the step-0 eyelid marker for a guide role, or None."""
    if slot == 'LIPS' or role == "pivot":
        return None
    suffix = next(k for k, r in T.EYELID_GUIDE_ROLES.items() if r == role)
    obj = bpy.data.objects.get(f"MK-Eyelid_{suffix}{SETS[slot]['side']}")
    return obj_world(obj) if obj is not None else None


def eyelid_rim_spots(props, eye_c):
    """{eyelid marker: world} from the head mesh's eye-hole rim nearest each
    eye center (``eye_c``: {side: world}); empty when there is no rim."""
    mesh = props.mesh
    if mesh is None or mesh.mode == 'EDIT':
        return {}
    graph = MeshGraph(mesh)
    loops = [l for l in graph.boundary_loops() if len(l) >= 6]
    spots = {}
    for side, c in eye_c.items():
        sign = 1.0 if side == ".L" else -1.0
        cands = [l for l in loops if centroid([graph.co[i] for i in l]).x * sign > 0.0]
        if not cands or c is None:
            continue
        loop = _ordered_rim(graph, min(cands, key=lambda l: (centroid([graph.co[i] for i in l]) - c).length))
        co = graph.co
        i_in = min(loop, key=lambda i: abs(co[i].x))
        i_out = max(loop, key=lambda i: abs(co[i].x))
        arc1, arc2 = split_loop(loop, i_in, i_out)
        z = lambda arc: max(co[i].z for i in arc)
        upper, lower = (arc1, arc2) if z(arc1) >= z(arc2) else (arc2, arc1)
        spots[f"MK-Eyelid_Upp{side}"] = co[max(upper, key=lambda i: co[i].z)]
        spots[f"MK-Eyelid_Low{side}"] = co[min(lower, key=lambda i: co[i].z)]
        spots[f"MK-Eyelid_In{side}"] = co[i_in]
        spots[f"MK-Eyelid_Out{side}"] = co[i_out]
    return spots


def _ordered_rim(graph, verts):
    """Order a boundary loop's vertices by walking its boundary edges."""
    vs = set(verts)
    start = verts[0]
    order, prev, cur = [start], None, start
    while True:
        nxt = [w for w, is_b in graph.neighbors[cur] if is_b and w in vs and w != prev]
        if not nxt or nxt[0] == start:
            break
        prev, cur = cur, nxt[0]
        order.append(cur)
    return order


def slot_pivot(arm, props, slot):
    """World pivot of a set: the eye mesh center for eyelids when registered,
    otherwise the set's Pivot guide (None if neither exists)."""
    spec = SETS[slot]
    if slot != 'LIPS':
        c = eye_center(props, spec["side"])
        if c is not None:
            return c
    name = spec["guides"]["pivot"]
    return _world(arm, name) if name in arm.data.bones else None


def analyze_slot(arm, mesh, slot, eye_c=None):
    """Split a slot's registered loop into upper / lower arcs.

    Corners, the upper arc and the centers follow the guide bones when they
    exist, otherwise sensible defaults. ``eye_c`` (an eye mesh center) wins
    over the Pivot guide. Returns a dict with world positions.
    """
    spec = SETS[slot]
    g = spec["guides"]
    loop, closed = edge_loop(mesh, edge_map(mesh)[slot])
    if not closed:
        raise ValueError(f"{slot}: 닫힌 루프를 등록하세요")
    mw = mesh.matrix_world
    co = {i: mw @ mesh.data.vertices[i].co for i in loop}

    def guide(role):
        mk = eyelid_marker(slot, role)          # step-0 eyelid markers win
        if mk is not None:
            return mk
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
        raise ValueError(f"{slot}: 양쪽 코너가 같은 버텍스에 놓였습니다")
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
    pivot = eye_c if eye_c is not None else guide("pivot")
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


def _eye_c(props, slot):
    return None if slot == 'LIPS' else eye_center(props, SETS[slot]["side"])


def slot_lines(arm, mesh, slot, props):
    """(analysis, upper line, lower line); lines are [(name, point)]."""
    info = analyze_slot(arm, mesh, slot, _eye_c(props, slot))
    co, spec = info["co"], info["spec"]
    upp = line_points(spec, "upp", [co[i] for i in info["upper"]], info["upp_mid"], props)
    low = line_points(spec, "low", [co[i] for i in info["lower"]], info["low_mid"], props)
    return info, upp, low


def _ribbon_mesh(context, arm, name, tag, upp, low, offset):
    """Ribbon (storm style) along an upper + lower line sharing both corners.

    Line vertices come first (index = point order), each with a copy pushed
    straight forward (-Y) by ``offset``; quads join them. ``MSK-line`` holds
    the line vertices and a Mask modifier shows only the line, so vertex
    indices of the line stay valid for vertex parenting.
    Returns (object, {point name: vertex index}, [(name, point)]).
    """
    _remove_objects({tag})
    order = list(upp) + [p for p in low[1:-1]]          # corners only once
    index = {nm: i for i, (nm, _p) in enumerate(order)}
    n = len(order)
    fwd = Vector((0.0, -offset, 0.0))
    verts = [p.copy() for _n, p in order] + [p + fwd for _n, p in order]
    faces = []
    for line in (upp, low):
        names = [nm for nm, _p in line]
        for a, b in zip(names, names[1:]):
            ia, ib = index[a], index[b]
            faces.append((ia, ib, ib + n, ia + n))
    me = bpy.data.meshes.new(name)
    me.from_pydata([tuple(v) for v in verts], [], faces)
    me.update()
    rib = bpy.data.objects.new(name, me)
    rib[OBJ_PROP] = tag
    rib["hb_points"] = [nm for nm, _p in order]     # vertex i = point i (re-hooking)
    rib.hide_render = True
    _ribbon_collection(context).objects.link(rib)
    rib.vertex_groups.new(name=MASK_GROUP).add(list(range(n)), 1.0, 'REPLACE')
    # (no Armature modifier: _weight_ribbon adds bone hooks, then the Mask)
    return rib, index, order


def _weight_ribbon(rib, arm, index, weights_for):
    """Make the ribbon ride the face bones.

    ``weights_for(point name)`` -> {bone: weight}; each point's line vertex
    and its forward copy get it, as vertex groups named after the bones,
    deformed by one Armature modifier (storm: FACE-Armature on the ribbon).
    Bones missing from the armature are skipped (returns their names).
    """
    n = len(rib.data.vertices) // 2
    missing = set()
    for nm, i in index.items():
        for bone, w in weights_for(nm).items():
            if bone not in arm.data.bones:
                missing.add(bone)
                continue
            vg = rib.vertex_groups.get(bone) or rib.vertex_groups.new(name=bone)
            vg.add([i, i + n], w, 'REPLACE')
            # an Armature modifier only uses deform bones (storm's ribbon
            # controls are deform too; the face mesh has no groups for them)
            arm.data.bones[bone].use_deform = True
    for m in [m for m in rib.modifiers if m.type in {'HOOK', 'ARMATURE', 'MASK'}]:
        rib.modifiers.remove(m)
    mod = rib.modifiers.new("Armature", 'ARMATURE')
    mod.object = arm
    mod.use_vertex_groups = True
    mod.use_bone_envelopes = False
    mask = rib.modifiers.new("Mask", 'MASK')      # after the armature: show the line only
    mask.vertex_group = MASK_GROUP
    return sorted(missing)


def _lip_weights(name):
    if name in SETS["LIPS"]["corners"]:          # corners: half jaw
        return {T.LIP_UPPER_PARENT: 0.5, T.JAW_BONE: 0.5}
    if "_low" in name:
        return {T.LIP_LOWER_PARENT: 1.0}
    return {T.LIP_UPPER_PARENT: 1.0}


EYELID_RIBBON_BONE = "STR-FACE_UPP"


RIBBON_COLL = "Facial_Ribbons"


def _ribbon_collection(context):
    """Collection holding the ribbon meshes and the lip HK- empties."""
    coll = bpy.data.collections.get(RIBBON_COLL)
    if coll is None:
        coll = bpy.data.collections.new(RIBBON_COLL)
    if coll.name not in context.scene.collection.children and not any(
            coll.name in c.children for c in bpy.data.collections):
        context.scene.collection.children.link(coll)
    return coll


def _ribbon_offset(arm, props):
    ref = (Vector(T.REFERENCE[1]) - Vector(T.REFERENCE[0])).length
    return RIBBON_OFFSET * _head_length(arm, props) / ref


def _build_ribbon(context, arm, props, upp, low):
    """Lip ribbon + one vertex-parented HK- empty per point + HK-Zip groups."""
    _remove_objects({"LIPS_HOOK"})
    rib, index, order = _ribbon_mesh(context, arm, RIBBON_LIPS, "LIPS_RIBBON", upp, low,
                                     _ribbon_offset(arm, props))
    # Zip groups: each point + the nearest point on the opposite line.
    upp_names = {nm for nm, _p in upp}
    for nm, p in order:
        other = low if nm in upp_names else upp
        group = [index[nm]]
        if nm not in (upp[0][0], upp[-1][0]):
            mate = min(other[1:-1] or other, key=lambda t: (t[1] - p).length_squared)[0]
            group.append(index[mate])
        rib.vertex_groups.new(name=ZIP_PREFIX + nm).add(group, 1.0, 'REPLACE')
    _weight_ribbon(rib, arm, index, _lip_weights)
    coll = rib.users_collection[0]
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


def _build_eyelid_ribbon(context, arm, props, side, upp, low):
    """Eyelid ribbon with one single-vertex HK- group per point (storm
    HK-Lid_*); the eyelid MCH bones Damped Track to these groups."""
    rib, index, order = _ribbon_mesh(context, arm, RIBBON_EYELID.format(side=side),
                                     "EYE_RIBBON" + side, upp, low, _ribbon_offset(arm, props))
    for nm, _p in order:
        rib.vertex_groups.new(name=HOOK_PREFIX + nm).add([index[nm]], 1.0, 'REPLACE')
    _weight_ribbon(rib, arm, index, lambda _nm: {EYELID_RIBBON_BONE: 1.0})
    return len(order)


class HB_OT_facial_generate(Operator):
    """등록된 루프로 가이드를 생성하고 (기존 가이드는 유지), MSTR-EYE / MSTR-MOUTH를
다시 맞춘 뒤 HK- 엠프티가 달린 입술 리본을 생성합니다"""

    bl_idname = "hb.facial_generate"
    bl_label = "가이드 생성"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return facial_rig(context) is not None

    def execute(self, context):
        props = context.scene.hb_facial
        arm = facial_rig(context)
        err = _check_mesh(props)
        if err:
            self.report({'ERROR'}, err)
            return {'CANCELLED'}
        mesh = props.mesh
        slots = [s for s in SETS if s in edge_map(mesh)]
        if not slots:
            self.report({'WARNING'}, "먼저 루프를 하나 이상 등록하세요")
            return {'CANCELLED'}

        try:
            infos = {s: analyze_slot(arm, mesh, s, _eye_c(props, s)) for s in slots}
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
                        # The eye center owns the eyelid pivot, and the eyelid
                        # markers (step 0) own the eyelid corner/upper/lower.
                        owned = ((role == "pivot" and _eye_c(props, slot) is not None)
                                 or (role != "pivot" and eyelid_marker(slot, role) is not None))
                        if owned:
                            b = ebs[name]
                            vec = b.tail - b.head
                            b.head = inv @ info["guides"][role]
                            b.tail = b.head + vec
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
                refit[T.EYE_MASTER.format(side=SETS[slot]["side"])] = slot_pivot(arm, props, slot)
        refit = {k: v for k, v in refit.items() if k in base}
        if refit:
            with _EditMode(context, arm) as ebs:
                for name, p in refit.items():
                    b = ebs[name]
                    vec = b.tail - b.head
                    b.head = inv @ p
                    b.tail = b.head + vec

        msg = f"가이드 {len(made)}개 추가"
        if refit:
            msg += f", 다시 맞춤: {', '.join(refit)}"
        if 'LIPS' in slots:
            info, upp, low = slot_lines(arm, mesh, 'LIPS', props)
            count = _build_ribbon(context, arm, props, upp, low)
            msg += f", {RIBBON_LIPS} + HK- 엠프티 {count}개"
        for slot in slots:
            if slot == 'LIPS':
                continue
            side = SETS[slot]["side"]
            info, upp, low = slot_lines(arm, mesh, slot, props)
            count = _build_eyelid_ribbon(context, arm, props, side, upp, low)
            msg += f", {RIBBON_EYELID.format(side=side)} (HK- 그룹 {count}개)"
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
    """MCH 본(피벗 -> 각 포인트)을 부모 본 및 컨스트레인트와 함께 생성합니다.
다시 실행하면 이 도구가 만든 MCH 및 DEF 본을 교체합니다"""

    bl_idname = "hb.facial_build_mch"
    bl_label = "MCH 생성"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return facial_rig(context) is not None

    def execute(self, context):
        props = context.scene.hb_facial
        arm = facial_rig(context)
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
                notes.append(f"{slot}: 먼저 가이드 생성을 실행하세요")
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
            plans[slot] = (points, slot_pivot(arm, props, slot))
        if not plans:
            self.report({'WARNING'}, "; ".join(notes) or "생성할 항목이 없습니다")
            return {'CANCELLED'}

        inv = arm.matrix_world.inverted()
        old = {s: set(_tagged(arm, 'MCH', s)) | set(_tagged(arm, 'DEF', s)) for s in plans}
        bones = arm.data.bones
        built, skipped = {}, []
        need_jaw_half = 'LIPS' in plans and T.JAW_BONE in bones
        with _EditMode(context, arm) as ebs:
            # Removing bones unparents their children (the controls stage
            # hangs the lip side on the half-jaw): re-attach them below.
            gone = set().union(*old.values()) | ({T.JAW_HALF} if need_jaw_half else set())
            orphans = {b.name: b.parent.name for b in ebs
                       if b.parent is not None and b.parent.name in gone and b.name not in gone}
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
                    m.display_type = 'STICK'      # thin lines: many radial MCH
                    _set_only_coll(arm, m, T.COLL_MCH)
                    built[slot].append(m.name)
            for child, par in orphans.items():
                if child in ebs and par in ebs:
                    ebs[child].parent = ebs[par]

        _clear_constraints(arm, [n for names in built.values() for n in names]
                           + ([T.JAW_HALF] if need_jaw_half else []))
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
                # eyelids (storm): MCH from the eye center Damped Track (Y) to
                # its single-vertex HK- group on the eyelid ribbon
                rib = bpy.data.objects.get(RIBBON_EYELID.format(side=SETS[slot]["side"]))
                for n in names:
                    group = HOOK_PREFIX + n[len("MCH-"):]
                    if rib is None or group not in rib.vertex_groups:
                        hooks_missing += 1
                        continue
                    c = arm.pose.bones[n].constraints.new('DAMPED_TRACK')
                    c.name = "Track_HK"
                    c.target, c.subtarget = rib, group
                    c.track_axis = 'TRACK_Y'
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

        msg = f"MCH 생성: " + ", ".join(f"{s} {len(n)}" for s, n in built.items())
        for slot, first, count in skipped:
            msg += f"; {slot} 건너뜀 (직접 만든 본이 이름 {count}개 사용 중, 예: {first})"
        if hooks_missing:
            msg += f"; HK- 타깃이 없는 MCH {hooks_missing}개 (가이드 생성을 실행하세요)"
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


DSP_GROUP_PREFIX = "dsp-"


def dsp_group(bone_name):
    """Mesh vertex group a DSP bone copies its location from. Never the bone's
    own name (storm: 'DSP-brow1.L' bone / 'dsp-brow1.L' group): an Armature
    modifier depends on every bone that has a same-named group, and the DSP
    bone depends on the mesh -> dependency cycle."""
    return DSP_GROUP_PREFIX + bone_name[len(DSP_PREFIX):] if bone_name.startswith(DSP_PREFIX) \
        else DSP_GROUP_PREFIX + bone_name


def build_dsp(context, arm, mesh, length, fallback_parent, mapping=None):
    """One-vertex group + DSP bone (Copy Location) per control -> vertex.

    The DSP bone copies the control's rest direction, length and roll so
    the control's shape (drawn at the DSP bone) keeps its size and facing.
    """
    if mapping is None:
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
            c = ebs[ctrl]
            b.head = inv @ (mw @ mesh.data.vertices[vi].co)
            b.tail = b.head + (c.tail - c.head)
            b.roll = c.roll
            par = parents[ctrl]
            b.parent = ebs.get(par) if par else None
            b.use_deform = False
            _set_only_coll(arm, b, DSP_COLL)
            built.append((name, ctrl, vi))
    _clear_constraints(arm, [b[0] for b in built])
    for n in old:                                  # groups of DSP bones not rebuilt
        for gname in (n, dsp_group(n)):            # (older builds used the bone name)
            vg = mesh.vertex_groups.get(gname)
            if vg is not None:
                mesh.vertex_groups.remove(vg)
    for name, ctrl, vi in built:
        gname = dsp_group(name)
        for old_name in (name, gname):
            vg = mesh.vertex_groups.get(old_name)
            if vg is not None:
                mesh.vertex_groups.remove(vg)
        mesh.vertex_groups.new(name=gname).add([vi], 1.0, 'REPLACE')
        arm.data.bones[name][DSP_PROP] = 1
        pb = arm.pose.bones[name]
        con = pb.constraints.new('COPY_LOCATION')
        con.name = "HB_DSP"
        con.target, con.subtarget = mesh, dsp_group(name)
        arm.pose.bones[ctrl].custom_shape_transform = pb
    return len(built), len(old), missing, blocked


class HB_OT_facial_build_all(Operator):
    """생성된 모든 MCH에 DEF 본을 만든 뒤, 저장된 모든 컨트롤의 DSP 본을
생성합니다. 다시 실행하면 이 도구가 만든 것만 교체합니다"""

    bl_idname = "hb.facial_build_all"
    bl_label = "전체 생성"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return facial_rig(context) is not None

    def execute(self, context):
        props = context.scene.hb_facial
        arm = facial_rig(context)
        if not _tagged(arm, 'MCH'):
            self.report({'WARNING'}, "먼저 MCH 생성을 실행하세요")
            return {'CANCELLED'}
        made, replaced = build_def(context, arm, props.def_length)
        msg = f"DEF {made}개 (교체 {replaced}개)"
        level = {'INFO'}
        if props.mesh is not None:
            from .rig import dsp_full_map
            n, old, missing, blocked = build_dsp(context, arm, props.mesh,
                                                 props.def_length, props.parent_bone,
                                                 dsp_full_map(arm, props.mesh))
            msg += f", DSP {n}개 (교체 {old}개)"
            if missing:
                msg += f"; 저장된 컨트롤 {len(missing)}개를 찾을 수 없음 (예: {missing[0]})"
                level = {'WARNING'}
            if blocked:
                msg += f"; 직접 만든 본이 DSP 이름 {len(blocked)}개를 사용 중"
                level = {'WARNING'}
        self.report(level, msg)
        return {'FINISHED'}


class HB_OT_facial_dsp_build(Operator):
    """저장된 모든 컨트롤의 DSP 본만 생성합니다"""

    bl_idname = "hb.facial_dsp_build"
    bl_label = "DSP 생성"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        props = context.scene.hb_facial
        return facial_rig(context) is not None and props.mesh is not None

    def execute(self, context):
        from .rig import dsp_full_map
        props = context.scene.hb_facial
        arm = facial_rig(context)
        n, old, missing, blocked = build_dsp(context, arm, props.mesh,
                                             props.def_length, props.parent_bone,
                                             dsp_full_map(arm, props.mesh))
        msg = f"DSP 본 {n}개 생성 (교체 {old}개)"
        if missing:
            msg += f"; 저장된 컨트롤 {len(missing)}개를 찾을 수 없음 (예: {missing[0]})"
        if blocked:
            msg += f"; {len(blocked)}개 건너뜀, 직접 만든 본이 이름을 사용 중"
        self.report({'WARNING'} if (missing or blocked) else {'INFO'}, msg)
        return {'FINISHED'}


class HB_OT_facial_dsp_store(Operator):
    """패널에 지정한 컨트롤에 활성(또는 하나만 선택된) 버텍스를 저장합니다"""

    bl_idname = "hb.facial_dsp_store"
    bl_label = "버텍스 저장"
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
                self.report({'WARNING'}, "버텍스 하나를 선택하세요 (또는 하나를 활성으로)")
                return {'CANCELLED'}
            active = sel[0]
        mapping = dsp_map(obj)
        mapping[props.dsp_control] = active.index
        obj[DSP_KEY] = mapping
        self.report({'INFO'}, f"{props.dsp_control} -> 버텍스 {active.index}")
        return {'FINISHED'}


class HB_OT_facial_dsp_store_nearest(Operator):
    """선택한 각 포즈 본에 대해, 레스트 본 헤드에 가장 가까운 메쉬 버텍스를 저장합니다"""

    bl_idname = "hb.facial_dsp_store_nearest"
    bl_label = "가장 가까운 버텍스 저장 (선택)"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        arm = facial_rig(context)
        return (arm is not None and arm.mode == 'POSE'
                and context.scene.hb_facial.mesh is not None)

    def execute(self, context):
        from mathutils import kdtree
        props = context.scene.hb_facial
        arm = facial_rig(context)
        bones = [pb for pb in (context.selected_pose_bones or [])
                 if not pb.name.startswith(DSP_PREFIX)]
        if not bones:
            self.report({'WARNING'}, "컨트롤 본을 선택하세요")
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
        self.report({'INFO'}, f"{mesh.name}에 컨트롤 {len(bones)}개 저장")
        return {'FINISHED'}


class HB_OT_facial_dsp_forget(Operator):
    """패널에 지정한 컨트롤의 저장된 버텍스를 삭제합니다"""

    bl_idname = "hb.facial_dsp_forget"
    bl_label = "컨트롤 저장 삭제"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        props = context.scene.hb_facial
        return props.mesh is not None and bool(props.dsp_control)

    def execute(self, context):
        props = context.scene.hb_facial
        mapping = dsp_map(props.mesh)
        if mapping.pop(props.dsp_control, None) is None:
            self.report({'WARNING'}, f"{props.dsp_control}은(는) 저장되어 있지 않습니다")
            return {'CANCELLED'}
        props.mesh[DSP_KEY] = mapping
        return {'FINISHED'}


# ----------------------------------------------------------------------
# Clear
# ----------------------------------------------------------------------
CLEAR_ITEMS = [
    ('MARKER', "마커", "마커 엠프티"),
    ('BASE', "베이스", "베이스 얼굴 본"),
    ('GUIDE', "가이드", "가이드 본, 입술 리본 및 HK- 엠프티"),
    ('MCH', "MCH", "생성된 MCH 본 (및 그 위의 DEF 본)"),
    ('CTRL', "컨트롤", "컨트롤 생성으로 만든 컨트롤, 눈 / 입 MCH 및 DEF-Eye"),
    ('DEF', "DEF + DSP", "생성된 DEF / DSP 본 및 DSP 버텍스 그룹"),
]


class HB_OT_facial_clear(Operator):
    """한 단계에서 생성한 것을 삭제합니다. 직접 만든 본은 건드리지 않습니다"""

    bl_idname = "hb.facial_clear"
    bl_label = "삭제"
    bl_options = {'REGISTER', 'UNDO'}

    stage: EnumProperty(items=CLEAR_ITEMS)

    @classmethod
    def poll(cls, context):
        return facial_rig(context) is not None

    def execute(self, context):
        props = context.scene.hb_facial
        if self.stage == 'MARKER':
            _remove_objects({"MARKER"})
            coll = bpy.data.collections.get(T.MARKER_COLL)
            if coll is not None and not coll.all_objects:
                bpy.data.collections.remove(coll)
            return {'FINISHED'}
        arm = facial_rig(context)
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
        brows = []
        if self.stage == 'CTRL':
            from .features import clear_runtime
            clear_runtime(arm)            # blink ribbons, blink tracks, lip zip, drivers
        if self.stage == 'CTRL':          # brow DEFs go back under their master
            brows = [pb.name for pb in arm.pose.bones if pb.name.startswith("DEF-Brow_")
                     and any(c.name == "Brow_Ctrl" for c in pb.constraints)]
            for n in brows:
                pb = arm.pose.bones[n]
                for c in [c for c in pb.constraints if c.name == "Brow_Ctrl"]:
                    pb.constraints.remove(c)
        with _EditMode(context, arm) as ebs:
            _remove_bones(ebs, names)
            for n in brows:
                master = "MSTR-BROW" + n[-2:]
                if n in ebs and master in ebs:
                    ebs[n].parent = ebs[master]
        if self.stage == 'GUIDE':
            _remove_objects(RIBBON_TAGS)
            coll = bpy.data.collections.get(RIBBON_COLL)
            if coll is not None and not coll.all_objects:
                bpy.data.collections.remove(coll)
        if dsp and props.mesh is not None:
            for n in dsp + [dsp_group(n) for n in dsp]:
                vg = props.mesh.vertex_groups.get(n)
                if vg is not None:
                    props.mesh.vertex_groups.remove(vg)
        self.report({'INFO'}, f"본 {len(names)}개 삭제")
        return {'FINISHED'}


_classes = (
    HB_OT_facial_detect_meshes,
    HB_OT_facial_replace_part_markers,
    HB_OT_facial_select_markers,
    HB_OT_facial_markers_only,
    HB_OT_facial_add_markers,
    HB_OT_facial_mirror_markers,
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
