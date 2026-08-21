# 운영 콘솔 디자인 규칙

이 문서는 `kor-travel-docker-manager` 운영 콘솔의 구현 규칙을 요약한다. 시각 방향의 정본은
[`DESIGN.md`](../DESIGN.md)이고, 실제 CSS 토큰의 정본은
[`frontend/tokens.css`](../frontend/tokens.css)다. 이 문서에 토큰 값을 복제하지 않고 두 파일을
기준으로 검토한다.

## 적용 범위

대시보드는 실시간 인프라 상태와 안전한 운영 조치를 빠르게 판단하는 Workbench다. 마케팅용
hero, 장식 이미지, 의미 없는 통계, gradient, glass 효과를 추가하지 않는다.

## 핵심 규칙

1. `--color-page`, `--color-card`, `--color-subtle`, `--color-line`으로 페이지·표면·구분선을
   구성한다.
2. `--color-brand`는 선택, 주요 조치, 진행 상태와 작은 강조에만 사용한다. `--color-danger`,
   `--color-warn`, `--color-ok`는 의미를 가진 상태 표시에만 사용한다.
3. 카드와 작업 패널은 `--radius-card`와 `--radius-panel`을 사용한다. 임의 radius와 강한
   색 그림자를 추가하지 않는다.
4. `--shadow-card`, `--shadow-card-hover`, `--shadow-modal` 외의 그림자를 새로 만들지 않는다.
5. 표시 제목은 `Space Grotesk`, 본문은 `IBM Plex Sans`, 컨테이너명·포트·해시는
   `IBM Plex Mono` 계열을 사용한다.
6. 전환은 필요한 속성만 `--ease-default`로 제한하고 `transition-all`을 사용하지 않는다.
   `prefers-reduced-motion`에서는 비필수 전환을 줄인다.
7. 서비스 표는 768px 이하에서 셀의 의미가 보이는 행 형태로 바꾸고, 핵심 조작은 키보드와
   작은 화면에서도 접근할 수 있어야 한다.

## 화면별 확인 항목

- 대시보드: 서비스 원장, 요약 신호, 동기화 방식과 명령 진입점을 먼저 보여 준다.
- 상세 패널: inspect, 로그, 메트릭, redacted 환경변수와 복구 조치를 같은 작업 표면에서
  구분한다.
- 인증·오류: 원인, 복구 방법, 상세 정보 순서를 유지하고, 위험한 조치에는 확인 문구를
  가까이 둔다.

새 화면이나 컴포넌트를 추가할 때는 먼저 [`DESIGN.md`](../DESIGN.md)와 기존
[`DashboardClient.tsx`](../frontend/src/components/DashboardClient.tsx),
[`globals.css`](../frontend/src/app/globals.css)를 확인하고 공용 토큰을 재사용한다.
