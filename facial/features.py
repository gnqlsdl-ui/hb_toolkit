"""Storm-style face features built with the controls stage.

Eyesocket  CTL-Eyesocket_upp/low (outer ring masters, under MSTR-EYE) with
           DEF-Eyesocket_in/mid/out_upp/low under them and DEF-Eyesocket_in /
           _out corners: placed from storm's ring around the eyelid line,
           snapped to the head mesh.
Ears       DEF-Ear_base -> DEF-Ear_tip deform chain under MSTR-EAR.
Blink      PRP-Eye_blink.L/R (pull down = blink) holds "Blink Height" and
           "Eyelid Follow". A blink ribbon per eye runs along the lower lid
           (shape key Blink_height lifts it towards the upper lid); every
           eyelid MCH gets a second Damped Track to it, driven by the PRP.
Lip Zip    CTL-Lips_corn.L/R "LipZip Sharpness"; scaling the corner down
           zips the lips from that corner: each lip HK- empty copies the
           location of its HK-Zip group (its point + the opposite point).

Values (positions, defaults, travel) come from data/storm_template.json.
"""

import bpy
from mathutils import Vector

from . import template as T
from . import operators as O

BLINK_RIBBON = "Ribbon_Mesh-Blink{side}"
BLINK_TAG = "BLINK_RIBBON{side}"
BLINK_GROUP = "BLK-"
BLINK_KEY = "Blink_height"
PRP = "PRP-Eye_blink{side}"
TRACK_BLINK = "Track_Blink"
ZIP_CONS = ("Zip_L", "Zip_R")
NOSE_CONS = ("Nose_Follow_Lips", "Nose_Limit")
NOSE_TARGET = "MCH-Lips_upp"          # storm MCH-lip_mid_upp: tail on the upper lip center
SIDES = (".L", ".R")


def _R():
    from . import rig
    return rig


def _extras():
    return _R().storm().get("extras", {})


# ----------------------------------------------------------------------
# Eyesocket markers (Add Markers)
# ----------------------------------------------------------------------
def _lid_curve(a, m, b, n=24):
    """Points of the quadratic through a (t=0), m (t=.5), b (t=1)."""
    out = []
    for i in range(n + 1):
        t = i / n
        la, lm_, lb = 2 * (t - 0.5) * (t - 1), -4 * t * (t - 1), 2 * t * (t - 0.5)
        out.append(a * la + m * lm_ + b * lb)
    return out


def eyesocket_spots(lid, mesh, side):
    """{eyesocket marker: world} from the eyelid markers {In, Upp, Out, Low}
    with storm's ring (u along the lid line, scale from the lid centroid)."""
    R = _R()
    lines = {"upp": _lid_curve(lid["In"], lid["Upp"], lid["Out"]),
             "low": _lid_curve(lid["In"], lid["Low"], lid["Out"])}
    pars = {w: R._params(p) for w, p in lines.items()}
    cen = sum(lines["upp"] + lines["low"], Vector()) / (len(lines["upp"]) * 2)
    ring = _extras().get("eyesocket", {})
    out = {}
    for mk, bones in T.EYESOCKET_MARKERS.items():
        spec = ring.get(bones[0])
        if spec is None:
            continue
        lp = R._at(lines[spec["line"]], pars[spec["line"]], spec["u"])
        d = lp - cen
        p = cen + Vector((d.x * spec["s"], d.y, d.z * spec["s"]))
        out[mk if side == ".L" else T.flip(mk)] = O.snap_to_surface(mesh, p)
    return out


