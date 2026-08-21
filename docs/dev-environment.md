# DEVELOPMENT ENVIRONMENT — 개발 환경 셋업

이 문서는 `kor-travel-docker-manager`를 로컬에서 구동하고 개발하기 위한 가이드를 다룬다.

---

## 1. 요구 사항

- **OS**: Linux 또는 WSL을 포함한 Linux shell (Windows에서는 WSL을 사용)
- **Runtime**:
  - Python 3.11 이상
  - Node.js 20 LTS (npm)
  - Poetry (Python 의존성 및 패키지 관리용)
- **Docker**: Linux Docker daemon과 현재 사용자가 Docker socket에 접근할 수 있어야 함. Windows 호스트에서 WSL을 사용할 때는 Docker Desktop의 WSL integration을 선택적으로 사용할 수 있음.

### 1.1 명령 실행 위치 강제

이 저장소의 기본 개발 환경은 WSL을 포함한 Linux shell이다. 개발, 검증, 서버, Docker, 버전 관리, CodeGraph 작업은 모두 Linux에서 실행한다.

| 실행 위치 | 실행 대상 |
|---|---|
| Linux/WSL | `git`, `codegraph`, `python`, `poetry`, `pip`, `node`, `npm`, `docker`, `docker compose`, `ktdctl`, `ruff`, `pytest`, `npm run type-check`, `npm run build`, 서버 실행, 파일 검색 |
| n150 Linux | Playwright E2E 우선 실행 (`npx playwright test`, Playwright browser install 포함) |
| Windows 호스트 | n150에서 Playwright E2E 실행이 불가능할 때의 예외 실행 |

Windows 경로 `F:\dev\kor-travel-docker-manager`는 WSL에서 `/mnt/f/dev/kor-travel-docker-manager`로 접근한다. 문서 예시가 Windows 경로를 보여 주면 Linux 경로로 변환해 실행한다.

---

## 2. 백엔드 개발 환경 구축 (FastAPI)

백엔드는 `backend` 디렉토리에 위치한다.

### 2.1 의존성 설치
Poetry를 사용해 패키지를 설치하고 가상환경을 활성화한다.

```bash
cd /mnt/f/dev/kor-travel-docker-manager
cd backend
poetry install
```

설치 후 `ktdctl` CLI를 사용할 수 있다.

```bash
poetry run ktdctl targets
poetry run ktdctl srv --build
poetry run ktdctl status srv
```

### 2.2 환경 변수 설정
저장소 루트 `.env` 파일을 만들고 필요한 값을 정의한다. Compose와 백엔드는 루트 `.env`를 읽으며, 다른 경로를 사용하려면 `KOR_TRAVEL_DOCKER_MANAGER_ENV_FILE`을 지정한다.

```env
# Linux/WSL에서 Docker socket을 명시할 때만 설정
DOCKER_HOST=unix:///var/run/docker.sock

# Kor Travel Geo 전용 PostgreSQL / PostGIS 접속 정보 (ADR-37)
# ⚠️ 5432로 두지 마라 — compose의 Geo PostgreSQL 포트 기본값이 `12500`이고
#    Geo API/Dagster DSN은 명시 설정이 필요하다. 나머지 셋은 12600/12700/12800이다.
KOR_TRAVEL_GEO_DB_PORT=12500
KOR_TRAVEL_GEO_POSTGRES_USER=addr
KOR_TRAVEL_GEO_POSTGRES_PASSWORD=change-me-geo-postgres-password
KOR_TRAVEL_GEO_POSTGRES_DB=kor_travel_geo
KOR_TRAVEL_GEO_STRICT_SOURCE_CHECK=1
```

### 2.2.1 반드시 있어야 하는 값 (없으면 compose 전체가 죽는다)

아래 네 값은 compose의 **secret 정의**가 참조하므로 `.env`에 없으면 기본값으로 떨어지지
않고 **모든 `docker compose` 명령이 즉시 실패한다.** manager backend의
`docker compose ps` 상태 조회도 같이 죽어서, 증상이 "대시보드의 모든 target이 안 보임"
으로 나타난다 — DB 문제처럼 보이지 않는다.

| 변수 | 쓰는 곳 |
|---|---|
| `KOR_TRAVEL_GEO_POSTGRES_PASSWORD` | `kor-travel-geo-postgres` superuser (secret file) |
| `KOR_TRAVEL_MAP_POSTGRES_PASSWORD` | `kor-travel-map-postgres` superuser (secret file) |
| `KOR_TRAVEL_CONCIERGE_POSTGRES_PASSWORD` | `kor-travel-concierge-postgres` superuser + db-init |
| `PINVI_POSTGRES_PASSWORD` | `pinvi-postgres` superuser + db-init |
| `KOR_TRAVEL_GEO_DOCKER_PG_DSN` | Geo API와 Geo Dagster의 `kor_travel_geo` 접속 DSN (명시적 설정 필수) |
| `KOR_TRAVEL_GEO_DAGSTER_PG_URL` | Geo Dagster metadata DB 접속 URL (명시적 설정 필수) |

