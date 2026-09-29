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
것은 webserver/daemon과 메타DB(`dagster_shared`)이고, 그것은 아직 없다(§7 2~4단계).
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
1단계(code-server 분리)도 Dagster를 쓰는 다섯 프로젝트가 모두 밟았다(§5). 2~4단계(공유
Dagster 스토리지 `dagster_shared` · 공용 webserver/daemon · 프로젝트별 daemon 철거)는
여전히 계획이다 — `dagster_shared`도 `11001`/`11002`도 **아직 없다**. 다른 프로젝트가 5단계를 먼저 밟는 절차는
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
하나의 `dagster_shared`로 통합되는 2단계가 아니다. 나중에 2~4단계가 실제로 진행되면
`pinvi_dagster`(그리고 geo·weather·transport의 Dagster 메타DB)도 다시 `dagster_shared`로
옮기는 별도 작업이 필요하다.

```
11000  PostgreSQL (단일 공용 인스턴스)
         ├ dagster_shared        ← run / event log / schedule storage
         ├ kor_travel_map
         ├ kor_travel_geo
         ├ kor_travel_concierge
         └ pinvi
11001  dagster-daemon     (전 프로젝트 공용)
11002  dagster-webserver  (전 프로젝트 공용)

code-server (dagster api grpc)  ← 프로젝트별 분리 유지, 각자 포트
```

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
2. 공유 인스턴스 스토리지(`11000`/`dagster_shared`)를 세우고, 각 프로젝트의 Dagster
   메타DB를 그리로 옮긴다.
3. 공유 `workspace.yaml`에 프로젝트별 `grpc_server`를 나열하고, 공유 webserver(`11002`)
   /daemon(`11001`)을 세운다.
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
| Manager 내부 설계 | [`architecture.md`](architecture.md) |
| 결정과 그 근거 | [`decisions.md`](decisions.md) |
| 두 곳에 적힌 사실의 결박 | [`bindings.md`](bindings.md) |
| 배포 절차 | [`prod-deployment.md`](prod-deployment.md) · [`docker-management.md`](docker-management.md) |
| 설치된 Manager revision | 호스트 `/opt/kor-travel-docker-manager/.ktdm-source-revision` |
| 운영 값(키·DSN·핀) | 호스트 `.env` (root 0600) — 저장소에 두지 않는다 |
