# ARCHITECTURE — Kor Travel Docker Manager 아키텍처

이 문서는 `kor-travel-docker-manager`의 시스템 아키텍처와 컴포넌트 간 데이터 흐름을 다룬다.

---

## 1. 개요

`kor-travel-docker-manager`는 Kor Travel/PinVi 계열 서비스를 구동하기 위한 통합 PostgreSQL/PostGIS, RustFS, `kor-travel-geo`, `kor-travel-concierge`, `kor-travel-map`, PinVi Docker 컨테이너의 구동 상태를 모니터링하고 제어하는 시스템이다.

```mermaid
graph TD
    subgraph Frontend [Next.js Dashboard Web]
        UI[Dashboard UI / Status & Controls]
        TQ[TanStack Query / API Polling]
        UI --> TQ
    end

    subgraph Backend [FastAPI Service]
        API[FastAPI Endpoints /api/containers, /api/targets]
        REG[Target Registry / config/docker-targets.yml]
        DS[DockerService Wrapper]
        CS[ComposeService Runner]
        API --> REG
        API --> DS
        API --> CS
    end

    subgraph CLI [Python CLI]
        CLI_CMD[ktdctl db/storage/gra/cadv/prom/geo/conc/map/pinvi/srv]
        CLI_CMD --> REG
        CLI_CMD --> DS
        CLI_CMD --> CS
    end

    subgraph Infrastructure [Docker Daemon / Host]
        D_Sock[docker.sock / Named Pipe]
        C_PG[kor-travel-geo-postgres/통합 PostgreSQL]
        C_RFS[RustFS Container]
        C_GEO_API[kor-travel-geo-api-latest/kor-travel-geo API]
        C_GEO_UI[kor-travel-geo-ui-latest/kor-travel-geo Web UI]
        C_CONC[kor-travel-concierge-*/Concierge]
        C_MAP[kor-travel-map-*/Map]
        C_PINVI[pinvi-*/PinVi]
        C_PROM[kor-travel-prometheus/Prometheus]
        C_GRAF[kor-travel-grafana/Grafana]
        C_EXP[kor-travel-cadvisor/cAdvisor Exporter]
        
        DS -->|API Calls / Controls| D_Sock
        D_Sock -->|Manage| C_PG
        D_Sock -->|Manage| C_RFS
        D_Sock -->|Manage| C_GEO_API
        D_Sock -->|Manage| C_GEO_UI
        D_Sock -->|Manage| C_CONC
        D_Sock -->|Manage| C_MAP
        D_Sock -->|Manage| C_PINVI
        D_Sock -->|Manage| C_PROM
        D_Sock -->|Manage| C_GRAF
        D_Sock -->|Manage| C_EXP
        C_PROM -->|Scrape| C_EXP
        C_GRAF -->|Datasource| C_PROM
    end
    
    TQ -->|HTTP requests| API
```

---

## 2. 백엔드 설계 (Python FastAPI)

백엔드는 가볍고 빠른 API 서빙을 위해 Python FastAPI를 채택한다. 로컬/원격 Docker 데몬과의 통신은 `docker` Python SDK를 사용한다.

### 2.1 Docker 데몬 연동 (`DockerService`)
- **연동 방식**: `docker.from_env()`를 호출하여 환경변수 및 기본 소켓 경로를 참조해 Docker 클라이언트를 초기화한다.
- **Windows 호스트**: 명명된 파이프 (`npipe:////./pipe/docker_engine`)를 통해 Docker Desktop 데몬과 통신한다.
- **Linux/WSL**: 유닉스 소켓 (`unix:///var/run/docker.sock`)을 통해 통신한다.
- **예외 처리**: Docker 데몬이 구동 중이지 않거나 권한이 없을 경우를 대비해, API 응답 시 503 Service Unavailable 및 정형화된 에러 객체를 반환하도록 설계한다.
- **사용 범위**: 컨테이너 상태, metrics, logs, inspect, 개별 action은 Docker SDK로 수행한다.