두 번째 차단도 있다. sanctioned 배포(`c6c_deployment.py`)는 secret의 `environment`
이름이 해결되지 않으면 mutation **전에**
`compose candidate secrets.<alias> environment is unresolved`로 거부한다. 오류 문자열에
어느 변수인지가 없어서 원인이 드러나지 않는다.

```bash
# 배포 전 확인. 값은 찍지 않는다.
for v in KOR_TRAVEL_GEO_POSTGRES_PASSWORD KOR_TRAVEL_MAP_POSTGRES_PASSWORD KOR_TRAVEL_CONCIERGE_POSTGRES_PASSWORD PINVI_POSTGRES_PASSWORD KOR_TRAVEL_GEO_DOCKER_PG_DSN KOR_TRAVEL_GEO_DAGSTER_PG_URL; do
  printf '%-46s ' "$v"; grep -q "^$v=" .env && echo SET || echo 'MISSING  <- compose가 죽는다'
done
```

`.env`는 권한 **600**이다. 백업본을 만들면 그것도 600으로 맞춘다 — 규정이 원본 이름만
지목하면 파생물이 통째로 빠져나간다(#179).

RustFS host 포트는 `storage` 대역을 사용한다. 기본값은 S3 API `12101`, console `12105`이다. 관측 target은 Grafana `12205`, cAdvisor `12301`, Prometheus `12401`을 사용하며, `kor-travel-geo`는 API `12501`, Web UI `12505`를 사용한다. `kor-travel-concierge`는 `12601`/`12602`/`12605`, `kor-travel-map`은 `12701`/`12702`/`12705`, PinVi는 `12801`/`12805`를 사용한다. PostgreSQL은 프로젝트마다 전용 instance이고 포트는 각 대역의 `x00`이다 — `12500`/`12600`/`12700`/`12800`(ADR-37). 전체 포트 정책은 `docs/ports.md`를 기준으로 한다.

### 2.3 로컬 개발 서버 실행
Poetry를 사용할 경우:
```bash
poetry run uvicorn kor_travel_docker_manager.main:app --host 0.0.0.0 --port 12901 --reload
```

Poetry 없이 수동으로 생성한 가상환경(`ktd_venv`)을 사용할 경우:
```bash
PYTHONPATH=src ktd_venv/bin/python -m uvicorn kor_travel_docker_manager.main:app --host 0.0.0.0 --port 12901 --reload
```
실행 후 `http://localhost:12901/docs`에서 OpenAPI 대화식 문서를 확인할 수 있다.

> [!IMPORTANT]
> WSL2 내부에서 백엔드를 실행하는 경우, 호스트 Windows 브라우저에서 WSL 가상 IP(예: `172.26.51.35`)로 직접 통신하면 방화벽 필터링 장치 등으로 인해 접속 연결이 거부되는 현상이 빈번히 발생합니다.
> 따라서 프론트엔드 환경변수 및 API 접속 주소는 항상 `http://localhost:12901`을 활용하여 WSL2 localhost 포트 포워딩을 통해 접근하십시오.

---

## 3. 의존 Docker 실행

다른 Kor Travel/PinVi 개발 저장소에서 DB 또는 RustFS가 필요할 때는 manager CLI로 바로 실행한다.

```bash
cd /mnt/f/dev/kor-travel-docker-manager/backend
poetry run ktdctl srv --build
```

> [!NOTE]
> dev 기본 Docker 네트워크는 host 모드(`KTDM_DOCKER_NETWORK_MODE=host`)다. 포트 NAT가 없으므로 각 컨테이너가 호스트 정규 포트에 직접 바인딩하고(컨테이너 내부 포트 = 호스트 포트), 서비스 간 참조는 `127.0.0.1:<포트>`를 사용한다. host networking을 지원하지 않는 Docker 엔진에서는 `KTDM_DOCKER_NETWORK_MODE=bridge`로 바꾼 뒤 서비스 간 hostname을 컨테이너명으로 복원해야 한다.

공식 target은 `db`, `storage`, `gra`, `cadv`, `prom`, `geo`, `conc`, `map`, `pinvi`, `all`이다. `srv`와 `main`은 `pinvi`, `default`는 `all`을 가리키는 별칭이다. `pinvi` target은 PinVi API/Dagster(`pinvi-dagster`, 12802)/Web을 포함한다. 의존 순서는 `config/docker-targets.yml`에서 읽으며 실제 실행 범위는 `depends_on` DAG의 전이 폐포다. 예를 들어 `ktdctl conc --build`는 `geo` 없이 Concierge 전용 PostgreSQL과 API/MCP/Scheduler/Web UI를 실행하고, `ktdctl map --build`는 Geo·Concierge·Map의 PostgreSQL과 앱 runtime을 실행한다.

추가 target 이름으로 `postgresql`, `rustfs`, `grafana`, `cadvisor`, `prometheus`, `kor-travel-geo`, `kor-travel-map`, `python-krtour-map`, `kor-travel-concierge`, `srv`, `pinvi`, `main`도 사용할 수 있다.

`geo` target과 Geo를 의존성으로 포함하는 `map`, `pinvi`, `all` target은 `/data/juso` 마운트와
`kor_travel_geo` 핵심 테이블 적재 상태를 확인한다. 의도적으로 빈 DB를 다루는 경우에만 `.env`에서
`KOR_TRAVEL_GEO_STRICT_SOURCE_CHECK=0`으로 낮춘다. `conc` target은 Geo에 의존하지 않는다.

---

## 4. 프론트엔드 개발 환경 구축 (Next.js)

프론트엔드는 `frontend` 디렉토리에 위치한다.

### 4.1 의존성 설치
npm을 사용해 필요한 Node 패키지들을 설치한다.

```bash
cd /mnt/f/dev/kor-travel-docker-manager
cd frontend
npm install
```

### 4.2 로컬 개발 서버 실행
```bash
npm run dev
```
기본적으로 `http://localhost:12905`에서 대시보드가 로드되며, 백엔드 서버(`http://127.0.0.1:12901`)에 자동으로 API를 요청한다.

---

## 4.3 운영(prod) 공개 주소 설정

운영 환경에서 매니저 백엔드 API와 대시보드는 각각 별도 공개 도메인으로 노출된다. 실제 도메인은 저장소에 커밋하지 않고 **gitignore된 env 파일에만** 둔다. TLS 종단과 라우팅, WebSocket 업그레이드는 앱 바깥의 리버스 프록시가 담당한다(저장소에 프록시 설정은 포함하지 않음).

| 대상 | 주입 위치(gitignore) | 변수 | 비고 |
|---|---|---|---|
| 대시보드 → 백엔드 API 주소 | `frontend/.env.production` | `NEXT_PUBLIC_BACKEND_URL` | `https://<api-domain>`. `wss://`는 `http→ws` 치환으로 자동 파생 |
| 백엔드 CORS 허용 Origin | 루트 `.env` | `KTDM_CORS_ALLOW_ORIGINS` | 대시보드 공개 Origin. 콤마로 여러 개. 미설정/`*`이면 localhost 두 Origin만 허용 |

- 계약(placeholder)은 `frontend/.env.example`, 루트 `.env.example`에 문서화되어 있다. 실제 도메인은 채워 넣지 않는다.
- `NEXT_PUBLIC_*`은 **빌드 타임에 번들로 인라인**되므로 운영 호스트에서 `npm run build`를 다시 수행해야 주소가 반영된다.
- **Next.js 환경파일 우선순위 주의**: `.env.local`이 `.env.production`을 덮어쓴다. 개발 기본값은 `frontend/.env.development`(localhost), 운영 값은 `frontend/.env.production`에 두고, `.env.local`에는 `NEXT_PUBLIC_BACKEND_URL`을 두지 않는다.
- 백엔드는 기동 시 루트 `.env`(또는 `KOR_TRAVEL_DOCKER_MANAGER_ENV_FILE` 경로)를 로드해 `KTDM_CORS_ALLOW_ORIGINS`를 적용한다. 개발에서는 미설정이면 `http://localhost:12905`와 `http://127.0.0.1:12905`만 허용한다.

---

## 5. 에이전트(Agent) 작업 가이드

새로운 기능을 구현하기 위해 AI 에이전트 세션을 실행할 때는 다음 흐름을 따른다:

1. **에이전트 고정 worktree 진입**:
   - ChatGPT Codex: `F:\dev\kor-travel-docker-manager-codex`
   - Claude Code: `F:\dev\kor-travel-docker-manager-claude`
   - Google Antigravity: `F:\dev\kor-travel-docker-manager-antigravity`
2. **코드 갱신 및 브랜치 작성**:
   Linux shell에서 실행한다.
   ```bash
   git fetch origin
   git switch -c agent/<topic> main
   ```
3. **CodeGraph 인덱스 동기화**:
   Linux shell에서 실행한다.
   ```bash
   codegraph sync
   codegraph status
   ```
4. **로컬 품질 게이트 확인**:
   WSL에서 실행한다.
   - 백엔드: `poetry run ruff check .` 및 `poetry run pytest`
   - 프론트엔드: `npm run type-check` 및 `npm run build`
5. **Playwright E2E 확인**:
   우선 n150 Linux 운영 환경에서 실행한다. n150에서 브라우저/그래픽/권한 문제로 실행할 수 없을 때만 Windows 호스트에서 예외 실행하고, 예외 사유를 작업 기록에 남긴다.
   ```bash
   npx playwright test
   ```
