# BMW M 디자인 시스템 적용 및 개발 가이드

이 문서는 `kor-travel-docker-manager` 저장소에 적용된 **BMW M 디자인 시스템(BMW M Design System)**의 시각적 원칙과 개발 규약을 기술합니다.
향후 다른 에이전트(Claude Code, ChatGPT Codex 등)가 UI 컴포넌트 추가, 변경 및 프론트엔드 작업을 진행할 때 본 시스템의 철학이 어긋나지 않도록 **일관성**을 유지해 주시기 바랍니다.

---

## 1. 핵심 철학 (UI Principles)

1. **플랫한 정밀함 (Industrial Precision)**
   - 시스템 내의 거의 모든 모서리 반경은 **0px (`rounded-none`)**입니다.
   - 드롭 섀도우나 그라데이션 광원은 사용하지 않으며, 평면적인 카드 표면(`bg-surface-card` — `#1a1a1a`)과 헤어라인 경계선(`border-hairline` — `#3c3c3c`)의 대비로만 깊이감을 드러냅니다.
2. **서체의 극한 대조 (Typography Contrast)**
   - 대형 표제어(Headlines)는 자신감 있는 **대문자(UPPERCASE) 700 (Bold)**을 사용하여 스탬프로 찍어낸 듯한 기계적인 인상을 줍니다.
   - 본문(Body) 및 설명문은 **300 (Light)** 가중치를 사용하여 대조적인 "엔지니어링된 지적인 목소리"를 연출합니다.
   - 중간 영역(400, 500)의 가중치를 혼용하여 서체 대비(Contrast)를 흐리지 마십시오.
3. **삼색 액센트의 희소성 (M Tricolor Rarity)**
   - M 삼색선(`colors.m-blue-light` → `colors.m-blue-dark` → `colors.m-red`)은 **브랜드의 표식**으로만 작동합니다.
   - 버튼의 배경색이나 일반 텍스트 하이라이팅 등 CTA(Call-to-Action) 요소에는 절대 삼색선을 사용하지 않으며, 오직 가로 디바이더(`m-stripe-divider`)나 로고 배지, 모터스포츠 탭 호버 등 정적 마킹 상황에서만 **드물게** 배치합니다.
4. **정밀성과 신뢰감 중심의 분위기 (Engineered Gravity)**
   - 페이지의 분위기는 불필요한 장식이나 차량 이미지 같은 마케팅성 요소를 배제하고, 차분하고 깊은 **Pure Black 단색 배경**과 날카로운 **1px 헤어라인 그리드**에서 우러나오는 기술적인 신뢰감에서 공급됩니다.

---

## 2. 디자인 토큰 정의 (Design Tokens)

### 1) 컬러 (Colors)
Tailwind CSS의 테마에 직접 통합되어 있습니다 (`tailwind.config.ts` 참고).

| 토큰 키 | 값 | 역할 |
|---|---|---|
| `bg-canvas` | `#000000` | 전체 floor의 기본 순수 블랙 배경색 |
| `bg-surface-soft` | `#0d0d0d` | 스펙 테이블 셀, 푸터 인접 영역 등 미세하게 밝은 블랙 |
| `bg-surface-card` | `#1a1a1a` | 카드, 버튼 배경, 입력 폼 배경 |
| `bg-surface-elevated` | `#262626` | 중첩 카드 레이아웃, 호버 시 하이라이팅 |
| `bg-carbon-gray` | `#2b2b2b` | 기술 사양 세부 spec-cell 등에 사용되는 카본 그레이 |
| `border-hairline` | `#3c3c3c` | 카드 테두리, 테이블 구분선, 1px 라인 기본값 |
| `border-hairline-strong` | `#262626` | 한 단계 더 두드러진 입체 경계선 |
| `text-on-dark` | `#ffffff` | display 타이틀 및 기본 주요 텍스트 |
| `text-body-text` | `#bbbbbb` | 본문 Paragraph (Light 300) 기본 색상 |
| `text-body-strong` | `#e6e6e6` | 강조된 리드 문단 혹은 본문 텍스트 |
| `text-muted` | `#7e7e7e` | 푸터 링크, 캡션, 비활성 탭 텍스트 |
| `colors.m-blue-light` | `#0066b1` | M 삼색선 (첫 번째 라이트 블루) |
| `colors.m-blue-dark` | `#1c69d4` | M 삼색선 (두 번째 다크/헤리티지 블루) |
| `colors.m-red` | `#e22718` | M 삼색선 (세 번째 파워 레드) |
| `colors.electric-blue` | `#0653b6` | M xDrive 전기차 전용 특화 콜드 블루 |

