# 공용 daemon 연결 재사용 채택 설계

## 범위

PR511의 상태 점검/빌드 bytecode를 기준으로 별도 stacked PR을 구성한다.
초기 Common38 lifecycle 후보 채택 뒤 실제 launch의 임시 Instance 회귀가 발견되어,
Common92d 수정 이력 뒤 현재는 Common38+41 통합 ff849ea wholewheel과 공식 webserver alias를 채택한다.
이전 후보와 실제 실패는 아래 이력으로 보존한다.
PR511 병합 뒤 이 PR의 base를 main으로 정산했다. 구현 검증과 운영 완료는 구분한다.

## 정식 wheel과 고정 설치

현재 source는 Common ff849ea8447c6d2cca1ffa653cfcbe2b2ffa6b32의 정식 wholewheel63,151B,
SHA256 1abf0547a7b7fbe76284b69c375e83d953788b83a05fbd6720957a869f865b53이다.
독립 archive 재빌드가 byte동일하며 package17개(Python16/py.typed1), RECORD nonself22행을 확인했다.
기존 daemon CLI/plugin과 새 kor-travel-dagster-webserver가 함께 들어간다.

다음은 이전92d wheel의 출처 이력이다.
Common 92d5cf16ad85c2b6cdd0140226066374ae345917(공식 launch 임시 Instance 수정)의 정식 전체 core wheel을
vendor에 넣었다. 원 wheel45621B와 독립 Git archive의 offline 재빌드가 동일 SHA256
4fa65afd0b8ae90002503fb48d4455fb28ba8574c8cbb529c5add4ff04923527였다.
RECORD nonself18행/package13개 Git bytes/공식 entrypoint와 LICENSE·NOTICE를 대조했다.
vendor/README.md와 common-source.json에 source REV/whole SHA/라이선스·파일 출처를 기록한다.
requirements.in의 kor-travel-common==0.1.0.dev0는 유지하고 Common wholewheel 해시만
갱신한다. 기존71개 잠금 버전·해시·원문 bytes는 그대로 두며 의존성을 다시 해석하지 않는다.
Docker는 개별 wheel을 /tmp/common-wheels에 명시 COPY하고 기존 pip
--no-deps/--require-hashes/--only-binary에 --find-links만 더한다.
임의 source2모듈 복사·수제 metadata·SDK monkeypatch·다른 runtime 의존성 변경은 하지 않는다.

기존 build context content tag는 명시 COPY 파일을 포함해 계산한다. vendor 디렉터리 glob으로
내용이 해시에서 빠지지 않게 개별 파일을 사용한다. 설치된 공식 배포판/console/plugin
metadata와 source map 검증을 새 회귀에 연결한다. artifact 생성 실패는 성공으로 처리하지 않는다.

## daemon 실행 경계

공용 daemon만 기존 storageguard 뒤 exec 명령을 kor-travel-dagster-daemon run -w
원 workspace로 바꾼다. dedicated daemon DSN은 기존 shared DSN과 동일한 보간으로 이 서비스에만
추가한다. 다른 Env/HC 전체 Test/heartbeat300/User/mount/리소스/업무 queue 정책은 유지한다.
공용 webserver는 같은 storageguard 뒤 정식 kor-travel-dagster-webserver alias로 기존 인자를
그대로 전달한다. daemon 전용 DSN은 webserver에 추가하지 않으므로 daemon pool activation0이다.
storage migrate와 기존 liveness-check는 원 명령을 유지한다.

Common wrapper가 원 console entry를 같은 process에서 호출하고 원 argv/SDK 신호·종료를
보존한다. unresolved StringSource/ref의 외부 CS 환경 전달, fork·busy/idle dispose 및
checkout 경쟁 거절은 최종 Common 계약을 따른다. cap2/dequeue30은 또 다른 정책 범위다.

## 검증·운영 제한

