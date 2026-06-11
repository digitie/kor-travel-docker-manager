# DECISIONS — Architecture Decision Records

본 문서는 `tripmate-manager` 프로젝트의 의사결정을 시간순으로 누적한다. 결정이 뒤집힐 때도 이전 기록은 지우지 않고 `superseded by ADR-XXX`로 표시한다.

## ADR 표준 형식

```
## ADR-NNN: <결정 요약>

- 상태: proposed | accepted | superseded by ADR-XXX
- 날짜: YYYY-MM-DD
- 결정자: <agent | human>

### 컨텍스트
<무엇이 문제였나. 어떤 제약·요구가 있었나.>

### 결정
<무엇을 정했는가. 한 문장으로.>

### 근거
- 

### 결과(긍정)
- 

### 결과(부정)
- 

### 후속
- (open) 추가 검증 필요한 사항
```

---

## ADR-1: 모노레포 구조 채택 (FastAPI 백엔드 + Next.js 프론트엔드)

- 상태: accepted
- 날짜: 2026-06-10
- 결정자: human, AI agent

### 컨텍스트
TripMate 인프라 관리 도구를 설계할 때, 백엔드 Docker 데몬을 통제하는 로직과 사용자에게 대시보드를 노출하는 UI 로직이 필요했다. 단일 저장소(Monorepo)에서 백엔드와 프론트엔드를 함께 관리하는 것이 릴리즈 및 개발의 편의성을 높일 것이라 판단했다.

### 결정
저장소 루트 아래 `backend/` (FastAPI) 및 `frontend/` (Next.js) 폴더를 독립적으로 분리하는 모노레포 폴더 구조를 채택한다.

### 근거
- 독립적인 의존성 관리 가능: 백엔드는 Python 가상환경(Poetry), 프론트엔드는 Node.js(npm)로 분리되어 패키지 충돌 방지.
- 소스 코드 추적 용이성: 인프라 관리라는 단일 도메인의 코드가 하나의 저장소로 묶여 관리 효율성 극대화.

### 결과(긍정)
- 저장소 하나만 복제(Clone)하면 프론트엔드와 백엔드 개발 준비 완료.
- Docker-Compose 등 루트 환경 설정의 공유가 용이함.

### 결과(부정)
- 배포 시 빌드 파이프라인을 `backend`와 `frontend` 각각 별도로 정의해야 함.

---

## ADR-2: Docker 데몬 제어를 위해 Python Docker SDK 채택

- 상태: accepted
- 날짜: 2026-06-10
- 결정자: human, AI agent

### 컨텍스트
백엔드가 PostgreSQL 및 RustFS 등의 Docker 컨테이너 상태를 검사하고 시작/중지 등의 제어 명령을 실행하려면 Docker 데몬과 API 통신을 수행해야 했다.

### 결정
공식적으로 관리되고 견고한 **Docker SDK for Python** (`docker` 라이브러리)을 사용해 Docker Engine의 소켓/파이프에 바인딩한다.

### 근거
- 단순 CLI 호출(예: `subprocess.run(["docker", "ps"])`) 방식 대비 정형화된 JSON 데이터 파싱이 용이하고 에러 처리가 훨씬 안전함.
- Windows Named Pipe 및 Linux Unix Socket 경로를 자동으로 해석해 호환성이 높음.

### 결과(긍정)
- 컨테이너 시작, 정지, 재시작 및 실시간 리소스 통계 조회 코드의 신뢰도 증가.
- 복잡한 쉘 파싱 로직 불필요.

### 결과(부정)
- 호스트에 Docker 데몬이 없거나 권한 바인딩이 실패할 경우, 애플리케이션 시작 단계에서 예외 핸들링을 구현해야 함.

---

## ADR-3: 프론트엔드 상태 관리에 TanStack Query (React Query) 채택

- 상태: accepted
- 날짜: 2026-06-10
- 결정자: human, AI agent

