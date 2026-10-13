# 공용 Dagster 최신 tick 조회 인덱스

Dagster 최신 tick SQL은 `selector_id = … OR (selector_id IS NULL AND job_origin_id = …)` 뒤 timestamp 내림차순·소수 limit를 사용한다. 두 조건의 BitmapOr 결과 전체를 정렬하면 history 증가와 다른 DB 쓰기의 I/O 경합 중 센서 평가와 UI 조회가 오래 기다릴 수 있다.

`docker/dagster-host/storage-migrate.py`는 PostgreSQL storage를 bootstrap한 뒤 `public.job_ticks("timestamp" DESC)`의 `ktdm_job_ticks_latest_timestamp` 인덱스를 준비한다. 이후 기존 Dagster migration을 exec한다. 데이터·기존 인덱스·조회 상태와 개수는 보존한다.

인덱스는 autocommit 연결에서 CONCURRENTLY로 만들며 연결·lock 대기는 각각 5초, SQL 작업은 120초로 제한한다. 같은 이름·같은 정의가 valid/ready면 재진입은 무연산이다. 같은 정의의 invalid/unfinished index만 drop concurrently 후 재생성한다. 다른 정의의 이름 충돌은 수동 확인이 필요한 오류다. 실패를 완료로 삼지 않는다.

운영 적용은 Manager의 storage 준비 단계가 소유하며 기존 배포 절차·호스트 lock을 따른다. 접속 URL은 선언된 PostgreSQL storage 설정에서 읽고 출력하지 않는다. 독립 운영 SQL을 각 consumer 저장소에 복제하지 않는다.

검증은 valid/ready 확인으로 끝내지 않는다. 같은 최신 ID/상태·조회 limit와 repository 범위를 보존하고 실행 계획·응답 시간을 전후 비교한다. GraphQL 원문·DB 접속값·운영 hostname은 커밋하지 않는다. 초기 timeout과 하니스 실패를 기록한다.

2026-10-14에 고정 구현의 독립 FULL 적대 리뷰 2건(OPEN 0), 관련 계약 318건, 실제 PostgreSQL·Dagster 이미지 4건을 통과했다. 운영에는 검토한 함수만 기존 Manager host lock 아래 적용했다(50.559초). 공유 webserver·daemon·Concierge code-server의 ID·restart 0·OOM false를 유지했으며 PostgreSQL/plane 재생성은 하지 않았다. 새 이미지 tag의 정식 rollout은 별도 Manager 릴리스 절차를 따른다.

같은 운영 최신 tick 조건의 계획은 Sort/BitmapOr에서 Index Scan으로 바뀌었다. repeatable-read의 같은 조건·최신 timestamp 범위 검증에서 단일 최신 ID/tick body와 valid/ready를 확인했다. 과거 history 정렬을 강제한 비교 하니스는 25초 outer timeout으로 실패했고 이를 성공 근거로 세지 않는다.

전체 기존 GraphQL 요약을 변경하지 않고 후속 5회 200/0.080~0.524초, 후보 API summary ok 및 n150 native Chromium live UI 90 PASS/skip·retry 0을 확인했다. 적용 직후 첫 표본의 timeout 2건과 이후 정상 3건은 별도 보존한다. 격리 16만 행 수치와 이 짧은 운영 표본을 모든 selector의 p95·장기 I/O/메모리 안정성 보장으로 확대하지 않는다.
