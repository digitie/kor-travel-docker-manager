# JOURNAL — 작업 일지

이 파일은 `kor-travel-docker-manager` 저장소에서 진행된 작업을 역시간순(가장 최신 항목이 맨 위)으로 기록한다.

---

## 2026-07-19 (PR #61 리뷰 차단 보강 설계 — T-033/T-034)

- PR #61 리뷰에서 raw Compose에는 있던 Map UI·Dagster web·Dagster daemon provenance가
  resolved 검증, snapshot build, candidate inspection, activation·rollback에서 누락돼 기존
  `development` image가 계속 기동될 수 있는 P1 경로를 확인했다.
- Map API만 기록하던 compatible-pair v3 대신 Map runtime 네 immutable image ID와 공통 clean
  source revision을 모두 기록하는 v4 clean-cut을 결정했다. capture/deploy/rollback은 Map
  runtime 네 service와 PinVi API를 같은 frozen transaction으로 build·재생성·검증하고,
  복원 실패 시 전체를 중지한다.
- cAdvisor는 raw exact listen argument와 default/custom resolved health URL이 같은 port인지
  확인하는 회귀 계약을 추가한다. 구현 뒤 동일 리뷰어 재검토 전에는 test·lint·Compose
  config를 실행하지 않는다.

## 2026-07-19 (C6c cAdvisor healthcheck 포트 drift 확인 — T-034)

- n150 production의 canonical compose는 cAdvisor를 `CADVISOR_PORT`(기본 `12301`)로
  정상 기동하고 해당 포트의 `/healthz`도 응답했지만, image에서 상속된
  healthcheck는 `8080`을 계속 조회해 container를 `unhealthy`로 판정했다.
- C6c bootstrap의 base-service readiness가 이 판정을 fail-close해 compatible-pair
  capture가 중단되었고, 계약에 따라 Map·PinVi API는 정지 상태를 유지했다.
- issue #62와 T-034는 cAdvisor listen·healthcheck가 같은 `CADVISOR_PORT`를 사용하게
  고정하고, 정상 health 확인 후 capture를 한 번만 재시도하는 작업으로 분리한다.

## 2026-07-19 (C7 Map UI·Dagster provenance 누락 확인 — T-033)

- n150 production 후보를 clean Map commit에서 빌드한 뒤 OCI label을 확인한 결과 Map API는
  exact revision을 가졌지만 Map UI·Dagster web·Dagster daemon은 Dockerfile 기본값
  `development`를 유지해 C7 runtime attestation을 통과할 수 없음을 확인했다.
- 원인은 세 compose service가 Dockerfile에 선언된 `KOR_TRAVEL_MAP_GIT_COMMIT` build arg를
  전달하지 않는 wiring 누락이다. issue #60으로 기록하고 실제 container 기동 전에
  candidate를 중단했다.
- T-033은 Map runtime 네 image가 같은 canonical source commit을 사용하도록 compose와
  계약 테스트를 정렬하고, n150 exact-image label 및 C7 attestation으로 완료한다.

## 2026-07-19 (T-032 C7 image provenance 완료·아카이브)

- docker-manager PR #58을 `ecaab504e63a99cb757318d3b67337bec962d90b`로 squash merge했다.
- clean HEAD→Git archive context→exact Compose build mapping→OCI label→compatible-pair manifest v3
  결박과 상위 C7 n150 production gate 완료를 반영해 T-032를 `tasks-done.md`로 옮겼다.
- 세션 상태 정본인 `CLAUDE.md`를 최종 merge 상태로 갱신했다. 이 저장소에는 별도
  `docs/resume.md`가 없다.

## 2026-07-19 (C7 C6c image source provenance fail-close 착수 — T-032)

- production `pinvi-pair capture/deploy --build`가 Map·PinVi 각 build context의 exact Git root,
  clean worktree, lowercase 40자 `HEAD`를 host-wide lock 안에서 파생·재검증하도록 설계했다.
- 적대적 사전 리뷰에서 live worktree build의 변경·원복 TOCTOU와 ignored 파일 혼입 위험을
  P1로 확인해, 실제 Docker build input을 각 exact `HEAD`의 일회성 Git archive context로 교체했다.
- 후속 리뷰에서 external Dockerfile·additional context가 snapshot을 우회할 수 있음을 확인해
  raw/resolved build mapping 전체와 snapshot 내부 Dockerfile 경로를 exact allowlist로 고정했다.
- Map의 `KOR_TRAVEL_MAP_GIT_COMMIT`, PinVi의 `PINVI_SOURCE_REVISION`/
  `PINVI_BUILD_ENVIRONMENT=production`을 canonical Compose build arg로만 전달하고, 사용자
  명시 값·resolved arg·source wiring drift를 첫 container mutation 전에 거부하도록 했다.
- 각 API build/recreate 직후 smoke보다 먼저 immutable image의
  `org.opencontainers.image.revision`을 검사하고 PinVi는
  `io.pinvi.build.environment=production`도 강제했다.
- compatible-pair를 v3 clean-cut해 active/rollback 각 pair에 두 image ID, 두 source revision,
  contract generation, recorded time을 exact 필수 필드로 보존했다. provenance가 없는
  v1/v2는 자동 전환하지 않으며 capture/deploy/rollback/smoke 결과도 image ID↔revision을
  함께 반환한다.
- 같은 단일 리뷰어가 두 P1 보강 뒤 새 P0/P1/P2 없음과 `ACCEPT FOR TESTS`를 확인했다. WSL
  Docker Python 3.13에서 C6c focused `597 passed`, backend 전체 `685 passed`, 변경 source strict
  mypy와 Ruff를 통과했다. production Compose도 `config --quiet`과 resolved exact build mapping을
  통과했다. Python 3.13 tarfile의 3.14 기본 filter 변경 안내 2건만 남고 기능 실패는 없다.

## 2026-07-19 (C6c Map API provider runtime clean-cut 정렬 — T-031)

- n150 migration 전 비파괴 preflight에서 Manager compose가 Map에서 제거된 provider credential env 9개를
  빈 값까지 API에 주입해 exact Map entrypoint가 fail-close하는 계약 drift를 확인했다.
- 제거된 env를 Map API compose에서 삭제하고 provider credential은 Dagster·daemon 수집 경계에만 남겼다.
- raw candidate·resolved candidate·최종 resolved C6c contract가 해당 이름과 제거된 live-preview flag의
  존재 자체를 API 기동 전에 거부하도록 회귀 guard와 테스트를 추가했다.
- legacy data.go.kr credential 잔여 주입도 제거하고 Map API `command`·`entrypoint` override를 세 검증
  경계와 runtime inspect에서 금지해 immutable image의 migration과 entrypoint fail-close 우회를 차단했다.
- migration·credential rotation·container/API/manifest 변경은 이 수정 PR 머지와 재검증 전까지 중지했다.

## 2026-07-19 (C6c Map UI 인증 fail-close 계약 보강 — T-031)

- Map UI runtime username·PBKDF2 hash·session secret을 기본값 없는 compose 보간과 정확한 Map UI Env
  경로로 고정하고, manager-only 평문 smoke 비밀번호가 container에 주입되지 않는 계약을 문서화했다.
- raw/resolved compose, runtime inspect, active-pair frozen recovery transaction에서 누락·변조·다른 서비스
  노출·평문 주입·live environment drift를 거부하는 회귀 테스트를 추가했다.
- 공식 차단 리뷰에 따라 첫 API stop 전에 current Map UI exact runtime 인증과
  login→protected→logout→reblock을 검사하고, 모든 Unicode whitespace session secret·credential
  repr/result/error 누출·필수 Map UI 서비스 부재를 거부하는 테스트와 운영 순서를 보강했다.
- local gate에서 Docker Compose resolved JSON이 literal `$`를 `$$`로 표현하는 경계를 확인해,
  resolved compose 비교는 escaped representation을 허용하되 current/final runtime은 raw exact 값을
  유지하고 잘못된 dollar 수와 비허용 경로 복제를 거부하는 회귀 테스트를 보강했다.
- 공식 리뷰 승인 뒤 ext4에서 C6c targeted 테스트 `541 passed`, backend 전체 테스트 `599 passed`,
  strict mypy와 신규 lint `0`, production Docker Compose config/resolved guard를 통과했다.
  n150 production cross-repo smoke와 실제 UI 로그인 검증은 아직 남아 있어 T-031은 진행 중으로 유지한다.
- n150 read-only preflight에서 일반 scalar의 username 문자열 일치를 confidential value leak으로 오인한
  false-positive를 mutation 없이 확인했다. username은 exact Map UI wiring/runtime equality만 강제하고,
  ops token·PBKDF2 hash·session secret·평문 credential만 전역 scalar isolation 대상으로 유지하도록 회귀
  테스트와 운영 문서를 보강했다. 공식 리뷰 승인 뒤 ext4에서 C6c targeted 테스트 `528 passed`, backend
  전체 테스트 `616 passed`, strict mypy와 신규 lint `0`, production Docker Compose `config --quiet` 및
  resolved guard `2/2`를 통과했다. root 권한이 필요한 n150 Map UI 비밀번호 회전, cross-repo smoke와
  실제 UI 로그인 검증은 아직 남았다.

## 2026-07-19 (C6c closed transaction 회귀 검증 — T-031)

- pass17~19의 frozen compose transaction, candidate/baseline 분리, 동일 transaction 복구 계약에 맞춰
  이전 테스트 fixture를 갱신했다. production guard를 우회하거나 실제 manager 경로 검증을 약화하지
  않고, 테스트마다 frozen root/active recovery transaction을 명시적으로 주입했다.
- C6c/Docker config focused 테스트 `395 passed`, backend 전체 테스트 `453 passed`,
  `c6c_deployment.py` strict mypy와 변경 파일 Ruff를 통과했다.
- n150 production 배포와 live UI/API E2E는 아직 수행하지 않았으므로 T-031은 진행 중으로 유지한다.

## 2026-07-18 (Map↔PinVi C6c ops principal 배포 결선 착수 — T-031)

- Map canonical ops clean-cut 뒤 PinVi가 삭제된 legacy endpoint를 호출하던 문제를 복구하기 위해,
  서비스 간 principal을 `ops:read`와 import-job `ops:cancel` 두 capability로 분리한다.
- token은 manager의 gitignore된 `.env`를 단일 source로 사용하되 map API와 PinVi API에만 각각
  전달한다. Map Dagster·daemon·UI와 PinVi Web·Dagster에는 전달하지 않는다.
- 일반 write token은 schedule·refresh policy·update request까지 불필요하게 열기 때문에 두지
  않는다. cancel token은 exact import-job cancel endpoint에만 결박하며, 단일 고정 PinVi 주체를
  위해 DB credential 수명주기를 추가하지 않는다.
- 구현 전 완료 조건을 `docs/tasks.md` T-031과 ADR-20에 먼저 기록했다. 이후 compose 계약 테스트,
  compatible image pair 배포/rollback, n150 read·cancel·거부 smoke와 로그인 검증까지 수행한다.
- 적대적 리뷰에서 production mode 누락 시 local+빈 token으로 부팅되는 fail-open, public liveness만으로
  Map과 PinVi를 동시에 올리는 순서, host-network bind/publish port 혼동, mutable tag rollback,
  gitignore된 override의 secret leak 검사 공백을 확인했다.
- `KTDM_DEPLOYMENT_ENVIRONMENT`와 `PINVI_ENVIRONMENT`를 명시적으로 일치시키고 production은 Map
  `KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED=true`까지 요구한다. 두 token은 32자 이상·모든 공백 없음·
  상호 다름을 container 변경 전에 검사한다.
- 최초 구현은 production `ensure pinvi`를 dependency 1회 → Map API wait → signed read 200 envelope/
  무토큰 401/import-job cancel 404/non-cancel mutation 403 → PinVi API wait → 나머지 앱 순서로
  분리했다. 아래 적대적 재리뷰에서 일반 ensure를 폐쇄하고 mixed pair 없는 전용 deploy로 보강했다.
- merged compose와 prod override의 environment/env_file/command/build args를 실제 token 값까지 검사하고,
  기동 뒤에는 모든 managed container를 inspect해 Map API와 PinVi API 외 노출을 거부한다.
