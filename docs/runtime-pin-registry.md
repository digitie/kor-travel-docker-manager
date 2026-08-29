# Runtime pin registry — 에이전트 레퍼런스

**대상 독자**: 이 저장소에서 작업하는 에이전트(Claude Code, Codex, Antigravity 등)와 운영자.
**정본 관계**: 결정 근거는 [ADR-40](decisions.md), 운영 절차는
[`prod-deployment.md` 2.1](prod-deployment.md), CLI/API 요약은
[`docker-management.md` 5.1](docker-management.md), 설계 맥락은
[`ktdctl-ui-migration.md`](ktdctl-ui-migration.md) 1부. **이 문서는 그 사이를 잇는
"코드를 고치기 전에 반드시 알아야 할 계약" 모음이다.**

구현: `backend/src/kor_travel_docker_manager/services/runtime_pin_registry.py`,
회전 요청 2-step은 `services/runtime_pin_request.py`(§7-1)
회귀: `backend/tests/test_runtime_pin_registry.py`,
`backend/tests/test_runtime_pin_request.py`, `backend/tests/test_docker_manager_cli.py`

---

## 0. 30초 요약

Map·PinVi를 **어느 커밋으로 재구축할지**를 고정한 값(pin)이 예전에는 Python 상수였다.
지금은 root 소유 JSON registry 파일이 그 값을 소유하고, 코드는 **계약만** 소유한다.
registry는 현재 pin뿐 아니라 **pinset의 생애 상태**(재시도 금지 목록, 회전 이력)도 담으며,
`rebuild-pinned`는 재시도 금지 pinset에 대해 **어떤 mutation보다 먼저 거부**한다.

```
값(어떤 커밋인가)      → registry 파일이 소유       → M05는 ktdctl pin rotate-pair 로 함께 바꾼다
계약(무엇이 유효한가)  → 코드가 소유                → 코드를 고쳐야 바뀐다
생애(실행해도 되는가)  → registry + 코드 하한선     → ktdctl pin block / rotate --block-previous
제안(무엇을 바꾸고 싶은가) → 별도 요청 파일이 소유  → UI가 기록, root가 apply-pending (§7-1)
관측(어디까지 이행됐나) → root 공개 사본이 소유     → GET /pinned-runtime/generation (§7-a)
```

M05 isolated PinVi runtime을 기동할 때도 이 소유 경계를 우회하지 않는다. Manager root driver는
private `0700` runtime directory에 `0600` admission을 만들고 exact transaction ID, current
pinset, Manager·Map·PinVi source revision을 함께 쓴다. PinVi `docker-app.sh`는 caller environment
marker가 아니라 이 admission을 no-follow로 검증한 경우만 `m05i-pinvi-<transaction>` Compose
mutation을 허용한다. Manager는 env file에 쓰는 값과 별개로 exact source revision·admission path·pinset을
clean child environment로 전달하고, PinVi는 root EUID에서 `/usr/bin/python3 -I`를 clean environment로
실행한다. 따라서 direct Compose·수동 root marker·PATH shim·환경변수는 Manager의 `rotate-pair`,
registry, one-shot ledger를 대체하지 못한다.

M05 isolated one-shot이 실패하면 root registry의 조건 없는 차단 항목에는 allowlist 고정 phase만
기록한다. HTTP body·status 원문, container log, 환경값, 파일 경로, 예외 문자열은 기록하거나 다음
candidate의 재시도 근거로 쓰지 않는다. 이 phase는 새 source pair를 만들 때 보정 범위를 정하는 용도일
뿐, 동일 pinset을 다시 실행하게 하는 권한이 아니다.

ordinary exception도 같은 원칙을 따른다. driver 본문은 이미 진입한 allowlist phase를 유지하고,
cleanup·terminal block 경계는 각각 `runtime_cleanup_failed`·`runtime_pin_block_failed`로 수렴한다.
allowlist 밖의 값만 `driver_contract_failed`로 좁혀진다. 따라서 공개 registry로 raw 예외를 역추론할
수 없고, 다음 immutable source pair의 보정 범위만 결정할 수 있다.

immutable admission mapping은 private JSON write 경계에서 plain mapping으로 복사한 뒤 canonical JSON으로만
직렬화한다. 따라서 불변 typed admission의 `MappingProxyType` 자체가 JSON serializer로 새지 않으며, 이
경계는 fixture가 아닌 실제 private writer 회귀로 고정한다.

isolated runtime 준비는 `runtime_setup_ports` → `runtime_setup_workspace` →
`runtime_setup_admission_build` → `runtime_setup_admission_write` → `runtime_setup_network` → `runtime_setup_credentials` →
`runtime_setup_map_config` → `runtime_setup_pinvi_config`의 고정 순서로 나뉜다. 이 값은 예외 원문·경로·값을
드러내지 않으며, 이미 terminal인 pinset의 재시도 근거가 아니다. 목적은 다음 immutable Manager source의
정적 보정 범위를 한 단계로만 좁히는 것이다.

`run-m05-isolated-e2e-once`의 strict result schema는 이 일곱 setup phase를 포함한 driver의 모든 public
terminal phase를 `completed` 외 exact 같은 집합으로 수용한다. 이 동등성은 source literal AST 회귀로 고정하므로,
새 driver phase는 launcher contract를 함께 바꾸지 않으면 CI에서 거부된다.
`blocked` receipt는 exact source revision·launch 전후 같은 snapshot·고정 schema를 모두 만족해도, root registry가
같은 pinset·Map·PinVi revision의 unconditional terminal block을 확인할 때만 launcher가 보존한다. registry 증명이
없거나 receipt가 어긋나면 launcher는 idempotent `ktdctl pin block`과 fixed fallback으로 fail-close한다. 그러므로
driver의 blocked receipt는 단독으로 재실행 권한이나 성공 근거가 될 수 없고, 안전한 allowlist phase가 임의
fallback으로 바뀌지 않는다.

### 단발 실행의 완료 판정