### 2.2 Compose 실행 (`ComposeService`)
- **역할**: 개발환경에서 의존 Docker를 앱 관점 target으로 실행한다.
- **실행 방식**: `docker compose`를 문자열 shell이 아닌 인자 배열로 실행한다.
- **지원 옵션**: `ensure`에서 `--build`, `--force-recreate`를 전달할 수 있다.
- **공유 target**: API와 Python CLI가 같은 registry(`db`, `storage`, `gra`, `cadv`, `prom`, `geo`, `conc`, `map`, `pinvi`, `all`)를 사용한다.
- **설정 파일**: target 정의, alias, 의존 순서, 초기화 단계는 `config/docker-targets.yml`에서 읽는다.
- **의존 순서**: 기본 순서는 `db -> storage -> gra -> cadv -> prom -> geo -> conc -> map -> pinvi`이며, 각 target은 자기 앞 단계까지 누적 실행한다.
- **초기화 단계**: `db`는 database/role/schema 복구, `storage`는 RustFS bucket 복구, `geo`는 `kor-travel-geo` API/Web UI 실행과 원천 DB 적재 검증을 수행한다.

### 2.3 API 엔드포인트 설계
- `GET /api/v1/targets`: 앱 관점 target 목록 반환.
- `POST /api/v1/targets/{target}/ensure`: target에 필요한 Docker 서비스를 실행. 개발환경에서는 `build=true`로 `docker compose up -d --build`를 수행.
- `GET /api/v1/containers`: 관리 대상 컨테이너의 상태, 포트, compose 설정, CPU/메모리/I/O 최신값 반환.
- `GET /api/v1/containers/{container_id}/inspect`: Docker inspect 핵심 정보를 secret redaction 후 반환.
- `POST /api/v1/containers/{container_id}/action`: 컨테이너 제어 명령 (`start`, `stop`, `restart`) 실행.
- `POST /api/v1/containers/{container_id}/config`: compose 파라미터 저장 후 컨테이너 재생성.
- `GET /api/v1/containers/{container_id}/logs`: 최근 100라인의 stdout/stderr 컨테이너 로그 반환.

---

## 3. 프론트엔드 설계 (Next.js & React)

프론트엔드는 Next.js 14+ App Router를 기반으로 구성하며, 실시간 대시보드 성격의 단일 페이지 애플리케이션(SPA) 형태로 운영한다.

### 3.1 상태 관리 및 데이터 동기화
- **TanStack Query (React Query)**: 백엔드 API와의 통신 및 캐싱을 전담한다. 대시보드 상태를 유지하기 위해 5초 단위의 폴링(`refetchInterval: 5000`)을 적용하여 인프라 상태 변화를 실시간으로 대시보드에 반영한다.
- **Zod & React Hook Form**: 컨테이너의 설정(예: 포트 번호, 환경변수, 데이터 볼륨 경로) 변경 양식을 안전하게 검증하고 전송한다.

### 3.2 UI/UX 디자인 시스템
- **관리 대시보드 우선**: 마케팅 hero나 장식 이미지를 배제하고, 상태 표·액션 버튼·상세 패널을 첫 화면의 중심에 둔다.
- **시각 양식**: Pure Black canvas, 1px hairline border, 직각 panel, 절제된 M 삼색선 divider를 적용한다.
- **상태 인디케이터**: 컨테이너 상태는 색상 점, 텍스트, 아이콘을 함께 사용해 빠르게 스캔할 수 있게 한다.
- **상세 패널**: inspect, mounts, networks, redacted env, 최근 로그, 최근 메트릭을 한 화면에서 확인할 수 있게 확장한다.

---

## 4. 데이터베이스 및 파일 스토리지 (대상 인프라)

`kor-travel-docker-manager`가 관리하는 Docker 컨테이너 정의는 다음과 같다.

1. **Kor Travel 통합 PostgreSQL / PostGIS**:
   - 컨테이너: `kor-travel-geo-postgres`
   - 이미지: `postgis/postgis:16-3.5`
   - 목적: `kor_travel_geo`, `pinvi`, `kor_travel_concierge`, `krtour_map` database를 하나의 공용 PostgreSQL/PostGIS 컨테이너에서 구동.
   - 내부 포트: `5432` / 외부 노출 포트: `5432`.
   - 기본 DSN: `postgresql+psycopg://addr:addr@localhost:5432/kor_travel_geo`.
   - 기본 pgdata: `KOR_TRAVEL_GEO_PGDATA=/home/digitie/kor-travel-geo-data/pgdata-final-20260529`.
