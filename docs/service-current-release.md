# 서비스 현재 릴리스 표식

이 문서는 이슈 #568·ADR-60의 정본이다. CLI는 **관측한 이미지의 초기 등록(adopt), 조회, drift 검증, Compose 인자 생성**에 더해 현재 구성을 확인하는 resolve와 정상인 현재 이미지로 선택 그룹 runtime을 재생성하는 recreate를 제공한다. recreate는 명시 --confirm 뒤에만 Docker up을 실행한다. resolve/recreate의 새 후보 배포·migration·DB rollback은 범위 밖이며 기존 배포 도구를 자동으로 가로채지 않는다. UI 새 후보는 아래 별도 apply-candidate 또는 등록된 component UI의 apply-component-ui 경로로 다룬다. resolve/recreate의 제품 회귀·독립 두 리뷰·전체 CI와 로컬 Docker 검증은 통과했으며 운영 적용은 대기 중이다.

## 유지해야 할 조건

- 현재 이미지는 tag나 작업 디렉터리의 소스 표식으로 추정하지 않고 runtime 컨테이너의 실제 image ID로 기록한다.
- 등록은 기존 Manager 전역 변경 잠금 G 안에서 수행한다. 잠금 경합이면 Docker 생성자와 파일 쓰기 전에 거절하며 다른 작업을 강제로 끝내지 않는다.
- 완성된 immutable release 디렉터리의 images.yml과 manifest.json을 쓴 뒤 current 서비스 symlink만 마지막에 원자 교체한다. 이전 표식은 실패 시 유지한다.
- reader는 pointer를 한 번 고정하고 그 디렉터리의 manifest와 overlay를 함께 읽는다. overlay는 services/image만 허용하며 역할 집합·ID가 manifest와 정확히 같아야 한다.
- 이미지 ID를 모두 포함하는 집합과 running을 요구하는 집합은 다르다. migrate도 overlay에 포함하지만 one-shot 컨테이너가 없다는 이유로 실패하지 않는다.
- 공용 DB·Manager Dagster daemon/webserver·퇴역 profile·monitoring을 앱 release에 섞지 않는다.
- 서로 다른 이미지 revision이 섞였거나 provenance를 모르면 공통 source_sha는 null이다. 개별 OCI revision은 관측값이며 코드 전체 해시를 확인했다는 뜻이 아니다.
- resolve/recreate는 root와 기존 G를 요구하며 Docker 생성자는 G를 획득한 뒤에만 만든다. 고정한 manifest·공유 overlay로 실제 Compose config를 메모리에서 해석하고 소유 이미지 ID와 현재 runtime의 healthy를 확인한다. drift나 불건강이면 변경 전에 거절한다. 고장 복구나 새 후보 적용 명령으로 쓰지 않는다.
- recreate는 검증한 resolved JSON을 stdin으로 전달해 같은 현재 이미지의 선택 그룹 runtime만 실행한다. one-shot·DB·공용 plane·다른 그룹은 실행하지 않고 current 갱신이나 자동 rollback도 하지 않는다.
- 앱 Compose는 기존 최소 실행 환경·DOCKER_*와 앱 env 파일을 사용한다. Manager 환경값을 앱 구성에 주입하지 않는다. 원문 config·환경값은 출력하지 않고 오류 원인은 기존 secret_scrub로 비밀을 가려 전달한다.

## 이슈 #568의 D1–D8 결정

| 항목 | 결정 |
| --- | --- |
| D1 | 정적 Compose + 서비스 current 표식 + immutable image overlay를 정본으로 한다. 현재 파일 제자리 수정은 기존 배포기의 이관 대상이며 새 정본으로 채택하지 않는다. |
| D2 | 절대 symlink를 쓴다. 고정 임시 이름 대신 고유 임시 symlink를 만들고 같은 디렉터리에서 replace한다. |
| D3 | 실행·재생성 기준은 전체 sha256 image ID다. mutable tag는 기준으로 쓰지 않는다. |
| D4 | drift는 명확히 실패한다. 기존 host-wide G를 그대로 쓴다. project 잠금 하나로 대체하면 installer·공용 plane과 직렬화되지 않는다. 기존 외부 배포기의 추가 잠금은 제거하지 않고 G 다음에 잡는다. |
| D5 | root가 쓰며 current는 0755, release 디렉터리는 0711, 공개 metadata 두 파일은 0644다. 앱 사용자에게 current 쓰기를 허용하지 않고 명시 root CLI를 사용한다. 비밀·raw inspect·환경값은 저장하지 않는다. |
| D6 | 기존 release 표식은 staged/source 준비와 admin 배포 표식을 구분한다. 준비 소스가 3f여도 runtime 4d는 준비 후 배포 전 상태로 설명할 수 있다. 기존 stage verifier를 깨뜨리지 않도록 표식을 무작정 삭제하지 않는다. 현재 배포 상태는 새 manifest의 구성요소별 ID/관측 revision으로 읽는다. |
| D7 | base image 기본값과 Manager 소유 옛 dagster-host 항목 제거는 모든 배포기·조회기의 wrapper 이관 및 config 비교 뒤 별도 적용한다. 초기 adopt에서는 base Compose나 공용 plane을 바꾸지 않는다. |
| D8 | Geo UI도 반드시 포함하고 latest-main의 현재 실제 ID를 고정한다. |

DB migration 후 옛 이미지로 자동 rollback하지 않는다(ADR-51). rollback도 호환성 판단·선택한 이미지 재생성·health 뒤 current를 바꾸는 순서다. proposal의 pointer 선행 교체 절차는 채택하지 않는다. 이번 CLI에는 rollback 실행 기능이 없다.

## 파일과 catalog

기본 root는 기존 데이터 디스크의 release root인 /srv/ktdm-releases다. 적용 전 운영자가 findmnt로 NVMe인지 확인한다. 이 기능은 mount/부팅 조건을 새로 만들지 않는다. root·부모 경로는 root 소유이고 group/other 쓰기와 중간 symlink가 없어야 한다.

- current/<service>: root 바로 아래 release 디렉터리를 가리키는 절대 symlink.
- <service>-adopt-<UTC>-<고유값>/images.yml: 해당 앱의 모든 image 역할. build/command/environment/volume 키는 없다.
- 같은 디렉터리 manifest.json: schema ktdm.service-release.v1, service, groups, images, source_sha, adopted_at, previous, health.
- images 항목: id, source_sha(null 허용), image_created_at(null 허용). adopted_at은 등록 시각이며 이미지 build 시각을 발명하지 않는다.
- release_directory는 reader가 계산한 출력 필드이며 파일에 독립 선언하지 않는다.

