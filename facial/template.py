"""Base face bones, taken from the rain facial rig (1.facial_rig_pose.blend).

Positions are the rain rig's world coordinates. ``REFERENCE`` is rain's
FACE-ROOT; every bone is moved by the transform that maps REFERENCE onto the
character's Head bone (rotation + uniform scale by length), so the layout
follows the Head bone's position, direction and size.
"""

# head, tail of rain's FACE-ROOT (roll 0)
REFERENCE = ((0.0, -0.0277, 1.3921), (0.0, -0.0527, 1.6152))

# rain head mesh landmarks (world): chin bottom / crown z, face front / back
# of head y, cranium half width x. Default marker spots are mapped from
# these onto the character's head mesh axis by axis, so a cartoon face with a
# short chin or a big cranium still gets markers in the matching place.
RAIN_LANDMARKS = dict(chin=1.3436, top=1.6239, front=-0.1723, back=0.0899,
                      half_width=0.0978)

# Bone collections
COLL_CTRL = "Facial_Base"
COLL_MCH = "Facial_MCH"
COLL_DEF = "Facial_DEF"

# name: dict(head, tail, parent, deform, coll, bbone, constraints)
# parent "@HEAD" means the Head bone the user picked.
# Constraint tuples:
#   ("ARMATURE", target)                   one Armature target, weight 1
#   ("STRETCH_TO", target)                 volume XZX, keep axis SWING_Y
BASE_BONES = {
    "FACE-ROOT": dict(head=(0.0, -0.0277, 1.3921), tail=(0.0, -0.0527, 1.6152),
                      parent="@HEAD", deform=True, coll=COLL_DEF, bbone=10),
    "MSTR-FACE_UPP": dict(head=(0.0, -0.0393, 1.4999), tail=(0.0, -0.0393, 1.5213),
                          parent=None, coll=COLL_CTRL,
                          constraints=[("ARMATURE", "FACE-ROOT")]),
    "STR-FACE_UPP": dict(head=(0.0, -0.0393, 1.3901), tail=(0.0, -0.0393, 1.5741),
                         parent="MSTR-FACE_UPP", deform=True, coll=COLL_DEF,
                         constraints=[("STRETCH_TO", "TGT-FACE_UPP")]),
    "TGT-FACE_UPP": dict(head=(0.0, -0.0393, 1.5741), tail=(0.0, -0.0393, 1.5834),
                         parent="MSTR-FACE_UPP", coll=COLL_CTRL),
    "MSTR-EYE.L": dict(head=(0.0467, -0.0903, 1.4728), tail=(0.0467, -0.1153, 1.4728),
                       parent="STR-FACE_UPP", coll=COLL_CTRL),
    "MSTR-EYE.R": dict(head=(-0.0467, -0.0903, 1.4728), tail=(-0.0467, -0.1153, 1.4728),
                       parent="STR-FACE_UPP", coll=COLL_CTRL),
    # Ear / nose masters run root -> tip along the part (marker lines):
    # ear base -> tip, nose bridge -> tip. Brow, teeth and tongue are built
    # from chain markers by parts.py.
    "MSTR-EAR.L": dict(head=(0.1016, -0.0400, 1.4552), tail=(0.1150, -0.0165, 1.4630),
                       parent="FACE-ROOT", coll=COLL_CTRL),
    "MSTR-EAR.R": dict(head=(-0.1016, -0.0400, 1.4552), tail=(-0.1150, -0.0165, 1.4630),
                       parent="FACE-ROOT", coll=COLL_CTRL),
    "MSTR-FACE_LOW": dict(head=(0.0, -0.0393, 1.4270), tail=(0.0, -0.0393, 1.4484),
                          parent=None, coll=COLL_CTRL,
                          constraints=[("ARMATURE", "FACE-ROOT")]),
    "P-FACE_LOW": dict(head=(0.0, -0.0393, 1.4273), tail=(0.0, -0.0393, 1.4355),
                       parent="MSTR-FACE_LOW", coll=COLL_MCH),
    "STR-FACE_LOW": dict(head=(0.0, -0.0922, 1.4716), tail=(0.0, -0.0998, 1.3486),
                         parent="P-FACE_LOW", deform=True, coll=COLL_DEF, bbone=10,
                         constraints=[("STRETCH_TO", "TGT-FACE_LOW")]),
    "TGT-FACE_LOW": dict(head=(0.0, -0.0998, 1.3486), tail=(0.0, -0.0998, 1.3393),
                         parent="MSTR-FACE_LOW", coll=COLL_CTRL),
    "MSTR-NOSE": dict(head=(0.0, -0.1413, 1.4836), tail=(0.0, -0.1509, 1.4208),
                      parent="STR-FACE_LOW", coll=COLL_CTRL),
    "MCH_SKULL": dict(head=(0.0, -0.0262, 1.4164), tail=(0.0, -0.0298, 1.4164),
                      parent="STR-FACE_LOW", coll=COLL_MCH),
    "DEF-JAW": dict(head=(0.0, -0.0262, 1.4164), tail=(0.0, -0.0726, 1.4164),
                    parent="MCH_SKULL", deform=True, coll=COLL_CTRL),
    "MCH-JAW": dict(head=(0.0, -0.0262, 1.4164), tail=(0.0, -0.0568, 1.4164),
                    parent="DEF-JAW", coll=COLL_MCH),
    # nose DEFs (rain DEF-NOSEBRIDGE / DEF_SM-NOSE_TIP / DEF_SM-NOSTRILL)
    "DEF-Nose_bridge": dict(head=(0.0, -0.1463, 1.4511), tail=(0.0, -0.1459, 1.4533),
                            parent="MSTR-NOSE", deform=True, coll=COLL_DEF),
    "DEF-Nose_tip": dict(head=(0.0, -0.1723, 1.4283), tail=(0.0, -0.1723, 1.4340),
                         parent="MSTR-NOSE", deform=True, coll=COLL_DEF),
    "DEF-Nostril.L": dict(head=(0.0266, -0.1471, 1.4251), tail=(0.0266, -0.1471, 1.4308),
                          parent="MSTR-NOSE", deform=True, coll=COLL_DEF),
    "DEF-Nostril.R": dict(head=(-0.0266, -0.1471, 1.4251), tail=(-0.0266, -0.1471, 1.4308),
                          parent="MSTR-NOSE", deform=True, coll=COLL_DEF),
    # rain names (its shape key drivers read CTL-Cheekpuff.*)
    "CTL-Cheekpuff.L": dict(head=(0.0704, -0.1091, 1.3935), tail=(0.0785, -0.1218, 1.3935),
                            parent="MSTR-MOUTH", coll=COLL_CTRL),
    "CTL-Cheekpuff.R": dict(head=(-0.0704, -0.1091, 1.3935), tail=(-0.0785, -0.1218, 1.3935),
                            parent="MSTR-MOUTH", coll=COLL_CTRL),
    "DEF_SM-CHIN": dict(head=(0.0, -0.1344, 1.3476), tail=(0.0, -0.1344, 1.3521),
                        parent="DEF-JAW", deform=True, coll=COLL_DEF),
    "MSTR-MOUTH": dict(head=(0.0, -0.0975, 1.3962), tail=(0.0, -0.0993, 1.4042),
                       parent="FACE-ROOT", coll=COLL_CTRL),
}

