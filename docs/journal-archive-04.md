# JOURNAL 아카이브 04 — 항목 제목 날짜 2026-08-28 ~ 2026-09-02

이 파일은 [`docs/journal.md`](journal.md)의 아카이브다(2026-10-16 분리). 파일 목록은 `journal.md` 상단 "과거 기록 아카이브" 표에 있다. 본문은 이동 당시 byte 그대로이며(원래 파일 순서 — 대체로 최신이 위지만 엄격한 시간순은 아니다) 아무 내용도 고치지 않는다.

---

## 2026-09-02 — docker-targets.yml 스키마 검증 잔여 3건 해소 (GM-11 후속)

`docs/tasks.md`에 남아 있던 GM-11 후속 세 항목을 순서대로 이행했다.

**(1) 지연 계산 + 깔끔한 CLI 오류.** `registry.py`의 `MANAGED_CONTAINERS`/
`MANAGED_TARGETS`/`TARGET_ALIASES`는 원래 모듈 import 시점에 즉시 계산되는
plain dict였다 — `load_targets_config()` 자체는 이미 `@lru_cache`였지만, 이
세 상수의 계산이 top-level 코드라 config가 깨지면 이 모듈을 그저 import만
해도 죽었다. `collections.abc.Mapping`을 구현하는 `_LazyMapping`으로 바꿔
`loader`가 최초 실제 접근(구독/순회/`in`/`.items()`)에서만 호출되게 했다 —
기존 20여 곳의 `MANAGED_CONTAINERS[...]`/`.items()`/`in` 사용부는 전혀 손대지
않았다(Mapping 프로토콜을 구현하면 이 문법이 그대로 동작한다). `cli.py`의
`DIRECT_ENSURE_ALIASES`도 모듈 top-level 상수에서 `_direct_ensure_aliases()`
함수로 옮겨 `main()`의 dispatch 직전에만 호출되게 했고, `main()`의
`build_parser()`~`args.func(args)` 구간 전체를 `except ValueError`로 감싸
registry.py의 이미 명확한 검증 메시지를 raw traceback 대신 stderr 한 줄 +
exit 1로 낸다.

여기서 끝나지 않고 임시 깨진 config로 fresh subprocess(`python -m
kor_travel_docker_manager.cli --help`)를 실제로 실행해 재현했더니, 별도
원인이 하나 더 드러났다: `metrics_collector.py` 맨 끝의 모듈 레벨 싱글턴
`metrics_collector = MetricsCollector()`가 `__init__`에서 `MANAGED_CONTAINERS`를
`{key: self._default_observation(key) for key in MANAGED_CONTAINERS}`로 즉시
순회한다 — `cli.py`가 `docker_service`를 top-level import하는 순간(`targets
validate`나 `pin show`처럼 Docker와 무관한 명령이라도) 이 순회가 실행돼 같은
문제가 재현됐다. `DockerService.__init__`의 `_backup_default_config`가 이미
써 온 것과 같은 패턴(생성자에서 config 로딩 실패를 삼키고 로그만 남김)을
적용해, 그 dict comprehension을 `try/except ValueError`로 감싸 fail-open(빈
dict로 시작, `logger.error`로 로그 남김)하게 고쳤다. `_collect_loop`가 매
10초 tick마다 `collect_metrics()`를 부르고 그 안의 `MANAGED_CONTAINERS` 순회가
같은 예외를 다시 내는데, 이미 `except Exception: logger.error(...)`로 감싸져
있어 조용히 사라지지 않고 계속 로그로 드러난다 — 관측 가능성을 잃지 않는다.

이 fail-open의 대가로 backend(FastAPI) 프로세스도 이제 깨진 config에서
import/기동 자체는 막지 않는다. 이것은 GM-11이 "의도적으로" 만들었다고 감사
기록에 남겼던 "backend 기동도 막는다"는 성질을 이번 후속이 명시적으로
되돌리는 것이다 — 대신 매 metrics 수집 tick과 실제 target/container 조회가
필요한 요청에서 같은 오류가 계속 관측 가능하게 다시 난다. `ktdctl`을 위해
지연 계산을 도입하면서 이 대가도 함께 받아들이는 것이 자연스러운 방향이라고
판단했다(그대로 두면 `ktdctl`만 고쳐지고 fastAPI 프로세스는 여전히 같은
싱글턴 경로로 죽었을 것이다).

**(2) `ktdctl targets validate`.** `targets` 서브커맨드를 `pin`과 같은 nested
subparser 패턴(`targets_subparsers = targets.add_subparsers(dest=
"targets_action", required=True)`)으로 바꿔 `targets list`(기존 flat `targets`
동작 그대로, `--json` 포함)와 `targets validate`(성공 시 `OK` 출력 + exit 0,
실패 시 `ValueError` 메시지를 stderr에 출력 + exit 1)로 나눴다.
`docs/docker-management.md` 3절의 GM-11 안내를 갱신해 `ktdctl targets
validate`를 정본 pre-flight 검증 경로로 세우고, 예전 python 한 줄짜리
워크어라운드는 `ktdctl` 미설치 환경을 위한 대안으로 남겼다. stale 라인 인용
(`registry.py:135`가 `MANAGED_CONTAINERS = load_targets_config()[...]`의 옛
top-level 자리를 가리키던 것)도 `registry.py:187-191`(현재 `_LazyMapping`
인스턴스 세 개가 있는 자리)로 고쳤다. README.md/`docs/dev-environment.md`의
예전 flat `ktdctl targets` 예시도 `ktdctl targets list`로 맞췄다.

