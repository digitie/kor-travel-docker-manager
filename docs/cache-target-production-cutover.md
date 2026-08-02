# T-VN-41 cache-target production cutover runbook

이 문서는 Map cache-target stream과 PinVi generation/outbox consumer를 production에서 처음 연결하는
manager 제품 경로의 정본이다. 실제 credential·호스트·도메인은 기록하지 않는다. 민감 접속값은
gitignore된 `docs/deploy-runbook.local.md`와 canonical `.env`에만 둔다.

## 1. 고정 배포 계약

Map API에만 다음 registry를 전달한다.

- `KOR_TRAVEL_MAP_API_CACHE_TARGET_SERVICE_PRINCIPALS`: command, consumer,
  restore-fence, recovery 정확히 네 principal의 token SHA-256 digest, 고유 principal ID, 공통 consumer ID,
  정확한 최소 scope와 `["pinvi"]` external system allowlist를 담은 JSON 배열

PinVi ordinary API에는 정확히 다음 7개만 전달한다.

- `PINVI_KOR_TRAVEL_MAP_CACHE_TARGET_SYNC_ENABLED`
- `PINVI_KOR_TRAVEL_MAP_CACHE_TARGET_COMMAND_TOKEN`
- `PINVI_KOR_TRAVEL_MAP_CACHE_TARGET_CONSUMER_TOKEN`
- `PINVI_KOR_TRAVEL_MAP_CACHE_TARGET_CONSUMER_ID`
- `PINVI_KOR_TRAVEL_MAP_CACHE_TARGET_EXPECTED_OPENAPI_SHA256`
- `PINVI_KOR_TRAVEL_MAP_CACHE_TARGET_EXPECTED_SOURCE_REVISION`
- `PINVI_KOR_TRAVEL_MAP_CACHE_TARGET_EXPECTED_CONTRACT_GENERATION`

이 7개 기능 변수와 별도로 Map cache-target HTTP endpoint는
`PINVI_KOR_TRAVEL_MAP_API_BASE_URL=http://127.0.0.1:12701`을 명시 전달한다. 암묵 localhost default에
의존하지 않으며 기존 admin base URL과 같은 production root로 exact 검증한다. consumer ID는 PinVi 정본인
`pinvi-cache-target-consumer`로 고정한다.

`PINVI_KOR_TRAVEL_MAP_CACHE_TARGET_RESTORE_FENCE_TOKEN`과
`PINVI_KOR_TRAVEL_MAP_CACHE_TARGET_RECOVERY_TOKEN`은 ordinary API, PinVi Web/Dagster, Map UI/Dagster와
다른 장기 실행 container에 전달하지 않는다. 전용 initial-cutover runner에는 실제 사용하는 recovery
token만 실행 시간 동안 전달한다. restore-fence token은 Map registry와 향후 별도 restore 작업 경계에만
보관하며 initial-cutover runner에도 전달하지 않는다.

네 role token은 모두 32자 이상·공백 없음이며 서로 달라야 한다. 기존 Map/PinVi service, admin,
ops read/cancel token과도 같을 수 없다. Map registry에는 원문 token을 넣지 않고 lowercase SHA-256
digest만 넣는다. command principal은 `cache-target:command`, consumer principal은
`cache-target:read`·`cache-target:claim`·`cache-target:ack`·`cache-target:nack`·
`cache-target:snapshot`, restore-fence principal은 `cache-target:restore-fence`, recovery principal은
같은 recovery trust domain인 `cache-target:recovery`와 `cache-target:recovery-replay`의 정확한 두 scope를
가진다. initial runner는 replay를 호출하지 않지만 별도 replay principal·다섯 번째 token은 만들지 않는다.
extra principal·scope·external system은 허용하지 않는다. registry JSON과 각 token digest도 secret redaction/protected-value
검사의 대상이다. 안전한 audit/receipt에는 registry JSON이나 개별 digest 대신 canonical
role→(digest, consumer ID, exact scopes, external system) binding 전체의 logical SHA-256과 구조 검증 결과만
남긴다.

