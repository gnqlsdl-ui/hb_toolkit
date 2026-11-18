"""Brow / cheek shape keys built the studio way (rain / storm working files).

Per region (brow, cheek):

  bone meshes   one head per direction of the region's "all" control
                (x+ x- y+ y- z+ z-), each with its own
                small armature and an action: rest at frame 0, the pose at
                PEAK, rest again at 2 * PEAK. Scrub the timeline to see it,
                edit the PEAK key or repaint the weights to change the shape.
                brow: Bone_base > brow_main (pivot inside the skull) > brow
                (glabella), weights symmetric; y+ swings around the pivot.
                Shrinkwrap per direction: the forehead slides on the skull,
                the brow ridge never (keeps its bulge), the lid a little on y+,
                none when pushing into the skull
                cheek: Bone_base > cheek_root > Bone_cheek (left only)
  all .L        "Bones to all .L" puts each bone head at PEAK, left side only
                (the switch's MSK-L group), into the face's SK-<Region>_all_
                <dir>.L key (and mirrored .R); touch it up on that key's edit
                mesh in the face's all block and push it
  switch        one head whose keys are the face's all .L keys ("all .L to
                Switch"); one key on at a time picks the direction
  in / mid / out  geometry-node heads splitting the switch shape:
                in  = switch * In
                mid = switch * Mid - in        (Mid covers in + mid)
                out = switch - in - mid
                so in + mid + out is always the whole switch key.

Split to Keys writes every direction into the face's SK-<Region>_{in, mid,
out}_<dir>.L keys, mirrors them (and the all keys) into .R and refreshes the
in / mid / out edit meshes.

Layout: the bone heads sit left of the face's all edit block on its rows, each
in / mid / out head above its own edit block, the switch above mid.

All these heads are the face's head cut out (vertex attribute hb_src keeps
the face vertex index), so they share one topology.
"""

import bpy
import numpy as np
from mathutils import Quaternion, Vector

from . import operators as O
from . import shapekeys as SK

TAG = "SK_BONES"
COLL = "Facial_SK_Bones"
SRC_ATTR = "hb_src"
GN_GROUP = "HB-Linked_Shapekeys"
GN_SLOTS = 3
PEAK = 10                 # frame of the pose (0 and 2 * PEAK are rest)
PARTS = ("in", "mid", "out")
DIRS = ("x_pos", "x_neg", "y_pos", "y_neg", "z_pos", "z_neg")
SPLIT = {"in": (0.20, 0.45), "out": (0.55, 0.80)}     # t along the region: in fades out / out fades in
WEIGHT_GAIN = 2.0         # fallback deform weight = |storm draft delta| / max * gain (clamped)
PIVOT_DEPTH = 0.48        # brow_main sits this many head widths behind the glabella (inside the skull)
DEPTH_IN = 0.35           # pushing into the skull moves this much of the full value
# Shrinkwrap mask per direction (rain): heights t = (z - brow line) / eye distance
#   forehead  slides on the skull          brow ridge  never (keeps its bulge)
#   lid       a little, y+ only (stays on the eyeball while the brow lifts it)
SHRINK = {"x_pos": "all", "x_neg": "all",
          "y_pos": "forehead+lid", "y_neg": "forehead", "z_pos": "forehead_far"}   # z_neg: none
FOREHEAD_T = (0.08, 0.48)  # forehead mask 0 -> 1 between these heights
FOREHEAD_FAR_T = (0.30, 1.00)   # z+: the forehead lets the ridge come forward first
LID_T = (-0.20, -0.35, -0.75, -0.95)   # lid mask ramps in / full / ramps out (downwards)
LID_W = 0.3
REGIONS = {
    "brow": {"title": "Brow", "ctrl": "MSTR-BROW.L", "sym": True,
             "parent": "brow_main", "bone": "brow", "key": "brow_main",
             "dirs": DIRS, "arc": ("y_pos",),
             "ends": ("DEF-Brow_local1.L", "DEF-Brow_local7.L")},
    "cheek": {"title": "Cheek", "ctrl": "CTL-cheek_all.L", "sym": False,
              "parent": "cheek_root", "bone": "Bone_cheek", "key": "Bone_cheek",
              "dirs": DIRS, "arc": (),
              "ends": ("CTL-cheek_in.L", "CTL-cheek_out.L")},
}
MASKS = ("In", "Mid")
SMOOTH_ITERS = 10         # deform-weight drafts are smoothed over the mesh this many times
MIRROR = np.array([-1.0, 1.0, 1.0])


def key_name(region, part, d, side=".L"):
    return f"SK-{REGIONS[region]['title']}_{part}_{d}{side}"


def _tagged(kind=None, region=None):
    return [o for o in bpy.data.objects if o.get(O.OBJ_PROP) == TAG
            and (kind is None or o.get("hb_skb_kind") == kind)
            and (region is None or o.get("hb_skb_region") == region)]


def _coll(context):
    c = bpy.data.collections.get(COLL)
    if c is None:
        c = bpy.data.collections.new(COLL)
        context.scene.collection.children.link(c)
    return c


def _obj(context, name, data, kind, region, d=""):
    o = bpy.data.objects.new(name, data)
    _coll(context).objects.link(o)
    o[O.OBJ_PROP] = TAG
    o["hb_skb_kind"], o["hb_skb_region"], o["hb_skb_dir"] = kind, region, d
    return o


