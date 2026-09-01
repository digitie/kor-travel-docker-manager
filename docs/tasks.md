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
- [/] 범용 관리툴 감사 GM 트랙 — main `9916b33` 기준 전체 분석에서 나온 개선 태스크 20건(P1 7건 포함)을 [`docs/general-mgmt-audit.md`](general-mgmt-audit.md)의 우선순위 순으로 이행한다. 작업 브랜치 `refactor/general-mgmt-improvements`.
- [ ] atomic-write 프리미티브 잔여 통합 (GM-10 후속) — `services/secure_state_file.py`로 옮기지 않은 mkstemp 자리 9곳(admin_password_service·map_application_300 ×3·compose_service·pinvi_database_role_credentials·legacy_override_retirement·standalone_backup, 각자 back-reference 주석 있음)을 개별 소유권·symlink 정책 검토 후 이관한다. 적대적 리뷰가 발견한 `pinvi_bootstrap_credential.py`의 `_fsync_directory_descriptor`는 우선순위가 더 높다 — 디렉터리 fsync 실패가 이미 성공한 credential 파일을 바깥 `except BaseException`의 zeroize+unlink로 파괴할 수 있는 구조(단순 오탐 보고보다 심각)이며, one-shot 보안 초기화 경로라 개별 검토가 필요하다.
- [ ] docker-targets.yml 스키마 검증 잔여 (GM-11 후속) — (1) `cli.py`의 `DIRECT_ENSURE_ALIASES`가 모듈 import 시점에 `list_targets()`를 호출해, 설정 오류 시 `ktdctl`의 어떤 명령도 raw traceback으로 죽는다(메시지 내용 자체는 GM-11로 개선됐지만 표시 방식은 그대로). `main()` 안에서 지연 계산 + 정리된 stderr 메시지로 감싸려면 `registry.py`의 `MANAGED_CONTAINERS`/`MANAGED_TARGETS`/`TARGET_ALIASES` 모듈 레벨 즉시 계산 자체를 lazy하게 바꿔야 해서(현재 수십 곳이 이미 계산된 dict로 가정) 범위가 크다. (2) `ktdctl targets validate` 같은 전용 사전 검증 서브커맨드가 없다 — `docs/docker-management.md` 3절에 별도 python 한 줄 검증법을 문서화해뒀지만 전용 CLI는 아니다. (3) `frontend/src/components/DashboardClient.tsx:289-291`의 주석이 `targets.containers`를 여전히 `depends_on` 전이 폐포로 잘못 설명하고 있고, narrowest-target/'기타' 버킷 로직도 손 목록 의존을 그대로 유지한다 — GM-11 검증 노트가 이미 틀렸다고 확인한 전제인데 frontend 쪽 주석·구현은 손대지 않았다.