production cutover pin의 tracked 정본은
`cache_target_production_manifest.py`다. 현재 고정값은 contract generation `7`, Map cache-target
OpenAPI SHA-256
`622ea54c98e9b0c09592cf84aced36227992c6bdf256742a3532b892f0efccf2`, Map functional owner revision
`9b945ce832ecc3ed037d66c9d4e7bda9a1a69ae0`, Map release revision
`d50bb2c53c179d182b9cf017308df075b691414e`다. PinVi reviewed candidate
`6ac8baae2814fae5b16c95846ee40d77cc7fe283`는 review 출발점의 감사 정보일 뿐 release가 아니며,
PinVi release revision은 적대적 GO review 뒤 squash merge된
`4943282006139fa3b4ef3cb247780bfd9721b4c7`로 고정한다. candidate를 release로 자동 승격하거나
fallback으로 쓰지 않는다. Map과 PinVi 각각의 build/release SHA를 active와 rollback pair 양쪽에 결박해야만
cutover를 진행할 수 있다. Map #924의 final squash merge SHA를 이 문서와 tracked manifest의 Map release
revision에 고정했다. functional owner revision은 API 계약 소유자를, build/release revision은 실제
immutable image 출처를 뜻하므로 서로 대체하지 않는다. cache-target contract가 아예 설정되지 않은 기존
production C6c 경로에는 이 gate를 적용하지 않는다. 모든 필드가 canonical unset/default이면 contract가
없는 것으로 처리하되, 하나라도 부분 설정됐으면 기존 경로로 내려가지 않고 fail-close한다.

## 2. 사전 조건

1. Manager, Map, PinVi exact source revision이 review된 compatible pair이고 manifest active와 rollback image가
   local Docker에서 immutable ID로 존재하는지 확인한다. 두 pair 모두 같은 cache-target generation/contract를
   지원해야 하며 PinVi API cache health/pin smoke를 통과해야 한다.
   tracked `pinvi_release_revision`이 비어 있거나 두 pair 중 하나라도 그 exact release SHA와 다르면 이
   단계에서 중단한다.
2. C6c canonical `.env`, Compose path, project name, source revision evidence와 production root owner/mode를
   검증한다. `.env`의 cache-target sync는 literal `false`여야 한다.
3. raw Compose에 credential literal이 없고 resolved Compose에서 registry와 ordinary 7개 변수가 허가된
   service의 정확한 environment path에만 있는지 확인한다. frozen env SHA, raw/resolved Compose logical SHA,
   protected role-binding logical SHA를 계산한다.
4. running Map API에는 registry만, running PinVi API에는 ordinary 7개만 있고 restore-fence/recovery 원문이
   없는지 inspect digest 비교로 확인한다. 다른 container에는 네 role 이름·값이 없어야 한다.
5. Map/PinVi DB migration head, active compatible pair readiness, backlog/DLQ/epoch 전제와 pinned
   OpenAPI/source/contract generation을 확인한다.

사전 조건은 `/run/lock/kor-travel-docker-manager/global-mutation.lock` 획득 뒤 frozen canonical
`.env`/Compose identity로 다시 검증한다. lock 밖 결과를 mutation 권한으로 사용하지 않는다.

## 3. generation 7 최초 bootstrap과 결합 전환 window

기존 v4 manifest의 active와 rollback이 generation 7 이전 pair인 상태에서는 일반 pair deploy를 반복해
bootstrap하지 않는다. 첫 deploy는 `active=new, rollback=old`를 만들고 같은 candidate의 재배포는 old
rollback을 유지하므로, 양쪽 모두 exact release여야 하는 initial gate에 영구 도달하지 못한다. 전용
`cache-target cutover` command만 다음 전환을 한 번 허용한다.