`run-pinned-rebuild-once`와 `run-m05-isolated-e2e-once`는 실행권을 claim한 뒤 host-global mutation lock을
프로세스 수명 동안 보유한다. SSH client가 즉시 종료됐거나 출력 회수가 지연됐다는 사실은 실패·종료·차단의
증거가 아니다. **별도 외부 호출은 lock이 보유된 동안** 같은 pinset의 rebuild/E2E 재실행과 모든 runtime
pin 변경(`pin init`, `publish-generation`, `rotate`, `rotate-pair`, `apply-pending`, `rollback`, `block`)을
하지 않고 안전한 lock 상태만 기다린다. 이 명령들은 active lock이면 write 전에 코드로 거절된다.
launcher 자신은 검증한 driver 종료 뒤 상속 받은 같은 lock descriptor 안에서만 terminal block을 기록할 수 있다.
외부 호출은 lock이 해제된 뒤에만 root `ktdctl pin verify --json`의 exact Map/PinVi/pinset,
`published_copy=current`, generation binding `match`를 확인해 완료를 판정한다.

이 절차는 private output leaf, `result.json`, stderr, HTTP·container·환경 원문을 읽지 않는다. 공개 gate가
`match`면 호출 결과와 무관하게 rebuild는 완료된 것이며, 그 뒤에만 새 root-owned leaf에서 M04/M05 one-shot을
한 번 실행할 수 있다. `match` 전에는 terminal block을 쓰지 않는다. 이미 terminal로 block된 pinset은 이
판정과 관계없이 재실행하지 않고 fresh source pair로만 교체한다.

M05 one-shot launcher는 leaf와 one-shot ledger를 만들기 전에 M05 integration adapter의 비소비
source-materialization pair preflight를 반드시 통과한다. 이 검사는 generic registry가 소유하지 않는 PinVi provenance의
Map full revision·OpenAPI hash와 Manager-only admission contract만 대조한다. 따라서 `pin verify`가
generic pin/execution/generation gate를 통과했어도, 문서 commit 등을 runtime Map revision으로 잘못 회전한
pair는 E2E terminal이나 Compose mutation을 소비하지 않고 거부된다. pair가 통과한 뒤의 runtime failure만
current v6 execution terminal로 기록한다. 단 one-shot ledger claim은 `O_EXCL` create 뒤 fsync 오류에도
durable file을 남길 수 있으므로, claim 호출을 시작한 뒤의 오류는 성공 반환 여부와 무관하게 terminal로
수렴한다. launcher가 fallback 없이 수용하는 `preflight_rejected` receipt는 strict pre-claim phase와
`cleanup_failed:false`만 허용한다.

---

## 1. 절대 깨뜨리면 안 되는 불변식

에이전트가 이 영역을 고칠 때 아래를 어기면 **운영 사고이거나 교차 저장소 계약 파손**이다.

### 1-1. pinset digest의 바이트 계약은 kor-travel-map과 공유한다

`canonical_pinset_bytes()` / `canonical_pinset_sha256()`
(`services/pinned_runtime_release.py`)의 직렬화 규칙 — 정렬된 키, compact separator,
`ensure_ascii=True`, `{"sources":[...],"version":5}` 형태 — 은
kor-travel-map의 `scripts/lib/c7_prod_attestation.py`가 결박한 값과 일치해야 한다.
**한 바이트도 바꾸지 마라.** 바꾸면 map의 production attestation이 전부 실패한다.

회귀: `test_pinset_digest_algorithm_is_pinned_to_a_literal`(리터럴 고정),
`test_pinset_digest_uses_stable_canonical_compact_json`(바이트 레이아웃 고정).

### 1-2. generation manifest(v6)·rebuild journal(v8) 문서에 키를 추가하지 마라

같은 map attestation이 이 두 문서를 **exact-dict**로 검증한다. 키가 하나라도 늘거나
줄면 map 쪽이 fail-close한다. 가공(요약·번역·배지)이 필요하면 **API 응답 envelope에만**
넣고 문서 자체는 그대로 통과시킨다. 문서 스키마를 바꿔야 한다면 map 저장소의 동시 PR
없이는 불가능하다.

**2026-08-28 교차 저장소 정렬**: v8 journal은 16키이며 Map
`scripts/lib/c7_prod_attestation.py`도 같은 exact dict를 검증한다. 추가된 세 키는
`pinvi_role_credential_environment_rebind`, `pinvi_role_catalog_reset`,
`pinvi_role_lifecycle_block`다. committed journal에서는 catalog reset이 `completed`이고
lifecycle block은 `null`이어야 한다. credential rebind가 있으면 현재 environment/Compose
digest와 다시 결박한다. 이 규칙은 Manager PR #254와 Map PR #1112가 함께 적용하는 계약이며,
둘 중 하나가 병합되지 않은 release를 M05 generation gate에 쓰지 않는다.

### 1-3. canonical URL은 코드가 공급한다

registry 파일에 `url` 필드가 있지만, 로드할 때 코드의
`CANONICAL_RUNTIME_SOURCE_URLS`와 대조해 다르면 거부한다. 이 대조를 느슨하게 만들면
"파일을 편집해 임의 저장소를 가리키게 만들 수 있다"가 되어 파일 이관의 안전 논거가
통째로 무너진다.

회귀: `test_noncanonical_source_url_in_the_file_is_rejected`.

### 1-4. API 프로세스는 registry를 쓰지 않는다

registry는 root `0600`이다. UI에서 회전이 필요하면 **요청을 기록하고 root가 적용하는
2-step**으로 간다(KUM-M5, §7-1). backend가 registry를 직접 쓰게 만드는 변경은 넣지 마라.

> **주의 — "backend는 비-root라 물리적으로 못 쓴다"는 논거는 이 배포에서 성립하지
> 않는다.** n150 운영 배포는 `.env`가 root `0600`이라 backend를 `sudo -n`으로 띄운다
> (`deploy-runbook.local.md` §3-3, 실제 프로세스 uid 0으로 확인). 그 호스트에서 uid
> 경계는 없다. 실제로 강제되는 보호는 둘이다:
>
> 1. **HTTP 계층에 쓰기 경로가 없다.** 회귀
>    `test_the_http_layer_never_mutates_the_pin_registry`가 `api/` 전체를 훑어
>    `rotate_runtime_pin`·`write_runtime_pin_registry` 등 mutator 참조를 금지한다.
> 2. **`apply-pending`이 요청에서 role과 40-hex revision, 표시용 문자열만 취한다.**
>    URL·digest·차단 목록은 코드와 root registry에서 다시 만든다(§7-1).
>
> 비-root로 돌리는 호스트에서는 여기에 파일 권한 경계가 하나 더 얹힐 뿐이다. 설계
> 논거를 권한에만 걸지 마라.

