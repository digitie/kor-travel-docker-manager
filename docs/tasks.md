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
| **T-011** | 설정 저장 안정화 및 validation 고도화 | `[x]` | 2026-07-28 | diff 미리보기, 포트/네트워크/env 검증(baseline 인지 secret 방어) + 적대적 리뷰 2라운드(URL/비-URL 위조 변수명 우회, React key 포커스 유실) 수정, 실브라우저 검증 완료 |
| **T-031** | Map↔PinVi C6c ops read/cancel principal 배포 결선 | `[/]` | - | API 전용 secret 격리, compatible image pair 배포·rollback·smoke |
| **T-033** | C7 Map UI·Dagster OCI revision 결선 | `[/]` | - | issue #60, Map runtime 네 image의 exact source provenance |
| **T-034** | C6c cAdvisor healthcheck 포트 계약 정렬 | `[/]` | - | issue #62, listen·`/healthz`가 같은 `CADVISOR_PORT` 사용 |
| **T-035** | C7 Map production API 인증 env 결선 | `[/]` | - | issue #63, Map #780/#782 fail-closed 설정과 C6c preflight 정렬 |
| **T-036** | C7 PinVi Dagster image 계약 정렬 | `[/]` | - | exact PinVi image의 `DAGSTER_HOME`·code location과 manager Compose override 정렬 |
| **T-037** | C6c Map UI 통합 경로 smoke 정렬 | `[/]` | - | 삭제된 `/ops/providers` 대신 `/ops/datasets` 인증 lifecycle 검증 |
| **T-039** | C6c PinVi login SSR shell 판정 정렬 | `[/]` | - | HTTP shell은 route chunk까지, hydrated form은 최종 Playwright에서 검증 |
| **T-038** | Map destructive production 명시 승인 결선 | `[/]` | - | standalone false와 분리해 Manager Map API에 exact true·attestation 고정 |
| **T-041** | C6c rollback image retention 보장 | `[/]` | - | issue #72, candidate build 전 직전 active 5-image 세대 보존 |
| **T-043** | WS 인가 동시성 상한 + 프론트 배포 preflight | `[/]` | - | T-042 리뷰 후속, PR #76 |
| **T-040** | C7 Map features routes production 명시 결선 | `[/]` | - | issue #70, 요약 표 누락분 보강 |
| **T-012** | 대시보드 상세 패널 확장 | `[/]` | - | inspect 모달·5개 탭·dev ensure 버튼, 비밀 redaction 보강, 실브라우저 검증 |
| **T-044** | ensure 라우트의 production 서버측 차단 | `[x]` | 2026-07-28 | `ComposeService.ensure_target`이 target과 무관하게 production을 전면 차단, 적대적 리뷰 2명 + 검증 통과 |

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
- [x] compose 변경 전 diff 생성 및 UI 표시. 백엔드 호출 없이 프론트에서 baseline(`configTargetContainer.config`) vs
      현재 입력을 실시간 비교해 포트/네트워크 추가·삭제, env 변경 전후 값을 모달에 즉시 표시한다.
- [x] 포트, 볼륨, 네트워크 입력 validation 강화. 백엔드에 `docker_service.validate_container_config_update`를
      추가해 lock 획득·Docker 접근보다 먼저 검증하고(`ContainerConfigValidationError` → HTTP 422),
      프론트에도 같은 규칙을 미러링해 왕복 없이 즉시 피드백한다. 포트는 `${VAR:-default}` 보간 토큰을
      opaque하게 신뢰하고 리터럴 숫자만 1~65535 범위·host:container 형식을 검사한다(docker-compose.yml
      실제 ports 18개 전수 통과를 회귀 테스트로 고정). 볼륨은 이미 `compose_volume_graph_hash` 비교로
      완전히 불변 처리되어 있어(어떤 변경도 첫 mutation 전에 409) 새 검증을 추가하지 않고, 대신 프론트에서
      변경을 미리 감지해 제출 전에 경고하고 제출을 막는다.
