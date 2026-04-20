"""Renamer operators.

All operators rely on shared helpers in ``utils.bone_utils`` to avoid
duplicating selection / mode logic (DRY).
"""

import re

import bpy
from bpy.types import Operator

from ..utils.bone_utils import (
    get_selected_bone_names,
    is_armature_active,
    rename_bones,
)


class _RenamerBase:
    """Mixin providing the standard poll for renamer operators."""

    @classmethod
    def poll(cls, context):
        return is_armature_active(context) and len(get_selected_bone_names(context)) > 0


def _report_result(op, count: int, action: str) -> set:
    if count == 0:
        op.report({'WARNING'}, f"{action}: no bones changed")
        return {'CANCELLED'}
    op.report({'INFO'}, f"{action}: {count} bone(s) updated")
    return {'FINISHED'}


class HB_OT_renamer_rename(_RenamerBase, Operator):
    bl_idname = "hb.renamer_rename"
    bl_label = "Rename"
    bl_description = "Rename selected bones using the base name (numbered if multiple)"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        props = context.scene.hb_renamer
        base = props.new_name.strip()
        if not base:
            self.report({'WARNING'}, "New name is empty")
            return {'CANCELLED'}

        names = get_selected_bone_names(context)
        if len(names) == 1:
            name_map = {names[0]: base}
        else:
            width = max(2, len(str(len(names))))
            name_map = {
                old: f"{base}.{str(i + 1).zfill(width)}"
                for i, old in enumerate(names)
            }
        return _report_result(self, rename_bones(context, name_map), "Rename")


class HB_OT_renamer_add_prefix(_RenamerBase, Operator):
    bl_idname = "hb.renamer_add_prefix"
    bl_label = "Add Prefix"
    bl_description = "Prepend the prefix to selected bone names"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        prefix = context.scene.hb_renamer.prefix
        if not prefix:
            self.report({'WARNING'}, "Prefix is empty")
            return {'CANCELLED'}
        name_map = {n: f"{prefix}{n}" for n in get_selected_bone_names(context)}
        return _report_result(self, rename_bones(context, name_map), "Add Prefix")


class HB_OT_renamer_add_suffix(_RenamerBase, Operator):
    bl_idname = "hb.renamer_add_suffix"
    bl_label = "Add Suffix"
    bl_description = "Append the suffix to selected bone names"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        suffix = context.scene.hb_renamer.suffix
        if not suffix:
            self.report({'WARNING'}, "Suffix is empty")
            return {'CANCELLED'}
        name_map = {n: f"{n}{suffix}" for n in get_selected_bone_names(context)}
        return _report_result(self, rename_bones(context, name_map), "Add Suffix")


class HB_OT_renamer_remove_prefix(_RenamerBase, Operator):
    bl_idname = "hb.renamer_remove_prefix"
    bl_label = "Remove Prefix"
    bl_description = "Remove the prefix from selected bone names if present"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        prefix = context.scene.hb_renamer.prefix
        if not prefix:
            self.report({'WARNING'}, "Prefix is empty")
            return {'CANCELLED'}
        name_map = {
            n: n[len(prefix):]
            for n in get_selected_bone_names(context)
            if n.startswith(prefix)
        }
        return _report_result(self, rename_bones(context, name_map), "Remove Prefix")


class HB_OT_renamer_remove_suffix(_RenamerBase, Operator):
    bl_idname = "hb.renamer_remove_suffix"
    bl_label = "Remove Suffix"
    bl_description = "Remove the suffix from selected bone names if present"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        suffix = context.scene.hb_renamer.suffix
        if not suffix:
            self.report({'WARNING'}, "Suffix is empty")
            return {'CANCELLED'}
        name_map = {
            n: n[: -len(suffix)]
            for n in get_selected_bone_names(context)
            if n.endswith(suffix)
        }
        return _report_result(self, rename_bones(context, name_map), "Remove Suffix")


class HB_OT_renamer_search_replace(_RenamerBase, Operator):
    bl_idname = "hb.renamer_search_replace"
    bl_label = "Search & Replace"
    bl_description = "Replace occurrences of the search text in selected bone names"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        props = context.scene.hb_renamer
        search = props.search_text
        if not search:
            self.report({'WARNING'}, "Search text is empty")
            return {'CANCELLED'}

        flags = 0 if props.case_sensitive else re.IGNORECASE
        pattern = re.compile(re.escape(search), flags)
        name_map = {}
        for old in get_selected_bone_names(context):
            new = pattern.sub(props.replace_text, old)
            if new != old:
                name_map[old] = new
        return _report_result(self, rename_bones(context, name_map), "Search & Replace")


_classes = (
    HB_OT_renamer_rename,
    HB_OT_renamer_add_prefix,
    HB_OT_renamer_add_suffix,
    HB_OT_renamer_remove_prefix,
    HB_OT_renamer_remove_suffix,
    HB_OT_renamer_search_replace,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
