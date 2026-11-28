# 공용 Dagster 테넌트 공정성 — location 상한과 `dagster/priority`

2026-11-24 소유자 결정(ADR-65). 공용 Dagster instance 하나를 여러 테넌트가 나눠 쓰는 두 plane(n150: Map·PinVi·Concierge,
odroid: transport·weather·geo)에서 **누가 먼저, 얼마나 동시에** 도는지를 정하는 규칙의 정본이다. 테넌트 저장소는 이 문서를
링크하고 값을 다시 정하지 않는다.

배경: 2026-11-23 수집 실패 감사(Map 저장소 `docs/reports/2026-11-23-collection-failure-audit.md`) §1.4·§2.6 9 — n150의 location
상한(map·pinvi 10)이 전역 6보다 커서 무는 일이 없었고, 어느 테넌트도 `dagster/priority`를 달지 않아 큐가 찰 때 실시간 수집과
하루 한 번 backfill이 같은 FIFO 줄에 섰다.

## 1. 큐가 run을 고르는 방법(Dagster 1.13.24)

queue daemon(`QueuedRunCoordinatorDaemon`)은 매 dequeue에서

1. QUEUED run을 `dagster/priority`(run 본문 tag의 정수, 없거나 정수가 아니면 0) **내림차순**으로 정렬한다. 같은 값끼리는 FIFO다
   (`_priority_sort`, 안정 정렬).
2. 그 순서대로 **전역 상한**(`concurrency.runs.max_concurrent_runs`)과 **tag 상한**(`tag_concurrency_limits`)을 모두 만족하는 run만
   꺼낸다. 상한에 막힌 run은 건너뛰고 다음 run을 본다 — 높은 우선순위 run이 막혀 있어도 다른 run을 막지 않는다.

**주의 — pool에 막힌 run도 tag 카운터를 쓴다.** 2단계에서 daemon은 tag 상한 검사를 통과한 run을 먼저 tag 카운터에 더하고,
그다음 pool(op concurrency, Map의 `GEO_HEAVY` 등)에 막히면 큐에 남긴다 — 더한 카운트는 되돌리지 않는다
(`queued_run_coordinator_daemon.py` 310-322). 그래서 같은 location의 pool 대기 run이 앞줄에 쌓이면 뒤의 run은 실제로 도는 run이
상한보다 적어도 location 상한에 막힌다 — 막히는 조건은 `도는 run + 앞줄 pool 대기 run ≥ location 상한`이다(`TagConcurrencyLimitsCounter.is_blocked`,
count ≥ limit). location 상한이 작을수록 이 문턱이 낮다(Map 10이면 합 10, 4면 합 4 — 도는 Map run 1개에 pool 대기 3개). 앞줄에 서는
것이 우선순위이므로, 실시간 run에 10을 다는 것이 이 지연을 피하는 방법이다.

우선순위는 instance 전체의 줄 순서다. 다른 테넌트의 run과도 비교된다.

## 2. location 공정성 상한

**규칙: 한 location의 상한(`dagster/code_location`)은 그 호스트의 전역 상한보다 작다.** 한 테넌트가 전역 슬롯을 독점하지 못하게
하는 것이 목적이다. 상한의 합은 전역보다 커도 된다 — 상한은 독점을 막고 몫을 예약하지 않는다.

| 호스트 | 전역 | location 상한 | 정본 |
|---|---|---|---|
| n150 | 6 | Map 4 · PinVi 2 · Concierge 2 | `config/dagster-shared/dagster.yaml`(n150은 override 없이 기본을 쓴다) |
| odroid | 6 | transport 3 · weather 4 · geo 1 | `config/docker-targets.yml`의 `hosts.odroid`(transport는 기본 파일 값) |

- n150: 2026-11-24 전에는 map·pinvi 10이었다. Map 전체 적재 하나가 6을 다 차지하면 PinVi·Concierge run이 큐에 남았다.
  Map 4면 Map이 몰려도 2자리가 다른 테넌트에 남는다. PinVi 2는 Concierge와 같은 크기다.
