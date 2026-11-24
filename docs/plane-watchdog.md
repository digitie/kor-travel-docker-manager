# 공용 Dagster plane 수집 감시(plane watchdog)

> 근거: Map 저장소 `docs/reports/2026-11-23-collection-failure-audit.md` §4 #1(경보)·#3(fd 지표화)·#19(location 정지 진단).
> 결정: [ADR-64](decisions.md#adr-64-공용-dagster-plane-수집-감시는-manager-backend가-dagster-밖에서-돌린다).
> 임계값 정본: `config/plane-watchdog.yml`(이 문서는 값을 다시 적지 않는다).

## 1. 왜

두 plane(n150·odroid) 모두 경보가 없어 PinVi(11-13~16)·concierge(11-14~18) run 전부 실패, odroid 특보 이벤트
11-18 07:05Z부터 100% 실패를 아무도 몰랐다. n150 PinVi·concierge 사건은 code-server의 `dagster api grpc` 프로세스가
fd(soft 1024)를 다 써서 재시작될 때까지 55·100시간 동안 모든 launch와 sensor가 실패한 것이었고, 그동안 컨테이너
healthcheck는 healthy였다(평소 fd 13~24개).

## 2. 어디서 도나

- **Manager backend 프로세스 안의 백그라운드 루프**(`services/plane_watchdog.py`, `main.py` lifespan). backend는 systemd
  `ktdm-backend.service`(root)로 돌아 Dagster plane과 독립이다 — daemon·code-server가 죽어도 감시는 돈다. Dagster sensor로
  만들지 않는다(그 plane이 멈추면 경보도 멈춘다).
- 각 호스트의 Manager가 **자기 호스트의 plane**을 본다(공용 PG가 loopback 전용이라 원격 질의는 하지 않는다). registry가
  그 호스트에 `dagster` target을 두지 않으면 storage 점검은 "해당 없음"이다.
- 평가 주기는 설정의 `interval_seconds`. 화면은 마지막 평가 시각을 보여 주고, 3주기 넘게 평가가 없으면 "감시가 멈췄다"로
  표시한다(감시자 자신의 죽음이 보인다).

## 3. 규칙

| 규칙 id | 판정 | 자료 |
|---|---|---|
| `storage_unreachable` | storage 질의가 연속 N회 실패 | 아래 질의 |
| `location_tick_stall` | location의 활성 schedule·sensor 전부가 `max(min_silence, cadence_multiplier × 가장 짧은 기대 간격)` 동안 tick 없음 | instigator별 마지막 tick |
| `schedule_no_success` | 창(`cycles × cron 최대 간격 + grace`) 안 종료 run이 있는데 성공 0 | schedule의 job 종료 run |
| `tick_failures` | location 안 schedule·sensor의 최근 tick `count`개가 모두 FAILURE | instigator별 최근 tick 5개와 오류 문구 |
| `consecutive_failures` | job의 최근 종료 run이 `count`회 연속 FAILURE(CANCELED는 건너뜀) | 종료 run |
| `launch_failure_streak` | location의 최근 종료 run이 `count`회 연속 "시작 없이 FAILURE" | 종료 run(`start_time IS NULL`) |
| `launch_failure_rate` | 최근 창에서 launch 실패 비율 | 종료 run |
| `analytics_refresh_lag` | transport 공항 수집 run의 사전집계 상태 태그가 최근 `consecutive_runs`회 연속 경보 | 그 job의 최근 종료 run과 태그 하나(§3.2) |
| `disk_usage` | 사용률(df Capacity 식) | statvfs: `/`, Docker root, 공용 PG 데이터 mount |
| `io_pressure` | PSI `io full avg300`이 `for_evaluations`회 연속 임계 이상 | `/proc/pressure/io`(없으면 "확인 불가") |
| `dagster_fd_usage` | Dagster 컨테이너 안 **프로세스별** 열린 fd / 그 pid의 nofile soft 한도의 최댓값 | `/proc/<pid>/fd`·`limits`(호스트 PID는 `docker top`) |

세부 규칙:

- **활성 instigator**: 한 번이라도 tick했고, RUNNING이거나 `activity_lookback_hours` 안에 tick한 것. DB의 status만 믿지 않는다 —
  odroid transport schedule은 instigator 행이 STOPPED인데 5분마다 tick한다(2026-11-24 실측). 반대로 사람이 모든 instigator를
  끈 location은 lookback 동안 정지 경보가 날 수 있다(R4 같은 전수 정지 창). 의도된 정지면 무시한다.
- **schedule의 job**: schedule tag(`dagster/schedule_name`)가 붙은 가장 최근 run의 job이다. 성공은 그 job의 **모든 run**(수동·요청
  실행 포함)에서 센다 — schedule tag가 붙은 run만 보면 11-16 수동 성공이 있는 MCST job을 잘못 울린다(실측).
- **창이 feed보다 긴 schedule**(일·월 단위)은 그 job의 최근 종료 run 10개를 6시간마다 따로 읽는다. 창의 앞부분을 모르면 울리지
  않는다.
- 같은 job을 `schedule_no_success`가 이미 울리면 `consecutive_failures`는 그 job을 다시 울리지 않는다.
- **측정 실패**: 측정하지 못한 규칙은 "확인 불가"로 보이고, 그 규칙의 기존 경보는 그대로 둔다(측정 실패로 경보가 풀리지 않는다).

### 3.1 code-server 재시작 제안

`tick_failures`의 오류가 EMFILE(`Too many open files`·`Errno 24`) 또는 code-server 단절(`DagsterUserCodeUnreachableError`·
`DagsterCodeLocationLoadError`)이면, `launch_failure_streak`이면, `dagster_fd_usage`가 위험(grpc·code-server 프로세스)이면 경보
문구에 **재시작 명령을 제안**한다(`sudo -n docker restart <컨테이너>`, 컨테이너는 `docker top` 명령줄의 location으로 찾는다).
n150 PinVi 사건의 sensor tick 오류에는 "Too many open files" 문구가 없고 code-server 단절만 남았다(11-14 tick_body 실측) — 그래서
단절 문구도 제안 대상이다.

**자동 재시작은 구현하지 않았다.** 운영 runtime 변경은 C6c 경계(pinned workflow·host lock) 밖에서 일반 경로로 하지 않는다는
현행 계약과 충돌하고, 잘못된 재시작은 진행 중 run을 잃게 한다. 넣으려면 소유자 결정이 먼저다(§7).

### 3.2 공항 통계 사전집계 갱신(`analytics_refresh_lag`)

transport #283(`8041dfd`)은 공항 수집 op가 수집·사전집계 갱신 뒤 사전집계 상태를 판정해(transport `analytics_refresh_health`),
경보(`delayed`·`stale`·`missing`)면 **자기 run에 태그 `kortravel/analytics-refresh=<상태>`**를 단다. run은 SUCCESS 그대로다(수집은
성공했으므로 — 감사 §2.5의 부분 성공 FAILURE 소음을 되풀이하지 않는다). 상태 기준은 transport가 정본이다: 사전집계에 반영되지 않은
가장 오래된 적재 15분 이상 `delayed`, 40분 이상 또는 사전집계 2시간 초과 `stale`(대시보드가 원본 계산으로 떨어진다), 관측은 있는데
사전집계 없음 `missing`. Manager 설정은 심각도 대응과 연속 횟수만 정한다.

- **자료**: 설정한 (location, job)의 최근 종료 run과 그 태그 하나를 매 평가 읽는다(job 인덱스 역방향 30행 → 바깥에서 location 거름).
  2026-11-24 실측 odroid 69 ms(`runs_pkey` 역방향 71행), n150 0.2 ms(`idx_runs_by_job`).
- **연속**: 최신 run부터 태그 있는 run을 센다. 태그 없이 SUCCESS로 끝난 run(그 시점 정상, 또는 transport 판정 자체 실패)이 끊고,
  태그 없는 FAILURE·CANCELED는 건너뛴다(그 실패는 `consecutive_failures`·`schedule_no_success`가 본다).
- **심각도**: 최근 `consecutive_runs`회 중 가장 낮은 것 — 위험은 최근 N회가 모두 `critical_values`일 때만. 같은 경보 key가 주의에서
  위험으로 오르면 새 경보가 아니라 그 경보의 심각도가 바뀐다(`since` 유지, webhook 전이 없음 — 화면·상태 API·지표에는 바로 보인다).
  설정에 없는 태그 값은 주의로 센다.
- **해당 없음**: `active_hours` 안에 끝난 그 job의 run이 없으면 "해당 없음"이다. n150 plane에는 transport가 없다(공항 job은 2026-10-14
  odroid로 옮겼고 n150 storage에는 그 전 run만 남았다 — 2026-11-24 실측 3,268건, 마지막 10-14). odroid에서 job이 멈춘 것은
  `location_tick_stall`·`schedule_no_success`가 본다.
- **질의 실패**: 이 질의만 실패하면 이 규칙만 "확인 불가"이고 그 경보는 유지된다(다른 storage 규칙과 `storage_unreachable`은 그대로).
- **providers API를 쓰지 않은 이유**: transport `/v1/transport/providers`의 `airport_analytics_cache.alerting`도 같은 판정이지만, 감시가
  형제 서비스의 HTTP 경로(odroid gateway·backend 포트, 호스트 바인딩)에 새로 기대게 되고 호출마다 transport DB 질의를 일으킨다.
  run 태그는 이미 쓰는 읽기 전용 storage 경로(비밀번호·DSN 없음) 안에 있고, 판정은 transport가 run 시점에 한 그대로다. 대가: run이
  멈추면 태그도 멈춘다 — 그때는 위 두 규칙이 울린다.
- 화면 문구(상태별 설명)는 표시용일 뿐 판정에 쓰지 않는다. transport가 기준을 바꾸면 문구만 낡는다.

## 4. storage 질의(읽기 전용)

`docker exec -i --user postgres --env PGAPPNAME=ktdm-plane-watchdog <공용 PG> psql …`로 컨테이너 안 local socket에 붙는다(백업
primitive와 같은 경로, 비밀번호·DSN을 읽지 않는다). 모든 질의는 `BEGIN READ ONLY`·`statement_timeout`·`lock_timeout 2s` 안에서
돈다. 결과는 `jsonb_agg` 한 값이다.

| 질의 | 주기 | 인덱스 | 실측(2026-11-24, 순수 실행) |
|---|---|---|---|
| instigator + 최근 tick 5개 | 매 평가 | `idx_tick_selector_timestamp` 역방향 | n150 7.7 ms(82 instigator) |
| 종료 run 증분(`update_timestamp >= cursor - 5분`) | 매 평가 | `idx_run_range(status, update_timestamp)` + `idx_run_tags_run_idx` | n150 0.3 ms·odroid 12 ms(7분 창) |
| 종료 run 시작 시 적재(`bootstrap_hours`) | 기동 1회 | 같음 | n150 48시간 239 ms·odroid 48시간 15 s(load 23) |
| 사전집계 태그(설정한 job 최근 run) | 매 평가 | `runs_pkey`·`idx_runs_by_job` 역방향 + `idx_run_tags_run_idx` | odroid 69 ms·n150 0.2 ms |
| 긴 창 schedule의 job 최근 run | 6시간 | `idx_run_tags(key, value)` → `idx_runs_by_job` 역방향 30행 | location 거르기를 EXISTS로 하면 odroid 22.6 s(semi-join) → 바깥 거르기로 바꿈 |

odroid는 CPU 포화로 가벼운 질의도 수 초가 걸린다. `statement_timeout`·`bootstrap_hours`는 그 실측에 맞췄다.

## 5. location 정지 진단(감사 #19)

`location_tick_stall`이 처음 울릴 때(같은 location은 `min_interval_minutes`에 한 번, 전체 `max_captures`개까지) 백그라운드
스레드가 `diagnostics.directory`(기본 `/var/lib/kor-travel-docker-manager/plane-watchdog/diagnostics/<UTC>-<location>/`,
0700/0600)에 남긴다.

- `py-spy-<컨테이너>-<pid>.txt`: daemon과 그 location code-server의 Python 프로세스마다 `py-spy dump --pid`(py-spy가 있을 때).
  ptrace로 잠깐 멈춰 읽고 뗀다. **프로세스를 죽이거나 재시작하거나 signal을 보내지 않는다** — 대상 Dagster 이미지에는
  faulthandler·SIGUSR 처리기가 없어(2026-11-24 확인) SIGUSR1은 기본 동작(종료)이 된다.
- `threads-<컨테이너>-<pid>.txt`: 스레드마다 이름·상태(R/S/D)·wchan·커널 스택(항상, py-spy 없이도).
- `docker-logs-<컨테이너>.txt`: 최근 `log_tail_minutes`분 로그.
- `summary.json`: 경보 내용·수집 결과.

### 5.1 fd 누수 진단

PinVi·concierge fd 고갈은 재현되지 않아 원인이 미확정이다(가설: 당시 grpcio 1.84.0, IO 지연 조건, concierge와 공통 요소).
다음 발생 때 가르기 위해 `dagster_fd_usage` 경보가 **주의(50%)일 때 한 번, 위험(80%)으로 오를 때 한 번**(같은 경보 key라도
심각도마다) 그 컨테이너의 `dagster api grpc` 프로세스(없으면 Python 프로세스 전부)를 `<UTC>-fd-<컨테이너>/`에 남긴다.

- `fd-<컨테이너>-<pid>.txt`·`summary.json`: 열린 fd 종류별 개수(socket·pipe·anon_inode·file·device), 소켓은 그 프로세스의
  network namespace(`/proc/<pid>/net/{tcp,tcp6,udp,udp6,unix}`)와 inode로 대조한 `proto 상태 local -> remote`별 개수(대조 못 한
  소켓 수 포함), 파일은 경로별 개수, nofile 한도, **스레드 수**(`/proc/<pid>/status`).
- **패키지 버전**: 그 프로세스와 같은 인터프리터로 `docker exec <컨테이너> <python> -c …`(새 프로세스 하나)를 띄워
  grpcio·dagster·dagster-postgres·protobuf·psycopg2·sqlalchemy·kortravelcommon 버전을 읽는다.
- 읽기만 한다. 대상 프로세스에 signal·재시작이 없다.

**py-spy는 두 호스트 모두 아직 없다**(2026-11-24 확인, n150 x86_64·odroid aarch64 모두 PyPI wheel이 있다). 설치 전에는 `/proc`
스레드 상태와 로그만 남는다. 디렉터리는 지우지 않는다 — 정리는 사람이 한다.

## 6. 노출

- 화면: 대시보드 본문 "수집 경보" 패널(메뉴 `서비스 관리 › 수집 경보`). 진행 중 경보, 점검 항목(확인 불가 포함), 발송 상태,
  진단 파일, 최근 해소.
- 상태 API: `GET /api/v1/plane-alerts`(관리자 세션). 요청이 storage를 질의하지 않는다.
- 로그: firing은 WARNING `PLANE ALERT firing …`, 해소는 INFO(journald `ktdm-backend`).
- Prometheus(`/metrics`): `ktdm_plane_alerts_firing{rule,severity}`, `ktdm_plane_watchdog_last_evaluation_timestamp_seconds`,
  `ktdm_dagster_container_open_fds`·`_open_fds_total`·`_nofile_soft_limit{container}`(fd 누수 추적),
  `ktdm_dagster_location_tick_age_seconds{location}`.

## 7. 발송(소유자 결정 대기)

Manager에는 기존 알림 발송 수단이 없다(Alertmanager는 두지 않기로 했다, ADR-58 결정 11 부근; Resend 이메일 연동은 소유자
지시로 보류 #720). 그래서 기본 경로는 화면·상태 API·로그이고, 외부 발송은 **일반 webhook(JSON POST) 하나를 기본 꺼짐으로**
둔다. 켜려면 `config/plane-watchdog.yml`의 `notifiers.webhook.enabled: true`와 root `.env`의 `KTDM_ALERT_WEBHOOK_URL`.
전이(firing·resolved) 때만 보내고 같은 상태가 계속되면 다시 보내지 않는다. 실패는 재시도하지 않고 화면에 종류만 남긴다(URL은
비밀이라 남기지 않는다).

소유자가 정할 것: 발송 채널(webhook 수신처·Telegram 등), code-server 자동 재시작 허용 여부.

## 8. 시험

`backend/tests/test_plane_watchdog.py`가 감사 당시 실제 사건을 `backend/tests/fixtures/plane_watchdog/`(두 호스트 storage에서 읽기
전용으로 뽑은 실물)로 재생한다: weather location 정지(11-19 17:15 → 17:50 발화 → 20:22 해소), 특보 이벤트(11-18 07:52 발화),
PinVi launch 연속 실패(11-13 19:57 발화), PinVi sensor tick 연속 실패(11-14). 공항 통계 사전집계 규칙은 odroid transport 공항 수집 run
40개(11-24 01:21~07:26Z, 실물 간격)에 시나리오 태그를 덧씌워 재생한다(#283 태그는 추출 시점 배포 전): 한 번 튄 delayed 무시 → delayed
2회 주의 → stale 1회는 아직 주의 → 태그 없는 FAILURE 건너뜀 → stale 2회 위험 → 태그 없는 SUCCESS로 해소, n150 해당 없음.
화면은 `frontend/e2e/plane-alerts.spec.ts`.
