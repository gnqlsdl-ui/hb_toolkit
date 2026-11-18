"""Controls stage: the animator-facing layer on top of base / guides / MCH.

Build Controls (re-runnable, replaces only what it made, tag ``CTRL``):

Mouth   storm: MSTR-MOUTH (pivot inside the head) parents CTL-Lips_main_upp
        / _low and P-Lips_corn.L/R. Skull / jaw / half-jaw motion comes in
        under them: MSTR-Lips_upp (skull), MSTR-Lips_low (jaw) and
        MSTR-Lips_corn (half jaw) are copied in world space by P- bones under
        FACE-ROOT, whose local transform the mains / corners add first
        (Copy Transforms local, Before Full). Mains > center local +
        P-Lips_local_*.L/R mid controls > local 1 / 2. Corner > corn_local
        (corner micro) and corn_normal (arrow: pushes the corner out).
Eyes    MSTR-EYE_TARGET -> CTL-Eye_target.L/R; MCH-Eye_aim (Damped Track to
        the target) -> FK-Eye -> DEF-Eye (eyeball deform bone).
        MCH-Eyelid_follow / _follow_corn copy part of FK-Eye's rotation;
        CTL-Eyelid_upp/low (main) parent in / mid / out locals, the corners
        ride the corner follow.
Ribbons The lip / eyelid ribbons ride these controls through an Armature
        modifier (storm), so the MCH (Stretch To / Damped Track the ribbon)
        and the DEF on them follow the controls.
Brows   storm layout from Build Base (parts.py): DEF-Brow_local1/2/5/7 are
        the controls, 3 / 4 / 6 follow STR-Brow_local stretch bones.
Nose    STR-Nose aims at the upper lip ("Nose Follow Lips" on DEF-Nose,
        features.py).

Then every control gets a widget (WGT-HB_* in a hidden collection) and a
DSP bone on the nearest head-mesh vertex, so its shape sits on the skin.
"""

import json
import os

import bpy
from bpy.props import BoolProperty
from bpy.types import Operator
from mathutils import Matrix, Vector
from mathutils import kdtree

from . import template as T
from . import operators as O
from . import features as F

CTRL = 'CTRL'
WGT_COLL = "Facial_WGT"
DSP_AUTO_KEY = "hb_dsp_auto"
REF_LEN = (Vector(T.REFERENCE[1]) - Vector(T.REFERENCE[0])).length

EYE_TARGET = "MSTR-EYE_TARGET"
EYELID_FOLLOW = 0.3          # share of the eye rotation the lids follow
EYELID_FOLLOW_CORNER = 0.1

# Controls that get a DSP bone (shape drawn on the skin).
DSP_PREFIXES = ("CTL-Lips_", "P-Lips_local", "CTL-Eyelid_", "DEF-Brow_local", "MSTR-BROW", "CTL-cheek_",
                "CTL-Eyesocket_", "DEF-Eyesocket_",
                "CTL-Cheekpuff", "DEF-Nose", "DEF-Nostril", "DEF-Furrow", "DEF_SM-CHIN", "DEF-jawline")


# ----------------------------------------------------------------------
# Line helpers
# ----------------------------------------------------------------------
def _params(pts):
    """Normalized arc length (0..1) of every point of a polyline."""
    acc = [0.0]
    for a, b in zip(pts, pts[1:]):
        acc.append(acc[-1] + (b - a).length)
    total = acc[-1] or 1.0
    return [d / total for d in acc]


def _at(pts, params, u):
    for k in range(len(pts) - 1):
        if params[k + 1] >= u or k == len(pts) - 2:
            span = params[k + 1] - params[k]
            t = 0.0 if span <= 0 else min(max((u - params[k]) / span, 0.0), 1.0)
            return pts[k].lerp(pts[k + 1], t)
    return pts[-1].copy()


def _blend(controls, u):
    """{control: weight} for parameter u between sorted (name, u) controls."""
    for (na, ua), (nb, ub) in zip(controls, controls[1:]):
        if u <= ub or nb == controls[-1][0]:
            t = 0.0 if ub <= ua else min(max((u - ua) / (ub - ua), 0.0), 1.0)
            out = {}
            if 1.0 - t > 1e-4:
                out[na] = 1.0 - t
            if t > 1e-4:
                out[nb] = out.get(nb, 0.0) + t
            return out
    return {controls[-1][0]: 1.0}


# ----------------------------------------------------------------------
# Storm template data (data/storm_template.json, made by data/extract_storm.py)
# ----------------------------------------------------------------------
_STORM = None


def storm():
    """Ribbon weight curves, control positions and control shapes from storm."""
    global _STORM
    if _STORM is None:
        path = os.path.join(os.path.dirname(__file__), "data", "storm_template.json")
        with open(path, encoding="utf-8") as f:
            _STORM = json.load(f)
    return _STORM


def _curve(table, u):
    """{role: weight} at u, interpolated between the template's samples."""
    u = min(max(u, 0.0), 1.0)
    for (ua, wa), (ub, wb) in zip(table, table[1:]):
        if u <= ub:
            t = 0.0 if ub <= ua else (u - ua) / (ub - ua)
            out = {r: wa.get(r, 0.0) * (1 - t) + wb.get(r, 0.0) * t for r in set(wa) | set(wb)}
            break
    else:
        out = dict(table[-1][1])
    out = {r: w for r, w in out.items() if w > 1e-3}
    total = sum(out.values()) or 1.0
    return {r: w / total for r, w in out.items()}


def _lip_names(which, side):
    s = f"CTL-Lips_{which}"
    return {"C": s, "1": f"{s}1{side}", "2": f"{s}2{side}", "K": f"CTL-Lips_corn{side}",
            "KL": f"CTL-Lips_corn_local{side}"}


