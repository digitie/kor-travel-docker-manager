# ktdctl → UI 이관 및 운영 기능 격차 설계 (v2)

## 범위와 비목표

이 문서는 **설계 문서**다. 코드 변경은 전혀 없다. 목적: (1) `ktdctl`이 이미 할 수 있는
일 중 UI에 아직 없는 것을 찾아 이관 후보를 정리하고, (2) 운영에 필요하다고 지목된 영역
— GitHub source pull+build, git revision/계약 정합, git 이력 조회, Docker 이미지
업데이트, 백업, 설정/secret 변경, 기타 — 의 격차와 안전장치를 제안하며, (3) **v2에서
추가**: 반복되는 pin 회전 작업의 설정파일화, 비전문 관리자 편의성 중심의 우선순위
재정렬, 구조 리팩토링 필요성 평가를 다룬다.

**비목표**: 이 문서는 구현하지 않는다. 제안된 CLI 서브커맨드, API 엔드포인트, UI 화면은
전부 미착수 상태이며, 표기된 서명·경로·플래그는 설계 초안이지 확정 계약이 아니다.

**v2 개정 배경**: v1은 보안/안정성 우선으로 작성됐다. 오너가 방향을 재지정했다 —
"보안·안정성보다는 **비전문가의 관리 편의성·직관성**을 중심으로 검토하라." v2는 이
기준으로 전 항목을 재평가했다. 안전 속성을 내주는 항목은 무엇을 내주는지 명시하되,
안전을 이유로 편의성 항목을 기각하지 않는다. v2는 5개 분석 축(웹 UI 재점검 / pin
하드코딩의 설정파일화 / 기능 격차 재검증 / 비전문가 직관성 / 구조 리팩토링)을 각각
독립 조사 에이전트로 실코드 대조 조사한 뒤, **사실 정확성 리뷰와 지시 정합·일관성
리뷰 두 전문 리뷰를 독립 수행**해 확인된 지적(커밋 수·라인 인용 오류, manifest 읽기
경로의 rehearsal 게이트, P1의 배포·캐시·부트스트랩 공백, P6과 P1의 경계 논거 충돌 등)을
반영한 최종본이다.

---

## 현재 상태 인벤토리 (HEAD `b964958` 재검증 — 변경 0건)