# ----------------------------------------------------------------------
# Head mesh (the face's head, cut out)
# ----------------------------------------------------------------------
def head_mesh(face, head):
    """New mesh: the face's rest head only, with face vertex indices in hb_src."""
    import bmesh
    me = SK._bare_copy(face)
    bm = bmesh.new()
    bm.from_mesh(me)
    keep = set(head)
    layer = bm.verts.layers.int.new(SRC_ATTR)
    for v in bm.verts:
        v[layer] = v.index
    bmesh.ops.delete(bm, geom=[v for v in bm.verts if v.index not in keep], context='VERTS')
    bm.to_mesh(me)
    bm.free()
    tmp = bpy.data.objects.new("SKB-tmp", me)     # (vertex group names live on the mesh)
    tmp.vertex_groups.clear()
    bpy.data.objects.remove(tmp)
    for g in list(me.attributes):
        if g.name.startswith("hb_") and g.name != SRC_ATTR:
            me.attributes.remove(g)
    me.name = "SKB-head"
    return me


def _src(me):
    a = me.attributes[SRC_ATTR]
    out = np.empty(len(me.vertices), dtype=np.int32)
    a.data.foreach_get("value", out)
    return out


def _co(me):
    out = np.empty(len(me.vertices) * 3)
    me.vertices.foreach_get("co", out)
    return out.reshape(-1, 3)


# ----------------------------------------------------------------------
# Masks and weights (drafts, to repaint by hand)
# ----------------------------------------------------------------------
def _smooth(x):
    x = np.clip(x, 0.0, 1.0)
    return x * x * (3 - 2 * x)


def face_rig(face):
    return next((m.object for m in face.modifiers if m.type == 'ARMATURE' and m.object), None)


def _left(face, x):
    """1 on the left (+x), 0 on the right, soft over the centre line."""
    half = SK.MASK_BLEND * SK._R()._eye_dist(face_rig(face), 1.0)
    return _smooth((x + half) / (2 * half))


def _mirror(face, src, w):
    """Head-vertex values mirrored across x = 0 (through the face's mirror map)."""
    n = len(face.data.vertices)
    full = np.zeros(n)
    full[src] = w
    mir = SK.mirror_map(face)
    to = np.fromiter(mir.keys(), int, len(mir))
    fr = np.fromiter(mir.values(), int, len(mir))
    out = np.zeros(n)
    out[to] = full[fr]
    return out[src]


def _part_masks(co, arm, face, spec):
    """In (inner part), Mid (inner + middle part), MSK-L / MSK-R (sides)."""
    imw = face.matrix_world.inverted() @ arm.matrix_world
    a, b = (imw @ arm.data.bones[n].head_local for n in spec["ends"])
    ax = b - a
    t = ((co - np.array(a)) @ np.array(ax)) / max(ax.length_squared, 1e-12)
    w_in = 1.0 - _smooth((t - SPLIT["in"][0]) / (SPLIT["in"][1] - SPLIT["in"][0]))
    w_out = _smooth((t - SPLIT["out"][0]) / (SPLIT["out"][1] - SPLIT["out"][0]))
    left = _left(face, co[:, 0])
    return {"In": w_in, "Mid": np.clip(1.0 - w_out, 0.0, 1.0)}


def _region_weight(face, src, region):
    """Left deform-weight draft: how far storm's draft 'all' keys move each vertex."""
    sk = face.data.shape_keys
    n = len(face.data.vertices)
    base = np.empty(n * 3)
    sk.reference_key.data.foreach_get("co", base)
    base = base.reshape(-1, 3)
    m = np.zeros(n)
    for d in DIRS:
        kb = sk.key_blocks.get(key_name(region, "all", d))
        if kb is None:
            continue
        c = np.empty(n * 3)
        kb.data.foreach_get("co", c)
        m = np.maximum(m, np.linalg.norm(c.reshape(-1, 3) - base, axis=1))
    m = m[src]
    top = m.max() if m.size and m.max() > 0 else 1.0
    w = np.clip(m / top * WEIGHT_GAIN, 0.0, 1.0)
    return w * _left(face, base[src, 0])


def _smooth_weights(me, w, iters=SMOOTH_ITERS, factor=0.5):
    """Average each vertex with its neighbours (drafts from storm keys are
    jagged at the brow edge and crease the bone heads)."""
    ed = np.empty(len(me.edges) * 2, dtype=np.int32)
    me.edges.foreach_get("vertices", ed)
    a, b = ed[0::2], ed[1::2]
    deg = np.bincount(a, minlength=len(w)) + np.bincount(b, minlength=len(w))
    deg = np.maximum(deg, 1)
    w = w.astype(float).copy()
    for _ in range(iters):
        s = np.bincount(a, weights=w[b], minlength=len(w)) + np.bincount(b, weights=w[a], minlength=len(w))
        w = w * (1.0 - factor) + (s / deg) * factor
    return np.clip(w, 0.0, 1.0)


def _set_group(obj, name, w, eps=1e-3):
    g = obj.vertex_groups.get(name) or obj.vertex_groups.new(name=name)
    g.remove(list(range(len(obj.data.vertices))))
    for val in np.unique(np.round(w, 3)):
        if val > eps:
            g.add([int(i) for i in np.nonzero(np.round(w, 3) == val)[0]], float(val), 'REPLACE')
    return g


