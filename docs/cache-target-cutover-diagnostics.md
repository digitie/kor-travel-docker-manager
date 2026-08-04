# cache-target cutover 사전 진단 설계

## 1. 목적과 결정

T-VN-41의 최초 production cutover는 backup, restore rehearsal, candidate migration,
initial event, sync enable, causal canary를 하나의 fail-close 전환으로 묶는다. 이 경계는
유지한다. 다만 production DB의 `pg_dump` 경고 정책, restore rehearsal, 또는 재기동 직후
readiness처럼 **외부 이벤트 전에도 오래 걸리는 실패**가 발생하면, 현재 결합 window를
여러 번 시작했다가 pre-forward rollback하는 방식은 운영자가 원인을 분리하기 어렵고
서비스 중단 시간을 반복한다.

따라서 Docker Manager는 cutover를 우회하는 별도 배포 도구를 만들지 않고,
`cache-target diagnose`라는 read-mostly 사전 진단 경로를 소유한다. 진단의 성공은
cutover 성공을 뜻하지 않으며, cutover의 최신 backup 증적을 재사용하지도 않는다. 성공은
"현재 고정된 runtime·DB 도구·schema에서 전환에 필요한 검사 primitive가 동작한다"는
제한된 capability 증거다.

| 책임 | 소유자 |
|---|---|
| DB backup, scratch restore, writer fence, journal, retry/abort와 cutover 허용 판단 | Docker Manager |
| Map H35 migration/CSV/GC/verify의 데이터 의미와 typed helper receipt | Map |
| PinVi cache-target schema, initial/enable/canary의 API·데이터 의미 | PinVi |
| authenticated smoke endpoint의 HTTP 계약 | 각 서비스 |

즉 PinVi의 admin `GET` 응답 의미는 PinVi가 소유하지만, Compose 재기동 뒤 그 안전한
GET을 언제까지 기다리고 어느 실패에서 cutover를 중단할지는 Docker Manager의
orchestration 책임이다. 서비스에 Manager 전용 retry endpoint나 배포 상태를 추가하지
않는다.

이 설계는 `docs/cache-target-production-cutover.md`의 writer fence, external-event
boundary, C6c 전역 lock 및 owner-only journal 계약을 대체하지 않고 사전 gate로 보강한다.

## 2. 명령과 상태 모델

새 명령은 다음 한 가지다.

```bash
ktdctl cache-target diagnose --diagnostic-id <canonical-lowercase-uuid> --json
```

명령은 production에서 C6c 전역 lock을 취득한 뒤 canonical `.env`, raw/resolved
Compose logical SHA, active/rollback pair, Manager release, DB role·container·schema
revision, `pg_dump`/`pg_restore` major version을 freeze한다. 실제 값, DSN, credential,
resolved Compose 원문은 receipt나 출력에 넣지 않는다.

진단 journal은 cutover journal과 다른 owner-only `0600` 파일이며, 새 진단 UUID와
다음의 input logical SHA를 가진다.

```text
prepared
  -> writers_fencing
  -> writers_draining
  -> writers_drained
  -> writers_stopping
  -> writers_fenced
  -> map_application_checked
  -> map_dagster_checked
  -> pinvi_checked
  -> runtime_smoke_checked
  -> completed
```

실패는 어느 phase에서도 `failed` terminal로 한 번만 기록한다. 진단은 candidate image를
build·기동하지 않고, `.env`/manifest/active pair/Map·PinVi 운영 데이터를 바꾸지 않으며,
`external_event_count`는 항상 `0`이어야 한다. 이 값이 0이 아니거나 durable record에
없는 경우 진단 자체를 security failure로 취급한다.

`writers_fencing`은 production backup과 같은 exact writer registry 및 foreign-writer
검사를 재사용한다. 모든 검사가 끝나거나 실패하면 기존 **pre-bootstrap** pair의 exact
상태를 manifest와 다시 대조하고 writer를 재기동한다. 이때 diagnostic receipt identity는
후속 candidate bootstrap에 쓸 canonical transaction으로 유지하되, old runtime 재-attestation만
그 transaction의 raw Compose·external input에서 old pair image와 source provenance를
materialize한 frozen 문서로 수행한다. 이 시점의 old pair는 generation bootstrap이 아직
수행되지 않았으므로 tracked release pin과 같을 필요가 없다. release pin은 후속 cutover가 새
candidate를 build·bootstrap하기 직전에만 강제한다. 이 경계를 섞으면 diagnostic이 성공할 수
없거나 receipt가 즉시 stale해져 cutover gate 자체가 막히므로, 진단은 old pair를 새 release로
승격하거나 manifest를 바꾸지 않는다. 이로써 진단이 잠깐 writer를 멈추더라도 forward runtime,
migration, initial event, sync enable을 실행하지 않는다.

