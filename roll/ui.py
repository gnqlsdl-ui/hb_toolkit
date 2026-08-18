"""Roll generator panel.

Standalone Panel under the shared HB Toolkit N-Panel category.
"""

import bpy
from bpy.types import Panel

from ..ui import CATEGORY
from ..utils.bone_utils import is_armature_active, get_active_armature
from .operators import (active_bone_item, COMPONENT_PROP, DEF_MAIN_PROP,
                        TYPE_PROP, ROLE_PROP)
from .properties import COMPONENT_TYPE_ITEMS


class HB_PT_roll(Panel):
    bl_label = "HB Component"
    bl_idname = "HB_PT_roll"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = CATEGORY

    def draw(self, context):
        layout = self.layout
        props = getattr(context.scene, "hb_roll", None)
        if props is None:
            layout.label(text="Reload HB Toolkit", icon='ERROR')
            return

        if not is_armature_active(context):
            layout.label(text="Select an armature to run operators",
                         icon='INFO')

        box = layout.box()
        box.label(text="1. Add Component (at 3D cursor)", icon='BONE_DATA')
        row = box.row(align=True)
        row.prop(props, "guide_segment", text="")
        row.prop(props, "guide_side", text="")
        box.prop(props, "component_name", text="Name")
        for type_id, label, _desc in COMPONENT_TYPE_ITEMS:
            op = box.operator("hb.roll_create_guides", text=label, icon='ADD')
            op.comp_type = type_id

        box = layout.box()
        box.label(text="2. Place Roll Guides (orient main first)", icon='SNAP_ON')
        box.operator("hb.roll_fit_guides", icon='CON_TRACKTO')
        box.operator("hb.roll_clear_roll_guides", icon='X')

        box = layout.box()
        box.label(text="3. Build (select guides)", icon='MODIFIER')
        box.prop(props, "size")
        box.prop(props, "create_widgets")
        box.operator("hb.roll_build", icon='CHECKMARK')

        box = layout.box()
        box.label(text="4. Clear (select guides)", icon='X')
        box.operator("hb.roll_clear_build", icon='TRASH')
        box.operator("hb.roll_clear_roll_guides", icon='BONE_DATA')

        box = layout.box()
        box.label(text="5. Mirror (select bones)", icon='MOD_MIRROR')
        box.prop(props, "mirror_direction", text="")
        box.operator("hb.roll_mirror", icon='ARROW_LEFTRIGHT')

        box = layout.box()
        box.label(text="Component Name (select bones)", icon='PROPERTIES')
        row = box.row(align=True)
        row.prop(props, "component_name", text="")
        row.operator("hb.roll_set_component", text="", icon='CHECKMARK')
        box.operator("hb.roll_clear_component", text="Clear Tags", icon='TRASH')

        active = active_bone_item(get_active_armature(context), context)
        if active is not None:
            col = box.column(align=True)
            col.label(text="Active: " + active.name)
            col.label(text="component: "
                      + str(active.get(COMPONENT_PROP, "-")))
            col.label(text="role: " + str(active.get(ROLE_PROP, "-")))
            if active.get(DEF_MAIN_PROP):
                col.label(text="DEF main  |  type: "
                          + str(active.get(TYPE_PROP, "-")), icon='CHECKMARK')

        box = layout.box()
        box.operator("hb.toolkit_update", icon='FILE_REFRESH')


_classes = (HB_PT_roll,)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
