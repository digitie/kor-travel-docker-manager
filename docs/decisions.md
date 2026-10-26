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

## 과거 기록 아카이브

파일이 에이전트 read 한도(256 KB)를 넘어 2026-10-13에 **ADR-1~40**을 [`decisions-archive-01.md`](decisions-archive-01.md)로,
2026-10-20에 **ADR-41~50**(ADR-49는 결번)을 [`decisions-archive-02.md`](decisions-archive-02.md)로 옮겼다(본문은 byte 그대로,
번호·제목·상태 불변 — 역사는 다시 쓰지 않는다). 이 파일에는 ADR-51부터 있다. 번호는 계속 이어진다.
아카이브된 ADR을 인용할 때도 번호만 적는다(`ADR-37`처럼) — 어느 파일에 있는지는 아래 표가 말한다.

**분리 완료(2026-10-20).** 2026-10-13 분리 뒤에도 이 파일은 ≈239 KB(ADR-58 포함)로 여유가 ≈22 KB뿐이었다. ADR-41~50(41~48·50 —
ADR-49는 결번)을 파일 순서상 가장 오래된 연속 구간(ADR-41 시작부터 ADR-51 직전까지, 그 사이의 ADR-50이 ADR-48 앞에 있던 순서 그대로)
으로 뽑아 `decisions-archive-02.md`에 byte 그대로 옮기고 이 파일에는 ADR-51부터 남겼다(분리 전후 `## ADR-` 제목 집합 대조 완료). **다음
분리 규칙**: 이 파일이 다시 200 KB를 넘는 상태에서 ADR을 더하는 PR은 가장 오래된 아직-본문에-남은 연속 구간을 같은 방식(byte 그대로,
아래 인덱스 표에 행 추가, 분리 전후 `## ADR-` 제목 집합 대조)으로 `decisions-archive-03.md`에 옮긴다.

