# TASKS — 개발 태스크 백로그

이 문서는 `kor-travel-docker-manager`의 진행 중/대기 작업만 관리한다. 완료된 작업은
[`docs/tasks-done.md`](tasks-done.md)로 분리한다.

- 완료: `[x]`
- 진행 중: `[/]`
- 미진행: `[ ]`

---

## 작업 현황 요약

> **F1D v5 현재 정본**: 비운영 generation mutation은 root execution의
> `sudo -n ktdctl pinvi-pair rebuild-pinned --confirm`만 허용한다. 이전 `cache-target`,
> `db-backup`, `map-ui-auth`와 compatible-pair의 `deploy`·`rollback` 공개 경로는 모두
> 퇴역했으며, 아래의 v1–v4 상세 항목은 실행 지침이 아닌 퇴역 기록이다.
> **예외 하나**: `pinvi-pair capture`는 2026-08-19에 **runtime mutation이 없는 읽기 전용
> 관측기**로 되살아났다(ADR-38). 옛 v4 capture의 stop/up/recreate 스테이지는 복원하지
> 않았다. 최종 schema 상태의 backup/restore가 다시 필요해지면 pair/cache
> workflow와 독립된 새 Compose primitive·계약으로 별도 태스크를 만든다.

| 태스크 ID | 작업 항목 | 상태 | 완료 날짜 | 비고 |
|:---|:---|:---:|:---:|:---|
| **T-050** | 배포 alembic head 재발 방지 게이트 (issue #109) | `[x]` | 2026-08-04 | candidate 이미지 alembic head 정적 검사·진단 writer 재기동 image drift 거부. Manager 측 완료, issue #109 종료 |
| **T-051** | Map DB naming 정리(krtour_map→kor_travel_map) + issue #111/#114 결선 | `[x]` | 2026-08-04 | n150 실제 백업·DROP·RENAME·재배포·healthy 확인 완료 |
| **T-VN-41-F1D-D** | 최종 스키마 데이터 수용 검증 및 인수 기록 (issue #136) | `[/]` | - | C3 재구성 완료. 별도 원천/ETL 재적재 뒤 데이터 의존 E2E 결과를 기록 |
| **#171** | Map ADR-090 DSN 분리와 전용 PostgreSQL 선행 배포 | `[/]` | - | 전용 Map DB/F1D bootstrap, T-VN-40 canonical snapshot principal 최소 권한 결선 및 Hallmark 운영 콘솔 재설계 반영. upstream exact pair의 final receipt/live 검증 대기 |
| **#177** | 4분할 후 geo·concierge·map·pinvi 공통 백업 결선 | `[/]` | - | 신규 독립 `standalone_backup.py` + `ktdctl db-backup create/list/gc` + 읽기 전용 `GET /backups`. geo 앱 스케줄 백업 env는 PR #181에서 결선, n150 standalone cron·off-box 사본은 미완료 |
| **#178** | geo postgres 평문 자격증명 + 추측 가능 기본값(`addr`) 제거 | `[x]` | 2026-08-18 | n150 실제 role 비밀번호·3개 canonical env key를 함께 회전하고 PostgreSQL·geo API·Dagster web/daemon·DB init을 재생성했다. 새 비밀번호 인증, 기존 기본값 거부, secret file·health·공개 Manager 브라우저 수명주기를 확인했다 |
| **#179** | prod `.env` 파생 파일 권한 600 이탈 재발 방지 | `[x]` | 2026-08-18 | n150 기존 위반 7개를 `0600`으로 정리하고 식별 불가 백업 `.env.backup-pinvi-deploy-836a18f-`를 삭제했다. 전체 `.env*` 재검사를 통과했다 |
| **#173 / Map T-VN-H46F** | Map UI geo consumer credential 경계 | `[/]` | - | 충돌한 draft #173은 H46F PR #183으로 흡수. UI server-only alias·C6c exact wiring·Manager VWorld fallback 제거, 전문 적대 리뷰 2명 GO, backend 411 passed. PR 머지와 Map PR #1004 결합 CI 대기 |
| **T-C7-CAPTURE** | Map C7 런북 §2.1 step 8용 `ktdctl pinvi-pair capture` 추가 | `[/]` | - | 읽기 전용 관측 + v4 manifest 원자적 교체(ADR-38, 2026-08-19 개정 2). 적대 리뷰 2라운드 16건 수정 — manifest 기본 경로를 `c6c_state_paths`에서 유도(1차 개정의 §근거 1(A)는 사실오류였다), `KTDM_C6C_COMPATIBLE_PAIR_MANIFEST` fallback 제거(production `.env`에 넣으면 모든 mutation이 죽는 지뢰), basename 하드락 제거(lane은 `c7-compatible-pair-v4.json`을 쓴다), frozen env 읽기도 typed refusal, 비-JSON stdout에도 pre-image·`rollback_images_present`·`side_effects` 출력, 동일 runtime 재capture byte-멱등. 검증 `pytest -q` 555 passed/1 skipped(main baseline 411), `ruff` 68=68, `mypy --strict` 76=76(새 모듈 0). **미결(사용자 결정)**: C7 정본을 `/var/lib/.../compatible-pair-v4.json`(유도 기본값)로 할지 `/etc/kor-travel-map/c7-compatible-pair-v4.json`(오늘 lane이 읽는 파일)로 할지 — ADR-38 §미결 표. **프로비저닝 대기**: `/usr/local/bin/ktdctl` symlink(없어서 `sudo -n ktdctl`은 `command not found`), 두 checkout env, 그리고 두 checkout이 실행 중 revision을 실제로 포함하도록 하는 일 |

---

## 진행 순서

1. `tasks.md`와 `tasks-done.md`를 최신 완료/미완료 상태로 정리한다.
2. `kor-travel-concierge`는 `conc`, PinVi는 `srv` 별칭을 기준으로 안내한다.
3. 다음 앱 target 추가 시 `config/docker-targets.yml`, `docker-compose.yml`, 포트 문서, API/CLI 테스트를 함께 갱신한다.
4. 병행 작업 충돌을 줄이기 위해 각 PR 전후로 `main` rebase를 수행한다.

---

## 태스크 세부 내역

### T-031: Map↔PinVi C6c ops read/cancel principal 배포 결선 (퇴역 기록)

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

### T-045: Map UI credential rotation을 `ktdctl`의 audited production workflow로 제품화 (퇴역 기록)

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

### T-046: `pinvi-pair deploy`/`capture`의 `--wait-timeout` 하드코딩 제거 (퇴역 기록)

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

### T-047: compatible-pair canonical Compose readiness 계약 정렬 (퇴역 기록)

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

### T-048: T-VN-41 cache-target production manifest와 최초 cutover 제품화 (퇴역 기록)

- [x] ADR-28에 ordinary runtime 최소 권한, 4-role/legacy 상호 분리, default-off 최초 cutover와
      receipt, sync enable 뒤 compatible-pair attestation 순서를 기록했다. F1D v5에서 이
      control plane과 실행 문서는 함께 퇴역했다.
- [x] Map API에는 `KOR_TRAVEL_MAP_API_CACHE_TARGET_SERVICE_PRINCIPALS` JSON registry만 전달하고,
      PinVi ordinary API에는 정확히 `PINVI_KOR_TRAVEL_MAP_CACHE_TARGET_SYNC_ENABLED`,
      `..._COMMAND_TOKEN`, `..._CONSUMER_TOKEN`, `..._CONSUMER_ID`,
      `..._EXPECTED_OPENAPI_SHA256`, `..._EXPECTED_SOURCE_REVISION`,
      `..._EXPECTED_CONTRACT_GENERATION`만 전달한다.
- [x] command/consumer/restore-fence/recovery 네 token은 서로 및 기존 Map/PinVi
      service/admin/ops token과 달라야 한다. Map registry의 digest·principal·consumer·scope·
      external system을 원문 token과 교차 검증한다. registry는 정확히 네 principal, role별 최소 scope,
      `["pinvi"]`만 허용하고 extra principal/scope/system을 거부한다. registry JSON과 digest를 포함해
      raw/resolved/runtime 비인가 노출과 audit/log 누출을 차단한다.
- [x] tracked production pin manifest에 generation `7`, exact Map OpenAPI SHA-256, Map functional owner
      revision과 PinVi reviewed candidate를 기록했다. reviewed candidate는 자동 승격하지 않고 별도
      release pin이 확정되기 전 initial/enable 및 production pair capture/deploy/rollback을 mutation 전에
      fail-close하도록 했다. cache-target contract 미설정 경로는 회귀 없이 유지한다.
- [x] NO-GO 리뷰의 결합 전환 설계를 문서에 반영했다. 기존 v4 old pair에서 sync=false exact generation 7
      candidate를 검증한 뒤 active=rollback으로 원자 bootstrap하고, old pair는 coupled rollback bundle에만
      보존한다. backup/build/migrate/CSV/bootstrap/initial/enable/canary/GC/verify/forward commit을 한 process의
      C6c lock과 `0600` journal로 묶으며 non-terminal transaction은 다른 mutation을 차단한다.
- [x] production cache-target의 모든 값이 unset/default이면 기존 C6c 경로를 유지하고 부분 설정만 거부한다.
      Map·Pin release revision을 별도로 tracked pin에 두고 active/rollback source를 양쪽 모두 exact 검증한다.
- [/] 기존 v4 manifest 전용 one-time generation bootstrap을 구현한다. sync=false candidate 전체 attestation 후
      active=rollback atomic commit, old pair coupled rollback bundle 보존, crash/retry/foreign manifest와
      deploy/capture/rollback/bootstrap mutation-zero 회귀를 포함한다.
- [/] H35×T-VN-41 결합 orchestrator를 구현한다. Map application·Dagster와 Pin DB backup identity, image build,
      migration/CSV, bootstrap, initial, enable, canary, GC, verify, forward commit의 phase를 단일 process lock과
      owner-only journal로 수행하고 unfinished journal이면 same resume/coupled rollback 외 mutation을 차단한다.
      writer fence 뒤 세 DB dump와 실제 scratch restore rehearsal 전체의 앞뒤에서 write counter·stats reset,
      DB in-flight와 Map Dagster run을 재검증하고, Pin read-only preflight와 append-only final audit receipt를
      각각 별도 phase에 결박한다. canary 뒤 Map-owned typed GC를 실제 실행하고 backlog 0/referenced observation을
      확인한 다음 durable final fence로 exact 5 writer를 정지한다. stopped Map final evidence와 Pin finalize audit
      exact 1행을 결박하고 forward fsync 뒤 writer 재기동·health를 crash-resume한다.
- [/] n150 rollback에서 Compose health 직후 authenticated smoke가 일시적인 연결 거부로 실패해 원래 cutover
      오류를 덮어쓰는 문제를 보강한다. Map signed read, Map UI login, PinVi admin login, read-only PinVi Web
      shell의 **첫 요청**은 exact `ConnectionRefusedError`만 healthcheck `start_period`와 같은 30초의 bounded
      window에서 재시도한다. 다른 `OSError`·HTTP 계약 오류·인증 실패·destructive probe와 후속 admin 요청은
      재시도하지 않는다. n150에서 5초 window를 두 차례 모두 넘긴 뒤 runtime이 정상 authenticated smoke를
      통과한 재현을 회귀로 고정한다.
- [/] PinVi login 뒤 첫 canonical admin `GET`이 connection은 수립됐지만 응답을 주기 전에 timeout 나는
      readiness race를 별도 처리한다. session cookie가 있는 idempotent `GET /admin/etl/summary`와
      `GET /admin/provider-sync`만 `ConnectionRefusedError` 또는 timeout을 한 번 더 재시도하고,
      login은 기존 요청 전 `ConnectionRefusedError` 재시도만 유지한다. login timeout, logout·post-logout
      protection, 그 밖의 모든 `POST`와 destructive cancel probe는 timeout/connection failure 모두 즉시
      fail-close한다.
- [x] n150 실제 Map `pg_dump --data-only`가 순환 FK의 복원 주의 warning을 stderr로 출력하면서 종료 코드는
      0인 경우를 처리한다. data-only logical inventory는 heading·detail·두 hint가 모두 일치하는 정확한
      circular-FK advisory만 허용하고 schema-only·다른 warning·`pg_dump` nonzero exit는 fail-close한다.
      허용·비허용 warning 및 nonzero 실패를 각각 회귀로 고정한다.
- [/] pre-forward 실패는 new runtime stop 뒤 Map application→Dagster→Pin DB→manager env/state/manifest를 복구하고
      old image를 마지막에 기동한다. migration 뒤 일반 image-only rollback을 금지하고 forward commit/최초 외부
      event 뒤 old restore를 거부한다. DB rewind 뒤 stale receipt가 성공하지 않도록 live schema/epoch/cutover/
      convergence를 재검증한다.
- [/] Map candidate image의 typed helper CLI
      `python scripts/h35/h35_cutover.py {preflight,migrate,csv5,gc,verify}` exact JSON receipt를 소비한다. backup/
      restore/finalize와 runtime lifecycle은 manager가 소유하며 helper에는 넘기지 않는다. manager는 SQL/CSV
      의미, DSN, backup path, credential을 request나 코드에 하드코딩하지 않는다. production initial/enable에서는
      injected attestor/canary/rollback-smoke 인자를 제거한다.
- [ ] release unset, contract/pair/candidate mismatch에 대해 deploy/capture/rollback/initial/enable/bootstrap 각각의
      Docker/subprocess/env/manifest/retention/DB mutation이 0회임을 증명하는 행렬을 추가한다.
- [/] PinVi 최종 exact HEAD는 단일 독립 적대적 GO review 뒤 squash merge됐고, tracked
      `pinvi_release_revision`과 production candidate·active·rollback pair provenance gate를 exact merge SHA
      `4943282006139fa3b4ef3cb247780bfd9721b4c7`로 결박했다. Map #924 merge SHA가 확정되면
      `map_release_revision`을 최종 고정하고 exact-head Manager 리뷰를 진행한다.
- [ ] restore-fence/recovery 원문 token은 ordinary PinVi API와 다른 장기 실행 service에 주입하지
      않는다. C6c 전역 lock과 frozen canonical `.env`/Compose/active pair를 검증한 전용
      initial-cutover runner에는 command/consumer/recovery만 실행 시간 동안 전달하고, restore-fence는
      Map registry와 향후 별도 restore 작업 경계에만 보관한다. 종료 뒤 ephemeral container를 제거한다.
- [ ] production `sync=false`에서 고정 cutover UUID/epoch/reason으로 전용 runner를 실행하고,
      contract pin, frozen env/raw·resolved Compose logical SHA, active/rollback pair, source revision,
      protected 4-role binding logical SHA와 safe 결과 identity를 secret-free durable receipt로 원자 기록한다.
      registry JSON/개별 digest는 기록하지 않으며 같은 입력 retry는 같은 receipt로 수렴하고 다른 입력은
      fail-close한다.
- [ ] 성공 receipt가 있을 때만 canonical `.env`의 sync를 `true`로 원자 전환하고 동일 immutable
      active pair의 PinVi API만 재생성한다. startup readiness와 full compatible-pair runtime/image/
      provenance/secret-isolation attestation이 모두 통과해야 commit하며, 실패·crash는 `sync=false`
      env/runtime으로 복구한다.
- [ ] active와 rollback pair 모두 같은 cache-target generation/contract 및 cache health/pin smoke에 exact
      결박한다. generic rollback도 이 gate를 재사용하고 stale rollback image를 허용하지 않는다.
- [ ] `.env=true` 전에 `enable_preparing`을 fsync하고 initial receipt, active/rollback pair, old/new env SHA를
      묶는다. `env_committed→recreate_started→verified→committed`와
      `rollback_preparing→rollback_env_restored→rollback_recreate_started→rolled_back`을 durable phase로
      기록한다. 전체 cutover/enable은 단일 전역 critical section에서 수행하고 재획득 시 전부 refreeze한다.
- [ ] elevated recovery token은 Docker `-e value`/Compose env가 아닌 owner-only secret file과 고정 runner
      entrypoint로 전달한다. success/failure/signal 모든 경로에서 orphan container/mount/file을 분류·정리한다.
- [ ] terminal commit 전에 n150 causal canary로 고유 command→Map event→PinVi DB/cache→ACK, lag 0, DLQ 0,
      initial count/Merkle 일치를 검증하고 실패하면 `sync=false` rollback으로 전이한다.
- [ ] Compose/CLI/config 회귀, 4-role distinct·scope/digest 음성 회귀, runner argv/stdout/stderr/
      long-running runtime 비노출, lock 경합, foreign env/receipt, cutover retry/crash, enable rollback을
      검증하고 backend 전체·Ruff·strict mypy·canonical Compose gate를 통과한다.
- [ ] 최종 exact HEAD의 단일 독립 적대적 리뷰와 CI green 뒤 n150에서 별도 승인된 initial cutover→receipt→sync enable→
      pair attestation을 실행하고 live backlog/DLQ/epoch/snapshot readiness를 확인한다.

### T-049: cache-target 사전 진단·cutover abort budget 제품화 (퇴역 기록)

F1D v5에서 해당 control plane과 실행 문서를 퇴역했다. 아래는 이전 구현의 역사 기록이다.

- [x] **T-049A — typed diagnostic model과 storage.** `cache_target_diagnostics.py` 신설.
      sealed `DiagnosticPhase`(prepared→writers_fencing→writers_fenced→
      map_application_checked→map_dagster_checked→pinvi_checked→runtime_smoke_checked→
      completed, 그리고 failed/aborted 두 terminal) · `DiagnosticStage`(9종) ·
      `DiagnosticFailureClass`(8종) Literal union과 `CacheTargetDiagnosticIdentity`(설계
      문서 4절의 input logical identity 9개 필드) · `DiagnosticStageReceipt`(role·stage·
      status·failure_class·bounded elapsed time·digest만, raw stdout/stderr/DSN/경로는
      타입에 아예 없음) · `CacheTargetDiagnosticJournal`을 frozen dataclass로 정의했다.
      저장은 기존 `cache_target_cutover.write_cutover_state`/`read_owner_only_state`를
      재사용해 0600 파일·0700 부모 디렉터리·atomic replace+fsync를 그대로 물려받는다
      (`cache_target_window.py`가 세 번째 journal 타입에 쓰는 것과 같은
      `# type: ignore[arg-type]` 패턴). phase state machine(`_allowed_next_phases`),
      단계별 evidence 요구(`_validate_phase_evidence`), `external_event_count != 0`을
      즉시 security failure로 거부하는 불변식, `diagnostic_receipt_is_fresh`(T-049D가
      재사용할 stale/identity-mismatch/expired 판정)를 구현했다. 적대적 리뷰어 2명이
      독립적으로 찾은 두 실공백 — receipt의 `role`이 그 receipt가 담긴 evidence
      tuple(map_application/map_dagster/pinvi)과 실제로 일치하는지 아무도 검사하지
      않던 것, `completed` phase가 하나 이상의 `status="failed"` receipt를 포함해도
      막지 않던 것 — 을 `_validate_journal`에서 고쳤다. 회귀 테스트 36건
      추가(phase skip 거부, phase별 evidence 누락 거부, identity/receipt 필드 검증,
      role/evidence-tuple mismatch 거부, completed 상태의 failed receipt 거부, invalid
      journal이 disk에 절대 쓰이지 않음, 빈 receipt tuple round-trip, transition의
      carry-forward 의미론, freshness 판정 4가지 경로, tamper된 JSON 필드(extra/missing)
      거부, 0600 round-trip). backend 1431 passed, ruff 기존 baseline 유지(6건, 무관
      파일), mypy clean.
- [x] **T-049B — DB diagnostic primitive.** `cache_target_diagnostic_stages.py` 신설.
      `cache_target_backup.py`의 기존 pg_dump/pg_restore subprocess 패턴과
      circular-FK advisory grammar(`_is_circular_foreign_key_restore_advisory`)를
      재사용해 9개 stage(`source_archive`, `source_schema_inventory`,
      `source_data_inventory`, `archive_structure`, `scratch_create`,
      `scratch_restore`, `scratch_schema_inventory`, `scratch_data_inventory`,
      `scratch_cleanup`)를 각각 독립 호출 가능한 `diagnose_*` 함수로 분해했다. 각
      함수는 예상 가능한 실패를 raise 대신 `status="failed"` typed receipt로
      반환하고, 계약 위반(runtime 검증, scratch/production 이름 충돌)만
      `DeploymentContractError`로 raise한다. 적대적 리뷰어 2명이 독립적으로 같은
      실공백을 찾았다: `scratch_schema_inventory`/`scratch_data_inventory`가
      `_run_logical_inventory`의 실제 `failure_class`(timeout/subprocess_nonzero/
      stderr_policy_rejected)를 버리고 전부 `inventory_mismatch`로 뭉뚱그려
      원인 분리라는 설계 문서 3절의 취지를 무너뜨리던 것 — 실제 실패 class를
      그대로 propagate하도록 고쳤다. 추가로 리뷰에서 나온: production과 이름이
      같은 scratch runtime을 나머지 4개 scratch 함수도 방어적으로 거부하도록
      `_assert_scratch_does_not_collide_with_production` 공통화, archive 파일을
      여는 3개 지점에 `_validate_owner_only_directory` 방어, cleanup이 caller
      책임이라는 모듈 docstring 경고를 추가했다. 회귀 테스트 28건(기존 20 +
      failure_class propagation 2, 충돌 거부 4, restore 성공, 빈 archive 거부).
      backend 1459 passed, ruff/mypy clean.
- [x] **T-049C — writer fence와 orchestration.** `ComposeService.run_cache_target_diagnostic`을
      신설해 `ktdctl cache-target diagnose --diagnostic-id ...`에 결선했다. 기존
      cutover와 같은 C6c 전역 lock 안에서(diagnose/cutover, diagnose/diagnose 동시
      실행 불가) foreign-writer 검사(`attest_cache_target_global_writer_fence`)를
      거친 뒤 3-role(map_application → map_dagster → pinvi) 직렬 진단을 T-049B
      stage primitive로 실행하고, 새 `DiagnosticAttemptRecord`/`DiagnosticAttemptLog`
      모델(owner-only atomic storage)로 설계 문서 5절의 abort budget(24시간 내 2회,
      같은 (failure_stage, failure_class) 재현 시 자동 재시도 대신 `aborted`
      terminal)을 구현했다. journal이 이미 terminal이면 재호출은 재실행 없이
      그 결과를 재보고하고, non-terminal(crash) journal은 fail-close해 새
      diagnostic ID로만 재시작할 수 있다.

      적대적 리뷰어 2명이 독립적으로 서로 다른 실공백 3가지를 찾았다:
      (1) writer stop에 다른 mutation capability sentinel
      (`_CACHE_TARGET_WINDOW_MUTATION_CAPABILITY`)을 잘못 써서 production에서
      `assert_compose_mutation_allowed`에 항상 막혀 명령 자체가 동작하지
      않던 것 — 다른 모든 writer stop/start 지점과 같은
      `_COMPATIBLE_PAIR_MUTATION_CAPABILITY`로 고쳤다. (2) `docker compose stop`이
      일부 writer만 내리고 실패를 반환해도 `try/finally`가 stop 호출 *뒤*부터만
      writer 재기동을 보장해, 부분 실패 시 global lock은 풀리는데 production
      writer는 방치되던 것 — stop 호출 자체를 `try` 안으로 옮겨 재기동이 항상
      실행되도록 고쳤다. (3) writer 재기동 뒤 "재기동했으니 됐다"만 확인하고
      기존 cutover 경로가 다 하는 `_attest_cache_target_pair`(active pair 일치·
      resolved compose 계약·runtime secret 격리 재검증)를 하지 않던 것 — 재기동
      직후 재-attestation을 추가했다. 부수적으로 stop 뒤 in-flight transaction
      재확인(cutover의 fence와 대칭), `aborted` attempt가 재현 판정 기준(최신
      **`failed`** attempt)을 흐리지 않도록 `diagnostic_failure_is_reproduced`도
      고쳤다. 회귀 테스트: orchestration 14건(mutation capability 검증, 부분
      stop 실패에도 재기동·재-attestation 보장 포함) + attempt-budget/reproduction
      12건. backend 전체 1483 passed, ruff 기존 baseline 유지(6건, 무관 파일),
      mypy clean(src). candidate build/migration/initial event/sync enable/
      `.env`·manifest mutation은 여전히 하지 않는다 — 진단 성공은 cutover 성공을
      뜻하지 않는다.
- [x] **T-049D — cutover gate와 failure propagation.** 설계 문서 4절을 두 부분으로
      구현했다. (1) **gate**: `run_cache_target_cutover`가 새 forward window를 여는
      단 하나의 지점(`prepare_cache_target_window` 직전, 기존 journal이 없을 때만)에
      `_require_fresh_cache_target_diagnostic`을 결선했다 — T-049C 진단 journal이
      없거나 `completed`가 아니거나, T-049C가 계산하는 것과 같은 input logical
      identity(diagnostic 시점과 동일하게 old/현재 manifest 기준)로 fresh하지 않으면
      즉시 거부한다. 신선도 window는 새 상수
      `_CUTOVER_GATE_MAX_DIAGNOSTIC_AGE_SECONDS`(30분)로, T-049C의 abort-budget
      window(24시간, 진단 *재시도* 횟수 정책)와는 목적이 다른 별도 정책임을 주석으로
      명시했다. (2) **failure propagation**: `CacheTargetWindowJournal`에
      `failure_stage: WindowPhase | None`·`failure_class: WindowFailureClass | None`
      (`"contract_violation" | "unexpected_error"`, raw exception 내용은 절대 담지
      않음)을 추가하고, 새 `record_window_failure`가 coupled rollback으로 넘어가기
      직전(`journal.phase in FORWARD_PHASES`일 때만) 마지막 안전 phase와 실패
      분류를 얼린다. pre-forward 실패를 rollback으로 보내는 유일한 지점(
      `_run_cache_target_window_unlocked`의 `except Exception`)에 결선했고, 이미
      rollback 진행 중(재시작 후 resume)이면 최초 값을 그대로 보존한다.

      적대적 리뷰어 2명(gate 담당, failure-propagation 담당)이 독립적으로 같은
      mypy 실공백을 찾았다: `"contract_violation" if ... else "unexpected_error"`
      삼항식이 `WindowFailureClass` literal이 아니라 `str`로 추론돼 strict mypy가
      깨졌다 — 변수에 명시 타입 annotation을 붙여 고쳤다(런타임 동작은 항상 정확한
      literal이라 버그는 아니었음). 그 외 두 리뷰어 모두 실공백을 찾지 못했다:
      gate는 lock 안에서 TOCTOU 없이 동작하고, resume 경로는 의도대로 gate를
      재검사하지 않으며(design doc의 "새" window 문구와 일치), failure 필드는
      `transition_cache_target_window`의 carry-forward로 rollback_preparing까지
      정확히 보존되고 FORWARD phase 상태에서는 절대 disk에 쓰이지 않는다. 회귀
      테스트: gate 5건(missing/non-completed/stale/identity-mismatch/fresh-pass) +
      `record_window_failure` 4건 + 기존 rollback 테스트 1건 갱신(end-to-end 값
      검증 포함). backend 전체 1492 passed, ruff/mypy clean(touched files —
      `ruff format`은 이 환경 버전이 무관한 기존 줄까지 재포맷하려 해서 실행하지
      않음, T-049C에서 배운 교훈).
- [x] **T-049E 착수 중 발견·수정: `scratch_create`/production restore의
      dropdb NOTICE 오탐.** n150에서 실제로 `ktdctl cache-target diagnose`를
      처음 실행해보니 map_application/map_dagster/pinvi **3개 role 전부**에서
      `scratch_create`가 `admin_command_failed`로 100% 재현성 있게 실패했다.
      원인: `dropdb --if-exists --force`는 대상이 원래 없을 때도(진단마다 매번
      해당하는 일반적인 경우) "does not exist, skipping" NOTICE를 stderr에 내는데,
      공유 헬퍼 `_run_checked`가 `stderr`가 하나라도 있으면 무조건 실패로
      처리한다. `diagnose_scratch_create`(T-049B)가 이 dropdb를 무조건 실행하고
      있어서 매번 걸렸다. `_read_database_owner`가 이미 대상 부재(None)를
      확인해준 경우엔 dropdb 자체를 생략하도록 고쳤다. 같은 코드 리뷰에서
      적대적 리뷰어가 동일 패턴의 두 sibling도 찾아 같이 고쳤다:
      `_rehearse_database_restore`(scratch DB, cache_target_backup.py)와
      `restore_database_backup`(**production** DB, dropdb 전 존재 확인 자체가
      없었음) — 둘 다 실제 backup/restore/cutover 경로에서 도달 가능한 코드였다.
      적대적 리뷰어 2명이 스크래치 생성 함수 자체와 회귀 스위트를 검토했고
      confirmed 실공백은 없었다(TOCTOU는 fail-close라 안전, 재사용 가능한
      stale DB 분기는 실제 drop이라 같은 NOTICE 함정에 안 걸림). 회귀 테스트
      6건 추가(스킵/실행 조건 각각 diagnostic stage·rehearsal·production restore
      경로). backend 전체 1496 passed, ruff/mypy clean(touched files).
- [x] **T-049E 착수 중 발견·수정: `pg_restore` 60분 timeout이 실제 대용량 테이블
      복원 시간보다 짧음.** dropdb 수정 반영 후 n150에서 진단을 재실행하니
      writer fence·3-role 진단 stage 자체는 모두 정상 진행됐지만, map_application의
      `scratch_restore`가 `failure_class="timeout"`(정확히 3600000ms)으로
      실패했다. 실측: `feature.feature_weather_values`(1,780만 행) 단일 테이블의
      COPY + constraint 검증 + index 4개 재생성만으로 pg_restore가 약 **97분**
      걸렸다(2026-08-03, n150 수동 재현으로 완주까지 확인). 기존
      `timeout=3600`(60분) 하드코딩 3곳
      (`restore_database_backup`·`_restore_archive_into_database`·
      `diagnose_scratch_restore`)을 공유 상수
      `_DATABASE_RESTORE_TIMEOUT_SECONDS=10800`(3시간, 실측치에 여유를 더한
      잠정값)으로 교체했다. 이 archive는 stdin으로 스트리밍돼 `pg_restore --jobs`
      병렬 복원을 못 쓰므로(seekable archive 필요) 근본 해결은 아니며, 테이블이
      계속 커지면 이 값도 다시 부족해진다 — 코드 주석에 이를 명시했다. **중요**:
      `restore_database_backup`은 실제 production coupled-rollback 경로이므로,
      이 발견은 T-VN-41 실제 cutover/rollback이 이 테이블에서도 같은 문제를
      겪을 수 있었음을 뜻한다 — 진단 도구가 설계 의도대로 작동한 사례다.
      적대적 리뷰어 2명이 검증. 회귀 테스트 1건 추가(공유 상수가 실제
      `subprocess.run`에 전달되는지 확인). backend 전체 1497 passed, ruff/mypy
      clean(touched files).
- [/] **T-049E 후속 수정: pre-bootstrap diagnostic attestation 경계.** generation 7
      최초 cutover 전 diagnostic은 현재 old compatible pair가 manifest·frozen Compose·runtime
      secret isolation과 정확히 일치하는지를 검증해야 한다. 이 단계에 candidate release pin을
      적용하면 generation bootstrap 전의 old pair가 항상 거부되어 fresh receipt를 만들 수
      없다. diagnostic 전용 attestation은 old pair의 runtime 계약만 확인하고, tracked Map·PinVi
      release pin은 candidate build/bootstrap 및 그 이후의 일반 attestation에서 계속 강제한다.
      receipt identity는 candidate bootstrap과 같은 canonical transaction을 계속 쓰고, old
      runtime 재-attestation만 그 raw/external input에서 old pair image·source provenance를
      materialize한 frozen transaction으로 수행한다. 새 diagnostic UUID는 C6c lock 안에서 stale
      journal을 `aborted`로 보존한다. writer fence 전 quiescence preflight는 archive만 하고
      budget을 소모하지 않는다. `writers_stopping` durable boundary 이후에는 global fence digest
      전의 partial writer stop/crash도 mutation 가능 상태로 보고 terminal attempt로 대조·기록한
      owner-only archive를 거친 뒤에만 새 진단을 시작한다. n150에서 이 경계로 새 rehearsal을
      실행해 완료 receipt와 final cutover gate를 확인한다.
- [x] **T-049E 후속 조사 해결 — 스키마/데이터 inventory 해시 비교의 canonicalization.**
      map_dagster의 `scratch_data_inventory`와 pinvi의 `scratch_schema_inventory`가
      `inventory_mismatch`로 실패하던 원인(PostgreSQL의 dump→restore→dump
      비결정성 — CHECK 제약 텍스트 렌더링, `--inserts` 데이터 행 emission 순서)을
      실제로 고쳤다. 데이터 비교는 `_canonicalize_data_dump`(quote-aware SQL문
      분리·정렬)로 순서-무관하게 만들었다 — n150 `map_dagster.job_ticks` 실측으로
      검증(정규화 전 557줄 diff → 정규화 후 0). 스키마 비교는 처음엔
      `_canonicalize_schema_dump`(ARRAY cast 정규화 regex)로 시작했으나, 적대적
      리뷰어가 PostgreSQL이 `(A AND B) AND (C AND D)` → `A AND B AND (C AND D)`처럼
      중첩 AND도 재작성한다는 걸 map_application에서 추가로 찾아냈다 — 개별 패턴을
      계속 쫓는 대신, source의 schema-only dump를 scratch에 적용한 뒤 한 번 더
      재-dump해서(scratch가 자연히 겪는 것과 같은 dump→restore→dump 변환을 source
      쪽도 거치게 하는) `_run_normalized_source_schema_inventory`로 이 문제
      클래스 전체를 닫았다. n150 `map_application` 실측으로 검증(정규화된
      source hash == scratch hash, MATCH: True). 적대적 리뷰어 2명이 각각
      order-insensitivity의 진짜 오탐지 여부(다른 데이터가 같은 해시로 뭉개지지
      않는지)와 canonicalization의 철저함을 실측 기반으로 검증, confirmed 실공백
      없음. backend 전체 1545 passed, ruff/mypy clean(touched files).
- [ ] **T-049E — n150 production rehearsal**: sync=false에서 diagnostic을 한 번 실행하고
      receipt identity·artifact cleanup·runtime recovery를 확인한 뒤에만 final initial
      cutover를 한 번 실행한다.

### T-050: 배포 alembic head 재발 방지 게이트 (issue #109)

2026-08-03 prod 사고: `kor-travel-map-api-latest`가 floating tag `latest-main`(7/31
빌드, alembic head `0072`)으로 재기동되면서 entrypoint의 무조건 `alembic upgrade
head`가 조용히 실행돼 `0063`→`0072`까지만 올라갔다. `0073`(공개 링크 신뢰도 복구)이
빠져 공개 큐레이션 표면이 0으로 떨어졌다. 원인은 이 세션 자신의 T-049C cache-target
진단 writer 재기동(`_activate_cache_target_writers`)이 stale 이미지로 컨테이너를
다시 만든 것으로 추적됐다(재기동 시각 `2026-08-03T11:31:35Z`가 컨테이너
`StartedAt`과 정확히 일치). 사용자 결정으로 데이터는 복구하지 않고 폐기·재생성하며,
이 태스크는 재발 방지만 다룬다.

- [x] **candidate 이미지 alembic head 정적 검사(`--expected-alembic-head`).**
      `_assert_candidate_image_alembic_head`가 `docker run --rm --entrypoint sh
      <image> -c 'cd /app && alembic heads'`로 DB 접속·앱 기동 없이 candidate의
      alembic head만 읽어 operator가 명시한 값과 다르면(또는 head가 여럿이거나
      명령 자체가 실패하면) `deploy_compatible_pinvi_pair`를 mutation 전에
      fail-close한다. `ktdctl pinvi-pair deploy --expected-alembic-head <rev>`로
      노출했다. 생략(`None`)하면 기존 동작과 완전히 동일한 명시적 opt-in이다.
      **적대적 리뷰어 1명이 실공백을 찾았다**: 최초 구현은 `build=True`일 때도
      build **이전**의 floating tag를 검사해서, build가 그 태그를 새 이미지로
      덮어쓰면 검사가 실제로 활성화되는 이미지와 무관해지는 문제가 있었다(사고
      자체의 재발 방지 게이트 안에 사고와 같은 클래스의 결함이 있었던 셈). 검사
      위치를 `_ensure_production_pinvi_target` 안, `_prepare_c6c_candidate_pair`가
      build 뒤 돌려주는 `candidate_pair.map_image_id`(immutable ID) 직후로
      옮겨 고쳤다. PinVi는 대칭으로 검사하지 않는다 — PinVi alembic migration은
      이 경로의 일반 기동에서 자동 실행되지 않고 cache-target cutover의
      receipt-gated one-off runner에서만 실행되므로 위험이 비대칭이다(docstring에
      명시).
- [x] **진단 writer 재기동의 image drift 거부.** `_run_cache_target_diagnostic_unlocked`가
      writer를 멈추기 직전 `_inspect_current_pair(config)`로 exact running pair(5개
      writer의 immutable image ID·source revision·contract generation)를 찍어 두고,
      `finally`의 `_activate_cache_target_writers` 재기동 직후 다시 찍어 `_pair_matches`로
      비교한다 — 조금이라도 다르면(예: floating tag가 stop~restart 사이 이미
      다른 이미지를 가리키고 있던 경우) 기존 `_attest_cache_target_pair`(manifest의
      active pair와만 비교 — manifest 자체가 stale하면 이 drift를 못 잡음)보다
      먼저 fail-close한다. 진단은 read-mostly라 writer stop/restart 자체가 새
      candidate 활성화 수단이 되면 안 된다는 불변식을 명시적으로 강제한다.
      적대적 리뷰어 2명(게이트 자체 담당, drift 감지 담당) 모두 검증했고
      drift 감지 쪽은 confirmed 실공백 없음. 회귀 테스트 다수 추가(정적 검사
      성공/불일치/head 여럿/nonzero exit/timeout/raw output 비노출 6건, 배포
      게이트 pass-through·build-후-이미지 검사·opt-in skip 3건, 진단 drift 거부
      1건). backend 전체 1515 passed, ruff/mypy clean(touched files).
- [x] Manager 측 재발 방지 게이트는 완료로 issue #109를 종료했다(2026-08-04).
      Map 팀이 entrypoint의 무조건 `alembic upgrade head`를 손보는 별도 작업이
      나오면 이 게이트와의 관계를 별도로 재검토한다(coupled follow-up, blocker
      아님).

### T-051: Map DB naming 정리(krtour_map → kor_travel_map) + issue #111/#114 결선

n150 postgres 인스턴스에 `kor_travel_map`(rev `0036`, 오래된 leftover)과 `krtour_map`
(rev `0078`, 실제 최신 데이터)이 공존하고 있었다. `docker-compose.yml`의 모든 관련
기본값(`KOR_TRAVEL_MAP_PG_DSN`·`KOR_TRAVEL_MAP_DAGSTER_PG_URL`·postgres init용
`KRTOUR_MAP_POSTGRES_DB`)과 `cache_target_backup.py`의 `_ROLE_CONFIG` 기본값은 모두
`krtour_map`/`krtour_map_dagster`를 가리키고 있었는데, canonical 이름은
`kor_travel_map`/`kor_travel_map_dagster`가 맞다(사용자 확인). `krtour_map`은 legacy
naming이며 실제 최신 데이터를 담고 있으므로, `kor_travel_map`(구식)을 DROP하고
`krtour_map`을 `kor_travel_map`으로 RENAME하는 것이 올바른 정리 방향이다.

- [x] `docker-compose.yml`의 네 개 DSN/URL 기본값과 postgres init `KRTOUR_MAP_POSTGRES_DB`
      기본값을 `kor_travel_map`/`kor_travel_map_dagster`로 정렬했다. `.env.example`도
      동일하게 갱신했다.
- [x] `cache_target_backup.py`의 `_ROLE_CONFIG` database 기본값(owner 기본값은 postgres
      role 이름이라 그대로 유지)을 같은 이름으로 정렬했다. backend 전체 1527 passed,
      ruff/mypy clean(touched files), compose/contract 관련 회귀 453건 포함.
- [x] issue #114 — `KOR_TRAVEL_MAP_KOR_TRAVEL_GEO_API_KEY`를 map-api·map-dagster·
      map-dagster-daemon 세 서비스 `environment:`에 결선했다(host `.env`엔 이미 값이
      있었으나 compose가 전달하지 않아 `reverse_geocoder` 전역 필수 리소스가
      `GeoAuthNotConfiguredError`로 fail-close되고 있었다).
- [x] issue #111 — `KOR_TRAVEL_MAP_MIGRATION_EXPECTED_HEAD: "0078_cache_target_gc_observe"`를
      map-api에 명시 literal 값으로 결선했다(Map PR #931의 배포 게이트 활성화 — 이미지
      alembic head가 이 값과 다르면 DB 연결 전에 기동 자체를 거부한다). release pin
      갱신 때마다 이 값도 함께 갱신해야 한다.
- [x] n150에서 실제로: (1) `kor_travel_map`/`kor_travel_map_dagster` 백업 후 DROP,
      (2) `krtour_map`→`kor_travel_map`, `krtour_map_dagster`→`kor_travel_map_dagster`
      RENAME, (3) 이 커밋 배포, (4) map-api/map-dagster/map-dagster-daemon 재기동해
      정상 연결 확인. alembic head `0078`, 공개 큐레이션 4,424건(0 아님), geo API key
      정상 결선 확인.
- [x] issue #115(durable Dagster writer drain)는 T-052로 분리해 구현했다.

### T-052: cache-target 진단의 durable Dagster writer drain (퇴역 기록)

> **대체됨 — 구현·검증 정본이 아니다.** 이 기록은 direct daemon stop 초안의 이력을
> 보존할 뿐이다. F1D v5에서는 해당 diagnostic control plane 자체를 퇴역했으며,
> Manager는 Dagster daemon·GraphQL·DB를 직접 조작하지 않는다.

n150에서 `ktdctl cache-target diagnose`를 반복 실행하며 같은 문제를 계속 만났다:
`writers_fencing`의 preflight가 PostgreSQL 진행 중 트랜잭션과 Dagster 비종료 run이
모두 0임을 확인한 직후, 실제 writer stop 사이의 짧은 창에서 `kor-travel-map-dagster-daemon`의
schedule/sensor가 새 run을 만들어 매번 재시작해야 했다 — 운영자가 manager 경계 밖에서
Dagster GraphQL로 수동 취소해야 하는 임시방편이었다.

- [x] **`writers_draining` phase 신설.** `DiagnosticPhase`에 `writers_fencing`과
      `writers_stopping` 사이 새 phase를 추가했다(`_FORWARD_PHASES`가 index 기반이라
      `_allowed_next_phases`/`_validate_phase_evidence`는 코드 변경 없이 그대로
      맞물린다). preflight(검사와 stop 사이의 race를 만드는 지점)를 없애기 위해,
      DB in-flight 확인 뒤 dagster run count는 여기서 더 이상 확인하지 않고 바로
      `writers_draining`으로 전이해 daemon부터 멈춘다.
- [x] **daemon 선-정지로 race를 구조적으로 제거.** `_drain_cache_target_dagster_writer`가
      `["stop", "kor-travel-map-dagster-daemon"]`을 기존 writer stop/start와 **같은**
      `_COMPATIBLE_PAIR_MUTATION_CAPABILITY`로 호출한다 — 일반 compose mutation
      권한을 넓히지 않고, 이미 writer 목록에 포함된 서비스 하나만 먼저 멈추는 것뿐이다.
      daemon이 멈추면 새 run이 더 이상 생기지 않으므로, 이미 떠 있던 run만 남는다.
- [x] **bounded graceful wait → timeout 시에만 terminal cancel.**
      `_CACHE_TARGET_DRAIN_TIMEOUT_SECONDS`(5분, 설계 문서 5절의 60분 per-attempt
      예산보다 훨씬 짧게 잡아 drain 자체가 예산을 다 쓰지 않게 함) 동안
      `read_dagster_inflight_run_count`를 폴링하고, 그 안에 0이 되지 않으면
      `_cancel_dagster_nonterminal_runs`가 Dagster 표준 `DagsterInstance.report_run_canceled`
      API로만 정식 취소한다(raw GraphQL/compose 출력 없음). 스크립트는 완전히 고정된
      텍스트고 run ID·payload·credential은 어디에도(journal·CLI JSON·stdout) 남기지
      않는다 — count만 계산하고 취소하지, 식별자를 반환하지 않는다.
- [x] **재확인 후에만 `writers_stopping` 진행, 실패는 typed receipt로.** drain 성공
      뒤 DB in-flight와 dagster run count를 다시 확인하고, 하나라도 남아 있으면
      전체 writer stop(`writers_stopping`)로 절대 진행하지 않는다. 새
      `DiagnosticStage="writer_drain"`/`DiagnosticFailureClass="drain_timeout"`을
      추가해 drain 실패를 기존 per-role DB stage 실패와 같은 `failure` 튜플
      메커니즘으로 흘려보낸다 — abort-budget의 reproduced-failure 판정(같은
      원인 반복 시 `failed` 대신 `aborted`)을 drain 실패에도 그대로 적용한다.
      daemon pause 자체가 실패하면 `"admin_command_failed"`.
- [x] **모든 종료 경로에서 원복 보장.** `writers_draining`부터 이미 존재하던 하나의
      `try/finally`로 감싸, drain 실패·timeout·이후 단계 실패 어느 경우에도 기존
      `_activate_cache_target_writers`(전체 writer 재기동)와
      `_attest_cache_target_prebootstrap_pair`(pair 재검증)가 항상 실행된다 — daemon도
      writer 목록의 일부이므로 별도 resume 코드가 필요 없다.
- [x] **#113 불변식 확장 확인.** `writers_fencing`까지는(순수 preflight) crash 시
      attempt budget을 소모하지 않는 기존 규칙은 그대로 유지되지만, `writers_draining`은
      실제 daemon mutation이 일어난 뒤라 `_archive_superseded_cache_target_diagnostic`의
      기존 `reached_writer_stop_boundary` 판정(코드 변경 없음, exempt set에
      `writers_draining`을 추가하지 않아 자동으로 포함됨)에 따라 crash해도 attempt
      budget을 소모한다 — 새 회귀로 고정했다.
- [x] 적대적 리뷰어 2명(drain race/timeout/cancel 정책·compatible-pair 보호 미우회
      담당, secret/식별자 비노출·모든 종료 경로 원복·typed receipt 정확성 담당)이
      검토했다. 회귀 테스트 11건 추가(daemon pause 실패/성공, capability 확인,
      즉시 quiescent, schedule이 서서히 줄어드는 경우, timeout 후 cancel로 해소되는
      경우, cancel 후에도 남는 경우(`drain_timeout`), cancel 자체 실패
      (`admin_command_failed`), cancel 스크립트에 식별자/secret 없음 직접 검증,
      계속 run을 만드는 schedule을 흉내낸 통합 시나리오가 race 없이 완주,
      drain 이후 writer 재발생 시 전체 stop이 절대 호출되지 않고 fail-close,
      `writers_draining` crash가 attempt budget을 소모함). backend 전체 1533
      passed, ruff check/mypy clean(touched files) — `ruff format`은 이 환경
      버전이 무관한 기존 줄까지 재포맷하려 해서 실행하지 않음(T-049C에서 배운
      교훈).
- [ ] n150에서 실제 diagnose를 재실행해 `writers_draining`이 실제 schedule/sensor와
      맞물려 정상 동작하는지 확인한다(사용자 결정으로 데이터는 보존 대상이
      아니므로, 이 검증은 별도 승인 아래 진행한다).

### T-053: 독립 실행 가능한 DB 백업 CLI (`ktdctl db-backup create`) (퇴역 기록)

2026-08-04 issue #109 조사에서 확인한 근본 공백: 이 저장소 어디에도 **독립적으로
호출 가능한 DB 백업 도구가 없다.** 모든 `pg_dump`(`cache_target_backup.py`의
`create_database_backup`/`_rehearse_database_restore` 등)는 cache-target cutover
window 안에 내장된 private 스텝일 뿐이라, 사고 당시 운영자가 손으로 `pg_dump`를
실행해야 했다.

- [x] `ktdctl db-backup create --role {map_application,map_dagster,pinvi}`를
      신설했다. `ComposeService.create_standalone_backup`이 C6c 전역 lock을 pg_dump
      전체 동안 잡고, `database_runtimes_from_frozen_contract`로 frozen resolved
      Compose에서 파생한 `DatabaseRuntime`만 사용한다 — cutover window/journal과
      결합하지 않는, 언제든 단독 호출 가능한 경로다.
- [x] `~/backups/<role>/`에 canonical 이름(timestamp·역할·source revision 포함)의
      `.dump`와 같은 이름의 `.manifest.json`(timestamp, schema revision, sha256,
      byte size)을 owner-only(0700 디렉터리·0600 파일)로 남긴다. `Path.mkdir(mode=...,
      parents=True)`가 자동 생성되는 상위 디렉터리에는 `mode`를 적용하지 않는다는
      실공백을 구현 중 발견·수정했다(두 디렉터리를 각각 명시적으로 `mkdir`).
- [x] C6c 전역 lock을 pg_dump 전체 동안 유지하고 frozen resolved Compose 계약에서
      파생한 identity만 쓴다 — role 문자열로 임의 DSN을 조립하지 않는다.
- [x] raw stdout/stderr/DSN/credential을 매니페스트·CLI 출력·예외 메시지 어디에도
      넣지 않는다(secret-redaction-by-construction).
- [x] 적대적 리뷰어 2명이 검토했다. 리뷰어 1이 실공백을 찾았다: 존재-확인 뒤
      `_write_pg_dump`(cutover window용 idempotent 재사용 헬퍼)를 그대로 호출하면,
      존재-확인과 실제 쓰기 사이의 race에서 동시 재호출이 "거부" 계약을 어기고
      그 재사용 분기로 조용히 성공할 수 있었다 — `_write_pg_dump`를 재사용하지
      않고 `O_CREAT|O_EXCL`로 최종 파일명을 원자적으로 선점한 뒤 그 fd에 직접
      pg_dump를 스트리밍하도록 고쳤다. 리뷰어 2(lock 범위·identity 신뢰 모델·CLI
      검증·mutation 안전성 담당)는 confirmed 실공백 없음 — identity가 frozen
      resolved Compose를 그대로 신뢰하는 것은 새 공백이 아니라 기존 cache-target
      cutover/diagnose 경로와 같은, 이미 검증된 패턴임을 확인했다. 회귀 테스트
      8건 추가(owner-only 저장 검증, 동일 timestamp 재호출 거부, race 상황에서
      선점 파일이 조용히 유효한 백업으로 받아들여지지 않음을 직접 검증, 잘못된
      timestamp 거부, role별 runtime 선택, 잘못된 role 거부, CLI 결선 2건).
      backend 전체 1553 passed, ruff/mypy clean(touched files).

### T-054: 백업 목록/보존 관리 (`ktdctl db-backup list`, GC) (퇴역 기록)

- [x] `ktdctl db-backup list [--role ...] [--json]`이 T-053 manifest를 읽어
      사람이 읽을 수 있는 목록(시각·role·파일명·schema revision·크기·sha256)이나
      JSON을 출력한다. mutation이 없으므로 C6c lock/frozen transaction이
      필요 없는 순수 조회다. **적대적 리뷰어가 human-readable 출력에 시각
      필드가 빠져 있던 것을 찾았다** — 첫 구현이 이 항목의 요구사항을 만족
      못 하자 코드 대신 이 체크리스트 문구를 요구사항에 맞춰 낮춰놓았던
      실수를 바로잡고, 실제로 `created_at_unix`를 사람이 읽을 수 있는
      ISO 8601 UTC 문자열로 출력에 추가했다.
- [x] `cache_target_diagnostics.py`의 GC 개념(참조 보존 계약)을 filesystem
      retention에 맞게 적용했다 — 파일 목록/카운트 기반 참조가 없는 순수
      파일시스템 정리라 그 모듈의 헬퍼를 직접 재사용하진 않았지만 같은 원칙
      (무엇을 왜 지우는지 항상 typed 결과로 드러냄)을 따랐다. `--gc`로
      `keep_count`(기본 5, 나이 무관 항상 보존)와 `keep_days`(기본 14, 그
      이상은 그 안에서만 보존) 두 knob을 구현했다.
- [x] GC가 지운 백업(`deleted`)과 보존한 백업(`kept`)을 결과에 항상 명시한다
      (silent truncation 금지). manifest 자체가 손상됐거나 참조하는 `.dump`가
      없는 경우도 예외로 전체 조회를 막는 대신 `warnings`에 담아 CLI stderr에
      출력하고 나머지는 계속 보여준다.
- [x] GC 삭제는 `.manifest.json`을 먼저 지운 뒤 `.dump`를 지운다 — 중간에
      죽어도 다음 `list`가 고아 dump(디스크만 낭비)만 남기고 절대 깨지지
      않는다. 삭제 직전 owner-only 소유를 다시 검증해 다른 프로세스가 같은
      이름에 다른 파일을 심는 race를 배제하고, 백업 디렉터리의 무관한 파일은
      절대 건드리지 않는다(회귀로 고정).
- [x] 회귀 테스트 18건 추가(정렬·role 필터·빈 디렉터리·손상 manifest 경고·
      dump 유실 경고·count 기반 보존·days 기반 보존·dump+manifest 쌍 동시
      삭제·무관 파일 미접촉·잘못된 keep_count/keep_days 거부·CLI list 기본값/
      `--gc`/경고 stderr 출력/JSON 출력). backend 전체 1569 passed, ruff
      check/mypy clean(touched files).
- [ ] 적대적 리뷰어 2명 검토 대기 — fork는 Agent tool로 subagent를 만들 수
      없어 이 단계를 완료하지 못했다. 부모 세션이 리뷰를 진행한 뒤 커밋·
      PR·병합해야 한다.

### T-055: 안전장치 있는 DB 복구 CLI (`ktdctl db-backup restore`) (퇴역 기록)

- [x] `ktdctl db-backup restore --role ... --backup-id ... --expected-schema-revision ... --confirm`를
      신설했다. T-050의 `--expected-alembic-head` fail-close opt-in 패턴을 그대로
      따른다 — 복구 대상 DB의 **현재** schema revision을 `_read_schema_revision`으로
      읽어 operator가 `--expected-schema-revision`으로 명시한 값과 대조하고 다르면
      어떤 mutation도 하지 않고 즉시 거부한다. `--confirm`(store_true, 기본 False)
      없이는 CLI가 `compose_service`를 아예 호출하지 않는다(1차 방어) — 새
      `_STANDALONE_RESTORE_CAPABILITY` sentinel이 함수 자체 호출에도 한 번 더
      요구된다(2차 방어). 복구 직전 백업 파일을 재-해시해 manifest의 `sha256`과
      대조하고, dropdb/createdb/pg_restore는 기존 `restore_database_backup`과
      동일한 stderr-NOTICE-안전 조건부 dropdb 패턴을 그대로 따른다. 복구 뒤
      결과 DB의 schema revision이 manifest 기록값과 일치하는지 재확인한다.
- [x] production 대상은 C6c 전역 lock 안에서 실행하고, frozen resolved Compose
      계약에서 파생한 `DatabaseRuntime`으로만 대상을 식별한다(T-053/054와 동일
      패턴) — role 문자열로 임의 DSN을 조립하지 않는다.
- [x] 회귀 테스트 추가(capability 없이 거부, identity mismatch 거부 시 mutation
      0건, 존재하지 않는 backup-id 거부, 손상된 백업 payload(sha256 불일치) 거부
      시 mutation 0건, 정상 복구 end-to-end 및 결과가 manifest와 일치, secret
      비노출, CLI `--confirm` 없이 compose_service 미호출/있으면 정확한 인자로
      호출/에러 전파). fork로 구현했고 fork는 Agent tool로 subagent를 만들 수
      없어(T-054와 같은 제약) 리뷰 단계 전에 멈췄다 — 부모 세션이 이어받아
      적대적 리뷰어 2명(confirmation-gate 우회 가능성·role/backup-id 대상
      오지정 담당, stderr-NOTICE 회귀·백업 무결성·복구 후 검증 담당)을 돌렸다.
      둘 다 confirmed 실공백 없음 — capability sentinel은 진짜 module-private
      singleton이라 우회 경로 없음, dropdb는 fixed된 조건부 패턴을 정확히
      재사용, role/backup-id는 3중 교차검증. 리뷰어 2가 지적한 테스트 커버리지
      공백(post-restore schema mismatch 음성 테스트 부재)을 추가로 메꿨다.
      backend 전체 1589 passed, ruff/mypy clean(touched files).

### T-056: 읽기 전용 백업 이력 API + Web UI 페이지 (퇴역 기록)

T-053~055 의존. mutation(백업 생성·복구)은 계속 CLI 전용으로 남긴다 — 이 저장소의
기존 권한 경계(cache-target/pinvi-pair/map-ui-auth 모두 API에 노출되지 않고 CLI
전용인 것과 동일한 패턴)를 유지하고, HTTP 표면을 조회로만 넓힌다.

- [x] `GET /backups`(목록 전용, mutation 없음)를 추가한다.
- [x] 대시보드에 백업 이력 페이지를 추가해 이 목록을 보여준다.
- [x] 회귀 테스트, 적대적 리뷰어 2명, backend 전체/frontend type-check·build,
      ruff/mypy 통과 후 병합.

backend `GET /api/v1/backups`(role 옵션, `list_standalone_backups(gc=False)` 그대로
노출 — `gc`/create/restore로 가는 코드 경로 자체가 없음, 알 수 없는 role은 400,
`DeploymentContractError`는 409)와 회귀 테스트 6건, 프론트 `BackupHistoryPanel`
(TanStack Query, role 필터, 새로고침, timestamp/role/schema revision/size/sha256/
파일명 표시, mutation UI 없음)을 추가했다. 적대적 리뷰어 2명(백엔드 인증/mutation
경계/응답 내용 담당, 프론트 렌더링/build 담당). 백엔드 쪽은 confirmed 실공백
없음(role validation이 파일시스템 경로에 직접 쓰이지 않음을 실제 adversarial
요청으로 확인, warnings에도 raw stdout/전체 경로 없음). 프론트 리뷰어가 실제
버그를 찾았다: `useQuery`에 `retry: false`가 빠져 있어 400/409 같은 영구 에러도
TanStack 기본 재시도(~7초)를 다 거친 뒤에야 에러로 표시돼, 그동안 "로딩 중"으로
오해하게 만들었다 — `DashboardClient`의 `auth-me` 쿼리와 같은 이유로 `retry: false`
를 추가해 고쳤다. backend 전체 1595 passed, frontend type-check/lint/build 전부
통과, ruff/mypy(신규 코드 범위) clean.

### T-057: cache-target cutover 내장 백업 호출을 T-053 primitive로 통합 (퇴역 기록)

T-053 의존. 지금은 cache-target cutover 안의 백업 로직과 T-053의 독립 백업
도구가 같은 일을 서로 다른 코드 경로로 한다 — 오늘 있었던 naming drift 같은
사고의 재발 위험을 낮추려면 하나로 합쳐야 한다.

- [x] `_write_pg_dump`(cutover, idempotent 재사용 의미론)와
      `create_standalone_database_backup`(T-053, `O_CREAT|O_EXCL` 원자 선점
      의미론)이 각자 인라인으로 들고 있던 동일한 `pg_dump --format=custom`
      subprocess 호출·fsync·성공 판정을 새 `_stream_pg_dump_custom_format`
      공통 헬퍼로 뽑아냈다. 파일 생성 전략(두 함수의 의미가 서로 달라 통합할
      수 없음)은 호출자가 그대로 소유하고, 실제 pg_dump 실행 부분만
      공유한다. 기존 에러 메시지 텍스트는 호출자가 그대로 넘겨 바뀌지
      않았다(`match=`로 이 텍스트를 검사하는 테스트가 없음을 grep으로
      확인). `restore_database_backup`/`restore_standalone_database_backup`
      (T-055)은 이 태스크 범위 밖(체크리스트가 create/verify만 명시)이라
      건드리지 않았다.
- [x] 기존 cache-target 회귀 전체가 그대로 통과하는 것으로 동작 불변을
      확인했다(backend 전체 1589 passed, 리팩터링 전후 동일 count — 어떤
      테스트도 새로 고치지 않음). ruff/mypy clean(touched files). 적대적
      리뷰어 2명(behavior-equivalence 담당, 구조적 건전성·stderr-NOTICE
      안전 패턴 양방향 보존 담당) 완료 — 둘 다 confirmed 실공백 없음(에러
      메시지 텍스트·empty-output 검사·OSError 처리·fsync 순서 모두 리팩터링
      전후 byte-for-byte 동일 확인).
- [ ] **범위 재확인(사용자 지시로 방향 전환)**: 사용자가 호환성보다 설계적
      우월성·최적화·유지보수성 우선, 대대적 코드/schema 변경도 고려하라고
      지시를 바꿨다. fork는 receipt/journal/manifest 스키마 전체 통합
      재설계까지 밀어붙이는 대신, 오늘 이미 v1→v2로 한 번 바뀐
      production-critical journal(n150에 실 데이터 있음)을 같은 세션에서
      또 바꾸는 리스크를 이유로 멈추고 보고했다 — 사용자가 이 판단(안전한
      helper 추출만 반영, 전체 재설계는 별도 설계 단계를 먼저 거쳐 진행)에
      동의해 지금은 여기까지만 반영한다. journal/receipt/manifest 통합
      재설계는 별도 태스크(T-058 후보)로 남긴다.

### T-VN-41-F1: cache-target production pair re-pin (퇴역 기록)

- [x] Map #940의 service artifact/functional owner와 현재 production Map release,
      PinVi #428의 reviewed candidate/squash release를 GitHub merge provenance와 n150 배포
      receipt로 교차 확인했다.
- [x] `CacheTargetProductionPinManifest`의 generation 7 exact pair, 전체 pin 회귀,
      production cutover runbook을 한 PR에서 갱신했다. 적대적 리뷰 1건과 focused 검증을 통과하고
      reviewed exact Manager release를 production에 설치했다.

### T-VN-41-F1A: cache-target default-off 계약의 Manager 소유 bootstrap (퇴역 기록)

F2 사전 진단은 canonical `.env`에 Map registry·PinVi ordinary binding·generation pin이 모두
존재하는 것을 전제로 한다. 기존 production은 이 계약이 전혀 없어 diagnose가 mutation 전에
중단했다. 운영자가 raw Compose 또는 `.env`를 손으로 고치는 우회는 허용하지 않는다.

- [x] F1 production 재pin 뒤 read-only preflight로 cache-target contract가 완전 미구성임을
      확인하고, partial state를 자동 보정하지 않는 Manager-only bootstrap 경계를 설계했다.
- [x] `ktdctl cache-target bootstrap --confirm --json`을 추가했다. 이 command는 C6c global lock,
      frozen canonical env SHA, manager mutation capability 아래에서만 실행하며 production과
      완전 미구성 상태를 다시 검증한 뒤 단 한 번의 atomic replace로 기록한다.
- [x] 생성 값은 4개의 서로 다른 무작위 token과 최소 권한 registry다. Map에는 digest registry만,
      PinVi ordinary runtime에는 command/consumer token·consumer ID·sync=false·exact pin만 전달할
      수 있게 구성하며 restore-fence/recovery 원문은 Manager canonical env에만 둔다. stdout·journal·
      result에는 token 또는 registry 원문을 넣지 않고 role binding SHA와 env SHA만 남긴다.
- [x] process environment override, partial/기존 contract, 비production, admin/cache base 불일치,
      static production pin 불일치는 write 전에 fail-close한다. bootstrap은 container, DB, pair
      manifest, durable cutover journal을 전혀 변경하지 않는다.
- [x] 단일 적대적 리뷰에서 `export NAME=...` 또는 값 없는 dotenv 선언을 raw 검사에서 놓쳐
      duplicate key를 append할 수 있는 P2를 확인했다. dotenv parser의 key set으로 모든 선언을
      존재로 판정하도록 고치고 direct/export/blank 회귀를 추가했다.
- [x] 보정 뒤 단일 적대적 재검토에서 새 P0/P1/P2가 없음을 확인했다. focused 회귀와 backend 전체
      suite를 통과했다.
- [x] PR #131을 merge하고 exact trusted Manager release를 production에 설치했다. 최초 bootstrap은
      trusted installer가 보존한 deployment-owner `0600` env와 root-only replacement helper의 owner
      가정이 충돌해 write 전 fail-close했다. 이 ownership model 보강은 T-VN-41-F1B가 소유한다.
- [x] F1B trusted release 설치 뒤 production에서 command를 한 번 실행하고 secret-free attestation을
      확인했다. F2는 새 diagnostic ID로 재개했다.

### T-VN-41-F1B: trusted root canonical env 소유권 결박 (퇴역 기록)

trusted installer는 canonical `.env`의 owner/group/mode를 immutable release manifest에 기록하고
deployment owner의 `0600` ownership을 보존한다. 그러나 cache-target env helper는 호출 process의 UID만
owner로 허용해 root trusted mutation이 file replacement 전에 fail-close한다. 수동 `chown`이나 raw
env 편집은 하지 않는다.

- [x] n150에서 F1A bootstrap이 file type·mode·link·parent가 모두 안전한 상태에서도 owner UID 불일치로
      write 전에 거부됨을 secret-free metadata로 재현했다. container·DB·manifest·journal mutation은 없었다.
- [x] canonical env read/atomic replace가 frozen transaction snapshot의 UID/GID를 expected owner로
      명시 전달받게 한다. 이 값이 없으면 기존 current-process owner-only 검사를 유지한다.
- [x] root trusted Manager path에서는 snapshot의 owner identity와 path/bytes/expected SHA를 lock 안에서
      모두 재검증하고, parent root ownership·regular file·`0600`·single link와 replacement 후 UID/GID
      보존을 함께 강제한다. 임의 UID를 caller가 지정해 우회하는 공개 CLI/config 경로는 만들지 않는다.
- [x] direct/root·unprivileged owner·owner/GID drift·hardlink/symlink·digest drift의 음성 회귀와 단일
      적대적 리뷰를 통과했다. 보정본 backend 전체 suite `1605 passed`.
- [x] PR #133을 merge·trusted install한 뒤 F1A bootstrap을 완료하고 F2 diagnose를 재개했다.

### T-VN-41-F1C: legacy pre-stop diagnostic journal의 Manager 소유 퇴역 (퇴역 기록)

v1 diagnostic은 durable writer-drain lease/receipt 이전 schema라 post-drain recovery를 추론할 수 없다.
n150에 남은 것은 `writers_fencing` pre-stop state였으므로, state directory 삭제 대신 좁은 Manager
command로 receipt-first 퇴역해야 했다.

- [x] `ktdctl cache-target retire-legacy-diagnostic --confirm --json`이 exact v1
      `prepared`/`writers_fencing` journal만 owner-only receipt를 먼저 남기고 퇴역하도록 구현했다.
      attempt log·window·canonical `.env`·manifest·runtime·DB는 변경하지 않는다.
- [x] malformed/foreign/post-drain/v2 state 거부, receipt-first crash resume·directory fsync 재시도,
      CLI confirmation 회귀와 단일 적대적 리뷰를 통과했다. focused 115 passed, backend 전체
      1621 passed, Ruff와 strict mypy도 통과했다.
- [x] PR #135를 merge하고 trusted release를 n150에 설치했다. legacy journal 퇴역과 동일 receipt의
      idempotent 재실행을 secret-free attestation으로 확인했다.
- [x] F2 fresh v2 diagnostic은 writer stop 전 `writers_fencing`에서 Map runtime tuple drift를 감지해
      fail-close했다. DB·writer·runtime mutation은 없었다.

### T-VN-41-F1E: trusted pinned source-installer (퇴역 기록)

n150 canonical source cache는 user-owned `0700` checkout이고 tracked Map·PinVi production pin object가 없다.
root가 이 repository의 Git config·hook·remote 설정을 해석하거나 fetch하면 source owner boundary를 깨고,
F1D candidate는 여전히 build할 수 없다.

- [x] trusted root 전용 `ktdctl pinvi-pair install-pinned-sources --confirm`을 추가했다. 코드의
      canonical HTTPS `RepoSpec`과 `CACHE_TARGET_PRODUCTION_PINS` exact SHA만 root-owned empty bare staging
      repository로 fetch하며 hook·global/system/repository config·prompt·local/file/ext protocol·submodule·
      branch/tag 전체 fetch를 금지한다.
- [x] source-owner checkout은 read-only origin identity를 canonical URL과 대조하는 helper input으로만 쓴다.
      URL alias/userinfo/query/fragment/port, Map↔PinVi swap, source-root symlink/hardlink, relative/interpolated/
      duplicate/export dotenv 선언과 wrong tree/commit은 env write 전에 거부한다.
- [x] stable commit path에 root-owned immutable detached worktree를 만들고, source-root와
      `KOR_TRAVEL_MAP_GIT_COMMIT`/`PINVI_SOURCE_REVISION`의 source selection keyset을 unset-or-pin 규칙으로
      한 번에 검증·원자 교체한다. 다른 canonical env bytes는 보존한다.
- [x] private `0600` old-env backup과 secret-free durable journal(`prepared` → `env_replaced` → `committed`,
      rollback phase)을 fsync한다. crash resume은 old/new env SHA만 받아 수렴하고 foreign backup/worktree/
      journal은 cleanup이나 pair mutation을 막는다. F1E는 Docker·Compose·DB·runtime·image build를 0회로
      유지한다.
- [x] 단일 적대적 리뷰에서 submodule 재진입·root hook 경계를 보강하고, focused 71 passed, backend 전체
      1641 passed, Ruff, strict mypy를 통과했다.
- [x] PR #140과 exit-status 후속 PR #141을 merge한 뒤 trusted release를 n150에 설치했다. command는
      최초 transaction을 `committed`로 완료했고, 수정 릴리스에서 returncode `0`과 `resumed: true`를
      반환하는 idempotent 재실행까지 확인했다. F1E 경로는 Docker·Compose·DB·runtime·image build를
      호출하지 않았다.

### T-VN-41-F1F: pinned deployment input 재정렬 (퇴역 기록)

F1D의 n150 static preflight는 의도대로 mutation 전에 중단했다. live Map application DB는
`0083_nonderived_uuid_generator`인데 기존 tracked Map release `c0af…`의 image head는
`0082_legacy_write_fence`였다. H35 재실행이나 old image 우회가 아니라, 실제 배포된 Map
`8c5bdcf8…`과 그 service OpenAPI contract를 수용한 PinVi release를 하나의 새 input generation으로
재결박해야 한다.

- [x] **F1F-A (PinVi PR)** — Map `8c5bdcf8…` service artifact를 byte-exact 재vendor하고
      `derivation_enforced` required field를 수용한다. service SHA와 **Map exact release revision**,
      `.env.example` cache-target source revision을 함께 회전한다. PinVi exact merge SHA가 확정되기
      전에는 Manager production manifest를 추측값으로 갱신하지 않는다. PR #434 merge SHA
      `3b87c19c…`를 release authority로 확정했다.
- [x] **F1F-B (Manager PR)** — `CacheTargetProductionPinManifest`를 input generation v2로 올려
      Map application alembic head와 PinVi의 세 runtime contract scalar(OpenAPI SHA, Map expected source
      revision, generation)를 release pair와 같은 tracked 정본으로 둔다. 기존 trusted source-installer를
      versioned **pinned deployment input** transaction으로 승격해 Map/PinVi source root·revision,
      Map expected migration head, PinVi contract scalar를 owner-preserving atomic env replace 한 번으로
      설치한다. F1D는 v2 committed evidence 없이는 실행하지 않는다. PR #149 squash merge
      `8329f834…`로 완료했다.
- [x] v1 committed source journal은 과거 감사 증적으로 보존하되 v2 authority가 아니다. v1
      non-terminal/foreign residue는 모든 pair mutation을 계속 막고, v2 journal·backup·immutable
      worktree는 v1과 별도 path에서 crash resume·rollback을 수행한다. v2 installer는 v1 terminal
      evidence가 가리키는 root-owned immutable source만 predecessor로 수용하며, 임의 root-owned path나
      old revision scalar는 수용하지 않는다.
- [x] installer 진입 전의 일반 production-release gate는 새 manifest와 아직 old canonical env가
      다르다는 이유만으로 rotation 자체를 막으므로 F1F에는 별도 predecessor rotation preflight를 둔다.
      이 preflight는 prior terminal input receipt와 현재 env가 **정확한 이전 pinset**임을 증명할 때만
      old→new replacement를 허용한다. manifest·env 어느 한쪽만 임의 값이거나 non-terminal state면
      기존처럼 fail-close한다.
- [x] v2 pinset은 canonical manifest serialization SHA-256으로 식별한다. source input journal은 old/new
      pinset SHA, old/new env SHA와 exact worktree tree를 모두 기록한다. production control plane에는
      Map functional-owner와 reviewed PinVi candidate 같은 별도 audit ref를 남기지 않고, exact Map release와
      exact PinVi release를 유일 source authority로 사용한다. PinVi가 요구하는 source revision도 Map
      release와 동일해야 한다.
- [x] F1F-A는 PinVi source 안에 versioned cache-target upstream metadata를 추가한다. metadata는 Map
      release, service artifact SHA, contract generation을 exact 기록하고 vendored `openapi.service.json`과
      byte-exact로 대조된다. Manager F1F-B는 trusted exact Map/PinVi worktree에서 Map artifact SHA,
      PinVi metadata, PinVi vendored artifact, manifest pinset을 read-only로 네 방향 대조한다. 이 verifier는
      Docker·Compose·DB·runtime을 호출하지 않으며 어느 one-sided 상수 변경도 candidate build 전에 막는다.
- [x] prior F1D journal도 static filename으로 재사용하지 않는다. non-terminal journal은 새 input
      generation을 막고, terminal journal은 validated pin fingerprint 경로로 receipt-first archive한 뒤에만
      새 generation journal을 연다. 따라서 future re-pin이 terminal receipt를 덮거나 frozen input 비교에서
      영구 차단되는 일이 없다.
- [x] v2 input journal과 backup도 static path가 아니라 `history/<pinset_sha256>` 불변 세대로
      보관한다. next rotation과 B→A rollback 재시도는 predecessor의 exact `new_env_sha256`, exact worktree
      tree, archive된 F1D receipt를 다시 대조한다. current pinset의 receipt나 backup을 덮어 재시도를
      불가능하게 만들지 않는다.
- [x] v2 input install의 terminal state는 F1D handoff가 pending임을 durable하게 보존한다. pending 동안
      일반 deploy/rollback/diagnostic/enable/writer-drain은 모두 거부하고 같은 pinset F1D만 시작할 수 있다.
      F1D journal 생성 전 crash는 pending에서 재시도하며, F1D non-terminal·candidate halt failure는 같은
      pinset/candidate resume 외의 rotation·pair mutation을 허용하지 않는다. rotation preflight는 cache-target
      window·diagnostic·enable·writer-drain 등 모든 durable state가 terminal인지도 확인한다.
- [x] `docker-compose.yml`의 Map migration expected head는 stale literal을 제거하고 v2 installer가
      기록한 required env scalar만 받는다. candidate image static head, live DB head, resolved runtime
      expected head가 모두 한 manifest field에 정확히 일치해야 한다.
- [x] 각 PR은 단일 적대적 리뷰와 focused/full backend 검증을 통과했다. 두 PR merge 뒤 trusted
      Manager release를 설치하고 F1F input transaction의 first-run/idempotent rerun(무 Docker/DB/runtime
      mutation)을 확인한 다음에만 F1D destructive bootstrap을 재개한다.

| v2 pinset field | canonical env key / source | authority |
| --- | --- | --- |
| `map_release_revision` | `KOR_TRAVEL_MAP_GIT_COMMIT`, PinVi `…EXPECTED_SOURCE_REVISION` | exact Map release 하나 |
| `pinvi_release_revision` | `PINVI_SOURCE_REVISION` | exact PinVi release 하나 |
| `service_openapi_sha256` | `PINVI_…EXPECTED_OPENAPI_SHA256` | Map artifact bytes와 PinVi vendor metadata |
| `contract_generation` | `PINVI_…EXPECTED_CONTRACT_GENERATION` | semantic cache-target generation |
| `map_application_alembic_head` | `KOR_TRAVEL_MAP_MIGRATION_EXPECTED_HEAD` | Map candidate/live static head |

`pinset_sha256`은 위 다섯 field와 manifest schema version을 key-sort·compact JSON으로 serialize한
SHA-256이다. functional owner와 reviewed candidate는 v1 history에만 남으며 v2 manifest, canonical env,
PinVi metadata, candidate authority 어느 곳에도 존재하지 않는다.

### T-VN-41-F1D-D: 최종 스키마 데이터 수용 검증 및 인수 기록 (issue #136)

n150은 운영 서비스가 아니므로, old manifest·non-terminal F1D journal·중간 DB schema를 복구
근거로 보전하지 않는다. 과거 다섯 service compatible pair는 PinVi Web·Dagster를 제외해 실제
runtime generation과 DB writer 경계를 완결하지 못했다. 정본 설계는
[`tvn41-f1d-destructive-rebootstrap.md`](tvn41-f1d-destructive-rebootstrap.md)다.

- [x] **C3 runtime 결선** — 2026-08-06 n150 파기형 rebuild가 `committed`했고, Map application
      `0087_route_area_subtypes`, Map Dagster `29b539ebc72a`, PinVi `20260804_0049`와 v7 fixture
      `finalized`/exact `409 PIPELINE_CANCELLATION_UNSAFE`까지 확인했다. 로그인과 data-independent
      관리자 live UI smoke도 통과했다.
- [ ] **원천/ETL 재적재 인계** — 새 DB가 의도적으로 비어 있으므로 최종 스키마에 맞춘 원천/ETL
      재적재는 이 Manager transaction 밖의 별도 작업 흐름이 수행한다. 이 저장소는 샘플 데이터·백업·복원·
      데이터 이전을 수행하지 않는다.
- [ ] **데이터 의존 수용 검증 기록** — 재적재가 끝난 뒤 고정 curated/feature ID를 전제하는 관리자 UI
      상세·지도 표 landmark E2E와 PinVi 변경 E2E를 다시 실행해 결과를 기록한다. 모두 통과하면
      F1D-D를 완료 이력으로 이관한다.
### #171: Map ADR-090 DSN 분리와 전용 PostgreSQL 선행 배포

- [x] 통합 PostgreSQL의 `kor_travel_map` lifecycle을 제거하고, Map application·Dagster metadata 전용
      `kor-travel-map-postgres`를 `127.0.0.1:12703`으로 분리했다. 공용 DB recovery는 Map role·schema·
      extension·database를 더 이상 생성하거나 ownership/ACL을 변경하지 않는다.
- [x] F1D v5가 전용 DB health 확인·reset 뒤 Map 정본 `postgres-role-bootstrap.sh`와 Dagster DB init을
      `--rm` one-shot으로 실행하고, bootstrap catalog assertion 뒤 Map/Dagster migration을 시작하도록
      raw/resolved Compose·runtime 회귀를 추가했다. 일반 `ensure`는 해당 one-shot을 실행하지 않으며,
      DSN endpoint·database·principal, non-superuser Dagster metadata login, PostgreSQL 16 catalog 권한
      assertion과 pre-probe resume rebootstrap을 fail-close로 고정한다. migration 뒤 armed resume은 의도된
      runtime ACL을 보존하고 pre-migration bootstrap assertion을 재실행하지 않는다. long-lived PostgreSQL의
      superuser password는 Docker secret file로만 전달해 `Config.Env`에서 제거하고, 그 secret mount 소비자를
      PostgreSQL entrypoint 한 곳으로 제한한다. F1D는 reset 전 frozen Compose/`compose ps`가 결박한 실제
      container의 `Config.Env`도 다시 검사한다.
- [ ] Map release와 PinVi compatible pair가 merge된 exact revision으로 갱신된 뒤 n150 `rehearsal/rebuildable`
      live E2E를 실행한다. fresh data 재적재가 필요한 data-dependent 검증은 T-VN-41-F1D-D로 분리한다.
- [x] Hallmark audit의 critical 1·major 5·minor 1 개선을 반영했다. Cobalt 토큰과 Workbench 원장 구조를
      `DESIGN.md`·`frontend/tokens.css`·모든 운영 UI에 적용했고, 768px 이하에서는 서비스·백업 표를
      레이블형 행으로 전환했다. 이 변경은 #171의 deployment authority나 live E2E gate를 변경하지 않는다.

### #177: 4분할 후 geo·concierge·map·pinvi 공통 백업 결선

T-053~T-057(v5 rebuild에서 퇴역)이 남긴 공백을 새 독립 primitive로 다시 채운다 — 이 절
맨 위 안내("pair/cache workflow와 독립된 새 Compose primitive·계약으로 별도 태스크를
만든다")를 그대로 따른다.

- [x] `standalone_backup.py` 신설. cache-target/compatible-pair 기계와 완전히 무관하다.
      role→(container, database) 매핑만 코드에 두고, 포트·admin role 이름은 하드코딩하지
      않고 살아있는 컨테이너(`docker inspect`)에서 읽는다 — `.env`가 기본 포트를 덮어썼거나
      role 이름이 프로젝트마다 달라도(map은 `KOR_TRAVEL_MAP_POSTGRES_USER`에 기본값이 없다)
      항상 실제 기동값과 일치한다.
- [x] 연결은 `docker exec --user postgres` + unix socket(로컬 소켓은 `trust`로 남아 있다)만
      쓴다 — 어떤 postgres 비밀번호도 읽거나 다루지 않는다. `--port`는 host network + 프로젝트별
      포트라 필수로 명시한다(`docs/docker-management.md` 실측과 issue #177 경고 그대로).
- [x] 산출물은 `docs/docker-management.md` "산출물 3종 세트" 관례를 그대로 따른다 —
      `<role>-<ts>.dump` · `<role>-<ts>.dump.sha256`(`sha256sum -c` 그대로 먹는 형태) ·
      `<role>-<ts>.manifest`(`created_at_unix`·`duration_sec`·`instance`·`db_size_bytes`·
      `toc_entry_count`(`pg_restore --list` TOC 항목 수 — 문서의 수동 검증과 같은 sanity
      check)·`alembic_head`(best-effort, `public`→`app` schema 순서로 시도)).
- [x] `ktdctl db-backup {create,list,gc}` 세 subcommand와 읽기 전용 `GET /api/v1/backups`
      (create/gc mutation은 API에 노출하지 않음 — 이 저장소의 표준 CLI-전용 mutation
      경계. **복원은 CLI에도 아직 없다** — 별도 범위). Dashboard "백업 이력" 패널이
      이미 이 route를 호출하고 있었는데 v5 rebuild가 backend route를 지워 조용히
      404였던 것을 이번에 다시 살렸다(role 목록도 6개로 확장, 죽은 `schema_revision`
      필드 제거).
- [x] `scripts/run-standalone-backup.sh <role> <keep>` cron wrapper(crontab 예시 상단에
      기록). geo는 33GB급이라 `--keep`을 낮게(2) 둘 것을 명시.
- [x] 적대적 리뷰(4 dimension: security/correctness/test-coverage/compose-and-docs, 각
      독립 검증)에서 확인된 실공백 반영: pg_dump 성공 뒤 TOC count·docker cp 단계
      실패 시 컨테이너 임시 dump가 안 지워지던 것을 try/finally로 감쌌고, 같은 role
      동시 실행을 막는 `flock` 기반 락(`~/backups/<role>/.backup.lock`)을 추가했으며,
      `_ROLE_CONFIG`가 concierge/map/pinvi의 컨테이너 이름 env override
      (`KOR_TRAVEL_*_POSTGRES_CONTAINER`)를 무시하던 것을 존중하도록 고쳤다(geo는
      compose 자체가 override 변수를 안 둬서 리터럴 그대로). "복원도 CLI 전용"이라는
      오기(routes.py 문서화 주석·본 문서)도 정정했다. security dimension은 confirmed
      실공백 0건.
- [x] backend 전체 404 passed(standalone_backup 30 + API 5 + CLI 8 신규/조정 =
      38 관련 테스트), ruff/frontend type-check/lint clean.
- [ ] n150에 cron/systemd timer 실제 설치는 하지 않았다 — 운용 성격(#148과 같은 결정)에
      달려 있어 사용자 확인 후 진행한다.
- [ ] geo application DB role의 첫 standalone CLI 백업 실행(33GB, 시간·디스크 확인 필요)은 하지 않았다. geo는
      앱 레벨 스케줄 백업이 정본이므로 CLI는 수동 비상 백업으로만 사용한다.
- [ ] 복원 CLI(`ktdctl db-backup restore`)와 외부 오프박스 사본 자동화는 범위 밖 —
      각각 별도 태스크로 남긴다.

### #178: geo postgres 평문 자격증명 + 추측 가능 기본값(`addr`) 제거

- [x] `docker-compose.yml`의 `kor-travel-geo-postgres`를 형제 셋(map/concierge/pinvi)과
      같은 `POSTGRES_PASSWORD_FILE` + top-level `secrets:` 항목으로 전환했다(`docker
      inspect`에 평문이 남지 않는다).
- [x] `kor-travel-geo-dagster-db-init`의 `PGPASSWORD: ${...:-addr}` 평문 env도 같은 secret
      file 패턴(`PGPASSWORD="$(cat /run/secrets/...)"`)으로 전환했다.
- [x] geo-api/geo-dagster/geo-dagster-daemon의 `KTG_PG_DSN`/`KTG_DAGSTER_PG_URL` 기본값
      `postgresql://addr:addr@...`을 모두 제거하고 `:?...must be explicitly set` fail-close로
      바꿨다(5곳). `.env.example`에 `KOR_TRAVEL_GEO_DAGSTER_PG_URL` 예시를 추가했다(기존에
      누락돼 있었다).
- [x] backend 전체 388 passed, ruff clean, `docker-compose.yml` YAML 유효성 확인.
- [x] 2026-08-18 n150에서 임의 64자리 hex 비밀번호를 생성해 geo superuser role과
      canonical `.env`의 `KOR_TRAVEL_GEO_POSTGRES_PASSWORD`,
      `KOR_TRAVEL_GEO_DOCKER_PG_DSN`, `KOR_TRAVEL_GEO_DAGSTER_PG_URL`을 같은
      회전 transaction으로 갱신했다. PostgreSQL·geo API·Dagster web/daemon·DB init을
      재생성한 뒤 새 비밀번호 TCP 인증 성공, 기존 공개 기본값 `addr` 거부,
      PostgreSQL `Config.Env` 평문 부재와 `POSTGRES_PASSWORD_FILE` secret 읽기,
      모든 소비자의 새 DSN 일치, health endpoint를 확인했다.

### #179: prod `.env` 파생 파일 권한 600 이탈 재발 방지

- [x] `scripts/check-env-permissions.sh` 신설. `.env`만 이름을 지목하지 않고 `.env*` 전체를
      훑되 tracked 예시(`.env.example`)만 명시 제외한다. `--fix`로 위반분을 600으로 내릴 수
      있다(dry-run 기본).
- [x] `docs/deploy-runbook.local.md`(gitignored 운영 노트)에 "`.env` 수작업 백업 직후
      `chmod 600`" 관례와 점검 스크립트 사용법을 추가했다.
- [x] 2026-08-18 n150의 기존 위반 7개(`.env.bak.*`, `.env.backup-*`,
      `.env.kor-travel-geo-ui.local*`)를 `scripts/check-env-permissions.sh --fix`로
      `0600`에 수렴시켰다. 이름이 잘려 식별할 수 없던
      `.env.backup-pinvi-deploy-836a18f-`는 exact regular-file·single-link·metadata를
      확인한 뒤 삭제했고, `.env.example`을 제외한 전체 `.env*` 재검사를 통과했다.
