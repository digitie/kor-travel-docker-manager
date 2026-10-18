# Manager513 immutable FULL independent Backend/packaging review

- Reviewer: /root/review_finish_b. 새 peer 원문·모형 미열람, 저자 PASS 재인용으로 판정하지 않았다.
- Base: 42d884f7dcce9ccd7d8ac3d6778f2fc119847fed
- Head: bd8adfe22db557e5713ff4388a9030a98f7f8519
- Head tree: bc24e823f44f6b83a38fa6b4cd084eaac93389eb
- Base..head binary diff SHA256: 39f9aac152dd4cf3fcc26a20c86cc172d9376c070462e7ff3b4a50ecdc8ff638
- 범위: 16개 변경 파일 전체와 연결된 runtime_topology/Compose env snapshot/공식 Common wheel metadata·source.
- own fixture 생성 관측 UTC: 2026-10-18T06:02:16.282992+00:00; 원문 고정 UTC: 2026-10-18T06:03:18.818832+00:00. 실제 요청 수신 UTC는 대화에 없으므로 추정하지 않는다.
- Verdict: **PASS_SOURCE_WITH_NONBLOCKING_P3**. OPEN P0=0 / P1=0 / P2=0 / P3=1.
- 이는 실제 native image/ARM/PG/job launch/운영 배포/성능 수용 PASS가 아니다. 기존 root 위임에 새 승인 조건을 추가하지 않는다.

## 독립 finding

### M513-B-P3-01 — 예시 주석의 cAdvisor 기본 주기가 현재 canonical과 불일치

위치: .env.example:222. “cAdvisor 수집 주기는 docker-compose.yml의 30초 설정을 사용한다.”
반례: 같은 head docker-compose.yml:2449는 housekeeping_interval을 CADVISOR_HOUSEKEEPING_INTERVAL의 기본15s로 선언한다.
환경 override 없는 새 설치에서 예시대로 30초가 된다고 읽으면 실제15초와 다르다.
제품 YAML은 base/main517과 동일하고 운영에 30초를 적용했는지는 이 리뷰에서 관찰하지 않았다.
따라서 잘못된 문서 안내 P3이며 제품 blocker P0–P2로 확대하지 않는다.
권고: 예시 주석만 canonical 기본15초/env override로 정산. source/runtime cadence 변경은 필요 없다.
root/저자에게 위치와 재현을 직접 통보했다. 이 원 head와 finding은 보존한다.

## 실제 직접 검증

