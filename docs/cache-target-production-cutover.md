# T-VN-41 cache-target production cutover runbook

이 문서는 Map cache-target stream과 PinVi generation/outbox consumer를 production에서 처음 연결하는
manager 제품 경로의 정본이다. 실제 credential·호스트·도메인은 기록하지 않는다. 민감 접속값은
gitignore된 `docs/deploy-runbook.local.md`와 canonical `.env`에만 둔다.

## 1. 고정 배포 계약

Map API에만 다음 registry를 전달한다.

- `KOR_TRAVEL_MAP_API_CACHE_TARGET_SERVICE_PRINCIPALS`: command, consumer,
  restore-fence, recovery 네 principal의 token SHA-256 digest, 고유 principal ID, 공통 consumer ID,
  최소 scope와 `pinvi` external system allowlist를 담은 JSON 배열

PinVi ordinary API에는 정확히 다음 7개만 전달한다.

- `PINVI_KOR_TRAVEL_MAP_CACHE_TARGET_SYNC_ENABLED`
- `PINVI_KOR_TRAVEL_MAP_CACHE_TARGET_COMMAND_TOKEN`
- `PINVI_KOR_TRAVEL_MAP_CACHE_TARGET_CONSUMER_TOKEN`
- `PINVI_KOR_TRAVEL_MAP_CACHE_TARGET_CONSUMER_ID`
- `PINVI_KOR_TRAVEL_MAP_CACHE_TARGET_EXPECTED_OPENAPI_SHA256`
- `PINVI_KOR_TRAVEL_MAP_CACHE_TARGET_EXPECTED_SOURCE_REVISION`
- `PINVI_KOR_TRAVEL_MAP_CACHE_TARGET_EXPECTED_CONTRACT_GENERATION`

`PINVI_KOR_TRAVEL_MAP_CACHE_TARGET_RESTORE_FENCE_TOKEN`과
`PINVI_KOR_TRAVEL_MAP_CACHE_TARGET_RECOVERY_TOKEN`은 ordinary API, PinVi Web/Dagster, Map UI/Dagster와
다른 장기 실행 container에 전달하지 않는다. 전용 initial-cutover runner에는 실제 사용하는 recovery
token만 실행 시간 동안 전달한다. restore-fence token은 Map registry와 향후 별도 restore 작업 경계에만
보관하며 initial-cutover runner에도 전달하지 않는다.

네 role token은 모두 32자 이상·공백 없음이며 서로 달라야 한다. 기존 Map/PinVi service, admin,
ops read/cancel token과도 같을 수 없다. Map registry에는 원문 token을 넣지 않고 lowercase SHA-256
digest만 넣는다. command principal은 `cache-target:consumer`, consumer principal은
`cache-target:read`·`cache-target:claim`·`cache-target:ack`·`cache-target:nack`·
`cache-target:snapshot`, restore-fence principal은 `cache-target:restore-fence`, recovery principal은
initial begin/seal에 필요한 `cache-target:recovery`만 가진다. `cache-target:recovery-replay`는 향후 replay
작업이 실제 필요할 때 별도 검토하며 initial-cutover 권한에 미리 포함하지 않는다.

## 2. 사전 조건

1. Manager, Map, PinVi exact source revision이 review된 compatible pair이고 manifest active image 다섯 개가
   local Docker에서 immutable ID로 존재하는지 확인한다.
2. C6c canonical `.env`, Compose path, project name, source revision evidence와 production root owner/mode를
   검증한다. `.env`의 cache-target sync는 literal `false`여야 한다.
3. raw Compose에 credential literal이 없고 resolved Compose에서 registry와 ordinary 7개 변수가 허가된
   service의 정확한 environment path에만 있는지 확인한다.
4. running Map API에는 registry만, running PinVi API에는 ordinary 7개만 있고 restore-fence/recovery 원문이
   없는지 inspect digest 비교로 확인한다. 다른 container에는 네 role 이름·값이 없어야 한다.
5. Map/PinVi DB migration head, active compatible pair readiness, backlog/DLQ/epoch 전제와 pinned
   OpenAPI/source/contract generation을 확인한다.

