# 공용 제어 평면 PostgreSQL(:11000) 온보딩

## 이 문서는 누구를 위한 것인가

이 문서는 **kor-travel-docker-manager 저장소를 모르는 다른 저장소의 작업자**(geo · concierge · weather · transport, 그리고 앞으로 합류할 프로젝트)를 위한 것이다. n150 prod에는 프로젝트별 전용 PostgreSQL instance와 별개로, 여러 프로젝트가 database 단위로 나눠 쓰는 **공용 제어 평면 instance(`127.0.0.1:11000`)** 가 이미 떠 있다. 여기 합류하려면 Manager 저장소에서 바뀌어야 하는 것(compose·target 등록·secret·백업 role)과 **네 저장소에서 미리 끝낼 수 있는 것**(연결 파라미터화·Alembic head 고정·확장 목록 확정·데이터 규모 실측·advisory lock 점검)이 갈린다. 이 문서는 그 경계를 긋고, 네가 Manager PR을 기다리지 않고 **지금 당장 시작할 수 있는 일**을 앞쪽에 둔다. 이 문서가 다루지 않는 것은 "네 프로젝트가 언제 이전하는가"다 — 그것은 아직 아무 문서도 정하지 않았다(§10).

---

## 1. 지금 참인 것과 아직 아닌 것

이 저장소의 원칙은 "결정됐지만 아직 만들어지지 않은 구조를 현황으로 주장하지 않는다"이다. 먼저 그 선을 긋는다. **이 문서는 증거로 선을 긋는다고 선언했으므로, 증거 문장의 정확도가 곧 이 문서의 신뢰다** — 아래 각 행은 확인 가능한 술어로만 적었고, 시점에 따라 변하는 수치에는 측정 시각을 붙였다.

### 1.1 지금 참인 것 (2026-09-20 n150 실측)

| 항목 | 값 |
|---|---|
| 컨테이너 | `kor-travel-shared-postgres` (Up, healthy) |
| 이미지 | `postgis/postgis:16-3.5` — compose에 **리터럴 고정**(다른 앱 서비스처럼 `${..._IMAGE}` 템플릿이 아니다) |
| 서버 | PostgreSQL 16.9 / PostGIS 3.5.2 / pg_stat_statements 1.10 |
| 네트워크 | `network_mode: ${KTDM_DOCKER_NETWORK_MODE:-host}` — **기본값 있는 env 템플릿**이다(호스트 `.env`가 덮을 수 있다). 기본값 host에서는 컨테이너 프로세스가 호스트 포트에 직접 listen |
| 접속 좌표 | `127.0.0.1:11000` — 포트도 템플릿이다(`-p ${KOR_TRAVEL_SHARED_DB_PORT:-11000}`). **컨테이너 안에서도 5432가 아니라 11000**을 듣는다. 같은 서비스에 `ports: 127.0.0.1:${...}:${...}` 매핑이 함께 있지만 host networking에서는 Docker가 이를 무시한다 |
| 바인드 | `listen_addresses=127.0.0.1` (loopback 전용, 0.0.0.0 아님) |
| TCP 인증 | `scram-sha-256` (replication 포함). trust 행 없음. ⚠️ live `pg_hba_file_rules`에는 주소가 `all`인 **catch-all `host all all all scram-sha-256`** 행이 하나 있다 — 이 instance가 외부에 열리지 않는 근거는 hba가 아니라 오직 `listen_addresses=127.0.0.1`이다 |
| unix socket | `local all all trust` + `local replication all trust` — 의도적으로 유지(컨테이너 내부 전용: db-init·healthcheck·백업 CLI가 전부 여기 의존) |
| cluster 관리 role | `shared_admin` (superuser/createdb/createrole) — **앱에는 절대 주입하지 않는다** |
| bootstrap DB | `postgres` (앱 DB와 일부러 다른 중립 이름) |
| pgdata | 호스트 bind `/home/digitie/kor-travel-shared-data/pgdata` (0700, uid 999) |
| 정식 입주 프로젝트 | **concierge 하나뿐** — `kor_travel_concierge` (81 MB, owner `kor_travel_concierge_app`) |
| 연결 상한 | `max_connections=100` (기본값 — 공용 서비스 `command:`에 이 줄 자체가 없다). 2026-09-20 03:2x UTC 기준 사용 16 |
| 튜닝 | `shared_buffers=128MB` / `work_mem=16MB` / `maintenance_work_mem=128MB` / `effective_cache_size=512MB` / `max_wal_size=1GB` — 전부 `KOR_TRAVEL_SHARED_POSTGRES_*` env 템플릿이고 **cluster 전역 단일값**이다(테넌트별로 나눌 수 없다). `shm_size` 설정은 **없다** |
| prod 배포 트리 | `/opt/kor-travel-docker-manager` — 스택이 실제로 뜨는 곳이다(컨테이너 라벨 `com.docker.compose.project.config_files`가 이 경로를 가리킨다). `docker-compose.yml`·`config/docker-targets.yml`·`standalone_backup.py` md5가 `origin/main`과 정확히 일치 |
| ⚠️ prod 백업 cron이 도는 트리 | **다른 사본이다.** `digitie` crontab이 `/home/digitie/kor-travel-docker-manager/scripts/run-standalone-backup.sh`를 부르고, 그 스크립트가 고르는 `backend/ktd_venv`는 같은 트리의 `backend/src`를 editable로 가리킨다. 그 트리는 #363을 못 받아 **아직 옛 instance를 겨냥한다**(venv python으로 확인: `_ROLE_CONFIG["concierge"] == ('KOR_TRAVEL_CONCIERGE_POSTGRES_CONTAINER', 'kor-travel-concierge-postgres', 'kor_travel_concierge')`). 최근 manifest `concierge-*.manifest`의 `"instance"`가 `kor-travel-concierge-postgres:127.0.0.1:12600/kor_travel_concierge`로 그 사실을 그대로 적는다 |

concierge cutover는 **2026-09-19/20에 이미 끝났다**(실행 기록은 kor-travel-concierge 저장소 `docs/journal.md` 최상단 "2026-09-20: 공용 Postgres 인스턴스(`:11000`)로 실제 데이터 마이그레이션 완료(ADR-44)"). api·mcp·scheduler 세 컨테이너가 `:11000`을 보고 있다. 연결 수는 워커 기동에 따라 변하므로 숫자 자체는 판정 기준이 아니다 — 2026-09-20 03:2x UTC 실측으로 공용 instance 앱 연결 10개, 옛 instance(`:12600`) 0개이고, **불변량은 "옛 instance가 0"이라는 대비**다. Alembic head(`20260901_0029`)가 그대로 옮겨졌고, `crawl_runs`는 **신 instance에서만 단조 증가**한다(같은 시점 신 24798 / 구 24769) — 공용 instance가 유일한 writer라는 증거는 증가분의 크기가 아니라 증가가 한쪽에서만 일어난다는 사실이다.

**위 두 행(배포 트리 / cron 트리)의 분리가 지금 prod에서 실제 피해로 실현돼 있다.** 매일 03:30 concierge 백업은 쓰기가 끊긴 롤백 사본(`:12600`)을 뜨고 있다. 이 문서 §6.4·§7.5가 통째로 다루는 위험이 바로 그것이다 — "코드는 최신이다"만 읽고 넘어가지 마라.

### 1.2 아직 아닌 것 (계획이지 현황이 아니다)

| 항목 | 현재 |
|---|---|
| 공용 Dagster 스토리지 `dagster_shared` | **없다.** compose에도 live에도 없다 |
| 공용 Dagster webserver(`11002`) / daemon(`11001`) | **없다.** `docker-compose.yml`에 서비스 0건, n150에 컨테이너 0건. (`docs/platform-topology.md` §7(176·181·182·195·203·205·206행)에는 **계획으로** 등장한다 — 저장소 grep은 0건이 아니다) |
| geo / map / pinvi의 공용 instance 이전 | **없다.** 셋 다 ADR-37의 전용 instance 그대로 |
| geo/map/pinvi/weather/transport용 role·database | 정식 경로로 만들어진 것은 **없다** (§10.1 예외 주의) |
| `ktdctl db-backup`의 실제 복원 명령 | **없다.** 백업·리허설 복원만 있다 |
| 옛 instance 폐기 기준 | **아무 문서에도 없다** |

`docs/platform-topology.md` §7이 그린 5단계 전환 계획에서 **애플리케이션 DB 이사는 마지막 5단계**인데, concierge는 그 5단계만 먼저 실행된 예외다(데이터가 작고, 상태 저장 daemon이 없고, blast radius가 최소라는 이유). 1~4단계(code-server 분리 → 공유 Dagster 스토리지 → 공용 workspace/webserver/daemon → 프로젝트별 webserver/daemon 철거)는 아직 진행 중이거나 시작 전이다.

> ⚠️ **낡은 문서 경고.** 다른 저장소가 읽을 만한 Manager 문서 여럿이 아직 cutover 전을 적고 있다.
> - **`AGENTS.md`(74·151·156행)** — 다른 저장소 세션이 Manager를 처음 들여다볼 때 가장 먼저 읽는 파일이라 우선순위가 가장 높다. 74행 DB 서비스 정보 표가 `:12600`을 "ADR-44 cutover 완료 전까지는 이 instance가 활성", `:11000`을 "cutover 완료 후 활성"이라 적고, 룰 4(151행)·룰 9(156행)도 이전을 미완으로 서술한다.
> - `platform-topology.md` §7 도입부는 "결정된 목표이고 아직 만들어지지 않았다"로 시작하고 §4 instance 표에 `11000` 행이 없다.
> - `ports.md`(101행)는 `11000`을 "cutover 완료 후 활성"이라는 미래형으로 적는다.
> - `docker-management.md`(95행)는 "registry가 관리하는 컨테이너 21개"라고 하지만 실제 `containers:`는 36개다.
> - `architecture.md`(9행)는 "전용 PostgreSQL 4개"로 남아 있다.
>
> **현황 정본은 `docker-compose.yml`·`config/docker-targets.yml`과 이 문서다.**

---

## 2. 경계: Manager가 하는 일 / 네 저장소가 하는 일

공용 instance는 **Manager 소유**다. 네 저장소가 자기 compose에 아무리 잘 써 놔도 prod에서는 읽히지 않는다.

