# 공용 Dagster 실행 한도와 큐 확인 주기

## 목적과 범위

공용 instance의 `concurrency.runs.max_concurrent_runs`를 12에서 2로,
공식 `run_queue.dequeue_interval_seconds`를 기본 5초에서 명시적 30초로 바꾼다.
새 dispatch의 동시 부하와 큐 확인 빈도를 줄이는 설정이다. 이미 실행 중인 run을
취소하거나 중지하지 않고, scheduler·sensor의 평가나 QUEUED 생성도 막지 않는다.

Geo 등 각 location·run group의 tag 상한, pool 제한, 업무 cron, monitor·heartbeat·
healthcheck와 이미지·CLI는 보존한다. 개별 tag 상한이 전역 2보다 높아도 유효하다.
실제 dispatch는 전역 한도와 해당 tag 제한을 함께 만족해야 한다.

## 구현·검증 계획

공용 `config/dagster-shared/dagster.yaml`의 두 scalar만 바꾸고, 원본 바이트의
SHA256 앞 16자를 기존 Compose instance digest anchor 한 곳에 갱신한다.
새 렌더러는 만들지 않으며 기존 `test_workspace_digest_validate.py`의 파일 해시
검증과 모든 소비자 같은 digest 검증을 그대로 사용한다.

회귀는 두 값, 개별 상한의 양의 정수 유효성, 전역보다 높은 tag 상한 조합,
그 밖의 instance 설정과 Compose 모델 보존을 확인한다. 테스트 전용 통합
Docker·DB fixture와 운영 접근은 이 source 작업에서 실행하지 않는다.

## 적용과 한계

현재 운영 instance YAML과 이 저장소 YAML이 전체 동일하다고 가정하지 않는다.
운영 적용자는 실제 원본의 두 scalar만 변경하고 그 결과의 digest를 별도로
계산해야 한다. 이후 같은 구성·부하의 CPU·대기 지연을 측정해야 하며, 설정
구현만으로 CPU 개선이나 backlog 해소를 주장하지 않는다. 대기 중인 run의
시작 확인은 최대 한 polling 간격만큼 늦어질 수 있고, 전역 슬롯이 부족하면
더 오래 대기한다.

이 PR은 Common 소비자 PR513과 별도다. 초안 시점에는 아직 구현·회귀·운영
측정 전이다.
