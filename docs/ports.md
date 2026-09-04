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
| `pinvi` | `12800-12899` | PostgreSQL `12800`, API `12801`, Dagster `12802`, Web UI `12805` | PinVi |
| `kor-travel-docker-manager` | `12900-12999` | Backend `12901`, Dashboard `12905` | Manager |
| `weather` | `14100-14199` | PostgreSQL `14100`, API `14101`, Dagster Gateway `14102`, Dagster 내부 `14106`, Dagster metrics `14103`, Prometheus `14104`, Web UI `14105` | `kor-travel-weather` |

`weather`는 다른 target과 다른 `14100` 대역을 쓴다 — 독립 sibling 저장소가 이미 자기
README·production 도메인(`weather-api`/`weather-dagster`/`weather.digitie.mywire.org`)에서
이 포트로 문서화하고 있어, `12000`대 순번 규칙에 맞춰 재배치하지 않았다. Dagster
원본 webserver(원본 compose는 bridge 네트워크라 겹치지 않았다)는 host 네트워크에서
dagster-gateway의 외부 포트(14102)와 충돌해 내부 전용 `14106`으로 옮겼다 —
gateway와 web의 server-side proxy만 이 포트로 직접 붙고 외부에는 노출하지 않는다.

Concierge scheduler와 Map Dagster daemon은 외부 포트를 열지 않는 내부 실행 서비스다.
Geo Dagster webserver는 registry의 일반 runtime 표에는 없는 보조 서비스지만 Compose에서
`12502`를 사용한다. PinVi의 `srv`와 `main`은 `pinvi` target 별칭이다.

## PostgreSQL instance 경계

| 인스턴스 | 포트 | 데이터베이스 |
|---|---:|---|
| `kor-travel-geo-postgres` | `12500` | `kor_travel_geo`, `kor_travel_geo_dagster` |
| `kor-travel-concierge-postgres` | `12600` | `kor_travel_concierge` |
| `kor-travel-map-postgres` | `12700` | `kor_travel_map`, `kor_travel_map_dagster` |
| `pinvi-postgres` | `12800` | `pinvi` |

네 instance 모두 loopback 전용이다. `db` target의 호환 이름은 Geo instance만 실행하며,
Concierge·Map·PinVi database provisioning은 각 Compose 서비스 또는 pinned workflow가
자기 instance에서 수행한다.

## 변경 절차

새 서비스를 추가할 때는 먼저 `config/docker-targets.yml`의 `dependency_order`, target,
container metadata와 `docker-compose.yml`의 실제 listen 포트를 함께 갱신한다. 이후 API·CLI
registry 테스트와 이 문서를 같은 변경으로 갱신한다. 기존 서비스의 포트는 관련 프로젝트가
공유하므로 임의로 바꾸지 않는다.
