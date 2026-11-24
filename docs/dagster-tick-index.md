# 공용 Dagster tick 인덱스(최신 조회·보존 정리)

Dagster 최신 tick SQL은 `selector_id = … OR (selector_id IS NULL AND job_origin_id = …)` 뒤 timestamp 내림차순·소수 limit를 사용한다. 두 조건의 BitmapOr 결과 전체를 정렬하면 history 증가와 다른 DB 쓰기의 I/O 경합 중 센서 평가와 UI 조회가 오래 기다릴 수 있다.

`docker/dagster-host/storage-migrate.py`는 PostgreSQL storage를 bootstrap한 뒤 `public.job_ticks("timestamp" DESC)`의 `ktdm_job_ticks_latest_timestamp` 인덱스를 준비한다. 이후 기존 Dagster migration을 exec한다. 데이터·기존 인덱스·조회 상태와 개수는 보존한다.

인덱스는 autocommit 연결에서 CONCURRENTLY로 만들며 연결 대기는 5초, lock 대기는 60초(2026-11-23 전 5초), SQL 작업은 600초(전 120초)로 제한한다. 같은 이름·같은 정의가 valid/ready면 재진입은 무연산이다. 같은 정의의 invalid/unfinished index만 drop concurrently 후 재생성한다. 다른 정의의 이름 충돌은 수동 확인이 필요한 오류다. 실패를 완료로 삼지 않는다.

운영 적용은 Manager의 storage 준비 단계가 소유하며 기존 배포 절차·호스트 lock을 따른다. 접속 URL은 선언된 PostgreSQL storage 설정에서 읽고 출력하지 않는다. 독립 운영 SQL을 각 consumer 저장소에 복제하지 않는다.

검증은 valid/ready 확인으로 끝내지 않는다. 같은 최신 ID/상태·조회 limit와 repository 범위를 보존하고 실행 계획·응답 시간을 전후 비교한다. GraphQL 원문·DB 접속값·운영 hostname은 커밋하지 않는다. 초기 timeout과 하니스 실패를 기록한다.

2026-10-14에 고정 구현의 독립 FULL 적대 리뷰 2건(OPEN 0), 관련 계약 318건, 실제 PostgreSQL·Dagster 이미지 4건을 통과했다. 운영에는 검토한 함수만 기존 Manager host lock 아래 적용했다(50.559초). 공유 webserver·daemon·Concierge code-server의 ID·restart 0·OOM false를 유지했으며 PostgreSQL/plane 재생성은 하지 않았다. 새 이미지 tag의 정식 rollout은 별도 Manager 릴리스 절차를 따른다.

같은 운영 최신 tick 조건의 계획은 Sort/BitmapOr에서 Index Scan으로 바뀌었다. repeatable-read의 같은 조건·최신 timestamp 범위 검증에서 단일 최신 ID/tick body와 valid/ready를 확인했다. 과거 history 정렬을 강제한 비교 하니스는 25초 outer timeout으로 실패했고 이를 성공 근거로 세지 않는다.

전체 기존 GraphQL 요약을 변경하지 않고 후속 5회 200/0.080~0.524초, 후보 API summary ok 및 n150 native Chromium live UI 90 PASS/skip·retry 0을 확인했다. 적용 직후 첫 표본의 timeout 2건과 이후 정상 3건은 별도 보존한다. 격리 16만 행 수치와 이 짧은 운영 표본을 모든 selector의 p95·장기 I/O/메모리 안정성 보장으로 확대하지 않는다.

## 보존 정리 인덱스(2026-11-23)

`storage-migrate.py`의 `TICK_INDEXES`가 Manager 소유 `job_ticks` 인덱스의 정본이다(이름·btree 열, 기대 정의는 거기서
파생). 두 번째 항목 `ktdm_job_ticks_selector_status_timestamp (selector_id, status, "timestamp")`는 Dagster daemon의
tick 보존 정리를 위한 것이다. daemon은 instigator의 tick이 끝날 때마다 `retention`의 보존 일수별로
`DELETE … WHERE timestamp < … AND status IN (…) AND (selector_id = … OR selector_id IS NULL AND job_origin_id = …)`를
부른다(sensor는 7·14·30일 셋). Dagster의 `(selector_id, timestamp)` 인덱스로는 더 긴 보존이 남긴 **다른 status**의
오래된 행까지 매번 읽고 걸러 낸다.

- 실측(n150 공용 PG, `pg_stat_statements` 11-15~23): 이 DELETE가 71만 회·19,887초·shared hit 20억·read 1,036만 블록,
  status 여럿인 형태가 37만 회·9,149초. `dagster_shared` 읽기 비용 1위였다(T-READ-MGR-04를 미룬 이유).
- 재현(운영 `job_ticks` 47.4만 행의 열 복사본 + tick_body 1.2 kB 채움, 비내구 tmpfs PG18, 같은 인덱스 집합, 한 번 정리해
  밀린 행을 지운 뒤의 정상 상태): instigator마다 한 바퀴(DELETE 157회) 평균 5.34 ms·199 블록, 최대 4,547 블록 →
  인덱스 뒤 0.019 ms·6 블록(최대 12). 계획은 BitmapOr의 selector 쪽이 새 인덱스의 `(selector_id, status, timestamp <)`
  범위가 된다. 최신 tick 조회(`ORDER BY timestamp DESC LIMIT`)는 그대로 `ktdm_job_ticks_latest_timestamp`를 쓴다.
  인덱스 크기 33~39 MB, tmpfs 빌드 2~4초.
- 대안으로 sensor 보존을 일수 하나(예: 모든 status 14일)로 줄여도 정상 상태 비용은 같게 사라진다(재현 0.022 ms) — 범위
  안의 행이 전부 지울 행이 되기 때문이다. 그러나 보존 의미(당시 SKIPPED 7일·FAILURE 30일, 2026-11-24부터 SKIPPED 2일)가 바뀌고, `dagster.yaml`
  digest가 모든 테넌트 code-server env에 실려 있어 그 변경은 테넌트마다 재생성을 부른다. 그래서 의미를 바꾸지 않는
  인덱스로 했다.

적용 경로: 인덱스는 storage migrate one-shot이 만든다. `dagster-plane converge`(와 재구축 안의 plane 수렴)는 plane을
다시 만들기 전에 render에서 파생한 `service_completed_successfully` 의존 one-shot을 `run --rm --no-deps`로 먼저 돌리므로,
host 이미지 tag가 바뀐 설치 뒤 plane converge 한 번이 인덱스를 만든다. CONCURRENTLY라 쓰기를 막지 않는다. 다만 빌드는 진행 중인 쓰기 transaction(daemon의 정리 DELETE 포함)을 기다리고
그 대기가 `lock_timeout`에 걸려 60초로 늘렸다. SQL 상한은 600초(디스크 대기가 큰 호스트의 첫 빌드)다. 실패하면 plane은 그대로 두고 converge가 실패한다 — 다음 실행이
invalid 인덱스를 지우고 다시 만든다.
