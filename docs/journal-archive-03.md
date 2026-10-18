# JOURNAL 아카이브 03 — 항목 제목 날짜 2026-08-05 ~ 2026-08-28

이 파일은 [`docs/journal.md`](journal.md)의 아카이브다(2026-10-16 분리). 파일 목록은 `journal.md` 상단 "과거 기록 아카이브" 표에 있다. 본문은 이동 당시 byte 그대로이며(원래 파일 순서 — 대체로 최신이 위지만 엄격한 시간순은 아니다) 아무 내용도 고치지 않는다.

---

## 2026-08-28 — KUM-M14: 버튼을 만들지 않기로 하고, 대신 판정을 준다 (오너 결정)

설계 Q5는 "rehearsal 한정 재구축 버튼"을 승인했지만 그것은 만들 수 없다.
`rebuild-pinned`는 root를 요구하고, backend가 root로 도는 호스트에서도 **HTTP 요청
하나가 세 개 DB를 파기하는 작업을 시작할 수 있게 만드는 것은 경계를 없애는 것**이지
편의가 아니다. 오너가 게이트된 CLI 카드를 선택했고, 그대로 구현했다.

화면이 하는 일을 둘로 나눴다. **판정은 서버가** 하고(값싸고 안전하다), **실행은 SSH**에
남는다. `GET /api/v1/pinned-rebuild/preflight`가 이미 있는 읽기 전용 관측 셋 셋을 합쳐
"지금 눌러도 되는가"에 답한다 — 공개된 pin 상태(terminal 여부), 배포 모드와 미종결
journal, 사전 점검 결과. 어떤 mutation도 하지 않고 어떤 명령도 실행하지 않으며, 회귀
`test_the_module_never_executes_a_rebuild`가 그 모듈에 `subprocess`나
`compose_service` 참조가 없다는 사실로 이를 결박한다.

판정에서 지킨 구분:

- **phase 한정 차단은 시작을 막지 않는다.** journal 재개만 막는 것이므로 합치면
  재구축이 실제로 허용되는데도 "회전하라"고 말하게 된다.
- **미종결 journal은 차단이 아니라 경고다.** 그 상태에서 `rebuild-pinned`는 새로
  시작하지 않고 **재개**한다 — 그 사실을 모르면 결과를 잘못 읽는다.
- **확인 못 한 것이 확실한 차단을 덮지 않는다.** 둘 다 있으면 `blocked`다.
- **차단 상태에서도 명령을 숨기지 않는다.** 무엇을 실행하려던 것인지 보이지 않으면
  차단 사유와 연결 짓기 어렵다. 대신 "해소 전에는 실행하지 마세요"라고 쓴다.

비전문 관리자에게 실제 장벽은 "SSH로 가라"가 아니라 "가서 무엇을 쳐야 하고 지금 쳐도
되는지"였다. 그 장벽만 없애고 파괴적 실행의 마찰은 그대로 남긴다.

---

## 2026-08-28 — KUM-M10: 비밀번호를 화면에서 바꾸되, 재구축을 무효화할 수 있으면 막는다

관리자 비밀번호는 그동안 SSH에서 `.env`를 손으로 고치는 일이었다. 이제 패널에서 바꾼다 —
`KTDM_ADMIN_PASSWORD_HASH` **한 줄만** 다시 쓰고, `verify_admin_password`가 매번
`os.environ`을 읽으므로 재기동 없이 즉시 적용되며 진행 중인 세션은 끊기지 않는다.

**경계를 함수 모양으로 만들었다.** `_rewrite_env_single_key`는 매핑을 받지 않는다 — 임의
key=value 쓰기를 **표현할 수 없다.** 그 위에 allowlist와 사후 대조를 얹어, 재작성 결과에서
그 키 하나만 달라졌는지 확인하고 아니면 아무것도 쓰지 않는다.

**미종결 rebuild journal 가드는 세 갈래다.** resume은 journal의 `environment_sha256`을
현재 `.env` 바이트와 대조하므로, 비밀번호를 바꾸면 진행 중이던 rebuild의 재개가 영구
차단된다. 그런데 backend가 그것을 **항상 볼 수 있는 것은 아니다** — journal은
`rebuild-pinned`를 실행한 root의 `$HOME` 아래 `0700`에 있다. 그래서:

- `not_rebuildable`/`no_journal` — 통과.
- `unfinished_journal` — **거부, 우회 경로 없음.** 증명됐다는 것은 재개가 실제로 걸려
  있다는 뜻이므로 승인 플래그로도 뚫리지 않는다. 화면에도 우회 컨트롤이 없다.
- `unverifiable`/`unknown` — 명시 승인 없이는 거부. 화면은 SSH 확인 명령을 주고
  "재구축 무효화 동의"를 그대로 입력해야 버튼이 열린다.

"확인 불가"를 "안전"으로 읽지 않는 것이 이 설계의 요점이다.

**감사 기록의 분리**: 잘못된 현재 비밀번호만 `event_type=login`으로 남겨 브루트포스
카운터에 합류시키고, 가드 거부 같은 나머지는 `admin_password`로 남긴다 — 자격증명 추측이
아닌 거부가 카운터를 오염시키면 진짜 공격이 묻힌다. 비밀번호도 해시도 감사에 넣지 않는다.

**mock으로 대체한 것**: 실제 미종결 journal을 만들려면 파괴적 재구축을 돌려야 하므로 그
경로는 mock으로 검증했다. `.env` 재작성 자체는 tmp_path의 실제 파일로 검증한다.

---

## 2026-08-28 — KUM-M9: 백업 생성을 화면에서, 단 job 기록은 권위가 아니라고 말하면서

백업 생성은 그동안 CLI 전용이었다. geo 실측 소요가 879초~22분이고 기본 상한이 4시간이라
HTTP 요청 수명에 묶을 수 없었기 때문이다. `POST /api/v1/backups/{role}`이 `202`와 job id를
돌려주고 화면이 5초 간격으로 폴링한다.

설계에서 지킨 것:

- **job 기록은 권위가 아니다.** 프로세스가 죽으면 사라진다. 무엇이 실제로 남았는지는
  디스크의 manifest가 말하고 `GET /backups`가 그것을 읽는다. 그래서 job 소실은 데이터
  손실이 아니라 진행 표시의 손실이다 — 이 구분을 모듈 docstring과 문서에 명시했다.
- **shutdown은 진행 중 job을 취소하지 않는다.** `asyncio.to_thread`는 실행 중인 `pg_dump`를
  중단시키지 못하고, 취소는 기록만 잃는다. 짧게 배수하고 경고로 남긴다. 함께 기록한
  위험: role lock은 이 프로세스의 `flock`이라 재기동하면 풀리는데 컨테이너 안 `pg_dump`는
  계속 돈다 — 그 창에서 두 번째 dump가 붙을 수 있다.
- **실행 중 job은 어떤 축출 규칙으로도 버리지 않는다.** 진행 중에 사라지면 폴링이 404가
  되고 운영자는 결과를 볼 방법이 없다.
- **화면은 복원이 없다는 사실을 먼저 말한다.** 생성 버튼을 다는 순간 "백업이 있다"가
  "복원할 수 있다"로 읽히기 쉽다. 헤더 문구가 그 둘을 갈라 놓는다.

**공유 그룹 모드**를 함께 넣었다. 만드는 주체가 UI와 cron 둘이 되면 서로의 산출물을 지울 수
있어야 하는데, unlink는 파일이 아니라 **디렉터리** 권한이라 setgid 공유 디렉터리가 필요하다.
`KTDM_BACKUP_SHARED_GROUP`을 선언하면 `2770`/`0640` 계약으로 다루고, 선언하지 않으면 기존
`0700`/`0600` 그대로다 — 아무도 요구하지 않은 권한 완화를 기본값으로 만들지 않는다. 전제는
코드가 만들지 않고 **확인만** 한다(운영자가 건 setgid/ACL을 추측해 재설정하면 조용히
되돌아간다). setgid가 실제로 먹지 않아 산출물이 다른 그룹에 떨어지면 그 dump를 지우고
실패한다 — 목록에는 보이는데 아무도 못 읽는 백업은 거짓 안전감만 만든다.

---

## 2026-08-28 — 적대 리뷰 2건 반영: 죽어 있던 경로와 잠기는 상태들

전문 리뷰어 서브에이전트 2인이 각각 계약·fail-close 관점과 운영·사용성 관점으로 M5·M6·M7
커밋을 독립 리뷰했다. 확인된 지적을 전부 반영했다. 무거운 것부터:

**요청 경로가 배포에서 죽어 있었다.** `/var/lib`는 root `0755`라 비-root backend가 그
아래에 디렉터리를 만들 수 없는데, installer는 `-requests` 트리를 만들지 않았고 어떤 런북에도
그 단계가 없었다. 첫 클릭이 503으로 끝나고 메시지는 "운영자에게 확인하세요"였다 — 보고 있는
사람이 그 운영자다. installer가 디렉터리를 만들고, 503은 실행할 `install -d` 명령을 그대로
준다.

**손상된 요청 파일이 회전 요청 경로 전체를 잠갔다.** 읽지 못하면 id를 알 수 없어 id 대조
삭제가 불가능하고, 파일이 있으니 새 요청도 못 받는다. 화면의 unreadable 카드는 취소 버튼도
없이 방금 실패한 `show-pending`을 권하고 있었다. `clear-pending --force`를 넣었다 — 읽을 수
없는 파일만 파싱 없이 지우고 멀쩡한 요청에는 거부한다. 모든 손상 메시지에 전체 경로를 싣는다.

**`apply-pending`의 exit 1이 "아무 일 없음"과 "적용됐으나 정리 미완"을 겸했다.**
`|| echo "적용 안 됨"` 같은 스크립트가 pinset을 태워 놓고 적용 안 됐다고 보고할 수 있었다.
정리 미완을 exit 3으로 분리했고, `clear_runtime_pin_request`가 던지는
`RuntimePinRequestError`가 `except OSError`를 빠져나가 회전 뒤에 traceback으로 터지던 것도
함께 고쳤다.

**요청 기록에 경합이 있었다.** 읽고-나서-`os.replace`는 두 관리자가 동시에 요청할 때 나중
쓰기가 앞의 것을 말없이 덮으면서 **둘 다 "기록됨"으로 감사에 남았다.** `O_CREAT|O_EXCL`로
커널이 승자를 정하게 했다.

**계약 결박 env 검사가 추가·삭제를 통과시켰다.** 저장은 environment 매핑을 통째로 교체하므로
키를 빼는 것이 곧 삭제인데, "양쪽에 있을 때만 비교"라 삭제도 추가도 그대로 통과했다. 없음을
sentinel로 두고 비교한다. 같은 구멍이 있던 기존 `POSTGRES_INITDB_ARGS` 가드도 함께 닫혔다.
DSN을 결박하는 검증기가 candidate dict 밖에 있어 `pinvi-dagster`가 잠금 목록에서 통째로
빠져 있던 것도 합집합으로 고쳤다(대입이라 유도된 이름이 사라질 수 있던 것도 함께).

**readiness 새로고침이 실제로 다시 관측하지 않았다.** 서버에 30초 TTL이 있는데 route에
`refresh` 파라미터가 없어, SSH에서 image를 pull한 운영자가 새로고침을 눌러도 같은 차단
문구를 계속 봤다 — 조치가 실패한 줄 알게 되는 경로다. 같은 파일의 disk-usage route가 이미
쓰던 패턴을 그대로 적용했다. 실패 payload가 한국어 화면에 `compose_single_file` 같은 내부
id를 라벨로 띄우던 것도 고쳤다.

**`COMPOSE_PROJECT_NAME`이 영구 노란불이었다.** 운영 `.env`가 반드시 설정하는 값인데
"확인이 필요한 변수"로 분류돼 있어, 정상 호스트가 해소 방법 없는 경고를 영원히 달고 있었다.
지워지지 않는 경고는 패널을 안 읽게 만든다 — advisory 목록에서 뺐다.

**non-UTF-8 blob 하나가 패널 전체를 가렸다.** `_git_text`의 `text=True`가 던지는
`UnicodeDecodeError`는 `OSError`가 아니라 밖으로 새고, 최상위 `except Exception`이 그것을
받아 네 행 전부를 `unknown`으로 만들었다. 크기 상한도 다 읽은 뒤에 재고 있어 장식이었다.
파이프에서 상한+1 바이트만 읽는 `_git_blob_text`로 분리했다.

이 밖에 무결성 검사의 TOCTOU(검사와 읽기가 다른 syscall이라 그 사이 바꿔치기 가능)와
hardlink 통과, 개발 체크아웃에서 저장소 루트를 `0700`으로 만들던 chmod, 긴 사유가 요청
출처를 밀어내던 절단, `unknown`일 때 대기 요청이 화면에서 사라지던 문제, DELETE 거부가 감사에
남지 않던 문제, 취소 실패 후 유령 카드가 남던 문제를 고쳤다.

backend 975 passed / 1 skipped.

---

## 2026-08-28 — n150 격리 live E2E와, 그 과정에서 드러난 두 가지 사실

M5·M6·M7을 실제 호스트에서 16항목 격리 E2E로 검증했다(`scripts/run-pin-request-isolated-e2e`,
`ALL CHECKS PASSED on digitie-at-n150`). `/opt` 배포 트리와 운영 registry, 운영 요청
디렉터리는 건드리지 않는다. 통과 항목에는 실제 euid 기반 root 게이트, root가 비-root가
쓴 요청 파일을 읽는 무결성 검사, 회전 뒤 공개 사본 즉시 갱신, stale 요청 보존,
id 불일치 시 미삭제가 포함된다.

**사실 1 — 이 배포에서 backend는 root로 돈다.** `deploy-runbook.local.md` §3-3이
`.env`가 root `0600`이면 `sudo -n`으로 띄우라고 명시하고, n150의 uvicorn 프로세스 uid는
실제로 0이었다. 그래서 `runtime-pin-registry.md` §1-4가 오래 담고 있던 "backend는
비-root라 registry를 물리적으로 못 쓴다"는 논거는 **이 호스트에서 성립하지 않는다.**
그 문장을 정정하고, 대신 실제로 강제되는 경계를 회귀로 만들었다 —
`test_the_http_layer_never_mutates_the_pin_registry`가 `api/` 전체에서
`rotate_runtime_pin`·`write_runtime_pin_registry` 등 mutator 참조를 금지한다. 남은 보호는
그 규칙과, `apply-pending`이 요청에서 role·revision만 취하고 나머지를 재유도한다는 계약
둘이다. 설계 논거를 권한에만 걸지 않는다.

**사실 2 — 지금 고정된 PinVi revision으로는 재구축이 성립하지 않는다.** 새 readiness
검사를 실제 체크아웃에 돌린 결과 `missing`이었다: `97d2f924…`의 역할 부트스트랩
스크립트는 Manager가 주입하는 4개 모드 중 `PINVI_ROLE_CATALOG_RESET_ONLY`와 그
permit/result 경로 3개를 모른다(선언된 것은 `PINVI_ROLE_TOPOLOGY_VERIFY_ONLY` 하나뿐).
다음 pin 회전은 이 3개를 구현한 revision을 골라야 한다.

E2E가 잡아낸 결함 하나도 고쳤다. readiness의 `unknown` 경로가 evidence를 비워 반환해,
"읽지 못했습니다"만 있고 **어느 revision을 어느 체크아웃에서 찾았는지**가 없었다.
화면에서 진단을 시작할 수 없는 상태였으므로 `pinned_revision`·`pinvi_root`·`script_path`를
unknown 경로에도 싣는다.

---

## 2026-08-28 — KUM-M6: 설계 전제 정정과, Manager 단독으로 가능한 부분의 완료

설계 P10-3(i)은 "`compose_service.py`의 stderr 9문구 파싱을
`pinvi.role-topology-diagnostic.v1` typed JSON 소비로 이관"하라고 했다. PinVi의
`infra/postgres/bootstrap-pinvi-runtime-role.sh`를 직접 읽어 보니 **그 전제가 틀렸다.**

- typed envelope은 `PINVI_ROLE_TOPOLOGY_VERIFY_ONLY=1`일 때만 stdout 한 줄로 나오고
  언제나 exit 0이다. Manager가 분류하는 9문구는 **일반 부트스트랩 실행**의 stderr이며
  그 경로에는 envelope이 아예 없다(exit 2=입력 검증, 1=endpoint 미준비, 3=topology·소유권
  거부). 둘은 같은 실패의 다른 표현이 아니라 **서로 다른 실행**의 출력이다.
- 따라서 이관은 Manager만으로 불가능하다. PinVi가 일반 실행에서도 envelope을 내보내야
  성립하며 그것이 KUM-PV-3다. 고정 revision `97d2f924…` 기준으로 현재 9문구 map은
  정확하다는 것은 확인했다(문구 대조).

같은 조사에서 **실행 전에 알 수 있었던 결손 하나**를 찾았다. 고정 revision의 스크립트에는
`PINVI_ROLE_CATALOG_RESET_ONLY` 처리가 없다. Manager는 그 모드를 `-e`로 주입하는데,
변수는 조용히 무시되고 일반 부트스트랩이 실행된 뒤 Manager가 `{}` 결과를 읽고
fail-close한다. 데이터는 안전하지만 의도하지 않은 실행이 한 번 일어나고 pinset 하나가
소모된다 — 진단 5가 말하는 바로 그 낭비다.

그래서 이번에 한 일:

- readiness 검사 `pinvi_role_bootstrap_modes` 추가. **체크아웃 HEAD가 아니라 고정
  revision의 blob을 직접 읽는다**(`git show <rev>:<path>`) — 재구축이 실제로 쓰는 것이 그
  tree이고, 체크아웃이 어느 브랜치에 있든 답이 달라지면 안 된다. revision이 로컬에
  없으면 `unknown`이지 결손이 아니다(fetch 안 된 것을 차단으로 보고하면 거짓 차단이다).
- P10-3(iii) 계약 결박 env의 read-only화. 목록은 candidate 계약
  (`_CANDIDATE_CANONICAL_API_ENV_VALUES`)에서 **유도**한다 — 손으로 관리하는 두 번째
  목록을 만들면 계약과 화면이 갈라진다. 잠기는 이름은 14개 service에 걸쳐 82개이고,
  그중 `kor-travel-map-api` 한 서비스에만 21개가 그동안 편집 가능했다. 서버는 저장
  시점에 거부하고(재구축까지 미루면 실패가 조작에서 멀어진다) 화면은
  `config.locked_env`로 처음부터 잠근다.
- Manager의 `PINVI_ROLE_CATALOG_RESET_DIAGNOSTICS`에 스크립트가 결코 쓰지 않는
  `target_not_isolated`가 있다. 받아들이는 집합이 넓기만 한 것이라 fail-close 결함은
  아니므로 좁히지 않고 기록만 했다 — 좁히면 다른 revision에서 거짓 거부가 된다.

backend 946 passed / 1 skipped, frontend type-check·lint·build 통과.

---

## 2026-08-28 — KUM-M7 마무리: 재구축 사전 점검을 화면에 노출

백엔드(`GET /api/v1/deployment-readiness`)와 회귀는 앞선 라운드에서 끝나 있었고 화면만
비어 있었다. `SourceStatusPanel`에 "재구축 사전 점검" 섹션을 붙여 마감했다.

설계 문서(P10-4)는 선행으로 pin 패널을 적었지만, pin 패널은 M5로 mutation 패널이 됐다.
관측 전용 행을 거기 섞으면 그 패널이 무엇을 하는 곳인지 흐려지고, source-status 패널에는
이미 같은 모양의 `Row`/`VerdictIcon`이 있다. 그래서 배치를 옮겼다.

- **쿼리는 분리**했다. readiness가 실패해도 설치 기록·실행 이미지·계약 행은 그대로
  보여야 한다. 새로고침 버튼은 두 쿼리를 함께 다시 부른다.
- **`warn`/`unknown`을 차단으로 승격하지 않는다.** `missing`만 빨간 "지금 재구축하면
  실패합니다"이고 나머지는 "확인이 필요합니다"다. 전부 빨갛게 칠하면 진짜 차단 항목이
  묻힌다.
- **검사하지 않기로 결정한 항목(`unavailable_checks`)을 이유까지 그대로 표시한다.**
  오프라인 wheelhouse가 그것이다 — 숨기면 남은 항목이 전부인 것처럼 읽혀 잘못된 안심을
  준다.

화면 규약은 `docs/dashboard-ui.md` §6·§7에 반영했고, 관측 API 3종(readiness,
source-status, disk-usage)을 `docs/docker-management.md` 5.2 표에 등재했다.

---

## 2026-08-28 — KUM-M5: UI에서 pin 회전을 '요청'하고 root가 적용하는 2-step

대시보드에서 Map·PinVi pinned revision을 바꾸려면 지금까지는 SSH로 나가는 수밖에 없었다.
그렇다고 backend에 회전 권한을 주면 registry가 root `0600`이라는 물리적 경계 — 이 시스템에서
가장 값싼 안전장치 — 가 사라진다. 그래서 화면은 **요청만 기록**하고 적용은 여전히 root가
한다.

- 새 저장소 `services/runtime_pin_request.py`. registry와 **다른 트리**
  (`/var/lib/kor-travel-docker-manager-requests/`, 파일 `0600`)에 둔다 — registry 트리는
  installer가 매 설치마다 `0700 root:root`로 되돌려 비-root backend가 traverse할 수 없다.
  SQLite를 쓰지 않은 이유는 `database.py`의 DB 경로가 `__file__`에서 유도돼 backend와
  `ktdctl`이 같은 호스트에서 서로 다른 파일을 열기 때문이다.
- `POST/DELETE /api/v1/runtime-pins/requests`. 거부 코드 6종을 정의했고 **거부도 전부 감사
  행으로 남긴다**. `GET /api/v1/runtime-pins`에 `pending_request`가 붙으며, 요청 이후 pin이
  움직였으면 `pending`이 아니라 `stale`로 말한다 — CLI가 반드시 거부할 요청을 화면이 적용
  가능한 것처럼 보여 주면 안 된다.
- `ktdctl pin show-pending / apply-pending --confirm / clear-pending`. 적용은 요청에서
  role과 40-hex revision, 표시용 문자열만 취하고 canonical URL·digest·차단 목록은 코드와
  root registry에서 다시 만든다. base pinset이 어긋나면 거부하되 **요청을 지우지 않는다**
  (무엇이 버려지는지 사람이 보고 정해야 한다). 성공 시 요청 파일은 id 대조 후 삭제한다.
- 화면(`RuntimePinPanel`)은 제목의 "(읽기 전용)"을 떼고 요청 폼과 대기 요청 카드를 얻었다.
  폼은 `status === 'ok'`이고 대기 요청이 없을 때만 렌더링한다 — 눌러 본 뒤에야 거부를
  알게 하지 않는다.

**mock으로 대체한 것**: root 전용 경로(`apply-pending`의 euid 게이트)는 테스트에서
`_running_as_root`를 patch해 검증했다. 이 판정을 이름 있는 함수로 뽑은 것도 그 때문이다.
실제 root 소유권·퍼미션 계약은 n150 격리 E2E에서 확인한다.

계약 정본은 `docs/runtime-pin-registry.md` §7-1, 화면 규약은 `docs/dashboard-ui.md` §5-1.
회귀는 `test_runtime_pin_request.py`(16건), `test_api.py`의 `runtime_pin` 13건,
`test_docker_manager_cli.py`의 pending 9건. backend 938 passed / 1 skipped.

---

## 2026-08-28 — M05 baseline artifact 진단 정정과 immutable image 원인 확정

`29fbcdd…` candidate는 `baseline_reference_invalid`로 terminal 처리됐고 cleanup 뒤 Map·PinVi
transaction resource는 각각 0개였다. candidate와 ledger는 보존하며 재실행하지 않는다. Map
`9c64e862…`의 `application-reference.json`, manifest sidecar와 모든 tracked baseline artifact를 다시
정적으로 대조한 결과, 이전 기록과 달리 `application-seed.sql`을 포함한 declared digest는 실제 bytes와
일치했다. 기존의 source artifact 불일치 주장은 철회한다.

n150의 읽기 전용 image identity 확인에서는 Map Compose의 부동 `postgis/postgis:16-3.5-alpine` 태그가
baseline reference의 immutable PostGIS image와 달랐다. exact catalog receipt가 다른 source image에서
fail-close한 것이므로 Manager·PinVi의 runtime override로 고치지 않는다. 다음 변경은 Map Compose를
baseline digest에 고정하는 source PR이며, 그 committed Map revision을 PinVi pair·Manager pinset에
재결박한 새 candidate만 one-shot admission할 수 있다.

---

## 2026-08-28 — [철회됨] 고정 Map baseline artifact 불일치 판단

이 판단은 같은 날 후속 정적 재검증으로 철회했다. exact Map `9c64e862…`의
`application-reference.json`, sidecar와 `application-seed.sql`을 포함한 declared artifact bytes는 모두
정합했다. `29fbcdd…` candidate의 `baseline_reference_invalid`는 source artifact 불일치가 아니라 baseline이
고정한 immutable PostGIS image와 Compose 부동 태그가 가리킨 image identity의 drift였다.

candidate와 ledger는 terminal 기록으로 보존하며 재실행하지 않는다. 현재의 원인·후속 순서는 이 파일 최상단
`M05 baseline artifact 진단 정정과 immutable image 원인 확정` 항목이 정본이다. 이 철회된 판단은 Map
artifact 변경 또는 새 candidate 생성의 근거로 사용하지 않는다.

---

## 2026-08-28 — Alembic runtime contract의 고정 원인 분류

`6c888a5…` candidate는 `alembic_runtime_contract_failed`로 끝났고 cleanup 뒤 Map·PinVi
transaction resource는 각각 0개였다. candidate와 ledger는 terminal로 보존하고 재실행하지 않는다.

다음 candidate는 immutable Map source에 선언된 Alembic `RuntimeError` 문구만 memory 안에서
closed allowlist로 대조한다. destination facet, runtime configuration, baseline reference, schema
lineage, metadata contract의 다섯 enum만 종료 코드로 남기며, 문자열·hash·exception 원문은 result에
기록하지 않는다. allowlist 밖의 runtime error는 기존 generic enum으로 fail-close한다.
exact·prefix·unknown 세 경우를 실제 runner 종료 코드 회귀로 고정해 이후 진단 분류가 임의로 바뀌지 않게 한다.

---

## 2026-08-28 — Map fresh-init 예외 계열 분류 확장

`bea60f5…` candidate는 fixed `map_fresh_init_failed`와 `unclassified`로 끝났고, cleanup 뒤
Map·PinVi transaction resource는 각각 0개였다. candidate와 ledger는 terminal로 보존하고 재실행하지
않는다. launcher가 output directory를 스스로 새로 만들도록 요구하므로, admission 전 빈 output directory를
미리 만든 첫 호출은 ledger·result를 만들지 않았고 해당 빈 directory만 정확히 확인한 뒤 제거했다.

Map source는 고정한 채, one-shot runner는 `FreshMigrationError` allowlist 뒤에
`RuntimePrivilegeReconciliationError`, Alembic command/runtime contract, SQLAlchemy statement의 네
고정 예외 계열만 별도 종료 코드로 분류한다. 결과에는 enum만 남기고 원문 exception·stderr·환경값은
계속 폐기한다.