### Diagnostic ID 수명

canonical journal 경로에는 현재 diagnostic 하나만 둔다. 같은 ID로 재호출한 terminal
journal은 기존 결과만 재보고하며, 같은 ID의 nonterminal journal은 이전 process crash로
간주해 fail-close한다. 반대로 operator가 **새 UUID**를 명시하면 C6c 전역 lock 안에서
기존 journal을 먼저 typed read한다. nonterminal이면 `aborted`로 terminal 전이·fsync한다.
`writers_stopping`부터의 실제 rehearsal은 terminal journal과 정확히 같은 attempt record를
기록 또는 대조·fsync한 뒤 owner-only archive로 원자 이동한다. 반대로
`prepared`/`writers_fencing`의 quiescence preflight는 writer를 멈추거나 DB/runtime을 바꾸기
전이므로 archive만 하고 expensive rehearsal attempt budget은 소모하지 않는다. 그 다음에만
새 canonical journal을 쓴다.

`writers_stopping`은 `docker compose stop`을 호출하기 **직전** fsync하는 durable boundary다.
stop의 부분 실패 또는 process crash로 global writer-fence digest를 쓰지 못해도 이 phase가
남으므로, mutation 가능성이 있는 journal을 preflight로 오분류해 budget을 우회하지 않는다.

### Dagster writer-drain

`writers_fencing`만으로는 schedule/sensor daemon이 그 직후 새 run을 만들 수 있다. 이
race를 operator가 Manager 밖의 GraphQL 호출이나 일반 Compose 명령으로 해결하면 C6c
보호 경계와 journal 증적이 무너진다. 따라서 diagnostic과 cutover의 initial writer fence는
다음의 durable drain을 먼저 수행한다.

1. `writers_fencing`은 writer registry·current pair·Map drain capability를
   preflight한다. 이 단계에는 schedule/sensor·container·DB mutation이 없다.
2. `writers_draining`을 먼저 fsync한다. 이 phase는 Map control plane mutation을
   시작할 수 있는 경계이므로 `prepared`/`writers_fencing`과 달리 diagnostic attempt
   budget을 소모한다.
3. Map이 **writer-drain lease**를 소유한다. Manager는 frozen Compose의 Map control
   command만 호출하고, 그 명령은 Map DB에 이전 schedule/sensor 상태를 durable하게
   보존한 뒤 새 run 생성을 pause한다. 반환값은 opaque lease ID와 secret-free receipt
   digest뿐이다. Manager journal에는 run ID·schedule 이름·GraphQL 원문·credential을
   저장하지 않는다.
4. Map은 이미 실행 중인 Map Dagster run을 bounded grace window에서 terminal 상태가
   되기를 기다리고, 만료 때만 Map-owned typed terminal-cancel 정책으로 수렴시킨다.
   lease가 active이고 run count가 0일 때만 Manager가 `writers_drained`를 fsync한다.
   timeout은 terminal abort이며, data 보존을 위한 DB restore를 시도하지 않는다.
5. `writers_drained` 뒤 기존 `writers_stopping` fence를 수행한다.
   full writer stop 뒤에도 DB transaction 또는 Dagster run이 남으면 fail-close한다.
6. diagnostic의 성공·실패·예외와 superseded non-terminal journal recovery 모두에서
   Manager는 Map의 exact lease를 restore·attest한 뒤에만 archive/resume한다. journal
   phase가 `writers_draining` 이상이면 이 pre-backup recovery가 성공하기 전
   coupled DB rollback이나 archive를 허용하지 않는다.

Map control command는 기존 cache-target 4-token registry를 재사용하거나 다섯 번째
장기 token을 추가하지 않는다. Map runtime 안에서 실행되는 narrow typed command와
Map-owned durable lease가 유일한 control boundary이며, Manager의 일반 Compose/외부
GraphQL 우회는 금지한다.

이 흐름은 개발 중간 데이터의 보존 수단이 아니다. 데이터 유실은 file source 또는 ETL
재실행으로 재생성한다. 다만 **최종 DB schema**에서의 backup/restore rehearsal과 실제
cutover backup은 계속 필수이며, drain은 그 검증을 race 없이 시작하기 위한 runtime
제어 경계다.

archive의 이름 충돌·owner/mode·내용 재검증·directory fsync 중 하나라도 실패하면 새 진단을
시작하지 않는다. 따라서 receipt/attempt는 삭제되지 않으며, archive 직후 새 journal을 쓰기
전에 process가 죽어도 다음 새 UUID가 안전하게 진행할 수 있다. 기존 `completed` receipt도
operator가 새 UUID 진단을 요청한 경우에만 archive한다. 이는 기존 receipt의 freshness gate를
의도적으로 무효화하므로, 새 receipt가 완료되기 전에는 cutover를 계속 거부한다.

