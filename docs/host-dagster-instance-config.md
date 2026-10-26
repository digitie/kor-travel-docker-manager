# 호스트별 공용 Dagster instance 상한 보존 설계

상태: 제품·직접 회귀 구현 후 독립 검토 준비. 기준 main `6dd65766d083dd6cfd52ffd1f608dcf69502c125`. 운영 적용 전이다.

## 문제와 범위

현재 Odroid 공용 instance 상한은 5, n150 정본은 6이다. 새 Manager의 tracked `config/dagster-shared/dagster.yaml`은 6 하나여서 이후 plane 수렴 또는 새 code-server 기동이 Odroid의 5를 조용히 바꿀 수 있다. 이번 변경은 현재 두 값과 Odroid의 기존 태그 한도·로그 수준을 공식 호스트 구성으로 보존한다. 운영 튜닝을 새로 낮추거나 PG·메모리·실행 중 run·runtime pin/배포 상태 형식을 바꾸지 않는다. 새 admission 정책도 도입하지 않는다.

정상 Manager 설치는 공용 plane을 직접 재생성하지 않는다. 그러나 명시적 `dagster-plane converge`, Map/PinVi pinned 재구축, 새 Weather/Transport code-server 배포가 소비하는 설정까지 같은 정본이어야 한다. 두 consumer에 서로 다른 YAML override를 주는 방식으로 닫지 않는다.

## 선택안

1. 기존 `config/docker-targets.yml`의 `hosts` 각 항목에 `dagster_max_concurrent_runs`를 선언한다. n150은 6, Odroid는 5다. 같은 항목의 선택적 `dagster_tag_concurrency_limits`는 정확히 `{key, value, limit}`만 받으며 문자열 identity·양의 정수(bool 제외)·중복 없는 목록이어야 한다. `dagster_python_log_level`은 DEBUG/INFO/WARNING/ERROR/CRITICAL 중 하나다. 기존 `KTDM_HOST`와 호스트 identity 검사를 재사용하며 별도 환경변수에 정책값을 다시 선언하지 않는다. 미지의 host, bool/문자열/0/음수 등 잘못된 값은 거부한다. 구형 단일 호스트 개발 fixture만 기존 기본 YAML을 사용한다.
2. 새 작은 `services/shared_dagster_instance.py`에서 tracked 기본 YAML과 선택한 호스트 값으로 effective bytes를 만든다. 추가 override가 없고 같은 값 6이면 원 bytes를 그대로 반환한다. max만 다른 경우 기존 정수 한 자리 파생을 유지한다. 명시 태그/로그 override가 있으면 기본 목록의 순서를 유지하며 같은 `(key,value)`의 limit만 바꾸고 새로운 identity는 선언 순서대로 뒤에 더한다. value 없는 Map 전체 key 한도와 새 Concierge 기본 상한도 보존한다. 로그 수준 한 필드 외 다른 구조는 바꾸지 않고 비기본 구성만 결정적으로 직렬화한다. 이 경우 기본 주석/포맷을 보존한다고 주장하지 않는다. 공식 조회의 `sha256`은 파일 전체 SHA256 64자리이며, Compose digest는 그 앞 16자리다. public Dagster schema가 현 경로에서 `Field(int)`이므로 직접 env 치환은 쓰지 않는다.
3. 기본 6은 원 source 경로와 원 digest를 그대로 사용하여 n150의 불필요한 plane 재생성을 피한다. 기본과 다른 effective 파일만 기존 root 소유 state root 아래에 생성한다. 같은 bytes의 경로가 안정적이도록 digest로 파생하며 별도 manifest/승인 상태를 만들지 않는다. 기존 global mutation lock 아래에서 준비하고 원자 쓰기/파일·디렉터리 fsync를 재사용한다. read/status/환경 해석 단계에서 파일을 암묵적으로 만들지 않는다.
4. 파일은 비밀이 없는 읽기 전용 instance 구성이다. root 소유·group/other 쓰기 없음·symlink/비정규 파일/상위 경로 이탈 거부를 기존 보안 파일 패턴으로 검증한다. Odroid는 이미 NVMe로 확인된 기존 state/release 배치만 사용하며 쓰기 경로를 다른 장치로 임의 fallback하지 않는다. 동시 준비는 같은 global lock과 같은 bytes 재사용으로 직렬화한다. 현재 파일이 틀리거나 불안전하면 덮어 숨기지 않고 실패한다.
5. Manager compose의 공용 instance mount와 instance digest는 이 한 effective 결과에서 함께 유도한다. 기존 frozen environment→system bind snapshot→mutation 전 재검증을 통과시키며 workspace/credential/권한 계약은 유지한다. n150 기본 render는 종전과 같아야 한다. 부적합한 외부 path/digest override로 호스트 정책을 바꾸는 것을 허용하지 않는다.
6. 정상 installer가 새 release를 준비할 때 effective 파일을 기존 lock 아래 명시적으로 준비할 수 있는 작은 CLI 접점을 제공한다. 이후 `ktdctl dagster-instance-config show --json`은 정확히 `{host, max_concurrent_runs, path, sha256}` 네 필드만 읽는다. 이 파일 준비 자체가 plane 재시작이나 임시 max 변경을 수행하지 않는다.
7. 외부 Transport는 기존 `KOR_TRAVEL_DAGSTER_SHARED_INSTANCE_CONFIG`를 같은 Manager 결과로 채워야 한다. 별도 Transport PR139의 공식 배포 helper가 `sudo -n ktdctl dagster-instance-config show --json`을 읽고 그 path만 자식 배포에 전달한다. Transport 자체의 기본 옛 tracked-path fallback을 공식 배포에서 다시 허용하지 않도록 정본 배포 script 호출부 한 곳까지 연결한다. 이는 같은 정본을 전달하는 배선이며 독립 설정값이 아니다. Manager 설치가 먼저이며 Transport caller 배포가 뒤따른다. caller는 prepare를 자동 호출하지 않고 show 실패·정본과 다른 override를 거부한다.
8. Weather 및 다른 Manager code-server, storage migrate, daemon/webserver 모두 같은 host의 동일 bytes/path/digest를 소비한다. 일부 consumer만 바뀌는 상태를 수용하지 않는다. 새로운 파일이 필요했던 host는 설치 직후 실제 적용 전에 `show`와 dry-run으로 유효 경로/동일 digest를 확인한다.

