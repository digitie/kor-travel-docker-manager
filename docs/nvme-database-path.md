# NVMe 설치의 SQLite 경로

## 설계

KTDM_DATABASE_PATH를 프로세스 환경에 설정하면 절대 SQLite 파일 경로를 사용한다.
키가 없으면 database.py의 기존 __file__ 기준 ../../../../pinvi_metrics.db 계산을 그대로 유지한다.
키가 있는데 비어 있거나 상대 경로이면 시작을 거절한다. schema/API/권한/DB 기본값은 바꾸지 않는다.

공식 backend unit은 EnvironmentFile로 .env를 주입하지 않는다. main.py의 load_dotenv는
일부 API/DB import 뒤에 있으므로 루트 .env에 이 키만 추가하는 것으로는 충분하지 않다.
backend의 systemd Environment 단일키와 CLI의 프로세스 환경에 같은 경로를 명시한다.
임의 import 순서 변경이나 전체 .env의 systemd 주입은 하지 않는다.

## 운영 경계

기존 DB 본체와 존재하는 WAL/SHM 또는 journal의 일관된 보존·복원은 운영자가 별도로 수행한다.
설치 origin/venv/PYTHONPATH/symlink에 따라 달라질 수 있는 기본 경로를 추측하지 않는다.
이 source 변경은 기존 파일 이동·schema 변경·기동 checker를 추가하지 않는다.
격리 회귀17개/backend Ruff/diff 검사가 통과했다. 원 main의 실제 경로 산술은
명시경로 및 상대경로거절2조건에서2RED였고 새 함수는2GREEN이었다.
테스트는 제품의 경로 선택·DB_PATH·DATABASE_URL AST와 실제 engine 할당문을 실행하되
create_engine은 대역하므로 DB 모듈 import/engine 생성/DB 연결·파일 생성은0이다.
전체 suite/실제 DB snapshot·이전/ARM64 설치·운영 실행은 수행하지 않았다.


## 프로세스 환경 설정 예시

다음은 운영자가 적용할 설정 형식이며 이 PR에서 실행하지 않았다. /srv/ktdm-state는
단순 디렉터리 이름이 아니라 실제 NVMe에 놓였는지 운영자가 확인해야 한다.

backend systemd drop-in의 단일키:

    [Service]
    Environment="KTDM_DATABASE_PATH=/srv/ktdm-state/pinvi_metrics.db"

CLI도 같은 프로세스 환경으로 실행한다:

    KTDM_DATABASE_PATH=/srv/ktdm-state/pinvi_metrics.db ktdctl <기존 명령>

전체 .env를 EnvironmentFile에 연결하거나 모듈 import 순서를 바꾸지 않는다. 늦게 읽히는
.env 파일에만 키를 놓고 적용되었다고 판단하지 않는다. 환경변수는 DB 초기화 시점에
선택되므로 실행 중 값 변경은 기존 engine을 다른 파일로 옮기지 않는다.
빈 값은 unset과 다르다. 기본값을 계속 쓰려면 키 자체를 주지 않는다.

## 보존과 로그

DB_PATH는 논리 경로를 abspath로 정규화하며 symlink를 resolve하거나 NVMe 장치임을 검사하지
않는다. 따라서 운영자는 최종 파일과 parent의 realpath/mount, backend 계정 권한을 따로
확인한다. 기존 DB 및 WAL/SHM/journal은 backend 정지나 일관된 backup 절차로 보존한 후
정확한 목적지에 복원한다. 실행 중 DB 본체와 WAL을 서로 다른 시점에 일반 copy한 것을
일관된 snapshot으로 간주하지 않는다. 파일명만 바꾸는 이 기능은 schema나 데이터 이관이 아니다.

main.py95–99의 로그 산술은 source import origin이
/opt/kor-travel-docker-manager/backend/src/kor_travel_docker_manager/main.py일 때
/opt/kor-travel-docker-manager/backend/logs를 선택한다. APP alias가 NVMe release를 가리키면
그 경로는 NVMe를 통과한다. 실제 import origin을 root가 확인할 필요는 있지만,
이번 변경에는 로그 제품 설정을 추가하지 않았다.
