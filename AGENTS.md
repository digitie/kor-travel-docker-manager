# AGENTS.md

## Think Before Coding

- 요청이 모호할 때는 해석을 조용히 정하지 말 것
- 중요한 가정은 숨기지 말고 드러낼 것
- 해석에 따라 구현 방향이 크게 달라지면 그 차이를 먼저 표면화할 것
- 안전하게 진행하기 어려울 정도로 혼란스러우면 추측하지 말고 확인할 것

## Simplicity First

- 요청을 완전히 해결하는 최소한의 코드만 작성할 것
- 요청되지 않은 기능을 추가하지 말 것
- 일회성 용도를 위해 추상화를 만들지 말 것
- 구체적인 필요 없이 설정 가능성이나 유연성을 늘리지 말 것
- 구현이 문제에 비해 커졌다고 느껴지면 줄일 것

## Surgical Changes

- 요청을 처리하는 데 필요한 코드만 변경할 것
- 작업이 요구하지 않으면 주변 로직까지 다시 쓰지 말 것
- 관련 없는 코드의 포맷, 이름, 스타일을 건드리지 말 것
- 사용자가 더 넓은 변경을 원한 것이 아니라면 기존 패턴을 맞출 것
- 관련 없는 문제를 발견하면 패치에 섞지 말고 따로 언급할 것

## Goal-Driven Execution

- 모호한 요청을 구체적이고 검증 가능한 결과로 바꿀 것
- 버그 수정은 재현 없이 바로 신뢰하지 말 것
- 리팩터링은 동작 보존을 전제로 전후 기대를 확인할 것
- 넓고 막연한 점검보다 목적이 분명한 검증을 선호할 것
- 완전한 검증이 불가능하면 무엇이 아직 미검증인지 밝힐 것

## Practical Bias

- 비단순 작업에서는 성급함보다 신중함을 우선할 것
- 변경 내역은 리뷰 가능한 범위와 요청 범위에 가깝게 유지할 것
- 아주 단순하고 명백한 한 줄 작업은 과하게 무겁게 다루지 말 것

## 문서 언어 정책

이 저장소의 **모든 Markdown 문서는 한글로 작성한다**. 예외 없음. `README.md`, `CHANGELOG.md`도 본문은 한글이다.

다음 항목만 영어를 유지한다 — 한글로 옮기면 의미가 변하거나 정확성이 깨지기 때문:

- **코드 식별자**: 함수/클래스/변수/타입/엔드포인트 이름 (예: `DockerService`, `get_container_status`, `/api/containers`).
- **명령어와 경로**: `npm run dev`, `poetry run uvicorn`, `f:\dev\kor-travel-docker-manager\backend`.
- **외부 공식 용어**: Docker, PostgreSQL, PostGIS, RustFS, FastAPI, Next.js, TanStack Query, Tailwind CSS, Shadcn UI.
- **표준 keyword**: ADR, CHANGELOG, semver 라벨.
- **shell 출력 / 로그 예시**: 그대로 캡처한 문자열은 보존.

설명 문장, 절제목, 표 column 헤더, ADR 본문, 빠른 시작 가이드, 일지 항목은 한글로 적는다. 새 문서를 만들 때 영문 초안을 두지 않는다 — 처음부터 한글로 쓴다.

---

## 역할

이 저장소(`kor-travel-docker-manager`)는 PinVi 서비스 구동에 필요한 기반 인프라(PostgreSQL, RustFS 등)의 Docker 컨테이너 구동 관리 및 상태 모니터링을 담당하는 소프트웨어다.

- **Backend**: Python FastAPI 기반으로 구성되어 로컬 Docker 데몬과 상호작용하여 상태를 체크하고 제어한다.
- **Frontend**: Next.js (React), zod, react-hook-form, tanstack query, tailwind css, shadcn ui 기반의 대시보드 웹이다.

공용으로 사용하는 데이터베이스 및 파일 스토리지 인프라를 안정적으로 통합 관리하는 것이 목적이다.

---

## 식별자 (혼동 방지)

| 항목 | 값 |
|------|----|
| GitHub 저장소 이름 | `kor-travel-docker-manager` |
| Backend 기술 스택 | Python 3.11+, FastAPI, Docker SDK, Pytest, Ruff, Mypy |
| Frontend 기술 스택 | Next.js 14+ (App Router), TypeScript, Tailwind CSS, Shadcn UI, TanStack Query |
| DB 서비스 정보 | 통합 PostgreSQL / PostGIS (`kor-travel-geo-postgres`: 5432, DBs: `kor_travel_geo`, `pinvi`, `kor_travel_concierge`, `krtour_map`) |
| 파일 스토리지 정보 | RustFS (기본 host 포트: 12101 / 콘솔 host 포트: 12105) |
| 관측 서비스 정보 | Grafana `kor-travel-grafana`: 12205 / cAdvisor `kor-travel-cadvisor`: 12301 / Prometheus `kor-travel-prometheus`: 12401 |
| 지오코더 서비스 정보 | `kor-travel-geo` API `kor-travel-geo-api-latest`: 12501 / Web UI `kor-travel-geo-ui-latest`: 12505 |
| Concierge 서비스 정보 | `kor-travel-concierge` API `kor-travel-concierge-api-latest`: 12601 / MCP `kor-travel-concierge-mcp-latest`: 12602 / Web UI `kor-travel-concierge-ui-latest`: 12605 |
| Map 서비스 정보 | `kor-travel-map` API `kor-travel-map-api-latest`: 12701 / Dagster `kor-travel-map-dagster-latest`: 12702 / Web UI `kor-travel-map-ui-latest`: 12705 |
| PinVi 서비스 정보 | PinVi API `pinvi-api-latest`: 12801 / Web UI `pinvi-web-latest`: 12805 |
| Manager 포트 정보 | Backend API `12901`, Dashboard Web `12905` |
| Docker target 별칭 | `db`, `storage`, `gra`, `cadv`, `prom`, `geo`, `conc`, `map`, `pinvi` (`srv`, `main`은 `pinvi` 별칭) |

