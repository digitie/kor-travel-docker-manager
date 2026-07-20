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
| **T-011** | 설정 저장 안정화 및 validation 고도화 | `[/]` | - | Compose 재생성 경로 반영, diff/validation/rollback 남음 |
| **T-013** | 운영(prod) 공개 주소 `.env` 주입 및 CORS 환경변수화 | `[x]` | 2026-06-20 | 도메인 비노출, `KTDM_CORS_ALLOW_ORIGINS`, 프론트 환경파일 분리 |
| **T-014** | Docker host 네트워크 전환·컨테이너=호스트 포트·서비스 prod URL·pinvi-dagster·tripmate 정리 | `[x]` | 2026-06-20 | `KTDM_DOCKER_NETWORK_MODE=host`, 12802, `KTDM_PROD_URL_*`, `ktd_venv` |
| **T-015** | 프론트 Tailwind v4 + StyleSeed 전면 전환·전역 오류 복구 boundary | `[x]` | 2026-06-20 | geo PR #391 반영, `@theme` 토큰, `DESIGN-RULES.md` |
| **T-016** | 운영(prod) 배포 및 docker-manager 실행 검증 | `[x]` | 2026-06-20 | SSH 배포, venv --without-pip, 백엔드/프론트 기동·검증, 공개 라우팅은 인프라 |
| **T-017** | 운영 스택 db→conc 기동·geo 실데이터 복원·의존성 DAG 재설정 | `[x]` | 2026-06-20 | 이미지 save/load, geo 31GB 복원, `depends_on` DAG(concierge geo 비의존) |
| **T-018** | prod endpoint 문서 redaction | `[x]` | 2026-06-23 | `kor-travel-map` #508 동일 패턴 반영 |
| **T-019** | 관리자 로그인·세션·감사 로그·공개 API 키 관리 | `[x]` | 2026-06-23 | `kor-travel-geo` PR #399 패턴 반영 |
| **T-020** | PR #36 사후 리뷰 + fix-forward(보안 테스트 보강·감사 retention·CORS·프론트 a11y·utcnow 정리) | `[x]` | 2026-06-24 | 리뷰 코멘트, PR #37 머지, prod 배포·인증 검증 완료 |
| **T-021** | PR #36 후속 하드닝(신뢰 프록시 시크릿·brute-force durable·공개키 DB 직접조회·나머지 모달 a11y) | `[/]` | - | AUTH-3/AUTH-6/APIKEY-1/FE-4, `fix/pr36-followups-2` PR, prod 배포 후 머지 |
| **T-023** | concierge PR #127 참고 공개도메인 Secure 쿠키 보강(`_is_https`가 https 공개 origin 인식) | `[x]` | 2026-06-24 | 브라우저 E2E로 로그인 정상 확인(403 무), Secure 플래그 PR #40 머지·prod 검증 |
| **T-024** | 로그아웃/세션만료 시 LoginScreen 전환 회귀 수정(auth-me 401→authenticated:false) | `[x]` | 2026-06-24 | PR #37 FE-2 회귀, 브라우저 E2E로 발견·PR #41 머지 |
| **T-025** | 배포 런북(`deploy-runbook.local.md`) + push 전 보안 감사 절차 — concierge 스타일 정렬 | `[/]` | - | 민감 런북(gitignore)·AGENTS.md 절차·DO NOT #13/#14, 각 worktree 복사 |
| **T-029** | Concierge DB read 키를 Map Dagster에 단일 source로 주입 | `[x]` | 2026-07-13 | n150 단일 source 전환·cursor/수집기·권한·로그인 smoke 및 구 static 제거 완료 |
| **T-030** | Map OpiNet·KREX provider 키 compose 보간 drift 수정 | `[x]` | 2026-07-13 | 현재 env 이름·수집 서비스 전용 주입·API 제거 계약 테스트 고정 |
| **T-031** | Map↔PinVi C6c ops read/cancel principal 배포 결선 | `[/]` | - | API 전용 secret 격리, compatible image pair 배포·rollback·smoke |
| **T-033** | C7 Map UI·Dagster OCI revision 결선 | `[/]` | - | issue #60, Map runtime 네 image의 exact source provenance |
| **T-034** | C6c cAdvisor healthcheck 포트 계약 정렬 | `[/]` | - | issue #62, listen·`/healthz`가 같은 `CADVISOR_PORT` 사용 |
| **T-035** | C7 Map production API 인증 env 결선 | `[/]` | - | issue #63, Map #780/#782 fail-closed 설정과 C6c preflight 정렬 |
| **T-036** | C7 PinVi Dagster image 계약 정렬 | `[/]` | - | exact PinVi image의 `DAGSTER_HOME`·code location과 manager Compose override 정렬 |
| **T-037** | C6c Map UI 통합 경로 smoke 정렬 | `[/]` | - | 삭제된 `/ops/providers` 대신 `/ops/datasets` 인증 lifecycle 검증 |
| **T-039** | C6c PinVi login SSR shell 판정 정렬 | `[/]` | - | HTTP shell은 route chunk까지, hydrated form은 최종 Playwright에서 검증 |
| **T-038** | Map destructive production 명시 승인 결선 | `[/]` | - | standalone false와 분리해 Manager Map API에 exact true·attestation 고정 |
| **T-012** | 대시보드 상세 패널 확장 | `[ ]` | - | inspect, mounts, networks, redacted env를 UI에 연결 |
| **T-220** | `kor-travel-concierge` provider 상세 구현 및 과거 명칭 제거 | `[x]` | 2026-06-13 | 공식 프로젝트명 전환 완료 |
| **T-221** | `kor-travel-geo` DB명·환경변수·Docker 이름·Prometheus scrape 계약 동기화 | `[x]` | 2026-06-13 | `kor_travel_geo`, `KOR_TRAVEL_GEO_*`, `KTG_*`, `kor-travel-geo-*` 기준 반영 |
| **T-222** | 관측 target 개별 분리 및 포트 재배치 | `[x]` | 2026-06-13 | `gra`, `cadv`, `prom` 분리 및 새 포트 반영 |
| **T-223** | 앱 target 흐름 재정렬 및 실제 컨테이너 빌드 편입 | `[x]` | 2026-06-13 | `geo -> conc -> map -> pinvi`, `srv` 별칭 반영 |
| **T-224** | 과거 서비스명과 공용 인프라 명칭 정리 | `[x]` | 2026-06-15 | PinVi 및 `kor-travel-*` 기준 반영 |

