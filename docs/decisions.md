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

파일이 에이전트 read 한도(256 KB)를 넘어 2026-10-13에 **ADR-1~40**을 [`decisions-archive-01.md`](decisions-archive-01.md)로
옮겼다(본문은 byte 그대로, 번호·제목·상태 불변 — 역사는 다시 쓰지 않는다). 이 파일에는 ADR-41부터 있다. 번호는 계속 이어진다.
아카이브된 ADR을 인용할 때도 번호만 적는다(`ADR-37`처럼) — 어느 파일에 있는지는 아래 표가 말한다.

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

---

## ADR-41: host mutation lease는 부팅 시점에 만들고, 상호배제는 자기 자신이 보증한다

- 상태: accepted (두 번째 lease `pinned-runtime-rebuild.lock`과 비운영 lock 경로의 process env
  보충은 ADR-51 C-3으로 2026-09-27 superseded — 결정 1·2와 NONROOT-BACKEND 후속은 유지.
  아래 "두 lease의 계약이 어긋난다"는 lease가 G 하나가 되어 더는 성립하지 않고, 교훈 문단의
  괄호 안 예외도 사라졌다: lock 경로는 모든 모드에서 `.env` 값만으로 정한다)
- 날짜: 2026-08-28
- 결정자: agent (오너 승인 — root 권한 축소 후속)

### 컨텍스트

`KTDM_DEPLOYMENT_ENVIRONMENT=production`에서 모든 Compose mutation은
`/run/lock/kor-travel-docker-manager/global-mutation.lock` 하나를 지난다. 이 디렉터리를
Manager가 **런타임에 처음 만들고** 있었고, 부모인 `/run/lock`은 Debian 계열에서 `1777`
sticky다(n150 실측 `drwxrwxrwt root:root`, tmpfs). 즉 재부팅마다 창이 다시 열리고, 그 사이
비특권 로컬 사용자가 같은 이름을 선점하면 소유자·mode 검증이 실패해 **모든 컨테이너
mutation이 다음 재부팅까지 거부**된다. sticky bit 때문에 정리도 root만 할 수 있다.

두 번째로, `c6c_deployment_lock`은 `os.open` → `_validate_c6c_lock_fd` → `flock` 순으로
동작하면서 **획득 뒤 경로를 다시 대조하지 않았다.** `flock`은 경로가 아니라 inode 단위이므로,
그 사이 경로가 새 inode로 갈아끼워지면 두 주체가 서로 다른 inode를 잠근 채 각자 상호배제를
얻었다고 믿는다. 같은 파일의 `_verified_inherited_global_mutation_lock_fd`는 이미 `(st_dev,
st_ino)`를 대조하고 있었다 — idiom은 있었고 일반 경로에만 빠져 있었다.

### 결정

1. lease 디렉터리는 `systemd-tmpfiles`가 early boot에 만든다
   (`deploy/tmpfiles.d/kor-travel-docker-manager.conf`), 그리고 **그 유닛의 설치는
   `scripts/install-ktdm-trusted-release`가 책임진다.**
2. `flock` 획득 직후 fd의 `(st_dev, st_ino)`를 경로의 것과 대조하고, 불일치·부재는
   fail-close한다.

lease 소유자 계약은 그대로 root(`uid 0`, `0700` 디렉터리, `0600`/`nlink==1` lock 파일)다.

### 근거

- **선점 창은 시점의 문제이지 검증의 문제가 아니다.** 런타임 검증을 아무리 조여도 "먼저
  만든 쪽이 이긴다"는 성질은 남는다. tmpfiles의 `d` 타입은 early boot(`sysinit.target`
  이전)에 돌고 기존 디렉터리의 소유자·mode까지 바로잡으므로, 경쟁할 사용자 프로세스가
  아직 없다.
- **문서에 적힌 절차는 배달된 것이 아니다.** 최초 설계는 운영자가 `sudo install` 두 줄을
  기억해 실행하는 것이었다. 그러면 창은 "사람이 잊지 않는 한" 닫힌다 — 즉 닫히지 않는다.
  installer가 수행하고, 실패하면 release는 유지한 채 경고로 보고한다.
- **`d`는 내용을 지우지 않는다.** 이미 선점된 호스트에서 `--create`는 디렉터리를 깨끗하게
  보이게 만들지만 심어 둔 파일은 남는다. installer가 root 아닌 항목을 찾아 보고하고,
  런북의 확인 명령을 `ls -ld`가 아니라 `ls -la`로 둔다.
- **획득 뒤 재대조는 창을 좁힌다 — 닫지는 못한다.** 정확히 적어 둔다: `open`과 `flock`
  사이의 바꿔치기는 잡지만, 두 주체가 **각자 자기 시점에** 통과한 뒤 서로 다른 inode를
  잡고 있는 상태는 시점 검사로 잡히지 않는다. 상호배제 자체는 여전히 "lease 디렉터리에
  아무도 쓸 수 없다"(`0700 root:root`)를 전제로 성립한다. 그래도 넣는 이유는, 그 전제가
  코드 밖에 있고 전제가 바뀌는 순간(예: 서비스 계정과의 디렉터리 공유) 깨진 결과가
  "두 주체가 동시에 일곱 runtime을 만진다"이기 때문이다. 신원 대조와 함께 소유권·mode
  계약(`_validate_c6c_lock_fd`)도 다시 확인해, 잠그는 사이에 mode가 넓어진 inode를
  들고 임계 구역에 들어가지 않게 한다.
  `NONROOT-BACKEND`는 이 전제를 **유지한 채로**(소유자는 root, 접근만 그룹) 설계해야 한다.

### 기각한 설계: `KTDM_SERVICE_USER` 환경변수 seam

같은 작업에서 비-root 백엔드를 가능하게 하려고 "lease 소유자로 root와 선언된 계정을 함께
인정한다"를 구현했다가 **적대 리뷰에서 기각했다.** 기록해 둔다.

- **자기 증명이라 계약이 되지 못한다.** `configured_service_uid()`는 `os.environ`만 읽었다.
  백엔드 전체에서 `load_dotenv`는 `main.py`의 한 곳뿐이고 기본값 `override=False`라 프로세스
  env가 root 소유 `.env`를 이긴다. `cli.py`는 `.env`를 아예 읽지 않는다. 따라서 uid *U*는
  `KTDM_SERVICE_USER=U`를 export하는 것만으로 두 검사를 모두 통과한다 — `euid == euid`.
- **문서대로 하면 운영이 멈춘다.** `sudo`는 Debian 기본 `env_reset`으로 `KTDM_*`를 벗긴다.
  `.env`에 계정을 선언하고 tmpfiles UID를 그 계정으로 바꾸면, root의
  `configured_service_uid()`는 `0`을 반환하고 디렉터리 소유자는 서비스 계정이라
  `rebuild-pinned`·`pin rotate-pair`·`pin apply-pending`·legacy retirement가 전부 거부된다.
- **디렉터리 소유자를 넓히면 flock 상호배제가 깨진다.** 서비스 계정이 디렉터리 쓰기 권한을
  가지면 lock 파일을 unlink하고 새로 만들 수 있고, 그러면 root의 rebuild와 백엔드의 mutation이
  서로 다른 inode를 잠근 채 동시에 진행된다. (이 결함의 일반형은 위 결정 2로 막았다.)
- **두 lease의 계약이 어긋난다.** `pinned-runtime-rebuild.lock`은 같은 디렉터리에 있으면서
  `production=False`라 `{os.geteuid()}`를 요구한다. 디렉터리 소유자만 넓히면 서비스 계정이
  그 파일을 먼저 만들어 root의 rebuild를 부팅 내내 거부할 수 있다.

교훈은 **신뢰 결정의 입력을 프로세스 env에서 읽지 않는다**는 것이다. 이 저장소는 이미 옳은
패턴을 갖고 있다 — `_capture_c6c_deployment_lock_snapshot`은 **production 판정에 한해**
(비운영 분기는 다섯 키를 `os.environ`에서 보충한다) root 소유 `.env` 바이트를 읽어
identity를 고정한 뒤 그 값으로 lock 경로를 정한다.

### 후속

비-root 백엔드는 `docs/tasks.md`의 `NONROOT-BACKEND`로 분리한다. 올바른 방향은 소유자를
넓히는 것이 아니라 **소유자는 root로 두고 접근만 그룹으로 여는 것**이다: lease 디렉터리
`0710 root:<svcgrp>`(서비스 계정은 traverse만, 생성·unlink 불가), lock 파일도 tmpfiles가
`f` 타입으로 미리 만들어 `0660 root:<svcgrp>`, rebuild lease는 `0600 root:root` 유지.
그러면 어떤 파일도 비-root가 만들 수 없으므로 자기 증명이 성립하지 않고, 기대 gid를 잘못
선언해도 fail-close다. 서비스 identity는 `.env` 스냅샷에서 파라미터로 주입한다.

## ADR-42: Map application head는 candidate가 선언하고 두 독립 출처가 합의할 때만 받는다

- 상태: accepted
- 날짜: 2026-08-31
- 결정자: agent (Map `301` child migration 차단 대응)

### 컨텍스트

Map이 migration을 **하나**(`301_m03_import_children`) 더하자 Manager가 candidate를 거절했다.

```
receipt_contract_invalid                                  # _validate_application_contract
installed active Alembic graph head is not exactly 300    # Map fresh installer
```

