# Docker 관리 설계

이 문서는 `kor-travel-docker-manager`가 Portainer와 유사한 Docker 관리 경험을 제공하되, Kor Travel/PinVi 개발 및 로컬 운영에 필요한 범위로 제한하는 기준을 정리한다.

---

## 1. 목표

`kor-travel-docker-manager`는 불특정 다수에게 노출되는 범용 Docker 콘솔이 아니다. 목적은 `pinvi`, `kor-travel-concierge`, `kor-travel-map`, `kor-travel-geo`가 의존하는 공용 Docker 인프라와 앱 컨테이너를 한 곳에서 확인하고 실행하는 것이다.

- 의존 Docker가 꺼져 있으면 UI, API, CLI에서 즉시 실행한다.
- 개발환경에서는 필요한 경우 `docker compose up -d --build`로 빌드 후 실행한다.
- UI에서 상태, 포트, 리소스, 로그, compose 설정, Docker inspect 핵심 정보를 확인한다.
- 컨테이너 파라미터는 `docker-compose.yml`을 source of truth로 저장하되, credential 값은 `.env` override로 둔다.
- `DESIGN.md`의 Pure Black, 1px hairline, 직각 panel, M 삼색선의 희소한 accent만 관리 대시보드에 적용한다.
- dev 기본 네트워크는 Docker host 모드(`KTDM_DOCKER_NETWORK_MODE=host`)이며, 각 컨테이너는 호스트 정규 포트에 직접 바인딩한다.
- 운영(prod) 공개 주소는 저장소에 커밋하지 않고 gitignore된 `.env`(`KTDM_PROD_URL_*`)에만 두며, registry/대시보드가 이를 읽어 컨테이너별 공개 URL을 표시한다.

---

## 2. 현재 코드 수준

| 영역 | 현재 상태 | 보완 기준 |
|---|---|---|
| 상태 조회 | `GET /api/v1/containers`, WebSocket `/api/v1/ws/status` 구현 | target 단위 상태와 container inspect를 UI에 연결 |
| 제어 | `start`, `stop`, `restart` 구현 | target 단위 `ensure`와 개발환경 `--build` 지원 |
| 로그 | REST 최근 로그, WebSocket 실시간 로그 구현 | CLI `logs`와 UI 상세 패널에서 동일한 대상 기준 사용 |
| 메트릭 | CPU, 메모리, I/O 10초 수집 및 30일 보관 | 컨테이너 상세 화면에서 최근 추세와 현재값 동시 표시 |
| 설정 변경 | compose의 ports, env, volumes, networks 저장 및 재생성 구현 | 입력 검증, secret redaction, 변경 전 diff 표시 |
| CLI | `ktdctl` Python CLI 추가 | 다른 Kor Travel/PinVi 프로젝트에서 의존 Docker 실행용으로 사용 |
| 문서 | 통합 DB 모델과 CLI/API target 기준 정리 | 대시보드 상세 패널 구현 시 화면 문서 추가 |

현재 공식 관리 컨테이너는 다음 18개다. dev 기본 네트워크는 host 모드(`KTDM_DOCKER_NETWORK_MODE=host`)이며, 포트 NAT가 없으므로 각 컨테이너는 호스트 정규 포트에 직접 바인딩한다(컨테이너 내부 포트 = 호스트 포트). 서비스 간 참조는 `127.0.0.1:<포트>`를 사용한다.