1. 하나의 process가 C6c 전역 lock을 획득하고 transaction UUID와 owner-only `0600` durable journal을
   `prepared`로 먼저 fsync한다. journal이 non-terminal인 동안 같은 transaction의 resume/coupled rollback을
   제외한 모든 manager mutation은 subprocess·Docker·DB·env·manifest write 전에 차단한다.
2. resolved Compose에서 DB 쓰기 capability를 가진 service가 Map API·Dagster web·Dagster daemon,
   PinVi API·Dagster의 정확한 5개인지 확인하고 in-flight DB transaction과 Map Dagster run이 0일 때만
   모두 정지한다. 이 writer registry의 canonical digest는
   `526240609e2919357699b90244eb8cc8b9505f37db6c60552a98c7a37ed22d7c`다. old
   manifest/env/manager state와 Map application DB, Map Dagster DB, PinVi DB의 typed backup identity를 frozen
   rollback bundle에 결박한다. 세 dump와 scratch restore rehearsal 전체의 앞뒤에서 DB별 insert/update/delete
   counter와 `stats_reset` identity, in-flight 0, Map Dagster run 0을 다시 읽어 exact 동일해야만 commit한다.
   receipt는 archive SHA, schema/data logical inventory, 별도 scratch DB identity를 가진 restore rehearsal까지
   포함하며 DSN, path, credential, dump stdout/stderr는 기록하지 않는다.
3. exact Map/Pin release source에서 candidate image를 완성하고 `sync=false`로 전체 runtime을 검증한다.
   Map·Pin DB migration과 Map의 H35 CSV 전환을 service-owned typed CLI로 수행한 뒤, cache health와 source
   provenance가 맞는 첫 generation 7 pair를 v4 manifest의 active와 rollback 양쪽에 원자 commit한다. old pair는
   compatible-pair rollback slot에 남기지 않고 frozen coupled rollback bundle에만 보존한다.
4. 같은 lock/process에서 initial runner, sync enable, causal canary를 수행한 뒤 Map-owned `gc` helper로
   deterministic observation run에 결박된 실제 snapshot GC를 실행한다. acquired/non-skipped, bounded batch,
   remaining backlog 0, referenced 보존과 observation-current 일치를 typed receipt로 검증한다. observation
   ID는 `h35:{transaction_id}:cache-target-snapshot-gc:v1`이며 이전 축약형 `h35:{transaction_id}:gc`는
   fail-close한다. 그 다음
   `final_writers_fencing`을 먼저 fsync하고 exact 5 writer를 모두 정지한다. 세 DB in-flight 0, Map Dagster run
   0, registry와 stopped state의 fresh final fence를 확정하고 Map 두 DB write-counter hash를 별도 결박한다.
   stopped DB에서 Map `verify`가 stream/control/epoch/etag/high-watermark/count/Merkle/backlog 0의 full typed
   final evidence를 발행한다. Pin-owned final-boundary helper는 schema `0047` read-only preflight와 schema
   `0048` append-only finalize를 분리하고 initial/final fence, full Map evidence+SHA, initial/canary provenance를
   exact audit row에 결박한다. Manager는 fresh Pin DB audit row가 같은 request/evidence/fence이고 정확히 1행인지
   대조한다. final audit row는 개별 삭제하지 않는다. 모두 성공한 뒤 `forward_committed`를 먼저 fsync하고,
   exact 5 writer를 idempotent하게 재기동·health/attestation한 `runtime_activated`에서만 성공을 반환한다.
5. forward boundary 전 실패는 new runtime을 먼저 중지하고 Map application DB → Map Dagster DB → PinVi DB →
   manager env/state/manifest를 frozen bundle로 복구한 뒤 old image를 마지막에 기동·검증한다. migration 이후
   일반 image-only rollback은 금지한다. forward boundary 이후 실패는 old schema restore 대신 새 generation의
   fix-forward 또는 같은-generation recovery만 허용한다.

