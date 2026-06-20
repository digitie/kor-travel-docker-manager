# JOURNAL — 작업 일지

이 파일은 `kor-travel-docker-manager` 저장소에서 진행된 작업을 역시간순(가장 최신 항목이 맨 위)으로 기록한다.

---

## 2026-06-20 (Claude Code PR #23/#24 리뷰 후속 수정 — T-011/T-015)

- **작업 내용**:
  - Claude Code가 2026-06-19부터 올린 PR #23, #24(merged/closed 포함)를 확인하고 각각 후속 리뷰 코멘트를 남겼다.
  - #23 후속: 설정 변경 API와 미생성 컨테이너 start fallback이 Docker SDK 직접 `containers.run(...)` 경로로 `network_mode: host` 계약을 우회하던 문제를 수정했다. `docker-compose.yml` 저장 후 `docker compose up -d --force-recreate <service>`로 재생성하고, RustFS는 compose의 `rustfs-init` service를 그대로 실행하도록 변경했다.
  - #24 후속: 운영 콘솔 첫 화면을 compact top bar + KPI strip 중심으로 정리하고, UI/데이터 표시용 font token을 명시했다.
- **검증**:
  - 백엔드 `ktd_venv/bin/python -m ruff check .` 통과.
  - 백엔드 `ktd_venv/bin/python -m pytest` 25 passed.
  - 프론트 `npm run type-check`, `npm run build` 통과.
  - `docker compose config` 통과.
- **주의**:
  - `codegraph sync`는 로컬 `.codegraph` disk I/O 오류로 실패해 직접 파일 확인과 테스트로 검증했다.

---

## 2026-06-20 (프론트엔드 Tailwind v4 + StyleSeed 전면 전환 및 전역 오류 복구 boundary — T-015)

