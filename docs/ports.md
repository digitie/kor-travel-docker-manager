# 로컬 포트 정책

이 문서는 `kor-travel-docker-manager`의 현재 Compose·registry 계약을 기준으로 한 로컬
호스트 포트 정본이다. target과 서비스 목록은 [`config/docker-targets.yml`](../config/docker-targets.yml),
실제 listen·환경변수는 [`docker-compose.yml`](../docker-compose.yml)에서 확인한다.

## 기본 규칙

- 로컬 서비스 포트는 `12000`부터 시작하고 target마다 100 단위 대역을 배정한다.
- 일반 API는 대역의 `+1`, 추가 서비스 포트는 `+2`부터, Web UI는 `+5`를 사용한다.
- PostgreSQL은 프로젝트별 전용 instance를 사용하고 대역의 `+0`을 쓴다(ADR-37). 통합
  `5432` instance는 폐지되었으며 이 저장소의 현재 Compose는 `5432`를 listen하지 않는다.
- Manager 자체 포트는 별도 `12900-12999` 대역을 사용한다.
- `11000`은 `12000`대 target별 100단위 대역과 별개인 공용 제어 평면 PostgreSQL
  instance(`kor-travel-shared-postgres`, platform-topology.md §7) 전용 포트다.
  특정 target의 100단위 대역에 속하지 않는다 — 한 target이 아니라 공용 instance에
  합류한 프로젝트들이 공유하는 자리이기 때문이다(이전을 마친 concierge, ADR-44; role/
  database만 만들고 data cutover는 아직인 geo, ADR-45). 새 프로젝트가 합류해도 이
  포트 자체는 바뀌지 않는다.
- 표의 값은 host 네트워크 기본값 기준이다. `KTDM_DOCKER_NETWORK_MODE=host`에서는
  컨테이너 내부 프로세스가 호스트 포트에 직접 listen하고 서비스 간 참조는
  `127.0.0.1:<포트>`를 사용한다.

## 대역과 현재 사용 포트

| 대상 | 대역 | 현재 사용 포트 | 관리 대상 |
|---|---:|---|---|
| `db` | `12000-12099` | 없음 | 과거 통합 DB target의 호환 이름. 실제 Geo DB는 `12500`이다. |
| `storage` | `12100-12199` | S3 API `12101`, console `12105` | RustFS |
| `gra` | `12200-12299` | Web UI `12205` | Grafana |
| `cadv` | `12300-12399` | Exporter `12301` | cAdvisor |
| `prom` | `12400-12499` | HTTP `12401` | Prometheus |
| `geo` | `12500-12599` | PostgreSQL `12500`, API `12501`, Dagster `12502`, Web UI `12505` | `kor-travel-geo` |
| `conc` | `12600-12699` | PostgreSQL `12600`, API `12601`, MCP `12602`, Web UI `12605` | `kor-travel-concierge` |
| `map` | `12700-12799` | PostgreSQL `12700`, API `12701`, Dagster `12702`, Web UI `12705` | `kor-travel-map` |
| `pinvi` | `12800-12899` | PostgreSQL `12800`, API `12801`, Dagster webserver `12802`, Dagster code-server(gRPC, PinVi ADR-069) `12803`, Web UI `12805` | PinVi |
| `kor-travel-docker-manager` | `12900-12999` | Backend `12901`, Dashboard `12905` | Manager |
| `weather` | `14100-14199` | API `14101`, Dagster 게이트웨이 `14102`(Basic Auth, Dagster webserver 자체는 내부 전용 `14107`), Prometheus `14104`, Web `14105` | `kor-travel-weather` (Manager 내부 target, ADR-47 — 2026-09-20까지 외부 프로젝트였다) |
| `airport-db` | `14000-14000` | PostgreSQL `14000` | `kor-travel-airport` (외부 프로젝트) |
| `airport` | `14001-14099` | Backend `14001`, Frontend `14002` | `kor-travel-airport` (외부 프로젝트) |

### 외부 프로젝트 대역 (`14000-14099`)

위 두 target(`airport-db`, `airport`)은 **compose 정본이 이 저장소 밖**에 있다
(`external_project` 선언). 등록의 뜻은 좁다:

- Manager가 **상태를 보고**(`status`) **컨테이너 수명주기를 다룬다**
  (`start`/`stop`/`restart` — `control_container`가 Docker SDK로 컨테이너를 직접
  잡으므로 compose 프로젝트와 무관하게 동작한다).
