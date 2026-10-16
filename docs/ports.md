# 로컬 포트 정책

이 문서는 `kor-travel-docker-manager`의 현재 Compose·registry 계약을 기준으로 한 로컬
호스트 포트 정본이다. target과 서비스 목록은 [`config/docker-targets.yml`](../config/docker-targets.yml),
실제 listen·환경변수는 [`docker-compose.yml`](../docker-compose.yml)에서 확인한다.

## 기본 규칙

- 로컬 서비스 포트는 `12000`부터 시작하고 target마다 100 단위 대역을 배정한다.
- 일반 API는 대역의 `+1`, 추가 서비스 포트는 `+2`부터, Web UI는 `+5`를 사용한다.
- PostgreSQL은 아래 `11000` 공용 instance 하나다(Map도 ADR-53으로 옮겼다). 대역의 `+0`
  (예: Map `12700`)은 옛 전용 instance의 자리였고 지금은 비어 있다. 통합 `5432` instance는
  폐지되었으며 이 저장소의 현재 Compose는 `5432`를 listen하지 않는다.
- Manager 자체 포트는 별도 `12900-12999` 대역을 사용한다.
- `11000`은 `12000`대 target별 100단위 대역과 별개인 공용 제어 평면 PostgreSQL
  instance(`kor-travel-shared-postgres`, platform-topology.md §7) 전용 포트다.
  특정 target의 100단위 대역에 속하지 않는다 — 한 target이 아니라 공용 instance에
  합류한 프로젝트들(concierge ADR-44, geo ADR-45, PinVi ADR-46, weather ADR-47,
  transport)이 공유하는 자리이기 때문이다. 새 프로젝트가 합류해도 이 포트 자체는
  바뀌지 않는다.
- `11001`·`11002`는 같은 이유로 대역 밖에 두는 **공용 Dagster 제어 평면**의 자리다
  (platform-topology.md §7, 소유자 결정 D2). `11001`은 nginx Basic Auth gateway — 공용 Dagster의
  유일한 입구이고, OPNsense HAProxy가 공개 host `dagster.digitie.mywire.org`를 그리로 보낸다.
  `11002`는 공용 dagster-webserver이고 **`127.0.0.1`에서만** 듣는다(gateway 뒤에서만 닿는다 —
  서버 쪽 소비자인 Map·PinVi·geo API와 weather web의 프록시는 이 loopback 주소에 직접 붙는다).
  공용 dagster-daemon은 포트가 없다. 셋의 정의는 3단계 release(ADR-54)에 있고, n150에서는 창에서
  `KOR_TRAVEL_DAGSTER_UI_PASSWORD`를 넣고 `ensure dagster`를 돌린 뒤에 듣는다. 포트 값은
  `KOR_TRAVEL_DAGSTER_GATEWAY_PORT`·`KOR_TRAVEL_DAGSTER_WEBSERVER_PORT`의 기본값이다. 다른 서비스에
  배정하지 않는다.
- 표의 값은 host 네트워크 기본값 기준이다. `KTDM_DOCKER_NETWORK_MODE=host`에서는
  컨테이너 내부 프로세스가 호스트 포트에 직접 listen하고 서비스 간 참조는
  `127.0.0.1:<포트>`를 사용한다.

## 대역과 현재 사용 포트

