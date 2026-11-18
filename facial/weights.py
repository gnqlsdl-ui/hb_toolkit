"""Face weights: storm's facial skin weights carried onto this character.

data/storm_face_weights.json holds storm's head mesh (positions + polygons),
its facial deform weights and landmarks (eyelid / lip lines, brows, nose,
chin, jawline, ears, eyesocket). Build:

1. The same landmarks are measured on this character (lines from the
   registered edges, bone heads), both snapped to their skin.
2. A thin-plate spline maps storm's landmarks onto ours; storm's head mesh is
   bent with it so it lies on this face.
3. Every vertex of the face mesh takes the weights of the closest point on
   that bent mesh (interpolated inside its polygon).
4. Storm groups go to our bones by role: chains (lips, eyelids, brows) by
   their position along the line (split between the two nearest DEF), the
   rest by name. The remaining weight of each touched vertex goes to the
   vertex's other (body) groups, or to the Head bone.

Re-running replaces the weights it wrote (groups listed in the mesh's
``hb_face_groups``).
"""

import json
import os

import bpy
from mathutils import Vector
from mathutils.bvhtree import BVHTree
from mathutils.interpolate import poly_3d_calc

from . import template as T
from . import operators as O

GROUPS_KEY = "hb_face_groups"
MAX_DIST = 0.12          # x eye distance: farther vertices are not face skin
MIN_FACING = 0.3         # min dot of our vertex normal and storm's surface normal
_DATA = None


DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
TEMPLATE_FILE = "face_weights.json"          # saved from a cleaned rig (Save Weights)
STORM_FILE = "storm_face_weights.json"       # storm original (data/extract_storm.py)


def template_path():
    return os.path.join(DATA_DIR, TEMPLATE_FILE)


def data():
    """The weight template: face_weights.json when saved, else storm's."""
    global _DATA
    if _DATA is None:
        path = template_path()
        if not os.path.exists(path):
            path = os.path.join(DATA_DIR, STORM_FILE)
        with open(path, encoding="utf-8") as f:
            _DATA = json.load(f)
    return _DATA


def reload_data():
    global _DATA
    _DATA = None
    return data()


def _R():
    from . import rig
    return rig


# ----------------------------------------------------------------------
# Our side: lines, chains, landmarks
# ----------------------------------------------------------------------
def _lines(arm, props):
    out = {}
    mesh = props.mesh
    for slot in O.SETS:
        if slot in O.edge_map(mesh):
            try:
                _i, upp, low = O.slot_lines(arm, mesh, slot, props)
            except (ValueError, KeyError):
                continue
            out[slot] = (upp, low)
    return out


def _chains(arm, props, lines):
    """{(chain, side): [(u, DEF bone)]} on this rig, u sorted 0..1."""
    R = _R()
    bones = arm.data.bones
    ch = {}
    if 'LIPS' in lines:
        for which, line in zip(("upp", "low"), lines['LIPS']):
            names = [n for n, _p in line]
            par = R._params([p for _n, p in line])
            uc = par[names.index(f"Lips_{which}")]
            for nm, u in zip(names, par):
                b = "DEF-" + nm
                if b not in bones:
                    continue
                if u <= uc + 1e-6:
                    ch.setdefault((f"lip_{which}", ".L"), []).append((1 - u / uc if uc else 0.0, b))
                if u >= uc - 1e-6:
                    ch.setdefault((f"lip_{which}", ".R"), []).append(
                        ((u - uc) / (1 - uc) if uc < 1 else 0.0, b))
    for side in (".L", ".R"):
        slot = "EYE" + side.replace(".", "_")
        if slot in lines:
            for which, line in zip(("upp", "low"), lines[slot]):
                par = R._params([p for _n, p in line])
                ch[(f"lid_{which}", side)] = [(u, "DEF-" + nm) for (nm, _p), u in zip(line, par)
                                              if "DEF-" + nm in bones]
        brows = sorted((b.name for b in bones if b.name.startswith("DEF-Brow_") and b.name.endswith(side)),
                       key=lambda n: abs(bones[n].head_local.x))
        if brows:
            ch[("brow", side)] = [(i / max(1, len(brows) - 1), n) for i, n in enumerate(brows)]
    return {k: sorted(v) for k, v in ch.items() if v}


def _split(chain, u):
    """{bone: share} for parameter u on [(u, bone)]."""
    if u <= chain[0][0]:
        return {chain[0][1]: 1.0}
    for (ua, a), (ub, b) in zip(chain, chain[1:]):
        if u <= ub:
            t = 0.0 if ub <= ua else (u - ua) / (ub - ua)
            out = {a: 1 - t}
            out[b] = out.get(b, 0.0) + t
            return out
    return {chain[-1][1]: 1.0}


