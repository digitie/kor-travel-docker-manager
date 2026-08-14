# Kor Travel Docker Manager 디자인 시스템

## 목적과 범위

이 문서는 Kor Travel Docker Manager 운영 콘솔의 화면 설계 정본이다. 인증, 실시간 컨테이너 현황, 상세 inspect, 로그·성능 차트, 백업 이력, 관리자 인증 설정과 오류 화면이 하나의 언어로 동작하도록 규정한다. 비즈니스 서비스 화면이나 고객용 사이트에는 적용하지 않는다.

이번 시스템은 Hallmark 감사(2026-08-13)를 바탕으로 이전의 브랜드 기록을 대체한다. 화면은 장식보다 운영 판단과 안전한 조치의 순서를 먼저 드러낸다.

## 고정된 방향

| 구분 | 결정 |
| --- | --- |
| 장르 | modern-minimal |
| 앱 구조 | Workbench — 상단 작업 도구, 요약 신호, 서비스 원장, 상세 작업 표면 |
| 테마 | Cobalt — 차가운 종이 표면, cobalt 조치 신호, graphite 관찰 표면 |
| 탐색 | N13 인라인 명령 팔레트. `⌘/Ctrl + K`와 버튼이 같은 명령을 연다. |
| 하단 | Ft2 단일 상태 행. 데이터 원천과 현재 동기화 방식을 짧게 표시한다. |
| 장식 | 없음. 라이브 인프라 데이터가 시각적 밀도를 담당하며, 의미 없는 이미지·일러스트·그라데이션을 쓰지 않는다. |

## 공통 토큰

토큰 구현 정본은 `frontend/tokens.css`다. JSX, 차트 설정, 새 CSS에 별도 색상 값·그림자·radius를 넣지 않는다.

### 색상

| 역할 | 토큰 | 값 |
| --- | --- | --- |
| 페이지 | `--color-page` | `oklch(98.2% 0.006 255)` |
| 표면 | `--color-card` | `oklch(100% 0 0)` |
| 보조 표면 | `--color-subtle` | `oklch(95.9% 0.012 255)` |
| 구분선 | `--color-line` | `oklch(87.4% 0.018 255)` |
| 본문 | `--color-ink` | `oklch(35% 0.025 258)` |
| 강한 본문 | `--color-strong` | `oklch(24.5% 0.021 258)` |
| 주요 조치 | `--color-brand` | `oklch(56% 0.205 260)` |
| 성공 | `--color-ok` | `oklch(54% 0.14 150)` |
| 경고 | `--color-warn` | `oklch(64% 0.16 72)` |
| 위험 | `--color-danger` | `oklch(53% 0.2 28)` |
| 관찰 표면 | `--color-graphite` | `oklch(22% 0.025 258)` |

상태색은 상태 점, 배지, 작은 수치 강조에만 쓴다. 큰 컨테이너 표면 전체를 상태색으로 칠하지 않는다. 위험한 동작은 경고 배너가 아니라 해당 조치 버튼과 확인 문구에 가깝게 둔다.

### 타이포그래피

| 용도 | 서체 | 규칙 |
| --- | --- | --- |
| 표시 제목 | Space Grotesk | 대시보드 제목·섹션 제목. `600` 중심, 약한 음수 자간 |
| 본문·폼 | IBM Plex Sans | 14px 이상을 기본으로 하며 긴 문장은 1.5 이상의 행간 |
| 데이터·코드 | IBM Plex Mono | 컨테이너명, 포트, 해시, 수치, 동기화 상태 |

작은 대문자 레이블은 데이터 구획을 알리는 용도만 쓴다. 제목·버튼·설명 전체에 대문자를 남용하지 않는다.

### 간격·형태·모션

- 간격은 `--space-3xs`부터 `--space-2xl`까지의 8단계 토큰만 사용한다.
- 카드와 버튼의 기본 radius는 6px(`--radius-card`), 큰 작업 표면은 10px(`--radius-panel`)다. 큰 pill은 상태 배지에만 쓴다.
- 그림자는 카드와 모달에만 약하게 사용한다. hover는 그림자보다 테두리와 표면색의 변화로 먼저 전달한다.
- 전환은 색상·테두리·그림자·변형처럼 필요한 속성만 150ms `--ease-default`로 제한한다. `transition-all`은 사용하지 않는다.
- `prefers-reduced-motion`에서는 비필수 애니메이션과 전환을 거의 제거한다.

## 화면 구조

### 운영 대시보드

