# TripMate Manager

TripMate 구동에 필요한 공용 기반 서비스(PostgreSQL / PostGIS, RustFS 등)를 관리하고 실시간으로 모니터링하기 위한 관리 소프트웨어입니다. 

`tripmate`, `tripmate-agent`, `python-krtour-map`, `python-kraddr-geo` 등 포스트그레스와 파일 스토리지를 활용하는 여러 서비스에서 이 인프라를 안정적으로 공용하여 사용할 수 있게 돕습니다.

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
├── frontend/           # Next.js 프론트엔드 대시보드 웹
├── docs/               # 아키텍처, 결정 사항(ADR), 일지 및 백로그 문서
├── docker-compose.yml  # PostgreSQL 및 RustFS 서비스 로컬 구동용 compose 설정
├── AGENTS.md           # AI 에이전트 협업 정책 및 언어 규칙
├── SKILL.md            # 에이전트 개발 매뉴얼 및 명령어 세트
└── CLAUDE.md           # 세션 컨텍스트 가이드
```

---

## 시작하기

상세한 개발 환경 셋업 및 가이드는 [개발 환경 셋업 가이드](file:///f:/dev/tripmate-manager/docs/dev-environment.md) 문서를 참고해 주세요.

### 1. 인프라 컨테이너 구동
```bash
docker-compose up -d
```

### 2. 백엔드 실행
```bash
cd backend
poetry install
poetry run uvicorn src.tripmate_manager.main:app --reload
```

### 3. 프론트엔드 실행
```bash
cd frontend
npm install
npm run dev
```

---

## 에이전트 협업 규칙

본 저장소는 다양한 AI 에이전트들과 협업하여 개발됩니다. 저장소 기여 규칙은 [AGENTS.md](file:///f:/dev/tripmate-manager/AGENTS.md)를 참고해 주시고, 쉘 커맨드 및 체크리스트는 [SKILL.md](file:///f:/dev/tripmate-manager/SKILL.md)를 읽어 주시기 바랍니다.
