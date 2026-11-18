"""Storm's facial shape keys: keys, drivers and (optionally) storm's shapes.

data/storm_shapekeys.json (data/extract_storm_shapekeys.py) lists storm's
SK- keys with their driver as a product of terms. Each term reads one bone
channel; its axis is stored as a world direction and its amount relative to
the eye distance, so the driver is rebuilt on whatever axes / size this rig
has (e.g. a hand-rolled lip corner).

Create   empty keys (storm's order, slider range, MSK-face.L/R mask) with
         their drivers, for the keys whose controls exist in this rig.
Transfer storm's shape for a key carried onto this face: storm's deltas are
         pushed through the same landmark warp as the face weights, sampled
         at each vertex's closest point on storm's head, and flooded into
         the mouth pocket.
Clear    removes the keys this tool made (and their drivers) only.
"""

import json
import os

import bpy
from mathutils import Vector
from mathutils.bvhtree import BVHTree
from mathutils.interpolate import poly_3d_calc

from . import operators as O

DATA_FILE = "storm_shapekeys.json"
KEYS_PROP = "hb_shape_keys"          # mesh object property: key names this tool made
BASIS_PROP = "hb_shape_basis"        # True when this tool added the Basis
MASKS = ("MSK-face.L", "MSK-face.R")
MASK_BLEND = 0.06                    # x eye distance: half width of the centre blend
MIN_ALIGN = 0.8                      # |cos| between storm's axis and ours, else a note
FILL_RINGS = 10                      # rings flooded into the mouth pocket
_DATA = None


def data():
    global _DATA
    if _DATA is None:
        with open(os.path.join(os.path.dirname(__file__), "data", DATA_FILE), encoding="utf-8") as f:
            _DATA = json.load(f)
    return _DATA


def _R():
    from . import rig
    return rig


def specs(arm, groups=("mouth", "brow", "cheek")):
    """(buildable key specs, [(name, reason)] skipped) for this rig."""
    ok, skipped = [], []
    for k in data()["keys"]:
        if k["group"] not in groups:
            continue
        if not k["terms"]:
            skipped.append((k["name"], "드라이버 미지원"))
        elif any(t["bone"] is None or t["bone"] not in arm.data.bones for t in k["terms"]):
            miss = next(t["bone"] or t["storm"] for t in k["terms"]
                        if t["bone"] is None or t["bone"] not in arm.data.bones)
            skipped.append((k["name"], f"컨트롤 없음: {miss}"))
        else:
            ok.append(k)
    return ok, skipped


# ----------------------------------------------------------------------
# Masks
# ----------------------------------------------------------------------
def ensure_masks(mesh, arm):
    """MSK-face.L / .R: side masks with a soft blend over the centre line."""
    w = MASK_BLEND * _R()._eye_dist(arm, 1.0)
    mw = mesh.matrix_world
    groups = [mesh.vertex_groups.get(n) or mesh.vertex_groups.new(name=n) for n in MASKS]
    allv = list(range(len(mesh.data.vertices)))
    for g in groups:
        g.remove(allv)
    buckets = {}
    for v in mesh.data.vertices:
        x = (mw @ v.co).x
        t = min(1.0, max(0.0, (x + w) / (2 * w))) if w > 0 else float(x > 0)
        t = round(t * t * (3 - 2 * t), 3)
        buckets.setdefault(t, []).append(v.index)
    for t, ids in buckets.items():
        if t > 0:
            groups[0].add(ids, t, 'REPLACE')
        if t < 1:
            groups[1].add(ids, 1 - t, 'REPLACE')


# ----------------------------------------------------------------------
# Drivers
# ----------------------------------------------------------------------
AXES = "XYZ"


def _term_expr(arm, term, var, k):
    """(expression, transform type, alignment) of one term on this rig."""
    bone = arm.data.bones[term["bone"]]
    m = arm.matrix_world.to_3x3().normalized() @ bone.matrix_local.to_3x3().normalized()
    d = Vector(term["dir"])
    dots = [m.col[i].dot(d) for i in range(3)]
    i = max(range(3), key=lambda j: abs(dots[j]))
    sign = 1.0 if dots[i] > 0 else -1.0
    full = term["full"] * (k if term["kind"] == 'LOC' else 1.0) * sign
    if term["clamp"]:
        expr = f"({'max' if full > 0 else 'min'}(0, {var})/{full:.5f})"
    else:
        expr = f"({var}/{full:.5f})"
    return expr, f"{term['kind']}_{AXES[i]}", abs(dots[i])


def _frozen_axes(arm, bone_name):
    """Local axes storm's freeze limit (SK_Limit) holds on this control."""
    c = data().get("controls", {}).get(bone_name)
    bone = arm.data.bones.get(bone_name)
    if c is None or bone is None:
        return set()
    m = arm.matrix_world.to_3x3().normalized() @ bone.matrix_local.to_3x3().normalized()
    out = set()
    for fz in c["freeze"]:
        for d in fz["dirs"]:
            out.add(max(range(3), key=lambda j: abs(m.col[j].dot(Vector(d)))))
    return out


def _drive(key_block, arm, spec, k):
    """(Re)create the key's driver. Returns notes."""
    notes = []
    try:
        key_block.driver_remove("value")
    except (TypeError, RuntimeError):
        pass
    fc = key_block.driver_add("value")
    d = fc.driver
    d.type = 'SCRIPTED'
    for v in list(d.variables):
        d.variables.remove(v)
    parts = []
    for i, term in enumerate(spec["terms"]):
        var = f"v{i}"
        expr, ttype, align = _term_expr(arm, term, var, k)
        if align < MIN_ALIGN:
            notes.append(f"{spec['name']}: {term['bone']} 축 정렬도가 Storm 대비 {align:.2f}로 낮음")
        v = d.variables.new()
        v.name, v.type = var, 'TRANSFORMS'
        t = v.targets[0]
        t.id, t.bone_target = arm, term["bone"]
        space = term["space"]
        if term["kind"] == 'LOC' and space == 'LOCAL_SPACE' and \
                AXES.index(ttype[-1]) in _frozen_axes(arm, term["bone"]):
            space = 'TRANSFORM_SPACE'      # the limit holds the bone: read the raw channel
        t.transform_type, t.transform_space = ttype, space
        parts.append(expr)
    d.expression = " * ".join(parts)
    if spec.get("curve"):
        for m in list(fc.modifiers):
            fc.modifiers.remove(m)
        fc.keyframe_points.add(len(spec["curve"]))
        for p, (x, y, hl, hr) in zip(fc.keyframe_points, spec["curve"]):
            p.co = (x, y)
            p.handle_left_type = p.handle_right_type = 'FREE'
            p.handle_left, p.handle_right = hl, hr
        fc.extrapolation = 'LINEAR'
    return notes


# ----------------------------------------------------------------------
# Create / clear
# ----------------------------------------------------------------------
def ensure_brow_controls(context, arm):
    """Bring a rig built before the storm brow keys up to date, in place:
    MSTR-BROW stops carrying the DEF-Brow_local bones (it drives the SK-Brow
    keys like storm's CTL-Brow_all) and CTL-Brow_in / mid / out are added
    over the brow. Returns the bones it made."""
    from . import template as T
    from .parts import BROW_SK_CTRLS, BROW_SK_LIFT
    bones = arm.data.bones
    made = []
    todo = [s for s in (".L", ".R") if f"MSTR-BROW{s}" in bones and f"DEF-Brow_local7{s}" in bones
            and (any(f"CTL-Brow_{n}{s}" not in bones for n in BROW_SK_CTRLS)
                 or any(b.parent is not None and b.parent.name == f"MSTR-BROW{s}" for b in bones))]
    if not todo:
        return made
    coll_name = bones[f"MSTR-BROW{todo[0]}"].collections[0].name if bones[f"MSTR-BROW{todo[0]}"].collections else None
    with O._EditMode(context, arm) as ebs:
        for s in todo:
            master = ebs[f"MSTR-BROW{s}"]
            for eb in [b for b in ebs if b.parent == master]:
                eb.parent = master.parent                      # (keeps its place)
            cl = master.tail - master.head
            for nm, i in BROW_SK_CTRLS.items():
                name = f"CTL-Brow_{nm}{s}"
                if name in ebs:
                    continue
                src = ebs[f"DEF-Brow_local{i + 1}{s}"]
                eb = ebs.new(name)
                eb.head = src.head + cl * BROW_SK_LIFT
                eb.tail = eb.head + cl
                eb.roll = master.roll
                eb.parent = master.parent
                eb.use_deform = False
                if coll_name and coll_name in arm.data.collections_all:
                    arm.data.collections_all[coll_name].assign(eb)
                made.append(name)
    for n in made:
        arm.data.bones[n][O.GEN_PROP] = arm.data.bones[f"MSTR-BROW{n[-2:]}"].get(O.GEN_PROP, "BASE")
    if made:
        _R().apply_storm_shapes(context, arm, only=set(made))
    return made


def setup_controls(arm, spec_list):
    """storm's locks / "can't move this way" limits on the controls behind the
    keys (their raw channel still drives the key; the bone stays put)."""
    controls = data().get("controls", {})
    used = {t["bone"] for s in spec_list for t in s["terms"]}
    for name in used:
        c = controls.get(name)
        pb = arm.pose.bones.get(name)
        if c is None or pb is None:
            continue
        m = arm.matrix_world.to_3x3().normalized() @ pb.bone.matrix_local.to_3x3().normalized()

        def axis(d):
            dots = [m.col[i].dot(Vector(d)) for i in range(3)]
            i = max(range(3), key=lambda j: abs(dots[j]))
            return i, dots[i] > 0

        for d in c["lock_loc"]:
            pb.lock_location[axis(d)[0]] = True
        for d in c["lock_rot"]:
            i = axis(d)[0]
            pb.lock_rotation[i] = True
        for fz in c["freeze"]:
            con = pb.constraints.get(LIMIT_NAME) or pb.constraints.new('LIMIT_LOCATION')
            con.name = LIMIT_NAME
            con.owner_space = fz["space"]
            for a in "xyz":
                setattr(con, f"use_min_{a}", False)
                setattr(con, f"use_max_{a}", False)
                setattr(con, f"min_{a}", 0.0)
                setattr(con, f"max_{a}", 0.0)
            for d in fz["dirs"]:
                i, pos = axis(d)
                setattr(con, f"use_{'max' if pos else 'min'}_{'xyz'[i]}", True)


def ensure_storm_masks(arm, props, sampler):
    """The masks storm's keys use besides the side masks, sampled onto this face."""
    mesh = props.mesh
    n = len(mesh.data.vertices)
    mir = mirror_map(mesh)
    for name, rows in data().get("masks", {}).items():
        field = sampler.scalar(rows)
        # sampling is not exactly symmetric: a hole on one side cuts that
        # side's keys (right brow) and the correctives then lean to make up
        field = {i: max(field.get(i, 0.0), field.get(mir.get(i, i), 0.0))
                 for i in set(field) | {mir[j] for j in field if j in mir}}
        vg = mesh.vertex_groups.get(name) or mesh.vertex_groups.new(name=name)
        vg.remove(list(range(n)))
        buckets = {}
        for vi, w in field.items():
            buckets.setdefault(round(min(1.0, w), 3), []).append(vi)
        for w, ids in buckets.items():
            if w > 0:
                vg.add(ids, w, 'REPLACE')


LIMIT_NAME = "SK_Limit"
GROUPS = ("mouth", "brow", "cheek")


def create(context, arm, props, groups=GROUPS, sampler=None):
    """Keys + drivers for the buildable specs. Returns (made, skipped, notes)."""
    mesh = props.mesh
    notes0 = []
    if "brow" in groups:
        new = ensure_brow_controls(context, arm)
        if new:
            notes0.append(f"눈썹 컨트롤 {len(new)}개 추가됨 (이제 MSTR-BROW가 본 대신 쉐이프키를 구동)")
    ok, skipped = specs(arm, groups)
    if mesh.data.shape_keys is None:
        mesh.shape_key_add(name="Basis", from_mix=False)
        mesh[BASIS_PROP] = True
    ensure_masks(mesh, arm)
    want = {s["mask"] for s in ok if s["mask"]} - set(MASKS)
    if any(m not in mesh.vertex_groups for m in want):
        try:
            ensure_storm_masks(arm, props, sampler or Sampler(arm, props))
        except ValueError as e:
            notes0.append(str(e))
    setup_controls(arm, ok)
    blocks = mesh.data.shape_keys.key_blocks
    mine = set(mesh.get(KEYS_PROP, []))
    k = _R()._eye_dist(arm, 1.0)
    notes, made = notes0, 0
    # storm gives some .L / .R pairs different masks (Brow_all .L: none, .R:
    # MSK-inner_mouth); storm's mask spans the head so it never shows there,
    # here it cuts one side only -> a pair that disagrees gets no mask
    masks = {s["name"]: s["mask"] for s in ok}

    def _flip(name):
        return name[:-2] + {".L": ".R", ".R": ".L"}[name[-2:]] if name[-2:] in (".L", ".R") else name

    def _mask(spec):
        m, nm = spec["mask"], spec["name"]
        if spec.get("group") in UNPOSED and m not in MASKS:
            # brow / cheek edit meshes are cut by hand (MSK-L); storm's sampled
            # masks (MSK-inner_mouth...) miss part of this brow and cut the sculpt
            return ""
        if nm.endswith((".L", ".R")):       # (MSK-face.L / .R are a pair, not a mismatch)
            other = masks.get(_flip(nm), _flip(m))
            if _flip(other) != m:
                return ""
        return m

    for spec in ok:
        kb = blocks.get(spec["name"])
        if kb is None:
            kb = mesh.shape_key_add(name=spec["name"], from_mix=False)
            mine.add(spec["name"])
            made += 1
        elif spec["name"] not in mine:
            notes.append(f"{spec['name']}: 기존 키 유지, 드라이버 교체")
        kb.slider_min, kb.slider_max = spec["range"]
        kb.vertex_group = _mask(spec) if _mask(spec) in mesh.vertex_groups else ""
        kb.value = 0.0
        notes += _drive(kb, arm, spec, k)
    mesh[KEYS_PROP] = sorted(mine)
    mesh.active_shape_key_index = 0
    return made, skipped, notes


def clear(props):
    """Remove the keys this tool made and their drivers. Returns the count."""
    mesh = props.mesh
    sk = mesh.data.shape_keys
    if sk is None:
        return 0
    n = 0
    for name in list(mesh.get(KEYS_PROP, [])):
        kb = sk.key_blocks.get(name)
        if kb is None:
            continue
        try:
            kb.driver_remove("value")
        except (TypeError, RuntimeError):
            pass
        mesh.shape_key_remove(kb)
        n += 1
    for key in (KEYS_PROP, CUSTOM_PROP):
        if key in mesh:
            del mesh[key]
    sk = mesh.data.shape_keys
    if sk is not None and mesh.get(BASIS_PROP) and len(sk.key_blocks) == 1:
        mesh.shape_key_remove(sk.key_blocks[0])
    if BASIS_PROP in mesh:
        del mesh[BASIS_PROP]
    return n


# ----------------------------------------------------------------------
# Transfer storm's shapes
# ----------------------------------------------------------------------
class Sampler:
    """Maps a per-storm-vertex vector field onto this mesh's vertices."""

    def __init__(self, arm, props):
        import numpy as np
        from . import lattice, weights as W
        self.np = np
        mesh = props.mesh
        self.mesh = mesh
        self.warp, sd = lattice.storm_warp(arm, props)
        if self.warp is None:
            raise ValueError("랜드마크가 너무 적습니다 (루프를 등록하고 리그를 빌드하세요)")
        self.src = np.asarray(sd["verts"], float)
        self.dst = np.asarray(self.warp(sd["verts"]), float)
        verts = [Vector(v) for v in self.dst]
        polys = sd["polys"]
        bvh = BVHTree.FromPolygons(verts, polys)
        max_d = W.MAX_DIST * _R()._eye_dist(arm, 1.0)
        mw = mesh.matrix_world
        nrm = mw.to_3x3().inverted().transposed()
        self.rows = {}                 # our vertex -> [(storm vertex, weight)]
        near = set()                   # close to the face but facing away (mouth inside)
        for v in mesh.data.vertices:
            loc, pn, fi, _d = bvh.find_nearest(mw @ v.co, max_d)
            if loc is None:
                continue
            if (nrm @ v.normal).normalized().dot(pn) < W.MIN_FACING:
                near.add(v.index)
                continue
            poly = polys[fi]
            bary = poly_3d_calc([verts[i] for i in poly], loc)
            self.rows[v.index] = [(vi, bw) for vi, bw in zip(poly, bary) if bw > 0]
        # flood order for the unsampled vertices: ring by ring from the sampled ones
        nb = W._neighbors(mesh)
        self.fill = []
        known = set(self.rows)
        front = known
        for _ in range(FILL_RINGS):
            ring = {j for i in front for j in nb[i] if j in near and j not in known}
            if not ring:
                break
            self.fill.append([(j, [x for x in nb[j] if x in known]) for j in ring])
            known |= ring
            front = ring
        self.to_local = mw.to_3x3().inverted()

    def scalar(self, sparse):
        """{our vertex: weight} for a storm vertex-group table [[vi, w]]."""
        np = self.np
        field = np.zeros(len(self.src))
        for vi, w in sparse:
            field[vi] = w
        out = {}
        for vi, row in self.rows.items():
            x = sum(field[si] * bw for si, bw in row)
            if x > 0.002:
                out[vi] = float(x)
        for ring in self.fill:
            new = {}
            for j, srcs in ring:
                vals = [out.get(s, 0.0) for s in srcs]
                if vals and sum(vals) > 0:
                    new[j] = sum(vals) / len(vals)
            out.update(new)
        return out

    def deltas(self, sparse, symmetric=True):
        """{our vertex: Vector (object space)} for storm deltas [[vi, x, y, z]].
        symmetric=False keeps a one-sided shape one-sided (brow / cheek .L keys)."""
        np = self.np
        idx = np.array([r[0] for r in sparse], int)
        d = np.array([r[1:] for r in sparse], float)
        field = np.zeros_like(self.src)
        if len(idx):
            # the warp bends space: carry each delta as the difference of warped points
            field[idx] = np.asarray(self.warp(self.src[idx] + d), float) - self.dst[idx]
        out = {}
        for vi, row in self.rows.items():
            acc = np.zeros(3)
            for si, bw in row:
                acc += field[si] * bw
            if acc @ acc > 1e-12:
                out[vi] = acc
        for ring in self.fill:
            new = {}
            for j, srcs in ring:
                vals = [out[s] for s in srcs if s in out]
                if vals:
                    new[j] = sum(vals) / len(srcs)
            out.update(new)
        out = {vi: self.to_local @ Vector(v) for vi, v in out.items()}
        return self._symmetric(out) if symmetric else out

    def mirrored(self, d):
        """A one-sided shape flipped to the other side."""
        if self.mirror is None:
            self.mirror = mirror_map(self.mesh)
        return {self.mirror[i]: Vector((-v.x, v.y, v.z)) for i, v in d.items() if i in self.mirror}

    def _symmetric(self, d):
        """storm sculpts both sides as one shape (the L / R masks split it):
        average every delta with its mirror vertex's so the sides match."""
        if self.mirror is None:
            self.mirror = mirror_map(self.mesh)
        out = {}
        zero = Vector()
        for i in set(d) | {self.mirror[i] for i in d if i in self.mirror}:
            j = self.mirror.get(i)
            a = d.get(i, zero)
            if j is None:
                out[i] = a
                continue
            b = d.get(j, zero)
            out[i] = (a + Vector((-b.x, b.y, b.z))) * 0.5
        return out

    mirror = None


