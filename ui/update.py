"""Check / install HB Toolkit updates from the GitHub extension repo."""

import json
from pathlib import Path

import bpy
from bpy.types import Operator

PKG_ID = "hb_toolkit"
EXTENSION_INDEX_URL = (
    "https://raw.githubusercontent.com/gnqlsdl-ui/hb_toolkit/gh-pages/index.json"
)


def _iter_repos():
    return list(enumerate(bpy.context.preferences.extensions.repos))


def _index_path(repo):
    return Path(repo.directory) / ".blender_ext" / "index.json"


def _repo_has_package(repo, pkg_id):
    path = _index_path(repo)
    if not path.is_file():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return any(item.get("id") == pkg_id for item in data.get("data", []))


def _find_hb_repo():
    for index, repo in _iter_repos():
        if _repo_has_package(repo, PKG_ID):
            return index, repo
        if (getattr(repo, "remote_url", "") or "") == EXTENSION_INDEX_URL:
            return index, repo
    return None, None


def _ensure_remote_repo():
    """Add the GitHub index as a Blender remote repository if missing."""
    index, repo = _find_hb_repo()
    if repo is not None:
        return index, repo
    bpy.ops.preferences.extension_repo_add(
        name="HB Toolkit",
        remote_url=EXTENSION_INDEX_URL,
        use_sync_on_startup=True,
        type='REMOTE',
    )
    return _find_hb_repo()


class HB_OT_toolkit_update(Operator):
    bl_idname = "hb.toolkit_update"
    bl_label = "Check for Updates"
    bl_description = ("Sync the GitHub extension repo and install the latest "
                      "HB Toolkit version")

    def execute(self, context):
        if not EXTENSION_INDEX_URL:
            self.report({'WARNING'}, "Extension repo URL is not configured")
            return {'CANCELLED'}

        index, repo = _ensure_remote_repo()
        if repo is None:
            self.report({'ERROR'}, "Could not add the HB Toolkit extension repo")
            return {'CANCELLED'}

        bpy.ops.extensions.repo_sync(repo_index=index)
        bpy.ops.extensions.package_install(
            repo_index=index,
            pkg_id=PKG_ID,
            enable_on_install=True,
        )
        self.report({'INFO'}, "HB Toolkit update finished - restart if the panel looks stale")
        return {'FINISHED'}


_classes = (HB_OT_toolkit_update,)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
