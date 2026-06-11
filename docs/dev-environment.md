# DEVELOPMENT ENVIRONMENT — 개발 환경 셋업

이 문서는 `tripmate-manager`를 로컬에서 구동하고 개발하기 위한 가이드를 다룬다.

---

## 1. 요구 사항

- **OS**: Windows 10/11 (Docker Desktop 설치 필수)
- **Runtime**:
  - Python 3.11 이상
  - Node.js 20 LTS (npm)
  - Poetry (Python 의존성 및 패키지 관리용)
- **Docker**:
  - Docker Desktop 구동 중이어야 하며, 백엔드가 로컬 Docker Named Pipe에 접근할 수 있어야 함.

---

## 2. 백엔드 개발 환경 구축 (FastAPI)

백엔드는 `backend` 디렉토리에 위치한다.

### 2.1 의존성 설치
Poetry를 사용해 패키지를 설치하고 가상환경을 활성화한다.

```bash
cd backend
poetry install
```

### 2.2 환경 변수 설정
`backend/.env` 파일을 만들고 필요한 값을 정의한다 (기본값이 설정되어 있으므로 개발 단계에서는 선택 사항).

```env
# Docker Named Pipe 경로 (Windows 기본값)
DOCKER_HOST=npipe:////./pipe/docker_engine
# Linux/WSL 사용 시:
# DOCKER_HOST=unix:///var/run/docker.sock

# TripMate DB 접속 정보
POSTGRES_USER=tripmate
POSTGRES_PASSWORD=tripmate_dev_password
POSTGRES_DB=tripmate
```

### 2.3 로컬 개발 서버 실행
Poetry를 사용할 경우:
```bash
poetry run uvicorn src.tripmate_manager.main:app --host 0.0.0.0 --port 9091 --reload
```

Poetry 없이 수동으로 생성한 가상환경(`tripmate_venv`)을 사용할 경우 (WSL 권장):
```bash
PYTHONPATH=src tripmate_venv/bin/python -m uvicorn src.tripmate_manager.main:app --host 0.0.0.0 --port 9091 --reload
```
실행 후 `http://localhost:9091/docs`에서 OpenAPI 대화식 문서를 확인할 수 있다.

> [!IMPORTANT]
> WSL2 내부에서 백엔드를 실행하는 경우, 호스트 Windows 브라우저에서 WSL 가상 IP(예: `172.26.51.35`)로 직접 통신하면 방화벽 필터링 장치 등으로 인해 접속 연결이 거부되는 현상이 빈번히 발생합니다.
> 따라서 프론트엔드 환경변수 및 API 접속 주소는 항상 `http://localhost:9091`을 활용하여 WSL2 localhost 포트 포워딩을 통해 접근하십시오.

---

## 3. 프론트엔드 개발 환경 구축 (Next.js)

프론트엔드는 `frontend` 디렉토리에 위치한다.

### 3.1 의존성 설치
npm을 사용해 필요한 Node 패키지들을 설치한다.

```bash
cd frontend
npm install
```

### 3.2 로컬 개발 서버 실행
```bash
npm run dev
```
기본적으로 `http://localhost:9092`에서 대시보드가 로드되며, 백엔드 서버(`http://127.0.0.1:9091`)에 자동으로 API를 요청한다.

---

## 4. 에이전트(Agent) 작업 가이드

새로운 기능을 구현하기 위해 AI 에이전트 세션을 실행할 때는 다음 흐름을 따른다:

1. **에이전트 고정 worktree 진입**:
   - ChatGPT Codex: `F:\dev\tripmate-manager-codex`
   - Claude Code: `F:\dev\tripmate-manager-claude`
   - Google Antigravity: `F:\dev\tripmate-manager-antigravity`
2. **코드 갱신 및 브랜치 작성**:
   ```bash
   git fetch origin
   git switch -c agent/<topic> main
   ```
3. **CodeGraph 인덱스 동기화**:
   ```bash
   codegraph sync
   codegraph status
   ```
4. **로컬 품질 게이트 확인**:
   - 백엔드: `poetry run ruff check .` 및 `poetry run pytest`
   - 프론트엔드: `npm run type-check` 및 `npm run build`
