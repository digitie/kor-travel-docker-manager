# TASKS — 진행 중 백로그

이 문서는 진행 중이거나 아직 시작하지 않은 작업만 관리한다. 완료·퇴역·대체된 작업은
[`docs/tasks-done.md`](tasks-done.md)에 기록한다. 코드와 맞지 않는 과거 실행 절차는 active task로
남기지 않는다.

- 진행 중: `[/]`
- 미진행: `[ ]`

## 작업 현황

| 태스크 ID | 작업 항목 | 상태 | 다음 완료 조건 |
|:---|:---|:---:|:---|
| **MAP-LIVE-FOLLOWUP** | Map/PinVi cross-repo live consumer acceptance 후속 | `[/]` | PinVi WebSocket/mutating loop·consumer reconciliation과 Map task/journal/manifest 교차 대조를 실제 pair에서 기록 |
| **BACKUP-FOLLOWUP** | 독립 standalone backup의 남은 운영 보강 | `[/]` | off-box 사본 자동화와 보존 정책. Alembic downgrade/이전 revision restore는 범위 밖 |

## 공통 진행 규율

1. 작업 시작과 PR merge 직전에 `origin/main`을 fetch하고 필요하면 rebase한다.
2. 검증 가능한 작은 단위로 커밋하고 remote PR branch에 자주 push한다.
3. 코드 PR은 서로 다른 관점의 전문 적대 리뷰 2건을 통과해야 한다.
4. push 전에 staged file·일반 비밀·프로젝트별 민감 문자열 감사를 실행한다.
5. 완료된 항목은 이 파일에 `[x]`로 쌓지 않고 즉시 `tasks-done.md`로 옮긴다.
6. n150 배포와 Playwright live E2E는 `docs/deploy-runbook.local.md`를 정본으로 수행한다.

## MAP-LIVE-FOLLOWUP — Map/PinVi cross-repo live consumer acceptance

H300의 이번 수락은 schema/bootstrap·runtime provenance·login setup/protected view와
data-independent UI 11개에 한정했다. logout 후 재차단, PinVi WebSocket/mutating loop,
consumer reconciliation은 별도 운영 acceptance로 남긴다. 구현·실행 정본은
[Map 저장소 `docs/tasks.md`](https://github.com/digitie/kor-travel-map/blob/main/docs/tasks.md)의
`T-VN-41C`·`T-VN-41F1D-D2`이며, 이 Manager task는 cross-repo 결과와 exact pair를 함께 기록한다.

- [x] Map UI의 현재 socket close와 `/login` redirect, ticket/lease WebSocket wire를 실제 브라우저로
  확인했다.
- [x] Map UI에서 logout 뒤 `/ops/datasets` protected route 재진입이 `/login`으로 재차단되는지
  확인했다.
- [x] PinVi exact pair에서 logout 뒤 `/admin/features` protected route 재진입이 `/admin/login`으로
  재차단되는지 확인했다. 이 검증은 `/auth/logout` 204를 확인하고 application row를 쓰지 않았다.
- [/] PinVi #477 squash merge의 exact source와 새 pinset을 candidate build·attestation으로
  반영한다. 신뢰된 Manager의 최초 rebuild와 동일한 공식 재개는 새 pinset별 v8 journal을
  `map_runtime_ready` 미종결 상태에 남기고 각각 0이 아닌 종료로 끝났다. 두 전문 적대 리뷰는
  추가 재시도·수동 Docker/Compose/SQL·DB/journal/permit 조작을 금지했다. 기존 H300 generation은
  이전 pinset의 immutable 이력으로 보존하며, 새 candidate가 committed되기 전에는 #477 runtime
  반영 증거나 Map/PinVi live acceptance 근거로 사용하지 않는다. 비밀 비포함 외부 인시던트 증적이
  원인을 분리할 때까지 이 항목을 완료 처리하지 않는다.
- [ ] PinVi WebSocket/mutating loop와 consumer reconciliation의 성공·실패 증거를 기록한다.
- [ ] Map 저장소 `T-VN-41C`·`T-VN-41F1D-D2` 완료 기록과 Manager journal/manifest를 교차 대조한다.

## BACKUP-FOLLOWUP — standalone backup 운영 보강

Issue #177의 create/list/gc, cron, API/UI 구현과 n150 Geo Dagster·Concierge·PinVi 실증은 완료됐다.
남은 항목은 pair rebuild와 독립한다.

- [ ] off-box 사본 자동화와 보존 정책을 별도 설계한다.

Geo application DB의 첫 standalone 대용량 backup 실측 완료 이력은
[`docs/tasks-done.md`](tasks-done.md)의 `BACKUP-FOLLOWUP-GEO-INITIAL`에 보관한다.
