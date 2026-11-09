"""Shared UI constants for HB Toolkit.

All feature panels MUST import ``CATEGORY`` from here so the N-Panel
category name has a single source of truth (DRY). Feature panels also use
``tab_is`` in their ``poll`` so only the selected tab is drawn.
"""

CATEGORY = "hb_toolkit"

from . import update as update_ui  # noqa: E402
from . import tabs  # noqa: E402
from .tabs import tab_is  # noqa: E402,F401


def register():
    update_ui.register()
    tabs.register()


def unregister():
    tabs.unregister()
    update_ui.unregister()