---

## 진행 순서

1. `tasks.md`와 `tasks-done.md`를 최신 완료/미완료 상태로 정리한다.
2. `kor-travel-concierge`는 `conc`, PinVi는 `srv` 별칭을 기준으로 안내한다.
3. 다음 앱 target 추가 시 `config/docker-targets.yml`, `docker-compose.yml`, 포트 문서, API/CLI 테스트를 함께 갱신한다.
4. 병행 작업 충돌을 줄이기 위해 각 PR 전후로 `main` rebase를 수행한다.

---

## 태스크 세부 내역

### T-011: 설정 저장 안정화 및 validation 고도화

- [x] host 네트워크 기준에서 설정 저장·reset·미생성 start fallback이 Docker SDK 직접 생성 경로를 우회하지 않고 `docker compose up --force-recreate`를 사용하도록 변경
- [ ] compose 변경 전 diff 생성 및 UI 표시
- [ ] 포트, 볼륨, 네트워크 입력 validation 강화
- [ ] secret 성격 값은 `.env` override로 저장하도록 안내 및 방어 로직 추가
- [x] config 파일 변경과 재생성을 같은 host lock transaction으로 묶고, recreate/init 실패 시 원본 byte와
      파일 mode를 원자 복원한 뒤 기존 runtime 재생성을 시도하는 rollback 전략 문서화

### T-012: 대시보드 상세 패널 확장

- [ ] 컨테이너 row 선택 시 inspect 상세 drawer 또는 modal 표시
- [ ] mounts, networks, healthcheck, redacted env를 탭으로 분리
- [ ] target 단위 `ensure --build` 버튼을 개발 모드에서 제공
- [ ] 모바일/데스크톱에서 표와 상세 패널이 겹치지 않도록 반응형 검증

### T-029: Concierge DB read 키를 Map Dagster에 단일 source로 주입

- [x] 루트 `.env`의 `KOR_TRAVEL_MAP_KOR_TRAVEL_CONCIERGE_API_KEY`를 유일한 secret source로 정의
- [x] 실제 fetcher를 실행하는 Dagster·Dagster daemon에 동일한 base URL/key 환경변수 주입
- [x] 사용하지 않는 map API에는 read secret을 주입하지 않는 least privilege 계약 고정
- [x] `.env.example`, Docker 관리 문서, compose 계약 테스트 동기화
- [x] n150에 Concierge head 0017(scope migration 0016 포함) 배포·제약 및 실제 UI 로그인 검증
- [x] prod `.env` 주입·override의 key/base URL literal 각 세 줄 제거·compose 보간·Dagster 두 서비스 재생성
- [x] `.env`와 두 컨테이너 key를 값 비노출 constant-time equality로 확인
- [x] `limit=1` snapshot/changes 2페이지 cursor 검증, `page_size=200` 전체 8페이지/1,416건 순회, 실제 수집기 각 1,416건 및 내부/write 403 smoke
- [x] BFF/operator static admin overlap 회전·UI/BFF 검증 후 구 static 제거
- [x] 최종 old 401/new admin 200/read 공급 200·write 403/UI login 검증과 제한권한 백업 폐기

### T-030: Map OpiNet·KREX provider 키 compose 보간 drift 수정

- [x] OpiNet 공통 key가 과거 `KRTOUR_MAP_*` source 대신 현재 `KOR_TRAVEL_MAP_*` `.env` 값을 읽도록 수정
- [x] OpiNet map API live preview key는 별도 설정을 우선하고 미설정 시 공통 key를 재사용하도록 고정
- [x] EX·GO key가 과거 `KRTOUR_MAP_*` source 대신 현재 `KOR_TRAVEL_MAP_*` `.env` 값을 읽도록 수정
- [x] map API live preview key는 별도 설정을 우선하고 미설정 시 EX key를 재사용하도록 고정
- [x] OpiNet·KREX 공통 key는 Dagster·daemon에만, resolved preview key는 map API에만 주입하는
      최소 권한 계약과 `.env.example` placeholder를 테스트
- [x] 실제 secret 비노출 상태로 focused test·Ruff·Docker Compose 보간 검증

### T-031: Map↔PinVi C6c ops read/cancel principal 배포 결선

