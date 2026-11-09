# 컨테이너 상태 조회 공유와 일시 캐시

## 계약과 범위

REST 목록과 WebSocket 최초 화면·주기 방송은 같은 DockerService의 조회를 공유한다.
동시 요청은 하나의 동기 Future를 기다리고, 정상 응답만 완료 시점부터 monotonic
기준 2초 동안 재사용한다. 반환값은 매번 깊은 복사하므로 수집자나 화면의 수정이
다른 응답에 전파되지 않는다. 완료 시점의 private 복사본 하나를 cache와 Future가 함께
보관하고 호출자마다 다시 복사한다. 오류/불완전 응답도 같은 flight에서는 그 완료본을
공유하되 다음 요청용 cache에는 넣지 않는다. 정상 완료의 전체 복사는 기존처럼 private
복사와 반환 복사 두 번이다. 이 캐시는 권한·변경 대상·배포 성공 판정에 쓰지 않는다.

예외 및 error/offline/unreachable/down 행, 명시 inventory_complete=False 행은
캐시하지 않는다. 로컬 exited/created/not_created 상태는 재사용할 수 있다.
새 main의 미등록 컨테이너 목록 조회가 실패하면 inventory_complete=False를
보존하고 다음 요청에서 수집을 다시 시도한다. 모든 원격 관측이 실패하면 정상
캐시 적중률이 0일 수 있으며 동시 조회 공유만 남는다.

Docker start/stop/restart·설정 수정/초기화와 Compose ensure·pinned rebuild·공용
Dagster converge는 성공/실패에 관계없이 전후 process epoch를 갱신한다. 변경 중에는
응답을 공유하지만 캐시하지 않는다. 조회 중 epoch가 바뀌면 한 번 다시 관측하고,
두 번째도 무효화되면 오래된 값을 반환하지 않고 일시 불가로 실패한다. REST 목록은
내부 원문을 노출하지 않는 일반적인 일시 불가 고정 detail의 503, WS 최초 관측은 기존 재시도 코드 1013으로
닫는다. 방송 실패는 기존 방송 주기의 다음 tick에서 재시도한다. 다른 기존 오류 분류는 유지한다.

## 실패와 자원 경계

외부 Docker 명령·다른 프로세스의 변경은 이 프로세스의 epoch에 반영되지 않으므로
정상 응답의 캐시 재사용 창 2초를 허용한다. Docker 수집 자체의 경과 시간까지 2초라는
뜻은 아니다. 프로세스 간 공유 캐시나 새 watchdog은 추가하지 않는다.

asyncio 대기 취소는 실행 중인 동기 수집 Future에 전달되지 않는다. 수집 thread의
실행과 기존 Docker/HTTP 제한은 유지하며, 느리거나 멈춘 호출 자체의 새 종료 보장은
제공하지 않는다. get 진입 시 한 번 잡은 monotonic 5초 deadline으로 follower의
공유 Future 대기만 제한하고, 두 관측에 걸친 JOIN 대기는 같은 잔여 시간을 쓴다.
leader의 동기 collect와 복사·반환까지 전체5초라는 보장은 아니다. timeout 뒤 Future가
완료됐다면 결과를 다시 읽어 실제 collector TimeoutError와 완료 경합을 원래대로
전달한다. 아직 pending일 때만 고유 일시 불가로 반환하고 pending flight는 유지한다.
새 thread·대체 수집·flight 초기화는 하지 않는다. 늦은 leader 완료는 정상 회복 경로다.

각 instance는 cache 하나와 진행 Future 하나를 보유한다. 목록 크기와 호출자 수에
따라 깊은 복사의 CPU·순간 메모리 비용은 늘 수 있다. cache hit 복사는 lock 안에서
수행된다. 운영 RSS 상한·캐시 적중률·CPU 감소·지연 개선은 아직 실측하지 않았다.

## 원본 이력과 검증

원본 PR533 c038a1d의 제품 세 파일을 main557 0889d3b에 옮겼다. 최신 Docker inventory,
image reference·공개 URL과 compose 보안 경계는 보존했다. 제품 두 기존 파일에서
추가 wrapper/decorator만 역제거하면 main 전체 bytes와 같다. 새 cache 모듈에서
불완전 inventory 제외·재관측 상한·고유 일시 불가 예외·Future private 복사를 역제거하면 원본533 전체 bytes와 같다.

원본533의 Dagster 전역/지역 queue fixture 정정은 이 기능과 별개라 이번 범위에서
제외한다. Weather·Geo healthcheck PR486/488도 포함하지 않는다. 원본 draft는 보존한다.

