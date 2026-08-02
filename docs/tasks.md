# TASKS — 개발 태스크 백로그

이 문서는 `kor-travel-docker-manager`의 진행 중/대기 작업만 관리한다. 완료된 작업은
[`docs/tasks-done.md`](tasks-done.md)로 분리한다.

- 완료: `[x]`
- 진행 중: `[/]`
- 미진행: `[ ]`

---

## 작업 현황 요약

| 태스크 ID | 작업 항목 | 상태 | 완료 날짜 | 비고 |
|:---|:---|:---:|:---:|:---|
| **T-031** | Map↔PinVi C6c ops read/cancel principal 배포 결선 | `[/]` | - | 구현·기존 live 충족, T-045 회전과 새 official deploy 미완료 |
| **T-045** | Map UI credential rotation을 `ktdctl`의 audited production workflow로 제품화 | `[/]` | - | 값 비노출·원자 갱신·UI-only recreate·복구·감사 |
| **T-046** | `pinvi-pair deploy`/`capture`의 `--wait-timeout` 하드코딩 제거 (issue #88) | `[/]` | - | 마이그레이션 수반 배포·bootstrap의 오발동 rollback 방지, n150 실제 마이그레이션 배포 검증 대기 |
| **T-047** | compatible-pair canonical Compose readiness 계약 정렬 | `[/]` | - | healthcheck 선언 여부 기반 typed policy·실제 Compose 회귀 |
| **T-048** | T-VN-41 cache-target production manifest와 최초 cutover 제품화 | `[/]` | - | 4-role 격리·default-off runner·receipt·sync enable attestation |

---

## 진행 순서

1. `tasks.md`와 `tasks-done.md`를 최신 완료/미완료 상태로 정리한다.
2. `kor-travel-concierge`는 `conc`, PinVi는 `srv` 별칭을 기준으로 안내한다.
3. 다음 앱 target 추가 시 `config/docker-targets.yml`, `docker-compose.yml`, 포트 문서, API/CLI 테스트를 함께 갱신한다.
4. 병행 작업 충돌을 줄이기 위해 각 PR 전후로 `main` rebase를 수행한다.

---

## 태스크 세부 내역

### T-031: Map↔PinVi C6c ops read/cancel principal 배포 결선

- [x] read/cancel principal을 Map API와 PinVi API에만 결선하고 Map UI·Dagster·daemon,
      PinVi Web·Dagster에는 전달하지 않는 최소 권한 계약을 raw/resolved/runtime 단계에서
      고정했다.
- [x] production 일반 `ensure`와 container action/config/reset/direct Compose 경로에서
      Map runtime 4종과 PinVi API 변경을 차단하고, 전역 lock을 소유하는
      `pinvi-pair capture/deploy/rollback`만 다섯 immutable image 세대를 변경하도록 했다.
- [x] compatible-pair manifest v4가 active/rollback의 Map API·UI·Dagster web·daemon과
      PinVi API image ID, clean source revision, contract generation을 함께 기록한다.
      mixed generation은 시작 세대 복구 또는 다섯 runtime halt로 수렴한다.
- [x] Map UI username·PBKDF2 hash·session secret을 canonical Compose와 exact runtime에
      결박하고 manager smoke 평문은 container에 주입하지 않는 회귀 계약을 고정했다.
- [x] PR #54~#57, #64, #67, #69, #73의 리뷰·CI를 통과했다. 2026-07-26 C7 공식
      gate에서 read-auth `7/7`, KMA active/cap/empty 각 `2/2`, schedule-write `2/2`,
      POI-cache-causal `2/2`, `BLOCKED` 0건, 상태 복구와 active target 0을 확인했다.
- [x] 2026-07-27 compatible-pair에서 C6c principal smoke와 targeted live를 통과했다.
- [ ] 현재 canonical Manager `.env`의 Map UI hash/session은 running UI와 일치하지만
      manager smoke 평문은 그 PBKDF2 hash를 검증하지 못한다. 따라서 새 official deploy
      preflight는 container mutation 전에 중단된다. 수정은 T-045가 소유한다.
- [ ] n150에서 Map UI password hash와 session secret을 함께 회전하고, 새 manager smoke
      평문↔hash 일치, 이전 session 무효화, login→`/ops/datasets` 보호 GET→logout→재차단을
      확인한다.
- [ ] 회전 뒤 최신 exact Map·Manager·PinVi 조합으로 official compatible-pair deploy와
      cross-repo smoke·targeted live를 다시 통과한 뒤 완료 이력으로 옮긴다.

### T-045: Map UI credential rotation을 `ktdctl`의 audited production workflow로 제품화

- [ ] production에서만 실행되는 전용 `ktdctl` command를 추가하고 C6c 전역 lock,
      canonical manager checkout/Compose/`.env`, 실행 중 Map UI identity와 immutable image를
      mutation 전에 fail-closed로 검증한다. production C6c/rotation mutation은
      `/run/lock/kor-travel-docker-manager/global-mutation.lock`의 root-only hardened lock과
      `/var/lib/kor-travel-docker-manager/<compose-project>/compatible-pair-v4.json` manifest를
      공유한다. production source는 root-owned/non-writable checkout과 root-owned
      `/opt/kor-travel-docker-manager` package, `/usr/local/sbin/ktdctl-map-ui-auth-rotate`
      trusted launcher, root-owned `.ktdm-source-revision` exact git SHA 파일을 필수 evidence로 제공하며,
      `KTDM_MANAGER_SOURCE_REVISION`은 있을 때 파일과 일치해야 하는 보조 검증값이다. trusted release
      설치는 source owner 권한에서 clean checkout의 tracked `git archive`를 만들고 root-owned offline
      wheelhouse만 소비한다. staging exact source에서 backend wheel을 offline build해 설치하고, 기존
      또는 명시 deployment-owner 0600 `.env`를 보존한 뒤 isolated wheel venv, wheelhouse SHA, backend
      wheel SHA, wheel `RECORD` SHA, release manifest revision을 결박해 app root와 launcher까지
      activation/rollback 가능한 제품 경로로 제공한다. wheelhouse는 root `pip`가 읽기 전에 ancestor와
      각 wheel의 owner/mode/nlink/inode/digest를 snapshot하고 각 소비 단계 뒤 exact 재검증한다.
      installer도 rotation/pair workflow와 같은 host-global lock을 source `.env` snapshot부터 app root·
      launcher activation/rollback 종료까지 소유한다. lock 전후 `.env` identity·mode·owner·SHA가
      달라지면 설치를 시작하지 않는다. lock 안에서 canonical `.env`를 `O_NOFOLLOW` read-only FD로
      한 번 열어 exact bytes를 끝까지 보유하고, root-only 0700 staging에는 그 FD에서만 복사한다.
      따라서 검증 뒤 경로를 바꾸거나 같은 경로에 다른 inode를 끼워도 활성화할 수 없다. 처음 고정한
      source revision exact commit만 archive·기록한다. PID별 임시 경로 대신 host-global 고정
      transaction state가 old app/launcher evidence와 target revision, 새 launcher digest,
      `preparing|prepared|committed` phase를 fsync한다. 다음 실행은 lock 안에서 non-committed
      transaction을 exact rollback하고 committed cleanup residue를 idempotent GC한 뒤에만 새 설치를
      허용하며, 분류할 수 없는 legacy/foreign residue는 mutation 전에 fail-close한다.
      staging venv의 `ktdctl`은 canonical installed root를 고정한 실행 가능한 entrypoint로 만들고
      바뀐 bytes를 wheel `RECORD` digest·size와 release manifest에 다시 결박한다.
- [ ] 새 password 평문, PBKDF2 hash, session secret을 argv·stdout/stderr·audit·child
      environment·Docker metadata에 노출하지 않는다. PBKDF2 format과 평문↔hash 일치를
      독립 검증하고 hash와 session secret은 항상 함께 회전한다.
- [ ] canonical `.env`의 manager smoke 평문 password, Map UI PBKDF2 hash, session secret
      세 항목만 원자 교체하고 같은 immutable image로 Map UI service만
      `--no-deps --force-recreate --no-build --pull never --wait` 재생성한다.
      frozen compose는 mutation/rollback 모두 기존 C6c raw/resolved protected value·system bind·
      secret isolation 검증과 compatible-pair image/provenance/container-name 검증을 통과한 동일
      resolved 문서만 사용한다. 다른 project container와 manifest/image generation은 변경하지 않는다.
- [ ] 새 login→`/ops/datasets` 보호 GET→logout→재차단, 회전 전 session 거부를 검증한 뒤
      durable journal terminal state를 audit보다 먼저 commit한다. forward 실패 시 operator가 입력해
      hash와 대조 완료한 current password만 메모리에서 auth 검증에 쓰고, 이전 hash/image와 새로운
      recovery session secret으로 UI를 복구해 partial-forward session까지 무효화한다. rollback은
      `rollback_preparing` journal을 먼저 fsync한 뒤 root-private `env.recovery`를 생성하고, 어느
      한쪽만 남아도 expected bytes로 양방향 수렴한다. journal은 raw Docker inspect나 secret-bearing
      env를 저장하지 않고 UI/non-UI evidence를 digest만으로 보존한다. crash 재실행 시
      old/new/recovery SHA 각각을 phase matrix로 resume하고 foreign `.env`는 덮지 않으며 terminal
      audit·private artifact는 operation ID 기준 idempotent하게 정리한다. terminal result vocabulary는
      `committed`/`rolled_back`/`aborted`로 제한하고 prepared/orphan abort도 결정적 operation ID로
      cleanup crash 재실행에서 한 번만 기록한다.
- [ ] crash/signal/재실행 recovery journal, foreign container/name collision, `.env` drift,
      Compose/runtime drift, auth 실패, rollback 실패의 음성 회귀를 추가한다. 일반 container config
      변경은 lock 밖 preflight 결과를 신뢰하지 않고 lock 안에서 캡처한 exact Compose baseline으로
      secret interpolation 의미를 다시 검증한 뒤에만 candidate를 만든다.
- [ ] 단일 적대적 리뷰, focused/backend 전체 테스트, Ruff, strict mypy, canonical Compose gate,
      CI green을 통과한 별도 코드 PR을 병합한다.
- [ ] n150에서 전용 command로 실제 회전하고 official compatible-pair deploy, C6c principal
      smoke, C7 targeted live를 통과한 뒤 T-031과 함께 완료 이력으로 옮긴다.

### T-046: `pinvi-pair deploy`/`capture`의 `--wait-timeout` 하드코딩 제거 (issue #88)

kor-travel-map API는 uvicorn 기동 전에 `alembic upgrade head`를 실행한다. `_run_up_stage`가
`docker compose up --wait --wait-timeout 120`을 하드코딩했는데, `CREATE INDEX CONCURRENTLY`
등 non-transactional DDL을 쓰는 긴 마이그레이션(실측 8~18분)은 120초를 넘겨 deploy가 실패로
판정되고 `_recover_previous_pair` rollback이 발동한다 — **마이그레이션이 진행 중인 컨테이너를
그대로 뜯어** durable한 부분 적용 상태를 남긴다. kor-travel-map T-VN-H35(prod alembic
0063→0069) 실행 중 발견되어 배포가 중단됐다.

- [x] `_run_up_stage`가 `wait_timeout: int` 파라미터를 받아 하드코딩 `"120"` 대신 실제
      compose `--wait-timeout` 인자로 쓴다. `deploy_compatible_pinvi_pair` → CLI
      `pinvi-pair deploy --wait-timeout <seconds>`까지 전체 경로를 관통하며, 기본값(120)은
      바뀌지 않아 기존 호출은 회귀 없다.
- [x] **적대적 리뷰(1명)가 발견한 공백을 함께 수정한다**: `pinvi-pair capture`(clean
      bootstrap)도 5개 활성화 단계에서 같은 하드코딩 `wait=True`를 쓰고, 그중
      `bootstrap_map_api`는 정확히 같은 alembic 선행 실행 패턴이다 — 최초 bootstrap은
      전체 마이그레이션 이력을 처음부터 실행할 수 있어 증분 배포보다 오래 걸릴 가능성이
      크다. `capture_compatible_pinvi_pair`에도 같은 `wait_timeout` 파라미터와 CLI
      `--wait-timeout`을 추가하고, 검증 로직은 `_validate_c6c_wait_timeout` 공유 helper로
      중복 없이 통일했다.
- [x] `wait_timeout`은 int·1~3600초 범위만 허용하고(`bool`은 `isinstance(x, int)`가 `True`라
      별도 배제), lock 진입·subprocess 호출보다 먼저 검증한다. rollback/recovery 경로
      (`_recover_previous_pair`, `rollback_compatible_pinvi_pair`)는 의도적으로 그대로
      두어 기본 120초를 유지한다 — rollback 대상은 이미 마이그레이션이 끝난 옛 image라
      진짜 hang을 빠르게 판별하는 쪽이 더 안전하다.
- [x] 회귀 테스트 다수 추가(threading·기본값 유지·경계값 1/3600·잘못된 타입/범위 거부·
      `_run_up_stage`가 실제로 만드는 compose 인자·`_activate_pair_sequentially`의 세 단계
      모두 동일 값 사용·`capture`의 11개 `up --wait` 단계 모두 동일 값 사용). backend
      1067 passed(기존 1049 + 신규 18), ruff 기존 9건 유지, 변경 파일 mypy clean.
- [ ] n150에서 실제 긴 마이그레이션을 수반하는 `pinvi-pair deploy --wait-timeout <n>`
      (또는 `capture`)을 실행해 오발동 rollback 없이 통과하는 것을 확인한 뒤 완료 이력으로
      옮긴다.

### T-047: compatible-pair canonical Compose readiness 계약 정렬 (issue #90)

production compatible-pair deploy의 `_require_services_ready`는 모든 필수 service에
`State=running`과 `Health=healthy`를 동시에 요구한다. 그러나 canonical resolved Compose에서
Grafana, Prometheus, Concierge MCP·Scheduler·UI, Map Dagster daemon 등은 healthcheck를
선언하지 않는다. 그 결과 Docker가 정상 `running`으로 판정한 service도 mutation 전 preflight에서
항상 거부된다.

- [x] readiness policy는 별도 하드코딩 목록이 아니라 transaction에 고정된 canonical resolved
      Compose의 service spec에서 파생한다. 명시적으로 활성화된 healthcheck가 있는 service는
      `running + healthy`, healthcheck가 없거나 Compose 표준으로 비활성화된 service는
      `running`을 요구한다.
- [x] service 누락·종료, healthcheck 선언 service의 빈/`starting`/`unhealthy` health,
      malformed/모호한 healthcheck 정의는 mutation 전에 fail-close한다. image 상속 probe나
      `kill -0 1` 같은 가짜 readiness를 새 계약으로 만들지 않는다.
- [x] unit 회귀에서 선언/미선언/비활성 policy와 missing/exited/unhealthy/starting을 모두
      고정한다. 실제 disposable Docker Compose에서 healthcheck 없는 long-running service와
      실제 healthcheck가 `healthy`인 service 조합은 통과하고, 실제 `unhealthy` service는
      거부되는 것을 검증한다. `ps --all`과 canonical singleton 계약으로 stopped+running
      duplicate/scale/name drift를 fail-close하고 mixed malformed payload도 한 건도 버리지 않는다.
      `KTDM_REQUIRE_DOCKER_INTEGRATION=1` 필수 gate는 Docker/image 부재를 skip하지 않으며,
      cleanup의 container/network/volume residue 0까지 검증한다.
- [x] backend 전체 pytest, Ruff, strict mypy, canonical Compose config, frontend
      type-check/build, 보안 감사를 통과하고 draft PR에 정확한 gate를 기록한다.
- [ ] n150에서는 부모 에이전트가 read-only exact preflight를 재검증한 뒤에만 별도 승인된
      compatible-pair mutation을 수행한다.

### T-048: T-VN-41 cache-target production manifest와 최초 cutover 제품화

- [x] ADR-28과 [`cache-target-production-cutover.md`](cache-target-production-cutover.md)에
      ordinary runtime 최소 권한, 4-role/legacy 상호 분리, default-off 최초 cutover와 receipt,
      sync enable 뒤 compatible-pair attestation 순서를 먼저 고정한다.
- [ ] Map API에는 `KOR_TRAVEL_MAP_API_CACHE_TARGET_SERVICE_PRINCIPALS` JSON registry만 전달하고,
      PinVi ordinary API에는 정확히 `PINVI_KOR_TRAVEL_MAP_CACHE_TARGET_SYNC_ENABLED`,
      `..._COMMAND_TOKEN`, `..._CONSUMER_TOKEN`, `..._CONSUMER_ID`,
      `..._EXPECTED_OPENAPI_SHA256`, `..._EXPECTED_SOURCE_REVISION`,
      `..._EXPECTED_CONTRACT_GENERATION`만 전달한다.
- [ ] command/consumer/restore-fence/recovery 네 token은 서로 및 기존 Map/PinVi
      service/admin/ops token과 달라야 한다. Map registry의 digest·principal·consumer·scope·
      external system을 원문 token과 교차 검증하고 raw/resolved/runtime 비인가 노출을 차단한다.
- [ ] restore-fence/recovery 원문 token은 ordinary PinVi API와 다른 장기 실행 service에 주입하지
      않는다. C6c 전역 lock과 frozen canonical `.env`/Compose/active pair를 검증한 전용
      initial-cutover runner에만 실행 시간 동안 전달하고 종료 뒤 ephemeral container를 제거한다.
- [ ] production `sync=false`에서 고정 cutover UUID/epoch/reason으로 전용 runner를 실행하고,
      contract pin·active image pair·source revision·safe 결과 identity를 secret-free durable receipt로
      원자 기록한다. 같은 입력 retry는 같은 receipt로 수렴하고 다른 입력은 fail-close한다.
- [ ] 성공 receipt가 있을 때만 canonical `.env`의 sync를 `true`로 원자 전환하고 동일 immutable
      active pair의 PinVi API만 재생성한다. startup readiness와 full compatible-pair runtime/image/
      provenance/secret-isolation attestation이 모두 통과해야 commit하며, 실패·crash는 `sync=false`
      env/runtime으로 복구한다.
- [ ] Compose/CLI/config 회귀, 4-role distinct·scope/digest 음성 회귀, runner argv/stdout/stderr/
      long-running runtime 비노출, lock 경합, foreign env/receipt, cutover retry/crash, enable rollback을
      검증하고 backend 전체·Ruff·strict mypy·canonical Compose gate를 통과한다.
- [ ] 두 적대적 리뷰와 CI green 뒤 n150에서 별도 승인된 initial cutover→receipt→sync enable→
      pair attestation을 실행하고 live backlog/DLQ/epoch/snapshot readiness를 확인한다.
