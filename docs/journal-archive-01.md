# JOURNAL 아카이브 01 — 항목 제목 날짜 2026-06-10 ~ 2026-10-04

이 파일은 [`docs/journal.md`](journal.md)의 아카이브다(2026-10-16 분리). 파일 목록은 `journal.md` 상단 "과거 기록 아카이브" 표에 있다. 본문은 이동 당시 byte 그대로이며(원래 파일 순서 — 대체로 최신이 위지만 엄격한 시간순은 아니다) 아무 내용도 고치지 않는다.

---

## 2026-06-13 (`geo -> conc -> map -> pinvi` target 흐름 반영)

- **작업 내용**:
  - 사용자 지시에 따라 앱 target 순서를 `geo -> conc -> map -> pinvi`로 재정렬했다.
  - `kor-travel-concierge` API/MCP/Scheduler/Web UI를 `conc` target의 실제 compose service로 추가했다.
  - `kor-travel-map` API/Dagster/Web UI를 `map` target의 실제 compose service로 추가해 `ktdctl map --build`가 이미지를 빌드하고 실행하도록 변경했다.
  - PinVi API/Web UI를 `pinvi` target으로 추가하고 짧은 별칭 `srv`와 기존 호환 별칭 `main`을 연결했다.
  - 공용 DB 복구에 `krtour_map_dagster` database를 추가하고, RustFS bucket 복구에 `kor-travel-concierge` bucket을 추가했다.
  - CLI 직접 alias 처리를 registry 기반으로 바꿔 `conc`, `srv` 같은 새 alias가 자동 반영되게 했다.
- **검증**:
  - `docker compose config --quiet` 통과.
  - `scripts/ensure-kor-travel-geo-db.sh`, `scripts/ensure-rustfs-buckets.sh` `bash -n` 통과.
  - `PYTHONPATH=src backend/pinvi_venv/bin/python`으로 registry 해석 확인: `map`은 `db -> storage -> gra -> cadv -> prom -> geo -> conc -> map`, `srv`는 `... -> pinvi`로 resolve.

---

## 2026-06-13 (`kor-travel-geo` UI Prometheus scrape 추가)

- **작업 내용**:
  - `kor-travel-geo-ui`의 Next.js Prometheus endpoint(`/api/metrics`)를 scrape하도록 `kor-travel-geo-ui:12505` target을 추가했다.
- **검증**:
  - `docker compose config` 통과.
  - `config/prometheus/prometheus.yml`에서 `kor-travel-geo-api:12501`, `kor-travel-geo-ui:12505` scrape target 확인.

---

## 2026-06-13 (Grafana/cAdvisor/Prometheus target 개별 분리)

- **작업 내용**:
  - 단일 관측 target을 제거하고 `gra`, `cadv`, `prom`을 독립 CLI/API target으로 분리했다.
  - dependency 순서를 `db -> storage -> gra -> cadv -> prom -> geo -> map -> ai -> main`으로 조정했다.
  - Grafana는 공용 연계를 위해 `12205`, cAdvisor는 `12301`, Prometheus는 `12401`을 사용하도록 재배치했다.
  - `kor-travel-geo` API/Web UI는 새 dependency 순서에 맞춰 `12501`, `12505`로 이동했다.
  - Prometheus scrape target을 `kor-travel-geo-api:12501/metrics`로 갱신했다.
  - CLI 직접 별칭, API/CLI 테스트, 포트 문서, Docker 관리 문서, 개발 가이드를 같은 기준으로 갱신했다.
- **결정 사항**:
  - Grafana, cAdvisor, Prometheus compose service는 서로 `depends_on`으로 묶지 않고 독립 실행 가능하게 둔다.
  - `geo` 이상 target은 새 dependency 순서상 관측 컨테이너를 선행 실행한다.
- **검증**:
  - WSL `backend/pinvi_venv`에서 `ruff check .` 통과.
  - WSL `backend/pinvi_venv`에서 `pytest` → 22 passed.
  - WSL 프론트엔드에서 `npm run type-check`, `npm run build` 통과.
  - WSL Docker에서 Grafana `12205`, cAdvisor `12301`, Prometheus `12401`, `kor-travel-geo` API `12501`, Web UI `12505`로 재기동 완료.
  - HTTP 확인: Grafana `/api/health` 200, cAdvisor `/healthz` 200, Prometheus `/-/ready` 200, Geo API `/v1/healthz` 200, Geo UI `/` 307, RustFS `/health/live` 200.

---

## 2026-06-13 (`kor-travel-geo` DB명·환경변수·Prometheus scrape 계약 동기화)

- **작업 내용**:
  - 사용자 지시에 따라 `kor-travel-geo`가 현재 사용하는 DB명과 환경변수 계약에 Docker manager compose를 맞췄다.
  - `kor-travel-geo` DB 기본값을 `kor_travel_geo`로 변경했다.
  - manager override 변수는 `KOR_TRAVEL_GEO_*`로, API/UI 컨테이너 내부 환경변수는 앱이 읽는 `KTG_*`로 변경했다.
  - Docker service/container 이름과 target registry를 `kor-travel-geo-*` 기준으로 변경했다.
  - `/home/digitie/kor-travel-geo-data`를 PostgreSQL, RustFS, Prometheus, Grafana의 물리 데이터 디렉터리 기준으로 반영했다.
  - `kor-travel-geo` RustFS bucket 기본값을 `kor-travel-geo`로 맞췄다.
  - Prometheus scrape 설정에 `kor-travel-geo-api` job을 추가했다.
- **결정 사항**:
  - 기존 bind mount와 compose label을 가진 manager 스택 컨테이너는 중지 후 제거하고, 새 compose 기준으로 재생성 가능한 상태로 둔다.
  - 물리 데이터 디렉터리 이름도 프로젝트 공식명과 맞춘다.
- **검증**:
  - WSL Docker에서 manager 스택 컨테이너를 중지·제거하고 `/home/digitie/kor-travel-geo-data`로 데이터 디렉터리 이동 완료.
  - 과거 geo 이름 계열 문자열 검색 결과 없음.
  - `bash -n scripts/ensure-kor-travel-geo-db.sh scripts/verify-kor-travel-geo-source.sh scripts/ensure-rustfs-buckets.sh` 통과.
  - `docker compose config`에서 `KTG_PG_DSN=postgresql+psycopg://addr:addr@kor-travel-geo-postgres:5432/kor_travel_geo`, `POSTGRES_DB=kor_travel_geo` 확인.
  - `git diff --check` 통과.
  - WSL `/tmp/ktdm-venv` 임시 가상환경에서 `ruff check .` 통과.
  - WSL `/tmp/ktdm-venv` 임시 가상환경에서 `pytest` → 22 passed.
  - 프론트엔드 `npm run type-check` 통과.
  - `npm run lint`는 Next.js ESLint 초기 설정 프롬프트로 비대화형 실행이 중단됨.
  - `npx react-doctor@latest . --offline --verbose --json`은 실패 없이 완료했으며 기존 Next.js 14 보안 경고와 `DashboardClient` 구조성 경고 4건을 보고.

---

## 2026-06-13 (Kor Travel Docker Manager 프로젝트명 전환)

- **작업 내용**:
  - 프로젝트 공식명을 `Kor Travel Docker Manager` / `kor-travel-docker-manager`로 바꾸고 문서, package metadata, 프론트엔드 metadata를 동기화했다.
  - 백엔드 import package를 `kor_travel_docker_manager`로 변경하고 ASGI entrypoint 문서를 `kor_travel_docker_manager.main:app`으로 갱신했다.
  - CLI console script를 `ktdctl`로 전환하고 이전 CLI 명령 안내를 제거했다.
  - Docker Compose project name을 `kor-travel-docker-manager`로 고정해 network prefix를 새 프로젝트명 기준으로 통일했다.
- **결정 사항**:
  - 이전 CLI 이름을 병행 제공하지 않고 `ktdctl`만 공식 인터페이스로 둔다.
  - GitHub 저장소명은 코드 변경 PR 병합 후 `kor-travel-docker-manager`로 rename한다.

---

## 2026-06-13 (과거 이름 helper 제거 및 rebase 재검토)

- **작업 내용**:
  - `origin/main` 기준 최신 머지 상태를 확인한 뒤 `agent/remove-old-name-helper` 브랜치에서 재검토했다.
  - 과거 프로젝트명 기반 target alias와 fallback env 검색을 재수행하고, 남은 문서 표현과 UI 기본 표시명을 `kor-travel-geo`, `kor-travel-concierge` 기준으로 정리했다.
  - target을 중복 하드코딩하던 보조 shell helper를 제거하고, 공식 실행 경로를 `ktdctl` CLI와 API/dashboard registry로 단일화했다.
  - `ktdctl gra`, `ktdctl cadv`, `ktdctl prom`, `ktdctl all`도 직접 `ensure`로 해석되도록 CLI 직접 target 목록을 registry target과 맞췄다.
- **결정 사항**:
  - 과거 이름 수용 목적 alias/fallback/helper는 유지하지 않는다.
  - 실제 Docker container/service 이름과 물리 데이터 디렉터리는 후속 작업에서 `kor-travel-geo` 기준으로 맞춘다.

---

## 2026-06-13 (Prometheus/Grafana/Exporter 관측 스택 분리)

- **작업 내용**:
  - `docker-compose.yml`에 Prometheus, Grafana, cAdvisor Exporter를 각각 별도 Docker service로 추가했다.
  - 포트 정책에 맞춰 Grafana, cAdvisor Exporter, Prometheus를 각각 관리 컨테이너로 등록했다.
  - `config/docker-targets.yml`에 `grafana`, `prometheus`, `cadvisor` 관리 컨테이너를 등록했다.
  - Prometheus scrape 설정(`config/prometheus/prometheus.yml`)과 Grafana Prometheus datasource provisioning을 추가했다.
  - 관리 UI 목록에서 Prometheus, Grafana, cAdvisor Exporter가 역할별 아이콘과 표시명으로 구분되도록 프론트엔드 표시 로직을 보강했다.
  - `.env.example`, `docs/architecture.md`, `docs/docker-management.md`, `docs/ports.md`, `docs/decisions.md`, `docs/tasks-done.md`를 같은 기준으로 갱신했다.
- **결정 사항**:
  - Exporter는 Docker 컨테이너 리소스 메트릭에 적합한 cAdvisor를 사용하고, Grafana는 Prometheus datasource를 자동 등록한다.
  - `all` target에는 관측 스택까지 포함해 전체 로컬 인프라 실행 시 함께 올라가도록 한다.

---

## 2026-06-12 (태스크 장부 정리 및 kor-travel-concierge 선행 작업 등록)

- **작업 내용**:
  - 완료된 `T-001`~`T-010`, `T-013`~`T-016`을 `docs/tasks-done.md`로 분리하고, `docs/tasks.md`에는 진행 중/대기 작업만 남겼다.
  - 미완료 작업 `T-011`, `T-012`를 유지하고, `kor-travel-concierge` provider 상세 구현 및 명칭 전환을 `T-220` 선행 작업으로 등록했다.
  - 사용자 지정 순서인 `T-221`, `T-222`, `T-223`을 `T-220` 이후 순차 진행 항목으로 추가했다.
- **결정 사항**:
  - `T-221` 착수 전 `kor-travel-concierge` 잔여 명칭과 `pinvi` 직접 의존 설명을 먼저 정리한다.
  - `T-221`~`T-223`의 세부 범위는 현재 `kor-travel-docker-manager` 저장소 장부에 없으므로, `T-220` 완료 후 작업 전 상세 항목을 확정한다.

---

## 2026-06-12 (`kor-travel-geo` Docker API/UI 관리 편입)

- **작업 내용**:
  - `docker-compose.yml`에 `kor-travel-geo-api`, `kor-travel-geo-ui` 서비스를 추가해 `kor-travel-geo` REST API와 admin Web UI를 manager에서 함께 실행할 수 있게 했다.
  - `config/docker-targets.yml`에 `kor-travel-geo-api-latest`, `kor-travel-geo-ui-latest`를 공식 관리 컨테이너로 등록하고 `geo` 이상 target에 포함했다.
  - 포트 정책에 맞춰 API와 Web UI 포트를 배정하고, API 컨테이너가 compose 네트워크의 `kor-travel-geo-postgres:5432`, `rustfs:9000`을 사용하도록 설정했다.
  - `.env.example`, `docs/docker-management.md`, `docs/architecture.md`, `docs/ports.md`, `docs/dev-environment.md`, `README.md`, `docs/tasks.md`를 같은 기준으로 갱신했다.
- **결정 사항**:
  - 기존 `kor-travel-geo` 로컬 script와 같은 컨테이너 이름(`kor-travel-geo-api-latest`, `kor-travel-geo-ui-latest`)을 사용해 대시보드와 CLI가 기존 Docker 대상을 그대로 확인할 수 있게 한다.

---

## 2026-06-12 (WSL/Windows 실행 위치 정책 고정)

- **작업 내용**:
  - `git` 명령은 Windows 호스트에서만 실행하고, 패키지 설치·Docker·서버 실행·빌드·테스트·파일 검색 등 일반 개발 명령은 WSL에서만 실행하도록 문서화.
  - Playwright E2E는 실제 Windows 브라우저 환경 확인을 위한 명시 예외로 Windows 호스트에서 실행하도록 고정.
  - `AGENTS.md`, `SKILL.md`, `docs/dev-environment.md`, `CLAUDE.md`, `docs/tasks.md`에 실행 위치 정책을 반영.
- **결정 사항**:
  - Windows 경로가 문서에 나오더라도 git과 Playwright E2E를 제외한 명령 실행은 `/mnt/f/...` WSL 경로를 사용한다.

---

## 2026-06-12 (Kor Travel/PinVi 전용 Docker Manager CLI/API 및 문서 정리)

- **작업 내용**:
  - **통합 DB 모델 공식화**: `kor-travel-geo-postgres:5432` 하나에 `kor_travel_geo`, `pinvi`, `kor_travel_concierge`, `krtour_map` database를 담는 현재 구조를 공식 기준으로 문서화하고, 과거 분리 DB 기준 문구를 정리.
  - **target registry 도입**: `db`, `storage`, `geo`, `map`, `ai`, `main`, `all` target을 API/CLI가 공유하도록 정의.
  - **Python CLI 추가**: `ktdctl targets/status/ensure/logs/action/inspect` 명령을 추가하고, 개발환경에서 `ktdctl <alias> --build`로 의존 Docker를 바로 실행할 수 있게 함.
  - **짧은 CLI 별칭 추가**: `db`, `storage`, `gra`, `cadv`, `prom`, `geo`, `map`, `ai`, `main`을 공식 별칭으로 두고 `config/docker-targets.yml`의 dependency 순서를 따라 누적 실행하도록 구현.
  - **포트 정책 일원화**: PostgreSQL host 포트를 `5432`로 변경하고, RustFS는 `12101`/`12105`, manager API/Web은 `12901`/`12905`로 정리.
  - **초기화/복구 step 추가**: 통합 DB database/role/schema/extension 복구, RustFS bucket 복구, `kor-travel-geo` 원천 디렉터리와 핵심 테이블 적재 검증을 `ensure` 흐름에 연결.
  - **API 확장**: `GET /api/v1/targets`, `POST /api/v1/targets/{target}/ensure`, `GET /api/v1/containers/{container_id}/inspect`를 추가.
  - **Docker inspect redaction**: inspect 응답에서 password, secret, token, access key 계열 environment 값을 마스킹하도록 구현.
  - **문서 보강**: `docs/docker-management.md`를 신규 작성하고, `architecture`, `decisions`, `tasks`, `dev-environment`, `README`, 에이전트 가이드를 통합 DB/CLI 기준으로 갱신.
- **결정 사항**:
  - Docker 생명주기와 `--build`는 `docker compose` 인자 배열 실행으로 처리하고, stats/logs/inspect/action은 Docker SDK를 유지한다(ADR-7).
  - target alias와 초기화 step은 `config/docker-targets.yml`을 source of truth로 삼는다(ADR-8).
- **다음 작업**:
  - 대시보드 상세 패널에서 inspect API를 연결하고, compose 설정 변경 전 diff/validation을 강화한다.

## 2026-06-11 (WSL 네트워크 연결 복구 및 월 단위 로그 롤링 구현)

- **작업 내용**:
  - **WSL 가상 IP 통신 거부 결함 최종 해결**: 브라우저에서 `172.26.51.35:9091`로 백엔드 API에 접속 시, 포트 9091이 윈도우 프로세스(Firefox 등)의 좀비 커넥션 및 WSL2 포트 맵 꼬임으로 인해 접근 거부되던 현상을 해결. Windows powershell에서 WSL을 강제 종료(`wsl --shutdown`) 및 가상 어댑터를 리셋하여 9091 바인딩 꼬임 문제를 완벽히 해결 및 연결 정상 복구 완료.
  - **월 단위 로그 파일 롤링 및 1년 보관 로직 추가**: uvicorn 서버의 작동 로그 출력을 매월 1일 단위로 분할하여 `kor_travel_docker_manager.log.YYYY-MM` 형태로 백업하고, 1년(365일)이 지난 로그 파일을 자동으로 탐색하여 청소하는 백그라운드 클린업 스레드를 추가하여 로깅 유지 비용 제어.
  - **백엔드 가상환경 재구축 및 WebSocket 라이브러리 추가**: 기존 `.venv` 가상환경 내에 WebSocket 구동에 필수적인 `websockets` 라이브러리가 누락되었고, 파일 락(Lock) 및 패키지 찌꺼기로 인해 pip 설치가 교착 상태에 빠지던 이슈를 발견. Windows PowerShell을 통해 기존 가상환경을 강제 제거하고, WSL Python 3.12를 기반으로 하는 수동 가상환경을 깨끗하게 재구축한 뒤 `websockets`, `fastapi` 등의 필수 의존성을 완벽하게 재설치 완료.
  - **백엔드 실행 경로 매핑 및 PYTHONPATH 주입**: 백엔드 수동 기동 시 `PYTHONPATH=src` 환경 변수를 주입하여 uvicorn이 `kor_travel_docker_manager` 패키지 모듈을 바르게 탐색할 수 있도록 조정했다.
  - **대시보드 UI 글씨 크기 조정**: 테이블 컬럼 제목의 폰트 크기를 `text-[10px]`에서 `text-xs md:text-sm`으로 키우고, 테이블의 각 셀 내용(상태, 명칭, 역할, 포트 바인딩, 리소스 수치) 및 리차트(Recharts) 기반 그래프의 틱(Ticks), 범례(Legend), 툴팁(Tooltip)의 폰트 크기를 1~2px씩 일제히 상향하여 시인성 대폭 개선.
- **결정 사항**:
  - WSL 환경과의 통신 결함을 방지하기 위해 백엔드 접속 주소는 `localhost:9091`을 기본값으로 사용한다. (다만 가상 IP 바인딩을 활용하는 경우 프론트엔드가 환경에 맞추어 `http://172.26.51.35:9091`로 수동 통신하도록 .env.local을 구성한다.)
  - 가상환경 락 이슈 해결을 위해 캐시 및 락 찌꺼기가 남은 기존 `.venv`를 우회하는 수동 가상환경을 구축하여 사용한다.

## 2026-06-11 (대시보드 M 룩앤필 교정, CSS 링크 결함 수정 및 react-doctor 최적화 완료)

- **작업 내용**:
  - **디자인 가이드 대시보드 이식 및 교정**: 대시보드 메인 화면 상단에서 부적절한 자동차 피트라인 배경 이미지(`/images/pit_lane_night.png`) 및 억지스러운 모터스포츠 비유를 완전히 배제하고, Pure Black 배경과 얇은 hairline border 및 4px M 삼색선 디바이더로 구성된 실용적인 IT 인프라 대시보드 룩앤필로 정교화 및 수렴.
  - **CSS 폰트 로드 링크 결함 수정**: `next/font/google`을 활용한 폰트 로드를 완료하고, `layout.tsx`의 body에 `font-sans`를 명시적으로 매핑하여 런타임 상의 CSS 폰트 링크 깨짐을 완전히 차단.
  - **아키텍처 리팩토링 및 react-doctor 경고 제거**: `page.tsx`를 Server Component로 전환하여 메타데이터를 노출하고, 1,025라인의 대형 컴포넌트를 `src/components/DashboardClient.tsx` (Client Component)로 완벽히 분리. 또한 dynamic import(recharts), aria-label(접근성), stable key(key={idx} 대체), useMemo(derived state 제거), WebSocket 마운트 state 최적화를 적용하여 `react-doctor` 경고 25건을 모두 해결.
  - **디자인 시스템 문서화**: 디자인 시스템 적용 범위를 실제 대시보드의 테이블, 모달, 차트 모듈 사양으로 갱신하여 디자인 일관성 가이드를 강화.
  - **포트 확정 및 적용**: API 구동 포트를 9091로, WEB 구동 포트를 9092로 최종 확정하고, 소스코드(main.py, DashboardClient.tsx, env) 및 문서(CLAUDE.md, dev-environment.md)에 일제히 동기화 반영 완료.
- **결정 사항**:
  - 디자인 일관성 및 코드 품질 향상을 위해 서버-클라이언트 컴포넌트 분리 및 react-doctor 최적화 규칙을 반영함 (ADR-6).

## 2026-06-11 (실시간 컨테이너 모니터링 테이블, WebSocket 및 성능 차트 구현)

- **작업 내용**:
  - **백엔드**: `main.py`의 lifespan 동작 시 `metrics_service` 임포트 누락으로 인해 `NameError`가 발생하던 결함을 발견하고, `from kor_travel_docker_manager.services.metrics_service import metrics_service`를 임포트 목록에 추가하여 해결.
  - **백엔드**: SQLite3 데이터베이스 연동(`metrics_service.py`) 및 10초 주기 Docker stats 메트릭 수집기(`metrics_collector.py`) 구현. 최신 리소스 캐시 및 30일 만료 규칙 적용.
  - **백엔드**: WebSocket 라우트(`websocket.py`) 구현. `/api/ws/status`를 통한 상태/메트릭 실시간 브로드캐스트 및 `/api/ws/logs/{container_id}`를 통한 컨테이너 로그 스트리밍 제공.
  - **백엔드**: 지난 1시간의 수집 기록을 조회하는 GET `/api/containers/{container_id}/metrics` API 추가.
  - **프론트엔드**: 기존의 컨테이너 카드 뷰를 Premium Glassmorphic Table 형태로 전면 개편(`page.tsx`).
  - **프론트엔드**: WebSocket 실시간 상태 동기화 및 끊김 시 5초 폴링 Fallback 로직 연동.
  - **프론트엔드**: 터미널 스타일 로그 스트리밍 모달 다이얼로그 및 Recharts 기반의 1시간 리소스 이력 라인 차트 모달 기능 추가.
- **결정 사항**:
  - 실시간 리소스 모니터링 및 로그 스트리밍을 제공하기 위해 WebSockets 아키텍처를 도입하고, 기존 TanStack Query를 Fallback용으로 하이브리드 운영.
- **다음 작업**:
  - 개별 컨테이너 환경설정 업데이트 동작 확인 및 최종 사용자 테스트.

## 2026-06-10 (kor-travel-geo PostgreSQL/RustFS 인프라 이관)

- **작업 내용**:
  - `docker-compose.yml`에 `kor-travel-geo` 전용 `kor-travel-geo-postgres` 서비스를 추가하고, 기존 T-027 최종 DB 접속 계약을 `kor-travel-docker-manager` 기본 설정으로 이관했다.
  - 공용 RustFS 서비스의 포트, credential, 데이터 디렉터리, bucket 초기화를 `.env.example`과 compose에 명시하고 `kor-travel-geo` bucket을 함께 생성하도록 했다.
  - 초기 helper 명령을 추가해 `up/stop/restart/status/logs`를 주요 target 단위로 실행할 수 있게 했다.
  - 백엔드/프론트엔드 대시보드가 당시의 PostgreSQL/RustFS 관리 대상을 표시하도록 갱신했다.
- **결정 사항**:
  - PostgreSQL/RustFS Docker 생명주기와 로컬 포트 계약은 `kor-travel-docker-manager`가 관리한다(ADR-5).
- **다음 작업**:
  - compose live smoke와 대시보드의 compose create 액션 확장 여부를 후속으로 검토한다.

## 2026-06-10 (인프라 매니저 프로젝트 초기화 및 가이드라인 복사)

- **작업 내용**:
  - `maplibre-vworld-js` 저장소를 기반으로 AI 에이전트 개발 및 협업 가이드라인 (`CLAUDE.md`, `AGENTS.md`, `SKILL.md`) 복사 및 `kor-travel-docker-manager` 목적에 맞게 수정.
  - 에이전트 설정 파일 (`antigravity.json`, `claude.json`, `codex.json`, `.gemini/mcp.json`, `.claude/settings.local.json`, `.codex/config.toml`) 설정 완료.
  - 아키텍처 가이드(`docs/architecture.md`) 및 의사결정 기록(`docs/decisions.md` ADR-1 ~ ADR-4) 신규 생성.
  - 백로그 작업 시스템(`docs/tasks.md`) 및 환경 구축 문서(`docs/dev-environment.md`) 작성.
- **결정 사항**:
  - Python FastAPI 백엔드 + Next.js 프론트엔드의 모노레포 구조(ADR-1) 채택.
  - Docker Container 제어를 위해 Python Docker SDK(ADR-2) 채택.
  - 상태 동기화를 위해 TanStack Query(ADR-3) 및 Polling 방식 사용.
- **다음 작업**:
  - 루트 `.gitignore`, `docker-compose.yml`, `README.md` 작성.
  - 백엔드 (`backend/`) Poetry 초기화 및 FastAPI 뼈대 코드 작성.
  - 프론트엔드 (`frontend/`) Next.js 뼈대 코드 및 실시간 상태 대시보드 UI 구현.
## 2026-08-28 — M05 terminal registry의 원문 없는 phase 진단

Map `053904ce…`·PinVi `1b29bfea…`·Manager `8f41a9bd…`를 `rotate-pair`로 결박한 뒤 trusted
`run-pinned-rebuild-once`와 public generation gate를 통과했다. 그 다음 isolated M04/M05 one-shot은 정확히
한 번 실행되어 canonical pinset `5ad3b08c…`을 terminal로 차단했다. output leaf·HTTP 원문·container log·환경값은
열지 않는다. root registry의 공개 reason은 이제 임의 예외 문자열이 아니라 allowlist fixed phase만 기록한다.
따라서 다음 immutable source pair는 raw artifact 없이도 보정 범위를 알 수 있고, 같은 pinset의 재실행은 계속
불가능하다.

## 2026-09-20 — weather를 external target에서 internal target으로 전환 (ADR-47)

