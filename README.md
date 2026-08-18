# HB Toolkit

Blender 5+ 용 리깅 통합 툴킷 (**Blender Extension** 형식).

## 구조

프로젝트 루트(`hb_toolkit/`)가 곧 Extension 패키지입니다.

```
hb_toolkit/                  # = Extension 패키지
├── blender_manifest.toml    # Extension 메타데이터 (버전 단일 출처)
├── __init__.py              # register/unregister
├── utils/                   # 공통 유틸 (DRY)
├── ui/                      # N-Panel 카테고리 상수
├── renamer/                 # 본 이름 변경
├── roll/                    # HB Component (Footbank)
├── build.ps1 / build.py     # 릴리즈 빌드 스크립트
└── release/                 # 빌드된 zip 보관
```

## 기능

### Renamer
아마추어의 선택한 본 이름 일괄 편집.
- **Rename**: 베이스 이름 + 자동 넘버링
- **Add / Remove Prefix**
- **Add / Remove Suffix**
- **Search & Replace** (대소문자 옵션)

> Edit Mode / Pose Mode 양쪽에서 동작합니다.

### HB Component (Footbank)
3D 커서에 메인 가이드를 만들고, 롤 가이드를 붙인 뒤 Build 합니다.

## 설치 / 업데이트 (Blender)

Edit → Preferences → Get Extensions → Repositories **+** 에 아래 URL을 추가합니다.

```
https://raw.githubusercontent.com/gnqlsdl-ui/hb_toolkit/gh-pages/index.json
```

**Check for Updates on Startup** 을 켜면 다음 실행 때 새 버전을 받습니다. N-Panel `hb_toolkit` 의 **Check for Updates** 로도 갱신할 수 있습니다.

### 방법: Install from Disk
1. `release/hb_toolkit_v{버전}.zip` 다운로드
2. Blender → Edit → Preferences → **Get Extensions**
3. 우측 상단 ▼ → **Install from Disk...**
4. zip 선택 → 자동 활성화
5. 3D Viewport → N (사이드바) → **hb_toolkit** 탭

## 빌드 (개발자용)

```powershell
powershell -ExecutionPolicy Bypass -File .\build.ps1
```

```bash
python build.py
```

`blender_manifest.toml` 의 `version` 을 읽어 `release/hb_toolkit_v{MAJOR}.{MINOR}.{PATCH}.zip` 을 생성합니다.

## 릴리즈

작업은 `dev` 에서 하고, 배포는 `main` 에서 태그를 푸시합니다.

```
git switch main
git merge dev
git tag vX.Y.Z
git push origin main --tags
```

GitHub Action 이 zip 을 Release 에 올리고, Blender 용 `index.json` 을 `gh-pages` 에 게시합니다.

## 새 기능 추가 가이드

1. 프로젝트 루트에 `<feature>/` 폴더 생성 (`properties.py`, `operators.py`, `ui.py`, `__init__.py`)
2. 본 선택 등 공통 로직은 `utils/bone_utils.py` 활용 (중복 금지)
3. 해당 기능의 `ui.py` 에 독립 Panel 을 두고 `bl_category` 는 `ui.CATEGORY`
4. 루트 `__init__.py` 의 `_modules` 튜플에 모듈 등록
5. 모든 임포트는 **상대 임포트** (`from . import x` / `from ..utils import y`)

## 버전 업데이트

`blender_manifest.toml` 의 `version` 만 수정 → `build.ps1` 실행. 끝.