---

## 2026-08-28 — Map fresh-init 고정 종료 코드 진단과 candidate 회전

`aa78b4ec…` candidate는 Map `fresh-init`까지 명시해 `map_fresh_init_failed`로 끝났고,
cleanup 뒤 Map·PinVi transaction resource는 모두 없었다. 이 candidate와 ledger는 terminal로
보존하고 재실행하지 않는다. 원문 stderr·container log·환경값은 읽거나 결과에 쓰지 않았다.

다음 candidate의 fresh-init one-shot은 고정 Map source의 `migrate` 인자·동일 `_migrate` 경로를
실행하되, 알려진 `FreshMigrationError`만 고정 종료 코드로 바꾼다. Manager는 그 코드와 정적
allowlist를 대조해 제한된 `map_fresh_init_reason`만 root-owned `0600` result에 기록한다. 알려지지
않은 오류도 `unclassified`로만 남기며, stdout·stderr는 계속 폐기한다. 이 반복 절차는 원문 로그
접근 없이 다음 수정 범위를 좁히고 한 번의 ledger candidate를 재시도하지 않게 한다.

---

## 2026-08-28 — M05 Map command별 비밀 비포함 failure phase

`bc704aef…` candidate는 cleanup을 완결했고 잔여 container·network·volume이 없었지만, result의
`driver_phase`가 generic `runtime_command_failed`여서 Map postgres start·fresh-init·application start 중
정확한 명령을 구분할 수 없었다. candidate와 ledger는 terminal로 보존하고 재실행하지 않는다.

Map의 세 Compose command는 각각 고정된 failure phase로 변환해 result에 기록한다. command output·stderr·env
값은 계속 저장하지 않으며, fixed phase 문자열만 다음 candidate의 root-owned `0600` result에 남긴다.
focused test가 generic command failure의 phase 변환을 고정한다.

---

## 2026-08-28 — M05 fresh-init cleanup profile과 안전한 실패 단계 증적

`66bb373d…` candidate는 n150 Map `fresh-init` one-shot 세 개가 정상 종료한 뒤, cleanup이 profile을
명시하지 않아 해당 컨테이너 세 개와 transaction 전용 volume 하나를 누락했다. 결과는
`runtime_cleanup_failed`로 fail-close 되었고 재실행하지 않는다. 남은 리소스는 root-owned result의
transaction과 Compose owner label을 모두 대조한 뒤 그 exact 네 개만 제거했다.

Map cleanup에는 `--profile fresh-init`을 명시하고, result에는 raw command output 없이 cleanup 전
`driver_phase`와 `cleanup_failed` boolean을 함께 기록한다. 이 회귀는 profile 인자가 `down`에도
전달되는 focused test로 고정한다. 다음 실행은 새 Manager revision·새 ledger claim에서만 가능하다.

---

## 2026-08-28 — N150 `ss` 경로 회귀 방지와 M05 candidate 회전

PinVi `323e3ba8…`·Map `9c64e862…`·pinset `2d6d5ad5…`로 결박한 Manager `d7a048a1…`의 n150
isolated live E2E는 source materialization과 pair provenance를 통과했지만, Docker mutation 전 port
preflight가 존재하지 않는 `/usr/sbin/ss`를 호출해 `driver_contract_failed`로 끝났다. root-owned
`m05-isolated-once` ledger와 결과 증적은 보존하고, 그 candidate는 재실행하지 않는다.

portable Debian runtime 위치인 `/usr/bin/ss`로 고정하고, 모든 port probe가 그 경로만 호출하는 focused
회귀를 추가했다. 이후 새 Manager revision과 새 ledger claim으로만 n150 live E2E를 한 번 실행한다.

---

## 2026-08-28 — N150 host Ed25519 fallback을 포함한 PinVi pinset 회전

PinVi `323e3ba8…`은 M04/M05 attestation의 N150 host runner·source·evidence 경계를 유지하면서,
Manager release Python에만 없는 `cryptography` Ed25519 primitive를 `/usr/bin/openssl` fallback으로
fail-close 처리한다. container 안에서 attestation을 실행하려던 경로는 N150-only Node/host preflight와
nested Docker bind source를 깨므로 전문 적대 리뷰 P1로 폐기했다. host fallback은 `0600` temporary regular
file, opened-FD mode/owner 검증, fixed tool environment, Ed25519/SPKI/signature 검증, cleanup failure를
고정하고, 실제 n150 Manager venv에서도 sign/verify smoke를 통과했다.

exact Map `9c64e862…`와의 새 pinset은 `2d6d5ad5…`이다. 이전 `9835cfcc…`은 candidate로 admission하지
않으며, 이 새 PinVi source·Manager revision 조합의 root-owned ledger가 없는 경우에만 n150 isolated
M04/M05 live E2E를 정확히 한 번 실행한다.

---

## 2026-08-28 — M05 isolated loopback·fixture 권한 경계 보강

전문 적대 보안 재리뷰의 P1 두 건을 반영했다. root driver의 모든 host HTTP 대상은 명시적인
`127.0.0.1:port` HTTP URL만 허용하고 기본 opener와 PinVi cookie opener 모두 ambient proxy를
무시한다. 이로써 Map admin proxy credential을 host의 `HTTP_PROXY`/`HTTPS_PROXY` 경로로 보낼 수
없다.

provider fixture는 더 이상 bootstrap/migrator owner DSN이나 `SET ROLE`을 사용하지 않는다. generic
Map API image에는 `ktm_feature_dagster_runtime` DSN 하나만 주입하고, 실제 `FeatureBundle` provider
적재 경로와 `record_manual_provider_dedup_candidate` procedure만 호출한다. runtime privilege preflight,
loopback URL 차단·proxy-free transport, owner credential/직접 INSERT 부재를 focused 회귀로 고정했다.
다음 단계는 두 전문 재리뷰와 trusted release의 n150 단발 실행이다.

## 2026-08-28 — PinVi isolated migrator boundary를 포함한 pinset 회전

PinVi `7d66523a…`는 root Manager harness marker와 transaction-bound project에서만 isolated
migration을 허용한다. 이 실행 경계 변경을 exact Map `9c64e862…`와 새 pinset `9835cfcc…`으로
회전했다. 이전 `d4b34826…`은 실행하지 않으며, 새 root launcher가 완성·리뷰된 뒤 이 pinset만
한 번 admission한다.

이전 PinVi revision pin은 실제 merge commit과 달라 P0 계약 오류였다. Manager는 현재
pair가 지정한 네 OpenAPI source revision을 canonical 원격에서 exact object로 읽어 raw/canonical
SHA까지 대조한다. disposable driver는 M04 UI 승인, Map 승인, provider candidate 결선,
PinVi reconciliation receipt, M05 live attestation 순서를 실제로 실행하며, 실패 위치와
관계없이 private env·bootstrap credential·signing key를 제거한다. n150 실행은 수정 재리뷰와
draft PR CI가 통과한 뒤 한 번만 한다.

## 2026-08-28 — M05 isolated runtime provenance receipt와 PinVi source 회전

Manager는 Map admin/API/frontend와 PinVi API/Web/Dagster 여섯 image를 각각 image inspect ID 및 OCI
source revision label로 exact source pin과 대조한 뒤에만 fixed runtime provenance schema를 만든다. 공개
loopback endpoint 두 개의 bridge topology는 기존 strict inspect로 추가 검증한다. PinVi M05 `isolated`
attestation은 이 `0600` root receipt의 source·Map full OpenAPI·image ID 전체를 소비하며, canonical runtime
digest를 재사용하지 않는다.

PinVi source는 runtime provenance consumer가 포함된 `f9df39bc…`로 회전했고, 새 Manager pinset은
`d4b34826…`이다. 기존 `c1ad5a3e…` 및 그 historical candidate는 재실행하지 않는다. 다음 작업은 이
새 pinset 전용 root driver를 구현·검토하고 source snapshot/build/fixture/cleanup을 한 번의 ledger claim 아래
결선하는 것이다.

## 2026-08-28 — M05 격리 harness admission·runtime inspect 계약 구현

새 `m05_isolated_harness` module은 exact current pinset, installed Manager revision, harness version만으로
canonical JSON claim bytes와 ledger filename을 계산한다. root-owned `0700` ledger 아래 `O_NOFOLLOW|O_EXCL`
claim과 file·directory `fsync`를 child Docker mutation 전에 남기므로 transaction ID나 output path만 바꾼
재실행을 막는다. 이 claim은 실패에도 남으며 production rebuild ledger와 별도 namespace로 보존한다.

runtime receipt 입력은 Docker 원문 log가 아니라 strict inspect 결과만 쓴다. Map API와 PinVi API의 둘을
정확히 요구하고, running state, named user-defined bridge network와 inspect ID, service별 정확히 하나의 dedicated
network, exact harness/pinset/revision/transaction label, image label의 source revision, source-pair와 같은 exact
immutable image ID, 그리고 정확히 하나의 `127.0.0.1` published binding 외에는 모두 fail-close한다. container
label만 신뢰하지 않고 image inspect의 OCI revision label도 release source와 다시 대조한다. bridge driver·network
ID·label도 network inspect에서 재검증한다. focused 회귀는 replay ledger, host network, wildcard binding,
Map 또는 PinVi 역할 누락, provenance/image drift, multi-network와 network driver drift를 고정했다. 다음 구현은 이
contract를 소비하는 root-only source snapshot·compose driver와 label-scoped cleanup이다. 두 전문 적대 리뷰는
이 admission/inspect 범위의 P0/P1이 없음을 확인했고, source snapshot·global lock·receipt driver가 없는 동안에는
어떤 live candidate도 실행하지 않는다는 조건을 다시 고정했다.

## 2026-08-28 — M05 격리 bridge harness 선행 결정

`c1ad5a3e…` committed runtime은 host network이므로 M04/M05 attestation의 loopback published-port
검증을 만족하지 않는다. 과거 격리 checkout은 source pin이 달라 증거로 재사용할 수 없다. 두 전문 적대
리뷰는 canonical runtime을 bridge로 바꾸는 임시 우회와 수동 격리 실행을 모두 거절하고, exact Map/PinVi
source와 fresh bridge resource만 허용하는 root-only harness를 먼저 만들도록 요구했다. harness는 Docker
mutation 전에 `(harness, pinset, Manager revision)` immutable ledger claim과 inherited global lock을 확보하고,
production DB·volume·network·container와의 비공유를 strict inspect로 증명해야 한다. 그 성공 증적은 production
activation receipt로 승격하지 않는다.

임시로 시작한 standalone Map bridge build는 어떠한 runtime container·volume도 만들기 전에 중지했고, exact
Map checkout의 실행 전용 env file도 제거하여 clean 상태를 복구했다. 이후 M04/M05 live E2E는 harness PR의
review·CI·trusted release 설치 뒤에만 재개한다.

## 2026-08-28 — installed launcher execute bit 보존과 candidate 회전

`53d4639f…`은 trusted release가 `run-pinned-rebuild-once`를 executable로 설치하지 않아 admission 이전에 끝났다.
durable output·ledger·raw stderr가 모두 없으며 같은 pinset을 재시도하지 않는다. installer가 archive의 일반 script mode에
의존하지 않도록 launcher의 `0755` execute bit을 명시적으로 복원한다. PinVi `41a36ee6…`·Map `9c64e862…`의
`c1ad5a3e…`만 다음 candidate로 사용한다.

## 2026-08-28 — one-shot launcher의 pinset ledger와 설치 provenance fail-close

전문 적대 보안 리뷰의 P1 두 건을 반영했다. launcher는 caller가 제공한 exact Manager source revision을 root-owned
`.ktdm-source-revision`·release manifest의 `O_NOFOLLOW` evidence와 대조하고, installed Python package에서 읽은
actual pinset별 root-owned `O_NOFOLLOW|O_EXCL` ledger claim과 상위 directory fsync를 먼저 영구 기록한다. trusted
installer와 같은 global mutation lock은 admission부터 실제 `ktdctl` child 종료까지 유지한다. 따라서 output directory 이름을
바꿔도 같은 installed pinset은 다시 `ktdctl`을 호출할 수 없고, stale Manager release도 새 candidate 실행을 시작할 수 없다.
claim은 성공·실패 모두 보존한다. rebuild 내부는 같은 lock path를 새 descriptor로 다시 열지 않고, inode·owner·mode와
nonblocking flock을 재검증한 inherited descriptor를 재사용하므로 one-shot admission과 Manager mutation이 한 transaction으로
이어진다.

## 2026-08-28 — M05 one-shot launcher를 포함한 새 candidate 회전

`6269138f…`은 durable journal/manifest 없이 끝난 pre-journal 단회 시도로 보존하며 재실행하지 않는다. PinVi의
그 기록 source `55687a4f…`와 Map `9c64e862…`를 canonical pinset `53d4639f…`으로 다시 결박했으나, 이는 executable
bit 미보존으로 admission 이전에 끝나 재시도하지 않는다. 다음 n150
candidate는 trusted Manager release의 `run-pinned-rebuild-once`가 root-owned `result.json`을 남기는 경우에만 단 한 번
실행한다. raw stderr는 root 전용으로 보존하고 판정에 읽지 않는다.

## 2026-08-28 — M05 committed Map runtime provenance 재결박

기존 `030b12fc…` generation은 Map `9c64e862…`, API image `2260ec…`, UI image `5dc547…`로
`committed` 되었고 재실행 금지다. PinVi M05 attestation이 source와 image identity를 exact pair로 검사하므로,
PinVi source가 current main rebase로 `61dffcb5…`로 회전했으므로, Map `9c64e862…`와 새 canonical pinset
`6269138f…`는 이전 trusted candidate로 고정했다.
새 pinset은 n150에서 단 한 번만 rebuild하며, committed 뒤에만 isolated M04/M05 live E2E와 signed activation
attestation을 실행한다. 두 전문 적대 리뷰는 이 provenance·canonical hash 결박에 P0/P1 없음을 확인했다.

## 2026-08-28 — pre-journal candidate 결과 회수의 durable 경계

`6269138f…`는 단 한 번 실행됐으나 durable journal/manifest를 남기지 않았다. 실행 SSH의 장시간 source build로
구조화된 stdout을 회수하지 못했으며 raw stderr는 열지 않는다. 다음 candidate는 root-owned 신규 output directory를
단 한 번 만들고 `result.json`과 raw `stderr.log`를 분리하는 `scripts/run-pinned-rebuild-once`만 사용한다. output
directory가 이미 있으면 launcher는 command를 호출하지 않아 같은 candidate 재시도를 막는다.
## 2026-08-28 — pin registry 파일화와 pinset lifecycle 게이트 구현 (KUM-M1~M4, ADR-40)

설계 문서 v3 1부 트랙을 구현했다. pinned revision을 코드 상수에서 root 소유 JSON
registry로 옮기고(`services/runtime_pin_registry.py` 신설), pinset의 생애 상태를
기계가 강제하게 했다. `ktdctl pin init/show/verify/rotate/block/rollback`,
읽기 전용 `GET /api/v1/runtime-pins`, 프론트 "배포 버전 고정" 패널까지 포함한다.
결정 근거와 트레이드오프는 ADR-40에 기록했다.

**가장 중요한 동작 변경**: 현행 pin `cbb577d3…`를 terminal로 등재했다. 근거는 pinvi
`docs/journal.md`(origin/main, 2026-08-27)의 직접 기술이며, 직접 grep으로 확인했다 —
"`cbb577d3…`와 `52c6e538…` candidate/journal은 역사 증거로 보존하며 재시도·수정하지
않는다". 따라서 이 pinset으로 `rebuild-pinned`를 실행하면 mutation 이전에 fail-close하고,
해소 경로는 `ktdctl pin rotate`로 새 pinset을 만드는 것뿐이다. 지금까지 세 저장소가
문서 규율로만 지키던 규약을 코드가 강제한다. n150 상태 루트를 실측해 이 pinset의 미완
v8 journal이 **없음**을 확인했으므로(v5/v7 구 산출물만 존재) 진행 중 작업이 끊기지 않는다.

**전문 적대 리뷰 2건**(계약·fail-close 렌즈 / 운영·회귀·사용성 렌즈)을 독립 수행하고
확인된 지적을 전부 반영했다. 주요 반영: (1) 설치본은 트리를 통째 교체하므로 registry
기본값을 `/var/lib` 상태 디렉터리로 옮기고 트리 안 경로로의 회전을 거부, (2) 로드 시점
파일 무결성 검증 추가(lstat·소유자·쓰기 권한, 경로 기반 면제 제거), (3) 차단 항목도
digest를 revision으로 재계산 대조 — 어긋난 항목은 "차단했다"고 기록되지만 실제로는
아무것도 막지 못했다, (4) 차단 목록 truncate 제거(조용한 유실 대신 fail-close),
(5) 코드가 강제하는 제거 불가능한 차단 하한선(d9) 복원과 `is_blocked_pinset_retry`의
fail-open 제거, (6) 공개 사본이 stale이면 `stale`, registry 직접 읽기는 `degraded`로
표시해 배포본 seed를 권위 있는 값으로 위장하지 않음, (7) API가 phase 한정 차단과 무조건
차단을 게이트와 같은 의미로 구분, (8) `pin verify`가 재시도 금지 상태에 비정상 종료,
`pin show`가 "rebuild 거부됨"을 평문으로 안내, (9) `pin init --force`가 이력·차단
목록을 승계하고 이전 상태를 보존, (10) 동봉본을 읽기 전용 seed로 분리해 추적 파일을
mutation하지 않음.

**검증 범위(무엇을 live로, 무엇을 mock으로 했는지)**:
- **n150 격리 live E2E 16항목 전부 통과**(전용 홈 디렉터리 + 격리 env, 운영 트리 무변경을
  전후로 확인). 부재 시 fail-close, `--confirm` 없는 mutation 거부, 부트스트랩의 0600/0644
  퍼미션, show·verify의 읽기 전용성, seed terminal 등재 유지, **시작 게이트의 terminal
  거부**, 회전의 digest 자동 계산·이력·supersedes, **재기동 없는 즉시 반영**, 회전 뒤
  게이트 통과, 보존본 생성과 terminal rollback 거부, 비-canonical URL 변조 거부,
  group/world writable 거부, verify의 비정상 종료, 설치 트리 안 회전 거부, 그리고
  **root 전용 registry + 별도 공개 트리 배치에서 비-root 프로세스가 공개 사본을 `ok`로
  읽는지**까지 실제 root/비-root 프로세스로 확인했다.
- 세 번째 검증 리뷰가 찾은 P1 하나를 live로 재현·수정했다: 공개 사본 기본 경로가 installer가
  매 설치 `0700 root:root`로 되돌리는 상태 root 안이라 비-root 백엔드가 traverse조차 못 해
  조회 API가 영구 `unknown`이 됐을 것이다. 사본을 별도 트리로 분리했다. 또 live 실행 중
  **root가 사용자 소유 seed를 신뢰 입력으로 읽는 것을 무결성 규칙이 거부**함을 확인했고
  (설치본은 트리 전체가 root 소유라 정상 경로에서는 만족), 그 전제를 런북에 명시했다.
- **mock/단위로 대체한 것**: 실제 `rebuild-pinned` 전체 실행(파괴적 — 세 DB recreate와
  일곱 runtime 재기동이라 격리 불가). 게이트가 mutation·락 획득 이전에 거부한다는 사실은
  `rebuild_pinned_runtime()`을 실제로 호출해 source materialize와 락이 호출되지 않았음을
  단언하는 회귀로 고정했다. 또한 개발 체크아웃이 Windows 공유 마운트라 모든 파일이 0777로
  보고돼 mode 검사를 만족할 수 없으므로, 그 항목만 명시 opt-in 완화를 두고(root에서는 무효)
  실제 퍼미션 계약은 위 n150 live E2E로 검증했다.
- backend 751 tests pass, ruff clean, 프론트 type-check·lint·build 통과.

이번 라운드 범위 밖(후속 태스크): UI 2-step rotate(KUM-M5), typed 진단 소비(KUM-M6),
preflight readiness 노출(KUM-M7).

---

## 2026-08-28 — ktdctl → UI 이관 설계 문서 v3: 3저장소 커밋 교차 감사 반영·태스크 분해 (코드 변경 없음)

오너 지시에 따라 [`docs/ktdctl-ui-migration.md`](ktdctl-ui-migration.md)를 v3로 전면
재구성했다. (1) P1 트레이드오프를 항목별(리뷰 게이트/git 이력/테스트 고정/코드-배포본
단일성) 손실·보상 병기 서술로 확장하고, (2) kor-travel-map(3일 99커밋)·pinvi(25커밋)·
본 저장소(실질 94커밋)의 2026-08-25~28 커밋 전수를 저장소별 전담 조사 에이전트로
분석해 계약·pinning·결박 관련 이슈를 발굴, (3) 발굴 이슈와 P1 계열 개선을 문서
1부(문제 진단 6건 + P1 확장 + P10-1~5)로 최상위 재편, (4) 전 항목에 "왜 문제인가 /
현재의 불합리 / 수정 후 개선" 상세 서술을 추가, (5) 전체 작업을 태스크 단위
(KUM-M1~18, KUM-MAP-1~4, KUM-PV-1~4)로 분해하고 map·pinvi 쪽 수정사항도 동급 상세로
포함했다.

