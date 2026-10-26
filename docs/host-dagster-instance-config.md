# 호스트별 공용 Dagster instance 상한 보존 설계

상태: 초안. 기준 main `6dd65766d083dd6cfd52ffd1f608dcf69502c125`. 제품 구현·운영 적용 전이다.

## 문제와 범위

현재 Odroid 공용 instance 상한은 5, n150 정본은 6이다. 새 Manager의 tracked `config/dagster-shared/dagster.yaml`은 6 하나여서 이후 plane 수렴 또는 새 code-server 기동이 Odroid의 5를 조용히 바꿀 수 있다. 이번 변경은 현재 두 값을 공식 호스트 구성으로 보존한다. 수집 정책·태그별 상한·Geo·PG·메모리·실행 중 run·runtime pin/배포 상태 형식은 바꾸지 않는다. 상한 감축이나 새 admission 정책도 도입하지 않는다.

정상 Manager 설치는 공용 plane을 직접 재생성하지 않는다. 그러나 명시적 `dagster-plane converge`, Map/PinVi pinned 재구축, 새 Weather/Transport code-server 배포가 소비하는 설정까지 같은 정본이어야 한다. 두 consumer에 서로 다른 YAML override를 주는 방식으로 닫지 않는다.

## 선택안

1. 기존 `config/docker-targets.yml`의 `hosts` 각 항목에 `dagster_max_concurrent_runs`를 선언한다. n150은 6, Odroid는 5다. 기존 `KTDM_HOST`와 호스트 identity 검사를 재사용하며 별도 환경변수에 정책값을 다시 선언하지 않는다. 미지의 host, bool/문자열/0/음수 등 잘못된 값은 거부한다. 구형 단일 호스트 개발 fixture만 기존 기본 YAML을 사용한다.
2. 새 작은 `services/shared_dagster_instance.py`에서 tracked 기본 YAML과 선택한 호스트 값으로 effective bytes를 만든다. 같은 값 6이면 원 bytes를 그대로 반환한다. 값 5이면 `concurrency.runs.max_concurrent_runs` 정수 한 자리만 바꾸며 나머지 구조/주석/바이트를 보존한다. 출력 digest는 기존과 같은 `sha256(bytes)[:16]`이다. public Dagster schema가 현 경로에서 `Field(int)`이므로 직접 env 치환은 쓰지 않는다.
3. 기본 6은 원 source 경로와 원 digest를 그대로 사용하여 n150의 불필요한 plane 재생성을 피한다. 기본과 다른 effective 파일만 기존 root 소유 state root 아래에 생성한다. 같은 bytes의 경로가 안정적이도록 digest로 파생하며 별도 manifest/승인 상태를 만들지 않는다. 기존 global mutation lock 아래에서 준비하고 원자 쓰기/파일·디렉터리 fsync를 재사용한다. read/status/환경 해석 단계에서 파일을 암묵적으로 만들지 않는다.
4. 파일은 비밀이 없는 읽기 전용 instance 구성이다. root 소유·group/other 쓰기 없음·symlink/비정규 파일/상위 경로 이탈 거부를 기존 보안 파일 패턴으로 검증한다. Odroid는 이미 NVMe로 확인된 기존 state/release 배치만 사용하며 쓰기 경로를 다른 장치로 임의 fallback하지 않는다. 동시 준비는 같은 global lock과 같은 bytes 재사용으로 직렬화한다. 현재 파일이 틀리거나 불안전하면 덮어 숨기지 않고 실패한다.
5. Manager compose의 공용 instance mount와 instance digest는 이 한 effective 결과에서 함께 유도한다. 기존 frozen environment→system bind snapshot→mutation 전 재검증을 통과시키며 workspace/credential/권한 계약은 유지한다. n150 기본 render는 종전과 같아야 한다. 부적합한 외부 path/digest override로 호스트 정책을 바꾸는 것을 허용하지 않는다.
6. 정상 installer가 새 release를 준비할 때 effective 파일을 기존 lock 아래 명시적으로 준비할 수 있는 작은 CLI 접점을 제공한다. 이후 `ktdctl dagster-instance-config show --json`은 host/max/path/digest 같은 비밀 없는 파생값만 읽는다. 이 파일 준비 자체가 plane 재시작이나 임시 max 변경을 수행하지 않는다.
7. 외부 Transport는 기존 `KOR_TRAVEL_DAGSTER_SHARED_INSTANCE_CONFIG`를 같은 Manager 결과로 채워야 한다. Manager 소유 공식 배포 helper가 위 CLI를 읽고 그 path만 자식 배포에 전달하는 접점을 우선 사용한다. Transport 자체의 기본 옛 tracked-path fallback을 공식 배포에서 다시 허용하지 않도록 정본 배포 script 호출부 한 곳까지 연결한다. 이는 같은 정본을 전달하는 배선이며 독립 설정값이 아니다. 현재 소유 저장소 밖의 Transport 제품 파일을 바꿔야 한다면 별도 작은 PR/소유 조정을 먼저 확정한다.
8. Weather 및 다른 Manager code-server, storage migrate, daemon/webserver 모두 같은 host의 동일 bytes/path/digest를 소비한다. 일부 consumer만 바뀌는 상태를 수용하지 않는다. 새로운 파일이 필요했던 host는 설치 직후 실제 적용 전에 `show`와 dry-run으로 유효 경로/동일 digest를 확인한다.