요청 저장소(`services/runtime_pin_request.py`)는 **pin이 아니다.** 어떤 로드 경로도 그
파일을 읽지 않으며, 회귀 `test_the_request_store_is_not_a_pin_source`가
`pinned_runtime_release`·`compose_service` 소스에 그 모듈 이름이 등장하지 않는다는
사실로 이를 결박한다. 이 테스트가 깨지면 요청 파일이 authority가 된 것이므로 고치지
말고 되돌려라.

### 1-5. 차단 하한선은 파일이 아니라 코드가 소유한다

`_CODE_ENFORCED_BLOCKED_PINSETS`는 registry가 손상되거나 오래된 사본으로 시딩돼도
유지되는 최소 차단 집합이다. **`to_payload()`에 넣지 마라** — 파일에 적히는 순간
사람이 지울 수 있는 값이 되어 하한선이 아니게 된다.

### 1-6. 실패는 전부 fail-close다. 상수 폴백은 없다

파일 부재·파싱 실패·digest 불일치·소유권 위반은 예외를 던진다. "값을 모르면 기본값으로
진행"하는 경로를 만들면 안 된다. 조회 API도 값을 추측하지 않고 `unknown`을 반환한다.

### 1-7. generation 공개 사본은 private state를 읽는 우회로가 아니다

v6 manifest와 v8 rebuild journal은 root 소유 private state(`0700` 디렉터리·`0600` 파일)에
남는다. backend가 그 경로를 직접 읽거나 권한을 완화해서는 안 된다. `write_manifest`와
`write_rebuild_journal`은 typed model 검증 뒤 **같은 raw JSON**을 `0644` 공개 경로에 원자
복제하고, 기존 상태를 한 번 공개해야 할 때만 root가 다음 명령을 쓴다.

```bash
sudo -n backend/.venv/bin/ktdctl pin publish-generation \
  --manifest <root-owned-pinned-runtime-generation-v6.json> \
  --journal <root-owned-pinned-runtime-rebuild-v8-<pinset>.json> \
  --confirm
```

공개 파일은 Map exact-dict attestation과 같은 schema를 보존한다. public root 자체와 즉시
부모는 root(개발에서는 실행 uid) 소유·group/other 비쓰기여야 하며, public root는 symlink
없이 `0755`여야 한다. publisher는 같은 no-follow directory FD에서 검사·원자 교체한다.
사람용 상태·terminal
분류·다음 행동은 파일이 아니라 `GET /api/v1/pinned-runtime/generation`의 envelope에만
있다. custom `KTDM_PINNED_RUNTIME_STATE_ROOT` 배포는 읽기 가능한 별도 절대 경로를
`KTDM_PINNED_RUNTIME_PUBLIC_ROOT`로 함께 지정해야 하며, 위 소유권·권한 규칙을 만족하지
않으면 publisher와 reader가 모두 fail-close한다.

---

## 2. 데이터 모델

파일 스키마: `kor-travel-docker-manager.runtime-pin-registry.v1`

```json
{
  "schema": "kor-travel-docker-manager.runtime-pin-registry.v1",
  "release_version": 5,
  "sources": [
    {"role": "map",   "url": "https://github.com/digitie/kor-travel-map.git", "revision": "<40-hex>"},
    {"role": "pinvi", "url": "https://github.com/digitie/pinvi.git",          "revision": "<40-hex>"}
  ],
  "pinset_sha256": "<64-hex, 로드마다 재계산 대조>",
  "rotated_at": "2026-08-28T00:00:00Z",
  "rotated_by": "<사용자>",
  "reason": "<자유 텍스트 — world-readable 사본에 그대로 실린다>",
  "history": [
    {"pinset_sha256": "...", "rotated_at": "...", "rotated_by": "...",
     "reason": "...", "supersedes_pinset_sha256": "..."}
  ],
  "blocked_pinsets": [
    {"pinset_sha256": "...", "map_revision": "...", "pinvi_revision": "...",
     "reason": "...", "blocked_at": "...", "phase": "<선택>"}
  ]
}
```

파싱 규칙 (전부 strict):
- 최상위·`sources[]`·`history[]`·`blocked_pinsets[]` 모두 **미지 필드 거부**.
- `sources`는 정확히 2개, 순서는 `map` → `pinvi` 고정.
- `release_version`은 `5`가 아니면 거부(`_SUPPORTED_RELEASE_VERSION` — 순환 import를
  피하려 값을 복제했고 `test_supported_release_version_mirror_matches_the_release_module`이
  동일성을 고정한다).
- `pinset_sha256`과 **각 `blocked_pinsets[]` 항목의 digest**를 자기 revision으로
  재계산 대조한다. 차단 항목의 digest가 어긋나면 그 항목은 어떤 journal에도 매치하지
  않아 "차단했다고 기록됐지만 아무것도 막지 못하는" 조용한 무력화가 된다.
- `history`는 500건 초과 시 오래된 것부터 버린다(감사 기록). **`blocked_pinsets`는
  절대 버리지 않는다** — 초과는 fail-close다(가장 오래된 terminal이 조용히 빠지면
  그 candidate가 다시 실행 가능해진다).

---

## 3. 차단(block)의 두 가지 의미 — 가장 틀리기 쉬운 지점

| 종류 | `phase` | 무엇을 막나 | 누가 판정하나 |
|---|---|---|---|
| **조건 없는 차단** | 없음 | 그 pinset의 **legacy source 실행** | `compose_service._assert_pinset_is_not_permanently_blocked` (모든 rebuild에서 current v6 execution 결박·terminal을 확인하고, legacy terminal은 추가 감사 근거로 쓴다) |
| **phase 한정 차단** | 있음 | 그 phase 상태의 **journal 재개만** | `pinned_runtime_release.is_blocked_pinset_retry` → resume admission |

두 술어를 섞으면 안 된다:
- `is_unconditionally_blocked_pinset(digest)` — `phase is None` 항목만 본다. **시작
  게이트와 API의 `current_pinset_is_blocked`가 쓰는 술어.**
- `is_blocked_pinset(digest)` — phase 무관. **회전·rollback이 "차단된 곳으로 가지
  않는다"를 판정할 때만** 쓴다(그 판단에는 phase가 의미 없다).
- `blocked_entry_for(...)` — journal 식별자 4종(pinset+map rev+pinvi rev+phase)으로
  매치. resume admission이 쓴다.

d9 계열 historical 항목이 phase 한정인 이유: 그 candidate의 **특정 중단 지점 재개만**
막던 원래 의미를 보존하기 위해서다. 시작 게이트가 여기까지 막으면 과차단이 된다.

