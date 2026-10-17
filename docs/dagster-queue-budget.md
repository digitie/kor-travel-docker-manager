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

이 PR514는 Common 소비자 PR513과 별도다. source 구현과 관련 단위 회귀는
완료했으며 운영 측정은 하지 않았다.

## 실제 source 검증

- 새 정책 테스트는 변경 전 `12 != 2`로 실패했고, 변경 뒤 관련 4개 파일
  130개 테스트가 44.71초에 통과했다. Ruff와 diff 공백 검사도 통과했다.
- 별도 전체 모델 대조에서 instance는 두 scalar 이외 동일하고 모든 tag
  상한이 동일하다. Compose는 기존 raw 파일 SHA256 앞 16자 anchor를
  `344105d5c06a22d3`로 변경한 효과만 있으며, 7개 소비자가 같은 값을 받는다.
- `max_concurrent_runs`보다 큰 tag 상한은 오류가 아니다. 전역 상한과
  독립적인 양의 정수인지 검사하고, 실제로 더 큰 tag 상한이 남아 있는지
  회귀로 확인했다.
- 전체 backend suite·frontend·실제 SDK/DB/Docker·운영 실행은 이 작업에서
  재실행하지 않았다. frontend tree는 기준 main과 같고 CI 결과는 별도로
  확인한다. 운영 CPU·backlog·공정성 효과는 아직 미검증이다.

## 기존 instance snapshot 회귀 정산

최초 최종6f419f7 CI는 1FAIL/3049PASS/39SKIP이었다. 유일 실패는 NH-4의
`test_instance_bytes_unchanged`가 이전 정책 원문 SHA7925…를 고정했던 것이다.
이번 두 scalar 변경 뒤 원문 SHA3441…로 snapshot을 갱신하고 검사 이름·설명을
현재 범위에 맞췄다. 원문 전체 SHA 검증은 그대로 유지한다. 제품 config·Compose는
6f419f7과 byte 동일하다. 관련 host/workspace/digest/config 59개 테스트와 Ruff가
통과했으며 이전 CI 실패는 보존한다. 새 전체 CI 결과는 별도 판정한다.


## PR513 최신 feature 통합

최종513 f72e49를 ordinary merge해 Common92d 정식 wholewheel과 content tag73c를
동시에 보존한다. PR514의 정책 두 scalar와 raw source SHA/digest7소비자는 기존24e3과 같다.
cAdvisor는 최신 main #517의 기본15초/env 덮어쓰기·14제외·단일flags다.
base는 main이며 PR513이 먼저 merge되어야 한다. 이 문서의 과거 '별도'는 정책 변경의
책임범위가 별도라는 뜻이다. source branch는 이제513 제품을 포함하며 먼저 merge하지 않는다.
실제 운영 원본463c 또는 Geo override를 source 정본으로 덮는 작업은 하지 않았다.
