# bindings.md — 두 곳에 적힌 사실과 그것을 묶는 기계

`AGENTS.md` DO NOT 15의 인벤토리다. **같은 사실이 두 곳 이상에 적혀 있는데 사본을
지울 수 없는 경우**를 여기 등록한다. 등록의 목적은 하나다 —

> 결박된 중복과 방치된 중복을 **grep으로 구분할 수 있게** 만든다.

중복 자체는 결함이 아니다. 테스트가 프로덕션 상수를 다시 적는 것은 결박이고 좋은
중복이다. 문제는 둘이 겉보기에 같다는 것이다.

## 왜 이 파일이 생겼나

2026-09-02 하루에 같은 결함 클래스가 **네 번** 나왔다. 전부 증상이 같았다 —
조용히 갈라지고, 값비싼 실행 시점에야 드러난다.

| # | 사실 | 두 곳 | 드러난 지점 | 대가 |
|---|---|---|---|---|
| 1 | 봉인 여부 / 소각 여부 | 한 필드(`classification`)가 두 사실을 겸함 | 29분 rebuild 실패 뒤 | 회전 사이클 1회 |
| 2 | npm workspace 목록 | `package.json` glob ↔ Dockerfile `COPY` 목록 | 71분 rebuild 뒤 | 회전 사이클 1회 |
| 3 | 핀된 Map revision | pin registry ↔ PinVi pair 계약 | 71분 rebuild **뒤** | 회전 사이클 1회 |
| 4 | 설치본 실행 비트 대상 | git index mode ↔ 설치 스크립트 chmod 목록 | 회전 실행 시점 | launcher 무효 |

실측: 세 저장소의 코드·설정(문서 제외)에 40/64-hex 리터럴이 2개 이상 파일에
중복 등장하는 건이 **126건**이다(Manager 67 · PinVi 11 · Map 48). 대부분은 정당한
결박이지만, 어느 것이 그런지 지금은 알 수 없다. 그래서 **신규분부터** 등록한다.

## 등록 형식

각 항목에 다음 다섯을 적는다.

- **사실**: 무엇이 중복인가
- **정본**: 둘 중 어느 쪽이 진실인가
- **사본**: 어디에 다시 적혀 있나
- **사본이 필요한 이유**: 왜 유도로 지울 수 없나 (없으면 유도해서 지워라)
- **결박**: 둘을 묶는 테스트 이름. 없으면 왜 없는지

---

## 등록된 결박

### B-1. (해제) 설치본에서 실행 가능해야 하는 launcher 집합

ADR-51 D(I-2)에서 사본이 없어졌다. 새 installer는 archive mode를 눕히지 않고 group/other 쓰기만
지우므로(`chmod -R go-w`) 실행 비트는 git index 그대로 설치본에 간다 — 정본 하나만 남았다.
index에서 launcher 넷이 `100755`인지는 `test_root_launchers_are_executable_in_the_git_index`가 본다.

### B-2. (해제) `pair_contract_invalid`의 진단 어휘

ADR-51 잃는 보장 G-3에서 닫힌 어휘가 없어졌다. preflight는 어떤 diagnostic이든 가린 첫 줄을 내므로
사본이 필요 없다. 진단 없는 `pair_contract_invalid` 호출이 0건인지만
`test_every_pair_failure_carries_a_diagnostic`이 본다.

### B-3. M05 harness의 2-role 고정

- **정본**: `pinned_runtime_release.RUNTIME_SOURCE_ROLES`
- **사본**: `m05_isolated_harness`의 `M05IsolatedRuntimeRole` ·
  `_EXPOSED_RUNTIME_SERVICE_ROLES` · `_RUNTIME_IMAGE_ROLES`