# ----------------------------------------------------------------------
# Geometry nodes: rain's Linked_Shapekeys (three Object / Factor / Weight slots)
#   offset = sum_i (sample_i(position) - position) * weight_i * factor_i
# ----------------------------------------------------------------------
def _slot_names(i):
    return f"Object {i}", f"Factor {i}", f"Weight {i}"


def gn_group():
    ng = bpy.data.node_groups.get(GN_GROUP)
    if ng is not None and _slot_names(GN_SLOTS)[0] in {s.name for s in ng.interface.items_tree}:
        return ng
    if ng is not None:                       # the old one-slot group
        if ng.users:
            ng.name = GN_GROUP + "-old"
        else:
            bpy.data.node_groups.remove(ng)
    ng = bpy.data.node_groups.new(GN_GROUP, 'GeometryNodeTree')
    it = ng.interface
    it.new_socket("Geometry", in_out='INPUT', socket_type='NodeSocketGeometry')
    it.new_socket("Geometry", in_out='OUTPUT', socket_type='NodeSocketGeometry')
    for i in range(1, GN_SLOTS + 1):
        on, fn, wn = _slot_names(i)
        it.new_socket(on, in_out='INPUT', socket_type='NodeSocketObject')
        f = it.new_socket(fn, in_out='INPUT', socket_type='NodeSocketFloat')
        f.default_value = 1.0
        w = it.new_socket(wn, in_out='INPUT', socket_type='NodeSocketFloat')
        w.default_value = 0.0
    n, L = ng.nodes, ng.links
    gi, go = n.new('NodeGroupInput'), n.new('NodeGroupOutput')
    pos, idx = n.new('GeometryNodeInputPosition'), n.new('GeometryNodeInputIndex')
    setp = n.new('GeometryNodeSetPosition')
    gi.location, pos.location, idx.location = (-600, 0), (-600, -400), (-600, -500)
    total = None
    for i in range(1, GN_SLOTS + 1):
        on, fn, wn = _slot_names(i)
        y = -(i - 1) * 300
        info = n.new('GeometryNodeObjectInfo')
        info.transform_space = 'ORIGINAL'
        smp = n.new('GeometryNodeSampleIndex')
        smp.data_type, smp.domain = 'FLOAT_VECTOR', 'POINT'
        sub = n.new('ShaderNodeVectorMath')
        sub.operation = 'SUBTRACT'
        sc1, sc2 = n.new('ShaderNodeVectorMath'), n.new('ShaderNodeVectorMath')
        sc1.operation = sc2.operation = 'SCALE'
        for k, nd in enumerate((info, smp, sub, sc1, sc2)):
            nd.location = (-400 + k * 180, y)
        L.new(gi.outputs[on], info.inputs["Object"])
        L.new(info.outputs["Geometry"], smp.inputs["Geometry"])
        L.new(pos.outputs["Position"], smp.inputs["Value"])
        L.new(idx.outputs["Index"], smp.inputs["Index"])
        L.new(smp.outputs["Value"], sub.inputs[0])
        L.new(pos.outputs["Position"], sub.inputs[1])
        L.new(sub.outputs["Vector"], sc1.inputs[0])
        L.new(gi.outputs[wn], sc1.inputs["Scale"])
        L.new(sc1.outputs["Vector"], sc2.inputs[0])
        L.new(gi.outputs[fn], sc2.inputs["Scale"])
        if total is None:
            total = sc2.outputs["Vector"]
        else:
            add = n.new('ShaderNodeVectorMath')
            add.operation = 'ADD'
            add.location = (560, y)
            L.new(total, add.inputs[0])
            L.new(sc2.outputs["Vector"], add.inputs[1])
            total = add.outputs["Vector"]
    setp.location, go.location = (760, 0), (960, 0)
    L.new(gi.outputs["Geometry"], setp.inputs["Geometry"])
    L.new(total, setp.inputs["Offset"])
    L.new(setp.outputs["Geometry"], go.inputs["Geometry"])
    return ng


def _gn(obj, slots):
    """slots: [(object, factor, weight float or vertex-group name)], up to GN_SLOTS."""
    ng = gn_group()
    m = obj.modifiers.new("Linked Shape", 'NODES')
    m.node_group = ng
    ids = {s.name: s.identifier for s in ng.interface.items_tree
           if getattr(s, "in_out", "") == 'INPUT' and s.socket_type != 'NodeSocketGeometry'}
    ins = m.properties.inputs
    for i, (src, factor, weight) in enumerate(slots, 1):
        on, fn, wn = _slot_names(i)
        ins[ids[on]]["value"] = src
        ins[ids[fn]]["value"] = float(factor)
        if isinstance(weight, str):
            ins[ids[wn]]["type"] = 2                      # read the weight from an attribute
            ins[ids[wn]]["attribute_name"] = weight
        else:
            ins[ids[wn]]["value"] = float(weight)
    return m


# ----------------------------------------------------------------------
# Per-direction drafts: deform weight and Shrinkwrap mask
# ----------------------------------------------------------------------
def _draft_delta(face, src, region, d):
    """|storm draft SK-<Region>_all_<d>.L delta| per head vertex (None if missing)."""
    sk = face.data.shape_keys
    kb = sk.key_blocks.get(key_name(region, "all", d))
    if kb is None:
        return None
    n = len(face.data.vertices)
    base, c = np.empty(n * 3), np.empty(n * 3)
    sk.reference_key.data.foreach_get("co", base)
    kb.data.foreach_get("co", c)
    m = np.linalg.norm((c - base).reshape(-1, 3), axis=1)[src]
    return m if m.max() > 1e-6 else None


