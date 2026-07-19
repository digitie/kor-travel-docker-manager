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

UI/API/CLI는 Docker service 이름을 직접 외우지 않고 앱 관점 target을 사용한다. 공식 target 정의와 의존 관계는 `config/docker-targets.yml`에서 읽는다. 의존 관계는 각 target의 `depends_on`으로 표현되는 **DAG**이며, `ktdctl <target>`은 해당 target의 transitive 의존 폐포를 위상정렬 순서로 실행한다(`dependency_order`는 표시/결정적 정렬용 linearization).

```text
db -> storage -> gra -> cadv -> prom ─┬─ geo ──┐
                                      └─ conc ──┴─> map -> pinvi
```

핵심 의존: `geo`와 `conc`는 모두 `prom`에만 의존하며 서로 독립이다(**concierge는 geo에 의존하지 않는다**). `map`은 `geo`와 `conc` 모두에 의존하고, `pinvi`는 `map`에 의존한다. 예를 들어 `ktdctl conc`는 `db, storage, gra, cadv, prom, conc`만 실행하고(geo 제외), `ktdctl map`은 `db, storage, gra, cadv, prom, geo, conc, map`을 실행한다. 새 의존성은 `targets.<id>.depends_on`으로 선언한다.

| 공식 별칭 | 의미 | 누적 실행 범위 | 대표 별칭 |
|---|---|---|---|
| `db` | 통합 DB | 통합 PostgreSQL/PostGIS 실행 및 DB/role/schema 복구 | `postgresql`, `postgres`, `database` |
| `storage` | 통합 RustFS | `db` + RustFS 실행 및 bucket 복구 | `rustfs`, `s3`, `object-storage` |
| `gra` | 공용 Grafana | `storage` + Grafana Web UI 실행 | `grafana`, `dashboard`, `visualization` |
| `cadv` | cAdvisor Exporter | `gra` + cAdvisor Exporter 실행 | `cadvisor`, `exporter`, `metrics-exporter` |
| `prom` | Prometheus | `cadv` + Prometheus 실행 | `prometheus`, `metrics`, `monitoring` |
| `geo` | 지오코더/리버스지오코더 | `prom` + `kor-travel-geo` API/Web UI 실행 + 원천 데이터 적재 검증 | `kor-travel-geo`, `geocoder`, `reverse-geocoder` |
| `conc` | Kor Travel Concierge | `prom` + `kor-travel-concierge` API/MCP/Scheduler/Web UI 실행 (geo 비의존) | `kor-travel-concierge`, `concierge`, `agent` |
| `map` | Kor Travel Map | `geo`+`conc` + `kor-travel-map` API/Dagster/Web UI 실행 | `kor-travel-map`, `krtour-map`, `python-krtour-map` |
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

### 7.1 Concierge 소비자 read 키 배포

`kor-travel-map`의 Concierge feature pull은 루트 `.env`의
`KOR_TRAVEL_MAP_KOR_TRAVEL_CONCIERGE_API_KEY` 한 값을 유일한 secret source로 사용한다.
base compose가 이 값을 실제 fetcher를 실행하는 Dagster·Dagster daemon에 같은 이름으로 주입한다.
map API에는 사용하지 않는 read secret을 주입하지 않는다.
Concierge BFF/operator용 static `API_KEYS`를 소비자에 공유하거나, gitignore된
`docker-compose.override.yml`에 key literal을 반복하지 않는다.

prod 전환 순서는 다음과 같다.

1. 최신 Concierge API/UI를 배포하고 실제 Alembic runner로 `upgrade head`를 실행한다. DB head
   `20260713_0017`과 scope migration `20260713_0016`의 `scope` `NOT NULL`·`read|admin` CHECK를
   각각 확인한 뒤 API/MCP/scheduler/UI를
   서비스별로 재생성한다. UI의 admin hash/session secret이 비어 있지 않고 실제 로그인 POST가
   200+`Set-Cookie`, 잘못된 비밀번호가 401인지 확인한다.
2. Concierge 관리 UI/API에서 소비자·owner·발급일을 식별할 수 있는 label로 DB `read` scope 키를
   발급하고, DB에는 hash만 남았으며 발급 audit가 기록됐는지 확인한다.
3. manager의 gitignore된 prod `.env`와 override를 mode `0600` 임시 파일로 원자 백업한다. read 키는
   `.env`의 단일 변수에만 저장하고, override에 남은 map API·Dagster·daemon의 기존 key literal
   세 줄과 base URL literal 세 줄을 모두 제거한다. `docker compose config --quiet`만 실행하고
   resolved config 전체는 출력하지 않는다.
4. Dagster·Dagster daemon을 재생성한다. 과거 배포에서 map API에 같은 환경변수가 들어갔다면
   map API도 한 번 재생성해 과거 secret을 제거한다. map API에는 해당 key env가 없음을 확인한다.
   `.env`와 두 수집기 컨테이너의 값을 한 프로세스 안에서 constant-time 비교해
   `nonempty && all_equal`의 성공 여부와 exit code만 확인한다. 값·길이·digest는 출력하지 않는다.