핵심 발굴: 3.5일간 pin 회전 15회(그중 5회는 3,900~4,100줄 기능 커밋에 매몰 — "PR
review = pin 승인" 게이트가 명목상으로도 작동 불가), 회전 1회의 실제 blast radius는
Manager 4-6파일이 아니라 pair 9-11파일 + 제3 저장소 부기(map 부기 커밋 19건, pinvi는
Manager generation 보유 값의 수기 사본 14개), "terminal pinset 재시도 금지" 규약이 세
저장소 문서에 수기로만 존재하며 **현행 Manager pin 자체가 이미 terminal 선언된
pinset**(pinvi journal·map 차단 목록)이라는 사실, d9 legacy 상수가 "역사적 고정값"이
아니라 실패 위를 회전이 지나갈 때마다 축적되는 파생 하드코딩이라는 정정. 이에 따라
registry 스키마에 `blocked_pinsets`/`history`/`supersedes`를 추가하고 `pin block`과
`rebuild-pinned`의 terminal 자동 거부, rollback의 terminal 제한을 설계했다.

주요 정정 3건: P2 조회는 mode 게이트가 아니라 **권한 모델**(state root 0700/0600 +
리더의 uid/mode fail-close)이 실제 제약이라 P1-(c) root-side publisher에 의존한다는
재설계(P9-1단계 "기존 코드 무변경" 추정 철회), P3 `--self`는 installer가 이미 쓰는
0644 provenance 파일의 리더 ~10줄이면 된다는 정정, P6 비밀번호 변경이 미종결 rebuild
journal의 `environment_sha256` 대조와 충돌해 재개를 영구 차단할 수 있다는 신규
위험(가드 요건 추가). 신규 노출 설계: builder receipt 2종·fence/permit 5종·journal
행동 결정 필드·manifest/journal 버전(P2-4), preflight readiness 4행(P10-4), typed
진단 소비(P10-3), 계약 소유 경계 명문화(P10-5 — manifest/journal 키는 map attestation이
exact-dict로 결박한 공개 계약). `.env.example`의 PinVi role credential 6종 누락·폐기
변수 잔존 결함도 태스크(KUM-M15)로 등재했다. 이번 라운드도 설계·문서화만이며 코드
변경과 n150 배포는 없다.

---

## 2026-08-28 — ktdctl → UI 이관 설계 문서 v2 개정 (코드 변경 없음)

오너 지시에 따라 [`docs/ktdctl-ui-migration.md`](ktdctl-ui-migration.md)를 v2로
개정했다. 개정 축 5개: (0) Web UI화 대상 재점검과 필요한 추가 구현 확인, (1) 반복되는
pin 회전의 하드코딩을 설정파일+CLI+API로 전환하는 설계, (2) 기능 격차·API 노출 가능성
재검증, (3) 보안·안정성 대신 **비전문가 관리 편의성·직관성 중심** 재검토, (4) 전체 구조
리팩토링 필요성 평가. 각 축을 독립 조사 에이전트로 병렬 조사(전부 실코드 file:line
대조)한 뒤, 사실 정확성 리뷰와 지시 정합·일관성 리뷰 두 전문 리뷰를 독립 수행해 확인된
지적을 반영했다.

핵심 신규 내용: pin 회전이 최근 200커밋 중 42건을 차지하는 지배적 chore이며 하드코딩
4줄(`pinned_runtime_release.py` 3줄 + `map_application_300.py` 중복 상수 1줄)이 회전
1회를 "전형 5개 파일 수정 + PR + rsync + 재기동"으로 증폭시킨다는 실측 → root 소유
`runtime-pins` registry 파일 + `ktdctl pin init/show/verify/rotate/rollback` + 읽기
전용 `GET /runtime-pins` 설계(배포 트리 밖 경로·캐시 무효화·부트스트랩·backend용
world-readable 사본까지 리뷰 지적을 반영해 명세). usability-first 재정렬로 프론트 전용
quick win 9건(오류 humanize, 라벨 한국어화, freshness 배지, CLI 명령 카드 등)을 0군
신설, 백업 생성 버튼(비동기 job_runner)과 관리자 비밀번호 변경 폼을 2군으로 승격,
`diff-pinned`는 GitHub compare 링크로 전면 대체. 구조 리팩토링은 "전면 불필요 — job
runner·프론트 추출·pin 데이터화·read-only facade 4개 결손만 메우면 됨" 결론과 5단계
점진 계획으로 정리했다.

리뷰가 잡아낸 정정: manifest/journal 정식 경로 도우미가 `require_rebuildable_mode`로
게이트되어 있어 조회 route는 mode 게이트 없는 `pinned_runtime_state_root()` 기반으로
별도 구성해야 한다는 점, 관리자 비밀번호 폼이 P1의 "backend는 파일을 못 쓴다" 경계
논거를 완화한다는 점의 명시, 커밋 수(61→23)·leaf 명령 수(12→13)·회전 diff 파일 수
(6→전형 5) 등 수치 정정. 이번 라운드도 설계·문서화만이며 코드 변경과 n150 배포는 없다.

문서의 열린 질문 7건은 PR 머지 전에 오너에게 직접 물어 전부 확정했다 — pin registry
전환(승인), 백업 create UI화(승인, shared group 방식), 관리자 비밀번호 폼(승인),
UI 2-step pin rotate(승인), rehearsal 한정 rebuild 버튼(승인), restore 로드맵(포함),
CLAUDE.md 동기화(별도 작업). 결정은 문서 말미 "오너 결정 사항" 표와 우선순위 각
항목에 반영했고, 이 문서의 미결 정책 결정은 더 이상 없다.

---

## 2026-08-27 — ktdctl → UI 이관 설계 문서 작성(코드 변경 없음)

`ktdctl` CLI 기능 중 UI로 옮길 만한 것과, GitHub source pull+build·git revision/계약
정합·git 이력 조회·Docker 이미지 업데이트·백업·설정/secret 변경 등 운영에 필요한 기능의
격차를 조사해 [`docs/ktdctl-ui-migration.md`](ktdctl-ui-migration.md)로 정리했다. 조사
서브에이전트가 초안을 작성한 뒤 보안/blast-radius 리뷰와 완결성/실현가능성 리뷰를 각각
독립 서브에이전트로 수행했다.

두 리뷰가 일치되게 지적한 핵심 정정: (1) 초안이 "개별 컨테이너 조작이 C6c 보호를
우회한다"는 근거로 삼은 `assert_c6c_mutation_allowed`는 저장소 어디에도 존재하지 않는
함수였다 — 실제로는 control/config/reset이 C6c lock **안에서** 실행되고, `ensure`만
production 전용 하드스톱을 추가로 갖는다. 이 잘못된 전제 위에 세워졌던
`image rebuild-service` 제안은 로드맵에서 제외했다. (2) `pinvi-pair diff-pinned`
제안은 "read-only 최우선 후보"로 분류돼 있었지만, 실제로는 기존 pinned mirror가
per-pinset·root-owned 0700·rehearsal 전용이라 재사용할 수 없어 별도 fetch(네트워크
mutation)가 필요하다는 것이 확인돼 "정책 결정 필요" 군으로 내렸다. (3) 두 리뷰 중
하나가 초안에 없던 항목 — 이미 backend가 읽을 수 있는 0700 자기 소유
pinned-runtime-generation manifest — 을 찾아내 이번 문서의 최우선 후보로 승격했다.
그 외 `db-backup create/gc` API 노출을 가로막는 실질적 UID/ownership 장벽(이미
`docs/docker-management.md`에 문서화돼 있었음), `secret rotate`의 human/machine
credential 구분 필요성과 self-lockout 재분석도 반영했다.

이번 라운드는 오너 지시대로 **설계·문서화·`docs/tasks.md`의 `KTDCTL-UI-MIGRATION`
태스크 등록까지만** 진행했다. 코드 변경도 n150 배포도 없다. 문서의 열린 질문 6건에
오너가 답한 뒤 승인된 항목만 별도 구현 태스크로 분리한다.

전문 적대 리뷰가 receipt 안전성 오류가 terminal journal을 우회할 수 있고 reset `run`이 generic Compose
typed-output parser를 거칠 수 있는 P1을 지적했다. receipt read/metadata 오류는 이제 `unclassified` terminal
block으로 봉인하며, reset one-shot은 output capture와 typed-output diagnostic을 모두 끈다. 실패 분류의 유일한
입력은 inode-bound result receipt이고, focused generation/rebuild/release 회귀 167건으로 고정했다.

---

## 2026-08-27 — Manager 로그인 화면과 모달·패널을 kor-travel-geo-ui와 재정합

로그인 화면이 geo와 다르다는 지적을 받아 실제 geo `LoginForm.tsx`/`.login-shell`/
`.login-panel`을 다시 확인했다. Manager의 기존 로그인은 좌측 dark hero(headline·설명 문구)
+ 우측 폼의 2단 구성이었는데, geo에는 이런 hero 구성이 아예 없고 화면 중앙에 카드 하나만
띄우는 훨씬 단순한 구조다. `LoginScreen.tsx`를 geo와 동일한 구조로 다시 썼다 — 아이콘+제품명+
"관리자 로그인" 제목이 한 줄에 나란히, 필드 라벨은 uppercase 없이, 에러 메시지는 항상
마운트된 `aria-live="assertive"` 문단(빈 값이면 `:empty`로 숨김, geo와 동일한 접근성 패턴)으로
교체했다. hero 전용이던 `.ops-auth-frame`/`.ops-auth-intro`/`.ops-brand__mark`는 삭제하고
`.ops-auth-card`/`.ops-auth-brand`/`.ops-login-icon`/`.ops-auth-form`/`.ops-field`를 geo의
실측 spacing(카드 max-width 420px, gap 20px, 필드 gap 12px, 폼 gap 14px)에 맞춰 새로 정의했다.
`.ops-form-label`의 글자색도 geo의 `--text-primary`에 대응하는 `--color-ink`로 바로잡았다.

이어서 "로그인뿐 아니라 UI 전반을 검토"하라는 요청에 따라 서브에이전트 포크로 모달·테이블·
패널 헤더·배지·빈 상태를 geo의 실제 컴포넌트(`components/ui/dialog.tsx`,
`components/admin/BackupsPanel.tsx`, `components/admin/SettingsPanel.tsx`)와 대조했다.
확인된 것 중 실제로 반영한 항목: (1) `.ops-modal`의 corner radius가 geo의 실제 shadcn
Dialog(`rounded-lg` → `--radius` → `--radius-control`)보다 컸던 것을 `--radius-card`로
좁히고 shadow도 `--shadow-modal`로 올림(콘텐츠 패널 `--radius-panel`과는 분리), (2)
AdminSettingsPanel의 임시방편 섹션 헤더(`text-sm font-semibold`)를 이미 존재하던
`.ops-section-title`/`.ops-section-copy`로 통일, (3) BackupHistoryPanel의 아이콘이 있는
빈 상태를 geo처럼 아이콘 없는 평범한 텍스트로 단순화. 감사에서 나온 "모달 하단 우측 정렬
액션 버튼 행(geo의 DialogFooter)" 항목은 실제로 geo의 SettingsPanel류처럼 "저장/취소"가
아닌 상시 편집 패널 성격의 화면에는 억지로 끼워 맞출 명확한 커밋 액션이 없어 보류했다
(ContainerDetailModal의 유일한 하단 액션 행은 `NODE_ENV=production`에서 렌더되지 않는
개발 전용 버튼이라 실사용자에게는 애초에 보이지 않는다).

WSL에서 `next build`/`eslint`/`tsc --noEmit`을 통과시켰고, 로컬 QA 세션에서 로그인 화면·
백업 이력 모달·인증 설정 모달을 Playwright로 직접 확인했다.

---

## 2026-08-27 — Manager 오렌지 톤을 사용자 제공 참고 팔레트 값으로 정합

직전 오렌지 재조정(hue 50, 임의 추정치)이 사용자가 제공한
`web_service_palette_testset.html`(10개 서비스 후보 색상 비교용 HTML, Tailwind 기본 팔레트
기반)의 Orange 정의(`#EA580C`/`#C2410C`/`#FFEDD5`, Tailwind orange-600/700/100)를 실제로
참조하지 않고 지어낸 값이었음을 확인했다. 세 hex를 OKLCH로 정확히 변환해
(`oklch(64.6% 0.222 41.1)` / `oklch(55.3% 0.195 38.4)` / `oklch(95.4% 0.038 75.2)`)
`--color-brand`/`--color-brand-ink`/`--color-brand-tint`를 교체하고, neutral 계열 hue도
41로 맞췄다. info/warn/danger/ok 상태색은 변경하지 않았다.

WSL에서 `next build`/`eslint`/`tsc --noEmit`을 통과시켰고, 로컬 dev 서버에서 로그인 화면을
Playwright로 확인해 결과 색상이 Tailwind orange-600(널리 알려진 색)과 일치함을 확인했다.

---

## 2026-08-27 — Manager 컬러 톤을 오렌지 계열로 재조정

`tokens.css`의 OKLCH 팔레트를 호박색 계열(hue 85)에서 오렌지 계열(hue 50)로 재조정했다. hue 50은
`--color-danger`(28)와 `--color-warn`(72) 사이에 있어 세 색 모두 hue만으로는 구별이 약할 수
있으므로, brand는 두 상태색보다 채도를 뚜렷이 높게(chroma 0.19, danger 0.2/warn 0.16과 경계는
가깝지만 hue가 명확히 다른 순수 주황) 잡아 "진한 빨강(danger)"·"밝은 황금색(warn)"과 육안으로
분명히 구별되는 비비드한 오렌지가 되도록 했다. neutral 계열(paper/ink/graphite/shadow 틴트)도
hue 50으로 맞췄다. info/warn/danger/ok 상태색 자체는 변경하지 않았다.

WSL에서 `next build`/`eslint`/`tsc --noEmit`을 통과시켰고, 로컬 QA 세션에서 로그인 화면과 인증된
대시보드를 Playwright로 확인해 brand accent가 danger(빨강)/ok(초록) 상태색과 혼동되지 않음을
확인했다.

---

## 2026-08-27 — Map execution journal merge를 새 M05 source pinset으로 회전 후보

Map #1083 merge `9c64e862c9da82016e12038e2e135526b300ca9d`는 prior generic paired-builder
fail-close와 immutable Python base cache 원인을 provenance-safe하게 기록한다. 이 Map execution journal을
PinVi `97d2f924678f68c9aed7f60dbf41e73311012ebd`와 새 v5 pinset
`cbb577d37e664c56d11ed97f70117911b77547921857287fa87da1b73ce24fc5`으로 고정한다.

이 회전은 historical `872e3262…` candidate·journal·receipt·image·Compose·DB/runtime을 수정하거나
재사용하지 않는다. Manager #240 trusted immutable-base preflight의 merge·deployment 뒤 새 pinset의 official
candidate를 정확히 한 번 실행하고, `committed` evidence가 생기기 전에는 PinVi M05 live E2E를 시작하지 않는다.

---

## 2026-08-27 — Manager 컬러 톤을 호박색(amber) 계열로 재조정

`tokens.css`의 OKLCH 팔레트를 코랄 계열(hue 32)에서 호박색 계열로 재조정했다. 기존 `--color-warn`이
이미 hue 72(주황빛 amber, 경고색)를 쓰고 있어 brand를 같은 hue로 두면 "주요 실행"과 "경고" 색이
구별되지 않는 문제가 있었다. brand는 hue 85(더 노란/황금빛)로 warn과 hue를 13° 띄우고, lightness도
54%로 warn(64%)보다 뚜렷이 어둡게, chroma도 0.14로 낮춰 — 밝고 채도 높은 주황(warn)과 짙은 호박·
캐러멜 톤(brand)이 명확히 구별되도록 세 축(hue·lightness·chroma)을 함께 분리했다. neutral 계열
(paper/ink/graphite/shadow 틴트)은 hue 75로 맞춰 brand와 같은 따뜻한 계열을 유지했다. info/warn/
danger/ok 상태색 자체는 변경하지 않았다.

WSL에서 `next build`/`eslint`/`tsc --noEmit`을 통과시켰고, 로컬 QA 세션에서 로그인 화면과 인증된
대시보드를 Playwright로 확인해 brand accent가 warn(경고) 배지·색과 혼동되지 않음을 확인했다.

---

## 2026-08-27 — Map immutable Python base의 trusted candidate preflight 후보

n150의 `872e3262…` candidate는 API receipt 전 generic paired builder 오류로 fail-close했다. 이후
read-only Docker inspect로 Map API Dockerfile이 요구하는 digest-pinned Python base가 host image store에 없음을
확인했다. 같은 exact Map source의 sealed API candidate는 로컬에서 receipt까지 발행됐으므로 source tree나
PinVi evidence를 추측·수정하지 않는다.

Manager는 materialized·immutable Map source의 API/Dagster Dockerfile에서 `builder`/`runtime`의 동일한
`python@sha256` base만 엄격하게 읽고, cache 부재일 때만 raw stdout/stderr를 `DEVNULL`으로 폐기한 trusted
`docker pull` 뒤 다시 inspect한다. Dockerfile contract·pull·재관측 어느 하나라도 실패하면 paired build와
journal/Compose/DB/runtime mutation 전에 닫는다. 같은 pinset은 다시 실행하지 않으며, Map execution journal을
병합해 새 source pinset을 만든 뒤에만 fresh candidate를 한 번 실행한다.

---

## 2026-08-27 — paired builder failure의 비밀 비포함 분류 후보

새 `872e3262…` pinset의 trusted `rebuild-pinned --confirm`은 source worktree와 paired-builder
file의 root ownership·exact revision을 확인한 뒤, candidate journal·receipt·DB/runtime mutation 전
`application 300 paired builder failed`로 fail-closed했다. 원문 stderr는 기록하거나 출력하지 않았고,
같은 candidate를 자동 재시도하지 않는다.

후속 진단은 sealed builder stdout/stderr를 저장·전달·파싱하지 않는다. 대신 Manager private state에서
owner-only mode `0600` receipt 두 개의 존재 상태만 확인해 `api_receipt_missing`,
`paired_receipt_missing`, `unclassified`로 분류한다. unsafe·partial·충돌 상태는 항상
`unclassified`다. 이 분류는 secret·raw diagnostic·path를 포함하지 않으며 다음 trusted candidate의 단 한
번의 결과에서 후속 source 계약을 분리하기 위한 것이다.

---

## 2026-08-27 — Map M05·PinVi #487 merge source의 fresh pinset 회전 후보

Map #1081 merge `cf65e97345b5792420cfbc994e49ce6a7e3cd650`와 PinVi #487 squash merge
`97d2f924678f68c9aed7f60dbf41e73311012ebd`를 새 runtime source authority로 함께 고정했다.
canonical compact JSON의 v5 pinset은
`872e3262275190208553db4f31c865882365f46d67b9e40b99ef66af1154d457`이다. Map application-300
source commit도 같은 Map merge로 결박했다.

기존 d9 pinset과 `map_runtime_ready` journal은 historical failure evidence로 보존한다. 이 회전은
그 journal·candidate·image·Compose·DB·role을 변경하지 않으며, 새 pinset만 fresh v8 journal로
candidate build와 attestation을 시작할 수 있다. Manager PR의 두 전문 적대 리뷰·검증·trusted deployment
전에는 rebuild를 호출하지 않는다.

---

## 2026-08-27 — receipt 이전 d9 role topology failure의 pre-mutation 차단

서로 독립인 두 전문 적대 리뷰가, Manager #235의 새 `pinvi_role_lifecycle_block` receipt가 이미
`map_runtime_ready`에 남은 d9 historical journal에는 존재하지 않아 첫 재실행을 막지 못함을 확인했다.
historical journal을 소급 수정하지 않는 원칙은 유지한다. 대신 release policy에 d9의 exact pinset,
Map/PinVi source revision과 `map_runtime_ready` phase를 고정하고, 그 조합은 role credential write,
source materialize, paired build, Docker/Compose와 DB mutation보다 먼저 같은 generic 오류로 거부한다.
새 pinset은 이 one-off policy에 일치하지 않아 fresh v8 journal로만 진행한다. raw stderr·DSN·role·비밀번호·path는
policy, journal, 오류와 문서에 기록하지 않는다.

pre-PR v8 payload에서 새 receipt field를 제거한 회귀는 credential write와 source/build/Compose/DB 호출 전
차단됨을 고정한다.

---

## 2026-08-27 — Manager 컬러 톤을 코랄 계열로 재조정

`tokens.css`의 OKLCH 팔레트를 보라 계열(hue 295/298/300)에서 코랄 계열(hue 32)로 재조정했다.
`--color-brand`/`--color-brand-ink`/`--color-brand-tint`뿐 아니라 paper/ink 계열 neutral과
`--color-graphite*`, `--shadow-*` 틴트 hue도 함께 32로 맞춰 geo와 동일한 "neutral이 accent
hue 근처에 머문다"는 원칙을 유지했다. `--color-info`(파랑, hue 260)와
`--color-warn`/`--color-danger`/`--color-ok`는 이전과 마찬가지로 변경하지 않았다 — 특히
danger(hue 28)와 hue가 가깝기 때문에 brand는 danger보다 훨씬 밝고(64% vs 53%) chroma도 낮게
(0.19 vs 0.2) 잡아 시각적으로 분명히 구별되도록 했다. 레이아웃·타이포그래피·spacing·radius·
duration 등 구조적 토큰은 이전 보라 톤 작업에서 그대로 가져왔으며 이번 변경 대상이 아니다.

WSL에서 `next build`/`eslint`/`tsc --noEmit`을 통과시켰고, 로컬 QA 전용 admin credential로
로그인 화면과 인증된 대시보드(사이드바 활성 nav·KPI·아이콘 배경)를 Playwright로 확인해 코랄
accent가 기존 danger(빨강)/ok(초록) 상태색과 혼동 없이 구별됨을 확인했다.

---

## 2026-08-27 — d9 PinVi role topology의 동일 후보 재실행 차단 후보

Manager #234 trusted release에 lifecycle stage와 allowlisted 비밀 비포함 failure code가 반영된 뒤,
모든 읽기 전용 preflight를 마친 d9 candidate에 승인된 official rebuild를 정확히 한 번 실행했다. 결과는
`role_topology_noncanonical`이었고, v8 journal은 기존 `map_runtime_ready` generation으로 남았다.
`pinvi-admin-bootstrap` one-shot의 생성·성공 증거는 없다. 따라서 이 실행은 Map/PinVi live acceptance나
M01~M05 activation의 근거가 아니며, d9 command는 다시 실행하지 않는다.

후속 Manager 후보는 role lifecycle이 정확히 `pinvi_role_open` 또는 `pinvi_role_seal`에서
`role_topology_noncanonical`으로 실패할 때에만 비밀 비포함 terminal receipt를 같은 v8 journal에 fsync한다.
같은 pinset의 다음 rebuild admission은 role credential write, source materialize, paired build, Docker/Compose,
DB mutation과 role one-shot보다 먼저 이 receipt를 읽고 일반화된 차단 오류로 종료한다. 다른 stage/code와 raw
stderr·DSN·role·비밀번호·path는 receipt와 public error에 저장하지 않는다. 기존 d9 journal을 소급 수정하지
않으며, 새 guard가 merge·CI·전문 적대 리뷰·trusted deployment를 통과하기 전에는 어떤 rebuild도 재실행하지
않는다.

Linux `/tmp` 보안-mode filesystem에서 journal/lifecycle/rebuild focused regression 14개와 해당 두 test
module 전체 130개, 변경 module Ruff 및 strict mypy를 통과했다. 전체 backend suite는 Docker integration
cleanup까지 시작했으며 hosted CI에서 다시 확인한다.

---

## 2026-08-26 — d9 PinVi role lifecycle의 비밀 비포함 단계 진단 후보

Manager #233의 fetch 가능한 Map merge pin 회전을 trusted release로 배포한 뒤, 사용자 승인된
official rebuild를 새 pinset `d9aded44779114ed0595d3a4fb50908efb56b57c85148faf3083b0087a35e898`에서
한 번 실행했다. Map paired build, application/Dagster schema와 Map API/UI/Dagster/daemon 준비까지 통과해
v8 journal은 `map_runtime_ready`에 도달했다. 이어 PinVi role lifecycle은 `pinvi-db-runtime-role` open과
fail-closed cleanup seal 사이에서 0이 아닌 종료로 끝났으며, `pinvi-admin-bootstrap` one-shot은 생성되지
않았다. candidate는 `committed`가 아니므로 Map/PinVi live acceptance의 근거로 승격하지 않는다.

n150 candidate·journal·DB·role·Compose에는 수동 변경을 하지 않았다. exact pinned role script의
open → seal은 같은 PostGIS 16 이미지의 분리된 일회용 local database에서 성공했으므로, 현 증거만으로
script의 결정적 결함이나 role의 실제 봉인·미봉인 상태를 단정하지 않는다. raw stderr·DSN·role·password·path는
receipt나 public error에 저장·출력하지 않는다.

후속 Manager 후보는 Compose argv, open → admin bootstrap → seal 순서, root secret mount, cleanup,
journal phase와 candidate 입력을 바꾸지 않는다. `pinvi-db-runtime-role`의 exact 고정 비밀 비포함 문구만
`role_input_invalid`, `role_endpoint_not_ready`, `role_existing_owner_noncanonical`,
`role_topology_noncanonical`으로 allowlist하고, 나머지는 `unclassified`로 처리한다. lifecycle 오류에는
`pinvi_role_open`·`pinvi_bootstrap_credential`·`pinvi_admin_bootstrap`·
`pinvi_bootstrap_credential_cleanup`·`pinvi_role_seal` 중 해당 stage와 allowlisted code만 보존한다.
원문 출력, 자동 retry, topology/권한 완화, journal 소급 기록은 추가하지 않는다. focused 14개와 native Linux
temporary directory에서 backend `test_pinned_runtime_rebuild.py` 87개, 변경 service Ruff 및 strict mypy를
통과했다. 두 전문 적대 리뷰와 PR gate가 끝나기 전에는 rebuild를 재실행하지 않는다.

---

## 2026-08-26 — fetch 가능한 Map merge commit으로 pinned source 회전

PinVi role bootstrap source checkout을 exact approved release로 수렴한 뒤 official rebuild를 한 번
재개했다. raw C6c와 external readiness를 통과한 뒤 isolated root-owned bare source namespace가 Map
PR #1066의 deleted head `cc81081…`를 canonical GitHub URL에서 exact fetch하려 했으나 object를 받지 못해
fail-close했다. source worktree, paired builder/image, candidate journal/manifest, Docker/Compose runtime,
role one-shot과 세 DB reset은 시작하지 않았다.

`cc81081…`의 local tree와 canonical remote에서 exact-SHA fetch가 검증된 #1066 merge
`14d18230…`의 tree는 정확히 같다. 따라서 Manager source authority는 PR head를 되살리거나 local object를
root Git input으로 쓰지 않고, 동일 Git 트리의 exact fetch가 검증된 머지 커밋으로만 회전한다. PinVi #488
`93296aee…`는 #477 merge를 ancestor로 포함하므로
그대로 유지한다. 새 canonical pinset은 `d9aded44779114ed0595d3a4fb50908efb56b57c85148faf3083b0087a35e898`이다.
trusted deployment와 새 pinset candidate의 full attestation 전에는 공식 rebuild를 재호출하지 않는다.

---

## 2026-08-26 — PinVi role source prebuild fail-close 기록

Manager #230의 Debian provenance offline wheelhouse와 #231의 versioned issuance 경로를 trusted
`/opt` release로 설치한 뒤, 사용자 승인된 `pinvi-pair rebuild-pinned --confirm`을 한 번 실행했다.
공식 installer는 exact source revision과 explicit versioned wheelhouse를 대조해 성공했고, 기존
provenance-unknown default wheelhouse는 읽거나 수정하지 않았다.

rebuild는 raw C6c prebuild transaction에서 `pinvi-db-runtime-role`의 canonical read-only source bind가
없음을 발견해 종료했다. n150 source-owner checkout의 `HEAD`는 `25505e056…`이고 Manager current pin은
`93296aee…`이다. 필요한 `infra/postgres/bootstrap-pinvi-runtime-role.sh`는 전자에는 없고 후자에는
tracked regular file로 있다. checkout HEAD 자체가 candidate authority는 아니지만, source materialize보다
앞서 raw bind graph가 존재성을 확인하므로 이 file은 release deployment의 명시적 precondition이다.

이 failure는 source materialize, paired builder/image build, journal/manifest write, Docker/Compose runtime
mutation, role one-shot, 세 DB reset 이전에 발생했다. fresh root environment였다면 여섯 PinVi role
credential의 atomic initialization과 lock/state directory 준비만 선행할 수 있으며, complete tuple은 다음
official retry가 validate·reuse하고 수동 삭제·회전하지 않는다. source는 clean canonical origin이고 exact
pin object도 이미 보유하므로, WIP를 reset하거나 script를 복사하지 않고 source owner가 same checkout을
approved exact release로 수렴한 뒤에만 동일 command를 한 번 재개한다. bind/guard 완화나 manual
Docker/Compose/SQL·journal/DB 조작은 금지한다.

---

## 2026-08-26 — Debian provenance 기반 offline wheelhouse 발행 후보

Manager #229의 clean merged source를 공식 trusted installer로 설치할 때, 기존 root-owned source
wheelhouse에는 runtime dependency만 있고 backend build backend인 `poetry-core` wheel이 없어서
`pip wheel --no-index`가 activation 전에 fail-close했다. transaction staging/rollback은 installer가
정리했고 canonical `.env`, Docker/Compose, candidate, journal, runtime, DB에는 write가 없었다.

후속 후보는 network·PyPI·home/user-writable 입력을 쓰지 않는다. dedicated provisioning 도구는 기존
root-owned source wheelhouse의 file identity를 snapshot하고, Debian `python3-poetry-core`의 installed
status와 `dpkg --verify`를 확인한 뒤 installed pure-Python package로부터 standards-compliant
`poetry_core-<version>-py3-none-any.whl`를 만든다. source wheel과 생성 wheel SHA만 담은 비밀 비포함
provenance manifest를 같은 temporary directory에 쓰고 fsync한 뒤, root-owned default destination을
atomic publish한다. Debian evidence command는 fixed `/usr/bin/dpkg`·`/usr/bin/dpkg-query`와 minimal
environment만 사용한다. 기존 destination은 no-replace publish로 덮어쓰지 않으며 concurrent 발행은
root-only lock으로 거부한다. crash 뒤 `.wheelhouse.stage.*` residue와 source의 동일 `poetry-core`
wheel filename 또는 다른 version·대소문자의 모든 `poetry-core` candidate는 자동 정리·교체하지 않고
fail-close한다.

focused regression은 generated wheel의 member/`RECORD`, destination no-overwrite, Debian package drift
reject를 다룬다. bootstrap에서 user-writable checkout의 Python/Bash를 root가 직접 실행하지 않는다.
root operator는 out-of-band release attestation의 exact commit과 SHA-256으로 Git blob을 root-owned
temporary file에 복사·검증하고, 그 staged file만 clean environment로 실행한다. installer도 같은
staging과 expected source revision을 사용한다. 새 PR의 두 전문 적대 리뷰와 전체 backend gate,
trusted deployment가 끝나기 전에는 `rebuild-pinned`를 다시 실행하지 않는다.

기본 destination이 다른 release·operator의 root-owned artifact와 충돌하면 그 artifact를 삭제·수정·채택하지
않는다. exact merged commit을 포함한 새 root-owned destination을 provisioner와 installer에 함께 넘기는
versioned issuance만 허용한다. 이 선택은 existing wheelhouse의 provenance 불명 상태를 정상 artifact로
승격하지 않으면서, concurrent 운영 WIP와의 충돌을 피한다.

---

## 2026-08-26 — fresh PinVi role credential의 trusted rebuild/rebind 보정 후보

Concierge boundary #228을 trusted release로 설치하고 sanctioned stage·retire와 공개 HTTP/실제 browser
login → authenticated BFF → logout → re-block acceptance를 끝낸 뒤, 승인된
`pinvi-pair rebuild-pinned --confirm`을 다시 실행했다. 이 실행은 PinVi database URL identity 계약에서
멈췄다. 허용된 이름·선언 여부만 읽기 전용으로 대조한 결과 canonical root `.env`에 새 M05 dedicated
role topology의 여섯 값이 모두 없었다. 따라서 candidate source materialize, image build, journal write,
runtime stop, DB reset은 어느 것도 시작하지 않았다.

초기 후보의 적대 리뷰는 두 P1을 지적했다. 첫째 root command가 ambient Manager 경로를 따라가거나 root
`.env`를 바꾸기 전에 rebuild lifecycle/C6c token을 확인하지 않았다. 둘째, 새 pinset의 v8 journal이 이미
`map_runtime_ready`인데 여섯 값이 없으면 environment SHA·resolved Compose SHA가 달라져 재개가 fail-close한다.

보정 후보는 trusted `/opt/kor-travel-docker-manager` root-owned Compose·`.env` pair만 사용하며, role을 쓰는
`.env`는 O_NOFOLLOW descriptor와 file identity로, Compose source는 frozen file identity로 확인한다. pinned root
`.env` 값은 caller process 또는 dotenv 보간을 섞지 않는 literal snapshot으로 다룬다. exact `rehearsal/rebuildable`, PinVi
production, Map principal-required, C6c token과 journal admission을 모두 통과한 뒤에만 fresh role을 기록한다.
그 journal은 정확히 `map_runtime_ready`인 경우에 한해, root write에 남긴 이전 environment SHA marker와 새
candidate raw/resolved Compose digest를 대조하여 단 한 번의 비밀 비포함 rebind receipt로만 이어진다. partial·
blank·duplicate·다른 phase/digest·path/identity drift는 기존 값을 추측하거나 덮어쓰거나 회전하지 않고
fail-close한다. 완전한 기존 credential은 재사용만 하며 원문 credential은 Compose output, CLI result, durable
journal, log에 넣지 않는다.

이 변경은 진행 중인 PinVi source 작업을 건드리지 않으며, PinVi API/Compose 계약의 strict explicit guard도
약화하지 않는다. 두 전문 적대 리뷰와 전체 backend gate, trusted deployment가 끝나기 전에는 n150 rebuild를
재시도하지 않는다.

---

## 2026-08-26 — Concierge retirement를 무관한 PinVi candidate interpolation에서 분리하는 보정 후보

#227 merge trusted release를 n150에 공식 installer로 반영한 뒤 protected snapshot을 확인하고 sanctioned
`retire-legacy-override`를 실행했다. API auth 세 값의 canonical root fallback은 통과했지만, archive·Concierge
recreate 전에 full Manager Compose가 아직 materialize하지 않은 PinVi role candidate의 explicit credential guard를
해석해 fail-close했다. 이 실패는 pending snapshot·legacy source·Docker/Compose runtime·DB를 바꾸지 않았다.

후속 후보는 full Compose를 약화하거나 PinVi 값을 임의로 채우지 않는다. trusted canonical Compose에서 Concierge
API/MCP/scheduler/UI와 transitive `depends_on` service, 그 service가 실제 참조한 top-level
secret/network/volume/config만 root-owned temporary projection으로 만든다. raw/resolved C6c 검증과 archive 뒤 네
service recreate가 같은 projection을 사용하므로, retirement에 무관한 Map/PinVi candidate guard가 Concierge
preflight를 막지 않고 그쪽 source·value·runtime도 이 경로에 유입되지 않는다.

projection은 trusted canonical file의 안전한 parent에 `0600`으로 생성해 실행 동안만 쓰고 즉시 제거한다. caller와
legacy home source는 projection path·내용·Compose cwd·env-file을 지정할 수 없다. UI/API auth allowlist, root
authority fallback 범위, protected pending→archive와 rebuild host lease는 그대로 유지한다. 값·경로·digest는
출력하거나 기록하지 않는다.

---

## 2026-08-26 — legacy UI source의 미선언 API auth 값을 canonical root authority로 재검증하는 보정 후보

#226 merge trusted release를 n150에 공식 installer로 반영한 뒤, 이전에 준비된 final source를 `stage-legacy-override`
공식 경로로 다시 처리했다. stage는 protected pending snapshot을 만들었고 Docker/Compose·DB·runtime·canonical root
`.env`를 바꾸지 않았다. 이어 `retire-legacy-override`는 archive·candidate root `.env` write·canonical Compose 검증
전에 legacy UI source가 API runtime 값을 선언하지 않았다는 이유로 fail-close했다.

읽기 전용 환경 이름 대조 결과 canonical root에는 대응 `KOR_TRAVEL_CONCIERGE_*` API auth authority가 이미 있으며,
legacy UI source에는 UI 전용 값만 있었다. 보정 후보는 `API_KEYS`, `APP_ENV`, `API_AUTH_ENABLED`가 source에 **아예
없을 때만** root의 대응 값을 읽어 기존 API key-set/backend key membership·`production`·authentication-enabled
검증에 함께 넣는다. 이 fallback은 candidate update를 만들지 않으므로 root authority를 덮어쓰지 않는다. source가
세 값을 선언했다면 빈 값도 포함해 source 값이 여전히 필수이며, UI의 `KTC_*` credential·session·proxy·origin에는
fallback을 추가하지 않는다.

따라서 이 후보는 trusted `/opt` Compose execution root, protected pending→archive, raw/resolved C6c 계약, source
descriptor 검증, home source의 Compose argv/cwd/env-file 비유입을 유지한다. 실제 API auth 값·source path·digest는
출력하거나 기록하지 않는다.

---

## 2026-08-26 — 실제 legacy Compose 장형 `env_file`를 exact sibling source로 수용하는 보정 후보

#224 merge trusted release를 n150에 공식 installer로 반영한 뒤 root-only `stage-legacy-override`를 한 번
실행했다. 이 명령은 Docker/Compose·DB·runtime·canonical root `.env`를 바꾸기 전에, 실제 legacy UI가 쓰는
Compose 장형 `env_file` mapping을 구형 상대 문자열 allowlist가 인식하지 못해 fail-close했다. stage 입력의
final source `.env`는 이 명령의 owner-only precondition에 맞췄지만 pending snapshot이나 Compose mutation은 만들지
않았다.

후속 후보는 자유로운 path 형식을 열지 않는다. 구형 `../kor-travel-concierge/.env` 한 문자열은 그대로
수용하고, 장형 표현은 override 위치에서 계산한 동일 sibling `.env`의 `path`와 boolean `required: true`만 든
한 mapping으로 한정한다. 실제 source가 명시한 Compose raw mode `format: raw`는 같은 exact mapping에서만 추가로
수용한다. 다른 absolute path·추가 key/format·`required: false`·여러 source는 계속 stage 전 fail-close한다.
descriptor-safe final-file snapshot, trusted `/opt` canonical execution root, protected pending→archive, home source의
Compose argv/cwd/env-file 비유입은 변경하지 않는다.

---

## 2026-08-26 — trusted release/runtime split을 legacy Compose handoff로 고정하는 후속 후보

Manager #223은 merge 뒤 trusted release로 배포됐지만, installed shim이 고정한 canonical `/opt` project root를
retirement code가 blanket 거부해 공식 `retire-legacy-override`가 mutation 전에 fail-close했다. 이 실패는 root
`.env`, Docker/Compose, DB, runtime을 바꾸지 않았으므로 P0 incident는 아니지만, rebuild 선행 조건의 sanctioned
경로가 막혀 P1 release blocker다. home checkout의 Compose YAML과 parent directory는 user-writable이므로 단순
거부 해제·home cwd/Compose 사용·legacy override 자동 병합은 root Docker mutation에 신뢰할 수 없는 입력을 주입하는
P0가 되어 허용하지 않는다.

후속 후보는 trusted `/opt` root·`.env`·canonical Compose·C6c 검증·Compose cwd를 execution authority로 유지한다.
별도 `stage-legacy-override --source <absolute-path> --confirm`은 Docker/Compose를 호출하지 않고, root-owned
`0600` final legacy override와 고정 sibling Concierge `.env`를 `O_NOFOLLOW` descriptor/fstat로 snapshot해 fixed
C6c state 아래 owner-only pending directory에 원자적으로 넣는다. user-writable home parent는 snapshot 전
availability를 방해할 수는 있어도 root-owned final input을 바꾸거나 Manager Compose source가 될 수 없다. source는
stage 뒤 삭제·rename·재사용하지 않는다.

`retire-legacy-override`는 pending snapshot만 읽어 root `.env` candidate와 raw/resolved canonical Compose를
검증하고, 성공 시 같은 protected filesystem에서 pending directory 전체를 archive로 rename한다. pending이 있으면
`activate-concierge`도 fail-close한다. snapshot이 없거나 내용이 달라지면 root 설정·Docker runtime 변경 없이 중단한다.
archive 뒤 durability와 재생성 실패의 기존 typed fail-close 의미론은 유지한다. 값·source path·credential·digest는
출력하거나 이 일지에 기록하지 않는다.

추가 읽기 전용 config 확인에서 n150의 canonical pinned rebuild가 `rehearsal/rebuildable` mode와 non-default C6c
state를 사용한다는 사실을 확인했다. 따라서 production-only gate나 production fixed-state 강제는 두 번째 P1이 된다.
후속 보정은 trusted `/opt` root를 여전히 고정하면서, exact rehearsal/rebuildable·PinVi production·Map principal-required
contract에서는 `rebuild-pinned`와 같은 root-owned host lease 및 해당 C6c state를 사용한다. caller가 mode·project
root·stage root·lock path를 주입하거나 home Compose를 실행 root로 바꾸는 경로는 추가하지 않는다.

---

## 2026-08-26 — legacy Compose override를 canonical UI 경계로 이관하는 후보

Manager #222를 n150에 반영한 뒤 승인된 `rebuild-pinned --confirm`은 DB reset·image build·journal write 전에
실제 `docker-compose.override.yml` 존재를 발견해 single-file boundary에서 fail-close했다. 읽기 전용 topology
점검 결과 이 legacy override는 Geo backup의 네 runtime 값을 덮고 Concierge UI에 production command와 전체
Concierge `.env`를 전달했다. 후자는 provider/LLM/search key까지 UI process에 섞일 수 있어 canonical runtime
경계가 될 수 없다.

후속 후보는 Geo backup 값의 정식 root `.env` source를 유지하고, Concierge UI에는 auth username·password hash·
session secret·admin proxy secret·trusted-proxy flag·public origin·same-origin BFF 설정만 exact allowlist로 전달한다.
UI backend origin은 canonical loopback API 주소에 고정하고, API와 UI의 admin proxy secret은 같은 Manager root source를
쓴다. UI command는 auth 값이 비었거나 session/proxy secret이 짧으면 fail-fast하고 production build/start를 수행한다.

`ktdctl compose-boundary retire-legacy-override --confirm`은 root-only로 legacy override의 알려진 Geo 값과
canonical sibling Concierge source의 정확한 이름만 raw 파싱한다. backend key가 `API_KEYS`의 exact member인지, 모든
입력이 regular file·안전 mode인지, 기존 root 값이 source와 충돌하지 않는지를 검사한다. 또한 API의 `APP_ENV=production`과
`API_AUTH_ENABLED=true`를 함께 이관·검증해 override 퇴역 뒤 local/unauthenticated 기본값으로 내려가는 downgrade를 막는다.
candidate `.env`를 atomic 갱신하고 canonical Compose의 실제 raw/resolved 출력을 메모리에서 C6c 검증한 뒤에만 override를
owner-only archive로 rename한다. 이 전 과정은 deployment contract에 따라 직렬화한다. canonical
rehearsal/rebuildable은 pinned-runtime rebuild host lease를, production은 fixed C6c global mutation lock을 사용한다.
archive rename 전 실패면
원래 `.env`를 복구하지만, rename 뒤 directory durability가 불확실하면 candidate `.env`와 archive를 유지한 typed failure로
중단해 split-brain rollback을 만들지 않는다. archive 성공 뒤에는 같은 deployment lock 안에서 Concierge API/MCP/scheduler/UI 정확한
service만 canonical source로 재생성한다. 이 재생성만 실패하면 archive와 candidate를 보존하고
`compose-boundary activate-concierge --confirm`으로 재시도한다. source credential file은 group/other-readable mode와 dotenv
공백·tab duplicate 선언을 거부한다. raw/resolved C6c는 API/UI host network, API loopback port, UI auth guard·production command도
고정한다. special character가 든 fake secret의 dotenv round-trip 회귀를
고정했다. 값·경로·digest는 출력하거나 작업 일지에 기록하지 않는다. 이 후보의 merge·배포와 two-review 승인 전에는 rebuild를
재시도하지 않는다.

---

## 2026-08-26 — pinset artifact preflight 순서 보정 후보

n150의 별도 사전 점검은 source provenance 및 PinVi role credential을 통과했다. 그 뒤 승인된 rebuild의
첫 base candidate는 source materialize보다 먼저 네 Map fence/permit mount path를 빈 `.env` 값으로
해석하면서 DB reset 전에 fail-close했다. 이 path들은 현재 pinset state root에서 결정론적으로 도출되고,
이후 candidate override에도 같은 값으로 전달된다. 따라서 운영자가 pinset별 artifact path를 설정할 문제가
아니라 Manager가 source materialize·image build·journal·DB mutation 전에 directory를 준비해 기본 candidate에
제공해야 하는 순서 결함이다. 이 보정이 merge·재배포되기 전에는 rebuild를 더 재시도하지 않는다.

---

## 2026-08-26 — PinVi M05 role lifecycle 후보 보정

PinVi #488은 source authority `main` commit
`93296aee5d47676e6b9b79303bf417c598a273ac`으로 merge됐다. 이 source 보정은 Manager가 허용하는
PinVi PostgreSQL endpoint pair만 받아 root role bootstrap 환경을 정규화한다. 이전 #477 candidate가
`map_runtime_ready`에서 멈춘 원인은 M05 migration이 요구하는 root·runtime application·schema owner·
migration owner·migrator 분리와 root 단일 DSN 배선의 불일치로 한정했다. raw 실행 로그나 DB 데이터를
근거로 추가 추정하지 않았고, n150에서는 다시 실행하지 않았다.

Manager candidate는 bootstrap profile의 `pinvi-db-runtime-role` one-shot을 추가했다. initial-superuser
secret file은 PostgreSQL, 기존 DB 생성 one-shot, 이 role one-shot만 읽으며 normal API/Dagster에는 runtime
application DSN만, admin bootstrap에는 migrator DSN만 전달한다. rebuild는 migrator login을 open한 뒤
admin/schema bootstrap을 수행하고 성공·실패 모두 explicit seal을 실행한다. C6c는 endpoint
`127.0.0.1:12800`, source bind, exact env/secret/depends-on, role name·password의 상호 분리, normal
runtime으로의 root-secret 누출 금지를 raw/resolved Compose 모두에서 검증한다. materialized source는 read-only
non-executable mode일 수 있으므로 role bootstrap은 직접 exec가 아닌 `sh`로 호출한다. 관련 계약·rebuild 단위 테스트 114건,
Compose mutation fixture 회귀 97건, 전체 backend 테스트 584건과 변경 Python 모듈의 strict type 검사를
통과했다. Manager release pin은 #488 commit과 pinset
`9073c294d6138fff895983adbc9ca483ab2eede6da15bb1ef4888572fe7fe491`으로 회전했다. Manager 적대 리뷰·CI, n150의 role
credential 구성이 끝나기 전에는 rebuild나 live acceptance를 재개하지 않는다.

---

## 2026-08-26 — PinVi #477 새 pinset rebuild 미종결 인시던트 기록

PR #219가 회전한 PinVi #477 source와 정규 pinset
`cb8d15591480111d7f4cd70398ad46b129e814ad3b9375dfa0fc83562b366752`을 사용해 신뢰된 n150
Manager의 승인된 `ktdctl pinvi-pair rebuild-pinned --confirm`을 실행했다. 최초 실행과 동일한
공식 재개를 한 번만 추가로 실행했으나 두 경우 모두 0이 아닌 종료로 끝났고, 새 pinset의 v8
journal은 `phase=map_runtime_ready`, `journal_generation=20`에 남았다. source pin은 Map
`cc81081ff2e540a6ad9c428a296515e1d79bc316`, PinVi #477 squash
`10efb21ad84b23db2eeb6d09856cda16d3337822`와 정확히 일치하지만 새 generation은
`committed`되지 않았다.

두 전문 적대 리뷰어가 독립적으로 확인한 결과, 현재 v8 journal에는 Map runtime 기동, PinVi
bootstrap one-shot, PinVi schema head 확인 중 어느 실패 지점인지를 구분할 비밀 비포함 durable
failure receipt가 없다. raw 실행 출력은 보안상 폐기했으므로, 세 번째 재시도와 raw
Docker/Compose/SQL 조작, DB·journal·permit 삭제 또는 수정은 하지 않는다. 읽기 전용 상태에서는
두 PostgreSQL만 healthy/running이고 seven runtime은 fail-closed 정리 후 종료됐으며 OOM과 Docker
`State.Error`가 없는 것만 확인했다. 이 상태만으로 daemon, Compose 또는 PinVi 원인을 단정하지
않는다.

새 후보가 `committed`되기 전에는 Map/PinVi live consumer acceptance를 진행하거나 H300의 이전
generation을 새 candidate 증거로 재사용하지 않는다. 이번 인시던트에서 application row·건수·
업무상 무결성은 조회·대조하지 않았고, 이전 revision 또는 DB restore도 수행하지 않았다. 다음
조치는 값·로그 원문이 아닌 허용목록 failure stage나 service-level 상태를 얻는 외부 인시던트
절차가 결정한다. 현재 active journal에 hotfix를 덧씌우거나 version을 바꾸지 않는다.

---

## 2026-08-26 — PinVi #477 source pinset 회전 준비

PinVi #477은 squash merge된 `main` commit
`10efb21ad84b23db2eeb6d09856cda16d3337822`을 source authority로 사용해야 한다. 따라서 Map
source `cc81081ff2e540a6ad9c428a296515e1d79bc316`는 유지하고, PinVi source만 merge commit으로
회전한 새 canonical pinset
`cb8d15591480111d7f4cd70398ad46b129e814ad3b9375dfa0fc83562b366752`을 후보
build·attestation의 다음 입력으로 등록했다. PR branch head를 pin하지 않아 squash 이전 commit
history를 authority로 재사용하지 않는다.

이 변경은 n150 runtime, H300 committed generation, 기존 journal을 변경하지 않는다. 기존
generation은 이전 pinset의 immutable 이력으로 보존한다. 새 pinset의 candidate image·세 schema
head·v6/v8 evidence가 모두 exact 검증되고 별도 승인된 rebuild가 committed되기 전에는 #477을
배포됐거나 live acceptance 완료로 주장하지 않는다.

---

## 2026-08-26 — Manager 대시보드를 kor-travel-geo-ui 디자인 시스템(보라 톤)으로 재정렬

Manager 프론트엔드의 매크로구조를 상단 topbar 단일 페이지에서 `kor-travel-geo-ui`와 동일한
좌측 고정 사이드바 Workbench 구조(작은 화면에서는 접근 가능한 drawer)로 전환했다. 신규
`AppShell` 컴포넌트가 브랜드 마크·단일 "대시보드" nav·사이드바 footer(빠른 명령/인증
설정/백업 이력/로그아웃)를 담당하며, `DashboardClient`는 기존 `.ops-overview`/`.ops-ledger`
등 콘텐츠 마크업을 그대로 `AppShell`의 children으로 옮겼다. geo design.md의 "별도 풋터 없이
사이드바 로그아웃만 둔다" 규칙에 따라 콘텐츠 하단 풋터 바는 제거했다.

`tokens.css`의 OKLCH 팔레트는 hue만 회전시켜(neutral 255→295, ink 258→298, brand 260→300)
보라 계열로 재조정했고 `info`는 원래 hue(260, 파랑)를 유지해 brand와 분리했다. radius(0.375/
0.625rem→0.5/0.75rem)·duration(120/220/420ms)·easing·z-index 토큰은 geo와 동일한 값으로
맞췄다. 폰트는 next/font로 로드하던 IBM Plex Sans/Space Grotesk를 걷어내고 geo와 동일한
시스템 폴백 스택("Pretendard Variable"/"Noto Sans KR"/"Apple SD Gothic Neo")으로 교체했으며,
사이드바·이력/상태 라벨·테이블 헤더·KPI 숫자의 font-family를 mono→display로 바꾸고
font-weight/letter-spacing을 geo의 실제 수치(예: page-title h1 weight 760, nav-title 750,
panel-header 740)에 맞춰 정렬했다. `ktdctl db-backup` 히스토리 API 라우트는 이번 변경으로
사이드바에 다시 노출되며 실제로 정상 응답하는 것을 확인했다(v5 rebuild 때의 404 회귀는 이미
해소돼 있었음).

WSL에서 `next build`/`eslint`/`tsc --noEmit`을 모두 통과시켰고, 로컬 QA 전용 admin
credential로 백엔드를 띄워 데스크톱·390px 모바일 drawer·명령 팔레트·백업 이력 모달을
Playwright로 직접 확인했다.

적대적 리뷰어 서브에이전트 1건을 별도로 수행해 geo의 실제 `AppShell`/`use-modal-a11y` 구현과
직접 대조시켰다. 확인된 findings와 조치: (1, high) 모바일 drawer가 `role="dialog"
aria-modal="true"`를 달고도 Tab 트랩이 없어 `<main>`으로 포커스가 새는 문제 — drawer가 열려
있는 동안 `mainRef`에 `inert`를 토글하도록 고쳤다. (2, medium) drawer가 열린 채로 데스크톱
폭까지 커지면 닫기 버튼이 `display:none`이 되며 포커스가 `<body>`로 유실되는 문제 — geo와
동일하게 `<main>`으로 포커스를 되돌리도록 고쳤다. (3, low) 모바일 사이드바 drawer와
`.ops-modal-backdrop`(명령 팔레트 등)가 같은 `--z-modal` 값을 공유해 향후 동시 등장 시 쌓임
순서가 미정의될 수 있는 문제 — `--z-drawer`(300) 토큰을 새로 추가해 사이드바 전용으로
분리했다. (4, nit) `globals.css` 헤더 주석이 옛 테마명("Hallmark: Cobalt")으로 남아 있던
것과 (5, nit) `<main>` 제거 후 재정렬하지 않았던 JSX 들여쓰기 260줄을 정리했다. 리뷰는 OKLCH
hue 회전·`info`/`warn`/`danger`/`ok` 불변·journal의 구체적 수치 인용을 geo 실제 코드와
대조해 모두 정확함을 확인했다.

---

## 2026-08-26 — C7 v4 capture 퇴역과 H300 v6/v8 단일 정본화

H300 committed generation의 실행 authority는 seven-service v6
`pinned-runtime-generation`과 v8 rebuild journal이다. 과거 `pinvi-pair capture`는
five-service `compatible-pair-v4.json`을 교체해 Map C7 runner가 읽게 하던 별도 관측
경로였으며, F1D가 퇴역 대상으로 삼는 v4 artifact를 다시 생성할 수 있었다.

현재 generation에서는 두 증명 경로를 병존시키지 않는다. `pinvi-pair capture` CLI,
전용 service와 v4 runner-shape test를 제거하고, 남아 있던 운영 프로비저닝 task는
`tasks-done.md`로 이관했다. current candidate의 runtime provenance와 D1/F1D-E/D2/41C
후속 검증은 v6/v8 manifest·journal의 seven image와 three-DB identity만 사용한다.
이 정리는 n150에 명령을 실행하지 않았으며 container·DB·manifest와 application data를
변경하지 않았다.

---

## 2026-08-26 — BACKUP-FOLLOWUP Geo standalone 대용량 backup 1회 실측

n150에서 `ktdctl db-backup create geo --timeout 14400`을 한 번 실행했다. Geo application
DB의 standalone `pg_dump -Fc --compress=6` 단계는 manifest `duration_sec=1,311.8`으로
기록됐고, 명령 전체 wall-clock은 `24m 21.903s`(copy·TOC·checksum·manifest 포함)였다. dump
크기는 `4,717,161,289` bytes였다. dump·`.sha256`·`.manifest` 세 파일이 생성됐으며 모두 mode
`600`, `sha256sum -c`는 `OK`, `.copying` 임시 파일은 남지 않았다. 생성된 manifest의 TOC와
Alembic head는 CLI가 기록한 메타데이터로만 확인했다.

이 실행은 Geo runtime/DB를 재기동하거나 restore하지 않았고, application row·건수·업무상
무결성을 조회·대조하지 않았다. Geo backup의 off-box 사본 자동화와 보존 정책은 외부 목적지·
자격증명 결선이 없어 `BACKUP-FOLLOWUP`의 남은 조건이다.

---

## 2026-08-26 — MAP-LIVE-FOLLOWUP PinVi protected-route 재차단 확인

n150 PinVi exact runtime에서 현재 admin live credential로 `/admin/login`에 로그인한 뒤
`/admin` 보호 화면을 열었다. 브라우저 context의 `/auth/logout` 응답은 `204`였고, 같은 context로
`/admin/features`에 재진입했을 때 최종 경로가 `/admin/login`으로 유지됐다. 이 검증은 인증·세션
상태만 확인하고 application row나 PinVi 업무 데이터를 쓰지 않았다.

PinVi WebSocket/mutating loop와 consumer reconciliation은 `MAP-LIVE-FOLLOWUP`의 남은 active
조건이다. Map 쪽 protected-route 재차단은 직전 기록대로 완료됐으며, 300 이후 일반 application
row의 내용·건수·업무상 무결성 검증은 수행하지 않는다.

---

## 2026-08-26 — MAP-LIVE-FOLLOWUP Map protected-route 재차단 확인

n150 Map UI에서 현재 Manager smoke credential로 login한 뒤 `/ops/datasets`를 열고 logout을
실행했다. logout 응답은 `200`, 이동 경로는 `/login`이었고, 같은 session으로 protected
`/ops/datasets`를 다시 열었을 때도 최종 경로가 `/login`으로 유지됐다. 이 수동 browser check는
session/auth 상태만 확인하고 application row나 PinVi 데이터를 쓰지 않았다.

PinVi equivalent reblock, PinVi WebSocket/mutating loop와 consumer reconciliation은
`MAP-LIVE-FOLLOWUP`의 남은 active 조건이다.

---

## 2026-08-26 — 퇴역 전 C7 capture 관측 기록 (현재 실행 근거 아님)

이 항목은 v4 퇴역 전에 수집한 historical evidence이며, 현재 `capture` 실행·검사 또는 C7
재개의 근거가 아니다. 당시 n150 trusted Manager 설치본에서 `ktdctl pinvi-pair capture --help`를
mutation 없이 실행했다.
출력의 `capture_contract=pair-capture-v1`과 “실행 중 컨테이너를 시작·정지·재생성하지 않고
빌드하지 않는다”는 당시 read-only 경계를 확인했다. 그 구현·manifest는 퇴역됐으며, 현재
generation에는 v6/v8 committed evidence만 사용한다. 이번 historical observation에서는
manifest·컨테이너·DB를 변경하지 않았다.

---

## 2026-08-26 — MAP-LIVE-FOLLOWUP Map ops read/auth 계약 결선

Map exact pair의 n150 frontend에서 `ops-c7-read-auth.live.spec.ts`를 1 worker로 실행했다.
실제 logout UI socket close/`/login` redirect 1개와 ticket 없음·서명 변조 `4401`, expired ticket
`4408` 뒤 fresh lease, healthy socket의 자연 `4408` rotation 3개가 통과했다(총 4개).
이 시나리오들은 application row를 쓰지 않고 auth/WebSocket wire만 확인했다.

Map 저장소 PR #1070 merge `2eeae9b5b588cb3fadca521c496f159b09967e05`에 이 결과를 기록했다.
현재 남은 `MAP-LIVE-FOLLOWUP`은 logout 뒤 protected route 재진입 재차단, PinVi WebSocket/mutating
loop, consumer reconciliation이며, data-dependent D2와 함께 정해진 후속 순서에서 실행한다.

---

## 2026-08-26 — application `300` 최종 n150 수락

Map PR #1066의 exact source `cc81081ff2e540a6ad9c428a296515e1d79bc316`과 Manager PR #207의
merge commit `ecfbddb7b3d1afbd74646abbaa4082dd70b53a42`를 사용한 trusted Manager 설치본에서
승인된 `ktdctl pinvi-pair rebuild-pinned --confirm`을 재개했다. 고정 pinset digest는
`14a9a512836a48489146dc2bb0a04de309cf451b274b934d79805d171f83a193`이다.

- durable journal: `version=8`, `journal_generation=32`, transaction
  `5121a6d2-692d-4bd9-a5b0-d572d58c0f8f`, 최종 `phase=committed`
- Map API/UI/Dagster와 PinVi runtime은 재생성된 image 기준으로 healthy/readiness 상태를 확인했다.
  committed journal candidate에는 Map·PinVi image ID, Map/Dagster/PinVi head, 세 DB identity,
  pinset digest와 application candidate evidence가 함께 결박돼 있다. application row의 내용·건수·
  업무상 데이터 무결성은 조회하거나 대조하지 않았다.
- 이전 revision 또는 기존 DB 복구·restore는 수행하지 않았다. 필요하면 `300` fresh schema에
  source/ETL을 처음부터 재적재한다.
- 실제 브라우저 login setup과 data-independent live UI 시나리오를 n150에서 실행해
  scenario catalog, backup-only 정책(`execute=false`), 운영 홈, 운영 로그의 **11개 테스트가 모두 통과**했다.
  구성은 `auth.setup.ts` 1개, `admin-scenario-catalog.live.spec.ts` 4개,
  `backups-restore.live.spec.ts` 2개, `home-dashboard-roundtrip.live.spec.ts` 2개,
  `logs.live.spec.ts` 2개이며, 실행 보고서는 n150의
  `/tmp/kor-travel-map-playwright/admin-frontend-live/report`에 생성됐다.
  Features의 초기 목록·검색·필터·정렬·반응형·딥링크도 통과했으며, 실제 두 번째 페이지나 고정 ID/컬렉션을
  전제하는 테스트의 실패는 fresh schema 정책에 따른 데이터 의존 항목으로 수락 게이트에서 제외했다.
- 이 수락 묶음은 login setup과 protected view의 브라우저 계약만 다룬다. logout→재차단과 PinVi
  WebSocket/mutating loop는 실행하지 않았다. 해당 독립 운영 acceptance는 이 저장소의
  `MAP-LIVE-FOLLOWUP`과 [Map 저장소 `docs/tasks.md`](https://github.com/digitie/kor-travel-map/blob/main/docs/tasks.md)의
  `T-VN-41C`·`T-VN-41F1D-D2`가 소유한다.
- 현재 Manager `.env`의 smoke credential로 `POST /api/auth/login`은 `200`과 `Set-Cookie`를 반환했다.
  배포 런북의 이전 credential 후보는 현재 runtime에서 `401`로 거부됐으며, 비밀번호를 문서나 코드에
  기록하거나 재설정하지 않았다.

따라서 T-VN-41-F1D-H300의 destructive rebuild·runtime provenance·이번 live UI 수락 조건을 완료 처리하고,
진행 중 백로그에는 C7 read-only capture, `MAP-LIVE-FOLLOWUP`, standalone backup 후속을 남긴다.

---

## 2026-08-26 — n150 application `300` rebuild의 Map 이미지 entrypoint 계약 정렬(PR #207)

PR #206의 고정 recovery/probe argv 수정 후 trusted Manager를 n150에 설치하고 승인된
`ktdctl pinvi-pair rebuild-pinned --confirm`을 재개했다. Map fresh root/finalize, metadata,
Dagster, PinVi schema 단계는 통과했지만 최종 runtime secret isolation에서 Map API image가
실제로 봉인한 `Entrypoint=["/app/docker/api-entrypoint.sh"]`, `Cmd=null`을 Manager가 이전 Map
계약인 `Entrypoint=null`, `Cmd=["./docker/api-entrypoint.sh"]`로 기대해 fail-closed 중단됐다.

이는 DB·schema·행 데이터의 무결성 실패가 아니라 Map `8b433827` 이후의 image Dockerfile 실행
경계와 Manager runtime attestation의 stale contract 불일치다. PR #207은 Manager의 기대값과
운영 문서를 현재 Map image 정의에 맞추고, 실제 이미지 entrypoint와 빈 command를 통과시키면서
Compose-level `command`·`entrypoint` override 및 provider credential 차단은 그대로 유지한다.
회귀 테스트는 image entrypoint/empty command 수락과 우회값 거부를 고정한다. 사용자 정책에 따라
행/콘텐츠/건수 검증, 이전 revision 복구, 기존 DB restore는 수행하지 않으며, 필요 시 fresh
`300` schema에 source/ETL을 처음부터 재적재한다.

## 2026-08-26 — n150 `300` 재개에서 Map role-bootstrap 환경 계약 보완(PR #203)

PR #202 merge 후 trusted Manager를 설치하고 approved `ktdctl pinvi-pair rebuild-pinned --confirm`을
재개했다. Map paired candidate·receipt 검증과 새 application database 생성은 통과했지만, Map 정본
`database-credential-preflight.sh`가 요구하는 `KOR_TRAVEL_MAP_POSTGRES_PASSWORD`가 Manager의
role-bootstrap one-shot 환경에 전달되지 않아 bootstrap 단계에서 종료됐다. 이는 데이터나 `300`
스키마 무결성 문제가 아니라 Manager–Map source contract 누락이다.

PR #203은 Compose 원문과 frozen journal source hash를 바꾸지 않고, Manager의 role-bootstrap one-shot에
기존 배포 환경의 `KOR_TRAVEL_MAP_POSTGRES_PASSWORD`를 `--env`로 명시 전달하도록 보완한다. credential
값 자체나 행 데이터는 코드·문서에 기록하지 않았다. 집중 Compose contract 32개와 bootstrap command
회귀 테스트가 통과했으며, PR merge 후 trusted install을 갱신해 같은 durable journal과
고정 paired receipt로 재개한다. 사용자 승인 정책에 따라 일반 row 내용·건수·업무상 무결성 검증, 이전
revision 복구, 기존 DB restore는 수행하지 않는다. 필요 시 fresh `300` schema에 source/ETL을 처음부터
재적재한다.

## 2026-08-26 — Map Dagster static inspection entrypoint 차단

Manager PR #201 merge 후 trusted install을 갱신하고 approved `rebuild-pinned --confirm`을 다시 실행했다.
Map·PinVi candidate image build와 paired receipt 생성은 완료됐지만, DB mutation 전에 Map Dagster static
inspection이 `ktm-dagster-storage head`를 기본 production entrypoint로 실행하면서 sealed absolute
runtime command 오류로 중단됐다. 같은 image를 `--network none --entrypoint /usr/local/bin/ktm-dagster-storage`
와 `head`로 직접 실행하면 정적 head JSON을 반환하므로 image나 Dagster graph 자체의 실패가 아니라
Manager static launch invocation의 계약 불일치다.

PR #202는 Map Dagster에만 고정 absolute entrypoint를 전달하고 네트워크를 계속 차단한다. 실패한
candidate image·receipt는 production evidence로 재사용하지 않으며, PR merge 후 새 paired artifact를
만든 뒤에만 rebuild를 재개한다. 이번 실패에서도 fresh schema reset, 기존 DB/revision 복구, 행 데이터
검증은 수행하지 않았다. 추가 적대 리뷰에서 pre-journal receipt가 다음 실행에 `--verify`로 재사용될 수
있음을 찾아 PR #202 후속 수정으로 보완했다. durable journal이 없는 실행은 기존 API·paired receipt를
정확한 owner-only 경로에서 폐기한 뒤 sealed builder를 fresh build mode로 호출하고, journal이 있는
crash resume에서만 receipt `--verify`를 허용한다. resume 때도 현재 paired receipt·API receipt·Map
image/config/contract 증거를 journal candidate와 exact 대조해 split-brain을 차단한다.

## 2026-08-26 — n150 rebuild 전 source contract 차단을 사전 수정

Manager PR #200 merge 뒤 trusted Manager release와 Map `cc81081ff2e540a6ad9c428a296515e1d79bc316`
source를 n150에 준비하고 approved `rebuild-pinned --confirm`을 실행했다. Candidate image와
receipt 생성 및 정확한 PostGIS image 준비까지는 완료됐지만, DB mutation 전에 Map source environment
contract가 `KOR_TRAVEL_MAP_DAGSTER_PROFILE`의 중첩 fallback을 허용된 exact path로 인식하지 못해
fail-closed 중단됐다. 기존 source의 `api` env_file만 실제 계약인데 Manager가 제거된 Dagster
`.env` env_file까지 요구하는 두 번째 stale expectation도 같은 사전 검증에서 확인됐다.

새 수정은 네 Dagster 서비스의 fallback 경로만 허용하고, 다른 경로의 protected placeholder는 계속
거부한다. env_file 계약은 현재 Map source의 API 파일 하나만 고정한다. 회귀 테스트 31개와 실제 Map
exact source의 contract v4 판정이 통과했으며, 이 과정에서 DB·행 데이터·기존 revision은 변경하지 않았다.
수정 PR merge 후에만 n150 trusted install과 새 paired rebuild를 재개한다.

## 2026-08-26 — Map #1066 merge와 application `300` v5 pin 확정

Map PR #1066의 exact head `cc81081ff2e540a6ad9c428a296515e1d79bc316`가 전체 CI green 후
merge commit `14d18230e5a9ff21caf26d6abe37aed1e4944685`로 merge됐다. 이 source head는 n150에서 paired
builder가 내부 candidate builder를 직접 실행할 때 필요한 Git mode `100755`를 보존한다.
후속 단위 테스트는 NTFS/WSL의 filesystem 실행 비트뿐 아니라 `git ls-tree HEAD`의 canonical
mode도 `100755`인지 확인한다. 두 전문 적대 리뷰는 P0/P1/P2=0 GO이며, schema/bootstrap,
fresh-root/finalize, receipt/recovery 코드는 변경되지 않았다.

이 exact Map revision과 PinVi `27fe2043b7b8e747fbb42d91e461ea462f930bb7`를 조합한 v5
canonical pinset digest는 `14a9a512836a48489146dc2bb0a04de309cf451b274b934d79805d171f83a193`다.
Manager release authority와 application `300` source를 이 exact head로 회전했고 관련 회귀 테스트
28개가 통과했다. Manager PR #200 merge 뒤에만 새 paired image·receipt를 만들고 n150 approved
rebuild를 재개한다.

사용자 승인 정책에 따라 일반 application row의 내용·건수·업무상 무결성은 계속 release gate에서
제외한다. 필요한 데이터는 fresh `300` schema에 source/ETL로 처음부터 재적재하며, 이전 revision
또는 기존 DB 복구는 수행하지 않는다.

## 2026-08-25 — Map `300` role bootstrap helper의 C6c 오탐 결선 보완 (PR #198 merge)

n150의 trusted `rebuild-pinned --confirm`에서 Map 정본 `dd2ee61f…`의 두 read-only bind source는
존재·경로 검사를 통과했지만, `scripts/database-credential-preflight.sh`가 선언하는
`KOR_TRAVEL_MAP_*` 식별자를 C6c secret text 누출로 잘못 분류했다. 이 파일은 bootstrap과 함께
Map release가 소유하는 canonical helper이며, 실제 credential 값이 아니라 검증할 환경변수 이름을
참조한다.

Manager는 이제 정확한 Map role-bootstrap 서비스의 두 canonical target에만 같은 source-owned
예외를 적용한다. 경로·read-only·정본 source provenance 검증은 유지하고, helper 안의 protected
식별자 이름만 허용하며 실제 credential 값과 다른 operator bind의 protected text 검사는 그대로
fail-closed한다. 실제 credential 값이나 Manager `.env`를 코드·문서에 기록하지 않는다.

- Map/Manager Compose contract 회귀: `29 passed`
- Manager 전체 backend 테스트: `726 passed, 3 skipped`
- 변경 파일 Ruff·`git diff --check`: 통과

두 전문 적대 리뷰는 P0/P1/P2 모두 0인 GO였고, PR #198은 merge commit
`19409e3fad4bbe37a89edec99fee6f67de51fcff`로 `main`에 반영됐다. 이제 n150 trusted install을
이 Manager 정본으로 갱신한 뒤 승인된 rebuild를 재개한다.

## 2026-08-25 — Map `dd2ee61f` 통합 CI green과 `300` release pin 회전

Map PR #1064의 exact head `dd2ee61fdb1d0cedb0d7cb3526c804a3dfc5404e`가 Python
3.11/3.12/3.13, lint, type/build, OpenAPI drift, fixture replay와 PostGIS 통합 CI를 모두
통과했다. Manager의 application-300 source와 v5 canonical release pinset을 이 exact
커밋으로 회전했다. PinVi source `27fe2043b7b8e747fbb42d91e461ea462f930bb7`는 유지하고,
새 canonical compact pinset digest는
`49548a610cbfa3a0d2242ef6e9a8cbd5664e61dec92391b8a476b02951b65c62`다.

이전 c95 pin의 image·receipt·journal은 새 release evidence로 재사용하지 않는다. `300` 승격
후 일반 application row 데이터의 내용·건수·업무상 무결성 검증은 release gate가 아니며, 필요할
경우 fresh schema에 source/ETL로 처음부터 재적재한다. 이전 revision 또는 기존 DB 복구는 수행하지
않는다.

---

## 2026-08-25 — Map c95fbb01 exact pair 재고정과 application `300` 데이터 gate 퇴역

Map PR #1064의 통합 fixture·teardown 보정이 반영된 exact commit
`c95fbb019ebaa618ead2be86d4023d5d918fce66`으로 Manager의 application-300 source authority를
회전했다. PinVi source는 유지하고 v5 canonical pinset은
`e7eccb61e7d0c0faa5920bd497d812f2847ea778e972da1773cfb55948c20b2c`로 다시 계산했다. 이전
Map/pinset의 image·receipt·journal은 새 release evidence로 재사용하지 않는다.

사용자 승인 정책에 따라 일반 application row의 내용·건수·업무상 데이터 무결성은 application `300`
release gate에서 제외했다. 필요한 데이터는 fresh schema에 source/ETL로 처음부터 재적재할 수 있으며,
이전 revision이나 기존 DB 복구는 수행하지 않는다. receipt는 schema/bootstrap·provenance 범위만
증명한다.

---

## 2026-08-25 — Map LO residue gate와 v5 release pin 회전

Map PR #1064의 최신 exact commit `7d44b98b3d0671329e9a6711187091d95cf960cf`가 fresh-root
pre-state에서 `pg_largeobject_metadata`의 database-wide owner/ACL 잔류를 거부하고, canonical
application catalog·privileged residue digest에도 해당 행을 포함한다. 실제 disposable PostgreSQL에서
large object를 만들고 PUBLIC SELECT ACL을 부여한 뒤 probe가 거부되는 negative regression을 통과했다.

Manager v5 source와 canonical pinset을 이 Map commit으로 회전했다.

- pinset: `a75c2f1a4ef569c65177061573ad4cf418798a2556dca02052dd87cae54b6936`
- 이전 Map/pinset의 image·receipt·journal은 새 release evidence로 재사용하지 않음
- Map fresh integration: `2 passed`; Map root/catalog unit: `34 passed`; contract artifact: `18 passed`
- Manager renewal crash focus: `70 passed`

---

## 2026-08-25 — expired fence renewal의 file↔journal crash 수렴

두 전문 적대 리뷰에서 fresh root/finalize fence를 새 bytes로 교체한 직후 journal 기록 전에
프로세스가 중단되면 다음 재개가 영구 정지할 수 있는 P1을 확인했다. renewal expiry를 현재 시각에
의존하지 않는 결정론적 값으로 만들고, 재개 시 현재 fence가 old 또는 결정론적 renewed bytes인지
판정해 renewed bytes가 이미 durable하면 journal을 먼저 수렴시키도록 보강했다. 그 외 missing·unsafe·
unknown fence는 기존 journal plan으로 typed missing probe를 다시 수행하며, probe가 fence·operation·
journal을 strict하게 결박하지 못하면 root/finalize 재실행을 허용하지 않는다. finalize도 동일한
수렴 경계를 적용했다.

- crash-first renewal unit: `70 passed` (rebuild focus 포함)
- 변경 source Ruff 통과
- root/finalize one-shot recovery test에서 probe 후 재실행 금지 확인
- 두 `uv.lock`은 열람·수정·stage하지 않음

---

## 2026-08-25 — fresh-root receipt-missing proof 소비 및 committed fast-path 재검증

Map `a7c950c215c981333eb6a46f607235aa422e88f4`의 root `probe-missing` wire를 Manager strict
parser에 연결했다. root `recover`가 실패한 경우에도 typed `receipt-missing-exact-prestate`가
operation·fence·journal·DB identity·candidate/contract digest·exact pre-root schema를 모두
결박하지 않으면 fence 갱신과 root 재실행을 허용하지 않는다. 기존 bootstrap 상태 문자열 fallback은
제거했다.

committed resume에서도 두 PostgreSQL container의 실제 secret-file-only runtime config를 재검증하고,
정확한 seven one-shot writer 부재 및 project-global orphan bootstrap credential sweep을 다시 수행한
뒤에만 성공을 반환하도록 보강했다. release pinset은 Map source와 함께
`6a035e257aefc0cc20d1e37f9e08882c9335e196a1af9a223d85fb286a00ed50`으로 회전했다.

- Map/Manager root·finalize·release 회귀: `95 passed` (Manager focus set)
- 변경 source Ruff 통과
- 완료된 `T-VN-40` 잔여 항목은 active tasks에 없음
- 두 `uv.lock`은 열람·수정·stage하지 않음

---

## 2026-08-25 — v8 세 DB identity·crash residue 최종 hardening checkpoint

Map exact commit을 `a7c950c215c981333eb6a46f607235aa422e88f4`, release pinset을
`6a035e257aefc0cc20d1e37f9e08882c9335e196a1af9a223d85fb286a00ed50`로 회전했다. 이전
pinset의 image·receipt·journal은 새 release evidence로 재사용하지 않는다.

두 전문 적대 리뷰의 finding에 따라 다음 경계를 보강했다.

- finalize 응답 유실은 Map의 typed exact-prestate proof 뒤에만 fence를 갱신해 재실행
- committed resume에서 두 PostgreSQL container image와 Map application·Dagster metadata·PinVi
  세 DB identity를 journal과 실시간 재대조
- Dagster metadata LOGIN role의 privilege/membership 외 connection limit, password expiry,
  role/database-local setting 잔여까지 permit과 journal에 결박
- rebuild 중단 뒤 남은 PinVi bootstrap plaintext credential을 global lock·모든 one-shot 부재 조건에서
  owner-only strict scan 후 zeroize/unlink/fsync
- v6 candidate tag는 현 세대 exact image ID만 보존하고 stale tag를 제거하되 v5 content reference를
  먼저 확인
- committed resume에서도 external prerequisite를 candidate build 전에 확인하고 일곱 runtime과 두
  PostgreSQL image를 재검증
- Map Dagster migration receipt를 구 v2 부분 비교에서 v3 exact field set으로 올리고, journal의
  operation/head/permit, sealed candidate digest, metadata DB identity와 catalog digest 형식을 대조

Manager 전체 backend `721 passed, 3 skipped`, v3 receipt 직접 회귀를 포함한 rebuild `68 passed`,
Map C7/Dagster `305 passed`, 변경 파일 Ruff·strict mypy와 frontend type-check/build를 통과했다.
다음 단계는 두 리뷰어의 exact-commit 재검토 finding을 닫고 final gate를 다시 실행하는 것이다.
완료된 `T-VN-40` 잔여 항목은 active `tasks.md`에 없다.

## 2026-08-25 — application `300` paired candidate와 v6/v8 rebuild 결선

Map PR #1064의 exact commit `d0ced47128c2b175bcd22d7e44fa979512ccf203`을 Manager release
pin에 고정하고 canonical pinset을
`f95428ea5ee1f5583bada5a53ecb72cc75e7ed55560850e1032f5d3eeb9b6331`로 회전했다.
이전 Map commit으로 만든 로컬 API·Dagster image와 paired receipt는 새 pin의 release evidence로
재사용하지 않는다. exact `d0ced471…` sealed paired candidate는 이 Manager checkpoint 뒤 다시 build해
동일 candidate tree, PostgreSQL image와 application head `300`을 검증한다. 그 로컬 결과도 n150
production 증거가 아니므로 live 실행에서 다시 검증한다.

Manager PR #197은 Map API·Dagster를 독립 Compose build 대상에서 제거하고 paired receipt의 exact
image ID를 사용한다. Manager는 Map UI와 PinVi API·Web·Dagster 네 image만 build한다. generation manifest는
v6, pinset별 resume journal/tombstone은 v8로 올렸다. Map application fresh DB는 exact DB identity를
고정한 뒤 root/finalize 각각 operation plan→read-only fence→durable execution intent→result 순서로
진행하고, application final permit과 별도 Dagster metadata identity permit을 발행한다. 결과 없는
execution intent는 같은 operation ID의 append-only DB receipt를 먼저 recover하고, receipt 부재와 exact
pre-state가 함께 증명될 때만 안전하게 같은 operation을 재실행한다. 만료 fence도 operation ID를 보존한
채 이 조건에서만 갱신한다. Dagster storage는 journal transaction ID를 쓰는 intent+receipt v2로 수렴하며,
web·daemon은 `--no-deps`로 기동해 implicit 재실행을 막는다. final/committed resume은 일곱
running container의 실제 image ID를 journal generation과 다시 대조한다.

DB create/bootstrap response-loss 수렴, 외부 Geo·Concierge·RustFS read-only prerequisite,
`pinvi-db-init` writer 배제, Dagster LOGIN/NOINHERIT exact identity도 함께 고정했다. Manager backend
전체 결과는 `694 passed, 3 skipped`, 변경 파일 Ruff와 7개 변경 source strict mypy가 통과했다. Map의
OpenAPI/lint/frontend gate도 통과했고 Python 3개 CI matrix는 이 기록 시점에 진행 중이다. 코드 checkpoint
`da49ec7e858e4aa6e95457e664184e42688885e4`을 PR #197 원격 branch에 push했다.

사용자 결정에 따라 이전 Alembic revision·DB로 돌아가는 복구 계획은 없으며 backup/scratch restore를
release gate로 사용하지 않는다. 다음 단계는 DB crash/resume/identity와 Compose/provenance/security의
독립 전문 적대 리뷰 2건, Map merge 후 Manager rebase, n150 trusted install과 approved
`rebuild-pinned --confirm`, 공개 UI login/protected/logout 및 PinVi acceptance다.

## 2026-08-24 — T-VN-41C M01~M05 role-residue RC 재고정과 host rebuild lease

PostgreSQL role은 cluster 범위라 database를 새로 만들어도 M01~M05의 이미 알려진 role
membership이 남을 수 있다. Map base migration과 Manager의 pre-migration principal assertion은
그 정확한 10개 future role만 양방향으로 보류하고, source-owned M01/M05 phase가 이후
membership 상대방과 PostgreSQL 16 option까지 다시 exact 검증하도록 정렬했다. 미등록
`ktm_feature_*`/`ktm_curation_*` edge는 계속 fail-close한다.

Manager는 Map RC `b9818097`을 pinset `f946bdfa…`로 재고정했다. 기존 `f27c2763…`의
non-terminal journal은 immutable failure evidence로 보존하며, 서로 다른 pinset은 새 pinset별
journal과 transaction으로만 시작한다. F1D rehearsal의 일반 C6c lock이 실행 사용자 home을 쓸 수
있는 문제도 보완해, root-only rebuild는 `/run/lock/kor-travel-docker-manager/` 아래의 고정
host lease를 candidate source materialize 전부터 final commit까지 잡는다.

이 lease는 Manager launcher 사이의 직렬화 경계다. n150의 외부 Compose watcher는 같은 lease를
획득하도록 wrapper를 정렬하고, 실제 재구축 전에는 해당 project/container/process가 모두
정지·비활성인지 별도 확인한다. 세 standalone dump의 scratch restore는 F1D rollback이 아니라
사용자 승인 release evidence이므로, 각각의 manifest에 기록된 dump SHA-256·TOC·schema head
대조가 성공한 뒤에만
새 pinset rebuild를 시작한다.

---

## 2026-08-23 — T-VN-41C pinned candidate 빌드 직렬화 및 n150 지연 원인 확인

두 전문 리뷰어(API·DB/운영)가 n150 승인 재빌드의 지연·실패를 교차 점검했다. 첫 번째
실패는 BuildKit이 일괄 `compose build`에서 여러 frontend session을 동시에 열어
`only one connection allowed`와 context deadline을 반복한 것이었고, 별도 tvnm05 자동
빌드가 같은 Docker daemon을 계속 점유해 재현성을 악화시켰다. n150은 14 GiB 메모리 중
4 GiB swap이 모두 사용되고 load가 20대까지 올라가 apt/npm 단계와 Docker API 조회도
대기했다.

Manager PR #197에는 candidate 7개 runtime service를 한 번의 multi-target bake로 보내지
않고 각 service별 frozen Compose build를 순서대로 실행하는 최소 수정(e33b19c)을 반영했다.
회귀 테스트 35건과 변경 파일 Ruff를 통과시키고 원격에 push했으며, trusted 설치본도 같은
commit으로 교체했다. 순차화 뒤 Map·PinVi 후보 image build와 static head 직전까지 진행되는
것을 확인했다.

다만 tvnm05 감시 작업이 `DOCKER_BUILDKIT=0`/BuildKit 빌드를 종료 직후 재기동해 Docker
daemon의 BuildKit session healthcheck가 계속 `only one connection allowed` 상태로 남았다.
승인된 최신 시도들은 이 one-shot 단계에서 fail-close했고, 새 state journal과 DB reset은
생성되지 않았다. Docker daemon 재시작은 다른 서비스 중단을 수반하므로 별도 승인 전에는
실행하지 않는다. 이전 승인 시도의 `databases_recreated` journal과 fresh Map DB는 실패
증적으로 보존한다.

---

## 2026-08-23 — T-VN-41C/M01~M05 pinned rebuild migration boundary 보완

고정 RC 재빌드에서 DB를 재생성하고 candidate image를 attestation한 뒤 Map API
기동이 `ktm_manual_feature_procedure_owner` 부재로 반복 대기하는 원인을 확인했다.
Map source의 0226~0236 migration은 단일 `alembic upgrade head`로 적용할 수 없고,
0225 boundary·M01 role phase·0233 boundary·M05 pre role·0235 migration·M05 repair
role의 순서를 요구한다.

Manager pinned rebuild가 승인된 transaction 안에서 Map candidate image의 exact
boundary script와 source-owned role bootstrap을 순서대로 실행하도록 고정했다.
boundary script는 장기 Map API service를 재사용하지 않고 migrator DSN만 가진
전용 bootstrap service에서 실행해 API/ops/curation/Geo/object-store credential이
one-shot으로 상속되지 않게 했다.
기존 pre-migration principal assertion은 legacy role bootstrap 직후로 유지해
runtime ACL 변화 뒤의 false failure를 막았고, armed/uninitialized resume에서도
같은 순서를 재현한다. 회귀 테스트는 모든 boundary/phase command와 순서를 고정한다.

---

## 2026-08-23 — T-VN-M01 candidate source 경로 override 전달 보완

고정 RC pinned rebuild의 DB·컨테이너 변경 전 candidate preflight가 staged Map
worktree 대신 canonical source-consent 경로를 검사하던 경로를 수정했다. Compose
resolved 값에는 candidate override가 반영되어도 transaction의 원본 환경 snapshot은
그대로 보존되므로, source environment contract gate에 frozen 환경과 candidate
override를 병합해 전달하도록 고정했다. 그 결과 exact Map revision의
`docker-compose.yml`·env-file 계약을 실제 staged worktree에서 검사한다.

회귀 테스트로 source contract 호출의 staged 경로와 rebuild candidate override를
고정했으며, backend 전체는 `598 passed, 3 skipped`, 변경 Ruff와 diff 검사를 통과했다.
저장소 전체 strict mypy는 기존 설치 패키지 untyped import 등 baseline 진단으로
실패했으며 이번 변경의 새 진단은 확인되지 않았다.

---

## 2026-08-23 — T-VN-M01 적대적 리뷰 P1 보완

두 전문 리뷰어가 공통으로 지적한 reset 경계의 자격증명 충돌을 보완했다. manual Feature
생성 원문·digest가 Map API service/ops/cursor/metrics/Geo·UI 인증·curation raw 또는
curation/cache-target digest와 재사용되면 `recreate_empty_databases()` 전에 fail-close한다.
비 ASCII 입력도 예외 없이 안전하게 비교하도록 바이트 기반 constant-time 비교를 사용하고,
오류에는 자격증명 값이나 digest를 기록하지 않는다.

API canonical Compose에 `KOR_TRAVEL_MAP_API_ADMIN_MANUAL_FEATURE_CREATE_ENABLED`를
명시적으로 연결하고 기본값은 `false`로 고정했다. 마지막 7개 runtime service readiness 뒤에는
실제 Docker inspect 결과를 다시 수집해 runtime secret isolation과 Map UI 인증 배선을
검증한 뒤에만 `contract_verified`로 진행한다. redaction 대상에도 manual Feature 원문·digest를
추가했다. candidate build gate가 Map source environment contract를 실제 호출하도록 연결하고,
manual flag의 exact `:-false` wiring을 결박했으며,
config loader 단독 호출에서도 `true|false` 외 값을 거부한다. committed journal fast path도
readiness·DB head 확인 뒤 Docker inspect 기반 runtime secret/UI auth 검증을 수행한다. 회귀 검증은
충돌 사전 차단 합성과 invalid flag를 포함해 전체 backend `598 passed, 3 skipped`, 변경 파일
Ruff, diff 검사를 통과했다. 저장소 전체 strict mypy에는 기존 baseline 진단이 남아 있어 별도
변경으로 섞지 않았다.

---

## 2026-08-23 — T-VN-M01 manual Feature credential 배선 정합성 보완

고정 RC의 승인된 F1D 재빌드에서 Map API가 `KOR_TRAVEL_MAP_API_ADMIN_FEATURE_CREATE_TOKEN_SHA256`
미설정으로 fail-close한 원인을 확인했다. Manager canonical Compose와 C6C candidate allowlist가
Map image의 M01 계약을 주입하지 않고 있었으므로, 원문 credential은 Map UI server runtime에만,
동일 원문에서 검증한 SHA-256 digest는 Map API에만 전달하도록 배선을 추가했다. production/rehearsal
환경에서는 부분 설정·공백·짧은 원문·형식 불일치·원문과 digest 불일치를 모두 mutation 전에 거부한다.

API/UI/source Compose 계약, resolved/runtime secret isolation, `.env.example`, 운영 문서를 함께
갱신하고 원문·digest가 다른 서비스로 새지 않는 회귀 테스트를 추가했다. 실제 승인된 값은
gitignore된 n150 secret env에만 남기며 receipt·로그·커밋에는 기록하지 않는다. backend 전체는
tmpfs에서 `596 passed, 3 skipped`이고 변경 파일 Ruff와 diff 검사를 통과했다. 전체 strict mypy는
저장소 기존 baseline 진단(설치된 패키지의 untyped import 등)으로 실패했으며 이번 변경의 새
진단은 별도 확인한다.

이 변경을 draft PR로 원격에 올리고 두 전문 리뷰어의 적대적 검토를 거친 뒤 trusted Manager
release를 갱신한다. 이후 새 state root로 승인된 `ktdctl pinvi-pair rebuild-pinned --confirm`을
재실행하고 T-VN-41C 및 M01~M05 live acceptance를 이어간다.

---

## 2026-08-23 — Map M01~M05 curation role graph 검증 결선

PR #193을 trusted release에 설치한 뒤 승인된 F1D retry에서 Map Dagster DB init과
기본 role bootstrap은 통과했지만, principal assertion이 `ktm_curation_*` membership을
예상하지 않아 fail-close했다. 현재 Map candidate head(0226~0236)는 curation command/
audit/executor 그룹과 API/Dagster executor membership을 정본 bootstrap script로
만드므로, Manager의 exact catalog assertion에도 이 네 그룹과 네 membership을 추가한다.

이 변경은 M01~M05 활성화 acceptance를 막던 Manager 검증 drift만 수정하며, 기존
NOLOGIN/NOINHERIT 및 runtime ACL fail-close 경계는 유지한다. 해당 수정 PR의 focused
database runtime 테스트와 두 전문 리뷰 후 trusted install을 갱신하고 같은 v7 retry
journal에서 Map principal assertion부터 재개한다.

## 2026-08-23 — Map Dagster bootstrap DSN 전달 수정

F1D v7 재개에서 Map Dagster DB init one-shot이 health가 정상인 전용 Map PostgreSQL에
접속하지 못하고 Unix socket 기본값으로 떨어지는 원인을 확인했다. Compose command가
`psql "$DSN" --dbname postgres` 형태여서 URI를 positional DBNAME으로 전달한 뒤
`--dbname postgres`가 이를 덮어쓰고 있었으며, `psql --dbname "$DSN"`으로 바꿔
conninfo를 명시했다. source Compose contract에 이 순서와 잘못된 형태의 부재를 고정했다.

이 수정은 dollar transport PR 머지 후 trusted release에 반영하기 위한 별도 PR이다.
현재 v7 journal의 DB는 재생성된 비종료 상태이며, 새 release 설치와 focused one-shot
검증이 끝난 뒤에만 승인된 rebuild를 다시 실행한다.

## 2026-08-23 — frozen Compose 재입력의 `$` 환경값 보존

F1D pinned runtime rebuild가 `docker compose config`로 해석한 문서를 다시 `-f -`로
전달할 때, 이미 해석된 환경값을 Compose가 한 번 더 보간하던 결함을 수정했다. 비밀번호에
`$`가 포함된 DSN이 잘려 bootstrap one-shot이 기본 Unix socket으로 접속하던 경로가
발견됐으며, materialized Compose의 `environment` 값에만 재보간 방지 이스케이프를 적용했다.
`command`/`entrypoint`의 `$$VAR`는 컨테이너 셸 계약을 보존하기 위해 변경하지 않는다.

환경값의 `$` 보존·원본 불변·command 비변경 회귀 테스트를 추가했다. 이 수정은 현재
실패한 v7 journal을 같은 transaction으로 재개하기 위한 선행 PR이며, n150 DB 재생성이나
runtime 재실행은 PR 검증·trusted release 설치 뒤에만 수행한다.

## 2026-08-22 — F1D v5 Map·PinVi release pinset 재고정

Map #1056 병합 뒤 현재 Map main `e420c89eb0f10776f7fb96e59ef3b409974d0d54`와
PinVi #465 병합 `27fe2043b7b8e747fbb42d91e461ea462f930bb7`을 v5 release authority에
반영했다. canonical pinset digest는
`de5206dcc198c76874dcf51ef7152cd6d8bff0cbf5766463709e9d69a2d9b7a5`이며, source
revision·canonical JSON·digest를 단위 계약으로 고정했다. 이 변경은 release metadata만
갱신하고 파괴적 rebuild나 production runtime mutation은 실행하지 않는다.

검증은 backend 전체 `577 passed, 3 skipped`, 변경 파일 Ruff, 변경 source strict mypy,
보안 패턴·whitespace 감사를 통과했다. 저장소 기존 Ruff import 4건과 전체 strict mypy
baseline 진단은 이번 변경과 무관해 유지한다.

## 2026-08-22 — C6c Map membership triple·fixture 순서 검증 rebase

열린 PR #170을 최신 `origin/main`에 재배치했다. C6c smoke의 Map dataset identity를
`provider_dataset_id × sync_scope × operation_key`와 canonical detail URL로 검증하고,
catalog-only 행의 null operation만 허용한다. 실행 membership와 표시 scope·operation이
어긋나거나 fixture lifecycle timestamp가 역전되면 mutation 전에 fail-close한다.

이 PR은 Map #170의 후속이며 PinVi projection과 같은 upstream triple 계약을 사용한다.
기존 main의 rehearsal 환경 테스트와 문서 원장은 보존했으며, PR 전 focused pytest·Ruff·strict
mypy와 적대적 전문 리뷰를 다시 수행한다.

---

## 2026-08-21 — 전체 문서·코드 정합성 감사

현재 Markdown 문서와 Compose·registry·FastAPI router·CLI·frontend 토큰을 대조하고, 서로 다른
운영 사실을 안내하던 부분을 현재 코드 기준으로 정리했다. 적대적 전문 리뷰어 2명이 별도로
전체 문서의 포트·DB·보안·배포·API 계약을 검토했으며, 두 리뷰의 지적을 모두 반영했다.

- PostgreSQL 전용 instance 포트를 `12500`/`12600`/`12700`/`12800`으로 통일하고 폐지된
  `5432`·`12703` 현재값, Geo 하나에 여러 프로젝트 DB가 있다는 설명, Geo 복구 스크립트의
  다른 프로젝트 DB 생성 주장을 제거했다.
- `/api/v1/auth/*`, `/api/v1/admin/*`, container reset, backup 목록, WebSocket 경로를
  실제 router prefix에 맞춰 문서화했다. 미인증 운영 `curl`을 401 경계 확인으로 바꾸고,
  공개 브라우저의 로그인→대시보드→로그아웃→LoginScreen 전환과 WebSocket 재연결 부재를
  필수 검증으로 명시했다.
- 전체 디렉터리를 복사하던 운영 `rsync` 예시를 소스 디렉터리만 동기화하도록 줄였고,
  trusted offline installer와 root-owned wheelhouse 경계를 문서화했다. 비밀번호를
  `docker exec -e`로 전달하던 수동 backup 예시는 passwordless Unix socket CLI로 대체했다.
- BMW M/Pure Black 문서를 현재 Hallmark Cobalt Workbench와 `frontend/tokens.css` 정본으로
  갱신하고 존재하지 않는 경로·깨진 `file:///` 링크를 제거했다. `.env.example`의 credential은
  placeholder로 바꾸고 테스트 fixture가 운영 관리자 비밀번호를 재사용하지 않게 분리했다.

- 검증: backend 전체 테스트 `567 passed, 3 skipped`, frontend `npm run type-check`와
  production `npm run build`, registry/포트 YAML 계약 검사를 통과했다. Markdown 내부
  로컬 링크 25개도 모두 확인했다.
- 저장소 기존 품질 부채는 범위를 넓혀 섞지 않았다. 현재 작업트리에서 Ruff 기존 진단
  68건과 `MYPYPATH=src mypy --strict --explicit-package-bases` 기존 진단 75건이 남아
  있으며, 이번 변경으로 새로 발생한 진단은 확인하지 못했다.
- 운영 호스트의 실제 관리자 비밀번호 회전·세션 무효화는 외부 운영 작업이므로 실행하지
  않았다. 추적 문서와 테스트에서는 해당 값을 제거하고 비운영 fixture로 대체했으며,
  운영 비밀 저장소에서 회전이 필요하다는 경계를 유지했다.

PR #186([전체 문서와 현재 코드 계약 정합성 감사](https://github.com/digitie/kor-travel-docker-manager/pull/186))로
제출했고 2026-08-21에 squash 머지했다. merge commit은
`eefca43717e9fe9806bdefc794c45a3581945e31`이다.

---

## 2026-08-20 — H49 standalone 백업 운영 증거와 public live E2E

H49의 n150 운영 AC 중 standalone 생성·검증·목록·GC·주기 실행을 실제로 확인했다.
Map application은 geo 앱 레벨 백업이 정본이고 Map application/Dagster 주기화는
kor-travel-map #148 정책에 맡기므로 별도 cron을 설치하지 않았다.

- Manager API가 root로 실행될 때 `Path.home()`이 `/root`가 되는 경로 drift를 막기 위해
  prod backend에 `KTDM_BACKUP_ROOT`를 operator CLI와 같은 절대 백업 root로 설정했다.
  기존 plain-text legacy baseline triplet은 새 JSON manifest parser가 읽는 디렉터리와
  섞지 않고 `${KTDM_BACKUP_ROOT}/legacy/<role>/`로 보존 이동했으며, active directory와 산출물은
  각각 0700/0600 권한을 확인했다.
- `geo_dagster`, `concierge`, `pinvi` 각각에 대해 두 번의 `ktdctl db-backup create`와
  `list`, `gc --keep 1`을 실행했다. 오래된 dump가 삭제되고 최신 dump·`.sha256`·`.manifest`가
  남았으며, role directory에서 `sha256sum -c`가 모두 성공했다. wrapper도 세 role에
  대해 수동 1회 실행해 create→GC→완료 경로를 확인했다.
- n150 host cron에 `CRON_TZ=UTC`와 함께 다음 UTC 시각의 wrapper를 설치했다:
  `geo_dagster` 03:15(keep 4),
  `concierge` 03:30(keep 7), `pinvi` 03:55(keep 7). cron daemon active와 중복 없는
  crontab을 확인했으며 geo application·Map roles는 임의로 활성화하지 않았다.
- public Manager UI에서 Playwright Chromium live E2E를 실행했다. sanitized transcript는
  `login=200 → GET /api/v1/backups=200(roles 3) → logout=200 → 보호 API=401`이며,
  인증값·세션값·공개 origin은 저장하지 않았다. backend root와 frontend static asset을
  함께 배포해야 이 결과가 재현된다는 점도 확인했다.
- off-box 자동화는 아직 완료 처리하지 않았다. 사용 가능한 환경에서 외부 목적지·자격증명·
  전송 도구 결선이 확인되지 않아 same-host 경로를 off-box로 간주하지 않았다. 따라서
  H49는 최신 산출물과 주기 실행 증거는 충족했지만 off-box AC와 restore CLI는 미결이다.

---

## 2026-08-19 — `pinvi-pair capture` 3차 확인 리뷰 blocking 2건 (ADR-38 개정 3)

3차 확인 리뷰가 남긴 것은 코드 결함이 아니라 **문서가 위험한 명령을 지시한다**와
**문서가 성립하지 않는 성질을 약속한다** 둘이었다.

- **B-1(운영 위험) — n150 설치본의 `capture`는 아직 파괴형이다.** 읽기 전용 실측으로
  확인했다: 설치본 revision은 `4191582779be47e9605a324ea27adbb99b438439`,
  `pinvi-pair --help`는 `{install-pinned-sources,bootstrap-pinned-drift,deploy,capture,rollback}`,
  `capture --help`는 `[--build] [--wait-timeout] [--verified-compatible] [--json]`이고
  설치 트리에 `services/c6c_pair_capture.py`가 **없다**. 즉 이 브랜치 문서가 "정본 호출"로
  공표한 문자열을 오늘 실행하면 **Map 넷 + PinVi API가 내려간다.**
  - ADR-38 최상단과 `docs/docker-management.md` §7.5 최상단에 경고 블록을 넣었다.
  - §7.5.1에 **읽기 전용 확인 절차**를 명령 형태로 넣었다. 핵심은 "capture를 실행해서
    확인하지 마라"다 — 확인은 `--help` 두 번으로 끝난다.
  - 코드에 자기 식별을 넣었다. `CAPTURE_CONTRACT = "pair-capture-v1"`이
    `capture --help` 설명, 성공 stdout의 **첫 줄**, `--json` receipt의 `capture_contract`
    **세 곳**에 같은 값으로 나온다. 옛 구현에는 이 문자열이 어디에도 없으므로 `--help`
    한 번으로 "설치된 것이 관측기인가"를 실행 없이 판정할 수 있다.
  - `scripts/install-ktdm-trusted-release`를 정독하고 §7.5.9에 설치 절차·전제조건·n150
    현재 상태를 적었다. 확인한 것: 정본 명령은 하나
    (`sudo -n /usr/bin/bash <SOURCE_ROOT>/scripts/install-ktdm-trusted-release <SOURCE_ROOT>`),
    `/home/digitie/kor-travel-docker-manager`에는 `.git`이 **없고**(배포 트리) 후보 checkout은
    `/home/digitie/f1d-v5-rehearsal/manager`, wheelhouse는 `*.whl` 25개로 이미 충분하며
    (이 브랜치는 새 런타임 의존이 없다), installer가 capture와 **같은 global mutation
    lock**을 잡고, Manager systemd unit이 없어 재기동이 필요 없으며, commit 뒤에는
    `.rollback` 트리가 삭제되어 자동 되돌리기 경로가 **없다**. **실행하지 않았다.**
- **B-2(멱등·attestation) — "재capture는 byte-멱등"이 §2.3 게이트를 오해시킨다.**
  runner는 `manifest_sha256`·`active.map_source_revision`·`active.pinvi_source_revision`
  (+`contract_generation`)을 **한 `if`에서 함께** attestation과 대조한다(443-448행).
  기록된 active(map `c8ed6164…`/pinvi `6a035695…`)와 실행 중 다섯(Map `817cfeae…`/PinVi
  `5cad141a…`)이 이미 다르므로 **첫 실전 capture는 정의상 멱등이 아니고 attestation
  재생성이 필수**다. 멱등 주장을 "identity가 같을 때만"으로 좁히고 세 필드를 명시했으며,
  receipt에 `recorded_at_preserved`·`attestation_action`을 더해 `false`일 때 비-JSON
  stdout에도 "§2.3 attestation을 다시 만들라"는 한 줄이 나오게 했다.

**함께 정리한 followup 셋.**

- **runner 행 번호 포인터 정정.** 실질 주장은 전부 참이고 포인터만 낡아 있었다.
  manifest shape 428-432 → **436**(+`_validate_pair` 439-440), sha256 대조 436 →
  **443-448**(sha 444), health 술어 501-508 → **508-518**, `_read_secure_file`
  112-146/112-164 → **111-162**, `_compose_container` 277-302 → **285-310**,
  `_validate_pair` 305-316/305-341 → **313-325/313-347**, `_exact_dict` 65-66 → **68-69**,
  manifest secure read `:623` → **635**. 재발 방지로 폐기된 인용 문자열을 코드·테스트·두
  문서에서 스캔하는 테스트와, `KTDM_C7_RUNNER_MODULE`이 주어지면 각 행이 실제로 무엇인지
  대조하는 테스트를 넣었다(실제 Map runner로 12개 anchor 전부 통과 확인).
- **비-production 환경의 R1-2 자기 충돌**을 ADR-38 §미결 끝에 따름정리로 적었다.
  rehearsal 모양 env에서는 `c6c_state_paths` 유도값과 `pinned_runtime_state_root`가 같은
  디렉터리라 capture가 자기 기본값을 배제 규칙으로 거부한다. 설치본은 production 분기라
  blocker가 아니어서 고치지 않고 선택지 셋만 남겼다.
- **"다섯 service가 running·healthy"의 정확한 뜻.** `State.Health`가 없는 컨테이너
  (healthcheck 미선언)는 health 항목을 **통과로 본다** — runner 508-518행과 의도적으로
  같은 술어다. 오늘 `kor-travel-map-dagster-daemon-latest`가 그 경우이며, 그 사실을 §7.5의
  같은 자리와 `_assert_container_is_healthy` docstring에 적었다.

**되돌리면 red가 되는지 실증**: 16개 mutation을 하나씩 되돌려 전부 red를 확인했다 —
stdout 첫 줄/receipt의 `capture_contract` 제거, `--help` 자기 식별 제거,
`CAPTURE_CONTRACT` 값 drift(문서 두 곳이 red), `recorded_at_preserved` 상수화,
`attestation_action` 제거, evidence 두 줄 제거, `ATTESTATION_BOUND_FIELDS`에서 필드 누락,
낡은 행 번호 복원(코드·테스트·문서 각각), `pinvi-pair`에 legacy `deploy` 재노출,
그리고 `KTDM_C7_RUNNER_MODULE`을 켠 상태에서 anchor 행 번호 drift 2건.

**검증**: `pytest -q` 567 passed / 3 skipped(직전 555 passed / 1 skipped — 새 skip 2건은
`KTDM_C7_RUNNER_MODULE` 게이트), `ruff check` 68=68, `mypy --strict -p
kor_travel_docker_manager` 76=76. n150은 **읽기만** 했고 prod mutation은 없다.

---

## 2026-08-19 — `pinvi-pair capture` 2차 적대 리뷰 7건 수정 (ADR-38 개정 2)

리뷰가 n150 실측으로 **1차 개정의 전제 자체를 뒤집었다.** 가장 큰 것은 ADR-38 §근거 1(A)의
사실오류다. n150에는 manager `.env`가 둘인데
(`/home/digitie/kor-travel-docker-manager/.env` = `rehearsal`/`rebuildable`,
`/opt/kor-travel-docker-manager/.env` = `production`), 실제 설치본
`/opt/kor-travel-docker-manager/backend/.venv/bin/ktdctl`의 shim이
`KOR_TRAVEL_DOCKER_MANAGER_PROJECT_ROOT=/opt/kor-travel-docker-manager`를 하드코딩하므로
`get_env_path()`가 읽는 것은 **production 쪽**이다. 그러면 `c6c_state_paths` 유도값은
`/var/lib/kor-travel-docker-manager/kor-travel-docker-manager/compatible-pair-v4.json`이고
**그 파일은 root:root 0600으로 이미 존재한다**. 내가 "네 번째 아티팩트 위치가 생긴다"며
기각했던 재사용이 실은 정답이었다.

- **B-2 기본 경로를 `c6c_state_paths`에서 유도**한다. 해결 순서는 `--manifest-path` →
  `E2E_C7_COMPATIBLE_PAIR_MANIFEST` → 유도값이고, 그래서 manifest가 "없어서" 거부되는
  경로가 사라졌다. 세 번째 state root 규칙을 만들지 않는다는 원래 목표가 이제 정직하게
  달성된다.
- **B-4 `KTDM_C6C_COMPATIBLE_PAIR_MANIFEST` fallback 제거.** 그 키를 production `.env`에
  넣으면 `c6c_state_paths`가 `"production C6c manifest and global lock paths are fixed"`로
  raise하고, 같은 함수가 host-global lock 경로도 정하므로 capture만이 아니라
  `c6c_deployment_lock_from_environment()`를 잡는 **모든 Manager mutation**이 죽는다.
  문서에 "이 키를 production `.env`에 넣지 마라"를 근거와 함께 박았다.
- **B-1 basename 하드락 제거.** runner(`run-c7-prod-live-e2e.sh` 607행)는 절대경로만
  요구하고 파일명 제약이 없는데, 오늘 C7 lane 스크립트는
  `E2E_C7_COMPATIBLE_PAIR_MANIFEST=/etc/kor-travel-map/c7-compatible-pair-v4.json`을 쓴다.
  manager가 runner에 없는 제약을 만들면 그 파일을 못 쓴다. 유지한 것은 절대·정규 경로,
  symlink 아님, ancestor root:root 비-group/other-writable, 기존 파일이면 v4 loader 통과.
- **B-3 런북 호출을 절대경로로 정정.** `sudo -n ktdctl …`은 오늘 `command not found`다 —
  sudo `secure_path`에 venv bin이 없고 `/usr/local/bin/ktdctl` symlink도 없다. 정본은
  `sudo -n /opt/kor-travel-docker-manager/backend/.venv/bin/ktdctl …`이며, symlink를 두는
  선택지는 프로비저닝 항목으로 남겼다(**실행하지 않았다**).
- **B1/F-2 typed refusal 구멍.** `values = effective_environment(get_env_path())`가 try
  밖이라 `DeploymentContractError`가 raw traceback으로 나가고 fence 문구도 안 붙었다.
  `_frozen_environment()`로 감쌌고, 지금까지 **어떤 테스트도 밟지 않던**
  `environment=None` 경로를 실제로 타는 테스트를 넣었다.
- **B-5 증거가 실제 호출형태에서 사라졌다.** 런북은 `--json` 없이 부르는데
  `previous_*`·`rollback_images_present`·`side_effects`·`input_sources`가 `--json`에만
  있었다. 비-JSON stdout 블록에도 낸다 — 특히 `rollback_images_present=false`("기록한
  rollback pair를 복원할 수 없다")가 사라지면 안 된다.
- **F-1 byte 멱등.** `recorded_at`이 매번 `now()`라 동일 runtime 재capture도 sha256이
  바뀌었다. runner가 `manifest_sha256 == attestation[...]`를 강제하므로 §2.3 attestation
  이후 재capture하면 게이트가 깨진다. 관측 identity(9필드 중 `recorded_at` 제외)가 기존
  `active`와 완전히 같을 때만 기존 시각을 보존해 byte-멱등으로 만들었고, 달라졌을 때만
  새 시각을 찍는다. "달라지면 attestation을 다시 만들어야 한다"를 문서에 함께 적었다.

**되돌리면 red가 되는지 실증**: 8개 mutation(위 7건 + F-1 역방향)을 하나씩 되돌려 전부
red를 확인했다. F-1은 양방향으로 확인했다 — 보존을 없애면
`test_recapturing_an_unchanged_runtime_is_byte_identical`이, 조건 없이 보존하면
`test_a_changed_runtime_stamps_a_new_recorded_at`이 red다.

**n150 실측(읽기 전용, mutation 없음)**: 두 manifest 파일은 byte-identical 복제본
(sha256 `f2051e42…`)이고 lane이 읽는 것은 `/etc` 쪽이다. 다섯 compose service는 모두
`com.docker.compose.project=kor-travel-docker-manager`로 running이며 Map 네 image가
revision `817cfeae…`, PinVi가 `5cad141a…`(`io.pinvi.build.environment=production`)다 —
즉 기록된 `active`(`c8ed6164…`/`6a035695…`)는 실행 중 runtime과 불일치한다. 그래도 오늘
capture를 그대로 돌리면 성공하지 않는다: `/home/digitie/kor-travel-map`은 git 저장소가
아니고 `/home/digitie/pinvi`는 clean이지만 `5cad141a…`를 갖고 있지 않아 revision 결박에서
`capture_refused_runtime`이 난다.

**검증**: `pytest -q` **555 passed / 1 skipped**(수정 전 이 브랜치 541/1, `origin/main`
baseline 411/0), `ruff check .` **68건으로 main baseline과 동일**하고 변경 파일 3개는
clean, `mypy --strict -p kor_travel_docker_manager` **76 errors in 11 files로 main
baseline과 동일**(`c6c_pair_capture.py`·`cli.py` 0건). n150은 읽기만 했고 prod mutation은
없다. push·PR 없음.

---

## 2026-08-19 — `pinvi-pair capture` 적대 리뷰 P1 9건 수정 (ADR-38 개정)

적대 리뷰 2명이 낸 P1 9건을 전부 고쳤다. 가장 큰 것은 **런북의 문자 그대로의 호출이
여전히 exit 2**였다는 것 — 이 명령의 유일한 존재 이유가 "런북을 고치지 않고 Manager에 그
명령을 존재하게 하는 것"이었으므로 목표 미달이었고, 실패 시점이 §2.1 step 4(alembic
upgrade) 뒤라 운영자가 막다른 길에 섰다.

- **R1-1/R2-1 세 입력의 frozen environment fallback**: manifest는
  `E2E_C7_COMPATIBLE_PAIR_MANIFEST`(없으면 `KTDM_C6C_COMPATIBLE_PAIR_MANIFEST`), checkout은
  `KTDM_C7_MAP_SOURCE_CHECKOUT`/`KTDM_C7_PINVI_SOURCE_CHECKOUT`. 정본 소유자가 C7 runner라
  runner가 쓰는 env 이름을 그대로 썼다. CLI flag는 override로 남겼고, receipt의
  `input_sources`가 값이 flag에서 왔는지 어느 env에서 왔는지 기록한다. 값이 없으면
  거부하되 메시지가 flag 이름과 env 이름을 모두 지목한다(막다른 길 금지).
  **(정정 2026-08-19)** `KTDM_C6C_COMPATIBLE_PAIR_MANIFEST` fallback은 지뢰였다 — 그 키를
  production `.env`에 넣으면 `c6c_state_paths`가 raise해 capture만이 아니라 global
  mutation lock을 잡는 모든 Manager mutation이 죽는다. 같은 날 2차 개정에서 제거하고,
  manifest 기본값을 `c6c_state_paths` 유도값으로 바꿨다.
- **R1-2 `rebuild-pinned` 배제**: manifest 경로가 `pinned_runtime_state_root(...)` 아래면
  precondition 거부다. 그 root에서 `rebuild-pinned`가 `f1d_legacy_artifact_paths()` —
  `compatible-pair-v4.json` 포함 — 를 퇴역시키므로 runner의 read target을 그 안에 두면
  rehearsal rebuild 한 번이 attestation 입력을 지운다. n150 rehearsal에서는 두 root가
  실제로 같은 디렉터리다. mode 게이트 없는 `pinned_runtime_state_root`를
  `pinned_runtime_generation.py`에서 추출해 규칙 정본을 하나로 유지했다.
- **R1-3 v5 pinned generation 대조**: `pinned-runtime-generation-v5.json`이 있으면 읽어
  다섯 image ID와 두 revision을 관측값과 맞추고 `pinned_generation_agrees`·
  `pinned_generation_divergent_roles`를 receipt와 stdout 한 줄에 노출한다. **거부하지
  않는다** — prod Map 재배포의 sanctioned 경로가 host compose 직접 실행이라 v5가 뒤처지는
  것이 정상 상태일 수 있다. `read_manifest`는 부모를 mkdir하므로 쓰지 않고 읽기 전용
  reader를 따로 뒀다.
- **R1-4 rollback 승격 회귀**: seed가 `rollback == active`라 승격 로직을 통째로 지워도
  81/81이 green이었다. seed를 `manifest_with_active_pair(initial_pair_manifest(older),
  newer)`로 바꿔 `rollback != active`인 상태에서 `rollback == previous.active` **및**
  `rollback != previous.rollback`을 단언한다.
- **R2-2 쓰기 전 재검증 + 스냅샷 복구**: runner 술어 검증이 되돌릴 수 없는 `os.replace`
  뒤에만 돌았다. `pair_manifest_bytes` 직후 쓰기 **전에** 돌리고, 커밋 후 재읽기가
  실패하면 pre-image 복구를 시도해 성공은 `capture_write_rolled_back`, 실패는
  `capture_write_indeterminate`로 구분한다.
- **R2-3 pre-image 증거 + generation 게이트**: `previous_manifest_sha256`·`previous_active`
  (9필드)·`previous_recorded_at`을 receipt에 넣고, 기존 manifest의 generation이 frozen
  `KTDM_C6C_CONTRACT_GENERATION`과 다르면 기본 거부한다(`--allow-generation-change`로만 통과).
- **R2-4 cross-repo 게이트**: 하드코딩된 `F:/dev/ktm-tvn36r` 경로 때문에 n150·CI에서 항상
  skip됐다. `KTDM_C7_RUNNER_MODULE`로 옮기고 **값이 주어졌는데 실패하면 skip이 아니라
  fail**시킨다. 계약 상수(top-level 키 집합·`version == 4`·pair 9필드) digest도 테스트에
  박았다. 로컬 Map 체크아웃으로 실행해 통과를, 없는 경로로 실행해 fail(skip 아님)을 확인했다.
- **R2-5 git env 위생과 ownership 구분**: 상속된 `GIT_DIR` 등 5개를 제거한 env를 하위
  프로세스에 넘긴다(실제 subprocess로 확인). stderr에 `dubious ownership`이 있으면
  `capture_refused_checkout_ownership`이라는 별도 terminal state로 알린다 — "commit 없음"과
  뭉개지 않는다.

**되돌리면 red가 되는지 실증**: 10개 수정 지점을 하나씩 되돌리는 mutation 스크립트를 돌려
전부 red를 확인했다(초판에서는 R2-2a 하나가 green으로 남아 테스트를 다시 짰다 — 기존 seed가
있으면 patched parser가 C-6에서 먼저 걸려 사전 검증 제거를 가리고 있었다).

**검증(정정 2026-08-19)**: 이 항목이 적었던 `532 passed / baseline 493`은 실측치가
아니었다. 같은 커밋을 CI-parity로 다시 돌린 실측은 `pytest -q` **541 passed / 1 skipped**,
`origin/main` baseline은 **411 passed / 0 skipped**다. `ruff check .`는 저장소 전체 68건으로
main baseline과 동일하고 변경 파일은 clean, `mypy --strict -p kor_travel_docker_manager`는
76 errors in 11 files로 main baseline과 **동일**하며 새 모듈에는 0건이다.

---

## 2026-08-19 — Map C7 런북 step 8용 `pinvi-pair capture` 복원 (읽기 전용, ADR-38)

Map 저장소 런북 `docs/runbooks/c7-prod-live-e2e.md` §2.1 step 8이 부르는
`ktdctl pinvi-pair capture --verified-compatible --build`가 Manager에 없었다. 사용자
결정에 따라 런북을 고치지 않고 Manager에 명령을 추가했다.

- **왜 manifest를 되살렸나**: `compatible-pair-v4.json`은 잔재가 아니라 살아 있는
  cross-repo 계약이다. `64069f7`에서 지운 것은 Manager 내부 reader였고, 소비자인 Map의
  C7 runner(`scripts/lib/c7_prod_attestation.py`)는 지금도 그 파일을 exact shape로
  강제한다 — top-level `{active, rollback, version}`, `version == 4`, 두 pair 모두 정확히
  9개 필드, root:root `0600`, 모든 ancestor가 uid 0·gid 0·`mode & 0o022 == 0`.
- **되살린 것**: `c6c_deployment.py`에 `CompatibleImagePair`/`CompatiblePairManifest`/
  `new_image_pair`/`parse_pair_manifest`/`manifest_with_active_pair`/
  `initial_pair_manifest`/`write_pair_manifest`(temp→fsync→chmod/chown→`os.replace`→dir
  fsync). 오케스트레이션은 새 모듈 `c6c_pair_capture.py`로 분리해 10k행
  `compose_service.py`를 키우지 않았다.
- **한 군데만 바꿨다**: 옛 writer의 `ensure_c6c_state_directory`(mkdir 부작용)를
  `assert_runner_readable_parent`(검증 전용)로 교체했다. capture는 **절대 mkdir하지
  않는다**. 부모가 없거나 정책을 어기면 거부한다.
- **의도적으로 복원하지 않은 것**: `assert_pair_manifest_bootstrap_allowed`(v4가 있으면
  거부 — 재배포 후 재capture를 막는다), `_halt_c6c_pair`/`_cleanup_bootstrap` 등
  stop/up/recreate 스테이지 전량, `compatible_pair_manifest_logical_hash`(runner는 raw
  bytes를 해시하므로 잘못된 해시가 attestation에 복사될 위험만 만든다),
  `expected_build_contexts` 계열(false-drift 생성기).
- **비파괴 보장**: 내보내는 docker argv는 `compose --project-directory ... ps -q`,
  `inspect --`, `image inspect --format=... --` 셋뿐이다. 테스트가 fake runner에 기록된
  전체 argv를 allowlist로 단언하고 `up|stop|start|rm|build|restart|kill|down` 토큰이
  어디에도 없음을 별도로 단언한다. 실패해도 컨테이너를 건드리지 않으며, 모든 non-zero
  메시지가 `maintenance fence stays closed; ...`로 끝난다.
- **state root**: 세 번째 규칙을 만들지 않으려고 `--manifest-path` 절대경로를 필수로
  했다(ADR-38 §근거). **(정정 2026-08-19)** 그 근거는 사실오류였다. n150에는 manager
  `.env`가 둘이고, 설치본
  `/opt/kor-travel-docker-manager/backend/.venv/bin/ktdctl`의 shim이
  `KOR_TRAVEL_DOCKER_MANAGER_PROJECT_ROOT=/opt/kor-travel-docker-manager`를 하드코딩하므로
  `get_env_path()`가 읽는 것은 `KTDM_DEPLOYMENT_ENVIRONMENT=production`인 `/opt` 쪽
  `.env`다. 여기서 인용한 `rehearsal`은 capture가 읽지 않는 `/home/digitie` 쪽 파일의
  값이었다. production 분기의 `c6c_state_paths` 유도값은 runner가 실제로 읽어 온
  `/var/lib/kor-travel-docker-manager/kor-travel-docker-manager/compatible-pair-v4.json`
  그 자체다. 2차 개정에서 기본값을 그 유도값으로 바꿨다.
- **정직한 자백 두 가지**: (1) 증거력이 v4보다 약하다 — 빌드도 rebuild 대조도 하지
  않으므로 `map_source_revision`은 image label 주장이고, 결박은 "그 commit이 지목된
  checkout에 실재하고 checkout이 clean"까지다. (2) "쓰기 없음"은 거짓이다 —
  `rebuild-pinned`와 같은 global mutation lock을 잡으므로 lock 디렉터리/파일이 생길 수
  있다. 둘 다 receipt의 `not_guaranteed`/`side_effects`에 노출한다.
- **cross-repo 회귀 게이트**: `backend/tests/test_c6c_pair_capture.py`가 runner 술어의
  사본(행 번호 주석 포함)으로 산출물을 검증하고, Map 체크아웃이 있으면 **실제
  `c7_prod_attestation.py` 모듈을 import해** `_validate_pair`·`_exact_dict`에 통과시킨다.
  runner가 계약을 바꾸면 이 파일이 먼저 red가 된다.
- **검증(정정 2026-08-19)**: 여기 적힌 `493 passed`와 `mypy 6건`은 실측치가 아니었다.
  CI-parity 실측은 이 브랜치 `541 passed / 1 skipped`, `origin/main` baseline
  `411 passed`, `ruff check .` 68건(main과 동일), `mypy --strict -p
  kor_travel_docker_manager` **76 errors in 11 files**(main과 동일, 새 모듈 0건)다.

n150은 읽기만 했다. prod mutation은 없었고, 실제 실행은 사용자 확인 대기다.

---
## 2026-08-19 — Map T-VN-H46F와 draft PR #173 흡수

충돌 상태인 draft PR #173의 credential 경계 의도를 최신 `main` C6c 구조에 재배치했다.
Map UI는 root `KOR_TRAVEL_MAP_KOR_TRAVEL_GEO_API_KEY`를 server-only
`KOR_TRAVEL_GEO_API_KEY`로만 받으며, candidate Compose는 미설정을 fail-close한다. raw/resolved
Compose와 runtime secret isolation은 Map API·Dagster·daemon의 source 이름, UI의 server-only
alias만 허용하고 PinVi/bootstrap 등 다른 service의 이름·값 누출을 거부한다.

Manager 자체 공개 API도 DB active key만 인정하도록 VWorld env fallback을 제거했다. #173은
오래된 base의 충돌과 후속 C6c 구조 변화를 함께 안고 있으므로 rebase보다 이 패치가 supersede하는
것이 안전하다. 전문 적대 리뷰 2명이 backend/C6c와 frontend 소비자 경계를 교차 점검해 모두
GO를 냈고, backend 전체 411개·집중 170개·Ruff·strict mypy·공개 placeholder Compose 검증을
통과했다. PR #183은 ready 상태이며 prod/n150은 변경하지 않았다.

## 2026-08-18 — issue #178/#179 n150 geo 자격증명 회전·환경파일 권한 정리 완료

PR #180에서 코드만 결선하고 보류했던 두 production 작업을 사용자 승인 아래 n150에서
완료했다. prod에 먼저 배포돼 있던 geo 예약 백업 env passthrough는 보존하면서,
`POSTGRES_PASSWORD_FILE`·fail-close DSN·권한 검사 스크립트가 포함된 Compose를 배포했다.

- **#178**: 임의 64자리 hex 비밀번호를 생성해 geo PostgreSQL superuser role과 canonical
  `.env`의 password/application DSN/Dagster DSN 세 key를 같은 transaction으로 회전했다.
  PostgreSQL, `kor-travel-geo-dagster-db-init`, geo API, Dagster web/daemon을 재생성했다.
  새 비밀번호 TCP 인증은 성공하고 공개 기본값 `addr`은 거부됐으며, PostgreSQL
  `Config.Env`에는 `POSTGRES_PASSWORD`가 없고 `POSTGRES_PASSWORD_FILE`만 남았다.
  DB init은 평문 `PGPASSWORD` 없이 exit 0, API·Dagster 소비자는 새 DSN exact 일치,
  PostgreSQL/API/Dagster/UI는 running·healthy(daemon은 running) 상태를 확인했다.
- **#179**: 기존 권한 위반 7개를 모두 `0600`으로 내리고, 이름이 잘려 배포 세대를
  식별할 수 없던 `.env.backup-pinvi-deploy-836a18f-`는 exact target 검증 뒤 삭제했다.
  `.env.example`을 제외한 root `.env*` 전체 재검사는 위반 0건으로 통과했다.
- **운영 검증**: geo API·Dagster와 Manager `/health`, Manager Web 200을 확인했다. n150
  Chromium 공개도메인 E2E에서 로그인→대시보드→로그아웃→로그인 화면 복귀를 통과했고,
  로그아웃 뒤 WebSocket 생성 0건·403 응답 0건이었다. 잘못된 비밀번호 401, Origin 없음
  403, 허용 Origin의 세션 없음 401도 확인했다.

회전 rehearsal에서 두 사전 오류(커스텀 PostgreSQL 포트와 실제 DB role 미지정)는
`ALTER ROLE` 전에 중단돼 mutation이 없었고, Docker Compose `environment` secret을
`.Mounts`에서 찾는 과도한 검증은 자동 롤백으로 기존 role·runtime을 복구했다. sibling
세 인스턴스와 같이 secret 파일은 컨테이너에서 읽히지만 Docker inspect `.Mounts`에는
나타나지 않는 Compose v5 동작을 확인해, 최종 검증은 `Config.Env` 참조·파일 읽기·DB init
성공으로 정렬했다. 각 rollback 뒤 health와 기존 TCP 인증을 재확인한 뒤 최종 회전을
다시 실행했다.

## 2026-08-18 — geo 스케줄 DB 백업 env passthrough (issue #177 geo 쪽 durable 결선)

kor-travel-geo의 앱 레벨 스케줄 백업(T-239)을 prod에서 켜는 데 필요한 `KTG_BACKUP_*` 4개가 compose에
passthrough가 없어 host-local `docker-compose.override.yml`에 임시로 들어가 있었다. 이 PR은 그것을
`.env`(`KOR_TRAVEL_GEO_BACKUP_SCHEDULE_ENABLED/SCHEDULE_INTERVAL_HOURS/ARTIFACT_TTL_DAYS/RETENTION_KEEP_MIN`)
→ geo-api·geo-dagster·geo-dagster-daemon 세 서비스 env로 정식 연결한다. 기본값은 geo Settings와 같아
(off/24/30/3) 설정이 없는 배포는 동작 변화가 없다. prod 권장값은 true/24/7/3이며, retention janitor가
RUNNING이고 성공해야 약 8본(≈ 38 GB) 수준으로 수렴한다. `docs/docker-management.md`에는 geo application DB는
앱 레벨 백업을 정본으로 두고 `ktdctl db-backup create geo`는 수동 비상 백업으로만 쓰라는 중복 주의를 적었다.
Dagster metadata DB(`geo_dagster`)는 standalone 주기 백업 대상으로 남긴다. 첫 자동 백업 실측(kor-travel-geo
journal 2026-08-18): 2026-08-18T00:15Z tick → `kor_travel_geo_backup_20260818T001517Z_zstd3.tar.zst` 4.71 GB,
sha256 verify OK, `next_due 08-19T00:15Z`.

---

## 2026-08-17 — issue #177/#178/#179: 4분할 뒤 남은 백업·자격증명·권한 공백 결선

PR #176(프로젝트별 전용 PostgreSQL 4개 분리)이 오늘 착지한 뒤 사용자가 n150 실측으로
찾아낸 세 결함을 코드 레벨에서 해결했다. 셋 다 `docs/docker-management.md`에 이미
기록된 실측/수작업 절차를 코드화하는 성격이다.

- **#177 백업 공백**: geo(33GB)·concierge·pinvi에는 백업 주체가 전혀 없었다(map만
  수작업 baseline 1세대). T-053~T-057이 v5 rebuild에서 통째로 퇴역한 뒤 남은 공백을,
  tasks.md 요약이 명시한 대로 "pair/cache workflow와 독립된 새 Compose primitive"로
  다시 채웠다 — 신설 `standalone_backup.py` + `ktdctl db-backup {create,list,gc}` +
  읽기 전용 `GET /api/v1/backups`. 포트·admin role은 하드코딩 대신 살아있는 컨테이너에서
  읽고, 연결은 `docker exec --user postgres` unix socket이라 비밀번호를 전혀 다루지
  않는다. Dashboard의 "백업 이력" 패널이 v5 rebuild로 지워진 backend route를 여전히
  호출하고 있어 조용히 404였던 것도 같이 복구했다(role 6종으로 확장, 죽은
  `schema_revision` 필드는 제거하고 `duration_sec`/`instance`/`db_size_bytes`/
  `toc_entry_count`/`alembic_head`로 대체).
- **#178 geo 평문 자격증명**: 4개 인스턴스 중 geo만 `POSTGRES_PASSWORD` 평문 env +
  추측 가능한 기본값 `addr`을 그대로 쓰고 있었다. 형제 셋과 같은
  `POSTGRES_PASSWORD_FILE` secret 패턴으로 전환하고, geo-api/geo-dagster/-daemon의
  DSN 기본값(`addr:addr` 리터럴)을 fail-close(`:?`)로 바꿨다. n150 실제 비밀번호
  회전(`ALTER ROLE` + 4개 소비자 DSN 동시 갱신)은 기존 PGDATA에 compose 변경만으로는
  안 먹으므로 별도 조율이 필요해 보류했다.
- **#179 `.env` 파생 파일 권한**: 백업본 7개가 600을 벗어나(3개는 world-writable) prod에
  남아 있었다. 원본 이름을 하드코딩하지 않는 `scripts/check-env-permissions.sh`
  (`--fix` 지원)를 추가하고, 운영 runbook에 "복사 직후 chmod 600" 관례를 못박았다.

4-dimension(security/correctness/test-coverage/compose-and-docs) 적대적 리뷰를
각 독립 검증까지 돌렸다. security는 confirmed 실공백 0건. correctness에서 confirmed
4건을 반영했다: pg_dump 성공 뒤 TOC count·docker cp 단계에서 실패하면 컨테이너 임시
dump가 안 지워지던 것을 try/finally로 감쌌고, 같은 role 동시 실행을 막는 `flock`
기반 락(`~/backups/<role>/.backup.lock`)을 추가했으며, `_ROLE_CONFIG`가 concierge/
map/pinvi의 컨테이너 이름 env override를 무시하던 것을 존중하도록 고쳤다(docker exec
timeout이 컨테이너 안쪽 pg_dump까지 죽이지는 못한다는 점은 docstring에만 명시 —
완전한 서버측 kill은 별도 과제로 남긴다). compose-and-docs에서 confirmed 3건(모두
문서·주석 정확성)도 반영: "복원도 CLI 전용"이라는 오기를 routes.py 문서화 주석과
docker-management.md에서 정정했고, 매니페스트 필드명 `created_at`→`created_at_unix`
오타를 고쳤다. test-coverage에서 confirmed 5건 중 4건을 테스트 보강으로 반영
(pg_dump/TOC/cp 호출의 정확한 인자 검증, gc `keep==count` 경계, `db-backup
create/list/gc` CLI 실경로 smoke, 컨테이너/DB 이름 검증 가드) — 프론트엔드
`formatBytes`/`formatTimestamp` 무테스트는 저장소 전체에 frontend 테스트 파일이
하나도 없는 기존 공백이라 이번 범위에서는 반영하지 않았다.

backend 전체 404 passed, ruff/mypy(기존 환경 오류 제외) clean, frontend
type-check·lint·build clean. n150 실제 크리덴셜 회전·기존 파일 chmod/정리·cron
설치·geo 첫 백업 실행은 모두 보류 — 라이브 프로덕션 credential rotation과 삭제를
포함해 사용자 확인이 필요하다.

또한 이슈 #107(map_release_revision `4a764a4f`→`6b537ed9` 재pin, Map PR #929의
`quarantine_candidates_before` preflight 게이트 추가)을 additive/호환 확인 뒤 반영했고,
#109/#111/#114는 T-050/T-051에서 이미 완료돼 있던 것을 확인해 종료했으며, #128은
이후 T-VN-41 pin rotation(0084까지 전진)으로 이미 superseded된 것을 확인해 종료했다.

