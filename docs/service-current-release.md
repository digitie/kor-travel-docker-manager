# 서비스 현재 릴리스 표식

이 문서는 이슈 #568·ADR-60의 정본이다. 현재 구현은 **관측한 이미지의 초기 등록(adopt), 조회, drift 검증, Compose 인자 생성**이다. Docker build/pull/up/restart·migration·DB rollback은 실행하지 않는다. 기존 배포 도구를 자동으로 가로채거나 설치본을 바꾸지 않는다.

## 유지해야 할 조건

- 현재 이미지는 tag나 작업 디렉터리의 소스 표식으로 추정하지 않고 runtime 컨테이너의 실제 image ID로 기록한다.
- 등록은 기존 Manager 전역 변경 잠금 G 안에서 수행한다. 잠금 경합이면 Docker 생성자와 파일 쓰기 전에 거절하며 다른 작업을 강제로 끝내지 않는다.
- 완성된 immutable release 디렉터리의 images.yml과 manifest.json을 쓴 뒤 current 서비스 symlink만 마지막에 원자 교체한다. 이전 표식은 실패 시 유지한다.
- reader는 pointer를 한 번 고정하고 그 디렉터리의 manifest와 overlay를 함께 읽는다. overlay는 services/image만 허용하며 역할 집합·ID가 manifest와 정확히 같아야 한다.
- 이미지 ID를 모두 포함하는 집합과 running을 요구하는 집합은 다르다. migrate도 overlay에 포함하지만 one-shot 컨테이너가 없다는 이유로 실패하지 않는다.
- 공용 DB·Manager Dagster daemon/webserver·퇴역 profile·monitoring을 앱 release에 섞지 않는다.
- 서로 다른 이미지 revision이 섞였거나 provenance를 모르면 공통 source_sha는 null이다. 개별 OCI revision은 관측값이며 코드 전체 해시를 확인했다는 뜻이 아니다.

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
    sudo ktdctl service-release compose weather -- up -d --no-deps kor-travel-weather-api

one-shot ID는 운영자가 실제 resolved Compose의 로컬 이미지 ID를 확인해 명시한다. API 이미지와 같다고 추정하거나 자동 실행하지 않는다. 모든 필수 one-shot을 지정해야 한다.

adopt는 runtime의 CID/image/start/health를 두 번 읽고 같을 때만 표식을 발행한다. 같은 상태의 반복 등록은 기존 표식을 반환한다. 현재 표식과 runtime이 다르면 기본 거절한다. 운영 담당자가 검증한 새 runtime을 metadata로 승계할 때만 --replace-current를 명시한다. 이 옵션도 role/Compose 좌표 변경을 조용히 허용하지 않으며 Docker를 변경하지 않는다.

show는 Docker나 G 없이 표식을 읽는다. verify는 관측만 하며 healthy=exit0, drift=exit1, 잘못된 표식/입력=exit2다. 파일·overlay가 깨졌으면 정상으로 간주하지 않는다.

compose-args는 Docker를 실행하지 않는다. argv 배열과 해당 그룹 runtime_services를 JSON으로 반환한다. 같은 project의 현재 overlay를 서비스명 순서대로 모두 포함한다. 뒤에 붙일 실행 명령·migration·drain·health와 다음 current의 발행은 해당 앱 배포기가 소유한다. 모든 배포기 이관 전에 기존 명령을 자동 대체했다고 보고하지 않는다.

## 호스트 wrapper: `service-release compose`

proposal의 `ktdm-odroid-compose`/`ktdm-transport-compose` 두 스크립트 대신 **하나의 일반 명령**을 쓴다. project·파일·overlay는 catalog와 current에서 유도하고 서비스 이름을 코드에 박지 않는다.

    sudo ktdctl service-release compose <service> [--group G] [-f /abs/extra.yml ...] -- <docker compose 하위 명령...>

- root로만 실행하며 G를 잡은 채 pointer를 읽고 `compose-args`와 같은 argv에 `-f` 추가 파일, `--` 뒤 인자를 붙여 `docker compose`를 실행한다. 종료 코드는 compose의 것을 그대로 돌려준다. G 경합·표지 누락·잘못된 추가 파일이면 Docker를 부르지 않고 exit 2다.
- 같은 project의 **모든** current overlay를 서비스명 순서로 붙인다. Geo를 다시 만들 때도 Weather overlay가 붙으므로 한쪽 overlay가 빠져 base 기본값으로 되돌아가는 일이 없다.
- `-f`는 current overlay **뒤**에 붙는다(뒤 파일이 이긴다). 절대 경로의 일반 파일만 받는다(symlink 거절). 후보 이미지나 되돌릴 이전 release의 `images.yml`을 넘기는 자리다.
- 이 명령은 current를 옮기지 않는다. 표지 갱신은 health 확인 뒤 `adopt --replace-current`가 한다.
- 읽기 전용 조회(`config`, `ps`)도 G를 잡는다. 배포 중에 조회하려면 G가 필요 없는 `compose-args`를 쓴다.

### 같은 상태로 재생성(정본 recreate)

    sudo ktdctl service-release compose geo -- up -d --no-deps kor-travel-geo-api

Geo/Weather(`kor-travel-odroid` project)의 정본 재생성 명령이다. 이미지는 current overlay의 image ID다. Transport는 운영 env(`RELEASE_SHA` 등)를 배포 스크립트가 셸 env로 덧붙이므로 이 명령으로 재생성하면 환경이 달라질 수 있다. Transport의 재생성 정본은 계속 `scripts/deploy-prod.sh`·`deploy-transport-admin-prod.sh`다.

### 배포(후보 → health → 표지)

1. `ktdctl service-release verify <service>`가 exit 0이어야 시작한다. drift(exit 1)·잘못된 표지(exit 2)면 멈춘다.
2. 후보 이미지 ID만 담은 overlay를 만든다(`services.<role>.image: sha256:<64hex>`).
3. `sudo ktdctl service-release compose <service> -f /abs/candidate.yml -- up -d --no-deps <roles...>`로 후보를 띄운다. migration·drain은 앱 배포기가 이 앞뒤에 둔다.
4. 앱 배포기의 health가 통과한 뒤에만 `sudo ktdctl service-release adopt <service> --confirm --replace-current [--oneshot-image ROLE=sha256:...]`로 실제 runtime을 새 current로 발행한다. 실패하면 current는 이전 release 그대로다.

### 되돌리기

ADR-51대로 DB 호환성을 먼저 판단한다. 호환이면 이전 release(`show`의 `previous`)의 `images.yml`을 `-f`로 넘겨 같은 compose 명령으로 재생성하고, health 뒤 `adopt --replace-current`로 current를 발행한다. 표지를 먼저 되돌리는 절차는 쓰지 않는다.

### 적용 전제

이 명령과 `service-release`는 해당 host에 그것을 포함한 Manager가 **설치된 뒤에만** 쓸 수 있다. 2026-10-28 기준 Odroid 설치본 `f368af3e`에는 `service-release`가 없다(초기 adopt는 별도 root source artifact로 실행). 다음 Odroid 정식 설치 전에는 앱 배포기가 이 명령에 의존하도록 바꾸지 않는다.

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