| 대상 | 대역 | 현재 사용 포트 | 관리 대상 |
|---|---:|---|---|
| — | `12000-12099` | 없음 | 비어 있다. 폐지된 통합 instance(ADR-37)와, 그 뒤 geo 전용 instance만 가리키던 `db` target(2026-09-28 폐지)의 자리였다. |
| `storage` | `12100-12199` | S3 API `12101`, console `12105`, **Prometheus `12102`, cAdvisor `12103`, Grafana `12104`, node_exporter `12106`**(ADR-48, 대역 예외 — 아래 참고; node_exporter는 아래 절) | RustFS |
| `gra` | `12200-12299` | Web UI `12104`(ADR-48로 `storage` 대역 안으로 재배치, 아래 참고). 대역 안의 `12205`는 **weather Web**이 쓴다(ADR-57, 아래 참고) | Grafana |
| `cadv` | `12300-12399` | Exporter `12103`(ADR-48로 `storage` 대역 안으로 재배치, 아래 참고) | cAdvisor |
| `prom` | `12400-12499` | HTTP `12102`(ADR-48로 `storage` 대역 안으로 재배치, 아래 참고) | Prometheus |
| `geo` | `12500-12599` | API `12501`, Dagster `12502`, Web UI `12505` (DB는 공용 `11000`) | `kor-travel-geo` |
| `conc` | `12600-12699` | API `12601`, MCP `12602`, Dagster code-server gRPC `12603`(loopback, ADR-55), Web UI `12605` (DB는 공용 `11000`) | `kor-travel-concierge` |
| `map` | `12700-12799` | API `12701`, Dagster `12702`, Web UI `12705`(`12700`은 퇴역한 전용 PostgreSQL의 자리, ADR-53) | `kor-travel-map` |
| `pinvi` | `12800-12899` | API `12801`, Dagster webserver `12802`, Dagster code-server(gRPC, PinVi ADR-069) `12803`, Web UI `12805` (DB는 공용 `11000`) | PinVi |
| `kor-travel-docker-manager` | `12900-12999` | Backend `12901`, Dashboard `12905` | Manager |
| `weather` | `14100-14199` | API `14101`, Dagster 게이트웨이 `14102`(Basic Auth, Dagster webserver 자체는 내부 전용 `14107`), Dagster code-server metrics `14103`(`prometheus_client`, `docker-compose.yml:2874` — 2026-10-13 이 표에 등록, 포트 자체는 종전부터 듣고 있었다), Prometheus `14104`, **Web `12205`**(대역 예외 — 아래 참고, ADR-57) | `kor-travel-weather` (Manager 내부 target, ADR-47 — 2026-09-20까지 외부 프로젝트였다) |
| 공용 Dagster(`dagster`) | 대역 밖 | gateway `11001`(`kor-travel-dagster-gateway`, nginx Basic Auth, `0.0.0.0` — 공개 host `dagster.digitie.mywire.org`, `/health`만 무인증), webserver `127.0.0.1:11002`(`kor-travel-dagster-webserver`, loopback 전용), daemon 포트 없음(`kor-travel-dagster-daemon`). storage one-shot 둘(`kor-travel-shared-db-init-dagster`, `kor-travel-dagster-storage-migrate`, DB는 공용 `11000`의 `dagster_shared`) | Manager (platform-topology.md §7, ADR-54) |
| `transport` | `14001-14099` | Backend `14001`, Frontend `14002`, Dagster code-server(gRPC) `14005`(loopback, 공용 Dagster plane의 location `kor-travel-transport`, ADR-54 개정). 옛 전용 Dagster 게이트웨이 `14003`·webserver `14004`는 그 저장소의 `legacy-dagster` profile이라 평소 듣지 않는다 (DB는 공용 `11000`의 `kor_travel_transport`) | `kor-travel-transport` (외부 프로젝트) |

### `gra`/`cadv`/`prom`의 대역 예외 (ADR-48)

Grafana(`12104`)·cAdvisor(`12103`)·Prometheus(`12102`)는 2026-09-21부터 자신의
target 이름이 가리키는 100단위 대역(`12200-12299`/`12300-12399`/`12400-12499`)이
아니라 `storage` 대역(`12100-12199`) 안의 세 포트를 쓴다. `12101`(S3 API)·`12105`
(console) 사이가 비어 있어 실제 포트 충돌은 없지만, "target 이름 대역 = 실제 포트
대역"이라는 §기본 규칙 전제는 이 세 target에 더 이상 성립하지 않는다 — target 이름
(`gra`/`cadv`/`prom`)과 `config/docker-targets.yml`의 키는 바뀌지 않았고 포트만
옮겼다. 근거·배경은 `docs/decisions.md` ADR-48(ADR-10의 포트 배정 부분을 supersede).

### weather Web의 대역 예외 (ADR-57)

