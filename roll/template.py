"""Roll mechanism template (single source of truth).

This module holds *pure data* describing how the forearm/hand roll
(twist-distribution) network is derived from a set of user-placed
``Guide-`` bones. ``operators.py`` consumes this template to stamp out
the System / Ctrl / DEF bones, their constraints, bone collections and
control-shape widgets.

Design contract (confirmed with the rig author):

* The user manually places 6 guide bones per segment:
  ``GRP_Guide-{seg}``, ``Guide-{seg}_Roll_Back``, ``Guide-{seg}_Roll_Front``,
  ``Guide-{seg}_Roll_In``, ``Guide-{seg}_Roll_Out`` and the main
  ``Guide-{seg}`` (the DEF-main, which defines the build orientation).
* The local frame mixes the main guide (for the segment axis) with the roll
  guides (for the cross-section), so the build matches the placed guides in
  all three axes regardless of how the main guide is rolled:
    - ``joint``     = main guide head
    - ``seg``       = main head -> tail (the bone axis)
    - ``up``        = toward the Back guide (Roll_Back/Front offset + roll plane)
    - ``side``      = toward the Out guide  (the In -> Out axis)
  Roll bones are rolled so their local +Z points at -seg; segment-aligned
  bones point their local +Z at ``up``.
* Generated bones anchor their *head* to a guide point and point along one
  frame axis. Helper lengths are template constants times the user ``size``;
  the main/DEF bones copy the segment geometry and ``System_Roll_In`` spans
  from the Roll_In guide head to the Roll_Out guide head.

Everything was captured from the reference ``.L`` setup (all bone rolls
are 0, all Transformation constraints are LOCAL/LOCAL identity passes of
100 degrees with ADD mixing).
"""

import math

# Captured Transformation constraint ranges.
ROLL_RAD = math.radians(100.0)  # Roll_Out / Roll_In passes (Ctrl-Roll driven)
ARM_RAD = math.radians(170.0)   # Roll_Back / Roll_Front passes (Ctrl-FK driven)

# --- frame anchor keys -------------------------------------------------
ANCHOR_JOINT = "joint"
ANCHOR_BACK = "back_head"
ANCHOR_FRONT = "front_head"
ANCHOR_IN_HEAD = "in_head"
ANCHOR_IN_TAIL = "in_tail"
ANCHOR_OUT = "out_head"   # Roll_Out guide head

# --- direction keys ----------------------------------------------------
DIR_SEG = "seg"
DIR_UP = "up"
DIR_NUP = "nup"
DIR_SIDE = "side"

# --- length specials ---------------------------------------------------
LEN_MAIN = "MAIN"    # tail == guide main tail (copies segment geometry)
LEN_IN = "IN"        # tail == guide In tail (legacy single-In layout)
LEN_TO_OUT = "TO_OUT"  # tail == Roll_Out guide head (In spans In->Out)

# Guide bone name templates (the user-placed input).
GUIDE_ROOT = "GRP_Guide-{seg}{side}"
GUIDE_BACK = "Guide-{seg}_Roll_Back{side}"
GUIDE_FRONT = "Guide-{seg}_Roll_Front{side}"
GUIDE_IN = "Guide-{seg}_Roll_In{side}"
GUIDE_OUT = "Guide-{seg}_Roll_Out{side}"
GUIDE_MAIN = "Guide-{seg}{side}"

GUIDE_NAMES = (GUIDE_ROOT, GUIDE_BACK, GUIDE_FRONT, GUIDE_IN, GUIDE_OUT,
               GUIDE_MAIN)

# Role tags written onto each guide bone ('hb_role' custom property). These
# identify a guide's function independent of its name, so guides can be freely
# renamed / reused and the Build step still finds them by role rather than by
# parsing the bone name.
ROLE_ROOT = "root"
ROLE_BACK = "back"
ROLE_FRONT = "front"
ROLE_IN = "in"
ROLE_OUT = "out"
ROLE_MAIN = "main"

# Maps a guide name template to its role. Order matches GUIDE_DEFAULTS keys.
GUIDE_ROLES = {
    GUIDE_ROOT: ROLE_ROOT,
    GUIDE_BACK: ROLE_BACK,
    GUIDE_FRONT: ROLE_FRONT,
    GUIDE_IN: ROLE_IN,
    GUIDE_OUT: ROLE_OUT,
    GUIDE_MAIN: ROLE_MAIN,
}

