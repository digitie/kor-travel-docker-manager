# TASKS — 진행 중 백로그

이 문서는 진행 중이거나 아직 시작하지 않은 작업만 관리한다. 완료·퇴역·대체된 작업은
[`docs/tasks-done.md`](tasks-done.md)에 기록한다. 코드와 맞지 않는 과거 실행 절차는 active task로
남기지 않는다.

- 진행 중: `[/]`
- 미진행: `[ ]`

## 작업 현황

| 태스크 ID | 작업 항목 | 상태 | 다음 완료 조건 |
|:---|:---|:---:|:---|
| **T-VN-41-F1D-H300** | Map application fresh `300` paired candidate와 Manager destructive rebuild 완결 | `[/]` | 두 전문 적대 리뷰 GO, Map PR #1064 CI green·merge, Manager PR #197 rebase, n150 trusted install·`rebuild-pinned --confirm`, live UI/PinVi acceptance, 문서·PR merge |
| **T-C7-CAPTURE-OPS** | 읽기 전용 `pinvi-pair capture` 운영 프로비저닝 정리 | `[/]` | n150 trusted Manager 설치본의 read-only contract 확인, checkout env와 manifest 정본 하나를 확정하고 최초 capture/attestation 기록 |
| **BACKUP-FOLLOWUP** | 독립 standalone backup의 남은 운영 보강 | `[ ]` | Geo application 첫 대용량 backup 실측과 off-box 사본 자동화. Alembic downgrade/이전 revision restore는 범위 밖 |

## 공통 진행 규율

1. 작업 시작과 PR merge 직전에 `origin/main`을 fetch하고 필요하면 rebase한다.
2. 검증 가능한 작은 단위로 커밋하고 remote PR branch에 자주 push한다.
3. 코드 PR은 서로 다른 관점의 전문 적대 리뷰 2건을 통과해야 한다.
4. push 전에 staged file·일반 비밀·프로젝트별 민감 문자열 감사를 실행한다.
5. 완료된 항목은 이 파일에 `[x]`로 쌓지 않고 즉시 `tasks-done.md`로 옮긴다.
6. n150 배포와 Playwright live E2E는 `docs/deploy-runbook.local.md`를 정본으로 수행한다.

## T-VN-41-F1D-H300 — application `300` 완결

Map은 in-place upgrade와 이전 revision 복구를 사용하지 않는다. exact Map release commit의 sealed paired
builder가 API·Dagster image와 application contract를 만들고, Manager는 그 receipt를 runtime generation의
유일한 Map API/Dagster authority로 사용한다. 정본 설계는
[`docs/tvn41-f1d-destructive-rebootstrap.md`](tvn41-f1d-destructive-rebootstrap.md)와 ADR-39다.

현재 원격 후보:

- Map PR #1064: `dd2ee61fdb1d0cedb0d7cb3526c804a3dfc5404e`
- Manager PR #197: root typed probe 소비·committed fast-path hardening checkpoint 진행 중
- release pinset: `49548a610cbfa3a0d2242ef6e9a8cbd5664e61dec92391b8a476b02951b65c62`
- 로컬 sealed paired build: 새 Map pin `dd2ee61f…` 기준 재생성 대기. 이전 pin의 image·receipt는
  release evidence로 재사용하지 않으며, 새 로컬 artifact도 n150 production 증거가 아니다.

남은 작업:

- [/] DB crash/resume/identity/fence/permit 관점과 Compose/provenance/security 관점의 독립 전문 리뷰
  1차 finding을 반영했다. 새 Map/Manager exact commit에서 P0/P1=0을 확인한다.
- [ ] Map PR #1064의 Python 3.11/3.12/3.13, lint, type/frontend, OpenAPI/fixture CI를 모두 green으로
  만든 뒤 merge한다.
- [ ] Map merge 뒤 Manager PR #197을 최신 `main`에 rebase하고 Map exact commit·pinset·paired candidate
  연속성을 다시 검증한다.
- [ ] Manager backend 전체 pytest, 변경 파일 Ruff·strict mypy, Compose 계약, frontend type-check/build를
  통과하고 draft PR 본문을 manifest v6/journal v8/application-300 기준으로 갱신한다.
- [ ] n150에서 trusted Manager release를 설치하고 approved root command
  `ktdctl pinvi-pair rebuild-pinned --confirm`을 실행한다. backup·scratch restore·이전 DB 복원은 실행하지
  않는다.
- [ ] n150에서 Map application `300`, Map Dagster candidate head, PinVi head, 세 DB identity, seven-service
  exact running image와 committed manifest를 확인한다.
- [ ] 공개 Manager UI와 Map UI에서 실제 브라우저 login→protected view→logout→재차단을 확인하고,
  PinVi data-independent acceptance 및 WebSocket 재연결 loop 부재를 확인한다.
- [ ] live evidence를 `journal.md`와 `tasks-done.md`에 기록하고 Map/Manager PR을 CI green 상태로 merge한다.

## T-C7-CAPTURE-OPS — 읽기 전용 capture 운영 정리

코드 구현은 PR #184에서 merge됐다. 남은 것은 n150 운영 프로비저닝뿐이며 destructive rebuild와 섞지 않는다.

- [ ] trusted 설치본의 `pinvi-pair capture --help`가 `capture_contract=pair-capture-v1`을 보고하는지 mutation
  없이 확인한다.
- [ ] Map·PinVi clean checkout env와 C7 manifest 정본 경로를 하나로 확정한다.
- [ ] 최초 read-only capture 뒤 C7 runner attestation을 다시 만들고 결과를 기록한다.

## BACKUP-FOLLOWUP — standalone backup 운영 보강

Issue #177의 create/list/gc, cron, API/UI 구현과 n150 Geo Dagster·Concierge·PinVi 실증은 완료됐다.
남은 항목은 pair rebuild와 독립한다.

- [ ] Geo application DB의 첫 standalone 대용량 backup을 디스크 여유·소요시간과 함께 실측한다.
- [ ] off-box 사본 자동화와 보존 정책을 별도 설계한다.