사전 조건은 `/run/lock/kor-travel-docker-manager/global-mutation.lock` 획득 뒤 frozen canonical
`.env`/Compose identity로 다시 검증한다. lock 밖 결과를 mutation 권한으로 사용하지 않는다.

## 3. 최초 cutover phase

1. operator가 재사용 가능한 고정 `cutover_id`, positive expected restore epoch와 감사 reason을 준비한다.
2. manager 전용 command가 C6c 전역 lock을 획득하고 non-committed 기존 receipt를 먼저 분류한다.
3. active PinVi immutable image로 일회성 runner를 시작한다. ordinary command/consumer와 전용 recovery
   credential만 runner process에 전달하며 argv·stdout/stderr에는 값을 넣지 않는다. restore-fence
   credential은 이 runner에 전달하지 않는다.
4. runner는 `sync=false`를 확인하고 pinned contract, source count/Merkle, recovery reconciliation과 completion을
   수행한다. 같은 cutover ID retry는 PinVi의 durable ledger와 idempotency key로 같은 결과에 수렴해야 한다.
5. manager는 원문 credential과 resolved environment를 제외한 cutover ID, request ID, epoch, count,
   Merkle root, published count, contract pin, active image/source identity를 owner-only receipt에 fsync 후
   원자 replace한다. runner container가 제거됐는지도 확인한다.

runner 실패, signal, lock 경합, 결과 parse 오류, foreign receipt는 sync를 열지 않는다. 원격 성공 여부가
불확실하면 같은 cutover ID로 재실행해 PinVi durable state가 결과를 확정하게 한다.

## 4. sync enable phase

1. committed initial-cutover receipt가 frozen env와 active compatible pair에 exact하게 맞는지 확인한다.
2. canonical `.env`의 `PINVI_KOR_TRAVEL_MAP_CACHE_TARGET_SYNC_ENABLED=false` 한 항목만 `true`로 원자
   변경한다. identity·owner·mode·이전 SHA가 다르면 덮어쓰지 않는다.
3. frozen Compose와 active PinVi immutable image로 PinVi API만 `--no-deps --force-recreate --no-build
   --pull never --wait` 재생성한다. image generation은 바꾸지 않는다.
4. PinVi startup cache-target readiness가 contract pin, migration, epoch, backlog/DLQ, fixed snapshot
   count/Merkle/high-watermark를 통과하는지 확인한다.
5. 기존 full compatible-pair validator로 Map runtime 네 개와 PinVi API의 image ID, source provenance,
   singleton/container name/readiness와 모든 protected secret isolation을 다시 attestation한다.
6. enable terminal receipt를 먼저 commit한 뒤 성공을 반환한다.

재생성 또는 attestation 실패 시 frozen 이전 `.env`를 복원하고 같은 immutable image의 PinVi API를
`sync=false`로 재생성·검증한다. 복구도 실패하면 receipt와 관련 evidence를 보존하고 추가 mutation을
차단한다. active/rollback compatible-pair manifest는 이 환경 전환에서 변경하지 않는다.

## 5. 완료 증거와 금지 사항

완료 증거는 initial/enable receipt ID와 SHA, active pair logical hash, Map/PinVi source revision,
contract pin, safe count/Merkle/published 값, startup/pair attestation 결과다. 원문 token, resolved Compose,
Docker inspect 원문, `.env` bytes, bearer header는 audit·JSON 출력·로그·receipt에 남기지 않는다.

다음은 금지한다.

- ordinary PinVi API에 restore-fence/recovery token 주입
- initial-cutover runner에 쓰지 않는 restore-fence token 주입
- Map principal registry에 원문 token 저장
- `docker compose config` resolved 결과를 파일·CI artifact·로그로 보존
- C6c 전역 lock 밖에서 `.env`, PinVi API runtime 또는 receipt 변경
- mutable tag로 runner/recreate 실행
- initial receipt 없이 sync enable
- pair attestation 실패를 warning으로 낮춰 계속 진행