def _lid_names(which, side):
    return {"in": f"CTL-Eyelid_in{side}", "a": f"CTL-Eyelid_{which}_in{side}",
            "mid": f"CTL-Eyelid_{which}_mid{side}", "b": f"CTL-Eyelid_{which}_out{side}",
            "out": f"CTL-Eyelid_out{side}"}


def _lip_controls(which, uc):
    """[(control, u)] along corner.L (0) -> center (uc) -> corner.R (1)."""
    cu = storm()["controls_u"][f"lips_{which}"]
    s = f"CTL-Lips_{which}"
    return [("CTL-Lips_corn.L", 0.0), (f"{s}2.L", uc * (1 - cu["2"])),
            (f"{s}1.L", uc * (1 - cu["1"])), (s, uc),
            (f"{s}1.R", uc + (1 - uc) * cu["1"]), (f"{s}2.R", uc + (1 - uc) * cu["2"]),
            ("CTL-Lips_corn.R", 1.0)]


def _lip_extra_positions(which, uc):
    """[(bone, u)] placed on the line but not weighting the ribbon: the
    storm mid controls P-Lips_local_<which>.L/R (parents of local 1 / 2)."""
    cu = storm()["controls_u"][f"lips_{which}"]
    p = cu.get("P", (cu["1"] + cu["2"]) / 2)
    return [(f"P-Lips_local_{which}.L", uc * (1 - p)), (f"P-Lips_local_{which}.R", uc + (1 - uc) * p)]


def _lid_controls(which, side):
    cu = storm()["controls_u"][f"lids_{which}"]
    n = _lid_names(which, side)
    return [(n["in"], 0.0), (n["a"], cu["a"]), (n["mid"], cu["mid"]), (n["b"], cu["b"]),
            (n["out"], 1.0)]


def _lip_weights(which, uc, u):
    """Storm lip weights: the half-line curve, mirrored about the center."""
    if u <= uc:
        side, s = ".L", (1 - u / uc) if uc > 0 else 0.0
    else:
        side, s = ".R", (u - uc) / (1 - uc) if uc < 1 else 0.0
    names = _lip_names(which, side)
    return {names[r]: w for r, w in _curve(storm()["weights"][f"lips_{which}"], s).items()}


def _lid_weights(which, side, u):
    names = _lid_names(which, side)
    return {names[r]: w for r, w in _curve(storm()["weights"][f"lids_{which}"], u).items()}


def _line_layout(slot, upp, low):
    """({control: world point}, {point name: {ctrl: w}}).

    ``upp`` / ``low`` are [(name, world point)] from corner a to corner b.
    Weights follow storm's ribbon (up to 3 controls per point, smooth).
    """
    pos, weights = {}, {}
    for which, line in (("upp", upp), ("low", low)):
        names = [n for n, _p in line]
        pts = [p for _n, p in line]
        par = _params(pts)
        if slot == 'LIPS':
            uc = par[names.index(f"Lips_{which}")]
            ctrls = _lip_controls(which, uc)
            wfn = lambda u, _w=which, _c=uc: _lip_weights(_w, _c, u)
        else:
            side = O.SETS[slot]["side"]
            ctrls = _lid_controls(which, side)
            wfn = lambda u, _w=which, _s=side: _lid_weights(_w, _s, u)
        for c, u in ctrls + (_lip_extra_positions(which, uc) if slot == 'LIPS' else []):
            pos.setdefault(c, _at(pts, par, u))
        for n, u in zip(names, par):
            weights.setdefault(n, wfn(u))
    return pos, weights


# ----------------------------------------------------------------------
# Widgets
# ----------------------------------------------------------------------
def _circle(n=24, plane="XY", r=1.0, c=(0.0, 0.0)):
    import math
    pts = []
    for i in range(n):
        a = 2 * math.pi * i / n
        x, y = c[0] + r * math.cos(a), c[1] + r * math.sin(a)
        pts.append((x, y, 0.0) if plane == "XY" else (x, 0.0, y) if plane == "XZ" else (0.0, x, y))
    return pts, [(i, (i + 1) % n) for i in range(n)]


def _stadium(hw, hh, n=10):
    import math
    pts = []
    r, cx = hh, hw - hh
    for i in range(n + 1):                                 # right half circle
        a = -math.pi / 2 + math.pi * i / n
        pts.append((cx + r * math.cos(a), r * math.sin(a), 0.0))
    for i in range(n + 1):                                 # left half circle
        a = math.pi / 2 + math.pi * i / n
        pts.append((-cx + r * math.cos(a), r * math.sin(a), 0.0))
    return pts, [(i, (i + 1) % len(pts)) for i in range(len(pts))]


def _merge(*parts):
    verts, edges = [], []
    for v, e in parts:
        o = len(verts)
        verts += v
        edges += [(a + o, b + o) for a, b in e]
    return verts, edges


def _shapes():
    cube_v = [(x, y, z) for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)]
    cube_e = [(a, b) for a in range(8) for b in range(a + 1, 8)
              if sum(1 for i in range(3) if cube_v[a][i] != cube_v[b][i]) == 1]
    return {
        "Circle": _circle(),
        "Ring": _circle(32, "XZ"),
        "Sphere": _merge(_circle(), _circle(24, "XZ"), _circle(24, "YZ")),
        "Cube": (cube_v, cube_e),
        "Diamond": ([(1, 0, 0), (0, 1, 0), (-1, 0, 0), (0, -1, 0)],
                    [(0, 1), (1, 2), (2, 3), (3, 0)]),
        "Bar": _stadium(1.0, 0.3),
        "Target": _merge(_circle(16, r=0.5, c=(1.0, 0.0)), _circle(16, r=0.5, c=(-1.0, 0.0)),
                         _stadium(1.75, 0.75)),
    }


