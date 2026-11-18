"""Storm's face lattices, masks, ear weights and corrective smooth.

data/storm_lattice.json (data/extract_storm_lattice.py) holds storm's nine
face lattices with their hook bones, the MSK-* mask weights and the ear
weights on storm's head mesh. Build:

1. storm's landmarks are warped onto ours (thin-plate spline, as the face
   weights); each skin lattice is that warp applied to storm's lattice box
   (center + axes, kept a regular grid). Teeth lattices fit the teeth mesh
   bounds the way storm's fit its teeth.
2. Hook bones (DEF-Facemask_upp/low, DEF-Eye_upp/low) sit on the centre of
   the lattice points they pull; the lattices ride their parent bone with an
   Armature constraint and are hooked to their bones (storm).
3. The face mesh gets the MSK-* groups (sampled from storm's head) and a
   Lattice modifier per lattice masked by them, plus storm's Corrective
   Smooth; eyes (and their shrinkwrap guides) and teeth / gums get theirs.
4. Ear weights are sampled from storm and blended into the existing weights.
"""

import json
import os

import bpy
from mathutils import Matrix, Vector
from mathutils.bvhtree import BVHTree
from mathutils.interpolate import poly_3d_calc

from . import operators as O

DATA_FILE = "storm_lattice.json"
LTC_TAG = "FACE_LATTICE"
LTC_COLL = "Facial_Lattice"
LTC_MOD = "LTC-"                  # modifier name prefix on the deformed meshes
CS_NAME = "FACE-CorrectiveSmooth"
HOOK_COLL = "Facial_Lattice"      # bone collection of the hook bones
_DATA = None


def data():
    global _DATA
    if _DATA is None:
        with open(os.path.join(os.path.dirname(__file__), "data", DATA_FILE), encoding="utf-8") as f:
            _DATA = json.load(f)
    return _DATA


def _W():
    from . import weights
    return weights


def _R():
    from . import rig
    return rig


# ----------------------------------------------------------------------
# Storm -> ours
# ----------------------------------------------------------------------
def storm_warp(arm, props):
    """(warp, storm head data) mapping storm world space onto this face."""
    W = _W()
    with open(os.path.join(W.DATA_DIR, W.STORM_FILE), encoding="utf-8") as f:
        sd = json.load(f)
    lines = W._lines(arm, props)
    ours = W._our_landmarks(arm, props, lines)
    keys = [k for k in sd["landmarks"] if k in ours]
    if len(keys) < 12:
        return None, sd
    warp = W._tps([sd["landmarks"][k] for k in keys], [tuple(ours[k]) for k in keys])
    return warp, sd


def sample(mesh, arm, warp, sd, tables, max_rel=None):
    """{group: {vertex: w}} on mesh from storm head tables {group: [[vi, w]]}."""
    W = _W()
    verts = [Vector(v) for v in warp(sd["verts"])]
    polys = sd["polys"]
    bvh = BVHTree.FromPolygons(verts, polys)
    per = [dict() for _ in verts]
    for g, rows in tables.items():
        for vi, w in rows:
            per[vi][g] = w
    k = _R()._eye_dist(arm, 1.0)
    max_d = (max_rel or W.MAX_DIST) * k
    mw = mesh.matrix_world
    nrm = mw.to_3x3().inverted().transposed()
    out = {g: {} for g in tables}
    for v in mesh.data.vertices:
        co = mw @ v.co
        loc, pn, fi, _d = bvh.find_nearest(co, max_d)
        if loc is None or (nrm @ v.normal).normalized().dot(pn) < W.MIN_FACING:
            continue
        poly = polys[fi]
        bary = poly_3d_calc([verts[i] for i in poly], loc)
        acc = {}
        for vi, bw in zip(poly, bary):
            if bw > 0:
                for g, w in per[vi].items():
                    acc[g] = acc.get(g, 0.0) + bw * w
        for g, w in acc.items():
            if w > 0.002:
                out[g][v.index] = min(1.0, w)
    return out