- `ktdctl pinvi-pair capture --verified-compatible`와 `ktdctl pinvi-pair rollback`을 추가했다. manifest는
  manager state 디렉터리에 두 immutable image ID를 mode 0600으로 원자 기록하며,
  단일 image·moving tag rollback 표면은 제공하지 않는다. 실제 테스트와 n150 배포 검증은 아직 남았다.
- C6c 적대적 재리뷰의 P1을 반영해 production 일반 `ensure`/container action·config·reset/direct Compose
  경로의 API mutation을 중앙 차단하고, 전용 `ktdctl pinvi-pair deploy` capability만 허용했다. deploy,
  capture, rollback은 preflight부터 manifest commit/복구까지 같은 host filesystem lock을 잡는다.
- manifest를 generation 포함 v2로 올렸다. merged compose의 두 API host network, PinVi production mode,
  Map bind port, 정확한 loopback base, container identity, manager-only smoke credential와 `env_file` 격리,
  두 immutable image override를 mutation 전에 검증한다. local token opt-out도 두 값이 모두 빈 경우로
  제한했다.
- mixed pair 창을 없애기 위해 기존 PinVi API를 먼저 quiesce하고 Map smoke → PinVi admin 로그인과
  ETL/provider-sync 200 envelope·typed cancel 오류/`Retry-After` → remaining app `--wait` → Map UI
  로그인/보호 `/ops/providers`/로그아웃과 PinVi Web shell → runtime inspect 순으로 승격한다.
- 어느 중간 단계나 rollback 검증이 실패해도 manifest를 갱신하지 않고 배포 시작 시점 active pair의 두
  image를 함께 복구해 전체 계약을 재검증한다. 복구가 불가능하면 두 API를 중지하고
  `halted_requires_operator`를 반환한다. 이 보강의 테스트 실행·n150 live 검증은 아직 하지 않았다.
- 최종 적대적 리뷰를 반영해 generic API mutation guard가 mode/required/token pair를 공통 검사하도록
  닫고, Compose 분류는 알려진 read-only만 허용하며 `scale`·`watch`·알 수 없는 명령을 default-deny한다.
- pair deploy/rollback은 dependency·UI·Dagster를 build/recreate하지 않는다. 비-API 필수 서비스는 변경
  없이 running/healthy를 검사하고 두 API만 `--no-deps`로 변경·복구한다. final/recovery runtime 검증도
  `ps --all` 존재 여부가 아니라 필수 서비스의 실제 running/healthy 상태를 요구한다.
- clean/legacy v1용 capture를 host-lock bootstrap transaction으로 강화했다. candidate Map → signed smoke
  → PinVi → 전체 smoke 성공 뒤에만 최초 v2를 기록하고 실패하면 두 API를 모두 중지한다. PinVi owned
  cancel fixture는 정확한 409/502/503 code/details/retryability와 양의 `Retry-After`만 허용하며 429와
  generic 오류는 거부한다. 요청에 따라 이 보강 직후 테스트는 아직 실행하지 않았다.
- 최종 차단 리뷰에서 local manager와 PinVi mode의 실제 계약을 `local → development`로 바로잡고,
  모든 Compose/Docker SDK/config 파일 mutation이 공통 mode/token guard와 재진입 가능한 host lock을
  공유하도록 닫았다. config recreate/init 실패는 compose 파일의 원래 byte를 원자 복원하고 기존 runtime
  재생성까지 시도한다.
- clean capture는 빈 host에서도 base dependency → Map API/signed smoke → Map dependents → PinVi API/
  canonical smoke → PinVi dependents로 전체 topology를 구성한다. 실패 시 기존 container를 삭제하지 않고
  transaction이 만든 container만 정리한다. rollback/recovery도 Map 복원·signed smoke 뒤 PinVi를 복원해
  혼합 pair를 실행하지 않는다.
- PinVi ETL/provider 응답은 `data: null`을 거부하고 실제 DTO 핵심 shape를 검사한다. Web 200도 admin login
  form과 Next build marker가 모두 있어야 한다. runtime inspect는 Env뿐 아니라 Cmd/Entrypoint/Labels와 모든
  안전 scalar에서 secret 이름·값 누출을 차단한다.
- manifest/lock을 checkout-independent Compose project 상태 디렉터리로 옮기고 relative/noncanonical/
  cross-project production override를 거부한다. manifest 원자 replace 뒤 부모 디렉터리까지 fsync한다.
  이 차단 리뷰 반영 뒤에도 신규 적대적 리뷰 2명 승인 전에는 테스트·lint를 실행하지 않는다.
- 신규 1차 적대적 리뷰에서 발견한 `wait --down-project=true` 분류 우회, raw Env 중복에 가려지는 secret,
  offset 없는 PinVi datetime, REST 500에서의 config/runtime 복원 진단 유실을 보강했다. 회귀 테스트는
  추가했지만 2명 승인 전 실행 금지 원칙에 따라 아직 실행하지 않았다.
- pass3 적대적 차단 리뷰 8건을 반영했다. clean bootstrap은 실제 init 예외도 created-only cleanup으로
  수렴하고, `wait --down-project=*`는 service 인자와 무관하게 project-wide guard/lock을 사용한다. production
  state root/파일명은 project별 단일 경로로 고정했다. Map/PinVi DTO와 owned cancel member, manifest
  version/recorded_at을 fail-closed로 강화했으며 parent fsync 실패 시 이전 manifest를 복원한다. config restore와
  미생성 start fallback은 subprocess 진단을 REST까지 보존한다. 회귀 테스트는 작성만 했고 신규 리뷰 2명 승인
  전까지 실행하지 않는다.
- pass4 적대적 차단 리뷰 3건을 반영했다. Map dataset-grid의 canonical 필드를 `execution_coverage`로
  바로잡고 production Map bind/PinVi base URL을 정확히 `12701`로 고정했다. Map dataset row와 PinVi
  repository/asset/schedule/sensor 배열 원소를 실제 DTO shape까지 검사하며 `[null]`과 잘못된 nested 원소를
  거부하는 회귀 fixture를 추가했다. 신규 적대적 리뷰 2명 승인 전이므로 테스트·lint·build는 실행하지 않았다.
- pass5 적대적 차단 리뷰 3건을 반영했다. Map tokenless/cross-token/non-cancel capability 음성 smoke는 HTTP
  status와 RFC7807 code를 함께 검사한다. PinVi destructive cancel은 transaction state로 정확히 한 번만
  호출하고 첫 증거를 deploy/bootstrap/final verification/recovery에서 재사용하며 uncertain 결과에는 재요청하지
  않는다. cancellation attempt/member/Dagster run의 전체 datetime·structured error·lifecycle·commit 보존
  DTO와 canonical 409 root-only shape를 회귀 fixture로 고정했다. 신규 적대적 리뷰 2명 승인 전이므로
  테스트·lint·build는 실행하지 않았다.
- pass6 cancel DTO 정렬을 반영했다. full 409은 unresolved count 0, resolved root+unresolved child,
  transient all-resolved topology를 허용하되 count와 member 상태를 정확히 맞춘다. retryable은 모든 failed
  member/run의 exact run-backed `cancel_failed`와 retryable error를 요구하고 `already_terminal` 대체를
  거부한다. in-progress/definitive CAS drift의 member `cancel_failed`+run `cancelled` canonical 전이는
  허용한다. actual `409 PIPELINE_CANCELLATION_UNSAFE`+`failed`와
  `503 DAGSTER_TERMINATION_TIMEOUT`+`retryable` pair도 고정했다. 회귀 fixture만 작성했으며
  테스트·lint·build는 실행하지 않았다.
- pass7에서 failed attempt의 retryable run-backed/definitive mismatch 혼재, status-error-finished DB lifecycle,
  retry subset lineage, frozen termination flag와 engine timestamp를 actual Map/PinVi 정본에 맞췄다.
  `Retry-After` header presence와 양의 정수 parsing을 분리하고, Compose `kill -s/--signal` 값 소비 및
  service-less/project-wide·unknown option default-deny fixture를 추가했다. 신규 적대적 리뷰 전이므로
  테스트·lint·build는 실행하지 않았다.
- pass8에서 Compose 옵션을 command별로 분리해 `build --pull`, `run --rm`, `rm -s/--stop`이 다음 service를
  값으로 소비하지 않게 했다. `config -o/--output`의 분리·inline·누락 형식은 write-capable mutation으로
  host lock과 capability를 요구하고, `--format json` 등 명시한 read-only 형식만 무변경으로 허용한다.
- PinVi cancel detail은 in-progress runless 실패를 definitive code로 한정하고, run-backed member/run 오류
  policy group, retryable exact evidence, resolved member와 Dagster terminal mapping을 현재 Map/PinVi 정본에
  맞췄다. feature-load root의 failed/SUCCESS 예외는 동일 run의 `provider_feature_load` child 증거가 있을
  때만 허용한다.
- `KTDM_C6C_CONTRACT_GENERATION`을 manager-only 보호값으로 올려 resolved compose scalar, non-root
  `env_file`, runtime Env를 포함한 모든 container 주입을 거부한다. bootstrap 정리 명령이 예외를 내도
  예외를 외부로 흘리지 않고 operator-required 상태로 수렴한다. 회귀 fixture만 작성했으며 신규 적대적
  리뷰 2명 승인 전이므로 테스트·lint·build는 실행하지 않았다.
- pass9에서 `Retry-After` parser를 ASCII `[0-9]+`와 1..300 범위로 고정했다. 부호, 앞뒤 공백,
  Unicode digit, 0, 301 이상은 header가 존재해도 parsing 실패로 처리한다.
- rustfs 같은 non-API config update/reset 및 미생성 start-create도 candidate raw compose 전체를 먼저
  검사한다. exact Map/PinVi API environment interpolation 외의 environment·label·command·build scalar에서
  ops/manager 보호 이름이나 현재 보호값을 참조하면 거부하고, non-root `env_file` 내용의 alias 값도 검사한다.
  거부는 compose 파일 쓰기와 container recreate 전에 발생하며 REST는 typed 409
  `COMPOSE_CANDIDATE_PROTECTED_REFERENCE`, `mutation_applied=false`를 반환한다. 정적 fixture만 보강했고
  테스트·lint·build는 실행하지 않았다.
- pass10 적대적 차단 리뷰 4건을 반영했다. candidate raw/resolved 검사를 compose 전체 graph와 top-level
  secret/config 외부 파일로 확장하고, API raw wiring은 suffix까지 canonical exact로 고정했다. `env_file` 경로는
  Compose 변수 연산자와 `$$`를 명시 해석하되 중첩·미완성 문법은 fail-close한다. generic ensure/up/create/
  recreate와 config prewrite가 검증 전에 Docker/file mutation을 실행하지 않도록 중앙 gate를 연결했고 candidate
  오류는 REST typed 409 detail을 보존한다. 지시에 따라 테스트·lint·build는 실행하지 않았다.
- pass11 적대적 차단 리뷰를 반영했다. volume short/long bind source를 보간·canonicalize해 root `.env`, manager
  state 파일, 보호 이름·현재 값이 든 파일의 relative/absolute/traversal/symlink/`:ro` 우회를 raw/resolved 모두
  차단했다. Windows-looking source는 fail-close하고 named volume은 host file 검사에서 분리했다. 내용 확인이
  불가능한 external secret/config alias reference도 빈 exact allowlist 밖에서 거부하며 rustfs config REST의
  typed 409와 compose/container mutation 0 fixture를 고정했다. 지시에 따라 테스트·lint·build는 실행하지 않았다.
- pass12 적대적 차단 리뷰 2건을 반영했다. manager 파일 ancestor·state directory·host root bind는 먼저
  거부하고, directory bind를 서비스별 canonical source/target allowlist로 닫았다. missing source와 oversized/
  unreadable/non-regular file은 Docker 자동 생성·mutation 전에 fail-close한다. cAdvisor의 `/:/rootfs`, `/var/run`,
  `/var/lib/docker`, `/dev/disk` mount를 제거하고 Docker socket+`/sys`, `--docker_only=true`로 축소했다. RustFS
  REST typed 409와 source/compose/container mutation 0 fixture를 보강했으며 같은 리뷰어 재승인 전이므로
  테스트·lint·build는 실행하지 않았다.