# Default guide layout for the "Create Guides" button. Offsets are in the
# default orientation (up = +Y, segment = -Z, in/out = +X) relative to the
# placement origin, and are scaled by the user ``size``. The user then
# moves these onto the mesh before building.
#   (name_template, head_offset, tail_offset, parent_to_root)
# Default layout matches the main guide's natural local frame for a -Z bone
# (roll 0): up = local Z = world -X, side = local X = world +Y. This keeps
# Create Guides consistent with "Fit Rolls to Main".
# Back/Front heads sit at -/+ up (the X axis here); their tails both point
# along -up (+X), as captured from the reference.
# In/Out are now two parallel guides (heads at +/- side); the Build spans
# System_Roll_In from the In head to the Out head. Their short +X tails are
# just visual handles and are not read by the build.
GUIDE_DEFAULTS = (
    (GUIDE_ROOT, (0.0, 0.0, 0.0), (0.0, 0.0, -0.023), False),
    (GUIDE_BACK, (-0.117, 0.0, 0.0), (-0.001, 0.0, 0.0), True),
    (GUIDE_FRONT, (0.117, 0.0, 0.0), (0.213, 0.0, 0.0), True),
    (GUIDE_IN, (0.0, 0.331, 0.0), (0.098, 0.331, 0.0), True),
    (GUIDE_OUT, (0.0, -0.331, 0.0), (0.098, -0.331, 0.0), True),
    (GUIDE_MAIN, (0.0, 0.0, 0.0), (0.0, 0.0, -1.0), True),
)

# Cross-section ratios (relative to the main guide's length), captured from
# the reference. Used by "Fit Rolls to Main" to position the Roll guides
# around a user-placed main guide along its own local frame:
#   up   = main local Z   (Roll_Back/Front offset axis)
#   side = main local X   (Roll_In axis)
GUIDE_RATIOS = {
    "grp_len": 0.023,       # GRP nub length along the segment
    "roll_offset": 0.117,   # Back/Front head distance from joint along up
    "back_len": 0.116,      # Roll_Back length (tail points along -up)
    "front_len": 0.096,     # Roll_Front length (tail points along -up)
    "in_head": 0.331,       # Roll_In head distance from joint along +side
    "out_head": 0.331,      # Roll_Out head distance from joint along -side
    "handle_len": 0.098,    # In/Out visual handle length (points -up)
}

# --- widget meshes (captured wire geometry) ----------------------------
# Flat circle (FK controls). Lies in the X/Z plane, radius ~0.005.
WGT_CIRCLE_NAME = "WGT-Circle"
WGT_CIRCLE_VERTS = [
    (0.005, 0.0, 0.0), (0.0049, 0.0, -0.001), (0.0046, 0.0, -0.0019),
    (0.0042, 0.0, -0.0028), (0.0035, 0.0, -0.0035), (0.0028, 0.0, -0.0042),
    (0.0019, 0.0, -0.0046), (0.001, 0.0, -0.0049), (-0.0, 0.0, -0.005),
    (-0.001, 0.0, -0.0049), (-0.0019, 0.0, -0.0046), (-0.0028, 0.0, -0.0042),
    (-0.0035, 0.0, -0.0035), (-0.0042, 0.0, -0.0028), (-0.0046, 0.0, -0.0019),
    (-0.0049, 0.0, -0.001), (-0.005, 0.0, 0.0), (-0.0049, 0.0, 0.001),
    (-0.0046, 0.0, 0.0019), (-0.0042, 0.0, 0.0028), (-0.0035, 0.0, 0.0035),
    (-0.0028, 0.0, 0.0042), (-0.0019, 0.0, 0.0046), (-0.001, 0.0, 0.0049),
    (0.0, 0.0, 0.005), (0.001, 0.0, 0.0049), (0.0019, 0.0, 0.0046),
    (0.0028, 0.0, 0.0042), (0.0035, 0.0, 0.0035), (0.0042, 0.0, 0.0028),
    (0.0046, 0.0, 0.0019), (0.0049, 0.0, 0.001),
]
WGT_CIRCLE_EDGES = [(i, (i + 1) % 32) for i in range(32)]

