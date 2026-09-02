# JOURNAL — 작업 일지

이 파일은 `kor-travel-docker-manager` 저장소에서 진행된 작업을 역시간순(가장 최신 항목이 맨 위)으로 기록한다.

## 2026-09-02 — WAL fallback 경고 once-guard 경합 수정 (48ab0cc2 적대적 리뷰 2인 반영)

바로 아래 항목(`48ab0cc2`, WAL 모드 안전 fallback + `save_metric` to_thread 테스트
커버리지)을 독립 적대적 리뷰 2인에게 맡겼다. 한 리뷰는 결함 없음으로 판정했고,
다른 한 리뷰가 Medium 1건을 찾았다.

**Medium(고침)**: `database.py`의 `_wal_fallback_warning_emitted` once-guard가
"플래그 확인 → `logger.warning()` → 플래그 세팅" 순서로 non-atomic했다. 엔진이
`connect_args={"check_same_thread": False}`라 connect 이벤트가 FastAPI 스레드풀과
metrics collector의 `asyncio.to_thread` 양쪽에서 동시에 발생할 수 있는데, 실제
핸들러(파일/syslog 등 I/O가 있는 핸들러)가 `logger.warning()` 도중 GIL을 놓으면
그 틈에 다른 스레드가 여전히 "아직 안 남겼음" 상태를 보고 통과해 경고가 여러 번
찍힐 수 있다는 지적이다 — 리뷰어가 지연 핸들러 + 동시 연결 64개로 11~12건 중복을
실측 재현했다. 영향은 로그 스팸뿐(모든 연결이 여전히 올바르게 rollback-journal로
fallback한다)이지만, 코드 자신의 주석("경고를 한 번만 남기고")과 정면으로
어긋나고, 이 프로젝트가 이미 `disk_usage.py`/`job_runner.py`/`source_status.py`/
`deployment_readiness.py`에서 쓰는 모듈 전역 상태 보호 관례(`threading.Lock()`)를
이 새 코드만 건너뛴 것이었다. `_wal_fallback_lock = threading.Lock()`을 추가해
확인과 세팅(및 `logger.warning()` 호출 자체)을 그 락 안에서 원자적으로 묶었다 —
락을 쥔 스레드가 로그를 남기는 동안 다른 스레드는 GIL 스케줄링이 아니라 락
자체에 막혀 대기하므로, 핸들러 속도와 무관하게 항상 정확히 한 번만 남는다.

`test_database.py`에 회귀 테스트
`test_set_sqlite_wal_mode_warns_only_once_under_concurrent_connections`를
추가했다: 지연 핸들러(10ms sleep)를 걸고 스레드 32개를 `threading.Barrier`로
동시에 출발시켜 각자 `:memory:` 연결로 `_set_sqlite_wal_mode`를 호출하는 시행을
5번 반복하며, 캡처된 경고 총 개수가 시행 횟수(5)와 정확히 같은지 확인한다.
기존 순차 호출 테스트(같은 스레드에서 두 번 부름)는 이 경합을 전혀 커버하지
못한다. 단일 시행만으로는 OS 스레드 스케줄링에 따라 non-atomic 버전이 우연히
안 걸릴 수 있음을 실측으로 확인했다(고치기 전 코드로 단일 시행 3회 중 1회는
우연히 통과) — 5회 반복으로 바꾸자 non-atomic 버전은 5회 연속 안정적으로 잡혔고,
고친 코드는 여러 번 반복 실행해도 항상 결정적으로 통과했다. 새 로직을 원래의
non-atomic 버전으로 되돌려(`MUTATION-TEST-TEMP`) 이 테스트가 실제로 실패하는
것을 확인한 뒤 정확히 복원해 재통과시켰다.

**Nit(고치지 않음)**: 같은 리뷰가 `test_database.py`의 신규/수정 라인 중 2줄이
`ruff format` 결과와 다르다고 지적했으나, 리뷰 스스로 확인했듯 저장소 내 84/105
파일이 이미 `ruff format --check`를 통과하지 못해 이 저장소에서 포맷팅은 애초에
게이트되지 않는다 — 조치하지 않는다.

전체 backend 테스트(1489 passed, 2 skipped)와 `ruff check`가 모두 깨끗하다.
`docs/tasks.md`에 남길 후속 항목은 없다 — 두 리뷰의 Critical/High/Medium findings는
모두 이 라운드에서 해소됐다.

## 2026-09-02 — SQLite WAL 모드 안전 fallback + `save_metric` to_thread 테스트 커버리지 (GM-14 후속 2건 종결)

`docs/tasks.md`에 남아 있던 GM-14 후속 항목 두 개를 모두 처리했다.

**(1) WAL 모드.** `database.py`의 `connect` 리스너는 지금까지 `busy_timeout`
PRAGMA만 걸고 WAL은 개발 체크아웃이 drvfs/9p 위에 있을 수 있다는 이유로 켜지
않았다. 이번에 두 번째 `connect` 리스너 `_set_sqlite_wal_mode`를 추가해 매
연결마다 `PRAGMA journal_mode=WAL`을 시도한 뒤, 그 성패를 예외 여부가 아니라
`PRAGMA journal_mode` 재조회 값으로 판정한다 — SQLite가 WAL 전환이 안 될 때
항상 예외를 던지는 게 아니라 조용히 이전 모드를 유지하는 경우가 있기 때문이다
(실측 확인: `:memory:` DB가 정확히 이 경우이며, 예외 없이 항상 `"memory"`
모드를 유지한다 — 이번 fallback 경로 테스트의 결정적이고 항상 재현 가능한
재료로 그대로 썼다). 실제로 WAL이 안 된 경우 경고를 한 번만 남기고(매 연결마다
스팸하지 않도록 모듈 전역 플래그로 억제) rollback-journal 모드로 계속 진행하며,
`busy_timeout`은 이 성패와 무관하게 항상 걸린다. `PRAGMA` 실행 자체가
`OSError`/`sqlite3.OperationalError`를 던지는 경우도 같은 fallback 경로로
처리한다. `test_database.py`에 기존 `busy_timeout` 3계층 테스트와 같은 구조로
7개 테스트를 추가했다: 일반 파일 기반 연결에서의 WAL 성공, `:memory:` 기반
fallback(경고 로그 1건 포함), PRAGMA 자체가 예외를 던지는 경로(dbapi 커넥션을
흉내 낸 얇은 래퍼로 재현 — `sqlite3.Connection`은 내장 메서드가 read-only
속성이라 인스턴스에 직접 monkeypatch할 수 없었다), 경고가 여러 연결에 걸쳐도
한 번만 남는지, 성공/fallback 양쪽에서 `busy_timeout`이 그대로 유지되는지,
그리고 `event.contains(...)`로 실제 엔진에 올바른 이벤트로 배선됐는지까지
확인한다. 새 리스너를 통째로 no-op으로 되돌려 5개 테스트가 실제로 실패하는
것을 먼저 확인했고, "경고 once" 가드만 좁게 제거해도 해당 테스트 하나가
정확히 그 이유로 실패하는 것도 별도로 확인한 뒤 복원해 재통과시켰다.

**(2) `save_metric` to_thread 커버리지.** `metrics_collector.py`의
`collect_metrics()`가 `metrics_service.save_metric(...)`을
`asyncio.to_thread`로 감싸는 부분(GM-14 원 커밋)에 전용 테스트가 없었다 —
같은 파일의 `cleanup_old_metrics` to_thread 테스트는 `collector._running`을
미리 `False`로 둬서 Docker client mocking이 필요한 `collect_metrics()` 본문을
일부러 건드리지 않기 때문이다. `test_prometheus_metrics.py`에 이미 있던
`_FakeDockerClient`/`_FakeContainer`로 `collect_metrics()`를 end-to-end로 실행하는
새 테스트를 추가하되, 기존 테스트가 쓰던 no-op `save_metric` monkeypatch 대신
`asyncio.get_running_loop()`가 `RuntimeError`를 던지는지로 to_thread 워커
스레드에서 실행됐음을 증명하는 기법(`_collect_loop`의 대응 테스트와 동일 기법)으로
교체했다. `metrics_collector.py`에서 `asyncio.to_thread` 래핑을 일시적으로 제거해
이 새 테스트가 실제로 실패하는 것을 확인한 뒤 복원해 재통과를 확인했다.

전체 backend 테스트(1488 passed, 2 skipped)와 `ruff check`가 모두 깨끗하다.
`docs/tasks.md`의 해당 항목은 완전히 해소돼 제거했다.

## 2026-09-02 — admin-auth-envelope 적대적 리뷰 2인 반영

바로 아래 항목(3d0a740d, `admin.py`/`auth.py` bare-string `HTTPException` 5곳을
`{code, message}` 구조화 봉투로 바꾼 수정)을 독립 적대적 리뷰 2인에게 맡겼다.
둘 다 Critical/High는 없었고 Medium은 한 리뷰에서 하나 나왔다.

**Medium(설명으로 종결, 코드 수정 없음)**: `auth.py`의 다섯 곳 중 RATE_LIMITED/
AUTH_MISCONFIGURED/INVALID_CREDENTIALS 세 곳은 봉투를 구조화해도 `LoginScreen.tsx`가
`humanizeError`/`CODE_MESSAGES`를 아예 부르지 않고 `err.status`만 보고 하드코딩된
문구를 쓰므로 로그인 화면에 관측 가능한 차이가 없다는 지적이다. 실제로 확인해보니
LoginScreen을 그대로 `humanizeError`로 옮기면 **새 회귀**가 생긴다는 것도 함께
드러났다 — 같은 로그인 흐름의 403 분기(`require_frontend_origin`,
`auth_service.py:95`)가 여전히 bare 문자열 `"INVALID_ORIGIN"`이고 `CODE_MESSAGES`에도
없어서, `humanizeError`가 `serverMessage`로 폴백해 지금 잘 나오는 "허용되지 않은
요청입니다..." 대신 원문 토큰을 그대로 보여주게 된다. 이 리팩터는 `require_frontend_origin`
봉투화까지 함께 해야 하는 별도 범위라 이번 라운드에서 하지 않고 `docs/tasks.md`에
후속으로 남겼다.

**Low 2건은 고쳤다.** (1) 두 리뷰 모두 6번째 bare-string `AUTH_MISCONFIGURED`
자리를 찾았다 — `auth_service.py`의 `_require_session_secret()`(`create_admin_session`
전용, 지금은 `verify_admin_password`가 로그인 경로에서 먼저 같은 조건을 걸러내
도달하지 않지만, 두 검사 사이 TOCTOU 창이나 `create_admin_session`의 다른 호출부가
생기면 노출된다). 같은 `{code, message}` 봉투로 바꾸고, 이 함수를 직접 호출해
검증하는 회귀 테스트를 추가했다(route 테스트로는 도달하지 않는 지점이라 unit 레벨
호출이 필요했다). (2) `frontend/src/lib/errors.ts`의 `INVALID_CREDENTIALS`
문구가 "현재 비밀번호가 일치하지 않습니다"로 비밀번호-변경 폼 전용 문구였는데, 이제
`auth.py`의 일반 로그인 실패도 같은 코드를 탄다 — `humanizeError`는 코드 매핑이
서버 메시지보다 우선이라, 이 좁은 문구가 로그인 실패에도 그대로 뜰 여지가 생겼다.
"현재"를 빼고 두 문맥 모두에서 참인 중립적 문구로 통일했다.

두 수정 모두 MUTATION-TEST-TEMP로 되돌려 새 테스트가 실제로 실패하는 것을 먼저
확인한 뒤 복원해 재통과를 확인했다.

## 2026-09-02 — 관측자의 딸꾹질이 후보를 태우던 경로를 닫는다

M05 격리 launcher는 driver 실행 **전**에는 registry 관측 함수의 exit status를 살려
`exit 1`한다. 그런데 **후**에는 `|| true`로 그 신호를 버렸다. 그러면 일시적 실패
(fork 실패·ENOMEM·venv 경합)에서 `receipt_validation_status`가 초기값 1 —
"receipt가 아예 없다"와 같은 값 — 에 머물러 모든 분기를 통과하고
`pin block-execution`에 도달한다. **receipt를 읽어보지도 않고 태운다.** 회전이
안 났으므로 CLI의 `current_matches`가 참이라 소각이 실제로 성공한다.

첫 수정은 그 자리를 `exit 1`(안 태움)로 바꾼 것이었고, 적대 리뷰가 그걸 blocker로
잡았다 — "receipt를 읽지도 않고 태운다"를 "**receipt를 읽지도 않고 안 태운다**"로
바꿨을 뿐이라는 것이다. driver가 본문 진입 뒤 하드 크래시하고 같은 원인으로 관측도
실패하는(강하게 상관된) 창에서는 원장에 block이 없고 `pin verify`가 실행 가능을
보고하며 원장 claim은 재시도를 허용하므로, 운영자가 문서대로 재실행하면 **acceptance
본문이 두 번 돈다**. 소각은 낭비이기 이전에 one-shot 불변식의 집행 수단이었다.

최종 형태: 관측 실패는 snapshot 등가 **게이트만** 무효화하고 검증기는 그대로 돈다.
receipt는 바로 옆 root 0600 파일이고 ktdctl 없이 읽히며, Tier 1이 revision·pinset·
execution identity·transaction으로 이미 이 launch에 결박한다. 결박에 실패하면(exit 1)
종전대로 소각으로 떨어진다. 재시도에는 백오프를 넣었고(겨냥한 실패는 수십~수백 ms
창이라 간격 없는 2회는 사실상 1회다) ktdctl 호출에 타임아웃을 걸었다(락을 쥔 채
무기한 대기하면 모든 pin 변경이 봉쇄된다).

함께: fallback 봉투가 receipt와 같은 harness 태그를 달고 tenant의 phase 네임스페이스에
없는 토큰을 발명하던 것을 걷어냈고, `_NETWORK` 정규식 잉여를 제거했다(plan 등가 비교가
이미 더 강하게 대조한다).

이중 선언 후보 7건을 병렬 검증한 결과 6건은 **이미 행동 테스트로 결박**돼 있었다.
이미 결박된 사실에 텍스트 미러를 얹는 것은 이 저장소가 결함으로 보는 과결박이라
추가하지 않았다.

## 2026-09-02 — GM-10 이관 적대적 리뷰 반영: compose_service.py 되돌림

바로 아래 항목(PinVi credential 수정 + GM-10 이관 2건)을 독립 적대적 리뷰
2인에게 맡겼다. 두 리뷰 모두 High/Critical은 없었지만, 한 리뷰가 Medium
하나를 정확히 잡았다: `compose_service.py`의 `_atomic_restore_compose_source`를
정본 `atomic_write_bytes`로 옮기면서, os.replace 뒤 디렉터리 fsync 실패
처리가 strict(uncaught raise)에서 best-effort(무조건 삼킴)로 조용히
바뀌었다. 이 함수의 유일한 호출자 `_recover_persisted_target_runtime`은
`except Exception`으로 그 예외를 잡아 `recovery_succeeded=False`를
보고하는데, 정본의 `fsync_directory`가 그 실패를 삼키면 함수가 정상
반환해 `ComposePostMutationContractError.recovery_succeeded`가 `True`로
오보된다 — 하필 사후 뮤테이션 실패를 다루는 안전망 경로에서, rename의
crash-durability가 실제로는 확인되지 않은 채로다. 같은 세션에서
`legacy_override_retirement.py`/`pinvi_database_role_credentials.py`의
`_write_atomic`을 정확히 이 이유로 이관 대상에서 뺐으면서, 같은 논리가
이 자리에도 적용된다는 것을 놓친 것이었다.

`_atomic_restore_compose_source`를 원래의 strict 인라인 구현(mkstemp+
write+fsync+os.replace+uncaught 디렉터리 fsync)으로 되돌리고, 새 테스트
`test_compose_service_atomic_restore.py`를 추가했다 — `os.fsync`를 감싸
파일 자신의 fsync가 관측된 이후의 디렉터리 fsync만 실패시켜, 그 예외가
propagate되고(호출자가 실패로 판정할 수 있게) rename 자체는 이미 끝나
있음(`compose_path`가 새 payload를 담고 있음)을 함께 확인한다.
mutation-test로 되돌리기 전 버전(정본 경유)이 이 테스트를 통과시키지
못함을 먼저 확인한 뒤 원복해 재통과를 확인했다.

두 리뷰가 공통으로 지적한 Low 하나(`standalone_backup.py`가 정본
`atomic_write_json`으로 옮기며 `ensure_ascii=False`→`True`로 조용히
바뀐 점)는 코드는 그대로 두고 `docs/tasks.md`에 명시적으로 기록했다 —
`BackupManifest.to_json()`의 필드는 오늘 전부 ASCII라 무해하지만, 향후
비-ASCII 필드가 추가되면 인코딩이 달라진다는 사실을 남겨야 한다.

전체 백엔드 테스트(1470 passed, 2 skipped)와 ruff가 모두 통과했다.

## 2026-09-02 — PinVi bootstrap credential 디렉터리 fsync 파괴 버그 수정, atomic-write 프리미티브 후속 이관 2건

`pinvi_bootstrap_credential.py`의 `_fsync_directory_descriptor`가 디렉터리
fsync 실패 시 항상 `DeploymentContractError`를 던지던 결함을 고쳤다.
`create_pinvi_bootstrap_credential`은 credential 파일을 쓰고 fsync·stat
검증까지 이미 성공시킨 **뒤**에 이 함수를 두 번 호출해 디렉터리 항목 교체의
durability만 보강하는데, 여기서 raise하면 바깥 `except BaseException`이
metadata가 non-None임을 보고 이미 올바르게 쓰인 파일을 zeroize+unlink해
버렸다 — durability 실패(디렉터리 fsync)가 correctness(파일 내용)를
파괴하는 형태의 버그였다. `_fsync_directory_descriptor`에 `best_effort`
파라미터를 추가해 해당 두 호출부만 경고 로그 후 계속 진행하도록 낮췄고,
삭제 뒤 디렉터리 fsync를 쓰는 나머지 두 자리(`_remove_empty_transaction_directory`,
`_zeroize_and_unlink`)는 그 시점에 이미 파일이 지워져 있어 파괴할 대상이
없으므로 기존 raise 동작을 그대로 뒀다. 회귀 테스트는 `os.fsync`를 감싸
대상 fd가 디렉터리인지로 구분해, credential 파일 자신의 fsync가 관측된
이후의 디렉터리 fsync만 실패시켜 실제 디렉터리를 건드리지 않고 재현했다.
`best_effort` 제거 상태로 되돌려 실패를 확인한 뒤 원복해 통과를 재확인했다.

이어서 GM-10 후속으로 남아 있던 mkstemp 자리 9곳을 모두 다시 읽고 개별
검토했다. `compose_service.py`의 `_atomic_restore_compose_source`와
`standalone_backup.py`의 `_atomic_write_bytes`/`_atomic_write_json` 2곳은
os.replace 기반의 평범한 발행이라 정본 `atomic_write_bytes`/`atomic_write_json`으로
옮겼다. 문서화되지 않았던 10번째 자리 `runtime_pin_request.py`의
`_atomic_write_json`도 grep으로 발견해 함께 이관했다(그 파일의 인라인
`_fsync_directory`가 이미 best-effort였어 동작 변화 없음; 이관 전
`replace_existing=True` 경로가 어떤 테스트에서도 실행되지 않는다는 것을
발견해 회귀 테스트를 추가했다). 나머지 7곳은 각각 os.replace 직전/후
identity 재검사, os.link(hardlink) 발행, 또는 디렉터리 fsync 실패를 별도
durability-uncertain 에러로 승격하는 의도된 strict 계약을 갖고 있어 정본
시그니처에 맞지 않는다고 판단해 그대로 뒀다 — 각 사유를 `docs/tasks.md`에
정리했다. 전체 백엔드 테스트(1469 passed, 2 skipped)와 ruff가 모두
통과했다.

## 2026-09-01 — GM 트랙 GM-11~GM-20 순차 이행 (P1 7건에 이어 P2 10건 중 9건 완료)

GM 트랙 개시 뒤 P1 7건(GM-01~GM-07)과 P2 3건(GM-08~GM-10)에 이어, 나머지 P2
10건(GM-11~GM-20)을 우선순위 순으로 순차 이행했다. 각 태스크는 구현 →
전문 적대 리뷰 2인(독립, 병렬) → 발견 반영 → mutation 검증 → 커밋/푸시의
동일한 주기를 거쳤고, 매 커밋 전 시크릿 스캔·rebase 확인을 거쳤다. GM-17만
오너 결정으로 범위를 축소했다 — 나머지는 전건 완료.

- **GM-11**: `docker-targets.yml` 스키마 사전 검증(`yaml_strict.py`)을 신설해
  오타 하나로 CLI/API 전체가 raw `KeyError`로 죽던 결함을 없앴다.
- **GM-12**: `main.py`에 예외 핸들러 3종을 모아 API 오류 envelope를
  통일했다. 기존 ~20곳의 평문 문자열 단언을 보존하기 위해 base
  `DeploymentContractError`의 `detail`은 dict로 바꾸지 않는 제약을
  확정했다(이후 GM-16의 `request_id`도 이 제약을 지켜 형제 키로 추가).
- **GM-13**: manifest 하나의 손상·형식 위반이 백업 목록 전체를 409로
  지우던 것을 개별 `{"state": "unreadable"}` 행으로 격리하고, 재기동 후
  이중 `pg_dump`를 막는 가드를 추가했다.
- **GM-14**: `post_backup`의 동기 감사 기록과 `metrics_collector`의
  `cleanup_old_metrics`/`save_metric`을 `asyncio.to_thread`로 내려 event
  loop 전체 정지를 막았다. SQLite `busy_timeout` PRAGMA도 추가.
- **GM-15**: WebSocket 상태 broadcast에 3초 send timeout을 걸어, 느린
  소켓 하나가 모든 탭의 갱신을 무기한 막던 결함을 제거했다.
- **GM-16**: 이중 로깅(패키지+root 핸들러 중복 부착)을 제거하고 요청
  상관관계 ID(`X-Request-ID`)를 신설해 UI 오류·서버 로그·감사 행을
  하나로 이을 수 있게 했다. 리뷰가 감사 로그 주입 키가 `RuntimePinRequest`
  자체의 도메인 `request_id`를 덮어쓰던 결함을 잡아 `http_request_id`로
  분리했다.
- **GM-17**(부분): 원안은 Map/PinVi 14개 서비스 존재 강제 완화와 bind
  allowlist의 root-owned 설정 외부화였으나, production 보안 경계를 직접
  건드리는 effort L 작업이라 오너에게 범위를 물었다. 재확인 과정에서
  "안전한" `docker-compose.yml` 개인 경로 제거조차 로컬/n150 prod가
  하드코딩 기본값에 의존 중임을 발견해 다시 보고했고, 오너는 최종적으로
  `.env.example` 플레이스홀더 정리만 승인했다. 본작업은 `docs/tasks.md`에
  후속 항목으로 남아 있다.
- **GM-18**: 백업 role(`BACKUP_ROLES`)과 pinned pair role
  (`RUNTIME_SOURCE_ROLES`)의 다층 하드코딩을 config 파생으로 정리했다.
  `GET /api/v1/backups`가 이제 `roles` 필드를 실어 보내고 프론트가
  거기서 파생한다. 리뷰가 CLI/API 회귀 테스트의 실제 보호 범위가
  과장돼 있었음을 지적해(정본과 우연히 같은 값의 독립 하드코딩을
  구분 못함) `RUNTIME_SOURCE_ROLES`를 monkeypatch로 바꿔치는 구조적
  테스트를 추가했다.
- **GM-19**: `c6c_deployment.py`·`compose_service.py`의 구세대 C6c 경로
  ~760줄(원안 추정보다 깊었던 4단 map-dataset 검증 서브트리 포함,
  `tarfile.extractall` 보안 민감 죽은 코드 포함)을 삭제했다. 기계적
  line-range 삭제가 데코레이터 경계를 두 번 잘못 잘라 32건 테스트
  실패로 즉시 드러났고 `git diff` 대조로 정확히 복구했다 — 대규모
  삭제 뒤 전체 스위트 검증의 실사례가 됐다. 프론트 더미 의존성
  (`react-hook-form`/`zod`)과 무소비 `port_policy`도 제거했고,
  `require_public_api_key`에 `GET /metrics`의 `KTDM_METRICS_REQUIRE_KEY`
  opt-in 게이트로 첫 실제 소비처를 만들어 `decisions.md`의 관련 open
  항목을 종결했다.
- **GM-20**(서비스 계층 분리 1단계): `services/errors.py`·
  `services/capabilities.py`를 신설해 `DeploymentContractError` 계열과
  mutation capability 센티널을 `c6c_deployment.py`(7,800줄대)에서
  분리하고, 48개 이상 파일의 기존 import를 깨지 않도록 재수출했다.
  `compose_service.py`의 두 transaction 캡처 메서드를 공개로 승격했고
  (lock 선보유 규율은 docstring으로 명시, GM-19의 데코레이터 사고
  직후라 인자 강제보다 저위험 옵션을 택함), `metrics_collector.py`↔
  `docker_service.py`의 순환 import를 콜백 주입으로 끊었다(docker
  client accessor를 `docker_service.py`가 자기 싱글턴 생성 직후
  배선). 후속 c6c_deployment 4분할·compose_service 3분할은 이 태스크
  범위 밖으로 남겼다.

모든 태스크가 mutation 검증(각 회귀 테스트를 원복해 실패를 실측 확인한
뒤 복구)을 거쳤고, 리뷰가 발견한 항목 중 이번 라운드에서 고치지 않은
것(GM-17 본작업, `m05_isolated_harness.py`의 세 번째 pinned role
리터럴 중복, `_resolve_repository_path` 테스트 커버리지 부재 등)은
`docs/tasks.md`에 후속 항목으로 남겼다. 작업 브랜치
`refactor/general-mgmt-improvements`, main에는 아직 머지하지 않았다.

## 2026-09-01 — 범용 관리툴 감사: 72개 발견 → 검증된 태스크 20건 (GM 트랙 개시)

main `9916b33` 기준으로 저장소 전체를 "범용 관리툴" 관점에서 분석했다. 6개 관점(백엔드
핵심 8k줄 파일 2개, pin/CLI 계열, API·관측성, 프론트엔드, 범용성 — 새 타깃 추가 비용·
하드코딩, 운영 흐름 — 설치·백업·진단)을 병렬 분석해 72개 발견을 수집하고, 중복 병합·
우선순위화로 20개 태스크로 정리한 뒤 P1/P2 전건을 적대적 검증자가 코드 라인 단위로
재확인했다. 기각 0건 — 단 13건이 REVISED로 실질적 구현 정정을 받았고(예: GM-01의
"복구 불능"은 과장으로 판정되어 rollback→rotate-pair 이중 명령 복구 경로가 실재함을
확인, 대신 "시스템 자체 안내가 그 복구를 가리키지 않는다"로 재정의), 그 정정은
태스크 문서의 검증 노트로 보존해 구현 시 본문보다 우선하게 했다.

P1 7건의 핵심: 단일 role pin 회전이 v6 execution registry를 stale로 만드는 결함(GM-01),
성능 차트 기간 선택이 WS 프레임 도착 즉시 무효화되는 표시 결함(GM-02), Manager 자신의
systemd 유닛 부재로 재부팅 후 관리면 전체가 다운되는 구조(GM-03), 실행 중 서비스 발밑의
/opt 트리 교체(GM-04), 프록시 뒤 로그인 rate limit의 전역 버킷 붕괴(GM-05), 영어 문자열
비교 기반 예외 분류(GM-06), 백업 복원 CLI 부재(GM-07).

정본은 `docs/general-mgmt-audit.md`. 작업 브랜치 `refactor/general-mgmt-improvements`에서
우선순위 순 순차 이행하고, 의미 단위마다 전문 적대 리뷰 2건, n150 live E2E 가능 항목은
live로, 완료 후 main에 머지한다.

## 2026-09-01 — M05 isolated one-shot의 잠재 결함 3연쇄 제거 (#280~#282)

pair 재핀으로 one-shot이 사상 처음 `pinvi_runtime` 기동까지 도달하자, 0/27
시대에는 도달 불가라 노출된 적 없던 결함들이 차례로 드러났다. 각각 컨테이너
내부 probe/forensic evidence로 실측하고 적대 리뷰 2인(opus·xhigh)을 거쳤다.

- **#280**: driver가 override(app-api의 external Map network join)를 자기
  `_compose`에만 쓰고 docker-app.sh build/up에는 전달하지 않았다 — PinVi
  reconciliation preflight가 startup에서 Map lease를 소비하므로 첫 up이
  결정적으로 실패(내부 probe: Map API로 timeout). PinVi #507의 범용
  `PINVI_DOCKER_COMPOSE_EXTRA_FILE` overlay(admission 맥락+root 0600 한정)와
  짝. 리뷰가 3연쇄를 추가 적발: `networks: !reset`이 정적 ipv4_address를
  삼키는 문제(→`!override`), /29 IPAM 만석, stale pin 무음 우회(→admission
  토큰). lease 이중 소비 위험이 있는 중복 force-recreate도 제거.
- **#281**: `!override`로 정적 IP가 실제 적용되자 하단부터 채우는 동적
  할당(postgres가 .2 선점)과 충돌 — 정적 api/frontend를 subnet 상단
  (hosts[-1]/-2)으로. 리뷰 반영: ipam `gateway:` 명시(가정→계약), 기동 직후
  정적 IP 실적용 inspect 단언(silent-drop 클래스 fail-close), forensic
  evidence path, 규칙 파생 테스트.
- **#282**: host publish 포트 대역(30000-38999)이 kernel ephemeral(실측
  32768-60999)과 겹쳐 `ss -ltn` 통과 후 outbound 선점으로 bind가 확률적으로
  실패(e2e5: rustfs :36464). 20000-29999로 이동 + ephemeral 하한(>29999)을
  런타임 계약으로 검증 + busy-window에서 상한 가드를 실제로 미는 회귀 테스트.

같은 pinset(a30e1544, Map 2845e142/PinVi c402a80b)의 execution은
`pin rebind-execution`으로 새 trusted release에 재결박한다 — rotate/rebuild
불필요(generation 957b730e 유지).

## 2026-08-31 — 최신 kor-travel-map Rail-Workbench UI 정합화

최신 `kor-travel-map` 관리자 화면의 기준을 확인하고, Manager의 인증·API·모달 동작은 유지한 채
접히는 좌측 rail, 그룹형 메뉴, 경계선만 있는 헤더, 통계 띠, 평면 섹션, 조밀한 서비스 원장 표로
화면 구조를 맞췄다. Manager 고유 기능은 실제 존재하는 대시보드 앵커와 운영 도구에만 연결했으며,
색상 토큰은 기존 Ember 오렌지 계통을 유지했다. 실행 중이 아닌 컨테이너의 메트릭은 임의의 0 대신
`—`로 표시한다.

검증: `frontend` `npm run type-check`, `npm run lint`, `npm run build`, `git diff --check` 통과.
미확인: n150 공개 화면 검증은 다음 단계에서 수행한다.

## 2026-08-31 — 적대 리뷰 라운드2: phase-scoped 차단을 무효화하던 3중 결함과 그 주변

라운드1에서 배선한 phase-scoped terminal(감사 I-1)은 **세 지점이 각각 단독으로 원상복구**시키고
있었다. (1) launcher가 blocked receipt의 durable 근거로 **무조건 기록만** 인정해, driver가 남긴
scoped 기록을 무시하고 fallback에서 무조건 차단으로 승격했다(R1-S1). (2) ledger 파일명이
identity-해시 그대로라 scoped 차단 후 재실행이 **O_EXCL 충돌 → `ledger_claim` 무조건 소각**으로
끝났다(R1-S2). (3) 본문 진입 후 인프라형 phase 이름의 helper 실패와 cleanup 실패가 실패 표면을
**scoped로 강등**시켰다(R1-S4/R2-S4 — 반대 방향의 결함: one-shot 위반).

수리: launcher에 `has_any_terminal_execution_block`(identity/pinset/revision 결박만, phase 무관),
`ktdctl pin block-execution --phase`(범용 옵션), ledger 파일명 attempt ordinal(내용은 identity-순수,
상한 32, 재실행 정본 판정은 registry), driver `body_entered` 플래그(본문 진입 후 실패는 phase 이름과
무관하게 무조건 소각, cleanup은 실패 표면을 강등 못 함).

같은 라운드의 나머지: forensic 캡처에 `_RAW_ENV_NAMES` 값 content-scrub(R1-S9 — 크기 제한은 내용
방어가 아니다), missing-receipt parser 2곳 exact 원복 + `forbid_extra=False` 호출부를 AST로 함수
이름 단위 고정(R1-S13/R2-S5 — additive 허용은 payload_sha256이 전 바이트를 결박하는 result 2곳만),
fence 미지필드 테스트를 tautology에서 실경로로 재작성, field-set 교차 게이트 fail-closed 강화(R2-S1),
forensic 기본값을 `bash -x` 트레이스로 행동 검증(R2-S9 — 문구 단언은 주석으로 우회된다).

검증: n150 게이트 124 passed(+root 전용 ledger ordinal 계약은 sudo 직접 실행으로 확인),
KTDM_MAP_CHECKOUT=실제 Map 체크아웃으로 5-사본 field-set 동일성 실측 green.

## 2026-08-31 — 봉인된 baseline digest를 `300` 도달 순간으로 한정하고, 그 너머를 receipt에 넘긴다

ADR-42가 head **값 고정**을 걷어냈지만 더 깊은 곳에 **상태 고정**이 남아 있었다. 봉인된
baseline digest는 `300` 시점의 물리 catalog를 서술하는데, 여러 지점이 **live DB를 그
digest와 exact 대조**한다. Map이 migration을 하나 더하면 새 객체 때문에 반드시 어긋나고,
그 어긋남은 계약 위반이 아니라 **비교 대상이 틀린 것**이다.

Map PostGIS 통합이 이것을 실증했다. 프로덕션 영향은 fresh 설치 실패에 그치지 않는다 —
final permit이 막히면 API/Dagster 컨테이너가 기동을 거부한다.

**이 저장소의 변경.** root result schema를 v2 → v3으로 올리고 `post_head_catalog_sha256` /
`post_head_seed_sha256`을 받는다. 이 parser는 exact field set을 요구하므로 필드 추가가 곧
계약 변경이고, 버전을 올려 그 사실을 드러낸다. permit의 `expected_catalog_sha256`은 head가
baseline root일 때만 봉인값이고 그 너머에서는 finalize 관측값이다.

**적용을 절반만 하고 초록이었다.** 처음에는 permit의 `receipts` 블록 한 곳만 고쳤는데,
같은 성질의 자리가 다섯 남아 있었다. 그중 하나가 특히 나빴다 —
`build_application_final_permit`이 `:1466`에서 자기 출력을 `validate_application_final_permit`에
넣는데, 그 함수가 두 catalog 값을 봉인값에 고정하고 있어 **빌더가 방금 쓴 head 인지 값을
자기 검증에서 거절**했다. head가 baseline root를 넘으면 permit이 아예 만들어지지 않는다.

**테스트가 못 잡은 이유가 더 중요하다.** `test_the_whole_fresh_chain_works_at_any_head`가
head **문자열**만 세 값으로 바꾸고 catalog digest는 전부 봉인값 그대로였다. head가 움직이면
catalog가 달라진다는 사실 — ADR-43이 존재하는 이유 자체 — 를 한 번도 태우지 않았다.
초록인 채로 아무것도 보지 않은 것이다.

fixture가 head > baseline root일 때 실제로 다른 catalog digest를 만들게 고쳤고, 그 상태에서
두 파라미터가 먼저 **실패**하는 것을 확인한 뒤 다섯 자리를 고쳤다.

**결박은 오히려 강해졌다.** head 너머에서는 봉인값 대신 root receipt가 관측한 head 상태와
대조한다(`prior.post_head_catalog_sha256`). 봉인값은 애초에 그 상태를 서술하지 않으므로 잃는
것이 없고, root receipt는 fence → journal → candidate evidence로 결박돼 있다. permit의 두
catalog 값이 **서로** 같아야 한다는 조건도 새로 생겼다.

`300`에서의 엄격함은 하나도 잃지 않는다 — head == baseline root이면 모든 자리가 종전과
동일한 봉인값 대조를 한다. 근거가 옮겨가는 것은 그 너머뿐이다.

기각한 대안은 baseline 재cut이다. 기계적으로는 가능하지만(핀 이미지가 n150에 살아 있어
태그로 보호해 뒀다) baseline은 `0236 → 300` squash 산물인데 head와 뒤섞이고, 무엇보다 **다음
migration에서 또 재cut**을 요구한다 — 같은 덫을 다시 놓는다. ADR-43.

미확인: 실 프로덕션 rebuild. Map PR digitie/kor-travel-map#1125의 PostGIS 통합이 `301` 적용을
확인한다.

## 2026-08-31 — Map application head 값 고정 해제: 리터럴 11사본을 candidate 선언으로

Map이 migration을 **하나**(`301_m03_import_children`) 더하자 Manager가 candidate를 거절했다.
막은 것은 배포 계약이 아니라 `application_head = "300"` 리터럴 열한 사본이었다.

**가장 위험했던 것은 계약 검증이 아니라 환경변수 세 개다.**
`KOR_TRAVEL_MAP_MIGRATION_EXPECTED_HEAD`가 `compose_service.py` 두 곳(candidate 환경,
runtime 환경)과 `scripts/m05_isolated_e2e.py`에서 리터럴이었다. 이 값이 head와 다르면 Map API
컨테이너가 **기동 자체를 거부**한다. 단위 테스트도 CI도 보지 못하는 자리라, 스키마가 하나
진화하는 순간 프로덕션이 조용히 죽는 구성이었다.

**`Application300Contract`에 head를 담을 자리가 없었다.** head는 `from_payload`에서 exact
비교로 검증된 뒤 **버려졌고**, `to_payload`가 상수를 다시 주입했다(왕복 손실, 호출자 0).
그래서 리터럴을 지우는 것만으로는 부족했다 — 계약 객체가 head를 운반할 수단부터 만들어야
나머지 여덟 치환이 값을 받을 수 있었다.

**값은 풀고 결박은 강화했다.** head는 두 독립 출처가 일치할 때만 받는다 — paired receipt의
baseline contract(digest로 receipt·journal까지 결박)와, candidate API image가 network 없이
출력한 installed graph의 head. 후자의 기계(`/usr/local/bin/ktm-application-schema`)는 Map에
**이미 있었고**, `map_dagster_head`/`pinvi_head`가 쓰던 `parse_candidate_static_head`를 그대로
쓴다. 둘이 다르면 거절한다 — 재빌드 없이 receipt를 재사용한 상태이고, 값 고정이 잡아주지
못하던 종류다.

`300`은 `BASELINE_ROOT_REVISION` 하나로 남는다 — `0236 → 300` handoff의 stamp 목적지이자
`forbidden_application_raw_revision`이 가리키는 격리 선언의 값이며, head가 아니다.
`test_baseline_root_stays_pinned`가 반대로 그것이 **움직이지 못하게** 한다.

**게이트는 변이로 확인했다.** runtime 환경 리터럴 재고정(2건 실패) · m05 harness 리터럴
재고정(1건) · 두 출처 합의 제거(1건) · head 문법 검증 제거(6건). `--wait-timeout 300` 예외는
바로 앞 줄을 보고 좁히고, **예외의 폭 자체**를 별도 테스트로 시험한다 — 예외가 넓으면
게이트가 조용히 무의미해지기 때문이다.

리터럴을 지웠다는 것만으로는 유연성의 증명이 아니다. `test_map_application_300.py`가
fence → root 결과 → finalize → final permit **전 구간**을 `300` / `301_m03_import_children` /
`0999_squashed.v2` 세 head로 완주시킨다.

검증: n150 CI-parity `ruff` clean, `1105 passed, 1 skipped`. 남은 2건
(`test_docker_manager_cli`)은 `origin/main`에서 동일하게 실패하는 환경 의존 건이다.

미확인: 실 candidate image로 `ktm-application-schema head`를 돌린 결과, 그리고 `301` 적용
end-to-end rebuild. 둘 다 Map PR 병합 뒤의 별도 실행이다. ADR-42.
## 2026-08-30 — isolated PinVi→Map service 경로를 private bridge로 분리

Map API의 isolated host publish는 root driver의 host-loopback 검증용이다. PinVi API container가
`host.docker.internal` gateway로 이 loopback listener에 붙으면 host network boundary상 도달할 수 없어,
M05 worker startup이 readiness 전에 실패한다. isolated override는 이제 PinVi `app-api`가 기존 default
network를 유지한 채 Map external bridge에도 함께 join하고, Map service base URL은 그 bridge의 fixed private
API address를 사용한다. host publish는 계속 loopback 하나이며, app-web·Dagster나 일반 Manager pin registry에는
Map/PinVi 업무 규칙을 추가하지 않는다. typed runtime expectation도 PinVi API에만 exact Map bridge의
secondary attachment를 허용하고 두 network ID를 모두 대조하며, missing·third attachment는 fail-close한다.
source regression은 host gateway URL 재도입 및 PinVi default network 탈락을 막는다.

## 2026-08-30 — isolated PinVi bootstrap credential의 host-side 전달 정합

isolated driver는 Compose에 private `PINVI_ENV_FILE`을 전달했지만, PinVi `docker-app.sh`의 migration 전
host-side bootstrap validator는 그 파일을 shell에 load하지 않고 현재 process 환경의
`PINVI_BOOTSTRAP_ADMIN_CREDENTIAL_FILE`만 읽는다. 따라서 owner-only absolute credential file을 정상 생성해도
direct `up`이 그 경로를 보지 못해 runtime command가 실패할 수 있었다. Manager는 credential 내용이 아닌
absolute host file path 하나만 direct command 환경에 함께 전달하도록 보정했고, admission tuple의 모든
필수 경계가 실제 child environment에 들어가는 회귀를 추가했다. 이 수정은 PinVi business rule을 generic
registry에 넣지 않으며, private file mode·lifetime·Compose 전달 계약도 바꾸지 않는다.

## 2026-08-30 — root launcher의 명시적 forensic capture

direct command terminal의 기본 receipt는 return code만 보존하므로, 원인 분석이 필요한 root operator는 기존에
환경변수를 직접 전달해야 했다. launcher는 이제 `--forensic-capture`를 root-only argument로 받는다. 명시하지
않으면 inherited capture 환경을 제거해 default raw discard를 유지하고, 명시하면 lock 재실행 경로까지 같은
argument를 전달해 bounded stderr capture가 확실히 유지된다. 이는 특정 consumer failure를 Manager registry에
새로 넣지 않는 일반 one-shot forensic authority 경계다.

## 2026-08-29 — isolated runtime의 전체 host-port 예약을 단일화

M05 isolated driver가 RustFS console port를 storage port의 `+1`로 유도했지만, 예약 표에는 그 값을 별도
항목으로 넣지 않아 다음 서비스의 동적 port와 겹칠 수 있었다. Map RustFS console, PinVi RustFS console과
PinVi wrapper가 기동 전 검사하는 observability endpoint까지 모두 독립 port로 예약하고, 모든 값의 유일성을
회귀로 고정했다. 이는 PinVi 특례가 아니라, 여러 Compose lifecycle을 한 driver가 준비할 때 실제 host publish
집합과 preflight 집합을 같게 만드는 일반 host-port allocation 보정이다.

## 2026-08-29 — isolated PinVi image build를 runtime start 전에 명시

isolated PinVi `docker-app.sh up`은 local API image가 이미 존재한다고 가정한다. pinned source만 materialize한
새 host에서는 이 전제가 성립하지 않아 startup이 image-not-found로 끝났다. Manager는 같은 verified isolated
environment로 `build`를 먼저 수행한 뒤 `up`을 실행하며, 어느 단계가 실패했는지 action별 generic bounded
failure evidence로 구분한다. 이는 일반 build-before-start lifecycle 보정이며 pin/provenance 계약은 그대로다.

## 2026-08-29 — generic direct-command failure forensic boundary

Compose config 이외의 direct child command가 실패하면 fixed `runtime_command_failed`만 남아 root operator도
exit code와 bounded stderr를 확인할 수 없었다. generic failure evidence writer는 이제 raw command·env를
기록하지 않고 return code만 담은 root-only receipt를 남기며, 명시적 forensic opt-in에서만 이미 bounded-drain한
stderr 최대 256 KiB를 별도 `0600` artifact로 보존한다. PinVi isolated startup은 이 공통 경계를 사용하고,
default discard·one-shot terminal·source provenance는 바뀌지 않는다.

## 2026-08-29 — isolated loopback admin 요청의 bridge gateway admission

rendered loopback publish가 정상인데 host root driver의 Map admin subscription 요청이 실패한 candidate를
대조했다. API의 trusted-proxy CIDR에는 frontend와 `127.0.0.1`만 있었지만, Docker published-port
forwarding 요청은 isolated bridge gateway에서 API에 도달한다. network allocator는 이제 gateway를 명시적으로
반환하고, API admin allowlist가 frontend·gateway·loopback 세 `/32`만 허용한다. subnet allocation과
주소 순서, generated CIDR presence를 회귀로 고정했다. 이는 isolated Docker transport admission의 보정이지
Map/PinVi source provenance나 generic pin registry의 업무 규칙 변경이 아니다.

## 2026-08-29 — Compose replacement tag로 isolated loopback publish 보존

root forensic으로 rendered config JSON을 확인한 결과, Compose output format 문제가 아니라 API override의
`ports: !reset`이 기존 publish뿐 아니라 뒤의 replacement list까지 기본값(빈 list)으로 처리하고 있었다.
isolated runtime은 기존 host publish를 정확한 loopback publish 하나로 **교체**해야 하므로 `!override`를
사용한다. frontend의 의도적인 empty-port reset과 다른 의미임을 코드 주석과 source regression으로 고정했다.
이는 Compose merge semantics를 바로잡는 Manager runtime override 보정이며 Map/PinVi source provenance나
generic pin/registry 계약을 바꾸지 않는다.

## 2026-08-29 — Compose config stdout의 bounded streaming admission

Compose config parse failure를 보존하는 추가 경로가 `stdout=PIPE` 전체를 메모리에 읽은 뒤
artifact cap을 적용한다는 전문 적대 리뷰 P1을 보정했다. config stdout은 이제 최대 256 KiB만
streaming capture하고 그 뒤 bytes도 끝까지 drain한다. 상한 초과는 JSON parser에 넘기지 않고
fail-close하며, 기본 marker에는 truncation 여부만 남긴다. root forensic opt-in raw stdout도 같은
이미 capture된 상한 bytes만 별도 `0600` artifact로 쓴다. 이 resource admission은 M05 source
pair와 무관한 일반 외부 CLI 경계이며, oversized-success regression으로 command exit 0 경로까지
고정했다. nonzero exit와 oversized stdout가 함께 오면 exit code evidence와 optional bounded stderr를
우선 보존해 command failure와 successful malformed output의 분리를 유지한다.

## 2026-08-29 — Compose 출력 파싱 실패를 command 실패와 분리

`docker compose config`가 nonzero인 경우와 성공 종료 뒤 JSON contract를 만족하지 않는 경우는 다른
보정 대상이다. rendered topology parser는 이제 후자에도 fixed root-only `compose_config_output` marker를
남기고, root forensic opt-in일 때만 최대 256 KiB의 raw stdout을 별도 `0600` artifact로 보존한다. 기본
운영은 원문을 폐기하며 두 failure 모두 ledger claim 전의 non-consuming preflight로 유지한다.

## 2026-08-29 — Compose config failure와 topology mismatch의 증거 분리

rendered publish preflight는 `docker compose config`가 성공한 뒤의 topology만 검사할 수 있지만,
command 자체가 실패하면 이전에는 같은 fixed phase로 수렴해 topology mismatch와 구분되지 않았다.
preflight는 이제 config command failure에도 return code만 담은 fixed root-only evidence를 남긴다.
원문 stderr는 기본적으로 폐기하며, root operator가 일시적으로 `KTDM_M05_FORENSIC_CAPTURE=1`을 명시한
경우에만 최대 256 KiB의 별도 `0600` forensic artifact로 보존한다. 이 opt-in은 일반 운영 기본값을
바꾸지 않고 one-shot ledger 전 단계에서 configuration failure를 좁히는 진단 경계다.

## 2026-08-29 — terminal 여부와 무관한 일반 execution rebind

trusted Manager release 설치 뒤 current execution이 이전 release를 가리키면 `pin verify`는 stale로
fail-close하지만, 기존 rebind는 current가 terminal인 경우만 허용했다. ledger claim 전 preflight
rejection처럼 runtime mutation을 소비하지 않은 execution도 새 Manager-only implementation으로 이행할
수 없어, source SHA를 불필요하게 바꾸거나 root registry를 수동 편집해야 하는 일반 lifecycle 결함이었다.

`ktdctl pin rebind-execution`은 이제 같은 Map/PinVi source와 **다른** trusted Manager revision이면
current terminal 여부와 무관하게 새 v6 execution identity를 append한다. root 전용 host-global mutation
lock은 계속 유지되어 active rebuild/E2E와 경쟁하지 않으며, old execution·terminal audit은 history와
block list에 보존된다. private registry publish 뒤 public copy write가 실패·중단되면 private current가
already exact target을 보존하므로, 같은 target rebind는 새 identity를 만들지 않고 private/public parity를
idempotent하게 복구한다. 이 동작은 M05에 한정되지 않는 Manager release lifecycle이고, Map/PinVi business
contract를 generic execution registry에 추가하지 않는다.

## 2026-08-29 — preflight publish 불일치의 root-only 안전 증거

rendered Compose preflight는 execution 소비 전 잘못된 publish를 멈추지만, raw config 전체를
cleanup하면 operator는 어느 port field가 달랐는지 다시 추측해야 했다. preflight는 이제 API
service의 ports declaration에서 고정 allowlist인 `host_ip`·`protocol`·`published`·`target`만
정규화한 최대 16개와 expected container/host port만 fixed v1 JSON으로 root-only runtime directory에
남긴다. 알 수 없는 Compose key·`name`·extension·임의 문자열은 절대 보존하지 않고, 유효하지 않은
허용 필드는 `null`로 치환한다. env, 전체 Compose config, container/HTTP output은 포함하지 않는다.
이 안전한 topology evidence는 preflight rejection과 성공 모두에서 다음 보정의 근거가 되며 generic
pin이나 Map/PinVi business contract에는 들어가지 않는다.

## 2026-08-29 — execution 소비 전 rendered loopback publish preflight

runtime inspect에서 loopback publish가 어긋난 것은 정확히 분류했지만, 기존 driver는 source
pair preflight 직후 ledger를 claim했다. 즉 실제 Docker mutation 전에 `docker compose config`로
알 수 있는 malformed publish도 v6 execution 하나를 terminal로 만들 수 있었다.

driver는 private isolated config를 만든 뒤, API service가 정확히 하나의
`127.0.0.1:<dynamic host port> → 13701/tcp` publish를 render하는지 non-mutating Compose config
출력에서 대조하고 그 뒤에만 ledger를 claim한다. 실패는 `preflight_rejected`로 cleanup될 뿐
execution을 block하지 않는다. runtime inspect 검사는 launch 뒤 TOCTOU 방어로 유지한다. 이는
Map/PinVi business contract가 아닌 모든 isolated consumer가 재사용할 수 있는 Manager runtime
topology admission pattern이다.

## 2026-08-29 — 일반 loopback publish readiness를 container health와 분리

isolated one-shot의 Map consumer만 container health 뒤 host loopback health GET을 여섯 번만
재시도해, 이미 일반 Manager smoke가 쓰는 30초 bounded startup 정책보다 짧았다. Container의
healthcheck 성공은 host publish socket의 즉시 수신을 보장하지 않으므로, 이 차이는 M05 업무
계약이 아니라 일반적인 Docker runtime lifecycle 결함이었다.

driver는 HTTP 전에 Docker inspect의 정확한 `127.0.0.1:<host-port>` TCP binding을 검사한다.
따라서 잘못된 bridge/publish topology는 transport timeout으로 잘못 분류되지 않고
`runtime_loopback_publish_invalid`으로 즉시 fail-close한다. binding이 맞으면 body 없는
loopback health GET의 transport 오류만 1초 간격 최대 30회 같은 one-shot 안에서 기다린다.
HTTP status와 JSON 계약 오류는 재시도하지 않는다. 이 policy는 Manager가 보편적으로 관리하는
컨테이너의 readiness 경계이며 Map·PinVi provenance, pin registry, 혹은 M05 business 규칙을
generic primitive에 넣지 않는다.

## 2026-08-29 — 일반 runtime pair 회전의 durable recovery

전문 적대 리뷰가 기존 `rotate-pair`의 P1을 확인했다. host-global mutation lock은 동시
mutation을 막지만 v5 source registry와 v6 execution registry의 private/public 파일 네 개를
한 filesystem rename으로 바꾸지는 못한다. 이전 구현은 v5를 먼저 publish한 뒤 v6 write가
실패하면 stale binding을 남기고, 같은 pair 재시도도 v5 no-op 거부로 막을 수 있었다.

일반 `runtime_pair_rotation` transaction을 추가했다. CLI는 trusted Manager revision과 두
target registry를 먼저 검증·계산하고 root-only 0600 intent에 저장한다. 이후 각 registry의
private/public 사본을 publish하고 모두 성공했을 때만 intent를 fsync 후 삭제한다. 중간 실패는
intent를 남겨 root `pin verify`와 rebuild/E2E mutation gate를 fail-close하며, 같은 source pair와
trusted Manager revision의 `rotate-pair`는 target 전체를 idempotent하게 다시 publish해 수동
파일 편집 없이 복구한다. 다른 target은 미완료 intent를 덮지 못한다. M05는 이 일반 runtime
lifecycle의 consumer일 뿐 transaction schema에는 등장하지 않는다.

후속 재리뷰에서 intent가 남은 사이 다른 root mutation이 execution terminal/rebind audit을
바꾼 뒤 recovery가 과거 target을 다시 쓰는 P1도 발견했다. pending 검사를 Compose나 M05에만
둘 수 없으므로, `ktdctl`의 공통 runtime mutation lock이 기본적으로 intent를 거부하도록
올렸다. `rotate-pair`만 recovery target을 transaction helper에서 exact 대조하므로 예외다.
따라서 init·단일 rotate·block·rollback·generation publish·execution migrate/rebind/block과
대기 요청 적용 모두 pending state를 관측하면 write 전에 멈춘다.

같은 검토에서 partial 실패로 v6 private file 자체가 없을 때 CLI가 legacy host 분기로
떨어지는 P1도 발견했다. pending intent를 v6 존재 여부보다 먼저 읽도록 고쳐, v6 파일이
없는 복구 상태도 반드시 transaction helper의 exact same-target 재개 또는 different-target
거부를 거친다. legacy source-only 회전은 **pending intent가 없고** v6 registry도 없는 host에만
남는다.

## 2026-08-29 — source pair 회전과 v6 execution을 함께 이행

v6 registry가 존재하는 host에서 `rotate-pair`가 v5 source registry만 바꾸면 current execution은
stale이 되고, `migrate-execution-v6`은 existing registry를 거부하며 `rebind-execution`은 source가
달라 거부한다. 즉 manual state edit 없이 새 source pair를 실행할 generic lifecycle이 없었다.

`ktdctl pin rotate-pair`는 이제 같은 host-global mutation lock 안에서 existing v6 registry를 읽고,
새 v5 pair와 trusted Manager revision으로 새 execution binding을 history에 append해 private/public registry를
갱신한다. 다중 registry write는 상단의 durable recovery intent가 완료될 때만 관측 가능 상태가 되며,
기존 terminal audit은 보존하고 새 source execution만 unblocked다. v6 registry가 아직 없는 legacy host는
기존처럼 source pair만 회전한 뒤 explicit migration을 한다. execution registry가 존재하지만 손상된 경우는
migration으로 오인하지 않고 fail-close한다.

## 2026-08-29 — M05 source pair provenance를 terminal 전에 검사

신규 v6 execution candidate는 Map main의 최신 문서 commit을 runtime pair로 회전했지만,
PinVi가 vendoring한 M05 provenance의 `full.source_revision`과 달라 `pair_contract_invalid`로
끝났다. source materialization은 ledger claim 전에 pair를 검사했지만 driver의 final terminal
boundary가 이 pre-ledger failure도 execution terminal로 기록해 잘못된 pair 하나가 새 Manager
execution을 소비했다.

generic runtime pin registry·v6 execution identity에는 Map/PinVi business contract를 넣지 않았다.
대신 M05 integration adapter에 비소비 source-materialization pair preflight를 추가해 launcher가 output leaf·ledger claim
전에 pinned source를 materialize하고 Map/PinVi provenance와 admission contract를 확인한다. 불일치면
fixed 오류만 반환하고 execution terminal·ledger·runtime build·Compose mutation은 만들지 않는다. driver
내부의 동일 검사는 TOCTOU 방어로 유지한다. ledger는 `O_EXCL` create 직후 write/fsync 실패에도 남을 수
있으므로 claim 호출 직전부터 실행권을 소비한 것으로 terminal 처리한다. `preflight_rejected` receipt는
엄격한 pre-claim phase와 `cleanup_failed:false`일 때만 launcher가 fallback 없이 수용한다. 이로써 root `pin verify`의 generic registry/generation
authority와 M05 consumer provenance authority가 섞이지 않는다.

## 2026-08-29 — v5 terminal 감사와 v6 실행 출처를 엄격 분리

v5 terminal에는 Manager revision이 없으므로, 그것을 직전 설치 release의 v6 execution으로
귀속하는 설계는 역사적 provenance를 만들어 내는 P1 오류였다. 해당 transition record와
installer 변경을 폐기했다. v5 registry는 source materialization의 원본 terminal 감사로 변경
없이 남기고, `pin migrate-execution-v6`은 현재 trusted Manager revision의 v6 execution 하나만
생성한다. 따라서 새 execution의 terminal은 실제 v6 one-shot 결과로만 기록된다.

적대 재리뷰에서 v5 source가 미차단일 때 v6 terminal을 건너뛸 수 있는 P1도 발견했다.
`rebuild-pinned`는 이제 source 상태와 무관하게 exact trusted v6 execution을 확인하고, missing·stale·terminal이면
mutation 전에 거부한다. UI preflight는 비-root라 private/public v6 parity를 증명할 수 없으므로 실행 가능
초록불을 내지 않고 root `pin verify`만 요구한다. 이 규칙은 M05에 한정되지 않으며, 기존 v5 audit을
삭제하거나 과거 실행의 출처를 추측하지 않는다.

같은 재리뷰에서 release snapshot을 global mutation lock 앞에서 읽으면 rotation이 그 사이에 끼어 old
source와 new execution을 섞을 수 있는 TOCTOU도 확인했다. pinned rebuild는 이제 global lease를 먼저 잡고
그 안에서 release·v5 registry·trusted Manager·v6 execution을 한 snapshot으로 검사한다. rotate/block/install과의
lock ordering은 `global → pinned`으로 고정했고, 실제 flock 회귀와 ordering 회귀를 추가했다.

## 2026-08-29 — v6 execution registry를 one-shot과 sibling receipt에 연결

일반 runtime execution registry를 actual mutation gate까지 연결했다. `ktdctl pin verify`는
source registry와 trusted Manager-derived execution binding을 함께 확인하며, migration 시 기존 v5
unconditional terminal은 v5 source audit으로만 남긴다. v6 execution은 현재 trusted Manager release에
새로 결박하며 terminal은 실제 v6 one-shot 결과에서만 기록한다. 새 execution은 terminal인 current
binding을 trusted Manager release revision이 달라진 경우에만 rebind할 수 있다.
`block-execution`도 generic root CLI로 제공해 launcher fallback이 임의 pair가 아닌 verified current
execution만 idempotent하게 차단한다.

M05는 이 generic mechanism의 첫 소비자로만 바뀌었다. one-shot ledger, Docker labels, driver result,
private PinVi admission, runtime provenance가 모두 exact execution identity를 포함한다. launcher는 source와
execution snapshot을 두 번 대조하고, old v5 terminal audit 자체가 아니라 current v6 execution terminal을
재실행 거부 근거로 쓴다. 적대 리뷰에서 발견한 inherited global-lock fallback과 public-copy parity도 함께
보정했다. focused identity/registry/harness/driver 회귀는 63 passed, 1 skipped와 Ruff clean이다.

## 2026-08-29 — 일반 runtime execution identity와 trusted rebind 기반

반복 terminal을 Map/PinVi 문서 SHA 변경으로 우회하지 않도록 v5 source pinset과 별도의
v6 runtime execution identity를 도입했다. v5 pinset은 source materialization과 historical
audit의 identity로 그대로 두고, v6 identity는 canonical Manager repository URL·v5 pinset·trusted
installed Manager revision을 SHA-256으로 결박한다. 따라서 같은 source pin에서 Manager implementation만
바뀌면 새 execution이 되지만, 같은 v6 identity는 계속 하나의 terminal lifecycle만 가진다.

새 execution registry와 `ktdctl pin migrate-execution-v6`,
`ktdctl pin show-execution`, `ktdctl pin rebind-execution`을 추가했다. migration은 기존
source registry를 바꾸지 않으며, rebind는 current execution이 terminal이고 trusted installed Manager
revision이 바뀐 경우에만 가능하다. 사용자가 전달한 expected SHA는 trusted
`.ktdm-source-revision`과 `.ktdm-release-manifest.json` 양쪽과의 TOCTOU 확인값일 뿐, 새
실행권을 만드는 입력이 아니다. 일반 registry/CLI는 특정 M05 harness에 종속되지 않고 M05는 첫
consumer로만 남긴다.

순수 identity·registry·CLI parser 회귀는 10 passed, Ruff clean을 통과했다. 다음은 이 identity를
generation/one-shot ledger와 sibling admission/attestation에 연결하는 일이다.

## 2026-08-29 — root 권한 보정 위 M05 재결박 준비

Manager #256을 main의 root 권한·환경 의존성 보정 위로 rebase했다. M05의 runtime pin mutation
직렬화·admission·public generation 계약은 보존했고, 새 head는 이전 trusted installer source와 다른
immutable candidate다.

`9b6eab1e…` exact pair는 clean trusted release, atomic `pin rotate-pair`, 단 한 번의 pinned rebuild와
공개 generation `match` 뒤 isolated M04/M05 E2E를 정확히 한 번 실행했다. 실행 완료 후 공개 `pin verify`가
same pair의 unconditional terminal block을 보였으므로, 이 pinset·source tuple·두 output leaf는 재실행하거나
열지 않는다. 단, 이후 오너의 최신 지시에 따라 raw forensic 열람은 gitignored local analysis에만 상세
기록하며 tracked journal·commit·push에는 넣지 않는다. 공개 상태만으로 특정 component의 결함을 단정하지 않는다.

rebase 뒤 inherited global-lock 회귀가 Windows-mounted pytest 임시 루트에서 POSIX `0600` mode를 보존하지 않아
실행 환경에 따라 실패할 수 있음을 확인했다. 계약 test만 Linux `/tmp` fixture로 옮기고 existing-file mode도
명시적으로 고정했다. runtime 구현은 바꾸지 않았다. focused M05 lock/driver/harness 검증은 54 passed, 1 skipped와
Ruff clean이다.

후속 forensic에서 서로 다른 후보 네 개가 Map container health 뒤 host loopback transport에서 같은 방식으로
종료했음을 확인했다. Compose `--wait`가 container 내부 health를 보장해도 host publish socket의 즉시 수신까지
보장하지 않는 짧은 경합을 같은 immutable candidate 안에서 흡수하도록, `map_health_transport_failed`만 1초 간격
최대 6회 재시도한다. HTTP status·응답 계약 오류는 재시도하지 않는다. terminal `pin verify` 뒤에는 `pin show`
exact block entry의 fixed phase/timestamp만 기록하며, 자유 입력 reason 원문을 자동화 판단에 쓰지 않는다.

## 2026-08-28 — root 권한 축소: 채택 2건, 적대 리뷰로 기각 1건

오너 질문 "root 권한이 필요한 이유는?"의 근거 확인에서 나온 후속 3건 중 2건을 반영하고,
1건은 적대 리뷰에서 기각했다.

**채택 (1) fixed artifact 읽기 경로의 중복 root 조건 제거.**
`_require_fixed_artifact_directory`가 `os.geteuid() != 0`을 검사해
`read_root_read_only_artifact`까지 root를 요구했다. 호출자 3곳을 전수 확인한 결과 쓰기 두
진입점은 각각 독립적으로 root를 검사하고 이 헬퍼 자체는 mutation을 하지 않으므로, 이
조건은 계약을 넓히지 않고 비-root 읽기만 막고 있었다.

**채택 (2) host lease 부팅 시 생성 + flock 획득 뒤 경로 재대조 (ADR-41).**
`/run/lock`은 `1777` sticky tmpfs(n150 실측)라 런타임 최초 생성이 매 부팅 선점 창이었다.
`deploy/tmpfiles.d/`를 추가하고 **설치를 trusted installer가 책임지게** 했다 — 문서에 적힌
`sudo install` 두 줄은 배달된 것이 아니다. 함께, `flock` 획득 뒤 `(st_dev, st_ino)`를
경로와 대조한다. `flock`은 inode 단위라 경로가 갈아끼워지면 두 주체가 각자 상호배제를
얻었다고 믿는다. 같은 파일의 inherited-fd 경로는 이미 같은 대조를 하고 있었다.

**기각 (3) `KTDM_SERVICE_USER` 기반 비-root 백엔드 seam.**
lease 소유자로 root와 선언된 계정을 함께 인정하도록 구현했다가, 머지 전 적대 리뷰에서
NO-GO를 받고 되돌렸다. 직접 확인한 결정적 사실:

- 백엔드 전체에서 `load_dotenv`는 `main.py:30` 한 곳뿐이고 기본 `override=False`라 프로세스
  env가 root 소유 `.env`를 이긴다. `cli.py`는 `.env`를 아예 읽지 않는다. 즉
  `os.environ`에서 읽는 uid 선언은 `euid == euid` 자기 증명이다.
- `sudo`의 `env_reset`이 `KTDM_*`를 벗기므로, 문서대로 설정하면 root 전용 워크플로 4종이
  전부 거부된다.
- 디렉터리 소유자를 넓히면 서비스 계정이 lock 파일을 unlink·재생성할 수 있어 flock 상호
  배제가 깨지고, 같은 디렉터리의 rebuild lease는 계약이 어긋나 root의 rebuild가 부팅 내내
  거부될 수 있다.

교훈은 신뢰 결정의 입력을 프로세스 env에서 읽지 않는다는 것이고, 이 저장소에는 이미 옳은
패턴이 있다(`_capture_c6c_deployment_lock_snapshot`은 root 소유 `.env` 바이트로 identity를
고정한다). 올바른 방향은 소유자를 넓히는 것이 아니라 소유자는 root로 두고 접근만 그룹으로
여는 것이며, `NONROOT-BACKEND` 태스크로 분리했다. ADR-41에 기각 근거를 남겼다.

리뷰가 지적하기 전에 자체 발견해 고친 것 둘도 기록해 둔다: 서비스 계정 모드에서 root가
lease 디렉터리를 먼저 만들면 조용한 잠금이 되는 문제, 기본 모드에서 계약 오류가 raw
`PermissionError`로 퇴화하는 문제. 둘 다 기각된 설계와 함께 사라졌다.

회귀 테스트는 `tests/test_c6c_lock_hardening.py` 8건(경로 재대조, symlink·장치 구분, 획득 중
바꿔치기와 mode 확장)과 fixed artifact 계약 2건이다. 전부 변이 주입으로 실제 검출을 확인했다.

테스트 효력 자체를 전담 리뷰로 돌린 것이 이번 작업에서 가장 값이 컸다. 세 라운드 연속으로
같은 양식의 결함이 나왔다 — 테스트는 있는데 효력이 없는 경우다. (1) 읽기 경로 게이트를
되돌려 넣어도 green(유일한 reader 테스트가 `geteuid`를 `0`으로 고정), (2) symlink 테스트가
*다른* 파일을 가리켜 `lstat`→`stat` 변이에도 green, (3) 이번 diff가 추가한 두 번째
`_validate_c6c_lock_fd`를 지워도 green, 그리고 바꿔치기 테스트가 `unlink`로 `nlink`가 0이
되는 바람에 **다른 검사가 대신 걸려** 통과하고 있었다 — hardlink를 먼저 만들어 `nlink == 1`을
유지시키자 경로 재대조가 유일한 방어가 되고, 그때서야 계약을 고정한다. `match=` 문구도
`"requires root"`로 느슨해 복합 변이(공용 헬퍼에 게이트 추가 + `replace` 게이트 삭제)가
통과하고 있어 전체 문구로 좁혔다.

백엔드 1066 passed / 1 skipped, ruff clean.

## 2026-08-28 — M05 isolated Compose admission P1 보정

전문 보안 적대 리뷰가 PinVi `docker-app.sh`의 isolated Compose 허용이 root UID와 호출자 설정
environment marker만 확인해 Manager `ktdctl` 밖의 mutation을 막지 못하는 P1을 발견했다. terminal
candidate와 raw artifact는 열거나 재실행하지 않았다.

Manager root driver는 private `0700` runtime directory에 `0600` admission을 만들고 exact transaction,
current pinset, Manager·Map·PinVi source revision을 함께 결박한다. PinVi는 이 파일을 no-follow로
검증할 때만 isolated direct Compose를 허용하며 legacy marker는 거부한다. 이 admission은 private
one-shot 입력이므로 generation public copy·manifest v6·journal v8의 공개 schema를 바꾸지 않는다.
새 source는 Map·PinVi CI와 exact-head 전문 적대 리뷰 두 건을 통과한 뒤에만 fresh pair로 쓸 수 있다.

## 2026-08-28 — M05 public generation P1 fail-close 보강

전문 적대 리뷰가 generation 공개 사본의 세 P1을 확인했다. manifest/journal 중 한 파일만
유효해도 committed처럼 보일 수 있었고, `pin verify`가 registry 사본만 검사했으며, custom
public root는 publisher가 경로를 바꿔치기당할 수 있었다. reader는 이제 두 strict raw 문서가
모두 있고 same generation일 때만 관측값을 반환한다. public root·부모·기존 파일은 no-follow
FD에서 소유권·mode·hard-link를 검증하고, publisher의 검사·교체·fsync도 같은 FD에 결박했다.

`pin verify`는 generation strict parse와 registry binding을 함께 보고한다. pair 회전 직후
완전한 이전 committed 또는 registry의 exact unconditional terminal generation은 `pending_rebuild`로
구별하되 current로 승격하지 않고, partial·malformed·phase-scoped block·drift는 exit 1이다.
`pin publish-generation`도 current pair `match`까지
재검증한다. terminal 대응 문서는 atomic `pin rotate-pair`로 정정했다.

같은 리뷰에서 preflight가 registry만 보고 stale·partial generation에도 재구축 명령을 안내할 수
있는 경계를 확인했다. preflight도 public generation의 `match` 또는 strict `pending_rebuild`를
필수로 읽으며, 그 외 status/binding은 `GENERATION_UNVERIFIED` fail-close와 `ktdctl pin verify`
안내로 수렴한다.

Map PR #1112는 v8 journal의 3개 PinVi role 확장 키를 exact-dict attestation에 추가하고
committed 의미까지 검증한다. 이 교차 저장소 pair가 모두 병합되기 전에는 generation API를
M05 acceptance gate로 사용하지 않는다. 관련 backend 295개는 POSIX `/tmp` 격리에서 통과했고,
terminal artifact나 n150 one-shot은 열거나 재실행하지 않았다.

## 2026-08-28 — KUM-M4 public generation 계약·CLI 수선

`ktdctl-ui-migration.md`의 KUM-M4 완료 표기와 실제 API 표면을 다시 대조했다. 기존에는
`GET /api/v1/runtime-pins`만 있었고, 문서가 요구한
`GET /api/v1/pinned-runtime/generation`은 없었다. backend가 root private state를 직접
읽는 방식은 권한 경계를 무너뜨리므로 채택하지 않았다.

`write_manifest`·`write_rebuild_journal`은 typed model로 검증된 v6/v8 raw JSON만 `0644`
public copy에 원자 복제하고, 이미 존재하는 상태는 root
`ktdctl pin publish-generation --manifest … --journal … --confirm`으로 같은 경로에
발행한다. API는 그 공개 사본만 strict parse해 원문 `manifest`/`journal`과 별도
`summary`·`terminal` envelope 및 current registry와의 `pinset_binding`을 반환한다.
Map의 exact-dict 계약을 보존하기 위해 raw 문서 키·버전은 바꾸지 않았다.

terminal current pinset의 다음 행동도 role별 `pin rotate`가 아니라 atomic
`pin rotate-pair`로 바로잡았다. focused public-copy/API/CLI 검증과 관련 backend 스위트
266개가 `/tmp` 격리에서 통과했다. WSL 공유 temp의 POSIX mode 한계로 기본 pytest capture는
state-file 무결성 테스트를 시작 전에 실패시키므로, 운영과 동일한 POSIX mode를 보장하는
임시 루트로 실행했다.

## 2026-08-28 — M05 `b46743ea…` terminal 보존 후 대기

Map `6bfa47038b439845662f89524531d2ef72374c2a`·PinVi
`340717de33b3672f7da84795626c4302eddd1176`·Manager
`00c33ad79f8e43b01fe543699428701aa9733c67`·pinset
`b46743ea72d86329d9574c21cc445fb9b33fdeaad07a2704a68a91fd7a0a89fe`는 PinVi·Manager CI와 exact-head 전문
적대 리뷰 두 건의 GO, clean trusted release, atomic pair rotation과 registry/public-copy gate 뒤 n150 isolated
M04/M05 launcher를 정확히 한 번 실행했다. launcher의 권위 있는 고정 결과는
`launcher_safe_result_unavailable`이었다. HTTP 원문·컨테이너 로그·환경값·output leaf는 읽거나 보관하지 않았다.

후속 gate는 exact unconditional terminal entry와 public copy를 확인했다. 따라서 이 candidate·source pair·Manager
source·output leaf는 절대 재실행하지 않는다. 사용자 지시에 따라 새 source·pair·pinset 생성이나 후속 n150 실행은 하지
않고 여기까지의 기록을 보존한 채 대기한다.

## 2026-08-28 — M05 `41be91fe…` terminal 보존과 driver receipt 수렴

Map `fa55316d858d95367b6a1ca6f17094408b543afe`·PinVi
`f9fce72fbc6ef73f3ec1700ef76995fdfc068e88`·Manager
`cd8b3054d9f49af88ef6f58e9319343c1453df27`·pinset
`41be91feb62feff039452e23a0d889c3b32c3e97e08c28e86ad0a1068ec8ad67`는 최신 CI와 exact-head 전문 적대
리뷰 두 건의 GO, clean trusted release, atomic pair rotation과 registry/public-copy gate 뒤 n150 isolated
M04/M05 launcher를 정확히 한 번 실행했다. launcher exit은 1이고 권위 있는 고정 결과는
`launcher_safe_result_unavailable`이었다. HTTP 원문·컨테이너 로그·환경값·output leaf는 읽거나 보관하지 않았다.
exact unconditional terminal entry와 public copy를 다시 확인했으므로 이 candidate·source pair·Manager source·output
leaf는 재실행하지 않는다.

이번 terminal은 driver가 고정 result를 만들기 전 예상하지 못한 ordinary exception으로 종료할 수 있는 경계를
보였다. driver는 이제 `BaseException`을 제외한 main·cleanup·terminal block의 모든 ordinary exception을
`driver_contract_failed` fixed terminal receipt로 수렴시키며, 원문 exception을 receipt에 기록하지 않는다. 회귀
테스트는 unknown exception·cleanup 오류·terminal block 오류에서도 blocked receipt와 fixed phase가 남고 원문이
없음을 고정한다. 다음 후보는 이 source, 새 Map terminal 기록과 새 PinVi
provenance를 fresh atomic pinset으로 결박하고 CI·source-head 전문 적대 리뷰 두 건을 통과한 경우에만 n150에서
한 번 실행할 수 있다.

## 2026-08-28 — M05 `5512ce12…` launcher safe-result terminal 보존

Map `73150672d26866122e231c085e9beefe81bfd776`·PinVi
`d8dc386dec7a800b83d457e1753b63f51470afc6`·Manager
`c31c8448fcade3ace84b0dbd0682328283ae20b9`·pinset
`5512ce12ca316e10404b9faf60eba8130815a4c7cdb3b91f4d8c80de1805cc8d`는 최신 CI와 exact-head 전문 적대
리뷰 두 건의 GO, clean trusted release, atomic pair rotation과 registry/public-copy gate 뒤 n150 isolated
M04/M05 launcher를 정확히 한 번 실행했다. launcher exit은 1이고 권위 있는 고정 결과는
`launcher_safe_result_unavailable`이었다. HTTP 원문·컨테이너 로그·환경값·output leaf는 읽거나 보관하지 않았다.

후속 gate는 exact Map·PinVi·pinset의 unconditional terminal entry와 public copy를 확인했다. 따라서 이
candidate와 source pair·Manager source·output leaf는 재실행하지 않는다. 다음 후보는 Map `fa55316d…` terminal
기록, 새 PinVi paired provenance, 이 기록을 반영한 새 Manager source를 새 atomic pinset으로 결박하고 CI와 전문
적대 리뷰 두 건을 다시 통과한 경우에만 만들 수 있다.

## 2026-08-28 — M05 launcher snapshot·safe envelope 재검증 보강

safe-result 부재 fallback의 전문 적대 재리뷰에서 두 P1을 확인했다. phase-scoped 차단만
존재할 때 unconditional terminal 판정이 idempotent 처리되어 승격되지 않았고, launcher가
driver 시작 뒤의 current pinset을 block할 수 있었다. 또한 driver stdout/stderr가 상위
호출자에게 남으며 `result.json`의 허용 필드·phase·pinset 검증이 느슨했다.

launcher는 이제 public-copy 검증을 두 번 통과한 Map·PinVi·pinset snapshot을 실행권으로
고정한다. driver의 stdout/stderr는 모두 버리고, 종료 뒤 snapshot이 같고 root-owned
`result.json`이 정확한 schema·source revision·pinset·phase·hash 계약을 만족할 때만
성공 결과로 수용한다. driver의 모든 blocked 결과를 포함한 그 외에는 실행 시작 snapshot의 두 revision을 명시해 terminal block을 쓰고,
그 exact unconditional entry를 다시 확인한 뒤 `launcher-result.json`의 fixed safe envelope만
권위 결과로 남긴다. registry는 phase-scoped entry 위에도 unconditional entry를 추가한다.
그러므로 rotation race가 다른 candidate를 terminal로 만들거나 임의 driver envelope이 새
candidate 성공 근거가 되는 경로가 없다. 이 새 Manager source의 CI와 source-head 전문
적대 리뷰 두 건이 모두 GO가 되기 전에는 n150 실행권이 없다.

## 2026-08-28 — M05 safe-result 부재 terminal과 launcher envelope 보강

Map `f90b7c28ee0a51cc5e2dce7a332e7feef9afe477`·PinVi
`fdff06ba746bf2de198fab075a356f88b9f228c9`·pinset
`fa28a6e7d7ee27b7bb6be6cd6c0a04ffc458cda329beca339a4ce6d038480381`은 최신 CI와 전문 적대
리뷰 두 건의 GO, trusted Manager `b45f54d5…` release, atomic pair rotation과 registry/public-copy 검증 뒤
n150 isolated M04/M05 launcher를 정확히 한 번 실행했다. launcher는 exit 1이었고 허용된 durable safe result는
없었다. HTTP 원문·container log·환경값·output leaf는 읽거나 보관하지 않았다. 후속 `pin verify`는 exact pinset의
terminal 차단을 확인했으므로 이 candidate는 재실행하지 않는다.

다음 launcher는 기존 `result.json`이 root-owned·고정 schema로 유효한 경우에만 driver 종료값을 수용한다. 그렇지
않으면 current pinset을 idempotent terminal block으로 결박하고 `launcher_safe_result_unavailable` fixed envelope를
`result.json` 또는 충돌 없는 `launcher-result.json`에 `0600`으로 남긴다. 원문 stderr·HTTP·컨테이너·환경값은
읽거나 쓰지 않는다. 다음 candidate는 Map `73150672…`·새 PinVi provenance·이 새 Manager source를 atomic pinset으로
결박한 경우에만 만들 수 있다.

## 2026-08-28 — M05 Map health terminal의 원문 없는 다음 진단 범위

Map `bbb29d177…`·PinVi `663e21b4…`·pinset `c700bd2e…`은 trusted Manager `4a6e1b0…` release의 atomic
pair rotation과 registry/public-copy gate 뒤 n150 isolated M04/M05 launcher를 정확히 한 번 실행했고
`map_health_http_failed` terminal로 끝났다. cleanup은 통과했다. HTTP response/body·container log·환경값은
읽거나 보관하지 않았고, driver가 root registry에 조건 없이 차단한 해당 pinset·source pair·Manager source·
output leaf는 재실행하지 않는다.

다음 fresh Manager source는 raw status나 socket detail을 저장하지 않으면서 HTTP status를
`map_health_status_failed`, loopback transport를 `map_health_transport_failed`로 분리한다. 이 고정 enum과
terminal 기록 Map revision·새 PinVi provenance를 fresh atomic pinset으로 결박하고 CI·전문 적대 리뷰·registry gate를
다시 통과한 경우에만 다음 one-shot을 허용한다.

---

## 2026-08-28 — PR backend CI의 TestClient 의존성 고정

새 PR CI의 첫 원격 실행은 애플리케이션 runtime 의존성만 설치해 `fastapi.testclient`가 요구하는 dev 의존성
`httpx` 없이 collection 단계에서 종료됐다. 후속 run은 local Compose v5와 GitHub Compose v2가 기본 bind option을
서로 다르게 JSON에 나타내고, fixture가 개발기 절대 `PINVI_PGDATA` 경로에 우연히 의존함도 발견했다. 이는 M05
실행·pin rotate·n150 mutation 이전의 CI 환경 결함이며 후보 실행 근거가 아니다. CI는 검증한 `httpx==0.28.1`을
pytest·Ruff와 함께 명시 설치하고, contract fixture는 임시 PinVi data directory를 직접 만들며 Compose의 동등한
기본 bind 표현만 허용한다. 새 CI run과 두 전문 적대 리뷰가 green/GO가 되기 전에는 trusted release나 one-shot
admission을 진행하지 않는다.

---

## 2026-08-28 — M05 receipt 상태 계약과 terminal registry binding 보정

전문 data-contract 재리뷰는 두 P1을 확인했다. PinVi detail 계약은 `applied`·`blocked`만 선언하는데,
이전 driver가 `blocked`와 미정의 상태를 polling timeout으로 합쳤다. driver는 이제 `applied`와 유효한
receipt만 성공으로 수용하고 `blocked`는 `m05_pinvi_receipt_blocked`, 나머지는
`m05_pinvi_receipt_invalid`로 즉시 terminal 처리한다.

또한 M05 direct launcher는 이전에 root registry의 unconditional terminal block을 확인하지 않아 다른
Manager revision이 같은 pinset을 다시 ledger claim할 여지가 있었다. admission은 이제 registry의 current
Map·PinVi·pinset 정합과 unconditional block을 ledger·Docker mutation 전에 확인하고, non-success result는
exact current pinset을 phase-scoped가 아닌 root registry unconditional block으로 결박한다. 새 회귀는
두 status와 gate 순서, block의 exact pair·unconditional 성질을 고정한다.

`22563762…`를 포함한 기존 terminal candidate와 output leaf는 계속 재실행하지 않는다. 이 보정, PinVi
trace artifact 보정, 두 전문 적대 재리뷰 및 green CI를 모두 통과한 fresh source pair만 다음 n150 E2E 후보가
된다.

---

## 2026-08-28 — receipt polling transport phase의 timeout 합류 차단

전문 data-contract 재리뷰는 receipt polling이 모든 `_PhaseError`를 재시도한 뒤
`m05_pinvi_receipt_timeout`으로 합쳐, 호출 단계 고정 HTTP 분류 계약을 깨는 P1을 확인했다. 다음 source는
transport·응답 형식 오류를 `m05_pinvi_receipt_http_failed` 등 이미 허용된 fixed phase로 즉시 전파한다.
후속 P1 보정 전 이력으로서, 이 항목의 polling timeout 설명은 최신 `M05 receipt 상태 계약과 terminal registry
binding 보정` 항목으로 대체됐다.

Map `b8d108bd…`·PinVi `50c875f5…`·pinset `22563762…` terminal 및 그 output leaf는 그대로 차단해
재실행하지 않는다. 다음 실행은 Map `bbb29d17…`·PinVi `a06086a4…` pair와 이 새 Manager source의
registry/public-copy gate, 최신 CI, 전문 적대 재리뷰 두 건이 모두 정합한 fresh candidate에서만 허용한다.

같은 검증 누락이 반복되지 않도록 이 저장소에는 backend Ruff·pytest와 frontend type-check·build를 PR마다
실행하는 GitHub Actions CI를 추가한다. backend toolchain은 검증한 `pytest==9.1.1`·`ruff==0.16.4`로
고정하고 action도 reviewed commit으로 고정한다. interpreter로만 호출하는 tracked non-executable shebang
script의 `EXE001`만 제외하며, 나머지 lint rule은 전체 backend/test/script에 적용한다. 이전 PR에 원격 CI
workflow가 없었다는 사실은 성공 근거로 취급하지 않고, 이 workflow가 green인 fresh source만 trusted release
후보가 된다.

---

## 2026-08-28 — M05 runtime HTTP terminal을 고정 enum으로 분리

Map `b8d108bd…`·PinVi `50c875f5…`·pinset `22563762…`의 n150 isolated M04/M05 one-shot은
`runtime_http_failed`로 끝났고 cleanup은 성공했다. 구조화 result의 fixed phase만 읽었으며 HTTP status/body,
socket 원문, container log, 환경값은 읽거나 보관하지 않았다. 해당 pinset은 root registry의 terminal evidence로
즉시 차단돼 재실행하지 않는다.

다음 driver는 loopback HTTP transport failure를 Map health/subscription, PinVi login/M04 fixture, Map approval,
M05 case lookup/decision, PinVi receipt polling의 fixed enum으로만 분리한다. 원문 transport detail은 계속 폐기하고,
새 Manager source와 새 Map/PinVi pair에서만 다음 one-shot을 실행한다.

---

## 2026-08-28 — isolated launcher의 installed-wheel project root 회귀 차단

Map `e6c08e…`·PinVi `932fb140…`·pinset `a3f6a8f3…`의 새 M05 isolated launcher는
trusted release를 올바르게 검증했지만, wheel 안 `python -I` 경로가 개발 checkout의 package 부모 규칙으로
project root를 계산해 runtime registry를 읽기 전 import 단계에서 fail-close했다. Docker·Compose·DB·driver
ledger가 시작되기 전의 fixed preflight failure이며, 해당 pinset은 `launcher_preflight` terminal evidence로
즉시 차단해 재실행하지 않는다.

runtime pin registry는 installed venv의 `sys.prefix`가 trusted `/opt` root와 일치하면 외부 root registry와
public copy를 직접 선택하도록 보정한다. 이 경로를 회귀 테스트로 고정해 `-I` launcher와 일반 CLI entrypoint의
project-root 발견 방식이 다시 어긋나지 않게 한다. 다음 live E2E는 새 Manager source와 새 Map/PinVi pinset에서만
정확히 한 번 실행한다.

---

## 2026-08-28 — M05 pair 회전의 intermediate pinset admission 차단

runtime pin registry의 role별 두 회전은 첫 write 뒤 intermediate Map/PinVi tuple을 만들 수 있었고,
M05 launcher는 source pair 검증보다 one-shot ledger를 먼저 claim했다. terminal seed에서의 단일 role
회전을 거부하고 `pin rotate-pair`가 두 revision을 한 registry replace로 바꾸도록 보정했다. launcher도
source pair를 확인한 뒤에만 ledger를 claim하므로 invalid pair는 candidate 실행권을 소진하지 않는다.

새 regression은 intermediate registry 보존본 부재, terminal seed의 single-role CLI 거부, pair 검증과 ledger
claim의 실행 순서를 고정한다. final pair의 `pin verify`가 성공하기 전에는 n150 E2E를 실행하지 않는다.

---

## 2026-08-28 — 사후 적대 리뷰 2건: 배포된 코드에서 찾은 것

**절차 위반을 먼저 적는다.** 저장소 규율은 "코드 PR은 서로 다른 관점의 전문 적대 리뷰
2건을 통과해야 한다"인데, 1차 리뷰는 M5·M6·M7 세 커밋에만 돌렸고 **그 뒤 작업
(M9·M10·M12·M13·M14, 병합 충돌 해소, 그리고 1차 리뷰 지적을 고친 커밋 `0ac5e5d`
자체)은 리뷰 없이 머지하고 운영에 배포했다.** 전체 테스트·lint·격리 E2E·CI는 전부
통과했지만, 그것들은 **내가 확인할 생각을 한 것**만 확인해 준다. 아래가 그 차이다.

두 리뷰어가 각각 계약·fail-close 관점과 운영·사용성 관점으로 `246680d..208751d`를 봤다.
확인된 지적을 전부 반영했다. 배포 시점에 **살아 있던** 것부터:

- **화면이 그 상황에서 반드시 실패하는 명령을 주고 있었다.** "재시도 금지 세트" 배너는
  현재 pinset이 terminal일 때만 뜨는데, 거기 붙은 명령은 `pin rotate`였다. 병합한
  PR #253이 바로 그 상태에서 단일 role 회전을 거부하도록 만들었으므로, 배너가 뜨는
  **모든** 경우에 그 명령은 exit 2다. 동봉 seed의 현재 pinset이 terminal이라 배포된
  호스트에서 그대로 보이고 있었다. `rotate-pair`로 바꿨다.
- **백업 신선도 배지가 거짓말을 했다.** role을 하나 고르면 필터된 목록으로 계산해서
  나머지 role이 전부 빨간 "백업 없음"이 됐다 — cron이 어젯밤 정상적으로 받아 둔
  백업을 없다고 말하는 셈이고, 그러면 운영자는 빨간 백업 경고를 노이즈로 학습한다.
  필터되지 않은 목록에서 계산한다.
- **끝난 백업이 화면에서 사라졌다.** 재접속 로직이 `running`만 다시 붙였다. 이 패널은
  "브라우저를 닫아도 진행됩니다"라고 말하므로 운영자는 실제로 닫는데, 돌아왔을 때
  실패한 백업이 아무 흔적도 남기지 않았다 — 시작조차 안 한 것과 구별되지 않았다.
- **두 패널이 서버가 쓴 정확한 메시지를 버렸다.** `humanizeError`가 상태 코드 문구를
  서버 메시지보다 우선해서, 비밀번호 오타가 "로그인이 필요합니다. 다시 로그인하세요"로
  나왔다. 운영자는 멀쩡한 비밀번호로 다시 로그인하고 어리둥절해진다. 우선순위를
  **코드 → 서버 메시지 → 상태 문구**로 바꾸고, 누락된 코드 14종을 등록하고, 원문을
  접어서 보여 주는 `InlineError`를 공용 컴포넌트로 만들어 두 패널에 붙였다.
- **미종결 journal에 초록불이 켜졌다.** 재구축 판정이 그 상태를 `ok`로 내고 "아래 명령을
  실행하세요"라고 썼다. 그건 새로 시작하는 것이 아니라 **재개**인데, 그 사실은 회색
  글머리 하나로 `확인 불가` 목록과 똑같은 모양으로 묻혀 있었다. `attention` 상태를
  새로 만들어 초록불과 분리하고, 화면에서도 테두리와 제목으로 갈랐다.
- **경과 시간이 얼어 있었다.** 렌더 시점 계산이라 4시간짜리 작업이 "3초 경과"에
  멈춰 있었다 — 살아 있는지 알려 주는 유일한 숫자가 그것이다. 1초 시계를 붙였다.
- **재기동 뒤에도 "진행 중"이라고 우겼다.** job 조회에 오류 분기가 없어 404를 무시하고
  마지막 상태를 계속 보여 줬다. 기록을 잃었다고 말하고, 실제로 남은 것은 목록이
  정본이라고 알려 준다.

계약 쪽에서 나온 것:

- **`--force`가 멀쩡한 요청을 지웠다.** 오류 **문자열에 "readable"이 있는가**로 판정해서
  hardlink나 group-writable 부모 같은 무결성 실패를 전부 "손상됨"으로 보고 삭제했다.
  무결성은 "이 내용을 믿고 행동해도 되는가"의 문제고 버릴지 말지는 "이것이 요청이기는
  한가"의 문제다 — 내용이 파싱되면 거부하고, 무결성 문제는 고칠 수 있다고 알린다.
  반대로 **끊어진 symlink는 `exists()`가 False라 "지울 것 없음"이었는데** 정작 쓰기는
  계속 실패해 경로가 영구히 잠겼다. 그 경우를 지운다.
- **`--block-previous`가 아무것도 등재하지 않을 수 있었다.** phase 무관 술어를 써서
  phase 한정 항목만 있는 pinset을 이미 terminal로 오인하고 exit 0으로 성공을 보고했다.
  같은 수정이 `block_runtime_pinset`에는 들어갔는데 회전 경로에는 빠져 있었다.
- **공유 그룹 모드가 첫 백업에서 죽었다.** setgid 부모 아래 `mkdir`은 그룹과 setgid
  비트만 상속하고 permission 비트는 umask가 정한다(2770 아래 자식이 2755). 그리고 lock
  파일이 `0600`으로 고정돼 있어 처음 만든 쪽만 열 수 있었다 — UI가 먼저 만들면 cron이
  이후 영원히 `EACCES`를 받고, 그 오류는 `StandaloneBackupError`가 아니라 raw
  `PermissionError`라 CLI가 traceback으로 죽었다.
- `.env` identity에 크기·ctime이 없어 **제자리 수정**을 감지하지 못했다. `create_task`가
  실패하면 `running` 기록이 영구히 남아 그 role이 409만 돌려줬다. restore-plan이 gc와
  경합하면 raw `OSError`가 새어 나갔다.

문구도 고쳤다: `apply-pending`은 이제 `--expect-revision`을 요구하는데 안내 문자열 6곳이
옛 형태였고, journal 확인 명령이 `<COMPOSE_PROJECT_NAME>` placeholder라 붙여넣으면 없는
경로를 조회해 **"journal 없음 = 안전"으로 오독**하게 만들었다(서버가 실제 경로로 만들어
준다). `chmod -R 2770` 안내는 dump 파일까지 group-writable로 만들어 0640 정책과
어긋났다. 감사 행에서 비밀번호 변경이 "로그인"으로 표시돼 설정 오류가 보안 사고처럼
읽혔다.

리뷰가 **깨끗하다고 확인한 것**도 기록해 둔다: M12 추출은 분기 순서까지 순수 기계적이고,
병합은 테스트를 하나도 떨어뜨리지 않았으며, 비밀번호 쓰기 순서와 배타 생성·fd 무결성
검사 자체는 방어된다.

회귀 9건을 추가했다(공유 그룹 mode·lock mode·typed lock 오류·placeholder 없는 복구
명령·hardlink 보존·끊어진 symlink 해소·재개가 초록불이 되지 않음·force_refresh 전달·
사라진 dump의 판정화). backend 1056 passed, n150 격리 E2E 22항목 통과.

---

## 2026-08-28 — n150 운영 배포 (`208751d7`)와, 그 과정에서 드러난 런북 함정

로드맵 잔여 전부(M5·M6·M7·M9·M10·M12·M13 1단계·M14·M18)를 main에 머지하고 n150에
배포했다. PR #253(`rotate-pair`, CI)을 먼저 머지하고 그 위에서 충돌을 해소했으며,
PR #235는 **이미 main에 더 넓은 형태로 들어와 있어** 병합하면 104 커밋을 되돌리므로
닫았다(근거는 PR 코멘트에 남겼다).

배포 자체보다 중요한 것은 배포하려다 발견한 사실이다.

**런북이 백엔드를 배포하지 못하는 절차를 적고 있었다.** `deploy-runbook.local.md` §3은
`backend/src/`를 home 트리로 rsync하고 재기동하라고 했는데, 실행 중인 백엔드는
`/opt/kor-travel-docker-manager`(trusted release)에서 돈다. 그대로 따르면 **아무것도
배포되지 않은 채 재기동이 성공하고 `/health`도 200을 준다** — 배포한 줄 알고 넘어가게
되는 정확한 모양의 함정이다.

실제로 그 함정에 이미 빠져 있었다. 배포 전 상태:

- `/opt` 설치본은 오늘 08:32에 `00c33ad7`로 갱신돼 있었는데,
- **실행 중이던 프로세스는 어제 10:50에 뜬 것**이라 그보다 훨씬 옛 코드를 서비스 중이었고,
- `/api/v1/runtime-pins`를 비롯한 새 route가 전부 404였다(옛 route는 403 — origin
  가드까지 도달하므로, 이 둘의 차이가 "재기동 안 됨"과 "코드 없음"을 갈라 준다).

그래서 `prod-deployment.md` §3의 trusted 설치 경로를 그대로 밟았다: 커밋 전용 clean
체크아웃(`~/ktdm-src-208751d7…`), release 전용 wheelhouse(`wheelhouse-208751d7`),
git blob에서 root-staging 후 SHA-256 대조한 provisioner/installer 실행,
`--expected-source-revision`으로 설치, 그다음 재기동. 설치본 manifest의
`manager_source_revision`이 `208751d788ae…`임을 확인했다. installer가 M5용
`/var/lib/kor-travel-docker-manager-requests`도 `0700 root:root`로 만들었다.

배포 후 검증: `/health` 200, `:12905` 200, 새 route 7종 전부 401(= 존재하고 인증을
요구), origin 없는 요청 403, 세션 없는 요청 401, 틀린 비밀번호 401, 공존하는 다른 next
앱 7개와 `:12815` 200 보존.

런북 §3을 실제 절차로 고치고, 체크리스트에 "설치본 revision이 방금 머지한 커밋인가"와
"새 route가 401인가(404면 재기동 누락)"를 넣었다. **백엔드를 먼저 올린다**는 순서도
적었다 — 프론트를 먼저 배포하면 새 패널이 없는 백엔드로 404를 쏘아 대시보드 대부분이
깨져 보인다.

---

## 2026-08-28 — KUM-M12: 표시 규약을 찾을 수 있는 자리로 옮긴다

`DashboardClient.tsx`가 2,138줄이었다. 그중 라벨 매핑·아이콘 판정·포맷터는 컴포넌트
상태와 아무 관계가 없는 순수 함수인데도 그 안에 묻혀 있었고, `dashboard-ui.md` §2가
"셋 다 DashboardClient.tsx 상단에 있다"고 안내해야 했다 — 규약을 찾으려면 2천 줄짜리
파일을 열어야 한다는 뜻이다.

`lib/containerPresentation.ts`(타입 2종 + `statusLabel`·`roleLabel`·`getStatusConfig`·
`getContainerPresentation`)와 `lib/format.ts`(`formatBytes`·`formatTimestamp`)로 옮겼다.
컴포넌트는 1,968줄이 됐고, 옮긴 심볼이 다시 정의되지 않았다는 것과 죽은 lucide import가
남지 않았다는 것을 이관 스크립트가 확인했다.

**JSX 본문은 일부러 쪼개지 않았다.** 남은 것은 서른 개 가까운 state를 공유하는 하나의
컴포넌트라, 하위 컴포넌트로 뜯으려면 그 state를 전부 prop으로 꿰어야 한다. 순수 리팩터가
행동 변경 위험을 안게 되는 지점이고, 지금 얻는 가독성보다 잃는 것이 크다. 이 판단을
여기 남겨 다음 사람이 같은 계산을 다시 하지 않게 한다.

`dashboard-ui.md` §2는 새 위치를 가리키고, 분기 순서가 우선순위라는 사실
(`geocoder-api`가 `-api`에 먼저 걸리는 함정)을 함께 적었다.

---

## 2026-08-28 — KUM-M13(1단계): 복원을 만들기 전에 "복원할 수 있는가"를 먼저 묻는다

오너 결정에 따라 파괴적 복원은 뒤로 미루고 읽기 전용 `ktdctl db-backup restore-plan`을
먼저 만들었다. 순서를 이렇게 잡은 이유가 있다 — **목록에 백업이 보이는 것과 그 백업으로
실제 복원할 수 있는 것은 다르다.** dump가 잘려 있어도, digest가 manifest와 어긋나도,
live schema revision이 백업 시점과 달라도 백업 목록은 똑같이 보인다. 복원 버튼을 먼저
만들면 그 거짓 안전감 위에 파괴적 명령을 얹게 된다.

계획은 아무것도 바꾸지 않고 답한다: 어느 dump를 쓸지, **digest를 다시 계산해** manifest와
대조한 결과, 크기가 맞는지, live schema revision이 백업 시점과 같은지, 어느 컨테이너가
영향을 받는지. manifest에 적힌 sha256을 그대로 믿었다면 이 점검은 아무것도 검증하지 않는
셈이 됐을 것이다 — 회귀 하나가 "크기는 같고 내용만 바뀐 dump"로 그 경로를 지킨다.

**차단과 참고를 구분한다.** schema revision 불일치는 차단이 아니다 — 복원 자체는 가능하고,
코드가 기대하는 schema보다 과거로 간다는 사실을 알고 결정하는 것이 사람의 몫이다. 읽지
못한 것을 "맞다"로 말하지도 않는다(`LIVE_HEAD_UNKNOWN`). 차단 요인이 있으면 exit 1이라
스크립트 게이트로 쓸 수 있다.

백업 패널은 role을 하나 고르면 이 명령을 복사할 수 있게 준다. "복원은 아직 구현돼 있지
않습니다"라는 문구 옆에 그 차이를 미리 확인할 방법을 두는 것이 요점이다.

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

## 2026-08-05 (T-VN-41-F1E — production 완료)

PR #140을 n150 trusted Manager release로 설치한 뒤 `install-pinned-sources --confirm`은 source authority
transaction을 `committed`로 끝냈다. 이 실행은 root-owned exact detached worktree와 canonical source selection만
수렴했으며 Docker·Compose·DB·runtime·image build를 호출하지 않았다.

첫 실행은 JSON success와 `committed` 상태에도 CLI 공통 process-result의 `returncode`가 빠져 shell 종료 코드
`1`을 냈다. 후속 PR #141은 Compose service 경계에서 성공 결과에 `returncode: 0`을 명시하고 그 회귀를 고정했다.
trusted release 갱신 후 같은 product command는 `committed`, `resumed: true`, `returncode: 0`으로 재실행됐으므로
F1E를 완료한다. 다음 작업은 F1D의 one-shot pinned drift bootstrap이다.

---

## 2026-08-05 (T-VN-41-F1E — trusted pinned source-installer 구현·검증)

`ktdctl pinvi-pair install-pinned-sources --confirm`은 trusted installed Manager root와 root EUID를 먼저
확인한 뒤, C6c global lock과 frozen canonical env snapshot 안에서 source authority만 수렴한다. 이 경로는
Compose transaction, Docker SDK/CLI, DB, runtime inspect/recreate, image build를 호출하지 않는다.

source-owner helper만 기존 user-owned checkout의 local canonical origin을 읽고, root는 code-owned canonical
HTTPS URL과 tracked full SHA를 root-owned empty bare staging에만 sanitized fetch한다. 모든 root Git 명령은
hook, file/ext protocol, credential helper와 global/system config를 차단한다. gitlink/submodule은 worktree 생성
전과 crash 뒤 기존 worktree 재사용 경로 모두에서 거부한다.

source root·revision scalar 네 key는 strict dotenv parser로 하나의 owner-preserving atomic replace에서 바뀐다.
private old-env backup과 owner-only journal은 replace 전에 fsync하고, foreign/non-terminal residue는 deploy,
capture, rollback을 막는다. rollback 완료 journal도 original env SHA가 아니면 pair mutation을 허용하지 않는다.

단일 적대적 코드 리뷰의 P1 두 건(submodule 재진입, root worktree hook)을 반영했다. focused 71 passed,
backend 전체 1641 passed(기존 deprecation warning 2건), Ruff 및 strict mypy가 통과했다. 다음 단계는 코드 PR
merge와 n150 trusted release 설치·secret-free production 실행이다.

---

## 2026-08-05 (T-VN-41-F1E — trusted pinned source-installer 설계, issue #138)

F1D candidate가 source authority로 쓸 tracked Map·PinVi commit object는 n150의 current source cache에
없다. 이 cache는 user-owned `0700` Git worktree이므로 root가 그 Git config·hook·remote를 실행해
fetch/clone/archive하는 것은 P0다. canonical HTTPS origin 문자열만 맞아도 `include`, URL rewrite,
custom upload-pack, credential 설정 같은 repository-local 입력을 root가 해석해서는 안 된다.

F1E는 source owner checkout을 read-only origin identity helper input으로만 읽는다. root는 코드에 고정한
canonical HTTPS `RepoSpec`과 tracked release SHA만 사용해 empty bare staging repo를 만들고, sanitized Git
environment에서 exact commit 하나만 fetch한다. commit/tree를 다시 검증한 root-owned immutable detached
worktree가 normal builder의 clean HEAD/`git archive` input이 된다.

source root 두 값뿐 아니라 source-selection revision scalar도 unset-or-pin 조건으로 함께 검증·원자 교체한다.
private `0600` old-env backup과 secret-free durable journal이 env replace 전 fsync되며, crash resume은 old/new
SHA만 인정한다. F1E는 Docker·Compose·DB·runtime·image build를 호출하지 않고, 성공 뒤 F1D가 새 frozen
transaction에서 exact candidate를 build한다.

---

## 2026-08-05 (T-VN-41-F1D — pinned compatible-pair drift bootstrap 설계, issue #136)

F2 fresh v2 diagnostic은 writer stop 전 `writers_fencing`에서 Map API/UI, Map Dagster web/daemon,
PinVi API가 active compatible-pair manifest와 다른 tuple임을 감지해 fail-close했다. 현재 canonical
source cache의 clean HEAD도 tracked cache-target production pin과 다르므로, 일반 `pinvi-pair deploy`와
rollback은 모두 의도대로 거부한다. 이 상태에서 raw Docker·Compose·`.env`로 수렴시키지 않는다.

단일 적대적 설계 리뷰의 P1 세 건을 반영해 일반 deploy 예외 대신 one-shot
`pinvi-pair bootstrap-pinned-drift --confirm` transaction으로 한정한다. source authority는 tracked
Map·PinVi release pin뿐이며 current runtime이나 `.env` HEAD를 candidate/rollback source로 채택하지
않는다. candidate·live Map/PinVi DB head가 동일한 expected head인 경우에만 runtime을 바꾸며, old image static
head drift는 재기동하지 않는 기존 감사 근거로만 남긴다. candidate 실패 시 old image rollback 대신 다섯
runtime을 halt한다. 성공 manifest는 active와 rollback을 동일 candidate로 bootstrap한다.

candidate build 뒤 runtime stop 전 owner-only durable journal에 original manifest SHA, frozen env/Compose
identity, candidate immutable IDs·source revision, DB head와 phase를 기록한다. non-terminal·foreign·손상
journal은 다른 pair mutation을 막으며 동일 candidate resume만 허용한다. `.env` source checkout의 장기
갱신은 별도 trusted source-installer transaction으로 분리한다.

---

## 2026-08-05 (T-VN-41-F1C — legacy pre-stop diagnostic journal 퇴역 완료)

F1B trusted release의 default-off bootstrap과 secret-free contract attestation은 n150에서 성공했다. 이어
새 UUID로 F2 `cache-target diagnose`를 시작하기 직전, Manager는 Docker·DB·runtime mutation 전에 기존
version `1` diagnostic journal을 발견하고 fail-close했다. secret-free metadata로 이 journal은
`writers_fencing`(writer stop 전)이고, window journal은 `rolled_back` terminal이며, 현재 attempt log는
별도 abort-budget 정본임을 확인했다.

수동 state-directory 삭제나 `.env`/Compose 우회 대신, `ktdctl cache-target retire-legacy-diagnostic --confirm`
이라는 단일 제품 경로를 설계했다. command는 root-only state의 exact v1 pre-stop diagnostic 하나만 receipt를
남기고 퇴역시킨다. post-drain/terminal/v2/suspicious state는 recovery를 추측하지 않고 계속 fail-close한다.
attempt log, window, manifest, canonical env, Docker runtime, DB는 변경하지 않아 abort budget과 cutover boundary를
우회하지 않는다. PR #135에서 strict parser·receipt-first crash resume·directory fsync 재시도와 CLI
confirmation 회귀를 보강하고 단일 적대적 리뷰를 통과했다. focused 115 passed, backend 전체 1621 passed,
Ruff와 strict mypy를 통과한 뒤 trusted release를 n150에 설치했다.

`retire-legacy-diagnostic --confirm --json`은 owner-only receipt를 남기고 성공했으며, 같은 command의
재실행도 같은 receipt를 반환해 idempotence를 확인했다. 이후 새 v2 diagnostic은 stale v1 state가 아니라
runtime tuple drift에서 writer stop 전에 fail-close했다. 따라서 F1C는 완료이고 F1D의 Manager-only
drift bootstrap이 다음 작업이다.

---

## 2026-08-05 (T-VN-41-F1B — trusted root canonical env 소유권 결박)

F1A가 merge된 exact trusted Manager release를 n150에 설치하고 bootstrap을 실행했지만, command는
canonical `.env` 교체 전에 `canonical env file is unsafe`로 fail-close했다. read-only metadata에서
파일은 regular·single link·`0600`, app root는 root-owned/non-writable였고, 유일한 불일치는 trusted
installer가 의도대로 보존한 deployment owner UID와 root command의 effective UID였다. 따라서 이 실패는
raw env/Compose나 runtime mutation으로 넘어가지 않았고 container·DB·pair manifest·cutover journal도
바꾸지 않았다.

해결은 수동 `chown`이 아니라 frozen C6c transaction snapshot의 owner UID/GID를 canonical env helper에
explicit expected identity로 전달하는 것이다. replacement는 여전히 root-owned parent, no-follow regular
file, `0600`, single link, expected SHA/identity 재검증과 atomic replace를 강제하고 UID/GID를 보존한다.
직접 호출자가 arbitrary owner를 넣는 CLI/config 표면은 추가하지 않는다. F1B를 별도 reviewable PR로
보강한 뒤에만 F1A bootstrap과 F2 diagnostic을 재개한다.

단일 적대적 리뷰는 bootstrap·일반 enable·window enable 세 경로가 frozen transaction의
`env_file_identity.uid/gid`만 전달하고 receipt/journal SHA까지 다시 결박함을 확인했다. 임의 owner
identity는 CLI/config에 노출되지 않으며, expected identity를 생략한 non-root 호출은 기존 current-EUID
검사를 유지한다. 새 P0/P1/P2는 없었고 backend 전체 suite는 `1605 passed`다. 이후 trusted F1B release를
n150에 설치해 `cache-target bootstrap --confirm --json`과 4-role default-off secret-free attestation을
성공적으로 완료했다.

---

## 2026-08-05 (T-VN-41-F1A — default-off cache-target bootstrap)

F1 Manager production 재pin을 설치한 뒤 F2 `cache-target diagnose`를 read-only로 재시도하기 전,
기존 canonical `.env`에 cache-target 4-role contract와 cache API base가 아예 없음을 확인했다.
이 상태에서는 diagnose가 base URL equality gate에서 fail-close하며, raw Compose 또는 수동 `.env`
편집으로 계약을 보충하는 것은 final boundary의 단일 정본 원칙을 깬다.

따라서 `ktdctl cache-target bootstrap --confirm --json`을 설계·구현한다. 이 command는 C6c global
lock과 frozen env SHA 아래에서 완전 미구성 상태만 받아 네 개의 독립 token·최소 권한 registry·
`sync=false`·production pin을 한 번에 원자 기록한다. 부분 설정/재실행/환경 override는 write 전
거부하고, container·DB·pair manifest·cutover journal에는 손대지 않는다. 출력에는 환경과 role binding의
digest만 포함한다. 이 PR의 적대적 리뷰와 production 설치·secret-free attestation 뒤에만 새
diagnostic ID로 F2를 재개한다.

적대적 리뷰는 dotenv가 허용하는 `export NAME=...` 및 값 없는 선언을 raw `NAME=` 검사로
놓쳐 duplicate key를 append할 수 있는 P2를 찾아냈다. token이 있는 구성은 기존 config gate가
먼저 차단하므로 P1은 아니지만, “하나라도 존재하면 거부” 규칙에는 위배된다. bootstrap은 parser의
key set(값 없음 포함)으로 partial 선언을 판정하도록 보정하고 direct/export/blank 회귀를 추가했다.
보정 diff의 재검토에서는 새 P0/P1/P2가 없었고, backend 전체 suite `1604 passed`를 확인했다.

---

## 2026-08-05 (issue #129 — T-VN-41-F1 production pair re-pin 시작)

T-VN-41 production final boundary를 시작하기 전 Manager tracked manifest가 이전
generation 7 pair를 가리키는 drift를 확인했다. production에 실제 배포된 Map release와 PinVi
release, PinVi가 독립 검증한 service OpenAPI artifact/functional owner, PinVi PR #428의 review
candidate를 GitHub merge provenance 및 n150 배포 receipt로 교차 확인했다. Map release는
`c0afaa4e318a2e2e6d85f53bb889af3e6adec8c1`, functional owner는
`e12494bd5c4b5b2e1d51c72b6ddcf18eead0e53f`, service OpenAPI SHA-256은
`144b4335d98fc021368b3297f5b8ed7b1c560e9850ebbdd8af71e45623ba7b3d`다. PinVi
reviewed candidate `51289cb1651e7771b0ff5c685989a9768d81b870`와 squash release
`3ff54b8b15965c6ecd5c55b1419208e65831c7fe`는 역할이 다르므로 각각 보존한다.

이 값은 Manager manifest, 전체 pin 회귀, runbook에서 동시에 갱신한다. exact contract와
active/rollback pair가 모두 일치하지 않으면 기존 fail-close gate가 mutation 전에 중단한다.
Manager를 production에 배포하고 적대적 리뷰 1건을 통과하기 전에는 F2 diagnose/cutover를
실행하지 않는다.

---

## 2026-08-04 (issue #107 — map_release_revision re-pin `4a764a4f` → `6b537ed9`)

Map PR #929(머지 SHA `6b537ed99aecb583805f3cde2ce7a9fcf8d14329`, MERGED
2026-08-03T08:27:48Z)가 GC receipt `row_counts`에 `quarantine_candidates_before`
preflight 게이트를 추가로 도입했다. 이슈 #107은 현재 pin `4a764a4f`에
알려진 결함이 없다고 명시했고(선택적/저긴급), 이번 변경은 순수 additive라
Manager의 기존 검증기와 호환된다는 점도 함께 확인했다: `row_counts`는
`quarantine_candidates`/`quarantine_collections`/`quarantine_items` 키를
새로 얻지만 migrate/verify receipt 검증은 key set에 대해 loose하고,
`_validate_map_gc_receipt`는 GC receipt 전용이라 영향받지 않는다.
`cache_target_production_manifest.py`의 tracked `map_release_revision`과 당시
cache-target production cutover 문서의 pin 이력 문단을 갱신했다.
`service_openapi_sha256`(PinVi 소유, Map release revision과 무관)은
변경하지 않았다. 백엔드 전체 스위트(1595 passed) 통과 확인.

---

## 2026-08-04 (T-056 완료 — 읽기 전용 백업 이력 API + Web UI 페이지)

fork가 멈춰둔 T-056을 이어받아 적대적 리뷰어 2명을 돌렸다(백엔드 인증/mutation
경계/응답 내용 담당, 프론트 렌더링/build 담당). 백엔드는 confirmed 실공백
없음 — 실제 adversarial 요청(`role=../../etc/passwd` 등)으로 직접 확인했고,
`GET /backups`는 구조적으로 `gc`/create/restore 코드 경로 자체가 없어 이
저장소의 CLI-전용 mutation 권한 경계를 그대로 유지한다. 프론트 리뷰어가
실제 버그를 찾았다: `useQuery`에 `retry: false`가 빠져서 400/409 같은
영구 에러도 TanStack 기본 재시도(~7초)를 다 거친 뒤에야 에러로 표시됐다
— `DashboardClient`의 `auth-me` 쿼리와 같은 이유로 `retry: false`를
추가해 고쳤다.

backend 전체 1595 passed, frontend type-check/lint/build 전부 통과. 이로써
T-053~T-057(백업 생성→목록/GC→복구→내장 통합→읽기 전용 API/UI) 백업/복구
트랙 전체가 완료됐다. 남은 건 T-058 후보(receipt/journal/manifest 스키마
통합 재설계, 별도 설계 필요)와 T-049E(map-ui/map-api revision drift로
중단된 재검증)뿐이다.

---

## 2026-08-04 (T-056 구현 — 읽기 전용 백업 이력 API + Web UI 페이지, 리뷰 대기)

T-053~055가 남긴 standalone DB backup(`ktdctl db-backup create/list/restore`,
CLI 전용)에 읽기 전용 HTTP 표면을 추가했다. `GET /api/v1/backups`(`?role=`
옵션)는 `ComposeService.list_standalone_backups(gc=False)`를 그대로 노출하며,
알 수 없는 role은 400, `DeploymentContractError`는 409로 매핑한다. mutation
(생성·GC·복구)은 이 API에 두지 않는다 — cache-target/pinvi-pair/map-ui-auth와
동일하게 CLI 전용 권한 경계를 그대로 유지한다. 프론트엔드는 `AdminSettingsPanel`과
같은 모달 패턴으로 `BackupHistoryPanel`을 추가해 role 필터·새로고침과 함께
timestamp/role/schema revision/size/sha256/파일명을 표시한다(트리거 UI 없음).

backend 회귀 테스트 6건 추가(세션 인증 필요, 목록 반환, 빈 목록, role 필터,
알 수 없는 role 400, contract 실패 409) — 전체 스위트 1595 passed, ruff/mypy
(신규 코드 범위) 통과. frontend type-check·lint·build(WSL, `next build` 성공,
`✓ Compiled successfully`) 모두 통과.

fork로 구현했으나 fork는 Agent tool로 subagent를 만들 수 없어(T-054/T-055와
같은 제약) 적대적 리뷰어 2명·커밋·PR·병합 전 단계에서 멈췄다. 부모 세션이
리뷰와 이후 단계를 이어받아야 한다.

---

## 2026-08-04 (T-057 완료 + 방향 전환 판단 — 안전한 helper 통합만 반영, 전체 스키마 재설계는 보류)

T-057 진행 중 사용자가 지시를 바꿨다: "호환성, 기존계약 유지보다는 설계적
우월성·최적화·유지보수성을 중점적으로, 대대적인 코드 변경 및 DB schema
변경도 고려." fork에게 즉시 전달했다.

fork는 이미 완료한 안전한 부분(`_write_pg_dump`/`create_standalone_database_backup`의
중복 pg_dump subprocess 로직을 `_stream_pg_dump_custom_format` 공통
헬퍼로 추출, 테스트 1589건 변경 없이 그대로 통과)은 유지하되, receipt/
journal/manifest 스키마 전체를 하나로 재설계하는 더 큰 작업은 **스스로
멈추고 보고**했다 — 이유: 오늘 이미 T-049F에서 journal을 v1→v2로 한 번
바꿨고 n150에 그 v2 상태가 실제로 남아있는데, 같은 세션에서 또 한 번
cutover-critical journal 스키마를 바꾸는 건 리스크가 크니 별도 설계
단계를 먼저 거쳐야 한다는 판단이었다. 이 판단이 타당하다고 보고 사용자에게
확인한 결과, 동의를 받아 안전한 부분만 반영하고 전체 재설계는 별도 태스크
(T-058 후보)로 미뤘다.

적대적 리뷰어 2명(behavior-equivalence 담당, 구조적 건전성 담당) 확인 —
에러 메시지 텍스트·empty-output 검사·OSError 처리·fsync 순서 모두
리팩터링 전후 byte-for-byte 동일, confirmed 실공백 없음.

T-053→T-054→T-055→T-057(안전 범위) 백업/복구 체인이 완료됐다. 남은 건
T-056(읽기 전용 API/Web UI), T-058 후보(스키마 통합 재설계, 별도 설계
필요), T-049E(map-ui/map-api revision drift로 중단된 재검증)다.

`_write_pg_dump`(cutover, idempotent 재사용)와 `create_standalone_database_backup`
(T-053, `O_CREAT|O_EXCL` 원자 선점)이 각자 인라인으로 들고 있던 동일한
`pg_dump --format=custom` subprocess 호출을 `_stream_pg_dump_custom_format`
공유 헬퍼로 뽑아냈다. 파일 생성 전략은 두 함수의 계약이 근본적으로 달라(하나는
재사용 허용, 하나는 재사용 거부) 통합하지 않고 각자 소유하게 두었다 — 실제
pg_dump 실행 부분만 공유한다. 에러 메시지 텍스트는 호출자가 그대로 넘겨
바뀌지 않았음을 확인(패턴매치하는 테스트 없음도 grep으로 확인). 범위는
체크리스트가 명시한 create/verify만 — restore 쪽(T-055)은 건드리지 않았다.

fork로 구현했으나 T-054/T-055와 같은 제약(fork는 Agent tool로 subagent를
만들 수 없음)으로 리뷰 단계 전에 멈췄다 — **아직 커밋·PR·병합 전이다.**
backend 전체 1589 passed(리팩터링 전후 동일 count, 테스트 변경 없음 —
동작 불변의 직접 증거), ruff/mypy clean(touched files) 확인까지만 마쳤다.
부모 세션이 적대적 리뷰어 2명을 돌리고 커밋해야 한다.

---

## 2026-08-04 (T-055 완료 — 안전장치 있는 DB 복구 CLI)

fork가 멈춰둔 T-055(`ktdctl db-backup restore`)를 이어받아 적대적 리뷰어 2명을
돌렸다(confirmation-gate 우회 가능성·role/backup-id 대상 오지정 담당,
stderr-NOTICE 회귀·백업 무결성·복구 후 검증 담당) — 이 세션 전체에서 가장
위험도 높은 명령(실 DB를 파괴적으로 덮어씀)이라 특히 꼼꼼히 봤다. 둘 다
confirmed 실공백 없음: `_STANDALONE_RESTORE_CAPABILITY`는 진짜
module-private singleton이라 CLI `--confirm` 우회 경로가 없고, dropdb는
이 세션 초반 실제 사고로 고쳤던 조건부 패턴을 정확히 재사용했고, role/
backup-id는 파일명·manifest JSON·요청 파라미터 3중 교차검증이라 다른
role의 백업을 잘못 복구할 수 없다. 리뷰어 2가 지적한 사소한 커버리지
공백(pg_restore가 exit 0인데 결과 schema가 틀린 경우의 음성 테스트 부재)만
추가로 메꿨다. backend 전체 1589 passed.

이로써 T-053→T-054→T-055(백업 생성→목록/GC→복구) 체인이 전부 완료됐다.
남은 건 T-057(cache-target cutover 내장 백업을 이 primitive로 통합)과
T-056(읽기 전용 API/Web UI)이다.

---

## 2026-08-04 (T-055: 안전장치 있는 DB 복구 CLI — 리뷰 대기 중, 미병합)

`ktdctl db-backup restore --role ... --backup-id ... --expected-schema-revision ...
--confirm`를 구현했다. T-050의 `--expected-alembic-head` fail-close opt-in 패턴을
그대로 따라, 복구 대상 DB의 **현재** schema revision을 읽어 operator가 명시한
값과 다르면 어떤 mutation도 없이 즉시 거부한다. `--confirm`이 1차 방어(없으면
CLI가 `compose_service`를 아예 호출하지 않음), 새 `_STANDALONE_RESTORE_CAPABILITY`
sentinel이 2차 방어(함수 자체 호출에도 요구)다. 복구 직전 백업 파일을 재-해시해
manifest sha256과 대조하고, dropdb/createdb/pg_restore는 기존
`restore_database_backup`과 동일한 stderr-NOTICE-안전 조건부 dropdb 패턴을
그대로 따랐다.

fork로 구현했으나 T-054와 같은 제약(fork는 Agent tool로 subagent를 만들 수
없음)으로 적대적 리뷰어 2명 단계 전에 멈췄다 — **아직 커밋·PR·병합 전이다.**
회귀 테스트는 추가했고 backend 전체 1578 passed, ruff/mypy clean(touched
files) 확인까지만 마쳤다. 부모 세션이 적대적 리뷰어 2명(confirmation-gate
우회 가능성 담당, 복구 메커니즘 정확성 담당)을 돌리고 커밋해야 한다.

---

## 2026-08-04 (PR #119/T-049F 병합 조정 — T-052 대체, evidence-validation 공백 수정)

사용자 지시로 GitHub PR #119(다른 세션이 만든 것으로 보임 — 이 세션의 T-052(PR #117)가
이미 병합된 뒤에 브랜치됐는데도 완전히 별도 모듈로 같은 issue #115를 다시 구현)를
서브에이전트로 먼저 충돌·중복 여부 분석했다. 결론: 중복이 아니라 **T-052를 대체하는
더 완성도 높은 구현**(Map 자체의 begin/attest/restore lease/receipt 프로토콜, cutover까지
커버) — 코드는 깨끗이 auto-merge됐고 docs만 append 충돌, T-050의 gate 테스트 3건만
v1 journal 픽스처 때문에 깨짐(v2 계약에 맞게 고침).

병합 전 적대적 리뷰어 2명을 추가로 돌렸다(레이스/crash-recovery/rollback-claim 검증
담당, secret 비노출/schema 검증 담당). 리뷰어가 `_validate_phase_evidence`(diagnostics·
window 양쪽)의 실공백을 찾았다: restore receipt만 있고 그 전에 있어야 할 lease/receipt는
없는 불가능한 조합이 phase 문턱 검사로는 안 걸러졌다 — phase 무관 무조건 검사를
추가해 고쳤다. 다른 리뷰어는 crash-recovery 전체가 Map의 `begin` idempotency에
의존한다는 medium 우려를 남겼는데, 이 저장소만으로는 검증 불가능해 fix 없이 열어둔다
(Map 쪽 확인 필요, `docs/tasks-done.md`에 기록).

backend 전체 1580 passed, ruff/mypy clean. T-052는 "대체됨" 표시로 남기고 T-049F를
정본으로 `tasks.md`/`tasks-done.md`에 반영했다.

---

## 2026-08-04 (T-049F: isolated durable writer-drain 완료)

Map-owned lease/receipt chain을 Manager diagnostic·cutover journal에 결선했다. initial fence는
`writers_draining → writers_drained → writers_stopping`을 fsync하며, pre-backup crash는 DB
rollback 없이 Map restore → full writer activation → prior pair re-attestation으로만 복구한다.
backup 뒤 coupled rollback은 Map DB의 drained state를 되돌리는 특성상 `manager_state_restored`
뒤 webserver-only restore receipt를 `writers_restored`로 fsync하고, 그 뒤 daemon 포함 old runtime과
pair attestation을 연다.

단일 적대 리뷰가 발견한 begin JSON null-key·forbidden argv, actual Compose progress stderr, late
Dagster run cancel, pre-backup/superseded diagnostic pair re-attestation 누락을 모두 수정했다.
후속 단일 적대 리뷰에서 `writers_restored` fsync 직후 crash 재개 누락, v1 journal의 불명확한
upgrade 경계, stale pair 대조보다 이른 writer restore, JSON boolean `run_count` 수락을 발견해
수정했다. v1 journal은 compatibility migration 대신 모든 mutation 전에 명시적으로 거부하고
격리 state를 새로 만들도록 고정했다. Manager regression 148건과 actual ephemeral Docker Compose
rehearsal 1건, Map strict command 5건과 isolated PostgreSQL migration/CAS 3건이 통과했다.
production/n150·기존 데이터는 접근하지 않았다.

---

## 2026-08-04 (T-054: 백업 목록/보존 관리 완료)

T-053(`ktdctl db-backup create`)이 남긴 owner-only manifest를 읽는
`ktdctl db-backup list [--role ...] [--json]`과, 파일 목록 기반
age/count 보존 정책(`--gc`)을 구현했다. GC는 `.manifest.json`을 먼저 지우고
`.dump`를 나중에 지워, 중간에 죽어도 다음 조회가 고아 dump(디스크 낭비)만
남기고 절대 깨지지 않게 했다. 손상된 manifest나 dump 유실은 예외로 전체
조회를 막는 대신 `warnings`로 담아 나머지는 계속 보여준다.

fork로 구현했으나 fork는 Agent tool로 subagent(적대적 리뷰어)를 만들 수
없어 리뷰 단계를 완료하지 못하고 uncommitted 상태로 멈췄다 — 부모 세션이
이어받아 적대적 리뷰어 2명(GC 삭제 안전성/race, 목록·출력 정확성)을 돌렸다.
GC 안전성 쪽은 confirmed 실공백 없음(합성 fixture로 직접 재현 검증). 목록/출력
쪽 리뷰어가 **실제 결함**을 찾았다: human-readable 출력에 시각 필드가
빠져 있었는데, 코드를 고치는 대신 `docs/tasks.md`의 요구사항 문구 자체를
구현에 맞춰 조용히 낮춰놓았던 것. `created_at_unix`를 ISO 8601 UTC로 출력에
추가하고 문구도 원복 + 사고 경위를 명시해 고쳤다. 회귀 테스트 1건 추가.
backend 전체 1570 passed, ruff/mypy clean(touched files).

---

## 2026-08-04 (백업/복구 기능 gap 분석 + T-053 독립 DB 백업 CLI)

T-049E n150 재검증 중 `kor-travel-map-ui`/`kor-travel-map-api` revision drift로
막힌 시점에, 사용자가 "전체 구현을 재검토해서 백업 리스토어에 필요한 기능을
찾고 개선할 부분을 찾아 구현 계획을 세우라"고 방향을 크게 틀었다 — Map/PinVi
프로젝트가 명확한 설계 없이 구현되고 있다는 문제의식.

서브에이전트로 전체 조사(backend/CLI/API/Web UI/ADR)를 돌린 결과: **독립적으로
호출 가능한 DB 백업 도구가 전혀 없다**는 게 가장 큰 공백으로 확인됐다. 모든
`pg_dump`는 cache-target cutover window 안에 내장된 private 스텝일 뿐이었고,
이슈 #109 때 운영자가 손으로 `pg_dump`/`psql DROP/RENAME`을 실행해야 했던 게
바로 이 공백 때문이었다. CLI만 유일한 노출 경로였고(API/Web UI는 백업/복구
관련 기능 0건, grep 확인 — 다만 이건 mutation을 CLI 전용으로 유지하는 기존
권한 경계와 일치해서 버그가 아니라 지켜야 할 설계일 가능성이 큼), ADR 37개
어디에도 백업/복구를 1급 기능으로 다룬 곳이 없어 사용자의 진단이 맞았음을
확인했다.

조사 결과를 T-053(백업 생성)→T-054(목록/GC)→T-055(안전장치 있는 복구)→
T-057(cache-target 통합)→T-056(읽기 전용 API/Web UI) 5개 태스크로 tasks.md에
공식 등록하고, T-053부터 순차 구현을 시작했다.

**T-053**: `ktdctl db-backup create --role {map_application,map_dagster,pinvi}`.
cache_target_backup.py의 기존 typed pg_dump/digest/owner-only-storage primitive를
재사용하되 cutover window/journal과 완전히 분리했다. 구현 중 두 실공백을
발견·수정했다: (1) `Path.mkdir(mode=..., parents=True)`가 자동 생성되는 상위
디렉터리에는 `mode`를 적용하지 않아 `~/backups`가 0700이 아니게 될 뻔한 것,
(2) 적대적 리뷰어 1명이 찾은 TOCTOU — 존재-확인 뒤 cutover-window 전용
idempotent-재사용 헬퍼(`_write_pg_dump`)를 그대로 쓰면 동시 재호출이 "거부"
계약을 어기고 조용히 성공할 수 있었던 것 — `O_CREAT|O_EXCL`로 최종 파일명을
원자적으로 선점하는 방식으로 고쳤다. 리뷰어 2(lock 범위·identity 신뢰 모델)는
confirmed 실공백 없음. backend 전체 1553 passed.

다음은 T-054(목록/GC)부터 순서대로 이어간다. T-049E(map-ui/map-api revision
drift로 중단된 상태)는 이 백업/복구 트랙이 끝난 뒤 재개한다.

---

## 2026-08-04 (T-049E 후속: inventory hash canonicalization으로 미해결 사항 해결)

2026-08-03 journal에 미해결로 남겼던 schema/data inventory hash 오탐(pg_dump
dump→restore→dump 비결정성)을 실제로 고쳤다. 데이터는 SQL문 정렬로 순서-무관
비교, 스키마는 source를 scratch와 같은 dump→restore→dump 변환에 한 번 통과시켜
비교하는 방식으로 문제 클래스 전체를 닫았다 — 개별 비결정성 패턴을 regex로
쫓는 대신 근본 메커니즘 자체를 무력화했다. n150 실측(job_ticks 557줄 diff→0,
map_application schema hash 일치)으로 검증했고 적대적 리뷰어 2명이 order-
insensitivity의 오탐지 여부와 canonicalization 철저함을 각각 확인했다.

T-052(durable Dagster writer drain, PR #117)가 먼저 병합된 뒤 이 작업을 이어받아
최신 코드에 재통합했다(stash pop, 충돌 없음, backend 1545 passed). 다음 단계는
실제 n150에서 `ktdctl cache-target diagnose`를 끝까지 돌려 `completed` phase
도달을 확인하는 것 — T-049E의 마지막 남은 검증이다.

---

## 2026-08-04 (T-052: cache-target 진단의 durable Dagster writer drain, issue #115)

issue #115가 요구한 6단계 설계를 그대로 구현했다: `writers_fencing`(순수 preflight)과
`writers_stopping`(실제 전체 writer stop) 사이에 새 `writers_draining` phase를 넣고,
그 안에서 `kor-travel-map-dagster-daemon`만 기존 writer stop/start와 같은
`_COMPATIBLE_PAIR_MUTATION_CAPABILITY`로 먼저 멈춘다. daemon이 멈추면 schedule/sensor가
더 이상 새 run을 못 만들므로, preflight 검사와 실제 stop 사이의 race가 구조적으로
사라진다. 이미 떠 있던 run은 5분 bounded wait으로 기다리고, 그래도 안 끝나면 Dagster
표준 `report_run_canceled` API로만 정식 취소한다(raw GraphQL/run 식별자/credential은
절대 어디에도 남기지 않는다 — count만 계산·비교).

drain 실패는 새 `DiagnosticStage="writer_drain"`/`DiagnosticFailureClass="drain_timeout"`으로
기존 per-role DB stage 실패와 같은 `failure` 튜플·abort-budget reproduced-failure
메커니즘을 그대로 탄다. 모든 종료 경로(성공/drain 실패/이후 단계 실패)는 이미
있던 하나의 `try/finally`에 자연스럽게 편입돼, `_activate_cache_target_writers`
(daemon 포함 전체 재기동)와 `_attest_cache_target_prebootstrap_pair`가 항상
실행된다 — daemon만을 위한 별도 resume 코드가 필요 없었다. `writers_fencing`까지는
attempt budget을 소모하지 않는 #113 불변식은 `_archive_superseded_cache_target_diagnostic`의
기존 판정 로직을 전혀 건드리지 않고도 자동으로 확장됐다(`writers_draining`이
exempt set에 없으므로 crash 시 자동으로 budget을 소모한다) — 코드 변경 없이 회귀
테스트만 추가해 고정했다.

적대적 리뷰어 2명이 검증했다(drain race/timeout/cancel 정책·compatible-pair 보호
미우회, secret/식별자 비노출·모든 종료 경로 원복). 회귀 테스트 11건(단위 7건 + 통합
2건 + #113 확장 1건 + cancel 스크립트 정적 검증 1건) 추가. backend 전체 1533 passed,
ruff/mypy clean(touched files).

n150 실제 재검증(schedule과 맞물린 실제 drain 동작 확인)은 별도 승인 아래 진행한다 —
이슈 #115 자체의 "현재 데이터는 보존 대상이 아니다"라는 임시 운영 결론에 따라
지금 당장 cache-target 진단을 다시 돌리지는 않았다. 이 초안은 Map durable lease/receipt와
crash recovery를 보존하지 못하므로, T-049F가 재작성한다.

## 2026-08-04 (T-051: Map DB naming 정리 착수 + issue #111/#114 결선)

이슈 #109 사고 조사 도중 n150 postgres에 `kor_travel_map`(rev 0036, 오래된 leftover)과
`krtour_map`(rev 0078, 실제 최신 데이터)이 공존한다는 것을 발견했다. 사용자 확인:
`kor_travel_map`이 canonical 이름이 맞고, `krtour_map`은 legacy naming — 오래된
`kor_travel_map`을 DROP하고 `krtour_map`을 `kor_travel_map`으로 RENAME해야 한다.
map_dagster도 동일 패턴(`kor_travel_map_dagster` 80MB 낡음 vs `krtour_map_dagster`
819MB 최신).

같은 조사 중 새 이슈 3건이 열렸다: #111(Map PR #931의 `KOR_TRAVEL_MAP_MIGRATION_EXPECTED_HEAD`
게이트를 Manager compose가 아직 결선 안 함), #114(`KOR_TRAVEL_MAP_KOR_TRAVEL_GEO_API_KEY`
결선 누락으로 map dagster provider ETL 전체가 fail-close), #115(cache-target
writer-drain 관련 대형 신규 기능 — 완료 전까지 진단/rehearsal 중단하라는 임시 운영
결론 포함).

이번 커밋에서 코드/설정 정렬만 반영했다: `docker-compose.yml`·`.env.example`·
`cache_target_backup.py`의 DB 이름 기본값을 `kor_travel_map`/`kor_travel_map_dagster`로
정렬하고, #111·#114 결선을 추가했다. n150의 실제 DROP/RENAME과 재기동은 아직
실행하지 않았다(다음 단계). #115는 범위가 커서 별도 태스크로 분리하고, 지시대로
cache-target 진단/rehearsal(T-049E)은 여기서 중단한다.

---

## 2026-08-04 (T-049E: pre-bootstrap diagnostic attestation 경계 수정)

final cache-target cutover의 사전 조건을 n150에서 다시 확인하는 중, tracked Map·PinVi
release pin은 이미 generation 7 candidate를 가리키지만 현재 compatible-pair manifest는
bootstrap 전 old pair를 가리키는 상태를 확인했다. 이는 정상적인 one-time generation
bootstrap의 출발 상태다. 그러나 `cache-target diagnose`의 writer 재기동 직후 일반
`_attest_cache_target_pair`를 호출해 old pair에도 candidate release pin을 요구하고 있었다.
그 결과 diagnostic은 모든 DB role 검사 뒤에도 fresh receipt를 완료할 수 없고, final
cutover가 요구하는 gate가 스스로 막혔다.

diagnostic 전용 `_attest_cache_target_prebootstrap_pair`를 추가해 old pair에는 manifest,
frozen Compose, runtime readiness·image identity와 secret isolation만 다시 검증하도록
분리했다. receipt identity는 candidate bootstrap에 쓸 canonical transaction으로 유지하고,
old pair attestation에만 그 transaction의 raw Compose·external input에서 old image·source
provenance를 materialize한 frozen transaction을 사용한다. 따라서 현재 candidate를 기준으로
fresh receipt를 만들고 cutover에서 그대로 재검증할 수 있으며, old pair가 새 release로
승인되거나 manifest가 진단 중 바뀌는 경로는 만들지 않는다. tracked release pin은 candidate
build/generation bootstrap과 bootstrap 이후 일반 attestation에 그대로 남긴다.

검토 중 singleton diagnostic journal의 복구 경로도 보강했다. 이전 process가 nonterminal
journal을 남긴 경우 새 UUID가 단순히 거부돼, `aborted` attempt를 기록해도 새 rehearsal을
시작할 제품 인터페이스가 없었다. 새 UUID는 C6c lock 안에서 해당 journal을 typed `aborted`
terminal로 전이하고 attempt record와 대조·기록한 뒤, owner-only archive로 원자 이동한다.
terminal receipt의 archive는 새 UUID가 명시적으로 supersede할 때만 허용하며, 충돌·검증·fsync
실패는 fail-close한다. `PR #108`의 restore/dropdb 보정과는 별개의 production 발견이라 별도
수정으로 관리한다. focused backend 회귀, Ruff 및 strict mypy(변경 서비스)를 다시 확인한다.

같은 흐름의 n150 실행에서 `writers_fencing` 전 quiescence가 0이 아니어 process가 종료한
사례를 확인했다. 이 단계는 writer stop과 DB/runtime mutation보다 앞선 preflight인데도,
crash journal을 회전할 때 full rehearsal과 같은 abort budget을 소모했다. writer fence digest가
없으면 archive만 하고 attempt record를 남기지 않도록 경계를 분리한다. fence digest가 남은
실제 rehearsal은 기존처럼 terminal attempt로 보존한다.

적대적 리뷰에서 digest 부재만으로 무변경을 증명할 수 없다는 P1을 확인했다. `writers_fencing`
phase 안에도 실제 `stop` 호출이 있어 partial stop 또는 crash 뒤 digest 없이 journal이 남을 수
있다. `stop` 직전 `writers_stopping` durable phase를 새로 기록하고, `prepared`/`writers_fencing`
만 preflight로 archive하며 그 이후은 digest 유무와 관계없이 terminal attempt로 보존하도록
정정했다.

---

## 2026-08-04 (T-050: 배포 alembic head 재발 방지 게이트, issue #109)

prod에서 `kor-travel-map-api-latest`의 공개 큐레이션 표면이 0으로 떨어진 사고(issue #109)를
조사했다. 원인은 floating tag `latest-main`(7/31 빌드, alembic head `0072`)으로 컨테이너가
재기동되면서 entrypoint의 무조건 `alembic upgrade head`가 `0063`→`0072`까지만 조용히
올리고, 공개 링크 신뢰도를 복구하는 `0073`이 빠진 것이었다. 컨테이너 `StartedAt`
(`2026-08-03T11:31:35Z`)이 이 세션 자신의 T-049C cache-target 진단 writer 재기동
시각과 정확히 일치해, 원인을 이 세션의 라이브 테스트 작업으로 추적했다. 사용자는
데이터 복구 대신 폐기·재생성을 택했고, 이 작업은 재발 방지만 다뤘다.

두 게이트를 구현했다: (1) `pinvi-pair deploy --expected-alembic-head`로 candidate Map
API 이미지의 alembic head를 DB 접속 없이 정적으로 검사해 mutation 전 fail-close, (2)
cache-target 진단의 writer 재기동에 exact image pair drift 검사를 추가해 read-mostly
작업이 새 candidate를 조용히 활성화하지 못하게 함. 적대적 리뷰어 2명 중 1명이 (1)의
실공백을 찾았다 — 최초 구현이 build 이전 tag를 검사해서 build가 그 태그를 덮어쓰면
검사가 무의미해지는, 사고 자체와 같은 클래스의 결함이었다. build 뒤 immutable image
ID를 검사하도록 고쳤다. (2)는 confirmed 실공백 없음. 회귀 테스트 10건 추가, backend
전체 1515 passed, ruff/mypy clean.

---

## 2026-08-03 (T-049E 재실행: pg_restore timeout 수정 + inventory 해시 비교의 근본 한계 확인)

dropdb NOTICE 수정을 n150에 배포하고 진단을 재실행했다. writer fence·3-role 진단
stage가 정상 진행되어 `scratch_create`는 3개 role 전부 통과했지만, 이번엔 다른
문제 2건이 나왔다.

**1) map_application `scratch_restore` timeout(수정함).** `feature.feature_weather_values`
(1,780만 행) 단일 테이블만으로 pg_restore가 기존 60분 timeout을 넘겼다. n150에서
같은 아카이브를 수동으로 복원해 **끝까지 완주시켜 실측**했다: COPY ~19분,
constraint 검증 ~7분, index 4개(개당 2~10분) — 총 약 97분. archive가 stdin
스트리밍이라 `pg_restore --jobs` 병렬화를 못 쓰는 구조적 제약을 확인했다(파일
기반 seekable archive가 필요). 공유 상수 `_DATABASE_RESTORE_TIMEOUT_SECONDS=10800`
(3시간, 실측 + 여유)으로 3곳(`restore_database_backup`·
`_restore_archive_into_database`·`diagnose_scratch_restore`)의 하드코딩
`timeout=3600`을 교체했다. **이 발견의 무게**: `restore_database_backup`은
실제 production coupled-rollback 경로다 — 즉 실제 T-VN-41 cutover가 롤백을
타야 했다면 이 테이블에서 같은 timeout에 걸렸을 수 있다. 진단 도구가 정확히
설계 의도대로("production을 실제로 만지기 전에 문제를 찾아낸다") 작동한 사례다.
적대적 리뷰어 2명 검증, 회귀 테스트 1건 추가, backend 전체 1497 passed.

**2) map_dagster/pinvi `inventory_mismatch`(의도적으로 미해결).** n150에서 writer를
실제로 멈추고 원인을 격리했다:
- pinvi 스키마: `CHECK (x = ANY (ARRAY[...]::text[]))` 형태 제약이 최초 생성과
  dump→restore→재생성 뒤 **의미는 같지만 텍스트 표현이 다르게**(배열 전체 cast vs
  원소별 cast) 저장되는 PostgreSQL의 알려진 동작. 실제 스키마 손상이 아니다.
- map_dagster 데이터: `event_logs`/`job_ticks`처럼 자주 갱신되는 테이블에서
  `pg_dump --data-only --inserts`의 **행 emission 순서가 두 번의 별도 dump
  호출 사이에 달라짐**을 확인했다 — writer를 완전히 멈춘 상태에서도 재현되어,
  새 쓰기가 아니라 순서 비결정성 자체가 원인임을 확인했다(diff에서 동일 행이
  다른 줄 위치에 나타남, 내용은 100% 동일).

두 사례 모두 이 진단의 schema/data inventory 비교 설계 자체가 "동일 데이터의
pg_dump 출력은 byte-identical"이라는, PostgreSQL이 실제로 보장하지 않는 가정
위에 있다는 걸 보여준다. 코드 한 줄로 안전하게 고칠 범위가 아니라고 판단해
**의도적으로 미해결로 남겼다** — canonicalization이나 순서-무관 비교 같은 별도
설계가 필요하다. docs/tasks.md에 미해결 항목으로 기록.

---

## 2026-08-03 (issue #99 pin 갱신 + T-049E 착수 중 n150 실측으로 dropdb NOTICE 오탐 발견·수정)

issue #99에 Map 쪽에서 실측 확정값과 함께 pin 갱신 요청·PR #925 머지 알림·PR #928 알림이
연달아 왔다. `map_release_revision`을 `d50bb2c5`(결함 포함, PR #925로 확인) →
`0dbbd0b5`(PR #925 반영) → `4a764a4f`(PR #928, 문서 전용)로 순서대로 갱신·병합했다(PR #104,
#105).

pin 갱신 뒤 n150에 실제 배포하고 `ktdctl cache-target diagnose`를 처음으로 production에서
실행했다. 여러 겹의 실제 운영 문제를 순서대로 만나 해결했다:

1. **배포 자체 실패**: 백엔드 재기동이 `pkill` 권한 부족(기존 프로세스가 root 소유)으로
   조용히 실패해 옛 코드가 계속 떠 있었다. sudo로 재기동해 해결.
2. **writer 재기동 부분 실패**: 진단 중 3개 서비스(`map-dagster-daemon`, `map-dagster`,
   `pinvi-api`)가 `Created` 상태로 멈췄다. `docker compose up -d --wait`로 명시적 재기동.
3. **`/tmp` tmpfs 용량 부족**: `source_data_inventory`가 `--inserts --rows-per-insert=1`
   dump를 기본 `/tmp`(7.5GB tmpfs)에 써서 `disk quota exceeded`. `TMPDIR`을 디스크 기반
   경로로 지정해 우회.
4. **stuck Dagster run이 in-flight 체크를 막음**: `feature_price_krex_rest_areas_job`이
   `RESOURCE_INIT_FAILURE`로 계속 재시도 중이었다. 서브에이전트로 근본 원인을 조사해
   `KOR_TRAVEL_MAP_KOR_TRAVEL_GEO_API_KEY`가 `.env`엔 있는데 `docker-compose.yml`에
   연결이 안 돼 있던 것으로 확인, kor-travel-map#930 등록. 진단 동안만
   `map-dagster-daemon`을 잠시 멈추는 방식으로 우회.
5. **`scratch_create`가 3개 role 전부에서 100% 재현 실패**: 진짜 코드 버그였다.
   `dropdb --if-exists`가 대상 부재 시에도 내는 NOTICE를 `_run_checked`가 stderr
   존재만으로 실패 처리. `diagnose_scratch_create`와 sibling
   `_rehearse_database_restore`/`restore_database_backup`(production restore 경로)을
   모두 고쳤다. 적대적 리뷰어 2명이 검증했고 confirmed 실공백 없음. 회귀 6건 추가,
   backend 전체 1496 passed.

T-049 진단 도구가 설계 의도대로 "production을 실제로 만지기 전에 문제를 찾아낸다"는
목적을 그대로 증명한 하루였다 — 5건 모두 코드 병합 전에는 드러나지 않았을 실제 운영/코드
결함이었다.

---

## 2026-08-03 (T-049D: cache-target cutover gate와 window failure propagation 구현)

이슈 #99에 Map 쪽 적대 리뷰 결과가 추가로 달렸다 — 현재 pinned `map_release_revision`
(`d50bb2c5`)에 실제 결함 2건(migrate 재시도 영구 거부 위험, 공개 item 카운트
`source_present` 누락)이 있고 수정은 kor-travel-map PR #925에 있다. 사용자 판단으로
T-049D(pin과 무관한 gate 로직)는 지금 진행하고, pin 갱신은 PR #925 머지 후 별도
처리하기로 했다.

`cache-target cutover`가 T-049C 진단 receipt 없이 새 forward window를 열 수 없게
`_require_fresh_cache_target_diagnostic` gate를 결선하고, pre-forward 실패가
coupled rollback으로 넘어갈 때 window journal에 마지막 안전 phase와 실패 분류를
남기는 `record_window_failure`를 추가했다. 적대적 리뷰어 2명(gate 담당·
failure-propagation 담당)이 같은 strict mypy 실공백(삼항식이 literal이 아닌 str로
추론됨)을 독립적으로 찾아 고쳤고, 그 외에는 실공백이 없었다.

T-049C에서 배운 교훈대로 이번에도 `ruff format` 전체 실행 대신 diff를 무관한
재포맷 없이 최소로 유지했다. backend 전체 1492 passed.

T-049E(n150 production rehearsal)가 마지막 phase다 — 그 전에 Map PR #925 머지와
pin 갱신이 필요하다.

---

## 2026-08-03 (T-049C: cache-target 진단 writer fence·orchestration·abort budget 구현)

이슈 #99에 새 댓글이 달려 실측 확정값과 함께 Map 쪽 문서 PR 머지 시점 조율 요청이
왔지만, T-049C 범위와는 무관하고(cutover-retry 기대값 대조는 T-049D/E) 응답은
사용자 판단으로 보류했다.

`ktdctl cache-target diagnose`를 신설해 T-049A(journal 모델)·T-049B(DB stage
primitive)를 기존 cutover와 같은 C6c 전역 lock·writer-fence 기계 안에 결선했다.
설계 문서 5절의 abort budget(24시간 2회, 재현 실패 시 자동 재시도 대신 aborted)을
새 `DiagnosticAttemptRecord`/`DiagnosticAttemptLog`로 구현했다.

production-critical 코드라 적대적 리뷰어 2명을 병렬로(락/동시성 담당, 데이터
안전성/writer-fence 담당) 투입했고, 겹치지 않는 세 가지 실공백을 각각 찾았다.
가장 심각한 것은 writer stop에 엉뚱한 mutation capability sentinel을 써서
production에서 이 명령 자체가 항상 막혀 있던 것이었다 — 배포 전에 잡혔다는 점이
이 리뷰 단계의 존재 이유를 그대로 보여준다. 나머지 둘(부분 stop 실패 시 writer
방치, 재기동 뒤 pair 재-attestation 누락)도 모두 고치고 각각의 회귀 테스트를
추가했다. backend 전체 1483 passed, ruff/mypy 유지.

T-049D(cutover gate)부터는 이 diagnose 결과를 실제로 cutover 시작 조건에 묶는다.

---

## 2026-08-03 (T-049B: cache-target 진단 DB stage primitive 구현)

open issue 확인 결과 이슈 #99(H35 Map 쪽 `0063→0078` 실 prod 데이터 실측 확정값)만
있었고 T-049B 범위와는 무관해(T-049D/E의 cutover-retry 기대값 대조용) 이번 phase에는
반영하지 않았다.

`cache_target_diagnostic_stages.py`를 신설해 설계 문서 3절의 9개 DB stage를
`cache_target_backup.py`의 기존 pg_dump/pg_restore helper 위에서 typed
`DiagnosticStageReceipt`로 분해했다. 적대적 리뷰어 2명이 독립적으로 같은 핵심 공백을
찾았다: scratch schema/data inventory 비교 stage가 `_run_logical_inventory`의 실제
실패 원인(timeout/subprocess 실패/정책 위반 stderr)을 버리고 전부
`inventory_mismatch`로 뭉뚱그려, 설계 문서가 명시한 "원인을 stderr_policy_rejected와
다른 subprocess failure class로 분리한다"는 목적을 무너뜨리고 있었다 — 고쳤다.
리뷰어 1은 추가로 archive 파일 open 지점에 `cache_target_backup.py`의 owner-only
디렉터리 검증이 빠져 있었던 것과, scratch 관련 4개 함수가 production과의 이름 충돌을
`diagnose_scratch_create`만큼 방어하지 않던 것을 지적해 반영했다. 회귀 테스트
28건으로 backend 전체 1459 passed, ruff/mypy clean을 확인했다.

T-049C(writer fence·orchestration)부터는 이 primitive들을 순서대로 호출하고,
mid-sequence 실패 시 `diagnose_scratch_cleanup` 호출을 보장하는 책임을 진다.

---

## 2026-08-03 (T-049A: cache-target 진단 typed 모델·storage 구현)

설계 문서 6절이 고정한 5-phase 순서의 첫 PR로 `cache_target_diagnostics.py`를 신설했다.
`DiagnosticPhase`/`DiagnosticStage`/`DiagnosticFailureClass` sealed Literal union,
`CacheTargetDiagnosticIdentity`(설계 문서 4절의 input logical identity), 비밀 없는
`DiagnosticStageReceipt`, `CacheTargetDiagnosticJournal`을 `cache_target_window.py`의
frozen-dataclass phase-state-machine 패턴을 그대로 따라 정의했다. storage는 기존
`cache_target_cutover.write_cutover_state`/`read_owner_only_state`를 재사용해 0600/0700
atomic write 계약을 그대로 물려받는다.

적대적 리뷰어 2명(Agent tool, 독립·병렬)이 코드를 리뷰했다. 두 실공백을 확인:
(1) `DiagnosticStageReceipt.role`이 그 receipt가 저장된 evidence tuple
(`map_application_receipts`/`map_dagster_receipts`/`pinvi_receipts`)과 실제로 일치하는지
아무도 검사하지 않아, `map_application_receipts`에 `role="pinvi"` receipt가 그대로
통과했다. (2) `completed` phase 진입 시 모든 receipt의 `status="succeeded"`를 요구하지
않아, 하나 이상의 stage가 실패한 채로도 진단이 `completed`로 끝날 수 있었다. 둘 다
`_validate_journal`에 명시적 검사를 추가해 고쳤고, 각각의 회귀 테스트를 추가했다.
리뷰 중 나온 사소한 지적(`external_event_count: int` → `Literal[0]`로 타입 강화, 테스트
이름 개선, missing-field tamper 테스트 추가, write-never-persists-invalid 테스트 추가,
carry-forward 의미론 테스트 추가)도 모두 반영했다. 최종 36건의 회귀 테스트로 backend
전체 1431 passed, ruff 기존 baseline 유지, mypy clean을 확인했다.

T-049B(DB diagnostic primitive)부터는 매 phase 착수 전 open issue를 먼저 확인한다.
issue #99(H35 Map 쪽 `0063→0078` 실 prod 데이터 실측 확정값)는 T-049A 범위와 무관하고
T-049D/E의 cutover-retry 기대값 대조에 필요하므로 해당 phase에서 반영한다.

---

## 2026-08-03 (T-049 cutover 사전 진단과 abort budget 설계)

T-VN-41에서 production Map data logical inventory가 fail-close된 뒤 full pre-forward window를
반복 실행해 원인을 찾는 방식이 길어지는 문제가 드러났다. `cache-target diagnose`가 C6c lock과
writer fence 안에서 DB별 archive·schema/data inventory·scratch restore rehearsal과 canonical
authenticated smoke를 별도 transaction으로 확인하도록 설계했다. 이 경로는 candidate build, migration,
initial event, sync enable, `.env`/manifest mutation을 하지 않으며 `external_event_count=0`을 보장한다.

진단과 cutover journal은 raw stderr/stdout, DSN, credential, resolved Compose, backup path를 남기지 않고
typed stage/failure class와 logical identity만 owner-only receipt에 기록한다. diagnostic archive는 최신
cutover backup으로 재사용하지 않으며, actual cutover는 writer fence 뒤 fresh backup/rehearsal을 다시
만든다. 같은 input의 반복 failure에는 bounded abort budget을 적용해 regression을 포함한 수정과 새
diagnostic receipt 없이는 재시도를 막는다. 상세 정본은 당시 cache-target cutover 진단 문서와
ADR-29다.

## 2026-08-03 (T-048 PinVi authenticated-readiness GET timeout 보강)

결합 rollback이 old runtime을 재기동한 뒤 PinVi login은 성공했지만, 바로 다음 canonical
`GET /admin/etl/summary`가 response-header 전에 한 번 timeout 났다. 같은 session의 다음 실행에서는
ETL summary·provider sync·typed cancel·logout·post-logout protection이 모두 정상 통과했으므로, login credential이나
canonical response 계약이 아니라 authenticated read readiness race로 분리했다.

`run_pinvi_canonical_smoke`의 session cookie가 있는 두 idempotent admin GET만 `ConnectionRefusedError` 또는
`TimeoutError`를 한 번(최대 두 attempt) 재시도한다. retry opt-in은 body 없는 `GET`만 허용하며, login/logout,
post-logout protection, cancel을 포함한 모든 `POST`는 opt-in 자체를 거부한다. 단, login의 기존 요청 전
`ConnectionRefusedError` retry는 유지하고 timeout은 계속 fail-close한다. default timeout fail-close와 destructive
request 차단, direct·`URLError` timeout의 마지막 attempt 성공 회귀를 함께 고정했다.

## 2026-08-03 (T-048 authenticated smoke readiness window 보강)

n150에서 pre-forward 실패 뒤 결합 rollback을 같은 cutover ID로 재개했을 때, 구 runtime은 정상 복원됐지만
Compose health 직후 authenticated smoke의 첫 TCP 연결이 두 번 연속 5초 window를 넘겼다. 이후 동일 runtime의
Map UI login/logout/protected-page와 PinVi admin session·canonical read/cancel smoke는 모두 통과했으므로,
credential·계약 오류가 아니라 readiness race로 분류했다.

첫 signed read와 Map UI·PinVi admin login, PinVi Web shell의 `ConnectionRefusedError` 재시도만 기존 5회에서
30회(1초 간격)로 늘려 Compose healthcheck `start_period`와 같은 bounded window로 맞췄다. timeout·DNS 등 다른
`OSError`, HTTP/envelope/인증 오류, destructive cancel과 후속 admin 요청은 계속 즉시 실패한다. 마지막(30번째)
attempt에서만 성공하는 회귀를 추가해 window 축소를 막았다. endpoint·credential·응답 본문은 journal에 기록하지
않는다.

## 2026-08-03 (T-048 production logical inventory warning 판정 보강)

n150의 실제 Map logical inventory에서 `pg_dump --data-only`가 순환 FK의 restore-advisory warning을 stderr로
출력했지만 exit code는 0이었다. 기존 구현은 stderr가 비어 있지 않다는 이유만으로 정상 dump를 실패 처리해
pre-forward cutover를 coupled rollback으로 되돌렸다. data-only dump에서는 heading·detail·두 고정 hint가
모두 일치하는 circular-FK advisory block만 허용하고, schema-only·다른 warning·nonzero exit는 계속
fail-close하도록 고정했다. 허용·비허용 warning 및 nonzero exit 회귀를 추가했다. 경고 원문·DB DSN·credential은
journal이나 receipt에 기록하지 않는다.

## 2026-08-02 (T-048 production rollback smoke readiness 보강)

n150 최초 결합 전환의 pre-forward rollback에서 Compose health 통과 직후 Map loopback endpoint가 잠깐
연결을 거부해, 원래 backup/fence 오류 대신 recovery smoke 오류가 최종 원인으로 덮이는 현상을 확인했다.
첫 Map signed read와 기존 read-only PinVi Web login shell 호출에서 exact `ConnectionRefusedError`만
5회·1초 간격으로 재시도한다. 각 HTTP 호출의 10초 timeout까지 포함한 최악 상한은 14초다. timeout·DNS 등
다른 `OSError`, typed HTTP 응답, envelope/인증/권한 오류와 PinVi destructive cancel probe는 재시도하지
않는다. recovery에서 Map UI 또는 PinVi login port가 같은 race를 보인 후에는 두 로그인에만 opt-in flag를
추가했다. exact 연결 거부는 TCP handshake 전에 실패하므로 요청 재전송 불확실성이 없고, destructive cancel과
후속 admin 요청은 기본값 `false`를 유지한다. exact unavailable+cause 조합, 비재시도 오류와 시도 상한 회귀를
추가했다.

## 2026-08-02 (T-048 PinVi release pin과 Map GC observation 계약 정렬)

PinVi #424가 단일 적대적 GO review 뒤 squash merge되어 exact release SHA
`4943282006139fa3b4ef3cb247780bfd9721b4c7`가 확정됐다. tracked production manifest에 이 SHA를
release로 고정해 candidate와 active·rollback pair provenance가 다른 PinVi source를 mutation 전에
거부하도록 했다. reviewed candidate SHA는 자동 승격 근거가 아닌 감사 출발점으로 그대로 보존했다.

merged PinVi의 final-boundary request 13개 필드와 append-only audit row의 request/evidence/Map evidence/
initial·final fence/prior/canary 8개 대조 필드를 Manager parser·fresh DB query와 다시 대조해 동일함을
확인했다. Map GC observation ID는 versioned namespace 정본인
`h35:{transaction_id}:cache-target-snapshot-gc:v1`로 정렬하고 이전 `h35:{transaction_id}:gc` receipt를
fail-close하는 회귀를 추가했다. Map #924 merge 뒤 `map_release_revision`을 final merge SHA로 바꾸고
Manager exact-head 단일 적대적 리뷰를 진행한다.

## 2026-08-02 (T-048 race-free final fence와 실제 GC checkpoint D)

causal canary 뒤 running writer의 순간적인 in-flight 0을 최종 경계로 승인하던 경쟁 조건을 제거했다. Map
H35 helper chain에 실제 `gc` operation을 추가해 acquired/non-skipped, remaining backlog 0, referenced 보존과
deterministic observation 일치를 typed receipt로 먼저 fsync한다. 그 뒤 `final_writers_fencing`을 durable하게
기록하고 exact 5 writer를 모두 정지한 상태에서 세 DB in-flight 0, Map Dagster run 0, registry/state의 별도
final fence와 Map application/Dagster write-counter hash를 결박한다.

stopped Map `verify`는 stream/control/restore epoch/etag/high-watermark/snapshot count·Merkle와 네 backlog 0의
full typed evidence를 반환한다. Pin finalize request는 initial/final fence와 full Map evidence+SHA를 전달하며,
Manager는 append-only audit receipt를 fresh Pin DB의 exact 1행과 request/evidence/fence/prior/canary 전체로
대조한다. Pin audit INSERT는 final fence hash에 포함하지 않되 Map 두 DB counter는 verify 전·finalize 전후
불변이어야 한다. audit commit 뒤 Manager journal fsync가 유실되어도 같은 request의 동일 audit row replay만
허용한다.

forward boundary는 writer가 stopped인 상태에서 먼저 fsync한다. 이후 exact 5 writer를 idempotent하게 재기동하고
health와 compatible-pair attestation을 통과한 `runtime_activated`에서만 성공한다. GC backlog/observation 실패,
foreign audit row, Map counter drift, finalize 응답 유실 재개와 forward-commit 뒤 재기동 재개 회귀를 제품 경계에
추가했다.

## 2026-08-02 (T-048 결합 window 구현 checkpoint C)

`prepared` journal 뒤 exact 5-writer registry를 먼저 검증하고 DB in-flight와 Map Dagster run 0에서 모든
writer를 정지하는 `writers_fenced` phase를 추가했다. Map application·Dagster와 Pin DB는 custom dump를
owner-only transaction directory에 직접 stream하고, 각각 별도 scratch DB에 실제 restore해 Alembic head와
schema/data logical inventory가 일치해야만 typed backup receipt를 만든다. 세 backup 전체 앞뒤의
insert/update/delete counter와 `stats_reset` identity, in-flight 0, Map Dagster run 0이 같아야
`backups_committed`에 도달한다.

DB identity는 cross-repo `h35-db-identity-v1`의 prefix·필드별 NUL·terminal NUL exact bytes로 통일했다.
scratch DB는 운영 identity를 가장하지 않고 별도 rehearsal identity를 원 archive SHA와 inventory에 결박한다.
manager env/manifest/initial/enable 상태도 같은 transaction의 rollback bundle에 넣고, rollback은 세 DB 전체
restore 뒤 manager state와 old runtime을 순서대로 복구하는 private capability에서만 허용한다.

Pin 경계는 read-only schema `0047` preflight와 schema `0048` append-only final audit를 분리한다. 따라서
window는 `candidate_built`, `pin_preflight_verified`, `map_preflight_verified`와 terminal 직전
`final_boundary_verified`를 각각 durable phase로 기록한다. final audit row는 app-level DELETE하지 않으며,
pre-forward rollback에서는 schema `0047` Pin DB 전체 restore로만 제거된다. release unset은 journal·Docker·
DB mutation 전에 차단하는 회귀를 추가했다.

## 2026-08-02 (T-048 NO-GO 반영: generation bootstrap과 H35 결합 전환 재설계)

exact head `58ca4491` 적대 리뷰에서 기존 v4 active/rollback이 old Pin인 production은 새 release gate와 일반
manifest 승격 규칙 때문에 generation 7 active=rollback pair를 만들 수 없는 순환 의존이 확인됐다. initial과
enable도 별도 CLI가 각각 lock을 잡아 그 사이 H35 DB/CSV 변경이나 DB rewind를 막지 못하고, host receipt만
일치하면 live cutover state를 다시 보지 않는 문제가 있었다.

ADR-28과 runbook을 보강해 existing-v4 전용 one-time generation bootstrap을 정본화했다. sync=false exact
candidate를 검증·배포한 뒤 v4 active/rollback을 같은 첫 generation 7 pair로 원자 commit하고, old pair는
일반 rollback slot이 아니라 Map application·Dagster·Pin DB와 manager env/state/manifest를 함께 복구하는
coupled rollback bundle에만 둔다.

backup→build→DB migration→H35 CSV→bootstrap→initial→enable→causal canary→GC→verify→forward commit 전체를
한 process의 C6c lock과 owner-only `0600` durable journal로 수행한다. non-terminal journal은 same transaction
resume/coupled rollback 외 manager mutation을 subprocess 전에 차단한다. pre-forward 실패는 new runtime을
먼저 중지하고 세 DB와 manager state를 복구한 뒤 old image를 마지막에 기동한다. migration 뒤 image-only
rollback은 금지하며 forward commit 또는 최초 외부 event 뒤에는 old restore를 거부한다.

Map schema/CSV 동작은 candidate image의 `h35_cutover.py`가 `preflight/migrate/csv5/verify`만 소유하고,
backup/restore/finalize와 runtime lifecycle은 manager가 소유한다. manager는 transaction/source/schema/backup
identity에 결박된 secret-free exact JSON receipt만 소비한다. cache-target 전체가 unset/default인 기존 production C6c는
유지하고 부분 설정만 fail-close하며, Map·Pin actual release source를 active/rollback 양쪽에 결박한다.
production initial/enable의 호출자 제공 attestor/canary/smoke 주입은 제거하고, 모든 public mutation entrypoint에
release/pair/candidate 오류의 mutation-zero 행렬을 추가하는 구현 계획으로 전환했다.

## 2026-08-02 (T-048 T-VN-41 production cutover docs-first 착수)

ADR-28과 전용 runbook으로 cache-target production 경계를 먼저 고정했다. Map API에는 digest 기반
4-role registry만, PinVi ordinary API에는 sync/command/consumer/consumer ID/세 contract pin의 정확한
7개 변수만 전달한다. restore-fence/recovery 원문은 ordinary runtime에서 제외하고 C6c 전역 lock과
frozen canonical evidence를 검증한 일회성 initial-cutover runner에는 실제 사용하는 command·consumer·
recovery만 주입한다. restore-fence는 Map registry와 향후 별도 restore 작업 경계에만 보관한다.

최초 runner 결과를 secret-free durable receipt로 먼저 commit한 뒤에만 sync를 `true`로 원자 전환한다.
동일 immutable PinVi API image를 재생성하고 기존 full compatible-pair attestation까지 통과해야 enable을
확정하며, 실패·crash는 `sync=false` env/runtime으로 수렴하도록 구현 범위를 정했다.

적대 설계 리뷰를 반영해 Map registry는 신규 `cache-target:command`를 포함한 정확한 네 principal·role별
최소 scope·`["pinvi"]`만 허용하도록 강화했다. initial runner에는 실제 쓰는 command/consumer/recovery만
주입한다. active뿐 아니라 rollback pair도 같은 generation/contract와 cache health/pin smoke에 결박하며,
env 변경 전 `enable_preparing`부터 enable/rollback 전 단계를 fsync하는 crash journal과 전체 전역 critical
section을 구현 계약으로 추가했다.

추가 적대 리뷰에 따라 initial receipt는 frozen env/raw·resolved Compose, active/rollback pair와 protected
4-role binding의 logical SHA를 함께 묶되 registry JSON·개별 digest는 기록하지 않는다. elevated recovery
token은 Docker inspect metadata에 남지 않는 owner-only secret-file/고정 entrypoint 경계로 전달하고 모든
종료 경로에서 orphan을 정리한다. terminal enable 전에는 command→Map event→PinVi DB/cache→ACK, lag/DLQ,
count/Merkle를 확인하는 n150 causal canary를 필수 rollback gate로 추가했다.

운영 adapter는 canonical `.env`를 owner-only 단일 링크 regular file로 검증하고 기대 SHA에서만 원자 교체한다.
enable 전에는 `sync=true` Compose 후보를 별도로 resolve해 journal에 SHA를 고정하므로 crash 재개 시 process
override가 달라져도 거부한다. `ktdctl cache-target initial|enable` command를 추가하고, causal canary는 running
PinVi API container의 `pinvi-cache-target-causal-canary`를 bounded `docker exec`로 호출한다. stdout의 exact
receipt만 parse하며 고정 target, UUID identity, 연속 generation/order, backlog 0, cursor/count/Merkle 수렴을
검증하고 raw stdout/stderr는 남기지 않는다. enable 실패 시 `sync=false` runtime 재생성 뒤 generic Compose
health smoke까지 통과해야 `rolled_back`에 도달한다.

첫 구현 checkpoint로 Map API registry와 PinVi ordinary 7개 변수 및 명시 API base URL을 Compose에 배치했다.
별도 contract validator는 canonical consumer ID, 정확한 네 principal·role scope·`["pinvi"]`, token digest,
네 role/legacy token 상호 분리와 pin 형식을 검증한다. restore-fence/recovery 원문은 manager-only로 분류하고
raw/resolved/runtime protected name/value 경계에 registry JSON과 digest까지 포함했다.

두 번째 구현 checkpoint로 frozen env/raw·resolved Compose/active·rollback pair/role-binding hash에 결박된
initial receipt와 enable/rollback durable phase 모델을 추가했다. owner-only state의 atomic fsync/replace,
동일 receipt retry 수렴·foreign evidence 거부, recovery secret-file의 성공/실패 cleanup, causal evidence 없는
terminal commit 거부를 독립 테스트로 고정했다. recovery principal은 같은 trust domain의
`cache-target:recovery` + `cache-target:recovery-replay` exact 두 scope로 정렬하되 별도 replay token은 만들지
않는다.

frozen initial runner adapter는 command/consumer/recovery 세 token을 단일 owner-only bundle mount로 전달하고
ephemeral container의 세 env를 빈 값으로 override한다. restore-fence는 bundle API에 받지 않으며 argv·Docker
create metadata에는 원문/digest가 남지 않는다. fixed one-off container identity는 active immutable image와
Compose one-off label을 대조한 뒤에만 success/failure/retry orphan으로 제거한다. exact receipt retry는 runner를
재실행하지 않고 secret-free 결과로 수렴하며, final cross-repo pin attestor는 주입 계약으로 남겼다.

production pin checkpoint에서는 generation `7`, Map OpenAPI SHA-256
`622ea54c98e9b0c09592cf84aced36227992c6bdf256742a3532b892f0efccf2`, Map functional owner
`9b945ce832ecc3ed037d66c9d4e7bda9a1a69ae0`와 PinVi reviewed candidate
`6ac8baae2814fae5b16c95846ee40d77cc7fe283`를 tracked manifest에 기록했다. candidate는 감사 정보로만
취급하고 `pinvi_release_revision`은 비워 두었다. 따라서 두 적대적 GO review와 PinVi merge 뒤 별도 final
pin commit 전에는 production initial/enable과 compatible-pair capture/deploy/rollback이 모두 mutation 전에
fail-close한다. 주입 attestor도 이 release gate를 우회할 수 없고, timeout 오류는 Python exception context에
원래 subprocess payload를 남기지 않는다. cache-target contract 미설정 경로는 기존 동작을 유지한다.

## 2026-07-31 (T-047 compatible-pair canonical readiness 계약 정렬)

production compatible-pair preflight가 canonical healthcheck가 없는 Grafana, Prometheus,
Concierge MCP·Scheduler·UI, Map Dagster daemon까지 일률적으로 `Health=healthy`를 요구해
정상 `running` runtime을 mutation 전에 영구 차단하는 결함을 확인했다.

ADR-27에 따라 frozen transaction의 canonical resolved Compose service spec에서 typed readiness
policy를 직접 파생한다. 활성 healthcheck는 `running + healthy`, healthcheck가 없거나 Compose
표준으로 비활성화됐으면 `running`을 요구한다. service 누락·종료, 선언된 healthcheck의 빈/
`starting`/`unhealthy` 상태, malformed/모호한 정의는 Docker 조회 전 또는 mutation 전에
fail-close한다. 이름별 예외 목록, image 상속 probe 추측, `kill -0 1` 같은 가짜 probe는 추가하지
않았다.

unit 회귀는 선언/미선언/비활성 healthcheck와 missing/exited/starting/unhealthy/malformed를
고정했다. 로컬 `alpine:3.20`을 `pull_policy: never`로 사용하는 폐기형 실제 Compose에서는
healthcheck가 healthy인 service와 healthcheck 없는 long-running service 조합이 통과하고 실제
unhealthy service가 거부되는 것을 확인했다. focused 결과는 `14 passed`이며 변경 source/test
Ruff도 통과했다.

전체 backend `1,155 passed`, frontend `type-check`/production build, 공개 placeholder를 사용한
canonical Compose `config --quiet`/resolved 22-service 분류도 통과했다. 저장소 전체 Ruff에는
이번 변경과 무관한 `test_api.py`/`test_c6c_image_retention.py` import 정렬 6건, strict mypy에는
`registry.py`의 기존 `no-any-return` 1건만 남았고, touched source/test에는 새 오류가 없다.
추적 이슈는 #90이다.

단일 적대 리뷰어의 exact `9759d969` 리뷰는 P1으로 기본 `compose ps`가 같은 service의 stopped
replica를 숨기고 dict가 duplicate를 덮어쓰는 mutation 전 fail-open을 실제 Docker에서 재현했다.
P2로 실제 Docker test가 clean runner에서 image 부재를 silent skip하고 `compose down` 실패와
residue를 검사하지 않는 증거 공백도 지적했다.

이를 반영해 normal/frozen recovery readiness는 모두 `ps --all`을 사용한다. canonical
`scale`/`deploy.replicas`와 runtime record는 service별 exact singleton이며 canonical
`container_name`도 exact 일치해야 한다. 예상 밖 service, duplicate, mixed malformed record는
하나도 버리지 않고 전체 payload를 거부한다. 필수 실제 gate는
`KTDM_REQUIRE_DOCKER_INTEGRATION=1`에서 Docker 부재를 실패로 만들고, image가 없으면 명시적으로
준비한 뒤 immutable image ID를 Compose에 사용한다. 실제 scale 2에서 replica 하나를 stop해
기본 `ps`가 running 하나만 숨겨 보이는 조건을 재현했고 새 `ps --all` 경로가
stopped+running duplicate를 거부했다. `down` 반환 코드와 사후 project container/network/volume
0개도 검증한다. 보강 focused unit `37 passed`, 필수 actual Docker gate `1 passed`, backend
전체 `1,179 passed`다.

같은 단일 리뷰어가 exact `fd61c16f`를 다시 검토해 이전 P1/P2가 모두 해소됐고 새 blocker가
없다고 판정했다. 최종 판정은 `P0 0 / P1 0 / P2 0`, `ACCEPT FOR TESTS`이며 PR #91은
draft/mergeable, 원격 check가 구성되지 않은 상태다. n150 mutation은 수행하지 않았고 부모
작업의 read-only exact preflight와 별도 승인된 compatible-pair 실행만 남겼다.

## 2026-07-31 (T-045 Map UI credential rotation 제품화 착수)

T-045를 별도 코드 PR로 진행 중 전환했다. 첫 checkpoint는 `ktdctl map-ui-auth rotate`
입력 경계와 Map UI PBKDF2 hash 정본을 먼저 고정하고, 이후 같은 PR에 production
transaction·journal/recovery·UI-only recreate 검증을 누적한다.

rebase 후 hardening checkpoint에서 production C6c/rotation mutation을 root-only
`/run/lock/kor-travel-docker-manager/global-mutation.lock` hardened lock으로 통일했다.
Map UI rotation은 lock 내부에서 canonical `.env`를 다시 읽어 pre-lock 값과 SHA/hash를
재검증하고, journal/backup/frozen compose는 root-owned private file 검증을 통과한 경우에만
읽거나 정리한다. source snapshot 배포는 root-owned/non-writable checkout과 root-owned
`.ktdm-source-revision` exact git SHA 파일을 필수 evidence로 요구하며, root process가
user-owned `.git/config`를 실행하지 않도록 git 명령 검증을 제거했다.

추가 hardening에서 `.env` owner를 `SUDO_UID`(없으면 root direct는 root, non-root 테스트는 현재
UID)로 산출한 뒤 최초 read/re-read/replace/recovery까지 같은 expected owner로 전파했다. frozen
compose 생성은 현재 `.env`와 root-owned compose evidence를 전후 재검증하고, 기존 C6c
raw/resolved protected value·system bind·secret isolation validator를 통과한 resolved 문서만
UI recreate와 rollback recovery에 사용한다.

두 번째 rereview checkpoint에서는 production state를 env-owner `$HOME` 추론에서 FHS 정본으로
clean-cut했다. C6c pair와 Map UI rotation은 모두 `/run/lock/kor-travel-docker-manager/global-mutation.lock`
및 `/var/lib/kor-travel-docker-manager/<compose-project>/compatible-pair-v4.json`를 같은
`c6c_state_paths()` 결과로 사용한다. root 실행은 user-writable venv를 직접 `sudo`하지 않고,
root-owned `/usr/local/sbin/ktdctl-map-ui-auth-rotate` → root-owned `/opt/kor-travel-docker-manager`
isolated venv/package 경계를 통해서만 rotation module을 import한다.

rollback/recovery journal은 fresh recovery session으로 생기는 세 번째 `.env` SHA를
`recovery_env_sha256`으로 기록하고 `rollback_prepared`→`rollback_recreate_started`→
`rollback_verified`→`rolled_back` phase를 둔다. pending recovery는 pre-rotation UI stable
signature와 non-UI snapshot을 journal evidence로 읽어 active image, UI health/auth, non-UI
불변성을 재검증하고, terminal cleanup에서 backup·journal·frozen compose를 함께 정리한다.

세 번째 hardening에서는 journal evidence를 secret-free로 재정의했다. UI runtime은 stable canonical
bytes의 SHA-256만 저장하고, non-UI runtime은 service별 allowlist metadata digest만 저장한다.
rollback은 root-private `env.recovery` bytes를 먼저 fsync한 뒤 journal에 SHA를 기록하고, 재실행은
old/new/recovery SHA 각각에서 같은 recovery bytes로만 resume한다. `committed`/`rolled_back` terminal
journal은 cleanup 중 backup·recovery artifact가 이미 지워진 crash도 current terminal SHA와 runtime/auth
검증 후 같은 operation audit을 보충하고 남은 artifact를 정리한다. env_new/committed crash 뒤 같은
stdin 두 줄을 replay해도 pending journal recovery가 일반 current-hash 검증보다 먼저 실행되도록 했다.

trusted root launcher는 `/usr/bin/python3 -I -S`로 wheel `RECORD`와 root-owned site-packages/package를
검증한 뒤에만 venv Python을 exec한다. venv `bin/python` symlink는 canonical root-owned
`/usr/bin/python3.x` target chain으로 resolve될 때만 허용한다. 추가로
`scripts/install-ktdm-trusted-release`를 도입해 clean checkout의 tracked `git archive`를 root-owned
staging에 푼다. git archive는 source owner 권한으로 만들고, root는 root-owned/non-writable offline
wheelhouse만 `pip --no-index --find-links`로 소비한다. 기존 또는 명시 deployment-owner 0600 `.env`를
보존하며, staging exact source에서 backend wheel을 offline build해 설치한다. isolated wheel venv·
wheelhouse SHA·backend wheel SHA·wheel `RECORD` SHA·`.ktdm-release-manifest.json`을 만든 뒤
`/opt/kor-travel-docker-manager` activation/rollback 및 launcher self-check까지 이어지게 했다. launcher
self-check 실패 시에도 새 app root와 새 launcher를 제거하고 이전 app root와 launcher bytes/mode를 복구한다.

단일 적대 리뷰어의 세 번째 exact-head 리뷰는 production lock 선택, state root mode, active pair
provenance, 실제 wheel `RECORD`, wheelhouse 신뢰 시점, recovery file/journal crash window를
P0으로 지적했다. 이를 반영해 pair deploy/capture/rollback과 rotation은 canonical `.env`의
identity·bytes로 같은 lock을 선택하고 lock 획득 직후 transaction snapshot과 다시 결박한다.
`.env`가 없었다가 생기거나 경로·inode·bytes가 바뀌면 mutation 전에 중단한다. production state
root는 공용 primitive가 root-owned 0700으로 만들며, frozen rotation Compose에는 active pair의
다섯 immutable image ID와 Map/PinVi revision·production provenance를 모두 주입한다.

trusted installer는 canonical root-owned wheelhouse의 모든 ancestor와 각 wheel의
owner/mode/nlink/inode/digest를 root `pip` 실행 전에 snapshot하고 각 소비 단계 뒤에 exact
재검증한다. 실제 Poetry console script의 `../../../bin/ktdctl`은 exact venv entrypoint 하나만
허용하고 나머지 `RECORD` escape는 거부한다. recovery는 `rollback_preparing` journal을 먼저
fsync하고 orphan recovery와 양방향으로 수렴하며, foreign `.env`는 덮지 않는다. terminal audit은
operation ID당 한 번만 보충하고, 실패한 rollback 시도는 재시도 가능한 non-terminal evidence로
남긴다. active runtime은 canonical service 집합·container name·healthy 상태·OCI source revision을
모두 fail-close로 검증한다.

첫 disposable Linux 설치는 실제 wheel build/install과 `RECORD` 검증까지 통과한 뒤, installed
package의 registry가 source-layout 상대 경로를 사용해 `.venv/lib/config/docker-targets.yml`을
찾는 제품 경계 결함을 드러냈다. trusted launcher가 고정하는
`KOR_TRAVEL_DOCKER_MANAGER_PROJECT_ROOT`를 Compose와 registry의 공통 root resolver가 사용하도록
바꿔 source checkout과 installed `/opt` layout을 같은 명시 root 계약으로 수렴시켰다.

네 번째 exact-head 적대적 리뷰는 DockerService의 네 일반 mutation 진입점이 production lock을
선택한 뒤 새 effective environment를 별도로 캡처해 다른 transaction을 실행할 수 있는 결함,
installer가 고정 revision 대신 움직이는 `HEAD`를 archive하는 결함, installer가 rotation과 다른
lock 경계에서 오래된 `.env`를 활성화할 수 있는 결함, prepared/orphan recovery audit의 cleanup
crash 재시도 중복을 차단점으로 판정했다. DockerService는 공용 lock snapshot context가 반환한
identity·bytes와 실제 environment transaction을 exact 결박하도록 네 진입점을 통일했다.

trusted installer는 처음 읽은 exact commit SHA로 `diff-index`와 `git archive`를 모두 수행한다.
또한 source `.env`의 inode·mode·owner·size·mtime/ctime·SHA snapshot을 잡은 뒤
`/run/lock/kor-travel-docker-manager/global-mutation.lock`을 root-only/nonblocking 방식으로
획득하고, lock 안에서 같은 snapshot임을 재검증한다. 그 lock은 wheel build부터 app root·launcher
activation/rollback이 끝날 때까지 inherited descriptor로 유지되어 rotation과 두 installer의
경쟁을 모두 차단한다. terminal audit은 `committed`/`rolled_back`/`aborted` 세 상태로 clean-cut하고,
prepared residue와 orphan backup은 결정적 operation identity로 audit write 뒤 cleanup이
중단되어도 한 terminal result만 남긴다. staging venv의 generated `ktdctl`은 canonical `/opt`
Python과 project root를 고정하는 entrypoint로 다시 만들고 해당 wheel `RECORD` digest·size를
재산출해, atomic activation 뒤에도 직접 CLI와 trusted rotation launcher가 모두 같은 installed
root를 사용한다.

다섯 번째 exact-head 적대적 리뷰는 installer가 `.env` snapshot 검증 뒤 경로를 다시 열어
복사하는 TOCTOU, 일반 config mutation이 lock 밖에서 읽은 stale secret interpolation baseline을
사용하는 문제, 새 release 검증 뒤 이전 backup 삭제 실패가 EXIT rollback trap을 다시 발동할 수
있는 문제를 차단점으로 판정했다. installer는 전역 lock 안에서 canonical `.env`를
`O_NOFOLLOW` read-only FD로 한 번 열고 identity·mode·owner·SHA를 결박한 뒤, root-only 0700
staging에 그 descriptor의 exact bytes만 복사하도록 바꿨다. 경로와 descriptor는 copy 전후에
각각 재검증하고 installed `.env`도 owner/mode/nlink/size/SHA를 확인한다.

DockerService는 lock 안 exact Compose transaction bytes에서 대상 service의 environment baseline을
다시 읽어 secret interpolation 의미를 재검증한 뒤에만 candidate를 만든다. release commit은
검증된 active app/launcher evidence를 durable `committed` state로 먼저 fsync하고, EXIT cleanup도
그 state를 보면 rollback 대신 active를 보존한 채 post-commit GC만 수행한다. 실패 경로는 이전
app/launcher 복구가 완결되지 않으면 state와 유일한 rollback residue를 root-only 경로에 보존한다.

여섯 번째 exact-head 적대적 리뷰는 보존한 PID-scoped app/launcher residue가 다음 실행의 recovery
state와 연결되지 않는 문제와, launcher destination이 directory일 때 `mv -f`가 backup을 그 안으로
옮기고 성공으로 오인하는 두 P1을 재현했다. installer artifact를 PID 경로에서 host-global 고정
경로로 clean-cut하고, root-private `trusted-release-transaction.json`에 old app/launcher exact
evidence, target revision, 새 launcher SHA와 `preparing|prepared|committed` phase를 atomic fsync한다.
전역 lock 획득 직후 stale state를 먼저 reconcile하므로 non-committed는 old app/launcher exact
rollback, committed는 active revision/launcher digest 검증 뒤 idempotent GC를 끝내야 새 install을
시작할 수 있다. state가 없는데 artifact가 있거나 legacy PID/foreign collision이면 mutation 전에
fail-close한다. 새 app root의 dev/ino도 activation 전에 기록하므로 cleanup이 일부 파일을 지운 뒤
중단돼 manifest가 불완전해져도 transaction이 만든 exact directory만 제거하고 old rollback을
복원하며, 같은 경로의 foreign directory는 삭제하지 않는다. `preparing`은 activation artifact가
생기기 전 phase라 canonical app/env가 정상 운영 변경으로 달라져 staging 검증이 중단돼도
staging/archive/state만 폐기하고 active baseline을 건드리지 않은 채 다음 실행에서 다시 snapshot한다.
`committed` GC도 old rollback의 recorded root dev/ino를 사용하므로 이전 GC가 old tree 내부를
부분 삭제해 revision/manifest evidence가 사라진 뒤 중단돼도 같은 transaction-owned directory만
계속 삭제해 수렴하고, 다른 inode로 바뀐 경로는 fail-close한다. 실제 gate에서 old rollback 하위
mount로 GC를 중단해 `.ktdm-source-revision`이 이미 사라진 committed residue를 만든 뒤, mount 해제
후 재실행이 traceback 없이 남은 old tree를 제거하고 새 install까지 residue 0으로 완료했다.

launcher installer도 고정 root-owned staging file, destination regular-file shape 검증,
`mv -T`, 설치 후 owner/mode/nlink/SHA 재검증으로 바꿨다. disposable Debian 실제 gate에서 정상
offline build/install, committed GC 중 rollback mount failure→state/residue 보존→unmount 후 재실행
자동 GC, launcher destination directory collision→old app/launcher residue 보존→collision 제거 후
재실행 exact 복구·설치, 새 app 하위 mount로 cleanup을 부분 삭제 상태에서 중단→mount 해제 후
기록한 root dev/ino로 transaction-owned partial tree만 제거하고 old release 복구·재설치를 모두
통과했다.

최종 로컬 회귀는 backend 1,146건, C6c deployment 856건, Docker config 93건, credential
rotation 64건과 touched Ruff·strict mypy·shell syntax를 통과했다. 수정한 exact clean Git tree로
Debian disposable container에서
root-owned offline wheelhouse를 새로 만들고 실제 Poetry backend wheel을 build/install했다.
설치된 `RECORD`의 유일한 site-packages 밖 항목이 exact `.venv/bin/ktdctl`임을 확인하고,
release manifest↔source revision, canonical `/opt` installed `ktdctl`, installed config,
trusted launcher `--help`까지 통과했다. 별도 실제 process contention gate에서 같은 global lock을
다른 process가 보유하면 installer가 source/archive/wheel/app mutation 전에 즉시 중단하는 것도
확인했다. held `.env` FD 보강 뒤에도 동일한 실제 Debian offline build/install을 다시 통과했고,
staging 중 env-owner가 canonical 경로를 다른 inode로 교체하는 공격을 주입했을 때 active app을
만들지 않고 실패하는 actual TOCTOU gate도 통과했다.
이 checkpoint를 push한 뒤 같은 단일 적대 리뷰어의 exact-head 재리뷰와 n150 검증으로 이어간다.

---

## 2026-07-31 (T-046: `pinvi-pair deploy`/`capture`의 `--wait-timeout` 하드코딩 제거, issue #88)

kor-travel-map API는 uvicorn 기동 전에 `alembic upgrade head`를 실행하는데, `_run_up_stage`가
`docker compose up --wait --wait-timeout 120`을 하드코딩해 `CREATE INDEX CONCURRENTLY` 등
non-transactional DDL을 쓰는 긴 마이그레이션(실측 8~18분)에서 deploy가 실패로 판정되고
`_recover_previous_pair` rollback이 마이그레이션 진행 중인 컨테이너를 그대로 뜯어 durable한
부분 적용 상태를 남기는 문제였다. kor-travel-map T-VN-H35(prod alembic 0063→0069) 실행 중
실제로 배포가 중단되며 발견됐다(issue #88).

`_run_up_stage`부터 `deploy_compatible_pinvi_pair` → CLI `pinvi-pair deploy --wait-timeout`까지
`wait_timeout`을 관통시켰다. 기본값(120)은 그대로라 기존 호출은 회귀 없다.

적대적 리뷰 1명(Workflow 도구)이 스코프 공백을 찾아냈다: `pinvi-pair capture`(clean bootstrap)도
5개 활성화 단계에서 같은 하드코딩 `wait=True`를 쓰고, `bootstrap_map_api` 단계는 issue #88과
정확히 같은 alembic 선행 실행 패턴이었다 — 최초 bootstrap은 전체 마이그레이션 이력을 처음부터
실행할 수 있어 오히려 증분 배포보다 초과 가능성이 크다는 지적도 있었다. 리뷰가 실제 코드
라인(4139/4191/4214/4237/4264)까지 짚어 재확인했고, `capture_compatible_pinvi_pair`에도 같은
파라미터·CLI 플래그를 추가해 막았다(검증 로직은 `_validate_c6c_wait_timeout` 공유 helper로
통일 — CLI 기본값도 리터럴 대신 같은 상수를 import해 두 곳이 벌어지는 것을 막았다).

rollback/recovery 경로(`_recover_previous_pair`, `rollback_compatible_pinvi_pair`)는 의도적으로
그대로 뒀다 — rollback 대상은 이미 마이그레이션이 끝난 옛 image라, 진짜 hang을 빠르게 판별하는
쪽이 더 안전하다.

회귀 테스트 다수 추가: threading·기본값 유지·경계값(1/3600)·잘못된 타입/범위(`bool`은
`isinstance(x, int)`가 `True`라 별도 배제 필요)·`_run_up_stage`가 실제로 만드는 compose
인자·`_activate_pair_sequentially`의 세 단계 모두 동일 값 사용·`capture`의 11개 `up --wait`
단계(base 7종 + map_api + map_dependents + pinvi_api + pinvi_dependents) 모두 동일 값 사용.
backend 1067 passed(기존 1049 + 신규 18), ruff 기존 9건 유지, 변경 파일 mypy clean.

n150에서 실제 긴 마이그레이션을 수반하는 배포로 오발동 rollback이 재현되지 않는 것은 아직
확인하지 못했다 — kor-travel-map 쪽 실제 cutover 시점에 검증 예정.

---

## 2026-07-31 (C6c/C7 완료 태스크 이관과 credential blocker 분리)

`docs/tasks.md`에 남은 C6c/C7 태스크의 GitHub·운영 증거를 다시 대조했다. 실제 인수까지
끝난 T-037/038/039/040/041은 `docs/tasks-done.md`로 이관했다. T-031은 구현과 기존
live가 충족됐지만 새 official deploy의 credential preflight가 막혀 있어 활성 상태를
유지하고, 제품화 작업을 T-045로 분리했다.

- T-037/039: PR #67/#69의 Map UI 통합 경로와 PinVi login shell 계약을 2026-07-27
  compatible-pair에서 확인했다. Map UI는 login→`/ops/datasets` 보호 GET→logout→재차단을,
  PinVi는 SSR route chunk와 hydrated login form을 각각 책임 경계대로 통과했다.
- T-038/040: Manager가 Map API의 destructive와 features route를 production에서 literal
  `true`로 승인하고 다른 service/channel에는 이름이 없는 계약을 유지했다. 2026-07-26 C7
  destructive live와 2026-07-27 pair/live를 통과했고 Map issue #796 및 manager
  issue #70은 closed 상태다.
- T-041: PR #73(`c7328ed9`)의 content-addressed rollback reference와 cleanup 구현을 기준으로
  n150에서 active/rollback reference 가용성과 cleanup 성공을 확인했다. manager issue #72는
  closed 상태다. 프로세스 `SIGKILL` 주입 테스트는 실행했다고 과장하지 않고, 완료 근거를
  불변 reference·복구 보존 회귀·실운영 가용성으로 명시했다.
- 교차 C7 공식 증거는 2026-07-26 Map 조합에서 read-auth `7/7`, KMA active/cap/empty 각
  `2/2`, schedule-write `2/2`, POI-cache-causal `2/2`, `BLOCKED` 0건, 상태 복구와 active
  target 0이었다.
- T-031/T-045: canonical Manager `.env`의 Map UI hash/session은 running UI와 일치하지만
  manager smoke 평문은 hash 검증에 실패한다. 새 official deploy는 mutation 전에
  중단되는 것이 올바른 fail-closed 동작이다. `ktdctl` 전용 audited production workflow로
  hash와 session을 함께 회전하고 복구·감사·실운영 인수를 마칠 때까지 T-031을 완료로
  기록하지 않는다.

이 변경은 문서 전용이다. 코드, runtime, compose, 운영 환경은 변경하지 않았다.

---

## 2026-07-28 (tasks.md 정리 보정 — T-011/T-012/T-043/T-044 완료 이력 이관 누락 수정)

T-033/034/035/036을 tasks-done.md로 이관하면서, 같은 세션에서 이미 `[x]` 완료 처리했던
T-011·T-012·T-043·T-044는 정작 tasks.md에 그대로 남아 있던 것을 놓쳤다. `tasks.md`
자신의 선언("진행 중/대기 작업만 관리한다. 완료된 작업은 tasks-done.md로 분리한다")과
어긋나는 상태였다. 네 태스크의 요약 표 행과 전체 상세 절(기존 내용 그대로, 실측 기록
포함)을 tasks-done.md로 옮기고 tasks.md에서 제거했다. 파일 끝의 불필요한 trailing
blank line도 함께 정리했다. 코드 변경 없음(문서 전용).

---

## 2026-07-28 (T-033/034/035/036 완료 이력 이관 — GitHub issue 상태 확인)

n150 읽기 전용 점검에서 이미 증거로 충족을 확인했던 T-033·T-034·T-035·T-036의 남은
체크박스를 마무리하면서, 각 태스크 본문이 지시한 대로 관련 GitHub issue(#60/#62/#63)를
`gh issue view`로 확인했다 — **세 건 모두 이미 closed 상태였다**(저장소에 열린 issue
자체가 0건). 즉 GitHub 쪽은 이전에 이미 정리되어 있었고, 남은 것은 `docs/tasks.md`가
그 사실을 반영하지 못하고 있던 문서 쪽 지연뿐이었다.

- T-033: n150에서 실행 중인 Map 4종 image의 `org.opencontainers.image.revision`이 모두
  동일 40자 commit(`c8ed6164...`)이고 manifest v4 기록과도 일치함을 재확인.
- T-034: cAdvisor healthy·`/healthz` 200과 manifest의 2026-07-27 active 세대 기록으로
  capture+readiness가 이미 통과했음을 재확인.
- T-035: Map API 46시간째 healthy, `docker exec env`로 이름만 확인한 service별 secret
  격리(admin proxy는 API+UI, service token/cursor signing은 API 전용, Dagster/daemon은
  전무)가 설계 계약과 정확히 일치함을 재확인.
- T-036: `pinvi-dagster-latest` 9일째 healthy로 PinVi dependent bootstrap 완료를 재확인.

네 태스크 모두 `docs/tasks.md`에서 제거하고 `docs/tasks-done.md`에 요약 표 행과 전체
상세 절(기존 체크박스 + 이번 실측으로 채운 마지막 체크박스)을 그대로 이관했다. 코드
변경 없음(문서 전용).

---

## 2026-07-28 (n150 production 배포 + T-043 1013 shed 실측 검증)

`main`(T-012, T-011 2라운드, T-044)이 병합된 뒤로도 n150 production 매니저는 갱신되지
않은 상태였다(백엔드 프로세스가 오늘 03:30에 기동되어 T-011/T-044 코드보다 앞선다).
읽기 전용 사전 점검(cAdvisor healthz, compatible-pair manifest, 실행 중 Map/PinVi
이미지 4+1종의 OCI revision, Map API/UI의 production 전용 secret 이름 존재 여부)으로
n150의 현재 상태를 먼저 확인한 뒤, 사용자 승인을 받아 실제 배포를 진행했다.

**배포 절차**([[prod-deploy-mechanics]] 메모 그대로): `backend/src/`·`frontend/src/`만
dry-run 확인 후 rsync(`.env`·`docker-compose.override.yml`·`frontend/.env.*` 등 보존
파일은 경로 자체가 겹치지 않아 자동 회피), 프론트 `npm run build`로 재빌드, 백엔드
프로세스를 내리고 `nohup setsid`로 재기동(`/health` 200 확인), 프론트는 `next start
-p 12905`의 **process group만**(PGID 확인 후 `kill -TERM -<pgid>`) 종료하고 재기동
(`Ready in 947ms`). 호스트에 공존하는 다른 프로젝트의 next-server(v15/v16 여러 종)는
PID/PGID를 미리 확인해 전혀 건드리지 않았다. `kill -TERM -<pgid>`는 auto mode
classifier가 차단해 사용자가 직접 실행했다.

**T-043 1013 shed 실측**: 배포 직후 `scripts/verify-frontend-toolchain.sh`가 `툴체인
정상`을 보고했다. WS shed 동작은 `/ws/status`가 아니라 실제로는 `/api/v1/ws/status`에
마운트되어 있다는 것을 라우터 코드에서 재확인한 뒤(첫 시도는 잘못된 경로라 accept 이전
ASGI 거절 → uvicorn이 403으로 변환하는 것이었다 — 이 자체가 코드 주석이 설명하는 정확한
현상이었다), Origin 헤더도 앱의 `allowed_frontend_origins()`를 그대로 호출해 실제
허용값과 100% 동일하게 맞췄다. 그 뒤 `/api/v1/ws/status`에 유효한 Origin으로 300개의
미인증 WebSocket 연결을 `asyncio.Barrier`로 동시에 발생시켜 실측: `4401`(AUTH_REQUIRED)
121건, `1013`(TRY_AGAIN_LATER, shed) 179건. 기본 상한 `KTDM_WS_MAX_PENDING_AUTHORIZATIONS=64`를
넘는 동시 인가 시도가 정확히 shed되는 것을 실제 production에서 확인했다. 테스트 스크립트는
실행 뒤 `/tmp`에서 삭제했고, `/health` 200과 다른 모든 컨테이너의 기존 uptime이 그대로
유지되는 것을 확인해 부작용이 없음을 검증했다.

이번 세션에서 함께 확인한(읽기 전용) T-033·T-034·T-035·T-036의 n150 관련 잔여 체크박스는
증거상 이미 충족된 것으로 보이나(각 태스크 본문 참조 대신 이 항목에 요약: OCI revision
전 서비스 동일 40자 commit, cAdvisor healthy+manifest 최신 active 세대, Map API/UI
secret 이름별 격리 계약 실측 일치, PinVi Dagster 9일째 healthy), GitHub issue(#60/#62/#63)를
직접 닫는 것은 별도 확인 없이 진행하지 않았다 — tasks.md 체크박스 갱신은 이번 항목의
범위 밖이라 다음 세션에서 사용자 확인 후 처리한다.

---

## 2026-07-28 (T-044: ensure 라우트 production 서버측 차단)

T-012 적대적 리뷰가 남긴 후속 항목이다. `POST /targets/{target}/ensure`(`ComposeService.ensure_target`)는
`db`·`storage`·`gra`·`cadv`·`prom`·`geo`·`conc`처럼 Map/PinVi API 런타임이 아닌 target에 대해서는
production에서도 막히지 않았다 — `assert_c6c_mutation_allowed`는 대상 service가 C6c runtime과
겹치지 않으면 그대로 반환하는데(이는 개별 컨테이너 start/stop/config가 production에서도 정상
동작해야 하므로 의도된 것이다), `ensure`는 그런 개별 제어가 아니라 target 전체(의존 서비스
다건)를 `--build`/`--force-recreate`+init step까지 허용하는 범용 dev 부트스트랩 경로라서 다르다.
지금까지 유일한 방어선은 프론트가 production 빌드에서 버튼 자체를 숨기는 것뿐이었는데, 이는
브라우저 번들의 속성이지 백엔드의 속성이 아니다 — `npm run dev` 프론트를 운영 백엔드에 붙이면
버튼이 보이고 실제로 실행됐다.

**수정**: `ensure_target`이 `assert_manager_mutation_allowed`의 반환값(`mode`)을 받아,
`assert_c6c_mutation_allowed` 호출 직후(`c6c_deployment_lock` 안, compose baseline 검사와 첫
Docker subprocess보다 먼저) `mode == "production"`이면 target·service 구성과 무관하게 전면
차단하도록 했다. `assert_c6c_mutation_allowed` 자체나 `assert_compose_mutation_allowed`,
`control_container`/`update_container_config`/`reset_container_config`는 건드리지 않았다 —
그 경로들이 비-C6c target을 production에서도 허용하는 것은 의도된 동작이고 이 태스크의 범위
밖이다. `DeploymentContractError`를 재사용해 기존과 동일하게 HTTP 409로 매핑되므로 라우트 코드
변경이 없고, CLI(`ktdctl ensure`)도 같은 메서드를 호출하므로 자동으로 함께 막힌다.

새 회귀 테스트 2건: production + 비-C6c target(`storage`) → 거부(`subprocess.run` 미호출까지
확인), local/개발 모드에서는 정상 `ensure` 흐름이 막히지 않는 것을 양성 대조로 확인. 기존
`test_production_generic_mutation_guard_rejects_every_api_entrypoint`(C6c target `map`은
`assert_c6c_mutation_allowed`가 이미 차단)와 두 차단이 서로 가리지 않고 공존하는 것도 함께
재확인했다.

**적대적 리뷰 2명(ultracode on, Workflow 도구로 병렬 실행) + 독립 검증**: 리뷰어 1(보안
완결성/mode 판정/상태 코드 담당)은 `_validate_mutation_environment`가 `local`/`production`
외에는 절대 반환하지 않는 fail-closed 계약임을 확인해 새 검사를 우회할 제3의 값이 없음을,
`ensure_target`/`_ensure_target_unlocked`의 호출자가 API 라우트와 CLI 단 둘뿐임을(다른 라우트·
WebSocket·스케줄러 경로에서 같은 "target 전체 up --build" 동작에 도달할 방법이 없음) 코드
전수 추적으로 확인했다. 리뷰어 2(테스트 품질/회귀 위험 담당)는 두 production 차단(C6c
전용·신규 전면 차단)이 서로 마스킹하지 않고 공존함을, 문서상 production에서 `ensure`에 실제로
의존하는 사용처가 없음을 확인했다. 두 리뷰 모두 새 raise 메시지가 "compatible-pair 워크플로"를
언급한 것을 지적했다 — 이 지점은 C6c target이면 이미 위에서 걸러지므로 항상 비-C6c target에서만
도달하는데, 그 대안은 이 지점에서 결코 적용되지 않는다. 메시지를 "manage this service directly
on the host instead"로 단순화했다. 테스트 하나에 실제로는 읽히지 않는(트랜잭션을 통째로 mock해
`os.environ`을 안 읽는) `monkeypatch.setenv` 호출이 남아 있던 것도 제거했다. 검증 단계(별도
에이전트, xhigh effort)에서 두 리뷰의 모든 구체적 주장을 코드에서 직접 재확인(CONFIRMED)했고
우회나 회귀는 발견되지 않았다 — "핵심 수정은 그대로 merge해도 안전하다"는 결론.

backend 1049 passed(기존 1047 + 신규 2), ruff 기존 9건 유지, 변경 파일 mypy clean. 백엔드 정책
변경만이고 UI 표면이 없어 실브라우저 E2E는 수행하지 않았다.

---

## 2026-07-28 (T-011 적대적 리뷰 반영 — credential 스캔 우회 2건, React key 포커스 유실)

T-011 구현 직후 적대적 리뷰어 2명(Agent 도구 병렬 실행, 이 시점 ultracode는 off라서 Workflow
대신 Agent를 썼다)이 리뷰했고, 수정 후 재검토에서 관련된 2차 공백을 추가로 발견해 총 2라운드로
막았다.

**1라운드 — 리뷰어가 찾은 것**

- **리뷰어 1(보안, confirmed)**: `_value_has_literal_url_credential`이 `_INTERPOLATION_BLOCK_RE`로
  `${...}` 블록을 통째로 지운 뒤 남은 부분만 스캔했다. `${FAKE_NAME:-literal-secret}`처럼 지어낸
  변수명으로 감싸기만 하면 스캔 대상 문자열 자체가 사라져 credential 검사를 통째로 우회했고,
  이미 `${REAL_VAR:-old}`로 보호되던 key를 `${FAKE_VAR:-new-secret}`로 바꾸는 것도 "여전히 보간
  형태"라 규칙 1(재보간 요구)만으로는 막지 못했다. 두 가지 구체적 우회 입력을 리뷰어가 제시했고,
  직접 재현 스크립트(shell 이스케이프를 피하려고 Python 파일로 작성)로 두 우회 모두 확인 후 수정:
  `_INTERPOLATION_BLOCK_RE`를 완전히 제거하고 raw 값을 그대로 스캔하되, `scheme://user:pass@`의
  password 캡처 그룹 자체가 `${VAR}` 보간인 경우만 예외로 인정한다. credential 스캔은 **baseline과
  완전히 같은 값**에만 예외를 준다(구조가 아니라 byte-동일성으로 게이트) — 그래야 지어낸 이름으로
  감싸는 우회가 통하지 않는다.
- **리뷰어 2(UX, pre-existing이지만 새 기능으로 새로 문제가 됨)**: ports/volumes/networks 행의
  React `key`에 필드 값 자체(`key={\`port-${idx}-${port}\`}`)가 들어 있어, 이번에 추가한
  "타이핑 중 즉시 검증" 기능과 만나면 매 keystroke마다 key가 바뀌어 DOM input이 재마운트되고
  브라우저 포커스가 사라졌다 — 새 기능이 사실상 타이핑 불가능했다. index 전용 key로 교체(행은
  추가/삭제만 되고 재정렬은 없어 안전). 그 외 whitespace로 인한 오탐 메시지(복붙 시 붙는 공백),
  `aria-describedby`/`aria-live` 접근성 공백도 함께 수정.
- 수정 후 mutation test(수정을 되돌리는 스크립트로 원래 취약한 구현을 재도입)로 새로 추가한
  회귀 테스트 4건만 실패하고 나머지 79건은 그대로 통과하는 것을 확인해, 테스트가 실제로 이
  수정에 의존한다는 것을 검증했다.

**2라운드 — 1라운드 수정 직후 재검토에서 재발견한 것**

1라운드 수정을 실브라우저로 재검증하려고 Grafana 컨테이너의 설정 모달을 열었을 때(실제
`docker-compose.yml`의 `GF_SECURITY_ADMIN_PASSWORD=${GRAFANA_ADMIN_PASSWORD:-admin}`을 보고),
리뷰어 1이 고친 credential 스캔(`_value_has_literal_url_credential`)이 `scheme://user:pass@`
**형태에만** 반응한다는 것을 재확인했다 — DSN이 아닌 단일 리터럴 비밀은 이 스캔의 대상이 아니다.
직접 재현: baseline `${GRAFANA_ADMIN_PASSWORD:-admin}`에 `${TOTALLY_MADE_UP_NAME:-h4x0r-literal-secret}`를
넣으면 규칙 1(재보간 요구)은 "여전히 `${...}` 형태"라는 이유로 통과시켰다 — `BYPASS CONFIRMED`.
1라운드에서 고친 것은 DSN류(연결 문자열)뿐이고, 이 저장소의 압도적 다수인 "단일 값 비밀"
(`PASSWORD`, `TOKEN`, `API_KEY` 등 하나의 리터럴 값)에는 같은 클래스의 우회가 그대로 남아 있었다.

- **수정**: 규칙 1(baseline이 보간이면 새 값도 보간이어야 한다)을 일반화했다. sensitive key
  (`_is_sensitive_key`)이면, 새 값이 여전히 `${...}` 형태여도 `:-default` 리터럴 자체를 baseline의
  default와 비교해서 **달라졌으면 거부**한다. 변수 이름이 바뀌었는지는 부수적이다 — 판단 기준은
  "새 리터럴이 git 추적 파일에 커밋되는가"이다. default를 완전히 없애는 것(`${OTHER_NAME}`, default
  없음)은 git에 아무것도 남기지 않으므로 허용하고, 변수 이름만 바뀌고 default 리터럴이 baseline과
  동일하면 허용하며, 비-sensitive key(포트 번호 등)는 이 검사의 대상이 아니라 기본값을 자유롭게
  바꿀 수 있다(과다 차단 방지).
- 새 검증 5건 추가(위조 변수명 거부, 같은 변수명 아래 새 리터럴 거부, 변수 이름만 바뀌고 default
  동일하면 허용, default 제거 허용, 비-sensitive key default 변경 허용) + 기존 DSN 우회 테스트 1건은
  rule 3만 단독 검증하도록 key 이름을 sensitive하지 않은 이름으로 바꿔 재구성. mutation test로
  새 테스트 2건만 실패 확인. backend 전체 1047 passed(기존 1042 + 5), ruff 기존 9건 유지. 프론트
  `configValidation.ts`에 동일 로직 미러링, type-check/lint/build 모두 통과(build는 `next dev`와
  동시 실행 시 `.next` lock 충돌로 정지하는 것을 발견 — dev 서버를 먼저 내리고 build를 단독
  실행해야 한다).
- **실브라우저 재검증**(WSL dev backend + frontend, admin 로그인 세션, 로컬 검증 전용 임시
  `KTDM_ADMIN_*`/`KTDM_SESSION_SECRET`/`KTDM_FRONTEND_ORIGINS`/`KTDM_CORS_ALLOW_ORIGINS` 환경변수 —
  `KTDM_CORS_ALLOW_ORIGINS`는 루트 `.env`의 prod 값이 `KTDM_FRONTEND_ORIGINS` 오버라이드보다
  우선하므로 둘 다 명시적으로 설정해야 했다): Grafana 컨테이너의 `GF_SECURITY_ADMIN_PASSWORD`
  필드에 `${TOTALLY_MADE_UP_NAME:-h4x0r-literal-secret}`를 한 글자씩 타이핑 → 인라인 오류 표시,
  diff 미리보기에 `before → after` 정확히 표시, "적용 및 재생성" 버튼 비활성화까지 확인. 같은
  세션에서 포트 필드에도 문자열을 이어서 타이핑해 React key 수정으로 값이 잘리거나 초기화되지
  않고 그대로 누적되는 것을 확인했다. 실제 제출은 한 번도 누르지 않아 로컬 컨테이너는 그대로
  유지했다.

---

## 2026-07-28 (T-011 설정 저장 validation 고도화 — diff 미리보기·baseline 인지 secret 방어)

- T-011의 남은 3개 항목(diff 표시, 포트/볼륨/네트워크 validation, secret 값 방어)을 마무리했다.
- **diff 미리보기**는 백엔드 호출 없이 프론트에서 계산한다. `configTargetContainer.config`(baseline)와
  현재 입력 상태를 비교해 포트·네트워크 추가/삭제, env 변경 전후 값을 모달에 실시간으로 보여 준다.
- **포트 validation**: `docker_service.validate_port_mapping`을 추가했다. 이 저장소의 모든 ports 항목이
  `${VAR:-12101}:${VAR:-12101}` 형태를 쓰므로(docker-compose.yml 전수 확인), `${...}` 보간 토큰은
  opaque하게 신뢰하고 리터럴 숫자만 1~65535 범위·host[:container] 형식을 검사한다. 실제 compose 파일의
  ports 18개·environment 244개 전체를 대상으로 "지금 저장해도 통과하는가"를 living regression test로
  고정했다(파일이 바뀌면 테스트도 같이 검증한다).
- **볼륨**: 이미 `compose_volume_graph_hash` 비교로 완전히 불변 처리되어 있어(임의 변경이 첫 mutation
  전에 409) 서버에 새 검증을 추가하지 않았다. 대신 프론트가 baseline과 비교해 변경을 감지하면 제출 전에
  경고하고 제출 버튼을 막아, 이미 서버가 거부할 왕복을 미리 차단한다.
- **secret 방어의 핵심 설계 결정**: 처음에는 T-012에서 확장한 `_is_sensitive_key`(정적 key-이름
  substring 목록)를 그대로 재사용하려 했으나, 실제 compose 파일 244개 env 전수 검증에서
  `KOR_TRAVEL_MAP_API_PUBLIC_API_KEY_REQUIRED=true`가 오탐으로 걸렸다 — 이름에 "API_KEY"가 들어가지만
  원래부터 리터럴 불리언 값이다. 정적 key-이름 휴리스틱은 READ(T-012의 inspect 마스킹, 과다 redaction이
  안전한 방향)에는 맞지만 WRITE(이 작업, 오탐이 실제 기능을 막는 회귀)에는 너무 거칠다는 것을 실측으로
  확인했다. **baseline이 이미 `${...}` 보간이었는지**를 기준으로 바꿨다 — 이미 `.env`로 분리돼 있던
  참조를 리터럴로 되돌리는 것만 막고, 원래부터 리터럴이던 값(불리언 flag, DB 이름 등)은 건드리지 않는다.
  baseline을 모르는 경우(오늘의 UI에서는 발생하지 않는 신규 key 추가 시나리오)에만 안전한 쪽으로
  key-이름 휴리스틱을 fallback으로 쓴다.
- key 이름과 무관하게, 값 안에 literal 접속 자격증명(`scheme://user:pass@`)이 `${...}` 밖에 남아 있으면
  별도로 거부한다 — `..._PG_DSN`, `..._DATABASE_URL`처럼 이름이 평범해도 값에 비밀번호가 박혀 있을 수
  있다(T-012 리뷰가 READ 경로에서 지적한 것과 같은 클래스의 위험을 WRITE 경로에서도 막는다). 다만
  `${OUTER_VAR:-postgresql://user:dev_pw@host/db}`처럼 자격증명 전체를 하나의 `${...:-default}` 기본값
  안에 두는 이 저장소의 기존 관행(모든 DSN류 값이 이 형태)은 통과시킨다 — `${...}` 블록을 통째로 지운
  뒤 바깥쪽에 literal credential이 남아 있는지만 본다.
- 검증은 lock 획득이나 Docker 접근보다 먼저 실행한다(`update_container_config` 진입 직후, compose 파일을
  읽는 로컬 YAML 읽기만 하고 lock은 잡지 않는다). mutation 테스트로 이 순서를 직접 확인했다: 검증 호출을
  제거하면 lock/환경 스냅샷 mock의 `AssertionError`가 먼저 터져 테스트가 실패한다.
- 프론트에는 같은 규칙을 미러링한 `configValidation.ts`/`configDiff.ts`를 추가해 왕복 없이 즉시
  피드백한다(서버가 최종 게이트이므로 프론트가 놓쳐도 보안 문제는 아니고 UX 품질만 낮아진다). 필드별
  인라인 오류, 볼륨 불변 경고, 오류가 있으면 제출 버튼 비활성화까지 실브라우저로 확인했다.
- **로컬 실브라우저 검증**(WSL 백엔드 + dev 프론트, 실제 컨테이너는 절대 재생성하지 않고 모달만 조작):
  잘못된 포트 형식 → 인라인 오류 + 제출 비활성화, `POSTGRES_PASSWORD`를 리터럴로 바꾸면 baseline 인지
  거부 메시지, 무해한 `POSTGRES_DB` 변경은 diff 패널에 정확히 표시, 볼륨 변경 시 불변 경고, 잘못된
  네트워크 이름 거부까지 모두 확인. 제출은 한 번도 누르지 않아 로컬 인프라는 그대로 유지했다.
- backend 1035 passed(신규 validation 테스트 다수 + negative control로 검증 순서 확인), ruff 기존 9건
  유지, 프론트 type-check/lint/build 통과.

---

## 2026-07-28 (T-012 적대적 리뷰 반영 — 비밀 노출·포커스 탈취 수정)

- **적대적 리뷰 2명이 각각 실제 비밀 노출 경로를 찾았다.** 이 패널은 inspect를 UI에
  연결한 최초 지점이라, API/CLI에만 있던 redaction 공백이 브라우저 클릭 한 번으로 열렸다.
  - key 이름 누락: `SENSITIVE_KEY_PARTS`에 `API_KEY`가 없어 `ACCESS_KEY`에 안 걸렸다.
    `.env.example`에서 실제로 `*_API_KEY` 5개(OpiNet·KREX EX/GO·concierge·VWorld)를 확인.
    `API_KEY`/`APIKEY`/`CREDENTIAL`/`PASSWD`를 추가했다.
  - 값 내부 credential: key 이름이 뭘 걸어도 값 자체에 `postgresql+asyncpg://user:pw@host`
    형태로 비밀번호가 박힌 DSN은 못 잡는다(`PINVI_DATABASE_URL`, `KOR_TRAVEL_MAP_PG_DSN`,
    `KTG_PG_DSN` 등). key 전체를 가리는 대신 URL userinfo의 비밀번호 구간만 정규식으로
    치환해, `..._BASE_URL`처럼 비밀 없는 URL은 그대로 읽을 수 있게 했다.
  - `cmd`/`entrypoint`는 그동안 어떤 필터도 거치지 않았다. 지금 compose에는 credential이
    없지만(모두 environment로 주입) 이미지 내장 CMD나 `mc alias set ep access secret`
    관용구가 통로가 될 수 있어 같은 redaction을 적용했다.
  - 실행 중 10개 컨테이너 전수 검사(로컬 실브라우저 + fetch)로 유출 0건을 확인했다.
    `KTG_PG_DSN`은 `postgresql+psycopg://addr:<redacted>@127.0.0.1:5432/kor_travel_geo`로
    나온다.
- **포커스 탈취**: 열릴 때 포커스 이동 effect가 `[onClose]`에 묶여 있었는데, `onClose`는
  부모의 인라인 화살표라 WS broadcast(2초)마다 새 identity를 받아 effect가 재실행되고
  포커스가 닫기 버튼으로 계속 끌려갔다. 마우스 검증으로는 안 드러나는 키보드/스크린리더
  결함이었다. 한 번만 포커스하는 effect와 ref로 최신 `onClose`를 읽는 별도 keydown
  effect로 분리하고, 부모의 콜백도 `useCallback`으로 고정했다. 7초간(broadcast 3회분)
  포커스가 유지되는 것을 확인했다.
- **`ensure --build`가 라벨보다 훨씬 넓은 범위를 건드렸다.** `ensure_target`은
  depends_on 폐포 전체를 재생성한다 — `prom` 5개, `map` 12개, `pinvi` 18개(전체 스택).
  확인 없이 클릭 한 번으로 실행됐고, db가 포함되면 스키마·권한 복구 스크립트까지 돈다.
  실행 전 실제 대상 서비스 목록과 개수를 보여 주고 확인을 받게 했다.
- **target 매칭 comment가 사실과 달랐다.** `containers`를 "직접 소유"라고 적었지만
  실제로는 depends_on까지 펼쳐진 목록이라 통합 PostgreSQL이 db·geo·conc·map·pinvi·all
  여섯 target 모두에 들어 있다. 첫 매치를 쓰면 `dependency_order`가 좁은 것부터 나열돼
  있다는 우연에 기댄 것이었다(`all`은 18개 담아 순서가 바뀌면 클릭 한 번이 전체 스택
  재생성이 된다). 순서와 무관하게 가장 좁은 target을 고르도록 고쳤다.
- **`assert_manager_mutation_allowed`가 production을 막는다는 코드 주석이 틀렸다.** 직접
  읽어 확인 — 그 함수는 환경 선언의 정합성만 검증하고 문자열을 돌려주며,
  `assert_c6c_mutation_allowed`는 대상이 C6c runtime(Map 4종·pinvi-api)과 안 겹치면 그냥
  반환한다. 즉 db·storage·gra·cadv·prom·geo·conc는 production에서도 통과한다. 현재
  유일한 방어선은 프론트 `NODE_ENV` 빌드 타임 제거뿐임을 주석에 정직하게 남기고, 서버측
  차단은 T-044로 분리했다.
- 그 외: `aria-controls`가 활성 탭 하나만 유효한 dangling IDREF였던 것을 고정 id로
  통일, tabpanel에 `tabIndex=0`과 탭 좌우/Home/End 키 이동 추가, 미생성·오프라인
  컨테이너의 상세 버튼 비활성화(500 에러 대신), raw FastAPI JSON을 사용자 문구로 교체,
  running 중 `종료 코드 0`으로 오독되던 표시 제거.
- backend 990 passed(신규 redaction 테스트 다수 포함, negative control로 구 predicate가
  9건 실패함을 확인), ruff 기존 9건 유지, 프론트 type-check/lint/build 통과.

---

## 2026-07-28 (대시보드 inspect 상세 패널 — T-012)

- 백엔드 `GET /containers/{id}/inspect`는 T-010부터 있었지만 프론트에서 호출하는 코드가
  0건이었다. 이미 mounts·networks·healthcheck·redact된 env를 모두 반환하고 있어, 이번 작업은
  기존 계약에 UI를 배선하는 일이었다.
- `ContainerDetailModal`을 별도 파일로 분리했다. `DashboardClient.tsx`가 이미 1,400줄대라
  탭 5개를 그 안에 넣으면 리뷰가 어려워진다.
- target 매칭은 registry가 **직접 소유한 `containers`**를 쓴다. `resolved_services`는
  depends_on까지 펼쳐지므로 상위 target이 잘못 잡힌다(예: postgres가 `pinvi`로 매칭).
  실제로 `db`가 정확히 잡히는 것을 브라우저에서 확인했다.
- `ensure --build` 버튼은 `NODE_ENV !== 'production'` 가드로 개발 빌드에만 노출한다.
  운영 빌드에서는 번들에서 분기가 죽고, 서버도 production mutation 차단으로 거절하므로 이중이다.
- **로컬 실브라우저 검증**(WSL 백엔드 + dev 프론트, Windows Chromium). 18개 row 전부 상세
  버튼 노출, 5개 탭이 실데이터 렌더 — 마운트 rw/ro, 네트워크 2개의 IP/GW/MAC/alias,
  healthcheck `healthy`와 최근 검사 로그, **env 비밀값 `<redacted>`**. Esc 닫기 동작, 콘솔
  오류 0건(로그인 전 401·기존 favicon 404 제외).
- 반응형: 390×844에서 page 가로 스크롤 0, 모달이 viewport 내부, 넓은 Mounts 표와 탭 목록은
  각자 컨테이너 안에서만 가로 스크롤. 1440×900도 동일.
- `ensure --build` **클릭은 실행하지 않았다.** 로컬 `db` target은 실데이터가 마운트된
  PostgreSQL을 재생성하므로 확인 없이 누를 대상이 아니다. 버튼 렌더·target 매칭·title까지
  확인했고, 클릭 경로는 기존 `POST /targets/{target}/ensure`를 그대로 호출한다.
- 검증 중 확인한 환경 특성: WSL에서 `cleanup_old_log_files`가 `/mnt/f`(9p)를 스캔해 기동이
  약 2분 걸린다. 코드 문제가 아니라 마운트 성능이며, 운영(n150 로컬 디스크)은 20~35초다.

---

## 2026-07-28 (WS 인가 동시성 상한 + 프론트 배포 preflight — T-043)

- T-042 리뷰가 남긴 두 항목을 처리했다. accept-then-close 계약상 미인증 peer도 handshake를
  완료하는데 WS 라우트에는 제한이 없었고, 운영 호스트의 `frontend/node_modules`가 부분 설치
  상태(최상위 패키지는 있는데 `.bin`이 비어 `next: not found`)로 남아 있었다.
- 동시 인가 handshake를 `KTDM_WS_MAX_PENDING_AUTHORIZATIONS`(기본 64, 프로세스당)로 묶고
  초과분을 `close(1013)`으로 흘려보낸다. **per-IP 제한은 쓰지 않았다** — 이 배포의 공개
  트래픽은 전부 리버스 프록시 IP 하나로 도착해(신뢰 프록시 CIDR이 loopback 전용, 운영
  로그에서 실제로 모든 외부 접속이 라우터 IP로 관측됨) per-IP 버킷이 인터넷 전체를 한 키에
  묶어 정상 관리자까지 막는다.
- **적대적 리뷰 2명이 측정으로 최초 근거 자체를 반증했다. 정직하게 기록한다.**
  - 위협 모델이 틀렸다: 미인증 peer는 SQLite에 도달하지 못한다. `validate_session_cookie`는
    쿠키가 없으면 session을 열기 전에 `None`을 돌려주고 DB SELECT는 HMAC 서명 검증 뒤에
    있다(측정: DB session 0건, 호출당 0.2~2.6us). "거절마다 DB 조회가 잡힌다"는 서술을
    코드·문서·env에서 모두 걷어냈다.
  - shed 경로가 완화하려던 경로보다 비쌌다(blocker). 거절 1건마다 `logger.warning`을 부른
    탓이다 — 측정 거절당 1039~1207us, 로그 제거 시 57~62us, 4401 경로 285~313us.
    attacker가 제어하는 무제한 동기 디스크 write이기도 했다. edge-triggered로 바꿨다.
  - `uvicorn --limit-concurrency` 권고를 철회했다. h11 구현이 WebSocket upgrade를 503 검사
    **이전에** return하므로(`h11_impl.py:221-230`) WS에는 발동하지 않는다. 연결 수 제한은
    HAProxy `maxconn`/stick-table로 안내한다.
  - 테스트 공백: counter 증가/감소를 통째로 지워도 970건이 전부 통과했다. 실제 인가 중
    counter가 오르고 그 사이 요청이 shed되는지, 취소(BaseException)에서 slot이 반납되는지를
    검증하는 테스트를 추가하고 mutation에서 정확히 그 테스트만 실패함을 확인했다.
  - preflight가 부분 설치를 통과시켰다 — 막으려던 바로 그 장애다(`next`·`typescript`는
    있고 `react`가 없는 트리). `npm ls --depth=0`을 결정적 게이트로 바꿨다.
- 그 밖에 재인가 deadline jitter(배포 재기동 후 위상 고정 해소), 1013 시 폴백 폴링
  5초→30초(5초 폴링은 요청마다 전체 docker sweep이라 shed가 부하를 되레 키운다),
  심볼릭 링크 경로 해석·인자 검증·`--fix` 파괴성 경고를 보완했다.
- backend 970 passed, ruff 기존 9건 유지, 프론트 type-check/lint/build 통과. PR #76.

---

## 2026-07-28 (C7 WebSocket accept-then-close 종료 코드 계약 — T-042)

- `kor-travel-map`의 C7 WebSocket 작업(`T-ADM-C7W` issue #806/PR #807, `T-VN-H11` issue #809)을
  참조해 매니저 WebSocket 로직을 점검한 결과, **Map이 고친 것과 같은 결함이 그대로 있었다.**
  `ws_status`/`ws_logs`의 거절이 `accept()` 이전에 `close(4401)`을 호출해 uvicorn(0.28.1 legacy
  `websockets_impl`)이 HTTP 403 handshake 거절로 바꿔 보냈고, 브라우저는 `4401`이 아니라 `1006`만
  관측했다. 즉 종료 코드 계약이 어떤 실제 client에도 도달하지 않았다.
- 기존 `test_ws_status_requires_session`은 이를 잡지 못했다. Starlette TestClient는 ASGI
  `websocket.close`를 그대로 되던져 pre-accept close와 accept-then-close를 **모두** 같은
  `WebSocketDisconnect(4401)`로 보고한다 — 거짓 통과였다. 계약은 `test_ws_contract.py`에서
  ASGI 메시지 시퀀스(`accept` → `close`)로 고정했고, 구 동작 negative control에서 6건이 실패함을
  확인했다.
- `C-2`(subprotocol echo)는 이식하지 않았다. 매니저는 쿠키 인증이라 client가
  `Sec-WebSocket-Protocol`을 보내지 않고, RFC 6455는 제시되지 않은 protocol 선택을 금지한다.
- 같은 handler의 확인된 결함도 함께 정리했다: idle container에서 client 종료 미검출로 reader
  thread/docker socket 누수, 소진된 stream의 무한 polling, accept 후 close 없는 return,
  event loop 위의 blocking docker/SQLite 호출, 연결 0건에도 도는 docker sweep, 살아 있는 소켓의
  세션 재검증 부재(logout·TTL 미적용).
- **적대적 리뷰 2명 × 2라운드.** 1라운드는 양쪽 모두 REQUEST_CHANGES였고, 재인가가
  `asyncio.wait` timeout 기반이라 프레임마다 창이 리셋돼 keepalive를 보내는 client가 logout·TTL을
  무한 우회하는 것을 probe로 재현했다(19회 기대 → **0회**). 프론트 `attempt`를 `onopen`에서
  리셋해 지수 backoff가 무력화된 것, `ensure_ascii=False` 회귀도 함께 잡혔다. 2라운드에서는
  테스트 모듈이 import 시점에 공유 env를 덮어써 34건이 실패하는 blocker를 추가로 발견했다.
- 리뷰어 간 상충 1건(로그 스트림 thread 누수)은 직접 확인해 **기각**했다. docker-py 7.1.0의
  `logs(stream=True)`는 generator가 아니라 `CancellableStream`을 반환하고 그 `close()`가
  `sock.shutdown(SHUT_RDWR)`으로 park된 worker를 깨운다. n150에서 로그 소켓을 16회 열고 닫아
  hang 0건·pool이 정확히 8에서 유지되는 것으로 재확인했다.
- **n150 운영 HAProxy TLS 엣지 경유 실브라우저 E2E**로 계약을 검증했다. 미인증
  `/ws/status`·`/ws/logs` 모두 `code=4401, reason=AUTH_REQUIRED, wasClean=true, data frame 0건`,
  인증 후 알 수 없는 container_id는 `4000/INVALID_CONTAINER_ID`로 구분됐다. 로그인→대시보드
  (18개 컨테이너, `REALTIME WS SYNC`)→로그아웃 전환도 정상이고, **로그아웃 뒤 20초간 WS 재연결
  시도 0건**으로 과거의 403 무한 재시도 루프가 사라진 것을 확인했다.
- **settle window 기본값은 실측으로 정했다.** 같은 엣지에서 `0.25`는 10/10, `0.0`은 12/12 모두
  4401이었고 1006은 한 번도 없었다(거절 왕복 264~791ms → 79~373ms). uvicorn legacy
  `websockets_impl`이 `websocket.close`를 `handshake_completed_event` 뒤에 처리해 101과 close가
  서버 단에서 이미 직렬화되기 때문이다. Map의 `0.25`는 `websockets-sansio` 기준이라 이 스택에
  이식되지 않는다. 기본값을 `0.0`으로 두고 `KTDM_WS_ACCEPT_CLOSE_SETTLE_SECONDS` 조절 knob은
  남겼다 — **uvicorn ws 구현이나 프록시 토폴로지 변경 시 재측정 필요.**
- backend 956 passed, ruff 기존 9건 유지, 프론트 type-check/lint/build 통과.

---

## 2026-07-20 (C7 Map features routes production 결선 착수 — T-040)

- n150 C7 attestation에서 Manager production Map API runtime에
  `KOR_TRAVEL_MAP_API_FEATURES_ROUTES_ENABLED`가 없어 검증이 중단되는 원인을 확인했다. Map image의
  미설정 기본값은 `true`라 feature 관리 REST는 동작했지만, review된 Manager source가 production
  노출을 명시 승인했다는 사실을 runtime에서 증명할 수 없었다.
- issue #70과 ADR-25를 먼저 기록했다. Manager canonical Compose의 Map API에만 exact `true`
  literal을 두고, C6c raw/resolved/runtime 보호 환경 계약이 누락·값 변경·다른 service나 channel의
  이름 유출을 첫 mutation 전에 거부하도록 구현한다.
- 최신 main `b3bcb83`에서 독립 branch/worktree를 만들고 CodeGraph 영향도를 확인했다. 변경은 공통
  candidate 검증과 compatible-pair capture/deploy/rollback 최종 runtime 검증에 모두 도달한다.
- canonical Compose와 공통 production literal 집합을 정렬하고 누락·`false`·API 외 유출을 raw,
  resolved, runtime 각각의 `false`·누락 음성 회귀로 고정했다. focused 42개, C6c·Docker config
  849개, backend 전체 907개 테스트가 통과했다.
- 변경 source strict mypy와 Ruff의 기존 `E721`/`UP038` 기준선을 제외한 변경 파일 검사가 통과했고,
  공개 placeholder만 사용한 canonical Docker Compose `config --quiet`도 통과했다.

## 2026-07-20 (C6c PinVi login SSR shell 오탐 수정 착수 — T-039)

- n150 C6c capture status2에서 PinVi Web은 healthy이고 `/admin/login`도 200·`text/html`·비어 있지 않은
  body·일반 Next.js static marker·`admin/login` 전용 page chunk를 반환했지만, raw HTML에
  `data-testid="admin-login-form"`이 없다는 이유만으로 shell smoke가 실패했다.
- exact PinVi page는 `Suspense fallback={null}` 아래 client component를 hydration하므로 SSR shell에
  form이 없는 것이 정상이다. HTTP shell smoke는 status/content/body와 일반 static marker에 더해
  route-specific page chunk를 요구해 generic fallback을 fail-close하고, 실제 form과 로그인 상호작용은
  최종 n150 Playwright가 소유하도록 경계를 정렬한다.
- C4e main `c4e7cad`에서 T-039 독립 branch/worktree를 만들었다. 문서-only draft PR을 먼저 게시한 뒤
  exact 구현 diff를 같은 단일 적대적 reviewer가 승인하기 전에는 test/lint를 실행하지 않는다.
- CodeGraph depth 4에서 직접 호출은 active-contract 검증과 단위 테스트 두 곳이고, 상위 영향은
  capture/deploy/rollback의 공통 full-contract 검증으로 확인했다. 로컬 exact PinVi production build의
  `admin/login.html`도 form 0개·Next static marker·`(admin)/admin/login/page-<hex>.js` chunk를 확인했다.
- 구현 diff는 exact route script `src` regex와 form 유무 양성 2건, non-200/non-HTML/empty/generic/다른
  route 음성 5건으로 제한했다. 동일 단일 reviewer 승인 전 정책에 따라 test/lint는 실행하지 않았다.
- 단일 reviewer의 P2 두 건에 따라 content type prefix 비교를 exact `text/html` media type token
  비교로 바꾸고 `text/html-fallback` 음성 fixture를 추가했다. 운영 문서도 실제
  `(admin)/admin/login/page-<hex>.js` route chunk와 exact하게 맞췄다. 코드 재리뷰 전에는 test/lint를
  계속 실행하지 않는다.
- 같은 단일 reviewer가 수정된 exact head `e5a5835`를 재검토해 P0~P2 없음으로 승인했다. 그 뒤에만
  gate를 실행했고 focused auth smoke 13개, C6c 전체 806개, backend 전체 894개가 통과했다.
- pytest 기본 임시경로가 Windows 사용자 Temp를 가리킨 최초 실행은 capture 임시 파일 오류로 0개를
  수집했고, C6c 전체의 최초 재시도도 NTFS 권한 비트 안전성 검사에서 실패했다. WSL `/tmp`를 명시한
  동일 범위 재실행은 모두 통과해 코드 실패가 아닌 실행 환경 기준선임을 확인했다.
- 변경 source Ruff와 기존 import 정렬 기준선을 제외한 C6c test Ruff가 통과했다. strict mypy는 공용
  venv에 없는 `types-PyYAML` stub만 제외하고 변경 source에서 통과했다. 대형 기존 파일 전체의 import
  정렬·format 기준선은 이 수정과 무관하므로 섞지 않았다.

## 2026-07-20 (Map destructive production 명시 로컬 검증 완료 — T-038)

- Map standalone compose가 파괴 작업 kill-switch를 기본 `false`로 내리면, Manager가 운영 Map API의
  승인된 파괴 작업을 명시적으로 결선해야 한다. image 기본값이나 host 환경의 우연한 상속은 쓰지 않는다.
- Manager canonical compose의 Map API에만 exact `true` literal을 두고 raw/resolved/runtime C6c 보호
  환경 계약과 compatible-pair v4·C7 environment hash에 결박한다. Dagster·UI·PinVi 등 다른 service에는
  이름과 값이 없어야 한다.
- enablement 증거는 source/runtime attestation이 소유하고, 실제 delete/restore/swap 사용의 actor는
  Map API가 인증 principal에서 기록한다. 단일 적대적 정적 리뷰에서 P0~P2 없음 판정을 받은 뒤 canonical
  Docker config fixture 누락 한 곳을 exact `true`로 정렬했다.
- WSL ext4 임시 디렉터리에서 C6c·Docker config focused `839 passed`, backend 전체 `897 passed`를
  확인했다. 변경 범위는 Ruff 0.3.7의 기존 `I001` 8건을 제외한 gate와 변경 source strict mypy를
  통과했고, 공개 placeholder만 사용한 production `docker compose config --quiet`도 통과했다.

## 2026-07-20 (C6c Map UI 통합 경로 smoke 수정 착수 — T-037)

- 최종 Map UI 로그인은 200과 `Set-Cookie`를 반환했지만, C6c가 clean-cut된
  `/ops/providers`를 보호 페이지로 조회해 404로 compatible-pair capture를 차단하는 현상을
  n150에서 재현했다.
- provider 운영 화면의 통합 정본인 `/ops/datasets`를 login `next`, 로그인 후 보호 GET,
  logout 후 재차단 GET의 단일 경로로 사용하고 회귀 테스트·Docker 관리 문서를 함께 정렬한다.
- 단일 적대적 리뷰에서 P0~P2 없음 판정을 받은 뒤 focused 800개·backend 전체 888개 테스트,
  Ruff 0.3.7 baseline 제외 gate와 변경 source strict mypy를 통과했다. PR 병합 뒤 같은 exact
  source compatible-pair capture로 재검증한다.

## 2026-07-20 (C7 PinVi Dagster image 계약 drift 수정 착수 — T-036)

- n150 C7 verified-compatible capture의 PinVi dependent bootstrap에서 `pinvi-dagster`가
  기동하지 못하는 blocker를 재현 로그와 exact source 계약으로 좁혔다.
- PinVi exact source revision의 image는 `DAGSTER_HOME=/opt/pinvi/.dagster`와
  `pinvi.etl.definitions`를 정본으로 사용하지만 manager canonical Compose가 각각 존재하지 않는
  `/opt/dagster/dagster_home`과 과거 `tripmate.etl.definitions`로 덮어쓰고 있었다.
- T-036은 두 override와 resolved Compose 회귀 계약만 최소 수정한다. 구현은 단일 적대적 리뷰
  승인 전까지 테스트를 실행하지 않고, 승인 뒤 로컬 gate와 n150 compatible-pair capture로 검증한다.

## 2026-07-20 (C7 Map production env 로컬 검증 완료 — T-035)

- 같은 적대적 리뷰어가 marker 단조성, source 전체 scalar tree, tracked `env_file` object
  identity와 test fixture 보강까지 exact head별로 재검토했고 최종 P0~P2 차단점이 없음을 확인했다.
- Python 3.12에서 backend 886개 테스트가 모두 통과했다. 변경 파일은 Ruff 0.3.7 범위 검사와
  `types-PyYAML`을 포함한 strict mypy를 통과했고, 공개 dummy 값으로 기본·커스텀 Compose
  `config --quiet`도 모두 통과했다.
- 저장소 main 자체의 Ruff/mypy 누적 오류는 별도 baseline으로 분리했다. 이번 변경 파일 Ruff는
  기존 exact-type 검사(`E721`, `UP038`)를 명시적으로 보존했고, 새 source 두 파일은 strict mypy
  suppression 없이 통과했다.
- PR #64는 merge commit `3f9973806e8addff96eb1339602f992ed424fb1c`로 `main`에 병합됐다.
  issue #63은 계속 열어 두며, final n150 v4 exact-pair의 startup/readiness, runtime secret
  isolation, cAdvisor health와 C7 live E2E가 끝난 뒤에만 닫는다.

## 2026-07-20 (C7 Map production env 적대적 리뷰 P1 보강 — T-035)

- CodeGraph depth 4 재점검에서 현재 UI auth preflight가 candidate build/recreate 전에 실행되어,
  base runtime에 새 admin proxy env가 없으면 첫 전환과 rollback이 모두 Docker mutation 0회에서
  순환 차단됨을 확인했다.
- compatible-pair v4 shape는 유지하면서 manifest active pair의 exact `map_source_revision`에서 Map
  source `docker-compose.yml`을 읽는다. active/rollback이 모두 알려진 source env v3인 최초 전환
  window에서만 현재 UI admin proxy의 없음/frozen exact를 허용한다. source env v4가 한 번 기록되면
  이후 v3 rollback도 필수 exact이며 candidate와 activation 후 runtime의 결선은 완화하지 않았다.
- `.env.example`에 공개된 admin/service/cursor local placeholder 세 값은 production config와
  raw/resolved candidate에서 명시적으로 거부하고 local에서만 허용하도록 보강했다. 재리뷰 승인
  전 정책에 따라 test/lint/Compose gate는 아직 실행하지 않았다.
- 두 번째 재리뷰에서 active/rollback 두 slot만으로 v4 이력의 단조성을 증명할 수 없고 source Compose의
  다른 scalar path에 보호 placeholder를 복제할 수 있음을 확인했다. manifest v4 exact shape는 유지하고
  sibling marker에 최초 v3/v3 logical hash를 pending으로 원자 고정한 뒤 성공한 activation/runtime
  isolation/전체 smoke 후 complete로만 전환한다. complete는 rollback/rotation이 낮출 수 없으며
  A3→B4→rollback A3→C3 뒤에도 누락을 거부한다.
- source classifier는 profile/public/debug/service/admin/cursor 이름과 placeholder를 전체 scalar tree에서
  exact path/count로 검사한다. API·Dagster·daemon `env_file`의 path/options shape와 exact commit에
  추적된 참조 파일 내용도 고정하고, 다른 service/build/label/command/config/secret 유출 fixture를
  추가했다. 재리뷰 승인 전이므로 test/lint/Compose gate는 계속 실행하지 않았다.

## 2026-07-19 (C7 Map production API env 구현 준비 — T-035)

- 수정 전 CodeGraph로 `C6cDeploymentConfig`, config loader, raw candidate validator, resolved
  secret isolation의 depth 4 영향도를 확인했다. 배포·캡처·롤백과 공용 fixture가 모두 직접
  영향권이므로 config·Compose·raw/resolved/runtime 검사를 한 변경으로 정렬했다.
- canonical Compose에 production/public-key/debug/metrics와 host-network loopback trusted proxy
  CIDR literal을 고정했다. admin proxy secret은 Map API+UI BFF exact pair, service token과 cursor
  signing secret은 Map API-only로 결선하고 모두 manager `.env`에서 hard-require한다.
- 세 신규 secret의 32자 이상·Unicode 공백 금지·기존 ops/UI/smoke credential 포함 상호 구분을
  mutation 전에 검증한다. 다른 service의 environment/env_file/build arg/config/secret/label/command
  경로와 runtime metadata로 이름 또는 값이 유출되는 경우를 거부하는 음성 fixture를 보강했다.
- 구현 diff와 보안 점검 뒤 동일 적대적 리뷰어에게 넘길 준비 상태다. 리뷰 승인 전 정책에 따라
  test/lint/Compose gate와 n150 live 검증은 아직 실행하지 않았다.

## 2026-07-19 (C7 Map production API env 결선 착수 — T-035)

- Map PR #782 교차 적대 리뷰에서 manager main이 ops principal만 전달해 새 production image의
  admin/service/public/debug/metrics 불변식을 만족하지 못하고 startup 전에 fail-close하는 P1을
  확인했다. 이어지는 PR #780의 cursor signing secret도 같은 final cutover에 포함한다.
- issue #63과 ADR-23을 만들고 admin proxy는 Map API+UI BFF, service/cursor secret은 Map API-only,
  profile/public/debug는 canonical literal로 고정했다. 인증된 Prometheus scrape 결선 전에는 metrics
  endpoint를 명시적으로 끈다.
- 다음 단계는 문서 선행 commit 뒤 canonical Compose와 C6c raw/resolved/runtime preflight·음성
  fixture를 구현하고, 같은 단일 적대적 리뷰어 승인 전에는 test/lint/Compose gate를 실행하지 않는 것이다.

## 2026-07-19 (PR #61 리뷰 차단 보강 설계 — T-033/T-034)

- PR #61 리뷰에서 raw Compose에는 있던 Map UI·Dagster web·Dagster daemon provenance가
  resolved 검증, snapshot build, candidate inspection, activation·rollback에서 누락돼 기존
  `development` image가 계속 기동될 수 있는 P1 경로를 확인했다.
- Map API만 기록하던 compatible-pair v3 대신 Map runtime 네 immutable image ID와 공통 clean
  source revision을 모두 기록하는 v4 clean-cut을 결정했다. capture/deploy/rollback은 Map
  runtime 네 service와 PinVi API를 같은 frozen transaction으로 build·재생성·검증하고,
  복원 실패 시 전체를 중지한다.
- cAdvisor는 raw exact listen argument와 default/custom resolved health URL이 같은 port인지
  확인하는 회귀 계약을 추가한다. 구현 뒤 동일 리뷰어 재검토 전에는 test·lint·Compose
  config를 실행하지 않는다.
- manager 구현은 manifest v4, 다섯 service snapshot build·activation·rollback·halt와 관련
  회귀 계약까지 작성했다. Map main의 C7 attestation runner도 동반 PR #778에서 v4 9-field
  pair와 네 Map role image ID 비교로 동기화하며, manager PR은 이 선행 계약에 의존한다.
- 교차 재리뷰에서 canonical v4 파일이 없고 과거 기본 `compatible-pair-v2.json`만 있는 host를
  빈 state로 오인하는 경로와 ADR-20의 과거 v3·두 API 지침이 현행 ADR-21과 충돌하는 문제를
  확인했다. 저장소 역사에 실제 존재한 v2/v3 sibling은 payload를 신뢰하지 않고 fail-close하며,
  ADR-20의 배포 결과는 ADR-21의 v4·다섯 runtime transaction이 대체함을 명시한다.

## 2026-07-19 (C6c cAdvisor healthcheck 포트 drift 확인 — T-034)

- n150 production의 canonical compose는 cAdvisor를 `CADVISOR_PORT`(기본 `12301`)로
  정상 기동하고 해당 포트의 `/healthz`도 응답했지만, image에서 상속된
  healthcheck는 `8080`을 계속 조회해 container를 `unhealthy`로 판정했다.
- C6c bootstrap의 base-service readiness가 이 판정을 fail-close해 compatible-pair
  capture가 중단되었고, 계약에 따라 Map·PinVi API는 정지 상태를 유지했다.
- issue #62와 T-034는 cAdvisor listen·healthcheck가 같은 `CADVISOR_PORT`를 사용하게
  고정하고, 정상 health 확인 후 capture를 한 번만 재시도하는 작업으로 분리한다.

## 2026-07-19 (C7 Map UI·Dagster provenance 누락 확인 — T-033)

- n150 production 후보를 clean Map commit에서 빌드한 뒤 OCI label을 확인한 결과 Map API는
  exact revision을 가졌지만 Map UI·Dagster web·Dagster daemon은 Dockerfile 기본값
  `development`를 유지해 C7 runtime attestation을 통과할 수 없음을 확인했다.
- 원인은 세 compose service가 Dockerfile에 선언된 `KOR_TRAVEL_MAP_GIT_COMMIT` build arg를
  전달하지 않는 wiring 누락이다. issue #60으로 기록하고 실제 container 기동 전에
  candidate를 중단했다.
- T-033은 Map runtime 네 image가 같은 canonical source commit을 사용하도록 compose와
  계약 테스트를 정렬하고, n150 exact-image label 및 C7 attestation으로 완료한다.

## 2026-07-19 (T-032 C7 image provenance 완료·아카이브)

- docker-manager PR #58을 `ecaab504e63a99cb757318d3b67337bec962d90b`로 squash merge했다.
- clean HEAD→Git archive context→exact Compose build mapping→OCI label→compatible-pair manifest v3
  결박과 상위 C7 n150 production gate 완료를 반영해 T-032를 `tasks-done.md`로 옮겼다.
- 세션 상태 정본인 `CLAUDE.md`를 최종 merge 상태로 갱신했다. 이 저장소에는 별도
  `docs/resume.md`가 없다.

## 2026-07-19 (C7 C6c image source provenance fail-close 착수 — T-032)

- production `pinvi-pair capture/deploy --build`가 Map·PinVi 각 build context의 exact Git root,
  clean worktree, lowercase 40자 `HEAD`를 host-wide lock 안에서 파생·재검증하도록 설계했다.
- 적대적 사전 리뷰에서 live worktree build의 변경·원복 TOCTOU와 ignored 파일 혼입 위험을
  P1로 확인해, 실제 Docker build input을 각 exact `HEAD`의 일회성 Git archive context로 교체했다.
- 후속 리뷰에서 external Dockerfile·additional context가 snapshot을 우회할 수 있음을 확인해
  raw/resolved build mapping 전체와 snapshot 내부 Dockerfile 경로를 exact allowlist로 고정했다.
- Map의 `KOR_TRAVEL_MAP_GIT_COMMIT`, PinVi의 `PINVI_SOURCE_REVISION`/
  `PINVI_BUILD_ENVIRONMENT=production`을 canonical Compose build arg로만 전달하고, 사용자
  명시 값·resolved arg·source wiring drift를 첫 container mutation 전에 거부하도록 했다.
- 각 API build/recreate 직후 smoke보다 먼저 immutable image의
  `org.opencontainers.image.revision`을 검사하고 PinVi는
  `io.pinvi.build.environment=production`도 강제했다.
- compatible-pair를 v3 clean-cut해 active/rollback 각 pair에 두 image ID, 두 source revision,
  contract generation, recorded time을 exact 필수 필드로 보존했다. provenance가 없는
  v1/v2는 자동 전환하지 않으며 capture/deploy/rollback/smoke 결과도 image ID↔revision을
  함께 반환한다.
- 같은 단일 리뷰어가 두 P1 보강 뒤 새 P0/P1/P2 없음과 `ACCEPT FOR TESTS`를 확인했다. WSL
  Docker Python 3.13에서 C6c focused `597 passed`, backend 전체 `685 passed`, 변경 source strict
  mypy와 Ruff를 통과했다. production Compose도 `config --quiet`과 resolved exact build mapping을
  통과했다. Python 3.13 tarfile의 3.14 기본 filter 변경 안내 2건만 남고 기능 실패는 없다.

## 2026-07-19 (C6c Map API provider runtime clean-cut 정렬 — T-031)

- n150 migration 전 비파괴 preflight에서 Manager compose가 Map에서 제거된 provider credential env 9개를
  빈 값까지 API에 주입해 exact Map entrypoint가 fail-close하는 계약 drift를 확인했다.
- 제거된 env를 Map API compose에서 삭제하고 provider credential은 Dagster·daemon 수집 경계에만 남겼다.
- raw candidate·resolved candidate·최종 resolved C6c contract가 해당 이름과 제거된 live-preview flag의
  존재 자체를 API 기동 전에 거부하도록 회귀 guard와 테스트를 추가했다.
- legacy data.go.kr credential 잔여 주입도 제거하고 Map API `command`·`entrypoint` override를 세 검증
  경계와 runtime inspect에서 금지해 immutable image의 migration과 entrypoint fail-close 우회를 차단했다.
- migration·credential rotation·container/API/manifest 변경은 이 수정 PR 머지와 재검증 전까지 중지했다.

## 2026-07-19 (C6c Map UI 인증 fail-close 계약 보강 — T-031)

- Map UI runtime username·PBKDF2 hash·session secret을 기본값 없는 compose 보간과 정확한 Map UI Env
  경로로 고정하고, manager-only 평문 smoke 비밀번호가 container에 주입되지 않는 계약을 문서화했다.
- raw/resolved compose, runtime inspect, active-pair frozen recovery transaction에서 누락·변조·다른 서비스
  노출·평문 주입·live environment drift를 거부하는 회귀 테스트를 추가했다.
- 공식 차단 리뷰에 따라 첫 API stop 전에 current Map UI exact runtime 인증과
  login→protected→logout→reblock을 검사하고, 모든 Unicode whitespace session secret·credential
  repr/result/error 누출·필수 Map UI 서비스 부재를 거부하는 테스트와 운영 순서를 보강했다.
- local gate에서 Docker Compose resolved JSON이 literal `$`를 `$$`로 표현하는 경계를 확인해,
  resolved compose 비교는 escaped representation을 허용하되 current/final runtime은 raw exact 값을
  유지하고 잘못된 dollar 수와 비허용 경로 복제를 거부하는 회귀 테스트를 보강했다.
- 공식 리뷰 승인 뒤 ext4에서 C6c targeted 테스트 `541 passed`, backend 전체 테스트 `599 passed`,
  strict mypy와 신규 lint `0`, production Docker Compose config/resolved guard를 통과했다.
  n150 production cross-repo smoke와 실제 UI 로그인 검증은 아직 남아 있어 T-031은 진행 중으로 유지한다.
- n150 read-only preflight에서 일반 scalar의 username 문자열 일치를 confidential value leak으로 오인한
  false-positive를 mutation 없이 확인했다. username은 exact Map UI wiring/runtime equality만 강제하고,
  ops token·PBKDF2 hash·session secret·평문 credential만 전역 scalar isolation 대상으로 유지하도록 회귀
  테스트와 운영 문서를 보강했다. 공식 리뷰 승인 뒤 ext4에서 C6c targeted 테스트 `528 passed`, backend
  전체 테스트 `616 passed`, strict mypy와 신규 lint `0`, production Docker Compose `config --quiet` 및
  resolved guard `2/2`를 통과했다. root 권한이 필요한 n150 Map UI 비밀번호 회전, cross-repo smoke와
  실제 UI 로그인 검증은 아직 남았다.

## 2026-07-19 (C6c closed transaction 회귀 검증 — T-031)

- pass17~19의 frozen compose transaction, candidate/baseline 분리, 동일 transaction 복구 계약에 맞춰
  이전 테스트 fixture를 갱신했다. production guard를 우회하거나 실제 manager 경로 검증을 약화하지
  않고, 테스트마다 frozen root/active recovery transaction을 명시적으로 주입했다.
- C6c/Docker config focused 테스트 `395 passed`, backend 전체 테스트 `453 passed`,
  `c6c_deployment.py` strict mypy와 변경 파일 Ruff를 통과했다.
- n150 production 배포와 live UI/API E2E는 아직 수행하지 않았으므로 T-031은 진행 중으로 유지한다.

## 2026-07-18 (Map↔PinVi C6c ops principal 배포 결선 착수 — T-031)

- Map canonical ops clean-cut 뒤 PinVi가 삭제된 legacy endpoint를 호출하던 문제를 복구하기 위해,
  서비스 간 principal을 `ops:read`와 import-job `ops:cancel` 두 capability로 분리한다.
- token은 manager의 gitignore된 `.env`를 단일 source로 사용하되 map API와 PinVi API에만 각각
  전달한다. Map Dagster·daemon·UI와 PinVi Web·Dagster에는 전달하지 않는다.
- 일반 write token은 schedule·refresh policy·update request까지 불필요하게 열기 때문에 두지
  않는다. cancel token은 exact import-job cancel endpoint에만 결박하며, 단일 고정 PinVi 주체를
  위해 DB credential 수명주기를 추가하지 않는다.
- 구현 전 완료 조건을 `docs/tasks.md` T-031과 ADR-20에 먼저 기록했다. 이후 compose 계약 테스트,
  compatible image pair 배포/rollback, n150 read·cancel·거부 smoke와 로그인 검증까지 수행한다.
- 적대적 리뷰에서 production mode 누락 시 local+빈 token으로 부팅되는 fail-open, public liveness만으로
  Map과 PinVi를 동시에 올리는 순서, host-network bind/publish port 혼동, mutable tag rollback,
  gitignore된 override의 secret leak 검사 공백을 확인했다.
- `KTDM_DEPLOYMENT_ENVIRONMENT`와 `PINVI_ENVIRONMENT`를 명시적으로 일치시키고 production은 Map
  `KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED=true`까지 요구한다. 두 token은 32자 이상·모든 공백 없음·
  상호 다름을 container 변경 전에 검사한다.
- 최초 구현은 production `ensure pinvi`를 dependency 1회 → Map API wait → signed read 200 envelope/
  무토큰 401/import-job cancel 404/non-cancel mutation 403 → PinVi API wait → 나머지 앱 순서로
  분리했다. 아래 적대적 재리뷰에서 일반 ensure를 폐쇄하고 mixed pair 없는 전용 deploy로 보강했다.
- merged compose와 prod override의 environment/env_file/command/build args를 실제 token 값까지 검사하고,
  기동 뒤에는 모든 managed container를 inspect해 Map API와 PinVi API 외 노출을 거부한다.
- `ktdctl pinvi-pair capture --verified-compatible`와 `ktdctl pinvi-pair rollback`을 추가했다. manifest는
  manager state 디렉터리에 두 immutable image ID를 mode 0600으로 원자 기록하며,
  단일 image·moving tag rollback 표면은 제공하지 않는다. 실제 테스트와 n150 배포 검증은 아직 남았다.
- C6c 적대적 재리뷰의 P1을 반영해 production 일반 `ensure`/container action·config·reset/direct Compose
  경로의 API mutation을 중앙 차단하고, 전용 `ktdctl pinvi-pair deploy` capability만 허용했다. deploy,
  capture, rollback은 preflight부터 manifest commit/복구까지 같은 host filesystem lock을 잡는다.
- manifest를 generation 포함 v2로 올렸다. merged compose의 두 API host network, PinVi production mode,
  Map bind port, 정확한 loopback base, container identity, manager-only smoke credential와 `env_file` 격리,
  두 immutable image override를 mutation 전에 검증한다. local token opt-out도 두 값이 모두 빈 경우로
  제한했다.
- mixed pair 창을 없애기 위해 기존 PinVi API를 먼저 quiesce하고 Map smoke → PinVi admin 로그인과
  ETL/provider-sync 200 envelope·typed cancel 오류/`Retry-After` → remaining app `--wait` → Map UI
  로그인/보호 `/ops/providers`/로그아웃과 PinVi Web shell → runtime inspect 순으로 승격한다.
- 어느 중간 단계나 rollback 검증이 실패해도 manifest를 갱신하지 않고 배포 시작 시점 active pair의 두
  image를 함께 복구해 전체 계약을 재검증한다. 복구가 불가능하면 두 API를 중지하고
  `halted_requires_operator`를 반환한다. 이 보강의 테스트 실행·n150 live 검증은 아직 하지 않았다.
- 최종 적대적 리뷰를 반영해 generic API mutation guard가 mode/required/token pair를 공통 검사하도록
  닫고, Compose 분류는 알려진 read-only만 허용하며 `scale`·`watch`·알 수 없는 명령을 default-deny한다.
- pair deploy/rollback은 dependency·UI·Dagster를 build/recreate하지 않는다. 비-API 필수 서비스는 변경
  없이 running/healthy를 검사하고 두 API만 `--no-deps`로 변경·복구한다. final/recovery runtime 검증도
  `ps --all` 존재 여부가 아니라 필수 서비스의 실제 running/healthy 상태를 요구한다.
- clean/legacy v1용 capture를 host-lock bootstrap transaction으로 강화했다. candidate Map → signed smoke
  → PinVi → 전체 smoke 성공 뒤에만 최초 v2를 기록하고 실패하면 두 API를 모두 중지한다. PinVi owned
  cancel fixture는 정확한 409/502/503 code/details/retryability와 양의 `Retry-After`만 허용하며 429와
  generic 오류는 거부한다. 요청에 따라 이 보강 직후 테스트는 아직 실행하지 않았다.
- 최종 차단 리뷰에서 local manager와 PinVi mode의 실제 계약을 `local → development`로 바로잡고,
  모든 Compose/Docker SDK/config 파일 mutation이 공통 mode/token guard와 재진입 가능한 host lock을
  공유하도록 닫았다. config recreate/init 실패는 compose 파일의 원래 byte를 원자 복원하고 기존 runtime
  재생성까지 시도한다.
- clean capture는 빈 host에서도 base dependency → Map API/signed smoke → Map dependents → PinVi API/
  canonical smoke → PinVi dependents로 전체 topology를 구성한다. 실패 시 기존 container를 삭제하지 않고
  transaction이 만든 container만 정리한다. rollback/recovery도 Map 복원·signed smoke 뒤 PinVi를 복원해
  혼합 pair를 실행하지 않는다.
- PinVi ETL/provider 응답은 `data: null`을 거부하고 실제 DTO 핵심 shape를 검사한다. Web 200도 admin login
  form과 Next build marker가 모두 있어야 한다. runtime inspect는 Env뿐 아니라 Cmd/Entrypoint/Labels와 모든
  안전 scalar에서 secret 이름·값 누출을 차단한다.
- manifest/lock을 checkout-independent Compose project 상태 디렉터리로 옮기고 relative/noncanonical/
  cross-project production override를 거부한다. manifest 원자 replace 뒤 부모 디렉터리까지 fsync한다.
  이 차단 리뷰 반영 뒤에도 신규 적대적 리뷰 2명 승인 전에는 테스트·lint를 실행하지 않는다.
- 신규 1차 적대적 리뷰에서 발견한 `wait --down-project=true` 분류 우회, raw Env 중복에 가려지는 secret,
  offset 없는 PinVi datetime, REST 500에서의 config/runtime 복원 진단 유실을 보강했다. 회귀 테스트는
  추가했지만 2명 승인 전 실행 금지 원칙에 따라 아직 실행하지 않았다.
- pass3 적대적 차단 리뷰 8건을 반영했다. clean bootstrap은 실제 init 예외도 created-only cleanup으로
  수렴하고, `wait --down-project=*`는 service 인자와 무관하게 project-wide guard/lock을 사용한다. production
  state root/파일명은 project별 단일 경로로 고정했다. Map/PinVi DTO와 owned cancel member, manifest
  version/recorded_at을 fail-closed로 강화했으며 parent fsync 실패 시 이전 manifest를 복원한다. config restore와
  미생성 start fallback은 subprocess 진단을 REST까지 보존한다. 회귀 테스트는 작성만 했고 신규 리뷰 2명 승인
  전까지 실행하지 않는다.
- pass4 적대적 차단 리뷰 3건을 반영했다. Map dataset-grid의 canonical 필드를 `execution_coverage`로
  바로잡고 production Map bind/PinVi base URL을 정확히 `12701`로 고정했다. Map dataset row와 PinVi
  repository/asset/schedule/sensor 배열 원소를 실제 DTO shape까지 검사하며 `[null]`과 잘못된 nested 원소를
  거부하는 회귀 fixture를 추가했다. 신규 적대적 리뷰 2명 승인 전이므로 테스트·lint·build는 실행하지 않았다.
- pass5 적대적 차단 리뷰 3건을 반영했다. Map tokenless/cross-token/non-cancel capability 음성 smoke는 HTTP
  status와 RFC7807 code를 함께 검사한다. PinVi destructive cancel은 transaction state로 정확히 한 번만
  호출하고 첫 증거를 deploy/bootstrap/final verification/recovery에서 재사용하며 uncertain 결과에는 재요청하지
  않는다. cancellation attempt/member/Dagster run의 전체 datetime·structured error·lifecycle·commit 보존
  DTO와 canonical 409 root-only shape를 회귀 fixture로 고정했다. 신규 적대적 리뷰 2명 승인 전이므로
  테스트·lint·build는 실행하지 않았다.
- pass6 cancel DTO 정렬을 반영했다. full 409은 unresolved count 0, resolved root+unresolved child,
  transient all-resolved topology를 허용하되 count와 member 상태를 정확히 맞춘다. retryable은 모든 failed
  member/run의 exact run-backed `cancel_failed`와 retryable error를 요구하고 `already_terminal` 대체를
  거부한다. in-progress/definitive CAS drift의 member `cancel_failed`+run `cancelled` canonical 전이는
  허용한다. actual `409 PIPELINE_CANCELLATION_UNSAFE`+`failed`와
  `503 DAGSTER_TERMINATION_TIMEOUT`+`retryable` pair도 고정했다. 회귀 fixture만 작성했으며
  테스트·lint·build는 실행하지 않았다.
- pass7에서 failed attempt의 retryable run-backed/definitive mismatch 혼재, status-error-finished DB lifecycle,
  retry subset lineage, frozen termination flag와 engine timestamp를 actual Map/PinVi 정본에 맞췄다.
  `Retry-After` header presence와 양의 정수 parsing을 분리하고, Compose `kill -s/--signal` 값 소비 및
  service-less/project-wide·unknown option default-deny fixture를 추가했다. 신규 적대적 리뷰 전이므로
  테스트·lint·build는 실행하지 않았다.
- pass8에서 Compose 옵션을 command별로 분리해 `build --pull`, `run --rm`, `rm -s/--stop`이 다음 service를
  값으로 소비하지 않게 했다. `config -o/--output`의 분리·inline·누락 형식은 write-capable mutation으로
  host lock과 capability를 요구하고, `--format json` 등 명시한 read-only 형식만 무변경으로 허용한다.
- PinVi cancel detail은 in-progress runless 실패를 definitive code로 한정하고, run-backed member/run 오류
  policy group, retryable exact evidence, resolved member와 Dagster terminal mapping을 현재 Map/PinVi 정본에
  맞췄다. feature-load root의 failed/SUCCESS 예외는 동일 run의 `provider_feature_load` child 증거가 있을
  때만 허용한다.
- `KTDM_C6C_CONTRACT_GENERATION`을 manager-only 보호값으로 올려 resolved compose scalar, non-root
  `env_file`, runtime Env를 포함한 모든 container 주입을 거부한다. bootstrap 정리 명령이 예외를 내도
  예외를 외부로 흘리지 않고 operator-required 상태로 수렴한다. 회귀 fixture만 작성했으며 신규 적대적
  리뷰 2명 승인 전이므로 테스트·lint·build는 실행하지 않았다.
- pass9에서 `Retry-After` parser를 ASCII `[0-9]+`와 1..300 범위로 고정했다. 부호, 앞뒤 공백,
  Unicode digit, 0, 301 이상은 header가 존재해도 parsing 실패로 처리한다.
- rustfs 같은 non-API config update/reset 및 미생성 start-create도 candidate raw compose 전체를 먼저
  검사한다. exact Map/PinVi API environment interpolation 외의 environment·label·command·build scalar에서
  ops/manager 보호 이름이나 현재 보호값을 참조하면 거부하고, non-root `env_file` 내용의 alias 값도 검사한다.
  거부는 compose 파일 쓰기와 container recreate 전에 발생하며 REST는 typed 409
  `COMPOSE_CANDIDATE_PROTECTED_REFERENCE`, `mutation_applied=false`를 반환한다. 정적 fixture만 보강했고
  테스트·lint·build는 실행하지 않았다.
- pass10 적대적 차단 리뷰 4건을 반영했다. candidate raw/resolved 검사를 compose 전체 graph와 top-level
  secret/config 외부 파일로 확장하고, API raw wiring은 suffix까지 canonical exact로 고정했다. `env_file` 경로는
  Compose 변수 연산자와 `$$`를 명시 해석하되 중첩·미완성 문법은 fail-close한다. generic ensure/up/create/
  recreate와 config prewrite가 검증 전에 Docker/file mutation을 실행하지 않도록 중앙 gate를 연결했고 candidate
  오류는 REST typed 409 detail을 보존한다. 지시에 따라 테스트·lint·build는 실행하지 않았다.
- pass11 적대적 차단 리뷰를 반영했다. volume short/long bind source를 보간·canonicalize해 root `.env`, manager
  state 파일, 보호 이름·현재 값이 든 파일의 relative/absolute/traversal/symlink/`:ro` 우회를 raw/resolved 모두
  차단했다. Windows-looking source는 fail-close하고 named volume은 host file 검사에서 분리했다. 내용 확인이
  불가능한 external secret/config alias reference도 빈 exact allowlist 밖에서 거부하며 rustfs config REST의
  typed 409와 compose/container mutation 0 fixture를 고정했다. 지시에 따라 테스트·lint·build는 실행하지 않았다.
- pass12 적대적 차단 리뷰 2건을 반영했다. manager 파일 ancestor·state directory·host root bind는 먼저
  거부하고, directory bind를 서비스별 canonical source/target allowlist로 닫았다. missing source와 oversized/
  unreadable/non-regular file은 Docker 자동 생성·mutation 전에 fail-close한다. cAdvisor의 `/:/rootfs`, `/var/run`,
  `/var/lib/docker`, `/dev/disk` mount를 제거하고 Docker socket+`/sys`, `--docker_only=true`로 축소했다. RustFS
  REST typed 409와 source/compose/container mutation 0 fixture를 보강했으며 같은 리뷰어 재승인 전이므로
  테스트·lint·build는 실행하지 않았다.
- pass13 적대적 리뷰의 volume/TOCTOU 지적을 반영했다. 운영 DB·RustFS·Geo·Prometheus·Grafana bind를 별도
  migration 없이 유지하되 config API의 top-level/service volume graph를 pre-request compose와 exact immutable로
  고정했다. internal/default named volume만 허용하고 bind-capable local driver option, unknown driver/option,
  external alias를 raw/resolved에서 거부한다. cAdvisor short/long RO access와 `/sys` mountpoint, root:docker `0660`
  socket, root-owned parent chain의 inode/device/mode snapshot을 mutex 안에서 compose write·Docker subprocess 직전
  재검증한다. mismatch의 write 전 차단/compose byte 복원과 REST typed 409 mutation 0 fixture를 보강했으며 같은
  리뷰어 재승인 전이므로 테스트·lint·build는 실행하지 않았다.
- pass14 차단 리뷰를 반영해 mutex 안에서 persisted/request의 raw·resolved volume graph를 각각 exact 비교하고,
  include/extends/`COMPOSE_FILE`/추가 override를 거부하는 single-file mutation 경계를 고정했다. cAdvisor는 raw
  literal과 resolved identity 모두 RO `/sys`·Docker socket exact set만 허용하고 named-volume raw alias와
  resolved project-name drift를 차단한다. 첫 mutation 성공 후 다음 preflight가 drift하면 원래 오류와 복구
  진단을 typed 500으로 보존하고 compose byte/mode와 persisted runtime을 best-effort 복구한다. reset/API
  no-mutation, Docker/ensure direct/API post-mutation fixture를 추가했으며 같은 리뷰어 재승인 전이므로
  테스트·lint·build는 실행하지 않았다.
- pass15 차단 리뷰 4건만 반영했다. mutation command에서 override 탐색을 제거하고 subprocess 직전
  single-file 경계를 재검증한다. `ensure` recovery는 최초 compose byte/mode를 원자 복원하고 동일
  raw/resolved hash·system snapshot을 재검증한 뒤에만 baseline runtime을 재생성하며, 실패 시 Docker recovery를
  실행하지 않는다. preflight drift 원본 복원이 실패해 durable config mutation이 남는 경우도 원래 candidate
  오류와 복구 진단을 typed 500으로 보존한다. CLI fixture는 완전한 validation DTO로 고쳤고 테스트·lint·build는
  재승인 전 금지에 따라 실행하지 않았다.
- pass16에서 Compose interpolation TOCTOU를 닫았다. transaction 시작 시 `.env` 존재 여부·byte와
  device/inode/mode/uid/gid, process env를 합친 effective mapping을 비밀값 비노출 snapshot으로 고정하고
  raw/resolved 검증·mutation·recovery 전체에서 재사용한다. mutation subprocess는
  `--env-file /dev/null`과 frozen env만 받고, 직전 `.env` 생성·삭제·내용·identity drift는 typed contract
  오류와 Docker subprocess 0으로 중단한다. direct/API no-mutation과 frozen env/recovery identity fixture를
  보강했으며 재승인 전 금지에 따라 테스트·lint·build는 실행하지 않았다.
- pass17에서 production mutation lock을 project state에서 분리한 사용자 단일 전역 경로로 고정했다. lock을
  잡은 뒤 manifest 경로와 root `.env`, canonical compose source, external `env_file` graph·byte·identity를
  한 번만 capture하고 pair deploy/capture/rollback 및 recovery 전체에 같은 transaction snapshot을 전달한다.
  외부 입력은 exact `{path, required, format}` list만 허용하며 top-level secret/config file source는
  fail-close한다. resolution은 외부 byte를 익명 fd로만 제공하고, mutation은 original project directory에서
  완전 해석된 compose JSON을 `-f -` stdin으로 실행해 relative bind/build 의미와 secret 비노출을 함께
  보존한다. 최초 mutation 뒤 source/external 계약 drift는 같은 snapshot으로 복구 또는 두 API halt를 시도하고
  원래 오류와 복구 진단을 typed post-mutation 오류에 남긴다. 지시에 따라 추가 fixture는 중단했고
  테스트·lint·build 없이 정적 호출부와 diff만 확인했다.
- pass18에서 live env/source drift 뒤에도 복구 가능한 frozen resolved stdin 경계를 추가하고, config 변경의
  baseline/candidate transaction을 분리해 원본 오류·halt 증거와 exact 원본 복원을 보존했다.
- pass19에서 첫 mutation 전에 manifest active SHA를 root frozen source/env/external/system 입력으로 resolve한
  recovery transaction을 별도 생성해 deploy/rollback과 legacy capture 실패 복구에만 사용하도록 분리했다.

---

## 2026-07-14 (Map OpiNet·KREX provider 키 compose 보간 drift 수정 — T-030)

- manager `.env`는 현재 Map 계약인 `KOR_TRAVEL_MAP_OPINET_API_KEY`와
  `KOR_TRAVEL_MAP_KREX_EX_API_KEY`를 사용하지만 base compose가 과거 `KRTOUR_MAP_*` source를
  읽어, 값이 있어도 Dagster·Dagster daemon에 빈 문자열을 전달하는 반복 장애의 원천을
  확인했다. KREX GO key와 두 provider의 map API live preview key도 함께 점검했다.
- OpiNet·KREX EX·GO 공통 key를 현재 이름에서 명시 보간하되 실제 수집기를 실행하는
  Dagster·Dagster daemon에만 주입한다. API에는 resolved live preview 변수만 주입하고, 별도 값이
  없을 때 각각 OpiNet 공통 key와 EX key를 compose interpolation source로 재사용한다. 실제 secret은
  코드·문서·테스트에 넣지 않고 gitignore된 루트 `.env` 한 곳에만 둔다.
- 계약 테스트가 수집 서비스의 공통 key와 API 전용 preview key 경계를 분리하고 API에 원본 공통
  key가 없음을 고정한다. `.env.example`의 빈 placeholder 각 1건, 백엔드 focused test·Ruff,
  placeholder를 사용한 `docker compose config --quiet` 및 current/fallback/blank resolved-value 검증을
  통과했다.

---

## 2026-07-13 (Concierge DB read 키 단일 source 주입·운영 전환 — T-029)

- `KOR_TRAVEL_MAP_KOR_TRAVEL_CONCIERGE_API_KEY`를 manager 루트 `.env`의 단일 secret source로
  정의하고, base compose가 실제 fetcher를 실행하는 Dagster·Dagster daemon에 동일하게 주입하도록
  했다. 사용하지 않는 map API에는 read secret을 주입하지 않는다.
- Concierge feature base URL도 두 서비스에 같은 계약으로 주입해 prod override의 중복 literal 없이
  `/api/v1/features/{snapshot,changes}`를 호출할 수 있게 했다.
- `.env.example`에는 빈 placeholder와 DB `read` scope 발급 원칙만 기록했다. 구현 PR에서는 실제
  prod `.env`와 `docker-compose.override.yml`을 변경하지 않고 n150 전환 단계에서만 적용했다.
- 계약 테스트가 두 서비스의 source 식과 `.env.example` key 정의 1건을 고정한다. n150 Python 3.11
  일회성 컨테이너에서 백엔드 테스트 40개와 Ruff를 통과했고, n150의 Docker Compose로
  `config --quiet` 보간도 통과했다. 로컬 테스트는 실행하지 않았다.
- n150에서 Concierge DB를 Alembic head `20260713_0017`로 올리고 scope migration
  `20260713_0016`의 `scope NOT NULL`, `read|admin` CHECK와 soft-delete schema를 확인했다. UI
  재생성 뒤 admin 해시/session secret 비어 있지 않음, 로그인 POST 200+`Set-Cookie`, BFF settings
  200, 잘못된 비밀번호 401을 확인했다. 재생성 직후 준비 구간에서 일시 503이 한 번 있었으나
  준비 완료 후 같은 전체 검증을 재실행해 통과했다.
- DB `read` 키를 발급해 DB에는 해시와 발급 감사 기록만 남기고, manager `.env` 한 곳에만 주입했다.
  override의 기존 key 세 줄과 base URL 세 줄을 제거하고 Dagster·Dagster daemon을 재생성했다. 과거
  환경변수를 확실히 제거하기 위해 map API도 한 번 재생성했으며, map API에는 read key가 없고 두
  수집기 컨테이너 값은 `.env`와 같음을 값 비노출 constant-time 비교로 확인했다.
- snapshot과 changes 각각 `limit=1` 2페이지 cursor 검증을 수행한 뒤 `page_size=200`으로 전체를
  순회했다. 두 모드 모두 8페이지, 1,416건이었고 cursor 진행·export ID 무중복 조건을 통과했다.
  실제 Dagster 컨테이너 수집기도 snapshot/changes 각 1,416건을 반환했다.
- BFF/operator static admin 키를 old/new overlap으로 교체하고 UI·BFF를 검증한 뒤 old를 제거했다.
  최종 old admin 401, new admin 내부 GET 200, read 공급 GET 200, read 내부/write 403과 로그인 POST
  200+`Set-Cookie`를 확인했다. 성공 후 key/cookie 임시 파일, 제한권한 백업, migration 복원
  지점을 삭제하고 관련 서비스 최근 로그에 오류가 없음을 확인해 T-029를 완료했다.

---

## 2026-06-28 (PinVi public API URL·CORS origin 환경변수화 — T-027)

- PinVi live mutating E2E 재검증 중 public Web origin의 `/auth/login` preflight가 `400 Bad Request`로 거부되는 배포 drift를 확인했다. 원인은 manager compose가 `PINVI_CORS_ALLOWED_ORIGINS`를 로컬 origin으로만 고정하고, Web build/runtime API URL도 로컬 API 기본값으로만 선언하던 것이다.
- `PINVI_PUBLIC_API_URL`과 `PINVI_CORS_ALLOWED_ORIGINS`를 `.env` 주입값으로 받도록 바꾸고, dev 기본값은 기존 로컬 `127.0.0.1:12801`/`12805` 계약으로 유지했다. prod 실제 도메인은 gitignore된 `.env`에만 둔다.
- 검증: `docker compose config -q`로 compose 보간/문법을 확인했고, PinVi 쪽 live mutating E2E는 이 변경을 운영 compose에 동기화한 뒤 재실행한다.

---

## 2026-06-28 (PinVi API worker 기본값 환경변수화 — T-026)

- PinVi live WebSocket mutating E2E에서 운영 배포의 `pinvi-api`가 `uvicorn --workers 2`로 고정되어 process-local broadcast broker가 worker 간 전달을 하지 못하는 문제를 확인했다. HTTP mutation과 WebSocket 연결이 서로 다른 worker에 배정되면 같은 trip의 변경 broadcast가 누락될 수 있다.
- `docker-compose.yml`의 `pinvi-api` command를 `--workers ${PINVI_API_WORKERS:-1}`로 바꾸고, 환경변수와 `.env.example`에 `PINVI_API_WORKERS=1` 기본값을 추가했다. shared broker 도입 전에는 worker 1이 안전한 운영 계약임을 compose 주석과 아키텍처 문서에 남겼다.
- 검증: `docker compose config -q`로 compose 보간/문법을 확인했고, PinVi 쪽 live mutating E2E는 이 변경을 배포한 뒤 재실행한다.

---

## 2026-06-26 (배포 런북 + push 전 보안 감사 절차 — concierge 스타일 정렬 — T-025)

- 반복된 prod 배포 실수와 민감 운영 정보를 `docs/deploy-runbook.local.md`(gitignore `*.local.md`, 커밋 금지)에 상세 기록했다. 형제 프로젝트 `kor-travel-concierge`의 `deploy-runbook.local.md` 스타일에 맞춰 §0 접속 테이블 / §1 ★최우선 반복실수(heredoc 명령 깨짐·curl-only 검증 함정) / §2 그 외 함정 / §3 표준 절차 / §4 셀프 체크리스트 / §5 푸시 전 추가 스캔(grep)+`git check-ignore` 자기검증 구조로 작성. concierge 런북의 교차 내용(이 repo가 소유한 `docker-compose.override.yml` env_file 사일런트 스킵, `docker compose config`/`.env` 시크릿 평문 덤프 주의, OPNsense 라우터 HAProxy)도 반영. prod 비번이 커밋된 테스트 값과 동일하다는 노출도 명시(변경 권고).
- 보안 감사는 concierge와 동일하게 **문서화 절차**로 정렬(전용 script/hook 미도입 — 처음 만들었던 `scripts/security-audit.sh`·`.githooks/pre-push`·`core.hooksPath`는 concierge에 없어 제거): `AGENTS.md`에 "## prod 배포 & 보안 감사" + "### remote 푸시 전 보안 감사(필수 절차)"(스테이징 파일 점검·`git diff --cached | grep` 일반 비밀 스캔·런북 §5 프로젝트별 패턴·.env.example placeholder·덤프 혼입 점검) 추가, DO NOT #13(보안 감사 생략 금지)·#14(배포 후 브라우저 검증 생략 금지) 추가, 작업 전 필독 목록에 런북 참조 추가.
- 런북은 git으로 전파되지 않으므로 각 worktree(`-codex`, `-codex-pr38`)에 수동 복사.

---

## 2026-06-24 (로그아웃/세션만료 시 LoginScreen 전환 회귀 수정 — T-024)

- 공개도메인 브라우저 E2E(Playwright)에서 발견: **로그아웃(또는 세션 만료) 후 대시보드가 LoginScreen으로 전환되지 않고** "통신 연결 오류" 배너 + 401 폴링 루프에 멈추는 회귀. 원인은 T-020(PR #37) FE-2에서 401 처리를 하드 리로드 → `auth-me` 쿼리 무효화로 바꾼 것: react-query가 refetch 에러 시 직전 성공 데이터(`authenticated:true`)를 유지해 `isAuthenticated`가 false로 내려가지 않는다(기존 하드 리로드는 전체 상태를 리셋해 우회했었음).
- 수정 1: `auth-me` queryFn이 401을 throw하지 않고 `{authenticated:false}`로 반환하도록 변경. 미인증을 유효 상태로 취급 → 로그아웃/만료 시 `isAuthenticated=false` → 리로드 없이 LoginScreen 즉시 전환(FE-2 의도대로 동작). 초기 미로그인 로드도 동일.
- 수정 2(브라우저 E2E로 추가 발견): 상태 WebSocket의 `onclose` 재연결이 무조건 `setTimeout(connectWS)`을 걸어, 로그아웃 시 서버가 WS를 닫으면 effect cleanup 이후에도 재연결이 스케줄돼 LoginScreen에서 **403 WS 핸드셰이크 무한 재시도 루프**가 돌았다. `cancelled` 플래그를 추가해 cleanup/언마운트 이후에는 재연결하지 않도록 수정(미인증 시 WS 시도 0건).
- 검증: 프론트 `type-check`·`build`, prod 배포 후 브라우저 로그아웃→LoginScreen 전환 + WS 루프 정지(콘솔 에러 누적 중단) 확인.

---

## 2026-06-24 (concierge PR #127 참고: 공개도메인 Secure 쿠키 보강 — T-023)

- 형제 프로젝트 `kor-travel-concierge` PR #127(공개 도메인 로그인 403 INVALID_ORIGIN — 운영 TLS 종단 프록시(라우터 HAProxy)가 `X-Forwarded-Proto: https` 미주입 → same-origin 재구성이 http가 돼 https Origin과 불일치 → 신뢰 origin 화이트리스트로 보완)를 참고해 동일 계열 문제를 점검·보강했다.
- 이 repo의 origin(CSRF) 검사는 concierge와 달리 **헤더 재구성이 아니라 화이트리스트(`KTDM_FRONTEND_ORIGINS`) 대조** 방식이라 **로그인 403 버그가 없음**을 실제 공개도메인 브라우저 E2E(Playwright, `https://manager.…`)로 확인했다(로그인→대시보드 18컨테이너·WS 실시간 동작, `me 401(초기)→login 200→me 200`).
- 다만 동일한 프록시-proto 문제로 `_is_https`가 내부 http로 판단해 **세션 쿠키 `Secure` 플래그가 누락**되는 약점이 남아 있었다. `_is_https`를 보강: 신뢰 `X-Forwarded-Proto`/직접 https가 아니어도 **브라우저 Origin이 설정된 https 공개 origin(`allowed_frontend_origins`)과 일치하면 https로 간주**해 `Secure`를 부여한다. 브라우저 Origin을 화이트리스트와 대조하므로 안전하고, LAN http origin은 영향 없으며 prod `.env` 변경이 필요 없다(기존 allowlist 재사용).
- 단위 테스트 추가(https 공개 origin→True, http LAN→False, 미등록 https→False, 직접 https→True). 검증: 백엔드 `ruff`(클린)·`pytest`(39 passed), prod 배포 후 실제 브라우저 로그인 재검증.

---

## 2026-06-24 (prod 풀 라이브 e2e + Retry-After 버그 수정 — T-022)

- n150(prod)에서 docker 컨테이너를 변경하지 않는 범위로 풀 라이브 e2e(63→65 케이스: health·unauth 게이트·CORS·RBAC(컨테이너 무변경)·로그인 음성/검증·next sanitize·인증 읽기전용·감사·키 lifecycle·WebSocket·세션 보안·AUTH-6 레이트리밋·프론트)를 수행했다. stdlib urllib + websockets 기반 e2e 스크립트(`/tmp/prod_e2e.py`, repo 미커밋)로 venv python 실행.
- 라이브 e2e가 실제 버그 1건을 발견: **로그인 429(rate limited) 응답에 `Retry-After` 헤더가 누락**. 원인은 주입된 `response` 객체에 헤더를 설정한 뒤 `HTTPException`을 raise하면 그 헤더가 응답에 반영되지 않기 때문(PR #36 원본). `HTTPException(headers={"Retry-After": ...})`로 전달하도록 수정하고 회귀 테스트를 추가했다.
- e2e의 다른 2건 실패는 시스템 정상 동작 확인(테스트 기대 수정): (1) 80자 초과 라벨은 truncate가 아니라 422 검증 거부, (2) 세션의 User-Agent fingerprint 바인딩으로 로그인/WS의 UA가 다르면 거부됨(보안 기능 정상) → 동일 UA로 검증.
- 검증: 백엔드 `ruff`(클린)·`pytest`(38 passed), prod 배포 후 라이브 e2e 전체 통과.

---

## 2026-06-24 (PR #36 후속 하드닝 — T-021)

- T-020에서 배포 리스크로 분리했던 후속 항목을 모두 반영(별도 PR, fix/pr36-followups-2). 적용:
  - **AUTH-3**: `_request_from_trusted_proxy`에 선택적 공유 시크릿 헤더(`KTDM_TRUSTED_PROXY_SECRET` / `X-KTDM-Proxy-Secret`) 요구 추가 — 설정 시 신뢰 CIDR이라도 헤더가 일치해야 X-Forwarded-* 를 신뢰(host 네트워크 로컬 프로세스의 loopback 위조 차단), 미설정 시 기존 동작(하위호환).
  - **AUTH-6**: 인메모리 brute-force 카운터를 제거하고 `login_audit_events` 기반 durable 집계로 전환 — 재시작·다중 워커에서 유지되며 마지막 성공 이후 실패만 카운트(성공 시 리셋 효과 보존).
  - **APIKEY-1**: 공개 API 키 검증의 프로세스 로컬 TTL 캐시를 제거하고 요청당 `key_hash` 유니크 인덱스 DB 조회로 전환 — 키 폐기가 모든 워커에 즉시 반영. `KTDM_PUBLIC_API_KEY_CACHE_TTL_S` 폐기.
  - **FE-4**: log/chart/config 모달에 `role="dialog"`/`aria-modal`/`aria-label`·Escape 닫기·닫기 버튼 초기 포커스 및 접근명(aria-label) 추가.
  - 문서: `.env.example`에 `KTDM_TRUSTED_PROXY_SECRET` 추가, 미사용 `KTDM_PUBLIC_API_KEY_CACHE_TTL_S` 제거.
- 검증: 백엔드 `ruff`(클린)·`pytest`(37 passed; AUTH-3/AUTH-6 테스트 추가), 프론트 `type-check`·`build` 통과. prod 배포 후 인증 end-to-end(로그인/me/컨테이너/키 생성·폐기/로그아웃·폐기쿠키 재사용 401) 재검증.

---

## 2026-06-24 (PR #36 사후 리뷰 + fix-forward — T-020)

- 자동 머지된 PR #36(`[codex]` 관리자 인증·공개 API 키)에 대해 보안/정확성/설정/프론트/테스트 5개 차원의 다각도 적대적 코드리뷰(원시 28건 → 검증 후 확정 24건, critical/high 없음)를 수행하고 PR #36에 한글 상세 리뷰 코멘트를 게시했다.
- #36은 이미 main(`b72becaa`)에 머지되어 있어 fix-forward 방식으로 후속 수정 PR(`fix/pr36-review-followups`)을 작성했다. 적용한 변경:
  - 백엔드: 로그인 username 불일치 시에도 PBKDF2를 항상 수행(타이밍 기반 username 열거 차단), `login_audit_events` 보존 상한(`KTDM_LOGIN_AUDIT_MAX_ROWS`, 기본 5000)·logout 감사 게이트·misconfigured 경로 레이트리밋(미인증 감사 적재 방지), CORS 명시 분기의 stray `*` 제거, 공개 API 키 캐시 TTL 파싱 가드, `metrics_service.init_db` 엔진 live 참조 + 실패 시 fail-fast, `key_hint` 컬럼 폭 정렬(6), `utcnow()` 헬퍼로 deprecated `datetime.utcnow()` 일괄 제거.
  - 테스트: 세션 검증 부정 경로(쿠키 없음→401, logout 후 폐기 쿠키 재사용→401, 변조 쿠키→401, `/auth/me`), WebSocket 인증 게이트(4401/성공), 신뢰 프록시 X-Forwarded-For 처리 긍정·부정을 추가/보강(28→35 passed).
  - 프론트: 백그라운드 401 시 하드 리로드 대신 `auth-me` 무효화로 SPA 내 LoginScreen 전환(dead `next` 파라미터 제거), 로그인 비밀번호 필드 autofocus, Admin Settings 모달 dialog 시맨틱·Escape·초기 포커스, 생성 키 "지우기" 컨트롤.
  - 문서: `.env.example`에 Grafana prod 오버라이드 주석·감사 로그 상한 env 추가.
- 후속(별도 PR 권장): 신뢰 프록시 기본값(loopback) 하드닝, brute-force 스로틀 영속화, 나머지 모달(log/chart/config) a11y, 공개 API 키 캐시 멀티워커 대응 — 배포 토폴로지(reverse proxy) 영향이 있어 별도 검증과 함께 진행.
- 검증: 백엔드 `ruff check`(클린), `pytest`(35 passed), 프론트 `type-check`·`build` 통과.

---

## 2026-06-23 (관리자 로그인·세션·공개 API 키 — T-019)

- `kor-travel-geo` PR #399의 관리자 로그인·공개 API 키 패턴을 확인하고 매니저에 적용했다. 대시보드는 로그인 화면을 먼저 보여 주며, 보호 API와 WebSocket은 지정된 프론트엔드 Origin과 관리자 세션을 함께 검증한다.
- 관리자 비밀번호는 `admin` 계정용 PBKDF2 해시로 gitignore된 `.env`에만 저장하고, 세션은 HMAC 서명 `httpOnly` 쿠키와 DB 저장 세션 해시로 검증한다.
- 로그인 성공·실패·로그아웃·API 키 생성/폐기 이벤트를 `login_audit_events`에 기록하고, 관리자 설정 UI에서 감사 로그와 공개 API 키 상태를 확인하도록 했다.
- 공개 API 키는 VWorld 호환 32자리 영문/숫자 문자열로 생성하며, 원문은 생성 직후 1회만 표시한다. DB에는 SHA-256 해시와 끝 6자리 힌트만 저장하고, 활성 키 해시는 짧은 TTL 메모리 캐시로 읽되 생성·폐기 시 즉시 무효화한다.
- `kor-travel-geo` v2 API가 같은 키를 쓰도록 compose와 `.env.example`에 PR #399의 `KTG_*` 관리자 인증·공개 API 키 env 계약을 반영했다.
- PR #399 사후 리뷰 코멘트를 다시 확인하고, 매니저에 해당하는 `X-Forwarded-*` 신뢰 제한, 401 세션 만료 처리, 로그인 오류 접근성, clipboard fallback, 외부 `env_file` raw 읽기 하드닝을 추가 반영했다.
- 검증: 백엔드 `ruff check`, 백엔드 `pytest`, 프론트 `type-check`, 프론트 `build`, `docker compose config -q` 통과.

---

## 2026-06-23 (prod endpoint 문서 redaction — T-018)

- `kor-travel-map` #508과 같은 prod endpoint 노출 패턴이 이 저장소에도 있는지 확인했다. 추적 파일 기준으로 `docs/journal.md`에 남아 있던 실제 운영 도메인 표현을 placeholder로 치환했다.
- gitignore된 루트 `.env`, `frontend/.env.production`, `docs/prod-access.local.md`에는 실제 값이 남아 있으나, 저장소 커밋 대상이 아니므로 정책 범위 안으로 확인했다.

---

## 2026-06-22 — kor-travel-map 서비스 env rename + prod 도메인 정합 (by claude)

`kor-travel-map`이 패키지 rename(`KRTOUR_MAP_*`→`KOR_TRAVEL_MAP_*`, `krtour.map_dagster`→
`kortravelmap.dagster`) 이후 docker-manager의 map 서비스 블록이 구 이름 그대로라 현재 이미지로는
동작 불가했다. 4개 서비스(api/ui/dagster/dagster-daemon)를 현재 코드 기준으로 정합.

- **backend env 키 rename**: `KRTOUR_MAP_ADMIN_*`→`KOR_TRAVEL_MAP_API_*`, `KRTOUR_MAP_*`→
  `KOR_TRAVEL_MAP_*` (컨테이너가 읽는 KEY만 변경, 우변 `${...:-default}`·값은 보존 →
  기존 `krtour_map` DB / `krtour-map` bucket 데이터 연결 유지). healthcheck 포트 env 참조도 정정.
- **dagster-daemon command 모듈**: `krtour.map_dagster.definitions`→`kortravelmap.dagster.definitions`.
  (dagster webserver는 이미지 default CMD 사용 — 현재 코드라 정상.)
- **UI NEXT_PUBLIC**: 구 `NEXT_PUBLIC_KRTOUR_MAP_ADMIN_API`(localhost)→`NEXT_PUBLIC_KOR_TRAVEL_MAP_API`
  등 신 이름 + **브라우저-facing prod 도메인**(env-driven: `${KTDM_PROD_URL_MAP_API:-localhost}` 등)
  + geo 추가. map admin은 BFF 프록시가 아니라 브라우저 직접 호출이라 cross-origin prod 도메인이 필수.
- **API CORS**: prod frontend origin(`KTDM_PROD_URL_MAP`) + localhost 허용.
- 검증: `docker compose config -q` VALID. 렌더 확인 — NEXT_PUBLIC=map-api/map-dagster/geo-api 도메인,
  CORS=`["https://<map-host>",...]`, object public=s3-api/krtour-map.

---

## 2026-06-20 (운영 스택 db→conc 기동, geo 실데이터 복원, 의존성 DAG 재설정 — T-017)

- **운영 스택 기동(db→conc, 도메인 정합성 확인)**: 운영 호스트에 dev의 빌드된 이미지를 `docker save | ssh docker load`로 전송(geo ~4.3GB, concierge ~4.5GB, GDAL 재빌드 회피)하고 `ktdctl`로 하나씩 기동했다. db·storage·gra·cadv·prom·geo·conc 각 단계에서 해당 `<service-prod-host>` 계열 도메인이 503→정상(200/307/406 등)으로 전환됨을 확인했다. 매니저 API가 running 11/18을 반영.
  - rustfs 크래시(root 소유 데이터 디렉터리 Permission denied)는 digitie 소유 쓰기가능 디렉터리로 `RUSTFS_DATA_DIR`를 전환해 해결(sudo 불필요).
  - geo는 앱 스키마(ops/public/x_extension)와 `pg_stat_statements`가 필요해 처음엔 data-less 기동했고, concierge는 기동 시 자동 마이그레이션(17테이블)으로 스키마 불필요.
- **geo 실데이터 복원**: dev `kor_travel_geo`(31GB)를 `pg_dump -Fc | ssh pg_restore`로 운영 DB에 복원해 지오코딩 데이터를 살렸다(운영 geo DB를 drop/recreate 후 전체 schema+data 복원).
- **의존성 DAG 재설정(ADR-18)**: target 의존을 선형 누적에서 `depends_on` DAG로 전환했다. `geo`와 `conc`는 각각 `prom`에만 의존(상호 독립, **concierge는 geo 비의존**), `map`은 `[geo, conc]`, `pinvi`는 `[map]`. `registry.target_sequence_for_target`을 폐포 위상정렬로 재작성하고, docker-compose의 concierge-api에서 geo-api 의존 제거·map-api에 geo-api 의존 추가.
- **검증**: 백엔드 25 pytest 통과(`conc` 시퀀스에서 geo 제외 반영), `docker compose config` 통과(concierge-api: geo-postgres/rustfs만, map-api: geo-api+concierge-api 포함), ruff 통과. 문서 ADR-18·docker-management DAG·tasks 동기화.
- **비민감 처리**: 운영 접속 정보/도메인/IP는 gitignore된 `.env`·`docs/prod-access.local.md`에만, 운영 전용 설정(`RUSTFS_DATA_DIR`, `STRICT_SOURCE_CHECK=0` 등)은 운영 호스트 `.env`에만 둔다.

---

## 2026-06-20 (운영(prod) 배포 및 docker-manager 실행 검증 — T-016)

- **작업 내용**:
  - 운영 호스트에 SSH 접속 후 docker-manager를 배포·기동했다(접속 정보는 gitignore된 `docs/prod-access.local.md`/`.env`에만 기록, git 비노출). 운영 호스트는 fresh 상태(Docker만 설치, repo·매니저 미설치)였다.
  - 소스+gitignore된 운영 설정(`.env`, `frontend/.env.production`)을 rsync로 전달했다.
  - 백엔드: 운영 호스트에 `python3-venv` 미설치 + sudo 제한이라 `python3 -m venv --without-pip` 후 get-pip.py로 pip을 부트스트랩하고 `pip install -e .` → uvicorn `:12901` 기동.
  - 프론트엔드: `npm ci` + `npm run build`(`.env.production`의 `NEXT_PUBLIC_BACKEND_URL`이 번들에 인라인) + `next start :12905`.
- **검증**:
  - 백엔드 `/health` healthy, `/api/v1/containers` 18개(모두 not_created, Docker 연동 동작), 프론트 `/` HTTP 200, 번들에 운영 API 도메인 인라인 확인.
- **범위 밖(네트워크 인프라)**:
  - 운영 공개 도메인(`manager.*`/`manager-api.*`)은 DDNS로 공인 IP에 연결되나, 게이트웨이/리버스 프록시에서 매니저 포트로 라우팅이 아직 없어 외부 접근은 404다. `manager.*→:12905`, `manager-api.*→:12901` 포워딩/프록시 설정이 필요하다(저장소 밖, 라우터/게이트웨이 영역).
  - 즉 docker-manager 앱 자체는 운영 호스트에서 정상 동작 확인 완료, 공개 도메인 접근만 인프라 라우팅이 남았다.
- **문서**: `docs/prod-deployment.md`(비민감 배포 런북) 추가, `docs/prod-access.local.md`(gitignore) 기록, `.gitignore`에 `*.local.md` 추가.

---

## 2026-06-20 (Claude Code PR #23/#24 리뷰 후속 수정 — T-011/T-015)

- **작업 내용**:
  - Claude Code가 2026-06-19부터 올린 PR #23, #24(merged/closed 포함)를 확인하고 각각 후속 리뷰 코멘트를 남겼다.
  - #23 후속: 설정 변경 API와 미생성 컨테이너 start fallback이 Docker SDK 직접 `containers.run(...)` 경로로 `network_mode: host` 계약을 우회하던 문제를 수정했다. `docker-compose.yml` 저장 후 `docker compose up -d --force-recreate <service>`로 재생성하고, RustFS는 compose의 `rustfs-init` service를 그대로 실행하도록 변경했다.
  - #24 후속: 운영 콘솔 첫 화면을 compact top bar + KPI strip 중심으로 정리하고, UI/데이터 표시용 font token을 명시했다.
- **검증**:
  - 백엔드 `ktd_venv/bin/python -m ruff check .` 통과.
  - 백엔드 `ktd_venv/bin/python -m pytest` 25 passed.
  - 프론트 `npm run type-check`, `npm run build` 통과.
  - `docker compose config` 통과.
- **주의**:
  - `codegraph sync`는 로컬 `.codegraph` disk I/O 오류로 실패해 직접 파일 확인과 테스트로 검증했다.

---

## 2026-06-20 (프론트엔드 Tailwind v4 + StyleSeed 전면 전환 및 전역 오류 복구 boundary — T-015)

- **작업 내용**:
  - **오류 복구 boundary**(`kor-travel-geo` PR #391 반영): App Router `app/error.tsx`/`app/global-error.tsx`, `components/layout/AppErrorPanel.tsx`, `lib/error-recovery.ts`를 추가했다. Next 기본 영어 오류 화면 대신 한국어 복구 패널을 보여 주고, chunk/RSC/network 계열 런타임 오류는 sessionStorage flag로 같은 pathname당 1회 hard reload를 시도한다.
  - **Tailwind v4 전환**: `globals.css`를 `@import "tailwindcss"` + `@theme` CSS-first로 바꾸고, `postcss.config.js`를 `@tailwindcss/postcss`로 교체, `package.json` 의존성을 tailwindcss/@tailwindcss/postcss `^4`로 올리고 autoprefixer 제거, v3 `tailwind.config.ts`를 삭제했다.
  - **StyleSeed 라이트 토큰**(`kor-travel-geo-ui/docs/DESIGN-RULES.md` 반영): `@theme`에 surface(page/card/subtle/elevated/row), 5단계 text(strong/ink/secondary/tertiary/disabled), 단일 brand teal(`#0f766e`), status(info/warn/danger/ok), 약한 shadow, motion 토큰을 정의했다. `DashboardClient`와 `AppErrorPanel`을 Pure Black 다크에서 이 토큰으로 전면 리스타일(단일 accent·약한 그림자·44px 터치타깃·상태 dot+text·rounded-card)했다.
  - 문서: `docs/DESIGN-RULES.md` 신규(매니저용 포팅), `DESIGN.md`에 StyleSeed 전환 superseded 안내, ADR-17 추가.
- **검증**:
  - v4 의존성 설치(tailwindcss 4.3.1, @tailwindcss/postcss 4.3.1, oxide 네이티브 엔진) 완료.
  - 프론트 `type-check`·`build` 통과(아래 최종 검증 절에서 재확인).
  - 잔여 Pure Black 토큰(`bg-black`/`text-on-dark`/`border-hairline`/`m-blue-*`) 0건 확인.

---

## 2026-06-20 (Docker host 네트워크 전환·컨테이너=호스트 포트 통일·서비스 prod URL 반영·pinvi-dagster 추가·tripmate 잔재 정리 — T-014)

- **작업 내용**:
  - **host 네트워크(dev 기본)**: `docker-compose.yml` 전 서비스(19개)에 `network_mode: ${KTDM_DOCKER_NETWORK_MODE:-host}`를 적용했다. 포트 NAT가 없는 host 모드에 맞춰 인프라(RustFS 12101/12105, Grafana 12205, Prometheus 12401, cAdvisor 12301)와 앱이 호스트 정규 포트에 직접 바인딩하도록 바꾸고, 서비스 간 참조(PostgreSQL DSN, RustFS 엔드포인트, 내부 API/Dagster URL)를 컨테이너명 → `127.0.0.1:<포트>`로 전환했다. `config/prometheus/prometheus.yml` scrape 타깃과 `scripts/ensure-rustfs-buckets.sh` 엔드포인트도 `127.0.0.1` 기준으로 맞췄다.
  - **컨테이너=호스트 포트 통일**: `kor-travel-geo`(이미 동일), `kor-travel-concierge`(api `--port`, mcp `MCP_PORT`, ui는 `next dev` command 오버라이드), `kor-travel-map`(`*_CONTAINER_PORT` 기본값을 12701/12702/12705로), PinVi(api `--port`, web은 `next start -p` command 오버라이드)를 모두 컨테이너 내부 포트 = 호스트 포트로 맞췄다.
  - **PinVi Dagster 추가**: `pinvi-dagster`(host=container 12802) compose service와 registry 컨테이너/`pinvi` target 편입을 추가하고, upstream PinVi 저장소에 `apps/etl/Dockerfile`(python:3.12-slim, editable 설치, `dagster-webserver -m tripmate.etl.definitions`)을 신규 작성했다.
  - **서비스 prod 공개 URL 반영**: 관리 16개 서비스(geo … s3-api)의 운영 공개 주소를 gitignore된 `.env`의 `KTDM_PROD_URL_*`에 저장하고, `docker-targets.yml`의 `prod_url_env`(환경변수 이름만 커밋)와 `docker_service._public_url()`로 읽어 대시보드 `public_url` 링크로 표시하도록 백엔드/프론트엔드를 확장했다. `.env.example`은 example.org 플레이스홀더로만 문서화(도메인 비노출).
  - **tripmate 잔재 정리**: 루트 `tripmate_metrics.db` → `pinvi_metrics.db`(코드는 이미 `pinvi_metrics.db` 사용) 개명, 백엔드 venv `tripmate_venv` 제거 후 문서 표준명 `ktd_venv`로 재생성, 잔여 `backend/logs/tripmate_manager.log` 제거. 추적 코드에는 과거 명칭 잔재가 없었고 journal의 과거 이력 기록만 보존했다.
  - 문서: ADR-16 추가, `docs/tasks.md` T-014 등록, `docs/docker-management.md`(컨테이너 18개·host 모드·포트 동일·pinvi-dagster), `docs/ports.md`(12802·host 모드) 동기화.
- **검증**:
  - `docker compose config` exit 0, 경고/에러 0. 19개 서비스 `network_mode: host`, 모든 published 포트가 host=container, `pinvi-dagster` 렌더링 확인.
  - 백엔드 `ruff check`, `public_url` 해석 단위 검증. 프론트 `type-check`·`build` 통과(예정 항목은 최종 검증 절에서 재확인).
  - 추적 파일 전수 grep으로 실제 도메인·잔여 `tripmate`·구 포트 참조 부재 확인.
- **런타임 검증 필요(범위 외 주의)**:
  - host 모드 실제 기동은 Docker 엔진의 host networking 지원에 의존하므로 사용자 환경에서 `ktdctl <target> --build` 런타임 검증이 필요하다.
  - `pinvi-dagster`는 PinVi `apps/etl` ETL 모듈이 미완(Sprint 1 stub)이라 webserver 기동은 upstream 모듈 상태에 따라 달라질 수 있다.

---

## 2026-06-20 (운영 공개 주소 `.env` 주입 및 CORS 환경변수화 — T-013)

- **작업 내용**:
  - 매니저 백엔드 API/대시보드의 운영 공개 도메인을 소스에 하드코딩하지 않고 gitignore된 env 파일에만 주입하도록 설정 계층을 정비했다(외부 비노출).
  - 백엔드(`main.py`): 기동 시 루트 `.env`(또는 `KOR_TRAVEL_DOCKER_MANAGER_ENV_FILE`)를 `load_dotenv`로 로드하고, CORS 허용 Origin을 `KTDM_CORS_ALLOW_ORIGINS`(콤마 구분, 미설정/`*`이면 전체 허용)로 환경변수화했다. 기존 `allow_origins=["*"]` 개발 기본 동작은 유지.
  - 프론트엔드: 백엔드 주소를 환경별로 분리했다. `frontend/.env.development`(localhost), `frontend/.env.production`(운영 API 도메인)을 추가하고, Next.js 우선순위상 `.env.local`이 `.env.production`을 덮어쓰는 사고를 막기 위해 `.env.local`에서 `NEXT_PUBLIC_BACKEND_URL`을 제거(주석화)했다. WS 주소는 `http→ws` 치환으로 `wss`가 자동 파생된다.
  - 계약 문서화: 루트 `.env.example`과 신규 `frontend/.env.example`에 새 변수를 **플레이스홀더**(`manager.example.org` 등)로만 기재해 실제 도메인 노출 없이 셋업을 안내했다. 실제 값은 gitignore된 루트 `.env`, `frontend/.env.production`에만 존재.
  - 문서: ADR-15 추가, `docs/tasks.md` T-013 등록·완료, `docs/dev-environment.md` 4.3 운영 공개 주소 절 추가.
- **검증**:
  - `git check-ignore`로 실제 도메인 파일 4종(`.env`, `frontend/.env.{production,development,local}`) ignore 확인, 추적 파일 전수 grep에서 실도메인 누출 0건.
  - 백엔드: 전체 `ruff check` 통과, CORS 파싱 검증(콤마 리스트 trim, `*`/미설정→전체 허용, 루트 `.env` end-to-end 로드로 운영 Origin 적용).
  - 프론트엔드: `@next/env`로 환경 우선순위 결정적 검증(prod→운영 API 도메인, dev→localhost, `.env.local` 섀도잉 없음), `npm run type-check`·`npm run build` 통과, 운영 빌드 번들에 API 도메인 인라인 및 `.next` ignore 확인.

---

## 2026-06-17 (브랜드 표기 PinVi 교정 및 관측 컨테이너 재기동)

- **작업 내용**:
  - PR #19에서 `Pinvi`로 표기된 브랜드명을 정식 표기 `PinVi`로 교정했다. 14개 파일에서 대소문자 구분 치환으로 73개 표기를 수정했다(.env.example, AGENTS.md, CLAUDE.md, SKILL.md, README.md, config/docker-targets.yml, docs/*).
  - 소문자 식별자(`pinvi`, `pinvi-api-latest`, `pinvi-media`)와 환경변수 prefix(`PINVI_*`)는 런타임/계약 식별자이므로 그대로 두고, 사람이 읽는 표시 문자열만 `PinVi`로 맞췄다.
  - PR #19의 공용 컨테이너 명칭 변경(`tripmate-* -> kor-travel-*`)을 실제 런타임에 반영하기 위해 관측 컨테이너(rustfs/prometheus/grafana/cadvisor)를 새 이름으로 재생성했다. 공용 DB(`kor-travel-geo-postgres`)와 실행 중인 concierge 스택은 유지했다.
- **검증**:
  - 대소문자 구분 `Pinvi` 잔여 검색 0건, `PinVi` 정상 반영 확인.
  - `docker ps`로 `kor-travel-prometheus/grafana/cadvisor/rustfs` 신규 이름 기동 및 Prometheus `/-/healthy` 200 확인.

---

## 2026-06-17 (멀티 에이전트 MCP/agent/skill 설정 확장 — filesystem MCP & OpenCode 포팅)

- **작업 내용**:
  - Claude Code(`claude.json`), Codex(`codex.json`, `.codex/config.toml`), Antigravity(`antigravity.json`), OpenCode(`opencode.json`) 네 도구에 `@modelcontextprotocol/server-filesystem` MCP 서버를 추가했다. 허용 디렉터리는 각 도구의 worktree(`...-claude`, `...-codex`, `...-antigravity`, `...-opencode`)로 지정해 기존 codegraph cwd 규칙과 일치시켰다.
  - OpenCode에 없던 기존 MCP 설정·agent·skill을 OpenCode 형식으로 포팅했다.
    - **MCP**: `opencode.json`에 playwright/sequential-thinking/codegraph/filesystem 4개 서버를 OpenCode local 스키마(`type:"local"`, `command` 배열, `environment`)로 정의하고 `skills.paths`에 `.opencode/skills`를 등록했다.
    - **Agent**: `.opencode/agent/`에 6개 subagent(api-designer, backend-developer, frontend-developer, mobile-developer, ui-designer, ui-fixer)를 추가했다. Claude markdown 5종은 본문을 그대로 보존하고 frontmatter만 OpenCode 형식(`mode: subagent`, `tools` 맵)으로 변환했으며, Codex에만 있던 ui-fixer는 새로 작성했다.
    - **Skill**: `.opencode/skills/`에 postgres 외 8개 skill(SKILL.md + postgres/references 7종)을 원본과 byte 동일하게 복제했다.
  - codegraph의 `cwd`는 OpenCode local MCP 스키마에 없는 필드(런타임이 instance 디렉터리에서 자동 설정)라 `opencode.json`에서는 제외했다.
- **검증**:
  - 4개 JSON 설정 `ConvertFrom-Json` 파싱 통과, `.codex/config.toml` 포함 5개 설정에서 filesystem 서버 존재 확인.
  - skill 15개 파일 SHA256 원본 동일, 포팅한 agent 5종 본문이 원본과 byte 동일.
  - 5개 병렬 감사 에이전트로 적대적 검증 수행: filesystem 추가·agent·skill·완전성은 모두 pass, opencode.json은 codegraph `cwd` 제거로 해결.
- **범위 외 메모**:
  - `.gemini/mcp.json`은 사용자가 지정한 4개 도구에 포함되지 않아 filesystem을 추가하지 않고 현 상태를 유지했다.
  - playwright 패키지명 `@modelcontextprotocol/server-playwright`는 기존 4개 설정과 동일하게 유지했다(일관성). 공식 `@playwright/mcp`로의 전환은 전체 설정 동기화가 필요한 별도 사안.

---

## 2026-06-15 (PinVi 및 Kor Travel 공용 명칭 정리)

- **작업 내용**:
  - 남아 있던 과거 서비스명 계열 명칭을 PinVi 기준으로 정리했다.
  - 공용 컨테이너 성격이 강한 RustFS, Grafana, cAdvisor, Prometheus 이름은 `kor-travel-*` 기준으로 변경했다.
  - PinVi 전용 database/role/bucket/env 이름을 `pinvi`, `PINVI_*`, `pinvi-media` 기준으로 맞췄다.
  - 과거 geo 패키지명 계열 잔여 명칭이 없는 것을 확인했다.
- **검증**:
  - WSL에서 과거 서비스명과 과거 geo 패키지명 계열 잔여 검색 결과 0건 확인.

---

## 2026-06-13 (`geo -> conc -> map -> pinvi` target 흐름 반영)

- **작업 내용**:
  - 사용자 지시에 따라 앱 target 순서를 `geo -> conc -> map -> pinvi`로 재정렬했다.
  - `kor-travel-concierge` API/MCP/Scheduler/Web UI를 `conc` target의 실제 compose service로 추가했다.
  - `kor-travel-map` API/Dagster/Web UI를 `map` target의 실제 compose service로 추가해 `ktdctl map --build`가 이미지를 빌드하고 실행하도록 변경했다.
  - PinVi API/Web UI를 `pinvi` target으로 추가하고 짧은 별칭 `srv`와 기존 호환 별칭 `main`을 연결했다.
  - 공용 DB 복구에 `krtour_map_dagster` database를 추가하고, RustFS bucket 복구에 `kor-travel-concierge` bucket을 추가했다.
  - CLI 직접 alias 처리를 registry 기반으로 바꿔 `conc`, `srv` 같은 새 alias가 자동 반영되게 했다.
- **검증**:
  - `docker compose config --quiet` 통과.
  - `scripts/ensure-kor-travel-geo-db.sh`, `scripts/ensure-rustfs-buckets.sh` `bash -n` 통과.
  - `PYTHONPATH=src backend/pinvi_venv/bin/python`으로 registry 해석 확인: `map`은 `db -> storage -> gra -> cadv -> prom -> geo -> conc -> map`, `srv`는 `... -> pinvi`로 resolve.

---

## 2026-06-13 (`kor-travel-geo` UI Prometheus scrape 추가)

- **작업 내용**:
  - `kor-travel-geo-ui`의 Next.js Prometheus endpoint(`/api/metrics`)를 scrape하도록 `kor-travel-geo-ui:12505` target을 추가했다.
- **검증**:
  - `docker compose config` 통과.
  - `config/prometheus/prometheus.yml`에서 `kor-travel-geo-api:12501`, `kor-travel-geo-ui:12505` scrape target 확인.

---

## 2026-06-13 (Grafana/cAdvisor/Prometheus target 개별 분리)

- **작업 내용**:
  - 단일 관측 target을 제거하고 `gra`, `cadv`, `prom`을 독립 CLI/API target으로 분리했다.
  - dependency 순서를 `db -> storage -> gra -> cadv -> prom -> geo -> map -> ai -> main`으로 조정했다.
  - Grafana는 공용 연계를 위해 `12205`, cAdvisor는 `12301`, Prometheus는 `12401`을 사용하도록 재배치했다.
  - `kor-travel-geo` API/Web UI는 새 dependency 순서에 맞춰 `12501`, `12505`로 이동했다.
  - Prometheus scrape target을 `kor-travel-geo-api:12501/metrics`로 갱신했다.
  - CLI 직접 별칭, API/CLI 테스트, 포트 문서, Docker 관리 문서, 개발 가이드를 같은 기준으로 갱신했다.
- **결정 사항**:
  - Grafana, cAdvisor, Prometheus compose service는 서로 `depends_on`으로 묶지 않고 독립 실행 가능하게 둔다.
  - `geo` 이상 target은 새 dependency 순서상 관측 컨테이너를 선행 실행한다.
- **검증**:
  - WSL `backend/pinvi_venv`에서 `ruff check .` 통과.
  - WSL `backend/pinvi_venv`에서 `pytest` → 22 passed.
  - WSL 프론트엔드에서 `npm run type-check`, `npm run build` 통과.
  - WSL Docker에서 Grafana `12205`, cAdvisor `12301`, Prometheus `12401`, `kor-travel-geo` API `12501`, Web UI `12505`로 재기동 완료.
  - HTTP 확인: Grafana `/api/health` 200, cAdvisor `/healthz` 200, Prometheus `/-/ready` 200, Geo API `/v1/healthz` 200, Geo UI `/` 307, RustFS `/health/live` 200.

---

## 2026-06-13 (`kor-travel-geo` DB명·환경변수·Prometheus scrape 계약 동기화)

- **작업 내용**:
  - 사용자 지시에 따라 `kor-travel-geo`가 현재 사용하는 DB명과 환경변수 계약에 Docker manager compose를 맞췄다.
  - `kor-travel-geo` DB 기본값을 `kor_travel_geo`로 변경했다.
  - manager override 변수는 `KOR_TRAVEL_GEO_*`로, API/UI 컨테이너 내부 환경변수는 앱이 읽는 `KTG_*`로 변경했다.
  - Docker service/container 이름과 target registry를 `kor-travel-geo-*` 기준으로 변경했다.
  - `/home/digitie/kor-travel-geo-data`를 PostgreSQL, RustFS, Prometheus, Grafana의 물리 데이터 디렉터리 기준으로 반영했다.
  - `kor-travel-geo` RustFS bucket 기본값을 `kor-travel-geo`로 맞췄다.
  - Prometheus scrape 설정에 `kor-travel-geo-api` job을 추가했다.
- **결정 사항**:
  - 기존 bind mount와 compose label을 가진 manager 스택 컨테이너는 중지 후 제거하고, 새 compose 기준으로 재생성 가능한 상태로 둔다.
  - 물리 데이터 디렉터리 이름도 프로젝트 공식명과 맞춘다.
- **검증**:
  - WSL Docker에서 manager 스택 컨테이너를 중지·제거하고 `/home/digitie/kor-travel-geo-data`로 데이터 디렉터리 이동 완료.
  - 과거 geo 이름 계열 문자열 검색 결과 없음.
  - `bash -n scripts/ensure-kor-travel-geo-db.sh scripts/verify-kor-travel-geo-source.sh scripts/ensure-rustfs-buckets.sh` 통과.
  - `docker compose config`에서 `KTG_PG_DSN=postgresql+psycopg://addr:addr@kor-travel-geo-postgres:5432/kor_travel_geo`, `POSTGRES_DB=kor_travel_geo` 확인.
  - `git diff --check` 통과.
  - WSL `/tmp/ktdm-venv` 임시 가상환경에서 `ruff check .` 통과.
  - WSL `/tmp/ktdm-venv` 임시 가상환경에서 `pytest` → 22 passed.
  - 프론트엔드 `npm run type-check` 통과.
  - `npm run lint`는 Next.js ESLint 초기 설정 프롬프트로 비대화형 실행이 중단됨.
  - `npx react-doctor@latest . --offline --verbose --json`은 실패 없이 완료했으며 기존 Next.js 14 보안 경고와 `DashboardClient` 구조성 경고 4건을 보고.

---

## 2026-06-13 (Kor Travel Docker Manager 프로젝트명 전환)

- **작업 내용**:
  - 프로젝트 공식명을 `Kor Travel Docker Manager` / `kor-travel-docker-manager`로 바꾸고 문서, package metadata, 프론트엔드 metadata를 동기화했다.
  - 백엔드 import package를 `kor_travel_docker_manager`로 변경하고 ASGI entrypoint 문서를 `kor_travel_docker_manager.main:app`으로 갱신했다.
  - CLI console script를 `ktdctl`로 전환하고 이전 CLI 명령 안내를 제거했다.
  - Docker Compose project name을 `kor-travel-docker-manager`로 고정해 network prefix를 새 프로젝트명 기준으로 통일했다.
- **결정 사항**:
  - 이전 CLI 이름을 병행 제공하지 않고 `ktdctl`만 공식 인터페이스로 둔다.
  - GitHub 저장소명은 코드 변경 PR 병합 후 `kor-travel-docker-manager`로 rename한다.

---

## 2026-06-13 (과거 이름 helper 제거 및 rebase 재검토)

- **작업 내용**:
  - `origin/main` 기준 최신 머지 상태를 확인한 뒤 `agent/remove-old-name-helper` 브랜치에서 재검토했다.
  - 과거 프로젝트명 기반 target alias와 fallback env 검색을 재수행하고, 남은 문서 표현과 UI 기본 표시명을 `kor-travel-geo`, `kor-travel-concierge` 기준으로 정리했다.
  - target을 중복 하드코딩하던 보조 shell helper를 제거하고, 공식 실행 경로를 `ktdctl` CLI와 API/dashboard registry로 단일화했다.
  - `ktdctl gra`, `ktdctl cadv`, `ktdctl prom`, `ktdctl all`도 직접 `ensure`로 해석되도록 CLI 직접 target 목록을 registry target과 맞췄다.
- **결정 사항**:
  - 과거 이름 수용 목적 alias/fallback/helper는 유지하지 않는다.
  - 실제 Docker container/service 이름과 물리 데이터 디렉터리는 후속 작업에서 `kor-travel-geo` 기준으로 맞춘다.

---

## 2026-06-13 (Prometheus/Grafana/Exporter 관측 스택 분리)

- **작업 내용**:
  - `docker-compose.yml`에 Prometheus, Grafana, cAdvisor Exporter를 각각 별도 Docker service로 추가했다.
  - 포트 정책에 맞춰 Grafana, cAdvisor Exporter, Prometheus를 각각 관리 컨테이너로 등록했다.
  - `config/docker-targets.yml`에 `grafana`, `prometheus`, `cadvisor` 관리 컨테이너를 등록했다.
  - Prometheus scrape 설정(`config/prometheus/prometheus.yml`)과 Grafana Prometheus datasource provisioning을 추가했다.
  - 관리 UI 목록에서 Prometheus, Grafana, cAdvisor Exporter가 역할별 아이콘과 표시명으로 구분되도록 프론트엔드 표시 로직을 보강했다.
  - `.env.example`, `docs/architecture.md`, `docs/docker-management.md`, `docs/ports.md`, `docs/decisions.md`, `docs/tasks-done.md`를 같은 기준으로 갱신했다.
- **결정 사항**:
  - Exporter는 Docker 컨테이너 리소스 메트릭에 적합한 cAdvisor를 사용하고, Grafana는 Prometheus datasource를 자동 등록한다.
  - `all` target에는 관측 스택까지 포함해 전체 로컬 인프라 실행 시 함께 올라가도록 한다.

---

## 2026-06-12 (태스크 장부 정리 및 kor-travel-concierge 선행 작업 등록)

- **작업 내용**:
  - 완료된 `T-001`~`T-010`, `T-013`~`T-016`을 `docs/tasks-done.md`로 분리하고, `docs/tasks.md`에는 진행 중/대기 작업만 남겼다.
  - 미완료 작업 `T-011`, `T-012`를 유지하고, `kor-travel-concierge` provider 상세 구현 및 명칭 전환을 `T-220` 선행 작업으로 등록했다.
  - 사용자 지정 순서인 `T-221`, `T-222`, `T-223`을 `T-220` 이후 순차 진행 항목으로 추가했다.
- **결정 사항**:
  - `T-221` 착수 전 `kor-travel-concierge` 잔여 명칭과 `pinvi` 직접 의존 설명을 먼저 정리한다.
  - `T-221`~`T-223`의 세부 범위는 현재 `kor-travel-docker-manager` 저장소 장부에 없으므로, `T-220` 완료 후 작업 전 상세 항목을 확정한다.

---

## 2026-06-12 (`kor-travel-geo` Docker API/UI 관리 편입)

- **작업 내용**:
  - `docker-compose.yml`에 `kor-travel-geo-api`, `kor-travel-geo-ui` 서비스를 추가해 `kor-travel-geo` REST API와 admin Web UI를 manager에서 함께 실행할 수 있게 했다.
  - `config/docker-targets.yml`에 `kor-travel-geo-api-latest`, `kor-travel-geo-ui-latest`를 공식 관리 컨테이너로 등록하고 `geo` 이상 target에 포함했다.
  - 포트 정책에 맞춰 API와 Web UI 포트를 배정하고, API 컨테이너가 compose 네트워크의 `kor-travel-geo-postgres:5432`, `rustfs:9000`을 사용하도록 설정했다.
  - `.env.example`, `docs/docker-management.md`, `docs/architecture.md`, `docs/ports.md`, `docs/dev-environment.md`, `README.md`, `docs/tasks.md`를 같은 기준으로 갱신했다.
- **결정 사항**:
  - 기존 `kor-travel-geo` 로컬 script와 같은 컨테이너 이름(`kor-travel-geo-api-latest`, `kor-travel-geo-ui-latest`)을 사용해 대시보드와 CLI가 기존 Docker 대상을 그대로 확인할 수 있게 한다.

---

## 2026-06-12 (WSL/Windows 실행 위치 정책 고정)

- **작업 내용**:
  - `git` 명령은 Windows 호스트에서만 실행하고, 패키지 설치·Docker·서버 실행·빌드·테스트·파일 검색 등 일반 개발 명령은 WSL에서만 실행하도록 문서화.
  - Playwright E2E는 실제 Windows 브라우저 환경 확인을 위한 명시 예외로 Windows 호스트에서 실행하도록 고정.
  - `AGENTS.md`, `SKILL.md`, `docs/dev-environment.md`, `CLAUDE.md`, `docs/tasks.md`에 실행 위치 정책을 반영.
- **결정 사항**:
  - Windows 경로가 문서에 나오더라도 git과 Playwright E2E를 제외한 명령 실행은 `/mnt/f/...` WSL 경로를 사용한다.

---

## 2026-06-12 (Kor Travel/PinVi 전용 Docker Manager CLI/API 및 문서 정리)

- **작업 내용**:
  - **통합 DB 모델 공식화**: `kor-travel-geo-postgres:5432` 하나에 `kor_travel_geo`, `pinvi`, `kor_travel_concierge`, `krtour_map` database를 담는 현재 구조를 공식 기준으로 문서화하고, 과거 분리 DB 기준 문구를 정리.
  - **target registry 도입**: `db`, `storage`, `geo`, `map`, `ai`, `main`, `all` target을 API/CLI가 공유하도록 정의.
  - **Python CLI 추가**: `ktdctl targets/status/ensure/logs/action/inspect` 명령을 추가하고, 개발환경에서 `ktdctl <alias> --build`로 의존 Docker를 바로 실행할 수 있게 함.
  - **짧은 CLI 별칭 추가**: `db`, `storage`, `gra`, `cadv`, `prom`, `geo`, `map`, `ai`, `main`을 공식 별칭으로 두고 `config/docker-targets.yml`의 dependency 순서를 따라 누적 실행하도록 구현.
  - **포트 정책 일원화**: PostgreSQL host 포트를 `5432`로 변경하고, RustFS는 `12101`/`12105`, manager API/Web은 `12901`/`12905`로 정리.
  - **초기화/복구 step 추가**: 통합 DB database/role/schema/extension 복구, RustFS bucket 복구, `kor-travel-geo` 원천 디렉터리와 핵심 테이블 적재 검증을 `ensure` 흐름에 연결.
  - **API 확장**: `GET /api/v1/targets`, `POST /api/v1/targets/{target}/ensure`, `GET /api/v1/containers/{container_id}/inspect`를 추가.
  - **Docker inspect redaction**: inspect 응답에서 password, secret, token, access key 계열 environment 값을 마스킹하도록 구현.
  - **문서 보강**: `docs/docker-management.md`를 신규 작성하고, `architecture`, `decisions`, `tasks`, `dev-environment`, `README`, 에이전트 가이드를 통합 DB/CLI 기준으로 갱신.
- **결정 사항**:
  - Docker 생명주기와 `--build`는 `docker compose` 인자 배열 실행으로 처리하고, stats/logs/inspect/action은 Docker SDK를 유지한다(ADR-7).
  - target alias와 초기화 step은 `config/docker-targets.yml`을 source of truth로 삼는다(ADR-8).
- **다음 작업**:
  - 대시보드 상세 패널에서 inspect API를 연결하고, compose 설정 변경 전 diff/validation을 강화한다.

## 2026-06-11 (WSL 네트워크 연결 복구 및 월 단위 로그 롤링 구현)

- **작업 내용**:
  - **WSL 가상 IP 통신 거부 결함 최종 해결**: 브라우저에서 `172.26.51.35:9091`로 백엔드 API에 접속 시, 포트 9091이 윈도우 프로세스(Firefox 등)의 좀비 커넥션 및 WSL2 포트 맵 꼬임으로 인해 접근 거부되던 현상을 해결. Windows powershell에서 WSL을 강제 종료(`wsl --shutdown`) 및 가상 어댑터를 리셋하여 9091 바인딩 꼬임 문제를 완벽히 해결 및 연결 정상 복구 완료.
  - **월 단위 로그 파일 롤링 및 1년 보관 로직 추가**: uvicorn 서버의 작동 로그 출력을 매월 1일 단위로 분할하여 `kor_travel_docker_manager.log.YYYY-MM` 형태로 백업하고, 1년(365일)이 지난 로그 파일을 자동으로 탐색하여 청소하는 백그라운드 클린업 스레드를 추가하여 로깅 유지 비용 제어.
  - **백엔드 가상환경 재구축 및 WebSocket 라이브러리 추가**: 기존 `.venv` 가상환경 내에 WebSocket 구동에 필수적인 `websockets` 라이브러리가 누락되었고, 파일 락(Lock) 및 패키지 찌꺼기로 인해 pip 설치가 교착 상태에 빠지던 이슈를 발견. Windows PowerShell을 통해 기존 가상환경을 강제 제거하고, WSL Python 3.12를 기반으로 하는 수동 가상환경을 깨끗하게 재구축한 뒤 `websockets`, `fastapi` 등의 필수 의존성을 완벽하게 재설치 완료.
  - **백엔드 실행 경로 매핑 및 PYTHONPATH 주입**: 백엔드 수동 기동 시 `PYTHONPATH=src` 환경 변수를 주입하여 uvicorn이 `kor_travel_docker_manager` 패키지 모듈을 바르게 탐색할 수 있도록 조정했다.
  - **대시보드 UI 글씨 크기 조정**: 테이블 컬럼 제목의 폰트 크기를 `text-[10px]`에서 `text-xs md:text-sm`으로 키우고, 테이블의 각 셀 내용(상태, 명칭, 역할, 포트 바인딩, 리소스 수치) 및 리차트(Recharts) 기반 그래프의 틱(Ticks), 범례(Legend), 툴팁(Tooltip)의 폰트 크기를 1~2px씩 일제히 상향하여 시인성 대폭 개선.
- **결정 사항**:
  - WSL 환경과의 통신 결함을 방지하기 위해 백엔드 접속 주소는 `localhost:9091`을 기본값으로 사용한다. (다만 가상 IP 바인딩을 활용하는 경우 프론트엔드가 환경에 맞추어 `http://172.26.51.35:9091`로 수동 통신하도록 .env.local을 구성한다.)
  - 가상환경 락 이슈 해결을 위해 캐시 및 락 찌꺼기가 남은 기존 `.venv`를 우회하는 수동 가상환경을 구축하여 사용한다.

## 2026-06-11 (대시보드 M 룩앤필 교정, CSS 링크 결함 수정 및 react-doctor 최적화 완료)

- **작업 내용**:
  - **디자인 가이드 대시보드 이식 및 교정**: 대시보드 메인 화면 상단에서 부적절한 자동차 피트라인 배경 이미지(`/images/pit_lane_night.png`) 및 억지스러운 모터스포츠 비유를 완전히 배제하고, Pure Black 배경과 얇은 hairline border 및 4px M 삼색선 디바이더로 구성된 실용적인 IT 인프라 대시보드 룩앤필로 정교화 및 수렴.
  - **CSS 폰트 로드 링크 결함 수정**: `next/font/google`을 활용한 폰트 로드를 완료하고, `layout.tsx`의 body에 `font-sans`를 명시적으로 매핑하여 런타임 상의 CSS 폰트 링크 깨짐을 완전히 차단.
  - **아키텍처 리팩토링 및 react-doctor 경고 제거**: `page.tsx`를 Server Component로 전환하여 메타데이터를 노출하고, 1,025라인의 대형 컴포넌트를 `src/components/DashboardClient.tsx` (Client Component)로 완벽히 분리. 또한 dynamic import(recharts), aria-label(접근성), stable key(key={idx} 대체), useMemo(derived state 제거), WebSocket 마운트 state 최적화를 적용하여 `react-doctor` 경고 25건을 모두 해결.
  - **디자인 시스템 문서화**: 디자인 시스템 적용 범위를 실제 대시보드의 테이블, 모달, 차트 모듈 사양으로 갱신하여 디자인 일관성 가이드를 강화.
  - **포트 확정 및 적용**: API 구동 포트를 9091로, WEB 구동 포트를 9092로 최종 확정하고, 소스코드(main.py, DashboardClient.tsx, env) 및 문서(CLAUDE.md, dev-environment.md)에 일제히 동기화 반영 완료.
- **결정 사항**:
  - 디자인 일관성 및 코드 품질 향상을 위해 서버-클라이언트 컴포넌트 분리 및 react-doctor 최적화 규칙을 반영함 (ADR-6).

## 2026-06-11 (실시간 컨테이너 모니터링 테이블, WebSocket 및 성능 차트 구현)

- **작업 내용**:
  - **백엔드**: `main.py`의 lifespan 동작 시 `metrics_service` 임포트 누락으로 인해 `NameError`가 발생하던 결함을 발견하고, `from kor_travel_docker_manager.services.metrics_service import metrics_service`를 임포트 목록에 추가하여 해결.
  - **백엔드**: SQLite3 데이터베이스 연동(`metrics_service.py`) 및 10초 주기 Docker stats 메트릭 수집기(`metrics_collector.py`) 구현. 최신 리소스 캐시 및 30일 만료 규칙 적용.
  - **백엔드**: WebSocket 라우트(`websocket.py`) 구현. `/api/ws/status`를 통한 상태/메트릭 실시간 브로드캐스트 및 `/api/ws/logs/{container_id}`를 통한 컨테이너 로그 스트리밍 제공.
  - **백엔드**: 지난 1시간의 수집 기록을 조회하는 GET `/api/containers/{container_id}/metrics` API 추가.
  - **프론트엔드**: 기존의 컨테이너 카드 뷰를 Premium Glassmorphic Table 형태로 전면 개편(`page.tsx`).
  - **프론트엔드**: WebSocket 실시간 상태 동기화 및 끊김 시 5초 폴링 Fallback 로직 연동.
  - **프론트엔드**: 터미널 스타일 로그 스트리밍 모달 다이얼로그 및 Recharts 기반의 1시간 리소스 이력 라인 차트 모달 기능 추가.
- **결정 사항**:
  - 실시간 리소스 모니터링 및 로그 스트리밍을 제공하기 위해 WebSockets 아키텍처를 도입하고, 기존 TanStack Query를 Fallback용으로 하이브리드 운영.
- **다음 작업**:
  - 개별 컨테이너 환경설정 업데이트 동작 확인 및 최종 사용자 테스트.

## 2026-06-10 (kor-travel-geo PostgreSQL/RustFS 인프라 이관)

- **작업 내용**:
  - `docker-compose.yml`에 `kor-travel-geo` 전용 `kor-travel-geo-postgres` 서비스를 추가하고, 기존 T-027 최종 DB 접속 계약을 `kor-travel-docker-manager` 기본 설정으로 이관했다.
  - 공용 RustFS 서비스의 포트, credential, 데이터 디렉터리, bucket 초기화를 `.env.example`과 compose에 명시하고 `kor-travel-geo` bucket을 함께 생성하도록 했다.
  - 초기 helper 명령을 추가해 `up/stop/restart/status/logs`를 주요 target 단위로 실행할 수 있게 했다.
  - 백엔드/프론트엔드 대시보드가 당시의 PostgreSQL/RustFS 관리 대상을 표시하도록 갱신했다.
- **결정 사항**:
  - PostgreSQL/RustFS Docker 생명주기와 로컬 포트 계약은 `kor-travel-docker-manager`가 관리한다(ADR-5).
- **다음 작업**:
  - compose live smoke와 대시보드의 compose create 액션 확장 여부를 후속으로 검토한다.

## 2026-06-10 (인프라 매니저 프로젝트 초기화 및 가이드라인 복사)

- **작업 내용**:
  - `maplibre-vworld-js` 저장소를 기반으로 AI 에이전트 개발 및 협업 가이드라인 (`CLAUDE.md`, `AGENTS.md`, `SKILL.md`) 복사 및 `kor-travel-docker-manager` 목적에 맞게 수정.
  - 에이전트 설정 파일 (`antigravity.json`, `claude.json`, `codex.json`, `.gemini/mcp.json`, `.claude/settings.local.json`, `.codex/config.toml`) 설정 완료.
  - 아키텍처 가이드(`docs/architecture.md`) 및 의사결정 기록(`docs/decisions.md` ADR-1 ~ ADR-4) 신규 생성.
  - 백로그 작업 시스템(`docs/tasks.md`) 및 환경 구축 문서(`docs/dev-environment.md`) 작성.
- **결정 사항**:
  - Python FastAPI 백엔드 + Next.js 프론트엔드의 모노레포 구조(ADR-1) 채택.
  - Docker Container 제어를 위해 Python Docker SDK(ADR-2) 채택.
  - 상태 동기화를 위해 TanStack Query(ADR-3) 및 Polling 방식 사용.
- **다음 작업**:
  - 루트 `.gitignore`, `docker-compose.yml`, `README.md` 작성.
  - 백엔드 (`backend/`) Poetry 초기화 및 FastAPI 뼈대 코드 작성.
  - 프론트엔드 (`frontend/`) Next.js 뼈대 코드 및 실시간 상태 대시보드 UI 구현.
## 2026-08-28 — M05 terminal registry의 원문 없는 phase 진단

Map `053904ce…`·PinVi `1b29bfea…`·Manager `8f41a9bd…`를 `rotate-pair`로 결박한 뒤 trusted
`run-pinned-rebuild-once`와 public generation gate를 통과했다. 그 다음 isolated M04/M05 one-shot은 정확히
한 번 실행되어 canonical pinset `5ad3b08c…`을 terminal로 차단했다. output leaf·HTTP 원문·container log·환경값은
열지 않는다. root registry의 공개 reason은 이제 임의 예외 문자열이 아니라 allowlist fixed phase만 기록한다.
따라서 다음 immutable source pair는 raw artifact 없이도 보정 범위를 알 수 있고, 같은 pinset의 재실행은 계속
불가능하다.
