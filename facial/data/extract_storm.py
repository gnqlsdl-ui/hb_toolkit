"""Extract storm ribbon weights + control shapes into hb_toolkit/facial/data/storm_template.json."""
import bpy, sys, json, re
from mathutils import Vector, Euler
out = sys.argv[sys.argv.index('--') + 1]
rig = bpy.data.objects['RIG-storm']; B = rig.data.bones; PB = rig.pose.bones
rig.animation_data_clear()
for _pb in PB:                       # rest pose, constraints still evaluated
    _pb.location = (0, 0, 0); _pb.scale = (1, 1, 1)
    _pb.rotation_quaternion = (1, 0, 0, 0); _pb.rotation_euler = (0, 0, 0)
bpy.context.view_layer.update()
_dev = max(((pb.head - pb.bone.head_local).length, pb.name) for pb in PB if pb.name.startswith("DSP-"))
print("DSP max rest->pose offset", _dev)
EYE_DIST = (B['MSTR-Eye.L'].head_local - B['MSTR-Eye.R'].head_local).length


def R5(x):
    return round(x, 5)


def params(pts):
    acc = [0.0]
    for a, b in zip(pts, pts[1:]):
        acc.append(acc[-1] + (b - a).length)
    return [d / (acc[-1] or 1) for d in acc]


def project(pts, par, p):
    best = (1e9, 0.0)
    for k in range(len(pts) - 1):
        a, b = pts[k], pts[k + 1]; ab = b - a
        t = 0 if ab.length_squared == 0 else max(0, min(1, (p - a).dot(ab) / ab.length_squared))
        d = (a + ab * t - p).length
        if d < best[0]:
            best = (d, par[k] + (par[k + 1] - par[k]) * t)
    return round(best[1], 4)


def rows(obj):
    me = obj.data; gn = {g.index: g.name for g in obj.vertex_groups}; mw = obj.matrix_world
    res = []
    for v in me.vertices:
        ws = {gn[g.group]: g.weight for g in v.groups if g.weight > 1e-4}
        if ws.get('MSK-line', 0) < 0.5:
            continue
        res.append((mw @ v.co, ws))
    return res


def table(sel, role):
    pts = [p for p, _ in sel]; par = params(pts)
    tab = []
    for (p, ws), u in zip(sel, par):
        d = {}
        for g, w in ws.items():
            r = role(g)
            if r:
                d[r] = d.get(r, 0) + w
        s = sum(d.values()) or 1
        tab.append([round(u, 4), {k: round(v / s, 4) for k, v in d.items()}])
    return pts, par, tab


weights = {}; ctl_u = {}; lid_lines = {}; lip_lines = {}

# ---- lips: half line center (0) -> corner (1), .L side
lips = rows(bpy.data.objects['HLP-storm-geometry_ribbon_lips'])


def lip_role(g):
    if not g.startswith('CTL-Lips_') or g.endswith('.R'):
        return None
    if g.startswith('CTL-Lips_corn_local'):
        return 'KL'                       # corner micro control (storm)
    if g.startswith('CTL-Lips_corn'):
        return 'K'
    m = re.match(r'CTL-Lips_local(\d?)_(upp|low)', g)
    return (m.group(1) or 'C') if m else None


for which in ('upp', 'low'):
    sel = []
    for p, ws in lips:
        if p.x < -1e-5:
            continue
        kinds = {('upp' if '_upp' in g else 'low') for g in ws if g.startswith('CTL-Lips_local')}
        if which in kinds or not kinds:
            sel.append((p, ws))
    sel.sort(key=lambda t: t[0].x)
    pts, par, tab = table(sel, lip_role)
    weights[f'lips_{which}'] = tab
    lip_lines[which] = (pts, par)
    ctl_u[f'lips_{which}'] = {r: project(pts, par, B[n].head_local) for r, n in
                              (('1', f'CTL-Lips_local1_{which}.L'), ('2', f'CTL-Lips_local2_{which}.L'),
                               ('P', f'P-Lips_local_{which}.L'))}

# ---- lids: inner corner (0) -> outer corner (1), .L
lids = rows(bpy.data.objects['HLP-storm-geometry_ribbon_eyelids'])


def hk_key(ws, which):
    for g in ws:
        if g == 'HK-Lid_corn_in.L':
            return -1
        if g == 'HK-Lid_corn_out.L':
            return 999
        m = re.match(r'HK-Lid_' + which + r'(\d+)\.L$', g)
        if m:
            return int(m.group(1))
    return None


