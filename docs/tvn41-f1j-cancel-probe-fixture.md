# T-VN-41-F1J — Map 소유 cancel-probe fixture lifecycle 실행 계획

## 목적과 완료 기준

F1D candidate의 PinVi canonical smoke가 실제 Map cancellation 경로를 검증하면서도, 존재하지 않는 UUID의
`404` 또는 Dagster 장애를 성공으로 오인하지 않게 한다. 완료는 n150에서 다음 durable evidence를 남기는 것이다.

1. Manager가 candidate Map의 fixture를 ensure해 새 transaction-scoped `job_id`를 받는다.
2. PinVi의 기존 `POST /admin/provider-sync/import-jobs/{job_id}/cancel` relay가 정확히 한 번 실행된다.
3. canonical full detail과 root ID를 포함한 `409 PIPELINE_CANCELLATION_UNSAFE`만 success가 된다.
4. Map finalize가 fixture job을 terminal로 정리하되 cancellation history와 receipt는 남긴다.
5. process crash·response loss·same-candidate rerun은 새로운 POST나 fixture reuse 없이 durable state에서
   재개하거나 fail-close한다.

## 현재 실패와 제외 범위

F1I journal은 마지막 시도가 `login(200) → etl_summary(200) → provider_sync(200) → cancel(404)`였음을
보인다. 따라서 PinVi login/session/role, Manager runtime, 일반 admin read route는 원인이 아니다.

이 작업은 PinVi가 fixture를 생성하거나 Map DB에 직접 연결하도록 바꾸지 않는다. Manager의 raw Docker,
Compose, `docker exec`, `.env` 또는 DB mutation도 추가하지 않는다. Map startup/migration seed도 쓰지 않는다.
그런 경계는 transaction recovery와 배포 rollback에 hidden side effect를 만들기 때문이다.

## 소유권과 API 경계

Map은 다음 REST lifecycle API와 전용 `ops:fixture` principal을 소유한다. 해당 credential은 Manager와 Map API에만
있고 PinVi 서비스에는 주입하지 않는다.

| 동작 | Map internal API | 호출자 | 결과 |
| --- | --- | --- | --- |
| ensure/read | `PUT`/`GET /v1/ops/contract-fixtures/c6c-cancel-probe/{transaction_id}` | Manager | dynamic job ID와 durable 상태, consumed/finalized의 immutable canonical unsafe outcome 반환 |
| relay 검증 | 기존 PinVi cancel API | Manager → PinVi | PinVi가 기존 `ops:cancel` 경계로 Map normal cancellation 호출 |
| finalize | `POST /v1/ops/contract-fixtures/c6c-cancel-probe/{transaction_id}/finalize` | Manager | exact consumed fixture만 terminal로 정리 |

`transaction_id`는 F1D durable journal의 public UUID이고 Map이 `job_id`를 새로 발급한다. 기존
`KTDM_C6C_CANCEL_PROBE_JOB_ID`는 삭제한다. fixture lifecycle API는 ordinary ops API가 아니므로 `ops:read`,
`ops:cancel`, admin/service token의 권한을 확장하지 않는다.

## Map 영속 모델과 상태 전이

Map migration은 `ops.c6c_cancel_probe_fixtures`에 one-row-per-F1D-transaction ownership을 명시한다. 실제
table/column 명세는 Map ORM과 existing canonical cancellation table의 식별자에 맞추되, 아래 관계는 바꾸지 않는다.

| 필드/관계 | 불변식 |
| --- | --- |
| `transaction_id` | PK. Manager의 F1D durable transaction identity와 one-to-one |
| `job_id` | Map 생성 UUID, `ops.import_jobs` FK, unique·`NOT NULL` |
| `state` | `armed`, `consumed`, `finalized`만 허용하는 `TEXT CHECK` |
| `cancellation_id` | `consumed`/`finalized`에서만 존재하는 canonical cancellation row FK·unique |
| timestamps | `created_at`, `consumed_at`, `finalized_at`는 `TIMESTAMPTZ NOT NULL`/상태 관계 CHECK |

