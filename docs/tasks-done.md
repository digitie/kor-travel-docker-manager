# TASKS-DONE — 완료 태스크 기록

이 문서는 `kor-travel-docker-manager`에서 완료된 작업을 역순이 아닌 태스크 번호순으로 보관한다.
진행 중/대기 작업은 [`docs/tasks.md`](tasks.md)를 기준으로 한다.

> ⚠️ **ID 재사용 주의.** `T-013`~`T-018`은 서로 다른 두 작업 집합에 중복 부여돼 있다.
> 2026-06-12/13에 완료된 초기 집합(CLI 별칭·포트 정책·실행 위치 문서화·geo target 편입·
> 관측 스택·프로젝트명 전환)과, 2026-06-20/23에 완료된 운영 배포 집합(prod 공개 주소·host
> 네트워크 전환·Tailwind v4 전환·prod 배포 검증·운영 스택 기동·endpoint redaction)이 같은
> 번호를 쓴다. 재번호는 하지 않는다 — 이미 push된 커밋 메시지(예: `682074b`가 T-016,
> `d80e74f`가 T-017)와 `docs/journal.md`가 양쪽을 각각 참조하고 있어, 번호를 바꾸면 불변인
> git 이력과 문서가 어긋난다. **두 집합은 완료 날짜로 구분하고**, 상세 절 제목에도 날짜를
> 병기했다. 재사용된 행은 비고에 `⚠️ ID 재사용`으로 표시했다.

> ⚠️ 이 문서는 완료 당시의 사실을 보존하는 역사 기록이다. 현재 포트·DB·CLI·디자인
> 계약은 [`docs/tasks.md`](tasks.md), [`docs/ports.md`](ports.md),
> [`docs/architecture.md`](architecture.md)와 현재 코드·Compose를 기준으로 확인한다.

## 2026-10-17 — T-MGR-02r 닫음(실측 결과 n150 변경 없음)

`docs/tasks.md`에서 **문구 그대로** 옮겼다(첫 줄 `[ ]`→`[x]`, 실측 근거를 덧붙임).

