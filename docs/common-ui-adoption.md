# 공통 UI 채택 기록

## 범위와 현황

원본 dirty checkout을 보존한 ext4 shared clone에서 소비자만 수정한다.
컨테이너 원장, 마운트, 백업 이력, 고정 버전의 네 표를 공통 VirtualTable로 이전한다.
인증 설정·백업·버전·배포 상태·상세 및 빠른 명령·로그·차트·설정 창은 공통 Radix Dialog로 통일한다.
브라우저 확인 창은 공통 AlertDialog로 대체한다. 로그인은 LoginForm 주입 콜백,
탐색은 AppMenu를 사용한다. 지도는 추가하지 않는다.

## 부모 공용 API 요청

`@kor-travel/ui/virtual-table`: data, columns, getRowId, manualSorting(기본 true),
sorting/onSortingChange, virtualized, empty/loading/error, selection과 2인수 ColumnDef 타입.
`@kor-travel/ui/primitives`: Button, Checkbox, Dialog 계열, AlertDialog 계열, Tabs 계열.
LoginForm은 기존 LoginSubmission과 onSubmit 계약을 유지하면 충분하다.
로그인 RHF/Zod는 부모 공통 구현을 사용하며, 소비자 비밀번호 변경 폼도 RHF/Zod를 사용한다.
Zustand는 AppShell마다 독립 store를 만들어 메뉴 접힘을 보관한다. 인증 상태를 옮기지 않는다.
Pretendard는 로컬 패키지 CSS를 사용해 외부 폰트 요청 없이 적용한다.

## 버전 정책

부모 version-targets.json의 exact 버전을 적용한다. TypeScript는 부모가 확인한
openapi-typescript 및 typescript-eslint peer 호환성에 따라 5.9.3을 유지하고,
Node 타입은 실행 환경 22에 맞춘 22.20.5를 사용한다. Base UI는 도입하지 않는다.


## 구현과 검증의 경계

`OpsDialog`가 Radix 포털·Escape·포커스 가두기를 사용하며 호출 버튼 복귀를 연결한다.
`useConfirm`은 AlertDialog 승인 전 실행 차단, 취소·unmount 해제, 호출 버튼 복귀를 담당한다.
기존 인증/로그아웃 회귀와 함께 확인 창 승인·취소, AppShell store 격리 회귀를 검증한다.
의존성 및 산출물 검사는 `npm run check:dependencies`로 CI 설치 직후 실행한다.
최종 설치·검증 결과는 journal에 남긴다.


## 브라우저 검증에서 수정한 회귀

첫 확인 창 취소 뒤 호출 버튼 초점이 복귀하지 않는 문제를 데스크톱·모바일에서 재현했다.
인라인 표 열 정의의 cell renderer identity가 바뀌며 버튼 DOM이 교체된 것이 원인이었다.
서비스 원장의 열 정의를 모듈 상수로 옮겨 호출 버튼을 유지하고 동일 브라우저 회귀로 확인했다.
모바일 AppMenu는 기존 가로 탐색을 유지하도록 슬롯별 반응형 스타일을 적용했다.

## React Doctor 전체 경고 차단

0.9.22의 실제 `--help`에서 `--no-telemetry`, `--scope full`, `--blocking warning`을 확인했다.
CI는 전체 소스를 경고 차단 모드로 검사하며 오류0·경고0을 확인했다. suppression이나 baseline은 없다.
앞 후보의16경고 이력은 journal에 보존한다.

큰 패널의 조회/상태 hook과 의미 있는 화면 구역을 분리했다. 대시보드는 원장·요약·연결 상태·
앱별 상태·각 운영 모달, 인증 설정은 키·감사·비밀번호, 버전은 경고·변경 요청·이력,
배포 상태는 준비도·사전 점검·소스 상태로 나뉜다. 상세 탭과 설정 배열/미리보기도 별도 경계다.
공통 표·Radix·세션 authority는 유지하고 상태를 view 안으로 복제하지 않는다.

순차 재시작은 Promise 체인으로 이전 요청이 끝난 뒤 다음 요청을 보낸다. 병렬화하지 않았다.
백업 생성 응답은 작업 전용 캐시에 연결해 진행 중 상태를 즉시 표시하되, 실제 백업 목록은
기존대로 작업 완료 뒤 재조회한다. 별도 회귀가 서버 응답을 완료로 과장하지 않는지 확인한다.
세션 소실 시 확인 취소는 활성 세션의 cleanup으로 처리하며 재로그인 회귀를 유지한다.
Toast 상태 생성 함수는 순수 모듈로 옮겨 Fast Refresh 경계를 분리했다.

`check:dependencies`는 `--source src`를 명시해 중복 Table 구현과 금지 의존성도 검사한다.

## WebSocket 정리

기존 상태·로그 소켓의 생성 실패·재연결·인증 만료 분기는 유지한다.
생성 소켓과 핸들러 해제/close를 `createManagedWebSocket` 자원으로 묶었고,
상태 효과는 재연결·unmount, 로그 효과는 컨테이너 전환·unmount 때 해당 자원을 해제한다.
로그아웃9개 회귀와 자원 해제2개 회귀를 함께 확인한다.
