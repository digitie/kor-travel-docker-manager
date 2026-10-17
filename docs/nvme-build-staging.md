# NVMe 설치의 임시 빌드 경로

## 설계와 범위

Manager 설치자가 기존 KTDM_PINNED_RUNTIME_STATE_ROOT를 NVMe의 canonical 절대 경로로 설정한다.
project별 state root와 source cache의 기존 규칙을 유지한다. 운영 장치나 mount를 검사하는
새 기동 조건은 추가하지 않는다.

memfd를 지원하는 경우 기존 메모리 descriptor를 그대로 사용한다. 지원하지 않는 경우(AttributeError 또는 ENOSYS)의
private frozen Compose descriptor helper는 호출자가 전달한 state root 아래의 전용
0700 임시 디렉터리에서 0600 파일을 만들고 즉시 unlink한다. 실제 external env_file
snapshot의 memfd-only 보안 계약은 바꾸지 않는다.

Map API/Dagster buildx는 frozen state root 아래 동일한 규칙의 임시 디렉터리를 TMPDIR로
받는다. 기존 DOCKER_CONFIG, DOCKER_HOST, XDG_RUNTIME_DIR와 고정 PATH는 유지한다.
디렉터리를 준비할 수 없으면 빌드를 시작하지 않으며 Python의 자동 /tmp 선택을 쓰지 않는다.
성공과 실패 모두 임시 디렉터리를 정리한다. atomic 파일 교체는 기존 destination parent를 쓴다.

## 검증 및 운영 경계

구현은 frozen state_paths.state_root를 Map builder에 명시 전달한다. 같은 build의 API와
Dagster 두 호출은 하나의 TMPDIR를 공유한다. memfd의 ENOMEM/EPERM 등 다른 오류는
디스크로 우회하지 않는다. fallback helper는 현재 제품 내부 호출자가 없으며 실제 secret
env_file snapshot의 memfd-only 경로는 그대로다.

로컬 검증은 실제 파일 descriptor와 임시 디렉터리의 권한·cleanup·실패를 검사하고 Docker
호출은 대역했다. 신규 격리 테스트 14개와 backend Ruff가 통과했다. parent main의 실제
함수는 명시 임시 경로 두 조건에서 2 RED, 새 함수는 같은 조건에서 2 GREEN이었다.
전체 backend suite, 실제 NVMe 배포, 이미지 빌드, CPU 및 eMMC 쓰기 검증은 수행하지 않았다.

재현 명령은 다음과 같다(프로젝트 의존성이 있는 Python을 사용한다).

    python -B -m pytest --noconftest -q backend/tests/test_nvme_build_staging.py
    ruff check backend

새 테스트는 실제 제품 함수 AST를 실행하여 DB/SDK/Compose 초기화를 피한다. 기존
builder 실패 test 본문도 동일하게 실행했다. 기존 test 모듈을 --noconftest로 직접 실행한
시도는 module-autouse의 own_pinned_pair fixture 부재로 실행하지 못했다. 전역 integration
fixture를 켜지 않았으며 이 시도를 전체 또는 기존 모듈 PASS로 세지 않는다.
초기 새 테스트의 6실패는 interpreter의 memfd attribute 부재를 mock이 고려하지 못한
fixture 오류였고 raising=False로 수정했다. 최초 명령의 registry 경로 전달 오류도 수정했으며
그 실패를 제품 결함으로 판정하지 않는다. CodeGraph 초기화·sync는 로컬에서 완료했다.

이 변경만으로 모든 저장소를 NVMe로 옮기지는 않는다. 설치자가 HOME/state/source/logs/cache/
venv/Node 빌드 경로와 Docker data-root를 NVMe에 배치해야 한다. Manager DB 기본 상대 경로도
설치 소스 위치에 의존하므로 실제 설치 root가 NVMe인지 확인한다. DB 기본값은 변경하지 않는다.


## 독립 리뷰 후 경로 이탈 보완

초기 b631e5a는 fallback mkstemp prefix에 진단용 label을 그대로 넣었다. 절대 경로나
상대 ../ label은 dir 인자를 벗어난 파일을 만들 수 있다는 독립 P2 재현을 수용했다.
현재 내부 호출자는0이고 실제 env_file 경로는 영향이 없지만 helper 자체의 state 한정
계약은 위반했다. memfd의 진단 label은 유지하고 디스크 fallback prefix만 고정 문자열로
바꿨다. 자기 scratch의 절대·상대 경로2회귀는 수정 전2FAIL, 수정 후16PASS에 포함된다.
원 b631e5a와 초기 리뷰는 보존하며 전체 suite/실제 운영 검증은 여전히 미실시다.
