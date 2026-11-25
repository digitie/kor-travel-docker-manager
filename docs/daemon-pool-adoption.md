# 공용 daemon 연결 재사용 채택 설계

## 범위

PR511의 상태 점검/빌드 bytecode를 기준으로 별도 stacked PR을 구성한다.
초기 Common38 lifecycle 후보 채택 뒤 실제 launch의 임시 Instance 회귀가 발견되어,
Common92d 수정 이력 뒤 현재는 Common38+41 통합 ff849ea wholewheel과 공식 webserver alias를 채택한다.
이전 후보와 실제 실패는 아래 이력으로 보존한다.
PR511 병합 뒤 이 PR의 base를 main으로 정산했다. 구현 검증과 운영 완료는 구분한다.

## 정식 wheel과 고정 설치

(2026-11-24 이후 현재 source는 아래 "Common py 0.1.0.dev2 채택" 절이다. 이 절은 그 전 이력이다.)
당시 source는 Common ff849ea8447c6d2cca1ffa653cfcbe2b2ffa6b32의 정식 wholewheel63,151B,
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

## Weather 주소 설정 전달의 별도 통합 범위

Weather main10b59dc3의 WeatherSettings와 env prefix를 대조해 활성 API와 code-server에
KOR_TRAVEL_WEATHER_VWORLD_API_KEY/KAKAO_REST_API_KEY와
KOR_TRAVEL_WEATHER_ADDRESS_REFRESH_AUTO_ENABLED/BATCH_SIZE/TIMEOUT_SECONDS를 전달한다.
두 key 기본값은 빈 문자열, 나머지는 true/50/5로 Weather와 같다. .env.example에는
실제 값을 넣지 않는다. legacy webserver/daemon·migrate·다른 서비스에는 추가하지 않는다.

기본값/빈값fallback·명시 override·전달 대상·예시6개 정적 보간 회귀는 원Compose에서
5FAIL1PASS를 확인한 뒤 관련5모듈53PASS로 통과했다. 이는 기존 테스트의 보간 도우미와
rawYAML 대조이며 실제 Docker Compose 렌더/운영 provider 호출을 실행한 증거는 아니다.
전체 모델 차이는 두서비스의5키씩10개 추가뿐이며 Common wheel/host이미지 입력은 불변이다.
이 연결은 설정 전달이며 daemon 시작·instigator 활성화·실제 provider 검증을 수행하지 않는다.

별도 광범위 source파생3모듈은 conftest/SDK/network를 차단해126PASS/53deselect/
14setupERROR였다. 기존 own_pinned_pair fixture를 의도적으로 로드하지 않은 준비 한계이며
전체PASS로 세지 않는다. raw로그를 보존했고 전체표준CI/격리fixture 회귀는 root가 정산한다.
env편집 준비의 AssertionError/StopIteration은 파일쓰기 전 검증에서 중단한 것으로,
서비스 줄 시작을 정확히 고쳐 최종 전체모델 차이를 재확인했다.

## 최종 Common38·41 소비 검토 정산

제품bd8과 설명정정c654의 독립2리뷰 및 제품CI2SUCCESS는
[원문·해시·범위](reviews/2026-10-18-common-union-consumer-closure/README.md)에 보존한다.
기존 실패·격리·운영 NOT_RUN 한계를 유지하며 source 통과를 실제 이미지/서비스·성능 수용으로 바꾸지 않는다.

## Common py 0.1.0.dev2 채택과 접속 예산(2026-11-24)

vendor wheel을 Common `f1faf56`(py 0.1.0.dev2, GitHub prerelease `py-v0.1.0.dev2`) 정식 wholewheel(83,112B, SHA256
`aa65a95e…b5c0`)로 바꿨다. dev0(`b4f7f3d`)에서 바로 dev2로 가므로 dev1(`ec5e168`)의 변경도 함께 들어온다. 독립 재빌드(f1faf56 Git
archive, uv 0.11.21, `SOURCE_DATE_EPOCH=1795475640`)가 byte 동일했고 package 21개 Git bytes·RECORD nonself 26행을 대조했다.
기존 71개 runtime 잠금(버전·해시)은 그대로이고 Common 한 줄(버전·해시)과 Dockerfile의 wheel 이름만 바뀌었다. 5입력 내용 태그는
`fc52552976c61b10`이다.

