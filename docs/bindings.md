# bindings.md — 두 곳에 적힌 사실과 그것을 묶는 기계

`AGENTS.md` DO NOT 15의 인벤토리다. **같은 사실이 두 곳 이상에 적혀 있는데 사본을
지울 수 없는 경우**를 여기 등록한다. 등록의 목적은 하나다 —

> 결박된 중복과 방치된 중복을 **grep으로 구분할 수 있게** 만든다.

중복 자체는 결함이 아니다. 테스트가 프로덕션 상수를 다시 적는 것은 결박이고 좋은
중복이다. 문제는 둘이 겉보기에 같다는 것이다.

## 왜 이 파일이 생겼나

2026-09-02 하루에 같은 결함 클래스가 **네 번** 나왔다. 전부 증상이 같았다 —
조용히 갈라지고, 값비싼 실행 시점에야 드러난다.

| # | 사실 | 두 곳 | 드러난 지점 | 대가 |
|---|---|---|---|---|
| 1 | 봉인 여부 / 소각 여부 | 한 필드(`classification`)가 두 사실을 겸함 | 29분 rebuild 실패 뒤 | 회전 사이클 1회 |
| 2 | npm workspace 목록 | `package.json` glob ↔ Dockerfile `COPY` 목록 | 71분 rebuild 뒤 | 회전 사이클 1회 |
| 3 | 핀된 Map revision | pin registry ↔ PinVi pair 계약 | 71분 rebuild **뒤** | 회전 사이클 1회 |
| 4 | 설치본 실행 비트 대상 | git index mode ↔ 설치 스크립트 chmod 목록 | 회전 실행 시점 | launcher 무효 |

실측: 세 저장소의 코드·설정(문서 제외)에 40/64-hex 리터럴이 2개 이상 파일에
중복 등장하는 건이 **126건**이다(Manager 67 · PinVi 11 · Map 48). 대부분은 정당한
결박이지만, 어느 것이 그런지 지금은 알 수 없다. 그래서 **신규분부터** 등록한다.

## 등록 형식

각 항목에 다음 다섯을 적는다.

- **사실**: 무엇이 중복인가
- **정본**: 둘 중 어느 쪽이 진실인가
- **사본**: 어디에 다시 적혀 있나
- **사본이 필요한 이유**: 왜 유도로 지울 수 없나 (없으면 유도해서 지워라)
- **결박**: 둘을 묶는 테스트 이름. 없으면 왜 없는지

---

## 등록된 결박

### B-1. 설치본에서 실행 가능해야 하는 launcher 집합

- **정본**: `scripts/` 아래 git index mode가 `100755`인 파일
- **사본**: `scripts/install-ktdm-trusted-release`의 `chmod 0755` 목록
- **사본이 필요한 이유**: 설치 시점에는 **git index가 없다**. 설치 스크립트는
  release archive를 상대로 돌기 때문에 정본을 그 자리에서 읽을 수 없고, 목록을
  들고 갈 수밖에 없다. 그리고 정규화가 막는 것은 exec 비트가 아니라 **write
  비트**다 — `tar.umask=0`인 archive는 world-writable 파일을 그대로 푼다(실측).
  그래서 전부 0644로 내린 뒤 명시 목록만 0755로 되돌린다. 사본은 지울 수 없고,
  대신 index와 어긋나지 않게 결박한다.
- **결박**: `test_installer_executable_set_mirrors_the_git_index`
- **드러난 경위**: `rotate-pinned-pair`가 index에서는 `100755`인데 목록에 없어
  설치본에서 `-rw-r--r--`로 조용히 무효가 됐다. index를 고쳐도 여전히 무효였다.

### B-2. `pair_contract_invalid`의 진단 어휘

- **정본**: `scripts/m05_isolated_e2e.py`의 `_PAIR_DIAGNOSTICS`
- **사본**: 15곳의 `_fail("pair_contract_invalid", diagnostic=...)` 호출부
- **사본이 필요한 이유**: 각 호출부가 자기 문맥의 문구를 골라야 하고, 그 값이
  launcher stderr로 나가므로 자유 문자열을 열 수 없다.