# Double-headed rotation arc (roll controls). Captured from the user's
# retuned widget (XY plane, arrowheads at the open end).
WGT_ARROW_NAME = "WGT-Roll_Arc"
WGT_ARROW_VERTS = [
    (0.0, 1.0, 0.0), (-0.098, 0.9952, 0.0), (-0.1951, 0.9808, 0.0),
    (-0.2903, 0.9569, 0.0), (-0.3827, 0.9239, 0.0), (-0.4714, 0.8819, 0.0),
    (-0.5556, 0.8315, 0.0), (-0.6344, 0.773, 0.0), (-0.7071, 0.7071, 0.0),
    (-0.773, 0.6344, 0.0), (-0.8315, 0.5556, 0.0), (-0.8819, 0.4714, 0.0),
    (-0.9239, 0.3827, 0.0), (-0.9569, 0.2903, 0.0), (-0.9808, 0.1951, 0.0),
    (-0.9952, 0.098, 0.0), (-1.0, 0.0, 0.0), (-1.1, 0.1, 0.0),
    (-0.9, 0.1, 0.0), (1.1, 0.1, 0.0), (0.9, 0.1, 0.0),
    (1.0, 0.0, 0.0), (0.9952, 0.098, 0.0), (0.9808, 0.1951, 0.0),
    (0.9569, 0.2903, 0.0), (0.9239, 0.3827, 0.0), (0.8819, 0.4714, 0.0),
    (0.8315, 0.5556, 0.0), (0.773, 0.6344, 0.0), (0.7071, 0.7071, 0.0),
    (0.6344, 0.773, 0.0), (0.5556, 0.8315, 0.0), (0.4714, 0.8819, 0.0),
    (0.3827, 0.9239, 0.0), (0.2903, 0.9569, 0.0), (0.1951, 0.9808, 0.0),
    (0.098, 0.9952, 0.0),
]
WGT_ARROW_EDGES = [
    (16, 17), (16, 18), (20, 21), (0, 1), (1, 2), (2, 3), (3, 4), (4, 5),
    (5, 6), (6, 7), (7, 8), (8, 9), (9, 10), (10, 11), (11, 12), (12, 13),
    (13, 14), (14, 15), (15, 16), (19, 21), (21, 22), (22, 23), (23, 24),
    (24, 25), (25, 26), (26, 27), (27, 28), (28, 29), (29, 30), (30, 31),
    (31, 32), (32, 33), (33, 34), (34, 35), (35, 36), (0, 36),
]

# Square frame with tick marks (Root control). Captured from
# WGT-Armature_Root-ForeArm.L.
WGT_ROOT_NAME = "WGT-Root"
WGT_ROOT_VERTS = [
    (1.0, 0.0, 1.0), (-1.0, 0.0, 1.0), (1.0, 0.0, -1.0), (-1.0, 0.0, -1.0),
    (0.6667, 0.0, -1.0), (-0.6667, 0.0, -1.0), (0.6667, 0.0, 1.0),
    (-0.6667, 0.0, 1.0), (1.0, 0.0, 0.6667), (1.0, 0.0, -0.6667),
    (-1.0, 0.0, 0.6667), (-1.0, 0.0, -0.6667),
]
WGT_ROOT_EDGES = [
    (3, 5), (1, 7), (2, 9), (3, 11), (2, 4), (0, 6), (0, 8), (1, 10),
]