# ----------------------------------------------------------------------
# Bones (planned inside Build Controls' edit-mode pass)
# ----------------------------------------------------------------------
def plan_bones(add, arm, props, lines, k, inv, up, clen):
    """Queue eyesocket / ear / blink-control bones through ``add``."""
    R = _R()
    bones = arm.data.bones
    ex = _extras()
    mesh = props.mesh

    def vert(p_arm, length):
        return p_arm, p_arm + up * length

    for side in SIDES:
        slot = "EYE" + side.replace(".", "_")
        eye = T.EYE_MASTER.format(side=side)
        if slot in lines and eye in bones:
            upp, low = lines[slot]
            pts_l = {"upp": [p for _n, p in upp], "low": [p for _n, p in low]}
            par_l = {w: R._params(p) for w, p in pts_l.items()}
            allp = pts_l["upp"] + pts_l["low"]
            cen = sum(allp, Vector()) / len(allp)
            marker_of = {b: mk for mk, bs in T.EYESOCKET_MARKERS.items() for b in bs}
            for name, spec in ex.get("eyesocket", {}).items():
                mk = marker_of.get(name)
                mk = mk if side == ".L" or mk is None else T.flip(mk)
                obj = bpy.data.objects.get(mk) if mk else None
                if obj is not None:                    # placed marker wins
                    p = O.obj_world(obj)
                else:
                    lp = R._at(pts_l[spec["line"]], par_l[spec["line"]], spec["u"])
                    d = lp - cen
                    p = cen + Vector((d.x * spec["s"], d.y, d.z * spec["s"]))
                    p = O.snap_to_surface(mesh, p)
                parent = eye if spec["parent"] == "EYE" else spec["parent"] + side
                deform = name.startswith("DEF-")
                add(name + side, *vert(inv @ p, clen * 0.8), parent, slot, deform=deform)
            bl = ex.get("blink")
            if bl:
                corner = pts_l["upp"][-1]
                off = Vector(bl["prp_offset"]) * k
                if side == ".R":
                    off.x = -off.x
                head = inv @ (corner + off)
                add(PRP.format(side=side), *vert(head, bl["prp_length"] * k), eye, slot)
        ear = f"MSTR-EAR{side}"
        if ear in bones:
            b = bones[ear]
            mid = b.head_local.lerp(b.tail_local, 0.5)
            add(f"DEF-Ear_base{side}", b.head_local.copy(), mid, ear, "EAR" + side.replace(".", "_"),
                deform=True, roll=("copy", ear))
            add(f"DEF-Ear_tip{side}", mid.copy(), b.tail_local.copy(), f"DEF-Ear_base{side}",
                "EAR" + side.replace(".", "_"), deform=True, roll=("copy", ear))


# ----------------------------------------------------------------------
# Runtime setup (props, constraints, drivers, blink ribbon)
# ----------------------------------------------------------------------
def _driver(target, prop, expr, variables):
    """(Re)create a scripted driver. variables: (name, 'TRANSFORMS', (id, bone, type[, space]))
    or (name, 'SINGLE_PROP', (id, data_path)). space defaults to LOCAL_SPACE
    (after constraints); TRANSFORM_SPACE reads the raw channel."""
    try:
        target.driver_remove(prop)
    except (TypeError, RuntimeError):
        pass
    fc = target.driver_add(prop)
    d = fc.driver
    d.type = 'SCRIPTED'
    for v in list(d.variables):
        d.variables.remove(v)
    for name, kind, args in variables:
        v = d.variables.new()
        v.name, v.type = name, kind
        t = v.targets[0]
        if kind == 'TRANSFORMS':
            t.id, t.bone_target, t.transform_type = args[:3]
            t.transform_space = args[3] if len(args) > 3 else 'LOCAL_SPACE'
        else:
            t.id_type = 'OBJECT'
            t.id, t.data_path = args
    d.expression = expr
    return fc


def _prop(pb, name, default, lo, hi):
    if name not in pb:
        pb[name] = default
    ui = pb.id_properties_ui(name)
    ui.update(min=lo, max=hi, soft_min=lo, soft_max=hi, default=default)
    try:
        pb.property_overridable_library_set(f'["{name}"]', True)
    except (TypeError, AttributeError):
        pass


BLINK_EDIT = "hb_blink_edit"       # armature prop (JSON): hand edits of the blink lines
LIFT_PROP = "hb_blink_lift"         # ribbon prop: the Blink_height line as built (flat xyz)


