# TASKS — 진행 중 백로그

이 문서는 진행 중이거나 아직 시작하지 않은 작업만 관리한다. 완료·퇴역·대체된 작업은
[`docs/tasks-done.md`](tasks-done.md)에 기록한다. 코드와 맞지 않는 과거 실행 절차는 active task로
남기지 않는다.

- 진행 중: `[/]`
- 미진행: `[ ]`

## 작업 현황

| 태스크 ID | 작업 항목 | 상태 | 다음 완료 조건 |
|:---|:---|:---:|:---|
| **T-C7-CAPTURE-OPS** | 읽기 전용 `pinvi-pair capture` 운영 프로비저닝 정리 | `[/]` | n150 trusted Manager 설치본의 read-only contract 확인, checkout env와 manifest 정본 하나를 확정하고 최초 capture/attestation 기록 |
| **MAP-LIVE-FOLLOWUP** | Map/PinVi cross-repo live consumer acceptance 후속 | `[/]` | logout→재차단, PinVi WebSocket/mutating loop, consumer reconciliation을 실제 pair에서 실행하고 Map 저장소 정본 task와 결과를 교차 기록 |
| **BACKUP-FOLLOWUP** | 독립 standalone backup의 남은 운영 보강 | `[ ]` | Geo application 첫 대용량 backup 실측과 off-box 사본 자동화. Alembic downgrade/이전 revision restore는 범위 밖 |

## 공통 진행 규율

1. 작업 시작과 PR merge 직전에 `origin/main`을 fetch하고 필요하면 rebase한다.
2. 검증 가능한 작은 단위로 커밋하고 remote PR branch에 자주 push한다.
3. 코드 PR은 서로 다른 관점의 전문 적대 리뷰 2건을 통과해야 한다.
4. push 전에 staged file·일반 비밀·프로젝트별 민감 문자열 감사를 실행한다.
5. 완료된 항목은 이 파일에 `[x]`로 쌓지 않고 즉시 `tasks-done.md`로 옮긴다.
6. n150 배포와 Playwright live E2E는 `docs/deploy-runbook.local.md`를 정본으로 수행한다.

## T-C7-CAPTURE-OPS — 읽기 전용 capture 운영 정리

코드 구현은 PR #184에서 merge됐다. 남은 것은 n150 운영 프로비저닝뿐이며 destructive rebuild와 섞지 않는다.

- [ ] Map·PinVi clean checkout env와 C7 manifest 정본 경로를 하나로 확정한 뒤, trusted 설치본에서
  `capture_contract=pair-capture-v1` read-only capture를 실행한다. n150 `--help` 계약 사전 확인은
  `journal.md`에 기록했다.
- [ ] 최초 read-only capture 뒤 C7 runner attestation을 다시 만들고 결과를 기록한다.

## MAP-LIVE-FOLLOWUP — Map/PinVi cross-repo live consumer acceptance

H300의 이번 수락은 schema/bootstrap·runtime provenance·login setup/protected view와
data-independent UI 11개에 한정했다. logout 후 재차단, PinVi WebSocket/mutating loop,
consumer reconciliation은 별도 운영 acceptance로 남긴다. 구현·실행 정본은
[Map 저장소 `docs/tasks.md`](https://github.com/digitie/kor-travel-map/blob/main/docs/tasks.md)의
`T-VN-41C`·`T-VN-41F1D-D2`이며, 이 Manager task는 cross-repo 결과와 exact pair를 함께 기록한다.

- [x] Map UI의 현재 socket close와 `/login` redirect, ticket/lease WebSocket wire를 실제 브라우저로
  확인했다.
- [x] Map UI에서 logout 뒤 `/ops/datasets` protected route 재진입이 `/login`으로 재차단되는지
  확인했다. PinVi 쪽 equivalent reblock은 아직 남는다.
- [x] PinVi exact pair에서 logout 뒤 `/admin/features` protected route 재진입이 `/admin/login`으로
  재차단되는지 확인했다. 이 검증은 `/auth/logout` 204를 확인하고 application row를 쓰지 않았다.
- [ ] PinVi WebSocket/mutating loop와 consumer reconciliation의 성공·실패 증거를 기록한다.
- [ ] Map 저장소 `T-VN-41C`·`T-VN-41F1D-D2` 완료 기록과 Manager journal/manifest를 교차 대조한다.

## BACKUP-FOLLOWUP — standalone backup 운영 보강

Issue #177의 create/list/gc, cron, API/UI 구현과 n150 Geo Dagster·Concierge·PinVi 실증은 완료됐다.
남은 항목은 pair rebuild와 독립한다.

- [ ] Geo application DB의 첫 standalone 대용량 backup을 디스크 여유·소요시간과 함께 실측한다.
- [ ] off-box 사본 자동화와 보존 정책을 별도 설계한다.