| | dev0(b4f7f3d, 지금 운영) | dev2(f1faf56, 이 채택) |
|---|---|---|
| daemon primary engine(run·event·schedule) 각 pool | 2+2, 대기 1초, 수명 잠금 **안**에서 대기 | 4+4, 대기 10초, 잠금 **밖**에서 대기 |
| daemon primary 연결 상한 / 유휴 보관 | 12 / 6 | 24 / 12 (env 상한 3×10=30) |
| webserver storage | pre-ping 없음 | `KOR_TRAVEL_DAGSTER_WEBSERVER_POOL_PRE_PING=1`(Manager 기본 켬) |

**렌더.** daemon에만 `KOR_TRAVEL_DAGSTER_DAEMON_POOL_SIZE`·`_MAX_OVERFLOW`·`_TIMEOUT_SECONDS`(`-` 보간, 기본 4·4·10 = Common 기본),
webserver에만 `KOR_TRAVEL_DAGSTER_WEBSERVER_POOL_PRE_PING`(기본 1, 소유자 방향)을 싣는다. 이름은 wheel 원문의 상수와 대조한다
(`test_the_rendered_env_names_are_the_ones_the_vendored_wheel_reads`). 빈 값·범위 밖은 Common이 종료 코드 2로 거절한다.

**접속 예산 — 공용 role `kor_travel_dagster_shared_app` `CONNECTION LIMIT 60`(2026-11-25 소유자 결정, 옛 45).** 이 role 하나를
daemon·webserver·run worker(code-server의 자식)·daemon의 임시 start_run instance가 함께 쓴다. `max_connections`(100)보다 이 한도가 먼저
걸린다. 한도는 root `.env` `KTDM_DAGSTER_SHARED_ROLE_CONNECTION_LIMIT`(비우면 60, 두 plane 공통)이고 db-init
(`kor-travel-shared-db-init-dagster`)이 실행될 때마다 그 값으로 `ALTER ROLE`한다 — 손으로 한 `ALTER ROLE`은 다음 실행이 되돌린다.
db-init은 양의 정수가 아니거나 instance의 비-superuser 자리(`max_connections − superuser_reserved_connections − reserved_connections`,
오늘 두 호스트 97) 이상인 값을 role을 바꾸기 전에 거부한다.

| 몫 | 상주(유휴) | 바쁜 시간 동시(가정) |
|---|---|---|
| (a) daemon primary QueuePool | dev0 6 → **dev2 12** | dev0 12 → **dev2 24**(상한) |
| (b) webserver(`optimize_for_webserver` pool_size 1·overflow 20 ×3 engine) | 3 | 6(이론 상한 63 — 동시 요청 수에 묶임) |
| (c) run worker(NullPool — 쓰는 동안만) | 0 | n150 전역 6 run × 2 ≈ 12, odroid 5 run ≈ 10 |
| (d) daemon 임시 start_run instance(NullPool) | 0 | 3 |
| (e) code-server의 sensor·schedule 평가가 `context.instance`를 쓸 때(NullPool) | 0 | 수 개 — 세지 않음 |
| (f) multiprocess executor의 step 하위 프로세스 | 0 | run당 2를 넘을 수 있음 — 세지 않음 |

- 상주: dev0 약 9(2026-11-24 n150 실측 이 role 연결 8, 전부 idle) → dev2 약 15. 격리 PG 실측(아래) daemon+webserver 상주 9는 빈
  workspace라 primary pool이 덜 찬 값이다.
