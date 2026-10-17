# 메트릭 관측 기본값과 이미지 조회 비용 절감

## 범위와 불변식

관측 한 건의 기본값을 한 번 계산하여 이전 관측이 없는 경우의 기본값과 새 관측의
템플릿으로 재사용한다. 기존 코드는 dict.get의 기본 인자를 미리 평가한 뒤 같은
기본값을 다시 계산했다. 이 과정의 MANAGED_CONTAINERS 접근은 전체 파생 mapping과
호스트 검증을 반복할 수 있다.

수집 cycle은 기존 MANAGED_CONTAINERS.items()가 만든 spec snapshot을 정상·정지·
통계 실패·조회 실패·미생성·offline 관측에 전달한다. 외부 helper 호출이 spec을
주지 않으면 기존처럼 그 호출 시점의 registry를 읽는다. 명시한 빈 spec도 유효하다.
cycle 밖에 설정을 저장하지 않으므로 다음 cycle의 registry 변경이 반영된다.
생성자의 기존 기본값 초기화는 변경하지 않았다.

inspect의 Config.Image와 Image ID가 모두 있으면 반환값이 이미 결정되므로
container.image 추가 조회를 생략한다. 하나라도 없으면 tags/short_id 조회와 예외
fallback을 그대로 실행한다. 실제 Docker 상태나 stats를 캐시하지 않는다.

수집 주기·통계 계산·저장·API·Prometheus DTO는 바꾸지 않는다. 이전 오류 횟수,
정지 컨테이너의 빈 메트릭, 설정 오류의 기존 전파와 실패 격리를 유지한다.
snapshot 재사용으로 cycle 도중 변경된 설정은 다음 cycle부터 반영된다.

## 로컬 회귀

실제 함수·클래스 AST를 분리하여 앱 초기화·Docker SDK·DB를 대역으로 바꾸고 실행했다.
별도의 실제 registry _LazyMapping 클래스 AST도 사용해 16행 cycle의 loader가
한 번만 호출됨을 확인했다. 제품 구현을 테스트용 사본으로 다시 작성하지 않았다.

- 관측 중복: 원본 5FAIL/6PASS → 11PASS.
- inspect 이미지 조회: 기존 1FAIL/17PASS → 18PASS.
- cycle snapshot: 기존 19FAIL/19PASS → 38PASS; 실제 LazyMapping 추가 후 최종 39PASS.
- 정상·정지·stats 실패·get 실패·NotFound·offline의 이전/이후 관측·최신 메트릭·
  오류 카운터·daemon 상태가 고정 시각에서 모두 같았다.
- backend 전체 Ruff 통과. 전체 pytest·frontend type/build·운영 비교는 여기서
  수행하지 않았다. 독립 리뷰와 CI도 별도 gate다.

최초 회귀 대역 작성 과정의 잘못된 테스트 메서드명과 offline 오류 횟수 기대값은
제품을 바꾸지 않고 실제 기존 계약에 맞게 정정했다. 최초 실패 기록은 외부 근거에
보존했으며 위 RED 수치는 정정된 동일 대역의 제품 전후 결과다.

## 합성 비교

실행 명령은 아래와 같다. pytest가 설치된 로컬 interpreter만 필요하며 운영 설정과
Docker·DB·SDK에 접속하지 않는다.

    python backend/tests/benchmark_metrics_observation.py

동일 source AST에 합성 전체 mapping copy와 fake Docker stats/save를 연결했다.
16/64행 × 100cycle을 5쌍 교차 순서로 측정했으며 생성자 비용은 제외했다.
모든 쌍의 최종 관측 출력은 같았다. 아래는 process CPU 중앙값이다.

| 행 수 | 상태 | 이전 CPU 초 | 이후 CPU 초 | 이전→이후 registry resolve |
| --- | --- | ---: | ---: | ---: |
| 16 | 정지 | 0.018544 | 0.010371 | 3300→100 |
| 16 | 실행 | 0.634123 | 0.622069 | 3300→100 |
| 64 | 정지 | 0.119434 | 0.038725 | 12900→100 |
| 64 | 실행 | 2.718004 | 2.526916 | 12900→100 |

64행에서 spec 방문은 825600→6400, image 속성 조회는 6400→0이었다.
별도 tracemalloc 100cycle pass의 peak는 정지16행 62382→59524B,
실행16행 89248→93136B, 정지64행 175727→162549B,
실행64행 224613→217817B였다. 실행16행은 증가했으며 RSS 또는 메모리의
일괄 개선으로 해석하지 않는다.

이 mapping은 실제 호스트 검증 비용을 재현하지 않는다. running 경로는 to_thread와
event loop 비용도 포함한다. 운영 backend CPU 표본과 UI 지연은 여러 작업의 합이므로
이 수치를 운영 CPU 개선율이나 전체 문제 해결로 확대하지 않는다. 실서비스 적용,
설정 규모와 동일 작업량 CPU·RSS·수집 소요 시간 비교는 root의 후속 검증이다.
