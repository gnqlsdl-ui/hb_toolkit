"""Properties for the roll mechanism generator (``scene.hb_roll``)."""

import bpy
from bpy.props import FloatProperty, EnumProperty, BoolProperty, StringProperty
from bpy.types import PropertyGroup


# Mechanism types a component root ('def_main') bone can declare via its
# 'hb_type' custom property. Each entry also drives an "Add Component" button
# that stamps the guide set. Append new entries here as more mechanism
# generators are added; the Build step dispatches on this id.
COMPONENT_TYPE_ITEMS = [
    ('footbank', "Footbank", "Foot roll / bank (twist) mechanism"),
]


class HB_RollProps(PropertyGroup):
    guide_segment: StringProperty(
        name="Segment",
        description="Segment name for the guide set (e.g. ForeArm, Hand)",
        default="ForeArm",
    )

    guide_side: EnumProperty(
        name="Side",
        description="Side suffix for the created guides",
        items=[
            ('.L', "Left (.L)", "Left side"),
            ('.R', "Right (.R)", "Right side"),
        ],
        default='.L',
    )

    size: FloatProperty(
        name="Size",
        description="Scale factor for generated helper bone lengths and "
        "control widgets. Guide-driven positions are unaffected",
        default=1.0,
        min=0.01,
        soft_max=5.0,
    )

    mirror_direction: EnumProperty(
        name="Mirror From",
        description="Which side to copy from when mirroring",
        items=[
            ('NEGATIVE_X', "-X to +X", "Copy bones on -X over to +X (e.g. .L -> .R)"),
            ('POSITIVE_X', "+X to -X", "Copy bones on +X over to -X (e.g. .R -> .L)"),
        ],
        default='NEGATIVE_X',
    )

    create_widgets: BoolProperty(
        name="Create Widgets",
        description="Generate and assign custom-shape widgets to controls",
        default=True,
    )

    component_name: StringProperty(
        name="Component",
        description="Component name to write into the 'component' custom "
        "property of the selected bones",
        default="",
    )

    component_type: EnumProperty(
        name="Type",
        description="Mechanism type written into 'hb_type' on the DEF-main "
        "bone. The Build step uses this to pick which generator to run",
        items=COMPONENT_TYPE_ITEMS,
    )


_classes = (HB_RollProps,)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.hb_roll = bpy.props.PointerProperty(type=HB_RollProps)


def unregister():
    del bpy.types.Scene.hb_roll
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