- **배포는 각 저장소가 계속 소유한다.** `ensure`는 외부 target을 거부한다 — Manager의
  C6c 계약 기계(보호값 스캔·볼륨 그래프·단일파일 경계·핀셋)는 Manager 자신의 후보를
  전제하고, 형제 프로젝트의 compose는 그 계약을 받은 적이 없다.

`airport`이 두 target인 것은 실제로 **compose 프로젝트가 둘**이기 때문이다
(`docker-compose.yml`과 `docker-compose.db.yml`이 각각 `kor-travel-airport`와
`kor-travel-airport-db` 프로젝트로 돈다). `airport`이 `airport-db`에 `depends_on`으로
매달려 있어 `status airport`는 두 프로젝트를 순서대로 조회한다.

호출은 **그 프로젝트의 `working_dir`에서** 돈다. compose가 `-f`를 푸는 기준은
`--project-directory`가 아니라 **cwd**라서, Manager 루트에서 돌리면
`-f docker-compose.yml`이 Manager 자신의 compose를 연다. 그리고 그 호출이 물려받는
환경은 **명시된 allowlist뿐이다**(`PATH`·`HOME`·`USER`·`LANG`·`LC_ALL`·`TMPDIR`·
`XDG_RUNTIME_DIR`과 `DOCKER_*`) — Compose에서 셸 환경은 `.env`보다 **우선**하므로
전부 상속하면 형제 프로젝트의 설정을 조용히 덮어쓴다.

`ktdctl logs <외부 target>`은 **그 target 자신의 프로젝트**만 보여 준다. 여러
프로젝트의 로그는 한 스트림으로 합칠 수 없고(특히 `-f`), `airport`은 `airport-db`에
의존하므로 폐포가 항상 두 프로젝트에 걸친다. 빠진 프로젝트는 stderr에 한 줄로 알린다.
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

## PostgreSQL instance 경계

| 인스턴스 | 포트 | 데이터베이스 |
|---|---:|---|
| `kor-travel-geo-postgres` | `12500` | `kor_travel_geo`, `kor_travel_geo_dagster`(**cutover 전까지는 활성 원본**, 이후 롤백 보관용 — 아래 참고) |
| `kor-travel-concierge-postgres` | `12600` | `kor_travel_concierge`(**cutover 전까지는 활성 원본**, 이후 롤백 보관용 — 아래 참고) |
| `kor-travel-map-postgres` | `12700` | `kor_travel_map`, `kor_travel_map_dagster` |
| `pinvi-postgres` | `12800` | `pinvi`, `pinvi_dagster`(**롤백 안전망** — ADR-46 이후 앱은 여기 쓰지 않는다) |
| `kor-travel-shared-postgres` | `11000` | `kor_travel_concierge`(concierge 전용 role — ADR-44 cutover 완료로 **현재 활성**), `kor_travel_geo`/`kor_travel_geo_dagster`(geo 전용 role `kor_travel_geo_app` — ADR-45 role/database 생성 완료, data cutover는 아직이라 **활성 아님**), `pinvi`+`pinvi_dagster`(ADR-46, 데이터 보존 없이 fresh 구성으로 이전해 **현재 활성**), `kor_travel_weather`+`kor_travel_weather_dagster`(weather 전용 role `kor_travel_weather_app`/`kor_travel_weather_dagster_app` — ADR-47, 데이터 보존 없이 fresh 구성으로 internal target 전환해 **현재 활성**). 합류 절차는 [`shared-postgres-onboarding.md`](shared-postgres-onboarding.md) |

다섯 instance 모두 loopback 전용이다. `db` target의 호환 이름은 Geo instance만 실행하며,
Map database provisioning은 각 Compose 서비스 또는 pinned workflow가 자기 instance에서
수행한다. PinVi의 provisioning은 `kor-travel-shared-db-init-pinvi` +
`pinvi-shared-db-runtime-role`(아래 참고)이 공용 instance에서 수행한다.

`kor-travel-shared-postgres`는 platform-topology.md §7(2026-09-19 결정)이 목표로 한
공용 제어 평면의 첫 실제 구현이다. **이전 대상은 concierge·geo·PinVi·weather다**
(ADR-44/ADR-45/ADR-46/ADR-47) — map은 여전히 전용 instance에 남고, 이 문서가 그
프로젝트의 이전까지 끝났다고 주장하지 않는다.