1. applicable AGENTS/CLAUDE/SKILL/architecture/ADR50/task·adoption·vendor 문서와 전체 Git delta를 읽었다. Linux/WSL에서 Git object를 읽고 own archive만 만들었다. private runbook/env/auth/peer archive는 읽지 않았다.
2. 정적 whole Compose 모델을 독립 역복원했다. 허용 차이는 3host image refs, daemon/webserver 각 command executable, daemon dedicated DSN1개, 활성 Weather API/CS 각5env뿐(총16 leaf). 이를 원값으로 돌리면 전체 base 모델과 동일했다. 나머지 service/HC/User/port/mount/network/resources/restart·storageguard·queue/policy는 변경0이다.
3. dedicated daemon DSN은 원 shared DSN과 동일하고 daemon 한 곳만 받는다. 원 shell guard와 argv 순서를 보존한다. webserver alias에도 같은 guard가 앞서며 dedicated daemon DSN은 없다. storage migrate/liveness native 명령·daemon HC360/start_interval45/timeout60/retries2/start90는 그대로다.
4. 실제 runtime_topology.derive_shared_dagster_plane(newmodel)를 own archive에서 호출해 공식 alias 두 개를 원 공용 plane slot으로 인식함을 검증했다. 새 process/service/container를 만드는 제품 코드는 없다. 이 소스 변경 자체가 daemon을 시작하지 않으며 daemon OFF 운영 정책을 바꾸지 않는다.
5. requirements.txt 전체를 별도 strict parser로 읽어 base71개 전부의 version/hash 집합 동일, 추가 Common1개만 wholewheel SHA로 고정됨을 확인했다. Dockerfile의 no-deps/require-hashes/only-binary/pip check 순서와 원 pinned Python3.12 base/UID10001을 읽었다. requirements comment/annotation churn을 의존 변경으로 세지 않았다.
6. vendor wheel 63,151B SHA256 1abf0547a7b7fbe76284b69c375e83d953788b83a05fbd6720957a869f865b53. 제공 Common38 union artifact와 whole bytes 동일; package17개를 실제 ff849ea Git source와 각각 byte 비교했다. 제품 회귀로 RECORD nonself22행/원 LICENSE·NOTICE/두 console entrypoint/SQLAlchemy plugin을 검증했다. Common source를 다시 구현한 mock은 쓰지 않았다.
7. wheel METADATA를 실제 packaging.Requirement로 읽어 Linux/aarch64/Python3.12의 daemon-pool 및 dagster-webserver extra 요구6개가 frozen runtime map에 모두 있고 버전 제약을 만족함을 확인했다. core-only wheel의 SDK lazy import를 실제 zipimport로 확인했다. 신규 image 전체 pip check/ARM native dependency 설치를 수행한 것으로 표시하지 않는다.
8. 명시5입력의 Linux sha256sum listing 형식을 독립 계산해 content tag930a44266e814ab6와 세 image refs가 일치함을 확인했다. Dockerfile/bytecode helper/storage migrate의 변경 범위를 Git로 확인했다.
9. own sterile 제품 회귀: test_daemon_pool_adoption, test_dagster_daemon_storage_guard, test_weather_address_refresh_compose, test_dagster_host_bytecode의 **35 PASS / 2.46초**. 실제 guard source는 Postgres fake를 허용하고 SQLite fake를 거부하며 prefix/suffix 유사 CLI를 거부한다. 실제 stdlib 대신 자기 작은 fixture에서 checked-hash O0/외부 symlink/compile error를 검사했다.
10. vendored wheel 배포판 entrypoint를 실제로 로드해 SDK1.13.24/PG0.29.24/SQLA2.0.54 환경에서 두 정식 console의 --help를 각각 실행, exit0/Usage를 확인했다. daemon run, web listener, 실제 worker·DB/provider 접속은 실행하지 않았다.
11. 활성 Weather 두 서비스5env의 빈 key/true/50/5와 명시 override·비대상 서비스 미전달을 회귀에서 검증했다. source env snapshot이 .env/process/override를 읽고 native Compose로 전달하는 기존 경로를 읽었다. 실제 비밀값·프로덕션 Compose 렌더는 관찰하지 않았다.

## 별도 RC 보존 독립 대조

Root 지정 RC f3680ca1083a86f2abb6c2ae46ea9328721eae9b의 부모가 c7d6c4671c620c0bc901a324baaff9ddfc6513fc + PR513 head임을 실제 Git로 확인했다.
- PR513 명시5build입력은 RC와 byte 동일.
- c7의 compose_service.py(521), database.py(522), metrics_collector.py(525), config/dagster-shared/dagster.yaml(514)은 RC에서 각 byte 동일.
- RC whole Compose의 c7 대비 변경은 3host images/webserver alias/Weather10env뿐이고 각 새 값은 PR513과 동일. c7에 이미 있던 daemon wrapper/전용Env·RC instance digest/env는 유지된다.
- prometheus_targets.py/config/prometheus/prometheus.yml은 base/main42와 byte 동일. 기존 RC 제품을 main 전체 재설치로 덮는 방식과 구분한다.
- backend/프런트 실제 서비스나 current installed RC metadata를 읽지 않았다. root 보고68P/1deselect와 환경실패는 저자/운영자 별도 증거이며 내 35P에 합산하지 않았다.

## own 격리·실패·미실행 한계