def _smooth_field(mesh, field, iters=2):
    """Blend a {vertex: w} field with its edge ring (soft mask borders)."""
    nb = _W()._neighbors(mesh)
    w = dict(field)
    for _ in range(iters):
        region = set(w) | {j for i in w for j in nb[i]}
        nxt = {}
        for i in region:
            ring = [w.get(j, 0.0) for j in nb[i]]
            x = 0.5 * w.get(i, 0.0) + 0.5 * (sum(ring) / len(ring) if ring else 0.0)
            if x > 0.002:
                nxt[i] = x
        w = nxt
    return w


def _fill_pockets(mesh, nb, slots, field):
    """The mouth / eye pockets (facing away, never sampled) take the mask of
    their loop, ring by ring, so the lattice moves the lid / lip edge and
    what is behind it together."""
    W = _W()
    out = dict(field)
    for slot, loop in slots.items():
        pocket = W._pockets(mesh, nb, {slot: loop})
        front, seen = set(loop), set(loop)
        while front:
            nxt = {j for i in front for j in nb[i] if j in pocket and j not in seen}
            for j in nxt:
                src = [out.get(k, 0.0) for k in nb[j] if k in seen]
                x = sum(src) / len(src) if src else 0.0
                if x > 0.002:
                    out[j] = x
                else:
                    out.pop(j, None)
            seen |= nxt
            front = nxt
    return out


# ----------------------------------------------------------------------
# Lattice placement
# ----------------------------------------------------------------------
def _ortho(axes):
    """Gram-Schmidt on three axis vectors, keeping their lengths."""
    x, y, z = (Vector(a) for a in axes)
    lx, ly, lz = x.length, y.length, z.length
    xn = x.normalized()
    yn = (y - xn * y.dot(xn)).normalized()
    zn = xn.cross(yn)
    if zn.dot(z) < 0:
        zn = -zn
    return xn * lx, yn * ly, zn * lz


def _warp_matrix(M, warp):
    """storm lattice matrix -> ours through the warp (center + half axes)."""
    c = M.translation
    pts = [c]
    for i in range(3):
        a = M.col[i].to_3d() * 0.5
        pts += [c + a, c - a]
    w = [Vector(p) for p in warp([tuple(p) for p in pts])]
    axes = [w[1 + 2 * i] - w[2 + 2 * i] for i in range(3)]
    x, y, z = _ortho(axes)
    m = Matrix.Identity(4)
    for i, a in enumerate((x, y, z)):
        m.col[i][:3] = a
    m.translation = w[0]
    return m


def _bounds(objs):
    pts = [o.matrix_world @ v.co for o in objs for v in o.data.vertices]
    if not pts:
        return None
    return (Vector([min(p[i] for p in pts) for i in range(3)]),
            Vector([max(p[i] for p in pts) for i in range(3)]))


def _fit_matrix(M, storm_bb, ours_bb):
    """Storm lattice relative to storm's teeth bounds -> our teeth bounds."""
    smin, smax = (Vector(v) for v in storm_bb)
    omin, omax = ours_bb
    ssize, osize = smax - smin, omax - omin
    sc = Vector([osize[i] / ssize[i] if ssize[i] > 1e-6 else 1.0 for i in range(3)])
    rel = M.translation - (smin + smax) * 0.5
    m = M.copy()
    m.translation = (omin + omax) * 0.5 + Vector([rel[i] * sc[i] for i in range(3)])
    for i in range(3):
        col = m.col[i].to_3d()
        m.col[i][:3] = Vector([col[j] * sc[j] for j in range(3)])
    return m


def _gums(teeth, others):
    """Meshes named like gums that sit with this teeth mesh."""
    if teeth is None:
        return []
    c = sum((teeth.matrix_world @ Vector(b) for b in teeth.bound_box), Vector()) / 8
    out = []
    for o in bpy.data.objects:
        if o.type != 'MESH' or "gum" not in o.name.lower():
            continue
        oc = sum((o.matrix_world @ Vector(b) for b in o.bound_box), Vector()) / 8
        d_self = (oc - c).length
        if all(d_self <= (oc - sum((t.matrix_world @ Vector(b) for b in t.bound_box), Vector()) / 8).length
               for t in others if t is not None):
            out.append(o)
    return out


