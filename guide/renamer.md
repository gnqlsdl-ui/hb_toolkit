# Renamer

아마추어 본 이름을 일괄 편집하는 기능입니다.

N-Panel: **renamer**

## 사용 방법

1. 아마추어를 선택
2. Edit Mode 또는 Pose Mode에서 대상 본 선택
3. 패널에서 원하는 작업 실행

## 기능

### Rename

- **New Name**: 베이스 이름 입력
- 선택 본에 자동 넘버링 적용

### Prefix / Suffix

- **Prefix** / **Suffix** 필드에 문자열 입력
- **Add** / **Remove**로 일괄 추가·제거

### Search & Replace

- **Find** / **Replace** 텍스트
- **Case Sensitive** 옵션
- 선택 본 이름에 일괄 치환

## 모드

Edit Mode와 Pose Mode 모두 지원합니다. Pose Mode에서는 선택된 pose bone에 대응하는 edit bone을 처리합니다.

## META ↔ RIG 동기화 시

META-Tumbo / RIG-Tumbo 등 **양쪽 아마추어에 같은 이름**이 필요한 작업에서는:

- 본 이름 변경 후 콘스트레인트 subtarget, 드라이버, 버텍스 그룹 등 **참조 경로**를 함께 확인하세요.
- 프로젝트 리그 규칙에 `NOHLP_Armature@BoneName` 콘스트레인트 이름 규칙이 있다면 본 이름 변경 시 콘스트레인트 이름도 맞춥니다.
