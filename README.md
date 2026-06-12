# TripMate Manager

TripMate 구동에 필요한 공용 기반 서비스(PostgreSQL / PostGIS, RustFS 등)를 관리하고 실시간으로 모니터링하기 위한 관리 소프트웨어입니다.

`tripmate`, `kor-travel-concierge`, `python-krtour-map`, `kor-travel-geo` 등 포스트그레스와 파일 스토리지를 활용하는 여러 서비스에서 이 인프라를 안정적으로 공용하여 사용할 수 있게 돕습니다.

---

## 기술 스택

- **Backend**: Python 3.11+, FastAPI, Docker SDK for Python, Poetry, pytest, Ruff
- **Frontend**: Next.js 14+ (App Router), TypeScript, TanStack Query (v5), Zod, React Hook Form, Tailwind CSS, Shadcn UI
- **Infrastructure**: Docker / Docker Compose (PostgreSQL / PostGIS, RustFS)

---

## 프로젝트 구조

```
tripmate-manager/
├── backend/            # Python FastAPI 백엔드 서비스
├── config/             # Docker target alias, 의존 순서, 초기화 step 설정
├── frontend/           # Next.js 프론트엔드 대시보드 웹
├── docs/               # 아키텍처, 결정 사항(ADR), 일지 및 백로그 문서
├── docker-compose.yml  # PostgreSQL, RustFS, kor-travel-geo 로컬 구동용 compose 설정
├── AGENTS.md           # AI 에이전트 협업 정책 및 언어 규칙
├── SKILL.md            # 에이전트 개발 매뉴얼 및 명령어 세트
└── CLAUDE.md           # 세션 컨텍스트 가이드
```

---

## 시작하기

상세한 개발 환경 셋업 및 가이드는 [개발 환경 셋업 가이드](docs/dev-environment.md) 문서를 참고해 주세요.

### 1. 인프라 컨테이너 구동

백엔드 패키지를 설치한 뒤 정식 CLI를 사용할 수 있습니다.

```bash
cd /mnt/f/dev/tripmate-manager/backend
poetry install
poetry run tmctl targets
poetry run tmctl main --build
```

`kor-travel-geo`만 필요한 경우:

```bash
poetry run tmctl geo --build
```

공식 별칭은 `db`, `storage`, `geo`, `map`, `ai`, `main`이며, 의존 순서는 `config/docker-targets.yml`의 `db -> storage -> geo -> map -> ai -> main`을 따른다.

기본 접속 정보는 다음과 같습니다.

| 대상 | Host 포트 | 접속 정보 |
|------|-----------|-----------|
| 통합 PostgreSQL / PostGIS | `5432` | `postgresql://localhost:5432` 안의 `kraddr_geo`, `tripmate`, `kor_travel_concierge`, `krtour_map` database |
| RustFS S3 API | `12101` | `http://127.0.0.1:12101` |
| RustFS console | `12105` | `http://127.0.0.1:12105` |
| kor-travel-geo API | `12201` | `http://127.0.0.1:12201` |
| kor-travel-geo Web UI | `12205` | `http://127.0.0.1:12205` |
| Manager Backend API | `12901` | `http://127.0.0.1:12901` |
| Manager Dashboard Web | `12905` | `http://127.0.0.1:12905` |

TripMate 계열 전체 포트 정책과 관련 로컬 레포 조사 결과는 [로컬 포트 정책](docs/ports.md)을 참고해 주세요.

정지/재시작은 같은 CLI에서 수행합니다.

```bash
poetry run tmctl action kraddr-geo-api restart
poetry run tmctl action rustfs stop
```

Docker 관리 설계와 CLI/API 상세는 [Docker 관리 설계](docs/docker-management.md)를 참고해 주세요.

### 2. 백엔드 실행
```bash
cd backend
poetry install
poetry run uvicorn tripmate_manager.main:app --host 0.0.0.0 --port 12901 --reload
```

### 3. 프론트엔드 실행
```bash
cd frontend
npm install
npm run dev
```

---

## 에이전트 협업 규칙

본 저장소는 다양한 AI 에이전트들과 협업하여 개발됩니다. 저장소 기여 규칙은 [AGENTS.md](AGENTS.md)를 참고해 주시고, 쉘 커맨드 및 체크리스트는 [SKILL.md](SKILL.md)를 읽어 주시기 바랍니다.