5. n150에서 Concierge backend를 직접 호출한다. 먼저 `limit=1`로 snapshot과 changes를 각각
   2페이지까지 요청해 cursor가 실제 다음 페이지를 가리키는지 검증한다. 이어 `page_size=200`으로
   두 모드를 끝까지 순회해 전체 건수와 export ID 무중복을 확인한다. cursor는 opaque라 크기를
   비교하지 않는다. `has_more=true`면 unseen `next_cursor`가 필수이고 그 값을 다음 요청에 그대로
   쓰며, `has_more=false`면 non-null cursor여도 종료한다. 빈 최종 page의 입력 cursor echo도
   허용한다. 실제 Dagster 컨테이너 fetcher는 `endpoint=snapshot|changes`, `cursor=None`,
   `page_size=200`을 각각 명시해 두 모드의 전체 결과를 소비한다. read 키의
   `DELETE /api/v1/destinations/0`과 `GET /api/v1/settings`가 403이고 응답이 admin scope 부족을
   가리키는지 확인한다. 데이터가 2페이지보다 적다면 cursor 검증을 합격으로 처리하지 않는다.
6. 기존 static 키가 BFF와 공유돼 있으면 먼저 BFF/operator key를 회전한다. 새 static admin 키를
   생성해 `KOR_TRAVEL_CONCIERGE_API_KEYS=old,new`로 API/MCP/scheduler를 재생성하고,
   `KOR_TRAVEL_CONCIERGE_BACKEND_API_KEY=new`로 UI를 강제 재생성한다. UI key가 allowlist와
   일치하는지 값 비노출 비교 후 실제 로그인 POST와 BFF 호출을 다시 확인한다.
7. 모든 smoke가 통과한 뒤에만 `KOR_TRAVEL_CONCIERGE_API_KEYS=new`으로 구 static 키를 제거하고
   API/MCP/scheduler를 재생성한다. 구 키 401, 새 admin 키의 내부 API 200, read 키의 공급 GET 200·
   내부/write 403, UI 로그인 200+`Set-Cookie`를 다시 확인한다.
8. 성공 시 key/cookie 임시 파일과 secret 포함 백업을 즉시 삭제한다. 실패 시 `.env`와 override를
   함께 복원해 관련 서비스를 재생성하고 신규 DB read 키를 폐기한 뒤 임시 파일을 삭제한다. static
   제거 뒤 실패했다면 구 static 키를 allowlist에 임시 재등록하고 API/UI를 재생성하며 incident와
   rollback 시점만 기록한다.

2026-07-13 n150 전환에서는 위 절차를 다음 결과로 완료했다.

- snapshot·changes의 `limit=1` 2페이지 cursor 검증이 모두 통과했다.
- `page_size=200` 전체 순회는 두 모드 모두 8페이지, 1,416건이었고 export ID 중복이 없었다. 실제
  Dagster 컨테이너 수집기도 두 모드에서 각각 1,416건을 반환했다.
- map API를 재생성해 사용하지 않는 read secret을 제거했고, Dagster·Dagster daemon만 `.env`의
  동일한 read key를 가진다는 값 비노출 동등성 검증을 통과했다.
- static admin 교체 후 구 키 401, 신규 admin GET 200, read 공급 GET 200, read 내부/write 403을
  확인했다. UI 로그인 POST 200+`Set-Cookie`, BFF settings 200, 잘못된 비밀번호 401도 재확인했다.
- 성공 뒤 key/cookie 임시 파일과 secret 포함 제한권한 백업을 모두 삭제했다.

### 7.2 Map OpiNet·KREX provider 키 주입

`kor-travel-map`의 OpiNet·KREX credential은 gitignore된 루트 `.env`의 현재 이름을 source로
사용한다.

- `KOR_TRAVEL_MAP_OPINET_API_KEY`: OpiNet station·price 수집용이다. base compose가 실제 수집기를
  실행하는 Dagster·Dagster daemon에만 같은 이름으로 명시 보간한다.
- `KOR_TRAVEL_MAP_KREX_EX_API_KEY`: 교통 돌발·notice를 포함한 EX endpoint용이다. base compose가
  Dagster·Dagster daemon에만 같은 이름으로 명시 보간한다.
- `KOR_TRAVEL_MAP_KREX_GO_API_KEY`: data.go.kr 계열 KREX 수집용이다. 같은 두 수집 서비스에만 명시
  보간한다.

Map API에는 provider credential을 하나도 주입하지 않는다. provider 조회·수집은 Dagster 경계에서
수행하며, 제거된 `KOR_TRAVEL_MAP_API_*_SERVICE_KEY`와 legacy
`KOR_TRAVEL_MAP_DATA_GO_KR_SERVICE_KEY`가 빈 값으로라도 API container environment에 존재하면 Map
entrypoint 또는 Manager C6c preflight가 기동 전에 거부한다. Map API compose의 `command`와
`entrypoint` override도 금지해 immutable image의 migration·fail-close entrypoint를 우회하지 못하게 한다.
기동 뒤 runtime inspect에서도 `Cmd=["./docker/api-entrypoint.sh"]`, `Entrypoint=null`과 provider
environment 부재를 다시 확인한다.

과거 `KRTOUR_MAP_*` 이름을 source로 쓰면 `.env`에 현재 이름의 key가 있어도 빈 문자열이
컨테이너로 전달된다. 따라서 override에 bare key나 secret literal을 반복하지 않는다. 변경 뒤에는
resolved config 전체를 출력하지 말고 `docker compose config --quiet`를 실행한 뒤, 한 프로세스
안에서 `.env`와 두 수집 컨테이너 값을 constant-time 비교하고 API 컨테이너에는 provider runtime
변수가 없는지 확인한다. 검증 결과는 `nonempty && all_equal` 같은 불리언만 남기며
실제 값·길이·digest는 로그에 남기지 않는다. API 컨테이너에는 제거된 provider runtime 이름이
하나도 없어야 한다.

