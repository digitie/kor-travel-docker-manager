# AGENTS.md

## 문서 언어 정책

이 저장소의 **모든 Markdown 문서는 한글로 작성한다**. 예외 없음. `README.md`, `CHANGELOG.md`도 본문은 한글이다.

다음 항목만 영어를 유지한다 — 한글로 옮기면 의미가 변하거나 정확성이 깨지기 때문:

- **코드 식별자**: 함수/클래스/변수/타입/엔드포인트 이름 (예: `DockerService`, `get_container_status`, `/api/containers`).
- **명령어와 경로**: `npm run dev`, `poetry run uvicorn`, `f:\dev\tripmate-manager\backend`.
- **외부 공식 용어**: Docker, PostgreSQL, PostGIS, RustFS, FastAPI, Next.js, TanStack Query, Tailwind CSS, Shadcn UI.
- **표준 keyword**: ADR, CHANGELOG, semver 라벨.
- **shell 출력 / 로그 예시**: 그대로 캡처한 문자열은 보존.

설명 문장, 절제목, 표 column 헤더, ADR 본문, 빠른 시작 가이드, 일지 항목은 한글로 적는다. 새 문서를 만들 때 영문 초안을 두지 않는다 — 처음부터 한글로 쓴다.

---

## 역할

이 저장소(`tripmate-manager`)는 TripMate 서비스 구동에 필요한 기반 인프라(PostgreSQL, RustFS 등)의 Docker 컨테이너 구동 관리 및 상태 모니터링을 담당하는 소프트웨어다. 

- **Backend**: Python FastAPI 기반으로 구성되어 로컬 Docker 데몬과 상호작용하여 상태를 체크하고 제어한다.
- **Frontend**: Next.js (React), zod, react-hook-form, tanstack query, tailwind css, shadcn ui 기반의 대시보드 웹이다.

공용으로 사용하는 데이터베이스 및 파일 스토리지 인프라를 안정적으로 통합 관리하는 것이 목적이다.

---

## 식별자 (혼동 방지)

| 항목 | 값 |
|------|----|
| GitHub 저장소 이름 | `tripmate-manager` |
| Backend 기술 스택 | Python 3.11+, FastAPI, Docker SDK, Pytest, Ruff, Mypy |
| Frontend 기술 스택 | Next.js 14+ (App Router), TypeScript, Tailwind CSS, Shadcn UI, TanStack Query |
| DB 서비스 정보 | 통합 PostgreSQL / PostGIS (`kraddr-geo-postgres`: 5432, DBs: `kraddr_geo`, `tripmate`, `kor_travel_concierge`, `krtour_map`) |
| 파일 스토리지 정보 | RustFS (기본 host 포트: 12101 / 콘솔 host 포트: 12105) |
| 지오코더 서비스 정보 | `kor-travel-geo` API `kraddr-geo-api-latest`: 12201 / Web UI `kraddr-geo-ui-latest`: 12205 |
| Manager 포트 정보 | Backend API `12901`, Dashboard Web `12905` |
| Docker target 별칭 | `db`, `storage`, `geo`, `map`, `ai`, `main` (`config/docker-targets.yml` 순서 기준) |

---

## 개발 환경 정책

개발 환경은 Linux 기반이며, Windows 호스트에서는 WSL (Windows Subsystem for Linux)을 사용하여 백엔드/프론트엔드 등을 구동한다. 단, git 및 소스코드 버전 관리는 Windows 호스트에서 직접 진행한다.

- **명령 실행 위치 강제**:
  - `git` 관련 명령(`git status`, `git fetch`, `git switch`, `git add`, `git commit`, `git push` 등)은 Windows 호스트에서만 실행한다.
  - `python`, `poetry`, `pip`, `node`, `npm`, `docker`, `docker compose`, `tmctl`, `ruff`, `pytest`, 빌드, 서버 실행, 파일 검색 등 git이 아닌 모든 개발/검증 명령은 WSL에서 실행한다.
  - Playwright E2E는 명시 예외로 Windows 호스트에서 실행한다. 브라우저/그래픽/확장 연동 상태를 실제 Windows 사용자 환경 기준으로 검증하기 위함이다.
  - 문서 예시에서 Windows 경로(`F:\...`)가 나오더라도 git과 Playwright E2E를 제외한 실행 명령은 WSL 경로(`/mnt/f/...`)로 변환해 실행한다.

