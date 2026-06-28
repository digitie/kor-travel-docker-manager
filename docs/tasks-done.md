# TASKS-DONE — 완료 태스크 기록

이 문서는 `kor-travel-docker-manager`에서 완료된 작업을 역순이 아닌 태스크 번호순으로 보관한다.
진행 중/대기 작업은 [`docs/tasks.md`](tasks.md)를 기준으로 한다.

---

## 완료 현황 요약

| 태스크 ID | 작업 항목 | 완료 날짜 | 비고 |
|:---|:---|:---:|:---|
| **T-001** | 에이전트 및 워크스페이스 문서 초기화 | 2026-06-10 | 가이드 및 설정 완료 |
| **T-002** | 프로젝트 인프라 설정 (`.gitignore`, `docker-compose.yml`, `README.md`) | 2026-06-11 | `kor-travel-geo` 인프라 이관 반영 완료 |
| **T-003** | FastAPI 백엔드 뼈대 구성 (`backend/pyproject.toml`, main app) | 2026-06-11 | 뼈대 구성 및 websockets 추가 완료 |
| **T-004** | Docker 제어 모듈 (`DockerService` 및 API 엔드포인트) 구현 | 2026-06-11 | 실시간 메트릭 및 로그 엔드포인트 포함 구현 완료 |
| **T-005** | Next.js 프론트엔드 구성 (`frontend/package.json` 및 라우팅) | 2026-06-11 | 패키지 구성 및 recharts 설치 완료 |
| **T-006** | 대시보드 UI 및 TanStack Query 연동 개발 | 2026-06-11 | WebSocket 실시간 테이블 및 차트/로그 모달 완료 |
| **T-007** | 품질 검증 및 최종 통합 테스트 | 2026-06-11 | 백엔드 테스트 및 프론트엔드 빌드 검사 완료 |
| **T-008** | Docker 관리 문서 및 target registry 정리 | 2026-06-12 | 통합 DB 모델, CLI/API target 기준 정리 |
| **T-009** | Python CLI 및 target ensure/build 구현 | 2026-06-12 | `ktdctl` CLI 추가 |
| **T-010** | Docker inspect API 및 secret redaction 구현 | 2026-06-12 | `/api/v1/containers/{id}/inspect` 추가 |
| **T-013** | 설정 파일 기반 CLI 별칭 및 초기화/복구 step 구현 | 2026-06-12 | `db/storage/geo/map/ai/main` alias와 init step 추가 |
| **T-014** | Kor Travel/PinVi 계열 로컬 포트 정책 일원화 | 2026-06-12 | PostgreSQL `5432`, RustFS `12101/12105`, manager `12901/12905` 반영 |
| **T-015** | 실행 위치 정책 문서화 | 2026-06-12 | 당시 정책: git은 Windows, 일반 개발 명령은 WSL, Playwright E2E는 Windows로 고정. T-028로 대체 |
| **T-016** | `kor-travel-geo` Docker API/UI target 편입 | 2026-06-12 | `geo` target에 API/Web UI compose 서비스 추가 |
| **T-017** | 관측 스택 Docker target 추가 | 2026-06-13 | Grafana, cAdvisor, Prometheus 분리 컨테이너 추가 |
| **T-018** | 프로젝트명 및 CLI 명령 전환 | 2026-06-13 | `kor-travel-docker-manager`, `ktdctl` 기준으로 변경 |
| **T-222** | 관측 target 개별 분리 및 포트 재배치 | 2026-06-13 | `gra`, `cadv`, `prom` target과 `12205`, `12301`, `12401` 포트 반영 |
| **T-224** | 과거 서비스명과 공용 인프라 명칭 정리 | 2026-06-15 | PinVi 및 `kor-travel-*` 기준 반영 |
| **T-026** | PinVi API worker 기본값 환경변수화 | 2026-06-28 | `PINVI_API_WORKERS=1` 기본값으로 process-local WebSocket broadcast 제약 반영 |
| **T-027** | PinVi public API URL·CORS origin 환경변수화 | 2026-06-28 | prod public Web/API origin을 gitignore `.env`에서 주입하도록 변경 |
| **T-028** | Linux 전용 개발·버전관리·CodeGraph 실행 위치 정책 정리 | 2026-06-28 | `git`/CodeGraph는 Linux, Playwright E2E는 n150 우선·불가 시 Windows fallback |

---

## 완료 태스크 세부 내역

### T-001: 에이전트 및 워크스페이스 문서 초기화