### 컨텍스트
대시보드는 PostgreSQL과 RustFS 등의 컨테이너의 실시간 구동 상태를 동적으로 계속 반영해야 한다. 복잡한 WebSockets 연결 없이 가볍게 주기적으로 상태를 업데이트(Polling)하는 구조가 필요했다.

### 결정
서버 상태 동기화 및 캐싱 라이브러리로 **TanStack Query (React Query) v5**를 채택하고, 5초 주기의 Polling 메커니즘을 적용한다.

### 근거
- Polling 구현이 매우 간단함 (`refetchInterval: 5000`).
- API 요청에 대한 Loading, Success, Error 상태를 선언적으로 관리 가능하여 UI 반응성 확보.
- 불필요한 전역 상태 라이브러리(Zustand, Redux) 도입 제거로 번들 사이즈 및 복잡도 축소.

### 결과(긍정)
- 컨테이너가 제어 명령에 의해 상태가 변했을 때(예: starting -> running), 대시보드가 자동으로 5초 내로 리렌더링되어 최신 상태 반영.
- 캐싱 및 재시도 로직 기본 지원.

### 결과(부정)
- 주기적인 API 호출 발생 (5초마다 백엔드 조회). 성능 부하가 매우 미미하여 수용 가능.

---

## ADR-4: 에이전트 친화적 문서 및 설정 구조 채택 (maplibre-vworld-js 미러링)

- 상태: accepted
- 날짜: 2026-06-10
- 결정자: human, AI agent

### 컨텍스트
여러 AI 에이전트(Antigravity, Claude Code, Codex)가 동시에 또는 번갈아 협업할 때 컨텍스트 오염을 막고 지침을 명확히 할 규칙 시스템이 필요했다. 앞서 `maplibre-vworld-js`에서 성공적으로 적용된 AI 가이드 구조가 이를 훌륭히 해결해 주었다.

### 결정
`maplibre-vworld-js`와 동일한 `CLAUDE.md`, `AGENTS.md`, `SKILL.md` 문서와 에이전트별 `.json`/`.toml` 설정 구조를 카피해 프로젝트 루트에 배치한다.

### 근거
- 에이전트가 로컬 `CodeGraph` 싱크를 독립적으로 수행할 수 있는 고정 worktree 환경 지원.
- 문서 언어 정책(한글 정책) 및 DO NOT 규칙을 명시해 코드 및 기여 정합성 확보.

### 결과(긍정)
- 새로운 세션이 시작될 때 에이전트가 실시간으로 할 일 및 제한 사항을 즉각 인지.
- 다중 에이전트 간 개발 생산성 증가.

### 결과(부정)
- 루트 폴더에 설정용 마크다운 및 JSON 파일이 늘어남 (관련 정보는 gitignore 또는 에이전트 내부 가이드로 통제 가능).

---

## ADR-5: python-kraddr-geo 인프라 생명주기를 TripMate Manager로 이관한다

- 상태: accepted
- 날짜: 2026-06-10
- 결정자: human, AI agent

### 컨텍스트
`python-kraddr-geo`는 자체 저장소에서 PostgreSQL/PostGIS compose와 RustFS 구동 스크립트를 관리해 왔다. 같은 PC에서 `tripmate`, `tripmate-agent`, `python-krtour-map`, `python-kraddr-geo`가 공용 PostgreSQL/RustFS 포트를 함께 쓰면서, 각 저장소가 개별적으로 컨테이너를 정지/재시작하면 포트 점유와 credential 기준이 충돌할 수 있다.

### 결정
PostgreSQL/PostGIS와 RustFS의 Docker 생명주기와 로컬 포트·credential·bucket 기본값은 `tripmate-manager`가 관리한다. `python-kraddr-geo`는 host port `15434`와 RustFS `9003`/`9004`에 접속하되, 컨테이너 생성·정지·재시작은 `tripmate-manager`의 compose, `scripts/infra.sh`, 백엔드 API 또는 대시보드를 통해 수행한다.

