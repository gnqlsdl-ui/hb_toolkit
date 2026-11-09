from . import properties
from . import operators
from . import overlay
from . import ui


def register():
    properties.register()
    operators.register()
    overlay.register()
    ui.register()


def unregister():
    ui.unregister()
    overlay.unregister()
    operators.unregister()
    properties.unregister()