def _our_landmarks(arm, props, lines):
    R = _R()
    mesh = props.mesh
    mw = arm.matrix_world
    bones = arm.data.bones
    lm = {}

    def put(key, p):
        if p is not None:
            lm[key] = O.snap_to_surface(mesh, p)

    def head(n):
        return mw @ bones[n].head_local if n in bones else None

    us = [i / 8 for i in range(9)]
    for side in (".L", ".R"):
        slot = "EYE" + side.replace(".", "_")
        if slot in lines:
            for which, line in zip(("upp", "low"), lines[slot]):
                pts = [p for _n, p in line]
                par = R._params(pts)
                for u in us:
                    put(f"lid_{which}{side}@{u:.3f}", R._at(pts, par, u))
    if 'LIPS' in lines:
        for which, line in zip(("upp", "low"), lines['LIPS']):
            names = [n for n, _p in line]
            pts = [p for _n, p in line]
            par = R._params(pts)
            uc = par[names.index(f"Lips_{which}")]
            for s in (0.0, 0.25, 0.5, 0.75, 1.0):
                put(f"lip_{which}.L@{s:.3f}", R._at(pts, par, uc * (1 - s)))
                if s > 0:
                    put(f"lip_{which}.R@{s:.3f}", R._at(pts, par, uc + (1 - uc) * s))
    for side in (".L", ".R"):
        brows = sorted((b.name for b in bones if b.name.startswith("DEF-Brow_") and b.name.endswith(side)),
                       key=lambda n: abs(bones[n].head_local.x))
        if brows:
            put(f"brow{side}@0", head(brows[0]))
            put(f"brow{side}@1", head(brows[-1]))
            put(f"brow{side}@m", head(brows[len(brows) // 2]))
        for n in ("DEF-jawline1", "DEF-jawline2", "DEF-jawline3", "DEF-Nostril"):
            put(n + side, head(n + side))
        put("ear_base" + side, head(f"DEF-Ear_base{side}"))
        tip = bones.get(f"DEF-Ear_tip{side}")
        put("ear_tip" + side, mw @ tip.tail_local if tip else None)
        for b in bones:
            if b.name.startswith("DEF-Eyesocket_") and b.name.endswith(side):
                put(b.name, head(b.name))
    put("nose_tip", head("DEF-Nose_tip"))
    put("nose_bridge", head("DEF-Nose_bridge"))
    put("chin", head("DEF_SM-CHIN"))
    return lm


# ----------------------------------------------------------------------
# Thin-plate spline (3D, kernel r)
# ----------------------------------------------------------------------
def _tps(src, dst, reg=1e-8):
    import numpy as np
    S = np.asarray(src, float)
    D = np.asarray(dst, float)
    n = len(S)
    K = np.linalg.norm(S[:, None, :] - S[None, :, :], axis=2)
    K += np.eye(n) * reg
    P = np.hstack([np.ones((n, 1)), S])
    A = np.zeros((n + 4, n + 4))
    A[:n, :n], A[:n, n:], A[n:, :n] = K, P, P.T
    b = np.zeros((n + 4, 3))
    b[:n] = D
    sol = np.linalg.solve(A, b)
    W, Aff = sol[:n], sol[n:]

    def apply(V):
        V = np.asarray(V, float)
        U = np.linalg.norm(V[:, None, :] - S[None, :, :], axis=2)
        return U @ W + np.hstack([np.ones((len(V), 1)), V]) @ Aff
    return apply


# ----------------------------------------------------------------------
# Clean-up: fill, smooth, limit
# ----------------------------------------------------------------------
SMOOTH_ITERS = 3        # topology smoothing passes (0.5 blend with the ring)
MAX_INFLUENCES = 4       # face bones per vertex (the Head takes the rest)
MIN_WEIGHT = 0.01


def _neighbors(mesh):
    nb = [[] for _ in mesh.data.vertices]
    for e in mesh.data.edges:
        a, b = e.vertices
        nb[a].append(b)
        nb[b].append(a)
    return nb


def _avg(dicts):
    out = {}
    for d in dicts:
        for b, w in d.items():
            out[b] = out.get(b, 0.0) + w
    n = len(dicts) or 1
    return {b: w / n for b, w in out.items()}


def _loop_pins(arm, mesh, lines):
    """{vertex: {DEF: w}} for the registered lip / eyelid loop vertices: each
    rides the DEF bones of the line point(s) it sits on (weight 1 in total)."""
    bones = arm.data.bones
    mw = mesh.matrix_world
    me = mesh.data
    pins, slots = {}, {}
    for slot, edges in O.edge_map(mesh).items():
        if slot not in lines:
            continue
        segs = []
        for line in lines[slot]:
            for (na, pa), (nb_, pb) in zip(line, line[1:]):
                segs.append((na, pa, nb_, pb))
        vids = {v for ei in edges if ei < len(me.edges) for v in me.edges[ei].vertices}
        for vi in vids:
            co = mw @ me.vertices[vi].co
            best = None
            for na, pa, nb_, pb in segs:
                ab = pb - pa
                t = 0.0 if ab.length_squared == 0 else max(0.0, min(1.0, (co - pa).dot(ab) / ab.length_squared))
                dd = (pa + ab * t - co).length
                if best is None or dd < best[0]:
                    best = (dd, na, nb_, t)
            _d, na, nb_, t = best
            out = {}
            for nm, x in (("DEF-" + na, 1 - t), ("DEF-" + nb_, t)):
                if nm in bones and x > 1e-4:
                    out[nm] = out.get(nm, 0.0) + x
            if out:
                s = sum(out.values())
                pins[vi] = {b: x / s for b, x in out.items()}
                slots.setdefault(slot, set()).add(vi)
    return pins, slots


INNER_DECAY = {"LIPS": 0.85, "EYE": 0.6}   # per edge ring going into the mouth / eye hole


def _inner(mesh, nb, known, pins, slots, jaw, trust=False):
    """Weights for the inside of the mouth / eye holes that could not sample
    the template (the pocket behind a lip / eyelid loop). They take the
    loop's weights fading ring by ring (to the Head); inside the mouth the
    part below the lip corners goes to the jaw instead."""
    me = mesh.data
    mw = mesh.matrix_world
    out = {}
    for slot, loop in slots.items():
        decay = INNER_DECAY["LIPS" if slot == 'LIPS' else "EYE"]
        zc = span = None
        if slot == 'LIPS' and jaw:
            zs = [(mw @ me.vertices[i].co).z for i in loop]
            zc = (max(zs) + min(zs)) / 2
            xs = [(mw @ me.vertices[i].co).x for i in loop]
            span = max(1e-4, (max(xs) - min(xs)) * 0.25)
        # the loop cuts the skin from the inside: walk in from its neighbours
        # that go backwards (into the head), never crossing the loop again
        cos = {i: mw @ me.vertices[i].co for i in loop}
        region, front = set(), set()
        for i in loop:
            for j in nb[i]:
                if j not in loop and (mw @ me.vertices[j].co - cos[i]).y > 0:
                    front.add(j)
        region |= front
        while front:
            nxt = set()
            for i in front:
                for j in nb[i]:
                    if j not in region and j not in loop:
                        nxt.add(j)
            region |= nxt
            front = nxt
            if len(region) > len(me.vertices) * 0.3:      # not a closed pocket
                region = set()
                break
        levels, front, seen = [], set(loop), set(loop)
        while front:
            nxt = {j for i in front for j in nb[i] if j in region and j not in seen}
            seen |= nxt
            if nxt:
                levels.append(nxt)
            front = nxt
        def val(k):
            return out[k] if k in out else (pins[k] if k in pins else known[k])

        if not trust:          # storm: its mouth / eye pockets don't match ours
            known_in = {k: v for k, v in known.items() if k not in region}
        else:
            known_in = known
        known = known_in

        for lvl in levels:
            for j in lvl:
                if trust and j in known:       # sampled from a saved template: keep it
                    continue
                src = [k for k in nb[j] if (k in out or k in pins or k in known) and k not in lvl]
                base = _avg([val(k) for k in src])
                d = {b: x * decay for b, x in base.items()}
                if zc is not None:
                    z = (mw @ me.vertices[j].co).z
                    low = min(1.0, max(0.0, (zc - z) / span + 0.5))
                    low = low * low * (3 - 2 * low)
                    lost = max(0.0, 1.0 - sum(d.values()))
                    d[jaw] = d.get(jaw, 0.0) + lost * low
                out[j] = d
    return out


def _clean(mesh, known, unknown, pins=None, slots=None, jaw=None, iters=SMOOTH_ITERS):
    """Fill the vertices that could not sample (from their neighbours), smooth
    over the mesh topology (so lids / lips stay apart across the openings),
    keep the strongest MAX_INFLUENCES and cap the face total at 1. ``pins``
    (lip / eyelid loop vertices) keep their weights through the smoothing;
    the mouth / eye-hole insides come from :func:`_inner`."""
    pins = pins or {}
    nb = _neighbors(mesh)
    w = dict(known)
    w.update(pins)
    inner = _inner(mesh, nb, known, pins, slots or {}, jaw, trust=(iters == 0))
    w.update(inner)
    unknown = set(unknown) - set(inner) - set(pins)
    todo = set(unknown)
    while todo:                                   # flood fill from sampled rings
        filled = {}
        for i in todo:
            ring = [w[j] for j in nb[i] if j in w]
            if ring:
                filled[i] = _avg(ring)
        if not filled:
            break
        w.update(filled)
        todo -= set(filled)
    region = set(w)
    for _ in range(iters):
        nxt = {}
        for i in region:
            # neighbours outside the region count as zero face weight (soft border)
            ring = [w.get(j, {}) for j in nb[i]]
            if not ring:
                nxt[i] = w[i]
                continue
            r = _avg(ring)
            keys = set(w[i]) | set(r)
            nxt[i] = {b: 0.5 * w[i].get(b, 0.0) + 0.5 * r.get(b, 0.0) for b in keys}
        nxt.update(pins)
        w = nxt
    out = {}
    for i, d in w.items():
        top = sorted(((b, x) for b, x in d.items() if x >= MIN_WEIGHT), key=lambda t: -t[1])
        top = top[:MAX_INFLUENCES]
        tot = sum(x for _b, x in top)
        if tot > 1.0:
            top = [(b, x / tot) for b, x in top]
        if top:
            out[i] = dict(top)
    return out


# ----------------------------------------------------------------------
# Transfer
# ----------------------------------------------------------------------
def transfer(context, arm, props):
    """Paint storm-like facial weights on props.mesh. Returns (count, notes)."""
    mesh = props.mesh
    if mesh is None or mesh.type != 'MESH':
        return 0, ["얼굴 메쉬 없음"]
    d = data()
    notes = []
    lines = _lines(arm, props)
    ours = _our_landmarks(arm, props, lines)
    keys = [k for k in d["landmarks"] if k in ours]
    if len(keys) < 12:
        return 0, [f"일치하는 랜드마크가 {len(keys)}개뿐 (먼저 엣지를 등록하고 리그를 생성하세요)"]
    warp = _tps([d["landmarks"][k] for k in keys], [tuple(ours[k]) for k in keys])
    verts = [Vector(v) for v in warp(d["verts"])]
    bvh = BVHTree.FromPolygons(verts, d["polys"])
    polys = d["polys"]

    # storm group -> {our bone: share}
    chains = _chains(arm, props, lines)
    head_bone = props.head_bone if props.head_bone in arm.data.bones else None
    gmap, missing = [], set()
    for g in d["groups"]:
        role = d["roles"][g]
        if "chain" in role:
            ch = chains.get((role["chain"], role["side"] or ".L"))
            out = _split(ch, role["u"]) if ch else {}
        else:
            out = {}
            for b, w in role["bones"].items():
                b = head_bone if b == "@HEAD" else b
                if b and b in arm.data.bones:
                    out[b] = out.get(b, 0.0) + w
        if not out:
            missing.add(g)
        gmap.append(out)
    if missing:
        notes.append(f"이 리그에 본이 없는 storm 그룹 {len(missing)}개 (예: {sorted(missing)[0]})")

    # wipe what we wrote last time; those vertices go back to the Head bone
    old = [n for n in mesh.get(GROUPS_KEY, []) if n != head_bone and n in mesh.vertex_groups]
    old_idx = {mesh.vertex_groups[n].index for n in old}
    touched = [v.index for v in mesh.data.vertices if any(g.group in old_idx for g in v.groups)]
    for name in old:
        mesh.vertex_groups.remove(mesh.vertex_groups[name])
    hg = mesh.vertex_groups.get(head_bone) if head_bone else None
    if hg is not None and touched:
        bone_idx = {g.index for g in mesh.vertex_groups
                    if g.name in arm.data.bones and arm.data.bones[g.name].use_deform and g != hg}
        lone = [i for i in touched if not any(g.group in bone_idx for g in mesh.data.vertices[i].groups)]
        hg.add(lone, 1.0, 'REPLACE')

    k = _R()._eye_dist(arm, 1.0)
    max_d = MAX_DIST * k
    lo = Vector([min(v[i] for v in verts) for i in range(3)]) - Vector((max_d,) * 3)
    hi = Vector([max(v[i] for v in verts) for i in range(3)]) + Vector((max_d,) * 3)
    mw = mesh.matrix_world
    nrm = mw.to_3x3().inverted().transposed()
    sw = d["weights"]
    known = {}             # vertex index -> {bone: w} sampled from the template
    unknown = set()        # near the face but facing away (eye holes, mouth inside)
    for v in mesh.data.vertices:
        co = mw @ v.co
        if any(co[i] < lo[i] or co[i] > hi[i] for i in range(3)):
            continue
        loc, pn, fi, dist = bvh.find_nearest(co, max_d)
        if loc is None:
            continue
        # facing the same way: keeps the inside of the eye holes / mouth
        # from picking up a surface on the other side (e.g. storm's mouth)
        if (nrm @ v.normal).normalized().dot(pn) < MIN_FACING:
            unknown.add(v.index)
            continue
        poly = polys[fi]
        bary = poly_3d_calc([verts[i] for i in poly], loc)
        acc = {}
        for vi, bw in zip(poly, bary):
            if bw <= 0:
                continue
            for gi, w in sw[vi]:
                for bone, share in gmap[gi].items():
                    acc[bone] = acc.get(bone, 0.0) + bw * w * share
        known[v.index] = {b: w for b, w in acc.items() if w > 0.002 and b != head_bone}
    pins, slots = _loop_pins(arm, mesh, lines)
    result = _clean(mesh, known, unknown, pins, slots,
                    T.JAW_BONE if T.JAW_BONE in arm.data.bones else None,
                    d.get("smooth", SMOOTH_ITERS))

    written = set()
    face_bones = {b for m in gmap for b in m if b != head_bone}
    groups = {}

    def group(name):
        if name not in groups:
            groups[name] = mesh.vertex_groups.get(name) or mesh.vertex_groups.new(name=name)
        return groups[name]

    vgi = {g.index: g for g in mesh.vertex_groups}
    deform = {b.name for b in arm.data.bones if b.use_deform}   # (DSP groups are not skin)
    for vi, face in result.items():
        f = min(sum(face.values()), 1.0)
        for b, w in face.items():
            group(b).add([vi], w, 'REPLACE')
            written.add(b)
        rest = 1.0 - f
        other = [(vgi[g.group], g.weight) for g in mesh.data.vertices[vi].groups
                 if g.group in vgi and vgi[g.group].name in deform
                 and vgi[g.group].name not in face_bones
                 and vgi[g.group].name != head_bone and g.weight > 0]
        tot = sum(w for _g, w in other)
        if tot > 0:
            for g, w in other:
                g.add([vi], rest * w / tot, 'REPLACE')
        elif head_bone:
            group(head_bone).add([vi], rest, 'REPLACE')
            written.add(head_bone)
    mesh[GROUPS_KEY] = sorted(written)

    # the mesh must ride the rig
    mods = [m for m in mesh.modifiers if m.type == 'ARMATURE']
    if not mods:
        m = mesh.modifiers.new("Armature", 'ARMATURE')
        m.object = arm
        notes.append("Armature 모디파이어 추가")
    elif mods[0].object is None:
        mods[0].object = arm
        notes.append("Armature 모디파이어가 이제 리그를 사용")
    return len(result), notes


def spread_to_edge_bones(arm, props, prefix=None):
    """Hand-paint helper: the weight a vertex has in the UNLOCKED lip / eyelid
    DEF groups (e.g. a whole lid painted into one group) is shared out again
    between the DEF bones along the registered loop, by where the vertex sits
    along it (closest loop segment, split between its two end bones). Locked
    groups and the per-vertex total are kept. Returns vertices changed.

    With ``prefix`` (e.g. "DEF-Lid_") every loop bone with that prefix takes
    part, locked or not, and missing groups are created."""
    mesh = props.mesh
    me = mesh.data
    mw = mesh.matrix_world
    bones = arm.data.bones
    lines = _lines(arm, props)
    if prefix:
        unlocked = {}
        for slot, ul in lines.items():
            for line in ul:
                for nm, _p in line:
                    b = "DEF-" + nm
                    if b.startswith(prefix) and b in bones and b not in unlocked:
                        unlocked[b] = mesh.vertex_groups.get(b) or mesh.vertex_groups.new(name=b)
    else:
        unlocked = {g.name: g for g in mesh.vertex_groups
                    if not g.lock_weight and g.name in bones and bones[g.name].use_deform}
    segs = []
    for slot, ul in lines.items():
        for which, line in zip(("upp", "low"), ul):
            for (na, pa), (nb_, pb) in zip(line, line[1:]):
                a, b = "DEF-" + na, "DEF-" + nb_
                if a in unlocked or b in unlocked:
                    segs.append((a, pa, b, pb, which))
    edge_bones = {s[0] for s in segs} | {s[2] for s in segs}
    targets = {n for n in edge_bones if n in unlocked}
    if not targets:
        return 0
    idx = {unlocked[n].index: n for n in targets}

    def line_of(name):
        """'upp' / 'low' when the painted group belongs to one line (its
        weight then stays on that line), None for corners."""
        base = name[len("DEF-"):]
        if "_upp" in base:
            return "upp"
        if "_low" in base:
            return "low"
        return None

    def nearest(co, which):
        best = None
        for a, pa, b, pb, w in segs:
            if which is not None and w != which:
                continue
            ab = pb - pa
            t = 0.0 if ab.length_squared == 0 else max(0.0, min(1.0, (co - pa).dot(ab) / ab.length_squared))
            dd = (pa + ab * t - co).length
            if best is None or dd < best[0]:
                best = (dd, a, b, t)
        return best

    changed = 0
    for v in me.vertices:
        parts = {}
        for x in v.groups:
            if x.group in idx and x.weight > 0:
                k = line_of(idx[x.group])
                parts[k] = parts.get(k, 0.0) + x.weight
        if sum(parts.values()) <= 1e-5:
            continue
        co = mw @ v.co
        share = {}
        for which, total in parts.items():
            best = nearest(co, which) or nearest(co, None)
            _d, a, b, t = best
            sub = {n: x for n, x in ((a, 1 - t), (b, t)) if n in targets and x > 1e-4}
            s = sum(sub.values())
            for n, x in sub.items():
                share[n] = share.get(n, 0.0) + total * x / s
        if not share:
            continue
        for gi in [x.group for x in v.groups if x.group in idx]:
            unlocked[idx[gi]].remove([v.index])
        for n, x in share.items():
            unlocked[n].add([v.index], min(x, 1.0), 'REPLACE')
        changed += 1
    return changed


def spread_along_edges(arm, props, prefix, blend=False):
    """Share the weight in the ``prefix`` groups (e.g. "DEF-Lips_") out over
    the loop bones by following the mesh edges: every painted vertex walks
    along the edges (inside the painted area) to the registered loop; the
    loop vertex it reaches first gives the bone, so each edge column off the
    loop rides its own bone (``blend``: mixed with the next loop vertex by
    path length instead). Upper / lower stay apart: a vertex whose weight sits on '_upp'
    bones only walks to the upper half of the loop (corners are shared).
    Per-vertex totals and other groups are kept. Returns vertices changed."""
    import heapq
    mesh = props.mesh
    me = mesh.data
    mw = mesh.matrix_world
    bones = arm.data.bones
    lines = _lines(arm, props)
    pins, slots = _loop_pins(arm, mesh, lines)
    groups = {g.index: g for g in mesh.vertex_groups if g.name.startswith(prefix)}
    loop = {vi: w for vi, w in pins.items()
            if all(b.startswith(prefix) for b in w)}
    if not loop:
        return 0
    co = [mw @ v.co for v in me.vertices]
    nb = _neighbors(mesh)

    def side(ws):
        """'upp' / 'low' when (nearly) all of the weight is on that line's
        bones, None for corners / mixed (walks both)."""
        up = sum(w for n, w in ws.items() if "_upp" in n[len("DEF-"):])
        lo = sum(w for n, w in ws.items() if "_low" in n[len("DEF-"):])
        if up + lo <= 0:
            return None
        if lo <= 0.05 * (up + lo):
            return "upp"
        if up <= 0.05 * (up + lo):
            return "low"
        return None

    total, vside = {}, {}
    for v in me.vertices:
        ws = {groups[x.group].name: x.weight for x in v.groups if x.group in groups and x.weight > 0}
        if ws and v.index not in loop:
            total[v.index] = sum(ws.values())
            vside[v.index] = side(ws)
    lside = {vi: side(w) for vi, w in loop.items()}
    # (loop pins are {bone: share}: side() reads them the same way)
    loop_nb = {vi: [j for j in nb[vi] if j in loop] for vi in loop}

    def walk(which):
        """Dijkstra from the loop vertices of ``which`` through the painted
        vertices of that side: {vertex: [(dist, seed), ...]} two nearest seeds."""
        seeds = [vi for vi, s in lside.items() if s in (which, None)]
        allowed = {vi for vi, s in vside.items() if s in (which, None)}
        best = {}
        heap = [(0.0, s, s) for s in seeds]
        heapq.heapify(heap)
        while heap:
            d, vi, s = heapq.heappop(heap)
            found = best.setdefault(vi, [])
            if any(s == fs for _d, fs in found) or len(found) >= 2:
                continue
            found.append((d, s))
            for j in nb[vi]:
                if j in allowed:
                    heapq.heappush(heap, (d + (co[j] - co[vi]).length, j, s))
        return best

    paths = {"upp": walk("upp"), "low": walk("low")}
    changed = 0
    for vi, t in total.items():
        cands = []
        for which in (("upp", "low") if vside[vi] is None else (vside[vi],)):
            cands += paths[which].get(vi, [])
        if not cands:
            continue
        cands.sort()
        d1, s1 = cands[0]
        # second seed: a loop neighbour of the first (keeps the blend along the line)
        d2, s2 = next(((d, s) for d, s in cands[1:] if s in loop_nb[s1]), (None, None))
        share = {}
        if not blend or s2 is None or d1 + d2 <= 0:      # one bone per edge column
            parts = [(s1, 1.0)]
        else:
            parts = [(s1, d2 / (d1 + d2)), (s2, d1 / (d1 + d2))]
        for s, f in parts:
            for b, w in loop[s].items():
                share[b] = share.get(b, 0.0) + f * w
        for gi in [x.group for x in me.vertices[vi].groups if x.group in groups]:
            groups[gi].remove([vi])
        for b, w in share.items():
            if b in bones and w * t > 1e-5:
                g = mesh.vertex_groups.get(b) or mesh.vertex_groups.new(name=b)
                g.add([vi], min(w * t, 1.0), 'REPLACE')
        changed += 1
    for vi, w in loop.items():                    # loop vertices: their own bone(s)
        t = sum(x.weight for x in me.vertices[vi].groups if x.group in groups)
        if t <= 1e-5:
            continue
        for gi in [x.group for x in me.vertices[vi].groups if x.group in groups]:
            groups[gi].remove([vi])
        for b, f in w.items():
            g = mesh.vertex_groups.get(b) or mesh.vertex_groups.new(name=b)
            g.add([vi], min(f * t, 1.0), 'REPLACE')
        changed += 1
    me.update()
    return changed


def _pockets(mesh, nb, slots):
    """Vertices inside the mouth / eye holes (behind each registered loop)."""
    me = mesh.data
    mw = mesh.matrix_world
    out = set()
    for loop in slots.values():
        cos = {i: mw @ me.vertices[i].co for i in loop}
        front = {j for i in loop for j in nb[i] if j not in loop and (mw @ me.vertices[j].co - cos[i]).y > 0}
        region = set(front)
        while front:
            front = {j for i in front for j in nb[i] if j not in region and j not in loop}
            region |= front
            if len(region) > len(me.vertices) * 0.3:
                region = set()
                break
        out |= region
    return out


SMOOTH_FACTOR = 0.5


def smooth_weights(arm, props, iters=2, factor=SMOOTH_FACTOR, max_influences=5):
    """Even out the face weights over the skin: every deform group of the
    face vertices blends with its edge ring (sums stay 1). The lip / eyelid
    loop vertices keep their weights and the mouth / eye pockets are left
    alone, so upper and lower lids / lips never mix. Returns vertices changed."""
    mesh = props.mesh
    me = mesh.data
    deform = {b.name for b in arm.data.bones if b.use_deform}
    gi = {g.index: g.name for g in mesh.vertex_groups if g.name in deform}
    # every bone the facial tool made (hand-renamed groups included), not the body
    face = {b.name for b in arm.data.bones if b.use_deform and O.GEN_PROP in b}
    face |= set(mesh.get(GROUPS_KEY, []))
    face.discard(props.head_bone)
    nb = _neighbors(mesh)
    lines = _lines(arm, props)
    pins, slots = _loop_pins(arm, mesh, lines)
    fixed = set(pins) | _pockets(mesh, nb, slots)
    w = {}
    for v in me.vertices:
        d = {gi[g.group]: g.weight for g in v.groups if g.group in gi and g.weight > 0}
        if d:
            w[v.index] = d
    region = {i for i, d in w.items() if any(b in face for b in d)}
    region |= {j for i in region for j in nb[i] if j in w}       # soft outer border
    region -= fixed
    for _ in range(iters):
        nxt = {}
        for i in region:
            ring = [w[j] for j in nb[i] if j in w]
            if not ring:
                continue
            r = _avg(ring)
            keys = set(w[i]) | set(r)
            nxt[i] = {b: (1 - factor) * w[i].get(b, 0.0) + factor * r.get(b, 0.0) for b in keys}
        w.update(nxt)
    groups = {n: mesh.vertex_groups[n] for n in set(gi.values())}
    for i in region:
        top = sorted(((b, x) for b, x in w[i].items() if x >= MIN_WEIGHT), key=lambda t: -t[1])[:max_influences]
        tot = sum(x for _b, x in top) or 1.0
        keep = {b for b, _x in top}
        for b, x in top:
            groups[b].add([i], x / tot, 'REPLACE')
        for g in me.vertices[i].groups:
            n = gi.get(g.group)
            if n and n not in keep:
                groups[n].remove([i])
    return len(region)


def _flip_name(n):
    if n.endswith(".L"):
        return n[:-2] + ".R"
    if n.endswith(".R"):
        return n[:-2] + ".L"
    return n


def mirror_weights(arm, props, tol=0.001):
    """Copy the skin weights of the +X (.L) half onto the -X (.R) half,
    .L groups becoming .R (center groups stay). Only deform-bone groups are
    touched; vertices on the center line are kept. Returns (changed, unmatched)."""
    from mathutils import kdtree
    mesh = props.mesh
    me = mesh.data
    mw = mesh.matrix_world
    bones = arm.data.bones
    hb = bones.get(props.head_bone)
    cx = (arm.matrix_world @ hb.head_local).x if hb else 0.0
    co = [mw @ v.co for v in me.vertices]
    kd = kdtree.KDTree(len(co))
    for i, p in enumerate(co):
        kd.insert(p, i)
    kd.balance()
    deform = {g.index: g for g in mesh.vertex_groups if g.name in bones and bones[g.name].use_deform}
    # chain bones (lips / lids / brows) map by their place along the line,
    # since the two sides can have a different number of bones
    chains = _chains(arm, props, _lines(arm, props))
    remap = {}
    for (chain, side), lst in chains.items():
        if side != ".L":
            continue
        other = chains.get((chain, ".R"))
        if not other:
            continue
        for u, b in lst:
            remap[b] = _split(other, u)

    def flip_w(name, w):
        if name in remap:
            return [(n, w * s) for n, s in remap[name].items()]
        f = _flip_name(name)
        return [(f, w)] if f in bones else [(props.head_bone, w)]

    def group(name):
        return mesh.vertex_groups.get(name) or mesh.vertex_groups.new(name=name)

    plan = []
    unmatched = 0
    for i, p in enumerate(co):
        if abs(p.x - cx) <= 1e-5:
            # center line: make .L / .R pairs equal (their average)
            ws = {deform[x.group].name: x.weight for x in me.vertices[i].groups
                  if x.group in deform and x.weight > 0}
            pairs = {}
            for n, w in ws.items():
                if n.endswith((".L", ".R")) and _flip_name(n) in bones:
                    pairs.setdefault(n[:-2], {})[n] = w
            if pairs:
                src = dict(ws)
                for base, d in pairs.items():
                    avg = sum(d.values()) / 2
                    src[base + ".L"] = avg
                    src[base + ".R"] = avg
                plan.append((i, src))
            continue
        if p.x > cx:
            continue
        q = p.copy()
        q.x = 2 * cx - q.x
        _c, j, d = kd.find(q)
        if d > tol:
            unmatched += 1
            continue
        src = {}
        for x in me.vertices[j].groups:
            if x.group in deform and x.weight > 0:
                for n, w in flip_w(deform[x.group].name, x.weight):
                    src[n] = src.get(n, 0.0) + w
        plan.append((i, src))
    for i, src in plan:
        for x in [x for x in me.vertices[i].groups if x.group in deform]:
            deform[x.group].remove([i])
        for name, w in src.items():
            if name in bones and w > 1e-5:
                group(name).add([i], min(w, 1.0), 'REPLACE')
    me.update()
    return len(plan), unmatched


class HB_OT_facial_mirror_weights(bpy.types.Operator):
    """얼굴 웨이트의 .R 절반을 .L 절반과 같게 만듭니다 (디폼 그룹만, .L 이름은
.R로 바뀜)"""

    bl_idname = "hb.facial_mirror_weights"
    bl_label = "웨이트 미러 L > R"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return O.facial_rig(context) is not None and context.scene.hb_facial.mesh is not None

    def execute(self, context):
        n, miss = mirror_weights(O.facial_rig(context), context.scene.hb_facial)
        msg = f"버텍스 {n}개 미러"
        if miss:
            msg += f"; {miss}개는 미러 버텍스 없음 (해당 부분 메쉬가 비대칭)"
        self.report({'WARNING'} if miss else {'INFO'}, msg)
        return {'FINISHED'}


class HB_OT_facial_spread_edge_weights(bpy.types.Operator):
    """잠기지 않은 입술 / 눈꺼풀 DEF 그룹에 칠한 웨이트를 위치에 따라 루프를 따라
있는 DEF 본들에 다시 나눠 줍니다 (유지할 그룹은 잠그세요)"""

    bl_idname = "hb.facial_spread_edge_weights"
    bl_label = "엣지 본으로 펼치기"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return O.facial_rig(context) is not None and context.scene.hb_facial.mesh is not None

    def execute(self, context):
        n = spread_to_edge_bones(O.facial_rig(context), context.scene.hb_facial)
        context.scene.hb_facial.mesh.data.update()
        self.report({'INFO'} if n else {'WARNING'},
                    f"버텍스 {n}개를 엣지 본에 펼침" if n
                    else "웨이트가 있는 잠기지 않은 입술 / 눈꺼풀 DEF 그룹 없음")
        return {'FINISHED'} if n else {'CANCELLED'}


SAVE_MARGIN_RINGS = 4   # skin rings saved around the weighted face (zero border)


def save_template(arm, props, path=None):
    """Write the face mesh's current facial weights as the transfer template
    (same layout as storm's: verts, polys, groups + roles, weights, landmarks).
    Returns (vertices saved, groups)."""
    mesh = props.mesh
    me = mesh.data
    mw = mesh.matrix_world
    bones = arm.data.bones
    head_bone = props.head_bone
    lines = _lines(arm, props)
    chains = _chains(arm, props, lines)
    roles = {}
    for (chain, side), lst in chains.items():
        for u, b in lst:
            roles.setdefault(b, {"chain": chain, "side": side, "u": round(u, 4)})
    face = [g for g in mesh.vertex_groups
            if g.name in bones and bones[g.name].use_deform and g.name != head_bone
            and (g.name in roles or bones[g.name].get(O.GEN_PROP) or g.name.startswith("DEF"))]
    gid = {g.index: i for i, g in enumerate(face)}
    for g in face:
        roles.setdefault(g.name, {"bones": {g.name: 1.0}})
    weighted = {v.index for v in me.vertices if any(x.group in gid and x.weight > 0 for x in v.groups)}
    nb = _neighbors(mesh)
    keep, front = set(weighted), set(weighted)
    for _ in range(SAVE_MARGIN_RINGS):
        front = {j for i in front for j in nb[i] if j not in keep}
        keep |= front
    order = sorted(keep)
    new = {o: i for i, o in enumerate(order)}
    polys = [[new[v] for v in p.vertices] for p in me.polygons if all(v in keep for v in p.vertices)]
    weights = []
    for o in order:
        weights.append([[gid[x.group], round(x.weight, 4)] for x in me.vertices[o].groups
                        if x.group in gid and x.weight > 0.002])
    lm = _our_landmarks(arm, props, lines)
    out = {
        "source": f"hb_toolkit face weight template (saved from {mesh.name}; first built from "
                  "Blender Studio 'storm' v1.1, CC-BY 4.0)",
        "smooth": 0,             # already clean: sample it as is
        "eye_dist": round(_R()._eye_dist(arm, 1.0), 5),
        "verts": [[round(c, 5) for c in (mw @ me.vertices[o].co)] for o in order],
        "polys": polys,
        "groups": [g.name for g in face],
        "roles": {g.name: roles[g.name] for g in face},
        "weights": weights,
        "landmarks": {k: [round(c, 5) for c in p] for k, p in lm.items()},
    }
    with open(path or template_path(), "w", encoding="utf-8") as f:
        json.dump(out, f, separators=(",", ":"))
    reload_data()
    return len(order), len(face)


class HB_OT_facial_save_weights(bpy.types.Operator):
    """얼굴 메쉬의 얼굴 웨이트(직접 또는 Face Weights로 정리한 것)를 이후 모든
Build Rig가 가져올 템플릿으로 저장합니다 (data/face_weights.json)"""

    bl_idname = "hb.facial_save_weights"
    bl_label = "웨이트를 템플릿으로 저장"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        return O.facial_rig(context) is not None and context.scene.hb_facial.mesh is not None

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        n, g = save_template(O.facial_rig(context), context.scene.hb_facial)
        self.report({'INFO'}, f"버텍스 {n}개 / 그룹 {g}개를 {TEMPLATE_FILE}에 저장")
        return {'FINISHED'}


class HB_OT_facial_face_weights(bpy.types.Operator):
    """얼굴 메쉬에 storm 방식의 얼굴 웨이트를 칠합니다 (이 버튼이 이전에 쓴 웨이트는
교체)"""

    bl_idname = "hb.facial_face_weights"
    bl_label = "얼굴 웨이트"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return O.facial_rig(context) is not None and context.scene.hb_facial.mesh is not None

    def execute(self, context):
        props = context.scene.hb_facial
        arm = O.facial_rig(context)
        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        n, notes = transfer(context, arm, props)
        msg = f"버텍스 {n}개에 얼굴 웨이트 적용"
        if notes:
            msg += "; " + "; ".join(notes)
        self.report({'INFO'} if n else {'WARNING'}, msg)
        return {'FINISHED'} if n else {'CANCELLED'}


class HB_OT_facial_smooth_weights(bpy.types.Operator):
    """피부 위에서 얼굴 웨이트를 스무스합니다 (입술 / 눈꺼풀 엣지와 입 안쪽은 그대로
두어 위아래가 섞이지 않음)"""

    bl_idname = "hb.facial_smooth_weights"
    bl_label = "스무스"
    bl_options = {'REGISTER', 'UNDO'}

    iterations: bpy.props.IntProperty(name="반복 횟수", default=2, min=1, max=10)
    factor: bpy.props.FloatProperty(name="강도", default=SMOOTH_FACTOR, min=0.05, max=1.0)

    @classmethod
    def poll(cls, context):
        return O.facial_rig(context) is not None and context.scene.hb_facial.mesh is not None

    def execute(self, context):
        props = context.scene.hb_facial
        n = smooth_weights(O.facial_rig(context), props, self.iterations, self.factor)
        self.report({'INFO'}, f"Smoothed {n} vertices")
        return {'FINISHED'}


_classes = (HB_OT_facial_face_weights, HB_OT_facial_save_weights,
            HB_OT_facial_spread_edge_weights, HB_OT_facial_mirror_weights,
            HB_OT_facial_smooth_weights)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