### 7.3 Map↔PinVi canonical ops read/cancel principal

PinVi API는 Map의 canonical `/v1/ops/datasets*`와 `/v1/ops/pipeline*` 조회, 그리고
`POST /v1/ops/pipeline/executions/import_job/{job_id}/cancel`만 사용한다. 브라우저 BFF secret,
public service token, trusted CIDR을 재사용하지 않는다.

Map API의 production fail-closed 설정은 ops pair만으로 완결되지 않는다. ADR-23에 따라 manager
`.env`는 admin proxy secret, API-only service token, API-only cursor signing secret도 서로 다른
값으로 보관한다. admin proxy secret은 Map API와 Map UI BFF에만 전달하고 service/cursor 값은 Map
API 외 service에 전달하지 않는다. profile은 `production`, public API key gate는 `true`, debug route는
`false`로 candidate에 고정한다. Map metrics는 인증된 Prometheus scrape 경로가 없는 동안 endpoint를
`false`로 명시해 무인증 fallback과 startup drift를 함께 차단한다. host network admin proxy의
trusted CIDR는 `127.0.0.1/32`·`::1/128` exact JSON으로 명시한다. 실제 값·길이·digest는 로그에
남기지 않고 shape, 상호 불일치, 허용 service별 존재 여부만 증거로 남긴다.

- manager `.env`가 `KOR_TRAVEL_MAP_API_OPS_READ_TOKEN`과
  `KOR_TRAVEL_MAP_API_OPS_CANCEL_TOKEN`의 단일 source다. 두 값은 각각 32자 이상이고 공백 문자가
  하나도 없으며 서로 달라야 한다.
- Map API에는 같은 이름으로 전달한다. PinVi API에는 각각
  `PINVI_KOR_TRAVEL_MAP_OPS_READ_TOKEN`과 `PINVI_KOR_TRAVEL_MAP_OPS_CANCEL_TOKEN`으로 전달한다.
- mode는 추론하지 않는다. 개발 PC는 `KTDM_DEPLOYMENT_ENVIRONMENT=local`,
  `PINVI_ENVIRONMENT=development`, `KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED=false`, n150은 각각
  `production`, `production`, `true`를 명시한다. 세 값이 없거나 서로 맞지 않으면 manager는 어떤
  container도 변경하기 전에 중단한다.
- PinVi API의 Map 주소는 `PINVI_KOR_TRAVEL_MAP_ADMIN_BASE_URL`이며, manager host-network
  기본값은 `http://127.0.0.1:${KOR_TRAVEL_MAP_API_CONTAINER_PORT}`이다. publish port가 아니라 Map
  process의 실제 bind port를 사용한다. production은 host network·loopback·bind port 일치를
  preflight에서 강제한다.
- Map Dagster·daemon·UI와 PinVi Web·Dagster에는 위 token을 전달하지 않는다. `docker inspect`로
  검증할 때도 값을 출력하지 말고 서비스별 존재 여부와 constant-time 동등성 boolean만 남긴다.
- read token은 GET에만 사용한다. cancel token은 exact import-job cancel endpoint에만
  사용하며 schedule command, refresh policy, update request mutation은 같은 token으로도 403이어야
  한다.

Map UI runtime 인증의 `KOR_TRAVEL_MAP_UI_ADMIN_USERNAME`,
`KOR_TRAVEL_MAP_UI_ADMIN_PASSWORD_HASH`, `KOR_TRAVEL_MAP_UI_SESSION_SECRET`은 기본값 없는 `:?`
보간으로 Map UI의 정확한 Env path에만 전달한다. PBKDF2 반복 수는 100,000 이상, session secret은 32자
이상이며 Python `str.isspace()`가 인식하는 모든 Unicode 공백 문자를 포함할 수 없다. Map UI는
`env_file`을 사용할 수 없다. username은 confidential 값이 아닌 identity라 다른 서비스의 일반 scalar와
같거나 그 일부여도 허용하지만, Map UI의 exact wiring/runtime equality와 Map UI 밖 username 환경변수 이름
금지는 유지한다.
현재 pair의 exact `map_source_revision`에서 Map source `docker-compose.yml`을 읽었을 때
admin/service/profile/public/debug hard-require가 있고 cursor가 아직 없는 source env v3가 manifest
active/rollback 양쪽에만 있고 marker가 없으면, manager는 manifest logical hash를 sibling
`map-production-env-migration-v1.json`의 pending baseline으로 mutation 전에 원자 기록한다. pending은
동일 manifest 재시도만 허용하며 현재 UI admin proxy는 없음 또는 frozen exact다. activation·runtime
격리·전체 smoke가 성공하면 manifest commit 전에 marker를 complete로 바꾸며 manager는 이를 삭제하거나
pending으로 낮추지 않는다. complete 뒤에는 pair rotation으로 slot이 다시 v3/v3가 되어도 admin proxy는
필수 exact다. marker는 fixed-shape 0600 owner regular file이며 corrupt/symlink/wrong owner/mode와 baseline
drift는 fail-close한다. compatible-pair manifest v4와 pair의 exact 9개 필드는 바꾸지 않는다.