### 2) 타이포그래피 (Typography)
서체는 기본적으로 `BMW Type Next Latin`을 모방하기 위해 `Inter`를 매핑하며, display 자간은 `-0.5px`로 좁혀 구성하거나 대안으로 헤드라인에 `Saira Condensed`를 사용할 수 있습니다.

| 토큰 | 폰트 크기 | 두께 (Weight) | 행간 (Line Height) | 자간 (Letter-spacing) | 사용 영역 |
|---|---|---|---|---|---|
| `display-xl` | `80px` | `700` | `1.0` | `0` (Inter: `-0.5px`) | 최상단 메인 히어로 헤드라인 (대문자 필수) |
| `display-lg` | `56px` | `700` | `1.05` | `0` (Inter: `-0.5px`) | 주요 섹션 헤더 (대문자 필수) |
| `display-md` | `40px` | `700` | `1.1` | `0` (Inter: `-0.5px`) | 카드 섹션 타이틀, 모델명 |
| `display-sm` | `32px` | `700` | `1.15` | `0` | 표 스펙 값, 중간 타이틀 |
| `title-lg` | `24px` | `700` | `1.3` | `0` | 3열 카드 내 대형 카드 타이틀 |
| `title-md` | `20px` | `400` | `1.4` | `0` | 카드 부제목, 리드 단락 |
| `title-sm` | `18px` | `400` | `1.4` | `0` | 스펙 콜아웃, 상세 서술 리드문 |
| `label-uppercase` | `14px` | `700` | `1.3` | `1.5px` (`tracking-machined`) | 버튼 글자, 탭 메뉴, 링크 레이블 (대문자 필수) |
| `body-md` | `16px` | `300` | `1.5` | `0` | 기본 설명문, 본문 단락 |
| `body-sm` | `14px` | `300` | `1.5` | `0` | 푸터 본문, 쿠키 동의 설명문, 세부 법적 고지 |
| `caption` | `12px` | `400` | `1.4` | `0.5px` | 사진 캡션, 크레딧 라인 |
| `button` | `14px` | `700` | `1.0` | `1.5px` (`tracking-machined`) | 모든 버튼 레이블 (대문자 필수) |
| `nav-link` | `14px` | `400` | `1.4` | `0.5px` | 탑 네비게이션 메뉴 아이템 |

### 3) 여백 및 레이아웃 (Spacing)
- **기본 단위 (Base unit):** `4px`
- **토큰 정의:** 
  - `spacing.xxs`: `4px`
  - `spacing.xs`: `8px`
  - `spacing.sm`: `12px`
  - `spacing.md`: `16px`
  - `spacing.lg`: `24px`
  - `spacing.xl`: `40px`
  - `spacing.xxl`: `64px`
  - `spacing.section`: `96px`
- **단위 간격 규약:**
  - 메인 레이아웃 밴드 간의 수직 마진은 반드시 `{spacing.section}` (`96px` 또는 Tailwind `py-24`)을 기준 주기로 정밀하게 유지합니다.
  - 카드 내의 내부 패딩은 `{spacing.lg}` (`24px`) 또는 `{spacing.xl}` (`40px`)로 설정합니다.

---

## 3. 핵심 UI 컴포넌트 규격 (Components Specification)

### 1) Top Navigation (`top-nav`)
- **높이:** `64px` 고정
- **배경:** Pure Black (`bg-canvas` — `#000000`)
- **요소:** 왼쪽 정렬 BMW M 로고(M 삼색선 배지 + BMW roundel + "M"), 중앙 horizontal 메뉴 (`nav-link` 사양), 오른쪽 다국어/검색/계정 클러스터.

### 2) Button
- **Primary Button (`button-primary`):** 
  - 배경 `#000000` (또는 투명), 글자 `#ffffff` (대문자, `tracking-machined`), `1px border-white`. 
  - `rounded-none`, 높이 `48px`, 패딩 `16px 32px`.
- **Outline Button (`button-primary-outline`):**
  - 배경 투명 (`bg-transparent`), 흰색 테두리만 있는 폼. 주로 히어로 이미지 상단 오버레이 CTA로 사용.
