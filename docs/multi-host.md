# N-호스트 일반화(T-MGR-NH) — 설계 (최종안)

> 상태: **최종안(2026-10-16)** — 리뷰 3회를 반영했다(1차 "범위가 크다·가정이 빠졌다", 2차 blocker B1·B2와 정정 C1). 구현은 이
> 문서의 PR 순서대로 한다. 정본 결정은 ADR-58이고, 과제 정의는 `tasks.md`의 T-MGR-NH다.

## 1. 목표와 수용 기준

임의의 target을 registry `hosts:`의 N개 호스트 중 어디에나 두고 옮길 수 있게 한다. 호스트를 더하거나 target을 옮길 때 **코드는
바뀌지 않는다.** 바뀌는 것은 registry와 커밋된 파생물(이미 대조 테스트가 있다: workspace·소비자 origin·Prometheus 타깃)뿐이다.

**공통 수용 기준(모든 PR):** 이동 대상을 소유하지도 소비하지도 않는 호스트의 렌더는 byte 그대로다. 소비자 origin과 Prometheus
타깃은 파생에 따라 바뀐다. **확인 명령**은 그 호스트에 설치된 `.env`로 `docker compose config`를 돌려 PR 전후를 비교하는
것이다(n150은 설치본 `/opt/kor-travel-docker-manager`). 각 PR은 자기 테스트에 세 번째 가상 호스트(`mars`) fixture 단언을 넣는다
(이동 대상, 그리고 호스트별 기대 diff). 공통 하네스는 두지 않는다.

## 2. 이미 N-ready인 것 (01a~06k)

| 영역 | 근거 |
|---|---|
| 호스트 선언·자기 식별(N개, default 하나, 설치본은 로컬 선언 호스트 정확히 하나) | `registry.current_host`, `test_registry_hosts.py` |
| 변경 경계(쓰기는 직접 소유 호스트에서만, `ensure`는 host-local 폐포) | `require_local_targets`·`local_mutation_target`, `test_host_mutation_scope.py`(mars) |
| R1 주소(같은 호스트는 loopback, 원격 단일 소유는 그 addr) | `registry.r1_address` |
| 소비자 origin(R2) — compose 기본값 8건(NH-8 뒤 9건)을 registry와 대조, `.env` 재정의는 validate가 거부 | T-MGR-01c, `test_consumer_origin_defaults.py`(mars). 플립 PR이 기본값을 고치지 않으면 빨강 |
| workspace 파생 + 설치 파일 == digest env | #481, T-MGR-02 |
| Prometheus registry job 파생 | T-MGR-04, `test_prometheus_targets_are_derived.py`(mars) |
| 호스트별 값(PG 이미지·튜닝·mem_limit·데이터 장치)과 효과 가드 | T-MGR-03·05 |
| Python 코드의 호스트 리터럴 0건(허용 1건 — 다른 저장소 파일 이름) | `test_code_has_no_host_name_literals` |

## 3. 호스트 배치 규칙

### 3.1 여러 호스트에서 도는 서비스

| 서비스 | 소유 | 규칙 |
|---|---|---|
| 공용 PG `kor-travel-shared-postgres` | target 6개의 `services`: `geo`·`conc`·`map`·`pinvi`·`weather`·`dagster` | **PG는 소비 target이 있는 호스트에서 돈다 — 구성상 그렇다.** 소비 target이 PG를 직접 소유하므로 target을 옮기면 PG가 그 호스트 렌더에 함께 들어간다. 그래서 "PG가 없는 호스트의 소비자"는 만들 수 없다. 별도 검사가 필요 없고, R7(NH-2)의 PG 간선은 **빨강을 주장하지 않는다**(소유 합집합이라 실패할 수 없다). 소유를 `dagster` 하나로 모으는 안은 `services` 목록·상태 표시·폐포·C6c declared 축을 함께 흔들어 단순하지 않아 택하지 않는다 |
| 공용 Dagster plane(daemon·webserver·gateway) | `dagster`(오늘 `all`) | plane은 PG가 있는 호스트에서 돈다(같은 target, ADR-58 결정 3 NOTE). 공유 location이 있는 호스트에는 plane이 있어야 한다 → NH-2의 validate 규칙 하나(§4 NH-2) |
| RustFS `rustfs` | `storage`(오늘 `all`) | NH-3에서 명시 목록 |
| cAdvisor | `cadv`(`all`) | 모든 호스트 — `all`이 맞다 |
| node_exporter | `node`(외부 project, `odroid`) | 다른 호스트에 두면 `host:`를 넓힌다 |

