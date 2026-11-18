"""Extract storm's face lattices into hb_toolkit/facial/data/storm_lattice.json.

Run in background on storm_v1.1.blend:
  blender -b storm_v1.1.blend --python extract_storm_lattice.py -- <out.json>

Writes, all in storm world space (meters, storm faces -Y):
  lattices  grid size / interpolation / rest matrix, hooks (point indices +
            our bone), parent bone, mask group, which meshes get it, how to fit
  bones     new hook bones (head / tail / z axis / parent), ours by name
  shapes    custom shapes for those bones (same format as storm_template)
  masks     MSK-* weights per storm head vertex (same order as
            storm_face_weights.json "verts")
  ears      DEF-Ear_* weights per storm head vertex
  smooth    the head's Corrective Smooth settings
"""
import bpy, sys, json
from mathutils import Vector, Euler
out = sys.argv[sys.argv.index('--') + 1]
rig = bpy.data.objects['RIG-storm']; B = rig.data.bones; PB = rig.pose.bones
rig.animation_data_clear()
for pb in PB:
    pb.location = (0, 0, 0); pb.scale = (1, 1, 1)
    pb.rotation_quaternion = (1, 0, 0, 0); pb.rotation_euler = (0, 0, 0)
bpy.context.view_layer.update()
EYE_DIST = (B['MSTR-Eye.L'].head_local - B['MSTR-Eye.R'].head_local).length


def R5(x):
    return round(x, 5)


def vec(v):
    return [R5(c) for c in v]


# storm bone -> ours
OURS = {"STR-Face_upp": "STR-FACE_UPP", "MSTR-Face_low": "MSTR-FACE_LOW", "MSTR-Mouth": "MSTR-MOUTH",
        "MSTR-Teeth_upp": "MSTR-TEETH_UPP", "MSTR-Teeth_low": "MSTR-TEETH_LOW",
        "DEF-Teeth_upp": "DEF-TEETH_UPP", "DEF-Teeth_low": "DEF-TEETH_LOW",
        "DEF-Facemask_upp": "DEF-Facemask_upp", "DEF-Facemask_low": "DEF-Facemask_low"}
for s in ('.L', '.R'):
    OURS.update({f"CTL-Cheek_all{s}": f"CTL-cheek_all{s}", f"CTL-Cheek_puff{s}": f"CTL-Cheekpuff{s}",
                 f"DEF-Eye_upp{s}": f"DEF-Eye_upp{s}", f"DEF-Eye_low{s}": f"DEF-Eye_low{s}"})
    for k in (1, 2):
        for j in ('upp', 'low'):
            OURS[f"DEF-Teeth_{j}{k}{s}"] = f"DEF-TEETH_{j.upper()}{k}{s}"
NEW_BONES = ["DEF-Facemask_upp", "DEF-Facemask_low", "DEF-Eye_upp.L", "DEF-Eye_low.L",
             "DEF-Eye_upp.R", "DEF-Eye_low.R"]

# lattice -> (our name, mask group on the face mesh, target meshes, fit)
LAT = {
    "HLP-storm-lattice_face_mask": ("LTC-Facemask", "MSK-face_mask", ["face", "eye.L", "eye.R"], "warp"),
    "HLP-storm-lattice_Eye.L": ("LTC-Eye.L", "MSK-eye_lattice.L", ["face", "eye.L"], "warp"),
    "HLP-storm-lattice_Eye.R": ("LTC-Eye.R", "MSK-eye_lattice.R", ["face", "eye.R"], "warp"),
    "HLP-storm-lattice_Cheek.L": ("LTC-Cheek.L", "MSK-cheek_all.L", ["face"], "warp"),
    "HLP-storm-lattice_Cheek.R": ("LTC-Cheek.R", "MSK-cheek_all.R", ["face"], "warp"),
    "HLP-storm-lattice_Cheekpuff.L": ("LTC-Cheekpuff.L", "MSK-cheek_lattice.L", ["face"], "warp"),
    "HLP-storm-lattice_Cheekpuff.R": ("LTC-Cheekpuff.R", "MSK-cheek_lattice.R", ["face"], "warp"),
    "HLP-storm-lattice_Teeth_upp": ("LTC-Teeth_upp", None, ["teeth_upp"], "teeth_upp"),
    "HLP-storm-lattice_Teeth_low": ("LTC-Teeth_low", None, ["teeth_low"], "teeth_low"),
}
# storm modifier order on the head (lattice names)
FACE_ORDER = ["LTC-Cheek.L", "LTC-Cheek.R", "LTC-Cheekpuff.L", "LTC-Facemask", "LTC-Eye.L",
              "LTC-Eye.R", "LTC-Cheekpuff.R"]
FIT_OBJECTS = {"teeth_upp": ["GEO-storm-teeth_top", "GEO-storm-gums_top"],
               "teeth_low": ["GEO-storm-teeth_bottom", "GEO-storm-gums_bottom"]}


