"""Marker objects: colored, easy-to-click shapes plus selection helpers.

Markers are small mesh objects (not empties) so they can carry a color in
the viewport and give a solid click target: Root = green sphere, Tip = red
cube, chain middle points = part-colored small sphere, point markers =
yellow sphere. They never render. Only their position is ever read, so any
object with a marker name (including empties from older versions) works;
:func:`convert_markers` swaps old empties for the colored shapes in place.
"""

import bmesh
import bpy

from . import template as T

OBJ_PROP = "hb_facial_obj"     # same tag as operators.OBJ_PROP
LOCK_KEY = "hb_facial_locked"  # scene property: objects made unselectable
VERSION_KEY = "hb_marker_v"    # marker naming version stamped on new markers
MARKER_VERSION = 2

COLORS = {
    "root": (0.2, 1.0, 0.35, 1.0),
    "tip": (1.0, 0.25, 0.25, 1.0),
    "point": (1.0, 0.9, 0.2, 1.0),
    "eye": (0.3, 0.85, 1.0, 1.0),
    "NOSE": (1.0, 0.75, 0.2, 1.0),
    "EYELID": (0.3, 0.85, 1.0, 1.0),
    "EYESOCKET": (0.25, 0.55, 1.0, 1.0),
    "BROW": (0.75, 0.45, 1.0, 1.0),
    "TONGUE": (1.0, 0.45, 0.7, 1.0),
    "TEETH_UPP": (0.95, 0.95, 0.95, 1.0),
    "TEETH_LOW": (0.6, 0.6, 0.6, 1.0),
    "CHEEK": (1.0, 0.55, 0.35, 1.0),
    "JAWLINE": (0.2, 0.85, 0.75, 1.0),
}
# radius (or half size for the cube) as a fraction of the Head bone length
SIZES = {"root": 0.012, "tip": 0.009, "mid": 0.007, "point": 0.014}


def kind(name):
    if name in T.ROOT_MARKERS:
        return "root"
    if name in T.TIP_MARKERS:
        return "tip"
    if name in T.POINT_MARKERS:
        return "point"
    return "mid"


def color_key(name):
    if name in ("MK-Nose_Bridge", "MK-Nose_Tip", "MK-Nostril.L", "MK-Nostril.R"):
        return "NOSE"
    if name.startswith(("MK-Eyelid_Upp", "MK-Eyelid_Low")):
        return "EYELID"
    if name.startswith("MK-Eyesocket_"):
        return "EYESOCKET"
    k = kind(name)
    if k != "mid":
        return k
    for chain, spec in T.CHAINS.items():
        names = {n for n, _p in spec["markers"]} | {T.flip(n) for n, _p in spec["markers"]}
        if name in names:
            return chain
    return "point"


def _shape_mesh(shape):
    name = f"MK_Shape_{shape}"
    me = bpy.data.meshes.get(name)
    if me is not None:
        return me
    me = bpy.data.meshes.new(name)
    bm = bmesh.new()
    if shape == "Cube":
        bmesh.ops.create_cube(bm, size=2.0)
    else:
        bmesh.ops.create_icosphere(bm, subdivisions=2, radius=1.0)
    bm.to_mesh(me)
    bm.free()
    me.materials.append(None)
    return me


def _material(key):
    name = f"MK_Color_{key}"
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name)
    rgba = COLORS[key]
    mat.diffuse_color = rgba
    if mat.use_nodes and mat.node_tree:
        bsdf = next((n for n in mat.node_tree.nodes if n.type == 'BSDF_PRINCIPLED'), None)
        if bsdf is not None:
            bsdf.inputs[0].default_value = rgba
    return mat


def marker_collection(context):
    coll = bpy.data.collections.get(T.MARKER_COLL)
    if coll is None:
        coll = bpy.data.collections.new(T.MARKER_COLL)
        context.scene.collection.children.link(coll)
    return coll