- [x] manager `.env`의 `KOR_TRAVEL_MAP_API_OPS_READ_TOKEN`과
      `KOR_TRAVEL_MAP_API_OPS_CANCEL_TOKEN`을 map API에만 주입하고 Dagster·daemon·UI에는
      주입하지 않는다.
- [x] 같은 두 값을 PinVi API의 `PINVI_KOR_TRAVEL_MAP_OPS_READ_TOKEN`과
      `PINVI_KOR_TRAVEL_MAP_OPS_CANCEL_TOKEN`으로만 전달하고 PinVi Web/Dagster에는
      주입하지 않는다.
- [x] 두 token은 각각 32자 이상·모든 공백 없음·상호 다름을 container 변경 전 검증하고, 실제 값은
      gitignore된 `.env`에만 둔다. manager/PinVi production mode와 Map
      `OPS_PRINCIPAL_REQUIRED=true`를 함께 강제한다.
- [x] production 배포 경로를 preflight/readiness → Map API → signed read·cancel·거부 smoke
      → Map UI·Dagster web·daemon → PinVi API → 전체 managed container readiness/secret inspect
      순서로 구현하고 Map runtime 네 service를 pair transaction에 포함한다.
- [x] rollback은 현재 contract generation의 canonical Map+PinVi immutable image ID pair 단위로만
      원자 기록·복원하며, legacy/과거 generation 조합을 정상 rollback 지점으로 오인하지 않는다.
- [x] base/override merged config의 `environment`·`env_file`·command·build args와 runtime inspect를
      API 두 곳만 허용하는 계약 테스트로 고정한다.
- [x] production 일반 `ensure`/container action·config·reset/direct Compose의 Map runtime/PinVi API mutation을
      중앙 차단하고, deployment-wide lock을 잡는 전용 `pinvi-pair deploy` capability만 허용한다.
- [x] manifest에 contract generation을 기록하고 merged compose의 host network·PinVi production
      mode·Map bind port·loopback base·container identity·다섯 immutable image override·manager-only smoke
      credential 격리를 mutation 전에 검증한다.
- [x] deploy/rollback 중 mixed pair를 노출하지 않고 Map/PinVi canonical smoke, owned fixture의 정확한
      409/502/503 typed cancel·`Retry-After`, 필수 서비스 running/healthy, Map UI auth lifecycle, runtime
      격리 뒤에만 manifest를 commit한다.
- [x] manifest가 없는 clean 환경은 host lock 안에서 base dependency→Map API→Map dependents→
      PinVi API→PinVi dependents를 단계 bootstrap하고 전체 smoke 성공 뒤 최초 v4를 기록한다.
      Map dependent provenance가 없는 v1/v2/v3는 자동 전환하지 않는다. 실패하면 Map runtime
      네 service와 PinVi API를 중지하고 transaction이 만든 container만 제거한다. 중간 실패는
      시작 시점 active runtime set 전체 복구 또는 다섯 service 명시적 halt로 끝낸다.
- [x] pass3 차단 리뷰의 init 예외 cleanup, project-wide `wait --down-project`, production 단일 state path,
      깊은 Map/PinVi DTO·owned cancel·manifest 검증, parent fsync 실패 복원, config/runtime 복원 진단 보존을
      코드·회귀 테스트·운영 문서에 반영한다(테스트 실행은 신규 적대적 리뷰 2명 승인 뒤 수행).
- [x] pass4 차단 리뷰의 canonical `execution_coverage`, production exact `12701` fail-close, Map dataset row와
      PinVi repository/asset/schedule/sensor 배열 원소의 실제 DTO 검증 및 `null` 음성 fixture를 반영한다
      (테스트 실행은 신규 적대적 리뷰 2명 승인 뒤 수행).
- [x] pass5 차단 리뷰의 cross-token capability typed status/code 음성 smoke, transaction당 destructive cancel
      정확히 1회 및 결과 재사용, actual cancellation attempt/member/Dagster run/root-only DTO 검증을 반영한다
      (테스트 실행은 신규 적대적 리뷰 2명 승인 뒤 수행).
- [x] pass6 DTO 정렬로 full 409 unresolved 0·resolved root/child topology·transient all-resolved를 허용하고,
      retryable exact run-backed failure와 in-progress CAS drift transition matrix를 실제 PinVi projection에 맞춘다
      (`409 PIPELINE_CANCELLATION_UNSAFE`와 `503 DAGSTER_TERMINATION_TIMEOUT` pair 포함).
      (테스트 실행은 신규 적대적 리뷰 2명 승인 뒤 수행).
- [x] pass7 actual DB lifecycle 정렬로 failed mixed evidence, retry subset lineage, frozen termination/engine time,
      `Retry-After` presence/parse 분리와 Compose kill signal/unknown option default-deny fixture를 반영한다
      (테스트 실행은 신규 적대적 리뷰 2명 승인 뒤 수행).
- [x] pass8에서 Compose `build --pull`·`run --rm`·`rm -s/--stop`의 command별 flag 의미와
      `config -o/--output` write-capable default-deny를 고정하고, cancel member/run policy·terminal mapping,
      feature-load child success 예외, contract generation 격리, bootstrap cleanup 예외 수렴을 반영한다
      (테스트 실행은 신규 적대적 리뷰 2명 승인 뒤 수행).
