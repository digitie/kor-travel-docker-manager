# TASKS — 개발 태스크 백로그

이 문서는 `tripmate-manager` 개발 태스크 목록과 진행 현황을 관리한다. 완료된 작업은 `[x]`, 진행 중은 `[/]`, 미진행은 `[ ]`로 마킹한다.

---

## 작업 현황 요약

| 태스크 ID | 작업 항목 | 상태 | 완료 날짜 | 비고 |
|:---|:---|:---:|:---:|:---|
| **T-001** | 에이전트 및 워크스페이스 문서 초기화 | `[x]` | 2026-06-10 | 가이드 및 설정 완료 |
| **T-002** | 프로젝트 인프라 설정 (`.gitignore`, `docker-compose.yml`, `README.md`) | `[x]` | 2026-06-11 | python-kraddr-geo 인프라 이관 반영 완료 |
| **T-003** | FastAPI 백엔드 뼈대 구성 (`backend/pyproject.toml`, main app) | `[x]` | 2026-06-11 | 뼈대 구성 및 websockets 추가 완료 |
| **T-004** | Docker 제어 모듈 (`DockerService` 및 API 엔드포인트) 구현 | `[x]` | 2026-06-11 | 실시간 메트릭 및 로그 엔드포인트 포함 구현 완료 |
| **T-005** | Next.js 프론트엔드 구성 (`frontend/package.json` 및 라우팅) | `[x]` | 2026-06-11 | 패키지 구성 및 recharts 설치 완료 |
| **T-006** | 대시보드 UI 및 TanStack Query 연동 개발 | `[x]` | 2026-06-11 | WS 실시간 테이블 및 차트/로그 모달 완료 |
| **T-007** | 품질 검증 및 최종 통합 테스트 | `[x]` | 2026-06-11 | 백엔드 테스트 및 프론트엔드 빌드 검사 완료 |

---

## 태스크 세부 내역

### T-001: 에이전트 및 워크스페이스 문서 초기화
- [x] `antigravity.json`, `claude.json`, `codex.json` 설정 파일 생성
- [x] `.gemini/`, `.claude/`, `.codex/` 내부 설정 디렉토리 매핑
- [x] 에이전트 협업 정책 (`AGENTS.md`), 진입 컨텍스트 (`CLAUDE.md`), 스킬 매뉴얼 (`SKILL.md`) 생성
- [x] 아키텍처 문서(`docs/architecture.md`) 및 의사결정 문서(`docs/decisions.md` ADR-1~4) 생성
- [x] 작업 일지 (`docs/journal.md`) 및 백로그 관리 파일 (`docs/tasks.md`) 작성

### T-002: 프로젝트 인프라 설정
- [x] 모노레포용 통합 `.gitignore` 작성
- [x] PostgreSQL + RustFS 구동을 위한 `docker-compose.yml` 루트 정의
- [x] `python-kraddr-geo`용 PostgreSQL/RustFS 포트·credential·bucket 기본값 이관
- [x] 공용 인프라 구동/정지/재시작 helper `scripts/infra.sh` 추가
- [x] 전체 저장소 개요를 담은 `README.md` 작성

### T-003: FastAPI 백엔드 뼈대 구성
- [x] `backend/pyproject.toml` 생성 및 dependencies 추가 (FastAPI, uvicorn, docker sdk 등)
- [x] `backend/src/tripmate_manager/main.py` 진입 소스 및 환경 설정 모듈 작성
- [x] 백엔드 ruff/lint 검증 스크립트 셋업

### T-004: Docker 제어 모듈 구현
- [x] `DockerService` 클래스 개발 (컨테이너 상태 수집, 시작/정지/재시작 통제)
- [x] API 라우터 (`/api/containers`) 연동 및 로그 조회 기능 작성
- [x] 단위 테스트 작성 및 pytest 통과 검증

### T-005: Next.js 프론트엔드 구성
- [x] `frontend/package.json` 및 `tsconfig.json` 정의
- [x] Zod, React Hook Form, TanStack Query, Tailwind CSS 패키지 설치
- [x] App Router 구조 기반 루트 레이아웃 작성

### T-006: 대시보드 UI 및 TanStack Query 연동 개발
- [x] TanStack Query Client 및 Query Provider 설정
- [x] 컨테이너 구동 제어 상태 카드 UI 구현 (neon glow 및 micro-animations 반영)
- [x] 백엔드 연동 액션 버튼 및 로그 출력 콘솔 UI 구현

### T-007: 품질 검증 및 최종 통합 테스트
- [x] 백엔드 및 프론트엔드 전체 린터/타입 빌드 테스트 실행
- [x] Docker 데몬 연동 수동 기능 확인
- [x] BMW M 디자인 시스템(DESIGN.md) 반영 및 `/bmw` 쇼케이스 검증 완료
- [x] `docs/design-system.md` 보강 및 `react-doctor` 성능 오딧 검증 완료
- [x] 변경 사항에 대한 `walkthrough.md` 작성 및 최종 PR 제출