for which in ('upp', 'low'):
    roles = {'in': 'in', 'out': 'out', f'{which}_right': 'a', f'mid_{which}': 'mid', f'{which}_left': 'b'}

    def lid_role(g):
        if not g.startswith('DEF-Eyelid_') or not g.endswith('.L'):
            return None
        return roles.get(g[len('DEF-Eyelid_'):-2])

    keyed = sorted(((hk_key(ws, which), p, ws) for p, ws in lids
                    if p.x > 0 and hk_key(ws, which) is not None), key=lambda t: t[0])
    pts, par, tab = table([(p, ws) for _k, p, ws in keyed], lid_role)
    weights[f'lids_{which}'] = tab
    lid_lines[which] = (pts, par)
    ctl_u[f'lids_{which}'] = {r: project(pts, par, B[n].head_local) for r, n in
                              (('a', f'DEF-Eyelid_{which}_right.L'), ('mid', f'DEF-Eyelid_mid_{which}.L'),
                               ('b', f'DEF-Eyelid_{which}_left.L'))}

# ---- extras: eyesocket ring, blink control, properties
def at(pts, par, u):
    for k in range(len(pts) - 1):
        if par[k + 1] >= u or k == len(pts) - 2:
            span = par[k + 1] - par[k]
            t = 0 if span <= 0 else min(max((u - par[k]) / span, 0), 1)
            return pts[k].lerp(pts[k + 1], t)
    return pts[-1]


lid_cen = sum(lid_lines['upp'][0] + lid_lines['low'][0], Vector()) / (
    len(lid_lines['upp'][0]) + len(lid_lines['low'][0]))
socket = {}
for ours, sn, which in (("CTL-Eyesocket_upp", "P-Eyesocket_mid_upp", "upp"),
                        ("CTL-Eyesocket_low", "P-Eyesocket_mid_low", "low"),
                        ("DEF-Eyesocket_in_upp", "DEF-Eyesocket_in_upp", "upp"),
                        ("DEF-Eyesocket_mid_upp", "DEF-Eyesocket_mid_upp", "upp"),
                        ("DEF-Eyesocket_out_upp", "DEF-Eyesocket_out_upp", "upp"),
                        ("DEF-Eyesocket_in_low", "DEF-Eyesocket_in_low", "low"),
                        ("DEF-Eyesocket_mid_low", "DEF-Eyesocket_mid_low", "low"),
                        ("DEF-Eyesocket_out_low", "DEF-Eyesocket_out_low", "low"),
                        ("DEF-Eyesocket_in", "DEF-Eyesocket_in", "upp"),
                        ("DEF-Eyesocket_out", "DEF-Eyesocket_out", "upp")):
    h = PB[sn + ".L"].head
    pts, par = lid_lines[which]
    d = (h - lid_cen); d2 = Vector((d.x, 0, d.z)).normalized()
    best = max((Vector(((at(pts, par, i / 200) - lid_cen).x, 0, (at(pts, par, i / 200) - lid_cen).z)).normalized().dot(d2), i / 200)
               for i in range(201))
    u = best[1]
    lp = at(pts, par, u) - lid_cen
    s = Vector((d.x, 0, d.z)).length / (Vector((lp.x, 0, lp.z)).length or 1)
    socket[ours] = {"line": which, "u": round(u, 4), "s": round(s, 4),
                    "parent": "CTL-Eyesocket_" + which if ours.startswith("DEF-Eyesocket_") and
                    ours[-4:] in ("_upp", "_low") else "EYE"}

