# TASKS — 활성 작업

이 문서는 완료되지 않은 작업만 순서대로 한 줄씩 나열한다. lane, 담당자 구분,
계층형 하위 작업과 완료 이력은 두지 않는다. 완료 이력은
[`docs/tasks-done.md`](tasks-done.md), 실행 근거와 현재 상태는
[`docs/journal.md`](journal.md)가 정본이다.

- [/] M05 execution identity v6 — v5 Map·PinVi source pinset은 보존하고 trusted Manager revision을 포함한 v6 execution identity를 registry·`ktdctl`·one-shot ledger·terminal block·public generation binding에 연결하며, M05 provenance preflight와 모든 mutation을 봉인하는 durable `rotate-pair` recovery가 partial/stale binding 없이 새 pair를 만든다.
- [/] M05 sibling contract — PinVi isolated admission/activation과 Map attestation이 Map SHA·PinVi SHA·v5 pinset·Manager SHA·v6 execution identity를 exact 대조하도록 함께 이행한다.
- [/] M05 terminal forensic — every terminal one-shot의 raw E2E output·HTTP·container·환경·private receipt를 완주 전까지 gitignored local analysis에 상세 기록하고, tracked 문서·commit·push에는 넣지 않는다.
- [ ] M05 activation — 일반 host-loopback readiness 정책과 ledger 전 rendered Compose publish preflight를 적용한 새 v6 execution candidate에서 `ktdctl` atomic binding, 단발 rebuild, public execution binding, isolated M04/M05 live E2E, activation attestation을 각각 정확히 한 번 통과한다.
- [/] Map/PinVi cross-repo live consumer acceptance — WebSocket/mutating loop·consumer reconciliation과 Manager manifest/journal을 실제 pair에서 교차 대조한다.
- [/] standalone backup 운영 보강 — off-box 사본 자동화와 보존 정책을 완료한다.
- [/] ktdctl UI migration — public generation 관측과 남은 M5~M7 UI 이관을, root CLI authority를 유지한 채 완료한다.
- [/] journal/attestation drift — Manager generation receipt와 Map attestation의 execution binding field를 함께 정렬한다.
- [ ] non-root backend — root ownership을 유지하면서 service-group 접근 경계와 root/서비스 계정 mutation 검증을 완료한다.