---

## 4. 경로 해석 규칙

| 상황 | registry | 공개 사본 |
|---|---|---|
| `KTDM_RUNTIME_PINS_FILE` / `KTDM_RUNTIME_PINS_PUBLIC_FILE` 설정 | 그 값 | 그 값 |
| 설치 root(`/opt/kor-travel-docker-manager`)에서 실행 | `/var/lib/kor-travel-docker-manager/runtime-pins.json` | `/var/lib/kor-travel-docker-manager-public/runtime-pins.json` |
| 그 외(개발 체크아웃) | `<repo>/config/runtime-pins.json` (gitignored) | `<repo>/.ktdm-runtime-pins.json` (gitignored) |

**왜 이렇게 갈라지나**

- 배포 트리 안에 registry를 두면 **다음 release 설치가 회전 결과를 조용히 되돌린다.**
  trusted installer는 트리를 staging→commit으로 통째 교체하기 때문이다. 그래서 설치
  root에서는 env 없이도 기본값이 트리 밖을 가리키고, **트리 안 경로로의 회전·부트스트랩은
  거부된다**(`_assert_registry_is_writable_target`).
- 공개 사본이 **별도 트리**인 이유: installer가 `/var/lib/kor-travel-docker-manager`를
  매 설치 `0700 root:root`로 되돌리므로, 그 안에 두면 비-root backend가 traverse조차
  못 해 조회 API가 영구 `unknown`이 된다(n150 실측).
- `config/runtime-pins.seed.json`은 **추적되는 읽기 전용 seed**다. `pin init`의 기본
  입력이며 **회전 대상이 아니다**(쓰기 시도는 거부된다).

---

## 5. 파일 무결성 규칙

읽을 때마다 `_assert_registry_file_integrity`가 확인한다:

1. `lstat`으로 본다 — **symlink를 따라가 다른 파일을 읽지 않는다.**
2. 일반 파일이어야 한다.
3. 소유자는 **root이거나 이 프로세스 자신**이어야 한다.
   → root가 사용자 소유 seed를 신뢰 입력으로 읽는 것도 여기서 막힌다. 설치본은 트리
   전체가 root 소유라 정상 경로에서는 만족한다.
4. group/other **쓰기** 권한이 있으면 거부한다. `0600`(운영)·`0644`(개발 체크아웃)는
   통과, `0664`·`0666`은 거부.
5. stat 실패는 통과가 아니라 **거부**다.

**개발 완화**: Windows 공유 마운트(WSL drvfs)는 모든 파일을 `0777`로 보고해 4번을
만족할 수 없다. `KTDM_RUNTIME_PINS_ALLOW_INSECURE_MODE=1`로 **mode 항목만** 완화할 수
있고, **소유자 검사는 완화되지 않으며 `euid == 0`에서는 이 완화 자체가 무효다**(파괴적
작업을 수행하는 주체가 완화 대상이 되면 완화가 곧 구멍이다). 테스트는
`backend/tests/conftest.py`가 이 값을 설정한다.

---

## 6. CLI 계약

전부 `ktdctl pin <sub>`. mutation은 `--confirm` 없이는 아무것도 쓰지 않는다.

| 명령 | 성격 | 요지 |
|---|---|---|
| `pin init [--seed PATH] [--reason R] [--force] --confirm` | mutation | 호스트 최초 1회. `--seed` 기본값은 설치본의 `config/runtime-pins.seed.json`. 기존 파일이 있으면 `--force` 없이는 거부하고, `--force`여도 **이력·차단 목록을 승계하고 이전 상태를 digest 이름으로 보존**한다 |
| `pin show [--json]` | 읽기 전용 | 현재 pin·digest·회전 메타·차단 목록·최근 이력. 현재 pinset이 조건 없이 차단됐으면 legacy source terminal임을 평문으로 경고하고, 현재 실행권은 `pin verify`로 확인하도록 안내한다 |
| `pin verify [--json]` | 읽기 전용 | digest·canonical URL·registry 공개 사본과 v6/v8 generation 및 v6 execution 공개 사본의 strict parse를 함께 확인한다. incomplete/malformed/drift generation, missing/stale execution, 또는 terminal current execution이면 exit 1이다. legacy v5 terminal은 exact current·미차단 v6 execution이 있을 때만 단독으로 exit 1 사유가 아니다. pair 회전 직후의 유효한 이전 committed generation 또는 exact unconditional terminal generation은 `pending_rebuild`로 보고하되 current라고 부르지 않는다 |
| `pin publish-generation --manifest PATH --journal PATH --confirm` | mutation, **root 전용** | 검증된 private v6 manifest·current v8 journal을 `0644` 공개 사본으로 원자 복제한 뒤 strict reader와 current registry pair까지 다시 검증한다. 경로는 절대 경로만 허용하며, API가 root state를 직접 읽는 우회로는 만들지 않는다 |
| `pin rotate --role map\|pinvi --revision <40-hex> --reason R [--block-previous] --confirm` | mutation | digest 자동 계산, 이력에 `supersedes` 기록, 이전 파일을 `runtime-pins.<old-digest>.json`으로 보존, 공개 사본 갱신. 아무것도 바뀌지 않는 회전과 **차단된 pinset을 만들어 내는 회전은 거부** |
| `pin block <pinset-sha256> --reason R [--map-revision] [--pinvi-revision] [--phase] --confirm` | mutation, **root 전용** | terminal 판정 pinset 등재. 현재 pinset이면 revision 인자 생략 가능, 다른 pinset이면 두 revision 필수 |
| `pin rollback --to <pinset-sha256> --reason R --confirm` | mutation | 보존본으로 원복. **차단된 pinset으로는 원복하지 않는다** — 무제한 rollback은 교차 저장소의 "terminal 재시도 금지" 규약을 코드로 깨뜨리는 일이다 |
| `pin show-pending [--json]` | 읽기 전용 | UI가 남긴 대기 요청. 요청 이후 pin이 바뀌었으면 **먼저 그 사실을 경고**한다. 대기 요청이 없으면 exit 1. `--json`은 부재·손상 경로에서도 JSON만 stdout에 낸다 |