허용 phase는 `prepared → writers_fencing → writers_fenced → backups_committed → candidate_built →
pin_preflight_verified → map_preflight_verified → map_database_forwarded → databases_forwarded → csv_forwarded →
generation_bootstrapped → initial_committed → sync_enabled → canary_verified → gc_started → gc_verified →
final_writers_fencing → final_writers_fenced → map_final_verified → final_boundary_verified → forward_committed →
runtime_activated`다. rollback은
forward boundary 전에만 `rollback_preparing → new_runtime_stopped →
map_db_restored → map_dagster_db_restored → pinvi_db_restored → manager_state_restored → old_runtime_restored →
rolled_back` 순서로 진행한다. phase를 건너뛰거나 뒤로 이동하지 않으며 각 전이는 owner-only atomic replace와
directory fsync 뒤에만 다음 mutation을 허용한다.

Map helper는 manager가 SQL/schema/CSV 의미를 재구현하지 않도록 candidate image에 포함된
`python scripts/h35/h35_cutover.py {preflight,migrate,csv5,gc,verify}` 다섯 operation만 제공한다. request는 stdin
단일 JSON, receipt는 stdout 단일 JSON line이며 helper는 runtime stop/start/recreate, lock/journal,
credential/path 탐색, backup/restore/finalize를 하지 않는다. 이 lifecycle은 manager가 소유한다. DB 연결은
manager가 exact candidate image의 기존 runtime environment로 주입하고 request에는 DSN, credential, path를
싣지 않는다. `csv5`는 image-bundled canonical 5-file bundle만 사용하고 host path 인자를 받지 않는다.

helper receipt의 exact 공통 key는 `contract_version`, `operation`, `transaction_id`, `status`,
`source_revision`, `database_identity`, `request_digest`, `prior_receipt_digest`, `schema_before`, `schema_after`,
`forward_boundary`, `row_counts`, `checks`, `cache_target_evidence`, `runtime_mutation_count`,
`external_event_count`다. `database_identity`는
manager backup receipt의 DB identity/schema/transaction UUID에 결박하는 non-secret opaque digest다.
`prior_receipt_digest`는 `preflight=null`, `migrate=preflight digest`, `csv5=migrate digest`, `gc=csv5 digest`,
`verify=gc digest`로
연결하고 모든 phase에서 `runtime_mutation_count=0`, `external_event_count=0`을 요구한다. stdout에 extra/missing
field가 있거나 stderr가 있거나 foreign transaction/source/schema/digest면 진행하지 않는다. 같은
transaction/request/prior digest의 재실행만 idempotent receipt를 허용한다.

DB identity는 Map·Pin·Manager가 공유하는 `h35-db-identity-v1`이다. bytes는
`b"h35-db-identity-v1\0" + transaction_uuid + b"\0" + role + b"\0" + database_name + b"\0" +
system_identifier_decimal + b"\0"`의 SHA-256이며 UUID·role·DB 이름·decimal을 canonical 검증한다. scratch
rehearsal DB는 이름이 다르므로 운영 DB identity를 재사용하지 않고 별도 identity를 기록해 원 archive SHA와
schema/data inventory에 결박한다.

`preflight`는 schema `0063`/public row `3265`와 0075 기존행 identity/NFC/trim/length/CHECK/FK 위반 0,
`migrate`는 schema `0078`/public row `3043`과 0064/0068/0069 partial residue 0, `csv5`는 file count 5,
accepted 222, rejected 0, public row 3265, `gc`는 acquired=true/skipped=false, remaining 0, referenced observation
일치를 반환한다. `verify`는 stopped schema `0078`/public row `3265`와 0075~0078
schema/index/outbox/receipt/GC 전수 PASS를 exact checks로 반환한다. `forward_boundary`의 결정·영속은 manager가
소유하고 helper는 `preflight=not_crossed`, migrate 이후 `schema_0078` 관측값만 반환한다. manager 코드와
문서에 실제 DSN, backup path, host, credential을 하드코딩하지 않는다.