# Curved twist arrow (the Twist control). Lies in the X/Z plane, two
# concentric arcs joined by a small arrowhead.
WGT_TWIST_NAME = "WGT-Twist_Arrow"
WGT_TWIST_VERTS = [
    (-0.87, 0.0, -0.1534), (-0.8832, 0.0, 0.0193), (-0.8625, -0.0, 0.1912),
    (-0.8086, -0.0, 0.3558), (-0.7237, -0.0, 0.5067), (-0.6109, -0.0, 0.6382),
    (-0.4747, -0.0, 0.7451), (-0.3202, -0.0, 0.8234), (-0.1534, -0.0, 0.87),
    (0.0193, -0.0, 0.8832), (0.1912, -0.0, 0.8625), (0.3558, -0.0, 0.8086),
    (0.5067, -0.0, 0.7237), (0.6382, -0.0, 0.6109), (0.7451, -0.0, 0.4747),
    (0.8234, 0.0, 0.3202), (0.87, 0.0, 0.1534), (0.8832, 0.0, -0.0193),
    (0.8625, 0.0, -0.1912), (0.8086, 0.0, -0.3558), (0.7237, 0.0, -0.5067),
    (0.6109, 0.0, -0.6382), (0.4747, 0.0, -0.7451), (-0.5617, 0.0, -0.8021),
    (-0.5067, 0.0, -0.7237), (-0.6382, 0.0, -0.6109), (-0.7451, 0.0, -0.4747),
    (-0.8234, 0.0, -0.3202), (-0.6779, 0.0, -0.1195), (-0.6882, -0.0, 0.015),
    (-0.6721, -0.0, 0.149), (-0.6301, -0.0, 0.2773), (-0.5639, -0.0, 0.3949),
    (-0.476, -0.0, 0.4973), (-0.3699, -0.0, 0.5806), (-0.2495, -0.0, 0.6416),
    (-0.1195, -0.0, 0.6779), (0.015, -0.0, 0.6882), (0.149, -0.0, 0.6721),
    (0.2773, -0.0, 0.6301), (0.3949, -0.0, 0.5639), (0.4973, -0.0, 0.476),
    (0.5806, -0.0, 0.3699), (0.6416, -0.0, 0.2495), (0.6779, -0.0, 0.1195),
    (0.6882, 0.0, -0.015), (0.6721, 0.0, -0.149), (0.6301, 0.0, -0.2773),
    (0.5639, 0.0, -0.3949), (0.476, 0.0, -0.4973), (0.3699, 0.0, -0.5806),
    (-0.3399, 0.0, -0.4854), (-0.3949, 0.0, -0.5639), (-0.4973, 0.0, -0.476),
    (-0.5806, 0.0, -0.3699), (-0.6416, 0.0, -0.2495), (0.4223, 0.0, -0.6628),
    (-0.3165, 0.0, -0.7194),
]
WGT_TWIST_EDGES = [
    (0, 1), (1, 2), (2, 3), (3, 4), (4, 5), (5, 6), (6, 7), (7, 8), (8, 9),
    (9, 10), (10, 11), (11, 12), (12, 13), (13, 14), (14, 15), (15, 16),
    (16, 17), (17, 18), (18, 19), (19, 20), (20, 21), (21, 22), (23, 24),
    (24, 25), (25, 26), (26, 27), (28, 29), (29, 30), (30, 31), (31, 32),
    (32, 33), (33, 34), (34, 35), (35, 36), (36, 37), (37, 38), (38, 39),
    (39, 40), (0, 27), (41, 42), (42, 43), (43, 44), (44, 45), (45, 46),
    (46, 47), (47, 48), (48, 49), (49, 50), (51, 52), (52, 53), (53, 54),
    (54, 55), (22, 56), (23, 57), (51, 57), (40, 41), (50, 56), (28, 55),
]

# Custom-shape transform targets: a control's widget is displayed in the
# space of another bone (``custom_shape_transform``). Captured per control:
#   FK    -> its own DEF bone
#   Twist -> its GRP_System bone
#   Roll  -> the long System_Roll_In bone
CSTF_TARGETS = {
    "def": "DEF-{seg}{side}",
    "grp_def": "GRP_DEF-{seg}{side}",
    "grp_system": "GRP_System-{seg}{side}",
    "root": "Root-{seg}{side}",
    "roll_in": "System-{seg}_Roll_In{side}",
}