def _save_blink_edits(arm):
    """Keep the hand edits of each blink ribbon's Blink_height key (the closed
    line) on the armature, per eyelid point: rebuilding makes the ribbon again."""
    import json
    saved = json.loads(arm.get(BLINK_EDIT, "{}"))
    for side in SIDES:
        tag = BLINK_TAG.format(side=side)
        rib = next((o for o in bpy.data.objects if o.get(O.OBJ_PROP) == tag), None)
        keys = rib.data.shape_keys if rib is not None and rib.type == 'MESH' else None
        if keys is None or BLINK_KEY not in keys.key_blocks:
            continue
        key, basis = keys.key_blocks[BLINK_KEY], keys.reference_key
        n = len(rib.data.vertices) // 2
        names = {g.index: g.name[len(BLINK_GROUP):] for g in rib.vertex_groups
                 if g.name.startswith(BLINK_GROUP)}
        lift = list(rib.get(LIFT_PROP, []))
        pts = {}
        for v in rib.data.vertices[:n]:
            nm = next((names[e.group] for e in v.groups if e.group in names), None)
            if nm is None:
                continue
            i = v.index
            co0, co1 = key.data[i].co, key.data[i + n].co
            off = basis.data[i + n].co - basis.data[i].co
            d1 = co1 - co0 - off
            default = Vector(lift[3 * i:3 * i + 3]) if len(lift) >= 3 * n else None
            if default is not None and (co0 - default).length < 1e-6 and d1.length < 1e-6:
                continue                           # as built
            pts[nm] = {"co": list(co0), "d1": list(d1),
                       "def": list(default) if default is not None else None}
        saved[side] = pts
    arm[BLINK_EDIT] = json.dumps(saved)


def clear_runtime(arm):
    """Remove what setup() made outside the bones (also used by Clear Controls)."""
    _save_blink_edits(arm)
    O._remove_objects({BLINK_TAG.format(side=s) for s in SIDES})
    for pb in arm.pose.bones:
        for c in [c for c in pb.constraints if c.name in (TRACK_BLINK,) + NOSE_CONS]:
            pb.constraints.remove(c)
    for obj in [o for o in bpy.data.objects if o.get(O.OBJ_PROP) == "LIPS_HOOK"]:
        for c in [c for c in obj.constraints if c.name in ZIP_CONS]:
            obj.constraints.remove(c)
        if obj.animation_data:
            for fc in list(obj.animation_data.drivers):
                if any(n in fc.data_path for n in ZIP_CONS):
                    obj.animation_data.drivers.remove(fc)
    prune_drivers(arm)


def prune_drivers(arm):
    """Drop armature drivers whose target path no longer exists."""
    ad = arm.animation_data
    if not ad:
        return 0
    dead = []
    for fc in ad.drivers:
        try:
            arm.path_resolve(fc.data_path)
        except ValueError:
            dead.append(fc)
    for fc in dead:
        ad.drivers.remove(fc)
    return len(dead)


