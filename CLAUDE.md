# CLAUDE.md — 프로젝트 컨텍스트

이 파일은 에이전트(Claude Code, Antigravity, Codex 등)가 세션 시작 시 가장 먼저 읽는 컨텍스트 문서다.

## 프로젝트 현황 (2026-08-25)

PinVi 구동에 필요한 프로젝트별 전용 PostgreSQL/PostGIS 4개, RustFS, `kor-travel-geo`, `kor-travel-concierge`, `kor-travel-map`, PinVi Docker 컨테이너 구동 관리 및 상태 모니터링 관리 소프트웨어다.
현재 FastAPI API, Next.js 대시보드, Python CLI, 설정 파일 기반 Docker target registry가 구현되어 있다.

현재 코드에는 FastAPI API, Next.js 대시보드, `ktdctl` CLI, 설정 파일 기반 target registry,
전용 PostgreSQL 4개와 RustFS를 포함한 Compose 관리가 구현되어 있다. PostgreSQL은 Geo
`12500`, Concierge `12600`, Map `12700`, PinVi `12800`의 loopback 전용 instance를 사용하며
통합 `5432` instance는 없다.

C6c production은 일반 runtime mutation을 차단하고, host-wide lock을 소유하는 pinned
workflow만 Map·PinVi 일곱 runtime을 같은 generation으로 다룬다. 비운영 환경의 재구축은
`pinvi-pair rebuild-pinned --confirm`으로 제한된다. application `300`의 running state
authority는 v6 pinned generation과 v8 rebuild journal이고, **어느 source revision으로
재구축할지는 root 소유 runtime pin registry가 소유한다**(ADR-40). 설치본에
`pinvi-pair capture`가 보이면 과거 v4 명령이므로 실행하지 말고, 정확한 merged trusted
Manager release를 먼저 설치한다. 독립적인 `ktdctl db-backup`과 `GET /api/v1/backups`는
전용 PostgreSQL backup primitive다.

pinned revision은 더 이상 소스코드 상수가 아니다. `ktdctl pin show/verify/rotate`가
정본 인터페이스이고, terminal(재시도 금지) 판정을 받은 pinset은 `rebuild-pinned`가
mutation 이전에 거부한다 — 해소 경로는 새 revision으로의 회전뿐이다. 계약과 운영 절차는
`docs/docker-management.md` 5.1과 `docs/prod-deployment.md` 2.1을 정본으로 본다.

Manager는 Map API의 destructive/features route를 production에서 literal `true`로 명시 승인하고,
read/cancel principal은 Map API와 PinVi API에만 격리한다. Map UI는 `/ops/datasets` 기준의
login/protected/logout lifecycle, PinVi login은 HTTP route chunk와 hydrated form의 분리된
smoke 계약을 사용한다. PR #73의 content-addressed reference는 active/rollback image 세대를
보존하고 manifest commit 뒤 불필요한 reference를 정리한다.

2026-07-26 C7 공식 gate에서 read-auth `7/7`, KMA active/cap/empty 각 `2/2`,
schedule-write `2/2`, POI-cache-causal `2/2`, `BLOCKED` 0건과 상태 복구를 확인했다.
2026-07-27 compatible-pair에서도 C6c principal smoke와 targeted live, active/rollback
reference 가용성과 cleanup을 확인했다. T-037/038/039/040/041은
`docs/tasks-done.md`로 이관됐다.

현재 active release task는 T-VN-41-F1D-H300이다. Map은 이전 revision 복구와 in-place upgrade를
사용하지 않고 application head `300`을 새 baseline으로 삼는다. exact Map commit의 sealed paired
candidate가 API·Dagster image와 application contract를 제공하고, Manager는 Map UI와 PinVi
API·Web·Dagster 네 image만 build한다. generation manifest는 v6, pinset별 resume journal은 v8이다.
세 DB를 fresh recreate한 뒤 root/finalize fence·intent·result, application/metadata permit과 exact
running image를 검증한다. 다음 gate는 전문 적대 리뷰 2건, n150 `rebuild-pinned --confirm`과
live UI/PinVi acceptance이며, **재구축 전에 `ktdctl pin verify`가 0을 반환해야 한다** —
2026-08-28 기준 동봉 seed의 현재 pinset은 terminal이라 회전이 선행되어야 한다.

T-047은 production compatible-pair readiness를 frozen canonical resolved Compose와 정렬한다.
활성 healthcheck service는 `running + healthy`, healthcheck가 없거나 명시 비활성화된 service는
`running`을 요구한다. service별 예외 목록과 가짜 probe는 금지하며, malformed 계약과 선언된
healthcheck의 비정상 상태는 mutation 전에 fail-close한다. `ps --all`의 service별 record와
canonical scale/replica/container name은 exact singleton이어야 한다.

- **Backend**: Python FastAPI 기반 (`backend/`)
- **Frontend**: Next.js 14+ TypeScript 기반 (`frontend/`)

## 디렉토리 구조

```
/mnt/f/dev/kor-travel-docker-manager/
├── backend/            # FastAPI 백엔드 (Python 3.11+, Poetry)
│   ├── src/            # 백엔드 소스코드
│   └── tests/          # 백엔드 단위/통합 테스트
├── config/             # Docker target alias, 의존 순서, 초기화 step 설정
├── frontend/           # Next.js 프론트엔드 (React, TS, Tailwind, Shadcn)
│   ├── src/app/        # App Router 및 페이지
│   └── src/components/ # UI 컴포넌트
├── docs/               # 아키텍처 및 의사결정 문서
├── docker-compose.yml  # 전용 PostgreSQL/RustFS 및 앱 서비스 로컬 구동 Compose 파일
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