- [x] secret 성격 값은 `.env` override로 저장하도록 안내 및 방어 로직 추가. 정적 key-이름 휴리스틱만 쓰면
      `KOR_TRAVEL_MAP_API_PUBLIC_API_KEY_REQUIRED=true`처럼 이름에 "API_KEY"가 들어 있지만 원래부터 리터럴
      불리언인 값까지 오탐으로 막는다(실제 compose 파일 전수 검증에서 발견). 그래서 "baseline이 이미
      `${...}` 보간이었는지"를 기준으로 삼는다 — 이미 `.env`로 분리돼 있던 참조를 리터럴로 되돌리는 것만
      막고, 원래부터 리터럴이던 값은 자유롭다(baseline을 모르는 신규 key에는 안전한 쪽으로 key-이름
      휴리스틱을 fallback으로 쓴다). key 이름과 무관하게 값 안에 literal 접속 자격증명
      (`scheme://user:pass@`)이 `${...}` 밖에 남아 있으면 별도로 거부한다 — `${OUTER:-postgresql://user:dev_pw@host/db}`처럼
      비밀번호까지 하나의 `${...:-default}` 안에 두는 이 저장소의 기존 관행은 통과시킨다.
- [x] config 파일 변경과 재생성을 같은 host lock transaction으로 묶고, recreate/init 실패 시 원본 byte와
      파일 mode를 원자 복원한 뒤 기존 runtime 재생성을 시도하는 rollback 전략 문서화
- [x] **적대적 리뷰 2명이 찾은 문제를 수정한다(1차 라운드).** (1) 리뷰어 1: `_value_has_literal_url_credential`가
      `${...}` 블록을 통째로 지우고 스캔해서, `${FAKE_NAME:-literal-secret}`처럼 지어낸 변수명으로
      감싸기만 해도 credential 스캔이 통째로 우회됐다 — raw 값을 그대로 스캔하고 password 캡처
      그룹 자체가 보간인 경우만 예외로 인정하도록, 그리고 credential 스캔을 baseline과 완전히
      같은 값에만 예외로 두도록 재작성했다. (2) 리뷰어 2: ports/volumes/networks 행의 React
      `key`에 필드 값 자체(`port-${idx}-${port}`)가 들어 있어 매 keystroke마다 DOM 노드가
      재마운트되어 포커스가 사라졌다 — index 전용 key로 교체(행은 추가/삭제만 되고 재정렬은
      없어 안전). 그 외 whitespace로 인한 오탐 메시지, `aria-describedby`/`aria-live` 접근성
      공백도 함께 수정. 두 버그 모두 리뷰어가 제시한 실제 입력으로 직접 재현 후 수정 확인,
      추가로 수정을 되돌리는 mutation test로 새 회귀 테스트 4건만 실패하는 것을 확인했다.
- [x] **1차 라운드 수정 직후 재검토에서 발견한 2차 보안 공백을 수정한다.** 1차 수정은
      `scheme://user:pass@` 형태(DSN)에만 반응하는 credential 스캔이라, URL이 아닌 단일 리터럴
      비밀(예: `GF_SECURITY_ADMIN_PASSWORD=${GRAFANA_ADMIN_PASSWORD:-admin}`)에는 적용되지
      않는다는 것을 재발견했다 — baseline이 이미 `${...}` 보간이어도, sensitive key의 값을
      `${TOTALLY_MADE_UP_NAME:-h4x0r-literal-secret}`처럼 지어낸 이름 + 새 리터럴 default로
      바꾸면 "여전히 보간 형태"라는 이유만으로 규칙 1(재보간 요구)을 통과했다(실제
      `docker-compose.yml`의 Grafana 서비스로 직접 재현). 규칙 1을 일반화해, sensitive key면
      `${...}` 형태를 유지해도 `:-default` 리터럴 자체가 baseline과 달라졌으면 거부하도록 했다
      (변수 이름만 바뀌고 default 리터럴이 동일하면 허용, default를 아예 없애는 것도 허용,
      비-sensitive key는 대상이 아님 — 포트 기본값 변경 등 정상 편집을 막지 않는다). 백엔드
      회귀 테스트 5건 추가 + mutation test로 새 테스트 2건만 실패 확인, 전체 1047 backend
      테스트 통과, 프론트 `configValidation.ts`에 동일 로직 미러링 후 실브라우저(WSL dev
      backend/frontend, 로그인 세션)에서 Grafana 컨테이너의 `GF_SECURITY_ADMIN_PASSWORD`
      필드에 직접 재현 입력을 타이핑해 차단 메시지·diff 미리보기·제출 버튼 비활성화를
      확인했다(실제 컨테이너 재생성은 트리거하지 않음). 같은 세션에서 포트 필드에 여러
      글자를 연속 입력해 React key 수정으로 포커스가 유지되는 것도 함께 재확인했다.

