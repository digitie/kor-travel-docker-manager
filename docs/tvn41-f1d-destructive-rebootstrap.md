# T-VN-41-F1D — 파기형 pinned runtime generation 재bootstrap 설계

## 전제

이 문서가 다루는 n150 환경은 운영 서비스가 아니다. 현재 Map·PinVi 데이터베이스, 과거
compatible-pair manifest, 이전 F1D journal과 backup은 보전·복원 대상이 아니다. 데이터가
필요해지면 최종 스키마에 맞춰 file source와 ETL을 다시 실행한다.

따라서 이 작업의 목표는 오래된 DB head나 중단된 receipt를 복구하는 것이 아니라, tracked
release pin 하나로 **새 runtime generation과 새 DB schema를 다시 만드는 것**이다. raw Docker,
Compose, SQL, `.env`, state-file 삭제를 사람이 조합해 실행하는 경로는 만들지 않는다.

## 현재 구조의 결함

기존 `bootstrap-pinned-drift`는 Map 네 service와 PinVi API만 compatible pair로 기록한다.
하지만 PinVi Web·Dagster는 같은 source tree와 PinVi DB를 공유한다. 다섯 service만 halt하거나
재기동하면 두 PinVi service가 다른 image generation과 DB schema를 계속 사용할 수 있다.

또한 F1D의 `prepared` journal은 당시 candidate의 DB head를 immutable input으로 보관한다.
새 Map migration 또는 PinVi migration으로 pinset을 회전하면, old journal을 보존하는 한 새
input installation과 bootstrap은 모두 서로를 차단한다. 비운영 환경에서 이 receipt를 복구
근거로 해석하는 것은 데이터와 runtime의 어느 쪽도 보호하지 못하면서 재실행만 막는다.

## 목표 모델

F1D는 `CompatibleImagePair`가 아니라 다음 일곱 service의 단일 `PinnedRuntimeGeneration`을
소유한다.

| 소유 서비스 | image와 source 불변식 |
| --- | --- |
| Map API, UI, Dagster web, Dagster daemon | 네 image ID와 Map source revision이 모두 같다. |
| PinVi API, Web, Dagster | 세 image ID와 PinVi source revision이 모두 같다. |

manifest는 이 일곱 immutable image ID, 두 source revision, Map application/Dagster와 PinVi의
새 schema head, pinset digest를 하나의 generation으로 기록한다. 구 v4 pair·old rollback은
새 generation의 authority가 아니다. 새 v5 manifest에는 `active_generation` 하나만 두며,
DB preimage가 없는 상황을 rollback slot으로 가장하지 않는다.

## 단일 명령과 상태 전이

기존 `ktdctl pinvi-pair bootstrap-pinned-drift --confirm`은 제거하고, 의미가 분명한
`ktdctl pinvi-pair rebuild-pinned --confirm` 하나로 대체한다. 이 명령은 frozen canonical
environment의 typed pair `KTDM_DEPLOYMENT_ENVIRONMENT=rehearsal` 및
`KTDM_DEPLOYMENT_LIFECYCLE=rebuildable`가 정확할 때만 동작한다. 환경/lifecycle은
`local/development`, `rehearsal/rebuildable`, `production/operational`만 유효한 enum pair다.
기존 production config에 lifecycle 값 하나만 추가하거나 `--confirm`만 주는 것으로는 파기 권한이
되지 않으며, 실제 서비스 운영 lifecycle에서는 항상 거부한다.

```text
preflighted
  → candidate_attested
  → reset_intent_durable
  → databases_recreated
  → map_application_ready
  → map_dagster_ready
  → map_runtime_ready
  → pinvi_schema_ready
  → pinvi_api_ready
  → pinvi_runtime_ready
  → contract_verified
  → manifest_committing
  → committed
```

`candidate_attested`는 일곱 candidate image ID, 두 source revision, candidate artifact가 직접 보고한 세 expected schema head,
frozen environment/Compose digest와 pinset digest를 owner-only journal에 fsync하고 retention reference로
보존한 상태다. candidate artifact 하나라도 없거나 provenance/schema-head contract가 다르면 DB를
건드리지 않는다. `reset_intent_durable` 이후 process crash 재실행은 journal에 기록된 exact candidate만
사용한다. candidate image가 사라진 경우에는 새 build로 덮어쓰지 않고 fail-close한다.