- 바쁜 시간 동시 최악: (a)24 + (b)6 + (c)12 + (d)3 ≈ **45**(n150; odroid는 (c)10이라 ≈43) — 옛 한도 45에 닿았다. (e)(f)를 세면
  더 늘 수 있다. 넘치면 PostgreSQL이 새 접속을 `too many connections for role`로 거절하고 그때 접속하던 쪽(run worker면 run 실패)이
  맞는다. daemon pool이 24를 모두 쓰는 것은 sensor·schedule worker(각 4)와 run coordinator·backfill·heartbeat가 한 engine에 동시에
  몰릴 때뿐이라 흔하지 않지만, 0이라고 할 수 없다.
- 소유자 결정(2026-11-25): pool은 Common 기본(24)을 그대로 두고 **role 한도를 60으로** 올린다(②). 최악 45는 60의 75%, odroid 43은
  72%다 — 아래 수집 경보의 주의선(80%) 밑이고, (e)(f)가 몇 개 더해지면 주의선에 닿는다. 그래도 넘치는 징후가 보이면
  ① `KOR_TRAVEL_DAGSTER_DAEMON_POOL_SIZE=3`·`_MAX_OVERFLOW=3`(상한 18)이 남은 수단이다.
- **관측**: 수집 경보(plane watchdog) 규칙 `dagster_role_connections` — 이 role의 client 연결 수 / 한도가 최근 3회 평가(약 4~6분) 연속
  80% 이상이면 주의, 95% 이상이면 위험. `/metrics`에 `ktdm_dagster_role_connections`·`_connection_limit`·`_connection_ratio{role}`와
  instance 전체 `ktdm_shared_postgres_client_connections`·`ktdm_shared_postgres_connection_capacity`
  ([plane-watchdog.md](plane-watchdog.md) §3.3).

**공용 instance 여유 — 한도는 칸막이이지 예약이 아니다(2026-11-25 두 호스트 읽기 전용 실측).** `pg_settings`·`pg_roles.rolconnlimit`·
`pg_database.datconnlimit`·`pg_stat_activity`(client backend). 두 instance 모두 PostgreSQL 18.6, `max_connections` 100,
`superuser_reserved_connections` 3, `reserved_connections` 0 → 비-superuser 자리 **97**.

| 호스트 | 유한 한도 | 유한 한도 합 / 자리 | 한도 없는 role(지금 연결) | 지금 client 연결 | Dagster가 60을 다 쓸 때 남는 자리 |
|---|---|---|---|---|---|
| n150 | role `kor_travel_dagster_shared_app` 45 → **60**(지금 13) · DB `kor_travel_map` `datconnlimit` 55(재구축이 `floor(0.57 × 97)`로 건다, 지금 17) | 100 → **115 / 97 — 초과(18)** | `pinvi_application_runtime` 3, `kor_travel_concierge_app` 2, 앱 role 넷(geo·weather·transport·map_dagster) 0 | 36(superuser 2 포함) | 37 — Map 지금 17 + PinVi·Concierge 5 = 22를 담는다 |
| odroid | role `kor_travel_dagster_shared_app` 45 → **60**(지금 9) | 45 → **60 / 97** — 초과 아님 | `kor_travel_weather_app` 8, `kor_travel_geo_app` 7, `kor_travel_transport_app` 4 | 30(superuser 3 포함) | 37 — geo·weather·transport 지금 19를 담는다 |

role별 수는 모든 backend 종류를 센 같은 질의의 다른 줄이라 client 연결 합과 1 어긋난다(n150 37 vs 36, odroid 31 vs 30 — 조사 질의 자신의
연결·표본 시각 차이). 2026-11-25 2차 표본: n150 이 role 13·client 26, odroid 9·30. 2026-11-25 n150 사전 확인 A(아래)는 소비자 다섯
컨테이너 모두 `same`이었다.

