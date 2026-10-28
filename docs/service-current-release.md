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

one-shot ID는 운영자가 실제 resolved Compose의 로컬 이미지 ID를 확인해 명시한다. API 이미지와 같다고 추정하거나 자동 실행하지 않는다. 모든 필수 one-shot을 지정해야 한다.

adopt는 runtime의 CID/image/start/health를 두 번 읽고 같을 때만 표식을 발행한다. 같은 상태의 반복 등록은 기존 표식을 반환한다. 현재 표식과 runtime이 다르면 기본 거절한다. 운영 담당자가 검증한 새 runtime을 metadata로 승계할 때만 --replace-current를 명시한다. 이 옵션도 role/Compose 좌표 변경을 조용히 허용하지 않으며 Docker를 변경하지 않는다.

show는 Docker나 G 없이 표식을 읽는다. verify는 관측만 하며 healthy=exit0, drift=exit1, 잘못된 표식/입력=exit2다. 파일·overlay가 깨졌으면 정상으로 간주하지 않는다.

compose-args는 Docker를 실행하지 않는다. argv 배열과 해당 그룹 runtime_services를 JSON으로 반환한다. 같은 project의 현재 overlay를 서비스명 순서대로 모두 포함한다. 뒤에 붙일 실행 명령·migration·drain·health와 다음 current의 발행은 해당 앱 배포기가 소유한다. 모든 배포기 이관 전에 기존 명령을 자동 대체했다고 보고하지 않는다.

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
