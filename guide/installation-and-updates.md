# 설치 및 업데이트

HB Toolkit은 **Blender Extension** 형식으로 배포합니다 (Blender 4.2+ / 5+).

## 원격 저장소로 설치 (권장)

1. Blender → **Edit → Preferences → Get Extensions**
2. **Repositories** 탭 → **+** 추가
3. Index URL:

```
https://raw.githubusercontent.com/gnqlsdl-ui/hb_toolkit/gh-pages/index.json
```

4. 저장소 이름 예: `HB Toolkit GitHub`
5. 목록에서 **HB Toolkit** 설치

### 자동 업데이트

- Preferences → Get Extensions → **Check for Updates on Startup** 활성화
- 또는 N-Panel `hb_toolkit` 하단 **Check for Updates** 버튼

## Install from Disk

1. [GitHub Releases](https://github.com/gnqlsdl-ui/hb_toolkit/releases)에서 `hb_toolkit_v{버전}.zip` 다운로드
2. **Get Extensions** → 우측 상단 ▼ → **Install from Disk...**
3. zip 선택 후 활성화

## 설치 후 확인

- 3D Viewport → N → **hb_toolkit** 탭이 보이면 정상
- 애드온이 로드되지 않으면 **Reload** 또는 Blender 재시작

## 개발용 로컬 빌드

```powershell
powershell -ExecutionPolicy Bypass -File .\build.ps1
```

산출물: `release/hb_toolkit_v{MAJOR}.{MINOR}.{PATCH}.zip`

버전은 `blender_manifest.toml`의 `version`만 수정하면 됩니다.

## 문제 해결

| 증상 | 확인 |
|------|------|
| N-Panel에 탭 없음 | Extension이 활성화되었는지, Blender 5.0+ 인지 확인 |
| 코드 수정이 반영 안 됨 | `extensions/user_default/hb_toolkit`에 설치된 복사본을 사용 중일 수 있음. zip 재설치 또는 해당 폴더에 파일 복사 후 Reload |
| 업데이트가 안 됨 | `index.json` URL이 gh-pages 최신인지, 네트워크 확인 |