- [x] `antigravity.json`, `claude.json`, `codex.json` 설정 파일 생성
- [x] `.gemini/`, `.claude/`, `.codex/` 내부 설정 디렉토리 매핑
- [x] 에이전트 협업 정책 (`AGENTS.md`), 진입 컨텍스트 (`CLAUDE.md`), 스킬 매뉴얼 (`SKILL.md`) 생성
- [x] 아키텍처 문서(`docs/architecture.md`) 및 의사결정 문서(`docs/decisions.md` ADR-1~4) 생성
- [x] 작업 일지 (`docs/journal.md`) 및 백로그 관리 파일 (`docs/tasks.md`) 작성

### T-002: 프로젝트 인프라 설정

- [x] 모노레포용 통합 `.gitignore` 작성
- [x] PostgreSQL + RustFS 구동을 위한 `docker-compose.yml` 루트 정의
- [x] `kor-travel-geo`용 PostgreSQL/RustFS 포트·credential·bucket 기본값 이관
- [x] 공용 인프라 구동/정지/재시작 초기 helper 추가
- [x] 전체 저장소 개요를 담은 `README.md` 작성

### T-003: FastAPI 백엔드 뼈대 구성

- [x] `backend/pyproject.toml` 생성 및 dependencies 추가 (FastAPI, uvicorn, docker sdk 등)
- [x] `backend/src/kor_travel_docker_manager/main.py` 진입 소스 및 환경 설정 모듈 작성
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
- [x] 컨테이너 구동 제어 상태 카드 UI 구현
- [x] 백엔드 연동 액션 버튼 및 로그 출력 콘솔 UI 구현

### T-007: 품질 검증 및 최종 통합 테스트

- [x] 백엔드 및 프론트엔드 전체 린터/타입 빌드 테스트 실행
- [x] Docker 데몬 연동 수동 기능 확인
- [x] BMW M 디자인 시스템(DESIGN.md) 반영 및 `/bmw` 쇼케이스 검증 완료
- [x] `docs/design-system.md` 보강 및 `react-doctor` 성능 오딧 검증 완료
- [x] 변경 사항에 대한 `walkthrough.md` 작성 및 최종 PR 제출

### T-008: Docker 관리 문서 및 target registry 정리

- [x] `docs/docker-management.md` 신규 작성
- [x] 통합 DB 모델(`kor-travel-geo-postgres:5432`)을 공식 기준으로 문서 정정
- [x] UI/API/CLI에서 공유할 target registry 정의
- [x] 오래된 분리 DB target 제거 및 초기 helper 정리

### T-009: Python CLI 및 target ensure/build 구현

- [x] `ktdctl` console script 추가
- [x] `targets`, `status`, `ensure`, `logs`, `action`, `inspect` 명령 추가
- [x] `ensure <target> --build`에서 `docker compose up -d --build`를 인자 배열로 실행
- [x] CLI mock 테스트 추가

### T-010: Docker inspect API 및 secret redaction 구현

- [x] `GET /api/v1/targets` API 추가
- [x] `POST /api/v1/targets/{target}/ensure` API 추가
- [x] `GET /api/v1/containers/{container_id}/inspect` API 추가
- [x] inspect environment redaction 테스트 추가

### T-013: 설정 파일 기반 CLI 별칭 및 초기화/복구 step 구현

- [x] `config/docker-targets.yml`에 `db`, `storage`, `geo`, `map`, `ai`, `main` 의존 순서 정의
- [x] `ktdctl db --build`처럼 짧은 별칭을 직접 `ensure`로 실행하는 CLI shortcut 추가
- [x] 통합 DB database/role/schema/extension 복구 스크립트 추가
- [x] RustFS 공용 bucket 복구 스크립트 추가
- [x] `kor-travel-geo` 원천 디렉터리와 핵심 적재 테이블 검증 스크립트 추가
- [x] API/CLI가 같은 설정 파일 registry를 읽도록 정리

### T-014: Kor Travel/PinVi 계열 로컬 포트 정책 일원화

- [x] 관련 canonical 로컬 레포의 현재 포트 사용처 조사
- [x] `docs/ports.md`에 현재 포트와 정책 포트 비교표 작성
- [x] 통합 PostgreSQL host 포트를 `5432`로 변경
- [x] RustFS host 포트를 S3 API `12101`, console `12105`로 변경
- [x] RustFS 컨테이너 내부 포트를 이미지 표준 `9000`, `9001`로 정리
- [x] `kor-travel-docker-manager` Backend API를 `12901`, Dashboard Web을 `12905`로 변경
- [x] `config/docker-targets.yml`에 포트 정책 metadata와 target 대역 추가
- [x] 포트 정책 ADR 추가

### T-015: 실행 위치 정책 문서화

- [x] `AGENTS.md`에 git/WSL/Playwright E2E 실행 위치 강제 규칙 추가
- [x] `SKILL.md` 빠른 시작 명령을 WSL 기준으로 정리
- [x] `docs/dev-environment.md`에 명령 실행 위치 표와 에이전트 작업 절차 추가
- [x] `CLAUDE.md` 빠른 검증 명령에 WSL/Windows 예외 정책 명시