| 번호 | 제목 | 상태(이동 당시 첫 줄 — 여러 줄이면 요약) | 파일 |
|---|---|---|---|
| ADR-1 | 모노레포 구조 채택 (FastAPI 백엔드 + Next.js 프론트엔드) | accepted | [`decisions-archive-01.md`](decisions-archive-01.md#adr-1-모노레포-구조-채택-fastapi-백엔드--nextjs-프론트엔드) |
| ADR-2 | Docker 데몬 제어를 위해 Python Docker SDK 채택 | accepted | [`decisions-archive-01.md`](decisions-archive-01.md#adr-2-docker-데몬-제어를-위해-python-docker-sdk-채택) |
| ADR-3 | 프론트엔드 상태 관리에 TanStack Query 채택 | accepted | [`decisions-archive-01.md`](decisions-archive-01.md#adr-3-프론트엔드-상태-관리에-tanstack-query-채택) |
| ADR-4 | 에이전트 친화적 문서 및 설정 구조 채택 | accepted | [`decisions-archive-01.md`](decisions-archive-01.md#adr-4-에이전트-친화적-문서-및-설정-구조-채택) |
| ADR-5 | Kor Travel/PinVi 계열 PostgreSQL 생명주기를 통합 DB 컨테이너로 관리한다 | superseded (ADR-37, 2026-08-17; Docker 생명주기 일원화 원칙은 유지) | [`decisions-archive-01.md`](decisions-archive-01.md#adr-5-kor-travelpinvi-계열-postgresql-생명주기를-통합-db-컨테이너로-관리한다) |
| ADR-6 | BMW M 시각 양식의 인프라 대시보드 수렴 및 react-doctor 최적화 | superseded (ADR-36, 2026-08-13; React 구조 개선 기록은 유지) | [`decisions-archive-01.md`](decisions-archive-01.md#adr-6-bmw-m-시각-양식의-인프라-대시보드-수렴-및-react-doctor-최적화) |
| ADR-7 | Kor Travel/PinVi 전용 Docker Manager 및 Python CLI 채택 | accepted | [`decisions-archive-01.md`](decisions-archive-01.md#adr-7-kor-travelpinvi-전용-docker-manager-및-python-cli-채택) |
| ADR-8 | 짧은 CLI 별칭과 설정 파일 기반 의존 순서를 채택한다 | accepted | [`decisions-archive-01.md`](decisions-archive-01.md#adr-8-짧은-cli-별칭과-설정-파일-기반-의존-순서를-채택한다) |
| ADR-9 | Kor Travel/PinVi 계열 로컬 포트 대역 정책을 일원화한다 | accepted | [`decisions-archive-01.md`](decisions-archive-01.md#adr-9-kor-travelpinvi-계열-로컬-포트-대역-정책을-일원화한다) |
| ADR-10 | Prometheus, Grafana, Exporter 관측 스택을 별도 컨테이너로 분리한다 | accepted (host 포트 배정만 ADR-48로 2026-09-21 superseded — 본문 참조) | [`decisions-archive-01.md`](decisions-archive-01.md#adr-10-prometheus-grafana-exporter-관측-스택을-별도-컨테이너로-분리한다) |
| ADR-11 | Kor Travel Geo와 Kor Travel Concierge 공식명을 채택하고 과거 이름 수용 계층을 제거한다 | accepted | [`decisions-archive-01.md`](decisions-archive-01.md#adr-11-kor-travel-geo와-kor-travel-concierge-공식명을-채택하고-과거-이름-수용-계층을-제거한다) |
| ADR-12 | 프로젝트명을 Kor Travel Docker Manager로 변경하고 CLI를 `ktdctl`로 전환한다 | accepted | [`decisions-archive-01.md`](decisions-archive-01.md#adr-12-프로젝트명을-kor-travel-docker-manager로-변경하고-cli를-ktdctl로-전환한다) |
| ADR-13 | Kor Travel Geo DB명, 컨테이너명, 데이터 경로를 현재 앱 기준으로 맞춘다 | accepted | [`decisions-archive-01.md`](decisions-archive-01.md#adr-13-kor-travel-geo-db명-컨테이너명-데이터-경로를-현재-앱-기준으로-맞춘다) |
| ADR-14 | 앱 target 흐름을 `geo -> conc -> map -> pinvi`로 재정렬하고 실제 앱 컨테이너를 manager에서 빌드한다 | accepted | [`decisions-archive-01.md`](decisions-archive-01.md#adr-14-앱-target-흐름을-geo---conc---map---pinvi로-재정렬하고-실제-앱-컨테이너를-manager에서-빌드한다) |
| ADR-15 | 운영(prod) 공개 주소를 gitignore된 `.env`/`.env.production`로 주입하고 CORS Origin을 환경변수화한다 | accepted | [`decisions-archive-01.md`](decisions-archive-01.md#adr-15-운영prod-공개-주소를-gitignore된-envenvproduction로-주입하고-cors-origin을-환경변수화한다) |
| ADR-16 | dev 기본 네트워크를 Docker host 모드로 전환하고 컨테이너=호스트 포트로 통일한다 | accepted | [`decisions-archive-01.md`](decisions-archive-01.md#adr-16-dev-기본-네트워크를-docker-host-모드로-전환하고-컨테이너호스트-포트로-통일한다) |
| ADR-17 | 프론트엔드를 Tailwind v4 + StyleSeed 라이트 토큰으로 전환하고 전역 오류 복구 boundary를 추가한다 | superseded (ADR-36, 2026-08-13; Tailwind v4·오류 복구 boundary 기록은 유지) | [`decisions-archive-01.md`](decisions-archive-01.md#adr-17-프론트엔드를-tailwind-v4--styleseed-라이트-토큰으로-전환하고-전역-오류-복구-boundary를-추가한다) |
| ADR-18 | target 의존을 선형 누적에서 `depends_on` DAG로 전환한다 (concierge는 geo 비의존) | accepted | [`decisions-archive-01.md`](decisions-archive-01.md#adr-18-target-의존을-선형-누적에서-depends_on-dag로-전환한다-concierge는-geo-비의존) |
| ADR-19 | 관리자 인증과 공개 API 키 관리를 `kor-travel-geo` PR #399 패턴으로 맞춘다 | accepted | [`decisions-archive-01.md`](decisions-archive-01.md#adr-19-관리자-인증과-공개-api-키-관리를-kor-travel-geo-pr-399-패턴으로-맞춘다) |
| ADR-20 | Map↔PinVi ops principal을 API 전용 read/cancel capability로 배포한다 | accepted (ops principal) / compatible-pair 실행 부분 superseded (application `300`) | [`decisions-archive-01.md`](decisions-archive-01.md#adr-20-mappinvi-ops-principal을-api-전용-readcancel-capability로-배포한다) |
| ADR-21 | C6c compatible pair에 clean source revision을 결박한다 | partially superseded (application `300` v6/v8 authority) | [`decisions-archive-01.md`](decisions-archive-01.md#adr-21-c6c-compatible-pair에-clean-source-revision을-결박한다) |
| ADR-22 | cAdvisor listen·healthcheck 포트를 단일 정본으로 관리한다 | accepted | [`decisions-archive-01.md`](decisions-archive-01.md#adr-22-cadvisor-listenhealthcheck-포트를-단일-정본으로-관리한다) |
| ADR-23 | Map production API 인증 환경을 C6c runtime set에 결박한다 | accepted | [`decisions-archive-01.md`](decisions-archive-01.md#adr-23-map-production-api-인증-환경을-c6c-runtime-set에-결박한다) |
| ADR-24 | Map destructive enablement는 Manager production source가 명시 승인한다 | accepted | [`decisions-archive-01.md`](decisions-archive-01.md#adr-24-map-destructive-enablement는-manager-production-source가-명시-승인한다) |
| ADR-25 | Map feature 관리 REST는 Manager production source가 명시 활성화한다 | accepted | [`decisions-archive-01.md`](decisions-archive-01.md#adr-25-map-feature-관리-rest는-manager-production-source가-명시-활성화한다) |
| ADR-26 | compatible-pair image를 content-addressed local reference로 보존한다 | partially superseded (application `300` v6/v8 authority) | [`decisions-archive-01.md`](decisions-archive-01.md#adr-26-compatible-pair-image를-content-addressed-local-reference로-보존한다) |
| ADR-27 | compatible-pair readiness는 canonical resolved Compose의 healthcheck 선언을 따른다 | partially superseded (application `300` v6/v8 authority) | [`decisions-archive-01.md`](decisions-archive-01.md#adr-27-compatible-pair-readiness는-canonical-resolved-compose의-healthcheck-선언을-따른다) |
| ADR-28 | cache-target 최초 production 전환은 default-off runner와 durable receipt로 수행한다 | accepted | [`decisions-archive-01.md`](decisions-archive-01.md#adr-28-cache-target-최초-production-전환은-default-off-runner와-durable-receipt로-수행한다) |
| ADR-29 | cache-target의 긴 pre-forward 검증은 독립 사전 진단 gate로 분리한다 | accepted | [`decisions-archive-01.md`](decisions-archive-01.md#adr-29-cache-target의-긴-pre-forward-검증은-독립-사전-진단-gate로-분리한다) |
| ADR-30 | legacy pre-stop diagnostic은 제한된 Manager receipt로만 퇴역한다 | accepted | [`decisions-archive-01.md`](decisions-archive-01.md#adr-30-legacy-pre-stop-diagnostic은-제한된-manager-receipt로만-퇴역한다) |
| ADR-31 | pinned compatible-pair drift는 one-shot bootstrap transaction으로만 수렴한다 | superseded (ADR-34, 2026-08-06) | [`decisions-archive-01.md`](decisions-archive-01.md#adr-31-pinned-compatible-pair-drift는-one-shot-bootstrap-transaction으로만-수렴한다) |
| ADR-32 | pinned source selection은 user-owned Git checkout과 분리한 trusted transaction이다 | accepted | [`decisions-archive-01.md`](decisions-archive-01.md#adr-32-pinned-source-selection은-user-owned-git-checkout과-분리한-trusted-transaction이다) |
| ADR-33 | release pin 교체는 source와 runtime contract input을 같은 generation으로 설치한다 | partially superseded (F1D runtime/recovery와 legacy state 부분은 ADR-34, 2026-08-06) | [`decisions-archive-01.md`](decisions-archive-01.md#adr-33-release-pin-교체는-source와-runtime-contract-input을-같은-generation으로-설치한다) |
| ADR-34 | 비운영 deployment는 완전한 runtime generation과 새 schema로 재구축한다 | partially superseded (application-300 candidate·manifest/journal은 ADR-39, 2026-08-25) | [`decisions-archive-01.md`](decisions-archive-01.md#adr-34-비운영-deployment는-완전한-runtime-generation과-새-schema로-재구축한다) |
| ADR-35 | Map ADR-090 principal은 전용 PostgreSQL instance에서만 bootstrap한다 | superseded (ADR-37, 2026-08-17; Map principal 경계 원칙은 ADR-53이 다시 supersede — 본문 참조) | [`decisions-archive-01.md`](decisions-archive-01.md#adr-35-map-adr-090-principal은-전용-postgresql-instance에서만-bootstrap한다) |
| ADR-36 | 운영 콘솔을 Cobalt Workbench 디자인 시스템으로 수렴한다 | accepted | [`decisions-archive-01.md`](decisions-archive-01.md#adr-36-운영-콘솔을-cobalt-workbench-디자인-시스템으로-수렴한다) |
| ADR-37 | PostgreSQL은 프로젝트마다 전용 instance를 쓰고 DB 포트는 대역의 `x00`이다 | accepted (concierge 범위는 ADR-44로 일부 superseded; 이후 ADR-45/46/47/53이 나머지 범위도 — 본문 참조) | [`decisions-archive-01.md`](decisions-archive-01.md#adr-37-postgresql은-프로젝트마다-전용-instance를-쓰고-db-포트는-대역의-x00이다) |
| ADR-38 | `pinvi-pair capture`는 컨테이너를 관측만 하고 C7 runner용 v4 manifest를 원자적으로 갱신한다 | superseded (2026-08-26, application `300` v6/v8 단일 정본) | [`decisions-archive-01.md`](decisions-archive-01.md#adr-38-pinvi-pair-capture는-컨테이너를-관측만-하고-c7-runner용-v4-manifest를-원자적으로-갱신한다) |
| ADR-39 | Map application은 paired candidate와 fresh `300` 증거로만 재구축한다 | accepted | [`decisions-archive-01.md`](decisions-archive-01.md#adr-39-map-application은-paired-candidate와-fresh-300-증거로만-재구축한다) |
| ADR-40 | pinned revision은 코드 상수가 아니라 root 소유 registry 파일이 소유하고, pinset의 생애 상태를 기계가 강제한다 | accepted | [`decisions-archive-01.md`](decisions-archive-01.md#adr-40-pinned-revision은-코드-상수가-아니라-root-소유-registry-파일이-소유하고-pinset의-생애-상태를-기계가-강제한다) |
| ADR-41 | host mutation lease는 부팅 시점에 만들고, 상호배제는 자기 자신이 보증한다 | accepted (두 번째 lease `pinned-runtime-rebuild.lock`과 비운영 lock 경로의 process env — 본문 참조) | [`decisions-archive-02.md`](decisions-archive-02.md#adr-41-host-mutation-lease는-부팅-시점에-만들고-상호배제는-자기-자신이-보증한다) |
| ADR-42 | Map application head는 candidate가 선언하고 두 독립 출처가 합의할 때만 받는다 | accepted | [`decisions-archive-02.md`](decisions-archive-02.md#adr-42-map-application-head는-candidate가-선언하고-두-독립-출처가-합의할-때만-받는다) |
| ADR-43 | 봉인된 baseline digest는 `300` 도달 순간에만 대조하고, 그 너머는 receipt가 정본이다 | accepted | [`decisions-archive-02.md`](decisions-archive-02.md#adr-43-봉인된-baseline-digest는-300-도달-순간에만-대조하고-그-너머는-receipt가-정본이다) |
| ADR-44 | concierge를 공용 제어 평면 PostgreSQL instance(`:11000`)로 이전한다 — ADR-37의 concierge 범위 부분 supersede | accepted (구현 진행 중 — instance·role·database는 이 변경으로 만들어졌고, 본문 참조) | [`decisions-archive-02.md`](decisions-archive-02.md#adr-44-concierge를-공용-제어-평면-postgresql-instance11000로-이전한다--adr-37의-concierge-범위-부분-supersede) |
| ADR-45 | geo를 공용 제어 평면 PostgreSQL instance(`:11000`)로 이전한다 — ADR-37의 geo 범위 부분 supersede | accepted (구현 진행 중 — instance·role·database 생성 코드는 이 변경으로, 본문 참조) | [`decisions-archive-02.md`](decisions-archive-02.md#adr-45-geo를-공용-제어-평면-postgresql-instance11000로-이전한다--adr-37의-geo-범위-부분-supersede) |
| ADR-46 | PinVi를 공용 제어 평면 PostgreSQL instance(`:11000`)로 이전한다 — 데이터 보존 없이, ADR-44의 pinvi 범위 확장 | accepted (구현 진행 중 — instance·role·database·DSN 배선은 이 변경으로, 본문 참조) | [`decisions-archive-02.md`](decisions-archive-02.md#adr-46-pinvi를-공용-제어-평면-postgresql-instance11000로-이전한다--데이터-보존-없이-adr-44의-pinvi-범위-확장) |
| ADR-47 | weather를 external target에서 Manager internal target으로 전환한다 — 앱·Dagster DB 둘 다 공용 instance로, 데이터 보존 없이 | accepted (Web 포트 `14105` 부분만 2026-10-08 ADR-57로 superseded — `12205`. 본문 참조) | [`decisions-archive-02.md`](decisions-archive-02.md#adr-47-weather를-external-target에서-manager-internal-target으로-전환한다--앱dagster-db-둘-다-공용-instance로-데이터-보존-없이) |
| ADR-48 | Prometheus/cAdvisor/Grafana host 포트를 12102/12103/12104로 재배정한다 — ADR-10의 포트 배정 부분 supersede | accepted (repo-level 설정·문서 변경만 이 변경의 범위 — n150 실제 재배포는 별도, 본문 참조) | [`decisions-archive-02.md`](decisions-archive-02.md#adr-48-prometheuscadvisorgrafana-host-포트를-121021210312104로-재배정한다--adr-10의-포트-배정-부분-supersede) |
| ADR-50 | 보안모델과 이미지 정합성은 과결박 없이 단순하게 간다 — 상시 설계 원칙 | accepted (상시 원칙 — 개별 변경이 아니라 앞으로의 모든 설계 결정에 적용) | [`decisions-archive-02.md`](decisions-archive-02.md#adr-50-보안모델과-이미지-정합성은-과결박-없이-단순하게-간다--상시-설계-원칙) |

---

## ADR-51: 배포는 마이그레이션 전진이다 — 자동 리셋을 없애고, 재구축 상태기계와 봉인·신뢰 릴리스 결박을 걷어낸다

- 상태: accepted
- 날짜: 2026-09-26
- 결정자: 사용자(잃는 보장 목록 전체 승인, "마이그레이션 전진", 쌍 preflight 게이트 유지,
  M05 유지, rebuildable 유지, `--restart` 전 백업 강제 안 함), Claude
- 관련: ADR-50의 미해결 첫 항목을 푼다. ADR-34·39·46의 "데이터 보존 없음" 전제와 ADR-40의
  pinset 영구 차단 부분을 supersede한다. 건너편은 kor-travel-map ADR-102다. ADR-49는 결번으로
  둔다.

### 컨텍스트

pinned-runtime 재구축은 새 pinset마다 Map·PinVi DB를 지우고 다시 만든다. 그 위에 phase 스물,
receipt·intent 여럿, 재개 규칙, pinset별 v8 journal, 실행 레지스트리, Map storage permit이
쌓였다. 재조사해 보니 그 대부분은 두 질문에 답하려고 존재했다 — "리셋 도중 죽으면 어디서
재개하는가"와 "이 DB가 이번 회차에 만든 그 DB인가". 그 기계가 낸 비용은 ADR-50 목록 그대로다
(영구 고착된 pinset, 봉인된 실패, 탈출구가 새 커밋뿐인 상태).

### 결정

1. **자동 리셋 경로를 없앤다.** 배포는 DB를 보존하고 멱등 one-shot으로 head까지 올린다
   (Map `alembic upgrade head` + 권한 재조정, Dagster storage one-shot, `pinvi-admin-bootstrap`).
   DB를 지우는 길은 `ktdctl pinvi-pair rebuild-pinned --restart --reason "<사유>" --confirm`
   하나다(rebuildable 모드에서만). 리셋 전 백업은 강제하지 않는다.
2. **영속 상태는 전역 `deploy-status.json` 하나다** — `absent` / `in_progress` / `committed`.
   pinset별 journal, phase, receipt, permit, 실행 증거는 없앤다. 재개는 없고 처음부터 다시 돈다
   (모든 단계가 멱등이다). committed와 같은 pair를 다시 돌리면 빌드 없이 수렴만 한다. 상태 파일이
   없거나 채택에 실패해도 결과는 전체 경로 한 번이지 리셋이 아니다.
3. **기계가 강제하는 성질은 다섯이다.** (1) 이미지는 핀된 커밋에서 빌드된다(라벨 + 해시 잠금
   의존성) (2) migration이 런타임보다 먼저 끝나고 Manager가 head를 독립으로 읽는다 (3) mutator는
   한 번에 하나(`/run/lock` flock 하나) (4) 무엇도 자동으로 리셋되지 않는다 (5) 비밀은 출력되지
   않고 Dagster gRPC는 loopback이다. 이 다섯을 두 번 증명하거나, 빌드 때 정해진 사실을 런타임에
   다시 확인하거나, root 소유 상태를 root로부터 지키는 검사는 걷어낸다.
4. **그대로 두는 것.** pinset id 공식(`canonical_pinset_sha256` 바이트 동일 — 바꾸면 committed
   세대를 못 찾는다), 2-revision 핀 원장, Map/PinVi 쌍 계약 preflight **게이트**(회전 전),
   M05 격리 하네스, 컨테이너별 이미지 대조(companion 포함), one-shot writer 퇴역.
5. **C6c 보호 참조는 리터럴 표 대신 파생 규칙으로 둔다.** UI/API 사용자는 git 리뷰 없이 compose
   env를 고친다 — in-model 행위자다. 그 후보의 (service, env key) → 보호 변수 참조는 설치된 릴리스
   git compose의 참조의 부분집합이어야 한다. 보호 변수는 민감 키 이름이거나 값이 `.env` 비밀과 같은
   변수다. runtime 서비스의 `env_file`은 계속 금지한다.
6. **락은 먼저 합치고 나서 줄인다.** rehearsal 모드에서 UI mutator는 다른 락 파일을 잡는다.
   backend API와 legacy override retirement까지 `/run/lock`의 전역 락 하나로 모은 뒤에야 pinned
   lease와 env 파생 락을 없앤다.
7. **호스트 스크립트는 같은 변경에서 함께 바꾼다.** chain16·repin.sh·gen_attest.py·run-d2.sh는
   v6/v8 파일에 묶여 있다. kor-travel-map의 `scripts/n150/`로 옮겨 버전 관리한다.

### 잃는 보장 (사용자 승인, 2026-09-26)

- **A. journal**: 중간 재개, phase별 증거, 크래시와 재개 사이의 `.env`/compose 변경 감지.
- **B. 실행 결박**: 막힌 pinset 28개·실행 27개의 영구 거부(경고로 바뀐다 — 알고 재배포할 수 있다),
  Manager 리비전과 pinset의 기계 결박(기록만 남는다).
- **C. Map storage 봉인**: DB oid·system identifier·이미지·config sha 결박, DB 안 append-only 기록,
  매 기동 정확 catalog 대조, entrypoint argv 봉인(loopback 가드만 남는다).
- **D. 설치기**(1,887줄 → 약 120줄): 설치 중 root 변조 감지(`.env` identity, RECORD digest,
  dev/ino), crash-reconcile 상태기계, 백엔드 재시작 지연 옵션. releases/와 symlink로 바꾸되
  compose 프로젝트 루트는 resolve하지 않는다(상대 bind가 지워질 release 경로에 묶이지 않게).
- **E. 소스 봉인**: 워크트리 불변화, symlink·submodule 금지, 로컬 origin 확인 — 실행마다
  `fetch` + `git archive <sha>`로 바꾼다. SHA가 곧 증명이다.
- **F. Map 측 attestation 체인**: 러너 스냅샷, `/etc` attestation, gen_attest.py.
- **G. 실패 출력**: 닫힌 어휘. 원문을 스크럽해 남긴다(#399가 1단계). `.env`에 없는 비밀은
  스크럽을 빠져나갈 수 있다.
- **H. Manager의 Map DB 권한 재검증**: virgin/bootstrap attestation. Map migration one-shot과
  런타임 head 확인에 맡긴다.
- **리셋을 기본에서 뺀 대가**: 배포가 빈 DB 경로를 증명하지 않는다(`--restart`·M05·CI에서만),
  손으로 만든 drift가 살아남는다, 낮은 head로 되돌릴 수 없다(roll-forward 또는 `--restart`),
  Map 서비스 login의 `.env` 비밀번호 회전이 배포로 반영되지 않는다(후속 reconcile),
  `pinvi-admin-bootstrap`이 매 배포 bootstrap admin을 복구한다, `--restart`가 유일한 파괴 경로다.

### 순서

doc-only ADR(이것과 Map ADR-102) → A(storage 영수증 없이 통과) → Map M1(storage one-shot 멱등화)
→ Map M2 + 호스트 스크립트 → B1(`deploy_status`·ensure helper, 미연결) → B2(전환, `--restart`,
현재 committed 세대 채택 — 추가 리셋 0회) → B3(v8·evidence 삭제) → C(락 통합) → D(permit mount
제거, v6 쓰기 중단). 결정 2와 무관한 항목(설치기·소스 봉인·실패 출력·compose 규칙)은 그 뒤 각자.
급한 Map 수정이 생기면 전환 전 구 모델로 리셋 포함 배포를 한 번 더 허용한다.

### NOTE: C 범위 — 하나의 락에 있는 것과 일부러 뺀 것 (2026-09-27, C-2)

락은 `/run/lock/kor-travel-docker-manager/global-mutation.lock`(G) 하나다. 모든 획득은
`LOCK_EX|LOCK_NB`이고 기다리지 않는다 — launcher·CLI는 종료 코드 2(또는 non-zero), backend
API는 409 `MANAGER_MUTATION_ACTIVE`. 예외는 비root 개발용 `local`의 `$HOME` 락 하나다.

**G 위에 있는 것** (production·rehearsal 공통):

- launcher: `run-pinned-rebuild-once`(재구축), `run-m05-isolated-e2e-once`(M05), trusted installer.
- `ktdctl pin` mutator(rotate·rotate-pair·apply-pending·rollback·block·init …), `rebuild-pinned`.
- `ktdctl compose-boundary` stage·retire·activate-concierge — C-2 전 rehearsal은 pinned lease
  P만 잡았다.
- backend API의 Manager 자기 프로젝트 컨테이너 조작·설정·초기화·compose 저장, frozen recovery —
  C-2 전 rehearsal은 실행 사용자 `$HOME` 락을 잡고 있었다(모드 검사에서 거절되긴 했지만 락은
  G와 무관했다).
- 관리자 비밀번호 변경의 `.env` 단일 키 재작성 — retire가 lock 아래에서 읽은 바이트로 `.env`를
  다시 쓰므로, 그 사이의 비밀번호 변경이 조용히 사라지는 lost update를 닫는다.

재구축의 세 번째 획득(`.env`에서 유도한 락)은 C-2부터 G와 같은 key라 재진입 no-op이다. 파일 락은
G와 P 두 개이고, P는 C-3에서 지운다.

**일부러 G 밖에 둔 것** — Manager의 compose·`.env`·레지스트리·`/opt` 상태를 바꾸지 않거나
제안에 그친다:

- **백업**: standalone backup·gc·rehearse-restore는 역할별 `.backup.lock`을 그대로 쓴다. cron
  (03:15·03:30·03:55 UTC)은 C의 영향을 받지 않는다. pinvi backup이나 rehearse-restore는 지금처럼
  `--restart`와 겹칠 수 있다.
- **offbox 동기화**.
- **제안**: runtime-pin 요청 POST/DELETE와 `ktdctl pin clear-pending`. `apply-pending`이 G 안에서
  다시 검사한다.
- **transport 컨테이너**: 외부 target `transport`, 정확히 compose 프로젝트
  `kor-travel-transport`에 등록된 컨테이너(`kor-travel-transport-backend-1`·`-frontend-1`)의
  SDK start·stop·restart. 남의 compose를 바꾸지 않고 Manager 락이 지키는 대상도 아니다.
  이름 접두사로 넓히지 않는다 — 같은 저장소의 별도 compose 프로젝트
  `kor-travel-transport-admin`은 Manager에 등록되지 않았고 이 범위에 없다. (2026-09-28 개정:
  처음 이 줄은 개명 전 이름의 접두사 glob으로 적혀 있었다 — target이 `airport`에서
  `transport`로 바뀌면서 정확한 프로젝트 이름으로 좁혔다.)

### NOTE: C 완료 — 락을 줄였다 (2026-09-27, C-3)

결정 6의 "줄인다"가 끝났다. Manager의 파일 락은 G 하나다.

- pinned lease P(`pinned-runtime-rebuild.lock`)와 그 획득 함수를 지웠다. 재구축은 G를 한 번
  잡고(launcher가 물려준 fd면 검증해 그대로 쓴다) 그 안에서 `.env`를 캡처·검증하고 본문을 돈다.
  세 번째 획득과 그 lock snapshot도 없다. 캡처 뒤 `.env`의 디스크 재확인은 하지 않는다 — 재구축
  본문은 캡처한 snapshot(동결된 Compose 문서와 그 effective 값)으로만 돌고, 본문 내내 쥔 G가
  Manager의 모든 `.env` 쓰기를 막는다(C-3 적대 리뷰: 대신 둔 대조가 같은 객체끼리 비교해 실패할 수
  없었으므로 지웠다).
- 경로 유도는 `manager_mutation_lock_path`: `local`만 `$HOME` 개발 락이고, 미지정·미지의 모드를
  포함한 나머지는 전부 G다(fail closed). `KTDM_C6C_DEPLOYMENT_LOCK` override를 지웠다. 경로는
  `.env` 파일 값으로 정하고 프로세스 환경으로 **채우지** 않는다(ADR-41의 교훈). 단 프로세스 환경의
  `KTDM_DEPLOYMENT_ENVIRONMENT`가 명시적으로 local이 아니면 파일이 local이어도 G다 — 프로세스
  환경은 락을 **더 엄격하게만** 바꾼다(파일만 고치고 재시작하지 않은 backend).
- root 요구는 재구축 입구의 root 검사와 G 디렉터리 소유자 검사가 그대로 맡는다.
- C 밖에 남긴 것(D 이후): CLI의 terminal block 상속 fd 예외와 M05 launcher의 fd 전달,
  installer의 자체 lock 코드, ADR-41의 획득 뒤 재검사 정리.

### NOTE: D-1 — v6 manifest의 reader를 옮기거나 지웠다 (2026-09-27, D-1)

순서의 마지막 항목 D(permit mount 제거, v6 쓰기 중단)를 세 PR로 나눈다 — D-1 reader 이전·삭제,
D-2 v6 쓰기와 v6 코드 삭제, D-3 permit mount 제거. 각 릴리스는 직전 릴리스 위에 설치되고, 직전
릴리스로 되돌려 설치해도 동작한다. D-1은 v6 쓰기를 건드리지 않는다 — 커밋은 계속 쓰지만 아무도
읽지 않는다.

- **M05 driver는 `deploy-status.json`을 읽는다.** `_source_pair_preflight`가 private v6 manifest 대신
  state root의 `deploy-status.json`을 읽고, 기록이 없거나 읽히지 않으면·`committed`가 아니면·pinset이
  현재 release와 다르면·`schema_heads.map_application`이 materialize된 Map source에서 파생한 head와
  다르면 실행권 소비 전에 `pair_contract_invalid`와 닫힌 어휘 진단 넷 중 하나로 거부한다. 읽기는
  `materialize_pinned_runtime_sources` 뒤에 둔다 — 그 호출이 state root의 소유자·`0700`을 검증하고
  `read_deploy_status`는 그것을 보지 않는다. `manager_revision`·image는 대조하지 않는다(Manager 설치는
  재배포 없이 revision을 바꾸고, M05는 자기 이미지를 빌드한다). committed 배포에서는 옛 v6 대조와 같은
  값을 본다 — 커밋이 두 파일에 같은 head를 쓴다.
- **`in_progress`를 거부한다.** `begin_deploy`가 committed 기록을 덮어쓰므로, 실패한 배포(같은 pair
  포함) 뒤에는 한 번 commit될 때까지 M05가 멈춘다. 의도된 fail-close다 — 옛 v6 대조는 마지막 커밋
  세대만 보므로 그 뒤 실패한 배포를 몰랐다.
- **지운 것, 대체 없음**: `GET /api/v1/pinned-runtime/generation`, root `ktdctl pin publish-generation`,
  `pin verify`의 `generation_public_copy`·`generation_pinset_binding` 키와 그 exit-1 분기, rebuild
  preflight §2(`GENERATION_UNVERIFIED`). `pin verify` 종료 코드는 pin registry·execution registry·회전
  intent·runtime-pins 공개 사본만으로 정한다. preflight의 `can_start`는 §1 때문에 이미 늘 false였으므로
  바뀌지 않는다.
- **잃은 것(기록된 손실)**: 공개 generation view. 비-root backend·UI·Map·PinVi는 배포가 어디까지
  끝났는지 볼 수 없고, root가 `deploy-status.json`·rebuild `result.json`을 읽는다. 다시 필요해지면 두
  번째 공개 사본이 아니라 ADR-41의 "소유자는 root, 접근만 그룹" 패턴으로 연다.
- **D-2까지 남긴 것**: 커밋의 v6 manifest·공개 사본 쓰기와 `pinned_runtime_generation.py`의 v6 함수(이제
  테스트만 부른다). D-1 아래로 되돌린 Manager의 M05 driver·`pin verify`가 v6를 읽으므로 쓰기는 D-1
  설치 뒤에 멈춘다(D-2). 순서를 뒤집으면 새 pair 배포 뒤 옛 M05가 "pinset differs"로 멈춘다.
- 교차 저장소: Map `docs/integration-map.md`와 PinVi `docs/runbooks/live-mutating-e2e.md`의 generation
  API 절차는 "`ktdctl pin verify` exit 0 + M05 launcher preflight 통과"로 바뀐다(문서 PR만, 코드 변경 없음).

### NOTE: D-2 — v6 쓰기를 멈추고 v6 코드를 지웠다 (2026-09-27, D-2)

D-1이 설치된 위에서 커밋의 v6 쓰기를 멈춘다. 커밋이 남기는 기록은 `deploy-status.json` 하나다.

- **커밋은 v6를 쓰지 않는다.** `compose_service`의 커밋은 `write_deploy_status`만 부른다. 그 기록
  쓰기가 실패해도 검증이 끝난 런타임을 내리지 않는다는 규칙은 그대로다 — 상태는 `in_progress`로
  남고 다음 실행이 처음부터 다시 돈다. 덤으로, 옛 `write_manifest` 안의 공개 사본 쓰기가 런타임이
  뜬 뒤 실패해 배포를 실패로 만들던 자리가 없어졌다.
- **지운 코드**: `pinned_runtime_generation.py`의 `PinnedRuntimeManifest`, manifest·generation·candidate
  evidence payload parser(살아 있는 호출자가 manifest reader뿐이었다), `read_manifest`·`write_manifest`,
  공개 사본 경로·발행·reader와 그 결박·요약 helper, private·public JSON IO helper, manifest 상수,
  `PinnedRuntimeStatePaths.manifest` 필드. 남긴 것은 in-memory `PinnedRuntimeGeneration`·
  `MapApplication300CandidateEvidence`·`generation_logical_sha256`(rebuild `result.json`의
  `generation_sha256`), 배포 mode 검증과 state 경로 함수다.
- **남긴 것**: `TRUSTED_PUBLIC_ROOT`와 `/var/lib/kor-travel-docker-manager-public` — runtime-pins·
  runtime-executions 공개 사본이 쓴다. `migrate-execution-v6`·`ExecutionIdentityV6`·
  `pinned-runtime-candidate-v6/` image prefix는 v6 manifest와 무관한 이름이라 그대로다.
  `KTDM_PINNED_RUNTIME_PUBLIC_ROOT`는 더 아무 의미가 없다(`.env`에 남아 있어도 무해).
- **on-disk 계약이 옮겨 갔다.** v6 스키마 동결은 끝났고, 이제 동결 대상은 rebuild와 M05가 함께 읽는
  `deploy-status.json`이다 — 옛 version reader 없이 `_VERSION`을 올리면 둘 다 멈춘다
  (`runtime-pin-registry.md` §1-2).
- **되돌림 하한**: 호스트의 v6 파일은 더는 갱신되지 않으므로 D-2 이후 되돌릴 수 있는 가장 낮은
  Manager는 D-1이다. D-1 이전 release의 M05 driver는 그 옛 파일을 현재 pinset과 대조하므로 D-2
  이후 새 pair가 커밋되면 "pinset differs"로 멈춘다.
- **호스트 잔여물**: private·공개 `pinned-runtime-generation-v6.json`과 공개 `pinned-runtime-rebuild-v8.json`은
  아무것도 읽지 않는다. 지우는 것은 선택이고 D-1 이전으로 되돌릴 일이 없다고 판단한 뒤에만 한다
  (`prod-deployment.md` §8.1). 교차 저장소 변경은 없다.

### NOTE: D-3 — permit mount를 걷어냈다, D 완료 (2026-09-27, D-3)

D-2가 설치된 위에서 Map Dagster storage permit의 마운트·env·allowlist를 걷어낸다. 전제는 n150에 핀된
Map이 M1(`51ee65d4c`, ADR-102)을 포함하는 것이다 — M1 이후 storage one-shot은 permit 디렉터리도
`KOR_TRAVEL_MAP_DAGSTER_STORAGE_PERMIT_IMAGE_ID`·`..._STORAGE_CONFIG_SHA256`도 읽지 않는다.

- **지운 것**: Manager compose의 Map 서비스 넷(`kor-travel-map-dagster`·`-dagster-code-server`·
  `-dagster-daemon`·`-dagster-storage-migrate`)의 `/run/kor-travel-map-dagster-storage-permit` bind와
  `..._STORAGE_PERMIT_IMAGE_ID`·`..._STORAGE_CONFIG_SHA256`·`..._APPLICATION_FINAL_PERMIT_DAGSTER_IMAGE_ID` env,
  `kor-travel-map-api`의 죽은 `..._APPLICATION_FINAL_PERMIT_API_IMAGE_ID`. `config/docker-targets.yml`의 같은
  네 서비스 `compose_binds` 항목은 키째 지웠다(목록이 비면 validator가 거부한다). 코드에서는
  `compose_service`의 pinset별 영수증·permit 디렉터리 준비와 prebuild snapshot의 permit override,
  `MapApplication300ArtifactDirectories`, `generation_compose_environment`·`CandidateRuntimeBuild`의
  permit·config sha env, M05의 죽은 `KOR_TRAVEL_MAP_APPLICATION_FINAL_PERMIT_VOLUME`. compose가
  `${KOR_TRAVEL_MAP_DAGSTER_STORAGE_PERMIT_DIR:?}`를 쓰던 동안에는 셋 중 하나만 바뀌어도 모든 재구축이
  막히므로 compose·allowlist·코드를 한 PR로 옮겼다. Map 서비스의 compose bind와 `compose_binds`가 양방향으로
  같은지는 이제 계약 테스트가 본다.
- **남긴 것**: `KOR_TRAVEL_MAP_POSTGRES_IMAGE_ID`. M05의 `KOR_TRAVEL_MAP_DAGSTER_STORAGE_PERMIT_VOLUME` —
  M05는 Map 자신의 compose를 돌리고 핀된 Map revision이 아직 그 volume을 선언한다. Map이 standalone permit
  volume을 지운 revision이 n150에 핀된 뒤에 지운다. `MapApplication300CandidateEvidence.dagster_config_sha256`은
  판정에 쓰는 곳이 없고, `source_status`의 `_PERMIT_DIR`·`_FENCE_DIR` suffix는 이제 어떤 compose 변수에도
  걸리지 않는다(선택 D-4 — 앞의 것을 지우면 `result.json`의 `generation_sha256` 값이 바뀐다).
- **첫 수렴**은 `kor-travel-map-api`(env)·`-dagster`·`-dagster-daemon`·`-dagster-code-server`를 다시
  만든다. storage one-shot은 전체 경로에서만 돈다. `.env`에 남은 `KOR_TRAVEL_MAP_DAGSTER_STORAGE_PERMIT_DIR`·
  `..._CONFIG_SHA256`은 무해하다(`prod-deployment.md`의 퇴역 키 목록).
- **D 완료 — 잃은 것(기록된 손실)**:
  - 공개 generation view(D-1). 비-root backend·UI·Map·PinVi는 배포 진행을 볼 수 없다.
  - M05는 실패한 배포(같은 pair 포함) 뒤 한 번 commit될 때까지 거부한다(D-1).
  - M1 이전 Map pinset(예: Map `6511441f` / pinset `e4909e26`)으로 되돌리면 storage one-shot에서 실패한다 —
    그 이미지는 permit을 요구하는데 마운트가 없다(`dagster_storage_permit_unavailable`). 배포는
    `in_progress`로 남고 runtime은 멈춘 채다. 복구는 roll-forward(M1 이후 pair로 회전해 재구축)뿐이고,
    가드를 다시 넣지 않는다.
- **되돌림 하한**: Manager는 여전히 D-1이다. D-2·D-1 release는 permit 디렉터리를 스스로 만들어 마운트하고
  M1 이후 Map은 그것을 무시하므로, D-3 아래로 되돌려 설치해도 동작한다.
- **D 밖에 남긴 것**: C-3 NOTE의 CLI terminal block 상속 fd 예외와 M05 launcher fd 전달, installer 자체
  lock 코드, 위의 M05 volume과 선택 D-4. 호스트의 `<state_root>/map-application-300-artifacts/`·
  `map-application-300-candidate/`는 아무것도 읽지도 쓰지도 않는다 — 지우는 것은 선택이다
  (`prod-deployment.md` §8.1).
- 교차 저장소: Map `docs/architecture/dagster-boundary.md`의 "Manager가 전환기 동안 permit 디렉터리를 계속
  마운트한다"는 서술이 더는 사실이 아니다 — Map 문서 PR만 필요하다(코드 변경 없음).

### NOTE: D(설치기) I-1 — 설치 root symlink의 소비자를 먼저 옮겼다 (2026-09-27, I-1)

잃는 보장 D(설치기)를 세 단계로 나눈다 — I-1 소비자 이전, 한 번뿐인 수동 레이아웃 전환, I-2 새
설치기. D-1/D-2와 같은 순서다: 읽는 쪽을 먼저 옮기고 쓰는 쪽을 나중에 바꾼다. I-1은 옛 설치기로
평평한 레이아웃에 설치되고, 거기서는 resolve와 lexical 경로가 같으므로 동작이 바뀌지 않는다.

- **레이아웃 해석.** ADR의 "releases/와 symlink"는 `/opt/kor-travel-docker-manager` → 상대 symlink
  `ktdm-release-<sha40>`, 실제 release는 `/opt/ktdm-release-<sha40>/`(root 0755)로 둔다. `/opt/.../releases/<sha>`
  + `current`는 기각했다 — 안정 경로가 바뀌어 launcher 넷·systemd·Map 호스트 스크립트를 한 번에 고쳐야
  하고 상대 bind 서비스가 한 번 재생성된다. release를 `/opt` 바로 아래에 두므로 resolve한 `..`와 lexical
  `..`가 같은 `/opt`를 가리키고(형제 저장소 기본값 `../kor-travel-concierge` 등), `ktdm-release-`는
  `kor-travel-docker-manager`와 문자열 접두가 겹치지 않는다. compose v5.2와 venv가 symlink 경로를 그대로
  쓰는 것은 n150에서 실측했다(`--project-directory <link>` → bind source `<link>/cfg`, `sys.prefix` 유지).
- **resolve하지 않는 자리**(compose 프로젝트 루트가 되는 경로): 재구축 root(`trusted_pinned_runtime_project_root`),
  환경 snapshot의 compose·override 경로(frozen `config`의 `--project-directory`가 된다), canonical `up`의
  `--project-directory`, legacy override retirement의 project context(Compose cwd·`--file`·projection 임시 파일).
  하나라도 풀리면 상대 bind source가 `/opt/ktdm-release-<sha>`로 굳어 설치마다 컨테이너가 재생성되고 GC 뒤
  재시작에서 깨진다. 비교하는 쪽은 양쪽을 다 푼다 — 재구축 경로 가드, operator bind의 backend 소스 가드
  (한쪽만 풀던 것이라 symlink root에서 조용히 fail-open했다), candidate volume graph(원래 양쪽 다 풀었다).
- **symlink를 따라가는 자리**: 재구축 root·legacy retirement의 디렉터리 검사, 관리자 비밀번호 `.env`의 부모
  검사, M05 driver·`run-pinned-rebuild-once`·`trusted_manager_source_revision`의 설치 root 검사. 옛 코드는
  `lstat`로 symlink root를 거부했다. 따라간 대상이 디렉터리·root 소유·group/other 쓰기 금지인지는 그대로 본다.
- **provenance는 `.ktdm-source-revision` 하나다.** `trusted_manager_source_revision`(rebind-execution),
  `run-pinned-rebuild-once`, M05 driver, `source_status`가 release manifest를 읽지 않는다. 옛 설치기는 I-2까지
  manifest를 계속 쓰지만 아무도 읽지 않는다. `source_status`의 `inconsistent` 상태와 `manifest` 필드가
  없어졌다(UI는 쓰지 않았다).
- **잃은 것**: 설치 root의 lstat 신원 확인(dev/ino·symlink 거부)과 revision·manifest 두 기록의 교차 대조 —
  결정 3의 "root 소유 상태를 root로부터 지키는 검사"다. bind 가드의 resolve는 손실이 아니라 fail-open 수정이다.
- **되돌림 하한**: I-1 이하 어디로든 옛 설치기로 되돌릴 수 있다(평평한 레이아웃). 레이아웃 전환 뒤에는 옛
  설치기가 "canonical .env path" 검사로 해 없이 거부하므로, 그 아래로 가려면 전환을 먼저 되돌린다
  (`prod-deployment.md`에 I-2와 함께 적는다).

### NOTE: D(설치기) I-2 — 설치기를 release 디렉터리와 symlink로 다시 썼다, D 완료 (2026-09-27, I-2)

I-1과 한 번뿐인 레이아웃 전환(`prod-deployment.md` §3.0) 뒤에 설치기를 다시 썼다. 1,887줄이 196줄(주석·빈
줄 제외 145줄)이 됐다.

- **하는 일**: root 소유 clone인지 보고 `cd /` → G를 잡는다 → clone에 그 commit이 있는지 본다 → `/opt/ktdm-release-<sha>`에 revision 표식이
  없으면 `git archive <sha>`를 풀고 `chmod -R go-w`, 오프라인 wheelhouse로 venv, `ktdctl` wrapper(설치 root
  경로 shebang + `KOR_TRAVEL_DOCKER_MANAGER_PROJECT_ROOT`), import 확인, 마지막에 표식 → 지금 `.env`를 복사 →
  tmpfiles·backend·frontend 유닛·logrotate를 그 release에서 설치 → backend stop → symlink flip → `reset-failed`(깨진 release가 재기동 한도를 넘겼어도 롤백 start가 거절되지
  않게) → start → **그 유닛의 MainPID가 12901을 듣고** `/health` 200을 내는지 60초 → 직전 release 하나만 남기고 GC. flip 전
  실패는 설치 root와 도는 backend를 건드리지 않는다(`/etc`의 유닛은 이미 새 release의 것일 수 있다).
- **롤백은 옛 sha 재설치다.** 표식이 있는 release는 빌드 없이 재사용하고 지금 `.env`를 앞으로 복사한다. 손으로
  symlink를 넘기면 옛 `.env` 사본(옛 비밀·관리자 해시)이 살아나므로 문서로 금지한다. `.env`를 공유 symlink로
  두지 않은 이유는 관리자 비밀번호 재작성(`O_NOFOLLOW`), legacy retirement(`nlink 1`·`0600`), 재구축의
  `ENV_FILE == root/.env`가 모두 실제 파일을 요구하기 때문이다.
- **지운 것(D의 잃는 보장)**:
  - `.env` identity: 스냅샷·fd 재실행·단계별 대조·fd 복사. 모든 `.env` 쓰기가 G 아래에 있으므로(C-2) G 아래의
    `cp -p` 하나로 대체했다.
  - RECORD digest: `ktdctl` RECORD 행 갱신과 설치 감사·release manifest(`.ktdm-release-manifest.json`). 읽는
    쪽은 I-1에서 이미 옮겼다.
  - dev/ino: wheelhouse 스냅샷, lock fd 재검증(ADR-41 결정 2의 설치기 적용분), 앱 트리 신원·신원 기반 삭제.
  - crash-reconcile 상태기계(`trusted-release-transaction.json`, stage/rollback 디렉터리, trap). 표식을 마지막에
    쓰고 같은 sha를 다시 돌리는 것이 복구다.
  - 백엔드 재시작 지연 옵션. 항상 stop → flip → start다. `--restart-backend`·`--allow-live`는 호환을 위해
    받되 무시하고, `--env-file`은 없앴다.
- **D 목록에 이름은 없지만 함께 지운 것**: `/proc` 실행 중 프로세스 스캔(실행 중인 트리를 옮기거나 지우지
  않는다), lock 전 tmpfiles 자가 복구(`install -d`로 대체), 설치기가 방금 만든 트리에 대한 root 대 root 검사와
  `systemd-analyze verify` 필터, lease 디렉터리 감사(결정 3), 0755 `chmod` 목록과 `bindings.md` B-1(git index가
  유일한 정본이 됐다), clean checkout 검사(`archive <sha>`라 결과와 무관), 설치본 안의 `.wheelhouse` 산출물 —
  그래서 `provision-ktdm-offline-wheelhouse.py`의 `--source-wheelhouse`는 이제 필수다.
- **새로 요구하는 것(적대 리뷰)**: source clone이 root 소유여야 한다. 옛 문서는 hash 대조로 root staging한
  installer만 실행하라고 했지만 실제 운영은 운영자 clone에서 바로 돌렸고, 그 계정(공개 트래픽을 받는
  `ktdm-frontend`의 실행 계정과 같다)이 다음 설치를 가져갈 수 있었다. root clone이면 git도 root로 돈다(옛
  `sudo -u <clone owner>`가 없어졌다). 성공 판정은 포트를 차지한 남의 프로세스를 새 release로 오인하지 않게
  MainPID의 listener를 본다.
- **남긴 것**: G(`umask 077`로 만들고 `0:600:1` 확인, 일반 파일이 아니면 열기 전에 거부, 경합이면 종료 코드 2), state·request 디렉터리(request
  소유권은 처음에만 정한다), 정확한 sha, 오프라인 wheelhouse(root 소유·group/other 쓰기 금지 한 줄), `ktdctl`
  wrapper, tmpfiles(ADR-41 결정 1)·systemd·logrotate와 그 `.env` 값 검증(비root 계정, 안전한 절대 경로).
- **바뀐 운영 성질**: G를 설치 내내(빌드 포함 1~2분) 쥔다 — 그동안 재구축·M05·UI mutation이 거절되고, 반대도
  같다. 설치는 항상 backend를 재기동하므로 G 밖에서 도는 UI 백업 job이 끊길 수 있다(옛 `--restart-backend`와
  같다). `backend/logs/`는 release 안에 있어 GC 때 사라진다(journald가 정본). 설치 root 밖에 남는 옛 설치기
  산출물(`/var/lib/kor-travel-docker-manager/trusted-release-tools-*`·`trusted-tool-bootstrap*`)은 아무것도
  읽지 않는다.
- **되돌림 하한**: I-1이다. 새 installer로 I-1을 설치하면 표식이 있는 레이아웃 전환 release를 재사용한다. 그
  아래는 §3.0 역전환 뒤 옛 installer로 간다. C-3 NOTE가 남겨 둔 "installer 자체 lock 코드"는 이것으로 끝났다.

### NOTE: E(소스 봉인) E-1 — 소스 reader를 파일 읽기로 옮겼다 (2026-09-27, E-1)

잃는 보장 E(소스 봉인)를 세 단계로 나눈다 — E-1 reader 이전, E-2 M05의 실행별 checkout, E-3 재구축 소스를
`fetch` + `git archive <sha>`로. D·I와 같은 순서다: 읽는 쪽을 먼저 옮긴다. E-1은 호스트 상태를 바꾸지 않는다.

- **재구축의 Map 계약 reader**(`candidate_contract` 단계 — 수렴 포함 매 실행): `docker-compose.yml`과 추적
  env_file을 `git show`·`ls-tree`·`cat-file` 대신 materialize된 source root에서 파일로 읽는다. root는 핀된
  revision 그대로의 트리이므로 같은 바이트다. env_file 규칙은 그대로다(일반 파일·실행 비트 없음 = 옛 "100644
  blob", 64 KiB, UTF-8, 보호 이름 금지, 없으면 건너뜀). 정화되지 않은 환경에서 root git을 부르던
  `_run_git_read`·`_run_git_bytes`가 없어졌다.
- **M05 `_pair`**: 네 표면 파일도 source root에서 읽는다. PinVi attestation이 service 표면을 그 표면의 릴리스
  revision에서 읽으므로 그 object를 보충하는 fetch는 E-2까지 남는다.
- **드러난 것**: 이 reader를 부르는 테스트는 함수 전체를 대역으로 바꾸고 있어서, source가 git 저장소가 아니면
  모든 재구축이 깨지는데도 CI는 초록이었을 것이다. 이제 `.git` 없는 디렉터리를 실제로 읽힌다.
- **잃은 것**: 없다. git index의 "추적되는 100644 blob"이 파일의 "일반 파일·실행 비트 없음"이 됐고, 트리가
  핀된 revision인 한 둘은 같다.

### NOTE: E(소스 봉인) E-2 — M05가 봉인 트리를 떠났다 (2026-09-27, E-2)

M05 본문은 봉인된 source 트리 대신 **실행별 checkout** 둘(`<leaf>/runtime/map-src`·`pinvi-src`)에서 돈다.
`checkout_pinned_run_source`가 `git init` → canonical HTTPS에서 핀된 revision만 `fetch --depth 1` →
`checkout --detach`로 만든다(재구축과 같은 정화된 root git). SHA가 곧 증명이라 tree·clean 재확인은 없다.

- **archive가 아니라 checkout인 이유**: PinVi attestation(`m05_activation_attestation.py`)이 진짜 clean
  checkout(`--show-toplevel`·HEAD·`status --porcelain --untracked-files=all`)과 Map의 service 릴리스 revision blob을
  요구한다. Map checkout이 그 revision을 함께 받는다. 이 예외를 없애려면 PinVi PR과 재핀이 필요하다.
- **preflight 자신은 fetch하지 않는다.** `_pair`의 fetch 보충(봉인 Map 트리의 object store에 service 릴리스
  revision을 넣던 것)이 없어졌다 — 그 object는 본문의 checkout이 받는다. 재사용하는 source materialize는 그
  pinset의 봉인 트리가 아직 없을 때(예: 회전 직후 재구축 전)만 받는다 — E-3 전까지 그대로다.
- **순서**: checkout은 `runtime_setup_workspace`(claim 전)에서 만든다. fetch가 실패해도 실행권을 쓰지 않는다.
  compose·빌드·러너·attestation이 모두 같은 두 checkout을 쓴다. cleanup이 지우고, 지우지 못하면 receipt의
  `disposable_run_worktree_retained`(이름은 그대로, 뜻은 "실행별 checkout이 남았다")만 켠다.
- **지운 것(E의 "워크트리 불변화" 중 M05 몫)**: 일회용 worktree 재유도(`expected_tree` 교차 대조, submodule·commit
  확인 포함)·제거·
  잔여물 요약 증거(`disposable-run-worktree.json`), 봉인 사후조건(실행이 봉인 트리를 건드리지 않았다는 관측).
  봉인 트리는 M05 본문에 쓰이지 않으므로 지킬 것이 없다.
- **남은 것**: 재구축 source의 봉인·staging(E-3에서 archive로), rotation preflight(결정 4의 게이트).

### NOTE: E(소스 봉인) E-3 — 재구축 소스를 fetch + archive로 바꿨다, E 완료 (2026-09-27, E-3)

재구축(과 M05 preflight)의 source는 revision으로 이름 붙은 디렉터리다 —
`<state_root>/pinned-runtime-sources/<role>-<revision>/{tree/, source.json}`. 없을 때만 임시 bare에 canonical
HTTPS에서 그 revision을 `fetch --depth 1`로 받아 `git archive`(`tar.umask=0022`, 저장소의 export-ignore·
export-subst는 `info/attributes`로 끔)를 풀고 `source.json`(role·revision·tree)을 쓴 뒤 rename으로 놓는다. 있으면
git을 부르지 않는다. `pinned_runtime_sources.py`는 754줄에서 401줄이 됐다(M05 실행별 checkout 포함).

- **"실행마다 fetch"의 해석**: 디렉터리 이름이 SHA이고 그 SHA가 곧 증명이므로 "없을 때만 fetch하고 같은
  revision은 재사용"으로 읽었다(설치기 I-2와 같은 방식). 같은 pair의 수렴은 네트워크 없이 끝나고, 새 pair의
  첫 재구축만 GitHub가 필요하다(전과 같다). revision 단위라 한쪽만 바뀐 회전은 다른 쪽을 다시 받지 않는다.
- **GC**: 재구축이 G 안에서 materialize 직후 이번 pair가 쓰지 않는 `<role>-<revision>`과 끊긴 `.partial-*`를
  지운다(실패해도 배포는 계속, 기록부터 지운다). M05와 preflight는 지우지 않는다.
- **손상**: 이름을 붙이기 전에 `os.sync()`로 내용을 디스크에 둔다 — 이름 붙은 트리는 git 없이 재사용되기
  때문이다. 기록이 없거나 깨졌거나 다른 revision을 가리키면(전원 차단, 끊긴 GC) 거부하지 않고 지운 뒤 다시
  만든다 — SHA로 이름 붙은 캐시라 다시 만드는 것이 언제나 안전하고, 거부하면 수동 삭제 전까지 모든 재구축과
  M05 preflight가 막힌다(적대 리뷰).
- **파일 모드**: 0444/0555 봉인이 0644/0755(실행 비트는 git index 그대로, 디렉터리는 호출자의 umask와 무관하게
  0755)가 됐다. 이미지 안의 파일 모드가 바뀌므로 새 pinset의 이미지 digest가 달라진다. 기존 pinset은 태그된
  이미지를 재사용하므로 다시 빌드하지 않는다.
- **지운 것(E 전부)**: 워크트리 불변화(0555/0444 봉인과 재사용 때마다의 전수 walk), symlink 금지 walk,
  submodule 금지(archive에서 gitlink는 빈 디렉터리가 되므로 빌드가 크게 실패한다), 로컬 origin 확인(`.env`의
  `*_REPO_DIR` checkout을 source 소유자 권한으로 읽던 동의 증거와 privilege drop), 결정 3에 따른 부수 제거(clean
  검사, 하위 디렉터리 owner/0700/symlink 재검사, staging·promotion 상태기계, 호출자 state_paths 대조).
  `.env`의 두 checkout은 재구축·M05의 입력이 아니다(readiness 화면과 비핀 compose 기본값만 쓴다).
- **남긴 것**: 정화된 root git(hook·credential·file/ext 프로토콜 없음, HOME·global/system config 없음, HTTPS만) —
  비-root 사용자의 gitconfig가 `insteadOf`로 URL을 바꿔 root로 코드를 돌리지 못하게 하는 유일한 장치다. DTO
  필드(role·root·revision·tree)는 그대로라 `candidate_git_tree`와 `generation_sha256`은 바뀌지 않는다. M05 본문의
  실행별 checkout(E-2 — PinVi attestation 때문에 archive가 아니다).
- **되돌림 하한**: 조건부로 I-1 그대로다. `<state_root>/pinned-runtime-sources-v5/`와 `.env`의 두 checkout을 그대로
  두면 옛 release는 봉인 트리를 오프라인으로 재검증하고, 없으면 네트워크로 다시 만든다. 새 레이아웃은 옛
  release가 읽지 않으므로 무해하다. v5 디렉터리 삭제는 선택이다(`prod-deployment.md` §8.1).

### NOTE: 잃는 보장 G(실패 출력) G-1 — 스크러버 하나와 출력 경계 (2026-09-27, G-1)

이 NOTE의 "G"는 잃는 보장 G(실패 출력)다 — C NOTE의 전역 락 G와 다르다. G를 세 PR로 나눈다 — G-1 스크러버와
출력 경계, G-2 재구축 경로의 봉인 해체(원문 tail), G-3 M05 채널 하나. 새 원문을 열기 전에 오늘의 누출부터 막는다.

- **스크러버 하나**: `services/secret_scrub.py`. 규칙은 민감한 key 이름의 값(길이 4 이상, `SERVICE_KEY` 추가, SQL
  `''` 변형 포함), 값 안의 URL userinfo 비밀번호, 호출자가 넘기는 추가 값(M05 생성 비밀 — G-3)이다. 원천은 프로세스
  환경과 `.env`다. `.env`가 없으면 프로세스 환경만 가리고, 있는데 읽지 못하면 원문 대신 그 사실만 낸다. 문자열만이
  아니라 dict·list 구조를 모양째 가린다. inspect 응답·UI compose 검증(`docker_service`)과 프런트엔드 사전 검증 목록
  (`configValidation.ts`, 일치를 테스트가 본다)도 같은 조각 목록을 쓴다 — `*_SERVICE_KEY`는 이제 inspect에서 가려지고
  UI에서 리터럴 값 저장이 거부된다(`${VAR:-}` 보간은 그대로 통과).
- **출력 경계**: CLI stderr(재구축 실패 원문, 모든 명령의 마지막 catch-all, targets config 한 줄, compose 명령 결과와
  `action` 결과 전체), API 오류 본문(계약 오류 세 핸들러 — post-mutation의 `restoration` 포함, ensure 500, 컨테이너
  config·reset·action 500), M05 driver(G-3). 원인 문구는 그대로 보이고 비밀만 가린다.
- **오늘 막은 누출**: 재구축 CLI의 `ValueError` 원문, 명령 처리기가 잡지 않은 예외의 Python 기본 traceback, targets
  config 분기의 `OSError` 원문(그 분기가 `OSError`를 잡으므로 처리기의 아무 `OSError`나 가리지 않은 채 찍혔다),
  post-mutation `restoration`과 컨테이너 설정 실패 본문의 compose 출력. 재구축 경로는 JSON 판정도 남긴다. 그 밖의
  잡지 않은 예외는 가린 traceback과 종료 코드 1이다(Python 기본값과 같다).
- **지운 것**: 쓰이지 않던 `_redact_c6c_output`·`redact_config` 경로.
- **남는 누출 부류(기록)**: 규칙에 걸리지 않는 비밀 — `.env`·환경에 없는 값, 목록에 걸리지 않는 key 이름, 변형된
  형태(JSON escape, percent-encoding, base64, compose `$$`), 4자 미만 값. #399부터 같았다.

### NOTE: 잃는 보장 G(실패 출력) G-2 — 재구축 경로의 봉인을 해체하고 원문 tail을 싣는다 (2026-09-27, G-2)

재구축 실패는 이제 원래 예외 그대로 올라가고, 출력 경계(G-1 스크러버)가 한 번 가린다.

- **봉인 해체**: `PinnedRuntimePrejournalFailure`(고정 문구 "candidate preparation failed"), 16개 단계 집합의 검증,
  journal watermark와 두 표식(`prejournal`/`journal reached`), 빌드 fan-out의 서비스 표식을 지웠다. 단계 이름은
  `_rebuild_stage(stage)`가 예외에 속성으로만 붙인다(가장 안쪽 단계가 이긴다, 타입·메시지·traceback 불변) —
  `rebuild_failure_stage(exc)`가 읽는다. 이제 `DeploymentContractError`만이 아니라 OSError 같은 예외도 단계를 얻는다.
- **typed 진단 추출 삭제**: Map Dagster storage migration error JSON의 코드, PinVi admin bootstrap
  `{error_code, phase}` 허용 목록, Compose attach prefix 판독, 중복 key 거부 파서, `allow_typed_error_diagnostic`.
  같은 정보가 원문 tail에 그대로 들어 있다.
- **원문 tail**: `services/errors.py`의 `command_output_tail` 하나(스트림당 끝 16 KiB, 자른 첫 줄은 버린다 — 비밀의
  뒷조각만 남으면 스크러버가 알아보지 못한다). Compose 명령 실패는 `pinned runtime rebuild Compose <args> failed
  (exit N)`에 stderr를, one-shot `run`이면 stdout도 붙인다(다른 명령의 stdout은 데이터라 싣지 않는다). `compose
  config`는 stderr만 — stdout은 비밀이 보간된 설정 문서 자체다. 같은 helper를 buildx(DEVNULL → capture), base
  image pull·inspect, 정적 head 명령, PinVi fence psql, `database_runtime._run_checked*`(stderr만), root git에
  붙였다. root git 실패는 첫 줄을 상수로 두고 명령(경로)을 그 아래에 둔다 — M05 preflight는 그 첫 줄만 낸다.
  호출자가 없던 `alembic heads` 검사는 지웠다.
- **CLI**: 실패 분기가 하나다. `--json`은 `{"status": "failed", "stage": …}`(단계 밖이면 `stage` 없음) —
  `classification`과 `service`는 사라졌다. 원인은 가린 traceback으로 stderr에 간다. `pin` 계열 실패 문구도
  같은 스크러버를 지난다(원문 tail을 실을 수 있다).
- **launcher**: `run-pinned-rebuild-once`의 claim 해제(`<pinset>.prejournal-NN` 개명, 시도 상한 5, `claim-released`
  마커)를 지웠다. claim은 감사 기록일 뿐이고 같은 pinset의 재실행은 다음 ordinal을 받는다. 옛 호스트의
  `.prejournal-NN` 파일은 ordinal 계산에 들어가지 않는 무해한 기록이다.
- **CLI가 예외 문구를 그대로 찍는 자리를 없앴다**(적대 리뷰): 계약 오류(`ComposeCandidateContractError`는
  `ValueError`)가 compose tail을 싣게 되자 `action`·`ensure`의 `print(str(exc))`가 가리지 않은 채 냈다. 모든 예외
  출력과 JSON `detail`이 한 helper(`_scrubbed_message`)를 지나고, 테스트가 원문 출력 자리의 부재를 본다.
  `--json`이 아니면 실패 단계를 한 줄로 낸다. `.env`를 읽지 못한 사용자에게는 root로 다시 돌리라고 말한다.
- **스크러버 원천**(적대 리뷰): 합치지 않고 key·값 쌍을 모은다 — 프로세스 환경, 보간한 `.env`, 보간하지 않은
  `.env` 원문. compose는 프로세스 환경을 앞세우고 재구축은 원문을 넘기므로, 합치면 실제로 쓰인 값이 빠졌다.
- **잃는 것**: result.json의 journal 전후 분류. 그 분류를 읽던 소비자는 이 launcher의 해제 판정 하나였다.
- **남는 누출 부류(기록)**: G-1과 같다. 여기에 더해 one-shot 컨테이너가 자격증명을 변형해(JSON escape 등) stdout에
  찍으면 tail에 남는다. 잘린 조각도 남는다 — psql은 긴 문장을 약 60열로 줄여 `LINE 1: ...`로 되풀이하고, 여러 줄
  값(PEM)은 tail 경계에서 잘릴 수 있다. 16 KiB를 넘는 한 줄은 싣지 않고 그 사실만 적는다. tail은 root 0600 `stderr.log`와 409 API 본문(G-1 경계에서 가림)으로만 나간다.

### NOTE: 잃는 보장 G(실패 출력) G-3 — M05는 항상 캡처하고 채널이 하나다, G 완료 (2026-09-27, G-3)

- **항상 캡처**: `KTDM_M05_FORENSIC_CAPTURE`·`--forensic-capture`/`--no-forensic-capture`·파일별 증거 leaf
  (`failed-*-command.json/.stderr/.stdout`, `*-exception.txt`, compose config 증거) 없음. `_command`는 모든 외부 명령의
  두 스트림을 받아 스트림마다 끝 256 KiB만 남긴다(ring, 잘린 첫 줄은 버린다). 호출자가 받는 stdout은 데이터라
  실패에 싣지 않는다(`compose config`는 크기와 넘침 사실만).
- **채널 하나**: 실패하면 진행 phase·phase·diagnostic·종료값·두 스트림 끝부분·traceback(`__context__` 포함)을 한 텍스트로
  모아 가린 뒤 stderr로 낸다. launcher는 그것을 output leaf의 root 0600 `stderr.log`(driver보다 먼저 만든다)로 받는다.
  잡지 않은 예외(모듈 로드 중 포함)는 가린 excepthook이 낸다. receipt(`result.json`)에는 여전히 phase만 실린다.
- **생성 비밀은 생성 시 등록**: `_random_secret`이 스스로 가림 대상에 오르고, `_write_env_file`이 자식에게 넘기는 env 파일의
  민감 값(파생 해시·compose `$$` 형태 포함)을 올린다. `_RAW_ENV_NAMES`·`_register_forensic_scrub_*` 목록은 열한 개를
  놓쳤다. 가림은 Manager의 스크러버 하나(`scrub_failure_text`)다.
- **닫힌 어휘 삭제**: `_SOURCE_DIAGNOSTIC_PREFIX`·`_PAIR_DIAGNOSTICS`·`_SAFE_DIAGNOSTICS`, fresh-init 진단 runner(Map
  오류 문구를 종료 코드로 바꾸던 entrypoint override)·`_MAP_FRESH_INIT_EXIT_DIAGNOSTICS`, receipt의
  `map_fresh_init_reason`과 launcher의 `DIAGNOSTIC_TOKEN`. Map fresh-init은 자기 entrypoint로 돌고 원문이 tail에 남는다.
  preflight·rotation preflight는 어떤 거부든 가린 첫 줄을 낸다. deploy-status를 읽지 못한 이유, rotation `git show`의
  stderr, HTTP status와 짧은 body도 diagnostic에 실린다(stderr.log에만 가고 receipt에는 가지 않는다).
- **launcher**: `pin verify`·`block-execution`의 stderr를 버리지 않는다. preflight stderr도 통과시킨다.
- **G 완료 — 가린 원문이 나가는 채널**: rebuild leaf와 M05 leaf의 `stderr.log`(root 0600), preflight·rotation preflight 한 줄,
  CLI stderr와 `--json` `detail`, API 오류 본문. 닫힌 채 남는 것: receipt·registry·result.json 스키마(phase 어휘와 key 집합은
  launcher 계약이다), consume-failure marker.
- **스크러버 규칙**: 민감한 key 이름의 값(4자 이상, SQL `''` 변형 포함), URL userinfo 비밀번호, 호출자 추가 값. 원천은 합치지
  않은 key·값 쌍 — 프로세스 환경, 보간한 `.env`, 보간하지 않은 `.env`, (M05) 생성 비밀과 env 파일 값.
- **남는 누출 부류**: 원천에 없는 값, 목록에 걸리지 않는 key 이름의 값, 변형(JSON escape·percent·base64), 잘린 조각(psql
  `LINE 1:` 60열 절단, tail 경계의 여러 줄 값), 4자 미만.
- **적대 리뷰 반영**:
  - fresh-init 진단 override가 없는 서비스에 얹혀 compose가 거부하던 것이 우연한 claim 전 검사였다. 그래서
    `--profile fresh-init config`에 그 서비스가 있는지 claim 전에 명시적으로 본다(`runtime_setup_map_config`).
  - `finally`의 cleanup·terminal block·소비 기록 실패도 실패 텍스트를 낸다. 통과한 본문을 소각하는 자리라서다.
  - 256 KiB를 넘는 한 줄은 그렇다고 적는다. `true`/`false`/숫자 env 값은 가림 대상이 아니다.
  - rotation preflight의 git 실패는 stderr 마지막 줄을 붙인다. 잘린 HTTP body는 토큰 조각을 버린다.
  - Ed25519 개인키 본문도 가림 대상이다. excepthook이 실패하면 원문 대신 고정 한 줄을 낸다.
- **남는 것(기록)**: 자식이 만든 비밀(PinVi `docker-app.sh`의 일회용 migrator 비밀번호, 로그인 세션 JWT)은 어느 원천에도
  없다. preflight 한 줄이 journald로 가므로 저엔트로피 `.env` 값은 `<redacted>` 자리로 드러날 수 있다(가림 오라클).
  driver는 `/opt/kor-travel-docker-manager/.env`만 본다 — 없으면 조용히 프로세스 환경으로만 가린다.
- **다음**: fresh-init 서비스 이름(`db-application-schema-fresh-300` → 핀된 Map의 `db-application-schema-fresh`)과 ADR-100/101
  role 변경은 M05 포팅 PR에서 한다. 그 전까지 핀된 Map으로는 M05가 claim 전에 `runtime_setup_map_config`로 멈춘다.

### NOTE: 결정 5 P-1 — C6c 보호 참조를 설치된 릴리스 compose에서 파생한다 (2026-09-27, D5 P-1)

- **원본**: 설치기가 설치할 때마다 git에서 `docker-compose.yml`을 읽어 release에 읽기 전용 사본 `.ktdm-release-compose.yml`(0444)로
  쓴다(추출 분기 밖 — 같은 revision을 다시 설치하면 빠진 사본이 복구된다). UI는 `docker-compose.yml`을 제자리에서 고치므로 그것은
  원본이 될 수 없다. release(마커가 있는 디렉터리)에 사본이 없으면 거부한다. 개발 checkout에서는 compose 파일 자체가 원본이다.
- **규칙**(`services/compose_references.py`): 자리(서비스 env key, 서비스의 다른 필드, 최상위 항목)마다 후보가 참조하는
  보호 변수는 원본이 같은 자리에서 참조하는 것의 부분집합이어야 한다. 보호 변수는 이름이 민감하거나(`is_sensitive_key`)
  값이 `.env` 비밀을 **담는** 변수다(DSN). `.env` 비밀은 민감한 이름의 값 중 4자 이상이고, 숫자·불리언이 아니며, 원본
  텍스트에 없는 값이다(원본 기본값 `admin`은 비밀이 아니다). secret·config mount는 그 항목의 `environment:` 변수를, 값 없는
  env key(`KEY:`, 목록의 `KEY`)는 같은 이름의 변수를(compose가 환경에서 끌어온다), `env_file`은 언제나 보호된 참조로 센다.
  비밀 값이 글자 그대로 들어 있어도 자리를 밝혀 거부한다.
- **P-1은 병행**이다. raw 검증기의 리터럴 표 검사가 먼저 자기 문구로 거부하고, 파생 규칙은 그 뒤에 돈다. 표가 놓친 것이
  여기서 잡힌다 — 공유 PostgreSQL 비밀(`KOR_TRAVEL_SHARED_POSTGRES_PASSWORD`)과 geo·concierge·weather·transport 비밀은
  어느 표에도 없어서 다른 서비스로 옮겨도 통과했다. P-2에서 표를 지우고 P-3에서 문서를 고친다.

### NOTE: 결정 5 P-2 — 리터럴 보호 참조 표를 지웠다 (2026-09-27, D5 P-2)

- **지운 것**: raw 검증기의 이름·값 스칼라 스캔과 그 허용 경로 표(`_DATABASE_ALLOWED_NON_ENV_PATHS`,
  `_PINVI_DATABASE_URL_ALLOWED_PATHS`), 값 표(`_CANDIDATE_PROTECTED_VALUE_ENV_NAMES`), 이름 합집합에만 쓰이던
  `_DATABASE_SECRET_ENV_NAMES`·`_CURATION_PRINCIPAL_ENV_NAMES`, 13개 서비스 `env_file` 금지 목록, Map·PinVi PostgreSQL
  password secret의 sole-consumer 스캔, secret·config `environment:` 이름 스캔과 그 하드코딩 면제 셋. resolved 검증기의
  스칼라 스캔·배선의 `.env` 재대조(`_CANDIDATE_ALLOWED_API_ENV_SOURCES`·`_CANDIDATE_SOURCE_DEFAULT_VALUES`)·`env_file`
  목록도 지웠다 — resolved 문서는 raw가 원본과 대조한 참조를 보간한 결과라 다시 증명하지 않는다(결정 3).
- **대신**: 파생 규칙이 옛 이름 스캔의 자리(bind·env_file 내용 검사보다 앞)에서 돈다. bind source·`env_file` 내용 스캔은
  남기되 `.env` 비밀 **값**만 찾는다(`secret_values_for`). 파일이 변수 이름을 적는 것은 누출이 아니다 — 스크립트는 자기가
  쓰는 env 이름을 적고, 컨테이너가 그 값을 받는지는 파생 참조 규칙이 본다. 옛 Map role bootstrap 면제
  (`_MAP_ROLE_BOOTSTRAP_SOURCE_TARGETS`)가 규칙이 됐다. 이름까지 찾게 두면 파생 이름 집합이 넓어져 운영 compose의
  `rustfs-init` bind 파일이 거부됐다(n150 미리보기에서 잡았다 — 배포 전부를 막았을 것이다).
- **남긴 것**: 값을 잠그는 것 — `_CANDIDATE_CANONICAL_API_ENV_VALUES`(raw 배선의 정확한 값), 필수 서비스 집합, UI의
  계약 잠금(`_CONTRACT_LOCKED_ENV_NAMES_BY_SERVICE`). 실행 중 재증명(결정 3 범위)과 Map 저장소 compose 검사
  (`_MAP_SOURCE_PROTECTED_ENV_VALUES`)는 이 결정 밖이다.
- **테스트**: 표 등록을 강제하던 테스트와 S4(family 게이트가 소비자 스캔을 건너뛸 수 있다) 가정 테스트 둘을 지웠다 —
  파생 규칙은 진입점의 무조건 호출 하나이고, 진입점 테스트가 그것을 결박한다. sole-consumer 테스트는 같은 문서를
  파생 규칙에 태운다.

### NOTE: G 위의 진입점 하나 추가 — `ktdctl dagster-plane converge` (2026-10-15)

재구축의 공용 Dagster plane 수렴 조각만 같은 G·같은 환경 계약(rehearsal/rebuildable)·같은 compose 호출(`up -d --no-deps
<plane>`)로 따로 도는 명령을 G 위 목록에 더한다. generation·pin·DB·`.env`를 바꾸지 않고 plane daemon·webserver만 다시
만든다 — Manager 설치가 공용 workspace를 바꿔 plane이 unhealthy인데 preflight가 재구축을 막는 경우의 출구다
(`docs/prod-deployment.md` §8.2).

## ADR-52: 공용 PostgreSQL instance는 PID 1에 init을 두고, healthcheck는 exec 형식으로 binary를 직접 부른다

- 상태: accepted
- 날짜: 2026-09-28
- 결정자: 사용자("PR + 즉시 배포" — init: true·exec healthcheck·stop_grace_period·shm_size 승인), Claude
- 관련: ADR-44·45(공용 instance), #426(Dagster 서비스에 같은 처방), `docs/shared-postgres-onboarding.md`

### 컨텍스트

`kor-travel-shared-postgres`가 2026-09-25 11:20, 09-25 21:00, 09-26 04:32, 09-27 08:32,
09-28 03:28(UTC)에 "server process (PID N) exited with exit code 2" → "terminating any other
active server processes" → crash recovery를 겪었다. recovery는 `recovery_init_sync_method=fsync`로
73GB PGDATA를 훑고 end-of-recovery checkpoint까지 가서 매번 3~11분 걸렸고, 그동안 concierge·PinVi·
weather·transport·geo 전 테넌트가 "database system is in recovery mode"를 받았다.

조사로 확인한 것(kor-travel-geo 세션의 읽기 전용 조사, 적대 검증 포함):

- 죽은 다섯 PID는 로그를 한 줄도 남기지 않았고 `DETAIL: Failed process was running`도 없다 —
  **backend가 아니다.** OOM·signal·PANIC·JIT·`/dev/shm` 오류도 그 시각에 없다.
- PG16 postmaster의 `CleanupBackend`(postmaster.c)는 거둔 자식이 0·1 외의 코드로 끝나면
  BackendList를 보기 **전에** `HandleChildCrash`를 부른다. postmaster가 컨테이너 PID 1이면
  (`HostConfig.Init` 없음) docker exec·healthcheck가 남긴 고아가 전부 그것의 자식이 된다.
- healthcheck가 `CMD-SHELL pg_isready ...`(timeout 5초)였다. 이미지의 dash는 명령을 exec하지
  않고 fork하고, `pg_isready`는 Debian의 Perl `pg_wrapper`를 거친다. 호스트 I/O stall(consumer
  SATA SSD 86% 사용, 에이전트 빌드가 겹친 시각)로 probe가 timeout되면 docker는 `sh`만 죽이고,
  고아가 된 `pg_isready`는 서버가 응답하지 않아 exit 2(`PQPING_NO_RESPONSE`)로 끝난다.
- 같은 기계(셸 래퍼 + 거두지 않는 PID 1)가 #426에서 Dagster 서비스에 좀비 565개를 쌓았다.
- 09-25 08:03의 unclean stop은 별개다 — `systemctl restart docker`가 docker 기본 10초 뒤
  postgres를 SIGKILL했다(`stop_grace_period` 없음).
- `shm_size`가 없어 `/dev/shm`이 docker 기본 64MB다. 옛 geo 전용 instance는 512mb였고, 09-22에
  "could not resize shared memory segment"가 한 번 있었다.

고아의 출처(healthcheck냐 사람·에이전트의 `docker exec sh -c psql`이냐)는 PID 수준으로 증명하지
못했다 — docker가 probe timeout kill을 debug 레벨에서만 남긴다. 그래서 두 출처를 **모두** 막는다.

### 결정

`kor-travel-shared-postgres`에:

1. `init: true` — PID 1은 docker-init이고, 어떤 경로로 생긴 고아든 그것이 거둔다. postmaster는 그
   종료 코드를 보지 않는다. image의 `STOPSIGNAL SIGINT`(fast shutdown)는 docker-init이 그대로 전달한다.
2. healthcheck를 exec 형식으로 바꾸고 binary의 절대 경로(`/usr/lib/postgresql/16/bin/pg_isready`)를
   부른다 — 중간 셸도 Perl wrapper도 없으므로 timeout 때 죽는 것은 probe 자신이다. `-t 3`을 명시하고
   docker `timeout`을 10초로 벌려 probe가 먼저 끝나게 한다.
3. `stop_grace_period: 300s` — fast shutdown의 checkpoint를 기다린다.
4. `shm_size: 512mb` — 병렬 질의의 dynamic shared memory 자리.

C6c 서버 형태 계약(`_POSTGRES_ALLOWED_SERVICE_KEYS`)에 `init`·`stop_grace_period`를 더하고, healthcheck
프로그램 검사가 exec 형식에서는 argv[1]만 프로그램 자리로 보게 고쳤다. 예전 검사는 argv 전체를
셸 payload로 읽어 `-h`·`127.0.0.1`을 "비정본 프로그램"으로 거부했다 — 그대로 두면 이 변경이 배포
전체를 막는다. 허용 프로그램 집합(`pg_isready`·`test`·`cat`)은 그대로고, exec 형식의 `sh`·`env`·`psql`도
여전히 거부된다.

### 근거

#426이 Dagster 12개 서비스에 같은 두 처방(exec 형식 + `init: true`)을 적용해 좀비 565 → 40을
실측했다. PostgreSQL에서는 고아가 좀비로 끝나지 않고 클러스터 전체 재시작이 된다는 점만 다르다.
map 전용 instance는 healthcheck가 `test "$(cat /proc/1/comm)" = postgres`로 **postmaster가 PID 1임을
전제**하므로 이 ADR의 범위 밖이다(그쪽은 kor-travel-map 소유의 compose 계약과 함께 바꿔야 한다).

### 결과

- `docker-compose.yml`: 위 네 가지.
- `c6c_deployment.py`: 허용 키 두 개, exec 형식 healthcheck 파서.
- 테스트: `test_shared_postgres_runtime_contract.py`(init·exec probe·`-t` < timeout·grace·shm 결박),
  `test_f1d_compose_contract.py`(exec 형식으로도 임의 프로그램 거부, 인자 있는 exec probe 통과,
  정본 공용 instance 형태 통과).
- 반영에는 컨테이너 재생성이 필요하다 — 모든 테넌트가 짧게(정상 종료 1~2분) 끊긴다. 에이전트 빌드가
  돌지 않는 시각에, 테넌트에 알리고 한다.

### 확인하지 않은 것

- 고아의 정확한 출처(healthcheck vs 수동 exec). 반영 전에 `docker events --filter
  container=kor-travel-shared-postgres --filter event=exec_die`를 걸어 두면 다음 사건의 출처가 남는다.
- 반영 뒤 일주일(에이전트 빌드가 포함된)에 exit-code-2 crash가 없는지.
- `recovery_init_sync_method=syncfs`(recovery 시간 단축)와 `shared_buffers` 재조정은 별개 결정으로 남겼다.

## ADR-53: Map의 두 DB를 공용 instance로 옮긴다 — ADR-35의 Map principal 경계를 이 topology에서 supersede

- 상태: accepted — 효력은 PR마다 다르다. 결정 4(튜닝·백업 예약분)는 MT와
  함께 생긴다. 결정 3의 울타리(R2·R3·R4)는 M1이 만들고, 결정 1·2·5와 결정 3이 공용 instance에 걸리는
  것은 M2(이동, `feat/map-db-shared-instance`)와 함께다 — M2가 코드·compose·문서를 그 모양으로 바꾸고,
  그것이 **사실이 되는 것은 창(runbook §4.4 B5)에서 M2를 설치하고 B9 adopt 재구축이 커밋할 때**다.
  설치 전이나 창에서 되돌려진 뒤에는 1~3·5가 n150에서 아직 사실이 아니다. ADR-35·ADR-37의 supersede
  표시는 M2가 달았다. M2를 설치한 뒤 B6 `.env` 편집 전까지는 Map DSN이 아직 퇴역 instance의 포트라
  C6c가 거부한다(`… (found 0)`) — Map/PinVi 재구축만이 아니라 Manager 프로젝트의 **모든** compose
  mutation(모든 테넌트의 대시보드 설정 변경·reset·없는 컨테이너 시작 포함)이 거부된다. 그래서 B6은 설치와
  같은 단계에서 바로 잇는다.
- 날짜: 2026-09-28
- 결정자: 사용자(오너 결정 C와 하위 결정 D1~D10), Claude
- supersedes: ADR-35 "Map principal 경계 원칙은 유지"(이 topology 한정), ADR-37의 Map 전용 instance 부분
- 관련: ADR-44~47, ADR-50, ADR-51, ADR-52(공용 instance의 init·exec probe·grace), Map ADR-103

### 결정
1. `kor_travel_map`·`kor_travel_map_dagster`는 `kor-travel-shared-postgres`에 산다. 전용
   `kor-travel-map-postgres`는 퇴역하고 PGDATA는 보존한다(롤백용).
2. Map fresh bootstrap은 **그 instance의 기존 admin**(`POSTGRES_USER`)으로, 그 admin secret을 **one-shot
   안에서만 실행 시점에** 읽어 돈다. 새 superuser·새 영속 DSN·Map env 키는 없다. Map 런타임은 받지 않는다.
3. 재구축은 공용 instance를 readiness로만 본다(up·recreate·restart 금지, R3). instance admin 소유 DB, 그리고
   소유자가 Map 두 DB 밖의 DB도 소유한 DB는 파기 대상이 아니다(R2). Dagster metadata role 비밀번호 회전은 그 role이
   아무것도 소유하지 않을 때만 한다. Map DB는 PUBLIC CONNECT를 닫고 앱 DB에 연결 상한 floor(0.4×usable)을 건다(R4).
4. 공용 instance를 Map의 튜닝(D4)으로 한 번 재기동한다: `pg_prewarm`(autoprewarm — 아래) +
   `pg_stat_statements`, `shared_buffers=1GB`, `work_mem=64MB`, `maintenance_work_mem=256MB`,
   `effective_cache_size=1536MB`, `random_page_cost=1.1`, `max_wal_size=2GB`, `shm_size: 1gb`, 현재 이미지 digest
   고정(이미지 교체 아님). `ALTER SYSTEM`은 쓰지 않는다 — 값은 compose `command:`가 정본이다.
   - autoprewarm은 **dump만** 모든 database를 덮는다(유효한 buffer 전부를 DB OID와 함께 적는다). reload는
     보장이 아니다: 파일을 DB OID 순으로 정렬해 database마다 worker 하나를 차례로 띄우고, free buffer가
     바닥나면 다음 worker를 띄우지 않으며 worker 안에서도 거기서 멈춘다(PostgreSQL `REL_16_STABLE`
     `autoprewarm.c`의 `apw_load_buffers`·`autoprewarm_database_main`). 1GB dump는 NBuffers에 가깝고, 그동안
     다시 붙는 테넌트가 free buffer를 쓴다. 그래서 OID가 가장 큰 — 가장 나중에 만든 — DB가 마지막에 올라오고
     아예 못 올라올 수 있다. M2 뒤에는 그것이 prewarm을 요구한 Map의 두 DB다. per-DB 단계는 두지 않는다
     (오너 결정 D4의 범위). 기동마다 `autoprewarm successfully prewarmed N of M` 줄이 실제 적재량이다.
   - 종료 checkpoint의 grace는 D4가 120초 이상을 요구한다. ADR-52의 `stop_grace_period: 300s`가 이미 그것을
     넘으므로 이 ADR은 값을 바꾸지 않는다. `init: true`와 exec probe도 ADR-52의 것이다.
   - Manager의 컨테이너 stop/restart가 그 grace(`Config.StopTimeout`)를 docker에 넘기는 것과, 대시보드의 한
     서비스 재생성(설정 변경·reset·없는 컨테이너 시작과 그 복구)이 `--no-deps`인 것은 이 ADR의 결정이 아니다 —
     compose 정의를 바꾸지 않아 이 결정의 창을 기다릴 이유가 없어 떼어 낸 #434의 것이다(n150에 `6af5dd1`로
     설치됨, `docs/docker-management.md` §7).
   - 백업의 디스크 예약분은 살아있는 `max_wal_size`에서 유도한다: max(2 GiB, `max_wal_size` + 1 GiB).
5. ADR-100 superset 창을 닫는다.

### 구현(M2)
- **instance는 DSN 포트에서 유도한다.** Map은 `KOR_TRAVEL_MAP_PG_DSN`(Dagster URL은 같은 authority),
  PinVi는 `pinvi-api`의 resolved `PINVI_DATABASE_URL`이다. 그 포트를 `-p`로 듣는 PostgreSQL 서버 서비스
  (declared ∪ witnessed, C6c 파서)가 **정확히 하나**여야 한다 — 이름·포트 리터럴이 없다
  (`database_runtime._instance_for_dsn`, C6c `_validate_map_database_dsn_instance`). Map DB 소유자는 그
  instance의 admin(`POSTGRES_USER`)이다(S1). 옛 "PinVi는 다른 컨테이너여야 한다" 검사는 지웠다.
- 재구축은 instance를 `compose ps`로만 본다(running·healthy·컨테이너 이름). `up`·이미지 대조·Map
  PostgreSQL secret 격리 검사와 후보의 `postgres_image_id`는 사라졌다. R3 울타리의 예외 집합(M1까지 Map
  전용 instance 하나)이 비어 **절대**가 됐다 — 재구축은 PostgreSQL 서버 서비스를 이름으로 부르지 않는다.
- **멈추기 전의 S1 판정**(`require_map_bootstrap_admin_ready`): bootstrap이 돌 때(앱 DB 없음·bootstrap
  전·`--restart`) admin이 superuser인지, database 0에 role 0·admin·`ktm_%`의 role setting이 없는지,
  `postgis`·`pg_prewarm`이 있는지, admin password(그 instance의 secret이 가리키는 변수)가 32–256자
  URI-unreserved이고 Map service·metadata password와 다른지, 그리고 그 password가 admin의 **살아있는**
  SCRAM-SHA-256 verifier에 맞는지(socket으로 `pg_authid`를 읽어 프로세스 안에서 비교 — 평상시 그 password로
  TCP 인증하는 것이 없어 회전·편집 drift는 창에서야 드러난다) 읽기만으로 본다. Map 스크립트가 여전히 정본이다.
  Dagster metadata DB가 없으면(`--restart` 제외) init이 거부할 metadata role도 같은 자리에서 거부한다
  (`require_map_dagster_metadata_initializable`) — 공용 instance에서는 다른 tenant의 role도 그 이름의 후보다.
- bootstrap one-shot은 `-e KOR_TRAVEL_MAP_POSTGRES_USER=<admin> -e KTDM_MAP_BOOTSTRAP_PGPORT=<port>`(runtime에서
  유도)를 받고, instance의 admin secret file을 `cat`해 DSN을 **셸 안에서** 만든다(T-SECRET 통과, §1.3(d)
  주 변형). C6c가 entrypoint·네 줄(`cat` 경로 = 자기 secret target, `exec` 경로 = 그 스크립트를 싣는 bind의
  컨테이너 경로)·secret 하나(Map DSN 포트 instance의 admin secret)·mount(`config/docker-targets.yml`의
  `compose_binds` 그 서비스 항목과 원소 단위로 같고 전부 `:ro`)·digest로 고정한 이미지·profile·restart·키 허용
  목록·env 키 허용 목록(계약표 행과 리터럴 스위치 둘)·실행 시점 키 부재를 고정한다. compose가 보간하는
  `$$`를 `docker compose config`도 `$$`로 내므로(n150 Compose v5.2.0 실측) raw·resolved가 같은 네 줄이다.
- R4(M1)는 공용 instance에서 두 가지를 더 한다: Dagster init이 `createdb` 직후 그 DB의 PUBLIC CONNECT를 닫고,
  R4 transaction의 끝(read-back 통과 뒤)에서 두 Map DB에 붙어 있지만 이제 CONNECT가 없는 client 세션을 끝낸다
  (REVOKE는 연결할 때만 검사된다).
- **admin secret 규칙은 이름이 아니라 모든 서버에서 유도한다**(`_assert_instance_admin_secret_holders`):
  서버의 `POSTGRES_PASSWORD_FILE` → `secrets[]` → 최상위 `secrets.<source>.environment`. 그 secret을
  마운트하거나 그 변수(resolved에서는 그 값)를 env에 드는 서비스는 instance 자신이거나, pinned runtime·그
  이미지가 아닌 `restart: "no"` one-shot이어야 한다. admin secret을 유도할 수 없는 서버는 거부한다.
  Map superuser secret을 이름으로 지키던 두 검사(소유자 배선·유일 소비자 스캔)를 대신한다.
- 백업 role `map_application`·`map_dagster`는 공용 컨테이너를 겨냥한다(포트·admin은 살아있는 컨테이너에서).
- Map DSN의 login은 이름으로 고정하지 않는다(M1과 같다) — C6c는 metadata user·instance admin 자리만 막고,
  그것이 Map의 login인지는 R4가 live role 그래프로 본다. D10으로 ADR-100 principal 이름 목록도 C6c에서 빠졌다.

### 받아들인 위험
- 공용 admin은 모든 tenant에 닿는 superuser다. Map의 pinned bootstrap 스크립트가 fresh bootstrap마다
  그 권한으로 돈다. 그동안 admin 비밀번호가 psql 인자로 호스트 프로세스 표에 보인다. n150의 `/proc`은
  `hidepid` 없이 붙어 있어 root만이 아니라 **호스트 PID namespace의 모든 프로세스**가 읽는다 — 권한 없는
  시스템 daemon(systemd-resolve·systemd-network·systemd-oom·_chrony·syslog·polkitd·messagebus·sshd privsep)도
  포함이다. 컨테이너는 자기 PID namespace라 못 보고, cAdvisor는 `comm`만 모은다. 호스트 후속: `/proc`을
  `hidepid=2`(모니터링용 gid 포함)로 붙이는 것과 Map의 `PGPASSFILE`.
- 공용 instance 장애가 Map 장애가 된다(2026-09-25~28 crash-restart 5회 — ADR-52가 원인으로 본 probe 고아를
  init·exec probe로 닫았고, 재기동 뒤 72시간 감시가 그것을 확인한다).
- **반대 방향도 결합이다.** Map의 가장 무거운 부하(PostGIS 3.5.2 on glibc, bulk load, 큰 jsonb 집계)가 모든
  테넌트가 공유하는 postmaster 안에서 돈다. Map backend 하나가 OOM으로 죽거나 segfault하면 postmaster가 **모든
  테넌트**를 crash-restart한다. 컨테이너 메모리 상한은 없다. 72시간 감시는 crash marker를 그것을 낸 database·
  질의로 귀속한다.
- 공용 instance를 `depends_on`하는 서비스가 20개에서 25개로 는다(Map의 API·Dagster webserver·code-server·
  storage-migrate·role bootstrap). 손으로 돌리는 `--no-deps` 없는 `up`/`run`이나 `create` 하나가 공용 서비스의
  정의가 drift된 동안 공용 instance를 재생성하는 길(아래 compose config hash 항목)이 그만큼 넓어진다. 2026-09-29 기준
  Map `scripts/n150`과 Manager 스크립트에는 그런 호출이 없다(grep).
- **앱 DB는 createdb부터 R4까지 PUBLIC CONNECT다.** Map fresh bootstrap이 `datacl IS NULL`을 요구하므로 먼저
  닫을 수 없다. bootstrap·alembic·Dagster init(몇 분) 동안 모든 tenant login이 붙을 수 있다. Map 데이터는 읽을
  수 없고(Map schema는 PUBLIC에서 걷힌다), R4가 CONNECT 없는 세션을 끝낸다. 남는 것은 bootstrap **전**에 붙은
  세션이 자기 이름으로 large object나 `ALTER DEFAULT PRIVILEGES`를 남겨 Map의 fresh 검사가 거부하는 경우다 —
  멈춘 뒤라 재구축이 실패하고 M-R 또는 수동 정리로 간다(fail-closed). Dagster DB는 createdb 직후 닫는다.
- **되돌림은 PinVi 아래에서 Map DB의 정체를 두 번째로 바꾼다.** B9가 커밋한 뒤 PinVi는 공용 instance의 새 Map
  DB를 상대로 돌며 그 DB의 cursor·ack·feature_id를 자기 `pinvi` DB에 남길 수 있다. M-R(V1–V9 실패 뒤 몇 시간)과
  인수 뒤 되돌림(몇 주 뒤)은 Map을 옛 12700 DB(이동 전 sequence·cursor)로 되돌린다. `--adopt-live-databases`는
  identity 게이트를 건너뛰므로 Manager의 어떤 것도 그 전환을 막지 않고, 거꾸로 간 Map cursor를 PinVi가 어떻게
  다룰지는 정해져 있지 않다. 그래서 두 되돌림은 0단계로 P5의 결합 상태 두 질의(Map 쪽은 그때의 live Map DB와
  옛 instance 둘 다, PinVi 쪽은 `pinvi`)를 돌리고, 한 행이라도 나오면 오너 결정(PinVi 계약대로 PinVi의 Map
  결합 상태를 리셋하거나, 되돌리지 않고 앞으로 고치기)으로 멈춘다(runbook §4.5).
- Map의 `ktm_*` role 이름·membership 그래프가 cluster 전역을 점유한다. `ALTER ROLE <admin> SET`·
  `ALTER ROLE ALL SET`이 생기면 다음 Map fresh bootstrap이 거부된다(재구축은 멈추기 전에 거부한다).
- `ktdctl pinvi-pair rebuild-pinned --restart`는 공용 instance에서 Map 두 DB와 PinVi DB를 지운다. launcher만
  그것을 넘기지 않는다.
- R2 울타리는 env 이름을 live 소유 관계로 좁힐 뿐, Manager가 기록한 DB identity에 결박하지 않는다(M1이 적고 M2가
  여기로 옮겼다, `docs/docker-management.md` §7.7): (1) 자기 이름의 DB **하나만** 소유한 다른 tenant login이
  metadata user·Dagster DB 이름으로 일관되게 박히면 배타성 검사를 지나고 `--restart`가 그 DB를 지운다 — 기록된
  identity가 없는 배포의 R4도 그 DB의 PUBLIC CONNECT를 걷는다. (2) PinVi drop 소유자에는 배타성을 걸지 않는다.
  2026-09-28 공용 instance에는 (1)의 모양인 login이 없다.
- Map DSN login은 이름으로 고정하지 않는다(M1과 같다). D10으로 C6c의 ADR-100 principal 이름 목록이 빠져, 그
  이름(예: `ktm_feature_runtime`)을 login으로 박은 `.env`는 C6c가 아니라 R4의 live 판정(멈추기 전 preflight, 앱 DB가
  이미 있을 때)과 Map bootstrap의 preflight(앱 DB가 없을 때 — 멈춘 뒤)가 거부한다.
- Map ops/audit 행과 Dagster 이력, 그리고 비-ops 행 일부(`feature_state_transitions` 25 등)와 설정성 행
  (`curated_*`·`provider_sync`, loader가 다시 쓸 때까지)을 잃는다(감사 dump·옛 PGDATA 보존).
  `pinvi`·`template_postgis`의 PUBLIC CONNECT는 남는다(후속).
- 튜닝은 cluster 전역이다: 64MB `work_mem`의 무거운 꼬리는 page cache를 밀어내고 swap에 닿을 수 있다(실측
  부하에서는 수용 가능, 72시간 감시 항목). dockerd 자체가 멈출 때는 systemd `TimeoutStopSec`(n150 90초)가
  300초 grace보다 먼저 끝난다(호스트 후속).
- 이 결정의 compose 변경을 설치하는 순간 공용 서비스의 compose config hash가 실행 중 컨테이너와 달라진다.
  그 사이에 공용 instance를 `depends_on`하는 **어떤** 서비스(db-init one-shot 포함)든 `--no-deps` 없는
  `up`·`run`(`run --rm` 포함)이나 `create`(그 플래그가 없다)가 하나라도 돌면 compose가 공용 instance를
  재생성한다(n150 Compose v5.2.0 실측). Manager의 한 서비스 재생성은 #434 뒤로 `--no-deps`라 의존 서비스를
  거쳐서는 이 길이 아니다. 그러나 `--no-deps`는 **공용 서비스 자신**을 재생성하는 것은 막지 않는다 — 공용
  컨테이너(`kor-travel-shared-postgresql`)의 대시보드·CLI 설정 변경과 reset은 그것을 설치된 새 정의로 다시
  만든다. 창의 게이트(Q-RUNS·HH:25–40·`CHECKPOINT`·로그 보존) 밖에서 모든 테넌트가 계획에 없이 재기동된다.
  사람·에이전트의 명령과 db-init 재실행(onboarding §7.3이 자가치유로 권하는 것)도 코드가 막지 못한다. 그래서
  설치 직후 계획된 재기동까지 한 창에서 가고, 창의 공지가 drift guard가 MATCH를 찍을 때까지 그것들 — 공용
  컨테이너 자신의 설정 변경·reset 포함 — 을 동결한다(되돌림 T-R에서 옛 정의를 설치한 뒤에도 같다). 재생성이
  아닌 stop/restart(`control_container`)는 동결하지 않는다.
- **지금 도는 #433 상태 대비** 이 튜닝은 dynamic shared memory(DSM)의 여유를 **줄인다 — 없애지 않는다.**
  병렬 hash 한 노드의 DSM 상한은 `work_mem × hash_mem_multiplier(2) × (max_parallel_workers_per_gather(2) + 1)`로
  96MB에서 384MB로 네 배가 되고, `/dev/shm`은 512mb에서 1gb로 두 배다. 동시에 최대로 자란 병렬 hash를 담는
  여유는 약 5.3개에서 약 2.7개로 절반이 된다. 여러 병렬 hash 노드를 가진 질의 하나가 1GB 가까이 쓸 수 있다.
  모자라면 마지막으로 할당한 질의 하나가 ENOSPC(`posix_fallocate`) 오류로 끝난다 — crash는 아니다.
  autoprewarm leader는 `max_worker_processes`(8, 기본값 실측) 한 칸을 상주로 쓴다. 그래서 병렬 worker로 쓸
  칸은 7에서 6이 된다. #433 재생성 뒤 약 18시간(512mb·16MB) DSM 오류는 0건이었다(2026-09-29 리뷰 실측, 그
  전 64MB·16MB에서는 18건). **72시간 감시에서 새 DSM 오류는 hard alarm이다.** 미리 합의한 후속은
  `shm_size: 2gb`다. 오늘의 비율을 되찾고, tmpfs라 segment가 있는 동안만 RAM을 쓴다. 공지한 창에서
  재생성한다. D4 자체는 다시 열지 않는다.
- `max_wal_size=2GB`는 두 비용을 키운다. 2026-09-29 리뷰 실측으로 #433 재생성 뒤 checkpoint 225번 중 57번이
  1GB의 WAL로 시작됐고, 최근 timed checkpoint 거리는 450–525MB였다. (1) WAL이 많은 부하에서는 crash
  recovery의 replay가 약 두 배까지 길어질 수 있다. 공용 instance의 09-25~28 crash-restart 다섯 번은 각각
  1–7분이었다. (2) 종료 때 내려야 할 dirty set의 상한이 128MB에서 1GB로 커진다. 그런데 dockerd의
  `TimeoutStopSec`는 90초 그대로다. 그래서 daemon이 거는 정지(재부팅·패키지 재시작)가 공용 instance를
  SIGKILL하고 crash recovery로 이어질 가능성이 커진다. docker.service `TimeoutStopSec=300` drop-in(호스트
  후속, 오너 결정)은 이 튜닝 **뒤가 아니라 함께** 하기를 권한다.

## ADR-54: 공용 Dagster 제어 평면 3단계 — 공용 daemon·webserver·gateway와 target별 합류 스위치

- 상태: accepted — 효력은 정의까지다. 모든 target이 `own`이라 plane은 빈 workspace로 돌고 어느 프로젝트도
  합류하지 않았다. 합류는 프로젝트마다 별도 PR·창이다(platform-topology.md §7 전환 runbook).
- 날짜: 2026-09-30
- 결정자: 사용자(D2 — gateway `11001`·webserver `127.0.0.1:11002`·daemon 포트 없음, D3 전역 12, D4 코드 선언,
  D6 관측 — 2026-09-30 개정, transport는 나중에 필드 하나와 파생 항목 하나로 합류), Claude
- 관련: platform-topology.md §7(2~4단계, G3-a·G3-b), ADR-20(단일 compose 파일 경계), ADR-50(과결박 없이),
  ADR-52(init·exec probe), 2단계(#443: `dagster_shared`·호스트 이미지·공용 `dagster.yaml`)

### 컨텍스트

2단계가 `dagster_shared`와 그 schema, Manager 소유 호스트 이미지(`kor-travel-dagster-host:<내용 해시>`),
공용 `dagster.yaml`(location별 상한은 `dagster/code_location`)을 세웠다. 아무것도 그것을 쓰지 않았다. 네
프로젝트(Map·PinVi·geo·weather)는 각자 webserver·daemon·메타DB를 쓰고, 소비자(API·UI)는 그 webserver를 부른다.
공용 plane으로 옮기려면 (1) 공용 daemon·webserver와 인증된 입구가 있어야 하고, (2) 공용 workspace가 합류한
code-server를 가리켜야 하며, (3) 프로젝트 하나를 옮길 때 code-server·소비자·옛 webserver/daemon이 함께
바뀌어야 한다 — 한 곳이라도 빠지면 두 daemon이 같은 schedule을 쏘거나 소비자가 옛 instance를 본다.

### 결정

1. **호스트 서비스.** `kor-travel-dagster-daemon`·`kor-travel-dagster-webserver`는 storage migrate와 같은 호스트
   이미지(tag·build context 동일), 비-root(`USER dagster`, uid 10001, `/opt/dagster/state` 쓰기 가능),
   `init: true`, `restart: unless-stopped`, migrate에 `service_completed_successfully`로 기댄다. env는 공용 URL
   앵커와 `DAGSTER_HOME`(daemon은 `DAGSTER_DAEMON_HEARTBEAT_TOLERANCE=300`)뿐 — run은 code-server의 자식이라
   앱 비밀이 필요 없다. daemon은 PinVi와 같은 storage 가드(비-Postgres instance면 기동 거부) 뒤 `dagster-daemon
   run`, healthcheck는 exec 형식 `dagster-daemon liveness-check`. webserver는 `127.0.0.1:11002`에서만 듣는다.
2. **모든 location을 보는 probe.** 프로젝트별 webserver의 `repositoriesOrError`는 location 하나만 떠도 초록이라
   여러 테넌트의 webserver에서는 한 테넌트의 실패를 가린다. 공용 webserver probe는 붙은 workspace 파일에서
   기대 location을 읽고 `workspaceOrError.locationEntries[*].locationOrLoadError.__typename`이 전부
   `RepositoryLocation`일 때만 초록이다. exec 형식, dagster를 import하지 않고(yaml·urllib), 자체 timeout(8초)이
   healthcheck timeout(10초)보다 짧고, gRPC를 부르지 않는다(webserver가 이미 가진 location 상태를 읽는다).
3. **gateway(`11001`).** weather·transport gateway의 형태 — nginx Basic Auth, same-origin이 아닌 브라우저 POST
   403(Origin 없는 POST는 아래 개정), `/health`만 무인증 204, `frame-ancestors 'self'`+iframe을 여는 프로젝트 UI(기본
   `KTDM_PROD_URL_GEO`, `KOR_TRAVEL_DAGSTER_FRAME_ANCESTORS`로 덮는다). 공식 nginx를 digest로 고정하고
   uid 101로 돌린다. 그래서 nginx 설정 전체를 Manager가 소유한다(`config/dagster-shared/gateway.conf`, pid·temp를
   `/tmp/nginx`로). 기동 스크립트는 fail-closed다 — 비밀번호가 비었거나(값이 비면 compose는 secret 파일을
   만들지 않는다, n150 Compose v5.2.0 실측), 사용자 이름·origin 값이 설정을 깰 모양이면 nginx를 띄우지 않는다.
   비밀번호는 secret 파일로 받고(`docker inspect`에 없다) htpasswd는 컨테이너 `/tmp`의 `{PLAIN}` 한 줄이다(원문이
   이미 그 컨테이너의 secret 파일에 있어 컨테이너 안 해시는 더 지키는 것이 없다). 비밀번호 env는 `:?`로
   요구하지 않는다 — 그러면 그 줄이 없는 `.env`에서 모든 compose 명령이 죽는다. Dagster webserver 자신의 CSP는
   지우지 않고 frame-ancestors를 두 번째 정책으로 더한다(브라우저는 둘 다 적용한다).
4. **합류 스위치와 파생 workspace.** target마다 `dagster: {control_plane: own|shared, consumers: {서비스: {env:
   internal|public[/경로]}}}`(registry가 형태를 fail-close로 검증한다). `config/dagster-shared/workspace.yaml`(2026-10-15 #481부터 호스트별 `workspace.<host>.yaml`, 옛 별칭은 2026-10-17 NH-4로 제거)은
   `shared` target의 code-server command에서 **파생**한다 — host `127.0.0.1`, port는 `-p`의 compose 기본값,
   `location_name`은 `--location-name`이 있으면 그것, 없으면 `-m`(상한 값과 같은 이름). 소비자 env만 선언인 이유:
   PinVi·geo API는 앱 코드의 기본값(`localhost:12802`, `127.0.0.1:12502`)에 기대어 compose에 그 env가 없어,
   compose만으로는 찾을 수 없다. 나머지(code-server, 옛 webserver·daemon, 그것에 기대는 gateway, 공용
   webserver·gateway, 공개 host env)는 모양에서 찾는다.
5. **렌더러를 두지 않는다 — compose가 렌더된 모양이고 테스트가 스위치에서 파생한 기대와 대조한다.** compose는
   단일 정본 파일이고(ADR-20: override·`include`·`COMPOSE_FILE` 거부) 주석이 계약의 일부다. 오버레이는 그 경계를
   깨고, 파일을 기계로 다시 쓰면 주석을 잃는다(주석 보존 YAML 라이브러리도 없다). 전환은 네 번뿐이고 각각
   검토되는 PR이다. 그래서 전환 PR이 compose를 손으로 바꾸고 `test_dagster_shared_workspace_is_derived.py`가
   빠진 단계를 이름으로 말한다: (a) code-server가 앵커와 공용 `dagster.yaml` 마운트를 받고(그 자리의 옛 마운트
   없이) gRPC가 `127.0.0.1`, (b) 선언한 소비자 env가 공용 webserver·공개 host로 풀리고 어떤 활성 서비스의
   env·build arg도 옛 webserver·gateway 포트를 부르지 않는다, (c) 옛 webserver·daemon·gateway가 `profiles:
   [legacy-dagster]`이고 어느 target의 `services`·`runtime_services`에도 없으며(`containers`에는 남아 되돌리기에
   쓴다) 활성 서비스가 그것에 기대지 않는다, 그리고 합류한 target이 있으면 plane target이 `all`에 있다. `own`
   target은 그 어느 것도 갖지 않는다. 같은 파일의 참조 구현 `_flip`이 모델 위에서 그 편집을 하고, 네 target
   모두에서 계약·workspace 파생·G3-b가 초록임을 보인다.
6. **빈 plane.** 모든 target이 `own`이면 workspace는 `load_from: []`이고 daemon·webserver는 healthy다(daemon은
   heartbeat만, probe는 기대 location 0개). plane target은 첫 합류 전까지 `all`에서 빠진다 — 그 전의 plane은 할
   일이 없고, 비밀번호 전에는 gateway가 기동을 거부하기 때문이다. 첫 합류 PR이 `all`에 넣는다(테스트가 요구한다).
7. **게이트.** G3-a — 첫 전환 전에 role `kor_travel_dagster_shared_app`의 연결 수를 전역 run 상한 12가 찬
   상태에서 잰다(`CONNECTION LIMIT`, dagster-postgres는 NullPool). 모자라면 db-init 두 문장과 테스트 기대를
   함께 올린다. **2026-10-01 Map 전환 전 30 → 45**: weather·PinVi·geo 세 테넌트 1초 표본 745개(15분) 최대 6(평균 2.1),
   같은 시각 Map 전용 instance의 role 최대 5 — Map 합류 뒤 추정 약 11; 전역 run 12가 찬 최악(run worker마다 연결 하나
   이상 + daemon·webserver 스레드)은 30에 다가간다. 공용 instance `max_connections` 100에 지금 최대 43이 붙으므로 여유는
   약 57 — 45는 그 안이다. 창 스크립트가 Map forward의 펜스 전에 db-init을 다시 돌려(멱등 ALTER) live role에 적용하고
   `rolconnlimit`을 확인한다. D3(전역 12, Map 10)는 바꾸지 않았다(굶주림 증거 없음). G3-b — workspace location마다 `dagster/code_location` 상한이 있고 `shared` target의 location은
   모두 workspace에 있다(테스트).
8. **전환과 되돌리기**는 platform-topology.md §7 runbook이다 — drain, fence(옛 daemon 뒤 webserver 정지,
   `fence_ts`), switch(스위치를 바꾼 release 설치 → `ensure dagster` → `ensure P`), verify(probe·instigator
   동등·옛 DB tick 0·슬롯당 tick 하나·첫 run SUCCESS·전환 판정 SQL·소비자 격리). **D6 개정(2026-09-30, "관찰 기간을
   대폭 줄이고 모두 마이그레이션 후 함께 관측")**: 전환 사이에 soak이 없다 — 네 프로젝트를 잇달아 옮기고,
   넷이 모두 오른 뒤 약 24시간(일일 주기 하나, Map C7 prod gate GREEN 포함)을 함께 관측한 다음 4단계로 간다.
   옛 메타DB `DROP` 전 30일은 그대로다. 되돌리기는
   **공용 workspace에서 P를 먼저 내리고(`ensure dagster`) 옛 daemon을 띄운다(`ensure P`)** — 반대면 두 daemon이 쏜다.

9. **적대 리뷰(2026-09-30)로 더한 것.**
   - **내용 digest(H1).** bind source가 설치본 symlink(`/opt/kor-travel-docker-manager` → release)를 거친 경로라
     compose config hash는 경로 문자열만 본다. 전환·되돌리기로 workspace 내용만 바뀌면 daemon·webserver가
     재생성되지 않아 옛 workspace를 계속 읽는다 — 전진 전환은 P의 schedule을 조용히 멈추고(probe도 같은 옛 파일을
     읽어 초록), 되돌리기는 이중 발화한다. 그래서 공용 설정 파일을 붙인 상시 서비스는 그 파일 sha256 앞 16자를 env로
     싣는다: daemon·webserver는 `KOR_TRAVEL_DAGSTER_WORKSPACE_DIGEST`·`KOR_TRAVEL_DAGSTER_INSTANCE_DIGEST`, gateway는
     `DAGSTER_GATEWAY_CONF_DIGEST`, 합류한 code-server는 `KOR_TRAVEL_DAGSTER_INSTANCE_DIGEST`. 파생 테스트가 내용과
     대조한다. `ensure dagster --recreate`는 답이 아니다(target에 공용 PostgreSQL이 있다).
   - **gateway의 `/graphql`(H2·M1).** dagster-webserver는 Accept에 text/html이 없는 GET의 `query` 인자도
     실행한다(mutation 포함) — 교차 사이트 `<img>`가 캐시된 Basic Auth로 부를 수 있다. POST가 아닌 `/graphql`은
     **인자를 하나도** 받지 않고(`query`만 막으면 `?%71uery=…`·`?query=&query=…`가 지나간다 — nginx는 디코드 전 이름의
     첫 값을, Starlette는 디코드한 이름의 마지막 값을 본다, 재리뷰 MED-1), 브라우저의 `Sec-Fetch-Site`가 `same-origin`·`none`(또는 헤더 없음 — 비-브라우저)일 때만 받는다.
     `Upgrade` 요청(GraphQL subscription WebSocket — 모든 테넌트의 run·compute log)도 허용 Origin이 아니면 403이다.
     POST의 인자는 막지 않는다 — UI의 Apollo HTTP link가 모든 요청에 `?op=<OperationName>`을 붙이고
     (`createOperationQueryStringApolloLink`, 1.13.24 번들 확인; 처음 판은 인자 전부를 막아 UI를 깨뜨렸다, 재리뷰
     HIGH-1), POST는 이미 Origin·`Sec-Fetch-Site`로 갈리고 본문이 요청을 정한다. UI의 subscription WebSocket은 인자
     없는 `<origin>/graphql`이라 통과한다. UI HTML(`/`)은 iframe으로 열리므로 이 검사 밖이다. `limit_req`는 두지 않았다 — HAProxy 뒤에서는 모든 요청이 한
     주소로 오므로 Basic Auth 시도만이 아니라 UI 전체를 함께 조인다.
   - **daemon probe(M4).** workspace의 code-server 전부가 gRPC `SERVING`인지 본 뒤 **같은 프로세스를 exec**해
     `dagster-daemon liveness-check`로 넘어간다(두 번째 프로세스·셸 없음). daemon은 매 반복 workspace를 다시 읽으므로
     떠 있는 code-server는 로드된다. daemon 자신의 location 로드 상태는 노출되지 않으므로 runbook의 tick 검증(슬롯당
     하나)이 그 효과를 본다.
   - **버전 상한(M5, 계획 0.6).** webserver probe가 각 location의 `dagsterLibraryVersions`를 호스트 이미지에 설치된 같은
     배포판(`importlib.metadata`, dagster import 없음)과 비교해 더 높거나 모르면 빨갛다. runbook 4번이 그것에 걸린다.
   - **pinned 재구축(M2, 재리뷰 MED-2).** pinned 재구축(`RUNTIME_SERVICES`·`COMPOSE_BUILT_RUNTIME_SERVICES`), C6c 보호
     집합, 명시적 `compose_up("<서비스>")`, M05 하네스·이미지 보존, `legacy_override_retirement`가 옛 webserver·daemon을
     literal로 든다. frozen render는 `--profile bootstrap`만 켜므로 옛 서비스가 `legacy-dagster`로 가면 재구축에서
     사라지고, 명시적 `up <서비스>`는 꺼진 profile도 띄워 옛 daemon이 되살아난다. 그 참조를 스위치에서 파생하는 것은
     pinned 재구축·M05·C6c 계약 전체에 닿는 변경이라 이 PR에 넣지 않았다. 대신 계약 테스트가 옮기는 target의 옛
     서비스 이름(compose에서 파생)을 `backend/src`·`scripts` 전체에서 온전한 토큰으로 찾고, 하나라도 있으면 그 target의
     `shared`를 `(pinned)`로 막는다 — 목록 몇 개가 아니라 코드 전체라, 부분 파생이 게이트를 초록으로 만들지 못한다.
     2026-09-30 기준 Map·PinVi·geo(`legacy_override_retirement._GEO_SERVICES`)가 걸리고 weather만 열려 있다.
     **해제(같은 날, 후속 PR):** 그 참조는 전부 `services/runtime_topology.py`가 렌더된 모델(설치된 release의 reference
     compose·targets)과 스위치에서 파생한다 — `shared`면 옛 서비스는 어떤 실행 집합에도 없고 carrier인 code-server가
     slot을 잇는다. 모두 `own`이면 이름·집합·tag·호출 순서가 글자까지 같다. 게이트는 되돌아옴을 막는 문으로 남는다.
   - **포트는 literal.** `shared` code-server의 `-p`는 literal이어야 한다 — workspace는 정적 파일이라 `.env`의 포트
     override를 모른다. 계약 테스트가 요구하고 참조 전환이 literal로 바꾼다.
   - **옛 공개 host(b).** 활성 서비스가 옮긴 target의 옛 공개 host env(그 target 옛 서비스 컨테이너의 `prod_url_env`,
     예: `KTDM_PROD_URL_MAP_DAGSTER`)를 더 부르지 않는다.
   - **재리뷰 LOW.** 두 probe는 붙인 `workspace.yaml`·`dagster.yaml`의 sha256을 자기 digest env와 비교해, 호스트에서
     파일만 고쳐 컨테이너가 옛 내용을 들고 있으면 빨갛다(재생성하라는 뜻). 파생 테스트는 `config/dagster-shared/`의
     파일을 모르는 모양(디렉터리·절대 경로·긴 형식)으로 붙이면 건너뛰지 않고 빨갛다. 버전 상한은 location마다
     `dagster` 자신이 보고·비교됐어야 초록이다(보고하지 않거나 호스트 dagster 버전을 모르면 빨강).
   - **webserver도 storage 가드.** daemon과 같은 가드(YAML anchor 하나)를 지나 argv로 넘어간다(`sh -ec <가드> sh <argv>`).
   - **개정(2026-10-01, Map 전환 준비): 비-브라우저 API POST.** `Origin`과 `Sec-Fetch-Site`가 **둘 다 없는** POST는
     Basic Auth만으로 통과한다. 브라우저는 POST에 항상 Origin을 싣고(fetch·XHR·form), Fetch Metadata도 싣는다 — 둘 다
     없으면 브라우저가 아니므로 캐시된 자격증명을 빌려 쓰는 CSRF가 아니다. 그런 호출자는 스스로 Basic Auth를 실어야
     하고(없거나 틀리면 401), 교차 사이트·`null` Origin이나 Origin 없이 `Sec-Fetch-Site`만 실은 POST는 여전히 403이다.
     이유: C7 게이트의 queue sensor 조작은 bridge network의 Playwright 컨테이너에서 돌아 loopback webserver에 닿지
     못하고, 공개 host(이 gateway)로 GraphQL mutation을 보낸다. 서버 쪽 소비자(Map·PinVi·geo API)는 계속 loopback에
     붙는다. 격리 plane 통합 테스트가 no-Origin+인증 200, 인증 없음·틀린 비밀번호 401, 교차·`null` Origin 403,
     `Sec-Fetch-Site`만 있는 no-Origin 403을 본다.
   - **개정(같은 날): plane을 아는 pinned 재구축.** smoke가 공용 webserver에 자기 location을 묻는다(PinVi
     `/admin/etl/summary`, Map `/v1/ops/pipeline/*`). 그래서 Map·PinVi 중 `shared`인 target이 있으면 재구축이 PinVi
     smoke **전에** 그 carrier(code-server)를 띄우고 plane이 그 location을 싣게 한다(적대 리뷰 H1·M1·M3·L4 반영).
     - plane 서비스는 **frozen render에서** 모양(공용 workspace를 붙인 활성 `dagster-webserver`·`dagster-daemon`)으로
       파생하고, 설치된 release의 것과 다르면 거부한다. 붙인 workspace 파일에서 실을 location을 읽는다.
     - **한 규칙의 이중 발화 가드(M1).** 그 workspace의 location마다 그 location을 싣는 target(설치된 모델에서 파생,
       모든 Dagster target)의 plane 밖 webserver·daemon·gateway가 돌면 거부한다 — Map·PinVi만이 아니라 설치됐지만
       아직 펜스 전인 다른 target(앞으로의 transport)도. 어느 target도 싣지 않는 location이 있으면 거부한다.
     - **다시 만들 때만 `up -d --no-deps`(M3).** 떠 있는 daemon·webserver가 frozen render가 만들 컨테이너와 같으면
       `up`하지 않는다 — 이미지(render의 `image:` 참조의 image ID = 컨테이너 `.Image`, 곧 Dagster 올림), render의 env
       전부(공용 URL 앵커, heartbeat tolerance, `*_DIGEST`), command·entrypoint가 같고, 돌며 재시작 중이 아니어야 한다.
       config hash는 쓰지 않는다: frozen render(`--env-file /dev/null`, stdin compose)와 평범한 render가 다르게 내서
       무조건 `up`은 매 재구축 plane을 다시 만들고 그동안 모든 테넌트의 webserver가 잠깐 끊긴다. (처음 판은 `*_DIGEST`만
       봐서 이미지·앵커·tolerance·command가 바뀐 release에서 낡은 plane이 살아남았다 — 재리뷰 MED.) daemon 확인은
       돌고 재시작 중이 아님까지다 — daemon health는 모든 code-server를 봐서 다른 테넌트에 묶인다.
     - **이 target만 기다린다(H1).** `--wait`를 쓰지 않는다 — plane의 healthcheck는 workspace의 location 전부와
       code-server 전부를 봐서, geo·weather의 code-server 하나가 내려가면 Map 배포가 실패하고 smoke 뒤에 뜨는
       pinvi-web까지 내려간 채로 남았다. 대신 webserver의 `workspaceOrError`에서 합류한 pinned location이
       `RepositoryLocation`이고 daemon 컨테이너가 도는지를 300초 상한 안에서 본다(넘으면 거부).
     - **반대 방향(M2).** `own`인 Map·PinVi의 location을 설치본 workspace가 적었거나 떠 있는 webserver가 싣고
       있으면, 무엇을 멈추기 전에 거부한다(own daemon이 뜨면 같은 schedule을 둘이 쏜다). webserver에 물을 수 없는데
       plane daemon이 돌면 판정할 수 없어 거부한다. 되돌리기는 창 스크립트
       (`scripts/dagster-shared-cutover.sh <target> rollback <sha>`)로 한다 — plane에서 먼저 내린다.
     - 모두 `own`이면 plane을 건드리지 않고 docker 호출도 더하지 않는다(파생 이전 지문과 같다). M2의 물음은 plane
       webserver에 대한 읽기 하나다.
   - **개정(2026-10-02, transport 합류): 형제 프로젝트(`external_project`)도 합류한다 — Manager로 옮겨 오지 않고(M-T
     없이).** compose가 그 저장소에 있으므로 Manager는 모양을 파생할 수 없다. 그래서 그 target의 `dagster.external`이
     모양을 **선언**한다(code-server·옛 webserver·daemon·gateway의 compose 서비스 이름, `--location-name`, literal `-p`).
     - 공용 workspace의 항목과 `dagster/code_location` 상한(transport 3, `kortraveltransport/run_group` 넷 각 1 — 옛
       instance 그대로, D3)은 그 선언에서 나온다. location 이름은 옛 workspace의 `kor-travel-transport` 그대로라 selector
       id가 두 instance에서 같다(전환 스크립트의 tick 범위 한정이 성립한다).
     - 선언과 실물의 일치는 **전환 때 효과로** 본다. 창 스크립트가 실행 중 code-server의 compose label(project·
       working_dir·config_files)로 그 프로젝트를 `--profile legacy-dagster`까지 렌더하고 같은 규칙으로 파생해 선언과
       대조한다. 어긋나거나 gRPC가 loopback이 아니거나 서비스가 `container_name`을 두면 펜스 전에 멈춘다. env 파일은
       label이 말하지 못해(배포 스크립트의 임시 파일) `EXTERNAL_ENV_FILE`로 받는다. code-server는 `--no-build`로
       다시 만들고 그 이미지가 호스트에 있어야 한다.
     - 이중 발화 가드(M1)의 소유자에 형제 프로젝트가 든다. 옛 서비스의 컨테이너는 compose 기본 이름
       (`<project>-<service>-1`)이다. 선언이 빠지면 그 location은 소유자가 없어 Map·PinVi 재구축이 거부한다(fail-closed).
     - 소비자 env는 그 저장소가 소유한다(`consumers` 금지). transport의 소비자(운영 UI, 다른 compose project
       `kor-travel-transport-admin`)는 이름 붙은 query만 자기 location으로 좁혀 보내고(`lib/dagster-scope.ts`), 전환
       **뒤에** 배포한다 — 그 전의 UI는 옛 webserver를 불러 다른 테넌트를 보지 못한다.
     - 형제 code-server도 공용 plane code-server 규칙(#456)을 지킨다 — `code-server start`, Manager 공용 probe 원문
       (`x-dagster-code-server-probe`)을 exec 형식으로, 같은 proxy heartbeat, `init: true`. 창 스크립트가 렌더끼리
       대조한다(저장소 사이에 SHA를 박지 않는다 — 효과로 본다).
     - transport의 **되돌리기는 지원하지 않는다**(소유자 결정 — 보장 없는 수동 best-effort). 창 스크립트의 `rollback`
       모드는 이 형제 프로젝트에 대해 검증하지 않았다(이전 release의 code-server는 `--location-name`이 없어 선언과의
       대조에서 멈춘다).
     - transport의 code-server는 `dagster code-server start`다(reload가 정의를 다시 읽는다 — Manager 쪽 전환은
       `fix/dagster-code-server-reloadable`). 창 스크립트의 파생은 `api grpc`와 `code-server start`를 모두 code-server로 본다.

### 근거

- 모든 target이 `own`인 동안 기존 서비스의 정의는 한 글자도 바뀌지 않는다. n150 실측(읽기 전용, live `.env`,
  sudo): 설치본 `08824b0`과 이 브랜치의 `docker compose config --hash '*'`가 기존 41개 서비스 전부 같고 새
  것은 셋(daemon·webserver·gateway)뿐이다. 설치가 어떤 컨테이너의 재생성도 걸어 두지 않는다.
- transport는 Manager 안으로 옮겨 온 뒤(M-T) `dagster` 필드 하나와 workspace의 파생 항목 하나로 합류한다 —
  location 이름이 custom이어도 `--location-name`을 읽는다. 상한 두 줄은 공용 `dagster.yaml`에 더한다(2단계).
  (2026-10-02 개정: M-T를 기다리지 않고 형제 프로젝트 그대로 합류했다 — 위 개정, `dagster.external` 선언.)

### 결과

- 전환 PR의 할 일이 테스트로 닫혀 있다. 빠뜨린 단계는 이름으로 빨갛다(참조 구현에서 한 단계씩 뺀 대조군).
- 공용 plane의 입구는 인증된 gateway 하나다. webserver는 loopback이고 서버 쪽 소비자는 그 loopback에 직접 붙는다.
- 한 테넌트의 location이 안 뜨면 공용 webserver가 unhealthy가 된다 — 그것이 의도다(가리지 않는다). gateway는
  webserver에 `service_started`로만 기대어 그때도 UI를 연다.

### 받아들인 위험 · 남은 것

- **`127.0.0.1:11002`는 인증이 없다(D2 + host network의 본성).** host network의 컨테이너는 전부 공용 webserver에
  인증 없이 닿는다 — 공용 plane의 모든 테넌트의 run·schedule을 읽고 mutation을 보낼 수 있다. 호스트 컨테이너를
  신뢰 경계 안으로 두는 오늘의 모델(Docker 그룹은 root 동급, ADR-20)과 같은 부류라 받아들였다. 대신 `internal`을
  가리키는 소비자는 브라우저의 GraphQL 원문을 넘기지 않아야 한다(이름 붙은 operation만 자기 location scope로 —
  weather는 PR #65의 `scopedDagsterRequest`가 그렇게 한다, 2026-09-30 확인). Manager가 그것을 코드로 알 수는 없어
  runbook 4번이 각 소비자가 **할 수 있는 것**을 직접 시도해 본다.

- Dagster OSS에는 RBAC가 없다. 합류한 code-server는 모두 공용 metadata 자격증명을 가지므로 다른 테넌트의 run
  기록을 읽고 바꿀 수 있다. 공용 instance라는 결정에 딸린 것이고 받아들였다.
- 소비자 URL의 앱 쪽 준비(location-scoped GraphQL, 계획 3.1~3.4)는 각 저장소의 PR이다. Map API의 host
  allowlist(`KOR_TRAVEL_MAP_API_DAGSTER_ALLOWED_HOSTS`)는 URL이 아니라 이 계약 밖이고, Map API가 클라이언트에
  돌려주는 공개 GraphQL URL은 이제 Basic Auth gateway 뒤다 — C7 게이트는 그 입구로 Basic Auth를 실어 붙는다(위
  개정: 비-브라우저 POST). Map API의 서버 쪽 호출은 내부 GraphQL URL(loopback)로 가르고 공개 URL은 보고용으로만
  남긴다(Map 저장소, `consumers`의 `internal`·`public/graphql`). Map 전환은 Map 전용 C6c 계약(보호 서비스 집합, pinned rebuild의 필요 서비스)도 바꿔야 한다.
- ~~Map·PinVi 전환 전에 pinned 재구축·C6c의 literal 집합을 스위치에서 파생하는 PR이 필요하다(위 9, M2).~~ 해제 — `runtime_topology`.
- 공개 host `dagster.digitie.mywire.org` → `192.168.1.14:11001`과 옛 hostname redirect는 저장소 밖(OPNsense)이다.


## ADR-55: Concierge는 공용 Dagster code server로 합류한다

- 상태: accepted
- 날짜: 2026-10-06
- 결정자: human, Codex

`conc`는 Dagster webserver/daemon을 새로 만들지 않고 shared-only family로 등록한다.
code location은 `ktc.dagster.definitions`, loopback gRPC는 `12603`이다. 기존 APScheduler는
`legacy-scheduler` rollback profile로 남고 기본 runtime/target에서 제외한다.
workspace는 registry/compose에서 유도하고 digest를 갱신한다. canonical child-health/incarnation
probe와 shared PostgreSQL instance를 그대로 사용한다. location limit 2와 각 Concierge job limit 1을
추가하며 기존 프로젝트의 cap을 바꾸지 않는다. shared-only의 webserver/daemon None은 허용하지만
own family 누락과 한쪽만 있는 legacy 정의는 계속 거부한다. candidate bind 허용 목록도 공유 instance
read-only bind를 명시한다. Map/PinVi pinned 재구축 정본의 기존 필드는 유지하고 추가된 서비스 관측만
갱신한다. 앱의 backend/generation 전환·drain은 Concierge CLI가 소유한다.
실제 운영 전환은 별도 창이며 이 변경의 격리 검증을 운영 설치 완료로 간주하지 않는다.


## ADR-56: C6c `env_file` 누출은 그 파일의 loader 모두가 원본에서 받지 않는 값이다

- 상태: accepted
- 날짜: 2026-10-07
- 결정자: human, Claude

**문제.** ADR-55로 Concierge API/MCP/공유 Dagster code server가 `${KOR_TRAVEL_CONCIERGE_REPO_DIR}/.env`를
다시 읽게 되자, `validate_compose_candidate_protected_values`의 `env_file` 내용 스캔이 모든 Map/PinVi
pinned 재구축을 `prebuild_snapshot`에서 "compose candidate env_file leaks C6c data for
kor-travel-concierge-api"로 거부했다. 그 스캔은 Manager `.env`의 **모든** 민감 값(55개)을 C6c 데이터로
보았고, Concierge `.env`에는 그중 두 묶음이 정당하게 있었다 — 여러 서비스가 공유하는 VWorld browser key
(`VWORLD_SERVICE_KEY`·`KOR_TRAVEL_GEO_V2_API_KEY` = Manager의 `NEXT_PUBLIC_VWORLD_API_KEY`·
`KOR_TRAVEL_CONCIERGE_UI_VWORLD_SERVICE_KEY` 등 6개 이름)와 Concierge UI 자신의 auth 값 세 개
(`KTC_ADMIN_PASSWORD_HASH`·`KTC_UI_SESSION_SECRET`·`KTC_ADMIN_PROXY_SECRET`). 두 번은 `REPO_DIR` 줄을
꺼서 우회했지만 Concierge는 그 `.env`가 필요하다.

**결정.** `env_file` 내용에서 누출로 보는 값은 원본(설치된 release의 `.ktdm-release-compose.yml`)의 `env_file`
경로마다 파생한다(`compose_references.env_file_secret_values_by_path`). 파일 하나는 그것을 읽는 서비스(loader)
**모두**에게 같은 내용을 주므로 허용은 다음 셋의 합이다.

1. 모든 loader가 원본에서 받는 값의 **교집합**. "받는다"는 environment·command·entrypoint 자리만 센다
   (image·container_name·build·ports·healthcheck는 Compose가 쓰고 끝난다). DSN 안의 비밀도 받은 것이다.
2. 같은 checkout **가족** 중 그 파일을 읽지 않는 서비스(BFF UI)가 받는 값 가운데, 가족 밖 어느 서비스도 원본의
   어느 자리에서도 받지 않는 값 — 그 checkout만의 비밀이다(UI auth 세 값, BFF backend key).
3. 원본이 `NEXT_PUBLIC_*` 컨테이너 env key·build arg에 두는 값 — 브라우저 번들에 실리는 공개 값이다(VWorld key).

가족은 경로가 뿌리를 둔 변수 하나(대개 `*_REPO_DIR`)를 build context나 `env_file` 경로로 쓰는 원본의 서비스다
(Concierge: API·MCP·공유 Dagster code server·legacy scheduler·UI). 경로의 변수가 하나가 아니거나, 가족이 다른
뿌리를 함께 쓰거나, 가족에 C6c 보호 서비스(topology에서 파생한 Map/PinVi 필수 집합)가 있으면 fail-closed로
거부한다. 내용 스캔은 값을 percent-decoding해서도 본다. 원본에 없는 `env_file` 자리는 종전대로 전체 집합이다.

**택하지 않은 것.** (a) 보호 집합을 "Map/PinVi 소유 키"로 좁히기 — 소유를 이름 접두사나 서비스 표로 적어야
하고(리터럴 결박), Geo·Weather·Manager 자신의 비밀이 보호 밖으로 떨어진다. (b) topology의 "non-C6c tenant"
면제 — 면제된 가족의 `env_file`은 Map DB password까지 실어도 통과한다. (c) 첫 판의 "가족 누구든 받는 값"
합집합 — 적대 리뷰 MED-1: 공유 Dagster plane password(전 tenant run 기록)를 code server만 받는데, 같은
`.env`를 읽는 MCP(`0.0.0.0`, 기본 인증 꺼짐)에게도 흘러간다. 교집합이 그것을 막는다.

**결과.** n150 실측(키 이름만): 55개 보호 값 중 Concierge `.env`에 허용되는 값은 6개다 — VWorld(공개, 6개 이름),
Concierge UI session secret·admin hash·admin proxy secret(가족 전용), Concierge API key
(`KOR_TRAVEL_CONCIERGE_API_KEYS`·`..._BACKEND_API_KEY`가 같은 값, 네 loader 공통), Concierge app DB password
(네 loader의 DSN 안). 공유 Dagster app password를 포함한 49개 값은 누출이다. 설치된 compose와 실제 Concierge
`.env`는 통과하고, 같은 `.env`에 공유 Dagster password를 더하면(메모리 안) 거부된다. 설치된 `42ffc553` 코드는
같은 후보를 위 메시지로 거부한다(resolved 검사는 양쪽 모두 통과).

## ADR-57: weather Web은 host 포트 `12205`에서 듣는다 — 141xx 대역 밖의 의도된 예외

- 상태: accepted (repo-level 설정·문서 변경만 이 변경의 범위 — n150 재배포는 별도 단계)
- 날짜: 2026-10-08
- 결정자: 사용자(소유자), Claude

### 컨텍스트
OPNsense HAProxy의 weather backend는 `192.168.1.14:12205`를 가리킨다. 소유자는 HAProxy를 그대로
두고 weather Web을 그 포트에 직접 붙이라고 지시했다("weather 웹을 12205 포트로 설정"). 그때까지
weather Web(`kor-travel-weather-web`)은 `14105`에서 들었다 — ADR-47은 "HAProxy가 `14105`를
가리키므로 포트를 재배정하지 않는다"고 적었지만, 그 전제는 지금 HAProxy 설정과 맞지 않는다.
`12205`는 ADR-48(2026-09-21)이 Grafana를 `12104`로 옮기며 비운 자리다. 2026-10-08 n150 `ss -ltn`
실측에서 `12205`를 듣는 프로세스는 없고(Grafana는 `12104`), n150 Manager `.env`에는
`KOR_TRAVEL_WEATHER_WEB_PORT` 재정의가 없다(compose 기본값이 그대로 적용된다).

### 결정
- `KOR_TRAVEL_WEATHER_WEB_PORT`의 기본값을 `14105` → `12205`로 바꾼다.
- `network_mode: host`에서는 `ports:`가 버려지고 이미지 CMD(weather `deploy/Dockerfile.web`)가
  `--port 14105`를 굽는다. 그래서 compose가 `command:`로 CMD를 같은 모양
  (`npm run start -- --hostname 0.0.0.0 --port ${KOR_TRAVEL_WEATHER_WEB_PORT:-12205}`)으로 덮는다.
  `ports:`·registry(`config/docker-targets.yml`의 `connection`·`expected_ports`)·weather API의
  `KOR_TRAVEL_WEATHER_CORS_ORIGINS` 기본값의 loopback origin도 같은 변수/값을 따른다.
- 이 포트는 weather 대역(`14100-14199`) 밖의 의도된 예외다(`docs/ports.md` "weather Web의 대역 예외").

### 근거
- 소유자가 HAProxy 쪽 값을 유지하고 목표 포트를 명시했다.
- 이미지 CMD를 바꾸는 것은 weather 저장소의 일이고, host 모드에서 listen 포트를 바꾸는 유일한
  compose 수준 수단은 프로세스 인자다. npm `start` 스크립트(`next start --hostname 127.0.0.1
  --port 14105`)에 `--` 뒤로 붙인 인자가 이긴다 — 이미지 CMD 자신이 이미 이 방식으로 `0.0.0.0`을 얻는다.

### 결과(긍정)
- HAProxy 설정 변경 없이 공개 weather Web이 n150의 `12205`로 닿는다(재배포 후).
- 포트가 변수 하나(`KOR_TRAVEL_WEATHER_WEB_PORT`)에서 command·`ports:`·CORS 기본값으로 함께 풀린다.

### 결과(부정)
- `14105`를 가정한 북마크·외부 규칙은 재배포 뒤 닿지 않는다.
- weather 이미지 CMD의 인자 모양(`npm run start -- …`, WORKDIR `/app`)이 바뀌면 이 command도 함께
  바꿔야 한다. 이미지 쪽 `EXPOSE 14105`는 host 모드에서 의미가 없다.
- `gra` target 이름의 대역(`12200-12299`) 안에 다른 target의 포트가 생긴다.

### 확인하지 않은 것
- n150의 weather Web 컨테이너는 Manager 재설치 + `kor-travel-weather-web` 재생성 전까지 계속
  `14105`를 듣는다. 이 ADR·브랜치는 live 서비스를 건드리지 않았다.
- weather Web의 `WEATHER_UI_PUBLIC_ORIGIN`(CSRF origin)은 공개 HTTPS origin이라 포트와 무관하다고
  보았지만 재배포 뒤 로그인으로 확인해야 한다.

### cutover(n150)
- 이 ADR 전에 임시 방편으로 systemd 유닛 `weather-web-port-12205.service`를 두었다. 이 유닛은 iptables
  nat PREROUTING `REDIRECT 12205→14105`를 걸고 enable 상태다. 웹을 재생성하기 **직전**에
  `systemctl disable --now weather-web-port-12205.service`를 실행하고 유닛 파일을 지운다.
  - `stop`만 하면 enable이 남아 재부팅 뒤 규칙이 되살아난다. 그러면 LAN 트래픽이 아무도 듣지 않는 14105로 간다.
- 재생성은 `up -d --no-deps kor-travel-weather-web`으로 한다. CORS 기본값이 바뀌어 weather API의
  config-hash도 달라지므로, `--no-deps`가 없으면 API까지 재생성된다.

## ADR-58: 두 번째 Docker 호스트(odroid `192.168.1.13`) — 호스트 프로필 Manager와 호스트당 Dagster plane·PostgreSQL

- 상태: **accepted**(2026-10-20 — T-MGR-01a~05(01a·01a-F·01b·01c·02·02r·03·03r·04·04r·05)가 전부 설치됐다: registry
  호스트 차원·R1~R5·R7, workspace/consumer-origin/Prometheus/PG 수치·이미지 env 파생물, `ensure` 프리플라이트 효과
  검사가 머지돼 n150에 반영됐다(`docs/tasks-done.md` 해당 항목). **아직 계획인 부분(T-MGR-06 — host 플립 PR ×3: transport
  → weather → geo)**: odroid로의 실제 이전은 미집행이며, 그 세 target의 "odroid에서 실행 중" 서술은 여전히 계획이다 —
  아래 "결정"·"관련 문서" 중 파생 **구조**(registry 호스트 차원, 각 파생기, 프리플라이트)는 현황이고, odroid가 **실제로
  맡는 호스트**라는 서술만 계획으로 남는다. 2026-10-13~16 "잠정 시행"으로 선반영된 부분은 T-MGR-NH(일반화, 완료)로
  대체됐다 — 아래 본문은 설계 당시 기록이며 일반화 이후 실제 구조는 `docs/multi-host.md`가 정본이다)
- 날짜: 2026-10-13
- 결정자: 사용자(소유자 — "geo·weather·transport를 odroid로", "포트·URL·이름은 n150과 동일하고 호스트만 `.14 → .13`",
  "데이터 보존·롤백은 검토하지 않는다(다시 로드)", "Manager는 범용성을 유지한다 — 리터럴 결박 대신 rendered 모델
  파생·런타임 검증·규칙 고정", "설계와 각 프로젝트 문서 수정해서 코딩을 할 수 있게 준비하고 각각 PR 머지까지 진행"),
  Claude
- 관련: ADR-47(weather internal target — loopback 전제), ADR-50(과결박 없이), ADR-51(installer에 로직을 더하지 않는다),
  ADR-52(PG `init`·exec probe), ADR-53(Map 두 DB의 공용 instance 이전 — 공용 PG 튜닝 수치는 compose `command:` 리터럴
  `docker-compose.yml:254-279`), ADR-54(공용 Dagster plane — "plane 하나"), ADR-57(weather Web
  `12205`), `docs/platform-topology.md` §10, `docs/ports.md` "호스트 규칙", `docs/prod-deployment.md` §9,
  `docs/docker-management.md` §3.2, `docs/shared-postgres-onboarding.md` §11, `docs/bindings.md` B-5~B-9.
  교차 저장소(같은 날, 같은 설계에서): transport ADR-014, geo ADR-070, Map ADR-110, concierge ADR-50, weather
  `docs/decisions.md` 인덱스(Map 번호 거울), PinVi는 그 저장소 규약의 다음 번호. Track 1(geo 주소 갱신 대응: geo ADR-069·
  Map ADR-109·concierge ADR-49·transport T-045)은 이미 main에 머지돼 있다 — 상호작용은 "관계" 절.
- 설계 정본: Track 2 설계 최종본(2026-10-13, 저장소 밖 — 절 1~15, 변경 결정 목록, 리뷰 반영 기록 3차, 실측 반영 기록)과
  그 fact base. 이 ADR은 그 설계의 **결정**만 옮기고 수치·근거는 요약한다. 인용 `file:line`은 이 저장소 `094d397`
  (origin/main, 2026-10-13) 기준이다. 설계 최종본의 절 번호를 인용할 때는 항상 "설계 §x.y"로 접두한다 — 접두 없는 `§`는 이
  저장소 문서의 절이다.

### 컨텍스트

**바꿔야 할 단일 호스트 전제(S1~S19; S9는 transport 저장소 몫).** Manager는 호스트가 하나라는 가정을 코드·registry·compose·테스트
네 층에 두고 있다.

| # | 전제 | 근거(`094d397`) |
|---|---|---|
| S1 | Docker 클라이언트는 로컬 소켓(`docker.from_env()`); compose는 로컬 `working_dir`에서 subprocess | `backend/src/kor_travel_docker_manager/services/docker_service.py:717-722`; `services/compose_service.py:3137-3166` |
| S2 | 전부 `network_mode: host`, 서비스 간 참조 `127.0.0.1:<port>` | `.env.example:5`; `docs/ports.md` "기본 규칙" |
| S3 | registry target에 **host 필드가 없다**; `connection:`은 URL과 설명 문자열이 섞여 있다; 한 컨테이너가 여러 target에 속한다(service 이름 `kor-travel-shared-postgres`는 geo·conc·map·pinvi·weather·dagster **6개** target의 `services:`에 있고, containers 키는 `kor-travel-shared-postgresql`이다 — 결정 1의 host 합집합은 **service 이름**으로 센다) | `config/docker-targets.yml:23-345`(containers; `:24-26` 이름 쌍), `:438,492,531,590,731,798`(services), `:778-800`(dagster target) |
| S4 | 외부 target `working_dir`는 로더가 **절대 경로·정규화만** 검증한다; 로컬 존재 대조는 `ktdctl targets validate --check-coordinates` **옵트인**(기본 off)뿐이다 | `services/registry.py:942-972`; `cli.py:183,1722` |
| S5 | C6c: PG는 정확히 하나, `listen_addresses=127.0.0.1`, DSN host `127.0.0.1` | `services/c6c_deployment.py:974-990,1392-1394` |
| S6 | 호스트 bind는 n150 절대 경로, build context는 `../kor-travel-{geo,weather}` | `config/docker-targets.yml:868-`(compose_binds); `docker-compose.yml:1083,2403`(geo api·cs)·`:2705`(weather) |
| S7 | 공용 workspace는 host `127.0.0.1`로만 파생(0.0.0.0은 127.0.0.1로 정규화); 테스트·cutover가 강제 | `services/runtime_topology.py:247-255`; `backend/tests/test_dagster_shared_workspace_is_derived.py:420,503-505`; `scripts/dagster-shared-cutover.sh:202-203` |
| S8 | 유일한 원격 추상은 `offbox-sync`(SSH/rsync **push**, 백업 전송만) | `services/offbox_backup_sync.py:1-16,40-45` |
| S9 | (transport 저장소 몫 — transport ADR-014) 배포 스크립트가 `.14` 외 호스트를 거부하고 DSN `@127.0.0.1:11000`을 `require_exact` | transport `scripts/deploy-server14.sh`·`deploy-server14-remote.sh`; Manager는 R2/R4 효과(`targets validate --json`)만 제공 |
| S10 | compose subprocess에는 allowlist + `DOCKER_*`만 통과한다; compose 파일은 root `.env`와 프로세스 env만 읽고 **registry를 읽지 못한다** | `services/compose_service.py:3154-3158` |
| S11 | Manager backend는 compose 서비스가 아니라 **systemd**(`ktdm-backend`, uvicorn `0.0.0.0:12901`); 설치는 root clone + 오프라인 wheelhouse + trusted installer | `deploy/systemd/ktdm-backend.service:29`; `scripts/install-ktdm-trusted-release:28`; `docs/prod-deployment.md` §3·§3.1 |
| S12 | 공용 plane의 `dagster.yaml`·`workspace.yaml` digest가 **compose 리터럴**이고 healthcheck가 마운트 파일 sha256[:16]과 대조해 다르면 exit. instance digest 리터럴 **7곳**(daemon·webserver·conc/map/pinvi/geo/weather 코드서버), workspace digest 2곳 | `docker-compose.yml:828-829,847,898-899,911,1373,1713,2128,2442,2838` |
| S13 | 공용 PG 서버 이미지는 digest 핀, db-init one-shot 6개는 태그 `postgis/postgis:16-3.5` | `docker-compose.yml:213`; `backend/tests/test_transport_shared_bootstrap_contract.py:23` |
| S14 | 백업 role 목록에 weather가 없다 | `services/standalone_backup.py:57-64` |
| S15 | transport code-server는 Manager 설치 경로의 `dagster.yaml`을 bind한다(transport `docker-compose.shared.yml`) | transport 저장소 |
| S16 | compose `depends_on`이 **tenant 경계를 넘는다**: `kor-travel-map-api → kor-travel-geo-api`(service_healthy). `ensure`는 `--no-deps` 없이 의존 closure를 범위로 센다(C6c 재구축만 `--no-deps` 강제) | `docker-compose.yml:1530-1538`(geo `:1535`, concierge `:1537`), `:1630,1698,1962`(같은 호스트 안 간선); `services/compose_service.py:3572-3589,3714-3719,3935` |
| S17 | Manager compose는 weather base URL을 **PinVi ETL code-server에만** 리터럴로 준다; `pinvi-api` 블록에는 없어 pinvi-api는 코드 기본값 `localhost:14101`로 trip 날씨·weather card를 부른다 | `docker-compose.yml:2142`(ETL 리터럴 `http://127.0.0.1:14101`), `:1943-2018`(pinvi-api 블록 — weather env 없음) |
| S18 | 공용 instance `run_monitoring.max_runtime_seconds: 21600`; 테넌트는 `dagster/max_runtime` 태그로 올린다 | `config/dagster-shared/dagster.yaml:137-144` |
| S19 | weather code-server만 `mem_limit 4g`·`mem_swappiness 10`·`oom_score_adj -500`; 공용 PG·geo code-server에는 `mem_limit`·`oom_score_adj`가 없다; compose 전체에 `memswap_limit` 0건 | `docker-compose.yml:2882-2885,2988,3074`; `:254-269`(PG) |

**소유자 지시와 비범위.** geo·weather·transport 세 서비스와 **그들의 PostgreSQL**을 odroid(`192.168.1.13`)에서 돌리고,
n150(`192.168.1.14`)의 Map/PinVi/concierge/HAProxy는 REST로만 부른다. 포트·URL·컨테이너명·compose 프로젝트명·디렉터리
규칙은 n150과 동일, 호스트만 `.14 → .13`. 데이터 보존·롤백은 비범위 — 세 DB는 **소스에서 재적재**하고 n150의 세 DB는 전환
확인 뒤 drop한다. 레지스트리 도입 없음, replication/HA 없음, Map/PinVi/concierge 재배치 없음. **이중 수집은 금지**한다(두
호스트가 같은 data.go.kr/KRIC/Opinet 키로 동시에 수집하면 오퍼레이션별 일일 한도를 2배 소모) — 전환 중 수집 공백을 받아들인다.

**odroid 실측(2026-10-13, 비root 읽기 전용 프로브 + 소유자 처리 결과).** aarch64 Cortex-A55 ×4 1.8 GHz, RAM 7,676 MiB,
**swap 0**, eMMC 58 GB(`/` + `/var/lib/docker`, 33 G free), NVMe 938 GB ext4 통째 `/mnt/media`(Plex 라이브러리 336 G, 555 G
free, 슬롯 링크 PCIe 2.0 ×1), noble userland + **PID1 systemd 245(focal)** + kernel 5.10.198, **cgroup v1(hybrid) memory
컨트롤러·swap accounting on**, Docker 29.1.3(docker.io), **`docker compose` v2 플러그인·buildx 없음**(python docker-compose 1.29.2만),
IP **DHCP**(NM 프로필 소유 = 보존 서비스 `webvnc-router-network.service`), 호스트 방화벽 없음(Docker iptables 규칙만), 상주
서비스 Plex(32400/32469)·Samba(139/445, `[homes]` 쓰기 가능)·webvnc-router(2121/6080)·8080·1994 + legacy parking-radar
(3000/8000/18000, uvicorn RSS 1.43 GiB)·Portainer(9443). **같은 날 03:4xZ 소유자 처리 완료**: WSL에서 `ssh digitie@192.168.1.13`
직접 접속 가능(종전 "No route"는 WSL Docker 잔재 네트워크가 `.13`을 가린 것 — 제거됨), `/etc/sudoers.d/digitie` NOPASSWD:ALL
(`sudo -n` 동작), `digitie`를 docker 그룹에 추가, `timedatectl set-ntp true` + timesyncd 재시작으로 시계 2026-10-13 03:38Z 동기·RTC
갱신(상주 서비스 유지). **미완**: timesyncd 부팅 순서 사이클 영속화(drop-in — 재부팅 뒤 재발 가능), compose v2·buildx 설치,
방화벽, fstab bind, swapfile. 실측·미완 목록의 정본은 `docs/platform-topology.md` §10.

**하드웨어 판단(요약; 배율은 가정).** A55 코어당 N150의 1/3~1/4(가정) → geo `mv_refresh` 1.2~1.6 h가 4~5 h(6 h 상한의
70~85 %), full load 12 h+. 메모리 예산 7.5 GiB에서 OS·Docker 1.0 + 보존 서비스 0.55(실측 유휴 ≈0.3) 선공제 → 상시 anon ≈5.55,
최악 동시 창(geo run ‖ weather 적재 ‖ Opinet) ≈7.85 → **RAM을 넘는다**(여유 −0.35) — 그래서 memswap 정책·swapfile·geo 플립
조건이 결정에 들어간다. IO는 geo(temp spill·MV swap·GIN·dump)가 수혜, weather는 hot set(≈20 GB 가정)이 캐시 밖이라 IO 의존이
생긴다. n150은 디스크 183 GB·메모리 피크(PG 몫 + geo 4.8 + weather 1.7 + transport 1.4 GiB)·CPU를 돌려받는다.

### 결정

1. **registry에 호스트 차원을 둔다 — `hosts:` 절 + target `host:`.** 주소의 유일한 자리는 `config/docker-targets.yml`의 새
   최상위 절 `hosts:`다(`n150: {addr: 192.168.1.14, default: true}`, `odroid: {addr: 192.168.1.13}`). tenant target(geo·weather·
   transport·map·pinvi·conc)은 `host:` 하나(생략 = default), infra target(storage·dagster·cadv)은 `host: all`(모든 호스트에
   host-local로 존재), prom·gra는 생략(= n150, 관측 결정 11). **컨테이너는 host-local**이다: 같은 이름의 컨테이너는 그것을
   service로 갖는 target들의 host **합집합**에 존재한다(`kor-travel-shared-postgresql`은 `dagster`(all) 때문에 두 호스트 모두;
   `kor-travel-geo-api`는 geo target의 host 하나). 로더 검증: `default: true` 정확히 1개(0·2개는 `TargetsConfigError`), `addr`는
   IPv4 리터럴, `host:` 값은 `hosts` 키 또는 `all`(unknown host 거부), 운영 모드의 `KTDM_HOST` 값은 `hosts` 키. `all` target의
   `include`는 `KTDM_HOST`로 필터해 "이 호스트의 target만"으로 파생한다. **첫 구현(T-MGR-01)은 기제만 넣고 tenant target은
   전부 default host에 둔다.** **n150 렌더(`docker compose config`) 허용 diff 목록 — 단일 정본**(결과(긍정)·후속·`prod-deployment.md`
   §9.2-1 게이트·T-MGR-01c/02/03 수용 기준은 전부 이 목록을 가리킨다; 어느 PR이 목록 밖 줄을 바꾸면 그 PR이 이 목록을 먼저 고친다):
   ① `depends_on` 간선 −1(`kor-travel-map-api → kor-travel-geo-api`, T-MGR-01b) ② geo api/cs 소스 bind +1줄씩(T-MGR-01d)
   ③ pinvi-api 블록 weather env +1줄(T-MGR-01c; 나머지 R2 기본값은 값 불변) ④ workspace bind source 2줄(`workspace.yaml` →
   `workspace.n150.yaml`, `:832,902`; T-MGR-02) ⑤ instance digest 값 7곳 1회(T-MGR-02) ⑥ `oom_score_adj` 줄(PG·plane daemon/webserver)과
   `mem_limit` 있는 서비스마다 `memswap_limit` 줄(T-MGR-03) ⑦ 결정 8 ⅰ 중 n150 compose에 없는 PG `-c` 키 6개(`max_parallel_workers`·
   `_per_gather`·`effective_io_concurrency`·`checkpoint_timeout`·`wal_compression`·`autovacuum_max_workers`)의 env 파생 줄 — 기본값 = PG 16
   서버 기본값이라 효과 0, 테스트가 기본값 == 서버 기본값을 단언(T-MGR-03) ⑧ db-init-geo의 확장 4종 `CREATE … IF NOT EXISTS` 줄
   (결정 3; n150에서는 no-op, T-MGR-03) ⑨ `mem_limit`의 "없음" 기본값 표현(T-MGR-03이 정하면 추가). 값이 바뀌는 것은 ①②③④⑤⑥⑦⑧뿐이고
   PG·geo cs에 n150에서 실효 `mem_limit`는 생기지 않는다.
2. **호스트마다 같은 Manager 인스턴스(호스트 프로필 Manager).** 대안 (1) "n150 Manager 하나가 `DOCKER_HOST=ssh://`로 원격
   Docker"는 체크아웃·`config/`·secrets를 odroid에도 같은 경로로 미러해야 하고(결국 체크아웃 둘), 긴 배포가 ssh 끊김에
   취약하며, C6c "PG 하나" 잠금을 전면 수정한다 → 기각. 대안 (3) "(2) + n150 Manager가 odroid Manager REST 집계"는 기제 하나를
   더 얹는다 → 지금은 두지 않는다(원격 상태는 아래 R4의 HTTP probe로 충분). **Manager 간 SSH는 없다.** 각 Manager는 자기
   로컬 소켓(S1 불변)·자기 호스트 좌표(S6 불변)·자기 wheelhouse(S11)로 돈다. 자기 식별은 root `.env`의 **`KTDM_HOST=<hosts 키>`**
   하나다.
3. **호스트당 Dagster plane + 호스트당 PostgreSQL.** ADR-54의 "공용 plane 하나"와 ADR-47/C6c의 "PG 하나·loopback"은 **호스트
   단위로 범위를 명시**한다(supersede가 아니다 — 한 호스트 안에서는 그대로 참이다). odroid에는 같은 compose의 `dagster`
   target(daemon·webserver `127.0.0.1:11002`·gateway 11001·`dagster_shared`)과 같은 서비스명·포트·bind의 PG
   (`kor-travel-shared-postgres`, 11000, `listen_addresses=127.0.0.1`)가 뜬다. PG는 registry에서 `dagster` target의 service이므로
   **odroid `ensure dagster`가 PG를 올린다**. DB·롤은 n150과 같은 이름(`kor_travel_geo`·`kor_travel_weather`·`kor_travel_transport`·
   `dagster_shared`). **connlimit은 `dagster_shared` 45만**(compose 리터럴 `docker-compose.yml:726,728`); 앱 role 셋은 db-init이
   `CONNECTION LIMIT`을 두지 않아 무제한이고 n150 실측도 같다(fact base — 설계 §5.4의 "15/10/10"은 compose·실측 어디에도 없는 값이라
   쓰지 않는다; odroid도 db-init 그대로). **`kor_travel_transport_dagster`는 만들지 않는다**(Manager db-init-transport는 그것을
   만들지 않으며 — `backend/tests/test_transport_shared_bootstrap_contract.py:35-37,47` — transport backend 코드도 읽지 않는다;
   transport env의 `DAGSTER_POSTGRES_URL`은 그 저장소 compose의 `:?` 보간 때문에 **모양만** 남긴다). pg_hba는 loopback scram만
   (n150의 `10.88.0.0/24` 잔재는 복제하지 않는다). **geo 확장 4종의 생성 주체**: 오늘 db-init-geo는 확장을 만들지 않는다
   (`docker-compose.yml:382-391` 주석 — cutover `pg_restore`가 dump의 `x_extension` 배치를 재생하게 두려고; one-shot이 만드는 확장은
   concierge postgis 1종 `:371`뿐, onboarding §4.3). odroid는 pg_restore 없는 fresh 경로이고 app role은 NOSUPERUSER라 postgis를
   만들 수 없으므로 **T-MGR-03이 db-init-geo에 `CREATE SCHEMA IF NOT EXISTS x_extension` + `CREATE EXTENSION IF NOT EXISTS {postgis,
   pg_trgm,unaccent,pg_stat_statements} SCHEMA x_extension`을 더한다**(geo DDL과 같은 배치 — compose 주석 `:384-387`; 스키마 소유는
   geo ADR-070과 맞춘다; n150에서는 전부 존재해 no-op). weather·transport는 n150에서도 fresh DB로 합류했고 소스에 `CREATE EXTENSION`이
   없어 공백이 없다. **transport role/DB one-shot의 실행 경로**: `kor-travel-shared-db-init-transport`(`:588`)는 어느 target의 service도
   등록된 `init_steps`도 아니다(`config/docker-targets.yml` — n150에서도 그랬다) → odroid에서는 설치 루트에서 `docker compose run --rm
   --no-deps kor-travel-shared-db-init-transport`로 1회 돌린다(`prod-deployment.md` §9.2 4a′; named `init_steps` 등록은 대안 — transport가
   Manager 앱 lifecycle에 들어올 때 함께). **대안 (b) "n150 공용 plane에 odroid code-server를 원격 등록"은 기각**: gRPC
   무인증 LAN 노출 + n150 PG LAN 개방 + C6c 잠금 해제 + compute log가 n150 webserver 로컬 파일이라 odroid run 로그가 **안 보인다**
   (`config/dagster-shared/dagster.yaml:38-42`, `LocalComputeLogManager`) + 한 instance가 두 호스트를 섞어 센다. (c) 혼합(transport만 (b))은 8 GB가 정말
   모자랄 때 재검토하되 (b)의 보안 비용은 그대로다. (a)의 비용은 UI 둘과 ≈0.7 GiB, 그리고 plane healthcheck가 **모든** location
   SERVING을 요구하므로(빈 workspace는 healthy, `docker-compose.yml:789-794`) 단계적 이전은 **workspace에 location을 하나씩 더하는**
   방식으로만 가능하다 → 결정 6 "플립 = 합류".
   **규칙(2026-10-16, T-MGR-NH): plane은 PG가 있는 호스트에서 돈다** — 둘은 같은 `dagster` target이라 배치가 하나다(나누지 않는다).
   호스트 집합은 registry에 명시 목록으로 둔다(`docs/multi-host.md` NH-3).
4. **파생 규칙 R1~R7(값이 아니라 규칙을 고정한다).**
   - **R1** registry `connection:`의 host는 `{host}` 자리표시 — 렌더 호스트 ∈ 컨테이너의 host 집합이면 `127.0.0.1`, 아니면 그
     집합의 (단일) addr. `all` 컨테이너(PG·RustFS·cAdvisor)는 항상 `127.0.0.1`. 설명 문자열 항목(S3)은 대상이 아니다.
   - **R2** 내부 target(Map/PinVi)의 consumer env는 **compose 기본값을 커밋된 파생물로** 둔다. 대상은 줄 번호 열거가 아니라
     **규칙**이다: "형제 서비스 포트(`12501`·`14001`·`14101`)를 기본값에 가진 **모든** compose env(비활성 `legacy-dagster` profile
     포함 — `compose config`는 profile을 가리지 않고 보간한다)". 파생 테스트(B-6)는 렌더된 compose를 **스캔**해 그 집합을 찾고
     registry 계산값과 대조한다 — 지정 줄만 보면 하나를 빠뜨려도 초록이다. 오늘 그 집합은 **7곳**: geo
     `KOR_TRAVEL_MAP_KOR_TRAVEL_GEO_BASE_URL: ${KOR_TRAVEL_MAP_DOCKER_GEO_BASE_URL:-http://<host(geo)>:12501}` **4곳**(`docker-compose.yml:1590`
     map-api, **`:1735` map code-server** — MOIS/geocode run을 실제로 돌리는 서비스, `:1827`·`:1934` legacy webserver/daemon), transport
     `${KOR_TRAVEL_MAP_KOR_TRAVEL_TRANSPORT_BASE_URL:-http://<host(transport)>:14001}`(`:1744`), PinVi ETL `:2142`를 같은 모양의
     `${PINVI_KOR_TRAVEL_WEATHER_BASE_URL:-http://<host(weather)>:14101}`로(**2026-10-14 잠정 시행으로 대체**: 소비자 포트 geo `12501`·transport API gateway `12301`·weather `12201`, 집합 8곳(geo 5 — Map 4·pinvi-api 1, transport 1, weather 2 — PinVi ETL·pinvi-api), n150 workspace digest `fbd0eadc90829753`(weather·transport 제외 4 location → **2026-10-15 `4461abc13cce9c4f`, geo도 제외 3 location**) — ADR-58 "잠정 시행".), **그리고 pinvi-api 블록(`:1943-2018`)에 같은 변수를
     추가**한다(없으면 weather 플립 뒤 PinVi 여행 날씨가 조용히 빈다 — S17). 플립 PR이 `<host(x)>`를 `127.0.0.1`에서
     `192.168.1.13`으로 **그 집합 전부** 바꾼다(geo 플립이 `:1590`만 바꾸면 map code-server는 loopback에 남아 n150 geo-api stop 뒤 모든
     geocode run이 connection refused — 테스트가 그것을 빨갛게 만들어야 한다). root `.env`의 재정의는 `ktdctl targets validate`가 파생
     기대값과 다르면 거부한다. 외부 저장소 값(concierge env, transport `.env.prod`)은 Manager가 쓰지 않고 기대값만 문서화한다.
   - **R2 보간 범위 예외(2026-10-25)**: 쌍 재구축은 정본 Map·PinVi 폐포만, 신뢰 설치의 일반·CLI 전용 모드 사전검사는 후보 release의 모든 host-local 소유 서비스 폐포만 보간한다. 제외된 off-host 서비스의 필수 env 검사는 이 두 경로에서 생략하지만 선택 종속성/도달 자원/필수 env와 전체 원문·환경 identity는 유지한다. 일반 전체 검증은 기존대로다. 일반 설치도 제외된 off-host 필수 검사를 생략하며, 기존 서비스 생명주기·health·복구는 보존한다. 디스크 계약이나 mutation 권한 확대는 없으며 [상세 규약](host-compose-scope.md)을 따른다.
   - **R3** 자기 식별 `KTDM_HOST`는 **운영 모드에서만 강제**한다. 운영 모드 = trusted installer가 만든 release 아래 실행
     (`/opt/kor-travel-docker-manager/.ktdm-source-revision` 존재 — `services/compose_references.py:33`, `services/registry.py:129-132`,
     `docs/prod-deployment.md` §3). 운영 모드에서 `KTDM_HOST`가 비었거나 `hosts[KTDM_HOST].addr`가 호스트 IPv4 집합에 없으면 거부
     (빈 env가 가드를 뚫지 못하게). 비운영(CI·개발·isolated plane)은 명시 시에만 검사하고 미설정이면 default host — 기존 하네스
     무변경.
   - **R4** 생성·변경 명령(`ensure`/`start`/`restart`/`db-backup`/`pin`/C6c)은 `KTDM_HOST ∈ target의 host 집합`일 때만(`all`은 항상
     통과). `stop`·`logs`·`status`는 **로컬 소켓에 있는 컨테이너면 host 무관** — 판정은 Docker SDK로 그 컨테이너명이 조회되는지
     (효과 기반, label 없음). 원격 target의 `status`는 `connection`이 `http(s)://`인 서비스를 **전부** HTTP probe하고 결과를 세 값으로
     구분한다 — `up`(2xx/3xx)·`down`(응답은 있으나 비정상)·`unreachable`(connect timeout/거부); `http(s)://`가 아닌 항목(설명 문자열·
     gRPC·DB)은 `remote(not probed)`. registry에는 방화벽 개방 여부 필드를 두지 않는다(그 정보의 정본은 `docs/ports.md` 방화벽 집합이고
     Manager가 읽을 자리가 아니다) — 그래서 odroid의 HAProxy 전용 포트(`12505`·`12205`·`14002`·`12301`·`12305`·`11001`)는 n150 Manager에서
     `unreachable`이 **정상**이며 문서·UI가 그렇게 적는다(거짓 빨강 아님). `ktdctl targets validate --json`을 추가한다(transport 배포
     스크립트의 효과 검사용).
   - **R5** 호스트 좌표(`working_dir`·build context·bind)는 **호스트 상대** — 같은 경로 규칙을 odroid에 적용하고, 존재 대조는 오늘처럼
     `targets validate --check-coordinates` 옵트인(S4)이되 **자기 호스트 target에만** 건다(원격 target의 좌표는 이 호스트에 없는 것이
     정상). PGDATA bind 문자열 `/home/digitie/kor-travel-shared-data/pgdata`는 바뀌지 않고, odroid에서는 그 경로가
     NVMe(`/mnt/media/kor-travel/shared-data`)의 **fstab bind 마운트**다(symlink는 대안 — `/home/digitie`가 Samba `[homes]` 공유라
     symlink는 SMB 클라이언트가 지울 수 있고, 그러면 `ensure`가 eMMC에 빈 PGDATA를 만들어 조용히 initdb한다). 결박은 매 `ensure`
     프리플라이트가 `findmnt -T <bind 경로> -o SOURCE`를 root `.env`의 `KTDM_DATA_SOURCE_DEVICE`와 대조하는 **효과**로(미설정이면
     생략 — n150 무변경).
   - **R6** compose 파일은 **하나**다. 호스트별 차이는 root `.env`뿐(튜닝·이미지·비밀·workspace digest·`KTDM_PROD_URL_*`·`KTDM_HOST`).
     compose 리터럴 중 env로 파생시키는 것: PG 튜닝 수치(`${KTDM_PG_SHARED_BUFFERS:-1GB}` 꼴, 기본값 = 현재 n150 값
     `docker-compose.yml:265-291`; n150 compose에 없는 키는 결정 1 ⑦; `max_connections`는 compose에 **없다**(서버 기본 100) — 추가하지
     않는다; connlimit 45와 `max_locks_per_transaction=256`(결정 8 ⅰ)은 리터럴 그대로), PG 서버·db-init 6개 이미지(`${KTDM_POSTGRES_IMAGE:-postgis/postgis:16-3.5@sha256:8b33…}`·
     `${KTDM_POSTGRES_CLIENT_IMAGE:-postgis/postgis:16-3.5}` — 기본값은 현재 문자열 byte 그대로, odroid `.env`가 arm64
     `imresamu/postgis:16-3.5`를 준다), workspace bind 경로·digest. 커밋된 파생물로 고정하는 것: R2 consumer env 기본값(7곳)·
     Prometheus 타깃·workspace 파일.
   - **R7** compose `depends_on` 간선은 **양끝 서비스의 host 집합이 같을 때만** 둔다. tenant 간 의존은 런타임 REST 검증(health
     probe·fail-open 기동·`failure_kind`)으로 대체한다. T-MGR-01b가 `kor-travel-map-api → kor-travel-geo-api`(`docker-compose.yml:1535`)
     를 **삭제**한다 — Map은 `kor_travel_geo_preflight_required`(기본 false) fail-open 기동 경로가 있어 안전하다(Map 저장소:
     `packages/kor-travel-map-api/src/kortravelmap/api/app.py:700`의 분기, 설정은 `settings.py:164-176` — 설계 §7.2 R7 인용; Map 저장소
     기준이라 Manager head로는 검증하지 않는다). 같은 호스트에
     남는 간선(`map-api → concierge-api`, `pinvi-api → map-api`, `map-ui/map-cs → map-api`)은 그대로. 결박 테스트: registry로
     계산한 "호스트를 넘는 간선 집합 == ∅"(B-9). 이유: `ensure`는 `--no-deps` 없이 의존 closure를 올리므로(S16) geo 플립 뒤 n150
     `ensure map`이 geo-api·db-init-geo를 n150에 되살리거나 빈 `kor_travel_geo`를 재생성한다. R4는 target만 막고 closure는 못 막는다.
5. **파생 표면별 기제 — 런타임 파생과 커밋된 파생물을 구분한다.**

   | 표면 | 기제 | 결박 |
   |---|---|---|
   | registry `connection:`·probe(R1) | **런타임** 렌더(Manager가 registry를 읽는다) | 단위 테스트 |
   | compose env 기본값(R2 — 형제 포트를 기본값에 가진 env 전부, 오늘 7곳: geo 4·transport 1·weather 1 + pinvi-api 신설; **2026-10-14 잠정 시행으로 대체**: 소비자 포트 geo `12501`·transport API gateway `12301`·weather `12201`, 집합 8곳(geo 5 — Map 4·pinvi-api 1, transport 1, weather 2 — PinVi ETL·pinvi-api), n150 workspace digest `fbd0eadc90829753`(weather·transport 제외 4 location → **2026-10-15 `4461abc13cce9c4f`, geo도 제외 3 location**) — ADR-58 "잠정 시행".), `config/prometheus/prometheus.yml`, `config/dagster-shared/workspace.<host>.yaml` | **커밋된 파생물**(리터럴 `192.168.1.13` 포함) — compose·정적 YAML은 registry를 읽지 못한다(S10). `workspace.yaml`과 같은 선례(`docker-compose.yml:789-794`) | 파생 테스트가 기대값을 `hosts:`+`host:`에서 계산해 파일과 대조(B-5·B-6·B-8); 플립 PR이 파생물을 빠뜨리면 빨갛다 |
   | root `.env` | 호스트별 값(비밀·튜닝·digest·`KTDM_HOST`·`KTDM_PROD_URL_*`) | `targets validate`가 파생 기대값과 다른 재정의를 거부 |

**2026-10-15 T-MGR-02 구현 범위 정정.** 실제 registry 배치를 기준으로 각 호스트 shared location은3개씩이다.
아래 원계획의 빈 odroid/6개 default 가정과 Geo1·Weather2 일괄 조정은 현재 구현에 적용하지 않는다.
workspace는 선언된 N개 호스트에서 유도하고 default alias를 유지한다. dagster.yaml은 이번 단계에서 byte 불변이다.
사용자는 후속 실측을 근거로 튜닝 변경도 허용했으나 같은 수집량·freshness 비교 전에는 임의 병렬도 축소를 하지 않는다.
cutover는 root 환경을 별도 파싱하지 않고 기존 frozen/resolved mount source와 두 env digest를 그대로 사용한다.

6. **host 플립 = workspace 합류; `workspace.{n150,odroid}.yaml` 두 파일 커밋 + compose 보간.** 파생기(`runtime_topology.py`)에
   host 필터를 더해 `workspace.<host>.yaml` = `control_plane: shared` **이고** `target.host == host`인 location(host 값은 계속
   `127.0.0.1`, S7 불변). 별도 선언 필드 없음. T-MGR-02 직후 `workspace.odroid.yaml`은 `load_from: []`, `workspace.n150.yaml`은 오늘
   `workspace.yaml`(6 location: geo 12503·conc 12603·map 12703·pinvi 12803·weather 14106·transport 14005)과 **byte 동일**. compose의 두
   bind(`docker-compose.yml:832,902`)를 `./config/dagster-shared/workspace.${KTDM_HOST:-n150}.yaml:/opt/dagster/dagster_home/workspace.yaml:ro`
   로 — 컨테이너 안 경로가 그대로라 healthcheck digest 비교(`:847,911`)와 cutover의 컨테이너 내부 읽기(`scripts/dagster-shared-cutover.sh:424`)
   는 불변. 바뀌는 참조 4곳: cutover `:168,394,764`(호스트 파일 경로 — 셸이라 root `.env`의 `KTDM_HOST`를 읽고 미설정이면 n150),
   liveness 테스트 `backend/tests/test_dagster_daemon_liveness_contract.py:218`(`workspace.` 접두). digest env:
   `KOR_TRAVEL_DAGSTER_WORKSPACE_DIGEST: ${KTDM_DAGSTER_WORKSPACE_DIGEST:-8a86a75219b7938d}`(기본 = n150 파일; B-7; **2026-10-14 잠정 시행으로 대체**: 소비자 포트 geo `12501`·transport API gateway `12301`·weather `12201`, 집합 8곳(geo 5 — Map 4·pinvi-api 1, transport 1, weather 2 — PinVi ETL·pinvi-api), n150 workspace digest `fbd0eadc90829753`(weather·transport 제외 4 location → **2026-10-15 `4461abc13cce9c4f`, geo도 제외 3 location**) — ADR-58 "잠정 시행".). odroid root `.env`가
   `sha256sum config/dagster-shared/workspace.odroid.yaml | cut -c1-16`을 준다 — 플립 PR마다 바뀌므로 플립 절차에 "odroid Manager 같은
   sha 재설치 + `.env` digest 갱신 + `targets validate`" 단계를 둔다. **대안 "설치 시 렌더"(trusted installer가 workspace를 고정
   경로로 렌더, 참조 0곳 변경)는 기각** — ADR-51 "installer에 로직을 더하지 않는다"; 누락은 validate(사전)와 healthcheck exit
   (사후) 두 겹으로 드러나므로 보간이 더 단순하다. 플립 PR의 n150 비용: `workspace.n150.yaml`이 바뀌어 digest 기본값이 바뀌고
   n150 `ensure dagster`가 daemon/webserver를 재생성한다(tick 공백 ≈2 min) ×3; 코드서버는 재생성되지 않는다(workspace digest env는
   daemon·webserver 2곳뿐). 결박: `workspace.n150.yaml ∪ workspace.odroid.yaml == registry shared location 집합`(교집합 ∅, B-5);
   "합류 target이 있으면 plane이 `all`에"(`test_dagster_shared_workspace_is_derived.py:605-608`)는 호스트별 `all` 집합에 같은 규칙으로.
7. **`dagster.yaml`은 한 벌, location 상한 조정은 T-MGR-02에서 1회.** `max_concurrent_runs`·`tag_concurrency_limits[].limit`는
   dagster 1.13 instance 스키마에서 `Field(int)`라 env 파생이 불가하다. 호스트 보호는 `tag_concurrency_limits`의 **location 값**으로
   충분하다(`config/dagster-shared/dagster.yaml:57-94` — `dagster/code_location` 항목은 `:73-94`): geo **1**, weather **2**(n150 실측 매시 run p95 < 5 min일 때, 아니면 1),
   transport **3**(그대로), map/pinvi/concierge·`max_concurrent_runs: 12` 불변 — 이동 뒤 n150에는 그 location이 없어 무해하고
   odroid의 실효 상한은 1+2+3=6. **플립 PR마다 조정하지 않는다**: instance digest 리터럴 7곳(S12)과
   `backend/tests/test_dagster_shared_config.py`가 대조하므로 PR마다 바꾸면 n150 plane·코드서버 전부 재생성이 3회다. 한 번에 바꾸면
   1회. T-MGR-02에서 7곳 리터럴을 YAML anchor 하나(`x-ktdm-instance-digest`)로 합친다(권장). `max_runtime_seconds 21600`은 바꾸지
   않는다(geo 장기 job은 이미 `dagster/max_runtime` 24 h 태그 — `dagster.yaml:137` 주석의 태그 사용 테넌트 목록(PinVi·weather·transport)에
   geo가 빠져 있으니 T-MGR-02의 1회 수정에서 주석만 함께 고친다; `mv_refresh`만 geo T-GEO-02에서 결정). `legacy-dagster` profile은
   재활용하지 않는다. 두 plane은 같은 체크아웃·같은 `docker/dagster-host/Dockerfile`에서 **호스트별 arch로** 빌드한다(같은 sha).
   **2026-10-26 좁은 정정(호스트 상한 보존).** 위 단일 기본 YAML과 location 정책은 유지한다. 현재 n1506/Odroid5를 설치·후속 수렴이 보존하도록 `hosts.dagster_max_concurrent_runs`만 정본으로 추가한다. 같은 6이면 원 bytes/path/digest를 그대로 쓰며, 다른 5는 같은 YAML의 해당 정수만 바꾼 root 소유 내용 주소 파일을 기존 G 아래 명시 준비한다. 별도 수기 YAML·태그별 상한·PG/Geo 정책·부팅 마운트 조건을 추가하지 않는다. 모든 Manager consumer와 외부 공식 caller는 이 한 결과를 사용한다. 외부 caller가 Manager G 전체를 소유하는 것은 아니므로 alias 전환의 잔여 경합과 설치 선행 순서를 [기능 계약](host-dagster-instance-config.md)에 명시한다. 구현·순수 회귀와 운영 적용 수용은 구분한다.
8. **PG·메모리 정책.** (ⅰ) odroid 튜닝(env 파생, 기본값 = n150 `docker-compose.yml:265-291`의 현재 값): `shared_buffers 1GB`,
   `effective_cache_size 2GB`(n150 1536MB), `work_mem 16MB`(n150 64MB), `maintenance_work_mem 256MB`, `max_parallel_workers 2`/`_per_gather 1`,
   `effective_io_concurrency 200`(fio 뒤 확정), `checkpoint_timeout 900`, `wal_compression zstd`, `autovacuum_max_workers 2`;
   **`max_locks_per_transaction=256`은 env 파생이 아니라 두 호스트 같은 리터럴**이다(2026-10-13 #471 — 공유 lock table은 cluster 전역 한
   벌이고 크기는 그 instance에 붙는 테넌트의 가장 큰 트랜잭션이 정한다; 호스트마다 다르게 둘 근거가 없다. odroid의
   `autovacuum_max_workers 2`는 MaxBackends를 1 줄일 뿐이다, onboarding §6.1 C10) — 앞 목록 중 뒤의 여섯
   키는 n150 compose에 **없어서**(서버 기본값으로 돈다) env 파생 줄을 새로 넣으면 n150 렌더에 `-c` 줄이 생긴다: 기본값 = PG 16 서버
   기본값(8/2/1/300s/off/3)으로 효과 0, 결정 1 ⑦의 허용 diff. 대안 "odroid PGDATA에 `ALTER SYSTEM`(postgresql.auto.conf)로만 두기"는
   compose·`.env` 밖의 손 상태라 기각(단일 정본). `shm_size 1gb`·`init: true`·exec healthcheck(ADR-52) 동일. (ⅱ) **`mem_limit`는 호스트별 값이다 — odroid 값이고 n150은 현재 상태 그대로**(설계 §5.3: PG `mem_limit` n150 "없음" /
   odroid "4g(가드)"). odroid: PG **4g**(OOM 가드이지 하드 캡이 아니다 — 하드 캡 2.5g/3.5g은 §14-14 대안, 유휴 때 빈 RAM을 캐시로
   못 쓴다), geo cs **2g**·`pids_limit 1024`, weather cs **1.5g**(초과 시 배치 축소). n150: PG·geo cs 없음(`docker-compose.yml:254-279`·
   `:2442` 블록에 `mem_limit` 없음 — S19), weather cs 4g(`:2882`) 그대로. 자리는 R6대로 env 파생(`${KTDM_PG_MEM_LIMIT:-…}` 꼴, 기본값
   = n150 현재 상태와 **효과 동일** — "없음"을 기본값으로 표현하는 형태는 T-MGR-03이 정한다)이고 odroid root `.env`가 값을 준다.
   (ⅲ) **OOM 희생자 정책(리터럴, 두 호스트 동일)**: `oom_score_adj` PG **-800**, plane daemon/webserver **-600**, weather cs
   -500 유지, geo cs·transport cs 0 — 호스트 OOM이 PG backend보다 run worker를 먼저 고르게 한다(backend 하나가 죽어도 postmaster가 전
   backend를 재시작해 세 API·세 code-server·plane이 동시에 끊긴다). (ⅳ) **memswap 정책(리터럴 규칙, 두 호스트 동일)**: odroid는 cgroup v1
   swap accounting이 켜져 있어 Docker 기본 `--memory-swap`(= `mem_limit` 2배)이 실제로 적용된다 → **`mem_limit`가 있는 서비스마다
   `memswap_limit == mem_limit`**(같은 변수/값; PG·geo cs는 odroid에서만 limit이 생기고, weather cs는 n150 4g/odroid 1.5g 각각);
   n150(cgroup v2)에서는 `memory.swap.max=0`과 같은 뜻. 이것이 없으면 "limit 초과 = OOM = run FAILURE"가 성립하지 않고 swap을 먼저
   먹는다. (ⅴ) **transport code-server는 transport 저장소 compose의 서비스**(`docker-compose.shared.yml`; Manager compose에는 없다 —
   `config/docker-targets.yml:671-`의 외부 project)이므로 그 `mem_limit 1.0g`·`oom_score_adj 0`·`memswap_limit == mem_limit`는
   transport ADR-014의 몫이다 — Manager T-MGR-03은 PG·geo cs·weather cs(+ 같은 규칙에 걸리는 Manager compose의 다른 `mem_limit`
   서비스)만 다룬다. Manager systemd `MemoryMax=300M`. (ⅵ) swapfile
   4 GB `/mnt/media/kor-travel/swapfile`(NVMe, `dd`, `vm.swappiness=10`)만 — zram 없음; memswap 정책 때문에 swapfile은 limit 없는
   프로세스(OS·보존 서비스·Manager)만 흡수하는 **호스트 OOM 전 완충**이다. (ⅶ) geo 백업·restore drill은 odroid에서 **off**(재로드
   원칙과 일치; drill 2 h+/일 회피), weather 백업 role은 추가하지 않는다(S14), transport 3일 백업은 odroid cron으로 옮기는 것을
   추천(**§14-7 소유자 결정 전 추천값** — 대안 "아무것도 안 함").
9. **ARM64 이미지 — odroid 현지 빌드, 레지스트리 없음, 내용 해시 태그에 arch 없음.** 호스트는 실측 aarch64이므로 arm64는 조건부가
   아니라 필수다. n150에 binfmt가 없어 QEMU 교차 빌드는 쓰지 않고, 레지스트리는 호스트가 셋 이상일 때 재검토. 태그 규칙은
   n150과 동일(`latest-main`·`rel-<sha>`·내용 해시) — 두 호스트가 **같은 tag로 다른 arch 내용**을 갖는다. 이것은 **레지스트리가
   없을 때만 안전**하므로 레지스트리 도입의 선결 조건으로 기록한다. 기존 digest 핀은 제거하지 않고 odroid는 root `.env`·태그
   완화 + 효과 검사(`ensure` 프리플라이트가 이미지 `.Architecture` == 호스트 arch)로 간다. PG는 `imresamu/postgis:16-3.5`(공식
   `postgis/postgis`에 arm64가 없다; `pg_isready` 경로 동일 여부는 설치 때 확인). 남은 미검증은 이미지 **내용물**(base digest가
   manifest list인지, dagster-host 잠금본의 aarch64 wheel, Playwright arm64 Chromium)이다.
10. **SSH·비밀·offbox.** Manager 간 SSH 없음. 사람 접속은 WSL 직결 키 로그인(2026-10-13 실측 성공; n150 ProxyJump는 보조).
    **odroid 비밀번호 SSH off는 전제**(키만). root 작업은 NOPASSWD sudo(소유자 설치 완료). 호스트마다 root-0600 `.env` 하나,
    값 집합이 다르다(odroid: geo/weather/transport·odroid PG·plane 비밀, `KTDM_HOST=odroid`, PG 튜닝·이미지, workspace digest,
    호스트별 `KTDM_PROD_URL_DAGSTER/_GEO*/_WEATHER*`, `KOR_TRAVEL_DAGSTER_UI_PASSWORD`). 두 파일에 같은 값은 Map→geo API key,
    Map→transport service token 둘 — 동기화 기제 없이 효과(401/404)로 드러난다(B U-2). `offbox-sync`는 push 모델(S8) 그대로 두되
    **odroid→n150 push만**, n150 쪽 `authorized_keys`를 forced-command(`rrsync` 쓰기 전용 경로 `<backup root>/odroid/`)로 묶는다 —
    LAN에 열린 호스트가 n150(백업·pin registry의 집)에 들어가는 상시 키를 갖지 않게. n150→odroid push는 하지 않는다.
    **호스트 래퍼 스크립트의 정본 위치**: n150 홈의 `~/install-mgr.sh`·`~/preflight.sh`·`~/guarded-rb.sh`는 저장소 밖 호스트 사본이다
    (메모리 `manager-site-packages-lags-source`·`preflight-before-n150-mutations`). odroid에는 **같은 모델로 홈 사본**을 두는 것을
    추천한다(최소 변경; `install-mgr.sh`는 root clone + `scripts/install-ktdm-trusted-release` 호출이라 호스트 무관, `preflight.sh`는
    n150 특유의 검사(공용 plane·Map/PinVi)를 빼고 §9.5의 odroid 효과 검사로 바꾼 사본). 저장소 `scripts/`로 승격(두 호스트가 같은
    파일을 설치본에서 실행)은 대안 — 선택은 §14-30(이 ADR에서 추가). 어느 쪽이든 ADR-51대로 installer 자체에는 로직을 더하지 않는다.
11. **관측은 (b) n150 중앙 Prometheus + 1단계 federate.** odroid에는 exporter만(cAdvisor 12103, node_exporter 신설 — 포트는
    `docs/ports.md` 절차, 후보 12106; n150에도 node_exporter 추가). n150 `config/prometheus/prometheus.yml`은 **커밋된 파생물**
    (B-8): job `cadvisor-odroid`·`node-odroid`·`weather-code-server`(14103)·`ktdm-odroid`(12901)·`weather-federate`(odroid weather
    Prometheus 14104 `/federate`, `weather_*`+`ALERTS`). weather-api 14101 `/metrics`는 1단계에서 직접 scrape하지 않는다(bearer
    토큰 중복 회피) — weather Prometheus의 rule이 weather 체크아웃 bind(`config/docker-targets.yml:965-971`)라 n150 체크아웃을
    지우면 정본이 사라지므로 즉시 퇴역은 하지 않는다(2단계 퇴역 시점은 §14-11). Grafana 하나(n150), `labels: host=odroid`.
    rule: `count(up{host="odroid"}) == N ∧ 전부 1`(전부 0은 다운뿐 아니라 **DHCP 임대 변경** 의심), `node_filesystem_avail_bytes
    {mountpoint="/mnt/media"} < 100 GiB`·`{mountpoint="/"} < 10 GiB`, 시계 rule `node_timex_sync_status == 0 ∨ |node_time_seconds −
    timestamp| > 5`(federate 샘플이 odroid 타임스탬프를 달고 와 out-of-bounds로 조용히 버려지는 경우를 먼저 설명한다).
    Alertmanager는 두지 않는다.
    **NOTE(2026-10-16, T-MGR-NH):** 위 rule·federate job은 아직 `config/prometheus/`에 없다(파생된 것은 scrape 타깃뿐). 결정 10의
    offbox 방향과 이 결정의 "중앙 = n150"은 **오늘은 관례에 따른 역할**이다 — registry 필드가 없고, N-호스트 일반화에서도
    deferred다(`docs/multi-host.md` §5).
12. **Manager 범용성 원칙(소유자 반복 지시).** 호스트 이름·주소·odroid 특유의 값을 코드에 리터럴로 박지 않는다 — 주소는 registry
    `hosts:` 한 곳, 자기 식별은 `KTDM_HOST` 하나, 호스트 차이는 root `.env`의 env 파생(기본값 = n150 값)과 rendered 모델 파생,
    검증은 효과 기반 런타임 검사(IPv4 집합·`findmnt` 장치·이미지 arch·`docker compose config` 렌더 성공·컨테이너명 조회)로 한다.
    새 게이트는 ADR-50 "먼저 멈춘다"를 따른다 — 결박은 값이 아니라 규칙(R1~R7)을 고정하고, 파생물은 테스트가 registry에서 다시
    계산해 대조한다.
13. **이전 순서와 게이트.** transport → weather → geo, 각 target의 `host:` 플립은 **그 단계의 PR**(T-MGR-06 ×3)이고 플립 직전에
    n150의 그 code-server를 **먼저 멈춘다**(이중 수집 금지). **geo 플립 조건(이 결정이 정본; 설계 §3.2 조건 (3)에서 옮김)** — n150 geo
    무거운 run 중 anon ≤ 산식 상한(7.5 − 목표 여유 − 6.35; 여유 0.2면 **0.95 GiB**, 여유는 §14-12), swapfile 활성, cgroup 효과 검사
    PASS, 백업·drill off, weather 7일 정상 상태 — 을 전부 통과할 때만 옮기고, 미달이면 n150 잔류(혼합 배치 ≈6.19 GiB — 모델이 그대로
    수용). 절차(전체 순서표·target당 절차)·체크리스트·해체는 `docs/prod-deployment.md` §9.2·§9.5·§9.6.

### 근거

- **정확성·단일 정본**: 주소가 한 곳(`hosts:`)에만 있고 나머지는 파생이다. 파생을 런타임(connection)과 커밋된 파생물(compose·
  YAML)로 나누는 이유는 compose가 registry를 읽을 수 없다는 **사실**(S10)이지 취향이 아니다 — 커밋된 파생물은 결박 테스트가 다시
  계산해 대조하므로 플립 PR이 하나라도 빠뜨리면 CI에서 빨갛다.
- **단순성**: 새 런타임 기제는 host 차원 하나다. 원격 Docker·Manager 간 SSH·레지스트리·installer 렌더·호스트별 compose/dagster.yaml
  두 벌을 전부 두지 않았다. compose 하나 + `.env` 차이는 오늘의 운영 모델(`.env`가 호스트 값의 자리)과 같다.
- **보안(ADR-50 순서대로 단순성 뒤)**: loopback 규칙(gRPC·PG·webserver·RustFS bind)을 어느 호스트에서도 바꾸지 않는다. 경계는
  bind가 아니라 인증·CIDR·호스트 방화벽이다(`docs/ports.md` "호스트 규칙"). (b)를 기각한 결정적 이유도 새 신뢰 경계(무인증 gRPC
  LAN 노출)다.
- **장애 격리**: odroid 다운 ≠ n150 plane(두 plane 사이 통신 없음). 오늘은 피호출자가 같은 호스트라 다운 시 RST가 즉시 오지만
  전환 뒤엔 SYN이 **타임아웃까지** 간다(Map geo 호출당 10 s, PinVi weather card ≈30 s 블록) — consumer 쪽 connect timeout 분리·
  회로차단은 Map ADR-110·PinVi·concierge ADR-50의 몫(교차 저장소).
- **실측이 결정을 바꾼 것**: cgroup v1 swap accounting on → memswap 정책; Samba `[homes]` 쓰기 가능 → symlink 대신 fstab bind;
  `docker compose` v2 부재 → 설치 단계 + 렌더 효과 검사 + 이질 major면 렌더 테스트 두 major; DHCP → OPNsense 예약 추천 +
  `up` 전부 0 신호; 호스트 방화벽 없음 → 정책 (가) "우리 포트만 제한, 그 외 accept"(보존 서비스 UDP 리스너 열거 없음,
  `flush ruleset` 금지); timesyncd가 이번 부팅에서 큐잉되지 않은 상태 → "재기동"이 아니라 **부팅 순서 사이클 해소(drop-in)** 선행.

### 결과(긍정)

- 포트·URL·컨테이너명·project명·bind 경로가 두 호스트에서 동일하다 — 사람과 문서가 외울 것이 "호스트만 다르다" 하나다. odroid
  점유 포트(TCP+UDP 실측) × 우리 집합 충돌 0(`docs/ports.md` "호스트 규칙").
- n150 렌더는 T-MGR-01a~05 전체에서 **결정 1의 허용 diff 목록 ①~⑨**만 바뀐다 — PG·geo cs에 n150에서 실효 `mem_limit`는 생기지
  않는다(테스트가 diff를 계산한다). isolated plane 하네스·C6c 테스트는 `KTDM_HOST` 미설정(비운영)에서 **무변경 통과**.
- n150이 디스크 ≈183 GB·메모리 피크·CPU를 돌려받고 공용 PG 13 DB → 9, Map/PinVi/concierge 코드서버의 디스크 대기 timeout 사망이
  줄어든다(가정 — 효과는 관측으로).

### 결과(부정)

- UI(Dagster webserver·Manager 대시보드)가 둘, root `.env`가 둘(교차 비밀 2개는 결박 없이 효과로만 드러난다 — B U-2), `ensure
  dagster`가 두 호스트에서 각각 돈다. 플립 PR 3건마다 n150 plane 재생성(≈2 min tick 공백)과 odroid Manager 재설치가 따른다.
- odroid 최악 동시 창은 RAM을 넘는다(여유 −0.35 GiB) — memswap 정책이 run FAILURE로 받고 재실행에 맡긴다. geo는 조건 미달 시
  n150에 남을 수 있다(혼합 배치).
- 같은 tag가 두 호스트에서 다른 arch 내용을 갖는다(레지스트리 도입 선결 조건). `imresamu/postgis`는 제3자 이미지다.
- geo·weather·transport의 n150 이력(highway 28 GB 등)은 보존하지 않는다(소유자 비범위). geo full load는 하루 창(12 h+ 추정)이고
  새 `data_token`이 Track 1 전량 재지오코딩(≈2.5 h+ 추정)을 유발한다(아래 "관계").
- 두 plane의 dagster 버전 차이는 허용하지만(통신 없음) 운영자가 두 UI를 본다.

### 관계 — Track 1(geo 주소 갱신 대응)과의 상호작용

Track 1(geo ADR-069·Map ADR-109·concierge ADR-49·transport T-045)은 Map·concierge Dagster 센서가 ≤5분마다 geo `POST /v2/dataset/version`을
폴링하고 릴리스마다 전 장소를 `GEO_HEAVY_POOL`(instance 전역 1) 직렬로 reverse 재호출한다. Track 2 뒤 그 호출은 전부 LAN
(RTT ≈10 ms)·공개 키 경로(loopback 신뢰 peer 면제 소멸)이며, **odroid 빈 호스트 full load + mv_refresh는 새 `data_token`을 내므로
geo 플립 9단계에서 Map/concierge URL을 `.13`으로 돌리는 순간 전량 재지오코딩이 걸린다**(≈2.5 h+ 추정; n150 dump 복원 지름길이면
토큰이 같아 0). 추천은 수용(새 소스로 적재했으면 재지오코딩이 정확성상 옳다)하되 9단계를 mv_refresh 완료 **다음 날** geo 야간 job
창 밖에서 실행하고 운영자 UI skip은 쓰지 않는다(§14-19). `GEO_HEAVY_POOL` 유지 근거는 "n150 디스크 대기"에서 **"odroid CPU
보호"**로 바뀐다(Map ADR-110에 기록). 센서 LAN 폴링은 새 inbound 규칙이 필요 없다(consumer/공개 API 집합 12501이 n150 addr를 허용).

### 바뀌는 테스트(구현 PR에서)

- 신규(이름은 각 T에서 확정): registry `hosts:` 로더(default 0·2개 거부, `addr` IPv4, unknown host 거부, `host:` 기본·`all`), R1 렌더
  (all 컨테이너 항상 loopback), R3(운영 모드 거부·빈 env 거부·비운영 통과), R4(생성 거부·stop 허용), R7(교차 호스트 간선 ∅ — 간선
  하나 일부러 남겨 빨갛게), workspace 호스트별 파생(합집합 == shared 집합, n150 파일 byte 동일), digest 기본값 == n150 파일,
  `targets validate` R2·digest 효과·`--json`, R2 커밋 파생물 == registry 계산값(B-6), Prometheus 타깃 == registry 계산값(B-8),
  `ensure` 프리플라이트(amd64 이미지 + arm64 가짜 호스트 → 거부; 장치 env 설정 + 다른 SOURCE → 거부, 미설정 → 통과; compose 플러그인
  부재 fixture → 거부), memswap("mem_limit 있는 서비스마다 memswap_limit == mem_limit" — 하나 빼면 빨강).
- 기존 변경: `backend/tests/test_dagster_shared_workspace_is_derived.py`(두 파일 + 호스트별 `all` 규칙),
  `backend/tests/test_transport_shared_bootstrap_contract.py:23`(이미지 == compose 기본값), `backend/tests/test_dagster_shared_config.py`
  (workspace digest env 기본값 형태 + instance digest 1회 갱신), `backend/tests/test_dagster_daemon_liveness_contract.py:218`(bind 문자열).
  불변: cutover 로직, C6c 테스트. isolated plane: `KTDM_HOST` 미설정 → default host로 무변경 통과 + odroid 프로필(3 location) 하네스
  한 번 더(두 프로필 합 6).
- 거짓 안심 금지: 거부는 **효과**(non-zero + 메시지)로 단언하고, 새 검사는 일부러 한 번 빨갛게 만든다.

### 열린 결정(설계 §14 번호 — 소유자)

2026-10-13 소유자 처리로 **닫힌 것**: §14-24 root 권한 방식(NOPASSWD sudo 설치 — 설계 권고 ⅰ; 설계 §11.1-3(c)가 권하지 않던
docker 그룹 추가도 함께 됐다 — Manager는 root systemd로 돌므로 운영 경로에는 영향 없음, 기록만), §14-5 중 접속 경로(WSL 직결 키
로그인 성립 — ProxyJump 불필요), §14-1 중 비root 선행 실측(arch·RAM·디스크·cgroup·Docker·시계·상주 서비스), §14-25 중 "동기화
자체"(됐다 — 남은 것은 부팅 순서 사이클 영속화 방식). **반쪽 상태 하나**: 설계 §14-24 ⅰ은 NOPASSWD를 "비밀번호 SSH off와 묶어서"
권했는데 NOPASSWD:ALL·docker 그룹은 됐고 `PasswordAuthentication no`(`prod-deployment.md` §9.1-3)는 아직이다 — 그때까지 LAN에서
비밀번호로 들어오면 곧바로 무비밀번호 root가 되는 창이 열려 있으니 §9.1-3은 시계와 무관한 단계라 **0 다음 바로** 한다.

**열린 것**(추천을 함께 적는다): §14-1 n150 선행 실측(geo cs·PG anon, weather hot set·매시 run p95, mv_refresh 소요 — 반나절) ·
§14-2 폐기 범위(parking-radar·Portainer만, Plex 정의 보관·Portainer 볼륨 유지·`prune` 금지; Plex·Samba·webvnc·8080·1994 보존) ·
§14-3 Dagster (a) 승인/(b) 기각 · §14-4 odroid plane 공개 host 이름(`weather-dagster.` 재사용 vs 새 이름 — `KTDM_PROD_URL_DAGSTER`
선행) · §14-5 비밀번호 SSH off·offbox forced-command·12905 허용 소스 · §14-6 odroid 현지 빌드 승인 + HAProxy LAN 주소 · §14-7 백업
대상(transport 3일만) · §14-8 geo ZIP 재확보·serving DB 직접 full load·dump 복원 지름길 허용 여부 · §14-9 Manager 로컬 체크아웃을
origin/main으로(선행 조건) · §14-10 Map UI BFF 경로(공개 host 유지 추천) · §14-11 관측 (b) 승인 + weather Prometheus 2단계 퇴역
시점 · §14-12 이전 순서와 geo 보류 조건의 **목표 여유**(추천 0.2 → anon ≤ 0.95 GiB) · §14-13 수집 공백 수용 · §14-14 PG 가드 4g
vs 하드 캡 · §14-15 swapfile 4 GB만 · §14-16 중단선(cgroup 효과 검사 실패 또는 NTP 사이클 해소 drop-in 불가/재부팅 2회 실패면
전체 보류; systemd 245 핀 상태의 Manager 단위 `MemoryMax`·`After=time-sync.target` 효과 포함) · §14-17 weather `RETENTION_AHEAD_DAYS`
· §14-18 `dagster.yaml` 한 벌 + 상한 1회(추천) · §14-19 Track 1 재계획 수용 · §14-20 odroid IP 고정(OPNsense DHCP 예약 추천) ·
§14-21 geo 공개 키 정책(VWorld 공용 키 폴백 유지 추천 — DB에 키가 하나라도 발급되면 폴백이 꺼지는 all-or-nothing) · §14-22
Docker data-root(B안: eMMC 유지 + PG·데이터 bind만 NVMe, 추천) · §14-23 Plex 공존 예산(전환·geo 야간 창에 트랜스코딩 없음,
Plex 라이브러리에서 `/mnt/media/kor-travel` 제외, 용량 상한) · §14-25 NTP 영속화 방식(timesyncd 유지 + 보존 unit drop-in으로
사이클만 끊기, 추천) · §14-26 compose major(Ubuntu `docker-compose-v2` 2.40 + `docker-buildx` 추천 vs docker.com 5.x — 이질 major면
렌더 테스트 두 major) · §14-27 systemd 245 핀 유지(추천) vs noble 정합 · §14-28 방화벽 정책 (가) 추천.

**이 ADR에서 추가한 열린 결정(설계 §14에 없음 — 1차 리뷰 2026-10-13):**
- **§14-29 odroid Manager 대시보드(`12905`) 유무.** 설계 §4.1·§4.3은 odroid에 dashboard `12905`를 두고 허용 소스를 "n150 +
  운영자 PC"로 적지만, prod 빌드 대시보드는 브라우저에서 `NEXT_PUBLIC_BACKEND_URL`(공개 host `manager-api.*`)로 API를 부르고
  (`docs/prod-deployment.md` §4·§5), 같은 표가 `12901`을 "n150 addr만"으로 닫고 HAProxy 전환표에 odroid용 `manager`/`manager-api` host가
  없어 **그 대시보드는 자기 API에 닿지 못한다**. 선택: **(가) odroid에는 backend만**(frontend 설치 없음; 운영은 `ktdctl`과 n150 Prometheus
  의 `ktdm-odroid` scrape, 대시보드는 n150 것 하나 — **추천**, 단순성) / (나) 대시보드를 두면 `12901` 허용 소스에 운영자 PC(또는
  HAProxy) 추가 + odroid 빌드용 `NEXT_PUBLIC_BACKEND_URL`·공개 host 2개(§9.4 행 추가). 결정 전까지 `ports.md`·`platform-topology.md`
  §10.1의 `12905` 행은 "(§14-29)" 조건부다.
- **§14-30 호스트 래퍼 스크립트 정본 위치**(결정 10): odroid 홈 사본(추천) vs 저장소 `scripts/` 승격.

### 후속

- T-MGR-01a~d·02~05(기제), T-MGR-06 ×3(플립 PR), T-MGR-07(문서 — 이 ADR과 `docs/*` 반영은 이 PR; `.env.example` 주석은 코드 T에서),
  T-OPS-01~03 — `docs/tasks.md`. 전부 origin/main 최신(≥ `094d397` — 이 ADR의 인용 줄 번호 기준)에서 브랜치한다(로컬 stale
  체크아웃 사용 금지; 설계의 "`08a515e` 이후"는 그 다음 커밋이 `094d397`이라 같은 뜻이다).
- (open) 실측 뒤에만 확정되는 값: `effective_io_concurrency`·`wal_compression`(fio·sysbench), weather location 상한 2 vs 1, geo
  `mv_refresh` 상한, cAdvisor per-container series 유무(cgroup v1), `imresamu` `pg_isready` 경로, base digest의 manifest list 여부,
  dagster-host 잠금본 aarch64 wheel, Playwright arm64 Chromium.
- (open) 이 ADR이 accepted가 되는 시점 = T-MGR-01a~05 머지 + n150 설치(렌더 변경이 **결정 1의 허용 diff 목록 ①~⑨** 안임을
  확인). 플립 PR이 끝나고 `docs/prod-deployment.md` §9.6
  해체까지 되면 ADR-47·ADR-54의 "범위" 문구와 `docs/platform-topology.md` §4·§5 현황 표를 갱신한다.

### 잠정 시행(2026-10-14 — 소유자 승인, T-MGR-01a~02 앞당김의 부분집합)

소유자가 geo·weather·transport를 odroid로 먼저 옮기고(n150의 weather·transport 컨테이너는 멈춤, 수집은 odroid 자체
Dagster) odroid의 포트를 **공개 대역으로 통일**했다 — geo `12501`(불변), weather `12201`(n150의 `14101`이 아니다), transport는
backend `14001`이 아니라 **API gateway `12301`**(gateway가 `/v1/service/exports/*`를 `192.168.1.14`에만 연다). 이로써 위
"포트를 하나도 바꾸지 않는다"(결정 1·`docs/ports.md` 호스트 규칙)는 이 세 소비자 origin에 한해 소유자 결정으로 대체됐다.
n150에서 깨진 둘을 고치는 최소 변경만 먼저 넣는다(계획 모델이 나중에 그대로 흡수하는 모양):

- **`hosts:` + target `host:`**(결정 1의 부분집합): `config/docker-targets.yml`에 `n150`(default)·`odroid`, weather·transport에
  `host: odroid`. 로더가 default 정확히 하나·IPv4 `addr`·알려진 `host:`를 fail-close로 본다. R1 렌더·R3 `KTDM_HOST`·R4 명령
  규칙·`all` 필터·`targets validate --json`은 **없다**(T-MGR-01a·01b) — 그래서 n150에서 `ensure weather`는 여전히
  weather 컨테이너를 n150에 되살릴 수 있다(`ensure all`은 아래 제외로 막힘; 운영자가 하지 않는다).
- **workspace 호스트 필터**(결정 6의 부분집합): 파생 workspace = `control_plane: shared` ∧ target이 default 호스트(n150).
  파일은 오늘처럼 `workspace.yaml` 하나(n150 plane만 붙인다) — weather `14106`·transport `14005`가 빠져 n150 plane healthcheck
  (모든 location SERVING)가 다시 초록이 된다. `workspace.{n150,odroid}.yaml` 두 파일·`${KTDM_HOST}` bind·digest env 파생은
  T-MGR-02 그대로 남는다. `dagster.yaml` location 상한은 불변(결정 7 — 없는 location의 상한은 무해).
- **R2 소비자 기본값(8곳)**: Map geo 4곳 `http://192.168.1.13:12501`, transport `http://192.168.1.13:12301`, PinVi ETL weather를
  리터럴에서 `${PINVI_KOR_TRAVEL_WEATHER_BASE_URL:-http://192.168.1.13:12201}`로, pinvi-api에 같은 변수 추가(S17), 그리고
  pinvi-api에 `PINVI_KOR_TRAVEL_GEO_BASE_URL`(odroid geo, 소유자 "pinvi geo 연동해")·`PINVI_KOR_TRAVEL_GEO_API_KEY`(빈 기본값 —
  odroid geo `ops.public_api_keys` 전용 키는 root `.env`, PinVi 쪽 짝 PR이 설정을 더한다).
  결박은 렌더 스캔(`backend/tests/test_consumer_origin_defaults.py`) — 형제 → 소비자 포트 표만 테스트에 있고 호스트는
  registry `host:`에서 파생한다. ~~geo는 소비자가 호스트보다 앞선다~~ — **2026-10-15 geo도 `host: odroid`**(아래 "geo
  workspace 제외")라 테스트의 geo 재정의는 없어졌다. 포트 표를 R1 계산으로 바꾸는 것·`.env` 재정의 거부는 T-MGR-01c.
- **`all`에서 weather 제외**: weather에 `excluded_from_all: true`, n150 `all.include`에서 뺐다 — n150 `ensure all`이 지운 DB로
  weather를 다시 세우지 않게. target 자체(상태·로그)는 남는다. `hosts:`에 `all`은 예약어(infra `host: all`)라 로더가 거부한다.
- **T-MGR-NH가 없애야 할 2-호스트 가정**: (ⅰ) workspace 파생의 "default 호스트 = 이 plane" 필터(파일 하나, n150 고정),
  (ⅱ) ~~소비자 origin 테스트의 geo 호스트 재정의 표~~(2026-10-15 제거됨), (ⅲ) weather의 `excluded_from_all`(호스트별 `all` 필터로 대체),
  (ⅳ) 소비자 포트 표(호스트별 포트가 다르다는 사실을 registry가 갖게 — T-MGR-01c와 함께), (ⅴ) 소비자 origin 테스트의
  "consumer는 default 호스트에서 돈다" 가정(`_expected_origin`이 default → `127.0.0.1`), (ⅵ) Prometheus odroid job 주소
  리터럴(#474, T-MGR-04).
- **geo workspace 제외(2026-10-15 소유자 결정)**: n150 geo code-server(`127.0.0.1:12503`)를 멈췄더니 n150 공용 daemon·webserver가
  "code servers not serving: ['kortravelgeo_dagster.definitions']"로 unhealthy가 됐다(운영자 preflight 차단). geo target에
  `host: odroid` → 같은 파생 규칙으로 n150 `workspace.yaml`에서 빠지고(남는 것 concierge·map·pinvi 3 location, digest
  `4461abc13cce9c4f`), 소비자 origin 테스트의 geo 재정의가 군더더기로 빨개져 지웠다. **geo Dagster는 2026-10-15부터 odroid의 plane에서 돈다**(schedule·sensor는 현재 STOPPED — 켜는 시점은 odroid 부하를 보고 정한다)(n150 code-server 02:42Z 정지, odroid `127.0.0.1:12503`이 odroid
  자체 workspace에 02:52Z 합류 — Manager 밖, 호스트 측 구성; Manager가 그 workspace를 파생하는 것은 T-MGR-02). geo는 `all.include`에 남긴다 — `map`이 `depends_on: [geo]`라 `ensure all`·`ensure map`
  폐포가 어차피 geo를 담는다(그 간선 삭제는 R7, T-MGR-01b; 그 전까지 n150 `ensure map`은 n150 geo 컨테이너를 되살린다 —
  workspace에는 없으므로 이중 적재는 없다). Map/PinVi pinned 수렴(`wanted`)은 map·pinvi location만 보고, fence·소유자 파생
  (`installed_location_owners`)은 workspace에 있는 location만 검사하므로 geo가 빠져도 바뀌지 않는다.

---

## ADR-59: Map의 Valkey(`kor-travel-map-valkey`)를 Manager가 n150에 띄운다 — loopback 전용, `.env` 비밀번호, 백업 없음

- 상태: accepted
- 날짜: 2026-10-23
- 결정자: 소유자(방향 — Map ADR-114 이행 2단계), Claude(구현)

### 컨텍스트

Map ADR-114(2026-10-23)는 weather·transport의 현재·예보 상태(와 weather 하루 세 구간 요약 14일)를 Map이 PostgreSQL이
아니라 메모리 DB(Valkey)에 들게 했다. 쓰는 것은 Map Dagster run worker(code-server의 자식)이고 읽는 것은 Map API다 — 두
프로세스가 같이 보는 저장소가 필요하다. 이행 순서 2번이 "Manager: Valkey 서비스 추가(n150)"다. 규모는 예상 0.2~0.5 GB,
원천(weather·transport)에서 다시 받을 수 있는 데이터다.

### 결정

`kor-travel-map-valkey`(Valkey 9.0, BSD)를 Manager compose의 `map` target 서비스로 둔다.

1. **접근.** host network에서 `--bind 127.0.0.1 --port 12704`(Map 대역의 빈 자리, `docs/ports.md`). `ports:`는 두지 않는다.
   접속은 `requirepass`로 막는다. URL을 받는 것은 Map API와 Map Dagster code-server 둘뿐이다.
2. **비밀번호.** root `.env`의 `KOR_TRAVEL_MAP_VALKEY_PASSWORD` 하나가 원천이다(`:?` — 없으면 compose 렌더가 실패하고,
   설치기가 flip 전에 그것을 본다). compose 최상위 `x-map-valkey-url` 앵커가 그 값으로
   `KOR_TRAVEL_MAP_VALKEY_URL=redis://:<pw>@127.0.0.1:12704/0`을 만들고 두 소비자가 그 앵커를 참조한다. Valkey에는
   `VALKEYCLI_AUTH` env 하나로 들어가고, 기동 스크립트가 그것을 **stdin 설정**(`valkey-server -`)으로 넘긴다 — argv에는
   남지 않는다(n150 `/proc`은 hidepid가 없어 호스트의 누구나 argv를 읽는다). 값은 URI unreserved 문자만 받는다(URL에
   그대로 들어가므로 — 공용 Dagster app 비밀번호와 같은 규칙). 어기면 Valkey가 기동을 거부한다(exit 64). 기동
   스크립트는 `exec` 전에 `VALKEYCLI_AUTH`를 지워 서버 프로세스의 environ에도 남기지 않는다.
3. **메모리·지속성.** `maxmemory 1gb` + `noeviction`(넘치면 조용히 지우지 않고 쓰기를 실패시킨다), `mem_limit`·
   `memswap_limit` `1536m`(fork copy-on-write 여유, swap 없음). RDB(`save 3600 1 300 100 60 10000`) + AOF
   `appendfsync everysec`. 데이터는 project 범위 internal named volume `kor-travel-map-valkey-data`(MOIS 원천과 같은
   규칙 — `name`·`external` 없음, 격리 스택은 자기 volume을 갖는다)에 둔다. host bind로 두지 않은 이유: C6c 후보 검증은
   **모든** 서비스의 bind source가 이미 있어야 통과하므로, bind로 두면 운영자가 디렉터리를 만들기 전까지 n150의 모든
   C6c 경유 변경(재구축 포함)이 실패한다. n150은 디스크가 하나라 Docker data root가 곧 데이터 디스크다(Valkey를
   eMMC·NVMe가 나뉜 호스트로 옮기면 다시 본다). `stop_grace_period: 60s`로 마지막 fsync·RDB 저장을 기다린다.
4. **프로세스.** 이미지 entrypoint(`docker-entrypoint.sh` — `/data` chown, `setpriv`로 uid 999)를 그대로 거친다. 이미지
   PID 1이던 tini 대신 `init: true`, healthcheck는 exec 형식 `valkey-cli -e -h 127.0.0.1 -p 12704 ping`(ADR-52). `-e`가
   없으면 `valkey-cli`는 NOAUTH·LOADING 오류 응답에도 0으로 끝난다(2026-10-23 n150 실측).
5. **순서.** Map API와 code-server는 공용 PostgreSQL과 같은 모양으로 `depends_on: {kor-travel-map-valkey: service_healthy}`다.
   `ensure map`은 의존 폐포로 Valkey를 먼저 올린다. pinned 재구축·같은 pair 수렴은 slot을 `up --no-deps`로 띄워 의존성을
   끌어오지 않으므로, Map·PinVi target의 `runtime_services` 가운데 slot·companion이 아닌 서비스(오늘은 Valkey 하나 —
   `runtime_topology.derive_pinned_infrastructure`가 targets에서 파생, 이름을 외우지 않는다)를 **무엇이든 멈추기 전에**
   `up -d --no-deps --wait`로 올린다. 자리는 같은 pair 수렴 직전, 그리고 전체 경로의 읽기 전용 거부 판정(DB 교체·
   `--restart` R2·S1·격리 전제) **뒤**·`deploy-status.json`의 `in_progress` 기록 **앞**이다 — Valkey가 healthy가 되지 못하면 떠 있던 Map·PinVi와 배포 기록을 건드리지 않은 채 재구축이 끝난다(전체 경로의
   실패 정리 `stop`은 그 뒤 `try` 안에만 있다). 공용 PostgreSQL과 다른 점은 이것이 그 target의 소유라는 것뿐이다 —
   재구축은 Valkey를 멈추지 않는다. n150은 `rehearsal/rebuildable`이라 일반 `ensure`가 거부되므로 첫 기동은 다음
   재구축(또는 같은 pair 수렴)이 한다.
6. **백업 없음.** `ktdctl db-backup`·standalone backup 어디에도 넣지 않는다. 잃으면 Map 적재 job이 다시 채운다.
   `rebuild-pinned --restart`(세 DB를 새로 만든다)도 Valkey는 비우지 않는다 — 원천에서 받은 현재·예보 상태라 Map DB와
   맞출 것이 없고, 다음 적재가 세대를 교체한다. 비워야 하면 named volume을 지우고(`docker volume rm
   kor-travel-docker-manager_kor-travel-map-valkey-data`) 다음 재구축이 빈 Valkey를 올리게 한다.
7. **이미지.** `valkey/valkey:9.0-alpine@sha256:187679e3…`(multi-arch index — amd64·arm64가 같은 digest라 호스트별 이미지
   env가 필요 없다).

### 근거

- 공용 PostgreSQL과 같은 접근 모양(loopback + 비밀번호)이라 새 네트워크 모델을 들이지 않는다. Valkey 전용 bridge
  network는 host network인 Map 서비스가 붙을 수 없다.
- docker `secrets:`(파일)로 넘기면 Valkey 컨테이너의 env에서는 빠지지만, Map 쪽은 어차피 URL env로 받으므로(Map의 PG DSN과
  같은 노출 수준) 얻는 것이 작고 기동 스크립트·healthcheck가 파일을 읽어야 해 복잡해진다(ADR-50).
- 새 검사기·게이트는 더하지 않았다. 재구축에 더한 것은 배포 단계 하나(위 5)이고 대상은 targets에서 파생한다. 결박은 렌더된 모델 테스트 하나(`backend/tests/test_map_valkey_service.py` — 소비자는
  `KOR_TRAVEL_MAP_VALKEY_URL`을 받는 서비스로 파생, URL의 host·port·비밀번호를 Valkey의 `--bind`·`--port`·healthcheck·
  `VALKEYCLI_AUTH`와 대조, 기동 스크립트를 sh로 실제 실행해 거부·stdin 경로 확인)뿐이다.

### 결과(긍정)

- Map ADR-114 3단계(적재 job·REST)가 기댈 저장소가 생긴다. URL env 이름은 Map이 읽을 이름으로 이 PR에서 정한다.

### 결과(부정)

- 새 필수 `.env` 키가 생긴다 — 없으면 Manager의 **모든** compose 명령이 실패한다(설치기가 flip 전에 거부하므로 설치는
  일어나지 않는다). 운영자는 설치 전에 키를 넣는다.
- 비밀번호가 Valkey 컨테이너 env(`VALKEYCLI_AUTH`)와 Map 두 컨테이너 env(URL)에 보인다 — `docker inspect` 권한자에게는
  보인다(Map PG DSN과 같은 수준).
- 운영할 컨테이너가 하나 는다. Valkey가 healthy가 되지 못하면 `ensure map`도, pinned 재구축·수렴도 실패한다(재구축은
  아무것도 멈추기 전에 실패한다 — 떠 있던 Map·PinVi는 그대로다). 비밀번호 문자 규칙은 Valkey 기동에서만 보므로 base64
  비밀번호는 설치는 통과하고 다음 재구축에서 실패한다(값 검사기를 더하지 않았다 — ADR-50; `.env.example`이 hex를 적는다).
- `maxmemory`가 1 GB라 Map이 새 세대를 다 쓴 뒤 옛 세대를 지우는 동안 순간 사용량이 데이터의 두 배가 된다(예상 0.2~0.5 GB면
  여유). 그 위에 RDB·AOF rewrite fork의 copy-on-write가 얹혀 `mem_limit` 1536m에 다가갈 수 있다 — Map ADR-114 3단계 키
  설계가 세대 크기를 이 안에 둔다.

### 후속

- (open) Map ADR-114 3단계에서 Map이 `KOR_TRAVEL_MAP_VALKEY_URL`을 읽는다. 이름이 바뀌면 이 앵커를 같이 고친다.
- (open) M05 격리 스택(bridge network)은 Valkey를 아직 싣지 않는다 — Map이 Valkey 없이 기동 실패하게 되면(ADR-114 §4는
  해당 엔드포인트만 503) 그때 격리 스택에 더한다.