source 단계에서는 실제 SDK/PG/Docker/SSH/provider/운영 자격 파일에 접근하지 않는다.
가짜 DBAPI·wheel metadata/source·정적 Compose·guard/argv 회귀와 source 품질을 검사한다.
전체 backend의 Docker 통합 fixture는 root의 허가된 환경/CI 결과로 분리하며, 일반 gateEnv0이
모든 Docker 시험 비활성화를 뜻한다고 가정하지 않는다.
실제 native 이미지·CLI/SIGTERM/PG접속 정리·같은 workload CPU/RSS/queue 지연/신선도는
root의 별도 실제 gate다. 라이브러리 microbench를 운영 전체 CPU 감소로 환산하지 않는다.


## 기존17ff 채택 검증과 적용 gate

다음 항목은 초기 후보 검증 이력이다. 현재92d 갱신 검증·한계는 아래 회귀 정산 절에서 구분한다.

- 기존71개 runtime의 버전과 전체 wheel hash 집합을 그대로 유지한 uv offline compile로
  Common1개만 추가한72개 잠금본을 만들었다. 재생성 명령은 requirements.txt 머리다.
- public Compose 전체 모델 비교에서 공용 host3개의 내용 tag, daemon executable1개,
  원 shared DSN과 동일한 dedicated Env1개 외 차이0이었다. guard/Env의 나머지·HC전체dict·
  원 instance YAML/업무 튜닝은 동일하다. build context는 명시 COPY의5파일로 식별한다.
- focused392PASS/51.93초, Ruff/diff check가 통과했다. 초기 새 시험의 HC script 위치를
  Test마지막 argv로 잘못 읽은1FAIL/137PASS는 보존하고 -c 다음 program을 검사하도록
  test-only 정정했다. 제품 HC는 바꾸지 않았다.
- 별도 빈 core-only venv에 pip25.0.1/no-index/no-deps/require-hashes로 정식 설치하고
  기본 dist-info/source13/console metadata·lazy CLI import(SDK/SQLA/DBdriver import0)를
  확인했다. 잘못된 hash의 force-reinstall은 EXIT1로 거절됐다.
- frontend는 PR511에서 type/lint/35tests/build를 통과한 tree와 동일하다. 이번의 새로운
  frontend 실행·전체 backend/Docker 통합 실행으로 표시하지 않는다. PR513 최종 CI와
  독립2리뷰는 별도다. 로컬 Docker 통합 fixture를 또 실행하지 않았다.
- 실제 Manager daemon native 이미지/원CLI·SIGTERM/외부CS InstanceRef/PG 연결 반환·
  같은 workload CPU/RSS/pool 대기/수집 신선도 gate는 root가 별도로 확인한다.
  Common의 fake·root 격리 SDK/PG 성공과 이 소비자 운영 성공을 혼동하지 않는다.

## CI guard 추출 회귀 정산

최초 최종290f CI는 기존 storage guard 추출기가 공식 wrapper 실행 파일을
인식하지 못해 2FAIL/3049PASS/39SKIP이었다. 실제 Compose의 기동 전제조건은
바뀌지 않았다. 면제 목록을 늘리지 않고, 테스트 추출기의 정확한 실행 파일
목록에 `kor-travel-dagster-daemon`을 추가했다. 비슷한 prefix/suffix 이름은
허용하지 않는 회귀도 추가했다. Docker·Compose·명시5개 이미지 입력은
290f와 byte 동일하므로 이 테스트 정정 때문에 이미지를 재빌드하지 않는다.
이전 CI 실패는 보존한다. 수정 전 local2FAIL4PASS를 재현했고 수정 후 관련7개
모듈377PASS/47.26초·Ruff·diff검사가 통과했다. 새 HEAD의 전체 CI는 별도 판정한다.


## 공식 launch 임시 Instance 회귀 정산

이전17ff는 실제 daemon heartbeat/health를 통과했으나 DefaultRunLauncher의 gRPC
start_run이 같은 프로세스에서 만드는 임시 DagsterInstance.from_ref에 네 번째
engine이 생겨 job launch를 막았다. 원 실패와 이전 성공 범위를 보존하며 heartbeat나
InstanceRef 직렬화만으로 launch 성공이라고 하지 않는다.

현재92d는 최초 primary 세 storage만 QueuePool로 예약한다. pooled primary 연결 상한은12,
유휴 상한6이며, 임시 SDK storage는 원 NullPool로 두고 SDK의 원 수명대로 연결을 닫는다.
전체 daemon 프로세스의 모든 임시 연결까지12개로 제한한다는 뜻은 아니다.
fork/세대/동시성/primary strong registry·정리/pre_ping 보호는 유지한다.