같은 pinset의 non-terminal journal은 같은 phase를 idempotently 재개한다. `reset_intent_durable` 뒤
실패한 phase의 재실행은 세 DB를 partial state에서 재사용하지 않고 다시 모두 drop/create한다. 다른 pinset의 새 rebuild는
Manager가 소유한 F1D/F1F v1~v4 state와 mutation gate를 v5 authority로 완전히 교체한다. legacy tombstone은
코드에 고정한 path allowlist만 대상으로 하며, 각 parent가 canonical state root 아래 owner-owned `0700` directory인지,
각 file이 `lstat` 기준 regular file·manager owner·`0600`·link count 1·bounded size인지 확인한다. `dir_fd`와
`O_NOFOLLOW`로 열어 pre/post `fstat` inode가 같은지도 대조한 뒤 bounded bytes의 SHA-256만 receipt에 fsync한다.
foreign/symlink/hardlink/owner·mode·size·JSON shape 손상은 모두 fail-close하며 어떠한 DB/runtime mutation도 하지
않는다. 검증한 tombstone receipt를 먼저 fsync한 뒤에만 같은 `dir_fd`로 legacy file을 unlink하고 old reader와
`assert_*_allows_pair_mutation` gate를 제거한다. 사람이 state file을 삭제하거나 legacy receipt를 변환하지 않는다.

## 파기 범위와 schema 초기화

1. Manager가 frozen resolved Compose에서 Map application, Map Dagster, PinVi database의 정확한
   database/container/owner identity를 읽는다. 이 세 database 외의 Geo·Concierge·공용 service
   database는 변경하지 않는다.
2. trusted source staging에서 일곱 image를 먼저 build하고 immutable ID, OCI source revision, Map application·
   Map Dagster·PinVi schema head를 candidate journal에 고정한다. Map Dagster head는 source revision으로
   추정하지 않고 candidate Dagster image의 head-inspection command가 출력한 dependency storage head만
   수용한다. source checkout의 local HEAD,
   floating tag, 기존 image는 authority가 아니다.
3. `reset_intent_durable` 뒤 일곱 runtime을 모두 중지하고 writer가 없음을 확인한다. PinVi Dagster는
   writer이므로 API만 멈춘 채 DB를 재생성하지 않는다.
4. PostgreSQL owner 권한으로 세 database를 `DROP DATABASE ... WITH (FORCE)` 후 같은 owner로
   `CREATE DATABASE` 한다. dump, backup, restore, old database head 비교는 사용하지 않는다.
5. Map API candidate entrypoint만 Map application migration owner로 기동해 candidate-attested
   `map_application_head`까지 적용하고 health/ops principal을 확인한다. 다음에는 Map Dagster candidate의
   migration-only command만 실행한다. 이 command는 같은 candidate image의 `dagster instance migrate`로
   storage migration을 적용하고, `public.alembic_version`의 정확히 한 `version_num`이 candidate가 직전
   출력한 `map_dagster_head`와 일치할 때만 성공한다. 둘의 head는 candidate artifact에서 attested한 별도
   field이며, 그 뒤에만 Map UI, Dagster web·daemon을 같은 candidate image로 기동한다.
6. PinVi 쪽의 별도 `pinvi-admin-bootstrap` one-shot CLI는 먼저 candidate-static `pinvi_head`까지
   `alembic upgrade head`를 실행하고, 같은 transaction에서 그 head를 확인한 후에만
   `PINVI_BOOTSTRAP_ADMIN_CREDENTIAL_FILE`만 받는다. Manager는 frozen smoke credential에서 owner-only
   `0600` credential file을 만들고 이를 candidate one-shot container에 read-only mount한다. Docker에는
   file path만 전달하므로 password 원문은 container environment·inspect metadata·journal·log에 남지 않는다.
   CLI는 fresh PinVi DB에 admin을 만든 뒤 종료하며, Manager는 container 종료 여부와 file owner/mode를
   검증하고 file을 unlink한다. crash 뒤 재실행도 frozen credential에서 새 file을 만들어 수행한다. normal API,
   Web, Dagster, Map service에는 이 mount나 credential environment를 주입하지 않는다. 이후 normal PinVi
   API와 Web·Dagster를 credential 없이 같은 candidate image로 기동한다. normal PinVi API는 migration이나
   implicit bootstrap admin을 수행하지 않고, ready 전에 candidate `pinvi_head`를 다시 대조한다.