| 컨테이너 ID | Docker 컨테이너 | 역할 | 포트(host=container) |
|---|---|---|---|
| `kor-travel-geo-postgresql` | `kor-travel-geo-postgres` | `kor_travel_geo`, `pinvi`, `kor_travel_concierge`, `krtour_map` database를 담는 통합 PostgreSQL / PostGIS | `5432` |
| `rustfs` | `kor-travel-rustfs` | Kor Travel/PinVi 계열 미디어 및 원천 데이터용 S3 호환 오브젝트 스토리지 | `12101`, `12105` |
| `grafana` | `kor-travel-grafana` | 다른 앱과도 공통 연계하는 Grafana 시각화 도구 | `12205` |
| `cadvisor` | `kor-travel-cadvisor` | Docker 컨테이너 리소스 메트릭을 노출하는 cAdvisor Exporter | `12301` |
| `prometheus` | `kor-travel-prometheus` | cAdvisor Exporter와 앱 메트릭을 수집하고 저장하는 Prometheus | `12401` |
| `kor-travel-geo-api` | `kor-travel-geo-api-latest` | `kor-travel-geo` REST API | `12501` |
| `kor-travel-geo-ui` | `kor-travel-geo-ui-latest` | `kor-travel-geo` admin Web UI | `12505` |
| `kor-travel-concierge-api` | `kor-travel-concierge-api-latest` | `kor-travel-concierge` API | `12601` |
| `kor-travel-concierge-mcp` | `kor-travel-concierge-mcp-latest` | `kor-travel-concierge` MCP HTTP | `12602` |
| `kor-travel-concierge-scheduler` | `kor-travel-concierge-scheduler-latest` | `kor-travel-concierge` scheduler | 내부 실행 |
| `kor-travel-concierge-ui` | `kor-travel-concierge-ui-latest` | `kor-travel-concierge` Web UI | `12605` |
| `kor-travel-map-api` | `kor-travel-map-api-latest` | `kor-travel-map` admin API | `12701` |
| `kor-travel-map-dagster` | `kor-travel-map-dagster-latest` | `kor-travel-map` Dagster Webserver | `12702` |
| `kor-travel-map-dagster-daemon` | `kor-travel-map-dagster-daemon-latest` | `kor-travel-map` Dagster daemon | 내부 실행 |
| `kor-travel-map-ui` | `kor-travel-map-ui-latest` | `kor-travel-map` admin Web UI | `12705` |
| `pinvi-api` | `pinvi-api-latest` | PinVi API | `12801` |
| `pinvi-dagster` | `pinvi-dagster-latest` | PinVi Dagster Webserver (`apps/etl/Dockerfile`, code location `tripmate.etl.definitions`) | `12802` |
| `pinvi-web` | `pinvi-web-latest` | PinVi Web UI | `12805` |

---

## 3. 설정 파일 기반 target 모델

UI/API/CLI는 Docker service 이름을 직접 외우지 않고 앱 관점 target을 사용한다. 공식 target 정의와 의존 순서는 `config/docker-targets.yml`에서 읽는다. 기본 의존 순서는 다음과 같다.

```text
db -> storage -> gra -> cadv -> prom -> geo -> conc -> map -> pinvi
```

이 순서는 누적 적용된다. 예를 들어 `ktdctl map --build`는 `db`, `storage`, `gra`, `cadv`, `prom`, `geo`, `conc`, `map` 순서로 필요한 서비스를 실행하고 초기화 단계를 수행한다. 새 앱이나 중간 의존성이 생기면 `config/docker-targets.yml`의 `dependency_order`, `targets.<id>.services`, `targets.<id>.init_steps`만 확장한다.

| 공식 별칭 | 의미 | 누적 실행 범위 | 대표 별칭 |
|---|---|---|---|
| `db` | 통합 DB | 통합 PostgreSQL/PostGIS 실행 및 DB/role/schema 복구 | `postgresql`, `postgres`, `database` |
| `storage` | 통합 RustFS | `db` + RustFS 실행 및 bucket 복구 | `rustfs`, `s3`, `object-storage` |
| `gra` | 공용 Grafana | `storage` + Grafana Web UI 실행 | `grafana`, `dashboard`, `visualization` |
| `cadv` | cAdvisor Exporter | `gra` + cAdvisor Exporter 실행 | `cadvisor`, `exporter`, `metrics-exporter` |
| `prom` | Prometheus | `cadv` + Prometheus 실행 | `prometheus`, `metrics`, `monitoring` |
| `geo` | 지오코더/리버스지오코더 | `prom` + `kor-travel-geo` API/Web UI 실행 + 원천 데이터 적재 검증 | `kor-travel-geo`, `geocoder`, `reverse-geocoder` |
| `conc` | Kor Travel Concierge | `geo` + `kor-travel-concierge` API/MCP/Scheduler/Web UI 실행 | `kor-travel-concierge`, `concierge`, `agent` |
| `map` | Kor Travel Map | `conc` + `kor-travel-map` API/Dagster/Web UI 실행 | `kor-travel-map`, `krtour-map`, `python-krtour-map` |
| `pinvi` | PinVi | `map` + PinVi API/Dagster/Web UI 실행 | `srv`, `main`, `pinvi` |
| `all` | 전체 | `db`부터 `pinvi`까지 전체 순서 | `default` |