def targets(props, role):
    """Meshes a lattice role deforms."""
    if role == "face":
        return [props.mesh] if props.mesh else []
    if role in ("eye.L", "eye.R"):
        eye = props.eye_mesh_l if role == "eye.L" else props.eye_mesh_r
        if eye is None:
            return []
        out = [eye]
        for m in eye.modifiers:              # the shrinkwrap guide must follow too
            if m.type == 'SHRINKWRAP' and m.target is not None and m.target not in out:
                out.append(m.target)
        return out + [o for o in eye_parts(props, eye) if o not in out]
    if role == "skin":                      # meshes lying on the face (brows, lashes)
        return skin_meshes(props)
    if role in ("teeth_upp", "teeth_low"):
        teeth = props.teeth_upp_mesh if role == "teeth_upp" else props.teeth_low_mesh
        other = props.teeth_low_mesh if role == "teeth_upp" else props.teeth_upp_mesh
        return ([teeth] if teeth else []) + _gums(teeth, [other])
    return []


def _center(o):
    return sum((o.matrix_world @ Vector(b) for b in o.bound_box), Vector()) / 8


def eye_parts(props, eye):
    """Other meshes of an eyeball (cornea, iris...): centred inside its bounds."""
    lo = Vector([min((eye.matrix_world @ Vector(b))[i] for b in eye.bound_box) for i in range(3)])
    hi = Vector([max((eye.matrix_world @ Vector(b))[i] for b in eye.bound_box) for i in range(3)])
    skip = {props.mesh, props.eye_mesh_l, props.eye_mesh_r, props.brow_mesh,
            props.teeth_upp_mesh, props.teeth_low_mesh, props.tongue_mesh} | set(skin_meshes(props))
    pad = Vector((0.001,) * 3)
    lo, hi = lo - pad, hi + pad
    out = []
    for o in bpy.data.objects:
        if o.type != 'MESH' or o in skip or o.get(O.OBJ_PROP) or o.name.startswith(("WGT", "MK-")):
            continue
        if any(m.type == 'SHRINKWRAP' and m.target == o for m in eye.modifiers):
            continue                          # the guide is added by the caller
        corners = [o.matrix_world @ Vector(b) for b in o.bound_box]
        if all(lo[i] <= c[i] <= hi[i] for c in corners for i in range(3)):   # wholly inside
            out.append(o)
    return out


def eye_weights(arm, props):
    """Eyeballs (and their cornea / guide) ride their DEF-Eye bone."""
    done = []
    for side, role in ((".L", "eye.L"), (".R", "eye.R")):
        bone = f"DEF-Eye{side}"
        if bone not in arm.data.bones:
            continue
        for o in targets(props, role):
            if any(g.name.startswith("DEF-") and g.name != bone for g in o.vertex_groups):
                continue                      # weighted by hand to other bones: keep
            vg = o.vertex_groups.get(bone) or o.vertex_groups.new(name=bone)
            vg.add(list(range(len(o.data.vertices))), 1.0, 'REPLACE')
            _ensure_armature(o, arm)
            done.append(o.name)
    return done


def _ensure_armature(o, arm):
    m = next((m for m in o.modifiers if m.type == 'ARMATURE'), None)
    if m is None:
        m = o.modifiers.new("Armature", 'ARMATURE')
        with bpy.context.temp_override(object=o):
            bpy.ops.object.modifier_move_to_index(modifier=m.name, index=0)
    m.object = arm
    return m


ROOT_DIST = 0.0003      # m: a vertex this close to the face skin is a root


def skin_meshes(props):
    """Meshes lying on the face: the Brow mesh and eyelashes (by name)."""
    out = [props.brow_mesh] if props.brow_mesh and props.brow_mesh != props.mesh else []
    for o in bpy.data.objects:
        if o.type == 'MESH' and "lash" in o.name.lower() and o not in out and not o.get(O.OBJ_PROP):
            out.append(o)
    return out


SURF_NAME = "FACE-SurfaceDeform"