## 3. DB별 stage와 비밀 없는 증적

각 DB role은 직렬로 다음 stage를 수행한다. 세 DB는 disk I/O와 scratch database 생성
부하를 예측 가능하게 유지하기 위해 기본적으로 병렬 실행하지 않는다.

| stage | 작업 | 성공 증적 | 실패 분류 예 |
|---|---|---|---|
| `source_archive` | custom `pg_dump` 생성 | archive byte bucket·SHA-256 | `subprocess_nonzero`, `stderr_policy_rejected`, `timeout` |
| `source_schema_inventory` | schema-only logical inventory | SHA-256 | `subprocess_nonzero`, `stderr_policy_rejected`, `timeout` |
| `source_data_inventory` | data-only deterministic inventory | SHA-256 | `subprocess_nonzero`, `stderr_policy_rejected`, `timeout` |
| `archive_structure` | archive list 검증 | pass | `archive_invalid` |
| `scratch_create` | owner 전용 scratch DB 생성 | opaque scratch identity | `admin_command_failed` |
| `scratch_restore` | archive restore | pass | `restore_failed`, `timeout` |
| `scratch_schema_inventory` | source schema와 비교 | equal | `inventory_mismatch` |
| `scratch_data_inventory` | source data와 비교 | equal | `inventory_mismatch` |
| `scratch_cleanup` | scratch DB 삭제·absence 확인 | pass | `cleanup_failed` |

receipt는 `role`, `stage`, `status`, `failure_class`, bounded elapsed time, archive와
inventory digest, opaque scratch identity만 기록한다. stderr/stdout, table 이름, command
argv, database name, container inspect, backup path, credential, DSN은 **기록·반환·로그
출력하지 않는다**. stderr가 정책상 거부된 경우에도 `stderr_policy_rejected`만 기록한다.

원문이 필요한 개발 조사에는 production receipt를 열람하지 않는다. 같은 `pg_dump` major
version을 고정한 integration fixture에서 해당 경고 grammar를 재현해 단위 테스트로 추가한다.
지원하는 circular-FK advisory는 정확한 heading, 하나 이상의 detail, 그리고 해당 PostgreSQL
major가 내는 정확한 hint sequence로만 허용한다. 다른 warning, schema-only warning, nonzero
exit와 decode 오류는 계속 fail-close다. 이 규칙은 T-VN-41에서 Map data logical inventory가
중단된 원인을 `stderr_policy_rejected`와 다른 subprocess failure class로 분리하도록 한다.

성공과 실패 모두 archive는 root-only temporary 영역에서 제거하고 scratch DB는 absence를
확인한다. cleanup 실패는 성공으로 낮추지 않는다. durable receipt에는 artifact 위치가 없으므로
다음 진단이 민감 backup 파일을 재발견하거나 재사용할 수 없다.

## 4. cutover와의 결박·재사용 규칙

`cache-target cutover`는 새 forward window를 열기 전에 다음 모두가 같은 input logical
identity로 만족하는 `completed` 진단 receipt를 요구한다.

- Manager release와 `pg_dump`/`pg_restore` major version
- active/rollback pair 및 raw/resolved Compose logical SHA
- DB role/container binding, owner/admin role digest, schema revision
- protected writer registry digest와 canonical smoke contract revision

receipt가 없거나 `failed`, terminal이 아니거나 어느 identity가 달라졌거나 만료되면 new
cutover를 시작하지 않는다. 만료 시간은 운영 정책으로 짧게 고정하며, schema migration,
pair/Compose 변경, Manager deploy, DB 도구 major 변경은 시간과 무관하게 즉시 무효화한다.

사전 진단의 archive/row inventory는 절대로 forward backup으로 재사용하지 않는다. 실제
cutover는 writer fence 뒤 최신 data에 대해 새 backup과 restore rehearsal을 다시 만들고,
그 receipt만 coupled rollback에 사용한다. 이 분리는 data freshness와 빠른 capability
diagnosis를 동시에 보장한다.

forward window 안의 backup stage도 같은 typed stage receipt를 journal에 즉시 저장한다.
pre-forward failure는 기존 coupled rollback으로 복구하되, 최종 journal에는 일반적인
`rolled_back`만 남기지 않고 마지막 `failure_stage`와 `failure_class`를 남긴다. 이 두 값은
다음 실행의 원인 분기에는 쓰되 어떤 경로에서도 raw process output으로 확장하지 않는다.

## 5. retry·시간 budget·operator 경계