- **Icon Button (`button-icon`):**
  - 캐러셀 컨트롤, 화살표 등 원형 제어판. `48px × 48px`, 배경 `bg-surface-card` (#1a1a1a), 원형 (`rounded-full`), 흰색 아이콘 중앙 정렬.

### 3) Containers & Panels
- **인프라 상태 테이블 (`infra-status-table`):**
  - 배경 `#000000`, `rounded-none`, 1px 헤어라인 외곽선 (`border-hairline`). 데이터 필드는 가독성을 위한 Inter (Light 300) 폰트가 탑재되며, 각 셀의 구분은 얇은 border로 마감합니다.
- **콘솔 로그 터미널 (`log-terminal-modal`):**
  - 배경 `#0d0d0d` (`bg-surface-soft`), `rounded-none` 모서리. 상단에 닫기 X 버튼 등 제어판이 존재하며, 내부 텍스트는 monospace 계열의 12px 폰트로 실시간 개행 처리됩니다.
- **성능 실시간 차트 (`chart-panel`):**
  - 배경 `#000000`, `rounded-none`, padding `24px`. Recharts 라이브러리를 동적 로딩하여 CPU(녹색), 메모리(헤리티지 블루), I/O(골드/레드) 라인을 극미니멀 1px 헤어라인 그리드 위에 드로잉합니다. 폰트 크기는 시각 접근성을 위해 12px 이상을 고수합니다.

### 4) Signature Accent (`m-stripe-divider`)
- **형태:** `4px` 두께의 가로형 단색 삼색선 스트라이프.
- **색상 흐름:** 라이트 블루 (`#0066b1`) → 다크 블루 (`#1c69d4`) → 레드 (`#e22718`).
- **사용법:** 임의의 CTA 버튼 채우기나 배경에 절대 금지하며, 오직 모터스포츠 크롬, 세부 사양 단락 분리기, 활성화 탭 표시줄 등 브랜드 상징 순간에만 적용.

---

## 4. 에이전트 개발 규약 (Do's & Don'ts)

### ✅ Do
- **둥근 모서리 차단**: 모든 카드(`rounded-none`), 입력창(`rounded-none`), 버튼(`rounded-none`)은 직각으로 마감합니다.
  - *예외*: carousel 화살표, 닫기 X 버튼, 플로팅 챗봇 버튼과 같이 **원형 제어 장치**에 한해서만 `rounded-full`을 허용합니다.
- **UPPERCASE 700 + tracking-machined**: 버튼과 카테고리 탭 등 레이블 텍스트는 반드시 대문자(All-Caps)와 함께 `tracking-machined` (1.5px 자간)를 주어 기계적으로 조립된 인상을 줍니다.
- **가로 세로 96px 여백**: 레이아웃 컴포넌트 간 수직 간격은 큰 밴드 단위로 `py-24` (96px) 또는 `py-16` (64px)을 등간격으로 배치합니다.

### ❌ Don't
- **삼색선을 버튼에 채우지 마십시오**: 삼색 그라데이션은 M 브랜드의 표식으로만 써야 합니다. `Start`, `Stop` 버튼 등 액션에 삼색선을 입히지 마십시오.
- **Atmospheric Gradient 금지**: 텍스트 뒤에 화려한 그라데이션 백그라운드나 발광 네온 장식을 삽입하지 마십시오. 여백은 순수한 단색 Canvas (#000000) 또는 Surface Card (#1a1a1a)로 채웁니다.
- **가독성을 위한 임의의 Bold 적용 금지**: 본문이나 세부 스펙 텍스트에 Bold(400, 500)를 어설프게 섞지 마십시오. M 브랜드 목소리의 핵심은 **700과 300의 날카로운 두께 대비**입니다.

---

## 5. 반응형 설계 및 중단점 (Responsive Breakpoints)

| 이름 | 범위 | 주요 반응형 규칙 |
|---|---|---|
| **Mobile** | `< 768px` | 햄버거 메뉴로 전환, 히어로 h1 폰트 `48px` 축소, 그리드 1열 배치, 푸터 1열 축소 |
| **Tablet** | `768px ~ 1024px` | 네비게이션 가로 유지(조밀화), 카드 그리드 2열 배치, 스펙 테이블 2열 배치 |
| **Desktop** | `1024px ~ 1440px` | 전체 탑 네비게이션 활성화, 3열 카드 그리드, 스펙 테이블 4열 배치 |
| **Wide** | `> 1440px` | 최대 콘텐츠 폭 `1440px`에 고정 후 중앙 정렬, 좌우 마진 확보 |

---

## 6. 적용 파일 현황

- **Tailwind 설정**: [tailwind.config.ts](file:///f:/dev/kor-travel-docker-manager/frontend/tailwind.config.ts) (M 브랜딩 색상, 스페이싱, 0px 둥글기 스케일 수록)
- **글로벌 스타일**: [globals.css](file:///f:/dev/kor-travel-docker-manager/frontend/src/app/globals.css) (Next.js font/google 연동 및 스크롤바 0px 직각화 적용)
- **대시보드 페이지**: [page.tsx](file:///f:/dev/kor-travel-docker-manager/frontend/src/app/page.tsx) (BMW M 디자인 원칙인 Pure Black, 둥글기 0px, 서체 대비, M 삼색선 디바이더를 이식하여 리스킨된 메인 인프라 제어 센터)
