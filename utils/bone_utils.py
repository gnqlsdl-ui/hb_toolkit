"""Common bone utilities shared by all features (DRY).

All features needing access to selected bones MUST use these helpers
instead of duplicating mode-checking logic.

Both EDIT and POSE modes are first-class supported. Selection is read
through Blender's context members (``selected_editable_bones`` and
``selected_pose_bones``) so this module is robust against Blender 5.0's
removal of the ``Bone.select`` attribute (replaced by the new Armature
Collections system).
"""

import uuid

import bpy

# Prefix used by the 2-pass rename algorithm. Anything starting with this
# string MUST be considered a transient name and never persisted.
_TMP_PREFIX = "__hb_tmp_"


def is_armature_active(context) -> bool:
    """Return True if the active object is an armature."""
    obj = context.active_object
    return obj is not None and obj.type == 'ARMATURE'


def get_active_armature(context):
    """Return the active armature object, or None."""
    obj = context.active_object
    if obj is not None and obj.type == 'ARMATURE':
        return obj
    return None


# Recognised side suffixes, longest first so ``.L`` is preferred over ``L``.
_SIDE_SUFFIXES = (".L", ".R", "_L", "_R", "-L", "-R")
_SIDE_FLIP = {"L": "R", "R": "L"}


def split_side(name: str):
    """Split a bone name into ``(base, suffix)`` where suffix is the side.

    Returns ``(name, "")`` when no recognised side suffix is present.
    """
    for suf in _SIDE_SUFFIXES:
        if name.endswith(suf):
            return name[: -len(suf)], suf
    return name, ""


def flip_side(suffix: str) -> str:
    """Flip a side suffix (e.g. ``.L`` -> ``.R``). Empty stays empty."""
    if not suffix:
        return ""
    return suffix[:-1] + _SIDE_FLIP.get(suffix[-1], suffix[-1])


def flip_side_name(name: str) -> str:
    """Return ``name`` with its side suffix flipped (``.L`` <-> ``.R``)."""
    base, suf = split_side(name)
    return base + flip_side(suf)


def _get_bones_collection(obj):
    """Return the writable bone collection for the object's current mode.

    EDIT mode -> ``edit_bones``; POSE/OBJECT modes -> ``data.bones``.
    Both collections expose a writable ``.name`` per bone, so renaming
    works in either mode without an explicit mode switch.
    """
    if obj.mode == 'EDIT':
        return obj.data.edit_bones
    return obj.data.bones


def get_selected_bone_names(context) -> list[str]:
    """Return names of currently selected bones, mode-aware.

    Works in EDIT and POSE modes. Returns an empty list if no armature
    is active or nothing is selected.

    Uses Blender context members (``selected_editable_bones`` /
    ``selected_pose_bones``) instead of touching ``Bone.select``, which
    was removed in Blender 5.0.
    """
    obj = context.active_object
    if obj is None or obj.type != 'ARMATURE':
        return []

    mode = obj.mode
    if mode == 'EDIT':
        bones = getattr(context, "selected_editable_bones", None) or []
        return [b.name for b in bones]
    if mode == 'POSE':
        bones = getattr(context, "selected_pose_bones", None) or []
        return [b.name for b in bones]
    return []


def _rename_two_pass(bones, pending: dict[str, str]) -> int:
    """Apply ``pending`` ({old: new}) using a two-pass swap-proof rename."""
    tmp_to_final: dict[str, str] = {}
    for old, new in pending.items():
        bone = bones.get(old)
        if bone is None:
            continue
        tmp = f"{_TMP_PREFIX}{uuid.uuid4().hex[:12]}"
        bone.name = tmp
        tmp_to_final[tmp] = new

    count = 0
    for tmp, new in tmp_to_final.items():
        bone = bones.get(tmp)
        if bone is None:
            continue
        bone.name = new
        count += 1
    return count


def rename_bones(context, name_map: dict[str, str]) -> int:
    """Rename bones safely using ``name_map`` ({old_name: new_name}).

    Uses a **two-pass** strategy so that arbitrary rename plans (including
    swaps like ``A->B, B->A`` and chained moves) succeed without Blender
    auto-suffixing names due to transient collisions:

    1. Pass 1: every source bone is renamed to a unique temporary name.
    2. Pass 2: each temporary bone is renamed to its final target name.

    POSE mode handling: some Blender environments do not flush
    ``data.bones[*].name`` writes immediately while in POSE mode. To
    guarantee correctness we temporarily switch to EDIT mode, perform
    the rename on ``edit_bones``, then restore POSE mode. Selection is
    preserved by Blender across mode switches.

    Returns the number of bones whose final name was applied.
    """
    obj = context.active_object
    if obj is None or obj.type != 'ARMATURE':
        return 0

    prev_mode = obj.mode
    must_switch = (prev_mode == 'POSE')
    if must_switch:
        bpy.ops.object.mode_set(mode='EDIT')
    try:
        bones = _get_bones_collection(obj)
        pending = {
            old: new
            for old, new in name_map.items()
            if new and old != new and bones.get(old) is not None
        }
        if not pending:
            return 0
        return _rename_two_pass(bones, pending)
    finally:
        if must_switch:
            bpy.ops.object.mode_set(mode=prev_mode)