> ⚠️ **n150은 유한 한도의 합이 자리를 넘는다(115 > 97, 옛 45로도 100 > 97).** Dagster role 60과 Map DB 55가 **동시에** 다 차면
> instance 자리가 먼저 끝나고, 그때부터는 한도와 무관하게 비-superuser의 새 접속이 모두 `remaining connection slots are reserved`로
> 거절된다(PinVi·Concierge 포함 — 이 둘은 한도가 없어 보장된 몫도 없다). 두 한도는 각자 "한 테넌트가 instance를 다 먹지 못하게"
> 하는 칸막이이지 서로의 몫을 예약하지 않는다. 지금 실사용은 합 36으로 여유가 크고, 두 최악(Dagster 45·Map 동시 run 6)이 한 시각에
> 겹칠 일은 드물다고 보고 받아들인다. 위험해지는 신호는 `ktdm_shared_postgres_client_connections`가 자리 97에 다가가는 것이다 — 그때는
> Map DB 한도(0.57)나 Dagster role 한도를 낮추거나 `max_connections`를 올린다(`max_connections`는 PG 재시작이 필요하다).
> odroid는 유한 한도가 Dagster 하나라 합 초과는 없지만, 한도 없는 세 앱 role의 합이 37을 넘으면 같은 일이 생긴다(지금 19).

**n150 적용 경로(설치 뒤 한 번, 서비스 무중단).** n150은 `rehearsal/rebuildable`이라 `ktdctl ensure dagster`가 거부되고(production ensure
금지), plane 수렴(`dagster-plane converge`·pinned 재구축)은 storage migrate만 돌리고 db-init은 돌리지 않는다. 그래서 설치된 release의
compose로 db-init one-shot을 lock G 아래서 직접 돌린다 — `scripts/dagster-shared-cutover.sh`의 G3-a 단계(30 → 45 때)와 같은 명령이다.

```bash
# 1) 이 PR 이상의 Manager release 설치(trusted installer) — backend 재기동으로 감시 규칙도 함께 들어온다.

# 2) 사전 확인 A — db-init은 role 비밀번호도 root `.env` 값으로 다시 건다. 떠 있는 소비자(daemon·webserver·code-server)의 URL
#    비밀번호와 같아야 무중단이다. 값은 출력하지 않고 해시만 비교한다(`bash -x` 금지). 모든 줄이 "same"이어야 한다.
sudo bash -s <<'EOF'
want=$(printf %s "$(sed -n 's/^KOR_TRAVEL_DAGSTER_SHARED_APP_PASSWORD=//p' /opt/kor-travel-docker-manager/.env)" | sha256sum)
for c in $(docker ps --format '{{.Names}}'); do
  got=$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$c" \
    | sed -n 's#^[A-Z_]*=postgresql[^:]*://kor_travel_dagster_shared_app:\([^@]*\)@.*#\1#p' | sort -u)
  [ -n "$got" ] || continue
  if [ "$(printf %s "$got" | sha256sum)" = "$want" ]; then echo "$c same"; else echo "$c DIFFERENT"; fi
done
EOF
#    줄 수가 소비자 수와 같아야 한다(2026-11-25 n150: daemon·webserver·Map·PinVi·Concierge code-server 5) — 0줄이면 확인이 된 것이 아니다.
#    DIFFERENT가 하나라도 있으면 멈춘다 — `.env`가 그 컨테이너 생성 뒤에 바뀐 것이다. 이대로 돌리면 그 소비자의 새 접속이 인증에 실패한다.
#    (`.env` 값이 따옴표·`export`와 함께 적혀 있어도 DIFFERENT가 난다 — 멈추는 쪽으로 틀린다.)
#    (daemon·webserver는 `ktdctl dagster-plane converge --dry-run`의 outcome `current`로도 같은 것을 본다.)
# 3) 사전 확인 B — lock G 파일이 Manager가 만든 모양(root·0600·링크 1)인가. 없으면 flock이 0644로 만들어 이후 Manager 변경이 모두
#    "lock is unsafe"로 거부되므로, 이 값이 아니면 돌리지 않는다.
sudo stat -c '%u:%a:%h' /run/lock/kor-travel-docker-manager/global-mutation.lock   # → 0:600:1

# 4) role 한도 적용: 멱등 ALTER ROLE(CONNECT 재부여도 멱등). 종료 코드 75는 다른 Manager 변경이 lock을 쥐고 있다는 뜻이다.
sudo flock -n -E 75 /run/lock/kor-travel-docker-manager/global-mutation.lock \
  docker compose -p kor-travel-docker-manager --project-directory /opt/kor-travel-docker-manager \
    -f /opt/kor-travel-docker-manager/docker-compose.yml --env-file /opt/kor-travel-docker-manager/.env \
    run -T --rm --no-deps kor-travel-shared-db-init-dagster
#    출력 중: "role kor_travel_dagster_shared_app connection limit 60 (non-superuser capacity 97)"(뒤에 REVOKE·GRANT 줄이 이어진다)
# 5) 확인(읽기 전용)
sudo docker exec -u postgres kor-travel-shared-postgres sh -c \
  'psql -X -qAt -h /var/run/postgresql -p 11000 -U "$POSTGRES_USER" -d postgres -c "SELECT rolconnlimit FROM pg_roles WHERE rolname = '\''kor_travel_dagster_shared_app'\''"'
#    → 60
```