def new_marker(context, name, location, head_length):
    """Create one colored marker object at ``location`` (world)."""
    k = kind(name)
    me = _shape_mesh("Cube" if k == "tip" else "Sphere")
    obj = bpy.data.objects.new(name, me)
    obj.material_slots[0].link = 'OBJECT'
    key = color_key(name)
    obj.material_slots[0].material = _material(key)
    obj.color = COLORS[key]
    s = head_length * SIZES[k]
    obj.scale = (s, s, s)
    obj.location = location
    obj.show_name = True
    obj.show_in_front = True
    obj.hide_render = True
    obj.visible_shadow = False
    obj[OBJ_PROP] = "MARKER"
    obj[VERSION_KEY] = MARKER_VERSION      # current naming: never renamed by migration
    marker_collection(context).objects.link(obj)
    return obj


def convert_markers(context, head_length):
    """Replace marker empties (older versions) with colored marker shapes."""
    done = 0
    for name in T.MARKERS:
        obj = bpy.data.objects.get(name)
        if obj is None or obj.type != 'EMPTY':
            continue
        loc = obj.matrix_world.translation.copy()
        bpy.data.objects.remove(obj, do_unlink=True)
        new_marker(context, name, loc, head_length)
        done += 1
    return done


def marker_groups():
    """[(label, [marker names])] in panel order."""
    g = [("Face Upp", ["MK-FaceUpp_Root", "MK-FaceUpp_Tip"]),
         ("Face Low", ["MK-FaceLow_Root", "MK-FaceLow_Tip"]),
         ("Nose", ["MK-Nose_Root", "MK-Nose_Base"]),
         ("Nose DEF", ["MK-Nose_Bridge", "MK-Nose_Tip", "MK-Nostril.L", "MK-Nostril.R"]),
         ("Skull", ["MK-Skull"]),
         ("Eye .L", ["MK-Eye.L", "MK-Eye_Pupil.L"]),
         ("Eye .R", ["MK-Eye.R", "MK-Eye_Pupil.R"]),
         ("Eyelid .L", list(T.EYELID_MARKERS)),
         ("Eyelid .R", [T.flip(n) for n in T.EYELID_MARKERS]),
         ("Eyesocket .L", list(T.EYESOCKET_MARKERS)),
         ("Eyesocket .R", [T.flip(n) for n in T.EYESOCKET_MARKERS]),
         ("Ear .L", ["MK-Ear_Root.L", "MK-Ear_Tip.L"]),
         ("Ear .R", ["MK-Ear_Root.R", "MK-Ear_Tip.R"])]
    brow = [n for n, _p in T.CHAINS["BROW"]["markers"]]
    g += [("Brow .L", brow), ("Brow .R", [T.flip(n) for n in brow])]
    names = [n for n, _p in T.CHAINS["CHEEK"]["markers"]]
    g += [("Cheek .L", names), ("Cheek .R", [T.flip(n) for n in names])]
    c, *side = [n for n, _p in T.CHAINS["JAWLINE"]["markers"]]
    g.append(("Jawline", [T.flip(n) for n in reversed(side)] + [c] + side))
    g += [("Cheek Puff", ["MK-CheekPuff.L", "MK-CheekPuff.R"]), ("Chin", ["MK-Chin"])]
    g.append(("Tongue", [n for n, _p in T.CHAINS["TONGUE"]["markers"]]))
    for chain, label in (("TEETH_UPP", "Teeth Upp"), ("TEETH_LOW", "Teeth Low")):
        f, s, b = [n for n, _p in T.CHAINS[chain]["markers"]]
        g.append((label, [T.flip(b), T.flip(s), f, s, b]))
    return g


def short_label(name):
    """Button text: 'MK-Brow_Mid1.L' -> 'Mid1.L', 'MK-Skull' -> 'Skull'."""
    base = name[3:]
    return base.split("_", 1)[1] if "_" in base else base


def lock_others(scene, lock):
    """Make every non-marker object unselectable (lock) or undo that."""
    if lock:
        names = [o.name for o in scene.objects
                 if o.get(OBJ_PROP) != "MARKER" and not o.hide_select]
        for n in names:
            scene.objects[n].hide_select = True
        scene[LOCK_KEY] = names
        return len(names)
    names = list(scene.get(LOCK_KEY, []))
    for n in names:
        obj = scene.objects.get(n)
        if obj is not None:
            obj.hide_select = False
    if LOCK_KEY in scene:
        del scene[LOCK_KEY]
    return len(names)
