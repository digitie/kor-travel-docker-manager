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

## 2026-08-06 — T-VN-41-F1J Map 소유 cancel-probe fixture lifecycle

- [x] **T-VN-41-F1J — dynamic fixture·정확한 PinVi cancel relay·isolated final verification**

  Map PR #960(F1J-A)은 transaction-scoped cancel-probe fixture를 Map DB에 `armed → consumed → finalized`
  상태로 소유하고, 전용 `ops:fixture` principal과 exact `409 PIPELINE_CANCELLATION_UNSAFE`를 추가했다.
  Manager PR #159(F1J-B)는 static UUID와 넓은 성공 집합을 제거하고, fixture ensure·durable receipt·response-loss
  resume·finalize를 Map canonical state에만 결박했다. PinVi PR #435와 Manager PR #160(F1J-C)은 Map service
  provenance bytes, migration head, cache-target/C6c capability를 strict preflight pinset으로 수렴했다.

  final F1J-D는 Map #960, PinVi #439, Manager #163 정확한 source에서 새 Compose project·DB·volume·network만
  생성해 실행했다. `ops:read` 사전 점검은 `200`, Manager smoke의 로그인·ETL·provider-sync는 모두 `200`, cancel은
  exact `409`이고 durable rerun 결과도 일치했다. 관리자 live UI E2E는 5/5, 새 PinVi DB의 mutating trip
  WebSocket/reconnect E2E는 1/1 통과했다. 기존 runtime/data, backup/restore는 전혀 사용하지 않았고 모든
  일회성 container·volume·network·image tag·scratch를 폐기했다. 이 완료는 issue #136의 F1J 보강 범위만 닫으며,
  상위 F1D bootstrap task의 별도 잔여 조건은 열린 backlog에 유지한다.

## 2026-08-05 — T-VN-41-F1I F1D fail-close checkpoint 관측성 (issue #136 보강)

- [x] **T-VN-41-F1I — F1D fail-close checkpoint 관측성**

  F1D journal과 CLI는 raw exception·로그·credential을 보존하거나 출력하지 않고, allowlist checkpoint,
  마지막 실패 checkpoint/UTC 시각, strict integer failure count, halt 상태만 durable하게 남긴다. extended
  journal reader는 네 diagnostic field가 모두 갖춰진 정확한 shape만 수용하고 기존 production base-v2는
  `null/null/0`으로 정규화한다. candidate action 직전 checkpoint fsync, failure evidence persistence,
  persistence 실패 뒤에도 유지되는 `finally` halt, original evidence를 덮지 않는 halt failure를 회귀로
  고정했다.

  PR #156(문서)과 PR #157(구현)을 병합하고 trusted Manager release를 n150에 설치했다. 동일 frozen
  candidate의 다음 F1D 실행에서 `prepared.contract.pinvi_smoke`와 failure count `1`을 안전하게 확인했다.
  마지막 시도만 분리한 결과 login·ETL·provider-sync는 `200`, configured cancel probe만 `404`였으므로,
  후속 F1J가 Map-owned fixture lifecycle을 구현한다.

## 2026-08-04 — T-049F durable Map writer-drain (issue #115)