### T-012: 대시보드 상세 패널 확장

- [x] 컨테이너 row의 상세 버튼으로 inspect modal을 연다(`ContainerDetailModal`).
      기존 모달과 같은 `role="dialog"`/`aria-modal`/`aria-label` 패턴에 Esc 닫기와
      포커스 이동을 더했다.
- [x] mounts, networks, healthcheck, redacted env를 탭으로 분리한다
      (`role="tablist"`/`tab`/`tabpanel`과 `aria-controls`/`aria-labelledby` 연결).
- [x] target 단위 `ensure --build` 버튼을 개발 빌드에서만 제공한다. `containers`는 사실
      depends_on까지 펼쳐진 목록이라(통합 PostgreSQL은 db·geo·conc·map·pinvi·all 여섯
      target에 모두 있다) 첫 매치를 쓰면 `dependency_order` 순서에 의존하게 된다.
      순서와 무관하게 **가장 좁은** target을 고른다. 실행 전에는 실제 재생성 대상
      서비스 목록과 개수를 보여 주고 확인을 받는다(db 포함 시 스키마 스크립트 경고).
- [x] 모바일/데스크톱 반응형을 실브라우저로 검증한다. 390×844에서 page 가로 스크롤 0,
      모달이 viewport 안에 들어오고, 넓은 Mounts 표와 탭 목록은 각자의 컨테이너 안에서만
      가로 스크롤한다. 1440×900도 동일.
- [x] 로컬 실브라우저 검증: 18개 row 전부 상세 버튼 노출, 5개 탭이 실제 inspect 데이터를
      렌더(마운트 rw/ro, 네트워크 2개 IP/MAC/alias, healthcheck `healthy`+최근 검사 로그).
      콘솔 오류 0건(로그인 전 401과 기존 favicon 404 제외).
- [x] **적대적 리뷰 2명이 찾은 비밀 노출을 수정한다.** 이 패널이 inspect를 UI에 연결한
      최초 지점이라, 그동안 API/CLI에만 있던 redaction 공백이 브라우저 한 번의 클릭으로
      열렸다. key 이름 누락(`*_API_KEY`가 `ACCESS_KEY`에 안 걸림)과 값 내부 credential
      (`postgresql://user:password@`)을 모두 막고, `cmd`/`entrypoint`도 필터에 넣었다.
      실행 중 10개 컨테이너 전수 검사에서 유출 0건, `KTG_PG_DSN`은
      `postgresql+psycopg://addr:<redacted>@...`로 비밀번호만 가려지는 것을 확인했다.
- [x] 리뷰 지적 반영: WS broadcast(2초)마다 포커스를 빼앗던 effect 의존성 수정(7초간
      포커스 유지 확인), `aria-controls` dangling IDREF 4건 해소, tabpanel `tabIndex=0`,
      탭 좌우/Home/End 키 이동, 미생성·오프라인 컨테이너 버튼 비활성화, raw JSON 오류 문구
      교체, running 중 `종료 코드 0` 오표시 제거.

### T-044: ensure 라우트의 production 서버측 차단

T-012 적대적 리뷰에서 확인한 공백이다. `POST /targets/{target}/ensure`는 production에서도
비-C6c target을 막지 않는다. `assert_manager_mutation_allowed`는 환경 선언의 정합성만
검증하고 문자열을 돌려주며, `assert_c6c_mutation_allowed`는 대상 service가 C6c
runtime(Map 4종·pinvi-api)과 겹치지 않으면 그대로 반환한다. 따라서 db·storage·gra·cadv·
prom·geo·conc는 통과한다.