def transfer(context, arm, props, names=None, sampler=None):
    """Write storm's shape into the (existing) keys. Returns the names done."""
    mesh = props.mesh
    sk = mesh.data.shape_keys
    if sk is None:
        return []
    by_name = {k["name"]: k for k in data()["keys"]}
    todo = [n for n in (names or mesh.get(KEYS_PROP, [])) if n in by_name and n in sk.key_blocks]
    if not todo:
        return []
    sampler = sampler or Sampler(arm, props)
    basis = sk.reference_key.data
    done = []
    for n in todo:
        kb = sk.key_blocks[n]
        spec = by_name[n]
        # storm has two kinds of sided keys: one symmetric shape split by the
        # MSK-face masks (mouth), and truly one-sided shapes (brow, cheek).
        one_sided = n.endswith((".L", ".R")) and not (spec["mask"] or "").startswith("MSK-face.")
        if one_sided:
            left = by_name.get(n[:-2] + ".L", spec)
            dl = sampler.deltas(left["deltas"], symmetric=False)
            if n.endswith(".R"):
                dl = sampler.mirrored(dl)
        else:
            dl = sampler.deltas(spec["deltas"])
        for i, p in enumerate(basis):
            kb.data[i].co = p.co + dl[i] if i in dl else p.co
        done.append(n)
    mesh.data.update()
    return done


MIRROR_TOL = 0.0005     # m: a vertex and its mirror may be this far off


def mirror_map(mesh):
    """{vertex: mirror vertex} across x = 0 (rest shape, object space)."""
    from mathutils import kdtree
    sk = mesh.data.shape_keys
    cos = [p.co for p in sk.reference_key.data] if sk else [v.co for v in mesh.data.vertices]
    kd = kdtree.KDTree(len(cos))
    for i, c in enumerate(cos):
        kd.insert(c, i)
    kd.balance()
    out = {}
    for i, c in enumerate(cos):
        _co, j, dist = kd.find((-c.x, c.y, c.z))
        if dist < MIRROR_TOL:
            out[i] = j
    return out


def mirror_key(mesh, name):
    """Make a key's shape symmetric from its own side (.L: +x, .R: -x,
    unsided: +x) and give the .L / .R partner the same shape (storm: one
    symmetric shape, split by the side masks). Returns the keys written."""
    sk = mesh.data.shape_keys
    kb = sk.key_blocks[name]
    basis = sk.reference_key.data
    mir = mirror_map(mesh)
    sign = -1.0 if name.endswith(".R") else 1.0
    delta = [kb.data[i].co - basis[i].co for i in range(len(basis))]
    for i, j in mir.items():
        if basis[i].co.x * sign < -1e-6:              # the weak side copies the strong one
            d = delta[j]
            kb.data[i].co = basis[i].co + Vector((-d.x, d.y, d.z))
        elif abs(basis[i].co.x) <= 1e-6:              # centre line stays on it
            d = delta[i]
            kb.data[i].co = basis[i].co + Vector((0.0, d.y, d.z))
    done = [name]
    partner = name[:-2] + (".R" if name.endswith(".L") else ".L") if name.endswith((".L", ".R")) else None
    if partner and partner in sk.key_blocks:
        pk = sk.key_blocks[partner]
        for i in range(len(basis)):
            pk.data[i].co = kb.data[i].co
        done.append(partner)
    mesh.data.update()
    return done


# ----------------------------------------------------------------------
# Edit a key on the posed face
# ----------------------------------------------------------------------
SCULPT_TAG = "SK_SCULPT"
ORIG_ATTR = "hb_orig"
FD_EPS = 0.001          # m: key offset used to measure how the rig carries a delta
SOLVE_ITERS = 5


def sculpt_object():
    return next((o for o in bpy.data.objects if o.get(O.OBJ_PROP) == SCULPT_TAG), None)


class _Flat:
    """Face mesh evaluated without the modifiers that change its vertex count."""

    def __init__(self, mesh, also=()):
        self.mesh = mesh
        self.mods = [m for m in mesh.modifiers
                     if m.type in ('SUBSURF', 'MASK', 'MULTIRES') or m.type in also]

    def __enter__(self):
        self.state = [m.show_viewport for m in self.mods]
        for m in self.mods:
            m.show_viewport = False
        return self

    def __exit__(self, *exc):
        for m, s in zip(self.mods, self.state):
            m.show_viewport = s


def _eval(context, mesh):
    import numpy as np
    mesh.data.update()
    context.view_layer.update()
    ev = mesh.evaluated_get(context.evaluated_depsgraph_get())
    out = np.empty(len(ev.data.vertices) * 3)
    ev.data.vertices.foreach_get("co", out)
    return out.reshape(-1, 3)


def _pose_for(arm, spec):
    """Move the key's controls to where the key is fully on. Returns the
    pose it replaced: {bone: [mode, loc, quat, euler]}."""
    k = _R()._eye_dist(arm, 1.0)
    saved = {}
    for term in spec["terms"]:
        pb = arm.pose.bones[term["bone"]]
        if pb.name not in saved:
            saved[pb.name] = [pb.rotation_mode, list(pb.location), list(pb.rotation_quaternion),
                              list(pb.rotation_euler)]
        bone = pb.bone
        m = arm.matrix_world.to_3x3().normalized() @ bone.matrix_local.to_3x3().normalized()
        dots = [m.col[i].dot(Vector(term["dir"])) for i in range(3)]
        i = max(range(3), key=lambda j: abs(dots[j]))
        full = term["full"] * (k if term["kind"] == 'LOC' else 1.0) * (1.0 if dots[i] > 0 else -1.0)
        if term["kind"] == 'LOC':
            pb.location[i] = full
        else:
            if pb.rotation_mode in ('QUATERNION', 'AXIS_ANGLE'):
                pb.rotation_mode = 'XYZ'
                pb.rotation_euler = (0.0, 0.0, 0.0)
            pb.rotation_euler[i] = full
    return saved


def _restore_pose(arm, saved):
    for name, (mode, loc, quat, eul) in saved.items():
        pb = arm.pose.bones.get(name)
        if pb is None:
            continue
        pb.rotation_mode = mode
        pb.location, pb.rotation_quaternion, pb.rotation_euler = loc, quat, eul


def edit_begin(context, arm, props, name, pose=True):
    """Pose the key's controls and make a sculpt copy of the deformed face."""
    mesh = props.mesh
    spec = next((k for k in data()["keys"] if k["name"] == name), None)
    prev_active = context.view_layer.objects.active
    prev_mode = prev_active.mode if prev_active else 'OBJECT'
    if prev_active and prev_mode != 'OBJECT':
        try:                      # first: fails (changing nothing) while a transform is running
            bpy.ops.object.mode_set(mode='OBJECT')
        except RuntimeError as e:
            raise ValueError("진행 중인 이동 / 회전을 먼저 끝내세요") from e
    saved = {}
    if pose and spec and spec["terms"] and all(t["bone"] in arm.data.bones for t in spec["terms"]):
        saved = _pose_for(arm, spec)
    with _Flat(mesh):
        context.view_layer.update()
        dg = context.evaluated_depsgraph_get()
        me = bpy.data.meshes.new_from_object(mesh.evaluated_get(dg), preserve_all_data_layers=True,
                                             depsgraph=dg)
    me.name = f"SCULPT-{name}"
    attr = me.attributes.new(ORIG_ATTR, 'FLOAT_VECTOR', 'POINT')
    flat = [c for v in me.vertices for c in v.co]
    attr.data.foreach_set("vector", flat)
    obj = bpy.data.objects.new(me.name, me)
    obj.matrix_world = mesh.matrix_world
    (mesh.users_collection[0] if mesh.users_collection else context.scene.collection).objects.link(obj)
    for m in mesh.modifiers:
        if m.type == 'SUBSURF':
            s = obj.modifiers.new(m.name, 'SUBSURF')
            s.levels, s.render_levels = m.levels, m.render_levels
    obj[O.OBJ_PROP] = SCULPT_TAG
    obj["hb_sk_key"] = name
    obj["hb_sk_mesh"] = mesh.name
    obj["hb_sk_pose"] = json.dumps(saved)
    obj["hb_sk_prev"] = json.dumps([prev_active.name if prev_active else "", prev_mode])
    mesh.hide_set(True)
    for o in context.selected_objects:
        o.select_set(False)
    obj.select_set(True)
    context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode='SCULPT')
    return obj


def _edit_end(context, arm, obj):
    """Remove the sculpt copy, show the face again, put the pose / mode back."""
    mesh = bpy.data.objects.get(obj.get("hb_sk_mesh", ""))
    saved = json.loads(obj.get("hb_sk_pose", "{}"))
    prev_name, prev_mode = json.loads(obj.get("hb_sk_prev", '["", "OBJECT"]'))
    if context.view_layer.objects.active == obj and obj.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    me = obj.data
    bpy.data.objects.remove(obj, do_unlink=True)
    if me.users == 0:
        bpy.data.meshes.remove(me)
    if mesh is not None:
        mesh.hide_set(False)
    if arm is not None:
        _restore_pose(arm, saved)
    prev = bpy.data.objects.get(prev_name)
    if prev is not None and prev.name in context.view_layer.objects:
        context.view_layer.objects.active = prev
        prev.select_set(True)
        if prev_mode != 'OBJECT':
            try:
                bpy.ops.object.mode_set(mode=prev_mode)
            except RuntimeError:
                pass


def edit_apply(context, arm, props, mirror=True):
    """Put the sculpted change into the key so the rigged face matches the
    sculpt in this pose. Returns (changed, unreachable, residual mm, keys)."""
    import numpy as np
    obj = sculpt_object()
    mesh = bpy.data.objects[obj["hb_sk_mesh"]]
    name = obj["hb_sk_key"]
    if context.view_layer.objects.active == obj and obj.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')          # flush the sculpt to the mesh
    n = len(mesh.data.vertices)
    if len(obj.data.vertices) != n or ORIG_ATTR not in obj.data.attributes:
        raise ValueError("스컬프트 복사본의 토폴로지가 바뀌었습니다 (remesh / dyntopo 사용 불가)")
    sc = np.empty(n * 3)
    obj.data.vertices.foreach_get("co", sc)
    orig = np.empty(n * 3)
    obj.data.attributes[ORIG_ATTR].data.foreach_get("vector", orig)
    user = (sc - orig).reshape(-1, 3)
    changed = np.linalg.norm(user, axis=1) > 1e-6
    kb = mesh.data.shape_keys.key_blocks[name]
    mesh.hide_set(False)
    result = (0, 0, 0.0, [])
    if changed.any():
        K = np.empty(n * 3)
        kb.data.foreach_get("co", K)
        K = K.reshape(-1, 3)

        def put(arr):
            kb.data.foreach_set("co", arr.reshape(-1))

        # how the rig carries a key offset in this pose (per vertex 3x3),
        # without the smoothing modifier (it couples neighbours)
        def measure():
            with _Flat(mesh, also=('CORRECTIVE_SMOOTH',)):
                e0 = _eval(context, mesh)
                m = np.zeros((n, 3, 3))
                for a in range(3):
                    off = K.copy()
                    off[:, a] += FD_EPS
                    put(off)
                    m[:, :, a] = (_eval(context, mesh) - e0) / FD_EPS
                put(K)
            return m

        M = measure()
        if np.abs(M[changed]).max() < 1e-2:
            # the face was hidden a moment ago and may not be evaluated yet: force it
            context.scene.frame_set(context.scene.frame_current)
            M = measure()
        det = np.linalg.det(M)
        solvable = changed & (np.abs(det) > 1e-3)
        idx = np.nonzero(solvable)[0]
        unreachable = int(changed.sum() - len(idx))
        if not len(idx):
            mesh.hide_set(True)               # keep the sculpt: nothing could be stored
            raise ValueError(f"{name}은(는) 이 포즈에서 스컬프트한 버텍스를 움직이지 않습니다 "
                             "(이 포즈에서 키가 꺼져 있거나 버텍스가 마스크 밖에 있음)")
        if len(idx):
            Minv = np.linalg.inv(M[idx])
            with _Flat(mesh):
                target = _eval(context, mesh)[idx] + user[idx]
                res = 0.0
                for _ in range(SOLVE_ITERS):
                    r = target - _eval(context, mesh)[idx]
                    res = float(np.linalg.norm(r, axis=1).max())
                    if res < 1e-5:
                        break
                    K[idx] += np.einsum('nij,nj->ni', Minv, r)
                    put(K)
                res = float(np.linalg.norm(target - _eval(context, mesh)[idx], axis=1).max())
        else:
            res = 0.0
        keys = mirror_key(mesh, name) if mirror and len(idx) else [name]
        result = (len(idx), unreachable, res * 1000, keys)
    _edit_end(context, arm, obj)
    return result


# ----------------------------------------------------------------------
# Pose Shape Keys add-on (Blender Studio): one-click setup
# ----------------------------------------------------------------------
# The add-on keeps, per pose key: a pose (Action + frame), a storage object
# (the deformed face in that pose, to sculpt / deform) and target shape keys.
# Its Push writes "storage - rigged face (Armature modifier only, targets
# muted)" into the targets. Here every storm key gets its pose key:
#   pose     both sides at once (.L and .R controls), so one symmetric
#            sculpt fills both keys and the MSK-face masks split it (storm)
#   storage  <mesh>-<name>: Basis / Morph Target (the posed face with the
#            keys as they are now) / New Changes, head only shown, in a grid
POSE_ACTION = "HB-ShapeKey_Poses"
STORAGE_TAG = "SK_STORAGE"
LABEL_TAG = "SK_LABEL"
STORAGE_COLL = "Facial_ShapeKeys"
HEAD_GROUP = "MSK-head"


def pose_keys_available(mesh):
    return hasattr(bpy.types.Mesh, "pose_keys") and mesh is not None


def _base(name):
    return name[:-2] if name.endswith((".L", ".R")) else name


def _pose_channels(arm, spec_list, k):
    """{(bone, path, index or -1): value} that turns these keys fully on."""
    from mathutils import Quaternion
    out = {}
    for spec in spec_list:
        for term in spec["terms"]:
            pb = arm.pose.bones[term["bone"]]
            m = arm.matrix_world.to_3x3().normalized() @ pb.bone.matrix_local.to_3x3().normalized()
            dots = [m.col[i].dot(Vector(term["dir"])) for i in range(3)]
            i = max(range(3), key=lambda j: abs(dots[j]))
            full = term["full"] * (k if term["kind"] == 'LOC' else 1.0) * (1.0 if dots[i] > 0 else -1.0)
            if term["kind"] == 'LOC':
                out[(pb.name, "location", i)] = full
            elif pb.rotation_mode == 'QUATERNION':
                axis = Vector((0, 0, 0))
                axis[i] = 1.0
                out[(pb.name, "rotation_quaternion", -1)] = tuple(Quaternion(axis, full))
            else:
                out[(pb.name, "rotation_euler", i)] = full
    return out


REST = {"location": 0.0, "rotation_euler": 0.0, "rotation_quaternion": (1.0, 0.0, 0.0, 0.0)}


def _set_channels(arm, channels, values):
    for (bone, path, idx) in channels:
        pb = arm.pose.bones[bone]
        v = values.get((bone, path, idx), REST[path])
        if idx < 0:
            setattr(pb, path, v)
        else:
            getattr(pb, path)[idx] = v


def _head_verts(mesh, arm, props):
    """Vertices riding the head / face bones (what a storage object shows)."""
    names = {b.name for b in arm.data.bones if b.use_deform and O.GEN_PROP in b}
    if props.head_bone:
        names.add(props.head_bone)
    gi = {g.index for g in mesh.vertex_groups if g.name in names}
    return [v.index for v in mesh.data.vertices if any(g.group in gi and g.weight > 0.0 for g in v.groups)]


def _armature_only(mesh):
    class Ctx:
        def __enter__(self_):
            self_.mods = [(m, m.show_viewport) for m in mesh.modifiers if m.type != 'ARMATURE']
            for m, _s in self_.mods:
                m.show_viewport = False

        def __exit__(self_, *exc):
            for m, s in self_.mods:
                m.show_viewport = s
    return Ctx()


