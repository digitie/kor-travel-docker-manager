# TASKS — 진행 중 백로그

이 문서는 진행 중이거나 아직 시작하지 않은 작업만 관리한다. 완료·퇴역·대체된 작업은
[`docs/tasks-done.md`](tasks-done.md)에 기록한다. 코드와 맞지 않는 과거 실행 절차는 active task로
남기지 않는다.

- 진행 중: `[/]`
- 미진행: `[ ]`

## 작업 현황

| 태스크 ID | 작업 항목 | 상태 | 다음 완료 조건 |
|:---|:---|:---:|:---|
| **T-VN-41-F1D-H300** | Map application fresh `300` paired candidate와 Manager destructive rebuild 완결 | `[/]` | n150 trusted install·`rebuild-pinned --confirm`, live UI/PinVi acceptance, 문서 정리 |
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

- Map PR #1066 exact head: `cc81081ff2e540a6ad9c428a296515e1d79bc316` (merge 완료; merge commit `14d18230…`)
- Manager PR #197: `7017a5d2d7192018ddf0667623c4cc18b290c46b` (merge 완료)
- Manager PR #198: Map role-bootstrap helper bind의 C6c 오탐 보완 (merge commit `19409e3f…`)
- Manager PR #200: Map `cc81081f…` release pin 회전 (merge commit `01b51b32…`)
- Manager PR #201: Map source environment contract 보완 (merge commit `86018450…`)
- Manager PR #202: Map Dagster static inspection launch contract 보완 (merge commit `e582e924…`)
- Manager PR #203: Map role-bootstrap password `--env` 전달 보완 (draft; pending exact head)
- release pinset: `14a9a512836a48489146dc2bb0a04de309cf451b274b934d79805d171f83a193`
- n150 재개에서 Map role-bootstrap의 required password 전달 누락이 확인됐다. PR #203 merge 후
  trusted install을 갱신하고 frozen Compose source hash를 유지한 같은 durable journal을 재개한다.
- durable rebuild journal이 없는 pre-journal receipt는 다음 실행에서 `--verify` 입력으로 사용하지 않는다.
  Manager는 정확한 두 receipt를 안전하게 폐기한 뒤 sealed builder를 fresh build mode로 호출한다. journal이
  있는 crash resume에서만 두 receipt를 `--verify`로 재검증하며, 현재 receipt·Map image/config 증거가
  journal candidate와 정확히 일치할 때만 resume을 계속한다.
- 로컬 sealed paired build: 새 Map pin `cc81081f…` 기준 PR #202 merge 뒤 paired image·receipt를 새로
  만들었고, 현재 durable journal resume의 고정 evidence로 사용한다. 이전 후보의 image·receipt는 release
  evidence로 재사용하지 않으며, 새 로컬 artifact도 n150 production 증거가 아니다.

남은 작업:

- [x] Manager PR #200을 ready 상태로 전환하고 required CI·전문 리뷰 green 후 merge했다.
- [x] n150 rebuild를 막은 Map source environment contract 보완 PR #201을 전문 적대 리뷰 2건과 함께
  통과·merge하고 trusted Manager 설치본을 갱신했다.
- [x] Map Dagster static inspection launch contract 보완 PR #202를 전문 적대 리뷰 2건과 함께
  통과·merge하고 trusted Manager 설치본을 갱신했다.
- [x] PR #202 merge 뒤 새 Manager release로 새 Map `cc81081f…` 기준 paired image·receipt를 생성했다.
  이전 pin의 image·receipt·journal은 재사용하지 않았다.
- [ ] Manager PR #203을 전문 적대 리뷰 2건과 함께 통과·merge하고 trusted Manager 설치본을 갱신한다.
- [ ] n150에서 trusted Manager release를 설치하고 approved root command
  `ktdctl pinvi-pair rebuild-pinned --confirm`을 실행한다. backup·scratch restore·이전 DB 복원은 실행하지
  않는다.
- [ ] n150에서 Map application `300`, Map Dagster candidate head, PinVi head, 세 DB identity, seven-service
  exact running image와 committed manifest를 확인한다.
- [ ] 공개 Manager UI와 Map UI에서 실제 브라우저 login→protected view→logout→재차단을 확인하고,
  PinVi data-independent acceptance 및 WebSocket 재연결 loop 부재를 확인한다.
- [ ] live evidence를 `journal.md`와 `tasks-done.md`에 기록한다.

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
