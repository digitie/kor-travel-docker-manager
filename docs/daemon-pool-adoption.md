# 공용 daemon 연결 재사용 채택 설계

## 범위

PR511의 상태 점검/빌드 bytecode를 기준으로 별도 stacked PR을 구성한다.
Common38 최종 lifecycle fix/독립 closure를 확인한 뒤 제품 source를 ebd07591dcdd5dc55d0213bef83b091e10ff00d6로 고정했다.
PR511 병합 뒤 이 PR의 base를 main으로 정산했다. 구현 검증과 운영 완료는 구분한다.

## 정식 wheel과 고정 설치

Common final17ff89d2839c8a65fa6d7c2383fef676a86f075a(제품e0c1389)의 정식 전체 core wheel을
vendor에 넣었다. 원 wheel45405B와 독립 Git archive의 offline 재빌드가 동일 SHA256
0bd62cca5b5a2493e532113f0ae603c430a5f56e7d42428005096f0bdc811229였다.
RECORD nonself18행/package13개 Git bytes/공식 entrypoint와 LICENSE·NOTICE를 대조했다.
vendor/README.md와 common-source.json에 source REV/whole SHA/라이선스·파일 출처를 기록한다.
requirements.in에는 kor-travel-common==0.1.0.dev0를 추가하고 기존71개 잠금 버전을
제약으로 유지한 채 --find-links vendor로 해시 잠금본을 재생성한다.
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
공용 이미지의 webserver/storage migrate는 Common 설치 뒤에도 기존 명령을 사용하므로
pool activation0이다. 기존 liveness-check도 원 CLI를 호출한다.

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


## 현재 검증과 적용 gate

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
이전 CI 실패는 보존한다. 수정 전 local2FAIL4PASS를 재현했고 수정 후 관련7개\n모듈377PASS/47.26초·Ruff·diff검사가 통과했다. 새 HEAD의 전체 CI는 별도 판정한다.