# Marker empties that place base bones before Build Base.
# name -> rain position (world); the default spot under the Head bone.
MARKER_COLL = "Facial_Markers"
# Line markers: a Root (bone head) and a Tip (bone tail) empty per bone.
# (bone, root marker, tip marker, bones moved with it). Applied in order:
# fitting a line moves/rotates/scales its whole group, so the two face
# squash lines go first and carry the parts; each part's own line then
# places that part exactly.
NOSE_DEFS = ("DEF-Nose_bridge", "DEF-Nose_tip", "DEF-Nostril.L", "DEF-Nostril.R")
MARKER_GROUPS = (
    ("STR-FACE_UPP", "MK-FaceUpp_Root", "MK-FaceUpp_Tip",
     ("STR-FACE_UPP", "TGT-FACE_UPP", "MSTR-FACE_UPP", "MSTR-EYE.L", "MSTR-EYE.R")),
    ("STR-FACE_LOW", "MK-FaceLow_Root", "MK-FaceLow_Tip",
     ("STR-FACE_LOW", "TGT-FACE_LOW", "MSTR-FACE_LOW", "P-FACE_LOW", "MSTR-NOSE",
      "MCH_SKULL", "DEF-JAW", "MCH-JAW", "DEF_SM-CHIN") + NOSE_DEFS),
    # eyes: eyeball center -> pupil center (MSTR-EYE aims at the pupil).
    # The center is also the eyelid pivot; its default is the eye mesh center.
    ("MSTR-EYE.L", "MK-Eye.L", "MK-Eye_Pupil.L", ("MSTR-EYE.L",)),
    ("MSTR-EYE.R", "MK-Eye.R", "MK-Eye_Pupil.R", ("MSTR-EYE.R",)),
    ("MSTR-EAR.L", "MK-Ear_Root.L", "MK-Ear_Tip.L", ("MSTR-EAR.L",)),
    ("MSTR-EAR.R", "MK-Ear_Root.R", "MK-Ear_Tip.R", ("MSTR-EAR.R",)),
    # nose master: bridge top -> nose base (rain MSTR-NOSE -> DEF-NOSE)
    ("MSTR-NOSE", "MK-Nose_Root", "MK-Nose_Base", ("MSTR-NOSE",) + NOSE_DEFS),
)
# Point marker -> bones moved (translated) so the first one's head sits on it.
POINT_MARKERS = {
    "MK-Skull": ("MCH_SKULL", "DEF-JAW", "MCH-JAW"),
    "MK-CheekPuff.L": ("CTL-Cheekpuff.L",),
    "MK-CheekPuff.R": ("CTL-Cheekpuff.R",),
    "MK-Chin": ("DEF_SM-CHIN",),
    "MK-Nose_Bridge": ("DEF-Nose_bridge",),
    "MK-Nose_Tip": ("DEF-Nose_tip",),
    "MK-Nostril.L": ("DEF-Nostril.L",),
    "MK-Nostril.R": ("DEF-Nostril.R",),
}
# Eyelid guide markers: inner / upper / outer / lower point of the eye
# opening. Step 1 (Generate Guides) puts the Guide-Eyelid_* bones on them.
# Default: the eye-hole rim of the head mesh, else rain's eyelid corners.
# Order = the outline: In -> Upp -> Out -> Low.
EYELID_MARKERS = {
    "MK-Eyelid_In.L": (0.0283, -0.1203, 1.4630),
    "MK-Eyelid_Upp.L": (0.0484, -0.1271, 1.4866),
    "MK-Eyelid_Out.L": (0.0772, -0.1109, 1.4700),
    "MK-Eyelid_Low.L": (0.0492, -0.1236, 1.4560),
}
EYELID_GUIDE_ROLES = {"In": "corner_a", "Out": "corner_b", "Upp": "upp", "Low": "low"}