def _dir_weight(face, src, region, spec, d, reach):
    """Deform weight for one direction: storm's draft movement / the bone's
    movement (reach; rotations: relative to the draft's own peak). Brows are
    made symmetric (one centre bone), cheeks stay left."""
    m = _draft_delta(face, src, region, d)
    if m is None:
        w = _region_weight(face, src, region)
    elif reach:
        w = np.clip(m / reach, 0.0, 1.0)
    else:
        w = np.clip(m / m.max() * WEIGHT_GAIN, 0.0, 1.0)
    if spec["sym"]:
        w = np.maximum(w, _mirror(face, src, w))
    return w


def _brow_line(arm, face):
    """(|x|, z) samples of the left brow line, face space, sorted by x."""
    imw = face.matrix_world.inverted() @ arm.matrix_world
    pts = sorted((imw @ b.head_local)[:] for b in arm.data.bones
                 if b.name.startswith("DEF-Brow_local") and b.name.endswith(".L"))
    if len(pts) < 2:
        return None
    pts = np.array(pts)
    return np.abs(pts[:, 0]), pts[:, 2], pts[:, 0].max()


def _shrink_mask(co, line, k, kind):
    n = len(co)
    if kind == "all":
        return np.ones(n)
    xs, zs, xmax = line
    ax = np.abs(co[:, 0])
    t = (co[:, 2] - np.interp(ax, xs, zs)) / k
    lo, hi = FOREHEAD_FAR_T if kind.endswith("_far") else FOREHEAD_T
    m = _smooth((t - lo) / (hi - lo))
    if "lid" in kind:
        a, b, c, e = LID_T
        lid = _smooth((a - t) / (a - b)) * (1.0 - _smooth((c - t) / (c - e)))
        lid *= 1.0 - _smooth((ax - xmax) / (0.3 * k))           # only under the brow
        lid *= co[:, 1] < np.median(co[:, 1])                    # front of the head
        m = np.maximum(m, LID_W * lid)
    return m


# ----------------------------------------------------------------------
# Rig and its action
#   brow:  Bone_base > brow_main (pivot inside the skull) > brow (glabella)
#   cheek: Bone_base > cheek_root > Bone_cheek
#   the action keys spec["key"]: rest at 0 and 2 * PEAK, the pose at PEAK
# ----------------------------------------------------------------------
def _glabella(co, h, width, k):
    near = (np.abs(co[:, 0]) < 0.08 * width) & (np.abs(co[:, 2] - h.z) < 0.25 * k)
    if not near.any():
        return Vector((0.0, h.y, h.z))
    i = np.nonzero(near)[0][np.argmin(co[near, 1])]
    return Vector((0.0, co[i, 1], co[i, 2]))


def _new_armature(context, region, spec, arm, face, co, k):
    imw = face.matrix_world.inverted() @ arm.matrix_world
    h = imw @ arm.data.bones[spec["ctrl"]].head_local
    up = Vector((0, 0, 1))
    width = float(np.ptp(co[:, 0]))
    centre = Vector(co.mean(0))
    ad = bpy.data.armatures.new(f"SKB-{spec['title']}")
    tmp = _obj(context, "SKB-tmp", ad, "tmp", region)
    prev = context.view_layer.objects.active
    context.view_layer.objects.active = tmp
    bpy.ops.object.mode_set(mode='EDIT')
    eb = ad.edit_bones.new("Bone_base")
    eb.head, eb.tail = centre, centre + up * k * 0.6
    par = ad.edit_bones.new(spec["parent"])
    b = ad.edit_bones.new(spec["bone"])
    if spec["sym"]:                       # pivot in the skull, the bone at the glabella
        g = _glabella(co, h, width, k)
        p = Vector((0.0, g.y + PIVOT_DEPTH * width, g.z))
        par.head, par.tail = p, p + up * k * 0.3
        b.head, b.tail = g, g + up * k * 0.15
    else:                                 # cheek_root just above the control
        par.head, par.tail = h + up * k * 0.15, h + up * k * 0.3
        par.use_deform = False
        b.head, b.tail = h, h + up * k * 0.15
    par.parent = eb
    b.parent = par
    bpy.ops.object.mode_set(mode='OBJECT')
    context.view_layer.objects.active = prev
    bpy.data.objects.remove(tmp, do_unlink=True)
    return ad


def _pose(rig, spec, d_name, term, k, face, arm):
    """Pose spec["key"] like storm's control at the key's full value; returns
    how far the region moves (None for rotations)."""
    to_local = (face.matrix_world.inverted() @ arm.matrix_world).to_3x3()
    d = (to_local @ Vector(term["dir"])).normalized()
    if term["kind"] == 'LOC':             # pure axis: storm's control axis is tilted a bit
        i = max(range(3), key=lambda j: abs(d[j]))
        d = Vector([(1.0 if d[i] > 0 else -1.0) if j == i else 0.0 for j in range(3)])
    pb = rig.pose.bones[spec["key"]]
    pb.rotation_mode = 'QUATERNION'
    rest = pb.bone.matrix_local.to_3x3().normalized().inverted()
    if term["kind"] != 'LOC':
        pb.rotation_quaternion = Quaternion(rest @ d, term["full"])
        return None
    reach = term["full"] * k
    v = d * reach
    if spec["sym"] and d.y > 0.5:         # brow into the skull: only a little
        v *= DEPTH_IN
    if d_name in spec["arc"]:             # swing around the pivot: follows the forehead
        bone = rig.data.bones[spec["bone"]].head_local
        r = bone - pb.bone.head_local
        axis = r.cross(v)
        if axis.length > 1e-9 and r.length > 1e-9:
            pb.rotation_quaternion = Quaternion(rest @ axis.normalized(), v.length / r.length)
            return reach
    pb.location = rest @ v
    return reach


