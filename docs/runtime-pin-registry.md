# Runtime pin registry — 에이전트 레퍼런스

**대상 독자**: 이 저장소에서 작업하는 에이전트(Claude Code, Codex, Antigravity 등)와 운영자.
**정본 관계**: 결정 근거는 [ADR-40](decisions.md), 운영 절차는
[`prod-deployment.md` 2.1](prod-deployment.md), CLI/API 요약은
[`docker-management.md` 5.1](docker-management.md), 설계 맥락은
[`ktdctl-ui-migration.md`](ktdctl-ui-migration.md) 1부. **이 문서는 그 사이를 잇는
"코드를 고치기 전에 반드시 알아야 할 계약" 모음이다.**

구현: `backend/src/kor_travel_docker_manager/services/runtime_pin_registry.py`
회귀: `backend/tests/test_runtime_pin_registry.py`, `backend/tests/test_docker_manager_cli.py`

---

## 0. 30초 요약

Map·PinVi를 **어느 커밋으로 재구축할지**를 고정한 값(pin)이 예전에는 Python 상수였다.
지금은 root 소유 JSON registry 파일이 그 값을 소유하고, 코드는 **계약만** 소유한다.
registry는 현재 pin뿐 아니라 **pinset의 생애 상태**(재시도 금지 목록, 회전 이력)도 담으며,
`rebuild-pinned`는 재시도 금지 pinset에 대해 **어떤 mutation보다 먼저 거부**한다.

```
값(어떤 커밋인가)      → registry 파일이 소유       → ktdctl pin rotate 로 바꾼다
계약(무엇이 유효한가)  → 코드가 소유                → 코드를 고쳐야 바뀐다
생애(실행해도 되는가)  → registry + 코드 하한선     → ktdctl pin block / rotate --block-previous
```

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

> **⚠ 이미 어긋나 있다 (2026-08-28 확인).** map의 `_JOURNAL_KEYS`는 **13키**인데
> Manager의 `to_payload()`는 **15키**를 내보낸다 — 확장 키
> `pinvi_role_credential_environment_rebind`·`pinvi_role_lifecycle_block`가 값이
> `None`일 때도 항상 실리고 `write_rebuild_journal`이 그대로 기록한다. 즉 **지금
> Manager가 쓰는 journal은 map의 production attestation을 통과하지 못한다.** v8
> 도입 때 Manager만 확장한 결과다.
>
> 해소 경로는 둘뿐이고 **둘 다 map 저장소 변경을 수반한다**: (a) map의
> `_JOURNAL_KEYS`에 두 키를 추가하거나, (b) 두 키를 journal 문서 밖(별도 receipt
> 파일)으로 옮긴다. 그 전까지 회귀
> `test_rebuild_journal_emits_two_keys_the_map_attestation_currently_rejects`가
> **괴리의 범위가 넓어지는 것만** 막는다 — "괜찮다"고 말하는 테스트가 아니다.
> 확장 키를 더 넣지 마라.

### 1-3. canonical URL은 코드가 공급한다

registry 파일에 `url` 필드가 있지만, 로드할 때 코드의
`CANONICAL_RUNTIME_SOURCE_URLS`와 대조해 다르면 거부한다. 이 대조를 느슨하게 만들면
"파일을 편집해 임의 저장소를 가리키게 만들 수 있다"가 되어 파일 이관의 안전 논거가
통째로 무너진다.

회귀: `test_noncanonical_source_url_in_the_file_is_rejected`.

### 1-4. API 프로세스는 registry를 쓰지 않는다

registry는 root `0600`이고 backend는 비-root다. 이 물리적 경계가 가장 값싼 안전장치다.
UI에서 회전이 필요하면 **요청을 기록하고 root가 적용하는 2-step**으로 간다(KUM-M5).
backend가 registry를 직접 쓰게 만드는 변경은 넣지 마라.

### 1-5. 차단 하한선은 파일이 아니라 코드가 소유한다