# Eyesocket (eyelid outer ring) markers -> bones. Add Markers places them
# from the eyelid markers with storm's ring (data/storm_template.json); the
# rain defaults below are only a rough ring around rain's eyelid markers.
EYESOCKET_MARKERS = {
    "MK-Eyesocket_In.L": ("DEF-Eyesocket_in",),
    "MK-Eyesocket_InUpp.L": ("DEF-Eyesocket_in_upp",),
    "MK-Eyesocket_Upp.L": ("DEF-Eyesocket_mid_upp", "CTL-Eyesocket_upp"),
    "MK-Eyesocket_OutUpp.L": ("DEF-Eyesocket_out_upp",),
    "MK-Eyesocket_Out.L": ("DEF-Eyesocket_out",),
    "MK-Eyesocket_OutLow.L": ("DEF-Eyesocket_out_low",),
    "MK-Eyesocket_Low.L": ("DEF-Eyesocket_mid_low", "CTL-Eyesocket_low"),
    "MK-Eyesocket_InLow.L": ("DEF-Eyesocket_in_low",),
}


def _socket_defaults():
    lid = {k.split("_")[1][:-2]: EYELID_MARKERS[k] for k in EYELID_MARKERS}
    c = [sum(p[i] for p in lid.values()) / 4 for i in range(3)]

    def ring(*names):
        p = [sum(lid[n][i] for n in names) / len(names) for i in range(3)]
        return tuple(c[i] + (p[i] - c[i]) * (1.3 if i != 1 else 1.0) for i in range(3))
    return {"MK-Eyesocket_In.L": ring("In"), "MK-Eyesocket_InUpp.L": ring("In", "Upp"),
            "MK-Eyesocket_Upp.L": ring("Upp"), "MK-Eyesocket_OutUpp.L": ring("Out", "Upp"),
            "MK-Eyesocket_Out.L": ring("Out"), "MK-Eyesocket_OutLow.L": ring("Out", "Low"),
            "MK-Eyesocket_Low.L": ring("Low"), "MK-Eyesocket_InLow.L": ring("In", "Low")}

