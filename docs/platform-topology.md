# platform-topology.md — 플랫폼 전체 구조 (다른 프로젝트를 위한 참조)

이 문서의 독자는 **Manager가 아닌 프로젝트**다. `kor-travel-map`·`kor-travel-geo`·
`kor-travel-concierge`·`pinvi`·`kor-travel-airport`·`kor-travel-weather`, 그리고 앞으로
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
| 예 | `geo` · `conc` · `map` · `pinvi` | `airport` · `airport-db` · `weather` |
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
  kor-travel-weather-api:
    name: kor-travel-weather-api-1      # 실제 컨테이너 이름
    compose_service: api                # 그 프로젝트 compose에서의 서비스 이름
    external_project: kor-travel-weather  # ← 있으면 외부, 없으면 Manager 소유
    role: weather-api
    display_name: Kor Travel Weather API
    connection: "http://127.0.0.1:14101"
    expected_ports: ["14101:14101"]     # 실측과 대조되는 선언

targets:                          # 사람이 다루는 단위 = 한 프로젝트(또는 그 일부)
  weather:
    port_band: "14100-14199"
    depends_on: [...]
    aliases: [...]                # 손에 익은 이름들
    services: [...]               # 내부 target만
    runtime_services: [...]       # 상시 떠 있어야 하는 것
    containers: [...]             # 위 containers의 키
    init_steps: [...]             # idempotent 보정 단계
```

**포트 정책은 이 파일에 없다.** GM-19에서 `port_policy`/`port_band` 블록을 소비하는
코드가 0건임이 확인돼 제거됐다 — 정본은 [`ports.md`](ports.md) 하나다. `port_band`는
target 단위로만 남아 있다.

`ktdctl targets validate --check-coordinates`가 선언과 실재를 대조한다. 기본값이
아닌 이유는 형제 저장소가 **배포 호스트에만** 있기 때문이다. 무조건 돌리면 개발
체크아웃과 CI에서 스키마가 완벽해도 실패한다.

---

## 4. 지금의 데이터 평면 — PostgreSQL

2026-08-17(ADR-37)에 **통합 `5432` 인스턴스를 폐지하고 프로젝트별 전용 인스턴스로
나눴다.** 현재 인스턴스는 여섯이다.

| 인스턴스 | 포트 | 소유 | 담긴 DB |
|---|---:|---|---|
| `kor-travel-geo-postgres` | `12500` | Manager compose | `kor_travel_geo`, `kor_travel_geo_dagster` |
| `kor-travel-concierge-postgres` | `12600` | Manager compose | `kor_travel_concierge` |
| `kor-travel-map-postgres` | `12700` | Manager compose | `kor_travel_map`, `kor_travel_map_dagster` |
| `pinvi-postgres` | `12800` | Manager compose | `pinvi`, `pinvi_dagster` |
| `kor-travel-shared-postgres` | `11000` | Manager compose | `kor_travel_concierge` (ADR-44 공용 제어 평면 instance — concierge가 2026-09-19/20에 이전을 마쳤다. 합류 절차는 [`shared-postgres-onboarding.md`](shared-postgres-onboarding.md)) |
| `kor-travel-weather-db` | `14100`→`5432` | 외부 | — |
| `kor-travel-airport-db` | `14000`→`5432` | 외부 | — |

**애플리케이션 DB와 Dagster 메타DB가 같은 인스턴스 안에 나란히 있다**(geo·map·pinvi,
2026-09-19 PinVi PR #558/#356으로 pinvi도 합류). 이것이 §7의 전환에서 갈라지는
지점이다.

`db` target이 `12000-12099` 대역을 들고 있으면서 실제로는 geo 인스턴스(`12500`)를
가리키는 것은 **폐지된 통합 인스턴스의 빈 자리**를 역사로 남겨 둔 것이다. 새
프로젝트가 이 이름을 재사용하면 안 된다.

---

## 5. 지금의 제어 평면 — Dagster

**`pinvi`와 `geo`가 §7 1단계(code-server 분리)를 충족한 첫 두 프로젝트다**
(둘 다 2026-09-19, 서로 다른 PR이 거의 동시에 착지했다 — PinVi ADR-069/PR
`digitie/pinvi#559`+`#358`, geo는 PR #357). 참조 구현은 `kor-travel-weather`
PR #61. `map`/`conc`의 webserver/daemon은 여전히 각자 `-m <모듈>`로 코드를
**in-process로 직접 로드**한다.

| 프로젝트 | webserver | daemon | code-server(gRPC) | 코드 로드 방식 |
|---|---|---|---|---|
| `pinvi` | `pinvi-dagster` `12802` | `pinvi-dagster-daemon` (포트 없음) | `pinvi-dagster-code-server` `12803` | webserver/daemon → `-w workspace.yaml`(grpc_server), code-server만 `-m pinvi.etl.definitions` |
| `geo` | `kor-travel-geo-dagster` `12502` | `kor-travel-geo-dagster-daemon`(포트 없음) | `kor-travel-geo-dagster-code-server` `12503` | PR #357 — pinvi와 같은 3-분리 형태(상세는 그 PR 참조, 이 문서는 표만 갱신) |
| `map` | `kor-travel-map-dagster` `12702` | `kor-travel-map-dagster-daemon` (포트 없음) | 없음 | `-m kortravelmap.dagster.definitions` |
| `weather` | 내부 전용 + 게이트웨이 `14102` | — | 외부 프로젝트 소유(자체 3-분리, `dagster-code-server`) | 외부 프로젝트 소유 |
| `conc` | 없음 | — | 없음 | — |

> **PinVi가 weather와 다른 점**: weather는 자체 bridge network + 서비스명 DNS로
> code-server에 접속하지만, PinVi는 이 저장소의 compose가 강제하는
> `network_mode: host`라 `workspace.yaml`이 `host: 127.0.0.1`을 쓴다(PinVi
> ADR-069 §결정 2) — `map`/`conc`가 같은 1단계를 밟을 때도 같은 이유로
> 서비스명이 아니라 loopback을 써야 한다. geo도 같은 `network_mode: host`
> compose 안에 있으므로 같은 제약을 받는다(구체적인 접속 방식은 PR #357 확인).

이 배치의 결과가 §7 전환의 전제다: **공유 webserver/daemon으로 가려면 모든 프로젝트가
먼저 code-server를 분리해야 한다.** `pinvi`·`geo`가 그 1단계를 밟았고, 나머지
(`map`/`weather`/`conc`)는 아직이다.

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

**단, 5단계(애플리케이션 DB 이사)는 concierge 하나에 대해 먼저 실행됐다** — 공용
instance `kor-travel-shared-postgres`(`:11000`)가 실제로 떠 있고 `kor_travel_concierge`가
거기 산다(ADR-44, 2026-09-19/20). 1~4단계(code-server 분리 · 공유 Dagster 스토리지
`dagster_shared` · 공용 webserver/daemon · 프로젝트별 daemon 철거)는 여전히 계획이다 —
`dagster_shared`도 `11001`/`11002`도 **아직 없다**. 다른 프로젝트가 5단계를 먼저 밟는
절차는 [`shared-postgres-onboarding.md`](shared-postgres-onboarding.md)가 갖는다.

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
   (지금은 하나도 분리돼 있지 않다). 이 단계까지는 기존 webserver/daemon을 그대로 둔다.
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
   `14000-14199`.
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
