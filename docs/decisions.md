# DECISIONS — Architecture Decision Records

본 문서는 `kor-travel-docker-manager` 프로젝트의 의사결정을 시간순으로 누적한다. 결정이 뒤집힐 때도 이전 기록은 지우지 않고 `superseded by ADR-XXX`로 표시한다.

## ADR 표준 형식

```text
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
Kor Travel/PinVi 인프라 관리 도구를 설계할 때, 백엔드 Docker 데몬을 통제하는 로직과 사용자에게 대시보드를 노출하는 UI 로직이 필요했다. 단일 저장소에서 백엔드와 프론트엔드를 함께 관리하는 것이 릴리즈 및 개발의 편의성을 높일 것이라 판단했다.

### 결정
저장소 루트 아래 `backend/` 및 `frontend/` 폴더를 독립적으로 분리하는 모노레포 폴더 구조를 채택한다.

### 근거
- 백엔드는 Python 가상환경(Poetry), 프론트엔드는 Node.js(npm)로 의존성을 분리할 수 있다.
- 인프라 관리라는 단일 도메인의 코드와 문서를 하나의 저장소에서 추적할 수 있다.

### 결과(긍정)
- 저장소 하나만 복제하면 프론트엔드와 백엔드 개발 준비가 가능하다.
- Docker Compose 등 루트 환경 설정의 공유가 쉽다.

### 결과(부정)
- 배포 시 백엔드와 프론트엔드 빌드 파이프라인을 각각 정의해야 한다.

---

## ADR-2: Docker 데몬 제어를 위해 Python Docker SDK 채택

- 상태: accepted
- 날짜: 2026-06-10
- 결정자: human, AI agent

### 컨텍스트
백엔드가 PostgreSQL 및 RustFS 컨테이너 상태를 검사하고 시작/중지 등의 제어 명령을 실행하려면 Docker 데몬과 API 통신을 수행해야 했다.

### 결정
공식적으로 관리되고 견고한 Docker SDK for Python (`docker` 라이브러리)을 사용해 Docker Engine의 소켓/파이프에 바인딩한다.

### 근거
- 단순 CLI 호출 대비 정형화된 JSON 데이터 파싱과 에러 처리가 쉽다.
- Windows Named Pipe 및 Linux Unix Socket 경로를 자동으로 해석해 호환성이 높다.

### 결과(긍정)
- 컨테이너 시작, 정지, 재시작 및 실시간 리소스 통계 조회 코드의 신뢰도가 높아진다.
- 복잡한 쉘 출력 파싱 로직이 필요 없다.

### 결과(부정)
- 호스트에 Docker 데몬이 없거나 권한 바인딩이 실패할 경우 예외 처리가 필요하다.

---

## ADR-3: 프론트엔드 상태 관리에 TanStack Query 채택

- 상태: accepted
- 날짜: 2026-06-10
- 결정자: human, AI agent

### 컨텍스트
대시보드는 PostgreSQL과 RustFS 컨테이너의 실시간 구동 상태를 계속 반영해야 한다. 복잡한 전역 상태 관리 없이 서버 상태를 캐싱하고 주기적으로 갱신할 구조가 필요했다.

### 결정
서버 상태 동기화 및 캐싱 라이브러리로 TanStack Query v5를 채택하고, WebSocket 연결 실패 시 5초 주기 polling fallback을 적용한다.

### 근거
- Loading, Success, Error 상태를 선언적으로 관리할 수 있다.
- WebSocket 장애 시에도 API polling으로 상태 확인을 지속할 수 있다.

### 결과(긍정)
- 컨테이너 제어 명령 후 대시보드가 자동으로 최신 상태를 반영한다.
- 불필요한 전역 상태 라이브러리 도입을 피한다.

### 결과(부정)
- fallback 상태에서는 주기적인 API 호출이 발생한다.

---

## ADR-4: 에이전트 친화적 문서 및 설정 구조 채택

- 상태: accepted
- 날짜: 2026-06-10
- 결정자: human, AI agent

### 컨텍스트
여러 AI 에이전트가 동시에 또는 번갈아 협업할 때 컨텍스트 오염을 막고 지침을 명확히 할 규칙 시스템이 필요했다.

### 결정
`CLAUDE.md`, `AGENTS.md`, `SKILL.md` 문서와 에이전트별 설정 구조를 저장소 루트에 배치한다.

### 근거
- 에이전트별 worktree와 CodeGraph 싱크를 독립적으로 수행할 수 있다.
- 문서 언어 정책과 DO NOT 규칙을 명시해 기여 정합성을 확보한다.

### 결과(긍정)
- 새로운 세션이 시작될 때 에이전트가 제한 사항을 즉시 인지한다.
- 다중 에이전트 협업 생산성이 높아진다.

### 결과(부정)
- 루트 폴더에 설정용 문서와 JSON 파일이 늘어난다.

---

## ADR-5: Kor Travel/PinVi 계열 PostgreSQL 생명주기를 통합 DB 컨테이너로 관리한다

- 상태: superseded (ADR-37, 2026-08-17; Docker 생명주기 일원화 원칙은 유지)
- 날짜: 2026-06-10
- 결정자: human, AI agent

### 컨텍스트
`pinvi`, `kor-travel-concierge`, `python-krtour-map`, `kor-travel-geo`가 같은 PC에서 PostgreSQL/RustFS 포트를 함께 쓰면서, 각 저장소가 개별적으로 컨테이너를 정지/재시작하면 포트 점유와 credential 기준이 충돌할 수 있다.

### 결정
Kor Travel/PinVi 계열 database는 `kor-travel-geo-postgres` 컨테이너의 `kor_travel_geo`, `pinvi`, `kor_travel_concierge`, `krtour_map` database로 통합하고, Docker 생명주기와 로컬 포트·credential·bucket 기본값은 `kor-travel-docker-manager`가 관리한다.

> 이 기록의 통합 database·`5432` 결정은 ADR-37로 폐기되었다. 현재도 공용 Docker 생명주기와
> credential·bucket의 Manager 소유 원칙만 유효하다.

### 근거
- 공용 인프라의 stop/restart 권한을 한 저장소에 모으면 포트 경합과 중복 컨테이너 제거 위험이 줄어든다.
- PostgreSQL 표준 접속 포트(`localhost:5432`) 하나로 다른 앱 database도 함께 관리할 수 있다.
- manager 대시보드가 통합 DB와 RustFS 상태를 함께 보여 줄 수 있다.

### 결과(긍정)
- 로컬 인프라 구동·정지·재시작 절차가 `kor-travel-docker-manager`로 일원화된다.
- 하위 프로젝트는 애플리케이션/API/UI 실행과 접속 설정에 집중한다.

### 결과(부정)
- 하위 프로젝트만 단독 복제한 환경에서는 인프라를 올리기 전에 `kor-travel-docker-manager` checkout이 필요하다.

---

## ADR-6: BMW M 시각 양식의 인프라 대시보드 수렴 및 react-doctor 최적화

- 상태: superseded (ADR-36, 2026-08-13; React 구조 개선 기록은 유지)
- 날짜: 2026-06-11
- 결정자: human, AI agent

### 컨텍스트
`DESIGN.md` 지침에 따라 전체 프론트엔드의 스타일 테마를 BMW M 브랜드의 시각 원칙으로 일체화해야 했다. 그러나 대시보드가 쇼케이스용 페이지처럼 보이면 관리 UI의 목적성이 흐려질 수 있었다.

### 결정
자동차 배경 이미지를 배제하고, Pure Black 배경, 1px hairline, 직각 panel, 희소한 M 삼색선만 대시보드에 적용한다. `page.tsx`와 `DashboardClient.tsx`를 분리하고 `react-doctor` 경고를 해소한다.

### 근거
- 관리 UI는 사진 중심의 마케팅 페이지가 아니라 dense dashboard 형태가 적합하다.
- 서버/클라이언트 컴포넌트 분리로 Next.js 구조와 성능이 개선된다.

### 결과(긍정)
- 대시보드가 기술적이고 차분한 인프라 제어 센터로 정리된다.
- 접근성 및 렌더링 품질이 개선된다.

### 결과(부정)
- 컴포넌트 파일이 분할되어 구조가 1계층 늘어난다.

---

## ADR-7: Kor Travel/PinVi 전용 Docker Manager 및 Python CLI 채택

- 상태: accepted
- 날짜: 2026-06-12
- 결정자: human, AI agent

### 컨텍스트
사용자는 Portainer와 유사한 Docker 관리 경험을 원하지만, 범용 공개 관리 콘솔이 아니라 Kor Travel/PinVi 개발 및 로컬 운영에 필요한 Docker만 빠르게 확인·실행·수정할 수 있기를 원했다. 또한 다른 Kor Travel/PinVi 개발 저장소에서 의존 Docker가 필요할 때 manager에서 바로 실행할 CLI가 필요했다.

### 결정
앱 관점 target registry를 도입하고, FastAPI API와 Python CLI(`ktdctl`)가 같은 target 정의를 공유한다. target 실행과 개발환경 `--build`는 `docker compose`를 인자 배열로 호출하고, 컨테이너 stats/logs/inspect/action은 Docker SDK를 유지한다.

### 근거
- target 기반 CLI는 `ktdctl main --build`처럼 하위 프로젝트에서 의존 Docker를 쉽게 실행할 수 있다.
- `docker compose up -d --build`는 Docker SDK보다 Compose CLI가 공식 동작에 가깝다.
- 문자열 shell이 아니라 인자 배열로 실행하면 쉘 인젝션과 quoting 문제가 줄어든다.
- Docker SDK inspect는 UI 상세 정보와 secret redaction에 적합하다.

### 결과(긍정)
- UI/API/CLI가 같은 target 모델을 사용한다.
- 개발자가 의존 Docker 실행을 위해 Portainer나 수동 compose 명령을 외울 필요가 줄어든다.
- 상세 inspect API를 통해 Portainer식 상세 화면을 확장할 수 있다.

### 결과(부정)
- Compose CLI와 Docker SDK를 함께 유지하므로 테스트 surface가 늘어난다.

### 후속
- (open) 대시보드 상세 drawer에서 inspect, mounts, networks, redacted env를 연결한다.
- (open) compose 설정 변경 전 diff와 validation을 강화한다.

---

## ADR-8: 짧은 CLI 별칭과 설정 파일 기반 의존 순서를 채택한다

- 상태: accepted
- 날짜: 2026-06-12
- 결정자: human, AI agent

### 컨텍스트
Kor Travel/PinVi 계열 프로젝트가 늘어나면서 개발자가 매번 compose service 이름이나 내부 컨테이너 이름을 기억하는 방식은 실수가 잦다. 사용자는 `db`, `storage`, `geo`, `map`, `ai`, `main` 같은 짧은 별칭으로 필요한 Docker 의존성을 바로 실행하고, 그 의존 순서를 설정 파일로 관리하기를 원했다. 이후 Grafana, cAdvisor, Prometheus를 공용 관측 의존성으로 분리하면서 공식 별칭은 `gra`, `cadv`, `prom`까지 확장되었다. 특히 `kor-travel-geo`는 원천 DB 적재 상태가 흔히 문제의 원인이 되므로 시작 시 검증이 필요했다.

### 결정
공식 의존 순서를 `db -> storage -> gra -> cadv -> prom -> geo -> map -> ai -> main`으로 정하고, `config/docker-targets.yml`을 target alias, 포함 service, 초기화 step의 source of truth로 사용한다. CLI는 `ktdctl db --build`처럼 짧은 별칭을 `ensure`로 해석한다. `gra`, `cadv`, `prom`은 각각 Grafana, cAdvisor, Prometheus를 독립 target으로 실행하고, `geo` target에는 `kor-travel-geo` API/Web UI compose service를 포함한다.

### 근거
- 짧은 별칭은 하위 프로젝트 README와 개발 스크립트에서 사용하기 쉽다.
- 의존 순서를 코드가 아니라 설정 파일에서 읽으면 앱이나 중간 target을 추가할 때 변경 범위가 작다.
- `db`, `storage`, `geo` 초기화 단계를 target에 연결하면 Docker 시작 직후 DB/schema/bucket/원천 적재 상태를 일관되게 확인할 수 있다.
- `geo` 전체 적재는 무겁고 도메인 로더가 책임져야 하므로 manager는 자동 적재 대신 검증과 복구 지침 제공을 담당한다.

### 결과(긍정)
- `ktdctl main --build` 한 번으로 통합 DB, RustFS, Grafana, cAdvisor, Prometheus, `kor-travel-geo` API/Web UI, geo 검증까지 순서대로 수행된다.
- `config/docker-targets.yml`만 수정해 future target과 init step을 확장할 수 있다.
- DB와 RustFS는 idempotent 복구 스크립트로 반복 실행해도 안정적으로 보정된다.

### 결과(부정)
- `geo` 검증이 기본 strict 모드이므로 원천 DB가 비어 있으면 `main` 실행도 실패할 수 있다.
- Grafana, cAdvisor, Prometheus가 `geo` 앞 의존성으로 들어오므로 `geo` 이상 target 실행 시 관측 컨테이너도 함께 올라간다.

### 후속
- (done) `geo` target은 `kor-travel-geo` API/Web UI 컨테이너를 함께 관리한다.
- (open) 실제 앱 컨테이너를 이 compose에서 함께 관리하게 되면 `map`, `ai`, `main` target의 `services`를 확장한다.
- (open) UI에서 target dependency graph와 init step 결과를 보여 준다.

---

## ADR-9: Kor Travel/PinVi 계열 로컬 포트 대역 정책을 일원화한다

- 상태: accepted
- 날짜: 2026-06-12
- 결정자: human, AI agent

### 컨텍스트
Kor Travel/PinVi 계열 레포가 각자 `9001`, `9003`, `9041`, `13082`, `18082` 같은 포트를 독립적으로 사용하면서 포트 충돌과 문서 drift가 반복됐다. 특히 공용 RustFS와 manager 자체 포트는 여러 프로젝트가 동시에 참조하므로, dependency 순서에 따른 예측 가능한 대역 규칙이 필요했다.

### 결정
로컬 host 포트는 `12000`부터 시작하고, `config/docker-targets.yml`의 dependency 순서에 따라 target마다 `100` 단위 대역을 배정한다. API는 대역 `+1`, 추가 서비스 포트는 `+2`, Web UI는 `+5`를 사용한다. PostgreSQL 접속 포트는 표준 `5432` 예외를 유지하고, `kor-travel-docker-manager` 자체 포트는 dependency 변화 방지를 위해 `12900-12999` 대역으로 고정한다.

### 근거
- target 순서와 포트 대역이 1:1로 대응하면 새 서비스 추가 시 충돌 가능성을 사전에 줄일 수 있다.
- Web UI와 API offset을 고정하면 문서·스크립트·프론트엔드 환경변수의 추론 가능성이 높아진다.
- PostgreSQL은 다른 서비스 대역과 별도로 표준 `5432`를 사용해 DB 접속 계약을 단순화한다.
- manager 자체 포트를 dependency 대역 밖에 두면 target 추가로 manager UI/API 포트가 밀리지 않는다.

### 결과(긍정)
- PostgreSQL은 `5432`, RustFS host 포트는 `12101`/`12105`, manager API/Web은 `12901`/`12905`로 일관된다.
- 관련 로컬 레포의 현재 포트와 정책 포트를 `docs/ports.md`에서 비교할 수 있다.
- `config/docker-targets.yml`에 포트 대역 metadata가 남아 CLI/API target 정의와 정책 문서의 연결이 명확해진다.

### 결과(부정)
- 관련 레포의 실제 설정은 각 레포에서 별도 변경해야 하므로 한동안 구 포트와 신 포트 문서가 공존할 수 있다.
- RustFS host 포트가 바뀌므로 기존 `.env`를 쓰는 개발자는 `.env.example`을 참고해 수동 갱신해야 한다.

### 후속
- (open) `pinvi`, `kor-travel-concierge`, `kor-travel-geo`, `python-krtour-map` 레포에서 정책 포트로 설정과 문서를 순차 반영한다.
- (open) CLI에서 `ports` 명령을 추가해 `config/docker-targets.yml`의 포트 정책을 출력한다.

---

## ADR-10: Prometheus, Grafana, Exporter 관측 스택을 별도 컨테이너로 분리한다

- 상태: accepted
- 날짜: 2026-06-13
- 결정자: human, AI agent

### 컨텍스트
Kor Travel/PinVi 공용 인프라가 늘어나면서 Docker 컨테이너 상태뿐 아니라 시간에 따른 리소스 메트릭을 표준 관측 도구로 확인할 필요가 생겼다. 사용자는 Prometheus, Grafana, Exporter를 각각 별도 Docker 컨테이너로 올리고, 이후 Grafana를 다른 앱과도 공통 연계할 수 있도록 단일 관측 target이 아니라 개별 target으로 분리하기를 요청했다.

### 결정
단일 관측 target은 사용하지 않는다. Grafana(`kor-travel-grafana`)는 `gra`, cAdvisor Exporter(`kor-travel-cadvisor`)는 `cadv`, Prometheus(`kor-travel-prometheus`)는 `prom` target으로 분리한다. dependency 순서는 `db -> storage -> gra -> cadv -> prom -> geo -> map -> ai -> main`이며, host 포트는 Grafana `12205`, cAdvisor `12301`, Prometheus `12401`을 사용한다. Compose service는 독립 실행 가능해야 하므로 Grafana와 Prometheus service 사이의 compose `depends_on`은 두지 않는다.

### 근거
- Prometheus, Grafana, Exporter를 target까지 분리하면 수집, 저장, 시각화 책임과 실행 단위가 명확해진다.
- Grafana가 `storage` 바로 다음 공용 target이 되면 다른 앱이 Prometheus 여부와 무관하게 같은 Grafana 포트 계약을 사용할 수 있다.
- `gra`, `cadv`, `prom`은 기존 `+1`, `+5` offset 규칙을 그대로 적용한다.
- cAdvisor는 Docker 컨테이너 CPU, memory, filesystem, network 메트릭을 Prometheus 형식으로 노출하므로 현재 관리 대상과 잘 맞는다.
- Grafana provisioning으로 Prometheus datasource를 자동 등록하면 수동 초기 설정을 줄일 수 있다.

### 결과(긍정)
- `ktdctl gra`, `ktdctl cadv`, `ktdctl prom`으로 관측 도구를 필요한 단위에서 실행할 수 있다.
- Grafana는 `http://127.0.0.1:12205`, cAdvisor는 `http://127.0.0.1:12301`, Prometheus는 `http://127.0.0.1:12401`에서 접근할 수 있다.
- `geo` 이상 target과 `all` target에 관측 도구가 포함되어 전체 로컬 인프라 구동 시 함께 올라간다.

### 결과(부정)
- cAdvisor는 read-only Docker socket·`/sys` mount와 일부 권한이 필요하며, host root filesystem/disk inventory는 수집하지 않는다.
- Grafana 기본 admin password는 `.env`에서 반드시 운영자별 값으로 교체해야 한다.

### 후속
- (open) Grafana dashboard JSON provisioning을 추가해 Kor Travel/PinVi 컨테이너 리소스 화면을 자동 구성한다.
- (open) FastAPI의 자체 `/metrics` 노출 여부를 검토해 Prometheus scrape target에 추가한다.

---

## ADR-11: Kor Travel Geo와 Kor Travel Concierge 공식명을 채택하고 과거 이름 수용 계층을 제거한다

- 상태: accepted
- 날짜: 2026-06-13
- 결정자: human, AI agent

### 컨텍스트
과거 geo 계열 GitHub 레포와 프로젝트명은 `kor-travel-geo`로, 과거 AI agent 계열 GitHub 레포와 프로젝트명은 `kor-travel-concierge`로 변경되었다. 사용자는 호환 목적 코드를 남기지 말고 새 공식명을 기준으로 정리하기를 원했다.

### 결정
문서, display name, 공식 CLI alias, 포트 표, task 명칭은 `kor-travel-geo`, `kor-travel-concierge`를 기준으로 정리한다. 과거 프로젝트명 기반 alias와 fallback env는 제거한다. target을 중복 하드코딩하던 보조 shell script도 제거하고, 새 입력값은 `KOR_TRAVEL_GEO_*`, `KOR_TRAVEL_CONCIERGE_*` 계열만 사용한다.

### 근거
- 과거 이름 alias를 유지하면 새 프로젝트명 전환 이후에도 문서와 자동화에 과거 이름이 계속 전파된다.
- 공식명과 설정명이 1:1로 대응해야 하위 프로젝트에서 어떤 target을 호출해야 하는지 명확하다.
- local checkout과 `.env`도 새 이름 기준으로 맞추는 것이 장기 유지보수 비용을 줄인다.

### 결과(긍정)
- `ktdctl kor-travel-geo`와 `ktdctl kor-travel-concierge`를 공식 alias로 사용할 수 있다.
- 문서와 UI display name은 새 프로젝트명 기준으로 정리된다.
- 과거 이름 alias와 fallback env가 제거되어 설정 drift가 줄어든다.
- target 실행 경로가 Python CLI와 API registry로 단일화되어 중복 하드코딩이 사라진다.

### 결과(부정)
- `/mnt/f/dev/kor-travel-geo` checkout에 Dockerfile과 UI 디렉터리가 없으면 `ktdctl geo --build`가 실패한다.

### 후속
- (open) `kor-travel-geo` checkout에 Dockerfile과 UI 디렉터리 이전이 완료되어야 한다.
- (done) Docker container/service 이름과 물리 데이터 디렉터리를 `kor-travel-geo` 기준으로 맞춘다.

---

## ADR-12: 프로젝트명을 Kor Travel Docker Manager로 변경하고 CLI를 `ktdctl`로 전환한다

- 상태: accepted
- 날짜: 2026-06-13
- 결정자: human, AI agent

### 컨텍스트
사용자는 본 Docker 관리 도구의 프로젝트명과 GitHub 저장소명을 `kor-travel-docker-manager`로 변경하고, CLI 명령 이름을 `ktdctl`로 바꾸기를 요청했다. 기존 이름과 CLI를 함께 유지하면 설치 문서와 자동화가 다시 분기되므로 단일 이름만 유지해야 했다.

### 결정
저장소, Python package, Poetry package, 프론트엔드 package, 문서의 공식 프로젝트명을 `kor-travel-docker-manager` / `Kor Travel Docker Manager`로 전환한다. Python console script는 `ktdctl`만 제공하고, backend import package는 `kor_travel_docker_manager`로 변경한다. Docker Compose project name도 `kor-travel-docker-manager`로 고정한다.

### 근거
- GitHub 저장소명, 설치 명령, Python import path, CLI binary가 같은 naming family를 사용해야 문서 drift가 줄어든다.
- `ktdctl`은 Kor Travel Docker Manager의 짧은 제어 명령으로 하위 프로젝트 스크립트에서 사용하기 쉽다.
- compose project name을 고정하면 로컬 checkout 폴더명과 무관하게 Docker network prefix가 일정하다.

### 결과(긍정)
- 새 설치 문서는 `poetry run ktdctl ...` 기준으로 통일된다.
- `kor_travel_docker_manager.main:app`이 백엔드 공식 ASGI entrypoint가 된다.
- GitHub 저장소명을 바꿔도 코드와 문서가 같은 이름을 사용한다.

### 결과(부정)
- 기존 로컬 checkout 경로나 이전 CLI 이름을 쓰는 외부 스크립트는 새 이름으로 수정해야 한다.

### 후속
- (open) GitHub 저장소 rename 후 원격 URL과 로컬 checkout 경로를 새 이름으로 맞춘다.

---

## ADR-13: Kor Travel Geo DB명, 컨테이너명, 데이터 경로를 현재 앱 기준으로 맞춘다

- 상태: accepted
- 날짜: 2026-06-13
- 결정자: human, AI agent

### 컨텍스트
`kor-travel-geo` 앱은 현재 Python 패키지와 설정 prefix를 `kortravelgeo` / `KTG_*`로 전환했고, 기본 PostgreSQL DB 이름도 `kor_travel_geo`로 고정했다. 그러나 manager compose에는 이전 geo 이름 계열의 DB명, 환경변수, Docker service/container 이름, 물리 데이터 디렉터리 경로가 남아 있어, API 컨테이너가 현재 앱 설정을 읽지 못하거나 잘못된 DB와 저장소 경로를 사용할 위험이 있었다.

### 결정
Kor Travel Geo의 DB명은 `kor_travel_geo`로, manager override 변수는 `KOR_TRAVEL_GEO_*`로, API/UI 컨테이너 내부 환경변수는 앱이 읽는 `KTG_*`로 맞춘다. Docker service/container 이름은 `kor-travel-geo-*`로 맞추고, 물리 데이터 디렉터리는 `/home/digitie/kor-travel-geo-data`로 이동한다. RustFS bucket 기본값도 `kor-travel-geo`로 맞춘다. Prometheus는 `kor-travel-geo-api:12501/metrics`와 `kor-travel-geo-ui:12505/api/metrics`를 pull 방식으로 scrape한다.