현재는 프론트의 `NODE_ENV` 분기(빌드 타임 제거)가 **유일한** 방어선인데, 이는 브라우저
번들의 속성이지 백엔드의 속성이 아니다. `npm run dev` 프론트를 운영 백엔드에 붙이면
버튼이 보이고 실제로 실행된다.

- [x] `ComposeService.ensure_target`이 `assert_manager_mutation_allowed`의 반환값(`mode`)을
      받아, `assert_c6c_mutation_allowed` 호출 직후(`c6c_deployment_lock` 안, baseline 검사와
      첫 Docker subprocess보다 먼저) `mode == "production"`이면 target·service 구성과 무관하게
      전면 차단하도록 했다. `assert_c6c_mutation_allowed`·`assert_compose_mutation_allowed`·
      `control_container`·`update_container_config`·`reset_container_config`는 건드리지
      않는다 — 그 경로들의 "비-C6c target은 production에서도 허용" 동작은 개별 컨테이너
      제어를 위한 의도된 동작이라 범위 밖이다(`ensure`만 target 전체를
      `--build`/`--force-recreate`+init step까지 허용하는 범용 dev 부트스트랩이라 다르다).
      `DeploymentContractError` → 기존과 동일하게 HTTP 409, 라우트 코드 변경 없음.
      CLI(`ktdctl ensure`)도 같은 메서드를 호출하므로 동일하게 막힌다.
- [x] 음성 회귀 테스트 2건 추가: production + 비-C6c target(`storage`) → 거부(`subprocess.run`
      미호출까지 확인), local/개발 모드에서는 동일 target의 정상 `ensure` 흐름이 막히지
      않는지 양성 대조로 확인. 기존 `test_production_generic_mutation_guard_rejects_every_api_entrypoint`
      (C6c target `map`은 `assert_c6c_mutation_allowed`로 이미 차단)와 공존하며 서로
      가리지 않는 것도 확인.
