"""Properties for the facial edge-bone / DSP generator (``scene.hb_facial``)."""

import bpy
from bpy.props import (BoolProperty, EnumProperty, FloatProperty, IntProperty,
                       PointerProperty, StringProperty)
from bpy.types import PropertyGroup


def _poll_mesh(_self, obj):
    return obj.type == 'MESH'


class HB_FacialProps(PropertyGroup):
    mesh: PointerProperty(
        name="Mesh",
        description="Character mesh whose edges the generated bones follow",
        type=bpy.types.Object,
        poll=_poll_mesh,
    )

    head_bone: StringProperty(
        name="Head",
        description="Head bone the base face bones are built under. It should "
        "run from the base of the head to the top: the base layout is scaled "
        "to its length",
        default="",
    )

    show_preview: BoolProperty(
        name="Preview Lines",
        description="Draw STR-FACE_UPP / STR-FACE_LOW as lines between their "
        "markers in the viewport",
        default=True,
    )

    count_mode: EnumProperty(
        name="Count",
        description="How many bones to place along each edge path",
        items=[
            ('VERTEX', "Every Vertex",
             "One bone per vertex on the path (follows the topology exactly)"),
            ('EVEN', "Even Count",
             "A fixed number of bones spaced evenly by length. Bone names "
             "stay the same across characters"),
        ],
        default='VERTEX',
    )

    lip_count: IntProperty(
        name="Lip Bones / Side",
        description="Even Count: bones between the lip center and a corner",
        default=8, min=1, soft_max=20,
    )

    lid_count: IntProperty(
        name="Lid Bones / Lid",
        description="Even Count: bones between the eyelid corners",
        default=9, min=1, soft_max=30,
    )

    parent_bone: StringProperty(
        name="Fallback Parent",
        description="Parent for MCH / DSP bones when the base bone they "
        "belong under (MCH_SKULL, MCH-JAW, MSTR-EYE) does not exist",
        default="",
    )

    def_length: FloatProperty(
        name="DEF Length",
        description="Length of generated DEF / DSP bones (they point up, +Z)",
        default=0.0014, min=0.0001, soft_max=0.05, precision=4, unit='LENGTH',
    )

    dsp_control: StringProperty(
        name="Control",
        description="Control bone whose widget should ride the stored vertex",
        default="",
    )

    dsp_armature: PointerProperty(
        name="Armature",
        description="Armature that holds the controls (used to pick a "
        "control name while the mesh is in Edit Mode)",
        type=bpy.types.Object,
        poll=lambda _s, o: o.type == 'ARMATURE',
    )


_classes = (HB_FacialProps,)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.hb_facial = bpy.props.PointerProperty(type=HB_FacialProps)


def unregister():
    del bpy.types.Scene.hb_facial
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
