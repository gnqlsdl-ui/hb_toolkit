"""Base face bones, taken from the rain facial rig (1.facial_rig_pose.blend).

Positions are the rain rig's world coordinates. ``REFERENCE`` is rain's
FACE-ROOT; every bone is moved by the transform that maps REFERENCE onto the
character's Head bone (rotation + uniform scale by length), so the layout
follows the Head bone's position, direction and size.
"""

# head, tail of rain's FACE-ROOT (roll 0)
REFERENCE = ((0.0, -0.0277, 1.3921), (0.0, -0.0527, 1.6152))

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
    "MSTR-EYE.L": dict(head=(0.033, -0.062, 1.4728), tail=(0.033, -0.082, 1.4728),
                       parent="STR-FACE_UPP", coll=COLL_CTRL),
    "MSTR-EYE.R": dict(head=(-0.033, -0.062, 1.4728), tail=(-0.033, -0.082, 1.4728),
                       parent="STR-FACE_UPP", coll=COLL_CTRL),
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
    "MCH_SKULL": dict(head=(0.0, -0.0262, 1.4164), tail=(0.0, -0.0262, 1.4364),
                      parent="STR-FACE_LOW", coll=COLL_MCH),
    "DEF-JAW": dict(head=(0.0, -0.0262, 1.4164), tail=(0.0, -0.0726, 1.4164),
                    parent="MCH_SKULL", deform=True, coll=COLL_CTRL),
    "MCH-JAW": dict(head=(0.0, -0.0262, 1.4164), tail=(0.0, -0.0568, 1.4164),
                    parent="DEF-JAW", coll=COLL_MCH),
    "MSTR-MOUTH": dict(head=(0.0, -0.0975, 1.3962), tail=(0.0, -0.0993, 1.4042),
                       parent="FACE-ROOT", coll=COLL_CTRL),
}

# Marker empties that place the face squash lines before Build Base.
# name -> rain position (world). Each pair is the STR bone's head -> tail.
MARKER_COLL = "Facial_Markers"
MARKERS = {
    "MK-FaceUpp_Root": (0.0, -0.0393, 1.3901),
    "MK-FaceUpp_Top": (0.0, -0.0393, 1.5741),
    "MK-FaceLow_Root": (0.0, -0.0922, 1.4716),
    "MK-FaceLow_Chin": (0.0, -0.0998, 1.3486),
}
# (STR bone, root marker, tip marker, bones moved with it)
MARKER_GROUPS = (
    ("STR-FACE_UPP", "MK-FaceUpp_Root", "MK-FaceUpp_Top",
     ("STR-FACE_UPP", "TGT-FACE_UPP", "MSTR-FACE_UPP", "MSTR-EYE.L", "MSTR-EYE.R")),
    ("STR-FACE_LOW", "MK-FaceLow_Root", "MK-FaceLow_Chin",
     ("STR-FACE_LOW", "TGT-FACE_LOW", "MSTR-FACE_LOW", "P-FACE_LOW")),
)

# Parents used by the generated MCH bones (and their DSP bones).
LIP_UPPER_PARENT = "MCH_SKULL"
LIP_LOWER_PARENT = "MCH-JAW"
JAW_BONE = "DEF-JAW"
JAW_HALF = "MCH-Lips_JawHalf"   # follows the jaw 50%: parent of the lip corners
EYE_MASTER = "MSTR-EYE{side}"   # .L / .R: parent and pivot of the eyelid MCH
MOUTH_MASTER = "MSTR-MOUTH"