- [x] pass9에서 `Retry-After`를 ASCII decimal 1..300으로 제한하고, generic non-API config
      update/reset/create의 raw compose 전체·`env_file` 보호 이름/값 검증을 파일 쓰기·재생성 전에 수행한다.
      candidate 거부는 불변 상태와 typed 409 detail을 보존한다
      (테스트 실행은 신규 적대적 리뷰 2명 승인 뒤 수행).
- [x] pass10에서 raw/resolved compose 검사를 service 하위가 아닌 top-level `secrets`/`configs`/extension과
      service mount/reference를 포함한 전체 graph로 확장한다. API wiring은 suffix까지 정해진 canonical raw
      표현만 허용하고, `env_file`/외부 config 경로의 Compose 변수 문법은 완전 해석할 수 없으면 거부한다.
      generic ensure/up/create/recreate와 config prewrite는 두 단계 검증 뒤에만 mutation하며 typed 409 detail과
      mutation 0을 보존한다(테스트 실행은 신규 적대적 리뷰 2명 승인 뒤 수행).
- [x] pass11에서 service volume short/long bind source를 보간 후 canonical path로 해석해 root `.env`, manager
      state 파일, 보호 이름·현재 값이 든 외부 파일 mount를 raw/resolved 단계에서 거부한다. symlink·traversal·
      relative/absolute·Windows-looking·`:ro` 변형을 닫고 named volume은 경로로 오인하지 않는다. 내용 확인이
      불가능한 external secret/config alias reference도 exact allowlist 외에는 fail-close하며 rustfs REST config의
      typed 409와 파일/container mutation 0을 고정한다(테스트 실행은 신규 적대적 리뷰 2명 승인 뒤 수행).
- [x] pass12에서 manager 보호 파일의 ancestor directory와 host root bind를 거부하고, directory bind는 서비스별
      exact source/target allowlist로 한정한다. 존재하지 않거나 bounded regular-file 검사가 불가능한 source도
      fail-close해 Docker 자동 directory 생성과 TOCTOU 우회를 막는다. cAdvisor의 host root·Docker data directory
      mount는 제거하고 Docker socket+`/sys` 기반 container-only 모드로 전환하며 RustFS REST typed 409와
      source/compose/container mutation 0을 고정한다(같은 리뷰어 재승인 전 테스트 실행 금지).
- [x] pass13에서 config API의 전체 volume graph(top-level 정의와 모든 service reference)를 pre-request compose와
      exact immutable로 고정해 volume add/remove/source/target/type/mode 변경을 409로 거부한다. internal/default
      named volume만 허용하고 local bind driver option·unknown driver/option·external alias는 raw/resolved 모두
      fail-close한다. cAdvisor `/sys`·Docker socket은 short `:ro` 또는 long `read_only: true`만 허용하며,
      root-owned parent chain과 `/sys` mountpoint, root:docker `0660` socket의 inode/device/mode snapshot을 compose
      write와 Docker subprocess 직전에 재검증한다. mismatch는 write 전 중단 또는 compose byte 복원으로 durable
      mutation 0을 보존한다(같은 리뷰어 재승인 전 테스트 실행 금지).
- [x] pass14에서 mutex 안의 persisted compose와 request candidate의 raw/resolved volume graph를 각각 exact
      비교하고 `include`·service `extends`·`COMPOSE_FILE`·추가 override를 거부하는 single-file mutation 경계를
      고정한다. cAdvisor mount는 raw literal/resolved identity 모두 RO `/sys`와 Docker socket 두 개만 허용하고,
      raw named-volume `name`/`external` 및 resolved project-derived name drift를 차단한다. 첫 mutation subprocess
      성공 뒤 후속 preflight drift가 나면 원본 compose byte/mode와 persisted runtime을 best-effort 복구하고
      원래 계약 오류·복구 결과를 보존한 typed 500으로 승격한다(같은 리뷰어 재승인 전 테스트 실행 금지).
- [x] pass15에서 mutation Docker command의 override 탐색을 제거하고 subprocess 직전 single-file 경계를
      재검증한다. `ensure`는 최초 compose byte/mode와 raw/resolved/snapshot baseline을 복원·재검증한 뒤에만
      runtime recovery를 실행하며, 검증 실패 시 Docker recovery를 금지한다. preflight drift의 원본 복원도
      원자 복원 실패를 원래 오류와 함께 typed 500 durable mutation으로 보존한다(재승인 전 테스트 실행 금지).
- [x] pass16에서 transaction 시작 시 `.env` 존재 여부·byte·device/inode/mode/uid/gid와 effective Compose
      environment를 비밀값 비노출 snapshot으로 고정한다. raw/resolved 검증과 Docker mutation은 같은 snapshot을
      사용하고, subprocess 직전 `.env` 생성·삭제·내용·identity drift를 재검증한다. mutation은
      `--env-file /dev/null`과 frozen process env만 사용하며 `ensure`/config recovery도 최초 snapshot을 재사용한다
      (재승인 전 테스트 실행 금지).
- [x] pass17에서 production mutation mutex를 checkout/project와 무관한 단일 전역 lock으로 고정하고,
      lock 안에서 manifest 경로와 root `.env`·canonical compose·외부 `env_file` 입력을 한 번만 snapshot한다.
      외부 입력은 exact 4-key graph와 byte/identity를 매 경계에서 재검증하고 Docker resolution에만 익명 fd로
      전달하며, mutation은 original project directory에서 완전 해석된 compose를 stdin으로 소비한다.
      deploy/capture/rollback은 최초 mutation 뒤 모든 계약 오류를 같은 root snapshot의 recovery 또는 다섯 runtime
      halt로 수렴시키고 원래 오류와 복구 결과를 typed post-mutation 오류로 보존한다
      (지시에 따라 테스트·lint·build는 실행하지 않고 정적 diff 검사만 수행).