- pass13 적대적 리뷰의 volume/TOCTOU 지적을 반영했다. 운영 DB·RustFS·Geo·Prometheus·Grafana bind를 별도
  migration 없이 유지하되 config API의 top-level/service volume graph를 pre-request compose와 exact immutable로
  고정했다. internal/default named volume만 허용하고 bind-capable local driver option, unknown driver/option,
  external alias를 raw/resolved에서 거부한다. cAdvisor short/long RO access와 `/sys` mountpoint, root:docker `0660`
  socket, root-owned parent chain의 inode/device/mode snapshot을 mutex 안에서 compose write·Docker subprocess 직전
  재검증한다. mismatch의 write 전 차단/compose byte 복원과 REST typed 409 mutation 0 fixture를 보강했으며 같은
  리뷰어 재승인 전이므로 테스트·lint·build는 실행하지 않았다.
- pass14 차단 리뷰를 반영해 mutex 안에서 persisted/request의 raw·resolved volume graph를 각각 exact 비교하고,
  include/extends/`COMPOSE_FILE`/추가 override를 거부하는 single-file mutation 경계를 고정했다. cAdvisor는 raw
  literal과 resolved identity 모두 RO `/sys`·Docker socket exact set만 허용하고 named-volume raw alias와
  resolved project-name drift를 차단한다. 첫 mutation 성공 후 다음 preflight가 drift하면 원래 오류와 복구
  진단을 typed 500으로 보존하고 compose byte/mode와 persisted runtime을 best-effort 복구한다. reset/API
  no-mutation, Docker/ensure direct/API post-mutation fixture를 추가했으며 같은 리뷰어 재승인 전이므로
  테스트·lint·build는 실행하지 않았다.
- pass15 차단 리뷰 4건만 반영했다. mutation command에서 override 탐색을 제거하고 subprocess 직전
  single-file 경계를 재검증한다. `ensure` recovery는 최초 compose byte/mode를 원자 복원하고 동일
  raw/resolved hash·system snapshot을 재검증한 뒤에만 baseline runtime을 재생성하며, 실패 시 Docker recovery를
  실행하지 않는다. preflight drift 원본 복원이 실패해 durable config mutation이 남는 경우도 원래 candidate
  오류와 복구 진단을 typed 500으로 보존한다. CLI fixture는 완전한 validation DTO로 고쳤고 테스트·lint·build는
  재승인 전 금지에 따라 실행하지 않았다.
- pass16에서 Compose interpolation TOCTOU를 닫았다. transaction 시작 시 `.env` 존재 여부·byte와
  device/inode/mode/uid/gid, process env를 합친 effective mapping을 비밀값 비노출 snapshot으로 고정하고
  raw/resolved 검증·mutation·recovery 전체에서 재사용한다. mutation subprocess는
  `--env-file /dev/null`과 frozen env만 받고, 직전 `.env` 생성·삭제·내용·identity drift는 typed contract
  오류와 Docker subprocess 0으로 중단한다. direct/API no-mutation과 frozen env/recovery identity fixture를
  보강했으며 재승인 전 금지에 따라 테스트·lint·build는 실행하지 않았다.
- pass17에서 production mutation lock을 project state에서 분리한 사용자 단일 전역 경로로 고정했다. lock을
  잡은 뒤 manifest 경로와 root `.env`, canonical compose source, external `env_file` graph·byte·identity를
  한 번만 capture하고 pair deploy/capture/rollback 및 recovery 전체에 같은 transaction snapshot을 전달한다.
  외부 입력은 exact `{path, required, format}` list만 허용하며 top-level secret/config file source는
  fail-close한다. resolution은 외부 byte를 익명 fd로만 제공하고, mutation은 original project directory에서
  완전 해석된 compose JSON을 `-f -` stdin으로 실행해 relative bind/build 의미와 secret 비노출을 함께
  보존한다. 최초 mutation 뒤 source/external 계약 drift는 같은 snapshot으로 복구 또는 두 API halt를 시도하고
  원래 오류와 복구 진단을 typed post-mutation 오류에 남긴다. 지시에 따라 추가 fixture는 중단했고
  테스트·lint·build 없이 정적 호출부와 diff만 확인했다.
- pass18에서 live env/source drift 뒤에도 복구 가능한 frozen resolved stdin 경계를 추가하고, config 변경의
  baseline/candidate transaction을 분리해 원본 오류·halt 증거와 exact 원본 복원을 보존했다.
- pass19에서 첫 mutation 전에 manifest active SHA를 root frozen source/env/external/system 입력으로 resolve한
  recovery transaction을 별도 생성해 deploy/rollback과 legacy capture 실패 복구에만 사용하도록 분리했다.

---

## 2026-07-14 (Map OpiNet·KREX provider 키 compose 보간 drift 수정 — T-030)

- manager `.env`는 현재 Map 계약인 `KOR_TRAVEL_MAP_OPINET_API_KEY`와
  `KOR_TRAVEL_MAP_KREX_EX_API_KEY`를 사용하지만 base compose가 과거 `KRTOUR_MAP_*` source를
  읽어, 값이 있어도 Dagster·Dagster daemon에 빈 문자열을 전달하는 반복 장애의 원천을
  확인했다. KREX GO key와 두 provider의 map API live preview key도 함께 점검했다.
- OpiNet·KREX EX·GO 공통 key를 현재 이름에서 명시 보간하되 실제 수집기를 실행하는
  Dagster·Dagster daemon에만 주입한다. API에는 resolved live preview 변수만 주입하고, 별도 값이
  없을 때 각각 OpiNet 공통 key와 EX key를 compose interpolation source로 재사용한다. 실제 secret은
  코드·문서·테스트에 넣지 않고 gitignore된 루트 `.env` 한 곳에만 둔다.
- 계약 테스트가 수집 서비스의 공통 key와 API 전용 preview key 경계를 분리하고 API에 원본 공통
  key가 없음을 고정한다. `.env.example`의 빈 placeholder 각 1건, 백엔드 focused test·Ruff,
  placeholder를 사용한 `docker compose config --quiet` 및 current/fallback/blank resolved-value 검증을
  통과했다.

---

## 2026-07-13 (Concierge DB read 키 단일 source 주입·운영 전환 — T-029)

- `KOR_TRAVEL_MAP_KOR_TRAVEL_CONCIERGE_API_KEY`를 manager 루트 `.env`의 단일 secret source로
  정의하고, base compose가 실제 fetcher를 실행하는 Dagster·Dagster daemon에 동일하게 주입하도록
  했다. 사용하지 않는 map API에는 read secret을 주입하지 않는다.
- Concierge feature base URL도 두 서비스에 같은 계약으로 주입해 prod override의 중복 literal 없이
  `/api/v1/features/{snapshot,changes}`를 호출할 수 있게 했다.
- `.env.example`에는 빈 placeholder와 DB `read` scope 발급 원칙만 기록했다. 구현 PR에서는 실제
  prod `.env`와 `docker-compose.override.yml`을 변경하지 않고 n150 전환 단계에서만 적용했다.
- 계약 테스트가 두 서비스의 source 식과 `.env.example` key 정의 1건을 고정한다. n150 Python 3.11
  일회성 컨테이너에서 백엔드 테스트 40개와 Ruff를 통과했고, n150의 Docker Compose로
  `config --quiet` 보간도 통과했다. 로컬 테스트는 실행하지 않았다.
- n150에서 Concierge DB를 Alembic head `20260713_0017`로 올리고 scope migration
  `20260713_0016`의 `scope NOT NULL`, `read|admin` CHECK와 soft-delete schema를 확인했다. UI
  재생성 뒤 admin 해시/session secret 비어 있지 않음, 로그인 POST 200+`Set-Cookie`, BFF settings
  200, 잘못된 비밀번호 401을 확인했다. 재생성 직후 준비 구간에서 일시 503이 한 번 있었으나
  준비 완료 후 같은 전체 검증을 재실행해 통과했다.
- DB `read` 키를 발급해 DB에는 해시와 발급 감사 기록만 남기고, manager `.env` 한 곳에만 주입했다.
  override의 기존 key 세 줄과 base URL 세 줄을 제거하고 Dagster·Dagster daemon을 재생성했다. 과거
  환경변수를 확실히 제거하기 위해 map API도 한 번 재생성했으며, map API에는 read key가 없고 두
  수집기 컨테이너 값은 `.env`와 같음을 값 비노출 constant-time 비교로 확인했다.
- snapshot과 changes 각각 `limit=1` 2페이지 cursor 검증을 수행한 뒤 `page_size=200`으로 전체를
  순회했다. 두 모드 모두 8페이지, 1,416건이었고 cursor 진행·export ID 무중복 조건을 통과했다.
  실제 Dagster 컨테이너 수집기도 snapshot/changes 각 1,416건을 반환했다.
- BFF/operator static admin 키를 old/new overlap으로 교체하고 UI·BFF를 검증한 뒤 old를 제거했다.
  최종 old admin 401, new admin 내부 GET 200, read 공급 GET 200, read 내부/write 403과 로그인 POST
  200+`Set-Cookie`를 확인했다. 성공 후 key/cookie 임시 파일, 제한권한 백업, migration 복원
  지점을 삭제하고 관련 서비스 최근 로그에 오류가 없음을 확인해 T-029를 완료했다.

---

## 2026-06-28 (PinVi public API URL·CORS origin 환경변수화 — T-027)

- PinVi live mutating E2E 재검증 중 public Web origin의 `/auth/login` preflight가 `400 Bad Request`로 거부되는 배포 drift를 확인했다. 원인은 manager compose가 `PINVI_CORS_ALLOWED_ORIGINS`를 로컬 origin으로만 고정하고, Web build/runtime API URL도 로컬 API 기본값으로만 선언하던 것이다.
- `PINVI_PUBLIC_API_URL`과 `PINVI_CORS_ALLOWED_ORIGINS`를 `.env` 주입값으로 받도록 바꾸고, dev 기본값은 기존 로컬 `127.0.0.1:12801`/`12805` 계약으로 유지했다. prod 실제 도메인은 gitignore된 `.env`에만 둔다.
- 검증: `docker compose config -q`로 compose 보간/문법을 확인했고, PinVi 쪽 live mutating E2E는 이 변경을 운영 compose에 동기화한 뒤 재실행한다.

---

## 2026-06-28 (PinVi API worker 기본값 환경변수화 — T-026)

- PinVi live WebSocket mutating E2E에서 운영 배포의 `pinvi-api`가 `uvicorn --workers 2`로 고정되어 process-local broadcast broker가 worker 간 전달을 하지 못하는 문제를 확인했다. HTTP mutation과 WebSocket 연결이 서로 다른 worker에 배정되면 같은 trip의 변경 broadcast가 누락될 수 있다.
- `docker-compose.yml`의 `pinvi-api` command를 `--workers ${PINVI_API_WORKERS:-1}`로 바꾸고, 환경변수와 `.env.example`에 `PINVI_API_WORKERS=1` 기본값을 추가했다. shared broker 도입 전에는 worker 1이 안전한 운영 계약임을 compose 주석과 아키텍처 문서에 남겼다.
- 검증: `docker compose config -q`로 compose 보간/문법을 확인했고, PinVi 쪽 live mutating E2E는 이 변경을 배포한 뒤 재실행한다.

---

## 2026-06-26 (배포 런북 + push 전 보안 감사 절차 — concierge 스타일 정렬 — T-025)