def _key_pose(rig, spec):
    """Rest at 0 and 2 * PEAK, the current pose at PEAK."""
    pb = rig.pose.bones[spec["key"]]
    loc, rot = pb.location.copy(), pb.rotation_quaternion.copy()
    rig.animation_data_create()
    for frame, rest in ((0, True), (PEAK, False), (2 * PEAK, True)):
        if rest:
            pb.location, pb.rotation_quaternion = (0, 0, 0), (1, 0, 0, 0)
        else:
            pb.location, pb.rotation_quaternion = loc, rot
        pb.keyframe_insert("location", frame=frame)
        pb.keyframe_insert("rotation_quaternion", frame=frame)
    pb.location, pb.rotation_quaternion = loc, rot
    if rig.animation_data.action:
        rig.animation_data.action.name = f"{rig.name}-anim"


# ----------------------------------------------------------------------
# Build
# ----------------------------------------------------------------------
def clear(context=None):
    actions = set()
    for o in _tagged():
        if o.animation_data and o.animation_data.action:
            actions.add(o.animation_data.action)
        data = o.data
        bpy.data.objects.remove(o, do_unlink=True)
        if data is not None and data.users == 0:
            (bpy.data.meshes if isinstance(data, bpy.types.Mesh) else bpy.data.armatures).remove(data)
    for a in actions:
        if a.users == 0:
            bpy.data.actions.remove(a)
    c = bpy.data.collections.get(COLL)
    if c is not None and not c.objects:
        bpy.data.collections.remove(c)


def build(context, arm, props):
    """(Re)create the bone / switch / in-mid-out heads for brow and cheek."""
    face = props.mesh
    if context.view_layer.objects.active is not None and context.view_layer.objects.active.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    by = {s["name"]: s for s in SK.data()["keys"]}
    head = SK._head_verts(face, arm, props)
    k = SK._R()._eye_dist(arm, 1.0)
    clear(context)
    notes = []
    base_me = head_mesh(face, head)
    src = _src(base_me)
    co = _co(base_me)
    line = _brow_line(arm, face)
    for region, spec in REGIONS.items():
        if spec["ctrl"] not in arm.data.bones:
            notes.append(f"{region}: {spec['ctrl']} 없음")
            continue
        masks = _part_masks(co, arm, face, spec)
        ad = _new_armature(context, region, spec, arm, face, co, k)
        # one bone head per direction
        for d in spec["dirs"]:
            sname = key_name(region, "all", d)
            s = by.get(sname)
            if s is None or not s["terms"]:
                notes.append(f"{sname}: storm 스펙 없음")
                continue
            me = base_me.copy()
            me.name = f"SKB-{spec['title']}_{d}"
            o = _obj(context, me.name, me, "bone", region, d)
            rig = _obj(context, f"SKB-{spec['title']}_{d}-rig", ad, "rig", region, d)
            rig.show_in_front = True
            context.view_layer.update()
            reach = _pose(rig, spec, d, s["terms"][0], k, face, arm)
            _key_pose(rig, spec)
            w = _smooth_weights(base_me, _dir_weight(face, src, region, spec, d, reach))
            _set_group(o, "Bone_base", 1.0 - w)
            _set_group(o, spec["bone"], w)
            m = o.modifiers.new("Armature", 'ARMATURE')
            m.object = rig
            # brows: the forehead slides over the skull, the ridge keeps its bulge
            kind = SHRINK.get(d) if spec["sym"] and line is not None else None
            if kind:
                _set_group(o, "MSK-shrinkwrap", _shrink_mask(co, line, k, kind))
                # the rest face, sitting with this head (Shrinkwrap works in world space)
                target = _obj(context, f"{me.name}-rest", base_me, "rest", region, d)
                target.hide_viewport = target.hide_render = True
                sw = o.modifiers.new("Shrinkwrap", 'SHRINKWRAP')
                sw.target, sw.vertex_group = target, "MSK-shrinkwrap"
                sw.wrap_method, sw.wrap_mode = 'NEAREST_SURFACEPOINT', 'ON_SURFACE'
            o["hb_skb_key"] = sname
        # switch: one key per direction (all .L to Switch fills them from the face)
        me = base_me.copy()
        me.name = f"SKB-{spec['title']}_switch"
        sw_obj = _obj(context, me.name, me, "switch", region)
        sw_obj.shape_key_add(name="Basis")
        for d in spec["dirs"]:
            sw_obj.shape_key_add(name=key_name(region, "all", d)).value = 0.0
        _set_group(sw_obj, "MSK-L", _left(face, co[:, 0]), eps=-1)   # what Bones to all .L keeps
        # in / mid / out: the switch split (they add up to the whole switch key)
        parts = {}
        for part in PARTS:
            me = base_me.copy()
            me.name = f"SKB-{spec['title']}_{part}"
            parts[part] = _obj(context, me.name, me, "part", region, part)
            for nm in MASKS:
                _set_group(parts[part], nm, masks[nm], eps=-1)
        _gn(parts["in"], [(sw_obj, 1.0, "In")])
        _gn(parts["mid"], [(sw_obj, 1.0, "Mid"), (parts["in"], -1.0, 1.0)])
        _gn(parts["out"], [(sw_obj, 1.0, 1.0), (parts["in"], -1.0, 1.0),
                           (parts["mid"], -1.0, 1.0)])
    arrange(context, props)
    to_switch(context, props)        # the switch starts from the face's current all keys
    return notes