## ADR-58의 좁은 정정

ADR-58은 instance YAML 한 벌과 host별 workspace만으로 충분하다고 결정했다. 이번 실제 설치 경계에서는 같은 기본 YAML을 유지하면서 호스트의 명시 보호값만 파생하는 예외가 필요하다. 두 YAML을 손으로 유지하거나 installer에 전체 compose 렌더 기능을 넣지 않는다. 정책 정본·pure bytes 파생·공식 consumer 접점만 추가하고 기존 기록은 지우지 않는다. 아래 파일 권한과 installer 순서는 이 좁은 예외의 구현 계약이다.

2026-10-26 후속 RO 대조에서 기존 Odroid는 Geo location1·Weather location3·external_weather2·external_weather_slow1 및 Python WARNING을 쓰고, tracked 기본은 각각10·10·3·항목 없음 및 INFO임을 확인했다. max5만 복사하면 후속 code-server가 이 튜닝을 잃으므로 Odroid registry에 그 네 태그와 WARNING을 명시한다. 나머지 기본 목록은 유지하며 새 `concierge_admin_region_refresh` 안전 한도도 제거하지 않는다. 현재 운영 파일을 매번 읽어 상속하거나 임의 YAML patch를 받지 않는다. normal 설치는 현재 plane을 변경하지 않으며 실제 plane/code-server 적용은 Root가 별도 순서로 수용한다.

## 파일·consumer·동시 실행 경계

- 기본 6은 기존 source 경로와 원 bytes·digest를 보존한다. 운영 `/opt` alias를 외부 caller에서 독립 realpath로 고정하지 않는다.
- 비기본 파일은 기존 `/var/lib/kor-travel-docker-manager`(root 0700)의 `dagster-instance/<전체 SHA256>.yaml`이다. 하위 디렉터리는 root 0755, 파일은 root 0444·정규 파일·hardlink 1개다. 파일과 부모 장치가 같아야 한다. 현재 Odroid의 기존 NVMe state 배치를 사용하며 새 부팅·마운트 시작 조건을 추가하지 않는다.
- prepare는 정상 installer의 기존 G 락 FD9를 재사용한다. 완성·fsync한 임시 파일을 no-replace hardlink로 publish한 뒤 임시 이름을 제거하고 디렉터리를 fsync한다. 기존 파일이 같으면 그대로 사용하며, 다른 bytes·불안전 mode·symlink·hardlink는 덮어쓰지 않는다.
- show, 상태 조회와 읽기 전용 `config`는 prepare·서비스 변경을 하지 않는다. prepare되지 않은 Odroid 파일은 실패한다. 부모 0700이므로 일반 사용자 직접 파일 읽기를 가정하지 않고 기존 `sudo -n ktdctl`을 쓴다.
- Manager의 8개 instance consumer는 동일 effective path를 readonly bind한다. instance digest가 있는 7개 consumer는 같은 16자리 파생값을 쓴다. storage-migrate의 기존 digest 환경변수 유무는 바꾸지 않는다. frozen render와 변경 직전 검증에서도 독립 path/digest override를 거부한다.
- 외부 Transport의 show 재확인과 Docker bind 사이에는 Manager 설치 alias 전환이 끼어들 수 있다. 외부 배포 전체가 Manager G를 소유한다고 주장하지 않는다. Odroid의 내용 주소 파일은 같은 path 덮어쓰기를 거부하지만, 기본 6의 기존 source alias 경합을 전 consumer 원자 전환으로 닫았다고 하지 않는다. 공식 배포는 Manager 설치와 외부 배포를 운영 순서로 직렬화한다.