---

## 개발 환경 정책

개발 환경은 Linux 기반이며, Windows 호스트에서는 WSL (Windows Subsystem for Linux)을 사용하여 백엔드/프론트엔드 등을 구동한다. 단, git 및 소스코드 버전 관리는 Windows 호스트에서 직접 진행한다.

- **명령 실행 위치 강제**:
  - `git` 관련 명령(`git status`, `git fetch`, `git switch`, `git add`, `git commit`, `git push` 등)은 Windows 호스트에서만 실행한다.
  - `python`, `poetry`, `pip`, `node`, `npm`, `docker`, `docker compose`, `ktdctl`, `ruff`, `pytest`, 빌드, 서버 실행, 파일 검색 등 git이 아닌 모든 개발/검증 명령은 WSL에서 실행한다.
  - Playwright E2E는 명시 예외로 Windows 호스트에서 실행한다. 브라우저/그래픽/확장 연동 상태를 실제 Windows 사용자 환경 기준으로 검증하기 위함이다.
  - 문서 예시에서 Windows 경로(`F:\...`)가 나오더라도 git과 Playwright E2E를 제외한 실행 명령은 WSL 경로(`/mnt/f/...`)로 변환해 실행한다.

- **에이전트별 고정 worktree**:
  - Google Antigravity: `F:\dev\kor-travel-docker-manager-antigravity`
  - Claude Code: `F:\dev\kor-travel-docker-manager-claude`
  - ChatGPT Codex: `F:\dev\kor-travel-docker-manager-codex`
- Windows 호스트에서 각 worktree 진입 시 `git fetch` 후 `git switch -c agent/<topic> main`으로 새 브랜치를 따서 작업한다.
- CodeGraph 인덱스는 각 worktree에서 최초 1회 `codegraph init -i`로 생성한 후, 작업 시작 시 `codegraph sync`를 수행한다. `.codegraph/`는 gitignore 대상이다.

작업 전에 반드시 다음을 읽는다:
1. `CLAUDE.md` — 현재 작업과 잔존 부채
2. `SKILL.md` — DO NOT 룰, 빠른 시작, 도메인 어휘
3. `docs/architecture.md` — 백엔드 + 프론트엔드 + Docker 구조 설계
4. `docs/decisions.md` — ADR 기록
5. `docs/tasks.md` — 백로그 작업 추적
6. `docs/deploy-runbook.local.md` — (있으면) prod 배포/운영에서 반복된 실수와 민감 접속 정보 정본 런북. `*.local.md` gitignore 대상이라 git으로 전파되지 않으므로 **각 worktree에 수동 복사**해 둔다. prod 배포·운영 작업 전 필독.

---

## prod 배포 & 보안 감사

**prod(n150) 배포 절차·접속·반복 함정의 정본은 `docs/deploy-runbook.local.md`** (gitignore된 로컬 전용, 민감정보 포함)에 있다. 배포 전 반드시 읽고, 특히 **배포 후 공개도메인 브라우저 로그인→로그아웃 전환 검증**을 빼먹지 않는다. (이 런북은 커밋되지 않으므로 각 git worktree에도 같은 경로로 복사해 둔다.)

### remote 푸시 전 보안 감사 (필수 절차)

`git push` / PR 생성 직전에 아래를 수행한다(WSL bash). **하나라도 걸리면 푸시 중지** 후 원인 제거.

1. **스테이징 파일 점검**: `git diff --cached --name-only`에 `*.local.md`, `.env`(`.env.example` 제외), `.env.production`, `prod-access*`, `docker-compose.override.yml`, 키/시크릿 파일이 **없어야** 한다.
2. **diff 비밀 스캔**: 커밋 대상 diff에서 일반 비밀 패턴을 검색한다(이 파일은 커밋되므로 **여기에 실제 호스트/도메인/비밀번호 같은 구체 값을 적지 않는다**).
   ```bash
   git diff --cached -U0 | grep -nEi '(api[_-]?key|secret|password|passwd|token|pbkdf2_sha256|AKIA[0-9A-Z]{16}|BEGIN [A-Z ]*PRIVATE KEY)' && echo '⛔ 의심 항목 발견 — 푸시 중지' || echo '✅ 일반 비밀 패턴 없음'
   ```
   - 매칭이 나오면 placeholder인지 실제 값인지 확인하고, 실제 값이면 제거하거나 `.local`/`.env`로 옮긴다.
   - **프로젝트별 민감 문자열**(prod 호스트 IP·도메인·SSH 사용자·관리자 비밀번호 등)은 `docs/deploy-runbook.local.md`의 "푸시 전 추가 스캔" 패턴으로도 함께 검색한다(그 값들은 런북에만 두고 커밋 파일에는 절대 적지 않는다).