# ----------------------------------------------------------------------
# Bones to all .L (bone heads -> face all keys, touched up on their edit
# meshes), all .L to Switch (face all keys -> switch), Split (switch -> face)
# ----------------------------------------------------------------------
def _evaluated(context, obj):
    context.view_layer.update()
    ev = obj.evaluated_get(context.evaluated_depsgraph_get())
    out = np.empty(len(ev.data.vertices) * 3)
    ev.data.vertices.foreach_get("co", out)
    return out.reshape(-1, 3)


def _group_weights(obj, name):
    w = np.zeros(len(obj.data.vertices))
    g = obj.vertex_groups.get(name)
    if g is None:
        return None
    for v in obj.data.vertices:
        for e in v.groups:
            if e.group == g.index:
                w[v.index] = e.weight
    return w


class _EditMeshes:
    """The brow / cheek edit meshes (Pose Keys storage, one per key without
    .L / .R: the .R is mirrored when the keys are registered on the face).
    put() writes the shape into Morph Target over the mesh's Basis; the
    sculpt layer (New Changes) is kept on top."""

    def __init__(self, face):
        self.by = {pk.name: pk.storage_object for pk in face.data.pose_keys
                   if pk.storage_object is not None}
        self.n = len(face.data.vertices)

    def _obj(self, name):
        return self.by.get(name[:-2] if name[-2:] in (".L", ".R") else name)

    def _key(self, obj, key):
        keys = obj.data.shape_keys
        kb = keys.key_blocks.get(key) if keys else None
        if kb is None or len(obj.data.vertices) != self.n:
            return None
        return kb

    def get(self, name):
        """The edit mesh as it shows (Morph Target + New Changes), or None."""
        o = self._obj(name)
        if o is None or len(o.data.vertices) != self.n:
            return None
        return SK._sculpted_co(o)

    def put(self, name, d_head, src, mirror=True):
        o = self._obj(name)
        kb = self._key(o, "Morph Target") if o is not None else None
        if kb is None:
            return []
        base = np.empty(self.n * 3)
        o.data.shape_keys.reference_key.data.foreach_get("co", base)
        base = base.reshape(-1, 3)
        base[src] += d_head
        kb.data.foreach_set("co", base.reshape(-1))
        o.data.update()
        return [o.name]

    def mask(self, name, w_head, src, group="MSK-L"):
        """Vertex group (head weights, 0 elsewhere) on the edit mesh, to cut the
        shape in half with (the key's Vertex Group)."""
        o = self._obj(name)
        if o is None or len(o.data.vertices) != self.n:
            return
        g = o.vertex_groups.get(group)
        if g is not None:
            o.vertex_groups.remove(g)
        g = o.vertex_groups.new(name=group)
        for w in np.unique(np.round(w_head, 4)):
            if w > 0:
                g.add([int(i) for i in src[np.round(w_head, 4) == w]], float(w), 'REPLACE')
        kb = self._key(o, "Morph Target")
        if kb is not None:
            kb.vertex_group = group          # applied: the right half is cut off
            o.data.update()


def capture(context, arm, props, only=None):
    """Bone heads at PEAK, whole (both sides), cut to .L by the MSK-L group set
    as its Vertex Group -> the SK-<Region>_all_<dir> edit meshes' Morph Target, to
    touch up there (New Changes is kept). only: bone heads to redo (default all)."""
    face = props.mesh
    fk = _EditMeshes(face)
    scene = context.scene
    frame = scene.frame_current
    scene.frame_set(PEAK)
    done = []
    try:
        for bh in (only if only is not None else _tagged("bone")):
            region = bh["hb_skb_region"]
            sw = next(iter(_tagged("switch", region)), None)
            ref = sw if sw is not None else bh
            rest = _co(ref.data)
            src = _src(ref.data)
            done += fk.put(bh["hb_skb_key"], _evaluated(context, bh) - rest, src)
            left = _group_weights(ref, "MSK-L")
            if left is None:
                left = _left(face, rest[:, 0])
            fk.mask(bh["hb_skb_key"], left, src)            # the half cut, on Morph Target
    finally:
        scene.frame_set(frame)
    return done


def to_switch(context, props):
    """The SK-<Region>_all_<dir> edit meshes (as they show, touch-ups included)
    -> the switch keys."""
    fk = _EditMeshes(props.mesh)
    done = []
    for region in REGIONS:
        sw = next(iter(_tagged("switch", region)), None)
        if sw is None:
            continue
        src = _src(sw.data)
        for kb in sw.data.shape_keys.key_blocks[1:]:
            c = fk.get(kb.name)
            if c is None:
                continue
            kb.data.foreach_set("co", c[src].reshape(-1))
            done.append(kb.name)
        sw.data.update()
    return done


