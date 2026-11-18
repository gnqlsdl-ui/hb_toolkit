"""Extract storm's facial shape keys into hb_toolkit/facial/data/storm_shapekeys.json.

Run in background on storm_v1.1.blend:
  blender -b storm_v1.1.blend --python extract_storm_shapekeys.py -- <out.json>

Per key (GEO-storm-head, names starting with SK-):
  group     mouth / brow / cheek / other
  range     slider min / max, mask = vertex group (MSK-face.L / .R ...)
  terms     the driver as a product of terms; each term reads one bone channel:
              bone   our bone name (None when our rig has no such control yet)
              kind   LOC / ROT
              dir    the bone axis in world space, signed so that moving /
                     turning along it raises the key (storm faces -Y)
              full   amount for a factor of 1: LOC in eye distances, ROT in radians
              clamp  True: only that direction counts (max / min in the driver)
              space  LOCAL_SPACE / TRANSFORM_SPACE
  curve     driver F-curve points [[x, y, handle_left, handle_right]] or None (linear)
  deltas    [[vertex, dx, dy, dz]] from the Basis, storm world space, same
            vertex order as storm_face_weights.json
"""
import bpy, sys, json, re
from mathutils import Vector
out = sys.argv[sys.argv.index('--') + 1]
rig = bpy.data.objects['RIG-storm']; B = rig.data.bones
head = bpy.data.objects['GEO-storm-head']; K = head.data.shape_keys
EYE_DIST = (B['MSTR-Eye.L'].head_local - B['MSTR-Eye.R'].head_local).length
RW = rig.matrix_world.to_3x3().normalized()
HW = head.matrix_world.to_3x3()


def R5(x):
    return round(x, 5)


OURS = {"DEF-Jaw": "DEF-JAW", "MSTR-Mouth": "MSTR-MOUTH",
        "CTL-Lips_local_upp": "CTL-Lips_upp", "CTL-Lips_local_low": "CTL-Lips_low"}
for s in ('.L', '.R'):
    OURS.update({f"CTL-Lips_corn{s}": f"CTL-Lips_corn{s}", f"DEF-Nostril{s}": f"DEF-Nostril{s}",
                 f"CTL-Cheek_puff{s}": f"CTL-Cheekpuff{s}",
                 f"CTL-Brow_all{s}": f"MSTR-BROW{s}", f"P-Eyelid_mid_low{s}": f"CTL-Eyelid_low{s}"})
    for part in ("in", "mid", "out"):
        OURS[f"CTL-Brow_{part}{s}"] = f"CTL-Brow_{part}{s}"
    for part in ("all", "in", "mid", "out"):
        OURS[f"CTL-Cheek_{part}{s}"] = f"CTL-cheek_{part}{s}"


def group_of(n):
    if n.startswith(("SK-Brow", "SK-Furrow")):
        return "brow"
    if n.startswith("SK-Cheek_"):
        return "cheek"
    if n.startswith(("SK-MC_", "SK-mouth", "SK-Mouth", "SK-Lip", "SK-cheek_puff", "SK-nostril",
                     "SK-compression")):
        return "mouth"
    return "other"


TERM = re.compile(r'\((?:(max|min)\(0, (\w+)\)|(\w+))\s*/\s*(-?[\d.]+)\)')
SIMPLE = re.compile(r'^\s*(-?)\s*(\w+)\s*(?:\*\s*(-?[\d.]+))?\s*$')
AXIS = {'X': 0, 'Y': 1, 'Z': 2}


def term(var, full, clamp):
    """One driver term -> world-direction description (None when unsupported)."""
    if var.type != 'TRANSFORMS':
        return None
    t = var.targets[0]
    b = B.get(t.bone_target)
    kind, ax = t.transform_type.split('_')
    if b is None or kind not in ('LOC', 'ROT'):
        return None
    axis = (RW @ b.matrix_local.to_3x3().normalized()).col[AXIS[ax]]
    sign = 1.0 if full > 0 else -1.0
    return {"storm": t.bone_target, "bone": OURS.get(t.bone_target), "kind": kind,
            "dir": [R5(c * sign) for c in axis],
            "full": R5(abs(full) / EYE_DIST) if kind == 'LOC' else R5(abs(full)),
            "clamp": clamp, "space": t.transform_space}


drivers = {}
for fc in K.animation_data.drivers:
    m = re.search(r'key_blocks\["(.+?)"\]\.value', fc.data_path)
    if m:
        drivers[m.group(1)] = fc

