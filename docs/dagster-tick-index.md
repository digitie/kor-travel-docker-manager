# 공용 Dagster 최신 tick 조회 인덱스

Dagster 최신 tick SQL은 `selector_id = … OR (selector_id IS NULL AND job_origin_id = …)` 뒤 timestamp 내림차순·소수 limit를 사용한다. 두 조건의 BitmapOr 결과 전체를 정렬하면 history 증가와 다른 DB 쓰기의 I/O 경합 중 센서 평가와 UI 조회가 오래 기다릴 수 있다.

`docker/dagster-host/storage-migrate.py`는 PostgreSQL storage를 bootstrap한 뒤 `public.job_ticks("timestamp" DESC)`의 `ktdm_job_ticks_latest_timestamp` 인덱스를 준비한다. 이후 기존 Dagster migration을 exec한다. 데이터·기존 인덱스·조회 상태와 개수는 보존한다.

인덱스는 autocommit 연결에서 CONCURRENTLY로 만들며 lock 대기는 5초, 작업은 120초로 제한한다. 같은 이름·같은 정의가 valid/ready면 재진입은 무연산이다. 같은 정의의 invalid/unfinished index만 drop concurrently 후 재생성한다. 다른 정의의 이름 충돌은 수동 확인이 필요한 오류다. 실패를 완료로 삼지 않는다.

운영 적용은 Manager의 storage 준비 단계가 소유하며 기존 배포 절차·호스트 lock을 따른다. 접속 URL은 선언된 PostgreSQL storage 설정에서 읽고 출력하지 않는다. 독립 운영 SQL을 각 consumer 저장소에 복제하지 않는다.

검증은 valid/ready 확인으로 끝내지 않는다. 같은 최신 ID/상태·조회 limit와 repository 범위를 보존하고 실행 계획·응답 시간을 전후 비교한다. GraphQL 원문·DB 접속값·운영 hostname은 커밋하지 않는다. 초기 timeout과 하니스 실패를 기록한다.

현재 운영 효과·live/UI 및 최종 리뷰는 진행 중이다. 격리 16만 행의 실행 계획 개선을 운영 p95나 장기 안정성으로 확대하지 않는다.