## 4. 최초 cutover phase

1. operator가 재사용 가능한 고정 `cutover_id`, positive expected restore epoch와 감사 reason을 준비한다.
   reason은 secret이 아니며 Docker argv/운영 감사에 노출될 수 있으므로 credential·개인정보를 넣지 않는다.
   실행 command는 다음 하나다.

   ```bash
   ktdctl cache-target initial \
     --cutover-id <canonical-uuid> \
     --expected-restore-epoch <positive-int> \
     --reason '<non-secret-audit-reason>' \
     --json
   ```
2. manager 전용 command가 C6c 전역 lock을 획득하고 non-committed 기존 receipt/journal을 먼저 분류한다.
3. active PinVi immutable image로 일회성 runner를 시작한다. ordinary command/consumer와 전용 recovery
   credential만 runner process에 전달하며 argv·stdout/stderr에는 값을 넣지 않는다. Docker `-e value`나
   Compose environment를 쓰지 않고 manager가 만든 owner-only 임시 secret file을 read-only mount한 뒤
   고정 runner entrypoint가 내부에서 읽어 export한다. restore-fence credential은 이 runner에 전달하지 않는다.
4. runner는 `sync=false`를 확인하고 pinned contract, source count/Merkle, recovery reconciliation과 completion을
   수행한다. 같은 cutover ID retry는 PinVi의 durable ledger와 idempotency key로 같은 결과에 수렴해야 한다.
5. manager는 원문 credential과 resolved environment를 제외한 cutover ID, request ID, epoch, count,
   Merkle root, published count, contract pin, active/rollback image·source·logical identity를 owner-only
   receipt에 frozen env/raw Compose/resolved Compose/role-binding logical SHA와 함께 fsync 후 원자 replace한다.
   registry JSON과 개별 token digest는 receipt에 넣지 않는다. runner container/mount와 host secret file이
   제거됐는지도 확인한다.

runner 실패, signal, lock 경합, 결과 parse 오류, foreign/stale receipt는 sync를 열지 않는다. 모든 종료
경로에서 orphan container/mount/secret file을 분류·정리한다. 원격 성공 여부가 불확실하면 같은 cutover
ID로 재실행해 PinVi durable state가 결과를 확정하게 한다.

## 5. sync enable phase

1. committed initial-cutover receipt가 frozen env와 active/rollback compatible pair에 exact하게 맞는지
   확인한다. generic rollback 후보도 같은 generation/contract와 cache health/pin smoke를 통과해야 한다.
2. `.env` 변경 전에 `enable_preparing` journal을 fsync한다. journal은 initial receipt SHA, active/rollback
   pair logical SHA, old/new env SHA, `sync=true`로 사전 resolve한 enabled Compose SHA와 transaction ID를
   묶는다. journal은 owner-only regular file(`0600`, hardlink·symlink 금지)이며 crash 재개에서도 같은
   transaction ID를 유지한다. 실행 command는 `ktdctl cache-target enable --json`이다.
3. canonical `.env`의 `PINVI_KOR_TRAVEL_MAP_CACHE_TARGET_SYNC_ENABLED=false` 한 항목만 `true`로 원자
   변경한다. identity·owner·mode·이전 SHA가 다르면 덮어쓰지 않고 성공 뒤 `env_committed`를 기록한다.
4. 재생성 직전 `recreate_started`를 기록하고 frozen Compose와 active PinVi immutable image로 PinVi API만
   `--no-deps --force-recreate --no-build
   --pull never --wait` 재생성한다. image generation은 바꾸지 않는다.
5. PinVi startup cache-target readiness가 contract pin, migration, epoch, backlog/DLQ, fixed snapshot
   count/Merkle/high-watermark를 통과하는지 확인한다.
6. 기존 full compatible-pair validator로 Map runtime 네 개와 PinVi API의 image ID, source provenance,
   singleton/container name/readiness와 모든 protected secret isolation을 다시 attestation한다.