# Widget application presets. Scale/translation/cstf captured from the
# retuned .L rig (ForeArm values are the canonical template; scale * size).
# ``bone_size`` -> use_custom_shape_bone_size: the FK circle uses an absolute
# size (False); the roll/twist arrows scale with the bone length (True).
# ``cstf`` -> CSTF_TARGETS key for the custom_shape_transform bone.
WGT_CIRCLE = {
    "mesh": WGT_CIRCLE_NAME,
    "scale": (39.36, 39.36, 39.36),
    "translation": (0.0, 0.0, 0.0),
    "rotation": (0.0, 0.0, 0.0),
    "bone_size": False,
    "cstf": "def",
}
WGT_TWIST = {
    "mesh": WGT_TWIST_NAME,
    "scale": (1.555, 1.555, 1.555),
    "translation": (0.0, -0.038, 0.0),
    "rotation": (0.0, 0.0, 0.0),
    "bone_size": True,
    "cstf": "grp_system",
}
WGT_ARROW_UP = {
    "mesh": WGT_ARROW_NAME,
    "scale": (1.146, 1.146, 1.146),
    "translation": (0.183, 0.337, 0.0),
    "rotation": (0.0, 0.0, -math.pi / 2.0),
    "bone_size": True,
    "cstf": "roll_in",
}
WGT_ARROW_DOWN = {
    "mesh": WGT_ARROW_NAME,
    "scale": (1.0, 1.0, 1.0),
    "translation": (-0.183, 0.337, 0.0),
    "rotation": (0.0, 0.0, math.pi / 2.0),
    "bone_size": True,
    "cstf": "roll_in",
}
WGT_ROOT = {
    "mesh": WGT_ROOT_NAME,
    "scale": (6.782, 6.782, 6.782),
    "translation": (0.0, 0.0, 0.0),
    "rotation": (0.0, 0.0, 0.0),
    "bone_size": True,
    "cstf": None,
}

# Bone display colors (CUSTOM palette: normal, select, active), captured from
# the reference. Only the FK and Roll controls are colored.
COLOR_FK = ((0.157, 0.133, 0.957), (0.157, 0.133, 0.957), (0.392, 0.624, 1.0))
COLOR_ROLL = ((0.376, 0.592, 0.82), (0.231, 0.682, 0.847), (0.137, 0.816, 0.937))


# Constraint specs. ``target`` resolves to a Ctrl/System bone of the same
# segment: 'fk' -> Ctrl-{seg}_FK, 'roll' -> Ctrl-{seg}_Roll,
# 'roll_back' -> Ctrl-{seg}_Roll_Back, 'system' -> System-{seg}.
COPY_LOC_LOCAL = ("COPY_LOCATION", {"target": "fk", "space": "LOCAL"})
# Copy Rotation only on the Y (twist) axis, matching the reference.
COPY_ROT_LOCAL = ("COPY_ROTATION", {"target": "fk", "space": "LOCAL",
                                    "use": (False, True, False)})
# Rot_y bones copy the Twist control's Y rotation (REPLACE, LOCAL/LOCAL).
COPY_ROT_TWIST = ("COPY_ROTATION", {"target": "twist", "space": "LOCAL",
                                    "use": (False, True, False)})
COPY_SCALE_WORLD = ("COPY_SCALE", {"target": "fk", "space": "WORLD"})
COPY_XFORM_SYSTEM = ("COPY_TRANSFORMS", {"target": "system", "space": "WORLD"})


def _xform(target, from_axis, to_axis, from_deg, to_deg):
    """ROTATION->ROTATION Transformation pass on a single (possibly crossed) axis.

    Drives the ``to_axis`` output rotation from the ``from_axis`` input
    rotation of ``target``. ``from_deg``/``to_deg`` are the captured limits in
    degrees (they may have opposite signs for an inverted mapping, and
    ``from_axis`` may differ from ``to_axis`` for a cross-axis route). The
    active slot (min for negative, max for positive) follows the sign of
    ``from_deg``.
    """
    return ("TRANSFORM", {"target": target,
                          "from_axis": from_axis, "to_axis": to_axis,
                          "from": math.radians(from_deg),
                          "to": math.radians(to_deg)})