prp = PB["PRP-Eye_blink.L"]; eye = PB["MSTR-Eye.L"]
lim = next((c for c in prp.constraints if c.type == 'LIMIT_LOCATION'), None)
extras = {
    "eyesocket": socket,
    "blink": {
        # from the outer eyelid corner (the eye center depth varies a lot between characters)
        "prp_offset": [R5(x / EYE_DIST) for x in prp.head - lid_lines['upp'][0][-1]],
        "prp_length": R5(prp.bone.length / EYE_DIST),
        "full_travel": R5((1 / 30) / EYE_DIST),       # storm: Blink = -loc_y * 30
        "limit_y": [R5((lim.min_y if lim and lim.use_min_y else -1 / 30) / EYE_DIST),
                    R5((lim.max_y if lim and lim.use_max_y else 0) / EYE_DIST)],
    },
    "props": {
        "PRP-Eye_blink": {"Blink Height": [0.3, 0.0, 1.0], "Eyelid Follow": [0.1, 0.0, 1.0]},
        "CTL-Lips_corn": {"LipZip Sharpness": [2.0, 0.0, 5.0]},
        "DEF-Nose": {"Nose Follow Lips": [0.2, 0.0, 1.0]},
    },
    "zip": {"corner_k": 1.2, "scale_gain": 3.0},
    # MSTR-Mouth sits inside the head (the mouth turns about it): offset from
    # the lip center (mid of the upper / lower lip controls) and its length
    "mouth_master": {
        "offset": [R5(x / EYE_DIST) for x in PB["MSTR-Mouth"].head
                   - (PB["CTL-Lips_local_upp"].head + PB["CTL-Lips_local_low"].head) * 0.5],
        "length": R5(PB["MSTR-Mouth"].bone.length / EYE_DIST),
        "axis": [R5(x) for x in (rig.matrix_world.to_3x3() @ PB["MSTR-Mouth"].bone.matrix_local.to_3x3()).col[1]],
    },
}

# ---- our control -> storm bone
MAP = {
    "MSTR-FACE_UPP": "MSTR-Face_upp", "MSTR-FACE_LOW": "MSTR-Face_low",
    "TGT-FACE_UPP": "TGT-Face_upp", "TGT-FACE_LOW": "TGT-Face_low",
    "MSTR-MOUTH": "MSTR-Mouth", "DEF-JAW": "DEF-Jaw", "MSTR-NOSE": "MSTR-Nose",
    "DEF-Nose_tip": "DEF-Nose_tip", "DEF-Nose_bridge": "DEF-Nose_bridge", "DEF_SM-CHIN": "DEF-Chin",
    "DEF-Nose": "DEF-Nose", "DEF-Furrow": "DEF-Furrow",
    "DEF-Nostril.L": "DEF-Nostril.L", "DEF-Nostril.R": "DEF-Nostril.R",
    "MSTR-TONGUE": "MSTR-Tongue", "DEF-TONGUE1": "P-Tongue1", "DEF-TONGUE2": "P-Tongue2",
    "DEF-TONGUE3": "P-Tongue3", "DEF-TONGUE4": "P-Tongue4",
    "MSTR-TEETH_UPP": "MSTR-Teeth_upp", "MSTR-TEETH_LOW": "MSTR-Teeth_low",
    "DEF-TEETH_UPP": "DEF-Teeth_upp", "DEF-TEETH_LOW": "DEF-Teeth_low",
    "CTL-Lips_main_upp": "CTL-Lips_main_upp", "CTL-Lips_main_low": "CTL-Lips_main_low",
    "CTL-Lips_upp": "CTL-Lips_local_upp", "CTL-Lips_low": "CTL-Lips_local_low",
    "MSTR-EYE_TARGET": "P-Eye_target",
}
SIDED = {
    "DEF-TEETH_UPP1{S}": "DEF-Teeth_upp1{S}", "DEF-TEETH_UPP2{S}": "DEF-Teeth_upp2{S}",
    "DEF-TEETH_LOW1{S}": "DEF-Teeth_low1{S}", "DEF-TEETH_LOW2{S}": "DEF-Teeth_low2{S}",
    "CTL-Lips_upp1{S}": "CTL-Lips_local1_upp{S}", "CTL-Lips_upp2{S}": "CTL-Lips_local2_upp{S}",
    "CTL-Lips_low1{S}": "CTL-Lips_local1_low{S}", "CTL-Lips_low2{S}": "CTL-Lips_local2_low{S}",
    "CTL-Lips_corn{S}": "CTL-Lips_corn{S}",
    "CTL-Lips_corn_local{S}": "CTL-Lips_corn_local{S}", "CTL-Lips_corn_normal{S}": "CTL-Lips_corn_normal{S}",
    "P-Lips_local_upp{S}": "P-Lips_local_upp{S}", "P-Lips_local_low{S}": "P-Lips_local_low{S}",
    "CTL-Eye_target{S}": "TGT-Eye{S}", "MSTR-EYE{S}": "MSTR-Eye{S}", "FK-Eye{S}": "FK-Eye{S}",
    "CTL-Eyelid_upp{S}": "P-Eyelid_mid_upp{S}", "CTL-Eyelid_low{S}": "P-Eyelid_mid_low{S}",
    "CTL-Eyelid_upp_in{S}": "DEF-Eyelid_upp_right{S}", "CTL-Eyelid_upp_mid{S}": "DEF-Eyelid_mid_upp{S}",
    "CTL-Eyelid_upp_out{S}": "DEF-Eyelid_upp_left{S}",
    "CTL-Eyelid_low_in{S}": "DEF-Eyelid_low_right{S}", "CTL-Eyelid_low_mid{S}": "DEF-Eyelid_mid_low{S}",
    "CTL-Eyelid_low_out{S}": "DEF-Eyelid_low_left{S}",
    "CTL-Eyelid_in{S}": "DEF-Eyelid_in{S}", "CTL-Eyelid_out{S}": "DEF-Eyelid_out{S}",
    "MSTR-BROW{S}": "CTL-Brow_all{S}",
    "CTL-Brow_in{S}": "CTL-Brow_in{S}", "CTL-Brow_mid{S}": "CTL-Brow_mid{S}",
    "CTL-Brow_out{S}": "CTL-Brow_out{S}",
    **{f"DEF-Brow_local{i}{{S}}": f"DEF-Brow_local{i}{{S}}" for i in range(1, 8)},
    "CTL-cheek_all{S}": "CTL-Cheek_all{S}", "CTL-cheek_in{S}": "CTL-Cheek_in{S}",
    "CTL-cheek_mid{S}": "CTL-Cheek_mid{S}", "CTL-cheek_out{S}": "CTL-Cheek_out{S}",
    "CTL-Cheekpuff{S}": "CTL-Cheek_puff{S}",
    "DEF-Ear_base{S}": "DEF-Ear_base{S}", "DEF-Ear_tip{S}": "DEF-Ear_tip{S}",
    "PRP-Eye_blink{S}": "PRP-Eye_blink{S}",
    "CTL-Eyesocket_upp{S}": "P-Eyesocket_mid_upp{S}", "CTL-Eyesocket_low{S}": "P-Eyesocket_mid_low{S}",
    "DEF-Eyesocket_in_upp{S}": "DEF-Eyesocket_in_upp{S}", "DEF-Eyesocket_mid_upp{S}": "DEF-Eyesocket_mid_upp{S}",
    "DEF-Eyesocket_out_upp{S}": "DEF-Eyesocket_out_upp{S}", "DEF-Eyesocket_in_low{S}": "DEF-Eyesocket_in_low{S}",
    "DEF-Eyesocket_mid_low{S}": "DEF-Eyesocket_mid_low{S}", "DEF-Eyesocket_out_low{S}": "DEF-Eyesocket_out_low{S}",
    "DEF-Eyesocket_in{S}": "DEF-Eyesocket_in{S}", "DEF-Eyesocket_out{S}": "DEF-Eyesocket_out{S}",
}
for k, v in SIDED.items():
    for s in ('.L', '.R'):
        MAP[k.replace('{S}', s)] = v.replace('{S}', s)

