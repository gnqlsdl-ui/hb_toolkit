# HB Toolkit

Blender 5+ 용 리깅 통합 툴킷 (**Blender Extension** 형식).

## 구조

프로젝트 루트(`hb_toolkit/`)가 곧 Extension 패키지입니다.

```
hb_toolkit/                  # = Extension 패키지
├── blender_manifest.toml    # Extension 메타데이터 (버전 단일 출처)
├── __init__.py              # register/unregister
├── utils/                   # 공통 유틸 (DRY)
├── ui/                      # 메인 N-Panel (Category: "HB")
├── renamer/                 # 기능 1: 본 이름 변경
├── build.ps1 / build.py     # 릴리즈 빌드 스크립트
└── release/                 # 빌드된 zip 보관
```

## 기능

### Renamer
아마처의 선택한 본 이름 일괄 편집.
- **Rename**: 베이스 이름 + 자동 넘버링
- **Add / Remove Prefix**
- **Add / Remove Suffix**
- **Search & Replace** (대소문자 옵션)

> Edit Mode / Pose Mode 양쪽에서 동작합니다.

## 설치 (Blender Extension)

### 방법 1. Install from Disk
1. `release/hb_toolkit_v{버전}.zip` 다운로드
2. Blender → Edit → Preferences → **Get Extensions**
3. 우측 상단 ▼ → **Install from Disk...**
4. zip 선택 → 자동 활성화
5. 3D Viewport → N (사이드바) → **HB** 탭

### 방법 2. (개발자) 폴더 직접 링크
- 확장 저장소 폴더에 패키지를 심볼릭 링크하거나
- `blender --command extension build` 로 공식 빌드

## 빌드 (개발자용)

PowerShell:
```powershell
powershell -ExecutionPolicy Bypass -File .\build.ps1
```

Python (3.6+):
```bash
python build.py
```

`blender_manifest.toml` 의 `version` 을 자동으로 읽어 `release/hb_toolkit_v{MAJOR}.{MINOR}.{PATCH}.zip` 을 생성합니다.

## 새 기능 추가 가이드

1. 프로젝트 루트에 `<feature>/` 폴더 생성 (`properties.py`, `operators.py`, `ui.py`, `__init__.py`)
2. 본 선택 등 공통 로직은 `utils/bone_utils.py` 활용 (중복 금지)
3. `ui/main_panel.py` 의 `draw()` 에서 해당 기능의 `draw_section` 호출
4. 루트 `__init__.py` 의 `_modules` 튜플에 모듈 등록
5. 모든 임포트는 **상대 임포트** (`from . import x` / `from ..utils import y`)

## 버전 업데이트

`blender_manifest.toml` 의 `version` 만 수정 → `build.ps1` 실행. 끝.
