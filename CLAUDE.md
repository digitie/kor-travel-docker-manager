# CLAUDE.md — 프로젝트 컨텍스트

이 파일은 에이전트(Claude Code, Antigravity, Codex 등)가 세션 시작 시 가장 먼저 읽는 컨텍스트 문서다.

## 프로젝트 현황 (2026-07-20)

PinVi 구동에 필요한 통합 PostgreSQL/PostGIS, RustFS, `kor-travel-geo`, `kor-travel-concierge`, `kor-travel-map`, PinVi Docker 컨테이너 구동 관리 및 상태 모니터링 관리 소프트웨어다.
현재 FastAPI API, Next.js 대시보드, Python CLI, 설정 파일 기반 Docker target registry가 구현되어 있다.

C7 C6c image provenance PR #61은 병합됐다. issue #63/T-035는 Map #780/#782의 production
admin/service/public/debug/cursor 설정을 manager C6c env/preflight에 결박하는 중이다. production compatible-pair는 Map·PinVi exact
clean `HEAD`의 Git archive build context와 OCI revision label을 검증하고, manifest v4에 Map
API·UI·Dagster web·daemon 네 immutable image ID와 공통 revision, PinVi API image ID와
revision을 하나의 fail-closed runtime-set 계약으로 기록한다.

T-039는 n150 C6c capture의 PinVi login shell 오탐을 수정한다. PinVi `/admin/login`은
`Suspense fallback={null}` 아래의 client component라 SSR HTML에 `admin-login-form`이 없을 수 있다.
Manager의 HTTP shell smoke는 200·`text/html`·비어 있지 않은 body·일반 Next.js static marker와
`admin/login` 전용 page chunk를 검증하고, hydration 후 form과 로그인 동작은 최종 Playwright가 검증한다.
일반 fallback shell이 route chunk 없이 통과하는 완화는 허용하지 않는다.

T-040/issue #70은 Map production API의 feature 관리 REST가 image 기본값 `true`로 동작하더라도
Manager source가 이를 명시 승인했다는 사실을 C7 attestation이 증명할 수 있도록 canonical Compose에
`KOR_TRAVEL_MAP_API_FEATURES_ROUTES_ENABLED=true`를 literal로 결선한다. C6c는
source/resolved/runtime의 API exact path와 다른 service·channel의 이름 부재를 모두 검증한 뒤에만
compatible-pair를 승인한다.

- **Backend**: Python FastAPI 기반 (`backend/`)
- **Frontend**: Next.js 14+ TypeScript 기반 (`frontend/`)

## 디렉토리 구조

```
f:\dev\kor-travel-docker-manager\
├── backend/            # FastAPI 백엔드 (Python 3.11+, Poetry)
│   ├── src/            # 백엔드 소스코드
│   └── tests/          # 백엔드 단위/통합 테스트
├── config/             # Docker target alias, 의존 순서, 초기화 step 설정
├── frontend/           # Next.js 프론트엔드 (React, TS, Tailwind, Shadcn)
│   ├── src/app/        # App Router 및 페이지
│   └── src/components/ # UI 컴포넌트
├── docs/               # 아키텍처 및 의사결정 문서
├── docker-compose.yml  # PostgreSQL/RustFS/kor-travel-geo 로컬 구동 compose 파일
├── AGENTS.md           # 에이전트 협업 정책 및 한글 언어 규정
├── SKILL.md            # 에이전트 매뉴얼 및 명령어 세트
└── CLAUDE.md           # 본 파일 (세션 상태 관리)
```

## 로컬 개발 및 빠른 검증 명령

아래 개발/검증/Docker/서버/버전 관리 명령은 WSL을 포함한 Linux shell에서 실행한다. `git`과 CodeGraph도 Linux에서만 실행하며, Playwright E2E는 우선 n150 Linux 운영 환경에서 실행하고 불가능할 때만 Windows 호스트 실행을 예외로 허용한다.

### 백엔드 (FastAPI)
```bash
# 의존성 설치 (Poetry)
cd /mnt/f/dev/kor-travel-docker-manager
cd backend
poetry install

# 린팅 및 포맷팅 (Ruff)
poetry run ruff check .
poetry run ruff format .

# 백엔드 실행
poetry run uvicorn kor_travel_docker_manager.main:app --host 0.0.0.0 --port 12901 --reload
# 또는 수동 가상환경: PYTHONPATH=src ktd_venv/bin/python -m uvicorn kor_travel_docker_manager.main:app --host 0.0.0.0 --port 12901 --reload

# 테스트 실행
poetry run pytest

# 개발 의존 Docker 실행
poetry run ktdctl srv --build
# 짧은 별칭: db, storage, gra, cadv, prom, geo, conc, map, pinvi, srv
# gra/cadv/prom은 Grafana 12205, cAdvisor 12301, Prometheus 12401을 분리 실행
# geo target은 kor-travel-geo API 12501, Web UI 12505까지 포함
# conc target은 kor-travel-concierge API/MCP/Web UI를 포함하고, map target은 kor-travel-map API/Dagster/Web UI까지 포함
```

### 프론트엔드 (Next.js)
```bash
# 의존성 설치 (npm)
cd /mnt/f/dev/kor-travel-docker-manager
cd frontend
npm install

# 타입 체크
npm run type-check

# 린팅
npm run lint

# 프론트엔드 실행
npm run dev

# 빌드 검증
npm run build
```

## 작업 후 의무사항

1. `docs/journal.md`에 항목 추가 (역시간순 작업 기록)
2. `docs/tasks.md`의 태스크 상태(T-NNN) 갱신
3. 새로운 주요 아키텍처 결정이 있을 시 `docs/decisions.md`에 ADR 문서 추가
4. PR 작성 또는 변경 내용 완료 시 fast lint 및 build 통과 확인
5. Docker 관리 기능 변경 시 `docs/docker-management.md`와 CLI/API target 정의 동기화