## ADR-58의 좁은 정정

ADR-58은 instance YAML 한 벌과 host별 workspace만으로 충분하다고 결정했다. 이번 실제 설치 경계에서는 같은 기본 YAML을 유지하면서 host 보호 정수 하나만 파생하는 예외가 필요하다. 두 YAML을 손으로 유지하거나 installer에 전체 compose 렌더 기능을 넣지 않는다. 정책 정본·pure bytes 파생·공식 consumer 접점만 추가하고 기존 기록은 지우지 않는다. 세부 path/파일 권한/installer 호출 위치는 구현 전에 Root와 확정한다.

## 직접 검증 과제

- [ ] n1506 기본 bytes/path/digest 및 resolved consumer 입력이 기존과 동일하다.
- [ ] Odroid5 출력은 정수 한 자리 외 전체 bytes가 같고, 모든 공용 instance consumer가 같은 경로와 digest를 사용한다.
- [ ] invalid host/value, host 위조, mismatched path/digest, 미준비 파일이 fail-closed한다.
- [ ] root 소유/정확 mode/불안전 symlink·hardlink·경로 이탈·다른 장치 경계를 검증한다.
- [ ] 동시 준비·기존 동일 파일 재사용·다른 내용 거부·write/fsync/rename 실패에서 부분 파일을 publish하지 않는다.
- [ ] prepare는 기존 host lock을 사용하고 show/status는 파일 쓰기·서비스 변경이 없다.
- [ ] Transport 배포 helper의 실제 argv/environment에서 동일 effective path가 전달되고 raw env/비밀을 출력하지 않는다.
- [ ] 기존 workspace/태그별 상한/Geo/PG/재구축 권한·frozen bind 회귀가 보존된다.
- [ ] backend 관련 및 전체 CI, 두 독립 적대 리뷰를 완료한다. 운영 적용/live UI는 Root가 Map 배포 뒤 조율한다.

## 현재 미수용

아직 제품 코드·설치·SSH·Docker·PG·빌드·서비스 변경은 없다. 이전 실제 CPU 창들은 서로 다른 시점의 관측이다. 04:05의 active2/BuildKit idle과 04:15의 active5/queued3·BuildKit 재개를 하나의 원인으로 합치지 않는다. 이번 설계는 5를 유지하는 데 필요한 경계이며 CPU 저감 효과나 값을 2로 줄이는 정책을 주장하지 않는다.