def bbox(names):
    pts = []
    for n in names:
        o = bpy.data.objects[n]
        pts += [o.matrix_world @ v.co for v in o.data.vertices]
    return [vec(Vector([min(p[i] for p in pts) for i in range(3)])),
            vec(Vector([max(p[i] for p in pts) for i in range(3)]))]


lattices = []
for sname, (ours, mask, targets, fit) in LAT.items():
    o = bpy.data.objects[sname]; lt = o.data
    arm_con = next((c for c in o.constraints if c.type == 'ARMATURE'), None)
    parent = OURS.get(arm_con.targets[0].subtarget) if arm_con and arm_con.targets else None
    hooks = []
    for m in o.modifiers:
        if m.type != 'HOOK':
            continue
        g = o.vertex_groups.get(m.vertex_group)
        idx = [i for i, p in enumerate(lt.points) for gg in p.groups
               if g and gg.group == g.index and gg.weight > 0]
        hooks.append({"bone": OURS[m.subtarget], "group": m.vertex_group, "points": idx})
    e = {"name": ours, "storm": sname, "size": [lt.points_u, lt.points_v, lt.points_w],
         "interp": [lt.interpolation_type_u, lt.interpolation_type_v, lt.interpolation_type_w],
         "outside": lt.use_outside, "matrix": [vec(r) for r in o.matrix_world],
         "parent": parent, "mask": mask, "targets": targets, "fit": fit, "hooks": hooks}
    if fit in FIT_OBJECTS:
        e["fit_bbox"] = bbox(FIT_OBJECTS[fit])
    lattices.append(e)

bones = {}
for n in NEW_BONES:
    b = B[n]; pb = PB[n]
    m = rig.matrix_world @ pb.matrix
    bones[n] = {"storm": n, "head": vec(m.translation), "tail": vec(rig.matrix_world @ pb.tail),
                "z": vec(m.to_3x3().normalized().col[2]), "parent": OURS[b.parent.name],
                "deform": False}

shapes, used = {}, set()
for n in NEW_BONES:
    pb = PB[n]
    if pb.custom_shape is None:
        continue
    dpb = pb.custom_shape_transform or pb
    D3 = rig.matrix_world.to_3x3().normalized() @ dpb.matrix.to_3x3().normalized()
    ln = pb.bone.length if pb.use_custom_shape_bone_size else 1.0
    R = D3 @ Euler(pb.custom_shape_rotation_euler, 'XYZ').to_matrix()
    off = D3 @ Vector(pb.custom_shape_translation)
    shapes[n] = {"storm": n, "wgt": pb.custom_shape.name, "rot": [vec(r) for r in R],
                 "size": [R5(x * ln / EYE_DIST) for x in pb.custom_shape_scale_xyz],
                 "offset": [R5(x / EYE_DIST) for x in off],
                 "offset_bone": [R5(x / EYE_DIST) for x in off + dpb.head - pb.head],
                 "wire": round(getattr(pb, 'custom_shape_wire_width', 1.0), 2),
                 "color": pb.bone.color.palette}
    used.add(pb.custom_shape.name)
widgets = {}
for n in sorted(used):
    me = bpy.data.objects[n].data
    widgets[n] = {"verts": [vec(v.co) for v in me.vertices], "edges": [list(e.vertices) for e in me.edges]}

head = bpy.data.objects['GEO-storm-head']; hme = head.data
gname = {g.index: g.name for g in head.vertex_groups}
MASKS = sorted({l["mask"] for l in lattices if l["mask"]} | {"MSK-corrective_smooth"})
EARS = ["DEF-Ear_base.L", "DEF-Ear_tip.L", "DEF-Ear_base.R", "DEF-Ear_tip.R"]


def sparse(names):
    res = {n: [] for n in names}
    for v in hme.vertices:
        for g in v.groups:
            n = gname[g.group]
            if n in res and g.weight > 0.002:
                res[n].append([v.index, round(g.weight, 4)])
    return res


cs = next(m for m in head.modifiers if m.type == 'CORRECTIVE_SMOOTH')
data = {
    "source": "Blender Studio 'storm' rig v1.1 (CC-BY 4.0): face lattices, masks, ear weights",
    "eye_dist": R5(EYE_DIST),
    "face_order": FACE_ORDER,
    "lattices": lattices, "bones": bones, "shapes": shapes, "widgets": widgets,
    "masks": sparse(MASKS), "ears": sparse(EARS),
    "smooth": {"factor": round(cs.factor, 3), "iterations": cs.iterations, "scale": cs.scale,
               "smooth_type": cs.smooth_type, "rest_source": cs.rest_source,
               "use_only_smooth": cs.use_only_smooth, "use_pin_boundary": cs.use_pin_boundary,
               "mask": cs.vertex_group},
    "head_verts": len(hme.vertices),
}
with open(out, 'w') as f:
    json.dump(data, f, separators=(',', ':'))
print('OKL', len(lattices), len(bones), len(shapes), {k: len(v) for k, v in data["masks"].items()})
