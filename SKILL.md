# SKILL — kor-travel-docker-manager 에이전트 매뉴얼

> 이 파일은 당신(AI 에이전트)이 작업을 시작하기 전 반드시 읽어야 한다.
> 1회 읽는 것으로 백엔드 및 프론트엔드의 세부 구성 실수를 방지할 수 있다.

---

## 1. 정체성

본 저장소(`kor-travel-docker-manager`)는 `pinvi`, `kor-travel-concierge`, `kor-travel-map`, `kor-travel-geo`가 사용하는 공용 데이터베이스(PostgreSQL/PostGIS), 파일 저장소(RustFS), 앱 API/Web UI를 Docker 기반으로 안정적으로 통합 관리하고 상태를 대시보드로 모니터링하기 위한 관리 도구다.

- **FastAPI 백엔드**: 로컬 Docker 데몬과 소켓 또는 API로 연동해 컨테이너의 상태(`running`, `exited` 등)를 읽고 Start/Stop/Restart 제어 명령을 실행한다.
- **Python CLI**: `ktdctl db|storage|gra|cadv|prom|geo|conc|map|pinvi|all|srv --build`로 개발환경 의존 Docker를 바로 실행한다.
- **Next.js 프론트엔드**: 관리자 대시보드 화면을 렌더링하며, `DESIGN.md`와 `frontend/tokens.css`의 Hallmark Cobalt Workbench 토큰으로 운영 정보를 표시한다.
- **포트 정책**: 로컬 host 포트는 `docs/ports.md`의 `12000` 시작, target별 `+100`, API `+1`, Web UI `+5` 규칙을 따른다. PostgreSQL은 프로젝트마다 전용 instance이고 포트는 각 대역의 `x00`이다(`12500`/`12600`/`12700`/`12800`, ADR-37 — 표준 `5432`는 폐지). Grafana/cAdvisor/Prometheus는 `12205`/`12301`/`12401`, `kor-travel-geo`는 `12501`/`12505`, `kor-travel-concierge`는 `12601`/`12602`/`12605`, `kor-travel-map`은 `12701`/`12702`/`12705`, PinVi는 `12801`/`12805`, manager 자체는 `12900-12999`를 사용한다.

---

## 2. 빠른 시작

> [!IMPORTANT]
> 본 프로젝트의 개발, 검증, 버전 관리는 **WSL을 포함한 Linux 환경** 내부에서 수행한다. `git`과 CodeGraph도 Linux shell에서만 실행한다. Playwright E2E는 우선 **n150 Linux 운영 환경**에서 수행하고, 불가능한 경우에만 Windows 호스트 실행을 예외로 허용한다.

### 명령 실행 위치

| 실행 위치 | 허용 명령 |
|---|---|
| Linux/WSL | `git`, `codegraph`, `python`, `poetry`, `pip`, `node`, `npm`, `docker`, `docker compose`, `ktdctl`, `ruff`, `pytest`, 빌드, 서버 실행, 일반 파일 검색 |
| n150 Linux | Playwright E2E 우선 실행 (`npx playwright test`, 브라우저 설치 포함) |
| Windows 호스트 | n150에서 Playwright E2E 실행이 불가능한 경우의 예외 실행 |

Playwright E2E 예외 상황을 제외한 작업을 Windows PowerShell/CMD에서 실행하지 않는다.

### 백엔드 (FastAPI) Setup
```bash
cd /mnt/f/dev/kor-travel-docker-manager/backend
poetry install
poetry run ktdctl geo --build
poetry run ruff check .
poetry run pytest
poetry run uvicorn kor_travel_docker_manager.main:app --host 0.0.0.0 --port 12901 --reload
```

### 프론트엔드 (Next.js) Setup
```bash
cd /mnt/f/dev/kor-travel-docker-manager
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
    kor_travel_docker_manager/
      main.py                 — FastAPI 진입점과 루트 환경변수 로드
      api/
        routes.py             — 컨테이너 상태 조회, 제어, 로그 API 엔드포인트
      services/
        registry.py           — config/docker-targets.yml 기반 target registry
        compose_service.py    — docker compose ensure/status/logs 실행
        docker_service.py     — Python Docker SDK 활용 컨테이너 상태 제어 및 수집
  tests/                      — pytest 단위 및 통합 테스트 코드
config/
  docker-targets.yml          — db/storage/gra/cadv/prom/geo/conc/map/pinvi alias, 의존 순서, init step 정의
frontend/
  src/
    app/
      layout.tsx              — 루트 레이아웃 (Global CSS, Provider 구성)
      page.tsx                — 대시보드 메인 뷰 (상태 카드, 제어 버튼, 로그 콘솔)
    components/               — 버튼, 카드 등 프리미엄 디자인 컴포넌트
    lib/                      — API fetch, 입력 검증, 설정 diff 유틸리티
docs/
  architecture.md             — 아키텍처 가이드 (백엔드 ⇄ Docker 소켓, API ⇄ 프론트엔드)
  decisions.md                — 의사결정 기록 (ADRs)
  journal.md                  — 작업 일지 (역시간순)
  tasks.md                    — 백로그 태스크
  dev-environment.md          — 개발 환경 설치 가이드
  docker-management.md        — CLI/API 표면과 target 모델의 정본
  prod-deployment.md          — 운영 호스트 설치·배포 런북
  runtime-pin-registry.md     — pin registry 기능 레퍼런스 (고치기 전에 읽을 것)
  ktdctl-ui-migration.md      — ktdctl→UI 이관 설계와 태스크 분해
```

---

## 3.1 기능별 레퍼런스 (해당 영역을 고치기 전에 읽는다)