def _wgt_collection(context):
    coll = bpy.data.collections.get(WGT_COLL)
    if coll is None:
        coll = bpy.data.collections.new(WGT_COLL)
        context.scene.collection.children.link(coll)
    coll.hide_viewport = True
    coll.hide_render = True
    return coll


def _wgt_object(context, name, verts, edges):
    obj = bpy.data.objects.get(name)
    me = bpy.data.meshes.new(name)
    me.from_pydata([tuple(v) for v in verts], [tuple(e) for e in edges], [])
    me.update()
    if obj is None:
        obj = bpy.data.objects.new(name, me)
        _wgt_collection(context).objects.link(obj)
    else:
        old = obj.data
        obj.data = me
        if old.users == 0:
            bpy.data.meshes.remove(old)
        me.name = name
    obj[O.OBJ_PROP] = "WIDGET"
    obj.hide_render = True
    return obj


def ensure_widgets(context):
    return {k: _wgt_object(context, f"WGT-HB_{k}", v, e) for k, (v, e) in _shapes().items()}


def _color(pb):
    n = pb.name
    if n.startswith("MSTR-") or "_main_" in n or n == EYE_TARGET:
        pal = 'THEME03'
    elif n.endswith(".L"):
        pal = 'THEME04'
    elif n.endswith(".R"):
        pal = 'THEME01'
    else:
        pal = 'THEME09'
    try:
        pb.color.palette = pal
    except (AttributeError, TypeError):
        pass


def _assign(pb, wgt, size):
    pb.custom_shape = wgt
    length = pb.bone.length or 1.0
    pb.custom_shape_scale_xyz = (size / length,) * 3
    pb.bone.show_wire = True
    _color(pb)


def _jaw_widget(context, arm):
    """U line through the jawline bones, in DEF-JAW's shape space."""
    names = ([f"DEF-jawline{i}.R" for i in (3, 2, 1)] + ["DEF-jawline_Center"]
             + [f"DEF-jawline{i}.L" for i in (1, 2, 3)])
    bones = arm.data.bones
    if T.JAW_BONE not in bones or any(n not in bones for n in names):
        return None
    jaw = bones[T.JAW_BONE]
    to_local = jaw.matrix_local.inverted()
    verts = [tuple(to_local @ bones[n].head_local / jaw.length) for n in names]
    return _wgt_object(context, "WGT-HB_Jaw", verts, [(i, i + 1) for i in range(len(verts) - 1)])


def assign_widgets(context, arm, props):
    """Give every face control a shape sized to the head. Returns the count."""
    w = ensure_widgets(context)
    hb = arm.data.bones.get(props.head_bone)
    s = (hb.length if hb else REF_LEN) / REF_LEN
    bones = arm.data.bones
    sizes = {}

    def put(name, key, size):
        if name in bones:
            sizes[name] = (w[key] if isinstance(key, str) else key, size)

    mouth_hw = None
    if "CTL-Lips_corn.L" in bones and "CTL-Lips_corn.R" in bones:
        mouth_hw = (bones["CTL-Lips_corn.L"].head_local - bones["CTL-Lips_corn.R"].head_local).length / 2
    hw = mouth_hw or 0.025 * s

    put("MSTR-FACE_UPP", "Ring", 0.105 * s)
    put("MSTR-FACE_LOW", "Ring", 0.095 * s)
    put("TGT-FACE_UPP", "Cube", 0.005 * s)
    put("TGT-FACE_LOW", "Cube", 0.005 * s)
    put(T.MOUTH_MASTER, "Bar", hw * 1.35)
    jaw = _jaw_widget(context, arm)
    if jaw is not None and T.JAW_BONE in bones:
        sizes[T.JAW_BONE] = (jaw, bones[T.JAW_BONE].length)
    else:
        put(T.JAW_BONE, "Cube", 0.006 * s)
    put("MSTR-NOSE", "Sphere", 0.006 * s)
    for n in T.NOSE_DEFS:
        put(n, "Sphere", 0.0025 * s)
    put("DEF_SM-CHIN", "Sphere", 0.004 * s)
    put("MSTR-TONGUE", "Ring", 0.012 * s)
    for i in range(1, 5):
        put(f"DEF-TONGUE{i}", "Ring", 0.008 * s)
    put("DEF-jawline_Center", "Sphere", 0.002 * s)
    for jw in ("UPP", "LOW"):
        put(f"MSTR-TEETH_{jw}", "Cube", 0.005 * s)
        put(f"DEF-TEETH_{jw}", "Sphere", 0.002 * s)
    put("CTL-Lips_main_upp", "Bar", hw * 0.55)
    put("CTL-Lips_main_low", "Bar", hw * 0.55)
    put("CTL-Lips_upp", "Circle", 0.0022 * s)
    put("CTL-Lips_low", "Circle", 0.0022 * s)
    put(EYE_TARGET, "Target", None)
    for side in (".L", ".R"):
        eye = T.EYE_MASTER.format(side=side)
        r = bones[eye].length if eye in bones else 0.012 * s
        put(eye, "Ring", r * 1.45)
        put(f"FK-Eye{side}", "Ring", r * 1.2)
        put(f"CTL-Eye_target{side}", "Circle", None)
        put(f"MSTR-EAR{side}", "Sphere", 0.008 * s)
        put(f"CTL-Cheekpuff{side}", "Sphere", 0.004 * s)
        put(f"MSTR-BROW{side}", "Bar", 0.011 * s)
        for i in range(1, 8):
            put(f"DEF-Brow_local{i}{side}", "Circle", 0.0028 * s)
        put(f"CTL-cheek_all{side}", "Circle", 0.006 * s)
        for n in ("in", "mid", "out"):
            put(f"CTL-cheek_{n}{side}", "Circle", 0.0026 * s)
        put(f"P-jawline_all{side}", "Cube", 0.003 * s)
        for i in (1, 2, 3):
            put(f"DEF-jawline{i}{side}", "Sphere", 0.002 * s)
        for jw in ("UPP", "LOW"):
            for k in (1, 2):
                put(f"DEF-TEETH_{jw}{k}{side}", "Sphere", 0.002 * s)
        put(f"CTL-Lips_corn{side}", "Diamond", 0.003 * s)
        for which in ("upp", "low"):
            for k in (1, 2):
                put(f"CTL-Lips_{which}{k}{side}", "Circle", 0.0022 * s)
            # eyelids
            eye_w = 0.03 * s
            a, b = f"CTL-Eyelid_in{side}", f"CTL-Eyelid_out{side}"
            if a in bones and b in bones:
                eye_w = (bones[a].head_local - bones[b].head_local).length
            put(f"CTL-Eyelid_{which}{side}", "Bar", eye_w * 0.3)
            for n in ("in", "mid", "out"):
                put(f"CTL-Eyelid_{which}_{n}{side}", "Circle", 0.0016 * s)
        put(f"CTL-Eyelid_in{side}", "Diamond", 0.002 * s)
        put(f"CTL-Eyelid_out{side}", "Diamond", 0.002 * s)

    # eye targets: sized from the distance between the two targets
    tl, tr = bones.get("CTL-Eye_target.L"), bones.get("CTL-Eye_target.R")
    half = ((tl.head_local - tr.head_local).length / 2) if (tl and tr) else 0.03 * s
    for n in ("CTL-Eye_target.L", "CTL-Eye_target.R"):
        if n in sizes:
            sizes[n] = (sizes[n][0], half * 0.32)
    if EYE_TARGET in sizes:
        sizes[EYE_TARGET] = (sizes[EYE_TARGET][0], half)

    for name, (wgt, size) in sizes.items():
        _assign(arm.pose.bones[name], wgt, size)
    done = set(sizes) | apply_storm_shapes(context, arm, s)
    return len(done)