**(3) frontend 주석 정정.** `DashboardClient.tsx`의 두 댓글
(`detailTarget`/`containerGroups` 자리)이 `target.containers`를 여전히
"`depends_on` 전이 폐포"로 설명하고 있었다 — GM-11의 `_validate_targets_config`
docstring이 이미 이 전제가 틀렸다고 확인한 내용이다(모니터링 target이
`depends_on` 폐포엔 있어도 `containers`엔 없는 것이 반증). 두 댓글을
"`containers`는 `config/docker-targets.yml`에 target마다 손으로 채운 고정
목록이고, 참조 무결성만 검증되지 유도되지 않는다"는 정확한 설명으로 고쳤다
— 근거로 `rustfs`가 `geo`/`conc`/`map`/`pinvi`/`all` target의 `containers`에
각각 독립적으로 반복 등재돼 있는 실제 config를 인용했다. narrowest-target/
`기타` 버킷 로직 자체는 이미 이 손 목록 전제와 무관하게 올바르게 동작해서
변경하지 않았다(댓글만 정정, 동작은 그대로).

`test_docker_manager_cli.py`에 6건(broken-config 종단 확인 1건,
`_cmd_targets_validate` 직접 호출 성공/실패 1건, through-`main()` 성공/실패
2건, `targets list` 하위호환 1건, sub-action 없는 `targets` 거부 1건),
`test_registry_targets_config.py`에 2건(`_LazyMapping`이 생성 시점엔
`loader`를 부르지 않는다는 계약, `MANAGED_CONTAINERS` 자신이 실제 접근에서만
예외를 낸다는 계약), `test_prometheus_metrics.py`에 1건(`MetricsCollector()`
생성이 깨진 config에서도 살아남되 `render_prometheus_metrics()` 같은 실제
호출 시점 메서드는 여전히 같은 예외를 낸다는 대조)을 추가했다. 새 테스트
전부 mutation-test로 원복 전 실패를 확인한 뒤 복원했다. 전체 backend 1501
passed, 2 skipped, ruff 통과. frontend type-check/lint/test/build 전부 통과.

**적대적 리뷰 2건(item2-targets-validate 재검토) 반영.** 두 리뷰가 독립적으로
같은 Medium 결함을 짚었다: fresh 프로세스(진짜 `ktdctl` 사용)에서 깨진
config면 stderr에 두 줄이 찍힌다 — `metrics_collector.py`의 모듈 레벨 싱글턴
생성자가 fail-open하며 남기는 `logger.error(...)`가 `cli.py`가 로깅을 전혀
설정하지 않아 `logging.lastResort`를 통해 형식 없이 그대로 새고, 바로 뒤에
`main()`의 깔끔한 한 줄이 따라붙는다. 기존 회귀 테스트(`len(err.strip()
.splitlines()) == 1`)는 pytest가 이 모듈을 collection 시점에 이미 정상
config로 import해 둔 뒤라 그 `except` 분기 자체가 테스트 안에서 실행되지
않아 통과해 버리고 있었다 — 직접 fresh subprocess로 재현해 확인(두 줄).
`logger.error(...)` 호출을 제거했다: 관측성은 잃지 않는다(`_collect_loop`가
매 tick마다 같은 예외를 다시 내고 이미 `except Exception`으로 감싸여 있어
FastAPI 백엔드가 실제로 수집을 시작한 뒤에는 `main.py`가 구성한 포맷 있는
핸들러로 계속 로그에 드러난다). 새 실제-subprocess 회귀 테스트를 추가해
pytest의 import/logging-capture 부작용을 우회하고 진짜 stderr를 확인한다.

