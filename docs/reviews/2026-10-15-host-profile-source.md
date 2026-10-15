# 호스트 registry 소스 검증

Geo·Weather·Transport의 배포 설정을 동일한 Manager 계약으로 맞추기 위한 첫 단계다.
registry v2에 호스트를 선언하고 tenant 기본 배치는 유지한다. 이 PR은 운영 배포나 호스트 이전 완료 기록이 아니다.

## 구현과 검증 범위

- default host는 정확히 하나이며 IPv4 주소와 target의 host 참조를 검증한다.
- 설치된 trusted release는 명시적 자기 host와 실제 할당된 IPv4를 요구한다.
  warm registry cache 뒤 환경 경로 변경도 재검증하며 secondary IPv4도 포함한다.
- 직접 실행 `services`만 컨테이너 소유권이다. Map 등의 `containers` 관찰 목록은 Geo 소유권을 넓히지 않는다.
  집계 target의 원격 의존은 로컬 `all` 실행 집합에서 제외한다.
- 연결 문자열은 host-local이면 loopback, 원격이면 registry 주소로 파생한다.
  목록 순회당 한 snapshot만 생성해 반복 할당과 자기 주소 검사를 줄인다.
- 관련 registry·target·외부 프로젝트 계약 116 PASS, backend Ruff PASS.
  전체 backend와 수정 후 두 독립 재리뷰는 검증 중이다.

초기 독립 리뷰 A의 운영 판정 우회·version 혼용과 B의 반복 할당·secondary IPv4 누락을 수정했다.
원문과 고정 소스 해시, 실패·재검증 기록은 비공개 검증 산출물로 보존한다.

## 남은 운영 게이트

R4 변경 명령의 호스트 제한, 호스트별 workspace와 digest, consumer origin 파생, 실제 설치·배포 검증은
후속 단계다. 이 registry 변경을 독립 Dagster plane 전환 완료로 해석하지 않는다.
Geo의 기존 메모리·동시성·타임아웃 등 튜닝값은 변경하지 않았다.