- 반복된 prod 배포 실수와 민감 운영 정보를 `docs/deploy-runbook.local.md`(gitignore `*.local.md`, 커밋 금지)에 상세 기록했다. 형제 프로젝트 `kor-travel-concierge`의 `deploy-runbook.local.md` 스타일에 맞춰 §0 접속 테이블 / §1 ★최우선 반복실수(heredoc 명령 깨짐·curl-only 검증 함정) / §2 그 외 함정 / §3 표준 절차 / §4 셀프 체크리스트 / §5 푸시 전 추가 스캔(grep)+`git check-ignore` 자기검증 구조로 작성. concierge 런북의 교차 내용(이 repo가 소유한 `docker-compose.override.yml` env_file 사일런트 스킵, `docker compose config`/`.env` 시크릿 평문 덤프 주의, OPNsense 라우터 HAProxy)도 반영. prod 비번이 커밋된 테스트 값과 동일하다는 노출도 명시(변경 권고).
- 보안 감사는 concierge와 동일하게 **문서화 절차**로 정렬(전용 script/hook 미도입 — 처음 만들었던 `scripts/security-audit.sh`·`.githooks/pre-push`·`core.hooksPath`는 concierge에 없어 제거): `AGENTS.md`에 "## prod 배포 & 보안 감사" + "### remote 푸시 전 보안 감사(필수 절차)"(스테이징 파일 점검·`git diff --cached | grep` 일반 비밀 스캔·런북 §5 프로젝트별 패턴·.env.example placeholder·덤프 혼입 점검) 추가, DO NOT #13(보안 감사 생략 금지)·#14(배포 후 브라우저 검증 생략 금지) 추가, 작업 전 필독 목록에 런북 참조 추가.
- 런북은 git으로 전파되지 않으므로 각 worktree(`-codex`, `-codex-pr38`)에 수동 복사.

---

## 2026-06-24 (로그아웃/세션만료 시 LoginScreen 전환 회귀 수정 — T-024)