기능을 고치기 전에 그 기능의 레퍼런스를 먼저 읽는다. 각 문서 첫 절에 "절대 깨뜨리면
안 되는 불변식"이 있고, 그중 상당수는 **다른 저장소와 공유하는 계약**이라 이 저장소만
보고는 위험을 알 수 없다.

| 기능 | 레퍼런스 | 대표 함정 |
|---|---|---|
| Map·PinVi pin 고정과 재구축 게이트 | [`docs/runtime-pin-registry.md`](docs/runtime-pin-registry.md) | pinset digest 직렬화와 generation/journal 문서 스키마는 kor-travel-map attestation이 exact-dict로 결박한 교차 저장소 계약이다. 키 추가·직렬화 변경은 map 동시 PR 없이는 불가 |
| UI에서의 pin 회전(2-step 요청) | [`docs/runtime-pin-registry.md` §7-1](docs/runtime-pin-registry.md) | 요청 파일은 제안일 뿐 pin이 아니다. 어떤 로드 경로에서도 `runtime_pin_request`를 import하면 안 되고, 그 사실을 회귀가 결박한다 |
| 대시보드 화면 규약 | [`docs/dashboard-ui.md`](docs/dashboard-ui.md) | 오류는 `humanizeError`를 거치고 `alert()`는 금지다. `targets[].containers`는 `depends_on` 전이 폐포라 "첫 매치"로 그룹을 만들면 안 된다 |

---

## 4. 절대 하지 말 것 (DO NOT)

1. **`main` 직접 푸시 금지**: 반드시 브랜치 작업 후 PR 제출을 거쳐 머지한다.
2. **Docker Socket 접근 권한 무시 금지**: Windows 호스트 또는 WSL 환경에서 Docker 데몬에 정상적으로 접근할 수 있도록 `DockerService`가 `docker.from_env()`를 호출할 때 예외 처리를 철저히 작성한다.
3. **Next.js Client Directive 누락 금지**: 프론트엔드에서 React `useState`, `useEffect`, TanStack Query 훅을 사용하는 파일의 첫 줄에 `'use client'`를 누락하지 않는다.
4. **API 키 및 Credential 평문 커밋 금지**: `.env`에 보관하고 git 추적을 방지한다.
5. **독립성 유지 실패 금지**: `kor-travel-docker-manager`는 서비스의 "인프라 관리"만을 수행하므로, 다른 Kor Travel/PinVi 구성 패키지의 비즈니스 로직(예: 지도 렌더링, 관광지 정보 정합성 검사 등)을 수행해서는 안 된다.
6. **인프라 생명주기 재분산 금지**: `kor-travel-geo` 등 하위 프로젝트 저장소가 PostgreSQL/RustFS 및 `kor-travel-geo` API/Web UI 컨테이너를 직접 정지/재시작하지 않도록, 포트·credential·bucket·compose 설정은 이 저장소의 `docker-compose.yml`, `ktdctl` CLI에 둔다.
7. **target 순서 하드코딩 금지**: 새 Docker 의존성을 추가할 때는 `config/docker-targets.yml`의 `dependency_order`, `targets`, `init_steps`를 갱신하고 API/CLI가 같은 registry를 읽게 유지한다.
8. **실행 위치 정책 위반 금지**: `git`, CodeGraph, 개발/검증/Docker/서버 명령은 Linux shell에서만 실행한다. Playwright E2E는 n150 Linux에서 우선 실행하고, 불가능한 경우에만 Windows 호스트를 예외로 사용한다.
9. **`ruff format` 전체 실행 금지**: 이 저장소는 ruff-format 적용본이 아니다. 전체에 돌리면 무관한 파일 수천 줄이 재작성돼 리뷰가 불가능해진다. 린트는 `ruff check`만 쓴다.
10. **교차 저장소 계약 무단 변경 금지**: pinset digest 직렬화, generation manifest(v6)·rebuild journal(v8) 문서 스키마는 kor-travel-map이 결박한 값이다. 바꾸려면 해당 저장소의 동시 PR이 전제다 — 자세한 내용은 [`docs/runtime-pin-registry.md`](docs/runtime-pin-registry.md) 1절.
11. **fail-close 경로에서 예외 삼키기 금지**: 신뢰 판정을 하는 코드에서 `except: return False`/`pass`로 넘어가면 파일이 사라진 순간 보호가 통째로 열린다. 판정할 수 없으면 거부한다.

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

- [ ] 백엔드 `ruff check` 통과 (`ruff format` 전체 실행은 금지 — DO NOT 9번)
- [ ] 백엔드 `pytest` 통과
- [ ] 프론트엔드 `npm run type-check` 통과
- [ ] 프론트엔드 `npm run build` 통과 (Next.js 빌드 성공 확인)
- [ ] `docs/journal.md`에 작업 항목 추가 (역시간순)
- [ ] `docs/tasks.md`의 태스크 상태 갱신
- [ ] 새로운 구조나 설계 추가 시 `docs/decisions.md`에 ADR 추가
- [ ] **새 기능을 추가했으면 기능 레퍼런스 문서를 함께 작성/갱신한다** (§3.1 표에 등재).
      다른 에이전트가 그 기능을 고치기 전에 읽을 문서이므로, 불변식·계약·실패 모드·
      흔한 상황별 대응을 포함한다. 코드 주석으로 대신하지 않는다
- [ ] 줄바꿈이 LF인지 확인한다. Windows에서 스크립트로 파일을 다시 쓰면 CRLF가 섞여
      diff가 파일 전체 재작성으로 부푼다 (`file <path>`로 확인)