`geo` 이후 앱 target은 모두 실제 앱 컨테이너를 이 저장소 compose에서 빌드하고 실행한다. `main`은 독립 target이 아니라 `pinvi`의 호환 별칭이며, 새 자동화에서는 짧은 별칭 `srv`를 사용한다.

로컬 host 포트는 `docs/ports.md`의 정책을 따른다. `db` 대역은 `12000-12099`지만 PostgreSQL은 표준 `5432` 접속 포트를 고정하므로 비워 둔다. `storage` 대역의 RustFS는 S3 API `12101`, console `12105`를 사용한다. `gra`는 Grafana `12205`, `cadv`는 cAdvisor `12301`, `prom`은 Prometheus `12401`을 사용한다. `geo` 대역의 `kor-travel-geo`는 API `12501`, Web UI `12505`를 사용한다. `conc` 대역은 `12601`/`12602`/`12605`, `map` 대역은 `12701`/`12702`/`12705`, `pinvi` 대역은 `12801`(API)/`12802`(Dagster)/`12805`(Web)를 사용한다. `kor-travel-docker-manager` 자체 Backend API와 Dashboard Web은 dependency 변화에 흔들리지 않도록 `12901`, `12905`를 사용한다.

---

## 4. 초기화 및 복구 흐름

`ensure`는 `docker compose up -d` 후 target 순서에 맞춰 idempotent 초기화 단계를 실행한다.

| 단계 | 실행 조건 | 스크립트 | 역할 |
|---|---|---|---|
| DB 복구 | `db` 이상 | `scripts/ensure-kor-travel-geo-db.sh` | PostgreSQL readiness 대기, `kor_travel_geo`, `pinvi`, `kor_travel_concierge`, `krtour_map`, `krtour_map_dagster` database 생성/소유자 보정, role/password refresh, PostGIS/pg_stat_statements/schema grant 보정 |
| RustFS 복구 | `storage` 이상 | `scripts/ensure-rustfs-buckets.sh` | RustFS health 대기 후 `pinvi-media`, `kor-travel-geo`, `kor-travel-concierge`, `krtour-map`, `krtour-uploads` bucket 생성 |
| Geo 원천 검증 | `geo` 이상 | `scripts/verify-kor-travel-geo-source.sh` | `/data/juso` 마운트와 `load_manifest`, `tl_juso_text`, `mv_geocode_target` 적재 상태 확인 |

`geo` target은 compose에서 `kor-travel-geo-api`, `kor-travel-geo-ui`를 실행하고, dev 기본 host 네트워크에서 API 컨테이너는 `127.0.0.1:5432`(PostgreSQL)와 `127.0.0.1:12101`(RustFS)을 사용한다. 대시보드와 CLI는 registry에 등록된 컨테이너 이름(`kor-travel-geo-api-latest`, `kor-travel-geo-ui-latest`)을 같은 Docker 대상으로 사용한다.

`geo` 검증은 원천 DB가 비어 있거나 핵심 테이블이 없으면 기본적으로 실패한다. 전체 적재는 무겁고 `kor-travel-geo`의 도메인 로더가 책임지는 작업이므로, manager는 자동 전체 적재 대신 명확한 실패 메시지와 복구 지침을 출력한다. 비어 있는 DB를 의도적으로 허용해야 하는 경우에만 `.env`에서 `KOR_TRAVEL_GEO_STRICT_SOURCE_CHECK=0`으로 낮춘다.