- [x] pass18에서 recovery/halt를 frozen resolved transaction 전용 실행으로 분리하고, config update/reset의
      persisted baseline과 exact candidate transaction을 분리해 forward는 candidate, restore는 baseline만 쓴다.
- [x] pass19에서 manifest active image override를 root frozen 입력으로 미리 해석한 별도 recovery transaction을
      deploy/rollback 복구에 사용하고, forward transaction과 identity를 분리한다. manifest가 없는
      v3 bootstrap capture는 이전 active pair 복구 대신 생성한 서비스 정리·API halt로 수렴한다.
- [x] Map UI username·PBKDF2 hash·session secret을 기본값 없는 canonical raw 보간과 exact resolved/runtime
      Env 경로로 고정하고, manager-only 평문 smoke 비밀번호 비주입 및 frozen snapshot/rollback 인증값
      격리 계약과 회귀 테스트·운영 문서를 추가한다. Docker Compose resolved literal escape와 runtime raw-exact
      분리를 포함한 ext4 C6c targeted `541 passed`, backend 전체 `599 passed`로 검증했다.
- [x] 공식 차단 리뷰를 반영해 canonical test baseline, raw/resolved Map UI 필수 서비스, 모든 Unicode
      whitespace 거부, credential redaction을 고정한다. deploy/rollback은 readiness 뒤 current Map UI의 exact
      runtime 인증과 login/protected/logout/reblock을 첫 API stop 전에 검사하며 실패 시 mutation 0이다.
      strict mypy, 신규 lint `0`, production Docker Compose config/resolved guard를 통과했다.
- [x] n150 read-only preflight에서 일반 scalar의 username 문자열 일치를 secret leak으로 오인한 false-positive를
      mutation 없이 확인했다. username identity의 exact wiring/runtime equality와 confidential 값의 전역 scalar
      격리를 분리하는 회귀 계약을 추가했다. 공식 리뷰 승인 뒤 ext4 C6c targeted `528 passed`, backend 전체
      `616 passed`, strict mypy와 신규 lint `0`, production Docker Compose `config --quiet` 및 resolved guard
      `2/2`를 통과했다.
- [x] Map clean-cut entrypoint에서 제거된 provider credential env 9개를 API compose에서 삭제하고, 해당
      이름·legacy data.go.kr credential·제거된 live-preview flag를 raw candidate·resolved candidate·최종
      C6c contract가 이름의 존재 자체로 fail-close하도록 회귀 계약을 추가했다. Map API의 `command`·
      `entrypoint` override와 runtime `Cmd`/`Entrypoint` drift도 금지해 immutable image migration과
      entrypoint guard 우회를 차단했다.
- [ ] n150 production에서 root 권한으로 Map UI 비밀번호를 회전하고 cross-repo smoke와 실제 UI 로그인 검증을
      통과한 뒤 완료 이력으로 옮긴다.

### T-033: C7 Map UI·Dagster OCI revision 결선

- [x] `kor-travel-map-api`, `kor-travel-map-ui`, `kor-travel-map-dagster`,
      `kor-travel-map-dagster-daemon`의 build가 모두 동일한 canonical
      `KOR_TRAVEL_MAP_GIT_COMMIT`을 Dockerfile에 전달하도록 compose를 정렬한다.
- [x] raw·resolved compose 계약이 네 service의 build arg·snapshot context·Dockerfile을
      exact 검증하고 일부 service 또는 revision이 다른 회귀를 첫 mutation 전에 차단한다.
- [x] candidate build가 Map runtime 네 image와 PinVi image를 모두 같은 frozen snapshot에서
      완성하고, 각 immutable image ID와 OCI revision을 manifest v4에 기록한다.
- [x] capture/deploy/rollback이 Map runtime 네 service를 같은 frozen transaction으로
      재생성·검증하며, 복원 실패 시 다섯 runtime을 모두 중지해 혼합 generation을 차단한다.
- [x] resolved fixture drift, candidate build service 누락, dependent image/revision mismatch,
      activation·rollback 누락에 대한 회귀 계약을 추가한다.
- [x] canonical v4 경로가 저장소 역사에 존재한 sibling `compatible-pair-v2.json`과
      `compatible-pair-v3.json`을 payload·file type과 무관하게 mutation 전에 fail-close하고,
      legacy bytes 불변과 Docker 미호출을 실행형 회귀로 고정한다.
- [ ] n150에서 clean exact commit으로 네 image를 빌드해 각
      `org.opencontainers.image.revision` label이 같은 40자 commit인지 확인한다.
- [ ] C7 runtime attestation과 live E2E가 실제 기동된 네 Map image provenance를 통과하면
      issue #60을 닫고 완료 이력으로 옮긴다.

### T-034: C6c cAdvisor healthcheck 포트 계약 정렬

- [x] canonical compose의 cAdvisor listen 포트와 명시적 `/healthz` healthcheck가
      모두 `CADVISOR_PORT`(기본 `12301`)를 단일 정본으로 사용하게 한다.