이번 Manager 변경은 vendor 전체 wheel·그 hash/source 기록·Common 잠금 해시 한 개와
그로부터 유도한 공용 host 세 image ref뿐이다. Dockerfile의 명시 wheel 경로는 basename이
같아 그대로 유지한다. 나머지 네 runtime 의존 파일 입력 중 requirements 외 세 파일도
원형이고, cAdvisor30초/14제외·PR512 기능·instance queue 정책은 바꾸지 않는다.
위 cAdvisor30초는 fcc2 핀 갱신 당시 기준이다. main #517 통합 후 현재 source는 CADVISOR_HOUSEKEEPING_INTERVAL 기본15초/env 덮어쓰기·제외14집합이며, Odroid 운영30초는 root의 별도 적용·관측 범위다.
새 wholewheel/lazy import/기존 잠금/Compose wrapper와 작은 stdlib fixture11개 회귀는 통과했다.
실제 SDK/격리 PG의 공식 start_run 성공은 root 실행 evidence로 별도 인계되며,
새 Manager native 빌드·운영 launch/신선도·CPU 재측정과 두 독립 리뷰는 후속이다.


## Common38·41 wholewheel과 webserver alias 후속 준비

최신 main42d884f(PR530 포함)을 PR513에 ordinary merge했고 기존 Common92d wheel과
daemon CLI·host5입력은 그대로 보존했다. 공식 kor-travel-dagster-webserver를
guard 추출기가 인식하도록 exact 이름과 유사 prefix/suffix 음성 회귀만 준비했다.
기존 추출기에서는 새 이름1FAIL/8PASS, 정정 뒤9PASS였다. 실제 Compose 명령은 아직
dagster-webserver이며 구 wheel에는 새 alias가 없어 명령만 먼저 바꾸지 않는다.

최종 Common38+41 wholewheel은 별도 제공 후 기존71개 runtime의 version/hash를 유지하며
소비한다. 두 정식 CLI와 SQLAlchemy pool plugin의 보존을 확인하고 webserver 한 곳의
실행 이름만 바꾼다. instance/queue·daemon HC·다른 서비스 정책 변경이나 운영 실행은
이 준비 작업에 포함하지 않는다. 운영 RC의 queue/NVMe/DB경로/metrics 변경은 별도
feature에 있으므로 최신 main 또는 PR513만으로 운영 RC 전체를 덮어쓰지 않는다.

## 최종 wholewheel·공식 webserver alias 채택

앞 절의 준비 이후 최종 ff849ea wholewheel로 교체했다. 기존71개 runtime의 모든
version/hash는 그대로이고 Common hash1개만 갱신했다. Dockerfile/bytecode helper/
storage-migrate 입력은 불변이며 wheel·requirements 변경을 반영한5입력 content tag는
930a44266e814ab6이다. 공용 host3개 image ref를 함께 정렬했다.

공용 webserver 실행 이름만 공식 alias로 바꾸고 원 guard/후속 argv·Env/HC/네트워크/
mount/리소스를 유지한다. daemon의 기존 pooled CLI와 전용 DSN, storage migrate는 그대로다.
원 실패 이력·단계별 수치는 보존하며 이 소스 채택을 운영 이미지 빌드·배포·실수집·CPU
개선으로 해석하지 않는다. 운영 RC의 별도514/521/522/525 기능도 이 PR로 덮어쓰지 않는다.

최종 wholewheel·alias 단계의 관련4모듈47PASS(3.89초), Ruff/diff PASS다.
기존 loopback검증기가 native 실행파일 이름만 찾던 최초1FAIL/46PASS는 보존했고,
공식 alias의 정확 이름도 인식하도록 test-only 정정했다. loopback 기준은 바꾸지 않았다.
전체 Compose 의미차이는 공용3image refs와 webserver command실행파일1개뿐임을 대조했고,
Common 해시를 이전값으로 역치환하면 requirements.txt 전체bytes가 이전과 일치한다.
실제 SDK/이미지build/liveUI·backend전체/운영은 이번 작성자 검증에서 NOT_RUN이다.
