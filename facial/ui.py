"""Facial panel: 0 Setup (markers) -> 1 Register Edges -> 2 Build Rig (all steps)."""

import bpy
from bpy.types import Panel

from ..ui import CATEGORY, tab_is
from ..utils.bone_utils import get_active_armature
from .operators import SLOT_ITEMS, dsp_map, edge_map
from . import markers as M


def _shape_keys_box(layout, props):
    from .shapekeys import sculpt_object
    box = layout.box()
    box.label(text="3. 쉐이프키", icon='SHAPEKEY_DATA')
    row = box.row(align=True)
    row.operator("hb.facial_shape_keys", icon='ADD')
    row.operator("hb.facial_shape_transfer", icon='IMPORT')
    row.operator("hb.facial_shape_clear", text="", icon='X')
    mesh = props.mesh
    if mesh is None:
        return
    editing = sculpt_object()
    if editing is not None:
        box.label(text=f"스컬프트 중: {editing.get('hb_sk_key', '')}", icon='SCULPTMODE_HLT')
        row = box.row(align=True)
        row.scale_y = 1.3
        row.operator("hb.facial_shape_apply", icon='CHECKMARK')
        row.operator("hb.facial_shape_cancel", text="", icon='X')
        return
    if mesh.data.shape_keys is not None:
        box.template_list("MESH_UL_shape_keys", "hb_facial", mesh.data.shape_keys, "key_blocks",
                          mesh, "active_shape_key_index", rows=6)
        row = box.row(align=True)
        row.operator("hb.facial_shape_edit", icon='SCULPTMODE_HLT')
        row.operator("hb.facial_shape_mirror", icon='MOD_MIRROR')
    # keyframed poses -> edit meshes (sculpt) -> keys + drivers on the face, last
    row = box.row(align=True)
    row.operator("hb.facial_shape_pose_keys", icon='MESH_GRID')
    row.operator("hb.facial_shape_pose_clear", text="", icon='X')
    row = box.row(align=True)
    row.operator("hb.facial_shape_pose_refresh", icon='FILE_REFRESH')
    row.operator("hb.facial_shape_pose_empty", text="", icon='TRASH')
    row.operator("hb.facial_shape_pose_add", icon='ADD')
    row = box.row(align=True)
    row.operator("hb.facial_shape_mix", icon='MOD_DATA_TRANSFER')
    row.prop(props, "show_sk_overlay", text="", icon='OVERLAY')
    row = box.row(align=True)
    row.operator("hb.facial_shape_brow_work", icon='MOD_MIRROR')
    row.operator("hb.facial_shape_brow_push", icon='EXPORT')
    row.operator("hb.facial_shape_place_brow_corr", text="", icon='SNAP_GRID')
    box.operator("hb.facial_shape_brow_rz", icon='DRIVER_ROTATIONAL_DIFFERENCE')
    row = box.row(align=True)
    row.operator("hb.facial_shape_copy_weights", text="웨이트 복사 (활성 그룹)",
                 icon='GROUP_VERTEX').which = 'ACTIVE'
    row.operator("hb.facial_shape_copy_weights", text="전체", icon='GROUP_VERTEX').which = 'ALL'
    row = box.row(align=True)
    row.operator("hb.facial_shape_copy_keys", text="쉐이프키 복사 (활성)",
                 icon='SHAPEKEY_DATA').which = 'ACTIVE'
    row.operator("hb.facial_shape_copy_keys", text="전체", icon='SHAPEKEY_DATA').which = 'ALL'
    row = box.row(align=True)
    row.operator("hb.facial_skbones_build", icon='BONE_DATA')
    row.operator("hb.facial_skbones_clear", text="", icon='X')
    row = box.row(align=True)
    row.operator("hb.facial_skbones_bake", icon='SHAPEKEY_DATA')
    row.operator("hb.facial_skbones_to_switch", icon='FORWARD')
    box.operator("hb.facial_skbones_update", icon='FILE_REFRESH')
    row = box.row(align=True)
    row.scale_y = 1.3
    row.operator("hb.facial_shape_register", icon='SHAPEKEY_DATA')
    # correctives: taken again from the face with the keys on, registered alone
    box = layout.box()
    box.label(text="4. 코렉티브 / 조합 (얼굴에 등록 후)", icon='MOD_SHRINKWRAP')
    row = box.row(align=True)
    row.operator("hb.facial_shape_corr_rebase", icon='FILE_REFRESH').which = 'SELECTED'
    row.operator("hb.facial_shape_corr_rebase", text="전체").which = 'ALL'
    row = box.row(align=True)
    row.scale_y = 1.3
    row.operator("hb.facial_shape_corr_register", icon='SHAPEKEY_DATA').which = 'ALL'
    row.operator("hb.facial_shape_corr_register", text="선택").which = 'SELECTED'


