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

- 상태: accepted
- 날짜: 2026-06-10
- 결정자: human, AI agent

### 컨텍스트
`pinvi`, `kor-travel-concierge`, `python-krtour-map`, `kor-travel-geo`가 같은 PC에서 PostgreSQL/RustFS 포트를 함께 쓰면서, 각 저장소가 개별적으로 컨테이너를 정지/재시작하면 포트 점유와 credential 기준이 충돌할 수 있다.

### 결정
Kor Travel/PinVi 계열 database는 `kor-travel-geo-postgres` 컨테이너의 `kor_travel_geo`, `pinvi`, `kor_travel_concierge`, `krtour_map` database로 통합하고, Docker 생명주기와 로컬 포트·credential·bucket 기본값은 `kor-travel-docker-manager`가 관리한다.

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

- 상태: accepted
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

- 상태: accepted
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

Manager 대시보드는 Docker 컨테이너 시작·정지·설정 변경 API를 직접 호출한다. 기존에는 로컬 개발 편의를 위해 별도 인증이 없었지만, 운영 공개 주소와 CORS 설정이 들어온 뒤에는 관리자 화면과 API를 보호해야 한다. 사용자는 단일 관리자 계정(`admin`/`ad.min`)을 요구했고, 비밀번호와 API 키 원문은 git에 노출하지 않아야 했다. 또한 `kor-travel-geo` PR #399의 로그인·API 키 UX와 보안 패턴을 따르고, `kor-travel-geo` v2 API도 같은 VWorld 키 계약을 사용할 수 있어야 했다.

### 결정

단일 관리자 로그인은 프론트엔드 Origin 제한, PBKDF2 비밀번호 해시, HMAC 서명 `httpOnly` 세션 쿠키, DB 저장 세션 해시를 함께 사용한다. 로그인·로그아웃·실패 시도·API 키 생성/폐기는 DB 감사 로그로 남긴다. 공개 API 키는 VWorld 호환 32자리 영문/숫자 문자열로 생성하되, 원문은 생성 응답에서 1회만 보여 주고 DB에는 SHA-256 해시와 끝 6자리 힌트만 저장한다. 활성 키 해시는 짧은 TTL 메모리 캐시로 읽고 생성·폐기 시 무효화한다. 로그인된 신뢰 UI 요청은 외부 공개 API의 key 검증을 생략할 수 있는 공통 dependency를 제공한다. `X-Forwarded-*` 계열 헤더는 `KTDM_TRUSTED_PROXY_CIDRS`에 포함된 직접 peer에서 온 요청일 때만 감사 로그·rate-limit·secure cookie 판단에 사용한다.

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

- 상태: accepted
- 날짜: 2026-07-18
- 결정자: human, Codex

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

production Map/PinVi API mutation은 일반 `ensure`, container action/config/reset, direct Compose API
service 변경에서 중앙 차단한다. Compose 명령은 알려진 read-only 명령만 무변경으로 분류하고,
`scale`·`watch`와 알 수 없는 명령은 보수적으로 mutation으로 분류한다. 모든 API mutation 진입점은
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
명시 서비스가 보이더라도 두 API scope로 default-deny한다.
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
  단계 기동하고 최초 v3를 원자 기록한다. 실패하면 두 API를 중지하고 이 transaction이 새로 만든
  container만 제거하며, 기존 container는 삭제하지 않는다.
- `ktdctl pinvi-pair rollback`은 Map 복원과 signed smoke 뒤에 PinVi를 복원하므로 서로 다른 generation을
  동시에 실행하지 않는다.
- production `ktdctl pinvi-pair deploy`는 canonical single-file compose와 active/rollback generation을 먼저 검증하고,
  dependency·UI·Dagster가 이미 running/healthy인지 read-only로 확인한다. 기존 PinVi API를 quiesce한 뒤
  두 API에만 `--no-deps` build/recreate를 적용하고, Map signed smoke, PinVi canonical ETL/provider-sync와
  owned fixture의 정확한 409/502/503 typed cancel, UI auth lifecycle, runtime secret 격리를 모두 통과해야
  manifest를 갱신한다. 나머지 서비스는 pair transaction에서 재생성하지 않는다.
- 중간 실패는 배포 시작 시점 active pair 전체를 동일 image override로 복구해 전체 계약을 다시
  검증한다. 복구도 검증할 수 없으면 두 API를 중지하고 `halted_requires_operator` 상태를 반환한다.

### 결과(부정)

- Map과 PinVi image가 모두 새 계약을 이해해야 하므로 단일 서비스만 임의 rollback할 수 없다.
  기존 manifest나 알 수 없는 manifest는 bootstrap capture로 덮어쓰지 않는다. ADR-21 이후
  provenance가 없는 v1/v2는 자동 전환하지 않고 거부한다.
- pair 전환 중에는 혼합 generation 노출을 막기 위해 PinVi API를 먼저 중지하므로 짧은 쓰기 중단이 있다.
- local/prod mode를 자동 추론하지 않으므로 기존 `.env`에도 세 mode/required 값을 명시해야 한다.

### 후속

- (open) 여러 service principal과 개별 폐기·감사 요구가 생기면 DB 기반 hashed credential registry를
  별도 ADR로 검토한다.

## ADR-21: C6c compatible pair에 clean source revision을 결박한다

- 상태: accepted
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
immutable image label, compatible-pair v3
record에 같은 revision을 끝까지 유지한다. v3의 active/rollback pair는 각각
`map_image_id`, `map_source_revision`, `pinvi_image_id`, `pinvi_source_revision`,
`contract_generation`, `recorded_at`을 exact 필수 필드로 갖는다.

### 근거

- moving tag와 image ID는 image byte 정체성만 보장하며 source checkout 정체성은 보장하지 않는다.
- manager가 mutation lock 안에서 checkout→arg→label→manifest를 연결해야 두 서비스가
  서로 다른 generation으로 빌드되는 경로를 닫을 수 있다.
- build 전후 `git status`만 검사하면 build 중 변경 후 원복하는 TOCTOU와 ignored 파일을 놓치므로
  build input 자체를 exact Git tree로 고정해야 한다.
- raw/resolved build mapping은 Git snapshot context, 저장소 내부의 지정 Dockerfile, provenance
  arg만 허용하며 external Dockerfile, additional context, secret, target 같은 추가 입력은 거부한다.
- operator가 입력한 revision을 신뢰하지 않고 Git `HEAD`와 비교해야 타이포·stale
  환경값을 mutation 전에 거부할 수 있다.
- 현재 제작 단계이므로 provenance가 없는 v2 호환 계층을 유지하지 않고 v3로
  clean-cut하는 것이 오인 가능성을 낮춘다.

### 결과(긍정)

- capture/deploy/rollback 증거에서 image ID와 source commit을 함께 추적할 수 있다.
- dirty checkout, `development`, revision mismatch, label 누락은 운영 container 변경 전 또는
  candidate 승격 전에 fail-close한다.

### 결과(부정)

- production `--build`는 두 저장소의 Git metadata와 clean worktree를 모두 필요로 한다.
- provenance 필드가 없는 v1/v2 compatible-pair manifest는 배포·rollback에 사용할 수 없다.

### 후속

- (open) n150의 실제 clean checkout과 두 image label, manifest source revision을 C6c live smoke
  증거에 포함한다.
