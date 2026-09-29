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
- 표의 값은 host 네트워크 기본값 기준이다. `KTDM_DOCKER_NETWORK_MODE=host`에서는
  컨테이너 내부 프로세스가 호스트 포트에 직접 listen하고 서비스 간 참조는
  `127.0.0.1:<포트>`를 사용한다.

## 대역과 현재 사용 포트

| 대상 | 대역 | 현재 사용 포트 | 관리 대상 |
|---|---:|---|---|
| — | `12000-12099` | 없음 | 비어 있다. 폐지된 통합 instance(ADR-37)와, 그 뒤 geo 전용 instance만 가리키던 `db` target(2026-09-28 폐지)의 자리였다. |
| `storage` | `12100-12199` | S3 API `12101`, console `12105`, **Prometheus `12102`, cAdvisor `12103`, Grafana `12104`**(ADR-48, 대역 예외 — 아래 참고) | RustFS |
| `gra` | `12200-12299` | Web UI `12104`(ADR-48로 `storage` 대역 안으로 재배치, 아래 참고) | Grafana |
| `cadv` | `12300-12399` | Exporter `12103`(ADR-48로 `storage` 대역 안으로 재배치, 아래 참고) | cAdvisor |
| `prom` | `12400-12499` | HTTP `12102`(ADR-48로 `storage` 대역 안으로 재배치, 아래 참고) | Prometheus |
| `geo` | `12500-12599` | API `12501`, Dagster `12502`, Web UI `12505` (DB는 공용 `11000`) | `kor-travel-geo` |
| `conc` | `12600-12699` | API `12601`, MCP `12602`, Web UI `12605` (DB는 공용 `11000`) | `kor-travel-concierge` |
| `map` | `12700-12799` | API `12701`, Dagster `12702`, Web UI `12705`(`12700`은 퇴역한 전용 PostgreSQL의 자리, ADR-53) | `kor-travel-map` |
| `pinvi` | `12800-12899` | API `12801`, Dagster webserver `12802`, Dagster code-server(gRPC, PinVi ADR-069) `12803`, Web UI `12805` (DB는 공용 `11000`) | PinVi |
| `kor-travel-docker-manager` | `12900-12999` | Backend `12901`, Dashboard `12905` | Manager |
| `weather` | `14100-14199` | API `14101`, Dagster 게이트웨이 `14102`(Basic Auth, Dagster webserver 자체는 내부 전용 `14107`), Prometheus `14104`, Web `14105` | `kor-travel-weather` (Manager 내부 target, ADR-47 — 2026-09-20까지 외부 프로젝트였다) |
| `transport` | `14001-14099` | Backend `14001`, Frontend `14002`, Dagster 게이트웨이 `14003`·webserver `14004`·code-server(gRPC) `14005`(셋 다 loopback, Manager 미등록 컨테이너) (DB는 공용 `11000`의 `kor_travel_transport`) | `kor-travel-transport` (외부 프로젝트) |

### `gra`/`cadv`/`prom`의 대역 예외 (ADR-48)

Grafana(`12104`)·cAdvisor(`12103`)·Prometheus(`12102`)는 2026-09-21부터 자신의
target 이름이 가리키는 100단위 대역(`12200-12299`/`12300-12399`/`12400-12499`)이
아니라 `storage` 대역(`12100-12199`) 안의 세 포트를 쓴다. `12101`(S3 API)·`12105`
(console) 사이가 비어 있어 실제 포트 충돌은 없지만, "target 이름 대역 = 실제 포트
대역"이라는 §기본 규칙 전제는 이 세 target에 더 이상 성립하지 않는다 — target 이름
(`gra`/`cadv`/`prom`)과 `config/docker-targets.yml`의 키는 바뀌지 않았고 포트만
옮겼다. 근거·배경은 `docs/decisions.md` ADR-48(ADR-10의 포트 배정 부분을 supersede).

**`cadv` 대역은 비어 있지 않다.** Manager에 등록되지 않은 외부 compose 프로젝트
`kor-travel-transport-admin`(transport 저장소의 `docker-compose.transport-admin.yml`)이
이 대역 안의 `12301`(API 게이트웨이)·`12302`(Dagster 게이트웨이)·`12305`(관리 웹)를
`0.0.0.0`에서 listen한다(2026-09-28 n150 `ss -ltn` 실측). Manager는 이 프로젝트를 모르므로
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

Concierge scheduler와 Map Dagster daemon은 외부 포트를 열지 않는 내부 실행 서비스다.
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
| `kor-travel-shared-postgres` | `11000` | `kor_travel_map`+`kor_travel_map_dagster`(ADR-53), `kor_travel_concierge`(ADR-44), `kor_travel_geo`+`kor_travel_geo_dagster`(ADR-45), `pinvi`+`pinvi_dagster`(ADR-46), `kor_travel_weather`+`kor_travel_weather_dagster`(ADR-47), `kor_travel_transport`+`kor_travel_transport_dagster` — 프로젝트마다 자기 두 database를 소유하는 app role 하나. 합류 절차는 [`shared-postgres-onboarding.md`](shared-postgres-onboarding.md) |

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

## 변경 절차

새 서비스를 추가할 때는 먼저 `config/docker-targets.yml`의 `dependency_order`, target,
container metadata와 `docker-compose.yml`의 실제 listen 포트를 함께 갱신한다. 이후 API·CLI
registry 테스트와 이 문서를 같은 변경으로 갱신한다. 기존 서비스의 포트는 관련 프로젝트가
공유하므로 임의로 바꾸지 않는다.