def _save_pose(arm):
    return {pb.name: (pb.rotation_mode, tuple(pb.location), tuple(pb.rotation_quaternion),
                      tuple(pb.rotation_euler), tuple(pb.rotation_axis_angle), tuple(pb.scale))
            for pb in arm.pose.bones}


def _rest_pose(arm):
    for pb in arm.pose.bones:
        pb.location = (0.0, 0.0, 0.0)
        pb.rotation_quaternion = (1.0, 0.0, 0.0, 0.0)
        pb.rotation_euler = (0.0, 0.0, 0.0)
        pb.rotation_axis_angle = (0.0, 0.0, 1.0, 0.0)
        pb.scale = (1.0, 1.0, 1.0)


def _load_pose(arm, saved):
    for name, (mode, loc, quat, eul, aa, sc) in saved.items():
        pb = arm.pose.bones.get(name)
        if pb is None:
            continue
        pb.rotation_mode = mode
        pb.location, pb.rotation_quaternion, pb.rotation_euler = loc, quat, eul
        pb.rotation_axis_angle, pb.scale = aa, sc


def _bare_copy(mesh):
    """The face mesh data without shape keys / pose keys (copied once: taking
    the keys off a full copy for every edit mesh takes minutes)."""
    me = mesh.data.copy()
    me.name = "HB-SK_template"
    me.pose_keys.clear()
    for key in (KEYS_PROP, BASIS_PROP):
        if key in me:
            del me[key]
    tmp = bpy.data.objects.new("HB-SK_template", me)
    tmp.shape_key_clear()
    bpy.data.objects.remove(tmp)
    return me


def _drop_pose_action():
    """Delete the pose action. Every pose key pointing at it is unlinked first:
    a pose key left holding a deleted action crashes Blender on its next write."""
    act = bpy.data.actions.get(POSE_ACTION)
    if act is None:
        return
    for me in bpy.data.meshes:
        for pk in getattr(me, "pose_keys", []):
            if pk.action == act:
                pk.action = None
    for ob in bpy.data.objects:
        ad = ob.animation_data
        if ad is not None and ad.action == act:
            ad.action = None
    bpy.data.actions.remove(act)


def _one_sided(spec):
    """A sided key holding a one-sided shape (no MSK-face side mask)."""
    return spec["name"].endswith((".L", ".R")) and not (spec["mask"] or "").startswith("MSK-face.")


# ----------------------------------------------------------------------
# The pose action (the studio's "test" action): one pose every FRAME_STEP
# frames, grouped in sections, frame 0 = rest. It is the source of the poses:
# kept between runs (only missing poses are added), editable by hand (move a
# control at a pose's frame and key it), with a timeline marker per pose.
# ----------------------------------------------------------------------
FRAME_STEP = 10
SECTIONS = (("mouth", 0), ("mouth_open", 0), ("other", 0), ("custom", 0))   # (order only)
UNPOSED = ("brow", "cheek")         # made with the bone heads: no pose, rest frame 0
ACTION_VERSION = 2
CUSTOM_PROP = "hb_sk_custom"        # mesh object property: JSON list of custom keys
REST_SKIP = ("DEF", "MCH", "ORG", "DSP", "TGT", "WGT")


def _section(name):
    c = _cell(name)
    if c is None:
        return "other"
    if c[0] == "mouth" and c[1] >= 4:
        return "mouth_open"
    return c[0]


def _action_fcurves(act):
    try:
        return list(act.fcurves)
    except Exception:
        return [fc for l in act.layers for s in l.strips for cb in s.channelbags for fc in cb.fcurves]


def _rest_value(arm, fc):
    p = fc.data_path
    if p.endswith("scale"):
        return 1.0
    if p.endswith("rotation_quaternion"):
        return 1.0 if fc.array_index == 0 else 0.0
    if p.endswith("rotation_axis_angle"):
        return 1.0 if fc.array_index == 2 else 0.0
    if p.endswith(("location", "rotation_euler")):
        return 0.0
    return fc.evaluate(0)                            # custom property: its frame-0 value


def _key(fc, frame, value):
    """Set (or add) the key at frame. (keyframe_points.insert adds nothing on
    an F-curve made by fcurve_ensure_for_datablock in 5.x.)"""
    for p in fc.keyframe_points:
        if int(round(p.co[0])) == frame:
            p.co[1] = value
            p.handle_left[1] = p.handle_right[1] = value
            return
    fc.keyframe_points.add(1)
    p = fc.keyframe_points[-1]
    p.co = (frame, value)
    p.handle_left_type = p.handle_right_type = 'AUTO_CLAMPED'
    p.interpolation = 'BEZIER'


def _channel_fcurves(act, arm, chan):
    """F-curves of one channel (bone, path, index or -1 for the whole array)."""
    bone, path, idx = chan
    dp = f'pose.bones["{bone}"].{path}'
    n = len(getattr(arm.pose.bones[bone], path)) if idx < 0 else 1
    out = []
    for i in (range(n) if idx < 0 else (idx,)):
        fc = None
        for f in _action_fcurves(act):
            if f.data_path == dp and f.array_index == i:
                fc = f
                break
        if fc is None:
            fc = act.fcurve_ensure_for_datablock(arm, dp, index=i)
        out.append(fc)
    return out


def _write_pose(act, arm, frame, values):
    """Key {(bone, path, idx): value} at frame."""
    for chan, v in values.items():
        fcs = _channel_fcurves(act, arm, chan)
        vals = list(v) if chan[2] < 0 else [v]
        for fc, x in zip(fcs, vals):
            _key(fc, frame, x)


def _fill_rest(act, arm, frames):
    """Every channel keyed at every pose frame: rest where it was not keyed
    (a channel keyed for one pose would otherwise leak into its neighbours)."""
    for fc in _action_fcurves(act):
        have = {int(round(k.co[0])) for k in fc.keyframe_points}
        rest = _rest_value(arm, fc)
        for f in frames:
            if f not in have:
                _key(fc, f, rest)
        fc.update()


def _apply_frame(arm, act, frame):
    """Pose the rig as the action has it at frame (rest for what it doesn't key)."""
    _rest_pose(arm)
    for fc in _action_fcurves(act):
        dp = fc.data_path
        if not dp.startswith('pose.bones["'):
            continue
        bone, rest = dp[12:].split('"]', 1)
        pb = arm.pose.bones.get(bone)
        if pb is None:
            continue
        v = fc.evaluate(frame)
        if rest.startswith('["'):
            pb[rest[2:-2]] = v
        else:
            attr = rest[1:]
            cur = getattr(pb, attr, None)
            if cur is None:
                continue
            if hasattr(cur, "__len__"):
                cur[fc.array_index] = v
            else:
                setattr(pb, attr, v)


def _with_action(arm, act):
    """Context: the rig temporarily plays act (for keying / slots)."""
    class Ctx:
        def __enter__(self_):
            ad = arm.animation_data_create()
            self_.old = ad.action
            self_.slot = getattr(ad, "action_slot", None) if ad.action else None
            ad.action = act
            return getattr(ad, "action_slot", None)

        def __exit__(self_, *exc):
            ad = arm.animation_data
            ad.action = self_.old
            if self_.old is not None and self_.slot is not None:
                ad.action_slot = self_.slot
    return Ctx()


def _markers(context, names_frames, all_names):
    tl = context.scene.timeline_markers
    for m in [m for m in tl if m.name in all_names]:
        tl.remove(m)
    for name, frame in names_frames.items():
        tl.new(name, frame=frame)


# ----------------------------------------------------------------------
# Custom keys (correctives / extra shapes posed by hand, Add Pose Key)
# ----------------------------------------------------------------------
def custom_specs(mesh):
    raw = mesh.get(CUSTOM_PROP)
    if not raw:
        return []
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return []


def _save_custom(mesh, items):
    mesh[CUSTOM_PROP] = json.dumps(items)


def _moved_channels(arm, eps_loc=1e-4, eps_rot=1e-3):
    """[(bone, 'LOC'/'ROT', axis, value)] of the controls posed away from rest."""
    out = []
    for pb in arm.pose.bones:
        if pb.name.startswith(REST_SKIP):
            continue
        mb = pb.matrix_basis
        loc = mb.to_translation()
        rot = mb.to_euler('XYZ')
        for i in range(3):
            if abs(loc[i]) > eps_loc:
                out.append((pb.name, 'LOC', i, float(loc[i])))
            if abs(rot[i]) > eps_rot:
                out.append((pb.name, 'ROT', i, float(rot[i])))
    return out


def _pose_values(arm, moved):
    """Action channels for a pose from _moved_channels (whole transforms)."""
    vals = {}
    for bone in {m[0] for m in moved}:
        pb = arm.pose.bones[bone]
        vals[(bone, "location", -1)] = tuple(pb.location)
        if pb.rotation_mode == 'QUATERNION':
            vals[(bone, "rotation_quaternion", -1)] = tuple(pb.rotation_quaternion)
        elif pb.rotation_mode == 'AXIS_ANGLE':
            vals[(bone, "rotation_axis_angle", -1)] = tuple(pb.rotation_axis_angle)
        else:
            vals[(bone, "rotation_euler", -1)] = tuple(pb.rotation_euler)
    return vals


def _drive_custom(kb, arm, terms):
    try:
        kb.driver_remove("value")
    except (TypeError, RuntimeError):
        pass
    d = kb.driver_add("value").driver
    d.type = 'SCRIPTED'
    parts = []
    for i, t in enumerate(terms):
        var = f"v{i}"
        v = d.variables.new()
        v.name, v.type = var, 'TRANSFORMS'
        tg = v.targets[0]
        tg.id, tg.bone_target = arm, t["bone"]
        tg.transform_type = f"{t['kind']}_{AXES[t['axis']]}"
        tg.transform_space = 'TRANSFORM_SPACE'           # the raw channel: what was posed
        full = t["value"]
        parts.append(f"({'max' if full > 0 else 'min'}(0, {var})/{full:.5f})")
    d.expression = " * ".join(parts) if parts else "0"


def add_pose_key(context, arm, props, name, pair=True):
    """A new key (or .L / .R pair) for the pose the rig holds now: driver from
    the moved controls (product of clamped terms, like the studio file), its
    pose keyed in the pose action, a pose key and an edit mesh. Returns notes."""
    mesh = props.mesh
    moved = _moved_channels(arm)
    if not moved:
        raise ValueError("먼저 컨트롤을 포즈하세요 (레스트에서 움직인 것이 없음)")
    base = name if name.startswith("SK-") else f"SK-{name}"
    base = _base(base)
    items = [c for c in custom_specs(mesh) if c["base"] != base]
    if any(n == base or _base(n) == base for n in mesh.get(KEYS_PROP, [])) and not any(
            c["base"] == base for c in custom_specs(mesh)):
        raise ValueError(f"{base}은(는) 이미 이 도구의 키입니다")
    terms = [{"bone": b, "kind": k, "axis": a, "value": v} for b, k, a, v in moved]
    if pair:
        keys = {base + ".L": [t for t in terms if not t["bone"].endswith((".R", "_R"))],
                base + ".R": [t for t in terms if not t["bone"].endswith((".L", "_L"))]}
        if not keys[base + ".L"] or not keys[base + ".R"]:
            raise ValueError("쌍으로 만들려면 양쪽 모두 포즈해야 합니다 (또는 단일 키로 만드세요)")
    else:
        keys = {base: terms}
    # frame: the current one if free, else the next free custom frame
    act = bpy.data.actions.get(POSE_ACTION)
    used = {pk.frame for pk in getattr(mesh.data, "pose_keys", []) if act and pk.action == act}
    used |= {c["frame"] for c in items}
    frame = context.scene.frame_current
    if frame <= 0 or frame in used:
        frame = max(used | {0}) + 2 * FRAME_STEP      # (packed into place by setup)
    # (the shape keys and their drivers are made last, by register_keys)
    items.append({"base": base, "names": list(keys), "terms": terms, "pair": pair,
                  "key_terms": keys,
                  "pose": [[b, p, i, v] for (b, p, i), v in _pose_values(arm, moved).items()],
                  "frame": frame})
    _save_custom(mesh, items)
    mesh[KEYS_PROP] = sorted(set(mesh.get(KEYS_PROP, [])) | set(keys))
    return setup_pose_keys(context, arm, props)


def remove_custom(mesh, base):
    _save_custom(mesh, [c for c in custom_specs(mesh) if c["base"] != base])


# ----------------------------------------------------------------------
# Pose keys + edit meshes
# ----------------------------------------------------------------------
def _storm_groups(mesh, arm):
    """{base: [key names]} of the storm keys this face works on. The face needs
    no shape keys for this: the keys are made last (register_keys), after the
    edit meshes were sculpted. Without a list on the face: every buildable key."""
    by_name = {s["name"]: s for s in specs(arm)[0]}
    mine = [n for n in mesh.get(KEYS_PROP, []) if n in by_name] or list(by_name)
    groups = {}
    for n in mine:                                   # storm's order, .L / .R together
        groups.setdefault(_base(n), []).append(n)
    return groups, by_name


def _assign_frames(groups, sections, taken):
    """{base: frame} for groups without one: after everything (_pack then puts
    them in their section)."""
    out = {}
    f = max(taken | {0}) + FRAME_STEP
    for b in groups:
        out[b] = f
        taken.add(f)
        f += FRAME_STEP
    return out


def _pack(act, frames, sections):
    """Poses back to back, FRAME_STEP apart, sections in SECTIONS order with one
    empty step between them (the studio file: mouth 10-70, open 90-150, ...).
    Moves their keys in the action; returns the new {base: frame}."""
    order = [s for s, _f in SECTIONS]
    seq = sorted(frames, key=lambda b: (order.index(sections[b]), frames[b], b))
    new, f, prev = {}, FRAME_STEP, None
    for b in seq:
        if prev is not None and sections[b] != prev:
            f += FRAME_STEP
        new[b] = f
        prev = sections[b]
        f += FRAME_STEP
    remap = {frames[b]: new[b] for b in frames if frames[b] != new[b]}
    if remap:
        for fc in _action_fcurves(act):
            for p in fc.keyframe_points:
                f0 = int(round(p.co[0]))
                if f0 in remap:
                    dx = remap[f0] - p.co[0]
                    p.co[0] += dx
                    p.handle_left[0] += dx
                    p.handle_right[0] += dx
            fc.update()
    return new


