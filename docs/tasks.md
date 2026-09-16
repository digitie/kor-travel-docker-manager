# TASKS — 활성 작업

이 문서는 완료되지 않은 작업만 순서대로 한 줄씩 나열한다. lane, 담당자 구분,
계층형 하위 작업과 완료 이력은 두지 않는다. 완료 이력은
[`docs/tasks-done.md`](tasks-done.md), 실행 근거와 현재 상태는
[`docs/journal.md`](journal.md)가 정본이다.

- [/] M05 execution identity v6 — v5 Map·PinVi source pinset은 보존하고 trusted Manager revision을 포함한 v6 execution identity를 registry·`ktdctl`·one-shot ledger·terminal block·public generation binding에 연결하며, M05 provenance preflight와 모든 mutation을 봉인하는 durable `rotate-pair` recovery가 partial/stale binding 없이 새 pair를 만든다.
- [/] M05 sibling contract — PinVi isolated admission/activation과 Map attestation이 Map SHA·PinVi SHA·v5 pinset·Manager SHA·v6 execution identity를 exact 대조하도록 함께 이행한다.
- [/] M05 terminal forensic — every terminal one-shot의 raw E2E output·HTTP·container·환경·private receipt를 완주 전까지 gitignored local analysis에 상세 기록하고, tracked 문서·commit·push에는 넣지 않는다.
- [ ] M05 activation — 일반 host-loopback readiness 정책과 ledger 전 rendered Compose publish preflight를 적용한 새 v6 execution candidate에서 `ktdctl` atomic binding, 단발 rebuild, public execution binding, isolated M04/M05 live E2E, activation attestation을 각각 정확히 한 번 통과한다. **2026-09-03 실측**: pinset `e6b52db4`(Map `8078b110` + PinVi `357da189`), Manager `5befecbb`에서 rotate-pair → 단발 rebuild → rebind-execution → isolated M04/M05 live E2E가 `status: passed`로 닫혔다(`m04_attestation_sha256=d5f0c4d0…`, `m05_attestation_sha256=69fb285e…`, `runtime_provenance_sha256=bac562a0…`, `cleanup_failed=false`). 남은 판정은 소유자 몫이다.
- [/] Map/PinVi cross-repo live consumer acceptance — WebSocket/mutating loop·consumer reconciliation과 Manager manifest/journal을 실제 pair에서 교차 대조한다.
- [/] standalone backup 운영 보강 — off-box 사본 자동화와 보존 정책을 완료한다.
- [/] ktdctl UI migration — public generation 관측과 남은 M5~M7 UI 이관을, root CLI authority를 유지한 채 완료한다.
- [/] journal/attestation drift — Manager generation receipt와 Map attestation의 execution binding field를 함께 정렬한다.
- [ ] non-root backend — root ownership을 유지하면서 service-group 접근 경계와 root/서비스 계정 mutation 검증을 완료한다.
- [ ] atomic-write 프리미티브 잔여 통합 (GM-10 후속) — mkstemp 9곳 중 `standalone_backup.py` 1곳만 정본으로 이관됐고 나머지 8곳은 각각 정본 시그니처와 맞지 않는 이유가 있다(TOCTOU 재검사·strict 디렉터리 fsync 계약·hardlink 발행·`recovery_succeeded` 신호원). `runtime_pin_request.py`는 대상이 죽은 코드라 후속은 이관이 아니라 `replace_existing` 플래그 제거다. 조사 기록: `docs/journal.md` 2026-09-03 「tasks.md에서 이관한 조사 기록」.
- [ ] LoginScreen.tsx를 `humanizeError`/`CODE_MESSAGES`로 옮긴다 — 다만 `require_frontend_origin`(403 `INVALID_ORIGIN`)이 bare 문자열이고 `CODE_MESSAGES`에도 없어, 먼저 그것을 봉투화하고 코드 매핑을 추가하지 않으면 원문 토큰이 화면에 노출되는 새 회귀가 생긴다. 조사 기록: `docs/journal.md` 2026-09-03 「tasks.md에서 이관한 조사 기록」.
- [x] GM-17 선행조건 — targets 문서의 자리를 trusted 설치본에 고정하고 무결성을 검증한다 (2026-09-17 완료). `TARGETS_FILE`/`PROJECT_ROOT` env redirect를 설치본에서 거절하고, 읽기를 `O_NOFOLLOW` + `fstat` 기반 검증 descriptor로 바꿔 root 소유·nlink 1·group/other 비쓰기를 강제한다(개발 checkout은 종전 그대로). 감사 노트 (b)가 "그대로 옮기면 보안 회귀"라고 지목한 그 자리다 — **이것이 초록이어야 아래 allowlist 이관이 가능하다.** 변이 셋으로 결박 확인.
- [x] GM-17 본작업 A — bind allowlist 외부화 (2026-09-17 완료). `_CANDIDATE_ALLOWED_OPERATOR_BINDS`(125줄 dict 리터럴)를 `config/docker-targets.yml`의 `compose_binds:` 절로 옮기고 코드에는 검증 규칙만 남겼다. 새 bind 하나에 backend 수정 + trusted release 재설치가 필요하던 병목이 config 작업이 됐다. **값은 한 글자도 바꾸지 않았다** — 옮기기 전후 해석된 매핑 29건이 정확히 같다(sha256 `e7ec261c30db1d04`). 변이(항목 하나 제거)로 3건이 빨개지고 그중 하나가 실제 배포 검증 경로라, 설정이 진짜 소비된다는 것까지 확인했다.
- [ ] GM-17 후속 — 개인 경로 기본값 제거(`${VAR:-/home/digitie/...}` → `${VAR:?}`). **A에서 의도적으로 제외했다.** 감사 노트 실측: 로컬 dev와 n150 prod 둘 다 지금 그 하드코딩된 기본값에만 의존해 돌고 있어서, `.env`를 먼저 갱신하지 않고 필수화하면 다음 `compose up`/재시작이 즉시 깨진다 — "안전한 정리"가 아니라 운영 중단 변경이다. `.env` 갱신과 **조율**해서만 한다.
- [ ] GM-17 후속 — bind 값 정책 강화. A는 구조만 검증한다(필수/미지 필드, 절대경로, 진짜 bool, 중복 키). 값의 정책(manager 경로 노출 금지 등)은 넣지 않았다 — `rustfs-init`이 실제로 manager 설치 경로를 container target으로 쓰므로 순진한 규칙은 지금 유효한 항목을 거부한다. 규칙을 세우려면 그 예외를 먼저 분류해야 한다.
- [ ] GM-17 본작업 B — required-set 완화. dev ensure에서 15개 서비스 존재 강제를 존재-조건부로 바꾼다(frozenset 14 + `_PINVI_DB_INIT_SERVICE` 별도 강제 = 실질 15). 감사 노트 (d)대로 production ensure는 이미 원천 거부(`compose_service.py:4663-4676`)라 "production 모드로 한정"은 ensure에 한해 공허하고, 실제 적용 대상은 pinned-rebuild와 production save/mutation 경로다. **다수 cross-service validator를 존재-조건부로 바꾸는 광범위 감사가 필요하고 effort L, 그 이하로 축소 불가**(노트 원문). 문서 전역 보호 이름/값 스캔은 무조건 유지한다. **착수 전 오너와 범위를 재확인할 것.**