7. n150 causal canary로 고유 command→Map event→PinVi DB/cache 반영→ACK의 인과 사슬과 lag 0, DLQ 0,
   initial receipt의 count/Merkle 일치를 확인한다. journal transaction ID를 canary run ID로 재사용하고
   running ordinary API container에서 `docker exec`로 실행한다. manager는 bounded timeout과 exact 단일 JSON
   parser를 적용하고 raw stdout/stderr를 보관·반환하지 않는다. 고정 synthetic target UUID, 서로 다른
   run/target/command/event UUID, 연속 generation/relay order, 증가하는 cache generation, backlog/dead 0,
   cursor/count/Merkle 수렴을 확인한 뒤 cutover ID·active pair hash·contract generation을 결박한다. 하나라도
   실패하면 rollback으로 전이한다.
8. 검증 증거를 `verified`, terminal 결과를 `committed`로 각각 fsync한 뒤 성공을 반환한다.

재생성 또는 attestation 실패 시 `rollback_preparing`을 먼저 기록하고 frozen 이전 `.env`를 복원한 뒤
`rollback_env_restored`를 기록한다. `rollback_recreate_started` 뒤 같은 immutable image의 PinVi API를
`sync=false`로 재생성하고 canonical Compose health smoke와 pair attestation을 통과한 뒤 terminal
`rolled_back`을 기록한다. 복구도 실패하면 마지막 phase와 관련
evidence를 보존하고 추가 mutation을 차단한다. active/rollback compatible-pair manifest는 이 환경 전환에서
변경하지 않는다.

backup부터 forward terminal phase까지 하나의 process가 하나의 C6c 전역 lock critical section을 유지한다.
crash 때문에 lock을 새로 획득하는 경우 canonical `.env`/Compose, active와 rollback pair, DB backup/schema
identity, initial receipt를 모두 다시 freeze하고 window journal의 transaction identity와 exact 대조한 뒤에만
같은 phase resume 또는 coupled rollback을 수행한다.

## 6. 완료 증거와 금지 사항

완료 증거는 initial/enable receipt ID와 SHA, frozen env/raw Compose/resolved Compose/role-binding logical SHA,
active/rollback pair logical hash, old/new env SHA, 각 durable journal phase, Map/PinVi source revision,
contract pin, safe count/Merkle/published 값, startup/pair attestation 결과다. 원문 token, resolved Compose,
Docker inspect 원문, `.env` bytes, bearer header는 audit·JSON 출력·로그·receipt에 남기지 않는다.

다음은 금지한다.

- ordinary PinVi API에 restore-fence/recovery token 주입
- initial-cutover runner에 쓰지 않는 restore-fence token 주입
- Map principal registry에 원문 token 저장
- registry JSON이나 개별 token digest를 receipt/audit/log에 출력
- elevated recovery token을 Docker `-e value` 또는 Compose environment로 전달
- `docker compose config` resolved 결과를 파일·CI artifact·로그로 보존
- C6c 전역 lock 밖에서 `.env`, PinVi API runtime 또는 receipt 변경
- mutable tag로 runner/recreate 실행
- initial receipt 없이 sync enable
- `enable_preparing` fsync 없이 `.env`를 `true`로 변경
- cache-target generation/health/pin 검증을 통과하지 않은 stale rollback pair 보존 또는 실행
- causal canary 실패를 무시하고 terminal enable commit
- pair attestation 실패를 warning으로 낮춰 계속 진행
- old v4 pair를 일반 rollback slot에 둔 채 generation 7 initial을 시작
- migration 뒤 DB를 복구하지 않고 image만 old generation으로 rollback
- non-terminal window journal이 있는데 다른 manager mutation을 실행
- stored host receipt만 보고 live DB epoch/schema/cutover/convergence 재검증을 생략
- production entrypoint에 호출자 제공 attestor, canary, rollback smoke를 주입
