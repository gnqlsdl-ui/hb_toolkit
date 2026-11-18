"""Brow curve, tongue chain, teeth arches, cheeks and jawlines built from
chain markers.

Each part is shaped by several ordered marker empties (see
``template.CHAINS``). Build Base calls :func:`build_parts` inside its Edit
Mode session; Add Markers can call :func:`mesh_chain_points` to place the
markers on a registered part mesh (brow / teeth / tongue).
"""

from mathutils import Vector

from ..utils.mesh_path import resample
from . import template as T


SIDES = (".L", ".R")


def chain_names(chain, side):
    """Marker names of one chain side (center markers keep their name)."""
    names = [n for n, _p in T.CHAINS[chain]["markers"]]
    return [T.flip(n) for n in names] if side == ".R" else names


def resolve_points(chain, side, get_world, default_world, mirror_x):
    """World points of a chain side.

    Marker position when the empty exists; a missing .R marker mirrors its
    .L marker across ``mirror_x``; otherwise the default spot.
    """
    out = []
    for name_l, _p in T.CHAINS[chain]["markers"]:
        name = T.flip(name_l) if side == ".R" else name_l
        w = get_world(name)
        if w is None and side == ".R":
            wl = get_world(name_l)
            if wl is not None:
                w = Vector((2.0 * mirror_x - wl.x, wl.y, wl.z))
        if w is None:
            w = default_world(T.MARKERS[name])
        out.append(Vector(w))
    return out


def catmull_rom(points, steps=24):
    """Dense polyline through ``points`` (uniform Catmull-Rom)."""
    if len(points) < 3:
        return [p.copy() for p in points]
    pts = [points[0] * 2 - points[1]] + list(points) + [points[-1] * 2 - points[-2]]
    out = []
    for i in range(1, len(pts) - 2):
        p0, p1, p2, p3 = pts[i - 1], pts[i], pts[i + 1], pts[i + 2]
        for s in range(steps):
            t = s / steps
            t2, t3 = t * t, t * t * t
            out.append(0.5 * ((2 * p1) + (-p0 + p2) * t
                              + (2 * p0 - 5 * p1 + 4 * p2 - p3) * t2
                              + (-p0 + 3 * p1 - 3 * p2 + p3) * t3))
    out.append(points[-1].copy())
    return out


# Brow (storm): 7 DEF-Brow_local bones along the brow; 1, 2, 5, 7 are the
# controls, 3 / 4 ride STR-Brow_local (2 -> 5) and 6 rides STR-Brow_local1
# (5 -> 7) through a P- bone copying the stretch bone's location.
BROW_MAIN = (1, 2, 5, 7)
BROW_FOLLOW = {3: ("STR-Brow_local", 2), 4: ("STR-Brow_local", 2), 6: ("STR-Brow_local1", 5)}
BROW_STRETCH = {"STR-Brow_local": (2, 5, 0.0), "STR-Brow_local1": (5, 7, 1.0)}   # from, to, bulge
BROW_SK_CTRLS = {"in": 0, "mid": 3, "out": 6}     # storm CTL-Brow_*: resampled brow point index
BROW_SK_LIFT = 2.0        # x control length above the brow (storm keeps them over the brow)


def brow_constraints(arm):
    """Pose constraints of the storm brow (after the edit pass)."""
    pbs = arm.pose.bones
    bones = arm.data.bones
    for s in SIDES:
        for name, (a, b, bulge) in BROW_STRETCH.items():
            n = name + s
            if n not in pbs:
                continue
            c = pbs[n].constraints.new('STRETCH_TO')
            c.name = "Stretch_Brow"
            c.target, c.subtarget = arm, f"DEF-Brow_local{b}{s}"
            c.volume, c.keep_axis = 'VOLUME_XZX', 'SWING_Y'
            c.rest_length = bones[n].length
            c.bulge = bulge
        for i, (strb, _par) in BROW_FOLLOW.items():
            n, t = f"P-Brow_local{i}{s}", strb + s
            if n not in pbs or t not in bones:
                continue
            h, a, b = bones[n].head_local, bones[t].head_local, bones[t].tail_local
            ab = b - a
            ht = max(0.0, min(1.0, (h - a).dot(ab) / ab.length_squared)) if ab.length_squared else 0.0
            c = pbs[n].constraints.new('COPY_LOCATION')
            c.name = "Follow_Brow"
            c.target, c.subtarget = arm, t
            c.head_tail = ht


