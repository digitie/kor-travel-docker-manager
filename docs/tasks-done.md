# TASKS-DONE — 완료 태스크 기록

이 문서는 `kor-travel-docker-manager`에서 완료된 작업을 역순이 아닌 태스크 번호순으로 보관한다.
진행 중/대기 작업은 [`docs/tasks.md`](tasks.md)를 기준으로 한다.

> ⚠️ **ID 재사용 주의.** `T-013`~`T-018`은 서로 다른 두 작업 집합에 중복 부여돼 있다.
> 2026-06-12/13에 완료된 초기 집합(CLI 별칭·포트 정책·실행 위치 문서화·geo target 편입·
> 관측 스택·프로젝트명 전환)과, 2026-06-20/23에 완료된 운영 배포 집합(prod 공개 주소·host
> 네트워크 전환·Tailwind v4 전환·prod 배포 검증·운영 스택 기동·endpoint redaction)이 같은
> 번호를 쓴다. 재번호는 하지 않는다 — 이미 push된 커밋 메시지(예: `682074b`가 T-016,
> `d80e74f`가 T-017)와 `docs/journal.md`가 양쪽을 각각 참조하고 있어, 번호를 바꾸면 불변인
> git 이력과 문서가 어긋난다. **두 집합은 완료 날짜로 구분하고**, 상세 절 제목에도 날짜를
> 병기했다. 재사용된 행은 비고에 `⚠️ ID 재사용`으로 표시했다.

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
| **T-032** | C6c Map·PinVi image source provenance fail-close | 2026-07-19 | PR #58 squash merge, clean HEAD→Git archive→OCI label→manifest v3 결박 |
| **T-021** | PR #36 후속 하드닝(신뢰 프록시 시크릿·brute-force durable·공개키 DB 직접조회·모달 a11y) | 2026-06-24 | AUTH-3/AUTH-6/APIKEY-1/FE-4, PR #38 머지·prod 검증 |
| **T-025** | 배포 런북 + push 전 보안 감사 절차 | 2026-06-24 | `deploy-runbook.local.md`(gitignore), AGENTS.md 절차·DO NOT #13/#14 |
| **T-042** | C7 WebSocket 종료 코드 계약(accept-then-close) 결선 | 2026-07-28 | PR #75, n150 프록시 경유 실브라우저에서 `4401`/`wasClean` 확인, settle 실측 |
| **T-013** | 운영(prod) 공개 주소 `.env` 주입 및 CORS 환경변수화 | 2026-06-20 | 도메인 비노출, `KTDM_CORS_ALLOW_ORIGINS`, 프론트 환경파일 분리 ⚠️ ID 재사용 |
| **T-014** | Docker host 네트워크 전환·컨테이너=호스트 포트·서비스 prod URL·pinvi-dagster·tripmate 정리 | 2026-06-20 | `KTDM_DOCKER_NETWORK_MODE=host`, 12802, `KTDM_PROD_URL_*`, `ktd_venv` ⚠️ ID 재사용 |
| **T-015** | 프론트 Tailwind v4 + StyleSeed 전면 전환·전역 오류 복구 boundary | 2026-06-20 | geo PR #391 반영, `@theme` 토큰, `DESIGN-RULES.md` ⚠️ ID 재사용 |
| **T-016** | 운영(prod) 배포 및 docker-manager 실행 검증 | 2026-06-20 | SSH 배포, venv --without-pip, 백엔드/프론트 기동·검증, 공개 라우팅은 인프라 ⚠️ ID 재사용 |
| **T-017** | 운영 스택 db→conc 기동·geo 실데이터 복원·의존성 DAG 재설정 | 2026-06-20 | 이미지 save/load, geo 31GB 복원, `depends_on` DAG(concierge geo 비의존) ⚠️ ID 재사용 |
| **T-018** | prod endpoint 문서 redaction | 2026-06-23 | `kor-travel-map` #508 동일 패턴 반영 ⚠️ ID 재사용 |
| **T-019** | 관리자 로그인·세션·감사 로그·공개 API 키 관리 | 2026-06-23 | `kor-travel-geo` PR #399 패턴 반영 |
| **T-020** | PR #36 사후 리뷰 + fix-forward(보안 테스트 보강·감사 retention·CORS·프론트 a11y·utcnow 정리) | 2026-06-24 | 리뷰 코멘트, PR #37 머지, prod 배포·인증 검증 완료 |
| **T-023** | concierge PR #127 참고 공개도메인 Secure 쿠키 보강(`_is_https`가 https 공개 origin 인식) | 2026-06-24 | 브라우저 E2E로 로그인 정상 확인(403 무), Secure 플래그 PR #40 머지·prod 검증 |
| **T-024** | 로그아웃/세션만료 시 LoginScreen 전환 회귀 수정(auth-me 401→authenticated:false) | 2026-06-24 | PR #37 FE-2 회귀, 브라우저 E2E로 발견·PR #41 머지 |
| **T-029** | Concierge DB read 키를 Map Dagster에 단일 source로 주입 | 2026-07-13 | n150 단일 source 전환·cursor/수집기·권한·로그인 smoke 및 구 static 제거 완료 |
| **T-030** | Map OpiNet·KREX provider 키 compose 보간 drift 수정 | 2026-07-13 | 현재 env 이름·수집 서비스 전용 주입·API 제거 계약 테스트 고정 |
| **T-220** | `kor-travel-concierge` provider 상세 구현 및 과거 명칭 제거 | 2026-06-13 | 공식 프로젝트명 전환 완료 |
| **T-221** | `kor-travel-geo` DB명·환경변수·Docker 이름·Prometheus scrape 계약 동기화 | 2026-06-13 | `kor_travel_geo`, `KOR_TRAVEL_GEO_*`, `KTG_*`, `kor-travel-geo-*` 기준 반영 |
| **T-223** | 앱 target 흐름 재정렬 및 실제 컨테이너 빌드 편입 | 2026-06-13 | `geo -> conc -> map -> pinvi`, `srv` 별칭 반영 |

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

