# HB Component — Footbank

발 / 손목 등 **twist·roll·bank** 메커니즘을 가이드 기반으로 생성하는 컴포넌트 빌더입니다.

N-Panel: **HB Component**

## 워크플로 요약

```
1. Add Component (메인 Guide- 생성)
      ↓  사용자가 회전·롤 맞춤
2. Place Roll Guides (Roll 가이드 배치)
      ↓
3. Build (Root / Ctrl / System / DEF 생성)
      ↓
4. Mirror (선택, 반대쪽 복사)
```

필요 시 **Clear Build** / **Clear Roll Guides**로 되돌릴 수 있습니다.

## 1. Add Component

- **Segment**: 본 구간 이름 (예: `ForeArm`, `Hand`)
- **Side**: `.L` / `.R`
- **Name**: 컴포넌트 인스턴스 라벨 (`component` 커스텀 프로퍼티)
- **Footbank** 버튼: 3D 커서 위치에 메인 가이드 `Guide-{seg}{side}` 생성

메인 가이드는 커서 **head**, tail은 **-Z × Size** 방향입니다. 생성 후 Edit Mode에서 회전·롤을 맞춥니다.

## 2. Place Roll Guides

메인 가이드를 선택한 상태에서 실행합니다.

생성·배치되는 본:

| 본 | 역할 (`hb_role`) |
|----|------------------|
| `GRP_Guide-{seg}{side}` | 그룹 루트 |
| `Guide-{seg}_Roll_Back{side}` | back |
| `Guide-{seg}_Roll_Front{side}` | front |
| `Guide-{seg}_Roll_In{side}` | in |
| `Guide-{seg}_Roll_Out{side}` | out (선택) |

메인 가이드의 **로컬 Z = up**, **로컬 X = side** 축을 기준으로 `GUIDE_RATIOS` 비율로 배치합니다.

### 가이드 체인 (다구간)

ForeArm + Hand처럼 **연속 구간**을 만들 때:

1. ForeArm 메인 가이드 생성 → Place Roll Guides → (선택) Build
2. Hand 메인 가이드를 **ForeArm tail** (또는 상위 Guide-)에 맞춤
3. **Parent** 또는 **Connect**로 상위에 연결
4. Hand Place Roll Guides → Build

빌드 시 **하위 Root**는 상위 **DEF**에 자동 연결됩니다.

예: `Root-Hand.L` → `DEF-ForeArm.L`

체인 인식 방법:

- 메인 가이드의 **부모 체인** (Parent / Connect 모두 지원)
- `GRP_Guide`가 상위 `Guide-`를 부모로 유지
- 부모가 없을 때 **head–tail 근접** 폴백 (같은 side의 다른 메인 가이드)

### Connect 사용 시 주의 (Place Roll Guides)

Connect로 상위에 붙인 메인 가이드는 Place 시 **GRP** 아래로 옮겨집니다.  
헤드 위치가 밀리지 않도록 connect를 먼저 끊고, 배치 후 원래 head/tail을 복원합니다.

## 3. Build

Place된 가이드를 읽어 다음 계층을 생성합니다.

```
Root-{seg}
├── GRP_Ctrl-{seg}     → Ctrl (Twist, FK, Roll_Back/Front)
├── GRP_System-{seg}   → System (roll / rot 네트워크)
└── GRP_DEF-{seg}      → DEF-{seg}
```

### Build 옵션

- **Size**: 헬퍼 본 길이·위젯 스케일 계수 (가이드 위치는 가이드에 따름)
- **Create Widgets**: 커스텀 쉐이프 자동 할당

### 생성 위젯 (v1.3+)

| 컨트롤 | 메쉬 | 비고 |
|--------|------|------|
| Root | `WGT-Root` | square frame |
| FK | `WGT-Circle` | bone_size=False |
| Twist | `WGT-Twist_Arrow` | GRP_System cstf |
| Roll Back / Front | `WGT-Roll_Arc` | System_Roll_In cstf |

## 4. Clear

| 버튼 | 삭제 대상 | 유지 |
|------|-----------|------|
| **Clear Build** | Root, Ctrl, System, DEF, GRP_* | `Guide-*` 메인·롤 가이드 |
| **Clear Roll Guides** | GRP_Guide, Roll_* | 메인 `Guide-{seg}` (+ Clear Build 동작) |

Clear Roll Guides 후 메인 가이드는 **상위 Guide- 부모**를 복원합니다.

## 5. Mirror

빌드된 한쪽을 반대 side로 복사합니다.

- **Mirror From**: `-X → +X` (`.L` → `.R`) 또는 반대
- 미러 전에 해당 side 본을 선택

## 커스텀 프로퍼티

| 키 | 의미 |
|----|------|
| `hb_role` | 가이드 역할 (`main`, `back`, `front`, …) |
| `hb_type` | 메커니즘 타입 (`footbank`) |
| `component` | 인스턴스 이름 |
| `def_main` | DEF 생성용 메인 가이드 표시 |

## 팁

- 여러 구간을 한 번에 Build: 각 구간 가이드를 선택 후 Build (부모 구간이 먼저 빌드됨)
- Size는 가이드 **배치**에는 영향 없음 — 생성 본 길이·위젯 스케일만
- 위젯이 이상하면 **Create Widgets** 켠 상태로 Clear Build → Build