### 3.2 같은 호스트에 있어야 하는 것 (C6c loopback)

C6c가 loopback을 고정한다: PinVi→Map `127.0.0.1:12701`(`c6c_deployment.py:4108-4121`), concierge UI→API `127.0.0.1:12601`(`:248`),
PG DSN loopback(`:589`·`:987`·`:1146`). 이 간선들은 compose `depends_on`으로도 선언돼 있다(`pinvi-api → kor-travel-map-api`,
`kor-travel-concierge-ui → kor-travel-concierge-api`). 그 밖에 **`kor-travel-map-api → kor-travel-concierge-api`가 Map과 concierge를
묶는다** — Map을 옮기면 concierge도 같은 호스트여야 한다. R7을 validate로 옮기면(NH-2) 이 간선들의 "같은 호스트"가 강제된다(PG
간선은 §3.1대로 구성상 성립). depends_on 없이 loopback을 쓰는 간선을 새로 만들면 그 PR이 depends_on을 함께 둔다 — 문서 제약
(`platform-topology.md`에 한 줄, NH-2).

## 4. 남은 가정과 PR 계획

| # | 가정 | 위치 | PR |
|---|---|---|---|
| G1 | `host:`는 단일 호스트 또는 `all`뿐이다 | `registry.target_hosts` | NH-1 |
| G15 | `host:` 생략 = default 호스트(암묵) — `gra`·`prom`·`conc`·`map`·`pinvi` | `registry.target_hosts` | NH-1 |
| G7 | 교차 호스트 compose `depends_on`(R7) 검사가 테스트에만 있다 | `test_host_mutation_scope.py::_cross_host_declared_dependencies` | NH-2 |
| G17 | R7이 **소유자 없는 서비스**를 건너뛴다: `kor-travel-shared-db-init-transport`, profile 서비스(`kor-travel-map-db-role-bootstrap`·`kor-travel-map-application-schema`·`pinvi-admin-bootstrap`·`kor-travel-concierge-scheduler`·legacy `*-dagster`/`*-daemon`/weather plane 8개). init step이 돌리는 `kor-travel-shared-db-init-dagster`·`kor-travel-dagster-storage-migrate`·`rustfs-init`는 소유자 계산에서 빠져 있다 | 같은 함수 | NH-2 |
| G2 | PG를 뺀 공용 infra(`dagster` plane·`storage` RustFS)가 `all` | `config/docker-targets.yml` | NH-3 |
| G4 | compose·registry의 default 호스트 기본값에 결박 테스트가 없다 | `docker-compose.yml:875,878,947,950`, `docker-targets.yml:997,1004` | NH-4 |
| G5 | 옛 `workspace.yaml` 별칭 | 아래 NH-4 범위 | NH-4 |
| G18 | Map UI의 **server-side BFF**(`app/api/geo/[...path]/route.ts`)가 `KOR_TRAVEL_GEO_INTERNAL_URL ?? NEXT_PUBLIC_KOR_TRAVEL_GEO_BASE_URL`을 읽는데, compose map-ui에는 앞의 것이 없다. 그래서 공개 URL로 우회하고 있다(n150 운영은 `.env`의 공개 URL로 동작 — 코디네이터 확인). **1·2차 문서의 "버그 아님"은 틀렸다** | `docker-compose.yml` map-ui, `c6c_deployment.py:4480` | NH-8 |
| G16 | config의 project 이름·경로에 호스트 이름: `kor-travel-odroid-monitoring`·`/opt/kor-travel-odroid` | `docker-targets.yml`의 `kor-travel-node-exporter` 컨테이너 `external_project`, `node` target `external_project.project`·`working_dir` | NH-5 |
| G8 | 스크립트 문자열 1건(`run-pin-request-isolated-e2e:141` "n150 격리 e2e …") | scripts | NH-5 |
| G9 | Prometheus의 손으로 쓴 Manager job(`prometheus.yml:12-18`) | `config/prometheus/prometheus.yml` | NH-6 |
| G10 | 런북이 odroid 특유 단계다 | `prod-deployment.md` §9 | NH-7 |