- **작업 내용**:
  - weather(`kor-travel-weather`)의 8개 서비스(api·web·dagster-code-server·
    dagster-webserver·dagster-daemon·dagster-gateway·migrate·prometheus) 전부를
    Manager의 `docker-compose.yml`로 옮겼다(`network_mode: host`) — weather 자신의
    저장소 compose는 local-dev/e2e 전용으로 격하됐다(삭제하지 않음).
  - weather 전용 `db`는 은퇴한다. 사용자 지시("데이터는 옮기지 마 — 어차피 새로 쌓으면
    됨")에 따라 `kor-travel-shared-postgres`(`:11000`)에 완전히 빈 상태로 앱 DB
    (`kor_travel_weather`)를 새로 만들었다(`kor-travel-shared-db-init-weather`). Dagster
    메타 DB(`kor_travel_weather_dagster`)는 오늘 앞서 이미 만들어져 있던 것을 그대로
    재사용했다(같은 역할·이름, PinVi ADR-46과 같은 패턴).
  - 멀티에이전트 workflow(연구 5개 + 설계 + 적대 리뷰 2건)로 `network_mode: host` 전환이
    바꾸는 두 가지를 미리 잡았다: dagster-webserver/gateway의 포트 14102 충돌(→
    webserver를 내부 전용 14107로 이동, gateway nginx 오버라이드가 그리로 proxy_pass),
    dagster-code-server의 무인증 gRPC 포트(14106) loopback-only 보장이 bridge NAT
    대신 `-h 127.0.0.1` 자체 바인드로 넘어간 것.
  - CI가 잡은 것(로컬에서 계약 테스트 파일 하나만 돌리고 놓쳤던 것): dagster-webserver의
    healthcheck가 `/server_info`(code location이 죽어도 200)를 썼던 것 → GraphQL
    `repositoriesOrError` 질의로 교체(geo/map/pinvi와 동일). dagster-daemon에
    `DAGSTER_DAEMON_HEARTBEAT_TOLERANCE`가 없었던 것 → weather 자신이 이 하드닝의
    원인이 된 사고(18시간 정지) 당사자라 추가했다. 그리고 weather가 더 이상 external
    target이 아니게 되며 그 사실을 실제 예시로 삼던 5개 테스트 파일의 전제가 낡았다 —
    fork 서브에이전트가 합성 fixture(airport 아래에 이름 충돌을 재현)로 커버리지를
    보존한 채 갱신했다.
  - **n150 실배포까지 완료했다**: 이미지 4종 build(대부분 캐시 히트) → db-init
    실행(role/db 확인) → `alembic upgrade head`(빈 DB에 16개 마이그레이션, 문제
    없음 — 별도 부트스트랩 불필요했음이 실측됨) → weather 자신의 독립 스택
    `docker compose down`(볼륨 보존, 롤백 안전망) → Manager 소유 스택 기동. 검증:
    공개 도메인 3개(`weather`/`weather-api`/`weather-dagster`.digitie.mywire.org) 전부
    HAProxy 경유로 정상 응답, 14106/14107이 LAN IP로는 연결 거부되지만 loopback으로는
    연결됨(핵심 보안 요구사항 실측 확인), Prometheus 타겟 2개 모두 up, 7개 컨테이너
    로그 전부 깨끗.
  - 알려진 채 남겨 둔 노출(사용자 결정): dagster-code-server의 metrics 포트(14103)는
    weather 자신의 Python 코드가 여는 것이라 compose로 바인드 주소를 제어할 수 없다 —
    host 모드에서 LAN에 노출된다. geo·PinVi의 dagster-code-server에도 이미 같은 부류의
    노출이 실재함을 이번 적대 리뷰에서 발견했다(이 작업과 무관, 별도 후속).
- **결정 사항**:
  - weather는 PinVi(ADR-46)와 같은 패턴 — 데이터 보존 없이 fresh 구성. 롤백은 옛
    Manager 전용 instance가 아니라 weather 자신의 `compose.yaml`(격하됐지만 존재)과
    그 안의 `weather-postgres` 볼륨(cutover 시점까지의 데이터, 보존됨)이다.
  - 기존 포트 대역(14100-14199)은 그대로 유지 — n150 HAProxy가 이미 그 포트로
    라우팅 중이고, `network_mode: host`는 포트 NAT이 없어 코드/설정 변경 없이 계속
    동작함이 실측 확인됐다.
  - §7(공유 `dagster_shared`/공용 webserver·daemon으로의 통합)은 이 작업의 범위 밖 —
    weather는 자기 전용 dagster 4종(code-server/webserver/daemon/gateway)을 그대로
    유지한 채 internal target이 됐을 뿐이다.
- **다음 작업**:
  - dagster-code-server 계열 metrics 포트(14103) LAN 노출 — weather/geo/pinvi 공통,
    별도 후속(weather 저장소 소스 변경 또는 n150 호스트 방화벽).
  - 최소 24-48시간 안정성 관찰(스케줄된 ingest 사이클이 실제로 `kor_travel_weather`에
    데이터를 쌓는지) 후, 옛 `weather-postgres` 볼륨 폐기 여부를 별도로 결정.
  - 세션 우선순위 순서(geo → weather/transport/concierge → map → pinvi)의 다음
    단계는 transport — 단, 그 저장소는 read-only 제약(미추적 WIP 다수)이 걸려 있어
    별도 스코프 확인 필요.

## 2026-09-21 — transport 공용 DB·RustFS provisioning 계약 추가

- **작업 내용**:
  - `kor-travel-shared-db-init-transport` one-shot을 추가해 공용 PostgreSQL에
    application DB(`kor_travel_transport`)와 Dagster metadata DB
    (`kor_travel_transport_dagster`)를 각각 별도 비특권 role로 멱등 생성하도록 했다.
    둘 다 `alembic_version`을 쓰므로 한 DB를 공유하지 않는다.
  - 각 DB에서 `PUBLIC CONNECT`를 회수하고 해당 role에만 접속 권한을 부여했다.
    application·Dagster 비밀번호는 서로 다른 Docker secret/env로 분리했다.
  - RustFS 초기화 bucket에 `kor-travel-transport-raw`를 추가했다. KRIC 등 갱신 빈도가
    낮은 원본 파일은 transport 라이브러리가 이 공용 bucket에 보관한다.
  - transport의 api/web/Dagster 컨테이너는 아직 Manager가 소유하지 않는다. transport
    저장소의 분리 compose와 receipt-gated hard cutover가 배포 정본이며, 이 변경은 그
    전에 필요한 공용 기반만 제공한다.
- **다음 작업**:
  - Manager 변경을 n150 배포 트리에 반영해 db-init과 RustFS bucket 성공을 확인한 뒤,
    transport 저장소의 cutover receipt·배포·live E2E를 실행한다.

## 2026-09-27 — rustfs-init이 minio/mc 없이 버킷을 만든다

- **발견**: M05 port(#424) 뒤 첫 실제 격리 실행(`/root/m05-once-p1`)이 claim 뒤
  `map_application_start_failed`로 BLOCKED됐다. stderr는
  `pull access denied for minio/mc, repository does not exist`였다. Docker Hub의
  `minio/mc`·`minio/minio` 저장소가 사라졌다(n150에서 Hub API 404). Map·PinVi·Manager 세
  compose가 모두 이 이미지로 버킷을 만들고 있었다. PinVi는 digest로 핀했지만 저장소가 없으면
  digest도 받을 수 없다.
- **Manager 쪽 영향**: `rustfs-init`은 rustfs 컨테이너를 재생성할 때마다
  `docker_service`가 `run --rm rustfs-init`으로 돌린다. 이 실패는 mutation 롤백으로 이어진다.
  n150에는 `minio/mc` 이미지가 남아 있지 않아(2026-09-27 확인) 다음 rustfs 재생성이 곧 이
  실패였다.
- **변경**: `rustfs-init` 이미지를 `rustfs` 서비스와 같은 `rustfs/rustfs:latest`로 바꿨다.
  `scripts/ensure-rustfs-buckets.sh`는 그 이미지에 든 curl(8.19)의 `--aws-sigv4`로
  `PUT /<bucket>`을 보낸다. 접속 실패·5xx만 기동 대기로 재시도한다(`RUSTFS_WAIT_RETRIES`, 기본 60).
  4xx는 HTTP 코드와 S3 오류 본문을 남기고 바로 실패한다. 이미 있는 버킷은 200(RustFS) 또는
  409 `BucketAlreadyOwnedByYou`(S3)로 성공이다.
- **실측(n150, 일회용 RustFS)**: 6개 버킷 생성은 기동 중에 시작해도 exit 0이었고 재실행도 exit 0이었다.
  틀린 비밀번호는 403 `SignatureDoesNotMatch`로 exit 1, 접속할 곳이 없으면 재시도 뒤 HTTP 000으로
  exit 1이었다.
- **짝**: Map `fix/rustfs-init-curl`, PinVi `fix/rustfs-init-curl`. M05는 핀된 pair의 compose를
  돌리므로 두 저장소 머지 뒤 새 pair로 회전해야 다시 돈다.

## 2026-09-27 — Dagster healthcheck 폭주를 끝냈다: 셸 래퍼, deadline 없는 CLI, 거두지 않는 PID 1

- **발견**: M05 격리 실행이 n150 과부하 속에서 연달아 멈췄다. load는 57~137까지 올랐다.
  원인은 Manager가 띄우는 Dagster 스택 넷(Map·PinVi·geo·weather)의 healthcheck였다.
  - code-server는 `dagster api grpc-health-check`(Map·geo는 10초마다), daemon은 `CMD-SHELL dagster-daemon liveness-check`를
    돌렸다. 둘 다 매번 dagster를 import한다(유휴 2초 안팎).
  - 적대 리뷰가 메커니즘을 바로잡았다. docker는 컨테이너마다 probe를 하나씩만 돌린다. 쌓인 것은 `CMD-SHELL` 래퍼의
    고아다. timeout 때 `sh`만 죽고 Python은 계속 돌며, PID 1(dagster)은 그것을 거두지 않는다. 그래서 좀비가 565개 있었다
    (geo code-server 261, geo daemon 85, weather 78, PinVi 25, Map 20). exec 형식 컨테이너에는 0개였다.
  - CLI probe에는 deadline이 없다. weather code-server는 2026-09-26 04:39 DB 접속 오류 뒤 gRPC가 멈춰 있었다.
    probe가 2,915회 연속 실패했고, 1시간 넘게 산 probe 프로세스가 쌓여 있었다. 그 사이 weather 수집이 사실상 멈췄다.
- **변경(#426, `7bda04db`)**:
  - code-server는 같은 gRPC health 호출(`DagsterApi` → SERVING)을 dagster import 없이 `grpc_health`로 직접 부른다(`python -I`,
    deadline 8초, 0.5~0.9초). 30초 간격에 180초 기동 창, `start_interval` 5초.
  - daemon은 `liveness-check`를 exec 형식으로 120초/60초 간격에 retries 2로 돌린다.
    `tolerance + interval × retries = 300 + 240초`다.
  - webserver 검사는 exec 형식으로 바꿨다.
  - 12개 서비스 모두 `init: true`다.
  - `test_dagster_daemon_liveness_contract.py`가 exec 형식, `init`, gRPC health 호출, `interval × retries ≤ tolerance`를
    command에서 유도한 서비스마다 요구한다. main에 대고 돌리면 29건이 빨갛다.
- **반영**:
  - Map·PinVi는 설치 → rebind → 같은 pair 수렴(`converged`)으로 반영했다.
  - geo·weather는 호스트에서 `-p kor-travel-docker-manager --project-directory /opt/kor-travel-docker-manager`를 명시하고
    `up -d --no-deps --no-build --wait`로 반영했다(먼저 `--dry-run`).
  - weather는 code-server를 먼저, healthy 뒤 webserver·daemon 순으로 했다.
- **결과**: 좀비 565 → 40(남은 것은 비-Dagster·airport), 동시 probe 47 → 1, weather code-server 복구(수집 재개).
- **짝**: Map #1284(Map 자체 compose에 같은 계약). airport(transport) 스택은 그 저장소에서 따로 한다.

## 2026-09-28 — M05 격리 실행의 host 포트를 고정해 web 이미지 빌드 캐시를 살린다

- **발견(n150, 2026-09-27)**: `_free_ports`가 탐색 시작점을 transaction에서 뽑아 실행마다 포트가 달랐다.
  그런데 포트는 빌드 입력이다. Map frontend는 `NEXT_PUBLIC_KOR_TRAVEL_MAP_API`(map_api)와
  `NEXT_PUBLIC_KOR_TRAVEL_MAP_DAGSTER_URL`(compose가 `KOR_TRAVEL_MAP_DAGSTER_PORT`에서 파생)을,
  PinVi web은 `NEXT_PUBLIC_PINVI_API_URL`과 `NEXT_PUBLIC_GRAFANA_URL`(`PINVI_GRAFANA_PORT`에서 파생)을
  빌드 인자로 굽는다. 그래서 두 web 이미지는 매 실행 BuildKit 캐시를 놓쳤다. PinVi web은 수 GB
  node_modules 층을 단일 스레드 gzip으로 다시 내보내느라 20-120분을 썼다. p2-p7은 그 언저리에서 죽었다
  (commit deadline, BuildKit session healthcheck 사망, 빌드가 더한 부하 속 healthcheck 창 초과).
  포트와 무관한 Map API·PinVi API 이미지는 같은 실행에서 캐시로 3-10초였다.
- **변경**: 탐색 시작점을 `_PORT_SCAN_BASE = 28629`로 고정하고 `transaction` 인자를 없앴다. ephemeral
  하한 가드(<= 29999면 닫힘), 포트별 `ss -H -ltn` 검사, 사용 중이면 다음 13포트 창으로 넘어가는 탐색,
  30000 미만 상한, `ports_unavailable`은 그대로다. M05 실행은 launcher의 host-global mutation lock으로
  직렬화되므로 고정 창을 실행끼리 다투지 않는다. 앞 창이 쓰이고 있으면 그 실행만 캐시를 놓친다.
  - 28629는 p7(transaction `7cb6f355`)의 창이다. 같은 핀으로 끝까지 export된 이미지가 n150에 남아 있다
    (PinVi web `5ad448e8` 22:01:42, Map frontend `ce948a62` 19:56:57). PinVi web 이력에
    `NEXT_PUBLIC_PINVI_API_URL=…:28634`(+5)·`NEXT_PUBLIC_GRAFANA_URL=…:28641`(+12)이 찍혀 있다.
    20000이었다면 고정 뒤 첫 실행이 어느 실행도 쓴 적 없는 창이라 PinVi web 층을 부하 속에서 한 번 더
    내보냈다(적대 리뷰). 30000 아래 창은 105개(첫 창 + 대체 104)다.
  - 한계: n150 BuildKit 캐시는 GC 예약 하한(43.77 GiB)에 붙어 있어 새 빌드마다 LRU 축출이 돈다. 사이에
    낀 prod 재구축·D2 사이클이 web 사슬을 밀어낼 수 있다 — "한 번 빌드하면 계속 재사용"은 그 사슬이 최근
    사용 쪽에 남아 있는 동안만이다.
- **감사(핀 pair Map `a18d9274`·PinVi `fd07903f`의 compose를 n150에서 `docker compose config`로 렌더,
  비밀은 매 실행 새 값)**: 두 실행 사이에 달라지는 빌드 인자는 위 네 개뿐이었고, 고정 뒤에는 0개다.
  - 비밀은 어느 빌드 인자에도 닿지 않는다. 빌드 인자는 Map `KOR_TRAVEL_MAP_GIT_COMMIT`(핀),
    PinVi `PINVI_SOURCE_REVISION`(핀)·`PINVI_BUILD_ENVIRONMENT=isolated`와 상수 기본값뿐이다.
  - 빌드 컨텍스트 경로는 실행마다 다르지만 내용은 같다. Map은 실행별 checkout(`runtime/map-src`)이다.
    driver는 그 안에 아무것도 쓰지 않는다(env·override·admission은 형제 `runtime/`). `.git/`·`.env*`는
    `.dockerignore`가 뺀다. PinVi는 `docker-app.sh`가 핀 revision을 `git archive`해 mktemp 디렉터리에 푼다.
  - 실행마다 다른 것은 이름뿐이다. project·image 이름과 compose가 붙이는 이미지 label
    `com.docker.compose.project`가 그렇다. label은 이미지 config(ID)만 바꾸고 층 캐시 키는 바꾸지 않는다.
    서비스 `labels`(transaction 등)는 컨테이너 label이지 build label이 아니다.
- **테스트**: `test_two_runs_hand_every_image_build_the_same_inputs`(full_path)는 가짜 docker 호스트로
  `main`을 두 번 끝까지 돌리고, **빌드 명령이 받은 입력**을 비교한다. Map `compose up --build`와 PinVi
  `docker-app.sh build`·`compose up --build` 순간의 env 파일(+ driver가 넘긴 셸 env)을 핀 compose
  `build.args`가 보간하는 이름(`NEXT_PUBLIC_*` 전부, 포트·revision 키)으로 투영하고, 렌더된 build 절은
  context 경로만 빼고 본다. transaction이 다르고 전체 env(비밀·project 이름)가 다르다는 것, 두 web 이미지와
  그 포트 키를 실제로 봤다는 것도 확인한다. 그래서 포트가 아닌 실행별 값(transaction, network 주소)이
  빌드 인자로 새는 회귀도 잡는다. origin/main driver에 대고 돌리면 빨갛다(`NEXT_PUBLIC_KOR_TRAVEL_MAP_API`
  등이 실행마다 다르다). 적대 리뷰가 지적한 첫판(`ss`가 확인한 포트 목록 비교)은 env 파일이 쓰이기 전에
  멈춰 빌드 입력을 보지 못해 걷어냈다. 사용 중 창 건너뛰기의 결정성, ephemeral 가드(20000·29999·빈 값·
  숫자 아님·읽기 실패), 30000 상한(모든 창 사용 중 → 30000 아래 창만 다 보고 닫힘)도 고정했다.

## 2026-09-28 — 옛 geo·concierge·PinVi 전용 PostgreSQL을 Manager에서 퇴역시켰다

- **실측(n150, 읽기 전용)**: 떠 있는 PostgreSQL은 `kor-travel-shared-postgres`(:11000)·
  `kor-travel-map-postgres`(:12700)·`pinvi-postgres`(:12800) 셋뿐이었다. geo·concierge 전용
  instance와 airport DB(`kor-travel-airport-db-postgres-1`)는 이미 없었다. geo·weather·
  concierge·PinVi·transport의 DSN은 전부 `127.0.0.1:11000`을 가리켰고, `:12700`을 쓰는 것은
  Map API와 Map Dagster 셋뿐이었다. `pinvi-postgres`는 ADR-46 이전의 동결 롤백 사본으로
  client 접속이 0이었다. 설치본(`/opt` → `e25f105`) compose에는 퇴역할 서비스 정의 여섯이
  그대로 있었다.
- **문제**: Manager는 그 instance들을 "롤백 안전망"이라며 계속 정의하고 띄우고 의존하고
  백업했다. `kor-travel-geo-api`와 geo Dagster 셋은 `kor-travel-geo-postgres`에, concierge-api는
  옛 instance와 그 db-init에, PinVi 넷은 `pinvi-postgres`·`pinvi-db-init`에 `depends_on`으로
  걸려 있었다. 재구축(`rebuild-pinned`)은 매번 `pinvi-postgres`를 띄웠다. 주기 백업의
  `geo_dagster`는 없는 컨테이너를 겨냥해 실패할 자리였고 `pinvi`는 동결 사본을 떴다.
- **변경**:
  - compose에서 `kor-travel-geo-postgres`·`kor-travel-concierge-postgres`·`pinvi-postgres`·
    `kor-travel-concierge-db-init`·`pinvi-db-init`·`kor-travel-geo-dagster-db-init`을 뺐다.
    `geo-dagster-db-init`은 옛 geo instance(:12500)에 `kor_travel_geo_dagster`를 만들던
    one-shot이다. 공용 instance에는 `kor-travel-shared-db-init-geo`가 이미 그 DB를 만든다.
    그 셋의 superuser secret과, 거기로 가던 `depends_on` 간선도 모두 뺐다.
  - `config/docker-targets.yml`: 컨테이너 넷(`kor-travel-airport-postgresql` 포함), target
    `db`(서비스는 geo instance 하나)·`airport-db`, geo/conc/map/pinvi/all의 옛 멤버,
    `geo-source-verification` init step, `compose_binds` 셋을 뺐다. `storage`와 `airport`는
    더 이상 없는 target에 의존하지 않는다.
  - `scripts/ensure-kor-travel-geo-db.sh`·`scripts/verify-kor-travel-geo-source.sh`를 지웠다.
    둘 다 옛 geo instance **안에** 마운트돼 돌던 스크립트다. 검증 스크립트의 테스트도 함께 지웠다.
  - C6c 계약에서 `pinvi-postgres`/`pinvi-db-init`의 신원·이미지·command·superuser secret 검사,
    그 컨테이너의 런타임 secret 격리 검사를 뺐다. PinVi env 불변식에서는
    `PINVI_DB_PORT`·`PINVI_POSTGRES_USER`·`PINVI_POSTGRES_PASSWORD`를 뺐다. loopback 결박과 initdb
    규칙은 전역 술어가 모든 선언·목격된 PostgreSQL에 대해 계속 본다. 이름으로 아는 서비스
    목록에서도 옛 geo/concierge를 뺐다.
  - 재구축은 이제 Map PostgreSQL만 띄운다. 제거하는 one-shot 목록에서 `pinvi-db-init`을 뺐다.
    그대로 두면 새 compose에서 `no such service`로 죽는다. 공용 instance는 다른 프로젝트도 쓰므로
    이 경로가 다시 만들지 않는다(다른 호출과 같이 `--no-deps`).
  - `ktdctl db-backup`: `geo`·`geo_dagster`·`pinvi`가 `kor-travel-shared-postgres`를 뜬다(override는
    `KOR_TRAVEL_SHARED_POSTGRES_CONTAINER`). `map_application`/`map_dagster`는 그대로다.
  - concierge `DATABASE_URL`은 기본값이 없다(`:?`, geo의 `KTG_PG_DSN`과 같은 형태). `:12600`의
    superuser 리터럴을 공용 instance의 app role로 바꾼 첫판은 비밀번호가 없어 인증에서 기동 뒤에
    죽는 기본값이었다(적대 리뷰). n150은 `.env`로 값을 준다 — 떠 있는 concierge 셋의 DSN이
    `kor_travel_concierge_app:<비밀번호>@127.0.0.1:11000`이다(읽기 전용 inspect, 비밀번호는 보지 않음).
    `test_f1d_compose_contract`의 합성 env에 그 키가 없어 concierge api를 resolve하는 검사 하나가
    `required variable ... is missing`으로 빨갛게 됐다 — `:?`가 실제로 걸린다는 증거이고, 합성 env에
    키를 더했다.
  - `.env.example`에서 옛 instance만 읽던 키를 뺐다. 예시 DSN은 공용 instance 형태로 바꿨다.
  - `docs/ports.md`·`platform-topology.md`·`docker-management.md`·`shared-postgres-onboarding.md`·
    `architecture.md`·`dev-environment.md`·`README.md`를 현재 토폴로지로 맞췄다.
    `docs/tasks.md`에서 대상 코드가 사라진 GM-17 B 후속 셋을 닫았다.
- **Map은 그대로다**: `kor-travel-map-postgres`(:12700)와 그 one-shot·DSN·백업 role은 건드리지 않았다.
- **테스트**:
  - 옛 서비스를 고정하던 검사를 갱신했다. f1d 골든 테이블에서 required 집합은 12→11이고, 소비자
    루프의 예외(`pinvi-db-init`)가 사라졌다. PinVi 전용 instance의 신원·secret 검사도 지웠다.
    전역 술어의 표본은 옛 geo/concierge 대신 공용 instance다.
  - 다중 프로젝트 경로(`airport`→`airport-db`)는 conftest의 합성 fixture로 계속 태운다.
    weather의 옛 좌표를 복원하는 기존 방식과 같다.
  - 새 `test_compose_model_references.py`는 참조를 모델에서 읽어 compose가 정의하는 서비스와
    대조한다. `depends_on` 간선, Manager target의 `services`/`runtime_services`/init step 서비스,
    컨테이너의 `compose_service`, `compose_binds`, 백업 role의 컨테이너(와 이름 override 변수),
    C6c가 이름으로 아는 서비스, 재구축 one-shot 목록, 배포 DB 서비스가 대상이다.
    origin/main 그대로에서는 초록이다(main은 옛 서비스를 아직 정의한다). origin/main에서 옛 서비스
    정의만 지운 사본에서는 6건이 빨갛다(간선 17, target 쪽 12, 백업 role 3, 코드 이름 5).
  - n150 전체 스위트(ruff 0.16.4 포함)는 `0bc1223`에서 2014 passed, 2 skipped다. 새 compose는 더미
    env로 `docker compose config -q`를 통과한다. 렌더 결과에 옛 이름과 포트가 0건이다.
- **적대 리뷰 반영(두 렌즈, 차단은 운영 렌즈의 MED 둘)**:
  - **백업 회전이 옛 데이터의 유일한 사본을 지울 뻔했다.** n150 `~/backups/pinvi`의 dump 7개는 전부
    `pinvi-postgres:12800`에서 떴고, ADR-46이 옮기지 않은 PinVi 데이터의 유일한 백업이다. `gc`는
    출처를 보지 않고 최신 keep개만 남겼으므로 role을 공용 instance로 돌린 뒤 7일이면 전부 지웠을
    것이다(`geo_dagster` 5개·`concierge` 7개도 옛 instance 출처). 이제 `gc`는 role이 **지금 뜨는
    자리**(`_role_config`의 컨테이너·database)의 dump끼리만 세고, 다른 자리의 dump는 지우지 않고
    `other_instance_kept`로 매번 알린다. `restore-plan`은 다른 자리의 dump를 `INSTANCE_MISMATCH`로
    차단한다(무결성이 멀쩡해도 다른 데이터다 — `rehearse-restore`도 그래서 시도하지 않는다). 출처는
    manifest의 `instance`에서 읽는다. n150 role 디렉터리의 manifest 22개가 전부
    `<컨테이너>:127.0.0.1:<포트>/<db>` 형태이고 Map 둘은 지금 자리와 같아 Map 회전은 그대로다.
  - **cron 사본은 설치로 바뀌지 않는다.** crontab은 git이 아닌 사본
    `/home/digitie/kor-travel-docker-manager`를 부른다. 2026-09-20에 그 사본의 `backend/src`만
    갱신돼 `config/docker-targets.yml`(9월 8일판, `compose_binds` 없음)과 어긋났고, 그 뒤 세 role이
    매일 `compose_binds 절이 없다`로 실패했다 — 공용 instance의 데이터는 cron으로 한 번도 떠진 적이
    없다. 아래 배포 절차에서 사본 동기화를 필수로 올렸다.
  - 재구축이 띄우는 PostgreSQL을 c6c의 `_MAP_POSTGRES_SERVICE`에서 읽는 모듈 상수로 옮기고, 그것과
    `RUNTIME_SERVICES`·`_PINNED_RUNTIME_EXTERNAL_PREREQUISITES`를 `test_compose_model_references`에
    넣었다. 문서: `docker-management.md`의 `db` target 잔재 둘, `platform-topology.md` §5/§7(code-server는
    Dagster를 쓰는 다섯 프로젝트가 **각자 하나씩** 돌린다 — Map은 #397로 2026-09-25에 분리됐고 표가
    "없음"이라고 낡아 있었다), 공용 instance role의 부하·백업 없는 DB 목록.
  - 반영하지 않은 것: `AGENTS.md`·`CLAUDE.md`·`SKILL.md`의 낡은 DB 토폴로지(전용 instance 넷,
    `db` target, 규칙 9)는 에이전트 지시 파일이라 사용자 승인 뒤 문서 전용 후속으로 고친다. 이 브랜치의
    커밋 두 개(`0bc1223`·`163e927`)의 author가 worktree 로컬 설정 때문에 `ChatGPT Codex`로 찍혔다 —
    이미 푸시한 이력은 다시 쓰지 않는다.
- **배포 절차(운영자, 한 창에서)**:
  1. PR을 CI green으로 머지하고 **main의 머지 커밋만** 설치한다(미머지 head는 설치하지 않는다).
  2. 설치 **전에** 옛 PinVi 사본을 마지막으로 뜬다: `docker exec --user postgres pinvi-postgres
     pg_dump -U <그 컨테이너의 POSTGRES_USER> -p 12800 -Fc -d pinvi -f /tmp/pinvi-final.dump`(있으면
     `pinvi_bootstrap`도) → `docker cp`로 `/home/digitie/backups/legacy/retired-2026-09-28/`에 옮기고
     `pg_restore --list`로 읽히는지 확인한다.
  3. 같은 곳으로 옛 instance 출처의 dump 세트(`<role>-*.dump`·`.dump.sha256`·`.manifest`)를 옮긴다 —
     `pinvi` 7개·`geo_dagster` 5개·`concierge` 7개·`geo` 1개. 새 `gc`는 그것들을 지우지 않지만, 옮기지
     않으면 cron 로그가 매일 `kept N dump(s) taken from another instance`를 찍고, 첫 새 dump 전까지는
     `restore-plan`의 기본 후보가 옛 dump다(`INSTANCE_MISMATCH`로 차단된다).
  4. `.env`에 `KOR_TRAVEL_CONCIERGE_DOCKER_DATABASE_URL`이 있는지 확인한다(이제 필수 — 없으면 모든
     compose 명령이 실패한다). 설치 → rebind → verify. 릴리스에서 `docker compose config -q`가 통과하는지
     본다(orphan 경고는 `config`가 아니라 `up`/`down`만 낸다).
  5. 같은 창에서 `docker stop -t 60 pinvi-postgres && docker rm pinvi-postgres`. 설치 뒤에는 그 컨테이너를
     hba 검사도 백업도 상태 화면도 보지 않는다. 데이터 디렉터리 `/home/digitie/pinvi-data/pgdata`,
     `/home/digitie/kor-travel-geo-data/pgdata-final-20260529`, `/home/digitie/kor-travel-concierge-data/pgdata`는
     지우지 않는다.
  6. **필수**: cron 사본을 설치본과 맞춘다 — `backend/src`(그 디렉터리 안에서만 `--delete` 가능),
     `config/docker-targets.yml`, `scripts/run-standalone-backup.sh`. `config/`의 나머지(떠 있는
     Prometheus/Grafana가 마운트한다)는 건드리지 않는다. 사본에서 digitie로
     `backend/ktd_venv/bin/ktdctl targets validate` → 세 role을 한 번씩 손으로 돌리고(`run-standalone-backup.sh
     geo_dagster 4`·`concierge 7`·`pinvi 7`) 새 manifest의 `instance`가
     `kor-travel-shared-postgres:127.0.0.1:11000/<db>`인지 본다.
  7. 호스트 `.env`의 옛 키(`PINVI_POSTGRES_PASSWORD`·`PINVI_DB_PORT`·`KOR_TRAVEL_GEO_POSTGRES_PASSWORD`·
     `KOR_TRAVEL_CONCIERGE_POSTGRES_PASSWORD` 등)는 **`e25f105`로 되돌릴 창이 닫힐 때까지 둔다.** 그
     릴리스의 C6c는 `PINVI_POSTGRES_PASSWORD`와 `PINVI_DB_PORT == 12800`을 요구하고 compose가 그 키로 secret을
     정의한다 — 먼저 지우면 롤백이 `PinVi database URL identity is invalid`로 죽는다.
  8. `geo` role(`kor_travel_geo` 약 35 GB)은 다섯 프로젝트가 쓰는 공용 instance를 뜨므로 조용한 시간에만
     손으로 돌린다.

## 2026-09-28 — transport 백업을 Manager standalone role로 접었다

- **오너 결정(2026-09-28)**: transport 스택의 백업을 Manager의 standalone `db-backup` role로 옮긴다.
- **실측(n150, 읽기 전용 — `ls`/`stat`/`docker ps`만)**:
  - transport 저장소의 자체 cron(`0 18 */3 * * .../kor-travel-airport/scripts/n150-backup-cron.sh`, API
    `POST /v1/admin/backups`)이 남긴 마지막 자동 dump는 `parking-radar-20260905T230044Z.dump`(13.5 MB)다.
    그 뒤로 `/home/digitie/apps/kor-travel-airport/backups/`에는 2026-09-27에 손으로 뜬
    `pre-kric-20260927T055443Z.dump`(1,011,308,463 B, 약 9분)와 그 전 시도의 `.incomplete`(342 MB)뿐이다.
    `kor_travel_transport_dagster`는 백업이 아예 없었다.
  - `/`·`/home`·`/home/digitie/backups`·`/var/lib/docker`·`/home/digitie/kor-travel-shared-data`가 모두 같은
    장치(`stat -c %d` 64512)다. 백업 root, pg_dump가 먼저 쓰는 컨테이너 `/tmp`(Docker 쓰기 층), 공용
    instance PGDATA가 한 디스크를 나눠 쓴다. 여유는 20,473,664 블록 × 4 KiB ≈ 83.9 GB(전체 약 500 GB).
  - `~/backups/geo`는 비어 있다(#429 절차대로 옛 instance dump를 `legacy/`로 옮겼다). `geo_dagster`·
    `concierge`·`pinvi`는 2026-09-28 03:05~03:15에 공용 instance에서 새로 떴다.
- **전제 정정**: 지시가 든 `BACKUP_COMMAND_TIMEOUT_SECONDS`·`MAX_BACKUP_BYTES`(2 GiB)는 Manager에 없다.
  transport 앱(`backend/app/services/backup_restore.py`, compose 기본 120초)의 한도다. Manager `create`에는
  기본 14,400초 timeout(API는 60~86,400초) 하나만 있었고 크기 상한도 디스크 확인도 없었다. 120초로는
  9분짜리 transport dump를 뜰 수 없다 — 자체 cron이 멈춘 원인 후보지만 그쪽 로그를 보지 않았으므로
  확정하지 않는다.
- **변경**:
  - `standalone_backup`: `transport`(`kor_travel_transport`)·`transport_dagster`(`kor_travel_transport_dagster`).
    둘 다 `kor-travel-shared-postgres`를 뜨고 `KOR_TRAVEL_SHARED_POSTGRES_CONTAINER` override를 따른다 —
    #429가 `geo_dagster`·`concierge`·`pinvi`에 준 모양 그대로이고, manifest는
    `kor-travel-shared-postgres:127.0.0.1:<포트>/<db>`다. DB 이름은 compose의
    `kor-travel-shared-db-init-transport`가 literal로 고정한 값이다.
  - `database_runtime._ROLE_CONFIG`는 건드리지 않았다. v5 재구축이 **파기·재생성하는** DB 목록이라 거기
    넣으면 재구축이 transport DB를 지운다. #429도 이 파일을 바꾸지 않았다.
  - **디스크 여유, role마다**: `create`가 pg_dump 전에 `필요량 = 2 x 예상 dump + 2 GiB`를 확인하고 모자라면
    시작하지 않는다(`StandaloneBackupInsufficientSpaceError`, CLI exit 2). 예상 dump는 같은 자리(컨테이너·
    database)에서 뜬 최신 dump를 DB 증가율만큼 키운 값, 그런 dump가 없으면 live DB 크기다. 2배는 컨테이너
    `/tmp`와 host 사본이 복사가 끝날 때까지 함께 있어서다. 예약분 2 GiB는 공용 instance `max_wal_size`(1GB)
    위의 여유다. 전역 상한 하나를 두지 않은 이유: dump 크기가 role마다 만 배 넘게 달라(pinvi 수백 KB ~
    geo 4.7 GB), 큰 role에 맞춘 값은 디스크가 빠듯할 때 작은 role까지 막고 작은 role에 맞춘 값은 큰 role을
    지키지 못한다. 필요량은 설정 숫자가 아니라 role의 database에서 나오므로 DB가 자라도 낡지 않는다.
  - timeout은 그대로 뒀다. 멈춘 명령의 상한이지 자원 가드가 아니고, 가장 큰 geo(880초)와 transport(약 9분)
    모두 기본 4시간 안이다.
  - `scripts/run-standalone-backup.sh` 허용 목록에 두 role을 넣고 헤더에 권장 crontab 줄을 적었다
    (`transport_dagster` 매일 18:10 UTC keep 7, `transport` `*/3` 18:30 UTC keep 3). 프론트 신선도 표가 그
    주기를 미러링한다(72 h / 24 h). transport 생성 확인 문구에 약 9분을 적었다.
  - 문서: `docker-management.md`(role 표, 디스크 여유 절, 백업 없는 DB 목록, transport 실측·cron),
    `shared-postgres-onboarding.md` §6.4(주기 대상, `database_runtime`에 넣지 말 것, 프론트 표).
- **geo 판정 — 고칠 것이 없었다**: timeout 880초 ≪ 14,400초. 새 디스크 확인에서 첫 실행(이 자리의 geo dump
  없음)은 2 × 약 35 GB + 2 GiB ≈ 72 GB로 n150 여유 83.9 GB 안이다(35 GiB로 잡아도 77 GB). 한 번 뜬 뒤에는
  2 × 4.7 GB + 2 GiB ≈ 11.5 GB다. 여유가 72 GB 아래일 때 geo를 처음 뜨면 거부되고 문구에 필요량·여유·근거가
  찍힌다. geo는 여전히 수동 전용이다.
- **테스트**:
  - 새 단언: transport 둘이 공용 instance의 자기 DB를 실제로 뜬다(pg_dump 인자·manifest `instance`), 디스크
    산술(첫 실행 상한, DB 증가 비례, 같은 자리의 최신 dump만, 읽지 못하는 manifest는 건너뜀, geo와 작은 role의
    숫자), 모자라면 pg_dump 전에 거부하고 아무것도 남기지 않음(정확히 필요량이면 진행), 가짜 `ktdctl`을 끼워
    wrapper를 **실행해** 모든 role에 대해 무엇을 불렀는지(`test_run_standalone_backup_wrapper.py`).
    `test_compose_model_references`는 `BACKUP_ROLES`를 파라미터로 받아 두 role을 자동으로 본다(초록).
  - **origin/main에서 빨갛다**: origin/main 코드 + 새 테스트(대상 네 파일)는 14 failed / 191 passed, 이 브랜치는
    207 passed. 첫판은 autouse fixture가 `standalone_backup.shutil`을 거쳐 패치해서, `shutil`을 import하지 않는
    origin/main에서는 파일 전체가 setup ERROR로 무너져 **어느 단언이 깨지는지를 가렸다** — `shutil`을 직접
    패치하도록 고쳤다.
  - n150 전체 스위트(ruff 0.16.4 포함)는 `df0f5be`에서 2037 passed, 2 skipped다(#429의 2014에서 새 단언과
    `BACKUP_ROLES` 파라미터 증가분만큼 늘었다). `ktdm-readiness-*` 잔여 컨테이너는 없었다.
- **운영 절차(설치 뒤, 운영자)**:
  1. PR을 CI green으로 머지하고 main의 머지 커밋만 설치한다(`~/install-mgr.sh <sha>` → rebind → verify).
  2. cron 사본 `/home/digitie/kor-travel-docker-manager`를 설치본과 맞춘다 — `backend/src`(그 안에서만
     `--delete`), `scripts/run-standalone-backup.sh`(실행 비트 유지). `config/`의 나머지는 건드리지 않는다.
  3. 여유 확인: `transport` 첫 실행은 약 28 GB(2 × 13 GB + 2 GiB)가 필요하다.
  4. 한 번씩 손으로(조용한 시간, `transport_dagster` 먼저) wrapper를 돌리고 확인한다: `db-backup list <role>
     --json`의 `instance`가 `kor-travel-shared-postgres:127.0.0.1:11000/<db>`, `sha256sum -c`, `restore-plan`
     exit 0. `transport_dagster`는 `rehearse-restore`까지(`verified: true`). `transport`의 리허설은 약 14 GB를
     더 쓰므로 선택이고 `df`를 먼저 본다.
  5. digitie crontab에 두 줄을 **덧붙인다**(`CRON_TZ=UTC` 아래, 기존 줄과 같은 `KTDM_BACKUP_ROOT` 접두).
  6. 두 role이 검증된 dump를 하나씩 남긴 **뒤에만** transport 저장소의 cron 줄(`n150-backup-cron.sh`)을
     걷어낸다. 그 저장소의 `docs/architecture/backup-restore.md` "자동 백업" 절은 그 저장소 후속으로 고친다.

## 2026-09-28 — transport 백업 role 리뷰 반영(geo 첫 실행 상한, 리허설 디스크, 실패 탐지 경로)

- 적대 리뷰(origin/main...`222aa67`)가 blocking MED 둘과 LOW 여섯을 냈다. **위 항목의 "geo 판정 — 고칠 것이
  없었다", 권장 cron 시각(18:10/18:30 UTC), 운영 절차는 이 항목이 대체한다.** n150에서는 이번에도 `ls`·`stat`만
  썼다(운영 DB 질의·crontab·`.env` 읽기 없음).
- **MED 1 — geo가 새 디스크 가드에 곧 막힐 자리였다.** `~/backups/geo`가 비어 있어(#429가 옛 dump를 `legacy/`로
  옮겼다) geo `create`는 매번 첫 실행 상한 2 × `pg_database_size` + 2 GiB를 탔다. 위 항목은 그것을 "≈ 72 GB"로
  적었지만 72는 GiB였다 — **77.3 GB**. 04:00 UTC n150 여유는 19,898,003 블록 × 4 KiB = **81.5 GB**(`stat -f`)라
  차이가 약 4.2 GB였고, transport 보존분(약 3 GB)이 쌓이면 1 GB 남짓, geo DB가 1 GB 자랄 때마다 필요량은 2 GB씩
  는다. 위 항목의 테스트는 83.9 GB라는 낡은 스냅숏을 상수와 비교해 "geo는 들어간다"는 거짓 안심을 줬다(지웠다).
  - 고침: 첫 실행 추정 = **min(1.25 × Σ`pg_table_size`(relkind `r`·`m`), `pg_database_size`)**. custom format
    dump에는 인덱스 페이지가 없다. 테이블 질의는 **그 role의 database에 붙어서**(`--dbname`) 한다 — `pg_class`는
    database마다 따로다. min이라 옛 상한보다 커지는 일은 없다(리뷰가 권한 1.25 × 테이블 단독은 인덱스가 거의
    없는 database에서 옛 상한보다 커질 수 있어 min으로 묶었다).
  - `db-backup create --expected-dump-bytes N`: 추정만 바꾸고 확인은 남긴다(2N + 2 GiB). CLI가 stderr(cron
    로그)에 찍고 거부 문구에도 남는다. 0·음수·비정수는 argparse가 거부한다.
  - geo의 테이블 합은 재지 않았다(운영 DB에 질의하지 않는다는 이번 작업 규칙). `docker-management.md`에
    운영자용 읽기 전용 질의와 비상 시 `--expected-dump-bytes 6000000000`(필요량 약 14.1 GB)을 적었다.
- **MED 2 — transport 실패가 여전히 조용할 자리였다.** 새 신선도 배지(72 h/24 h)가 유일한 제품 안의 탐지인데
  배지는 backend가 읽는 root를 본다. backend는 `.env`의 `KTDM_BACKUP_ROOT`로 root를 정하고, n150에는
  `/etc/logrotate.d/kor-travel-docker-manager`가 없다(`stat`로 확인 — installer가 같은 키로 렌더링하고 없으면
  건너뛴다). backend는 `User=` 없는 유닛이라 root로 돌아 `/root/backups`를 읽고 cron은 `/home/digitie/backups`에
  쓴다 — 배지는 cron dump를 못 보고 `<role>.log`는 로테이션되지 않는다. 위 항목의 운영 절차가 "logrotate가 이미
  덮는다"고 적은 것은 틀렸다.
  - 고침(문서·wrapper 헤더): 키가 없으면 로테이션도 배지도 없다고 적었다. 해소 순서(G lock 아래 `.env`에 키
    추가 → 설치 → 배지 확인, 공유 그룹 없이 UI에서 백업을 만들지 말 것 — root 0600 manifest가 digitie cron의
    gc를 막는다)와 cron과 같은 root를 준 CLI 신선도 확인을 적었다. 코드 후속(목록 응답·배지에 backend가 읽은
    root 경로 싣기)은 `docker-management.md` "아직 안 된 것"에 남겼다.
- **LOW**:
  - 주기: 18:30 UTC는 03:30 KST로 transport의 `bus_reference_collection_job`과 정확히 겹쳤다. `transport_dagster`
    16:50 UTC(01:50 KST), `transport` 17:15 UTC `*/3`(02:15 KST)로 옮겼다 — transport Dagster의 매시 :00,
    03:00·03:30, ferry :45(4시간마다)를 피한다(`kor-travel-transport` `definitions.py` 로컬 체크아웃에서 확인).
  - `rehearse-restore`도 copy-in·`createdb` **전에** 잰다: manifest의 database 크기 + dump + 그 instance의
    `max_wal_size`(`pg_size_bytes(current_setting(...))`로 질의) + 2 GiB. 모자라면 아무것도 만들지 않는다.
  - UTF-8이 아닌 manifest가 `UnicodeDecodeError`(ValueError)로 새어 create를 pg_dump 전에 traceback으로 죽였다 —
    `_read_manifest`가 `StandaloneBackupError`로 바꾼다(목록의 `unreadable` 행으로도 격하된다).
  - 테스트: transport role 테스트가 pg_dump 전 크기 질의가 `pg_database_size('<role db>')` 하나이고 테이블 질의가
    `--dbname <role db>`인지 결박한다. 가짜는 어떤 이름에도 숫자를 돌려줘, 전에는 `postgres`를 재도 초록이었다.
  - crontab을 고치는 운영 절차는 파일로 떠서 비어 있지 않은지·정확히 한 줄(추가는 두 줄)이 바뀌는지 확인한 뒤
    설치한다(`crontab -l | … | crontab -`은 `crontab -l`이 실패하면 빈 crontab을 설치한다).
  - transport 수동 복원 절차를 적었다: 공용 instance에서 superuser로 `pg_restore --clean --if-exists
    --single-transaction --exit-on-error`, `--no-owner`·`--no-acl` 없이(소유자 `kor_travel_transport_app`과 GRANT가
    돌아온다). transport 앱의 복원 API는 이름 패턴(`parking-radar-*.dump`)·2 GiB·기본 120초 timeout 때문에 이
    dump를 쓸 수 없다. n150에서 실행해 보지는 않았다.
- 거부한 리뷰 항목은 없다.
- 리뷰가 적은 concierge 이상(브랜치 결함 아님)을 `ls`로 재확인했다: `concierge.log` mtime 03:30:34 UTC,
  `~/backups/concierge` 디렉터리 mtime 03:05:23, 최신 dump는 03:05 수동분이다 — 03:30 cron 실행이 dump를 남기지
  않았다. 로그 내용은 읽지 않았다. 운영 절차의 0단계로 넣었다.
- **테스트(n150)**:
  - `9a7b75a`에서 ruff 0.16.4(CI 명령) 초록, 대상 여섯 파일(`test_standalone_backup`·`test_docker_manager_cli`·
    `test_run_standalone_backup_wrapper`·`test_api`·`test_compose_model_references`·
    `test_cron_executed_scripts_are_executable`) 356 passed.
  - 빨강 확인: 직전 head `222aa67` 코드 + 이번 테스트는 16 failed / 215 passed — transport role 테스트(pg_dump 전
    테이블 질의가 없다), 경계 테스트(첫 실행 추정이 DB 크기라 정확히 필요한 여유에서도 거부), 리허설 디스크, UTF-8이
    아닌 manifest, override 모두 빨갛다. origin/main(`88599eb`) 코드 + 브랜치 테스트는 23 failed / 321 passed.
  - 전체 스위트(`/tmp/b3-test.sh`, `9a7b75a`): **2047 passed, 2 skipped**(2m47s). `ktdm-readiness-*` 잔여 컨테이너
    0개. 이 항목 뒤의 커밋은 문서만 바꾼다.
- **운영 절차(설치 뒤, 운영자)** — 위 항목의 1~6을 대체한다. 명령은 `docker-management.md`의 "transport 주기
  백업 — 실패를 누가 보는가"·"transport 복원" 절에 있고, 순서는 이렇다:
  0. `concierge.log`의 03:30 실행을 먼저 읽는다. `.env`에 `KTDM_BACKUP_ROOT`가 없는지 개수만 센다.
  1. CI green으로 머지하고, **설치 전에** G lock 아래에서 live `.env`에 `KTDM_BACKUP_ROOT=/home/digitie/backups`를
     덧붙인 뒤 머지 커밋을 설치한다(`~/install-mgr.sh <sha>` → rebind → verify). logrotate 파일이 생겼는지 본다.
  2. cron 사본의 `backend/src`와 wrapper를 설치본과 맞춘다.
  3. Dashboard 배지가 cron dump를 보는지 확인한다. 공유 그룹 없이 UI에서 백업을 만들지 않는다.
  4. `transport_dagster` → `transport` 순으로 wrapper를 한 번씩 손으로 돌려 검증한다.
  5. crontab에 두 줄(16:50 UTC daily keep 7, 17:15 UTC `*/3` keep 3)을 파일 경유로 덧붙인다.
  6. 두 role이 **cron으로** 검증된 dump를 하나씩 남긴 뒤에만 transport 저장소의 cron 줄을 파일 경유로(정확히 한 줄)
     걷어낸다.

## 2026-09-28 — transport 백업 운영 절차 리뷰 반영(UI 생성 한 번이 cron을 깨는 자리)

- 적대 리뷰(`222aa67..e323695`)가 MED 하나를 냈다(HIGH 없음). **위 항목의 운영 절차(0~6)는 이 항목이
  대체한다.** 코드 쪽(첫 실행 추정, 리허설 가드, override, UTF-8이 아닌 manifest, wrapper, cron 시각)은 리뷰가
  확인했고 LOW 넷은 올리지 않았다.
- **MED — 운영 절차가 UI 생성 한 번으로 cron을 깨는 창을 열었다.** 1단계는 공유 그룹도, transport role 디렉터리도
  없는 상태에서 live `.env`에 `KTDM_BACKUP_ROOT=/home/digitie/backups`를 넣었고, 막는 것은 3단계의 문장("공유
  그룹 없이 UI에서 만들지 않는다")뿐이었다. Dashboard는 `BACKUP_ROLES` 전부에 생성 버튼을 두고 backend는 root로
  돈다. 공유 그룹 없이 root backend가 digitie cron의 root에 쓰면:
  - role 디렉터리가 **없을 때**(transport 둘 — 리뷰 시점 06:05 UTC n150에 없었다): backend가 그것과
    `.backup.lock`을 `root:root 0700`/`0600`으로 만든다. 그 뒤 digitie cron은 매번 `_prepare_backup_root`의
    `os.chmod`에서 EPERM이다. typed 오류가 아닌 `PermissionError`라 CLI는 traceback으로 끝나고 그 role은 다시
    떠지지 않는다. 배지는 UI dump 덕에 90시간 초록이다.
  - **있을 때**(`geo_dagster`·`concierge`·`pinvi`): root `0600` manifest를 digitie `gc`가 읽지 못해 그 role 전체를
    매번 exit 2로 거부한다. create는 성공해 배지는 초록인 채 보존 정리만 멈추고, dump가 공용 instance PGDATA와 같은
    파일시스템(리뷰의 `stat` 실측)에 쌓인다.
  - 위 항목의 경고는 둘째 경로만 적었다 — 새 두 role이 정확히 첫째 경로다.
- **고침(코드)**: `POST /api/v1/backups/{role}`이 job을 띄우기 전에 `ui_backup_owner_conflict(role)`을 본다.
  `KTDM_BACKUP_SHARED_GROUP`이 없고 backend의 euid가 role 디렉터리(없으면 그 부모)의 소유자와 다르면 409와 이유
  (경로, 두 uid, 공유 그룹 절, 그 uid로 도는 CLI 대안)를 돌려준다. 공유 그룹이 있으면 건너뛴다 — 그 전제(setgid·
  그룹)는 create가 이미 확인한다. 둘 다 없으면 이 프로세스가 만들므로 막지 않고, `stat`이 실패하면 판정하지 않고
  create의 오류에 맡긴다. CLI는 그대로다 — cron과 손 실행은 그 디렉터리의 주인 계정으로 도는 것이 전제다.
- **고침(문서)**: `docker-management.md` "transport 주기 백업 — 실패를 누가 보는가"의 순서를 바꾸고 두 경로를 모두
  적었다. `prod-deployment.md` §3.x는 두 계정이 같은 root를 쓰면 필수라고 제목부터 적었다. wrapper 헤더도 같다.
  - 리뷰가 적지 않은 것 하나를 더 찾았다: **`ktdctl`은 `.env`를 읽지 않는다**(backend만 `load_dotenv`). 공유 그룹을
    `.env`에만 넣으면 cron은 `0700` 정책으로 돌아 다음 실행이 role 디렉터리를 `os.chmod(…, 0o700)`으로 되돌려
    setgid를 벗기고, 그 뒤 UI 생성이 `not a shared setgid directory`로 거부된다. crontab 환경 줄 하나
    (`KTDM_BACKUP_SHARED_GROUP=ktdm-backup`, 백업 줄 위)를 절차와 두 문서에 넣었다.
- **테스트(n150)**:
  - 새 단언(`test_api.py`): 다른 계정 소유의 role 디렉터리에는 409이고 job도 파일도 없다, role 디렉터리가 없고 부모가
    다른 계정 것이면 409이고 디렉터리를 만들지 않는다, 공유 그룹이 선언됐거나 root·부모가 모두 없으면 202로 돈다.
    소유자는 chown(root 전용) 대신 euid를 바꿔 흉내낸다. 기존 `POST` 테스트 넷은 이 테스트 계정 소유의 임시
    root(`owned_backup_root`)를 쓴다 — 없으면 판정이 실행 계정의 진짜 `~/backups`에 결박된다.
  - 빨강 확인: 직전 head `e323695`의 `backend/src` + 이번 테스트는 거부 둘이 빨갛다(`test_api.py -k backup`에서
    2 failed / 15 passed). 202 쪽 둘은 과잉 거부를 막는 단언이라 옛 코드에서도 초록이다.
  - `e3035dc`에서 ruff 0.16.4(CI 명령) 초록, 대상 여섯 파일 360 passed. 첫 실행 한 번은
    `test_a_failed_backup_job_reports_the_failure_instead_of_vanishing` 1 failed였고 그 뒤 같은 명령 세 번,
    `test_api.py` 단독, 그 테스트 단독 모두 초록이었다 — 재현되지 않았다. 원문을 남기지 못해 원인은 확정하지 않는다.
    이번 확인은 job 제출 **전에** 돌아 그 테스트의 job 수명과 겹치지 않는다.
  - 전체 스위트(`/tmp/b3-test.sh`, `e3035dc`): **2051 passed, 2 skipped**(4m30s, 새 단언 넷만큼 늘었다).
    `ktdm-readiness-*` 잔여 컨테이너 0개. 이 항목 뒤의 커밋은 문서만 바꾼다.
- **운영 절차(머지 뒤, 운영자)** — 위 항목의 0~6을 대체한다. 명령은 `docker-management.md`의 같은 절에 있다:
  0. `concierge.log`의 03:30 실행을 먼저 읽는다. `.env`에 `KTDM_BACKUP_ROOT`·`KTDM_BACKUP_SHARED_GROUP`가 없는지
     개수만 센다.
  1. CI green으로 머지한다. cron 사본의 `backend/src`(그 안에서만 `--delete`)·`config/docker-targets.yml`·wrapper
     (실행 비트 유지)를 **머지 커밋에서** 맞춘다 — 설치 전이라 설치본은 아직 옛 release다.
  2. 여유를 보고(`transport` 첫 실행 최대 약 28 GB) **digitie로** wrapper를 손으로 돌린다 — `transport_dagster` →
     `transport`. 검증은 "transport 백업을 Manager standalone role로 접었다" 항목의 4(`instance`, `sha256sum -c`,
     `restore-plan`, `transport_dagster`는
     `rehearse-restore`). `stat -c '%U:%G %a %n'`로 두 role 디렉터리와 `.backup.lock`이 digitie 소유인지 본다.
  3. 머지 커밋을 `.env` 그대로 설치·확인한 뒤, G lock 아래에서 live `.env`에 `KTDM_BACKUP_ROOT=/home/digitie/backups`
     한 줄을 넣고 같은 커밋을 다시 설치한다(아래 MED-2). 공유 그룹은 하지 않는다 — 아래 "단순화".
  4. `/etc/logrotate.d/kor-travel-docker-manager`가 생겼는지, Dashboard 배지가 2의 dump를 보는지 확인한다.
  5. crontab에 두 줄(16:50 UTC daily keep 7, 17:15 UTC `*/3` keep 3)을 파일 경유로 덧붙인다.
  6. 두 role이 **cron으로** 검증된 dump를 하나씩 남긴 뒤에만 transport 저장소의 cron 줄을 파일 경유로(정확히 한 줄)
     걷어낸다.
- **단순화(머지 전, 소유자 원칙 "결박을 쌓지 말 것")**: 409 가드가 생긴 뒤에는 공유 그룹이 cron의 안전에 필요하지
  않다 — 없으면 UI 생성만 거절되고 cron은 그대로다. 그래서 n150 절차의 3단계는 `.env` 한 줄 + 설치만 하고, 공유
  그룹(`chgrp -R`·`2770`·crontab 환경 줄)은 UI에서도 만들고 싶을 때의 선택으로 되돌렸다. installer는 그룹이 없으면
  logrotate를 `su root root`로 렌더링하고, cron 계정 소유 `0700` 디렉터리의 `<role>.log`는 그대로 돈다.
  `docker-management.md`·`prod-deployment.md` §3.x·wrapper 헤더를 같이 고쳤다.
- **적대 리뷰(`e3236951..2e99ae11`, MED 2·HIGH 0) 반영**:
  - MED-1: 공유 그룹이 선언되면 가드가 통째로 빠졌다. role 디렉터리가 없을 때 root backend가 그것을 만들면 주인이
    root라 cron 계정은 mode를 고칠 수 없고, crontab에 그룹 값이 빠지면(절반만 한 설정, 나중의 단순화) 첫 cron 실행이
    EPERM이다. 이제 공유 그룹은 **이미 있는** role 디렉터리만 면제하고, 없으면 그룹과 무관하게 부모 소유자와
    비교한다(409 메시지는 그 uid로 `ktdctl db-backup create <role>`을 안내). 새 테스트가 옛 코드에서 빨갛다.
  - MED-2: 절차가 가드 설치 **전에** `.env`를 바꿨다. 설치가 실패해 옛 release로 되돌리면 installer가 그 `.env`를
    가드 없는 release에 복사한다. 이제 3) `.env` 그대로 설치·확인 → 4) G lock 아래 `.env` 한 줄 + 같은 커밋 재설치.
    가드 이전 release로 되돌릴 때는 `KTDM_BACKUP_ROOT`를 먼저 뺀다.

## 2026-09-28 — 외부 target `airport`를 `transport`로 개명했다(Manager 쪽)

- **배경**: 소유자 지시로 형제 프로젝트의 배포 identity를 airport에서 transport로 바꾼다. 도메인(공항
  주차)은 그대로다. transport 저장소의 cutover — compose 프로젝트 `kor-travel-airport` →
  `kor-travel-transport`, 배포 사본 `/home/digitie/apps/kor-travel-airport` →
  `/home/digitie/apps/kor-travel-transport`, 컨테이너 `kor-travel-airport-<svc>-1` →
  `kor-travel-transport-<svc>-1` — 가 먼저 나가고, 이 Manager 릴리스는 그 창 **직후에** 머지·설치한다.
  옛 이름 `airport`·`kor-travel-airport`는 별칭으로 남기지 않는다(소유자 결정). 먼저 설치하면 Manager가
  아직 없는 컨테이너·디렉터리를 보고, 늦으면 옛 Manager가 사라진 것을 본다 — 어느 쪽이든 깨지는 것은
  상태 화면·수명주기 조작이고 transport 서비스 자체는 아니다.
- **변경**:
  - `config/docker-targets.yml`(한 커밋 — 검증기가 컨테이너의 `external_project`와 target 프로젝트의
    일치를 강제한다): `dependency_order`와 target 키, 좌표(`kor-travel-transport`,
    `/home/digitie/apps/kor-travel-transport`, `config_files` 그대로), `display_name`, description(공용
    instance의 `kor_travel_transport`), aliases `[kor-travel-transport]`, 컨테이너 둘(id·실제 이름·role·
    display_name). 포트 번호는 그대로다(선언 형태는 아래 리뷰 후속). #429가 뺀 전용 DB target·그 postgres 컨테이너·`depends_on`은 되살리지
    않았다.
  - `docker-compose.yml`의 transport 배포 사본 경로 주석, `registry.py`의 `ServiceGroup` 예시.
  - 문서: `ports.md`, `platform-topology.md`(설정 예시를 새 이름으로), `decisions.md` ADR-51
    "C 범위"(접두사 glob을 정확한 프로젝트 `kor-travel-transport`로 좁히고 같은 저장소의 별도 compose
    프로젝트 `kor-travel-transport-admin`은 범위 밖이라고 적었다), `prod-deployment.md`,
    `shared-postgres-onboarding.md`, `tasks.md`(순서 계약을 열린 항목으로).
- **테스트 구조**: 멀티프로젝트 메커니즘 검사가 실제 외부 target을 예시로 쓰고 있었다 — 묶음·실행·로그
  검사는 그 위에 얹은 합성 옛 DB target을, 검증기·이름 충돌 검사는 그 선언을 직접 고쳐 썼다. 이름이
  바뀌면 메커니즘 검사까지 따라 흔들린다. conftest에 합성 외부 target 쌍(`test-sibling` →
  `test-sibling-db`, 같은 디렉터리의 compose 프로젝트 둘 — 옛 실례와 같은 형태)을 두고 메커니즘은 그것으로
  센다. 새 이름을 따르는 것은 실제 선언을 겨냥한 검사뿐이다: 측정 좌표, `ensure` 거부, 컨테이너발 로그의
  소속 해석, 실제 컨테이너의 SDK 수명주기(실제 이름)와 config/reset guard, 409 핸들러,
  `--check-coordinates`.
  - 부분 문자열 검사를 정확한 값으로 바꿨다: `ensure` 거부 문구, 외부 변경 거부 문구, CLI의 생략 프로젝트
    안내 줄, `--check-coordinates`의 stderr 두 줄, `# project=` 헤더 둘과 그 순서. 409 검사는 손으로 만든
    예외 대신 실제 `reset` guard가 던진 예외를 받아 payload 전체를 대조한다.
  - 새 검사: `test_the_transport_target_declares_its_containers_under_the_new_names`,
    `test_the_old_airport_identity_resolves_nowhere`(target·별칭·컨테이너 id·실제 이름·소속 프로젝트·
    `working_dir`를 공개 해석 경로로 본다. 순회가 비면 부정 단언이 공허하므로 본 외부 target·컨테이너 목록에
    하한을 건다).
- **검증(n150, 아카이브 사본)**:
  - 대상 7개 파일: 리베이스 전 브랜치 `465eb49` 332 passed. origin/main `88599eb`의 코드·설정에 이 브랜치의 테스트만
    얹으면 14 failed — 새 검사 둘, 측정 좌표, `ensure` 거부, 컨테이너발 로그 소속, SDK 수명주기 셋,
    config/reset guard 둘, reset guard 둘(`name-differs`·`transport`), 409, `--check-coordinates`.
  - 전체 스위트(`b3-test.sh`, 리베이스 전 `b2c88cc`, ruff 0.16.4 `All checks passed!`): 1회차 2 failed, 2017 passed,
    2 skipped — `test_api.py::test_post_backup_returns_202_and_a_job_that_finishes`(`'failed' ==
    'succeeded'`)와 `test_compose_readiness_integration.py::test_canonical_compose_readiness_matches_real_runtime`
    (`docker compose ... up --detach --pull never` 30초 timeout). 2회차 1 failed, 2018 passed, 2 skipped —
    `test_api.py::test_a_failed_backup_job_reports_the_failure_instead_of_vanishing`. 둘 다 부하 속
    타이밍이다(다른 세션의 전체 스위트와 겹쳐 load 6.7~8.7). 두 1회차 실패는 브랜치·main 사본에서 따로
    돌리면 둘 다 통과했고, `test_api.py -k backup` 15회 반복은 main 6/15·브랜치 4/15로 같은 backup job
    검사가 흔들렸다 — 이 변경 이전부터 있는 flake다.
- **게이트**: `git grep -n "kor-travel-airport\|'airport'\|\bairport:"`의 히트는 이 journal의 역사 기록,
  새 부재 검사의 상수 셋, #430이 더한 `test_standalone_backup.py`의 합성 "다른 instance" 문자열 하나(개명
  전 전용 DB 컨테이너 이름을 흉내 낸 백업 manifest 값 — target을 가리키지 않는다)뿐이다. 코드의 과거 실측
  주석(`compose_service.py`의 2026-09-18 실례, `postgres_hba_posture.py`의 전용 DB `Cmd` 실측)과
  journal·tasks-done·ADR 서술의 과거 사실은 그대로 뒀다. 이 grep은 **리베이스할 때마다 다시 돌린다** — 한 번
  깨끗했다는 것은 그 뒤에 올라온 변경을 보증하지 않는다(아래 #430이 실례다).
- **리뷰 후속(같은 날, 적대 리뷰 MED 1·LOW 4)**:
  - **#430 위로 리베이스했다.** 첫 판은 "`feat/transport-backup-roles`와는 문서만 겹친다"고 적었다. 틀렸다
    — `backend/tests/test_docker_manager_cli.py`도 겹쳤고(자동 병합), 충돌은 `docs/journal.md` 하나였다. 더
    큰 것은 그 브랜치가 **새로 적은** 줄이다. `docker-management.md` "transport 복원" 3단계가 쓰기 주체를
    멈출 자리로 개명 전 배포 사본 경로를 가리켰다. cutover 뒤 그 자리에서 `docker compose ... stop`을 치면
    이미 은퇴한 옛 프로젝트를 멈추고도 성공으로 끝나고, 새 프로젝트의 진짜 쓰기 주체는 한 트랜잭션
    superuser 복원 내내 계속 쓴다 — "쓰기 주체를 멈췄다"는 전제가 조용히 깨진다. 새 디렉터리와
    `--project-name kor-travel-transport`를 적은 정확한 `stop` 명령, 멈춘 뒤 네 서비스가 `docker ps`에 없는지
    보는 확인으로 고쳤다. 그 사이 그 브랜치가 #430(`68cc1a9`)으로 main에 들어와 리베이스는 main 위로 했다.
  - **`expected_ports`를 실측으로 고쳤다.** transport backend·frontend는 `network_mode: host`,
    PortBindings 없음이다(n150 `docker inspect` 실측 — 운영 overlay `docker-compose.shared.yml`이
    `ports: !reset []`을 걸고 uvicorn·next가 호스트 14001·14002에 직접 listen한다). 선언 `14001:8000`·
    `14002:3000`은 운영에서 쓰이지 않는 base compose의 bridge 매핑이었고, 대시보드는 바인딩이 없으면
    `expected_ports`를 대신 보여 주므로(`DashboardClient.tsx`) 없는 매핑을 보여 줬다. 다른 host-network
    컨테이너처럼 `14001:14001`·`14002:14002`로 선언한다. 첫 판은 거꾸로 `platform-topology.md` 예시
    (`14001:14001`)를 틀린 선언 쪽으로 "바로잡았다"고 적었는데 예시가 맞았다 — 예시는 되돌리고 무엇을 위한
    선언인지 적었다. target 주석에는 `config_files`가 base 하나뿐인 이유를 적었다(shared overlay는 `${…:?}`
    필수 보간이라 env 파일 없이 열면 실패하고, `ps`·`logs`는 base만으로 같은 컨테이너를 찾는다 — 리뷰가
    n150에서 읽기 전용 `ps`로 확인했다). overlay를 더하는 일은 transport ADR-010 후속이다.
  - **`ports.md`**: transport 행에 Dagster 게이트웨이 `14003`·webserver `14004`·code-server `14005`(loopback,
    Manager 미등록 컨테이너)를 적었다. 미등록 외부 프로젝트 `kor-travel-transport-admin`이 이름뿐인 `cadv`
    대역 안의 `12301`·`12302`·`12305`를 `0.0.0.0`에서 쓴다(`ss -ltn` 실측) — `ports.md`와 `cadv` target
    주석에 적었다. Manager는 그 프로젝트를 모르므로 거기 새 포트를 배정해도 충돌을 알려 주지 않는다.
  - **순서 계약에 되돌리기와 머지 시점을 더했다**(`tasks.md`). transport runbook은 72시간 안의 `rollback`에서
    Manager를 스냅숏의 이전 sha로 다시 설치하라고 한다 — 그러지 않으면 되살아난 옛 컨테이너가 상태 화면·
    수명주기 조작에서 사라진다. 그 사이 다른 Manager PR이 들어왔으면 이 개명만 revert한 release를 설치한다.
    그리고 창이 끝나기 전에는 main에 머지하지 않는다 — 머지해 두면 다른 세션의 main sha 설치가 개명을 먼저
    내보내고, 그것을 막는 장치는 없다.
  - **메트릭 이력은 끊긴다 — 받아들인다.** 리뷰가 설치본(`ktdm-release-88599eb9`)의 런타임 상태를 읽기
    전용으로 훑었다. 결정을 내리는 상태(`/var/lib/kor-travel-docker-manager{,-public,-requests}`, 락 디렉터리,
    Grafana DB, Prometheus 설정 — 규칙·알림 없음)에는 airport 키가 없다. 조용히 잃는 것은 둘이다. 메트릭
    sqlite는 행을 `container_id`로 키하므로 transport 두 카드의 CPU·메모리 이력은 설치 시점부터 새로
    시작하고, 옛 id의 행(`kor-travel-airport-backend` 11,115·`-frontend` 11,275, #429가 뺀 전용 DB
    컨테이너 8,387)은 30일 정리(`cleanup_old_metrics(days=30)`)가 지울 때까지 고아로 남는다. `/metrics`의
    `ktdm_container_*` 계열은 `container_id`·`container_name`·`role` 라벨이 바뀌어 Prometheus 이력이
    갈라진다. 그 계열을 쓰는 대시보드·알림은 없으므로 깨지는 것은 없다. 연속성이 필요하면 설치 때
    `UPDATE metrics SET container_id='kor-travel-transport-backend' WHERE
    container_id='kor-travel-airport-backend'`(frontend도 같게)를 한 번 돌리면 되지만, 하지 않는다.
  - 검증(n150, 아카이브 사본, 리베이스 뒤 `8b93eb6`): 대상 9개 파일(멀티프로젝트 셋, CLI, 전역 락 경합,
    hba posture, targets 설정, standalone backup, Prometheus 메트릭) 430 passed. 전체 스위트(`b3-test.sh`,
    ruff 0.16.4 `All checks passed!`): 2 failed, 2052 passed, 2 skipped —
    `test_api.py::test_post_backup_does_not_refuse_where_no_other_account_is_harmed[nothing_exists_yet]`(#430의
    backup job 검사, `'failed' == 'succeeded'`)와
    `test_compose_readiness_integration.py::test_canonical_compose_readiness_matches_real_runtime`(compose `up`
    30초 timeout — 같은 날 다른 세션의 전체 실행에서도 실패, load 11~13). compose readiness는 따로 돌리면
    브랜치·main(`68cc1a9`) 사본 모두 통과했다. backup job 검사는 main 사본에서도 같은 모양으로 실패한다 — 그
    검사만 따로 13회는 브랜치 1·main 2 실패, `test_api.py -k backup` 18회는 브랜치 4·main 5 실패였고, 실패한
    job의 `error`는 역할과 무관하게(`geo`·`transport`) job runner의 `cancelled`였다 — 위 1회차와 같은 기존
    flake다.
    이번 후속은 새 검사를 더하지 않았다 — `expected_ports`는 표시 선언이라 값에 검사를 결박하지 않는다.
- **남은 것**: 설치 뒤 호스트에서 `ktdctl targets validate --check-coordinates`가 `OK`인지, cron 사본의
  `config/docker-targets.yml`을 설치본과 맞췄는지 본다. 머지는 transport 창이 끝난 뒤다. 다시 리베이스하면
  gate grep을 다시 돌린다.

## 2026-09-28 — 공용 PostgreSQL이 자기 고아 때문에 다섯 번 crash recovery했다 (ADR-52)

- **발견(kor-travel-geo 세션, 읽기 전용 조사 + 적대 검증)**: geo admin UI가 "KTG_PG_DSN 확인" 503을
  보이는 것을 쫓다가, `kor-travel-shared-postgres`가 09-25~28에 다섯 번 "server process (PID N) exited
  with exit code 2"로 crash recovery한 것을 찾았다. 매번 전 테넌트가 3~11분 끊겼다.
  - 죽은 PID는 로그를 남긴 적 없는 비-backend였다. PG16 `CleanupBackend`는 거둔 자식이 0·1 외 코드면
    BackendList 조회 전에 `HandleChildCrash`를 부르고, postmaster가 PID 1이라(`init` 없음) exec·healthcheck
    고아가 전부 그것의 자식이다.
  - healthcheck는 `CMD-SHELL pg_isready`(timeout 5초)였다. dash가 fork하고 `pg_isready`는 Perl
    `pg_wrapper`다. 호스트 stall로 timeout되면 `sh`만 죽고 고아 `pg_isready`가 exit 2로 끝난다 — #426의
    Dagster 좀비와 같은 기계인데, PostgreSQL에서는 클러스터 전체 재시작이 된다.
  - 09-25 08:03 unclean stop은 `systemctl restart docker`가 docker 기본 10초로 SIGKILL한 별개 사건이다.
- **변경**: `init: true`, exec 형식 probe(`/usr/lib/postgresql/16/bin/pg_isready -t 3`, docker timeout 10초),
  `stop_grace_period: 300s`, `shm_size: 512mb`. C6c 서버 형태 계약에 `init`·`stop_grace_period`를 더하고,
  healthcheck 프로그램 검사가 exec 형식에서 argv[1]만 보게 고쳤다 — 예전 검사는 인자 `-h`까지
  "비정본 프로그램"으로 거부해 이 변경이 배포를 막았을 것이다. 새 계약 테스트
  `test_shared_postgres_runtime_contract.py`, F1D 테스트 셋.
- **반영(이 PR 머지 뒤)**: 공용 instance 재생성이 필요하다 — 모든 테넌트가 짧게 끊긴다. geo 세션이 geo MV
  refresh가 끝난 뒤, 에이전트 빌드가 없는 시각에 한다. 재생성 전 `docker events ... event=exec_die`로
  고아 출처를 한 번 확인한다.

## 2026-09-29 — 대시보드의 stop/restart가 grace를 따르고, 한 서비스 재생성이 의존 서비스를 건드리지 않는다

- **배경**: Map DB를 공용 instance로 옮기는 작업(그 ADR은 튜닝 PR MT가 추가한다)에서 MT에 들어 있던 두
  수정을 떼어 먼저 낸다. 둘 다 compose 정의를 바꾸지 않아 설치해도 공용 instance의 재생성을 무장하지 않고,
  **지금** n150에 살아 있는 위험을 닫는다 — MT의 창을 기다릴 이유가 없다. n150은 Manager `0fe0d97`이고 공용
  instance는 ADR-52 설정(`stop_grace_period: 300s`)으로 09-28 13:10:57Z에 재생성돼 `Config.StopTimeout=300`이다.
- **위험 1 — 10초 SIGKILL**: `_control_container_unlocked`가 `container.restart()`·`container.stop()`을 인자
  없이 불렀다. n150에 설치된 docker-py 7.2.0은 `restart(self, container, timeout=10)`이라 **항상** `t=10`을
  보낸다. 그래서 대시보드·CLI(`action <id> restart`)의 재시작은 ADR-52의 300초를 무시하고 공용 postmaster에
  10초 뒤 SIGKILL을 보낸다 — shutdown checkpoint가 느린 날이면 다음 기동이 모든 테넌트의 crash recovery다.
  공용 instance에서 실제로 그런 재시작이 있었는지는 확인하지 않았다 — 코드의 동작을 적은 것이다.
  `stop()`은 `t`를 빼서 dockerd가 컨테이너의 300초를 쓰지만 docker-py의 HTTP read timeout(기본 60초)은
  10초만 늘리므로, 70초를 넘는 정상 종료는 client timeout 실패로 보고될 수 있었다.
  - **변경**: 컨테이너 자신의 `Config.StopTimeout`(compose `stop_grace_period`)을 읽어 있으면
    `timeout=`으로 넘긴다(`0`도 선언이다 — `None`으로 가른다). docker-py가 read timeout도 같은 만큼 늘린다.
    grace를 선언하지 않은 컨테이너는 예전과 같이 인자 없이 부른다. 리터럴 없음, 컨테이너에서 파생한다.
  - **대가(동작 변화)**: Manager가 소유한 컨테이너의 stop/restart는 그 grace(공용 instance 300초) 내내
    host 변경 lock을 쥔다. lock은 기다리지 않으므로(`LOCK_NB`, 설치 스크립트도 `flock -n`) 그 사이의
    rebuild·install·대시보드 변경은 곧바로 거부된다 — 예전에는 restart가 약 10초, stop이 최대 70초였다.
- **위험 2 — 의존 서비스 재생성**: 설정 변경(`POST /containers/{id}/config`), reset
  (`POST /containers/{id}/reset`), 없는 컨테이너의 시작은 모두 `_update_container_config_unlocked`에서
  `up -d --force-recreate <svc>`를 돌렸고, 그 실패 뒤 복구(`_restore_compose_transaction`)도 같은 argv였다.
  compose `up`은 `--no-deps`가 없으면 `depends_on`을 전이적으로 따라가 config hash가 어긋난 의존 서비스까지
  재생성한다. `0fe0d97` compose에서 공용 instance에 `depends_on`으로 직접 기대는 서비스는 20개(one-shot
  일곱 — db-init 다섯·`kor-travel-weather-migrate`·`pinvi-admin-bootstrap` — 와 geo·concierge·PinVi·weather
  서비스)이고, 다른 서비스를 거쳐 기대는 것까지 33개다(concierge ui·mcp·scheduler, geo ui, pinvi-web,
  weather web·gateway·prometheus, Map api·ui와 Dagster 셋). 공용 instance의 정의를 바꾸는 Manager를 설치한 뒤
  계획 재기동까지의 사이에 그중 하나의 env를 고치면 공용 instance가 CHECKPOINT도 실행·시각 확인도 없이
  재생성되고 모든 테넌트가 재시작할 수 있었다.
  - **변경**: 두 자리가 한 helper `_single_service_recreate_args`로 argv를 만들고, 그것이 `--no-deps`를
    넣는다. target 단위 `ensure`(`compose_service.py`)는 production에서 거부되고 rebuild는 이미
    `--no-deps`를 강제하므로 손대지 않았다.
  - **대가(동작 변화)**: compose는 의존 서비스를 만들거나 시작하거나 healthy를 기다리지 않고, one-shot도
    다시 돌리지 않는다. compose가 0으로 끝나면 API도 성공을 보고하므로, 공용 instance가 내려간 채 없는
    테넌트 컨테이너를 시작하면 DB 없이 뜬 컨테이너가 남는다. 의존 서비스는 운영자가 먼저 따로 올린다.
    `docs/docker-management.md` §7과 API 표에 적었다.
  - **효과의 증거**: 이 브랜치의 검사는 argv만 본다. compose가 `--no-deps`로 어긋난 의존 서비스를 그대로
    두는 효과는 리뷰가 n150 Compose v5.2.0 scratch project에서 손으로 확인했다(`--no-deps`: 의존 서비스의
    ID·StartedAt 불변 / 대조군: 의존 서비스 재생성과 one-shot 재실행). 기계 검사는 M1의 T-R3/T-R3c가 싣는다.
- **테스트**: `test_docker_service_config.py`에 `test_restart_passes_the_container_stop_timeout`
  (stop·restart × StopTimeout 247·0), `test_restart_without_stop_timeout_is_unchanged[stop|restart]`,
  `test_start_ignores_the_stop_timeout`, `test_public_restart_of_a_manager_owned_container_passes_its_stop_timeout`
  (공개 `control_container`가 c6c lock과 mutation 환경 계약을 지난 뒤에도 `timeout`이 닿는다). 컨테이너 id와
  이름은 `MANAGED_CONTAINERS`에서 Manager 소유 컨테이너를 골라 쓴다(target 이름 바꾸기가 이름과 무관한 검사를
  깨지 않게). 복구 argv는 main의 `test_config_recreate_failure_restores_exact_file_and_runtime`에 단언 하나로
  더했다 — 정방향과 복구가 같은 `--no-deps` argv다. 옛 argv를 박던 두 테스트는 `--no-deps`를 기대하고,
  `test_multi_project_boundaries.py`의 두 수명주기 fake는 실제 docker-py `Container`처럼 `attrs`를 들고 인자
  없이 불리는지 본다.
  - **red-check(n150, 선택 14건)**: 브랜치 14 passed. main의 `docker_service.py`로 되돌리면
    8 failed(argv 두 건, 복구 검사, grace 네 건, 공개 진입점). 변형마다: 복구 argv만 `--no-deps`를 빼면 1 failed,
    정방향만 빼면 3 failed, `if grace` 참/거짓 판정은 2 failed(StopTimeout 0 두 건), `restart()`만 인자를 빼면
    3 failed, grace 없는 컨테이너에 `timeout=10`을 넘기면 5 failed(`…_is_unchanged` 두 건과 수명주기 fake 세
    건), `start()`에도 grace를 넘기면 1 failed, 공개 `control_container`가 Manager 소유 컨테이너의 restart를
    `_unlocked`를 거치지 않고 인자 없이 부르면 1 failed(공개 진입점 검사만 — `_unlocked` 검사들은 초록).
    `test_start_ignores_the_stop_timeout`은 main에서도 초록이다 — grace 수정을 잡는 검출기가 아니라 `start()`
    확장을 막는 회귀 방지다.
- **남은 것**:
  - MT에는 튜닝 `command`, 이미지 digest 핀, `shm_size` 1gb, 백업 disk reserve와 그 문서·ADR이 남는다. 이
    브랜치가 머지되면 MT를 리베이스한다 — 같은 두 커밋이 빈 커밋으로 빠지는지 보고, journal 끝 append 충돌을
    풀고, MT의 journal·ADR에서 grace 전달과 `--no-deps`를 MT 몫으로 적은 줄을 지워 이 변경을 가리킨다. 설치
    뒤에는 spec의 머리말·§0.4·§1.2(f)와 §4.3/§4.5의 동결(공용 instance의 config hash가 어긋난 동안 config
    변경·reset·없는 컨테이너 start 금지)을 "이 변경으로 닫힘, 설치 sha"로 고친다.
  - 전용 Map instance `kor-travel-map-postgres`는 compose에 `stop_grace_period`가 없어 n150에서
    `Config.StopTimeout=null`이다(09-29 확인). 이 변경 뒤에도 대시보드·CLI의 stop/restart는 docker 기본 10초
    뒤 SIGKILL이다 — 이 변경이 만든 것은 아니다. 퇴역할 때까지 대시보드·CLI로 멈추지 않는다
    (`docs/docker-management.md` §7). grace를 주는 것은 compose 변경이라 별도 변경이다.

## 2026-09-28 — Map DB 이동 M1: 재구축의 tenant 울타리와 launcher의 adopt 통과

소유자 결정 C(Map의 두 DB를 공용 instance로)의 첫 PR이다. 오늘 topology(Map 전용 12700)에서도 안전하게
먼저 들어가는 것만 담았다 — 공용 instance 튜닝(MT)과 이동(M2)은 창 안에서만 머지한다.

- **launcher**(`scripts/run-pinned-rebuild-once`): `SHA OUT [--adopt-live-databases REASON]`. 이동은 같은 pair의
  재구축이라 새 DB identity를 받아들이는 adopt가 필요한데 launcher가 인자 둘만 받았다. REASON은 한 줄 1~200자
  printable이고 `-`로 시작하지 않는다(argparse가 옵션으로 읽는다) — lock·claim·출력 디렉터리보다 먼저 본다.
  `--restart`는 넘기지 않는다. 인자 둘의 argv는 그대로다.
- **R2 소유자**: instance admin은 어떤 drop의 허용 소유자도 아니다. Map drop 소유자는 첫 drop 전에 Map 쌍 밖의
  DB를 소유하지 않아야 한다(live `datdba`). Dagster metadata role의 password 회전은 그 role이 DB도
  `pg_shdepend` 행도 소유하지 않을 때만 한다. 공용 instance에서는 모든 tenant login이 후보라, 일관되게 잘못 박힌
  metadata user·Dagster DB 이름 한 쌍이 PinVi의 password를 돌리거나 `pinvi_dagster`를 지울 수 있었다(스펙 §1.1 b).
  C6c는 `KOR_TRAVEL_MAP_DAGSTER_METADATA_USER == KOR_TRAVEL_MAP_DAGSTER_POSTGRES_DB`를 재구축 전에 요구한다.
  - 부수 효과(의도): 12700에서 `ktm_feature_schema_owner`가 잔재 `ktm_40b`·`ktm_gcverify`도 소유하므로 거기서
    `--restart`는 이제 거부한다. 이 계획은 `--restart`를 쓰지 않는다.
- **이름 울타리**: `postgres`·`template*`을 수렴 경로·`create_database_if_absent`·격리에서도 거부한다(공용
  instance의 `postgres`는 admin 소유에 `alembic_version`이 없어 "bootstrap 전 Map DB"로 읽힌다).
- **R3 chokepoint**: 재구축의 모든 compose 호출이 지나는 `_run_pinned_runtime_rebuild_compose`에서 서비스를
  명시하지 않은 mutation과, 전용 집합(`_PINNED_RUNTIME_DATABASE_SERVICES`) 밖의 PostgreSQL 서버(declared ∪
  witnessed, 새 공개 helper `postgres_server_services`)를 거부한다. 해석기는 기존 것을 `_compose_mutation_scope`로
  나눠 재사용했다.
- **R4**: 배포(bootstrap 뒤, Map API 전)와 같은 pair 수렴(`up` 전)마다 두 Map DB에서 PUBLIC CONNECT를 걷고,
  app DB에 `KOR_TRAVEL_MAP_PG_DSN` login CONNECT와 `CONNECTION LIMIT floor(0.4 × usable)`를 건 뒤 읽어서
  확인한다(login 집합은 `has_database_privilege`로 — membership으로 얻는 CONNECT까지 잡는다).
  - 12700 읽기 전용 실측(10:24Z, `BEGIN READ ONLY`): 두 DB 모두 `datacl` NULL·`datconnlimit` −1, non-superuser
    LOGIN은 `kor_travel_map_dagster`·`krtour_map`·`ktm_feature_service`, app DB 소유자를 상속하는 login 0,
    Dagster DB 소유자를 상속하는 login은 그 자신뿐, `max_connections` 100·superuser 예약 3·예약 0 → usable 97,
    상한 38. 설치 뒤 같은 pair 수렴의 read-back이 통과할 모양이다. `krtour_map`은 두 DB의 CONNECT를 잃는다(스펙이
    받아들였다).
- **실 PostgreSQL 통합 테스트**(§1.6): 공용 instance와 같은 digest 이미지, `--network none`, S1 모양(Map 소유자
  = instance admin). T-R2a~d, T-NAME, T-R4, T-R4i, T-CAP과 membership read-back. compose 쪽 T-R3/T-R3c는
  `--no-deps`가 drift된 PostgreSQL을 한 번도 멈추지 않는 것과, 없으면 compose가 그것을 다시 만드는 것(§0.4의
  위험)을 실제로 보인다.
- **테스트(n150)**:
  - 커밋마다(`7d247d8`~`d4d1ce4`) launcher·`database_runtime`·재구축 단위 파일이 초록이다(165/165/183/213/238).
  - gate 켠 전체 스위트(`KTDM_REQUIRE_DOCKER_INTEGRATION=1`, `bd4aade`의 새 clone, n150 venv): **2154 passed,
    2 skipped**(root 전용 M05 ledger 둘), 8m12s. ruff 0.16.4 깨끗. 수집 수는 main `5b99322` 2054 → 2156, 새 사례
    102(단위 91, 통합 11).
    - 먼저 `git archive` 사본에서 돌린 같은 스위트는 5 failed였다: 넷은 git index가 있어야 하는 테스트(실행 비트,
      installer의 `git show`)라 clone에서 14 passed, 하나는 기존 `test_compose_readiness_integration`이 부하
      14~18에서 compose 30초 예산을 넘긴 것이다(재실행 31.6초 통과). 그 실행이 남긴 `Created` 컨테이너 셋은
      지웠다 — 이전 실행(다른 PID 넷)의 `ktdm-readiness-*` 잔재 12개는 내 것이 아니라 그대로 뒀다.
  - 빨강 확인(새 테스트를 `5b99322` 코드에): 단위 91 중 **79 red**, 12 green — launcher 회귀 가드 7(인자 둘의 argv,
    `--restart` 거부 모양 6), R3 허용 사례 4, R2 대조군 1. 통합 11 중 **7 red**(T-R2a·T-R2d·T-NAME·T-R4·T-R4i·
    membership·T-CAP), 4 green — T-R2b·T-R2c는 기존 울타리의 대조군, T-R3·T-R3c는 compose 자체의 동작이다.
    harness가 새 patch 대상(`ensure_map_databases_isolated`)을 허용하게 하면 기존 harness 테스트 70개는
    `5b99322`에서 전부 초록이다 — harness 변경이 그들의 단언을 바꾸지 않았다.
  - 변이 11개가 각자의 탐지기를 빨갛게 했다: chokepoint 끔(21), 공용 instance `up` 주입(4 — chokepoint 켬·끔 둘 다,
    끔일 때는 테스트 자신의 독립 단언이 잡는다), 수렴·배포의 격리 호출 제거(각 1), admin 차감 제거(6), 배타성 제거(2),
    회전 소유 검사 제거(3), ensure 이름 울타리 제거(4), read-back login 집합 끔(2), launcher 사유 검증 끔(8).
- **스펙과 다른 점**: R3는 스펙의 열 가지 동작 대신 해석기가 read-only가 아니라고 분류하는 **모든** 명령과 해석
  불가 명령에 건다(두 번째 목록을 두지 않는다). launcher는 `-`로 시작하는 사유도 거부한다. C6c 규칙을 넣으며
  `.env.example`과 두 계약 fixture의 metadata user를 Dagster DB 이름에 맞췄다. 통합 fixture는 PGDATA를 tmpfs에
  두고 `max_connections`로 상한 helper의 입력을 정한다.
- `/tmp/b3-test.sh`는 다른 세션의 Map pytest 때문에 BUSY로 재시도 중이었다 — 위의 clone 실행이 같은 스위트다.

## 2026-09-28 — M1 적대 리뷰 수정: R4를 live role 그래프에, R3를 compose가 닿는 범위에

M1(`fix/rebuild-tenant-fences`) 리뷰의 MED 셋과 값싼 LOW를 고쳤다. 코드는 `86cf82d`·`714b24c`·`b6ac031`, 문서
`13dec2e`.

- **R4(MED 둘)**: `ensure_map_databases_isolated`는 env에서만 온 이름에 grant한 뒤 같은 env 값과 비교하는
  read-back으로 확인했다 — 일관되게 잘못 박힌 env를 그대로 승인했다(리뷰 실측: 다른 login에 Map DB CONNECT,
  다른 tenant DB의 PUBLIC CONNECT 회수). 이제 한 `--single-transaction` 스크립트다.
  - 바꾸기 전 DO 전제: app DB 소유자 = `ktm_feature_schema_owner`, login은 LOGIN·non-superuser이고 그 소유자의
    member(Map bootstrap이 service login에 schema owner를 `INHERIT FALSE`로 준다), Dagster DB 소유자 = frozen
    metadata user이고 그 user는 다른 DB를 소유하지 않는다.
  - app DB의 명시 CONNECT 가운데 소유자·login 밖의 grantee는 ACL에서 유도해 걷는다(수렴 — 옛 login·손 grant 하나가
    이후 모든 수렴·배포를 막던 LOW). Dagster DB는 소유자 이름으로만 결박되므로 PUBLIC만 걷는다.
  - read-back은 같은 transaction의 DO 블록이다. 거부하면 GRANT·REVOKE·상한이 함께 롤백되고, 문구에 login 집합이
    실린다. commit 뒤의 Python read-back은 지웠다.
  - C6c는 `KOR_TRAVEL_MAP_PG_DSN`을 형제 다섯처럼 결박한다(`postgresql+asyncpg`, `127.0.0.1`, Map 포트, app DB)
    — login은 bootstrap·metadata user·Map principal이 아니어야 한다. b2 규칙은 자기 문구를 갖는다.
- **R3(MED)**: chokepoint가 이름 붙은 서비스만 셌다. `create`는 `--no-deps`가 없고 drift된 의존 PostgreSQL을 다시
  만든다. 이제 의존성으로 번지는 명령(`create`·`start`·`restart`·`scale`·`watch`·`up`·`run`)이 `--no-deps` 없이
  오면 frozen resolved 문서의 `depends_on` closure를 범위에 넣는다. `--remove-orphans`와 읽을 수 없는 문서
  (`services`·`depends_on`)는 거부한다 — `postgres_server_services`는 빈 집합 대신 거부한다.
- **launcher**: 공백만인 사유를 lock 전에 거부한다(CLI `not reason.strip()`와 같게).
- **문서**: §7.7에 "R4 거부" runbook(진단 쿼리·되돌리는 SQL), R2 runbook의 스키마 목록은 Map bootstrap을
  가리키게, 받아들인 남은 위험(아래)을 적었다.
- **남은 위험(M2가 ADR-53에 옮긴다)** — 앞 항목의 "foreign login은 거부된다"는 오늘 인벤토리에서만 참이다.
  - 자기 이름의 DB **하나만** 소유한 다른 tenant login은 metadata user·Dagster DB 이름으로 일관되게 박히면 R2
    배타성과 R4 전제를 지난다 — `--restart`는 그 DB를 지우고, adopt·기록 없는 배포의 R4는 그 DB의 PUBLIC CONNECT를
    걷는다. 공용 instance에는 그런 login이 없다(12:02Z 읽기 전용 실측: login 다섯 모두 `*_app`/
    `pinvi_application_runtime`, 자기 이름의 DB 없음). Manager가 기록한 DB identity에 결박하는 수정은 기록이 없는
    정당한 `--restart`(첫 배포, DB 없는 adopt)를 막고 PinVi drop 의미도 바꿔서 이번에는 하지 않았다.
  - PinVi drop 소유자에는 배타성이 없다 — M1 이전부터 있던 `--restart` 위험이다.
- **12700 수렴 전제 실측(12:02Z, 읽기 전용)**: `kor_travel_map` 소유자 `ktm_feature_schema_owner`,
  `ktm_feature_service`는 그 member(`pg_has_role … MEMBER` = t), `kor_travel_map_dagster`는 자기 DB만 소유,
  두 DB `datacl` NULL. live Map API의 `KOR_TRAVEL_MAP_PG_DSN` 모양은 `postgresql+asyncpg`·`127.0.0.1:12700`·
  `ktm_feature_service`·`/kor_travel_map`(값은 출력하지 않았다) — 설치 뒤 같은 pair 수렴이 새 전제와 C6c를 통과할
  모양이다.
- **테스트(n150, 14:02~14:36Z, `/tmp/m1fix-c-13dec2e`)**:
  - 표적 6 파일, gate=1, `13dec2e` 코드: **482 passed**, 0 failed, 0 skipped.
  - 빨강 확인(같은 테스트를 `6c77c9b` 코드에): **32 failed**, 450 passed. 새·바뀐 테스트 44 가운데 32가 빨강 —
    R4 통합 8/8, R4 단위 2, R3 거부 9, C6c 11(b2 문구 1, service DSN 10), launcher 2. 초록 12는 대조군(허용 6,
    disjoint 1), 재구축 argv 전체 검사 4(재구축의 호출은 바뀌지 않았다), compose 특성 T-R3d 1(Manager 코드를
    import하지 않는다).
  - 변이 11개가 모두 각자의 탐지기를 빨갛게 했다: read-back PUBLIC 열(`grantee = 0`→4294967295, 1), member 검사
    (2), app 소유자(1), Dagster 소유자(1), Dagster 배타성(1), 수렴(1), closure(6), orphans(1), 빈 services(1),
    service DSN(10), launcher strip(2).
  - `/tmp/b3-test.sh`: ruff 0.16.4 깨끗, **2184 passed, 2 skipped**.
  - gate 켠 전체 스위트(`KTDM_REQUIRE_DOCKER_INTEGRATION=1`, 공유 checkout `13dec2e`): **2184 passed, 2 skipped**
    (root 전용 M05 ledger 둘), 실 PostgreSQL·compose 통합 20건 통과, `ktdm-it-*` 잔재 0.
  - GitHub CI(dispatch 36421529963, `13dec2e`): 백엔드 2163 passed, 23 skipped(통합은 gate 없이 skip).
- 테스트 대역 정리: 공허한 이름 서로소 단언을 뺐고, T-R3/T-R3c는 compose 특성 테스트로 표시했으며, R3 end-to-end
  단언은 해석기를 다시 쓰지 않고 argv 낱말을 본다. `_SHARED_IMAGE` 중복은 MT가 compose에 digest를 핀한 뒤 렌더된
  서비스에서 읽게 바꾼다.
- 아직: rename PR이 머지되지 않아 리베이스 전이다. 리베이스 뒤 gate 켠 n150 실행을 다시 하고 수를 PR 본문에 싣는다.

## 2026-09-28 — M1을 #432·#433 위로 리베이스하고, 이동의 ADR 번호를 ADR-53으로 고쳤다

- `fix/rebuild-tenant-fences`(`aa04fcb`, 밑 `5b99322`)를 main `0fe0d97`로 옮겼다. 그 사이에 #432(외부 target
  `airport` → `transport` 개명)와 #433(ADR-52: 공용 instance의 `init`·exec probe·grace)이 들어왔다.
  - 텍스트 충돌은 `docs/journal.md` 끝의 추가 하나뿐이었다. 둘 다 두고 #432·#433 항목 뒤에 M1 항목을 이었다.
  - 겹친 나머지 넷(`c6c_deployment.py`·`test_f1d_compose_contract.py`는 #433, `docker-management.md`·
    `tasks.md`는 #432)은 hunk가 달라 자동으로 합쳐졌다. M1 diff에는 `airport`가 없다.
  - range-diff로 보면 커밋 13개 중 9개는 내용이 같다. 나머지 넷은 번호 고침(셋)과 journal 문맥(하나)만
    다르다.
- **ADR-52는 #433의 것이다. 이 이동의 Manager ADR은 ADR-53이다.** 이 번호를 쓴 자리 넷을 각 줄을 넣은
  커밋 안에서 고쳤다: launcher 테스트의 사유 문자열, `docker-management.md`와 journal의 "남은 위험" 두 줄,
  `13dec2e`의 커밋 메시지. 트리에 남은 `ADR-52`는 전부 #433의 것이다.
- **테스트(n150, `629b6a7`, `/tmp/wf19-m1-prep-629b6a7`)**:
  - gate 켠 전체 스위트(`KTDM_REQUIRE_DOCKER_INTEGRATION=1`, 새 `git clone`, consolidation venv): **2197 passed,
    2 skipped**(root 전용 M05 ledger 둘), 5m28s. docker 통합 21건(실 PostgreSQL 17, compose 3, readiness 1)이
    모두 통과했고 `ktdm-it-*` 잔재는 0이다.
  - `/tmp/b3-test.sh`: ruff 0.16.4 깨끗, **2197 passed, 2 skipped**.
  - GitHub CI(dispatch 36450493686): 백엔드 2176 passed, 23 skipped(통합은 gate 없이 skip), 프론트엔드 green.
  - 수집 수는 main `0fe0d97` 2067 → 2199다. 새 사례는 132개다.
- **빨강 확인(`0fe0d97` 코드)**: M1이 바꾼 테스트 파일 일곱은 HEAD로 두고, 나머지 파일은 모두 `0fe0d97`로 되돌려 돌렸다.
  - 새 132 중 **109 red**, 23 green. green은 모두 대조군이거나 회귀 가드다:
    - R2 대조군 3: Map 쌍만 소유한 owner의 drop, T-R2b, T-R2c.
    - R3 허용 사례 10.
    - compose 특성 테스트 3(T-R3·T-R3c·T-R3d). 이들은 Manager 코드를 import하지 않는다.
    - launcher 회귀 가드 7: 인자 둘의 argv 1, `--restart` 거부 6.
  - M1이 고친 기존 테스트 19도 빨갛다: 예약 이름 문구 12, metadata preflight의 새 필드 6, reset 호출 순서 1.
    나머지 기존 테스트 443은 초록이다. 따라서 harness 변경은 그 테스트들의 단언을 바꾸지 않았다.
  - 옛 코드에 없는 이름 때문에 빨강 실행에는 shim 둘이 필요했다.
    - harness의 `monkeypatch.setattr` 두 곳(module·service)에 `raising=False`를 붙였다.
    - 통합 파일의 `ensure_map_databases_isolated` import는 `getattr(…, None)`으로 바꿨다.
    - shim이 없으면 통합 파일의 수집 오류 하나에서 멈춘다. 처음에는 service 쪽 한 곳만 풀었는데, 그때는 기존
      harness 테스트 37이 patch 대상이 없다는 이유로 빨갰다. 두 곳을 다 풀자 그 37개는 초록이 됐다.

## 2026-09-29 — M1 리뷰 2차 LOW: 거부를 멈추기 전으로, `--no-deps`는 compose가 읽은 것만

M1 리뷰 2차(`b690967`)는 HIGH·MED 없이 LOW 여섯을 냈다. 값싸고 분명한 넷을 고쳤고, 하나는 문서로, 하나는
고치지 않았다.

- **멈추기 전 preflight(LOW 1·6)**: 전체 배포 경로는 R2·R4 전에 Map·PinVi를 멈춘다(`_deploy_forward`의 `stop`).
  그 뒤의 거부는 pair를 내린 채 남겼다 — 오늘 12700의 `--restart`는 schema owner가 잔재 `ktm_40b`·`ktm_gcverify`도
  소유해 매번 그랬고, DSN login을 Map의 member가 아닌 것으로 바꾼 뒤의 새 pair도 Map schema를 올린 뒤에 멈췄다.
  - `--restart`: `require_databases_resettable`이 리셋의 R2 판정(이름·허용 소유자·Map 소유자 배타성)을 읽기만으로
    `begin_deploy` 전에 돌린다. 판정 코드는 `_read_resettable_owners` 하나이고 리셋이 drop 직전에 다시 부른다(결박).
  - 일반·adopt: app DB가 `present`(schema owner 소유)면 `require_map_databases_isolatable`이 R4 transaction의 **바로
    그** DO 전제 블록을 `SET TRANSACTION READ ONLY`로 먼저 돌린다(Dagster DB가 있을 때). 전체 경로는 그 둘의
    소유자와 login membership을 R4 전에 바꾸지 않는다 — Map의 membership `GRANT`는 fresh bootstrap
    (`docker/postgres-role-bootstrap.sh`)에만 있고 `runtime_privileges`는 객체 권한만 준다. 없거나 bootstrap 전인
    DB는 미리 보면 거짓 거부라 만든 뒤 transaction 안에서만 판정한다. 같은 pair 수렴은 아무것도 멈추지 않아
    preflight가 없다.
- **R3 우회(LOW 2)**: `--no-deps`·`--remove-orphans`를 argv 전체에서 찾았다. `run SERVICE` 뒤는 컨테이너 argv이고
  `-e --no-deps`의 뒤쪽은 옵션 값이라 compose는 의존성을 끌어온다. 해석기(`_parse_compose_mutation`)가 범위와 함께
  **플래그로 읽은** 명령 옵션을 내고, R3·`up`/`run`의 `--no-deps` 요구·해석기 자신의 API 의존성 확장이 모두 그것을
  본다. startup gate는 첫 일치 낱말 대신 해석기의 명령 위치를 쓴다.
- **격리의 두 guard(LOW 4)**: 지우지 않고 테스트를 달았다. 이름 울타리는 "권한을 바꾸는 경로에 둔다"는 이 모듈의
  규칙이고, metadata user 개수 검사가 없으면 풀기(`(x,) = …`)가 `ValueError`로 샌다. 검사는 격리와 preflight가 함께
  쓰는 `_map_isolation_metadata_user`로 옮겼다.
- **상한 관찰(LOW 3, 문서)**: §7.7 R4에 설치 뒤 첫 수렴의 상한 38 관찰 항목(로그의
  `too many connections for database`, `numbackends`, 되돌리기와 소유자 결정 D5)을 적었다.
- **고치지 않음(LOW 5)**: R3의 `depends_on` closure를 "`--no-deps` 필수 + `create` 거부" 규칙으로 바꾸는 단순화.
  closure는 compose가 실제로 닿는 것에 결박하고(변이로 빨강 확인됨), MT가 같은 집합
  (`_COMPOSE_COMMANDS_THAT_REACH_DEPENDENCIES`)을 쓴다. 이득은 30줄이고, 재구축이 보내는 호출에서 판정은 같다.
- **MT 리베이스에 미치는 것**: MT(`feat/shared-instance-tuning`의 `2883068`)는 같은 해석기 줄에 자기 `no_deps`
  변수를 넣었다. 다음 리베이스에서 그 hunk는 충돌한다 — MT의 명령 집합(`_COMPOSE_COMMANDS_THAT_REACH_DEPENDENCIES`)과
  M1의 `parsed_flags`를 함께 두면 된다(MT의 `no_deps`는 `"--no-deps" in parsed_flags`와 같다).
- **테스트(n150, `24f3315`)**:
  - 표적 8 파일(`database_runtime`·재구축·통합 둘·launcher·F1D 계약·multi-project·docker config), gate=1, 패치한
    `b690967` 사본(`/tmp/wf19-m1-fix-b690967`): 첫 실행 **1 failed**, 662 passed — startup gate가 서비스 없는
    `up -d --no-deps`를 R3보다 먼저 "requires --no-deps"로 거부해 문구가 바뀌었다. 서비스를 말하지 않는 호출은 R3에
    맡기도록 고친 뒤 **663 passed**, 0 failed. 그 사본과 push한 트리는 `.ruff_cache` 말고 같다(`diff -r`).
  - 새 사례는 43개다(단위 40, 실 PostgreSQL 통합 3). 수집 수 2199 → 2242.
  - 빨강 확인: 변이 11개가 모두 각자의 탐지기를 빨갛게 했다 — preflight 호출 제거(5), R2 preflight 무연산(4), R4
    preflight 무연산(13), READ ONLY 제거(1), Dagster 부재 무시(1), 격리 이름 울타리 제거(16), metadata user 하나
    검사를 `sorted(…)[0]`로(4), startup gate·R3의 `--no-deps`를 argv 전체로(2·1), R3 `--remove-orphans`를 argv
    전체로(1), 해석기가 플래그를 모으지 않음(4). 복원 뒤 같은 44개는 초록이다. 새 43 가운데 39가 적어도 한 변이에서
    빨갛고, 초록으로 남은 넷은 대조군이다(`ps`·`down` 해석, bootstrap 전·부재 app DB에서 preflight를 부르지 않음).
    바꾼 기존 테스트 하나(`test_restart_skips_the_map_database_precheck`)도 빨갛다.
  - 중간 커밋 `0be1ad9`(해석기만): 재구축·multi-project 두 파일 **153 passed**.
  - gate 켠 전체 스위트(`KTDM_REQUIRE_DOCKER_INTEGRATION=1`, `24f3315`의 새 `git clone`, consolidation venv):
    **2240 passed, 2 skipped**(root 전용 M05 ledger 둘), 5m49s, `ktdm-it-*` 잔재 0.
  - `/tmp/b3-test.sh`: ruff 0.16.4 깨끗, **2240 passed, 2 skipped**, 5m12s.
  - GitHub CI(dispatch 36459717650, `24f3315`): 백엔드 **2216 passed, 26 skipped**(통합은 gate 없이 skip),
    프론트엔드 green.

## 2026-09-28 — 공용 instance를 Map의 튜닝으로 (MT, ADR-53 D4) — main(#432·#433) 위로 다시 세움

Map의 두 DB를 공용 instance로 옮기는 결정(오너 결정 C, ADR-53)의 튜닝 PR이다. 이 브랜치는 **창 안에서만**
머지·설치한다 — 설치하는 순간 공용 서비스의 compose config hash가 실행 중 컨테이너와 달라져, `--no-deps` 없는
`up` 한 번이 공용 instance를 recreate한다(모든 테넌트 재기동). 그래서 main에 설치되지 않은 채로 두지 않는다.

- **다시 세운 이유(적대 리뷰)**: 첫 판(`66e3f06`)은 `5b99322` 위였고, 그 사이 main에 #432(airport → transport
  개명)와 #433(ADR-52: `init: true`, exec probe `-t 3`/timeout 10초, `stop_grace_period: 300s`, `shm_size: 512mb`,
  C6c exec 파서)이 들어가 n150에 설치됐다(공용 instance 13:10:57Z 재생성). 첫 판은 다섯 파일에서 충돌했고,
  `docker-compose.yml`은 충돌 표시 **밖에서** `init`·`stop_grace_period`·`shm_size`를 두 벌 남겼다 — `safe_load`는
  뒤엣값(300s·512mb)을 조용히 읽고 `docker compose config`는 파일을 거부한다. 같은 번호 ADR-52도 두 개가 됐다.
  그래서 첫 판을 되살리지 않고 `0fe0d97` 위에 새로 세웠다: #433의 init·probe·grace·C6c 파서·ADR-52는 그대로
  두고, 첫 판의 init/probe 커밋(`7814218`·`66e3f06`)과 중복 테스트·ADR 문구는 버렸다. `c6c_deployment.py`
  diff는 없다. 이 결정의 ADR은 **ADR-53**이다.
- **compose**(`kor-travel-shared-postgres`): 튜닝 `command:`(`pg_prewarm,pg_stat_statements` preload,
  `pg_prewarm.autoprewarm=on`, `shared_buffers=1GB`, `work_mem=64MB`, `maintenance_work_mem=256MB`,
  `effective_cache_size=1536MB`, `random_page_cost=1.1`, `max_wal_size=2GB`), `${KOR_TRAVEL_SHARED_POSTGRES_*}`
  우회 제거, 이미지 digest 고정, `shm_size` 512mb → 1gb. grace 300s는 D4의 ≥120초를 이미 넘어 그대로다.
  `docker compose config -q`(v5.1.4, 모든 `:?` 변수를 채운 env) 통과, 공용 서비스 config hash는 main과 다르다
  (의도된 무장).
- **컨테이너 stop/restart의 grace 전달과 한 서비스 재생성의 `--no-deps`는 이 브랜치의 몫이 아니다.** 처음에는
  여기 있었지만 compose를 바꾸지 않아 recreate를 무장하지 않으므로 떼어 냈고, **#434**로 머지돼 n150에
  `6af5dd1`로 설치됐다(위 2026-09-29 "대시보드의 stop/restart가 grace를 따르고…" 항목). MT는 그 위로 다시
  올렸다(아래 2026-09-29 재기반 항목).
- **백업 예약분**: max(2 GiB, 살아있는 `max_wal_size` + 1 GiB). `create`는 그 값을 이미 하던 database 크기
  질의와 **같은 exec**로 읽는다(새 exec·새 DB 의존 없음, `--expected-dump-bytes`면 `max_wal_size`만). 읽지 못하면
  pg_dump 전에 거부한다. `rehearse-restore`는 WAL을 두 번 센다 — 일부러 보수적이라고 docstring과
  docker-management.md에 적었다.
- **테스트**: 튜닝·digest·shm ≥ 1 GiB·grace ≥ 120초·probe timeout ≥ 2×`-t`·파서는 main의
  `test_shared_postgres_runtime_contract.py` 한 파일(서비스 로더·duration/크기 파서 하나)에 둔다. 로더는 중복 키를
  거부한다(`yaml_strict`) — `_real_compose_config`도 같다. 격리 실행 테스트
  `test_shared_postgres_runtime_integration.py`(gate `KTDM_REQUIRE_DOCKER_INTEGRATION`)는 기대값을 전부 compose에서
  읽는다: probe로 healthy, 모든 `-c` 값이 `pg_settings`에서 `command line`으로, autoprewarm leader, PID 1 =
  docker-init, StopTimeout·ShmSize = 파싱한 grace·shm, grace 안의 깨끗한 정지.
- **빨강 확인(n150, git-archive 사본, 테스트는 새 것·src와 compose는 `0fe0d97`)**: compose 계약 3/8(튜닝·digest·
  shm), 백업 11/11(단언에서 6 —
  예약분 3·못 읽는 답 1·transport 크기 질의 2, 새 helper API가 없어서 5), gated 격리 실행 1/1(튜닝 값
  read-back에서). 대상 테스트 7파일은 새 head에서 482 passed, ruff(0.16.4) 통과. 옛 코드에서도 초록인 가드는 변이로 빨갛게 만들었다: grace 119s → 1/1,
  probe timeout 5s → 1/1, 중복 키 한 줄 → 9/10, 크기 파서 단위 → 2/2, 예약분 headroom 빼기 → 1/3(2GB 경우만 —
  나머지는 하한이 지배). grace 전달·`--no-deps`의 빨강 확인은 #434 항목에 있다. 482 passed는 그때 이 브랜치에
  함께 있던 그 두 수정의 테스트를 포함한 수다.
- **남은 것**: M1이 `0fe0d97` 위로 올라오면 그 위로 다시 올리고, §1.6 실 PostgreSQL 테스트를 포함한 gated
  실행을 다시 돈다. 전체 스위트·gated 실행의 최종 수치는 PR 본문에 적는다. 머지는 창 안에서, PR head와 머지
  SHA의 `git diff --stat`이 비었는지 확인한 뒤다.

## 2026-09-29 — MT 리뷰 수정: M1 위로 재기반, recreate 위험의 경계, 예약분 테스트의 판별력

MT(ADR-53 D4)의 적대 리뷰 MED 2건·LOW 14건을 반영했다. 이 브랜치는 여전히 **창 안에서만** 머지·설치한다.

- **M1 위로 올렸다**(`origin/fix/rebuild-tenant-fences` `b690967`, 스펙 §1.0의 rename → M1 → MT → M2 사슬).
  충돌은 이 파일 하나였다(두 항목을 다 둠). 그때 함께 있던 두 fix 커밋(`fix(docker): …`)은 #434로 squash
  머지돼 다음 재기반에서 손으로 뺐다(아래 2026-09-29 재기반 항목).
- **MED — recreate 위험의 경계가 좁았다.** ADR-53의 받아들인 위험·helper docstring·스펙의 A1/T-R 공지가
  "`--no-deps` 없는 `up`"만 적었다. n150 Compose v5.2.0에서는 `run --rm <의존 서비스>`도 drift된 의존을
  재생성한다(리뷰 실측). 공용 instance를 `depends_on`하는 서비스에는 db-init one-shot과 `pinvi-admin-bootstrap`도
  있고, onboarding §7.3은 db-init 재실행을 자가치유로 권한다 — Manager 코드가 아니라 사람·에이전트의 명령이
  남은 길이다. 그래서 ADR-53·스펙(§0.4·A1·T-R)이 `up`·`run`(`--no-deps` 없이)과 `create`(그 플래그가
  없다)를 모두 적고, 공지가 drift guard의 MATCH까지 db-init 재실행도 동결한다. 서비스 개수는 적지 않는다
  (리뷰가 센 것은 19였고, 테넌트가 늘 때마다 바뀐다).
  - 범위 해석기(`_compose_mutation_scope`)는 의존성까지 닿는 명령을 M1의 `_COMPOSE_COMMANDS_THAT_REACH_DEPENDENCIES`
    하나로 본다 — `run`(과 `start`·`scale`)이 들어간다. `--no-deps`는 compose가 플래그로 읽은 것만 센다: `run
    SERVICE` 뒤 컨테이너 argv의 같은 글자는 compose 플래그가 아니다. 그 판정은 M1(#435)의 `parsed_flags` 하나이고
    R3 chokepoint도 같은 것을 본다.
  - 대시보드의 한 서비스 재생성이 `--no-deps`가 되면서 바뀐 동작(의존 서비스를 띄우지도 기다리지도 않고
    one-shot을 다시 돌리지 않는다)은 #434가 docker-management.md §7과 helper docstring에 적었다.
- **MED — 예약분 테스트가 유도를 가르지 못했다.** 유도하는 세 자리 중 `--expected-dump-bytes` 경로와
  rehearse-restore의 테스트가 가짜 `max_wal_size` 1 GiB를 썼고, 그 값에서는 유도값 = 하한 2 GiB라 고정
  상수로 되돌려도 초록이었다(리뷰 변이 둘 다 93/93 통과). 세 테스트가 한 표 `_RESERVE_CASES`(2GB → 3 GiB,
  1GB → 2 GiB, 512MB → 2 GiB, 기대값은 리터럴)로 돈다. 그리고 `_require_free_space`가 `max_wal_size`를 분기
  **앞에서 한 번** 읽는다 — geo 첫 백업의 비상 경로(디스크가 가장 빠듯한 때)만 WAL을 빠뜨리는 갈래가
  구조로 없어졌다. 크기와 WAL을 한 exec로 읽던 세 번째 psql argv 사본(`_query_db_size_and_max_wal_bytes`)은
  지웠다: 운영자 값 경로와 manifest용 크기 질의 때문에 절약이 이미 부분적이었고, 대가는 백업마다 30초 상한
  exec 하나다.
- **LOW**:
  - ADR-53 상태 줄이 결정마다 효력을 갖는 PR을 적는다(4 = MT, 3의 울타리 = M1, 1·2·5와 3의 공용 instance
    부분 = M2). ADR-35·37의 supersede 표시는 그것을 사실로 만드는 M2가 단다.
  - 격리 실행 테스트는 이제 Manager의 정지 경로(`_control_container_unlocked`)로 실제 docker-py 컨테이너를
    멈춘다 — daemon이 보고한 `Config.StopTimeout`이 `timeout`으로 닿는지 본다. Docker probe의
    `OSError`/`TimeoutExpired`는 `test_compose_readiness_integration.py`의 gate helper로 skip/fail한다(gate 0에서
    ERROR가 아니다). M1의 두 통합 파일은 아직 gate helper 사본을 따로 든다(M1 몫).
  - duration 파서는 compose가 실제로 쓰는 `5m0s`에 결박했다. (`--no-deps` 복구 argv 단언과 중복 테스트 정리는
    #434의 것이다.)
  - `_disk_reserve_bytes`는 dump를 뜨는 그 instance의 WAL만 덮는다고 적었다(M2가 Map 전용 instance를 퇴역시킬
    때까지의 일시적 틈). docker-management.md의 수치는 `2N + 예약분`(D4 뒤 3 GiB)으로: geo 비상 백업 약
    15.2 GB, 그 뒤 약 12.6 GB. transport는 n150 읽기 전용 실측(`pg_database_size` 15,405,461,987 B)으로
    리허설·복원 약 21.8 GB(WAL을 한 번 세면 약 19.6 GB), 첫 실행 상한 약 34.0 GB.
  - onboarding §1.1의 튜닝 행은 그 전 기록 그대로 되돌렸다(머리말이 D4 값을 갖는다).
  - rehearse-restore가 WAL을 두 번 세는 것은 그대로 둔다 — 일부러 보수적이고 docstring에 근거가 있다.
- **n150 실행**(모두 `/tmp`, 운영 컨테이너는 읽기 한 번 — transport 크기):
  - 빨강 확인: 해석기 새 테스트를 리뷰 전 `compose_service.py`(`b814370`)에서 돌리면 2/5(`run`,
    `run-argv-no-deps`). 예약분 변이 — 운영자 값 경로가 WAL을 0으로 → 3/3(2GB는 필요량으로, 나머지는 거부 문구의
    `max_wal_size`로), rehearse-restore가 하한만 → 1/3(2GB만 — 이 경우가 없으면 초록이다, 리뷰가 본 그대로),
    대조군 추정 경로가 WAL을 0으로 → 3/3. (한 서비스 재생성의 `--no-deps` 변이는 #434 항목에 있다.) Manager
    정지 경로(#434)가 timeout을 넘기지 않으면 격리 실행 테스트가 `assert [{}] == [{'timeout': 300.0}]`로 빨갛다.
    `PATH`에 docker가 없을 때 gate 0: 새 파일 1 skipped, 리뷰 전 파일 1 error(`FileNotFoundError`); gate 1은
    새 파일도 1 error(의도한 gate 실패).
  - 대상 8파일 633 passed(`4f81ba4`). gated(`KTDM_REQUIRE_DOCKER_INTEGRATION=1`, 새 `git clone`, M1의 두 통합
    파일 포함) 22 passed(`4f81ba4`), fixture 잔여 없음. 전체 스위트(`b3-test.sh`, `2b349a8`): ruff 0.16.4 통과,
    2220 passed, 2 skipped. 그 뒤의 커밋은 이 journal뿐이다.
- **남은 것**: M1이 더 바뀌면 다시 올린다. 분리 브랜치는 #434로 머지됐고 MT는 그 위로 올렸다(아래). PR 본문에 위
  수치를 옮기고, 머지는 창 안에서 PR head와 머지 SHA의 `git diff --stat`이 비었는지 확인한 뒤다.

## 2026-09-29 — MT를 main `6af5dd1`(#434·#435) 위로 다시 올렸다

- **바탕**: main이 `0fe0d97`에서 `6af5dd1`로 움직였고 n150에 설치됐다 — #434(컨테이너 stop/restart의 grace 전달과
  한 서비스 재생성의 `--no-deps`, MT에서 떼어 낸 것)와 #435(M1). 둘 다 squash 머지라 MT에 든 커밋들과 patch-id가
  같지 않아 rebase가 스스로 떨어뜨리지 못한다. 그래서 `git rebase --onto origin/main 0f22094`로 M1 브랜치의
  14커밋(#435에 들어감)과 떼어 낸 두 커밋(`f2db08e`·`0f22094`, #434에 들어감)을 명시적으로 빼고 MT 자신의
  10커밋만 옮겼다. 두 커밋의 내용이 #434에 다 있다는 것은 옮긴 뒤 MT의 main 대비 diff에 `docker_service.py`와
  `test_multi_project_boundaries.py`가 없고 `test_docker_service_config.py`는 `yaml_strict` 로더 5줄뿐인 것으로
  확인했다.
- **충돌 풀이**:
  - `test_docker_service_config.py` import: main의 `registry` import와 MT의 `yaml_strict` import를 둘 다 둔다.
  - `journal.md`: main의 항목(M1 리뷰 2차)을 먼저, MT의 두 항목을 그 뒤에 둔다.
  - `compose_service.py` 범위 해석기: MT의 `no_deps` 변수를 버리고 M1의 `parsed_flags`(compose가 플래그로 읽은
    것)로 센다. 명령 집합은 main의 `_COMPOSE_COMMANDS_THAT_REACH_DEPENDENCIES` 하나다. MT가 main에 더하는 것은
    API 의존성 확장이 `{"up", "create", "restart", "watch"}` 대신 그 집합을 쓰는 조건 한 줄과 주석뿐이다
    (`run`·`start`·`scale`이 들어간다).
  - `4820eed`의 `--no-deps` 복구 argv 단언·중복 테스트 삭제와 `4f81ba4`의 helper docstring·docker-management.md
    대시보드 문단은 #434가 이미 main에 둔 것이라 main 쪽을 택했다. 두 커밋 메시지도 그만큼 줄였다.
- **ADR-53·journal**: grace 전달과 `--no-deps`를 MT(결정 4) 몫으로 적은 줄을 지우고 #434를 가리킨다. ADR-53 상태
  줄의 결정 4는 이제 튜닝과 백업 예약분이다. compose 주석과 onboarding C9의 "Manager의 stop/restart가 grace를
  따른다"는 사실 서술이라 지우지 않고 `(#434)`를 달았다.
- **compose 검사**: Manager의 `yaml_strict` 로더(`load_yaml_rejecting_duplicate_keys`)로 `docker-compose.yml`을
  읽으면 중복 키 없이 서비스 44개다. `kor-travel-shared-postgres`는 #433의 `init: true`, exec probe(`pg_isready …
  -t 3`, `timeout: 10s`), `stop_grace_period: 300s`를 그대로 갖고, MT의 튜닝 `command`(`-c` 10개, 튜닝 값에
  `${KOR_TRAVEL_SHARED_POSTGRES_*}` 없음), digest 핀, `shm_size: 1gb`를 더한다. 대조군: `stop_grace_period`
  한 줄을 복제하면 같은 로더가 `ConstructorError`로 거부한다.
- **n150 실행**(모두 `/tmp`, 운영 컨테이너·DB는 건드리지 않았다. chain17 t64a가 끝난 21:29Z 뒤, `6453239`):
  - gated 전체 스위트(`KTDM_REQUIRE_DOCKER_INTEGRATION=1`, 새 `git clone`, consolidation venv): **2266 passed,
    2 skipped**(root 전용 M05 ledger 둘), 0 failed, 9m51s. `ktdm-it-*`·`ktdm-sharedpg-*` 잔재 0.
  - `/tmp/b3-test.sh`: ruff 0.16.4 깨끗. 전체 pytest는 세 번 모두 **1 failed, 2265 passed, 2 skipped**였다. 실패는
    셋 다 main의 `test_compose_readiness_integration.py::test_canonical_compose_readiness_matches_real_runtime`이
    docker 호출의 30초 subprocess timeout에 걸린 것이다(한 번은 `docker stop`, 두 번은 `docker compose up`; load
    6~19, 실행 시간 6m39s·18m52s·11m48s). 환경으로 판정한다: MT가 건드리지 않는 파일이고, 같은 head의 gated
    실행에서는 초록이었고, 같은 부하에서 그 테스트 하나를 떼어 돌리면 main `6af5dd1`에서도 빨갛다(MT 두 번·main
    한 번, 모두 1 failed).
  - **빨강 확인(새 테스트 대 main)**: 새 `git clone`에서 테스트가 아닌 경로 10개(`backend/src`의 두 파일, compose,
    docs)를 `6af5dd1`로 되돌리고 MT가 바꾼 테스트 다섯 파일을 gate=1로 돌렸다 → **18 failed, 341 passed**. main
    대비 새 테스트 id 20개 중 **14개가 빨갛다**:
    - 범위 해석기 2/5 — `run`, `run-argv-no-deps`. `up`·`up-no-deps`·`run-no-deps`는 대조군이라 main에서도
      초록이다.
    - compose 계약 2/3 — 튜닝 `command`, digest 핀. duration·크기 파서 테스트는 테스트 자신의 파서를 보므로
      main에서도 초록이다.
    - gated 격리 실행 1/1.
    - 백업 9/11 — 예약분 3, 운영자 값 경로 3, 못 읽는 답 2, rehearse-restore 2GB 1. rehearse-restore의 1GB·512MB는
      유도값이 하한 2 GiB와 같아 main에서도 초록이고, 2GB 경우가 그 둘을 가른다.
    - 바뀐 기존 테스트 4개도 빨갛다(shm ≥ 1 GiB 하나, 백업 필요량 셋). 사라진 id 2개는 parametrize로 바뀐 두
      백업 테스트다.
    - `test_docker_service_config.py`의 중복 키 로더는 main에도 중복 키가 없어 초록이다. 이것은 가드이고, 빨강은
      앞 항목의 변이(9/10)가 보였다.
  - 잔재: b3 두 번과 떼어 돌린 readiness 실행 세 번이 남긴 `ktdm-readiness-*` 컨테이너 15개를 지웠다. 셋 다
    `Created`였고, 일부는 client 시한이 지난 뒤 daemon이 만든 것이다. 09-28 08:05의
    `ktdm-readiness-2020836-readine0_default` 네트워크는 이 작업의 것이 아니어서 두었다.

## 2026-09-29 — MT 리뷰 3차: 운영 이름에 닿지 않는 정지 시험, prewarm·DSM의 정직한 서술, 범위 해석기 hunk 철회

MT(ADR-53 D4)의 세 번째 적대 리뷰 MED 3건(그중 둘은 같은 것)·LOW 12건 가운데 MT 몫을 반영했다. 이 브랜치는
여전히 **창 안에서만** 머지·설치한다.

- **바탕**: main이 `c66ec1c`에서 `24f6c57`로 움직였다 — #436(문서)·#437(`.env.example`·dev-environment.md, geo
  Dagster URL 드라이버). 둘 다 MT가 건드리지 않는 파일이라 충돌 없이 다시 올렸다. 위 항목들이 적은 `6af5dd1`·
  `6453239`·`289cdfa`는 그 전 head다.
- **MED — 격리 정지 시험이 운영 이름으로 풀렸다.** `test_shared_postgres_runtime_integration.py`는 Manager의 정지
  경로를 운영 registry 키(`kor-travel-shared-postgresql`)로 불렀고, 그 이름은 운영 공용 instance다. 격리를 지키는
  것은 `_get_client` 패치 하나뿐이라, 메서드가 client를 다른 길(`docker.from_env()`·캐시된 client)로 얻게 바뀌면
  n150(운영, 머지 gate가 도는 곳)에서 운영 공용 instance를 300초 grace로 멈춘다(사용자 정지는
  `unless-stopped`가 되살리지 않는다). 이제 docker_service가 보는 registry를 throwaway 키(`ktdm-it-sharedpg`)
  **하나뿐인** 사본으로 갈아 끼우고 그 이름을 격리 컨테이너로 둔다 — 운영 키는 아예 풀리지 않는다. 격리 이름이
  fixture의 compose 프로젝트(`ktdm-sharedpg-…`)로 시작하고 공용 서비스의 registry 이름과 다른지 먼저 단언하고,
  fake client의 `get`도 받은 이름을 확인한다. (리뷰가 제안한 `monkeypatch.setitem(registry.MANAGED_CONTAINERS, …)`
  은 쓸 수 없다 — `MANAGED_CONTAINERS`는 쓰기 없는 `_LazyMapping`이다.)
- **MED — prewarm은 모든 DB를 덮지 않는다.** dump는 덮지만 reload는 DB OID 순이고 free buffer가 바닥나면 멈춘다
  (REL_16_STABLE `autoprewarm.c`: `apw_compare_blockinfo` 정렬, `apw_load_buffers`의 `!have_free_buffer()`,
  worker 루프의 같은 조건). 1GB dump는 NBuffers에 가깝고 재기동 중 다시 붙는 테넌트가 free buffer를 쓴다 — OID가
  가장 큰 DB, M2 뒤에는 Map의 둘이 마지막이고 못 올라올 수 있다. ADR-53 결정 4, compose 주석,
  platform-topology.md를 고쳤다. 감시(runbook A7i·§4.7)는 기동마다 `autoprewarm successfully prewarmed N of M`을
  기록하고 큰 부족을 발견으로 친다. per-DB 단계는 두지 않는다(D4 범위).
- **LOW 반영**:
  - 범위 해석기 hunk와 그 테스트를 **철회**했다(`compose_service.py`·`test_pinned_runtime_rebuild.py`는 main과
    같다). 식별자 목록은 비었는지로만 쓰이고, 의존성 범위는 M1의 R3가 frozen resolved 문서의 `depends_on`
    closure로 본다. 그 hunk가 넓힌 것은 손으로 쓴 한 단계 API 대응표였고, 테스트 이름은 compose가 닿는 것을
    센다고 했지만 `run --rm pinvi-web`에서 compose는 공용 instance·db-init·map-api 등까지 닿는다. 그래서 앞
    항목들의 "범위 해석기가 `run`을 센다"는 이제 사실이 아니다 — `run`의 위험은 R3·재구축의 `--no-deps`·손 명령
    동결이 덮는다.
  - `run` 특성 시험 두 개(T-R3e/f): `run --rm <dependent>`는 `--no-deps` 없이 drift된 의존 PostgreSQL을 다시
    만들고, `--no-deps`면 그대로 둔다. 창의 동결과 ADR-53의 위험이 기대는 측정이다.
  - M1의 두 실 PostgreSQL 파일이 따로 들던 `_SHARED_IMAGE` digest 사본을 정본 compose의 핀에서 읽는다(계약
    파일의 로더). gate helper 사본도 readiness 파일의 것(`_required_docker_gate`·`_unavailable_docker_fixture`)으로
    합쳤다.
  - probe의 `docker timeout ≥ 2×-t` 단언을 뺐다 — D4가 아니고 ADR-52의 다음 조정을 묶는 결박이다.
  - ADR-53 받아들인 위험: #433 대비 DSM 여유가 절반(병렬 hash 노드 상한 96MB → 384MB, shm 512mb → 1gb, 약 5.3 →
    2.7개)이고 autoprewarm leader가 worker 한 칸(7 → 6)을 쓴다 — 위험을 **줄일 뿐 없애지 않는다**. 72시간 안의 새
    DSM 오류는 hard alarm이고 미리 합의한 후속은 `shm_size: 2gb`다. `max_wal_size=2GB`는 crash replay를 약 두 배까지
    늘리고 90초 `TimeoutStopSec` 안에 내려야 할 dirty set을 키운다 — drop-in은 MT와 함께 하기를 권한다.
  - 공용 서비스 **자신**의 대시보드·CLI 설정 변경·reset도 동결한다(`--no-deps`는 이름 붙은 서비스의 재생성을
    막지 않는다). stop/restart는 재생성이 아니라 허용한다. ADR-53 문장을 고쳤다.
  - `ktdctl db-backup create --expected-dump-bytes`의 도움말이 예약분을 고정 2 GiB로 적었다 — max(2 GiB, 그
    instance의 살아있는 `max_wal_size` + 1 GiB)로.
- **MT 몫이 아닌 것**: `/opt/.env`의 UI admin password hash 네 개에 든 `$`가 보간되는 문제는 MT 밖이라 오너에게
  넘긴다(runbook §4.8). CI 증거·머지 동결·B5/B6 절차는 runbook(§1.0·§4.3·§4.4)에 적었다.
- **n150 실행**(모두 `/tmp`, 운영 컨테이너·DB는 읽기만 — `postgres:16-alpine`·공용 postgis 이미지 inspect):
  - 대상 8파일(계약·격리 실행·T-R3 특성·실 PostgreSQL·재구축·백업·CLI·docker_service 설정), gate=1,
    `git archive`한 `19adc54` 트리: **521 passed**, fixture 잔재 없음. ruff 0.16.4 통과. 부하(load 12~14, 다른
    세션의 transport `up --build`) 때문에 16분 16초였다.
  - 빨강 확인(버리는 사본에 변이, gate=1): 세 테스트 모두 빨갛다(3 failed).
    - 정지 경로가 `_get_client` 대신 `docker.from_env()`로 client를 얻게 바꾸면 격리 정지 시험이
      `assert [] == ['ktdm-sharedpg-…-pg-1']`로 빨갛다 — 패치가 우회돼도 이름은 격리 컨테이너로만 풀려 그 컨테이너만
      멈췄고, 운영 공용 instance의 `Id`·`StartedAt`(`2026-09-28T13:10:57Z`)·상태는 실행 전후가 같았다.
    - `run` 특성 시험에 `--no-deps`를 붙이면 T-R3e가 `pg` 컨테이너 ID 동일로, 빼면 T-R3f가 다시 만들어진 `pg`에
      접속하지 못해(새 컨테이너가 아직 기동 중) 빨갛다.
    - 변이 없는 같은 세 테스트는 3 passed(8분 31초).
  - 전체 스위트와 gated 전체 실행은 verify 단계의 몫이다. 그 수치와 CI run ID·`headSha`는 PR 본문에 적는다.

## 2026-09-29 — Map DB 이동 M2: 적대 리뷰 수정(유도한 one-shot 계약, 멈추기 전 판정 둘, 공용 instance의 R4, 되돌림의 결합 상태)

M2(`feat/map-db-shared-instance`, MT 위)의 적대 리뷰 MED 2건·LOW 11건 가운데 M2 몫을 반영했다. 이 브랜치는 창
§4.4 B5에서만 머지·설치한다. main `24f6c57`(#436·#437) 위의 새 MT 위로 다시 올렸다 — `.env.example`·
dev-environment.md는 #437과 겹치는 줄이 없어 충돌이 없었다.

- **MED — one-shot의 mount가 코드 리터럴이었다(유도 → 결박 회귀).** `_MAP_DB_ROLE_BOOTSTRAP_MOUNTS`와 raw 검사가
  두 bind의 경로·`${KOR_TRAVEL_MAP_REPO_DIR:-../kor-travel-map}` 기본값·`:ro`를 적었는데, 신뢰된
  `config/docker-targets.yml`의 `compose_binds.kor-travel-map-db-role-bootstrap`이 이미 같은 사실을 든다(GM-17이 그
  절을 정본으로 만든 이유가 "새 bind에 backend 수정·재설치가 필요 없게"다). 이제 one-shot의 `volumes`는 그 항목과
  **원소 단위로** 같아야 하고 전부 `:ro`다 — raw는 원문 `source:target:ro`, resolved는 같은 env로 보간한 source와
  `type: bind`. `exec` 줄의 경로는 `postgres-role-bootstrap.sh`로 끝나는 bind의 컨테이너 경로다. 상수는 지웠고,
  세 번째 `:ro` 항목이 backend 수정 없이 흐르는 테스트와 쓰기 가능 항목을 거부하는 테스트를 더했다.
- **MED — 되돌림이 PinVi 아래에서 Map DB 정체를 두 번째로 바꾼다.** 코드가 아니라 runbook(§4.5 M-R·인수 뒤 0단계
  결합 상태 검사)과 ADR-53 받아들인 위험으로 닫았다.
- **LOW 반영**:
  - 멈추기 전 S1 판정이 admin password를 admin의 **살아있는** SCRAM-SHA-256 verifier와 대조한다(`pg_authid`를
    socket으로 읽어 PBKDF2 → ClientKey → SHA-256 = StoredKey를 프로세스 안에서 `compare_digest`). 평상시 그
    password로 TCP 인증하는 것이 없어 회전·편집 drift가 B9에서야 드러나던 자리다. 단위 테스트의 기대값은 n150의
    버리는 컨테이너(`postgres:16-alpine`, network none, 곧바로 지움)에서 PostgreSQL 16이 만든 verifier이고, gated
    테스트가 fixture 서버의 verifier로 같은 식을 확인한다.
  - Dagster metadata DB가 없으면(`--restart` 제외) init이 거부할 role을 같은 자리에서 먼저 거부한다
    (`require_map_dagster_metadata_initializable`, 읽기 전용 preflight + 회전 단언). 공용 instance의 NOLOGIN
    `kor_travel_transport_dagster_app` 같은 이름이 박힌 `.env`가 Map bootstrap·alembic 뒤에야 거부되던 자리다.
  - R4: Dagster init이 `createdb` 직후 그 DB의 PUBLIC CONNECT를 닫고, R4 transaction의 끝(read-back 통과 뒤)에서
    두 Map DB에 붙어 있지만 이제 CONNECT가 없는 client 세션을 끝낸다(REVOKE는 연결할 때만 검사된다). 앱 DB는
    Map의 fresh 검사가 `datacl IS NULL`을 요구해 먼저 닫을 수 없다 — 남는 창은 ADR-53에 적었다.
  - one-shot 이미지를 digest로 고정했다(`postgres:16-alpine@sha256:721873c3…`, n150 실측: 태그·`.Id`·
    `RepoDigests`가 모두 그 digest). C6c가 `@sha256:`을 요구하고, env 키는 계약표의 그 서비스 행과 리터럴 스위치
    둘만 받는다(`PGOPTIONS`·`PSQL*` 거부).
  - 단일 정본: instance admin 이름은 C6c의 `postgres_server_admin_name` 하나로 읽고(술어도 `POSTGRES_IDENTIFIER`
    하나 — 전에는 C6c가 `_admin`을 받고 재구축이 나중에 거부했다), DSN authority는 `loopback_dsn_authority` 하나,
    S1의 LIKE 패턴은 C6c의 `MAP_PRINCIPAL_PREFIX`에서 만든다. `PostgresAdminSecret.target`(읽는 곳 없음)을 지우고
    `service_secret_sources`를 모듈 안으로 숨겼다. resolved 검증은 Map DSN 모양 판정을 한 번만 한다.
  - 문구: 호스트 프로세스 표의 admin password는 root만이 아니라 **호스트 PID namespace의 모든 프로세스**(권한 없는
    시스템 daemon 포함)가 읽는다 — compose 주석·ADR-53·docker-management.md를 고치고 `/proc` `hidepid=2`를 후속으로
    적었다. M2 설치부터 B6까지는 Map/PinVi 재구축만이 아니라 **모든** Manager compose mutation이 거부된다(ADR-53
    상태 줄·tasks.md). ADR-53에 역방향 폭발 반경(Map backend 하나의 crash가 모든 테넌트를 재기동)과 공용 instance에
    기대는 서비스 20 → 25를 적었고, onboarding §6(C10)·§7.3에도 옮겼다.
  - 테스트: bootstrap one-shot의 no-password 테스트가 공용 instance의 admin secret을 frozen 문서에서 **유도할 수
    있게** 두고(그래야 `-e`로 값을 넘기는 회귀가 실제로 argv에 올릴 수 있다) 모든 `-e` 값에 그 값이 없는지 본다.
    이름에 결박한 `hasattr` 단언 셋은 지웠다(바로 앞의 효과 단언이 남는다). 낡은 이름의 전체 재구축 테스트를
    `test_full_rebuild_never_names_a_postgres_service`로, hba 테스트의 fixture를 합성 이름·포트로 바꿨다.
- **반영하지 않은 것**: resolved 문서의 admin password 값 스캔(`_assert_instance_admin_secret_holders`)은 남긴다 —
  ADR-51 백스톱(`assert_resolved_secret_values_stay_at_reference_sites`)은 `compose_path`가 있을 때만 돌아 완전한
  중복이 아니고, 지우는 쪽이 보안 약화다. 모든 admin secret holder에 digest를 요구하는 것(선택)은 db-init one-shot이
  MT 결정대로 태그를 쓰므로 하지 않았다. `run --rm`·`environment:` secret·`-e`의 실 compose gated 테스트(선택)는 T-SECRET
  spike와 V5가 덮는다.
- **n150 실행**(모두 `/tmp`, 운영 컨테이너·DB는 읽기만 — 이미지 inspect. SCRAM 기대값용 버리는 컨테이너 하나는
  `--network none`으로 띄워 바로 지웠다):
  - 첫 대상 실행(`d9ffdf4`)은 14 failed였다. 셋 다 이 수정의 결함이었다: (1) S1의 첫 질의에 `pg_authid`를
    넣어 superuser가 아닌 admin에게 권한 오류가 나 "must be a superuser" 거부를 가렸다 — verifier는 superuser를
    확인한 **뒤** 따로 읽게 고쳤다(gated T-S1이 잡았다). (2) Bash heredoc이 테스트의 `\\`를 `\`로 접어 LIKE
    패턴 기대값이 틀렸다. (3) `test_docker_service_config.py`의 bootstrap fixture 이미지가 digest 없는 태그라 새
    digest 규칙에 12건이 걸렸다 — fixture에 digest를 붙였다.
  - 고친 뒤 대상 14파일(f1d 계약·database_runtime 단위와 gated 실 PostgreSQL·재구축·hba·docker_service 설정·공용
    계약·grep 게이트·compose 모델·백업·multi-project 둘·registry·후보), gate=1, `2699264` 트리: **950 passed**,
    fixture 잔재 없음. ruff 0.16.4 통과.
  - 빨강 확인(버리는 사본에 변이 일곱을 함께, gate=1): 새 테스트가 모두 빨갛다 — mount가 `compose_binds`를 따른다
    ·쓰기 가능 항목 거부·`exec` 경로(3), 이미지 digest(3), env 키(3), Dagster DB의 PUBLIC 닫기(2), R4 세션 정리
    (단위 1·gated 1), SCRAM 식(단위 2·gated 1 — HMAC 라벨을 바꾸면 PostgreSQL이 만든 verifier와 어긋난다),
    멈추기 전 Dagster role 판정(4). 같은 실행에서 gated 모듈 fixture의 `docker rm --force`가 부하(load 12~14)로
    120초를 넘겨 teardown ERROR 하나가 났고, 컨테이너는 곧 지워졌다(잔재 0 확인).
  - SCRAM 판정을 "항상 맞다"로 바꾸면 drift 검출 넷(단위 4 — 다른 verifier·빈 값·md5·두 줄), PostgreSQL verifier
    단위 1, 재구축의 `password-drift` 1, gated 2가 빨갛다(8 failed).
  - 전체 스위트와 gated 전체 실행은 verify 단계의 몫이다. 그 수치와 CI run ID·`headSha`는 PR 본문에 적는다.

## 2026-09-29 — Map DB 공용 instance 이전 창 실행(ADR-53)

- 운영 스크립트(`/root/map-db-move-20260929/bin/`, 원본 `F:\dev\handoff\window\`, 해시는 README 목록과 일치)로 돌렸다.
  소유자 지시: 다른 테넌트 일정은 고려하지 않는다 → Part A의 weather STARTED run 4개를 `ACCEPT_RUN_IDS`로 넘겼다.
- T0 prechecks 0 FAIL(PREV_SHA `6af5dd1`). #438 MT 머지 `0811a68b` → 10:20:05Z 설치 → **Part A** 10:25Z 공용 재생성
  (CHECKPOINT·`stop --time 300`·`up --no-deps`): 모든 `-c` read-back, init(PID 1 docker-init), StopTimeout 300, shm 1GB,
  exec probe, drift MATCH, autoprewarm leader 1, 크래시 복구 없는 시작. A7 20분 게이트 통과(크래시 0·DSM 0, tenant
  컨테이너·엔드포인트 A0과 같음). 첫 `autoprewarm.blocks` 2.9 MB.
- **Part B** fence 10:48~10:55Z: daemon 먼저, Map run 대기, 나머지 정지, 감사 dump 둘(12700, sha256 검증, `audit/`에 roles.sql·
  126개 테이블 행 수), CHECKPOINT 뒤 12700 `stop --time 120`(clean shutdown, 컨테이너 보존).
- #439 M2 머지 `3b282a7a` → 설치 → `env`(G 아래 `.env`: Map DSN 둘 11000으로, 은퇴 키 삭제; rebind·pin verify 0·S-SYNC·
  drift MATCH; /root 스크립트가 핀된 Map과 일치) → `rebuild` TAG mv1: deploy-status 대조로 adopt 선택, 3분, `deployed`,
  heads map_application 400·map_dagster 29b539ebc72a·pinvi 불변 → `verify` V1~V7 0 FAIL 0 WARN(새 metadata DB run 14
  SUCCESS) → `chain16` V8(ACL 40/40, D1 11, D2 passed) → `backup` V9(manifest `kor-travel-shared-postgres:127.0.0.1:11000/…`,
  sha256 OK). 11:33Z 완료.
- 이전 직후 40-monitor: 0 ALARM 0 WARN — 크래시 표지 0, client 26/97, Map 상한 미도달, DSM 0, checkpoint write 최대 270 s,
  swap-in·OOM 없음, prewarm leader 1, Map run 32 SUCCESS, heartbeat 정상, 모든 백업 로그 정상.

## 2026-09-29 — 공용 Dagster 제어 평면 2단계: `dagster_shared` 스토리지(아무도 쓰지 않는다)

platform-topology.md §7 2단계의 Manager 쪽을 만들었다(브랜치 `feat/dagster-shared-storage`, 계획
`F:\dev\handoff\dagster-shared-plan.md` §5 2단계·§8). webserver·daemon·gateway는 없다(3단계). 운영 중인
서비스의 정의는 하나도 바뀌지 않는다.

- **db-init** `kor-travel-shared-db-init-dagster`: 공용 instance에 role `kor_travel_dagster_shared_app`과
  DB `dagster_shared`. 다른 db-init과 같은 모양(scoped role, owner 확인, PUBLIC CONNECT 회수, 비밀번호는 psql
  변수)에 하나를 더했다 — 비밀번호가 metadata URL에 그대로 들어가므로 빈 값이나 URI 예약 문자는 role을 만들기
  전에 거부한다. 이름은 transport처럼 literal이다.
- **secret** `kor-travel-dagster-shared-app-password`(`KOR_TRAVEL_DAGSTER_SHARED_APP_PASSWORD`). `:?`로 요구되므로
  **설치 전에 n150 `.env`에 먼저 넣는다**(onboarding §6.5 — 없으면 이 compose의 모든 명령이 죽는다).
- **URL 앵커** `x-dagster-shared-control-env`: `KOR_TRAVEL_DAGSTER_SHARED_PG_URL`의 유일한 정의,
  `postgresql+psycopg2://` 명시. 이 저장소의 첫 YAML 앵커다(Manager의 중복 키 거부 로더가 merge를 받는 것도 확인).
- **호스트 이미지** `docker/dagster-host/`: `python:3.12-slim` digest 고정, `requirements.in`의 정확 핀(dagster 가족
  1.13.24, dagster-postgres 0.29.24, SQLAlchemy 2.0.54, psycopg2-binary 2.9.13, pydantic 2.13.5, grpcio 1.84.0)에서
  만든 해시 잠금본만 `--require-hashes --no-deps --only-binary=:all:`로 설치하고 `pip check`한다. 비-root(uid 10001),
  쓰기는 `/opt/dagster/state`뿐. build context는 그 디렉터리 하나다(저장소 전체를 보내지 않는다).
- **storage migrate** `kor-travel-dagster-storage-migrate`: 공용 설정이 `should_autocreate_tables: false`라
  `dagster instance migrate`만으로는 빈 DB에 table이 생기지 않는다. 그래서 같은 `dagster.yaml`을 autocreate만 켠
  채 Dagster 자신의 storage 생성자로 bootstrap한 뒤 원래 설정으로 `dagster instance migrate`를 exec한다.
  `dagster.yaml`이 없거나(Dagster가 SQLite로 조용히 떨어진다) storage가 PostgreSQL이 아니면 거부한다.
- **공용 `config/dagster-shared/dagster.yaml`**: storage는 앵커 env, QueuedRunCoordinator(기본값 — `pools`와 함께면
  `run_coordinator`의 상한은 Dagster가 거부한다) 전역 12(D3), `.dagster/repository` location 상한 넷(각 10)과 오늘의
  Map 4·weather 3 tag 상한, `run_queue` 재시도 3, Map의 run_monitoring·retention, schedule·sensor 스레드 4,
  로컬 쓰기 `/opt/dagster/state`, telemetry off.
- **target** `dagster`(별칭 `dagster-shared`·`dagster-control-plane`): 두 one-shot, runtime 없음, 3단계까지
  `excluded_from_all`. `compose_binds`에 설정 파일 mount.
- **백업 role** `dagster_shared`: `_ROLE_CONFIG`·cron wrapper 허용 목록·UI 신선도 표(24h). crontab 줄은 설치 뒤.
- **공용 instance를 재생성하지 않는다.** n150에서 설치본(`3b282a7`) compose와 이 브랜치 compose를 같은 `.env`로
  `docker compose config --hash '*'`: 기존 39개 서비스의 hash가 전부 같고 새 것은 둘뿐이다.
  `kor-travel-shared-postgres`는 `3aaa6fe2…`로 실행 중 컨테이너의 `config-hash` 라벨과 같다.
- **테스트**: `test_dagster_shared_config.py`(선언끼리 — 앵커 URL·instance 설정의 env·db-init literal·백업
  database·compose code-server의 `-m`과 location 상한, 해시 잠금), `test_dagster_shared_storage_integration.py`
  (gated — 정본 build context에서 격리 태그로 이미지를 만들고, 고정된 공용 PostgreSQL 이미지 위에서 db-init·migrate를
  두 번 돌려 owner·CONNECT·alembic head·table 소유·Dagster가 읽은 설정·URL 비밀번호 거부를 본다. 이미지와
  compose 잔재는 지운다).
  - n150 대상 10파일 gate=1(`a73a069`의 새 clone, consolidation venv): **599 passed**, 잔재 0.
  - 빨강 확인(버리는 clone에 변이 하나씩, gate=1) 13/13 빨강: URL 스킴 `postgresql://`, autocreate 켬, geo 상한 삭제,
    백업 DB 오타, URL DB 오타, db-init REVOKE 삭제, `--require-hashes` 삭제, 하한 핀, runtime에 one-shot, wrapper
    허용 목록(각 단위 1~2 failed), 그리고 gated 셋 — migrate를 맨 `dagster instance migrate`로(bootstrap 없이),
    db-init REVOKE 삭제, 비밀번호 거부 삭제(각 1 failed).
  - 전체: gated 전체 스위트(`KTDM_REQUIRE_DOCKER_INTEGRATION=1`, `de57f2a`의 새 clone, consolidation venv)
    **2371 passed, 2 skipped**, 잔재 0. `/tmp/b3-test.sh`는 같은 head에서 ruff 깨끗, **1 failed**(이 브랜치의 통합
    테스트 — 격리 PostgreSQL의 initdb 임시 서버가 `pg_ctl` 기본 60초 안에 종료 checkpoint를 못 끝내 exit 1, load 12)였다.
    fixture에 `PGCTLTIMEOUT=300`을 준 `6b420e9`에서 b3 **2371 passed, 2 skipped**, gated 세 파일 12 passed.
- 문서: platform-topology §7(포트 D2 — gateway `11001`·webserver `127.0.0.1:11002`·daemon 포트 없음, 이력 새로
  시작 D1, transport는 나중에 합류, D3~D6), ports.md(`11001`/`11002` 예약, `dagster_shared`), onboarding·
  docker-management의 백업 표, tasks.md.
- **남은 것**: 적대 리뷰, PR·머지, `.env` 선행 추가 → 설치 → 창에서 `ktdctl ensure dagster` → 검증 넷(alembic
  head·owner·PUBLIC CONNECT·백업 산출물) → crontab. 3단계의 ADR은 그때 쓴다(계획 3.5).

## 2026-09-30 — 공용 Dagster 2단계: 적대 리뷰 수정(상한이 실제로 물게, one-shot이 ensure를 가르게, 설치 preflight)

브랜치 `feat/dagster-shared-storage`. 운영 서비스 정의는 여전히 하나도 바뀌지 않는다.

- **H1 — location 상한이 아무것도 막지 않았다.** `.dagster/repository`는 `DagsterRun.tags_for_storage()`가
  run_tags 표에만 쓰고, queue daemon은 `get_run_records()`의 `record.dagster_run.tags`(run_body)를 센다.
  Dagster 1.13.24 소스 확인: `RunDomain.create_run`이 `remote_job_origin`이 있으면 `dagster/code_location`을
  run 본문 tags에 넣고(사용자 tag를 덮어쓴다), queue로 오는 경로는 전부 그것을 준다 — scheduler
  `create_run`, sensor, GraphQL `create_valid_pipeline_run`(launchpad·asset Materialize `__ASSET_JOB`),
  `submit_asset_runs`(asset backfill), `job_backfill`, `create_reexecuted_run`(UI·자동 재실행). n150
  GraphQL `Run.tags` 실측도 `dagster/code_location=kortravelmap.dagster.definitions`(geo도 같다). 상한 key를
  `dagster/code_location`, 값은 location 이름(= code-server `-m` 모듈)으로 바꿨다. 파싱 대조(항진명제)를 효과
  테스트로 갈음: 격리 PostgreSQL instance에서 Dagster 자신의 `create_run`으로 한 location에 STARTED 10개 +
  QUEUED 1, 다른 location에 QUEUED 1을 만들고 daemon의 `_get_runs_to_dequeue`를 돌린다 — 11번째는 남고
  다른 location은 나간다. 대조군(옛 key)은 막지 않는다. 3단계 합류 조건(workspace `location_name` = 모듈)과
  전환 판정 SQL은 platform-topology §7.
- **M1** 설치기 preflight: `.env` 복사 뒤·host 쓰기와 flip 전에 `docker compose --env-file ${REL}/.env config
  --quiet`, 실패면 exit 126, 오류에서는 compose가 참조하는 변수 이름만 싣는다. n150 실측(읽기 전용, sudo):
  설치본 compose + live `.env` rc=0, 이 브랜치 compose + live `.env` rc=1 `KOR_TRAVEL_DAGSTER_SHARED_APP_PASSWORD`.
  onboarding §6.5 정정 — Compose v5.2.0에서 `secrets: environment:`만으로는 빈 변수가 `config`·`ps`를
  죽이지 않고(실측), 전역으로 만드는 것은 `${…:?}`다.
- **M2** `dagster_shared` cron 줄은 설치본 wrapper `/opt/kor-travel-docker-manager/scripts/run-standalone-backup.sh`.
  옛 사본(`/home/digitie/kor-travel-docker-manager`)의 허용 목록에는 이 role이 없다(n150 확인). 설치본
  `ktdctl db-backup list`가 cron 계정으로 도는 것을 실측했다. 2단계 검증은 그 경로를 cron 계정으로.
- **M3** target `dagster`: `services`는 공용 instance 하나, db-init과 storage migrate는 init step
  (`run --rm --no-deps`)으로 차례대로 — 각 종료 코드가 ensure를 가른다.
- **M4** readiness `postgres_public_connect`(warn): PUBLIC이 CONNECT를 가진 non-template DB(NULL `datacl`의
  기본 ACL 포함). n150 공용 instance에서는 `pinvi`(owner `pinvi_application_runtime`) 하나 — 걷는 것은 PinVi의
  변경이다(onboarding §5.3). 격리 fixture에 기본 ACL DB를 하나 더 두고 새 role이 붙고 TEMP를 만들지만 table은
  못 읽고 `public`에 못 만든다, 걷힌 bootstrap DB에는 못 붙는다를 고정했다.
- **M5** 호스트 이미지 tag = Dockerfile·requirements.txt·storage-migrate.py의 내용 해시(`75f02d642f06bcbd`),
  env override 없음. 테스트가 Dockerfile의 COPY 원천까지 해시 집합으로 결박한다.
- LOW: role에 NOREPLICATION·NOBYPASSRLS·CONNECTION LIMIT 30(CREATE·ALTER 둘 다), role 소속 0이 아니면 db-init이
  멈춘다, 두 one-shot을 계약 오류가 이름으로 부른다, migrate docstring 정정(`instance migrate`는 alembic만이 아니다,
  생성자는 주 table이 없을 때만 만든다), dagster.yaml에 retention은 tick뿐이라는 주석.
- **재생성 없음**(n150, 설치본 `3b282a7` vs 이 브랜치, 같은 live `.env`, `config --hash '*'`): 기존 39개 전부
  같고 새 것은 둘(`kor-travel-shared-db-init-dagster`·`kor-travel-dagster-storage-migrate`),
  `kor-travel-shared-postgres` `3aaa6fe2…` = 실행 중 컨테이너의 `config-hash` 라벨.
- **테스트**(n150, `02099d0`): 빨강 확인 14/14 빨강(버리는 사본에 변이 하나씩) — 단위 11(상한 key 되돌림, one-shot을
  `up`으로, migrate step 삭제, preflight를 `config`가 아닌 명령으로, preflight 원문 에코, 기본 ACL 빠진 SQL,
  warn→missing, 이미지 tag `latest-main`, ALTER 속성 삭제, nameable 삭제, 소속 검사 삭제)과 gated 3(옛 key로
  11번째 run이 나간다, 기본 ACL 빠진 SQL이 `acl_probe`를 놓친다, CREATE 속성 삭제). `/tmp/b3-test.sh`: ruff 깨끗,
  **2389 passed, 2 skipped**. gated 전체(`KTDM_REQUIRE_DOCKER_INTEGRATION=1`, 새 clone): **2389 passed, 2 skipped**,
  잔재 0.

## 2026-09-30 — 공용 Dagster 2단계: 재리뷰 LOW 셋(cold start 대기, COPY/ADD 추출, 3단계 게이트)

적대 재리뷰가 브랜치를 통과시키며 남긴 LOW 셋을 닫았다.

- **cold start**: `ensure dagster`는 db-init을 `run --rm --no-deps`로 돌려 compose가 `depends_on`
  (service_healthy)을 보지 않고, 앞선 `up -d`도 healthy를 기다리지 않는다. db-init 스크립트 맨 앞에서
  `pg_isready`를 2초 간격 60번(약 120초) 기다리고, 끝내 안 뜨면 메시지와 함께 exit 1. `up -d --wait`는
  target의 `services`(up) 경로를 바꿔야 해서 고르지 않았다 — 스크립트 안의 대기가 더 좁다. 테스트는 실제
  스크립트를 stub `pg_isready`·`sleep`·`cat`·`psql`로 돌린다(포기 시 psql·secret 읽기 0회, 세 번째 probe에서
  뜨면 다음 명령으로 진행).
- **COPY 원천 추출**: `ADD`·소문자·`\` 줄 이음을 읽고, 못 읽는 형식(JSON 배열, heredoc, `--from`, escape
  지시자)은 건너뛰지 않고 멈춘다.
- **3단계 게이트**(platform-topology §7): G3-a 연결 상한 30을 전역 run 12가 찬 상태에서 실측(NullPool),
  G3-b 공용 `workspace.yaml`의 `location_name` ↔ `dagster/code_location` 상한 결박 테스트를 그 파일과 함께.
- **테스트**(n150, `fc555a8`): 빨강 확인 — db-init을 부모 커밋의 스크립트로 되돌리면 대기 테스트 둘 빨강,
  옛 추출기로 되돌리면 추출 테스트 빨강. `/tmp/b3-test.sh`: ruff 0.16.4 깨끗, **2392 passed, 2 skipped**.

## 2026-09-30 — 공용 Dagster 3단계: 공용 daemon·webserver·gateway, target별 합류 스위치(ADR-54)

브랜치 `feat/dagster-shared-stage3`(origin/main `08824b0` 위). 모든 target이 `own`이라 plane은 빈 workspace로 돌고
어느 프로젝트의 서비스 정의도 바뀌지 않는다.

- **호스트 서비스** `kor-travel-dagster-daemon`·`-webserver`: 호스트 이미지(migrate와 같은 tag·context), 비-root,
  `init: true`, migrate `service_completed_successfully`, env는 공용 URL 앵커·`DAGSTER_HOME`(+daemon tolerance
  300)뿐. daemon은 storage 가드 뒤 `dagster-daemon run`, exec `liveness-check`. webserver는 `127.0.0.1:11002`,
  probe는 붙은 workspace의 location **전부**가 `RepositoryLocation`인지(dagster import·gRPC 없음, 자체 timeout 8초).
- **gateway** `11001`: 공식 nginx digest 고정, uid 101, Manager 소유 전체 설정(`config/dagster-shared/gateway.conf`),
  Basic Auth(secret 파일, `{PLAIN}`), Origin 없는·남의 POST 403, `/health` 204, frame-ancestors(기본 geo UI).
  fail-closed 기동 — n150 Compose v5.2.0은 값이 빈 env secret의 파일을 만들지 않는다(격리 실측), 그것도 빈
  비밀번호로 거부한다. Dagster webserver의 자체 CSP는 두고 frame-ancestors를 두 번째 정책으로 더한다(실측).
- **스위치와 파생**: target별 `dagster: {control_plane, consumers}`(넷 다 `own`, registry가 형태 검증),
  `config/dagster-shared/workspace.yaml`은 `shared` target의 code-server command에서 파생(지금 `load_from: []`).
  렌더러는 두지 않았다 — 단일 compose 경계(ADR-20)와 주석 보존 때문에 전환 PR이 손으로 바꾸고,
  `test_dagster_shared_workspace_is_derived.py`가 (a)·(b)·(c)·`all` 포함·workspace drift·G3-b를 파생 기대와 대조한다.
  참조 구현 `_flip`으로 네 target 각각·전부를 뒤집어 초록, 한 단계씩 뺀 9개 대조군이 이름으로 빨갛다.
- **재생성 없음**(n150 읽기 전용, sudo, live `.env`, `config --hash '*'`): 설치본 `08824b0` vs 이 브랜치 — 기존 41개
  전부 같고 새 것은 셋(daemon·webserver·gateway). live `.env`에 gateway 비밀번호가 없어도 `config`는 rc=0.
  실행 중 컨테이너 31개 중 20개가 설치본 정의와 hash가 다른 것은 이 브랜치와 무관한 기존 상태다(설치본·브랜치
  어느 쪽과 대조해도 같은 20개).
- **테스트**(n150): 대상 6파일 312 passed; 격리 plane 통합 5 passed(빈 plane의 셋 healthy, heartbeat 있음·
  instigator 0, 비-root uid, 없는 location을 기대하면 probe 빨강, gateway 401·403·204·200·CSP, 기동 거부 넷);
  `/tmp/b3-test.sh`(`9811d5e`) ruff 0.16.4 깨끗, **2452 passed, 2 skipped**. 빨강 확인(버리는 사본에 변이 하나씩)
  단위 16/16 빨강 — workspace drift, G3-b 상한 없는 location, own 서비스의 legacy profile, own code-server의 공용
  URL, 호스트 서비스의 앱 비밀, webserver `0.0.0.0`, daemon `init` 삭제, daemon 가드 무력화, probe 판정 뒤집기,
  probe 경로, probe 타입, `control_plane` 오타, runtime에서 daemon 삭제, ensure에서 gateway 삭제, 남의 서비스에
  소비자 선언, code-server target의 스위치 삭제.
  gated 1 빨강 — gateway의 `"POST:0" 1`을 0으로 + 빈 비밀번호 검사 무력화: 통합 본 테스트와 빈 비밀번호 거부가 빨갛고 나머지 거부 셋은 초록.
- **D6 개정(2026-09-30 소유자)**: 전환 사이 soak 없음, 넷 뒤 약 24시간 함께 관측(C7 GREEN 포함) 뒤 4단계,
  옛 메타DB `DROP` 전 30일은 그대로 — §7 runbook·D6·ADR-54에 반영.
- **남은 것**: 적대 리뷰·PR, 버전 상한 검사(계획 0.6), G3-a 실측, 에지(3.6), 소비자 PR 3.1~3.4, Map 전환의 host
  allowlist·C7 인증·C6c 보호 서비스 집합, 전환 창.

## 2026-09-30 — 공용 Dagster 3단계: 적대 리뷰 수정(재생성 digest, `/graphql` CSRF, pinned 게이트, 버전 상한)

브랜치 `feat/dagster-shared-stage3`(origin/main `9f0f1cd` 위). 모든 target은 여전히 `own`이다.

- **H1** 공용 설정을 붙인 상시 서비스가 그 파일 sha256 앞 16자를 env로 싣는다(daemon·webserver: workspace·instance,
  gateway: gateway.conf, 합류한 code-server: instance). bind가 설치본 symlink를 거쳐 compose hash가 경로만 보므로,
  내용만 바뀐 workspace로는 재생성되지 않던 구멍이다. 파생 테스트가 내용과 대조하고 참조 전환이 digest를 갱신한다.
- **H2·M1** gateway: POST가 아닌 `/graphql`은 인자가 있으면 403(처음에는 `query` 인자만 막았다 — 재리뷰 MED-1이
  인코딩된 이름·중복 이름 우회를 찾아 인자 전부로 넓혔고, 그 판이 POST까지 막아 UI의 `POST /graphql?op=<Operation>`을
  깨뜨려 재리뷰 HIGH-1로 POST를 뺐다 — POST는 Origin·`Sec-Fetch-Site`가 가른다), `Sec-Fetch-Site`가 same-origin·none·없음이 아니면 403, 허용
  Origin이 아닌 `Upgrade` 403. UI HTML(`/`)의 교차 사이트 탐색(iframe)은 그대로 200.
- **M2** 옛 webserver·daemon 이름을 literal로 든 코드가 있는 target은 계약 테스트가 `(pinned)`로 `shared`를 막는다 —
  재리뷰 MED-2 뒤 목록 셋이 아니라 `backend/src`·`scripts` 전체를 토큰으로 찾는다(Map·PinVi·geo가 걸리고 weather만 열림). 파생은 별도 PR이다. `test_flipping_a_target_renders_a_consistent_plane`의 과장된 docstring 정정.
- **M3** `127.0.0.1:11002` 무인증은 ADR-54에 받아들인 위험으로, runbook 4번이 소비자가 **할 수 있는 것**을 본다(weather
  PR #65 `scopedDagsterRequest` 확인).
- **M4** daemon probe: workspace code-server 전부 gRPC `SERVING` → 같은 프로세스 exec `liveness-check`.
- **M5** webserver probe가 `dagsterLibraryVersions` ≤ 호스트 설치 버전(`importlib.metadata`)을 본다, 모르면 빨강.
- LOW: `shared` code-server의 `-p` literal, 옛 공개 host env 참조 금지, webserver도 storage 가드(YAML anchor 하나,
  argv handoff), liveness 테스트의 조기 return 구멍, 합류 조건(/opt/dagster/state 쓰기·pool 접두) 문서·검증 단계.
  `limit_req`는 HAProxy 뒤 단일 주소라 UI 전체를 조이므로 두지 않았다(ADR-54).

## 2026-09-30 — 공용 Dagster 전환의 `(pinned)` 해제: 옛 webserver·daemon 이름을 렌더된 모델과 스위치에서 파생

브랜치 `feat/dagster-shared-derived-legacy`(3단계 head `6f384ba` 위). 모든 target은 여전히 `own`이다 — 전환 PR이 아니다.

- **왜**: 계약 테스트가 Map·PinVi·geo를 `(pinned)`로 막고 있었다. pinned 재구축(slot·build 목록, `compose_up`),
  C6c(필수·런타임·secret isolation 집합), 이미지 보존, M05, 옛 override 이관이 옛 webserver·daemon 이름을 literal로
  들었다. 전환하면 그 서비스는 `legacy-dagster`로 내려가 frozen render(`--profile bootstrap`만)에서 사라지는데 literal은
  그것을 계속 요구·검사·기동한다.
- **파생**: `services/runtime_topology.py`. family(code-server·webserver·daemon·gateway)는 모양으로 찾는다(계약 테스트와
  같은 규칙, 독립 파생과 대조하는 테스트가 있다). 원본은 이 코드와 함께 설치된 release의 reference compose와
  `docker-targets.yml`이다(env로 옮기지 않는다, import 시점에 읽지 않는다). 스위치가 carrier를 고른다 — `own`이면 webserver,
  `shared`면 code-server, daemon slot은 `shared`면 없다.
  - pinned generation의 이미지 일곱은 **slot**(`map_api`…, payload 필드 이름)으로 키하고, slot의 서비스·build 서비스·
    candidate tag·companion·stop/up/verify·배포 기록 images 키를 topology에서 얻는다. 옛 서비스를 실은 render는 거부.
  - C6c: 실행·필수 집합은 스위치를 따르고, env 계약(DSN·Geo key·UI 잠금)은 family 전부에 남는다(profile로 내려간 옛
    서비스도 켜면 그 값으로 돈다). admin secret 전역 규칙의 runtime 집합도 파생.
  - 보존: 이름은 carrier를 따르고, 전환된 target의 옛 tag는 stale로 알아보고 지운다(모르는 reference로 멈추지 않게).
  - M05 격리 이미지 역할 키 `pinvi-app-dagster`(PinVi 저장소의 `app-dagster` 이미지 — Manager 서비스가 아니다), receipt 불변.
  - 옛 override 이관의 Geo 서비스는 Geo family의 webserver·daemon(과거 파일의 모양이라 스위치와 무관).
  - 계약: 모든 target에서 `(pinned)`가 비어야 하고, 옛 이름을 `backend/src`·`scripts`에 다시 넣으면 빨갛다.
- **실측(n150 Compose v5.2.0)**: `config --no-interpolate`와 `config --services`는 profile을 거르지 않는다 — 꺼진
  profile의 서비스도 적는다. 보간한 `config`(frozen render)는 거른다. 활성 서비스가 꺼진 profile 서비스에 `depends_on`하면
  보간한 `config`는 "depends on undefined service"로 실패한다(fail-closed).
- **불변(모두 `own`)**: 지문 32항목이 `6f384ba`와 같다 — slot 서비스, build 서비스, candidate·paired tag, companion,
  배포 images, 보존 reference·패턴, C6c 필수·known·Map runtime·PinVi DSN·Map DSN·API env·UI 잠금 표, 오류 이름 가림,
  mutation 분류, generation payload·logical sha256, 두 compose env, **첫 배포·수렴의 compose 호출 순서 전체**. 다른 것은
  M05 역할 키 하나(의도). 운영 pinset `7ea6689c`는 두 코드로 재계산해도 같다. compose는 바뀌지 않았다 — 설치본
  `9f0f1cd` 대비 기존 41개 hash 전부 같고 새 것은 3단계의 셋뿐.
- **테스트(n150)**: `/tmp/b3-test.sh`(`9e572ff`) ruff 0.16.4 깨끗, **2500 passed, 2 skipped**. 대상 13파일 957 passed.
  Map·PinVi 전환을 실제 재구축 경로(`_forward_harness`)로 돌려 옛 webserver·daemon이 어떤 compose 호출·readiness·
  이미지 검사·배포 기록에도 없고 code-server가 `up`된다. 넷 다 전환한 compose의 실제 frozen render(보간한 `config`)에
  옛 서비스가 없다. 빨강 확인(버리는 사본에 변이 하나씩) 16/16 — carrier·daemon slot이 스위치를 무시, `own`의 carrier
  이동, gateway 파생, process 순서, companion 거부 제거, C6c 필수·secret isolation·PinVi build 배선이 family 전부/webserver,
  보존의 옛 이름 인식·서비스 없는 slot, API 분류의 옛 서비스, frozen profile에 `legacy-dagster`, 옛 override의 스위치
  추종, 계약의 코드 스캔 제거, literal 재도입.
- **남은 것**: 전환된 target의 첫 재구축은 Map이면 paired tag 이름이 바뀌어(`…/<code-server>:<pinset>`) Map 이미지를
  다시 빌드하고 전체 경로를 돈다(images 키가 바뀌므로 수렴이 아니다) — 의도. `shared`→`own` 되돌리기 뒤에는 code-server
  이름의 보존 tag를 `own` namespace가 모른다(fail-closed, 손으로 지운다). 계약 (c)는 profile 없는 서비스의 `depends_on`만
  보는데, `bootstrap` profile 서비스가 옛 서비스에 기대면 frozen render가 "undefined service"로 실패한다(지금은 없다).

### 2026-09-30 — 적대 리뷰(`0eb26a4`) 수정, origin/main `6f30fd5`(#445) 위로 재기반

- **MED-1** 파생은 해석이 끝난 뒤 그것이 필요한 mutation에서만 한다(`_parse_compose_mutation`) — read-only(`ps`·
  `config`·`logs`)와 명시 서비스 `stop`, `--no-deps` `up`은 설치된 모델을 읽지 않는다. family는 target별로 파생·캐시하고
  pinned runtime·C6c는 Map·PinVi만 부른다 — geo·weather 모양이 어긋나도 막히지 않는다(전체 파생만 거부). 공용 plane
  (공용 workspace를 붙인 서비스)은 target의 runner·gateway로 세지 않는다.
- **MED-2** 전환된 target의 옛 webserver·daemon·gateway 컨테이너(설치된 `containers`에서 이름 파생)가 docker에서 돌고
  있으면 수렴·전체 경로 모두 무엇을 멈추거나 migration하기 전에 거부한다(없으면 통과, 못 읽으면 거부). `own`이면 볼 것이 없다.
- **MED-3** 보존·candidate namespace는 Map·PinVi family의 이름 전부를 스위치와 무관하게 알아본다 — `shared`→`own` 되돌리기
  뒤 code-server 이름의 tag도 stale로 지운다. 그 밖의 이름은 여전히 namespace를 멈춘다. `own`의 보존 대상(desired)은 그대로다.
- **LOW-4** gateway에서 그 target의 runner를 뺀다(Map daemon이 Map gateway로 세이던 것). **LOW-7** 전환 뒤에도 raw 후보에
  profile로 남은 옛 Map webserver·daemon은 서비스별 계약(Geo key·DSN·host network)을 그대로 본다(필수는 아니다).
- **불변식을 저장소로**: `test_every_own_rebuild_matches_the_pre_adr54_fingerprint`가 `6f384ba`에서 뜬 지문
  (`backend/tests/fixtures/pinned_runtime_own_fingerprint.json`, seed revision·pinset은 자리표)과 대조한다 — 의도한 변경 셋(M05
  역할 키, 보존이 알아보는 이름, seed에 묶인 sha256)만 빠져 있다. 운영 pinset `7ea6689c`는 고정 벡터 테스트가 재계산한다.
- **빨강 확인** 27/27(이전 16 + 이번 11). pinset 변이는 테스트 모듈 수집 단계에서 먼저 빨갛다(seed pin 대조).

## 2026-09-30 — 공용 Dagster: weather 전환 PR(첫 합류, ADR-54)

브랜치 `feat/dagster-shared-flip-weather`(origin/main `6f30fd5` 위 — 3단계 설치본). 참조 전환 `_flip`을 파일에 그대로 옮겼다.

- weather `dagster.control_plane: shared`. code-server: `<<: *dagster-shared-control-env`, instance digest, 옛
  `deploy/dagster.yaml` 대신 공용 `dagster.yaml`(compose_binds도), gRPC는 이미 `127.0.0.1:14106`(literal).
- weather-web: `DAGSTER_UI_INTERNAL_URL` → `http://127.0.0.1:${KOR_TRAVEL_DAGSTER_WEBSERVER_PORT:-11002}`,
  `NEXT_PUBLIC_DAGSTER_URL` → `${KTDM_PROD_URL_DAGSTER:-…11001}`, gateway `depends_on` 삭제.
- 옛 weather webserver·daemon·gateway → `profiles: [legacy-dagster]`, weather의 `services`·`runtime_services`에서 뺌
  (`containers`에는 남음). plane target `dagster`가 `all`에 든다.
- 공용 workspace에 `kortravelweather_dagster.definitions`(127.0.0.1:14106), daemon·webserver의 workspace digest 갱신.
- 합류 조건(n150 읽기 전용 실측): code-server는 root라 `/opt/dagster/state`를 첫 쓰기 때 만든다(`/opt/dagster`
  쓰기 가능, 아직 없음); op pool을 쓰지 않는다; dagster 1.13.24 = 호스트; schedule 17개 전부 코드에서 RUNNING,
  sensor 0; 공용 `dagster.yaml`에 weather `dagster/code_location` 10과 `kortravelweather/run_group=external_weather`
  3이 있다.
- 테스트: weather를 `own` 예로 쓰던 대조군을 pinvi·map으로 옮기고, weather 전환의 한 부분을 되돌리면 이름으로
  빨간 committed-state 대조군을 더했다. 격리 plane 통합 테스트는 빈 workspace(와 그 digest)로 돈다.

## 2026-09-30 — 공용 Dagster: PinVi 전환 PR(둘째 합류, ADR-54)

브랜치 `feat/dagster-shared-flip-pinvi`(origin/main `c6ea8f8` 위 — weather 전환 설치본). 참조 전환 `_flip`을 파일에 옮겼다.

- PinVi `dagster.control_plane: shared`. code-server: `<<: *dagster-shared-control-env`, instance digest, 이미지에 구운
  `apps/etl/dagster.yaml` 대신 공용 `dagster.yaml`을 `/opt/pinvi/.dagster/dagster.yaml`에(compose_binds도), gRPC 포트는
  literal `12803`(공용 workspace가 `.env` override를 모른다). healthcheck의 gRPC 포트도 같은 literal이다 — 계약이
  `shared` code-server의 healthcheck가 `-p`와 다른 포트(또는 `${VAR:-N}`)를 부르면 빨갛다(적대 리뷰 LOW).
- pinvi-api: `PINVI_DAGSTER_BASE_URL` → `http://127.0.0.1:${KOR_TRAVEL_DAGSTER_WEBSERVER_PORT:-11002}`. admin_etl은
  `pinvi_dagster_location_name`(= `pinvi.etl.definitions`)으로 location을 좁혀 부른다 — 공용 plane의 다른 테넌트가 섞이지 않는다.
- 옛 `pinvi-dagster`·`pinvi-dagster-daemon` → `profiles: [legacy-dagster]`, PinVi의 `services`·`runtime_services`에서 뺌.
  #447 파생으로 pinned slot `pinvi_dagster`는 code-server가 된다.
- 공용 workspace에 `pinvi.etl.definitions`(127.0.0.1:12803), daemon·webserver의 workspace digest `111114624da6f2ac`.
- 합류 조건(n150 읽기 전용 실측): code-server는 root, `/opt/dagster`는 없지만 `/opt` 쓰기 가능이라 state는 첫 쓰기 때
  생긴다; op pool 없음; dagster 1.13.24 = 호스트; 코드에서 RUNNING schedule 7·sensor 1(옛 DB는 4개가 수동 RUNNING,
  나머지는 DECLARED_IN_CODE); 공용 `dagster.yaml`에 PinVi `dagster/code_location` 10. 주의: 일부 job에 `max_runtime`
  tag가 없어 공용 instance 기본 21600초를 받는다(나머지는 3600).
- 적용은 `ensure pinvi`가 아니라 pinned 재구축이다(runbook §7 3번에 추가) — 전체 경로라 Map도 잠깐 멈춘다.
- 테스트: pinned 재구축·C6c·보존·파생 legacy 테스트는 "Map·PinVi `own`" 기준선(conftest `own_pinned_pair`,
  `_own_pair_documents`) 위에서 `_flip`을 얹는다. `own` 대조군은 geo로 옮겼고, f1d의 PinVi runtime 목록은 slot
  carrier에서 파생한다. 계약 env에 공용 URL 앵커의 필수 키를 더했다.

## 2026-10-01 — 공용 Dagster: geo 전환 PR(셋째 합류, ADR-54)

브랜치 `feat/dagster-shared-flip-geo`(origin/main `cc5fa06` 위 — PinVi 전환 설치본). 참조 전환 `_flip`을 파일에 옮겼다.

- geo `dagster.control_plane: shared`. code-server: `<<: *dagster-shared-control-env`, instance digest, 이미지에 구운
  `dagster_home/dagster.yaml` 대신 공용 `dagster.yaml`(compose_binds는 기존 백업 디렉터리 항목 옆에), gRPC는
  `-h 0.0.0.0` → **`127.0.0.1`**(plan §4 — 인증 없는 gRPC가 LAN에 열려 있었다. f1d의 알려진 예외 목록이 비었다),
  포트는 literal `12503`(healthcheck도).
- geo-api: `KTG_DAGSTER_URL` → `http://127.0.0.1:${KOR_TRAVEL_DAGSTER_WEBSERVER_PORT:-11002}`(geo의 SSRF allowlist가
  host만 보고 127.0.0.1을 허용한다), `KTG_DAGSTER_PUBLIC_URL` → `${KTDM_PROD_URL_DAGSTER:-…11001}`. 옛
  `KOR_TRAVEL_GEO_DAGSTER_PUBLIC_URL`(`geo-dagster` host)은 더 읽지 않는다. geo의 summary·run 조회·launch는
  `dagster_repository_location_name`으로 좁혀 부르고 run 상세는 `repositoryOrigin`으로 소유를 본다(geo #569).
- 옛 `kor-travel-geo-dagster`·`-daemon` → `profiles: [legacy-dagster]`, geo의 `services`·`runtime_services`에서 뺌.
- 공용 workspace에 `kortravelgeo_dagster.definitions`(127.0.0.1:12503), daemon·webserver의 workspace digest 갱신.
- 합류 조건(n150 실측): code-server는 `appuser`(uid 999)이고 `/opt/dagster`를 소유한다 — `/opt/dagster/state`를
  첫 쓰기 때 만든다; op pool 없음(`pool`·`dagster/concurrency_key` 0); dagster 1.13.24 = 호스트; 코드 선언은
  schedule 3개 중 RUNNING 2(`backup_retention_janitor_daily`·`scheduled_backup`, `backup_restore_drill_daily`는
  STOPPED)와 `run_failure_sensor` RUNNING — 옛 instance의 RUNNING 집합(3)과 같다; 공용 `dagster.yaml`에 geo
  `dagster/code_location` 10. `max_runtime` 86400이 4 job(full_load_batch·load_source·db_restore·restore_drill),
  나머지는 공용 기본 21600.
- geo UI iframe: `/admin/dagster`의 `DagsterEmbed`는 **요청마다** UI 컨테이너의 `KTG_DAGSTER_PUBLIC_URL`(→`.env`→
  `NEXT_PUBLIC_DAGSTER_URL`)을 읽는다 — 빌드에 굽지 않는다. 지금 geo-ui 컨테이너에는 그 값이 없어 iframe은 안내만
  보인다(전환 전과 같다). 켜려면 geo-ui에 `KTG_DAGSTER_PUBLIC_URL`을 공용 공개 host로 준다 — gateway의
  `frame-ancestors` 기본값은 `KTDM_PROD_URL_GEO`(= geo UI origin)라 그대로 맞는다. 다만 iframe 안의 Basic Auth
  prompt와 공용 UI가 모든 테넌트를 보인다는 점은 소유자 결정이다.
- 테스트: `own` 대조군은 이제 마지막 `own`인 Map으로 든다(Map은 이미 loopback이라 loopback 대조군은 `-h 0.0.0.0`을
  먼저 입힌다). 설치 모델의 스위치 단언은 map·pinvi `own`(기준선 fixture), geo·weather `shared`.

## 2026-10-01 — 공용 Dagster: Map 전환 준비(Manager 쪽, ADR-54 개정)

브랜치 `feat/dagster-shared-map-prep-mgr`(처음 `cc5fa06` 위에서 geo 전환과 독립으로 만들고, geo 머지(#449 `9211d63`) 뒤 그 위로 rebase). Map은 뒤집지 않는다.

- **plane을 아는 pinned 재구축.** `RuntimeTopology.shared_dagster_slots`(Map·PinVi 중 `shared` carrier slot)가 있으면
  전체 경로가 PinVi smoke 전에 그 carrier를 띄우고 plane이 그 location을 싣게 한다. 같은 pair 수렴도 같은 단계를
  거친다. plane 서비스는 `runtime_topology.derive_shared_dagster_plane`이 frozen render에서 모양(공용 workspace를 붙인
  활성 `dagster-webserver`·`dagster-daemon`)으로 파생하고 설치본과 다르면 거부한다. 모두 `own`이면 plane을 건드리지
  않는다(파생 이전 지문 테스트 그대로 초록).
- **적대 리뷰(5ece4aa) 반영.** H1: `--wait`(plane healthcheck가 모든 테넌트를 봐서 geo·weather 하나가 Map 배포를
  막았다) 대신 `up -d --no-deps` 뒤 webserver `workspaceOrError`에서 합류한 pinned location만 `RepositoryLocation`인지,
  daemon이 도는지 300초 안에서 본다. M1: plane이 실을 workspace의 location마다 그 target(모든 Dagster target, 설치
  모델에서 파생)의 plane 밖 webserver·daemon·gateway가 돌면 거부 — 한 규칙. M2: `own`인 Map·PinVi의 location을
  설치본 workspace가 적었거나 떠 있는 webserver가 싣고 있으면 무엇을 멈추기 전에 거부(물을 수 없는데 plane daemon이
  돌면 거부). M3: 떠 있는 plane이 frozen render가 만들 컨테이너와 같으면(이미지 ID·render env 전부·command·entrypoint, 재리뷰
  MED로 digest만 보던 것을 넓힘) `up`하지 않는다(frozen·평범한
  render의 config hash가 달라 무조건 `up`은 매번 plane을 다시 만들었다). L2: 재시도 상한은 대기의 시작을 묶는다(최악
  약 190초). L4: plane 파생에 캐시가 없다. 창 스크립트를 `scripts/dagster-shared-cutover.sh`로 저장소에 넣었다(weather
  전용 첫 판(weather-cutover.sh)은 같은 경로의 첫 커밋으로 남긴다 — 옛 서비스 이름이 literal이라 트리에 두면 `(pinned)` 게이트가 막는다).
- **smoke 재시도.** PinVi canonical smoke의 본문 없는 GET 둘(`/admin/etl/summary`·`/admin/provider-sync`)만 최대 6회,
  5→10→20→30→30초, 첫 시도부터 180초 상한(넘길 대기는 하지 않는다). 연결 거부·timeout과 PinVi의 upstream 미준비
  envelope(502/503 `FEATURE_SERVICE_UNAVAILABLE`)만 재시도하고, 다 쓰면 마지막 결과로 fail-closed. cancel POST·login·
  logout은 재시도하지 않는다. Map 쪽 canonical smoke는 재구축 경로에 따로 없다 — PinVi provider-sync가 Map
  `/v1/ops/pipeline/*`를 거쳐 그 역할을 한다.
- **gateway: 비-브라우저 API POST.** `Origin`·`Sec-Fetch-Site`가 둘 다 없는 POST는 Basic Auth만으로 통과한다(C7의
  sensor 조작이 bridge network에서 공개 host로 온다). 교차·`null` Origin, Origin 없이 `Sec-Fetch-Site`만 실은 POST는
  403, 인증 없음·틀린 비밀번호는 401. 격리 plane 통합 테스트가 본다. gateway digest `1f7e708e5c1bd519`.
- **Map 전환 계약.** Map API 소비자에 `KOR_TRAVEL_MAP_API_DAGSTER_INTERNAL_GRAPHQL_URL: internal/graphql`(서버 쪽이
  부르는 loopback)을 더했다 — `KOR_TRAVEL_MAP_API_DAGSTER_GRAPHQL_URL: public/graphql`은 보고용으로 남는다. 이름은 Map
  저장소 `feat/dagster-shared-map-prep`에서 확정한다(2026-10-01 현재 그 브랜치에 아직 변경 없음).

## 2026-10-01 — 공용 Dagster: Map 전환 PR(마지막 합류, ADR-54)

브랜치 `feat/dagster-shared-flip-map`(origin/main `3c9661c` 위 — Map 준비 Manager #450 설치본). 참조 전환 `_flip`을 옮겼다.

- Map `dagster.control_plane: shared`. code-server: `<<: *dagster-shared-control-env`, instance digest, 이미지에 구운
  `dagster_home/dagster.yaml` 대신 공용 `dagster.yaml`(compose_binds), gRPC는 이미 `127.0.0.1`, 포트 literal `12703`
  (healthcheck·`KOR_TRAVEL_MAP_DAGSTER_CODE_SERVER_PORT`도).
- Map API(Map #1290의 부르는/보고하는 URL 분리): `KOR_TRAVEL_MAP_API_DAGSTER_URL` →
  `http://127.0.0.1:${KOR_TRAVEL_DAGSTER_WEBSERVER_PORT:-11002}`, `…_DAGSTER_INTERNAL_GRAPHQL_URL` → 그 `/graphql`,
  `…_DAGSTER_GRAPHQL_URL` → `${KTDM_PROD_URL_DAGSTER:-…11001}/graphql`(보고·C7 attestation), `…_DAGSTER_ALLOWED_HOSTS`
  → loopback만(옛 `kor-travel-map-dagster`·`KTDM_PROD_MAP_DAGSTER_HOST` 삭제). Map UI의 `NEXT_PUBLIC_KOR_TRAVEL_MAP_DAGSTER_URL`
  (build arg·env) → 공용 공개 host — 전체 경로 재구축이 UI를 다시 굽는다. C6c의 Map UI build 배선 검사가 그 값을 스위치에서
  고른다(`own`이면 옛 값).
- 옛 `kor-travel-map-dagster`·`-daemon` → `legacy-dagster`, Map의 `services`·`runtime_services`에서 뺌.
  `kor-travel-map-dagster-storage-migrate`는 **남긴다** — pinned 재구축이 옛 metadata DB의 storage head를 계약으로 보고
  (`require_head(... map_dagster_head)`), 되돌리기가 그 DB로 돌아가며, code-server가 그 one-shot에 기댄다. frozen
  render(`--profile bootstrap`)에서 profile로 내리면 재구축의 `run`이 서비스를 찾지 못한다.
- 공용 workspace에 `kortravelmap.dagster.definitions`(127.0.0.1:12703), 네 location이 모두 공용 plane에 있다.
- 합류 조건(n150 읽기 전용 실측): code-server는 `appuser`이고 `/opt/dagster`는 root 소유 읽기 전용이지만
  `/opt/dagster/state`는 이미지에 `appuser` 소유로 있다(공용 `dagster.yaml`의 artifacts·compute_logs 자리) ; pool은 셋 다
  `kor_travel_map.` 접두(`kor_travel_geo`·`krex_notice_snapshot`·`opinet_api`); `concurrency_limits` 행은 Map·공용 둘 다 0
  (pool은 `default_limit: 1`); dagster 1.13.24; 공용 `dagster.yaml`에 Map cap 10; 코드 선언 RUNNING은 schedule 1
  (`current_weather_summary_refresh_minutely_schedule`) + sensor 10 = 옛 instance RUNNING 11과 같다; 공용 instance의 Map
  run 0 — reconcile sensor는 null cursor로 시작한다(Map run ≤ 200, #1290).
- 테스트: `own`인 target이 남지 않아 대조군의 출발점을 `_unflip`(전환 전 모양으로 되돌리기)으로 만든다 — `_unflip` 뒤
  `_flip`이 커밋된 렌더를 위반 없이 다시 만드는지 검사한다. `own_pinned_pair`·`_own_pair_documents`는 스위치만이 아니라
  compose도 되돌린다. f1d는 Map Dagster 서비스를 slot에서 파생하고, 필수 보호 서비스 골든은 9 → 8(옛 webserver·daemon →
  code-server, 바뀐 칸 하나). Map API host allowlist가 loopback만인지 따로 본다.
- 창 스크립트: Map 앱 drain 게이트(precheck와 펜스·취소 뒤), Map 소비자 scope 확인, 끝에 C7 자격증명 파일과
  `.d2-live.env`에 넣을 세 값(공개 GraphQL URL·canonical sha256 — Map README의 값과 같음을 n150에서 확인).

## 2026-10-02 — transport 앱 dump 접두어 문서 갱신(transport ADR-011)

- transport가 백엔드·내부 식별자를 `kor-travel-transport`로 옮겨(transport ADR-011) 앱 백업 dump 이름이
  `parking-radar-*.dump`에서 `kor-travel-transport-*.dump`로 바뀐다. `docker-management.md`의 transport 복원
  설명과 상시 스택 목록(`parking-radar` → `transport`)만 고쳤다. Manager 코드·target·백업 cron은 바뀌지 않는다
  (Manager standalone dump 이름은 원래 앱 접두어와 무관하다). transport 브랜치 머지·배포와 함께 머지한다.

## 2026-10-02 — Map이 OpiNet·KREX 대신 transport 내부 export를 읽는다(Manager 배선)

- Map이 휴게소·주유소·유가를 kor-travel-transport의 `GET /v1/service/exports/*`(header
  `X-Kor-Travel-Transport-Service-Token`, loopback Host만)에서 읽게 되면서, Map Dagster 세 서비스
  (code-server, 옛 webserver·daemon)에 `KOR_TRAVEL_MAP_KOR_TRAVEL_TRANSPORT_BASE_URL`(기본
  `http://127.0.0.1:14001`)과 `KOR_TRAVEL_MAP_KOR_TRAVEL_TRANSPORT_SERVICE_TOKEN`을 concierge read key와
  같은 모양(루트 `.env` 한 이름이 원천, `.env.example` 빈 줄, Map API·UI에는 없음)으로 넣었다.
- 같은 세 서비스에서 `KOR_TRAVEL_MAP_OPINET_*` 여섯과 `KOR_TRAVEL_MAP_KREX_EX/GO_API_KEY`를 지웠다
  (`.env.example`·docs·테스트도). `KRTOUR_MAP_DATA_GO_KR_SERVICE_KEY` 등 다른 provider 키는 그대로다.
  KREX go 폴백·OpiNet scope 선택자를 세던 `test_map_provider_credential_wiring.py`의 검사 다섯은 대상이
  사라져 지웠고, 접두(`KOR_TRAVEL_MAP_OPINET_`·`KOR_TRAVEL_MAP_KREX_`)가 compose·`.env.example` 어디에도
  없음을 보는 검사 하나로 바꿨다. placeholder 유도 검사는 `_TOKEN` 접미도 센다(하한 3 유지).
- **C6c `_FORBIDDEN_MAP_API_PROVIDER_ENV_NAMES`는 그대로 뒀다.** 이 목록은 "Map API 컨테이너에 들어오면
  안 되는 옛 이름" 거부 목록이고 Map `docker/api-entrypoint.sh`의 `removed_provider_keys`와 같은 집합이다.
  `…_API_OPINET_SERVICE_KEY`·`…_API_KREX_SERVICE_KEY`를 빼면 그 거부가 약해질 뿐 얻는 것이 없다.
  `secret_scrub`은 이름 조각(`TOKEN`) 기반이라 새 token도 코드 변경 없이 가려진다 — 주석의 예시만 고쳤다.
- **transport 컨테이너 env는 Manager가 소유하지 않는다**(외부 target, transport 저장소의
  `docker-compose.shared.yml` + n150 `.env.server14`). 그래서 `TRANSPORT_SERVICE_EXPORT_TOKEN`과
  `REST_AREA_COLLECTION_ENABLED=true`는 Manager compose에 만들지 않고, 운영자가 두 쪽을 같은 값으로 맞추는
  절차를 `docker-management.md` 7.3에 적었다. M-T(transport의 Manager 이관) 때 한 원천으로 접는다.

## 2026-10-02 — 공용 plane code-server를 `code-server start`로(reload가 definitions를 다시 import), probe를 자식에 묶고 orphan run 정리

C7 schedule-write가 prod에서 실패한 원인: Map은 cron override를 definitions import 때 읽는데 code-server가 `dagster
api grpc`라 location reload가 "not currently supported" 경고만 남기고 무시됐다. 네 code-server(Map·PinVi·geo·weather —
`code_server_subcommand`로 모양에서 파생)를 `dagster code-server start`로 바꿨다. n150 일회용 컨테이너(live 이미지,
빈 loopback 포트) 실측: `api grpc`는 reload에 import 1회 그대로, `code-server start`는 새 자식 pid로 다시 import.

- **probe(`x-dagster-code-server-probe`, 네 서비스 공유).** proxy health는 고정 SERVING이라 자식에 전달되는
  `ListRepositories`를 본다. 실패가 시간으로 이어지면(load error·닿지 못함 90초, 시간 초과 5분) PID 1(tini)을 끝낸다 — run worker가 있으면
  기다리고(실패한 reload 뒤 옛 자식의 run을 지킨다), 닿지 못함·시간 초과가 2시간이면 run이 있어도 끝낸다. 카운터는
  `boot_id` + PID 1 tick으로 incarnation을 가른다. 실측: 없는 `-m`·자식 kill → 재시작, 실패한 reload 중 run 진행 →
  run SUCCESS 뒤 재시작.
- **orphan run.** `DefaultRunLauncher`는 worker health를 지원하지 않아 daemon의 run monitoring은 code-server
  재시작으로 worker를 잃은 STARTED run을 `max_runtime`(weather 57600초)까지 둔다(2026-10-01 weather 6건 최대 14.7시간,
  queue 54건). probe가 incarnation마다 한 번 reaper를 띄워 컨테이너 시작 전에 시작한 자기 location의 STARTED run만
  실패로 만든다. 여전히 못 잡는 것: 컨테이너는 살아 있는데 worker 하나만 죽는 경우.
- **heartbeat** `DAGSTER_GRPC_PROXY_HEARTBEAT_TTL_SECONDS=600`(자식 argv `--heartbeat-timeout 600` 확인).
- **공용 `dagster.yaml`·`workspace.yaml`은 바꾸지 않았다** — 설치는 code-server만 재생성한다.
- **⚠️ 설치 순서: Map commit이 `fix/dagster-entrypoint-code-server`(Map PR #1295)를 포함하는 pinned pair가 회전된
  뒤에만 이 Manager를 설치한다.** 그 전 Map 이미지는 `code-server start` argv를 봉인에서 거부한다. Manager는 이
  결합을 검사하지 않는다(결박·preflight를 더하지 않는다).

## 2026-10-02 — 공용 Dagster: transport 합류(형제 프로젝트, ADR-54 개정)

브랜치 `feat/transport-shared-dagster`(origin/main `9dc51d9` 위). 짝은 transport `feat/shared-dagster-plane`. 소유자 승인,
전환·배포는 하지 않았다(소유자가 리뷰 뒤 실행). 처음엔 prep(transport `own`)·flip 두 커밋이었으나 transport의 되돌리기를
지원하지 않기로 해(소유자 결정, 아래) 한 커밋으로 합쳤다.

- **prep.** transport는 compose가 그 저장소에 있는 첫 합류자라 Manager가 모양을 파생할 수 없다 →
  `targets.transport.dagster.external`이 선언한다(code-server·옛 webserver·daemon·gateway 서비스 이름, location
  `kor-travel-transport`, 포트 `14005`). registry는 `external`을 형제 프로젝트에만 받고 거기서는 필수, `consumers`는
  금지(소비자는 그 저장소의 운영 UI, 다른 compose project). `DagsterFamily.external_project`·`container_name()`(compose
  기본 이름 `<project>-<service>-1`), `installed_location_owners()`가 선언한 location을 싣는다 — 그러지 않으면 workspace의
  transport location이 "which no Dagster target serves"로 Map·PinVi 재구축을 막는다. 공용 `dagster.yaml`에 transport 상한
  (location 3, `kortraveltransport/run_group` 넷 각 1 — 옛 instance 그대로) → instance digest가 그 파일을 붙인 서비스
  전부에서 바뀐다(geo·weather·map·pinvi code-server는 다음 ensure/재구축 때 한 번 재생성된다).
- **창 스크립트.** 형제 프로젝트면 실행 중 code-server의 compose label(project·working_dir·config_files)로 그 프로젝트를
  `--profile legacy-dagster`까지 렌더하고 같은 규칙으로 파생해 선언과 대조한다(어긋나거나 loopback이 아니거나
  `container_name`을 두면 펜스 전에 멈춘다). env 파일은 `EXTERNAL_ENV_FILE`(0600/0400). code-server 이미지가 호스트에
  있어야 한다(`--no-build`). 공용 plane 서비스는 Manager project, 나머지는 그 project(`project_of`). derive는 `api grpc`와
  `code-server start`를 모두 code-server로 본다(병행 `fix/dagster-code-server-reloadable`와 같은 규칙).
- **#456 뒤 rebase(2026-10-03).** 공용 plane code-server 규칙(`code-server start`, 공용 probe
  `x-dagster-code-server-probe`, proxy heartbeat 600, init·exec 형식)을 형제 프로젝트에도 건다 — 그 compose는 여기서 볼 수
  없으므로 창 스크립트 derive가 공용 URL을 받은 형제 code-server에 대해 Manager 렌더와 대조한다(`api grpc`, 다른 probe 원문·
  포트, shell 형식, heartbeat, init 각각 멈추는 빨간 대조군). `test_every_shared_plane_code_server_reloads_its_definitions`는
  workspace에서 형제 location을 빼고 이 compose의 code-server와 대조한다. transport 쪽은 probe 원문을 복사해 쓴다.
- **적대 리뷰(2026-10-03) 반영 — 펜스 전 가드 둘.** (H1) 형제 이미지의 dagster 버전이 공용 webserver(`{ version }`)와 다르면
  멈춘다(`dagster_versions_match` — n150 실측: 운영 transport 이미지는 1.13.25, 호스트 1.13.24 → 그대로 전환하면 공용
  webserver가 버전 상한으로 unhealthy, 전 테넌트). (M1) 옛 instance의 schedule·sensor 상태가 코드의 `default_status`와
  다르면 이름을 말하고 멈춘다(`instigators_off_their_code_default`) — 공용 instance는 새로 시작해 코드 기본값으로 돈다.
  각각 옛 스크립트에 먼저 빨간 것을 보고(8 failed) 고쳤다. systemd-run 안내는 `-E EXTERNAL_ENV_FILE=…`를 싣는다.
- **flip.** `control_plane: shared`, 공용 workspace에 `kor-travel-transport`(127.0.0.1:14005), workspace digest.
- **되돌리기는 지원하지 않는다**(소유자 결정 2026-10-02, 보장 없는 수동 best-effort). 창 스크립트의 `rollback` 모드는 이
  형제 프로젝트에 대해 검증하지 않았다 — 이전 transport release의 code-server는 `--location-name`이 없어 선언과의
  대조에서 멈춘다. 옛 메타DB는 30일 보존한다. `docs/ports.md`의 옛 12302·14003·14004 기재를 고쳤다.
- 테스트: `test_dagster_external_family.py`(펜스가 transport 옛 컨테이너를 본다 + 셋 각각 돌면 거부하는 빨간 대조군,
  선언이 빠지면 소유자 없음, registry 검증), 창 스크립트 derive를 형제 렌더에 돌리고 location·port·host·
  container_name·gateway 어긋남이 각각 멈추는 빨간 대조군, workspace·G3-b·상한 파생에 선언 포함.
- n150 읽기 전용 실측: transport 옛 instance RUNNING = schedule 10·sensor 0(코드 `default_status` 전부 RUNNING),
  공용 role 연결 3/45, transport code-server root·dagster 1.13.24, `.env.server14`의 `BACKEND_RUNTIME_IMAGE`는 낡은
  digest(→ transport prepare 단계가 전환 env 파일을 만든다), 공용 instance 정의는
  `/opt/kor-travel-docker-manager/config/dagster-shared/dagster.yaml`.
- 검증(n150, python:3.11 컨테이너, CI 핀 ruff 0.16.4): ruff 통과. pytest는 (합치기 전) prep·flip 둘 다 2488 passed·96 skipped·
  14 failed — 14건(`test_docker_service_config` 12, `test_m05_isolated_e2e_driver` 1, `test_pinned_runtime_rebuild` 1)은
  main `9dc51d9`의 같은 환경에서도 같은 14건이다(컨테이너에 docker CLI 없음 → "could not start"). 실제 transport 렌더
  (`docker compose config`, 예시 env)를 derive에 넣어 선언과 일치(CODE·옛 서비스·location·포트)를 확인했다.

## 2026-10-04 — 공용 code-server probe의 LOW 셋(적대 리뷰 2026-10-03)

- **부팅부터 멈춘 자식.** `Check`는 SERVING인데 `ListRepositories`가 처음부터 시간 초과면 한 번도 healthy가 못 되어
  5분 문턱이 영영 안 걸렸다. 한 번도 healthy가 아니면 시간 초과 구간 30분에 죽인다(부팅 때 느린 자식은 기다린다).
- **시계.** 문턱을 `time.monotonic()`으로 잰다 — NTP step이 문턱을 늘이거나 줄이지 않는다. Linux monotonic은 시스템
  전역이고 상태 키가 `boot_id`를 품으므로 healthcheck 프로세스 사이에서 안전하다. 상태 파일에 `monotonic` 꼬리표를 더해
  벽시계로 쓴 옛 파일(필드 넷)은 처음부터 센다. reaper의 시각(dagster `start_time`과 비교)과 5분 시도 간격(파일 mtime과
  비교)은 벽시계 그대로다.
- **임시 파일.** 겹친 probe가 같은 `path + '.new'`를 덮거나 옮기던 것을 probe마다 다른 이름(pid + 난수) + `os.replace`로.
- 테스트(각각 옛 probe에 먼저 빨간 것을 봤다, 7 failed): 부팅부터 시간 초과면 1800초에 죽인다, 벽시계 ±1시간 step에도
  90초에 죽인다(load error·닿지 못함), 남의 `.new` 임시 파일을 건드리지 않고 매 쓰기 이름이 다르다, 옛 형식 파일은
  처음부터다.
- **transport와 함께 간다.** transport `docker-compose.shared.yml`의 원문 사본을 같은 이름 브랜치
  (`fix/dagster-probe-lows`)에 다시 복사했다 — 전환 스크립트가 두 원문을 정확히 대조한다.
- 공용 config digest(instance `c4a18eb47deb0693`·workspace `cd0fff9b88ceaff1`·gateway `1f7e708e5c1bd519`)는 그대로다.
  healthcheck가 바뀌는 것은 code-server 넷(map·pinvi·geo·weather)뿐이다.