def _storm_widget(context, name):
    d = storm()["widgets"][name]
    return _wgt_object(context, f"WGT-HB_storm_{name[4:]}", [tuple(v) for v in d["verts"]],
                       [tuple(e) for e in d["edges"]])


def _eye_dist(arm, s):
    bones = arm.data.bones
    a, b = T.EYE_MASTER.format(side=".L"), T.EYE_MASTER.format(side=".R")
    if a in bones and b in bones:
        return (bones[a].head_local - bones[b].head_local).length
    return storm()["eye_dist"] * s


STORM_SKIP = set()


EYE_SCALED = ("FK-Eye", "MSTR-EYE.")      # sized by the eyeball, not the eye spacing


def _eye_scale(arm, side, k):
    """Scale for eye shapes: our eye-center -> lid front depth vs storm's."""
    data = storm()
    eye = arm.data.bones.get(T.EYE_MASTER.format(side=side))
    rib = bpy.data.objects.get(O.RIBBON_EYELID.format(side=side))
    if eye is None or rib is None or not data.get("eye_front"):
        return k
    c = arm.matrix_world @ eye.head_local
    front = max(c.y - (rib.matrix_world @ v.co).y for v in rib.data.vertices)
    return front * data["eye_dist"] / data["eye_front"] if front > 0 else k


def apply_storm_shapes(context, arm, s=1.0, only=None):
    """Storm's widget, orientation, size and offset on every mapped control.

    The template keeps each shape's axes in armature space and its size /
    offset relative to storm's eye distance, so this rebuilds the same look
    on any bone orientation and scales it by this character's eye distance.
    Offsets are taken from the DSP bone head when the control gets one,
    from its own head otherwise. Returns the names it shaped.
    """
    data = storm()
    base_k = _eye_dist(arm, s)
    eye_k = {}
    wgts = {}
    done = set()
    # template axes are world axes (storm faces -Y, Z up): undo this rig's object rotation
    to_arm = arm.matrix_world.to_3x3().normalized().inverted()
    for name, spec in data["shapes"].items():
        pb = arm.pose.bones.get(name)
        if pb is None or name in STORM_SKIP or (only is not None and name not in only):
            continue
        k = base_k
        if name.startswith(EYE_SCALED):
            side = name[-2:]
            if side not in eye_k:
                eye_k[side] = _eye_scale(arm, side, base_k)
            k = eye_k[side]
        if spec["wgt"] not in wgts:
            wgts[spec["wgt"]] = _storm_widget(context, spec["wgt"])
        bone = pb.bone
        length = bone.length or 1.0
        d3 = bone.matrix_local.to_3x3().normalized()
        inv = d3.inverted() @ to_arm
        rot = inv @ Matrix(spec["rot"])
        anchor = spec.get("anchor")
        bones = arm.data.bones
        if anchor and not all(a in bones for a in anchor):
            continue            # keep our own widget (e.g. jawline U without a chin bone)
        if anchor:
            # storm drew it from a DSP we don't make: offset from the anchors' midpoint
            mid = sum((bones[a].head_local for a in anchor), Vector()) / len(anchor)
            off = (arm.matrix_world.to_3x3() @ (mid - bone.head_local)) / k \
                + Vector(spec["offset_anchor"])
        elif name.startswith(DSP_PREFIXES):
            off = spec["offset"]
        else:
            off = spec["offset_bone"]
        pb.custom_shape = wgts[spec["wgt"]]
        pb.use_custom_shape_bone_size = True
        pb.custom_shape_rotation_euler = rot.to_euler('XYZ')
        pb.custom_shape_scale_xyz = [v * k / length for v in spec["size"]]
        # (custom shape translation is in bone axes but not scaled by bone length)
        pb.custom_shape_translation = inv @ (Vector(off) * k)
        if hasattr(pb, "custom_shape_wire_width"):
            pb.custom_shape_wire_width = spec["wire"]
        bone.show_wire = True
        _color(pb)
        done.add(name)
    return done


