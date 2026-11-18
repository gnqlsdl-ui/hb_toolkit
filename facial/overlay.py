"""Viewport preview of the marker empties.

Every line marker pair (Root -> Tip) and every chain (brow, tongue, teeth)
is drawn as a colored line / polyline with its name, a green dot on the
first marker (head) and a red dot on the last (tail); chain middle points
get the part color. Point markers get a yellow dot. Drawn live while the markers
move; nothing is added to the scene. Toggle with "Preview" in the panel.
"""

import blf
import bpy
import gpu
from bpy_extras.view3d_utils import location_3d_to_region_2d
from gpu_extras.batch import batch_for_shader

from . import template as T
from .parts import catmull_rom

ROOT_COLOR = (0.2, 1.0, 0.35, 1.0)    # head
TIP_COLOR = (1.0, 0.25, 0.25, 1.0)    # tail
POINT_COLOR = (1.0, 0.9, 0.2, 1.0)

# Line color per bone (part); anything missing is white.
LINE_COLORS = {
    "STR-FACE_UPP": (0.25, 0.85, 1.0, 0.9),
    "STR-FACE_LOW": (1.0, 0.6, 0.15, 0.9),
    "MSTR-BROW.L": (0.75, 0.45, 1.0, 0.9),
    "MSTR-BROW.R": (0.75, 0.45, 1.0, 0.9),
    "MSTR-EAR.L": (0.35, 0.5, 1.0, 0.9),
    "MSTR-EAR.R": (0.35, 0.5, 1.0, 0.9),
    "MSTR-NOSE": (1.0, 0.85, 0.3, 0.9),
    "MSTR-EYE.L": (0.3, 0.85, 1.0, 0.9),
    "MSTR-EYE.R": (0.3, 0.85, 1.0, 0.9),
    "NOSE": (1.0, 0.75, 0.2, 0.9),
    "EYELID": (0.3, 0.85, 1.0, 0.9),
    "BROW": (0.75, 0.45, 1.0, 0.9),
    "TEETH_UPP": (0.95, 0.95, 0.95, 0.9),
    "TEETH_LOW": (0.7, 0.7, 0.7, 0.9),
    "TONGUE": (1.0, 0.45, 0.7, 0.9),
    "CHEEK": (1.0, 0.55, 0.35, 0.9),
    "JAWLINE": (0.2, 0.85, 0.75, 0.9),
}

_handles = []


def _visible(name):
    obj = bpy.data.objects.get(name)
    if obj is None or not obj.visible_get():
        return None
    return obj.matrix_world.translation.copy()


def _collect(context):
    """([(label, color key, [points...])], [(name, point)]) of visible markers.

    Lines are 2-point paths; chains are several points in marker order
    (brow inner -> outer, tongue root -> tip, teeth front -> back per side).
    """
    props = getattr(context.scene, "hb_facial", None)
    if props is None or not props.show_preview:
        return [], []
    paths, points = [], []
    for bone, root, tip, _members in T.MARKER_GROUPS:
        a, b = _visible(root), _visible(tip)
        if a is not None and b is not None:
            paths.append((bone, bone, [a, b]))
    for chain, spec in T.CHAINS.items():
        sides = (".L", ".R") if spec["sided"] else ("",)
        for s in sides:
            names = [n for n, _p in spec["markers"]]
            if s == ".R":
                names = [T.flip(n) for n in names]
            pts = [p for p in (_visible(n) for n in names) if p is not None]
            if len(pts) >= 2:
                paths.append((chain + s, chain, pts))
    for mk in T.POINT_MARKERS:
        p = _visible(mk)
        if p is not None:
            points.append((mk, p))
    return paths, points


def _outlines(context):
    """[(color key, closed point loop)] for OUTLINES whose markers all exist."""
    props = getattr(context.scene, "hb_facial", None)
    if props is None or not props.show_preview:
        return []
    out = []
    for key, names, style in T.OUTLINES:
        pts = [_visible(n) for n in names]
        if all(p is not None for p in pts):
            out.append((key, _eye_shape(*pts) if style == "eye" else pts + [pts[0]]))
    return out