weather Web(`kor-travel-weather-web`)은 weather 대역(`14100-14199`)의 `+5`(`14105`)가 아니라
`12205`에서 듣는다(2026-10-08 소유자 결정). OPNsense HAProxy의 weather backend가
`192.168.1.14:12205`를 가리키고, 소유자는 HAProxy 쪽을 그대로 두기로 했다. `12205`는 ADR-48이
Grafana를 `12104`로 옮기며 비운 자리다(`gra` 대역 안이지만 Grafana는 더 이상 거기 없다).
값은 `KOR_TRAVEL_WEATHER_WEB_PORT`의 기본값이다. host 모드에서는 `ports:`가 버려지므로
compose가 이미지 CMD(`--port 14105`)를 같은 모양으로 덮어 이 변수로 포트를 넘긴다 —
`ports:`만 바꾸면 프로세스는 계속 `14105`를 듣는다. `14105`는 이제 비어 있다.
`12200-12299`의 나머지 자리에 새 포트를 배정할 때는 `12205`를 피한다.

**`cadv` 대역은 비어 있지 않다.** Manager에 등록되지 않은 외부 compose 프로젝트
`kor-travel-transport-admin`(transport 저장소의 `docker-compose.transport-admin.yml`)이
이 대역 안의 `12301`(API 게이트웨이)·`12305`(관리 웹)를
`0.0.0.0`에서 listen한다(2026-09-28 n150 `ss -ltn` 실측). 옛 `12302`(transport 전용 Dagster 게이트웨이)는
transport가 공용 Dagster plane에 합류하며 없어졌다(ADR-54 개정 2026-10-02, 그 저장소의 운영 UI 배포가 지운다). Manager는 이 프로젝트를 모르므로
포트 충돌을 알려 주지 않는다 — `12300-12399`에 새 포트를 배정하지 않는다.

### 외부 프로젝트 대역 (`14000-14099`)

위 target(`transport`)은 **compose 정본이 이 저장소 밖**에 있다
(`external_project` 선언 — compose 프로젝트 `kor-travel-transport`, 배포 사본
`/home/digitie/apps/kor-travel-transport`). 등록의 뜻은 좁다:

- Manager가 **상태를 보고**(`status`) **컨테이너 수명주기를 다룬다**
  (`start`/`stop`/`restart` — `control_container`가 Docker SDK로 컨테이너를 직접
  잡으므로 compose 프로젝트와 무관하게 동작한다).
- **배포는 각 저장소가 계속 소유한다.** `ensure`는 외부 target을 거부한다 — Manager의
  C6c 계약 기계(보호값 스캔·볼륨 그래프·단일파일 경계·핀셋)는 Manager 자신의 후보를
  전제하고, 형제 프로젝트의 compose는 그 계약을 받은 적이 없다.

이 target은 2026-09-28까지 `airport`였다(compose 프로젝트와 배포 디렉터리도 그 이름).
transport 저장소가 배포 identity를 `transport`로 바꾸면서 Manager도 target 키·컨테이너
id·실제 컨테이너 이름(`kor-travel-transport-<서비스>-1`)을 함께 바꿨다. 옛 이름은
별칭으로 남기지 않았다 — 옛 이름을 쓰는 호출은 `unknown target`으로 드러난다. 공항 주차
기능 자체는 그대로다(바뀐 것은 배포 이름뿐이다).

같은 날까지는 전용 DB target(`docker-compose.db.yml`, :14000)도 등록돼 있었고 앱 target이
그것에 `depends_on`으로 매달려 `status`가 두 프로젝트를 순서대로 조회했다. 그 instance가
n150에서 사라지고 transport가 공용 instance(`:11000`)로 옮겨 target을 뺐다. 의존 폐포가
두 compose 프로젝트에 걸치는 기능 자체는 코드에 남아 있고, 테스트가 합성 외부 target
쌍(`test-sibling` → `test-sibling-db`)으로 그 경로를 태운다.