### T-032: C6c Map·PinVi image source provenance fail-close

- [x] production `pinvi-pair capture/deploy --build`가 Map·PinVi checkout의 exact Git root,
      clean worktree, lowercase 40자 `HEAD`를 container mutation 전에 검증하고 각 API build arg의
      유일한 source revision으로 전달하도록 고정
- [x] live worktree 대신 exact `HEAD` Git archive 임시 context를 사용해 build 중 변경·원복과
      ignored 파일 혼입을 차단
- [x] raw/resolved Compose의 context·in-tree Dockerfile·build arg를 exact allowlist로 고정하고
      external Dockerfile, additional context, secret, target 같은 추가 build input을 거부
- [x] build 후 Map·PinVi immutable image의 `org.opencontainers.image.revision`과 PinVi
      `io.pinvi.build.environment=production`을 container stop/recreate 전에 검증
- [x] compatible-pair manifest를 v3로 clean-cut하고 active/rollback과
      capture/deploy/rollback/recovery/smoke 결과에 image ID↔source revision을 함께 보존
- [x] 단일 적대적 리뷰의 두 P1을 반영하고 재검토 `ACCEPT FOR TESTS` 확인
- [x] WSL Docker Python 3.13 C6c focused `597 passed`, backend 전체 `685 passed`, 변경 source
      strict mypy, Ruff, production Compose `config --quiet`/resolved exact build mapping 통과
- [x] PR #58을 squash merge(`ecaab504e63a99cb757318d3b67337bec962d90b`)하고 상위 C7
      완료 흐름의 n150 production gate까지 마감

### T-021: PR #36 후속 하드닝(신뢰 프록시 시크릿·brute-force durable·공개키 DB 직접조회·모달 a11y)

- [x] AUTH-3: `KTDM_TRUSTED_PROXY_SECRET`으로 신뢰 CIDR 매칭만으로는 `X-Forwarded-*`를 신뢰하지
      않도록 보강한다. host 네트워크의 로컬 프로세스가 loopback 출처로 헤더를 위조하는 것을 차단한다.
- [x] AUTH-6: 로그인 실패 카운터를 인메모리가 아닌 감사 로그(durable) 기반으로 계산해 재시작·다중
      워커에서도 유지되게 한다(`check_login_rate_limit`).
- [x] APIKEY-1: 공개 API 키 유효성을 DB에서 직접 조회한다(`public_api_key_is_valid`).
- [x] FE-4: 로그·차트·설정 모달에 `role="dialog"`/`aria-modal`/`aria-label`을 부여한다.
- [x] PR #38 머지 및 prod 배포 후 검증.