두 번째 리뷰는 추가로 Medium 하나를 짚었다: `main()`의 outer `except
ValueError`가 `args.func(args)` 전체(~30개 명령 핸들러)를 감싸, config
오타와 무관한 내부 불변식 위반(`compose_service.py`/`c6c_deployment.py`의
"stage is invalid"/"attempt count must be positive" 같은 프로그래밍 버그성
assert)까지 "config 오류인 척하는 exit 1"로 둔갑시킬 표면적을 남겼다(오늘은
그 두 사이트가 `_cmd_pinvi_pair` 자신의 더 좁은 기존 `except ValueError`에
먼저 걸려 실제로 새지는 않지만, 새 명령이 추가될 때마다 그 전제가 계속
성립한다는 보장이 없다). `registry.py`에 `TargetsConfigError(ValueError)`
전용 서브클래스를 추가해 스키마/참조 무결성 검증 raise 전부(`load_
targets_config`/`_validate_targets_config`/`_require_list_field`)를 여기로
옮기고, `main()`/`_cmd_targets_validate`/`MetricsCollector.__init__`은
`TargetsConfigError`만 좁혀 잡도록 고쳤다 — `ValueError`를 상속하므로 기존
`except ValueError:` 호출부(`resolve_target_name`/
`container_id_to_compose_service`의 "잘못된 사용자 입력" 오류를 잡던
`_cmd_status`/`_cmd_ensure`/`_cmd_action` 등)는 전혀 바뀌지 않는다. 새
테스트로 (1) 실제 subprocess에서 stderr가 정확히 한 줄인지, (2) `main()`이
config와 무관한 bare `ValueError`를 더 이상 삼키지 않는지(monkeypatch로
명령 핸들러가 그런 예외를 내게 하고 실제로 새 나오는지 확인), (3)
`_validate_targets_config`가 실제로 `TargetsConfigError` 타입(단순
`ValueError` 상위 타입 매치가 아니라)을 내는지를 각각 추가했다. 세 건 모두
mutation-test(고쳤던 코드를 되돌려 새 테스트가 기대한 이유로 실패하는지
확인한 뒤 복원)로 효력을 확인했다. 리뷰가 짚은 나머지(사전 존재하던 `targets`
sub-action 필수화로 인한 exit 2 변경)는 의도된 breaking change로 문서·테스트에
이미 반영돼 있어 손대지 않았다. 전체 backend 1504 passed, 2 skipped, ruff
통과.

## 2026-09-02 — WebSocket 경로 요청 상관관계 ID 공백 해소 (GM-16 후속)

`docs/tasks.md`에 남아 있던 GM-16 후속 항목을 이행했다. `main.py`의
`@app.middleware("http")`(Starlette `BaseHTTPMiddleware`)는 `scope["type"] !=
"http"`인 요청을 명시적으로 skip하므로 WebSocket 연결에는 전혀 실행되지 않았다
— `request_id_var`가 기본값 `"-"`에 머물러 `/ws/status`·`/ws/logs/{id}`의 로그
줄(특히 4401/1013/4000 거절 로그)이 UI에 노출되는 request id와 상관지어지지
않았다.

`websocket.py`의 `ws_status`/`ws_logs` 두 handler 모두 **함수 첫 줄**에서
main.py 미들웨어와 정확히 같은 패턴(`request_id = str(uuid.uuid4()); token =
request_id_var.set(request_id)`)으로 직접 발급하도록 고쳤다. 두 handler 모두
shed 검사(1013)·인가 거부(4401)·container 미상(4000, ws_logs만) 조기 return이
기존 inner try/finally보다 앞에 있어, 새 outer try/finally가 함수 전체(그
조기 return들과 기존 inner try/finally 전부)를 감싸도록 구조를 바꿨다.
`request_id_var.reset(token)`은 기존 inner finally(status_manager.disconnect
/log_stream.close, 마지막 `_close_best_effort`)가 전부 끝난 뒤 이 outer
finally에서 마지막으로 실행된다 — inner finally 안에서만 reset했다면 조기
return하는 거절/shed 연결이 inner try에 도달하지 못해 contextvar token이
새어나갔을 것이다(다음 연결이 이전 연결의 토큰을 reset하는 등 격리가 깨짐).

**명시적 범위 제한(버그 아님)**: `ConnectionManager.broadcast()`와
`status_broadcast_loop()`는 main.py lifespan에서 단 한 번 시작되는 독립
asyncio task로, 어느 개별 연결의 request_id_var 컨텍스트 안에도 있지 않다 —
한 번의 broadcast tick이 여러 연결에 동시에 fan-out되므로 "이 로그의
request_id"라 부를 단일 연결이 애초에 없다. 이 구조적 한계를 조용히 넘어가지
않고 두 자리 모두에 그 이유를 설명하는 주석을 남겼다(고치려는 시도는 하지
않음 — 상관관계가 필요하면 각 연결의 개별 handler가 자기 컨텍스트로 남기는
로그를 봐야 한다).

`test_ws_contract.py`에 caplog 기반 회귀 테스트 3건을 새로 추가했다(기존
파일에는 request_id 관련 검증이 전혀 없었다): 거절된(4401) `/ws/status`·
`/ws/logs/{id}` 연결도 실제 uuid4 request id를 로그에 남기는지, 그리고 연속된
두 `/ws/status` 연결이 서로 다른 id를 받는지(격리 — token 누수 시 값이
재사용되거나 크래시함). mutation-test로 두 handler 각각에서
`request_id_var.set()`을 함수 첫 줄이 아니라 `_accept_best_effort()` 성공
이후로 옮겨봤고, 해당 새 테스트가 정확히 실패함(거절 경로 로그가 `"-"`로
남고, 조기 return 시 `token`이 미정의라 outer finally에서
`UnboundLocalError`)을 확인한 뒤 원래 위치로 되돌렸다. 백엔드 전체 스위트
1492 passed / 2 skipped, ruff 통과.

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

