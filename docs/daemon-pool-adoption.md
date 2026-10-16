# 공용 daemon 연결 재사용 채택 설계

## 범위

PR511의 상태 점검/빌드 bytecode를 기준으로 별도 stacked PR을 구성한다.
Common38 최종 lifecycle fix와 두 독립 closure 전에는 제품 wheel·설정을 넣지 않는다.
이 문서는 구현·운영 완료를 뜻하지 않는다. PR511 병합 뒤 base 정산은 merge 담당이 한다.

## 정식 wheel과 고정 설치

최종 Common source의 전체 core wheel을 정상 빌드하고 두 번의 산출물/source·RECORD·entrypoint를
확인한다. vendor의 개별 wheel과 source REV/whole SHA/라이선스 출처를 기록한다.
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