def setup_pose_keys(context, arm, props, refresh=(), recreate=()):
    """Pose keys + pose action + storage objects for the tool's shape keys.
    The pose action and the storage objects already there are kept (only
    missing poses / edit meshes are added). refresh: pose keys whose storage
    Morph Target is captured again from their frame. recreate: pose keys
    whose deleted edit mesh is made again. Returns (pose keys, new storage, notes)."""
    import numpy as np
    mesh = props.mesh
    sk = mesh.data.shape_keys
    groups, by_name = _storm_groups(mesh, arm)
    custom = {c["base"]: c for c in custom_specs(mesh)}
    for b, c in custom.items():
        groups[b] = list(c["names"])
    if not groups:
        return 0, 0, ["이 도구로 만든 쉐이프키가 없습니다 (먼저 쉐이프키를 누르세요)"]
    k = _R()._eye_dist(arm, 1.0)
    lone = {n: _one_sided(by_name[n]) for b, names in groups.items() if b not in custom
            for n in names}
    pks = mesh.data.pose_keys

    # --- pose action: kept; an old one (consecutive frames) is rebuilt once
    act = bpy.data.actions.get(POSE_ACTION)
    if act is not None and act.get("hb_version") != ACTION_VERSION:
        _drop_pose_action()
        act = None
    fresh = act is None
    if fresh:
        act = bpy.data.actions.new(POSE_ACTION)
        act.use_fake_user = True
        act["hb_version"] = ACTION_VERSION
    sections = {b: ("custom" if b in custom else _section(b)) for b in groups}
    # brow / cheek are made with the bone heads (skbones), not posed: their pose
    # keys sit at the rest frame 0 and their edit meshes show the key on the
    # rest face; only mouth and correctives get poses in the action
    unposed = {b for b in groups if sections[b] in UNPOSED}
    frames, stale = {}, set()
    for pk in pks:
        if pk.name in groups and pk.action == act and pk.frame > 0:
            if pk.name in unposed:
                stale.add(int(pk.frame))
            else:
                frames[pk.name] = int(pk.frame)
    posed = set(frames)                              # poses already in the action
    for b, c in custom.items():
        frames.setdefault(b, int(c["frame"]))
    missing = [b for b in groups if b not in frames and b not in unposed]
    frames.update(_assign_frames(missing, sections, set(frames.values())))
    stale -= set(frames.values())
    with _with_action(arm, act) as slot:
        if stale:                                    # brow / cheek poses of an older run
            for fc in _action_fcurves(act):
                for p in reversed(list(fc.keyframe_points)):
                    if int(round(p.co[0])) in stale:
                        fc.keyframe_points.remove(p)
        for b in [b for b in groups if b not in posed and b not in unposed]:
            if b in custom:
                vals = {(bn, p, i): (tuple(v) if i < 0 else v) for bn, p, i, v in custom[b]["pose"]}
            else:
                specs_b = [by_name[n] for n in groups[b] if not (lone[n] and n.endswith(".R"))]
                vals = _pose_channels(arm, specs_b, k)
            _write_pose(act, arm, frames[b], vals)
        frames = _pack(act, frames, sections)
        _fill_rest(act, arm, [0] + sorted(frames.values()))
    for b in unposed:
        frames[b] = 0
    for c in custom.values():
        c["frame"] = frames[c["base"]]
    if custom:
        _save_custom(mesh, [custom[c["base"]] if c["base"] in custom else c
                            for c in custom_specs(mesh)])
    _markers(context, {b: frames[b] for b in groups if b not in unposed}, set(groups))

    # --- storage objects (the posed face, Armature modifier only)
    coll = bpy.data.collections.get(STORAGE_COLL)
    if coll is None:
        coll = bpy.data.collections.new(STORAGE_COLL)
        context.scene.collection.children.link(coll)
    head = _head_verts(mesh, arm, props)
    head_set = set(head)
    n = len(mesh.data.vertices)
    base_co = np.empty(n * 3)
    (sk.reference_key.data if sk else mesh.data.vertices).foreach_get("co", base_co)
    # Add the missing pose keys first, then look every one up again by index:
    # adding to the collection moves its items, and writing through a pose key
    # fetched before an add crashes Blender.
    have = {pk.name for pk in pks}
    had_storage = any(o.get(O.OBJ_PROP) == STORAGE_TAG for o in bpy.data.objects)
    new_objs = []
    for b, names in groups.items():
        if b in have:
            continue
        pk = pks.add()
        pk.name = b
        for nm in names:
            t = pk.target_shapes.add()
            t.shape_key_name = nm                      # (its update also sets .name)
    index = {pk.name: i for i, pk in enumerate(pks)}
    for b, names in groups.items():
        for t in pks[index[b]].target_shapes:
            if t.name in lone:
                t.mirror_x = lone[t.name] and t.name.endswith(".R")
    made, notes = 0, []
    template = None
    # capture from the action's frame on top of the rest pose (as the add-on's
    # Set Pose does): the user may be posing other controls right now
    full = _save_pose(arm)
    basis = base_co.reshape(-1, 3)
    # the rig may be playing an action (Set Pose assigns the pose action): it
    # would pose the rig over what is set here on every update
    ad = arm.animation_data
    held = (ad.action, getattr(ad, "action_slot", None)) if ad and ad.action else None
    if held:
        ad.action = None

    def key_co(b):
        """brow / cheek: the key (its first non-mirrored target) on the face, or None."""
        t = next((t.name for t in pks[index[b]].target_shapes if not t.mirror_x), None)
        kb = sk.key_blocks.get(t) if sk and t else None
        if kb is None:
            return None
        c = np.empty(len(basis) * 3)
        kb.data.foreach_get("co", c)
        return c.reshape(-1, 3)

    def capture(b):
        """The keyframed pose on the face before any shape key (the rig alone):
        the edit mesh is sculpted from it, and the keys are made from the edit
        meshes last. brow / cheek: the rest face plus the key, if the face has it."""
        _apply_frame(arm, act, frames[b])
        if sk is not None:
            only, active = mesh.show_only_shape_key, mesh.active_shape_key_index
            mesh.show_only_shape_key, mesh.active_shape_key_index = True, 0    # basis only
        try:
            out = _eval(context, mesh)
        finally:
            if sk is not None:
                mesh.show_only_shape_key, mesh.active_shape_key_index = only, active
        if b in unposed:
            c = key_co(b)
            if c is not None:
                out = out + (c - basis)
        return out

    with _armature_only(mesh):
        for b, names in groups.items():
            pk = pks[index[b]]
            pk.action = act
            if slot is not None:
                pk.action_slot = slot
            pk.frame = frames[b]
            if pk.storage_object is not None:
                if b in refresh and not (b in unposed and key_co(b) is None):
                    # the pose changed: show it again (a brow / cheek mesh without
                    # its key on the face is the hand-made stage: kept)
                    keys = pk.storage_object.data.shape_keys
                    tgt = keys.key_blocks.get("Morph Target") if keys else None
                    if tgt is not None:
                        over = pk.storage_object.get(OVER_KEYS) and sk is not None
                        co = _over_keys_co(context, arm, mesh, pk, act) if over else capture(b)
                        tgt.data.foreach_set("co", co.reshape(-1))
                        pk.storage_object.data.update()
                continue
            if b in have and b not in recreate:        # its edit mesh was deleted by hand: leave it so
                continue
            posed = capture(b)
            if template is None:
                template = _bare_copy(mesh)
            me = template.copy()
            me.name = f"{mesh.name}-{b}"
            obj = bpy.data.objects.new(me.name, me)
            coll.objects.link(obj)
            obj.vertex_groups.clear()
            obj.use_shape_key_edit_mode = True
            obj.shape_key_add(name="Basis")
            target = obj.shape_key_add(name="Morph Target")
            target.data.foreach_set("co", posed.reshape(-1))
            target.value = 1.0
            adjust = obj.shape_key_add(name="New Changes", from_mix=False)   # empty layer to sculpt on
            adjust.value = 1.0
            obj.active_shape_key_index = 2
            # only the head: Mask modifier in Object Mode, hidden faces in Sculpt / Edit Mode
            obj.vertex_groups.new(name=HEAD_GROUP).add(head, 1.0, 'REPLACE')
            mk = obj.modifiers.new("Head Only", 'MASK')
            mk.vertex_group = HEAD_GROUP
            for p in me.polygons:
                if not all(v in head_set for v in p.vertices):
                    p.hide = True
            for m in mesh.modifiers:
                if m.type == 'SUBSURF':
                    s = obj.modifiers.new(m.name, 'SUBSURF')
                    s.levels, s.render_levels = m.levels, m.render_levels
            obj[O.OBJ_PROP] = STORAGE_TAG
            pk.storage_object = obj
            # name tag under the chin (the object's origin is at the feet)
            hc = base_co.reshape(-1, 3)[head]
            lbl = bpy.data.objects.new(b[3:] if b.startswith("SK-") else b, None)
            lbl.empty_display_type = 'PLAIN_AXES'
            lbl.empty_display_size = 0.001
            lbl.show_name = not getattr(props, "show_sk_overlay", False)
            lbl.hide_select = True
            lbl[O.OBJ_PROP] = LABEL_TAG
            coll.objects.link(lbl)
            lbl.parent = obj
            lbl.location = (float(hc[:, 0].min()), float(hc[:, 1].min()), float(hc[:, 2].min()))
            made += 1
            new_objs.append(obj)
    if held:
        ad.action = held[0]
        if held[1] is not None:
            ad.action_slot = held[1]
    _load_pose(arm, full)
    if template is not None:
        bpy.data.meshes.remove(template)
    mesh.data.update()
    context.view_layer.update()
    if new_objs and had_storage:
        _place_new(mesh, new_objs, base_co.reshape(-1, 3), head)   # the layout may be hand-made
    elif new_objs:
        arrange(context, props, base_co.reshape(-1, 3), head)
    return len(groups), made, notes


def _place_new(mesh, objs, base_co, head):
    """New edit meshes in a row above the CORRECTIVES block (the others stay put)."""
    hc = base_co[head] if head else base_co
    dx, dz = (hc[:, 0].max() - hc[:, 0].min()) * 1.25, (hc[:, 2].max() - hc[:, 2].min()) * 1.3
    new = set(objs)
    others = [pk.storage_object for pk in mesh.data.pose_keys
              if pk.storage_object is not None and pk.storage_object not in new
              and _cell(pk.name) is None and not is_brow_corr(pk.name)]
    if not others:
        others = [o for o in bpy.data.objects if o.get(O.OBJ_PROP) == STORAGE_TAG and o not in new]
    x0 = min(o.location.x for o in others)
    z = max(o.location.z for o in others) + dz
    taken = {(round(o.location.x / dx), round(o.location.z / dz)) for o in others}
    i = 0
    for o in objs:
        while (round((x0 + i * dx) / dx), round(z / dz)) in taken:
            i += 1
        o.location = Vector((x0 + i * dx, others[0].location.y, z))
        i += 1


def empty_keys(context, arm, props, names):
    """Empty these pose keys' target shape keys (back to the basis: nothing
    pre-applied) and capture their edit meshes again from the pose alone, to
    sculpt from scratch. Unpushed sculpts (New Changes) are kept."""
    import numpy as np
    mesh = props.mesh
    sk = mesh.data.shape_keys
    done = []
    if sk is None:                               # nothing registered yet: just recapture
        setup_pose_keys(context, arm, props, refresh=set(names))
        return done
    basis = np.empty(len(mesh.data.vertices) * 3)
    sk.reference_key.data.foreach_get("co", basis)
    for pk in mesh.data.pose_keys:
        if pk.name not in names:
            continue
        for t in pk.target_shapes:
            kb = sk.key_blocks.get(t.name)
            if kb is not None:
                kb.data.foreach_set("co", basis)
                done.append(kb.name)
    mesh.data.update()
    setup_pose_keys(context, arm, props, refresh=set(names))
    return done


def refresh_from_frame(context, arm, props, names):
    """Capture these pose keys' Morph Target again from their frame in the pose
    action (after the pose was edited there)."""
    return setup_pose_keys(context, arm, props, refresh=set(names))


# ----------------------------------------------------------------------
# Correctives over the registered keys: once the simple keys are on the face,
# a corrective's edit mesh is taken again from the face with every key on (its
# own off), sculpted there, and only the correctives are registered again.
# ----------------------------------------------------------------------
OVER_KEYS = "hb_sk_over_keys"       # edit mesh prop: its Morph Target has the face keys on


def corrective_names(mesh):
    """Pose keys laid over the other keys: the 'other' block and the custom ones."""
    custom = {c["base"] for c in custom_specs(mesh)}
    return [pk.name for pk in mesh.data.pose_keys
            if pk.name in custom or _section(pk.name) == "other"]


def _over_keys_co(context, arm, mesh, pk, act):
    """The face at the pose key's pose: the rig and every face key (drivers on),
    the pose key's own keys off. Call with the rig's action held off."""
    if act is not None and pk.action == act:
        _apply_frame(arm, act, int(pk.frame))
    else:
        _rest_pose(arm)
    kbs = mesh.data.shape_keys.key_blocks
    own = [kbs[t.name] for t in pk.target_shapes if t.name in kbs]
    muted = [kb.mute for kb in own]
    only = mesh.show_only_shape_key
    mesh.show_only_shape_key = False
    for kb in own:
        kb.mute = True
    try:
        return _eval(context, mesh)
    finally:
        for kb, m in zip(own, muted):
            kb.mute = m
        mesh.show_only_shape_key = only


def rebase_correctives(context, arm, props, names):
    """These correctives' edit meshes start from the face with the keys on:
    Morph Target = that face at the pose (their own keys off); New Changes,
    the sculpt layer, is kept as it is on top. Returns (names done, notes)."""
    import numpy as np
    mesh = props.mesh
    if mesh.data.shape_keys is None:
        raise ValueError("얼굴에 쉐이프키가 없습니다 (먼저 얼굴에 등록하세요)")
    n = len(mesh.data.vertices)
    act = bpy.data.actions.get(POSE_ACTION)
    full = _save_pose(arm)
    ad = arm.animation_data
    held = (ad.action, getattr(ad, "action_slot", None)) if ad and ad.action else None
    if held:
        ad.action = None
    done, notes, kept = [], [], []
    try:
        with _armature_only(mesh):
            for pk in mesh.data.pose_keys:
                o = pk.storage_object
                if pk.name not in names or o is None:
                    continue
                keys = o.data.shape_keys
                kb = keys.key_blocks if keys else {}
                if "Morph Target" not in kb or "New Changes" not in kb or len(o.data.vertices) != n:
                    notes.append(f"{pk.name}: Morph Target / New Changes 없음")
                    continue
                if not o.get(OVER_KEYS):
                    nc = kb["New Changes"]
                    c, r = np.empty(n * 3), np.empty(n * 3)
                    nc.data.foreach_get("co", c)
                    nc.relative_key.data.foreach_get("co", r)
                    if float(np.abs(c - r).max()) > 1e-5:
                        kept.append(pk.name)
                base = _over_keys_co(context, arm, mesh, pk, act)
                mt = kb["Morph Target"]
                mt.relative_key = keys.reference_key
                mt.vertex_group = ""
                mt.mute, mt.value = False, 1.0
                mt.data.foreach_set("co", base.reshape(-1))
                o[OVER_KEYS] = True
                o.data.update()
                done.append(pk.name)
    finally:
        if held:
            ad.action = held[0]
            if held[1] is not None:
                ad.action_slot = held[1]
        _load_pose(arm, full)
        mesh.data.update()
        context.view_layer.update()
    if kept:
        notes.append(f"기존 스컬프트(New Changes) 유지: {', '.join(n[3:] for n in kept[:4])}"
                     + (" 외" if len(kept) > 4 else "")
                     + " - 리그 포즈 기준으로 잡은 모양이면 키 위에 겹치니 확인하세요")
    return done, notes


# ----------------------------------------------------------------------
# Combos: keys driven by two or more controls (x * y, x * jaw open). Their
# edit meshes can be taken again over the keys like the correctives (the
# combo then starts from its base keys added up, as the studio did).
# ----------------------------------------------------------------------
def _key_terms():
    """{key name: terms} of storm's keys."""
    return {k["name"]: k["terms"] for k in data()["keys"]}


def _pose_terms(pk, terms=None):
    """The terms of a pose key's first key that has storm terms (else [])."""
    terms = terms if terms is not None else _key_terms()
    for t in pk.target_shapes:
        if terms.get(t.name):
            return terms[t.name]
    return []


def combo_names(mesh):
    """Storm pose keys whose key reads two or more channels, but not correctives."""
    corr = set(corrective_names(mesh))
    terms = _key_terms()
    return [pk.name for pk in mesh.data.pose_keys
            if pk.name not in corr and len(_pose_terms(pk, terms)) > 1]


def rebase_names(mesh):
    """Pose keys that can be taken again over the face keys."""
    corr = corrective_names(mesh)
    return corr + [n for n in combo_names(mesh) if n not in corr]


def _same_term(a, b):
    return (a["bone"] == b["bone"] and a["kind"] == b["kind"]
            and Vector(a["dir"]).normalized().dot(Vector(b["dir"]).normalized()) > 0.99)


def combo_parents(mesh):
    """{pose key: [pose keys of one channel it is made of]}: the base keys a
    combo / corrective is laid on (x_neg, y_neg -> yx_neg)."""
    terms = _key_terms()
    pks = list(mesh.data.pose_keys)
    single = [(pk.name, _pose_terms(pk, terms)[0]) for pk in pks if len(_pose_terms(pk, terms)) == 1]
    out = {}
    for pk in pks:
        tt = _pose_terms(pk, terms)
        if len(tt) < 2:
            continue
        par = []
        for t in tt:
            for name, s in single:
                if _same_term(t, s) and name not in par and name != pk.name:
                    par.append(name)
                    break
        if par:
            out[pk.name] = par
    return out


REG_HASH = "hb_sk_reg_hash"         # edit mesh prop: hash of the shape last registered


def co_hash(co):
    import hashlib
    import numpy as np
    return hashlib.md5(np.round(np.asarray(co, dtype=np.float64), 5).astype(np.float32).tobytes()).hexdigest()


def _rig_only_co(context, arm, mesh, pk, act):
    """The face at the pose key's pose with the rig alone (no shape key).
    Call with the rig's action held off, inside _armature_only."""
    if act is not None and pk.action == act:
        _apply_frame(arm, act, int(pk.frame))
    else:
        _rest_pose(arm)
    sk = mesh.data.shape_keys
    if sk is None:
        return _eval(context, mesh)
    only, active = mesh.show_only_shape_key, mesh.active_shape_key_index
    mesh.show_only_shape_key, mesh.active_shape_key_index = True, 0       # basis only
    try:
        return _eval(context, mesh)
    finally:
        mesh.show_only_shape_key, mesh.active_shape_key_index = only, active


MIX_KEY = "Mix"
MIX_PROP = "hb_sk_mix"              # edit mesh prop: JSON [[pose key, weight], ...]


def mix_edit_meshes(context, arm, props, target, sources):
    """The target edit mesh starts from other edit meshes mixed: its 'Mix'
    key is set so that the mesh = the rig at its pose + sum(weight * (source
    sculpt - the rig at the source's pose)) + its New Changes (kept).
    sources: [(pose key name, weight)]. Returns notes."""
    import numpy as np
    mesh = props.mesh
    pks = {pk.name: pk for pk in mesh.data.pose_keys}
    tpk = pks.get(target)
    if tpk is None or tpk.storage_object is None:
        raise ValueError(f"{target}: 편집 메쉬 없음")
    obj = tpk.storage_object
    n = len(mesh.data.vertices)
    srcs = [(s, w) for s, w in sources if s != target and abs(w) > 1e-6]
    if not srcs:
        raise ValueError("섞을 편집 메쉬를 하나 이상 고르세요 (비율 0 제외, 자기 자신 제외)")
    for s, _w in srcs:
        o = pks[s].storage_object if s in pks else None
        if o is None or len(o.data.vertices) != n:
            raise ValueError(f"{s}: 편집 메쉬 없음")
    keys = obj.data.shape_keys
    if keys is None or "Morph Target" not in keys.key_blocks or len(obj.data.vertices) != n:
        raise ValueError(f"{target}: Morph Target 없음")
    act = bpy.data.actions.get(POSE_ACTION)
    full = _save_pose(arm)
    ad = arm.animation_data
    held = (ad.action, getattr(ad, "action_slot", None)) if ad and ad.action else None
    if held:
        ad.action = None
    try:
        with _armature_only(mesh):
            total = _rig_only_co(context, arm, mesh, tpk, act).copy()
            for s, w in srcs:
                total += w * (_sculpted_co(pks[s].storage_object)
                              - _rig_only_co(context, arm, mesh, pks[s], act))
    finally:
        if held:
            ad.action = held[0]
            if held[1] is not None:
                ad.action_slot = held[1]
        _load_pose(arm, full)
        mesh.data.update()
        context.view_layer.update()
    kept = _set_layer(obj, MIX_KEY, total, keep_sculpt=True)
    obj[MIX_PROP] = json.dumps([[s, float(w)] for s, w in srcs])
    return ["New Changes(기존 스컬프트)는 그대로 위에 남음"] if kept else []


