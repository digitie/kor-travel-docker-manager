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
