# DEVELOPMENT ENVIRONMENT — 개발 환경 셋업

이 문서는 `kor-travel-docker-manager`를 로컬에서 구동하고 개발하기 위한 가이드를 다룬다.

---

## 1. 요구 사항

- **OS**: Windows 10/11 (Docker Desktop 설치 필수)
- **Runtime**:
  - Python 3.11 이상
  - Node.js 20 LTS (npm)
  - Poetry (Python 의존성 및 패키지 관리용)
- **Docker**:
  - Docker Desktop 구동 중이어야 하며, 백엔드가 로컬 Docker Named Pipe에 접근할 수 있어야 함.

### 1.1 명령 실행 위치 강제

이 저장소의 기본 개발 환경은 WSL이다. git과 Playwright E2E를 제외한 모든 개발/검증/서버/Docker 명령은 WSL에서 실행한다.

| 실행 위치 | 실행 대상 |
|---|---|
| WSL | `python`, `poetry`, `pip`, `node`, `npm`, `docker`, `docker compose`, `ktdctl`, `ruff`, `pytest`, `npm run type-check`, `npm run build`, 서버 실행, 파일 검색 |
| Windows 호스트 | `git` 전체, Playwright E2E (`npx playwright test`, Playwright browser install 포함) |

Windows 경로 `F:\dev\kor-travel-docker-manager`는 WSL에서 `/mnt/f/dev/kor-travel-docker-manager`로 접근한다. 문서 예시가 Windows 경로를 보여 주더라도 git과 Playwright E2E를 제외한 명령은 WSL 경로에서 실행한다.

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
`backend/.env` 파일을 만들고 필요한 값을 정의한다 (기본값이 설정되어 있으므로 개발 단계에서는 선택 사항).

```env
# Docker Named Pipe 경로 (Windows 기본값)
DOCKER_HOST=npipe:////./pipe/docker_engine
# Linux/WSL 사용 시:
# DOCKER_HOST=unix:///var/run/docker.sock

# 통합 PostgreSQL / PostGIS 접속 정보
KOR_TRAVEL_GEO_DB_PORT=5432
KOR_TRAVEL_GEO_POSTGRES_USER=addr
KOR_TRAVEL_GEO_POSTGRES_PASSWORD=addr
KOR_TRAVEL_GEO_POSTGRES_DB=kor_travel_geo
KOR_TRAVEL_GEO_STRICT_SOURCE_CHECK=1
```

RustFS host 포트는 `storage` 대역을 사용한다. 기본값은 S3 API `12101`, console `12105`이다. 관측 target은 Grafana `12205`, cAdvisor `12301`, Prometheus `12401`을 사용하며, `kor-travel-geo`는 API `12501`, Web UI `12505`를 사용한다. `kor-travel-concierge`는 `12601`/`12602`/`12605`, `kor-travel-map`은 `12701`/`12702`/`12705`, PinVi는 `12801`/`12805`를 사용한다. PostgreSQL은 표준 `5432`를 사용한다. 전체 포트 정책은 `docs/ports.md`를 기준으로 한다.

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

공식 target 별칭은 `db`, `storage`, `gra`, `cadv`, `prom`, `geo`, `conc`, `map`, `pinvi`이다. `srv`와 `main`은 `pinvi`를 가리키는 별칭이다. 의존 순서는 `config/docker-targets.yml`에서 읽으며 기본값은 `db -> storage -> gra -> cadv -> prom -> geo -> conc -> map -> pinvi`이다. 예를 들어 `ktdctl map --build`는 통합 DB, RustFS, 관측 스택, `kor-travel-geo`, `kor-travel-concierge`, `kor-travel-map` API/Dagster/Web UI 실행까지 수행한다.

추가 target 이름으로 `postgresql`, `rustfs`, `grafana`, `cadvisor`, `prometheus`, `kor-travel-geo`, `kor-travel-map`, `python-krtour-map`, `kor-travel-concierge`, `srv`, `pinvi`, `main`도 사용할 수 있다.

`geo` 이상 target은 `/data/juso` 마운트와 `kor_travel_geo` 핵심 테이블 적재 상태를 확인한다. 의도적으로 빈 DB를 다루는 경우에만 `.env`에서 `KOR_TRAVEL_GEO_STRICT_SOURCE_CHECK=0`으로 낮춘다.

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

## 5. 에이전트(Agent) 작업 가이드

새로운 기능을 구현하기 위해 AI 에이전트 세션을 실행할 때는 다음 흐름을 따른다:

1. **에이전트 고정 worktree 진입**:
   - ChatGPT Codex: `F:\dev\kor-travel-docker-manager-codex`
   - Claude Code: `F:\dev\kor-travel-docker-manager-claude`
   - Google Antigravity: `F:\dev\kor-travel-docker-manager-antigravity`
2. **코드 갱신 및 브랜치 작성**:
   Windows 호스트에서 실행한다.
   ```bash
   git fetch origin
   git switch -c agent/<topic> main
   ```
3. **CodeGraph 인덱스 동기화**:
   WSL에서 실행한다.
   ```bash
   codegraph sync
   codegraph status
   ```
4. **로컬 품질 게이트 확인**:
   WSL에서 실행한다.
   - 백엔드: `poetry run ruff check .` 및 `poetry run pytest`
   - 프론트엔드: `npm run type-check` 및 `npm run build`
5. **Playwright E2E 확인**:
   Windows 호스트에서 실행한다. Playwright E2E는 실제 Windows 브라우저 환경을 검증하는 예외 작업이므로 WSL에서 실행하지 않는다.
   ```bash
   npx playwright test
   ```