source classifier는 admin을 API+frontend, service를 API-only, cursor를 v3 전체 scalar tree 0회/v4
API-only exact 1회로 제한한다. 보호 이름·placeholder가 Dagster/daemon/build/label/command/env_file/
config/secret 등 다른 source path에 있으면 거부한다. manager candidate와 runtime의 metrics-off·trusted
loopback 검사는 이 source 세대 판정과 섞지 않고 기존 raw/resolved/runtime validator가 담당한다.
`KTDM_C6C_CONTRACT_GENERATION`, Map UI smoke 평문 비밀번호, PinVi admin smoke 계정, owned typed-failure
`KTDM_C6C_CANCEL_PROBE_JOB_ID`는 manager `.env`에만 둔다. 이 값들은 compose service env나 다른
`env_file`에 주입하지 않는다. 특히 contract generation은 secret이 아니더라도 배포 판단용 manager-only
값이므로 resolved compose의 command·label·build arg를 포함한 scalar와 runtime Env 어디에도 전달하지
않는다. frozen snapshot과 rollback도 최초 environment snapshot의 Map UI 인증값만 해석하고 hash·session
secret·ops token의 다른 서비스 노출이나 평문 비밀번호 주입을 전역 scalar에서 거부한다. 최초 설치의
manifest가 없는 환경에서는 base dependency부터 전체 토폴로지를 순서대로 bootstrap하고
candidate runtime set 전체 계약을 검증해 최초 v4를 만든다. Map dependent provenance가 없는 v1/v2/v3는
자동 전환하지 않고 거부한다. canonical v4 경로 옆에 저장소 역사상 실제 기본 파일명이었던
`compatible-pair-v2.json` 또는 `compatible-pair-v3.json`이 남아 있어도 빈 state로 간주하지 않는다.
payload를 읽어 자동 변환하지 않으며 symlink·비정규 파일·다른 owner·group/world writable mode를
포함한 어느 legacy artifact든 mutation 전에 operator migration/removal을 요구한다.

```bash
ktdctl pinvi-pair capture --verified-compatible --build
# 기본 manifest: ~/.local/state/kor-travel-docker-manager/<COMPOSE_PROJECT_NAME>/compatible-pair-v4.json
```

capture는 host lock 안에서 base dependency → Map API signed smoke → Map UI/Dagster → PinVi →
canonical smoke → PinVi Web/Dagster → 전체 smoke 순서로 candidate를 검증한다. 실패하면 v4를 기록하지
않고 Map runtime 네 service와 PinVi API를 중지하며 capture가 새로 만든 container만 제거한다.
기존 manifest는 덮어쓰지 않는다. manifest v4의 active/rollback은 Map runtime 네 image ID,
`map_source_revision`, `pinvi_image_id`, `pinvi_source_revision`, `contract_generation`,
`recorded_at`의 exact 9개 필드를 기록한다. image ID는 `sha256:<64 hex>`, source revision은
lowercase 40자 Git commit이어야 한다. `latest-main` 같은 tag, `development` revision, 단일 image,
과거 generation, 로컬에 없는 image ID는 배포와 rollback 전에 거부한다. 실행 중인 두
image ID·revision이 manifest의 active pair와 달라도 배포를 거부한다. 일반
`ensure`/container action·config·reset과 direct
Compose API mutation은 production에서 409/CLI 오류로 거부한다. `scale`·`watch`와 알 수 없는 Compose
명령도 read-only로 오인하지 않는다. production의 low-level Compose runner는 알려진 read-only 명령만
capability 없이 허용하며, 일반 인프라 mutation도 `ensure`/config 같은 관리 workflow capability를 거친다.

```bash
ktdctl pinvi-pair deploy --build
```

전용 deploy는 다음 순서를 코드로 강제한다.

1. deployment-wide host lock을 잡고 mode/token/base URL/generation, 단일 canonical base compose의
   host network·PinVi production mode·Map bind port·정확한 loopback base·container identity·다섯 immutable
   image override·`env_file`/secret 격리를 runtime 변경 전에 검사한다. 별도 compose override/include/extends는
   mutation source로 허용하지 않는다. `--build`는 두 저장소 build context가 exact Git root이고
   worktree가 clean인지 검사한 뒤 각 lowercase 40자 `HEAD`를
   `KOR_TRAVEL_MAP_GIT_COMMIT`/`PINVI_SOURCE_REVISION`으로 파생하고 PinVi build mode를
   `production`으로 고정한다. 다섯 candidate image는 runtime `up`과 분리해 먼저 build하고 immutable
   image ID·revision label과 PinVi production label을 검증한다. 사용자가 지정한 값이 파생값과
   다르거나 image label이 유효하지 않으면 첫 container stop/recreate 전에 거부한다. Docker에는
   live checkout 대신 각 `HEAD`의 일회성 Git archive context만 전달해 build 중 변경·원복과 ignored
   파일 혼입을 막는다. raw/resolved build mapping도 이 context, 저장소 내부 지정 Dockerfile,
   provenance arg만 exact 허용하고 external Dockerfile·additional context·secret·target을 거부한다.