| PR | 내용 | n150 렌더 영향 | 빨갛게 되는 수용 테스트 | 의존 |
|---|---|---|---|---|
| **NH-1** `host:` 목록 + 명시 필수 | `target_hosts`가 이름·목록(`[n150, odroid]`)·`all`을 받는다. **host 목록은 infra 전용이다** — tenant(소비자 origin을 가진 target)에 목록을 두면 #491의 `consumer_origin` 규칙이 오류를 낸다(origin이 하나가 아니다). 로더는 빈 목록, 미선언 이름, 목록 안 `all`, **`host:` 생략**을 거부한다(임계값 없음). 같은 PR에서 `gra`·`prom`·`conc`·`map`·`pinvi`에 `host: n150`을 적는다 | 없음 — 생략과 명시 default는 같은 값이다(`docker compose config` 동일) | `host:`를 지우면 로더 오류. mars fixture: 목록 target이 목록 밖 호스트의 `ensure` 폐포·workspace·Prometheus에 나타나면 빨강 | 없음 |
| **NH-2** R7·소비 규칙을 validate로 | 검사를 `services/`로 옮기고 `targets validate`(설치기 flip 전)가 렌더된 compose로 돌린다. 소유자 = target `services` ∪ 그 target의 `init_steps`가 `run`하는 서비스. **소유자 없는 compose 서비스는 거부**한다. 예외는 명시 allowlist(서비스 이름 + 이유): `kor-travel-shared-db-init-transport`(수동 1회 — `prod-deployment.md` §9.2 4a′)와 profile 서비스(위 G17 목록). 위반은 "S(A) → P(B), A ⊄ B"로 보고한다. 규칙 하나를 더한다: 공유 Dagster location을 가진 target의 호스트 ⊆ `dagster` 호스트(그 호스트 workspace를 실을 plane이 있어야 한다). **code-server↔plane 호스트는 이 규칙이 보장한다**(PG는 §3.1대로 구성상). `platform-topology.md`에 "loopback 간선은 depends_on으로 선언한다" 한 줄 | 없음 | mars fixture: `pinvi`만 mars로 옮기면 `pinvi-api → kor-travel-map-api`로 거부. `map`만 옮기면 `kor-travel-map-api → kor-travel-concierge-api`로 거부. 새 무소유 서비스를 compose에 넣으면 거부. allowlist의 서비스가 compose에서 사라지면 빨강(낡은 허용 금지). 공유 Dagster target을 mars로 옮기고 `dagster`에 mars가 없으면 거부 | 없음(NH-1 뒤면 목록 fixture도 쓴다) |
| **NH-3** plane·RustFS 명시 목록 | `dagster`·`storage`를 `host: all` → `host: [n150, odroid]`로. 비교 테스트는 두지 않는다(누락은 NH-2의 plane 규칙이 잡는다) | 없음(두 호스트 집합 = 오늘의 `all`) | mars fixture: mars 렌더에 plane·RustFS가 없다(PG는 mars에 소비 target이 있을 때만 — §3.1) | NH-1·NH-2 |
| **NH-4** default 호스트 기본값 결박 + 별칭 제거 | 테스트 하나: compose `:-n150`·`:-<digest>` 기본값과 registry bind source(`docker-targets.yml:997,1004`)가 default 호스트 파생값(호스트 이름, `workspace.<default>.yaml`의 sha256[:16])과 같다. **별칭 제거 범위**: `runtime_topology.read_host_workspace`의 `workspace.yaml` 허용, `_SHARED_WORKSPACE_SOURCE`, `runtime_topology.py:202`의 정규식 무접미 분기, `config/dagster-shared/workspace.yaml` 파일, `scripts/dagster-shared-cutover.sh:268,271`, compose 주석 `:821`, `test_f1d_compose_contract.py:2280` 부근 서술, `test_host_workspaces.py::test_all_committed_hosts_are_derived_and_alias_is_default`(별칭 단언 제거). **컨테이너 안 경로 `/opt/dagster/dagster_home/workspace.yaml`은 그대로** | 없음 — compose는 이미 `workspace.${KTDM_HOST}`이고 n150 `.env`에 `KTDM_HOST`가 설정돼 있다(이름·상태 확인 2026-10-16) | default를 mars로 바꾸면 기본값 불일치로 빨강. workspace 파일 하나를 고치면 digest 기본값 불일치로 빨강. 별칭 경로 render는 validate가 거부 | 없음 |
| **NH-5** 호스트 이름 없는 project 이름 | `kor-travel-odroid-monitoring` → `kor-travel-host-monitoring`, `/opt/kor-travel-odroid` → `/opt/kor-travel-host-monitoring`. 스크립트 사유 문자열 1건을 중립 문구로. 리터럴 검사 확장은 하지 않는다. **호스트 측 작업**: odroid의 node-exporter stack은 `compose-mon.yml` **파일만** 새 디렉터리로 옮겨 새 project 이름으로 올린다(`/opt/kor-travel-odroid` 자체는 geo·weather·transport·thermal-guard가 쓰므로 그대로)(T-OPS-01b와 같은 창, 저장소 밖) | 없음(외부 project) | 기존 Python 리터럴 검사(스크립트 문자열) | 적용은 T-OPS-01b와 함께 |
| **NH-6** Manager job 파생 | 손으로 쓴 Manager job을 `prometheus_targets` 파생에 넣는다: Manager는 모든 선언 호스트에서 돈다(결정 2) → 호스트마다 static_config(R1 주소·`host` label). 새 alert·rule은 범위 밖 | compose 없음. `prometheus.yml`에 odroid static_config 1개(Prometheus reload) | mars fixture: Manager job에 mars가 없으면 빨강. 손 수정은 기존 대조 테스트가 빨강 | odroid Manager 설치(T-OPS-01 단계 8) 뒤 머지 |
| **NH-7** 런북(doc-only) | `prod-deployment.md`에 두 절차: "호스트 추가 `<name> <addr>`", "target 이전 `<target> <from> <to>`"(registry 한 줄 → 파생물 대조 테스트가 가리키는 파일 갱신 → validate → `ensure`). **수동 단계를 명시한다**: ① 옛 호스트의 그 target code-server를 먼저 정지(두 plane 이중 적재 방지) ② stateful tenant(Map·PinVi·concierge)의 PG dump/restore — **범위 밖 수동 단계** ③ 비-default 호스트 `.env`의 `KTDM_DAGSTER_WORKSPACE_DIGEST` 갱신 ④ HAProxy 백엔드 변경 — **범위 밖 수동 단계** ⑤ 옛 호스트 정리(컨테이너·DB, 연결 0 확인 뒤). §9의 odroid 단계는 그 사례로 남긴다 | 없음 | 문서 lint 초록 | NH-1~4 |
| **NH-8** Map UI BFF의 geo 내부 origin | compose map-ui `environment:`에 `KOR_TRAVEL_GEO_INTERNAL_URL: ${KOR_TRAVEL_MAP_DOCKER_GEO_BASE_URL:-http://192.168.1.13:12501}`(R2 geo origin — map-api 4곳과 **같은 원천 변수**라 `.env` 재정의 규칙도 그대로)을 넣고 `test_consumer_origin_defaults`의 소비자 env에 포함한다. C6c의 map-ui 정본 env 모양(`c6c_deployment.py:4480` 부근)도 함께 고친다 | **map-ui env 한 줄**(허용 diff로 명시). map-ui 재생성 | registry geo `host:`를 mars로 바꾸면 기대 origin이 달라져 빨강(기존 테스트 규칙). env를 빼면 빨강 | 없음 — 먼저 할 수 있다 |

