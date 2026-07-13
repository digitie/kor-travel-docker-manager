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

- `KOR_TRAVEL_MAP_OPINET_API_KEY`: OpiNet station·price 수집용이다. base compose가 map API·
  Dagster·Dagster daemon에 같은 이름으로 명시 보간한다. API live preview의
  `KOR_TRAVEL_MAP_API_OPINET_SERVICE_KEY`는 별도 값을 우선하고 미설정 시 이 공통 key를 재사용한다.
- `KOR_TRAVEL_MAP_KREX_EX_API_KEY`: 교통 돌발·notice를 포함한 EX endpoint용이다. base compose가
  map API·Dagster·Dagster daemon에 같은 이름으로 명시 보간한다.
- `KOR_TRAVEL_MAP_KREX_GO_API_KEY`: data.go.kr 계열 KREX 수집용이다. 같은 세 서비스에 명시
  보간한다.
- `KOR_TRAVEL_MAP_API_KREX_SERVICE_KEY`: map API live preview가 별도 key를 써야 할 때만
  설정한다. 미설정 시 compose가 EX key를 재사용한다.

과거 `KRTOUR_MAP_*` 이름을 source로 쓰면 `.env`에 현재 이름의 key가 있어도 빈 문자열이
컨테이너로 전달된다. 따라서 override에 bare key나 secret literal을 반복하지 않는다. 변경 뒤에는
resolved config 전체를 출력하지 말고 `docker compose config --quiet`를 실행한 뒤, 한 프로세스
안에서 `.env`와 대상 컨테이너 값을 constant-time 비교해 `nonempty && all_equal` 여부만 확인한다.
실제 값·길이·digest는 로그에 남기지 않는다.