# Shapes storm draws from a DSP bone our rig doesn't have: offset from the
# midpoint of matching bones (ours, storm's) instead.
ANCHORS = {"MSTR-MOUTH": (("CTL-Lips_upp", "CTL-Lips_low"),
                          ("CTL-Lips_local_upp", "CTL-Lips_local_low")),
           "DEF-JAW": (("DEF_SM-CHIN",), ("DEF-Chin",))}
shapes = {}; used = set()
for ours, sn in MAP.items():
    pb = PB.get(sn)
    if pb is None or pb.custom_shape is None:
        print('MISSING', ours, sn)
        continue
    # DSP bones are moved onto the skin by constraints: use evaluated (rest-pose) matrices
    dpb = pb.custom_shape_transform or pb
    D3 = rig.matrix_world.to_3x3().normalized() @ dpb.matrix.to_3x3().normalized()
    ln = pb.bone.length if pb.use_custom_shape_bone_size else 1.0
    R = D3 @ Euler(pb.custom_shape_rotation_euler, 'XYZ').to_matrix()
    off = D3 @ Vector(pb.custom_shape_translation)     # translation is not scaled by bone length
    shapes[ours] = {
        "storm": sn, "wgt": pb.custom_shape.name,
        "rot": [[R5(x) for x in row] for row in R],
        "size": [R5(x * ln / EYE_DIST) for x in pb.custom_shape_scale_xyz],
        "offset": [R5(x / EYE_DIST) for x in off],
        "offset_bone": [R5(x / EYE_DIST) for x in off + dpb.head - pb.head],
        "wire": round(getattr(pb, 'custom_shape_wire_width', 1.0), 2),
        "color": pb.bone.color.palette,
    }
    if ours in ANCHORS:
        names, storm_names = ANCHORS[ours]
        mid = sum((PB[n].head for n in storm_names), Vector()) / len(storm_names)
        shapes[ours]["anchor"] = list(names)
        shapes[ours]["offset_anchor"] = [R5(x / EYE_DIST) for x in off + dpb.head - mid]
    used.add(pb.custom_shape.name)

