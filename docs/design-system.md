# 운영 콘솔 디자인 시스템

이 문서는 인증, 실시간 컨테이너 현황, inspect, 로그·메트릭, 백업 이력을 보여 주는
`kor-travel-docker-manager` 운영 콘솔의 디자인 기준이다. 고객용 서비스 화면에는 적용하지
않는다.

## 정본과 시각 방향

- 제품 방향: [`DESIGN.md`](../DESIGN.md)
- CSS 토큰: [`frontend/tokens.css`](../frontend/tokens.css)
- 전역 스타일: [`frontend/src/app/globals.css`](../frontend/src/app/globals.css)
- 대시보드 조립: [`DashboardClient.tsx`](../frontend/src/components/DashboardClient.tsx)

화면은 최신 `kor-travel-map` admin의 Rail-Workbench 구조를 따른다. 밝은 페이지·카드 표면과
Ember 오렌지 조치 신호, 얕은 그림자와 hairline 구분선을 사용하며, 운영 데이터가 정보 밀도를
만든다. 구조와 컴포넌트 간격은 Map과 맞추되 `frontend/tokens.css`의 기존 오렌지 색상톤은
변경하지 않는다.

## 토큰 사용

`frontend/tokens.css`의 `@theme` 토큰을 JSX와 CSS에서 재사용한다. 새 색상·그림자·radius를
직접 작성하지 않는다.

| 용도 | 토큰 |
|---|---|
| 페이지·표면 | `--color-page`, `--color-card`, `--color-subtle`, `--color-elevated` |
| 본문·상태 | `--color-strong`, `--color-ink`, `--color-secondary`, `--color-brand`, `--color-danger`, `--color-warn`, `--color-ok` |
| 관찰 표면 | `--color-graphite`, `--color-graphite-2`, `--color-graphite-ink` |
| 경계·그림자 | `--color-line`, `--shadow-card`, `--shadow-card-hover`, `--shadow-modal` |
| 형태·동작 | `--radius-card`, `--radius-panel`, `--radius-pill`, `--ease-default` |
| 서체 | `--font-display`, `--font-sans`, `--font-mono` |

카드와 모달은 낮은 대비의 그림자를 사용하고, 상태색은 상태 점·배지·작은 수치 강조에
한정한다. 위험한 조치는 해당 버튼과 확인 문구를 가까이 배치한다. Docker stats의 값은
수집되지 않은 경우 임의의 0 대신 확인 필요 상태로 표시한다.

## 화면 구조

1. 상단에는 서비스 식별, 명령 진입점, 설정·백업·로그아웃과 동기화 상태를 둔다.
2. 요약 영역은 서비스 원장과 현재 동기화 상태를 함께 보여 주되, 라이브 데이터가 없는
   수치나 차트를 꾸며 넣지 않는다.
3. 서비스 원장은 데스크톱에서 표로, 768px 이하에서 의미가 보이는 행으로 표시한다.
4. inspect·로그·메트릭·백업·인증 설정은 같은 작업 표면 언어를 사용하고 Escape, 초기
   focus, keyboard navigation을 보존한다.

## 검토 기준

- 새 화면이 별도 색상 체계나 토큰을 만들지 않았는가?
- gradient, glass 효과, 반복 카드, 가로 스크롤 표, `transition-all`이 들어가지 않았는가?
- 320px, 375px, 414px, 768px 폭에서 조작과 닫기 동작이 가능한가?
- 문서의 설명이 실제 [`tokens.css`](../frontend/tokens.css)와 컴포넌트 구현에 맞는가?
