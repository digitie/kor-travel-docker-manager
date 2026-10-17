# NVMe 설치의 임시 빌드 경로

## 설계와 범위

Manager 설치자가 기존 KTDM_PINNED_RUNTIME_STATE_ROOT를 NVMe의 canonical 절대 경로로 설정한다.
project별 state root와 source cache의 기존 규칙을 유지한다. 운영 장치나 mount를 검사하는
새 기동 조건은 추가하지 않는다.

memfd를 지원하는 경우 기존 메모리 descriptor를 그대로 사용한다. 지원하지 않는
private frozen Compose descriptor helper는 호출자가 전달한 state root 아래의 전용
0700 임시 디렉터리에서 0600 파일을 만들고 즉시 unlink한다. 실제 external env_file
snapshot의 memfd-only 보안 계약은 바꾸지 않는다.

Map API/Dagster buildx는 frozen state root 아래 동일한 규칙의 임시 디렉터리를 TMPDIR로
받는다. 기존 DOCKER_CONFIG, DOCKER_HOST, XDG_RUNTIME_DIR와 고정 PATH는 유지한다.
디렉터리를 준비할 수 없으면 빌드를 시작하지 않으며 Python의 자동 /tmp 선택을 쓰지 않는다.
성공과 실패 모두 임시 디렉터리를 정리한다. atomic 파일 교체는 기존 destination parent를 쓴다.

## 검증 및 운영 경계

현재는 설계 초안이며 구현·회귀 결과는 후속 커밋에 기록한다. unit test는 실제 로컬
파일 descriptor와 임시 디렉터리의 권한·cleanup·실패를 검사하고 Docker 호출은 대역한다.
실제 NVMe 배포, 이미지 빌드, CPU 및 eMMC 쓰기 검증은 수행하지 않았다.

이 변경만으로 모든 저장소를 NVMe로 옮기지는 않는다. 설치자가 HOME/state/source/logs/cache/
venv/Node 빌드 경로와 Docker data-root를 NVMe에 배치해야 한다. Manager DB 기본 상대 경로도
설치 소스 위치에 의존하므로 실제 설치 root가 NVMe인지 확인한다. DB 기본값은 변경하지 않는다.