| 일 | 소유자 | 왜 |
|---|---|---|
| PostgreSQL instance 자체(기동·인증 자세·pgdata·튜닝) | **Manager** | `docker-compose.yml`의 `kor-travel-shared-postgres` 서비스 하나가 정본 |
| 네 프로젝트의 role 생성·비밀번호 동기화 | **Manager** | 프로젝트 전용 `kor-travel-shared-db-init-<project>` one-shot |
| 네 프로젝트의 database 생성(owner=네 app role) | **Manager** | `createdb -O <app_user>` — one-shot이 superuser로 |
| **확장 설치**(`CREATE EXTENSION`) | **Manager** | 네 app role은 `NOSUPERUSER`라 스스로 못 한다. §4.3 |
| CONNECT ACL(PUBLIC revoke + 네 role만 grant) | **Manager** | §5.3 |
| **Manager 변경의 prod 반영**(소스 전달 + `ktdctl ensure <target>`) | **Manager 운영자**(n150 접근 권한자) | PR 머지만으로는 n150에 반영되지 않는다. `docs/prod-deployment.md` §2(rsync 또는 trusted installer)로 소스를 옮긴 뒤 prod 호스트에서 `ensure`를 돌려야 db-init one-shot이 실제로 실행된다 |
| 백업(pg_dump) | **Manager**(프로젝트에 따라 다르다) | role 등록은 Manager 저장소 PR이 필요하고, **geo application DB만은 Manager가 아니라 프로젝트 자체 스케줄 백업이 정본**이다. §6.4 |
| **실제 데이터 복원/cutover 실행** | **너** (사람이 손으로) | Manager에 파괴적 복원 명령이 없다. §7.0 |
| Alembic 마이그레이션 작성·실행 | **너** | 단, 확장 생성은 빼야 한다 |
| 앱의 DSN 배선(env 변수 이름·기본값) | **너 + Manager** | 변수 이름은 네가 정하고(단 §6.6 — 이미 배포된 이름은 바꾸지 않는다), compose 자리는 Manager가 만든다 |
| 옛 instance 폐기 시점 | **너** | 아무도 안 정해 뒀다. 네가 종료 조건을 정의해야 한다 |

내부 target(`geo`/`conc`/`map`/`pinvi`)은 Manager가 `ensure`로 배포하고, 외부 target(`weather`/`airport`)은 상태 조회·수명주기만 한다. 그러나 **공용 instance는 어느 쪽이든 Manager 소유**이므로, weather 같은 외부 프로젝트도 role/database를 얻으려면 db-init one-shot이 **Manager compose에** 들어가야 한다.

---

## 3. 먼저 할 수 있는 일 — Manager PR을 기다리지 않고 지금 끝내는 것

이 절이 이 문서의 핵심이다. 아래는 전부 네 저장소 안에서 독립적으로 끝난다.

| # | 할 일 | 판정 기준(끝났다고 말할 수 있는 조건) |
|---|---|---|
| P1 | **DSN을 env 변수 하나로 파라미터화** | 코드·compose·테스트 어디에도 host/port/db가 하드코딩돼 있지 않다. `grep`으로 `12500`/`12600`/`12700`/`12800`/`5432`가 0건 |
| P2 | **DSN fallback 기본값 감사** | 기본값이 **옛 instance를 가리키지 않는다**. §3.1 |
| P3 | **Alembic head 고정** | `SELECT version_num FROM alembic_version`을 적어 두고, 미적용 리비전이 0. cutover 창에서는 새 마이그레이션을 머지하지 않는다 |
| P4 | **확장 목록 확정** | `SELECT extname, extversion FROM pg_extension`의 결과를 Manager에 제출할 목록으로 확정. 마이그레이션 코드에서 `CREATE EXTENSION`을 **전부 제거** |
| P5 | **특권 요구 감사** | 마이그레이션·런타임 코드에 `CREATE EXTENSION` / `CREATE ROLE` / `CREATE DATABASE` / `ALTER SYSTEM` / `pg_read_file` 등 superuser 동작이 0건 |
| P6 | **소유권 가정 점검** | `SELECT tableowner, count(*) FROM pg_tables WHERE schemaname='public' GROUP BY 1`이 앱 role 하나로 수렴하는지 확인. 여러 role이 섞여 있거나 소유 role이 superuser면 cutover 전에 정리 — **이것이 §7.3 role 리맵의 입력이다** |
| P7 | **데이터 규모·덤프 시간 실측** | `pg_database_size`와 `pg_dump -Fc --compress=6` 실측 초. §3.2 |
| P8 | **DB 목록 분류** | 네 instance의 database를 **앱 데이터 / bootstrap / 잔해**로 갈라 목록화. 이전 대상은 앱 데이터만 |
| P9 | **advisory lock 사용처 열거** | `git grep -n pg_advisory` 결과를 전수 목록으로. 세션 수준(`pg_advisory_lock`)과 트랜잭션 수준(`pg_advisory_xact_lock`)을 **구분**해서. §3.3 |
| P10 | **Dagster 메타DB 위치 구분** | 네 프로젝트의 Dagster 메타DB가 §7 계획의 2단계(공용 Dagster 스토리지)인지, 앱 DB와 함께 5단계인지 명시. **weather는 시작 전에 §10.1을 먼저 읽어라 — 네 Dagster 메타DB는 이미 두 군데에 있다** |
| P11 | **연결 풀 크기 재산정** | 분모는 `max_connections=100`(cluster 전역, 2026-09-20 기준 16 사용)이다. 판정 기준은 "**네 풀 크기 × 프로세스 수가 남은 연결 예산 안에 드는가**". 넘으면 compose 편집이 필요하고 그것은 §6.1 C9의 비용(공용 instance 재기동 = 기존 테넌트 다운타임)을 부른다 |
| P12 | **옛 instance 폐기 종료 조건 정의** | "N일 무사고 + 백업 M세대 확보 후 폐기" 같은 문장을 네 저장소 문서에 박아 둔다. ⚠️ **"N일 무사고"를 셀 때 `docker ps`의 `Up N weeks`를 쓰지 마라 — 그것은 컨테이너 가동 시간이지 cutover 이후 경과 시간이 아니다.** 기준 시각은 네 cutover 기록의 날짜다 |
| P13 | **row count·sequence 검증 쿼리 준비** | 주요 테이블의 `SELECT count(*)`와 `pg_sequences`의 `last_value` 목록을 스크립트로. **`n_live_tup`는 쓰지 않는다**. §7.4 |

### 3.1 P2가 가장 위험한 한 줄이다

concierge 선례에서 compose는 세 자리(api·mcp·scheduler)에 이런 모양을 갖는다:

```
DATABASE_URL: ${KOR_TRAVEL_CONCIERGE_DOCKER_DATABASE_URL:-<옛 instance를 가리키는 기본값>}
```

그리고 **옛 instance는 롤백 안전망으로 계속 healthy하게 떠 있다.** 즉 호스트 `.env`에서 그 변수가 빠지거나 이름이 바뀌면, 스택은 오류 없이 기동해서 **조용히 폐기된 DB에 쓰기 시작한다.** 이 DSN을 검증하는 테스트·validator는 Manager 저장소에 0건이다.

네 프로젝트가 같은 패턴을 복제한다면 셋 중 하나를 골라라.

- **(a) fallback 기본값을 두지 않고 변수 부재 시 기동을 실패시킨다(권장).** 실물 예가 이미 저장소에 있다 — geo가 `KTG_PG_DSN: ${KOR_TRAVEL_GEO_DOCKER_PG_DSN:?KOR_TRAVEL_GEO_DOCKER_PG_DSN must be explicitly set}` 형태다(`docker-compose.yml:535` 외 3자리). `:?` 한 글자가 "부재 = 기동 실패"를 만든다.
- (b) fallback을 공용 instance로 둔다.
- (c) 앱 기동 시 실제 접속 대상의 host/port를 로그에 남기고, 기대값과 다르면 죽는다.

**옛 instance를 기본값으로 두는 것만은 하지 마라** — 부재가 "안전한 기본"이 아니라 "폐기된 DB 선택"이 되는 형태다.

### 3.2 P7 — 다른 프로젝트의 실측 규모 (자기 비용 추정의 기준선)

2026-09-20 `pg_database_size` 실측.

| instance | database | 크기 | 분류/참고 |
|---|---|---|---|
| shared `:11000` | `kor_travel_concierge` | 81 MB | 앱 데이터 — cutover 완료 |
| concierge `:12600` (옛) | `kor_travel_concierge` 83 MB / `ktc_bootstrap` 19 MB / `postgres` 7.2 MB / `p2_proof_ktc` 7.2 MB | — | **한 행에 세 분류가 다 있다**: 앱 데이터(첫째) / bootstrap(`ktc_bootstrap`·`postgres`) / 잔해(`p2_proof_ktc`). P8의 연습 예제로 쓰라 |
| geo `:12500` | `kor_travel_geo` **32 GB** / `kor_travel_geo_dagster` 92 MB | — | 자릿수가 다르다 |
| map `:12700` | `kor_travel_map` 26 MB / `kor_travel_map_dagster` **7.2 MB** / 잔해 4종(`ktm_40b`·`ktm_bootstrap`·`ktm_gcverify`·`ktm_gcverify_dagster` 9.0 MB) | — | ⚠️ 9.0 MB짜리는 `ktm_map_dagster`가 아니라 잔해 `ktm_gcverify_dagster`다 — 이 둘을 뒤바꾸기 쉽다 |
| pinvi `:12800` | `pinvi` **7.2 MB** / `pinvi_bootstrap` 7.4 MB | — | `pinvi_dagster`는 **존재하지 않는다**(문서와 불일치) |

덤프 시간 실측: concierge는 `pg_dump -Fc --compress=6`이 **2초**, geo는 2026-08-17 기준 **4.4 GB / 879초**였다. concierge가 "가장 쉬운 사례"였고, geo의 hard cutover 다운타임은 덤프+복원+검증으로 **시간 단위**로 잡아야 한다.

> ⚠️ **불확실:** ADR-44는 concierge 데이터를 "실측 231 MB"로 적지만 실제 database 크기는 81~83 MB다. 231 MB는 database가 아니라 instance(PGDATA) 전체 또는 부수 DB 합산으로 보인다. **네 다운타임을 추정할 때 database 크기와 instance 크기를 반드시 구분하라.**

### 3.3 P9 — advisory lock이 hard cutover를 강제한다

PostgreSQL advisory lock은 **database 단위로 스코프**된다. 같은 키라도 다른 database면 서로 충돌하지 않는다. 신·구 DB에 writer가 동시에 붙는 점진 전환을 하면, 두 writer가 "같은" 락을 각자 다른 database에서 잡아 **상호배제가 조용히 0이 된다.** 그래서 ADR-44는 점진 전환을 금지하고 완전 정지 후 일괄 전환(hard cutover)만 허용한다.

이 성질은 §7.1 1단계(동결)가 왜 "정지 명령을 한 번 돌리는 일"이 아니라 **창 전체를 지키는 일**인지도 설명한다 — 창 도중 옛 DB에 writer가 하나라도 되살아나면 advisory lock은 그것을 막지 못하고, 두 정본이 조용히 생긴다.

네가 할 일은 전수 열거다. `pg_advisory_xact_lock`(트랜잭션 종료 시 자동 해제)과 `pg_advisory_lock`(세션 수준, 자동 해제 안 됨)을 구분해서 적어라 — 세션 수준 락은 정지 절차에서 별도로 확인할 대상이다.

> ⚠️ **불확실:** ADR-44 본문의 "5곳"은 파일 수로 보인다. concierge 저장소 실측 `git grep -n pg_advisory`는 `pg_advisory_xact_lock` 호출부 6개와 세션 수준 `pg_advisory_lock` 1개를 보여준다. 숫자보다 **네 저장소에서 직접 센 목록**을 신뢰하라.

---

## 4. 접속 계약 — 합류 후 네 앱이 보게 될 것

### 4.1 좌표와 인증