def surface_bind(context, arm, props, obj):
    """Bind obj to the face mesh with a Surface Deform modifier (bound in the
    rest pose). It replaces obj's Armature / lattice modifiers: the face
    carries everything, shape keys included. Returns True when bound."""
    face = props.mesh
    for m in [m for m in obj.modifiers if m.type in ('ARMATURE', 'SURFACE_DEFORM')
              or (m.type == 'LATTICE' and m.name.startswith(LTC_MOD))]:
        obj.modifiers.remove(m)
    mod = obj.modifiers.new(SURF_NAME, 'SURFACE_DEFORM')
    mod.target = face
    with context.temp_override(object=obj, active_object=obj):
        bpy.ops.object.modifier_move_to_index(modifier=mod.name, index=0)
    # bind on the rest shape: rest pose, shape keys off
    saved = {pb.name: (tuple(pb.location), tuple(pb.rotation_quaternion), tuple(pb.rotation_euler),
                       tuple(pb.scale)) for pb in arm.pose.bones}
    for pb in arm.pose.bones:
        pb.location, pb.rotation_quaternion = (0, 0, 0), (1, 0, 0, 0)
        pb.rotation_euler, pb.scale = (0, 0, 0), (1, 1, 1)
    context.view_layer.update()
    try:
        with context.temp_override(object=obj, active_object=obj):
            bpy.ops.object.surfacedeform_bind(modifier=mod.name)
    except RuntimeError:
        pass
    for pb in arm.pose.bones:
        pb.location, pb.rotation_quaternion, pb.rotation_euler, pb.scale = saved[pb.name]
    context.view_layer.update()
    if not mod.is_bound:
        obj.modifiers.remove(mod)
        return False
    return True


def skin_weights(arm, props, obj):
    """A mesh lying on the face (eyebrows) takes the face's deform and mask
    weights at its closest face point (interpolated inside the polygon)."""
    face = props.mesh
    fme = face.data
    fmw = face.matrix_world
    verts = [fmw @ v.co for v in fme.vertices]
    polys = [list(p.vertices) for p in fme.polygons]
    bvh = BVHTree.FromPolygons(verts, polys)
    deform = {b.name for b in arm.data.bones if b.use_deform}
    names = {g.index: g.name for g in face.vertex_groups
             if g.name in deform or g.name.startswith("MSK-")}
    per = [{names[g.group]: g.weight for g in v.groups if g.group in names} for v in fme.vertices]
    for g in [g for g in obj.vertex_groups if g.name in deform or g.name.startswith("MSK-")]:
        obj.vertex_groups.remove(g)
    groups = {}
    omw = obj.matrix_world
    pos = [omw @ v.co for v in obj.data.vertices]
    near = [bvh.find_nearest(p) for p in pos]
    # roots: the vertices on the skin. Lash tips (off the skin) copy their
    # nearest root so a strand moves as one with the lid it grows from.
    far = max((n[3] for n in near if n[0] is not None), default=0.0)
    root = [n[0] is not None and n[3] <= max(ROOT_DIST, 0.15 * far) for n in near]
    roots = [i for i, r in enumerate(root) if r] or list(range(len(pos)))
    for v in obj.data.vertices:
        src = v.index if root[v.index] else min(roots, key=lambda j: (pos[j] - pos[v.index]).length_squared)
        loc, _n, fi, _d = near[src]
        if loc is None:
            continue
        poly = polys[fi]
        acc = {}
        for vi, bw in zip(poly, poly_3d_calc([verts[i] for i in poly], loc)):
            if bw > 0:
                for g, w in per[vi].items():
                    acc[g] = acc.get(g, 0.0) + bw * w
        tot = sum(w for g, w in acc.items() if g in deform) or 1.0
        for g, w in acc.items():
            if w < 0.002:
                continue
            if g not in groups:
                groups[g] = obj.vertex_groups.new(name=g)
            groups[g].add([v.index], w / tot if g in deform else min(1.0, w), 'REPLACE')
    _ensure_armature(obj, arm)
    return len(groups)


# ----------------------------------------------------------------------
# Build
# ----------------------------------------------------------------------
def clear(arm):
    """Remove the lattices, their modifiers and the hook bones' constraints."""
    for o in bpy.data.objects:
        if o.type == 'MESH':
            for m in [m for m in o.modifiers if (m.type == 'LATTICE' and m.name.startswith(LTC_MOD))
                      or m.name == CS_NAME]:
                o.modifiers.remove(m)
    for o in [o for o in bpy.data.objects if o.get(O.OBJ_PROP) == LTC_TAG]:
        lt = o.data
        bpy.data.objects.remove(o, do_unlink=True)
        if lt is not None and lt.users == 0:
            bpy.data.lattices.remove(lt)


