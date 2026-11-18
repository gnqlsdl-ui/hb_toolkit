"""Viewport overlay of the shape key edit meshes.

Each edit mesh gets a frame in its state color and a name plate under it:
  gray    untouched (the pose as taken, nothing sculpted)
  orange  sculpted / changed since the last register: register again
  green   registered: the face has this shape
  red     corrective still taken from the rig alone: 'take again over keys'
The active edit mesh also shows where it comes from: arrows from the base keys
a combo / corrective is laid on (cyan) and from the meshes it was mixed from
(magenta, with the weights). Block titles (BROW / CHEEK / ...) are drawn large.
Nothing is added to the scene. Toggle with "편집 메쉬 표시" in the panel.
"""

import json

import blf
import bpy
import gpu
from bpy.app.handlers import persistent
from bpy_extras.view3d_utils import location_3d_to_region_2d
from gpu_extras.batch import batch_for_shader
from mathutils import Vector

from . import operators as O
from . import shapekeys as S

GRAY = (0.6, 0.6, 0.6, 0.9)
ORANGE = (1.0, 0.6, 0.15, 1.0)
GREEN = (0.3, 0.9, 0.4, 1.0)
RED = (1.0, 0.3, 0.3, 1.0)
CYAN = (0.3, 0.85, 1.0, 1.0)
MAGENTA = (1.0, 0.4, 0.9, 1.0)
PURPLE = (0.7, 0.5, 1.0, 1.0)
PLATE = (0.05, 0.05, 0.05, 0.72)
STATES = {
    'NEW': (GRAY, "손 안 댐"),
    'NEED': (ORANGE, "등록 필요"),
    'REG': (GREEN, "등록됨"),
    'RAW': (RED, "다시 뽑기 필요"),
}

_handles = []
_cache = {}             # object name -> (registered hash, mesh name, state)
_parents = {"n": -1, "map": {}}


# ----------------------------------------------------------------------
# State
# ----------------------------------------------------------------------
def _state(pk_name, obj, corr):
    reg = obj.get(S.REG_HASH)
    c = _cache.get(obj.name)
    if c is not None and c[0] == reg and c[1] == obj.data.name:
        return c[2]
    import numpy as np
    try:
        co = S._sculpted_co(obj)
        keys = obj.data.shape_keys
        mt = keys.key_blocks.get("Morph Target") if keys else None
        base = np.empty(len(obj.data.vertices) * 3)
        (mt.data if mt is not None else obj.data.vertices).foreach_get("co", base)
        touched = float(np.abs(co - base.reshape(-1, 3)).max()) > 1e-5
        if pk_name in corr and not obj.get(S.OVER_KEYS):
            state = 'RAW'
        elif reg and reg == S.co_hash(co):
            state = 'REG'
        elif touched or reg:
            state = 'NEED'
        else:
            state = 'NEW'
    except (AttributeError, RuntimeError, ValueError):
        state = 'NEW'
    _cache[obj.name] = (reg, obj.data.name, state)
    return state


@persistent
def _on_depsgraph(_scene, depsgraph):
    if not _cache:
        return
    for u in depsgraph.updates:
        idb = u.id.original if u.id is not None else None
        if isinstance(idb, bpy.types.Object):
            if u.is_updated_geometry:
                _cache.pop(idb.name, None)
        elif isinstance(idb, (bpy.types.Key, bpy.types.Mesh)):
            me = idb.user if isinstance(idb, bpy.types.Key) else idb
            name = getattr(me, "name", None)
            for k in [k for k, v in _cache.items() if v[1] == name]:
                _cache.pop(k, None)


@persistent
def _on_load(_dummy):
    _cache.clear()
    _parents["n"] = -1


def _combo_parents(mesh):
    n = len(mesh.data.pose_keys)
    if _parents["n"] != n:
        try:
            _parents["map"] = S.combo_parents(mesh)
        except (KeyError, OSError, ValueError):
            _parents["map"] = {}
        _parents["n"] = n
    return _parents["map"]


