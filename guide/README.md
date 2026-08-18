# HB Toolkit 가이드

Blender 5+ 리깅 툴킷 **HB Toolkit** 사용 문서입니다.

| 문서 | 내용 |
|------|------|
| [설치 및 업데이트](installation-and-updates.md) | Extension 설치, 원격 저장소, 자동 업데이트 |
| [HB Component (Footbank)](footbank-component.md) | Footbank 컴포넌트 워크플로, 가이드 체인, 빌드 |
| [Renamer](renamer.md) | 본 이름 일괄 변경 |

## N-Panel 위치

3D Viewport → **N** (사이드바) → **hb_toolkit** 탭

각 기능은 접을 수 있는 독립 패널로 나뉩니다 (`renamer`, `HB Component`).

## 개발 / 릴리즈 (요약)

- 작업 브랜치: `dev`
- 배포 브랜치: `main` + 태그 `vX.Y.Z`
- 버전 단일 출처: `blender_manifest.toml`의 `version`
- 빌드: `powershell -ExecutionPolicy Bypass -File .\build.ps1`

자세한 저장소 구조는 루트 [README.md](../README.md)를 참고하세요.
