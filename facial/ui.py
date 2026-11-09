"""Facial panel: guide sets -> edge DEF/MCH bones, and DSP bones."""

import bpy
from bpy.types import Panel

from ..ui import CATEGORY, tab_is
from ..utils.bone_utils import get_active_armature
from .operators import DSP_KEY, dsp_map
from .properties import GUIDE_SET_ITEMS


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
        if arm is not None:
            layout.prop_search(props, "parent_bone", arm.data, "bones")

        box = layout.box()
        box.label(text="1. Add Guides (3D cursor / eye rim)", icon='BONE_DATA')
        row = box.row(align=True)
        for set_id, label, _d in GUIDE_SET_ITEMS:
            row.operator("hb.facial_add_guides", text=label).guide_set = set_id
        box.operator("hb.facial_mirror_guides", icon='MOD_MIRROR')

        box = layout.box()
        box.label(text="2. Build Edge Bones", icon='MODIFIER')
        box.prop(props, "count_mode", expand=True)
        if props.count_mode == 'EVEN':
            row = box.row(align=True)
            row.prop(props, "lip_count")
            row.prop(props, "lid_count")
        row = box.row(align=True)
        row.prop(props, "make_mch")
        row.prop(props, "def_length", text="Length")
        row = box.row(align=True)
        row.operator("hb.facial_build", icon='CHECKMARK')
        row.operator("hb.facial_clear", text="", icon='TRASH')

        box = layout.box()
        box.label(text="3. DSP Bones", icon='MESH_DATA')
        box.prop(props, "dsp_armature")
        if arm is not None:
            box.prop_search(props, "dsp_control", arm.data, "bones")
        else:
            box.prop(props, "dsp_control")
        row = box.row(align=True)
        row.operator("hb.facial_dsp_store", icon='VERTEXSEL')
        row.operator("hb.facial_dsp_forget", text="", icon='X')
        box.operator("hb.facial_dsp_store_nearest", icon='SNAP_VERTEX')
        stored = len(dsp_map(props.mesh)) if props.mesh and DSP_KEY in props.mesh else 0
        box.label(text=f"Stored on mesh: {stored}")
        row = box.row(align=True)
        row.operator("hb.facial_dsp_build", icon='CHECKMARK')
        row.operator("hb.facial_dsp_clear", text="", icon='TRASH')


_classes = (HB_PT_facial,)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