# ----------------------------------------------------------------------
# Drawing
# ----------------------------------------------------------------------
def _rect(region, rv3d, obj):
    """(xmin, ymin, xmax, ymax) of the edit mesh's head on screen, or None
    (its bound_box can still hold the whole body: the head group is used)."""
    mw = obj.matrix_world
    lo, hi = S.head_bounds(obj)
    corners = [Vector((x, y, z)) for x in (lo.x, hi.x) for y in (lo.y, hi.y) for z in (lo.z, hi.z)]
    pts = [location_3d_to_region_2d(region, rv3d, mw @ c) for c in corners]
    if any(p is None for p in pts):
        return None
    xs, ys = [p.x for p in pts], [p.y for p in pts]
    r = (min(xs), min(ys), max(xs), max(ys))
    if r[2] < 0 or r[3] < 0 or r[0] > region.width or r[1] > region.height:
        return None
    return r


def _lines(segs, color, width):
    if not segs:
        return
    sh = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
    region = bpy.context.region
    batch = batch_for_shader(sh, 'LINES', {"pos": [(x, y, 0.0) for x, y in segs]})
    sh.uniform_float("viewportSize", (region.width, region.height))
    sh.uniform_float("lineWidth", width)
    sh.uniform_float("color", color)
    batch.draw(sh)


def _box(x0, y0, x1, y1, color):
    sh = gpu.shader.from_builtin('UNIFORM_COLOR')
    batch = batch_for_shader(sh, 'TRIS', {"pos": [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]},
                             indices=((0, 1, 2), (0, 2, 3)))
    sh.uniform_float("color", color)
    batch.draw(sh)


def _frame(r, color, width):
    x0, y0, x1, y1 = r
    _lines([(x0, y0), (x1, y0), (x1, y0), (x1, y1), (x1, y1), (x0, y1), (x0, y1), (x0, y0)],
           color, width)


def _text(x, y, text, size, color):
    blf.size(0, size)
    blf.color(0, *color[:3], 1.0)
    blf.position(0, x, y, 0)
    blf.draw(0, text)


def _plate_rect(cx, top, lines, scale):
    pad = 4 * scale
    dims = []
    for text, size, _c in lines:
        blf.size(0, size)
        dims.append(blf.dimensions(0, text))
    w = max(d[0] for d in dims) + pad * 2
    h = sum(d[1] for d in dims) + pad * (len(lines) + 1)
    return (cx - w / 2, top - h, cx + w / 2, top), dims


def _plate(cx, top, lines, scale):
    """Name plate centered at cx, its top at top: [(text, size, color)]."""
    pad = 4 * scale
    (x0, _y0, x1, y1), dims = _plate_rect(cx, top, lines, scale)
    _box(x0, _y0, x1, y1, PLATE)
    y = y1 - pad
    for (text, size, color), (tw, th) in zip(lines, dims):
        y -= th
        _text(cx - tw / 2, y, text, size, color)
        y -= pad


def _arrow(a, b, color, width, label=None, scale=1.0):
    """a -> b (screen points), stopping short of the frames' centers."""
    d = (b - a)
    if d.length < 1.0:
        return
    u = d.normalized()
    s, e = a + u * 12 * scale, b - u * 12 * scale
    side = Vector((-u.y, u.x))
    head = 10 * scale
    _lines([tuple(s), tuple(e), tuple(e), tuple(e - u * head + side * head * 0.5),
            tuple(e), tuple(e - u * head - side * head * 0.5)], color, width)
    if label:
        m = (s + e) * 0.5
        _text(m.x + 4, m.y + 4, label, int(11 * scale), color)