---

## 2026-08-15 — T-VN-40 canonical snapshot principal 최소 권한 결선

- Manager frozen environment가 PinVi canonical snapshot·cutover mapping 원시 token pair에서 Map API용
  SHA-256 digest를 파생하도록 했다. raw token은 ordinary PinVi API만 받고, Map API는 두 digest만 받으며
  Map UI·Dagster·bootstrap과 PinVi Web·Dagster에는 두 형태 모두 전달하지 않는다.
- raw/resolved Compose 및 runtime secret isolation validator가 pair의 함께 설정·최소 길이·공백·상호 불일치,
  기존 보호 credential과의 재사용 금지, digest 단독 주입·불일치, 정확한 service path 외 이름/값 누출을
  container mutation 전에 fail-close하도록 확장했다.
- C6c deployment config와 Compose frozen snapshot에 동일 derivation을 적용해 preflight와 실제 subprocess
  environment가 갈라지지 않도록 했다. contract test는 API-only 전달, digest 정확성, bootstrap 제외와
  부분·재사용·위조 digest 거부를 고정한다.

---

## 2026-08-13 — Hallmark 운영 콘솔 전면 재설계

- Hallmark v1.1.0을 적용해 현재 frontend를 감사했다. 과거 BMW M 기록과 실제 운영 콘솔 사이의
  디자인 정본 불일치, 동일 KPI 카드 반복, modal blur/중첩 카드, 모바일 가로 스크롤 표, 토큰을 우회한
  Recharts 색상, `transition-all`을 critical 1·major 5·minor 1로 기록했다.