- **결박**: `test_pair_failures_carry_a_closed_vocabulary_diagnostic`
  (진단 없는 호출 0건 + 쓰인 문자열이 어휘의 부분집합)

### B-3. M05 harness의 2-role 고정

- **정본**: `pinned_runtime_release.RUNTIME_SOURCE_ROLES`
- **사본**: `m05_isolated_harness`의 `M05IsolatedRuntimeRole` ·
  `_EXPOSED_RUNTIME_SERVICE_ROLES` · `_RUNTIME_IMAGE_ROLES`
- **사본이 필요한 이유**: 별칭 배선은 오늘 no-op이고, 정본이 늘면 별칭만 따라가고
  networks/services/images/provenance/이항 분기가 남아 harness가 unconstructible이
  된다. 그 실패는 두 스택을 다 띄운 뒤(1~2시간)에 나온다 — 능력 없는 결합이라
  이 저장소가 결함으로 규정한 과결박이다(`docs/tasks.md` GM-18 won't-fix).
- **결박**: `test_m05_isolated_harness_is_deliberately_two_role`
- **드러난 경위**: won't-fix 결정이 이 테스트를 안전망으로 들었는데 **테스트가
  존재하지 않았다.** `test_normative_docs_cite_real_symbols`가 잡았다.

### B-4. 규범 문서가 지목하는 테스트

- **정본**: `backend/tests/`의 테스트 함수
- **사본**: `AGENTS.md`·`SKILL.md`·`CLAUDE.md`·`docs/tasks.md`·`docs/resume.md`·
  `docs/runtime-pin-registry.md`·`docs/docker-management.md`의 인용
- **사본이 필요한 이유**: 문서가 결정의 근거로 테스트를 지목하는 것은 이 저장소의
  규약이고, 그 인용이 없으면 왜 그렇게 결정했는지 추적할 수 없다.
- **결박**: `test_normative_docs_cite_real_symbols`
- **범위 주의**: `docs/journal.md`·`docs/tasks-done.md`·감사 보고서는 **검사하지
  않는다.** 과거 기록이므로 지금 기준으로 고치라고 하면 역사를 다시 쓰게 된다.

---

## 결박할 수 없어 남겨 둔 것

### U-1. 핀된 Map revision (교차 저장소)

- **정본**: Manager runtime pin registry의 `map_revision`
- **사본**: PinVi `contracts/kor-travel-map-m05-pair-provenance-v1.json`의
  `map.admin.source_revision`·`map.full.source_revision`, 그리고 PinVi 테스트 핀 2곳
- **왜 결박할 수 없나**: 두 선언이 **다른 저장소**에 있고 릴리스 주기가 독립이라,
  어떤 단일 빌드도 한쪽을 다른 쪽에서 유도할 수 없다. PinVi는 자기가 어느 pinset에
  들어갈지 모른다.
- **완화**: `scripts/rotate-pinned-pair`가 회전 **전에** 대상 PinVi revision의
  계약을 읽어 두 값을 비교하고, 어긋나면 두 값을 찍고 회전을 거부한다
  (`docs/runtime-pin-registry.md` §7.5). 비용은 shallow fetch 한 번,
  막는 것은 rebuild 한 사이클이다.
- **근본 수리(백로그)**: 계약 스키마 v2에서 `source_revision`을 걷어내고
  **생산자를 pin registry 하나로** 만든다. PinVi가 revision을 필요로 하는 세 지점은
  계약이 아니라 Manager receipt가 전달한 값을 쓴다. 그러면 Map 문서 한 줄이 PinVi
  커밋을 부르는 증폭이 사라진다. 비용이 커서(PinVi 3개 검사 재설계 + 두 저장소
  동시 릴리스) 별건으로 둔다.
- **재발 이력**: 2026-09-01 이후 재핀 **네 번**(#506·#508·#510·#519). 그중 하나는
  커밋 제목이 스스로 "docs-only bump"라고 적었다.