- **작업 내용**:
  - **오류 복구 boundary**(`kor-travel-geo` PR #391 반영): App Router `app/error.tsx`/`app/global-error.tsx`, `components/layout/AppErrorPanel.tsx`, `lib/error-recovery.ts`를 추가했다. Next 기본 영어 오류 화면 대신 한국어 복구 패널을 보여 주고, chunk/RSC/network 계열 런타임 오류는 sessionStorage flag로 같은 pathname당 1회 hard reload를 시도한다.
  - **Tailwind v4 전환**: `globals.css`를 `@import "tailwindcss"` + `@theme` CSS-first로 바꾸고, `postcss.config.js`를 `@tailwindcss/postcss`로 교체, `package.json` 의존성을 tailwindcss/@tailwindcss/postcss `^4`로 올리고 autoprefixer 제거, v3 `tailwind.config.ts`를 삭제했다.
  - **StyleSeed 라이트 토큰**(`kor-travel-geo-ui/docs/DESIGN-RULES.md` 반영): `@theme`에 surface(page/card/subtle/elevated/row), 5단계 text(strong/ink/secondary/tertiary/disabled), 단일 brand teal(`#0f766e`), status(info/warn/danger/ok), 약한 shadow, motion 토큰을 정의했다. `DashboardClient`와 `AppErrorPanel`을 Pure Black 다크에서 이 토큰으로 전면 리스타일(단일 accent·약한 그림자·44px 터치타깃·상태 dot+text·rounded-card)했다.
  - 문서: `docs/DESIGN-RULES.md` 신규(매니저용 포팅), `DESIGN.md`에 StyleSeed 전환 superseded 안내, ADR-17 추가.
- **검증**:
  - v4 의존성 설치(tailwindcss 4.3.1, @tailwindcss/postcss 4.3.1, oxide 네이티브 엔진) 완료.
  - 프론트 `type-check`·`build` 통과(아래 최종 검증 절에서 재확인).
  - 잔여 Pure Black 토큰(`bg-black`/`text-on-dark`/`border-hairline`/`m-blue-*`) 0건 확인.

---

## 2026-06-20 (Docker host 네트워크 전환·컨테이너=호스트 포트 통일·서비스 prod URL 반영·pinvi-dagster 추가·tripmate 잔재 정리 — T-014)

- **작업 내용**:
  - **host 네트워크(dev 기본)**: `docker-compose.yml` 전 서비스(19개)에 `network_mode: ${KTDM_DOCKER_NETWORK_MODE:-host}`를 적용했다. 포트 NAT가 없는 host 모드에 맞춰 인프라(RustFS 12101/12105, Grafana 12205, Prometheus 12401, cAdvisor 12301)와 앱이 호스트 정규 포트에 직접 바인딩하도록 바꾸고, 서비스 간 참조(PostgreSQL DSN, RustFS 엔드포인트, 내부 API/Dagster URL)를 컨테이너명 → `127.0.0.1:<포트>`로 전환했다. `config/prometheus/prometheus.yml` scrape 타깃과 `scripts/ensure-rustfs-buckets.sh` 엔드포인트도 `127.0.0.1` 기준으로 맞췄다.
  - **컨테이너=호스트 포트 통일**: `kor-travel-geo`(이미 동일), `kor-travel-concierge`(api `--port`, mcp `MCP_PORT`, ui는 `next dev` command 오버라이드), `kor-travel-map`(`*_CONTAINER_PORT` 기본값을 12701/12702/12705로), PinVi(api `--port`, web은 `next start -p` command 오버라이드)를 모두 컨테이너 내부 포트 = 호스트 포트로 맞췄다.
  - **PinVi Dagster 추가**: `pinvi-dagster`(host=container 12802) compose service와 registry 컨테이너/`pinvi` target 편입을 추가하고, upstream PinVi 저장소에 `apps/etl/Dockerfile`(python:3.12-slim, editable 설치, `dagster-webserver -m tripmate.etl.definitions`)을 신규 작성했다.
  - **서비스 prod 공개 URL 반영**: 관리 16개 서비스(geo … s3-api)의 운영 공개 주소를 gitignore된 `.env`의 `KTDM_PROD_URL_*`에 저장하고, `docker-targets.yml`의 `prod_url_env`(환경변수 이름만 커밋)와 `docker_service._public_url()`로 읽어 대시보드 `public_url` 링크로 표시하도록 백엔드/프론트엔드를 확장했다. `.env.example`은 example.org 플레이스홀더로만 문서화(도메인 비노출).
  - **tripmate 잔재 정리**: 루트 `tripmate_metrics.db` → `pinvi_metrics.db`(코드는 이미 `pinvi_metrics.db` 사용) 개명, 백엔드 venv `tripmate_venv` 제거 후 문서 표준명 `ktd_venv`로 재생성, 잔여 `backend/logs/tripmate_manager.log` 제거. 추적 코드에는 과거 명칭 잔재가 없었고 journal의 과거 이력 기록만 보존했다.
  - 문서: ADR-16 추가, `docs/tasks.md` T-014 등록, `docs/docker-management.md`(컨테이너 18개·host 모드·포트 동일·pinvi-dagster), `docs/ports.md`(12802·host 모드) 동기화.
- **검증**:
  - `docker compose config` exit 0, 경고/에러 0. 19개 서비스 `network_mode: host`, 모든 published 포트가 host=container, `pinvi-dagster` 렌더링 확인.
  - 백엔드 `ruff check`, `public_url` 해석 단위 검증. 프론트 `type-check`·`build` 통과(예정 항목은 최종 검증 절에서 재확인).
  - 추적 파일 전수 grep으로 실제 도메인·잔여 `tripmate`·구 포트 참조 부재 확인.
- **런타임 검증 필요(범위 외 주의)**:
  - host 모드 실제 기동은 Docker 엔진의 host networking 지원에 의존하므로 사용자 환경에서 `ktdctl <target> --build` 런타임 검증이 필요하다.
  - `pinvi-dagster`는 PinVi `apps/etl` ETL 모듈이 미완(Sprint 1 stub)이라 webserver 기동은 upstream 모듈 상태에 따라 달라질 수 있다.

---

## 2026-06-20 (운영 공개 주소 `.env` 주입 및 CORS 환경변수화 — T-013)

- **작업 내용**:
  - 매니저 백엔드 API/대시보드의 운영 공개 도메인을 소스에 하드코딩하지 않고 gitignore된 env 파일에만 주입하도록 설정 계층을 정비했다(외부 비노출).
  - 백엔드(`main.py`): 기동 시 루트 `.env`(또는 `KOR_TRAVEL_DOCKER_MANAGER_ENV_FILE`)를 `load_dotenv`로 로드하고, CORS 허용 Origin을 `KTDM_CORS_ALLOW_ORIGINS`(콤마 구분, 미설정/`*`이면 전체 허용)로 환경변수화했다. 기존 `allow_origins=["*"]` 개발 기본 동작은 유지.
  - 프론트엔드: 백엔드 주소를 환경별로 분리했다. `frontend/.env.development`(localhost), `frontend/.env.production`(운영 API 도메인)을 추가하고, Next.js 우선순위상 `.env.local`이 `.env.production`을 덮어쓰는 사고를 막기 위해 `.env.local`에서 `NEXT_PUBLIC_BACKEND_URL`을 제거(주석화)했다. WS 주소는 `http→ws` 치환으로 `wss`가 자동 파생된다.
  - 계약 문서화: 루트 `.env.example`과 신규 `frontend/.env.example`에 새 변수를 **플레이스홀더**(`manager.example.org` 등)로만 기재해 실제 도메인 노출 없이 셋업을 안내했다. 실제 값은 gitignore된 루트 `.env`, `frontend/.env.production`에만 존재.
  - 문서: ADR-15 추가, `docs/tasks.md` T-013 등록·완료, `docs/dev-environment.md` 4.3 운영 공개 주소 절 추가.
- **검증**:
  - `git check-ignore`로 실제 도메인 파일 4종(`.env`, `frontend/.env.{production,development,local}`) ignore 확인, 추적 파일 전수 grep에서 실도메인 누출 0건.
  - 백엔드: 전체 `ruff check` 통과, CORS 파싱 검증(콤마 리스트 trim, `*`/미설정→전체 허용, 루트 `.env` end-to-end 로드로 운영 Origin 적용).
  - 프론트엔드: `@next/env`로 환경 우선순위 결정적 검증(prod→운영 API 도메인, dev→localhost, `.env.local` 섀도잉 없음), `npm run type-check`·`npm run build` 통과, 운영 빌드 번들에 API 도메인 인라인 및 `.next` ignore 확인.

---

## 2026-06-17 (브랜드 표기 PinVi 교정 및 관측 컨테이너 재기동)

- **작업 내용**:
  - PR #19에서 `Pinvi`로 표기된 브랜드명을 정식 표기 `PinVi`로 교정했다. 14개 파일에서 대소문자 구분 치환으로 73개 표기를 수정했다(.env.example, AGENTS.md, CLAUDE.md, SKILL.md, README.md, config/docker-targets.yml, docs/*).
  - 소문자 식별자(`pinvi`, `pinvi-api-latest`, `pinvi-media`)와 환경변수 prefix(`PINVI_*`)는 런타임/계약 식별자이므로 그대로 두고, 사람이 읽는 표시 문자열만 `PinVi`로 맞췄다.
  - PR #19의 공용 컨테이너 명칭 변경(`tripmate-* -> kor-travel-*`)을 실제 런타임에 반영하기 위해 관측 컨테이너(rustfs/prometheus/grafana/cadvisor)를 새 이름으로 재생성했다. 공용 DB(`kor-travel-geo-postgres`)와 실행 중인 concierge 스택은 유지했다.
- **검증**:
  - 대소문자 구분 `Pinvi` 잔여 검색 0건, `PinVi` 정상 반영 확인.
  - `docker ps`로 `kor-travel-prometheus/grafana/cadvisor/rustfs` 신규 이름 기동 및 Prometheus `/-/healthy` 200 확인.

---

## 2026-06-17 (멀티 에이전트 MCP/agent/skill 설정 확장 — filesystem MCP & OpenCode 포팅)

- **작업 내용**:
  - Claude Code(`claude.json`), Codex(`codex.json`, `.codex/config.toml`), Antigravity(`antigravity.json`), OpenCode(`opencode.json`) 네 도구에 `@modelcontextprotocol/server-filesystem` MCP 서버를 추가했다. 허용 디렉터리는 각 도구의 worktree(`...-claude`, `...-codex`, `...-antigravity`, `...-opencode`)로 지정해 기존 codegraph cwd 규칙과 일치시켰다.
  - OpenCode에 없던 기존 MCP 설정·agent·skill을 OpenCode 형식으로 포팅했다.
    - **MCP**: `opencode.json`에 playwright/sequential-thinking/codegraph/filesystem 4개 서버를 OpenCode local 스키마(`type:"local"`, `command` 배열, `environment`)로 정의하고 `skills.paths`에 `.opencode/skills`를 등록했다.
    - **Agent**: `.opencode/agent/`에 6개 subagent(api-designer, backend-developer, frontend-developer, mobile-developer, ui-designer, ui-fixer)를 추가했다. Claude markdown 5종은 본문을 그대로 보존하고 frontmatter만 OpenCode 형식(`mode: subagent`, `tools` 맵)으로 변환했으며, Codex에만 있던 ui-fixer는 새로 작성했다.
    - **Skill**: `.opencode/skills/`에 postgres 외 8개 skill(SKILL.md + postgres/references 7종)을 원본과 byte 동일하게 복제했다.
  - codegraph의 `cwd`는 OpenCode local MCP 스키마에 없는 필드(런타임이 instance 디렉터리에서 자동 설정)라 `opencode.json`에서는 제외했다.
- **검증**:
  - 4개 JSON 설정 `ConvertFrom-Json` 파싱 통과, `.codex/config.toml` 포함 5개 설정에서 filesystem 서버 존재 확인.
  - skill 15개 파일 SHA256 원본 동일, 포팅한 agent 5종 본문이 원본과 byte 동일.
  - 5개 병렬 감사 에이전트로 적대적 검증 수행: filesystem 추가·agent·skill·완전성은 모두 pass, opencode.json은 codegraph `cwd` 제거로 해결.
- **범위 외 메모**:
  - `.gemini/mcp.json`은 사용자가 지정한 4개 도구에 포함되지 않아 filesystem을 추가하지 않고 현 상태를 유지했다.
  - playwright 패키지명 `@modelcontextprotocol/server-playwright`는 기존 4개 설정과 동일하게 유지했다(일관성). 공식 `@playwright/mcp`로의 전환은 전체 설정 동기화가 필요한 별도 사안.

---

## 2026-06-15 (PinVi 및 Kor Travel 공용 명칭 정리)

- **작업 내용**:
  - 남아 있던 과거 서비스명 계열 명칭을 PinVi 기준으로 정리했다.
  - 공용 컨테이너 성격이 강한 RustFS, Grafana, cAdvisor, Prometheus 이름은 `kor-travel-*` 기준으로 변경했다.
  - PinVi 전용 database/role/bucket/env 이름을 `pinvi`, `PINVI_*`, `pinvi-media` 기준으로 맞췄다.
  - 과거 geo 패키지명 계열 잔여 명칭이 없는 것을 확인했다.
- **검증**:
  - WSL에서 과거 서비스명과 과거 geo 패키지명 계열 잔여 검색 결과 0건 확인.

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
