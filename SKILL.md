# SKILL — tripmate-manager 에이전트 매뉴얼

> 이 파일은 당신(AI 에이전트)이 작업을 시작하기 전 반드시 읽어야 한다.
> 1회 읽는 것으로 백엔드 및 프론트엔드의 세부 구성 실수를 방지할 수 있다.

---

## 1. 정체성

본 저장소(`tripmate-manager`)는 `tripmate`, `tripmate-agent`, `python-krtour-map`, `python-kraddr-geo`가 사용하는 공용 데이터베이스(PostgreSQL/PostGIS) 및 파일 저장소(RustFS)를 Docker 기반으로 안정적으로 통합 관리하고 상태를 대시보드로 모니터링하기 위한 관리 도구다.

- **FastAPI 백엔드**: 로컬 Docker 데몬과 소켓 또는 API로 연동해 컨테이너의 상태(`running`, `exited` 등)를 읽고 Start/Stop/Restart 제어 명령을 실행한다.
- **Next.js 프론트엔드**: 관리자 대시보드 화면을 렌더링하며, 미려한 UI(dark mode, HSL tailored color palette, glassmorphism)를 제공해 운영의 직관성을 돕는다.

---

## 2. 빠른 시작

> [!IMPORTANT]
> 본 프로젝트의 백엔드 및 프론트엔드는 **WSL (Linux) 환경** 내부에서 실행 및 패키지 관리를 수행한다. 단, `git` 버전 관리는 **Windows 호스트**에서 수행한다.

### 백엔드 (FastAPI) Setup
```bash
scripts/infra.sh up kraddr-geo
cd backend
poetry install
poetry run ruff check .
poetry run pytest
poetry run uvicorn src.tripmate_manager.main:app --reload
```

### 프론트엔드 (Next.js) Setup
```bash
cd frontend
npm install
npm run type-check
npm run dev
```

---

## 3. 디렉토리 지도

```
backend/
  src/
    tripmate_manager/
      main.py                 — 백엔드 FastAPI 진입점
      config.py               — 환경 설정 (데이터베이스 URL, Docker Socket 경로 등)
      api/
        routes.py             — 컨테이너 상태 조회, 제어, 로그 API 엔드포인트
      services/
        docker_service.py     — Python Docker SDK 활용 컨테이너 상태 제어 및 수집
  tests/                      — pytest 단위 및 통합 테스트 코드
frontend/
  src/
    app/
      layout.tsx              — 루트 레이아웃 (Global CSS, Provider 구성)
      page.tsx                — 대시보드 메인 뷰 (상태 카드, 제어 버튼, 로그 콘솔)
    components/               — 버튼, 카드 등 프리미엄 디자인 컴포넌트
    hooks/                    — TanStack Query API 구독 훅
    lib/                      — zod 스키마 정의 및 axios/fetch 유틸리티
docs/
  architecture.md             — 아키텍처 가이드 (백엔드 ⇄ Docker 소켓, API ⇄ 프론트엔드)
  decisions.md                — 의사결정 기록 (ADRs)
  journal.md                  — 작업 일지 (역시간순)
  tasks.md                    — T-NNN 백로그 태스크
  dev-environment.md          — 개발 환경 설치 가이드
```

---

## 4. 절대 하지 말 것 (DO NOT)

1. **`main` 직접 푸시 금지**: 반드시 브랜치 작업 후 PR 제출을 거쳐 머지한다.
2. **Docker Socket 접근 권한 무시 금지**: Windows 호스트 또는 WSL 환경에서 Docker 데몬에 정상적으로 접근할 수 있도록 `DockerService`가 `docker.from_env()`를 호출할 때 예외 처리를 철저히 작성한다.
3. **Next.js Client Directive 누락 금지**: 프론트엔드에서 React `useState`, `useEffect`, TanStack Query 훅을 사용하는 파일의 첫 줄에 `'use client'`를 누락하지 않는다.
4. **API 키 및 Credential 평문 커밋 금지**: `.env`에 보관하고 git 추적을 방지한다.
5. **독립성 유지 실패 금지**: `tripmate-manager`는 서비스의 "인프라 관리"만을 수행하므로, 다른 TripMate 구성 패키지의 비즈니스 로직(예: 지도 렌더링, 관광지 정보 정합성 검사 등)을 수행해서는 안 된다.
6. **인프라 생명주기 재분산 금지**: `python-kraddr-geo` 등 하위 프로젝트 저장소가 PostgreSQL/RustFS 컨테이너를 직접 정지/재시작하지 않도록, 포트·credential·bucket·compose 설정은 이 저장소의 `docker-compose.yml`과 `scripts/infra.sh`에 둔다.

---

## 5. 도메인 어휘

| 약어 / 용어 | 의미 |
|------|------|
| PostgreSQL | 관계형 데이터베이스. PostGIS 확장 플러그인이 내장되어 공간 데이터(GeoJSON 등)를 처리. |
| RustFS | 초고속 분산 파일 시스템. 이미지 및 미디어 자원을 보관하기 위한 미니 오브젝트 스토리지. |
| Docker Socket | Docker 데몬과 API 통신을 수행하기 위한 유닉스 소켓 또는 명명된 파이프 (Windows). |
| TanStack Query | 비동기 상태 관리 라이브러리 (React Query). API 캐싱 및 폴링에 필수적. |

---

## 6. 작업 후 체크리스트

- [ ] 백엔드 `poetry run ruff check .` 통과
- [ ] 백엔드 `poetry run pytest` 통과
- [ ] 프론트엔드 `npm run type-check` 통과
- [ ] 프론트엔드 `npm run build` 통과 (Next.js 빌드 성공 확인)
- [ ] `docs/journal.md`에 작업 항목 추가 (역시간순)
- [ ] `docs/tasks.md`의 태스크 상태 갱신
- [ ] 새로운 구조나 설계 추가 시 `docs/decisions.md`에 ADR 추가
