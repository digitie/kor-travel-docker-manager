# DECISIONS 아카이브 02 — ADR-41~50

이 파일은 [`docs/decisions.md`](decisions.md)의 아카이브다(2026-10-20 분리). 번호 인덱스는 `decisions.md` 상단에 있다. 본문은 이동 당시 byte 그대로이며 아무 내용도 고치지 않는다 — superseded 표시 등 상태 변경은 `decisions.md`의 후속 ADR이 갖는다. ADR-49는 결번이라 이 파일에도 없다.

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