- [x] raw compose 계약이 exact `--port=${CADVISOR_PORT:-12301}`과 health URL을 고정하고,
      default/custom resolved config에서 listen·probe 포트가 같은지 검증한다.
- [ ] n150 production에서 cAdvisor `healthy`와 설정 포트 `/healthz` 200을 확인한 뒤
      중단된 C6c compatible-pair capture를 단 한 번 재시도한다.
- [ ] capture와 후속 readiness가 통과하면 issue #62를 닫고 완료 이력으로 옮긴다.

### T-035: C7 Map production API 인증 env 결선

- [x] ADR-23에서 admin BFF, API-only service/cursor, public/debug/profile, metrics 비활성 계약과
      service별 최소 주입 범위를 문서로 먼저 고정한다.
- [x] canonical Compose와 `.env.example`에 Map #780/#782 production 설정을 정확히 반영했다.
- [x] C6c raw/resolved/runtime preflight가 credential shape·상호 구분·허용 service exact set과
      production literal을 mutation 전에 검증하게 한다.
- [x] 누락·약한 값·재사용·다른 service 유출·설정 drift 음성 fixture를 추가했다.
- [x] 두 번째 적대적 리뷰 P1에 따라 manifest v4 exact 9-field shape 밖의 sibling 단조 marker를
      추가했다. 최초 v3/v3 logical manifest hash만 pending 재시도를 허용하고 성공 검증 뒤 complete로
      영구 닫아 A3→B4→rollback A3→C3 회전도 누락 예외를 다시 열지 못한다.
- [x] marker atomic write/fsync와 fixed shape, 0600 regular owner, corrupt/symlink/mode/owner 및 pending
      baseline drift fail-close 회귀 계약을 추가했다.
- [x] 두 번째 적대적 리뷰 P2에 따라 source Compose 전체 scalar tree에서 admin=API+frontend,
      service=API-only, cursor=v3 0회/v4 API exact 1회 외 모든 service/field leak를 거부했다.
- [x] 세 번째 적대적 리뷰 P2에 따라 profile/public/debug도 API-only exact path로 올리고,
      API·Dagster·daemon `env_file`의 known path/options와 tracked exact-revision 내용까지 검증한다.
- [x] 네 번째 적대적 리뷰 P2에 따라 tracked `env_file`을 exact `100644 blob`·64 KiB 이하·UTF-8로
      제한하고, 허용되지 않은 service의 `env_file: null` 우회도 차단했다.
- [x] `.env.example`의 세 공개 local placeholder를 production config/raw/resolved에서 각각 거부하고
      local 허용 회귀 계약을 추가했다.
- [x] 동일 적대적 리뷰어의 최종 P0~P2 없음 판정 뒤 backend 886개, 변경 파일 Ruff,
      strict mypy, 기본·커스텀 Compose gate를 통과했다.
- [x] PR을 merge한다.
- [ ] n150 final v4 exact-pair에서 Map API startup/readiness와 runtime secret isolation을 확인한 뒤
      issue #63을 닫고 완료 이력으로 옮긴다.

### T-036: C7 PinVi Dagster image 계약 정렬

- [x] C7 exact PinVi source revision의 `apps/etl/Dockerfile`과 package metadata에서
      `DAGSTER_HOME=/opt/pinvi/.dagster`, code location `pinvi.etl.definitions` 계약을 확인한다.
- [x] canonical `pinvi-dagster` Compose가 image 계약을 과거 `tripmate` 경로로 덮어쓰지 않도록
      environment와 command를 정렬한다.
- [x] resolved Compose 회귀 테스트로 `DAGSTER_HOME`과 code location을 고정한다.
- [x] 적대적 리뷰 승인 뒤 focused/backend/Compose gate를 통과하고 PR #66을 병합한다.
- [ ] C7 n150 compatible-pair capture에서 PinVi dependent bootstrap을 완료한다.

### T-037: C6c Map UI 통합 경로 smoke 정렬

- [x] 최종 Map UI에서 `/ops/providers`가 clean-cut되고 `/ops/datasets`로 통합된 경로 계약을 확인한다.
- [x] login `next`, 로그인 후 보호 GET, logout 후 재차단 GET을 단일 `/ops/datasets` 정본으로 묶는다.
- [x] auth lifecycle 단위 테스트와 Docker 관리 문서를 같은 경로로 정렬한다.
- [x] 단일 적대적 리뷰 P0~P2 없음 판정과 backend 888개, focused 800개, Ruff, strict mypy gate를 통과한다.
- [ ] PR을 병합한다.
- [ ] n150 compatible-pair capture에서 실제 보호 페이지 200과 logout 후 재차단을 확인한다.

### T-039: C6c PinVi login SSR shell 판정 정렬

- [x] n150 read-only 응답이 200·`text/html`·비어 있지 않은 body·`/_next/static/`과
      `/admin/login/page-*.js` route chunk를 포함하지만 `admin-login-form`은 포함하지 않는 원인을
      PinVi의 `Suspense fallback={null}` client login page와 대조한다.
- [x] HTTP shell smoke와 browser smoke의 책임을 문서로 먼저 분리한다. shell은 status/content/body,
      일반 Next.js static marker와 `admin/login` 전용 page chunk를 확인하고, hydration 후 form·로그인
      상호작용은 최종 n150 Playwright가 담당한다.
- [x] `run_ui_auth_smoke`에서 raw SSR `admin-login-form` 요구를 제거하고 route-specific page chunk를
      exact 판정한다. 일반 Next.js fallback HTML이나 다른 route chunk만 있는 응답은 계속 fail-close한다.
