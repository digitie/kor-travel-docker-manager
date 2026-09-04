# TASKS — 활성 작업

이 문서는 완료되지 않은 작업만 순서대로 한 줄씩 나열한다. lane, 담당자 구분,
계층형 하위 작업과 완료 이력은 두지 않는다. 완료 이력은
[`docs/tasks-done.md`](tasks-done.md), 실행 근거와 현재 상태는
[`docs/journal.md`](journal.md)가 정본이다.

- [/] M05 execution identity v6 — v5 Map·PinVi source pinset은 보존하고 trusted Manager revision을 포함한 v6 execution identity를 registry·`ktdctl`·one-shot ledger·terminal block·public generation binding에 연결하며, M05 provenance preflight와 모든 mutation을 봉인하는 durable `rotate-pair` recovery가 partial/stale binding 없이 새 pair를 만든다.
- [/] M05 sibling contract — PinVi isolated admission/activation과 Map attestation이 Map SHA·PinVi SHA·v5 pinset·Manager SHA·v6 execution identity를 exact 대조하도록 함께 이행한다.
- [/] M05 terminal forensic — every terminal one-shot의 raw E2E output·HTTP·container·환경·private receipt를 완주 전까지 gitignored local analysis에 상세 기록하고, tracked 문서·commit·push에는 넣지 않는다.
- [/] 격리 하네스가 불변 핀 소스 트리를 오염시키지 않게 한다 — 러너가 `-v "$repo_root:/work"`로 핀 worktree를 **root RW**로 노출해 `apps/web/node_modules`(실제 내용)와 마운트포인트 3개가 0755로 남고, 다음 preflight가 정당하게 거부해 **같은 pinset 재실행이 불가능해진다**(2026-09-03·04 연속 재현). B안(일회용 git worktree — object store에서 재유도)으로 구현했고 적대 리뷰 2인의 지적(중단 시 남는 admin 등록, 무커버리지 테스트, 제거 실패가 통과 실행을 태우는 문제, 자기참조 tree 검사, 사후조건 과대주장)을 반영했다. 운영 선행조치도 실행했다 — 활성 pinset `e6b52db4`의 봉인 PinVi 트리에서 잔여물 4개를 제거했고 `_validate_immutable_tree`가 pinvi/map 둘 다 ACCEPT다(2026-09-04 실측). 남은 것은 이 수정을 얹은 첫 격리 e2e 실행이다.
- [ ] M05 activation — 일반 host-loopback readiness 정책과 ledger 전 rendered Compose publish preflight를 적용한 새 v6 execution candidate에서 `ktdctl` atomic binding, 단발 rebuild, public execution binding, isolated M04/M05 live E2E, activation attestation을 각각 정확히 한 번 통과한다. **2026-09-03 실측**: pinset `e6b52db4`(Map `8078b110` + PinVi `357da189`), Manager `5befecbb`에서 rotate-pair → 단발 rebuild → rebind-execution → isolated M04/M05 live E2E가 `status: passed`로 닫혔다(`m04_attestation_sha256=d5f0c4d0…`, `m05_attestation_sha256=69fb285e…`, `runtime_provenance_sha256=bac562a0…`, `cleanup_failed=false`). 남은 판정은 소유자 몫이다.
- [/] Map/PinVi cross-repo live consumer acceptance — WebSocket/mutating loop·consumer reconciliation과 Manager manifest/journal을 실제 pair에서 교차 대조한다.
- [/] standalone backup 운영 보강 — off-box 사본 자동화와 보존 정책을 완료한다.
- [/] ktdctl UI migration — public generation 관측과 남은 M5~M7 UI 이관을, root CLI authority를 유지한 채 완료한다.
- [/] journal/attestation drift — Manager generation receipt와 Map attestation의 execution binding field를 함께 정렬한다.
- [ ] Manager의 git 기능을 pygit2로 포팅한다 — 현재 git 접근은 전부 `subprocess` + `/usr/bin/git`이다(`pinned_runtime_sources.py` 18곳의 `_run_root_git`, `source_status.py`, `scripts/m05_isolated_e2e.py`). 그 결과 (a) 실패 사유가 exit code로 접혀 `"Git operation failed"` 한 문장으로 뭉개지고 — 2026-09-04 적대 리뷰가 "missing but already registered"를 코드로 구분하지 못해 경로 존재 여부로 우회 판정해야 했다 —, (b) `worktree list --porcelain` 같은 출력을 문자열로 재파싱해야 하며, (c) 검사와 실행 사이에 프로세스 경계가 있어 TOCTOU 창이 남는다. pygit2는 libgit2 바인딩이라 오류를 타입으로 받고 object/ref를 직접 읽는다. **범위 주의**: worktree add/move/remove는 libgit2 지원이 부분적이므로 무엇이 포팅 가능한지 먼저 실측하고, root 전용 환경 봉인(`GIT_CONFIG_NOSYSTEM`·`protocol.*.allow=never`·`credential.helper=`)과 동등한 보장을 pygit2 설정으로 재현할 수 있는지 확인한 뒤 착수한다. 현재 venv에 미설치.
- [ ] non-root backend — root ownership을 유지하면서 service-group 접근 경계와 root/서비스 계정 mutation 검증을 완료한다.
- [ ] atomic-write 프리미티브 잔여 통합 (GM-10 후속) — mkstemp 9곳 중 `standalone_backup.py` 1곳만 정본으로 이관됐고 나머지 8곳은 각각 정본 시그니처와 맞지 않는 이유가 있다(TOCTOU 재검사·strict 디렉터리 fsync 계약·hardlink 발행·`recovery_succeeded` 신호원). `runtime_pin_request.py`는 대상이 죽은 코드라 후속은 이관이 아니라 `replace_existing` 플래그 제거다. 조사 기록: `docs/journal.md` 2026-09-03 「tasks.md에서 이관한 조사 기록」.
- [ ] LoginScreen.tsx를 `humanizeError`/`CODE_MESSAGES`로 옮긴다 — 다만 `require_frontend_origin`(403 `INVALID_ORIGIN`)이 bare 문자열이고 `CODE_MESSAGES`에도 없어, 먼저 그것을 봉투화하고 코드 매핑을 추가하지 않으면 원문 토큰이 화면에 노출되는 새 회귀가 생긴다. 조사 기록: `docs/journal.md` 2026-09-03 「tasks.md에서 이관한 조사 기록」.
- [ ] GM-17 본작업 — production compose candidate의 required-set 완화와 bind allowlist 외부화. **착수 전 오너와 범위를 재확인할 것** — production 보안 경계를 직접 건드리고, allowlist 이동은 root-owned 설정 파일의 소유권·권한 검증 인프라를 먼저 요구한다. 조사 기록: `docs/journal.md` 2026-09-03 「tasks.md에서 이관한 조사 기록」.
