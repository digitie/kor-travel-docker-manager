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

우선순위는 instance 전체의 줄 순서다. 다른 테넌트의 run과도 비교된다.

## 2. location 공정성 상한

**규칙: 한 location의 상한(`dagster/code_location`)은 그 호스트의 전역 상한보다 작다.** 한 테넌트가 전역 슬롯을 독점하지 못하게
하는 것이 목적이다. 상한의 합은 전역보다 커도 된다 — 상한은 독점을 막고 몫을 예약하지 않는다.

| 호스트 | 전역 | location 상한 | 정본 |
|---|---|---|---|
| n150 | 6 | Map 4 · PinVi 2 · Concierge 2 | `config/dagster-shared/dagster.yaml`(n150은 override 없이 기본을 쓴다) |
| odroid | 5 | transport 3 · weather 3 · geo 1 | `config/docker-targets.yml`의 `hosts.odroid`(transport는 기본 파일 값) |

- n150: 2026-11-24 전에는 map·pinvi 10이었다. Map 전체 적재 하나가 6을 다 차지하면 PinVi·Concierge run이 큐에 남았다.
  Map 4면 Map이 몰려도 2자리가 다른 테넌트에 남는다. PinVi 2는 Concierge와 같은 크기다.
- odroid: 세 값 모두 이미 전역 5보다 작아 바꾸지 않았다(2026-10-26 운영 대조로 보존한 튜닝). 값별 근거는 `hosts.odroid` 주석.
- location 안의 업무 상한(`kortravelweather/run_group`·`kortraveltransport/run_group`·`kortravelcommon/job`·
  `kor_travel_map.feature_update_request_id`)은 테넌트 정책이라 이 규칙과 독립이다.
- 시험: `backend/tests/test_dagster_shared_config.py::test_every_location_cap_is_below_its_host_global_budget`가 두 호스트의
  **렌더 결과**(호스트 override 반영)와 각 호스트 workspace에 실린 location을 대조한다.

새 location이 합류하면 그 호스트의 전역보다 작은 상한을 함께 정한다(`docs/platform-topology.md` §8 합류 절차).

## 3. `dagster/priority` 공통 규칙

| 값 | 분류 | 기준 | 예 |
|---|---|---|---|
| **10** | 실시간 수집 | 1시간 미만 주기의 정기 수집·상태 적재(지금 상황을 사용자에게 보여 주는 것) | transport 공항 주차·고속도로 소통(5분) |
| **5** | 매시 수집 | 1시간 이상·하루 미만 주기의 정기 수집 | transport 유가·휴게소 유가 |
| **0** | 배치 | 하루 이상 주기, backfill·전체 재적재, 정리·유지보수·검증·export, 수동·관리자 일괄 실행 | 일 1회 적재, 보존 정리 |

- 값은 정수 문자열(`"10"`, `"5"`, `"0"`)이다. **음수와 10 초과는 쓰지 않는다** — 음수는 그 run을 다른 테넌트 뒤로 무기한 굶길 수 있고,
  10 초과는 이 표 밖의 등급을 만든다.
- 태그는 **job 정의의 tags**에 단다. 그래야 schedule·sensor·launchpad·재실행 모든 경로의 run 본문 tag에 들어간다. RunRequest가
  같은 key를 덮어쓰지 않게 한다.
- sensor가 띄우는 run은 그 run이 하는 일의 주기로 분류한다(sensor 평가 간격이 아니다).
- 분류한 job에는 0도 명시한다. 태그가 없는 job은 0으로 취급되지만, 명시하면 테넌트 시험이 "분류 누락"을 잡을 수 있다
  (job 이름 → 값 표 하나 + 모든 job이 {0, 5, 10} 중 하나를 갖는지 검사 — transport `JOB_PRIORITIES`·
  `backend/tests/test_job_runtime_priority.py`가 본보기).
- 사람이 화면에서 기다리는 대화형 요청 run(예: Concierge interactive lane)은 이 표에 없다. 정하기 전까지 0이다.

적용 현황(2026-11-24):

| 테넌트 | 상태 |
|---|---|
| transport | 적용(실시간 10·유가 5·나머지 0) — transport DAGSTER-RUNTIME-PRIORITY-20261124 |
| Map | 적용 PR(state job·feature schedule) |
| weather | 적용 PR |
| PinVi·Concierge·geo | 미적용(0) — 각 저장소 후속 |

## 4. 한계

- 우선순위는 **줄 순서**만 바꾼다. 이미 도는 run을 멈추지 않으므로, 긴 배치가 슬롯을 차지하고 있으면 실시간 run도 슬롯이 빌 때까지
  기다린다. 그것을 막는 것이 location 상한과 run group 상한이다.
- 상한이 무는 경로는 `dagster/code_location`이 run 본문에 들어가는 경로(schedule·sensor·launchpad·backfill·재실행)뿐이다
  (`config/dagster-shared/dagster.yaml` 주석, 2026-09-30 적대 리뷰 H1).
- 상한 값은 instance digest에 들어간다 — 바꾸면 그 호스트의 daemon·webserver·code-server가 다시 만들어진다(`dagster-plane converge`).