Own scratch: /home/digitie/dev/manager513-popper-ucvo5i0i. Python3.11.15 SDK1.13.24/dagster-postgres0.29.24/SQLAlchemy2.0.54/PyYAML6.0.3/pytest9.1.1의 기존 own read-only venv 사용. 실제 Docker base는 Python3.12이므로 이 차이를 숨기지 않는다.
- pytest는 --noconftest -c /dev/null -p no:cacheprovider, plugin auto load OFF, HOME/TMPDIR own, env allowlist로 실행했다. backend 기본 conftest/Docker/실제 .env는 로드하지 않았다.
- 최초 Popen 시험은 WSL wrapper가 먼저 종료되어 로그0/child 종료됨으로 NOT_COMPLETED, PASS로 세지 않았다. 그 후 bounded 동기 실행으로 35P를 직접 확인했다.
- own Git 검증 첫 시도는 Common source 경로를 잘못 src/로 지정해 exit128; 실제 packages/py/kor-travel-common/src로 정정했다.
- 첫 RC 비교는 main 기준16변경과 c7 기준14변경이 같다고 잘못 가정해 assertion 실패했다. c7에 기존 daemon pool/전용DSN/RC Env가 이미 있다는 Git 차이를 확인한 후, 허용 변경14개와 새 값·기존 나머지 전체 보존으로 검사해 PASS했다. 진단 스크립트의 중간 JSON은 최종 근거가 아니며 최종 own-static-r4.log와 own-static.safe.json만 최종 검증이다.
- 도구 오케스트레이션 JS TextEncoder 부재/quoting/template 오류는 shell 또는 제품 실행 이전 준비 실패다. 초반 요구사항 원문 출력이 길어 truncate되어 parsed map과 좁은 diff를 재확인했다.
- 전체 backend/frontend suite/CI 재조회, 실제 Docker build/pip image설치/운영PG/SDK daemon/job/RPC/provider/SSH/browser/수집 CPU·메모리·신선도는 **NOT_RUN**.
- Common ff849 independent report0a4339...의 이전68P는 현재 consumer35P와 분리한다. 같은 wheel bytes/provenance는 직접 확인했다.
- 원repo/원WT/refs/index/source 수정0, 새 gate/권한/정책 추가0. 새 peer 결과나 author 모델·결론 원문을 독립 판정 근거로 읽지 않았다.
- 운영 수용·daemon OFF는 root 영역이며 이 source review는 새 승인을 요구하거나 기존 root 위임을 철회하지 않는다.

## 변경 파일 고정 정보