2. 현재 active set과 공용 dependency·Map/PinVi UI·Dagster의 running/healthy를 확인한다. 현재 Map UI
   container를 inspect해 username·hash·session secret이 frozen environment와 정확히 같은지 검증한 다음,
   login→`/ops/providers`→logout→재차단 lifecycle을 통과해야 한다. 어느 단계든 실패하면 Docker mutation은
   0이며 기존 runtime도 중지하지 않는다. 통과하면 다섯 runtime을 함께 중지해 mixed set 노출을 막은 뒤
   `--no-deps`로 새 Map API image를 먼저 재생성한다.
   image의 `org.opencontainers.image.revision`을 clean Map `HEAD`와 비교한 뒤에만 직접 read 200,
   무토큰 401,
   cancel token으로 허용된 cancel 계약과 대표 non-cancel schedule command mutation 403을 확인한다.
   실제 running job이 없으면
   존재하지 않는 import-job ID의 404까지 인증 통과 증거로 사용하고, 파괴적 취소는 최종 C7 gate의
   owned job에서 수행한다.
3. `--no-deps`로 Map UI·Dagster web·daemon의 exact candidate image를 재생성하고 공통 Map revision을
   검증한 다음 새 PinVi API image를 재생성한다. PinVi image의
   `org.opencontainers.image.revision`과 `io.pinvi.build.environment=production`을 먼저 검증한 뒤
   PinVi admin ETL/provider-sync에서 canonical 조회가
   200인지 확인한다. owned fixture 취소는 409 `PIPELINE_CANCELLATION_IN_PROGRESS`,
   502 `DAGSTER_TERMINATE_FAILED`, 503 `DAGSTER_UNAVAILABLE` 중 status/code/details/retryability가 정확히
   일치하고 양의 `Retry-After`를 보존해야 한다. 429나 generic code는 실패다.
4. 변경하지 않은 모든 필수 service가 계속 running/healthy인지 확인한 뒤 managed container를 `docker inspect`로
   검사한다. Map API에는 Map 이름 세 개(read/cancel/required), PinVi API에는 대응 token 두 개만,
   Map UI에는 username·PBKDF2 hash·session secret 세 개만 존재해야 한다. runtime `.Config`의
   Env/Cmd/Entrypoint/Labels와 안전하게 순회할 수 있는 모든 scalar에서 confidential 이름·값을 찾고 각
   서비스의 정확한 허용 Env path 외 노출과 UI 평문 비밀번호 주입을 거부한다. username은 Map UI exact
   Env 이름·값만 고정하며 일반 scalar의 동일 문자열은 secret leak으로 처리하지 않는다.
5. Map UI 로그인·`/ops/providers` 보호 화면·로그아웃·재차단과 PinVi Web login shell을 확인한다. 새
   generation의 Map/PinVi canonical smoke와 runtime 격리를 한 번 더 확인한 뒤에만 active manifest를
   갱신한다.

모든 중간 실패는 배포 시작 시점 active set의 다섯 immutable image를 함께 복원하고 같은 merged
contract·Map/PinVi canonical smoke·UI auth·runtime 검사를 다시 수행한다. 복구 검증도 실패하면 다섯
runtime을 중지하고 명시적인 operator-required 상태로 끝낸다. legacy/과거 generation으로의 부분 fallback은 없다.

```bash
ktdctl pinvi-pair rollback
```

rollback 명령은 manifest의 다섯 image ID가 모두 로컬에 있는지 먼저 확인하고 단일 canonical
compose가 전체 계약을 만족하는지 **stop 전에** 확인한다. 다섯 service를 함께 중지한 뒤 Map API
복원·signed smoke, Map dependent 복원·revision 검증, PinVi 복원과 전체 smoke·UI auth·runtime 격리가
모두 일치해야 manifest의 active set을 갱신한다. 실패하면 시작 시점 set을 복구하거나 모두 중지한다.

manifest와 mode 0600 lock은 checkout이 아니라
`~/.local/state/kor-travel-docker-manager/<COMPOSE_PROJECT_NAME>/`에 함께 저장한다. production에서는
root와 `compatible-pair-v4.json`/`deployment.lock`/`map-production-env-migration-v1.json` 파일명을
고정하고 모든 path override를 거부해 같은
Compose project가 서로 다른 lock으로 갈라지지 않게 한다. manifest version은 bool/string/float 변환 없이
정확한 integer만 허용하고 두 pair의 `recorded_at`은 offset ISO 8601 datetime이어야 한다. 기록은 파일
fsync, 원자 replace, 부모 디렉터리 fsync 순서이며 마지막 fsync 실패 시 이전 byte/mode를 다시 원자
복원·fsync한다. 복원을 완료할 수 없지만 새 byte가 정확히 남아 있으면 rename commit으로 일관되게 취급해
runtime과 manifest가 서로 다른 pair로 갈라지지 않게 한다.