### T-028: Linux 전용 개발·버전관리·CodeGraph 실행 위치 정책 정리

- [x] `git` 버전 관리 명령을 Windows 호스트 예외에서 Linux shell 전용으로 변경
- [x] CodeGraph 생성/동기화도 Linux shell에서만 수행하도록 명시
- [x] Playwright E2E 기본 실행 위치를 n150 Linux 운영 환경으로 바꾸고, 불가능한 경우에만 Windows fallback을 허용
- [x] `AGENTS.md`, `SKILL.md`, `CLAUDE.md`, `docs/dev-environment.md`, `docs/tasks-done.md`를 새 정책에 맞춰 동기화

### T-016: `kor-travel-geo` Docker API/UI target 편입

- [x] `docker-compose.yml`에 `kor-travel-geo-api`, `kor-travel-geo-ui` 서비스 추가
- [x] `config/docker-targets.yml`에 `kor-travel-geo-api`, `kor-travel-geo-ui` 관리 컨테이너 등록
- [x] `geo` target이 API/Web UI 실행과 원천 데이터 검증을 함께 수행하도록 변경
- [x] 초기 helper target도 `geo` 이상에서 API/Web UI를 포함하도록 정리
- [x] `.env.example`, 포트 문서, Docker 관리 문서에 API/Web UI 포트 기준 추가

### T-017: 관측 스택 Docker target 추가

- [x] `docker-compose.yml`에 Prometheus, Grafana, cAdvisor Exporter를 별도 service로 추가
- [x] `config/docker-targets.yml`에 Grafana, Prometheus, cAdvisor 관리 컨테이너를 등록
- [x] 포트 정책에 맞춰 Grafana, cAdvisor Exporter, Prometheus 포트를 배정
- [x] Prometheus scrape config와 Grafana Prometheus datasource provisioning을 추가
- [x] `.env.example`, 아키텍처/포트/Docker 관리 문서, ADR을 갱신

### T-018: 프로젝트명 및 CLI 명령 전환

- [x] 공식 프로젝트명과 GitHub 저장소명을 `kor-travel-docker-manager` 기준으로 문서화
- [x] Python package import path를 `kor_travel_docker_manager`로 변경
- [x] CLI console script를 `ktdctl`로 변경하고 이전 CLI 명령 안내 제거
- [x] 프론트엔드/백엔드 package metadata와 화면 metadata를 새 이름으로 갱신
- [x] Docker Compose project name을 `kor-travel-docker-manager`로 고정

### T-222: 관측 target 개별 분리 및 포트 재배치

- [x] 단일 관측 target을 제거하고 `gra`, `cadv`, `prom` target으로 분리
- [x] dependency 순서를 `db -> storage -> gra -> cadv -> prom -> geo -> map -> ai -> main`으로 조정
- [x] Grafana는 공용 연계를 위해 `12205`, cAdvisor는 `12301`, Prometheus는 `12401`로 배치
- [x] `kor-travel-geo` API/Web UI를 새 dependency 순서에 맞춰 `12501`, `12505`로 이동
- [x] CLI, API 테스트, 포트 문서, Docker 관리 문서, 개발 가이드를 같은 기준으로 갱신

### T-224: 과거 서비스명과 공용 인프라 명칭 정리

- [x] PinVi 전용 database, role, bucket, 환경변수 기본값을 `pinvi` 및 `PINVI_*` 기준으로 정리
- [x] 공용 RustFS와 관측 컨테이너 이름을 `kor-travel-*` 기준으로 정리
- [x] 문서, 테스트, 설정 파일에서 과거 서비스명 잔여 표기를 제거

### T-026: PinVi API worker 기본값 환경변수화

- [x] `pinvi-api` compose command의 hardcoded `--workers 2`를 `PINVI_API_WORKERS` 환경변수로 대체
- [x] shared broker 도입 전 PinVi WebSocket broadcast broker가 process-local이라는 운영 제약을 문서화
- [x] `.env.example`에 `PINVI_API_WORKERS=1` 기본값 추가

### T-027: PinVi public API URL·CORS origin 환경변수화

- [x] `pinvi-web` build/runtime의 API URL을 `PINVI_PUBLIC_API_URL`로 주입 가능하게 변경
- [x] `pinvi-api` CORS 허용 origin을 `PINVI_CORS_ALLOWED_ORIGINS`로 주입 가능하게 변경
- [x] dev 기본값은 로컬 `127.0.0.1` API/Web origin으로 유지하고 prod 실제 도메인은 gitignore `.env`에만 두도록 문서화
