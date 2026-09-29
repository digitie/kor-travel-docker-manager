# platform-topology.md — 플랫폼 전체 구조 (다른 프로젝트를 위한 참조)

이 문서의 독자는 **Manager가 아닌 프로젝트**다. `kor-travel-map`·`kor-travel-geo`·
`kor-travel-concierge`·`pinvi`·`kor-travel-transport`·`kor-travel-weather`, 그리고 앞으로
합류할 프로젝트가 "내가 이 플랫폼에서 어디에 서 있고, 무엇을 내가 소유하며, 무엇을
Manager에게 맡기는가"를 여기서 읽는다.

**이 문서가 아닌 것.**

- Manager **자신의** 내부 설계(FastAPI 백엔드·Next.js 대시보드·Docker SDK 연동)는
  [`architecture.md`](architecture.md)가 갖는다.
- 포트 **표**의 정본은 [`ports.md`](ports.md)다. 여기서는 표를 베끼지 않고 **규칙과
  그 규칙이 사는 자리**만 말한다. 베끼면 둘이 갈린다.
- 배포 절차의 세부는 [`prod-deployment.md`](prod-deployment.md),
  [`docker-management.md`](docker-management.md)가 갖는다.
- 두 곳에 적힌 사실을 묶는 기계는 [`bindings.md`](bindings.md)에 등록한다.

---

## 1. 한 문장

**Manager는 호스트의 컨테이너 연합을 관장하고, 각 프로젝트는 자기 코드와 이미지를
소유한다.** 그 경계가 어디인지가 이 문서의 전부다.

---

## 2. 소유 경계 — 내부 target과 외부 target

프로젝트가 플랫폼에 들어오는 방식은 **둘뿐이고**, 둘의 차이는 "compose 정본이 어디
있는가" 하나다.

| | 내부 target | 외부 target (`external_project`) |
|---|---|---|
| compose 정본 | **Manager의 `docker-compose.yml`** | **그 프로젝트 저장소** |
| 예 | `geo` · `conc` · `map` · `pinvi` · `weather`(ADR-47, 2026-09-20까지는 외부였다) | `transport`(2026-09-28까지 이름이 `airport`였고, 그때까지 전용 DB target도 있었다) |
| Manager가 하는 일 | 상태 조회 + 수명주기 + **배포(`ensure`)** | 상태 조회 + 수명주기 **만** |
| `ktdctl ensure` | 가능 | **거부한다** |
| 배포 소유자 | Manager | 그 저장소 |

외부 target에 `ensure`가 거부되는 이유는 정책이 아니라 **계약 부재**다. Manager의
C6c 계약 기계(보호값 스캔·볼륨 그래프·단일파일 경계·핀셋)는 *Manager 자신이 만든
후보*를 전제한다. 형제 저장소의 compose는 그 계약을 받은 적이 없으므로 통과시킬
근거가 없다. 상태·수명주기가 되는 것은 `control_container`가 Docker SDK로 컨테이너를
직접 잡아 compose 프로젝트와 무관하게 동작하기 때문이다.

> **내부 target 프로젝트가 가장 자주 틀리는 것**: 자기 저장소의 `docker-compose.yml`을
> 고치고 "prod에 반영했다"고 적는 것이다. **prod에서 읽히는 compose는 Manager의
> 것이다.** 프로젝트 저장소의 compose는 로컬 개발(`scripts/docker-up.sh`)과 격리
> e2e 스택에만 쓰인다. Manager가 프로젝트 저장소를 참조하는 것은 build context와
> 스크립트 bind mount뿐이다.

---

## 3. 등록되는 자리 — `config/docker-targets.yml`

프로젝트가 플랫폼에 **보이게** 되는 유일한 자리다.

