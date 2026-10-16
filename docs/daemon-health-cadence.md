# 공용 Dagster daemon 점검 간격 조정

## 범위

공용 kor-travel-dagster-daemon의 healthcheck interval120→360초, start_interval15→45초만
조정한다. Test의 파일 digest·workspace gRPC·정식 liveness-check, timeout60/retries2/
start_period90, heartbeat tolerance300, 업무 schedule·sensor·queue 정책은 유지한다.
다른 legacy daemon이나 code-server의 점검 간격은 이 변경에 포함하지 않는다.

## 기동 대기와 관측 지연

render 기동 예산은 start_period90+(interval360+timeout60)×retries2+여유15=945초다.
기존 shared-plane wait의900초 상한으로 자르면 정상 기동보다 먼저 실패할 수 있으므로
기존 공용 허용 상한3600초를 재사용한다. readiness 판정·stop/cleanup 전 fail-closed는 유지한다.
일반 Compose outer wait는 이미 frozen health에서 파생하며 기존 최대1155초보다 작다.

Docker probe 간격은 Dagster 내부 heartbeat 생성 주기가 아니다. tolerance300을360초보다
크게 올릴 필요는 없다. 정상 daemon은 두 probe 사이에도 heartbeat를 갱신한다.
정지한 daemon의 상태를 Docker가 표시하는 시점은 다음 probe·retries·timeout만큼 늦어질 수
있으며, 간격 연장은 장애 감지 지연과 점검 비용의 교환이다. unhealthy만으로 Docker가
자동 재시작하는 것은 아니다. start_interval 연장도 최초 healthy 확인이 늦어질 수 있다.

## Bytecode 조사 경계

dagster-host Dockerfile과 잠금 의존성의 공개 소스를 조사한다. 운영 이미지 cache 부재와
실제 CPU 개선은 아직 확인하지 않았다. Docker 빌드/운영·SDK·DB 접근 없이 source 필요성과
기존 Common helper 또는 표준 라이브러리 compileall 재사용 가능성만 먼저 보고한다.

현재는 설계·초안 PR 단계이며 구현 회귀·독립2리뷰·운영 전개 및 측정은 별도다.