def part_bone_names(brow_count):
    """Every bone name build_parts can create (for clash checks)."""
    names = ["MSTR-TONGUE"] + [f"DEF-TONGUE{i}" for i in range(1, 5)]
    for s in SIDES:
        names += [f"MSTR-BROW{s}"] + [f"DEF-Brow_local{i}{s}" for i in range(1, 8)]
        names += [f"STR-Brow_local{s}", f"STR-Brow_local1{s}"]
        names += [f"CTL-Brow_{n}{s}" for n in BROW_SK_CTRLS]
        names += [f"P-Brow_local{i}{s}" for i in BROW_FOLLOW]
    names += ["DEF-Furrow", "STR-Nose", "DEF-Nose"]
    for jaw in ("UPP", "LOW"):
        names += [f"MSTR-TEETH_{jaw}", f"DEF-TEETH_{jaw}"]
        names += [f"DEF-TEETH_{jaw}{k}{s}" for k in (1, 2) for s in SIDES]
    for s in SIDES:
        names += [f"CTL-cheek_{n}{s}" for n in ("all", "in", "mid", "out")]
        names += [f"P-jawline_all{s}"] + [f"DEF-jawline{i}{s}" for i in range(1, 4)]
    names.append("DEF-jawline_Center")
    return names


def build_parts(ebs, arm, points, scale, brow_count, set_coll):
    """Create the part bones in Edit Mode.

    ``points``: {chain: {side: [world points]}} ('' side for the tongue).
    ``set_coll(edit_bone, coll_name)`` assigns the bone collection.
    Returns the created bone names.
    """
    inv = arm.matrix_world.inverted()
    up = (inv.to_3x3() @ Vector((0.0, 0.0, 1.0))).normalized()
    made = []

    def bone(name, head, tail, parent, deform, coll, connect=False):
        b = ebs.new(name)
        b.head, b.tail, b.roll = head, tail, 0.0
        b.parent = ebs.get(parent) if parent else None
        b.use_connect = connect and b.parent is not None
        b.use_deform = deform
        set_coll(b, coll)
        made.append(name)
        return b

    a = lambda w: inv @ w          # world -> armature space

    # --- brows (storm): 7 DEF-Brow_local along the curve through the markers;
    # 1, 2, 5, 7 are controls, 3 / 4 / 6 follow stretch bones (BROW_FOLLOW) ---
    inner = {}
    for s in SIDES:
        pts = points["BROW"][s]
        curve = catmull_rom(pts)
        p = [a(w) for w in resample(curve, 7)]          # p[0] = local1 (inner)
        inner[s] = p[0]
        # storm: MSTR-BROW (storm CTL-Brow_all) and CTL-Brow_in / mid / out move no
        # bone - they drive the SK-Brow shape keys (shapekeys.py); the local
        # bones are the fine controls on top, straight under the face bone
        master = f"MSTR-BROW{s}"
        cl = up * (T.BROW_CTRL_LEN * scale)
        bone(master, p[3], p[3] + cl, T.CHAIN_PARENTS["BROW"], False, T.COLL_CTRL)
        for nm, i in BROW_SK_CTRLS.items():
            h = p[i] + cl * BROW_SK_LIFT
            bone(f"CTL-Brow_{nm}{s}", h, h + cl, T.CHAIN_PARENTS["BROW"], False, T.COLL_CTRL)
        dl = up * (T.BROW_DEF_LEN * scale)
        for i in BROW_MAIN:                               # controls, deform
            bone(f"DEF-Brow_local{i}{s}", p[i - 1], p[i - 1] + dl, T.CHAIN_PARENTS["BROW"],
                 True, T.COLL_CTRL)
        for name, (i, j, _b) in BROW_STRETCH.items():
            bone(name + s, p[i - 1], p[j - 1], f"DEF-Brow_local{i}{s}", False, T.COLL_MCH)
        for i, (strb, par) in BROW_FOLLOW.items():
            _i, j, _b = BROW_STRETCH[strb]
            sa, sb = p[_i - 1], p[j - 1]
            ab = sb - sa
            t = max(0.0, min(1.0, (p[i - 1] - sa).dot(ab) / ab.length_squared)) if ab.length_squared else 0.0
            q = sa + ab * t                      # P- sits on the stretch line (storm),
            d = ab.normalized() * (T.BROW_DEF_LEN * scale * 0.5)   # the DEF on the curve
            bone(f"P-Brow_local{i}{s}", q, q + d, f"DEF-Brow_local{par}{s}",
                 False, T.COLL_MCH)
            bone(f"DEF-Brow_local{i}{s}", p[i - 1], p[i - 1] + dl, f"P-Brow_local{i}{s}",
                 True, T.COLL_DEF)
    if all(s in inner for s in SIDES):                    # furrow: between the brows
        c = (inner[".L"] + inner[".R"]) * 0.5
        bone("DEF-Furrow", c, c + up * (T.BROW_CTRL_LEN * scale),
             T.CHAIN_PARENTS["BROW"], True, T.COLL_CTRL)

    # --- nose (storm): MSTR-NOSE > STR-Nose (aims at the upper lip, see
    # features.setup) > DEF-Nose (nose base) > tip / nostrils; bridge on STR ---
    nostrils = [ebs.get(f"DEF-Nostril{s}") for s in SIDES]
    if "MSTR-NOSE" in ebs and all(nostrils):
        base = (nostrils[0].head + nostrils[1].head) * 0.5
        top = ebs["MSTR-NOSE"].head.copy()
        bone("STR-Nose", top, base, "MSTR-NOSE", False, T.COLL_MCH)
        bone("DEF-Nose", base, base + up * (T.BROW_CTRL_LEN * scale), "STR-Nose", True, T.COLL_CTRL)
        for n in ("DEF-Nose_tip", "DEF-Nostril.L", "DEF-Nostril.R"):
            if n in ebs:
                ebs[n].parent = ebs["DEF-Nose"]
        if "DEF-Nose_bridge" in ebs:
            ebs["DEF-Nose_bridge"].parent = ebs["STR-Nose"]

    # --- tongue: 4-segment chain root -> tip (rain names: 4 = root) ---
    pts = [a(w) for w in points["TONGUE"][""]]
    back = (pts[0] - pts[1]).normalized() * (0.018 * scale)
    bone("MSTR-TONGUE", pts[0], pts[0] + back, T.CHAIN_PARENTS["TONGUE"], False, T.COLL_CTRL)
    parent = "MSTR-TONGUE"
    for k in range(4):
        name = f"DEF-TONGUE{4 - k}"
        bone(name, pts[k], pts[k + 1], parent, True, T.COLL_DEF, connect=k > 0)
        parent = name

    # --- teeth: one arch per jaw, tooth bones stand up. storm: the teeth /
    # gums deform with MSTR-Teeth only; the tooth bones pull the teeth lattice
    # (lattice.py), they don't deform the mesh ---
    for jaw, chain in (("UPP", "TEETH_UPP"), ("LOW", "TEETH_LOW")):
        L = [a(w) for w in points[chain][".L"]]
        R = [a(w) for w in points[chain][".R"]]
        master = f"MSTR-TEETH_{jaw}"
        bone(master, (L[2] + R[2]) * 0.5, L[0], T.CHAIN_PARENTS[chain], True, T.COLL_CTRL)
        height = up * (T.TOOTH_HEIGHT * scale)
        bone(f"DEF-TEETH_{jaw}", L[0], L[0] + height, master, False, T.COLL_DEF)
        for s, side_pts in ((".L", L), (".R", R)):
            for k in (1, 2):
                p = side_pts[k]
                bone(f"DEF-TEETH_{jaw}{k}{s}", p, p + height, master, False, T.COLL_DEF)

    ctrl = up * (T.BROW_CTRL_LEN * scale)
    for s in SIDES:
        # --- cheek: rain CTL-cheek_in / mid / out, cheek_all on the mid point ---
        cin, cmid, cout = (a(w) for w in points["CHEEK"][s])
        parent = T.CHAIN_PARENTS["CHEEK"]
        bone(f"CTL-cheek_all{s}", cmid, cmid + ctrl * 1.5, parent, False, T.COLL_CTRL)
        for nm, p in (("in", cin), ("mid", cmid), ("out", cout)):
            bone(f"CTL-cheek_{nm}{s}", p, p + ctrl, parent, False, T.COLL_CTRL)

        # --- jawline side: DEF-jawline1..3 under P-jawline_all (center below) ---
        jw = points["JAWLINE"][s]                       # [center, 1, 2, 3]
        holder = f"P-jawline_all{s}"
        bone(holder, a(jw[2]), a(jw[2]) + ctrl * 0.3, T.CHAIN_PARENTS["JAWLINE"],
             False, T.COLL_CTRL)                         # rain: on jawline2
        for i, w in enumerate(jw[1:], 1):
            bone(f"DEF-jawline{i}{s}", a(w), a(w) + up * (T.BROW_DEF_LEN * scale),
                 holder, True, T.COLL_DEF)
    # jawline center, shared by both sides
    c = a(points["JAWLINE"][".L"][0])
    bone("DEF-jawline_Center", c, c + up * (T.BROW_DEF_LEN * scale),
         T.CHAIN_PARENTS["JAWLINE"], True, T.COLL_DEF)
    return made