호출은 **그 프로젝트의 `working_dir`에서** 돈다. compose가 `-f`를 푸는 기준은
`--project-directory`가 아니라 **cwd**라서, Manager 루트에서 돌리면
`-f docker-compose.yml`이 Manager 자신의 compose를 연다. 그리고 그 호출이 물려받는
환경은 **명시된 allowlist뿐이다**(`PATH`·`HOME`·`USER`·`LANG`·`LC_ALL`·`TMPDIR`·
`XDG_RUNTIME_DIR`과 `DOCKER_*`) — Compose에서 셸 환경은 `.env`보다 **우선**하므로
전부 상속하면 형제 프로젝트의 설정을 조용히 덮어쓴다.

`ktdctl logs <외부 target>`은 **그 target 자신의 프로젝트**만 보여 준다. 여러
프로젝트의 로그는 한 스트림으로 합칠 수 없기 때문이다(특히 `-f`). 폐포가 여러 프로젝트에
걸치면 빠진 프로젝트는 stderr에 한 줄로 알린다.
target이 **자기 프로젝트에 runtime 서비스를 하나도 선언하지 않으면** 거부한다 —
그때 폐포만 남기면 남의 서비스 이름을 Manager compose에 물어보게 된다.

좌표가 이 호스트에 실재하는지는 `ktdctl targets validate --check-coordinates`가 본다.
기본값이 아닌 이유는 형제 저장소가 **배포 호스트에만** 있기 때문이다 — 무조건 돌리면
개발 checkout과 CI에서 스키마가 완벽해도 실패한다.

Manager는 외부 컨테이너의 **compose 설정을 편집하지 않는다.** `compose_service` 이름은
그 프로젝트 안에서만 유일해서, Manager 문서에서 같은 이름을 찾으면 전혀 다른 서비스가
나올 수 있다. 그래서 목록 화면은 외부 컨테이너의 `config`를 비워 보내고, 설정
변경·초기화·부재 시 재생성은 거부한다. 수명주기(start/stop/restart)만 Docker SDK로
동작한다.