배포 좌표·소유 image 역할은 config/docker-targets.yml의 service_releases가 소유한다. host는 같은 target의 기존 host 선언에서 유도한다. Transport의 project·working_dir도 external_project: true로 기존 target 좌표에서 유도하며 두 번 선언하지 않는다. 이 목록은 target lifecycle의 services/runtime_services를 재분류하지 않는다. Weather의 옛 Prometheus나 공용 DB를 잘못 포함하지 않기 위함이다.

운영 대조에서 Geo 코드서버는 컨테이너 이름과 달리 Compose 서비스가 geo-dagster-code-server임을 확인했다. service_releases의 역할은 실제 Compose label/정본 파일 이름을 사용하고 Manager lifecycle의 kor-travel-geo-dagster-code-server를 그대로 복사하지 않는다. Transport의 dagster-migrate는 legacy-dagster profile이므로 현재 release에는 포함하지 않으며 활성 migrate만 명시한다.

Transport의 현재 운영 스크립트는 core/admin 모두 project kor-travel-transport를 강제하며 파일 조합이 다르다. catalog의 core/admin 그룹으로 나누되 같은 images.yml을 쓴다. 옛 registry 주석의 별도 admin project를 실제 배포 좌표로 추정하지 않는다. Geo/Weather는 project를 공유하므로 Compose 인자 생성 시 두 current overlay를 모두 붙인다. 하나가 없으면 base image로 fallback하지 않는다.

## CLI

아래 명령은 해당 host의 설치본 또는 정확한 작업 소스에서 실행한다. 초기 등록 전에 경합 중인 배포가 끝나고 담당이 확정되어야 한다. --root는 기존 root 신뢰·디스크 위치 검사를 대체하지 않는다.

    ktdctl service-release adopt geo --confirm
    ktdctl service-release adopt weather --confirm --oneshot-image kor-travel-weather-migrate=sha256:<64hex>
    ktdctl service-release adopt transport --confirm --oneshot-image migrate=sha256:<64hex>
    ktdctl service-release show weather --json
    ktdctl service-release verify weather --json
    ktdctl service-release compose-args weather --group main --json
    ktdctl service-release compose-args transport --group core --json
    ktdctl service-release compose-args transport --group admin --json
    ktdctl service-release resolve weather --group main --json
    ktdctl service-release resolve transport --group admin --json
    ktdctl service-release recreate transport --group admin --confirm --wait-timeout 120 --json

one-shot ID는 운영자가 실제 resolved Compose의 로컬 이미지 ID를 확인해 명시한다. API 이미지와 같다고 추정하거나 자동 실행하지 않는다. 모든 필수 one-shot을 지정해야 한다.