---

## 5. 공개 인터페이스

### 5.1 CLI

정식 CLI는 백엔드 패키지의 console script인 `ktdctl`이다. 짧은 별칭은 곧바로 `ensure`로 해석된다.

```bash
ktdctl targets
ktdctl db --build
ktdctl storage
ktdctl geo --recreate
ktdctl conc --build
ktdctl map --build
ktdctl srv --build
ktdctl gra
ktdctl cadv
ktdctl prom
```

명시형 명령도 유지한다.

```bash
ktdctl status srv
ktdctl ensure geo --build
ktdctl logs storage --follow
ktdctl action kor-travel-geo-postgresql restart
ktdctl inspect kor-travel-geo-postgresql --json
```

다른 Kor Travel/PinVi 저장소에서는 개발 서버 시작 전에 필요한 target만 호출한다.

```bash
ktdctl srv --build
```

### 5.2 API

| 메서드 | 경로 | 설명 |
|---|---|---|
| `GET` | `/api/v1/targets` | 앱 관점 target 목록과 의존 순서 |
| `POST` | `/api/v1/targets/{target}/ensure` | target 서비스를 `docker compose up -d`로 실행하고 초기화 단계 수행 |
| `GET` | `/api/v1/containers` | 관리 컨테이너 상태 목록 |
| `GET` | `/api/v1/containers/{container_id}/inspect` | Docker inspect 핵심 정보의 redacted 요약 |
| `POST` | `/api/v1/containers/{container_id}/action` | `start`, `stop`, `restart` |
| `POST` | `/api/v1/containers/{container_id}/config` | compose 파라미터 저장 및 재생성 |
| `GET` | `/api/v1/containers/{container_id}/logs` | 최근 로그 |
| `GET` | `/api/v1/containers/{container_id}/metrics` | 최근 메트릭 이력 |

`ensure`는 Docker SDK가 아니라 `docker compose`를 인자 배열로 실행한다. 반면 stats, logs, inspect, 개별 action은 Docker SDK를 유지한다.

---

## 6. UI 방향

대시보드는 관리 작업에 집중한다. 마케팅 hero나 자동차 사진을 넣지 않고, `DESIGN.md`의 룩앤필을 아래 방식으로만 반영한다.

- Pure Black canvas, hairline border, 직각 panel을 기본 표면으로 사용한다.
- M 삼색선은 상단 4px divider 또는 중요 section marker로만 사용한다.
- 상태 테이블은 dense dashboard 형태를 유지하고, 반복 카드 남용을 피한다.
- 상세 패널은 컨테이너 선택 시 오른쪽 drawer 또는 modal로 열어 inspect, mounts, networks, env redaction, 최근 로그, 최근 메트릭을 함께 보여 준다.
- 파라미터 편집은 변경 전 diff와 재생성 경고를 표시하고, credential literal 입력은 `.env` 사용을 안내한다.

---

## 7. 안전 규칙

- Docker 관리 대상은 registry에 등록된 target과 container로 제한한다.
- target registry의 공식 source of truth는 `config/docker-targets.yml`이며, 임시 하드코딩 target을 API/CLI에 추가하지 않는다.
- 외부 공개 인증, 사용자 계정, 멀티테넌시 기능은 v1 범위가 아니다.
- `docker compose` 실행은 반드시 문자열 shell이 아니라 인자 배열로 수행한다.
- inspect와 로그 출력에서 secret 성격의 environment 값은 redaction한다.
- compose 파일은 구조 설정을 저장하고, 비밀번호와 API key는 `.env` 또는 `.env.local`에 둔다.
- 포트 `5432`, `12101`, `12105`, `12205`, `12301`, `12401`, `12501`, `12505`, `12601`, `12602`, `12605`, `12701`, `12702`, `12705`, `12801`, `12802`, `12805`, `12901`, `12905`는 Kor Travel/PinVi 계열 프로젝트가 공용으로 사용하므로 임의 변경하지 않는다.