- **concierge**는 2026-09-19/20에 실제 데이터 cutover까지 끝났다(위 표의 "현재
  활성"이 그 사실을 반영). §7의 원래 계획대로 실 데이터를 pg_dump/restore로 옮긴
  hard cutover였다.
- **geo는 아직 role/database가 만들어진 단계다** — 앱 DB(`kor_travel_geo`)와 Dagster
  메타 DB(`kor_travel_geo_dagster`) 둘 다 옮길 예정이고, geo의
  webserver/daemon/code-server 3-프로세스 토폴로지(T-307) 자체는 바뀌지 않고 DSN만
  새 instance를 가리키게 될 것이지만, 실제 데이터 cutover
  (`kor-travel-geo-postgres`→`kor-travel-shared-postgres`)와 DSN 전환은 아직 별도
  배포 단계로 남아 있다 — 합류 전 확인·절차는
  [`shared-postgres-onboarding.md`](shared-postgres-onboarding.md)를 따른다. geo의
  cutover가 끝나면 위 표의 "활성 아님" 표시가 이 사실을 반영해 갱신된다.
- **PinVi는 concierge·geo와 다르다** — 사용자 지시로 데이터 보존을 요구하지 않아,
  옛 `pinvi`/`pinvi_dagster`의 데이터를 옮기지 않고 공용 instance에 fresh
  상태로(M05 role topology를 처음부터 재구성) 만들었다. 옛 instance
  (`pinvi-postgres`)는 삭제하지 않고 롤백 안전망으로 그대로 둔다(쓰기 대상 아님,
  읽기도 정상 운영에서는 쓰지 않는다) — 단, 데이터 자체는 cutover 시점 이후로
  갱신되지 않으므로 "롤백"은 그 시점 데이터로 되돌아간다는 뜻이다. PinVi는 이미
  자체 M05 role topology(`bootstrap-pinvi-runtime-role.sh`)를 갖고 있어, 공용
  instance에서도 같은 스크립트로 같은 role 분리(app/schema-owner/migration-owner/
  migrator)를 재구성했다 — root bootstrap 계정만 전용 instance 자신의 superuser
  (`PINVI_POSTGRES_USER`)에서 공용 cluster 관리자(`KOR_TRAVEL_SHARED_POSTGRES_USER`)로
  바뀐다.
- **weather는 PinVi와 같은 패턴이다** — 사용자 지시로 데이터 보존을 요구하지
  않아("어차피 새로 쌓으면 됨"), 옛 전용 `db`(`weather-postgres` volume, weather
  자신의 compose.yaml에만 남는다)의 데이터를 옮기지 않고 공용 instance에 완전히
  빈 상태로 `kor_travel_weather`(앱)·`kor_travel_weather_dagster`(Dagster 메타)를
  새로 만든다. weather는 concierge/geo/pinvi와 달리 **Manager가 관리하는 전용
  postgres 컨테이너를 애초에 가진 적이 없다** — 그래서 롤백 안전망으로 옛 전용
  instance를 그대로 남겨 두는 나머지 셋의 패턴이 여기는 적용되지 않는다; 롤백은
  weather 자신의 `compose.yaml`(local-dev/e2e로 격하됐지만 삭제되지 않은)을 다시
  띄우는 것이고, 그 안의 `db` 서비스/볼륨이 그 역할을 대신한다.

cutover 후에도 옛 instance는 삭제하지 않고 롤백 안전망으로 그대로 둔다(쓰기 대상
아님, 읽기도 정상 운영에서는 쓰지 않는다). 공용 instance 안에서도 ADR-37의 교훈
(role·ACL은 database가 아니라 cluster 전역)을 지켜, 프로젝트마다 자기 database
하나에만 권한을 갖는 전용 role을 새로 만든다 — cluster 관리자 계정은 앱에 노출하지
않는다.

## 변경 절차

새 서비스를 추가할 때는 먼저 `config/docker-targets.yml`의 `dependency_order`, target,
container metadata와 `docker-compose.yml`의 실제 listen 포트를 함께 갱신한다. 이후 API·CLI
registry 테스트와 이 문서를 같은 변경으로 갱신한다. 기존 서비스의 포트는 관련 프로젝트가
공유하므로 임의로 바꾸지 않는다.