def _blink_ribbon(context, arm, props, side, upp, low):
    """Lower-lid line whose shape key lifts it to the upper lid; one BLK-
    vertex per eyelid point (same arc-length parameter on the other line)."""
    R = _R()
    name = BLINK_RIBBON.format(side=side)
    tag = BLINK_TAG.format(side=side)
    O._remove_objects({tag})
    pu = [p for _n, p in upp]; pl = [p for _n, p in low]
    par_u, par_l = R._params(pu), R._params(pl)
    order = []            # (point name, u, which)
    for which, line, par in (("upp", upp, par_u), ("low", low, par_l)):
        for (nm, _p), u in list(zip(line, par))[1:-1]:
            order.append((nm, u, which))
    n = len(order)
    off = Vector((0.0, -O._ribbon_offset(arm, props), 0.0))
    base = [R._at(pl, par_l, u) for _nm, u, _w in order]
    lift = [R._at(pu, par_u, u) for _nm, u, _w in order]
    me = bpy.data.meshes.new(name)
    me.from_pydata([tuple(p) for p in base] + [tuple(p + off) for p in base],
                   [(i, i + n) for i in range(n)], [])
    me.update()
    rib = bpy.data.objects.new(name, me)
    rib[O.OBJ_PROP] = tag
    rib.hide_render = True
    rib.display_type = 'WIRE'
    O._ribbon_collection(context).objects.link(rib)
    rib.vertex_groups.new(name=O.MASK_GROUP).add(list(range(n)), 1.0, 'REPLACE')
    index = {}
    for i, (nm, _u, _w) in enumerate(order):
        rib.vertex_groups.new(name=BLINK_GROUP + nm).add([i], 1.0, 'REPLACE')
        index[nm] = i
    rib.shape_key_add(name="Basis", from_mix=False)
    key = rib.shape_key_add(name=BLINK_KEY, from_mix=False)
    rib[LIFT_PROP] = [c for p in lift for c in p]
    import json
    edits = json.loads(arm.get(BLINK_EDIT, "{}")).get(side, {})
    for i, p in enumerate(lift):
        e = edits.get(order[i][0])
        if e:                    # the closed line as edited by hand (kept on the armature)
            q = p + (Vector(e["co"]) - (Vector(e["def"]) if e["def"] else p))
            key.data[i].co = q
            key.data[i + n].co = q + off + Vector(e["d1"])
        else:
            key.data[i].co = p
            key.data[i + n].co = p + off
    # rides both lids: half the lower-lid and half the upper-lid weights
    wmap = {}
    for nm, u, _w in order:
        w = {}
        for which in ("upp", "low"):
            for b, x in R._lid_weights(which, side, u).items():
                w[b] = w.get(b, 0.0) + 0.5 * x
        wmap[nm] = w
    O._weight_ribbon(rib, arm, index, lambda nm: wmap.get(nm, {}))
    return rib, [nm for nm, _u, _w in order]