불완전 inventory 재수집과 연속 변경 중 유한 실패 두 반례는 기존 코드에서 2 FAIL로
재현했다. 수정 후 복사 격리·예외 공유·실패한 변경 무효화·실행 중 대기 취소와 실제
REST/WS 함수 공유를 포함한 집중 20개가 통과했다. 정본 Ruff 0.16.4 검사도 통과했다.
구0.3.7 lint의 기존 184개 오류는 버전 차이의 원결과로 보존했다. 추가 REST503/WS1013·실제 inventory 수집 경계까지 집중33 PASS를 확인했다.
전체 회귀·새 exact CI·독립 두 리뷰·실제 UI 및 운영 효과 검증은 후속이다.

독립 리뷰에서 cache만 복사하고 Future에는 수집자의 원본 객체를 보관한 반례가 확인됐다.
같은 flight의 늦은 대기자가 수집자 후속 변경을 읽는 정상/오류/불완전 3가지 반례를
원3 FAIL로 보존했다. 완료 private 복사본을 Future와 cache가 함께 쓰도록 수정한 뒤
이 3가지와 정상 복사 횟수를 포함한 집중37 PASS를 확인했다. 그 외 수집·실패·취소·
epoch·권한 경계는 유지한다.

6864657의 로컬 전체 검사는 이 반례 수정 전에 정확 pytest PID만 중단하여
1059 PASS/30 SKIP/중단으로 보존했다. 이를 전체 PASS로 수용하지 않는다. 수정 후
새 exact CI·추가 전체 회귀와 독립 리뷰·실제 UI 수용은 여전히 후속이다.

공유 대기의 장애 증폭을 줄이기 위해 follower JOIN을 위의 고정5초로 제한했다. 원3a의
무제한 공유 대기/완료 경합/두 관측 합산 budget 누락 3 FAIL을 보존했다. 실제 보류된
Future·반복 follower·완료 뒤 회복·취소된 asyncio 호출의 worker 반환·수집자의 실제
TimeoutError 원객체와 완료 경합을 검증한다. 가짜 monotonic clock은 단위검사의 대기
구간을 결정적으로 진행시키며 운영 시간이나 leader 종료 시간을 실측한 근거가 아니다.
3a75123 exact CI의 backend3384 PASS/40 SKIP와 frontend 성공은 그 이전 소스의
검증으로 보존한다. 새 JOIN 경계의 최종 CI와 후보 UI는 별도다.

새 JOIN 경계는 snapshot/inventory/remote/WS 원검증을 합친 집중89 PASS와 정본 Ruff로 확인했다. 운영 장애나 leader 전체 종료 시간의 실측은 아니다.


## 2026-11-09 — 현재 파일을 그대로 읽는 안전한 파서 선택

운영 UI의 컨테이너 목록에서 503을 재현했고, 실제 로그는 위 5초 follower JOIN 제한에
도달했음을 보였다. 별도 읽기 전용 프로세스에서 현재 수집을 한 번 측정한 결과는
전체 4.1061초, Compose 파싱 1.3280초, Docker 실행 목록 0.0737초,
원격 HTTP 관측 0.6813초였다. 남은 약 2.02초는 로컬 행 처리와 개별 Docker 조회 등이며
각각의 비용을 따로 재지는 않았다. 이 프로세스의 메트릭 캐시는 비어 있고 운영
backend의 캐시·경합·부하를 통제한 전후 비교가 아니다.

같은 실제 Compose 원문 233,819바이트를 읽어 두 안전한 파서로 각각 한 번 파싱했다.
Python `SafeLoader` 1.4345초와 `CSafeLoader` 0.1318초였고 결과 모델은 같았다.
따라서 `docker_service.get_compose_config`에서 설치된 안전한 C 파서를 우선 쓰고,
libyaml이 없는 설치에서는 기존 `SafeLoader`를 쓴다. 다른 YAML 소비자는 바꾸지 않는다.
파싱 오류가 나면 다른 파서로 재시도하지 않고 기존 오류 로그와 빈 결과를 유지한다.

파일은 호출마다 새로 연다. TTL·mtime·내용 캐시를 추가하지 않아 같은 mtime·크기로
교체된 바이트도 다음 호출에 반영한다. Python 객체 태그를 허용하지 않으며 alias·merge·
한글·보간 전 환경 문자열·숫자·bool의 의미도 기존 안전한 파서와 비교한다.
조회 공유·5초 제한·HTTP 예산·원격 동시성·음성 상태의 캐시 제외·freshness는 그대로다.
이 단일 파싱 표본을 수집 전체의 절감률이나 운영 UI 503 해결 완료로 해석하지 않는다.
최신 후보 CI·독립 두 리뷰와 실제 설치 후 수집·UI 검증은 별도 수용한다.