- [x] positive SSR shell과 form 포함 shell, route chunk가 없는 generic fallback, 다른 route chunk,
      status/content-type/empty-body 오류를 focused 단위 테스트로 고정한다.
- [x] 같은 단일 적대적 reviewer의 P0~P2 없음 승인 뒤에만 focused/full test와 Ruff/mypy를 실행한다.
- [ ] 최신 main rebase·CI green 뒤 n150 compatible-pair capture와 최종 Playwright login form을 확인한다.

### T-038: Map destructive production 명시 승인 결선

- [x] Manager canonical `kor-travel-map-api`에
      `KOR_TRAVEL_MAP_API_DESTRUCTIVE_ENABLED=true`를 literal로 명시한다.
- [x] raw·resolved candidate, activation 뒤 runtime이 exact `true`이고 다른 service·channel에는 이름이
      없는지 C6c protected environment 계약으로 고정한다.
- [x] standalone Map compose의 기본 `false`와 Manager의 명시적 production 승인을 교차 계약 테스트로
      구분한다. image 기본값이나 host env fallback은 승인 근거가 아니다.
- [x] compatible-pair manifest v4 및 C7 attestation의 Map API environment hash가 이 enablement를
      포함하고, 실제 destructive backup 작업은 인증 principal actor를 감사한다는 운영 증거를 문서화한다.
- [ ] Map issue #796의 actor/OpenAPI 변경과 함께 단일 적대 리뷰·CI·n150 final live를 통과한다.

### T-040: C7 Map features routes production 명시 결선

- [x] issue #70과 ADR-25에 Map feature 관리 REST가 production에서 명시적으로 활성화되어야 하는
      이유와 API-only fail-closed 경계를 기록한다.
- [x] Manager canonical `kor-travel-map-api`에
      `KOR_TRAVEL_MAP_API_FEATURES_ROUTES_ENABLED=true`를 literal로 명시한다.
- [x] raw source, Docker-resolved candidate, activation 뒤 runtime이 exact `true`이고 다른
      service·`env_file`·build arg·command·label·config·secret에는 이름이 없는지 C6c 보호 환경
      계약과 음성 회귀 테스트로 고정한다.
- [x] focused 42개, C6c·Docker config 849개, backend 907개, Ruff baseline 제외, strict mypy,
      canonical Compose config gate를 통과한다.
- [ ] 단일 적대적 리뷰와 CI를 통과한다.
- [ ] n150 compatible-pair recapture와 C7 live E2E에서 feature 관리 REST를 확인한 뒤 issue #70을 닫는다.

### T-019: 관리자 로그인·세션·감사 로그·공개 API 키 관리

- [x] 단일 관리자 계정(`admin`) 로그인 화면을 추가하고 실제 비밀번호는 gitignore된 `.env`의 `KTDM_ADMIN_PASSWORD_HASH`에 PBKDF2 해시로만 저장
- [x] 관리자 세션을 HMAC 서명 `httpOnly` 쿠키와 DB 저장 세션 해시로 검증하고, 지정된 프론트엔드 Origin만 관리자 API를 호출하도록 제한
- [x] 로그인 성공·실패·로그아웃·API 키 생성/폐기 이벤트를 `login_audit_events`에 기록하고 관리자 UI에서 조회
- [x] VWorld 호환 32자리 공개 API 키를 UI 버튼으로 생성하고, 원문은 1회만 표시하며 DB에는 해시와 힌트만 저장
- [x] 공개 API 키 활성 해시는 짧은 TTL 메모리 캐시로 읽고 생성·폐기 시 즉시 무효화
- [x] 신뢰된 로그인 세션 요청은 공개 API 키 검증을 생략할 수 있도록 공통 dependency 제공
- [x] `kor-travel-geo` PR #399의 v2 공개 API 키·관리자 인증 env 계약을 compose와 `.env.example`에 반영
- [x] PR #399 사후 리뷰를 재확인해 미검증 `X-Forwarded-*` 신뢰 차단, 401 처리, 로그인 접근성, clipboard fallback을 보강

### T-013: 운영(prod) 공개 주소 `.env` 주입 및 CORS 환경변수화

- [x] 백엔드 CORS 허용 Origin을 `KTDM_CORS_ALLOW_ORIGINS`(콤마 구분, 기본 `*`)로 제어하고 기동 시 루트 `.env`를 로드한다
- [x] 프론트엔드 백엔드 주소를 `.env.development`/`.env.production`로 분리하고 `.env.local` 섀도잉을 제거한다
- [x] 실제 운영 도메인은 gitignore된 `.env`/`frontend/.env.production`에만 두고 `.env.example`은 플레이스홀더로 문서화한다
- [x] 백엔드 ruff·CORS 파싱, 프론트 type-check·prod 빌드(인라인) 검증

### T-014: Docker host 네트워크 전환·컨테이너=호스트 포트·서비스 prod URL·pinvi-dagster·tripmate 정리