### 근거
- 공용 인프라의 stop/restart 권한을 한 저장소에 모으면 포트 경합과 중복 컨테이너 제거 위험이 줄어든다.
- `python-kraddr-geo`의 T-027 최종 DB 포트(`15434`)와 RustFS 포트(`9003`/`9004`)를 그대로 유지하면 기존 DSN과 프론트엔드 설정을 크게 바꾸지 않아도 된다.
- manager 대시보드가 `tripmate-postgres`, `kraddr-geo-postgres`, `tripmate-rustfs` 상태를 함께 보여 줄 수 있다.
# DECISIONS — Architecture Decision Records

본 문서는 `tripmate-manager` 프로젝트의 의사결정을 시간순으로 누적한다. 결정이 뒤집힐 때도 이전 기록은 지우지 않고 `superseded by ADR-XXX`로 표시한다.

## ADR 표준 형식

```
## ADR-NNN: <결정 요약>

- 상태: proposed | accepted | superseded by ADR-XXX
- 날짜: YYYY-MM-DD
- 결정자: <agent | human>

### 컨텍스트
<무엇이 문제였나. 어떤 제약·요구가 있었나.>

### 결정
<무엇을 정했는가. 한 문장으로.>

### 근거
- 

### 결과(긍정)
- 

### 결과(부정)
- 

### 후속
- (open) 추가 검증 필요한 사항
```

---

## ADR-1: 모노레포 구조 채택 (FastAPI 백엔드 + Next.js 프론트엔드)

- 상태: accepted
- 날짜: 2026-06-10
- 결정자: human, AI agent

### 컨텍스트
TripMate 인프라 관리 도구를 설계할 때, 백엔드 Docker 데몬을 통제하는 로직과 사용자에게 대시보드를 노출하는 UI 로직이 필요했다. 단일 저장소(Monorepo)에서 백엔드와 프론트엔드를 함께 관리하는 것이 릴리즈 및 개발의 편의성을 높일 것이라 판단했다.

### 결정
저장소 루트 아래 `backend/` (FastAPI) 및 `frontend/` (Next.js) 폴더를 독립적으로 분리하는 모노레포 폴더 구조를 채택한다.

### 근거
- 독립적인 의존성 관리 가능: 백엔드는 Python 가상환경(Poetry), 프론트엔드는 Node.js(npm)로 분리되어 패키지 충돌 방지.
- 소스 코드 추적 용이성: 인프라 관리라는 단일 도메인의 코드가 하나의 저장소로 묶여 관리 효율성 극대화.

### 결과(긍정)
- 저장소 하나만 복제(Clone)하면 프론트엔드와 백엔드 개발 준비 완료.
- Docker-Compose 등 루트 환경 설정의 공유가 용이함.

### 결과(부정)
- 배포 시 빌드 파이프라인을 `backend`와 `frontend` 각각 별도로 정의해야 함.

---

## ADR-2: Docker 데몬 제어를 위해 Python Docker SDK 채택

- 상태: accepted
- 날짜: 2026-06-10
- 결정자: human, AI agent

### 컨텍스트
백엔드가 PostgreSQL 및 RustFS 등의 Docker 컨테이너 상태를 검사하고 시작/중지 등의 제어 명령을 실행하려면 Docker 데몬과 API 통신을 수행해야 했다.

### 결정
공식적으로 관리되고 견고한 **Docker SDK for Python** (`docker` 라이브러리)을 사용해 Docker Engine의 소켓/파이프에 바인딩한다.

### 근거
- 단순 CLI 호출(예: `subprocess.run(["docker", "ps"])`) 방식 대비 정형화된 JSON 데이터 파싱이 용이하고 에러 처리가 훨씬 안전함.
- Windows Named Pipe 및 Linux Unix Socket 경로를 자동으로 해석해 호환성이 높음.

### 결과(긍정)
- 컨테이너 시작, 정지, 재시작 및 실시간 리소스 통계 조회 코드의 신뢰도 증가.
- 복잡한 쉘 파싱 로직 불필요.

### 결과(부정)
- 호스트에 Docker 데몬이 없거나 권한 바인딩이 실패할 경우, 애플리케이션 시작 단계에서 예외 핸들링을 구현해야 함.