7. 세 live schema head와 일곱 immutable image ID를 journal의 candidate와 대조한다. 그 뒤에만
   F1J fixture ensure → PinVi cancel 한 번 → exact `409 PIPELINE_CANCELLATION_UNSAFE` → finalize,
   admin UI login/logout, provider/ETL 상태를 검증하고 single active manifest를 commit한다.

실패하면 old runtime·old DB·old manifest를 되살리지 않는다. 해당 generation의 일곱 runtime을
중지한 채 journal에 실패 checkpoint를 남긴다. 재실행은 새 database에 같은 generation을
다시 기동하거나, 명시적으로 같은 명령을 새 pinset에 대해 처음부터 실행한다.

## 보안과 운영 경계

- DB 삭제·생성은 Manager 내부의 frozen Compose database identity를 통해서만 수행한다. shell
  interpolation, 임의 database name, 호스트 SQL, password 출력은 허용하지 않는다.
- source selection은 기존 trusted bare staging 원칙을 유지한다. user-owned checkout Git 설정을
  root가 해석하지 않는다.
- Map `ops:read`, `ops:cancel`, `ops:fixture` capability는 기존 최소 권한을 유지한다. PinVi에
  fixture credential이나 Map admin proxy credential을 주입하지 않는다.
- 새 runtime generation과 DB schema를 검증한 뒤 source/ETL 재적재는 별도 작업이다. rebuild는
  sample data, backup, restore, data migration을 수행하지 않는다.

## PR 단위 작업

1. **F1D-A**: 이 설계·ADR·task 문서를 병합한다.
2. **F1D-B**: `CompatibleImagePair`와 v4 manifest, old `deploy`/`capture`/`rollback` 및 legacy
   mutation gate를 제거한다. 일곱 service `PinnedRuntimeGeneration`, single-active v5 manifest,
   세 schema head와 tombstone receipt를 단일 authority로 구현한다. 기존 F1G/F1H의 window·inert
   diagnostic receipt도 이 typed tombstone allowlist에 흡수하며 별도 T-VN 선행 task로 남기지 않는다.
   environment/lifecycle enum pair와 rebuildable의 exclusive mutation policy도 이 PR에서 typed loader·회귀
   test로 고정한다.
3. **F1D-C0 (Map PR)**: candidate Dagster image의 dependency storage head를 기계 판독 가능하게 출력하고,
   같은 image가 `dagster instance migrate` 뒤 strict `public.alembic_version` 대조를 수행하는
   migration-only command를 구현한다. Map application Alembic과 Dagster storage revision을 혼용하지 않는다.
4. **F1D-C1 (PinVi PR)**: `pinvi-admin-bootstrap` CLI와 credential-file contract를 구현한다. 이 CLI가
   PinVi Alembic migration과 admin bootstrap의 유일 owner가 되게 하고 normal API의 implicit migration/direct
   password environment bootstrap을 제거한다. owner/mode/content validation·migration→admin idempotence·
   redaction test를 포함한다.
5. **F1D-C2 (Manager PR)**: C0 Map과 C1 PinVi source pin을 입력으로 `rebuild-pinned --confirm` transaction을
   구현한다. explicit rebuildable lifecycle, candidate-first attestation/retention, scoped DB recreate,
   Map Dagster migration-only invocation, one-shot credential-file mount, generation build/start, F1J canonical
   smoke와 crash resume을 포함한다.
6. **F1D-D (docs-only PR)**: n150에서 파기형 rebuild, final schema head, admin live UI E2E와 PinVi
   mutating E2E를 실행한 결과를 기록하고 source/ETL 재적재 작업으로 handoff한다.