def _clear_button(layout, stage):
    layout.operator("hb.facial_clear", text="", icon='TRASH').stage = stage


class HB_PT_facial(Panel):
    bl_label = "페이셜"
    bl_idname = "HB_PT_facial"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = CATEGORY
    bl_order = 10

    @classmethod
    def poll(cls, context):
        return tab_is(context, 'FACIAL')

    def draw(self, context):
        layout = self.layout
        props = getattr(context.scene, "hb_facial", None)
        if props is None:
            layout.label(text="HB Toolkit을 다시 로드하세요", icon='ERROR')
            return
        arm = get_active_armature(context) or props.dsp_armature

        layout.prop(props, "dsp_armature")
        layout.prop(props, "mesh")
        if arm is None:
            layout.label(text="아마추어를 한 번 선택하세요 (기억됩니다)", icon='INFO')

        # 0. Base
        box = layout.box()
        box.label(text="0. 설정 (머리 본, 파츠 메쉬, 마커)", icon='BONE_DATA')
        if arm is not None:
            box.prop_search(props, "head_bone", arm.data, "bones")
        sub = box.box()
        row = sub.row()
        row.label(text="파츠 메쉬", icon='MESH_DATA')
        row.operator("hb.facial_detect_meshes", text="자동 감지", icon='VIEWZOOM')
        col = sub.column(align=True)
        col.use_property_split = True
        col.use_property_decorate = False
        for prop in ("eye_mesh_l", "eye_mesh_r", "brow_mesh", "teeth_upp_mesh",
                     "teeth_low_mesh", "tongue_mesh"):
            col.prop(props, prop)
        sub.operator("hb.facial_replace_part_markers", icon='SNAP_ON')
        row = box.row(align=True)
        row.operator("hb.facial_add_markers", icon='EMPTY_DATA')
        _clear_button(row, 'MARKER')
        row = box.row(align=True)
        row.operator("hb.facial_mirror_markers", icon='MOD_MIRROR')
        row.prop(props, "show_preview", text="", icon='HIDE_OFF')
        # 1. Edges
        box = layout.box()
        box.label(text="1. 엣지 등록 (에디트 모드)", icon='EDGESEL')
        stored = edge_map(props.mesh)
        col = box.column(align=True)
        for slot, label, _d in SLOT_ITEMS:
            row = col.row(align=True)
            row.operator("hb.facial_register_edges", text=f"{label} 등록",
                         icon='ADD').slot = slot
            row.label(text=f"엣지 {len(stored[slot])}개" if slot in stored else "-")
            sub = row.row(align=True)
            sub.enabled = slot in stored
            sub.operator("hb.facial_clear_edges", text="", icon='X').slot = slot
        box.prop(props, "count_mode", expand=True)
        if props.count_mode == 'EVEN':
            row = box.row(align=True)
            row.prop(props, "lip_count")
            row.prop(props, "lid_count")

        # 2. Build
        box = layout.box()
        box.label(text="2. 리그 생성", icon='ARMATURE_DATA')
        box.label(text="베이스 > 가이드+리본 > MCH > 컨트롤+쉐이프 > DEF > DSP > 웨이트")
        row = box.row(align=True)
        row.scale_y = 1.5
        row.operator("hb.facial_build_rig", icon='PLAY')
        _shape_keys_box(layout, props)        # (its own box, below this one)
        row = box.row()
        row.prop(props, "show_steps", icon='TRIA_DOWN' if props.show_steps else 'TRIA_RIGHT',
                 emboss=False)
        if not props.show_steps:
            return
        col = box.column(align=True)
        for op, stage, icon in (("hb.facial_build_base", 'BASE', 'BONE_DATA'),
                                ("hb.facial_generate", 'GUIDE', 'OUTLINER_OB_EMPTY'),
                                ("hb.facial_build_mch", 'MCH', 'CONSTRAINT_BONE'),
                                ("hb.facial_build_controls", 'CTRL', 'POSE_HLT'),
                                ("hb.facial_build_all", 'DEF', 'MODIFIER')):
            row = col.row(align=True)
            row.operator(op, icon=icon)
            _clear_button(row, stage)
        row = col.row(align=True)
        row.operator("hb.facial_face_weights", icon='MOD_VERTEX_WEIGHT')
        row.operator("hb.facial_save_weights", text="", icon='FILE_TICK')
        row = col.row(align=True)
        row.operator("hb.facial_spread_edge_weights", icon='MOD_SMOOTH')
        row.operator("hb.facial_mirror_weights", icon='MOD_MIRROR')
        row = col.row(align=True)
        row.operator("hb.facial_smooth_weights", icon='MOD_SMOOTH')
        row.operator("hb.facial_build_lattices", icon='MOD_LATTICE')
        row.operator("hb.facial_clear_lattices", text="", icon='X')
        if arm is not None:
            box.prop_search(props, "parent_bone", arm.data, "bones")
        box.prop(props, "def_length")
        sub = box.box()
        sub.label(text="DSP 지정: 컨트롤 -> 버텍스 (자동 = 가장 가까운 버텍스)", icon='MESH_DATA')
        if arm is not None:
            sub.prop_search(props, "dsp_control", arm.data, "bones")
        else:
            sub.prop(props, "dsp_control")
        row = sub.row(align=True)
        row.operator("hb.facial_dsp_store", icon='VERTEXSEL')
        row.operator("hb.facial_dsp_forget", text="", icon='X')
        sub.operator("hb.facial_dsp_store_nearest", icon='SNAP_VERTEX')
        row = sub.row(align=True)
        row.label(text=f"저장됨: {len(dsp_map(props.mesh))}")
        row.operator("hb.facial_dsp_build", text="DSP만 생성")