```yaml
containers:                      # 컨테이너 한 대 = 한 항목
  kor-travel-transport-backend:
    name: kor-travel-transport-backend-1  # 실제 컨테이너 이름
    compose_service: backend              # 그 프로젝트 compose에서의 서비스 이름
    external_project: kor-travel-transport  # ← 있으면 외부, 없으면 Manager 소유
    role: transport-backend
    display_name: Kor Travel Transport 백엔드
    connection: "http://127.0.0.1:14001"
    expected_ports: ["14001:14001"]       # host:container. 실제 바인딩이 없을 때(host network)
                                          # 대시보드가 대신 보여 주는 선언이라 host network면 둘이 같다

targets:                          # 사람이 다루는 단위 = 한 프로젝트(또는 그 일부)
  transport:
    port_band: "14001-14099"
    depends_on: [...]
    aliases: [...]                # 손에 익은 이름들
    services: [...]               # 내부 target만
    runtime_services: [...]       # 상시 떠 있어야 하는 것
    containers: [...]             # 위 containers의 키
    init_steps: [...]             # idempotent 보정 단계
```

(weather는 2026-09-20(ADR-47)까지 이 자리의 예시였다 — 지금은 internal target이라
`external_project` 필드가 없다. 실제 예시는 `config/docker-targets.yml`을 본다.)

**포트 정책은 이 파일에 없다.** GM-19에서 `port_policy`/`port_band` 블록을 소비하는
코드가 0건임이 확인돼 제거됐다 — 정본은 [`ports.md`](ports.md) 하나다. `port_band`는
target 단위로만 남아 있다.

`ktdctl targets validate --check-coordinates`가 선언과 실재를 대조한다. 기본값이
아닌 이유는 형제 저장소가 **배포 호스트에만** 있기 때문이다. 무조건 돌리면 개발
체크아웃과 CI에서 스키마가 완벽해도 실패한다.

---

## 4. 지금의 데이터 평면 — PostgreSQL

2026-08-17(ADR-37)에 **통합 `5432` 인스턴스를 폐지하고 프로젝트별 전용 인스턴스로
나눴다.** 그 뒤 concierge(ADR-44)·geo(ADR-45)·PinVi(ADR-46)·weather(ADR-47)·transport가
공용 instance(`:11000`, §7 5단계)로 옮겼고, 2026-09-28에 옛 전용 인스턴스(geo `:12500`·
concierge `:12600`·PinVi `:12800`)를 Manager compose에서 뺐다 — 그날 n150 실측으로 모든
서비스의 DSN이 `:11000`(Map만 `:12700`)을 가리켰고, 남아 있던 옛 인스턴스는 접속 0인
`pinvi-postgres` 하나였다. 옛 데이터 디렉터리는 호스트에 그대로 둔다. Map의 두 DB도
ADR-53으로 공용 인스턴스로 옮겨, 이제 Manager의 PostgreSQL은 공용 하나다.

| 인스턴스 | 포트 | 소유 | 담긴 DB |
|---|---:|---|---|
| `kor-travel-shared-postgres` | `11000` | Manager compose | `kor_travel_map`+`kor_travel_map_dagster`(ADR-53), `kor_travel_concierge`(ADR-44), `kor_travel_geo`+`kor_travel_geo_dagster`(ADR-45), `pinvi`+`pinvi_dagster`(ADR-46), `kor_travel_weather`+`kor_travel_weather_dagster`(ADR-47), `kor_travel_transport`+`kor_travel_transport_dagster` — 합류 절차는 [`shared-postgres-onboarding.md`](shared-postgres-onboarding.md) |