### 근거
- DB명과 앱 환경변수는 실제 `kor-travel-geo` 런타임 계약과 일치해야 한다.
- compose service/container 이름, 대시보드 registry, 테스트 fixture가 같은 식별자를 써야 운영 화면과 실제 Docker 대상이 어긋나지 않는다.
- bind mount 경로는 기존 컨테이너 생성 시점에 고정되므로 물리 디렉터리 이동 전 관련 컨테이너를 내리고 제거한 뒤 새 compose 기준으로 재생성해야 한다.
- Prometheus는 앱이 능동 연결하지 않고 `/metrics`를 scraper가 가져가는 표준 pull 구조가 맞다.

### 결과(긍정)
- `ktdctl geo --build`로 올라간 API 컨테이너가 `KTG_PG_DSN=postgresql+psycopg://addr:addr@kor-travel-geo-postgres:5432/kor_travel_geo`를 받는다.
- `geo` 검증과 DB 복구 스크립트의 기본 검사 DB가 `kor_travel_geo`로 맞는다.
- PostgreSQL, RustFS, Prometheus, Grafana bind mount가 `/home/digitie/kor-travel-geo-data` 아래로 통일된다.
- Prometheus 관측 스택이 `kor-travel-geo` API와 admin UI 자체 성능 메트릭을 수집할 수 있다.

### 결과(부정)
- 기존 `.env`에 이전 geo 이름 계열 변수를 직접 넣어 둔 개발자는 `.env.example`을 기준으로 `KOR_TRAVEL_GEO_*` 이름으로 갱신해야 한다.
- 기존 컨테이너를 제거하고 새 compose 기준으로 재생성해야 하므로 전환 시점에 짧은 로컬 중단이 발생한다.

---

## ADR-14: 앱 target 흐름을 `geo -> conc -> map -> pinvi`로 재정렬하고 실제 앱 컨테이너를 manager에서 빌드한다

- 상태: accepted
- 날짜: 2026-06-13
- 결정자: human, AI agent

### 컨텍스트
기존 target registry는 `geo -> map -> ai -> main` 순서를 사용했고, `map`, `ai`, `main`은 실제 앱 컨테이너 없이 선행 공용 인프라만 실행하는 placeholder 성격이었다. 사용자는 `map` target이 실제 `kor-travel-map` Docker 이미지를 빌드하고 실행해야 하며, 앱 흐름은 `kor-travel-geo` 다음 `kor-travel-concierge`, 그 다음 `kor-travel-map`, 그 다음 PinVi라고 명확히 지정했다.

### 결정
공식 dependency 순서를 `db -> storage -> gra -> cadv -> prom -> geo -> conc -> map -> pinvi`로 변경한다. `conc`는 `kor-travel-concierge`, `map`은 `kor-travel-map`, `pinvi`는 PinVi 앱 컨테이너를 실제 compose service로 빌드하고 실행한다. `srv`와 `main`은 `pinvi` target의 별칭으로 둔다.

### 근거
- 사용자가 지정한 서비스 흐름과 CLI 별칭(`conc`, `srv`)을 source of truth에 반영해야 한다.
- `map` target이 실제 앱을 빌드하지 않으면 `ktdctl map --build`라는 명령 의미와 실행 결과가 어긋난다.
- `main`을 독립 target으로 유지하면 PinVi를 추가하는 순간 `12900` 대역과 manager 자체 포트가 충돌하므로, PinVi를 `12800-12899` 대역의 마지막 앱 target으로 둔다.
- registry alias 기반 직접 실행으로 바꾸면 새 target alias를 추가할 때 CLI 고정 목록을 다시 수정하지 않아도 된다.

### 결과(긍정)
- `ktdctl conc --build`는 `kor-travel-concierge` API/MCP/Scheduler/Web UI를 빌드하고 실행한다.
- `ktdctl map --build`는 `geo`, `conc` 이후 `kor-travel-map` API/Dagster/Web UI까지 빌드하고 실행한다.
- `ktdctl srv --build` 또는 기존 `ktdctl main --build`는 PinVi API/Web UI까지 실행한다.
- 공용 DB 복구가 `krtour_map_dagster` database까지 보정하고, RustFS 복구가 `kor-travel-concierge` bucket까지 보정한다.

### 결과(부정)
- 기존 `ai` target은 제거되므로 해당 별칭을 쓰던 스크립트는 `conc`로 바꿔야 한다.
- `kor-travel-map` 포트가 `126xx`에서 `127xx`로 이동하고, `kor-travel-concierge`가 `126xx` 대역을 사용한다.
- `ktdctl map --build`가 실제 앱 이미지들을 빌드하므로 이전보다 실행 시간이 길어진다.

### 후속
- (open) 대시보드에서 `conc`, `map`, `pinvi` target의 dependency graph와 init step 결과를 표시한다.
- (open) Prometheus scrape target에 새 앱의 `/metrics` 표면이 안정화되면 추가한다.

---

## ADR-15: 운영(prod) 공개 주소를 gitignore된 `.env`/`.env.production`로 주입하고 CORS Origin을 환경변수화한다

- 상태: accepted
- 날짜: 2026-06-20
- 결정자: human, AI agent

### 컨텍스트
매니저 백엔드 API와 대시보드는 운영 환경에서 각각 별도 공개 도메인으로 노출된다. 프론트엔드는 `NEXT_PUBLIC_BACKEND_URL` 빌드타임 변수로 백엔드 주소를 알아야 하고(기본값 `http://localhost:12901`), 백엔드 CORS는 `allow_origins=["*"]`로 고정되어 있었다. 운영 도메인을 소스에 하드코딩하면 공개 저장소에 노출되고, 환경별 분기도 어렵다. 또한 Next.js 환경파일 우선순위상 `.env.local`이 `.env.production`을 덮어써, 기존 개발용 `.env.local`이 운영 빌드를 localhost로 고정시키는 사고 위험이 있었다.

### 결정
운영 공개 주소를 소스/문서에 커밋하지 않고 gitignore된 `.env`(백엔드)와 `frontend/.env.production`(프론트엔드)에만 저장한다. 백엔드 CORS 허용 Origin은 `KTDM_CORS_ALLOW_ORIGINS` 환경변수(콤마 구분, 미설정/`*`이면 전체 허용)로 제어하고, 프론트엔드는 환경별 `.env.development`/`.env.production`로 백엔드 주소를 분리하며 `.env.local`에는 `NEXT_PUBLIC_BACKEND_URL`을 두지 않는다.

### 근거
- 운영 도메인은 dynamic DNS 기반 사설 주소라 공개 저장소에 노출하지 않아야 한다.
- `NEXT_PUBLIC_*`은 빌드타임에 번들로 인라인되므로 운영 호스트 빌드에서 `.env.production` 값이 들어가야 한다.
- 백엔드는 `python-dotenv`로 루트 `.env`를 기동 시 로드하므로 동일 파일에서 CORS를 제어할 수 있다.
- 개발 기본값을 `*`/`localhost`로 유지하면 기존 로컬 워크플로가 깨지지 않는다.

### 결과(긍정)
- 운영 도메인 변경 시 코드 수정 없이 gitignore된 env 파일만 갱신하면 된다.
- 백엔드 CORS를 운영 대시보드 Origin으로 좁혀 노출 표면을 줄일 수 있다.
- `.env.example`/`frontend/.env.example`이 플레이스홀더로 계약을 문서화해 실제 도메인 누출 없이 셋업을 안내한다.

### 결과(부정)
- `NEXT_PUBLIC_*` 인라인 특성상 운영 호스트에서 `next build`를 다시 수행해야 주소가 반영된다.
- 운영 도메인이 env 파일에만 존재하므로 배포 자동화에서 해당 파일 주입을 별도로 보장해야 한다.

### 후속
- (open) 리버스 프록시(TLS/WS 업그레이드) 설정 예시를 운영 배포 문서로 분리한다.
- (open) 인증 도입 시 `allow_credentials`와 Origin 화이트리스트를 함께 재검토한다.

---

## ADR-16: dev 기본 네트워크를 Docker host 모드로 전환하고 컨테이너=호스트 포트로 통일한다

- 상태: accepted
- 날짜: 2026-06-20
- 결정자: human, AI agent

### 컨텍스트
기존 compose는 bridge 네트워크 + 포트 NAT(`host:container`) 매핑을 사용했고, 서비스 간 통신은 compose DNS 컨테이너명(`kor-travel-geo-postgres`, `rustfs` 등)에 의존했다. 사용자는 dev 기본 네트워크를 host 모드로 통일하고, `kor-travel-geo`/`kor-travel-concierge`/`kor-travel-map`/PinVi의 컨테이너 내부 포트도 호스트 포트와 동일하게 맞출 것을 요청했다. host 모드에서는 포트 NAT가 없어 컨테이너가 바인딩한 포트가 곧 호스트 포트이므로, 정규 포트(12xxx, 5432) 직접 바인딩과 컨테이너=호스트 포트 통일이 필수다. 또한 관리 대상 서비스별 운영 공개 주소를 대시보드에 반영하고, PinVi Dagster(`pinvi-dagster`, 12802)를 새 관리 컨테이너로 추가해야 했다.

### 결정
dev 기본 네트워크 모드를 `network_mode: ${KTDM_DOCKER_NETWORK_MODE:-host}`로 전환한다. 모든 인프라/앱 서비스는 호스트 정규 포트에 직접 바인딩하고(컨테이너 내부 포트 = 호스트 포트), 서비스 간 참조는 `127.0.0.1:<포트>`를 사용한다. 관리 서비스의 운영 공개 URL은 docker-targets.yml의 `prod_url_env`가 가리키는 `KTDM_PROD_URL_*` 환경변수에서 읽어 대시보드 `public_url`로 표시한다(실제 도메인은 gitignore된 `.env`에만 저장). `pinvi-dagster`(12802)를 신규 관리 컨테이너로 등록하고 PinVi 저장소에 `apps/etl/Dockerfile`을 추가한다.

### 근거
- host 모드에서는 NAT가 없으므로 컨테이너=호스트 포트 통일이 동작의 전제 조건이다(사용자 요청과 일치).
- 정규 포트를 직접 바인딩하면 `docs/ports.md` 포트 정책과 컨테이너 내부 포트가 일치해 추적이 단순해진다.
- 서비스 간 참조를 `127.0.0.1`로 두면 host 네트워크에서 일관되게 동작한다.
- prod 공개 주소를 env로 주입하면 도메인을 저장소에 노출하지 않고 대시보드에 반영할 수 있다([[ADR-15]] 패턴 재사용).

### 결과(긍정)
- `docker compose config`가 모든 서비스 `network_mode: host`와 host=container 포트로 검증된다.
- 대시보드가 컨테이너별 운영 공개 URL을 표시한다.
- `pinvi` target이 API/Dagster/Web을 모두 포함한다.

### 결과(부정)
- host 모드에서 `ports:` 매핑은 무시되며, 매니저 대시보드의 실시간 PortBindings 표시는 `expected_ports`(compose 선언 포트)에 의존한다.
- `KTDM_DOCKER_NETWORK_MODE=bridge`로 되돌리면 `127.0.0.1` 참조가 동작하지 않으므로 서비스 간 hostname을 컨테이너명으로 수동 복원해야 한다.
- host 모드는 Docker 엔진의 host networking 지원에 의존하며(WSL2 native docker에서는 동작, Docker Desktop은 버전/설정에 따라 제약), 런타임 검증이 필요하다.
- PinVi `apps/etl` ETL 모듈은 미완(Sprint 1 stub)이라 `pinvi-dagster` webserver 기동은 upstream 모듈 완성에 따라 달라질 수 있다.

### 후속
- (open) host 모드 런타임 기동 검증 후 대시보드 PortBindings 표시 전략을 보완한다.
- (open) PinVi ETL 모듈이 완성되면 `pinvi-dagster` 헬스체크/스케줄러(daemon) 편입을 검토한다.

---

## ADR-17: 프론트엔드를 Tailwind v4 + StyleSeed 라이트 토큰으로 전환하고 전역 오류 복구 boundary를 추가한다

- 상태: superseded (ADR-36, 2026-08-13; Tailwind v4·오류 복구 boundary 기록은 유지)
- 날짜: 2026-06-20
- 결정자: human, AI agent