---

## ADR-3: 프론트엔드 상태 관리에 TanStack Query (React Query) 채택

- 상태: accepted
- 날짜: 2026-06-10
- 결정자: human, AI agent

### 컨텍스트
대시보드는 PostgreSQL과 RustFS 등의 컨테이너의 실시간 구동 상태를 동적으로 계속 반영해야 한다. 복잡한 WebSockets 연결 없이 가볍게 주기적으로 상태를 업데이트(Polling)하는 구조가 필요했다.

### 결정
서버 상태 동기화 및 캐싱 라이브러리로 **TanStack Query (React Query) v5**를 채택하고, 5초 주기의 Polling 메커니즘을 적용한다.

### 근거
- Polling 구현이 매우 간단함 (`refetchInterval: 5000`).
- API 요청에 대한 Loading, Success, Error 상태를 선언적으로 관리 가능하여 UI 반응성 확보.
- 불필요한 전역 상태 라이브러리(Zustand, Redux) 도입 제거로 번들 사이즈 및 복잡도 축소.

### 결과(긍정)
- 컨테이너가 제어 명령에 의해 상태가 변했을 때(예: starting -> running), 대시보드가 자동으로 5초 내로 리렌더링되어 최신 상태 반영.
- 캐싱 및 재시도 로직 기본 지원.

### 결과(부정)
- 주기적인 API 호출 발생 (5초마다 백엔드 조회). 성능 부하가 매우 미미하여 수용 가능.

---

## ADR-4: 에이전트 친화적 문서 및 설정 구조 채택 (maplibre-vworld-js 미러링)

- 상태: accepted
- 날짜: 2026-06-10
- 결정자: human, AI agent

### 컨텍스트
여러 AI 에이전트(Antigravity, Claude Code, Codex)가 동시에 또는 번갈아 협업할 때 컨텍스트 오염을 막고 지침을 명확히 할 규칙 시스템이 필요했다. 앞서 `maplibre-vworld-js`에서 성공적으로 적용된 AI 가이드 구조가 이를 훌륭히 해결해 주었다.

### 결정
`maplibre-vworld-js`와 동일한 `CLAUDE.md`, `AGENTS.md`, `SKILL.md` 문서와 에이전트별 `.json`/`.toml` 설정 구조를 카피해 프로젝트 루트에 배치한다.

### 근거
- 에이전트가 로컬 `CodeGraph` 싱크를 독립적으로 수행할 수 있는 고정 worktree 환경 지원.
- 문서 언어 정책(한글 정책) 및 DO NOT 규칙을 명시해 코드 및 기여 정합성 확보.

### 결과(긍정)
- 새로운 세션이 시작될 때 에이전트가 실시간으로 할 일 및 제한 사항을 즉각 인지.
- 다중 에이전트 간 개발 생산성 증가.

### 결과(부정)
- 루트 폴더에 설정용 마크다운 및 JSON 파일이 늘어남 (관련 정보는 gitignore 또는 에이전트 내부 가이드로 통제 가능).

---

## ADR-5: python-kraddr-geo 인프라 생명주기를 TripMate Manager로 이관한다

- 상태: accepted
- 날짜: 2026-06-10
- 결정자: human, AI agent

### 컨텍스트
`python-kraddr-geo`는 자체 저장소에서 PostgreSQL/PostGIS compose와 RustFS 구동 스크립트를 관리해 왔다. 같은 PC에서 `tripmate`, `tripmate-agent`, `python-krtour-map`, `python-kraddr-geo`가 공용 PostgreSQL/RustFS 포트를 함께 쓰면서, 각 저장소가 개별적으로 컨테이너를 정지/재시작하면 포트 점유와 credential 기준이 충돌할 수 있다.

### 결정
PostgreSQL/PostGIS와 RustFS의 Docker 생명주기와 로컬 포트·credential·bucket 기본값은 `tripmate-manager`가 관리한다. `python-kraddr-geo`는 host port `15434`와 RustFS `9003`/`9004`에 접속하되, 컨테이너 생성·정지·재시작은 `tripmate-manager`의 compose, `scripts/infra.sh`, 백엔드 API 또는 대시보드를 통해 수행한다.