[
  {
    "path": ".env.example",
    "blob": "4b0bbfeca96e259784451f48d5eccb827744b84b",
    "sha256": "4b7929dfe5fd63b6bdb7619fa8b4c9dd5628b5c021136c9fe7b7fd9bd2729e17"
  },
  {
    "path": "backend/tests/test_daemon_pool_adoption.py",
    "blob": "3aa24e8cb843f954a06ac470143ba876d53ea5dc",
    "sha256": "c4729e169487f655ecbcd4508ab6879897befc97f604f10ea19fd21d8b64b522"
  },
  {
    "path": "backend/tests/test_dagster_daemon_storage_guard.py",
    "blob": "965d41d347ecce0dfb0e282286c05470789d2164",
    "sha256": "6a819b1a8d6de030592984a20306fe7c6f35fed9ec7ff67311b9ff3d1ba0312a"
  },
  {
    "path": "backend/tests/test_dagster_shared_config.py",
    "blob": "79b39b884d7f32b7f21d4835be058b5aec547fdb",
    "sha256": "6b9b429132d166f1f38303f1c752e37be7b68f93a31ca68f549a262e5f6db4e8"
  },
  {
    "path": "backend/tests/test_weather_address_refresh_compose.py",
    "blob": "1593aa6843c61eade22223d98bd2dde70af89360",
    "sha256": "348fc3760e5c7367450924fa733aaebff6572ee717a0a5a6b55ef6b96e633daf"
  },
  {
    "path": "docker-compose.yml",
    "blob": "77a6c76a9f1968a09e5cdbc7043f2163d7b77ccb",
    "sha256": "fc65cf20bf73b2d5e8fb7a624a9e78a8fad7f24fd9eb8e99188022f6a99fa101"
  },
  {
    "path": "docker/dagster-host/Dockerfile",
    "blob": "fb280c02bed3ba2ab5dc90affe29ec0e75f1f445",
    "sha256": "92788eb6c25193281f2947ec2422d9fa1083a3d4b833be87db4904766fe81693"
  },
  {
    "path": "docker/dagster-host/requirements.in",
    "blob": "ad3ecef03bbffd57a1513c3c6622e7d5821a9c65",
    "sha256": "3bf852973a0af45890f54216d061c293ad782532f52dad50f1eec87c548c059d"
  },
  {
    "path": "docker/dagster-host/requirements.txt",
    "blob": "eff78c16b8539bc17ca5f34460d08c40721a505a",
    "sha256": "4d460cdc4fbd1080de2fcb829d2c1cf998b1eddbc612e36fdd9c6d66a76d43e0"
  },
  {
    "path": "docker/dagster-host/vendor/README.md",
    "blob": "6188d79f2e0ce4c8e1fd012a0593306f5bca0ef7",
    "sha256": "698865a9727284af3c3f205b022851a68f6c7610dd4dff2e546ee3ddb928e8ba"
  },
  {
    "path": "docker/dagster-host/vendor/common-source.json",
    "blob": "2fb07fc5b953a042d9f66272193501db14a97bf7",
    "sha256": "37c33e2fbdcae7c0f44b503dbe5699f8c58592281f5c1e5d38640c2ec20e1ddd"
  },
  {
    "path": "docker/dagster-host/vendor/kor_travel_common-0.1.0.dev0-py3-none-any.whl",
    "blob": "08709de41809d99adb990c28a5deb8b20aaf82df",
    "sha256": "1abf0547a7b7fbe76284b69c375e83d953788b83a05fbd6720957a869f865b53"
  },
  {
    "path": "docs/cadvisor-observer-budget.md",
    "blob": "643b2bb2aed6a025478b923f8424a6a39530674d",
    "sha256": "bc682f0b60d13a254ea01b86e5f289f10769e1412bc998e59d84c67c3f4bff0c"
  },
  {
    "path": "docs/daemon-pool-adoption.md",
    "blob": "554e4db9c0218762127a24aeb3d138fb033bdd2d",
    "sha256": "690f9419cc92aa504dfe31f3d0791d7dae9f564c38098c5cf16160127e821c8a"
  },
  {
    "path": "docs/journal.md",
    "blob": "607c0d30ead108546fe31ffaefb1011940eae5a3",
    "sha256": "0b80e13ddd2a9d3182cfad057b9d972a35aebbdf197ba68f32a43e0eb6d5114a"
  },
  {
    "path": "docs/tasks.md",
    "blob": "8309e27d9440a67808b00dd9ee9c850f8ea9e979",
    "sha256": "7cfe53456ff3bdb8064d6df9643964732fe9051ffc98e3c51ebd24cf35523244"
  }
]

## 최종 own static safe projection

