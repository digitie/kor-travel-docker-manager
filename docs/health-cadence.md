# 배포 상태 점검 간격 정합

사용자가 승인한 상태 점검 간격을 Manager Compose 정본에 반영한다. 이미 운영에서90초인 Weather를 다시3배로 늘리지 않고90초로 맞춘다. 데이터 수집 주기와 비즈니스 튜닝은 이 변경의 대상이 아니다.

| 기존 Manager 서비스 키 | 기존 정본 | 목표 |
| --- | --- | --- |
| kor-travel-geo-api | 10s | 30s |
| kor-travel-geo-dagster-code-server | 30s | 90s |
| kor-travel-geo-ui | 10s | 30s |
| kor-travel-weather-api | 10s | 90s |
| kor-travel-weather-dagster-code-server | 30s | 90s |
| kor-travel-dagster-webserver | 30s | 90s |
| kor-travel-shared-postgres | 10s | 30s |
| rustfs | 10s | 30s |

서비스 선언의 변경은 위 기존8개 healthcheck.interval뿐이다. 재구축 outer wait는 아래와 같이 해당 선언에서 파생한다. 검사 Test, timeout/retries/start_period/start_interval, Env, 이미지, 포트, PG 스키마와 자식 worker/orphan 보호를 바꾸지 않는다. Weather Web에는 healthcheck를 새로 만들지 않는다. Transport는 외부 프로젝트로 Manager에 새 서비스를 추가하지 않으며 소유 저장소 PR83의30→90초/legacy DB5→15초 정책과 별도로 연결한다.

Geo580의 운영 override CS 이름과 Manager 서비스 키가 다를 수 있다. 이 저장소는 여기 이미 있는 kor-travel-geo-dagster-code-server 키만 고친다. 배포자는 각 배포 정본의 실제 서비스 이름으로 완성된 Compose와 실행 중인 Config.Healthcheck.Interval을 대조한다.

실행 간격이 늘면 지속 장애 발견도 늦어질 수 있다. 이 변경이 timeout을 늘리거나 readiness를 축소하는 것은 아니다. stdlib bytecode와 Common HTTP 점검은 이미 별도 소스에 있으므로 새 helper를 만들지 않는다.

## 검증과 적용 상태

신규9회귀는 기존 source에서8FAIL/1PASS였고, 수정 후 기존 상태 점검·공용 PG·서비스 참조와 함께134PASS였다. 전체 Compose 구조 대조에서8개 interval 외 모든 필드가 같고 새 서비스는0개다. 전체 Ruff와 frontend35/type-check/lint/build를 통과했다. 실제 Docker/SSH/PG 호출은0이다. 최종 CI·독립 리뷰와 운영 적용/효과는 별도로 확인한다.

소스 머지나 Compose 파일 저장만으로 실행 중인 컨테이너에 적용됐다고 기록하지 않는다. 운영자는 별도 전개 절차로 필요한 대상만 적용하고 API/UI/DB 상태를 보존한다. 동일 구성의 전후 CPU 표본과 실제 점검 횟수·duration·오류 수를 비교하기 전에는 CPU 개선을 확정하지 않는다.

## 재구축 기동 예산

간격만 조정한 첫 CI는2986PASS/38SKIP/5FAIL이었다. 세 시험은 plane의 파생 상한을465초로 고정한 기대값이 낡았고, 두 시험은 CS 기동 창600 + (90 + 10) × 5 = 1100초가 기존900초 compose wait보다 길다는 실제 계약 충돌을 잡았다. 이 실패를 보존하고 guard나 HC 값을 낮추지 않았다.

compose_service._compose_wait_timeout_seconds는 frozen transaction의 서비스 healthcheck에서 start_period + (interval + timeout) × retries + 기존15초 여유를 계산한다. 기존900초는 하한으로 유지하고 기존 허용 최대3600초를 넘거나 잘못된 선언은 runtime stop 전에 거절한다. 무한대기나 임의 큰 상수로 대체하지 않는다. nanosecond render와 duration 문자열, Docker0 기본값을 처리한다.

현재 정본의 최대는 Weather API1140 + 15 = 1155초이고 CS는1100 + 15 = 1115초다. 두 rebuild up 호출은 live checkout 대신 같은 frozen render의 파생값을 전달한다. shared plane의 기존900초 clamp는 유지하고 현재 webserver 파생635초를 그대로 사용한다. HC Test/timeout/retries/start_period는 여전히 그대로다.

보강 뒤 관련 재구축·plane249회귀와 전체 Ruff가 통과했다. 실제 기동 시간이나 CPU 개선을 실행한 결과는 아니다.

## 공용 daemon 후속

PR511의 공용 daemon interval360/start_interval45와 빌드 bytecode 준비는
[별도 후속 범위](daemon-health-cadence.md)로 정산한다. 위 기존8개 interval 정책은 유지한다.