# Per-segment generated bone definitions. Order matters: parents are
# created before children. ``parent`` of None means world (unparented);
# ``"@def"`` / ``"@ctrl"`` mean the parent segment's DEF / Ctrl_FK bone
# (used only when the segment is a non-root child in a chain).
# ``Root-{seg}`` is the component top; GRP_Ctrl / GRP_System / GRP_DEF hang
# off it.
#
# Each entry: name, anchor, direction, length, parent, collection_role,
# widget, constraints.
# Driver for System_Rot_y_Local: the bone's local Y (twist) is driven from a
# custom property living on the FK control, scaled by ``factor``. The driver
# variable name is derived from ``prop`` with non-identifier chars -> '_'.
ROT_Y_LOCAL_DRIVER = {
    "data_path": "rotation_euler",
    "index": 1,            # Y (twist) euler component
    "factor": 3,
    "prop_bone": "fk",     # CTRL_TARGETS key of the bone holding the property
    "prop": "Component_Rot_y_Local{side}",
}
# Custom property stamped on the FK control (drives Rot_y_Local).
FK_CUSTOM_PROPS = ({"name": "Component_Rot_y_Local{side}", "default": 0.0},)


ROOT = "Root-{seg}{side}"

# Unsuffixed Roots the user added by hand. Always deleted on Build / Clear.
LEGACY_GENERATED = (
    "Root-{seg}",
)


def generated_names(seg, side):
    """All generated bone names for a segment (current template + leftovers)."""
    names = set()
    for flag in (True, False):
        for spec in generated_bones(is_root=flag):
            names.add(spec["name"].format(seg=seg, side=side))
    for tmpl in LEGACY_GENERATED:
        names.add(tmpl.format(seg=seg, side=side))
    return names