def _hook_bones(context, arm, specs):
    """Create / move the hook bones: {name: (head, parent)} in world space."""
    k = _R()._eye_dist(arm, 1.0) / data()["eye_dist"]
    imw = arm.matrix_world.inverted()
    prev = context.view_layer.objects.active
    mode = arm.mode
    context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode='EDIT')
    ebs = arm.data.edit_bones
    coll = arm.data.collections_all.get(HOOK_COLL) or arm.data.collections.new(HOOK_COLL)
    made = []
    for name, (head, parent, length) in specs.items():
        eb = ebs.get(name) or ebs.new(name)
        h = imw @ head
        eb.head = h
        eb.tail = h + (imw.to_3x3() @ Vector((0, 0, 1))).normalized() * (length * k)
        eb.align_roll(imw.to_3x3() @ Vector((0, -1, 0)))
        eb.parent = ebs.get(parent)
        eb.use_deform = False
        coll.assign(eb)
        made.append(name)
    bpy.ops.object.mode_set(mode='OBJECT')
    for n in made:
        arm.data.bones[n][O.GEN_PROP] = "LATTICE"
    if mode == 'POSE':
        bpy.ops.object.mode_set(mode='POSE')
    context.view_layer.objects.active = prev
    return made


def _new_lattice(context, name, spec, matrix):
    lt = bpy.data.lattices.new(name)
    lt.points_u, lt.points_v, lt.points_w = spec["size"]
    lt.interpolation_type_u, lt.interpolation_type_v, lt.interpolation_type_w = spec["interp"]
    lt.use_outside = spec["outside"]
    o = bpy.data.objects.new(name, lt)
    o[O.OBJ_PROP] = LTC_TAG
    coll = bpy.data.collections.get(LTC_COLL)
    if coll is None:
        coll = bpy.data.collections.new(LTC_COLL)
        context.scene.collection.children.link(coll)
    coll.objects.link(o)
    o.matrix_world = matrix
    o.display_type = 'WIRE'
    return o


def _rest_matrix(arm, bone):
    # rest, not the current pose: the user may be posing while this runs
    return arm.matrix_world @ arm.data.bones[bone].matrix_local


MIRROR_X = Matrix.Diagonal((-1.0, 1.0, 1.0, 1.0))


def _symmetrize(mats):
    """.R boxes mirror their .L; centre boxes sit on x = 0."""
    for name in list(mats):
        if name.endswith(".L") and name[:-2] + ".R" in mats:
            mats[name[:-2] + ".R"] = MIRROR_X @ mats[name]
        elif not name.endswith((".L", ".R")):
            mats[name].translation.x = 0.0


