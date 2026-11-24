# 공용 Dagster storage 보존 정리

2026-11-24 소유자 결정(ADR-66). 두 plane(n150·odroid)의 공용 Dagster storage(`dagster_shared`)에서 오래된 run·event_log와
멈춘 instigator의 tick을 Manager가 주 1회 지운다. 설정은 두 plane 공통이다.

배경: Dagster의 `retention`(`config/dagster-shared/dagster.yaml`)은 **RUNNING instigator의 tick만** 그 instigator의 다음 tick에서
지운다. run·event_log에는 보존 설정이 없고, 멈췄거나 workspace에서 빠진 instigator의 tick은 아무도 지우지 않는다. 2026-11-23
수집 실패 감사 §1.4: n150 event_logs 2.0 GB·job_ticks 1.1 GB가 무기한 증가.

## 1. 무엇을 지우나

| 대상 | 조건(설정 키) | 함께 지우는 것 |
|---|---|---|
| SUCCESS run | 마지막 갱신(`runs.update_timestamp`)이 90일 전(`runs.success_days`) | 그 run의 event_logs, run_tags(FK cascade), asset_event_tags, asset_check_executions |
| FAILURE·CANCELED run | 180일 전(`runs.failure_days`) | 같음 |
| 멈춘 instigator의 tick | instigator가 RUNNING이 아니고(STOPPED·DECLARED_IN_CODE·행 없음) 14일(`ticks.stopped_instigator_days`) 안에 tick이 없는 selector의, 14일보다 오래된 tick | — |

**지우지 않는 run**(나이가 넘어도):

- 진행 중·대기 상태(QUEUED·NOT_STARTED·STARTING·STARTED·CANCELING·MANAGED) — 종료 상태만 지운다.
- 자산 상태를 쥔 run: `asset_keys.last_run_id`가 가리키는 run, 자산의 (asset_key, event 종류, partition)별 **최신** event를 가진
  run, asset check (asset_key, check_name)별 최신 실행의 run. 지우면 자산 카탈로그·partition 상태가 바뀐다.
- 남는 run이 `dagster/parent_run_id`·`dagster/root_run_id`로 가리키는 run(재실행·재시도 계보가 끊기지 않게).
- 아직 있는 backfill(`bulk_actions`)의 run, concurrency slot·대기 step을 쥔 run.

RUNNING instigator의 tick은 손대지 않는다 — Dagster가 자기 tick 때 보존 설정대로 지운다(월 1회 schedule처럼 14일 안에 tick이
없어도 다음 tick에 지운다). selector가 NULL인 옛 형식 tick은 세기만 하고 지우지 않는다(두 호스트 0행).

## 2. 왜 Dagster `delete_run`이 아닌가

Dagster 1.13.24의 `DagsterInstance.delete_run(run_id)`은 `runs` 행 삭제(run_tags는 FK cascade)와 `delete_events`(그 run의
event_logs, materialization·observation event의 asset_event_tags 삭제, concurrency slot 해제)를 각자 연결로 부른다. 정리 job은
**같은 표를 같은 순서로** 지우되 SQL 한 트랜잭션으로 옮기고 셋을 더한다.

1. 배치·timeout: run 200개(설정)를 한 트랜잭션으로, `statement_timeout`·`lock_timeout`을 건다. `delete_run`은 run마다 연결 셋이고
   timeout을 걸 수 없다.
2. 자산 상태 보존: `delete_run`은 자산의 최신 event까지 지운다. 위 보호 규칙이 그런 run을 남긴다.
3. `asset_check_executions`: `delete_run`은 남겨 고아가 된다. 정리 job은 함께 지운다.

concurrency slot·대기 step은 해제 대신 그것을 쥔 run을 지우지 않는다(종료 run이면 Dagster가 이미 풀었어야 한다).

정리 job은 Dagster를 import하지 않는다. 그래서 schema가 검토한 판(Dagster 1.13.24, alembic head `29b539ebc72a`)일 때만 돈다 —
다르면 아무것도 지우지 않고 실패한다. host 이미지의 Dagster 핀을 올리면 `test_pinned_dagster_version_matches_the_host_image`가
SQL 재검토를 요구한다. 손대지 않는 표: `snapshots`(job·실행 계획 snapshot — `delete_run`도 남긴다, 두 호스트 12행), `jobs`·
`instigators`·`asset_keys`·`kvs`·`daemon_heartbeats`.

시각: `runs.update_timestamp`·`job_ticks.timestamp`는 Dagster가 UTC naive로 쓴다. 경계는 `now() AT TIME ZONE 'UTC'`에서 뺀다.
최신 run·tick 시각이 UTC 지금보다 한 시간 넘게 앞서면(시각이 UTC가 아니다) 지우지 않는다.

## 3. 실행

- 모듈: `backend/src/kor_travel_docker_manager/dagster_storage_retention.py`, 설정: `config/dagster-storage-retention.yml`(모든 키 필수).
- 접근: 백업·수집 감시와 같은 길 — `docker exec … psql`(공용 PG 컨테이너 안 local socket, `POSTGRES_USER`). 비밀번호·DSN을 읽지
  않는다. 각 호스트가 자기 plane의 storage만 본다.