- `DESIGN.md`를 Kor Travel 운영 콘솔 정본으로 교체하고 `frontend/tokens.css`에 Cobalt 색상·서체·간격·
  radius·motion 토큰을 분리했다. display는 Space Grotesk, 본문은 IBM Plex Sans, 데이터는 IBM Plex Mono를
  사용한다. Hallmark 실행 메타데이터는 `.hallmark/log.json`에 남겼다.
- 대시보드는 Workbench 구조로 바꿨다. 네 개의 같은 KPI 카드는 하나의 상태 원장과 graphite 동기화 신호면으로
  합쳤고, `⌘/Ctrl + K` 인라인 명령 팔레트는 인증 설정·백업 이력·상태 새로고침·로그아웃을 실제로 실행한다.
  서비스 표는 768px 이하에서 셀 레이블이 있는 행 카드로 전환해 가로 스크롤에 의존하지 않는다.
- 로그인, 오류 화면, inspect·로그·차트·구성 변경·백업·인증 설정 패널을 같은 Cobalt 표면과 `ops-*` 상호작용
  상태로 수렴했다. 차트는 색상·tooltip·서체를 토큰으로만 참조하고, backdrop blur와 `transition-all`을 제거했다.
- `npm run type-check`와 `npm run lint`를 통과했다. `npm run build`는 이 worktree에서 Next.js 최적화 단계가
  120초 안에 끝나지 않아 시간 제한으로 중단했으며, PR CI에서 다시 확인한다. upstream exact Map/PinVi pair
  부재로 #171의 n150 live E2E와 merge gate는 계속 보류한다.

