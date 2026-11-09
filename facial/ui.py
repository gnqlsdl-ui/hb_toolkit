"""Facial panel: 0 Base -> 1 Edges/Guides -> 2 MCH -> 3 Build All (DEF + DSP)."""

import bpy
from bpy.types import Panel

from ..ui import CATEGORY, tab_is
from ..utils.bone_utils import get_active_armature
from .operators import SLOT_ITEMS, dsp_map, edge_map


def _clear_button(layout, stage):
    layout.operator("hb.facial_clear", text="", icon='TRASH').stage = stage


class HB_PT_facial(Panel):
    bl_label = "facial"
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
            layout.label(text="Reload HB Toolkit", icon='ERROR')
            return
        arm = get_active_armature(context) or props.dsp_armature

        layout.prop(props, "mesh")
        if arm is None:
            layout.label(text="Select the armature to build", icon='INFO')

        # 0. Base
        box = layout.box()
        box.label(text="0. Base (Head -> face bones)", icon='BONE_DATA')
        if arm is not None:
            box.prop_search(props, "head_bone", arm.data, "bones")
        row = box.row(align=True)
        row.operator("hb.facial_add_markers", icon='EMPTY_DATA')
        _clear_button(row, 'MARKER')
        box.prop(props, "show_preview")
        row = box.row(align=True)
        row.operator("hb.facial_build_base", icon='CHECKMARK')
        _clear_button(row, 'BASE')

        # 1. Edges -> guides
        box = layout.box()
        box.label(text="1. Register Edges (Edit Mode) -> Guides", icon='EDGESEL')
        stored = edge_map(props.mesh)
        col = box.column(align=True)
        for slot, label, _d in SLOT_ITEMS:
            row = col.row(align=True)
            row.operator("hb.facial_register_edges", text=f"Register {label}",
                         icon='ADD').slot = slot
            row.label(text=f"{len(stored[slot])} edges" if slot in stored else "-")
            sub = row.row(align=True)
            sub.enabled = slot in stored
            sub.operator("hb.facial_clear_edges", text="", icon='X').slot = slot
        box.prop(props, "count_mode", expand=True)
        if props.count_mode == 'EVEN':
            row = box.row(align=True)
            row.prop(props, "lip_count")
            row.prop(props, "lid_count")
        row = box.row(align=True)
        row.operator("hb.facial_generate", icon='OUTLINER_OB_EMPTY')
        _clear_button(row, 'GUIDE')

        # 2. MCH
        box = layout.box()
        box.label(text="2. MCH + Setup", icon='CONSTRAINT_BONE')
        if arm is not None:
            box.prop_search(props, "parent_bone", arm.data, "bones")
        row = box.row(align=True)
        row.operator("hb.facial_build_mch", icon='CHECKMARK')
        _clear_button(row, 'MCH')

        # 3. All bones
        box = layout.box()
        box.label(text="3. Build All (DEF + DSP)", icon='MODIFIER')
        box.prop(props, "def_length")
        sub = box.box()
        sub.label(text="DSP: control -> vertex", icon='MESH_DATA')
        sub.prop(props, "dsp_armature")
        if arm is not None:
            sub.prop_search(props, "dsp_control", arm.data, "bones")
        else:
            sub.prop(props, "dsp_control")
        row = sub.row(align=True)
        row.operator("hb.facial_dsp_store", icon='VERTEXSEL')
        row.operator("hb.facial_dsp_forget", text="", icon='X')
        sub.operator("hb.facial_dsp_store_nearest", icon='SNAP_VERTEX')
        row = sub.row(align=True)
        row.label(text=f"Stored: {len(dsp_map(props.mesh))}")
        row.operator("hb.facial_dsp_build", text="DSP Only")
        row = box.row(align=True)
        row.scale_y = 1.3
        row.operator("hb.facial_build_all", icon='CHECKMARK')
        _clear_button(row, 'DEF')


_classes = (HB_PT_facial,)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
