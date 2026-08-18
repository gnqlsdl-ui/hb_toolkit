"""Shared UI constants for HB Toolkit.

All feature panels MUST import ``CATEGORY`` from here so the N-Panel
category name has a single source of truth (DRY).
"""

from . import update as update_ui

CATEGORY = "hb_toolkit"


def register():
    update_ui.register()


def unregister():
    update_ui.unregister()