| 축 | 값 | 비고 |
|---|---|---|
| host | `127.0.0.1` | loopback 전용. 원격 접속 경로 없음 |
| port | `11000` | **네 프로젝트는 새 포트를 받지 않는다.** 12000대 target별 100단위 대역과 별개이며, 합류 프로젝트가 늘어도 이 포트는 바뀌지 않는다 |
| database | `kor_travel_<project>` (기본값) | compose 변수로 override 가능 |
| user | `kor_travel_<project>_app` (기본값) | |
| 인증 | scram-sha-256 | **앱 접속 경로**는 TCP뿐이고 TCP는 예외 없이 scram이다 |
| 연결 예산 | `max_connections=100` (cluster 전역, 2026-09-20 03:2x UTC 기준 16 사용) | 네 풀 크기는 **남은 예산 안**에 들어야 한다. 상한을 올리려면 compose `command:`에 줄을 추가해야 하고 §6.1 C9의 비용이 따른다 |
| DSN 모양 | `postgresql+asyncpg://<app_role>:<password>@127.0.0.1:11000/<database>` | **값은 문서·저장소에 절대 쓰지 않는다.** 호스트 `.env`(root 0600)에만 |

컨테이너 안에서 `docker exec --user postgres ... psql -U shared_admin`이 비밀번호 없이 붙는 것은 unix socket이 `trust`이기 때문이다. Manager의 백업 모듈도 바로 그 경로를 쓴다("어떤 postgres 비밀번호도 읽거나 다루지 않는다"). **운영 작업(§7의 덤프·복원)은 그 소켓 경로를 쓰고, 네 앱은 그 경로를 쓰지 않는다** — 앱에게는 TCP + scram만이 접속 경로다.

### 4.1-bis 네 프로젝트가 "내부"인가 "외부"인가로 접속 경로가 갈린다

geo/concierge/map/pinvi처럼 **Manager 자신의 `docker-compose.yml` 안에서** 뜨는
프로젝트("내부 target")는 host networking을 공유하므로 위 표의 `127.0.0.1:11000`을
그대로 쓴다 — 코드 변경이 필요 없다.