def _draw():
    context = bpy.context
    props = getattr(context.scene, "hb_facial", None)
    if props is None or not props.show_sk_overlay:
        return
    mesh = props.mesh
    if mesh is None or not S.pose_keys_available(mesh):
        return
    region, rv3d = context.region, context.region_data
    if region is None or rv3d is None:
        return
    scale = context.preferences.system.ui_scale
    try:
        corr = set(S.corrective_names(mesh))
    except (AttributeError, KeyError):
        corr = set()
    combos = set()
    parents = _combo_parents(mesh)
    active = context.view_layer.objects.active
    rects, by_obj = {}, {}
    gpu.state.blend_set('ALPHA')
    plates = []
    for pk in mesh.data.pose_keys:
        o = pk.storage_object
        if o is None or o.get(O.OBJ_PROP) != S.STORAGE_TAG or not o.visible_get():
            continue
        r = _rect(region, rv3d, o)
        if r is None:
            continue
        rects[pk.name] = r
        by_obj[o.name] = pk.name
        state = _state(pk.name, o, corr)
        color, word = STATES[state]
        sel = o.select_get()
        _frame(r, color, 3.0 if sel else 1.5)
        if o == active:
            pad = 3 * scale
            _frame((r[0] - pad, r[1] - pad, r[2] + pad, r[3] + pad), (1, 1, 1, 0.9), 1.5)
        if r[2] - r[0] < 34 * scale:
            continue
        sub = [word]
        if o.get(S.OVER_KEYS):
            sub.append("키 위")
        if o.get(S.MIX_PROP):
            sub.append("Mix")
        if pk.name in parents:
            combos.add(pk.name)
            sub.append(" × ".join(S.pretty_name(p).split(" ", 1)[-1] for p in parents[pk.name]))
        big = r[2] - r[0] > 90 * scale
        plates.append(((r[0] + r[2]) / 2, r[1] - 2 * scale,
                       [(S.pretty_name(pk.name), int((13 if big else 10) * scale), (1, 1, 1, 1)),
                        ("  ·  ".join(sub), int((10 if big else 8) * scale), color)]))
    # brow work meshes: purple, with their key values; active -> the edit meshes they feed
    for o in S.work_objects():
        if not o.visible_get():
            continue
        r = _rect(region, rv3d, o)
        if r is None:
            continue
        _frame(r, PURPLE, 3.0 if o.select_get() else 2.0)
        if o == active:
            pad = 3 * scale
            _frame((r[0] - pad, r[1] - pad, r[2] + pad, r[3] + pad), (1, 1, 1, 0.9), 1.5)
            c = Vector(((r[0] + r[2]) / 2, (r[1] + r[3]) / 2))
            for pname, _k in S.WORK_KEYS:
                if pname in rects:
                    q = rects[pname]
                    _arrow(c, Vector(((q[0] + q[2]) / 2, (q[1] + q[3]) / 2)), PURPLE, 2.5, scale=scale)
        if r[2] - r[0] < 34 * scale:
            continue
        keys = o.data.shape_keys
        vals = []
        for pname, kname in S.WORK_KEYS:
            kb = keys.key_blocks.get(kname) if keys else None
            if kb is not None:
                vals.append(f"{S.pretty_name(pname).split(' ', 1)[-1]} {kb.value:.2f}"
                            + (" (끔)" if kb.mute else ""))
        big = r[2] - r[0] > 90 * scale
        plates.append(((r[0] + r[2]) / 2, r[1] - 2 * scale,
                       [("눈썹 y−·x− 작업 (대칭)", int((13 if big else 10) * scale), (1, 1, 1, 1)),
                        ("  ·  ".join(vals), int((10 if big else 8) * scale), PURPLE)]))
    # where the active one comes from
    act = by_obj.get(active.name) if active is not None else None
    if act in rects:
        r = rects[act]
        c = Vector(((r[0] + r[2]) / 2, (r[1] + r[3]) / 2))
        for p in parents.get(act, ()):
            if p in rects:
                q = rects[p]
                _arrow(Vector(((q[0] + q[2]) / 2, (q[1] + q[3]) / 2)), c, CYAN, 2.5, scale=scale)
        for child, ps in parents.items():           # what it is a base of (dim)
            if act in ps and child in rects:
                q = rects[child]
                _arrow(c, Vector(((q[0] + q[2]) / 2, (q[1] + q[3]) / 2)),
                       (CYAN[0], CYAN[1], CYAN[2], 0.35), 1.5, scale=scale)
        try:
            recipe = json.loads(active.get(S.MIX_PROP, "[]"))
        except (TypeError, ValueError):
            recipe = []
        for s, w in recipe:
            if s in rects:
                q = rects[s]
                _arrow(Vector(((q[0] + q[2]) / 2, (q[1] + q[3]) / 2)), c, MAGENTA, 2.5,
                       label=f"×{w:g}", scale=scale)
    taken = []
    for cx, top, lines in plates:
        _plate(cx, top, lines, scale)
        taken.append(_plate_rect(cx, top, lines, scale)[0])
    _titles(context, mesh, rects, taken, scale)
    gpu.state.blend_set('NONE')


