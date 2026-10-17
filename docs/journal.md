# JOURNAL — 작업 일지

새 항목은 아래 "과거 기록 아카이브" 블록 **다음, 첫 항목 위**에 더한다(최신이 위).

## 과거 기록 아카이브

파일이 820,205 B(에이전트 read 한도 256 KB의 3배)가 되어 2026-10-16에 옛 항목을 아래 파일들로 옮겼다(T-MGR-07). 본문은 byte 그대로, 원래 파일 순서 그대로다 — 이 파일 끝 다음이 가장 번호가 큰 아카이브의 첫 항목이고, 번호가 작을수록 오래된 구간이다. 과거 검색은 `rg <패턴> docs/journal*.md`.

**다음 분리 규칙.** 이 파일이 200 KB를 넘으면 파일 끝(가장 오래된 쪽)부터 연속 구간을 ≤ 200 KB로 묶어 다음 번호 `journal-archive-NN.md`로 같은 방식(byte 그대로, 아래 표 맨 위에 행 추가, 분리 전후 항목 제목 multiset 대조)으로 옮긴다. 기존 아카이브 파일은 다시 채우지 않는다.

| 파일 | 항목 제목 날짜(최소 ~ 최대) | 항목 수 | 크기 |
|---|---|---|---|
| [`journal-archive-04.md`](journal-archive-04.md) | 2026-08-28 ~ 2026-09-02 | 57 | 107,062 B |
| [`journal-archive-03.md`](journal-archive-03.md) | 2026-08-05 ~ 2026-08-28 | 127 | 198,530 B |
| [`journal-archive-02.md`](journal-archive-02.md) | 2026-06-15 ~ 2026-08-05 | 93 | 199,193 B |
| [`journal-archive-01.md`](journal-archive-01.md) | 2026-06-10 ~ 2026-10-04(파일 끝에 늦게 덧붙인 항목 몇 건 때문에 최대가 크다) | 55 | 199,413 B |

---

## 2026-10-17 — PR531 빠른 명령 중복 로그아웃 P2 수정

- 독립 C의 M531-C-P2-01을 수용했다. 원8ff8에서 버튼 뒤 Ctrl+K 빠른 명령으로 두 번째
  POST가 생기고 첫503/auth-true 정산이 남은 요청 중 관측을 여는 실제 반례를 재현했다.
  C의 원문과 본인7PASS/1FAIL·읽기1→3/소켓2/POST2 로그는 보존한다.
- 두 UI 진입점을 같은 동기 useRef guard로 묶었다. React pending 재렌더 전에도 중복을
  막고 POST 이후 인증 재확인까지의 처리가 끝난 finally에서만 다음 시도를 허용한다.
  재확인 오류 뒤 읽기 차단·명시 재시도와 새 로그인 회복의 기존 정책은 유지한다.
- 고정 회귀2개는 원본7PASS/2FAIL→수정9PASS이며 버튼→명령·같은프레임2회·auth대기·
  실패후의도재시도를 검증했다. frontend전체44PASS/11files,type/lint/build/backendRuff PASS.
  backend 전체pytest·실제 UI/SDK/DB/서비스 조작은0이다.
- 기존8ff8 native 후보 빌드만으로 새 source를 적용했다고 하지 않는다. 두 independent
  closure·exactCI·root 새 frontend 빌드/정식 n150 UI는 후속이며 Cookie/harness 변경0이다.

## 2026-10-17 — PR531 재로그인 관측 차단 잔존 P2 수정

- 독립 리뷰 M531-D-P2-01: logout200→auth 재확인 장애→후속 미인증→새 로그인 뒤
  차단 표식이 남아 관측이 꺼지는 반례를 수용했다. 원 c228077·최초 검증·리뷰와 실패는 보존한다.
- 실제 QueryClient/로그인 폼에서 새1FAIL4PASS를 확인한 뒤, onLogin이 auth-me를
  오류 전파 방식으로 재조회하여 authenticated=true일 때만 표식을 해제하도록 최소 수정했다.
  재조회503·미인증 응답은 해제하지 않는 음성도 포함했다.
- 제품529092a, 집중7PASS/frontend전체42PASS·type/lint/build PASS. backend와 쿠키·
  하니스·운영 변경0이며 독립 closure·새CI·root 정식n150 live UI는 후속이다.


## 2026-10-17 — PR531 로그아웃 관측 중단 구현·로컬 검증

- 제품 a300ad4는 DashboardClient의 pending부터 보호 읽기/상태·로그 WS를 끄고,
  4개 읽기 query의 실제 fetch AbortSignal 취소를 POST보다 먼저 완료한다.
  auth 재조회 후 정상 세션은 복구하고 재조회 오류에는 읽기를 막고 재시도를 허용한다.
- 실제 QueryClientProvider·제어 Promise 회귀에서 원본3FAIL→집중4PASS,
  frontend 전체39PASS·type/lint/build·backend Ruff/diff 검사 PASS.
  backend 전체 pytest·SDK/DB/Docker/원격/실제 UI 실행은 하지 않았다.
- 초기 합성 disk 응답의 summary 누락, 토스트의 실제 부가 문구와 test exact 옵션
  차이는 테스트만 정정했고 원 로그는 보존했다. 제품 판정 기준을 완화하지 않았다.
- 기존 root r9 46/48과 Strict 실패는 그대로다. 독립2리뷰·CI·root 설치/새 공개 UI는
  후속이며 525 backend 변경이나 하니스 변경을 이 PR에 섞지 않았다.


## 2026-10-17 — 로그아웃 폴링 재진입 수정 준비

- 최신 main e84f3ce의 별도 feature worktree에서 onSettled의 WS false→auth 재조회
  사이 containers 폴링 재활성화 경로를 확인했다. 실제 callback/options 제어 Promise
  모형 9조건으로 재현했으며 운영·브라우저·private 접근은 없다.
- logout pending부터 보호 읽기/WS를 잠시 끄고 요청 취소 완료 뒤 POST를 보내는 최소안을
  구현한다. 성공/실패 세션 회복 회귀, 독립2리뷰와 root 공개 UI는 후속이다.
- 기존 r9 46/48 결과와 Cookie Strict 실패를 보존하고 하니스401 예외는 늘리지 않는다.


## 2026-10-17 — T-MGR-02r 실측: n150 plane의 location 상한은 바꾸지 않는다

- 읽기 전용: n150 공용 webserver GraphQL(`127.0.0.1:11002`) `runsOrError`로 14일 19,845 run(SUCCESS 19,158·FAILURE 686·CANCELED 1).
  location은 `dagster/code_location` tag, duration = end − start, queue 대기 = start − creation, 동시 실행은 구간 sweep(시간 비율).
- n150 workspace는 map·pinvi·concierge만 LOADED. geo·weather·transport의 n150 마지막 run은 10-15T02:15Z·10-14T21:05Z·10-14T21:10Z.
- 남은 세 location: pinvi p95 24 s·최대 동시 4(상한 10), concierge p95 20 s·최대 2(상한 2, 의도된 상한), map 9 run·최대 1(상한 10).
  합산 최대 동시 4, 대기 p95 26.7 s·p99 81 s → 전역 12도 멀다. 결정 7도 이 셋과 12는 불변 → **변경 없음, T-MGR-02r 닫음**(doc-only).
- odroid(geo·weather·transport 상한)는 범위 밖(codex). 참고로 n150 시절 weather 매시 job p95 > 5 min(airkorea 1,128 s·초단기예보 1,199 s),
  external_weather 3이 이미 대기 최대 80 min — 결정 7 규칙(weather 1)을 그대로 적용하기 전에 odroid 실측이 필요하다. `dagster.yaml`은
  두 plane이 한 벌을 읽어 바꾸면 둘 다 재생성된다.


## 2026-10-17 — NH-6 Manager Prometheus job을 호스트마다 파생

- 전제 확인(읽기 전용): odroid에 Manager가 설치됐고(codex RC), n150에서 `http://192.168.1.13:12901/metrics`가 200
  (`text/plain; version=0.0.4`, `ktdm_up 1`)이다. 먼저 머지하면 `up=0`이던 조건이 풀렸다.
- `services/prometheus_targets.py`: 손으로 쓰던 `kor-travel-docker-manager` job을 파생에 넣었다. Manager는 선언된 모든
  호스트에서 돈다(ADR-58 결정 2) → `hosts:` 순서로 static_config 하나씩(R1 주소·`host` label). 포트는 backend를 띄우는
  `deploy/systemd/ktdm-backend.service`의 `ExecStart … --port`에서 읽는다(없거나 둘이면 거부). `NON_REGISTRY_JOBS`는 없앴다.
- `prometheus.yml`: odroid static_config 1개 추가(`192.168.1.13:12901`). compose 변화 없음.
- 테스트(n150 per-clone venv): 대상 21 PASS. 빨강 확인 4건 — odroid static_config 삭제, main의 옛 파일, unit `--port`
  변경, 손 job 추가가 모두 빨갛다.

## 2026-10-17 — PR515 기본 off metric 범위 보존

- 초기10항목 지속화 뒤 독립검토에서 disable_metrics가기본집합에추가가아닌
  전체교체임을 확인했다. CPU최우선 사용자요구에 맞춰 기본off에서빠진
  memory_numa/advtcp/process/cpuset4를뒤에추가해합집합14로 보강했다.
- v0.52.1 공식기본집합·Set교체동작과지원상수를공개원문으로직접확인했다.
  실제binary지원과적용은root별도소유이며agent운영접근0이다.
- 기존10에서새보존회귀1FAIL2PASS→관련26PASS/291deselect5.89초,
  Ruff/diff검사·전체Compose2arg외동일확인. 이미지/HC/resources변경0.
- 최종14는실제기존10과다르므로초기단계'재시작불필요'를확대하지않는다.
  root가실제healthURL정합과한번에clone/recreate후측정할예정이며미실행이다.


## 2026-10-17 — cAdvisor 초기10항목 source 지속화(PR515, 후속14보강 전)

- 최신main872c326의 새 featureWT에서 설계초안부터 PR을 만들었다.
  기존log/docker_only/port를 보존하고 housekeeping30s 및 정확disable_metrics
  인자만 추가했다. CPU우선30초유지·docker_only를rootaggregate0으로
  단정하지 않는 범위를 주석과 문서에 남겼다.
- old2RED 보존→관련25PASS/291deselect5.83초·Ruff·diff검사 PASS.
  전체모델은 두arg이외동일, 원문도 기존줄변경0이며 개별HC·자원·이미지와
  모든다른서비스는 보존했다. 자체음성4조건은 제품test수와 구분했다.
- 실제runtime은 root가이미같은값임을 확인했으므로 source지속화로
  재시작/새이미지없이 정합을 맞춘다. agent의 SDK/DB/Docker/운영호출0.
  두독립리뷰/CI/머지·root별도CPU측정은 후속이며 개선효과미확정이다.


## 2026-10-17 — 공용 daemon 점검·빌드 cache 후속(PR511)

- 공용 daemon만 interval120→360/start_interval15→45로 늘리고 원 Test·timeout60/retries2/start90·heartbeat tolerance300을 보존했다. shared-plane 대기945초가 기존900초에 잘리지 않게 기존3600초 허용 상한을 재사용한다.
- Common37 c485 dependency-free helper 원문을 SPDX·SHA 회귀와 함께 build context에 추가했다. 고정 pip --compile + stdlib checked-hash O0 workers1 빌드 준비이며 runtime Common 의존성·queue 업무 설정은 변경하지 않는다.
- 초기2RED 및 focused387PASS/최종 bytecode-context25PASS/Ruff/frontend type·lint·35tests·build를 확인했다. backend 전체 pytest는 로컬 Docker 통합 fixture 진입 후 own pytest SIGINT/EXIT1로 중단해 전체 PASS가 아니다. 제품 source5fd205e 고정 후 독립2리뷰·CI·native 운영 검증은 별도다. [범위·위험](daemon-health-cadence.md).

## 2026-10-17 — 활성 원장 정리(T-MGR·T-MGR-NH 완료분)

- `docs/tasks.md`에 남아 있던 완료 항목 35건을 `docs/tasks-done.md`로 문구 그대로 옮겼다(새 절 "2026-10-17 — 활성 원장 정리"). 내역은
  다음과 같다.
  - T-MGR-01c·02·03·04·05·06k·06i·NH: 첫 줄 `[x]`와 머지 PR을 덧붙였다.
  - 옛 GM-17 등 `[x]` 27건.
- 남은 일은 활성 원장에 잔여 한 줄로 남겼다: T-MGR-02r(`dagster.yaml` 상한), T-MGR-04r(Prometheus 잔여 job), T-MGR-NH 잔여(NH-6·NH-7).

## 2026-10-17 — 상태 점검 간격 정본 정합(T-MGR-HC-CADENCE, PR505)

- 기존8개 서비스의 healthcheck.interval만 승인값으로 조정했다. Geo30/90/30초, Weather90/90초, 공용 webserver90초, PG/RustFS30초다. 이미90초인 운영 Weather를270초로 늘리지 않는다.
- 신규9회귀는 기존 source에서8FAIL/1PASS, 수정 후 기존 health·공용 PG·서비스 참조와 함께134PASS였다. Compose 전체 구조 대조는8개 interval 외 모든 필드 동일/서비스추가0이다. 전체 Ruff와 frontend35/type-check/lint/build도 통과했다.
- 첫 CI의2986PASS/38SKIP/5FAIL은 보존했다. CS1100초 기동 창과 wait900초 충돌은 frozen health에서 outer wait를 파생해 해결하고 원 guard를 유지했다. 현재 최대1155초/기존 floor900·상한3600이며 stop 이전 검증과 호출 연결을 포함한249회귀/Ruff가 통과했다. plane의3개465초 고정 기대도 실제 render635초로 정산했다.
- 실제 Docker/SSH/PG 호출과 운영 변경은0이다. 별도 전개·실행Config 확인 및 CPU 전후 측정 전에는 적용/개선 완료로 표시하지 않는다. 상세는 [간격 정합](health-cadence.md)을 따른다.

## 2026-10-16 — N-호스트 일반화 설계(T-MGR-NH)

- `docs/multi-host.md` — 리뷰 3회를 거친 최종안이다. 1차 리뷰에서는 범위를 덜고(deferred로 이동) 빠진 가정을 채웠다.
- 2차 blocker B1 — PG 간선은 소유 합집합이라 실패할 수 없다. PG 소유를 하나로 모으지 않고, "소비 target이 PG를 소유하므로 구성상
  같은 호스트"로 정직하게 적었다. 그래서 R7 red 주장에서 PG를 뺐다. code-server↔plane은 validate 규칙 하나로 보장한다(공유
  location 호스트 ⊆ `dagster` 호스트).
- 2차 blocker B2 — 소유자 없는 서비스: 소유자에 init step의 `run` 서비스를 넣는다. 나머지는 명시 allowlist가 아니면 validate가 거부한다.
- C1 — Map UI geo URL은 server-side BFF가 읽는다. "버그 아님"을 정정하고 NH-8(`KOR_TRAVEL_GEO_INTERNAL_URL`, R2 geo origin)을
  더했다. `host:` 생략은 임계값 없이 지금 금지한다(NH-1에서 5개 target에 명시).

## 2026-10-16 — 재구축의 plane location 기다림 상한 파생(T-MGR-06k)

- `_wait_shared_plane_locations`의 고정 300초를 `dagster-plane converge`와 같은 render healthcheck 파생 상한
  (`_shared_plane_health_wait_seconds`)으로 바꿨다. 오늘 render에서 daemon은 465초다. healthcheck를 늘리면 기다림도 따라 늘어난다.

## 2026-10-16 — journal 분리(T-MGR-07)

- `docs/journal.md`(820,205 B)의 옛 항목 332건을 `journal-archive-01~04.md`(각 ≤ 200 KB)로 byte 그대로 옮겼다. 본문에는 최근 54건이 남는다.
  분리 전후로 검증했다: 이어 붙이면 원본과 byte가 같고, 항목 제목 multiset(386건)도 같다. 상단에 인덱스와 다음 분리 규칙을 두었다.

## 2026-10-16 — 공용 PG 수치·이미지 env, OOM/memswap 규칙, db-init-geo 확장(T-MGR-03)

- 공용 PG 서버 이미지·db-init 6개 이미지·튜닝 6키를 `${KTDM_*:-<오늘 값>}`으로, n150 compose에 없던 6키는 PG 16 서버 기본값으로
  추가했다(효과 0). `max_locks_per_transaction`·`pg_stat_statements.*`·preload는 리터럴로 둔다.
- `oom_score_adj` PG -800·plane -600. `mem_limit`가 호스트별 env인 서비스(PG·geo cs·weather cs)는 `memswap_limit`에 같은 식을 둔다.
  PG·geo cs의 기본값은 `0`(상한 없음 — 렌더에서 빠진다)이다. concierge scheduler·cs(고정 2g)는 이 규칙에서 뺐다 — n150에
  swap 0 효과가 새로 생기기 때문이다. weather cs는 ADR-58 결정 8 ⅳ대로 렌더 기준 "4g + swap(Docker 기본 2배) → swap 0"이
  된다. n150에서 이 컨테이너는 이미 odroid로 옮겨 돌지 않으므로 실제 영향은 없다. 기본값은 그대로 둔다.
- 리뷰 반영: 서버 이미지 가드를 넣었다 — 렌더된 이미지가 digest 핀이고 `:16-`인지 본다. `targets validate`와 `ensure`
  프리플라이트가 같은 규칙 하나를 쓰고, 대상은 registry가 PostgreSQL로 선언한 서비스다. `.env`에 다른 major나 floating
  tag를 넣으면 재생성된 PG가 PGDATA를 거부하기 때문이다. 통합 fixture는 `${`가 남은 이미지 참조면 skip하지 않고 fail한다.
  `oom_score_adj`의 하한은 -999다.
- db-init-geo가 `x_extension`에 확장 4종과 app role `USAGE`를 만든다(odroid fresh 경로). 격리 instance에서 fresh 실행과
  재실행(no-op)을 통합 테스트로 돌렸다.
- n150 설치 `.env`로 렌더를 비교했다. 차이는 ADR-58 결정 1의 ⑥·⑦·⑧뿐이다. 배포·n150 변경은 하지 않았다.

## 2026-10-16 — workspace digest 효과 검사·instance digest anchor(T-MGR-02 잔여)

- `ktdctl targets validate`가 공용 plane 서비스(digest env + workspace bind)의 설치된 workspace 파일 sha256[:16]을 보간된
  `KOR_TRAVEL_DAGSTER_WORKSPACE_DIGEST`와 대조한다 — 어긋나면 plane healthcheck가 unhealthy가 될 것을 설치 전에 멈춘다.
