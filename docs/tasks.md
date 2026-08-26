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
- [/] 원인은 M05가 요구하는 role topology와 #477의 root 단일 DSN 배선 불일치로 분리됐다. PinVi
  #488 merge commit `93296aee5d47676e6b9b79303bf417c598a273ac`은 Manager가 허용하는 exact loopback
  endpoint에서만 role bootstrap을 실행하도록 보정했다. Manager 후보는 root·runtime·schema owner·migration
  owner·migrator identity를 분리하고 open → bootstrap → seal lifecycle을 강제한다. Manager release pin은 #488
  commit과 pinset `9073c294d6138fff895983adbc9ca483ab2eede6da15bb1ef4888572fe7fe491`으로 회전했다. Manager
  PR의 적대 리뷰·CI와 n150의 새 role credential 구성이 모두 완료되기 전에는 rebuild를 재개하지 않는다.
- [x] n150 candidate가 빈 artifact path 설정 때문에 DB reset 전에 fail-close한 것을 확인했다. 기본
  preflight는 현재 pinset state root에서 네 fence/permit mount directory를 도출해 사용하도록 Manager #222로
  보정·병합·배포했다. 실제 빈 `.env`와 Compose resolution 회귀를 포함한 587개 backend 테스트, 전문 적대 리뷰
  2건을 통과했다.
- [/] Manager #223을 trusted `/opt` release로 배포한 뒤 root-only retirement는 project-root override를
  blanket 거부해 mutation 전 fail-close했다. home checkout의 Compose file은 user-writable이므로 그 거부만
  풀거나 home root를 Compose 입력으로 허용하면 P0가 된다. 후속 Manager PR은 `/opt`를 canonical execution
  root로 유지하고, root-owned `0600` final legacy override와 고정 sibling Concierge `.env`만
  `compose-boundary stage-legacy-override --source <absolute-path> --confirm`으로 protected C6c state에
  descriptor-safe snapshot한다. retire는 이 staged pair만 raw allowlist·backend key/API key-set membership·
  production/authentication API guard·host network·API/UI production command/port·atomic root `.env`·실제
  raw/resolved C6c config를 통과할 때 같은 state filesystem 안에서 archive하고, 해당 deployment lock 안에서
  Concierge API/MCP/scheduler/UI를 재생성한다. 실제 n150은 `rehearsal/rebuildable` pinned rebuild mode이므로
  stage/retire는 그 exact mode·PinVi production·Map principal-required contract와 pinned-runtime rebuild host
  lease를 공유하며, production으로 수동 전환하지 않는다. production mode에서는 fixed C6c global mutation
  lock을 사용한다. archive durability 불확실성은 candidate `.env`를 되돌리지 않는 typed
  failure로 남긴다. archive 뒤 재생성만 실패하면 `compose-boundary activate-concierge --confirm`으로 같은 계약을
  재검증한 뒤 재시도한다. home source는 rename/delete/Compose 실행 대상이 아니며, 그 전에는 override를 수동
  삭제하거나 rebuild를 재시도하지 않는다.
- [/] #224 merged trusted release를 n150에 공식 installer로 반영했고 source 권한 precondition도 정렬했다.
  공식 stage는 Docker/DB/runtime/root `.env` mutation 전에, 실제 legacy Compose가 사용하는 장형 `env_file`
  mapping을 구형 문자열 allowlist가 거부하는 P1을 확인했다. 후속 보정은 override 위치에서 계산한 exact sibling
  `.env`와 boolean `required: true` 한 항목만 수용하며, home Compose 실행·source 변환·수동 삭제를 추가하지 않는다.
- [ ] PinVi WebSocket/mutating loop와 consumer reconciliation의 성공·실패 증거를 기록한다.
- [ ] Map 저장소 `T-VN-41C`·`T-VN-41F1D-D2` 완료 기록과 Manager journal/manifest를 교차 대조한다.

## BACKUP-FOLLOWUP — standalone backup 운영 보강

Issue #177의 create/list/gc, cron, API/UI 구현과 n150 Geo Dagster·Concierge·PinVi 실증은 완료됐다.
남은 항목은 pair rebuild와 독립한다.

- [ ] off-box 사본 자동화와 보존 정책을 별도 설계한다.

Geo application DB의 첫 standalone 대용량 backup 실측 완료 이력은
[`docs/tasks-done.md`](tasks-done.md)의 `BACKUP-FOLLOWUP-GEO-INITIAL`에 보관한다.