`_CODE_ENFORCED_BLOCKED_PINSETS`는 registry가 손상되거나 오래된 사본으로 시딩돼도
유지되는 최소 차단 집합이다. **`to_payload()`에 넣지 마라** — 파일에 적히는 순간
사람이 지울 수 있는 값이 되어 하한선이 아니게 된다.

### 1-6. 실패는 전부 fail-close다. 상수 폴백은 없다

파일 부재·파싱 실패·digest 불일치·소유권 위반은 예외를 던진다. "값을 모르면 기본값으로
진행"하는 경로를 만들면 안 된다. 조회 API도 값을 추측하지 않고 `unknown`을 반환한다.

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
| **조건 없는 차단** | 없음 | 그 pinset의 **모든 실행** | `compose_service._assert_pinset_is_not_permanently_blocked` (rebuild 시작 게이트) |
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
| `pin show [--json]` | 읽기 전용 | 현재 pin·digest·회전 메타·차단 목록·최근 이력. 현재 pinset이 조건 없이 차단됐으면 **"rebuild가 거부됩니다"를 평문으로 경고**한다 |
| `pin verify [--json]` | 읽기 전용 | digest 재계산·canonical URL·공개 사본 정합. **현재 pinset이 차단됐거나 공개 사본이 current가 아니면 exit 1** — digest가 맞다는 이유로 0을 반환하면 운영자가 rebuild 직전에 잘못 안심한다 |
| `pin rotate --role map\|pinvi --revision <40-hex> --reason R [--block-previous] --confirm` | mutation | digest 자동 계산, 이력에 `supersedes` 기록, 이전 파일을 `runtime-pins.<old-digest>.json`으로 보존, 공개 사본 갱신. 아무것도 바뀌지 않는 회전과 **차단된 pinset을 만들어 내는 회전은 거부** |
| `pin block <pinset-sha256> --reason R [--map-revision] [--pinvi-revision] [--phase] --confirm` | mutation | terminal 판정 pinset 등재. 현재 pinset이면 revision 인자 생략 가능, 다른 pinset이면 두 revision 필수 |
| `pin rollback --to <pinset-sha256> --reason R --confirm` | mutation | 보존본으로 원복. **차단된 pinset으로는 원복하지 않는다** — 무제한 rollback은 교차 저장소의 "terminal 재시도 금지" 규약을 코드로 깨뜨리는 일이다 |

**`pin unblock`은 의도적으로 없다.** 해소 경로는 새 revision으로의 회전이다.

`--reason`은 world-readable 공개 사본과 인증된 API 응답에 **그대로** 실린다. 비밀을
적지 않는다.

---

## 7. API 계약 — `GET /api/v1/runtime-pins`

읽기 전용. 인증 필요(미인증 401). mutation 없음.

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

## 8. rebuild와의 관계

```
ktdctl pinvi-pair rebuild-pinned --confirm
  └─ _require_pinned_runtime_rebuild_root()            root 강제
  └─ current_pinned_runtime_release()                  registry 로드 (없으면 fail-close)
  └─ _assert_pinset_is_not_permanently_blocked(digest) ★ 조건 없는 차단이면 여기서 거부
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

정상 동작이다. 현재 pinset이 terminal로 등재돼 있다는 뜻이고, 해소는 회전뿐이다.

```bash
cd /opt/kor-travel-docker-manager
sudo -n backend/.venv/bin/ktdctl pin show           # 왜 차단됐는지 확인
sudo -n backend/.venv/bin/ktdctl pin rotate \
  --role pinvi --revision <새 40-hex> \
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
- [ ] 코드 하한선을 파일에 기록하지 않는다(§1-5).
- [ ] 새 회귀가 "값 고정"이 아니라 "성질"을 검증한다 — 정당한 회전으로 깨지는 단언은
      이 전환이 없애려던 churn을 되살리는 것이다.
- [ ] 실제 퍼미션·소유권이 걸린 변경은 n150 격리 live E2E로 확인했다(Windows 공유
      마운트에서는 mode 계약을 검증할 수 없다).