**weather는 2026-09-20부터 이 섹션에 없다** — Manager의 own internal target이 됐고
(`config/docker-targets.yml`의 `weather:` target에서 `external_project:` 필드가
빠졌다), compose 정본도 이 저장소의 `docker-compose.yml`로 옮겨왔다
(`kor-travel-weather-api`/`-web`/`-dagster-*`/`-prometheus` + `kor-travel-shared-db-init-weather`
+ `kor-travel-weather-migrate`, ADR-47). weather 자신의 `compose.yaml`/`deploy/compose.n150.yaml`은
삭제되지 않고 local-dev/e2e 용으로 남는다 — prod 정본이 아니라는 뜻만 바뀌었다. weather의
bare `prometheus` compose 서비스명이 Manager 자신의 `prometheus:` 서비스와 겹쳤던 문제는
`kor-travel-weather-prometheus`로 이름을 바꿔 해소했다(외부 target이었을 때는 "config
편집 거부" 특례로 무해했지만, internal target에는 그 특례가 적용되지 않는다).

legacy Concierge scheduler와 Map Dagster daemon은 외부 포트를 열지 않는 내부 실행 서비스다.
Geo Dagster webserver는 registry의 일반 runtime 표에는 없는 보조 서비스지만 Compose에서
`12502`를 사용한다. PinVi의 `srv`와 `main`은 `pinvi` target 별칭이다. PinVi Dagster
code-server(`12803`, PinVi ADR-069)도 daemon과 같은 내부 전용이다 — webserver/daemon만
gRPC로 접속하고, 외부에는 열지 않는다. weather의 Dagster code-server(`14106`)/webserver
(`14107`, ADR-47로 재배치)도 같은 이유로 loopback 전용이다 — gateway(`14102`)만 외부에 연다.
Map Dagster code-server(`12703`, #397)도 `-h 127.0.0.1` loopback 전용이다. Geo Dagster
code-server는 `12503`이다.

## PostgreSQL instance 경계

| 인스턴스 | 포트 | 데이터베이스 |
|---|---:|---|
| `kor-travel-shared-postgres` | `11000` | `kor_travel_map`+`kor_travel_map_dagster`(ADR-53), `kor_travel_concierge`(ADR-44), `kor_travel_geo`+`kor_travel_geo_dagster`(ADR-45), `pinvi`+`pinvi_dagster`(ADR-46), `kor_travel_weather`+`kor_travel_weather_dagster`(ADR-47), `kor_travel_transport`+`kor_travel_transport_dagster` — 프로젝트마다 자기 두 database를 소유하는 app role 하나. 공용 Dagster instance의 `dagster_shared`(소유 role `kor_travel_dagster_shared_app`, platform-topology.md §7 2단계 — Manager 설치 뒤 창의 `ensure dagster`가 만든다). 합류 절차는 [`shared-postgres-onboarding.md`](shared-postgres-onboarding.md) |

loopback 전용이다. Map database provisioning은 pinned workflow가 이 instance의 admin으로
Map one-shot을 돌려 수행하고(ADR-53 S1 — Map은 db-init이 없다), 나머지 프로젝트는 각자의
`kor-travel-shared-db-init-*` one-shot이 공용 instance에서 role·database를 만든다. 공용
instance 안에서도 ADR-37의 교훈(role·ACL은 database가 아니라 cluster 전역)을 지켜,
프로젝트마다 자기 database에만 권한을 갖는 전용 role을 쓴다 — cluster 관리자 계정은 앱에
노출하지 않는다.

**튜닝 (ADR-53 D4).** 공용 instance는 Map의 튜닝(`shared_buffers=1GB`, `work_mem=64MB`,
autoprewarm 등)으로 돈다. 포트는 그대로 `11000`이고, 값은 compose `command:`의 리터럴이
정본이다 — 전부 cluster 전역이라 바꾸면 모든 테넌트가 재기동을 겪는다. 목록은
[`platform-topology.md`](platform-topology.md) §7.

**옛 전용 instance의 퇴역(2026-09-28).** geo(`kor-travel-geo-postgres`, `:12500`)·
concierge(`kor-travel-concierge-postgres`, `:12600`)·PinVi(`pinvi-postgres`, `:12800`)는
cutover 뒤 롤백 안전망으로 compose에 남아 있었다. 그날 n150 실측으로 모든 서비스의 DSN이
`:11000`(Map만 `:12700`)을 가리켰고, 남아 있던 옛 instance는 접속 0인 `pinvi-postgres`
하나였다. 그래서 그 셋과 one-shot(`kor-travel-concierge-db-init`·`pinvi-db-init`·
`kor-travel-geo-dagster-db-init`)을 compose·targets·백업·C6c 계약에서 뺐다. 데이터
디렉터리는 호스트에 그대로 둔다. weather는 Manager가 관리하는 전용 instance를 가진 적이
없고, 옛 transport(당시 이름 airport) 전용 DB(`:14000`)도 이미 사라졌다.

## 호스트 규칙 (계획 — ADR-58 proposed; `hosts:`·workspace 필터·소비자 origin만 2026-10-14 잠정 시행)

2026-10-13 현재 모든 서비스는 n150(`192.168.1.14`) 한 호스트에 있다. ADR-58은 geo·weather·transport와
그들의 PostgreSQL·Dagster plane을 odroid(`192.168.1.13`)로 옮기되 **이 문서의 포트는 하나도 바꾸지
않는다**는 것을 결정했다. 호스트가 둘이 되면 아래 규칙이 더해진다.

- **포트 정책은 호스트 로컬이다.** 두 호스트가 `11000`(PG)·`11001`/`11002`(plane)·`12101`/`12105`
  (RustFS)·`12103`(cAdvisor)·`12901`/`12905`(Manager)를 **각각** 가진다. 충돌이 아니다 — 같은 역할의
  인스턴스가 호스트마다 하나다.
- **`host = hosts[<target.host>].addr`, 같은 호스트 안 참조는 `127.0.0.1`.** 주소의 정본은
  registry의 `hosts:` 절(계획)이다 — 이 문서에 적힌 `.13`/`.14`는 그 파생값의 **예시**이지 정본이 아니다.
  n150의 Map/PinVi가 odroid의 geo/transport/weather를 부를 때만 `http://<hosts[odroid].addr>:<소비자 포트>`
  (오늘 값 `192.168.1.13`)이며, 그 값은 Manager compose env 기본값(커밋된 파생물, `bindings.md` B-6)에서 나온다.
  **소비자 포트는 2026-10-14 소유자가 odroid에서 공개 대역으로 통일했다**: geo `12501`(불변), weather **`12201`**
  (n150의 `14101`이 아니다), transport는 backend `14001`이 아니라 **API gateway `12301`**(gateway가
  `/v1/service/exports/*`를 n150 `192.168.1.14`에만 연다). 그래서 아래 "포트를 바꾸지 않는다"는 이 세 소비자
  origin에는 적용되지 않는다(ADR-58 "잠정 시행(2026-10-14)").
- **호스트가 바뀌어도 포트는 바꾸지 않는다.** 포트 재배정이 필요해 보이면 그것은 호스트 규칙이 아니라
  이 문서의 대역 규칙 문제다.
- **바인드 주소도 바꾸지 않는다.** RustFS `0.0.0.0`, exporter `0.0.0.0`, Manager `0.0.0.0`, loopback
  전용(`11000`·`11002`·code-server gRPC)은 그대로다. host network라 포트는 방화벽 경계가 아니므로(ADR-48)
  경계는 인증·CIDR·**odroid 호스트 방화벽**이다.

### odroid 점유 포트 × 우리 포트 집합 — 충돌 0 (2026-10-13 실측, TCP+UDP)

우리 집합은 전부 `11000-14106` 대역의 **TCP**다(11000/11001/11002, 12101/12105, 12103, 12205, 12301/12305,
12501/12503/12505, 12901/12905, 14001/14002/14005, 14101/14103/14104/14106 + node_exporter 신설). odroid가
오늘 듣는 것: TCP 22·139·445·1994·2121·3000·6080·8000·8080·9443·18000·32400·32469, UDP 137/138·1900/1901·
32410/32412-32414·(소유 미상 6개: 9662·12129·14367·48244·59870·60025), loopback 32401/32600/33083/37063·
127.0.0.53:53. **TCP 점유 포트 중 우리 대역 안은 없고**, UDP 12129·14367은 숫자가 대역 안이지만 프로토콜이
달라 충돌이 아니다. 따라서 "포트 동일" 규칙은 odroid에서 그대로 성립한다. 폐기 대상(parking-radar
3000/8000/18000, Portainer 9443)을 빼도 같다.

### node_exporter

호스트 swap·메모리·디스크 여유·시계 동기 상태가 ADR-58의 핵심 위험이라 n150·odroid 양쪽에 node_exporter를
둔다. 포트는 **`12106`**(`storage` 대역 안, ADR-48의 세 관측 포트 `12102`~`12104` 옆; `12205`는 weather Web이라
피한다).

- **odroid: 2026-10-14 기동**(소유자 지시, 임시 최소 반영). Manager 설치 전이라 저장소 밖 호스트 파일
  `/opt/kor-travel-odroid/compose-mon.yml`(project `kor-travel-odroid-monitoring`)로 cAdvisor `12103`과 함께 띄웠다.
  registry 좌표는 2026-10-17(T-MGR-NH NH-5)부터 호스트 이름 없는 `/opt/kor-travel-host-monitoring`·`kor-travel-host-monitoring`이다
  — 호스트 측 개명은 T-OPS-01b에서 한다(그 전까지 odroid의 `targets validate --check-coordinates`는 이 좌표를 없다고 보고한다).
  n150 Prometheus가 `cadvisor-odroid`·`node-odroid` job(`labels: host=odroid`)으로 수집한다 —
  `config/prometheus/prometheus.yml`의 주소는 2026-10-16부터 registry 파생물이다(T-MGR-04 — job 이름은 컨테이너 id
  `cadvisor`·`kor-travel-node-exporter`, 호스트는 `host` label). **odroid 방화벽은 아직 없다**
  (INPUT ACCEPT — `prod-deployment.md` §9.1 단계 7 미실시): exporter 집합의 "n150 addr만" 경계(아래 표)는 그 단계 전까지
  성립하지 않는다. cAdvisor의 노출 수준은 n150과 같고(`0.0.0.0:12103`, privileged), node_exporter는 새 표면이다(읽기 전용).
- n150: **두지 않는다(2026-10-16 T-MGR-04 판단)**. 디스크 여유를 보려면 호스트 `/`를 읽기 전용으로 마운트해야 하는데
  Manager compose에 넣으면 C6c 후보 계약의 bind 경계(금지 source `/etc`·`/proc`·`/sys`… 의 상위)에 새 예외가 필요하다 —
  단순성 기준으로 넣지 않는다. odroid의 호스트 측 stack은 registry `node` target(외부 프로젝트
  `kor-travel-host-monitoring`, `host: odroid`)으로 등록했고 중앙 Prometheus 타깃은 그 등록에서 파생한다(B-8). n150에도
  같은 호스트 측 stack을 두면 `node`의 `host:`만 넓힌다.

### plane별 공개 host (계획)

| plane | gateway | 공개 host |
|---|---|---|
| n150 공용 plane | `192.168.1.14:11001` | `dagster.digitie.mywire.org`(불변) |
| odroid plane | `192.168.1.13:11001` | 소유자 결정 §14-4 — `weather-dagster.`/`geo-dagster.` 재사용(지금은 ADR-54 redirect) 또는 새 이름. `KTDM_PROD_URL_DAGSTER`는 호스트마다 **다른 값** |

HAProxy backend 전환표(geo/weather/transport/pr → `.13`)는 `prod-deployment.md` §9.4.

### odroid 호스트 방화벽 집합 (계획 — 정책 (가): 우리 포트만 제한, 그 외는 accept)

| 집합 | 포트 | 허용 소스 |
|---|---|---|
| consumer/공개 API | `12501`, `12201`(weather — 2026-10-14 통일, 옛 `14101`), `12301`(transport API gateway — n150 소비자는 backend `14001`이 아니라 이것을 부른다) | n150 addr + HAProxy(OPNsense) LAN addr(§14-6) — 그 외 LAN drop |
| HAProxy 웹 backend | `12505`, `12205`, `14002`, `12301`, `12305`, `11001` | HAProxy addr만 |
| exporter | `12103`, node_exporter, `14103`, `14104`, `12901` | n150 addr만 |
| 관리 | `22`(키만), `12905`(§14-29 — (가) backend만이면 odroid에 리스너 없음, **추천**) | 22: LAN(키만은 sshd 설정); 12905: (나)일 때만 n150 addr + 운영자 PC addr(§14-5) — 그때는 `12901`도 같은 소스에 열어야 대시보드가 자기 API에 닿는다 |
| 로컬 전용 | `11000`, `11002`, `12503`/`14106`/`14005`, `12101`/`12105` | LAN 유입 drop(bind는 서비스별로 다름 — 바꾸지 않는다) |
| 보존 서비스(Plex·Samba·webvnc 등 TCP+UDP) | 열거하지 않음 | **정책 accept(변경 0)** — 기본 DROP + 허용 열거(나)는 누락이 곧 Plex 검색·NetBIOS 단절이라 소유자가 원할 때만(§14-28) |

두 안 공통: `flush ruleset` 금지(`table inet ktm`만 flush — 기본 `/etc/nftables.conf`의 `flush ruleset`은 Docker nat/filter를
지워 bridge 컨테이너(Plex 등)의 포트 공개를 죽인다), `nftables.service` enable 시 docker.service보다 먼저인지 확인, 재부팅
전후 `nc` 매트릭스 + `iptables -t nat -S DOCKER` 존재 단언. n150 방화벽에 새 inbound는 없다.

## 변경 절차

새 서비스를 추가할 때는 먼저 `config/docker-targets.yml`의 `dependency_order`, target,
container metadata와 `docker-compose.yml`의 실제 listen 포트를 함께 갱신한다. 이후 API·CLI
registry 테스트와 이 문서를 같은 변경으로 갱신한다. 기존 서비스의 포트는 관련 프로젝트가
공유하므로 임의로 바꾸지 않는다. 서비스를 다른 호스트로 옮길 때도 포트는 바꾸지 않는다
(위 "호스트 규칙") — 바뀌는 것은 registry의 `host:`와 그것에서 파생되는 consumer origin뿐이다.
