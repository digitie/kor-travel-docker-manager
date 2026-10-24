# 호스트 Compose 보간 범위

## 목적과 예외

Compose는 비활성 profile의 필수 환경변수도 보간한다. 다른 호스트로 이전한 Weather의
필수 값이 이 호스트에 없을 때 Map·PinVi 쌍 재구축과 CLI 전용 설치가 함께 막히는 경계를
좁힌다. 임의 서비스 목록을 받는 새 CLI 옵션이나 더미 비밀은 추가하지 않는다.

- 쌍 재구축의 세 capture는 정본 Map·PinVi runtime/family·직접 support 서비스,
  bootstrap one-shot·외부 prerequisite·공용 Dagster plane에서 실제 Compose 종속성을 닫는다.
- 설치기의 `--cli-only` 사전검사는 후보 release의 정본 reference Compose와 검증된
  레지스트리에서 **모든 호스트 소유 서비스·initializer·family**를 선택한다. Map·PinVi
  쌍이 없는 호스트에도 쌍 전용 C6C 자격증명 검사를 추가하지 않는다.
- 일반 설치, 일반 Compose 검증과 `service_scope=None` 경로는 전체 문서 검증을 유지한다.
  이 예외가 잃는 검사는 **제외된 다른 호스트 서비스의 필수 환경변수·외부 입력 검사**다.
  같은 호스트의 선택 서비스와 도달 종속성의 필수 값은 계속 강제한다. 전체 source-status의
  경고를 선택 쌍의 배포 판정으로 치환하지 않는다.

## 권한과 동일성

서비스 소유권은 레지스트리의 직접 services/init_steps와 정본 family에서 파생한다.
관찰 containers나 target의 원격 URL 관계는 Compose 서비스 실행 권한이 아니다.
unknown/cross-host 종속성은 외부 env_file 읽기 전에 거부한다. depends_on 및
network_mode/ipc/pid의 service 참조를 검사하고 보간 뒤에도 host/subset 폐포를 다시 검사한다.
container namespace, links/external_links/volumes_from/extends, build.additional_contexts는
현재 범위에서 키 존재 자체를 거부한다.

raw 보호 검사·선택 외부 파일 snapshot·resolved/frozen 문서·mutation 재검사는 같은
메모리 scope를 사용한다. 도달 volumes/networks/configs/secrets만 남기고 YAML alias가
전개된 뒤 미사용 x 문서를 제거한다. networks가 생략되거나 빈 목록/매핑이고
network_mode가 없으면 정본 default 네트워크를 보존한다.

전체 원본 Compose bytes/mode와 root 환경파일의 bytes/identity는 그대로 snapshot과
재검증에 남는다. scope는 메모리 필드이며 deploy-status/pinset의 디스크 형식·version은
바꾸지 않는다. 공유 PostgreSQL 변경 금지 등 기존 mutation 권한도 그대로다.

## 설치 전 후보 경계

CLI 전용 설치는 symlink flip 전에 후보 release 자신의 Python `-I` 코드와 root/config,
root/.env, root/docker-compose.yml을 사용한다. 아직 trusted 설치본이 아닌 wheel의
site-packages 경로나 옛 APP의 정본을 family authority로 쓰지 않는다. 현재 설치본의
trusted 판정을 전역 완화하지 않는다. 일반 설치의 기존 전체 config 경로는 보존한다.
실패하면 flip하지 않고 비밀을 포함할 수 있는 resolved 문서나 child stderr를 출력하지 않는다.

## 검증과 남은 수용

집중 회귀는 실제 Docker Compose config와 public capture/frozen 경계, 외부 파일·환경·원문
drift, CLI 전용 child 실패/후보 경로, 설치 패키지 위치의 pre-flip 반례를 검증한다.
Docker 서비스 기동·운영 환경 수정은 하지 않는다. 기존 쌍 재구축·atomic restore·source-status
231건은 별도 통과했다. 이 문서 고정 시점 최종 전체 회귀·두 독립 코드 리뷰·정확 CI·공식 CLI
설치와 실제 배포 수용은 아직 완료로 세지 않는다. 최종 결과는 PR과 감사 원장에 정산한다.