class HB_PT_facial_markers(Panel):
    """마커를 클릭하면 선택합니다 (Shift: 추가). 그룹 이름을 클릭하면 그룹 전체를 선택합니다."""

    bl_label = "마커"
    bl_idname = "HB_PT_facial_markers"
    bl_parent_id = "HB_PT_facial"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = CATEGORY

    @classmethod
    def poll(cls, context):
        return tab_is(context, 'FACIAL')

    def draw_header(self, context):
        locked = M.LOCK_KEY in context.scene
        self.layout.operator("hb.facial_markers_only", text="",
                             icon='RESTRICT_SELECT_ON' if locked else 'RESTRICT_SELECT_OFF',
                             depress=locked)

    def draw(self, context):
        layout = self.layout
        if M.LOCK_KEY in context.scene:
            layout.label(text="마커만 선택: 다른 오브젝트는 선택 불가", icon='LOCKED')
        col = layout.column(align=True)
        for label, names in M.marker_groups():
            row = col.row(align=True)
            sub = row.row(align=True)
            sub.scale_x = 0.9
            sub.operator("hb.facial_select_markers", text=label).names = ",".join(names)
            for n in names:
                exists = n in bpy.data.objects
                cell = row.row(align=True)
                cell.enabled = exists
                op = cell.operator("hb.facial_select_markers", text=M.short_label(n),
                                   depress=exists and bpy.data.objects[n].select_get())
                op.names = n


_classes = (HB_PT_facial, HB_PT_facial_markers)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