- [x] **적대적 리뷰 2명 + 독립 검증 통과.** 리뷰어 1은 `mode` 판정이
      `_validate_mutation_environment`(local/production만 반환하는 fail-closed 계약)에
      전적으로 의존해 우회 불가능함을, `ensure_target`/`_ensure_target_unlocked`의 호출자가
      API 라우트와 CLI 단 둘뿐임을(다른 라우트·WS·스케줄러 경로 없음) 확인했다. 리뷰어 2는
      두 production 차단(C6c 전용·신규 전면 차단)이 서로 마스킹하지 않고 공존함을, 실제로
      의존하는 production `ensure` 사용처가 문서상 없음을 확인했다. 두 리뷰 모두 raise 메시지가
      비-C6c target에서만 도달하는 이 지점에 compatible-pair(Map/PinVi 전용) 문구를 넣은 것이
      부적절하다고 지적해 메시지를 단순화했고("manage this service directly on the host
      instead"), 테스트 하나에 실제로는 읽히지 않는 `monkeypatch.setenv` 호출이 남아 있던 것도
      제거했다. 검증 단계에서 두 리뷰의 모든 구체적 주장을 코드에서 직접 재확인(CONFIRMED)했고
      우회·회귀는 발견되지 않았다. backend 1049 passed, ruff 기존 9건 유지, 변경 파일 mypy
      clean. 백엔드 정책 변경만이라 UI 변경이나 실브라우저 E2E는 필요 없다.

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
- [x] PR을 병합한다. (PR #67, 2026-07-20 merged)
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
- [x] 최신 main rebase·CI green을 통과한다. (PR #69, 2026-07-20 merged)
- [ ] n150 compatible-pair capture와 최종 Playwright login form을 확인한다.

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
- [x] 단일 적대적 리뷰와 CI를 통과한다. (PR #71, 2026-07-20 merged)
- [ ] n150 compatible-pair recapture와 C7 live E2E에서 feature 관리 REST를 확인한 뒤 issue #70을 닫는다.

### T-041: C6c rollback image retention 보장

- [x] n150에서 `pinvi-pair deploy --build` 성공 직후 새 manifest의 active 5개는 존재하지만
      rollback으로 기록된 직전 active 5개 image ID가 모두 사라지는 문제를 재현하고 issue #72로 기록한다.
- [x] build/no-build deploy와 rollback은 mutation 전 manifest active/rollback 합집합을, capture/deploy는
      candidate를 service+전체 image SHA 기반 예약 namespace에 보존하고 exact ID를 재검증한다.
      (PR #73, `c6c_image_retention.py`의 `ensure_pair_references`/`require_empty_retention_namespace`/
      `validate_retention_namespace_is_reserved`)
- [x] retention 실패는 첫 container mutation 전에 중단한다. manifest commit 뒤 새 rollback 밖 reference를
      정리하고, cleanup residue가 있으면 다음 mutation 전에 해소해 과거 세대가 누적되지 않게 한다.
      (`test_stale_retention_cleanup_failure_blocks_candidate_and_container_mutation`)
- [/] moving tag rollover, 일부 tag 실패·wrong-ID collision, SIGKILL cut point, candidate 실패 정리,
      active=rollback dedupe, no-build·rollback·capture, post-commit cleanup pending과 다음 mutation 차단을
      실행형 회귀 테스트로 고정한다.
      **7개 시나리오 중 6개 완료. `SIGKILL cut point`만 미커버.** 확인 결과 기존 `SIGKILL` 매치는
      container 대상 `docker kill -s SIGKILL`이고, retention 진행 중 프로세스가 죽는 cut point를
      재현하는 테스트는 없다(`cut_point|interrupted|crash|abrupt` 검색 0건). 나머지 6개 대응:
      rollover `test_moving_service_tag_rollover_keeps_previous_content_reference`,
      일부 tag 실패 `test_partial_tag_failure_does_not_remove_existing_references`,
      wrong-ID collision `test_existing_content_reference_never_retargets_another_image`,
      candidate 실패 정리 `test_candidate_retention_failure_cleans_only_to_start_manifest_before_stop`,
      dedupe `test_active_equals_rollback_deduplicates_five_references`,
      post-commit cleanup pending `active_manifest_committed_retention_cleanup_pending`.
- [x] mutation 전 실패 또는 시작 manifest 확정+previous runtime 복구 검증 성공 때만 candidate를 정리하고,
      recovery 실패·mixed runtime·manifest 불확정이면 관련 reference를 모두 보존한다.
      (`test_rollback_verification_failure_keeps_manifest_and_recovers_start_pair`)
- [ ] 단일 적대적 리뷰와 CI green 뒤 n150 exact Manager로 compatible-pair를 재배포하고 실제 rollback
      가용성 및 C7 strict live E2E를 통과하면 issue #72를 닫고 완료 이력으로 옮긴다.

### T-043: WS 인가 동시성 상한 + 프론트 배포 preflight

T-042의 적대적 리뷰가 남긴 두 항목이다. accept-then-close 계약상 미인증 peer도 handshake를
완료하는데 WS 라우트에는 제한이 없었고, 운영 호스트의 `frontend/node_modules`가 부분 설치
상태로 남아 배포 중 빌드가 실패할 수 있었다.

- [x] 동시 인가 handshake를 `KTDM_WS_MAX_PENDING_AUTHORIZATIONS`로 묶고 초과분을
      `close(1013)`으로 흘려보낸다. per-IP를 쓰지 않는 이유(프록시 IP 단일화)를 함께 기록한다
- [x] 서버를 내리기 전에 프론트 툴체인을 검증하는 `scripts/verify-frontend-toolchain.sh`를
      추가한다(`npm ls --depth=0`이 결정적 게이트)
- [x] 적대적 리뷰 2명 반영 — 잘못된 위협 모델(미인증 peer는 SQLite에 도달하지 못한다),
      shed 경로가 더 비쌌던 문제(거절당 로그 write), `--limit-concurrency` 오권고,
      counter 증가를 검증하지 않던 테스트 공백을 모두 수정
- [ ] n150 배포 후 1013 shed 동작과 preflight를 운영에서 확인한다