M05 one-shot 뒤 `pin verify --json`가 exit 1이고 `current_execution_is_blocked: true`이면, 이는
검증기 장애가 아니라 해당 v6 terminal execution의 재시도 금지 판정이다. legacy v5
`current_pinset_is_blocked: true`는 source audit으로 보존되며, `execution_binding: current`와
`current_execution_is_blocked: false`가 함께 있어야 새 trusted implementation의 one-shot이 가능하다.
`reason`은 root 운영자 자유 입력이므로 자동화가 원문을 해석하거나 비밀을 넣어서는 안 된다.

`GET /api/v1/pinned-rebuild/preflight`는 v5 source 상태와 무관하게 v6 execution의 private/public parity를
비-root UI에서 증명할 수 없으므로 `can_start=false`와 `ktdctl pin verify`만 안내한다. 따라서 UI가 v6
terminal을 보지 못하고 초록불을 주는 일이 없다. registry가 정상이어도 공개 generation이
`partial`·`malformed`·`unverified`·`drift`·`unknown`이면 같은 방식으로 `can_start=false`이고, 새 pair
회전 직후의 strict `pending_rebuild`와 current `match`만
preflight가 command를 제시할 수 있는 상태다.

모든 runtime pin mutation은 같은 host-global mutation lock을 nonblocking으로 획득하고, 변경 대상의 읽기·
검증·write·대기 요청 정리를 **한 lock 안에서** 끝낸다. 예외는 launcher가 검증한 상속 descriptor로 기록하는
terminal fallback 하나뿐이다. 따라서 외부 관찰자는 lock 해제 뒤
`pin verify --json`만 읽어 완료를 판정하며, lock 보유 중 어떤 pin 명령도 retry·block·pair 교체에 쓰지 않는다.

### 6-1. 후보 동결과 문서 전용 변경

M05 candidate는 Map·PinVi runtime source와 Manager source를 한 번 확정해 pinset을 계산한 시점에 동결한다.
그 뒤의 **문서 전용** Map/PinVi/Manager PR은 즉시 병합할 수 있으나, runtime source revision·PinVi
`source_revision`·registry pair·pinset을 다시 결박하지 않는다. 문서는 동결된 candidate를 참조만 한다.
코드·Compose·계약·빌드 입력처럼 runtime 결과를 바꾸는 변경만 새 candidate와 한 번의 CI/전문 리뷰/E2E를
필요로 한다. 이 규칙은 사소한 문서 정정마다 pair를 재생성해 one-shot과 CI를 낭비하는 일을 막는다.
| `pin apply-pending (--expect-revision R \| --any-revision) [--block-previous] --confirm` | mutation, **root 전용** | 대기 요청을 적용한다. §7-1의 순서대로 전부 재검증하고, 성공 시 요청 파일을 id 대조 후 삭제한다 |
| `pin clear-pending (--request-id <id> \| --force) --confirm` | mutation | 대기 요청을 폐기한다. id가 어긋나면 exit 1이며 **아무것도 지우지 않는다**. `--force`는 **읽을 수 없는** 파일만 파싱 없이 지운다(멀쩡한 요청은 거부) |

**`apply-pending`의 종료 코드** — 이 구분이 없으면 `|| echo "적용 안 됨"` 같은 스크립트가
pinset을 태워 놓고 "적용 안 됨"이라고 보고한다:

| 코드 | 의미 | registry |
|---|---|---|
| `0` | 적용 완료, 요청 파일도 정리됨 | 바뀜 |
| `1` | 대기 중인 요청이 없음 | 그대로 |
| `2` | 사전 조건 거부(`--confirm`·root·revision 미지정·base 불일치·차단·무변경) | 그대로 |
| `3` | **적용됐으나 요청 파일 정리 미완** — 수동 삭제 필요 | **바뀜** |

**`--expect-revision`이 사실상 필수인 이유.** 이 명령은 인자를 주지 않으면 "그 순간
파일에 들어 있는 것"을 적용한다. `show-pending`과 `apply-pending` 사이에 요청이 바뀔 수
있으므로, 무엇을 고정하는지 손으로 적었을 때(`--expect-revision`)만, 혹은 확인하지 않기로
명시했을 때(`--any-revision`)만 진행한다.

**`pin unblock`은 의도적으로 없다.** 해소 경로는 새 revision으로의 회전이다.

`--reason`은 world-readable 공개 사본과 인증된 API 응답에 **그대로** 실린다. 비밀을
적지 않는다.

---

## 7. API 계약 — `GET /api/v1/runtime-pins`

인증 필요(미인증 401). registry 자체는 **읽기만** 한다.

```jsonc
{
  "status": "ok" | "stale" | "degraded" | "unknown",
  "source": "published_copy" | "registry" | null,
  "detail": "<status != ok일 때 사람 말 설명>",
  "published_at": "...",
  "pins": { "release_version": 5, "pinset_sha256": "...", "sources": [...],
            "rotated_at": "...", "rotated_by": "...", "reason": "..." },   // unknown이면 null
  "lifecycle": {
    "current_pinset_is_blocked": false,              // phase 없는 차단만 계수
    "current_pinset_has_phase_scoped_block": false,  // phase 한정은 별도 필드
    "blocked_pinsets": [...],
    "history": [...]
  },
  "pending_request": null | {                        // §7-1. 없으면 null
    "status": "pending" | "stale" | "unreadable",
    "request_id": "...", "role": "map" | "pinvi", "revision": "<40-hex>",
    "reason": "...", "requested_by": "...", "requested_at": "...",
    "base_pinset_sha256": "...", "prospective_pinset_sha256": "..."
  },
  "summary": { "state": "ok" | "action_required" | "unverified",
               "text": "<한국어>", "next_action": "<SSH 명령 또는 빈 문자열>" }
}
```

**status의 의미 — 값을 추측하지 않는 것이 이 엔드포인트의 계약이다**

- `ok` — 공개 사본을 읽었고 registry와도 어긋나지 않는다.
- `stale` — 공개 사본이 registry보다 오래됐다(둘 다 읽을 수 있을 때만 판정 가능).
- `degraded` — 공개 사본이 없어 registry를 직접 읽었다. **그 파일이 이 호스트의 운영
  registry가 아니라 배포본에 딸려 온 개발 seed일 수 있으므로 권위 있는 값이라고 말하지
  않는다.**
- `unknown` — 둘 다 읽을 수 없다. `pins`는 `null`.

프론트(`components/RuntimePinPanel.tsx`)는 `ok`가 아닌 모든 상태를 "확인 필요"로
표시하고 복사 가능한 SSH 명령을 함께 준다.