def split(context, arm, props):
    """Switch keys one at a time -> the in / mid / out edit meshes' Morph Target
    (New Changes kept; .R is mirrored when the keys are registered)."""
    fk = _EditMeshes(props.mesh)
    written = []
    for region, spec in REGIONS.items():
        sw = next(iter(_tagged("switch", region)), None)
        if sw is None:
            continue
        src = _src(sw.data)
        rest = _co(sw.data)
        parts = {o.get("hb_skb_dir"): o for o in _tagged("part", region)}
        if set(parts) != set(PARTS):
            continue
        keys = sw.data.shape_keys.key_blocks
        old = [kb.value for kb in keys]
        for kb in keys[1:]:
            kb.value = 0.0
        try:
            for kb in keys[1:]:
                kb.value = 1.0
                d = kb.name[len(f"SK-{spec['title']}_all_"):-2]
                for p in PARTS:
                    written += fk.put(key_name(region, p, d),
                                      _evaluated(context, parts[p]) - rest, src)
                kb.value = 0.0
        finally:
            for kb, v in zip(keys, old):
                kb.value = v
    return written


# ----------------------------------------------------------------------
# Layout, read off the face's brow / cheek edit blocks (as laid out by hand):
#   bone heads   left of the all block, on its + / - rows, X | Y | Z
#   in/mid/out   each above the middle column of its own in / mid / out block
#   switch       above mid
# ----------------------------------------------------------------------
BONE_GAP = 1.2            # bone columns, in head widths (dx)
BONE_TO_ALL = 1.9         # last bone column -> first all column
PART_UP = {"brow": 1.24, "cheek": 1.70}     # in / mid / out above their block's + row (dz), as laid out by hand
SWITCH_UP = {"brow": 1.03, "cheek": 1.77}   # switch above in / mid / out (dz)


def _store_pos(region):
    """{(part, dir): location} of the face's edit meshes for this region."""
    title = REGIONS[region]["title"]
    out = {}
    for o in bpy.data.objects:
        if o.get(O.OBJ_PROP) != SK.STORAGE_TAG:
            continue
        tail = o.name.split(f"SK-{title}_", 1)
        if len(tail) != 2:
            continue
        part, _, d = tail[1].partition("_")
        if part in ("all",) + PARTS and d in DIRS:
            out[(part, d)] = o.location.copy()
    return out


def _label(context, region, key, text, loc, size):
    lb = next((o for o in _tagged("label", region) if o.get("hb_skb_dir") == key), None)
    if lb is None:
        lb = _obj(context, text, None, "label", region, key)
        lb.empty_display_type = 'PLAIN_AXES'
        lb.show_name = True
        lb.hide_select = True
    lb.empty_display_size = size
    lb.name = text
    lb.location = loc
    return lb


