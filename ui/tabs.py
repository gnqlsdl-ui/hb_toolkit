"""Tab bar for the HB Toolkit N-Panel category.

One N-Panel category holds every feature. A row of tab buttons at the top
picks which feature's panel is shown; feature panels hide themselves with
``poll`` via :func:`tab_is`. The update button is an icon at the end of
the tab row.
"""

import bpy
from bpy.props import EnumProperty
from bpy.types import Panel

from . import CATEGORY  # defined in ui/__init__ before this module loads

# (id, label, icon). Append here when a feature gets its own tab.
TABS = [
    ('RENAMER', "Renamer", 'OUTLINER_DATA_FONT'),
    ('COMPONENT', "Component", 'BONE_DATA'),
    ('FACIAL', "Facial", 'USER'),
]


def tab_is(context, tab_id):
    """True when ``tab_id`` is the selected HB Toolkit tab."""
    return getattr(context.window_manager, "hb_tab", None) == tab_id


class HB_PT_tabs(Panel):
    bl_label = "HB Toolkit"
    bl_idname = "HB_PT_tabs"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = CATEGORY
    bl_options = {'HIDE_HEADER'}
    bl_order = 0

    def draw(self, context):
        row = self.layout.row(align=True)
        row.scale_y = 1.3
        row.prop(context.window_manager, "hb_tab", expand=True)
        # Shared button: "Check for Updates" as an icon at the end of the row.
        row.separator()
        row.operator("hb.toolkit_update", text="", icon='FILE_REFRESH')


def register():
    bpy.types.WindowManager.hb_tab = EnumProperty(
        name="HB Toolkit Tab",
        items=[(i, label, "", icon, n) for n, (i, label, icon) in enumerate(TABS)],
        default=TABS[0][0],
    )
    bpy.utils.register_class(HB_PT_tabs)


def unregister():
    bpy.utils.unregister_class(HB_PT_tabs)
    del bpy.types.WindowManager.hb_tab