**애플리케이션 DB와 Dagster 메타DB가 같은 인스턴스 안에 나란히 있다**(geo·map·pinvi,
2026-09-19 PinVi PR #558/#356으로 pinvi도 합류). 이것이 §7의 전환에서 갈라지는
지점이다.

**퇴역 목록(보존물)** — 인스턴스 컨테이너는 지우고 데이터·되돌리기 재료는 남긴다.

| 옛 인스턴스 | 제거일 | 보존한 것 |
|---|---|---|
| `pinvi-postgres`(`:12800`) | 2026-09-28 | 데이터 디렉터리 `/home/digitie/pinvi-data/pgdata` |
| `kor-travel-map-postgres`(`:12700`) | 2026-09-29(ADR-53 이전 직후, 소유자 지시로 72시간 관찰 전 제거) | PGDATA `/home/digitie/kor-travel-map-data/pgdata`(이전 전 `kor_travel_map`·`kor_travel_map_dagster`와 검증 잔여 `ktm_40b`·`ktm_bootstrap`·`ktm_gcverify`·`ktm_gcverify_dagster`), 이미지 `sha256:69ee0897…`(로컬 태그 `ktm-retired/postgis:16.15-3.5.7-alpine`, tar `/root/map-db-move-20260929/postgis-16.15-3.5.7-alpine.tar`, pull 참조 `postgis/postgis@sha256:69ee08977169aa2bbdcfb5db9b54eaaf1907d4cc91b5b78f7dc00cd7951cfd90`), 창 증거 `/root/map-db-move-20260929/`(감사 dump·로그·스냅숏, 되돌리기에 필요한 옛 superuser 값이 든 `.env` 사본). 되돌리기는 `30-rollback.sh move-after-acceptance` |

`12000-12099` 대역은 비어 있다 — 폐지된 통합 인스턴스와, 그 뒤 geo 인스턴스(`12500`)만
가리키던 `db` target(2026-09-28 폐지)의 자리였다. 새 프로젝트가 `db`라는 이름을
재사용하면 안 된다.

---

## 5. 지금의 제어 평면 — Dagster

**Dagster를 쓰는 다섯 프로젝트(`pinvi`·`geo`·`weather`·`map`·transport)가 모두 §7
1단계(code-server 분리)를 충족했다.** code-server는 **프로젝트마다 하나씩 따로** 돈다 —
프로젝트끼리 합치지 않고, 합칠 계획도 없다(§7 "왜 code-server만 나뉘는가"). 공유하기로 한
것은 webserver/daemon과 메타DB(`dagster_shared`)다. 메타DB는 2단계의 정의만 있고 아무도 쓰지
않으며, webserver/daemon은 아직 없다(§7 2~4단계).
pinvi/geo는 2026-09-19(서로 다른 PR이 거의 동시에 착지 — PinVi ADR-069/PR
`digitie/pinvi#559`+`#358`, geo는 PR #357), weather는 원래 external target 때부터
분리돼 있었고(참조 구현 `kor-travel-weather` PR #61) 2026-09-20 ADR-47로 Manager
internal target이 되며 `network_mode: host`로도 옮겨왔다 — 지금은 pinvi/geo와 같은
접속 방식(서비스명 DNS가 아니라 loopback, 아래 참고)을 쓴다. `map`은 2026-09-25
(#397, ADR-069 짝)에 같은 형태가 됐다. transport는 외부 프로젝트라 자기 저장소의 배포
사본이 webserver·daemon·code-server를 띄운다(n150 `kor-travel-transport-dagster-*` — 이름은 그
저장소의 compose 프로젝트 이름을 따른다).
`conc`는 Dagster를 쓰지 않는다. 2026-09-28 n150 실측으로 code-server 다섯 개가 모두
떠 있었다.

| 프로젝트 | webserver | daemon | code-server(gRPC) | 코드 로드 방식 |
|---|---|---|---|---|
| `pinvi` | `pinvi-dagster` `12802` | `pinvi-dagster-daemon` (포트 없음) | `pinvi-dagster-code-server` `12803` | webserver/daemon → `-w workspace.yaml`(grpc_server), code-server만 `-m pinvi.etl.definitions` |
| `geo` | `kor-travel-geo-dagster` `12502` | `kor-travel-geo-dagster-daemon`(포트 없음) | `kor-travel-geo-dagster-code-server` `12503` | PR #357 — pinvi와 같은 3-분리 형태(상세는 그 PR 참조, 이 문서는 표만 갱신) |
| `map` | `kor-travel-map-dagster` `12702` | `kor-travel-map-dagster-daemon` (포트 없음) | `kor-travel-map-dagster-code-server` `12703`(loopback 전용) | #397 — webserver/daemon → `-w workspace.yaml`(grpc_server), code-server만 `-m kortravelmap.dagster.definitions` |
| `weather` | `kor-travel-weather-dagster-webserver` 내부 전용 `14107` + 게이트웨이(Basic Auth) `14102` | `kor-travel-weather-dagster-daemon` (포트 없음) | `kor-travel-weather-dagster-code-server` `14106`(loopback 전용, 무인증) | ADR-47 — Manager 소유, webserver/daemon → `-w workspace.yaml`(grpc_server, Manager 소유 오버라이드가 `host: dagster-code-server`를 `127.0.0.1`로 재작성), code-server만 `-m kortravelweather_dagster.definitions` |
| `transport`(외부) | 그 저장소 compose | 그 저장소 compose | `kor-travel-transport-dagster-code-server-1` | 그 저장소가 소유한다 — Manager compose에는 없다 |
| `conc` | 없음 | — | 없음 | — |

> **접속 방식**: pinvi·geo·weather·map 모두 이 저장소의 compose가 강제하는
> `network_mode: host`라 `workspace.yaml`이 서비스명이 아니라 `host: 127.0.0.1`을
> 쓴다(PinVi ADR-069 §결정 2가 먼저 정한 패턴, geo는 PR #357, weather는 ADR-47 —
> weather는 처음엔 자체 bridge network + 서비스명 DNS를 시도했다가 internal target
> 전환과 함께 이 패턴으로 옮겨왔다).

이 배치의 결과가 §7 전환의 전제다: **공유 webserver/daemon으로 가려면 모든 프로젝트가
먼저 code-server를 분리해야 한다.** Dagster를 쓰는 다섯 프로젝트가 모두 그 1단계를
밟았다. 다음 차단 요인은 §7의 "공유의 전제 둘"(같은 인스턴스 스토리지, dagster 버전 호환)이다.

---

## 6. 자격증명 배선 규약

Manager compose가 프로젝트에 값을 넘기는 형태는 하나다.

```yaml
KOR_TRAVEL_MAP_OPINET_API_KEY: ${KOR_TRAVEL_MAP_OPINET_API_KEY:-}
```

지켜야 할 것 셋.

1. **`.env.example`에 빈 placeholder를 함께 둔다.** 운영자가 채울 자리를 모르면
   값은 비고 그 job만 조용히 죽는다. `test_map_provider_credentials_have_empty_env_example_placeholders`가
   compose에서 유도해 이것을 센다 — 목록을 손으로 적지 않는다.
2. **키를 받는 서비스는 그 키를 쓰게 하는 선택자도 함께 받는다.** OpiNet은 키가 있어도
   `scope`를 안 넘기면 영원히 비활성이었다. KREX go key, 서울 열린데이터광장 키까지
   **같은 형태의 구멍이 세 번** 났다.
3. **빈 문자열은 값이 아니다.** `${X:-}`는 변수를 *항상 정의하고 값을 비운다.* 받는
   쪽이 pydantic이면 `SecretStr("")`가 되어 `if secret is None` 가드가 **배포
   형상에서 한 번도 발화하지 않는다.** 로컬·CI에서는 env를 아예 안 주므로 보이지
   않고 prod에서만 뚫린다. 받는 쪽에서 `env_ignore_empty=True`(pydantic-settings)로
   닫는다.

값 자체는 호스트 `.env`(root 0600)에만 둔다. Manager의 로그 마스킹은 패턴 기반이라
`*_API_KEY`/`*_SERVICE_KEY` 꼴 새 이름도 자동으로 가려진다.

---

## 7. 전환 중 — 공유 제어 평면 (2026-09-19 결정)

**결정된 목표**이고 **대부분 아직 만들어지지 않았다.** 이 절은 계획이지 현황이 아니다.

**단, 5단계(애플리케이션 DB 이사)는 Map을 뺀 전부에 대해 실제로 완료됐다** — 공용
instance `kor-travel-shared-postgres`(`:11000`)에 `kor_travel_concierge`(ADR-44),
`kor_travel_geo`+`kor_travel_geo_dagster`(ADR-45), `pinvi`+`pinvi_dagster`(ADR-46, 데이터
보존 없이 fresh 구성), `kor_travel_weather`+`kor_travel_weather_dagster`(ADR-47),
`kor_travel_transport`+`kor_travel_transport_dagster`가 활성이다(2026-09-28 n150 실측: 해당
서비스의 DSN이 전부 `:11000`). 옛 전용 인스턴스는 같은 날 Manager compose에서 뺐다(§4).
1단계(code-server 분리)도 Dagster를 쓰는 다섯 프로젝트가 모두 밟았다(§5). 2단계(공유
Dagster 스토리지 `dagster_shared`)는 **Manager 쪽 정의가 있다** — db-init
`kor-travel-shared-db-init-dagster`, storage migrate one-shot `kor-travel-dagster-storage-migrate`,
공용 `config/dagster-shared/dagster.yaml`, Manager 소유 호스트 이미지(`docker/dagster-host/`),
백업 role `dagster_shared`, target `dagster`. n150에 생기는 것은 그 release를 설치한 뒤 창에서
`ensure dagster`를 돌렸을 때다(두 one-shot은 init step이라 어느 하나라도 실패하면 ensure가 실패한다).
2단계 검증의 백업은 crontab 줄이 부르는 **그 경로**(설치본 `/opt/kor-travel-docker-manager/scripts/
run-standalone-backup.sh dagster_shared 7`)를 cron 계정으로 돌린다. 아무것도 아직 그 instance를 쓰지 않는다. 3·4단계(공용
webserver/daemon/gateway · 프로젝트별 webserver/daemon 철거)는 여전히 계획이다 — `11001`/`11002`에는
**아직 아무것도 없다**. 다른 프로젝트가 5단계를 먼저 밟는 절차는
[`shared-postgres-onboarding.md`](shared-postgres-onboarding.md)가 갖는다.

**공용 instance의 튜닝은 Map의 값이다(ADR-53 D4).** Map의 두 DB가 이리로 오기 전에
instance를 한 번 재기동해 Map이 전용 instance에서 쓰던 값을 올린다 — `pg_prewarm`
(autoprewarm) + `pg_stat_statements`, `shared_buffers=1GB`,
`work_mem=64MB`, `maintenance_work_mem=256MB`, `effective_cache_size=1536MB`, `max_wal_size=2GB`.
값은 compose `command:`의 리터럴이 정본이고 `ALTER SYSTEM`은 쓰지 않는다. autoprewarm의 dump는
모든 database를 덮지만 재기동 때의 reload는 DB OID 순이고 free buffer가 바닥나면 멈춘다 —
가장 나중에 만든 DB(Map이 오면 Map의 둘)가 마지막이고 못 올라올 수 있다(best effort, ADR-53
결정 4). 병렬 hash의 DSM을 위해 `shm_size: 1gb`를 두지만, 지금 도는 512mb·16MB보다 여유는
절반이다(줄일 뿐 없애지 않는다, ADR-53 받아들인 위험). 이미지는 그날 실행 중이던 digest로
고정한다(교체가 아니다).
종료 checkpoint의 grace는 ADR-52의 `stop_grace_period: 300s`가 D4의 요구(≥120초)를 이미
넘는다. 전부 cluster 전역이라 한 테넌트를 위한 변경이 **모든 테넌트의 재기동**이다.

**PinVi의 `pinvi_dagster`는 2단계(`dagster_shared`)가 아니다.** 그 데이터베이스를
**PinVi 전용으로 유지한 채** 물리적으로 공용 instance로 옮긴 것뿐이다 — concierge의
`kor_travel_concierge`처럼 5단계(애플리케이션 DB급 이사)의 연장이지, 여러 프로젝트가
하나의 `dagster_shared`로 통합되는 2단계가 아니다. 그리고 2~4단계는 그 메타DB들을
`dagster_shared`로 **옮기지 않는다** — 이력은 새로 시작한다(아래 D1).

```
11000  PostgreSQL (단일 공용 인스턴스)
         ├ dagster_shared        ← 공용 Dagster instance: run / event log / schedule storage
         ├ kor_travel_map · kor_travel_geo · kor_travel_concierge · pinvi · …
         └ 옛 *_dagster 메타DB   ← 4단계까지 그대로, 그 뒤 접속 차단·보존(D1·D6)
11001  nginx Basic Auth gateway (공용, 공개 host dagster.digitie.mywire.org)
         │   OPNsense HAProxy → 11001. `/health`만 인증 없이, POST는 same-origin 검사
         └→ 127.0.0.1:11002  dagster-webserver (공용, loopback 전용 — gateway 뒤에서만)
(포트 없음)  dagster-daemon (공용) — 나가는 연결뿐(Postgres, code-server gRPC).
             health는 `dagster-daemon liveness-check`(DB heartbeat)

code-server (dagster api grpc)  ← 프로젝트별 분리 유지, 각자 포트
```

**참여 범위.** 공용 plane은 Map·PinVi·geo·weather 넷이다. **transport는 나중에 합류한다** —
빠진 것이 아니라 미뤘다. 그때까지 transport는 자기 webserver·daemon·메타DB
(`kor_travel_transport_dagster`)를 그대로 쓰고, Manager 배포로 옮겨 온 뒤(M-T) 같은 절차로
합류한다. 설계는 그 합류가 재설계 없이 되게 잡았다 — 공용 `dagster.yaml`에
`dagster/code_location=<transport code-server의 -m 모듈>` 상한 3과 `kortraveltransport/run_group`
상한 넷(각 1)을 더하는 것이 전부다(키가 이미 테넌트 이름공간이라 겹치지 않는다).

**포트(D2).** `11001`은 공용 Dagster의 **입구 하나**다 — nginx Basic Auth gateway가 듣고,
HAProxy(OPNsense)가 공개 host `dagster.digitie.mywire.org`를 그리로 보낸다. webserver는
`127.0.0.1:11002`에서만 듣는다(gateway 없이는 인증이 없다 — weather `14102`/`14107`과 같은
배치다). daemon은 포트가 없다. 옛 문서의 "11001 daemon / 11002 webserver"는 틀렸다 — daemon은
아무것도 listen하지 않는다.

**왜 code-server만 나뉘는가.** 프로젝트마다 Python 의존성이 다르므로 한 gRPC
프로세스에 여러 프로젝트 코드를 올릴 수 없다. 반면 webserver와 daemon은 코드를
로드하지 않고 `workspace.yaml`의 `grpc_server` 항목을 통해 각 code-server에 붙는다 —
이것이 Dagster가 공식 지원하는 배치다.

**공유의 전제 둘.**

- 공유 webserver/daemon과 모든 code-server가 **같은 인스턴스 스토리지**를 본다.
  그것이 `11000`의 `dagster_shared`다.
- 공유 프로세스의 dagster 버전이 모든 프로젝트의 code-server와 **호환**해야 한다.
  프로젝트마다 dagster 핀이 다르면 그것이 1차 차단 요인이다.

**선행 작업 순서.** 각 단계는 다음 단계의 전제다.

1. 프로젝트마다 `dagster api grpc` code-server를 **별도 서비스로 분리**한다
   (2026-09-25 Map을 끝으로 다섯 프로젝트 모두 완료, §5). 이 단계까지는 기존 webserver/daemon을 그대로 둔다.
2. 공유 인스턴스 스토리지(`11000`/`dagster_shared`)를 세운다 — db-init이 role·DB를,
   storage migrate one-shot이 schema를 만든다. 프로젝트의 옛 Dagster 메타DB는 **옮기지
   않는다**(D1). 아직 아무것도 이 스토리지를 쓰지 않는다.
3. 공유 `workspace.yaml`에 프로젝트별 `grpc_server`를 나열하고, 공유 webserver
   (`127.0.0.1:11002`)·그 앞의 gateway(`11001`)·daemon(포트 없음)을 세운다. 그 뒤 프로젝트를
   **하나씩** 옮긴다(PinVi → geo → weather → Map). 한 프로젝트를 옮기면 옛 daemon·webserver를
   멈추고, 그 code-server와 소비자(API·UI)를 공용 URL로 돌린다.
   - `grpc_server`의 `location_name`은 그 code-server의 `-m` 모듈이다 — 공용 `dagster.yaml`의
     `dagster/code_location` 상한 값이 그 이름이다(오늘 네 instance의 실측 location 이름과 같다).
     다른 이름을 주면 그 location의 상한이 조용히 사라진다.
   - **전환 판정**: 옮긴 프로젝트의 run이 모두 상한이 걸린 location 값을 단다 —
     `dagster_shared`에서 `SELECT count(*) FROM runs r WHERE r.create_timestamp > <전환 시각> AND NOT
     EXISTS (SELECT 1 FROM run_tags t WHERE t.run_id = r.run_id AND t.key = 'dagster/code_location'
     AND t.value IN (<상한 값들>))`이 0이다. 0이 아니면 되돌린다(상한 없는 run이 전역 12를 먹는다).
4. 프로젝트별 webserver/daemon을 내린다. **이 단계 전까지는 되돌리기가 싸다.**
5. 애플리케이션 DB를 `11000`으로 이사한다 — 프로젝트별 롤·ACL·마이그레이션 원장·
   백업 경로가 전부 따라온다. **가장 비싸고 되돌리기 어려운 단계이므로 마지막이다.**

**이 전환이 뒤집는 것.** ADR-37(프로젝트별 전용 인스턴스)과
[`ports.md`](ports.md)의 "통합 `5432` instance는 폐지되었다" 문장. 전환이 실제로
일어나면 **그 ADR을 새 ADR로 갈음하고 `ports.md`의 `11000` 대역을 추가**해야 한다.
지금 그것을 미리 고치지 않는 이유는, 고쳐 두면 문서가 만들어지지 않은 구조를
현황으로 주장하게 되기 때문이다.

**되돌리기.** 4단계 전까지는 프로젝트별 webserver/daemon이 살아 있으므로 공유
프로세스를 내리는 것으로 끝난다. 5단계 이후는 DB 복원이 필요하다.

**소유자 결정(2026-09-29).**

- **D1 — 이력은 새로 시작한다.** `dagster_shared`는 빈 DB에서 시작하고 옛 메타DB의 run·event·
  tick을 옮기지 않는다. 다섯 메타DB가 모두 같은 Dagster schema지만 이력이 짧고, 합치면 serial
  `event_logs.id`·`asset_keys`·instigator selector·`kvs`가 충돌하며 낡은 QUEUED/STARTED run을
  공용 daemon이 집어 든다. 옛 프로젝트별 `*_dagster` DB는 지우지 않고 4단계 뒤 최종 dump를 뜬 채
  접속을 막아(`ALLOW_CONNECTIONS false`) 보존한다.
- **D3 — 전역 run 상한은 12다.** 이것은 호스트 보호 상한이지 테넌트 손잡이가 아니다(n150의
  부하는 CPU가 아니라 디스크 대기다). 테넌트별 상한은 오늘 값 그대로 `dagster/code_location` tag로
  준다 — Map·PinVi·geo·weather 각 10. (`.dagster/repository`가 아니다 — 그 tag는 run_tags 표에만
  있고 queue daemon이 세는 run 본문에는 없어 상한이 아무것도 막지 않는다. `dagster/code_location`은
  Dagster가 queue로 들어오는 모든 경로 — schedule·sensor·launchpad·asset Materialize·backfill·재실행 —
  에서 run 본문에 넣는다. 2026-09-30 적대 리뷰 H1, 근거는 공용 `dagster.yaml` 주석.) 테넌트 안의 상한도 그대로다(Map
  `kor_travel_map.feature_update_request_id` 4, weather `kortravelweather/run_group=external_weather`
  3; transport는 합류 때 3과 `run_group` 넷). 근거 없이 올리지 않는다.
- **D4 — schedule·sensor의 켜짐/꺼짐은 코드에 선언한다**(`default_status`). 빈 `dagster_shared`는
  DB에만 켜져 있던 instigator를 STOPPED로 올린다 — geo의 셋과 transport의 하나가 그렇다. 그
  프로젝트는 옮기기 **전에** 코드에서 선언한다. GraphQL로 다시 켜는 것은 저장소 밖 상태라 쓰지 않는다.
- **D5 — 로그 표시는 오늘과 같다.** run은 code-server 컨테이너 안에서 돌고 compute log는 그
  컨테이너의 `/opt/dagster/state/compute_logs`에 남는다. webserver는 자기 파일시스템을 읽으므로
  UI의 stdout/stderr는 code-server를 분리한 오늘도 이미 비어 있다 — 공용 plane은 그것을 나쁘게도
  좋게도 하지 않는다. 공유 디렉터리나 오브젝트 스토리지 log manager는 별도 과제다.
- **D6 — soak.** 프로젝트 전환 사이 하루(그 프로젝트 schedule의 일일 주기 하나), 4단계 전 7일(그
  동안 `dagster_shared` 백업이 7일 연속 초록), 옛 메타DB `DROP` 전 30일(dump는 백업 보존 기간대로).

---

## 8. 새 프로젝트가 합류하는 절차

1. **대역을 고른다.** [`ports.md`](ports.md)의 규칙을 따른다 — 100 단위 대역,
   PostgreSQL `+0`, API `+1`, 추가 서비스 `+2`부터, Web UI `+5`. 외부 프로젝트는
   `14000-14099`(weather가 2026-09-20 ADR-47로 internal target이 되며 이 대역을
   떠났고, transport의 전용 DB target은 2026-09-28에 빠졌다 — 남은 것은 `transport`
   (같은 날 `airport`에서 개명)뿐이다).
2. **소유 방식을 정한다.** compose를 Manager에 둘 것인가(내부), 자기 저장소에 둘
   것인가(외부). 배포를 Manager에게 맡길 생각이 없다면 외부가 맞다.
3. **`config/docker-targets.yml`에 등록한다.** 외부면 컨테이너마다
   `external_project`를 단다.
4. **`docs/ports.md` 표에 대역과 포트를 적는다.**
5. `ktdctl targets validate --check-coordinates`를 배포 호스트에서 돌려 선언과 실재를
   대조한다.
6. 자격증명이 필요하면 §6의 세 규약을 지킨다.
7. 두 곳에 같은 사실을 적게 됐다면 [`bindings.md`](bindings.md)에 결박을 등록하거나,
   등록할 수 없으면 그 이유를 남긴다.

---

## 9. 어떤 사실이 어디에 사는가

문서가 낡는 가장 흔한 이유는 같은 사실이 두 곳에 적히는 것이다. 이 표가 정본의 자리다.

| 사실 | 정본 |
|---|---|
| 포트 대역·현재 사용 포트 | [`ports.md`](ports.md) |
| 컨테이너·target 등록 | `config/docker-targets.yml` |
| prod compose (내부 target) | Manager `docker-compose.yml` |
| prod compose (외부 target) | 그 프로젝트 저장소 |
| 공용 Dagster instance 설정·호스트 이미지 버전 | `config/dagster-shared/dagster.yaml` · `docker/dagster-host/requirements.in`(잠금본 `requirements.txt`) |
| Manager 내부 설계 | [`architecture.md`](architecture.md) |
| 결정과 그 근거 | [`decisions.md`](decisions.md) |
| 두 곳에 적힌 사실의 결박 | [`bindings.md`](bindings.md) |
| 배포 절차 | [`prod-deployment.md`](prod-deployment.md) · [`docker-management.md`](docker-management.md) |
| 설치된 Manager revision | 호스트 `/opt/kor-travel-docker-manager/.ktdm-source-revision` |
| 운영 값(키·DSN·핀) | 호스트 `.env` (root 0600) — 저장소에 두지 않는다 |