# ----------------------------------------------------------------------
# Default marker spots from a part mesh
# ----------------------------------------------------------------------
def _centroid(pts):
    return sum(pts, Vector()) / len(pts)


def _band(pts, key, center, width):
    """Points whose ``key`` is within ``width`` of ``center`` (nearest if none)."""
    sel = [p for p in pts if abs(key(p) - center) <= width]
    return sel or [min(pts, key=lambda p: abs(key(p) - center))]


def mesh_chain_points(obj, chain):
    """World spots for a chain's center / .L markers taken from a part mesh."""
    mw = obj.matrix_world
    pts = [mw @ v.co for v in obj.data.vertices]
    if len(pts) < 4:
        return None
    cx = sum(p.x for p in pts) / len(pts)

    if chain == "BROW":
        left = [p for p in pts if p.x > cx] or pts
        x0, x1 = min(p.x for p in left), max(p.x for p in left)
        w = 0.12 * (x1 - x0)
        return [_centroid(_band(left, lambda p: p.x, x0 + t * (x1 - x0), w))
                for t in (0.0, 1 / 3, 2 / 3, 1.0)]

    if chain == "TONGUE":
        y0, y1 = min(p.y for p in pts), max(p.y for p in pts)   # front, back
        w = 0.1 * (y1 - y0)
        out = []
        for t in (0.0, 0.25, 0.5, 0.75, 1.0):                    # root(back) -> tip
            c = _centroid(_band(pts, lambda p: p.y, y1 - t * (y1 - y0), w))
            out.append(Vector((cx, c.y, c.z)))
        return out

    if chain in ("TEETH_UPP", "TEETH_LOW"):
        yf, yb = min(p.y for p in pts), max(p.y for p in pts)
        d = yb - yf
        hw = max(abs(p.x - cx) for p in pts)
        left = [p for p in pts if p.x > cx] or pts

        def spot(sel):
            c = _centroid(sel)
            return Vector((c.x, c.y, min(p.z for p in sel)))   # tooth edge (lowest)

        front = [p for p in pts if abs(p.x - cx) < 0.2 * hw and p.y < yf + 0.15 * d] or pts
        side = _band(left, lambda p: p.y, yf + 0.45 * d, 0.12 * d)
        back = [p for p in left if p.y > yb - 0.15 * d] or left
        f = spot(front)
        return [Vector((cx, f.y, f.z)), spot(side), spot(back)]
    return None