def _eye_shape(*pts):
    """Eye outline: In, upper lid points, Out, lower lid points (closed).

    Each lid is its own smooth curve (Catmull-Rom) through its markers from
    corner to corner, so the curve passes through every marker and the two
    lids meet at pointed corners like a real eye.
    """
    n = len(pts)
    half = n // 2                       # index of Out
    upper = list(pts[:half + 1])        # In ... Out
    lower = list(pts[half:]) + [pts[0]]  # Out ... In
    return catmull_rom(upper) + catmull_rom(lower)[1:]


def _draw_view():
    context = bpy.context
    paths, points = _collect(context)
    outlines = _outlines(context)
    if not paths and not points and not outlines:
        return
    region = context.region
    gpu.state.blend_set('ALPHA')
    gpu.state.depth_test_set('NONE')

    line_sh = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
    for key, loop in outlines:                       # nose triangle
        segs = [p for a, b in zip(loop, loop[1:]) for p in (a, b)]
        batch = batch_for_shader(line_sh, 'LINES', {"pos": segs})
        line_sh.uniform_float("viewportSize", (region.width, region.height))
        line_sh.uniform_float("lineWidth", 2.5)
        line_sh.uniform_float("color", LINE_COLORS.get(key, (1, 1, 1, 0.9)))
        batch.draw(line_sh)
    for _label, key, pts in paths:
        segs = [p for a, b in zip(pts, pts[1:]) for p in (a, b)]
        batch = batch_for_shader(line_sh, 'LINES', {"pos": segs})
        line_sh.uniform_float("viewportSize", (region.width, region.height))
        line_sh.uniform_float("lineWidth", 3.0)
        line_sh.uniform_float("color", LINE_COLORS.get(key, (1, 1, 1, 0.9)))
        batch.draw(line_sh)

    pt_sh = gpu.shader.from_builtin('POINT_UNIFORM_COLOR')
    groups = [
        ([pts[0] for _l, _k, pts in paths], ROOT_COLOR, 11.0),
        ([pts[-1] for _l, _k, pts in paths], TIP_COLOR, 9.0),
        ([p for _n, p in points], POINT_COLOR, 11.0),
    ]
    for _l, key, pts in paths:                       # chain middle points
        if len(pts) > 2:
            groups.append((pts[1:-1], LINE_COLORS.get(key, (1, 1, 1, 1)), 7.0))
    for coords, color, size in groups:
        if not coords:
            continue
        gpu.state.point_size_set(size)
        batch = batch_for_shader(pt_sh, 'POINTS', {"pos": coords})
        pt_sh.uniform_float("color", color)
        batch.draw(pt_sh)
    gpu.state.point_size_set(1.0)
    gpu.state.blend_set('NONE')


def _label(region, rv3d, pos, text, color, dx=8, dy=0):
    p = location_3d_to_region_2d(region, rv3d, pos)
    if p is None:
        return
    blf.color(0, color[0], color[1], color[2], 1.0)
    blf.position(0, p.x + dx, p.y + dy, 0)
    blf.draw(0, text)


def _draw_labels():
    context = bpy.context
    paths, points = _collect(context)
    if not paths and not points:
        return
    region, rv3d = context.region, context.region_data
    blf.size(0, 12)
    for label, key, pts in paths:
        mid = pts[len(pts) // 2] if len(pts) > 2 else (pts[0] + pts[-1]) * 0.5
        _label(region, rv3d, mid, label, LINE_COLORS.get(key, (1, 1, 1, 1)))
    blf.size(0, 10)
    for _l, _k, pts in paths:
        _label(region, rv3d, pts[0], "H", ROOT_COLOR, dx=-14)
        _label(region, rv3d, pts[-1], "T", TIP_COLOR, dx=-12)
    for mk, p in points:
        _label(region, rv3d, p, mk[3:], POINT_COLOR)


def register():
    space = bpy.types.SpaceView3D
    _handles.append(space.draw_handler_add(_draw_view, (), 'WINDOW', 'POST_VIEW'))
    _handles.append(space.draw_handler_add(_draw_labels, (), 'WINDOW', 'POST_PIXEL'))


def unregister():
    space = bpy.types.SpaceView3D
    while _handles:
        space.draw_handler_remove(_handles.pop(), 'WINDOW')
