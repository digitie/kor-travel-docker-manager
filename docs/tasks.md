# TASKS — 활성 작업

이 문서는 완료되지 않은 작업만 순서대로 한 줄씩 나열한다. lane, 담당자 구분,
계층형 하위 작업과 완료 이력은 두지 않는다. 완료 이력은
[`docs/tasks-done.md`](tasks-done.md), 실행 근거와 현재 상태는
[`docs/journal.md`](journal.md)가 정본이다.

- [/] M05 execution identity v6 — v5 Map·PinVi source pinset은 보존하고 trusted Manager revision을 포함한 v6 execution identity를 registry·`ktdctl`·one-shot ledger·terminal block·public generation binding에 연결하며, M05 provenance preflight와 모든 mutation을 봉인하는 durable `rotate-pair` recovery가 partial/stale binding 없이 새 pair를 만든다.
- [/] M05 sibling contract — PinVi isolated admission/activation과 Map attestation이 Map SHA·PinVi SHA·v5 pinset·Manager SHA·v6 execution identity를 exact 대조하도록 함께 이행한다.
- [/] M05 terminal forensic — every terminal one-shot의 raw E2E output·HTTP·container·환경·private receipt를 완주 전까지 gitignored local analysis에 상세 기록하고, tracked 문서·commit·push에는 넣지 않는다.
- [ ] 격리 하네스가 불변 핀 소스 트리를 오염시키지 않게 한다 — `m05_isolated_e2e.py`가 `cwd=pinvi_root`로 npm/playwright를 돌려 `node_modules`·`apps/web/node_modules`·`playwright-report`·`test-results`를 0555 worktree 안에 쓴다(root라 mode를 무시한다). `_validate_immutable_tree`가 다음 preflight에서 정당하게 거부하므로 **같은 pinset 재실행이 불가능해진다** — pinset마다 worktree가 새로 생겨 2026-09-03까지 드러나지 않았다. 해제 조건: (1) e2e 실행 후에도 핀 worktree가 불변 검사를 통과한다, (2) 러너는 격리 복사본이나 읽기 전용 마운트에서 돌고 산출물은 output leaf로 간다, (3) 같은 pinset에서 rebind 후 재실행이 수동 정리 없이 성공한다, (4) 회귀 게이트가 오염을 만들어 실제로 검출되는 것을 보인다.
- [ ] M05 activation — 일반 host-loopback readiness 정책과 ledger 전 rendered Compose publish preflight를 적용한 새 v6 execution candidate에서 `ktdctl` atomic binding, 단발 rebuild, public execution binding, isolated M04/M05 live E2E, activation attestation을 각각 정확히 한 번 통과한다. **2026-09-03 실측**: pinset `e6b52db4`(Map `8078b110` + PinVi `357da189`), Manager `5befecbb`에서 rotate-pair → 단발 rebuild → rebind-execution → isolated M04/M05 live E2E가 `status: passed`로 닫혔다(`m04_attestation_sha256=d5f0c4d0…`, `m05_attestation_sha256=69fb285e…`, `runtime_provenance_sha256=bac562a0…`, `cleanup_failed=false`). 남은 판정은 소유자 몫이다.
- [/] Map/PinVi cross-repo live consumer acceptance — WebSocket/mutating loop·consumer reconciliation과 Manager manifest/journal을 실제 pair에서 교차 대조한다.
- [/] standalone backup 운영 보강 — off-box 사본 자동화와 보존 정책을 완료한다.
- [/] ktdctl UI migration — public generation 관측과 남은 M5~M7 UI 이관을, root CLI authority를 유지한 채 완료한다.
- [/] journal/attestation drift — Manager generation receipt와 Map attestation의 execution binding field를 함께 정렬한다.
- [ ] non-root backend — root ownership을 유지하면서 service-group 접근 경계와 root/서비스 계정 mutation 검증을 완료한다.
- [ ] atomic-write 프리미티브 잔여 통합 (GM-10 후속) — mkstemp 9곳 중 `standalone_backup.py` 1곳만 정본으로 이관됐고 나머지 8곳은 각각 정본 시그니처와 맞지 않는 이유가 있다(TOCTOU 재검사·strict 디렉터리 fsync 계약·hardlink 발행·`recovery_succeeded` 신호원). `runtime_pin_request.py`는 대상이 죽은 코드라 후속은 이관이 아니라 `replace_existing` 플래그 제거다. 조사 기록: `docs/journal.md` 2026-09-03 「tasks.md에서 이관한 조사 기록」.
- [ ] LoginScreen.tsx를 `humanizeError`/`CODE_MESSAGES`로 옮긴다 — 다만 `require_frontend_origin`(403 `INVALID_ORIGIN`)이 bare 문자열이고 `CODE_MESSAGES`에도 없어, 먼저 그것을 봉투화하고 코드 매핑을 추가하지 않으면 원문 토큰이 화면에 노출되는 새 회귀가 생긴다. 조사 기록: `docs/journal.md` 2026-09-03 「tasks.md에서 이관한 조사 기록」.
- [ ] GM-17 본작업 — production compose candidate의 required-set 완화와 bind allowlist 외부화. **착수 전 오너와 범위를 재확인할 것** — production 보안 경계를 직접 건드리고, allowlist 이동은 root-owned 설정 파일의 소유권·권한 검증 인프라를 먼저 요구한다. 조사 기록: `docs/journal.md` 2026-09-03 「tasks.md에서 이관한 조사 기록」.
