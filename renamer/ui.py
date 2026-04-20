"""Renamer panel.

Registered as a standalone Panel under the shared HB Toolkit N-Panel
category. Each feature owns its own Panel; they all share the same
``CATEGORY`` constant from ``ui/__init__.py``.
"""

import bpy
from bpy.types import Panel

from ..ui import CATEGORY
from ..utils.bone_utils import is_armature_active


class HB_PT_renamer(Panel):
    bl_label = "renamer"
    bl_idname = "HB_PT_renamer"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = CATEGORY

    def draw(self, context):
        layout = self.layout

        if not is_armature_active(context):
            layout.label(text="Select an armature", icon='INFO')
            return

        props = context.scene.hb_renamer

        col = layout.column(align=True)
        col.prop(props, "new_name")
        col.operator("hb.renamer_rename", icon='OUTLINER_DATA_FONT')

        layout.separator()

        col = layout.column(align=True)
        col.prop(props, "prefix")
        row = col.row(align=True)
        row.operator("hb.renamer_add_prefix", text="Add Prefix", icon='ADD')
        row.operator("hb.renamer_remove_prefix", text="Remove", icon='REMOVE')

        col = layout.column(align=True)
        col.prop(props, "suffix")
        row = col.row(align=True)
        row.operator("hb.renamer_add_suffix", text="Add Suffix", icon='ADD')
        row.operator("hb.renamer_remove_suffix", text="Remove", icon='REMOVE')

        layout.separator()

        col = layout.column(align=True)
        col.label(text="Search & Replace:")
        col.prop(props, "search_text", text="Find")
        col.prop(props, "replace_text", text="Replace")
        col.prop(props, "case_sensitive")
        col.operator("hb.renamer_search_replace", icon='VIEWZOOM')


_classes = (HB_PT_renamer,)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