---

## 7-a. API 계약 — `GET /api/v1/pinned-runtime/generation`

인증 필요(미인증 401). 이 route는 public copy만 다시 strict parse하며, private state
경로·원문 오류·환경값은 반환하지 않는다.

```jsonc
{
  "status": "ok" | "unverified" | "unknown",
  "source": "published_copy",
  "detail": "<unknown 또는 unverified일 때 고정 설명>",
  "manifest": {"version": 6, "active_generation": {"...": "..."}} | null,
  "journal": {"version": 8, "candidate": {"...": "..."}, "phase": "..."} | null,
  "pinset_binding": {"status": "match" | "pending_rebuild" | "drift" | "unknown",
                     "registry_pinset_sha256": "..." | null,
                     "generation_pinset_sha256": "..." | null},
  "terminal": null | {"class": "pinvi_role_lifecycle_block", "subclass": "...", "pinset_sha256": "..."},
  "summary": {
    "state": "committed" | "rebuilding" | "pending_rebuild" | "action_required" | "unverified" | "unknown",
    "text": "<한국어>", "next_action": "<SSH 명령 또는 빈 문자열>",
    "manifest_version": 6 | null, "journal_version": 8 | null
  }
}
```

`manifest`와 `journal`은 그대로의 raw document다. 두 파일의 교체 사이처럼 in-progress
상태에서 서로 다를 수 있으므로 API가 내용을 고쳐 맞추지 않는다. 두 문서가 서로 다른
generation이면 `status=unverified`로 raw 두 문서를 보존하되 gate는 fail-close한다. 하나만
유효하거나 둘 다 없으면 `unknown`이며 raw 문서를 반환하지 않는다. `summary`가 그 상태를
명확히 말하고, terminal은 journal의 typed lifecycle receipt가 있을 때만 고정 enum으로 나온다.
`pinset_binding`은 `GET /runtime-pins`의 current Map·PinVi pair와 비교한 결과다. 새 pair를
rotate한 직후 이전 committed **또는 registry가 Map·PinVi revision과 pinset까지 exact로 일치시킨
unconditional terminal** generation만 남은 경우는 `pending_rebuild`, 새 journal 후보가 current
pair와 다르면 `drift`, phase-scoped block뿐인 중단 또는 registry 공개 사본을 신뢰할 수 없으면
`unknown`/`drift`로 fail-close한다.

---

## 7-1. 회전 요청 2-step — UI는 제안하고 root가 적용한다

**왜 2-step인가.** UI에서 버튼 한 번으로 회전하려면 HTTP 계층에 registry 쓰기 경로가
생기고, 그러면 §1-4의 규칙이 무너진다. 반대로 회전을 CLI 전용으로 두면 화면에서
"바꾸려면 SSH로 가라"는 막다른 길만 보여 주게 된다. 2-step은 둘 다 피한다 — 화면은
무엇을 바꾸고 싶은지 기록하고, 실제 변경은 여전히 root 명령이 한다.

**저장 위치가 registry와 다른 트리인 이유.** `/var/lib/kor-travel-docker-manager`는
설치 때마다 `0700 root:root`로 재설정된다. 비-root로 도는 backend는 그 안에 쓸 수 없고,
root로 도는 backend라면 **쓸 수 있어서 더 문제다** — 요청이 registry 옆에 놓이면 두
파일의 권한 구분이 사라진다. 요청은
`/var/lib/kor-travel-docker-manager-requests/runtime-pin-requests.json`(설치 root에서
실행할 때의 기본값, 파일 `0600`)에 둔다. env `KTDM_RUNTIME_PIN_REQUEST_FILE`로 덮어쓸 수
있고, 개발 체크아웃에서는 `<repo>/.ktdm-runtime-pin-requests.json`이다.

**디렉터리는 설치가 만든다.** `/var/lib`는 root 소유 `0755`라 비-root backend가 그 아래에
디렉터리를 만들 수 없다. trusted installer가 `-requests` 트리를 `root:root 0700`으로
미리 만들고, backend를 비-root로 돌리는 호스트에서는 설치 후 소유자를 그 사용자로 바꾼다
(`sudo chown <backend-user>:<backend-group> /var/lib/kor-travel-docker-manager-requests`).
디렉터리를 group-writable로 만들면 안 된다 — 무결성 검사가 영구히 거부한다.

**요청 기록은 배타 생성이다.** "읽어 보고 없으면 쓴다"로는 두 관리자가(또는 한 관리자의
두 탭이) 경합할 때 나중 쓰기가 앞의 요청을 말없이 덮고 **둘 다 "기록됨"으로 감사에
남는다.** `O_CREAT|O_EXCL`로 커널이 승자를 정한다.

**손상된 요청 파일은 잠금이 된다.** 읽지 못하면 id를 알 수 없어 id 대조 삭제가 불가능하고,
파일이 있으니 새 요청도 받을 수 없다. 그래서 `pin clear-pending --force --confirm`이
있다 — **읽을 수 없는 파일만** 파싱 없이 지우며, 멀쩡한 요청에는 거부한다. 모든 손상
메시지는 전체 경로를 함께 낸다.

**왜 SQLite가 아닌가.** `database.py`의 DB 경로는 `__file__`에서 유도되고 env 오버라이드가
없다. 운영에서 backend는 소스 트리에서, `ktdctl`은 wheel이 설치된 site-packages에서 돌아
**같은 호스트에서 서로 다른 파일**을 연다. 사람이 읽는 감사 행은 그대로 SQLite에 남기되,
두 프로세스가 주고받는 pending request는 경로를 명시할 수 있는 파일이어야 한다.

### 요청 기록 — `POST /api/v1/runtime-pins/requests`

본문 `{role, revision, reason}`. 성공하면 `201`과
`{"status":"pending","request":{...},"next_action":"…apply-pending --confirm"}`.
거부는 전부 `409`(쓰기 불가만 `503`)이고 `detail`은 `{code, message, …}`다. **모든 거부도
감사 행으로 남는다** — 남지 않은 거부는 사후에 조사할 수 없다.