def setup(context, arm, props, lines, k):
    """Props, limits, drivers, blink ribbons and lip zip. Returns notes."""
    ex = _extras()
    notes = []
    pbs = arm.pose.bones
    clear_runtime(arm)
    defaults = ex.get("props", {})

    # --- blink / eyelid follow ---
    bl = ex.get("blink", {})
    travel = bl.get("full_travel", 0.4) * k
    for side in SIDES:
        prp = PRP.format(side=side)
        slot = "EYE" + side.replace(".", "_")
        if prp not in pbs:
            continue
        pb = pbs[prp]
        for name, (dv, lo, hi) in defaults.get("PRP-Eye_blink", {}).items():
            _prop(pb, name, dv, lo, hi)
        for c in [c for c in pb.constraints if c.name == "Blink_Limit"]:
            pb.constraints.remove(c)
        lim = pb.constraints.new('LIMIT_LOCATION')
        lim.name = "Blink_Limit"
        lim.owner_space = 'LOCAL'
        lim.use_transform_limit = True
        ly = bl.get("limit_y", [-0.4, 0.0])
        for ax in "xyz":
            setattr(lim, f"use_min_{ax}", True)
            setattr(lim, f"use_max_{ax}", True)
        lim.min_y, lim.max_y = ly[0] * k, ly[1] * k
        path = f'pose.bones["{prp}"]'
        for n, mul in ((f"MCH-Eyelid_follow{side}", 1.0), (f"MCH-Eyelid_follow_corn{side}", 0.3)):
            c = pbs[n].constraints.get("Follow_Eye") if n in pbs else None
            if c is not None:
                _driver(c, "influence", f"var * {mul}",
                        [("var", 'SINGLE_PROP', (arm, path + '["Eyelid Follow"]'))])
        if slot not in lines:
            continue
        upp, low = lines[slot]
        rib, names = _blink_ribbon(context, arm, props, side, upp, low)
        _driver(rib.data.shape_keys.key_blocks[BLINK_KEY], "value", "h",
                [("h", 'SINGLE_PROP', (arm, path + '["Blink Height"]'))])
        tracked = 0
        for nm in names:
            mch = "MCH-" + nm
            if mch not in pbs:
                continue
            c = pbs[mch].constraints.new('DAMPED_TRACK')
            c.name = TRACK_BLINK
            c.target, c.subtarget = rib, BLINK_GROUP + nm
            c.track_axis = 'TRACK_Y'
            _driver(c, "influence", f"min(1, max(0, -loc / {travel:.6f}))",
                    [("loc", 'TRANSFORMS', (arm, prp, 'LOC_Y'))])
            tracked += 1
        if not tracked:
            notes.append(f"블링크{side}: 눈꺼풀 MCH 없음 (MCH 생성을 실행하세요)")

    # --- nose follows the lips (storm STR-Nose) ---
    if "STR-Nose" in pbs and "DEF-Nose" in pbs and NOSE_TARGET in pbs:
        dv, lo, hi = defaults.get("DEF-Nose", {}).get("Nose Follow Lips", [0.2, 0.0, 1.0])
        _prop(pbs["DEF-Nose"], "Nose Follow Lips", dv, lo, hi)
        c = pbs["STR-Nose"].constraints.new('DAMPED_TRACK')
        c.name = NOSE_CONS[0]
        c.target, c.subtarget = arm, NOSE_TARGET
        c.head_tail = 1.0
        c.track_axis = 'TRACK_Y'
        _driver(c, "influence", "v",
                [("v", 'SINGLE_PROP', (arm, 'pose.bones["DEF-Nose"]["Nose Follow Lips"]'))])
        lim = pbs["STR-Nose"].constraints.new('LIMIT_ROTATION')
        lim.name = NOSE_CONS[1]
        lim.owner_space = 'LOCAL'
        lim.use_limit_x = True
        lim.min_x = lim.max_x = 0.0
    elif "STR-Nose" in pbs:
        notes.append("코 입술 따라가기: MCH-Lips_upp 없음 (입술 루프를 등록하세요)")

    # --- lip zip ---
    zip_def = ex.get("zip", {})
    gain, ck = zip_def.get("scale_gain", 3.0), zip_def.get("corner_k", 1.2)
    rib = bpy.data.objects.get(O.RIBBON_LIPS)
    corners = [f"CTL-Lips_corn{s}" for s in SIDES]
    if 'LIPS' in lines and rib is not None and all(c in pbs for c in corners):
        for cn in corners:
            for name, (dv, lo, hi) in defaults.get("CTL-Lips_corn", {}).items():
                _prop(pbs[cn], name, dv, lo, hi)
        R = _R()
        made = 0
        for line in lines['LIPS']:
            names = [n for n, _p in line]
            par = R._params([p for _n, p in line])
            for nm, u in list(zip(names, par))[1:-1]:
                hook = bpy.data.objects.get(O.HOOK_PREFIX + nm)
                group = O.ZIP_PREFIX + nm
                if hook is None or group not in rib.vertex_groups:
                    continue
                for cname, corner, kk in (("Zip_L", corners[0], ck * u),
                                          ("Zip_R", corners[1], ck * (1 - u))):
                    c = hook.constraints.new('COPY_LOCATION')
                    c.name = cname
                    c.target, c.subtarget = rib, group
                    _driver(c, "influence", f"(1 - s) * {gain} - {kk:.4f} * sh",
                            # raw scale: the corner's Limit Scale pins the evaluated one to 1
                            [("s", 'TRANSFORMS', (arm, corner, 'SCALE_AVG', 'TRANSFORM_SPACE')),
                             ("sh", 'SINGLE_PROP', (arm, f'pose.bones["{corner}"]["LipZip Sharpness"]'))])
                made += 1
        if not made:
            notes.append("립 집: HK- 엠프티 없음 (가이드 생성을 실행하세요)")
    return notes