widgets = {}
for n in sorted(used):
    me = bpy.data.objects[n].data
    widgets[n] = {"verts": [[R5(c) for c in v.co] for v in me.vertices],
                  "edges": [list(e.vertices) for e in me.edges]}

data = {
    "source": "Blender Studio 'storm' rig v1.1 (CC-BY 4.0): ribbon weights and face control shapes",
    "eye_dist": R5(EYE_DIST),
    "eye_front": R5(PB['MSTR-Eye.L'].head.y - min(p.y for p, _w in lids)),
    "weights_doc": "lips_*: u 0=center 1=corner, roles C/1/2/K(corner). "
                   "lids_*: u 0=inner 1=outer corner, roles in/a/mid/b/out",
    "weights": weights, "controls_u": ctl_u,
    "shapes_doc": "rot: shape axes in world space (storm faces -Y); size: scale xyz * bone length / eye_dist; "
                  "offset: from the display (DSP) bone head / eye_dist; offset_bone: from the "
                  "control's own head / eye_dist",
    "shapes": shapes, "widgets": widgets,
    "extras_doc": "eyesocket: u along the lid line, s = radial scale from the lid centroid (front "
                  "plane), then snapped to the skin. blink: PRP offset / length / travel in eye_dist.",
    "extras": extras,
}
with open(out, 'w') as f:
    json.dump(data, f, indent=1)
print('OK', len(shapes), len(widgets))

# ======================================================================
# Face weights: storm head mesh + facial weights + matching landmarks
# (written next to storm_template.json as storm_face_weights.json)
# ======================================================================
import os
from mathutils.bvhtree import BVHTree

head = bpy.data.objects['GEO-storm-head']
hme = head.data; hmw = head.matrix_world
hverts = [hmw @ v.co for v in hme.vertices]
hpolys = [list(p.vertices) for p in hme.polygons]
bvh = BVHTree.FromPolygons(hverts, hpolys)


def snap(p):
    loc, _n, _i, _d = bvh.find_nearest(p)
    return loc if loc is not None else p


def mirror(p):
    return Vector((-p.x, p.y, p.z))


def proj_u(pts, par, p):
    return project(pts, par, p)


# --- roles: storm deform group -> how to find our bone(s)
BODY = ('DEF-Head', 'DEF-Neck', 'DEF-Spine', 'DEF-Shoulder', 'DEF-UpperArm', 'DEF-Clavicle')
DIRECT = {"DEF-Chin": {"DEF_SM-CHIN": 1.0}, "DEF-Jaw": {"DEF-JAW": 1.0},
          "DEF-Nose_tip": {"DEF-Nose_tip": 1.0}, "DEF-Nose_bridge": {"DEF-Nose_bridge": 1.0},
          "DEF-Nose": {"DEF-Nose": 1.0},
          "DEF-Nostril.L": {"DEF-Nostril.L": 1.0}, "DEF-Nostril.R": {"DEF-Nostril.R": 1.0},
          "DEF-Furrow": {"DEF-Furrow": 1.0}}
brow_order = {}
for side in ('.L', '.R'):
    names = [pb.name for pb in PB if pb.name.startswith('DEF-Brow_local') and pb.name.endswith(side)]
    names.sort(key=lambda n: abs(PB[n].head.x))
    for i, n in enumerate(names):
        brow_order[n] = i / max(1, len(names) - 1)