- instance digest 리터럴 7곳을 첫 서비스 안 YAML anchor 한 자리로. 최상위 `x-` 키는 `compose config`가 그대로 내보내 렌더가
  한 줄 늘어서 쓰지 않았다. n150 `compose config` sha256 불변, 운영 모드 `targets validate --json` 설치본과 byte 동일(실측).
- `dagster.yaml` location 상한 조정은 실측이 필요해 제외. 배포·n150 변경은 하지 않았다.

## 2026-10-16 — `ensure` 프리플라이트 효과 검사(T-MGR-05)

- `ensure`가 `up` 전에 렌더된 문서에서 올릴 서비스의 로컬 이미지 arch(== `docker info`)와, `KTDM_DATA_SOURCE_DEVICE`가 있을 때만
  쓰기 bind 장치(`findmnt -T`)를 본다. compose 렌더 성공은 기존 transaction이 이미 강제하므로 새로 만들지 않고 순서만 테스트했다.
- n150: 설치본 렌더 43 서비스로 실측 통과(장치 env 없음 → `findmnt` 호출 없음). 배포·n150 변경은 하지 않았다.

## 2026-10-16 — Prometheus 타깃을 registry에서 파생(T-MGR-04)

- `config/prometheus/prometheus.yml`을 커밋된 파생물로: registry 컨테이너의 `metrics_path`마다 job, 그 컨테이너가 있는
  호스트마다 static_config(R1 주소, `host` label). geo job이 odroid 주소로 바뀌어 n150에 남은 옛 geo 수집이 끝난다.
  `cadvisor-odroid`·`node-odroid` 잠정 job은 `cadvisor`(두 호스트)·`kor-travel-node-exporter` job으로 바뀐다(대시보드 영향 없음 —
  Grafana provisioning은 datasource뿐).
- node_exporter는 Manager compose에 넣지 않았다 — 디스크 여유를 보려면 호스트 `/` 읽기 전용 bind가 필요하고 그것은 C6c 후보
  계약 bind 경계의 새 예외다. odroid 호스트 측 stack을 registry `node` target(외부 프로젝트)으로 등록했다.
- `targets validate --check-coordinates`는 R5대로 자기 호스트 target의 좌표만 본다(외부 target `node`·transport는 odroid).
- 관찰(읽기 전용): odroid 임시 stack의 cAdvisor가 `unhealthy`다(node-exporter는 정상). 배포·n150·odroid 변경은 하지 않았다.

## 2026-10-16 — 소비자 origin을 registry로(T-MGR-01c)

- 형제 target(geo·transport·weather)에 `consumer_origin: "http://{host}:<port>"`를 두고, 기대 origin을 R1으로(소비자 서비스를
  실행하는 target의 호스트에서 본 값) 파생한다. 테스트의 포트 표(ADR-58 잠정 ⅳ)를 지웠다.
- `ktdctl targets validate`가 root `.env`의 원천 변수 재정의가 기대값과 다르면 거부한다(값은 싣지 않는다). 설치기의 flip 전
  검사도 같은 명령이다. n150 root `.env`는 해당 변수 4개 모두 미설정이라 설치 영향이 없다(이름·상태만 읽어 확인).
- 리뷰 2인 반영: 스캔 포트를 registry가 명시한 것(`consumer_origin` + `retired_consumer_ports`)으로 좁혔다(S3·UI·Dagster 포트
  env가 형제 소비자로 잡혀 flip을 막던 위험). 실행 target 없는 서비스는 Dagster family target의 호스트, 그 밖은 unresolved로 보고
  (default로 조용히 풀지 않음 — T-MGR-NH ⅴ는 완료가 아니다). 끝 `/` 무시, R1 주소 계산을 `registry.r1_address` 하나로 합침, 설치기
  거부 문구를 중립으로 하고 위반 줄을 전부 낸다.
- 배포·n150 변경은 하지 않았다.

## 2026-10-16 — #477 리뷰 잔여(T-MGR-01a-F), #481 위로 rebase

- #481(호스트 변경 경계·workspace 분리)이 먼저 머지돼 겹치는 것(registry 소유권·host-local 폐포·연결 주소·host 선택)은 #481을
  정본으로 두고 #483의 같은 목적 코드는 버렸다. 얹은 것: 설치기 flip 전 `host-identity` 검사(+롤백 안내), "로컬 선언 호스트 정확히
  하나" 자기 식별, `sorted(owners)[0]` 제거(미해석으로 남김), 주소 조회 프로세스당 1회(+conftest 초기화), 세 번째 가상 호스트
  fixture, 호스트 리터럴 검사·허용 목록. `decisions.md` 분리 task는 별도 문서 PR로 뺐다.
- #484(T-MGR-01b 차이분)는 닫았다 — #481의 host 필터 방식을 정본으로(코디네이터 결정, "단순하게, 과결박 없이").
- 배포·n150 변경은 하지 않았다.

## 2026-10-16 — Geo UI Dagster 공개 URL 누락 수정

Geo API에만 Dagster 공개 URL이 설정되어 UI 콘솔 버튼이 표시되지 않는 배포 누락을 수정한다.
API와 동일한 KTDM_PROD_URL_DAGSTER 표현식을 UI runtime에 전달한다. 상태 점검은
node:http 단일 요청으로 /login의 2xx를 확인하고 4초 뒤 실패한다. fetch·shell 시작
비용과 localhost IPv6 해석을 피하며 기존 healthcheck 주기·timeout·재시도·PORT 설정은 유지한다.
운영에서 별도 검증된 core HTTP 점검을 정본 Compose에도 반영하며, 이 변경으로 운영 CPU가
감소했다고 주장하지 않는다. 이미지 재빌드나 API/code-server 변경은 필요하지 않다.
로컬 Compose 참조 회귀 32건과 실제 Node HTTP 모형 8조건(2xx·3xx·401·503·4초 예산·PORT override)이 통과했다.
CodeGraph init/sync와 diff 점검도 통과했다. 제품 리뷰·현재 CI·설정 반영·외부 UI 재검증은 별도 gate다.

## 2026-10-16 — 최신 root 환경 자기 식별과 host 실행 경계 통합(Codex)

- 기존 PR #481 `c881630`에 main `5433056`(#482)을 병합했다. root `.env`의
  `KTDM_HOST` fallback, 읽기 불가의 명시적 CLI 오류와 비-root backup wrapper 안내를
  유지하며 직접 소유권에 따른 변경 거절·호스트별 workspace 파생도 보존한다.
- 문서 충돌은 upstream의 T-MGR-01a-F 잔여 과제와 PR #481 구현 기록을 모두 남겼다.
  root `.env`로 두 호스트를 각각 선택해도 공개 `ensure_target`이 다른 호스트를
  transaction 전에 거절하는 회귀 2개를 추가했다.
- 집중 회귀의 첫 274 PASS/16 FAIL은 fixture가 registry loader만 대체하고 Compose의
  loader 별칭은 실제 캐시에 의존한 문제였다. 둘이 같은 fixture를 읽도록 교정해
  292 PASS를 확인했다. CI와 같은 Ruff0.16.4 명령·diff 검사도 통과했다.
  이전 Ruff0.3.7 실행 오류 원문은 보존하고 현재 CI 결함으로 집계하지 않는다.
- frontend 입력은 c881과 바이트 동일하다. 이전 complete artifact의 source provenance는
  그대로이며, 새 운영 환경 SHA 대조 전 재사용·새 source 표식·운영 성공을 주장하지 않는다.
  두 독립 제품 delta 리뷰·새 HEAD의 CI·현재 배포 revision을 기준으로 한 설치 준비와
  실제 배포/live는 별도 후속이다. 기존 운영 설치기는 실행하지 않았다.

## 2026-10-15 — 원격 target의 HTTP 관측과 로컬 도구 경계(Codex)
## 2026-10-15 — HTTPX 런타임·CI·오프라인 설치 계약 교정(Codex)

- 독립 B-P1-02를 수용했다. runtime으로 옮긴 `^0.27.0`은 CI `httpx==0.28.1`과
  교집합이 없어 실제 old wheel/pip에서 ResolutionImpossible이었다. 원문 실패를 보존하고
  지원 범위를 `^0.28.1`로 맞췄다. CI pin이나 resolver를 우회하는 설치 옵션은 바꾸지 않았다.
- 새 별도 venv에서 캐시 기반 의존성 해석과 실제 wheel 설치·CI editable 설치·pip check가
  통과했다. 그 환경에서 remote/multi-project52 PASS였다. 이어 자기 로컬 wheelhouse32개만
  제공한 빈 venv의 `--no-index --find-links` source build/설치도 CI 버전 조합으로 통과했다.
  cold 설치된 package/HTTPX0.28.1의 observer6 PASS·pip check PASS이며 중복 수를 합산하지 않는다.
- 이 wheelhouse는 자체 x86_64/CPython3.13 검증용이다. 운영 root-owned wheelhouse를 수정하거나
  배포하지 않았으며 대상 host/아키텍처의 준비는 기존 trusted 발행 절차를 따른다.
  전체 backend·독립 postfix 두 리뷰·최종 CI·live는 별도 후속이다.

## 2026-10-15 — 실제 Compose host 선택과 공개 plane 시작의 리뷰 결함 교정(Codex)

- 독립 리뷰 A-P2-01/B-P1-01을 수용했다. 두 plane의 workspace mount를 `KTDM_HOST`로,
  digest를 기존 `KTDM_DAGSTER_WORKSPACE_DIGEST`로 실제 보간한다. 기본 n150은 유지한다.
- 기존 frozen reader를 lower Compose activation에도 연결해 public `ensure_target("dagster")`
  및 의존성으로 plane을 시작할 때 host/source/두 digest를 명령 전달 전에 확인한다.
  converge와 같은 함수를 재사용하며 새 lock·봉인이나 runtime 튜닝을 추가하지 않았다.
- 실제 제품 전체 Compose를 plugin으로 정적 렌더한 n150/odroid/추가 host 및 공개 ensure의
  잘못된 source·각 plane digest 거절 회귀 OLD8 FAIL/3 PASS → NEW11 PASS.
  관련 host/CLI/converge/cutover 286 PASS, Ruff·diff PASS. 기존 실패 원문은 보존한다.
  실제 daemon 활성화·운영 설치·live는 미실행이며 전체 후속 검증은 별도다.

## 2026-10-15 — host writer·workspace 분리의 로컬 전체 회귀 완료(Codex)

- 고정 소스 `0a8dd96`의 ext4 독립 사본에서 backend 전체 2842 PASS/39 SKIP(318.69초).
  Docker daemon은 미존재 소켓으로 격리하고 검증 전용 shim은 정적 `compose config`만
  실제 plugin에 전달했다. 실제 Docker 통합 39건은 미실행이며 PASS로 합산하지 않는다.
- 초기 69 FAIL은 보존했다. 원본 제품 소스 `d8c2cfc` 그대로 정적 Compose 관련
  6파일 679 PASS로 환경 원인 68건을 확인했고, 나머지 외부 프로젝트 fixture 1건은
  직접 소유 host를 명시해 관련 host 경계 62 PASS로 닫았다. skip이나 기대 계약을 완화하지 않았다.
- frontend35 PASS·type/lint/build, backend 전체 Ruff·diff, offline wheel 및 CodeGraph sync도
  완료했다. HTTPX runtime 의존성은 설치 wheelhouse에서 포함 여부를 확인해야 한다.
  독립 제품 리뷰 2인·최종 CI·운영 설치·live E2E는 아직 후속이며 PR은 draft다.
  Common light health와 추가 CPU/cadence 변경은 이번 소스에 섞지 않았다.

## 2026-10-15 — 전체 회귀의 Docker 격리와 외부 프로젝트 fixture 전제 교정(Codex)

- 최초 전체 backend 검증은 69 FAIL/2773 PASS/39 SKIP이었다. 실제 Docker 접근을 막은
  검증 전용 shim이 데몬을 쓰지 않는 `compose config`도 exit127로 막아 정적 렌더와
  후속 volume 검증이 실패했다. 이 로그를 보존하고 정적 `config`만 허용하는 별도
  검증 경로로 재확인한다. daemon 명령과 image digest 원격 조회는 계속 차단한다.
- 별도 실제 fixture 결함은 external-project 거절 시험이 다른 host의 Transport를
  선택해 더 앞선 host 거절을 받던 것이다. 이 시험만 registry의 직접 소유 host를
  현재 host로 지정해 기존 외부 프로젝트 거절 경로를 확인한다. 실제 registry 배치와
  원격 mutation 거절은 바꾸지 않는다. 해당 시험과 host 경계 62 PASS, Ruff·diff PASS.
  전체 재검증과 독립 제품 리뷰·live 결과는 후속이며 완료로 기록하지 않는다.

- 로컬 컨테이너가 없는 원격 target을 로컬 미생성/offline으로 오인하지 않는다. 로컬에 남은
  컨테이너는 기존 Docker 상태와 stop/log 경로를 유지하고, 나머지는 모든 HTTP connection을
  4개 동시·각 2초 전체 예산으로 header만 관측한다. redirect·재시도·본문 읽기·원격 쓰기는 없다.
- HTTP up/down/unreachable과 미관측 remote는 Docker running이 아니다. metrics/config를 추정하지
  않으며 UI의 로컬 제어·로그·inspect·설정을 잠근다. host/registry snapshot도 항목별로 다시 읽지 않는다.
  HTTPX는 같은 기존 버전 범위의 개발 의존성을 runtime으로 옮겼다. offline wheelhouse 포함 확인이 필요하다.
- 원격 상태 OLD 2 FAIL/1 PASS, UI OLD 1 FAIL로 결함을 재현했다. 첫 NTFS UI 실행은 worker
  timeout으로 시험0건이었고 실패를 보존했다. 같은 lock의 ext4 의존성에서 전체 frontend35 PASS,
  type/lint/build PASS, remote/multi-project52 PASS, Ruff·diff·offline wheel build PASS.
- 최초 확대 backend 회귀는9 FAIL/40 PASS였다. 새 status snapshot의 import 별칭이 fixture 주입을
  우회한 결함을 고치고, 다른 host에 배치된 Transport를 사용하는 예전 같은-host Compose 경계 시험은
  임시 registry에서 해당 전제만 명시했다. 실제 registry의 이전된 host와 변경 거절 계약은 유지한다.
- 그 최초 확대 실행은 새 HTTP observer를 mock하지 않은 기존 시험 때문에 실제 원격 GET 읽기가
  나갔을 가능성을 배제할 수 없다. 이를 운영 호출0으로 기록하지 않는다. 쓰기/배포 경로는 없고,
  추가 재조회 없이 이후 모든 backend 시험의 기본 observer를 미관측 대역으로 격리했다.
  HTTP 동작 자체는 명시 MockTransport로만 검증했다. 전체 backend·독립2리뷰·live·설치는 후속이다.

## 2026-10-15 — compose 전체 저장의 host 소유권 보강(Codex)

- `save_compose_config`도 기존 registry의 직접 service 소유권을 검사한다. 원격 service 수정·삭제,
  미등록 service 추가, 원격 service에 영향을 줄 수 있는 project/network 공통 설정 변경을
  렌더·원자 저장 전에 거부한다. 로컬 service 변경과 기존 G·frozen validation·CAS 저장은 유지한다.
- 기존 Compose 실행 경계와 소유권 계산을 공유한다. config 저장은 컨테이너를 시작하지 않으므로
  가짜 argv/플래그로 의존성 면제를 주장하지 않는다. 실제 시작의 의존 폐포는 실제 파싱된 argv와
  frozen graph로 계속 판정하고, `scale`도 기존 의존성 도달 명령 집합을 재사용한다.
- 신규 전체 저장 회귀 OLD 4 FAIL/1 PASS → host/config 관련 176 PASS. scale 의존 경계를 추가한
  최종 host61 PASS, backend 전체 Ruff·diff PASS. 중복 실행 수는 합산하지 않는다.
  운영·배포·live E2E는 수행하지 않았다.

## 2026-10-15 — T-MGR-01b lower Compose 실행 범위

- 기존 Compose parser와 registry 직접 services/init·installed Dagster family·기존 pinned one-shot 이름으로 소유권을 파생한다. public run은 mutation lock 전에 직접 범위를 검사하고 실제 실행은 frozen resolved depends_on 폐포도 검사한다. 진짜 no-deps 옵션만 폐포를 생략한다.
- 모호한 전체 mutation·알 수 없는 서비스·remove-orphans는 범위를 보증할 수 없어 거절한다. 조회와 명시적 stop은 host 설정 장애에도 기존 경로를 보존한다. pinned rebuild는 pair 소유권을 root/intent 진입 전에 확인한다. 새 role·봉인·lock·registry field를 추가하지 않았다.
- OLD 7 FAIL로 진입·의존 범위 결함을 재현했고 새 host 회귀56 PASS. 관련 확대 최초734건은730 PASS/4 FAIL였으며 4개 모두 빈 resolved mock이 새 graph 검사를 충족하지 못한 CLI fixture였다. 실제 선언 graph로 고친 뒤 host+CLI 200 PASS로 해당4개를 재검증했다. 중복 수를 합산하지 않으며 최종 전체 backend 통과로 부르지 않는다.
- backend Ruff·diff PASS. compose 편집/remote 상태·전체 backend/두 독립 리뷰/live는 후속이며 운영 접근0이다. 다른 우선 운영 제어기 리뷰 동안 이 atom을 보존한다.

## 2026-10-15 — T-MGR-01b pin 쓰기의 host 경계

- pin/execution private·public pair 문서와 회전 intent는 Map/PinVi 각 target이 모두 local일 때만 쓴다. 기존 registry 직접 소유권 검사를 재사용하며, 단일 role 회전도 v6 pair 문서와 intent를 함께 쓰므로 같은 조건을 적용한다. 개별 요청 write/clear는 해당 role만 검사하고 손상 요청 discard는 pair를 검사한다.
- writer 6개·회전 진입 3개를 OLD 9 FAIL로 재현했다. off-host에서는 private 파일 바이트를 보존하고 intent 조회/생성 전 거절한다. split-host pair의 한쪽만 local인 경우 거절하며 local 단일 요청은 remote peer 때문에 막지 않는다.
- 관련 6파일 385 PASS, 전체 backend Ruff·diff PASS. 기존 v5/v6 digest·직렬화·버전·G/lock·복구 절차·read-only 경로는 변경하지 않았다. 이전 backup 381건과 겹치므로 합산하지 않는다.
- lower Compose·compose 편집·pinned workflow 상위 진입·remote 상태·전체 backend/독립 리뷰2인/live는 후속이다. 아직01b 전체 완료가 아니고 운영 접근·실제 pin 회전은 0이다.