| code | 언제 | 왜 여기서 막나 |
|---|---|---|
| `RUNTIME_PINS_UNVERIFIED` | 공개 사본이 `ok`가 아님 | 권위 없는 값을 base로 삼으면 `apply-pending`이 반드시 거부한다 |
| `RUNTIME_PINS_MALFORMED` | `sources`가 map/pinvi 40-hex 쌍이 아님 | 형식이 깨진 값에서 유도한 digest는 의미가 없다 |
| `RUNTIME_PIN_UNCHANGED` | 그 role이 이미 그 revision | 바뀌는 것이 없는 요청은 형식 자체가 잘못된 것이다 |
| `RUNTIME_PIN_BLOCKED_TARGET` | 적용 후 digest가 차단 목록에 있음 | phase 무관으로 본다. 그 조합을 다시 고정하는 것 자체가 금지다 |
| `RUNTIME_PIN_REQUEST_EXISTS` | 이미 대기 요청이 있음 | 조용히 덮어쓰지 않는다. 무엇을 버릴지는 사람이 정한다 |
| `RUNTIME_PIN_REQUEST_NOT_WRITABLE` (503) | 요청 디렉터리에 쓸 수 없음 | 경로와 **한 번 실행할 `install -d` 명령**을 알린다. "운영자에게 확인하세요"는 그 운영자가 보고 있을 때 막다른 길이다 |
| `RUNTIME_PIN_REQUEST_UNREADABLE` (409) | 요청 파일이 손상됨 | `clear-pending --force`로 해소한다 |

`reason`은 개행을 거부한다(`422`). 여기서 통과시키면 CLI가 읽지 못해 요청이 영원히
적용되지 않는다.

### 요청 취소 — `DELETE /api/v1/runtime-pins/requests/{request_id}`

id가 디스크의 것과 다르면 `404 RUNTIME_PIN_REQUEST_NOT_FOUND`다. 없는 요청과 "그 사이
다른 요청이 들어옴"을 같은 응답으로 묶는 것은 의도된 것이다 — 오래된 브라우저 탭이 방금
들어온 남의 요청을 지우지 못하게 한다. **거부도 감사 행으로 남는다** — id를 바꿔 가며
두드리는 시도가 흔적 없이 지나가면 안 된다.

`GET /api/v1/runtime-pins`는 `status`가 `unknown`일 때도 `pending_request`를 낸다.
요청 파일의 가독성은 registry의 가독성과 무관하고, 그 상태에서 id를 볼 수 없으면 취소도
못 하기 때문이다.

### 적용 — `ktdctl pin apply-pending --confirm` (root)

요청에서 취하는 것은 **role과 40-hex revision, 표시용 문자열뿐**이다. canonical URL은
코드가, digest는 코드가 재계산하고, 차단 목록은 root registry와 코드 하한선에서 다시
만든다. 순서(앞의 단계가 실패하면 뒤는 실행되지 않는다):

1. `--confirm` 없으면 거부(아무것도 쓰지 않음).
2. root가 아니면 거부. registry 쓰기와 요청 파일 삭제를 함께 하므로 절반쯤 진행한 뒤에
   권한 부족을 알면 안 된다.
3. 대기 요청을 읽는다. 없으면 exit 1.
4. registry를 로드한다(fail-close).
5. `request.base_pinset_sha256 != registry.pinset_sha256`이면 거부하고 **요청을 남겨
   둔다.** 자동 폐기하면 무엇이 버려지는지 운영자가 보지 못한다.
6. digest를 재계산해 요청에 적힌 값과 대조한다.
7. `--expect-revision`이 있으면 대조한다.
8. 차단(phase 무관)·무변경 사전 점검.
9. `rotate_runtime_pin(...)` — `rotated_by`에 `<적용자><-<요청자>`, `reason`에 요청 id와
   요청 시각을 함께 남긴다. 누가 제안하고 누가 실행했는지가 한 행에 남아야 한다.
   길이가 상한을 넘으면 **출처가 아니라 사유 쪽을 줄인다** — 그냥 이어 붙인 뒤 뒤를
   자르면 500자짜리 사유 하나가 요청 id·요청자·시각을 통째로 밀어내 2-step의 감사
   가치가 사라진다.
10. 요청 파일을 **id 대조 후** 삭제한다. 그 사이 id가 달라졌으면 지우지 않고 exit 3
    (registry는 이미 바뀌었으므로 "할 일 없음"과 같은 코드를 쓰면 안 된다).

요청 파일을 root가 읽는 것은 이 시스템에서 root가 비-root의 파일을 읽는 유일한
지점이다. 그래서 `_open_verified_request_file`은 내용이 아니라 **누가 그 자리에 쓸 수
있었는가**를 본다 — `O_NOFOLLOW`로 열고(symlink 거부), 일반 파일이어야 하며, hardlink를
거부하고(`st_nlink == 1`), 파일과 부모 모두 group/other 쓰기가 금지되고, 소유자는 `0`
또는 부모 디렉터리 소유자여야 한다.

검사와 읽기가 **같은 fd** 위에서 일어나는 것이 중요하다. `lstat` 뒤에 경로로 다시 열면
그 사이에 파일을 바꿔치기할 수 있고, 그러면 "검사한 것"과 "읽은 것"이 달라진다.

---

## 8. rebuild와의 관계

```
ktdctl pinvi-pair rebuild-pinned --confirm
  └─ _require_pinned_runtime_rebuild_root()            root 강제
  └─ global mutation lock → pinned rebuild lock         rotate/block/install과 직렬화
  └─ current_pinned_runtime_release()                  lock 안에서 registry snapshot 로드 (없으면 fail-close)
  └─ _assert_pinset_is_not_permanently_blocked(digest) ★ 같은 snapshot의 current v6 execution을 검증하고, 없거나 terminal이면 거부; legacy terminal은 추가 감사 근거
  └─ (락 획득, env snapshot, source materialize, DB reset …)   ← 여기부터가 mutation
       └─ resume journal이 있으면
            _assert_pinvi_role_lifecycle_block_admission()
              └─ is_blocked_pinset_retry(...)          ★ phase 한정 차단 판정
```

★ 표시 지점이 게이트다. **시작 게이트는 락 획득보다도 앞이며**, 회귀
`test_rebuild_refuses_a_blocked_pinset_before_touching_anything`이 실제
`rebuild_pinned_runtime()`을 호출해 `materialize.assert_not_called()` /
`lock.assert_not_called()`로 그 순서를 결박한다. 이 호출을 뒤로 옮기거나 지우면 그
테스트가 깨진다 — 깨지면 고치지 말고 되돌려라.

---

## 9. 흔한 상황별 대응

### "rebuild-pinned가 거부됩니다"