{
  "whole_compose_permitted_changes": [
    [
      "services",
      "kor-travel-dagster-daemon",
      "command"
    ],
    [
      "services",
      "kor-travel-dagster-daemon",
      "environment",
      "KOR_TRAVEL_DAGSTER_DAEMON_POSTGRES_URL"
    ],
    [
      "services",
      "kor-travel-dagster-daemon",
      "image"
    ],
    [
      "services",
      "kor-travel-dagster-storage-migrate",
      "image"
    ],
    [
      "services",
      "kor-travel-dagster-webserver",
      "command"
    ],
    [
      "services",
      "kor-travel-dagster-webserver",
      "image"
    ],
    [
      "services",
      "kor-travel-weather-api",
      "environment",
      "KOR_TRAVEL_WEATHER_ADDRESS_REFRESH_AUTO_ENABLED"
    ],
    [
      "services",
      "kor-travel-weather-api",
      "environment",
      "KOR_TRAVEL_WEATHER_ADDRESS_REFRESH_BATCH_SIZE"
    ],
    [
      "services",
      "kor-travel-weather-api",
      "environment",
      "KOR_TRAVEL_WEATHER_ADDRESS_REFRESH_TIMEOUT_SECONDS"
    ],
    [
      "services",
      "kor-travel-weather-api",
      "environment",
      "KOR_TRAVEL_WEATHER_KAKAO_REST_API_KEY"
    ],
    [
      "services",
      "kor-travel-weather-api",
      "environment",
      "KOR_TRAVEL_WEATHER_VWORLD_API_KEY"
    ],
    [
      "services",
      "kor-travel-weather-dagster-code-server",
      "environment",
      "KOR_TRAVEL_WEATHER_ADDRESS_REFRESH_AUTO_ENABLED"
    ],
    [
      "services",
      "kor-travel-weather-dagster-code-server",
      "environment",
      "KOR_TRAVEL_WEATHER_ADDRESS_REFRESH_BATCH_SIZE"
    ],
    [
      "services",
      "kor-travel-weather-dagster-code-server",
      "environment",
      "KOR_TRAVEL_WEATHER_ADDRESS_REFRESH_TIMEOUT_SECONDS"
    ],
    [
      "services",
      "kor-travel-weather-dagster-code-server",
      "environment",
      "KOR_TRAVEL_WEATHER_KAKAO_REST_API_KEY"
    ],
    [
      "services",
      "kor-travel-weather-dagster-code-server",
      "environment",
      "KOR_TRAVEL_WEATHER_VWORLD_API_KEY"
    ]
  ],
  "rest_model_equal": true,
  "runtime_versions_hashsets_71_equal": true,
  "wheel_sha": "1abf0547a7b7fbe76284b69c375e83d953788b83a05fbd6720957a869f865b53",
  "tag": "930a44266e814ab6",
  "core_dependency_metadata_checked": [
    "dagster-postgres",
    "dagster",
    "sqlalchemy",
    "dagster-graphql",
    "dagster-postgres",
    "dagster-webserver"
  ],
  "package_git_equal": true,
  "official_alias_plane": [
    "kor-travel-dagster-webserver",
    "kor-travel-dagster-daemon"
  ],
  "RC_build5_exact": true,
  "RC_owned4_exact": true,
  "RC_compose_only3images_webalias_Weather10_added": true,
  "RC_prometheus_main_exact": true
}

## 실제 전달받은 review 요청 원문

Manager513 최종source bd8adfe22db557e5713ff4388a9030a98f7f8519 freeze(Aauthor), base main42d884f. Geo원문고정후 Manager독립리뷰 우선해주세요. wholewheelCommonff849/hosttag930a44266e814ab6/공식webalias·Weather5env2active서비스, runtime71/versionhash보존/실제SDK패키지호환/daemon불변/구성회귀. C도독립review예정. root는RC c7보존새branch준비. product수정/운영0. A handoff곧전달.

추가 RC 요청 원문:

Root배포RC f3680ca1083a86f2abb6c2ae46ea9328721eae9b 준비(기존c7d6+Manager513bd8 ordinarymerge, docs2충돌 양쪽보존). WT /home/digitie/dev/kor-travel-manager-codex-odroid-nvme-rc-a-20261017. queue514/NVMecompose_service521/DB522/metrics525 각파일 c7 byteexact 확인. main42의prometheus_targets·config·관련tests가추가됨(source기존main동일). rootpure68PASS1deselect; 최초68P1F는Weathervenv에docker.errors부재로app전체import시험1개불가, 환경미제공으로기록. PR513review시RC가513제품입력과동일하고RC기능손실없음도delta점검가능하면추가. runtime설치아직0.