def generated_bones(is_root):
    space_name = "Transfer_System-{seg}{side}"
    root_name = ROOT
    # Child Root hangs off the parent component's DEF when the guides chain.
    root_parent = None if is_root else "@def"
    # Root Twist hangs off GRP_Ctrl; a child Twist chains onto the parent FK
    # so the FK controls form one limb chain. FK always parents to this Twist.
    twist_parent = "GRP_Ctrl-{seg}{side}" if is_root else "@ctrl"

    # Front/Back FK twist passes (uniform for every segment; geometry is the
    # same regardless of root/child position in the chain).
    front_fk = _xform("fk", "X", "X", -170, 170)
    back_fk = _xform("fk", "X", "X", 170, -170)

    return [
        # ---- Root (component top) ----
        {
            "name": root_name, "anchor": ANCHOR_JOINT, "dir": DIR_SEG,
            "length": 0.034, "parent": root_parent, "coll": "root",
            "widget": WGT_ROOT, "constraints": [], "deform": True,
        },
        # ---- Ctrl ----
        {
            "name": "GRP_Ctrl-{seg}{side}", "anchor": ANCHOR_JOINT, "dir": DIR_SEG,
            "length": 0.053, "parent": root_name, "coll": "ctrl",
            "widget": None, "constraints": [],
        },
        {
            "name": "Ctrl-{seg}_Twist{side}", "anchor": ANCHOR_JOINT, "dir": DIR_SEG,
            "length": 0.131, "parent": twist_parent, "coll": "ctrl",
            "widget": WGT_TWIST, "color": COLOR_FK, "constraints": [],
        },
        {
            "name": "Ctrl-{seg}_FK{side}", "anchor": ANCHOR_JOINT, "dir": DIR_SEG,
            "length": 0.105, "parent": "Ctrl-{seg}_Twist{side}", "coll": "ctrl",
            "widget": WGT_CIRCLE, "color": COLOR_FK, "constraints": [],
            "props": FK_CUSTOM_PROPS,
        },
        {
            "name": "Ctrl-{seg}_Roll_Back{side}", "anchor": ANCHOR_JOINT, "dir": DIR_UP,
            "length": 0.086, "parent": "Ctrl-{seg}_FK{side}", "coll": "ctrl",
            "widget": WGT_ARROW_UP, "color": COLOR_ROLL, "constraints": [],
        },
        {
            "name": "Ctrl-{seg}_Roll_Front{side}", "anchor": ANCHOR_JOINT, "dir": DIR_NUP,
            "length": 0.100, "parent": "Ctrl-{seg}_FK{side}", "coll": "ctrl",
            "widget": WGT_ARROW_DOWN, "color": COLOR_ROLL, "constraints": [],
        },
        # ---- System ----
        {
            "name": "GRP_System-{seg}{side}", "anchor": ANCHOR_JOINT, "dir": DIR_SEG,
            "length": 0.018, "parent": root_name, "coll": "system",
            "widget": None, "constraints": [],
        },
        {
            "name": "System-{seg}_Rot_y{side}", "anchor": ANCHOR_JOINT, "dir": DIR_SEG,
            "length": 0.080, "parent": "GRP_System-{seg}{side}", "coll": "system",
            "widget": None, "constraints": [COPY_ROT_TWIST],
        },
        {
            "name": "System-{seg}_Rot_y{side}.001", "anchor": ANCHOR_JOINT, "dir": DIR_SEG,
            "length": 0.058, "parent": "GRP_System-{seg}{side}", "coll": "system",
            "widget": None, "constraints": [COPY_ROT_TWIST],
        },
        {
            "name": space_name, "anchor": ANCHOR_JOINT, "dir": DIR_SEG,
            "length": 0.029, "parent": "System-{seg}_Rot_y{side}", "coll": "system",
            "widget": None, "constraints": [COPY_LOC_LOCAL],
        },
        {
            "name": "System-{seg}_Roll_Front{side}", "anchor": ANCHOR_FRONT, "dir": DIR_NUP,
            "length": 0.176, "parent": space_name, "coll": "system",
            "widget": None, "constraints": [front_fk],
        },
        {
            "name": "System-{seg}_Roll_Back{side}", "anchor": ANCHOR_BACK, "dir": DIR_NUP,
            "length": 0.176, "parent": "System-{seg}_Roll_Front{side}", "coll": "system",
            "widget": None, "constraints": [back_fk],
        },
        {
            "name": "System-{seg}_Roll_Out{side}", "anchor": ANCHOR_OUT, "dir": DIR_NUP,
            "length": 0.158, "parent": "System-{seg}_Roll_Back{side}", "coll": "system",
            "widget": None,
            "constraints": [_xform("roll_back", "Z", "Z", 100, 100),
                            _xform("roll_front", "Z", "Z", -100, -100),
                            _xform("fk", "Z", "Y", 170, -170)],
        },
        {
            "name": "System-{seg}_Roll_In{side}", "anchor": ANCHOR_IN_HEAD, "dir": DIR_SIDE,
            "length": LEN_TO_OUT, "parent": "System-{seg}_Roll_Out{side}", "coll": "system",
            "widget": None,
            "constraints": [_xform("roll_back", "Z", "Z", -100, -100),
                            _xform("roll_front", "Z", "Z", 100, 100),
                            _xform("fk", "Z", "X", -170, -170)],
        },
        {
            "name": "System-{seg}_Rot_y_Local{side}", "anchor": ANCHOR_JOINT, "dir": DIR_SEG,
            "length": 0.098, "parent": "System-{seg}_Roll_In{side}", "coll": "system",
            "widget": None, "constraints": [],
            "rotation_mode": "XYZ", "driver": ROT_Y_LOCAL_DRIVER,
        },
        {
            "name": "System-{seg}{side}", "anchor": ANCHOR_JOINT, "dir": DIR_SEG,
            "length": LEN_MAIN, "parent": "System-{seg}_Rot_y_Local{side}", "coll": "system",
            "widget": None, "constraints": [COPY_SCALE_WORLD],
        },
        # ---- Deform ----
        {
            "name": "GRP_DEF-{seg}{side}", "anchor": ANCHOR_JOINT, "dir": DIR_SEG,
            "length": 0.175, "parent": root_name, "coll": "def",
            "widget": None, "constraints": [COPY_XFORM_SYSTEM], "deform": True,
        },
        {
            "name": "DEF-{seg}{side}", "anchor": ANCHOR_JOINT, "dir": DIR_SEG,
            "length": LEN_MAIN, "parent": "GRP_DEF-{seg}{side}", "coll": "def",
            "widget": None, "constraints": [COPY_XFORM_SYSTEM], "deform": True,
        },
    ]


# Ctrl target name templates (for constraint subtarget resolution).
CTRL_TARGETS = {
    "fk": "Ctrl-{seg}_FK{side}",
    "twist": "Ctrl-{seg}_Twist{side}",
    "roll_back": "Ctrl-{seg}_Roll_Back{side}",   # up-arrow control (-up)
    "roll_front": "Ctrl-{seg}_Roll_Front{side}",  # down-arrow control (+up)
    "system": "System-{seg}{side}",
}