roles = {}
gname = {g.index: g.name for g in head.vertex_groups}
for g in head.vertex_groups:
    n = g.name
    if not n.startswith('DEF-') or n.startswith(BODY):
        continue
    side = '.R' if n.endswith('.R') else ('.L' if n.endswith('.L') else '')
    flip = (lambda p: mirror(p)) if side == '.R' else (lambda p: p)
    h = PB[n].head if n in PB else None
    m = re.match(r'DEF-Lips_(upp|low)_micro\d*', n)
    if n.startswith('DEF-Lips_corn_micro'):
        roles[n] = {"chain": "lip_upp", "side": side, "u": 1.0}
    elif m:
        pts, par = lip_lines[m.group(1)]
        roles[n] = {"chain": "lip_" + m.group(1), "side": side or ".L",
                    "u": 0.0 if not side else proj_u(pts, par, flip(h))}
    elif n.startswith('DEF-Eyelid_corn_in'):
        roles[n] = {"chain": "lid_upp", "side": side, "u": 0.0}
    elif n.startswith('DEF-Eyelid_corn_out'):
        roles[n] = {"chain": "lid_upp", "side": side, "u": 1.0}
    elif re.match(r'DEF-Eyelid_(upp|low)\d+', n):
        which = 'upp' if '_upp' in n else 'low'
        pts, par = lid_lines[which]
        roles[n] = {"chain": "lid_" + which, "side": side, "u": proj_u(pts, par, flip(h))}
    elif n in brow_order:
        roles[n] = {"chain": "brow", "side": side, "u": round(brow_order[n], 4)}
    elif n in DIRECT:
        roles[n] = {"bones": DIRECT[n]}
    elif n.startswith(('DEF-jawline', 'DEF-Eyesocket_', 'DEF-Ear_')):
        roles[n] = {"bones": {n: 1.0}}
    else:
        roles[n] = {"bones": {"@HEAD": 1.0}}

group_ids = sorted(roles)
gidx = {n: i for i, n in enumerate(group_ids)}
vw = []
for v in hme.vertices:
    row = [[gidx[gname[g.group]], round(g.weight, 4)] for g in v.groups
           if g.weight > 0.002 and gname[g.group] in gidx]
    vw.append(row)

# --- landmarks (snapped to the storm head surface)
lm = {}
us = [i / 8 for i in range(9)]
for which in ('upp', 'low'):
    pts, par = lid_lines[which]
    for u in us:
        p = at(pts, par, u)
        lm[f"lid_{which}.L@{u:.3f}"] = snap(p)
        lm[f"lid_{which}.R@{u:.3f}"] = snap(mirror(p))
    pts, par = lip_lines[which]
    for s in (0.0, 0.25, 0.5, 0.75, 1.0):
        p = at(pts, par, s)
        lm[f"lip_{which}.L@{s:.3f}"] = snap(p)
        if s > 0:
            lm[f"lip_{which}.R@{s:.3f}"] = snap(mirror(p))
for side in ('.L', '.R'):
    names = sorted((n for n in brow_order if n.endswith(side)), key=lambda n: brow_order[n])
    for key, n in (("0", names[0]), ("1", names[-1]), ("m", names[len(names) // 2])):
        lm[f"brow{side}@{key}"] = snap(PB[n].head)
    for n in ('DEF-jawline1', 'DEF-jawline2', 'DEF-jawline3', 'DEF-Nostril'):
        if n + side in PB:
            lm[n + side] = snap(PB[n + side].head)
    lm["ear_base" + side] = snap(PB["DEF-Ear_base" + side].head)
    lm["ear_tip" + side] = snap(PB["DEF-Ear_tip" + side].tail)
    for n in socket:
        if n.startswith("DEF-") and n + side in PB:
            lm[n + side] = snap(PB[n + side].head)
for key, n in (("nose_tip", "DEF-Nose_tip"), ("nose_bridge", "DEF-Nose_bridge"), ("chin", "DEF-Chin")):
    lm[key] = snap(PB[n].head)

wdata = {
    "source": "Blender Studio 'storm' rig v1.1 (CC-BY 4.0): GEO-storm-head facial weights",
    "eye_dist": R5(EYE_DIST),
    "verts": [[R5(c) for c in p] for p in hverts],
    "polys": hpolys,
    "groups": group_ids,
    "roles": roles,
    "weights": vw,
    "landmarks": {k: [R5(c) for c in p] for k, p in lm.items()},
}
wout = os.path.join(os.path.dirname(out), "storm_face_weights.json")
with open(wout, 'w') as f:
    json.dump(wdata, f, separators=(',', ':'))
print('OKW', len(hverts), len(group_ids), len(lm))