3. **`.env.example`은 placeholder만** — 실제 키가 들어가지 않았는지 확인한다.
4. **신규 파일이 비밀 운반체가 아닌지** — 덤프·로그·백업(`*.log`, `docker compose config`/`.env` 출력 등)이 섞이지 않았는지 확인한다.
5. 통과하면 푸시. 위 절차는 생략하지 말고 매 푸시 전에 실행한다.

---

## 지시 우선순위

1. 사용자 요청
2. 이 `AGENTS.md`
3. `SKILL.md`
4. `docs/architecture.md`, `docs/decisions.md`
5. `docs/tasks.md`, `docs/journal.md`, `README.md`
6. 기존 코드와 테스트

---

## 절대 하지 말 것 (DO NOT)

1. **`main` 직접 푸시 금지**: 반드시 feature 브랜치 + PR 제출 방식을 사용한다.
2. **비즈니스 로직과 인프라 관리의 혼선 금지**: 본 서비스는 PostgreSQL, RustFS의 컨테이너/상태 관리만을 목적으로 한다. PinVi의 여행 예약, 기상 통계 등 상위 도메인 비즈니스 코드를 이곳에 섞지 않는다.
3. **'use client' 누락 금지**: 프론트엔드에서 React 훅 또는 DOM 조작을 수행하는 Next.js 컴포넌트에는 첫 줄에 반드시 `'use client'` 지시어를 추가한다.
4. **포트 충돌 유발 금지**: 통합 PostgreSQL(`5432`), RustFS(`12101`, `12105`), Grafana(`12205`), cAdvisor(`12301`), Prometheus(`12401`), `kor-travel-geo`(`12501`, `12505`), `kor-travel-concierge`(`12601`, `12602`, `12605`), `kor-travel-map`(`12701`, `12702`, `12705`), PinVi(`12801`, `12805`), Manager(`12901`, `12905`) 포트는 Kor Travel/PinVi 구성 프로그램이 공용으로 접근할 수 있어야 하므로 임의로 변경하지 않는다.
5. **API 키 및 비밀번호 하드코딩 금지**: `.env` 및 `.env.local` 파일을 사용하고, git에 커밋하지 않는다.
6. **`.codegraph/` 커밋 금지**: 로컬 인덱싱 파일은 개별 에이전트의 로컬 빌드 결과물이므로 git 추적에서 제외한다.
7. **공용 인프라 설정 분산 금지**: PostgreSQL/RustFS의 Docker 생명주기, 포트, credential, bucket 기본값은 이 저장소에서 관리한다. 하위 프로젝트 저장소에 별도 정지/재시작 스크립트를 다시 만들지 않는다.
8. **Docker target 임시 하드코딩 금지**: 새 target, alias, 초기화/복구 step은 `config/docker-targets.yml`에 추가하고 API/CLI/문서가 같은 기준을 읽도록 유지한다.
9. **포트 정책 우회 금지**: 새 로컬 서비스 포트는 `docs/ports.md`의 `12000 + dependency index * 100 + offset` 규칙을 따른다. PostgreSQL 접속 포트는 표준 `5432`를 사용한다.
10. **Windows에서 개발 명령 실행 금지**: `git`과 Playwright E2E를 제외한 패키지 설치, Docker, 서버 실행, 테스트, 빌드, 파일 검색 명령을 Windows PowerShell/CMD에서 실행하지 않는다.
11. **WSL에서 git 실행 금지**: 버전 관리 작업은 Windows 호스트에서만 수행한다.
12. **Playwright E2E 실행 위치 혼선 금지**: Playwright E2E는 Windows 호스트에서만 실행하고, WSL에서 실행하지 않는다.
13. **remote 푸시 전 보안 감사 생략 금지**: `git push`(특히 PR 생성 직전) 전에 위 "remote 푸시 전 보안 감사" 절차를 수행해, 비밀(API 키·세션 시크릿·비밀번호·prod 호스트/도메인 등)이나 `*.local.md`·`.env*`가 스테이징/커밋에 섞이지 않았는지 확인한다. 통과 전에는 푸시하지 않는다.
14. **배포 후 검증 생략 금지**: prod 재배포 후 `/health`·`:12905` 200만 보지 말고, 공개도메인 **브라우저 로그인→대시보드→로그아웃 전환(+WS 재연결 루프 없음)** 까지 확인한다(반복적으로 깨진 항목). 절차·근본원인·복구는 `docs/deploy-runbook.local.md` 참조.