---

## 2026-08-12 — #171 전용 Map PostgreSQL P0 재검토 보강

- 2인의 적대적 재검토에서 발견된 P0를 반영했다. 장기 실행 Dagster의 metadata DSN을 전용 non-superuser
  login으로 분리하고, bootstrap superuser/role password는 F1D one-shot 밖으로 전달하지 않는다.
- C6c가 모든 Map DB DSN의 loopback `12703`, database, principal을 bootstrap 전에 검증하고 Map DB 관련
  service의 host network 이탈도 거부한다. shared `5432` 오결선·bridge override는 mutation 전에 fail-close한다.
- bootstrap catalog assertion은 PostgreSQL 16 membership option, group/login option, ownership, extension schema,
  relation/default ACL과 Dagster metadata DB owner까지 검증한다. pre-probe resume은 기존 checkpoint를 신뢰하지
  않고 reset과 두 bootstrap one-shot을 다시 수행한다.
- assertion은 bootstrap 직후 빈 application DB에서만 실행한다. upstream migration이 부여한 허용 runtime ACL은
  armed 이후 durable fixture resume에서 재검사하지 않으며, `PUBLIC` relation/default ACL은 bootstrap invariant
  위반으로 거부한다.
- 전용 PostgreSQL initial superuser password는 `POSTGRES_PASSWORD_FILE` Docker secret으로 이동했다. disposable
  Compose rehearsal에서 PostgreSQL 기동·secret file 인증·`docker inspect Config.Env`의 password 부재를 확인했고,
  raw/resolved Compose는 해당 secret을 PostgreSQL entrypoint 외 service가 mount하면 거부한다. F1D도 reset 전
  frozen Compose/`compose ps`가 확인한 실제 PostgreSQL `Config.Env`의 password 부재와 정확한 secret file
  경로를 fail-close한다.