대시보드의 일반 container config 변경·reset·미생성 start fallback도 같은 host lock과 공통 mode 계약을
사용한다. compose 파일을 바꾼 뒤 service recreate 또는 RustFS init이 실패하면 원본 byte와 file mode를
원자 복원하고 기존 설정으로 service를 다시 recreate한다. 복원 결과의 config/runtime 성공 여부는 API
500 응답의 `detail.restoration.config_restored`와 `runtime_restored`에 분리해 남기며, 실패한 candidate
설정을 파일에 방치하지 않는다. 첫 Docker mutation이 성공한 뒤 다음 command의 preflight에서 snapshot이나
raw/resolved graph drift를 발견하면 단순 409로 축소하지 않는다. 원래 candidate 오류, `mutation_applied=true`,
복구 시도·성공 여부와 진단을 `COMPOSE_POST_MUTATION_CONTRACT_FAILURE` typed 500으로 반환한다. 이때 config
경로와 `ensure` 모두 mutation 시작 시점의 원본 byte/mode를 먼저 원자 복원하고, 같은 raw/resolved hash와
system snapshot을 재검증한 뒤에만 baseline target runtime을 force-recreate한다. 복원·재검증 실패 시 Docker
recovery는 실행하지 않으며 복구 실패가 원래 계약 오류를 덮지 않는다. preflight drift 뒤 원본 원자 복원마저
실패해 candidate compose가 durable하게 남으면 409/no-mutation으로 축소하지 않고 같은 typed 500에
`config_restored=false`, `mutation_applied=true`를 기록한다.
Compose `wait`는 기본적으로 read-only지만 `--down-project`와
`--down-project=<bool>` 형식은 뒤에 특정 service가 있어도 project 전체 mutation으로 분류해 runtime-set guard와
같은 host lock을 적용한다. runtime `.Config.Env`
목록은 dict로 축약하지 않고 raw 순서로 검사하며 중복 이름을 거부한다. PinVi datetime은 날짜-only나
offset 없는 문자열을 거부하고 timezone offset이 있는 ISO 8601 값만 허용한다.

clean bootstrap result는 `init_results`를 항상 초기화하고 실제 init command가 예외를 내도 실패 결과로
흡수한다. 각 단계 전에 touched service를 기록하므로 이 경로를 포함한 모든 실패에서 transaction이 새로
만든 dependency/API만 제거하고 기존 container는 보존한다. signed Map smoke는 dataset-grid의 canonical
`execution_coverage`, typed `meta`, 각 dataset row의 identity/freshness/schedule/execution/catalog/policy/issue
shape를 검사하고 배열의 `null` 또는 잘못된 원소를 거부한다. PinVi smoke도 repository/asset/job/schedule/
sensor 배열 원소와 nullable datetime을 실제 admin DTO에 맞춰 깊게 검사한다. owned cancel 409/502/503은
fixture root member가 정확히 한 번 존재하고 UUID가 중복되지 않으며 unresolved count가 실제
`pending|cancel_failed` member 수와 같아야 한다. CAS 전이 중에는 0도 허용한다.
created service 제거 또는 기존 stopped service 복원 명령 자체가 예외를 내면 bootstrap 호출 밖으로
전파하지 않고 다섯 runtime halt 결과를 보존한 operator-required 상태로 끝낸다.

production compatible-pair preflight는 Map host bind port와 PinVi Map base URL을 각각 정확히 `12701`과
`http://127.0.0.1:12701`로 고정한다. 둘이 서로 일치하더라도 다른 포트면 첫 container mutation 전에
실패한다. local/development에서는 두 값을 동일하게 맞춘 비표준 포트를 허용한다.

Map capability smoke는 tokenless read의 typed 401뿐 아니라 cancel token의 GET/read, read token의 exact
import-job cancel, cancel token의 schedule mutation을 모두 typed 403 `OPS_SCOPE_FORBIDDEN`으로 확인한다.
올바른 cancel token의 exact path만 typed 404 domain boundary에 도달해야 한다. HTTP status와 RFC7807
`code`가 서로 다르면 실패한다.

PinVi owned cancel은 compatible-pair transaction마다 정확히 한 번만 POST한다. deploy/bootstrap의 첫
검증 결과를 final verification과 recovery에 재사용하며, 이미 요청했지만 결과를 검증하지 못한 경우에는
retryable/uncertain 상태를 바꿀 수 있는 두 번째 요청을 금지하고 fail-close한다. rollback과 그 recovery도
같은 transaction state를 공유한다. full cancel detail은 attempt datetime/error, member lifecycle,
`dagster_runs`, `committed_data_rolled_back=false`, warning을 실제 canonical DTO대로 검사한다. durable attempt가
아직 없는 409 `PIPELINE_CANCELLATION_IN_PROGRESS`만 exact `{root, cancellation: null}` shape를 허용한다.

full 409 attempt의 `unresolved_member_count`는 0을 허용하고 모든 member의 `pending|cancel_failed` 개수와
정확히 같아야 한다. owned root가 이미 resolved이고 child만 unresolved인 경우와 CAS/reconcile 중 잠시 모든
member가 resolved인 경우도 root identity와 member/run topology가 보존되면 canonical이다. retryable attempt의
`cancel_failed` member는 반드시 termination 대상 Dagster run에 결박되고, member와 matching run 모두
retryable structured error를 가진 exact `cancel_failed`여야 한다. 반면 in-progress/definitive CAS drift에서는
`cancel_failed` member와 이미 `cancelled`인 run 조합을 canonical transition으로 허용한다. definitive
attempt는 `409 PIPELINE_CANCELLATION_UNSAFE`+`failed`, timeout은
`503 DAGSTER_TERMINATION_TIMEOUT`+`retryable` pair로만 허용하고, root-only shape는
`409 PIPELINE_CANCELLATION_IN_PROGRESS`에만 한정한다.

in-progress runless `cancel_failed`는 definitive error code만 허용한다. run-backed `cancel_failed`의 run도
실패 snapshot이면 member와 retryable/definitive policy group이 같아야 한다. resolved run-backed member는
`cancelled↔CANCELED`, `done↔SUCCESS`, `failed↔FAILURE`를 정확히 맞춘다. 단,
`provider_feature_load_run`의 failed member와 SUCCESS run 조합은 동일 run에 초기 `done`이 아니었던
`provider_feature_load` child가 함께 있어 tracking failure를 입증할 때만 허용한다.