2. **RustFS**:
   - 컨테이너: `kor-travel-rustfs`
   - 이미지: `rustfs/rustfs:latest`
   - 목적: 미디어 자원과 `kor-travel-geo`, `kor-travel-concierge`, `kor-travel-map`, PinVi 원천·업로드 데이터 보관을 위한 공용 S3 호환 오브젝트 스토리지.
   - host 포트: `12101` (S3 API), `12105` (어드민 콘솔).
   - 컨테이너 내부 포트: `9000` (S3 API), `9001` (어드민 콘솔).
   - 기본 credential: `RUSTFS_ACCESS_KEY=rustfsadmin`, `RUSTFS_SECRET_KEY=rustfsadmin`.
   - 기본 bucket: `pinvi-media`, `kor-travel-geo`, `kor-travel-concierge`, `krtour-map`, `krtour-uploads`.
3. **Grafana**:
   - 컨테이너: `kor-travel-grafana`
   - compose service: `grafana`
   - 목적: Prometheus datasource 기반 공용 메트릭 시각화.
   - host 포트: `12205`.
   - 컨테이너 내부 포트: `3000`.
4. **cAdvisor Exporter**:
   - 컨테이너: `kor-travel-cadvisor`
   - compose service: `cadvisor`
   - 목적: Docker 컨테이너 CPU, memory, filesystem, network 메트릭을 Prometheus 형식으로 노출.
   - `--docker_only=true`와 read-only Docker socket·`/sys`만 사용하며 host root·Docker data
     directory는 mount하지 않음.
   - socket은 root:docker `0660`, `/sys`는 root-owned mountpoint 계약과 inode/device/mode 재검증을 통과해야 함.
   - host 포트: `12301`.
   - 컨테이너 내부 포트: `8080`.
5. **Prometheus**:
   - 컨테이너: `kor-travel-prometheus`
   - compose service: `prometheus`
   - 목적: cAdvisor Exporter와 앱 메트릭 수집 및 저장.
   - host 포트: `12401`.
   - 컨테이너 내부 포트: `9090`.
6. **kor-travel-geo API**:
   - 컨테이너: `kor-travel-geo-api-latest`
   - compose service: `kor-travel-geo-api`
   - 목적: 지오코딩/리버스 지오코딩 REST API 제공.
   - host 포트: `12501`.
   - 컨테이너 내부 포트: `12501`.
   - 내부 의존성: `kor-travel-geo-postgres:5432`, `rustfs:9000`.
   - 기본 source data mount: `KOR_TRAVEL_GEO_APP_DATA_DIR=/mnt/f/dev/kor-travel-geo/data` -> `/data:ro`.
7. **kor-travel-geo Web UI**:
   - 컨테이너: `kor-travel-geo-ui-latest`
   - compose service: `kor-travel-geo-ui`
   - 목적: `kor-travel-geo` admin Web UI 제공.
   - host 포트: `12505`.
   - 컨테이너 내부 포트: `12505`.
   - 내부 API URL: `http://kor-travel-geo-api:12501`.
8. **kor-travel-concierge API / MCP / Scheduler / Web UI**:
   - 컨테이너: `kor-travel-concierge-api-latest`, `kor-travel-concierge-mcp-latest`, `kor-travel-concierge-scheduler-latest`, `kor-travel-concierge-ui-latest`
   - compose service: `kor-travel-concierge-api`, `kor-travel-concierge-mcp`, `kor-travel-concierge-scheduler`, `kor-travel-concierge-ui`
   - 목적: 여행 concierge provider, MCP HTTP, scheduler, Web UI 제공.
   - host 포트: API `12601`, MCP `12602`, Web UI `12605`.
   - 내부 의존성: `kor-travel-geo-postgres:5432`, `rustfs:9000`, `kor-travel-geo-api:12501`.
9. **kor-travel-map API / Dagster / Web UI**:
   - 컨테이너: `kor-travel-map-api-latest`, `kor-travel-map-dagster-latest`, `kor-travel-map-dagster-daemon-latest`, `kor-travel-map-ui-latest`
   - compose service: `kor-travel-map-api`, `kor-travel-map-dagster`, `kor-travel-map-dagster-daemon`, `kor-travel-map-ui`
   - 목적: 지도 feature admin API, Dagster workflow, admin Web UI 제공.
   - host 포트: API `12701`, Dagster `12702`, Web UI `12705`.
   - 내부 의존성: `kor-travel-geo-postgres:5432`, `rustfs:9000`, `kor-travel-concierge-api:8000`.