- targeted 회귀 149개와 disposable PostgreSQL 16 catalog rehearsal을 통과했다. upstream exact Map/PinVi pair가
  아직 없으므로 n150 live E2E와 Manager merge는 계속 보류한다.

---

## 2026-08-12 — #171 전용 Map PostgreSQL 경계 승인

- ADR-090의 Map principal bootstrap을 공유 `kor-travel-geo-postgres`에 적용하지 않기로 확정했다.
  공유 DB recovery가 legacy `krtour_map` ownership·ACL을 복원하므로, shared bootstrap은 권한 경계를
  무음으로 되돌리고 실패 시 partial mutation도 남긴다.
- Map application과 Dagster metadata는 전용 `kor-travel-map-postgres`의 loopback `127.0.0.1:12703`으로
  이동한다. 통합 PostgreSQL `5432`는 Geo·Concierge·PinVi lifecycle만 계속 관리한다.
- bootstrap은 F1D reset 뒤 Manager가 실행하는 one-shot으로만 허용한다. bootstrap superuser DSN과 세 role
  password는 normal Map/PinVi runtime, Docker 장기 metadata, journal, stdout에 남기지 않는 것을 구현·검증
  조건으로 둔다.
- 정확한 Map release pin은 upstream Map PR의 merge된 revision과 PinVi compatibility artifact가 확정된 뒤에만
  갱신한다. draft source의 SHA를 production authority로 추정하지 않는다.