weather/transport처럼 **자기 저장소의 독립된 compose로** 뜨는 프로젝트("외부
target")는 별도 docker bridge network를 쓰므로 `127.0.0.1`로 이 instance에
닿지 못한다(2026-09-20 weather 실측 — `host.docker.internal` 경유 시도가
`listen_addresses`가 그 인터페이스를 듣지 않아 `ConnectionRefusedError`).
**전용 브리지 `kor-travel-shared-net`에 join해 서비스명 DNS로 접속한다**:

```
postgresql+asyncpg://<app_role>:<password>@kor-travel-shared-postgres:11000/<database>
```

합류하려면 (1) 네 compose의 해당 서비스에 `networks: [default, kor-travel-shared-net]`를
추가하고(`default`를 빼먹으면 프로젝트 내부 서비스 간 접속이 조용히 깨진다),
(2) `kor-travel-shared-net`은 Manager가 `docker network create`로 미리 만들어
둔 **external** 네트워크이므로 네 compose에도 `networks: { kor-travel-shared-net:
{ external: true } }` 선언이 필요하다. 이름만 알면 누구나 join할 수 있으므로,
합류 전에 이 문서(§9)에 프로젝트를 적어 두는 것을 관례로 한다 — 2026-09-20
기준 join 허용: weather(dagster-code-server/webserver/daemon 3개뿐, 그 프로젝트의
api/web 등 나머지 서비스는 join하지 않는다).

**이 브리지를 열기 위해 `kor-travel-shared-postgres` 쪽이 바뀐 것**(2026-09-20,
n150 실측): `network_mode: host`에서 `networks: [kor-travel-shared-net]`로,
`listen_addresses`는 `127.0.0.1`에서 `0.0.0.0`으로. **처음엔 `127.0.0.1,10.88.0.1`
(loopback + 브리지 게이트웨이 IP 명시)을 시도했다가 되돌렸다** — `10.88.0.1`은
컨테이너 자신이 아니라 브리지 인프라가 소유한 주소라 postgres가 bind()하지
못했고(`could not bind IPv4 address "10.88.0.1": Cannot assign requested
address`, WARNING이라 healthcheck는 계속 green이었다), host-mode 소비자(예:
concierge, 실 데이터 보유)가 쓰는 published-port 경로가 몇 분간 끊겼다. `0.0.0.0`이
맞는 값이다 — 이 서비스는 `network_mode: host`가 아니라 `networks:`(브리지
전용)이므로 `0.0.0.0`은 **컨테이너 자신의 네임스페이스 안**(loopback + 이
컨테이너의 브리지 IP)으로만 스코프되고, 실 LAN(`wlp2s0`, `192.168.1.0/24`)은
그 네임스페이스 밖이라 애초에 보이지 않는다. `0.0.0.0`은 **kor-travel-shared-postgres
하나만** 허용된다 — map/geo/concierge/pinvi 자체 전용 인스턴스는 여전히
`127.0.0.1` 단독만 허용한다(서비스 이름으로 가른다, 균일 허용이 아니다 —
기존 회귀 테스트가 `0.0.0.0`을 그 서비스들의 "반드시 거부돼야 하는" 정본
넓힘 사례로 쓰고 있었다). Manager의 compose 계약(`backend/tests/test_f1d_compose_contract.py`,
`c6c_deployment.py`의 `_POSTGRES_SHARED_POSTGRES_CANONICAL_LISTEN_VALUES`/
`_postgres_networks_value_is_canonical`)이 이 형태(서비스별 허용 값, `networks`
키 값 둘 다, 부분 일치 불허)를 CI에서 강제한다.

### 4.2 네 app role의 권한

db-init이 만드는 role은 정확히 이렇다:

```
ALTER/CREATE ROLE <app_user> WITH LOGIN PASSWORD '<secret>' NOSUPERUSER NOCREATEDB NOCREATEROLE
```

| 할 수 있는 것 | 할 수 없는 것 |
|---|---|
| 자기 database에 접속 | 다른 프로젝트 database에 접속(§5.3) |
| `public` 스키마에 테이블·인덱스·시퀀스 생성 | `CREATE EXTENSION` (postgis 포함) |
| 자기 테이블 소유·DDL | `CREATE DATABASE` / `CREATE ROLE` |
| 자기 database 소유(= `createdb -O`) | 확장 `DROP`/`ALTER` |
| | `ALTER SYSTEM`, 서버 파일 접근 |

`public` 스키마에 테이블을 만들 수 있는 이유는 PG15+ 기본에서 `public`의 owner가 `pg_database_owner`이고, database owner가 네 app role이기 때문이다. concierge 실측에서 `public` 테이블 28개 중 27개가 app role 소유이고 `spatial_ref_sys` 하나만 `shared_admin` 소유다(확장이 만든 것).

**"자기 테이블 소유·DDL"은 공짜로 성립하지 않는다.** 복원이 객체를 `shared_admin` 소유로 남기면 이 칸이 통째로 거짓이 되고, cutover 직후 첫 Alembic 마이그레이션이 권한 오류로 죽는다. 그래서 §7.1에 role 리맵 단계가 따로 있다.

### 4.3 확장은 네 마이그레이션이 만들지 않는다

**이것이 마이그레이션 작성자에게 가장 직접적으로 걸리는 제약이다.** postgis는 trusted extension이 아니므로 `NOSUPERUSER` role이 만들 수 없다. 공용 instance에서 확장은 db-init one-shot이 `shared_admin` 권한으로 미리 만든다(현재 `CREATE EXTENSION IF NOT EXISTS postgis` 1종).

그래서:

- 네 Alembic 마이그레이션에서 `CREATE EXTENSION`을 **제거**한다. 이미 존재해 `IF NOT EXISTS`가 조기 반환할 때만 우연히 통과하므로, 남겨 두면 깨끗한 재구축에서만 터지는 함정이 된다.
- 네가 필요한 확장 목록을 **Manager에 제출**하고, Manager의 네 프로젝트 one-shot에 줄이 추가되는 것을 경로로 삼는다.
- 확장 owner는 `shared_admin`이다. 확장 소유권을 가정하는 코드가 있으면 고친다.

---

## 5. role 분리 규칙 — 재사용 금지, 그 이유

### 5.1 규칙 (예외 없음)

1. **cluster 관리 계정(`shared_admin`)은 앱에 절대 주입하지 않는다.** db-init one-shot만 쓴다.
2. **프로젝트마다 전용 role**을 만든다. `NOSUPERUSER / NOCREATEDB / NOCREATEROLE`, 자기 database **하나만** 소유.
3. **프로젝트마다 자기 db-init one-shot 하나**를 추가한다. 기존 role을 재사용하지도, 남의 db-init에 자기 SQL을 얹지도 않는다.

### 5.2 왜 — 2026-08-17 사고

ADR-37이 기록한 사고의 **직접 원인**은 `scripts/ensure-kor-travel-geo-db.sh` 하나였다. 그 스크립트는 한 cluster 안에서 `pinvi` role과 `pinvi`·`kor_travel_concierge`·`krtour_map` database를 만들고 owner와 광범위한 grant를 재적용했다. 결과적으로 통합 instance는 "database만 나눠 쓰는" 형태가 아니라 **모든 프로젝트가 서로의 principal namespace를 공유하는** 형태였고, Map을 전용 instance로 뺀 뒤에도 통합 instance에 `ktm_` role 7개가 남아 Map migrator credential로 33 GB `kor_travel_geo`에 실제로 접속됐다(`CONNECTED as ktm_feature_migrator`).

더 나쁜 것은 재발 구조였다. 그 스크립트는 **복구 실행 때마다 그 구조를 조용히 되살릴 수 있었다** — ADR-37의 표현으로 "그대로 두면 복구 실행이 이 ADR을 되돌린다". 그래서 그대로 두지 않았다: 오늘 `scripts/ensure-kor-travel-geo-db.sh` 129행이 "ADR-37 — pinvi/concierge role·database는 여기서 만들지 않는다"이고 다중 프로젝트 프로비저닝은 잘려 나가 geo 전용이 됐다. 금지의 이유는 미관이 아니다. **다중-프로젝트 프로비저닝 스크립트는 격리를 되돌리는 재실행 가능한 기계**가 되기 때문이고, 그 논지는 스크립트를 고친 지금도 그대로다.

그리고 그 계열의 SQL 원본 **`scripts/init-kor-travel-geo.sql`은 아직 저장소에 추적된 채 남아 있다** — `CREATE DATABASE pinvi; CREATE DATABASE krtour_map; CREATE DATABASE kor_travel_concierge; CREATE USER pinvi WITH PASSWORD ...`를 평문 비밀번호와 함께 담고 있고, 지금은 어디에도 마운트되지 않는다. 파일 이름이 `geo`를 달고 있어 geo 세션에게 재사용 유혹이 가장 큰 자리다. **어떤 instance의 `initdb.d`에도 다시 걸지 마라.**

교훈 한 줄: **role·ACL·확장은 database가 아니라 cluster 전역이다.**

### 5.3 CONNECT — database를 나누는 것만으로는 격리가 안 된다

PostgreSQL은 기본적으로 **모든 database에 PUBLIC CONNECT를 부여**한다. 공용 instance 최초 배포 직후 실측(2026-09-19)에서 `kor_travel_concierge_app`이 bootstrap `postgres` database에 접속해 `SELECT 1`까지 실행할 수 있었다. `createdb`로 만드는 새 database의 `datacl`은 NULL이고, 그것은 PUBLIC에 CONNECT+TEMP를 준다는 뜻이다.

그래서 db-init 끝에 세 줄이 있다:

```
REVOKE CONNECT ON DATABASE <bootstrap DB> FROM PUBLIC;
REVOKE CONNECT ON DATABASE <app DB>       FROM PUBLIC;
GRANT  CONNECT ON DATABASE <app DB>       TO <app_user>;
```

**네 프로젝트의 db-init은 이 세 줄을 반드시 복사해야 한다.** compose 주석은 "아래 `-concierge` 접미사와 같은 패턴"이라고만 안내하므로, 초기 형태(role + createdb + extension)만 베끼면 **기존 concierge role이 네 새 database에 바로 붙는다.** 이것이 "다른 프로젝트가 합류해도 서로의 database에 기본 CONNECT가 새지 않는다"는 약속이 실제로 걸려 있는 지점이다.

현재 격리는 live에서 성립한다 — CONNECT 매트릭스 실측에서 `kor_travel_concierge_app`은 자기 DB만 `t`, bootstrap `postgres`는 `f`다.

> ⚠️ **남아 있는 구멍:** `template1`과 `template_postgis`는 여전히 기본 ACL이라 **모든 테넌트 role이 CONNECT 가능**하다(`template_postgis`는 TEMP까지). 읽을 내용이 확장 카탈로그뿐이라 데이터 유출 경로는 아니지만, "격리가 끝났다"고 적으면 틀린다. instance 전체의 CONNECT 자세를 검사하는 축은 아직 없다 — 네 db-init이 자기 DB만 보는 것은 옳고, cluster 전역 자세는 Manager가 별도로 소유해야 할 미해결 항목이다.

---

## 6. Manager 쪽 등록 체크리스트 — 빠뜨리면 나는 증상

네가 Manager에 PR을 내거나, Manager 담당자에게 요청할 때 쓰는 표다. **compose와 `docker-targets.yml`은 같은 커밋에서 움직여야 한다** — 정적 검사가 둘을 교차 대조한다.

### 6.1 `docker-compose.yml`

| # | 항목 | 빠뜨리면 나는 증상 |
|---|---|---|
| C1 | `kor-travel-shared-db-init-<project>` one-shot 추가 (image `postgis/postgis:16-3.5`, `restart: "no"`, `depends_on: kor-travel-shared-postgres: service_healthy`, `PGHOST=127.0.0.1 / PGPORT=11000 / PGUSER=shared_admin / PGDATABASE=<bootstrap DB>`) | role·database가 아예 안 생긴다. 또는 prod에 손으로 만들면 §10.1의 "흔적" 상태가 된다. ℹ️ 공용 postgres 서비스 블록을 복제한다면 **`ports:` 매핑은 빼도 된다 — host networking에서 Docker가 무시한다**(실제 경계는 `-p`와 `listen_addresses`가 정한다) |
| C2 | one-shot 스크립트에 **REVOKE/GRANT 3줄 포함** | 다른 프로젝트 role이 네 database에 기본 CONNECT로 붙는다(§5.3) |
| C3 | `|| true`로 오류를 삼키지 않는다 | 인증 실패·권한 부족을 삼키면 `service_completed_successfully` 게이트가 **막으려던 상황에서 오히려 통과**한다 |
| C4 | 앱 서비스 `depends_on`에 `kor-travel-shared-postgres: service_healthy` + `kor-travel-shared-db-init-<project>: service_completed_successfully` | DB 준비 전에 앱이 떠서 기동 레이스가 난다 |
| C5 | 최상위 `secrets:`에 `<project-kebab>-shared-app-password` (provider는 `environment:`) | compose interpolate 실패 |
| C6 | secret은 **반드시 env provider**. `file:` 금지 | 후보/resolved 양쪽 계약이 명시적으로 거부 → 배포 차단 |
| C7 | 앱 컨테이너에는 secret을 **마운트하지 않는다**(비밀번호는 DSN env로만) | 선례와 어긋나고, 비밀번호 보관처가 하나 더 늘어난다 |
| C8 | override 파일(`docker-compose.override.yml`)로 시도하지 않는다 | 그 파일이 **존재하는 것만으로** deployment readiness가 `missing`으로 떨어져 승인된 재구축 전체가 막힌다 |
| C9 | **튜닝·연결 상한 변경은 합류 PR과 분리한다** | `KOR_TRAVEL_SHARED_POSTGRES_*`와 `max_connections`는 **cluster 전역 단일값**이라 테넌트별로 나눌 수 없고, 반영하려면 **공용 postgres 재기동 = 이미 live인 기존 테넌트(현재 concierge)의 다운타임**이 따른다. 별개 배포 창에서, 기존 테넌트에 공지하고 한다. ⚠️ 공용 서비스에는 `shm_size` 설정이 **없다**(기본 `/dev/shm` 64MB) — 큰 DB의 병렬 질의가 `could not resize shared memory segment`로 죽는 자리이고, geo 전용 instance는 같은 자리에 `shm_size: 512mb`를 두고 있다 |

### 6.2 `config/docker-targets.yml`

| # | 항목 | 빠뜨리면 나는 증상 |
|---|---|---|
| T1 | 네 target의 `services:`에 `kor-travel-shared-postgres` + `kor-travel-shared-db-init-<project>` **직접** 추가 | `ensure <target>`이 **아무도 안 띄운 `:11000`**을 향해 앱을 올린다. ⚠️ 특히 `geo`는 `depends_on: [prom]`이라 폐포에 `conc`가 없다 — map/pinvi는 폐포에 conc가 있어 우연히 가려진다 |
| T2 | 네 target(과 폐포 하류 target, `all` 포함)의 `containers:`에 `kor-travel-shared-postgresql` 추가 | `ktdctl status`·대시보드·Prometheus에서 네 DB가 **아예 보이지 않는다**(조용한 실패 — 배포는 성공한다) |
| T3 | `containers:`에 넣었으면 `services:`에도 있어야 한다 | status가 `ensure`가 띄우지도 않는 컨테이너 건강을 따져 target이 **영구 degraded** |
| T4 | 공용 container 항목의 `connection` 문자열에 네 database 이름 추가 | 표시상 누락(기능 영향 없음) |
| T5 | db-init one-shot은 `containers:`·`runtime_services:`에 **넣지 않는다** | one-shot의 정상 상태는 `exited(0)`이므로 대시보드에 상시 비정상 카드로 남고, `runtime_services:`에 넣으면 status가 항상 실패 |
| T6 | 새 host bind를 들고 온다면 `compose_binds:` allowlist에 등재. **자료구조는 `compose 서비스 이름 → {container_path, read_only, source} 목록`이다.** `source`에는 compose 원문을 그대로(`${VAR:-default}` 포함) 옮겨 적는다 — 전개된 값을 적으면 대조가 어긋난다 | `compose candidate <svc> bind is not in the canonical baseline`으로 **배포 전체 거부**(GM-17 보안 경계). 공용 instance pgdata는 이미 등재돼 있으니 bind를 안 쓰면 이 절은 대개 건드릴 일이 없다 — **단, T7처럼 `init_steps`가 DB 컨테이너 안에서 파일을 읽는 경우는 예외다** |
| T7 | **`init_steps` 재지정.** target이 `init_steps`를 선언하면 그 `exec` 대상 컨테이너가 cutover 후에도 옳은 instance를 가리키는지 **같은 커밋에서** 고친다 | 빠뜨리면 (i) 옛 DB를 검증해 **거짓 초록**을 내거나, (ii) 옛 instance를 내리는 순간 `ensure <target>`이 **통째로 실패**한다. 실물: `geo` target이 `init_steps: geo-source-verification`을 선언하고 `ensure geo`가 `up -d` 뒤 `compose exec -T kor-travel-geo-postgres sh /opt/.../verify-kor-travel-geo-source.sh`를 돌린다 |
| T8 | 새 PostgreSQL 서버를 세우는 경우 `role:`이 `-postgresql`로 끝나야 한다 | `undeclared PostgreSQL server`로 거부. `role: db`로 위장해도 witnessed 축이 잡는다. (공용 instance에 합류만 한다면 **새 컨테이너를 만들지 않으므로** 해당 없음) |

### 6.3 코드·테스트·문서

| # | 항목 | 빠뜨리면 나는 증상 |
|---|---|---|
| X1 | `.env.example`에 새 secret env 이름(빈 placeholder) + 필요 시 DSN override 예시 | §6.5 참조 — 값이 호스트 `.env`에 없으면 **모든 target의 compose 명령이 죽는다** |
| X2 | `c6c_deployment.py`의 `_CANDIDATE_NAMEABLE_SERVICE_NAMES`에 새 서비스 이름 추가 | 계약 위반 시 거부 문구가 서비스 이름을 `<unrecognized service key sha256:xxxxxxxx>`로 **가린다** — 운영자가 어느 서비스가 거부됐는지 모른다. (`kor-travel-shared-postgres`도 현재 그 목록에 없다) |
| X3 | `test_api.py`의 target 응답 서비스 목록 | CI 빨강 |
| X4 | `test_docker_manager_cli.py` 2곳(`..._resolves_application_targets_to_shared_services`, `..._compose_ensure_build_command`) | CI 빨강 |
| X5 | (새 postgres를 세운 경우만) `test_f1d_compose_contract.py`의 declared 집합 리터럴 | CI 빨강 — **의도된 마찰**(하드코딩으로 굳는 것을 막는 검사) |
| X6 | `docs/ports.md` instance 표에 네 database 추가 + 네 target 행의 옛 instance 기재 정정 | 다른 저장소가 틀린 현황을 읽는다 |
| X7 | ADR 추가 + `AGENTS.md`(74·151·156행)/`platform-topology.md`/`docker-management.md`/`architecture.md` 정정 + `bindings.md` 등록 | 현재 이미 drift가 쌓여 있다(§1.2 경고). 같은 실수를 늘리지 않는다 |

`test_registry_targets_config.py`가 `containers[*].compose_service`와 `targets[*].services/runtime_services`를 실제 compose 서비스 집합과 대조하고(`test_real_config_only_names_services_that_exist_in_docker_compose`), `compose_binds`에도 같은 검사를 한다. **compose와 targets를 같은 커밋에서 움직이지 않으면 여기서 빨개진다** — 안 잡히면 `status`/`ensure`가 런타임에 `no such service`로 죽는다.

### 6.4 백업 (cutover **후** 별도 커밋)

**먼저 알아야 할 것: 프로젝트마다 cutover 직전 백업의 주인이 다르다.** "Manager cron이 받쳐 준다"고 가정한 채 창을 열면 안 된다.

| 프로젝트/role | 일상 백업 주인 | 근거 |
|---|---|---|
| `concierge` · `pinvi` · `geo_dagster` | **Manager cron** (`scripts/run-standalone-backup.sh`) | wrapper의 `case "$ROLE" in geo_dagster|concierge|pinvi)` 허용 목록 |
| **`geo` (application DB, 32 GB)** | **프로젝트 자체 스케줄 백업** — Manager가 아니다 | wrapper가 `geo`를 **명시적으로 거부**한다(`exit 2`). 헤더: "geo application DB role은 kor-travel-geo 앱 레벨 스케줄 백업이 정본이므로 cron에 넣지 않는다". compose도 geo 앱이 자체 `db_backup`을 돌린다고 적는다(33 GB DB에 아카이브 약 4.7 GB) |
| `map_application` · `map_dagster` | cron 대상 아님 (#148 정책과 중복) | 같은 wrapper 헤더 |

일상 백업 role은 설정 파일이 아니라 **코드에 박힌 고정 집합**이다(`geo` / `geo_dagster` / `concierge` / `map_application` / `map_dagster` / `pinvi`). 네 프로젝트 백업을 붙이려면 Manager 저장소 PR로 다음을 함께 고친다:

| 자리 | 파일 | 빠뜨리면 |
|---|---|---|
| `BackupRole` Literal | `backend/src/.../services/standalone_backup.py` | 타입 거부 |
| `BACKUP_ROLES` tuple | 같은 파일 | CLI `choices`에 안 보인다 |
| `_ROLE_CONFIG` dict | 같은 파일 | 대상 컨테이너·DB를 모른다 |
| cron allowlist `case "$ROLE" in ...)` | `scripts/run-standalone-backup.sh` | **role은 UI/CLI에 보이는데 주기 백업이 영원히 안 돈다**(`exit 2`, 조용함) |
| **n150 `digitie` crontab이 가리키는 체크아웃 자체** | 호스트 | **role 추가만으로는 부족하다 — crontab이 도는 트리를 동기화해야 한다.** 현재 crontab은 `/home/digitie/kor-travel-docker-manager`(배포 트리 `/opt/...`와 **다른 사본**)를 실행하고, 그 사본은 #363을 못 받아 아직 옛 instance를 겨냥한다(§1.1). 검증 핸들은 §7.5 |

CLI `choices`·API `routes.py`·프론트는 전부 파생이라 자동으로 따라온다.

**타이밍이 계약이다.** `_ROLE_CONFIG`의 컨테이너 참조를 공용 instance로 옮기는 것은 **실제 cutover가 끝난 뒤 별도 커밋**이어야 한다. 미리 옮기면 **cutover 직전 백업**(롤백의 마지막 보루)이 아직 비어 있는 새 instance를 겨냥해 무의미해진다. concierge에서도 준비 PR(#360)과 백업 재지정 PR(#363)이 cutover를 사이에 두고 분리됐다.

참고로 `_ROLE_CONFIG`는 `(컨테이너 env override명, 기본 컨테이너명, database명)` 3튜플만 갖고 **포트와 admin role은 살아 있는 컨테이너에서 동적으로 읽는다**. 그래서 concierge 재지정 때 바뀐 것은 컨테이너 이름 하나뿐이고 `:11000`·`shared_admin`은 자동으로 따라왔다. ⚠️ **단, `geo`/`geo_dagster`는 `container_env=None`이라 env override 경로 자체가 없다** — 재지정이 반드시 코드 변경이고, "바뀐 것은 컨테이너 이름 하나뿐"이라는 concierge의 편의는 geo에 그대로 적용되지 않는다.

> ⚠️ ADR-44가 인용하는 `config/backup-policy.yml`은 **Manager 저장소에 존재하지 않는다**. 백업 정책 정본은 위 세 자리 + cron allowlist + crontab(과 그 crontab이 가리키는 체크아웃)이다.

### 6.5 secret 하나의 폭발 반경

공용 secret 2종(`kor-travel-shared-postgres-password`, `<project>-shared-app-password`)은 `secrets: environment:` 형태다. `docker compose`는 **요청한 서비스와 무관하게 파일 전체를 interpolate**하므로, 값이 호스트 `.env`에 없으면 **무관한 target의 compose 명령까지 전부 죽는다.** `.env.example`이 이 사실을 명시한다.

따라서:

- 네가 합류하며 secret을 하나 추가하면 그 변수는 **이 compose를 쓰는 모든 호스트의 전역 전제**가 된다.
- **값이 호스트 `.env`에 들어가기 전에 compose를 머지하면 안 된다.** 합류 PR과 호스트 `.env` 갱신은 같은 배포 창에서 함께 간다.
- 공용 admin 비밀번호는 **프로젝트마다 새로 만들지 않고 재사용**한다. 네가 새로 추가하는 것은 app 비밀번호 **하나뿐**이다.

### 6.6 이름 규칙 요약

> ⚠️ **이 규칙은 신규 합류 프로젝트에만 적용한다. 이미 배포된 DSN 변수 이름은 바꾸지 않는다** — 개명은 §6.5의 전역 폭발 반경에 그대로 걸려, 호스트 `.env`의 기존 키가 고아가 되는 순간 **무관한 target의 compose 명령까지 함께 죽는다.** (실물: geo는 이미 `KTG_PG_DSN: ${KOR_TRAVEL_GEO_DOCKER_PG_DSN:?...}`으로 떠 있다. 아래 표를 규칙으로 읽고 개명하지 마라.)

| 대상 | 규칙 | concierge 선례 |
|---|---|---|
| one-shot 서비스 | `kor-travel-shared-db-init-<project>` | `kor-travel-shared-db-init-concierge` |
| one-shot container_name | `${KOR_TRAVEL_SHARED_DB_INIT_<PROJECT>_CONTAINER:-<같은 이름>}` | — |
| app secret | `<project-kebab>-shared-app-password` | `kor-travel-concierge-shared-app-password` |
| app secret env | `<PROJECT_UPPER_SNAKE>_SHARED_APP_PASSWORD` | `KOR_TRAVEL_CONCIERGE_SHARED_APP_PASSWORD` |
| database | `kor_travel_<project>` | `kor_travel_concierge` |
| app role | `kor_travel_<project>_app` | `kor_travel_concierge_app` |
| DSN env (**신규만**) | `<PROJECT>_DOCKER_DATABASE_URL` | `KOR_TRAVEL_CONCIERGE_DOCKER_DATABASE_URL` |

### 6.7 `ktdctl targets validate --check-coordinates`를 pre-flight로 믿지 마라

`platform-topology.md` §3/§8이 "선언과 실재를 대조한다"고 안내하지만, 실제 구현은 **`external_project`를 선언한 target의 `working_dir`·`config_files` 존재만** 본다. 포트도, 컨테이너 존재도, database도 보지 않는다. **공용 instance에 합류하는 내부 target에 대해서는 이 명령이 아무것도 검증하지 않는다** — pre-flight로 쓰면 거짓 안심이 된다.

---

## 7. cutover 절차

**유일한 실행 선례는 kor-travel-concierge 저장소 `docs/journal.md`의 2026-09-20 항목**("공용 Postgres 인스턴스(`:11000`)로 실제 데이터 마이그레이션 완료(ADR-44)")이다. 7단계 hard cutover 순서(사전 archival 백업 → 정지 → dump → restore → 검증 → DSN 전환 → 재기동), 실제 `pg_restore` 플래그, owner-only 경고 4건, "테이블 26개 row count 전부 old=new 정확히 일치, sequence 값 전부 일치", `alembic_version=20260901_0029`, `postgis_full_version()` 정상, "신규 instance 7개 연결, 구 instance 0개"가 거기 다 있다. **아래 §7.4의 검증 항목은 그 기록에서 직접 파생됐다.** 창을 열기 전에 그 항목을 먼저 읽어라.

### 7.0 먼저 알아야 할 제약: 복원 도구가 없고, 리허설은 네가 할 복원을 증명하지 않는다

`ktdctl db-backup`은 `create` / `list` / `gc` / `restore-plan` / `rehearse-restore` 다섯 개뿐이다. **실제 role DB를 덮어쓰는 파괴적 복원 명령이 없다** — CLI 도움말이 직접 그렇게 말한다. `restore-plan`은 읽기 전용 판정, `rehearse-restore`는 같은 instance 안의 scratch DB(`ktdm_rehearsal_<epoch>_<random>`)에 복원해 보고 **항상 지운다**.

⚠️ **리허설의 초록은 교차-instance 복원을 증명하지 않는다.** 구현 주석이 `--no-owner --no-privileges`를 일부러 쓰지 않는 이유를 이렇게 적는다 — "scratch DB는 원본과 **같은 인스턴스** 안에 만들므로 dump가 참조하는 role은 전부 실재한다". 즉 리허설은 **role이 실재하는 전제** 위에서만 돈다. cutover가 실제로 하는 일은 role이 실재하지 않는 다른 instance로의 복원이고, 리허설은 그 축을 전혀 검사하지 않는다. **리허설 초록을 복원 단계의 go 신호로 읽으면, 정지·덤프까지 끝낸 다운타임 창 한가운데에서 첫 실패를 만난다.** 그래서 §7.1에 창 **밖에서** 도는 교차-instance 드라이런(0g)을 따로 둔다.

도구가 해 주는 것은 "이 dump가 **같은 instance로** 복원 가능하다"의 증명까지고, **실제 복원 실행과 검증은 사람이 손으로 한다.** concierge cutover의 restore도 수동 `pg_restore`였다.

> 과거에 `ktdctl db-backup restore --confirm`이 구현·머지됐다가 리팩터에서 제거됐다. "writer 정지/재기동 절차 설계가 별도로 필요하다"는 이유로 로드맵 뒤로 미뤄진 것이고, **이미 닫힌 논의**다. "복원 명령을 만들어 주세요"를 전제로 계획을 세우지 마라.

### 7.1 순서와 판정 기준

| 단계 | 하는 일 | 판정 기준 |
|---|---|---|
| **0a** | Manager 합류 PR 머지 | CI green + 머지 완료. **머지만으로는 n150에 아무것도 반영되지 않는다** |
| **0b** | **prod 반영 — 소스 전달** | `docs/prod-deployment.md` §2(rsync 또는 trusted installer)로 배포 트리(`/opt/kor-travel-docker-manager`)에 반영. 주체는 Manager 운영자(§2) |
| **0c** | 호스트 `.env`에 secret 반영 | 새 app 비밀번호 env가 root 0600 `.env`에 존재(값은 어디에도 적지 않는다) |
| **0d** | `ktdctl ensure <target>` | db-init one-shot이 실제로 **실행되는 지점**이다 |
| **0e** | 결과 확인 | `kor-travel-shared-db-init-<project>`가 `Exited (0)` + 네 role/DB가 `pg_roles`/`pg_database`에 존재 + `datdba`가 네 app role |
| **0f** | 직전 백업과 여유 확인 | **직전 백업 1개가 실재하고 그 주인이 확인됨**(§6.4 표로 — geo는 Manager cron이 아니라 자기 앱 백업이다). 그리고 **`df`의 available ≥ (database 크기 + 덤프 예상 크기) × 1.5**. 옛 instance를 지우지 않으므로 이전은 용량을 영구히 추가한다 |
| **0g** | **교차-instance 복원 드라이런 (창 밖에서)** | 최신 덤프를 공용 instance의 scratch DB(`shared_admin`이 `createdb`)에 한 번 복원해 보고 성공하면 **즉시 drop**. 판정 기준은 "**오류 0건 + 소유자 집계가 app role로 수렴**". ⚠️ scratch 이름에 `ktdm_rehearsal_` 접두사를 쓰지 마라 — §7.6의 6시간 자동 drop과 겹친다. 다른 접두사를 쓴다 |
| **1** | **동결** (단순 "정지"가 아니다) | §7.1.1 |
| **2** | **최종 덤프** | §7.1.2 |
| **3** | **소유 role 점검 · 리맵 결정** | §7.1.3 |
| **4** | **복원** | §7.1.4 |
| **5** | **검증 6종** | §7.4 |
| **6** | **DSN 전환** | 호스트 `.env`의 네 DSN 변수 한 줄 교체. 컨테이너 `inspect`로 실제 값의 host:port가 `127.0.0.1:11000`인지 확인(비밀번호는 마스킹해서) |
| **7** | **재기동 + live 검증** | 앱 정상 기동 + 공용 instance `pg_stat_activity`에 네 role 연결이 보임 + **옛 instance에 앱 연결 0** |
| **8** | **writer 유일성 재확인** | 일정 시간 후 §7.4-(6) |
| **9** | (후속 커밋) 백업 재지정 | §6.4 |

#### 7.1.1 — 1단계는 "정지"가 아니라 "동결"이다

정지 명령을 한 번 돌리는 것으로는 부족하다. 정지를 **깨뜨리는 경로가 여럿 있고**, §3.3이 설명하듯 advisory lock은 database 스코프라 되살아난 writer를 막지 못한다 — 조용히 두 정본이 생긴다.

- **(a) 실제 정지.** 앱·스케줄러·워커를 명시적으로 멈춘다(`ktdctl action <container> stop` 등). 앱 컨테이너는 `restart: unless-stopped`라 죽여도 되살아나므로 `stop`이어야 한다.
- **(b) 창 동안 어떤 target에도 `ensure`를 돌리지 않는다.** `ktdctl ensure <target>`은 target의 모든 서비스에 `compose up -d`를 돌리고, 대시보드가 같은 동작을 `POST /targets/{target}/ensure`로 노출한다. **다른 사람이 누른 `ensure` 한 번**이면(또는 네 DB가 폐포에 걸린 **다른 target·`all` 실행** 한 번이면) 옛 DB에 writer가 되살아난다. 대시보드 조작 금지까지 규칙에 포함한다.
- **(c) 창을 여는 사람이 알린다.** 최소한 Manager 운영자와 폐포를 공유하는 다른 프로젝트 담당자에게 창의 시작·종료 시각을 알린다. 알리지 않은 창은 (b)를 강제할 수단이 없다.
- **(d) 판정 기준은 한 번이 아니라 두 번 본다.** 정지 직후와 **4단계 복원 직전**에 각각 옛 instance의 `pg_stat_activity`에서 `backend_type='client backend'`가 **0건**임을 확인한다. 세션 수준 advisory lock 보유 세션도 없어야 한다(P9 목록으로 확인).
- **(e)** 그럼에도 창 중 재기동이 일어났다면 §8.4로 간다.

#### 7.1.2 — 2단계 최종 덤프

`pg_dump --format=custom --compress=6`. 판정 기준:

- 종료 코드 0 + 파일 크기·소요 시간 기록.
- **실측 덤프 시간의 2배 이상으로 timeout을 명시해 실행한다.** 기본은 4시간(`timeout=14_400`)이고, 구현 docstring이 "geo(33GB급)처럼 큰 인스턴스는 기본 timeout으로도 부족할 수 있다"고 적는다.
- **중단돼 보여도 그대로 재시도하지 마라.** timeout에 걸리면 로컬 `docker exec` client만 끊기고 **컨테이너 안의 `pg_dump`는 계속 돈다**(docker exec가 timeout을 안쪽으로 전파하지 않는다). `pg_stat_activity`에서 그 `pg_dump` 백엔드가 사라진 것을 확인하기 전에는 재시도하지 않는다 — 두 pg_dump가 동시에 도는 시점이 하필 다운타임 창 한가운데다.

#### 7.1.3 — 3단계: 소유 role 점검과 리맵 (빠뜨리면 첫 마이그레이션이 죽는다)

**전용 instance의 소유 role은 공용 instance에 존재하지 않는다.** 실측: geo 전용 instance의 role은 `addr` 하나뿐이고 **superuser**이며 `kor_travel_geo`(32 GB)·`kor_travel_geo_dagster`를 모두 소유한다(concierge 전용 instance도 `POSTGRES_USER=addr`였다 — `docker-compose.yml:98`). 공용 instance의 role은 `shared_admin` / `kor_travel_concierge_app` / `kor_travel_weather_dagster_app` 셋뿐이고, §6.6의 이름 규칙이 `addr` 같은 role의 생성을 금지한다.

그대로 복원하면 dump 안의 모든 `ALTER ... OWNER TO addr` · `GRANT ... TO addr`가 실패하고, 아무 플래그 없이 `shared_admin`으로만 복원하면 객체가 전부 `shared_admin` 소유로 남는다. 그러면 §4.2가 약속한 "자기 테이블 소유·DDL"이 성립하지 않아 **cutover 직후 첫 Alembic 마이그레이션이 권한 오류로 죽는다.** `pg_database.datdba`만 보는 검사로는 이 실패를 잡지 못한다.

- **(a) 사전 쿼리** — 덤프의 소유 role이 새 instance에 **없음**을 확인한다. 옛 instance에서 `SELECT tableowner, count(*) FROM pg_tables WHERE schemaname='public' GROUP BY 1`과 `\du`로 소유 role 이름을 뽑고, 공용 instance의 `pg_roles`에 그 이름이 없음을 확인한다. (그 이름은 §9.1 제출 항목이기도 하다.)
- **(b) 실제로 쓸 명령** — concierge가 쓴 쪽을 그대로 쓴다: **`pg_restore --no-owner --role=<app_user>`**. `--no-owner`가 dump의 소유자 지정을 버리고, `--role`이 `SET ROLE`로 생성 객체를 app role 소유로 만든다. (대안인 `--no-owner` 후 `REASSIGN OWNED` / `ALTER ... OWNER` 일괄 패스는 단계가 늘고 창을 길게 만든다.)
- **(c) 소유 role이 superuser였다면 강등이 동반된다.** geo의 `addr`이 그 경우다 — 공용 instance에서 네 app role은 `NOSUPERUSER/NOCREATEDB/NOCREATEROLE`이므로, superuser를 전제한 코드·운영 절차가 있으면 cutover 전에 걷어낸다.

#### 7.1.4 — 4단계: 복원 (그대로 따라 할 수 있는 형태)

접속 경로부터 정한다. **`shared_admin`의 비밀번호를 꺼내 쓰지 않는다** — 컨테이너 안 unix socket이 `trust`이므로 Manager 백업 모듈과 같은 경로를 쓴다.

```
# 1) 덤프를 컨테이너 안으로 넣는다 (백업 산출물은 root:root 0600이다)
docker cp <dump 파일> <공용 컨테이너>:/tmp/<이름>.dump
# 2) pg_restore는 --user postgres로 돌 것이므로 컨테이너 안에서 소유자를 넘긴다
docker exec <공용 컨테이너> chown postgres /tmp/<이름>.dump
# 3) 복원 — unix socket, 비밀번호 없음. 포트는 5432가 아니라 11000이다
docker exec --user postgres <공용 컨테이너> \
  pg_restore --username shared_admin --port 11000 \
             --dbname <네 database> \
             --no-owner --role=<app_user> \
             /tmp/<이름>.dump
```

판정 기준:

- **대상 DB는 db-init이 `createdb -O <app_user>`로 만든 DB**여야 한다(§7.2).
- 오류 0건. **단, owner-only 경고가 몇 건 나오는 것은 정상이다** — 옛 instance 전용 role에 걸린 GRANT나 `pg_stat_statements`/`postgis` 확장 주석 때문이고, concierge 실측 4건이 "실제 데이터 손실 없이 무해함"으로 확인됐다.
- 끝나면 `/tmp`의 덤프 사본을 지운다.

### 7.2 복원 대상 DB에 관한 규칙

공용 instance의 bootstrap DB 이름이 `postgres`라는 중립 이름인 것은 우연이 아니다. compose 주석의 근거는 **"같은 이름으로 init하면 initdb가 심은 확장과 dump의 확장 배치가 복원에서 충돌한다(map cutover 실측)"** 이다.

그래서 복원 대상은 **initdb가 만든 DB가 아니라 db-init이 `createdb -O <app_user>`로 새로 만든 DB**여야 한다. 그리고 §3의 P8대로 **앱 데이터 / bootstrap / 잔해**를 먼저 갈라 두어야 한다 — concierge도 `kor_travel_concierge` 하나만 옮겼고, `ktc_bootstrap`·`postgres`(bootstrap)와 `p2_proof_ktc`(참조 없는 잔해)는 옮기지 않았다.

### 7.3 소유권의 빈틈 (멱등성이 안 덮는 곳)

db-init 재실행이 자가치유하는 것은 **role 속성·비밀번호·확장·CONNECT ACL**뿐이다. 소유권은 `createdb` 시점에만 적용되므로, **DB가 이미 존재하면 `-O`가 다시 걸리지 않는다.** role을 지웠다 다시 만들거나 DB를 수동으로 만든 뒤 one-shot을 돌리면 "소유자 불일치"가 조용히 남는다.

따라서 소유권은 **두 층에서** 본다.

- **database 층** — 복원 전후로 `pg_database.datdba`가 네 app role인지.
- **객체 층** — `SELECT tableowner, count(*) FROM pg_tables WHERE schemaname='public' GROUP BY 1`이 **app role 하나로 수렴**하는지(확장이 만든 `spatial_ref_sys` 같은 `shared_admin` 소유 1~2건은 정상). **`datdba`만 보면 §7.1.3의 실패를 통과시킨다.**

### 7.4 검증 6종 (그대로 따라 할 수 있는 형태)

1. **Alembic head** — `SELECT version_num FROM alembic_version;` 옛/새가 같아야 한다.
2. **확장** — `SELECT extname, extversion FROM pg_extension;` 목록과 버전이 같아야 한다(owner는 `shared_admin`으로 달라진다 — 정상). PostGIS는 `SELECT postgis_full_version();`까지 본다.
3. **row count** — 반드시 `SELECT count(*)`로 대조한다. **`pg_stat_user_tables.n_live_tup`은 믿으면 안 된다** — 옛 concierge instance의 `extracted_place_candidates`는 통계상 `n_live_tup=1`인데 실제 `count(*)`는 **4,273**이다(2026-09-20 재실측에도 그대로 재현된다. ANALYZE 이력 탓).
4. **sequence** — `SELECT last_value FROM <seq>` 또는 `pg_sequences`로 신·구 전수 대조. **어긋나면 복원 시점에는 조용하고 첫 INSERT에서 중복 키로 터진다** — 그때는 이미 검증 창을 지난 뒤다. concierge는 "테이블 26개 row count 전부 old=new 정확히 일치, sequence 값 전부 일치"를 기록으로 남겼다.
5. **소유자 집계** — §7.3의 객체 층 쿼리가 app role 하나로 수렴.
6. **writer 유일성** — 전환 후 일정 시간 뒤, **새 instance에서만 row가 늘어나는지** 재대조한다. 판정 기준은 증가분의 크기가 아니라 **한쪽에서만 단조 증가**한다는 사실이다(concierge는 `crawl_runs`가 그 지표였다).

### 7.5 시간대 — 백업 cron과 충돌한다

`scripts/run-standalone-backup.sh`가 cron으로 `geo_dagster` **03:15**, `concierge` **03:30**, `pinvi` **03:55**(UTC)에 돈다. **`geo`는 이 목록에 없다 — wrapper가 거부한다(`exit 2`).** 같은 role의 `_role_lock`(`~/backups/<role>/.backup.lock`)이 겹치면 cron wrapper가 `set -eu`로 그대로 중단된다.

수동 백업·리허설은 이 시각을 피하고, **cutover 당일 밤 cron이 어느 instance를 겨냥하는지**를 먼저 확인하라. 확인 핸들은 추측이 아니라 산출물에 있다 — **`~/backups/<role>/<파일>.manifest`의 `instance` 필드**가 `컨테이너:호스트:포트/DB`를 그대로 적는다(예: `kor-travel-concierge-postgres:127.0.0.1:12600/kor_travel_concierge`). 그 값이 아직 옛 instance면, 백업 재지정 커밋이 **crontab이 가리키는 체크아웃에** 반영되지 않은 것이다(§6.4 — 지금 prod가 정확히 그 상태다).

### 7.6 ⚠️ 리허설의 blast radius가 cluster 전역이다

`rehearse-restore`의 정리 루틴은 `SELECT datname FROM pg_database WHERE datname LIKE 'ktdm_rehearsal_%'`를 **instance 전체**에 돌려 이름의 epoch가 **6시간**보다 오래된 것을 role 구분 없이 `dropdb`한다.

전용 instance에서는 blast radius가 같은 instance의 role 쌍까지였다. **공용 instance에서는 합류한 모든 프로젝트로 넓어진다** — 프로젝트 A의 6시간 넘는 리허설(기본 timeout 4시간, `map_application` 실측 약 97분)이 프로젝트 B의 리허설 시작 때 복원 도중 drop될 수 있다. 문서에 "여러 role을 한 번에 리허설하지 말 것"은 있지만 **cross-project 관점은 아직 없다.** 큰 DB의 리허설을 돌릴 때는 다른 테넌트와 시간을 겹치지 마라. §7.1 0g의 드라이런 scratch DB에 **다른 접두사**를 쓰라고 한 것도 같은 이유다.

---

## 8. 롤백

### 8.1 구조

롤백은 **compose 편집이 아니라 env 변수 하나(DSN)를 되돌리고 재기동**하는 것이다. 그것이 가능하도록:

- **옛 instance를 지우지 않는다.** 데이터를 그대로 두고 계속 healthy하게 띄워 둔다.
- 네 target의 `services:`/`containers:`가 **옛 instance + 옛 db-init + 공용 instance + 공용 db-init 넷 모두**를 관리한다.
- 앱 서비스의 `depends_on`이 **신·구 양쪽**을 문다. 둘 다 무해하게 healthy해지고, 실제 접속 대상은 DSN 하나가 정한다.

concierge 실측: **cutover 직후(2026-09-20 기준)에도 옛 instance는 healthy하고 cutover 시점 데이터를 그대로 들고 있다. 무사고 경과는 아직 하루 미만이다** — `docker ps`의 `Up 2 weeks`는 **컨테이너 가동 시간**이지 cutover 이후 기간이 아니다. 이 구분을 틀리면 §3 P12의 종료 조건("N일 무사고 후 폐기")이 14배 부풀려진 근거 위에 서게 된다.

### 8.2 롤백 절차

1. 앱 정지(§7.1.1의 동결 기준을 그대로 적용 — `ensure` 금지 포함).
2. 호스트 `.env`의 DSN 변수를 옛 instance 값으로 되돌린다.
3. 재기동 후 `pg_stat_activity`로 옛 instance에 연결이 붙는지 확인.
4. cutover 이후 새 instance에 쌓인 데이터가 있다면 **그 손실을 명시적으로 판단**한다 — 자동으로 합쳐지지 않는다.

### 8.3 함정

| 함정 | 내용 |
|---|---|
| **하드 의존** | 앱의 `depends_on`이 옛 instance `service_healthy`와 옛 db-init `service_completed_successfully`를 **여전히 요구**한다. compose에서 지우지 않은 채 호스트에서 옛 instance를 내리면 `ensure`가 **통째로 실패**한다 |
| **`init_steps` 의존** | 같은 이유로, `init_steps`가 옛 DB 컨테이너에 `exec`하도록 남아 있으면 옛 instance를 내리는 순간 `ensure`가 죽는다(§6.2 T7) |
| **pgdata bind 유지** | 옛 instance의 pgdata bind도 `compose_binds` allowlist에 그대로 남아 있어야 한다 |
| **조용한 역주행** | §3.1 — DSN 변수가 빠지면 롤백이 아니라 **사고**로 같은 일이 일어난다 |
| **폐기 기준 부재** | "언제까지 남기는가"를 정한 문장이 저장소 어디에도 없다. `docker-targets.yml`은 "이 target 밖에서 별도로 폐기되기 전까지 여기 남는다"고만 한다. **이것을 답습하지 마라** — 네 저장소가 "N일 무사고 + 백업 M세대 확보 후 폐기" 같은 종료 조건을 직접 정의한다(§3 P12). 그리고 그 "N일"은 **cutover 날짜**부터 센다 |

### 8.4 창 중 동결이 깨졌을 때 — 옛 DB에 들어간 행의 처리

동결 위반(누군가의 `ensure`, 대시보드 조작, 폐포에 걸린 다른 target 실행)으로 창 도중 옛 DB에 쓰기가 일어났다면, **두 정본이 생긴 상태**다. 자동 병합은 없다.

1. **먼저 멈춘다.** 옛 instance의 writer를 다시 끊고(§7.1.1), 옛 instance에서 창 시작 이후 변경된 행을 범위로 잡는다(`created_at`/`updated_at`/시퀀스 최댓값 등 네 스키마의 시간축).
2. **크기를 센다.** 변경이 0건이면 창은 유효하다 — 기록만 남기고 진행한다.
3. **0건이 아니면 두 갈래뿐이다.** (a) 덤프를 버리고 창을 다시 연다(가장 안전, 다운타임 반복). (b) 그 행 집합을 신 instance로 수작업 이관하고 §7.4의 row count·sequence 검증을 **그 테이블에 대해 다시** 돌린다. sequence를 함께 맞추지 않으면 첫 INSERT에서 중복 키로 터진다.
4. **어느 쪽이든 기록에 남긴다.** 무엇이 언제 옛 DB에 들어갔고 어떻게 처리했는지가 없으면, 나중에 옛 instance를 폐기할 때 "이 데이터는 옮겨졌는가"를 아무도 답할 수 없다.

---

## 9. 프로젝트별 출발점

| 프로젝트 | 현재 | 첫걸음 |
|---|---|---|
| **concierge** | **이전 완료**(2026-09-19/20, 기록은 자기 저장소 `docs/journal.md` 최상단). 앱 DB가 `:11000`에 있고 Alembic head 유지 | ① **지금 prod 백업이 옛 instance를 뜨고 있다** — §6.4의 "crontab이 가리키는 체크아웃" 행을 먼저 닫아라(§7.5의 manifest `instance` 필드로 확인). ② 옛 instance 폐기 종료 조건 정의(§3 P12, 기준 시각은 **cutover 날짜**). ③ 새 확장이 필요할 때 Manager one-shot에 줄 추가(§4.3) |
| **geo** | 전용 `:12500`. `kor_travel_geo` **32 GB** + `kor_travel_geo_dagster` 92 MB | 규모가 자릿수로 다르다 — §3 P7부터. **P2는 이미 충족돼 있다**(`KTG_PG_DSN: ${KOR_TRAVEL_GEO_DOCKER_PG_DSN:?...}` — 확인만 하고 넘어가라). geo만의 항목: (i) **소유 role이 superuser `addr` 하나**라 §7.1.3의 리맵 + **NOSUPERUSER 강등**이 동반된다, (ii) **`init_steps: geo-source-verification`** 이 `kor-travel-geo-postgres` 안에서 `addr`로 `load_manifest`/`tl_juso_text`/`mv_geocode_target`을 세고 `/data/juso` 바인드를 요구한다 — 새 instance로 옮기려면 `/data/juso:ro`를 들고 가야 해 **`compose_binds` allowlist가 정확히 걸린다**(§6.2 T6·T7), (iii) **`geo.depends_on: [prom]`이라 폐포에 `conc`가 없다**(§6.2 T1), (iv) **cutover 직전 백업의 주인은 Manager cron이 아니라 kor-travel-geo 앱의 스케줄 백업이다**(§6.4) — 창을 열기 전에 그 백업이 실재하는지 네가 확인해야 한다, (v) `_ROLE_CONFIG["geo"]`는 `container_env=None`이라 env override 경로가 없고 재지정이 반드시 코드 변경이다. code-server 3-분리는 이미 완료 |
| **weather** | **외부 프로젝트.** DB는 `kor-travel-weather-db-1`(`postgres:16-alpine`, PostGIS 아님, compose bridge `kor-travel-weather_default`, `127.0.0.1:14100->5432`, 확장 `plpgsql`뿐). 사용자 테이블은 **`weather` DB 34개 + 같은 instance의 `weather_dagster` DB 22개**(2026-09-20 실측; `weather`의 카탈로그 밖 ordinary relation은 33개). Manager에는 수명주기만 등록 | ⚠️ **네 Dagster 메타DB는 이미 두 군데에 있다 — 로컬 `weather_dagster`와 공용 instance의 `kor_travel_weather_dagster`(§10.1이 "흔적"이라 부르는 그것). §3 P10을 시작하기 전에 어느 쪽이 정본인지부터 정하라.** 그다음: 공용 instance는 **Manager 소유**이므로 db-init one-shot이 **Manager compose에** 들어가야 한다 — 네 저장소 compose에 넣으면 prod에서 읽히지 않는다(§2). PostGIS 기반 이미지로 옮겨가는 것의 영향(확장·타입)도 미리 본다 |
| **transport** | **Manager에 자리가 없다.** 프로젝트로서 등장하는 곳 0건 — `docker-compose.yml` 서비스·`docker-targets.yml` target·n150 컨테이너 모두 없다. 포트 대역 미배정. (코드 주석·`websocket.py`·`compose_service.py` 등에 보이는 영어 단어 `transport`는 무관하다) | §6의 등록 체크리스트를 **처음부터** 밟는다. 합류 요청 시 §9.1의 정보를 제출하는 것이 출발점 |

### 9.1 합류 요청 시 Manager에 제출할 정보

**제출 방법:** Manager 저장소에 이슈를 열고 아래 표를 그대로 붙인다. 합류 PR(compose + `docker-targets.yml` + 테스트 + 문서)은 네가 열어도 되고 Manager 담당자에게 맡겨도 되지만, **누가 여는지를 그 이슈에서 먼저 정한다.** 머지 이후 prod 반영(소스 전달 + `ensure`)은 §2 표대로 Manager 운영자의 몫이고, 그것이 §7.1의 0b~0e다.

| 항목 | 예/형식 |
|---|---|
| 프로젝트 슬러그 | kebab(`kor-travel-transport`) + UPPER_SNAKE(`KOR_TRAVEL_TRANSPORT`) |
| database 이름 | `kor_travel_transport` |
| app role 이름 | `kor_travel_transport_app` |
| **현재 객체 소유 role 이름** | 옛 instance에서 `pg_tables`의 `tableowner` 집계 결과. **그 role이 superuser면 명시하라** — §7.1.3의 리맵과 NOSUPERUSER 강등이 동반된다 |
| **필요한 확장 목록** | `extname` + 최소 버전 (§4.3 — Manager가 superuser로 만든다) |
| DSN env 변수 이름 | 신규면 `KOR_TRAVEL_TRANSPORT_DOCKER_DATABASE_URL`. **이미 배포된 이름이 있으면 그 이름 그대로**(§6.6) |
| DSN fallback 정책 | 부재 시 기동 실패(`${...:?...}`) / 공용 instance / (옛 instance 금지) |
| 데이터 규모·덤프 시간 실측 | `pg_database_size` + `pg_dump -Fc --compress=6` 초 |
| Alembic head | `version_num` |
| advisory lock 사용처 | 파일:줄 전수 목록, 세션/트랜잭션 구분 |
| 예상 최대 연결 수 | 풀 크기 × 프로세스 수. **분모는 `max_connections=100`(cluster 전역)** — 남은 예산 안에 드는지 함께 적는다 |
| **필요한 튜닝값 + 기존 테넌트에 미치는 영향** | `shared_buffers`/`work_mem` 등. **`shm_size` 요구 여부를 반드시 적는다**(현재 공용 서비스에는 설정이 없어 `/dev/shm` 기본 64MB다). 이 값들은 cluster 전역이고 반영에 **공용 instance 재기동 = 기존 테넌트 다운타임**이 따른다(§6.1 C9) |
| `init_steps` 유무 | 있으면 그 step이 어느 컨테이너에 `exec`하고 어떤 bind를 요구하는지(§6.2 T7) |
| 백업 role 필요 여부 | 필요하면 role 이름 제안. **현재 cutover 직전 백업의 주인이 누구인지도 함께**(§6.4) |
| Dagster 메타DB 유무 | 있으면 §7 계획의 어느 단계인지. **이미 공용 instance에 흔적이 있으면 그 사실을 적는다**(§10.1) |
| 이전 대상 database 분류 | 앱 데이터 / bootstrap / 잔해 |
| 옛 instance 폐기 종료 조건 | "N일 무사고 + 백업 M세대" 같은 문장 (기준 시각 = cutover 날짜) |

---

## 10. 알려진 구멍과 불확실

### 10.1 ⚠️ prod에 직접 만들고 compose에 db-init을 안 올리면, 그것은 배포가 아니라 흔적이다

공용 instance에 `kor_travel_weather_dagster`(약 7.4 MB, owner `kor_travel_weather_dagster_app`)와 그 app role이 **존재한다.** 그런데 Manager `origin/main`에 `weather_dagster` 문자열이 **0건**이고, n150에 `kor-travel-shared-db-init-weather-*` 컨테이너가 **없다**(`docker ps -a`에 `kor-travel-shared-db-init-concierge`만 있다). 같은 사실의 다른 쪽 — weather 전용 instance에는 로컬 `weather_dagster` DB(테이블 22개)가 여전히 있다(§9 weather 행).

즉 그 role/database는 compose의 db-init one-shot이 아니라 **수동 SQL로 만들어졌고 재현 불가능하다.** 빈 PGDATA에서 재구축하거나 깨끗이 재배포하면 되살아나지 않는다. 그리고 이것은 정확히 §5.2가 금지하는 형태와 같은 계열의 실패다 — 프로비저닝이 문서가 아니라 사람의 기억에 있다.

**금지한다.** 공용 instance의 role/database는 반드시 Manager compose의 프로젝트 전용 db-init one-shot이 만든다. 급해서 손으로 만들었다면, **같은 배포 창 안에** 그 SQL과 동등한 one-shot을 compose에 올려 멱등 재실행으로 자기 상태를 재단언하게 만든다.

(다행히 격리 자체는 성립한다 — 실측 CONNECT 매트릭스에서 `kor_travel_concierge_app`은 `kor_travel_weather_dagster`에 접속 불가이고 그 반대도 마찬가지다. 두 role 모두 `NOSUPERUSER/NOCREATEDB/NOCREATEROLE`이다. 문제는 권한이 아니라 **재현 가능성**이다.)

### 10.2 계약이 안 세는 자리

| 구멍 | 내용 |
|---|---|
| secret 소유자 배선 | Map postgres 비밀번호에는 소유자 배선 검사와 **단독 소비자 검사**가 이름으로 결박돼 있지만, `kor-travel-shared-postgres-password`와 app password에는 **동등한 검사가 없다.** 네 db-init이 cluster admin secret을 엉뚱한 서비스에 또 마운트해도 기계가 잡지 않는다 — 규약으로만 존재한다 |
| DSN 검증 | 공용 instance를 가리키는 DSN을 검증하는 테스트·validator가 **0건** |
| 배포 트리 ≠ cron 트리 | prod에 Manager 사본이 둘이고, 어느 사본이 어떤 일을 도는지를 검사하는 축이 **없다**. §1.1·§6.4·§7.5가 같은 사실의 세 얼굴이다 |
| initdb 인자의 한계 | `POSTGRES_INITDB_ARGS: "--auth-host=scram-sha-256"`은 **initdb 때만** 적용된다. 이미 만들어진 PGDATA에는 효력이 없다. compose만 보고 "이 instance는 scram이다"라고 단정하면 안 되고, **살아있는 `pg_hba`가 별도 축**이다(Manager의 `postgres_hba_posture` 검사가 배포 준비 게이트로 센다. 후보 집합은 `docker ps` 전수가 아니라 `docker-targets.yml`의 `containers` 절에서 온다 — 등록되지 않은 컨테이너는 이 검사에 안 잡힌다). §1.1의 catch-all `host all all all scram-sha-256` 행이 그 비대칭의 실물이다 |
| cluster 전역 CONNECT 자세 | §5.3의 template DB 구멍. 테넌트가 늘수록 "cluster 전역 자세를 누가 소유하는가"가 비어 있다 |
| `bindings.md` 미등록 | DSN이라는 하나의 사실이 최소 네 곳(호스트 `.env`, compose 기본값 3자리, db-init env, 백업 `_ROLE_CONFIG`)에 적혀 있는데 `bindings.md`에 결박으로도, 결박 불가 사유로도 등록돼 있지 않다 |
| healthcheck의 의미 | `pg_isready`는 인증을 완료하지 않는다. `healthy`는 "연결 수락"이지 "자격증명 정상"이 아니다 — 자격증명 정합성은 뒤따르는 db-init이 실제 `psql`로 증명한다 |

### 10.3 불확실로 남겨 두는 것

| 항목 | 상태 |
|---|---|
| geo·map·pinvi의 공용 instance 이전 **여부와 시점** | **어떤 문서도 정하지 않았다.** ADR-44가 "확인하지 않은 것"으로 명시 |
| ADR-44의 "231 MB" | database 실측(81~83 MB)과 불일치. instance 전체 또는 부수 DB 합산으로 **보인다**(단정 불가) |
| advisory lock 호출부 개수 | ADR의 "5곳"은 파일 수로 보이고, 실측은 트랜잭션 락 6 + 세션 락 1이다. **네 저장소에서 직접 센 목록을 쓰라** |
| cutover 실행 기록 | Manager `docs/journal.md`에는 **없다**(`11000` 0건 — ADR-44가 "결과를 `docs/journal.md`에 남긴다"고 한 약속은 Manager 쪽에서 미이행). **실제 기록은 kor-travel-concierge 저장소 `docs/journal.md` 2026-09-20 항목**이고, 그것이 유일한 실행 선례다(§7 도입부) |
| weather·transport의 합류 요청 여부·마이그레이션 도구 | Manager 저장소에서 확인할 수 없다. weather는 외부 compose 소유라 Manager에 스키마 정보가 없다 |
| 호스트 여력 | **2026-09-20 03:2x UTC 실측**: 메모리 14 GB(available 약 7 GB), 루트 466 G 중 **373 G 사용(84%, 여유 74 G)**. geo 32 GB를 그대로 옮기면(옛 instance를 지우지 않으므로 순증가다) 덤프 파일 약 4.4~4.7 GB가 창 동안 얹혀 약 37 G — 가능하지만 여유가 절반 아래로 떨어진다. **이 숫자를 그대로 쓰지 말고 창을 열기 전에 다시 재라 — 판정 기준은 §7.1 0f다** |

---

## 관련

- **kor-travel-concierge 저장소 `docs/journal.md` 2026-09-20 항목** — "공용 Postgres 인스턴스(`:11000`)로 실제 데이터 마이그레이션 완료(ADR-44)". **유일한 실행 선례.** 7단계 순서, 실제 `pg_restore --no-owner --role=` 플래그, owner-only 경고 4건, row count/sequence 전수 일치, Alembic head, 연결 수 대비까지 들어 있다. §7의 검증 항목은 전부 여기서 파생됐다.
- `docs/decisions.md` — **ADR-44**: concierge를 공용 제어 평면 PostgreSQL instance(`:11000`)로 이전한다 — ADR-37의 concierge 범위 부분 supersede (accepted, 2026-09-19). cutover 절차·롤백 안전망·합류 규칙의 정본.
- `docs/decisions.md` — **ADR-37**: 프로젝트별 전용 PostgreSQL instance 분리(2026-08-17 사고). concierge 범위만 ADR-44로 일부 superseded되고 **geo/map/pinvi는 그대로 유효**. §5.2의 근거.
- `docs/prod-deployment.md` §2 — 머지된 변경이 n150에 도달하는 경로(rsync 또는 trusted installer). §7.1 0b의 정본.
- `docs/platform-topology.md` — 다른 프로젝트를 위한 플랫폼 참조. §2(내부/외부 target), §5(code-server 분리 현황), §7(전환 5단계 계획 — 공용 Dagster `11001`/`11002`는 **여기에만, 계획으로** 존재한다). ⚠️ §4 instance 표와 §7 도입부가 아직 cutover 전을 적는다(§1.2).
- `AGENTS.md` — ⚠️ 74·151·156행이 아직 cutover 미완을 서술한다. 다른 저장소가 Manager를 처음 읽을 때 가장 먼저 여는 파일이므로 정정 우선순위가 가장 높다.
- `docs/ports.md` — 포트 규약의 정본. `11000`은 12000대 대역과 별개이며 합류 프로젝트가 늘어도 바뀌지 않는다. ⚠️ 101행이 아직 미래형이다.
- `docker-compose.yml` — `kor-travel-shared-postgres` + `kor-travel-shared-db-init-concierge`. **db-init 형태의 유일한 선례이자 복제 원본.**
- `config/docker-targets.yml` — target 등록·`containers`·`init_steps`·`compose_binds`(GM-17 보안 경계).
- `scripts/run-standalone-backup.sh` — cron allowlist와 그 헤더(왜 `geo`가 없는지). `backend/src/kor_travel_docker_manager/services/standalone_backup.py` — 백업 role 정본(`BackupRole` / `BACKUP_ROLES` / `_ROLE_CONFIG`), 덤프 timeout(기본 4시간)과 리허설의 `--no-owner` 미사용 이유(§7.0). ⚠️ ADR-44가 인용하는 `config/backup-policy.yml`은 이 저장소에 **없다**.
- `scripts/init-kor-travel-geo.sql` — **읽되 쓰지 마라.** 다중 프로젝트 provisioning의 원본이 평문 비밀번호와 함께 추적된 채 남아 있고, 지금은 어디에도 마운트되지 않는다(§5.2).