## 검증과 남은 수용

호스트 구성 직접·관련 순수 60개가 통과했다. 외부 호출 차단 하에서 파일/host/override/consumer/읽기 render와 격리 fixture의 mount 기본값 변환을 확인했으며, Docker·SSH·socket 호출은 0회다. 기존 상한·권한·frozen 회귀의 추가 결과와 최종 CI는 별도 정산한다.

전체 backend 첫 시도는 602 PASS/3 FAIL 뒤 명시적으로 중단했다(560.72초). 이 실행에는 로컬 owned Dagster PostgreSQL fixture와 이미지 빌드가 실제로 있었다. 새 mount `${...:-기본경로}`를 격리 fixture가 tmp Compose 기준 상대 경로로 남겨 storage-migrate가 `dagster.yaml: Is a directory`로 실패했다. 두 fixture의 기본값 해석만 정렬하고 실제 helper의 파일 경로·readonly 변환을 순수 검증했다. 원 실패는 보존하며 실제 통합 재통과로 치환하지 않는다. 정확한 세 project의 컨테이너·네트워크·볼륨 부재를 후속 RO 조회로 확인했다.

추가 관련 순수 선택의 첫 두 실행은 Docker availability/config 호출을 차단해 collection 오류 또는 차단127 실패로 끝났다. 이어진 명시 선택은 기존 own/shared 검사 모델이 새 instance mount/digest 보간을 풀지 않아 6 FAIL/167 PASS에서 멈췄다. 그 모델의 기본값 해석만 정렬했으며 검사기 단언은 유지했다. 이 마지막 정렬의 재검증은 최종 CI에 남긴다. 신규 모듈 mypy는 로컬 `types-PyYAML` stub 부재 1건으로 미수용이다. source Ruff와 Linux 캐시 CodeGraph init/sync·새4symbol 조회는 통과했지만 동적 CLI와 shell 호출의 graph 완전성을 주장하지 않는다.

- [ ] 정렬한 Dagster 통합 fixture의 실제 재실행과 전체 backend CI.
- [ ] 정확한 최종 제품 소스의 독립 두 적대 리뷰.
- [ ] 공식 Manager 설치 후 show/targets validate/수렴 dry-run 및 외부 caller의 실제 동일 파일 소비.
- [ ] Root가 조율하는 양 호스트 역할 보존·공개 UI 검증. 이 작업에서 운영 SSH·설치·서비스 변경은 없다.

이전 실제 CPU 창들은 서로 다른 시점의 관측이다. 더 이전 GraphQL의 active2, 04:05의 BuildKit idle cgroup과 04:15의 active5/queued3·BuildKit 재개를 하나의 원인으로 합치지 않는다. 이번 변경은 5를 유지하는 경계이며 CPU 저감이나 값을 2로 줄이는 정책을 주장하지 않는다.


## 기존 CI와 튜닝 보존 후속 검증

074a의 정확 CI는 backend3424 PASS/40 SKIP(369.75초), frontend PASS였다. 원 379의19 FAIL과 초기 검증 이력은 그대로다. 이 CI의 `-q` 원문에는 skip node/reason이 없어40개를 특정 Compose4개로 단정하지 않는다. `real_host_render`는 Docker executable 부재 때만 skip하며 전체11 매개 case다.

이후 튜닝 누락을 보여주는 새 positive3개는 기존 제품에서3 FAIL이었다. 첫 수정의3 FAIL/51 PASS는 기본 Map의 value 없는 key 한도를 잘못 거부한 구현을 보존한 결과이며, 그 형태를 그대로 유지해 현재 직접 모듈54 PASS/외부 호출0을 확인했다. n150 raw bytes/path/digest·기존 immutable/권한/G/RO 계약과 새 policy의 strict형식·중복거부·기본 태그 보존을 함께 검증했다. 이 후속 제품의 새 정확 CI·두 독립 리뷰·후보 UI 및 실제 설치/consumer 적용은 별도 미완이다.