원인은 배포 계약이 아니라 `application_head = "300"` 리터럴 **열한 사본**이었다.

| 위치 | 형태 |
|---|---|
| `map_application_300.py` | `APPLICATION_HEAD` 상수 + `from_payload`의 exact 비교 + fence/result/permit 9곳 |
| `map_application_300_candidate.py` | `_require_exact_string(contract, "application_head", …)` |
| `pinned_runtime_rebuild.py` | `map_application_head=APPLICATION_HEAD` |
| `compose_service.py` ×2 · `scripts/m05_isolated_e2e.py` | `KOR_TRAVEL_MAP_MIGRATION_EXPECTED_HEAD` 주입 |

`Application300Contract`에는 `application_head` **필드가 없었다** — head는 `from_payload`에서
검증된 뒤 버려졌고, `to_payload`가 상수를 다시 주입했다(왕복 손실). 즉 계약 객체는 head를
운반할 수단 자체를 갖고 있지 않았다.

세 개의 `KOR_TRAVEL_MAP_MIGRATION_EXPECTED_HEAD` 리터럴은 특히 위험했다. 이 값이 head와
다르면 Map API 컨테이너가 **기동 자체를 거부**한다. 단위 테스트도 CI도 보지 못하는 자리다.

### 결정

값 고정을 풀고, 결박을 강화한다. Manager는 head를 **candidate로부터** 얻되, 두 독립 출처가
일치할 때만 받는다.

1. **paired receipt의 baseline contract** — `_canonical_digest(contract)`가
   `application_contract_sha256`으로 결박되고, 그것이 paired receipt sha256 →
   `MapApplication300CandidateEvidence` → journal로 전파된다.
2. **candidate API image의 installed graph** —
   `docker run --network none --entrypoint /usr/local/bin/ktm-application-schema <api_image> head`.
   `map_dagster_head`/`pinvi_head`가 이미 쓰던 `parse_candidate_static_head` 기계를 그대로 쓴다.

둘이 다르면 `DeploymentContractError`로 거절한다.

`"300"`은 **baseline root**로만 남는다(`BASELINE_ROOT_REVISION` / `_BASELINE_ROOT_REVISION`) —
`0236 → 300` handoff의 stamp 목적지이자 `forbidden_application_raw_revision`이 가리키는
격리 선언의 값이다. head가 아니고, migration이 쌓여도 움직이지 않는다.

### 근거

**결박은 이미 암호학적으로 존재했다.** contract는 digest로 봉인돼 receipt·journal까지
전파되므로, 리터럴 비교는 그 위에 얹힌 **값 고정**일 뿐이었다. 지운 것은 값 고정 하나이고,
검증의 엄격함("정확히 기대한 head인가")은 그대로다 — 기대값의 출처만 리터럴에서 candidate로
바뀐다.

두 출처를 요구하는 쪽이 종전보다 **강하다.** 리터럴 비교는 "receipt가 `300`이라고 말하는가"만
봤고, image가 실제로 무엇을 담고 있는지는 보지 않았다. 이제 재빌드 없이 receipt를 재사용한
상태 — 값 고정이 잡아주지 못하던 종류 — 가 거절된다.

값은 풀되 **문법은 조인다**: `_SCHEMA_HEAD = ^[0-9a-z][0-9a-z_.-]{0,127}$`. Map
`PinnedRuntimeGeneration`의 head 문법과 같은 패턴이며, 임의 문자열이 head 자리에 들어오는
것을 막는다.

### 결과

- `Application300Contract`가 `application_head`를 필드로 갖는다 — 왕복 무손실.
- `_assert_pinned_runtime_database_heads`의 exact 대조는 그대로지만, 기대값이 candidate를
  따라간다. Map이 스키마를 진화시켜도 Manager가 막지 않는다.
- `backend/tests/test_map_application_head_flexibility.py`가 재고정을 정적으로 막는다.
  `--wait-timeout 300` 예외는 **바로 앞 줄**을 보고 좁히며, 그 폭 자체를 시험한다.
- `test_map_application_300.py`가 fence → root 결과 → finalize → final permit 전 구간을
  `300` / `301_m03_import_children` / `0999_squashed.v2` 세 head로 완주시킨다 — 리터럴 제거가
  아니라 **유연성 자체**를 증명한다.

### 확인하지 않은 것

- 실 candidate image로 `/usr/local/bin/ktm-application-schema head`를 돌린 결과. Map
  `api.Dockerfile:68`이 설치하고 `docker/application-schema-head.py`가 계약
  (`kor-travel-map.application-head.v1`)을 소유하는 것까지만 확인했다.
- `301`이 실제로 프로덕션에 적용될 때의 end-to-end rebuild. 그것은 Map PR 병합 뒤 별도 실행이다.

## ADR-43: 봉인된 baseline digest는 `300` 도달 순간에만 대조하고, 그 너머는 receipt가 정본이다

- 상태: accepted
- 날짜: 2026-08-31
- 결정자: agent (오너 선택 — "체인을 baseline 너머로 확장")

### 컨텍스트

Map이 migration을 하나(`301`) 더하자 배포가 막혔다. ADR-42가 head 리터럴을 걷어냈지만
그것은 **값 고정** 문제였고, 더 깊은 곳에 **상태 고정** 문제가 남아 있었다.

`alembic/baseline/*.sha256`은 `300` 시점의 물리 catalog와 `alembic_version` facet을
고정한다. 그런데 세 지점이 **live DB를 그 digest와 exact 대조**한다.

| 지점 | 대조 대상 |
|---|---|
| `application-schema-fresh-300.py:940` | `contract["source_catalog_sha256"]` |
| `application-schema-fresh-finalize.py:418` | `expected["destination_catalog_sha256"]` |
| `application-schema-final-permit.py:490` | `expected["catalog"]` |

PostGIS 통합 실행이 6건 실패로 이것을 실증했다. 프로덕션 영향은 fresh 설치 실패에
그치지 않는다 — final permit이 막히면 **API/Dagster 컨테이너가 기동을 거부한다.**

두 갈래를 나눠 봐야 했다.

**facet은 상태를 담지 않았다.** `application-destination-alembic-version.sql`의 산출물은
성공/실패 두 문자열뿐인 단일 boolean이고, 기대 digest는 성공 sentinel의 sha256이다.

    sha256("kor-travel-map.application-destination-alembic-version.v1\n")
      == 72eac11772cdd5428a856188405bd36bedfed03fd47dbb4550fc94b3e2cde32c

그 안에 `alembic_version = ARRAY['300']`을 넣어 두었기 때문에 head가 움직이면 영원히
`mismatch`가 되었다 — 옮겨갈 digest가 아예 존재하지 않았다.

**catalog는 진짜 상태 의존이다.** `application-catalog.sql`은 객체마다 한 행을 내므로
새 migration이 표·제약·함수를 더하면 digest가 반드시 바뀐다.

### 결정

**facet**: SQL에서 revision 술어를 제거한다. 기대 digest는 sentinel 해시이므로 **한 글자도
바뀌지 않는다**. 재봉인 대상은 SQL 파일 바이트와 reference manifest뿐이라 PostGIS oracle이
필요 없다. revision 동등성은 배포 executable 넷이 파생 head로 이미 대조한다.

**catalog**: 봉인된 digest는 `300` 도달 순간에만 대조한다. fresh installer가
`command.upgrade`를 `300`에서 한 번 끊어 exact 대조를 끝내고, head까지 올린 뒤 head 상태를
관측해 operation receipt에 남긴다. 그 이후의 정본은 receipt다.

    baseline digest ──(300 체크포인트)── root receipt ──(pre-ACL)── finalize receipt
                                                                        │
                                                              (post-ACL) └── final permit

root result schema를 v2 → v3으로 올린다(Manager가 exact field set으로 파싱한다).
`301`이 receipt 표의 `result_schema` 열거를 **넓히기만** 한다 — 기존 v2 행이 있으므로
좁히면 `ALTER` 자체가 실패한다.

### 근거

신뢰 근거가 "봉인된 digest가 미리 선언한다"에서 "attest된 candidate image가 만든 것을
receipt가 봉인한다"로 옮겨간다. 사슬은 끊기지 않는다 — head 상태 스키마는 image 안의
migration이 결정론적으로 만들고, receipt는 fence → journal → Manager evidence로 이미
결박돼 있다.

`300`에서의 엄격함은 **하나도 잃지 않는다.** 세 지점 모두 head == baseline root일 때
종전과 동일한 봉인값 대조를 한다. 근거가 옮겨가는 것은 그 너머뿐이다.

대안이던 **baseline 재cut**을 기각한 이유: baseline은 `0236 → 300` squash 산물인데 head와
뒤섞이고, 무엇보다 **다음 migration에서 또 재cut**을 요구한다 — 같은 덫을 다시 놓는다.
(제작에 필요한 핀 이미지 `dc17b064a946`가 n150에 untagged로만 남아 있어 `prune` 한 번에
소실될 수 있다는 점도 확인해 태그로 보호했다.)

### 결과

- `Application300Contract`/`FreshRootResult`에 head 상태 필드가 생긴다.
- permit의 `expected_catalog_sha256`은 head가 baseline root일 때만 봉인값이고, 그
  너머에서는 finalize 관측값이다 — verifier는 두 값의 일치를 보고, 그 출처인 finalize
  receipt는 `finalize_result_sha256`으로 같은 payload에 결박된다.