순서: NH-8 ∥ NH-1 ∥ NH-2 ∥ NH-4 ∥ NH-5 → NH-3 → NH-7. NH-6은 odroid Manager 설치 뒤.
규모: 모두 S(NH-7은 doc). NH-8을 뺀 나머지는 n150 compose 렌더 영향이 없다.

## 5. Deferred (이번 범위 밖 — 2-호스트 현실에서는 스키마만 흔든다)

| 항목 | 이유 |
|---|---|
| **R1 다중 소유 관측** — 대시보드가 다중 소유 컨테이너(PG·cAdvisor)를 호스트별로 보이게 하기. 지금은 원격 소유 둘 이상이면 `{host}` 미해석 → `unreachable` | 관측 확장이다. 세 번째 호스트 때 다시 본다 |
| **Prometheus alert·rule·federate**(ADR-58 결정 11) | `config/prometheus/`에 아직 없는 기능이다. N-호스트와 무관한 새 작업이다 |
| **offbox 방향**(결정 10)·**중앙 관측 호스트**(결정 11)의 registry 필드 | 오늘은 관례에 따른 역할이다(ADR-58 NOTE) |
| **공개 URL·HAProxy 백엔드 파생** | 저장소 밖 손 설정이다(NH-7 수동 단계) |
| **config 리터럴 검사**(`config/*.yml`) | 값보다 검사가 커진다. NH-5의 rename으로 오늘 남은 것은 없다 |
| PG 소유를 `dagster` 하나로 모으기 | §3.1 — 구성상 co-location이 이미 성립하고, 모으면 흔드는 것이 많다 |
| compose R2 소비자 기본값 8건의 자동 재작성 | 이미 registry와 대조된다(플립 PR이 고치지 않으면 빨강) |