# ----------------------------------------------------------------------
# Build controls
# ----------------------------------------------------------------------
def _rehook(rib, arm, weights_for):
    """Drop the ribbon's hooks / bone groups and hook it again."""
    names = list(rib.get("hb_points") or [])
    if not names:
        return None
    for m in list(rib.modifiers):
        if m.type in {'HOOK', 'ARMATURE', 'MASK'}:
            rib.modifiers.remove(m)
    for vg in list(rib.vertex_groups):
        if not vg.name.startswith((O.HOOK_PREFIX, O.MASK_GROUP)):
            rib.vertex_groups.remove(vg)
    index = {n: i for i, n in enumerate(names)}
    return O._weight_ribbon(rib, arm, index, weights_for)


def build_controls(context, arm, props):
    """Create the controls stage. Returns (count, notes)."""
    bones = arm.data.bones
    notes = []
    hb = bones.get(props.head_bone)
    s = (hb.length if hb else REF_LEN) / REF_LEN
    clen = 0.004 * s
    inv = arm.matrix_world.inverted()
    up = (inv.to_3x3() @ Vector((0.0, 0.0, 1.0))).normalized()
    mesh = props.mesh
    edges = O.edge_map(mesh) if mesh is not None else {}

    lines = {}
    for slot in O.SETS:
        if slot not in edges:
            continue
        try:
            _info, upp, low = O.slot_lines(arm, mesh, slot, props)
        except (ValueError, KeyError) as e:
            notes.append(str(e))
            continue
        lines[slot] = (upp, low)
    layouts = {slot: _line_layout(slot, *ul) for slot, ul in lines.items()}

    old = set(O._tagged(arm, CTRL))
    plan = []            # (name, head, tail, roll|None, parent, deform, coll, set)

    def add(name, head, tail, parent, set_id, deform=False, coll=T.COLL_CTRL, roll=0.0):
        plan.append((name, head, tail, roll, parent, deform, coll, set_id))

    def vert(p_arm, length=clen):
        return p_arm, p_arm + up * length

    # --- mouth ---
    mouth_fit = None
    if 'LIPS' in layouts and T.MOUTH_MASTER in bones:
        pos, _w = layouts['LIPS']
        mm = bones[T.MOUTH_MASTER]
        # storm: MSTR-Mouth sits inside the head behind the lip center, so
        # the whole mouth (MCH-Mouth_* copy its local transform) turns about it
        mspec = storm().get("extras", {}).get("mouth_master")
        if mspec:
            k_m = _eye_dist(arm, s)
            lip_c = (pos["CTL-Lips_upp"] + pos["CTL-Lips_low"]) * 0.5
            to_arm = arm.matrix_world.to_3x3().inverted()
            m_head = inv @ (lip_c + Vector(mspec["offset"]) * k_m)
            m_tail = m_head + (to_arm @ Vector(mspec["axis"])).normalized() * (mspec["length"] * k_m)
            mouth_fit = (m_head, m_tail)
        # storm hierarchy: MSTR-MOUTH is the outer parent of the lip mains and
        # the corner P-; the skull / jaw / half-jaw motion comes in underneath
        # through P- bones (Copy Transforms local, Before Full) that copy
        # MSTR-Lips_* riding the skull, the jaw and the half jaw.
        face_root = "FACE-ROOT" if "FACE-ROOT" in bones else props.head_bone
        half = T.JAW_HALF if T.JAW_HALF in bones else T.LIP_UPPER_PARENT
        for which, src in (("upp", T.LIP_UPPER_PARENT), ("low", T.LIP_LOWER_PARENT)):
            c = inv @ pos[f"CTL-Lips_{which}"]
            hm = vert(c, clen * 1.5)
            main = f"CTL-Lips_main_{which}"
            add(f"MSTR-Lips_{which}", *hm, src, 'LIPS', coll=T.COLL_MCH)
            add(f"P-Lips_{which}", *hm, face_root, 'LIPS', coll=T.COLL_MCH)
            add(main, *hm, T.MOUTH_MASTER, 'LIPS')
            add(f"CTL-Lips_{which}", *vert(c), main, 'LIPS')            # center local
            for side in (".L", ".R"):
                mid = f"P-Lips_local_{which}{side}"                      # mid control
                add(mid, *vert(inv @ pos[mid], clen * 0.8), main, 'LIPS')
                for k in (1, 2):
                    n = f"CTL-Lips_{which}{k}{side}"
                    add(n, *vert(inv @ pos[n]), mid, 'LIPS')
        for side in (".L", ".R"):
            cp = inv @ pos[f"CTL-Lips_corn{side}"]
            add(f"MSTR-Lips_corn{side}", *vert(cp), half, 'LIPS', coll=T.COLL_MCH)
            add(f"P-Lips_corn1{side}", *vert(cp), face_root, 'LIPS', coll=T.COLL_MCH)
            add(f"P-Lips_corn{side}", *vert(cp), T.MOUTH_MASTER, 'LIPS', coll=T.COLL_MCH)
            add(f"CTL-Lips_corn{side}", *vert(cp), f"P-Lips_corn{side}", 'LIPS')
            add(f"CTL-Lips_corn_local{side}", *vert(cp, clen * 1.8), f"CTL-Lips_corn{side}", 'LIPS')
            # (aimed in the edit pass once the corner roll is known)
            add(f"CTL-Lips_corn_normal{side}", *vert(cp, clen * 1.8), f"CTL-Lips_corn{side}", 'LIPS')

    # --- eyes ---
    eyes = {}
    for side in (".L", ".R"):
        eye = T.EYE_MASTER.format(side=side)
        if eye in bones:
            b = bones[eye]
            eyes[side] = (b.head_local.copy(), (b.tail_local - b.head_local), b.length)
    if eyes:
        dist = (hb.length if hb else REF_LEN)
        targets = {sd: h + d.normalized() * dist for sd, (h, d, _r) in eyes.items()}
        mid = sum(targets.values(), Vector()) / len(targets)
        root = "FACE-ROOT" if "FACE-ROOT" in bones else props.head_bone
        add(EYE_TARGET, *vert(mid, clen * 2), root, 'EYES')
        for side, (h, d, r) in eyes.items():
            set_id = "EYE" + side.replace(".", "_")
            eye = T.EYE_MASTER.format(side=side)
            add(f"CTL-Eye_target{side}", *vert(targets[side]), EYE_TARGET, set_id)
            tail = h + d
            add(f"MCH-Eye_aim{side}", h, tail, eye, set_id, coll=T.COLL_MCH, roll=("copy", eye))
            add(f"FK-Eye{side}", h, tail, f"MCH-Eye_aim{side}", set_id, roll=("copy", eye))
            add(f"DEF-Eye{side}", h, tail, f"FK-Eye{side}", set_id, deform=True,
                coll=T.COLL_DEF, roll=("copy", eye))
            slot = set_id
            if slot in layouts:
                add(f"MCH-Eyelid_follow{side}", h, tail, eye, set_id, coll=T.COLL_MCH, roll=("copy", eye))
                add(f"MCH-Eyelid_follow_corn{side}", h, tail, eye, set_id, coll=T.COLL_MCH,
                    roll=("copy", eye))
                pos, _w = layouts[slot]
                for which in ("upp", "low"):
                    main = f"CTL-Eyelid_{which}{side}"
                    add(main, *vert(inv @ pos[f"CTL-Eyelid_{which}_mid{side}"], clen * 1.2),
                        f"MCH-Eyelid_follow{side}", set_id)
                    for n in ("in", "mid", "out"):
                        cn = f"CTL-Eyelid_{which}_{n}{side}"
                        add(cn, *vert(inv @ pos[cn], clen * 0.8), main, set_id)
                for n in ("in", "out"):
                    cn = f"CTL-Eyelid_{n}{side}"
                    add(cn, *vert(inv @ pos[cn], clen * 0.8), f"MCH-Eyelid_follow_corn{side}", set_id)

    # --- eyesocket ring, ears, blink controls (features.py) ---
    k_eye = _eye_dist(arm, s)
    F.plan_bones(add, arm, props, lines, k_eye, inv, up, clen)

    blocked = [p[0] for p in plan if p[0] in bones and p[0] not in old]
    plan = [p for p in plan if p[0] not in blocked]
    if blocked:
        notes.append(f"이름 {len(blocked)}개를 직접 만든 본이 사용 중 (예: {blocked[0]})")

    front = (inv.to_3x3() @ Vector((0.0, -1.0, 0.0))).normalized()
    made = []
    with O._EditMode(context, arm) as ebs:
        O._remove_bones(ebs, old)
        if mouth_fit is not None:                 # MSTR-MOUTH to its storm pivot
            ebs[T.MOUTH_MASTER].head, ebs[T.MOUTH_MASTER].tail = mouth_fit
            ebs[T.MOUTH_MASTER].align_roll(front)
        for name, head, tail, roll, parent, deform, coll, set_id in plan:
            b = ebs.new(name)
            b.head, b.tail = head, tail
            if roll is None:
                b.roll = ebs[T.MOUTH_MASTER].roll
            elif isinstance(roll, tuple):
                b.roll = ebs[roll[1]].roll
            else:
                # upright controls: roll 0 is unstable on a vertical bone (.L / .R
                # came out with different axes), so aim Z at the face front: X = +X
                b.roll = roll
                if abs((b.tail - b.head).normalized().dot(up)) > 0.99:
                    b.align_roll(front)
            b.use_deform = deform
            O._set_only_coll(arm, b, coll)
            made.append((name, parent, set_id))
        for name, parent, _s in made:
            ebs[name].parent = ebs.get(parent) if parent else None
        # lip corners: Z faces out from the mouth pivot through the corner
        # (the user's hand-set .L roll), .R mirrored by construction
        if T.MOUTH_MASTER in ebs:
            piv = ebs[T.MOUTH_MASTER].head
            for side in (".L", ".R"):
                cb = ebs.get(f"CTL-Lips_corn{side}")
                if cb is None:
                    continue
                y = (cb.tail - cb.head).normalized()
                z = cb.head - piv
                z = z - y * z.dot(y)
                if z.length > 1e-6:
                    cb.align_roll(z.normalized())
                zc = cb.z_axis.copy()
                loc = ebs.get(f"CTL-Lips_corn_local{side}")
                if loc is not None:
                    loc.align_roll(zc)
                nrm = ebs.get(f"CTL-Lips_corn_normal{side}")
                if nrm is not None:                   # storm: points in along -X,
                    ln = nrm.length                   # its Z = the corner's Z (push axis)
                    nrm.head = cb.head.copy()
                    nrm.tail = cb.head - cb.x_axis * ln * (1 if side == ".L" else -1)
                    nrm.align_roll(zc)
    names = [m[0] for m in made]
    O._clear_constraints(arm, names)
    for name, _p, set_id in made:
        O._tag(arm, [name], CTRL, set_id)
    pbs = arm.pose.bones

    def con(owner, kind, sub, **kw):
        c = pbs[owner].constraints.new(kind)
        c.target, c.subtarget = arm, sub
        for k, v in kw.items():
            setattr(c, k, v)
        return c

    # --- mouth (storm) ---
    def follow(owner, src, local):
        if owner in names and src in arm.data.bones:
            if local:
                con(owner, 'COPY_TRANSFORMS', src, name="Mouth_Follow", owner_space='LOCAL',
                    target_space='LOCAL', mix_mode='BEFORE_FULL')
            else:
                con(owner, 'COPY_TRANSFORMS', src, name="Mouth_Follow")

    for which in ("upp", "low"):
        follow(f"P-Lips_{which}", f"MSTR-Lips_{which}", False)
        follow(f"CTL-Lips_main_{which}", f"P-Lips_{which}", True)
        n = f"CTL-Lips_{which}"
        if n in names:
            c = pbs[n].constraints.new('LIMIT_ROTATION')
            c.name, c.owner_space, c.use_limit_x = "Lip_Limit_Rot", 'LOCAL', True
            c.min_x = c.max_x = 0.0
            c = pbs[n].constraints.new('LIMIT_SCALE')
            c.name, c.owner_space = "Lip_Limit_Scale", 'LOCAL'
            for ax in "xyz":
                setattr(c, f"use_min_{ax}", True); setattr(c, f"use_max_{ax}", True)
                setattr(c, f"min_{ax}", 1.0); setattr(c, f"max_{ax}", 1.0)
    for side in (".L", ".R"):
        follow(f"P-Lips_corn1{side}", f"MSTR-Lips_corn{side}", False)
        follow(f"P-Lips_corn{side}", f"P-Lips_corn1{side}", True)
        cn, nn = f"CTL-Lips_corn{side}", f"CTL-Lips_corn_normal{side}"
        if cn in names:
            c = pbs[cn].constraints.new('LIMIT_SCALE')      # scale only drives the lip zip
            c.name, c.owner_space = "Corner_Limit_Scale", 'LOCAL'
            for ax in "xyz":
                setattr(c, f"use_min_{ax}", True); setattr(c, f"use_max_{ax}", True)
                setattr(c, f"min_{ax}", 1.0); setattr(c, f"max_{ax}", 1.0)
            pbs[cn].lock_location = (False, False, True)
        if nn in names and cn in names:
            pbs[nn].lock_location = (True, True, False)
            pbs[nn].lock_rotation = (True, True, True)
            pbs[nn].lock_scale = (True, True, True)
            pbs[nn].lock_rotation_w = True
            # storm: pushing the arrow along its Z pushes the corner along its Z
            try:
                pbs[cn].driver_remove("location", 2)
            except (TypeError, RuntimeError):
                pass
            fc = pbs[cn].driver_add("location", 2)
            d = fc.driver
            d.type = 'SCRIPTED'
            v = d.variables.new()
            v.name, v.type = "v", 'SINGLE_PROP'
            v.targets[0].id_type = 'OBJECT'
            v.targets[0].id = arm
            v.targets[0].data_path = f'pose.bones["{nn}"].location[2]'
            d.expression = "v"
    for side in (".L", ".R"):
        if f"MCH-Eye_aim{side}" in names:
            con(f"MCH-Eye_aim{side}", 'DAMPED_TRACK', f"CTL-Eye_target{side}",
                name="Aim", track_axis='TRACK_Y')
        for n, inf in ((f"MCH-Eyelid_follow{side}", EYELID_FOLLOW),
                       (f"MCH-Eyelid_follow_corn{side}", EYELID_FOLLOW_CORNER)):
            if n in names:
                con(n, 'COPY_ROTATION', f"FK-Eye{side}", name="Follow_Eye", influence=inf)

    # ribbons ride the controls now
    for slot, (_pos, weights) in layouts.items():
        rib_name = O.RIBBON_LIPS if slot == 'LIPS' else O.RIBBON_EYELID.format(side=O.SETS[slot]["side"])
        rib = bpy.data.objects.get(rib_name)
        if rib is None:
            notes.append(f"{rib_name} 없음 (Generate Guides를 실행하세요)")
            continue
        if not all(c in arm.data.bones for w in weights.values() for c in w):
            notes.append(f"{rib_name}: 컨트롤이 없어 리본을 그대로 둠")
            continue
        miss = _rehook(rib, arm, lambda n, _w=weights: _w.get(n, {}))
        if miss is None:
            notes.append(f"{rib_name}: 다시 훅하려면 다시 생성하세요 (Generate Guides)")

    # blink / eyelid follow / lip zip: properties, drivers, blink ribbons
    notes += F.setup(context, arm, props, lines, k_eye)
    return len(names), notes


