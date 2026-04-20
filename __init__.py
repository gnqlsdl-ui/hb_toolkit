"""HB Toolkit - Blender 5+ rigging extension.

Metadata lives in ``blender_manifest.toml`` (Blender Extension format).
This module only wires up the per-feature register / unregister hooks.
"""

from . import ui
from . import utils
from . import renamer


_modules = (ui, utils, renamer)


def register():
    for mod in _modules:
        if hasattr(mod, "register"):
            mod.register()


def unregister():
    for mod in reversed(_modules):
        if hasattr(mod, "unregister"):
            mod.unregister()


if __name__ == "__main__":
    register()