failed attempt의 top error는 definitive여야 하지만 member/run 증거는 frozen-base mismatch의 definitive
error와 exact run-backed retryable error가 함께 존재할 수 있다. attempt `status`는 `finished_at`/`error`의
DB lifecycle과 정확히 맞고, retry lineage는 self-reference를 금지하며 run-backed unresolved subset만 가진다.
member의 `requires_run_termination`은 frozen `initial_status`·`operation_kind`·Dagster run ID로 다시 계산해
일치해야 하고, run engine timestamp는 terminal result에서만 종료시각과 정렬되어야 한다.

`Retry-After`는 헤더 존재와 양의 정수 파싱 성공을 별도로 검사한다. retryable 502/503은 존재하는 양의
정수만 허용하고 non-retryable 409는 헤더 자체가 없어야 한다. 값은 공백·부호 없는 ASCII `[0-9]+` 중
1..300만 허용하며 Unicode digit, 0, 301 이상도 status와 무관하게 fail-close한다. low-level Compose
mutation parser는 `kill -s/--signal VALUE SERVICE`의 값을 service로
오인하지 않으며, service-less/project-wide·unknown command/option·option value 누락은 다섯 runtime 대상으로
default-deny한다.

Compose option은 command별 의미를 사용한다. `build --pull`, `run --rm`, `rm -s/--stop`은 값을 소비하지
않는 flag이고 `kill -s/--signal`만 다음 signal 값을 소비한다. `docker compose config -o/--output`은
resolved 설정을 파일에 쓰므로 분리·inline·값 누락 여부와 무관하게 mutation capability와 host lock을
요구한다. `config --format json`처럼 명시한 read-only option만 lock 없이 허용하며 unknown/incomplete
option은 runtime-set scope로 default-deny한다.

config/runtime 복원 실패 응답은 `returncode`, `stdout`, `stderr`, `error`를 `detail.restoration` 안에 그대로
보존한다. 미생성 container의 start fallback도 이 nested 복원 진단과 candidate command 결과를 REST 500까지
전달한다.

일반 non-API container config update/reset과 미생성 start-create뿐 아니라 generic ensure/up/create/recreate도
수정하거나 실행할 service만 검사하지 않는다. mutation 전에 raw와 Docker Compose resolved 문서의 전체 graph를
검사한다. 범위에는 모든 service 필드와 top-level `secrets`, `configs`, `x-*` extension, service의
secret/config mount·reference가 포함된다. 실제 존재하는 non-root `env_file`과 top-level secret/config 외부
파일 내용도 보호 이름·현재 값이 없는지 확인한다.

mutation source는 단일 canonical compose 파일이다. top-level `include`, service `extends`, process의
`COMPOSE_FILE`, `KOR_TRAVEL_DOCKER_MANAGER_OVERRIDE_FILE`, 실제 존재하는 `docker-compose.override.yml` 중
하나라도 있으면 resolution이나 Docker mutation 전에 fail-close한다. 운영에 필요한 prod 차이는 canonical
base compose의 명시적 environment 보간으로 합쳐야 한다. mutation command 자체도 original compose
directory를 `--project-directory`로 고정하고
`docker compose --env-file /dev/null --project-directory <canonical-directory> -f -`로 완전 해석된
compose JSON을 stdin에서 소비하며 override를 탐색하지 않는다.
transaction 시작 시 `.env`의 존재 여부·byte·device/inode/mode/uid/gid와 process env를 합친 effective
environment를 한 번만 snapshot한다. raw/resolved 검증과 Docker subprocess는 이 frozen mapping만 사용하며,
subprocess 직전에 `.env` 생성·삭제·내용·identity drift를 다시 확인한다. recovery도 최초 snapshot을 재사용하고
새 baseline을 만들지 않는다. snapshot의 값·원문 byte는 repr·오류·로그·hash에 노출하지 않는다. 같은 mutex
안에서 source byte와 include/extends/env/override 부재도 다시 확인하며, raw/resolved 계약을 별도 파일 합성
순서에 맡기지 않는다.

production mutation mutex는 compose project나 checkout별 state가 아니라 사용자별 단일 전역 경로를 사용한다.
local test process만 명시 override할 수 있고 production 값은 고정된다. lock 안에서 manifest 경로, root `.env`,
canonical compose byte/mode와 external `env_file` 입력을 한 번만 capture한다. `env_file`은 list의 exact
`{path, required, format}` mapping만 허용하며 각 regular file의 존재 여부·byte·device/inode/mode/uid/gid를
동결한다. Docker resolution에는 동결한 byte를 익명 fd로만 제공하고 외부 secret/config file source는 지원하지
않는다. deploy/capture/rollback의 stage, verification, recovery/halt는 모두 같은 transaction snapshot을 사용한다.
첫 mutation 이후 source나 외부 입력 계약이 바뀌면 원래 계약 오류와 recovery 결과를 typed post-mutation 오류로
보존하고, 검증되지 않은 pair를 계속 진행하지 않는다.
복구/halt만 frozen resolved transaction을 사용해 live env/source 재검증을 생략하며, config forward는 exact
candidate transaction, 원본 파일·runtime 복원은 persisted baseline transaction만 사용한다.
pair deploy/rollback과 bootstrap capture는 첫 mutation 전에 manifest active immutable image SHA를 root frozen
입력으로 해석한 별도 recovery transaction을 만들며, forward는 계속 root/candidate transaction을 사용한다.