- Map `tests/lint/test_baseline_contract_sql_is_not_revision_pinned.py`가 facet 재고정을
  막고, 기대 digest가 sentinel임을 직접 보이며, "호출자가 대신 본다"는 주장을 executable
  넷에 대해 코드로 고정한다.

### 잃은 것과 그 보상

head > baseline root에서 **post-ACL catalog의 사전 선언**이 사라진다. 그 상태를 처음
관측하는 것이 finalize이기 때문이다. 보상은 셋이다 — `privileged_residue`와 `seed`는
`301`이 건드리지 않아 봉인값 대조가 그대로 살아 있고, `verify_runtime_projection_invariants`가
runtime ACL 성질을 독립적으로 강제하며, destination facet(이제 head 무관)이
`alembic_version` ACL을 계속 증명한다.

### 확인하지 않은 것

- 실 프로덕션 rebuild. n150 CI-parity와 PostGIS 통합까지만 확인했다.
- 이 변경 뒤 baseline을 재cut할 때 `build-baseline.sh`가 새 facet SQL과 정합한지.

## 2026-09-17 — compose bind allowlist가 코드 상수에서 설치본 설정으로 옮겨졌다 (GM-17 A)

**결정.** production compose candidate가 허용하는 host bind 목록의 정본을
`c6c_deployment._CANDIDATE_ALLOWED_OPERATOR_BINDS`(코드 상수)에서
`config/docker-targets.yml`의 `compose_binds:` 절로 옮긴다. 값은 바꾸지 않는다 —
이관 전후 해석된 매핑 29건이 정확히 같다.

**왜.** 새 bind 하나 또는 여섯 번째 프로젝트의 pgdata에도 backend 코드 수정이
필요했고, 그것이 "범용 Docker target 관리 도구"의 가장 큰 저해 요인이었다(GM-17).