- **에이전트별 고정 worktree**:
  - Google Antigravity: `F:\dev\tripmate-manager-antigravity`
  - Claude Code: `F:\dev\tripmate-manager-claude`
  - ChatGPT Codex: `F:\dev\tripmate-manager-codex`
- Windows 호스트에서 각 worktree 진입 시 `git fetch` 후 `git switch -c agent/<topic> main`으로 새 브랜치를 따서 작업한다.
- CodeGraph 인덱스는 각 worktree에서 최초 1회 `codegraph init -i`로 생성한 후, 작업 시작 시 `codegraph sync`를 수행한다. `.codegraph/`는 gitignore 대상이다.

작업 전에 반드시 다음을 읽는다:
1. `CLAUDE.md` — 현재 작업과 잔존 부채
2. `SKILL.md` — DO NOT 룰, 빠른 시작, 도메인 어휘
3. `docs/architecture.md` — 백엔드 + 프론트엔드 + Docker 구조 설계
4. `docs/decisions.md` — ADR 기록
5. `docs/tasks.md` — 백로그 작업 추적

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
2. **비즈니스 로직과 인프라 관리의 혼선 금지**: 본 서비스는 PostgreSQL, RustFS의 컨테이너/상태 관리만을 목적으로 한다. TripMate의 여행 예약, 기상 통계 등 상위 도메인 비즈니스 코드를 이곳에 섞지 않는다.
3. **'use client' 누락 금지**: 프론트엔드에서 React 훅 또는 DOM 조작을 수행하는 Next.js 컴포넌트에는 첫 줄에 반드시 `'use client'` 지시어를 추가한다.
4. **포트 충돌 유발 금지**: 통합 PostgreSQL(`5432`), RustFS(`12101`, `12105`), `kor-travel-geo`(`12201`, `12205`), Manager(`12901`, `12905`) 포트는 TripMate 구성 프로그램이 공용으로 접근할 수 있어야 하므로 임의로 변경하지 않는다.
5. **API 키 및 비밀번호 하드코딩 금지**: `.env` 및 `.env.local` 파일을 사용하고, git에 커밋하지 않는다.
6. **`.codegraph/` 커밋 금지**: 로컬 인덱싱 파일은 개별 에이전트의 로컬 빌드 결과물이므로 git 추적에서 제외한다.
7. **공용 인프라 설정 분산 금지**: PostgreSQL/RustFS의 Docker 생명주기, 포트, credential, bucket 기본값은 이 저장소에서 관리한다. 하위 프로젝트 저장소에 별도 정지/재시작 스크립트를 다시 만들지 않는다.
8. **Docker target 임시 하드코딩 금지**: 새 target, alias, 초기화/복구 step은 `config/docker-targets.yml`에 추가하고 API/CLI/문서가 같은 기준을 읽도록 유지한다.
9. **포트 정책 우회 금지**: 새 로컬 서비스 포트는 `docs/ports.md`의 `12000 + dependency index * 100 + offset` 규칙을 따른다. PostgreSQL 접속 포트는 표준 `5432`를 사용한다.
10. **Windows에서 개발 명령 실행 금지**: `git`과 Playwright E2E를 제외한 패키지 설치, Docker, 서버 실행, 테스트, 빌드, 파일 검색 명령을 Windows PowerShell/CMD에서 실행하지 않는다.
11. **WSL에서 git 실행 금지**: 버전 관리 작업은 Windows 호스트에서만 수행한다.
12. **Playwright E2E 실행 위치 혼선 금지**: Playwright E2E는 Windows 호스트에서만 실행하고, WSL에서 실행하지 않는다.