# ----------------------------------------------------------------------
# Auto DSP
# ----------------------------------------------------------------------
def auto_dsp_map(arm, mesh):
    """{control: nearest head-mesh vertex} for every DSP_PREFIXES control."""
    mw = mesh.matrix_world
    kd = kdtree.KDTree(len(mesh.data.vertices))
    for v in mesh.data.vertices:
        kd.insert(mw @ v.co, v.index)
    kd.balance()
    out = {}
    for b in arm.data.bones:
        if b.name.startswith(DSP_PREFIXES) and not b.name.startswith(O.DSP_PREFIX) \
                and arm.pose.bones[b.name].custom_shape is not None:
            out[b.name] = kd.find(arm.matrix_world @ b.head_local)[1]
    mesh[DSP_AUTO_KEY] = out
    return out


def dsp_full_map(arm, mesh):
    mapping = auto_dsp_map(arm, mesh)
    mapping.update(O.dsp_map(mesh))           # hand-stored vertices win
    return mapping


# ----------------------------------------------------------------------
# Operators
# ----------------------------------------------------------------------
class HB_OT_facial_build_controls(Operator):
    """컨트롤(입술, 눈꺼풀, 눈 에임)과 그 아래 눈 / 입 MCH를 생성하고, 리본을
컨트롤에 훅한 뒤 모든 컨트롤에 쉐이프를 지정합니다"""

    bl_idname = "hb.facial_build_controls"
    bl_label = "컨트롤 생성"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return O.facial_rig(context) is not None

    def execute(self, context):
        props = context.scene.hb_facial
        arm = O.facial_rig(context)
        if props.mesh is not None and props.mesh.mode == 'EDIT':
            self.report({'ERROR'}, f"먼저 {props.mesh.name}의 Edit Mode를 종료하세요")
            return {'CANCELLED'}
        n, notes = build_controls(context, arm, props)
        w = assign_widgets(context, arm, props)
        msg = f"컨트롤 {n}, 쉐이프 {w}"
        if notes:
            msg += "; " + "; ".join(notes)
        self.report({'WARNING'} if notes else {'INFO'}, msg)
        return {'FINISHED'}