# Closed outlines drawn between markers: (color key, markers, style).
# style "eye": In, Upp, Out, Low; the upper and lower lid are each a smooth
# curve through their markers and meet at the corners.
# None draws straight lines.
OUTLINES = (
    ("NOSE", ("MK-Nostril.L", "MK-Nose_Tip", "MK-Nostril.R"), None),
    ("EYELID", tuple(EYELID_MARKERS), "eye"),
    ("EYELID", tuple(n[:-2] + ".R" for n in EYELID_MARKERS), "eye"),
    ("EYESOCKET", tuple(EYESOCKET_MARKERS), None),
    ("EYESOCKET", tuple(n[:-2] + ".R" for n in EYESOCKET_MARKERS), None),
)

# Chain markers: several ordered points that shape a part (rain positions,
# left side for sided parts; .R is made by Mirror Markers, or mirrored on the
# fly at build time when missing).
#   brow   inner -> outer, rain key points DEF_SM-BROW1 / 4 / 7 / 10
#   tongue root (back) -> tip, rain DEF-TONGUE4 ... TIP joints
#   teeth  front (center) -> side -> back, one dental arch per jaw
CHAINS = {
    "BROW": dict(
        sided=True,
        markers=(("MK-Brow_In.L", (0.0222, -0.1527, 1.4992)),
                 ("MK-Brow_Mid1.L", (0.0509, -0.1467, 1.5063)),
                 ("MK-Brow_Mid2.L", (0.0761, -0.1343, 1.5059)),
                 ("MK-Brow_Out.L", (0.0890, -0.1134, 1.4959))),
    ),
    "TONGUE": dict(
        sided=False,
        markers=(("MK-Tongue_Root", (0.0, -0.0422, 1.3668)),
                 ("MK-Tongue_J1", (0.0, -0.0751, 1.3857)),
                 ("MK-Tongue_J2", (0.0, -0.0955, 1.3854)),
                 ("MK-Tongue_J3", (0.0, -0.1144, 1.3790)),
                 ("MK-Tongue_Tip", (0.0, -0.1224, 1.3756))),
    ),
    "TEETH_UPP": dict(
        sided=True,   # Front is the center, Side / Back are mirrored
        markers=(("MK-TeethUpp_Front", (0.0, -0.1512, 1.3974)),
                 ("MK-TeethUpp_Side.L", (0.0226, -0.1375, 1.3979)),
                 ("MK-TeethUpp_Back.L", (0.0322, -0.1139, 1.4040))),
    ),
    "TEETH_LOW": dict(
        sided=True,
        markers=(("MK-TeethLow_Front", (0.0, -0.1441, 1.3661)),
                 ("MK-TeethLow_Side.L", (0.0242, -0.1254, 1.3683)),
                 ("MK-TeethLow_Back.L", (0.0305, -0.1065, 1.3718))),
    ),
    # cheek: rain CTL-cheek_in / mid / out
    "CHEEK": dict(
        sided=True,
        markers=(("MK-Cheek_In.L", (0.0301, -0.1341, 1.4458)),
                 ("MK-Cheek_Mid.L", (0.0584, -0.1291, 1.4303)),
                 ("MK-Cheek_Out.L", (0.0883, -0.1092, 1.4418))),
    ),
    # jawline: one line through the center, center -> ear on each side
    # (center + rain DEF-jawline1 / 2 / 3 = 4 points per side). The center
    # spot continues the jawline1 -> 2 direction to x = 0 (under the chin).
    "JAWLINE": dict(
        sided=True,   # Center is shared, 1 / 2 / 3 are mirrored
        markers=(("MK-Jawline_Center", (0.0, -0.1276, 1.3362)),
                 ("MK-Jawline_1.L", (0.0311, -0.1000, 1.3525)),
                 ("MK-Jawline_2.L", (0.0615, -0.0730, 1.3684)),
                 ("MK-Jawline_3.L", (0.0764, -0.0430, 1.3963))),
    ),
}
CHAIN_PARENTS = {"BROW": "STR-FACE_UPP", "TONGUE": "DEF-JAW",
                 "TEETH_UPP": "STR-FACE_LOW", "TEETH_LOW": "DEF-JAW",
                 "CHEEK": "MSTR-FACE_LOW", "JAWLINE": "DEF-JAW"}
# Markers that sit on the skin: snapped onto the head mesh when created.
SURFACE_CHAINS = ("CHEEK", "JAWLINE")
SURFACE_POINTS = ("MK-CheekPuff.L", "MK-CheekPuff.R", "MK-Chin",
                  "MK-Nose_Bridge", "MK-Nose_Tip", "MK-Nostril.L", "MK-Nostril.R")