def build(context, arm, props, with_ears=True):
    """Lattices + masks + corrective smooth + ear weights. Returns notes."""
    d = data()
    notes = []
    warp, sd = storm_warp(arm, props)
    if warp is None:
        return ["래티스: 랜드마크 부족 (루프를 등록하고 리그를 생성하세요)"]
    clear(arm)
    bones = arm.data.bones
    for b in bones:                       # storm: tooth bones pull the lattice only
        if b.name.startswith("DEF-TEETH"):
            b.use_deform = False
        elif b.name.startswith("MSTR-TEETH"):
            b.use_deform = True
    teeth_weights(arm, props)

    # --- lattice boxes
    mats = {}
    for spec in d["lattices"]:
        M = Matrix(spec["matrix"])
        if spec["fit"] == "warp":
            mats[spec["name"]] = _warp_matrix(M, warp)
        else:
            objs = [o for o in targets(props, spec["fit"])]
            teeth = props.teeth_upp_mesh if spec["fit"] == "teeth_upp" else props.teeth_low_mesh
            bb = _bounds([teeth]) if teeth else None
            if bb is None:
                notes.append(f"{spec['name']}: {spec['fit']} 메쉬 없음")
                continue
            mats[spec["name"]] = _fit_matrix(M, spec["fit_bbox"], bb)
    _symmetrize(mats)

    # --- hook bones on the centre of the points they pull
    def point_world(m, size, idx):
        u, v, w = size
        i, j, kk = idx % u, (idx // u) % v, idx // (u * v)
        loc = Vector([(i / (u - 1) - 0.5) if u > 1 else 0.0,
                      (j / (v - 1) - 0.5) if v > 1 else 0.0,
                      (kk / (w - 1) - 0.5) if w > 1 else 0.0])
        return m @ loc

    specs = {}
    for spec in d["lattices"]:
        if spec["name"] not in mats:
            continue
        for h in spec["hooks"]:
            b = d["bones"].get(h["bone"])
            if b is None:
                continue
            pts = [point_world(mats[spec["name"]], spec["size"], i) for i in h["points"]]
            c = sum(pts, Vector()) / len(pts)
            length = (Vector(b["tail"]) - Vector(b["head"])).length
            specs[h["bone"]] = (c, b["parent"], length)
    missing_parent = [n for n, (_c, p, _l) in specs.items() if p not in bones]
    if missing_parent:
        notes.append(f"래티스: {missing_parent}의 부모 없음")
    _hook_bones(context, arm, specs)
    _shapes(context, arm, list(specs))

    # --- lattices, constraint + hooks
    lats = {}
    for spec in d["lattices"]:
        name = spec["name"]
        if name not in mats:
            continue
        hooks = [h for h in spec["hooks"] if h["bone"] in arm.data.bones]
        if len(hooks) < len(spec["hooks"]):
            notes.append(f"{name}: 훅 본 없음")
        o = _new_lattice(context, name, spec, mats[name])
        o.parent = arm
        o.matrix_world = mats[name]
        if spec["parent"] in arm.data.bones:
            c = o.constraints.new('ARMATURE')
            c.name = "Armature"
            t = c.targets.new()
            t.target, t.subtarget, t.weight = arm, spec["parent"], 1.0
        imw = mats[name].inverted()           # rest placement (constraint adds the pose)
        for h in hooks:
            g = o.vertex_groups.new(name=h["group"])
            g.add(h["points"], 1.0, 'REPLACE')
            m = o.modifiers.new(f"Hook-{h['bone']}", 'HOOK')
            m.object, m.subtarget, m.vertex_group = arm, h["bone"], h["group"]
            m.falloff_type = 'NONE'
            m.matrix_inverse = (imw @ _rest_matrix(arm, h["bone"])).inverted()
        lats[name] = (o, spec)

    # --- masks on the face mesh
    mesh = props.mesh
    if mesh is not None:
        fields = sample(mesh, arm, warp, sd, d["masks"], max_rel=0.3)
        W = _W()
        nb = W._neighbors(mesh)
        _pins, slots = W._loop_pins(arm, mesh, W._lines(arm, props))
        for g, field in fields.items():
            field = _smooth_field(mesh, field, 1)
            field = _fill_pockets(mesh, nb, slots, field)
            vg = mesh.vertex_groups.get(g) or mesh.vertex_groups.new(name=g)
            vg.remove(list(range(len(mesh.data.vertices))))
            for vi, w in field.items():
                vg.add([vi], min(1.0, w), 'REPLACE')
        ear_notes = ears(mesh, arm, props, warp, sd) if with_ears else []
        notes += ear_notes

    # --- eyes (+ cornea / guide) ride DEF-Eye; brows / lashes copy the face skin
    eye_weights(arm, props)
    skins = []
    if mesh is not None:
        for s in skin_meshes(props):
            if s.mode == 'EDIT':
                notes.append(f"{s.name}: Edit Mode 상태라 건너뜀")
                continue
            # storm: the eyebrows ride the skin itself (Surface Deform), so they
            # also follow the shape keys; lashes keep the skin's weights
            if s == props.brow_mesh and surface_bind(context, arm, props, s):
                continue
            skin_weights(arm, props, s)
            skins.append(s)

    # --- lattice modifiers after the Armature modifier (storm order on the face)
    order = d["face_order"] + [s["name"] for s in d["lattices"] if s["name"] not in d["face_order"]]
    for name in order:
        if name not in lats:
            continue
        o, spec = lats[name]
        for role in spec["targets"]:
            objs = targets(props, role) + (skins if role == "face" else [])
            for t in objs:
                if role == "face" and spec["mask"] and t != mesh and spec["mask"] not in t.vertex_groups:
                    continue                  # a brow / lash outside this lattice's mask
                m = t.modifiers.new(LTC_MOD + name[len("LTC-"):], 'LATTICE')
                m.object = o
                if role == "face" and spec["mask"]:
                    m.vertex_group = spec["mask"]
                _place_after_deform(t, m)
    if mesh is not None:
        s = d["smooth"]
        cs = mesh.modifiers.new(CS_NAME, 'CORRECTIVE_SMOOTH')
        cs.factor, cs.iterations, cs.scale = s["factor"], s["iterations"], s["scale"]
        cs.smooth_type, cs.rest_source = s["smooth_type"], s["rest_source"]
        cs.use_only_smooth, cs.use_pin_boundary = s["use_only_smooth"], s["use_pin_boundary"]
        cs.vertex_group = s["mask"]
        _place_after_deform(mesh, cs)
    return notes


def _place_after_deform(obj, mod):
    """Move mod right after the Armature / lattice / smooth modifiers before it."""
    names = [m.name for m in obj.modifiers]
    idx = 0
    for i, m in enumerate(obj.modifiers):
        if m == mod:
            continue
        if m.type in ('ARMATURE', 'LATTICE', 'CORRECTIVE_SMOOTH') and i < names.index(mod.name):
            idx = i + 1
    with bpy.context.temp_override(object=obj):
        bpy.ops.object.modifier_move_to_index(modifier=mod.name, index=idx)


def _shapes(context, arm, names):
    """Storm's lattice handle shape on the hook bones."""
    R = _R()
    d = data()
    k = R._eye_dist(arm, 1.0)
    to_arm = arm.matrix_world.to_3x3().normalized().inverted()
    for n in names:
        spec = d["shapes"].get(n)
        pb = arm.pose.bones.get(n)
        if spec is None or pb is None:
            continue
        wd = d["widgets"].get(spec["wgt"])
        if wd is None:
            continue
        # own name: the file may already hold an unrelated "WGT-lattice"
        obj = R._wgt_object(context, f"WGT-HB_storm_{spec['wgt'][4:]}",
                            [tuple(v) for v in wd["verts"]], [tuple(e) for e in wd["edges"]])
        bone = pb.bone
        length = bone.length or 1.0
        inv = bone.matrix_local.to_3x3().normalized().inverted() @ to_arm
        pb.custom_shape = obj
        pb.use_custom_shape_bone_size = True
        pb.custom_shape_rotation_euler = (inv @ Matrix(spec["rot"])).to_euler('XYZ')
        pb.custom_shape_scale_xyz = [v * k / length for v in spec["size"]]
        pb.custom_shape_translation = inv @ (Vector(spec["offset_bone"]) * k)
        bone.show_wire = True
        if spec.get("color", "DEFAULT") != "DEFAULT":
            bone.color.palette = spec["color"]
        elif hasattr(R, "_color"):
            R._color(pb)


def teeth_weights(arm, props):
    """storm: teeth and gums ride MSTR-Teeth (the lattice does the rest)."""
    done = []
    for role, master in (("teeth_upp", "MSTR-TEETH_UPP"), ("teeth_low", "MSTR-TEETH_LOW")):
        if master not in arm.data.bones:
            continue
        for o in targets(props, role):
            for g in [g for g in o.vertex_groups if g.name.startswith(("DEF-TEETH", "MSTR-TEETH"))]:
                o.vertex_groups.remove(g)
            o.vertex_groups.new(name=master).add(list(range(len(o.data.vertices))), 1.0, 'REPLACE')
            m = next((m for m in o.modifiers if m.type == 'ARMATURE'), None)
            if m is None:
                m = o.modifiers.new("Armature", 'ARMATURE')
                with bpy.context.temp_override(object=o):
                    bpy.ops.object.modifier_move_to_index(modifier=m.name, index=0)
            m.object = arm
            done.append(o.name)
    return done


# ----------------------------------------------------------------------
# Ears
# ----------------------------------------------------------------------
def ears(mesh, arm, props, warp=None, sd=None):
    """Storm's ear weights blended into the face mesh (others scaled down)."""
    d = data()
    if warp is None:
        warp, sd = storm_warp(arm, props)
        if warp is None:
            return ["귀: 랜드마크 부족"]
    tables = {g: rows for g, rows in d["ears"].items() if g in arm.data.bones}
    if not tables:
        return ["귀: DEF-Ear 본 없음"]
    fields = sample(mesh, arm, warp, sd, tables, max_rel=0.6)
    # each side: only that side's vertices (the warp is smooth, the ears are far apart)
    mw = mesh.matrix_world
    me = mesh.data
    ear = {}
    for g, field in fields.items():
        right = g.endswith(".R")
        for vi, w in field.items():
            x = (mw @ me.vertices[vi].co).x
            if (x < 0) == right:
                ear.setdefault(vi, {})[g] = w
    vg_ear = {g: (mesh.vertex_groups.get(g) or mesh.vertex_groups.new(name=g)) for g in tables}
    ear_idx = {g.index for g in vg_ear.values()}
    had = {v.index for v in me.vertices if any(g.group in ear_idx and g.weight > 0 for g in v.groups)}
    for g in vg_ear.values():
        g.remove(list(range(len(me.vertices))))
    gi = {g.index: g for g in mesh.vertex_groups}
    deform = {b.name for b in arm.data.bones if b.use_deform}
    # vertices that carried ear weight before (e.g. smoothed out from the ear)
    # but get none now: their other weights go back up to 1
    for vi in had - set(ear):
        rest = [(gi[g.group], g.weight) for g in me.vertices[vi].groups
                if g.group in gi and gi[g.group].name in deform and g.weight > 0]
        s = sum(w for _g, w in rest)
        if s > 0:
            for g, w in rest:
                g.add([vi], w / s, 'REPLACE')
        elif props.head_bone:
            hg = mesh.vertex_groups.get(props.head_bone) or mesh.vertex_groups.new(name=props.head_bone)
            hg.add([vi], 1.0, 'REPLACE')
    for vi, ws in ear.items():
        tot = min(1.0, sum(ws.values()))
        if tot <= 0.002:
            continue
        scale = sum(ws.values()) and tot / sum(ws.values())
        others = [(gi[g.group], g.weight) for g in me.vertices[vi].groups
                  if g.group in gi and gi[g.group].name in deform and gi[g.group].name not in tables
                  and g.weight > 0]
        osum = sum(w for _g, w in others)
        if osum > 0:
            for g, w in others:
                g.add([vi], w / osum * (1 - tot), 'REPLACE')
        elif props.head_bone and tot < 1.0:
            hg = mesh.vertex_groups.get(props.head_bone) or mesh.vertex_groups.new(name=props.head_bone)
            hg.add([vi], 1 - tot, 'REPLACE')
        for g, w in ws.items():
            vg_ear[g].add([vi], w * scale, 'REPLACE')
    return []


# ----------------------------------------------------------------------
# Operators
# ----------------------------------------------------------------------
class HB_OT_facial_build_lattices(bpy.types.Operator):
    """Storm 얼굴 래티스(facemask, 눈, 볼, cheek puff, 치아)와 훅 본, MSK- 마스크
그룹, 얼굴 Corrective Smooth, 귀 웨이트를 생성합니다"""

    bl_idname = "hb.facial_build_lattices"
    bl_label = "래티스"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return O.facial_rig(context) is not None and context.scene.hb_facial.mesh is not None

    def execute(self, context):
        props = context.scene.hb_facial
        arm = O.facial_rig(context)
        if props.mesh.mode == 'EDIT':
            self.report({'ERROR'}, f"먼저 {props.mesh.name}의 Edit Mode를 종료하세요")
            return {'CANCELLED'}
        notes = build(context, arm, props)
        n = len([o for o in bpy.data.objects if o.get(O.OBJ_PROP) == LTC_TAG])
        self.report({'WARNING'} if notes else {'INFO'},
                    f"래티스 {n}개 생성" + ("; " + "; ".join(notes) if notes else ""))
        return {'FINISHED'}


class HB_OT_facial_clear_lattices(bpy.types.Operator):
    """얼굴 래티스와 그 모디파이어를 삭제합니다 (훅 본은 유지)"""

    bl_idname = "hb.facial_clear_lattices"
    bl_label = "래티스 삭제"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        clear(O.facial_rig(context))
        return {'FINISHED'}


_classes = (HB_OT_facial_build_lattices, HB_OT_facial_clear_lattices)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
