"""Properties for the facial edge-bone / DSP generator (``scene.hb_facial``)."""

import bpy
from bpy.props import (BoolProperty, EnumProperty, FloatProperty, IntProperty,
                       PointerProperty, StringProperty)
from bpy.types import PropertyGroup


def _poll_mesh(_self, obj):
    return obj.type == 'MESH'


def _sk_overlay_update(self, _context):
    """The overlay draws the edit meshes' names: the plain name tags go off."""
    from . import operators as O
    from .shapekeys import LABEL_TAG, TITLE_TAG
    for o in bpy.data.objects:
        if o.get(O.OBJ_PROP) in (LABEL_TAG, TITLE_TAG):
            o.show_name = not self.show_sk_overlay


class HB_FacialProps(PropertyGroup):
    mesh: PointerProperty(
        name="메쉬",
        description="생성되는 본이 따라갈 엣지가 있는 캐릭터 메쉬입니다",
        type=bpy.types.Object,
        poll=_poll_mesh,
    )

    head_bone: StringProperty(
        name="머리 본",
        description="베이스 얼굴 본이 생성될 머리 본입니다. 머리 아래쪽에서 "
        "정수리 방향으로 향해야 하며, 베이스 레이아웃이 이 본의 길이에 맞춰 "
        "스케일됩니다",
        default="",
    )

    eye_mesh_l: PointerProperty(
        name="눈 .L",
        description="왼쪽 안구 메쉬입니다. MSTR-EYE.L과 왼쪽 눈꺼풀 피벗이 "
        "이 메쉬의 정중앙에 배치됩니다",
        type=bpy.types.Object,
        poll=_poll_mesh,
    )

    eye_mesh_r: PointerProperty(
        name="눈 .R",
        description="오른쪽 안구 메쉬입니다. MSTR-EYE.R과 오른쪽 눈꺼풀 피벗이 "
        "이 메쉬의 정중앙에 배치됩니다",
        type=bpy.types.Object,
        poll=_poll_mesh,
    )

    brow_mesh: PointerProperty(
        name="눈썹", description="눈썹 메쉬(양쪽)입니다. 눈썹 마커를 이 메쉬 "
        "위에 배치합니다", type=bpy.types.Object, poll=_poll_mesh)
    teeth_upp_mesh: PointerProperty(
        name="윗니", description="윗니 메쉬입니다. 위쪽 치열 마커를 이 메쉬 "
        "위에 배치합니다", type=bpy.types.Object, poll=_poll_mesh)
    teeth_low_mesh: PointerProperty(
        name="아랫니", description="아랫니 메쉬입니다. 아래쪽 치열 마커를 이 메쉬 "
        "위에 배치합니다", type=bpy.types.Object, poll=_poll_mesh)
    tongue_mesh: PointerProperty(
        name="혀", description="혀 메쉬입니다. 혀 체인 마커를 이 메쉬를 "
        "따라 배치합니다", type=bpy.types.Object, poll=_poll_mesh)

    brow_count: IntProperty(
        name="눈썹 DEF",
        description="눈썹 한쪽당 DEF 본 개수입니다. 4개의 눈썹 마커를 지나는 "
        "커브를 따라 균등하게 배치됩니다",
        default=10, min=2, soft_max=30,
    )

    show_preview: BoolProperty(
        name="미리보기 라인",
        description="뷰포트에서 STR-FACE_UPP / STR-FACE_LOW를 마커 사이의 "
        "라인으로 표시합니다",
        default=True,
    )

    show_sk_overlay: BoolProperty(
        name="편집 메쉬 표시",
        description="편집 메쉬에 상태 색 테두리 / 이름표 / 조합 화살표를 그립니다 "
        "(회색 손 안 댐, 주황 등록 필요, 초록 등록됨, 빨강 다시 뽑기 필요)",
        default=True,
        update=_sk_overlay_update,
    )

    count_mode: EnumProperty(
        name="개수",
        description="각 엣지 경로를 따라 배치할 본의 개수를 정합니다",
        items=[
            ('VERTEX', "모든 버텍스",
             "경로의 버텍스마다 본을 하나씩 배치합니다 (토폴로지를 정확히 따름)"),
            ('EVEN', "균등 개수",
             "정해진 개수의 본을 길이에 따라 균등하게 배치합니다. 캐릭터가 "
             "달라도 본 이름이 동일하게 유지됩니다"),
        ],
        default='VERTEX',
    )

    lip_count: IntProperty(
        name="입술 본 / 한쪽",
        description="균등 개수: 입술 중앙과 입꼬리 사이의 본 개수입니다",
        default=8, min=1, soft_max=20,
    )

    lid_count: IntProperty(
        name="눈꺼풀 본 / 눈꺼풀",
        description="균등 개수: 눈꺼풀 양 끝 사이의 본 개수입니다",
        default=9, min=1, soft_max=30,
    )

    parent_bone: StringProperty(
        name="대체 부모 본",
        description="MCH / DSP 본이 들어갈 베이스 본(MCH_SKULL, MCH-JAW, "
        "MSTR-EYE)이 없을 때 사용할 부모 본입니다",
        default="",
    )

    def_length: FloatProperty(
        name="DEF 길이",
        description="생성되는 DEF / DSP 본의 길이입니다 (위쪽 +Z 방향)",
        default=0.0014, min=0.0001, soft_max=0.05, precision=4, unit='LENGTH',
    )

    show_steps: BoolProperty(
        name="단계별",
        description="단계별 버튼을 표시합니다 (리그 생성은 이 단계를 모두 순서대로 실행합니다)",
        default=False,
    )
    dsp_control: StringProperty(
        name="컨트롤",
        description="저장된 버텍스를 따라 위젯이 움직일 컨트롤 본입니다",
        default="",
    )

    # (kept the old property name so existing files keep their value)
    dsp_armature: PointerProperty(
        name="아마추어",
        description="페이셜 도구가 생성할 대상 리그입니다. 아마추어가 활성일 때 "
        "자동으로 채워지므로, 마커나 메쉬를 선택한 상태에서도 버튼이 "
        "계속 작동합니다",
        type=bpy.types.Object,
        poll=lambda _s, o: o.type == 'ARMATURE',
    )


_classes = (HB_FacialProps,)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.hb_facial = bpy.props.PointerProperty(type=HB_FacialProps)


def unregister():
    del bpy.types.Scene.hb_facial
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