v1 작성 이후 M05 계열 PR(#238–#242)이 merge됐지만 `git log b467c02..HEAD -- cli.py
api/`는 빈 결과다 — **CLI/API 표면은 v1 표와 동일**하다(M05 PR들은 전부 rebuild 내부
변경: pin 회전, builder 실패 분류, base-image preflight). `ktdctl`은
`build_parser()`(`backend/src/kor_travel_docker_manager/cli.py:260-418`)가 등록하는
**13개 leaf 명령**(기본 6 + `pinvi-pair rebuild-pinned` + `compose-boundary` 3 +
`db-backup` 3)으로 구성된다.

| ktdctl 명령 | 하는 일 | API 노출 | UI 노출 |
|---|---|---|---|
| `targets [--json]` | target 목록·의존 순서·해석된 service 목록 | `GET /api/v1/targets` | 간접적 — `ContainerDetailModal`의 영향 범위 계산에만 사용 |
| `status [target] [--json]` | `docker compose ps` 결과 | `GET /api/v1/containers`, `WS /api/v1/ws/status`가 사실상 동등 | 컨테이너 테이블 |
| `ensure <target> [--build] [--recreate] [--stream] [--json]` | target의 `depends_on` 폐포를 위상정렬 순서로 `docker compose up -d`(+`init_steps`) 실행 | `POST /api/v1/targets/{target}/ensure` — production에서 두 지점 하드스톱(`compose_service.py:4472`, `:4483`, T-044) | `ContainerDetailModal`의 `IS_DEV` 전용 버튼(서버가 차단, 409) |
| `logs <name> [-f] [--tail N] [--json]` | compose/service 로그 | `GET /containers/{id}/logs`, `WS /ws/logs/{id}` | 실시간 로그 모달 |
| `action <container> start\|stop\|restart` | 컨테이너 제어 | `POST /containers/{id}/action` | Start/Stop/Restart 버튼 |
| `inspect <container> [--json]` | inspect 요약 | `GET /containers/{id}/inspect` | `ContainerDetailModal` 5개 탭 |
| `pinvi-pair rebuild-pinned --confirm [--json]` | Map 4종+PinVi 3종 destructive 재구축, `rehearsal/rebuildable` 조합 아니면 거부 | 없음 | 없음 |
| `compose-boundary stage/retire/activate --confirm` | legacy Compose override 이관(1회성) | 없음 | 없음 |
| `db-backup create/list/gc` | pg_dump 백업 생성/조회/정리 | `list`만 `GET /api/v1/backups?role=` | `BackupHistoryPanel`(읽기 전용) |

그 외 API 전체 표면: `api/admin.py`(login-audit-events, public-api-keys GET/POST/DELETE),
`api/auth.py`(login/logout/me), `main.py:202`의 `GET /health`. **`GET /health`는 어느
UI에도 표시되지 않는다** — "관리도구 자신이 정상인가"는 비전문 운영자의 첫 질문인데
답할 화면이 없다(→ P7-D).

`ContainerConfigUpdate`(`api/routes.py:37-46`)의 `volumes`는 편집 필드가 아니라
echo-only 계약이다(값 변경 시 `ComposeCandidateContractError`로 거부). `image` 필드는
스키마에 없다.

**컨테이너 단위 조작과 target 재생성의 실제 관계**(v1 정정 유지):
`control_container`/`update_container_config`/`reset_container_config`는 C6c host-wide
lock **안에서** 실행되고, `ensure_target`만 production 전용 하드스톱을 추가로 갖는다.
config/reset이 production에서 허용되는 실질 이유는 바이트 동일 이미지 재사용·볼륨 그래프
불변·실패 시 restore 트랜잭션이다 — 코드가 바뀌는 작업(이미지 재빌드)에는 이 성질이
적용되지 않는다.

**CLI/API 계층 중복 없음(구조 건강)**: `cli.py`(431줄)와 `routes.py`(261줄)는 로직을
중복하지 않고 동일 서비스 함수를 공유한다(`ensure_target`, `control_container`,
`list_standalone_backups`, `list_targets` 모두 양쪽에서 같은 함수 호출). 중복은 표현
계층(exit code vs HTTP detail)뿐이다. 의미: **"ktdctl→UI 이관"은 대부분 신규 route +
기존 서비스 함수 호출로 끝나며, CLI 리팩토링이 선행 조건이 아니다.**

---

## P0. UI 화면 지도 — "무엇이 Web UI가 되는가"의 한눈 답

| 화면/패널 | 상태 | 요구하는 신규 백엔드 |
|---|---|---|
| 컨테이너 테이블(상태·메트릭·로그·제어) | 현존 → **개선**(P7: 라벨 한국어화·그룹 뷰·미리보기) | 없음 |
| 설정 편집 모달(ports/env/networks) | 현존 → **개선**(P7-H 볼륨 읽기 전용화) | 없음 |
| 백업 이력 패널 | 현존 → **확장**(P5: freshness 배지·생성 버튼·복원 안내) | 생성 버튼만 job_runner 필요 |
| 인증 설정 패널(API 키·감사) | 현존 → **확장**(P6: 비밀번호 변경 폼) | `POST /admin/password` |
| Manager 자기 상태 카드 | **신규**(P7-D) | 없음(`GET /health` 기존) |
| 배포 정합성 패널(pin·generation·drift) | **신규**(P1-c, P2, P3) | 읽기 전용 route 2-3개 |
| 디스크 사용량 카드 | **신규**(P8b) | `docker system df` wrapper |
| CLI 명령 카드(전 CLI-전용 작업 공통) | **신규**(P7-E) | 없음 |

실행(mutation)이 UI로 들어오는 것은 백업 생성(P5)·비밀번호 변경(P6)·(승인 시) 2-step
pin 회전 요청(P1-c)뿐이고, 나머지는 전부 읽기 전용 관측이다. P9-3단계(프론트 구조
추출)의 착수 트리거 "신규 패널 2개 이상"은 이 표의 신규 4행 기준으로 센다.

---

## P1. 반복 pin 회전의 설정파일화 — churn의 구조적 해법 (v2 신규, 최상위 후보)

### 문제의 크기

최근 200 커밋 중 42개가 "pin"을 언급한다. `pinned_runtime_release.py`는 도입 이후 총
23커밋을 겪었다. 회전 1회의 실측 diff(최근 회전 커밋들 `--stat` 확인): 의미상
**40-hex 값 1-2개** 변경이 → `pinned_runtime_release.py`(revision + digest) +
`map_application_300.py`(Map 회전 시 중복 상수 1줄) + 테스트 1-2개 파일의 하드코딩
기대값(`db77fcd`에서는 두 파일 합계 86줄) + journal/tasks 기록 = **전형 5개(4-6개) 파일
수정 + PR + rsync 재배포 + 백엔드 재기동**으로 증폭된다. prod는 `.git` 없는 rsync
배포본이라 pin이 코드 상수인 한 회전마다 전체 배포 사이클이 강제된다.

### 하드코딩 전수 조사 결과

churn하는 하드코딩은 정확히 네 줄이다:
- `services/pinned_runtime_release.py:169` — Map pinned revision
- `services/pinned_runtime_release.py:174` — PinVi pinned revision
- `services/pinned_runtime_release.py:183` — `pinset_sha256`(위 2개에서
  `canonical_pinset_sha256()`(`:94-101`)로 계산 가능한 파생값을 리터럴로 재기재)
- `services/map_application_300.py:26-28` — `MAP_APPLICATION_300_SOURCE_COMMIT`
  (**Map revision과 같아야 하는 중복본**)

churn하지 **않는** 인접 상수(전환 대상 아님): canonical URL 2개(도입 후 불변),
`PINNED_RUNTIME_RELEASE_VERSION`(구조 버전), d9 legacy 상수(역사적 고정값),
`APPLICATION_HEAD = "300"`(도입 후 무변경), schema 계약 문자열. PinVi/Dagster schema
head는 하드코딩이 아니라 candidate 명령으로 동적으로 읽는다는 것이
`compose_service.py:6048-6049`의 주석에 명시돼 있다. **ride-along 후보는 없다** —
churn하는 것은 SHA 3종(실질 2종)뿐이다.

### 현행 구조의 독립적 결함 2건 (usability와 무관한 전환 근거)

1. **동일 SHA의 이원 관리**: `MAP_APPLICATION_300_SOURCE_COMMIT`과
   `MAP_PINNED_RUNTIME_SOURCE.revision`은 같아야 하지만 런타임 비교 코드가 없다 —
   테스트 한 줄이 유일한 방어선이고, 어긋나면 rebuild가 candidate
   admission(`map_application_300.py:471-476`)에서야 fail한다. 단일 registry로 합치면
   hazard 자체가 소멸한다.
2. **파생값의 수동 재기재**: `pinset_sha256`은 계산 가능한 값인데 사람이 손으로 돌려
   붙여넣는다. `__post_init__`(`:141-145`)의 재계산 대조가 실수를 잡아주긴 하지만,
   "digest 수동 갱신"은 비전문 관리자에게 가장 실수 잦은 단계다.

### 설계 (a) — pin registry 파일 (root 소유 0600, self-describing)

```json
{
  "schema": "kor-travel-docker-manager.runtime-pin-registry.v1",
  "release_version": 5,
  "sources": [
    {"role": "map",   "url": "https://github.com/digitie/kor-travel-map.git", "revision": "<40-hex>"},
    {"role": "pinvi", "url": "https://github.com/digitie/pinvi.git",          "revision": "<40-hex>"}
  ],
  "rotated_at": "...", "rotated_by": "...", "reason": "...",
  "pinset_sha256": "<rotate 시 자동 계산되어 기록>"
}
```

- 로딩은 기존 선례를 그대로 탄다: `services/registry.py:16-35`가 이미 env 오버라이드
  + 구조 검증 + `lru_cache`로 `config/docker-targets.yml`에 동일한 일을 한다.
  `current_pinned_runtime_release()`(`pinned_runtime_release.py:187-190`)의 시그니처를
  유지한 채 내부만 파일 로드로 바꾸면 **rebuild 경로의 소비처는
  `compose_service.py:6018` 한 곳**이라 전파가 작다.
- **검증은 전부 유지**: `PinnedRuntimeSourceSpec.__post_init__`(`:56-63`)의 canonical
  URL 강제·40-hex 검증, `PinnedRuntimeRelease.__post_init__`(`:129-145`)의 role
  순서·digest 재계산 대조가 파일 파싱 직후 그대로 실행된다. **URL은 파일에 있어도
  코드의 `CANONICAL_RUNTIME_SOURCE_URLS`와 불일치하면 fail-close** — 파일로 옮겨도
  "임의 저장소를 가리키게 조작"은 코드 수정 없이는 불가능하다. 이것이 이 전환의 안전성
  핵심 논거다.
- `pinset_sha256`은 파일에 기록하되 로드 시 항상 재계산 대조(부분 편집·truncation 방어).
- `MAP_APPLICATION_300_SOURCE_COMMIT` 상수는 삭제하고 `release.source_for("map").revision`
  참조로 통합(결함 1 해소).

### 설계 (a′) — 배포·캐시·부트스트랩 (리뷰 지적 반영, 구현 착수의 전제)

- **파일 위치는 배포 트리 밖의 prod-local 경로다.** prod는 `backend/src/`만 rsync하는
  배포본이고 보존 파일 목록을 별도 관리한다 — registry 파일이 배포 트리 안이면 재배포가
  회전 결과를 덮어 P1의 존재 이유가 무너진다. `services/registry.py:17-20`의 env
  오버라이드 선례를 그대로 써서 `KTDM_RUNTIME_PINS_FILE`로 경로를 지정하고(개발 기본값은
  저장소 내 `config/runtime-pins.json`, prod는 `/opt/...` 밖의 운영 경로), 런북의 보존
  파일 목록에 등재한다.
- **캐시 무효화**: `registry.py`의 `lru_cache` 선례는 "불변 파일" 전제라 그대로 쓸 수
  없다. 로드 시 mtime+digest 검사로 재로드하거나 캐시 없이 매 호출 로드한다(rebuild
  시작 시 1회 + 조회 API뿐이라 성능 무의미). 따라서 **`pin rotate`는 실행 중 Manager에
  재기동 없이 즉시 반영된다** — 이것이 현행 대비 핵심 개선점이므로 명시한다.
- **부트스트랩과 부재 시 동작**: 최초 1회 `ktdctl pin init`(현행 코드 상수 값으로 파일
  생성)을 제공하고, 이후 **파일 부재·파싱 실패·digest 불일치는 전부 fail-close**다(상수
  폴백 없음 — 폴백이 있으면 "파일이 진실"이라는 단일성이 깨진다).

### 설계 (b) — `ktdctl pin` 서브커맨드 패밀리

- `ktdctl pin init --confirm` — 최초 부트스트랩(위).
- `ktdctl pin show [--json]` — registry 내용 + digest + 회전 메타. 읽기 전용.
- `ktdctl pin verify [--json]` — digest 재계산 대조 + canonical URL 대조. 읽기 전용.
- `ktdctl pin rotate --role map|pinvi --revision <40-hex> --reason "..." --confirm` —
  검증 → digest 자동 계산 → atomic write → 이전 파일을
  `runtime-pins.<old-digest>.json`으로 보존(회전 이력 = 롤백 소스) → backend용
  world-readable 사본 갱신(아래 (c)) → journal 기록. root 요구(rotate하는 사람은
  어차피 rebuild도 root로 실행).
- `ktdctl pin rollback --to <pinset-digest> --confirm` — 보존 파일로 원복. **현재는
  존재하지 않는 기능**(git revert + 재배포가 유일한 롤백)이며 config 전환의 순수 이득.

비전문 관리자 관점 핵심: "GitHub에서 SHA 복사 → 명령 하나 → digest 자동 계산"이 현재의
"5-6개 파일 편집 + digest 수동 계산 + 테스트 기대값 갱신 + PR + rsync + 재기동"을
대체한다.

### 설계 (c) — API/UI: 읽기 주체가 둘이므로 로더도 둘이다

- **root 로더**(rebuild 경로): (a)의 registry 파일을 직접 읽는다 — 소비처
  `compose_service.py:6018`.
- **backend 로더**(조회 API): registry는 root 0600이라 backend가 못 읽으므로,
  `pin rotate`/`init`의 atomic 시퀀스가 **secret 없는 world-readable 사본**(내용 전부
  공개 저장소의 commit SHA + 메타)을 backend가 읽을 수 있는 경로에 함께 쓴다. 사본에도
  `pinset_sha256`을 넣어 backend 로더가 재계산 대조하고, 사본 부재·stale(원본과 digest
  불일치 판단 불가 시)에는 `unknown`으로 fail-close 표시한다.
- `GET /api/v1/runtime-pins` — role별 revision, pinset digest, 회전 메타, 그리고
  **현재 committed generation의 digest와의 일치 여부**(P2와 결합: "registry엔 X, 살아
  있는 generation은 Y = 회전 후 rebuild 대기 중"을 그대로 보여준다).
- **쓰기(UI rotate 버튼)**: 1단계에서는 만들지 않는다 — registry가 root 소유인 한 API
  프로세스는 물리적으로 쓸 수 없고, 이 경계가 가장 값싼 안전장치다. 오너가 원하면
  2단계로 "UI는 회전 **요청**을 audit row로 기록(`api/admin.py:54-63`의 감사 패턴)하고
  실제 적용은 `ktdctl pin apply-pending --confirm`(SSH)"의 2-step 승인 모델이 가능하다.

### 트레이드오프 (정직하게)

**잃는 것**: PR review가 곧 pin 승인이던 암묵적 게이트 / git 이력 = pin 이력 / 테스트가
현재 pin 값을 고정하는 성질 / 코드=배포본 단일성.
**보상**: `--confirm`+root+reason 필수+journal·audit(실측상 최근 회전 PR은 사실상 1인
셀프 머지라 리뷰 게이트는 명목적이었다) / digest-명명 이전 파일 보존(롤백은 오히려
개선) / 테스트를 "값 고정"에서 "구조·digest 계산 검증"으로 재작성 — **회전 시 테스트
churn 자체가 소멸** / 로드 시 digest 재계산 + `pin verify` + 부재 시 fail-close.
**남는 실질 손실 1건**: 파일이면 rebuild 도중 교체가 이론상 가능하다 — 단 rebuild는
시작 시 release를 한 번 읽고(`compose_service.py:6018`) 전 과정이 그 digest로 키잉된
journal에 결박되므로, 도중 교체는 다른 digest의 별개 상태 공간으로 갈라질 뿐 진행 중
작업을 오염시키지 않는다. generation/journal 정합 모델은 무손상이다
(`pinned_runtime_sources.py:137-140`, `pinned_runtime_generation.py:314-334`의 digest
키잉은 digest 출처와 무관).

---

## P2. 이미 존재하는 상태의 노출 — release·manifest·journal (v1 1순위 확장)

v1의 1순위(generation manifest 노출)를 유지하되, v2 재조사에서 같은 계열의 더 싼
항목과 정정이 추가됐다.

1. **`GET /api/v1/pinned-runtime/release` — 노출 비용 최저(0-IO).**
   `current_pinned_runtime_release()`(`pinned_runtime_release.py:187`)는 순수 in-code
   상수 + `to_payload()` 직렬화까지 이미 있다. pin 회전이 지배적 chore인데 **현재 pin이
   뭔지 보는 방법이 소스코드 열람뿐이다**(SSH도 아닌 git checkout 필요). 파일 IO조차
   없어 캐시 불요. **P1 채택 시 이 endpoint는 `GET /runtime-pins`(P1-c)로 대체·병합
   된다** — P1보다 먼저 만들면 P1 시점에 소폭 재작업이 있음을 우선순위 배치에 명시한다.
2. **manifest + rebuild journal 통합 노출 — `GET /api/v1/pinned-runtime/generation`**
   (응답 키: `manifest` / `journal` / `summary`). `read_manifest`
   (`pinned_runtime_generation.py:2810`)에 더해 v1이 놓친
   `read_rebuild_journal`(`:2818`)도 노출한다 — journal은 실패/진행 중 rebuild의 phase를
   담는 durable JSON으로, 비전문 운영자에게 "지난 재구축이 어디까지 갔고 왜 멈췄나"는
   성공한 세대(manifest)보다 오히려 더 실용적이다.
   **환경 스코프 정정(리뷰 반영)**: 정식 경로 도우미 `pinned_runtime_state_paths()`
   (`:314`)는 내부에서 `require_rebuildable_mode`(`:327`)를 호출해
   `rehearsal/rebuildable` 조합이 아니면 예외를 던진다. 즉 이 패널의 주 무대는
   rehearsal 환경(현재 n150의 운용 모드)이며, 조회 route는 mode 게이트가 없는 읽기
   전용 진입점 `pinned_runtime_state_root()`(`:282`)를 기반으로 별도 구성해야 하고,
   상태 디렉터리가 없는 호스트에서는 "세대 기록 없음"을 정직하게 표시한다.
3. **평이한 언어 요약 계층은 설계 요건이다(v1 정정).** 실제 phase 값은
   `application_bootstrap_intent_durable`, `databases_recreated` 같은 내부 상태기계
   이름이고 image_ids는 sha256 다이제스트다 — 그대로 노출하면 비전문가에게 무의미하다.
   요건: **"재구축 진행 중 (n/전체 단계) / 정상 커밋됨 / 운영자 개입 필요"** 수준의
   한국어 요약 배지(`summary` 키) + 원시 값은 접힌 상세로. 이 요약 계층이 곧
   "rebuild-pinned 진행 관측 UI"다 — 실행자는 SSH에서 CLI를 돌리고, 관측자는 화면에서
   진행을 본다(실행자와 관측자가 다른 사람일 수 있다).

## P3. source 상태·정합성 — `source-status` (v1 설계 유지, 프레이밍 변경)

v1의 통합 설계(단일 `ktdctl source-status` + `GET /api/v1/source-status`)를 유지한다.
세부 사실관계(실행 이미지에 OCI revision label이 없어 non-raising wrapper와 `unknown`
경로가 필요, redaction 재사용, 절대 경로 비노출, TTL 캐시+수동 새로고침+single-flight)도
전부 유지. v2 변경점:

- **목적 재정의**: "감사"가 아니라 **"지금 뭐가 돌고 있나"를 사람 말로 보여주기**다.
  MATCH/DRIFT/unknown 영어 토큰과 raw SHA 비교는 비전문가에게 무의미하다 —
  **"최신 상태입니다 / 업데이트가 필요합니다(재구축 요청) / 확인할 수 없습니다"** +
  다음 행동 안내로 번역하고, SHA는 접힌 상세로 둔다.
- Geo/Concierge sibling checkout의 `git rev-parse` + clean/dirty, Manager 자신의
  `--self`(trusted installer의 provenance 기록 확장 필요 — 순수 개선이므로 채택 권고)도
  v1 그대로.
- 일반형 GitHub pull+build(Geo/Concierge/Manager 자신)는 **영구 범위 밖 결정** 유지.
  근거(v1에서 이월): Manager는 이 세 저장소의 자격증명을 갖지 않으며, "fetch해서
  신뢰"가 아니라 "검증하고 거부"가 이 프로젝트의 일관된 원칙이다.

## P4. git 이력 조회 — 대체안: GitHub compare 링크 (v1 설계 전면 교체)

v1은 `diff-pinned`(별도 scratch mirror + fetch)를 설계했다. v2 재검토 결론:
**비전문가는 commit diff를 읽지 않으며, diff를 호스트에서 계산할 필요가 애초에 없다.**
pinned SHA(P1/P2로 노출)와 실행 중 revision(P3)이 있으면
`github.com/<repo>/compare/<old>...<new>` **외부 링크만 렌더링**하면 된다 — fetch 0회,
신규 백엔드 0줄, 브라우저에서 GitHub이 diff를 보여준다.

v1의 scratch-mirror 설계는 "오프라인/air-gap 환경에서 diff가 필요해지는 경우"의
예비안으로만 남긴다(현재 니즈 없음). 예비안 요약(재론 시 상세 재설계 전제): 기존
pinned mirror는 per-pinset·단일 revision fetch·root 0700·rehearsal 전용이라 재사용
불가 → Manager 소유 별도 scratch bare에 양쪽 revision을 fetch하되 CLI/SSH 트리거
전용(HTTP 핸들러 금지), 기존 `_run_root_git` 하드닝 재사용 + `fetch.fsckObjects`
추가, fetch마다 journal 기록. GitHub egress 자체는 `rebuild-pinned`가 이미 수행 중이라
쟁점은 "처음 허용"이 아니라 "트리거 표면 확대"다.

## P5. 백업 (v1 대폭 개정 — usability-first 재배치)

### create — "백업 버튼"은 비전문가 관리도구의 핵심 기능 (승격)

v1은 "정책 결정 선행" 군에 뒀다. v2 판정: **주간·비상시 반복 작업 중 UI화 효용이 가장
큰 항목**이며, v1이 든 선행 과제 2개는 "하지 않을 이유"가 아니라 "구현 명세"다:

- **비동기 job 인프라(신규)**: 현재 저장소에 job 추상화가 전무하다 — `routes.py` 전
  핸들러가 sync `def`이고 create는 기본 timeout 14,400초(geo 실측 879초~22분)다.
  최소 설계: `services/job_runner.py` — job id → `{kind, state, started_at,
  result|error}`, `asyncio.create_task(asyncio.to_thread(fn))` 실행(기존
  `metrics_collector.py:19-23`·`main.py:129-148` lifespan 패턴 조합), kind별
  single-flight는 `_role_lock`(`standalone_backup.py:315`) 재사용. API:
  `POST /api/v1/backups/{role}` → 202 + job id,
  `GET /api/v1/backups/{role}/jobs/{id}` 폴링(경로에 role을 포함해 `/backups/{role}`과의
  세그먼트 충돌 회피), 기존 status WS에 완료 이벤트 편승. 핵심 효용: **"버튼 누르고
  브라우저 닫아도 되는 백업"**.
- **UID/ACL 결정(배포 설정)**: backend가 만드는 dump 경로와 운영자 cron gc 경로의
  분리 문제(`docs/docker-management.md` 명시)는 배포 설정 결정 하나로 풀린다 —
  선택지는 열린 질문 Q2에 정리했다.
- UI: role 선택 + **"geo는 수 시간이 걸릴 수 있습니다"** 예고 + 경과 표시(role별 실측
  소요가 이미 문서에 있어 예상 시간 하드코딩 가능) + 실행 중 버튼 비활성.
  가드레일은 일반 확인 다이얼로그면 충분하다 — create는 파괴적이지 않다.

### freshness 배지 — 백엔드 0줄 (v2 신규)

`scripts/run-standalone-backup.sh:7-11`이 cron 주기를 확정해 두었는데
`BackupHistoryPanel`은 생성 시각만 표시하고 신선도 판단이 없다 — "마지막 백업이 26시간
전이면 cron이 죽은 것"을 운영자가 암산해야 한다. **기존 `GET /api/v1/backups` 응답만으로
프론트 단독 구현 가능**: role별 기대 주기 상수 + "마지막 백업 N시간 전" + 임계 초과 시
경고색. "crontab 설치 여부" 감지 자체는 신규 코드 + UID 문제가 있어 보류 — freshness
배지만으로 실질 효용의 대부분을 얻는다.

### gc — CLI 전용 유지 (v1 결론 유지, 근거는 usability 기반)

usability 관점에서도 승격하지 않는다: gc는 cron 성격의 chore이지 화면에서 즉흥적으로
누를 일이 아니고, restore 부재 상태에서 유일 사본 삭제 버튼은 "실수 복구 불가 = 최악의
UX"다. UI 대체: `BackupHistoryPanel`에 보존 개수·최고령 표시 + 복사 가능한
`ktdctl db-backup gc` 명령 카드(→ P7-E).

**gc 결함 수선(한 곳에서 확정)**: `gc_standalone_backups`(`standalone_backup.py:
277-299`)의 `_role_lock` 부재(4시간 create와 경합 가능)와 orphan `.dump`
미수거·manifest 내용 기반 오삭제 가능성은 **UI 노출 여부와 무관한 독립 버그 수정**이다
(lock은 3줄). job_runner(P9 2단계)에 편승해 함께 고치면 되고, job_runner를 하지 않아도
단독 착수 가능하다. 이 문서에서 이 수선을 언급하는 다른 절은 모두 이 문단을 가리킨다.

### restore — 정정: "CLI 전용"이 아니라 "부재" (v1 오기 수정)

restore는 CLI에도 없다(`routes.py:100-101` "Restore isn't implemented anywhere yet").
create 버튼을 만들수록 restore 부재가 더 위험해진다 — 버튼이 안전감의 착시를 만든다.
최소 조치(프론트 전용): `BackupHistoryPanel`에 "복원은 아직 미구현 — 절차는 runbook
참조" 명시 + 백업 행에 향후 복원 명령 원형 복사 버튼. 구현 순서는 CLI 먼저(v1 유지),
로드맵 중요도는 승격. 규모 추정: `standalone_backup.py`(573줄)와 대칭인 restore 서비스
+ CLI 서브커맨드로 **대략 300-500줄 + role별 정지/기동 절차 설계**가 필요하다 — 이
문서의 다른 항목(~150-200줄)보다 한 단계 큰 투자다.

## P6. 설정/secret 변경 (v1 분할 개정)

### 관리자 비밀번호 변경 폼 — UI로 승격 (v2 신규 분리)

v1은 secret rotate 전체를 CLI로 묶었다. v2 판정: **"관리자 비밀번호 변경"은 모든 웹앱의
표준 UX이고 없는 쪽이 이상하다.** 분리 근거: `KTDM_ADMIN_PASSWORD_HASH`는
`verify_admin_password`가 **호출 시마다 `os.environ`에서 읽으므로**
(`auth_service.py:72` — 코드베이스 유일 읽기 지점), API 핸들러가 자기 프로세스의
`os.environ`을 갱신하면 재기동 없이 즉시 적용된다. 세션 검증은 password hash를 건드리지
않으므로 진행 중 세션도 죽지 않는다 — v1의 self-lockout 분석(session secret 대상)이
그대로 적용되지 않는다.

설계: `POST /api/v1/admin/password` — 현재 비밀번호 재검증(그 자체가 typed
confirmation) → 새 hash 생성(`hash_password_for_env`, `auth_service.py:52`) → `.env`
atomic 갱신 + `os.environ` 동시 갱신 → audit row(`admin.py:54-63` 감사 패턴).

**P1 경계 논거와의 관계(리뷰 지적 반영, 정직하게)**: P1-(c)는 "API 프로세스가 registry
파일을 물리적으로 못 쓴다"를 안전장치로 드는데, 이 항목은 같은 API 프로세스에 **`.env`
쓰기 능력**을 부여한다 — `.env`는 machine secret 전부가 든 파일이므로 이는 실질적 경계
완화다. 완화를 한정하는 조건: (i) 쓰기는 **`KTDM_ADMIN_PASSWORD_HASH` 단일 키
allowlist**로 제한하고 임의 key=value 쓰기는 구현하지 않는다(경로·파일 바이트 사전조건
검증은 `pinvi_database_role_credentials.py`의 **검증 로직만** 차용 — 함수 재사용 금지,
v1 경고 유지). (ii) prod `.env`의 소유·퍼미션이 backend 쓰기를 허용하는 구성인지가
전제조건이며 구현 시 확인한다. (iii) 이 완화로도 pin registry는 여전히 root 전용이다 —
P1의 경계 논거는 "backend가 어떤 파일도 못 쓴다"가 아니라 "pin은 못 쓴다"로 유지된다.

### 나머지 secret 회전 — CLI 전용 유지

usability 렌즈로 재도출해도 결론은 같다: session secret 회전은 "적용 = 전 관리자 세션
즉시 무효화 + SSH 재기동"이라 **UI 버튼을 눌러도 그 자리에서 완결되지 않는 작업**이고,
machine secret(PinVi role password, API token)은 비전문가가 화면에서 회전할 동기
자체가 없다(값을 쓰는 곳이 전부 기계다). v1 설계 유지: `ktdctl secret rotate` CLI 전용,
human/machine 클래스 구분(machine은 절대 비출력, human은 TTY 1회 출력), 회전과 재기동
분리, T-045 프로토타입(`ktdctl map-ui-auth rotate`) 기반. UI는 "재기동 대기 중"
다이제스트 비교 상태 표시 + **해소 명령(SSH 재기동 명령 원형) 복사 버튼**(P7-E)까지.

## P7. 비전문가 직관성 개선 — 프론트 전용 quick wins (v2 신규 절)

v2 조사에서 확인된, **백엔드 0줄로 가능한** 개선 목록. 어느 것도 v1에 없었다. 현
대시보드는 "관측은 친절, 조작은 전무"이며 그 관측조차 개발자 어휘다.

- **A. 오류 humanize.** `apiJson`이 실패 response body 원문을 그대로
  `ApiError.message`에 넣고(`frontend/src/lib/api.ts:194-195`) `DashboardClient`가
  `alert()`로 띄운다(`:563`, `:566`, `:580`, `:583`) — 비전문가가 raw JSON
  `{"detail":{"code":"COMPOSE_CANDIDATE_..."}}`를 브라우저 alert로 본다. detail
  code→한국어 설명 매핑 + "자세히" 접기로 원문 보존 + toast/panel 교체.
  `ContainerDetailModal.tsx:203-208`이 이미 모범 패턴(raw detail 숨기고 평이한 문구)
  이므로 전역화하는 것뿐이다.
- **B. 라벨 한국어화.** 상태 칸이 raw 영어(`running`/`not_created`,
  `DashboardClient.tsx:948-950`)인데 같은 화면 KPI는 한국어("실행 중")라 어휘가
  이중이다. role 칸도 내부 식별자(`map-api`, `metrics-exporter`) 그대로 —
  `getContainerPresentation`(`:157-193`)에 이미 한국어 표시명이 있다. 백업 role 필터
  (`geo_dagster` 등)와 열 이름(`alembic`, `SHA-256`)도 설명 라벨로.
- **C. target 그룹 뷰.** 21개 컨테이너가 평면 테이블 하나다. `GET /targets`가 의존
  구조를 이미 반환하므로 앱 단위(지오코더/컨시어지/지도/PinVi/공용 인프라) 섹션 접기 +
  섹션 헤더에 "모두 정상 / 1개 중지됨" 요약이 프론트 전용으로 가능하다.
- **D. Manager 자기 상태 카드.** `GET /health` 표시(인벤토리 참고).
- **E. 복사 가능한 CLI 명령 카드.** CLI 전용으로 남는 모든 작업에 "이 명령을 SSH에서
  실행" 원형 복사 카드를 둔다 — **CLI-전용 정책과 usability를 동시에 만족하는 최저비용
  수단**이며 이 문서의 CLI-전용 결정 전부에 공통 적용한다. 대상: `db-backup gc`,
  (향후) restore, secret rotate, `rebuild-pinned`, `pin rotate`, Manager 재기동,
  그리고 **sibling 앱 이미지 갱신**(Geo/Concierge 등의 `ensure --build` — P8 참고).
- **F. mutation 미리보기 전역화.** 이미 두 곳에 모범 패턴이 있다 —
  `ContainerDetailModal.runEnsure`의 영향 서비스 나열 confirm(`:93-109`)과 설정 모달의
  변경 diff 미리보기(`DashboardClient.tsx:1669-1713`). 이를 stop/restart(현재 확인
  없이 즉시 실행, `:548-550`)와 reset(문구뿐인 confirm, `:1720`)에 확장 — stop은 "의존
  서비스 N개 영향"을 이미 로드된 targets 데이터에서 계산 가능.
- **G. 기존 API 파라미터의 UI 노출.** 메트릭 기간(`hours` 지원하나 UI는 1 고정,
  `:594`), 로그 tail 선택/복사, 커맨드 팔레트 확충(현재 4항목, `:792-817`).
- **H. 볼륨 필드 읽기 전용화.** 편집 input과 `+ 추가` 버튼이 활성이지만
  (`:1521-1560`) 서버가 불변 계약으로 거부한다 — 현재는 편집 후에야 경고가 뜬다
  (`:1563-1568`의 사후 경고). "편집 후 경고"를 "처음부터 읽기 전용 렌더"로 바꾼다.
- **I. target 단위 일괄 재시작.** start/stop/restart가 컨테이너 1개씩뿐 — "geo 전체
  재시작"은 클라이언트 순차 호출로 시작 가능(각 호출이 기존 C6c 락 통과), 영향 목록
  확인 다이얼로그 필수. 신규 엔드포인트 불요이므로 0군에 배치한다.

## P8. 제외 확정 항목 (v1 결론 재확인)

- **`image rebuild-service`** — 제외 유지. 정직한 usability 비용 명시: 이 결정으로
  **Geo/Concierge/공용 인프라의 이미지 갱신은 UI에 어떤 경로도 없이 남는다**(수용된
  격차). 기각의 실제 근거는 안전(unpinned 배포·rollback 부재)과 정확성(`--no-deps`가
  `init_steps`를 조용히 건너뛰어 반쪽 기동 — 이것 자체가 나쁜 UX)이다. usability
  보완재: P3의 "업데이트 필요" 표시가 "언제 CLI를 돌려야 하는지"를 알려주고, P7-E
  카드가 실행할 `ensure --build` 명령 원형을 제공한다.
- **`rebuild-pinned` production 실행 버튼** — 제외 유지. 단 v1의 "절대 금지"에서 두
  갈래를 분리했다: (i) **진행 관측 UI는 만든다**(P2-3의 요약 계층 — v1은 이 관측 뷰를
  제안하지 않은 것이 공백이었다). (ii) 서버가 이미 `rehearsal/rebuildable` 조합이
  아니면 거부하므로 **rehearsal 환경 한정 버튼**은 기존 `IS_DEV` + 서버 거부 이중
  패턴으로 성립 가능하다 — 채택 여부는 Q5. production에서 필요한 것은 버튼이 아니라
  전제조건 체크리스트(락 상태·pinned SHA·generation phase — 전부 P1/P2 데이터) +
  붙여넣을 SSH 명령(P7-E)이다.
- **`compose-boundary` 3종** — 1회성 레거시, 투자 보류(무변경).
- **일반형 GitHub pull+build** — 영구 범위 밖(무변경, 근거는 P3).
- **Manager 자기 재기동** — 기술적 필연으로 SSH 전용(무변경). "재기동 필요" 감지 시
  정확한 SSH 명령을 표시(P7-E).

## P8b. 교차 요건·승격 항목 (제외 아님 — 활성 tier의 출처)

- **Docker disk-usage 카드 — 승격**: 비전문가가 시스템을 죽이는 가장 그럴듯한 경로가
  "디스크 참"이다. `docker system df` wrapper 신규(현재 저장소에 호출 0건) + KPI 카드
  + 임계 경고(85% 등). TTL 캐시·수동 새로고침·single-flight 전제(v1 유지). raw
  수치보다 "정리 시 약 N GB 확보 가능" 요약을 우선한다.
- **감사 로깅 공통 전제**(v1 유지): 이 문서의 신규 mutation 전부(`pin rotate`, backup
  create, password change)에 `admin.py:54-63` 패턴의 durable audit row.
- **Push 알림** — Grafana로 해결, 범위 밖(v1 유지, Grafana가 Manager를 실제 스크레이핑
  하는지는 미검증 전제로 명시).

---

## P9. 구조 리팩토링 평가 (v2 신규 절)

**결론: 전면 리팩토링 불필요.** UI 로드맵을 막는 것은 두 god-module(compose_service.py
7,503줄 / c6c_deployment.py 7,675줄)의 "크기"가 아니라 4개의 구체적 결손이다:
(1) job runner 부재, (2) DashboardClient 단일 컴포넌트(195-1770줄이 한 함수 —
useState 22개, raw WS effect 2개, 인라인 모달 4개), (3) pin이 소스코드 상수(→ P1),
(4) read-only facade 함수 몇 개의 부재. god-module 내용 대부분(rebuild 오케스트레이션
약 1,290줄 단일 메서드 `:6014-7307`, smoke 검증기 약 2,000줄)은 UI 로드맵이 **건드릴
필요가 없는** 코드다. 서비스 생태계는 이미 21개 모듈로 잘 추출돼 있고
(`standalone_backup.py` 573줄, `pinned_runtime_release.py` 190줄 등), 문서의 신규
엔드포인트가 필요로 하는 함수는 대부분 그 작은 모듈들에 있다.

**점진 계획 — 5단계, 각각 단독 배포 가능:**

1. **읽기 전용 facade + route**: `services/deployment_status.py` 신설 —
   manifest/journal 읽기(mode 게이트 없는 `pinned_runtime_state_root()` 기반, P2-2의
   스코프 정정 참조) + `inspect_c6c_image_source_revision`의 non-raising wrapper.
   P2·P3 해제. ~200줄 신규, 기존 코드 무변경.
2. **`services/job_runner.py`** + backup create 202-비동기 노출. gc 결함 수선(P5)도
   이때 편승. ~150줄 신규.
3. **프론트 추출**: status/log WS effect → 훅 2개, 인라인 모달 4개 → 파일 분리(기존
   `BackupHistoryPanel` 선례 패턴). 동작 무변경 순수 이동, DashboardClient 잔여
   ~600줄. **신규 패널이 2개 이상 되기 전에 선행**(P0 표의 신규 4행 기준) — 지금
   구조에 더하면 2,300줄+로 가고 ESC-스택 effect(`:285-302`)에 분기가 계속 늘어난다.
4. **pin registry 데이터화**(P1). 소비처 1곳이라 파급 최소.
5. **(선택, 로드맵 무관) god-module 기계적 분할**: rebuild 오케스트레이션 →
   `pinned_runtime_orchestrator.py`, smoke 검증기 → `c6c_smoke.py`. **UI 로드맵의 어떤
   항목도 이 단계를 선행조건으로 요구하지 않는다** — 유지보수성 투자로만 정당화되므로
   맨 뒤이고, 생략해도 로드맵은 완주된다.

안전 트레이드오프: 1·3·4단계는 위험 중립~감소(4단계는 "코드 수정으로 pin 회전"이라는
현재의 오류 유발 경로를 검증된 데이터 경로로 대체). 2단계만 실질 트레이드오프 — 장시간
mutation의 HTTP 트리거화이며 UID/ACL 결정(Q2) 선행. 5단계는 15k 라인 이동이라 리뷰
부담 대비 UI 효용이 0이다.

---

## 우선순위 권고 (v2 — usability-first 재정렬)

**0군 — 프론트 전용, 백엔드 0줄 (즉시 착수 가능, 정책 결정 불요)**
1. 오류 humanize + 라벨 한국어화 (P7-A·B) — 코드 변경 최소, 체감 최대.
2. 백업 freshness 배지 (P5) / Manager health 카드 (P7-D) / target 그룹 뷰 (P7-C).
3. 복사 가능한 CLI 명령 카드 (P7-E) — CLI-전용 정책 전체의 usability 보완재.
4. mutation 미리보기 전역화 (P7-F) / 볼륨 필드 읽기 전용화 (P7-H) / 기존 파라미터
   노출 (P7-G) / target 단위 일괄 재시작 (P7-I).

**1군 — 저비용 백엔드 read-only (리팩토링 1단계와 함께)**
5. `GET /pinned-runtime/release` (P2-1, 0-IO). **주의: P1(9번) 채택 시
   `GET /runtime-pins`로 대체되는 소폭 재작업이 있다** — Q1을 먼저 결정하면 이 항목은
   건너뛰고 9번에서 한 번에 만든다.
6. `GET /pinned-runtime/generation`(manifest+journal+요약, P2-2·3) = rebuild 진행
   관측 UI.
7. `source-status` + "사람 말" 정합성 패널 + GitHub compare 링크 (P3·P4).
8. disk-usage 카드 (P8b) / installer provenance 기록 (P3 `--self` 전제).

**2군 — 구조 투자 (오너 정책 승인 후)**
9. **pin registry 설정파일화 + `ktdctl pin` 패밀리 + `GET /runtime-pins`** (P1) —
   시간 절감 총량 최대. [Q1 승인 시]
10. job_runner + 백업 create 버튼 (P5, 리팩토링 2단계). [Q2 승인 시]
11. 관리자 비밀번호 변경 폼 (P6). [Q3 승인 시]
12. 프론트 구조 추출 (리팩토링 3단계 — P0 표의 신규 패널 2개 이상 시점 전에).
    — Q4 승인 시 "UI 2-step pin rotate"가, Q5 승인 시 "rehearsal 한정 rebuild 버튼"이
    이 군에 추가된다.

**3군 — CLI 전용 유지 / 제외 (v1 결론 유지분)**
13. secret rotate(비밀번호 외 전부) — CLI 전용 + 상태 표시 + 해소 명령 카드 (P6).
14. `db-backup gc` — CLI 전용(결함 수선은 P5의 단일 문단 참조).
15. `db-backup restore` — 부재 명시 + CLI 우선 로드맵 (P5). [Q6 승인 시 로드맵 편입]
16. `image rebuild-service` / `compose-boundary` / 일반형 pull+build /
    production rebuild 버튼 — 제외 (P8).

## 오너가 결정할 열린 질문 (v2)

1. **P1 pin registry 전환을 승인하는가.** 42/200 커밋 chore의 구조적 해법. 승인 시
   되돌리기 어려운 변화 요약: "PR review = pin 승인" 게이트가 CLI(`--confirm`+root+
   reason+audit)로 대체되고, git 이력 대신 digest-명명 보존 파일이 pin 이력이 되며,
   테스트가 현재 pin 값을 고정하는 성질이 사라진다(대신 회전 시 테스트 churn 소멸).
2. **백업 create의 UI화(job_runner)를 투자하는가.** 부속 배포 결정 — dump 경로의
   소유권을 (i) backend와 cron을 같은 UID로 통일 또는 (ii) shared group + setgid
   디렉터리 중 택일해야 한다. 권고: (ii)가 기존 프로세스 구성을 안 바꾼다.
3. **관리자 비밀번호 변경 폼**(P6)을 승인하는가 — backend에 `.env` 단일 키 쓰기를
   부여하는 경계 완화를 수반한다(P6에 명시).
4. [Q1이 "예"일 때만] UI 2-step pin rotate(요청 기록 + CLI 적용, P1-c)까지 갈
   것인가, 읽기 전용까지만 할 것인가.
5. rehearsal 환경 한정 `rebuild-pinned` 버튼(P8)을 둘 것인가 — 현재 n150이
   `rehearsal/rebuildable` 모드로 운용 중이므로 이 버튼은 실호스트에서 동작하는
   버튼이다(성립 조건이 이미 충족돼 있다는 뜻이자, 그만큼 실효 위험도 실재한다).
6. restore를 로드맵에 넣는가 — CLI 우선 전제, 규모는 대략 300-500줄 + role별
   정지/기동 절차 설계(P5의 추정 참조).
7. CLAUDE.md의 낡은 두 지점(퇴역한 `pinvi-pair capture`·T-045 언급) 동기화를 별도
   작업으로 처리하는가(v1에서 이월).