10. **PinVi API / Web UI**:
   - 컨테이너: `pinvi-api-latest`, `pinvi-web-latest`
   - compose service: `pinvi-api`, `pinvi-web`
   - 목적: PinVi 서비스 API와 Web UI 제공.
   - host 포트: API `12801`, Web UI `12805`.
   - 내부 의존성: host network의 `127.0.0.1:5432`, `127.0.0.1:12101`,
     `127.0.0.1:${KOR_TRAVEL_MAP_API_CONTAINER_PORT:-12701}`.
   - worker 수: PinVi 실시간 WebSocket broadcast broker는 shared broker 도입 전까지 process-local이므로 `PINVI_API_WORKERS=1`을 기본값으로 둔다. worker를 2 이상으로 올리려면 PinVi 쪽 broadcast broker가 프로세스 간 전달을 지원해야 한다.
   - public URL/CORS: dev 기본값은 `http://127.0.0.1:12801`/로컬 Web origin이며, prod에서는 gitignore된 `.env`의 `PINVI_PUBLIC_API_URL`과 `PINVI_CORS_ALLOWED_ORIGINS`로 공개 API 주소와 Web origin을 주입한다.
   - C6c production: manager mode와 PinVi mode를 모두 `production`, Map의
     `KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED`를 `true`로 명시한다. production의 일반
     `ensure`/container action·config·reset/direct Compose 경로는 두 API를 변경할 수 없고,
     host-wide lock을 잡는 `pinvi-pair deploy`만 generation이 같은 Map+PinVi pair를 단계 기동한다.
     transaction은 두 API만 `--no-deps`로 변경하고 dependency·UI·Dagster는 변경 없이
     running/healthy를 요구한다. manifest가 없을 때만 `pinvi-pair capture`가 같은 lock
     안에서 candidate pair를 bootstrap하고 전체 계약 성공 뒤 최초 v3를 기록한다.
     v3는 두 immutable image ID와 clean source revision을 active/rollback 각 pair에 함께
     결박하며 provenance가 없는 v1/v2를 거부한다. 실패 시 두 API를 중지한다.
   - Manager mutation의 compose source는 단일 canonical 파일이다. mutex 안에서 persisted/request의
     raw·Docker-resolved volume graph를 각각 exact 비교하고 include/extends/override 합성을 거부한다.
     cAdvisor mount는 RO `/sys`와 Docker socket exact set만 허용한다. 첫 mutation 성공 뒤 후속 preflight
     drift가 발생하면 원래 오류를 보존한 typed 500과 함께 compose/runtime 복구 결과를 반환한다.

`kor-travel-geo`, `kor-travel-concierge`, `kor-travel-map`, PinVi는 더 이상 자체 저장소의 Docker compose 또는 RustFS 구동 스크립트로 PostgreSQL/RustFS 생명주기를 직접 관리하지 않는다. `geo`, `conc`, `map`, `pinvi` target은 각 앱 컨테이너를 manager에서 함께 빌드하고 실행한다. 로컬에서 해당 인프라를 실행하거나 재시작할 때는 이 저장소의 `ktdctl` CLI, 대시보드/API를 사용한다. 공식 CLI target은 `db`, `storage`, `gra`, `cadv`, `prom`, `geo`, `conc`, `map`, `pinvi`이며, `srv`와 `main`은 `pinvi`를 가리키는 별칭이다. `config/docker-targets.yml`에서 순서와 포함 서비스를 확장한다.

로컬 host 포트 정책은 `docs/ports.md`를 기준으로 한다. PostgreSQL은 표준 `5432`를 사용하고, RustFS는 `storage` 대역(`12100-12199`), Grafana는 `gra` 대역(`12200-12299`), cAdvisor는 `cadv` 대역(`12300-12399`), Prometheus는 `prom` 대역(`12400-12499`), `kor-travel-geo`는 `geo` 대역(`12500-12599`), `kor-travel-concierge`는 `conc` 대역(`12600-12699`), `kor-travel-map`은 `map` 대역(`12700-12799`), PinVi는 `pinvi` 대역(`12800-12899`), `kor-travel-docker-manager` 자체 API/Web은 `12900-12999` 대역을 사용한다.