# rain sizes (scaled by the Head length / REFERENCE length)
TOOTH_HEIGHT = 0.017
BROW_DEF_LEN = 0.0046
BROW_CTRL_LEN = 0.0069


def _bone(name):
    return BASE_BONES[name]


def flip(name):
    return name[:-2] + ".R" if name.endswith(".L") else name


# name -> default rain position (center / .L markers; .R come from mirroring).
MARKERS = {}
for _b, _root, _tip, _m in MARKER_GROUPS:
    MARKERS[_root] = _bone(_b)["head"]
    MARKERS[_tip] = _bone(_b)["tail"]
for _mk, _members in POINT_MARKERS.items():
    MARKERS[_mk] = _bone(_members[0])["head"]
for _c in CHAINS.values():
    for _n, _p in _c["markers"]:
        MARKERS[_n] = _p
MARKERS.update(EYELID_MARKERS)
MARKERS.update(_socket_defaults())
for _n in list(MARKERS):                       # .R defaults: mirrored rain spots
    if _n.endswith(".L"):
        _p = MARKERS[_n]
        MARKERS[flip(_n)] = (-_p[0], _p[1], _p[2])

ROOT_MARKERS = ({g[1] for g in MARKER_GROUPS} | {c["markers"][0][0] for c in CHAINS.values()}
                | {"MK-Eyelid_In.L"})
TIP_MARKERS = ({g[2] for g in MARKER_GROUPS} | {c["markers"][-1][0] for c in CHAINS.values()}
               | {"MK-Eyelid_Out.L"})
ROOT_MARKERS |= {flip(n) for n in ROOT_MARKERS}
TIP_MARKERS |= {flip(n) for n in TIP_MARKERS}

# Markers placed together (filling in a missing one keeps the group's offset).
MARKER_SETS = [(g[1], g[2]) for g in MARKER_GROUPS]
for _c in CHAINS.values():
    MARKER_SETS.append(tuple(n for n, _p in _c["markers"]))
    if _c["sided"]:
        MARKER_SETS.append(tuple(flip(n) for n, _p in _c["markers"]))
MARKER_SETS.append(tuple(EYELID_MARKERS))
MARKER_SETS.append(tuple(flip(n) for n in EYELID_MARKERS))
MARKER_SETS.append(tuple(EYESOCKET_MARKERS))
MARKER_SETS.append(tuple(flip(n) for n in EYESOCKET_MARKERS))

SURFACE_MARKERS = set(SURFACE_POINTS)
for _c in SURFACE_CHAINS:
    for _n, _p in CHAINS[_c]["markers"]:
        SURFACE_MARKERS |= {_n, flip(_n)}

# Older marker names -> current ones (renamed in place when found, so a
# placement made with an older version is kept).
LEGACY_MARKERS = {
    "MK-FaceUpp_Top": "MK-FaceUpp_Tip",
    "MK-FaceLow_Chin": "MK-FaceLow_Tip",
    "MK-Nose": "MK-Nose_Root",
    "MK-Nose_Tip": "MK-Nose_Base",   # the line's end is the nose base now
    "MK-Ear.L": "MK-Ear_Root.L",
    "MK-Ear.R": "MK-Ear_Root.R",
    "MK-Brow_Root.L": "MK-Brow_In.L",
    "MK-Brow_Root.R": "MK-Brow_In.R",
    "MK-Brow_Tip.L": "MK-Brow_Out.L",
    "MK-Brow_Tip.R": "MK-Brow_Out.R",
    "MK-TeethUpp_Tip": "MK-TeethUpp_Front",
    "MK-TeethLow_Tip": "MK-TeethLow_Front",
    "MK-Tongue": "MK-Tongue_Root",
}

# Parents used by the generated MCH bones (and their DSP bones).
LIP_UPPER_PARENT = "MCH_SKULL"
LIP_LOWER_PARENT = "MCH-JAW"
JAW_BONE = "DEF-JAW"
JAW_HALF = "MCH-Lips_JawHalf"   # follows the jaw 50%: parent of the lip corners
LEVEL_ROLL = ("MCH_SKULL", "DEF-JAW", "MCH-JAW")   # X axis = the character's side axis
EYE_MASTER = "MSTR-EYE{side}"   # .L / .R: parent and pivot of the eyelid MCH
MOUTH_MASTER = "MSTR-MOUTH"