## 2026-11-09 — 첫 조회의 중복 키 검사 파싱 경로

PR #584 설치 뒤 별도 수집 표본의 전체 4.1061→3.2948초·Compose 파싱
1.3280→0.1227초를 확인했지만, 새 backend의 첫 공개 UI는 여전히 컨테이너 목록
503 두 건으로 실패했다. n150 기능 8개 통과와 Odroid 기능 8개 완료를 전체 UI
성공으로 바꾸지 않는다. quota·상위 cgroup throttling은 없었고, 실제 daemon 문맥을
복사한 별도 읽기와 외부 메모리 읽기 스택에서 평상시 조회가 빨라진 것도 확인했다.

남은 첫 조회 비용은 UI의 잠금 필드 이름을 처음 파생할 때 reference Compose를
엄격한 Python YAML 파서로 다시 읽는 경로였다. 단계별 CPU 프로파일에서
`_locked_env_present` 아래 YAML 파싱이 큰 비용을 차지했다. 프로파일 자체의
오버헤드가 있어 그 시간을 일반 응답 지연이나 운영 CPU 비율로 해석하지 않는다.

실제 같은 reference 원문 233,819바이트에 기존 Python 중복 키 파서와 동일한
생성자를 등록한 안전한 C 파서를 각각 한 번 적용했다. 1.4154초와 0.1223초,
모델 동등 및 중복·merge 충돌·해시 불가능 키·Python 객체·문법 오류 거부를 확인했다.
공개 `UniqueKeySafeLoader`의 Python 기반은 유지하고, 같은 중복 키 생성자를
등록한 private 안전 파서를 C 우선·없으면 Python으로 둔다. wrapper는 문자열에
literal TAB이 없을 때만 이 파서를 고른다. C 파서가 `a: \tbad`를 받아들이는 차이를
회귀에서 발견했으므로 TAB이나 비문자열 입력은 **파싱 전에** 기존 Python 경로로
보낸다. 정상적인 따옴표·block 내부 TAB도 일괄 거절하지 않고 원래 의미를 보존한다.
실제 reference에는 literal TAB이 0개라 빠른 경로를 사용한다. 모든 가능한 문법의
완전한 동일성을 주장하지 않는다. 기존 중복 검사 생성자·merge 펼침·해시 검사와
`finally`의 dispose는 그대로이며 파싱 오류를 다른 로더로 재시도하지 않는다.
새 캐시·기동 준비 단계·lookup 계약·시간 제한 변경은 추가하지 않는다.

이 변경은 해당 엄격한 로더를 공유하는 registry·topology·Compose 소비자에도 적용된다.
기존 보안 회귀와 새 Python/C 동등성·의미상 같은 bool/숫자 키·객체 태그 거부·
자원 해제·격리된 C 부재 fallback 검증을 유지한다. 최신 두 리뷰·CI 뒤 공식 설치의
첫 UI를 검증하며, 이미 따뜻해진 조회의 우연한 재실행으로 완료 처리하지 않는다.


### 엄격한 C 파서의 Unicode 호환 경계

고립 surrogate escape(예: `"\uD800"`)처럼 잘못된 Unicode를 기존 Python 파서는
받아들이고 C 파서는 더 엄격하게 거절할 수 있다. 이는 알려진 P3 호환 경계이며,
이 경우 오류를 기존 파서로 재시도해 통과시키지 않는다. 실제 유효 설정의 모델과
중복·merge 충돌·의미상 같은 bool/숫자 키·해시 불가능 키·객체 태그 거부는
독립 회귀·경계 표본으로 확인했다. 모든 YAML 문법의 완전한 동등성은 보장하지 않는다.

#584의 첫 UI 503은 보존한다. #585는 두 독립 리뷰·정확 CI 뒤 양 호스트에 설치했고,
새 backend의 첫 공개 UI를 목록 사전 조회·해당 smoke 재시도 없이 각각 8개씩 통과했다.
이 결과는 같은 부하 CPU/RSS·장기 경합 수용이나 n150 전체 Docker 불변성을 뜻하지 않는다.
최종 n150 래퍼는 Docker 후속 검사 실패를 남겼고 정확한 변화는 UNKNOWN이다.
설치별 실제 증거·첫 UI·단일 실측·남은 범위는 [운영 정산](reports/2026-11-09-manager-healthcheck-rollout.md)을 따른다.