- 공개도메인 브라우저 E2E(Playwright)에서 발견: **로그아웃(또는 세션 만료) 후 대시보드가 LoginScreen으로 전환되지 않고** "통신 연결 오류" 배너 + 401 폴링 루프에 멈추는 회귀. 원인은 T-020(PR #37) FE-2에서 401 처리를 하드 리로드 → `auth-me` 쿼리 무효화로 바꾼 것: react-query가 refetch 에러 시 직전 성공 데이터(`authenticated:true`)를 유지해 `isAuthenticated`가 false로 내려가지 않는다(기존 하드 리로드는 전체 상태를 리셋해 우회했었음).
- 수정 1: `auth-me` queryFn이 401을 throw하지 않고 `{authenticated:false}`로 반환하도록 변경. 미인증을 유효 상태로 취급 → 로그아웃/만료 시 `isAuthenticated=false` → 리로드 없이 LoginScreen 즉시 전환(FE-2 의도대로 동작). 초기 미로그인 로드도 동일.
- 수정 2(브라우저 E2E로 추가 발견): 상태 WebSocket의 `onclose` 재연결이 무조건 `setTimeout(connectWS)`을 걸어, 로그아웃 시 서버가 WS를 닫으면 effect cleanup 이후에도 재연결이 스케줄돼 LoginScreen에서 **403 WS 핸드셰이크 무한 재시도 루프**가 돌았다. `cancelled` 플래그를 추가해 cleanup/언마운트 이후에는 재연결하지 않도록 수정(미인증 시 WS 시도 0건).
- 검증: 프론트 `type-check`·`build`, prod 배포 후 브라우저 로그아웃→LoginScreen 전환 + WS 루프 정지(콘솔 에러 누적 중단) 확인.

---

## 2026-06-24 (concierge PR #127 참고: 공개도메인 Secure 쿠키 보강 — T-023)

- 형제 프로젝트 `kor-travel-concierge` PR #127(공개 도메인 로그인 403 INVALID_ORIGIN — 운영 TLS 종단 프록시(라우터 HAProxy)가 `X-Forwarded-Proto: https` 미주입 → same-origin 재구성이 http가 돼 https Origin과 불일치 → 신뢰 origin 화이트리스트로 보완)를 참고해 동일 계열 문제를 점검·보강했다.
- 이 repo의 origin(CSRF) 검사는 concierge와 달리 **헤더 재구성이 아니라 화이트리스트(`KTDM_FRONTEND_ORIGINS`) 대조** 방식이라 **로그인 403 버그가 없음**을 실제 공개도메인 브라우저 E2E(Playwright, `https://manager.…`)로 확인했다(로그인→대시보드 18컨테이너·WS 실시간 동작, `me 401(초기)→login 200→me 200`).
- 다만 동일한 프록시-proto 문제로 `_is_https`가 내부 http로 판단해 **세션 쿠키 `Secure` 플래그가 누락**되는 약점이 남아 있었다. `_is_https`를 보강: 신뢰 `X-Forwarded-Proto`/직접 https가 아니어도 **브라우저 Origin이 설정된 https 공개 origin(`allowed_frontend_origins`)과 일치하면 https로 간주**해 `Secure`를 부여한다. 브라우저 Origin을 화이트리스트와 대조하므로 안전하고, LAN http origin은 영향 없으며 prod `.env` 변경이 필요 없다(기존 allowlist 재사용).
- 단위 테스트 추가(https 공개 origin→True, http LAN→False, 미등록 https→False, 직접 https→True). 검증: 백엔드 `ruff`(클린)·`pytest`(39 passed), prod 배포 후 실제 브라우저 로그인 재검증.

---

## 2026-06-24 (prod 풀 라이브 e2e + Retry-After 버그 수정 — T-022)

- n150(prod)에서 docker 컨테이너를 변경하지 않는 범위로 풀 라이브 e2e(63→65 케이스: health·unauth 게이트·CORS·RBAC(컨테이너 무변경)·로그인 음성/검증·next sanitize·인증 읽기전용·감사·키 lifecycle·WebSocket·세션 보안·AUTH-6 레이트리밋·프론트)를 수행했다. stdlib urllib + websockets 기반 e2e 스크립트(`/tmp/prod_e2e.py`, repo 미커밋)로 venv python 실행.
- 라이브 e2e가 실제 버그 1건을 발견: **로그인 429(rate limited) 응답에 `Retry-After` 헤더가 누락**. 원인은 주입된 `response` 객체에 헤더를 설정한 뒤 `HTTPException`을 raise하면 그 헤더가 응답에 반영되지 않기 때문(PR #36 원본). `HTTPException(headers={"Retry-After": ...})`로 전달하도록 수정하고 회귀 테스트를 추가했다.
- e2e의 다른 2건 실패는 시스템 정상 동작 확인(테스트 기대 수정): (1) 80자 초과 라벨은 truncate가 아니라 422 검증 거부, (2) 세션의 User-Agent fingerprint 바인딩으로 로그인/WS의 UA가 다르면 거부됨(보안 기능 정상) → 동일 UA로 검증.
- 검증: 백엔드 `ruff`(클린)·`pytest`(38 passed), prod 배포 후 라이브 e2e 전체 통과.

---

## 2026-06-24 (PR #36 후속 하드닝 — T-021)

- T-020에서 배포 리스크로 분리했던 후속 항목을 모두 반영(별도 PR, fix/pr36-followups-2). 적용:
  - **AUTH-3**: `_request_from_trusted_proxy`에 선택적 공유 시크릿 헤더(`KTDM_TRUSTED_PROXY_SECRET` / `X-KTDM-Proxy-Secret`) 요구 추가 — 설정 시 신뢰 CIDR이라도 헤더가 일치해야 X-Forwarded-* 를 신뢰(host 네트워크 로컬 프로세스의 loopback 위조 차단), 미설정 시 기존 동작(하위호환).
  - **AUTH-6**: 인메모리 brute-force 카운터를 제거하고 `login_audit_events` 기반 durable 집계로 전환 — 재시작·다중 워커에서 유지되며 마지막 성공 이후 실패만 카운트(성공 시 리셋 효과 보존).
  - **APIKEY-1**: 공개 API 키 검증의 프로세스 로컬 TTL 캐시를 제거하고 요청당 `key_hash` 유니크 인덱스 DB 조회로 전환 — 키 폐기가 모든 워커에 즉시 반영. `KTDM_PUBLIC_API_KEY_CACHE_TTL_S` 폐기.
  - **FE-4**: log/chart/config 모달에 `role="dialog"`/`aria-modal`/`aria-label`·Escape 닫기·닫기 버튼 초기 포커스 및 접근명(aria-label) 추가.
  - 문서: `.env.example`에 `KTDM_TRUSTED_PROXY_SECRET` 추가, 미사용 `KTDM_PUBLIC_API_KEY_CACHE_TTL_S` 제거.
- 검증: 백엔드 `ruff`(클린)·`pytest`(37 passed; AUTH-3/AUTH-6 테스트 추가), 프론트 `type-check`·`build` 통과. prod 배포 후 인증 end-to-end(로그인/me/컨테이너/키 생성·폐기/로그아웃·폐기쿠키 재사용 401) 재검증.

---

## 2026-06-24 (PR #36 사후 리뷰 + fix-forward — T-020)

- 자동 머지된 PR #36(`[codex]` 관리자 인증·공개 API 키)에 대해 보안/정확성/설정/프론트/테스트 5개 차원의 다각도 적대적 코드리뷰(원시 28건 → 검증 후 확정 24건, critical/high 없음)를 수행하고 PR #36에 한글 상세 리뷰 코멘트를 게시했다.
- #36은 이미 main(`b72becaa`)에 머지되어 있어 fix-forward 방식으로 후속 수정 PR(`fix/pr36-review-followups`)을 작성했다. 적용한 변경:
  - 백엔드: 로그인 username 불일치 시에도 PBKDF2를 항상 수행(타이밍 기반 username 열거 차단), `login_audit_events` 보존 상한(`KTDM_LOGIN_AUDIT_MAX_ROWS`, 기본 5000)·logout 감사 게이트·misconfigured 경로 레이트리밋(미인증 감사 적재 방지), CORS 명시 분기의 stray `*` 제거, 공개 API 키 캐시 TTL 파싱 가드, `metrics_service.init_db` 엔진 live 참조 + 실패 시 fail-fast, `key_hint` 컬럼 폭 정렬(6), `utcnow()` 헬퍼로 deprecated `datetime.utcnow()` 일괄 제거.
  - 테스트: 세션 검증 부정 경로(쿠키 없음→401, logout 후 폐기 쿠키 재사용→401, 변조 쿠키→401, `/auth/me`), WebSocket 인증 게이트(4401/성공), 신뢰 프록시 X-Forwarded-For 처리 긍정·부정을 추가/보강(28→35 passed).
  - 프론트: 백그라운드 401 시 하드 리로드 대신 `auth-me` 무효화로 SPA 내 LoginScreen 전환(dead `next` 파라미터 제거), 로그인 비밀번호 필드 autofocus, Admin Settings 모달 dialog 시맨틱·Escape·초기 포커스, 생성 키 "지우기" 컨트롤.
  - 문서: `.env.example`에 Grafana prod 오버라이드 주석·감사 로그 상한 env 추가.
- 후속(별도 PR 권장): 신뢰 프록시 기본값(loopback) 하드닝, brute-force 스로틀 영속화, 나머지 모달(log/chart/config) a11y, 공개 API 키 캐시 멀티워커 대응 — 배포 토폴로지(reverse proxy) 영향이 있어 별도 검증과 함께 진행.
- 검증: 백엔드 `ruff check`(클린), `pytest`(35 passed), 프론트 `type-check`·`build` 통과.

---

## 2026-06-23 (관리자 로그인·세션·공개 API 키 — T-019)

- `kor-travel-geo` PR #399의 관리자 로그인·공개 API 키 패턴을 확인하고 매니저에 적용했다. 대시보드는 로그인 화면을 먼저 보여 주며, 보호 API와 WebSocket은 지정된 프론트엔드 Origin과 관리자 세션을 함께 검증한다.
- 관리자 비밀번호는 `admin` 계정용 PBKDF2 해시로 gitignore된 `.env`에만 저장하고, 세션은 HMAC 서명 `httpOnly` 쿠키와 DB 저장 세션 해시로 검증한다.
- 로그인 성공·실패·로그아웃·API 키 생성/폐기 이벤트를 `login_audit_events`에 기록하고, 관리자 설정 UI에서 감사 로그와 공개 API 키 상태를 확인하도록 했다.
- 공개 API 키는 VWorld 호환 32자리 영문/숫자 문자열로 생성하며, 원문은 생성 직후 1회만 표시한다. DB에는 SHA-256 해시와 끝 6자리 힌트만 저장하고, 활성 키 해시는 짧은 TTL 메모리 캐시로 읽되 생성·폐기 시 즉시 무효화한다.
- `kor-travel-geo` v2 API가 같은 키를 쓰도록 compose와 `.env.example`에 PR #399의 `KTG_*` 관리자 인증·공개 API 키 env 계약을 반영했다.
- PR #399 사후 리뷰 코멘트를 다시 확인하고, 매니저에 해당하는 `X-Forwarded-*` 신뢰 제한, 401 세션 만료 처리, 로그인 오류 접근성, clipboard fallback, 외부 `env_file` raw 읽기 하드닝을 추가 반영했다.
- 검증: 백엔드 `ruff check`, 백엔드 `pytest`, 프론트 `type-check`, 프론트 `build`, `docker compose config -q` 통과.

---

## 2026-06-23 (prod endpoint 문서 redaction — T-018)

- `kor-travel-map` #508과 같은 prod endpoint 노출 패턴이 이 저장소에도 있는지 확인했다. 추적 파일 기준으로 `docs/journal.md`에 남아 있던 실제 운영 도메인 표현을 placeholder로 치환했다.
- gitignore된 루트 `.env`, `frontend/.env.production`, `docs/prod-access.local.md`에는 실제 값이 남아 있으나, 저장소 커밋 대상이 아니므로 정책 범위 안으로 확인했다.

---

## 2026-06-22 — kor-travel-map 서비스 env rename + prod 도메인 정합 (by claude)

`kor-travel-map`이 패키지 rename(`KRTOUR_MAP_*`→`KOR_TRAVEL_MAP_*`, `krtour.map_dagster`→
`kortravelmap.dagster`) 이후 docker-manager의 map 서비스 블록이 구 이름 그대로라 현재 이미지로는
동작 불가했다. 4개 서비스(api/ui/dagster/dagster-daemon)를 현재 코드 기준으로 정합.

- **backend env 키 rename**: `KRTOUR_MAP_ADMIN_*`→`KOR_TRAVEL_MAP_API_*`, `KRTOUR_MAP_*`→
  `KOR_TRAVEL_MAP_*` (컨테이너가 읽는 KEY만 변경, 우변 `${...:-default}`·값은 보존 →
  기존 `krtour_map` DB / `krtour-map` bucket 데이터 연결 유지). healthcheck 포트 env 참조도 정정.
- **dagster-daemon command 모듈**: `krtour.map_dagster.definitions`→`kortravelmap.dagster.definitions`.
  (dagster webserver는 이미지 default CMD 사용 — 현재 코드라 정상.)
- **UI NEXT_PUBLIC**: 구 `NEXT_PUBLIC_KRTOUR_MAP_ADMIN_API`(localhost)→`NEXT_PUBLIC_KOR_TRAVEL_MAP_API`
  등 신 이름 + **브라우저-facing prod 도메인**(env-driven: `${KTDM_PROD_URL_MAP_API:-localhost}` 등)
  + geo 추가. map admin은 BFF 프록시가 아니라 브라우저 직접 호출이라 cross-origin prod 도메인이 필수.
- **API CORS**: prod frontend origin(`KTDM_PROD_URL_MAP`) + localhost 허용.
- 검증: `docker compose config -q` VALID. 렌더 확인 — NEXT_PUBLIC=map-api/map-dagster/geo-api 도메인,
  CORS=`["https://<map-host>",...]`, object public=s3-api/krtour-map.

---

## 2026-06-20 (운영 스택 db→conc 기동, geo 실데이터 복원, 의존성 DAG 재설정 — T-017)

- **운영 스택 기동(db→conc, 도메인 정합성 확인)**: 운영 호스트에 dev의 빌드된 이미지를 `docker save | ssh docker load`로 전송(geo ~4.3GB, concierge ~4.5GB, GDAL 재빌드 회피)하고 `ktdctl`로 하나씩 기동했다. db·storage·gra·cadv·prom·geo·conc 각 단계에서 해당 `<service-prod-host>` 계열 도메인이 503→정상(200/307/406 등)으로 전환됨을 확인했다. 매니저 API가 running 11/18을 반영.
  - rustfs 크래시(root 소유 데이터 디렉터리 Permission denied)는 digitie 소유 쓰기가능 디렉터리로 `RUSTFS_DATA_DIR`를 전환해 해결(sudo 불필요).
  - geo는 앱 스키마(ops/public/x_extension)와 `pg_stat_statements`가 필요해 처음엔 data-less 기동했고, concierge는 기동 시 자동 마이그레이션(17테이블)으로 스키마 불필요.
- **geo 실데이터 복원**: dev `kor_travel_geo`(31GB)를 `pg_dump -Fc | ssh pg_restore`로 운영 DB에 복원해 지오코딩 데이터를 살렸다(운영 geo DB를 drop/recreate 후 전체 schema+data 복원).
- **의존성 DAG 재설정(ADR-18)**: target 의존을 선형 누적에서 `depends_on` DAG로 전환했다. `geo`와 `conc`는 각각 `prom`에만 의존(상호 독립, **concierge는 geo 비의존**), `map`은 `[geo, conc]`, `pinvi`는 `[map]`. `registry.target_sequence_for_target`을 폐포 위상정렬로 재작성하고, docker-compose의 concierge-api에서 geo-api 의존 제거·map-api에 geo-api 의존 추가.
- **검증**: 백엔드 25 pytest 통과(`conc` 시퀀스에서 geo 제외 반영), `docker compose config` 통과(concierge-api: geo-postgres/rustfs만, map-api: geo-api+concierge-api 포함), ruff 통과. 문서 ADR-18·docker-management DAG·tasks 동기화.
- **비민감 처리**: 운영 접속 정보/도메인/IP는 gitignore된 `.env`·`docs/prod-access.local.md`에만, 운영 전용 설정(`RUSTFS_DATA_DIR`, `STRICT_SOURCE_CHECK=0` 등)은 운영 호스트 `.env`에만 둔다.

---

## 2026-06-20 (운영(prod) 배포 및 docker-manager 실행 검증 — T-016)

- **작업 내용**:
  - 운영 호스트에 SSH 접속 후 docker-manager를 배포·기동했다(접속 정보는 gitignore된 `docs/prod-access.local.md`/`.env`에만 기록, git 비노출). 운영 호스트는 fresh 상태(Docker만 설치, repo·매니저 미설치)였다.
  - 소스+gitignore된 운영 설정(`.env`, `frontend/.env.production`)을 rsync로 전달했다.
  - 백엔드: 운영 호스트에 `python3-venv` 미설치 + sudo 제한이라 `python3 -m venv --without-pip` 후 get-pip.py로 pip을 부트스트랩하고 `pip install -e .` → uvicorn `:12901` 기동.
  - 프론트엔드: `npm ci` + `npm run build`(`.env.production`의 `NEXT_PUBLIC_BACKEND_URL`이 번들에 인라인) + `next start :12905`.
- **검증**:
  - 백엔드 `/health` healthy, `/api/v1/containers` 18개(모두 not_created, Docker 연동 동작), 프론트 `/` HTTP 200, 번들에 운영 API 도메인 인라인 확인.
- **범위 밖(네트워크 인프라)**:
  - 운영 공개 도메인(`manager.*`/`manager-api.*`)은 DDNS로 공인 IP에 연결되나, 게이트웨이/리버스 프록시에서 매니저 포트로 라우팅이 아직 없어 외부 접근은 404다. `manager.*→:12905`, `manager-api.*→:12901` 포워딩/프록시 설정이 필요하다(저장소 밖, 라우터/게이트웨이 영역).
  - 즉 docker-manager 앱 자체는 운영 호스트에서 정상 동작 확인 완료, 공개 도메인 접근만 인프라 라우팅이 남았다.
- **문서**: `docs/prod-deployment.md`(비민감 배포 런북) 추가, `docs/prod-access.local.md`(gitignore) 기록, `.gitignore`에 `*.local.md` 추가.

---

## 2026-06-20 (Claude Code PR #23/#24 리뷰 후속 수정 — T-011/T-015)

- **작업 내용**:
  - Claude Code가 2026-06-19부터 올린 PR #23, #24(merged/closed 포함)를 확인하고 각각 후속 리뷰 코멘트를 남겼다.
  - #23 후속: 설정 변경 API와 미생성 컨테이너 start fallback이 Docker SDK 직접 `containers.run(...)` 경로로 `network_mode: host` 계약을 우회하던 문제를 수정했다. `docker-compose.yml` 저장 후 `docker compose up -d --force-recreate <service>`로 재생성하고, RustFS는 compose의 `rustfs-init` service를 그대로 실행하도록 변경했다.
  - #24 후속: 운영 콘솔 첫 화면을 compact top bar + KPI strip 중심으로 정리하고, UI/데이터 표시용 font token을 명시했다.
- **검증**:
  - 백엔드 `ktd_venv/bin/python -m ruff check .` 통과.
  - 백엔드 `ktd_venv/bin/python -m pytest` 25 passed.
  - 프론트 `npm run type-check`, `npm run build` 통과.
  - `docker compose config` 통과.
- **주의**:
  - `codegraph sync`는 로컬 `.codegraph` disk I/O 오류로 실패해 직접 파일 확인과 테스트로 검증했다.

---

## 2026-06-20 (프론트엔드 Tailwind v4 + StyleSeed 전면 전환 및 전역 오류 복구 boundary — T-015)

- **작업 내용**:
  - **오류 복구 boundary**(`kor-travel-geo` PR #391 반영): App Router `app/error.tsx`/`app/global-error.tsx`, `components/layout/AppErrorPanel.tsx`, `lib/error-recovery.ts`를 추가했다. Next 기본 영어 오류 화면 대신 한국어 복구 패널을 보여 주고, chunk/RSC/network 계열 런타임 오류는 sessionStorage flag로 같은 pathname당 1회 hard reload를 시도한다.
  - **Tailwind v4 전환**: `globals.css`를 `@import "tailwindcss"` + `@theme` CSS-first로 바꾸고, `postcss.config.js`를 `@tailwindcss/postcss`로 교체, `package.json` 의존성을 tailwindcss/@tailwindcss/postcss `^4`로 올리고 autoprefixer 제거, v3 `tailwind.config.ts`를 삭제했다.
  - **StyleSeed 라이트 토큰**(`kor-travel-geo-ui/docs/DESIGN-RULES.md` 반영): `@theme`에 surface(page/card/subtle/elevated/row), 5단계 text(strong/ink/secondary/tertiary/disabled), 단일 brand teal(`#0f766e`), status(info/warn/danger/ok), 약한 shadow, motion 토큰을 정의했다. `DashboardClient`와 `AppErrorPanel`을 Pure Black 다크에서 이 토큰으로 전면 리스타일(단일 accent·약한 그림자·44px 터치타깃·상태 dot+text·rounded-card)했다.
  - 문서: `docs/DESIGN-RULES.md` 신규(매니저용 포팅), `DESIGN.md`에 StyleSeed 전환 superseded 안내, ADR-17 추가.
- **검증**:
  - v4 의존성 설치(tailwindcss 4.3.1, @tailwindcss/postcss 4.3.1, oxide 네이티브 엔진) 완료.
  - 프론트 `type-check`·`build` 통과(아래 최종 검증 절에서 재확인).
  - 잔여 Pure Black 토큰(`bg-black`/`text-on-dark`/`border-hairline`/`m-blue-*`) 0건 확인.

---

## 2026-06-20 (Docker host 네트워크 전환·컨테이너=호스트 포트 통일·서비스 prod URL 반영·pinvi-dagster 추가·tripmate 잔재 정리 — T-014)

- **작업 내용**:
  - **host 네트워크(dev 기본)**: `docker-compose.yml` 전 서비스(19개)에 `network_mode: ${KTDM_DOCKER_NETWORK_MODE:-host}`를 적용했다. 포트 NAT가 없는 host 모드에 맞춰 인프라(RustFS 12101/12105, Grafana 12205, Prometheus 12401, cAdvisor 12301)와 앱이 호스트 정규 포트에 직접 바인딩하도록 바꾸고, 서비스 간 참조(PostgreSQL DSN, RustFS 엔드포인트, 내부 API/Dagster URL)를 컨테이너명 → `127.0.0.1:<포트>`로 전환했다. `config/prometheus/prometheus.yml` scrape 타깃과 `scripts/ensure-rustfs-buckets.sh` 엔드포인트도 `127.0.0.1` 기준으로 맞췄다.
  - **컨테이너=호스트 포트 통일**: `kor-travel-geo`(이미 동일), `kor-travel-concierge`(api `--port`, mcp `MCP_PORT`, ui는 `next dev` command 오버라이드), `kor-travel-map`(`*_CONTAINER_PORT` 기본값을 12701/12702/12705로), PinVi(api `--port`, web은 `next start -p` command 오버라이드)를 모두 컨테이너 내부 포트 = 호스트 포트로 맞췄다.
  - **PinVi Dagster 추가**: `pinvi-dagster`(host=container 12802) compose service와 registry 컨테이너/`pinvi` target 편입을 추가하고, upstream PinVi 저장소에 `apps/etl/Dockerfile`(python:3.12-slim, editable 설치, `dagster-webserver -m tripmate.etl.definitions`)을 신규 작성했다.
  - **서비스 prod 공개 URL 반영**: 관리 16개 서비스(geo … s3-api)의 운영 공개 주소를 gitignore된 `.env`의 `KTDM_PROD_URL_*`에 저장하고, `docker-targets.yml`의 `prod_url_env`(환경변수 이름만 커밋)와 `docker_service._public_url()`로 읽어 대시보드 `public_url` 링크로 표시하도록 백엔드/프론트엔드를 확장했다. `.env.example`은 example.org 플레이스홀더로만 문서화(도메인 비노출).
  - **tripmate 잔재 정리**: 루트 `tripmate_metrics.db` → `pinvi_metrics.db`(코드는 이미 `pinvi_metrics.db` 사용) 개명, 백엔드 venv `tripmate_venv` 제거 후 문서 표준명 `ktd_venv`로 재생성, 잔여 `backend/logs/tripmate_manager.log` 제거. 추적 코드에는 과거 명칭 잔재가 없었고 journal의 과거 이력 기록만 보존했다.
  - 문서: ADR-16 추가, `docs/tasks.md` T-014 등록, `docs/docker-management.md`(컨테이너 18개·host 모드·포트 동일·pinvi-dagster), `docs/ports.md`(12802·host 모드) 동기화.
- **검증**:
  - `docker compose config` exit 0, 경고/에러 0. 19개 서비스 `network_mode: host`, 모든 published 포트가 host=container, `pinvi-dagster` 렌더링 확인.
  - 백엔드 `ruff check`, `public_url` 해석 단위 검증. 프론트 `type-check`·`build` 통과(예정 항목은 최종 검증 절에서 재확인).
  - 추적 파일 전수 grep으로 실제 도메인·잔여 `tripmate`·구 포트 참조 부재 확인.
- **런타임 검증 필요(범위 외 주의)**:
  - host 모드 실제 기동은 Docker 엔진의 host networking 지원에 의존하므로 사용자 환경에서 `ktdctl <target> --build` 런타임 검증이 필요하다.
  - `pinvi-dagster`는 PinVi `apps/etl` ETL 모듈이 미완(Sprint 1 stub)이라 webserver 기동은 upstream 모듈 상태에 따라 달라질 수 있다.

---

## 2026-06-20 (운영 공개 주소 `.env` 주입 및 CORS 환경변수화 — T-013)

- **작업 내용**:
  - 매니저 백엔드 API/대시보드의 운영 공개 도메인을 소스에 하드코딩하지 않고 gitignore된 env 파일에만 주입하도록 설정 계층을 정비했다(외부 비노출).
  - 백엔드(`main.py`): 기동 시 루트 `.env`(또는 `KOR_TRAVEL_DOCKER_MANAGER_ENV_FILE`)를 `load_dotenv`로 로드하고, CORS 허용 Origin을 `KTDM_CORS_ALLOW_ORIGINS`(콤마 구분, 미설정/`*`이면 전체 허용)로 환경변수화했다. 기존 `allow_origins=["*"]` 개발 기본 동작은 유지.
  - 프론트엔드: 백엔드 주소를 환경별로 분리했다. `frontend/.env.development`(localhost), `frontend/.env.production`(운영 API 도메인)을 추가하고, Next.js 우선순위상 `.env.local`이 `.env.production`을 덮어쓰는 사고를 막기 위해 `.env.local`에서 `NEXT_PUBLIC_BACKEND_URL`을 제거(주석화)했다. WS 주소는 `http→ws` 치환으로 `wss`가 자동 파생된다.
  - 계약 문서화: 루트 `.env.example`과 신규 `frontend/.env.example`에 새 변수를 **플레이스홀더**(`manager.example.org` 등)로만 기재해 실제 도메인 노출 없이 셋업을 안내했다. 실제 값은 gitignore된 루트 `.env`, `frontend/.env.production`에만 존재.
  - 문서: ADR-15 추가, `docs/tasks.md` T-013 등록·완료, `docs/dev-environment.md` 4.3 운영 공개 주소 절 추가.
- **검증**:
  - `git check-ignore`로 실제 도메인 파일 4종(`.env`, `frontend/.env.{production,development,local}`) ignore 확인, 추적 파일 전수 grep에서 실도메인 누출 0건.
  - 백엔드: 전체 `ruff check` 통과, CORS 파싱 검증(콤마 리스트 trim, `*`/미설정→전체 허용, 루트 `.env` end-to-end 로드로 운영 Origin 적용).
  - 프론트엔드: `@next/env`로 환경 우선순위 결정적 검증(prod→운영 API 도메인, dev→localhost, `.env.local` 섀도잉 없음), `npm run type-check`·`npm run build` 통과, 운영 빌드 번들에 API 도메인 인라인 및 `.next` ignore 확인.

---

## 2026-06-17 (브랜드 표기 PinVi 교정 및 관측 컨테이너 재기동)

- **작업 내용**:
  - PR #19에서 `Pinvi`로 표기된 브랜드명을 정식 표기 `PinVi`로 교정했다. 14개 파일에서 대소문자 구분 치환으로 73개 표기를 수정했다(.env.example, AGENTS.md, CLAUDE.md, SKILL.md, README.md, config/docker-targets.yml, docs/*).
  - 소문자 식별자(`pinvi`, `pinvi-api-latest`, `pinvi-media`)와 환경변수 prefix(`PINVI_*`)는 런타임/계약 식별자이므로 그대로 두고, 사람이 읽는 표시 문자열만 `PinVi`로 맞췄다.
  - PR #19의 공용 컨테이너 명칭 변경(`tripmate-* -> kor-travel-*`)을 실제 런타임에 반영하기 위해 관측 컨테이너(rustfs/prometheus/grafana/cadvisor)를 새 이름으로 재생성했다. 공용 DB(`kor-travel-geo-postgres`)와 실행 중인 concierge 스택은 유지했다.
- **검증**:
  - 대소문자 구분 `Pinvi` 잔여 검색 0건, `PinVi` 정상 반영 확인.
  - `docker ps`로 `kor-travel-prometheus/grafana/cadvisor/rustfs` 신규 이름 기동 및 Prometheus `/-/healthy` 200 확인.

---

## 2026-06-17 (멀티 에이전트 MCP/agent/skill 설정 확장 — filesystem MCP & OpenCode 포팅)

- **작업 내용**:
  - Claude Code(`claude.json`), Codex(`codex.json`, `.codex/config.toml`), Antigravity(`antigravity.json`), OpenCode(`opencode.json`) 네 도구에 `@modelcontextprotocol/server-filesystem` MCP 서버를 추가했다. 허용 디렉터리는 각 도구의 worktree(`...-claude`, `...-codex`, `...-antigravity`, `...-opencode`)로 지정해 기존 codegraph cwd 규칙과 일치시켰다.
  - OpenCode에 없던 기존 MCP 설정·agent·skill을 OpenCode 형식으로 포팅했다.
    - **MCP**: `opencode.json`에 playwright/sequential-thinking/codegraph/filesystem 4개 서버를 OpenCode local 스키마(`type:"local"`, `command` 배열, `environment`)로 정의하고 `skills.paths`에 `.opencode/skills`를 등록했다.
    - **Agent**: `.opencode/agent/`에 6개 subagent(api-designer, backend-developer, frontend-developer, mobile-developer, ui-designer, ui-fixer)를 추가했다. Claude markdown 5종은 본문을 그대로 보존하고 frontmatter만 OpenCode 형식(`mode: subagent`, `tools` 맵)으로 변환했으며, Codex에만 있던 ui-fixer는 새로 작성했다.
    - **Skill**: `.opencode/skills/`에 postgres 외 8개 skill(SKILL.md + postgres/references 7종)을 원본과 byte 동일하게 복제했다.
  - codegraph의 `cwd`는 OpenCode local MCP 스키마에 없는 필드(런타임이 instance 디렉터리에서 자동 설정)라 `opencode.json`에서는 제외했다.
- **검증**:
  - 4개 JSON 설정 `ConvertFrom-Json` 파싱 통과, `.codex/config.toml` 포함 5개 설정에서 filesystem 서버 존재 확인.
  - skill 15개 파일 SHA256 원본 동일, 포팅한 agent 5종 본문이 원본과 byte 동일.
  - 5개 병렬 감사 에이전트로 적대적 검증 수행: filesystem 추가·agent·skill·완전성은 모두 pass, opencode.json은 codegraph `cwd` 제거로 해결.
- **범위 외 메모**:
  - `.gemini/mcp.json`은 사용자가 지정한 4개 도구에 포함되지 않아 filesystem을 추가하지 않고 현 상태를 유지했다.
  - playwright 패키지명 `@modelcontextprotocol/server-playwright`는 기존 4개 설정과 동일하게 유지했다(일관성). 공식 `@playwright/mcp`로의 전환은 전체 설정 동기화가 필요한 별도 사안.

---

## 2026-06-15 (PinVi 및 Kor Travel 공용 명칭 정리)

- **작업 내용**:
  - 남아 있던 과거 서비스명 계열 명칭을 PinVi 기준으로 정리했다.
  - 공용 컨테이너 성격이 강한 RustFS, Grafana, cAdvisor, Prometheus 이름은 `kor-travel-*` 기준으로 변경했다.
  - PinVi 전용 database/role/bucket/env 이름을 `pinvi`, `PINVI_*`, `pinvi-media` 기준으로 맞췄다.
  - 과거 geo 패키지명 계열 잔여 명칭이 없는 것을 확인했다.
- **검증**:
  - WSL에서 과거 서비스명과 과거 geo 패키지명 계열 잔여 검색 결과 0건 확인.

---

## 2026-06-13 (`geo -> conc -> map -> pinvi` target 흐름 반영)

- **작업 내용**:
  - 사용자 지시에 따라 앱 target 순서를 `geo -> conc -> map -> pinvi`로 재정렬했다.
  - `kor-travel-concierge` API/MCP/Scheduler/Web UI를 `conc` target의 실제 compose service로 추가했다.
  - `kor-travel-map` API/Dagster/Web UI를 `map` target의 실제 compose service로 추가해 `ktdctl map --build`가 이미지를 빌드하고 실행하도록 변경했다.
  - PinVi API/Web UI를 `pinvi` target으로 추가하고 짧은 별칭 `srv`와 기존 호환 별칭 `main`을 연결했다.
  - 공용 DB 복구에 `krtour_map_dagster` database를 추가하고, RustFS bucket 복구에 `kor-travel-concierge` bucket을 추가했다.
  - CLI 직접 alias 처리를 registry 기반으로 바꿔 `conc`, `srv` 같은 새 alias가 자동 반영되게 했다.
- **검증**:
  - `docker compose config --quiet` 통과.
  - `scripts/ensure-kor-travel-geo-db.sh`, `scripts/ensure-rustfs-buckets.sh` `bash -n` 통과.
  - `PYTHONPATH=src backend/pinvi_venv/bin/python`으로 registry 해석 확인: `map`은 `db -> storage -> gra -> cadv -> prom -> geo -> conc -> map`, `srv`는 `... -> pinvi`로 resolve.

---

## 2026-06-13 (`kor-travel-geo` UI Prometheus scrape 추가)

- **작업 내용**:
  - `kor-travel-geo-ui`의 Next.js Prometheus endpoint(`/api/metrics`)를 scrape하도록 `kor-travel-geo-ui:12505` target을 추가했다.
- **검증**:
  - `docker compose config` 통과.
  - `config/prometheus/prometheus.yml`에서 `kor-travel-geo-api:12501`, `kor-travel-geo-ui:12505` scrape target 확인.

---

## 2026-06-13 (Grafana/cAdvisor/Prometheus target 개별 분리)

- **작업 내용**:
  - 단일 관측 target을 제거하고 `gra`, `cadv`, `prom`을 독립 CLI/API target으로 분리했다.
  - dependency 순서를 `db -> storage -> gra -> cadv -> prom -> geo -> map -> ai -> main`으로 조정했다.
  - Grafana는 공용 연계를 위해 `12205`, cAdvisor는 `12301`, Prometheus는 `12401`을 사용하도록 재배치했다.
  - `kor-travel-geo` API/Web UI는 새 dependency 순서에 맞춰 `12501`, `12505`로 이동했다.
  - Prometheus scrape target을 `kor-travel-geo-api:12501/metrics`로 갱신했다.
  - CLI 직접 별칭, API/CLI 테스트, 포트 문서, Docker 관리 문서, 개발 가이드를 같은 기준으로 갱신했다.
- **결정 사항**:
  - Grafana, cAdvisor, Prometheus compose service는 서로 `depends_on`으로 묶지 않고 독립 실행 가능하게 둔다.
  - `geo` 이상 target은 새 dependency 순서상 관측 컨테이너를 선행 실행한다.
- **검증**:
  - WSL `backend/pinvi_venv`에서 `ruff check .` 통과.
  - WSL `backend/pinvi_venv`에서 `pytest` → 22 passed.
  - WSL 프론트엔드에서 `npm run type-check`, `npm run build` 통과.
  - WSL Docker에서 Grafana `12205`, cAdvisor `12301`, Prometheus `12401`, `kor-travel-geo` API `12501`, Web UI `12505`로 재기동 완료.
  - HTTP 확인: Grafana `/api/health` 200, cAdvisor `/healthz` 200, Prometheus `/-/ready` 200, Geo API `/v1/healthz` 200, Geo UI `/` 307, RustFS `/health/live` 200.

---

## 2026-06-13 (`kor-travel-geo` DB명·환경변수·Prometheus scrape 계약 동기화)

- **작업 내용**:
  - 사용자 지시에 따라 `kor-travel-geo`가 현재 사용하는 DB명과 환경변수 계약에 Docker manager compose를 맞췄다.
  - `kor-travel-geo` DB 기본값을 `kor_travel_geo`로 변경했다.
  - manager override 변수는 `KOR_TRAVEL_GEO_*`로, API/UI 컨테이너 내부 환경변수는 앱이 읽는 `KTG_*`로 변경했다.
  - Docker service/container 이름과 target registry를 `kor-travel-geo-*` 기준으로 변경했다.
  - `/home/digitie/kor-travel-geo-data`를 PostgreSQL, RustFS, Prometheus, Grafana의 물리 데이터 디렉터리 기준으로 반영했다.
  - `kor-travel-geo` RustFS bucket 기본값을 `kor-travel-geo`로 맞췄다.
  - Prometheus scrape 설정에 `kor-travel-geo-api` job을 추가했다.
- **결정 사항**:
  - 기존 bind mount와 compose label을 가진 manager 스택 컨테이너는 중지 후 제거하고, 새 compose 기준으로 재생성 가능한 상태로 둔다.
  - 물리 데이터 디렉터리 이름도 프로젝트 공식명과 맞춘다.
- **검증**:
  - WSL Docker에서 manager 스택 컨테이너를 중지·제거하고 `/home/digitie/kor-travel-geo-data`로 데이터 디렉터리 이동 완료.
  - 과거 geo 이름 계열 문자열 검색 결과 없음.
  - `bash -n scripts/ensure-kor-travel-geo-db.sh scripts/verify-kor-travel-geo-source.sh scripts/ensure-rustfs-buckets.sh` 통과.
  - `docker compose config`에서 `KTG_PG_DSN=postgresql+psycopg://addr:addr@kor-travel-geo-postgres:5432/kor_travel_geo`, `POSTGRES_DB=kor_travel_geo` 확인.
  - `git diff --check` 통과.
  - WSL `/tmp/ktdm-venv` 임시 가상환경에서 `ruff check .` 통과.
  - WSL `/tmp/ktdm-venv` 임시 가상환경에서 `pytest` → 22 passed.
  - 프론트엔드 `npm run type-check` 통과.
  - `npm run lint`는 Next.js ESLint 초기 설정 프롬프트로 비대화형 실행이 중단됨.
  - `npx react-doctor@latest . --offline --verbose --json`은 실패 없이 완료했으며 기존 Next.js 14 보안 경고와 `DashboardClient` 구조성 경고 4건을 보고.

---

## 2026-06-13 (Kor Travel Docker Manager 프로젝트명 전환)

- **작업 내용**:
  - 프로젝트 공식명을 `Kor Travel Docker Manager` / `kor-travel-docker-manager`로 바꾸고 문서, package metadata, 프론트엔드 metadata를 동기화했다.
  - 백엔드 import package를 `kor_travel_docker_manager`로 변경하고 ASGI entrypoint 문서를 `kor_travel_docker_manager.main:app`으로 갱신했다.
  - CLI console script를 `ktdctl`로 전환하고 이전 CLI 명령 안내를 제거했다.
  - Docker Compose project name을 `kor-travel-docker-manager`로 고정해 network prefix를 새 프로젝트명 기준으로 통일했다.
- **결정 사항**:
  - 이전 CLI 이름을 병행 제공하지 않고 `ktdctl`만 공식 인터페이스로 둔다.
  - GitHub 저장소명은 코드 변경 PR 병합 후 `kor-travel-docker-manager`로 rename한다.

---

## 2026-06-13 (과거 이름 helper 제거 및 rebase 재검토)

- **작업 내용**:
  - `origin/main` 기준 최신 머지 상태를 확인한 뒤 `agent/remove-old-name-helper` 브랜치에서 재검토했다.
  - 과거 프로젝트명 기반 target alias와 fallback env 검색을 재수행하고, 남은 문서 표현과 UI 기본 표시명을 `kor-travel-geo`, `kor-travel-concierge` 기준으로 정리했다.
  - target을 중복 하드코딩하던 보조 shell helper를 제거하고, 공식 실행 경로를 `ktdctl` CLI와 API/dashboard registry로 단일화했다.
  - `ktdctl gra`, `ktdctl cadv`, `ktdctl prom`, `ktdctl all`도 직접 `ensure`로 해석되도록 CLI 직접 target 목록을 registry target과 맞췄다.
- **결정 사항**:
  - 과거 이름 수용 목적 alias/fallback/helper는 유지하지 않는다.
  - 실제 Docker container/service 이름과 물리 데이터 디렉터리는 후속 작업에서 `kor-travel-geo` 기준으로 맞춘다.

---

## 2026-06-13 (Prometheus/Grafana/Exporter 관측 스택 분리)

- **작업 내용**:
  - `docker-compose.yml`에 Prometheus, Grafana, cAdvisor Exporter를 각각 별도 Docker service로 추가했다.
  - 포트 정책에 맞춰 Grafana, cAdvisor Exporter, Prometheus를 각각 관리 컨테이너로 등록했다.
  - `config/docker-targets.yml`에 `grafana`, `prometheus`, `cadvisor` 관리 컨테이너를 등록했다.
  - Prometheus scrape 설정(`config/prometheus/prometheus.yml`)과 Grafana Prometheus datasource provisioning을 추가했다.
  - 관리 UI 목록에서 Prometheus, Grafana, cAdvisor Exporter가 역할별 아이콘과 표시명으로 구분되도록 프론트엔드 표시 로직을 보강했다.
  - `.env.example`, `docs/architecture.md`, `docs/docker-management.md`, `docs/ports.md`, `docs/decisions.md`, `docs/tasks-done.md`를 같은 기준으로 갱신했다.
- **결정 사항**:
  - Exporter는 Docker 컨테이너 리소스 메트릭에 적합한 cAdvisor를 사용하고, Grafana는 Prometheus datasource를 자동 등록한다.
  - `all` target에는 관측 스택까지 포함해 전체 로컬 인프라 실행 시 함께 올라가도록 한다.

---

## 2026-06-12 (태스크 장부 정리 및 kor-travel-concierge 선행 작업 등록)

- **작업 내용**:
  - 완료된 `T-001`~`T-010`, `T-013`~`T-016`을 `docs/tasks-done.md`로 분리하고, `docs/tasks.md`에는 진행 중/대기 작업만 남겼다.
  - 미완료 작업 `T-011`, `T-012`를 유지하고, `kor-travel-concierge` provider 상세 구현 및 명칭 전환을 `T-220` 선행 작업으로 등록했다.
  - 사용자 지정 순서인 `T-221`, `T-222`, `T-223`을 `T-220` 이후 순차 진행 항목으로 추가했다.
- **결정 사항**:
  - `T-221` 착수 전 `kor-travel-concierge` 잔여 명칭과 `pinvi` 직접 의존 설명을 먼저 정리한다.
  - `T-221`~`T-223`의 세부 범위는 현재 `kor-travel-docker-manager` 저장소 장부에 없으므로, `T-220` 완료 후 작업 전 상세 항목을 확정한다.

---

## 2026-06-12 (`kor-travel-geo` Docker API/UI 관리 편입)

- **작업 내용**:
  - `docker-compose.yml`에 `kor-travel-geo-api`, `kor-travel-geo-ui` 서비스를 추가해 `kor-travel-geo` REST API와 admin Web UI를 manager에서 함께 실행할 수 있게 했다.
  - `config/docker-targets.yml`에 `kor-travel-geo-api-latest`, `kor-travel-geo-ui-latest`를 공식 관리 컨테이너로 등록하고 `geo` 이상 target에 포함했다.
  - 포트 정책에 맞춰 API와 Web UI 포트를 배정하고, API 컨테이너가 compose 네트워크의 `kor-travel-geo-postgres:5432`, `rustfs:9000`을 사용하도록 설정했다.
  - `.env.example`, `docs/docker-management.md`, `docs/architecture.md`, `docs/ports.md`, `docs/dev-environment.md`, `README.md`, `docs/tasks.md`를 같은 기준으로 갱신했다.
- **결정 사항**:
  - 기존 `kor-travel-geo` 로컬 script와 같은 컨테이너 이름(`kor-travel-geo-api-latest`, `kor-travel-geo-ui-latest`)을 사용해 대시보드와 CLI가 기존 Docker 대상을 그대로 확인할 수 있게 한다.

---

## 2026-06-12 (WSL/Windows 실행 위치 정책 고정)

- **작업 내용**:
  - `git` 명령은 Windows 호스트에서만 실행하고, 패키지 설치·Docker·서버 실행·빌드·테스트·파일 검색 등 일반 개발 명령은 WSL에서만 실행하도록 문서화.
  - Playwright E2E는 실제 Windows 브라우저 환경 확인을 위한 명시 예외로 Windows 호스트에서 실행하도록 고정.
  - `AGENTS.md`, `SKILL.md`, `docs/dev-environment.md`, `CLAUDE.md`, `docs/tasks.md`에 실행 위치 정책을 반영.
- **결정 사항**:
  - Windows 경로가 문서에 나오더라도 git과 Playwright E2E를 제외한 명령 실행은 `/mnt/f/...` WSL 경로를 사용한다.

---

## 2026-06-12 (Kor Travel/PinVi 전용 Docker Manager CLI/API 및 문서 정리)

- **작업 내용**:
  - **통합 DB 모델 공식화**: `kor-travel-geo-postgres:5432` 하나에 `kor_travel_geo`, `pinvi`, `kor_travel_concierge`, `krtour_map` database를 담는 현재 구조를 공식 기준으로 문서화하고, 과거 분리 DB 기준 문구를 정리.
  - **target registry 도입**: `db`, `storage`, `geo`, `map`, `ai`, `main`, `all` target을 API/CLI가 공유하도록 정의.
  - **Python CLI 추가**: `ktdctl targets/status/ensure/logs/action/inspect` 명령을 추가하고, 개발환경에서 `ktdctl <alias> --build`로 의존 Docker를 바로 실행할 수 있게 함.
  - **짧은 CLI 별칭 추가**: `db`, `storage`, `gra`, `cadv`, `prom`, `geo`, `map`, `ai`, `main`을 공식 별칭으로 두고 `config/docker-targets.yml`의 dependency 순서를 따라 누적 실행하도록 구현.
  - **포트 정책 일원화**: PostgreSQL host 포트를 `5432`로 변경하고, RustFS는 `12101`/`12105`, manager API/Web은 `12901`/`12905`로 정리.
  - **초기화/복구 step 추가**: 통합 DB database/role/schema/extension 복구, RustFS bucket 복구, `kor-travel-geo` 원천 디렉터리와 핵심 테이블 적재 검증을 `ensure` 흐름에 연결.
  - **API 확장**: `GET /api/v1/targets`, `POST /api/v1/targets/{target}/ensure`, `GET /api/v1/containers/{container_id}/inspect`를 추가.
  - **Docker inspect redaction**: inspect 응답에서 password, secret, token, access key 계열 environment 값을 마스킹하도록 구현.
  - **문서 보강**: `docs/docker-management.md`를 신규 작성하고, `architecture`, `decisions`, `tasks`, `dev-environment`, `README`, 에이전트 가이드를 통합 DB/CLI 기준으로 갱신.
- **결정 사항**:
  - Docker 생명주기와 `--build`는 `docker compose` 인자 배열 실행으로 처리하고, stats/logs/inspect/action은 Docker SDK를 유지한다(ADR-7).
  - target alias와 초기화 step은 `config/docker-targets.yml`을 source of truth로 삼는다(ADR-8).
- **다음 작업**:
  - 대시보드 상세 패널에서 inspect API를 연결하고, compose 설정 변경 전 diff/validation을 강화한다.

## 2026-06-11 (WSL 네트워크 연결 복구 및 월 단위 로그 롤링 구현)

- **작업 내용**:
  - **WSL 가상 IP 통신 거부 결함 최종 해결**: 브라우저에서 `172.26.51.35:9091`로 백엔드 API에 접속 시, 포트 9091이 윈도우 프로세스(Firefox 등)의 좀비 커넥션 및 WSL2 포트 맵 꼬임으로 인해 접근 거부되던 현상을 해결. Windows powershell에서 WSL을 강제 종료(`wsl --shutdown`) 및 가상 어댑터를 리셋하여 9091 바인딩 꼬임 문제를 완벽히 해결 및 연결 정상 복구 완료.
  - **월 단위 로그 파일 롤링 및 1년 보관 로직 추가**: uvicorn 서버의 작동 로그 출력을 매월 1일 단위로 분할하여 `kor_travel_docker_manager.log.YYYY-MM` 형태로 백업하고, 1년(365일)이 지난 로그 파일을 자동으로 탐색하여 청소하는 백그라운드 클린업 스레드를 추가하여 로깅 유지 비용 제어.
  - **백엔드 가상환경 재구축 및 WebSocket 라이브러리 추가**: 기존 `.venv` 가상환경 내에 WebSocket 구동에 필수적인 `websockets` 라이브러리가 누락되었고, 파일 락(Lock) 및 패키지 찌꺼기로 인해 pip 설치가 교착 상태에 빠지던 이슈를 발견. Windows PowerShell을 통해 기존 가상환경을 강제 제거하고, WSL Python 3.12를 기반으로 하는 수동 가상환경을 깨끗하게 재구축한 뒤 `websockets`, `fastapi` 등의 필수 의존성을 완벽하게 재설치 완료.
  - **백엔드 실행 경로 매핑 및 PYTHONPATH 주입**: 백엔드 수동 기동 시 `PYTHONPATH=src` 환경 변수를 주입하여 uvicorn이 `kor_travel_docker_manager` 패키지 모듈을 바르게 탐색할 수 있도록 조정했다.
  - **대시보드 UI 글씨 크기 조정**: 테이블 컬럼 제목의 폰트 크기를 `text-[10px]`에서 `text-xs md:text-sm`으로 키우고, 테이블의 각 셀 내용(상태, 명칭, 역할, 포트 바인딩, 리소스 수치) 및 리차트(Recharts) 기반 그래프의 틱(Ticks), 범례(Legend), 툴팁(Tooltip)의 폰트 크기를 1~2px씩 일제히 상향하여 시인성 대폭 개선.
- **결정 사항**:
  - WSL 환경과의 통신 결함을 방지하기 위해 백엔드 접속 주소는 `localhost:9091`을 기본값으로 사용한다. (다만 가상 IP 바인딩을 활용하는 경우 프론트엔드가 환경에 맞추어 `http://172.26.51.35:9091`로 수동 통신하도록 .env.local을 구성한다.)
  - 가상환경 락 이슈 해결을 위해 캐시 및 락 찌꺼기가 남은 기존 `.venv`를 우회하는 수동 가상환경을 구축하여 사용한다.

## 2026-06-11 (대시보드 M 룩앤필 교정, CSS 링크 결함 수정 및 react-doctor 최적화 완료)

- **작업 내용**:
  - **디자인 가이드 대시보드 이식 및 교정**: 대시보드 메인 화면 상단에서 부적절한 자동차 피트라인 배경 이미지(`/images/pit_lane_night.png`) 및 억지스러운 모터스포츠 비유를 완전히 배제하고, Pure Black 배경과 얇은 hairline border 및 4px M 삼색선 디바이더로 구성된 실용적인 IT 인프라 대시보드 룩앤필로 정교화 및 수렴.
  - **CSS 폰트 로드 링크 결함 수정**: `next/font/google`을 활용한 폰트 로드를 완료하고, `layout.tsx`의 body에 `font-sans`를 명시적으로 매핑하여 런타임 상의 CSS 폰트 링크 깨짐을 완전히 차단.
  - **아키텍처 리팩토링 및 react-doctor 경고 제거**: `page.tsx`를 Server Component로 전환하여 메타데이터를 노출하고, 1,025라인의 대형 컴포넌트를 `src/components/DashboardClient.tsx` (Client Component)로 완벽히 분리. 또한 dynamic import(recharts), aria-label(접근성), stable key(key={idx} 대체), useMemo(derived state 제거), WebSocket 마운트 state 최적화를 적용하여 `react-doctor` 경고 25건을 모두 해결.
  - **디자인 시스템 문서화**: 디자인 시스템 적용 범위를 실제 대시보드의 테이블, 모달, 차트 모듈 사양으로 갱신하여 디자인 일관성 가이드를 강화.
  - **포트 확정 및 적용**: API 구동 포트를 9091로, WEB 구동 포트를 9092로 최종 확정하고, 소스코드(main.py, DashboardClient.tsx, env) 및 문서(CLAUDE.md, dev-environment.md)에 일제히 동기화 반영 완료.
- **결정 사항**:
  - 디자인 일관성 및 코드 품질 향상을 위해 서버-클라이언트 컴포넌트 분리 및 react-doctor 최적화 규칙을 반영함 (ADR-6).

## 2026-06-11 (실시간 컨테이너 모니터링 테이블, WebSocket 및 성능 차트 구현)

- **작업 내용**:
  - **백엔드**: `main.py`의 lifespan 동작 시 `metrics_service` 임포트 누락으로 인해 `NameError`가 발생하던 결함을 발견하고, `from kor_travel_docker_manager.services.metrics_service import metrics_service`를 임포트 목록에 추가하여 해결.
  - **백엔드**: SQLite3 데이터베이스 연동(`metrics_service.py`) 및 10초 주기 Docker stats 메트릭 수집기(`metrics_collector.py`) 구현. 최신 리소스 캐시 및 30일 만료 규칙 적용.
  - **백엔드**: WebSocket 라우트(`websocket.py`) 구현. `/api/ws/status`를 통한 상태/메트릭 실시간 브로드캐스트 및 `/api/ws/logs/{container_id}`를 통한 컨테이너 로그 스트리밍 제공.
  - **백엔드**: 지난 1시간의 수집 기록을 조회하는 GET `/api/containers/{container_id}/metrics` API 추가.
  - **프론트엔드**: 기존의 컨테이너 카드 뷰를 Premium Glassmorphic Table 형태로 전면 개편(`page.tsx`).
  - **프론트엔드**: WebSocket 실시간 상태 동기화 및 끊김 시 5초 폴링 Fallback 로직 연동.
  - **프론트엔드**: 터미널 스타일 로그 스트리밍 모달 다이얼로그 및 Recharts 기반의 1시간 리소스 이력 라인 차트 모달 기능 추가.
- **결정 사항**:
  - 실시간 리소스 모니터링 및 로그 스트리밍을 제공하기 위해 WebSockets 아키텍처를 도입하고, 기존 TanStack Query를 Fallback용으로 하이브리드 운영.
- **다음 작업**:
  - 개별 컨테이너 환경설정 업데이트 동작 확인 및 최종 사용자 테스트.

## 2026-06-10 (kor-travel-geo PostgreSQL/RustFS 인프라 이관)

- **작업 내용**:
  - `docker-compose.yml`에 `kor-travel-geo` 전용 `kor-travel-geo-postgres` 서비스를 추가하고, 기존 T-027 최종 DB 접속 계약을 `kor-travel-docker-manager` 기본 설정으로 이관했다.
  - 공용 RustFS 서비스의 포트, credential, 데이터 디렉터리, bucket 초기화를 `.env.example`과 compose에 명시하고 `kor-travel-geo` bucket을 함께 생성하도록 했다.
  - 초기 helper 명령을 추가해 `up/stop/restart/status/logs`를 주요 target 단위로 실행할 수 있게 했다.
  - 백엔드/프론트엔드 대시보드가 당시의 PostgreSQL/RustFS 관리 대상을 표시하도록 갱신했다.
- **결정 사항**:
  - PostgreSQL/RustFS Docker 생명주기와 로컬 포트 계약은 `kor-travel-docker-manager`가 관리한다(ADR-5).
- **다음 작업**:
  - compose live smoke와 대시보드의 compose create 액션 확장 여부를 후속으로 검토한다.

## 2026-06-10 (인프라 매니저 프로젝트 초기화 및 가이드라인 복사)

- **작업 내용**:
  - `maplibre-vworld-js` 저장소를 기반으로 AI 에이전트 개발 및 협업 가이드라인 (`CLAUDE.md`, `AGENTS.md`, `SKILL.md`) 복사 및 `kor-travel-docker-manager` 목적에 맞게 수정.
  - 에이전트 설정 파일 (`antigravity.json`, `claude.json`, `codex.json`, `.gemini/mcp.json`, `.claude/settings.local.json`, `.codex/config.toml`) 설정 완료.
  - 아키텍처 가이드(`docs/architecture.md`) 및 의사결정 기록(`docs/decisions.md` ADR-1 ~ ADR-4) 신규 생성.
  - 백로그 작업 시스템(`docs/tasks.md`) 및 환경 구축 문서(`docs/dev-environment.md`) 작성.
- **결정 사항**:
  - Python FastAPI 백엔드 + Next.js 프론트엔드의 모노레포 구조(ADR-1) 채택.
  - Docker Container 제어를 위해 Python Docker SDK(ADR-2) 채택.
  - 상태 동기화를 위해 TanStack Query(ADR-3) 및 Polling 방식 사용.
- **다음 작업**:
  - 루트 `.gitignore`, `docker-compose.yml`, `README.md` 작성.
  - 백엔드 (`backend/`) Poetry 초기화 및 FastAPI 뼈대 코드 작성.
  - 프론트엔드 (`frontend/`) Next.js 뼈대 코드 및 실시간 상태 대시보드 UI 구현.
