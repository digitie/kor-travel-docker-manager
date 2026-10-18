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

## 빌드 시 Bytecode 준비

Common37 merged c48595880aaad4367802ad71af2525a8a5e17c6a의 dependency-free
build_bytecode.py를 docker/dagster-host/build-bytecode.py로 원문 복사했다.
SPDX와 SHA256 b07c7f94565147f619bffba7f1f9871106c0ee07d3683974393d596ffda5d266은
회귀시험으로 대조한다. runtime Common 설치나 requirements 변경은 추가하지 않는다.

기존 해시 고정 pip 설치에 --compile을 명시하고 pip check 뒤 python -I -B로
helper --stdlib을 한 번 실행한다. pip가 installed package cache를 준비하고,
helper는 site-packages/dist-packages와 test 계열을 제외한 현재 interpreter stdlib의
O0 checked-hash cache만 workers1로 준비한다. 실행 중 import나 시작 명령은 바꾸지 않는다.
PYTHONDONTWRITEBYTECODE=1은 보존하며 준비된 cache 읽기를 금지하지 않는다.
실패는 이미지 build 실패로 전파한다. 외부 cache prefix·목적지 링크·읽기 실패는 거절한다.

build context는 Dockerfile/build-bytecode.py/requirements.txt/storage-migrate.py 네 파일이다.
이 네 파일의 내용으로 계산한 image tag는 storage migrate·global webserver·daemon에
동일하게 적용한다. 이는 같은 공용 이미지 계약이며 각 서비스의 Env/User/Cmd/health Test/
리소스를 별도로 바꾸는 작업이 아니다.

운영의 선택한 stdlib direct source 표본에서 cache 부재가 root에 의해 관측되었지만,
전체 cache inventory·이 패치의 native build·CPU 개선은 이 source PR의 로컬 결과로
대신 증명하지 않는다. 작은 격리 소스의 실제 compile 결과에서 flags3/source hash,
외부 source/cache 링크, 문법 오류 nonzero와 native 파일 보존을 검증한다.
실제 stdlib 컴파일/운영 build·적용·같은 workload 비교는 root의 별도 gate다.

## 검증 상태와 제한

제품 source는 5fd205ea7b420be1bd2d7ae94af6771d89227ee0로 고정했다.
기존 HC 회귀 두 건은 정책에 맞춰 정산했다. legacy daemon의 짧은 감지 상한은 유지하고
공용 daemon만 새720초(360×2) detection 비용을 명시한다. 기존 rebuild fingerprint는
실제 함수 비교에서 first_deploy4회/converge1회의 대기 인자900→945만 달라 해당5개만
갱신했다. 호출 순서·나머지 인자는 동일하다.

초기 새2시험은 원 source에서 모두 실패했고, HC/bytecode focused387개가 통과했다.
RUN 줄 연결 누락은 검토 중 수정한 뒤 Docker instruction/실제 sh -n 검사를 추가했다.
최종 bytecode·content tag25개, Ruff, frontend type/lint/35tests/build가 통과했다.
backend 필수 전체 검사는 로컬 Docker 통합 fixture 진입을 확인해 own pytest를 SIGINT로 중단했다.
전체 PASS가 아니며 원문과 종료1/teardown 오류를 보존했다. 추가 Docker 조회·cleanup은 root가 별도 담당한다.
독립2리뷰·CI·운영 build/배포·성능 실측은 이 문서 작성 시 아직 별도다.

공용 run 상한·queue dequeue·센서/스케줄·pool 정책 변경은 이 PR에 포함하지 않는다.