- **사본이 필요한 이유**: 별칭 배선은 오늘 no-op이고, 정본이 늘면 별칭만 따라가고
  networks/services/images/provenance/이항 분기가 남아 harness가 unconstructible이
  된다. 그 실패는 두 스택을 다 띄운 뒤(1~2시간)에 나온다 — 능력 없는 결합이라
  이 저장소가 결함으로 규정한 과결박이다(`docs/tasks.md` GM-18 won't-fix).
- **결박**: `test_m05_isolated_harness_is_deliberately_two_role`
- **드러난 경위**: won't-fix 결정이 이 테스트를 안전망으로 들었는데 **테스트가
  존재하지 않았다.** `test_normative_docs_cite_real_symbols`가 잡았다.

### B-4. 규범 문서가 지목하는 테스트

- **정본**: `backend/tests/`의 테스트 함수
- **사본**: `AGENTS.md`·`SKILL.md`·`CLAUDE.md`·`docs/tasks.md`·`docs/resume.md`·
  `docs/runtime-pin-registry.md`·`docs/docker-management.md`의 인용
- **사본이 필요한 이유**: 문서가 결정의 근거로 테스트를 지목하는 것은 이 저장소의
  규약이고, 그 인용이 없으면 왜 그렇게 결정했는지 추적할 수 없다.
- **결박**: `test_normative_docs_cite_real_symbols`
- **범위 주의**: `docs/journal.md`·`docs/tasks-done.md`·감사 보고서는 **검사하지
  않는다.** 과거 기록이므로 지금 기준으로 고치라고 하면 역사를 다시 쓰게 된다.

> **B-5~B-9 최초 계획(ADR-58 proposed, 2026-10-13).** 각 항목의
> "결박"에 적은 T-MGR 번호의 PR이 사본과 테스트를 **같은 PR에서** 만든다(DO NOT 15의 결박 규칙).
> 테스트 이름은 그 PR에서 확정하며, 여기에는 이미 존재하는 테스트 파일만 코드 폰트로 적는다.
> **2026-10-14 잠정 시행**(ADR-58 "잠정 시행"): B-6은 렌더 스캔 테스트와 함께 들어왔고(2026-10-16 T-MGR-01c가 registry 파생으로 마감), B-5는 한 파일
> (`workspace.yaml` = n150 plane)에 호스트 필터만 들어왔다. 나머지는 계획 그대로다.
> **2026-10-15 PR #481 소스 구현:** B-5·B-7·B-9를 아래 테스트로 결박한다. 운영 설치·두 독립 리뷰와 live gate는 별도다.

### B-5. 호스트별 공용 Dagster workspace의 합집합

- **사실**: 공용 plane에 합류한 location 집합(오늘 6: geo·conc·map·pinvi·weather·transport)
- **정본**: `config/docker-targets.yml`의 `dagster.control_plane: shared`인 target과 그 `host:`
- **사본**: `config/dagster-shared/workspace.n150.yaml` ∪ `config/dagster-shared/workspace.odroid.yaml`
  (오늘의 파생물 `workspace.yaml`이 호스트별 두 파일이 된다; compose가 `${KTDM_HOST:-n150}`으로 고른다)
- **사본이 필요한 이유**: compose bind는 커밋된 파일만 마운트할 수 있고 registry를 읽지 못한다 — 오늘의
  `workspace.yaml`과 같은 이유(`docker-compose.yml:789-794`)
- **결박**: 합집합 == shared 집합 ∧ 교집합 ∅ ∧ `workspace.n150.yaml`은 첫 구현에서 오늘 파일과 byte 동일 —
  기존 `test_dagster_shared_workspace_is_derived`를 두 파일 + 호스트별 `all` 규칙으로 확장(T-MGR-02)
- **잠정(2026-10-14)**: 파일은 아직 `workspace.yaml` 하나이고 파생은 "shared ∧ default 호스트(n150)"다 — weather·transport
  (`host: odroid`)가 빠져 4 location(geo·conc·map·pinvi), 2026-10-15 geo도 빠져 3 location(conc·map·pinvi). 결박은 `test_the_workspace_file_is_the_derivation`과 옮긴 target의
  빨간 대조군(`test_bringing_a_moved_target_back_drifts_the_workspace`·`test_a_moved_target_left_in_the_workspace_is_named`).
  당시에는 odroid 파일이 없어 "합집합 == shared 집합"을 단언하지 않았다.
- **현재 결박**: `test_host_workspaces.py`의 `test_all_committed_hosts_are_derived`,
  `test_third_host_move_changes_only_affected_workspaces`, `test_host_all_and_own_switch_are_honored`가
  registry의 모든 host를 계산·비교한다. instance YAML byte는 변경하지 않는다. 옛 별칭 `workspace.yaml`은 2026-10-17
  T-MGR-NH NH-4로 없어졌고(파일이 **없음**을 같은 테스트가 단언), compose·registry의 default 호스트 기본값
  (`${KTDM_HOST:-…}`·`${KTDM_DAGSTER_WORKSPACE_DIGEST:-…}`)은 `test_default_host_values_are_the_default_hosts_derivation`이 결박한다.

### B-6. 내부 consumer가 부르는 형제 서비스의 origin(2026-10-14 잠정 시행 → 2026-10-16 T-MGR-01c 구현)

- **사실**: Map(api·dagster code-server·legacy webserver/daemon)→geo·Map→transport(API gateway)·PinVi ETL·pinvi-api→weather의 origin
- **정본**: registry — 형제 target의 `consumer_origin`(`http://{host}:<port>`; 포트는 2026-10-14 소유자가 odroid에서 통일한 값,
  geo `12501`·transport `12301`·weather `12201`)을 R1으로 렌더한다(`registry.consumer_origin`): 소비자 서비스가 도는 호스트에서 보아
  형제와 같은 호스트면 `127.0.0.1`, 아니면 형제 호스트의 `addr`. 소비자 호스트는 그 서비스를 **실행**하는 target의 호스트, 없으면
  (옛 `legacy-dagster` 서비스) 그 서비스의 Dagster family target의 호스트다 — 둘 다 없거나 하나로 정해지지 않으면 기대값을 짓지
  않고 "unresolved"로 보고한다. 형제가 여러 호스트에 있으면 주소가 하나가 아니라는 오류다
- **사본**: Manager `docker-compose.yml`에서 **registry가 명시한 형제 포트(`consumer_origin` 포트와 `retired_consumer_ports` —
  transport `14001`·weather `14101`)를 가진 URL을 값에 둔 env 전부**(비활성 profile 포함) — 오늘 8곳(geo 5·transport 1·weather 2).
  형제의 다른 포트(S3·UI·Dagster)는 소비자 origin이 아니다. 빼는 것: 형제 자신의 서비스(같은 호스트 안 자기 호출)와
  `NEXT_PUBLIC_*`(브라우저 공개 origin — root `.env`의 `KTDM_PROD_URL_*`, R6). 비교는 끝 `/`를 무시한다
- **사본이 필요한 이유**: compose는 registry를 읽지 못한다(compose subprocess는 allowlist env만 받는다). 값을 root `.env`로 내리면
  호스트마다 손으로 맞추는 두 번째 사본이 생긴다 — 그래서 기본값을 커밋 파생물로 두고 `.env` 재정의는 `targets validate`가
  거부한다(기대값과 다를 때만; 빈 값은 compose가 기본값을 쓴다; 문구에는 변수 이름과 기대값만; 설치기 flip 전 검사도 같은 명령)
- **결박**: `services/consumer_origins.py`의 스캔을 `backend/tests/test_consumer_origin_defaults.py`가 쓴다 —
  `test_every_consumer_default_is_the_registry_origin`(기본값 == 기대 origin, 하한 `test_the_scan_sees_every_known_consumer`),
  `test_reverting_any_one_consumer_to_loopback_is_named`·`test_moving_the_registry_port_and_retiring_the_old_one_names_every_consumer`
  (빨간 대조군), `test_unrelated_sibling_ports_are_not_consumer_origins`, `test_validate_rejects_a_root_env_override_that_differs`
  (validate 효과). 테스트에 포트 표는 없다

### B-7. 공용 workspace digest의 기본값

- **사실**: daemon·webserver가 기대하는 `workspace.yaml` sha256[:16]
- **정본**: 설치된 `config/dagster-shared/workspace.<host>.yaml`의 내용
- **사본**: compose `KOR_TRAVEL_DAGSTER_WORKSPACE_DIGEST: ${KTDM_DAGSTER_WORKSPACE_DIGEST:-<n150 파일 digest>}`의 기본값
  (오늘은 리터럴 `4461abc13cce9c4f` — 2026-10-15 geo·weather·transport가 빠진 n150 파일(concierge·map·pinvi), `docker-compose.yml:829,899`), odroid는 root `.env` 값
- **사본이 필요한 이유**: healthcheck가 마운트 파일과 env를 대조해 낡은 컨테이너를 exit시키는 기제 자체가 이 사본이다
  (`:847,911`); installer에 계산 로직을 더하지 않는다(ADR-51)
- **결박**: 기본값 == `workspace.n150.yaml` sha256[:16] — 기존 `test_dagster_shared_config` 확장(T-MGR-02); odroid 값은
  `targets validate`가 설치된 파일과 대조(효과)
- **현재 결박**: `test_frozen_workspace_uses_same_bytes_for_digest_and_parse`와
  `test_either_frozen_plane_digest_drift_rejected`가 기존 frozen render의 두 plane을 같은 파일 bytes에 묶는다.
  `test_wrong_host_or_noncanonical_workspace_path_rejected`가 선택 host와 실제 bind 경로를 함께 검사한다.

### B-8. Prometheus scrape 타깃(2026-10-16 T-MGR-04 구현)

- **사실**: 중앙 Prometheus(registry에서 파생 — `prometheus.yml`을 마운트하는 compose 서비스의 호스트, 오늘 n150)가 긁는 job·주소·경로
- **정본**: registry — `metrics_path`가 있는 컨테이너마다 job 하나(`job_name` = 컨테이너 id), 그 컨테이너가 있는 호스트
  (`container_hosts`)마다 static_config 하나: 주소는 R1(Prometheus 호스트면 `127.0.0.1`, 아니면 `hosts.<이름>.addr`), 포트는
  `connection`(`scheme://{host}:<port>`)의 포트 — metrics가 다른 포트에서 나오면 컨테이너의 `metrics_port`(2026-10-17
  T-MGR-04r, weather code-server), `labels: {host: <이름>}`(`services/prometheus_targets.py`)
  Manager backend job(`kor-travel-docker-manager`, systemd)도 같은 파일 안에서 파생한다(2026-10-17 NH-6): 선언된 모든 호스트마다
  static_config 하나(R1 주소·`host` label), 포트는 `deploy/systemd/ktdm-backend.service`의 `ExecStart … --port`, 경로는 backend `/metrics` route
- **사본**: `config/prometheus/prometheus.yml` — 손으로 쓴 job은 없다
- **사본이 필요한 이유**: Prometheus는 정적 파일만 읽는다
- **결박**: `backend/tests/test_prometheus_targets_are_derived.py` — 파생 == 파일(`test_prometheus_targets_are_the_registry_derivation`),
  target 이동·포트·경로 변경·세 번째 호스트가 빨갛다. 손 job 추가는 `test_no_job_in_the_file_is_hand_written`, 호스트 누락은
  `test_the_manager_job_has_one_static_config_per_declared_host`, unit 포트 변경은 `test_the_manager_port_comes_from_the_backend_unit`이 빨갛다. 설치기 health 확인과 `main.py` dev 진입점의
  하드코딩 포트가 unit과 다르면 `test_every_hard_coded_backend_port_is_the_unit_port`, unit에 `--port`가 둘이면
  `test_a_unit_with_two_ports_is_rejected`가 빨갛다

### B-9. 교차 호스트 `depends_on` 간선 ∅

- **사실**: compose `depends_on`이 tenant 경계를 넘지 않는다(R7)
- **정본**: registry에서 계산한 각 서비스의 host 집합
- **사본**: `docker-compose.yml`의 `depends_on` 간선(오늘 `kor-travel-map-api → kor-travel-geo-api`, `:1535`이 유일한 위반 후보)
- **사본이 필요한 이유**: 간선 자체는 compose에만 있다 — 지울 수는 있어도(T-MGR-01b가 지운다) 다시 생기는 것을 registry가 막지
  못한다
- **결박**: "양끝 host 집합이 다른 간선 집합 == ∅"(T-MGR-01b; 간선 하나를 일부러 남겨 빨갛게 확인)
- **현재 결박**: `test_declared_compose_dependencies_do_not_start_off_host_tenants`가 실제 Compose와 registry를
  비교하며 위험 간선을 다시 넣어 실패를 검증한다. 실제 실행은 `test_frozen_compose_dependencies_cannot_start_a_foreign_tenant`와
  lower/public Compose 회귀가 frozen graph와 실제 parsed flag 경계를 확인한다.

### B-10. 공용 PostgreSQL `command`의 GUC 이름 집합

- **사실**: 공용 instance가 `-c`로 설정하는 GUC 이름들
- **정본**: `docker-compose.yml` `kor-travel-shared-postgres`의 `command:`
- **사본**: `c6c_deployment._POSTGRES_ALLOWED_COMMAND_SETTINGS`
- **사본이 필요한 이유**: 후보 검사기는 검사 대상인 후보 문서를 신뢰할 수 없다 — 허용 목록은 후보 밖에
  있어야 `hba_file`·`config_file` 같은 이름을 막는다
- **결박**: `test_f1d_compose_contract.py::test_the_canonical_shared_postgres_runtime_shape_passes` — raw compose의
  공용 서비스를 C6c 검사기 자체에 통과시키고(목록을 테스트에 다시 적지 않는다), 허용 목록 밖의 GUC 하나를 더한 대조군이
  거부되는지 함께 본다. compose에만 이름을 더하면 빨강(2026-10-13 `max_locks_per_transaction`으로 확인). 2026-10-16(T-MGR-03) 서버 기본값 6키(`max_parallel_workers`·`_per_gather`·`effective_io_concurrency`·`checkpoint_timeout`·`wal_compression`·`autovacuum_max_workers`)를 더했고, 같은 검사기의 최상위 키 허용 목록(`_POSTGRES_ALLOWED_SERVICE_KEYS`)에 `mem_limit`·`memswap_limit`·`oom_score_adj`를 더했다(CI가 빨강으로 잡았다)

---

## 결박할 수 없어 남겨 둔 것

### U-1. 핀된 Map revision (교차 저장소)

- **정본**: Manager runtime pin registry의 `map_revision`
- **사본**: PinVi `contracts/kor-travel-map-m05-pair-provenance-v1.json`의
  `map.admin.source_revision`·`map.full.source_revision`, 그리고 PinVi 테스트 핀 2곳
- **왜 결박할 수 없나**: 두 선언이 **다른 저장소**에 있고 릴리스 주기가 독립이라,
  어떤 단일 빌드도 한쪽을 다른 쪽에서 유도할 수 없다. PinVi는 자기가 어느 pinset에
  들어갈지 모른다.
- **완화**: `scripts/rotate-pinned-pair`가 회전 **전에** 대상 PinVi revision의
  계약을 읽어 두 값을 비교하고, 어긋나면 두 값을 찍고 회전을 거부한다
  (`docs/runtime-pin-registry.md` §7.5). 비용은 shallow fetch 한 번,
  막는 것은 rebuild 한 사이클이다.
- **근본 수리(백로그)**: 계약 스키마 v2에서 `source_revision`을 걷어내고
  **생산자를 pin registry 하나로** 만든다. PinVi가 revision을 필요로 하는 세 지점은
  계약이 아니라 Manager receipt가 전달한 값을 쓴다. 그러면 Map 문서 한 줄이 PinVi
  커밋을 부르는 증폭이 사라진다. 비용이 커서(PinVi 3개 검사 재설계 + 두 저장소
  동시 릴리스) 별건으로 둔다.
- **재발 이력**: 2026-09-01 이후 재핀 **네 번**(#506·#508·#510·#519). 그중 하나는
  커밋 제목이 스스로 "docs-only bump"라고 적었다.

### U-2. (계획, ADR-58) 두 호스트 `.env`의 교차 비밀 2개

- **사실**: Map→geo 공개 API 키(`KOR_TRAVEL_MAP_KOR_TRAVEL_GEO_API_KEY`)와 Map→transport service token — 발급하는 쪽(geo·transport,
  odroid `.env`)과 쓰는 쪽(Map, n150 `.env`)이 다른 호스트의 root `.env`에 같은 값을 가져야 한다
- **왜 결박할 수 없나**: 두 파일은 저장소 밖(root 0600)이고 호스트가 다르다 — Manager 간 SSH가 없으므로(ADR-58 결정 2·10) 어느
  Manager도 상대 파일을 읽어 대조할 수 없다. 동기화 기제를 만드는 것은 새 신뢰 경계다(ADR-50 "먼저 멈춘다")
- **완화**: 효과로 드러난다 — 키 불일치는 geo 401/transport 404로 즉시 보이고, 플립 절차의 게이트("n150 쪽 컨테이너 stop 상태에서
  Map geocode·export 1 run 성공, 키 경로 200")가 그것을 전환 **전에** 잡는다(`docs/prod-deployment.md` §9.5). geo는 VWorld 공용 키
  폴백 유지(§14-21 추천)면 Map 쪽 값이 VWorld 키 하나로 고정된다
- **weather metrics bearer는 해당 없음**: 관측 1단계(federate)에서 n150이 weather-api `/metrics`를 직접 긁지 않으므로 토큰이 한
  호스트에만 있다