def _hide_helpers(arm):
    for name in (O.DSP_COLL, T.COLL_MCH, O.GUIDE_COLL):
        c = arm.data.collections_all.get(name)
        if c is not None:
            c.is_visible = False


class HB_OT_facial_build_rig(Operator):
    """얼굴 리그 전체를 순서대로 생성합니다: Base -> Guides + Ribbons -> MCH ->
Controls + Shapes -> DEF -> DSP. 입력이 없는 단계(등록된 루프 없음, 메쉬 없음)는
건너뜁니다"""

    bl_idname = "hb.facial_build_rig"
    bl_label = "리그 생성"
    bl_options = {'REGISTER', 'UNDO'}

    hide_helpers: BoolProperty(name="MCH / Guide / DSP 숨기기", default=True)
    face_weights: BoolProperty(
        name="얼굴 웨이트",
        description="마지막에 템플릿에서 얼굴 웨이트를 칠합니다 (끄면 메쉬에 이미 있는 "
        "웨이트를 유지, 예: 직접 칠하는 중)",
        default=True)
    lattices: BoolProperty(
        name="래티스",
        description="Storm 얼굴 래티스, 마스크, corrective smooth, 귀 웨이트를 생성합니다",
        default=True)

    @classmethod
    def poll(cls, context):
        arm = O.facial_rig(context)
        return arm is not None and bool(context.scene.hb_facial.head_bone)

    def execute(self, context):
        props = context.scene.hb_facial
        arm = O.facial_rig(context)
        if props.mesh is not None and props.mesh.mode == 'EDIT':
            self.report({'ERROR'}, f"먼저 {props.mesh.name}의 Edit Mode를 종료하세요")
            return {'CANCELLED'}
        done = []
        if 'FINISHED' not in bpy.ops.hb.facial_build_base():
            self.report({'ERROR'}, "Build Base 실패 (Info 로그 참고)")
            return {'CANCELLED'}
        done.append("Base")
        has_edges = props.mesh is not None and bool(O.edge_map(props.mesh))
        if has_edges:
            if 'FINISHED' in bpy.ops.hb.facial_generate():
                done.append("가이드")
            if 'FINISHED' in bpy.ops.hb.facial_build_mch():
                done.append("MCH")
        n, notes = build_controls(context, arm, props)
        done.append(f"컨트롤 {n}")
        if O._tagged(arm, 'MCH'):
            d, _r = O.build_def(context, arm, props.def_length)
            done.append(f"DEF {d}")
        w = assign_widgets(context, arm, props)
        done.append(f"쉐이프 {w}")
        if props.mesh is not None:
            mapping = dsp_full_map(arm, props.mesh)
            k, _old, _miss, blocked = O.build_dsp(context, arm, props.mesh, props.def_length,
                                                  props.parent_bone, mapping)
            done.append(f"DSP {k}")
            if blocked:
                notes.append(f"DSP 이름 {len(blocked)}개를 직접 만든 본이 사용 중")
            if self.face_weights:
                from .weights import transfer, smooth_weights
                nw, wnotes = transfer(context, arm, props)
                done.append(f"얼굴 웨이트 버텍스 {nw}개")
                notes += wnotes
            if self.lattices:
                from . import lattice
                # ear weights only when painting face weights (keeps hand-painted ears)
                notes += lattice.build(context, arm, props, with_ears=self.face_weights)
                done.append("래티스")
            if self.face_weights:
                smooth_weights(arm, props)
                done.append("스무스")
        else:
            notes.append("Mesh 없음: DSP 건너뜀")
        if not has_edges:
            notes.append("등록된 루프 없음: 입술 / 눈꺼풀 건너뜀")
        if self.hide_helpers:
            _hide_helpers(arm)
        msg = "생성: " + ", ".join(done)
        if notes:
            msg += "; " + "; ".join(notes)
        self.report({'WARNING'} if notes else {'INFO'}, msg)
        return {'FINISHED'}


_classes = (HB_OT_facial_build_controls, HB_OT_facial_build_rig)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