## 2026-10-15 — T-MGR-01b 백업 role의 host 소유권 경계

- 공용 PG의 host-all 소유권과 tenant DB 쓰기 권한을 분리했다. create/gc/rehearse는 기존 registry의 직접 target 소유권을 파일·PG 접근 전에 확인하고, API는 job 제출·감사 기록 전에 같은 검사를 thread에서 수행한다. 기존 StandaloneBackupError/HTTP409 응답 계약을 보존한다.
- 원격 Geo/Transport role 6조건은 OLD 6 FAIL로 재현했다. 새 회귀는 두 host의 실제 role 배치, unknown/missing operating host, public API의 job·파일 접근 전 거절을 확인한다. 기존 백업 알고리즘 모형은 host 계약 모형과 분리한다.
- 관련 4파일 381 PASS, 전체 backend Ruff·diff 검사 PASS, CodeGraph sync 완료. 초기 378 PASS/1 FAIL은 T02 CLI srv의 이동 전 Geo 기대였고 최신 host-local 기대와 Geo 미시작 음성 조건으로 수정했다. 추가 379 PASS/2 FAIL은 pytest-asyncio 미설치 상태의 새 모형 marker 오류였으며 asyncio.run으로 수정했다. 원 로그는 보존한다.
- lower Compose·pin writer·remote 상태의 나머지01b와 전체 backend/독립 리뷰2인/live는 후속이다. 이 백업 atom만으로 01b 완료나 운영 배포 가능을 선언하지 않는다. 실제 Docker/PG/운영 접근 0, instance 튜닝 bytes 불변이다.

## 2026-10-15 — T-MGR-02 호스트 workspace 연결 구현 중

- registry의 실제 배치로 host별 shared workspace를 유도하고 default alias를 보존한다. Compose 선택 host와 기존 frozen env의 두 digest를 같은 file bytes와 대조한다. cutover membership/digest는 같은 resolved source를 사용한다.
- ensure의 services와 init을 같은 local 실행 계획으로 연결했고 cross-host Map→Geo Compose 간선만 제거했다. 기존 same-host/infra 간선과 Geo odroid 배치는 보존한다.
- 단일 host 가정의 기존 전환 fixture를 실제 target 소유 plane에 맞추고 off-host/한쪽 stale digest 음성 회귀를 추가했다. dagster.yaml fullSHA와 현재 상한은 불변이다.
- 고정 후 관련115 PASS, 전체 backend Ruff와 diff 검사 PASS다. 이전 개발 중146조건 통과는 최종 고정 검증과 분리하며 합산하지 않는다.
- 이 단계는 lower Compose/backup/pin/remote 상태의 전체01b 완료가 아니다. 운영·DB·배포0, 전체 backend/독립 리뷰2인/live는 후속이며 현 로컬 결과만으로 완주를 선언하지 않는다.

## 2026-10-15 — T-MGR-01b 변경 소유권 기초 구현 중

- PR477 머지 main6bd65cd에서 별도 브랜치로 시작했다. registry의 직접 target/container 호스트 판정을 재사용하고 alias·대소문자와 pair 각 대상 검사를 추가했다.
- 공개 Docker start/restart/config/reset의 external 빠른 경로보다 먼저 소유 호스트를 검사한다. stop·logs·status의 기존 로컬 관측 경로는 유지한다.
- ensure에 사용할 services/init 동일 local 폐포 계산을 구현했다. 첫 커밋은 이 공통 primitive와 Docker 경계만이며 lower Compose·backup·pin·workspace 전체 연결은 아직 진행 중이다. T-MGR-01b/T02 완료나 운영 설치 가능 상태로 표시하지 않는다.
- 운영·Docker·네트워크·DB 변경 0, 기존 G·pin schema·instance 설정 변경 0. 새 봉인이나 권한 체인은 추가하지 않는다. 전체 회귀·2인 독립 리뷰·live 검증은 후속이다.

# JOURNAL — 작업 일지

이 파일은 `kor-travel-docker-manager` 저장소에서 진행된 작업을 역시간순(가장 최신 항목이 맨 위)으로 기록한다.

## 2026-10-15 — ktdctl이 root `.env`의 `KTDM_HOST`를 읽게 함