### T-025: 배포 런북 + push 전 보안 감사 절차

- [x] 민감 정보를 담는 `docs/deploy-runbook.local.md`를 gitignore(`*.local.md`) 대상으로 유지하고,
      반복 배포 실수와 고정 절차를 상세히 기록한다.
- [x] AGENTS.md에 「prod 배포 & 보안 감사」와 「remote 푸시 전 보안 감사(필수 절차)」를 둔다.
- [x] DO NOT #13/#14로 민감값 커밋 금지를 명문화한다.
- [x] 프로젝트별 민감 문자열(호스트 IP·SSH 사용자·공개 도메인·관리자 비번·시크릿) 스캔 명령을
      런북에 고정한다.

### T-042: C7 WebSocket 종료 코드 계약(accept-then-close) 결선

`kor-travel-map`의 `T-ADM-C7W`(#806/PR #807)·`T-VN-H11`(#809)과 같은 결함이 매니저에도 있었다.
거절이 `accept()` 이전에 `close(4401)`을 호출해 uvicorn이 HTTP 403 handshake 거절로 바꿔 보냈고,
브라우저는 `4401`이 아니라 `1006`만 관측했다. 기존 테스트는 Starlette TestClient가 ASGI close
메시지를 그대로 되던지는 탓에 거짓 통과하고 있었다.

- [x] 세 pre-accept close를 accept-then-close로 전환하고 data frame 0건을 고정한다 (C-1/C-6).
- [x] settle window를 `KTDM_WS_ACCEPT_CLOSE_SETTLE_SECONDS`로 env 튜너블·`[0,5]` clamp한다 (C-3).
- [x] accept 실패 뒤 close 금지, accept→settle→close를 취소 shield child task로 묶는다 (C-4/C-5).
- [x] 프론트가 `4401`/`4000`을 소비해 재시도를 멈추고 LoginScreen으로 전환한다 (C-8).
- [x] 계약을 TestClient로 구분할 수 없으므로 ASGI 메시지 시퀀스(`accept`→`close`) 회귀로 고정하고,
      구 동작 negative control에서 6건이 실패함을 확인한다.
- [x] 같은 handler의 확인된 결함(idle container client 종료 미검출, 소진 stream 무한 polling,
      accept 후 close 없는 return, event loop 위 blocking 호출, 연결 0건 docker sweep,
      살아 있는 소켓의 세션 재검증 부재)을 함께 정리한다.
- [x] 적대적 리뷰 2명 × 2라운드를 반영한다. 재인가 우회(keepalive가 창을 리셋), 프론트 backoff
      무력화, `ensure_ascii` 회귀, 테스트 모듈의 공유 env 오염을 mutation/negative control로 고정.
- [x] n150 운영 HAProxy TLS 엣지 경유 실브라우저에서 `code=4401, wasClean=true, data frame 0건`,
      알 수 없는 container는 `4000`, 로그아웃 후 20초간 WS 재연결 0건을 확인한다.
- [x] settle 기본값을 실측으로 확정한다 — `0.25` 10/10, `0.0` 12/12 모두 4401(1006 0건)이라
      `0.0`으로 두고 knob은 유지. uvicorn ws 구현·프록시 토폴로지 변경 시 재측정.

### T-013 (2026-06-20): 운영(prod) 공개 주소 `.env` 주입 및 CORS 환경변수화

- [x] 백엔드 CORS 허용 Origin을 `KTDM_CORS_ALLOW_ORIGINS`(콤마 구분, 기본 `*`)로 제어하고 기동 시 루트 `.env`를 로드한다
- [x] 프론트엔드 백엔드 주소를 `.env.development`/`.env.production`로 분리하고 `.env.local` 섀도잉을 제거한다
- [x] 실제 운영 도메인은 gitignore된 `.env`/`frontend/.env.production`에만 두고 `.env.example`은 플레이스홀더로 문서화한다
- [x] 백엔드 ruff·CORS 파싱, 프론트 type-check·prod 빌드(인라인) 검증

### T-014 (2026-06-20): Docker host 네트워크 전환·컨테이너=호스트 포트·서비스 prod URL·pinvi-dagster·tripmate 정리

- [x] dev 기본 네트워크를 `network_mode: ${KTDM_DOCKER_NETWORK_MODE:-host}`로 전환하고 인프라/앱이 호스트 정규 포트에 직접 바인딩하도록 맞춘다
- [x] 서비스 간 참조(DSN/RustFS/내부 API/Dagster)를 `127.0.0.1:<포트>`로, Prometheus scrape·rustfs-init 엔드포인트도 동기화한다
- [x] geo/concierge/map/pinvi 컨테이너 내부 포트를 호스트 포트와 동일하게 통일한다
- [x] `pinvi-dagster`(12802)를 compose/registry/`pinvi` target에 추가하고 PinVi `apps/etl/Dockerfile`을 신규 작성한다
- [x] 관리 16개 서비스의 prod 공개 URL을 `KTDM_PROD_URL_*`(.env, 비노출)·`prod_url_env`로 주입해 대시보드 `public_url`로 표시한다
- [x] tripmate 로컬 잔재 정리(`pinvi_metrics.db` 개명, `ktd_venv` 재생성)
- [x] `docker compose config`·백엔드 ruff·프론트 type-check/build 검증 및 문서 동기화

### T-015 (2026-06-20): 프론트 Tailwind v4 + StyleSeed 전면 전환·전역 오류 복구 boundary

- [x] `kor-travel-geo` PR #391의 오류 복구 boundary(error/global-error/AppErrorPanel/error-recovery)를 매니저에 반영
- [x] Tailwind v4 전환(`@import`+`@theme`, `@tailwindcss/postcss`, autoprefixer/tailwind.config 제거)
- [x] `kor-travel-geo-ui/docs/DESIGN-RULES.md`의 StyleSeed 라이트 토큰을 `@theme`에 정의
- [x] `DashboardClient`·`AppErrorPanel`을 Pure Black → StyleSeed 토큰으로 전면 리스타일
- [x] `docs/DESIGN-RULES.md` 포팅, `DESIGN.md` superseded 안내, ADR-17, 프론트 type-check/build 검증

### T-019 (2026-06-23): 관리자 로그인·세션·감사 로그·공개 API 키 관리

- [x] 단일 관리자 계정(`admin`) 로그인 화면을 추가하고 실제 비밀번호는 gitignore된 `.env`의 `KTDM_ADMIN_PASSWORD_HASH`에 PBKDF2 해시로만 저장
- [x] 관리자 세션을 HMAC 서명 `httpOnly` 쿠키와 DB 저장 세션 해시로 검증하고, 지정된 프론트엔드 Origin만 관리자 API를 호출하도록 제한
- [x] 로그인 성공·실패·로그아웃·API 키 생성/폐기 이벤트를 `login_audit_events`에 기록하고 관리자 UI에서 조회
- [x] VWorld 호환 32자리 공개 API 키를 UI 버튼으로 생성하고, 원문은 1회만 표시하며 DB에는 해시와 힌트만 저장
- [x] 공개 API 키 활성 해시는 짧은 TTL 메모리 캐시로 읽고 생성·폐기 시 즉시 무효화
- [x] 신뢰된 로그인 세션 요청은 공개 API 키 검증을 생략할 수 있도록 공통 dependency 제공
- [x] `kor-travel-geo` PR #399의 v2 공개 API 키·관리자 인증 env 계약을 compose와 `.env.example`에 반영
- [x] PR #399 사후 리뷰를 재확인해 미검증 `X-Forwarded-*` 신뢰 차단, 401 처리, 로그인 접근성, clipboard fallback을 보강

### T-029 (2026-07-13): Concierge DB read 키를 Map Dagster에 단일 source로 주입

- [x] 루트 `.env`의 `KOR_TRAVEL_MAP_KOR_TRAVEL_CONCIERGE_API_KEY`를 유일한 secret source로 정의
- [x] 실제 fetcher를 실행하는 Dagster·Dagster daemon에 동일한 base URL/key 환경변수 주입
- [x] 사용하지 않는 map API에는 read secret을 주입하지 않는 least privilege 계약 고정
- [x] `.env.example`, Docker 관리 문서, compose 계약 테스트 동기화
- [x] n150에 Concierge head 0017(scope migration 0016 포함) 배포·제약 및 실제 UI 로그인 검증
- [x] prod `.env` 주입·override의 key/base URL literal 각 세 줄 제거·compose 보간·Dagster 두 서비스 재생성
- [x] `.env`와 두 컨테이너 key를 값 비노출 constant-time equality로 확인
- [x] `limit=1` snapshot/changes 2페이지 cursor 검증, `page_size=200` 전체 8페이지/1,416건 순회, 실제 수집기 각 1,416건 및 내부/write 403 smoke
- [x] BFF/operator static admin overlap 회전·UI/BFF 검증 후 구 static 제거
- [x] 최종 old 401/new admin 200/read 공급 200·write 403/UI login 검증과 제한권한 백업 폐기

### T-030 (2026-07-13): Map OpiNet·KREX provider 키 compose 보간 drift 수정

- [x] OpiNet 공통 key가 과거 `KRTOUR_MAP_*` source 대신 현재 `KOR_TRAVEL_MAP_*` `.env` 값을 읽도록 수정
- [x] OpiNet map API live preview key는 별도 설정을 우선하고 미설정 시 공통 key를 재사용하도록 고정
- [x] EX·GO key가 과거 `KRTOUR_MAP_*` source 대신 현재 `KOR_TRAVEL_MAP_*` `.env` 값을 읽도록 수정
- [x] map API live preview key는 별도 설정을 우선하고 미설정 시 EX key를 재사용하도록 고정
- [x] OpiNet·KREX 공통 key는 Dagster·daemon에만, resolved preview key는 map API에만 주입하는
      최소 권한 계약과 `.env.example` placeholder를 테스트
- [x] 실제 secret 비노출 상태로 focused test·Ruff·Docker Compose 보간 검증

### T-220 (2026-06-13): `kor-travel-concierge` provider 상세 구현 및 과거 명칭 제거

- [x] `config/docker-targets.yml`의 `ai` target을 `kor-travel-concierge` 기준으로 정리
- [x] 과거 AI provider 명칭 기반 별칭을 제거하고 새 공식 별칭만 남긴다
- [x] 통합 DB 기본값을 `kor_travel_concierge` database 기준으로 정리하고 과거 env fallback을 제거한다
- [x] `pinvi` target이 `kor-travel-concierge`에 직접 의존하지 않도록 문서와 target 설명을 정리
- [x] `krtour-map`과 `kor-travel-concierge` 간 provider 관계만 남도록 아키텍처/포트/관리 문서를 동기화
- [x] 관련 테스트와 설정 검증을 갱신한다

### T-221 (2026-06-13): `kor-travel-geo` DB명·환경변수·Docker 이름·Prometheus scrape 계약 동기화

- [x] `kor-travel-geo` DB 기본값을 `kor_travel_geo`로 맞춘다
- [x] manager override 변수는 `KOR_TRAVEL_GEO_*`, API/UI 컨테이너 내부 변수는 `KTG_*`로 맞춘다
- [x] Docker service/container 이름을 `kor-travel-geo-*`로 맞춘다
- [x] 물리 데이터 디렉터리를 `/home/digitie/kor-travel-geo-data` 기준으로 맞춘다
- [x] RustFS bucket 기본값을 `kor-travel-geo`로 맞춘다
- [x] Prometheus scrape target에 `kor-travel-geo-api:12501/metrics`와 `kor-travel-geo-ui:12505/api/metrics`를 추가한다
- [x] 관련 문서와 테스트 fixture를 갱신한다

### T-223 (2026-06-13): 앱 target 흐름 재정렬 및 실제 컨테이너 빌드 편입

- [x] dependency 순서를 `db -> storage -> gra -> cadv -> prom -> geo -> conc -> map -> pinvi`로 조정한다
- [x] `kor-travel-concierge` target을 `conc`로 등록하고 API/MCP/Scheduler/Web UI compose service를 추가한다
- [x] `kor-travel-map` target을 `map`에 실제 API/Dagster/Web UI compose service로 연결한다
- [x] PinVi target을 `pinvi`로 등록하고 `srv`, `main` 별칭을 제공한다
- [x] 공용 DB/RustFS 복구 스크립트에 `krtour_map_dagster` database와 `kor-travel-concierge` bucket 보정을 추가한다
- [x] API/CLI 테스트와 문서를 새 target 흐름에 맞춰 갱신한다
