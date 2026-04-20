"""Common bone utilities shared by all features (DRY).

All features needing access to selected bones MUST use these helpers
instead of duplicating mode-checking logic.
"""

import bpy


def is_armature_active(context) -> bool:
    """Return True if the active object is an armature."""
    obj = context.active_object
    return obj is not None and obj.type == 'ARMATURE'


def get_selected_bone_names(context) -> list[str]:
    """Return names of currently selected bones, mode-aware.

    Works in EDIT and POSE modes. Returns an empty list if no armature
    is active or nothing is selected.
    """
    obj = context.active_object
    if obj is None or obj.type != 'ARMATURE':
        return []

    mode = obj.mode
    if mode == 'EDIT':
        return [b.name for b in obj.data.edit_bones if b.select]
    if mode == 'POSE':
        return [b.name for b in obj.pose.bones if b.bone.select]
    return [b.name for b in obj.data.bones if b.select]


def rename_bones(context, name_map: dict[str, str]) -> int:
    """Rename bones using ``name_map`` ({old_name: new_name}).

    Returns the number of bones successfully renamed. Skips entries
    where the new name is empty or unchanged.
    """
    obj = context.active_object
    if obj is None or obj.type != 'ARMATURE':
        return 0

    count = 0
    if obj.mode == 'EDIT':
        bones = obj.data.edit_bones
    else:
        bones = obj.data.bones

    for old_name, new_name in name_map.items():
        if not new_name or old_name == new_name:
            continue
        bone = bones.get(old_name)
        if bone is None:
            continue
        bone.name = new_name
        count += 1
    return count