항상 `ktdctl pin verify`로 v6 execution binding을 확인한다. `execution_binding: current`,
`current_execution_is_blocked: false`일 때만 새 trusted Manager implementation의 one-shot이
허용된다. execution이 없거나 stale·terminal이면 회전 또는 새 trusted Manager release rebind가
필요하다.

```bash
cd /opt/kor-travel-docker-manager
sudo -n backend/.venv/bin/ktdctl pin show           # 왜 차단됐는지 확인
sudo -n backend/.venv/bin/ktdctl pin rotate-pair \
  --map-revision <새 Map 40-hex> \
  --pinvi-revision <새 PinVi 40-hex> \
  --reason "<직전 candidate의 terminal 사유와 그것을 고친 revision>" \
  --block-previous --confirm
sudo -n backend/.venv/bin/ktdctl pin verify         # 0이면 재구축 가능
```

### "테스트가 registry file is missing으로 실패합니다"

`backend/tests/conftest.py`가 `KTDM_RUNTIME_PINS_FILE`을 seed로 고정한다. 개발자 셸에
이 env가 export돼 있으면 conftest가 덮어쓰지만, 다른 registry env를 쓰는 테스트를 새로
쓸 때는 `monkeypatch.setenv`로 격리하고 `clear_runtime_pin_registry_cache()`를 호출하라.

### "새 회귀 테스트에서 BlockedPinset을 만들면 digest 오류가 납니다"

차단 항목의 digest는 자기 revision으로 재계산 대조된다. 임의의 `"d" * 64`를 쓸 수 없다.
`test_runtime_pin_registry._consistent_digest(map_rev, pinvi_rev)` 헬퍼를 쓰라.

### "pin을 바꿨는데 API가 옛 값을 보여 줍니다"

공개 사본이 갱신되지 않은 것이다(`status: stale`). root로 `pin verify`를 실행해 확인하고,
사본을 다시 쓰려면 root pin 명령을 한 번 더 돌린다. **backend는 사본을 쓸 수 없다.**

### "어느 PinVi revision으로 회전해도 되나요"

아무 revision이나 되지 않는다. rebuild는 PinVi의
`infra/postgres/bootstrap-pinvi-runtime-role.sh`를 두 가지 특수 모드로 부른다
(`PINVI_ROLE_TOPOLOGY_VERIFY_ONLY`, `PINVI_ROLE_CATALOG_RESET_ONLY`와 그 permit/result
경로). **스크립트가 그 이름을 모르면 변수는 조용히 무시되고 일반 부트스트랩이 실행된
뒤** Manager가 결과를 읽고 fail-close한다 — 데이터는 안전하지만 의도하지 않은 실행이
한 번 일어나고 pinset 하나가 소모된다.

회전 전에 화면의 **재구축 사전 점검 → "고정된 PinVi revision의 역할 부트스트랩 계약"**
행을 본다(`GET /api/v1/deployment-readiness`의 `pinvi_role_bootstrap_modes`). 이 검사는
체크아웃 HEAD가 아니라 **고정 revision의 blob을 직접 읽는다**(`git show <rev>:<path>`) —
재구축이 실제로 쓰는 것이 그 tree이기 때문이다. revision이 로컬에 fetch돼 있지 않으면
`unknown`이며, 그것을 결손으로 보고하지 않는다.

### "UI에서 변경 요청을 남겼는데 아무것도 안 바뀝니다"

정상이다. 요청은 제안일 뿐이고 적용은 root가 한다(§7-1).

```bash
cd /opt/kor-travel-docker-manager
sudo -n backend/.venv/bin/ktdctl pin show-pending    # 무엇이 대기 중인지 확인
sudo -n backend/.venv/bin/ktdctl pin apply-pending --confirm
```

화면이 그 요청을 **"무효가 된 변경 요청"**으로 표시한다면 요청 이후 pin이 바뀐 것이다.
그 요청으로는 적용되지 않으므로 취소하고 다시 요청하거나
`ktdctl pin clear-pending --request-id <id> --confirm`으로 지운다.

### "대기 중인 요청을 읽지 못했습니다"

요청 파일이 손상됐거나 권한이 어긋난 상태다. id를 알 수 없어 화면에서는 취소할 수 없고,
파일이 있으니 새 요청도 받지 못한다. 파싱 없이 지운다:

```bash
sudo -n backend/.venv/bin/ktdctl pin clear-pending --force --confirm
```

이 명령은 **읽을 수 없는 파일만** 지운다. 멀쩡한 요청에는 거부하므로 실수로 남의 요청을
날릴 수 없다.

### "요청을 저장하지 못했습니다(503)"

요청 디렉터리가 없거나 backend 사용자가 쓸 수 없다. 응답에 실행할 명령이 함께 온다:

```bash
sudo install -d -o <backend-user> -g <backend-user> -m 0700 \
  /var/lib/kor-travel-docker-manager-requests
```

group-writable(`0770` 등)로 만들지 마라 — 무결성 검사가 그 디렉터리를 영구히 거부한다.

### "회전은 즉시 반영되나요?"

된다. 캐시는 mtime·size·inode 스탬프로 무효화되므로 실행 중 backend에 재기동 없이
반영된다. 회귀 `test_external_rotation_is_seen_without_an_explicit_cache_clear`가
외부 `os.replace` 상황으로 이 경로를 검증한다.

---

## 10. 이 영역을 고칠 때의 체크리스트

- [ ] digest 직렬화 규칙(§1-1)과 manifest/journal 문서 스키마(§1-2)를 건드리지 않았다.
- [ ] 새 fail-close 경로가 예외를 **삼키지** 않는다(`except: return False` 금지).
- [ ] 두 차단 술어(§3)를 목적에 맞게 골랐다.
- [ ] backend가 registry를 쓰지 않는다(§1-4).
- [ ] 요청 저장소가 authority로 승격되지 않았다 — 로드 경로에서 `runtime_pin_request`를
      import하지 않는다(§1-4, §7-1).
- [ ] 코드 하한선을 파일에 기록하지 않는다(§1-5).
- [ ] 새 회귀가 "값 고정"이 아니라 "성질"을 검증한다 — 정당한 회전으로 깨지는 단언은
      이 전환이 없애려던 churn을 되살리는 것이다.
- [ ] 실제 퍼미션·소유권이 걸린 변경은 n150 격리 live E2E로 확인했다(Windows 공유
      마운트에서는 mode 계약을 검증할 수 없다).
