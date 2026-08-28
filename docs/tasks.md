# TASKS — 활성 작업

이 문서는 완료되지 않은 작업만 의존 순서대로 한 줄씩 나열한다. lane, 담당자 구분,
계층형 하위 작업은 사용하지 않는다. 완료·퇴역 이력은
[`docs/tasks-done.md`](tasks-done.md), 실행 근거는 [`docs/journal.md`](journal.md)가 정본이다.

- [/] T-VN-M05-CATALOG-TEMPLATE0 — n150에서 단 한 번 실행한 `68d99705…`·`285618c0…`·`37932169…`·`31fe73ad…`·`b22bfb8c…`·`89330403…`·`c6c73cdf…` candidate는 terminal로 보존하고 재시도하지 않는다. `c6c73cdf…`은 `map_runtime_ready` 뒤 `role_catalog_reset_failed/foreign_membership`으로 끝났고 raw stderr·catalog row는 읽지 않았다.
- [/] T-VN-M05-NEW-CANDIDATE — `6269138f…`와 `53d4639f…`는 모두 재실행하지 않는다. 후자는 trusted release가 launcher 파일을 실행 가능하게 설치하지 않아 admission 이전에 끝났고 durable output·ledger·raw stderr가 없다. installer는 launcher mode `0755`을 명시적으로 보존한다. PinVi `7d66523a…`·Map `9c64e862…`의 `9835cfcc…`도 실행하지 않는다. PinVi `323e3ba8…`·Map `9c64e862…`의 `2d6d5ad5…`에서만 새 isolated harness를 정확히 한 번 실행한다. harness는 trusted installer와 같은 global mutation lock을 종료까지 유지하고, pinset별 root-owned `O_NOFOLLOW|O_EXCL` ledger claim+directory fsync로 output path를 바꿔도 재실행을 막는다.
- [ ] T-VN-M05-ACTIVATION — provenance가 재결박된 committed candidate에서만 n150 isolated M04/M05 live mutating E2E와 activation attestation을 실행한다.
- [/] T-VN-M05-ISOLATED-HARNESS — host-network canonical runtime을 변경하지 않고, exact Map/PinVi source·bridge network·fresh volume·loopback binding만 쓰는 root-only M04/M05 isolated harness를 추가한다. receipt schema는 여섯 image ID·두 source·Map full OpenAPI를 PinVi consumer와 결박했고, Docker mutation driver와 fresh source snapshot·cleanup receipt를 이어서 결선한다. pair의 admin/full/service/user historical source object는 canonical 원격에서 exact raw/canonical SHA로 먼저 검증하며, M04 UI 승인→Map 승인→candidate rebind→PinVi receipt→M05 attestation을 실제 실행한다. host loopback HTTP는 ambient proxy를 타지 않으며, generic Map image fixture에는 owner credential을 주입하지 않고 `ktm_feature_dagster_runtime`의 provider 적재·candidate procedure 경로만 쓴다. Docker mutation 전 `(harness, pinset, Manager revision)` ledger claim과 global lock을 고정하고, 증적은 production activation과 분리한다.
- [ ] T-VN-41F1D-D1 — 최종 격리 리허설과 provenance attestation을 기록한다.
- [ ] T-VN-41F1D-D2 — data-dependent Map/PinVi admin live E2E와 receipt 승격을 완료한다.
- [ ] T-VN-41C — relay, reconciliation, consumer enable paired acceptance를 완료한다.
- [ ] T-VN-41F1D-E — 이전 generation 퇴역과 v6/v8 attestation 전환을 완료한다.
- [ ] T-VN-H43 — production backup의 정기 dump, SHA-256, 보존, rollback 기준선을 확정한다.
- [ ] T-VN-H49 — 분할 인스턴스 backup의 주기 실행, bounded retention, off-box 증거를 완료한다.