def _block_of(name):
    if S.is_brow_corr(name):
        return "BROW CORR"
    c = S._cell(name)
    return {"brow": "BROW", "cheek": "CHEEK", "mouth": "MOUTH"}.get(c[0]) if c else "CORRECTIVES"


def _screen_rect(region, rv3d, obj):
    pts = [location_3d_to_region_2d(region, rv3d, obj.matrix_world @ Vector(c)) for c in obj.bound_box]
    if any(p is None for p in pts):
        return None
    return (min(p.x for p in pts), min(p.y for p in pts), max(p.x for p in pts), max(p.y for p in pts))


def _hit(a, b):
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _titles(context, mesh, rects, taken, scale):
    """Block titles (BROW, CHEEK, ...) beside their block where nothing is
    drawn: above it first (going up), then left of it, then below it."""
    region, rv3d = context.region, context.region_data
    blocks = {}
    for name, r in rects.items():
        b = _block_of(name)
        if b:
            blocks.setdefault(b, []).append(r)
    for o in S.work_objects():
        r = _rect(region, rv3d, o) if o.visible_get() else None
        if r is not None:
            blocks.setdefault("BROW CORR", []).append(r)
            taken.append(r)
    obstacles = list(rects.values()) + taken
    for o in context.visible_objects:                  # other meshes (bake meshes, ...)
        if o.type == 'MESH' and o.get(O.OBJ_PROP) not in (S.STORAGE_TAG, S.WORK_TAG) \
                and o != mesh:
            r = _screen_rect(region, rv3d, o)
            if r is not None:
                obstacles.append(r)
    size = int(20 * scale)
    pad, gap = 6 * scale, 6 * scale
    for title, rs in blocks.items():
        bx0, by0 = min(r[0] for r in rs), min(r[1] for r in rs)
        bx1, by1 = max(r[2] for r in rs), max(r[3] for r in rs)
        blf.size(0, size)
        w, h = blf.dimensions(0, title)
        bw, bh = w + pad * 2, h + pad * 2
        tries = [(bx0, by1 + gap + k * bh) for k in range(6)]                 # above
        tries += [(bx0 - gap - bw, by1 - bh - k * bh) for k in range(4)]      # left
        tries += [(bx0, by0 - gap - bh - k * bh) for k in range(8)]           # below
        x, y = tries[0]
        for tx, ty in tries:
            box = (tx, ty, tx + bw, ty + bh)
            if not any(_hit(box, r) for r in obstacles):
                x, y = tx, ty
                break
        _box(x, y, x + bw, y + bh, PLATE)
        _text(x + pad, y + pad, title, size, (1, 1, 1, 1))
        obstacles.append((x, y, x + bw, y + bh))


def _draw_safe():
    try:
        _draw()
    except ReferenceError:
        pass


def register():
    _handles.append(bpy.types.SpaceView3D.draw_handler_add(_draw_safe, (), 'WINDOW', 'POST_PIXEL'))
    bpy.app.handlers.depsgraph_update_post.append(_on_depsgraph)
    bpy.app.handlers.load_post.append(_on_load)


def unregister():
    while _handles:
        bpy.types.SpaceView3D.draw_handler_remove(_handles.pop(), 'WINDOW')
    for lst, fn in ((bpy.app.handlers.depsgraph_update_post, _on_depsgraph),
                    (bpy.app.handlers.load_post, _on_load)):
        if fn in lst:
            lst.remove(fn)
    _cache.clear()