`ensure`는 같은 transaction ID의 exact record만 다시 반환한다. 새 record는 normal cancellation path가
deterministically `409 PIPELINE_CANCELLATION_UNSAFE`를 내도록 실제 `running` import job을 만들며, 다른
Dagster run·provider data·feature data에는 접근하지 않는다. normal cancellation이 response를 만들 때 Map은
fixture와 canonical cancellation record를 같은 transaction에서 `consumed`로 기록한다. 따라서 HTTP response가
Manager에 유실돼도 Manager는 `GET`으로 consumed identity와 canonical result fingerprint를 검증하고 POST를
다시 보내지 않는다.

`finalize`는 fixture-specific repository transaction만 사용한다. 정확한 transaction ID, job ID, consumed
state, cancellation identity를 모두 확인한 뒤 job을 terminal로 만들고 fixture를 `finalized`로 전이한다.
generic finish/cancel, marker/history 삭제, finalized job 재무장은 금지한다. 새 F1D transaction에는 새 row와
새 job ID가 필요하다.

## Manager orchestration과 fail-close 규칙

candidate Map migration 및 Map authenticated readiness 후, PinVi API smoke 전 아래 상태기를 실행한다.

```text
ensure(armed) → journal armed receipt fsync → attempted=true receipt fsync
  → PinVi cancel POST 1회 → exact 409 + canonical detail 확인
  → Map GET(consumed) → verified receipt fsync → Map finalize → finalized receipt fsync → committed

ensure(consumed) → Map canonical unsafe outcome 조회·검증·receipt fsync → Map finalize → committed
ensure(finalized) → Map outcome과 existing receipt 일치 확인 → committed
```

Manager는 fixture 경로에서 `409 PIPELINE_CANCELLATION_UNSAFE` 이외 `404`, `502`, `503`, timeout, malformed
envelope, wrong root ID, response/detail drift를 모두 failure로 취급하고 protected runtime halt 규칙을 유지한다.
`PinviCancelProbeState` 같은 process-local 값은 recovery authority가 아니다. journal에는 safe transaction ID,
job ID, Map lifecycle state, cancellation ID, POST 직전 attempted flag, exact canonical response fingerprint,
verification/finalization UTC만 기록하고 credential, raw response, exception은 기록하지 않는다. receipt 전이는
`armed → consumed → finalized` 및 attempted false→true로만 단조 진행하며, job/cancellation identity와
검증·종결 시각은 확정 뒤 바뀔 수 없다. 특히 POST 전에
`attempted=true`를 durable write하므로 response loss 후 armed fixture에 같은 cancel POST를 재시도할 수 없다.

## Pair provenance와 rollout

Map lifecycle capability generation은 service OpenAPI artifact/Map revision/Map Alembic head와 함께
compatible-pair pinset의 required field가 된다. F1J Map release 뒤 PinVi metadata와 Manager input manifest는
그 exact generation을 재결박한다. endpoint 또는 generation이 없는 old Map image는 F1D rollback candidate가
아니며, Manager preflight에서 fail-close한다.

다음 PR 순서를 지킨다.

1. **F1J-A Map**: migration, repository, internal API/auth, cancellation integration, OpenAPI export와 unit/integration
   tests. Map 문서와 task 기록을 먼저 별도 문서 PR로 반영한다.
2. **F1J-C pair**: Map release artifact를 PinVi metadata에 반영하고 compatible-pair pinset/installer capability를
   재결박한다. PinVi code는 existing relay structured-error regression만 담당한다.
3. **F1J-B Manager**: static UUID/broad success removal, fixture credential wiring, durable receipt와 state-machine
   tests를 구현한다.
4. **F1J-D n150**: trusted installer로 Map → pair metadata/pin → Manager 순서로 설치한 뒤 destructive F1D,
   idempotent recovery, admin live Playwright E2E를 실행한다.

각 code PR은 implementation 전에 codegraph 영향도를 확인하고 단일 적대적 리뷰를 반영한다. 문서 전용 PR은
리뷰된 본 계획을 그대로 반영해 CI 대기 없이 merge한다.