1. 고정 상단: 서비스 식별, 인라인 명령 진입점, 설정·백업·로그아웃, 동기화 상태.
2. 요약: 동일 카드 네 장이 아니라, 서비스 수를 하나의 수평 원장으로 보이고 우측 graphite 신호면에 실시간/폴백 상태를 둔다.
3. 서비스 원장: 데스크톱은 표, 768px 이하에서는 레이블이 포함된 행 카드로 자연스럽게 전환한다. 가로 스크롤을 기본 수단으로 삼지 않는다.
4. 하단: 데이터 원천과 현재 동기화 모드를 한 줄로 표기한다.

### 인증과 오류

인증은 graphite 안내면과 밝은 폼면을 분리한다. 오류는 같은 밝은 작업 카드에서 원인·복구·상세 정보를 순서대로 보인다. 고객 마케팅용 hero, 미확인 통계, 장식 이미지는 사용하지 않는다.

### 상세·관리 패널

상세 inspect, 백업 이력, 인증 설정, 로그, 차트, 구성 변경은 동일한 `ops-modal` 헤더와 닫기 버튼을 쓴다. backdrop blur를 사용하지 않으며, 키보드 Escape와 초기 포커스 처리를 유지한다.

## 상호작용과 상태

| 요소 | 기본 | Hover | Focus | Active | Disabled | Loading | Error | Success |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 기본 버튼 | card+line | brand tint | 2px brand outline | 1px 아래 이동 | 50% opacity | spinner+문구 | danger 근접 문구 | 성공 문구 |
| 주요 버튼 | brand | brand ink | 2px brand outline | 1px 아래 이동 | 50% opacity | spinner+문구 | 위험 문구 | 완료 문구 |
| 입력 | card+line | tertiary line | brand line+ring | 유지 | 입력 금지 | 제출 버튼에서 표현 | 필드 아래 문구 | 별도 성공색 없음 |
| 표 행 | card | row surface | 행 안의 조치로 이동 | 해당 조치만 이동 | 조치만 비활성 | 처리 중 레이블 | 상태값과 원인 | 최신 데이터 반영 |

명령 팔레트는 실제 설정·백업·로그아웃 동작을 연다. 모달과 명령 팔레트는 Escape로 닫히며, 모든 클릭 가능한 요소는 키보드 focus를 잃지 않는다.

## 반응형 규칙

| 폭 | 규칙 |
| --- | --- |
| 320px | 상단 제어를 두 열과 명령 한 줄로 배치하고, 모든 조치의 높이를 40px 이상 보장한다. |
| 375px | 요약 수치를 두 열 원장으로 유지하고 긴 컨테이너명은 줄바꿈한다. |
| 414px | 서비스 행의 조치·도구를 전체 폭 행으로 배치한다. |
| 768px 이하 | 표 header를 숨기고 각 셀의 `data-label`을 표시한다. 콘텐츠 가로 스크롤을 만들지 않는다. |
| 960px 이상 | 서비스 원장을 표로, 요약을 넓은 원장과 우측 신호면으로 배치한다. |

## 구현 산출물

### CSS 변수

`frontend/tokens.css`의 `@theme`과 `:root`가 구현 정본이다. 새 화면은 `bg-brand`, `text-secondary`, `border-line`과 `ops-*` 공통 클래스를 사용한다.

### Tailwind v4

`@theme` 토큰으로 Tailwind 유틸리티를 생성한다. 임의 hex, 임의 shadow, 임의 radius 유틸리티를 새로 만들지 않는다.

### DTCG 대응

```json
{
  "color": {
    "brand": { "$type": "color", "$value": "oklch(56% 0.205 260)" },
    "surface": { "$type": "color", "$value": "oklch(100% 0 0)" },
    "graphite": { "$type": "color", "$value": "oklch(22% 0.025 258)" }
  },
  "font": {
    "display": { "$type": "fontFamily", "$value": "Space Grotesk" },
    "body": { "$type": "fontFamily", "$value": "IBM Plex Sans" },
    "mono": { "$type": "fontFamily", "$value": "IBM Plex Mono" }
  }
}
```

### shadcn 대응

기존 shadcn 구성요소를 추가할 때 `background`는 `--color-card`, `foreground`는 `--color-strong`, `primary`는 `--color-brand`, `border`는 `--color-line`, `ring`은 `--color-brand`로 매핑한다. 새 컴포넌트도 동일한 6px 기본 radius와 150ms 속성별 전환을 사용한다.

## 감사 기준

- 새 화면이 또 다른 비공유 디자인 시스템을 만들면 안 된다.
- 동일한 동등 카드 그리드, 의미 없는 glass/gradient, 카드 안의 카드, 가로 스크롤 표, `transition-all`은 회귀로 본다.
- 라이브 데이터가 없는 내용을 수치·차트·배지로 꾸미지 않는다.
- 320px, 375px, 414px, 768px에서 실제 클릭·입력·닫기 동작을 검증한다.