- [x] **T-049F — durable Map writer-drain**

  Manager는 frozen Compose의 Map API one-shot private command만 `begin → attest → restore`
  체인으로 실행한다. journal은 `writers_draining`, `writers_drained`, `writers_stopping`과
  rollback `writers_restored` phase에 lease/receipt SHA-256을 fsync한다. pre-backup·superseded
  diagnostic recovery는 restore 뒤 exact previous pair re-attestation 전 writer/archive를
  허용하지 않으며, backup rollback은 Map Dagster webserver-only restore receipt 뒤 daemon을
  포함한 old runtime을 연다. `writers_restored` crash는 top-level rollback 재개로 old runtime
  activation까지 수렴하고, superseded recovery는 manifest identity를 Map restore보다 먼저
  대조한다. version `1` state는 자동 migration 없이 mutation 전에 거부하며 recreate한다.
  public GraphQL/token/일반 Compose bypass는 없다. strict Manager regression 148건과 actual
  ephemeral Docker Compose rehearsal 1건을 통과했고 n150/prod는
  사용하지 않았다.

  **병합 시 추가 확인·수정**(부모 세션, 2026-08-04): 이미 병합된 T-052(direct
  daemon stop 초안, PR #117)를 대체하므로, T-052가 남긴 `test_cache_target_cutover_gate.py`의
  v1 phase 픽스처(`writers_drained`·lease/receipt evidence 누락)를 v2 계약에 맞게
  고쳤다. 적대적 리뷰어 2명(레이스/crash-recovery/rollback 담당, secret 비노출/
  schema 검증 담당)을 추가로 돌렸다. 리뷰어가 **실제 결함**을 찾았다:
  `_validate_phase_evidence`(diagnostics·window 양쪽)가 phase 문턱으로만 검사해서,
  `writer_drain_restore_receipt_sha256`만 있고 그보다 먼저 있어야 할
  `writer_drain_lease_id`/`writer_drain_receipt_sha256`은 없는 논리적으로 불가능한
  journal이 어느 phase에서든 통과할 수 있었다 — phase와 무관한 무조건 검사를
  추가해 고쳤다(회귀 테스트 2건 추가). 리뷰어 1은 crash-recovery 전체가 "Map의
  `begin`이 owner_id 기준으로 idempotent하다"는, 이 저장소만으로는 검증 불가능한
  가정에 의존한다는 medium 등급 우려를 남겼다 — Map 쪽 구현 소유자 확인 필요
  (fix되지 않고 열린 채로 남김, kor-travel-map 쪽에 별도 확인 요청 필요).
  backend 전체 1580 passed, ruff/mypy clean(touched files).

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
| **T-037** | C6c Map UI 통합 경로 smoke 정렬 | 2026-07-27 | PR #67, `/ops/datasets` login/protected/logout lifecycle을 n150에서 확인 |
| **T-038** | Map destructive production 명시 승인 결선 | 2026-07-26 | PR #68, Map issue #796 closed, destructive live gate와 actor 감사 증거 완료 |
| **T-039** | C6c PinVi login SSR shell 판정 정렬 | 2026-07-27 | PR #69, route chunk HTTP smoke와 hydrated login form을 n150에서 확인 |
| **T-040** | C7 Map features routes production 명시 결선 | 2026-07-27 | PR #71, issue #70 closed, features route production live gate 완료 |
| **T-041** | C6c rollback image retention 보장 | 2026-07-27 | PR #73, issue #72 closed, active/rollback reference 가용성과 cleanup 성공 확인 |
| **T-021** | PR #36 후속 하드닝(신뢰 프록시 시크릿·brute-force durable·공개키 DB 직접조회·모달 a11y) | 2026-06-24 | AUTH-3/AUTH-6/APIKEY-1/FE-4, PR #38 머지·prod 검증 |
| **T-025** | 배포 런북 + push 전 보안 감사 절차 | 2026-06-24 | `deploy-runbook.local.md`(gitignore), AGENTS.md 절차·DO NOT #13/#14 |
| **T-042** | C7 WebSocket 종료 코드 계약(accept-then-close) 결선 | 2026-07-28 | PR #75, n150 프록시 경유 실브라우저에서 `4401`/`wasClean` 확인, settle 실측 |
| **T-033** | C7 Map UI·Dagster OCI revision 결선 | 2026-07-28 | issue #60(closed), n150 실행 중 Map 4종 image의 `org.opencontainers.image.revision`이 동일 40자 commit임을 실측 |
| **T-034** | C6c cAdvisor healthcheck 포트 계약 정렬 | 2026-07-28 | issue #62(closed), n150 cAdvisor healthy·`/healthz` 200 및 compatible-pair active 세대 정상 확인 |
| **T-035** | C7 Map production API 인증 env 결선 | 2026-07-28 | issue #63(closed), n150 Map API startup/readiness 및 service별 secret 격리 계약을 `docker exec env`로 실측 |
| **T-036** | C7 PinVi Dagster image 계약 정렬 | 2026-07-28 | PR #66, n150 `pinvi-dagster-latest` 9일째 healthy로 dependent bootstrap 완료 확인 |
| **T-012** | 대시보드 상세 패널 확장 | 2026-07-28 | PR #79, inspect 모달·5개 탭·dev ensure 버튼, 비밀 redaction 보강, 적대적 리뷰 2명 반영, 실브라우저 검증 완료 |
| **T-011** | 설정 저장 안정화 및 validation 고도화 | 2026-07-28 | PR #80/#81, diff 미리보기·baseline 인지 secret 방어 + 적대적 리뷰 2라운드(URL/비-URL 위조 변수명 우회, React key 포커스 유실) 수정 |
| **T-044** | ensure 라우트의 production 서버측 차단 | 2026-07-28 | PR #81, `ComposeService.ensure_target` production 전면 차단, 적대적 리뷰 2명 + 검증 통과 |
| **T-043** | WS 인가 동시성 상한 + 프론트 배포 preflight | 2026-07-28 | T-042 리뷰 후속(PR #76), n150 배포 후 1013 shed 실측(300 동시 접속 중 179건 shed) 및 preflight 확인 완료 |
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

### T-037: C6c Map UI 통합 경로 smoke 정렬

- [x] 제거된 `/ops/providers`를 smoke 대상에서 삭제하고 provider/job 운용 정본인
      `/ops/datasets`로 login `next`, 보호 GET, logout 뒤 재차단을 통합했다.
- [x] auth lifecycle 단위 테스트와 Docker 관리 문서를 같은 경로로 정렬하고 PR #67의
      단일 적대적 리뷰, backend/focused 테스트, Ruff, strict mypy와 CI를 통과했다.
- [x] 2026-07-27 n150 compatible-pair에서 실제 로그인, `/ops/datasets` 200, logout 뒤
      재차단을 확인했다.

### T-038: Map destructive production 명시 승인 결선

- [x] canonical `kor-travel-map-api`에
      `KOR_TRAVEL_MAP_API_DESTRUCTIVE_ENABLED=true`를 literal로 두고 raw/resolved/runtime에서
      API에만 존재하는지 fail-closed로 검증했다. standalone Map 기본값 `false`와 Manager의
      production 명시 승인을 분리했다.
- [x] compatible-pair manifest v4와 C7 attestation의 Map API environment hash가 이 승인을
      포함하도록 했고, destructive backup 감사 actor가 실제 인증 principal을 기록하도록
      Map issue #796의 OpenAPI/actor 변경과 함께 완결했다.
- [x] PR #68과 Map 후속 변경의 리뷰·CI를 통과했다. 2026-07-26 n150 C7 destructive live
      gate를 통과하고 Map issue #796이 closed된 것을 확인했다.

### T-039: C6c PinVi login SSR shell 판정 정렬

- [x] PinVi `/admin/login`의 `Suspense fallback={null}` client page는 SSR HTML에 form이 없을
      수 있으므로 HTTP smoke를 200·`text/html`·비어 있지 않은 body·일반 Next.js static
      marker·`admin/login` 전용 page chunk 계약으로 한정했다.
- [x] generic fallback이나 다른 route chunk는 계속 거부하고 hydration 뒤 form과 실제 로그인은
      browser smoke가 소유하도록 책임을 분리했다. PR #69의 단일 적대적 리뷰, focused/full
      테스트, Ruff, strict mypy와 CI를 통과했다.
- [x] 2026-07-27 n150 compatible-pair에서 HTTP shell 계약과 최종 Playwright hydrated login
      form·로그인 동작을 함께 확인했다.

### T-040: C7 Map features routes production 명시 결선

- [x] canonical `kor-travel-map-api`에
      `KOR_TRAVEL_MAP_API_FEATURES_ROUTES_ENABLED=true`를 literal로 두고 다른 service,
      `env_file`, build arg, command, label, config, secret 경로에는 이름 자체가 없도록
      raw/resolved/runtime 계약과 음성 회귀 테스트로 고정했다.
- [x] PR #71의 focused 42개, C6c·Docker config 849개, backend 907개, Ruff baseline 제외,
      strict mypy, canonical Compose gate, 단일 적대적 리뷰와 CI를 통과했다.
- [x] 2026-07-27 newer compatible-pair와 C7 live에서 feature 관리 REST를 확인했고 manager
      issue #70이 closed된 것을 확인했다.

### T-041: C6c rollback image retention 보장

- [x] PR #73(`c7328ed9`)에서 deploy/rollback 시작 전 manifest active/rollback과 candidate
      다섯 image를 service+전체 image SHA 기반 content-addressed reference로 보존하고 exact
      image ID를 재검증하도록 했다.
- [x] retention 실패는 첫 container mutation 전에 중단한다. manifest commit 뒤 새 rollback
      세대 밖 reference를 정리하고 cleanup residue는 다음 mutation 전에 해소하도록 했다.
- [x] moving tag rollover, 일부 tag 실패, wrong-ID collision, candidate 실패 정리,
      active=rollback dedupe, no-build/rollback/capture, post-commit cleanup pending 차단을
      실행형 회귀로 검증했다. retention 과정의 프로세스 `SIGKILL` 주입은 완료 증거로
      과장하지 않으며, 현재 완료 판정은 content-addressed 불변성·복구 보존·실운영 검증에
      근거한다.
- [x] 2026-07-27 n150 production에서 active/rollback retention reference의 가용성과
      cleanup 성공을 확인했다. issue #72가 closed된 것을 확인했다.

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

### T-033: C7 Map UI·Dagster OCI revision 결선

- [x] `kor-travel-map-api`, `kor-travel-map-ui`, `kor-travel-map-dagster`,
      `kor-travel-map-dagster-daemon`의 build가 모두 동일한 canonical
      `KOR_TRAVEL_MAP_GIT_COMMIT`을 Dockerfile에 전달하도록 compose를 정렬한다.
- [x] raw·resolved compose 계약이 네 service의 build arg·snapshot context·Dockerfile을
      exact 검증하고 일부 service 또는 revision이 다른 회귀를 첫 mutation 전에 차단한다.
- [x] candidate build가 Map runtime 네 image와 PinVi image를 모두 같은 frozen snapshot에서
      완성하고, 각 immutable image ID와 OCI revision을 manifest v4에 기록한다.
- [x] capture/deploy/rollback이 Map runtime 네 service를 같은 frozen transaction으로
      재생성·검증하며, 복원 실패 시 다섯 runtime을 모두 중지해 혼합 generation을 차단한다.
- [x] resolved fixture drift, candidate build service 누락, dependent image/revision mismatch,
      activation·rollback 누락에 대한 회귀 계약을 추가한다.
- [x] canonical v4 경로가 저장소 역사에 존재한 sibling `compatible-pair-v2.json`과
      `compatible-pair-v3.json`을 payload·file type과 무관하게 mutation 전에 fail-close하고,
      legacy bytes 불변과 Docker 미호출을 실행형 회귀로 고정한다.
- [x] n150 production에서 실제 기동 중인 `kor-travel-map-api`·`-ui`·`-dagster`·
      `-dagster-daemon` 네 컨테이너의 `org.opencontainers.image.revision` label을
      `docker inspect`로 직접 확인 — 네 image 모두 동일한 40자 commit
      (`c8ed6164381fccd35df1840427e5a682f2a2789d`)이었고, 이는 compatible-pair
      manifest v4의 `map_source_revision` 기록과도 정확히 일치했다. issue #60은 이미
      closed 상태였다.

### T-034: C6c cAdvisor healthcheck 포트 계약 정렬

- [x] canonical compose의 cAdvisor listen 포트와 명시적 `/healthz` healthcheck가
      모두 `CADVISOR_PORT`(기본 `12301`)를 단일 정본으로 사용하게 한다.
- [x] raw compose 계약이 exact `--port=${CADVISOR_PORT:-12301}`과 health URL을 고정하고,
      default/custom resolved config에서 listen·probe 포트가 같은지 검증한다.
- [x] n150 production에서 cAdvisor `healthy`와 설정 포트(`CADVISOR_PORT`) `/healthz` 200을
      직접 확인했다. compatible-pair manifest v4에 2026-07-27 기록된 `active` 세대가
      이미 존재하고 그 이후 계속 healthy 상태였다 — capture와 후속 readiness가 이미
      통과한 상태임을 확인했다. issue #62는 이미 closed 상태였다.

### T-035: C7 Map production API 인증 env 결선

- [x] ADR-23에서 admin BFF, API-only service/cursor, public/debug/profile, metrics 비활성 계약과
      service별 최소 주입 범위를 문서로 먼저 고정한다.
- [x] canonical Compose와 `.env.example`에 Map #780/#782 production 설정을 정확히 반영했다.
- [x] C6c raw/resolved/runtime preflight가 credential shape·상호 구분·허용 service exact set과
      production literal을 mutation 전에 검증하게 한다.
- [x] 누락·약한 값·재사용·다른 service 유출·설정 drift 음성 fixture를 추가했다.
- [x] 두 번째 적대적 리뷰 P1에 따라 manifest v4 exact 9-field shape 밖의 sibling 단조 marker를
      추가했다. 최초 v3/v3 logical manifest hash만 pending 재시도를 허용하고 성공 검증 뒤 complete로
      영구 닫아 A3→B4→rollback A3→C3 회전도 누락 예외를 다시 열지 못한다.
- [x] marker atomic write/fsync와 fixed shape, 0600 regular owner, corrupt/symlink/mode/owner 및 pending
      baseline drift fail-close 회귀 계약을 추가했다.
- [x] 두 번째 적대적 리뷰 P2에 따라 source Compose 전체 scalar tree에서 admin=API+frontend,
      service=API-only, cursor=v3 0회/v4 API exact 1회 외 모든 service/field leak를 거부했다.
- [x] 세 번째 적대적 리뷰 P2에 따라 profile/public/debug도 API-only exact path로 올리고,
      API·Dagster·daemon `env_file`의 known path/options와 tracked exact-revision 내용까지 검증한다.
- [x] 네 번째 적대적 리뷰 P2에 따라 tracked `env_file`을 exact `100644 blob`·64 KiB 이하·UTF-8로
      제한하고, 허용되지 않은 service의 `env_file: null` 우회도 차단했다.
- [x] `.env.example`의 세 공개 local placeholder를 production config/raw/resolved에서 각각 거부하고
      local 허용 회귀 계약을 추가했다.
- [x] 동일 적대적 리뷰어의 최종 P0~P2 없음 판정 뒤 backend 886개, 변경 파일 Ruff,
      strict mypy, 기본·커스텀 Compose gate를 통과했다.
- [x] PR을 merge한다.
- [x] n150 final v4 exact-pair에서 Map API startup/readiness(`kor-travel-map-api-latest`
      46시간째 healthy)와 runtime secret isolation을 `docker exec env`로 이름만
      확인(값은 출력하지 않음) — Map API에는 admin proxy·service token·cursor
      signing secret·features/destructive 플래그가 모두 존재, admin BFF인 Map UI에는
      정확히 admin proxy secret만 존재, Dagster/daemon에는 둘 다 부재 — 설계된
      격리 계약과 정확히 일치했다. issue #63은 이미 closed 상태였다.

### T-036: C7 PinVi Dagster image 계약 정렬

- [x] C7 exact PinVi source revision의 `apps/etl/Dockerfile`과 package metadata에서
      `DAGSTER_HOME=/opt/pinvi/.dagster`, code location `pinvi.etl.definitions` 계약을 확인한다.
- [x] canonical `pinvi-dagster` Compose가 image 계약을 과거 `tripmate` 경로로 덮어쓰지 않도록
      environment와 command를 정렬한다.
- [x] resolved Compose 회귀 테스트로 `DAGSTER_HOME`과 code location을 고정한다.
- [x] 적대적 리뷰 승인 뒤 focused/backend/Compose gate를 통과하고 PR #66을 병합한다.
- [x] n150 production에서 `pinvi-dagster-latest`가 9일째 healthy 상태로 실행 중임을
      확인했다 — C7 compatible-pair capture에서 PinVi dependent bootstrap이 이미
      완료된 상태임을 확인했다.

### T-012: 대시보드 상세 패널 확장

- [x] 컨테이너 row의 상세 버튼으로 inspect modal을 연다(`ContainerDetailModal`).
      기존 모달과 같은 `role="dialog"`/`aria-modal`/`aria-label` 패턴에 Esc 닫기와
      포커스 이동을 더했다.
- [x] mounts, networks, healthcheck, redacted env를 탭으로 분리한다
      (`role="tablist"`/`tab`/`tabpanel`과 `aria-controls`/`aria-labelledby` 연결).
- [x] target 단위 `ensure --build` 버튼을 개발 빌드에서만 제공한다. `containers`는 사실
      depends_on까지 펼쳐진 목록이라(통합 PostgreSQL은 db·geo·conc·map·pinvi·all 여섯
      target에 모두 있다) 첫 매치를 쓰면 `dependency_order` 순서에 의존하게 된다.
      순서와 무관하게 **가장 좁은** target을 고른다. 실행 전에는 실제 재생성 대상
      서비스 목록과 개수를 보여 주고 확인을 받는다(db 포함 시 스키마 스크립트 경고).
- [x] 모바일/데스크톱 반응형을 실브라우저로 검증한다. 390×844에서 page 가로 스크롤 0,
      모달이 viewport 안에 들어오고, 넓은 Mounts 표와 탭 목록은 각자의 컨테이너 안에서만
      가로 스크롤한다. 1440×900도 동일.
- [x] 로컬 실브라우저 검증: 18개 row 전부 상세 버튼 노출, 5개 탭이 실제 inspect 데이터를
      렌더(마운트 rw/ro, 네트워크 2개 IP/MAC/alias, healthcheck `healthy`+최근 검사 로그).
      콘솔 오류 0건(로그인 전 401과 기존 favicon 404 제외).
- [x] **적대적 리뷰 2명이 찾은 비밀 노출을 수정한다.** 이 패널이 inspect를 UI에 연결한
      최초 지점이라, 그동안 API/CLI에만 있던 redaction 공백이 브라우저 한 번의 클릭으로
      열렸다. key 이름 누락(`*_API_KEY`가 `ACCESS_KEY`에 안 걸림)과 값 내부 credential
      (`postgresql://user:password@`)을 모두 막고, `cmd`/`entrypoint`도 필터에 넣었다.
      실행 중 10개 컨테이너 전수 검사에서 유출 0건, `KTG_PG_DSN`은
      `postgresql+psycopg://addr:<redacted>@...`로 비밀번호만 가려지는 것을 확인했다.
- [x] 리뷰 지적 반영: WS broadcast(2초)마다 포커스를 빼앗던 effect 의존성 수정(7초간
      포커스 유지 확인), `aria-controls` dangling IDREF 4건 해소, tabpanel `tabIndex=0`,
      탭 좌우/Home/End 키 이동, 미생성·오프라인 컨테이너 버튼 비활성화, raw JSON 오류 문구
      교체, running 중 `종료 코드 0` 오표시 제거.

### T-011: 설정 저장 안정화 및 validation 고도화

- [x] host 네트워크 기준에서 설정 저장·reset·미생성 start fallback이 Docker SDK 직접 생성 경로를 우회하지 않고 `docker compose up --force-recreate`를 사용하도록 변경
- [x] compose 변경 전 diff 생성 및 UI 표시. 백엔드 호출 없이 프론트에서 baseline(`configTargetContainer.config`) vs
      현재 입력을 실시간 비교해 포트/네트워크 추가·삭제, env 변경 전후 값을 모달에 즉시 표시한다.
- [x] 포트, 볼륨, 네트워크 입력 validation 강화. 백엔드에 `docker_service.validate_container_config_update`를
      추가해 lock 획득·Docker 접근보다 먼저 검증하고(`ContainerConfigValidationError` → HTTP 422),
      프론트에도 같은 규칙을 미러링해 왕복 없이 즉시 피드백한다. 포트는 `${VAR:-default}` 보간 토큰을
      opaque하게 신뢰하고 리터럴 숫자만 1~65535 범위·host:container 형식을 검사한다(docker-compose.yml
      실제 ports 18개 전수 통과를 회귀 테스트로 고정). 볼륨은 이미 `compose_volume_graph_hash` 비교로
      완전히 불변 처리되어 있어(어떤 변경도 첫 mutation 전에 409) 새 검증을 추가하지 않고, 대신 프론트에서
      변경을 미리 감지해 제출 전에 경고하고 제출을 막는다.
- [x] secret 성격 값은 `.env` override로 저장하도록 안내 및 방어 로직 추가. 정적 key-이름 휴리스틱만 쓰면
      `KOR_TRAVEL_MAP_API_PUBLIC_API_KEY_REQUIRED=true`처럼 이름에 "API_KEY"가 들어 있지만 원래부터 리터럴
      불리언인 값까지 오탐으로 막는다(실제 compose 파일 전수 검증에서 발견). 그래서 "baseline이 이미
      `${...}` 보간이었는지"를 기준으로 삼는다 — 이미 `.env`로 분리돼 있던 참조를 리터럴로 되돌리는 것만
      막고, 원래부터 리터럴이던 값은 자유롭다(baseline을 모르는 신규 key에는 안전한 쪽으로 key-이름
      휴리스틱을 fallback으로 쓴다). key 이름과 무관하게 값 안에 literal 접속 자격증명
      (`scheme://user:pass@`)이 `${...}` 밖에 남아 있으면 별도로 거부한다 — `${OUTER:-postgresql://user:dev_pw@host/db}`처럼
      비밀번호까지 하나의 `${...:-default}` 안에 두는 이 저장소의 기존 관행은 통과시킨다.
- [x] config 파일 변경과 재생성을 같은 host lock transaction으로 묶고, recreate/init 실패 시 원본 byte와
      파일 mode를 원자 복원한 뒤 기존 runtime 재생성을 시도하는 rollback 전략 문서화
- [x] **적대적 리뷰 2명이 찾은 문제를 수정한다(1차 라운드).** (1) 리뷰어 1: `_value_has_literal_url_credential`가
      `${...}` 블록을 통째로 지우고 스캔해서, `${FAKE_NAME:-literal-secret}`처럼 지어낸 변수명으로
      감싸기만 해도 credential 스캔이 통째로 우회됐다 — raw 값을 그대로 스캔하고 password 캡처
      그룹 자체가 보간인 경우만 예외로 인정하도록, 그리고 credential 스캔을 baseline과 완전히
      같은 값에만 예외로 두도록 재작성했다. (2) 리뷰어 2: ports/volumes/networks 행의 React
      `key`에 필드 값 자체(`port-${idx}-${port}`)가 들어 있어 매 keystroke마다 DOM 노드가
      재마운트되어 포커스가 사라졌다 — index 전용 key로 교체(행은 추가/삭제만 되고 재정렬은
      없어 안전). 그 외 whitespace로 인한 오탐 메시지, `aria-describedby`/`aria-live` 접근성
      공백도 함께 수정. 두 버그 모두 리뷰어가 제시한 실제 입력으로 직접 재현 후 수정 확인,
      추가로 수정을 되돌리는 mutation test로 새 회귀 테스트 4건만 실패하는 것을 확인했다.
- [x] **1차 라운드 수정 직후 재검토에서 발견한 2차 보안 공백을 수정한다.** 1차 수정은
      `scheme://user:pass@` 형태(DSN)에만 반응하는 credential 스캔이라, URL이 아닌 단일 리터럴
      비밀(예: `GF_SECURITY_ADMIN_PASSWORD=${GRAFANA_ADMIN_PASSWORD:-admin}`)에는 적용되지
      않는다는 것을 재발견했다 — baseline이 이미 `${...}` 보간이어도, sensitive key의 값을
      `${TOTALLY_MADE_UP_NAME:-h4x0r-literal-secret}`처럼 지어낸 이름 + 새 리터럴 default로
      바꾸면 "여전히 보간 형태"라는 이유만으로 규칙 1(재보간 요구)을 통과했다(실제
      `docker-compose.yml`의 Grafana 서비스로 직접 재현). 규칙 1을 일반화해, sensitive key면
      `${...}` 형태를 유지해도 `:-default` 리터럴 자체가 baseline과 달라졌으면 거부하도록 했다
      (변수 이름만 바뀌고 default 리터럴이 동일하면 허용, default를 아예 없애는 것도 허용,
      비-sensitive key는 대상이 아님 — 포트 기본값 변경 등 정상 편집을 막지 않는다). 백엔드
      회귀 테스트 5건 추가 + mutation test로 새 테스트 2건만 실패 확인, 전체 1047 backend
      테스트 통과, 프론트 `configValidation.ts`에 동일 로직 미러링 후 실브라우저(WSL dev
      backend/frontend, 로그인 세션)에서 Grafana 컨테이너의 `GF_SECURITY_ADMIN_PASSWORD`
      필드에 직접 재현 입력을 타이핑해 차단 메시지·diff 미리보기·제출 버튼 비활성화를
      확인했다(실제 컨테이너 재생성은 트리거하지 않음). 같은 세션에서 포트 필드에 여러
      글자를 연속 입력해 React key 수정으로 포커스가 유지되는 것도 함께 재확인했다.

### T-044: ensure 라우트의 production 서버측 차단

T-012 적대적 리뷰에서 확인한 공백이다. `POST /targets/{target}/ensure`는 production에서도
비-C6c target을 막지 않는다. `assert_manager_mutation_allowed`는 환경 선언의 정합성만
검증하고 문자열을 돌려주며, `assert_c6c_mutation_allowed`는 대상 service가 C6c
runtime(Map 4종·pinvi-api)과 겹치지 않으면 그대로 반환한다. 따라서 db·storage·gra·cadv·
prom·geo·conc는 통과한다.

현재는 프론트의 `NODE_ENV` 분기(빌드 타임 제거)가 **유일한** 방어선인데, 이는 브라우저
번들의 속성이지 백엔드의 속성이 아니다. `npm run dev` 프론트를 운영 백엔드에 붙이면
버튼이 보이고 실제로 실행된다.

- [x] `ComposeService.ensure_target`이 `assert_manager_mutation_allowed`의 반환값(`mode`)을
      받아, `assert_c6c_mutation_allowed` 호출 직후(`c6c_deployment_lock` 안, baseline 검사와
      첫 Docker subprocess보다 먼저) `mode == "production"`이면 target·service 구성과 무관하게
      전면 차단하도록 했다. `assert_c6c_mutation_allowed`·`assert_compose_mutation_allowed`·
      `control_container`·`update_container_config`·`reset_container_config`는 건드리지
      않는다 — 그 경로들의 "비-C6c target은 production에서도 허용" 동작은 개별 컨테이너
      제어를 위한 의도된 동작이라 범위 밖이다(`ensure`만 target 전체를
      `--build`/`--force-recreate`+init step까지 허용하는 범용 dev 부트스트랩이라 다르다).
      `DeploymentContractError` → 기존과 동일하게 HTTP 409, 라우트 코드 변경 없음.
      CLI(`ktdctl ensure`)도 같은 메서드를 호출하므로 동일하게 막힌다.
- [x] 음성 회귀 테스트 2건 추가: production + 비-C6c target(`storage`) → 거부(`subprocess.run`
      미호출까지 확인), local/개발 모드에서는 동일 target의 정상 `ensure` 흐름이 막히지
      않는지 양성 대조로 확인. 기존 `test_production_generic_mutation_guard_rejects_every_api_entrypoint`
      (C6c target `map`은 `assert_c6c_mutation_allowed`로 이미 차단)와 공존하며 서로
      가리지 않는 것도 확인.
- [x] **적대적 리뷰 2명 + 독립 검증 통과.** 리뷰어 1은 `mode` 판정이
      `_validate_mutation_environment`(local/production만 반환하는 fail-closed 계약)에
      전적으로 의존해 우회 불가능함을, `ensure_target`/`_ensure_target_unlocked`의 호출자가
      API 라우트와 CLI 단 둘뿐임을(다른 라우트·WS·스케줄러 경로 없음) 확인했다. 리뷰어 2는
      두 production 차단(C6c 전용·신규 전면 차단)이 서로 마스킹하지 않고 공존함을, 실제로
      의존하는 production `ensure` 사용처가 문서상 없음을 확인했다. 두 리뷰 모두 raise 메시지가
      비-C6c target에서만 도달하는 이 지점에 compatible-pair(Map/PinVi 전용) 문구를 넣은 것이
      부적절하다고 지적해 메시지를 단순화했고("manage this service directly on the host
      instead"), 테스트 하나에 실제로는 읽히지 않는 `monkeypatch.setenv` 호출이 남아 있던 것도
      제거했다. 검증 단계에서 두 리뷰의 모든 구체적 주장을 코드에서 직접 재확인(CONFIRMED)했고
      우회·회귀는 발견되지 않았다. backend 1049 passed, ruff 기존 9건 유지, 변경 파일 mypy
      clean. 백엔드 정책 변경만이라 UI 변경이나 실브라우저 E2E는 필요 없다.

### T-043: WS 인가 동시성 상한 + 프론트 배포 preflight

T-042의 적대적 리뷰가 남긴 두 항목이다. accept-then-close 계약상 미인증 peer도 handshake를
완료하는데 WS 라우트에는 제한이 없었고, 운영 호스트의 `frontend/node_modules`가 부분 설치
상태로 남아 배포 중 빌드가 실패할 수 있었다.

- [x] 동시 인가 handshake를 `KTDM_WS_MAX_PENDING_AUTHORIZATIONS`로 묶고 초과분을
      `close(1013)`으로 흘려보낸다. per-IP를 쓰지 않는 이유(프록시 IP 단일화)를 함께 기록한다
- [x] 서버를 내리기 전에 프론트 툴체인을 검증하는 `scripts/verify-frontend-toolchain.sh`를
      추가한다(`npm ls --depth=0`이 결정적 게이트)
- [x] 적대적 리뷰 2명 반영 — 잘못된 위협 모델(미인증 peer는 SQLite에 도달하지 못한다),
      shed 경로가 더 비쌌던 문제(거절당 로그 write), `--limit-concurrency` 오권고,
      counter 증가를 검증하지 않던 테스트 공백을 모두 수정
- [x] n150 배포 후 1013 shed 동작과 preflight를 운영에서 확인했다. `backend/src`·
      `frontend/src`를 rsync(보존 파일 제외)한 뒤 백엔드 프로세스와 프론트
      `next start -p 12905` 프로세스 그룹만(호스트에 공존하는 다른 앱들은 미접촉)
      재기동했다(`/health` 200, `next start` `Ready in 947ms`). `scripts/verify-frontend-toolchain.sh`
      실행 결과 `툴체인 정상`. 실제 production `/api/v1/ws/status`에 유효한 Origin
      헤더로 300개 동시 미인증 WebSocket 연결을 발생시켜 관측: `4401`(AUTH_REQUIRED) 121건,
      `1013`(TRY_AGAIN_LATER, shed) 179건 — 기본 상한(64)을 넘는 동시 인가 시도가 정확히
      shed되는 것을 실측으로 확인했다. 테스트 뒤 `/health` 200 유지, 다른 컨테이너 전부
      기존 uptime 그대로 영향 없음을 확인했다.

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