Map API read/cancel/required 및 PinVi API 대응 read/cancel key는 source 이름뿐 아니라 default/required suffix와
메시지까지 고정된 canonical raw 표현만 허용한다. `env_file`과 외부 파일 경로의 `$VAR`, `${VAR}`, `:-`, `-`,
`:?`, `?`, `:+`, `+`, `$$`를 Compose 의미로 해석하고, 중첩·미완성·지원하지 않는 표현은 fail-close한다.
그 밖의 environment key/value, label, command, build arg, 외부 reference가 ops 또는 manager-only 이름이나 현재
보호값을 참조하면 `COMPOSE_CANDIDATE_PROTECTED_REFERENCE`로 거부한다. raw와 resolved 검증이 모두 끝나기 전에는
compose 파일 또는 container를 변경하는 Docker mutation subprocess를 실행하지 않으며 REST 409 detail에
`stage=candidate_validation`, `mutation_applied=false`를 남긴다.

service `volumes`는 short syntax(`source:target[:mode]`)와 long syntax(`type: bind`)를 모두 source 보간 뒤
compose 파일 디렉터리 기준 canonical path로 해석한다. symlink와 `..`도 최종 실제 경로로 비교하므로 manager
루트 `.env`, compatible-pair manifest/lock, 보호 이름·현재 값이 든 regular file은 read-only mount여도
거부한다. Windows drive/UNC처럼 Linux에서 안전하게 canonicalize할 수 없는 source는 fail-close한다. 반면
Compose 규칙상 named volume인 source는 host file로 읽지 않으며 기존 전체 graph의 보호 이름/reference 검사는
계속 적용한다. top-level secret/config가 `external: true`이거나 내용 없는 external `name`만 제공하면 manager가
내용을 검증할 수 없으므로 service reference를 허용하지 않는다. 현재 canonical API wiring에도 external
secret/config mount 허용 항목은 없다.

기존 DB/RustFS/Geo/Prometheus/Grafana data와 init script처럼 운영자가 관리하는 canonical bind는 현재 persisted
compose의 source/target/access mode가 그대로인 경우에만 유지한다. config API는 top-level `volumes` 정의와 모든
service `volumes` reference를 raw 문서와 Docker-resolved 문서에서 각각 정규화하고, mutex 안에서 persisted
compose의 두 hash와 모두 exact 비교한다. add/remove/source/target/type/mode
변경은 기능상 지원하지 않으며 `COMPOSE_CANDIDATE_PROTECTED_REFERENCE` 409,
`mutation_applied=false`로 끝난다. UI/API 사용자는 `volumes`에 현재 값을 그대로 보내야 하며 env/port/network만
변경할 수 있다. 이 불변 경계 덕분에 운영 데이터 이전 없이 request가 임의 mutable host path를 새로 주입하거나
Docker의 missing-source directory 자동 생성을 유도할 수 없다.

새 named volume은 top-level internal/default 정의와 일치하는 service reference만 허용한다. `local` driver의
non-empty `driver_opts`(`type`, `o=bind|rbind`, `device` 포함), 알 수 없는 driver/option, raw의 명시적 `name` 또는
`external` key, 미선언 service reference는 fail-close한다. resolved volume `name`은 exact
`<canonical-project>_<top-level-alias>`여야 하고 project name을 확정할 수 없으면 named volume 전체를 거부한다.
기존 operator bind도 manager
`.env`·state file의 ancestor 또는 host root를 노출할 수 없고, regular file은 1 MiB 이하 UTF-8 내용 검사를
통과해야 한다.

cAdvisor system bind는 raw literal과 resolved identity 모두 정확히 두 개의 set, 즉 RO `/sys -> /sys`와
RO `/var/run/docker.sock -> /var/run/docker.sock`만 허용한다. named/anonymous/추가 bind, writable mode,
source/target interpolation alias는 모두 거부한다. `/sys`는 root-owned mountpoint/directory이고 source와 parent chain이 group/other-writable이 아니어야
한다. Docker socket은 실제 socket, uid 0, `docker` group, mode `0660`, other-write 금지 계약을 강제한다.
source와 parent chain의 canonical path·inode·device·mode·uid/gid snapshot은 같은 manager mutex 안에서 capture해
compose write 직전과 각 Docker subprocess 직전에 재검증한다. 변경되면 write 전에 중단하거나 이미 쓴 compose
byte를 원자 복원한다. `docker` group 구성원은 Docker daemon을 통해 root-equivalent 권한을 가진다는 기존 Linux
위협 모델에 포함하며, root 또는 동일 권한의 privileged host actor가 mutex 밖에서 filesystem을 교체하는 공격은
이 경계의 보호 대상이 아니다. Docker socket을 `0600`으로 바꾸는 별도 운영 전제는 두지 않는다.

cAdvisor는 더 이상 `/:/rootfs`, `/var/run`, `/var/lib/docker`, `/dev/disk`를 mount하지 않는다.
`--docker_only=true`와 read-only `/var/run/docker.sock`, `/sys`만 사용해 container CPU·memory·I/O 지표를
노출한다. host root filesystem/disk inventory는 제공하지 않지만 manager 대시보드의 Docker SDK 기반 container
상태·stats와 Prometheus의 container metric 수집은 유지한다.