- odroid: 2026-11-24에는 세 값 모두 이미 전역 5보다 작아 바꾸지 않았다(2026-10-26 운영 대조로 보존한 튜닝). 값별 근거는 `hosts.odroid` 주석.
- odroid 2026-11-28 소유자 승인(ADR-69): **전역 5→6, weather 3→4** — **같은 날 운영 실측 뒤 5·3으로 되돌렸다**(weather 대기 p75 239→592초,
  사전 구성 run 5번 모두 `statement_timeout`, ADR-69 "되돌림"). 아래는 올렸을 때의 근거와 위험 기록이다. transport 3·geo 1은 그대로다. weather 4 < 전역 6이라 규칙을
  지키고, weather가 몰려도 2자리가 transport·geo에 남는다(전에는 3 중 2). location 안 업무 상한(`external_weather` 2·
  `external_weather_slow` 1)도 4보다 작아 둘이 함께 돌아도 1자리가 지역·특보·export 사전 구성 run에 남는다.
  - 왜: weather A3 export 사전 구성(`weather_export_prebuild_job`, cron `23,38`)이 queue에서 기다렸다 — 우선순위 10(아래 §3 예외)을
    단 뒤에도 생성→시작 p75 189초·최대 579초(시뮬레이션). weather 3자리가 긴 run(단기예보 1~2시간, slow 외부 sweep, 일별 보존)에
    잡히고 전역 5가 찼다(daemon tick 2,205번 중 1,034번 "Maximum is 5", 2026-11-25~26 odroid 읽기 전용 실측). 운영 이력 재현
    시뮬레이션의 예상 적중: 우선순위 10만 19% → + weather 4면 22% → + weather 4·전역 6이면 29%(시작 지연 p75 42초).
    근거는 weather 저장소 `docs/export-prebuilt-pages.md` "결정과 적용"·`docs/tasks.md` `W-EXPORT-PREBUILT-CAPACITY-20261126`.
  - 받아들인 위험: 실측 동안 odroid load average 21~27(4코어)이었고, 동시 run이 하나 늘면 외부 provider 동시 호출과 메모리 압박도
    는다. weather code-server는 run worker까지 한 컨테이너에서 돌아 메모리 상한 2.5 GB(운영 실측 2026-11-28 — 손잡이는 root `.env`
    `KTDM_WEATHER_CODE_SERVER_MEM_LIMIT`, compose 기본 4g)를 최대 4 run이 나눈다. 소유자가 이를 알고 승인했다.
  - 배포 뒤 볼 것(1~2주): odroid load average·swap·OOM kill(특히 weather code-server 2.5 GB 상한 — `docker inspect`의
    `State.OOMKilled`, run 실패 사유), weather 외부 provider 429·한도 초과 응답, transport·geo run의 queue 대기(생성→시작)가
    전보다 늘지 않는지, 공용 role 연결 수(수집 경보 `dagster_role_connections`, run worker 몫 5→6 run), weather 쪽
    `W-EXPORT-PREBUILT-ACCEPT-20261126` 적중률. 나빠지면 `hosts.odroid`의 두 값을 되돌리는 PR → 같은 배포 절차다.
- location 안의 업무 상한(`kortravelweather/run_group`·`kortraveltransport/run_group`·`kortravelcommon/job`·
  `kor_travel_map.feature_update_request_id`)은 테넌트 정책이지만 **그 location 상한보다 작아야 한다** — 같으면 그 업무 하나가
  location 몫을 다 차지한다(`test_in_location_caps_leave_room_inside_their_location`). 그래서 Map 관리자 요청 worker 상한을
  4→2로 같이 줄였다(Map의 원래 근거는 "location 10 중 4만 worker에 주어 정기 적재에 과반을 남긴다"였다).
- 실측(n150 14일, 2026-11-24 읽기 전용): location별 동시 run 최대 Map 6·PinVi 4·Concierge 2. 새 상한을 넘던 시간은 Map 5개 이상
  0.7시간·PinVi 3개 이상 0.4시간(14일 중) — 상한이 무는 것은 드문 몰림 때뿐이다.
- 시험: `backend/tests/test_dagster_shared_config.py::test_every_location_cap_is_below_its_host_global_budget`가 두 호스트의
  **렌더 결과**(호스트 override 반영)와 각 호스트 workspace에 실린 location을 대조한다.

새 location이 합류하면 그 호스트의 전역보다 작은 상한을 함께 정한다(`docs/platform-topology.md` §8 합류 절차).

## 3. `dagster/priority` 공통 규칙

분류는 **자료의 성격과 주기**로 한다.