### 근거
- 공용 인프라의 stop/restart 권한을 한 저장소에 모으면 포트 경합과 중복 컨테이너 제거 위험이 줄어든다.
- `python-kraddr-geo`의 T-027 최종 DB 포트(`15434`)와 RustFS 포트(`9003`/`9004`)를 그대로 유지하면 기존 DSN과 프론트엔드 설정을 크게 바꾸지 않아도 된다.
- manager 대시보드가 `tripmate-postgres`, `kraddr-geo-postgres`, `tripmate-rustfs` 상태를 함께 보여 줄 수 있다.

### 결과(긍정)
- 로컬 인프라 구동·정지·재시작 절차가 `tripmate-manager`로 일원화된다.
- `python-kraddr-geo`는 애플리케이션/API/UI 실행과 접속 설정에 집중한다.

### 결과(부정)
- `python-kraddr-geo`만 단독 복제한 환경에서는 인프라를 올리기 전에 `tripmate-manager` checkout이 필요하다.

---

## ADR-6: BMW M 시각 양식(Look & Feel)의 인프라 대시보드 수렴 및 react-doctor 최적화

- 상태: accepted
- 날짜: 2026-06-11
- 결정자: human, AI agent

### 컨텍스트
사용자 요청(DESIGN.md 지침)에 따라 전체 프론트엔드의 스타일 테마를 BMW M 브랜드의 럭셔리 시각 원칙(Pure Black `#000000` 배경, 직각 `rounded-none` 모서리, `Inter`/`Saira` 서체의 700대 300 대비, M 삼색선 디바이더 희소성 적용 등)으로 일체화해야 했다. 그러나 이전 구현에서는 대시보드 상단에 부적절한 자동차 피트라인 배경 이미지가 들어가 쇼케이스용 페이지처럼 오인될 소지가 있었고, 구글 폰트 로드 과정에서 FOUT/FOIT 혹은 CSS 로드 실패가 유발되는 결함이 있었다. 또한 `react-doctor` 분석 결과 25개의 품질 최적화 경고가 발견되었다.

### 결정
구글 폰트 로드는 `layout.tsx`에서 `next/font/google`을 탑재하되 body에 `font-sans`를 명시하여 폴백 경로를 완성해 CSS 링크 깨짐을 완전히 해결한다. 그리고 상단 헤더의 자동차 배경 이미지를 배제하고, `page.tsx` (Server Component)와 `DashboardClient.tsx` (Client Component)로 역할을 분할하는 컴포넌트 아키텍처를 도입하여 `react-doctor` 경고 25건을 전면 조치(dynamic import, stable key, derived state 제거, aria-label 추가 등)한다.

### 근거
- 디자인 본질의 회복: 대시보드 본연의 목적에 방해가 되는 차량 이미지 오버레이를 지우고 오직 Pure Black 스타일 및 타이포그래피 대조를 융합하여 기계적인 다크 UI로 회귀.
- 코드 품질 및 확장성 확보: `react-doctor` 기반의 성능/접근성/버그 경고를 전원 해결하여 렌더링 멱등성과 런타임 성능을 개선함.

### 결과(긍정)
- 대시보드의 시각적 오염(자동차 사진 등)이 제거되고 극히 미니멀하며 세련된 IT 인프라 제어 센터 디자인으로 교정됨.
- `react-doctor` 결함 요소를 제거하여 접근성과 dynamic-import 기반의 첫 페이지 로딩 속도를 향상시킴.

### 결과(부정)
- 컴포넌트 파일이 분할되어 구조가 1계층 늘어남 (그러나 Next.js 14 표준 구조이므로 유지보수 상으로 권장되는 양식임).

### 후속
- (closed) `react-doctor`를 통한 진단 및 타입 정합성 검사를 모두 성공적으로 수행하여 컴파일 안정성 확보.