def _nc_delta(obj):
    """The New Changes layer's offset (value and mask applied), or None."""
    import numpy as np
    keys = obj.data.shape_keys
    nc = keys.key_blocks.get("New Changes") if keys else None
    if nc is None or nc.mute or nc.value == 0.0:
        return None
    n = len(obj.data.vertices)
    c, r = np.empty(n * 3), np.empty(n * 3)
    nc.data.foreach_get("co", c)
    nc.relative_key.data.foreach_get("co", r)
    d = (c - r).reshape(-1, 3) * nc.value
    if float(np.abs(d).max()) <= 1e-5:
        return None
    if nc.vertex_group and nc.vertex_group in obj.vertex_groups:
        w = np.zeros(n)
        gi = obj.vertex_groups[nc.vertex_group].index
        for v in obj.data.vertices:
            for e in v.groups:
                if e.group == gi:
                    w[v.index] = e.weight
        d *= w[:, None]
    return d


def _set_layer(obj, name, target, keep_sculpt):
    """Set the edit mesh's shape key `name` (relative to the basis, value 1)
    so the mesh becomes `target`, plus its New Changes when keep_sculpt (the
    sculpt stays on top; else the layer offsets it). Returns True if a New
    Changes sculpt was there."""
    import numpy as np
    keys = obj.data.shape_keys
    kbs = keys.key_blocks
    n = len(obj.data.vertices)
    ref = np.empty(n * 3)
    keys.reference_key.data.foreach_get("co", ref)
    ref = ref.reshape(-1, 3)
    kb = kbs.get(name)
    if kb is None:
        kb = obj.shape_key_add(name=name, from_mix=False)
    kb.relative_key = keys.reference_key
    kb.vertex_group = ""
    kb.mute, kb.value = False, 1.0
    kb.data.foreach_set("co", ref.reshape(-1))          # (off while the rest is summed)
    others = _sculpted_co(obj)                          # Morph Target + New Changes + ...
    nc = _nc_delta(obj)
    if keep_sculpt and nc is not None:
        others = others - nc
    kb.data.foreach_set("co", (ref + target - others).reshape(-1))
    order = [k.name for k in kbs]                       # the sculpt layer stays active
    if "New Changes" in order:
        obj.active_shape_key_index = order.index("New Changes")
    obj.data.update()
    return nc is not None


# ----------------------------------------------------------------------
# Brow work mesh (studio: GEO-storm-head_brows_x_neg.001): both brows on one
# symmetric mesh with the all y- / all x- shapes as keys, so the shapes (and
# the furrow between the brows) are sculpted on both sides at once; each key
# goes back to its edit mesh (the left half: the .L key, the .R one mirrors).
# ----------------------------------------------------------------------
WORK_TAG = "SK_WORK"
WORK_NAME = "Brows_x_y_neg"
WORK_KEYS = (("SK-Brow_all_y_neg", "Brow_all_y_neg"), ("SK-Brow_all_x_neg", "Brow_all_x_neg"))
WORK_LAYER = "Work"
# Brow rotation keys started from the in / mid y keys (least squares on storm's
# final keys: rz is mostly the inner brow tilting, the outer one hardly moves;
# 88% of the shape, the rest is sculpted on top)
BROW_RZ_RECIPES = {
    "SK-Brow_all_rz_neg": [("SK-Brow_in_y_neg", 0.84), ("SK-Brow_in_y_pos", -0.11),
                           ("SK-Brow_mid_y_pos", -0.11), ("SK-Brow_mid_y_neg", -0.04)],
    "SK-Brow_all_rz_pos": [("SK-Brow_in_y_pos", 0.59), ("SK-Brow_mid_y_neg", 0.15),
                           ("SK-Brow_in_y_neg", -0.08), ("SK-Brow_mid_y_pos", -0.04)],
}
BROW_CORR = ("Furrow_Ty_low_corr", "Brows_Ty_low_corr", "Brows_rx_low_corr", "Brow_Cheek_combo")


def is_brow_corr(name):
    return (name[3:] if name.startswith("SK-") else name) in BROW_CORR


def work_objects():
    return [o for o in bpy.data.objects if o.get(O.OBJ_PROP) == WORK_TAG]


def _symmetric(mesh, d, mir=None):
    """d made mirror-symmetric from its left side: the left mask's part of d
    plus that part mirrored to the right (as the .L key + its mirrored .R give)."""
    import numpy as np
    n = len(d)
    g = mesh.vertex_groups.get(MASKS[0])
    wl = np.zeros(n)
    if g is not None:
        for v in mesh.data.vertices:
            for e in v.groups:
                if e.group == g.index:
                    wl[v.index] = e.weight
    else:
        sk = mesh.data.shape_keys
        co = np.empty(n * 3)
        (sk.reference_key.data if sk else mesh.data.vertices).foreach_get("co", co)
        x = co.reshape(-1, 3)[:, 0]
        wl = np.where(x > 1e-6, 1.0, np.where(x < -1e-6, 0.0, 0.5))
    dl = d * wl[:, None]
    out = dl.copy()
    mir = mir if mir is not None else mirror_map(mesh)
    if mir:
        i = np.fromiter(mir.keys(), dtype=np.int64)
        j = np.fromiter(mir.values(), dtype=np.int64)
        m = dl[j].copy()
        m[:, 0] *= -1.0
        out[i] += m
    return out


def _pose_shape(context, arm, mesh, pk, act):
    """A pose key's shape over the rig alone: its edit mesh's sculpt minus the
    rig at its pose, else its face key (.L) minus the basis."""
    import numpy as np
    o = pk.storage_object
    if o is not None and len(o.data.vertices) == len(mesh.data.vertices):
        return _sculpted_co(o) - _rig_only_co(context, arm, mesh, pk, act)
    sk = mesh.data.shape_keys
    t = next((t.name for t in pk.target_shapes if not t.mirror_x), None)
    if sk is None or t not in sk.key_blocks:
        return None
    n = len(mesh.data.vertices)
    c, b = np.empty(n * 3), np.empty(n * 3)
    sk.key_blocks[t].data.foreach_get("co", c)
    sk.reference_key.data.foreach_get("co", b)
    return (c - b).reshape(-1, 3)


def _held(arm):
    ad = arm.animation_data
    held = (ad.action, getattr(ad, "action_slot", None)) if ad and ad.action else None
    if held:
        ad.action = None
    return held


def _restore(arm, held):
    if held:
        ad = arm.animation_data
        ad.action = held[0]
        if held[1] is not None:
            ad.action_slot = held[1]


def build_brow_work(context, arm, props):
    """The brow work mesh, its keys taken from the brow edit meshes (else the
    face keys), made symmetric. An existing one is kept. Returns (obj, notes)."""
    import numpy as np
    mesh = props.mesh
    have = work_objects()
    if have:
        return have[0], ["이미 있음: 그대로 둠"]
    pks = {pk.name: pk for pk in mesh.data.pose_keys}
    act = bpy.data.actions.get(POSE_ACTION)
    full = _save_pose(arm)
    held = _held(arm)
    shapes, notes = {}, []
    try:
        with _armature_only(mesh):
            for pname, kname in WORK_KEYS:
                d = _pose_shape(context, arm, mesh, pks[pname], act) if pname in pks else None
                if d is None:
                    notes.append(f"{pretty_name(pname)}: 모양 없음 (빈 키)")
                shapes[kname] = d
    finally:
        _restore(arm, held)
        _load_pose(arm, full)
        mesh.data.update()
        context.view_layer.update()
    sk = mesh.data.shape_keys
    n = len(mesh.data.vertices)
    basis = np.empty(n * 3)
    (sk.reference_key.data if sk else mesh.data.vertices).foreach_get("co", basis)
    basis = basis.reshape(-1, 3)
    mir = mirror_map(mesh)
    me = _bare_copy(mesh)
    me.name = f"{mesh.name}-{WORK_NAME}"
    me.vertices.foreach_set("co", basis.reshape(-1))
    obj = bpy.data.objects.new(me.name, me)
    coll = bpy.data.collections.get(STORAGE_COLL)
    if coll is None:
        coll = bpy.data.collections.new(STORAGE_COLL)
        context.scene.collection.children.link(coll)
    coll.objects.link(obj)
    obj.vertex_groups.clear()
    obj.use_shape_key_edit_mode = True
    obj.shape_key_add(name="Basis")
    for _p, kname in WORK_KEYS:
        kb = obj.shape_key_add(name=kname, from_mix=False)
        d = shapes.get(kname)
        if d is not None:
            kb.data.foreach_set("co", (basis + _symmetric(mesh, d, mir)).reshape(-1))
        kb.value = 1.0
    obj.active_shape_key_index = 1
    head = _head_verts(mesh, arm, props)
    head_set = set(head)
    obj.vertex_groups.new(name=HEAD_GROUP).add(head, 1.0, 'REPLACE')
    mk = obj.modifiers.new("Head Only", 'MASK')
    mk.vertex_group = HEAD_GROUP
    for p in me.polygons:
        if not all(v in head_set for v in p.vertices):
            p.hide = True
    for m in mesh.modifiers:
        if m.type == 'SUBSURF':
            s = obj.modifiers.new(m.name, 'SUBSURF')
            s.levels, s.render_levels = m.levels, m.render_levels
    me.use_mirror_x = True                    # edit / sculpt mirrored by default
    obj[O.OBJ_PROP] = WORK_TAG
    obj.location = mesh.location
    place_brow_correctives(context, props)
    return obj, notes


def push_brow_work(context, arm, props, register=False):
    """Each work key (made symmetric again) becomes its brow edit mesh's shape
    (a 'Work' layer: the edit mesh = the rig + the key's shape). register:
    then register those keys on the face. Returns (pose keys done, notes)."""
    import numpy as np
    mesh = props.mesh
    objs = work_objects()
    if not objs:
        raise ValueError("눈썹 작업 메쉬가 없습니다")
    w = objs[0]
    wk = w.data.shape_keys
    if wk is None or len(w.data.vertices) != len(mesh.data.vertices):
        raise ValueError("작업 메쉬의 버텍스 수가 얼굴과 다릅니다")
    pks = {pk.name: pk for pk in mesh.data.pose_keys}
    n = len(mesh.data.vertices)
    base = np.empty(n * 3)
    wk.reference_key.data.foreach_get("co", base)
    base = base.reshape(-1, 3)
    mir = mirror_map(mesh)
    act = bpy.data.actions.get(POSE_ACTION)
    done, notes = [], []
    targets = {}
    full = _save_pose(arm)
    held = _held(arm)
    try:
        with _armature_only(mesh):
            for pname, kname in WORK_KEYS:
                kb = wk.key_blocks.get(kname)
                pk = pks.get(pname)
                if kb is None or pk is None or pk.storage_object is None:
                    notes.append(f"{pretty_name(pname)}: 키 또는 편집 메쉬 없음, 건너뜀")
                    continue
                c = np.empty(n * 3)
                kb.data.foreach_get("co", c)
                d = _symmetric(mesh, c.reshape(-1, 3) - base, mir)
                targets[pname] = _rig_only_co(context, arm, mesh, pk, act) + d
    finally:
        _restore(arm, held)
        _load_pose(arm, full)
        mesh.data.update()
        context.view_layer.update()
    for pname, target in targets.items():
        _set_layer(pks[pname].storage_object, WORK_LAYER, target, keep_sculpt=False)
        done.append(pname)
    if register and done:
        _d, more = register_keys(context, arm, props, only=set(done))
        notes += more
        notes.append("눈썹 코렉티브는 '다시 뽑기 (키 위)' 후 확인하세요")
    return done, notes


_HEAD_BOUNDS = {}


def head_bounds(obj):
    """(min, max) local corners of an edit mesh's head (its 'MSK-head' group
    on the basis), else of its bound box. Object.bound_box can still hold the
    whole body (it is not refreshed for the Mask modifier until redrawn)."""
    import numpy as np
    key = (obj.name, obj.data.name, len(obj.data.vertices))
    hit = _HEAD_BOUNDS.get(key)
    if hit is not None:
        return hit
    g = obj.vertex_groups.get(HEAD_GROUP)
    out = None
    if g is not None:
        idx = [v.index for v in obj.data.vertices if any(e.group == g.index for e in v.groups)]
        if idx:
            n = len(obj.data.vertices)
            co = np.empty(n * 3)
            keys = obj.data.shape_keys
            (keys.reference_key.data if keys else obj.data.vertices).foreach_get("co", co)
            co = co.reshape(-1, 3)[idx]
            out = (Vector(co.min(0)), Vector(co.max(0)))
    if out is None:
        bb = [Vector(c) for c in obj.bound_box]
        out = (Vector((min(c.x for c in bb), min(c.y for c in bb), min(c.z for c in bb))),
               Vector((max(c.x for c in bb), max(c.y for c in bb), max(c.z for c in bb))))
    _HEAD_BOUNDS[key] = out
    return out


def world_rect(obj, at=None):
    """(xmin, xmax, zmin, zmax) of a mesh in the front view: edit / work
    meshes by their head, others by their bound box. at: another location."""
    if obj.get(O.OBJ_PROP) in (STORAGE_TAG, WORK_TAG):
        lo, hi = head_bounds(obj)
        p = at if at is not None else obj.location        # (no parent; matrix_world may lag)
        return (p.x + lo.x, p.x + hi.x, p.z + lo.z, p.z + hi.z)
    bb = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
    return (min(p.x for p in bb), max(p.x for p in bb), min(p.z for p in bb), max(p.z for p in bb))