- 증상(n150 prod, #477 설치 `ab52ab51` 뒤): root `.env`에 `KTDM_HOST=n150`이 있는데 `sudo ktdctl pin verify`가
  "operating release requires hosts and KTDM_HOST"로 실패. `sudo env KTDM_HOST=n150 ktdctl …`는 통과. preflight·pinned
  rebuild·dagster-plane converge·compose-boundary 등 ktdctl을 거치는 운영 작업 전부가 막혔다.
- 원인: `registry.current_host`가 `os.environ`만 봤다. backend는 `main.py`의 `load_dotenv`로 `.env`를 올리지만 `ktdctl`·설치
  래퍼(project root만 설정)·launcher는 그러지 않는다. #477 리뷰 차단 2번이 지적했으나 고치지 않고 머지됐다.
- 수정: `current_host`가 비어 있지 않은 프로세스 env → Manager 루트 `.env` 순으로 값을 정한다. trusted 설치본은 설치 root의
  `.env`만, 개발 checkout은 `get_env_path()`. 둘 다 비면 운영 모드는 여전히 거부하고, 선언 호스트·로컬 주소 검증은 그대로다.
  오류 문구에 출처(프로세스 env 또는 root `.env`)를 덧붙였다(접두는 유지).
- 조회당 `ip` subprocess 캐시는 기존 테스트가 "매 조회 재검증"을 의도로 고정하고 있어 이 PR에서 바꾸지 않았다. 나머지 리뷰
  항목은 `docs/tasks.md` T-MGR-01a-F로 옮겼다. 배포·n150 변경은 하지 않았다.
- 리뷰 반영: 운영 모드에서 root `.env`가 있는데 읽을 수 없으면(n150 `root:root 0600`, 비-root 백업 cron) 빈 값으로
  넘기지 않고 경로와 함께 "process env에 KTDM_HOST를 두라"로 거부한다. `run-standalone-backup.sh`는 같은 조건에서
  ktdctl 전에 exit 2, `docs/prod-deployment.md` §3.w에 crontab `KTDM_HOST=<host>` 환경 줄을 필수로 적었다.
  #477 이후 n150의 concierge·pinvi·transport·dagster_shared 주기 백업은 그 줄이 들어가기 전까지 실패한다.

## 2026-10-15 — PR #477 최신 main 병합과 이전 완료 상태 보존

- main `409833e`를 feature branch에 merge했다. registry v2의 단일 hosts 선언·현재 호스트 검증·집계 조회 최적화와 선택적 시작 RPC 설정을 유지한다.
- #479의 Geo `host: odroid` 및 n150 workspace 제외, #480의 C6c 보호 환경변수 식별자 경계 검사를 보존한다. 기존 instance 파일·앱 튜닝은 변경하지 않는다.
- 충돌은 registry 설명과 일지 두 곳이며 양쪽 작업 기록을 보존했다. T-MGR-01b 호스트 mutation guard와 T-MGR-02 호스트별 workspace는 별도 후속 작업이다.
- 병합 후 독립 리뷰에서 Geo 이동 전 가정이 남은 호스트 테스트 두 건의 실패를 재현했다. 이동 전→후 필터는 fixture를 명시하고 현재 배치는 Geo도 odroid로 검사하도록 고쳤다. 실제 배치를 되돌리거나 제품 로직을 완화하지 않았다.
- 로컬 회귀·정적 검사와 적대 리뷰를 거쳐 PR head를 갱신한다. 운영 설치·배포·live E2E 완료를 뜻하지 않는다.

## 2026-10-15 — 최신 main 통합과 시작 RPC 경합 보완

- #476·#478을 통합했다. registry의 중복 hosts 선언·검증기를 제거하고 Weather·Transport의 이미 이전된 배치를 보존한다.
- Geo 검증 run에서 실제 StartRun 60초 만료→재큐잉→늦은 worker 중복 종료→FAILURE를 확인했다.
  실패를 성공으로 덮지 않고 별도 새 run으로 재검증한다.
- daemon과 Geo proxy의 일반 RPC 예산을 호스트 환경으로 조정할 수 있게 했다. 기본값은 기존 60초,
  별도 schedule·sensor·repository·shutdown 제한은 명시해 파생 증가를 막는다. Geo 앱 튜닝은 변경하지 않는다.
- 운영 적용·새 run·live UI·최종 독립 리뷰 결과는 검증 후 별도 기록한다. 이 항목은 운영 완료 선언이 아니다.

## 2026-10-15 — 배포 설정 일치화의 호스트 registry 구현

- 최신 main에서 registry v2의 호스트 선언, 직접 실행 서비스 기준 소유권, host-local `all` 집합,
  `{host}` 연결 좌표와 `targets validate --json`을 구현했다. tenant 이동·운영 재생성은 수행하지 않았다.
- 두 독립 적대 리뷰의 trusted wheel/환경 경로 우회, version 혼용, 목록 순회의 반복 할당,
  secondary IPv4 누락을 반영했다. 관련 계약 116 PASS와 backend Ruff PASS를 확인했다.
  전체 backend와 수정 후 독립 재리뷰는 진행 중이다.
- Geo 튜닝값은 그대로 유지한다. 일치화 대상은 배포 경로·호스트 선택·공개 URL·Dagster 연결·버전 계약이다.
  호스트별 workspace/명령 제한 구현과 실제 이전 뒤 검증을 마쳐야 ADR-58 운영 전환을 완료할 수 있다.

## 2026-10-15 — C6c 보호 env 이름을 식별자 경계로만 맞춘다(오탐 수정)

- n150 재구축 t1015b가 배포 뒤 `validate_runtime_secret_isolation`에서 "a C6c credential leaks outside its exact environment path"로 멈췄다. 원인은 마지막 scalar 순회의 날 substring 검사(`name in text`)다 — pinvi-api의 `PINVI_KOR_TRAVEL_GEO_API_KEY`(PinVi 자신의 geo 키, #476 + PinVi ADR-075 — C6c 비밀이 아니다)가 보호 이름 `KOR_TRAVEL_GEO_API_KEY`(Map UI)를 품고 있어 누출로 오판했다.
- `contains_env_name(text, name)` 하나를 더해 이름은 `[A-Za-z0-9_]` 경계로만 맞춘다. 적용 4곳: `validate_current_map_ui_auth_runtime`·`validate_runtime_secret_isolation`의 scalar 순회, Map source 환경 계약의 `matching_names`와 추적 env_file 내용 검사. `NAME=`·`$NAME`·`${NAME:-}`·JSON 키·문장 속 단어는 그대로 잡는다. 비밀 **값** 검사와 정확 이름 비교는 바꾸지 않았다.
- 의도한 맞바꿈: 보호 이름을 **늘리기만 한** 이름(`<NAME>_FILE`, `${<NAME>_OLD}`, `PINVI_<NAME>`)은 이제 이름으로는 잡지 않는다. 그 값이 C6c 비밀이면 값 검사가 그대로 잡고, Map 설정은 `_FILE` 변형을 읽지 않는다.
- 테스트(`test_f1d_compose_contract.py`·`test_map_source_contract_files.py`): 바꾼 4곳마다 양성(늘린 이름 통과)·음성(`${NAME}`·`NAME=` 거부) 한 쌍. 양성은 그 자리를 날 substring으로 되돌린 돌연변이에서, 음성은 그 자리 검사를 끈 돌연변이에서 빨갛다. 값 누출 테스트는 값 검사를 끈 돌연변이에서 빨갛다. 배포·n150 반영은 이 PR 범위 밖이다.

## 2026-10-15 — n150 공용 workspace에서 geo 제외(소유자 결정)

- n150 geo code-server(`12503`)가 02:42Z 멈춘 뒤 공용 daemon·webserver가 geo location 미응답으로 unhealthy였다(운영자 preflight 차단).
- geo에 `host: odroid`를 두어 같은 잠정 파생으로 n150 `workspace.yaml`에서 뺐다 — 남는 것 concierge·map·pinvi, digest `4461abc13cce9c4f`. 소비자 origin 테스트의 geo 재정의는 군더더기로 빨개져 지웠다.
- geo Dagster는 2026-10-15부터 odroid의 plane에서 돈다(다른 세션이 odroid 자체 workspace에 02:52Z 합류 — Manager 밖, 호스트 측; 그 schedule·sensor는 지금 멈춤 상태). Manager의 odroid workspace 파생은 T-MGR-02. 배포·n150 반영은 이 PR 범위 밖이다.

## 2026-10-15 — `ktdctl dagster-plane converge`: 공용 plane만 설치 release render로 수렴(T-MGR-06j)

- #476 설치 뒤 n150 공용 daemon·webserver는 옛 workspace bind·옛 digest env로 남아 사라진 weather·transport code-server를 기다리며 unhealthy다. n150은 `rehearsal/rebuildable`이라 일반 `ensure`는 거부되고, plane을 고치는 재구축은 preflight가 plane unhealthy를 이유로 시작하지 않는다(소유자는 preflight 면제를 거절).
- 재구축의 plane 수렴 조각을 같은 lock(G, `_pinned_runtime_rebuild_environment_lock`)·같은 환경 계약(`assert_pinned_runtime_rebuild_allowed`)·같은 compose 호출(`up -d --no-deps <plane>`, `_recreate_shared_plane`로 재구축과 공유)로 따로 도는 명령을 더했다. 판정은 `_shared_plane_current`를 서비스별 이유로 펼친 `_shared_plane_drift`(같은 판정). ~~비밀 env 값은 싣지 않고 `_DIGEST` 16진 값만, 기다림은 300초 상한~~ — 아래 리뷰 반영 줄이 대체한다.
- 테스트 `backend/tests/test_dagster_plane_converge.py` 18건: plane 밖 서비스 미접촉·G 안에서만 compose·거부 환경 4종·lock 경합·pair 회전 대기·이중 발화·무연산·dry-run 무변경·healthy 실패 보고·CLI 플래그. 돌연변이 4종(gateway 추가, lock·환경 우회, 환경 검사 제거, 기다림 생략)이 모두 빨갛다.
- 적대 리뷰 2인(차단 없음) 반영: 기다림 상한을 render healthcheck에서 파생(daemon은 start_period 뒤 120초 간격이라 고정 300초는 정상 plane을 실패로 볼 수 있었다; 재리뷰 뒤 `start_period + (interval + timeout) × retries + 15초` → daemon 465초 — probe는 앞 probe가 끝난 뒤 interval을 쉬고 디스크 대기 중엔 timeout까지 걸린다). `--dry-run`은 늘 종료 코드 0이고 어느 결과든 health를 싣는다(JSON에 `health`·`remaining_drift` 항상), 무연산도 health를 보고 unhealthy면 종료 코드 2(`current_not_healthy`), drift 값 출력은 healthcheck가 이름으로 대조하는 파일 digest env만, healthcheck 없는 컨테이너는 즉시 실패. ADR-51에 G 위 진입점 NOTE.
- 배포·n150 실행은 이 PR 범위 밖이다(설치 뒤 `--dry-run` → `--confirm`).

## 2026-10-15 — odroid 이전 뒤 n150 소비자 origin·공용 workspace 잠정 정리(T-MGR-06i)

- 소유자가 geo·weather·transport를 odroid(`192.168.1.13`)로 옮기고 포트를 통일했다(geo `12501`·weather `12201`·transport API gateway `12301`). n150의 weather·transport 컨테이너는 멈췄다.
- n150에서 깨진 둘: (1) 소비자 env 기본값이 n150 loopback을 가리켰다 — R2 집합 8곳(geo 5 — pinvi-api geo 신설 포함·transport 1·weather 2 — pinvi-api 신설)을 odroid origin으로, weather는 n150 `all`에서 뺐다; (2) n150 공용 plane healthcheck는 모든 location SERVING을 요구하는데 workspace가 멈춘 weather `14106`·transport `14005`를 실었다 — `hosts:`+`host: odroid`와 workspace 파생의 default 호스트 필터로 둘을 뺐다(digest `fbd0eadc90829753`).
- 계획 모델(T-MGR-01a·01c·02·06)의 부분집합만 넣었다 — R1·R3·R4·`all` 필터·두 workspace 파일·Prometheus 타깃은 없다(ADR-58 "잠정 시행"). 배포·n150 반영은 이 PR 범위 밖이다.

## 2026-10-14 — Concierge 행정구역 재해결 lane 준비

- Concierge T-200의 `concierge/concierge_admin_region_refresh` job에 limit 1을 추가하고 lane 집합 계약을 갱신했다. 전체 location limit 2와 다른 테넌트 상한은 유지한다.
- instance 파일 변경의 재생성을 감지하도록 기존 7개 digest 선언을 동일 파일 SHA256으로 함께 회전했다. 기존 파일-환경 결박 테스트를 따른다.
- 독립 적대 리뷰 2인·관련 계약·live/UI 및 운영 설정 반영은 검증 중이다. 신규 job의 첫 운영 실행은 Geo 데이터 토큰/backfill과 Map 신뢰 규칙 배포 뒤에만 진행한다.

## 2026-10-14 — tick 조회 수정의 운영/live gate 완료

- 최신 main의 공용 PG lock 정책과 기록을 양쪽 모두 보존해 병합했다. 관련 계약 318 PASS, 실제 PG·Dagster 이미지 4 PASS 및 두 FULL source 리뷰 OPEN 0을 확인했다. source R3는 `25725af8`이고 핵심 함수 SHA256은 `8152a6f21038975cbe177153e04159fdd829b9e2844b424f41beb4edb6b5305c`다.
- 첫 운영 하니스는 remote 문자열의 구문 오류로 DDL 전에 실패했다. 문자열을 compile 확인 후 검토 함수만 Manager host lock 아래 적용해 50.559초에 인덱스 준비를 완료했다. 공유 3서비스 ID·health·restart 0·OOM false는 불변이며 PG/plane을 재생성하지 않았다.
- 실제 계획은 Index Scan이며 repeatable-read 최신 범위 검증에서 원래 조건의 단일 ID/tick body와 인덱스 flags를 확인했다. history 정렬을 강제한 비교 하니스의 outer timeout 25초는 실패로 보존한다.
- 적용 직후 GraphQL 표본은 timeout 2건 뒤 3건 정상, 후속 고정 표본 5건은 200/0.080~0.524초다. 후보 API summary ok, n150 native Chromium 전체 UI 90 PASS(0 skip/0 retry/0 flaky)를 확인했다. 초기 UI 71건 중 fixture 공개키 누락 1건은 own DB fixture 준비 후 단독 재검증과 전체 90건에서 통과했다.
- 이 단계는 Concierge의 실제 Geo 공개키 401이나 호스트 이전을 완료했다는 근거가 아니다. 새 Dagster host image의 정식 rollout과 장기 표본은 별도 운영 절차다. 원본 Codex worktree의 staged 변경은 보존했다.

## 2026-10-14 — tick 인덱스 R1 연결 대기 보강

독립 리뷰 B는 PostgreSQL handshake를 응답하지 않는 본인 loopback TCP fixture에서 새 연결이 SQL timeout 이전에 계속 대기하는 문제를 재현했다. 기존 R1 BLOCK을 보존하고 `connect_timeout=5`를 명시한다. 이미지 content tag와 focused 회귀 기대값을 함께 갱신한다. 수정 후보의 실제 blackhole·격리 이미지·운영/live/UI 및 재리뷰는 검증 중이다.

## 2026-10-13 — 공용 Dagster 센서 최신 tick 조회 병목 보강

- Concierge Track 1·2 live gate에서 전체 요약이 gateway timeout으로 실패했다. 저장소/활성 run 조회는 빠르지만 sensor tick과 daemon 최신 tick 조회는 metadata I/O를 오래 기다렸다. read-only 실행 계획은 `LIMIT 1`에서도 약 9,800행의 Bitmap Heap Scan·Sort를 보였다.
- 격리 PostgreSQL 16만 행의 같은 OR/최신 정렬 쿼리는 timestamp 정렬 인덱스 후 결과 ID 불변, Sort 제거, 25.058ms → 0.175ms였다. 이는 운영 p95 결과가 아니다.
- storage bootstrap에 쓰기를 막지 않는 `CREATE INDEX CONCURRENTLY`와 같은 정의의 invalid index 재진입 복구를 추가했다. 기존 이름의 다른 정의는 거부한다. 데이터 삭제·timeout 상향·tick 응답 축소는 하지 않는다. 단일 프로세스 bootstrap 뒤 기존 `exec dagster instance migrate` 흐름을 유지한다.
- 독립 적대 리뷰 2건·실제 PostgreSQL·운영 효과·live UI는 진행 중이다. 원본 Codex worktree의 다수 staged 변경은 보존하고 별도 깨끗한 worktree에서 구현했다.

## 2026-10-13 — 공용 PostgreSQL `max_locks_per_transaction` 64 → 256 (공유 lock table 넘침 stopgap)

- **증상**: 2026-10-12 09:25Z부터 `kor-travel-shared-postgres`(PG 16)가 `out of shared memory`를 냈다(26시간 3,160건 +
  새 연결 FATAL 178건). 멈춘 Map curation finalize(`feature.finalize_provider_curation_root`, root `97ac4e01…`)가 한
  트랜잭션에서 feature별 `pg_advisory_xact_lock` 약 11,820개를 잡고 30초마다 재시도되며 공유 lock table(기본 64 ×
  MaxBackends 122 = 7,808슬롯)을 넘겼다. 3,160건 중 2,789건은 finalize 자신이고 다른 테넌트에 닿은 것은 약 10건 중 1건이다 —
  간헐적이지만 cluster 전역이다. 12:59:54Z 조정자가 Map `feature_operation_reconciliation_sensor`(30초 재시도기)를 멈춰
  루프는 재기동 없이 멎었다 — 재생성은 급하지 않고, 재생성 뒤 그 sensor를 다시 켠다.
- **변경**: compose `command:`에 `-c max_locks_per_transaction=256`(31,232슬롯, 공유 메모리 약 +7.5~8 MB, lock table 전체 약
  10.6 MB; main segment는 anonymous mmap이라 `shm_size`와 무관). 다른 튜닝과 같이 compose 리터럴이 정본이고(ADR-53 D4),
  ADR-58 결정 8 ⅰ·T-MGR-03에 "env 파생하지 않고 두 호스트 같은 리터럴"로 적었다. C6c 허용 목록
  `_POSTGRES_ALLOWED_COMMAND_SETTINGS`에 이름을 더하고 그 주석을 고쳤다(정본 서버는 이제 공용 instance 하나). 256은 큰 finalize가
  한 번에 하나라는 전제다 — Map 등산로(57k)·MOIS bulk는 Map `fix/bulk-curation-locks`(#1319, 묶음 단위 lock) 뒤에 돌린다.
  ADR은 두지 않았다(D4 목록에 한 줄).
- **테스트**: D4 정확 목록에 한 줄. `test_f1d_compose_contract.py::test_the_canonical_shared_postgres_runtime_shape_passes`에
  허용 목록 밖 GUC(`max_pred_locks_per_transaction`) 대조군을 더했다 — raw compose를 C6c 검사기에 그대로 통과시키므로 허용
  목록에서 이름을 빼면 빨강(확인). 첫 판의 "슬롯 ≥ 2 × 11,820" 테스트는 한 사건의 숫자에 결박한 것이라 리뷰 반영으로 지우고
  근거는 onboarding §6.1 C10 산문으로 옮겼다. `bindings.md` B-10 등록.
- **문서 줄 번호**: compose에 8줄이 들어가 `:283` 뒤를 가리키던 ADR-58·T-MGR-*·bindings·docker-management·onboarding·ports·
  platform-topology·prod-deployment의 compose 줄 참조를 +8 했다(튜닝 블록 `:265-283` → `:265-291`).
- **반영(머지 뒤, 별개 창)**: 절차는 PR #471 코멘트. 설치는 공용 서비스의 compose config hash를 바꿔 재생성을 무장한다 —
  설치와 재생성 사이에 `--no-deps` 없는 `up`을 돌리지 않는다. 재생성은 호스트에서 수동 `CHECKPOINT` 뒤
  `up -d --no-deps --force-recreate kor-travel-shared-postgres`(production `ensure`는 거부된다).

## 2026-10-13 — Track 2: odroid 다중 호스트 설계 ADR-58(proposed)·문서 반영·`decisions.md` 아카이브 분리

- 소유자 지시("설계와 각 프로젝트 문서 수정해서 코딩을 할 수 있게 준비하고 각각 PR 머지까지 진행")에 따른 **문서만**의 변경이다.
  코드·compose·config·`.env.example`·테스트는 바뀌지 않았다. 설계 정본은 Track 2 설계 최종본(저장소 밖; 3차 적대 리뷰 6건과
  2026-10-13 odroid 실측 반영본)이고, 이 저장소에는 결정만 ADR-58로 옮겼다.
- **커밋 ① `docs/decisions.md` 아카이브 분리**: 파일이 359,272 B로 에이전트 read 한도(256 KB)를 넘어 ADR-1~40(2,524줄)을
  `docs/decisions-archive-01.md`로 **byte 그대로** 옮기고 상단에 번호·제목·상태·파일 인덱스 표를 두었다. 분리 전후 `## ADR-` 제목
  집합 57개가 같음을 스크립트로 대조했다(본 파일 17 + 아카이브 40). 저장소 안에 `decisions.md#anchor` 링크는 0건이라 링크 갱신은
  없다. 분리 뒤 `decisions.md`는 186,825 B(ADR-58·2차 리뷰 반영 포함 239,295 B — 한도 안, 상단 "다음 분리 규칙" 참조).
- **커밋 ② ADR-58(proposed)** — 두 번째 Docker 호스트 odroid(`192.168.1.13`): registry `hosts:` + target `host:`(tenant 단일/infra
  `all`, 컨테이너 host-local 합집합), 호스트마다 같은 Manager(`KTDM_HOST` 자기 식별, Manager 간 SSH 없음), 호스트당 Dagster plane·
  PG(ADR-54·47의 "하나"를 호스트 단위로 범위 명시), 파생 규칙 R1~R7(R7: tenant 간 `depends_on` 금지 — `map-api → geo-api`
  `docker-compose.yml:1527` 삭제), 파생 표면별 기제(런타임 vs 커밋 파생물 — compose는 registry를 읽지 못한다 `compose_service.py:3154-3158`),
  host 플립 = workspace 합류(`workspace.{n150,odroid}.yaml` + compose `${KTDM_HOST:-n150}` 보간; 설치 시 렌더는 ADR-51로 기각),
  `dagster.yaml` 한 벌 + location 상한 T-MGR-02 1회(instance digest 리터럴 7곳 → anchor), PG 이미지 변수(`imresamu` arm64)·튜닝 env
  파생·OOM 희생자(`oom_score_adj` PG -800)·**memswap 정책**(odroid cgroup v1 swap accounting on 실측), 내용 해시 tag에 arch 없음(레지스트리
  선결), offbox odroid→n150 push forced-command, 관측 (b) 중앙 Prometheus + federate 1단계, Manager 범용성 원칙(리터럴 결박 대신
  env·rendered 모델 파생 + 효과 검사). Dagster (b)·Manager (1)/(3)·설치 시 렌더의 기각 근거와 열린 결정(§14 번호)을 함께 적었다.
- 설계 인용과 head(`094d397`)의 차이를 고쳐 썼다: Map→transport env는 `:1732`가 아니라 **`:1736`**이고 변수명은
  `KOR_TRAVEL_MAP_KOR_TRAVEL_TRANSPORT_BASE_URL`(같은 이름의 `${…:-}` 보간); PinVi ETL weather는 `:2128`이 아니라 **`:2134`**(리터럴);
  instance digest 리터럴은 "8곳"이 아니라 **7곳**(`:820,890,1365,1705,2120,2434,2830`); weather cs 자원 제한은 `:2874-2877`,
  `mem_swappiness`는 `:2875,2980,3066`; readiness 테스트의 `compose version` 호출은 `test_compose_readiness_integration.py:78`.
  `test_f1d_compose_contract.py:724`가 "Compose v2/v5 bind 차이"를 안다는 설계 문장은 head에서 그 자리를 찾지 못해 인용하지 않았다.
  **1차 리뷰(두 리뷰어, 22건) 반영으로 더 고친 것**: ADR 결정 3의 compute log 근거는 `dagster.yaml:64-69`(run tag 주석)가 아니라
  **`:38-42`**(`LocalComputeLogManager`), 결정 7의 location 상한 근거는 `:100-117`(테넌트 안 상한)이 아니라 **`:57-94`**(`dagster/code_location`
  항목 `:73-92`) — 둘 다 설계 본문에서 옮겨온 drift; S6 geo context `:1075,2395` 보강; S9(transport 몫) 행 추가; ADR-53 설명 오기
  (튜닝 리터럴 → Map 두 DB 이전)와 결정 1의 교차 참조(결정 7 → 결정 4 R7) 정정. **설계와 다르게 둔 것**: `mem_limit`는 호스트별
  값(odroid PG 4g / n150 없음 — 설계 §5.3 표대로)이고 두 호스트 동일한 것은 `oom_score_adj`·memswap **규칙**뿐임을 결정 8·onboarding
  §11·T-MGR-03에 명시(설계 T-MGR-03과 1차 문서가 PG 4g를 n150에도 리터럴로 읽히게 적고 있었다); transport code-server의
  memswap/mem_limit은 transport 저장소 compose의 몫으로 분리(Manager compose에 그 서비스가 없다, `config/docker-targets.yml:671-`);
  concierge 두 서비스(`:1296,:1339` `mem_limit 2g`)가 memswap 규칙에 함께 걸린다는 n150 부작용을 T-MGR-03에 적었다; NTP ⑤
  `After=time-sync.target` drop-in을 §9.1 1-④에서 **11(Manager 설치 뒤)**로 옮김(설계 §11.2 2단계 순서와 같게 — `ktdm-backend.service`가
  설치 뒤에야 존재); 설계 §11.2 전체 순서표(0~11)를 §9.2 머리에 옮겨 §5·§9.4·§9.6·T-OPS가 그 번호를 쓰게 했다(종전 "transport 5"는
  저장소에 없는 번호였다); 설계 내부 절 번호(`§3.2 조건`·`§11.5`)를 ADR 결정 13·`prod-deployment.md` §9.6 포인터로 바꾸고 접두 규칙
  ("설계 §x.y")을 ADR 머리에 적었다. **열린 결정 2건 추가(§14-29·30)**: odroid 대시보드 `12905`(설계 §4.3 표대로면 `12901`이 n150만
  허용이라 대시보드가 자기 API에 닿지 못한다 — 추천 (가) backend만), 호스트 래퍼 스크립트 정본 위치(설계 §7.6·§15가 ADR 항목으로
  들었으나 빠져 있었다 — 추천 odroid 홈 사본). 그 밖에 `deploy-prod.sh`(transport T-TRN-01 예정, 아직 없음)·transport 백업 cron(§14-7
  추천값) 표시, `KTDM_DATA_SOURCE_DEVICE`를 `findmnt` 실측 placeholder로, ports.md "주소를 적지 않는다" 선언을 "예시·파생값"으로,
  platform-topology §3에 `hosts:` 스키마 단락, 인덱스 ADR-10/35/37 상태 칸 요약, 기준 리비전을 `094d397`로 통일, T-OPS-02/03 분리,
  `decisions.md` 다음 분리 규칙(200 KB 초과 시 ADR-41~50 → `decisions-archive-02.md`; archive-01은 184,575 B라 ADR-41~50 64,172 B를
  받아도 248,747 B로 한도 안이지만 "아카이브 ≤ 200 KB" 규칙과 현행 결정 인용 때문에 받지 않는다 — 1차 반영 보고의 "≈90 KB·256 KB
  초과"는 수치 오류였고 2차 리뷰가 잡았다)과 `journal.md` 분리(774 KB)를 T-MGR-07에 남김 — 이 PR에서는 하지 않는다(범위: 계획된
  ADR-1~40 분리 + ADR-58).
- **2차 리뷰(두 리뷰어, HIGH 2·MED 8·LOW 13) 반영** — 전부 반영, 기각 0(수치 정정 1건은 위). ① **R2 geo consumer 기본값은 compose에
  4곳**(`:1582` map-api·`:1727` map code-server·`:1819`/`:1926` legacy webserver/daemon)인데 `:1582` 한 곳으로 세고 있었다 — geo 플립이
  `:1582`만 바꾸면 code-server가 loopback에 남아 모든 geocode run이 connection refused가 된다. R2를 줄 번호 열거에서 **규칙**("형제 포트를
  기본값에 가진 모든 compose env, legacy profile 포함 — 테스트가 렌더를 스캔")으로 바꾸고 ADR 결정 4·5, bindings B-6, docker-management §3.2,
  platform-topology §9, T-MGR-01c·06을 같은 7곳 집합으로 맞췄다. ② **geo 확장 4종의 생성 주체 공백**: onboarding §11이 "db-init이
  만든다"고 적었으나 head db-init-geo는 확장을 만들지 않는다(`docker-compose.yml:374-383` — cutover pg_restore 전제; 유일한
  `CREATE EXTENSION`은 concierge `:363`). odroid fresh 경로에는 pg_restore가 없고 app role은 NOSUPERUSER라 T-MGR-03이 db-init-geo에
  `x_extension` 스키마 + 확장 4종 `IF NOT EXISTS`를 더하는 것으로 결정 3에 넣고 §9.2 8b에 `\dx` 게이트를 두었다(설계 §5.4의 drift).
  ③ **connlimit 15/10/10은 compose·실측 어디에도 없다**(`CONNECTION LIMIT`은 dagster 45뿐 `:718,720`; fact base "app roles unlimited") —
  설계 §5.4의 값이었고 ADR·onboarding·platform-topology에서 걷어냈다(odroid도 db-init 그대로 무제한). ④ n150 렌더 **허용 diff 목록을 ADR
  결정 1에 단일 정본(①~⑨)**으로 두고 결과·후속·§9.2-1·T-MGR-01b/01c/01d/02/03이 그것을 가리키게 했다 — 빠져 있던 pinvi-api env +1,
  workspace bind source 2줄, PG `-c` 키 6개(n150 compose에 없는 키 — 기본값 = PG 16 서버 기본값, 효과 0), db-init-geo 확장 줄을 포함.
  ⑤ odroid에서 transport role/DB one-shot을 돌릴 명령 경로가 없었다(`kor-travel-shared-db-init-transport` `:580`은 어느 target service도
  `init_steps`도 아님) → §9.2 4a′ `docker compose run --rm --no-deps …` 명시, 3단계는 `dagster_shared`만 생성으로 정정, onboarding §11에
  target별 실행 시점. ⑥ §5 "stop 상태에서 공개 host 200" 모순 → 내부 consumer run 게이트 뒤 backend 전환, 그 뒤 공개 200. ⑦ 원격
  `status` probe를 효과 기반 하나로: `http(s)://` 전부 probe, `up`/`down`/`unreachable`, registry에 방화벽 필드 없음(HAProxy 전용 포트의
  `unreachable`은 정상). ⑧ §9 머리·T-OPS-01: §9.1-0~7은 지금 가능, 8 이후는 T-MGR-01a~05 머지 뒤. LOW: S3 "7개 target" → 6개(service
  이름/containers 키 구분), S4 "로컬 존재 검사" → 절대 경로·정규화만(`registry.py:942-972`; 존재는 `--check-coordinates` 옵트인
  `cli.py:1722`)과 R5 결박, `max_connections`는 compose에 없음(추가 안 함), 14103을 ports.md weather 행에 등록·§10.1 code-server metrics로
  정정, `dagster.yaml:73-94`·`:137` 주석 geo 누락(T-MGR-02), NOPASSWD/비밀번호 SSH 반쪽 상태(§9.1-3을 0 다음 권장), fio 경로 순서,
  4b 게이트 `targets validate --json` 복원, Map fail-open 근거(`app.py:700`·설계 `settings.py:164-176`), §9.5 주소·§9.1-7 열린 결정 표기,
  tasks 의존 필드(01d/03/05 병렬·04→01a·07→01a~05), journal 수치.
- **odroid 상태는 2026-10-13 03:4xZ 소유자 처리 결과로 적었다**(설계 본문의 "sudo 비밀번호 요구·ProxyJump 필수"는 낡은 상태):
  WSL 직접 키 로그인 가능(WSL Docker 잔재 네트워크가 `.13`을 가렸던 것 — 제거), NOPASSWD sudo·docker 그룹·NTP 동기 **완료**;
  timesyncd 부팅 순서 사이클 영속화(drop-in)·compose v2/buildx·방화벽·fstab bind·swapfile **미완**. docker 그룹 추가는 설계
  §11.1-3(c)가 권하지 않던 것이지만 소유자가 했으므로 완료로 기록하고 운영 경로(root systemd)에 영향 없음을 적었다.
- 문서 반영(전부 "(계획 — ADR-58 proposed, 미구현)" 표시, 현황 서술은 그대로): `platform-topology.md` §2 호스트 차원·§4 호스트당 PG·
  §5 호스트당 plane·§7 플립 PR 절차·§8 단계 8·§9 정본 행 3개·**새 §10**(odroid 배치표·envelope 실측·완료/미완·Track 1 관계);
  `ports.md` "호스트 규칙"(포트 동일·호스트만 `.13`, 점유 포트 × 우리 집합 충돌 0 — TCP+UDP, node_exporter 후보 `12106`, plane별
  공개 host, odroid 방화벽 집합 (가)); `prod-deployment.md` §2 odroid 전제(현지 wheelhouse·현지 빌드·compose v2/buildx)·§5 backend
  전환 포인터·**새 §9**(초기화 상태와 남은 순서·플립 PR 배포 절차·재배포·HAProxy 전환표·체크리스트·n150 해체); `docker-management.md`
  **§3.2**(`KTDM_HOST`·운영 모드 판정·R4 표·`remote(not probed)`·R2·R7·프리플라이트·`--json` 초안); `shared-postgres-onboarding.md`
  **§11**(odroid instance — `ensure dagster`가 PG, imresamu, 튜닝 env, 메모리 정책·drill, 초기 적재, 백업); `architecture.md` §2.1
  호스트 프로필 단락; `bindings.md` B-5~B-9(계획 — 테스트 이름은 각 T에서 확정, 존재하는 테스트 파일만 코드 폰트)·U-2(교차 비밀 2개);
  `tasks.md` T-MGR-01a~07·T-OPS-01~03(수용 기준·수정 파일·의존·규모; `.env.example` 키는 여기에).
- 교차 저장소 번호(같은 설계): transport ADR-014, geo ADR-070, Map ADR-110, concierge ADR-50(Track 1 문서 geo ADR-069·Map ADR-109·
  concierge ADR-49·transport T-045는 이미 main). 다음 한 작업: **T-MGR-01a**(선행: 소유자 결정 §14-9 로컬 체크아웃을 origin/main으로,
  n150 선행 실측 §14-1).

## 2026-10-13 — Map 날씨 env 잔여 점검과 퇴역 접두 가드 확장(Map ADR-104/105)

- 소유자 승인 정리: Map이 날씨를 kor-travel-weather에 넘겼으므로(Map ADR-104/105) Manager가 Map 서비스에
  주던 KMA/AirKorea/weather env를 지우려 했다. 점검해 보니 **compose·`.env.example`·C6c·fixture에 지울 것이 없다.**
  Map Dagster 세 서비스의 `KOR_TRAVEL_MAP_KMA_WEATHER_MAX_GRIDS_PER_RUN`·`…_EXTRA_POINTS`(원천
  `KOR_TRAVEL_MAP_DOCKER_KMA_*`)는 #452(`b083f2a`, 2026-10-03)가 이미 지웠다. 현재 Map 서비스 블록에는
  `KMA`·`AIRKOREA`·`WEATHER`가 든 키도 보간 원천도 없다.
- Map API의 `docker/api-entrypoint.sh` `removed_provider_keys`(`KOR_TRAVEL_MAP_API_KMA_*`·`…_API_AIRKOREA_*` 등)는
  Manager compose가 건드린 적이 없다 — #452가 지운 이름은 API가 아니라 Dagster 쪽이었고 API 거부 목록에도 없다.
  C6c `_FORBIDDEN_MAP_API_PROVIDER_ENV_NAMES`는 그 거부 목록의 거울이라 그대로 둔다(2026-10-02 판단과 같다).
- 남는 키: `KOR_TRAVEL_MAP_DATA_GO_KR_SERVICE_KEY`(Map `settings.data_go_kr_service_key` — datagokr/krheritage/MOIS가
  아직 쓴다), `KOR_TRAVEL_MAP_SEOUL_OPEN_DATA_API_KEY`(날씨 아님), weather 자신의 `KOR_TRAVEL_WEATHER_*`(KMA·AirKorea 포함).
- 되살아남 방지: `test_docker_service_config.py`의 퇴역 접두 검사(옛 `test_retired_map_opinet_krex_settings_are_gone`
  → `test_retired_map_provider_settings_are_gone`)에 `KOR_TRAVEL_MAP_KMA_`·`KOR_TRAVEL_MAP_DOCKER_KMA_`·
  `KOR_TRAVEL_MAP_AIRKOREA_`를 더하고, environment **키**뿐 아니라 값의 `${...}` **보간 원천**도 센다(#452가 지운 원천
  이름은 키 검사로는 못 잡는다). 원천을 하나도 못 보면 실패하는 하한을 붙였다. RED: 키 재삽입·원천만 재삽입 두 변형이
  각각 실패, 원복 뒤 통과.
- compose·C6c 정본 행·fixture가 바뀌지 않으므로 fingerprint·pin rebind·C6c generation 변화는 없다.

## 2026-10-12 — Map MOIS 원천 SQLite를 named volume으로(Map ADR-108 짝)

- Map MOIS 재개형 적재(Map 브랜치 `feat/mois-chunked-resumable-load`, ADR-108)의 짝이다. `kor-travel-map-dagster-code-server`가
  원천 SQLite를 `/tmp/mois_source.db`(컨테이너 overlay — 재생성마다 소실, root 디스크 압박)에서 internal named volume
  `kor-travel-map-mois-source`로 옮긴다. 경로 env와 mount target은 변수 `KOR_TRAVEL_MAP_DOCKER_MOIS_SOURCE_DIR` 하나에서
  나온다(기본 `/opt/dagster/state/mois` — Map 이미지가 appuser 소유로 만드는 자리). 옛 `..._MOIS_SOURCE_DB_PATH` 변수는 버린다.
- 규칙 테스트 `test_map_mois_source_volume.py`: env를 받는 활성 서비스를 유도하고, 경로 디렉터리가 같은 서비스의 named
  volume target과 같은 표현식인지, volume 정의에 `name`·`external`·`driver_opts`가 없는지 본다.
- 이 저장소의 첫 top-level named volume이라 F1D 테스트의 `_compose_fragment`가 그것을 몰랐다(service·secret만 복사 → 정본
  service가 "named volume is undeclared"로 거부, 39건). fragment가 선택 service의 mount source에서 참조 volume을 유도해
  함께 옮기게 했다(`_carry_referenced_named_volumes`). n150 실측: 관련 세 파일 300 passed(실제 `docker compose config`
  해석 포함), 전체는 docker CLI 없는 컨테이너에서 main과 실패 집합 동일(14건, 전부 docker CLI 필요) + 새 2건 통과.
- 설치 순서: 새 디렉터리를 가진 Map pair 회전 → 이 compose 설치 → `mois_localdata_source_sync` 1회. n150 설치는 하지 않았다.

## 2026-10-08 — weather Web을 host 포트 `12205`로(ADR-57)

- 소유자 지시: OPNsense HAProxy의 weather backend(`192.168.1.14:12205`)는 그대로 두고 weather Web을 그 포트에 직접 붙인다. `12205`는 ADR-48이 Grafana를 `12104`로 옮기며 비운 자리다(n150 `ss -ltn` 실측: `12205` 미사용, Grafana `12104`, weather Web `14105`; Manager `.env`에 `KOR_TRAVEL_WEATHER_WEB_PORT` 재정의 없음).
- host 모드에서는 `ports:`가 버려지고 이미지 CMD가 `--port 14105`를 굽는다 — `kor-travel-weather-web`에 같은 모양의 `command:`를 두고 `--port ${KOR_TRAVEL_WEATHER_WEB_PORT:-12205}`로 넘긴다. `ports:`·registry `connection`/`expected_ports`·weather API CORS 기본 origin이 같은 변수/값을 따른다. `docker compose config`로 기본값과 변수 재정의(`13999`) 둘 다 command·ports·CORS에 풀리는 것을 확인했다.
- 이미지에 HEALTHCHECK가 없고(n150 실측 `null`), Manager 안에서 weather Web 포트를 probe하는 healthcheck·smoke·C6c 검사는 없다(`dagster-shared-cutover.sh`는 컨테이너 안 파일만 본다).
- 문서: `ports.md`(weather Web 대역 예외 절), `docker-management.md`, `decisions.md` ADR-57(+ADR-47의 "포트 재배정 안 함"에 Web만 superseded 표시). n150 재배포는 하지 않았다.

## 2026-10-07 — C6c `env_file` 판정을 loader 교집합에서 파생(ADR-56)

- `KOR_TRAVEL_CONCIERGE_REPO_DIR`가 다시 켜지자 모든 Map/PinVi pinned 재구축이 `prebuild_snapshot`에서 "env_file leaks C6c data for kor-travel-concierge-api"로 거부됐다. Concierge `.env`가 공유 VWorld key와 Concierge UI auth 세 값을 Manager `.env`와 같은 값으로 담기 때문이다.
- 누출 집합을 `env_file` 경로마다 원본 compose에서 파생한다: 모든 loader가 받는 값의 교집합 + 파일을 읽지 않는 가족 서비스(UI)만 받는 가족 전용 값 + `NEXT_PUBLIC_*` 공개 값. 모호한 가족(뿌리 둘, C6c 보호 서비스 포함)은 fail-closed, 내용은 percent-decoding해서도 본다.
- 적대 리뷰 MED-1(첫 판의 가족 합집합은 code server만 받는 공유 Dagster password를 MCP에게 흘린다)과 LOW 1~4를 반영했다. RED: 현재 head에서 Dagster password 3건·percent-encoded DSN 1건이 통과했다. GREEN 뒤 교집합을 합집합으로 바꾸는 mutation에서 4건 RED.
- n150 운영 파일 read-only 실측(키 이름만): 설치된 compose와 실제 Concierge `.env`는 통과, 같은 `.env`에 공유 Dagster password를 메모리에서 더하면 거부. 허용 값 6개(55개 중).

## 2026-10-07 — Concierge 운영 설치·backend 전환 검증 완료

- 두 독립 적대 리뷰·관련 53 tests/Ruff·CI를 통과한 PR #464를 머지했다. root 소유 source clone과 offline wheelhouse로 머지 커밋 `42ffc553`을 trusted installer에 적용하고 backend health를 확인했다. 기존 rehearsal/rebuildable 분류와 다른 앱의 배포 상태는 유지했다.
- Concierge·shared metadata DB 백업과 이전 소스·이미지 복구 지점 확보 후 old APScheduler 중지·drain, 앱 migration `20261006_0030`, canonical C6c의 네 서비스 재생성 및 공유 storage migrate·plane 갱신을 수행했다. Concierge CLI로 `dagster / generation 1` 전환을 완료했다.
- 실제 native/domain 성공·실패, UI 재시작 lineage와 동일 입력 재실패, 같은 batch lane 후속 성공을 확인했다. 검증 seed는 외부 provider를 호출하지 않는다. 세 sensor tick·공유 plane health, code server UID10001·2GiB 및 API/MCP SDK 부재를 확인했다.
- 공개/LAN 인증 및 N150 공개 Chromium 3 live E2E를 통과했다. Basic 인증 미주입·목록 제외 seed의 초기 하니스 실패 원문을 보존하고 조건 보정 후 다시 실행했다. 다른 다섯 앱의 기존 컨테이너 ID/health와 종료 시 domain pending/running 0을 확인했다. 테스트 실패 attention만 확인 처리하고 native/domain 이력·복구 지점은 보존했다.
- 운영 강제 종료·timeout·cancel drill과 실제 Whisper 최대 RSS·유료 provider 평가는 이번 운영 배포 검증에 포함하지 않았다. 접속·인증값·dump·상세 실행 증거는 비공개 기록에만 둔다.

## 2026-10-07 — Concierge 운영 전환 준비와 공식 재생성 경로 보강

- `compose-boundary activate-concierge`의 C6c 고정 projection/recreate allowlist를 기본 shared-only Dagster code server로 맞췄다. legacy profile의 APScheduler는 자동 재기동하지 않는다.
- 실제 registry runtime 집합과 allowlist 동등성, canonical Compose projection 및 공식 activate 명령의 exact service set을 회귀한다. 관련 Linux backend 53건과 Ruff를 통과했다.
- 독립 리뷰에서 발견한 배포 가이드의 scheduler 잔존 문구를 수정했다. 운영 전환은 DB 백업·기존 실행자 중지/drain·migration·generation CLI와 공개 UI 로그인 검증을 거친다. 이 기록 시점에는 실제 backend 전환을 아직 실행하지 않았다.

## 2026-10-06: Concierge shared-only Dagster family 추가

- `conc` 기본 runtime을 code server로 전환하고 기존 APScheduler는 명시적 legacy profile로 보존했다(ADR-55). 공유 workspace/digest·instance readonly bind·location2/job1 cap과 canonical health/reaper를 적용했다.
- own/partial legacy 누락은 계속 거부한다. Map/PinVi pinned fingerprint는 새 Compose 서비스의 관측 두 필드만 추가하며 기존 필드를 보존했다.
- backend 전체 2632 PASS/7 SKIP, frontend type-check/build, 전체 Ruff 통과. 독립 적대 리뷰에서 공유 계약을 검토했다. 앱의 lane starvation 반례는 Concierge에서 수정한다.
- Concierge 격리 native/Docker/N150 UI로 queue·cgroup·child load·incarnation reaper를 확인했다. 기존 운영 서비스와 DB는 변경하지 않았으며 실제 Concierge drain/backend 전환·운영 설치는 별도 창이다.

## 2026-10-04 — 옛 프로젝트별 Dagster 메타DB 의존 제거(4단계 사고 후속)

2026-10-03 22:51Z, 4단계가 옛 메타DB 다섯(`kor_travel_map_dagster`·`pinvi_dagster`·`kor_travel_geo_dagster`·
`kor_travel_weather_dagster`·`kor_travel_transport_dagster`)을 `ALLOW_CONNECTIONS false`로 막은 뒤 같은 pair
pinned 재구축이 Map·PinVi를 멈추고 `kor-travel-map-dagster-storage-migrate`에서
`dagster_storage_database_unavailable`로 죽었다(운영 몇 분 중단, 연결 재허용으로 복구). 같은 pair가 수렴하지
못한 것은 `deploy-status.json`의 `map_dagster` head를 막힌 DB에서 읽지 못해서다.

Manager가 옛 메타DB에 닿는 자리를 모두 지웠다. pinned 재구축은 Map application·PinVi 두 DB만 다룬다(옛 Map
storage migrate·storage head·metadata role/DB init·R4의 Dagster DB·`--restart` drop 제거). 옛 상태 파일의
`map_dagster` 항목은 읽을 때 버린다. compose는 옛 Map storage migrate 서비스와 code-server·`legacy-dagster`
정의의 옛 metadata URL env를 지웠고, db-init 넷은 옛 DB를 만들거나 확인하거나 grant하지 않는다. 백업 role
`geo_dagster`·`map_dagster`·`transport_dagster`를 지웠다. C6c 보호 집합에서 옛 Map storage migrate와 옛
metadata URL 행, PinVi Dagster storage URL 검사기를 뺐다. 남은 결합은 Map 저장소 하나다 — Map의
`postgres-role-bootstrap.sh`가 metadata env 넷을 문자열로 요구하므로 Map role bootstrap만 그 넷을 받는다(접속
없음). 되돌리기(per-project Dagster)는 지원하지 않는다(소유자 결정).

회귀 방지 `backend/tests/test_old_dagster_meta_db_retired.py`는 기본 profile·재구축 one-shot·init step 서비스,
db-init, 꺼진 profile의 `:?` 요구, 재구축 DB role, 백업 role을 본다 — 이 변경 전 트리에서 7개 모두 빨갛다. 실
PostgreSQL 통합 테스트는 막힌 모양의 옛 메타DB를 심고 리셋·격리가 그 oid·ACL·`datallowconn`을 건드리지 않는지
본다. 수렴은 상태 파일이 `committed`일 때만이다 — n150의 지금 상태(`in_progress`, 실패 뒤 손으로 `docker start`)에서
다음 재구축은 Map·PinVi를 멈추는 전체 경로이므로 디스크 대기가 낮은 창의 짧은 중단으로 잡고, 실패하면 다시 만들어진
컨테이너를 `docker start`로 올린다. 설치는 env가 바뀐 code-server 넷을 다시 만든다 — `ensure`와 재구축은 하나씩, 진행
중인 run이 없을 때만. 안전한 순서: 이 release 설치 → 같은 pair 재구축(`committed`면 `converged`, 아니면 전체 경로)으로
`committed` 상태 파일 확보 → crontab의 옛 백업 두 줄 삭제 → 옛 메타DB 차단 → 30일 뒤 DROP(platform-topology.md §7).


## 2026-10-03 — Concierge 스케줄러 종료 유예와 자원 상한

Concierge 앱의 SIGTERM 정리·작업 재투입·전사 프로세스 격리에 맞춰 운영 Compose의
스케줄러에 `init: true`, 종료 유예 180초, 메모리 상한 2GiB·예약 512MiB, CPU 상한 2개를
적용했다. 운영자가 재생성해도 자원 설정이 유지되도록 정본 서비스 정의에 넣었다.
종료 유예는 앱의 작업 완료 대기 20초와 진행 중 기본 LLM HTTP 제한 120초 뒤에 DB·자식
정리를 할 여유를 둔다. 모델 프로세스의 소프트 상한과 신규 claim 보류는 앱이 담당한다.

변경은 Concierge 스케줄러 한 서비스의 여섯 필드에 한정한다. 다른 서비스·공용 DB의
설정과 데이터는 변경하지 않는다. 신호·메모리·재투입 회귀 검증은 Concierge 저장소의
실제 Linux 프로세스 및 별도 PostgreSQL 테스트로 확인한다.


## 2026-09-04 — Concierge/Grafana 실행불가 원인 규명, kor-travel-weather를 관리 목록에 추가, 메뉴 글꼴 버그 수정

Concierge MCP·Grafana가 "실행 안 됨"으로 보고된 근본 원인은 코드 결함이 아니라
`.env` 완전성이었다 — `docker compose`는 요청한 서비스와 무관하게 파일 전체를
interpolate하므로, Map/PinVi 쪽의 `${VAR:?...}` 하나만 비어 있어도 Grafana(자체
필수 변수 0개)조차 못 띄운다. 로컬 `.env`가 Concierge/Map/PinVi 구간을 채운 적이
없었던 것으로 확인했다(`docs/docker-management.md` 3.1에 이 함정을 기록). 별개로
Grafana의 Prometheus datasource provisioning(`config/grafana/provisioning/datasources/prometheus.yml`)이
host 네트워크 전환 이전의 `http://prometheus:9090`을 그대로 쓰고 있어 `127.0.0.1:12401`로
고쳤다 — 대시보드가 없어 지금까지 드러나지 않았을 뿐 실제 버그였다.

`kor-travel-weather`(독립 sibling, KMA/AirKorea 날씨 데이터 플랫폼)를 새 `weather`
target으로 등록했다. 원본 compose.yaml은 bridge 네트워크 DNS를 쓰므로 이 저장소의
host 네트워크 규칙에 맞춰 모든 내부 참조를 `127.0.0.1:<포트>`로 옮겼고, dagster
원본 webserver는 host 모드에서 dagster-gateway의 외부 포트(14102)와 겹쳐 내부 전용
`14106`으로 재배치했다. dagster-gateway의 nginx conf와 자체 Prometheus scrape
config는 bridge DNS를 하드코딩하고 있어 `config/weather/`의 host 모드 override
파일로 컨테이너 기동 시점에 덮어쓴다(원본 sibling 저장소는 건드리지 않음, Grafana
datasource provisioning과 같은 패턴).

메뉴 글꼴 불일치는 두 개의 별개 CSS 버그였다: (1) `.nav-button { font: inherit }`가
`.nav-link`의 `font-size: 0.75rem`과 동일 specificity·더 늦은 source order라 캐스케이드에서
이겨, 버튼 기반 메뉴 항목(운영 도구 그룹, 시스템 그룹, 사이드바 하단 로그아웃)만
브라우저 기본 버튼 글꼴 크기로 커졌다 — `font-family: inherit`으로 좁혀 해결.
(2) `.nav-title`이 `--font-sans`와 같은 폰트 3개를 다른 우선순위로 나열한
`--font-display`를 썼는데 실제로 로드되는 웹폰트가 하나도 없어 OS 설치 폰트에 따라
그룹 헤더만 다른 글꼴로 보일 수 있었다 — `--font-sans`로 통일(상속 삭제).

세 가지 모두 실제 컨테이너를 기동해 live로 검증했다: weather 7개 서비스 전부
healthy(Prometheus가 api·dagster 두 scrape target 모두 `up`), Concierge 5개
컨테이너 전부 기동(API/MCP/Scheduler/UI, `db-init` 성공 종료), Grafana datasource
health API가 `"Successfully queried the Prometheus API."`, 메뉴 글꼴은 컴파일된
CSS로 실제 DOM computed style을 측정해 버튼·링크·그룹 헤더가 동일 폰트
스택(12px/560/`Noto Sans KR,...`)으로 렌더링됨을 확인했다.

## 2026-09-05 — 회전이 weather target의 미완 등록에서 막혔다, 그리고 재부팅이 docker를 끄고 갔다

D2가 드러낸 helper 계약 결함을 고치고(Map #1156, PinVi #527) 새 pinset
`af6d7061`(Map `c72456f6` + PinVi `f4401659`)으로 회전했다. 그런데 rebuild가 2초 만에
`prejournal_failure` / `stage: prebuild_snapshot`으로 죽었다.

### 먼저: 재부팅이 docker를 끄고 갔다

진단 중에 `docker ps`가 소켓 부재로 실패했다. 확인해 보니 n150이 **2026-09-05 00:12:00에
재부팅**됐고(dockerd 마지막 로그 09-04 22:18), `docker.service`·`docker.socket`이 **둘 다
disabled**라 아무도 띄우지 않았다. 상시 서비스(weather/concierge/geo/pinvi/map/prometheus)가
전부 내려가 있었다.

`systemctl start docker`로 복구했고 restart 정책이 컨테이너를 전부 되살렸다. 그리고
**`docker.socket`·`docker.service`를 enable**했다 — 어제 `ktdm-frontend`가 없어서 재부팅을
못 넘긴 것과 **정확히 같은 계열**이고, 이 호스트의 모든 서비스가 docker에 얹혀 있는데 그것이
부팅에 안 올라오는 것은 잠재 장애다.

### rebuild가 죽은 진짜 이유

러너는 `stderr.log`를 0바이트로 남긴다(CLI가 사유를 가린다). c6c lock을 정식으로 잡고
`capture_transaction_unlocked`를 직접 돌려 예외를 꺼냈다. 두 단계로 나왔다.

1. `compose candidate secrets.kor-travel-weather-metrics-token environment is unresolved`
   — Manager #318이 weather secret 둘을 compose에 선언했는데 `.env`에 대응 값이 없었다
   (`KOR_TRAVEL_WEATHER_METRICS_TOKEN`, `KOR_TRAVEL_WEATHER_POSTGRES_PASSWORD`).
   값은 `/home/digitie/kor-travel-weather/.env`에 있었다(후자는 `POSTGRES_PASSWORD` 이름으로).
   두 값을 n150 안에서 직접 옮겨 넣었다(백업 `.bak-pre-weather-keys-…`, sha256
   `f2f6c0aa` → `a5a85312`).
2. `compose candidate kor-travel-weather-db bind source does not exist`
   — **여기서 멈췄다.**

### 멈춘 이유 — 데이터 소유권 판정이다

Manager compose는 weather DB를 **bind**로 본다:

    ${KOR_TRAVEL_WEATHER_PGDATA:-/home/digitie/kor-travel-weather-data/pgdata}
      -> /var/lib/postgresql/data

그런데 **실행 중인 weather DB는 named volume**을 쓴다:

    /var/lib/docker/volumes/kor-travel-weather_weather-postgres/_data

즉 #318의 등록이 실제 배포와 다른 레이아웃을 전제한다. 빈 디렉터리를 만들면 검증은 통과하지만
Manager의 weather-db가 **실행 중인 것과 다른 빈 DB**가 되고 같은 호스트 포트(14100)에서
충돌한다. 이건 weather 스택의 데이터 소유권 문제이므로 임의로 정하지 않았다.

선택지는 셋이고 전부 소유자 판정이다.

- **(a) 모델을 현실에 맞춘다** — compose가 named volume을 쓰도록 고친다. 데이터 이동 없음.
- **(b) 현실을 모델에 맞춘다** — 실행 중 DB를 bind 경로로 이전한다. 다운타임과 데이터 이동.
- **(c) weather target을 pinned candidate에서 분리한다** — 등록을 되돌리거나 검증 범위에서 뺀다.

이것이 풀리기 전에는 `af6d7061` rebuild가 진행되지 않고, 따라서 attestation 재발행·D1·D2도
막힌다. 회전 자체는 이미 됐고 되돌릴 필요는 없다 — 같은 pinset을 새 output leaf에서 재시도할 수
있다고 러너가 명시한다.

## 2026-09-04 — 일회용 체크아웃의 첫 프로덕션 사이클이 통과했다

`e2e025`(Manager `b3217edc`, pinset `e6b52db4`)가 `status: passed`로 닫혔고, **끝난 뒤에도
봉인 트리가 그대로였다.** 이 수정이 목표한 것이 정확히 그것이다.

    m04_attestation_sha256            f08620a9…
    m05_attestation_sha256            37320bb5…
    runtime_provenance_sha256         25a80946…
    execution_identity_sha256         148f76b1…
    cleanup_failed                    false
    disposable_run_worktree_retained  false

실행 중 실측으로도 확인했다 — 일회용 체크아웃 `pinvi-run-b1049614…`가 uuid 접미로
0700·root 소유로 생성돼 pinvi bare에 세 번째 worktree로 등록됐고(핀 revision `357da18`),
그 시점에 봉인 트리 잔여물은 0건이었다. 종료 후:

    봉인 트리 잔여물          0건
    _validate_immutable_tree  pinvi ACCEPT / map ACCEPT
    일회용 worktree 등록      제거됨 (bare + 핀 worktree만 남음)
    일회용 디렉터리           제거됨
    격리 스택                 컨테이너 0개

### 새 증거가 실제로 쓸모 있었다

`disposable-run-worktree.json`:

    ignored_entries    3
    untracked_entries  0
    tracked_changes    0
    top_level_names    ["apps", "node_modules"]

적대 리뷰 #3의 지적("봉인 트리를 실행에서 빼면 gitignore 경로 쓰기를 관측하던 유일한
탐지기가 사라진다")이 옳았음을 첫 실행이 보여줬다. 이 세 건이 이전 두 사이클을 태운
잔여물과 같은 것이고, 이제는 삭제 전에 세어져 receipt에 남는다. `tracked_changes 0`은
실행이 추적 파일을 건드리지 않았다는 뜻이다.

### 우연히 확인된 것

`disposable_run_worktree_retained: false` — 적대 리뷰 #5를 받아들여 제거 실패를 실행
소각에서 분리했는데, 이번에는 제거가 성공해 그 분기를 타지 않았다. 분리 자체의 값어치는
실패했을 때 드러나므로 아직 관측되지 않았다.

## 2026-09-04 — pygit2 포팅은 하지 않는다 (조사 종결)

Manager의 git 접근(`pinned_runtime_sources.py`의 `_run_root_git` 18곳, `source_status.py`,
`scripts/m05_isolated_e2e.py`)을 subprocess에서 pygit2로 옮기는 안을 조사했다. **하지
않기로 한다.** 소유자 결정이며, 근거는 아래 실측이다. 다시 제안되지 않도록 남긴다.

### 결정적 이유 — worktree API가 libgit2에 없다

`nm -D` 실측: libgit2 1.9.6에 **`git_worktree_move`도 `git_worktree_remove`도 없다.**
그래서 `_promote_staging_worktree`와 `remove_disposable_run_worktree`/
`_cleanup_staging_worktree`의 git-owned 제거를 옮길 수 없다. `prune(force=True)`는
admin 엔트리만 지우고 **작업 디렉터리를 남긴다**(실측) — 우리가 방금 세운 계약과 정반대다.

`add_worktree`는 **로컬 브랜치만** 받는다. `--detach`가 불가능하므로 핀 worktree가
브랜치 위에 놓이고, 브랜치는 움직일 수 있다 — 핀 불변성이 약해진다. 우회하면 실행마다
유일한 브랜치를 만들어야 하고 비정상 종료 시 **stale 브랜치까지** 남는다(지금은 stale
admin 엔트리만 남는다).

그리고 libgit2는 **이미 등록된 경로에 두 번째 worktree를 만드는 것을 막지 않는다**(실측).
`rm -rf` 뒤 다른 이름으로 같은 경로에 add하면 성공하고, 원래 stale 등록이
`is_prunable=False`로 뒤집혀 판정이 영구히 오염된다. 즉 어제 우리가 넣은
`_is_registered_worktree` 우회 판정은 **사라지기는커녕 필수가 된다.**

### 기대했던 이점 둘이 무너졌다

- **오류 식별**: `GitError`에 code도 klass도 없다(실측 `attrs = ['add_note','args',
  'with_traceback']`). 구분은 여전히 문자열 비교다. 게다가 어제 우리를 괴롭힌
  `missing but already registered`는 **git CLI의 검사이지 libgit2의 검사가 아니다.**
- **성능**: 실행당 git 프로세스 오버헤드 총합 약 50 ms. 같은 harness 실행 시간은
  1시간 39분이다(0.001% 미만). `add_worktree`·`status`는 오히려 CLI가 빠르다.
  `_validate_immutable_tree`는 애초에 git이 아니라 `os.walk`다.

### 보안은 순손실

hook 미실행·credential helper 미실행은 libgit2의 구조적 우위지만 **이미 봉인돼 있어
신규 이득이 아니다.** 반면 `protocol.*.allow` 등가물 부재(`file://`·`git://` 상시 활성),
경로별 `-c safe.directory=<경로>`가 **전역 `owner_validation=False`로만** 대체,
`source_status.py`의 `timeout=5.0` 상실, `_drop_privileges`의 uid 강등이 in-process로
불가능, 그리고 설정 봉인이 프로세스 전역이라 장수하는 FastAPI 백엔드 안에서 한 줄로
되돌려질 수 있다. 배포 비용도 있다 — wheel이 자체 OpenSSL 3/libssh2를 번들해 `apt`
보안 갱신 경로 밖으로 나가고, offline wheelhouse(`--no-index`) 계약과 CPython ABI 핀에
새 제약이 생긴다.

### 조사가 찾은 것 중 실제로 고친 것

**1. `summarize_disposable_run_worktree`의 porcelain 파싱이 비-ASCII 경로를 깨뜨렸다.**
어제 넣은 코드의 진짜 버그다. 기본 출력은 `core.quotePath` 때문에 경로를 8진
이스케이프로 감싸는데 파서가 그것을 풀지 못해 한글 파일명이 증거 JSON에
`\355\225\234…`로 실렸다. rename 엔트리(`R  <new>\0<old>`)의 원본 경로도 상태 코드로
오독해 이름 집합을 오염시켰다. `-z`(NUL 구분)로 바꿔 파싱 규칙 자체를 없애고, rename의
두 번째 레코드를 건너뛰게 했다. 게이트 3건이 이 변이를 잡는다.

**2. `GIT_OPTIONAL_LOCKS=0`을 두 env에 추가했다 — 다만 주장은 축소한다.**
`source_status.py`는 처음부터 이 플래그를 걸고 있었고 이 모듈만 빠져 있었다. 조사는
"봉인 트리 검증이 admin index를 쓴다"고 보고했고 맨 셸에서는 재현됐다(mtime 갱신 뒤
plain=YES / 플래그=NO). **그러나 `_run_root_git`을 통과하는 실제 호출에서는 플래그
유무와 무관하게 index가 그대로였다**(직접 재측정). 그래서 동작 게이트를 붙이지
않았다 — 통과·실패가 갈리지 않는 게이트는 없느니만 못하다. 대신 기존
`test_pinned_runtime_sources.py`의 **env 정확일치 단언**이 이 추가를 고정한다. 남기는
이유는 방어 심층화와 두 모듈의 계약 일치이며, 관측되지 않은 효과를 주석에 적지 않았다.

서브에이전트 보고를 그대로 싣지 않고 재측정한 결과가 이 축소다.

## 2026-09-04 — 불변 트리 오염을 B안으로 고치고, 적대 리뷰 2인의 지적을 반영했다

실행을 봉인된 핀 worktree가 아니라 **일회용 체크아웃**에서 하도록 바꿨다. 그 체크아웃은
디스크 사본이 아니라 같은 bare 저장소의 object store에서 **재유도**한다 — 같은 revision,
같은 tree object이므로 파일 모드도 잔여물도 물려받지 않는다. 앞선 리뷰가 D(`cp -a`)를
기각한 사유(사본이 git admin dir을 공유한다)를 피하는 지점이 여기다.

PinVi 쪽 변경은 필요 없다. attestation과 러너가 각각 자기 `__file__`/스크립트 위치에서
repo root를 유도하고 `PINVI_PLAYWRIGHT_RUNNER_REPO_ROOT`를 무조건 덮어쓰므로, 하네스가
그 둘을 일회용 루트에서 실행하면 체인 전체가 따라온다(2026-09-04 §3의 패턴 그대로).
compose root와 build context는 봉인 트리를 계속 본다 — 읽기 전용 소비이기 때문이다.

### 적대 리뷰 2인이 바꾼 것

초판(`eea0618`)에 정확성·보안 두 각도로 리뷰어를 붙였고 각각 **BLOCK**과
**FIX-BEFORE-MERGE**를 냈다. 둘 다 실측으로 근거를 댔고, 다섯 가지를 고쳤다.

1. **중단된 실행이 남긴 등록을 하네스가 스스로 못 지웠다.** `main()`의 `finally`는
   SIGTERM/SIGHUP에서 돌지 않는데, 초판 `remove_disposable_run_worktree`는 디렉터리가
   없으면 그냥 돌아가서 admin 등록을 영구히 남겼다. 그러면 같은 경로의 다음
   `worktree add`가 `missing but already registered`로 죽는다. destination에 uuid를
   붙여 경로 충돌 자체를 없애고, 제거는 경로가 없어도 시도하며 **성공 판정을 exit code가
   아니라 등록이 사라졌는가**로 바꿨다(`worktree remove --force`는 경로가 사라진
   엔트리도 지운다 — 실측).
2. **테스트가 이 수정을 통째로 되돌려도 green이었다.** 초판 게이트는
   `_validate_immutable_tree`만 import하고 raw `git`으로 git 자체의 동작을 확인했다.
   더 나쁜 것은 full-path 스텁이 `materialize_disposable_run_worktree`를 **봉인 루트를
   그대로 돌려주도록** 대체해, 실행-루트 단언 전부가 무의미해진 것이다. 게이트를 프로덕션
   함수 직접 호출로 바꾸고 스텁이 다른 경로를 돌려주게 했다. 변이 검증: 제거 early-return
   복원과 tree 자기참조 복원에 각각 1건, 실행-루트 치환 **한 곳**만 되돌리면 20건이 red다.
3. **일회용 디렉터리 삭제 실패가 통과한 1.5시간짜리 실행을 태웠다.** 컨테이너 tmpfs 마운트
   잔존(EBUSY)이나 ENOSPC 하나로 `cleanup_failed`가 켜지고 pinset이 소각되며 attestation
   해시가 버려졌다 — 이 수정이 막으려던 바로 그 손실이다. 제거 실패는 실행을 태우지 않는
   receipt 필드(`disposable_run_worktree_retained`)로 강등하고, **봉인 파손만** 태운다.
4. **사후조건이 위험하지 않은 트리를 검사하면서 그 한계를 과대주장했다.** 봉인 트리를
   실행에서 뺀 뒤로는 gitignore 경로 쓰기를 관측하던 유일한 탐지기가 사라진다(§2). 삭제
   **전에** `--ignored=matching`까지 세어 receipt 증거로 남기게 했다. 사후조건 독스트링에는
   그것이 모드 검사이며 root의 in-place 내용 변조는 잡지 못한다는 §1의 사실을 적었다.
   검사 대상도 pinvi 한 role에서 map까지 둘로 넓혔다.
5. **기대 tree가 자기참조였다.** 같은 bare에서 다시 유도해 비교하면 git 결정성만 확인한다.
   `materialize_pinned_runtime_sources`가 이미 검증한 값을 preflight가 넘기도록 바꿔야
   비로소 핀과의 결박이다.

### 운영 선행조치 — 머지만으로는 다음 실행이 되지 않는다

**활성 pinset `e6b52db4`의 봉인 PinVi 트리는 지금 오염돼 있다**(e2e24가 다시 만들었다).
이 코드는 `materialize_pinned_runtime_sources`보다 **뒤**에서 도는데 거부는 그보다 앞에서
일어나므로, 그대로 머지하면 새 코드가 한 줄도 실행되지 않은 채 `source_materialization`
에서 끝난다. 같은 오염이 비활성 pinset 4개에도 있다.

조치는 `rm -rf`가 **아니다.** 디렉터리만 지우면 등록이 남고 다음 materialize의
`worktree move`가 죽는다(실측). 유일하게 등록까지 지우는 방법은

    git --git-dir=<bare> worktree remove --force <봉인 worktree>

다. 그 fatal을 일반 Git 실패로 접지 않고 조치까지 말하도록 `_promote_staging_worktree`에
선판정을 넣었다. 자기치유로 넣지 않은 이유는 §2 그대로다 — 상시 제거는 유일한 탐지기를
무음화한다. 1회성 운영 조치로 간다.

**실행했다(2026-09-04).** 활성 pinset `e6b52db4`의 PinVi 봉인 트리에서 잔여물 4개
(`node_modules`, `apps/web/node_modules` 864K, `apps/web/test-results`,
`apps/web/playwright-report`)를 제거했다. 봉인 worktree 자체는 지우지 않았으므로 등록
문제는 발생하지 않았고, `git worktree remove --force`도 필요 없었다. 제거 후 실측:

    pinvi   ACCEPT — immutable tree is sealed
    map     ACCEPT — immutable tree is sealed
    git status --porcelain --untracked-files=all   (빈 출력)
    HEAD                                           357da1897c2df2c86e5f3376e212451cf0f019ab

검사는 설치된 Manager의 `_validate_immutable_tree`를 그대로 돌려서 했다 — 다음
preflight가 쓸 바로 그 코드다. 다른 pinset 24개의 worktree에는 잔여물이 없다(활성
pinset만 오염돼 있었다). `rm -rf` 대신 잔여물만 지우는 쪽을 택한 이유는 그것이 최소
조치이고 봉인 worktree의 재materialize를 요구하지 않기 때문이다.

### 수용한 비용

`git worktree list` 관측이 Manager에 없다는 §4 B의 지적은 남아 있다. destination이
실행마다 유일해지면서 **경로 충돌로 죽는 경로는 없어졌지만**, 하드킬된 실행의 등록은
누적된다(prune 금지). 지금은 받아들이고, 누적 점검을 백로그에 둔다. 중단된 실행이 output
leaf에 PinVi 체크아웃 전체를 남기는 것도 같다 — n150은 74G 여유다.

## 2026-09-04 — 불변 트리 수정안 리뷰가 확립한 것

격리 하네스가 핀 소스 worktree를 오염시켜 같은 pinset 재실행이 막히는 문제에 대해
전문 리뷰어 5인(설계 3 + 적대 검증 2)을 붙였다. **합의는 나오지 않았다** — 설계
권고가 A(overlayfs)·B(일회용 git worktree)·D(일회용 복제본)로 갈렸고 검증 2인이
모두 `NEEDS-CHANGE`를 냈다. 그러나 리뷰가 확립한 사실 넷은 다음 착수의 출발점이다.

### 1. `_validate_immutable_tree`는 내용 검사가 아니라 **모드 검사**다

root는 CAP_DAC_OVERRIDE로 0444 파일을 모드 변경 없이 덮어쓸 수 있고, 0555인 ignored
디렉터리는 세 검사(`_validate_immutable_tree`, `_assert_worktree_clean`, 러너의
`assert_exact_live_checkout`)를 **모두 통과한다**(실측). 따라서 "이 검사가 유일한
tripwire이므로 보존해야 한다"는 A의 핵심 논거는 생각보다 약하다. 검증자는 판단 기준을
**"run tree를 object store에서 재유도하는가"**로 바꾸라고 했고, 그 기준에서는 B만
재유도하고 A·D는 디스크 트리를 물려받는다.

### 2. gitignore 경로 쓰기를 잡는 것은 모드 검사뿐이다

`node_modules/`·`test-results/`는 `.gitignore`에 있고 `playwright-report`는 빈
디렉터리라, 러너의 `--untracked-files=all`도 attestation의 `_assert_clean_checkout`도
넷 전부에 대해 눈이 멀어 있다. 그래서 **사후 삭제(C안)는 증거 세탁**이다 — 유일한
탐지기가 쓸 증거를 정확히 지우고 성공 경로의 fail-close를 fail-open으로 뒤집는다.

### 3. `PINVI_PLAYWRIGHT_RUNNER_REPO_ROOT`는 이미 확립된 패턴이다

`m05_activation_attestation.py`가 `os.environ.copy()` **뒤에** 자기 `__file__`에서
유도한 값으로 무조건 덮어쓰고, 그 경로에 실행 전후로 `_assert_clean_checkout`을 건다.
즉 권한 근거는 env가 아니라 **핀된 스크립트의 자기 위치 + git identity 검증**이다.
"caller 환경변수는 권한 근거가 아니다" 규칙과 충돌하지 않는다.

### 4. 각 후보안의 차단 사유

- **A(overlayfs)**: 사후 `_assert_clean_checkout`이 여전히 핀 트리를 보므로 컨테이너가
  실행한 트리와 검증한 트리가 갈라진다 — 보강 없이는 **회귀**다. merged 경로로 재검사하고
  upperdir를 write manifest로 증거화해야 비로소 현재보다 강해진다. 마운트 네임스페이스
  함정도 있다(private ns에서 마운트하면 dockerd가 조용히 lower를 bind한다).
- **B(일회용 worktree)**: 복사 비용은 실측 0.2초/20MB로 무시할 만하다. 다만 Manager에
  `git worktree list`가 없어 여분 worktree가 **보이지 않고**, `git worktree prune`이 운영
  금지라 제거 실패 시 admin 엔트리가 영구 누적된다.
- **D(cp -a 복제본)**: 사본이 핀 worktree의 git admin dir(index/HEAD)을 **공유한다**(실측
  inode 일치) — B를 기각한 바로 그 사유에 정면으로 걸린다.

### 5. 착수 전 결정이 필요한 것

현재 pinset의 worktree는 **지금 오염돼 있다**(e2e24가 다시 만들었다). pinset은
map+pinvi revision에서만 파생되므로, 어떤 수정을 넣든 그 수정의 첫 실행이 preflight에서
거부되고 leaf·receipt·ledger가 없는 자리라 또 한 사이클을 태운다. 제거를 자기치유로
넣으면 2번의 유일한 탐지기를 상시 무음화하므로 안 되고, 1회성 운영 조치로 간다면
`git worktree prune` 금지 하에 stale 엔트리를 받아들이는 결정을 명시해야 한다.

### 부수 발견

러너의 `assert_exact_live_checkout`에는 `GIT_OPTIONAL_LOCKS=0`이 없다(attestation의
`_git_env()`에는 있다). 핀 worktree의 index를 실행 중에 건드릴 수 있는 경로다.

## 2026-09-03 — 격리 M04/M05 live E2E가 통과했다

pinset `e6b52db4`(Map `8078b110` + PinVi `357da189`), Manager `5befecbb`에서
`run-m05-isolated-e2e-once`가 `status: passed`로 닫혔다.

    phase                       completed
    m04_attestation_sha256      d5f0c4d0…
    m05_attestation_sha256      69fb285e…
    runtime_provenance_sha256   bac562a0…
    cleanup_failed              false

실패 증거 파일은 하나도 없다. 상시 서비스 9개는 그대로였고 격리 스택은 완전히
정리됐다.

### 벽은 하나가 아니었다

같은 날 앞선 시도들이 각각 다른 이유로 죽었고, 그 이유들이 서로 다른 층에 있었다.

- **침묵사 3회** — 하네스와 런처가 `python -I`로 도는데 `-I`가 `-E`를 함의해
  `PYTHONUNBUFFERED`가 무효다. 로그인 세션에 매달아 두면 죽을 때 버퍼째 사라진다.
  `systemd-run`으로 옮기자 세 번의 침묵이 한 줄로 드러났다.
- **claim 소각** — 해제가 자기 프로세스의 분류기에만 걸려 있어, 프로세스 그룹이
  죽으면 소각이 기본값이 아니라 **유일한 결과**였다(#309에서 되찾기 도입).
- **다중 타깃 bake** — PinVi `docker-app.sh`가 두 타깃을 한 BuildKit 요청으로
  묶어 작은 호스트의 세션 한도를 넘겼다(PinVi #523).
- **계약이 운영 표면과 달랐다** — `/v1/debug` 라우트가 `debug_routes_enabled`
  뒤에 있었고 그 기본값은 `true`인데 배포 이미지는 production으로 돈다. 계약이
  운영이 제공하지 않는 라우트를 기술해 attestation이 구조적으로 통과 불가였다
  (Map #1140에서 라우트 제거, PinVi #526에서 재벤더).

### 이번 사이클이 새로 드러낸 것 둘

**preflight가 침묵했다.** `except (OSError, RuntimeError, ValueError): return 1`
분기가 아무것도 출력하지 않아 launcher가 `is not runnable:` 뒤에 빈칸을 찍었다.
계측 스크립트를 따로 붙여서야 사유를 알았고 한 사이클을 더 썼다(#312).

**그 사유가 불변 핀 소스 트리 오염이었다.** 하네스가 `cwd=pinvi_root`로
npm/playwright를 돌려 `node_modules`·`apps/web/node_modules`·`playwright-report`·
`test-results`를 0555 트리 안에 쓴다(root라 mode를 무시한다). Manager의 불변성
검사는 정당하게 거부한 것이다.

이것은 **pinset마다 worktree가 새로 생기기 때문에 지금까지 보이지 않았다** —
같은 pinset을 재실행하려는 순간에만 드러난다. 오늘 e2e를 여러 번 돌리면서 처음
부딪혔다. 오염 넷을 걷어내 preflight를 통과시켰고, 근본 수정은 러너의 마운트
설계에 걸리므로 `docs/tasks.md`에 올렸다.

### 실패한 명령의 stdout을 남기기 시작했다

e2e22는 `M04 live UI command exited with 1` 하나만 남기고 1시간 39분을 태웠다.
Playwright는 어느 spec의 어떤 단언이 깨졌는지를 **stdout**으로 내는데 하네스는
stderr만 잡았고, 거기엔 npm의 lifecycle 오류뿐이었다. #311에서 두 스트림을 같은
scrub·같은 상한·같은 root 0600 leaf 규칙으로 남기게 했다.

## 2026-09-03 — tasks.md에서 이관한 조사 기록

`docs/tasks.md`의 머리말은 활성 작업을 **한 줄씩** 두고 실행 근거는 이 파일이
정본이라고 규정한다. 그런데 미완 항목 셋이 각각 1천~6천 자 조사 기록을 그 자리에
쌓아 두어 활성 목록을 읽을 수 없게 만들고 있었다. 본문을 여기로 옮기고 태스크는
한 줄로 남긴다 — **내용은 한 글자도 줄이지 않았다.**

### atomic-write 프리미티브 잔여 통합

atomic-write 프리미티브 잔여 통합 (GM-10 후속, 일부 완료) — `pinvi_bootstrap_credential.py`의 `_fsync_directory_descriptor`(디렉터리 fsync 실패가 이미 성공한 credential 파일을 바깥 `except BaseException`의 zeroize+unlink로 파괴하던 버그)는 이번 세션에서 고쳤다: **여섯 호출부 전부**를 best-effort로 대칭화하고 `best_effort` 파라미터는 제거했다(모든 호출부가 같아져 죽은 분기가 됐다). 처음에는 두 자리만 낮췄는데, 적대 리뷰 2인이 독립적으로 같은 사슬을 재현했다 — 생성 쪽(`os.mkdir` 직후)이나 삭제 쪽(`unlink`/`rmdir` 직후)에서 raise하면 되돌릴 것이 없는 채로 중단되어 **빈 transaction 디렉터리**가 남고, 그러면 `_validate_exact_transaction_contents`가 이후 모든 orphan 정리를 영구 fail-close해 운영자가 손으로 rmdir하기 전까지 PinVi rebuild가 막힌다. 특히 `os.mkdir` 쪽 누수는 이 PR 이전부터 있었으므로 되돌리기만으로는 사슬이 닫히지 않는다. 네 자리를 각각 strict로 되돌리면 실패하는 회귀 테스트 2건을 붙였다(`test_transaction_directory_fsync_failure_does_not_leak_an_orphan`, `test_cleanup_directory_fsync_failure_still_removes_the_transaction`) — 종전 테스트는 credential 파일 fsync 관측 **이후**의 디렉터리 fsync만 실패시켜 네 자리를 전혀 덮지 못했다. 심각도 서술도 정정했다: `except BaseException`의 zeroize+unlink는 버그가 아니라 **계약상 올바른 fail-close 시크릿 위생**이고, 실제 피해는 파괴가 아니라 **가용성**이다(rebuild가 중단되고 그 claim이 탄다). mkstemp 자리 9곳 중 처음에는 2곳(`compose_service.py`의 `_atomic_restore_compose_source`, `standalone_backup.py`의 `_atomic_write_bytes`/`_atomic_write_json`)을 정본 `atomic_write_bytes`/`atomic_write_json`으로 옮겼으나, 적대적 리뷰(2인)에서 `compose_service.py` 쪽 이관이 회귀임이 드러나 되돌렸다 — 이 자리의 디렉터리 fsync 실패는 `_recover_persisted_target_runtime`이 `recovery_succeeded`를 판정하는 유일한 신호원인데, 옛 인라인 구현은 uncaught raise라 호출자의 `except Exception`이 그 실패를 잡아 `recovery_succeeded=False`로 정확히 보고했지만, 정본 `atomic_write_bytes`가 쓰는 `fsync_directory`는 디렉터리 fsync 실패를 무조건 삼켜(best-effort) 같은 상황에서 함수가 정상 반환하고 `recovery_succeeded=True`로 오보되는 회귀였다 — `legacy_override_retirement.py`/`pinvi_database_role_credentials.py`의 `_write_atomic`을 애초에 옮기지 않은 이유와 동일한 성격의 문제를 놓친 것이다. 원래의 strict 인라인 구현으로 되돌리고 `test_compose_service_atomic_restore.py`에 회귀 테스트를 추가했다(mutation-test로 되돌리기 전 버전이 실제로 이 테스트를 통과 못 시킴을 확인). `standalone_backup.py` 쪽은 원래 디렉터리 fsync가 전혀 없었던 자리라 이관이 durability를 오히려 추가하는 방향이라 그대로 뒀다 — 다만 정본 `atomic_write_json`이 `ensure_ascii=True`를 하드코딩해 옛 인라인의 `ensure_ascii=False`에서 조용히 바뀐 점은 리뷰에서 지적됐다: `BackupManifest.to_json()`의 필드(역할 enum·정수/실수·hex sha256·머신 생성 파일명)가 현재 전부 ASCII라 오늘은 바이트 차이가 없지만, 앞으로 비-ASCII 필드가 추가되면 이스케이프된 `\uXXXX` 표현으로 조용히 바뀔 수 있다는 점을 여기 명시적으로 기록해둔다. 문서화되지 않았던 10번째 자리 `runtime_pin_request.py`의 `_atomic_write_json`도 한 차례 이관했다가 **되돌렸다** — 이관 자체의 결함이 아니라 **대상이 죽은 코드**이기 때문이다: `replace_existing=True`를 켜는 프로덕션 호출자가 저장소에 없고(`api/routes.py`는 기본값을 쓴다), 그 플래그는 같은 함수 docstring이 명시한 계약(*기존 요청을 조용히 덮어쓰지 않는다*)과 정면으로 모순된다. 이관하고 회귀 테스트까지 얹으면 그 초록 테스트가 '지원되는 동작'으로 읽혀 감사 붕괴를 부른다는 적대 리뷰 지적을 받아 이관과 테스트를 함께 철회했다(현재 `backend/tests`에 `replace_existing` 테스트는 없다). 살아 있는 경로 `_exclusive_write_json`의 인라인 포맷은 그대로라 **이 파일에서 GM-10 목적(포맷 정의 단일화)은 미달성으로 남는다** — 후속은 이관이 아니라 `replace_existing` 플래그 자체의 제거다. 결과적으로 GM-10 태그가 붙은 9곳 중 실제로 이관된 것은 `standalone_backup.py` 1곳뿐이고, 나머지 8곳은 개별 검토 결과 정본 시그니처에 맞지 않아 남겨뒀다: (1) `admin_password_service.py` — os.replace 직전 `.env` identity 재검사(TOCTOU 방지, `_identity`가 크기·ctime까지 봄)가 있어 단순 write+replace로 축약할 수 없다. (2) `legacy_override_retirement.py`의 `_canonical_concierge_compose_projection` — 발행용이 아니라 Concierge compose projection을 만들어 `docker compose --file`에 넘겼다가 scope 종료 시 unlink하는 scratch 파일이라 os.replace 자체가 없다. (3) `legacy_override_retirement.py`의 `_write_atomic`과 (4) `pinvi_database_role_credentials.py`의 `_write_atomic` — 둘 다 replace 성공 후 디렉터리 fsync가 실패하면 각각 `LegacyRootEnvironmentDurabilityError`/"PinVi root environment durability is uncertain"으로 승격하는 의도된 strict 계약이고(전자는 `test_legacy_override_retirement.py`가 고정) 정본의 `dir_fsync`는 best-effort라 그대로 옮기면 이 계약을 무너뜨린다. 둘 다 replace 전후 identity 재검사도 있다. (5)(6) `map_application_300.py`의 `write_owner_only_artifact`/`publish_root_read_only_artifact` — os.replace가 아니라 os.link(hardlink)로 발행한다. (7) `map_application_300.py`의 `replace_root_read_only_artifact` — os.replace를 쓰지만 같은 파일의 `_fsync_directory`(uncaught raise)를 hardlink 쓰는 두 자리와 공유한다 — 이 하나만 best-effort로 낮추면 한 모듈 안에서 fsync 실패 처리 방식이 갈리므로 개별 보안 검토 없이 그렇게 하지 않았다. (8) `compose_service.py`의 `_atomic_restore_compose_source` — 위에서 서술한 대로 `recovery_succeeded` 신호를 지키기 위해 strict 인라인 구현으로 남겨뒀다.

### LoginScreen.tsx가 humanizeError

LoginScreen.tsx가 humanizeError/CODE_MESSAGES를 쓰지 않음 (admin-auth-envelope 적대적 리뷰 2인 발견) — `admin.py`/`auth.py`의 bare-string `HTTPException(detail="TOKEN")` 5곳을 `{code, message}` 구조화 봉투로 바꾼 수정(3d0a740d) 이후에도, `frontend/src/components/LoginScreen.tsx`는 `humanizeError`/`CODE_MESSAGES`를 전혀 부르지 않고 `err.status`(401/403/429/503)만 보고 하드코딩된 한국어 문구를 직접 분기한다. 그 결과 `auth.py`의 RATE_LIMITED/AUTH_MISCONFIGURED/INVALID_CREDENTIALS 세 곳(다섯 곳 중 세 곳)은 봉투를 구조화해도 로그인 화면에 관측 가능한 차이가 없다 — 실제로 이 경로를 고치는 것은 `admin.py`의 두 곳(`AdminSettingsPanel`이 `humanizeError`를 쓰는 곳)뿐이다. LoginScreen을 다른 패널처럼 `humanizeError`로 옮기는 것 자체는 이번 라운드에서 하지 않았다 — `require_frontend_origin`(`auth_service.py:95`, 403 INVALID_ORIGIN)이 여전히 bare 문자열이고 `CODE_MESSAGES`에도 없어, 그대로 옮기면 `humanizeError`가 `serverMessage`로 폴백해 지금 잘 나오는 "허용되지 않은 요청입니다..." 대신 원문 토큰 "INVALID_ORIGIN"을 노출하는 새 회귀가 생긴다. 착수 시 `require_frontend_origin`의 봉투화와 `INVALID_ORIGIN`의 `CODE_MESSAGES` 추가를 먼저 하고 나서 LoginScreen을 옮겨야 한다.

### GM-17 본작업

GM-17 본작업(required-set 완화 + bind allowlist 외부화) 미진행 — production compose candidate 검증(`c6c_deployment.py`)의 Map/PinVi 14(실질 15)개 서비스 존재 강제를 dev ensure에서 완화하고, `_CANDIDATE_ALLOWED_OPERATOR_BINDS` bind allowlist를 root-owned 설정 파일로 옮기는 작업(`docs/general-mgmt-audit.md` GM-17 검증 노트 참고)은 오너 결정으로 이번 세션에서 진행하지 않았다 — production 보안 경계를 직접 건드리고 effort L(검증 노트 자체가 "그 이하로 축소 불가"라고 명시)이라 review 없이 자동 진행하기에는 위험도가 다르다고 판단해 확인을 구했고, 오너는 가장 안전한 부분(`.env.example` 플레이스홀더 정리)만 승인했다. bind allowlist를 옮기려면 새 root-owned 파일에 대한 소유권·권한 검증 인프라(pin registry식)를 먼저 만들어야 하고(안 그러면 그 자체로 보안 회귀), required-set 완화는 다수 cross-service validator를 존재-조건부로 바꾸는 광범위 감사가 필요하다. 착수 전 오너와 범위를 재확인할 것.

## 2026-09-02 — 71분을 태운 뒤에야 문자열 두 개가 다르다고 했다

rebuild가 성공하고(pinset `4516a107`, 71분) e2e17을 돌렸더니 **몇 초 만에**
preflight가 거부했다: `M05 isolated source pair preflight is not runnable`.

### 진단에 걸린 시간이 결함이었다

launcher는 preflight stderr를 `>/dev/null`로 버리고, `preflight()`는 예외를
전부 삼켜 exit 1만 냈다. 단계를 손으로 나눠 돌려 `_pair`까지 좁히고, 거기서도
`pair_contract_invalid`를 내는 **15곳이 전부 진단 없이** 같은 문자열만 내서
traceback으로 줄번호를 역추적해야 했다.

실제 원인: PinVi의 pair 계약이 Map `4f268633`을 지목하는데 pinset은
`f58de9f4`를 핀했다. 그 사이는 **문서 3건 + 테스트 1건**이고 OpenAPI blob은
바이트 단위로 동일하다. 계약의 digest는 여전히 정확했고 어긋난 것은 라벨뿐이다.

### 세 각도로 판정했다 — 검사를 지우는 것은 오답

- **탐지력은 0이다.** `full.source_revision`은 `_pair` 밖으로 전파되지 않고,
  OCI 라벨 검사는 pinned 값끼리의 항등식이다. 주석이 주장하던 "라벨을 full
  기준으로 결박한다"는 코드상 거짓이었다.
- **그런데 지우면 유일한 계약↔릴리스 결박이 사라진다.** `_pair`의 digest 대조는
  **계약이 스스로 지목한 revision**에 앵커돼 있어 "계약은 자기무모순이다"만
  증명한다. 1690행을 지우면 Map 이력의 임의 커밋을 담은 계약이 통과한다.
- **그리고 실패가 무료에서 유료로 이동한다.** PinVi는 같은 동등성을 body 안에서
  세 번 재강제하는데(`attestation:594`, OCI 라벨 3개, `_assert_clean_checkout`의
  집합 멤버십) 전부 `body_entered=True` 이후라 **무조건 소각**이다. 이 트랙의
  원칙("소각은 양성 증거가 있을 때만")을 정면으로 위반한다.

과결박은 맞다. 다만 그 위치는 이 검사가 아니라 **계약 스키마의 `source_revision`
필드**다. 함정 감지기를 떼면 함정만 남는다.

### 그래서 검사를 앞으로 당겼다

오늘의 손실은 검사가 있어서가 아니라 **검사가 늦어서** 났다. `rotate-pair`는
계약을 보지 않고, `run-pinned-rebuild-once`도 보지 않는다. 문자열 두 개 비교로
0초에 판정 가능한 모순이 71분을 태운 뒤에 드러났다.

`scripts/rotate-pinned-pair`가 회전 **전에** 대상 PinVi revision의 계약을 shallow
fetch로 읽어 그 둘을 비교하고, 어긋나면 두 값을 찍고 회전을 거부한다. 원장은
그대로다. M05 adapter가 소유하는 검사이므로 `ktdctl` 본체가 아니라 driver에
두고 launcher가 엮는다.

### 진단도 함께

`pair_contract_invalid` 15곳에 닫힌 어휘 진단을 부여하고, `preflight()`가 그걸
**allowlist로 걸러** stdout에 내며, launcher가 그 값을 stderr 메시지에 싣는다.
이 경로는 output leaf 이전이라 forensic scrub 채널을 못 쓴다 — 그래서 노출은
닫힌 어휘로만 한다. 그 경계를 테스트가 건다(allowlist 밖 문자열은 phase만 난다).

### 남은 것

계약 스키마 v2에서 `source_revision`을 걷어내는 것(근본, PinVi 3개 검사 재설계
동반). 그때까지는 Map 문서 커밋마다 재핀이 필요하고, 이번이 **네 번째**다.

## 2026-09-02 — 소각 판정을 선언에서 관측으로, 실측이 두 번 범위를 넓혔다

앞 항목이 후속으로 남긴 `classification: unclassified` 구멍을 닫았다. 그런데
이건 이론적 경로가 아니었다 — **고치는 도중 실제 rebuild가 그 구멍을 밟았다.**

### 실측

09:35 UTC rebuild(pinset `cc3c516f`)가 29분 실행 후
`{"status":"failed","classification":"unclassified"}`로 닫혔다. `stderr.log`는
**0바이트** — `--json`이 원문을 내지 않는다. journal은 없고
`generation_public_copy`는 `pending_rebuild`. 아무것도 소비하지 않았는데
launcher는 claim을 유지했다.

비-JSON으로 한 번 더 돌려 원문을 얻었다 — `pinned runtime rebuild Compose build
command failed (exit 1)`. 후보 이미지 넷이 서 있었고 다음 순서는 `pinvi-web`.
근본 원인은 PinVi 쪽 의존성 트리 드리프트였다(digitie/pinvi#518): deps
스테이지가 workspace manifest를 손으로 나열하다 `apps/mobile/package.json`을
빠뜨렸고, `npm install`이 그 불일치에서 lockfile 트리를 조용히 버렸다.
**Manager에서 고치던 것과 같은 결함 클래스** — 한 사실이 두 곳에 독립 선언되고
둘을 묶는 기계가 없다.

### 열거를 두 번 시도하고 두 번 틀렸다

첫 수정은 봉인 밖 지점마다 stage를 붙이는 것이었다. 두 곳을 고친 직후 AST로
세어 보니 세 번째(`c6c_deployment_lock` 획득 자체)와 네 번째가 나왔다. lock
획득은 `with` 문으로는 표시조차 불가능하다 — 같은 블록이 `yield`까지 감싸
journal **이후** 실패도 지나간다.

그래서 선언을 버리고 **관측**으로 갔다. journal 경로를 알게 되면 적어 두고
실패 시 그 파일의 존재를 본다. 누가 언제 썼든 관측이 답을 주므로 앞으로
write가 늘어도 판정이 저절로 맞는다.

그런데도 AST 측정에서 `if journal_exists: … else:`를 "resume 검증"으로 읽고
넘겼다. `else`가 fresh 빌드 경로라는 것 — 이 흐름에서 가장 오래 걸리고 가장
잘 실패하는 구간이 통째로 봉인 밖이라는 것 — 은 실행이 가르쳐 줬다.

### 적대 리뷰 2인이 이 수정 자체에서 찾은 것

- **관측이 terminal block 게이트보다 늦었다.** 그 게이트는 바로 "이미 소비된
  후보"를 거절하려고 있는데, 관측 전에 두면 그 거절이 "도달한 적 없음" 통에
  담겨 **소비된 후보의 claim을 해제**한다. 정확히 반대 방향이었다.
- **봉인된 실패가 관측을 우회했다.** 봉인 단계는 전부 resume 분기보다 앞에서
  돌아, journal이 이미 있는 실행에서 봉인 단계가 실패하면 이미 DB를 리셋한
  후보의 claim이 해제됐다. `postjournal_failure`를 신설해 갈랐다.
- **테스트가 공허했다.** 초안은 본문을 통째로 stub으로 바꿔 래퍼 3줄만 걸었고,
  리뷰어의 mutation에서 `observe()` 삭제·게이트 뒤로 이동·`reached()` 대신
  `journal_path is None` 셋이 **전부 살아남았다.** 진짜 lock 경로를 타는 동작
  테스트로 대체해 셋 다 잡았다. 정적 AST 테스트는 삭제했다 —
  `min(observations) < min(writes)`는 1400줄 함수의 어휘적 줄번호 비교라
  저 mutant를 하나도 잡지 못한다.
- **중간 커밋 하나가 단독으로는 회귀**였다. 브랜치를 하나로 합쳐 bisect나
  cherry-pick으로도 그 상태를 밟을 수 없게 했다.

### 2차 리뷰가 다시 mutation으로 구멍을 증명했다

- **`postjournal_failure`가 어떤 테스트에도 걸려 있지 않았다.** CLI의 판정을
  `False`로 바꿔도 1535건이 전부 통과했다. 그 값을 지우면 resume 실행의 봉인
  실패가 다시 `prejournal_failure`가 되어, 이미 DB를 리셋한 후보의 claim이
  원장에서 빠진다 — 그 커밋이 닫으려던 바로 그 결함이다. **같은 커밋에서
  M-1은 동작 테스트를 받고 M-2는 받지 못했다**는 것이 요점이다.
- **pinset을 식별한 뒤 경로 계산이 실패하면 해제하고 있었다.** 경로 계산은
  lifecycle 4개 키 밖(cache-target 10개 · `COMPOSE_PROJECT_NAME` 정규식 ·
  state root canonical)까지 보는데 그 전에 검증되지 않는다. 규칙을 갈랐다 —
  **식별 전은 해제, 식별 후는 유지.**
- **봉인이 오늘의 진단 경로를 막았다.** `candidate_compose_build`를 봉인하면서
  어느 서비스가 죽었는지가 사라졌다. 오늘 그 정보가 없었다면 PinVi 원인을
  못 짚었을 것이다. fan-out이 이미 한 곳에 있으므로 그 자리에서 실어 보낸다.
- **내가 쓴 문서 세 곳이 틀렸다.** 코드가 구현하지 않는 규칙을 안전하지 않은
  방향으로 단정했고(§9), 파괴적 재실행을 "진단"으로 안내했으며(오늘 내가 실제로
  그렇게 했다 — 원장에 아무것도 남기지 않고), §8은 존재하지 않는 단언과 틀린
  lock 순서를 가리키고 있었다.

### 남은 것

재시도 잔여물 수거 job, 런타임 이미지 크기(PinVi 쪽 후속), `environment_admission`
이 세 지점을 한 이름으로 덮는 것(어느 블록이 닫혔는지 구분되지 않는다),
`c6c_deployment_lock`이 root `.env` 회전보다 뒤에 잡히는 것.

## 2026-09-02 — rebuild ledger: 소비하지 않은 후보를 태우던 claim을 해제한다

M05 launcher에 적용했던 감사 렌즈를 형제 `run-pinned-rebuild-once`에 처음 댔다.
이 파일을 **실제로 실행하는 테스트가 0건**이었다.

**blocker.** launcher는 `ktdctl` 실행 전에 `O_EXCL` claim을 쓰고 어떤 경우에도
해제하지 않았다. 그런데 CLI는 durable journal 이전에 fail-close하면 result에
`classification: prejournal_failure`를 명시한다 — 즉 이번 실행이 journal을 쓰지
않았음을 스스로 적어 준다. launcher는 그 파일을 dict인지만 보고 버렸다.
그 분류를 내는 stage에 `application_base_images`(`docker pull`)와
`application_builder`(빌드 중 네트워크)가 있어, **레지스트리 rate limit 한 번이
회전 사이클 1회를 태웠다.** 저장소 역사의 `53d4639f`·`6269138f`가 이 계열이고,
`docs/tasks.md`는 "rebuild가 중단되고 그 claim이 탄다"를 피해 기전으로 이미
적어 뒀다 — 원인(fsync)은 고쳤는데 증폭기(claim)가 그대로였다.

소각(claim 유지)은 기본값으로 두고 **해제만 양성 증거에 걸었다.** 해제는 삭제가
아니라 `<pinset>.prejournal-NN` 개명이라 원장이 보존된다.

**적대 리뷰 2인이 이 수정 자체에서 다시 여섯 건을 찾았다.**

- 문서가 정반대를 지시하고 있었다 — 유일한 트러블슈팅 절이 `rotate-pair
  --block-previous`(= 소각)를 안내해, 코드가 살려 둔 후보를 사람이 태우게 된다.
  §9에 해제 규약과 재시도 절차를 신설했다(이 커밋).
- 해제 판정이 `chown`/`chmod`/`mv` **뒤**에 있었다. 그 셋은 `set -e` 아래라
  ENOSPC/EIO면 스크립트가 먼저 죽는데, 디스크가 차서 빌드가 prejournal에서
  실패한 경우가 바로 그 상황이다 — **재시도가 가장 정당한 순간에 해제가
  실행되지 않는다.** 판정을 `result_tmp` 대상으로 앞으로 옮겼다.
- 해제 사실의 유일한 증거가 아무도 읽지 않는 0700 디렉터리의 파일명이었다.
  형제 launcher의 `claimed` 마커와 같은 형태로 `output_dir/claim-released`를 남긴다.
- stage allowlist를 없앴다. `PinnedRuntimePrejournalFailure.__init__`이 이미 같은
  집합으로 stage를 검증하므로, launcher 사본은 위험 방향을 막지 못하고 **좁아지는
  방향(= 재시도 가능한 후보 소각)만** 만든다. 형제 launcher에서 tenant 어휘를
  걷어낸 것과 같은 판단이다.
- 시도 상한 32 → **5**. 32는 M05(짧은 실행)에서 복사한 값인데 여기선 1회가
  1~2시간 + 이미지 4개 풀 빌드이고 잔여물 수거 job이 없다.
- `"the candidate was not consumed"` 문구를 좁혔다. `prejournal_failure`는
  "이번 실행이 journal을 쓰지 않았다"이지 "후보가 소비된 적 없다"가 아니다 —
  resume 실행도 이 분류를 낼 수 있다.

그리고 리뷰 대기 중 **내 수정 자체의 버그**를 자체 검증에서 잡았다: `os.rename`은
POSIX에서 대상을 조용히 덮어써 `FileExistsError`로 빈 슬롯을 찾는 루프가 동작하지
않고, 두 번째 실패가 첫 기록을 파괴했다. `os.link` + `unlink`로 create-only
의미론을 만들었다.

셸을 진짜 bash로 실행하는 동작 테스트 11건을 신설했다. mutation 3종 확인.

**후속으로 남긴 것**: `classification: unclassified` 구멍(admission gate와 seal 밖
pre-journal 구간 ~107줄이 전부 여기로 온다 — 근본 수정 위치는 launcher가 아니라
`rebuild_pinned_runtime`이다), OOM-kill/기동 실패의 소각, 재시도 잔여물 수거 job.

## 2026-09-02 — frontend 컴포넌트 렌더 테스트 인프라 도입 + 오류 표시 분기 회귀 테스트 (GM-12 후속)

`docs/tasks.md`에 남아 있던 GM-12 항목("컴포넌트 렌더링 테스트 인프라 자체가
없다")을 이행했다. `jsdom`, `@testing-library/react`/`jest-dom`/`user-event`/`dom`을
devDependencies로 추가하고(npm이 실제로 해석한 React 18 peer 호환 버전), `vitest.config.mts`의
`environment`를 `node`→`jsdom`으로, `include`에 `*.test.tsx`를 추가하고
`setupFiles: ['./src/vitest-setup.ts']`(`@testing-library/jest-dom/vitest` import)를
달았다. 기존 3개 순수 로직 테스트는 jsdom 아래서도 그대로 통과함을 확인했다.

예상 밖의 블로커: 이 프로젝트의 Vite(vitest가 무설치로 끌어온 8.2.2)는 SSR 모듈
변환에서 `.tsx`의 JSX를 확장자만으로 인식하지 않아 `@vitejs/plugin-react` 없이는
테스트 파일 자체가 파싱 에러로 죽었다 — Next.js는 SWC를 쓰므로 이 프로젝트에 원래
없던 의존성이었다. `@vitejs/plugin-react@6.1.1`을 추가해 해결했다.

`@tanstack/react-query`용 `frontend/src/test-utils.tsx`에 `renderWithQueryClient()`를
추가하고(테스트마다 새 `QueryClient`, `retry: false`), `SourceStatusPanel`(2건) ·
`ContainerDetailModal`(2건 + production 은닉 전용 1건) · `AdminSettingsPanel`(3건, 감사
노트가 지목했던 `loadPublicKeys` 상태 초기화 버그의 현재 수정 동작 회귀 포함) 세 컴포넌트의
오류 표시 분기에 회귀 테스트 8건을 추가했다. `postJson`/`deleteJson`이 `api.ts` 내부에서
`apiJson`을 모듈 스코프로 직접 참조해 export만 mock해서는 안 잡히는 함정, `IS_DEV`가
모듈 최초 평가 시 한 번만 고정되는 상수라 `vi.stubEnv` 검증에 파일 단위 격리 +
그 파일 안에서의 첫 동적 `import()`가 필요한 함정을 실측으로 확인하고 반영했다. 8건
전부 mutation-test로 비어있지 않음을 확인(해당 조건부 렌더를 일시적으로 깨고 그
테스트만 실패 → 정확히 원상복구, `git diff --stat` 0으로 확인). 상세 내역은
`docs/tasks.md`의 GM-12 완료 항목 참고. 전체 frontend type-check/lint/test(8
files, 31 passed)/build 통과.