- **무중단인 이유(사전 확인 A가 전제)**: `ALTER ROLE … CONNECTION LIMIT`은 새 접속 시도에만 걸린다 — 열린 세션은 그대로이고 컨테이너
  재생성·재시작이 없다(격리 PG18에서 열린 세션이 재실행 뒤에도 살아 있고 같은 비밀번호로 새 로그인이 되는 것을 적대 리뷰가 확인).
  비밀번호를 같은 값으로 다시 거는 것도 영향이 없다 — 값이 다르면(사전 확인 A의 DIFFERENT) 무중단이 아니다. `REVOKE/GRANT CONNECT`는
  이미 그 상태라 바뀌지 않는다.
- **lock**: Manager가 쓰는 host 변경 lock G다. 75로 끝나면 다른 Manager 변경(배포·재구축·자동 재시작)이 도는 중이다 — 끝난 뒤 다시 한다.
- **확인 둘 더**: 수집 경보 화면의 점검 항목 "공용 Dagster role 연결 수"가 `N/60(…)`으로 보이고, `/metrics`의
  `ktdm_dagster_role_connection_limit{role="kor_travel_dagster_shared_app"}`이 60이다.
- **odroid(Codex)**: plane이 Manager compose가 아니라 host compose(`/opt/kor-travel-odroid/compose.yml`)이고 그 db-init에 리터럴 45가
  들어 있다(2026-11-25 읽기 전용 확인) — 그 사본의 두 문장을 60으로 바꾸거나 이 정의로 갈아 끼운 뒤 같은 one-shot을 그 project에서 돌린다.
  고치지 않고 손으로 `ALTER ROLE`하면 다음 db-init이 45로 되돌린다.

**실제 SDK 확인(WSL 격리 PG, 운영 무관).** 새 이미지로 storage migrate → daemon(4·4·10) → webserver(pre-ping 1)를 띄워 daemon stderr
`daemon 연결 풀: primary engine 3개 각 pool_size=4 max_overflow=4 pool_timeout=10s`, webserver stderr `webserver storage pre-ping: 켜짐`,
GraphQL `runsOrError`·daemon 6종 healthy를 봤다. PG 컨테이너를 재시작(유휴 연결 절단)한 직후 첫 GraphQL 요청이 성공했고 daemon은
재시도 경고 없이 70초 이상 돌았다. 대조군(`PRE_PING=0`, 같은 이미지·절차)은 재시작 직후 첫 요청이 `psycopg2.OperationalError: server
closed the connection unexpectedly`로 실패하고 두 번째부터 성공했다 — PG 재시작·idle 절단 뒤 첫 요청 실패를 pre-ping이 없앤다. 잘못된 값(`POOL_SIZE=11`·빈 값, `PRE_PING=yes`)은 각각 종료 코드 2였다.
운영 접속 수·tick 지연·수집 신선도는 배포 뒤 관측이다.

**빌드 위치.** 이 이미지는 운영 박스에서 빌드하지 않는다(ADR-67) — WSL에서 `scripts/dagster_host_image.py build`로 만들어 옮긴다.
