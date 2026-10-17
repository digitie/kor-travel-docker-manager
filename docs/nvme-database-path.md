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
현재 설계 초안이며 격리 회귀와 최종 결과는 후속 커밋에 기록한다.
실제 DB 읽기/생성·설치·운영 실행은 수행하지 않는다.