| 값 | 분류 | 기준(둘 다 만족) | 예 |
|---|---|---|---|
| **10** | 실시간 수집 | 지금 상황(현황·상태)을 담는 자료를 1시간 미만 주기로 갱신하는 정기 수집·상태 적재 | transport 공항 주차·고속도로 소통(5분) |
| **5** | 매시 수집 | 시간 단위로 바뀌는 값(가격·예보 등)을 하루 미만 주기로 갱신하는 정기 수집(1시간 미만이어도 현황이 아니면 5) | transport 유가·휴게소 유가 |
| **0** | 배치 | 그 밖 전부 — 기준정보·시간표·목록 같은 기준 자료(주기와 무관), 하루 이상 주기, backfill·전체 재적재, 정리·유지보수·검증·export, 수동·관리자 일괄 실행 | transport KRIC 시간표(매시)·여객선 시간표(4시간), 일 1회 적재, 보존 정리 |

경계:

- 현황 자료를 정확히 1시간 주기로 갱신하면 5, 하루 주기 이상은 0이다. 예보·가격처럼 현황이 아닌 값은 주기가 1시간 미만이어도 5다.
- job tag는 그 job의 launchpad 수동 실행·재실행·backfill run에도 상속된다 — 실시간 job을 손으로 다시 돌린 run도 10이다.
  이것은 받아들인다(같은 일을 하는 run이다). 일괄 backfill을 낮게 돌리려면 별도 job으로 만든다.
- UI의 asset Materialize(`__ASSET_JOB`)는 job 정의 tag를 받지 않아 0이다.
- 이벤트·요청으로 뜨는 sensor run은 그 job이 하는 일로 분류한다(sensor 평가 간격이 아니다). 관리자 요청 처리 worker는 0이다.

- 값은 정수 문자열(`"10"`, `"5"`, `"0"`)이다. **음수와 10 초과는 쓰지 않는다** — 음수는 그 run을 다른 테넌트 뒤로 무기한 굶길 수 있고,
  10 초과는 이 표 밖의 등급을 만든다.
- 태그는 **job 정의의 tags**에 단다. 그래야 schedule·sensor·launchpad·재실행 모든 경로의 run 본문 tag에 들어간다. RunRequest가
  같은 key를 덮어쓰지 않게 한다.
- 분류한 job에는 0도 명시한다. 태그가 없는 job은 0으로 취급되지만, 명시하면 테넌트 시험이 "분류 누락"을 잡을 수 있다
  (job 이름 → 값 표 하나 + 모든 job이 {0, 5, 10} 중 하나를 갖는지 검사 — transport `JOB_PRIORITIES`가 표의 본보기다.
  transport 시험은 일부 job을 빼고 대소만 보므로, 새 적용은 "정의된 모든 job이 표에 있다"까지 검사한다).
- 사람이 화면에서 기다리는 대화형 요청 run(예: Concierge interactive lane)은 이 표에 없다. 정하기 전까지 0이다.

적용 현황(2026-11-24):

| 테넌트 | 상태 |
|---|---|
| transport | 적용(실시간 10·유가 5·나머지 0) — transport DAGSTER-RUNTIME-PRIORITY-20261124. 다른 것 하나: `reference_export_prepare_job`(요청 실행 export) 5 — 규칙상 0, 확인 후속(`docs/tasks.md`) |
| Map | 적용 PR(state job·feature schedule) |
| weather | 적용(weather `JOB_PRIORITIES`). 예외 하나: `weather_export_prebuild_job`(A3 export 사전 구성, cron `23,38`) 10 — 규칙상 export라 0이지만 Map 순회 전에 끝나야 적중하므로 소유자가 10으로 정했다(2026-11-26, weather PR #312·`W-EXPORT-PREBUILT-TIMING-20261125`). weather 시험 `OWNER_EXCEPTIONS`가 이 하나만 허용한다 |
| PinVi·Concierge·geo | 미적용(0) — 각 저장소 후속 |

## 4. 한계

- 우선순위는 **줄 순서**만 바꾼다. 이미 도는 run을 멈추지 않으므로, 긴 배치가 슬롯을 차지하고 있으면 실시간 run도 슬롯이 빌 때까지
  기다린다. 그것을 막는 것이 location 상한과 run group 상한이다.
- 상한이 무는 경로는 `dagster/code_location`이 run 본문에 들어가는 경로(schedule·sensor·launchpad·backfill·재실행)뿐이다
  (`config/dagster-shared/dagster.yaml` 주석, 2026-09-30 적대 리뷰 H1).
- 상한 값은 instance digest에 들어간다 — 바꾸면 그 호스트의 daemon·webserver·code-server가 다시 만들어진다(`dagster-plane converge`).
