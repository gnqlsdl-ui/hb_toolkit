import bpy
from bpy.props import StringProperty, BoolProperty
from bpy.types import PropertyGroup


class HB_PG_renamer(PropertyGroup):
    new_name: StringProperty(
        name="New Name",
        description="Base name for renaming. Numbering is appended automatically when multiple bones are selected",
        default="Bone",
    )
    prefix: StringProperty(
        name="Prefix",
        description="Prefix to add or remove",
        default="",
    )
    suffix: StringProperty(
        name="Suffix",
        description="Suffix to add or remove",
        default="",
    )
    search_text: StringProperty(
        name="Search",
        description="Substring to search for in bone names",
        default="",
    )
    replace_text: StringProperty(
        name="Replace",
        description="Substring to replace matches with",
        default="",
    )
    case_sensitive: BoolProperty(
        name="Case Sensitive",
        description="Use case-sensitive matching for search & replace",
        default=True,
    )


_classes = (HB_PG_renamer,)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.hb_renamer = bpy.props.PointerProperty(type=HB_PG_renamer)


def unregister():
    if hasattr(bpy.types.Scene, "hb_renamer"):
        del bpy.types.Scene.hb_renamer
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
