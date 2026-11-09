"""Viewport preview of the face squash lines between the marker empties.

Draws STR-FACE_UPP (root -> top) and STR-FACE_LOW (root -> chin) as lines
with a label, live, while the markers are moved. Nothing is added to the
scene; turn it off with the "Preview Lines" checkbox.
"""

import blf
import bpy
import gpu
from bpy_extras.view3d_utils import location_3d_to_region_2d
from gpu_extras.batch import batch_for_shader

from . import template as T

COLORS = {
    "STR-FACE_UPP": (0.25, 0.85, 1.0, 0.9),
    "STR-FACE_LOW": (1.0, 0.6, 0.15, 0.9),
}

_handles = []


def _lines(context):
    props = getattr(context.scene, "hb_facial", None)
    if props is None or not props.show_preview:
        return []
    out = []
    for str_bone, root, tip, _members in T.MARKER_GROUPS:
        a, b = bpy.data.objects.get(root), bpy.data.objects.get(tip)
        if a is None or b is None or not a.visible_get() or not b.visible_get():
            continue
        out.append((str_bone, a.matrix_world.translation.copy(),
                    b.matrix_world.translation.copy()))
    return out


def _draw_view():
    context = bpy.context
    lines = _lines(context)
    if not lines:
        return
    shader = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
    region = context.region
    gpu.state.blend_set('ALPHA')
    gpu.state.depth_test_set('NONE')
    for name, a, b in lines:
        batch = batch_for_shader(shader, 'LINES', {"pos": [a, b]})
        shader.uniform_float("viewportSize", (region.width, region.height))
        shader.uniform_float("lineWidth", 3.0)
        shader.uniform_float("color", COLORS.get(name, (1, 1, 1, 0.9)))
        batch.draw(shader)
    gpu.state.blend_set('NONE')


def _draw_labels():
    context = bpy.context
    lines = _lines(context)
    if not lines:
        return
    region, rv3d = context.region, context.region_data
    font = 0
    blf.size(font, 13)
    for name, a, b in lines:
        p = location_3d_to_region_2d(region, rv3d, (a + b) * 0.5)
        if p is None:
            continue
        r, g, bl, _a = COLORS.get(name, (1, 1, 1, 1))
        blf.color(font, r, g, bl, 1.0)
        blf.position(font, p.x + 8, p.y, 0)
        blf.draw(font, name)


def register():
    space = bpy.types.SpaceView3D
    _handles.append(space.draw_handler_add(_draw_view, (), 'WINDOW', 'POST_VIEW'))
    _handles.append(space.draw_handler_add(_draw_labels, (), 'WINDOW', 'POST_PIXEL'))


def unregister():
    space = bpy.types.SpaceView3D
    while _handles:
        space.draw_handler_remove(_handles.pop(), 'WINDOW')