def _free_x0(items, xs, dx, dz, cols, top):
    """Left column of a free spot beside the brow block for these items (in a
    cols-wide grid from the top row down): right of the block first, then left,
    further out until no other visible mesh is in the way."""
    skip = set(items)
    others = []
    for o in bpy.data.objects:
        if o.type != 'MESH' or o in skip or not o.visible_get():
            continue
        others.append(world_rect(o))
    lo, hi = head_bounds(items[0])                     # (the edit meshes share a shape)
    ox0, ox1, oz0, oz1 = lo.x, hi.x, lo.z, hi.z
    m = dx * 0.05

    def fits(x0):
        for i in range(len(items)):
            lx, lz = x0 + (i % cols) * dx, top - (i // cols) * dz
            r = (lx + ox0 - m, lx + ox1 + m, lz + oz0 - m, lz + oz1 + m)
            if any(r[0] < b[1] and b[0] < r[1] and r[2] < b[3] and b[2] < r[3] for b in others):
                return False
        return True

    right = max(xs) + dx * (1.0 + GROUP_GAP)
    left = min(xs) - dx * (1.0 + GROUP_GAP) - (cols - 1) * dx
    for k in range(60):
        for x0 in (right + k * dx, left - k * dx):
            if fits(x0):
                return x0
    return right


def place_brow_correctives(context, props):
    """The brow correctives and the brow work mesh in a free spot beside the
    brow block (where it is now; the other meshes are not moved, nothing is
    put over another mesh). Returns how many."""
    mesh = props.mesh
    pks = [pk for pk in mesh.data.pose_keys if pk.storage_object is not None]
    brow = {pk.name: pk.storage_object for pk in pks
            if (_cell(pk.name) or ("",))[0] == "brow"}
    if not brow:
        return 0
    xs = sorted({round(o.location.x, 5) for o in brow.values()})
    steps = [b - a for a, b in zip(xs, xs[1:]) if b - a > 1e-3]
    dx = min(steps) if steps else 0.3
    dzs = []
    for name, o in brow.items():
        if name.endswith("_pos") and name[:-4] + "_neg" in brow:
            dzs.append(abs(o.location.z - brow[name[:-4] + "_neg"].location.z))
    dz = sorted(dzs)[len(dzs) // 2] if dzs else dx
    corr = {pk.name[3:]: pk.storage_object for pk in pks if is_brow_corr(pk.name)}
    items = work_objects() + [corr[n] for n in BROW_CORR if n in corr]
    if not items:
        return 0
    cols = 3
    top = max(o.location.z for o in brow.values())
    y = next(iter(brow.values())).location.y
    x0 = _free_x0(items, xs, dx, dz, cols, top)
    for i, o in enumerate(items):
        o.location = Vector((x0 + (i % cols) * dx, y, top - (i // cols) * dz))
    # its title
    for t in [t for t in bpy.data.objects if t.get(O.OBJ_PROP) == TITLE_TAG and t.name.startswith("BROW CORR")]:
        bpy.data.objects.remove(t, do_unlink=True)
    coll = bpy.data.collections.get(STORAGE_COLL)
    ref = next((t for t in bpy.data.objects if t.get(O.OBJ_PROP) == TITLE_TAG and t.name == "BROW"), None)
    if coll is not None:
        t = bpy.data.objects.new("BROW CORR", None)
        t.empty_display_type = 'PLAIN_AXES'
        t.empty_display_size = dx * 0.3
        t.show_name = not getattr(props, "show_sk_overlay", False)
        t.hide_select = True
        t[O.OBJ_PROP] = TITLE_TAG
        coll.objects.link(t)
        lo, hi = head_bounds(items[0])               # just above the top row's heads
        t.location = Vector((x0 + lo.x, ref.location.y if ref is not None else y,
                             top + hi.z + dz * 0.12))
    return len(items)


def _sculpted_co(obj):
    """An edit mesh's shape (Morph Target + New Changes), without its modifiers."""
    import numpy as np
    keys = obj.data.shape_keys
    n = len(obj.data.vertices)
    out = np.empty(n * 3)
    if keys is None:
        obj.data.vertices.foreach_get("co", out)
        return out.reshape(-1, 3)
    kbs = keys.key_blocks
    keys.reference_key.data.foreach_get("co", out)
    out = out.reshape(-1, 3)
    base = out.copy()
    c = np.empty(n * 3)
    for kb in kbs[1:]:
        if kb.mute or kb.value == 0.0:
            continue
        kb.data.foreach_get("co", c)
        rel = base
        if kb.relative_key != keys.reference_key:
            rel = np.empty(n * 3)
            kb.relative_key.data.foreach_get("co", rel)
            rel = rel.reshape(-1, 3)
        d = (c.reshape(-1, 3) - rel) * kb.value
        g = obj.vertex_groups.get(kb.vertex_group) if kb.vertex_group else None
        if g is not None:                      # the key's vertex mask (e.g. MSK-L: half cut)
            w = np.zeros(n)
            for v in obj.data.vertices:
                for e in v.groups:
                    if e.group == g.index:
                        w[v.index] = e.weight
            d *= w[:, None]
        out = out + d
    return out


def _custom_key_terms(c):
    if c.get("key_terms"):
        return c["key_terms"]
    names, terms = c["names"], c["terms"]
    if len(names) == 2:
        return {n: [t for t in terms if not t["bone"].endswith(
            (".R", "_R") if n.endswith(".L") else (".L", "_L"))] for n in names}
    return {names[0]: terms}


REGISTER_PASSES = 3


def register_keys(context, arm, props, only=None):
    """The last step: make the face's shape keys with their drivers (storm's
    and the custom ones) and fill each from its edit mesh: the sculpt minus the
    face the rig makes at that pose (Armature only, the other keys on, its own
    off), carried back to rest. Keys the correctives fix go in first.
    only: just these pose keys (e.g. the correctives), on the keys already
    there: no keys or drivers are made. Returns (keys filled, notes)."""
    import numpy as np
    mesh = props.mesh
    if only is None:
        made, skipped, notes = create(context, arm, props)
        sk = mesh.data.shape_keys
        kbs = sk.key_blocks
        for c in custom_specs(mesh):
            pair = c.get("pair", len(c["names"]) == 2)
            for kname, kterms in _custom_key_terms(c).items():
                kb = kbs.get(kname) or mesh.shape_key_add(name=kname, from_mix=False)
                kb.value = 0.0
                kb.vertex_group = (MASKS[0] if kname.endswith(".L") else MASKS[1]) if pair else ""
                _drive_custom(kb, arm, kterms)
        mesh[KEYS_PROP] = sorted(set(mesh.get(KEYS_PROP, [])) | {n for c in custom_specs(mesh)
                                                                  for n in c["names"]})
    else:
        skipped, notes = [], []
        if mesh.data.shape_keys is None:
            raise ValueError("얼굴에 쉐이프키가 없습니다 (먼저 얼굴에 등록하세요)")
    sk = mesh.data.shape_keys
    kbs = sk.key_blocks
    act = bpy.data.actions.get(POSE_ACTION)
    pks = [pk for pk in mesh.data.pose_keys if pk.storage_object is not None
           and any(t.name in kbs for t in pk.target_shapes)
           and (only is None or pk.name in only)]
    # a corrective whose edit mesh was taken with the rig alone would only undo
    # the keys under it: emptied until it is taken again over the keys
    corr = set(corrective_names(mesh))
    raw = [pk for pk in pks if pk.name in corr and not pk.storage_object.get(OVER_KEYS)]
    if raw:
        ref = np.empty(len(mesh.data.vertices) * 3)
        sk.reference_key.data.foreach_get("co", ref)
        for pk in raw:
            for t in pk.target_shapes:
                if t.name in kbs:
                    kbs[t.name].data.foreach_set("co", ref)
        notes.append(f"코렉티브 {len(raw)}개는 비워 둠: '코렉티브 다시 뽑기' 후 스컬프트하고 등록하세요")
        pks = [pk for pk in pks if pk not in raw]
    # simple keys before the combos / correctives laid over them
    n_terms = {s["name"]: len(s["terms"]) for s in specs(arm)[0]}
    custom = {c["base"] for c in custom_specs(mesh)}
    pks.sort(key=lambda pk: (pk.name in custom or _section(pk.name) == "other",
                             max((n_terms.get(t.name, 1) for t in pk.target_shapes), default=1)))
    n = len(mesh.data.vertices)
    basis = np.empty(n * 3)
    sk.reference_key.data.foreach_get("co", basis)
    basis = basis.reshape(-1, 3)
    mir = None
    full = _save_pose(arm)
    ad = arm.animation_data
    held = (ad.action, getattr(ad, "action_slot", None)) if ad and ad.action else None
    if held:
        ad.action = None
    dg = context.evaluated_depsgraph_get()
    sculpted = {pk.name: _sculpted_co(pk.storage_object) for pk in pks}
    weights, masked = {}, {}           # keys whose mask cuts off part of the sculpt

    def push(pk):
        nonlocal mir
        targets = [(kbs[t.name], t.mirror_x) for t in pk.target_shapes if t.name in kbs]
        if act is not None and pk.action == act:
            _apply_frame(arm, act, int(pk.frame))
        else:
            _rest_pose(arm)
        muted = [kb.mute for kb, _m in targets]
        for kb, _m in targets:
            kb.mute = True
        try:
            delta = sculpted[pk.name] - _eval(context, mesh)
            moved = np.nonzero(np.abs(delta).max(1) > 1e-7)[0]
            mesh.crazyspace_eval(dg, context.scene)
            rest = np.zeros_like(delta)
            for i in moved:
                rest[i] = mesh.crazyspace_displacement_to_original(
                    vertex_index=int(i), displacement=Vector(delta[i]))
            mesh.crazyspace_eval_clear()
        finally:
            for (kb, _m), m in zip(targets, muted):
                kb.mute = m
        for kb, mirror in targets:
            d = rest
            if mirror:
                if mir is None:
                    mir = mirror_map(mesh)
                d = np.zeros_like(rest)
                for i, j in mir.items():
                    d[i] = (-rest[j][0], rest[j][1], rest[j][2])
            kb.data.foreach_set("co", (basis + d).reshape(-1))
            g = kb.vertex_group
            if g and g not in MASKS and g in mesh.vertex_groups:   # (not the side split)
                if g not in weights:
                    w = np.zeros(n)
                    gi = mesh.vertex_groups[g].index
                    for v in mesh.data.vertices:
                        for e in v.groups:
                            if e.group == gi:
                                w[v.index] = e.weight
                    weights[g] = w
                cut = float((np.abs(d).max(1) * (1.0 - weights[g])).max())
                if cut > 1e-3:
                    masked[kb.name] = (g, cut)
        mesh.data.update()
        return [kb.name for kb, _m in targets]

    done = []
    try:
        with _armature_only(mesh):
            # keys partly on at each other's poses (x / x_extreme): a few
            # passes settle them on each other
            for _pass in range(REGISTER_PASSES):
                done = [n for pk in pks for n in push(pk)]
    finally:
        if held:
            ad.action = held[0]
            if held[1] is not None:
                ad.action_slot = held[1]
        _load_pose(arm, full)
        mesh.data.update()
        context.view_layer.update()
    for pk in pks:                       # the overlay compares the sculpt with this
        if done and pk.name in sculpted:
            pk.storage_object[REG_HASH] = co_hash(sculpted[pk.name])
    for pk in raw:
        if REG_HASH in pk.storage_object:
            del pk.storage_object[REG_HASH]
    if skipped:
        notes.append(f"Storm 키 {len(skipped)}개 건너뜀 (예: {skipped[0][0]}: {skipped[0][1]})")
    if masked:
        nm, (g, cut) = max(masked.items(), key=lambda it: it[1][1])
        notes.append(f"마스크 밖 수정은 반영 안 됨: 키 {len(masked)}개 (최대 {nm}: {g} 밖 {cut * 1000:.1f}mm)")
    return done, notes


# Layout of the edit meshes (cells; rows count up from the bottom of a block),
# like the studio's working files: one block per face part, inside a brow /
# cheek block one 3 x 2 group per control (x y z across, + on top, - below).
PARTS = ("all", "in", "mid", "out")
MC_ROWS = ("yx_neg", "xy_neg", "xy_pos", "x_neg", "x_pos", "y_neg", "y_pos")   # bottom -> top
OTHER_ORDER = ("Mouth_pucker", "cheek_puff", "nostril_sneer", "compression_nose",
               "Brows_Ty_low_corr", "Brows_rx_low_corr", "Furrow_Ty_low_corr",
               "Brow_Cheek_combo", "Cheek_eyelid_corr")
BLOCK_GAP = 1.5          # cells between blocks
GROUP_GAP = 0.6          # cells between the all / in / mid / out groups


def _cell(name):
    """(block, column, row) of a pose key in its block, or None (-> others)."""
    import re
    n = name[3:] if name.startswith("SK-") else name
    m = re.match(r"(Brow|Cheek)_(all|in|mid|out)_(x|y|z|rz)_(pos|neg)$", n)
    if m:
        part = PARTS.index(m.group(2))
        col = "xyz".index(m.group(3)) if m.group(3) != "rz" else 3
        x = part * (3 + GROUP_GAP) + (1 if part else 0) + col      # "all" is 4 wide (rz)
        return m.group(1).lower(), x, 1 if m.group(4) == "pos" else 0
    m = re.match(r"Lip_(upp|low)_rx_(pos|neg)$", n)
    if m:
        return "mouth", 0 if m.group(2) == "pos" else 1, 6 if m.group(1) == "upp" else 5
    if n == "mouth_open_corr":
        return "mouth", 4, len(MC_ROWS)
    m = re.match(r"MC_(.+?)(_open|_extreme)?$", n)
    if m and m.group(1) in MC_ROWS:
        col = {None: 3, "_open": 4, "_extreme": 5}[m.group(2)]
        return "mouth", col, MC_ROWS.index(m.group(1))
    if n == "MC_Cheek_up_combo":
        return "mouth", 5, MC_ROWS.index("xy_pos")
    return None


def arrange(context, props, base_co=None, head=None):
    """Place the edit meshes in blocks over the character: brow (left, below),
    cheek (left, above), mouth (middle), correctives / others (right)."""
    import numpy as np
    mesh = props.mesh
    arm = O.facial_rig(context) or props.dsp_armature
    pks = mesh.data.pose_keys
    objs = {pk.name: pk.storage_object for pk in pks if pk.storage_object is not None
            and pk.storage_object.get(O.OBJ_PROP) == STORAGE_TAG}
    if not objs:
        return 0
    if base_co is None:
        sk = mesh.data.shape_keys
        n = len(mesh.data.vertices)
        base_co = np.empty(n * 3)
        (sk.reference_key.data if sk else mesh.data.vertices).foreach_get("co", base_co)
        base_co = base_co.reshape(-1, 3)
        head = _head_verts(mesh, arm, props)
    hc = base_co[head] if head else base_co
    lo, hi = hc.min(0), hc.max(0)
    dx, dz = (hi[0] - lo[0]) * 1.25, (hi[2] - lo[2]) * 1.3
    cells = {"brow": {}, "cheek": {}, "mouth": {}, "other": {}}
    rest = []
    for name in objs:
        c = _cell(name)
        if c is None:
            if not is_brow_corr(name):              # (placed by the brow block)
                rest.append(name)
        else:
            cells[c[0]][name] = (c[1], c[2])
    key = lambda nm: (OTHER_ORDER.index(nm[3:]) if nm[3:] in OTHER_ORDER else len(OTHER_ORDER), nm)
    for i, name in enumerate(sorted(rest, key=key)):
        cells["other"][name] = (i % 2, i // 2)
    width = {b: (max(c for c, _r in v.values()) + 1 if v else 0) for b, v in cells.items()}
    rows = {b: (max(r for _c, r in v.values()) + 1 if v else 0) for b, v in cells.items()}
    mouth_x = -width["mouth"] / 2
    left_w = max(width["brow"], width["cheek"])
    origin = {"mouth": (mouth_x, 0.0),
              "other": (mouth_x + width["mouth"] + BLOCK_GAP, 0.0),
              "brow": (mouth_x - BLOCK_GAP - left_w, 0.0),
              "cheek": (mouth_x - BLOCK_GAP - left_w, rows["brow"] + 1.0)}
    top = float(base_co[:, 2].max())
    z0 = (top - lo[2]) + dz * 0.5
    for b, v in cells.items():
        ox, oz = origin[b]
        for name, (c, r) in v.items():
            objs[name].location = mesh.location + Vector(((ox + c + 0.5) * dx, 0.0, z0 + (oz + r) * dz))
    # short name tags inside the brow / cheek blocks ("B in x+"): the full names overlap
    import re
    for name, o in objs.items():
        m = re.match(r"SK-(Brow|Cheek)_(all|in|mid|out)_(x|y|z|rz)_(pos|neg)$", name)
        tag = (f"{m.group(1)[0]} {m.group(2)} {m.group(3)}{'+' if m.group(4) == 'pos' else '-'}"
               if m else name[3:])
        for ch in o.children:
            if ch.get(O.OBJ_PROP) == LABEL_TAG and ch.name != tag:
                ch.name = tag
    # block titles
    coll = bpy.data.collections.get(STORAGE_COLL)
    for o in [o for o in bpy.data.objects if o.get(O.OBJ_PROP) == TITLE_TAG]:
        bpy.data.objects.remove(o, do_unlink=True)
    for b, title in (("brow", "BROW"), ("cheek", "CHEEK"), ("mouth", "MOUTH"), ("other", "CORRECTIVES")):
        if not cells[b] or coll is None:
            continue
        ox, oz = origin[b]
        t = bpy.data.objects.new(title, None)
        t.empty_display_type = 'PLAIN_AXES'
        t.empty_display_size = dx * 0.3
        t.show_name = not getattr(props, "show_sk_overlay", False)
        t.hide_select = True
        t[O.OBJ_PROP] = TITLE_TAG
        coll.objects.link(t)
        t.location = mesh.location + Vector((ox * dx, float(lo[1]), z0 + (oz + rows[b]) * dz + lo[2] + dz * 0.15))
    place_brow_correctives(context, props)
    return len(objs)


TITLE_TAG = "SK_TITLE"


def clear_pose_keys(props):
    """Remove the tool's storage objects, their pose keys and the pose action."""
    mesh = props.mesh
    n = 0
    names = {c["base"] for c in custom_specs(mesh)}
    if pose_keys_available(mesh):
        pks = mesh.data.pose_keys
        names |= {pk.name for pk in pks}
        for i in reversed(range(len(pks))):
            o = pks[i].storage_object
            if o is not None and o.get(O.OBJ_PROP) == STORAGE_TAG:
                pks.remove(i)
        mesh.data.active_pose_key_index = 0
    tl = bpy.context.scene.timeline_markers
    for m in [m for m in tl if m.name in names]:
        tl.remove(m)
    for o in [o for o in bpy.data.objects if o.get(O.OBJ_PROP) in (LABEL_TAG, TITLE_TAG)]:
        bpy.data.objects.remove(o, do_unlink=True)
    for o in [o for o in bpy.data.objects if o.get(O.OBJ_PROP) == STORAGE_TAG]:
        me = o.data
        bpy.data.objects.remove(o, do_unlink=True)
        if me.users == 0:
            bpy.data.meshes.remove(me)
        n += 1
    _drop_pose_action()
    coll = bpy.data.collections.get(STORAGE_COLL)
    if coll is not None and not coll.objects:
        bpy.data.collections.remove(coll)
    return n


# ----------------------------------------------------------------------
# Operators
# ----------------------------------------------------------------------
def _poll(context):
    props = context.scene.hb_facial
    return O.facial_rig(context) is not None and props.mesh is not None and props.mesh.mode == 'OBJECT'


class HB_OT_facial_shape_keys(bpy.types.Operator):
    """얼굴 메쉬에 Storm의 입·눈썹·볼 쉐이프키(빈 키)를 드라이버와 함께
만듭니다. 이 리그에 컨트롤이 있는 키만 만듭니다"""

    bl_idname = "hb.facial_shape_keys"
    bl_label = "쉐이프키"
    bl_options = {'REGISTER', 'UNDO'}

    shapes: bpy.props.BoolProperty(
        name="Storm 쉐이프", default=False,
        description="새 키를 이 얼굴로 옮긴 Storm의 쉐이프로도 채웁니다")

    @classmethod
    def poll(cls, context):
        return _poll(context)

    def execute(self, context):
        props = context.scene.hb_facial
        arm = O.facial_rig(context)
        before = set(props.mesh.get(KEYS_PROP, []))
        made, skipped, notes = create(context, arm, props)
        msg = f"키 {made}개 생성, {len(props.mesh.get(KEYS_PROP, []))}개 드라이버 연결"
        if self.shapes:
            new = [n for n in props.mesh.get(KEYS_PROP, []) if n not in before]
            try:
                msg += f", Storm 쉐이프 {len(transfer(context, arm, props, new))}개"
            except ValueError as e:
                notes.append(str(e))
        if skipped:
            msg += f"; {len(skipped)}개 건너뜀 (예: {skipped[0][0]}: {skipped[0][1]})"
        if notes:
            msg += "; " + "; ".join(notes[:3])
        self.report({'WARNING'} if notes else {'INFO'}, msg)
        return {'FINISHED'}


class HB_OT_facial_shape_transfer(bpy.types.Operator):
    """활성 쉐이프키(또는 이 도구의 모든 키)를 이 얼굴로 옮긴 Storm의
쉐이프로 채웁니다. 키의 기존 내용은 대체됩니다"""

    bl_idname = "hb.facial_shape_transfer"
    bl_label = "Storm 쉐이프"
    bl_options = {'REGISTER', 'UNDO'}

    all_keys: bpy.props.BoolProperty(name="전체 키", default=False)

    @classmethod
    def poll(cls, context):
        props = context.scene.hb_facial
        return _poll(context) and props.mesh.data.shape_keys is not None

    def execute(self, context):
        props = context.scene.hb_facial
        arm = O.facial_rig(context)
        names = None if self.all_keys else [props.mesh.active_shape_key.name]
        try:
            done = transfer(context, arm, props, names)
        except ValueError as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        self.report({'INFO'} if done else {'WARNING'},
                    f"Storm 쉐이프 {len(done)}개" if done else "Storm의 키가 아닙니다")
        return {'FINISHED'}


class HB_OT_facial_shape_clear(bpy.types.Operator):
    """이 도구로 만든 쉐이프키와 드라이버를 삭제합니다 (다른 키는 유지됩니다)"""

    bl_idname = "hb.facial_shape_clear"
    bl_label = "쉐이프키 삭제"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _poll(context)

    def execute(self, context):
        self.report({'INFO'}, f"키 {clear(context.scene.hb_facial)}개 삭제")
        return {'FINISHED'}


class HB_OT_facial_shape_edit(bpy.types.Operator):
    """포즈된 얼굴에서 활성 쉐이프키를 편집합니다: 키가 완전히 켜지는 위치로
컨트롤을 옮기고 변형된 얼굴의 스컬프트 복사본을 엽니다. 스컬프트한 뒤
적용을 누릅니다"""

    bl_idname = "hb.facial_shape_edit"
    bl_label = "키 편집"
    bl_options = {'REGISTER', 'UNDO'}

    pose: bpy.props.BoolProperty(
        name="컨트롤 포즈", default=True,
        description="키의 컨트롤을 키가 완전히 켜지는 포즈로 옮깁니다 (끄면 현재 포즈에서 스컬프트합니다)")

    @classmethod
    def poll(cls, context):
        props = context.scene.hb_facial
        mesh = props.mesh
        return (O.facial_rig(context) is not None and mesh is not None and mesh.mode == 'OBJECT'
                and mesh.data.shape_keys is not None and mesh.active_shape_key_index > 0
                and sculpt_object() is None)

    def execute(self, context):
        props = context.scene.hb_facial
        name = props.mesh.active_shape_key.name
        try:
            edit_begin(context, O.facial_rig(context), props, name, self.pose)
        except ValueError as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        self.report({'INFO'}, f"{name}을(를) 스컬프트한 뒤 적용을 누르세요")
        return {'FINISHED'}


class HB_OT_facial_shape_apply(bpy.types.Operator):
    """스컬프트한 변화를 쉐이프키에 넣고(리그를 거쳐 역변환하므로 포즈된 얼굴이
스컬프트와 일치합니다) 스컬프트 복사본을 닫습니다"""

    bl_idname = "hb.facial_shape_apply"
    bl_label = "적용"
    bl_options = {'REGISTER', 'UNDO'}

    mirror: bpy.props.BoolProperty(
        name="미러", default=True,
        description="편집한 쪽을 반대쪽과 .L / .R 짝 키에 복사합니다")

    @classmethod
    def poll(cls, context):
        return sculpt_object() is not None

    def execute(self, context):
        props = context.scene.hb_facial
        try:
            n, lost, res, keys = edit_apply(context, O.facial_rig(context), props, self.mirror)
        except ValueError as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        if not n and not lost:
            self.report({'INFO'}, "스컬프트한 내용이 없습니다")
        else:
            msg = f"버텍스 {n}개 -> {', '.join(keys)} (오차 {res:.2f} mm)"
            if lost:
                msg += f"; 이 포즈에서 키가 움직일 수 없는 버텍스 {lost}개 (마스크 밖이거나 키가 꺼져 있음)"
            self.report({'WARNING'} if lost or res > 0.5 else {'INFO'}, msg)
        return {'FINISHED'}


class HB_OT_facial_shape_cancel(bpy.types.Operator):
    """키를 바꾸지 않고 스컬프트 복사본을 닫습니다"""

    bl_idname = "hb.facial_shape_cancel"
    bl_label = "편집 취소"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return sculpt_object() is not None

    def execute(self, context):
        _edit_end(context, O.facial_rig(context), sculpt_object())
        return {'FINISHED'}


class HB_OT_facial_shape_mirror(bpy.types.Operator):
    """활성 키를 자기 쪽을 기준으로 대칭으로 만들고(.L 키는 왼쪽 절반)
.L / .R 짝 키에 복사합니다"""

    bl_idname = "hb.facial_shape_mirror"
    bl_label = "키 미러"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        mesh = context.scene.hb_facial.mesh
        return (mesh is not None and mesh.mode == 'OBJECT' and mesh.data.shape_keys is not None
                and mesh.active_shape_key_index > 0)

    def execute(self, context):
        mesh = context.scene.hb_facial.mesh
        done = mirror_key(mesh, mesh.active_shape_key.name)
        self.report({'INFO'}, "미러 완료: " + ", ".join(done))
        return {'FINISHED'}


class HB_OT_facial_shape_pose_keys(bpy.types.Operator):
    """이 도구의 쉐이프키에 Pose Shape Keys 애드온을 설정합니다: 키마다 포즈 키
(포즈 프레임, .L + .R 타깃)와 스컬프트하거나 변형할 헤드 메쉬를 하나씩 만들어
격자로 배치합니다. 기존 헤드 메쉬는 유지됩니다. 그다음 얼굴 메쉬에서 애드온의
Set Pose / Push를 사용합니다 (Data > Shape Keys > Pose Keys)"""

    bl_idname = "hb.facial_shape_pose_keys"
    bl_label = "편집 메쉬"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        props = context.scene.hb_facial
        if not pose_keys_available(props.mesh):
            cls.poll_message_set("먼저 Pose Shape Keys 애드온(Blender Studio)을 활성화하세요")
            return False
        return _poll(context) and sculpt_object() is None

    def execute(self, context):
        props = context.scene.hb_facial
        n, made, notes = setup_pose_keys(context, O.facial_rig(context), props)
        msg = f"포즈 키 {n}개, {STORAGE_COLL}에 새 편집 메쉬 {made}개"
        if notes:
            msg += "; " + "; ".join(notes)
        self.report({'WARNING'} if notes else {'INFO'}, msg)
        return {'FINISHED'}


class HB_OT_facial_shape_pose_clear(bpy.types.Operator):
    """편집 메쉬와 그 포즈 키, 포즈 액션을 삭제합니다 (Push하지 않은 편집 메쉬의
스컬프트는 사라지고, 쉐이프키는 유지됩니다)"""

    bl_idname = "hb.facial_shape_pose_clear"
    bl_label = "편집 메쉬 삭제"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return any(o.get(O.OBJ_PROP) == STORAGE_TAG for o in bpy.data.objects)

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        self.report({'INFO'}, f"편집 메쉬 {clear_pose_keys(context.scene.hb_facial)}개 삭제")
        return {'FINISHED'}


def _pose_key_names(context, props):
    """Pose keys to act on: the selected edit meshes', else the one(s) posed
    at the current frame."""
    pks = props.mesh.data.pose_keys
    sel = {o for o in context.selected_objects if o.get(O.OBJ_PROP) == STORAGE_TAG}
    names = [pk.name for pk in pks if pk.storage_object in sel]
    if not names:
        names = [pk.name for pk in pks if pk.frame == context.scene.frame_current]
    return names


class HB_OT_facial_shape_pose_refresh(bpy.types.Operator):
    """포즈 액션의 포즈를 편집 메쉬에 다시 반영합니다: 해당 프레임에서 Morph
Target을 새로 캡처합니다(그 프레임에서 포즈를 편집한 뒤). 선택한 편집 메쉬,
또는 현재 프레임의 포즈에 적용합니다. 스컬프트는 유지됩니다"""

    bl_idname = "hb.facial_shape_pose_refresh"
    bl_label = "프레임에서 새로고침"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        props = context.scene.hb_facial
        return (_poll(context) and pose_keys_available(props.mesh)
                and bool(props.mesh.data.pose_keys) and bpy.data.actions.get(POSE_ACTION) is not None)

    def execute(self, context):
        props = context.scene.hb_facial
        names = _pose_key_names(context, props)
        if not names:
            self.report({'WARNING'}, "편집 메쉬를 선택하거나 포즈의 프레임으로 이동하세요 (마커 참고)")
            return {'CANCELLED'}
        refresh_from_frame(context, O.facial_rig(context), props, names)
        self.report({'INFO'}, f"{len(names)}개 새로고침: {', '.join(names[:4])}")
        return {'FINISHED'}


class HB_OT_facial_shape_pose_empty(bpy.types.Operator):
    """선택한 편집 메쉬(또는 현재 프레임의 포즈)의 쉐이프키를 비웁니다: 미리
적용된 것 없이 편집 메쉬에 포즈만 보이므로 처음부터 스컬프트하고 Push할 수
있습니다. 키에 있던 내용은 사라집니다"""

    bl_idname = "hb.facial_shape_pose_empty"
    bl_label = "키 비우기"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return HB_OT_facial_shape_pose_refresh.poll(context)

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        props = context.scene.hb_facial
        names = _pose_key_names(context, props)
        if not names:
            self.report({'WARNING'}, "편집 메쉬를 선택하거나 포즈의 프레임으로 이동하세요 (마커 참고)")
            return {'CANCELLED'}
        done = empty_keys(context, O.facial_rig(context), props, names)
        self.report({'INFO'}, f"포즈 {len(names)}개의 키 {len(done)}개 비움")
        return {'FINISHED'}


class HB_OT_facial_shape_pose_add(bpy.types.Operator):
    """리그의 현재 포즈에 대한 새 쉐이프키를 만듭니다(코렉티브, 추가 쉐이프):
움직인 컨트롤로 만든 드라이버(클램프한 값의 곱), 이 프레임(또는 다음 빈
프레임)에 포즈 액션으로 키잉한 포즈, 포즈 키와 편집 메쉬를 만듭니다"""

    bl_idname = "hb.facial_shape_pose_add"
    bl_label = "포즈 키 추가"
    bl_options = {'REGISTER', 'UNDO'}

    key_name: bpy.props.StringProperty(name="이름", default="SK-new_corr")
    sides: bpy.props.EnumProperty(name="좌우", items=(
        ('PAIR', ".L / .R 쌍", "MSK-face.L / .R로 나뉜 키 두 개 (양쪽 모두 포즈하세요)"),
        ('SINGLE', "단일 키", "좌우 마스크 없는 키 하나")), default='PAIR')

    @classmethod
    def poll(cls, context):
        props = context.scene.hb_facial
        return _poll(context) and pose_keys_available(props.mesh) and sculpt_object() is None

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        props = context.scene.hb_facial
        try:
            n, made, notes = add_pose_key(context, O.facial_rig(context), props,
                                          self.key_name.strip(), self.sides == 'PAIR')
        except ValueError as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        self.report({'INFO'}, f"{self.key_name} 추가; 새 편집 메쉬 {made}개")
        return {'FINISHED'}


def copy_weights(src, targets, names):
    """Copy these vertex groups of src onto targets (same vertex order: the
    edit meshes are copies of the face). Replaces what a target's group held.
    Returns (meshes written, [skipped names])."""
    n = len(src.data.vertices)
    idx = {src.vertex_groups[nm].index: nm for nm in names}
    rows = {nm: {} for nm in names}
    for v in src.data.vertices:
        for g in v.groups:
            nm = idx.get(g.group)
            if nm is not None and g.weight > 0.0:
                rows[nm].setdefault(round(g.weight, 4), []).append(v.index)
    done, skipped = 0, []
    allv = list(range(n))
    for o in targets:
        if o.type != 'MESH' or len(o.data.vertices) != n:
            skipped.append(o.name)
            continue
        for nm in names:
            vg = o.vertex_groups.get(nm) or o.vertex_groups.new(name=nm)
            vg.remove(allv)
            for w, ids in rows[nm].items():
                vg.add(ids, w, 'REPLACE')
        o.data.update()
        done += 1
    return done, skipped


class HB_OT_facial_shape_copy_weights(bpy.types.Operator):
    """활성 메쉬의 버텍스 그룹(또는 모든 그룹)을 선택한 다른 메쉬에 복사합니다
    (예: 편집 메쉬 하나에서 나머지로). 같은 토폴로지끼리만 가능하며, 대상의
    그룹은 대체됩니다"""

    bl_idname = "hb.facial_shape_copy_weights"
    bl_label = "웨이트 복사"
    bl_options = {'REGISTER', 'UNDO'}

    which: bpy.props.EnumProperty(name="그룹", items=(
        ('ACTIVE', "활성 그룹", "활성 버텍스 그룹만 복사합니다"),
        ('ALL', "전체 그룹", "활성 메쉬의 모든 버텍스 그룹을 복사합니다")), default='ACTIVE')

    @classmethod
    def poll(cls, context):
        src = context.active_object
        if src is None or src.type != 'MESH' or not src.vertex_groups:
            cls.poll_message_set("웨이트가 있는 메쉬를 활성으로 만드세요")
            return False
        if not any(o is not src and o.type == 'MESH' for o in context.selected_objects):
            cls.poll_message_set("복사할 대상 메쉬도 선택하세요")
            return False
        return True

    def execute(self, context):
        src = context.active_object
        names = ([src.vertex_groups.active.name] if self.which == 'ACTIVE' and src.vertex_groups.active
                 else [g.name for g in src.vertex_groups])
        targets = [o for o in context.selected_objects if o is not src]
        done, skipped = copy_weights(src, targets, names)
        msg = f"{', '.join(names[:3])}{'...' if len(names) > 3 else ''} -> 메쉬 {done}개"
        if skipped:
            msg += f"; 건너뜀 (토폴로지 다름): {', '.join(skipped[:3])}"
        self.report({'WARNING'} if skipped else {'INFO'}, msg)
        return {'FINISHED'}


def copy_shape_keys(src, targets, names):
    """Copy these shape keys of src onto targets as offsets: target key = the
    target's own relative key + (src key - src relative key), so copying a
    sculpt layer (New Changes) keeps each target's own pose under it. A key
    missing on a target is added. Returns (meshes written, [skipped names])."""
    import numpy as np
    n = len(src.data.vertices)
    kbs = src.data.shape_keys.key_blocks
    deltas = {}
    for nm in names:
        kb = kbs[nm]
        c, r = np.empty(n * 3), np.empty(n * 3)
        kb.data.foreach_get("co", c)
        kb.relative_key.data.foreach_get("co", r)
        deltas[nm] = (c - r, kb.relative_key.name, kb.value, kb.vertex_group)
    done, skipped = 0, []
    for o in targets:
        if o.type != 'MESH' or len(o.data.vertices) != n:
            skipped.append(o.name)
            continue
        if o.data.shape_keys is None:
            o.shape_key_add(name="Basis", from_mix=False)
        tk = o.data.shape_keys.key_blocks
        for nm in names:
            d, rel, value, vg = deltas[nm]
            kb = tk.get(nm) or o.shape_key_add(name=nm, from_mix=False)
            if kb == o.data.shape_keys.reference_key:
                skipped.append(f"{o.name}:{nm} (basis)")
                continue
            if rel in tk and tk[rel] != kb:
                kb.relative_key = tk[rel]
            r = np.empty(n * 3)
            kb.relative_key.data.foreach_get("co", r)
            kb.data.foreach_set("co", r + d)
            kb.value = value
            if vg in o.vertex_groups:
                kb.vertex_group = vg
        o.data.update()
        done += 1
    return done, skipped


class HB_OT_facial_shape_copy_keys(bpy.types.Operator):
    """활성 메쉬의 활성 쉐이프키(또는 Basis를 뺀 모든 키)를 선택한 다른 메쉬에
    각 메쉬 고유 쉐이프 위의 오프셋으로 복사합니다(예: 편집 메쉬 하나의 스컬프트
    New Changes를 다른 편집 메쉬로). 같은 토폴로지끼리만 가능하며, 같은 이름의
    키는 대체됩니다"""

    bl_idname = "hb.facial_shape_copy_keys"
    bl_label = "쉐이프키 복사"
    bl_options = {'REGISTER', 'UNDO'}

    which: bpy.props.EnumProperty(name="키", items=(
        ('ACTIVE', "활성 키", "활성 쉐이프키만 복사합니다"),
        ('ALL', "전체 키", "Basis를 뺀 모든 쉐이프키를 복사합니다")), default='ACTIVE')

    @classmethod
    def poll(cls, context):
        src = context.active_object
        if src is None or src.type != 'MESH' or src.data.shape_keys is None \
                or len(src.data.shape_keys.key_blocks) < 2:
            cls.poll_message_set("쉐이프키가 있는 메쉬를 활성으로 만드세요")
            return False
        if src.mode != 'OBJECT':
            cls.poll_message_set("오브젝트 모드에서만 가능합니다")
            return False
        if not any(o is not src and o.type == 'MESH' for o in context.selected_objects):
            cls.poll_message_set("복사할 대상 메쉬도 선택하세요")
            return False
        return True

    def execute(self, context):
        src = context.active_object
        keys = src.data.shape_keys
        if self.which == 'ACTIVE':
            if src.active_shape_key_index == 0:
                self.report({'ERROR'}, "활성 키가 Basis입니다: 복사할 키를 선택하세요")
                return {'CANCELLED'}
            names = [src.active_shape_key.name]
        else:
            names = [kb.name for kb in keys.key_blocks[1:]]
        targets = [o for o in context.selected_objects if o is not src]
        done, skipped = copy_shape_keys(src, targets, names)
        msg = f"{', '.join(names[:3])}{'...' if len(names) > 3 else ''} -> 메쉬 {done}개"
        if skipped:
            msg += f"; 건너뜀: {', '.join(skipped[:3])}"
        self.report({'WARNING'} if skipped else {'INFO'}, msg)
        return {'FINISHED'}


class HB_OT_facial_shape_register(bpy.types.Operator):
    """편집 메쉬를 스컬프트한 뒤의 마지막 단계: 얼굴의 쉐이프키를 드라이버와 함께
만들고 각 키를 해당 편집 메쉬로 채웁니다. 얼굴 키의 기존 내용을 덮어씁니다"""

    bl_idname = "hb.facial_shape_register"
    bl_label = "얼굴에 등록"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        props = context.scene.hb_facial
        return (_poll(context) and pose_keys_available(props.mesh)
                and any(pk.storage_object is not None for pk in props.mesh.data.pose_keys)
                and sculpt_object() is None)

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        try:
            done, notes = register_keys(context, O.facial_rig(context), context.scene.hb_facial)
        except ValueError as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        msg = f"편집 메쉬에서 키 {len(done)}개 채움"
        if notes:
            msg += "; " + "; ".join(notes[:3])
        self.report({'WARNING'} if notes else {'INFO'}, msg)
        return {'FINISHED'}


_WHICH = (('SELECTED', "선택", "선택한 편집 메쉬(또는 현재 프레임의 포즈)의 코렉티브 / 조합 키"),
          ('ALL', "전체", "모든 코렉티브 + 조합 키"))


def _corr_targets(context, props, which):
    """(corrective / combo pose keys to act on, selected ones that are neither)."""
    corr = rebase_names(props.mesh)
    if which == 'ALL':
        return corr, []
    names = _pose_key_names(context, props)
    return [n for n in names if n in corr], [n for n in names if n not in corr]


def _corr_poll(context):
    props = context.scene.hb_facial
    return (_poll(context) and pose_keys_available(props.mesh)
            and props.mesh.data.shape_keys is not None and len(props.mesh.data.pose_keys) > 0
            and sculpt_object() is None)


class HB_OT_facial_shape_corr_rebase(bpy.types.Operator):
    """얼굴에 등록한 뒤: 코렉티브 / 조합 키(x×y, ×입벌림)의 편집 메쉬를 쉐이프키가
다 켜진 얼굴에서 다시 뽑습니다 (조합은 기본 키를 더한 모양에서 시작). Morph Target = 그 포즈에서 리그 + 모든 키(자기 키만 끔). New Changes
(스컬프트 레이어)는 그대로 위에 남습니다. 이후 프레임에서 새로고침도 키가 켜진
얼굴로 뽑습니다"""

    bl_idname = "hb.facial_shape_corr_rebase"
    bl_label = "다시 뽑기 (키 위)"
    bl_options = {'REGISTER', 'UNDO'}

    which: bpy.props.EnumProperty(name="대상", items=_WHICH, default='SELECTED')

    @classmethod
    def poll(cls, context):
        return _corr_poll(context)

    def execute(self, context):
        props = context.scene.hb_facial
        names, other = _corr_targets(context, props, self.which)
        if not names:
            self.report({'WARNING'}, "코렉티브 / 조합 편집 메쉬를 선택하거나 그 포즈의 프레임으로 이동하세요"
                        + (f" (코렉티브/조합 아님: {', '.join(other[:3])})" if other else ""))
            return {'CANCELLED'}
        try:
            done, notes = rebase_correctives(context, O.facial_rig(context), props, set(names))
        except ValueError as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        if other:
            notes.append(f"코렉티브/조합 아님, 건너뜀: {', '.join(other[:3])}")
        msg = f"{len(done)}개를 키가 켜진 얼굴에서 다시 뽑음"
        if notes:
            msg += "; " + "; ".join(notes[:3])
        self.report({'WARNING'} if notes else {'INFO'}, msg)
        return {'FINISHED'}


class HB_OT_facial_shape_corr_register(bpy.types.Operator):
    """코렉티브 / 조합 키만 얼굴에 다시 등록합니다: 편집 메쉬 모양 − (그 포즈에서 리그 +
다른 키가 다 켜진 얼굴). 단순 키와 드라이버는 건드리지 않습니다"""

    bl_idname = "hb.facial_shape_corr_register"
    bl_label = "코렉티브·조합만 등록"
    bl_options = {'REGISTER', 'UNDO'}

    which: bpy.props.EnumProperty(name="대상", items=_WHICH, default='ALL')

    @classmethod
    def poll(cls, context):
        return _corr_poll(context)

    def execute(self, context):
        props = context.scene.hb_facial
        names, other = _corr_targets(context, props, self.which)
        if not names:
            self.report({'WARNING'}, "등록할 코렉티브 / 조합 키가 없습니다")
            return {'CANCELLED'}
        try:
            done, notes = register_keys(context, O.facial_rig(context), props, only=set(names))
        except ValueError as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        msg = f"코렉티브·조합 키 {len(done)}개 등록"
        if other:
            notes.append(f"코렉티브/조합 아님, 건너뜀: {', '.join(other[:3])}")
        if notes:
            msg += "; " + "; ".join(notes[:3])
        self.report({'WARNING'} if notes else {'INFO'}, msg)
        return {'FINISHED'}


_ENUM_CACHE = []        # (Blender keeps only the strings the items callback returns alive)
MIX_SLOTS = 4


def _pose_items(self, context):
    props = context.scene.hb_facial
    mesh = props.mesh
    items = [('NONE', "-", "")]
    if mesh is not None and pose_keys_available(mesh):
        for pk in mesh.data.pose_keys:
            mark = "" if pk.storage_object is not None else "  (메쉬 없음)"
            items.append((pk.name, pretty_name(pk.name) + mark, pk.name))
    _ENUM_CACHE[:] = items
    return _ENUM_CACHE


def pretty_name(name):
    """Short readable name of a pose key: 'SK-MC_yx_neg' -> '입꼬리 yx−'."""
    import re
    n = name[3:] if name.startswith("SK-") else name
    sign = lambda s: "+" if s == "pos" else "−"
    m = re.match(r"(Brow|Cheek)_(all|in|mid|out)_(x|y|z|rz)_(pos|neg)$", n)
    if m:
        return f"{'눈썹' if m.group(1) == 'Brow' else '볼'} {m.group(2)} {m.group(3)}{sign(m.group(4))}"
    m = re.match(r"Lip_(upp|low)_rx_(pos|neg)$", n)
    if m:
        return f"{'윗입술' if m.group(1) == 'upp' else '아랫입술'} rx{sign(m.group(2))}"
    m = re.match(r"MC_(x|y|xy|yx)_(pos|neg)(_open|_extreme)?$", n)
    if m:
        tail = {None: "", "_open": " ·입벌림", "_extreme": " 끝"}[m.group(3)]
        return f"입꼬리 {m.group(1)}{sign(m.group(2))}{tail}"
    return n.replace("_", " ")


class HB_OT_facial_shape_mix(bpy.types.Operator):
    """다른 편집 메쉬들을 비율대로 섞어서 대상 편집 메쉬의 시작 모양을 만듭니다
(Storm 작업 파일처럼: 예) 눈썹 rz− = in y− ×0.55 + all y− ×0.45 ...,
rz+ = rz− ×-1). 대상에 'Mix' 쉐이프키로 들어가고(끄거나 지우기 쉬움)
New Changes(스컬프트)는 그대로 위에 남습니다.
결과 = 대상 포즈의 리그 + Σ 비율 × (소스 스컬프트 − 소스 포즈의 리그)"""

    bl_idname = "hb.facial_shape_mix"
    bl_label = "섞어서 시작"
    bl_options = {'REGISTER', 'UNDO'}

    target: bpy.props.EnumProperty(name="대상", items=_pose_items)
    make_mesh: bpy.props.BoolProperty(
        name="메쉬가 없으면 만들기", default=True,
        description="대상의 편집 메쉬를 지웠다면 새로 만듭니다")
    src0: bpy.props.EnumProperty(name="소스 1", items=_pose_items)
    src1: bpy.props.EnumProperty(name="소스 2", items=_pose_items)
    src2: bpy.props.EnumProperty(name="소스 3", items=_pose_items)
    src3: bpy.props.EnumProperty(name="소스 4", items=_pose_items)
    w0: bpy.props.FloatProperty(name="비율", default=1.0, soft_min=-2.0, soft_max=2.0, step=5)
    w1: bpy.props.FloatProperty(name="비율", default=1.0, soft_min=-2.0, soft_max=2.0, step=5)
    w2: bpy.props.FloatProperty(name="비율", default=1.0, soft_min=-2.0, soft_max=2.0, step=5)
    w3: bpy.props.FloatProperty(name="비율", default=1.0, soft_min=-2.0, soft_max=2.0, step=5)

    @classmethod
    def poll(cls, context):
        props = context.scene.hb_facial
        return (_poll(context) and pose_keys_available(props.mesh)
                and len(props.mesh.data.pose_keys) > 0 and sculpt_object() is None)

    def invoke(self, context, event):
        props = context.scene.hb_facial
        names = _pose_key_names(context, props)
        valid = {it[0] for it in _pose_items(self, context)}
        if names and names[0] in valid:
            self.target = names[0]
            o = next((pk.storage_object for pk in props.mesh.data.pose_keys
                      if pk.name == names[0]), None)
            try:
                recipe = json.loads(o.get(MIX_PROP, "[]")) if o is not None else []
            except (TypeError, ValueError):
                recipe = []
            for i in range(MIX_SLOTS):
                s, w = recipe[i] if i < len(recipe) else ('NONE', 1.0)
                if s in valid:
                    setattr(self, f"src{i}", s)
                    setattr(self, f"w{i}", w)
        return context.window_manager.invoke_props_dialog(self, width=420)

    def draw(self, context):
        col = self.layout.column()
        col.prop(self, "target")
        col.prop(self, "make_mesh")
        col.separator()
        for i in range(MIX_SLOTS):
            row = col.row(align=True)
            row.prop(self, f"src{i}", text="")
            sub = row.row(align=True)
            sub.scale_x = 0.5
            sub.prop(self, f"w{i}", text="")
        col.label(text="비율 -1 = 뒤집기 (예: rz+ 를 rz− ×-1 에서 시작)", icon='INFO')

    def execute(self, context):
        props = context.scene.hb_facial
        arm = O.facial_rig(context)
        if self.target == 'NONE':
            self.report({'WARNING'}, "대상을 고르세요")
            return {'CANCELLED'}
        pk = next((p for p in props.mesh.data.pose_keys if p.name == self.target), None)
        made = 0
        if pk is not None and pk.storage_object is None:
            if not self.make_mesh:
                self.report({'WARNING'}, f"{self.target}: 편집 메쉬 없음")
                return {'CANCELLED'}
            _n, made, _notes = setup_pose_keys(context, arm, props, recreate={self.target})
        sources = [(getattr(self, f"src{i}"), getattr(self, f"w{i}")) for i in range(MIX_SLOTS)]
        sources = [(s, w) for s, w in sources if s != 'NONE']
        try:
            notes = mix_edit_meshes(context, arm, props, self.target, sources)
        except ValueError as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        msg = f"{pretty_name(self.target)} = " + " + ".join(
            f"{pretty_name(s)}×{w:g}" for s, w in sources if s != self.target)
        if made:
            notes.insert(0, "편집 메쉬 새로 만듦")
        if notes:
            msg += "; " + "; ".join(notes)
        self.report({'INFO'}, msg)
        return {'FINISHED'}


class HB_OT_facial_shape_brow_work(bpy.types.Operator):
    """양쪽 눈썹이 한 메쉬에 있는 작업 메쉬를 만듭니다 (Storm의 brows_x_neg.001).
'Brow_all_y_neg' / 'Brow_all_x_neg' 쉐이프키가 들어 있고 정중앙 기준 좌우 대칭입니다
(편집 메쉬의 왼쪽 모양 + 그 미러). X 미러가 켜져 있어 양쪽을 같이 스컬프트합니다.
눈썹 코렉티브들과 함께 눈썹 블록 옆으로 배치됩니다"""

    bl_idname = "hb.facial_shape_brow_work"
    bl_label = "눈썹 y−·x− 작업 메쉬"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        props = context.scene.hb_facial
        return (_poll(context) and pose_keys_available(props.mesh)
                and len(props.mesh.data.pose_keys) > 0 and sculpt_object() is None)

    def execute(self, context):
        props = context.scene.hb_facial
        obj, notes = build_brow_work(context, O.facial_rig(context), props)
        msg = f"{obj.name}" + ("; " + "; ".join(notes) if notes else ": 만들고 눈썹 코렉티브 옆에 배치")
        self.report({'WARNING'} if notes else {'INFO'}, msg)
        return {'FINISHED'}


class HB_OT_facial_shape_brow_push(bpy.types.Operator):
    """눈썹 작업 메쉬의 키를 눈썹 편집 메쉬로 보냅니다: 'Brow_all_y_neg' -> 눈썹 all y−,
'Brow_all_x_neg' -> 눈썹 all x− (좌우 대칭으로 맞춘 뒤, 편집 메쉬의 'Work' 레이어).
얼굴에 등록하면 왼쪽이 .L 키, 그 미러가 .R 키가 됩니다"""

    bl_idname = "hb.facial_shape_brow_push"
    bl_label = "작업 메쉬 → 눈썹 키"
    bl_options = {'REGISTER', 'UNDO'}

    do_register: bpy.props.BoolProperty(name="얼굴에 등록까지", default=True)

    @classmethod
    def poll(cls, context):
        return _poll(context) and bool(work_objects()) and sculpt_object() is None

    def execute(self, context):
        props = context.scene.hb_facial
        try:
            done, notes = push_brow_work(context, O.facial_rig(context), props, self.do_register)
        except ValueError as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        msg = f"{len(done)}개 보냄" + (" + 등록" if self.do_register and done else "")
        if notes:
            msg += "; " + "; ".join(notes[:3])
        self.report({'INFO'}, msg)
        return {'FINISHED'}


def _place_rz(context, props, names):
    """New rz edit meshes in their brow block cell (the column after all z),
    if nothing is there; else they stay where they were put."""
    pks = {pk.name: pk.storage_object for pk in props.mesh.data.pose_keys}
    ax, ay = pks.get("SK-Brow_all_x_neg"), pks.get("SK-Brow_all_y_neg")
    if ax is None or ay is None:
        return
    dx = ay.location.x - ax.location.x
    context.view_layer.update()

    for name in names:
        o = pks.get(name)
        z = pks.get("SK-Brow_all_z_" + name[-3:])
        if o is None or z is None:
            continue
        at = z.location + Vector((dx, 0.0, 0.0))
        r = world_rect(o, at)
        free = True
        for b in bpy.data.objects:
            if b.type != 'MESH' or b == o or not b.visible_get():
                continue
            q = world_rect(b)
            if r[0] < q[1] and q[0] < r[1] and r[2] < q[3] and q[2] < r[3]:
                free = False
                break
        if free:
            o.location = at


class HB_OT_facial_shape_brow_rz(bpy.types.Operator):
    """눈썹 rz−/rz+ 편집 메쉬를 만들고(없으면) 눈썹 in / mid y+− 메쉬의 조합으로
시작 모양을 넣습니다 (Storm 최종 키에서 구한 조합식, 'Mix' 레이어):
rz− = in y− ×0.84, in y+ ×-0.11, mid y+ ×-0.11, mid y− ×-0.04
rz+ = in y+ ×0.59, mid y− ×0.15, in y− ×-0.08, mid y+ ×-0.04
비율은 '섞어서 시작'에서 바꿀 수 있습니다. New Changes(스컬프트)는 위에 남습니다"""

    bl_idname = "hb.facial_shape_brow_rz"
    bl_label = "눈썹 rz 메쉬 (in/mid y 조합)"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        props = context.scene.hb_facial
        return (_poll(context) and pose_keys_available(props.mesh)
                and len(props.mesh.data.pose_keys) > 0 and sculpt_object() is None)

    def execute(self, context):
        props = context.scene.hb_facial
        arm = O.facial_rig(context)
        names = {pk.name: pk for pk in props.mesh.data.pose_keys}
        todo = [t for t in BROW_RZ_RECIPES if t in names]
        if not todo:
            self.report({'WARNING'}, "이 리그에 눈썹 rz 포즈 키가 없습니다")
            return {'CANCELLED'}
        missing = {t for t in todo if names[t].storage_object is None}
        if missing:
            setup_pose_keys(context, arm, props, recreate=missing)
            _place_rz(context, props, missing)
        done, notes = [], []
        for t in todo:
            try:
                notes += mix_edit_meshes(context, arm, props, t, BROW_RZ_RECIPES[t])
                done.append(pretty_name(t))
            except ValueError as e:
                notes.append(str(e))
        msg = f"{', '.join(done)}: in/mid y 조합으로 시작" + (f" (메쉬 {len(missing)}개 새로 만듦)" if missing else "")
        if notes:
            msg += "; " + "; ".join(dict.fromkeys(notes))
        self.report({'INFO'}, msg)
        return {'FINISHED'}


class HB_OT_facial_shape_place_brow_corr(bpy.types.Operator):
    """눈썹 코렉티브(Furrow / Brows_Ty / Brows_rx / Brow_Cheek)와 눈썹 작업 메쉬를
눈썹 블록 왼쪽 옆으로 옮깁니다. 다른 편집 메쉬는 그대로 둡니다"""

    bl_idname = "hb.facial_shape_place_brow_corr"
    bl_label = "눈썹 코렉티브 옆으로"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        props = context.scene.hb_facial
        return _poll(context) and pose_keys_available(props.mesh)

    def execute(self, context):
        n = place_brow_correctives(context, context.scene.hb_facial)
        self.report({'INFO'}, f"{n}개 배치")
        return {'FINISHED'}


_classes = (HB_OT_facial_shape_keys, HB_OT_facial_shape_transfer, HB_OT_facial_shape_clear,
            HB_OT_facial_shape_edit, HB_OT_facial_shape_apply, HB_OT_facial_shape_cancel,
            HB_OT_facial_shape_mirror, HB_OT_facial_shape_pose_keys, HB_OT_facial_shape_pose_clear,
            HB_OT_facial_shape_pose_refresh, HB_OT_facial_shape_pose_empty, HB_OT_facial_shape_pose_add,
            HB_OT_facial_shape_copy_weights, HB_OT_facial_shape_copy_keys,
            HB_OT_facial_shape_register, HB_OT_facial_shape_corr_rebase,
            HB_OT_facial_shape_corr_register, HB_OT_facial_shape_mix,
            HB_OT_facial_shape_brow_work, HB_OT_facial_shape_brow_push,
            HB_OT_facial_shape_place_brow_corr, HB_OT_facial_shape_brow_rz)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