def arrange(context, props):
    face = props.mesh
    objs = _tagged()
    if not objs:
        return
    head = next((o for o in objs if o.get("hb_skb_kind") in ("rest", "switch")), None)
    dims = head.dimensions if head else Vector((0.3, 0.3, 0.35))
    # the heads keep the face's coordinates: their geometry sits above the origin
    ctr = (sum((Vector(c) for c in head.bound_box), Vector()) / 8) if head else Vector()
    top = ctr.z + dims.z * 0.62
    dx, dz = dims.x * 1.25, dims.z * 1.3
    for region, spec in REGIONS.items():
        pos = _store_pos(region)
        axes = list(dict.fromkeys(d.rsplit("_", 1)[0] for d in spec["dirs"]))
        if not all(("all", f"{a}_{s}") in pos for a in axes for s in ("pos", "neg")):
            continue                                     # no edit block to sit by
        x_all = pos[("all", f"{axes[0]}_pos")].x
        row = {s: pos[("all", f"{axes[0]}_{s}")].z for s in ("pos", "neg")}
        bx = {a: x_all - BONE_TO_ALL * dx - (len(axes) - 1 - i) * BONE_GAP * dx
              for i, a in enumerate(axes)}
        mid_axis = axes[len(axes) // 2]
        part_at = {}
        for p in PARTS:
            anchor = pos.get((p, f"{mid_axis}_pos"))
            if anchor is not None:
                part_at[p] = Vector((anchor.x, anchor.y, anchor.z + PART_UP[region] * dz))
        sw_at = (part_at["mid"] + Vector((0, 0, SWITCH_UP[region] * dz))) if "mid" in part_at else None
        for o in _tagged(region=region):
            kind, d = o.get("hb_skb_kind"), o.get("hb_skb_dir", "")
            if kind in ("bone", "rig", "rest"):
                axis, sign = d.rsplit("_", 1)
                o.location = Vector((bx[axis], 0.0, row[sign]))
            elif kind == "part" and d in part_at:
                o.location = part_at[d]
            elif kind == "switch" and sw_at is not None:
                o.location = sw_at
        # labels
        keep = set()
        for a in axes:
            _label(context, region, a, f"{spec['title']} {a.upper()} bones (+ / -)",
                   Vector((bx[a] - dx * 0.4, 0, row["pos"] + top)), dx * 0.05)
            keep.add(a)
        for p, at in part_at.items():
            _label(context, region, p, f"{spec['title']} {p.upper()}",
                   at + Vector((-dx * 0.4, 0, top)), dx * 0.05)
            keep.add(p)
        if sw_at is not None:
            _label(context, region, "switch", f"SWITCH  SK-{spec['title']}_all_*.L",
                   sw_at + Vector((-dx * 0.4, 0, top)), dx * 0.05)
            keep.add("switch")
        for lb in _tagged("label", region):
            if lb.get("hb_skb_dir") not in keep:
                bpy.data.objects.remove(lb)
        # region title
        t = next((o for o in _tagged("title", region)), None)
        if t is None:
            t = _obj(context, f"{spec['title'].upper()} SETUP", None, "title", region)
            t.empty_display_type = 'PLAIN_AXES'
            t.show_name = True
            t.hide_select = True
        t.empty_display_size = dx * 0.3
        t.name = f"{spec['title'].upper()} SETUP  (bones > all .L > switch > in / mid / out)"
        t.location = Vector((bx[axes[0]] - dx * 0.5, 0, row["pos"] + top + dz * 0.25))


# ----------------------------------------------------------------------
# Operators
# ----------------------------------------------------------------------
def _poll(context):
    props = context.scene.hb_facial
    return (O.facial_rig(context) is not None and props.mesh is not None
            and props.mesh.mode == 'OBJECT' and SK.pose_keys_available(props.mesh)
            and len(props.mesh.data.pose_keys) > 0)


class HB_OT_facial_skbones_build(bpy.types.Operator):
    """얼굴의 눈썹 / 볼 편집 블록 옆에 눈썹 / 볼 본 헤드(방향별 하나, 포즈를 잡거나
다시 칠할 수 있는 작은 애니메이션 아마추어), 스위치 헤드, in / mid / out 지오메트리
노드 헤드를 만듭니다. 이전에 만든 것은 교체합니다 (얼굴의 키는 유지)"""

    bl_idname = "hb.facial_skbones_build"
    bl_label = "눈썹/볼 셋업"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _poll(context)

    def invoke(self, context, event):
        if _tagged():
            return context.window_manager.invoke_confirm(self, event)
        return self.execute(context)

    def execute(self, context):
        notes = build(context, O.facial_rig(context), context.scene.hb_facial)
        n = len(_tagged("bone"))
        self.report({'WARNING'} if notes else {'INFO'},
                    f"본 헤드 {n}개, 영역별 스위치 + in / mid / out"
                    + ("; " + "; ".join(notes[:3]) if notes else ""))
        return {'FINISHED'}


class HB_OT_facial_skbones_bake(bpy.types.Operator):
    """피크 프레임의 본 헤드를 그대로(양쪽) 눈썹 / 볼 all 편집 메쉬(Morph Target)에
넣고, 거기서 다듬을 수 있도록 MSK-L 버텍스 그룹으로 왼쪽 절반만 남깁니다(양쪽을
보려면 그룹을 삭제). 본 헤드를 선택하면 선택한 것만, 아니면 전체를 처리합니다.
New Changes는 유지되며, .R은 키를 등록할 때 미러됩니다"""

    bl_idname = "hb.facial_skbones_bake"
    bl_label = "본 → all .L"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _poll(context) and bool(_tagged("bone"))

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        sel = [o for o in context.selected_objects if o.get("hb_skb_kind") == "bone"]
        done = capture(context, O.facial_rig(context), context.scene.hb_facial, sel or None)
        self.report({'INFO'}, f"all 키 {len(done)}개 기록 (프레임 {PEAK})")
        return {'FINISHED'}


class HB_OT_facial_skbones_to_switch(bpy.types.Operator):
    """눈썹 / 볼 all 편집 메쉬를 보이는 그대로(다듬은 내용 포함) 스위치에
복사합니다"""

    bl_idname = "hb.facial_skbones_to_switch"
    bl_label = "all .L → 스위치"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _poll(context) and bool(_tagged("switch"))

    def execute(self, context):
        done = to_switch(context, context.scene.hb_facial)
        self.report({'INFO'}, f"스위치 키 {len(done)}개 업데이트")
        return {'FINISHED'}


class HB_OT_facial_skbones_update(bpy.types.Operator):
    """모든 스위치 키를 in / mid / out 헤드로 나눠 눈썹 / 볼 in / mid / out 편집
메쉬(Morph Target)에 넣습니다 (New Changes는 유지)"""

    bl_idname = "hb.facial_skbones_update"
    bl_label = "키로 분리"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _poll(context) and bool(_tagged("switch"))

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        done = split(context, O.facial_rig(context), context.scene.hb_facial)
        self.report({'INFO'}, f"쉐이프키 {len(done)}개 업데이트")
        return {'FINISHED'}


class HB_OT_facial_skbones_clear(bpy.types.Operator):
    """눈썹 / 볼 본, 스위치, in / mid / out 헤드를 삭제합니다 (얼굴의 키는 유지)"""

    bl_idname = "hb.facial_skbones_clear"
    bl_label = "눈썹/볼 셋업 삭제"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return bool(_tagged())

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        clear(context)
        return {'FINISHED'}


_classes = (HB_OT_facial_skbones_build, HB_OT_facial_skbones_bake,
            HB_OT_facial_skbones_to_switch, HB_OT_facial_skbones_update,
            HB_OT_facial_skbones_clear)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