basis = [v.co.copy() for v in K.key_blocks['Basis'].data]
keys, skipped = [], []
for kb in K.key_blocks:
    n = kb.name
    if not n.startswith("SK-"):
        continue
    fc = drivers.get(n)
    terms, ok = [], fc is not None
    if fc is not None:
        d = fc.driver
        vars_ = {v.name: v for v in d.variables}
        expr = d.expression
        found = TERM.findall(expr)
        if found and TERM.sub('', expr).replace('*', '').strip() == '':
            for mm, v1, v2, val in found:
                name = v1 or v2
                full = float(val)
                clamp = bool(mm)
                tm = term(vars_[name], full, clamp) if name in vars_ else None
                if tm is None:
                    ok = False
                    break
                terms.append(tm)
        else:
            s = SIMPLE.match(expr)
            if s and s.group(2) in vars_:
                mult = float(s.group(3)) if s.group(3) else 1.0
                full = (1.0 / mult) * (-1.0 if s.group(1) else 1.0)
                tm = term(vars_[s.group(2)], full, False)
                ok = tm is not None
                if ok:
                    terms.append(tm)
            else:
                ok = False
    curve = None
    if fc is not None and len(fc.keyframe_points) > 0:
        pts = [[R5(p.co[0]), R5(p.co[1]), [R5(c) for c in p.handle_left], [R5(c) for c in p.handle_right]]
               for p in fc.keyframe_points]
        if not (len(pts) == 2 and pts[0][:2] == [0.0, 0.0] and pts[1][:2] == [1.0, 1.0]):
            curve = pts
    deltas = []
    for i, v in enumerate(kb.data):
        dv = HW @ (v.co - basis[i])
        if dv.length > 2e-5:
            deltas.append([i, R5(dv.x), R5(dv.y), R5(dv.z)])
    e = {"name": n, "group": group_of(n), "range": [kb.slider_min, kb.slider_max],
         "mask": kb.vertex_group, "terms": terms if ok else None,
         "expr": fc.driver.expression if fc is not None else None,
         "curve": curve, "deltas": deltas}
    keys.append(e)
    if not ok:
        skipped.append(n)

# --- the controls behind the keys: locks and "frozen" limits (storm keeps some
# controls in place with a Limit Location / Rotation of 0 and reads their raw
# channel), as world axes so they fit any bone orientation
PB = rig.pose.bones
controls = {}
for sn, ours in OURS.items():
    pb = PB.get(sn)
    if pb is None:
        continue
    m = RW @ pb.bone.matrix_local.to_3x3().normalized()
    c = {"storm": sn,
         "lock_loc": [[R5(x) for x in m.col[i]] for i in range(3) if pb.lock_location[i]],
         "lock_rot": [[R5(x) for x in m.col[i]] for i in range(3) if pb.lock_rotation[i]],
         "freeze": []}
    for con in pb.constraints:
        if con.type in ("LIMIT_LOCATION", "LIMIT_ROTATION") and not con.mute and con.influence > 0:
            if con.type == "LIMIT_LOCATION":
                flags = [con.use_min_x, con.use_max_x, con.use_min_y, con.use_max_y, con.use_min_z, con.use_max_z]
            else:
                flags = [con.use_limit_x, con.use_limit_y, con.use_limit_z]
            vals = [getattr(con, a) for a in ("min_x", "max_x", "min_y", "max_y", "min_z", "max_z")]
            if con.type != "LIMIT_LOCATION" or any(abs(v) > 1e-6 for v, f in zip(vals, flags) if f):
                print("LIMIT (not stored)", sn, con.type, flags, vals)
                continue
            # directions the control may not move in (min 0: not backwards, max 0: not forwards)
            dirs = [[R5(x * (-1.0 if j % 2 == 0 else 1.0)) for x in m.col[j // 2]]
                    for j, f in enumerate(flags) if f]
            c["freeze"].append({"type": con.type, "space": con.owner_space, "dirs": dirs})
    controls[ours] = c

# --- masks the keys use (besides the side masks, which are rebuilt from x)
gname = {g.index: g.name for g in head.vertex_groups}
mask_names = sorted({k["mask"] for k in keys if k["mask"] and not k["mask"].startswith("MSK-face.")})
masks = {n: [] for n in mask_names}
for v in head.data.vertices:
    for g in v.groups:
        n = gname[g.group]
        if n in masks and g.weight > 0.002:
            masks[n].append([v.index, round(g.weight, 4)])

data = {"source": "Blender Studio 'storm' rig v1.1 (CC-BY 4.0): GEO-storm-head shape keys",
        "eye_dist": R5(EYE_DIST), "head_verts": len(basis), "keys": keys,
        "controls": controls, "masks": masks}
print("CTRL", {k: (len(v["lock_loc"]), len(v["lock_rot"]), [(f["space"], len(f["dirs"])) for f in v["freeze"]]) for k, v in controls.items() if not k.endswith(".R")})
print("MASKS", {k: len(v) for k, v in masks.items()})
with open(out, 'w') as f:
    json.dump(data, f, separators=(',', ':'))
import collections
print('OKS', len(keys), dict(collections.Counter(k["group"] for k in keys)),
      'unparsed', len(skipped), skipped[:6])
for k in keys:
    if k["group"] == "mouth":
        print('KEY', k["name"], k["range"], k["mask"], len(k["deltas"]), k["expr"],
              [(t["bone"], t["kind"], t["dir"], t["full"], t["clamp"], t["space"][:5]) for t in (k["terms"] or [])], 'curve' if k["curve"] else '')
