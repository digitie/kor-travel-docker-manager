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
내부 원문을 노출하지 않는 고정 detail의 503, WS 최초 관측은 기존 재시도 코드 1013으로
닫는다. 방송 실패는 기존 방송 주기의 다음 tick에서 재시도한다. 다른 기존 오류 분류는 유지한다.

## 실패와 자원 경계

외부 Docker 명령·다른 프로세스의 변경은 이 프로세스의 epoch에 반영되지 않으므로
정상 응답의 캐시 재사용 창 2초를 허용한다. Docker 수집 자체의 경과 시간까지 2초라는
뜻은 아니다. 프로세스 간 공유 캐시나 새 watchdog은 추가하지 않는다.

asyncio 대기 취소는 실행 중인 동기 수집 Future에 전달되지 않는다. 수집 thread의
실행과 기존 Docker/HTTP 제한은 유지하며, 느리거나 멈춘 호출 자체의 새 종료 보장은
제공하지 않는다. 재관측 횟수만 제한한다.

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
