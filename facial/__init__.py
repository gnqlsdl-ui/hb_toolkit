from . import properties
from . import operators
from . import rig
from . import weights
from . import lattice
from . import shapekeys
from . import skbones
from . import overlay
from . import sk_overlay
from . import ui


def register():
    properties.register()
    operators.register()
    rig.register()
    weights.register()
    lattice.register()
    shapekeys.register()
    skbones.register()
    overlay.register()
    sk_overlay.register()
    ui.register()


def unregister():
    ui.unregister()
    sk_overlay.unregister()
    overlay.unregister()
    skbones.unregister()
    shapekeys.unregister()
    lattice.unregister()
    weights.unregister()
    rig.unregister()
    operators.unregister()
    properties.unregister()