재시도는 실패를 감추는 기능이 아니라 명시적인 transport readiness 정책이다.

- authenticated canonical smoke의 첫 signed/read 요청은 exact `ConnectionRefusedError`만
  healthcheck start-period와 같은 bounded window에서 재시도한다.
- session이 확립된 bodyless idempotent GET만, 명시적으로 opt-in된 짧은 `ConnectionRefusedError`/
  timeout 재시도를 가질 수 있다.
- login timeout, 모든 POST, cancel·restore·initial·enable과 기타 mutating request는 즉시
  fail-close한다. DB archive/inventory/restore stage 역시 자동 재시도하지 않는다.

하나의 diagnostic input identity에는 자동 재시도를 하지 않는다. operator가 명시적으로
새 diagnostic UUID를 시작할 수 있는 횟수는 production policy로 고정한다(초기값:
24시간 내 2회 — abort budget은 attempt 횟수 기준이며 attempt당 고정 시간 상한을
강제하지 않는다). budget을 넘기거나 같은 `failure_stage`/`failure_class`가 재현되면
Manager는 `aborted` terminal을 남기고 cutover 시작을 거부한다. operator는 해당
class가 재현되는 integration regression을 포함한 수정 PR과 새 진단 receipt 없이는
다시 실행할 수 없다.

n150 실측(2026-08-03): 대용량 테이블(1,780만 행) 하나의 `pg_restore`만으로도 약
97분이 걸릴 수 있어, DB archive/restore stage의 subprocess timeout은
`_DATABASE_RESTORE_TIMEOUT_SECONDS`(3시간, `cache_target_backup.py`)로 고정했다.
즉 하나의 `cache-target diagnose` 호출이 role 3개를 순차 진단하는 동안 정상적으로
여러 시간 걸릴 수 있다 — operator는 "각 60분" 같은 고정 시간을 기대하지 말고,
진행 중인 journal의 phase가 실제로 전진하고 있는지(non-terminal이지만 stuck이
아닌지)로 판단해야 한다.

이는 external event 이전에만 적용한다. 진단과 pre-forward backup은 external event가 0인
경우만 abort/rollback할 수 있다. initial runner 호출 직전 durable external-event boundary가
기록된 뒤에는 기존 규칙대로 old DB restore를 금지하고 동일 transaction resume 또는
fix-forward만 허용한다.

## 6. 구현 작업 단위와 검증

이 문서는 구현 순서도 고정한다. 각 항목은 별도 PR로 검증 가능하며, 구현은 이 문서의
receipt/phase 계약을 먼저 만족해야 한다.

1. **T-049A — typed diagnostic model과 storage**: sealed stage/failure enum, owner-only atomic
   journal/receipt, secret-redaction 및 stale-input rejection을 구현한다.
2. **T-049B — DB diagnostic primitive**: 현재 backup helper를 typed stage 결과로 분해하고
   source/scratch cleanup, supported `pg_dump` advisory grammar 및 exact fail-close test를 만든다.
3. **T-049C — writer fence와 orchestration**: global lock, foreign writer 검사, 3-role serial
   diagnostic, runtime re-attestation, abort budget을 `ktdctl cache-target diagnose`에 결선한다.
4. **T-049D — cutover gate와 failure propagation**: fresh diagnostic receipt 없이는 forward
   window를 열지 않게 하고, window journal에 마지막 safe stage/class를 남긴다.
5. **T-049E — n150 production rehearsal**: sync=false에서 diagnostic을 한 번 실행하고
   receipt identity·artifact cleanup·runtime recovery를 확인한 뒤에만 final initial cutover를
   한 번 실행한다.

필수 회귀는 다음과 같다.

- 각 DB/stage의 success, timeout, nonzero, unexpected stderr, cleanup failure와 raw payload
  비노출
- one/two-hint circular-FK advisory의 지원 major fixture 및 unknown warning 거부
- receipt tamper, foreign/stale identity, expired receipt, concurrent diagnose/cutover, crash resume
- foreign writer, diagnostic 실패 뒤 runtime exact recovery, external event 0 불변식
- safe GET retry의 opt-in 경계와 POST/timeout fail-close
- backend 전체, Ruff, strict mypy, canonical Compose 계약 및 n150 destructive live rehearsal

## 7. 운영 전환

이 문서가 먼저 병합된 뒤 T-049A부터 구현한다. 그 전에는 `cache-target cutover`를 반복 재시도하지
않는다. 현재 T-VN-41의 `source_data_inventory` failure는 이 설계가 요구하는 typed stage/class
증적과 integration fixture로 정확히 분류한 뒤 해결하고, diagnostic을 통과한 exact release에서만
final cutover를 한 번 수행한다.