- [x] dev 기본 네트워크를 `network_mode: ${KTDM_DOCKER_NETWORK_MODE:-host}`로 전환하고 인프라/앱이 호스트 정규 포트에 직접 바인딩하도록 맞춘다
- [x] 서비스 간 참조(DSN/RustFS/내부 API/Dagster)를 `127.0.0.1:<포트>`로, Prometheus scrape·rustfs-init 엔드포인트도 동기화한다
- [x] geo/concierge/map/pinvi 컨테이너 내부 포트를 호스트 포트와 동일하게 통일한다
- [x] `pinvi-dagster`(12802)를 compose/registry/`pinvi` target에 추가하고 PinVi `apps/etl/Dockerfile`을 신규 작성한다
- [x] 관리 16개 서비스의 prod 공개 URL을 `KTDM_PROD_URL_*`(.env, 비노출)·`prod_url_env`로 주입해 대시보드 `public_url`로 표시한다
- [x] tripmate 로컬 잔재 정리(`pinvi_metrics.db` 개명, `ktd_venv` 재생성)
- [x] `docker compose config`·백엔드 ruff·프론트 type-check/build 검증 및 문서 동기화

### T-015: 프론트 Tailwind v4 + StyleSeed 전면 전환·전역 오류 복구 boundary

- [x] `kor-travel-geo` PR #391의 오류 복구 boundary(error/global-error/AppErrorPanel/error-recovery)를 매니저에 반영
- [x] Tailwind v4 전환(`@import`+`@theme`, `@tailwindcss/postcss`, autoprefixer/tailwind.config 제거)
- [x] `kor-travel-geo-ui/docs/DESIGN-RULES.md`의 StyleSeed 라이트 토큰을 `@theme`에 정의
- [x] `DashboardClient`·`AppErrorPanel`을 Pure Black → StyleSeed 토큰으로 전면 리스타일
- [x] `docs/DESIGN-RULES.md` 포팅, `DESIGN.md` superseded 안내, ADR-17, 프론트 type-check/build 검증

### T-220: `kor-travel-concierge` provider 상세 구현 및 과거 명칭 제거

- [x] `config/docker-targets.yml`의 `ai` target을 `kor-travel-concierge` 기준으로 정리
- [x] 과거 AI provider 명칭 기반 별칭을 제거하고 새 공식 별칭만 남긴다
- [x] 통합 DB 기본값을 `kor_travel_concierge` database 기준으로 정리하고 과거 env fallback을 제거한다
- [x] `pinvi` target이 `kor-travel-concierge`에 직접 의존하지 않도록 문서와 target 설명을 정리
- [x] `krtour-map`과 `kor-travel-concierge` 간 provider 관계만 남도록 아키텍처/포트/관리 문서를 동기화
- [x] 관련 테스트와 설정 검증을 갱신한다

### T-221: `kor-travel-geo` DB명·환경변수·Docker 이름·Prometheus scrape 계약 동기화

- [x] `kor-travel-geo` DB 기본값을 `kor_travel_geo`로 맞춘다
- [x] manager override 변수는 `KOR_TRAVEL_GEO_*`, API/UI 컨테이너 내부 변수는 `KTG_*`로 맞춘다
- [x] Docker service/container 이름을 `kor-travel-geo-*`로 맞춘다
- [x] 물리 데이터 디렉터리를 `/home/digitie/kor-travel-geo-data` 기준으로 맞춘다
- [x] RustFS bucket 기본값을 `kor-travel-geo`로 맞춘다
- [x] Prometheus scrape target에 `kor-travel-geo-api:12501/metrics`와 `kor-travel-geo-ui:12505/api/metrics`를 추가한다
- [x] 관련 문서와 테스트 fixture를 갱신한다

### T-222: 관측 target 개별 분리 및 포트 재배치

- [x] 단일 관측 target을 제거하고 `gra`, `cadv`, `prom` target으로 분리한다
- [x] 관측 target 분리 당시 dependency 순서를 `db -> storage -> gra -> cadv -> prom -> geo -> map -> ai -> main`으로 맞춘다
- [x] Grafana 공용 연계를 위해 `gra`를 RustFS 다음 target으로 배치한다
- [x] Grafana `12205`, cAdvisor `12301`, Prometheus `12401`, `kor-travel-geo` API/Web UI `12501`/`12505` 포트를 반영한다
- [x] CLI/API 테스트와 문서를 새 target/포트 기준으로 갱신한다

### T-223: 앱 target 흐름 재정렬 및 실제 컨테이너 빌드 편입

- [x] dependency 순서를 `db -> storage -> gra -> cadv -> prom -> geo -> conc -> map -> pinvi`로 조정한다
- [x] `kor-travel-concierge` target을 `conc`로 등록하고 API/MCP/Scheduler/Web UI compose service를 추가한다
- [x] `kor-travel-map` target을 `map`에 실제 API/Dagster/Web UI compose service로 연결한다
- [x] PinVi target을 `pinvi`로 등록하고 `srv`, `main` 별칭을 제공한다
- [x] 공용 DB/RustFS 복구 스크립트에 `krtour_map_dagster` database와 `kor-travel-concierge` bucket 보정을 추가한다
- [x] API/CLI 테스트와 문서를 새 target 흐름에 맞춰 갱신한다

### T-224: 과거 서비스명과 공용 인프라 명칭 정리

- [x] PinVi 전용 database, role, bucket, 환경변수 기본값을 `pinvi` 및 `PINVI_*` 기준으로 맞춘다
- [x] 공용 RustFS와 관측 컨테이너 이름을 `kor-travel-*` 기준으로 맞춘다
- [x] 문서, 테스트, 설정 파일의 과거 서비스명 잔여 표기를 제거한다