Pinvi 최초 ui-only 등록은 기본 root가 정확히 absent이고 parent가 trusted인 경우에만 own runtime 관측 뒤 기본 root를 준비할 수 있다. 사용자 지정 absent root나 다른 서비스에는 생성 예외를 적용하지 않고 기존 unsafe/symlink root의 권한을 바꾸지 않는다. Map current/디렉터리는 생성하지 않는다. [Pinvi legacy Map 부재 조건](pinvi-web-candidate.md#map-current가-없던-기존-호스트의-초기-등록)을 함께 따른다.

adopt는 runtime의 CID/image/start/health를 두 번 읽고 같을 때만 표식을 발행한다. 같은 상태의 반복 등록은 기존 표식을 반환한다. 현재 표식과 runtime이 다르면 기본 거절한다. 운영 담당자가 검증한 새 runtime을 metadata로 승계할 때만 --replace-current를 명시한다. 이 옵션도 role/Compose 좌표 변경을 조용히 허용하지 않으며 Docker를 변경하지 않는다.

show는 Docker나 G 없이 표식을 읽는다. verify는 관측만 하며 healthy=exit0, drift=exit1, 잘못된 표식/입력=exit2다. 파일·overlay가 깨졌으면 정상으로 간주하지 않는다.

compose-args는 Docker를 실행하지 않는다. argv 배열과 해당 그룹 runtime_services를 JSON으로 반환한다. 같은 project의 현재 overlay를 서비스명 순서대로 모두 포함한다. 뒤에 붙일 실행 명령·migration·drain·health와 다음 current의 발행은 해당 앱 배포기가 소유한다. 모든 배포기 이관 전에 기존 명령을 자동 대체했다고 보고하지 않는다.

### 정상인 현재 release의 확인·재생성

resolve는 root·G 안에서 manifest와 같은 project의 현재 overlay를 고정하고 실제 Compose config를 해석한다. 공유 project의 소유 이미지 ID와 현재 runtime 상태를 대조하며, 응답에는 비밀이 없는 이미지 정보만 담는다. compose-args의 기존 인자 출력 동작은 그대로 유지한다.

recreate는 같은 검사를 통과한 정상 상태에만 적용한다. --confirm이 필요하며 --wait-timeout은 1~600초, 기본 120초다. 실행은 선택 그룹의 runtime 서비스 이름을 명시한 다음 명령에 한정한다.

    docker compose ... -f - up -d --no-deps --no-build --pull never --force-recreate --wait --wait-timeout <초> <선택 그룹 runtime 서비스...>

stdin의 -f -에는 검증한 resolved JSON을 전달하고 기존 달러 escape 처리를 재사용한다. 검증 뒤 파일·환경을 다시 해석해 다른 구성이 실행되는 차이를 막는다. 성공·실패 어느 쪽에서도 current를 발행하거나 previous 이미지로 자동 되돌리지 않는다. 실패 후 상태는 다시 관측해 판단한다.

이 경로는 현재 release를 재생성하는 첫 제품 명령이며 외부 배포기의 새 후보 적용·health 후 current 갱신까지 이관한 결과가 아니다. 공개 API의 production ensure 금지와 external SDK start/stop/restart 예외는 기존대로다. 이 CLI의 G 사용을 모든 API 변경이 직렬화됐다는 의미로 해석하지 않는다.

resolve/recreate의 제품 회귀·독립 두 리뷰와 실제 로컬 Docker 검증은 아래 범위에서 통과했다. 전체 CI도 수용했으며 운영 적용은 대기 중이다. Odroid에서는 Transport R10 배포 작업이 G를 보유하고 있어 이번 명령을 호스트에 설치하거나 실행하지 않았다. 앞서 수용한 초기 adopt와 소유 이미지 ID 비교를 이번 명령의 운영 검증으로 대신하지 않는다.

재생성 중 SIGINT·SIGTERM은 Compose 자식 종료까지 유예하고 후속 검증 뒤 실패로 보고한다. 기존 G descriptor를 자식에게 상속하므로 부모만 강제 종료돼도 실행 중인 자식이 잠금을 보유한다. health 대기는 Compose의 상한을 쓰며, host timeout으로 CLI만 끝내고 G를 먼저 놓는 경로는 만들지 않는다.

### 2026-10-28 resolve/recreate 제품 검증 현황

- 제품 `ea53818e`는 독립 소스 리뷰 두 건의 G descriptor 수명·실패 원인 소실 지적을 수정한 뒤 두 리뷰를 통과했다. focused 104건과 전체 Ruff가 통과했다. SIGINT·SIGTERM·SIGKILL 경계 세 건은 실제 자식 프로세스 회귀이며 운영 Docker 강제 종료 실측은 아니다.
- WSL 기본 Docker의 실제 CLI 14건이 20.489초에 통과했다. 선택 runtime의 CID·시작 시각만 바뀌고 이미지는 유지됐으며 sibling·one-shot·current/metadata는 보존됐다. 달러가 포함된 환경값의 literal 유지, Manager 환경 오염 무효, 실제 이미지 drift·G 경합 거절을 확인했다. build·pull·원격 호스트 접속은 없었고 소유 fixture·컨테이너·holder는 모두 정리했다. 정상 canonical G 파일은 유지했다.
- `manager573-local-cli-summary-r1.json`과 `manager573-local-cli-e2e-actual-r1-994f9655366b4404b8e586ea55a9f931.json`은 이 로컬 실측의 감사 참고 파일이며 저장소의 운영 로그나 공개 증거 링크가 아니다. 운영 호스트 설치·재생성·UI 검증 결과로 해석하지 않는다.
- 제품 `ea53818e`의 CI 실행 `40674834332`는 backend 3,550통과·40skip(412.88초), frontend 타입·린트·47테스트·build 모두 통과했다. Transport R10 작업과 현재 운영 상태를 보존하며, 호스트 설치 및 외부 배포기·wrapper 전체 이관은 계속 미완이다.

## 적용과 남은 이관

1. 코드·회귀·두 적대 리뷰·CI를 수용한다.
2. 실제 host의 현재 runtime과 구성·디스크를 다시 확인하고 이미지 교체 없는 adopt만 한다. 다른 배포 작업의 전역 잠금을 기다리며 강제로 풀지 않는다.
3. compose-args의 인자로 config를 resolved하고 실제 ID와 비교한다. 출력에 비밀이 있을 수 있으므로 원문을 저장·게시하지 않고 image 선택값만 비교한다. 이 단계에도 재생성은 없다.
4. Geo/Weather의 호스트 wrapper와 Transport의 정본 배포 스크립트를 순서대로 이관한다. 후보 적용·health 후만 current를 갱신하며 DB 호환성·기존 source/config 계약을 보존한다.
5. 모든 조회·배포 경로가 이관된 뒤 base image 기본값·공용 plane 중복 선언 제거를 검증한다. 이 단계 전에는 raw Compose 우회가 물리적으로 차단됐다고 주장하지 않는다.

초기 adopt, wrapper 실행, 배포기 이관, 운영 drift 관측은 각각 별도 수용 항목이다. 문서 합의와 단위 검사만으로 운영 적용을 완료 처리하지 않는다.

## 검증

backend/tests/test_service_release.py는 경로·소유권·원자 발행·G 경합·runtime singleton/health/drift·one-shot·혼합 source·두 관측 변경을 검증한다. backend/tests/test_service_release_cli.py는 catalog 역할 완전성·host 유도·one-shot 필수 지정·공유 project overlay 누락·CLI confirm/G/Docker 순서를 검증한다.

### 2026-10-28 후보 검증 수용

- 제품 코드 51742949: 독립 적대 리뷰 두 건 수용. FIFO metadata의 blocking open을 O_NONBLOCK으로 바꾸고 실제 FIFO 회귀 두 건으로 닫았다.
- focused 68건·전체 Ruff 통과. 같은 코드의 GitHub CI는 백엔드 3,514 통과·40 skip, frontend 타입·린트·47테스트·build 통과다.
- 실제 local Docker CLI 10개: 등록·조회·healthy·인자 출력·멱등성·image drift exit1·명시 재등록/previous·G 경합 exit2를 확인했다. 자기 container/fixture/holder는 정리했고 local CLI가 처음 만든 정상 G 파일은 삭제하지 않았다. 운영 호스트 검증은 아니다.
- n150 Chromium156에서 PC18+모바일18 항목을 통과했다. 후보 auth/API/SQLite/session/WS는 실제 실행이고 Docker 상태·디스크 입력만 fixture다. 업무 쓰기를 막고 JS/예상 밖 console/request/HTTP 오류0, 임시 server/tunnel/container/DB 정리까지 확인했다. 운영 공개 UI 검증으로 바꾸어 쓰지 않는다.
- 초기 UI fixture의 private helper import 실패는 NOT_RUN 기록으로 보존했다. 로컬 전체 pytest의 동시 편집 중 실행도 1실패 기록으로 남기고, 동결한 동일 제품 코드의 전체 CI 성공과 구분한다.

운영 adopt와 wrapper/배포기 이관 항목은 이 결과로 완료 처리하지 않는다.

### 2026-10-28 병합 후 운영 초기 등록 대기

PR #569와 Geo 운영 Compose 이름 보정 PR #570은 두 독립 리뷰와 각 PR의 CI 성공을 거쳐 병합됐다. 이는 CLI 구현·검증 수용이며 Manager 운영 설치나 current 초기 등록 완료를 뜻하지 않는다.

운영 초기 등록의 첫 실제 시도는 `acquire-existing-G`에서 `ManagerMutationActiveError`로 실패했다. Docker 생성·source/current 파일 쓰기는 없었다. 경합 중인 Transport DB 백업·복원 작업이 끝나기를 기다리며 기존 G를 강제로 해제하지 않는다. `manager568-initial-adopt-actual-r1.json`은 실패를 보존한 로컬 감사 참고 파일이며 저장소의 운영 로그나 공개 증거 링크가 아니다.

운영 adopt와 실제 resolved image 비교는 대기 중이다. 이후 초기 등록이 성공해도 wrapper/배포기 이관, 후속 배포의 health 확인과 current 갱신, base 기본값 정리는 별도 수용한다.

### 2026-10-28 운영 초기 등록 결과와 resolved 비교 대기

CLI 소스 `182cbfd`의 root 소유 NVMe source artifact에서 공식 명령으로 Geo·Weather·Transport의 current를 발행했고, 각 verify는 healthy였다. 전후 14개 컨테이너의 CID·image·시작 시각·running과 Manager env hash가 같았다. Manager 설치본·이미지·DB·schedule은 바꾸지 않았다. 세 manifest의 공통 source_sha는 OCI revision 미확정으로 null이며, Transport의 현재 4d 실행을 3f 배포 완료로 해석하지 않는다.

두 번째 실행은 마지막 `compose-args:transport:admin`에서 전체 105초 deadline에 도달해 `TimeoutExpired`로 끝났다(113.438초, 전체 FAIL). 세 초기 등록 성공과 전체 실행 실패를 함께 보존하며 첫 G 경합 실패도 유지한다. `manager568-initial-adopt-actual-r2.json`은 로컬 감사 참고 파일이며 저장소의 운영 로그나 공개 증거 링크가 아니다.

실제 resolved image 비교는 아직 수용하지 않았다. wrapper/배포기 이관과 base 기본값·옛 공용 plane 중복 선언 정리도 계속 별도 미완이다.

### 2026-10-28 운영 초기 등록·소유 이미지 ID 비교 수용

초기 등록 뒤 읽기 전용 검증을 17:30:48–17:32:14 KST에 수행했다(85.917초, exit 0/PASS). 공식 show 세 건·compose-args 네 건과 실제 Compose config를 사용했고 Geo main·Weather main·Transport core/admin의 config는 모두 exit 0, stderr 없음이었다. 소유 역할 13개(runtime 11·one-shot 2)가 모두 manifest·resolved 로컬 이미지·해당 runtime 이미지 기준으로 일치했다. one-shot은 실행 상태를 주장하지 않고 로컬 이미지 존재와 ID만 확인했다.

전후 14개 컨테이너의 CID·image·시작 시각·running과 Manager env hash를 보존했다. 검증 범위는 소유 이미지 ID이며 `nonimage_configuration_verified=false`다. 전체 비이미지 구성 동등성·후속 배포 안전을 수용한 결과가 아니다. `manager568-resolved-config-actual-r1.json`은 로컬 감사 참고 파일이며 저장소의 운영 로그나 공개 증거 링크가 아니다.

앞선 G 경합 실패와 마지막 admin compose-args timeout에 따른 초기 실행 전체 FAIL은 그대로 보존한다. 등록에 사용한 source artifact도 보존하며 Manager 설치본·이미지·DB·schedule은 바꾸지 않았다. 미확정 source_sha null과 Transport 4d 실행은 유지한다. 운영 초기 등록·소유 이미지 ID 대조는 수용하되 wrapper/배포기 이관과 base 기본값·옛 공용 plane 중복 선언 정리는 계속 미완이다.


## UI 후보만 적용하는 별도 경로

`apply-candidate`는 기존 `adopt`·`resolve`·`recreate`·pair pin의 의미를 바꾸지 않는다.
Map catalog의 `ui` 그룹은 `kor-travel-map-ui` 하나만 소유한다. Manager의 실제 project와
`/opt/kor-travel-docker-manager` 아래 immutable `.ktdm-release-compose.yml`·`.env`를 사용한다.
컨테이너 label의 config_files가 `-`인 것은 이전 frozen stdin 실행의 표식이며 파일명이 아니다.

초기 current가 없으면 기존 `service-release adopt map --confirm`으로 정상 실행 UI를 먼저
관측·등록한다. 후보 적용은 그 초기 UI image ID가 canonical committed paired 기록과 일치할 때만
시작한다. 정상인 기존 paired receipt를 복구한다며 새로 만들지 않는다.

```bash
ktdctl service-release apply-candidate map --group ui \
  --image-id sha256:<승인한64자리이미지ID> --source-sha <승인한merged40자리SHA> \
  --source-clone <root소유canonicalMapGitclone> --dry-run
# 같은 승인 tuple을 실제 적용할 때만 --dry-run 대신 --confirm을 사용한다.
```

SDK는 `service_release_candidate.apply_ui_candidate`다. `execute=False`는 읽기 전용 계획이며
`execute=True`만 UI를 바꾼다. 두 경우 모두 root와 기존 host-wide G를 요구하고 Docker client는
G 획득 뒤 생성한다. 새 잠금·서명·receipt 체계는 만들지 않는다.

source clone의 부모·Git 파일은 root 소유이며 group/other 쓰기·symlink·외부 object alternates를
허용하지 않는다. origin은 canonical Map URL이고 승인 SHA는 commit이며 로컬 origin/main에
포함되어야 한다. CLI는 fetch/build/pull을 수행하지 않는다. 운영자는 부모가 승인한 실제 merged SHA와
이미지 ID를 준비해야 한다. 이미지 ID와 OCI revision/source label을 대조하되 label만으로 빌드한
전체 소스 bytes를 재증명했다고 주장하지 않는다.

catalog의 고정 env 파일만 사용하고 process 환경·dotenv 외부 보간을 배제해 canonical `pinned_runtime_state_root`의 committed 배포 기록을 읽는다. env의 project도 선택 plan과 같아야 하며 현재 pair pin과 일치해야 한다.
현재 topology의 필수 paired runtime과 companion 역할을 빠짐없이 요구하고 비선택 paired 이미지도 실제 runtime과 같아야 한다. 이 기록과 pin은 **읽기만** 하며 UI SHA로
`map_revision`을 바꾸지 않는다. 새 UI provenance는 기존 service-release manifest/images.yml/current에
기록한다. 따라서 paired 기록의 과거 UI ID를 최신 component current로 오인하지 않는다. 이후 paired
rebuild가 UI를 바꾸면 component verify는 drift를 정직하게 보고하며 자동으로 표식을 맞추지 않는다.

검증한 Compose config를 메모리에서 고정하고 UI image를 승인한 immutable ID로 바꾼다.
실행은 `-f - up -d --no-deps --no-build --pull never --force-recreate --wait`와 UI 서비스
하나뿐이다. API·DB·Dagster·one-shot을 명령에 넣지 않는다. 달러 escape·최소 실행 환경·비밀 가림·
자식 종료까지 G descriptor를 보유하는 기존 코드를 재사용한다.

필수 companion은 frozen reference와 immutable image 관계로 유도하며 live drift로 기대 역할을 줄이지 않는다.
실행 직전 UI identity·전체 비선택 컨테이너의 ID/image/시작 시각/재시작 수/설정·mount/network,
paired 기준선/pin·current를 재검증한다. health와 비선택 불변 검증을 통과한 뒤 metadata 두 파일을
완성하고 발행 직전에 다시 확인해 current symlink를 바꾼다. 실패하면 current는 이전 값을 유지한다. 관측한 사후 불변식/health 실패는 해당 실행의 terminal 실패이며 다음 재검사 성공으로 승격하지 않는다. 자식 실패·중단·launch 오류 뒤에도 UI 검증, 비선택 HostConfig 전체와 기준선/current를 재관측해 종료 원인과 함께 반환한다.
발행 직전 guard·metadata I/O 실패도 child 종료값과 사후 진단을 보존한 구조화 실패로 반환한다. pointer 교체 뒤 durability 오류가 나면 재관측해 current 변경 여부를 정직하게 표시한다.
이미 UI mutation이 적용됐을 수 있으므로 실패 응답을 무변경 성공이나 자동 rollback으로 보고하지
않는다. 기존 `recreate`도 고장 복구 명령으로 바꾸지 않는다. 비밀 값·원문 inspect/config는 결과나
새 manifest에 저장하지 않는다.

실제 운영 적용은 후보 코드 CI·독립 delta 리뷰2건·Manager 머지와 부모의 정확 merged SHA/go 이후다.
현재 active Manager에서는 공식 `--cli-only` 설치 조건을 충족하지 않으므로 기존 trusted normal
업그레이드가 선행한다. 임의 venv/PYTHONPATH나 설치 venv 덮어쓰기로 우회하지 않는다. Manager 설치가
필요해도 Map API·DB·Dagster 수명주기는 호출하지 않는다.


## 기존 혼합 그룹의 등록 UI만 교체

Geo·Weather의 기존 main current에는 API·UI·코드 서버가 함께 있다. Transport는 기존 core/admin
그룹을 유지하며 public frontend만 선택한다. 이 배포를 위해 새 ui 그룹을
겹쳐 등록하거나 기존 manifest layout을 바꾸지 않는다. targets.<target>.ui_candidate는 기존
target에 등록된 UI 역할과 canonical 저장소 URL만 선언한다. 실제 Compose 서비스는 같은 target의
컨테이너 등록에서, group은 기존 service_releases에서 유도한다. 잘못된 역할·중복·다른 target의
컨테이너·유효하지 않은 URL은 거부한다. 회귀 정본은
test_catalog_derives_ui_from_existing_registered_target_without_new_group이다.

~~~bash
sudo -n ktdctl service-release apply-component-ui geo \
  --source-clone "$GEO_APPROVED_SOURCE_CLONE" --source-sha "$GEO_MERGED_SHA" \
  --image-id "$GEO_APPROVED_IMAGE_ID" --dry-run --json
sudo -n ktdctl service-release apply-component-ui geo \
  --source-clone "$GEO_APPROVED_SOURCE_CLONE" --source-sha "$GEO_MERGED_SHA" \
  --image-id "$GEO_APPROVED_IMAGE_ID" --confirm --json
~~~

Weather는 같은 명령의 target을 weather로 선택한다. source와 image는 운영자가 검증한
merged40자리 SHA 및 현재 Docker engine에 존재하는 immutable sha256 ID다. source clone은
root 소유 canonical origin/main의 승인 commit을 포함해야 하고 image OCI revision/source도
승인 tuple과 같아야 한다. label 검사가 빌드 내용 전체의 증명을 대신하는 것은 아니다.
새 target metadata는 대응 SDK와 함께 설치하며 이전 SDK에 설정만 먼저 적용하지 않는다.

Transport public UI는 같은 명령의 target을 transport로 선택한다. 등록된
transport-frontend 역할에서 Compose frontend를, 기존 current에서 core 그룹을 유도한다.
core의 backend·dagster-code-server·migrate와 admin의 두 역할은 기존 이미지와 metadata를
보존한다. 그룹을 ui로 분할하거나 current를 재등록하지 않는다. 외부 working_dir와 Compose
파일은 기존 current·catalog에서 읽으며 승인 UI source를 운영 checkout에 rsync하지 않는다.

UI 후보 저장소에 backend Alembic head가 운영 DB보다 오래됐어도 이 경로는 backend 파일·이미지,
DB 또는 code-server를 교체하거나 migration을 실행하지 않는다. 후보 source는 UI 이미지의 승인
revision 검사에만 쓰고 실제 구성은 현재 운영 Compose에서 고정한다. 이는 UI의 API 응답 호환성을
보장한다는 뜻은 아니다. 실제 UI·API·health 수용은 배포 후 별도로 수행한다. 기존 전체 bundle
배포의 schema gate를 완화하거나 이 UI 경로로 전체 bundle을 우회하지 않는다.

회귀 test_service_release_transport_ui_candidate.py는 외부 core/admin 여섯 이미지, 기존
backend revision과 더 오래된 후보 migration source를 함께 구성해 frontend 한 개만
--no-deps로 실행되는지 확인한다. 다른 runtime의 변경은 current 발행을 거부한다. fixture의
schema 표식 보존은 실제 DB 운영 측정이 아니며 운영 DB는 적용 전후 읽기 전용으로 따로 확인한다.
관리자는 같은 명령에 --component admin을 추가한다. components.admin.runtime은 기존
service_releases.transport.groups.admin의 transport-admin-web 하나를 참조한다. 추가 target,
container lifecycle 역할, current 그룹을 만들지 않고 gateway와 core를 그대로 유지한다.
임의 --role·--env·runtime 인자는 받지 않는다. 모르는 component, API/one-shot/다른 release의
runtime, 중복 선택 또는 선언되지 않은 필드는 Docker 조회 전에 거부한다.

~~~bash
sudo -n ktdctl service-release apply-component-ui transport --component admin \
  --source-clone "$TRANSPORT_APPROVED_SOURCE_CLONE" --source-sha "$TRANSPORT_MERGED_SHA" \
  --image-id "$TRANSPORT_ADMIN_IMAGE_ID" --dry-run --json
~~~

source_clone은 G의 source 검사에 맞는 root 소유 canonical Git clone이어야 한다.
git archive staging만 전달하거나 외부 alternates/worktree Git 저장소를 승인 clone으로 쓰지 않는다.
현재 Compose와 shared images overlay만 읽으며 기존 관리자 배포기의 전체 checkout rsync는 호출하지 않는다.
공개 UI와 관리자는 순차로 적용하고 각 실행은 방금 관측·발행한 current를 다시 읽는다.
혼합 source인 backend와 두 UI를 공통936 revision이라고 발행하지 않는다.

기존 G를 획득한 뒤 source를 재확인하고, 정상인 기존 component current와 공유 project의
모든 overlay를 읽어 Compose JSON을 한 번 고정한다. 적용은 선택 UI 하나와
--no-deps --no-build --pull never만 사용한다. Weather migrate 정의·이미지는 기존 overlay에
남지만 실행하지 않는다. 다른 runtime·DB·공용 Dagster를 실행 대상으로 넣지 않는다.
전체 비선택 컨테이너의 ID/image/start/restart/config/mount/network 보존과 모든 current·health를
검증한 뒤 기존 manifest의 UI 이미지 metadata만 바꿔 current를 발행한다.

비선택 비교에서 Docker inspect의 Mounts가 목록이면 전체 항목을 JSON 키 정렬 직렬화 기준으로
정렬해 열거 순서 차이만 제거한다. mount 항목의 모든 중첩 필드와 중복 수는 보존하며,
키 부재·null·빈 목록도 서로 구분한다. mount 내부 배열, Config.Env, HostConfig.Binds 등
다른 배열은 정렬하거나 무시하지 않는다. Source/Destination/RW/Mode/Propagation/Type,
추가 필드·중복 수가 바뀌면 실행 전·사후·발행 직전의 기존 비선택 guard가 계속 거부한다.
회귀는 test_service_release_candidate_mounts.py에서 Map paired와 Geo/Weather SDK 경로로 검증한다.

선택 UI의 현재 effective 환경은 G 안에서 읽고 기존 값을 보존한다. 후보 이미지에만 새로
추가된 기본 환경은 합치되 같은 키는 현재 값이 우선한다. 유일한 선택적 예외는 등록된
component의 release_env다. 이 키는 RELEASE_SHA 형식의 source 표시 전용이며, 후보 이미지
기본 Env의 값이 승인 source와 다르거나 없으면 변경 전에 거부한다. 같은 source의 canonical
origin/main 승인·OCI revision/source 검사도 모두 통과해야 해당 키만 새 SHA에 묶을 수 있다.
Transport 관리자의 TRANSPORT_ADMIN_RELEASE_SHA는 인증 후 GET /api/release의 표시 값으로만
소비되며 인증·세션·지도 키와 분리된다. public frontend에는 현재 revision 환경 키가 없어
일괄 환경 재작성이나 backend499의 revision 주입을 하지 않는다.
selected_environment_unchanged는 이렇게 승인한 source 표식과 그 외 기존 환경의 보존 조건이다.
저장된 Compose의 옛 키를 현재 UI에 다시 주입하지 않는다. 해석한 환경을 stdin에 고정하고 선택 UI의 env_file을 다시 읽지 않는다.
실행 전·사후·current 발행 직전에 승인한 환경/이미지 관계를 재검사한다. 읽을 수 없는 환경,
중복/불완전 항목·관찰 중 변경은 거부하고 actual 값은 오류·새 manifest에 저장하지 않는다.
Docker inspect의 raw 달러는 component에서 Compose 표현으로 한 번 인코딩하고 승인·비교는 raw 값을 사용한다.
render 실패부터 현재 값과 이미지 기본값의 비밀을 가린다. 이 보존은 component 경로에 한하며
기존 Map paired 경로의 환경 의미를 바꾸지 않는다.

Map/Pinvi의 paired 기준선을 Geo/Weather의 기준선으로 사용하지 않는다. 반대로 이 경로에
Map/Pinvi를 넣어 기존 paired 검사를 우회할 수도 없다. Map의 기존 apply-candidate는 기존
receipt/companion 검사를 그대로 유지하고 동일한 단일 UI 실행·관측·발행 본문을 재사용한다.
추가 pinset·journal phase·digest·봉인 파일이나 영구 env 해시 gate를 만들지 않는다.

dry-run 성공은 계획 수용이고 배포 완료가 아니다. mutation 이후 실패는 실제 실행·진단과
current 발행 상태를 구분하며 자동 rollback하지 않는다. current가 옛 상태라고 실제 UI가
바뀌지 않았다고 추정하지 않는다. 실제 이미지·health를 확인하고 기존 명시 복구 절차를 따른다.
새 경로의 local fake Docker/실제 G 자식 회귀와 CLI·registry 검사는 통과했으며 운영 적용은
정확 CI·두 독립 리뷰·PR 병합·설치 후 별도로 수용한다.


## Transport core 정상 이미지 후보와 state 인계

`apply-core-candidate`는 Transport의 기존 `core` 그룹만 다루는 별도 CLI/SDK 경로다.
기존 adopt/recreate/UI 후보 경로의 의미를 바꾸지 않는다. HTTP API·current manifest
schema·package 의존성·producer 정상 build wrapper는 변경하지 않는다.

    ktdctl service-release apply-core-candidate transport \
      --source-clone <root 소유 canonical merged M clone> --source-sha <M> \
      --build-receipt <정상 build receipt> --build-sha256 <승인 SHA256> \
      --receipt <별도 runtime prebuilt-v1 receipt> --sha256 <승인 SHA256> \
      --dry-run

실제 변경은 같은 입력에서 `--confirm`으로 선택한다. build receipt의
`mode=NORMAL_BUILD_ONLY`, `actualApplyAllowed=false`, `deployReady=false`는 그대로다.
이 파일은 immutable image의 출처 증거이며 운영 적용 허가나 live state 증거로
승격하지 않는다. 별도 runtime-v1의 release/images/schema/state/instigators를
정상 receipt에 projection하여 비교하고, 현재 CS·state freshness·schema·drain을
실행 시점에 다시 읽는다. 잘못된 tuple/경합은 Docker 변경 전에 거부한다.

역할은 기존 core catalog의 backend/frontend/dagster-code-server 세 runtime이며
migrate는 backend image의 **metadata alias만** 갱신한다. migrate 컨테이너를 생성·시작하거나
Alembic upgrade/stamp/downgrade를 실행하지 않는다. Git blob에서 코드 실행 없이
구한 candidate Alembic head와 runtime receipt 및 live read-only schema 결과가
같아야 한다. backend/CS의 기존 `RUN_DB_MIGRATIONS`는 명시적 `false`여야 하며
값이 없거나 다르면 자동 수정하지 않는다. 기존 normal producer의 `RELEASE_SHA`만
등록한 source M으로 바꾸고, 나머지 effective ENV·인증·키·session은 보존한다.

public frontend의 `NEXT_PUBLIC_API_BASE_URL`·`NEXT_PUBLIC_API_PORT`는 Next.js 빌드 때
client에 고정되므로 runtime ENV 보존만으로 동등성을 승인하지 않는다. 기존 frontend
ImageID의 두 기본값을 baked 기준으로 고정하고, 후보 이미지 기본값과 normal producer의
`artifacts.frontend.publicInputHashes` 두 항목 및 기존 public-input image label을 함께
비교한다. producer에는 두 입력을 명시하며 빈 base URL도 실제 입력으로 해시한다.
누락·추가 hash·불일치는 state snapshot/컨테이너 생성 전에 거부하고, 공통 사후 검사와
current 발행 직전에도 같은 기준을 재확인한다. 기존 image 기본값과 live ENV가 달라도
live 값을 client 승인으로 쓰지 않는다. 기존 nonrevision live ENV는 그대로 보존하며
runtime override로 후보 baked 변경을 숨길 수 없다. producer·receipt schema는 바꾸지 않는다.

현재 Transport의 `/opt/dagster/state`는 collector checkpoint volume과 별개인
CS writable layer다. 따라서 기존 mount 보존만으로 local artifact/compute log가
새 CS에 전달되었다고 판단하지 않는다. 한 G와 같은 기존 project flock 아래에서:

1. 이전 CS state를 고정 경로로만 snapshot한다. root 아래 전용 0700 디렉터리와
   0600 archive를 사용하고 기존 fingerprint/semantic manifest와 대조한다.
2. 검증된 frozen stdin에는 runtime 세 개만 남기고 의존 one-shot 참조를 제거한다.
   `up --no-start --no-deps --no-build --pull never --force-recreate`로 새 컨테이너를
   시작하지 않고 만든다. 이미지에 state를 덧붙이거나 commit/retag하지 않는다.
3. 승인된 새 CS의 created 상태·ImageID·mount를 확인한 뒤 기존 Docker SDK
   get_archive/put_archive로 writable layer에만 복원한다. 경로 이탈·중복·링크·특수
   파일·위험한 mode를 거부하고 UID/GID/mode/content를 보존한다. 고정 `/opt/dagster`
   부모가 없으면 그 부모 header만 안전하게 준비한다. host에는 tar를 풀지 않는다.
4. 새 CS를 시작하기 전에 state readback을 비교한다. ctime/inode의 일치를
   조작하지 않으며 원본 freshness와 목적지 semantic copy를 구분한다.
5. `start --wait`로 이미 만든 세 runtime만 시작한다. cold-start 대기는 기존
   frozen health 선언에서 파생한다. health/current 후보 이미지·ENV·mount·schema·state·
   instigator/worker·비선택 보존을 확인한 뒤 기존 publisher로 current를 한 번 교체한다.

G 다음에 잡는 project lease는 `targets.transport.core_candidate.project_lease`에
선언하며 승인 M의 기존 deploy-prod-remote.sh literal과 같아야 한다. 새 lock 경로나
대체 lease를 만들지 않는다. state 전송 자식과 Compose 자식 모두 G와 lease FD를
상속하고, 정상 signal은 기존 runner가 자식 종료까지 유예한다. 부모만 SIGKILL된
경우에도 살아 있는 자식이 두 descriptor를 보유한다. 임의 root의 별도 Docker/GraphQL
변경까지 막는 분산 fence라고 주장하지 않는다.

실패 시 이전 current와 private state snapshot을 보존하고 자동 재시도·rollback·adopt·
전체 plane pause를 하지 않는다. 실패 뒤 실제 runtime은 일부 교체되었을 수 있으므로
원인 확인과 별도 forward-fix 판단이 필요하다. archive에는 운영 데이터가 있으므로
public metadata나 Git에 넣지 않는다.

이 구현의 Linux 단위 검증은 fake Docker/GraphQL 경계 및 실제 파일/tar/FD/signal을
사용한다. 실제 Docker daemon의 새 이미지/no-start/state 인계와 운영 적용은 별도
두 소스 리뷰·정확 CI·병합·trusted SDK 설치 및 최종 이미지 tuple gate 이후다.


## Pinvi web 단독 후보

실제 pinvi-web 역할만 선택하는 [Pinvi web 후보 계약](pinvi-web-candidate.md)을 따른다. 기존 pair 재구축을 호출하지 않으며 검증된 Map component current와 pair의 다른 runtime/companion을 보존한다. 소스 회귀 통과와 운영 설치·적용·실제 브라우저 수용은 별도 단계다.

Pinvi web 적용 뒤의 Map 후보도 검증된 Pinvi current를 같은 방식으로 관측한다. 초기 Pinvi current가 없으면 후보 전용 경로에서 원 pair image 일치를 요구하며, 일반 compose-plan 준비는 공식 bootstrap으로 마친다. 상세 실패 경계는 위 Pinvi 계약의 양방향 절을 따른다.


## Weather 최신 정상 이미지와0040 후보

`service-release apply-core-candidate weather`는 기존 `weather.main`의 세 runtime(API·CS·UI)과 등록된 `kor-travel-weather-migrate`만 다룬다. Transport 후보 경로, paired pin/adopt/recreate, 공용 plane과 current manifest schema는 바꾸지 않는다.

```bash
ktdctl service-release apply-core-candidate weather \
  --source-clone <root 소유 canonical 실제 merged M clone> --source-sha <M> \
  --build-receipt <정상 3역할 build receipt> --build-sha256 <승인 SHA256> \
  --receipt <현재 schema/state/instigator/compiled baseline 승인 입력> --sha256 <승인 SHA256> \
  --dry-run
```

같은 승인 입력에서 `--confirm`이 실제 변경이다. normal producer의 `version=1`, `kind=weather-normal-build`, `mode=NORMAL_BUILD_ONLY`, `actualApplyAllowed=false`, `deployReady=false`는 그대로 둔다. build receipt를 운영 적용 허가나 live state로 승격하지 않는다. 역할은 `api`/`code-server`/`frontend`이며 `migrate`는 API ID alias다. `INSTALL_DAGSTER=0/1`의 API·CS는 별도 정상 이미지다. `deploy/Dockerfile.python`의 baked `KOR_TRAVEL_WEATHER_GIT_COMMIT`와 OCI/COPY/role/tree가 실제 M이어야 한다. frontend는 USERnode와 두 public input hash를 요구하며, Dockerfile에 없는 release 기본 ENV를 만들지 않는다.

공개 입력은 기존 runtime ENV만으로 승인하지 않는다. 기존 UI의 imageID와 승인한 `/app/.next/static/chunks/*.js` 파일 SHA·두 입력의 byte 구간을 private runtime receipt의 `public_baseline.byte_ranges`로 고정한다. 실제 기존 CID의 Docker archive 일반 파일에서 해당 구간을 다시 해시하고, baseline hash·normal receipt·후보 image label을 비교한다. 원문 키·URL을 새 manifest/오류/argv에 기록하지 않는다. 기존 기본 ENV 부재를 빈 문자열로 간주하거나 runtime override로 baked 변경을 숨기지 않는다.

### 실제 쓰기 전제와 실행 순서

- root, 기존 canonical current와 healthy runtime, 공식 merged Git/normal receipt, 같은 shared project의 다른 current, 비선택 lifecycle/mount, 승인 ENV/auth/session/key, source/python revision 외 환경 전체를 확인한다.
- Weather용 root 소유 project lease는 catalog에 선언한 고정 위치에 미리 준비돼 있어야 한다. CLI는 누락 lock을 만들지 않는다. 기존 G 다음 이 lease를 잡고 모든 mutation 자식에 두 FD를 유지한다.
- **기존 Transport와 같이 Weather schedule/sensor 사전 pause와 active run/worker drain을 요구한다.** 다른 location의 instigator 상태는 그대로여야 한다. 이 factory는 새 GraphQL 쓰기 인터페이스나 전역 pause를 추가하지 않는다. 전제 미충족은 stop/backup/snapshot/migration 전에 거부한다.
- root Git의 단일 migration lineage에0040이 포함돼야 하며 target은 승인 actual main에서 읽은 최신 head다. 특정 앱 SHA/0040을 영구 target으로 고정하지 않으며, 현재 DB revision은 runtime receipt와 같고 같은 lineage의 조상이어야 한다. 등록된 Weather tenant DSN/backup tenant와 migrate DSN/password가 같아야 한다. 알 수 없는 schema는 자동 보정하지 않는다.
- 선택 세 runtime만 정지한 후 고정 `/opt/dagster/state`를 private0600 archive로 보존한다. 기존 Weather export named volume과 공용 Dagster config bind는 이동·변경하지 않는다.
- 기존 `standalone_backup`의 Weather role로 새 tenant dump/TOC/hash/schema 증명을 만들고 이전 schema 일치를 요구한다. 이 role은 공용 PostgreSQL의 Weather DB만 지정하며 Dagster/shared/다른 DB 복원이나 재시작을 하지 않는다.
- 이미 정확0040이면 migrate를 실행하지 않는다. 아니라면 승인한 API image의 등록 command `alembic upgrade head`를 `--no-deps --no-build --pull never`의 선택 oneshot으로 한번 실행한다. DB-init·PG·Prometheus·legacy Dagster profile·다른 앱은 실행 문서에서 제외한다. source Git과 다른 schema·stamp/downgrade·자동 retry는 허용하지 않는다.
- 실제 merged Git head 확인 후 runtime3을 미시작 상태로 생성하고 CS state를 기존 archive helper로 복원한다. 고정 prefix·UID/GID/mode·semantic readback과 새 CS imageID/label/mount를 시작 전에 검사한다. Docker commit/copy/retag로 정상 이미지에 운영 state를 구워 넣지 않는다.
- 세 runtime health·schema·state·ENV/mount·다른 current/pin/컨테이너 불변과 Weather pause 상태를 재확인한 뒤 현재 표식만 한번 발행한다. 해석한 Compose는 stdin으로 고정하고 등록 파일은 수정하지 않는다.

### 실패·후속 수용 경계

실패하면 실제 phase, snapshot/backup을 보존하고 current를 발행하지 않는다. DDL 후에도 이미지/DB 자동 rollback, 반복 migration, ensure/adopt 우회는 없다. current가 옛 상태여도 선택 runtime이 정지됐거나 새 schema/이미지가 일부 적용됐을 수 있으므로 실제 상태를 확인하고 명시적으로 복구한다.

성공 결과도 `scheduler_resume_required=true`다. factory가 승인을 받지 않은 shared Dagster 상태를 자동 재개하지 않는다. 운영자는 원래 Weather instigator 상태만 공식 경로로 복구하고 타 location 불변·새 region API/readmodel/정상 UI를 별도 수용해야 한다. 따라서 healthy/current와 전체 운영 기능 완료를 구분한다. 초기0040 파생 테이블0행/`ready:false`는 예상 상태이며 기존 publication 후 갱신과 catalog fallback을 확인한다. 별도 DB 쓰기로 가짜 warm 상태를 만들지 않는다.

현재 소스는 fake Engine/로컬 회귀 대상이다. 실제 운영 snapshot/backup/DDL/이미지 배포·정상 로그인 PASS를 주장하지 않는다. 동일 원2인·정확 CI·실제 merged SDK 설치와 최종 실제 merged Weather 정상 이미지 증명 뒤에만 별도 운영 tuple을 승인한다. 기존 C11 bytes/버전과 CI순서는 변경하지 않는다.


### Weather source2의 DSN·canonical ENV·oneshot 사후 경계

DB 대상은 SQLAlchemy의 표준 URL parser로 해석한다. URL authority/path를 덮어쓰는 `host`/`hostaddr`/`port`/`dbname`/`service` 등 routing·credential query selector는 migration 전에 거부하고, 정상 DSN과 `connect_timeout`은 허용한다. 실제 driver의 query 우선순위를 무시한 별도 문자열 parser로 backup 대상 일치를 주장하지 않는다.

등록 Compose의 config JSON에 남은 달러 escaping은 한 번 decode한 actual 값으로 live API ENV와 비교한다. 그 actual 값을 stdin Compose 표현으로 한 번 encode하며, 실제 한 달러와 두 달러 및 불일치 대조군을 따로 보존한다. fake fixture도 실제 canonical config 표현을 사용한다.

migration 역할은 실행하는 그 단계에만 변경을 허용한다. 초기 단일·정지 상태를 보존하고 정상 실행 뒤에는 exact CID/image/command·환경/HostConfig/mount/lifecycle을 다시 고정해 사후·current 발행 직전까지 비교한다. 실행 완료 뒤 migration 역할이 바뀌거나 새 duplicate가 생기면 current를 발행하지 않는다. schema가 이미 head라서 실행을 생략한 경우도 기존 oneshot의 부재/정지 상태를 그대로 보존한다.