- 주기: systemd timer `ktdm-dagster-storage-retention.timer` — 일요일 06:10 KST(+최대 10분). 두 호스트 14일 실측에서 run 부하가
  낮은 시각이다(n150 06시 대 7.9 run-시간, odroid 37.4 — 하루 중 낮은 쪽). 공용 Dagster 밖이라 plane이 멈춰도 돈다.
- 순서: tick → SUCCESS → FAILURE → CANCELED. 배치가 가득 차면 `batch.pause_seconds` 쉬고 다음 배치. 한 번 실행은
  `timeouts.max_runtime_seconds`(45분)에서 멈추고 남은 것은 다음 주에 이어 지운다(`budget_exhausted: true`, 실패 아님).
- 배치가 실패하면(lock·statement timeout 등) 그 자리에서 멈추고 exit 1. 이미 커밋한 배치는 그대로다. 배치마다 지우는 순간에 조건을
  다시 보므로(`FOR UPDATE SKIP LOCKED`, tick은 RUNNING·최근 tick 재확인) 그새 켜진 instigator의 tick은 지우지 않는다.
- 겹침: 같은 호스트에서 정리끼리는 `/run/lock/ktdm-dagster-storage-retention.lock`으로 막는다. 배포 lock(G)은 잡지 않는다 —
  한 시간 가까이 배포를 막지 않으려고. 배포·storage migrate와 겹치면 `lock_timeout`(5초)이 그 배치를 실패시킨다.
- dry-run의 run 집계는 지울 run 전부를 한 읽기 전용 문장으로 세므로 상한이 따로다(`timeouts.plan_statement_seconds` 600초 —
  2026-11-24 30일 가정 실측: n150 2.4만 run 12초, odroid 4.4천 run 53초). apply의 배치는 200 run씩이라 60초 상한 안이다.
- 출력: JSON 한 줄씩 — 배치마다 `{"event": "batch", "phase": …}`, 끝에 `{"event": "summary", …}`. journal에 남는다.
- 디스크: PostgreSQL은 지운 행의 자리를 autovacuum 뒤 재사용한다. 파일 크기를 줄이지는 않는다(VACUUM FULL은 하지 않는다).

```sh
# dry-run(읽기 전용 트랜잭션, 지울 양만 센다)
sudo /opt/kor-travel-docker-manager/backend/.venv/bin/python -I -m kor_travel_docker_manager.dagster_storage_retention
# 한 번 실행
sudo /opt/kor-travel-docker-manager/backend/.venv/bin/python -I -m kor_travel_docker_manager.dagster_storage_retention --apply
# timer 설치(설치한 release의 deploy/systemd 두 파일)
sudo install -m 0644 deploy/systemd/ktdm-dagster-storage-retention.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now ktdm-dagster-storage-retention.timer
```

## 4. 첫 실행 예상량(2026-11-24 읽기 전용 실측)

| | n150 | odroid |
|---|---|---|
| 가장 오래된 run | 2026-09-30(공용 storage 시작) | 2026-10-14 |
| 지울 run(90/180일) | **0** — 첫 SUCCESS 삭제는 2026-12-29 무렵 | **0** — 2027-01-12 무렵 |
| 멈춘 instigator tick | **70,576행**, 41 selector(행 없음 35·12,249행, DECLARED_IN_CODE 5·48,548행, STOPPED 1·9,779행) | **325행**, 3 selector(행 없음) |
| 건드리지 않는 RUNNING schedule의 14일 넘은 tick | 14행(월 1회 schedule 14개) | 1행 |
| 정상 상태 주간 run 삭제 | 약 1만 run·19만 event_log 행(하루 약 1,450 run, run당 event 약 19행) | 약 5천 run·10만 event_log 행(하루 약 750 run) |

storage 크기(같은 날): n150 event_logs 2,040 MB·job_ticks 1,093 MB·run_tags 233 MB·runs 136 MB, odroid event_logs 858 MB·
job_ticks 184 MB. 두 호스트 모두 partition 있는 asset event·asset check 실행·backfill은 0행이다.

## 5. 시험

- `backend/tests/test_dagster_storage_retention.py`: 설정 검증, dry-run이 읽기만 하는지, 배치·쉼·시간 상한, schema·시각 사전 확인,
  배치 실패 시 멈춤, selector 검증, SQL이 run 키 표를 빠짐없이 다루는지.
- `backend/tests/test_dagster_storage_retention_integration.py`: 실제 공용 PG·Dagster 1.13.24 storage(정본 compose의 db-init·storage
  migrate)에 Dagster API로 run·event·materialization·instigator·tick을 만들고 정리 — 지울 것만 지우는지, 고아 0, 정리 뒤 Dagster가
  run·로그·최신 materialization·tick을 그대로 읽는지, dry-run 무변경, 두 번째 apply 0. gate는 `KTDM_REQUIRE_DOCKER_INTEGRATION`.