### 컨텍스트
매니저 대시보드는 BMW M Pure Black(다크) 양식([[DESIGN.md]], Tailwind v3 커스텀 토큰)을 사용했다. 사용자는 (1) `kor-travel-geo`의 글로벌 오류 복구 UI(PR #391: App Router error/global-error boundary, chunk/RSC/network 오류 1회 hard reload)를 매니저에도 똑같이 반영하고, (2) `kor-travel-geo-ui`의 `DESIGN-RULES.md`(StyleSeed 기반 운영 콘솔 규칙)를 매니저에 반영하며, (3) Tailwind v4로 전환할 것을 요청했다. geo 규칙은 순수 검정 금지·단일 accent·surface 토큰·약한 그림자를 강조해 기존 Pure Black 정체성과 충돌했고, 사용자는 전면 리스타일(StyleSeed화)을 선택했다.

### 결정
프론트엔드 빌드를 Tailwind v4(CSS-first `@theme`, `@tailwindcss/postcss`)로 전환하고, `globals.css`의 `@theme`에 StyleSeed 라이트 토큰(surface/text 5단계/brand teal/status/shadow/motion)을 정의한다. `DashboardClient`와 오류 복구 패널을 이 토큰으로 전면 리스타일한다. App Router `app/error.tsx`/`app/global-error.tsx`와 `AppErrorPanel`, `lib/error-recovery.ts`를 추가해 Next 기본 영어 오류 화면 대신 한국어 복구 패널을 보여 주고, chunk/RSC/network 계열 오류는 pathname당 1회 hard reload를 시도한다. `docs/DESIGN-RULES.md`를 매니저용으로 포팅하고 `DESIGN.md`(BMW M)는 superseded 기록으로 보존한다.

### 근거
- 사용자가 geo 규칙으로의 통일과 Tailwind v4 전환을 명시했다.
- v4 CSS-first `@theme`는 semantic 토큰을 단일 소스로 관리하기 쉽다.
- 오류 복구 boundary는 Firefox 포함 브라우저에서 Next 기본 오류 화면 노출을 막는다(geo와 동일 패턴).

### 결과(긍정)
- 대시보드가 라이트 StyleSeed 콘솔 룩(단일 brand accent, 약한 그림자, 44px 터치타깃, 상태 dot+text)으로 통일된다.
- 런타임 오류 시 한국어 복구 패널과 자동 1회 reload로 사용자 경험이 개선된다.
- 토큰이 `@theme` 한 곳에 모여 새 UI가 hardcoded hex 없이 작성된다.

### 결과(부정)
- BMW M Pure Black 시각 정체성이 제거된다(`DESIGN.md`는 기록 보존).
- Tailwind v4는 v3 대비 유틸리티/설정이 달라 향후 의존 패키지/플러그인 호환성 확인이 필요하다.
- geo가 참조하는 shadcn primitive 구조는 매니저에 도입하지 않고 토큰 직접 적용 방식을 유지했다.

### 후속
- (open) 모달/표 컴포넌트를 재사용 primitive로 분리해 DESIGN-RULES 적용을 더 일관화한다.
- (open) Tailwind v4 전환에 따른 시각 회귀를 Playwright 스냅샷으로 보강한다.

---

## ADR-18: target 의존을 선형 누적에서 `depends_on` DAG로 전환한다 (concierge는 geo 비의존)

- 상태: accepted
- 날짜: 2026-06-20
- 결정자: human, AI agent

### 컨텍스트
기존 target 의존은 `dependency_order` 선형 리스트의 슬라이스(`order[:index+1]`)로 표현돼, `ktdctl <target>`이 항상 그 target 앞의 모든 target을 누적 실행했다. 이 모델에서는 `geo -> conc` 순서 때문에 concierge가 geo에 의존하게 된다. 그러나 실제 아키텍처에서 `kor-travel-concierge`는 geo에 의존하지 않으며, `kor-travel-map`이 geo와 concierge 모두에 의존한다. 사용자는 "concierge는 geo 비의존, prometheus 다음 geo·conc 분기 후 map(geo+conc 의존)·pinvi"로 의존 그래프를 재설정할 것을 요청했다.

### 결정
각 target에 `depends_on`을 선언해 의존을 **DAG**로 표현한다. `target_sequence_for_target`은 선형 슬라이스 대신 transitive `depends_on` 폐포를 `dependency_order`(유효한 위상정렬) 순으로 정렬해 반환한다. 그래프: `db -> storage -> gra -> cadv -> prom`, 그 다음 `geo`와 `conc`가 각각 `prom`에만 의존(상호 독립), `map`은 `[geo, conc]`, `pinvi`는 `[map]`에 의존한다. docker-compose의 service `depends_on`도 정렬: concierge-api에서 geo-api 의존 제거, map-api에 geo-api 의존 추가.

### 근거
- concierge는 geo 없이 독립 기동·운영되어야 한다(실아키텍처 반영). `ktdctl conc`가 geo를 끌어오지 않는다.
- map은 지오코딩(geo)과 provider(concierge) 모두에 의존하므로 둘을 명시적 부모로 둔다.
- `dependency_order`가 DAG의 유효한 linearization이라 폐포를 그 순서로 정렬하면 부모가 항상 먼저 오는 결정적 순서가 보장된다.

### 결과(긍정)
- `ktdctl conc` = `db, storage, gra, cadv, prom, conc`(geo 제외). `ktdctl map` = `… geo, conc, map`. `ktdctl pinvi` = 전체.
- 새 의존성은 `targets.<id>.depends_on` 한 줄로 선언한다.

### 결과(부정)
- `dependency_order`는 여전히 표시/정렬용 linearization으로 남아 DAG와 이중 관리된다(유효성 전제).

### 후속
- (open) `depends_on` 사이클/유효성 검증을 로드 시 추가한다.

---

## ADR-19: 관리자 인증과 공개 API 키 관리를 `kor-travel-geo` PR #399 패턴으로 맞춘다

- 상태: accepted
- 날짜: 2026-06-23
- 결정자: human, AI agent

### 컨텍스트

Manager 대시보드는 Docker 컨테이너 시작·정지·설정 변경 API를 직접 호출한다. 기존에는 로컬 개발 편의를 위해 별도 인증이 없었지만, 운영 공개 주소와 CORS 설정이 들어온 뒤에는 관리자 화면과 API를 보호해야 한다. 사용자는 단일 관리자 계정과 비밀번호 해시 기반 인증을 요구했고, 비밀번호와 API 키 원문은 git에 노출하지 않아야 했다. 또한 `kor-travel-geo` PR #399의 로그인·API 키 UX와 보안 패턴을 따르고, `kor-travel-geo` v2 API도 같은 VWorld 키 계약을 사용할 수 있어야 했다.

### 결정

단일 관리자 로그인은 프론트엔드 Origin 제한, PBKDF2 비밀번호 해시, HMAC 서명 `httpOnly` 세션 쿠키, DB 저장 세션 해시를 함께 사용한다. 로그인·로그아웃·실패 시도·API 키 생성/폐기는 DB 감사 로그로 남긴다. 공개 API 키는 VWorld 호환 32자리 영문/숫자 문자열로 생성하되, 원문은 생성 응답에서 1회만 보여 주고 DB에는 SHA-256 해시와 끝 6자리 힌트만 저장한다. 활성 키 해시는 짧은 TTL 메모리 캐시로 읽고 생성·폐기 시 무효화한다. 로그인된 신뢰 UI 요청은 외부 공개 API의 key 검증을 생략할 수 있는 공통 dependency를 제공한다. `X-Forwarded-*` 계열 헤더는 `KTDM_TRUSTED_PROXY_CIDRS`에 포함된 직접 peer에서 온 요청일 때만 감사 로그·rate-limit·secure cookie 판단에 사용한다.

2026-08-18 보강: 여기서 “VWorld 호환”은 토큰 문자집합과 길이만 뜻한다. VWorld provider
credential은 Manager 공개 API나 Map→Geo consumer 인증을 대체하지 않는다. Manager 공개
API는 DB에 active로 등록한 Manager 전용 key만 받는다. Map UI는 Geo가 Map consumer에
발급한 root `KOR_TRAVEL_MAP_KOR_TRAVEL_GEO_API_KEY`를 server-only
`KOR_TRAVEL_GEO_API_KEY`로만 받아 `/api/geo` BFF에서 사용하며 브라우저 build/query에
위임하지 않는다. C6c raw/resolved/runtime 계약은 이 alias와 원본을 정확한 Map 서비스
밖에서 거부한다.

### 근거

- 관리자 API는 같은 PC의 대시보드에서만 호출해야 하므로 세션 검증과 Origin 허용 목록을 함께 적용한다.
- `X-Forwarded-*`는 클라이언트가 위조할 수 있으므로 신뢰 프록시 CIDR을 통과한 요청에서만 반영한다.
- 세션 쿠키만 믿지 않고 DB의 세션 해시 상태도 조회하면 로그아웃·재로그인·폐기 처리가 서버 측에서 가능하다.
- API 키 원문을 저장하지 않으면 DB 유출 시 직접 재사용 위험이 줄어든다.
- 키 검증은 외부 공개 API에서 자주 호출될 수 있어 활성 해시 목록을 짧게 캐시하되, 관리 UI에서 키 상태가 바뀌면 즉시 무효화한다.
- `kor-travel-geo` PR #399와 같은 env 계약을 compose에 반영하면 `kor-travel-geo` v2 공개 API와 관리자 UI가 같은 키 운용 방식을 쓸 수 있다.

### 결과(긍정)

- 대시보드 최초 진입 시 로그인 화면이 표시되고, 보호 API와 WebSocket은 유효한 세션이 있어야 접근된다.
- 관리자 UI에서 최근 로그인 감사 기록과 공개 API 키 목록·생성·폐기를 확인할 수 있다.
- `.env.example`에는 변수 이름과 예시만 있고, 실제 비밀번호 해시·세션 secret·proxy secret은 gitignore된 `.env`에만 남는다.
- 공개 API 키 검증 로직을 새 외부 API endpoint에 dependency로 붙여 재사용할 수 있다.

### 결과(부정)

- 기존 인증 없는 API 호출 스크립트는 관리자 세션 또는 별도 공개 API 키 검증 경로에 맞춰 수정해야 한다.
- 세션과 감사 로그 테이블은 현재 `Base.metadata.create_all` 기반으로 생성되며, 별도 마이그레이션 체계가 생기면 DDL 관리로 옮겨야 한다.
- 현재 감사 로그는 IP와 User-Agent를 해시로 저장하므로 원문 기반 장애 분석은 할 수 없다.

### 후속

- (open) 공개 API surface가 실제로 추가될 때 `require_public_api_key` dependency를 붙이고 key 생략 허용 조건을 endpoint별로 검토한다.
- (open) 운영 프록시 배치가 확정되면 `KTDM_FRONTEND_ORIGINS`, `KTDM_CORS_ALLOW_ORIGINS`, `KTG_ADMIN_PROXY_SECRET` 값을 배포 런북에 비공개로 연결한다.

---

## ADR-20: Map↔PinVi ops principal을 API 전용 read/cancel capability로 배포한다

- 상태: accepted (ops principal) / compatible-pair 실행 부분 superseded (application `300`)
- 날짜: 2026-07-18
- 결정자: human, Codex

### 현행성 경계(ADR-21 대체)

ADR-20의 service principal·권한 분리 결정은 유지한다. 다만 compatible-pair의 manifest
version·image 범위·전환·복구·halt 절차는 application `300`의 seven-service v6 generation과 v8
rebuild journal이 대체한다. 따라서 이 ADR에 남은 v4 exact 9-field pair와 다섯 runtime transaction
서술은 역사 기록이며 실행·복사·프로비저닝 지침이 아니다. 현재 운영 판단에는 v6/v8 authority와
`docs/docker-management.md` §7.5만 사용한다.

### 컨텍스트

Map의 legacy ops endpoint 제거 뒤 PinVi가 canonical datasets/pipeline REST API를 사용하려면 브라우저
BFF secret과 분리된 서버 간 인증이 필요하다. PinVi가 필요한 mutation은 import-job 취소 하나뿐인데
일반 write token을 주면 schedule command, refresh policy, update request까지 불필요하게 열리고, root
`.env`를 모든 Map 컨테이너가 읽게 하면 Dagster와 UI에도 secret이 퍼진다.

### 결정

manager의 gitignore된 `.env`에 Map API용 read token과 cancel token을 두고, 두 값을 Map API와 PinVi
API에만 서로 대응하는 환경변수로 주입한다. read token은 canonical ops GET, cancel token은 exact
import-job cancel endpoint에만 결박한다. 두 token은 32자 이상·모든 공백 문자 없음·상호 다름을 요구한다.
운영은 `KTDM_DEPLOYMENT_ENVIRONMENT=production`, `PINVI_ENVIRONMENT=production`,
`KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED=true`를 함께 명시하고, local은 세 값을 각각
`local`, `development`, `false`로 명시한다. local token opt-out은 두 값이 모두 빈 경우에만 허용하고 한쪽만
있거나 약한 pair는 거부한다. 고정된 단일 PinVi service principal에는 DB credential 테이블을 추가하지
않는다.

production Map runtime/PinVi API mutation은 일반 `ensure`, container action/config/reset, direct Compose API
service 변경에서 중앙 차단한다. Compose 명령은 알려진 read-only 명령만 무변경으로 분류하고,
`scale`·`watch`와 알 수 없는 명령은 보수적으로 mutation으로 분류한다. 모든 runtime-set mutation 진입점은
mode/required/token pair를 같은 fail-close guard로 검사한다. 배포·bootstrap capture·rollback은
deployment-wide filesystem lock을 잡고 전용 capability로 실행한다. 이 lock은 Compose, Docker SDK,
compose 설정 파일 변경을 포함한 모든 manager mutation이 공유한다. manifest와 lock은 checkout이 아니라
`~/.local/state/kor-travel-docker-manager/<COMPOSE_PROJECT_NAME>/` 아래의 고정 파일명에 함께 둔다.
production은 state root·manifest·lock override를 모두 거부해 동일 project가 별도 lock으로 갈라지는 것을
막는다. manifest version/시간 형식을 엄격히 검증하고 parent fsync 실패는 이전 snapshot 복원 또는 확인된
rename commit 중 하나로만 종결한다.
production compatible-pair의 Map host bind는 `12701`, PinVi Map base URL은
`http://127.0.0.1:12701`로 고정하고 서로 일치하는 다른 포트도 mutation 전에 거부한다. canonical smoke는
Map `execution_coverage`와 dataset row nested DTO, PinVi ETL definition 배열의 실제 원소 DTO를 검사한다.
비표준 포트는 local/development에서만 두 값을 함께 맞춰 사용할 수 있다.
Map capability 음성 smoke는 tokenless/cross-token/non-cancel 요청별 HTTP status와 RFC7807 code를 함께
검증한다. PinVi destructive cancel probe는 transaction state로 1회만 수행하고 검증된 첫 결과를 final
verification/recovery에 재사용한다. 이미 dispatch했지만 결과가 불확실하면 동일 fixture에 재요청하지 않는다.
cancel detail은 actual attempt/member/Dagster run DTO를 깊게 검사하고 attempt 전 root-only shape는 canonical
409 `PIPELINE_CANCELLATION_IN_PROGRESS`에만 허용한다.
full 409은 unresolved count 0, resolved root+unresolved child, transient all-resolved를 canonical topology로
허용한다. retryable failure는 failed member와 matching run 모두 exact run-backed `cancel_failed` 및 retryable
error를 요구하고, in-progress/definitive CAS drift의 member `cancel_failed`+run `cancelled` 전이는 별도로
허용한다. actual HTTP pair인 `409 PIPELINE_CANCELLATION_UNSAFE`+`failed`와
`503 DAGSTER_TERMINATION_TIMEOUT`+`retryable`도 exact status/error 조건으로 고정한다.
failed attempt 내부의 retryable run-backed 증거와 definitive mismatch 증거 혼재는 actual DB invariant대로
허용한다. status/error/finished timestamp, retry lineage, frozen termination flag, engine timestamp를 깊게
검증하고, `Retry-After`의 존재와 파싱 성공을 분리한다. Compose mutation option을 완전하게 해석하지 못하면
명시 서비스가 보이더라도 다섯 runtime scope로 default-deny한다.
Compose option은 command별 의미를 적용하고 `config -o/--output`을 write-capable mutation으로 분류한다.
cancel 결과는 in-progress runless definitive error, run-backed policy group, resolved terminal mapping을
Map/PinVi와 동일하게 검증하며 feature-load failed/SUCCESS 예외는 동일 run child 추적 증거를 요구한다.
contract generation은 manager-only 값으로 모든 container 주입을 금지하고 bootstrap cleanup 예외는
operator-required 상태로 수렴한다.
`Retry-After`는 ASCII decimal 1..300으로 한정한다. generic non-API config update/reset/create도 candidate
compose 전체와 `env_file`의 보호 이름·현재 값을 파일 쓰기 전에 검사하며, exact API interpolation 외
참조는 typed conflict로 거부하고 compose 파일과 container를 변경하지 않는다.
candidate 검사는 raw/resolved 전체 graph와 top-level secret/config 외부 파일까지 포함한다. API raw wiring은
suffix를 포함한 canonical 문자열만 허용하고 Compose 경로 보간을 완전히 해석할 수 없으면 거부한다. 이 검사는
generic ensure/up/create/recreate와 config 저장보다 먼저 수행한다.
bind mount도 raw/resolved source를 canonical path로 비교하며 root `.env`, manager state 파일, 보호값 포함
regular file과 Windows-looking source를 거부한다. named volume은 host path와 분리하고, 내용 확인 불가능한
external secret/config reference는 비어 있는 exact allowlist 밖에서 fail-close한다.
directory bind는 manager 파일 ancestor와 host root를 먼저 거부한 뒤 서비스별 canonical source/target
allowlist만 허용한다. 존재하지 않는 source는 Docker가 directory로 자동 생성하기 전에 거부한다. cAdvisor는
host root·Docker data bind를 제거하고 Docker socket+`/sys`의 container-only 관측만 수행한다.
config API에서는 persisted compose의 top-level/service volume graph 전체를 exact immutable로 취급해 volume
편집을 지원하지 않는다. internal/default named volume만 허용하고 bind-capable local driver option, unknown
driver/option, external alias는 raw/resolved에서 거부한다. 기존 operator data bind는 그대로 보존하되 request가
변경할 수 없다. cAdvisor system bind는 RO access를 allowlist key에 포함하고 `/sys` mountpoint와 root:docker
`0660` socket, root-owned non-writable parent chain의 inode/device/mode snapshot을 mutex 안에서 capture·재검증한다.
Docker group은 root-equivalent, privileged host filesystem actor는 threat model 밖으로 명시한다.
Manager mutation은 단일 canonical compose 파일만 사용한다. `include`, service `extends`, `COMPOSE_FILE`, 추가
override가 있으면 fail-close하고, 같은 mutex 안에서 persisted compose와 request candidate의 raw/resolved volume
graph hash를 각각 exact 비교한다. raw named-volume의 명시적 `name`/`external`은 금지하고 resolved name은
canonical project-derived name만 허용한다. cAdvisor는 raw literal/resolved identity 모두 RO `/sys`와 Docker
socket 두 mount의 exact set이어야 한다. 첫 mutation subprocess 성공 뒤 후속 preflight drift가 발생하면
원래 계약 오류를 typed 500에 보존하고 원본 compose byte/mode와 persisted runtime을 best-effort 복구한다.

### 근거

- 브라우저 BFF secret을 공유하거나 trusted CIDR을 넓히지 않고 서버 간 경계를 분리한다.
- `ops:cancel`은 PinVi가 실제로 필요한 최소 mutation만 허용한다.
- API 컨테이너 전용 주입은 Dagster·daemon·UI·PinVi Web로 secret이 확산되는 것을 막는다.
- 단일 고정 consumer의 두 회전 secret을 DB에서 관리하면 발급·폐기·암호화·bootstrap 수명주기만
  추가되며 현재 권한 모델이나 감사 의미가 개선되지 않는다.

### 결과(긍정)

- PinVi는 canonical REST만 사용하면서 read와 cancel 권한을 서로 다른 secret으로 갖는다.
- cancel token 탈취 시에도 schedule/policy/update request mutation은 거부된다.
- manager가 prod secret 주입과 compatible map+PinVi image pair 배포 순서를 한 곳에서 관리한다.
- `ktdctl pinvi-pair capture --verified-compatible`는 manifest가 없는 빈 환경에서
  base dependency → Map API → Map dependents → PinVi API → PinVi dependents 순서로 전체 토폴로지를
  단계 기동하고 최초 v4를 원자 기록한다. 실패하면 Map runtime 네 service와 PinVi API를 중지하고
  이 transaction이 새로 만든 container만 제거하며, 기존 container는 삭제하지 않는다.
- `ktdctl pinvi-pair rollback`은 Map API 복원과 signed smoke, Map UI·Dagster web·daemon의 exact image
  복원·revision 검증 뒤에 PinVi API를 복원하므로 서로 다른 generation을 동시에 실행하지 않는다.
- production `ktdctl pinvi-pair deploy`는 canonical single-file compose와 active/rollback generation을 먼저 검증하고,
  전체 topology가 running/healthy인지 read-only로 확인한다. 다섯 runtime을 함께 중지한 뒤 같은 frozen
  snapshot에서 만든 네 Map image와 PinVi API image에 `--no-deps` recreate를 적용하고, Map signed smoke,
  네 Map OCI revision, PinVi canonical ETL/provider-sync와 owned fixture의 정확한 409/502/503 typed cancel,
  UI auth lifecycle, runtime secret 격리를 모두 통과해야 manifest를 갱신한다.
- 중간 실패는 배포 시작 시점 active set의 다섯 image를 같은 frozen transaction으로 복구해 전체 계약을
  다시 검증한다. 복구도 검증할 수 없으면 다섯 runtime을 중지하고 `halted_requires_operator` 상태를 반환한다.

### 결과(부정)

- Map과 PinVi image가 모두 새 계약을 이해해야 하므로 단일 서비스만 임의 rollback할 수 없다.
  기존 manifest나 알 수 없는 manifest는 bootstrap capture로 덮어쓰지 않는다. ADR-21 이후
  provenance가 없는 v1/v2/v3 payload와 저장소 역사에 존재한 canonical sibling
  `compatible-pair-v2.json`/`compatible-pair-v3.json`은 자동 전환하지 않고 거부한다.
- pair 전환 중에는 혼합 generation 노출을 막기 위해 다섯 runtime을 함께 중지하므로 짧은 서비스 중단이 있다.
- local/prod mode를 자동 추론하지 않으므로 기존 `.env`에도 세 mode/required 값을 명시해야 한다.

### 후속

- (open) 여러 service principal과 개별 폐기·감사 요구가 생기면 DB 기반 hashed credential registry를
  별도 ADR로 검토한다.

## ADR-21: C6c compatible pair에 clean source revision을 결박한다

- 상태: partially superseded (application `300` v6/v8 authority)
- 날짜: 2026-07-19
- 결정자: human, AI agent

### 컨텍스트

C6c manifest는 immutable Map·PinVi image ID를 보존하지만 어느 source revision으로
빌드했는지를 증명하지 못했다. `--build`는 manager Compose가 각 저장소의 현재
worktree를 그대로 전송하므로 dirty checkout, 임의 build arg, `development` 기본값이
운영 image에 들어가도 image ID만으로는 이를 감지할 수 없었다.

### 결정

production C6c build는 Map·PinVi clean checkout의 exact lowercase 40자 `HEAD`를 manager가
직접 파생한다. build는 live worktree가 아니라 각 `HEAD`의 Git archive로 만든 일회성 context만
사용해 build 중 파일 변경·원복과 ignored 파일 혼입을 배제한다. canonical Compose build arg,
immutable image label, compatible-pair v4 record에 같은 revision을 끝까지 유지한다. v4의
active/rollback set은 각각 `map_image_id`, `map_ui_image_id`, `map_dagster_image_id`,
`map_dagster_daemon_image_id`, `map_source_revision`, `pinvi_image_id`,
`pinvi_source_revision`, `contract_generation`, `recorded_at`을 exact 필수 필드로 갖는다.

Map source revision의 적용 범위는 API 한 image에 한정하지 않는다. C7이 실제 runtime으로
검증하는 Map API·UI·Dagster web·Dagster daemon 네 image는 모두 같은 canonical
`KOR_TRAVEL_MAP_GIT_COMMIT`을 build arg로 받아 `org.opencontainers.image.revision`에 기록한다.
resolved Compose, Git snapshot context와 Dockerfile, candidate build·image inspection,
activation·rollback은 이 네 image를 분리할 수 없는 하나의 Map runtime set으로 취급한다.
compatible-pair manifest도 네 immutable image ID를 모두 보존해 moving tag가 갱신된 뒤에도
rollback이 이전 Map runtime 전체를 같은 source revision으로 exact 복원하게 한다.

### 근거

- moving tag와 image ID는 image byte 정체성만 보장하며 source checkout 정체성은 보장하지 않는다.
- manager가 mutation lock 안에서 checkout→arg→label→manifest를 연결해야 다섯 runtime이
  서로 다른 generation으로 빌드·기동되는 경로를 닫을 수 있다.
- build 전후 `git status`만 검사하면 build 중 변경 후 원복하는 TOCTOU와 ignored 파일을 놓치므로
  build input 자체를 exact Git tree로 고정해야 한다.
- raw/resolved build mapping은 Git snapshot context, 저장소 내부의 지정 Dockerfile, provenance
  arg만 허용하며 external Dockerfile, additional context, secret, target 같은 추가 입력은 거부한다.
- operator가 입력한 revision을 신뢰하지 않고 Git `HEAD`와 비교해야 타이포·stale
  환경값을 mutation 전에 거부할 수 있다.
- 현재 제작 단계이므로 Map dependent image provenance가 없는 v3 호환 계층을 유지하지 않고 v4로
  clean-cut하는 것이 오인 가능성을 낮춘다.

### 결과(긍정)

- capture/deploy/rollback 증거에서 Map runtime 네 image ID와 PinVi image ID, source commit을
  함께 추적할 수 있다.
- dirty checkout, `development`, revision mismatch, label 누락은 운영 container 변경 전 또는
  candidate 승격 전에 fail-close한다.
- activation·recovery 실패가 일부 Map runtime만 남기는 대신 전체 runtime set을 중지하므로
  혼합 generation을 정상 상태로 오인하지 않는다.

### 결과(부정)

- production `--build`는 두 저장소의 Git metadata와 clean worktree를 모두 필요로 한다.
- Map dependent image provenance가 없는 v1/v2/v3 compatible-pair manifest는
  배포·rollback에 사용할 수 없다.

### 후속

- (open) n150의 실제 clean checkout과 Map runtime 네 image·PinVi image label, manifest source
  revision을 C6c live smoke 증거에 포함한다.

## ADR-22: cAdvisor listen·healthcheck 포트를 단일 정본으로 관리한다

- 상태: accepted
- 날짜: 2026-07-19
- 결정자: human, AI agent

### 컨텍스트

canonical Compose는 host network의 cAdvisor listen 포트를 `CADVISOR_PORT`(기본
`12301`)로 바꿘지만 image의 기본 healthcheck는 `8080`을 조회했다. 설정 포트의
`/healthz`가 정상이어도 Docker health는 `unhealthy`가 되었고, production C6c capture의
base-service readiness가 정상 서비스를 오탐해 fail-close했다.

### 결정

cAdvisor 프로세스의 `--port` 인자와 Compose의 명시적 `/healthz` healthcheck는
모두 동일한 `CADVISOR_PORT` 보간을 사용한다. image에 상속된 기본 healthcheck는
canonical runtime health 계약으로 취급하지 않는다.

### 근거

- listen과 probe가 같은 포트 정본을 쓰면 기본·사용자 지정 포트 모두에서 drift가 없다.
- Docker `healthy`는 C6c의 기동 순서·rollback 판정에 사용되므로 실제 readiness endpoint와
  다른 probe를 허용하면 안 된다.

### 결과(긍정)

- 설정된 포트의 실제 cAdvisor readiness와 Docker health 판정이 일치한다.
- C6c base-service readiness가 실제 장애만 fail-close한다.

### 결과(부정)

- cAdvisor image 기본 healthcheck 변경은 자동 상속되지 않으며 Compose 계약을 직접
  갱신해야 한다.

### 후속

- (open) n150에서 Docker `healthy`와 설정 포트 `/healthz` 200을 모두 확인한 후
  C6c capture를 재시도한다.

## ADR-23: Map production API 인증 환경을 C6c runtime set에 결박한다

- 상태: accepted
- 날짜: 2026-07-19
- 결정자: human, Codex

### 컨텍스트

Map production profile은 ops principal뿐 아니라 admin BFF, service surface, public-key gate,
debug route 비활성, cursor 서명과 metrics 노출 정책도 fail-closed한다. manager canonical Compose가
ops read/cancel만 전달하면 새 Map image는 migration 전 또는 settings 생성 중 종료되어 C6c v4
runtime set을 기동할 수 없다.

### 결정

manager의 gitignore된 `.env`를 Map production API 인증 설정의 단일 source로 둔다. admin proxy
secret은 Map API와 Map UI BFF에만 같은 값으로 전달하고, service token과 cursor signing secret은
Map API에만 전달한다. production profile, public-key-required와 debug-off를 canonical literal로
고정한다. metrics는 인증된 Prometheus scrape가 결선되기 전까지 endpoint 자체를 명시적으로
비활성화하며, 암묵적 무인증 fallback을 허용하지 않는다. host network의 admin trusted proxy는
loopback `127.0.0.1/32`·`::1/128` exact JSON으로 고정해 image 기본값 drift에 의존하지 않는다.
C6c raw/resolved/runtime 검사는 각
credential의 shape·상호 구분·허용 service exact set과 설정 literal을 첫 mutation 전에 검증한다.
`.env.example`의 세 local placeholder는 production에서 exact 값으로 거부한다.

기존 runtime에서 새 UI env를 요구해 첫 전환을 순환 차단하지 않도록, 현재 pair의 manifest
`map_source_revision`이 가리키는 exact Map `docker-compose.yml`을 읽어 별도의 source env 계약
세대를 판정한다. admin/service/profile/public/debug hard-require가 있고 cursor가 없는 source v3가
manifest active/rollback 양쪽에 있고 migration marker가 처음 없을 때만 exact logical manifest hash를
`pending` baseline으로 mutation 전에 원자 기록한다. pending 재시도는 같은 hash만 허용하고, 현재 UI
admin proxy는 `없음 또는 frozen exact`로 검증한다. activation, runtime secret isolation, 전체 smoke가
성공하면 manifest commit 전에 sibling `map-production-env-migration-v1.json`을 `complete`로 원자
전환한다. 이 0600 owner regular-file marker는 manager가 삭제·초기화하지 않으며 rollback과 pair 회전도
낮출 수 없다. complete 뒤에는 source slot이 다시 v3/v3가 되어도 현재 UI admin proxy exact를 요구한다.
corrupt/symlink/wrong owner/mode와 pending baseline drift는 fail-close한다.

source 판정은 profile/public/debug/service를 API-only, admin을 API+frontend, cursor를 v3 전체 문서
0회/v4 API-only exact 1회로 고정한다. API·Dagster·daemon `env_file`은 known path/options exact
shape만 허용하고 exact revision에 추적된 참조 파일의 보호 이름도 거부한다. 해당 이름·placeholder가
다른 service 또는 build arg, label, command, config, secret 등 source Compose 전체 scalar tree의
다른 경로에 나타나도 거부한다.
candidate raw/resolved와 activation 후 runtime 검사는 source v3에서도 새 결선을 항상 필수 exact로
유지한다. 이 source env 세대와 migration marker는 compatible-pair manifest 자체의 v4 exact shape를
변경하지 않는다.

### 근거

- image 내부 기본값에 의존하면 Map 설정 변경이 manager 배포 계약을 조용히 깨뜨린다.
- admin BFF 공유 secret과 API-only service/cursor secret의 허용 runtime이 다르므로 이름 존재만
  확인하지 않고 exact service별 결선을 검증해야 한다.
- 사용하지 않는 metrics endpoint를 열어 둔 채 scrape credential만 누락하는 것보다 route를 끄는
  것이 현재 cutover에 단순하고 fail-closed다.

### 결과(긍정)

- C6c v4 candidate가 Map production settings와 같은 인증 불변식을 container mutation 전에 검증한다.
- credential이 Dagster·daemon·PinVi·다른 runtime으로 확산되는 경로를 차단한다.
- base runtime에서 새 manager 결선으로 가는 최초 managed cutover/rollback은 허용하면서, 완료
  marker 뒤의 A3→B4→rollback A3→C3 pair 회전도 누락 예외를 다시 열지 못한다.

### 결과(부정)

- 기존 production `.env`는 admin proxy, service, cursor signing 값을 별도로 준비해야 한다.
- Map metrics는 인증된 Prometheus credential 전달 경로를 별도 채택하기 전까지 수집하지 않는다.

### 후속

- (open) issue #63 구현·리뷰·CI와 n150 C6c v4 exact-pair 검증을 완료한다.
- (open) Map metrics가 운영상 필요하다는 측정이 나오면 secret-safe Prometheus credentials file
  전달을 별도 task로 설계한다.

## ADR-24: Map destructive enablement는 Manager production source가 명시 승인한다

- 상태: accepted
- 날짜: 2026-07-20
- 결정자: human, Codex

### 컨텍스트

Map application의 `admin_destructive_enabled` 기본값과 standalone compose 기본값은 안전하게
`false`여야 한다. 반면 Manager가 관리하는 production Map은 C7 인수와 백업 delete/restore/swap 등
승인된 운영 작업을 수행해야 한다. image 기본값이나 host env 누락 시 fallback으로 `true`가 되면 배포
의도와 파괴 권한을 구분할 수 없다.

### 결정

Manager canonical compose의 `kor-travel-map-api` environment에만
`KOR_TRAVEL_MAP_API_DESTRUCTIVE_ENABLED=true` literal을 둔다. 이 이름과 값은 C6c raw/resolved
candidate, activation 뒤 runtime의 exact protected path로 검사하고 다른 service·env_file·build arg·
command·label·config·secret 경로에는 나타날 수 없다. compatible-pair v4와 C7 attestation이 실제 Map
API environment hash를 보존하므로 source 승인과 runtime을 결박한다.

이 설정은 권한 주체를 대신하지 않는다. 각 destructive HTTP 요청은 계속 admin BFF 인증 principal을
요구하고, Map file registry의 delete/restore/swap event actor로 해당 principal을 기록한다. 따라서
enablement는 Manager source/runtime attestation, use는 Map의 principal audit event가 각각 증거를
소유한다.

### 결과

- standalone Map은 미설정 시 fail-closed이고 Manager production만 review된 source에서 명시 enable된다.
- host `.env` 누락이나 image default drift가 파괴 기능을 조용히 켤 수 없다.
- C6c/C7 환경 hash가 enablement drift를 mutation 전과 activation 뒤 모두 탐지한다.
- Manager 밖의 배포에서 파괴 작업이 필요하면 별도 승인 source를 작성해야 하며 암묵 fallback은 없다.

### 후속

- (open) Manager T-038과 Map issue #796을 각각 PR로 병합한 뒤 n150 exact pair를 recapture한다.

## ADR-25: Map feature 관리 REST는 Manager production source가 명시 활성화한다

- 상태: accepted
- 날짜: 2026-07-20
- 결정자: human, Codex

### 컨텍스트

Map API의 feature 관리 REST 미설정 기본값은 `true`라 Manager production runtime에
`KOR_TRAVEL_MAP_API_FEATURES_ROUTES_ENABLED`가 없어도 조회·조작 surface 자체는 동작한다. 그러나
C7 verifier는 기능 노출이 review된 Manager source의 명시 결정인지 image 기본값의 우연한 결과인지
구분할 수 없어 attestation을 fail-close했다. image 기본값이나 host 환경의 암묵 상속에 기대면 어떤
배포 source가 기능 노출을 승인했는지 증명할 수 없다.

### 결정

Manager canonical Compose의 `kor-travel-map-api` environment에만
`KOR_TRAVEL_MAP_API_FEATURES_ROUTES_ENABLED=true` literal을 둔다. C6c raw/resolved candidate와
activation 뒤 runtime은 이 exact path와 값을 보호 환경 계약으로 검사한다. 다른 service,
`env_file`, build arg, command, label, config, secret 등 다른 channel에는 이름 자체가 나타날 수 없다.

### 결과

- production feature 관리 REST 활성화 의도가 review된 Manager source와 runtime에 함께 결박된다.
- 누락, `false` 변경, 다른 service 유출은 첫 mutation 전 또는 activation 검증에서 fail-close한다.
- standalone Map과 Manager 밖 배포의 기본 정책은 바뀌지 않는다.

### 후속

- (open) issue #70 구현·단일 적대적 리뷰·CI와 n150 compatible-pair/C7 live E2E를 완료한다.

## ADR-26: compatible-pair image를 content-addressed local reference로 보존한다

- 상태: partially superseded (application `300` v6/v8 authority)
- 날짜: 2026-07-20
- 결정자: human, Codex

### 컨텍스트

compatible-pair v4 manifest의 immutable image ID는 배포 정체성을 기록하지만 Docker image store의
reference는 아니다. `pinvi-pair deploy --build`가 다섯 service의 moving tag를 candidate로 옮기고
기존 container를 교체하면 직전 active image는 tag와 container reference를 모두 잃어 GC 가능한 상태가
된다. n150에서 배포 직전에는 존재하던 다섯 image가 성공 직후 manifest rollback에 기록된 채 모두
사라져 다음 deploy/rollback preflight가 중단되는 문제를 재현했다.

### 결정

Manager는 service 이름과 전체 image SHA로 결정되는 예약된 repository/tag 형식의 전용
content-addressed local retention reference를 사용한다. 같은 reference를 다른 image ID로 다시 tag하지
않고, cleanup도 이 exact manager namespace의 tag만 제거하며 image ID 자체를 삭제하지 않는다. Compose의
resolved service image가 이 namespace를 사용하는 것도 거부한다.

build 유무와 무관한 deploy와 explicit rollback은 기존 read-only preflight 뒤 현재 manifest의
`active ∪ rollback` reference를 additive 생성·exact inspect한다. 전체 재검증 뒤 manifest 밖 stale
manager tag 정리를 성공시켜야만 build 또는 container mutation으로 진행한다. deploy candidate 다섯
image도 첫 container stop 전에 보존한다. 새 pair 검증과 manifest 원자 commit이 끝나면 새 manifest의
rollback image가 아닌 manager tag를 제거한다. activation 또는 manifest commit 실패 시 candidate
reference 정리는 다음 안전 조건으로 제한한다. 최초 capture도 candidate 보존 뒤에만 runtime mutation을
시작한다.

candidate cleanup은 container mutation 전 실패이거나, manifest가 시작 snapshot으로 확정되고 시작
active runtime의 복구·전체 검증이 성공한 경우에만 수행한다. previous-pair recovery 실패, mixed runtime,
manifest commit 상태 불확정 중 하나라도 있으면 candidate를 포함한 관련 retention reference를 모두
보존해 operator 진단·복구 근거가 사라지지 않게 한다. 정상 preflight가 다시 성립하기 전에는 이 residue를
reconcile하지 않는다.

post-commit cleanup 실패는 이미 검증·commit된 runtime을 과거 pair로 되돌리지 않고 명시적
`cleanup_pending` 결과로 남기고 이전 pair recovery를 호출하지 않는다. 다음 pair mutation은 현재 두
슬롯을 다시 additive ensure한 뒤 stale reference 정리를 성공시키기 전까지
중단해 세대가 무한 누적되지 않게 한다. 외부 root-equivalent actor의 강제 image 제거와
`docker image prune -a`는 threat model 밖이며, 이를 견디는 archive/registry는 별도 결정으로 다룬다.

### 근거

- content-addressed reference는 기존 tag를 덮어쓰지 않아 부분 tag 실패와 process crash가 현재
  rollback reference를 훼손하지 않는다.
- operation 시작 때 두 슬롯을 모두 보존하고 commit 뒤 rollback reference만 남기면 role tag
  수명주기보다 검증과 복구가 단순하며 상시 reference 수도 최소화된다.
- candidate도 activation 전에 보존해야 중간 실패 복구와 manifest commit 사이의 reference 공백이 없다.

### 결과

- moving service tag와 container 교체 뒤에도 manifest rollback의 다섯 image를 exact 복원할 수 있고,
  다음 mutation 전에는 active/rollback 두 슬롯을 모두 retention reference로 결박한다.
- 실패한 candidate reference와 과거 세대는 정해진 reconcile 경로로 정리된다.
- Docker 외부 privileged cleanup을 방지하려면 별도의 local registry 또는 archive가 필요하다.

### 후속

- (open) issue #72/T-041 구현·단일 적대적 리뷰·CI와 n150 rollback/live E2E를 완료한다.

## ADR-27: compatible-pair readiness는 canonical resolved Compose의 healthcheck 선언을 따른다

- 상태: partially superseded (application `300` v6/v8 authority)
- 날짜: 2026-07-31
- 결정자: human, Codex

### 컨텍스트

production compatible-pair의 mutation 전 preflight는 `services_for_target("pinvi")`의 모든
필수 service를 검사한다. 기존 `_require_services_ready`는 service별 Compose 계약을 보지 않고
무조건 Docker `State=running`과 `Health=healthy`를 요구했다. canonical resolved Compose에는
실제 HTTP/native readiness를 제공하는 service에만 healthcheck가 있고, Grafana, Prometheus,
Concierge MCP·Scheduler·UI, Map Dagster daemon 등에는 healthcheck가 없다. Docker Compose가
정상 `running`으로 관리하는 이 service들의 `Health`는 빈 값이므로 production deploy가 mutation
전에 항상 fail-close했다.

### 결정

readiness requirement는 transaction에 고정된 canonical resolved Compose service spec에서
service별 typed policy로 파생한다. 명시적으로 활성화된 canonical healthcheck가 있는 service는
`running + healthy`, healthcheck가 없거나 Compose 표준으로 명시 비활성화된 service는
`running`을 요구한다. service 누락, malformed/모호한 healthcheck, healthcheck 선언 service의
빈 health 또는 `starting`/`unhealthy`, 모든 service의 비-running 상태는 fail-close한다.

compatible-pair의 필수 service는 singleton으로 고정한다. canonical `scale`과
`deploy.replicas`가 있으면 정확히 정수 `1`이어야 하고 비-singleton deploy mode는 거부한다.
runtime 조회는 `docker compose ps --all`을 사용하며 service별 record가 정확히 하나여야 한다.
canonical `container_name`이 있으면 runtime `Name`도 exact 일치해야 한다. 종료 record를 기본
`ps` 필터로 숨기거나 같은 service의 여러 record를 dict로 덮어쓰지 않으며, payload 안의
예상 밖 service와 구조가 잘못된 record 하나라도 전체 readiness를 fail-close한다.

새 healthcheck는 실제 service-native readiness를 증명할 수 있을 때만 canonical Compose에
추가한다. scheduler/daemon의 PID 1 생존만 확인하는 probe나 HTTP 의미를 검증하지 않는 임의
socket probe를 이 문제의 우회책으로 추가하지 않는다. image에 상속된 healthcheck도 canonical
resolved Compose에 명시되지 않으면 production readiness 정본으로 채택하지 않는다.

### 근거

- Docker Compose `up --wait`는 healthcheck가 있는 service는 healthy, 없는 service는 running을
  기다린다. preflight와 activation이 같은 선언적 계약을 사용해야 정상 runtime을 서로 다르게
  판정하지 않는다.
- service 이름별 예외 목록은 Compose 변경 때 drift하므로 frozen resolved document에서 직접
  policy를 파생해야 한다.
- 실제 readiness가 없는 worker에 가짜 healthcheck를 추가하면 `healthy`가 업무 준비 상태라는
  잘못된 증거가 된다.
- malformed policy를 `running`으로 낮추지 않고 거부하면 canonical source 손상이나 지원하지 않는
  Compose 의미를 안전하게 탐지할 수 있다.
- `ps --all`과 exact singleton cardinality는 stopped/stale replica가 다른 running record 뒤에
  가려져 destructive mutation 전 preflight를 우회하는 것을 막는다.

### 결과(긍정)

- healthcheck가 없는 정상 장기 실행 service 때문에 compatible-pair deploy가 영구 차단되지 않는다.
- API·UI·Dagster web·cAdvisor 등 canonical healthcheck service의 실제 장애는 계속 fail-close한다.
- 새 service가 healthcheck를 추가하거나 제거하면 같은 resolved Compose snapshot에서 preflight
  의미도 함께 바뀌어 별도 목록 drift가 없다.
- stale·scaled·이름 drift container가 있으면 정상 record가 함께 있어도 operator 조치 전에는
  compatible-pair mutation을 시작하지 않는다.

### 결과(부정)

- healthcheck가 없는 service는 Docker process가 `running`이라는 liveness까지만 증명한다.
  더 강한 readiness가 필요하면 해당 service가 제공하는 authoritative probe를 먼저 설계해야 한다.

### 후속

- (open) issue #90/T-047의 n150 read-only exact preflight와 별도 승인된 compatible-pair
  실행을 완료한다. 단일 적대적 exact-head 리뷰는 `P0 0 / P1 0 / P2 0`으로 통과했다.

## ADR-28: cache-target 최초 production 전환은 default-off runner와 durable receipt로 수행한다

- 상태: accepted
- 날짜: 2026-08-02
- 결정자: human, Codex

### 컨텍스트

T-VN-41은 Map의 cache-target service stream과 PinVi의 generation/outbox consumer를 production에서
처음 연결한다. command·consumer·restore-fence·recovery는 서로 다른 권한이며, 최초 0→N backfill은
ordinary worker가 먼저 command를 lease하면 재현 가능한 snapshot/cutover 순서를 잃는다. 기존 C6c는
Map runtime 네 개와 PinVi API를 immutable compatible pair로 결박하고 전역 mutation lock, frozen
canonical `.env`/Compose, raw/resolved/runtime secret isolation을 이미 강제한다. 새 전환이 이 경계를
우회하거나 recovery credential을 ordinary API에 상시 주입하면 기존 보안·rollback 계약이 깨진다.

### 결정

Map API에는 digest 기반 `KOR_TRAVEL_MAP_API_CACHE_TARGET_SERVICE_PRINCIPALS` registry만 전달한다.
PinVi ordinary API에는 sync flag, command/consumer token, consumer ID와 OpenAPI/source/contract
generation pin의 정확한 7개 변수만 전달한다. restore-fence/recovery 원문 token은 ordinary API를 포함한
장기 실행 service에 전달하지 않는다. `sync=false`에서 C6c 전역 lock과 frozen canonical evidence를
검증한 dedicated initial-cutover runner에는 실제 사용하는 command·consumer·recovery token만 실행 시간
동안 주입한다. restore-fence token은 Map registry와 향후 별도 restore 작업 경계에만 보관한다.
Map cache-target endpoint는 7개 기능 변수와 별개인 `PINVI_KOR_TRAVEL_MAP_API_BASE_URL`로 명시하고 기존
admin base URL과 같은 production loopback root에 결박한다. consumer ID는
`pinvi-cache-target-consumer`만 허용한다.

네 role token은 서로 및 기존 Map/PinVi service/admin/ops credential과 달라야 한다. Map registry는
command·consumer·restore-fence·recovery 정확히 네 principal만 허용한다. 각 principal은 각각
`cache-target:command`, consumer의 read/claim/ack/nack/snapshot, `cache-target:restore-fence`,
recovery trust domain의 `cache-target:recovery` + `cache-target:recovery-replay`의 정확한 scope만 가지며
`external_systems`는 정확히 `["pinvi"]`다. 별도 replay principal·다섯 번째 token은 만들지 않는다. SHA-256
digest, 고유 principal ID, 같은 consumer ID와 이 구조를 원문 token에 교차 결박하고 extra principal·scope·
external system은 거부한다. raw Compose에는 secret literal을 두지 않고, registry JSON 자체와 네 token
digest도 protected/redaction 대상으로 다룬다. resolved/runtime validator는 각 값이 허가된 경로 밖에
나타나면 fail-close한다. runner argv·출력·receipt에는 원문 token이나 resolved environment를 남기지 않는다.
receipt와 audit에는 registry JSON이나 개별 digest 대신 canonical role→(digest, consumer ID, exact scopes,
external system) binding 전체의 logical SHA-256만 기록한다.

initial runner의 elevated recovery token은 Docker `-e NAME=value`나 Compose environment로 전달하지 않는다.
manager가 owner-only 임시 secret file을 만들고 runner 내부의 고정 entrypoint가 읽은 뒤 process environment로
올리는 경계만 허용한다. host secret file, ephemeral container와 mount는 success/failure/signal 모든 종료
경로에서 분류·정리하며 정리 결과를 secret-free evidence로 남긴다.

최초 runner 성공은 고정 cutover ID, epoch, contract pin, frozen env SHA, raw/resolved Compose logical SHA,
active와 rollback compatible-pair identity, protected role-binding logical SHA와 PinVi가 반환한
request/count/Merkle/published 결과를 owner-only durable receipt로 먼저 commit한다. active와 rollback pair
모두 같은 cache-target generation/contract를 지원해야 하며 generic rollback도 cache health/pin smoke를
통과해야 한다. stale receipt나 stale rollback image는 cutover와 이후 rollback 후보로 허용하지 않는다.

성공 initial receipt가 있어야 enable journal을 `enable_preparing`으로 먼저 fsync한다. 이 단계는 initial
receipt hash, active/rollback pair logical hash, 이전/새 canonical env SHA를 한 transaction identity로 묶는다.
그 뒤에만 journal을 `env_committed` → `recreate_started` → `verified` → `committed` 순으로 원자 전이하면서
canonical `.env`의 sync를 `true`로 바꾸고 같은 immutable active image의 PinVi API를 재생성한다. 실패·crash
복구도 `rollback_preparing` → `rollback_env_restored` → `rollback_recreate_started` → `rolled_back`의 durable
phase로 기록한다. ordinary startup cache health/pin smoke와 기존 full compatible-pair image/provenance/runtime/
secret-isolation attestation과 production causal canary가 모두 통과해야 enable을 commit한다. canary는 고유
command가 Map event가 되고 PinVi DB/cache에 반영된 뒤 ACK되는 인과 사슬, lag 0, DLQ 0, 예상 count/Merkle를
검증한다. 전체 initial/enable은 하나의 C6c 전역
critical section에서 수행한다. 명시적으로 lock을 재획득하는 재개 경로라면 canonical env/Compose와 active·
rollback pair, initial receipt를 전부 refreeze해 journal transaction identity와 다시 대조한다. mixed/foreign
evidence는 자동 덮어쓰지 않는다.

production 실행 권한은 코드에 추적되는 단일 pin manifest로 추가 결박한다. manifest는 contract generation,
Map OpenAPI SHA-256, Map functional owner revision, Map build/release revision, PinVi reviewed candidate와 별도의 PinVi release revision을
기록한다. reviewed candidate는 감사 정보일 뿐 실행 가능한 release fallback이 아니다. 최종 exact HEAD의 단일 독립 적대적 GO review와
PinVi merge SHA가 확정되기 전에는 release를 비워 두고 initial/enable 및 compatible-pair capture/deploy/
rollback을 모두 mutation 전에 차단한다. 최종 pin commit 뒤에도 active와 rollback pair 모두 exact Map·PinVi
release provenance를 가져야 한다. functional owner와 실제 image build/release owner를 혼용하지 않는다.
cache-target contract의 모든 값이 canonical unset/default인 기존 C6c 배포에는 이 추가 gate를 적용하지 않고,
부분 설정만 fail-close한다.

PinVi #424의 단일 적대적 GO review와 squash merge가 완료되어 PinVi release는
`4943282006139fa3b4ef3cb247780bfd9721b4c7`로 확정한다. Map release는 #924의 수정·merge SHA가 확정될
때까지 기존 merge #923 값을 유지하며, 두 release가 모두 최종 고정되기 전에는 Manager exact-head 리뷰와
운영 전환을 시작하지 않는다.

2026-08-02 NO-GO 리뷰에서 기존 v4 old pair는 일반 deploy로 generation 7의 active=rollback 상태에 도달할 수
없음이 확인됐다. 따라서 기존 manifest가 있는 production에는 one-time generation bootstrap을 둔다. 이 경로는
sync=false exact candidate의 image/source/cache contract를 검증한 뒤 v4 active와 rollback을 같은 첫 generation 7
pair로 원자 전환한다. old pair는 v4 rollback slot이 아니라 H35 coupled rollback bundle에만 보존한다.

H35와 T-VN-41은 별도 명령의 느슨한 조합이 아니라 하나의 결합 전환 transaction이다. backup, image build,
Map/Pin DB migration, H35 CSV, generation bootstrap, initial, enable, causal canary, GC, 최종 verify와 forward commit을
한 process의 C6c lock과 owner-only durable journal로 수행한다. non-terminal journal은 same resume/coupled rollback
외 모든 manager mutation을 차단한다. host receipt 재사용만으로 DB 신선도를 추정하지 않고, resume마다 backup
identity, schema revision, restore epoch, cutover ledger와 local/remote convergence를 fresh 검증한다.

forward boundary 전 실패는 new runtime stop 뒤 Map application/Dagster/Pin DB와 manager env/state/manifest를
결합 복구하고 old image를 마지막에 기동한다. migration 뒤 일반 image-only rollback은 금지한다. forward commit
또는 최초 외부 event 뒤에는 old schema restore를 거부하고 same-generation recovery/fix-forward만 허용한다.
Map schema/CSV 의미는 Map-owned typed CLI가 수행하고 manager는 transaction/source/schema/backup identity에
결박된 exact secret-free JSON receipt만 소비한다. production public method에는 호출자 제공 attestor/canary/smoke
주입점을 두지 않는다.

DB backup은 파일 존재나 archive list 성공만으로 승인하지 않는다. exact 5-writer registry를 정지한 하나의
fence에서 세 DB의 write counter와 stats reset identity, in-flight transaction, Map Dagster run을 전체 작업
앞뒤로 대조하고, 각 dump를 별도 scratch DB에 실제 restore해 schema/data logical inventory까지 검증한다.
cross-repo DB identity는 prefix와 terminal NUL을 포함한 `h35-db-identity-v1` 하나만 사용하며 scratch identity는
원본 identity와 분리한다.

Pin 최종 경계의 cache command/claim/app queue 의미는 Manager가 재구현하지 않고 Pin-owned typed helper가 같은
DB snapshot에서 검증한다. schema `0047` preflight는 read-only이고 schema `0048` final audit row는 append-only다.
causal canary 뒤 Map-owned `gc` helper가 실제 bounded GC와 deterministic observation의 backlog 0/referenced 보존을
증명한 뒤, Manager가 exact 5 writer를 모두 정지하고 별도 final fence를 확정한다. deterministic observation
ID는 versioned namespace `h35:{transaction_id}:cache-target-snapshot-gc:v1` 하나이며 이전 축약형은
fail-close한다. stopped Map verify의 full
stream/control/count/Merkle evidence를 Pin finalize request에 전달하며, Manager는 Pin receipt를 fresh DB audit
exact 1행의 request/evidence/initial·final fence/prior/canary와 다시 대조한다. Pin audit INSERT는 stable final
fence에서 제외하지만 Map application·Dagster write-counter hash는 verify 전과 finalize 전후 불변이어야 한다.
pre-forward rollback은 Pin DB 전체 restore를 사용하며 audit row를 개별 삭제하지 않는다. forward fsync 뒤에는
exact writer 재기동과 health/attestation을 재개해 `runtime_activated`에서만 성공한다.

### 근거

- default-off runner는 ordinary lifespan worker와 최초 snapshot/backfill의 lease 경쟁을 제거한다.
- recovery credential을 ephemeral 경계에만 두고 쓰지 않는 restore-fence credential도 주입하지 않으면
  ordinary API나 initial runner 탈취가 불필요한 restore 권한으로 확장되지 않는다.
- command 전용 scope와 정확한 4-principal registry는 source mutation 권한이 consumer 동작으로 번지는 것을
  막고, 허용 목록의 조용한 확장을 차단한다.
- C6c lock/frozen evidence를 재사용하면 cache-target 전환과 compatible-pair·credential rotation이 서로의
  검증 뒤 runtime을 바꾸지 못한다.
- env 변경 전 journal과 단계별 fsync는 원격 cutover 성공이나 runtime 재생성 중 process crash가 나도
  sync 상태와 복구 행동을 추측하지 않게 한다.
- active/rollback pair의 같은-generation 검증은 generic rollback이 cache-target을 모르는 stale image로
  production을 되돌리는 것을 막는다.
- Docker metadata 밖 secret-file 경계는 elevated recovery token이 종료된 runner의 inspect evidence에
  장기 잔류하는 것을 막는다.
- causal canary는 단순 readiness가 놓치는 Map→PinVi 실제 데이터 경로와 ACK 완료를 terminal commit 전에
  검증한다.
- 같은 active image 재생성과 full pair attestation은 환경 전환을 image generation 변경과 분리하면서도
  compatible-pair 정합성을 유지한다.
- candidate와 release를 분리한 tracked gate는 review 중인 commit이 운영 pair로 조용히 승격되는 것을 막고,
  release 확정 전 모든 mutation을 결정적으로 차단한다.
- one-time generation bootstrap은 old rollback 때문에 새 exact pair 자체를 만들 수 없는 순환 의존을 제거한다.
- 결합 journal과 DB identity 재검증은 host receipt만 남고 DB가 rewind된 상태의 stale resume를 막는다.
- forward boundary는 schema가 바뀐 뒤 image만 되돌리는 혼합 generation 복구를 금지한다.

### 결과(긍정)

- ordinary runtime은 최소 권한 7개 변수만 가지며 restore/recovery 원문을 보유하지 않는다.
- 최초 backfill, crash retry, sync enable과 rollback이 durable evidence로 판정된다.
- 기존 C6c production mutation 차단, frozen env, immutable image와 secret non-leak 보호가 유지된다.

### 결과(부정)

- initial cutover와 enable이 별도 phase가 되어 operator 절차와 회귀 테스트가 늘어난다.
- 전용 runner가 실행되는 짧은 동안에는 해당 ephemeral container process만 recovery credential을 가진다.

### 후속

- (open) T-048 구현·CI·최종 exact HEAD의 단일 독립 적대적 리뷰를 완료한다.
- (open) 별도 승인 아래 n150 initial cutover와 live receipt/pair attestation을 수행한다.

## ADR-29: cache-target의 긴 pre-forward 검증은 독립 사전 진단 gate로 분리한다

- 상태: accepted
- 날짜: 2026-08-03
- 결정자: human, Codex

### 컨텍스트

T-VN-41의 결합 cutover는 외부 event 전에 DB dump·logical inventory·scratch restore rehearsal과
authenticated runtime smoke를 수행한다. 이들은 failure를 fail-close해야 하지만, 실제 production DB의
`pg_dump` advisory grammar나 Compose 직후 readiness처럼 긴 primitive가 실패하면 entire cutover를 반복해
원인을 확인하게 된다. 그러면 old runtime 복구와 writer fence가 불필요하게 반복되고, terminal
`rolled_back`만으로는 어느 primitive가 실패했는지 충분히 분리되지 않는다.

### 결정

Docker Manager가 `cache-target diagnose`라는 별도 사전 진단 transaction을 제공한다. 이 경로는 C6c
global lock과 exact writer fence를 사용해 세 DB의 archive/inventory/scratch rehearsal과 canonical
authenticated smoke를 수행하지만, candidate build·migration·initial event·sync enable·manifest/.env
mutation은 하지 않는다. diagnostic receipt는 input logical identity와 typed stage/failure class만 남기며,
원문 stderr/stdout, DSN, credential, resolved Compose, artifact path는 남기지 않는다.

new cutover는 같은 Manager/tool/pair/Compose/DB-schema/writer-registry identity를 가진 fresh completed
diagnostic receipt를 요구한다. 다만 diagnostic archive나 row inventory는 current data backup의 대체물이
아니므로 actual cutover는 writer fence 뒤 fresh backup/rehearsal을 다시 만든다. 동일 input에서 diagnostic
failure가 반복되면 제한된 시간/횟수 budget 뒤 abort하고, regression이 포함된 수정과 새 receipt 없이는
new cutover를 시작하지 않는다.

### 근거

- capability failure를 forward window와 분리하면 external-event 경계, coupled rollback 및 data-freshness
  계약을 약화하지 않고 원인을 한 stage로 확정할 수 있다.
- typed error class는 production raw log를 보존하지 않고도 exact test fixture와 코드 수정으로 연결된다.
- `pg_dump` warning grammar를 major-version fixture로 고정하면 broad stderr allowlist로 보안을 낮추지 않는다.
- retry budget을 transport-ready GET에만 한정하면 mutating operation의 결과를 추측하지 않는다.

### 결과(긍정)

- operator는 long-running pre-forward failure 뒤 full cutover를 반복하지 않고 diagnostic receipt에서
  next action을 결정한다.
- cutover journal은 final `rolled_back` 외에 마지막 safe failure stage/class를 갖게 된다.
- Map/PinVi HTTP 의미와 Docker Manager lifecycle/retry 책임이 분리된다.

### 결과(부정)

- production 전환 전 writer를 잠깐 멈추는 별도 diagnostic window와 receipt lifecycle이 추가된다.
- archive와 scratch rehearsal을 diagnostic과 actual cutover에서 각각 수행하므로 happy path의 실행 시간은
  늘어난다. 이는 stale backup 재사용보다 data integrity를 우선한 의도적 비용이다.

### 후속

- (open) T-049A~E를 구현하고 n150 sync=false diagnostic rehearsal을 먼저 통과한다.
- (open) Map production data-only inventory failure를 typed failure class와 exact fixture로 재현해
  허용 grammar 또는 별도 fail-close 원인을 보강한다.

## ADR-30: legacy pre-stop diagnostic은 제한된 Manager receipt로만 퇴역한다

- 상태: accepted
- 날짜: 2026-08-05
- 결정자: human, Codex

### 컨텍스트

T-049F가 Map-owned durable writer-drain lease/receipt를 도입하면서 diagnostic과 window journal의
schema를 version `2`로 올렸다. 이전 version `1` diagnostic에는 lease/restore evidence가 없어
`writers_drained` 이후의 crash recovery를 안전하게 추론할 수 없다. n150 F2 재개 전 남아 있던 v1
diagnostic은 `writers_fencing`에 있어 Docker·DB·runtime mutation 이전에 fail-close됐고, 같은 state
directory의 window는 `rolled_back` terminal이었다. direct `rm`이나 state-directory 통째 삭제는
abort-budget attempt log와 terminal evidence까지 조용히 지울 수 있다.

### 결정

`ktdctl cache-target retire-legacy-diagnostic --confirm`을 production 전용 Manager command로 둔다.
명령은 C6c global lock과 frozen canonical environment 아래 exact diagnostic path 하나만 읽고,
owner-only regular/root-owned/single-link/`0600`, bounded raw JSON, v1 exact field set과
`prepared` 또는 `writers_fencing` phase를 강제한다. raw SHA와 phase만 담은 owner-only retirement
receipt를 먼저 fsync하고 source inode·mode·SHA를 다시 확인한 후 journal을 unlink하고 directory를 fsync한다.
receipt가 이미 있으면 동일 SHA/phase만 idempotent하게 마무리할 수 있다. unlink 뒤 directory fsync나
return 전에 process가 중단되어 journal이 이미 없으면 valid canonical receipt를 성공으로 재보고한다.

`writers_drained` 이후, terminal, v2, 손상·foreign journal은 퇴역 대상이 아니다. Map-owned durable
lease/receipt recovery 또는 existing transaction resume/rollback이 유일한 경로다. 현재 diagnostic
attempt log, window journal, compatible-pair manifest, canonical `.env`, Docker runtime, DB는 이 command가
변경하지 않는다. lock을 잡은 뒤 frozen transaction environment의 `assert_manager_mutation_allowed`와
production contract를 read-only로 다시 검사하므로 non-terminal 또는 invalid window도 retirement를 막는다.
CLI `--confirm`이 없으면 process는 어떠한 mutation도 시도하지 않는다.

### 근거

- v1 post-drain state를 자동 변환·삭제하지 않으면 writer stop/recovery를 추측해 실행하지 않는다.
- receipt-first와 inode/SHA 재검증은 process crash나 path replacement가 journal을 무증적으로 사라지게
  하는 것을 막는다.
- attempt log를 보존하면 service 전 환경에서 legacy state를 폐기해도 expensive rehearsal retry budget을
  우회하지 않는다.
- narrow command가 state directory 전체 삭제보다 점검 가능하고, raw journal/credential을 stdout이나
  tracked 문서에 노출하지 않는다.

### 결과

- F2는 safe pre-stop legacy state 뒤 fresh v2 diagnostic으로 재개할 수 있다.
- post-drain crash recovery는 이전처럼 fail-close하므로 일부 runtime이 정지된 채로 방치될 위험을
  "정리" 명목으로 키우지 않는다.
- operator는 manual file deletion 대신 audit 가능한 Manager 경로 하나를 사용한다.

## ADR-31: pinned compatible-pair drift는 one-shot bootstrap transaction으로만 수렴한다

- 상태: superseded (ADR-34, 2026-08-06)
- 날짜: 2026-08-05
- 결정자: human, Codex

### 컨텍스트

F2 fresh diagnostic은 writer fence 전 Map API/UI·Dagster web·Dagster daemon·PinVi API의 실행 image tuple이
active compatible-pair manifest와 다름을 확인하고 종료했다. 일반 `pinvi-pair deploy`와 rollback은 current
tuple이 manifest active와 동일할 때만 동작한다. 그 전제를 무시하면 mixed generation을 정상 rollback source로
오인하게 된다. 또한 canonical source cache의 clean HEAD도 tracked cache-target exact release pin과 달라
기존 `--build` source authority로 candidate를 만들 수 없다.

### 결정

일반 deploy의 우회 flag를 만들지 않고, production에서 한 번만 쓸
`ktdctl pinvi-pair bootstrap-pinned-drift --confirm`을 별도 transaction으로 둔다. command는 CLI로 image,
source revision, arbitrary migration head, force를 받지 않는다. candidate source authority는 코드에 tracked된
`CACHE_TARGET_PRODUCTION_PINS`이며, clean configured repository가 그 exact commit object를 보유한 경우에만
해당 Git archive를 일회성 build context로 사용한다. canonical `.env`는 frozen input identity로만 읽고
수정하지 않으며 그 source HEAD는 candidate authority가 아니다.

F1D는 candidate source provenance를 읽기 전에 F1E trusted source-installer의 `committed` journal,
root-owned detached exact worktree, pin/tree evidence를 모두 재검증한다. F1E journal이 없거나 non-terminal,
foreign, 손상 상태면 normal pair mutation과 달리 기존 source cache를 fallback으로 취급하지 않고 즉시
거부한다.

mutation 전 C6c global lock, frozen env·raw/resolved Compose·external input identity, strict old manifest와
retention image evidence, candidate resolved Compose secret isolation, candidate·live Map/PinVi DB head equality를
모두 검증한다. candidate와 live head가 하나라도 다르면 이 command는 runtime을
바꾸지 않고 H35 coupled DB recovery만 허용한다. old active image의 static head는 기존 drift를 설명하는 감사
근거일 뿐이다. 이 transaction은 old image를 rollback slot으로 승격하거나 재기동하지 않으므로 old head가
live DB와 다르다는 사실은 candidate build/activation을 막지 않는다. 같은 이유로 시작 Map runtime 내부의
source revision 불일치, image tuple, protected-value wiring 또는 UI auth도 candidate authority가 아닌 legacy drift
evidence일 뿐이다. 시작 runtime이 manifest active와 같아도 F1D journal이 없으면 tracked candidate를 다시
staged recreate해 exact frozen environment를 적용한다. 다섯 candidate runtime이 exact image·frozen environment로
activation된 뒤에만 runtime secret isolation과 UI auth를
강하게 검증하며, 실패하면 old image를 되살리지 않고 halt한다.

candidate build·immutable image attestation 뒤, runtime stop 전에 owner-only durable journal을 fsync한다.
journal은 original manifest SHA, frozen input digest, candidate five image IDs/source revisions, expected DB heads,
release pin version과 phase를 보존한다. non-terminal·foreign·손상 journal은 다른 pair mutation을 막고 동일
candidate의 resume만 허용한다. candidate activation 실패 시 old image를 다시 기동하지 않고 다섯 runtime을
halt한다. 성공 뒤에만 `active = rollback = candidate`인 bootstrap manifest를 원자 기록한다. old manifest는
commit 전 recovery evidence일 뿐 새 rollback slot으로 승격하지 않는다.

journal phase는 `prepared → runtime_activated → manifest_committing → committed`다. manifest write 전에
`manifest_committing` intent를 fsync하므로, manifest fsync와 terminal journal write 사이 crash도 original-old
manifest 또는 candidate-only manifest를 구분해 같은 candidate 검증·commit으로 재개한다. runtime activation 뒤의
contract/DB-head 재검증 실패는 모두 다섯 runtime halt로 수렴하며, manifest write/fsync 실패는 검증된 candidate와
intent journal을 보존해 재시도한다.

`.env`가 가리키는 source checkout을 장기적으로 새 release로 바꾸는 일은 trusted source-installer의 별도
transaction이다. drift bootstrap은 그 값을 암묵적으로 수정하지 않는다.

### 근거

- release pin을 유일한 source authority로 삼으면 stale checkout/current runtime을 승인하지 않는다.
- old active를 rollback slot으로 옮기지 않으면 완료 뒤 일반 preflight도 새 active와 rollback을 모두
  release pin으로 증명할 수 있다.
- Map API의 자동 migration 뒤 old image rollback은 schema-incompatible할 수 있으므로 DB head 불변 gate와
  halt 정책이 image-only recovery보다 안전하다.
- durable journal이 없으면 candidate activation과 manifest commit 사이 crash가 새 candidate build로 덮여
  복구 대상이 사라진다.

### 결과

- n150 F2는 raw Docker·Compose·`.env` 우회 없이 tracked exact immutable pair로 수렴할 수 있다.
- source checkout 갱신과 runtime drift 복구의 책임이 분리되어 다음 deploy의 provenance failure를 숨기지
  않는다.
- normal deploy/rollback의 strict active-manifest precondition은 약화되지 않는다.

## ADR-32: pinned source selection은 user-owned Git checkout과 분리한 trusted transaction이다

- 상태: accepted
- 날짜: 2026-08-05
- 결정자: human, Codex

### 컨텍스트

F1D drift bootstrap은 tracked exact Map·PinVi release commit을 source authority로 사용해야 한다. n150의
current source cache는 user-owned `0700` Git worktree이며 required commit object도 없다. root가 이
worktree에서 `git fetch`, `git archive`, `git config`를 실행하면 repository-local include, URL rewrite,
custom transport, hook 또는 credential 설정을 root execution boundary로 올릴 수 있다. source-root만 바꿔도
canonical `.env`의 `KOR_TRAVEL_MAP_GIT_COMMIT`/`PINVI_SOURCE_REVISION` scalar가 이전 값이면 normal builder는
여전히 fail-close한다.

### 결정

trusted root 전용 `ktdctl pinvi-pair install-pinned-sources --confirm` transaction을 둔다. source owner
checkout은 source-owner helper가 origin identity를 읽어 code-owned canonical HTTPS `RepoSpec`과 exact 비교하는
read-only input일 뿐이다. root는 user-owned Git config를 해석하지 않고, compiled URL과
`CACHE_TARGET_PRODUCTION_PINS` full SHA만 empty root-owned bare staging repo에 sanitized Git environment로
fetch한다. hooks, global/system/repository config, prompt, local/file/ext protocol, submodule, branch/tag/refspec
전체 fetch는 모두 금지한다.

exact commit과 tree를 검증한 뒤 root-owned non-writable stable path에 detached immutable worktree를 만든다.
frozen canonical `.env`에서 Map/PinVi source root와 revision scalar의 source-selection keyset을 strict parser로
정확히 한 번만 읽으며, revision scalar는 unset 또는 tracked pin이어야 한다. source root·revision scalar는
owner-preserving atomic replace 하나로만 새 value로 수렴하고 다른 bytes는 보존한다.

env replace 전 old env의 root-private `0600` backup과 secret-free journal을 fsync한다. journal은 old/new env
SHA, owner identity, old/new root, pin/tree evidence 및 `prepared → env_replaced → committed` phase를 담는다.
crash resume은 env가 old/new SHA 중 하나인 경우에만 진행하고, foreign/corrupt/non-terminal journal·backup·
worktree는 cleanup과 모든 pair mutation을 막는다. F1E는 Docker, Compose, DB, runtime inspect/recreate,
image build를 호출하지 않는다.

### 근거

- source owner의 repository-local configuration을 root Git process에서 분리하면 cache checkout이 신뢰 경계를
  우회하지 못한다.
- full SHA와 canonical URL을 code-owned authority로 두면 current HEAD, branch, tag, URL alias가 release source를
  바꾸지 못한다.
- source-root와 revision scalar를 하나의 keyset으로 바꾸면 다음 normal builder가 같은 clean pinned HEAD를
  유일 provenance로 파생한다.
- private backup과 durable journal이 없으면 atomic replace/fsync 경계 crash에서 secret-bearing old env를
  안전하게 복구할 수 없다.

### 결과

- F1D는 source cache의 stale HEAD와 무관하게 root-owned exact build input을 받는다.
- production runtime/DB/manifest를 전혀 바꾸지 않은 상태에서 source authority만 독립적으로 수렴한다.
- non-terminal source installation residue가 subsequent pair mutation을 허용해 crash state를 덮는 일이 없다.

## ADR-33: release pin 교체는 source와 runtime contract input을 같은 generation으로 설치한다

- 상태: partially superseded (F1D runtime/recovery와 legacy state 부분은 ADR-34, 2026-08-06)
- 날짜: 2026-08-05
- 결정자: human, Codex

### 컨텍스트

F1D의 static candidate gate가 live Map application DB head
`0083_nonderived_uuid_generator`와 v1 Map release pin `c0af…`의 static head
`0082_legacy_write_fence` 불일치를 mutation 전에 잡았다. 그 뒤 Map release만 바꾸면
PinVi가 요구하는 service OpenAPI SHA와 Map functional owner가 예전 canonical `.env` 값에 남고,
Map API entrypoint의 migration expected head가 Compose source의 stale literal에 남는다. 반대로
각 값을 raw `.env`나 Compose로 순서대로 바꾸면 source, contract, image head가 서로 다른 release를
가리키는 중간 상태를 durable하게 만들 수 있다.

현 source-only v1 installer도 그대로 재실행할 수 없다. committed journal은 new manifest revision과
다르면 거부되고, canonical env의 source root는 이미 root-owned immutable worktree라 user-owned input
검사에 맞지 않는다. 또 static F1D journal은 terminal frozen input을 새 pin과 비교해 future re-pin을
영구 차단할 수 있다.

### 결정

`CACHE_TARGET_PRODUCTION_PINS` v2는 Map/PinVi exact release와 cache-target service contract뿐 아니라
Map application Alembic head를 한 input generation으로 기록한다. Map functional owner와 reviewed PinVi
candidate는 control-plane pin에서 제거한다. cache-target source identity는 Map **release** 하나로 정규화해
PinVi expected source revision, Map artifact provenance, candidate source 모두 같은 exact SHA를 쓴다. pinset
identity는 canonical manifest serialization의 SHA-256이며 모든 v2 durable receipt가 이 identity를 기록한다.

PinVi release는 versioned upstream metadata(Map release, service artifact SHA, contract generation)를 source에
포함하고 vendored service OpenAPI bytes와 대조한다. Manager는 trusted exact Map/PinVi worktree에서 Map
artifact hash, PinVi vendored bytes와 metadata, manifest pinset을 read-only로 교차검증한 뒤에만 source input을
설치한다. trusted installer는 source-only v1
receipt를 재사용하지 않고 versioned v2 journal/backup/worktree에서 Map·PinVi source root/revision,
Map expected migration head, PinVi expected OpenAPI SHA/source revision/generation을 한 번의
owner-preserving atomic canonical-env replace로 설치한다. Map Compose는 hard-coded head를 가지지 않고
이 required scalar를 사용한다.

v2 rotation preflight는 current env가 arbitrary old state가 아니라 validated v1 terminal receipt의 exact
predecessor pinset임을 확인할 때만 실행한다. v1 root-owned immutable target은 verified predecessor로만
수용하며, 임의 root-owned path 또는 revision scalar는 수용하지 않는다. v1 terminal receipt는 감사용으로
남지만 v2 F1D authority가 아니며, v1 non-terminal 또는 foreign residue는 기존처럼 pair mutation을 막는다.

F1D journal도 pin fingerprint별 versioned history로 rotate한다. non-terminal journal은 새로운 generation을
막고, terminal journal은 validation 후 immutable history path로 receipt-first archive한 뒤 새 generation
journal만 연다. F1D는 v2 committed input evidence와 candidate/live static head equality를 모두 요구한다.
input journal과 backup 자체도 `history/<pinset_sha256>` immutable generation으로 보관한다. 후속 rotation은
predecessor input의 exact `new_env_sha256`, root-owned exact worktree tree와 archived F1D frozen-env digest를
모두 다시 대조한다. 그래서 B pinset rollback 뒤 canonical env가 A v2 input으로 돌아온 재시도도 legacy v1로
오인하지 않고 A receipt를 검증해 B generation을 다시 연다. input install은 F1D handoff pending을 durable하게 기록한다. pending 중 일반 pair mutation과
diagnostic/enable/writer-drain은 거부하고 같은 pinset F1D만 시작할 수 있다. rotation은 모든 relevant durable
state가 terminal이고 prior F1D가 terminal history로 archive된 경우에만 시작한다. installer와 artifact verifier는
Docker, Compose, DB, runtime, image build를 실행하지 않는다.

### 근거

- release source, cache-target contract, Map entrypoint head는 모두 candidate가 실제 기동되기 전에
  일치해야 하는 하나의 deployment input이다.
- predecessor proof와 versioned durable state는 새 pin이 기존 terminal receipt의 의미를 바꾸거나 old
  backup을 덮는 것을 막는다.
- required Compose interpolation은 release head를 소스 코드 literal과 canonical env 두 곳에서 따로
  관리하는 drift를 제거한다.
- one-sided Map/PinVi 상수 변경을 exact source artifact verifier가 candidate build 전에 차단한다.

### 결과

- F1D는 schema-ahead live DB에 stale candidate를 기동하거나, 새 image에 구 contract를 주입하지 않는다.
- PinVi vendor release가 확정된 뒤에만 Manager manifest가 exact pair를 기록하므로 placeholder SHA 또는
  guessed revision을 production authority로 만들지 않는다.
- future release rotation도 terminal historical receipt를 보존하면서 같은 procedure로 재실행할 수 있다.

## ADR-34: 비운영 deployment는 완전한 runtime generation과 새 schema로 재구축한다

- 상태: partially superseded (application-300 candidate·manifest/journal은 ADR-39, 2026-08-25)
- 날짜: 2026-08-06
- 결정자: human, Codex

### 컨텍스트

n150은 production 형식의 인증과 Compose contract를 시험하지만 운영 서비스가 아니다. 이전 F1D는
Map 네 service와 PinVi API만 image pair에 기록했고 PinVi Web·Dagster는 같은 source/PinVi DB를 쓰면서도
generation 밖에 남았다. 이 상태에서 old F1D journal·old manifest·중간 DB head를 복구하려 하면 새
release pin installation이 non-terminal receipt에 막히고, 반대로 다섯 service만 다시 기동하면 PinVi
auxiliary runtime이 다른 source/schema를 계속 사용할 수 있다.

현재 데이터는 final schema에 맞춘 source/ETL 재적재로 재생성할 수 있다. 따라서 backup/restore와
image rollback으로 중간 상태를 보전하는 것은 이 환경의 correctness를 높이지 않는다.

### 결정

typed environment/lifecycle pair `KTDM_DEPLOYMENT_ENVIRONMENT=rehearsal`,
`KTDM_DEPLOYMENT_LIFECYCLE=rebuildable`에서만 root execution의
`sudo -n ktdctl pinvi-pair rebuild-pinned --confirm`을 제공한다.
유효 pair는 `local/development`, `rehearsal/rebuildable`, `production/operational`뿐이다. 따라서 기존
production environment에 rebuildable 값 하나를 추가해 destructive command를 열 수 없다. 기존
`bootstrap-pinned-drift`는 제거하며, compatibility flag나 old journal 변환은 만들지 않는다.

command는 tracked `CACHE_TARGET_PRODUCTION_PINS`와 trusted source staging만 authority로 삼아 Map API,
UI, Dagster web, Dagster daemon 및 PinVi API, Web, Dagster의 일곱 image를 하나의
`PinnedRuntimeGeneration` v5로 build·attest한다. v5 manifest와 rebuild journal은 일곱 immutable image
ID, Map/PinVi exact source revision, Map application/Dagster와 PinVi schema head, pinset digest를 함께
기록한다. v5 manifest는 `active_generation` 하나만 기록하며 old v4 manifest/image/state는 authority가 아니다.

Manager는 source·Compose를 검증해 일곱 candidate image의 immutable ID/provenance와 세 expected schema head를
먼저 `candidate_attested` journal에 durable하게 고정한다. 그 뒤 `reset_intent_durable` receipt를 남기고
일곱 runtime을 중지한 다음 frozen resolved Compose로 확인한 Map application·Map Dagster·PinVi database만
drop/create 한다. shared
PostgreSQL의 Geo·Concierge database와 RustFS를 변경하지 않는다. backup, dump, restore, old head
comparison은 사용하지 않는다. Map API가 Map application migration의, Map Dagster candidate migration-only
command가 Map Dagster migration의 유일 owner다. Map Dagster head는 source revision으로 추정하지 않고 candidate
image가 직접 출력한 Dagster dependency storage head다. 그 command가 `dagster instance migrate`를 실행한 뒤
strict single-row `public.alembic_version`을 해당 head와 대조하고, Map application head도 별도로 대조한 뒤
Map dependents를 기동한다. PinVi의 `pinvi-admin-bootstrap` one-shot CLI가 PinVi migration을 `pinvi_head`까지
적용·검증하고 fresh admin을 만든 뒤 normal API·Web·Dagster를 기동한다. F1J fixture canonical smoke와 UI
contract까지 검증한 뒤 committed한다.

fresh PinVi DB admin은 credential file만 읽는 candidate API image의 별도 one-shot CLI가 migration 뒤 만든다.
Manager는 frozen smoke credential으로 owner-only `0600` file을 만들고 one-shot container에 read-only mount하며,
container 종료 뒤 안전하게 unlink한다. normal API/Web/Dagster·Map service·Docker metadata·journal/log에는
원문을 전달하거나 기록하지 않는다.

재실행은 동일 pinset이면 durable phase를 따라 계속한다. 새 pinset rebuild는 Manager가 소유한 typed legacy
path allowlist에서만 parent `0700`, file `lstat` regular/owner/`0600`/link count/bounded size와 `O_NOFOLLOW`
open의 same-inode를 검증한다. 그 뒤 raw digest를 tombstone receipt에 fsync하고 same `dir_fd` unlink 후
reader와 mutation gate를 v5 authority로 교체한다. foreign/corrupt legacy state는 fail-close하며 arbitrary file
삭제나 shell/SQL input은 허용하지 않는다. 실패 시 old runtime/DB를 복원하지 않고 candidate 일곱 runtime을 중지한다.

### 근거

- PinVi의 API·Web·Dagster를 하나의 source/DB generation으로 결박해야 실제 배포 단위를 완결할 수 있다.
- explicit `rebuildable` lifecycle은 production-shaped test contract와 실제 운영 데이터 보전 정책을
  구분하면서 accidental destructive command를 차단한다.
- scoped database identity와 Manager-owned discard receipt는 raw SSH/Docker/SQL/state deletion보다
  검증·재현·감사가 쉽다.
- final schema부터 source/ETL을 재적재하면 schema transition마다 backup compatibility를 유지하는
  복잡도가 사라진다.

### 결과

- F1D는 stale DB head와 non-terminal old receipt를 되살리는 대신 exact pinned generation과 새 schema를
  만드는 단일 경로가 된다.
- 모든 Map·PinVi application runtime이 manifest 안의 immutable generation으로 수렴한다.
- 일반 운영 lifecycle은 destructive rebuild command를 사용할 수 없고, data recovery는 final-schema
  backup 또는 source/ETL 재적재 workflow로 분리된다.

## F1G: legacy terminal window는 receipt-first 퇴역 뒤 새 v2 authority만 수용한다

> 상태: superseded — ADR-34/F1D-B가 legacy window와 inert diagnostic을 v5 typed tombstone
> allowlist 하나로 흡수했다. 아래 기록은 당시 v2 판단의 이력일 뿐, 별도 T-VN-41 실행 경로나
> predecessor authority가 아니다.

### 컨텍스트

n150의 F1F input installer preflight는 old `cache-target-window-v1.json`이 terminal `rolled_back` 상태로
남아 있음을 발견했다. 이 schema는 현재 writer-drain lease/receipt와 v2 rollback evidence를 표현하지 못해
새 F1F/F1D transaction의 predecessor로 읽을 수 없다. 운영 데이터는 중간 cutover state 보존보다 final schema의
backup/restore와 source ETL 재생성을 우선하므로, raw directory 삭제나 v1→v2 자동 변환도 정본을 만들지 못한다.

### 결정

production Manager는 `retire-legacy-window --confirm`으로 exact owner-only v1 `rolled_back` journal만 퇴역한다.
command는 raw SHA와 phase만 가진 root-owned receipt를 atomic fsync하고 재검증한 뒤 source journal을 unlink한다.
receipt가 이미 있으면 같은 SHA/phase일 때만 unlink cleanup을 idempotently 재개한다. nonterminal/다른 terminal
phase, v2, malformed/foreign file, receipt conflict는 모두 실패한다. global lock과 frozen canonical input 검증은
유지하며, raw Compose source는 bytes와 file identity만 동결·재검증한다. F1F input이 아직 없는 old production env를
candidate로 materialize하지 않으므로, Docker·Compose·DB·runtime·manifest·backup은 mutation 대상이 아니다.

### 결과

- v1 crash-state를 새 release authority로 오인하거나 수동 삭제로 감사 증거를 잃지 않는다.
- F1F installer는 명시적으로 퇴역한 legacy state 뒤에만 canonical env와 source authority를 교체한다.
- 현재 서비스 데이터는 변경하지 않으며, F1D의 destructive runtime transaction은 F1F first-run 검증 뒤에도
  별도 단계로 남는다.

## F1H: writer-drain 전 inert v2 diagnostic은 별도 receipt로만 퇴역한다

> 상태: superseded — ADR-34/F1D-B가 legacy window와 inert diagnostic을 v5 typed tombstone
> allowlist 하나로 흡수했다. 아래 기록은 당시 v2 판단의 이력일 뿐, 별도 T-VN-41 실행 경로나
> predecessor authority가 아니다.

### 컨텍스트

n150에서 F1C의 v1 diagnostic을 퇴역한 뒤 시작한 v2 diagnostic은 stale runtime tuple을 발견해
`writers_fencing`에서 멈췄다. typed journal의 `external_event_count`는 `0`이며 writer-drain lease/receipt와
writer fence, 역할별 stage receipt, runtime smoke, failure/completion evidence도 없다. 즉 writer stop이나 DB
side effect가 시작되지 않은 inert state이지만, F1F installer는 non-terminal diagnostic을 정확히 차단한다.

### 결정

production Manager는 `retire-inert-diagnostic --confirm`으로 exact current v2 `prepared` 또는
`writers_fencing` journal만 퇴역한다. typed reader의 exact schema와 strict scalar type(`version`은 exact `int` 2,
`started_at_unix`는 `bool`이 아닌 양의 `int`) 검증 뒤 모든 writer/post-stage evidence가
비어 있고 external event가 `0`일 때만, source version·raw SHA·phase를 담은 전용 receipt를 atomic fsync하고
source journal을 재검증한 후 unlink한다. companion path는 F1C legacy receipt와 분리한
`cache-target-diagnostic-inert-retirement-v1.json`이고, source가 없을 때는 이 receipt의 같은 source version/SHA/phase만
cleanup을 재개한다. writer evidence, stage receipt, runtime smoke, external event, failure/completion evidence, 이후
phase, 다른 schema, foreign file 또는 receipt conflict는 모두 fail-close한다.

F1G와 동일하게 global lock 아래 frozen canonical env와 raw Compose source bytes/file identity만 동결·재검증한다.
candidate materialization, Docker/Compose/runtime/DB/manifest/backup/credential 및 일반 mutation gate는 바꾸지
않는다. 따라서 이 명령은 pre-writer inert journal 하나만 auditably 퇴역할 수 있고, 실제 실행이 시작된
diagnostic의 recovery/termination을 추측하지 않는다.

### 결과

- F1F input rotation은 안전하지 않은 active diagnostic을 계속 차단하되, stale inert state가 영구 차단기가 되지
  않는다.
- raw deletion이나 v2→terminal 변환 없이 final F1D authority를 향한 durable state 경계를 유지한다.

## F1I: F1D fail-close의 안전한 checkpoint 관측성

### 컨텍스트

n150에서 F1D `bootstrap-pinned-drift`는 동일 frozen candidate를 두 번 activation한 뒤 five-runtime을
halt했다. bootstrap manifest는 여전히 old pair이고 F1D journal phase도 `prepared`라 same-pinset resume은
가능하지만, 기존 CLI는 post-mutation exception·Compose output·credential의 노출을 막기 위해
`halted_requires_operator`만 반환했다. 이 형식만으로는 candidate의 어느 bounded operation이 반복 실패했는지
구별할 수 없어, 안전한 재개 판단보다 무의미한 재시도가 먼저 일어난다.

### 결정

F1D는 activation·candidate re-verification의 closed allowlist checkpoint를 journal에 durable하게 기록한다.
각 checkpoint는 side effect 또는 검증 직전에 atomic write+fsync한다. `attempt_checkpoint`는 현재 시도 중인
operation의 audit field일 뿐 phase resume cursor가 아니며, same-pinset 재실행은 항상 기존 phase의 full
activation/verification을 다시 수행한다. failure catch는 raw exception을 기록하거나 출력하지 않고,
`last_failure_checkpoint`·UTC 시각·strict integer failure count만 journal에 쓴 뒤 protected runtime halt를
시도한다. 새 attempt checkpoint write는 이전 failure evidence를 지우지 않는다. validator는 failure checkpoint와
시각의 nullability, non-bool count의 0/양수 관계, checkpoint allowlist와 최대 count를 엄격히 검증한다.

`stop_pair` 실제 호출 전에는 이번 process가 새 `prepared` journal을 fsync했는지와
`runtime_mutation_started`를 함께 판단한다. 새 journal의 첫 checkpoint fsync만 runtime을 건드리지 않은
pre-mutation error로 끝내며 halt하지 않는다. 이미 존재한 base/extended v2 journal은 checkpoint가 없어도 과거
mutation 미시도의 증거가 아니므로 보수적으로 halt한다. `stop_pair` 호출 직전 true가 된 뒤 checkpoint 또는
failure evidence persistence가 실패하면, persistence failure가 원래 cause를 가리지 않더라도 독립 `finally`
경로로 반드시 halt한다. halt failure는 original activation checkpoint evidence를 대체하지 않는다. CLI JSON은
exception이 아닌 safe enum 전용 exception attribute를 통해 allowlist checkpoint·count·halt state만 반환한다.

현재 production의 base v2 journal은 F1D가 이미 만든 frozen candidate authority다. reader는 이 exact base
shape를 `attempt checkpoint 없음/실패 없음/count 0`으로만 normalize하며, 새 extended v2 shape는 모든 diagnostic
field가 있고 type·enum·timestamp·count 관계가 정확한 경우만 수용한다. 임의 partial field나 다른 shape는
fail-close한다.
첫 same-pinset resume은 기존 lock과 frozen-input 검증을 통과한 뒤에만 extended shape를 fsync한다. version을
느슨하게 해석하거나 old candidate/manifest를 fallback으로 삼지 않는다.

### 결과

- fail-close는 비밀값을 보존·노출하지 않으면서 재현 가능한 operator evidence를 남긴다.
- n150의 현재 `prepared` v2 transaction은 upgrade parser 때문에 차단되지 않고, 동일 candidate의 유일한
  resume authority를 유지한다.
- journal 기록 실패와 halt 실패 모두 후보 runtime을 계속 실행하게 만들지 않으며, generic mutation/rotation
  차단도 변하지 않는다.

## F1J: cancel probe는 Map 소유 transaction-scoped fixture lifecycle로 만든다

### 컨텍스트

F1I가 n150의 마지막 F1D attempt를 safe checkpoint로 분리했다. PinVi login, ETL summary, provider-sync는
모두 성공했지만 static `KTDM_C6C_CANCEL_PROBE_JOB_ID`를 이용한 cancel probe만 `404`였다. Manager는 UUID의
문법을 검증하고 PinVi relay에 전달할 뿐, 그 UUID에 대응하는 Map `import_job`·cancellation attempt를
생성하거나 정리하지 않는다. 따라서 같은 frozen candidate를 재시도해도 real cancellation contract가 생기지
않고, `409/502/503`을 모두 success로 수용하는 기존 smoke는 Dagster outage를 false-green으로 만들 수 있다.

### 결정

Map API가 fixture lifecycle의 유일한 owner다. Map migration은 `ops.c6c_cancel_probe_fixtures`를 추가하여 F1D
durable transaction ID와 Map-generated `job_id`, lifecycle state(`armed`, `consumed`, `finalized`), canonical
cancellation identity·UTC를 FK, unique key, exact `CHECK`로 결박한다. Map은 dedicated `ops:fixture` principal만
받는 internal `ensure`, `read`, `finalize` lifecycle API를 제공한다. Map startup hook, Manager direct DB,
`docker exec`, generic job API 또는 static fixture UUID로 이 record를 만들지 않는다.

Manager는 candidate Map migration·authenticated readiness 뒤 PinVi smoke 전에 `ensure`를 호출해 dynamic job ID를
durable receipt에 기록한다. `armed`일 때만 PinVi의 현행 normal cancel relay를 한 번 호출한다. normal Map
cancellation path는 fixture record를 원자적으로 `consumed`로 바꾸고 canonical
`409 PIPELINE_CANCELLATION_UNSAFE` detail을 남긴다. Manager는 이 single response와 root ID만 성공으로
인정하고, response가 유실된 crash recovery에서는 Map lifecycle state를 읽어 same transaction POST를
재전송하지 않는다. exact response가 검증된 뒤 Map `finalize`만 fixture job을 terminal로 정리하며 cancellation
attempt, member, `cancellation_id`와 receipt는 삭제하지 않는다.

이 lifecycle capability generation은 PinVi가 소유하는 일반 Map service provenance artifact의 capability다.
artifact는 Map release revision·service OpenAPI SHA와 `cache_target`/`c6c_cancel_probe` generation을 함께
선언하며, Manager는 trusted PinVi source의 그 정확한 bytes를 기존 Map artifact·cache-target pin과 교차
검증한다. 기존 compatible-pair manifest v4에는 F1J 전용 필드를 추가하지 않는다. Manager는 Map candidate에
endpoint/generation이 없거나 PinVi provenance·Map artifact·pin이 서로 다르면 mutation 전에 fail-close한다.
따라서 이전 image fallback으로 endpoint 부재를 숨기지 않는다. PinVi에는 fixture token이나 생성 endpoint를
주지 않으며 existing relay의 structured error preservation만 회귀로 확인한다.

### 결과

- F1D가 존재하지 않는 execution을 취소해 `404`가 되는 상태와 Dagster outage를 success로 오인하는 상태를
  동시에 제거한다.
- fixture UUID와 lifecycle row는 Map에만 있고, Manager journal은 안전한 transaction/job identity와 검증
  receipt만 보관한다. 재시작·response-loss·finalize 중단도 각 상태를 명시적으로 재개하거나 fail-close한다.
- Map schema/API 변경은 F1J-A, pair provenance 재결박은 F1J-C, Manager orchestration은 F1J-B로 분리해
  독립 리뷰·검증 가능하게 한다.

## ADR-35: Map ADR-090 principal은 전용 PostgreSQL instance에서만 bootstrap한다

- 상태: superseded (ADR-37, 2026-08-17; Map principal 경계 원칙은 유지)
- 날짜: 2026-08-12
- 결정자: 사용자, Codex
- 관련: #171, ADR-5, ADR-9, ADR-16, ADR-34, Map ADR-090

> 이 ADR은 Map principal을 전용 instance에서 bootstrap한다는 경계를 보존한다. Map만 분리하던
> 포트·통합 instance 서술은 ADR-37이 네 프로젝트 전용 instance와 `12500`/`12600`/`12700`/`12800`
> 정책으로 대체했다.

### 컨텍스트

Map ADR-090은 `ktm_feature_migrator`, `ktm_feature_api_runtime`,
`ktm_feature_dagster_runtime`과 NOLOGIN ownership group을 분리하고, DB owner·schema·extension·ACL을
전용 superuser로 bootstrap한다. 기존 `kor-travel-geo-postgres`는 Geo·Concierge·PinVi와 legacy Map
database를 함께 관리하며, recovery script가 `krtour_map` owner와 광범위한 grant를 다시 적용한다.
따라서 shared instance에서 bootstrap하면 Map 권한 경계를 깨거나 정상 recovery가 그 경계를 무음으로
되돌린다. host network에서는 두 PostgreSQL이 `5432`를 동시에 bind할 수도 없다.

### 결정

Map application database와 Map Dagster metadata database는 `kor-travel-map-postgres` 전용 PostGIS
instance로 분리한다. 이 instance는 host network loopback `127.0.0.1:12703`만 listen하며 Map target
대역을 사용한다. 통합 `kor-travel-geo-postgres:5432`는 Geo·Concierge·PinVi lifecycle만 계속 소유하고,
`kor_travel_map` 또는 Map role·schema·extension을 생성·복구·reset하지 않는다.

F1D v5는 frozen Compose에서 Map 두 database runtime을 전용 instance로, PinVi runtime을 통합 instance로
각각 유도한다. reset은 세 DB만 drop/create하며 Map application의 schema/extension/role provisioning은
Manager가 만들지 않는다. reset 직후 Map 정본 `postgres-role-bootstrap.sh`를 profile one-shot으로 실행하고,
성공한 경우에만 Map API migration과 Dagster storage migration을 실행한다. one-shot은 `--rm`으로 종료해
bootstrap superuser DSN과 세 role password가 normal runtime, journal 또는 잔존 container metadata에 남지
않게 한다. bootstrap profile은 일반 `compose up`에서 비활성화하며, 일반 `ensure`는
role/password/owner/ACL mutation을 절대 실행하지 않는다. 초기화·복구는 F1D reset transaction만 담당한다.

Map API에는 migrator DSN과 API runtime DSN만 전달한다. Map Dagster·daemon·storage migration에는 Dagster
application runtime DSN과 별도 non-superuser metadata login의 metadata DSN만 전달한다. bootstrap DSN과 raw
role password는 one-shot service의 유일한 입력이다. Compose candidate validator는 실행 전 DSN의
`127.0.0.1:12703` endpoint·database·principal과 host network를 frozen Compose identity에 결박하고,
raw/resolved secret reference도 검증한다. F1D는 bootstrap 뒤 database owner, role attribute, PostgreSQL 16
membership option, schema·relation·routine·type owner, extension schema, runtime/default ACL을 catalog에서
assertion하며 통과하지 못하면 migration을 시작하지 않고 fail-close한다. 이 assertion은 빈 application DB의
bootstrap invariant만 검사한다. Map migration이 허용된 runtime relation ACL을 만든 뒤에는 durable cancel
fixture를 보존하는 resume에서 pre-migration assertion을 반복하지 않는다. cancel probe가 아직 시작되지 않은
resume은 checkpoint와 무관하게 DB reset과 두 Map bootstrap one-shot을 다시 실행한다.

전용 PostgreSQL의 초기 superuser password는 long-lived container의 `Config.Env`에 넣지 않는다.
Compose secret file `POSTGRES_PASSWORD_FILE`로만 초기화 entrypoint에 전달하며, Docker inspect에는 secret
reference만 남고 password/DSN 원문은 남지 않는다. 이 secret alias는 PostgreSQL entrypoint의 정확한
mount 한 곳에서만 소비할 수 있으며 API·Dagster·PinVi 또는 one-shot의 추가 mount는 raw/resolved Compose
검증에서 fail-close한다. F1D는 database reset 전에 실제 PostgreSQL container의 `Config.Env`를 다시 inspect해
`POSTGRES_PASSWORD` 부재와 정확한 `POSTGRES_PASSWORD_FILE`만 허용한다. inspect 대상은 고정 이름이 아니라
frozen resolved Compose와 `compose ps`가 함께 확인한 Map PostgreSQL의 실제 singleton `Name`이다. bootstrap
one-shot만 같은 credential을 포함한 DSN을 받고 `--rm`으로 종료한다.

새 Map image 및 PinVi compatibility artifact는 upstream에서 merge된 exact revision만
`PINNED_RUNTIME_RELEASE`에 반영한다. draft source SHA나 `latest-main` tag는 production/rehearsal authority가
아니다.

### 결과

- Map ADR-090 privilege boundary가 공용 DB recovery와 독립되어 재현 가능하다.
- F1D destructive rebuild가 Geo·Concierge database, PinVi database, RustFS 또는 legacy shared Map DB를
  변경하지 않는다.
- Map release pin이 확정되기 전에는 구현·정적 검증까지만 허용되며, n150 live E2E와 Manager PR merge는
  exact pair authority가 준비된 뒤에만 진행한다.

## ADR-36: 운영 콘솔을 Cobalt Workbench 디자인 시스템으로 수렴한다

- 상태: accepted
- 날짜: 2026-08-13
- 결정자: 사용자, Codex
- 관련: ADR-17, #171

### 컨텍스트

ADR-17의 StyleSeed 라이트 토큰은 화면 공통 표면을 정리했지만, `DESIGN.md`에는 이전 BMW M 시각 기록이
남아 있고 실제 console은 동일한 KPI 카드, modal blur, 가로 스크롤 표, 차트의 개별 색상처럼 서로 다른
표현을 병행했다. 이 상태에서는 운영자가 조치·상태·보조 정보를 같은 우선순위로 읽게 되고, 작은 화면에서
컨테이너와 백업 원장을 안전하게 확인할 수 없다.

### 결정

Hallmark audit을 기준으로 modern-minimal 장르와 Workbench 구조를 채택한다. Cobalt theme의 semantic token은
`frontend/tokens.css` 한 곳에서 정의하고, display(Space Grotesk)·본문(IBM Plex Sans)·데이터(IBM Plex Mono)
서체 역할을 분리한다. 모든 화면은 이 token과 공통 `ops-*` 표면·버튼·modal·focus 상태를 사용한다. 임의
hex, 임의 shadow/radius, `transition-all`, 의미 없는 gradient/glass, 고객용 장식 자산은 새 UI에 넣지 않는다.

대시보드는 상단 명령, 상태 원장, graphite 동기화 신호, 서비스 원장, 단일 상태 footer 순서로 구성한다.
인라인 명령 팔레트는 장식이 아니라 인증 설정·백업 이력·새로고침·로그아웃에 연결한다. 서비스와 백업 표는
768px 이하에서 각 셀의 label을 보이는 행 카드로 바뀌며 가로 스크롤을 기본 상호작용으로 사용하지 않는다.
모든 modal은 같은 사각 작업 표면, Escape, 초기 focus, 명시적 닫기 버튼을 유지한다.

### 결과

- 상태 확인과 조치가 화면 전체에서 동일한 색상·타이포그래피·focus·disabled·loading 규칙을 따른다.
- 320px, 375px, 414px, 768px에서 운영 원장이 가로 스크롤 없이 읽히도록 구현·검증 대상이 명확해진다.
- #171의 PostgreSQL bootstrap, exact release pin, n150 live E2E/merge gate에는 영향을 주지 않는다.

## ADR-37: PostgreSQL은 프로젝트마다 전용 instance를 쓰고 DB 포트는 대역의 `x00`이다

- 상태: accepted
- 날짜: 2026-08-17
- 결정자: 사용자, Claude
- 관련: #176, ADR-35, ADR-5, ADR-16, Map ADR-090, `docs/ports.md`, `AGENTS.md` 룰 4·9

### 컨텍스트

ADR-35는 Map만 전용 instance로 뺐다. 그 근거는 "Map ADR-090의 principal 경계를 통합
instance의 recovery가 무음으로 되돌린다"였다. 2026-08-17 실측에서 그 근거가 Map에만
해당하지 않는다는 것이 드러났다.

**role·ACL·확장은 database가 아니라 cluster 전역이다.** Map을 전용 instance로 옮긴 뒤에도
통합 instance에는 `ktm_` role 7개가 남아 있었고, Map migrator credential로 33GB
`kor_travel_geo`에 실제로 접속됐다(`CONNECTED as ktm_feature_migrator`). database만
나누는 방식으로는 principal이 격리되지 않는다.

`scripts/ensure-kor-travel-geo-db.sh`가 그 구조를 고착시킨다 — 한 cluster 안에서
`pinvi` role과 `pinvi`·`kor_travel_concierge`·`krtour_map` database를 만들고 owner와
광범위한 grant를 재적용한다. 즉 통합 instance는 "여러 프로젝트가 database만 나눠 쓰는"
형태가 아니라 **모든 프로젝트가 서로의 principal namespace를 공유하는** 형태였다.

포트도 함께 정리해야 했다. host network에서는 두 PostgreSQL이 `5432`를 동시에 bind할 수
없고, `ports:`는 무시되므로 `-p`가 곧 호스트 포트다. 즉 instance를 늘리는 순간
"PostgreSQL은 표준 5432" 규칙은 유지가 불가능하다.

### 결정

**PostgreSQL instance는 프로젝트마다 하나씩 둔다.** 통합 instance는 폐지한다.

**DB 포트는 각 target 대역의 `x00`이다.** `docs/ports.md`의 `12000 + dependency index *
100 + offset` 규칙에서 DB의 offset을 `0`으로 고정한다.

| target | instance | 포트 |
|---|---|---|
| `geo` | `kor-travel-geo-postgres` | `12500` |
| `conc` | `kor-travel-concierge-postgres` | `12600` |
| `map` | `kor-travel-map-postgres` | `12700` |
| `pinvi` | `pinvi-postgres` | `12800` |

`AGENTS.md` 룰 9의 "PostgreSQL 접속 포트는 표준 `5432`를 사용한다"와 룰 4의 "통합
PostgreSQL(`5432`)"은 이 ADR로 대체한다. `db` target의 `12000-12099` 대역은 더 이상
"비워 두는" 대역이 아니라 **폐지된 통합 instance의 자리**다.

모든 instance는 `-c listen_addresses=127.0.0.1`로 loopback만 듣고,
`POSTGRES_INITDB_ARGS: --auth-host=scram-sha-256`으로 loopback 인증을 잠근다.
**둘 다 있어야 경계가 된다** — 아래 §되돌아본 것 참조. 크기 관련 설정(`shared_buffers` 등)은 각
프로젝트 데이터에 맞추되 **planner 성질**(`random_page_cost`)과 관측
설정(`pg_stat_statements.track/max`)은 통합 instance 값을 그대로 물려받는다 — 같은 쿼리가
instance마다 다른 계획을 타면 안 된다.

### 결과

- 한 프로젝트의 DB credential이 다른 프로젝트의 데이터에 닿지 못한다. 이것이 ADR-35가
  Map에 대해 얻으려던 것이고, 이제 네 프로젝트 모두에 적용된다. **단, instance를
  나누는 것만으로는 이것이 성립하지 않는다** — loopback 인증까지 잠가야 한다
  (§되돌아본 것).
- `ensure-kor-travel-geo-db.sh`는 **geo 전용**이 된다. 다른 프로젝트의 role/database를
  만드는 부분은 제거한다 — 그대로 두면 복구 실행이 이 ADR을 되돌린다.
- `config/docker-targets.yml`의 `expected_ports`·`connection`·`containers`가 instance
  4개를 반영해야 한다. 그 값은 운영자 대시보드에 그대로 표시된다.
- 배포 가드(`c6c_deployment.py`)의 Map 포트 상수는 compose 기본값과 같아야 한다.
  어긋나면 정상 배포가 `Map database DSN identity is invalid`로 막히고, 오류 문자열에
  포트가 없어 원인이 드러나지 않는다.
- 백업 주체가 instance 4개로 늘었다. 단일 instance 전제의 백업/복구 절차는 갱신이 필요하다.

### 되돌아본 것 (2026-08-17, 같은 날 적대 리뷰)

이 ADR의 초안은 "`listen_addresses=127.0.0.1`이 유일한 경계"이고 "한 프로젝트의
credential이 다른 프로젝트 데이터에 닿지 못한다"고 적었다. **둘 다 틀렸다.** 실측:

```
$ grep -v '^#' $PGDATA/pg_hba.conf          # 네 인스턴스 전부 동일
local   all  all                  trust
host    all  all  127.0.0.1/32    trust     <- 여기서 매칭이 끝난다
host    all  all  ::1/128         trust
host all all all scram-sha-256              <- entrypoint가 붙이지만 도달 안 함
```

`pg_hba.conf`는 first-match-wins다. postgres 공식 이미지는 `initdb`를 `--auth-host`
없이 부르고, 그때 initdb 기본값이 `trust`다. entrypoint가 파일 끝에 붙이는
`host all all all scram-sha-256`은 loopback 요청에 **절대 도달하지 않는다.**

네 인스턴스가 모두 host network를 공유하므로, 결과는 이렇다:

```
map 컨테이너 -> geo(12500) superuser addr, 33GB : CONNECTED   (비밀번호 없이)
geo 컨테이너 -> map(12700), features=1,008,852  : CONNECTED   (비밀번호 없이)
틀린 비밀번호로 접속                             : CONNECTED
```

즉 이 ADR이 얻으려던 principal 격리가 **0**이었다. 원래 사고(`ktm_` role이 통합
instance에 남아 Map credential로 geo에 접속됐다)보다 넓다 — 이제 credential조차
필요 없다. 이것은 분리로 **새로 생긴** 문제가 아니라(통합 instance도 같은 pg_hba였다)
분리가 **해결했다고 주장한** 문제가 그대로 남아 있었던 것이다. 더 나쁘다: 문서가
해결됐다고 말하면 아무도 다시 안 본다.

**대응 두 갈래.**

1. 새로 만드는 instance — 네 서비스에 `POSTGRES_INITDB_ARGS: "--auth-host=scram-sha-256"`.
   `--auth-local`은 건드리지 않는다. 유닉스 소켓은 컨테이너 파일시스템 안이라 밖에서
   못 닿고, entrypoint의 initdb.d 스크립트가 그 경로로 붙는다.
2. **이미 초기화된 4개는 이 설정이 안 먹는다.** `POSTGRES_INITDB_ARGS`는 initdb 때만
   쓰인다. 기존 인스턴스는 `pg_hba.conf`의 `127.0.0.1/32`·`::1/128` 줄을
   `scram-sha-256`으로 바꾸고 reload해야 한다.

전환 전에 "지금 trust 덕에 살아 있는 DSN"이 있는지 확인했다. `pg_hba`를 건드리지 않고
`pg_authid.rolpassword`의 SCRAM verifier(`SCRAM-SHA-256$<iter>:<salt>$<StoredKey>:<ServerKey>`)를
DSN 비밀번호로 재계산해 대조하는 방법이다. `.env`와 **해결된 compose**를 둘 다 훑어
8건 전부 PASS(불일치 0)를 받고 전환했다.

여기서도 같은 함정을 한 번 밟았다 — 1차 검사가 `.env`만 훑어 map 5건만 잡았다.
geo/concierge/pinvi의 DSN은 `.env`에 없고 compose의 `${VAR:-기본값}`에서 온다.
**실제 해석자(`docker compose config`)에게 물어야 한다.**

reload는 기존 연결을 재인증하지 않는다. 그래서 전환 직후에는 아무 일도 일어나지 않고,
재연결·재시작 때 드러난다 — "바꿨는데 멀쩡하다"를 성공으로 읽으면 안 된다.

---

## ADR-38: `pinvi-pair capture`는 컨테이너를 관측만 하고 C7 runner용 v4 manifest를 원자적으로 갱신한다

- 상태: superseded (2026-08-26, application `300` v6/v8 단일 정본)
- 날짜: 2026-08-19
- 결정자: 사용자, Claude
- 관련: ADR-31, ADR-34, Map `docs/runbooks/c7-prod-live-e2e.md` §2.1 step 8, Map `scripts/lib/c7_prod_attestation.py`

> 2026-08-26 H300의 current authority는 seven-service v6
> `pinned-runtime-generation`과 v8 rebuild journal로 바뀌었다. F1D legacy tombstone 대상인
> `compatible-pair-v4.json`을 다시 만들거나 attestation하는 경로는 current candidate를
> 증명하지 못하므로 `pinvi-pair capture` CLI와 구현을 퇴역했다. 아래 내용은 당시의
> 안전·복구 판단을 보존하는 역사 기록이며 현 운영 절차가 아니다. **아래 모든 명령·옵션·경로·표는
> 실행·복사·프로비저닝해서는 안 된다.** 정확한 merged trusted Manager release에서 `capture`가
> parser 단계에서 거부되는지 확인한 뒤에만 v6/v8 current authority를 사용한다.

> ## ⚠️ 퇴역 전 설치본 관측 기록 — 실행 금지
>
> 2026-08-19 실측: n150 설치본은 revision
> `4191582779be47e9605a324ea27adbb99b438439`이고 그 트리에
> `services/c6c_pair_capture.py`가 **없다**. 거기의 `pinvi-pair capture`는 **이름만 같은 옛 v4
> 파괴형 명령**으로, Map 넷과 PinVi API를 stop한 뒤 candidate image로 force-recreate한다
> (`--help` = `[--build] [--wait-timeout] [--verified-compatible] [--json]`,
> `pinvi-pair --help` = `{install-pinned-sources,bootstrap-pinned-drift,deploy,capture,rollback}`).
>
> 이 관측은 퇴역 이전의 사고 예방 근거일 뿐, 이 ADR의 후속 명령을 실행할 근거가 아니다. stale
> 설치본에 `capture`가 보이면 그 하위 명령을 호출하지 않고 정확한 merged trusted Manager release를
> 먼저 설치한다. 설치 뒤에는 top-level 도움말에 `rebuild-pinned`만 남고 `capture`는 parser 단계에서
> 거부되어야 한다.

### 컨텍스트

Map 저장소의 C7 prod live E2E 런북 §2.1 step 8이
`ktdctl pinvi-pair capture --verified-compatible --build`를 부르는데, 그 명령이 Manager에
없었다. ADR-34(F1D v5) 정리에서 compatible-pair의 `capture`·`deploy`·`rollback` 공개
경로를 퇴역시키면서 v4 모델과 writer를 함께 지웠기 때문이다(`64069f7`).

지운 것은 Manager 내부의 **reader**뿐이어야 했다. `compatible-pair-v4.json`은 잔재가
아니라 **살아 있는 cross-repo 계약**이다. Map의 C7 runner가
`E2E_C7_COMPATIBLE_PAIR_MANIFEST`로 그 파일을 읽고
(`c7_prod_attestation.py` 635행, `secure_reader(manifest_path, 0o600)`), top-level 키가 정확히
`{active, rollback, version}`이며 `version == 4`여야 하고(436행) 두 pair가 각각 정확히 9개
필드여야 한다고 강제한다(`_validate_pair(manifest["active"])`/`["rollback"]` 439-440행,
`_validate_pair` 본체 313-347행). 즉 산출물의 소비자는 사람이 아니라 그 runner이며, 1차
산출물은 **manifest 파일 bytes 그 자체**여야 한다.

n150 실측(2026-08-19)에서 확인한 상태는 이렇다.

- `/var/lib/kor-travel-docker-manager/kor-travel-docker-manager/compatible-pair-v4.json`
  — root:root `0600`, mtime 2026-07-31, sha256 `f2051e42…`. `active.recorded_at`은
  `2026-07-27T00:10:48.824808+00:00`, `active.map_source_revision`은 `c8ed6164…`,
  `pinvi_source_revision`은 `6a035695…`.
- `/etc/kor-travel-map/c7-compatible-pair-v4.json` — root:root `0600`, **위 파일과
  byte-identical**(같은 sha256 `f2051e42…`). 오늘 C7 lane 스크립트
  (`/home/digitie/c7-lane-run-*.sh`, `c7-rerun-*.sh`)가
  `E2E_C7_COMPATIBLE_PAIR_MANIFEST`로 가리키는 것은 **이쪽**이다.
- 실제 실행 중 runtime — Map 네 image가 모두 revision
  `817cfeaed88207987af21e5d0e6d641df21dd9b4`, PinVi가 `5cad141a…`
  (`io.pinvi.build.environment=production`). 즉 기록된 두 파일의 `active`는 **실행 중
  이미지와 불일치**한다. 다섯 compose service 모두 `com.docker.compose.project=
  kor-travel-docker-manager`이고, `kor-travel-map-dagster-daemon`만 healthcheck가 없다
  (capture는 healthcheck 없는 컨테이너를 거부하지 않는다).

state root를 어떻게 정할지가 남은 문제였다. Manager에는 이미 규칙이 둘 있다 —
`c6c_state_paths`(production일 때 `/var/lib/kor-travel-docker-manager/<project>`, 아니면
`KTDM_C6C_STATE_ROOT` 또는 `Path.home()/.local/state/...`)와
`pinned_runtime_state_paths`(`KTDM_PINNED_RUNTIME_STATE_ROOT`만). 세 번째를 만들면 안 된다.

### 결정

**capture는 실행 중인 다섯 컨테이너를 읽기만 하고, operator가 절대경로로 지목한
manifest를 원자적으로 교체한다. state root 규칙은 새로 만들지 않고 소비자의
전제조건을 그대로 미러링한다.**

- **manifest 기본 경로는 `c6c_state_paths(frozen env)[0]`에서 유도한다.** 세 번째
  state root 규칙을 만들지 않는다는 목표를 여기서 정직하게 달성한다. 해결 순서는
  `--manifest-path` → `E2E_C7_COMPATIBLE_PAIR_MANIFEST`(runner가 읽는 이름) → 유도
  기본값이며, 그래서 manifest 입력이 "없어서" 거부되는 경로는 존재하지 않는다.
  두 checkout은 `--map-source-checkout`/`--pinvi-source-checkout` →
  `KTDM_C7_MAP_SOURCE_CHECKOUT`/`KTDM_C7_PINVI_SOURCE_CHECKOUT`이며, 둘 다 없으면
  거부하되 메시지가 **flag 이름과 env 이름을 모두 지목해** 막다른 길을 만들지 않는다.
- **`KTDM_C6C_COMPATIBLE_PAIR_MANIFEST`는 fallback으로 읽지 않는다.** 그 키를
  production `.env`에 넣으면 `c6c_state_paths`가
  `"production C6c manifest and global lock paths are fixed"`로 raise하고, 같은 함수가
  host-global lock 경로도 정하므로 capture만이 아니라
  `c6c_deployment_lock_from_environment()`를 잡는 **모든 Manager mutation**이 함께
  죽는다. capture가 그 키를 읽으면 "런북을 통과시키려면 그 키를 넣어라"는 잘못된
  조언을 유도하게 된다.
- **basename은 강제하지 않는다.** runner는 절대경로만 요구하고
  (`run-c7-prod-live-e2e.sh` 607행) 파일명 제약을 걸지 않으며, 오늘 C7 lane은
  `c7-compatible-pair-v4.json`을 쓴다. manager가 runner에 없는 제약을 만들 이유가 없다.
  대신 유지하는 것은 절대·정규 경로, symlink 아님, ancestor 전부 root:root 비-group/
  other-writable, 기존 파일이면 v4 loader 통과다.
- **동일 identity 재capture만 byte-멱등이다. 그 밖에는 §2.3 attestation 재생성이 필수다.**
  관측한 active identity(runner 9필드 중 `recorded_at` 제외)가 기존 파일의 `active`와
  완전히 같으면 기존 `recorded_at`을 보존한다. 매번 `now()`를 찍으면 아무것도 바뀌지 않은
  재실행이 이미 발급된 attestation을 깨뜨리기 때문이다. 그러나 멱등은 **좁은 특수
  경우**다 — ① 기존 manifest가 없는 **첫 capture**와 ② runtime이 바뀐 뒤의 capture는
  정의상 새 시각을 찍는다. runner는 `manifest_sha256`,
  `active.map_source_revision`, `active.pinvi_source_revision`(그리고
  `active.contract_generation`)을 **한 `if`에서 함께** attestation과 대조하므로
  (`c7_prod_attestation.py` 443-448행 — sha 444, generation 445, map revision 446,
  pinvi revision 447), 그 두 경우에는 런북 §2.3 attestation을 **반드시 다시 만들어야
  한다**. capture는 이 사실을 침묵하지 않는다: receipt의 `recorded_at_preserved`가
  `false`이고 `attestation_action`이 그 문장을 담으며, `--json` 없이 부르는 런북 호출의
  stdout에도 `recorded_at_preserved=false`와 `attestation_action=…` 두 줄이 나온다.
- **capture는 자기를 식별한다.** `CAPTURE_CONTRACT = "pair-capture-v1"`이
  `pinvi-pair capture --help`, 성공 stdout의 **첫 줄**, `--json` receipt의
  `capture_contract` 필드 세 곳에 같은 값으로 나온다. n150 설치본에 이름이 같은 파괴형
  `capture`가 남아 있는 동안(위 경고 블록) 이 문자열이 "지금 이 호스트에 설치된 것이
  관측기인가"를 **실행 없이** 판정하는 유일한 근거다.
- **`rebuild-pinned`가 쓸어가는 자리는 거부한다.** manifest 경로가
  `pinned_runtime_state_root(...)` 아래면 `capture_refused_precondition`이다.
  `rebuild-pinned`는 그 root에서 `f1d_legacy_artifact_paths()` — `compatible-pair-v4.json`
  포함 — 를 퇴역시키므로, 그 안을 runner의 read target으로 두면 rehearsal rebuild 한 번이
  attestation 입력을 지운다. n150 rehearsal에서는 두 root가 실제로 같은 디렉터리다.
- parent 체인은 runner의 `_read_secure_file`(111-162행, ancestor 술어 130-145행)과
  **같은 술어**로 검증한다 —
  각 ancestor가 디렉터리·비symlink·uid 0·gid 0·`mode & 0o022 == 0`. **capture는 절대
  mkdir하지 않는다.** 부모가 없으면 거부한다.
- 허용 docker argv는 세 종류의 읽기 전용 조회뿐이다:
  `docker compose --project-directory <dir> ps -q <service>`, `docker inspect -- <id>`,
  `docker image inspect --format=... -- <ref>`. `up`/`stop`/`start`/`rm`/`build`/`restart`는
  코드 경로에 존재하지 않는다.
- 실패해도 **컨테이너를 중지하지 않는다.** 런북 §2.1 step 1의 maintenance fence는 닫힌
  채 유지하고, typed terminal state + exit code로만 표현한다. 모든 non-zero 메시지는
  `maintenance fence stays closed; no container was stopped, started, or recreated.`로
  끝난다.
- `--build`는 **아무것도 빌드하지 않는다.** 런북 문구 호환을 위해 수락하고 stderr에 1줄
  고지하며 receipt에 `build_flag_accepted_no_op`로 기록한다.
- **v5 pinned generation과 대조하되 거부하지 않는다.** 같은 사실을 두 번 적는
  `pinned-runtime-generation-v5.json`이 있으면 읽어 다섯 image ID와 두 revision을
  관측값과 맞춰 보고 `pinned_generation_agrees`·`pinned_generation_divergent_roles`를
  receipt와 stdout 한 줄에 노출한다. prod Map 재배포의 sanctioned 경로가 host compose
  직접 실행이라 v5가 뒤처지는 것이 **정상 상태일 수 있으므로** 불일치는 거부 사유가
  아니다. 그 파일은 읽기만 하며 부모 디렉터리도 만들지 않는다.
- **contract generation 전환은 명시 동의를 요구한다.** 기존 manifest의
  `active.contract_generation`이 frozen `KTDM_C6C_CONTRACT_GENERATION`과 다르면 기본
  거부하고, 의도적 전환일 때만 `--allow-generation-change`로 통과한다.
- **교체되는 manifest의 pre-image를 receipt에 남긴다**: `previous_manifest_sha256`,
  직전 `active`의 9필드 identity(`previous_active`), `previous_recorded_at`. 무엇을
  덮어썼는지 사후에 확인할 수 없는 상태를 만들지 않는다. 런북은 `--json` **없이**
  부르므로 이 값들과 `rollback_images_present`·`side_effects`·`input_sources`는
  비-JSON stdout 블록에도 나온다. 특히 `rollback_images_present=false`("기록한 rollback
  pair를 복원할 수 없다")가 기본 출력에서 사라지면 안 된다.
- **되돌릴 수 없는 `os.replace` 전에** 쓰려는 bytes를 runner 술어로 재검증한다. 커밋 후
  재읽기가 실패하면 pre-image 스냅샷 복구를 시도하고, 복구 성공은
  `capture_write_rolled_back`, 실패는 `capture_write_indeterminate`로 구분한다.
- **git 하위 프로세스 env를 위생 처리한다.** 상속된 `GIT_DIR`/`GIT_WORK_TREE`/
  `GIT_CEILING_DIRECTORIES`/`GIT_OBJECT_DIRECTORY`/`GIT_ALTERNATE_OBJECT_DIRECTORIES`를
  제거해 `git -C <checkout>` 결박이 조용히 다른 저장소로 새지 않게 한다. git이
  `dubious ownership`으로 거부하면 "commit 없음"으로 뭉개지 않고
  `capture_refused_checkout_ownership`이라는 별도 terminal state로 알린다.

### 근거

1. **`c6c_state_paths` 유도가 정답이다 — 설치본이 읽는 env가 production이기 때문.**
   n150에는 manager `.env`가 둘이다. `/home/digitie/kor-travel-docker-manager/.env`는
   `KTDM_DEPLOYMENT_ENVIRONMENT=rehearsal` + `KTDM_DEPLOYMENT_LIFECYCLE=rebuildable`이고,
   `/opt/kor-travel-docker-manager/.env`는 `KTDM_DEPLOYMENT_ENVIRONMENT=production`,
   `COMPOSE_PROJECT_NAME=kor-travel-docker-manager`,
   `KTDM_C6C_CONTRACT_GENERATION=c6c-ops-v1`이며 `KTDM_DEPLOYMENT_LIFECYCLE`이 **없다**.
   실제 설치본은 `/opt/kor-travel-docker-manager/backend/.venv/bin/ktdctl`이고 그 shim이
   `KOR_TRAVEL_DOCKER_MANAGER_PROJECT_ROOT=/opt/kor-travel-docker-manager`를
   하드코딩하므로 `get_env_path()`는 **`/opt` 쪽 `.env`**를 읽는다. 따라서 production
   분기에서 `c6c_state_paths`는 정확히
   `/var/lib/kor-travel-docker-manager/kor-travel-docker-manager/compatible-pair-v4.json`을
   돌려주고, **그 파일은 root:root `0600`으로 이미 존재한다**. 새 state root가 생긴다는
   초판의 서술은 거짓이었다 — 거기서 인용한 `rehearsal`/`rebuildable`은 capture가 읽지
   않는 `/home/digitie` 쪽 파일의 값이다.
2. **runner env 이름은 override로 남긴다.** runner도
   `E2E_C7_COMPATIBLE_PAIR_MANIFEST`라는 operator 지정 절대경로를 받아 같은 술어로 연다.
   유도 기본값과 다른 파일을 정본으로 삼고 싶을 때 쓰는 손잡이가 그 이름이면, 정책의
   정본은 여전히 runner 한 곳에 가깝다. ancestor 술어는 두 후보 모두 오늘 통과한다 —
   `/var/lib/kor-travel-docker-manager{,/kor-travel-docker-manager}`는 root:root `0700`,
   `/etc/kor-travel-map`은 root:root `0755`(group/other write 비트 0)다.
3. **healthy prod를 죽이는 것은 미승인 mutation이다.** 옛 v4 capture는 실패 시
   `_halt_c6c_pair`로 pair를 내렸다. 읽기 전용 검증기에는 되돌릴 mutation이 없으므로
   rollback도 없다.

### 결과(긍정)

- 런북 step 8이 **인자 없이 문자 그대로** 실행 가능한 **코드 경로**가 생겼고, 산출물이
  runner의 전수 검증(shape·정규식·소유권·mode)을 그대로 통과한다. 검증 술어의 사본과,
  계약 상수(top-level 키 집합·`version == 4`·pair 9필드) digest 고정과,
  `KTDM_C7_RUNNER_MODULE`이 가리키는 **실제 runner 모듈을 import한 테스트**가
  `backend/tests/test_c6c_pair_capture.py`에 있어 runner가 계약을 바꾸면 즉시 red가 된다.
  그 env가 주어졌는데 import·검증이 실패하면 skip이 아니라 fail이다.
- **오늘 n150에서 그대로 실행하면 여전히 성공하지 않는다.** 남은 것은 코드 결함이 아니라
  설치·프로비저닝·환경 항목 넷이며, 순서대로 이렇다(2026-08-19 실측).
  0. **이 브랜치가 아직 설치되지 않았다.** 설치본 revision은
     `4191582779be47e9605a324ea27adbb99b438439`이고 그 `capture`는 **파괴형**이다(위 경고
     블록). 그러므로 지금 그 명령을 실행하면 "실패"가 아니라 **컨테이너 다섯이 내려간다.**
     설치 절차는 `docs/docker-management.md` §7.5.9.
  1. `sudo -n ktdctl …`은 **`command not found`로 코드에 닿기 전에 죽는다.** sudo
     `secure_path`가 `/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/snap/bin`
     이고 venv bin이 없으며 `/usr/local/bin/ktdctl` symlink도 없다. 정본 호출은 절대경로
     `sudo -n /opt/kor-travel-docker-manager/backend/.venv/bin/ktdctl …`다.
  2. `KTDM_C7_MAP_SOURCE_CHECKOUT`/`KTDM_C7_PINVI_SOURCE_CHECKOUT`이 frozen `.env`에
     없다. `sudo`는 operator shell의 export를 버리므로 값은 반드시 frozen `.env`에 있어야
     한다. manifest 경로는 이제 유도되므로 **더는 막히지 않는다.**
  3. 그 값을 넣어도 revision 결박에서 `capture_refused_runtime`이 난다.
     `/home/digitie/kor-travel-map`은 **git 저장소가 아니고**,
     `/home/digitie/pinvi`는 clean checkout(HEAD `5a453eb5…`)이지만 실행 중 PinVi image의
     revision `5cad141a…`를 갖고 있지 않다. 관측 자체(다섯 service·label·health·image
     revision)는 오늘 통과한다.
- **동일 identity 재capture만** byte 수준으로 멱등이다. 직전 `active`가 `rollback`으로
  승격되고, 동일 identity로 다시 실행하면 기존 `rollback`과 기존 `recorded_at`이 함께
  보존되어 파일 bytes와 `manifest_sha256`이 변하지 않는다. 그래서 §2.3 attestation
  이후의 **무해한** 재실행은 게이트를 깨지 않는다. v4의 bootstrap-once 게이트
  (`assert_pair_manifest_bootstrap_allowed`)는 재배포 후 재capture를 막으므로 복원하지
  않았다.
  **단, 첫 실전 capture는 정의상 멱등이 아니다.** 오늘 기록된 active는
  map `c8ed6164…`/pinvi `6a035695…`인데 실행 중 다섯은 Map `817cfeae…`/PinVi
  `5cad141a…`다(2026-08-19 실측). 그러므로 첫 capture는 `manifest_sha256`과 두 revision을
  **모두** 바꾸고, runner가 그 셋을 attestation과 함께 대조하므로(443-448행) §2.3
  attestation 재생성이 **필수**다. capture가 `recorded_at_preserved=false` +
  `attestation_action=…`으로 그 사실을 stdout과 receipt 양쪽에 남긴다.
- 커밋 후 디스크에서 bytes를 되읽어 runner 술어로 재검증하고, 출력하는
  `manifest_sha256`은 **되읽은 bytes**의 해시다. attestation에 잘못된 해시가 복사될 수 없다.

### 결과(부정)

- **증거력이 v4보다 구조적으로 약하다.** v4는 clean checkout HEAD에서 실제 build해
  candidate와 대조했다. capture는 빌드하지 않으므로 `map_source_revision`은 결국
  **image builder가 붙인 label 주장**이고, 결박은 "그 commit이 operator가 지목한 checkout에
  실재하는 commit object이며 그 checkout이 clean이다"까지다
  (`git --no-optional-locks -C <checkout> cat-file -e <rev>^{commit}` /
  `status --porcelain=v1`). 로컬에서 위조 label을 붙인 image는 통과한다. receipt의
  `not_guaranteed` 배열이 이를 자백하고, `checkout_uid`로 신뢰 근거를 노출한다.
- **"쓰기 없음"은 거짓이다.** `rebuild-pinned`와의 상호배제를 위해 같은
  `c6c_deployment_lock_from_environment()`를 잡으므로 lock 디렉터리(0700)와 lock
  파일(0600)이 생길 수 있다. idempotent하고 n150에는 이미 존재하지만, receipt의
  `side_effects`에 경로를 명시한다.
- **global mutation lock은 Manager 경유 mutation만 직렬화한다.** prod Map 재배포의
  sanctioned 경로가 host `docker compose` 직접 실행이라 lock을 우회한다. 쓰기 직전 2차
  관측(TOCTOU)이 창을 좁힐 뿐 닫지 못한다.
- 런북 §3이 요구하는 "main에 병합된 최종 commit"과 capture는 결박되지 않는다. reachability
  (`merge-base --is-ancestor`)를 게이트로 넣으면 n150 실측 기준 오늘 fail-closed였을
  것이라 의도적으로 뺐다.

### 개정 (2026-08-19, 적대 리뷰 9건 반영)

초판은 세 입력을 CLI 필수 인자로 두어 **런북의 문자 그대로의 호출이 여전히 exit 2**였다.
이 명령의 유일한 존재 이유가 "런북을 고치지 않고 Manager에 그 명령을 존재하게 하는 것"이므로
목표 미달이었다. 게다가 실패 시점이 런북 §2.1 step 4(alembic upgrade) 뒤라 운영자가 막다른
길에 섰다. 그래서 세 입력을 frozen environment fallback으로 바꾸고(위 §결정 1번 bullet),
아래 여덟 가지를 함께 고쳤다.

| 리뷰 | 고친 내용 | terminal state / receipt |
|:---|:---|:---|
| R1-1/R2-1 | 세 입력의 frozen env fallback, flag는 override | `input_sources` |
| R1-2 | pinned runtime state root 아래 manifest 배제 | `capture_refused_precondition` |
| R1-3 | v5 pinned generation 대조(보고 전용) | `pinned_generation_agrees` 외 2 |
| R1-4 | rollback 승격 회귀 테스트 강화(`rollback != active` seed) | — |
| R2-2 | `os.replace` **전** runner 재검증 + 커밋 후 스냅샷 복구 | `capture_write_rolled_back` |
| R2-3 | pre-image 증거 + generation 전환 게이트 | `previous_*`, `allow_generation_change` |
| R2-4 | cross-repo 게이트를 `KTDM_C7_RUNNER_MODULE`로 이동(값 있으면 skip 금지) | — |
| R2-5 | git env 위생 + `dubious ownership` 구분 | `capture_refused_checkout_ownership` |

> **이 표의 R1-1/R2-1 행은 2차 개정에서 다시 바뀌었다.** 1차 개정은 manifest 경로의
> fallback을 `E2E_C7_COMPATIBLE_PAIR_MANIFEST` → `KTDM_C6C_COMPATIBLE_PAIR_MANIFEST`로
> 뒀는데, 뒤쪽 키는 production `.env`에서 모든 Manager mutation을 죽이는 지뢰였다.
> 아래 2차 개정이 정본이다.

### 개정 2 (2026-08-19, 실측이 뒤집은 전제 7건)

1차 개정의 §근거 1(A)는 **사실오류**였다. 근거로 인용한 `rehearsal`/`rebuildable`은
`/home/digitie/kor-travel-docker-manager/.env`의 값인데, 설치본 shim이 project root를
`/opt/kor-travel-docker-manager`로 하드코딩하므로 capture가 읽는 것은 production인
`/opt` 쪽 `.env`다. 그래서 "`c6c_state_paths`를 재사용하면 네 번째 아티팩트 위치가
생긴다"는 서술은 성립하지 않았고, 오히려 유도값이 runner가 읽어 온 바로 그 파일이었다.
그 오류 위에 쌓인 규칙(env fallback 체인, basename 하드락)까지 함께 정정했다.

| 리뷰 | 고친 내용 | 상태/증거 |
|:---|:---|:---|
| B-2 | manifest 기본 경로를 `c6c_state_paths`에서 유도. `--manifest-path`와 `E2E_C7_COMPATIBLE_PAIR_MANIFEST`는 override | `input_sources.manifest_path=c6c_state_paths` |
| B-4 | `KTDM_C6C_COMPATIBLE_PAIR_MANIFEST` fallback 제거(production `.env`에 넣으면 모든 mutation이 죽는 지뢰) | `MANIFEST_PATH_FORBIDDEN_ENV_NAME` |
| B-1 | basename 하드락 제거. runner가 걸지 않는 제약을 manager가 만들지 않는다 | lane의 `c7-compatible-pair-v4.json` 수용 |
| B-3 | 런북 정본 호출을 절대경로로 정정(`sudo -n` PATH에 venv bin 없음) | 문서 |
| B1/F-2 | `get_env_path()`/`effective_environment()`를 typed refusal로 감쌈 + `environment=None` 경로를 실제로 타는 테스트 | `capture_refused_precondition` |
| B-5 | pre-image·`rollback_images_present`·`side_effects`·`input_sources`를 비-JSON stdout에도 출력 | `_evidence_lines` |
| F-1 | 동일 identity 재capture 시 `recorded_at` 보존 → byte-멱등 (**그 밖에는 멱등이 아니며 §2.3 재생성 필수** — 개정 3에서 정정) | `manifest_sha256` 불변 |

state root 규칙은 여전히 새로 만들지 않는다. 1차 개정이 "소비자가 쓰는 env 이름을 읽는다"로
표현했던 것을 "**이 저장소가 이미 가진 규칙에서 유도한다**"로 바꾼 것이므로, 세 번째 규칙을
만들지 않는다는 원래 목표가 이제 정직하게 달성된다. Manager가 추가한 유일한 제약은
배제 규칙(R1-2)이며, 그 근거는 `rebuild-pinned`가 같은 이름을 지운다는 이 저장소 안의
사실이다.

### 개정 3 (2026-08-19, 3차 확인 리뷰 blocking 2건)

3차 확인 리뷰가 **운영 위험 1건**과 **거짓 주장 1건**을 남겼다. 둘 다 코드 결함이 아니라
"문서가 오늘 실행하면 위험한 명령을 지시한다" / "문서가 성립하지 않는 성질을 약속한다"였다.

| 리뷰 | 문제 | 고친 것 |
|:---|:---|:---|
| B-1 | n150 설치본(`41915827…`)의 `pinvi-pair capture`는 **파괴형**인데, 이 브랜치 문서가 그 문자열을 "정본 호출"로 공표했다. 오늘 문자 그대로 실행하면 컨테이너 다섯이 내려간다 | ADR 최상단·§7.5 최상단 경고 블록, §7.5.1 **읽기 전용 확인 절차**(`--help` 두 번), 코드에 `CAPTURE_CONTRACT = "pair-capture-v1"` 자기 식별(=`--help`·stdout 첫 줄·receipt 3곳), §7.5.9 설치 절차 조사 |
| B-2 | "재capture는 byte-멱등"이 §2.3 attestation 게이트에 대해 오해를 만든다. runner는 `manifest_sha256`·`active.map_source_revision`·`active.pinvi_source_revision`(+`contract_generation`)을 **함께** 대조하고(443-448행), 기록된 active와 실행 중 runtime이 이미 다르므로 **첫 실전 capture는 정의상 멱등이 아니다** | 멱등 주장을 "identity가 같을 때만"으로 좁히고 세 필드를 명시. receipt에 `recorded_at_preserved`·`attestation_action` 추가, 비-JSON stdout에도 두 줄 출력 |

함께 정리한 followup 셋.

- **runner 행 번호 포인터 정정.** 실질 주장은 모두 참이었고 포인터만 낡아 있었다.
  `manifest shape` 428-432 → **436**(+`_validate_pair` **439-440**),
  sha256 대조 436 → **443-448**(sha 444), health 술어 501-508 → **508-518**,
  `_read_secure_file` 112-146/112-164 → **111-162**,
  `_compose_container` 277-302 → **285-310**,
  `_validate_pair` 305-316/305-341 → **313-325/313-347**,
  `_exact_dict` 65-66 → **68-69**, manifest secure read `:623` → **635**.
  `test_c6c_pair_capture.py`가 폐기된 인용 문자열을 코드·테스트·두 문서에서 스캔해
  되살아나면 red가 되고, `KTDM_C7_RUNNER_MODULE`이 주어지면 각 행이 실제로 무엇인지까지
  대조한다.
- **비-production 환경의 R1-2 자기 충돌**은 위 §미결 끝에 따름정리로 적었다. 설치본이
  production 분기라 blocker가 아니다.
- **"다섯 service가 running·healthy"의 정확한 뜻**(healthcheck 미선언 컨테이너는 통과)을
  `docs/docker-management.md` §7.5 같은 자리와 `_assert_container_is_healthy` docstring에
  적었다. 오늘 `kor-travel-map-dagster-daemon-latest`가 그 경우다.

### 미결 (사용자 결정 대기)

**어느 파일을 C7 정본으로 둘 것인가.** 오늘 두 파일은 byte-identical 복제본이다
(sha256 `f2051e42…`).

| 선택지 | 장점 | 대가 |
|:---|:---|:---|
| **A. `/var/lib/kor-travel-docker-manager/kor-travel-docker-manager/compatible-pair-v4.json`** (= `c6c_state_paths` 유도 기본값, capture의 현재 기본) | capture를 인자·env 없이 부를 수 있다. 경로 규칙이 Manager 한 곳에 있다. ancestor가 `0700`이라 노출면이 더 좁다 | lane 스크립트(`c7-lane-run-*.sh`, `c7-rerun-*.sh`)의 `MANIFEST=`를 바꿔야 한다. Map 저장소 런북·스크립트가 Manager의 state root를 알게 된다 |
| **B. `/etc/kor-travel-map/c7-compatible-pair-v4.json`** (= 오늘 lane이 실제로 읽는 파일) | lane 스크립트를 안 고쳐도 된다. 소비자(Map) 옆에 소비자의 파일이 있다 | frozen `.env`에 `E2E_C7_COMPATIBLE_PAIR_MANIFEST`를 프로비저닝해야 한다(안 하면 capture는 A에 쓰고 lane은 B를 읽어 **서로 다른 파일을 보게 된다**). ancestor가 `0755` |
| C. 둘 다 유지(현재 상태) | 아무것도 안 해도 된다 | capture가 A만 갱신하므로 B가 조용히 낡는다. **가장 위험하다** |

기본값은 A로 두되(코드가 그렇게 동작한다) 결정은 사용자 몫이며, B를 고르면 실작업은
`.env` 한 줄 프로비저닝뿐이다.

**비-production(rehearsal 모양) 환경에서는 유도 기본값이 R1-2 배제에 스스로 걸린다.**
`KTDM_DEPLOYMENT_ENVIRONMENT`가 production이 아니면 `c6c_state_paths`는
`KTDM_C6C_STATE_ROOT`(없으면 `~/.local/state/kor-travel-docker-manager`) + `COMPOSE_PROJECT_NAME`을
쓰고, `pinned_runtime_state_root`는 `KTDM_PINNED_RUNTIME_STATE_ROOT`(없으면 같은
`~/.local/state/...`) + 같은 project를 쓴다. 두 env를 따로 주지 않으면 **두 root가 문자
그대로 같은 디렉터리**가 되어, flag도 runner env도 없이 부른 capture가 자기 유도 기본값을
"`rebuild-pinned`가 쓸어가는 자리"로 판정하고 `capture_refused_precondition`으로 거부한다.
실제 소비자인 **설치본은 production 분기**라 유도값이 `/var/lib/...`이고 pinned root와
겹치지 않으므로 이것은 blocker가 아니다(그래서 이번 라운드에서 고치지 않았다).
정리 선택지는 셋이다: (a) 그대로 두고 비-production에서는 `--manifest-path`/
`E2E_C7_COMPATIBLE_PAIR_MANIFEST`를 요구한다, (b) 두 root가 같을 때만 배제 규칙을 완화하되
그 완화가 production에 새지 않음을 테스트로 박는다, (c) 비-production 유도값에 별도
하위 디렉터리를 준다(= 사실상 세 번째 state root 규칙이라 §결정에 반한다). **결정은 뒤로
미룬다** — 오늘 이 경로를 타는 운영 절차가 없다.

### 후속

- (open, **선행 조건**) **이 브랜치를 n150에 설치한다.** 그 전에는 §7.5의 어떤 capture
  명령도 실행 금지다(설치본의 동명 `capture`가 파괴형). 조사 결과는
  `docs/docker-management.md` §7.5.9에 있고 요약은 이렇다 — 정본 명령은
  `sudo -n /usr/bin/bash <SOURCE_ROOT>/scripts/install-ktdm-trusted-release <SOURCE_ROOT>`
  하나이며, ① non-root 소유의 **clean** git checkout이 머지된 최종 commit에 있어야 하고
  (오늘 `/home/digitie/kor-travel-docker-manager`에는 `.git`이 없다 — 후보는
  `/home/digitie/f1d-v5-rehearsal/manager`), ② root-owned 오프라인 wheelhouse는 이미
  충분하며(이 브랜치는 새 런타임 의존이 **없다**), ③ installer가 capture/rebuild-pinned와
  **같은 global mutation lock**을 잡고, ④ `/opt/.../.env` bytes는 보존되며, ⑤ Manager용
  systemd unit이 없어 재기동이 필요 없고, ⑥ commit 뒤에는 `.rollback` 트리가 삭제되어
  자동 되돌리기 경로가 **없다**. **이번 라운드에서 실행하지 않았다.**
- (open) n150의 stale `pinned-runtime-generation-v5*`는 capture가 건드리지 않으므로 그대로
  남는다. capture는 이제 불일치를 **보고**하지만 고치지는 않는다. 별개 작업으로 분리한다.
- (open) revision reachability를 capture 게이트로 넣을지 여부.
- (open, 프로비저닝) `/usr/local/bin/ktdctl` →
  `/opt/kor-travel-docker-manager/backend/.venv/bin/ktdctl` symlink. 있으면 런북 문자
  그대로의 `sudo -n ktdctl …`이 동작한다. **이번 라운드에서 실행하지 않았다.**
- (open, 프로비저닝) `KTDM_C7_MAP_SOURCE_CHECKOUT`/`KTDM_C7_PINVI_SOURCE_CHECKOUT`.
  오늘 `/home/digitie/kor-travel-map`은 git 저장소가 아니고 `/home/digitie/pinvi`는
  실행 중 image의 revision을 갖고 있지 않다 — 값을 넣는 것만으로는 부족하고 두 checkout이
  실행 중 revision을 실제로 포함해야 한다.
- (open) 위 §미결의 정본 파일 선택.

## ADR-39: Map application은 paired candidate와 fresh `300` 증거로만 재구축한다

- 상태: accepted
- 날짜: 2026-08-25
- 결정자: 사용자, Codex
- 관련: ADR-34, ADR-37, Map ADR-090, Map application baseline `300`

### 컨텍스트

ADR-34의 F1D는 Manager가 일곱 runtime image를 모두 build하고 Map Alembic chain을 replay하는
모델이었다. Map은 서비스 전 단계에서 이전 revision 복구와 in-place upgrade 정책을 폐기하고,
기존 application revision `0236`을 새 baseline `300`으로 치환했다. Map API와 Dagster는 같은
commit/tree·PostgreSQL image·application contract를 증명하는 paired candidate를 이미 만든다.
Manager가 같은 두 image를 다시 독립 build하거나 source head만으로 application schema를 추정하면
candidate receipt와 실제 실행 image 사이에 두 번째 authority가 생긴다.

fresh DB에서 root/finalize one-shot을 호출하는 경계도 일반 migration보다 강한 crash 의미론이
필요하다. 실행 intent를 durable하게 남긴 뒤 응답을 잃으면 작업이 성공했는지 알 수 없으므로,
결과 없이 같은 command를 재발행하면 비멱등 DB 효과를 중복 실행할 수 있다. 반대로 결과·DB identity·
permit을 journal 밖에만 두면 재개가 다른 DB 또는 다른 candidate를 정상 결과로 오인할 수 있다.

### 결정

Map release authority는 exact Map commit과 그 commit의 sealed builder가 만든 API·Dagster paired
candidate receipt다. Manager는 Map API/Dagster를 다시 Compose build하지 않는다. Manager build 대상은
Map UI와 PinVi API·Web·Dagster 네 개이며, 일곱 runtime generation은 다음을 exact하게 결박한다.

- Map API image ID, Map Dagster image ID와 paired receipt SHA-256
- Map API·Dagster의 동일 commit/tree와 Dagster web·daemon의 동일 image ID
- paired application contract의 head `300`과 exact PostgreSQL image ID
- Map UI와 PinVi 세 image의 source revision
- Map application·Dagster metadata·PinVi schema head와 pinset digest

generation manifest는 v6, pinset별 rebuild journal과 tombstone은 v8을 사용한다. 구 v5 manifest와
v7 journal은 실행 authority가 아니며 typed allowlist로만 퇴역한다.

세 DB를 새로 만든 뒤 Map application DB의 system identifier·name·OID·owner·login role을 읽어 journal에
고정한다. DB 생성과 role bootstrap은 각각 durable intent와 exact state attestation으로 response loss를
수렴한다. root와 finalize 각각은 operation plan을 fsync하고 root-owned read-only fence를 발행한 뒤
execution intent를 fsync한다. Map은 같은 operation ID의 intent와 append-only receipt를 DB transaction에
함께 기록한다. host result가 없으면 DB receipt를 먼저 복구하고, receipt가 없으면서 exact pre-state인
경우에만 같은 operation을 다시 호출한다. 만료 fence도 이 조건에서 operation ID를 보존한 채 새 fence
transaction으로 갱신한다. `partial`·`foreign`·identity drift는 fail-close한다.

finalize 결과 뒤 application final permit을 발행한다. Dagster metadata DB는 application DB와 별도의
system identifier·name·OID·owner·login role을 검증하고, paired Dagster image/config/receipt와 함께
metadata permit에 결박한다. permit mount directory는 root-owned `0755`, 파일은 `0444`로 하여 non-root
runtime이 읽을 수 있게 하고, receipt/result directory는 root-owned `0700`으로 유지한다. Map Dagster
storage migration은 journal transaction ID를 operation ID로 쓰는 DB intent+receipt v2다. durable intent
재개에서도 같은 command를 실행해 기존 receipt를 복구하거나 미완료 intent를 완결하며, exact head와
operation ID가 다르면 거부한다. 성공 뒤 Dagster web·daemon은 `--no-deps`로 기동해 Compose
`depends_on`이 migration을 다시 실행하지 못하게 한다.

committed 또는 final resume은 일곱 running container의 실제 Docker image ID와 journal generation을
다시 exact 대조한다. 이 작업에는 backup, scratch restore, 이전 revision rollback이 없다. 데이터가
필요하면 head `300`에 맞는 source/ETL을 별도 실행한다.

### 근거

- paired receipt를 유일 Map API/Dagster image authority로 쓰면 같은 source를 두 번 build해 생기는
  provenance 분기를 제거한다.
- DB identity와 operation/result SHA를 journal에 묶으면 다른 fresh DB나 stale artifact 재사용을 막는다.
- DB intent와 append-only receipt를 같은 transaction에 두고 recover-first로 재개하면 response-loss에서
  이미 완료된 효과를 반복하지 않으면서, receipt 부재가 증명된 exact pre-state는 안전하게 수렴한다.
- application permit과 metadata permit을 분리하면 Dagster storage가 application DB 권한을 갖거나 두 DB
  identity가 섞이는 것을 구조적으로 거부할 수 있다.
- `--no-deps` 기동과 exact running image 검증은 one-shot 재실행과 tag drift를 각각 차단한다.

### 결과

- n150 rebuild는 이전 `0236` 또는 중간 migration을 복원하지 않고 fresh application head `300`만 만든다.
- manifest v6/journal v8은 candidate, DB identity, fence, result, permit과 실제 runtime image를 하나의
  재개 가능한 generation으로 기록한다.
- receipt가 없고 pre-state도 exact하지 않은 execution intent, image/receipt/DB identity drift,
  non-root가 읽을 수 없는 permit, implicit storage 재실행은 모두 runtime 기동 전에 fail-close한다.

## ADR-40: pinned revision은 코드 상수가 아니라 root 소유 registry 파일이 소유하고, pinset의 생애 상태를 기계가 강제한다

- 상태: accepted
- 날짜: 2026-08-28
- 결정자: agent (오너 승인 Q1, `docs/ktdctl-ui-migration.md` 1부)

### 컨텍스트

Map·PinVi의 pinned revision(40-hex 2개)과 그 파생 digest가 `services/pinned_runtime_release.py`의
코드 상수였고, Map revision의 중복본이 `services/map_application_300.py`에 하나 더 있었다.
pin은 이 시스템에서 가장 자주 바뀌는 값인데(2026-08-25~28 3.5일 실측 회전 15회, 그중 5회는
3,900~4,100줄짜리 기능 커밋에 매몰돼 "PR 리뷰 = pin 승인" 게이트가 명목상으로도 작동하지
못했다) 가장 바꾸기 비싼 곳에 있었다. 회전 1회가 코드 2파일 + 테스트 기대값(실측 86줄) +
PR + 릴리스 설치 + 재기동으로 증폭됐고, 그 여파는 이 저장소에 그치지 않았다 — 같은 3일
창에서 kor-travel-map은 pinset 부기 전용 커밋 19건, pinvi는 Manager generation이 이미 보유한
값 14개를 문서에 수기 복제했다.

더 심각한 것은 **"terminal(실패 종결) 판정된 pinset은 영구 재시도 금지"라는 핵심 운영
규약이 세 저장소의 수기 문서에만 존재**했다는 점이다. 어긴 실행을 막는 기계 게이트가 없었고,
실제로 이 감사에서 **현행 Manager pin `cbb577d3…`가 pinvi journal이 terminal로 선언한
pinset인데 Manager 코드만으로는 알 방법이 없다**는 사실이 드러났다.

### 결정

pinned revision과 pinset의 생애 상태를 root 소유 JSON registry 파일(`runtime-pin-registry.v1`)로
옮기고, terminal pinset의 실행을 rebuild가 mutation 이전에 거부한다.

### 근거

- **값은 파일, 계약은 코드.** canonical URL 집합·40-hex 형식·role 순서·pinset digest 재계산
  대조는 파싱 직후 코드가 강제한다. 파일을 편집해 임의 저장소를 가리키게 만드는 것은 코드
  수정 없이 불가능하다. digest 계산 규칙(`canonical_pinset_bytes`)은 kor-travel-map
  attestation과 공유하는 계약이므로 한 바이트도 바꾸지 않았다.
- **읽기 시점에 파일 자체를 검증한다.** `lstat`(symlink 미추종), 일반 파일, 소유자(root 또는
  자기 자신), group/other 쓰기 금지. "값은 파일, 신뢰는 소유권"이라는 논거는 소유권을 실제로
  보는 코드가 있어야 성립한다.
- **하한선은 코드가 소유한다.** registry가 손상되거나 오래된 사본으로 시딩돼도 d9 계열
  차단은 유지된다. 목록은 데이터, 하한선은 코드다.
- **차단에는 두 의미가 있고 섞지 않는다.** phase 한정 차단은 특정 journal 재개만 막고(기존
  d9 admission과 동일), 조건 없는 차단은 그 pinset의 모든 실행을 막는다(rebuild 시작 게이트).
- **배포 트리 밖.** trusted installer는 canonical execution root를 통째 교체하므로 트리 안
  registry는 다음 설치가 회전을 조용히 되돌린다. 설치 root에서 도는 경우 기본값을
  `/var/lib/kor-travel-docker-manager`로 두고, 트리 안 경로로의 회전은 거부한다.
- **재기동 불요.** mtime·size·inode 스탬프로 캐시를 무효화하므로 root CLI의 회전이 실행 중
  backend에 즉시 반영된다.

### 트레이드오프 (내주는 것)

1. PR 리뷰가 곧 pin 승인이던 암묵적 게이트 → `--confirm` + root + `--reason` 필수 + 이력·감사
   기록으로 대체. 실측상 그 게이트는 셀프 머지이거나 대형 diff에 매몰돼 명목적이었다.
2. git 이력 = pin 이력 → registry의 `history`와 digest 이름의 보존본으로 대체. 롤백은 오히려
   개선되지만(명령 1개) 보존본이 git처럼 분산 백업되지 않으므로 백업 대상 등재가 필요하다.
3. 테스트가 pin 값을 고정하던 성질 → 값 고정에서 구조 검증으로 재작성. 회전 시 값 고정
   churn은 소멸하고, rebuild 시나리오 fixture는 잔존한다.
4. **코드 = 배포본 단일성(가장 실질적 손실).** 동작이 코드 + 호스트 로컬 파일의 함수가 된다.
   부재·파싱 실패·digest 불일치는 전부 fail-close로 관리하되, 단일성 자체는 회복되지 않는다.

Map application `300`의 소스 commit 중복 상수는 삭제했다. 이원 관리 hazard가 소멸하는 대신
로컬 이중화가 사라지지만, 실질적인 교차 검증은 원래 로컬 중복본이 아니라 **Map이 공급하는
sealed paired candidate가 같은 commit을 선언해야 한다**는 admission이며 그 검증은 그대로다.

### 후속

pin 회전 UI(2-step 승인), typed 진단 소비, preflight readiness 노출은
`docs/ktdctl-ui-migration.md` 3부의 별도 태스크(KUM-M5·M6·M7)로 분리한다.

## ADR-41: host mutation lease는 부팅 시 생성하고, 소유자는 root와 선언된 서비스 계정 둘을 인정한다

- 상태: accepted
- 날짜: 2026-08-28
- 결정자: agent (오너 승인 — "셋다 진행")

### 컨텍스트

`KTDM_DEPLOYMENT_ENVIRONMENT=production`에서 모든 Compose mutation은
`/run/lock/kor-travel-docker-manager/global-mutation.lock` 하나를 지난다. 이 디렉터리를
Manager가 **런타임에 처음 만들고** 있었고, 부모인 `/run/lock`은 Debian 계열에서 `1777`
sticky다. 즉 재부팅 직후 비특권 로컬 사용자가 같은 이름을 선점하면 소유자·mode 검증이
실패해 **모든 컨테이너 mutation이 다음 재부팅까지 거부**된다. sticky bit 때문에 정리도
root만 할 수 있어, 권한 없는 사용자가 운영 전체를 잠글 수 있는 비대칭이 남아 있었다.

같은 지점이 두 번째 문제도 만들고 있었다. lease 소유자를 리터럴 `uid 0`으로 요구했기
때문에 **백엔드가 root로 돌아야만 컨테이너를 다룰 수 있었다.** 근거를 확인해 보면 백엔드
소스의 나머지 소유권 검사는 전부 `st_uid != os.geteuid()`(자기 자신) 기준이고, 남은 리터럴
root 게이트는 rebuild·pin 회전·fixed artifact 발행·legacy retirement 같은 **root 전용
워크플로**뿐이다. 즉 상시 실행되는 백엔드를 root로 묶어 두는 구조적 이유는 이 lease 하나였다.

### 결정

1. lease 디렉터리는 `systemd-tmpfiles`가 early boot에 만든다
   (`deploy/tmpfiles.d/kor-travel-docker-manager.conf`).
2. lease 소유자로 root와 `KTDM_SERVICE_USER`가 선언한 계정을 함께 인정한다. 미설정이면
   `0`으로 해석되어 기존 계약과 동치다.

### 근거

- **선점 창은 시점의 문제다.** `d` 타입은 이미 있는 디렉터리의 소유자·mode까지 바로잡고
  early boot에 돌므로, 경쟁할 사용자 프로세스가 아직 없다. 런타임 검증을 아무리 조여도
  "먼저 만든 쪽이 이긴다"는 성질은 사라지지 않는다.
- **서비스 계정 모드에서는 런타임 생성을 아예 금지한다.** `1777` 아래의 런타임 생성이 곧
  위의 창이므로, 없으면 tmpfiles.d를 가리키는 계약 오류로 거부한다. root 실행 경로는 창이
  없으므로 생성을 허용하되, 서비스 계정이 선언돼 있으면 만든 직후 그 계정으로 `lchown`해
  tmpfiles.d가 만들어 두었을 상태로 수렴시킨다 — 설치 누락이 "root가 `0700 root:root`로
  선점해 서비스 계정이 영영 못 들어가는" 조용한 잠금이 되지 않게 한다.
- **소유자를 하나만 인정하면 자가 교착이다.** `rebuild-pinned`는 root로, 백엔드는 서비스
  계정으로 도는데 lease는 공유한다. 한쪽만 허용하면 둘이 서로를 거부한다. 인정 집합을
  `{root, 선언된 계정}`으로 두면 두 신뢰 주체는 통과하고 제3의 uid는 여전히 막힌다.
- **rebuild lease는 건드리지 않는다.** `production` 판정은 global mutation lease일 때만
  참이고 `pinned-runtime-rebuild.lock`은 예전처럼 자기 소유(`os.geteuid()`)를 본다.
- **기본값은 오늘과 완전히 같다.** 환경변수 미설정이 `uid 0`이므로 이 변경만으로 운영
  동작이 바뀌지 않는다. 비-root 전환은 별도의 명시적 결정이다.

### 트레이드오프 (내주는 것)

1. **동작이 배포 트리 밖 파일(tmpfiles.d) 설치 여부에 의존한다.** 미설치 호스트에서 root
   경로는 예전처럼 동작하지만 선점 창이 남고, 서비스 계정 모드는 아예 거부된다. 설치는 배포
   절차 문서(`docs/prod-deployment.md` 3.z)의 필수 항목으로 등재했다.
2. **신뢰 집합이 1개에서 2개로 늘었다.** `KTDM_SERVICE_USER`를 잘못 선언하면 그 계정이 host
   lease를 소유할 수 있다. 선언은 root `0600` `.env`에만 존재하므로, 이 값을 쓸 수 있는
   주체는 이미 Manager를 통제하는 주체다.
3. **비-root 전환이 자동으로 완결되지는 않는다.** `.env` 소유권 이전, `docker` 보조 그룹,
   tmpfiles.d의 UID 필드를 함께 옮겨야 하고, root 전용 워크플로 네 가지는 `sudo`로 남는다.

### 후속

n150 운영 백엔드의 실제 계정 전환은 별도 작업으로 분리한다. 이 ADR은 전환을 **가능하게 하는
seam과 부팅 시 생성**까지만 확정한다.