**이 결정이 바꾸는 것 — 보안 경계의 거처.** 이 문서는 이제 "어떤 host 경로가
production 컨테이너에 마운트돼도 되는가"를 정한다. 기존 항목
(`docs/decisions.md` §"directory bind는 manager 파일 ancestor와 host root를 먼저
거부한 뒤 서비스별 canonical source/target allowlist만 허용한다")은 그 allowlist가
**코드**에 있다고 읽히게 두었으므로, 거처가 바뀐 사실을 여기 남긴다.

**무엇으로 그 거처를 받치는가.**

1. **자리 고정** — trusted 설치본에서 `KOR_TRAVEL_DOCKER_MANAGER_TARGETS_FILE`·
   `..._PROJECT_ROOT` redirect를 거절한다. 같은 자리를 가리키는 override만 허용한다.
2. **무결성** — `O_NOFOLLOW`로 열고 경로가 아니라 **열린 fd**를 `fstat`해 root 소유·
   `nlink==1`·group/other 비쓰기를 강제하고, 설치 루트까지의 **부모 디렉터리**도
   심링크 아님·root 소유·비쓰기를 확인한다.
3. **값 정책** — operator bind의 `resolved_source`가 민감 host 자리(`/etc`·`/root`·
   `/boot`·`/proc`·`/sys`·`/dev`·`/var/lib/docker`·docker.sock과 그 하위), 자격증명
   디렉터리(`.ssh`·`.gnupg`·`.aws`·`.kube`), **allowlist 파일 자신과 backend 소스**면
   거부한다.

3번의 마지막이 이 결정에서 가장 중요한 축이다 — **인가하는 파일을 인가되는 것으로 쓸
수 없다.** 그것이 허용되면 한 줄이 그 파일을 RW로 마운트하고, 그 컨테이너가 다음
backend 재기동에 임의 bind를 인가한다. 인가하는 것과 인가되는 것이 같아지면 경계가
아니다.

**방어하는 actor를 분명히 한다.** 이 보호의 대상은 **compose candidate 작성자**이지
host root가 아니다. ADR은 privileged host filesystem actor를 threat model 밖에 두고,
그 결정은 유지된다. 즉 위 셋이 새로 얻는 것은 "설정 파일이 되었으므로 종전보다 쉬워진
경로"를 되돌려 놓는 것이지, 새 threat model을 세우는 것이 아니다.

**적용 범위의 한계 — 명시한다.** 자리 고정과 소유권 강제는 **trusted 설치본
(`/opt/kor-travel-docker-manager`)에서 도는 경우에만** 적용된다. 같은 호스트의 개발
checkout에서 manager를 돌리면 세 판정이 모두 거짓이 되어 보호가 꺼지고, 그 상태에서는
env 하나로 allowlist 전체를 교체할 수 있다. n150에는 실제로 그런 체크아웃이 있다.
값 정책(3번)은 실행 형태와 무관하게 항상 적용된다.

**남긴 것.** target-side 값 정책, allowlist 변경의 즉시 반영(상주 backend가 프로세스당
한 번만 읽는다), `ktdctl targets validate`로의 semantic 검사 이관 — 전부
`docs/tasks.md`에 후속으로 있다.

## ADR-44: concierge를 공용 제어 평면 PostgreSQL instance(`:11000`)로 이전한다 — ADR-37의 concierge 범위 부분 supersede

- 상태: accepted (구현 진행 중 — instance·role·database는 이 변경으로 만들어졌고,
  실제 데이터 cutover는 별도 배포 단계에서 뒤이어 실행한다)
- 날짜: 2026-09-19
- 결정자: 사용자("데이터 마이그레이션까지 진행"), Claude
- 관련: ADR-37, ADR-25, `platform-topology.md` §7, `docs/ports.md`, `config/docker-targets.yml`,
  `config/backup-policy.yml`, kor-travel-concierge PR #236

### 컨텍스트

`platform-topology.md` §7(같은 날 작성)이 프로젝트별 전용 instance 4개(geo/concierge/
map/pinvi, ADR-37)를 공용 제어 평면 instance 하나로 통합하는 것을 목표로 정했지만
"결정됐지만 아직 안 만들어졌다"고 명시했다. concierge 저장소는 그 전 턴에서 연결 설정만
파라미터화(`DATABASE_POOL_SIZE`/`DATABASE_MAX_OVERFLOW`, PR #236)해 두고 실제 이전은
보류했다. 이번 턴에 이전 실행을 지시받았다.

조사로 확인한 제약:

- **실제 prod 복원 도구가 없다.** `ktdctl db-backup`은 백업과 scratch DB 리허설
  복원만 하고, CLI 도움말이 직접 "실제 role DB로 덮어쓰는 복원 명령은 아직 없습니다"라고
  밝힌다. 실제 cutover restore는 수동 `pg_restore`로 처음 실행한다.
- **role·ACL·확장은 database가 아니라 cluster 전역이라는 ADR-37의 교훈이 그대로
  적용된다.** 공용 instance로 되돌아가는 이 결정이 ADR-37이 막으려던 위험(여러
  프로젝트가 서로의 principal namespace를 공유)을 다시 불러올 수 있는 지점이다.
- **`pg_advisory_xact_lock`은 database 단위**(concierge 코드 5곳)라 cutover 중 신·구
  DB에 동시 쓰기가 있으면 동시성 보장이 깨진다 — 점진 전환이 아니라 완전 정지 후
  일괄 전환(hard cutover)이어야 한다.
- 데이터 규모가 작다(실측 231MB, PostGIS 확장 하나, Alembic head `20260901_0029`) —
  실제 다운타임은 컨테이너 재기동을 포함해도 수 분 내로 추정된다.

### 결정

**concierge만** `kor-travel-shared-postgres`(`:11000`, `postgis/postgis:16-3.5`,
`kor-travel-concierge-postgres`와 동일한 loopback scram-sha-256 패턴)로 이전한다.
geo/map/pinvi는 이 ADR의 범위 밖이며 각자 전용 instance(ADR-37)에 남는다.

공용 instance 안에서도 ADR-37의 교훈을 지킨다 — cluster 관리자 계정(`shared_admin`)은
앱에 절대 노출하지 않고, `kor-travel-shared-db-init-concierge` one-shot이 concierge
전용 role(`kor_travel_concierge_app`, NOSUPERUSER/NOCREATEDB/NOCREATEROLE)을 만들어
`kor_travel_concierge` database 하나에만 소유권을 준다. 다른 프로젝트가 합류할 때는
이 role을 재사용하지 않고 각자 새 role+새 db-init one-shot을 추가한다(한 스크립트가
여러 프로젝트의 role/database를 만드는 것이 2026-08-17 사고의 직접 원인이었다).

전환은 완전 정지 → 최종 `pg_dump`(`--format=custom --compress=6`) → 새 instance로
`pg_restore` → row count/Alembic head/PostGIS 검증 → `DATABASE_URL` 전환 →
재기동·live 검증 순의 hard cutover로 실행한다. 옛 `kor-travel-concierge-postgres`는
삭제하지 않고 롤백 안전망으로 그대로 둔다 — `config/docker-targets.yml`의 `conc`
target이 옛/새 instance를 함께 관리해 둘 다 건강 상태를 유지한다.

### 근거

이 인스턴스에서 concierge가 첫 이전 사례다. geo·map·pinvi까지 한 번에 옮기지 않는
이유는 `platform-topology.md` 자신의 원칙("결정됐지만 아직 안 만들어진 구조를 현황으로
주장하지 않는다")을 지키기 위해서다 — 실제로 한 일만 문서에 반영한다. concierge를
먼저 고른 것은 데이터가 가장 작고(231MB) Dagster 등 상태 저장 daemon이 없어 cutover
정지 시간의 blast radius가 가장 작기 때문이다.

### 결과

- `docker-compose.yml`: `kor-travel-shared-postgres` + `kor-travel-shared-db-init-concierge`
  서비스, 새 secret 2개(`kor-travel-shared-postgres-password`,
  `kor-travel-concierge-shared-app-password`), concierge api의 `depends_on`에 새
  instance 추가(옛 instance 의존은 유지 — 롤백 안전망도 같이 건강해야 한다).
- `config/docker-targets.yml`: `kor-travel-shared-postgresql` container 등록,
  `conc`/`map`/`pinvi`/`all` target의 `containers`(`services`는 `conc`만)에 추가,
  `compose_binds`에 새 pgdata bind 허용 항목 추가(GM-17 보안 경계 — 여기 없는 bind는
  candidate 검증이 거부한다).
- `docs/ports.md`: `11000` 포트와 새 instance 행 추가, "네 instance"→"다섯 instance"로
  갱신.
- `AGENTS.md` 룰 4·9와 DB 서비스 정보 표: concierge 포트가 `12600`이 아니라 `:11000`
  기준임을 반영하도록 이 변경과 함께 갱신한다(기존 문구는 ADR-37만 참조해
  concierge 이전을 반영하지 못했다).
- `config/backup-policy.yml`의 `concierge` role 자체는 바꾸지 않는다(`ktdm_standalone`
  layout 유지). `standalone_backup.py`의 `_ROLE_CONFIG["concierge"]` 컨테이너 참조는
  **실제 cutover가 끝난 뒤** 별도 커밋에서 `kor-travel-concierge-postgres`→
  `kor-travel-shared-postgres`로 옮긴다 — cutover 전에 미리 옮기면 cutover 직전
  백업이 아직 비어 있는 새 instance를 겨냥해 무의미해진다.
- **n150 실측으로 발견한 CONNECT 권한 gap(2026-09-19, 최초 배포 직후).**
  PostgreSQL은 기본적으로 모든 database에 `PUBLIC` `CONNECT`를 부여한다 —
  database만 나누는 것으로는 격리가 안 된다는 ADR-37의 교훈이 CONNECT 권한에도
  그대로 적용됨을 놓쳤다. 최초 db-init 스크립트는 role/database/extension만
  만들고 CONNECT는 걷어내지 않아, `kor_travel_concierge_app`이 bootstrap
  `postgres` database에 `SELECT 1`까지 실행할 수 있었다(실측 확인). db-init에
  `REVOKE CONNECT ... FROM PUBLIC`(bootstrap DB와 자기 DB 모두)과
  `GRANT CONNECT ... TO kor_travel_concierge_app`(자기 DB에만)을 추가해 즉시
  고쳤다 — 다른 프로젝트가 이 instance에 합류해도 서로의 database에 기본
  CONNECT가 새지 않는다. n150에는 같은 SQL을 직접 실행해 즉시 반영했고, 이
  커밋은 재현 가능하도록 소스에 반영한다.

### 확인하지 않은 것

- 이 커밋 시점에는 instance·role·database 생성 코드만 존재한다. 실제 n150 배포,
  백업, `pg_restore` cutover, live 검증은 이어지는 별도 단계에서 수행하고 결과를
  `docs/journal.md`에 남긴다.
- geo/map/pinvi의 이전 여부·시점은 이 ADR이 정하지 않는다.

## ADR-45: geo를 공용 제어 평면 PostgreSQL instance(`:11000`)로 이전한다 — ADR-37의 geo 범위 부분 supersede

- 상태: accepted (구현 진행 중 — instance·role·database 생성 코드는 이 변경으로
  만들어졌고, 실제 데이터 cutover는 별도 배포 단계에서 뒤이어 실행한다)
- 날짜: 2026-09-20
- 결정자: 사용자("kor-travel-shared-postgres로 db를 옮겨놔"), Claude
- 관련: ADR-37, ADR-44, `platform-topology.md` §7, `docs/ports.md`,
  `config/docker-targets.yml`, kor-travel-geo T-307(PR #357, code-server 분리),
  kor-travel-geo T-308

### 컨텍스트

ADR-44가 concierge만 공용 instance(`kor-travel-shared-postgres`, `:11000`)로 옮기고
"geo/map/pinvi의 이전 여부·시점은 이 ADR이 정하지 않는다"고 명시적으로 범위 밖에 뒀다.
이번 턴에 사용자가 geo도 옮기도록 지시했다 — `kor_travel_geo`(메인 앱 DB)와
`kor_travel_geo_dagster`(Dagster 메타 DB) 둘 다.

concierge와 달리 geo는:

- **데이터 규모가 훨씬 크다** — `kor_travel_geo` 단독 ~31GB(전국 지오코딩 실 데이터,
  `mv_geocode_target` 6.4M rows), 인스턴스 전체로는 ~33GB. concierge는 231MB였다.
- **REST API가 실 트래픽을 서빙 중**이라 hard cutover 동안 다운타임이 실제로 체감된다.
- **Dagster daemon/webserver/code-server**(T-307, PR #357)가 붙어 있다 — concierge는
  Dagster가 없어 이 제약이 없었다.
- **현재 role이 `addr` superuser**다 — concierge는 이미 scoped role(`kor_travel_concierge_app`)
  이었다. 공용 instance에 다른 프로젝트와 함께 들어가면서 superuser를 계속 쓰는 것은
  ADR-37이 막으려던 위험(principal namespace 공유)을 그대로 재현한다.
- **`pg_advisory_xact_lock`을 쓴다**(geo API 동시성, 백업 retention janitor) — ADR-44가
  concierge에 적용한 것과 같은 이유로 hard cutover(완전 정지 → dump → restore → 검증 →
  DSN 전환 → 재기동)여야 한다.
- 반대로 geo는 concierge에 없던 이점이 있다 — ADR-030으로 이미 검증된 `pg_dump`/
  `pg_restore`/체크섬/restore-drill 파이프라인, 그리고 docker-manager 자체
  `standalone_backup.py`의 `_ROLE_CONFIG`에 이미 등록된 `geo`/`geo_dagster` role로
  `ktdctl db-backup create`가 바로 신뢰 가능한 dump를 만든다. concierge는 이런 도구가
  없어 실제 cutover를 수동 `pg_restore`로 처음 실행해야 했다.

### 결정

**geo**를 `kor-travel-shared-postgres`로 이전한다 — `kor_travel_geo`와
`kor_travel_geo_dagster` 둘 다, 새 scoped role `kor_travel_geo_app`
(NOSUPERUSER/NOCREATEDB/NOCREATEROLE) 소유로. map/pinvi는 이 ADR의 범위 밖이며 각자
전용 instance(ADR-37)에 남는다.

`platform-topology.md` §7의 5단계 계획 중 2~4단계(전 프로젝트 공용 Dagster storage/
workspace/webserver/daemon)는 이번에 하지 않는다 — ADR-44가 concierge에 대해 그랬듯,
geo의 webserver/daemon/code-server 3-프로세스 토폴로지(T-307)는 그대로 두고 두 DB만
공용 instance로 옮긴다. concierge와 마찬가지로, `kor-travel-shared-db-init-concierge`를
그대로 본떠 `kor-travel-shared-db-init-geo` one-shot이 geo 전용 role을 만들어 geo의
두 database에만 소유권을 준다 — 다른 프로젝트의 role/db-init을 재사용하지 않는다
(2026-08-17 사고 재발 방지 원칙 유지).

**geo만의 차이 — extension을 db-init에서 만들지 않는다.** concierge db-init은
`CREATE EXTENSION IF NOT EXISTS postgis`를 기본 `public` 스키마에 미리 심는다. geo의
실제 스키마(`kor-travel-geo`의 `sql/ddl/001_schema.sql`)는 postgis/pg_trgm/unaccent/
pg_stat_statements를 전부 `x_extension` 스키마에 설치한다 — db-init이 먼저
`public`에 심으면 cutover의 `pg_restore`가 가져오는 `x_extension` 배치와 충돌한다
(공용 instance 서비스 자체 주석이 경고하는 "map 커토버 실측" 함정과 정확히 같은
원인). 그래서 geo의 db-init은 **빈 database 두 개 + role + CONNECT grant까지만** 하고,
extension/스키마/테이블은 cutover의 `pg_restore`가 dump 그대로 재생하게 둔다.

PUBLIC CONNECT 격리는 ADR-44가 concierge 최초 배포 뒤에야 발견해 hotfix로 추가했던
`REVOKE CONNECT ... FROM PUBLIC`(bootstrap DB + 자기 DB) / `GRANT CONNECT ... TO
kor_travel_geo_app`(자기 DB에만) 패턴을 처음부터 db-init에 반영한다.

전환은 ADR-44와 동일하게 완전 정지 → `ktdctl db-backup create --role geo`/
`--role geo_dagster`로 dump → 새 instance로 `pg_restore` → row count/Alembic head/
extension 배치 검증 → `KTG_PG_DSN`/`KTG_DAGSTER_PG_URL` 전환 → 재기동·live 검증 순의
hard cutover로 실행한다. 옛 `kor-travel-geo-postgres`는 삭제하지 않고 롤백 안전망으로
그대로 둔다 — `config/docker-targets.yml`의 `geo` target이 옛/새 instance를 함께
관리해 둘 다 건강 상태를 유지한다.

### 근거

geo는 이미 `platform-topology.md` §7 1단계(code-server 분리, T-307/PR #357)를 마쳤다.
2~4단계(전 프로젝트 공용 Dagster storage/webserver/daemon)는 map/pinvi/concierge와의
조정이 필요한 더 큰 변경이라 이번 지시의 범위가 아니다 — 사용자는 명시적으로 두 DB
("둘 다")만 옮기라고 확인했다. ADR-44가 concierge에 대해 5단계 순서를 건너뛰고 앱 DB만
곧장 옮긴 것과 같은 논리로, geo도 Dagster 공유 인프라 없이 자신의 두 DB만 공용
instance에 올린다 — geo의 프로세스 토폴로지는 바뀌지 않으므로 이 지름길의 blast
radius는 "DSN이 가리키는 곳"으로 한정된다.

### 결과

- `docker-compose.yml`: `kor-travel-shared-db-init-geo` one-shot 신규, 새 secret
  `kor-travel-geo-shared-app-password`, geo의 4개 서비스(api·code-server·webserver·
  daemon) `depends_on`에 새 instance 추가(옛 instance 의존은 유지).
- `config/docker-targets.yml`: `geo` target의 `services`/`runtime_services`/`containers`에
  `kor-travel-shared-postgres`·`kor-travel-shared-db-init-geo` 추가.
- `docs/ports.md`: geo 행에 이전 상태 주석 추가(concierge 행과 동일 패턴).
- `AGENTS.md` 룰 4·9와 DB 서비스 정보 표: geo 포트가 `12500`이 아니라 `:11000` 기준임을
  반영하도록 갱신.
- `standalone_backup.py`의 `_ROLE_CONFIG["geo"]`/`["geo_dagster"]` 컨테이너 참조는
  **실제 cutover가 끝난 뒤** 별도 커밋에서 `kor-travel-geo-postgres`→
  `kor-travel-shared-postgres`로 옮긴다(ADR-44와 동일 이유 — cutover 전에 미리 옮기면
  cutover 직전 백업이 아직 비어 있는 새 instance를 겨냥해 무의미해진다).

### 확인하지 않은 것

- 이 커밋 시점에는 instance·role·database 생성 코드만 존재한다. 실제 n150 배포, dump/
  `pg_restore` cutover, DSN 전환, live 검증은 이어지는 별도 단계에서 수행하고 결과를
  `docs/journal.md`(docker-manager)와 kor-travel-geo의 `docs/journal.md`에 남긴다.
- map/pinvi의 이전 여부·시점은 이 ADR도 정하지 않는다.

## ADR-46: PinVi를 공용 제어 평면 PostgreSQL instance(`:11000`)로 이전한다 — 데이터 보존 없이, ADR-44의 pinvi 범위 확장

- 상태: accepted (구현 진행 중 — instance·role·database·DSN 배선은 이 변경으로
  만들어졌고, n150에서의 실제 fresh bootstrap 실행은 별도 배포 단계에서 수행한다)
- 날짜: 2026-09-20
- 결정자: 사용자("kor-travel-shared-postgres로 db를 옮겨놔", "데이터 보존 불필요",
  AskUserQuestion 답변 "둘 다(pinvi + pinvi_dagster)"), Claude
- 관련: ADR-37, ADR-44, `platform-topology.md` §7, `docs/ports.md`,
  `config/docker-targets.yml`, PinVi 저장소 ADR-069·
  `infra/postgres/bootstrap-pinvi-runtime-role.sh`, PinVi PR #558/#356/#559/#358

### 컨텍스트

ADR-44는 "concierge만" 공용 instance로 이전하며 "geo/map/pinvi의 이전 여부·시점은 이
ADR이 정하지 않는다"고 명시했다. 사용자가 이번 턴에 PinVi도 이전하라고 지시했고,
데이터 보존은 필요 없다고 별도로 밝혔다(작업 도중 인터럽트로 전달). 후속 질문에 대한
답으로, `pinvi`(앱 DB)와 `pinvi_dagster`(Dagster 메타DB) 둘 다 이전 대상임을
확인받았다 — PinVi 전용 instance(`pinvi-postgres`, `:12800`) 전체를 사실상 폐역하는
결정이다.

조사로 확인한 제약(ADR-44의 concierge 사례와 다른 지점들):

- **PinVi는 concierge와 달리 이미 자체 다중 role 보안 모델(M05)을 갖고 있다.**
  `bootstrap-pinvi-runtime-role.sh`가 root bootstrap 계정과 별개로 4개 role
  (`PINVI_APP_DB_USER`/`PINVI_APP_SCHEMA_OWNER`/`PINVI_MIGRATION_OWNER`/
  `PINVI_MIGRATOR_DB_USER`)을 만들고, `x_extension`/`ops`/`pinvi_internal` 스키마와
  자체 CONNECT 격리(REVOKE/GRANT)까지 스스로 수행한다. 이 스크립트는 **fresh
  database에서 처음부터 role topology를 구성하도록 설계돼 있어**, 데이터 보존이
  필요 없는 이번 이전과 정확히 맞는다 — concierge처럼 새 role을 Manager
  db-init에서 만들 필요가 없다.
- **그 스크립트는 endpoint를 하드코딩된 allowlist로 검증한다** —
  `app-postgres:5432`/`127.0.0.1:12800`만 허용했다. 공용 instance(`127.0.0.1:11000`)를
  쓰려면 PinVi 저장소 쪽에서 이 allowlist를 먼저 넓혀야 했다(PinVi 자체 PR, 이
  ADR의 범위 밖).
- **`root bootstrap` 계정은 superuser급 권한이 필요하다** — 스크립트가
  `pg_catalog.pg_authid` 등을 `ACCESS EXCLUSIVE`로 잠그므로, 전용 instance에서는
  그 instance 자신의 superuser(`PINVI_POSTGRES_USER`)였던 이 역할이 공용
  instance에서는 cluster 관리자(`shared_admin`/`KOR_TRAVEL_SHARED_POSTGRES_USER`)로
  바뀐다 — concierge처럼 "cluster 관리자는 앱에 노출하지 않는다"는 원칙은 지키되,
  이 **root-only bootstrap one-shot**(`profile: bootstrap`, 정상 런타임에는 포함되지
  않음)만 예외로 그 계정을 받는다.
- **`pinvi_dagster`는 M05 범위 밖이다** — 그 스크립트가 만드는 것은 `pinvi`
  하나뿐이고, `pinvi_dagster`는 기존 `pinvi-db-init`처럼 `PINVI_APP_DB_USER` 소유의
  평범한 database다. 그 role은 M05 bootstrap이 만들기 전에는 존재하지 않으므로,
  새 `kor-travel-shared-db-init-pinvi`는 role 부재 시 `pinvi_dagster` 생성을
  조용히 건너뛰고 멱등하게 재실행 가능해야 한다(순서: db-init → role bootstrap
  (수동, profile bootstrap) → db-init 재실행).
- **PinVi는 `ktdctl pinvi-pair rebuild-pinned --confirm`이라는 별도의 pinned-image
  재구축 계약(`c6c_deployment.py`)의 대상이다** — concierge에는 이런 계약이 없다.
  이 계약은 `PINVI_DATABASE_URL`/`PINVI_DAGSTER_PG_URL`의 포트를 `PINVI_DB_PORT
  == 12800`으로 하드핀했다. 그대로 두면 DSN만 공용 instance로 바꿔도 다음
  `rebuild-pinned`가 그 컴포즈 후보를 비정상으로 판정해 거부한다 — 이 ADR은 이
  계약도 함께 확장한다.

### 결정

**PinVi의 `pinvi`(앱)와 `pinvi_dagster`(Dagster 메타DB) 둘 다** `kor-travel-shared-postgres`
(`:11000`)로 이전한다. **데이터는 옮기지 않는다** — 사용자 지시에 따라 공용 instance에
fresh 상태로 만들고, 옛 `pinvi-postgres`(`:12800`)의 기존 데이터는 그대로 둔 채
폐기하지 않는다(롤백 안전망, concierge와 같은 패턴).

구체적으로:

- Manager `docker-compose.yml`에 `kor-travel-shared-db-init-pinvi`(`pinvi` database
  생성 + `pinvi_dagster`를 role 존재 조건부로 생성)와 `pinvi-shared-db-runtime-role`
  (profile `bootstrap`, PinVi의 M05 bootstrap 스크립트를 공용 instance 대상으로
  재실행하는 root-only one-shot) 두 서비스를 추가한다. 옛 `pinvi-postgres`/
  `pinvi-db-init`/`pinvi-db-runtime-role`은 **손대지 않는다** — 롤백 안전망으로
  그대로 관리한다.
- `pinvi-api`/`pinvi-admin-bootstrap`/`pinvi-dagster`/`pinvi-dagster-code-server`/
  `pinvi-dagster-daemon`의 `PINVI_DATABASE_URL`/`PINVI_DAGSTER_PG_URL`을
  `${PINVI_DB_PORT:-12800}`에서 `${KOR_TRAVEL_SHARED_DB_PORT:-11000}`로 바꾼다.
  role 이름(`PINVI_APP_DB_USER` 등)과 database 이름(`pinvi`/`pinvi_dagster`)은
  **그대로 재사용한다** — 서로 다른 물리 cluster라 이름이 겹쳐도 충돌하지 않고,
  M05 스크립트 자신의 인터페이스이기도 하다. 이 다섯 서비스의 `depends_on`에
  `kor-travel-shared-postgres`/`kor-travel-shared-db-init-pinvi`를 추가하되, 옛
  `pinvi-postgres`/`pinvi-db-init` 의존은 유지한다(concierge와 같은 이유 —
  롤백 안전망도 같이 건강해야 한다).
- `c6c_deployment.py`: `_PINVI_SHARED_POSTGRES_PORT`(11000) 상수를 새로 두고,
  `_pinvi_dsn`/`_validate_pinvi_database_url_environment`가 `PINVI_DATABASE_URL`/
  `PINVI_DAGSTER_PG_URL`의 포트 핀을 `KOR_TRAVEL_SHARED_DB_PORT == 11000`으로
  바꾼다. `PINVI_DB_PORT == 12800` 핀은 **같은 함수 안에 별도 조건으로 남긴다** —
  전용 instance(`pinvi-postgres`/`pinvi-db-init`) 자신의 포트는 그대로 검증해야
  Map 대역(`12700`) 탈취 같은 기존 방어가 유지된다. 새 두 서비스는
  `_CANDIDATE_NAMEABLE_SERVICE_NAMES`에 등록해 오류 메시지에서 이름이 보이게 하고,
  `pinvi-shared-db-runtime-role`의 role env 참조는 `_PINVI_RUNTIME_ROLE_CANONICAL_ENV_VALUES`에서
  **파생**(POSTGRES_USER/PINVI_DB_PORT만 재정의)해 protected-reference leak-scan을
  통과시킨다. 두 서비스의 shape 자체를 바이트 단위로 고정하는 전용 validator는
  **쓰지 않았다** — 옛 두 서비스(`pinvi-postgres`/`pinvi-db-init`/
  `pinvi-db-runtime-role`)가 받는 것과 같은 수준의 exact-match 보호는 이 ADR의
  범위 밖이며, 필요해지면 별도 작업으로 추가한다.
- `config/docker-targets.yml`: `pinvi` target의 `services`에 두 서비스 추가(옛
  `pinvi-postgres`는 유지), `pinvi-shared-db-runtime-role`은 `pinvi-db-runtime-role`과
  같은 이유로 `services`에 넣지 않는다(bootstrap profile, ensure 대상 아님).
  `compose_binds`에 `pinvi-shared-db-runtime-role`의 스크립트 bind 허용 항목 추가.
- PinVi 저장소: `bootstrap-pinvi-runtime-role.sh`의 endpoint allowlist에
  `127.0.0.1:11000` 추가(별도 PinVi PR).

### 근거

PinVi를 concierge 다음으로 고른 것은 사용자가 직접 지시했기 때문이고, 데이터
보존을 요구하지 않은 것도 사용자의 명시적 결정이다(PinVi 사용자 데이터가 실 서비스
데이터임에도 이 선택을 했다 — 재현 가능한 손실로 판단한 것으로 이해한다). M05
role topology를 그대로 재사용하기로 한 것은, 이미 존재하는 보안 설계를 공용
instance라는 이유만으로 단순화(예: concierge 같은 단일 role 모델로 축소)하는 것이
PinVi 자체의 다중 role 분리(app/schema-owner/migration-owner/migrator) 설계 의도를
훼손하기 때문이다 — "같은 스크립트, 다른 endpoint"가 가장 작은 변경이었다.

### 결과

위 "결정" 절의 각 파일 변경이 실제 diff다. 검증: `pytest`(Manager backend
전체, 이 변경 전후 실패 집합 동일 — 사전 존재하는 무관한 flake 1건 제외),
`docker compose config` 기반 raw/resolved C6c 계약 테스트(`test_f1d_compose_contract.py`)
226건 통과.

### 확인하지 않은 것

- n150에서의 실제 fresh bootstrap 실행(`kor-travel-shared-db-init-pinvi` →
  `pinvi-shared-db-runtime-role` → `kor-travel-shared-db-init-pinvi` 재실행 →
  `DATABASE_URL` 전환 → 재기동·live 검증)은 별도 배포 단계에서 수행하고 결과를
  `docs/journal.md`에 남긴다.
- 옛 `pinvi-postgres` instance의 폐기 시점·조건은 이 ADR이 정하지 않는다
  (concierge와 같은 미해결 상태 — `shared-postgres-onboarding.md` P12 참고).
- `pinvi-shared-db-runtime-role`을 `ktdctl pinvi-pair rebuild-pinned`의
  role-topology 재검증 시퀀싱(`pinvi_database_role_credentials.py` 등)에 통합할지는
  이 ADR이 정하지 않는다 — 이번 이전은 그 시퀀싱과 독립적인, 수동 실행되는 root-only
  one-shot으로 취급했다.
- `pinvi_dagster`가 향후 §7 2단계(`dagster_shared`)로 다시 이전될 때, 이번 이전으로
  생긴 공용 instance 안의 `pinvi_dagster`를 어떻게 승계할지는 정하지 않았다
  (`platform-topology.md` §7 참고).

## ADR-47: weather를 external target에서 Manager internal target으로 전환한다 — 앱·Dagster DB 둘 다 공용 instance로, 데이터 보존 없이

- 상태: accepted (Web 포트 `14105` 부분만 2026-10-08 ADR-57로 superseded — `12205`.
  구현 진행 중 — compose 서비스·db-init·registry·포트 문서 배선은 이
  변경으로 만들어졌고, n150에서의 실제 배포·cutover는 별도 단계에서 수행한다)
- 날짜: 2026-09-20
- 결정자: 사용자("weather, transport도 내부 target으로 바꿔", "weather 전체(db·api·web·dagster
  전부)를 Manager compose로 통합", "weather는 데이터는 옮기지 마 — 어차피 새로 쌓으면 됨",
  "기존 14100-14199 그대로 유지"), Claude
- 관련: ADR-46, `platform-topology.md` §2·§7, `docs/ports.md`, `config/docker-targets.yml`,
  weather 저장소 `deploy/n150.md`·PR #61(code-server 분리)·PR #63/#64(되돌려진 공유
  Dagster storage 첫 시도)

### 컨텍스트

weather는 `platform-topology.md` §2가 정의하는 두 축(내부/외부 target) 중 **외부
target**이었다 — compose 정본이 weather 자신의 저장소에 있고, Manager는 상태·수명주기만
다뤘다(`ensure` 거부). 이번 세션 초반에는 weather의 Dagster 메타데이터 저장소만
공용 instance로 옮기는 좁은 작업(bridge network 경유)을 시도했으나, 그 방식은
`kor-travel-shared-postgres` 자신의 네트워크 형태를 바꿔야 했고 그 과정에서 실제
production 장애(concierge 연결 몇 분간 단절, 즉시 되돌림)를 냈다. 사용자가 이후 이
전제 자체를 바꿔 weather(와 장래 transport)를 **완전한 internal target**으로
전환하라고 지시했다 — 이러면 bridge network가 아예 필요 없어진다(internal target은
`network_mode: host`로 공용 instance에 `127.0.0.1:11000`으로 직접 닿는다, geo/concierge/
map/pinvi와 동일).

리서치(멀티에이전트 workflow, 5개 조사 + 설계 + 적대 리뷰 2건)로 확인한 제약:

- **weather는 Manager가 관리하는 전용 postgres 컨테이너를 가진 적이 없다** — 외부
  target이었을 때부터 자기 저장소의 `db`(`postgres:16-alpine`) 하나뿐이었다. 그래서
  concierge/geo/pinvi(전용 instance → 공용 instance로 "이전")와 달리, weather는
  "새로 만든다"는 쪽에 더 가깝다 — 롤백 안전망으로 남는 것도 Manager가 관리하던
  전용 instance가 아니라 weather 자신의 `compose.yaml`(격하되지만 삭제 안 됨)과
  그 안의 `db` 볼륨이다.
- **`network_mode: host`는 bridge NAT을 없애 두 가지를 직접 방어선으로 바꿔야
  했다**: (1) `dagster-webserver`와 `dagster-gateway`가 원래 컨테이너 내부에서 같은
  포트 14102를 썼다(bridge에서는 별 네임스페이스라 무해) — host 모드에서는 충돌하므로
  webserver를 내부 전용 `14107`로 옮기고 gateway의 nginx 오버라이드(Manager 소유)가
  거기로 proxy_pass한다. (2) `dagster-code-server`의 gRPC 포트(`14106`, 인증 없음,
  코드 실행 가능)는 bridge의 `ports: 127.0.0.1:14106:14106` publish 매핑이 지키던
  loopback-only를, host 모드에서는 프로세스 자신의 `-h` 바인드로 대체해야 한다(weather
  자신의 compose.yaml은 `-h 0.0.0.0`을 쓰는데 bridge 전제라서만 안전했다).
- **`dagster-code-server`의 metrics 포트(`14103`)는 compose로 제어할 수 없다** —
  weather 자신의 Python 코드(prometheus_client multiprocess 서버)가 여는 것이라 이
  compose의 `-h` 플래그가 닿지 않는다. host 모드에서 인증 없이 LAN에 노출된다.
  적대 리뷰가 이 노출을 HIGH로 escalate했고, 같은 패턴이 geo(`kor-travel-geo-dagster-code-server`)
  ·PinVi(`pinvi-dagster-code-server`)에도 이미 실재함을 발견했다(이 작업과 무관하게
  이미 있던 노출). **사용자 결정: 지금은 그대로 두고 알려진 것으로 기록, 후속
  작업으로 미룬다** — compose 수준 해법이 없어(weather 저장소 소스 변경 또는 n150
  호스트 방화벽 필요) 이 ADR의 범위 밖이다.
- **포트는 재배정하지 않는다** (Web `14105`만 2026-10-08 ADR-57로 superseded — `12205`) — n150의 HAProxy(저장소 밖, 호스트 설정)가 이미
  weather의 기존 포트(`192.168.1.14:14101`/`14102`/`14105`)로 공개 도메인을
  라우팅 중이다. `network_mode: host`에는 NAT이 없어 `ports:`는 사실상 무의미하고
  (Docker가 무시), 프로세스가 같은 포트에서 `0.0.0.0` 바인드를 유지하는 한 HAProxy는
  코드 변경 없이 계속 동작한다 — 실측 확인됨(`docker compose up`의
  "Published ports are discarded when using host network mode" 경고).
- **롤백 안전망 서술이 갈린다** — concierge/geo/pinvi는 "옛 전용 instance를 그대로
  둔다"가 롤백이지만, weather는 그런 instance가 없었으므로 롤백은 weather 자신의
  `compose.yaml`(local-dev/e2e로 격하)을 다시 띄우는 것이다. 실측: n150의 weather
  체크아웃은 이 ADR 작성 시점에 `main`(commit `fbb52e8`, PR #63/#64 되돌림 이후
  상태, clean)이라 이 롤백 경로가 실제로 유효함을 확인했다.

### 결정

weather의 **`api`·`web`·`dagster-code-server`·`dagster-webserver`·`dagster-daemon`·
`dagster-gateway`·`migrate`(one-shot)·`prometheus`** 8개 서비스 전부를 Manager의
`docker-compose.yml`로 옮긴다(`network_mode: ${KTDM_DOCKER_NETWORK_MODE:-host}`).
weather 자신의 `db`는 은퇴한다 — 앱 DB(`kor_travel_weather`)와 Dagster 메타DB
(`kor_travel_weather_dagster`)를 `kor-travel-shared-postgres`(`:11000`)에 **완전히
빈 상태로** 새로 만든다(`kor-travel-shared-db-init-weather`, 데이터 이전 없음, PinVi
ADR-46과 같은 패턴 — pg_dump/restore 없음). 기존 포트 대역(`14100-14199`)은 그대로
유지한다. weather 자신의 저장소(`compose.yaml`/`deploy/`)는 삭제하지 않고
local-dev/e2e 전용으로 남긴다 — prod 정본 지위만 옮겨온다.

Dagster 메타DB의 role/database 이름(`kor_travel_weather_dagster_app`/
`kor_travel_weather_dagster`)은 weather 자신의 compose.yaml(x-dagster-environment
앵커)이 이미 하드코딩해 둔 literal이므로 그대로 재사용한다 — 이 ADR이 새로 짓는
이름이 아니다. 앱 DB 쪽(`kor_travel_weather_app`/`kor_travel_weather`)은 이 ADR이
새로 정한다.

`§7 2~4단계`(여러 프로젝트가 하나의 `dagster_shared`/공유 webserver·daemon으로
합치는 것)는 이 ADR의 범위 밖이다 — weather는 자기 전용 webserver/daemon/
code-server/gateway 4종을 그대로 유지한 채 internal target이 될 뿐이다.

### 결과

`docker-compose.yml`(신규 9서비스 + secrets 3종), `config/kor-travel-weather/`(신규
지원 파일 3개: workspace.yaml·dagster-gateway.conf·prometheus.yml — weather 자신의
저장소는 건드리지 않고 Manager가 오버라이드만 소유), `config/docker-targets.yml`
(dependency_order·containers 7종·targets.weather·compose_binds 5종),
`.env.example`, `docs/ports.md`가 이 변경의 diff다. 검증:
`test_f1d_compose_contract.py`(전체 스위트 실행 예정 — 이 ADR 작성 직후).

### 확인하지 않은 것

- n150에서의 실제 배포(이미지 build, `kor-travel-shared-db-init-weather`/
  `kor-travel-weather-migrate` 실행, weather 자신의 기존 스택 정지, Manager 소유
  스택 기동, HAProxy 경유 공개 도메인 검증)는 별도 배포 단계에서 수행하고 결과를
  `docs/journal.md`에 남긴다.
- n150의 이미지 build 메커니즘(`ktdctl weather --build` 가정)은 geo/concierge에도
  적용되는 미확인 사항이며 이 ADR이 새로 만든 문제가 아니다.
- `alembic upgrade head`만으로 완전히 빈 database에 충분한지(옛 `db` 서비스의
  `docker-entrypoint-initdb.d`가 암묵적으로 하던 일이 있었는지)는 실제 배포 전
  dry-run으로 확인해야 한다.
- `dagster-code-server`의 metrics 포트(`14103`) 노출은 위에서 서술한 대로 사용자
  결정에 따라 의도적으로 미해결 상태로 남긴다.
- 옛 weather `db`(`weather-postgres` volume, weather 자신의 compose.yaml에만 남음)의
  폐기 시점·조건은 이 ADR이 정하지 않는다.
- transport의 internal target 전환은 이 ADR의 범위 밖이다 — 별도 작업.

## ADR-50: 보안모델과 이미지 정합성은 과결박 없이 단순하게 간다 — 상시 설계 원칙

- 상태: accepted (상시 원칙 — 개별 변경이 아니라 앞으로의 모든 설계 결정에 적용)
- 날짜: 2026-09-22
- 결정자: 사용자("보안모델은 단순하게", "manager 에서 docker 이미지 만들고 map geo pinvi
  concierge weather transport 올려서 이미지간 정합성 맞추는 것도 과결박없이 단순하게"),
  Claude
- 관련: ADR-37/44/45/46/47(공용 instance 이전 계열), kor-travel-map ADR-100(LOGIN role
  3→1 통합), kor-travel-map ADR-090(이 원칙 이전에 세워진 21-role 모델), PinVi M05 폐기

### 컨텍스트

이 플랫폼은 "안전한 쪽으로" 결박을 하나씩 더하는 방향으로 오래 자라왔다. 각각은 그
시점에 타당했지만, 누적 결과가 반복적으로 비용을 만들었고 그 비용이 실제로 관측됐다.

**관측된 과결박 비용(전부 이 저장소·형제 저장소에서 실제로 난 일이다):**

- **소비된 pinset을 다시 회전할 수 없었다** — claim 파일의 two-witness 술어가 재시도
  자체를 막아, 무해한 원인으로 멈춘 재구축이 영구 고착됐다. 탈출구가 "새 커밋"뿐이었다
  (PR #377에서 ledger 파일명에 attempt ordinal을 주는 것으로 완화).
- **봉인된 실패가 원인을 가린다** — 재구축 실패가 stage 한 단어만 남기고 끝나,
  venv에서 같은 호출을 재현해야 원문이 나왔다. 진단 가능한 실패보다 "조용한 실패"가
  더 비쌌다.
- **`.env` 해시 rebind 게이트가 pinset을 영구 고착시켰다** — `map_runtime_ready` 이후
  `.env`가 바뀌면 원인이 무해해도 그 pinset은 재개 불가였다(PR #382에서 제거).
- **4겹 중첩 해시** — Map의 `schema.sql` → `application-reference.json` →
  `application-reference.sha256` → 그 안의 receipt digest → receipt 파일 자체의
  byte hash. 한 줄 고치는 데 연쇄 재봉인이 필요했고, 각 층이 서로 다른 실패
  메시지로 따로 터졌다.
- **상호 배타 게이트 35개+가 role 하나 합치자 전부 무효화됐다** — ADR-090의
  "admin executor XOR provider executor" 강제가, LOGIN role을 하나로 합치는 순간
  구조적으로 성립 불가능해졌다(`ktm_feature_service`가 양쪽 모두의 member라서 모든
  호출을 거절). 결박이 촘촘할수록 그 위의 변경 하나가 더 크게 번졌다.
- **PinVi M05 다중 role 모델** — 전용 cluster 전제(`foreign_database_owner` 등)가
  공용 instance로 옮기는 순간 깨졌고, 그 전제를 지키려던 role lifecycle 기계장치가
  한 주에 half-cutover 결함 다섯 개를 냈다. 결국 통째로 폐기하고 geo 패턴(단일 scoped
  app role)으로 교체하는 것이 답이었다.

kor-travel-map 저장소는 2026-09-21에 vNext 우선순위를 **정확성 → 단일 정본/설계적
우월성 → 단순성 → 보안 → 확장성 → 실측 성능 → 호환성**으로 개정했다(CLAUDE.md /
AGENTS.md). 단순성이 보안보다 위다. 이 ADR은 그 순서를 Manager 쪽 설계에도 명시적으로
확장한다.

### 결정

1. **보안모델은 단순하게 간다.** role 분리·상호 배타 게이트·봉인 digest·attestation
   체인을 겹겹이 쌓지 않는다. 단일 scoped 주체(geo 패턴)를 기본형으로 두고, 그보다
   복잡한 모델은 그 복잡도가 막아주는 구체적 위협을 명시할 수 있을 때만 쓴다.
   "이론적으로 더 안전하다"는 근거로는 부족하다.

2. **Manager의 이미지 빌드와 다중 프로젝트 정합성도 같은 원칙으로 간다.** Manager가
   docker 이미지를 만들고 map · geo · pinvi · concierge · weather · transport를 올려
   이미지 간 정합성을 맞추는 구조에서, pinset 해시 계약 · 교차 저장소 revision 핀 ·
   journal phase 게이트 · receipt/permit 체인 같은 결박을 **더 늘리지 않는다.** 이미
   있는 것도 걷어낼 수 있으면 걷어낸다.

3. **새 게이트를 추가하려면 먼저 멈춘다.** 검사기·봉인·attestation을 하나 더 붙이자는
   판단이 들면, 그것 없이 성립하는 더 단순한 설계가 있는지부터 본다. 추가가 정말
   필요하면 무엇을 막는지와 어떤 실패 모드를 새로 만드는지를 같이 적는다.

4. **다만 동작 중인 보장을 조용히 없애지는 않는다.** 기존 결박을 걷어낼 때는 무엇을
   잃는지 명시하고 사용자 판단을 받는다. kor-travel-map ADR-100이 그 방식의 사례다 —
   LOGIN role을 합치면서 "API인지 Dagster인지 구분하던 감사 출처가 사라진다"는 것을
   범위가 밝혀질 때마다(21개 → 56개) 다시 보고하고 승인을 받았다.

### 근거

결박 하나하나는 국소적으로 옳아 보이지만, 이 플랫폼에서 실제로 비싼 실패는
"막지 못해서" 난 것보다 **"막아놔서 못 고친"** 쪽이 많았다. 위 목록이 그 증거다.
1~2인이 운영하는 시스템에서 진단 가능성과 복구 가능성은 그 자체로 보안 속성이다 —
영구 고착된 재구축과 원인을 가린 봉인은 가용성 사고였다.

### 결과

- **긍정**: 변경 한 건의 파급이 줄고, 실패했을 때 원인을 읽을 수 있고, 재시도가
  가능해진다. 형제 프로젝트 여섯 개를 한 Manager가 올리는 구조에서 프로젝트마다
  다른 결박을 외울 필요가 줄어든다.
- **부정**: DB가 강제하던 일부 경계가 애플리케이션 코드의 규율로 내려온다. 예를 들어
  "이 procedure는 Dagster만 부른다"는 것을 이제 DB가 확인해 주지 않는다 —
  호출부가 맞게 쓰는지는 코드 리뷰와 테스트의 몫이다.
- **후속**: 이 원칙은 소급 적용되지 않는다. 기존 결박을 언제 어디까지 걷어낼지는
  개별 작업에서 범위를 정하고 사용자 승인을 받는다. 이 ADR은 "앞으로 더 쌓지 않는다"와
  "걷어내는 방향이 기본값이다"까지를 고정한다.

### 미해결

- Manager의 pinned-runtime 재구축 파이프라인(pinset 해시 계약, journal phase,
  receipt/permit 체인)에서 실제로 무엇을 걷어낼지는 아직 정하지 않았다 — 별도 작업.
- kor-travel-map의 `schema.sql` 4겹 중첩 봉인을 줄일지도 별도 판단이다. ADR-100
  작업에서 해시 갱신 절차 자체는 스크립트로 흡수했지만 층 수는 그대로다.
- ADR-090(21-role 모델)의 나머지 부분 — NOLOGIN procedure owner 계층과 SECURITY
  DEFINER 경계 — 은 ADR-100 범위 밖으로 남겨뒀고, 이 ADR도 그것을 자동으로
  폐기하지 않는다.
## ADR-48: Prometheus/cAdvisor/Grafana host 포트를 12102/12103/12104로 재배정한다 — ADR-10의 포트 배정 부분 supersede

- 상태: accepted (repo-level 설정·문서 변경만 이 변경의 범위 — n150 실제 재배포는 별도
  단계, 아래 "확인하지 않은 것" 참고)
- 날짜: 2026-09-21
- 결정자: 사용자, Claude

### 컨텍스트
ADR-10(2026-06-13)이 배정한 host 포트 Grafana `12205`/cAdvisor `12301`/Prometheus
`12401`을 사용자가 명시적으로 `12102`/`12103`/`12104`로 바꾸도록 지시했다. 저장소를
조사한 범위(compose·target registry·문서·저장소 전체 grep)에서는 기존 포트가 다른
서비스와 충돌했다는 기록이나 미해결 이슈는 찾지 못했다 — 이 ADR은 사실만 기록하고
사유를 지어내지 않는다. 세 target 이름(`gra`/`cadv`/`prom`)과
`config/docker-targets.yml`의 키·compose service 이름은 그대로 두고 포트값만 바꾼다.

### 결정
- Prometheus host 포트: `12401` → `12102`
- cAdvisor host 포트: `12301` → `12103`
- Grafana host 포트: `12205` → `12104`

새 세 포트는 `storage` 대역(`12100-12199`) 안으로 들어간다 — `12101`(RustFS S3
API)과 `12105`(RustFS console) 사이의 빈 자리라 다른 서비스와의 실제 포트 충돌은
없지만, `docs/ports.md` §기본 규칙이 전제하는 "target 이름 대역 = 실제 포트 대역"
관례에는 이 세 target만 명시적 예외가 생긴다(`docs/ports.md` "`gra`/`cadv`/`prom`의
대역 예외" 절 참고).

### 근거
- 사용자가 정확한 목표값(`12102`/`12103`/`12104`)을 명시적으로 지정했다 — 기존
  100단위 대역 규칙과 어긋나더라도 그 값을 그대로 쓴다(사용자가 준 값을 임의
  재해석해 "올바른" 대역으로 바꾸지 않는다).
- `storage` 대역 안에 빈 자리(`12102`-`12104`)가 있어 저장소 전체 grep 기준으로 다른
  서비스와 실제 포트 충돌은 없다.

### 결과(긍정)
- `docker-compose.yml`의 `PROMETHEUS_PORT`/`CADVISOR_PORT`/`GRAFANA_PORT` 기본값,
  `config/docker-targets.yml`의 `connection`/`expected_ports`,
  `config/prometheus/prometheus.yml`의 scrape target,
  `config/grafana/provisioning/datasources/prometheus.yml`의 datasource URL,
  `.env.example`이 새 포트로 일관된다.
- `docs/ports.md`, `docs/architecture.md`, `docs/dev-environment.md`,
  `docs/docker-management.md`, `README.md`, `AGENTS.md`, `CLAUDE.md`, `SKILL.md`가
  같은 값을 반영한다.

### 결과(부정)
- `gra`/`cadv`/`prom` target 이름과 실제 포트 대역이 더 이상 맞지 않는다 — 이 세
  target을 보는 사람은 `docs/ports.md`의 예외 설명을 함께 봐야 한다.
- 기존에 `12205`/`12301`/`12401`을 가정한 외부 dashboard·북마크·방화벽 규칙이
  저장소 밖에 있다면 이 변경으로 깨진다(저장소 조사 범위에서는 찾지 못했다).

### 확인하지 않은 것
- n150 production에서 실제로 도는 Prometheus/cAdvisor/Grafana 컨테이너는 이 변경만
  으로는 바뀌지 않는다 — 새 포트는 재배포(컨테이너 재생성, 최소한 새 `.env`/
  `ports:` 매핑으로 `prometheus`/`cadvisor`/`grafana` compose service를 다시
  기동하는 것) 전까지 적용되지 않는다. 재배포 전까지 n150은 계속 옛 포트
  (`12401`/`12301`/`12205`)로 서비스한다. 이 ADR·PR은 repo-level 설정·문서 변경만
  포함하고 live 서비스는 건드리지 않았다.
- Grafana의 Prometheus datasource가 provisioning YAML 갱신만으로 반영되는지, 아니면
  재배포 시 컨테이너 재생성이 필요한지는 실제 n150 재배포에서 확인해야 한다.
- 저장소 밖의 방화벽/리버스 프록시가 이 세 포트를 번호로 참조하는 설정이 있는지는
  이 저장소 조사 범위 밖이다 — 저장소 안에서는 그런 설정을 찾지 못했다.

### 후속
- (open) n150 실제 재배포 — 사용자 확인 후 별도 작업으로 수행한다. 재생성 직전에는
  `scripts/verify-monitoring-port-migration.sh`의 환경변수 사전 검사를 통과해야 하며,
  재생성 뒤에는 같은 스크립트로 Prometheus readiness, cAdvisor health, Grafana health,
  Prometheus self/cAdvisor scrape를 모두 확인한다. 이 순서는 기존 `.env`의 옛 포트
  override가 scrape/datasource와 분리되는 것을 막는다.
- host network 모드에서는 `ports:`가 방화벽 경계가 아니다. 운영자는 새 포트
  `12102`/`12103`/`12104`가 기존 관측 포트와 같은 방화벽 노출 정책을 갖는지 별도로
  확인한다. 외부 노출을 바꾸는 것은 이 포트 이전과 분리된 보안 변경으로 다룬다.

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
   internal|public[/경로]}}}`(registry가 형태를 fail-close로 검증한다). `config/dagster-shared/workspace.yaml`은
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