- Manager 구현은 전용 DB service, strict principal DSN wiring, profile one-shot bootstrap, F1D catalog
  assertion과 shared recovery의 Map lifecycle 제거까지 진행했다. exact upstream pair가 없으므로 n150 live
  E2E는 아직 실행하지 않았다.

---

## 2026-08-11 (백로그 상태 정리 — F1D-D 수용 검증 범위 명확화)

원격 `main`의 최신 F1D v5 상태를 기준으로, 진행 표의 유일한 미완료 항목을
`T-VN-41-F1D-D`로 명확히 했다. 2026-08-06 C3 파기형 재구성과 7개 실행 컨테이너·스키마·fixture,
로그인 및 데이터 비의존 관리자 UI smoke는 완료됐다.

남은 범위는 Manager의 추가 세대 변경이 아니다. 최종 스키마에 맞춘 원천/ETL 재적재는
별도 작업 흐름으로 인계하며, 재적재 뒤 고정 curated/feature ID를 전제하는 관리자 UI 상세·지도
표 landmark E2E와 PinVi 변경 E2E를 다시 실행해 결과를 기록한다. 이 수용 검증이 통과하면
F1D-D를 완료 이력으로 이관한다.

---

## 2026-08-06 (T-VN-41-F1D-C3 — n150 파기형 rebuild 실증)

최신 Map typed-subtype pin으로 n150에서 `rebuild-pinned --confirm`을 실행해 새 generation을 `committed`로
결선했다. Map application `0087_route_area_subtypes`, Map Dagster `29b539ebc72a`, PinVi `20260804_0049`
schema head와 일곱 runtime container health를 확인했다. v7 journal receipt는 Map fixture가
`armed → consumed → finalized`로 단조 전이했고 PinVi canonical cancel outcome이 exact
`409 PIPELINE_CANCELLATION_UNSAFE`였음을 보존한다.

Map UI 로그인 POST는 `200`과 session cookie를 반환했다. n150 live UI E2E에서 운영 홈·파이프라인 catalog
6건과 Feature 목록·지도 초기 surface 10건을 통과했다. 새 DB에는 source/ETL data를 의도적으로 다시 적재하지
않았으므로, 고정 curated/feature ID를 요구한 기존 full suite의 일부는 data-dependent failure로 분리했다.
이는 C3 transaction 실패가 아니며 final-schema ETL 재적재 뒤 F1D-D acceptance에서 재실행한다.

---

## 2026-08-06 (T-VN-41-F1D-C3 — Map typed-subtype release pin 갱신)

Map `main`의 typed subtype 단일 정본 schema release(새 application head `0087`)를 F1D
candidate source pin으로 고정한다. 이 release는 Map core의 `detail`/`geom` legacy 정본을
제거하므로, 기존 DB를 보전하거나 intermediate schema에 맞추는 경로를 만들지 않는다. C3의
candidate static attestation과 파기형 세 DB 재생성은 새 Map head만 수용한다. v7
journal/tombstone filename도 pinset SHA로 분리해 old same-version state가 이 release의 새
generation을 차단하지 않게 한다.

---

## 2026-08-06 (T-VN-41-F1D-C3 — tombstone resume fail-close 보강)

적대적 리뷰에서 v7 journal을 쓴 직후 legacy tombstone이 실패하거나 process가 종료되면 다음 resume이
tombstone을 건너뛰어 destructive reset으로 갈 수 있음을 확인했다. 새 journal과 existing journal 모두
runtime/DB mutation 전에 동일 transaction/candidate의 tombstone receipt를 idempotently 재검증하도록
수렴시킨다. fixture `armed` receipt 전에는 기존처럼 세 DB를 다시 만들고, receipt 후에는 Map GET으로
immutable outcome을 수렴해야 하므로 DB를 보존한다. journal receipt에는 Map lifecycle의 creation,
consumption, finalization UTC evidence도 함께 고정한다. 이 필드가 없던 draft v6 journal/receipt는
v7 reader가 해석하지 않고 allowlisted legacy tombstone으로 퇴역한다.

---

## 2026-08-06 (T-VN-41-F1D-C3 — dynamic fixture 결선 설계)

v5 rebuild의 core path는 Map-owned F1J fixture helper를 실제 호출하지 않는다. C3는 이를
`rebuild-pinned` transaction에 결선하면서 journal을 v7 단일 형식으로 교체한다. fixture `armed`,
cancel/finalize POST 전 `attempted`, immutable `consumed` outcome, `finalized`를 secret 없이 매 전이에
fsync한다. 응답 유실 재개는 Map GET만 허용하며, attempted 뒤 같은 POST를 추측 재발행하지 않는다.

---

## 2026-08-06 (T-VN-41-F1D-C2 완료 이관)

v5 single-active generation과 candidate-first destructive rebuild, Map Dagster/application 및 PinVi
one-shot schema bootstrap을 결선했다. candidate source/config의 raw·resolved capability boundary를 DB
mutation 전에 fail-close하고 bootstrap profile에서 허용한 최소 Map read/cancel capability만 사용한다.

이전 F1J helper는 새 v5 transaction에서 아직 호출되지 않음을 확인했다. 따라서 dynamic fixture
ensure→one-shot canonical cancel→immutable receipt read→finalize와 response-loss resume은 완료로
표시하지 않고 열린 F1D-C3에서 journal 상태와 함께 결선한다.

---

## 2026-08-06 (T-VN-41-F1D-C1b — PinVi runtime provenance caller 결선)

PinVi Dockerfile label만으로는 Manager canonical Compose가 Web·Dagster에 revision/environment
build argument를 전달하지 않아 두 image가 `development` label로 빌드될 수 있었다. F1D candidate의
image inspect는 이미 일곱 image와 PinVi 세 image의 production label을 확인하므로, 누락된 caller
경계를 세 service build mapping으로 보강했다.

`validate_c6c_build_source_wiring`과 resolved C6c provenance preflight가 API·Web·Dagster의
exact source revision, production environment, Dockerfile path를 모두 fail-close로 검사한다. 실제
Compose resolver regression은 세 service가 같은 candidate argument를 받을 때만 통과하며, Dagster
argument 하나를 제거하면 preflight가 거부한다. 이 preflight는 이제 `rebuild_pinned_runtime`의
candidate seven-image build 직전에 staged source root를 expected context로 전달해 실행한다.
따라서 context가 staged Git snapshot 밖이면 Docker build가 시작되기 전에 중단한다. PinVi PR merge
SHA를 pinned release input으로 회전한 뒤에만 n150 destructive rebuild를 재개한다.

---

## 2026-08-06 (T-VN-41-F1D — legacy 공개 mutation 경로 퇴역)

F1D v5의 유일한 generation mutation은 비운영 `ktdctl pinvi-pair rebuild-pinned --confirm`으로
수렴한다. 따라서 구 compatible-pair의 `capture`·`deploy`·`rollback`, `cache-target`, Map UI 회전과
standalone `db-backup` 공개 명령을 CLI에서 제거했다. Compose의 pair/cache workflow가 backup primitive도
함께 제거했으므로 `create`·`list` 역시 제거해 dangling 경로를 만들지 않는다.

최종 schema 상태의 backup/restore 필요성은 별도로 유지하되, 이후에는 cache-target/pair 중간 state와
독립된 Compose contract를 새 태스크에서 설계한다. `tasks.md`와 production runbook은 v1–v4 내용을
퇴역 기록으로 명시하고, 과거 작업 일지와 완료 이력은 변경하지 않는다.

---

## 2026-08-06 (T-VN-41-F1D-C0 — Map Dagster storage 계약 병행)

F1D-B 영향도 확인에서 Map application Alembic revision과 Dagster dependency storage revision이 서로 다른
정본임을 확인했다. 따라서 source pin 또는 Map application head로 `map_dagster_head`를 추정하지 않는다.
Map candidate Dagster image가 자신의 storage head를 출력하고, 같은 image가 `dagster instance migrate` 뒤
strict single-row `public.alembic_version`을 대조하는 C0 PR을 F1D-C1과 병행한다. C2는 두 upstream PR의
exact source pin을 입력으로 받는다.

기존 F1G/F1H의 legacy journal 퇴역은 F1D-B typed tombstone allowlist에 흡수했다. 별도 복구·rollback
authority나 T-VN-41 선행 task로 남기지 않으며, foreign residue는 rebuild 전 fail-close한다.

---

## 2026-08-06 (T-VN-41-F1D-B — v5 generation foundation 진행)

F1D-A 설계 PR #165 병합 뒤, legacy compatible pair와 분리한
`pinned_runtime_generation` typed model을 추가했다. 이 model은 `local/development`,
`rehearsal/rebuildable`, `production/operational`의 배타적 lifecycle pair와 Map 네 image·PinVi 세 image,
세 schema head, single-active v5 manifest, candidate-first rebuild journal의 strict shape를 검증한다.

이 commit은 아직 runtime/DB mutation을 열지 않는다. F1D-B는 legacy reader/gate를 safe tombstone으로
교체하고, C1 PinVi one-shot migration/admin bootstrap PR과 함께 C2 `rebuild-pinned` orchestration의 입력을
완성한다.

---

## 2026-08-06 (T-VN-41-F1D-A — 파기형 runtime generation 재bootstrap 설계)

비운영 n150 state를 read-only로 확인한 결과, 기존 F1D journal은 prior pinset의 `prepared` receipt와
old Map schema head에 결박돼 있었고 Map 네 service와 PinVi API만 runtime pair에 기록했다. PinVi Web·Dagster는
같은 source/PinVi DB를 공유하지만 generation 밖에 남으므로, old journal을 복구하거나 다섯 service만
재기동하는 방식은 완결된 수렴이 아니다.

데이터·중간 DB·backup/restore 보전이 필요 없다는 결정에 따라, F1D는 일곱 service
`PinnedRuntimeGeneration` v5와 scoped fresh DB recreate를 소유하는 `rebuild-pinned --confirm`으로
재작성한다. source authority·ops principal 분리는 유지하고, old runtime/DB 복원이나 raw Docker/SQL/state
삭제는 허용하지 않는다. 상세 설계와 F1D-B/C/D PR 단위는
[`tvn41-f1d-destructive-rebootstrap.md`](tvn41-f1d-destructive-rebootstrap.md)에 기록했다.

---

## 2026-08-06 (T-VN-41-F1J-D — fresh isolated final rehearsal 완료)

Map #960, PinVi #439, Manager #163 정확한 source에서 새 Compose project·DB·volume·network와 매 run 생성한
credential만 사용해 F1J-D를 끝냈다. PinVi API는 Map API의 private control-network address로만 연결하고
`ops:read` 사전 점검을 먼저 통과해야 다음 단계로 진행한다. host loopback publish를 다른 bridge에서
우회하지 않으며, admin proxy credential을 ops principal 경계에 보내지 않는다.

Map/PinVi health와 fresh migration 뒤 direct ops read는 `200`이었다. Manager canonical smoke의 login·ETL·
provider-sync는 `200`, cancel은 exact `409 PIPELINE_CANCELLATION_UNSAFE`, durable resume 결과는 동일했다.
관리자 live UI Playwright는 5/5, 새 PinVi DB의 mutating trip WebSocket/reconnect E2E는 1/1 통과했다. 기존
runtime/data/backup/restore는 사용하지 않았고, exit cleanup은 root 소유 Playwright dependency까지 일회성
scratch·container·volume·network·image tag를 폐기했다. F1J 보강은 완료로 이관하며, 별도 F1D bootstrap issue는
완료로 오인해 닫지 않는다.

---

## 2026-08-06 (T-VN-41-F1J-D — live trip E2E revision 재결박)

새 isolated schema에서 Map ops read와 관리자 live UI는 통과했지만, mutating trip E2E 하나가
`POST /trips`의 `201 Created` 뒤 더는 표시되지 않는 옛 성공 문구를 기다려 실패했다. PinVi PR #439는
현재 UI의 `초안 여행을 저장했습니다.` assertion으로 이 test-only drift를 바로잡았고 CI를 통과했다.

Manager도 tracked source revision을 이 merge SHA와 canonical pinset digest로 회전한다. 이 값은 Map service
provenance·Alembic head·capability generation을 변경하지 않으며, 기존 runtime·DB·backup/restore를 쓰지 않는
fresh isolated F1J-D final run의 exact input만 다시 고정한다.

---

## 2026-08-06 (T-VN-41-F1J-D — PinVi Docker provenance repair pin 재결박)

PinVi PR #437(`6a931dc…`)은 Hatch `force-include` source를 두 editable install 전에 Docker build context의
`/contracts`에 제공하고, 재설치 뒤 final image에서 제거했다. CI는 실제 root Docker build와 package resource의
exact bytes/SHA·payload·helper 미잔존을 검사하며, n150의 새 임시 checkout cold build도 같은 검증을 통과했다.
예약 staleness 실행에는 이 image build를 추가하지 않는다.

Manager의 tracked pinset은 PinVi source revision까지 fail-close로 소유하므로, build 결함이 있는 이전
`2d59855…`를 유지하지 않고 이 merge revision으로 원자 회전한다. 이 회전 뒤 F1J-D는 Map·PinVi·Manager의 exact
release만 새 격리 Compose stack에서 사용한다. 기존 runtime/DB를 읽거나 보존·backup/restore하지 않는다.

---

## 2026-08-06 (T-VN-41-F1J-C — provenance 재결박 merge 완료)

PinVi PR #435(`2d59855…`)와 Manager PR #160(`0ff7f8d…`)이 merge됐다. PinVi는 일반 Map service provenance를
wheel/Docker runtime과 CI required gate까지 같은 bytes로 소비하고, Manager는 Map `1df45b57…`, PinVi merge,
service OpenAPI SHA, Map Alembic `0084_c6c_cancel_probe_fixtures`, cache-target/C6c capability를 새 pinset으로
원자 회전했다. compatible-pair manifest v4는 변경하지 않았다.

적대적 리뷰 1인은 실제 고정 Git artifact·migration·vendor/provenance bytes, root-owned worktree 검증 순서,
capability drift 음성 회귀를 검토해 P0/P1 없이 GO로 판정했다. 이제 남은 F1J-D는 n150에서 production stack/data를
읽거나 보존하지 않는 새 isolated Compose stack의 destructive rehearsal과 live UI E2E다.

---

## 2026-08-06 (T-VN-41-F1J-C — 일반 Map service provenance preflight 착수)

F1J-A(Map PR #960), F1J-B(Manager PR #159), 그리고 PinVi PR #435가 merge되어 dynamic fixture lifecycle,
durable receipt, 일반 provenance artifact가 각 소유 경계에 반영됐다. F1J-C는 C6c 전용 값을 기존 compatible-pair
manifest v4에 덧붙이지 않고, PinVi가
Map release revision·service OpenAPI SHA·`cache_target`/`c6c_cancel_probe` generation을 함께 소유하는 일반
service provenance artifact로 정리한다. Manager는 trusted PinVi source의 exact artifact를 현 Map artifact 및
cache-target pin과 preflight에서 교차 검증하고 어느 값이라도 drift하면 Docker/DB/runtime mutation 전에
fail-close한다. Manager PR #160은 Map #960 release, PinVi #435 merge release, Map Alembic `0084`를 하나의
새 pinset으로 회전한다.

최종 F1J-D는 production data를 보존·복원하는 작업이 아니라 n150 격리 stack의 파괴적 검증이다. 현 data는
유실돼도 최종 schema 기준 source/ETL로 재적재하며, 검증 중 backup/restore 또는 실제 production runtime 조작은
하지 않는다.

---

## 2026-08-06 (T-VN-41-F1J-B — dynamic fixture와 F1D durable receipt 구현)

Manager는 static `KTDM_C6C_CANCEL_PROBE_JOB_ID`를 완전히 제거하고 Map API에만 주입되는 별도 fixture
capability로 lifecycle API를 호출한다. candidate Map readiness 뒤에 dynamic fixture를 ensure하고 반환된 job ID만
PinVi의 기존 cancellation relay에 전달한다. 성공 결과는 canonical detail을 포함한 정확한
`409 PIPELINE_CANCELLATION_UNSAFE` 하나이며, `404`·`429`·`502`·`503`·다른 `409`·재시도 지시는 모두 fail-close다.

F1D journal은 fixture의 job/cancellation identity, lifecycle, POST 전 attempted 상태와 exact result/finalization
receipt를 각 전이 직후 durable write한다. receipt는 `armed → consumed → finalized` 및 attempted false→true만
허용하고 확정 identity/result/timestamp를 후퇴시키지 않는다. response loss 뒤에는 Map의 immutable canonical outcome을
읽어 same durable receipt를 확정하고 같은 destructive POST 없이 finalize를 재개한다. focused C6c 923개와 Manager
receipt regression 939개, 전체 backend suite 1,708개를 Linux tmpfs에서 통과했다. strict mypy는 fixture
lifecycle state와 receipt response의 exact type을 다시 좁혔고, 적대적 코드 리뷰 1인은 dynamic ensure·POST 전
attempted fsync·response-loss 재개·단조 receipt·credential isolation·정적 UUID 제거를 재검토해 GO로 판정했다.

---

## 2026-08-06 (T-VN-41-F1J — Map 소유 cancel-probe fixture lifecycle 설계)

F1I의 safe checkpoint로 마지막 F1D candidate attempt 하나를 분리한 결과, PinVi login·ETL summary·provider
sync는 모두 `200`이고 configured cancel probe만 `404`였다. 이는 Manager runtime, PinVi session/role 또는
read-route 문제가 아니라 static probe UUID에 대응하는 Map execution fixture의 lifecycle owner가 없다는
결론이다.

단일 적대적 설계 리뷰를 반영해 Manager는 candidate Map API가 준비된 뒤 PinVi smoke 전 Map의 전용 internal
lifecycle API를 호출한다. Map은 transaction-scoped dynamic fixture, dedicated `ops:fixture` principal, canonical
cancellation record FK와 `armed → consumed → finalized` durable state를 소유한다. PinVi는 기존 normal cancel
relay만 수행하고 Manager는 정확한 `409 PIPELINE_CANCELLATION_UNSAFE` 하나만 성공으로 인정한다. static UUID,
Manager direct DB/`docker exec`, Map startup seed, Dagster failure `502/503` 허용은 모두 제거 대상이다.

Map → pair rebind → Manager → n150 destructive verification의 네 PR/운영 단계와 crash recovery 조건은
[`tvn41-f1j-cancel-probe-fixture.md`](tvn41-f1j-cancel-probe-fixture.md)에 고정했다.

---

## 2026-08-05 (T-VN-41-F1G — legacy terminal window 퇴역 설계)

n150 trusted Manager release `067a851…`의 F1F input installer는 Docker·DB·runtime mutation 전에 legacy
`cache-target-window-v1.json`의 terminal `rolled_back` state를 발견해 fail-close했다. v1 window는 현재
writer-drain/rollback schema의 정본이 아니므로 raw state-directory 삭제나 자동 migration으로 넘기지 않는다.

F1G는 production 전용 receipt-first retirement command로 exact owner-only v1 `rolled_back`만 SHA/phase
evidence를 durable하게 남기고 unlink한다. global lock/frozen input revalidation은 유지하며 other manager
state·Docker·Compose·DB·runtime·manifest·backup은 변경하지 않는다. 이 PR merge와 n150 민감값 없는 retirement
뒤 F1F input first-run/idempotent rerun을 재개한다.

---

## 2026-08-05 (T-VN-41-F1F-B — Manager merge 완료)

PR #149는 squash merge `8329f834…`로 Manager `main`에 반영됐다. Map `8c5bdcf8…`, PinVi
`3b87c19c…`, service artifact SHA `c7838b20…`, contract generation `7`, Map application head
`0083_nonderived_uuid_generator`를 v2 pin manifest의 유일 authority로 만들고, static compose literal을
required canonical env scalar로 치환했다.

단일 적대적 리뷰는 archive 직후 crash, input과 F1D receipt의 frozen env binding, B→A(v2) rollback
재시도를 재검토해 P0/P1 없음으로 종료했다. focused regression 143건, 전체 `backend/tests`, 변경 파일
Ruff와 diff check를 통과했다. remote GitHub CI workflow는 구성돼 있지 않아 PR check는 없었다. 다음
mutation은 이 merge를 n150 trusted Manager release로 설치해 F1F first-run/idempotent rerun을 무 Docker/DB/
runtime mutation으로 증명한 뒤에만 F1D destructive bootstrap으로 진행한다.

---

## 2026-08-05 (T-VN-41-F1F-B — versioned input/F1D receipt 재시도 보강 중)

F1F-B는 Map `8c5bdcf8…`, PinVi `3b87c19c…`, service OpenAPI SHA와 Map application head를 v2
pin manifest의 유일 release authority로 승격했다. trusted installer는 source root/revision, PinVi
contract scalar, migration expected head를 Docker·Compose·DB·runtime 없이 owner-preserving atomic `.env`
replace 한 번으로 설치하고, F1D는 해당 handoff receipt 없이는 시작하지 않는다.

적대적 리뷰에서 future re-pin이 static state를 덮거나 B→A(v2) rollback 재시도가 legacy v1 predecessor로
잘못 분기할 수 있는 결함을 발견했다. 이를 `pinned-deployment-inputs-v2/history/<pinset_sha256>` 불변
세대로 정리하고, predecessor input의 frozen env SHA·검증된 worktree tree·pinset별 archive된 F1D
receipt를 교차검증하도록 보강했다. F1D journal을 archive한 직후 process가 종료돼도 archive receipt를 다시
엄격하게 검증해 재개한다. 같은 pinset의 `prepared → env_replaced → handoff_pending → f1d_in_progress →
f1d_completed` 재개와 B rollback 재시도를 모두 fail-closed로 처리한다. focused regression 143건과
Ruff를 통과했고, 변경 모듈 strict mypy는 기존 `registry.py:39` 반환형 오류 외 새 오류가 없다. 다음은
전체 backend 검증과 Manager PR의 CI/merge, 이후 n150 trusted release first-run·F1D live bootstrap·idempotent
rerun·admin UI E2E다.

---

## 2026-08-05 (T-VN-41-F1D — legacy protected-value gate 정정)

legacy image tuple을 허용한 trusted release의 다음 product preflight는 현재 protected-value wiring이 frozen
candidate 환경과 다르다는 evidence에서 mutation 없이 종료했다. F1D는 old runtime을 rollback/candidate authority로
재사용하지 않고 five-runtime을 exact candidate 환경으로 재생성하므로, 일반 deploy의 current runtime secret/UI
equality와 active image tuple equality를 시작 조건으로 재사용한 것도 수렴 목적과 맞지 않았다.

후속 수정은 candidate resolved Compose의 secret isolation을 mutation 전에 계속 강제하되, runtime secret isolation과
UI auth는 candidate activation 뒤 exact image·frozen environment에서만 검증한다. 이 뒤 검증 실패는 old image를
되살리지 않고 five-runtime halt로 수렴한다. legacy runtime protected value와 image tuple은 비교·재사용하지 않는다.

---

## 2026-08-05 (T-VN-41-F1D — legacy runtime tuple gate 정정)

candidate/live DB head gate를 정정한 trusted release의 다음 product preflight는 기존 Map UI와 Map API의 source
revision이 다르다는 evidence에서 mutation 없이 종료했다. F1D의 목적은 바로 이 legacy runtime drift를 candidate로
수렴하는 것이므로, 일반 deploy의 current-pair provenance 검증을 시작 조건으로 재사용한 것은 과도했다.

후속 수정은 시작 runtime이 manifest active와 완전히 같은 다섯 immutable image ID인지로만 이미 수렴한 상태를
판별한다. 시작 Map service의 source revision은 읽지 않으며, old image·manifest는 새 rollback source로 채택하지
않는다. candidate provenance, secret/UI, candidate/live DB head, durable journal, activation 뒤 halt 정책은 그대로
유지한다.

---

## 2026-08-05 (T-VN-41-F1D — legacy old-image head gate 정정)

병합한 F1D trusted release의 첫 product preflight는 old active Map API image head와 live DB head가 다르다는
evidence에서 mutation 없이 종료했다. 이 사실은 F1D가 해결해야 할 기존 drift이며, old image는 새 rollback으로
승격하거나 재기동하지 않는다. 따라서 old image static head를 candidate build/activation의 hard gate로 둔 ADR-31
문구는 transaction 목적과 맞지 않았다.

후속 수정은 old manifest·local immutable provenance는 유지하되, DB schema hard gate를 candidate Map/PinVi image와
live DB head의 일치로 한정한다. candidate/live mismatch는 계속 H35 coupled recovery만 허용한다. 이 변경 뒤 같은
product command를 재실행해 candidate activation과 final UI E2E를 확인한다.

---

## 2026-08-05 (T-VN-41-F1D — durable bootstrap 구현 중)

F1E terminal source selection 뒤에만 `ktdctl pinvi-pair bootstrap-pinned-drift --confirm`이 candidate를
빌드한다. 이 command는 stale runtime과 old manifest를 rollback source로 승격하지 않으며, tracked release
pin의 clean build provenance·immutable image provenance와 current Map/PinVi database head의 static candidate
Alembic head를 mutation 전에 일치시킨다.

owner-only `pinned-drift-bootstrap-v1.json`은 frozen env/Compose digest, old manifest SHA와 pair, candidate
immutable IDs/source revisions, 세 database head를 `prepared → runtime_activated → manifest_committing → committed`로
fsync한다. `manifest_committing` intent는 manifest fsync와 terminal journal 기록 사이 crash에서도 old/candidate-only
manifest를 구분해 같은 candidate로 수렴하게 한다. non-terminal journal은 일반 deploy/capture/rollback을 차단하고,
F1D만 동일 candidate로 재개한다. candidate activation·재검증·DB head 검증이 실패하면 old image rollback 대신
protected Map 네 runtime과 PinVi API를 halt한다. 단일 적대적 코드 리뷰의 F1E committed source evidence,
manifest/journal crash resume, candidate/live DB head와 old provenance, halt 수렴, terminal frozen input, CLI 출력 계약 지적을
보강했다. focused 85 passed, backend 전체 1655 passed, Ruff와 변경 source strict mypy를 통과했다. 다음 단계는
PR merge 뒤 trusted release 설치와 destructive live bootstrap·idempotent 재실행·admin UI E2E다.

---