- [x] **T-MGR-02r — `dagster.yaml` location 상한 1회 조정(ADR-58 결정 7)**. `tag_concurrency_limits`의 location 값(geo 1·weather 2 — n150 실측 매시 run p95 < 5 min일 때, 아니면 1·transport 3)과 instance digest anchor 한 자리 갱신을 한 번에. 실측이 선행 조건이라 T-MGR-02(#494)에서 뺐다. 규모 S. **닫음(2026-10-17, 변경 없음)**: n150 공용 Dagster run 이력 14일(GraphQL `runsOrError`, 2026-10-03T08Z~10-17T08Z, 19,845 run)을 location별로 실측했다. geo·weather·transport는 10-14~15에 odroid plane으로 옮겨 n150에서 마지막 run이 각각 10-15T02:15Z·10-14T21:05Z·10-14T21:10Z이고, n150 workspace에는 map·pinvi·concierge 세 location만 LOADED다. 세 location은 상한에 닿은 적이 없다 — pinvi(3,726 run, 전부 schedule: duration p95 24 s·max 746 s, 동시 실행 최대 4 / 상한 10), concierge(6,097 run: p95 20 s·max 1,023 s, 최대 2 / location 상한 2 + job별 1 — 설계대로 무는 상한), map(9 run뿐 — 10-03·10-12, p95 3,480 s, 최대 1 / 상한 10). 세 location 합산 동시 실행은 최대 4(시간의 93 %가 0, 6.3 %가 1), queue 대기 p95 26.7 s·p99 81 s(geo 이전 뒤 10-15T03Z~: p95 21 s·max 90 s)라 전역 `max_concurrent_runs: 12`도 멀다. ADR-58 결정 7도 map·pinvi·concierge·12는 불변이다. 그래서 n150 기준으로는 조정할 상한이 없다. **odroid 쪽은 범위 밖**: 결정 7의 geo 1·weather 2/1·transport 3은 odroid 호스트 보호용이고 odroid plane은 codex 영역이다. 같은 `dagster.yaml` 한 벌을 두 plane이 읽으므로, 바꾸면 instance digest가 바뀌어 **두 plane 모두** 재생성된다(n150은 `ktdctl dagster-plane converge --dry-run` → `--confirm`). 참고 수치(n150 시절 같은 14일): weather 매시 job p95가 5 min을 넘는다(airkorea 1,128 s·kma 초단기예보 1,199 s·특보 852 s·초단기실황 502 s) — 결정 7 규칙대로면 weather 1이지만, 이미 `kortravelweather/run_group=external_weather` 3이 대기 최대 80 min을 만들고 있어(10-03T21:15Z·10-14T00:15Z) 1로 내리면 매시 job이 한 시간 안에 끝나지 않을 수 있다 — odroid 실측으로 정할 일이다. transport는 최대 3(상한 3)·geo는 최대 2였다. `dagster.yaml:140` 주석의 태그 테넌트 목록에서 geo가 빠진 것도 그때 함께 고친다(주석만 바꿔도 digest가 바뀐다).

## 2026-10-17 — T-MGR-04r(Prometheus 잔여) 완료

`docs/tasks.md`에서 **문구 그대로** 옮겼다(첫 줄 `[ ]`→`[x]`, 머지·적용 결과를 덧붙임).

- [x] **T-MGR-04r — Prometheus 잔여(T-MGR-04 #492 뒤)**. `weather-code-server`(14103)·`weather-federate` job, transport code-server registry 등록, geo-ui `12505` scrape 여부(odroid 방화벽 뒤). Manager 자신의 호스트별 job은 T-MGR-NH NH-6, alert rule(`count(up{host})`·디스크·시계)은 `docs/multi-host.md` §5 deferred. 규모 S. **2026-10-17 구현(PR, 리뷰·머지 대기)**: weather code-server를 `metrics_port`(odroid `12203`, n150에서 200 실측)로 registry 파생 job에 넣었다; weather 자체 Prometheus가 odroid에 없어(14104 리스너 없음) **weather-federate는 두지 않는다**(weather 메트릭 접두사도 `weather_*`가 아니라 `ktw_*`); transport code-server 등록은 **뺐다**(#512 리뷰 F1 — `config_files`의 `docker-compose.yml`에 그 서비스가 없어 `status transport`가 실패한다; transport shared overlay 작업 때 다시); geo-ui `12505`는 계속 수집하고 odroid 방화벽 표에 n150 허용을 적었다(`ports.md`). **완료**: #512 머지, n150 Prometheus 적용·reload 뒤 target 9개 모두 up. 남은 alert rule은 활성 원장 `T-MGR-04r 잔여` 한 줄(`multi-host.md` §5 deferred).

## 2026-10-17 — T-MGR-NH 잔여(NH-6) 완료

`docs/tasks.md`에서 **문구 그대로** 옮겼다(첫 줄 `[ ]`→`[x]`, 머지·적용 결과를 덧붙임).

- [x] **T-MGR-NH 잔여 — NH-6(`docs/multi-host.md` §4)**. NH-7(런북)은 2026-10-17 `prod-deployment.md` §10으로 완료. NH-6: Prometheus의 Manager job을 registry 파생으로(호스트마다 static_config) — **2026-10-17 구현(PR, 리뷰·머지 대기)**: odroid Manager가 설치돼 n150에서 `http://192.168.1.13:12901/metrics` 200 확인. 머지 뒤 n150 Prometheus reload(`ensure prom` 또는 `/-/reload`)로 `up{job="kor-travel-docker-manager",host="odroid"}==1` 확인. deferred 목록은 §5. 규모 S. **완료**: #524 머지(`76387c8f`), n150 Prometheus 적용·reload 뒤 target 9개 모두 up(odroid Manager `192.168.1.13:12901` 포함). T-MGR-NH 시리즈(NH-1~8) 종료.

## 2026-10-17 — 활성 원장 정리: ADR-58 T-MGR·T-MGR-NH 완료분과 남아 있던 완료 항목

`docs/tasks.md`(활성 작업만 두는 원장)에 남아 있던 완료 항목을 **문구 그대로** 옮겼다. T-MGR 항목은 첫 줄의 `[ ]`를 `[x]`로
바꾸고 머지 PR을 덧붙였다. 남은 일이 있는 항목(T-MGR-02·04·NH)은 활성 원장에 잔여 한 줄(`…r`·"잔여")로 남겼다.

- [x] **T-MGR-NH — 임의 프로젝트의 N개 서버 분리 일반화(소유자 2026-10-14 지시)**. **설계 최종안(2026-10-16): [`docs/multi-host.md`](multi-host.md)** — 배치 규칙(§3), 남은 가정, PR 계획 NH-1~8, deferred 목록. ADR-58은 `n150`(default)·`odroid` 두 호스트와 geo·weather·transport 세 tenant의 이전을 전제로 설계됐다. 이것을 **임의의 target(tenant·내부 target·infra)을 N개 호스트 중 어디에나 두고 옮길 수 있는 구조**로 일반화한다. 범위: (1) registry `hosts:`가 N개를 받고 target `host:`는 단일 호스트 또는 집합(`all` 포함) — 2-호스트 가정("default가 아니면 odroid", `workspace.{n150,odroid}.yaml` 고정 두 파일, `<host(x)>`가 `127.0.0.1`/`192.168.1.13` 둘 중 하나라는 가정)을 코드·테스트·문서에서 제거; (2) 호스트별 파생물(Dagster workspace, consumer origin 기본값 R2, Prometheus 타깃 B-8, 공개 URL)을 호스트 목록에서 계산하는 단일 파생기 — 호스트를 하나 더해도 코드 수정 없이 파생물만 늘어난다; (3) 교차 호스트 간선(consumer→provider)을 registry에서 계산해 검증(R7), Map/PinVi 같은 내부 target의 플립도 같은 절차; (4) 호스트 추가·target 이전 런북을 "호스트 이름·주소를 인자로 받는 한 절차"로(오늘의 odroid 특유 단계는 그 절차의 사례); (5) 호스트 프로필 Manager(결정 2)·호스트당 Dagster plane·PG(결정 3)·중앙 관측(결정 11)은 N개에서 그대로 성립하는지 재확인하고 아니면 ADR 개정. 수용 기준(빨갛게 만드는 입력): 세 번째 가상 호스트 fixture를 registry에 더하고 target 하나를 옮기면 파생물 전부가 그 호스트를 반영하고 나머지 호스트 렌더는 byte 불변; 코드에 호스트 이름 리터럴 0건(검사 테스트). ADR-58 "잠정 시행(2026-10-14)"의 제거 목록 (ⅰ)~(ⅵ)(default-호스트 workspace 필터·geo 재정의 표·weather `excluded_from_all`·소비자 포트 표·"consumer는 default 호스트" 가정·Prometheus odroid 리터럴)을 모두 없애는 것이 이 작업의 일부다. **T-MGR-01a~06은 이 일반화와 충돌하지 않게 N-ready로 구현**한다(01a 착수 때 2-호스트 가정을 새로 넣지 않는지 리뷰 체크). 규모 L. 의존: 01a~06의 인터페이스 확정 뒤 일반화 PR, 또는 01a를 처음부터 N-ready로. **2026-10-16(#483)**: R1 다중 소유 — 공용 PG를 실행하지 않는 target만 있는 세 번째 호스트에서는 PG(n150·odroid)의 주소가 하나가 아니라 그 connection이 미해석으로 남는다(지금은 #481 동작 유지, N-host 규칙은 이 작업에서 정한다). 호스트 리터럴 검사는 허용 목록(잠정 Prometheus odroid job)이 있어 "리터럴 0건"은 아직 아니다. 잠정 시행 ⅳ(소비자 포트 표)는 T-MGR-01c로 registry에 옮겨졌다. ⅴ("consumer는 default 호스트")는 **완료가 아니다** — 01c는 소비자 호스트를 실행 target·Dagster family에서 파생하고 그 밖은 unresolved로 보고할 뿐, 서비스→호스트 소유의 일반 모델은 이 작업에 남는다. 리터럴 검사의 범위 밖(남은 리터럴): compose(`docker-compose.yml`의 `192.168.1.13` 기본값 8건 — R2 소비자 기본값), bash 스크립트, bash에 heredoc으로 든 Python. R1 다중 소유로 `{host}`가 렌더되지 않은 connection은 원격 HTTP 관측에서 `unreachable`로 보인다(미해석 URL은 연결되지 않는다). **완료(2026-10-17 원장 정리)**: 설계 #499(`4759ea7`), NH-1 #500(`27cafd6`)·NH-2 #501(`33f6e5b`)·NH-4 #503(`e401d8a`)·NH-3 #506(`33efbf7`)·NH-5 #502(`66a3919`)·NH-8 #504 — 남은 NH-6·NH-7은 활성 목록의 T-MGR-NH 잔여.
- [x] **T-MGR-01c — R2 consumer origin 커밋 파생물(ADR-58)**. 대상은 규칙 — "형제 포트 `12501`/`14001`/`14101`을 기본값에 가진 모든 compose env(비활성 `legacy-dagster` profile 포함)": 오늘 7곳 = geo `:1590`(map-api)·**`:1735`(map code-server)**·`:1827`·`:1934`(legacy webserver/daemon), transport `:1744`, PinVi ETL `:2142`(리터럴을 `${PINVI_KOR_TRAVEL_WEATHER_BASE_URL:-http://<host(weather)>:14101}`로) + **pinvi-api 블록(`:1943-2018`)에 같은 weather 변수 추가**(없으면 weather 플립 뒤 PinVi 여행 날씨가 조용히 빈다). 파생 테스트는 렌더된 compose를 **스캔**해 그 집합을 찾는다(줄 번호 열거 금지 — `docs/bindings.md` B-6) + `targets validate`의 root `.env` 재정의 거부. 수용 기준: 7곳 중 **어느 하나**를 registry와 다르게 고치면 빨강(특히 `:1735` 하나만 loopback으로 되돌려 빨강 확인); `.env`에 다른 값 → validate 거부; pinvi-api 렌더에 변수 존재; 오늘 값(전부 `127.0.0.1`)은 렌더 불변 — 차이는 ADR-58 결정 1 허용 diff ③뿐. 수정 파일: `docker-compose.yml`, 테스트, `docs/bindings.md` B-6. 규모 S. 의존: 01a. **2026-10-14 잠정 시행으로 대체(T-MGR-06i)**: 소비자 포트 `12501`/`12301`(transport API gateway)/`12201`(odroid 통일 포트), 집합 8곳(geo 5 — Map 4·pinvi-api 1, transport 1, weather 2), 오늘 값은 `127.0.0.1`이 아니라 `192.168.1.13`. 렌더 스캔 테스트(`backend/tests/test_consumer_origin_defaults.py`)는 있다 — 이 항목에 남은 것은 포트 표를 registry(R1)로 옮기는 것과 `targets validate`의 `.env` 재정의 거부. **2026-10-16 구현(PR, 리뷰·머지 대기)**: 형제 target `consumer_origin`(registry) + `registry.consumer_origin`(R1, 소비자 호스트 기준) + `services/consumer_origins.py` 스캔; `targets validate`가 root `.env` 재정의(기대값과 다른 비어 있지 않은 값)를 거부. n150 root `.env`는 원천 변수 4개 모두 미설정(설치 영향 없음). 리뷰 반영: 스캔 포트는 `consumer_origin` 포트 + 명시한 `retired_consumer_ports`(14001·14101)만, 소비자 호스트는 실행 target → Dagster family → 그 밖은 unresolved 보고(default로 풀지 않음), 끝 `/` 무시, 다중 호스트 형제는 명확한 오류, 설치기 거부 문구 중립·위반 줄 전부. **완료(2026-10-17 원장 정리)**: #491(`384cb40`).
- [x] **T-MGR-02 — workspace 호스트별 파생 잔여 + `dagster.yaml` 상한 1회 조정(ADR-58 결정 6·7)**. **#481이 한 것**: `config/dagster-shared/workspace.{n150,odroid}.yaml` 파생·커밋, compose bind `workspace.${KTDM_HOST:-n150}.yaml`, `KOR_TRAVEL_DAGSTER_WORKSPACE_DIGEST: ${KTDM_DAGSTER_WORKSPACE_DIGEST:-…}`, cutover 스크립트 호스트 선택(#489가 compose bind allowlist를 같은 경로로 맞춤). **남은 것**: `dagster.yaml` location 상한 1회(geo 1 / weather 2(n150 매시 run p95 < 5 min이면, 아니면 1) / transport 3 — 지금은 transport만), instance digest 리터럴 7곳 → YAML anchor 1곳(+`test_dagster_shared_config.py`), `targets validate`의 설치 workspace sha256[:16] == digest env 효과 검사, `.env.example`의 odroid digest 계산식. 규모 S. 소유자 결정 §14-18. **2026-10-16 구현(PR, 리뷰·머지 대기)**: `targets validate`가 compose 원문에서 digest env와 `…/workspace.yaml` bind를 함께 가진 서비스마다 설치된 파일 sha256[:16] == 보간된 digest를 효과로 검사(`services/workspace_digest.py`; 설치기 flip 전 검사 포함); instance digest 리터럴 7곳 → 첫 서비스 안 anchor 한 자리(최상위 `x-` 키는 `compose config` 렌더에 한 줄이 늘어 쓰지 않았다 — n150 렌더 sha256 불변 실측). 남은 것: `dagster.yaml` location 상한 1회(실측 필요), `.env.example` odroid digest 계산식은 이 PR에 넣음. **완료(2026-10-17 원장 정리)**: #494(`8fc11dd`) — 남은 `dagster.yaml` 상한 조정은 활성 목록의 T-MGR-02r.
- [x] **T-MGR-03 — PG 수치·이미지 env 파생 + OOM/memswap 정책 리터럴 + db-init-geo 확장(ADR-58 결정 3·8)**. compose PG 수치(`:265-291`)를 `${KTDM_PG_SHARED_BUFFERS:-1GB}` 꼴로(기본값 = 현재 n150 값) — 단 `max_locks_per_transaction=256`(#471)은 env 파생하지 않고 두 호스트 같은 리터럴로 둔다(ADR-58 결정 8 ⅰ); n150 compose에 없는 키 6개(`max_parallel_workers`·`_per_gather`·`effective_io_concurrency`·`checkpoint_timeout`·`wal_compression`·`autovacuum_max_workers`)는 기본값 = PG 16 서버 기본값(8/2/1/300s/off/3)으로 추가(허용 diff ⑦ — 테스트가 기본값 == 서버 기본값을 단언); `max_connections`는 compose에 없으니 **추가하지 않는다**(서버 기본 100); connlimit 45(`:726,728`)는 리터럴 불변이고 앱 role 셋에는 `CONNECTION LIMIT`을 두지 않는다(오늘 동작 그대로); **db-init-geo(`:423-445`)에 `CREATE SCHEMA IF NOT EXISTS x_extension` + `CREATE EXTENSION IF NOT EXISTS {postgis,pg_trgm,unaccent,pg_stat_statements} SCHEMA x_extension` 추가**(odroid fresh 경로의 확장 생성 주체 — `:382-391` 주석의 pg_restore 전제가 odroid에는 없다; n150 no-op; 스키마 소유는 geo ADR-070과 맞춘다; `docs/shared-postgres-onboarding.md` §4.3 "현재 postgis 1종" 문장 갱신); 서버 `:213` 및 db-init 6개 이미지를 `${KTDM_POSTGRES_IMAGE:-<현재 digest 핀>}`·`${KTDM_POSTGRES_CLIENT_IMAGE:-postgis/postgis:16-3.5}`로(기본값은 현재 문자열 byte 그대로); `oom_score_adj` PG `-800`·plane daemon/webserver `-600`(weather cs `-500` 유지, `:2884`); **`mem_limit`는 호스트별 값(ADR-58 결정 8 ⅱ)**: PG·geo cs는 odroid에서만(4g·2g — env 파생, n150 기본값은 "없음"과 효과 동일), weather cs는 `${…:-4g}`(odroid `.env` 1.5g); **memswap**: `mem_limit` 있는 서비스마다 `memswap_limit == mem_limit`(같은 변수; `mem_swappiness: 10` 유지) — concierge scheduler·cs(`:1304,:1347` `mem_limit 2g`, n150 전용)도 이 규칙에 걸리므로 n150에서 swap 0 효과가 새로 생긴다는 것을 PR 본문에 적는다(제외하려면 테스트 범위를 PG·geo cs·weather cs로 한정하고 그 이유를 적는다); **transport cs는 Manager compose에 없다**(transport 저장소 `docker-compose.shared.yml` — transport ADR-014 몫); `backend/tests/test_transport_shared_bootstrap_contract.py:23`을 "이미지 == compose 기본값"으로. 수용 기준: n150 `compose config` 차이 == ADR-58 결정 1 허용 diff ⑥·⑦·⑧(+ ⑨를 정하면 그 형태) — PG·geo cs에 n150에서 실효 `mem_limit`가 생기면 빨강; 테스트가 "`mem_limit` 있는 서비스마다 `memswap_limit == mem_limit`"를 단언(하나 빼면 빨강); fresh DB fixture에서 db-init-geo 뒤 `\dx`에 확장 4종(스키마 `x_extension`), n150형 fixture(이미 존재)에서 no-op. 수정 파일: `docker-compose.yml:213,254-291,382-445,2442 블록,2882-2885,…`, `.env.example`(`KTDM_POSTGRES_IMAGE`/`KTDM_POSTGRES_CLIENT_IMAGE`/`KTDM_PG_*`/`KTDM_*_MEM_LIMIT` — 어느 호스트에 필요한지 주석), 테스트, `docs/shared-postgres-onboarding.md` §4.3·§11 "(계획)" 해제. 규모 M. 의존: 없음(01a와 병렬 가능). 소유자 결정 §14-14. **2026-10-16 구현(PR, 리뷰·머지 대기)**: 이미지·튜닝 12키·`mem_limit` env(기본값 = n150; PG·geo cs `0` = 상한 없음, 렌더에서 빠짐 — ⑨의 형태), oom PG -800·plane -600, memswap 규칙은 `mem_limit`가 `KTDM_*_MEM_LIMIT` env인 서비스(PG·geo cs·weather cs)에 — **concierge scheduler·cs는 제외**(n150 전용 고정 2g, 걸면 n150에 swap 0 효과가 새로 생긴다; weather cs는 결정 8 ⅳ대로 n150에서도 swap 0), db-init-geo `x_extension` 확장 4종 + `USAGE`(격리 instance fresh·재실행 통합 테스트). n150 설치 `.env` 렌더 차이 = ⑥·⑦·⑧. `.env.example`에 키와 odroid 예시. **남은 것**: geo cs `pids_limit 1024`(결정 8 ⅱ — n150 렌더에 새 줄이 생겨 이번 범위에서 뺐다), odroid 값 실측 확정(fio 뒤 `effective_io_concurrency`). **완료(2026-10-17 원장 정리)**: #496(`0131dc2`).
- [x] **T-MGR-04 — Prometheus 타깃 커밋 파생물 + odroid job + transport cs registry 등록(ADR-58 결정 11)**. `config/prometheus/prometheus.yml` 타깃을 registry 계산값과 대조하는 파생 테스트(`docs/bindings.md` B-8); odroid job `cadvisor-odroid`·`node-odroid`·`weather-code-server`(14103)·`ktdm-odroid`(12901)·`weather-federate`(14104 `/federate`, `weather_*`+`ALERTS`), `labels: host=odroid`; rule `count(up{host="odroid"})==N`·`/mnt/media` < 100 GiB·`/` < 10 GiB·시계 rule(`node_timex_sync_status==0` ∨ 오차 > 5 s); ~~node_exporter를 n150·odroid compose에~~(2026-10-16 결정: Manager compose에 넣지 않는다 — 호스트 측 stack을 registry `node` target으로 등록, `docs/ports.md` "node_exporter"); transport code-server를 `config/docker-targets.yml` containers에 등록(대시보드 metrics). 수용 기준: 타깃을 registry와 다르게 고치면 빨강; `count(up)` 신호. 수정 파일: `config/prometheus/prometheus.yml`, `docker-compose.yml`, `config/docker-targets.yml`, `docs/ports.md`, `docs/bindings.md` B-8. 규모 S. 의존: 01a(타깃 계산에 `hosts:`+`host:` 필요). 소유자 결정 §14-11. **2026-10-16 구현(PR, 리뷰·머지 대기)**: 컨테이너 `metrics_path` → job, 호스트마다 static_config(R1 주소·`host` label) — `services/prometheus_targets.py` + `test_prometheus_targets_are_derived.py`; geo job은 odroid 주소로(옛 n150 잔재 수집 종료), `cadvisor`는 n150·odroid 한 job, odroid node_exporter는 registry `node` target(외부 호스트 측 stack)으로 등록. node_exporter를 Manager compose에 넣지 않았다(호스트 `/` 읽기 전용 bind가 C6c 후보 계약의 새 예외라 — `docs/ports.md`). 같은 PR에서 `targets validate --check-coordinates`를 R5대로 자기 호스트 target에만 건다(odroid의 `node`·transport 좌표가 n150에 없는 것은 정상). **남은 것**: `weather-code-server`(14103)·`ktdm-odroid`(12901, odroid Manager 설치 뒤)·`weather-federate` job, alert rule(`count(up{host=...})`·디스크 여유·시계), transport code-server registry 등록, geo-ui `12505`는 odroid 방화벽(§9.1 단계 7, HAProxy 전용) 뒤에 n150에서 닿지 않는다 — 그때 `metrics_path`를 뺄지 결정. **완료(2026-10-17 원장 정리)**: #492 — 남은 job·rule은 활성 목록의 T-MGR-04r·T-MGR-NH 잔여.
- [x] **T-MGR-05 — `ensure` 프리플라이트 효과 검사(ADR-58 결정 9·R5)**. 이미지 `.Architecture` == 호스트 arch; bind 소스 장치 == `KTDM_DATA_SOURCE_DEVICE`(설정 시만, `findmnt -T <bind 경로> -o SOURCE`; 미설정이면 생략 — n150 무변경); `docker compose config` 렌더 성공(버전 문자열 비교 없음 — odroid는 compose major가 n150과 다를 수 있다, §14-26). 수용 기준: amd64 이미지 + arm64 가짜 호스트 → 거부; 장치 env 설정 + 다른 SOURCE → 거부, 미설정 → 통과; compose 플러그인 부재 fixture → 거부. 수정 파일: `services/docker_service.py`, `services/compose_service.py`, `.env.example`(`KTDM_DATA_SOURCE_DEVICE` — odroid만), 테스트. 규모 S. 의존: 없음(01a와 병렬 가능). **2026-10-16 구현(PR, 리뷰·머지 대기)**: `services/ensure_preflight.py` — 로컬 이미지 arch == `docker info` arch, `KTDM_DATA_SOURCE_DEVICE` 설정 시 쓰기 bind 장치; compose 렌더 성공은 ensure transaction의 기존 `compose config` 해석이 이미 강제(순서만 테스트). n150 설치본 렌더(43 서비스)·장치 env 미설정으로 실측 통과. 리뷰 반영: 대상은 기존 compose argv 파서 + 렌더된 `depends_on` closure, `ensure --build`면 `build:` 서비스 arch 검사 생략, pinned 재구축·plane 재생성·복구는 이 검사를 거치지 않음(odroid 첫 배치는 `ensure`라 받아들임), 쓰기 bind env 목록을 `.env.example`·prod-deployment §9.1에. **완료(2026-10-17 원장 정리)**: #493(`d80087b`).
- [x] **T-MGR-06k — 재구축의 plane location 기다림 상한 파생(후속, 선택)**. `_wait_shared_plane_locations`의 고정 300초(`_SHARED_PLANE_PROBE_TIMEOUT_SECONDS`)를 `dagster-plane converge`와 같은 render healthcheck 파생 상한(`_shared_plane_health_wait_seconds`)으로. 규모 S. **2026-10-16 구현(PR, 리뷰·머지 대기)**: 고정 상수를 지우고 `_shared_plane_health_wait_seconds`를 쓴다(오류 문구도 파생값); 타임아웃 테스트가 파생 상한 사용·문구·경과 시간을 본다. **완료(2026-10-17 원장 정리)**: #498(`5ce6f0a`).
- [x] **T-MGR-06i — (잠정, 2026-10-14) transport·weather·geo 선시행의 정리 잔여**. **#481·#483이 한 것**: `workspace.odroid.yaml` 파생, R1 연결 주소·R3 자기 식별·R4 변경 경계·호스트별 `all` 필터 (weather `excluded_from_all` 제거), compose `map-api → geo-api` 간선 제거. **남은 것**: Prometheus odroid 타깃의 registry 파생(T-MGR-04), 소비자 origin 포트 표 → R1 계산·`.env` 재정의 거부(T-MGR-01c). 둘이 닫히면 이 항목도 닫는다. **완료(2026-10-17 원장 정리)**: #491·#492(잔여 둘이 닫혀 이 항목도 닫힌다).
- [x] journal/attestation drift — Manager generation receipt와 Map attestation의 execution binding field를 함께 정렬한다. **2026-09-26 닫음**: Map M2(#1272)가 `scripts/lib/c7_prod_attestation.py`를 지웠고(대체인 `c7_prod_runtime.py`는 Manager 파일을 읽지 않는다), Manager는 ADR-51 B3 PR-2에서 v8 journal 모델과 generation API의 `journal`·`terminal` 키를 지웠다 — 정렬할 두 쪽이 모두 없어졌다. 남은 것은 Map `docs/integration-map.md`와 PinVi `docs/runbooks/live-mutating-e2e.md`의 문서 drift(v8 exact-dict·`drift` 서술)뿐이고 그것은 각 저장소 몫이다.
- [x] GM-17 선행조건 — targets 문서의 자리를 trusted 설치본에 고정하고 무결성을 검증한다 (2026-09-17 완료). `TARGETS_FILE`/`PROJECT_ROOT` env redirect를 설치본에서 거절하고, 읽기를 `O_NOFOLLOW` + `fstat` 기반 검증 descriptor로 바꿔 root 소유·nlink 1·group/other 비쓰기를 강제한다(개발 checkout은 종전 그대로). 감사 노트 (b)가 "그대로 옮기면 보안 회귀"라고 지목한 그 자리다 — **이것이 초록이어야 아래 allowlist 이관이 가능하다.** 변이 셋으로 결박 확인.
- [x] GM-17 본작업 A — bind allowlist 외부화 (2026-09-17 완료, 적대 리뷰 2인 반영). `_CANDIDATE_ALLOWED_OPERATOR_BINDS`(125줄 dict 리터럴)를 `config/docker-targets.yml`의 `compose_binds:` 절로 옮기고 코드에는 검증 규칙만 남겼다. **병목 해소의 범위를 정확히 적는다**(리뷰 H-3 정정): installer가 `git archive`로 트리를 통째 교체하고 넘어오는 것은 `.env` 하나뿐이라, 설치본 config를 직접 고쳐도 다음 재설치에서 되돌아간다. 즉 **backend 코드 수정은 없어졌지만 재설치는 그대로다** — "config 작업이 됐다"는 절반만 참이었다. **값은 한 글자도 바꾸지 않았다** — 옮기기 전후 해석된 매핑 29건이 정확히 같다(sha256 `e7ec261c30db1d04`). 변이(항목 하나 제거)로 3건이 빨개지고 그중 하나가 실제 배포 검증 경로라, 설정이 진짜 소비된다는 것까지 확인했다.
- [x] 백업 wrapper의 git 실행 비트 (2026-09-17). `scripts/run-standalone-backup.sh`와 `scripts/run-offbox-sync.sh`가 git에 **`100644`**로 들어 있었다. crontab은 그 경로를 **직접 실행**하므로 새 체크아웃마다 `Permission denied`로 죽는다 — 실제로 `geo_dagster`·`concierge`·`pinvi` 백업이 **2026-09-12부터 5일간** 전부 실패했다(마지막 성공 09-11 03:15~03:55Z). Map 원장이 원인을 "배포가 벗겼다"로 적었는데 **틀렸다. 처음부터 없었다.** installer가 `0755`로 세우는 스크립트는 넷(`install-ktdm-trusted-release`·`run-pinned-rebuild-once`·`run-m05-isolated-e2e-once`·`rotate-pinned-pair`)뿐이고 백업 wrapper 둘은 그 목록에 없다(`/opt`의 사본도 `-rw-r--r--`). 그래서 09-16 호스트에서 한 `chmod +x`는 국소 수정이었고 **다음 체크아웃이 되돌렸을 것이다.** git 모드를 `100755`로 고치고, `backend/tests/test_cron_executed_scripts_are_executable.py`가 "헤더에 crontab 직접 실행 줄을 적은 스크립트는 git 모드가 `100755`"를 강제한다 — 목록을 손으로 들지 않고 **스크립트 자신의 주장**에 결박하되, 탐지기가 공허해지지 않게 그 둘을 하한으로 박았다. 복구 실측: 09-17 창에서 세 role 전부 artifact 생성(pinvi `pinvi-1789617309.dump`, GC 1건).
- [x] GM-17 후속 — allowlist 변경의 **즉시 반영** (2026-09-27 ADR-51 D I-2로 해소). allowlist는 release 설치로만 바뀌고, installer가 설치할 때마다 backend를 stop → flip → start 하므로 새 목록이 설치와 함께 반영된다. 설치본 config를 제자리에서 고치는 것은 지원 경로가 아니다(다음 설치가 되돌린다).
- [x] GM-17 B · S0 — 서비스 부재 형상 골든 테이블 (2026-09-17 완료, 동작 변경 0). 완화의 폭발 반경이 CI에 안 보이는 것이 B의 가장 큰 위험이라, 코드를 고치기 전에 "오늘 어떤 부재가 어떤 메시지로 거부되는가"를 표로 박았다. 실측이 드러낸 것: **여섯 형상 중 부재를 부재라고 말하는 것은 하나뿐**이고, 서비스를 `null`로 만들면 무관한 Map 메시지가 나온다. 이후 단계의 PR은 이 표의 diff를 본문에 싣고 **"거부 → 통과"로 바뀐 칸을 전부 의도한 완화로 열거**해야 한다.
- [x] GM-17 B · S1 — 무조건 인덱싱 제거 + 검사 순서 교정 (2026-09-17 완료, 판정 변경 0). required-set 검사를 여섯 validator 앞으로 옮기고, 서비스 값 모양 검사(`null`은 부재가 아니라 invalid — S4의 안전 조건이다)를 더하고, 15개 이름 루프의 무조건 인덱싱을 `.get()` + 계약 오류로 바꿨다. **required 집합은 한 글자도 바꾸지 않았다.** 판정 변경 0은 적대 리뷰가 독립으로 재확인했다 — 형상 36종 × 진입점 2개 = **72칸에서 verdict flip 0, 예외 타입 변경 0**. 순서 교정의 파급이 기존 검사 하나에서 드러나 fragment를 완전하게 만들어 의도를 보존했다. **정정(적대 리뷰 2026-09-17)**: 첫 기록이 "`absent_*` → missing required protected services"라고 단정했는데 **15개 중 하나에서 거짓**이다. `pinvi-db-init`은 frozenset(14) 밖이라 `_validate_pinvi_db_init_identity`가 먼저 걸러 `PinVi database init identity is invalid`를 낸다. 실질 15의 정체가 바로 그 비대칭이고, 이제 골든 테이블이 그 예외를 명시적으로 박는다.
- [x] GM-17 B · S0/S1 적대 리뷰 반영 (2026-09-17). 리뷰어 둘이 **각각** 같은 급소를 찾았다 — **골든 테이블이 자기 목적을 수행하지 못했다**. 첫 판은 `PASS/not-PASS`만 봤는데, required 집합을 `frozenset()`으로 비워도, 이름 하나를 빼도 전체 스위트가 초록이었다. 완화는 "거부 → 통과"가 아니라 **"거부 이유 A → 거부 이유 B"**로 움직이기 때문이다(S1이 넣은 `.get()` 가드가 전부 받아낸다). 고친 것: (1) 15개 서비스 **단독 부재**를 전수로 돌고 **이유를 박는다**, (2) **resolved 열 추가** — 프로덕션 diff의 절반이 무증거였다 (resolved 쪽 수정 셋을 전부 되돌려도 초록이었다), (3) required 집합과 15개 루프 이름을 **리터럴로** 고정(파생하면 S4가 비웠을 때 표도 함께 비어 공허하게 초록이 된다), (4) 죽은 중복 `isinstance` 블록 2곳 제거 — 하필 죽은 쪽이 raw/resolved를 **구분하는** 문구를 갖고 있었다, (5) 15개 루프에서 **부재와 invalid를 분리**(`.get()`이 둘을 `None` 하나로 뭉갠다 — S4에서 그대로 두면 S1이 없앤 혼동이 한 층 아래에서 되살아난다), (6) 후보가 정한 **임의 서비스 키가 계약 오류 문구로 에코**되던 것을 sha8로 가림 (그 문구는 CLI stderr와 HTTP body로 나가고, 이 루프는 보호값 전역 스캔보다 앞이다), (7) `_verdict`의 200자 절단 제거 — 그 절단 때문에 변이 하나가 **우연히** 죽어 있었다. 변이 검증: 리뷰가 살아남았다고 보고한 여섯(required에서 이름 하나 제거 ×2, 무조건 인덱싱, shape 루프 한정, resolved만 순서 되돌림, 완화+인덱싱)이 **전부 빨개진다**.
- [x] GM-17 B 후속 — **죽은 계약 테스트 3건** (2026-09-17 완료). `backend/tests/test_docker_service_config.py`의 `:1905`·`:2257`·`:2324`는 `pytest.raises(ComposeCandidateContractError)`에 `match=`가 없고, compose 문서에 `rustfs` 하나뿐이라 required 14개가 전부 빠져 있다. 실제로는 함수 맨 앞의 `_validate_map_production_secret_values`가 `KOR_TRAVEL_MAP_ADMIN_PROXY_SECRET must contain at least 32 characters`로 먼저 던지고, **payload를 아예 비워도 통과한다**(적대 리뷰 2026-09-17 실측). 겨냥한 보호 이름 스캔에는 도달조차 하지 않는다. **S4가 착지할 자리가 정확히 여기다** — 서비스 없는 compose로 mutation 경계를 덮는 유일한 테스트인데 아무것도 태우지 않는다. `match=` 추가 + `_prepare_candidate_transaction`(`:1769`)의 env 분기가 rustfs-only 문서에도 걸리게 고칠 것.
- [x] GM-17 B · S2 — Map 계열 validator 삼분할 (2026-09-17 완료, **진입점 기준** 동작 변경 0). `_validate_map_postgres_password_secret`을 셋으로 쪼갰다: (A) `_validate_map_postgres_password_owner_wiring` — 소유자 배선, 소유자 부재 시 건너뛴다; (B) `_assert_map_postgres_password_sole_consumer` — 유일 소비자 스캔; (D) `_validate_map_postgres_password_declaration` — 최상위 `secrets` 선언. **(B)와 (D)는 family validator 블록 밖, 진입점에 직접 있다.** 인가 집합은 `_authorized_map_postgres_password_reference`가 독립 파생하며 **모양(source/target)까지 검증**하고 소유자가 없으면 공집합이다. **적대 리뷰 둘이 각각 첫 판을 뚫었다.** 처음에는 (B)·(D)를 Map validator 안에 두고 "합성 진입점에 결박했다"고 적었는데, S4가 자르는 층은 그 위였다 — 진입점의 호출부를 소유자 존재로 감싸는 순진한 S4가 **전체 스위트 1,700건을 그대로 통과**했고, 그 상태에서 `pinvi-api`가 Map superuser password를 마운트하는 후보가 mutation 경계를 통과했다. 이중화로 막으려 한 두 번째 시도도 틀렸다 — 둘 중 하나를 지우는 변이를 **아무 검사도** 잡지 못했다(검사 불가능한 방어). 최종적으로 자리를 **하나**로 만들고 wrapper를 제거했다. 리뷰가 실증한 그 시나리오를 재현해 확인: 게이팅 + required 축소 + 15개 루프 우회를 모두 적용해도 이제 `unauthorized consumer`로 **거부**된다. 리뷰가 찾은 선재 공백 둘도 메웠다 — 소유자의 exact-target 불변식(지워도 1,700건이 전부 통과했다)과 짧은 문법(`secrets: ["<이름>"]`) 참조 축. 정정 둘: `authorized is None` 논리합은 **오늘 판정을 바꾸지 않는다**(1,782문서 × 2형상에서 변이와 동치) — S4를 위해 남기되 그것이 지금 일한다고 주장하지 않는다. 그리고 함수 기준으로는 8칸이 달라진다(전부 required-set이 막는 소유자-부재 영역) — **진입점 기준** 0이다.
- [x] GM-17 B · S3-a — PinVi PostgreSQL 신원을 db-init 게이트에서 분리 (2026-09-17 완료, 동작 변경 0). 종전 `_validate_pinvi_db_init_identity`는 맨 앞에서 `pinvi-db-init` 부재를 즉시 거부한 뒤, **같은 함수 안에서** `pinvi-postgres`의 image·environment·command를 검사했다. 그 command 배열이 `listen_addresses=127.0.0.1`을 강제하는 **저장소에서 유일한 자리**다. 따라서 S4가 그 함수를 db-init 존재로 게이팅하면 PostgreSQL의 loopback 결박이 통째로 사라진다 — 네트워크 노출 통제라 S2의 secret 소비자 스캔보다 결과가 나쁘다. **순서를 보존하려고 둘이 아니라 셋으로 나눴다**: (1) `_validate_pinvi_db_init_presence` (db-init 게이트 대상) → (2) `_validate_pinvi_postgres_identity`(**pinvi-postgres** 게이트 대상, loopback 포함) → (3) `_validate_pinvi_db_init_command`(db-init 게이트 대상). 둘로 나누면 (2)와 (3)의 선후가 뒤집혀 두 결함이 동시에 있는 문서의 문구가 바뀐다. 분할 중 (2) 안에 **db-init의 image를 보는 한 줄**이 있는 것을 발견했다(자리가 두 postgres 검사 사이라 옮기면 순서가 바뀐다) — 자리를 두고 존재-조건부로만 만들었다. **변이 검증에서 내 첫 시뮬레이션이 뚫렸다**: db-init 전용 validator만 no-op으로 만들고 **문서에는 db-init을 남겨 둬서**, 게이팅 변이가 검사 64건을 전부 통과했다. S2에서 배운 것과 같은 실패다 — S4가 실제로 만드는 형상(서비스가 사라진 상태)에 결박해야 한다. 고친 뒤 변이 넷이 각각 2·4·2·2건을 빨갛게 만든다. `listen_addresses` 강제가 한 곳뿐이라는 전제 자체도 검사로 박았다.
- [x] GM-17 B · S3-b — PinVi password validator 삼분할 (2026-09-17 완료, **판정 변경 0 / 진단 문구는 정정 후 0**). 선언 검사와 유일 소비자 스캔은 family scope가 걸리지 않는 전역 불변식으로, 소유자 배선만 조건부다. 인가 파생은 **모양까지 검증**한다 — PinVi 소유자 참조는 짧은 문법과 두 가지 target을 허용하므로 무검증 파생은 Map보다 위험하다. 인가 집합이 셋인 것이 PinVi의 특징이다(소유자는 파생, `pinvi-db-init`·`pinvi-db-runtime-role`은 **리터럴**이라 소유자와 무관하게 유효하다). **적대 리뷰 둘이 각각 첫 판을 뚫었다.** (1) 전역 둘의 **자리**를 옮긴 탓에 진입점 메시지가 바뀌었다 — 리뷰 A는 1,434형상에서 **215칸**, 리뷰 B는 66형상에서 8칸. 내 추론의 구멍: 소비자 스캔을 family 블록 뒤에 두면서 (A) 대 (B)의 선후만 따졌는데, 종전에 그 스캔은 **같은 family의 validator 전부보다 앞**이었다. 실제 권한상승 시도가 image provenance 문구 뒤로 숨는 형상이 생겼다 — **S1이 고친 바로 그 병이다.** 리뷰가 정정한 개념: **자리(position)와 게이팅(gating)은 다른 축**이고, "family 블록 밖"은 위치가 아니라 **scope 조건이 걸리지 않음**을 뜻한다. 전역 여섯을 원래 자리로 되돌려 고쳤고, **S2 이전(`df40e57`) 대비 78개 이중결함 형상에서 차이 0**으로 확인했다. 그 과정에서 **S2도 같은 회귀를 냈다는 것이 드러났다** — 현 main(`9bfabea`) 대비 8칸이 다른데 전부 Map 쪽이고, 이 PR이 그것까지 함께 고친다. (2) "Map(S2)과 같은 배치다"는 **코드 배치에 대해서만** 참이고 **검증에 대해서는 거짓**이었다 — S2가 Map에 넣은 결박 검사 넷이 PinVi에 복제되지 않아, 전역 둘을 게이팅·인라인·간접호출하는 변이 넷과 소유자 배선의 핵심 검사 셋이 **전부 생존**했다. 진입점 S4 시뮬레이션 검사를 넣어 열 변이가 모두 빨개진다. (3) `entrypoint` 가드가 양쪽 다 무검사였다 — `entrypoint`를 주면 `command`는 인자가 되므로 **command를 한 글자도 안 바꾸고** loopback을 무력화할 수 있는데, 그 유일한 방어를 지우고도 1,721건이 초록이었다. 검사를 넣었다. (4) loopback 결박의 자리를 세는 검사(당시 이름 `..._has_exactly_one_home`, 뒤에 `..._has_two_load_bearing_homes` — 2026-09-28 PinVi 전용 instance 퇴역과 함께 삭제)가 **파일 목록**을 비교해 같은 파일의 두 번째 자리를 못 봤다(docstring이 거짓이었다). AST로 **코드 리터럴 출현 횟수**를 세도록 고쳤다 — 문자열을 그냥 세면 설명을 더 쓸 때마다 빨개져서 결박이 아니라 잡음이 된다. (5) S4 시뮬레이션이 frozenset만 패치하고 **하드코딩 15개 루프**를 안 풀어 목표 지점에 닿기 전에 막히고 있었다. 둘 다 패치하도록 고쳤다.
- [x] **PostgreSQL 인증 우회 두 경로를 막았다** (2026-09-17 완료). 감사가 Map↔PinVi 60필드를 전수로 재서 찾았다 — Map postgres는 PinVi의 `_validate_pinvi_postgres_identity`에 해당하는 **서비스 신원 validator가 없어서** image·command·entrypoint·INITDB_ARGS가 통째로 무검사였다(Map 10/60 거부, PinVi 22/60 거부). (1) `POSTGRES_INITDB_ARGS=--auth-host=trust`가 Map에서 raw·resolved·**UI 저장 경로까지** 전부 통과했다 — 계약표에 Map 항목을 넣어 막았고, `_CONTRACT_LOCKED_ENV_NAMES_BY_SERVICE`가 같은 dict에서 파생되므로 UI 잠금도 따라온다. 두 서비스가 **공유 상수**를 쓰게 해서 한쪽만 바뀌는 것도 막았다. (2) 더 나쁜 것은 `POSTGRES_HOST_AUTH_METHOD=trust`가 **양쪽 모두** 통과한 것이다 — 계약 기계가 **값 동등** 비교라 "키가 추가됐다"를 표현할 수 없기 때문이다. 이것은 계약표로 막을 수 없으므로 이름 자체를 금지하는 **전역** 검사를 넣었다(어느 서비스가 들고 있든 결과가 같다). 정본 compose는 그 키를 어디에도 쓰지 않는다(전수 확인). fixture의 map-postgres 정의도 정본과 같게 보강했다 — S1에서 한 것과 같은 처방이다(검사를 약하게 하지 않고 fragment를 완전하게).
- [x] **Map postgres의 `command`·`entrypoint`가 무검사였다 — 인증 축보다 강했다** (2026-09-18 완료). 종전 원장은 이것을 "인증 축만 닫았다"는 **잔여**로 적었는데 심각도가 틀렸다. 적대 리뷰 실측: `-c hba_file=<경로>`는 pg_hba를 **통째로 갈아치우므로** `--auth-host=trust`와 결과가 같고, **fresh PGDATA를 요구하지 않아** 이미 도는 클러스터에도 즉시 적용된다. 즉 initdb 축을 731줄로 지키는 동안 검사 한 줄 없는 축이 더 확실하게 그것을 무너뜨리고 있었다. `entrypoint`를 주면 `command`가 인자로 강등되는 것도 무검사였다. 그리고 `listen_addresses` 강제는 저장소에 **PinVi 하나뿐**이었다 — 네 PostgreSQL이 전부 loopback을 쓰는데 검증은 하나만 봤다. 전역 술어 하나로 넷을 덮었다: 클러스터 서비스는 `entrypoint`·`privileged`·`user`·`cap_add`·`pid`를 선언할 수 없고, `command`는 `postgres`로 시작해 loopback을 박아야 하며, `-c hba_file` 계열 설정을 가질 수 없다. **이름을 열거하지 않는다** — 정본 compose의 넷이 전부 같은 형태라 (실측) 다섯째가 생겨도 따라온다. `listen_addresses` 결박이 이제 **두 자리**인데 둘 다 일이 다르고 둘 다 결박돼 있다(전역 바닥을 지우면 geo·concierge·map이, PinVi 쪽을 지우면 exact-match가 빨개진다) — "중복은 결박을 없앤다"의 조건이 성립하지 않는다.
- [x] **Concierge UI 게이트가 API 계약을 함께 끄고 있었다** (2026-09-18 완료). 14-에이전트 감사가 "존재 게이트 뒤에 숨은 전역 불변식"의 **네 번째 인스턴스**를 찾았고, S2·S3와 달리 이것은 S4를 기다리지 않았다 — **main에서 이미 열려 있었다.** `_validate_concierge_ui_canonical_contract`가 `UI not in services`면 함수째 return하는데 그 안에 **concierge-api의 계약 전부와 Manager root env 불변식**(PBKDF2 형식·반복수, 32자 secret, API_KEYS 소속, 12601/12605 포트)이 있었다. concierge 두 서비스는 required set 밖이라 후보에서 UI 한 줄만 지우면 API는 남은 채 전부 꺼졌다. 실측: `API_AUTH_ENABLED=false` + `network_mode: bridge` + 임의 proxy secret인 API가 두 진입점을 통과했다. **방향이 비대칭**이었다 — API 삭제는 거부, UI 삭제는 통과. 게다가 raw 정본 기본값이 `${KOR_TRAVEL_CONCIERGE_API_AUTH_ENABLED:-false}`라 **compose를 한 글자도 안 바꿔도** UI 삭제 + root env 미설정만으로 통과했고, API는 `--host 0.0.0.0` + `network_mode: host`이므로 대상이 전 인터페이스였다. **첫 처방이 틀렸다**: "API가 있으면 검사"로 고치자 정당한 후보 9건이 빨개졌다 — `_compose_fragment`가 의존성으로만 끌어온 서비스는 `image` 하나뿐인 **stub**이고 (`concierge-api`·`geo-api`·`rustfs`가 같은 모양), Map API가 concierge를 HTTP로 부르므로 **Map 단독 target 후보에 concierge-api가 stub으로 들어오는 것이 정상**이다. UI 게이트에 이유가 있었던 것이다 — UI의 존재가 "concierge를 실제로 배포한다"는 신호다. 그래서 게이트를 **"UI 존재 또는 API가 구성됨"**으로 바꿨다. "구성됨"은 **키 존재가 아니라 값이 있음**으로 판정한다 — `docker compose config`가 stub에도 `command: null`·`entrypoint: null`을 붙이므로(raw stub `['image']` vs resolved stub `['command','entrypoint','image','networks']`) 키로 보면 resolved에서 모든 stub이 오인된다. **검사 순서는 한 줄도 바꾸지 않았다**(S3-b가 그 실수로 215칸을 바꿨다) — 각 검사에 자기 주체의 조건만 달았으므로 **동작이 바뀌는 형상은 정확히 구멍 하나**다. 변이 검증: 게이트 되돌리기 5 / 키 존재로 판정 4 / environment 축만 2 / stub도 구성됨 10건 빨감.
- [x] GM-17 B · S3-c — PinVi DSN의 **전역 env 불변식 분리** (2026-09-18 완료, 동작 변경 0). `_validate_pinvi_database_url_identities`의 앞 절반은 서비스와 무관한 env 불변식이었다 — `PINVI_DB_PORT == 12800` 고정, role 이름 5자 상호 상이성과 정규식, root/app/migrator password 3자 상호 비동일. 뒤 절반만 `services`를 보고 그쪽은 이미 존재-조건부였다. 둘이 한 함수에 있어서 S4가 게이팅하면 앞 절반까지 함께 꺼졌다 — **S3-a가 loopback에 대해 고친 것과 같은 모양**이고, 비대칭이 결정적이었다(Map 쌍둥이 `_validate_map_database_dsn_identities`는 이미 `environment`만 받는다). **제자리에서 쪼갰다.** 감사가 396형상으로 실측했다 — 제자리 분할은 메시지 변경 0칸, 여섯 global invariant 앞으로 올리면 25칸, Map DSN 자리로 올리면 46칸이 바뀌고 그중 일부는 **S1의 "부재를 부재라고 말하기"를 되돌린다.** S3-b가 자리를 옮겨 215칸을 바꾼 실수를 반복하지 않았다 — 필요한 것은 위치가 아니라 **조건이 걸리지 않음**이다. 직접 재확인: origin/main 대비 78개 이중결함 형상에서 메시지 차이 0, verdict flip 0. 전역 절반은 **`services`를 인자로도 받지 않는다**(게이팅할 재료 자체를 없앴고, 검사가 시그니처를 결박한다). 변이 검증: 전역 절반 게이팅 5 / 포트 핀 제거 4 / role 상이성 2 / **role 정규식 1**(감사 실측으로 종전 커버리지 **0**이었다) / password 3자 2 / per-service 절반 제거 2건 빨감. 감사가 지목한 구체적 피해도 검사로 박았다 — `PINVI_DB_PORT=12700`이면 두 PostgreSQL이 모두 `network_mode: host`로 127.0.0.1:12700을 잡는 후보가 통과하는 **Map 대역 탈취**이고, 저장소에 host 포트 충돌 검사는 없다. owner 쌍(`APP_SCHEMA_OWNER`/`MIGRATION_OWNER`)은 **어느 DSN에도 나타나지 않아** per-service 비교가 구조적으로 못 보므로 전역 절반이 유일한 자리다.
- [x] GM-17 B 후속 — `expected_*` 사본 둘이 조용히 갈라질 수 있다 (리뷰 M-3). **2026-09-28 해당 없음** — 대상 코드가 옛 PinVi 전용 instance(`pinvi-postgres`/`pinvi-db-init`)와 함께 삭제됐다. `_validate_pinvi_db_init_presence`가 넷을 파생하고 `_validate_pinvi_postgres_identity`가 셋을 **독립으로 재계산**한다. 종전에는 한 지역 변수라 구조적으로 일치했다. 오늘 그 우연을 지키는 것은 `PINVI_DB_PORT == 12800` 강제 한 줄뿐인데 그것이 S3-c의 대상이다. 처방: `PINVI_DB_PORT`·`PINVI_POSTGRES_BOOTSTRAP_DB`를 비기본값으로 주입한 환경으로 진입점을 태우는 파라미터 축 검사(테스트 fixture가 그 둘을 아예 안 줘서 "env를 읽는가"가 검사되지 않는다). 삭제된 docstring "reset 대상과 같은 loopback DB를 가리키는지"도 복원할 것.
- [x] GM-17 B 후속 — **2026-09-28 해당 없음** — 대상 코드가 옛 PinVi 전용 instance(`pinvi-postgres`/`pinvi-db-init`)와 함께 삭제됐다. `pinvi-db-init`의 `image` 키가 **없으면** provenance 검사가 조용히 통과한다 (리뷰 L-5, `.get("image")`가 `None`을 돌려주는 경로). 오늘은 (1)이 `environment`까지 요구해 위험이 낮지만 S4가 (1)을 게이팅하면 열린다. 조건을 "존재하면 image가 정확히 `_PINVI_POSTGRES_IMAGE`"로 강하게 쓸 것.
- [x] GM-17 B 후속 — **2026-09-28 해당 없음** — 대상 코드가 옛 PinVi 전용 instance(`pinvi-postgres`/`pinvi-db-init`)와 함께 삭제됐다. `_validate_pinvi_db_runtime_role`의 `depends_on`이 `pinvi-db-init: service_completed_successfully`를 리터럴로 요구한다. S4가 db-init만 scope에서 빼고 role을 남기면 즉시 죽는다 — 선행 조사 (2)의 런타임 결합 목록에 추가.
- [x] **멀티프로젝트 target(#347)의 치명 회귀 셋을 고쳤다** (2026-09-18). 적대 리뷰가 머지된 상태에서 뚫었다 — 그리고 그 리뷰가 없었다면 `ktdctl logs`가 **모든 이름**에서 죽은 채로 남았을 것이다. (C-1) `external`을 `build_command`와 `_run_unlocked`에는 더했는데 **그 둘 사이의 공개 진입점 `run()`에는 더하지 않았다**. `logs`는 `external=None`도 무조건 넘기므로 weather·airport뿐 아니라 map·db·all까지 `TypeError`였다. 그런데 1770건이 전부 초록이었다 — 새 검사 344줄이 `build_command(external=…)`를 **직접** 부르는 층에만 결박돼 있어서, 그 아래 배관이 끊겨 있든 이어져 있든 구별하지 못했다. 리뷰가 실측한 변이 `run이 external을 받되 버린다`도 전체 스위트 생존이었다. (C-2) **compose는 `-f`를 `--project-directory`가 아니라 cwd 기준으로 푼다.** `_run_unlocked`가 `cwd=get_project_root()`로 돌아서 `airport`의 `-f docker-compose.yml`은 **Manager 자신의 compose**를 열었다(리뷰 실측: Manager의 concierge-ui 보간 오류). PR 본문의 "n150 실측"은 손으로 그 디렉터리에 들어가 친 것이라 이 경로를 통과한 적이 없다. (C-3) `compose_service`는 **그 프로젝트 안에서만** 유일한데 Manager가 그 값을 자기 compose에 곧장 조회했다 — weather의 `prometheus`가 Manager의 것과 겹쳐서, 목록 화면이 남의 카드에 Manager 설정을 그리고, 그 화면의 저장이 Manager compose로 가고, 없는 컨테이너 Start가 **Manager의 Prometheus를 만들어 놓고 성공을 보고**했다. 변경 세 경로(update·reset·NotFound 재생성)가 `_update_container_config_unlocked` **하나**로 모이므로 guard를 거기 한 자리에만 뒀다 — 호출부로 흩으면 한 벌을 지워도 아무 검사가 빨개지지 않는다(S2에서 배운 것). SDK 경로(start/stop/restart)는 프로젝트 무관이라 막지 않았다. 함께 고친 것: (M-1) 외부 호출이 `os.environ`을 통째로 상속했다 — Compose에서 셸 환경은 `.env`보다 **우선**하므로 `--env-file`을 빼는 것만으로는 주석이 막겠다던 일을 못 막는다. (M-2) 묶음 집계에 `returncode`가 없어 전부 running이어도 `ktdctl status weather`가 exit 1이었다. (H-1) 소속 무결성 3중 검사가 **테스트의 assert**였다 — 저장소 설정만 보므로 설치본에는 무력이고 `ktdctl targets validate`도 못 잡는다. 셋을 `_validate_targets_config` 안으로 옮겼다. (H-2) 외부 target의 `services`가 아무것과도 대조되지 않았다 — 저장소 밖 compose는 열 수 없으므로 **선언끼리** 묶었다(그 프로젝트 컨테이너의 `compose_service`는 전부 target의 `services`에 있어야 한다). 반대 방향은 강제하지 않는다 — one-shot은 `services`에만 있는 것이 이 저장소 규칙이다. (H-3) 같은 project를 다른 좌표로 선언하면 묶음은 갈리는데 컨테이너 소유 해석은 이름으로 첫 매치를 골랐다. (M-3) one-shot `kor-travel-weather-migrate`를 `containers:`에서 뺐다 — 그 규칙을 적은 주석 12줄 뒤에서 어긴 것이었다. (M-4) Manager target이 외부 target에 의존하면 **Manager target의** 배포가 막히는데 메시지는 Manager를 탓한다 — 선언 시점에 막고, `all.include`가 Manager target을 빠뜨리지 않는 것도 함께 못박았다. (L-1) `config_files` 중복과 `startswith("..")` 오탐. (L-2) 새 target 셋이 `compose_binds` 헤더 주석 안쪽에 들어가 있던 것을 앞으로 뺐다. **검사를 한 층 아래에 결박했다** — `subprocess.run`을 가로채 실제 argv와 **cwd**를 보는 `test_multi_project_execution.py`가 새 자리다. 변이 7종이 전부 죽는다(run이 external을 버림 4 / cwd를 Manager 루트로 3 / env 상속 1 / returncode 제거 2 / 외부 config를 Manager compose에서 1 / 외부 변경 guard 제거 2 / 배선 검증 무력화 8 — 앞의 셋은 적대 리뷰의 재실측으로 정정한 값이고, 내 첫 표는 변이의 **형태**를 적지 않아 재현되지 않았다).
- [x] **"전역"이라 적은 검사 둘이 전역이 아니었다** (2026-09-18 완료, 적대 리뷰 2라운드까지). (1) `POSTGRES_INITDB_ARGS`를 **계약표**로 막았는데 계약표는 서비스를 **열거**한다 — 저장소 compose에 그 키가 넷(geo·concierge·pinvi·map)인데 앞의 둘은 계약표에도 validator에도 UI 잠금에도 없었다. `--auth-host=trust`가 raw·resolved·UI 저장 **세 진입점을 전부** 통과했고, fresh PGDATA에서 initdb가 그 값을 pg_hba **첫 행**으로 쓰므로 12500/12600에 비밀번호 없는 superuser가 생긴다. official entrypoint가 그 값을 `eval`에 넣으므로 셸 주입 벡터이기도 하다. 처방은 계약표에 두 줄 추가가 아니라 `_assert_no_postgres_auth_override`와 **대칭인 전역 술어**다. (2) Concierge "구성됨" 신호가 `environment`·`command`·`network_mode` 셋뿐이라 `entrypoint`로 `--host 0.0.0.0`을 주고 `ports`로 전 인터페이스에 게시하고 `env_file`로 concierge `.env`(정본 compose가 **이미 쓰는** 통로이고 proxy secret이 거기 있다)를 읽는 후보가 세 신호를 한 글자도 건드리지 않고 통과했다. `entrypoint`는 내 docstring이 "resolved stub이 `entrypoint: null`을 갖는다"며 **언급까지 했는데** 집합에 넣지 않았다. **2라운드에서 내 수정 자체가 뚫렸다.** PinVi 신원 검사의 exact-match dict는 `.get()` 비교라 **키 부재도 거부**했는데, "자리를 하나로" 한다며 그 항목을 뺄 때 그 성질이 함께 사라졌다 — 리뷰어가 실제 postgres 컨테이너로 실측한 결과 **부재의 pg_hba는 `trust`와 바이트 단위로 같다**(이 저장소 compose 주석이 이미 그렇게 적어 뒀다). 960형상 대조에서 약화된 14칸이 전부 그 형상이다 — 중복 제거는 옳았지만 **남긴 쪽이 원래보다 약했다.** 그래서 값이 아니라 **존재를** 묶고, PostgreSQL을 **효과로** 식별한다(`POSTGRES_PASSWORD`/`POSTGRES_PASSWORD_FILE` — entrypoint가 그것 없이는 initdb를 돌리지 않는다). 이미지로 넓히면 같은 postgis 이미지를 쓰는 `pinvi-db-init`(psql one-shot)까지 걸려 fragment 13건이 빨개졌다. 함께 닫은 것: `PGDATA` 재지정(**"fresh PGDATA에서만"이라는 전제를 공격자가 만들 수 있다**), 클러스터 서비스의 `env_file`(raw 층에서 전역 술어를 통째로 우회했다 — 종전에는 금지가 열거된 서비스에만 걸렸다), UI 저장 경로의 **삭제**와 `str()` 정규화, 매핑형 `{키: None}`(`docker compose config`가 bare 이름을 그 형태로 정규화한다), 신호 셋의 truthy 판정, `ui: null`을 부재로 오인하던 것, geo·concierge를 아는 이름에 넣어 거부 문구가 서비스를 지목하게 한 것. 검사 40여 건 추가. 자리 선택은 실측으로 골랐다 — 새 전역 술어는 `_assert_no_postgres_auth_override` 바로 뒤, 새 런타임 술어는 **family validator 뒤**다(PinVi의 exact-match가 더 강하므로 그쪽이 먼저 말해야 한다).
- [x] **운영 중인 PostgreSQL의 비밀번호 없는 물리 복제를 닫았다** (2026-09-18 적용·검증). 인스턴스는 넷이 아니라 **여섯**이었다 — `kor-travel-weather-db-1`(:14100)을 처음 목록에서 빠뜨렸고 그쪽은 replication `trust`가 그대로였다. 여섯 대 모두 `host … replication … trust`를 `scram-sha-256`으로 고치고 SIGHUP reload했다(백업 `pg_hba.conf.bak-20260918T032837Z` / `…T045253Z`). **`local … trust` 행은 건드리지 않았다** — 컨테이너 내부 소켓 접속이고 entrypoint·healthcheck·db-init one-shot이 전부 그것에 의존한다. `pg_ctl reload`는 root로 못 돌아 `docker kill --signal=HUP`(PID 1 = postmaster, 재시작 아님)로 떨어졌다. 검증은 **문구가 아니라 효과로** — 각 컨테이너에서 빈 `PGPASSWORD`로 replication 접속이 `Password for user …`를 받는지 봤다(`pg_hba_file_rules`는 디스크 파일을 다시 읽으므로 reload 증거가 아니다). 31개 컨테이너 uptime·healthy 불변. **남은 것**: airport-db와 weather-db의 `host all all 127.0.0.1|::1 trust` 4건 — 외부 프로젝트 데이터 플레인이고 bridge NAT라 호스트 loopback에서 미도달이다(실측: 클라이언트 주소가 192.168.192.2/.3). 그 저장소 오너 몫이다.
- [x] ~~운영 중인 네 PostgreSQL이 비밀번호 없는 물리 복제를 허용한다~~ — **바로 위 항목이 이것을 대체한다**(같은 사안이 두 줄로 열려 있었다, 적대 리뷰 2026-09-18 라운드4 F14). 인스턴스는 넷이 아니라 여섯이었고 여섯 대 모두 닫았다.
- [x] **금지 목록을 허용 목록으로 뒤집었다** (2026-09-18 4라운드 완료). 적대 리뷰가 같은 병의 세 번째 재발을 찾았다 — 매번 "효과에 결박했다"고 적었는데 매번 **철자에** 결박돼 있었다. 실측으로 통과한 것: `-i` 한 토큰(= `listen_addresses='*'`), `-h0.0.0.0`, `-clisten_addresses=…`(붙여쓴 getopt 형), `-chba_file=…`, 그리고 식별 쪽은 `/usr/local/bin/postgres`·문자열 command·`sh -c 'exec postgres …'`. map·geo·concierge 세 대가 `network_mode: host`라 `-i` 한 토큰이 12500/12600/12700을 LAN에 열었다. 정본 넷의 형태가 좁고 고정적이므로(최상위 키 11개, GUC 12개 + `-p`) **모르는 것이 하나라도 있으면 거부**로 뒤집었다 — 허용 목록 하나가 `hba_file`·`config_file`·`data_directory`·`-D`·`-k`를 따로 열거하지 않고 전부 막고, 다음 compose 스펙이나 postgres 버전이 무엇을 추가해도 fail-close다. `entrypoint`는 목록에서 빼는 것으로 금지가 자동 성립한다. 식별은 `in_scope = declared OR witnessed`로, declared는 `config/docker-targets.yml`의 `role`(`*postgresql`)에서, witnessed는 **프로그램 자리**의 basename(argv[0] / 셸의 `-c` 페이로드 첫 낱말 / `exec` 뒤)에서 온다 — 토큰을 전부 쪼개 보는 첫 판은 `psql -d postgres`의 **DB 이름**을 흔적으로 세서 정당한 one-shot을 죽였다. **witnessed인데 declared가 아니면 그 불일치 자체를 거부**하므로 여섯째 postgres를 `role: db`로 선언해도 새어나가지 않는다. 특권 축은 **문서 전역**으로 올렸다 — 정본 34 서비스 중 열한 개 키는 사용 0건이고 `privileged`·`devices`는 cadvisor, `user`는 prometheus·grafana뿐이라 "전역 금지 + 예외 넷"이 가능하다. 방향이 계약표와 **반대**여서 새 서비스가 그 키를 들면 기본이 거부다. 이것이 리뷰어 둘이 각각 실측한 호스트 root 경로(`concierge-api`에 `privileged: true` + `pid: host`)를 닫는다.
- [x] **살아있는 `pg_hba`를 관측하는 축을 만들었다** (2026-09-18). compose 문서 검사와 live 상태 검사는 다른 축이고 저장소에 후자가 **0건**이었다 — `POSTGRES_INITDB_ARGS=--auth-host=scram-sha-256`은 **initdb 시점에만** 적용되므로 그 값이 계약에 들어오기 전에 만들어진 PGDATA는 그대로 남는다(n150 여섯 인스턴스에서 실측된 무자격 복제가 그 결과였다). `services/postgres_hba_posture.py`에 probe/decide 2분할로 두고 `deployment_readiness`의 check 행으로 결선했다. **설계 조사가 내 과제문의 오류를 잡았다** — `type = 'host'`는 틀린 술어다(`hostssl`·`hostnossl`·`hostgssenc`도 TCP라 `hostnossl all all 0.0.0.0/0 trust` 한 줄이 통과한다). `type <> 'local'`이 맞고, `local … trust`는 컨테이너 내부 소켓 접속이라 **건드리면 안 된다**(이 모듈 자신이 그것으로 붙는다). 자격증명을 읽지 않는다 — 소켓 접속이 `local all all trust`에 매칭되고, `$POSTGRES_PASSWORD_FILE`을 읽는 것은 외부 둘에 없고 `validate_*_postgres_runtime_secret_isolation`과 충돌하며 평문을 남기므로 **금지**다. 식별은 `MANAGED_CONTAINERS` 후보 집합 + 라이브 `Cmd[0]`·`Entrypoint` 증명이고, `role`은 게이팅이 아니라 **대조**에 쓴다(불일치 자체가 소견 — 그것이 항진명제를 막는다). 포트는 세 단계로 유도한다(`standalone_backup._discover_port`를 재사용하면 `Cmd`가 정확히 `["postgres"]`인 외부 둘이 **검사에서 사라진다**). 세 상태(통과/증명된 위반/`unknown`)이고 **0행·파싱 오류·파일이 reload보다 새로움은 전부 `unknown`**이다. 등급을 소유권으로 나눴다 — Manager 소유는 `missing`(차단), 외부는 최대 `warn`(`pinned_rebuild_preflight`가 `missing`을 재구축 blocker로 올리므로, 남의 DB로 Map 재구축을 막으면 안 된다). 함께 고친 것: `login_rate_limit_proxy`가 `_CHECK_LABELS`·`_CHECK_ORDER`에 없어서 `_unknown_payload` 응답에서 그 행이 조용히 사라졌다. 검사 25건.
- [x] GM-17 B · 선행 조사 (1) `running_from_trusted_install_root()` (2026-09-17 실측, **참**). n150의 두 실행 경로 모두 `/opt/kor-travel-docker-manager/backend/.venv`에서 돈다 — 상주 backend(`ktdm-backend.service` `ExecStart`, active·enabled, 12901 리스닝)와 pinned-rebuild 런처(`run-pinned-rebuild-once:319`가 `/opt/.../\.venv/bin/ktdctl`). 술어를 그 venv에서 직접 태워 확인했다: `__file__`이 trusted root 아래(분기 1 참), `sys.prefix == TRUSTED_INSTALL_ROOT/backend/.venv`(분기 2 참) → `True`. 호스트의 다른 `ktdctl`은 `/home/digitie/ktdm-gate/.venv` 하나뿐인데 **2026-08-30 #291 시점 체크아웃이라 `trusted_install` 모듈 자체가 없다**(`ModuleNotFoundError`) — 없는 메커니즘을 우회할 수는 없고, prod 검증 경로에 결선돼 있지도 않다. **결론: `declared` 축(= `config/docker-targets.yml`, GM-17 A가 신뢰시킨 문서)의 권위가 S4가 의존하는 모든 경로에서 유지된다.**
- [x] GM-17 B · 선행 조사 (2) 런타임 전제 전수 (2026-09-17). **"15개를 전제하는 자리"는 하나가 아니다 — 네 모듈에 걸친 여섯 집합이고, S4가 완화하는 것은 그중 하나뿐이다.**

## 2026-10-16 — ADR-58 registry 호스트 차원·변경 경계(T-MGR-01a·01a-F·01b·06j)

- [x] **T-MGR-01a — registry 호스트 차원**: `hosts:`·target `host:`·R1 연결 주소·R3 자기 식별·호스트별 `all` 필터·
  `targets validate --json`(#477), root `.env`의 `KTDM_HOST` 읽기(#482). n150 설치.
- [x] **T-MGR-01a-F — #477 리뷰 잔여**(#483, `33ec707`): 설치기 flip 전 host-identity 검사, "로컬 선언 호스트 정확히 하나"
  자기 식별(불일치 시 주소 캐시 비우고 한 번 재probe), `sorted(owners)[0]` 제거, 주소 조회 프로세스당 1회, 세 번째 가상 호스트
  fixture, 호스트 리터럴 검사·허용 목록. n150 설치.
- [x] **T-MGR-01b — R4 명령 host 규칙 + R7 교차 호스트 간선**(#481): 직접 소유권 변경 경계(`require_local_targets`·
  `require_local_container`·host-local 폐포 `local_mutation_target`), 원격 HTTP 관측(`remote_status.py`), compose
  `map-api → geo-api` 제거. (#484는 같은 범위의 대안이라 닫음.)
- [x] **T-MGR-06j — 공용 Dagster plane 수렴 명령의 n150 적용**: `ktdctl dagster-plane converge`(#478) 머지·n150 적용.

## 2026-10-14 — 공용 Dagster 최신 tick 조회와 연결 대기 복구

- [x] timestamp DESC 인덱스를 storage bootstrap에 추가하고 동일 정의 invalid index 재진입 복구, 연결/lock/SQL 시간 제한, 이름 충돌·오류 fail-closed를 구현했다. [계약](dagster-tick-index.md).
- 관련 계약 318 PASS, 실제 PG·Dagster 이미지 4 PASS, 독립 FULL 적대 리뷰 2건 OPEN 0 및 Concierge n150 live UI 90 PASS를 확인했다. 검토 함수의 운영 인덱스 적용/실제 계획·최신 ID/tick body/요약 응답을 검증했다. 공유 PG/plane 재생성·새 이미지 정식 rollout·장기 안정성을 완료로 계산하지 않는다.

## 2026-10-06 — Concierge 공용 Dagster 합류 구현

- [x] Concierge shared-only code server, workspace/digest·readonly instance bind·location/job cap과 canonical health/reaper를 구현했다(ADR-55). legacy profile과 기존 own/partial topology 검증을 유지한다.
- Linux backend 2632 PASS/7 SKIP, frontend 34 PASS 및 type/lint/build, Ruff 통과. Concierge 격리 native/Docker/N150 UI 검증을 완료했다. 독립 FULL 리뷰·PR 검사는 병합 gate이며 운영 설치·drain/backend 전환은 별도 작업이다.

## 2026-09-04 — 격리 하네스의 불변 핀 소스 트리 오염

- [x] 실행을 봉인 트리가 아니라 **일회용 체크아웃**(같은 bare의 object store에서 재유도)에서
  하도록 바꿨다(#315). 적대 리뷰 2인의 지적 5건을 반영하고(#315), porcelain 경로 파싱을
  NUL 구분으로 고쳤다(#316). 운영 선행조치로 활성 pinset의 봉인 PinVi 트리에서 잔여물
  4건을 제거했다.
- [x] `e2e025`가 통과하고 **끝난 뒤에도 봉인 트리가 ACCEPT**임을 실측했다 — 같은 pinset
  재실행이 가능하다. 상세는 `docs/journal.md` 2026-09-04.

## 2026-08-26 — C7 v4 capture 운영 항목 퇴역

- [x] **T-C7-CAPTURE-OPS — H300 v6/v8 정본과 충돌하는 v4 capture 퇴역**

  `pinvi-pair capture`는 과거 five-service `compatible-pair-v4.json`을 원자 교체하는
  읽기 전용 관측기였지만, application `300`의 current authority는 seven-service v6
  `pinned-runtime-generation`과 v8 rebuild journal이다. v4 artifact는 F1D legacy
  tombstone 대상이므로, 이를 다시 생성·attestation하는 운영 절차는 current generation을
  증명하지 못하고 서로 다른 정본을 되살린다.

  따라서 CLI·전용 service·v4 runner contract test를 함께 제거했고, active task에는
  v4 checkout env·manifest 프로비저닝이나 capture 실행을 남기지 않는다. H300 generation
  검증은 `pinvi-pair rebuild-pinned --confirm`이 남긴 v6/v8 manifest·journal과 그
  seven-service/three-DB attestation만 사용한다. 이 변경은 n150 container·DB·manifest를
  변경하지 않았고, 이전 revision restore나 application data integrity 검증도 수행하지 않았다.

## 2026-08-26 — Geo standalone 대용량 backup 1회 실측

- [x] **BACKUP-FOLLOWUP-GEO-INITIAL — Geo application standalone backup 1회 실측**

  n150에서 `ktdctl db-backup create geo --timeout 14400`을 한 번 실행했다. Geo application
  DB의 `pg_dump -Fc --compress=6` 단계는 manifest `duration_sec=1,311.8`, 명령 전체
  wall-clock은 `24m 21.903s`였고 dump 크기는 `4,717,161,289` bytes였다. dump·`.sha256`·
  `.manifest` 세 파일의 권한은 `600`, `sha256sum -c`는 `OK`, 임시 `.copying` 파일은 남지
  않았다. 이력은 Geo backup의 off-box 자동화·보존 정책과 독립하며, restore나 application
  row·건수·업무상 무결성 대조는 수행하지 않았다.

## 2026-08-26 — application `300` 최종 n150 수락

- [x] **T-VN-41-F1D-H300 — destructive rebuild·runtime provenance·data-independent live UI 완료**

  Map `cc81081ff2e540a6ad9c428a296515e1d79bc316`과 Manager PR #207 merge
  `ecfbddb7b3d1afbd74646abbaa4082dd70b53a42`를 고정한 trusted install에서 승인된
  `ktdctl pinvi-pair rebuild-pinned --confirm`을 재개했다. durable journal은
  `version=8`, generation `32`, transaction `5121a6d2-692d-4bd9-a5b0-d572d58c0f8f`,
  `phase=committed`로 끝났고, 고정 pinset digest는
  `14a9a512836a48489146dc2bb0a04de309cf451b274b934d79805d171f83a193`다.

  재생성된 Map·PinVi runtime과 독립 DB의 identity/provenance/readiness를 확인하고, committed
  journal의 image/head/pinset/application candidate evidence 결박을 확인했다. n150 실제 브라우저에서
  scenario catalog, backup-only 정책, 운영 홈, 운영 로그의 data-independent live UI **11개 테스트를
  모두 통과**했다(`auth.setup.ts` 1, `admin-scenario-catalog.live.spec.ts` 4,
  `backups-restore.live.spec.ts` 2, `home-dashboard-roundtrip.live.spec.ts` 2,
  `logs.live.spec.ts` 2; n150 report: `/tmp/kor-travel-map-playwright/admin-frontend-live/report`).
  일반 row의 내용·건수·업무상 데이터 무결성은 release gate로
  검사하지 않았고, 이전 revision/기존 DB restore도 하지 않았다. 필요하면 fresh `300` schema에
  source/ETL을 처음부터 재적재한다.

  현재 Manager `.env` smoke credential의 login은 `200 + Set-Cookie`였고, 배포 런북의 이전
  credential 후보는 runtime hash와 달라 `401`이었다. 실제 비밀번호는 기록·변경하지 않았다. Features의
  초기 목록·검색·필터·정렬·반응형·딥링크는 통과했으며, 두 번째 페이지·고정 ID/컬렉션을 전제하는
  데이터 의존 시나리오는 새 스키마 정책에 따라 수락 대상에서 제외했다.

  이 H300 기록은 login setup/protected view와 schema/runtime provenance 범위다. logout→재차단,
  PinVi WebSocket/mutating loop와 별도 consumer reconciliation은 Manager의
  `MAP-LIVE-FOLLOWUP` 및 [Map 저장소 `docs/tasks.md`](https://github.com/digitie/kor-travel-map/blob/main/docs/tasks.md)의
  `T-VN-41C`·`T-VN-41F1D-D2` 후속 운영 acceptance로 남긴다.

## 2026-08-26 — application `300` recovery/runtime contract 정렬

- [x] **T-VN-41-F1D-H300 — Manager PR #203~#206 merge 및 n150 rebuild 중간 결선**

  Manager PR #203(역할 bootstrap password 전달), #204(ACL grantability와 extension owner
  CREATE attestation), #205(Map schema owner ACL 기대값), #206(frozen entrypoint recovery/probe
  argv)을 각각 merge하고 trusted install에 반영했다. 승인된 n150 rebuild는 fresh root/finalize,
  metadata, Dagster, PinVi schema 단계까지 진행했으며, 최종 runtime attestation에서 Map image의
  `Entrypoint=["/app/docker/api-entrypoint.sh"]`, `Cmd=null`과 Manager의 이전 기대값이 어긋난
  source-contract 문제를 확인했다. 행/콘텐츠/건수 검증이나 이전 DB 복구는 수행하지 않았다.

- [x] **T-VN-41-F1D-H300 — Map API immutable image 실행 경계 확인 및 PR #207 준비**

  현재 Map Dockerfile의 immutable `ENTRYPOINT`/빈 `CMD`를 정본으로 확인하고, Manager runtime
  secret isolation과 운영 문서를 그 계약에 맞추는 PR #207을 준비했다. Compose-level override 금지,
  provider credential 격리, frozen transaction 검증은 유지하며 회귀 테스트로 올바른 entrypoint 수락과
  우회값 거부를 고정했다. PR #207 merge 후 동일 사용자 승인 rebuild를 재개하고 live UI acceptance를
  수행하는 조건은 active H300에 남긴다.

## 2026-08-25 — Map/Manager `300` release 후보 merge·전문 리뷰 완료

## 2026-08-26 — Map Dagster static inspection launch contract 보완

- [x] **T-VN-41-F1D — Manager PR #202 merge 및 trusted install 갱신**

  Map Dagster static head를 production entrypoint가 아닌 고정 absolute entrypoint와 `--network none`으로
  검사하도록 Manager를 보완했다. pre-journal stale receipt 재사용과 journal resume의 Map candidate exact
  binding도 함께 닫았고, 두 전문 적대 리뷰에서 P0/P1=0 GO를 확인했다. PR #202 merge commit은
  `e582e9244e1af8abd23053866c2f8388cbda79fc`이며 n150 trusted install에 반영됐다. 새 paired candidate와
  durable journal을 만든 뒤 role-bootstrap 단계에서 PR #203 후속 계약 누락이 발견되어, 최종 live acceptance는
  active H300에 남긴다.

- [x] **T-VN-41-F1D-H300 — Map PR #1064·Manager PR #197의 release pin 단계 완료**

  Map `dd2ee61fdb1d0cedb0d7cb3526c804a3dfc5404e`의 통합 CI와 두 전문 적대 리뷰를 통과한 뒤
  merge했고, Manager PR #197도 `7017a5d2d7192018ddf0667623c4cc18b290c46b`로 merge했다.
  Manager v5 canonical pinset은
  `49548a610cbfa3a0d2242ef6e9a8cbd5664e61dec92391b8a476b02951b65c62`로 고정했다. 이 단계의
  Map/Manager source·pinset 연속성 및 review P0/P1=0 결과는 후속 n150 rebuild의 입력으로만
  사용하며, 아직 live acceptance와 최종 rebuild는 active H300에 남아 있다.

- [x] **T-VN-41-F1D — Map role-bootstrap helper bind C6c 오탐 보완(PR #198)**

  canonical Map bootstrap과 credential preflight helper의 환경변수 식별자만 허용하고 실제
  protected value는 계속 거부하도록 Manager 후보 검증을 보완했다. 두 전문 적대 리뷰에서
  P0/P1/P2=0 GO를 확인했고 Manager PR #198을 `19409e3fad4bbe37a89edec99fee6f67de51fcff`로
  merge했다. 전체 backend `726 passed, 3 skipped`와 Compose contract `29 passed`를 통과했다.
  n150 trusted rebuild와 live acceptance는 active H300의 남은 조건이다.

## 2026-08-25 — application `300` 데이터 무결성 gate 퇴역

- [x] **T-VN-41-F1D-D — fresh `300` 데이터 재적재 후 수용 검증을 release 조건에서 제외**

  application `300` 전환은 schema/bootstrap과 runtime provenance만 증명한다. 일반 application row의
  내용·건수·업무상 무결성은 release gate로 검증하지 않으며, 필요한 경우 fresh schema에 source/ETL을
  처음부터 재적재할 수 있다. 이전 revision이나 기존 DB로의 복구 계획은 없으므로, 데이터 의존 UI E2E는
  별도 데이터 적재 작업의 운영 검증으로만 남기고 H300 완료 조건에서는 제거했다.

## 2026-08-25 — active backlog 정합성 정리와 T-VN-40 Manager 잔여 이관

- [x] **완료·퇴역 항목을 `tasks.md`에서 제거하고 이력으로 이관**

  active backlog에 완료 상태로 남아 있던 `DOC-AUDIT-20260821`, T-050, T-051, #173/H46F,
  #178, #179를 완료 이력으로 옮겼다. T-031, T-045~T-049, T-052~T-057,
  T-VN-41-F1/F1A/F1B/F1C/F1E/F1F는 ADR-34의 fresh rebuild와 ADR-39의 application `300`
  모델로 대체된 퇴역 control plane이므로, 과거 미체크 문장을 현재 작업으로 해석하지 않게 active
  backlog에서 제거했다. 상세 당시 기록은 이 문서의 기존 항목과 Git 이력에 보존된다.

- [x] **T-VN-40 Manager 잔여 항목 종결**

  PR #174에서 raw snapshot/mapping token은 PinVi exact service path와 Map API 전용 digest path로
  분리됐고, raw/resolved Compose 및 runtime secret isolation 회귀가 merge됐다. PR #172/#175/#176의
  Map 전용 PostgreSQL·principal bootstrap과 PR #183의 Map UI Geo credential 경계도 merge됐다.
  따라서 T-VN-40의 Manager 코드 잔여를 #171 또는 H46F의 미완료 항목처럼 `tasks.md`에 남기지 않는다.
  application `300`의 n150 live acceptance는 새 T-VN-41-F1D-H300이 소유한다.

- [x] **#171·#177 구현 issue와 C7 capture 구현 완료 사실 정리**

  GitHub #171과 #177은 closed 상태다. #171의 전용 DB/DSN/principal 및 Hallmark 운영 UI 구현은 완료됐고
  새 application `300` live acceptance만 H300으로 이관했다. #177의 standalone backup create/list/gc,
  cron, API/UI와 n150 Geo Dagster·Concierge·PinVi 실증은 완료됐으며, Geo application 대용량 실측과
  off-box 자동화만 `BACKUP-FOLLOWUP`으로 재식별했다. 읽기 전용 `pinvi-pair capture` 구현도 PR #184에서
  merge됐고, 설치본·checkout·manifest 프로비저닝만 `T-C7-CAPTURE-OPS`로 분리했다.

## 2026-08-06 — T-VN-41-F1D-C3 n150 결선

- [x] **F1D-C3 — dynamic fixture durable transaction과 n150 파기형 rebuild**

  Manager PR #167의 latest Map typed-subtype pin으로 n150에서 `rebuild-pinned --confirm`을 실행해 새
  generation을 committed했다. Map application `0087_route_area_subtypes`, Map Dagster `29b539ebc72a`,
  PinVi `20260804_0049` head와 일곱 runtime container health를 확인했다. v7 journal은 Map fixture의
  `armed → consumed → finalized`, PinVi의 exact `409 PIPELINE_CANCELLATION_UNSAFE` outcome, final
  `committed` phase를 보존했다. 따라서 response-loss 재개를 위해 같은 cancel/finalize POST를 추측
  재발행하지 않는 C3 transaction 경계도 실제 runtime에서 결선됐다.

  Map UI 로그인 POST는 `200`과 session cookie를 반환했다. data-independent live UI E2E는 운영 홈·파이프라인
  catalog 6건, Feature 목록·지도 초기 surface 10건을 통과했다. 기존 full suite의 curated detail 및 지도 table
  landmark 일부는 의도적으로 빈 새 DB에서 사전 적재된 고정 ID를 요구해 실패했으므로, 이는 C3 runtime
  failure로 처리하지 않고 final-schema ETL 재적재 뒤 F1D-D acceptance에서 다시 실행한다.

## 2026-08-06 — T-VN-41-F1D-B~C2 파기형 pinned runtime generation 구현

- [x] **F1D-B~C2 — v5 generation·candidate-first rebuild·upstream schema bootstrap 결선**

  `CompatibleImagePair` v4와 공개 pair/cache/backup mutation 경로를 퇴역하고, Map 네 service와
  PinVi 세 service를 하나의 `PinnedRuntimeGeneration` v5 manifest·journal·tombstone authority로
  수렴했다. `rehearsal/rebuildable` root 실행만 `pinvi-pair rebuild-pinned --confirm`을 허용하며,
  candidate 일곱 image를 attest한 뒤 세 scoped DB를 fresh recreate하고 Map application/Dagster 및
  PinVi schema head를 각각 증명한다.

  Map C0와 PinVi C1의 one-shot schema bootstrap을 frozen Compose profile까지 결박했고, raw/resolved
  C6c capability boundary와 candidate PinVi production Settings import를 DB reset·journal write 전에
  fail-close로 검사한다. bootstrap에는 필요한 Map base URL/read/cancel만 허용하며 fixture·관리자
  credential은 실행 시에만 별도로 주입한다. 기존 F1J fixture lifecycle을 새 v5 transaction에 연결하는
  작업과 최종 n150 live UI/PinVi mutating E2E 기록은 각각 열린 F1D-C3·F1D-D에서 처리한다.

## 2026-08-06 — T-VN-41-F1D-A 파기형 runtime generation 재bootstrap 설계

- [x] **T-VN-41-F1D-A — v5 rebuild 설계·ADR·task 정렬**

  비운영 n150에서는 중간 DB·backup·old runtime을 복구하지 않는다는 결정에 따라, Map 네 service와
  PinVi 세 service를 하나로 묶는 `PinnedRuntimeGeneration` v5, 세 scoped DB의 fresh recreate,
  candidate-first attestation, single-active manifest를 정본으로 기록했다. `local/development`,
  `rehearsal/rebuildable`, `production/operational` typed environment/lifecycle pair와 rebuildable의
  exclusive mutation policy를 결정했고, PinVi는 credential-file one-shot migration→admin bootstrap으로
  전환한다. old F1D/F1F state는 safe tombstone 뒤 v5 authority로 교체하며 ADR-31과 ADR-33의 해당
  legacy contract를 supersede 처리했다.

  단일 적대적 설계 리뷰는 candidate/image-before-reset, PinVi migration 순서, typed lifecycle,
  Map Dagster expected head, legacy file lstat/`O_NOFOLLOW` 경계를 보강한 뒤 GO를 받았다.

## 2026-09-03 — `tasks.md`에서 이관한 완료 항목

`docs/tasks.md`의 머리말은 **완료되지 않은 작업만** 한 줄씩 두라고 규정하는데,
완료 표시(`[x]`)된 항목 다섯이 수천 자 본문으로 남아 활성 목록을 가리고 있었다.
원문 그대로 옮긴다 — 내용은 한 글자도 줄이지 않았다.

- [x] ~~docker-targets.yml 스키마 검증 잔여 (GM-11 후속)~~ — 세 갭을 모두 닫았다. (1) `registry.py`의 `MANAGED_CONTAINERS`/`MANAGED_TARGETS`/`TARGET_ALIASES`를 즉시 계산 plain dict 대신 `collections.abc.Mapping`을 구현하는 `_LazyMapping`으로 바꿔, `loader`가 최초 실제 접근(구독/순회/`in`/`.items()`)에서만 실행되게 했다 — 기존 20여 곳의 dict-스타일 사용부는 전혀 손대지 않았다. `cli.py`의 `DIRECT_ENSURE_ALIASES`도 모듈 top-level 상수에서 `_direct_ensure_aliases()` 함수로 옮겨 `main()`의 dispatch 직전에만 호출되게 했고, `main()`의 `build_parser()`~`args.func(args)` 구간 전체를 `except TARGETS_CONFIG_ERRORS`로 감싸 registry.py의 이미 명확한 검증 메시지를 raw traceback 대신 stderr 한 줄 + exit 1로 낸다. 여기서 끝나지 않고 실측 재현(임시 깨진 config로 fresh subprocess 실행)에서 별도 원인 하나를 더 찾았다: `metrics_collector.py` 맨 끝의 모듈 레벨 싱글턴 `metrics_collector = MetricsCollector()`가 `__init__`에서 `MANAGED_CONTAINERS`를 즉시 순회해, `cli.py`가 `docker_service`를 top-level import하는 순간 같은 문제가 다시 재현됐다 — `DockerService.__init__`의 `_backup_default_config`와 같은 이유로 그 dict comprehension을 `try/except ValueError`로 감싸 fail-open(빈 dict로 시작, `logger.error`로 로그)하게 고쳤다. 이 대가로 backend(FastAPI) 프로세스도 이제 깨진 config에서 import/기동 자체는 막지 않는다(GM-11이 의도적으로 만들었던 "backend 기동도 막는" 성질을 이번 후속이 명시적으로 되돌린 것) — 대신 매 metrics 수집 tick과 실제 target/container 조회가 필요한 요청에서 같은 오류가 계속 관측 가능하게 다시 난다. (2) `targets` 서브커맨드를 `pin`과 같은 nested subparser 패턴으로 바꿔 `targets list`(기존 동작 그대로)와 `targets validate`(성공 시 `OK`+exit 0, 실패 시 메시지+exit 1)를 추가했다. `docs/docker-management.md` 3절의 GM-11 안내를 갱신해 새 서브커맨드를 정본으로, 예전 python 한 줄은 `ktdctl` 미설치 환경용 대안으로 남겼고 stale 라인 인용(`registry.py:135`→`187-191`)도 고쳤다. (3) `DashboardClient.tsx`의 두 댓글(narrowest-target/`기타` 버킷)을 `containers`가 손으로 채운 고정 목록이라는 정확한 설명으로 고쳤다(`rustfs`가 `geo`/`conc`/`map`/`pinvi`/`all`에 독립적으로 반복 등재된 것을 근거로 제시) — 로직 자체는 이미 올바르게 동작해 변경하지 않았다. 회귀 테스트 9건 추가(`test_docker_manager_cli.py` 6건, `test_registry_targets_config.py` 2건, `test_prometheus_metrics.py` 1건)와 각각 mutation-test로 비어있지 않음을 확인했다. 전체 backend 1506 passed, 2 skipped, ruff 통과. frontend type-check/lint/test/build 통과.
- [x] ~~frontend 컴포넌트 자동 회귀 테스트 부재 (GM-12 적대적 리뷰 발견)~~ — 컴포넌트 렌더 테스트 인프라를 처음 도입하고 지목된 3개 컴포넌트의 오류 표시 분기에 회귀 테스트를 추가했다. **인프라**: `jsdom@27.4.0`, `@testing-library/react@16`(React 18/19 peer), `@testing-library/jest-dom@6.9.1`, `@testing-library/user-event@14`, `@testing-library/dom@10`을 devDependencies로 추가(모두 npm이 실제로 해석한 peer 호환 버전 — 추측 아님). `vitest.config.mts`의 `environment`를 `node`→`jsdom`으로, `include`에 `*.test.tsx`를 추가하고 `setupFiles: ['./src/vitest-setup.ts']`(내용은 `import '@testing-library/jest-dom/vitest'` 한 줄, jest-dom 자체 README의 "With Vitest" 절이 명시하는 경로)를 달았다 — "지금 넣으면 의존성만 늘어난다"던 옛 주석은 지웠다. 기존 3개 순수 로직 테스트 파일은 jsdom 아래서도 그대로 통과함을 확인했다(23 passed, 이번에 추가된 `errors.test.ts` 포함 4개 파일). `@tanstack/react-query`용 `frontend/src/test-utils.tsx`에 `renderWithQueryClient()`를 추가했다(테스트마다 새 `QueryClient`를 만들고 `defaultOptions.queries.retry: false`를 준다 — 캐시 오염 방지와, `ContainerDetailModal`처럼 컴포넌트가 `retry`를 직접 지정하지 않는 쿼리의 오류 테스트가 react-query 기본 재시도 지수 백오프로 느려지는 것을 함께 막는다). 예상 밖의 블로커 하나: 이 프로젝트의 Vite(8.2.2, `vitest`가 무설치 상태로 끌어옴)는 SSR 모듈 변환에서 `.tsx`의 JSX를 파일 확장자만으로 인식하지 않아 `@vitejs/plugin-react` 없이는 "Unexpected JSX expression" 파싱 에러로 테스트 파일 자체가 죽었다 — Next.js는 SWC를 쓰므로 이 프로젝트에 원래 없던 의존성이었고, `@vitejs/plugin-react@6.1.1`(peer `vite: ^8.0.0` 충족)을 추가하고 `vitest.config.mts`의 `plugins: [react()]`로 해결했다. **컴포넌트 테스트**(총 8건, 새 파일 4개): `SourceStatusPanel.test.tsx`(2건) — 4개 독립 `useQuery` 중 `deployment-readiness`만 실패시키고 나머지 3개(소스 상태·runtime-pins·rebuild-preflight)는 성공시켜, 실패한 섹션에만 `InlineError`가 뜨고 다른 두 섹션(요약 카드·재구축 실행 카드)은 그대로 렌더됨을 `role="alert"` 개수(정확히 1)까지 확인했다. `ContainerDetailModal.test.tsx`(2건) + `ContainerDetailModal.prod.test.tsx`(1건) — useQuery 오류 경로(`InlineError`)와, react-query 밖의 imperative `ensure` 액션(`postJson` 거부 → 로컬 `ensureState`/`ensureMessage`) 오류 경로를 각각 검증했다. 후자에서 `postJson`은 `api.ts` 내부에서 자기 모듈 스코프의 `apiJson`을 직접 참조하므로(export 객체의 `apiJson`만 갈아 끼워서는 안 잡힘) `apiJson`/`postJson`을 독립적으로 mock해야 했다 — `AdminSettingsPanel` 쪽 `apiJson`/`postJson`/`deleteJson` 셋도 동일한 이유로 모두 개별 mock했다. `IS_DEV = process.env.NODE_ENV !== 'production'`은 모듈이 처음 평가될 때 한 번만 고정되는 상수라 이미 로드된 인스턴스에 나중에 `vi.stubEnv`를 걸어도 안 바뀐다는 점 때문에, production 은닉 테스트만 별도 파일로 분리해 `vi.stubEnv('NODE_ENV','production')` 뒤 그 파일 안에서 **처음** 동적 `import()`하는 방식으로 검증했다(Vitest `test.isolate` 기본값이 파일마다 격리된 모듈 레지스트리를 주는 것에 의존) — 같은 파일에 두 번째로 `vi.resetModules()`까지 곁들이는 방법은 `@tanstack/react-query` 모듈 인스턴스가 갈라져 `useQuery`가 "No QueryClient set"으로 죽는 것을 실측으로 확인하고 버렸다. `AdminSettingsPanel.test.tsx`(3건) — 로그인 기록 섹션의 `InlineError` 분기, 비밀번호 변경 섹션의 오류(다른 두 섹션과 달리 `HumanError` 객체가 아니라 `humanizeError(...).title + ' ' + hint`를 이미 합쳐 놓은 평문 문자열이라 `role="alert"`가 안 붙는다는 모양 차이까지 확인), 그리고 감사 노트가 지목했던 `loadPublicKeys` 버그의 **현재(수정된) 동작** 회귀 테스트 — 최초 조회 실패로 에러가 뜬 상태에서 새로고침이 성공하면 그 에러가 사라지고 새 목록이 뜨는지 확인했다(옛 버그를 재현하는 방향으로 되돌려 mutation-test하면 정확히 이 단언에서 실패함을 확인). **mutation-test 전수 검증**: 새로 작성한 8건 전부에 대해 해당 컴포넌트의 조건부 렌더(또는 `loadPublicKeys`의 상태 초기화 줄)를 `MUTATION-TEST-TEMP` 표시와 함께 일시적으로 깨고 그 테스트(만)가 실패하는 것을 확인한 뒤 정확히 원상복구했다(`git diff --stat`으로 컴포넌트 3개 파일의 최종 diff가 0임을 확인). 전체 frontend `type-check`/`lint`/`test`(8 files, 31 passed)/`build` 모두 통과.
- [x] ~~WebSocket 경로에 요청 상관관계 ID 미적용 (GM-16 적대적 리뷰 발견)~~ — `websocket.py`의 `ws_status`/`ws_logs` 두 handler 모두 **함수 첫 줄**에서 main.py 미들웨어와 동일한 패턴(`request_id = str(uuid.uuid4()); token = request_id_var.set(request_id)`)으로 직접 발급하도록 고쳤다. shed 검사(1013)·인가 거부(4401)·container 미상(4000, ws_logs만)이 모두 기존 inner try/finally보다 앞에서 return하므로, 새 outer try/finally가 함수 전체(그 조기 return들 + 기존 inner try/finally)를 감싸고 `request_id_var.reset(token)`은 기존 inner finally(status_manager.disconnect/log_stream.close, 마지막 `_close_best_effort`)가 전부 끝난 뒤 이 outer finally에서 마지막으로 실행된다 — inner finally 안에서만 reset했다면 조기 return하는 거절/shed 연결이 inner try에 도달하지 못해 contextvar token이 샜을 것이다. 범위 밖으로 명시: `ConnectionManager.broadcast()`/`status_broadcast_loop()`는 main.py lifespan에서 한 번 시작되는 독립 task로 어느 연결의 컨텍스트에도 속하지 않으므로(한 broadcast tick이 N개 연결에 fan-out) 그 안의 로그 줄은 구조적으로 per-connection request_id를 실을 수 없다 — 두 자리 모두에 그 이유를 명시하는 주석을 남기고 고치려 하지 않았다. `test_ws_contract.py`에 거절된(4401) 연결도 실제 request id를 받는지, 연속 두 연결이 서로 다른 id를 받는지(격리) 검증하는 회귀 테스트 3건을 추가했다(mutation-test로 `request_id_var.set()`을 accept() 이후로 옮기면 실패함을 확인).
- [x] ~~m05_isolated_harness.py의 세 번째 pinned role 리터럴 중복 (GM-18 적대적 리뷰 발견)~~ — **하지 않기로 결정(won't-fix)**. PR #300에서 한 차례 `M05IsolatedRuntimeRole = RuntimeSourceRole`로 배선했다가 적대 리뷰 2인이 독립적으로 되돌리라고 판정해 철회했다. 근거: (1) 오늘 완전한 no-op이다 — 정본도 `("map", "pinvi")` 두 원소다. (2) 정본이 role을 늘리면 별칭만 따라가고 `len(networks) != 2`, `expected_names = {"map": …, "pinvi": …}`, `_EXPOSED_RUNTIME_SERVICE_ROLES`, `_RUNTIME_IMAGE_ROLES`, provenance payload 키, `role == "map" else pinvi` 이항 분기가 그대로 남아 harness가 통째로 unconstructible이 된다 — 그 실패는 두 스택을 다 띄운 뒤(1~2시간)에 나온다. (3) CI에 mypy가 없어 타입 별칭 정본화는 강제되지도 않는다. (4) 실측: 정본을 3-role로 넓히면 `runtime_pin_registry`가 "map then pinvi exactly once"로 **모듈 수집 단계에서** 이미 fail-close한다 — 배선이 방어할 시나리오 자체가 없다. 즉 능력 없는 결합이고 이 저장소가 결함으로 규정한 과결박이다. ADR-40이 pair를 정확히 두 role로 동결했으므로 리터럴이 정직하다. 정본이 늘어나는 순간을 잡도록 `test_m05_isolated_harness_is_deliberately_two_role`이 2-role임을 단언하며, 그때 필요한 것은 별칭 배선이 아니라 networks/services/images/provenance/이항 분기의 **전면 일반화**임을 안내한다.
- [x] ~~`_resolve_repository_path`(compose_service.py) 실질 테스트 커버리지 0건 (GM-19 적대적 리뷰 발견)~~ — PR #300에서 이행했다. `backend/tests/test_compose_service_resolve_repository_path.py`에 실제 파일시스템 기반 테스트 4건을 추가했다(구현 변경 없음). 적대 리뷰가 그중 절대경로 케이스는 `pathlib`의 `/` 의미상 가드와 무관해 공허함을 지적했으므로, 그 가드(`is_absolute()` 분기) 자체가 삭제 후보라는 점을 여기 남긴다 — 나머지 3건은 비공허함을 mutation으로 확인했다.

## 2026-08-06 — T-VN-41-F1J Map 소유 cancel-probe fixture lifecycle

- [x] **T-VN-41-F1J — dynamic fixture·정확한 PinVi cancel relay·isolated final verification**

  Map PR #960(F1J-A)은 transaction-scoped cancel-probe fixture를 Map DB에 `armed → consumed → finalized`
  상태로 소유하고, 전용 `ops:fixture` principal과 exact `409 PIPELINE_CANCELLATION_UNSAFE`를 추가했다.
  Manager PR #159(F1J-B)는 static UUID와 넓은 성공 집합을 제거하고, fixture ensure·durable receipt·response-loss
  resume·finalize를 Map canonical state에만 결박했다. PinVi PR #435와 Manager PR #160(F1J-C)은 Map service
  provenance bytes, migration head, cache-target/C6c capability를 strict preflight pinset으로 수렴했다.

  final F1J-D는 Map #960, PinVi #439, Manager #163 정확한 source에서 새 Compose project·DB·volume·network만
  생성해 실행했다. `ops:read` 사전 점검은 `200`, Manager smoke의 로그인·ETL·provider-sync는 모두 `200`, cancel은
  exact `409`이고 durable rerun 결과도 일치했다. 관리자 live UI E2E는 5/5, 새 PinVi DB의 mutating trip
  WebSocket/reconnect E2E는 1/1 통과했다. 기존 runtime/data, backup/restore는 전혀 사용하지 않았고 모든
  일회성 container·volume·network·image tag·scratch를 폐기했다. 이 완료는 issue #136의 F1J 보강 범위만 닫으며,
  상위 F1D bootstrap task의 별도 잔여 조건은 열린 backlog에 유지한다.

## 2026-08-05 — T-VN-41-F1I F1D fail-close checkpoint 관측성 (issue #136 보강)

- [x] **T-VN-41-F1I — F1D fail-close checkpoint 관측성**

  F1D journal과 CLI는 raw exception·로그·credential을 보존하거나 출력하지 않고, allowlist checkpoint,
  마지막 실패 checkpoint/UTC 시각, strict integer failure count, halt 상태만 durable하게 남긴다. extended
  journal reader는 네 diagnostic field가 모두 갖춰진 정확한 shape만 수용하고 기존 production base-v2는
  `null/null/0`으로 정규화한다. candidate action 직전 checkpoint fsync, failure evidence persistence,
  persistence 실패 뒤에도 유지되는 `finally` halt, original evidence를 덮지 않는 halt failure를 회귀로
  고정했다.

  PR #156(문서)과 PR #157(구현)을 병합하고 trusted Manager release를 n150에 설치했다. 동일 frozen
  candidate의 다음 F1D 실행에서 `prepared.contract.pinvi_smoke`와 failure count `1`을 안전하게 확인했다.
  마지막 시도만 분리한 결과 login·ETL·provider-sync는 `200`, configured cancel probe만 `404`였으므로,
  후속 F1J가 Map-owned fixture lifecycle을 구현한다.

## 2026-08-04 — T-049F durable Map writer-drain (issue #115)

- [x] **T-049F — durable Map writer-drain**

  Manager는 frozen Compose의 Map API one-shot private command만 `begin → attest → restore`
  체인으로 실행한다. journal은 `writers_draining`, `writers_drained`, `writers_stopping`과
  rollback `writers_restored` phase에 lease/receipt SHA-256을 fsync한다. pre-backup·superseded
  diagnostic recovery는 restore 뒤 exact previous pair re-attestation 전 writer/archive를
  허용하지 않으며, backup rollback은 Map Dagster webserver-only restore receipt 뒤 daemon을
  포함한 old runtime을 연다. `writers_restored` crash는 top-level rollback 재개로 old runtime
  activation까지 수렴하고, superseded recovery는 manifest identity를 Map restore보다 먼저
  대조한다. version `1` state는 자동 migration 없이 mutation 전에 거부하며 recreate한다.
  public GraphQL/token/일반 Compose bypass는 없다. strict Manager regression 148건과 actual
  ephemeral Docker Compose rehearsal 1건을 통과했고 n150/prod는
  사용하지 않았다.

  **병합 시 추가 확인·수정**(부모 세션, 2026-08-04): 이미 병합된 T-052(direct
  daemon stop 초안, PR #117)를 대체하므로, T-052가 남긴 `test_cache_target_cutover_gate.py`의
  v1 phase 픽스처(`writers_drained`·lease/receipt evidence 누락)를 v2 계약에 맞게
  고쳤다. 적대적 리뷰어 2명(레이스/crash-recovery/rollback 담당, secret 비노출/
  schema 검증 담당)을 추가로 돌렸다. 리뷰어가 **실제 결함**을 찾았다:
  `_validate_phase_evidence`(diagnostics·window 양쪽)가 phase 문턱으로만 검사해서,
  `writer_drain_restore_receipt_sha256`만 있고 그보다 먼저 있어야 할
  `writer_drain_lease_id`/`writer_drain_receipt_sha256`은 없는 논리적으로 불가능한
  journal이 어느 phase에서든 통과할 수 있었다 — phase와 무관한 무조건 검사를
  추가해 고쳤다(회귀 테스트 2건 추가). 리뷰어 1은 crash-recovery 전체가 "Map의
  `begin`이 owner_id 기준으로 idempotent하다"는, 이 저장소만으로는 검증 불가능한
  가정에 의존한다는 medium 등급 우려를 남겼다 — Map 쪽 구현 소유자 확인 필요
  (fix되지 않고 열린 채로 남김, kor-travel-map 쪽에 별도 확인 요청 필요).
  backend 전체 1580 passed, ruff/mypy clean(touched files).

---

## 완료 현황 요약

| 태스크 ID | 작업 항목 | 완료 날짜 | 비고 |
|:---|:---|:---:|:---|
| **T-001** | 에이전트 및 워크스페이스 문서 초기화 | 2026-06-10 | 가이드 및 설정 완료 |
| **T-002** | 프로젝트 인프라 설정 (`.gitignore`, `docker-compose.yml`, `README.md`) | 2026-06-11 | `kor-travel-geo` 인프라 이관 반영 완료 |
| **T-003** | FastAPI 백엔드 뼈대 구성 (`backend/pyproject.toml`, main app) | 2026-06-11 | 뼈대 구성 및 websockets 추가 완료 |
| **T-004** | Docker 제어 모듈 (`DockerService` 및 API 엔드포인트) 구현 | 2026-06-11 | 실시간 메트릭 및 로그 엔드포인트 포함 구현 완료 |
| **T-005** | Next.js 프론트엔드 구성 (`frontend/package.json` 및 라우팅) | 2026-06-11 | 패키지 구성 및 recharts 설치 완료 |
| **T-006** | 대시보드 UI 및 TanStack Query 연동 개발 | 2026-06-11 | WebSocket 실시간 테이블 및 차트/로그 모달 완료 |
| **T-007** | 품질 검증 및 최종 통합 테스트 | 2026-06-11 | 백엔드 테스트 및 프론트엔드 빌드 검사 완료 |
| **T-008** | Docker 관리 문서 및 target registry 정리 | 2026-06-12 | 통합 DB 모델, CLI/API target 기준 정리 |
| **T-009** | Python CLI 및 target ensure/build 구현 | 2026-06-12 | `ktdctl` CLI 추가 |
| **T-010** | Docker inspect API 및 secret redaction 구현 | 2026-06-12 | `/api/v1/containers/{id}/inspect` 추가 |
| **T-013** | 설정 파일 기반 CLI 별칭 및 초기화/복구 step 구현 | 2026-06-12 | `db/storage/geo/map/ai/main` alias와 init step 추가 |
| **T-014** | Kor Travel/PinVi 계열 로컬 포트 정책 일원화 | 2026-06-12 | PostgreSQL `5432`, RustFS `12101/12105`, manager `12901/12905` 반영 |
| **T-015** | 실행 위치 정책 문서화 | 2026-06-12 | 당시 정책: git은 Windows, 일반 개발 명령은 WSL, Playwright E2E는 Windows로 고정. T-028로 대체 |
| **T-016** | `kor-travel-geo` Docker API/UI target 편입 | 2026-06-12 | `geo` target에 API/Web UI compose 서비스 추가 |
| **T-017** | 관측 스택 Docker target 추가 | 2026-06-13 | Grafana, cAdvisor, Prometheus 분리 컨테이너 추가 |
| **T-018** | 프로젝트명 및 CLI 명령 전환 | 2026-06-13 | `kor-travel-docker-manager`, `ktdctl` 기준으로 변경 |
| **T-222** | 관측 target 개별 분리 및 포트 재배치 | 2026-06-13 | `gra`, `cadv`, `prom` target과 `12205`, `12301`, `12401` 포트 반영 |
| **T-224** | 과거 서비스명과 공용 인프라 명칭 정리 | 2026-06-15 | PinVi 및 `kor-travel-*` 기준 반영 |
| **T-026** | PinVi API worker 기본값 환경변수화 | 2026-06-28 | `PINVI_API_WORKERS=1` 기본값으로 process-local WebSocket broadcast 제약 반영 |
| **T-027** | PinVi public API URL·CORS origin 환경변수화 | 2026-06-28 | prod public Web/API origin을 gitignore `.env`에서 주입하도록 변경 |
| **T-028** | Linux 전용 개발·버전관리·CodeGraph 실행 위치 정책 정리 | 2026-06-28 | `git`/CodeGraph는 Linux, Playwright E2E는 n150 우선·불가 시 Windows fallback |
| **T-032** | C6c Map·PinVi image source provenance fail-close | 2026-07-19 | PR #58 squash merge, clean HEAD→Git archive→OCI label→manifest v3 결박 |
| **T-037** | C6c Map UI 통합 경로 smoke 정렬 | 2026-07-27 | PR #67, `/ops/datasets` login/protected/logout lifecycle을 n150에서 확인 |
| **T-038** | Map destructive production 명시 승인 결선 | 2026-07-26 | PR #68, Map issue #796 closed, destructive live gate와 actor 감사 증거 완료 |
| **T-039** | C6c PinVi login SSR shell 판정 정렬 | 2026-07-27 | PR #69, route chunk HTTP smoke와 hydrated login form을 n150에서 확인 |
| **T-040** | C7 Map features routes production 명시 결선 | 2026-07-27 | PR #71, issue #70 closed, features route production live gate 완료 |
| **T-041** | C6c rollback image retention 보장 | 2026-07-27 | PR #73, issue #72 closed, active/rollback reference 가용성과 cleanup 성공 확인 |
| **T-021** | PR #36 후속 하드닝(신뢰 프록시 시크릿·brute-force durable·공개키 DB 직접조회·모달 a11y) | 2026-06-24 | AUTH-3/AUTH-6/APIKEY-1/FE-4, PR #38 머지·prod 검증 |
| **T-025** | 배포 런북 + push 전 보안 감사 절차 | 2026-06-24 | `deploy-runbook.local.md`(gitignore), AGENTS.md 절차·DO NOT #13/#14 |
| **T-042** | C7 WebSocket 종료 코드 계약(accept-then-close) 결선 | 2026-07-28 | PR #75, n150 프록시 경유 실브라우저에서 `4401`/`wasClean` 확인, settle 실측 |
| **T-033** | C7 Map UI·Dagster OCI revision 결선 | 2026-07-28 | issue #60(closed), n150 실행 중 Map 4종 image의 `org.opencontainers.image.revision`이 동일 40자 commit임을 실측 |
| **T-034** | C6c cAdvisor healthcheck 포트 계약 정렬 | 2026-07-28 | issue #62(closed), n150 cAdvisor healthy·`/healthz` 200 및 compatible-pair active 세대 정상 확인 |
| **T-035** | C7 Map production API 인증 env 결선 | 2026-07-28 | issue #63(closed), n150 Map API startup/readiness 및 service별 secret 격리 계약을 `docker exec env`로 실측 |
| **T-036** | C7 PinVi Dagster image 계약 정렬 | 2026-07-28 | PR #66, n150 `pinvi-dagster-latest` 9일째 healthy로 dependent bootstrap 완료 확인 |
| **T-012** | 대시보드 상세 패널 확장 | 2026-07-28 | PR #79, inspect 모달·5개 탭·dev ensure 버튼, 비밀 redaction 보강, 적대적 리뷰 2명 반영, 실브라우저 검증 완료 |
| **T-011** | 설정 저장 안정화 및 validation 고도화 | 2026-07-28 | PR #80/#81, diff 미리보기·baseline 인지 secret 방어 + 적대적 리뷰 2라운드(URL/비-URL 위조 변수명 우회, React key 포커스 유실) 수정 |
| **T-044** | ensure 라우트의 production 서버측 차단 | 2026-07-28 | PR #81, `ComposeService.ensure_target` production 전면 차단, 적대적 리뷰 2명 + 검증 통과 |
| **T-043** | WS 인가 동시성 상한 + 프론트 배포 preflight | 2026-07-28 | T-042 리뷰 후속(PR #76), n150 배포 후 1013 shed 실측(300 동시 접속 중 179건 shed) 및 preflight 확인 완료 |
| **T-013** | 운영(prod) 공개 주소 `.env` 주입 및 CORS 환경변수화 | 2026-06-20 | 도메인 비노출, `KTDM_CORS_ALLOW_ORIGINS`, 프론트 환경파일 분리 ⚠️ ID 재사용 |
| **T-014** | Docker host 네트워크 전환·컨테이너=호스트 포트·서비스 prod URL·pinvi-dagster·tripmate 정리 | 2026-06-20 | `KTDM_DOCKER_NETWORK_MODE=host`, 12802, `KTDM_PROD_URL_*`, `ktd_venv` ⚠️ ID 재사용 |
| **T-015** | 프론트 Tailwind v4 + StyleSeed 전면 전환·전역 오류 복구 boundary | 2026-06-20 | geo PR #391 반영, `@theme` 토큰, `DESIGN-RULES.md` ⚠️ ID 재사용 |
| **T-016** | 운영(prod) 배포 및 docker-manager 실행 검증 | 2026-06-20 | SSH 배포, venv --without-pip, 백엔드/프론트 기동·검증, 공개 라우팅은 인프라 ⚠️ ID 재사용 |
| **T-017** | 운영 스택 db→conc 기동·geo 실데이터 복원·의존성 DAG 재설정 | 2026-06-20 | 이미지 save/load, geo 31GB 복원, `depends_on` DAG(concierge geo 비의존) ⚠️ ID 재사용 |
| **T-018** | prod endpoint 문서 redaction | 2026-06-23 | `kor-travel-map` #508 동일 패턴 반영 ⚠️ ID 재사용 |
| **T-019** | 관리자 로그인·세션·감사 로그·공개 API 키 관리 | 2026-06-23 | `kor-travel-geo` PR #399 패턴 반영 |
| **T-020** | PR #36 사후 리뷰 + fix-forward(보안 테스트 보강·감사 retention·CORS·프론트 a11y·utcnow 정리) | 2026-06-24 | 리뷰 코멘트, PR #37 머지, prod 배포·인증 검증 완료 |
| **T-023** | concierge PR #127 참고 공개도메인 Secure 쿠키 보강(`_is_https`가 https 공개 origin 인식) | 2026-06-24 | 브라우저 E2E로 로그인 정상 확인(403 무), Secure 플래그 PR #40 머지·prod 검증 |
| **T-024** | 로그아웃/세션만료 시 LoginScreen 전환 회귀 수정(auth-me 401→authenticated:false) | 2026-06-24 | PR #37 FE-2 회귀, 브라우저 E2E로 발견·PR #41 머지 |
| **T-029** | Concierge DB read 키를 Map Dagster에 단일 source로 주입 | 2026-07-13 | n150 단일 source 전환·cursor/수집기·권한·로그인 smoke 및 구 static 제거 완료 |
| **T-030** | Map OpiNet·KREX provider 키 compose 보간 drift 수정 | 2026-07-13 | 현재 env 이름·수집 서비스 전용 주입·API 제거 계약 테스트 고정 |
| **T-220** | `kor-travel-concierge` provider 상세 구현 및 과거 명칭 제거 | 2026-06-13 | 공식 프로젝트명 전환 완료 |
| **T-221** | `kor-travel-geo` DB명·환경변수·Docker 이름·Prometheus scrape 계약 동기화 | 2026-06-13 | `kor_travel_geo`, `KOR_TRAVEL_GEO_*`, `KTG_*`, `kor-travel-geo-*` 기준 반영 |
| **T-223** | 앱 target 흐름 재정렬 및 실제 컨테이너 빌드 편입 | 2026-06-13 | `geo -> conc -> map -> pinvi`, `srv` 별칭 반영 |

---

## 완료 태스크 세부 내역

### T-001: 에이전트 및 워크스페이스 문서 초기화

- [x] `antigravity.json`, `claude.json`, `codex.json` 설정 파일 생성
- [x] `.gemini/`, `.claude/`, `.codex/` 내부 설정 디렉토리 매핑
- [x] 에이전트 협업 정책 (`AGENTS.md`), 진입 컨텍스트 (`CLAUDE.md`), 스킬 매뉴얼 (`SKILL.md`) 생성
- [x] 아키텍처 문서(`docs/architecture.md`) 및 의사결정 문서(`docs/decisions.md` ADR-1~4) 생성
- [x] 작업 일지 (`docs/journal.md`) 및 백로그 관리 파일 (`docs/tasks.md`) 작성

### T-002: 프로젝트 인프라 설정

- [x] 모노레포용 통합 `.gitignore` 작성
- [x] PostgreSQL + RustFS 구동을 위한 `docker-compose.yml` 루트 정의
- [x] `kor-travel-geo`용 PostgreSQL/RustFS 포트·credential·bucket 기본값 이관
- [x] 공용 인프라 구동/정지/재시작 초기 helper 추가
- [x] 전체 저장소 개요를 담은 `README.md` 작성

### T-003: FastAPI 백엔드 뼈대 구성

- [x] `backend/pyproject.toml` 생성 및 dependencies 추가 (FastAPI, uvicorn, docker sdk 등)
- [x] `backend/src/kor_travel_docker_manager/main.py` 진입 소스 및 환경 설정 모듈 작성
- [x] 백엔드 ruff/lint 검증 스크립트 셋업

### T-004: Docker 제어 모듈 구현

- [x] `DockerService` 클래스 개발 (컨테이너 상태 수집, 시작/정지/재시작 통제)
- [x] API 라우터 (`/api/containers`) 연동 및 로그 조회 기능 작성
- [x] 단위 테스트 작성 및 pytest 통과 검증

### T-005: Next.js 프론트엔드 구성

- [x] `frontend/package.json` 및 `tsconfig.json` 정의
- [x] Zod, React Hook Form, TanStack Query, Tailwind CSS 패키지 설치
- [x] App Router 구조 기반 루트 레이아웃 작성

### T-006: 대시보드 UI 및 TanStack Query 연동 개발

- [x] TanStack Query Client 및 Query Provider 설정
- [x] 컨테이너 구동 제어 상태 카드 UI 구현
- [x] 백엔드 연동 액션 버튼 및 로그 출력 콘솔 UI 구현

### T-007: 품질 검증 및 최종 통합 테스트

- [x] 백엔드 및 프론트엔드 전체 린터/타입 빌드 테스트 실행
- [x] Docker 데몬 연동 수동 기능 확인
- [x] BMW M 디자인 시스템(DESIGN.md) 반영 및 `/bmw` 쇼케이스 검증 완료
- [x] `docs/design-system.md` 보강 및 `react-doctor` 성능 오딧 검증 완료
- [x] 변경 사항에 대한 `walkthrough.md` 작성 및 최종 PR 제출

### T-008: Docker 관리 문서 및 target registry 정리

- [x] `docs/docker-management.md` 신규 작성
- [x] 통합 DB 모델(`kor-travel-geo-postgres:5432`)을 공식 기준으로 문서 정정
- [x] UI/API/CLI에서 공유할 target registry 정의
- [x] 오래된 분리 DB target 제거 및 초기 helper 정리

### T-009: Python CLI 및 target ensure/build 구현

- [x] `ktdctl` console script 추가
- [x] `targets`, `status`, `ensure`, `logs`, `action`, `inspect` 명령 추가
- [x] `ensure <target> --build`에서 `docker compose up -d --build`를 인자 배열로 실행
- [x] CLI mock 테스트 추가

### T-010: Docker inspect API 및 secret redaction 구현

- [x] `GET /api/v1/targets` API 추가
- [x] `POST /api/v1/targets/{target}/ensure` API 추가
- [x] `GET /api/v1/containers/{container_id}/inspect` API 추가
- [x] inspect environment redaction 테스트 추가

### T-013: 설정 파일 기반 CLI 별칭 및 초기화/복구 step 구현

- [x] `config/docker-targets.yml`에 `db`, `storage`, `geo`, `map`, `ai`, `main` 의존 순서 정의
- [x] `ktdctl db --build`처럼 짧은 별칭을 직접 `ensure`로 실행하는 CLI shortcut 추가
- [x] 통합 DB database/role/schema/extension 복구 스크립트 추가
- [x] RustFS 공용 bucket 복구 스크립트 추가
- [x] `kor-travel-geo` 원천 디렉터리와 핵심 적재 테이블 검증 스크립트 추가
- [x] API/CLI가 같은 설정 파일 registry를 읽도록 정리

### T-014: Kor Travel/PinVi 계열 로컬 포트 정책 일원화

- [x] 관련 canonical 로컬 레포의 현재 포트 사용처 조사
- [x] `docs/ports.md`에 현재 포트와 정책 포트 비교표 작성
- [x] 통합 PostgreSQL host 포트를 `5432`로 변경
- [x] RustFS host 포트를 S3 API `12101`, console `12105`로 변경
- [x] RustFS 컨테이너 내부 포트를 이미지 표준 `9000`, `9001`로 정리
- [x] `kor-travel-docker-manager` Backend API를 `12901`, Dashboard Web을 `12905`로 변경
- [x] `config/docker-targets.yml`에 포트 정책 metadata와 target 대역 추가
- [x] 포트 정책 ADR 추가

### T-015: 실행 위치 정책 문서화

- [x] `AGENTS.md`에 git/WSL/Playwright E2E 실행 위치 강제 규칙 추가
- [x] `SKILL.md` 빠른 시작 명령을 WSL 기준으로 정리
- [x] `docs/dev-environment.md`에 명령 실행 위치 표와 에이전트 작업 절차 추가
- [x] `CLAUDE.md` 빠른 검증 명령에 WSL/Windows 예외 정책 명시

### T-028: Linux 전용 개발·버전관리·CodeGraph 실행 위치 정책 정리

- [x] `git` 버전 관리 명령을 Windows 호스트 예외에서 Linux shell 전용으로 변경
- [x] CodeGraph 생성/동기화도 Linux shell에서만 수행하도록 명시
- [x] Playwright E2E 기본 실행 위치를 n150 Linux 운영 환경으로 바꾸고, 불가능한 경우에만 Windows fallback을 허용
- [x] `AGENTS.md`, `SKILL.md`, `CLAUDE.md`, `docs/dev-environment.md`, `docs/tasks-done.md`를 새 정책에 맞춰 동기화

### T-016: `kor-travel-geo` Docker API/UI target 편입

- [x] `docker-compose.yml`에 `kor-travel-geo-api`, `kor-travel-geo-ui` 서비스 추가
- [x] `config/docker-targets.yml`에 `kor-travel-geo-api`, `kor-travel-geo-ui` 관리 컨테이너 등록
- [x] `geo` target이 API/Web UI 실행과 원천 데이터 검증을 함께 수행하도록 변경
- [x] 초기 helper target도 `geo` 이상에서 API/Web UI를 포함하도록 정리
- [x] `.env.example`, 포트 문서, Docker 관리 문서에 API/Web UI 포트 기준 추가

### T-017: 관측 스택 Docker target 추가

- [x] `docker-compose.yml`에 Prometheus, Grafana, cAdvisor Exporter를 별도 service로 추가
- [x] `config/docker-targets.yml`에 Grafana, Prometheus, cAdvisor 관리 컨테이너를 등록
- [x] 포트 정책에 맞춰 Grafana, cAdvisor Exporter, Prometheus 포트를 배정
- [x] Prometheus scrape config와 Grafana Prometheus datasource provisioning을 추가
- [x] `.env.example`, 아키텍처/포트/Docker 관리 문서, ADR을 갱신

### T-018: 프로젝트명 및 CLI 명령 전환

- [x] 공식 프로젝트명과 GitHub 저장소명을 `kor-travel-docker-manager` 기준으로 문서화
- [x] Python package import path를 `kor_travel_docker_manager`로 변경
- [x] CLI console script를 `ktdctl`로 변경하고 이전 CLI 명령 안내 제거
- [x] 프론트엔드/백엔드 package metadata와 화면 metadata를 새 이름으로 갱신
- [x] Docker Compose project name을 `kor-travel-docker-manager`로 고정

### T-222: 관측 target 개별 분리 및 포트 재배치

- [x] 단일 관측 target을 제거하고 `gra`, `cadv`, `prom` target으로 분리
- [x] dependency 순서를 `db -> storage -> gra -> cadv -> prom -> geo -> map -> ai -> main`으로 조정
- [x] Grafana는 공용 연계를 위해 `12205`, cAdvisor는 `12301`, Prometheus는 `12401`로 배치
- [x] `kor-travel-geo` API/Web UI를 새 dependency 순서에 맞춰 `12501`, `12505`로 이동
- [x] CLI, API 테스트, 포트 문서, Docker 관리 문서, 개발 가이드를 같은 기준으로 갱신

### T-224: 과거 서비스명과 공용 인프라 명칭 정리

- [x] PinVi 전용 database, role, bucket, 환경변수 기본값을 `pinvi` 및 `PINVI_*` 기준으로 정리
- [x] 공용 RustFS와 관측 컨테이너 이름을 `kor-travel-*` 기준으로 정리
- [x] 문서, 테스트, 설정 파일에서 과거 서비스명 잔여 표기를 제거

### T-026: PinVi API worker 기본값 환경변수화

- [x] `pinvi-api` compose command의 hardcoded `--workers 2`를 `PINVI_API_WORKERS` 환경변수로 대체
- [x] shared broker 도입 전 PinVi WebSocket broadcast broker가 process-local이라는 운영 제약을 문서화
- [x] `.env.example`에 `PINVI_API_WORKERS=1` 기본값 추가

### T-027: PinVi public API URL·CORS origin 환경변수화

- [x] `pinvi-web` build/runtime의 API URL을 `PINVI_PUBLIC_API_URL`로 주입 가능하게 변경
- [x] `pinvi-api` CORS 허용 origin을 `PINVI_CORS_ALLOWED_ORIGINS`로 주입 가능하게 변경
- [x] dev 기본값은 로컬 `127.0.0.1` API/Web origin으로 유지하고 prod 실제 도메인은 gitignore `.env`에만 두도록 문서화

### T-032: C6c Map·PinVi image source provenance fail-close

- [x] production `pinvi-pair capture/deploy --build`가 Map·PinVi checkout의 exact Git root,
      clean worktree, lowercase 40자 `HEAD`를 container mutation 전에 검증하고 각 API build arg의
      유일한 source revision으로 전달하도록 고정
- [x] live worktree 대신 exact `HEAD` Git archive 임시 context를 사용해 build 중 변경·원복과
      ignored 파일 혼입을 차단
- [x] raw/resolved Compose의 context·in-tree Dockerfile·build arg를 exact allowlist로 고정하고
      external Dockerfile, additional context, secret, target 같은 추가 build input을 거부
- [x] build 후 Map·PinVi immutable image의 `org.opencontainers.image.revision`과 PinVi
      `io.pinvi.build.environment=production`을 container stop/recreate 전에 검증
- [x] compatible-pair manifest를 v3로 clean-cut하고 active/rollback과
      capture/deploy/rollback/recovery/smoke 결과에 image ID↔source revision을 함께 보존
- [x] 단일 적대적 리뷰의 두 P1을 반영하고 재검토 `ACCEPT FOR TESTS` 확인
- [x] WSL Docker Python 3.13 C6c focused `597 passed`, backend 전체 `685 passed`, 변경 source
      strict mypy, Ruff, production Compose `config --quiet`/resolved exact build mapping 통과
- [x] PR #58을 squash merge(`ecaab504e63a99cb757318d3b67337bec962d90b`)하고 상위 C7
      완료 흐름의 n150 production gate까지 마감

### T-037: C6c Map UI 통합 경로 smoke 정렬

- [x] 제거된 `/ops/providers`를 smoke 대상에서 삭제하고 provider/job 운용 정본인
      `/ops/datasets`로 login `next`, 보호 GET, logout 뒤 재차단을 통합했다.
- [x] auth lifecycle 단위 테스트와 Docker 관리 문서를 같은 경로로 정렬하고 PR #67의
      단일 적대적 리뷰, backend/focused 테스트, Ruff, strict mypy와 CI를 통과했다.
- [x] 2026-07-27 n150 compatible-pair에서 실제 로그인, `/ops/datasets` 200, logout 뒤
      재차단을 확인했다.

### T-038: Map destructive production 명시 승인 결선

- [x] canonical `kor-travel-map-api`에
      `KOR_TRAVEL_MAP_API_DESTRUCTIVE_ENABLED=true`를 literal로 두고 raw/resolved/runtime에서
      API에만 존재하는지 fail-closed로 검증했다. standalone Map 기본값 `false`와 Manager의
      production 명시 승인을 분리했다.
- [x] compatible-pair manifest v4와 C7 attestation의 Map API environment hash가 이 승인을
      포함하도록 했고, destructive backup 감사 actor가 실제 인증 principal을 기록하도록
      Map issue #796의 OpenAPI/actor 변경과 함께 완결했다.
- [x] PR #68과 Map 후속 변경의 리뷰·CI를 통과했다. 2026-07-26 n150 C7 destructive live
      gate를 통과하고 Map issue #796이 closed된 것을 확인했다.

### T-039: C6c PinVi login SSR shell 판정 정렬

- [x] PinVi `/admin/login`의 `Suspense fallback={null}` client page는 SSR HTML에 form이 없을
      수 있으므로 HTTP smoke를 200·`text/html`·비어 있지 않은 body·일반 Next.js static
      marker·`admin/login` 전용 page chunk 계약으로 한정했다.
- [x] generic fallback이나 다른 route chunk는 계속 거부하고 hydration 뒤 form과 실제 로그인은
      browser smoke가 소유하도록 책임을 분리했다. PR #69의 단일 적대적 리뷰, focused/full
      테스트, Ruff, strict mypy와 CI를 통과했다.
- [x] 2026-07-27 n150 compatible-pair에서 HTTP shell 계약과 최종 Playwright hydrated login
      form·로그인 동작을 함께 확인했다.

### T-040: C7 Map features routes production 명시 결선

- [x] canonical `kor-travel-map-api`에
      `KOR_TRAVEL_MAP_API_FEATURES_ROUTES_ENABLED=true`를 literal로 두고 다른 service,
      `env_file`, build arg, command, label, config, secret 경로에는 이름 자체가 없도록
      raw/resolved/runtime 계약과 음성 회귀 테스트로 고정했다.
- [x] PR #71의 focused 42개, C6c·Docker config 849개, backend 907개, Ruff baseline 제외,
      strict mypy, canonical Compose gate, 단일 적대적 리뷰와 CI를 통과했다.
- [x] 2026-07-27 newer compatible-pair와 C7 live에서 feature 관리 REST를 확인했고 manager
      issue #70이 closed된 것을 확인했다.

### T-041: C6c rollback image retention 보장

- [x] PR #73(`c7328ed9`)에서 deploy/rollback 시작 전 manifest active/rollback과 candidate
      다섯 image를 service+전체 image SHA 기반 content-addressed reference로 보존하고 exact
      image ID를 재검증하도록 했다.
- [x] retention 실패는 첫 container mutation 전에 중단한다. manifest commit 뒤 새 rollback
      세대 밖 reference를 정리하고 cleanup residue는 다음 mutation 전에 해소하도록 했다.
- [x] moving tag rollover, 일부 tag 실패, wrong-ID collision, candidate 실패 정리,
      active=rollback dedupe, no-build/rollback/capture, post-commit cleanup pending 차단을
      실행형 회귀로 검증했다. retention 과정의 프로세스 `SIGKILL` 주입은 완료 증거로
      과장하지 않으며, 현재 완료 판정은 content-addressed 불변성·복구 보존·실운영 검증에
      근거한다.
- [x] 2026-07-27 n150 production에서 active/rollback retention reference의 가용성과
      cleanup 성공을 확인했다. issue #72가 closed된 것을 확인했다.

### T-021: PR #36 후속 하드닝(신뢰 프록시 시크릿·brute-force durable·공개키 DB 직접조회·모달 a11y)

- [x] AUTH-3: `KTDM_TRUSTED_PROXY_SECRET`으로 신뢰 CIDR 매칭만으로는 `X-Forwarded-*`를 신뢰하지
      않도록 보강한다. host 네트워크의 로컬 프로세스가 loopback 출처로 헤더를 위조하는 것을 차단한다.
- [x] AUTH-6: 로그인 실패 카운터를 인메모리가 아닌 감사 로그(durable) 기반으로 계산해 재시작·다중
      워커에서도 유지되게 한다(`check_login_rate_limit`).
- [x] APIKEY-1: 공개 API 키 유효성을 DB에서 직접 조회한다(`public_api_key_is_valid`).
- [x] FE-4: 로그·차트·설정 모달에 `role="dialog"`/`aria-modal`/`aria-label`을 부여한다.
- [x] PR #38 머지 및 prod 배포 후 검증.

### T-025: 배포 런북 + push 전 보안 감사 절차

- [x] 민감 정보를 담는 `docs/deploy-runbook.local.md`를 gitignore(`*.local.md`) 대상으로 유지하고,
      반복 배포 실수와 고정 절차를 상세히 기록한다.
- [x] AGENTS.md에 「prod 배포 & 보안 감사」와 「remote 푸시 전 보안 감사(필수 절차)」를 둔다.
- [x] DO NOT #13/#14로 민감값 커밋 금지를 명문화한다.
- [x] 프로젝트별 민감 문자열(호스트 IP·SSH 사용자·공개 도메인·관리자 비번·시크릿) 스캔 명령을
      런북에 고정한다.

### T-042: C7 WebSocket 종료 코드 계약(accept-then-close) 결선

`kor-travel-map`의 `T-ADM-C7W`(#806/PR #807)·`T-VN-H11`(#809)과 같은 결함이 매니저에도 있었다.
거절이 `accept()` 이전에 `close(4401)`을 호출해 uvicorn이 HTTP 403 handshake 거절로 바꿔 보냈고,
브라우저는 `4401`이 아니라 `1006`만 관측했다. 기존 테스트는 Starlette TestClient가 ASGI close
메시지를 그대로 되던지는 탓에 거짓 통과하고 있었다.

- [x] 세 pre-accept close를 accept-then-close로 전환하고 data frame 0건을 고정한다 (C-1/C-6).
- [x] settle window를 `KTDM_WS_ACCEPT_CLOSE_SETTLE_SECONDS`로 env 튜너블·`[0,5]` clamp한다 (C-3).
- [x] accept 실패 뒤 close 금지, accept→settle→close를 취소 shield child task로 묶는다 (C-4/C-5).
- [x] 프론트가 `4401`/`4000`을 소비해 재시도를 멈추고 LoginScreen으로 전환한다 (C-8).
- [x] 계약을 TestClient로 구분할 수 없으므로 ASGI 메시지 시퀀스(`accept`→`close`) 회귀로 고정하고,
      구 동작 negative control에서 6건이 실패함을 확인한다.
- [x] 같은 handler의 확인된 결함(idle container client 종료 미검출, 소진 stream 무한 polling,
      accept 후 close 없는 return, event loop 위 blocking 호출, 연결 0건 docker sweep,
      살아 있는 소켓의 세션 재검증 부재)을 함께 정리한다.
- [x] 적대적 리뷰 2명 × 2라운드를 반영한다. 재인가 우회(keepalive가 창을 리셋), 프론트 backoff
      무력화, `ensure_ascii` 회귀, 테스트 모듈의 공유 env 오염을 mutation/negative control로 고정.
- [x] n150 운영 HAProxy TLS 엣지 경유 실브라우저에서 `code=4401, wasClean=true, data frame 0건`,
      알 수 없는 container는 `4000`, 로그아웃 후 20초간 WS 재연결 0건을 확인한다.
- [x] settle 기본값을 실측으로 확정한다 — `0.25` 10/10, `0.0` 12/12 모두 4401(1006 0건)이라
      `0.0`으로 두고 knob은 유지. uvicorn ws 구현·프록시 토폴로지 변경 시 재측정.

### T-033: C7 Map UI·Dagster OCI revision 결선

- [x] `kor-travel-map-api`, `kor-travel-map-ui`, `kor-travel-map-dagster`,
      `kor-travel-map-dagster-daemon`의 build가 모두 동일한 canonical
      `KOR_TRAVEL_MAP_GIT_COMMIT`을 Dockerfile에 전달하도록 compose를 정렬한다.
- [x] raw·resolved compose 계약이 네 service의 build arg·snapshot context·Dockerfile을
      exact 검증하고 일부 service 또는 revision이 다른 회귀를 첫 mutation 전에 차단한다.
- [x] candidate build가 Map runtime 네 image와 PinVi image를 모두 같은 frozen snapshot에서
      완성하고, 각 immutable image ID와 OCI revision을 manifest v4에 기록한다.
- [x] capture/deploy/rollback이 Map runtime 네 service를 같은 frozen transaction으로
      재생성·검증하며, 복원 실패 시 다섯 runtime을 모두 중지해 혼합 generation을 차단한다.
- [x] resolved fixture drift, candidate build service 누락, dependent image/revision mismatch,
      activation·rollback 누락에 대한 회귀 계약을 추가한다.
- [x] canonical v4 경로가 저장소 역사에 존재한 sibling `compatible-pair-v2.json`과
      `compatible-pair-v3.json`을 payload·file type과 무관하게 mutation 전에 fail-close하고,
      legacy bytes 불변과 Docker 미호출을 실행형 회귀로 고정한다.
- [x] n150 production에서 실제 기동 중인 `kor-travel-map-api`·`-ui`·`-dagster`·
      `-dagster-daemon` 네 컨테이너의 `org.opencontainers.image.revision` label을
      `docker inspect`로 직접 확인 — 네 image 모두 동일한 40자 commit
      (`c8ed6164381fccd35df1840427e5a682f2a2789d`)이었고, 이는 compatible-pair
      manifest v4의 `map_source_revision` 기록과도 정확히 일치했다. issue #60은 이미
      closed 상태였다.

### T-034: C6c cAdvisor healthcheck 포트 계약 정렬

- [x] canonical compose의 cAdvisor listen 포트와 명시적 `/healthz` healthcheck가
      모두 `CADVISOR_PORT`(기본 `12301`)를 단일 정본으로 사용하게 한다.
- [x] raw compose 계약이 exact `--port=${CADVISOR_PORT:-12301}`과 health URL을 고정하고,
      default/custom resolved config에서 listen·probe 포트가 같은지 검증한다.
- [x] n150 production에서 cAdvisor `healthy`와 설정 포트(`CADVISOR_PORT`) `/healthz` 200을
      직접 확인했다. compatible-pair manifest v4에 2026-07-27 기록된 `active` 세대가
      이미 존재하고 그 이후 계속 healthy 상태였다 — capture와 후속 readiness가 이미
      통과한 상태임을 확인했다. issue #62는 이미 closed 상태였다.

### T-035: C7 Map production API 인증 env 결선

- [x] ADR-23에서 admin BFF, API-only service/cursor, public/debug/profile, metrics 비활성 계약과
      service별 최소 주입 범위를 문서로 먼저 고정한다.
- [x] canonical Compose와 `.env.example`에 Map #780/#782 production 설정을 정확히 반영했다.
- [x] C6c raw/resolved/runtime preflight가 credential shape·상호 구분·허용 service exact set과
      production literal을 mutation 전에 검증하게 한다.
- [x] 누락·약한 값·재사용·다른 service 유출·설정 drift 음성 fixture를 추가했다.
- [x] 두 번째 적대적 리뷰 P1에 따라 manifest v4 exact 9-field shape 밖의 sibling 단조 marker를
      추가했다. 최초 v3/v3 logical manifest hash만 pending 재시도를 허용하고 성공 검증 뒤 complete로
      영구 닫아 A3→B4→rollback A3→C3 회전도 누락 예외를 다시 열지 못한다.
- [x] marker atomic write/fsync와 fixed shape, 0600 regular owner, corrupt/symlink/mode/owner 및 pending
      baseline drift fail-close 회귀 계약을 추가했다.
- [x] 두 번째 적대적 리뷰 P2에 따라 source Compose 전체 scalar tree에서 admin=API+frontend,
      service=API-only, cursor=v3 0회/v4 API exact 1회 외 모든 service/field leak를 거부했다.
- [x] 세 번째 적대적 리뷰 P2에 따라 profile/public/debug도 API-only exact path로 올리고,
      API·Dagster·daemon `env_file`의 known path/options와 tracked exact-revision 내용까지 검증한다.
- [x] 네 번째 적대적 리뷰 P2에 따라 tracked `env_file`을 exact `100644 blob`·64 KiB 이하·UTF-8로
      제한하고, 허용되지 않은 service의 `env_file: null` 우회도 차단했다.
- [x] `.env.example`의 세 공개 local placeholder를 production config/raw/resolved에서 각각 거부하고
      local 허용 회귀 계약을 추가했다.
- [x] 동일 적대적 리뷰어의 최종 P0~P2 없음 판정 뒤 backend 886개, 변경 파일 Ruff,
      strict mypy, 기본·커스텀 Compose gate를 통과했다.
- [x] PR을 merge한다.
- [x] n150 final v4 exact-pair에서 Map API startup/readiness(`kor-travel-map-api-latest`
      46시간째 healthy)와 runtime secret isolation을 `docker exec env`로 이름만
      확인(값은 출력하지 않음) — Map API에는 admin proxy·service token·cursor
      signing secret·features/destructive 플래그가 모두 존재, admin BFF인 Map UI에는
      정확히 admin proxy secret만 존재, Dagster/daemon에는 둘 다 부재 — 설계된
      격리 계약과 정확히 일치했다. issue #63은 이미 closed 상태였다.

### T-036: C7 PinVi Dagster image 계약 정렬

- [x] C7 exact PinVi source revision의 `apps/etl/Dockerfile`과 package metadata에서
      `DAGSTER_HOME=/opt/pinvi/.dagster`, code location `pinvi.etl.definitions` 계약을 확인한다.
- [x] canonical `pinvi-dagster` Compose가 image 계약을 과거 `tripmate` 경로로 덮어쓰지 않도록
      environment와 command를 정렬한다.
- [x] resolved Compose 회귀 테스트로 `DAGSTER_HOME`과 code location을 고정한다.
- [x] 적대적 리뷰 승인 뒤 focused/backend/Compose gate를 통과하고 PR #66을 병합한다.
- [x] n150 production에서 `pinvi-dagster-latest`가 9일째 healthy 상태로 실행 중임을
      확인했다 — C7 compatible-pair capture에서 PinVi dependent bootstrap이 이미
      완료된 상태임을 확인했다.

### T-012: 대시보드 상세 패널 확장

- [x] 컨테이너 row의 상세 버튼으로 inspect modal을 연다(`ContainerDetailModal`).
      기존 모달과 같은 `role="dialog"`/`aria-modal`/`aria-label` 패턴에 Esc 닫기와
      포커스 이동을 더했다.
- [x] mounts, networks, healthcheck, redacted env를 탭으로 분리한다
      (`role="tablist"`/`tab`/`tabpanel`과 `aria-controls`/`aria-labelledby` 연결).
- [x] target 단위 `ensure --build` 버튼을 개발 빌드에서만 제공한다. `containers`는 사실
      depends_on까지 펼쳐진 목록이라(통합 PostgreSQL은 db·geo·conc·map·pinvi·all 여섯
      target에 모두 있다) 첫 매치를 쓰면 `dependency_order` 순서에 의존하게 된다.
      순서와 무관하게 **가장 좁은** target을 고른다. 실행 전에는 실제 재생성 대상
      서비스 목록과 개수를 보여 주고 확인을 받는다(db 포함 시 스키마 스크립트 경고).
- [x] 모바일/데스크톱 반응형을 실브라우저로 검증한다. 390×844에서 page 가로 스크롤 0,
      모달이 viewport 안에 들어오고, 넓은 Mounts 표와 탭 목록은 각자의 컨테이너 안에서만
      가로 스크롤한다. 1440×900도 동일.
- [x] 로컬 실브라우저 검증: 18개 row 전부 상세 버튼 노출, 5개 탭이 실제 inspect 데이터를
      렌더(마운트 rw/ro, 네트워크 2개 IP/MAC/alias, healthcheck `healthy`+최근 검사 로그).
      콘솔 오류 0건(로그인 전 401과 기존 favicon 404 제외).
- [x] **적대적 리뷰 2명이 찾은 비밀 노출을 수정한다.** 이 패널이 inspect를 UI에 연결한
      최초 지점이라, 그동안 API/CLI에만 있던 redaction 공백이 브라우저 한 번의 클릭으로
      열렸다. key 이름 누락(`*_API_KEY`가 `ACCESS_KEY`에 안 걸림)과 값 내부 credential
      (`postgresql://user:password@`)을 모두 막고, `cmd`/`entrypoint`도 필터에 넣었다.
      실행 중 10개 컨테이너 전수 검사에서 유출 0건, `KTG_PG_DSN`은
      `postgresql+psycopg://addr:<redacted>@...`로 비밀번호만 가려지는 것을 확인했다.
- [x] 리뷰 지적 반영: WS broadcast(2초)마다 포커스를 빼앗던 effect 의존성 수정(7초간
      포커스 유지 확인), `aria-controls` dangling IDREF 4건 해소, tabpanel `tabIndex=0`,
      탭 좌우/Home/End 키 이동, 미생성·오프라인 컨테이너 버튼 비활성화, raw JSON 오류 문구
      교체, running 중 `종료 코드 0` 오표시 제거.

### T-011: 설정 저장 안정화 및 validation 고도화

- [x] host 네트워크 기준에서 설정 저장·reset·미생성 start fallback이 Docker SDK 직접 생성 경로를 우회하지 않고 `docker compose up --force-recreate`를 사용하도록 변경
- [x] compose 변경 전 diff 생성 및 UI 표시. 백엔드 호출 없이 프론트에서 baseline(`configTargetContainer.config`) vs
      현재 입력을 실시간 비교해 포트/네트워크 추가·삭제, env 변경 전후 값을 모달에 즉시 표시한다.
- [x] 포트, 볼륨, 네트워크 입력 validation 강화. 백엔드에 `docker_service.validate_container_config_update`를
      추가해 lock 획득·Docker 접근보다 먼저 검증하고(`ContainerConfigValidationError` → HTTP 422),
      프론트에도 같은 규칙을 미러링해 왕복 없이 즉시 피드백한다. 포트는 `${VAR:-default}` 보간 토큰을
      opaque하게 신뢰하고 리터럴 숫자만 1~65535 범위·host:container 형식을 검사한다(docker-compose.yml
      실제 ports 18개 전수 통과를 회귀 테스트로 고정). 볼륨은 이미 `compose_volume_graph_hash` 비교로
      완전히 불변 처리되어 있어(어떤 변경도 첫 mutation 전에 409) 새 검증을 추가하지 않고, 대신 프론트에서
      변경을 미리 감지해 제출 전에 경고하고 제출을 막는다.
- [x] secret 성격 값은 `.env` override로 저장하도록 안내 및 방어 로직 추가. 정적 key-이름 휴리스틱만 쓰면
      `KOR_TRAVEL_MAP_API_PUBLIC_API_KEY_REQUIRED=true`처럼 이름에 "API_KEY"가 들어 있지만 원래부터 리터럴
      불리언인 값까지 오탐으로 막는다(실제 compose 파일 전수 검증에서 발견). 그래서 "baseline이 이미
      `${...}` 보간이었는지"를 기준으로 삼는다 — 이미 `.env`로 분리돼 있던 참조를 리터럴로 되돌리는 것만
      막고, 원래부터 리터럴이던 값은 자유롭다(baseline을 모르는 신규 key에는 안전한 쪽으로 key-이름
      휴리스틱을 fallback으로 쓴다). key 이름과 무관하게 값 안에 literal 접속 자격증명
      (`scheme://user:pass@`)이 `${...}` 밖에 남아 있으면 별도로 거부한다 — `${OUTER:-postgresql://user:dev_pw@host/db}`처럼
      비밀번호까지 하나의 `${...:-default}` 안에 두는 이 저장소의 기존 관행은 통과시킨다.
- [x] config 파일 변경과 재생성을 같은 host lock transaction으로 묶고, recreate/init 실패 시 원본 byte와
      파일 mode를 원자 복원한 뒤 기존 runtime 재생성을 시도하는 rollback 전략 문서화
- [x] **적대적 리뷰 2명이 찾은 문제를 수정한다(1차 라운드).** (1) 리뷰어 1: `_value_has_literal_url_credential`가
      `${...}` 블록을 통째로 지우고 스캔해서, `${FAKE_NAME:-literal-secret}`처럼 지어낸 변수명으로
      감싸기만 해도 credential 스캔이 통째로 우회됐다 — raw 값을 그대로 스캔하고 password 캡처
      그룹 자체가 보간인 경우만 예외로 인정하도록, 그리고 credential 스캔을 baseline과 완전히
      같은 값에만 예외로 두도록 재작성했다. (2) 리뷰어 2: ports/volumes/networks 행의 React
      `key`에 필드 값 자체(`port-${idx}-${port}`)가 들어 있어 매 keystroke마다 DOM 노드가
      재마운트되어 포커스가 사라졌다 — index 전용 key로 교체(행은 추가/삭제만 되고 재정렬은
      없어 안전). 그 외 whitespace로 인한 오탐 메시지, `aria-describedby`/`aria-live` 접근성
      공백도 함께 수정. 두 버그 모두 리뷰어가 제시한 실제 입력으로 직접 재현 후 수정 확인,
      추가로 수정을 되돌리는 mutation test로 새 회귀 테스트 4건만 실패하는 것을 확인했다.
- [x] **1차 라운드 수정 직후 재검토에서 발견한 2차 보안 공백을 수정한다.** 1차 수정은
      `scheme://user:pass@` 형태(DSN)에만 반응하는 credential 스캔이라, URL이 아닌 단일 리터럴
      비밀(예: `GF_SECURITY_ADMIN_PASSWORD=${GRAFANA_ADMIN_PASSWORD:-admin}`)에는 적용되지
      않는다는 것을 재발견했다 — baseline이 이미 `${...}` 보간이어도, sensitive key의 값을
      `${TOTALLY_MADE_UP_NAME:-h4x0r-literal-secret}`처럼 지어낸 이름 + 새 리터럴 default로
      바꾸면 "여전히 보간 형태"라는 이유만으로 규칙 1(재보간 요구)을 통과했다(실제
      `docker-compose.yml`의 Grafana 서비스로 직접 재현). 규칙 1을 일반화해, sensitive key면
      `${...}` 형태를 유지해도 `:-default` 리터럴 자체가 baseline과 달라졌으면 거부하도록 했다
      (변수 이름만 바뀌고 default 리터럴이 동일하면 허용, default를 아예 없애는 것도 허용,
      비-sensitive key는 대상이 아님 — 포트 기본값 변경 등 정상 편집을 막지 않는다). 백엔드
      회귀 테스트 5건 추가 + mutation test로 새 테스트 2건만 실패 확인, 전체 1047 backend
      테스트 통과, 프론트 `configValidation.ts`에 동일 로직 미러링 후 실브라우저(WSL dev
      backend/frontend, 로그인 세션)에서 Grafana 컨테이너의 `GF_SECURITY_ADMIN_PASSWORD`
      필드에 직접 재현 입력을 타이핑해 차단 메시지·diff 미리보기·제출 버튼 비활성화를
      확인했다(실제 컨테이너 재생성은 트리거하지 않음). 같은 세션에서 포트 필드에 여러
      글자를 연속 입력해 React key 수정으로 포커스가 유지되는 것도 함께 재확인했다.

### T-044: ensure 라우트의 production 서버측 차단

T-012 적대적 리뷰에서 확인한 공백이다. `POST /targets/{target}/ensure`는 production에서도
비-C6c target을 막지 않는다. `assert_manager_mutation_allowed`는 환경 선언의 정합성만
검증하고 문자열을 돌려주며, `assert_c6c_mutation_allowed`는 대상 service가 C6c
runtime(Map 4종·pinvi-api)과 겹치지 않으면 그대로 반환한다. 따라서 db·storage·gra·cadv·
prom·geo·conc는 통과한다.

현재는 프론트의 `NODE_ENV` 분기(빌드 타임 제거)가 **유일한** 방어선인데, 이는 브라우저
번들의 속성이지 백엔드의 속성이 아니다. `npm run dev` 프론트를 운영 백엔드에 붙이면
버튼이 보이고 실제로 실행된다.

- [x] `ComposeService.ensure_target`이 `assert_manager_mutation_allowed`의 반환값(`mode`)을
      받아, `assert_c6c_mutation_allowed` 호출 직후(`c6c_deployment_lock` 안, baseline 검사와
      첫 Docker subprocess보다 먼저) `mode == "production"`이면 target·service 구성과 무관하게
      전면 차단하도록 했다. `assert_c6c_mutation_allowed`·`assert_compose_mutation_allowed`·
      `control_container`·`update_container_config`·`reset_container_config`는 건드리지
      않는다 — 그 경로들의 "비-C6c target은 production에서도 허용" 동작은 개별 컨테이너
      제어를 위한 의도된 동작이라 범위 밖이다(`ensure`만 target 전체를
      `--build`/`--force-recreate`+init step까지 허용하는 범용 dev 부트스트랩이라 다르다).
      `DeploymentContractError` → 기존과 동일하게 HTTP 409, 라우트 코드 변경 없음.
      CLI(`ktdctl ensure`)도 같은 메서드를 호출하므로 동일하게 막힌다.
- [x] 음성 회귀 테스트 2건 추가: production + 비-C6c target(`storage`) → 거부(`subprocess.run`
      미호출까지 확인), local/개발 모드에서는 동일 target의 정상 `ensure` 흐름이 막히지
      않는지 양성 대조로 확인. 기존 `test_production_generic_mutation_guard_rejects_every_api_entrypoint`
      (C6c target `map`은 `assert_c6c_mutation_allowed`로 이미 차단)와 공존하며 서로
      가리지 않는 것도 확인.
- [x] **적대적 리뷰 2명 + 독립 검증 통과.** 리뷰어 1은 `mode` 판정이
      `_validate_mutation_environment`(local/production만 반환하는 fail-closed 계약)에
      전적으로 의존해 우회 불가능함을, `ensure_target`/`_ensure_target_unlocked`의 호출자가
      API 라우트와 CLI 단 둘뿐임을(다른 라우트·WS·스케줄러 경로 없음) 확인했다. 리뷰어 2는
      두 production 차단(C6c 전용·신규 전면 차단)이 서로 마스킹하지 않고 공존함을, 실제로
      의존하는 production `ensure` 사용처가 문서상 없음을 확인했다. 두 리뷰 모두 raise 메시지가
      비-C6c target에서만 도달하는 이 지점에 compatible-pair(Map/PinVi 전용) 문구를 넣은 것이
      부적절하다고 지적해 메시지를 단순화했고("manage this service directly on the host
      instead"), 테스트 하나에 실제로는 읽히지 않는 `monkeypatch.setenv` 호출이 남아 있던 것도
      제거했다. 검증 단계에서 두 리뷰의 모든 구체적 주장을 코드에서 직접 재확인(CONFIRMED)했고
      우회·회귀는 발견되지 않았다. backend 1049 passed, ruff 기존 9건 유지, 변경 파일 mypy
      clean. 백엔드 정책 변경만이라 UI 변경이나 실브라우저 E2E는 필요 없다.

### T-043: WS 인가 동시성 상한 + 프론트 배포 preflight

T-042의 적대적 리뷰가 남긴 두 항목이다. accept-then-close 계약상 미인증 peer도 handshake를
완료하는데 WS 라우트에는 제한이 없었고, 운영 호스트의 `frontend/node_modules`가 부분 설치
상태로 남아 배포 중 빌드가 실패할 수 있었다.

- [x] 동시 인가 handshake를 `KTDM_WS_MAX_PENDING_AUTHORIZATIONS`로 묶고 초과분을
      `close(1013)`으로 흘려보낸다. per-IP를 쓰지 않는 이유(프록시 IP 단일화)를 함께 기록한다
- [x] 서버를 내리기 전에 프론트 툴체인을 검증하는 `scripts/verify-frontend-toolchain.sh`를
      추가한다(`npm ls --depth=0`이 결정적 게이트)
- [x] 적대적 리뷰 2명 반영 — 잘못된 위협 모델(미인증 peer는 SQLite에 도달하지 못한다),
      shed 경로가 더 비쌌던 문제(거절당 로그 write), `--limit-concurrency` 오권고,
      counter 증가를 검증하지 않던 테스트 공백을 모두 수정
- [x] n150 배포 후 1013 shed 동작과 preflight를 운영에서 확인했다. `backend/src`·
      `frontend/src`를 rsync(보존 파일 제외)한 뒤 백엔드 프로세스와 프론트
      `next start -p 12905` 프로세스 그룹만(호스트에 공존하는 다른 앱들은 미접촉)
      재기동했다(`/health` 200, `next start` `Ready in 947ms`). `scripts/verify-frontend-toolchain.sh`
      실행 결과 `툴체인 정상`. 실제 production `/api/v1/ws/status`에 유효한 Origin
      헤더로 300개 동시 미인증 WebSocket 연결을 발생시켜 관측: `4401`(AUTH_REQUIRED) 121건,
      `1013`(TRY_AGAIN_LATER, shed) 179건 — 기본 상한(64)을 넘는 동시 인가 시도가 정확히
      shed되는 것을 실측으로 확인했다. 테스트 뒤 `/health` 200 유지, 다른 컨테이너 전부
      기존 uptime 그대로 영향 없음을 확인했다.

### T-013 (2026-06-20): 운영(prod) 공개 주소 `.env` 주입 및 CORS 환경변수화

- [x] 백엔드 CORS 허용 Origin을 `KTDM_CORS_ALLOW_ORIGINS`(콤마 구분, 기본 `*`)로 제어하고 기동 시 루트 `.env`를 로드한다
- [x] 프론트엔드 백엔드 주소를 `.env.development`/`.env.production`로 분리하고 `.env.local` 섀도잉을 제거한다
- [x] 실제 운영 도메인은 gitignore된 `.env`/`frontend/.env.production`에만 두고 `.env.example`은 플레이스홀더로 문서화한다
- [x] 백엔드 ruff·CORS 파싱, 프론트 type-check·prod 빌드(인라인) 검증

### T-014 (2026-06-20): Docker host 네트워크 전환·컨테이너=호스트 포트·서비스 prod URL·pinvi-dagster·tripmate 정리

- [x] dev 기본 네트워크를 `network_mode: ${KTDM_DOCKER_NETWORK_MODE:-host}`로 전환하고 인프라/앱이 호스트 정규 포트에 직접 바인딩하도록 맞춘다
- [x] 서비스 간 참조(DSN/RustFS/내부 API/Dagster)를 `127.0.0.1:<포트>`로, Prometheus scrape·rustfs-init 엔드포인트도 동기화한다
- [x] geo/concierge/map/pinvi 컨테이너 내부 포트를 호스트 포트와 동일하게 통일한다
- [x] `pinvi-dagster`(12802)를 compose/registry/`pinvi` target에 추가하고 PinVi `apps/etl/Dockerfile`을 신규 작성한다
- [x] 관리 16개 서비스의 prod 공개 URL을 `KTDM_PROD_URL_*`(.env, 비노출)·`prod_url_env`로 주입해 대시보드 `public_url`로 표시한다
- [x] tripmate 로컬 잔재 정리(`pinvi_metrics.db` 개명, `ktd_venv` 재생성)
- [x] `docker compose config`·백엔드 ruff·프론트 type-check/build 검증 및 문서 동기화

### T-015 (2026-06-20): 프론트 Tailwind v4 + StyleSeed 전면 전환·전역 오류 복구 boundary

- [x] `kor-travel-geo` PR #391의 오류 복구 boundary(error/global-error/AppErrorPanel/error-recovery)를 매니저에 반영
- [x] Tailwind v4 전환(`@import`+`@theme`, `@tailwindcss/postcss`, autoprefixer/tailwind.config 제거)
- [x] `kor-travel-geo-ui/docs/DESIGN-RULES.md`의 StyleSeed 라이트 토큰을 `@theme`에 정의
- [x] `DashboardClient`·`AppErrorPanel`을 Pure Black → StyleSeed 토큰으로 전면 리스타일
- [x] `docs/DESIGN-RULES.md` 포팅, `DESIGN.md` superseded 안내, ADR-17, 프론트 type-check/build 검증

### T-019 (2026-06-23): 관리자 로그인·세션·감사 로그·공개 API 키 관리

- [x] 단일 관리자 계정(`admin`) 로그인 화면을 추가하고 실제 비밀번호는 gitignore된 `.env`의 `KTDM_ADMIN_PASSWORD_HASH`에 PBKDF2 해시로만 저장
- [x] 관리자 세션을 HMAC 서명 `httpOnly` 쿠키와 DB 저장 세션 해시로 검증하고, 지정된 프론트엔드 Origin만 관리자 API를 호출하도록 제한
- [x] 로그인 성공·실패·로그아웃·API 키 생성/폐기 이벤트를 `login_audit_events`에 기록하고 관리자 UI에서 조회
- [x] VWorld 호환 32자리 공개 API 키를 UI 버튼으로 생성하고, 원문은 1회만 표시하며 DB에는 해시와 힌트만 저장
- [x] 공개 API 키 활성 해시는 짧은 TTL 메모리 캐시로 읽고 생성·폐기 시 즉시 무효화
- [x] 신뢰된 로그인 세션 요청은 공개 API 키 검증을 생략할 수 있도록 공통 dependency 제공
- [x] `kor-travel-geo` PR #399의 v2 공개 API 키·관리자 인증 env 계약을 compose와 `.env.example`에 반영
- [x] PR #399 사후 리뷰를 재확인해 미검증 `X-Forwarded-*` 신뢰 차단, 401 처리, 로그인 접근성, clipboard fallback을 보강

### T-029 (2026-07-13): Concierge DB read 키를 Map Dagster에 단일 source로 주입

- [x] 루트 `.env`의 `KOR_TRAVEL_MAP_KOR_TRAVEL_CONCIERGE_API_KEY`를 유일한 secret source로 정의
- [x] 실제 fetcher를 실행하는 Dagster·Dagster daemon에 동일한 base URL/key 환경변수 주입
- [x] 사용하지 않는 map API에는 read secret을 주입하지 않는 least privilege 계약 고정
- [x] `.env.example`, Docker 관리 문서, compose 계약 테스트 동기화
- [x] n150에 Concierge head 0017(scope migration 0016 포함) 배포·제약 및 실제 UI 로그인 검증
- [x] prod `.env` 주입·override의 key/base URL literal 각 세 줄 제거·compose 보간·Dagster 두 서비스 재생성
- [x] `.env`와 두 컨테이너 key를 값 비노출 constant-time equality로 확인
- [x] `limit=1` snapshot/changes 2페이지 cursor 검증, `page_size=200` 전체 8페이지/1,416건 순회, 실제 수집기 각 1,416건 및 내부/write 403 smoke
- [x] BFF/operator static admin overlap 회전·UI/BFF 검증 후 구 static 제거
- [x] 최종 old 401/new admin 200/read 공급 200·write 403/UI login 검증과 제한권한 백업 폐기

### T-030 (2026-07-13): Map OpiNet·KREX provider 키 compose 보간 drift 수정

- [x] OpiNet 공통 key가 과거 `KRTOUR_MAP_*` source 대신 현재 `KOR_TRAVEL_MAP_*` `.env` 값을 읽도록 수정
- [x] OpiNet map API live preview key는 별도 설정을 우선하고 미설정 시 공통 key를 재사용하도록 고정
- [x] EX·GO key가 과거 `KRTOUR_MAP_*` source 대신 현재 `KOR_TRAVEL_MAP_*` `.env` 값을 읽도록 수정
- [x] map API live preview key는 별도 설정을 우선하고 미설정 시 EX key를 재사용하도록 고정
- [x] OpiNet·KREX 공통 key는 Dagster·daemon에만, resolved preview key는 map API에만 주입하는
      최소 권한 계약과 `.env.example` placeholder를 테스트
- [x] 실제 secret 비노출 상태로 focused test·Ruff·Docker Compose 보간 검증

### T-220 (2026-06-13): `kor-travel-concierge` provider 상세 구현 및 과거 명칭 제거

- [x] `config/docker-targets.yml`의 `ai` target을 `kor-travel-concierge` 기준으로 정리
- [x] 과거 AI provider 명칭 기반 별칭을 제거하고 새 공식 별칭만 남긴다
- [x] 통합 DB 기본값을 `kor_travel_concierge` database 기준으로 정리하고 과거 env fallback을 제거한다
- [x] `pinvi` target이 `kor-travel-concierge`에 직접 의존하지 않도록 문서와 target 설명을 정리
- [x] `krtour-map`과 `kor-travel-concierge` 간 provider 관계만 남도록 아키텍처/포트/관리 문서를 동기화
- [x] 관련 테스트와 설정 검증을 갱신한다

### T-221 (2026-06-13): `kor-travel-geo` DB명·환경변수·Docker 이름·Prometheus scrape 계약 동기화

- [x] `kor-travel-geo` DB 기본값을 `kor_travel_geo`로 맞춘다
- [x] manager override 변수는 `KOR_TRAVEL_GEO_*`, API/UI 컨테이너 내부 변수는 `KTG_*`로 맞춘다
- [x] Docker service/container 이름을 `kor-travel-geo-*`로 맞춘다
- [x] 물리 데이터 디렉터리를 `/home/digitie/kor-travel-geo-data` 기준으로 맞춘다
- [x] RustFS bucket 기본값을 `kor-travel-geo`로 맞춘다
- [x] Prometheus scrape target에 `kor-travel-geo-api:12501/metrics`와 `kor-travel-geo-ui:12505/api/metrics`를 추가한다
- [x] 관련 문서와 테스트 fixture를 갱신한다

### T-223 (2026-06-13): 앱 target 흐름 재정렬 및 실제 컨테이너 빌드 편입

- [x] dependency 순서를 `db -> storage -> gra -> cadv -> prom -> geo -> conc -> map -> pinvi`로 조정한다
- [x] `kor-travel-concierge` target을 `conc`로 등록하고 API/MCP/Scheduler/Web UI compose service를 추가한다
- [x] `kor-travel-map` target을 `map`에 실제 API/Dagster/Web UI compose service로 연결한다
- [x] PinVi target을 `pinvi`로 등록하고 `srv`, `main` 별칭을 제공한다
- [x] 공용 DB/RustFS 복구 스크립트에 `krtour_map_dagster` database와 `kor-travel-concierge` bucket 보정을 추가한다
- [x] API/CLI 테스트와 문서를 새 target 흐름에 맞춰 갱신한다
