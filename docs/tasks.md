# TASKS — 개발 태스크 백로그

이 문서는 `kor-travel-docker-manager`의 진행 중/대기 작업만 관리한다. 완료된 작업은
[`docs/tasks-done.md`](tasks-done.md)로 분리한다.

- 완료: `[x]`
- 진행 중: `[/]`
- 미진행: `[ ]`

---

## 작업 현황 요약

| 태스크 ID | 작업 항목 | 상태 | 완료 날짜 | 비고 |
|:---|:---|:---:|:---:|:---|
| **T-011** | 설정 저장 안정화 및 validation 고도화 | `[ ]` | - | compose diff, secret `.env` 분리, 입력 검증 보강 |
| **T-012** | 대시보드 상세 패널 확장 | `[ ]` | - | inspect, mounts, networks, redacted env를 UI에 연결 |
| **T-220** | `kor-travel-concierge` provider 상세 구현 및 과거 명칭 제거 | `[x]` | 2026-06-13 | 공식 프로젝트명 전환 완료 |
| **T-221** | `kor-travel-geo` DB명·환경변수·Docker 이름·Prometheus scrape 계약 동기화 | `[x]` | 2026-06-13 | `kor_travel_geo`, `KOR_TRAVEL_GEO_*`, `KTG_*`, `kor-travel-geo-*` 기준 반영 |
| **T-222** | 관측 target 개별 분리 및 포트 재배치 | `[x]` | 2026-06-13 | `gra`, `cadv`, `prom` 분리 및 새 포트 반영 |
| **T-223** | 앱 target 흐름 재정렬 및 실제 컨테이너 빌드 편입 | `[x]` | 2026-06-13 | `geo -> conc -> map -> pinvi`, `srv` 별칭 반영 |
| **T-224** | 과거 서비스명과 공용 인프라 명칭 정리 | `[x]` | 2026-06-15 | Pinvi 및 `kor-travel-*` 기준 반영 |

---

## 진행 순서

1. `tasks.md`와 `tasks-done.md`를 최신 완료/미완료 상태로 정리한다.
2. `kor-travel-concierge`는 `conc`, Pinvi는 `srv` 별칭을 기준으로 안내한다.
3. 다음 앱 target 추가 시 `config/docker-targets.yml`, `docker-compose.yml`, 포트 문서, API/CLI 테스트를 함께 갱신한다.
4. 병행 작업 충돌을 줄이기 위해 각 PR 전후로 `main` rebase를 수행한다.

---

## 태스크 세부 내역

### T-011: 설정 저장 안정화 및 validation 고도화

- [ ] compose 변경 전 diff 생성 및 UI 표시
- [ ] 포트, 볼륨, 네트워크 입력 validation 강화
- [ ] secret 성격 값은 `.env` override로 저장하도록 안내 및 방어 로직 추가
- [ ] 컨테이너 재생성 전 확인 단계와 실패 시 rollback 전략 문서화

### T-012: 대시보드 상세 패널 확장

- [ ] 컨테이너 row 선택 시 inspect 상세 drawer 또는 modal 표시
- [ ] mounts, networks, healthcheck, redacted env를 탭으로 분리
- [ ] target 단위 `ensure --build` 버튼을 개발 모드에서 제공
- [ ] 모바일/데스크톱에서 표와 상세 패널이 겹치지 않도록 반응형 검증

### T-220: `kor-travel-concierge` provider 상세 구현 및 과거 명칭 제거

- [x] `config/docker-targets.yml`의 `ai` target을 `kor-travel-concierge` 기준으로 정리
- [x] 과거 AI provider 명칭 기반 별칭을 제거하고 새 공식 별칭만 남긴다
- [x] 통합 DB 기본값을 `kor_travel_concierge` database 기준으로 정리하고 과거 env fallback을 제거한다
- [x] `pinvi` target이 `kor-travel-concierge`에 직접 의존하지 않도록 문서와 target 설명을 정리
- [x] `krtour-map`과 `kor-travel-concierge` 간 provider 관계만 남도록 아키텍처/포트/관리 문서를 동기화
- [x] 관련 테스트와 설정 검증을 갱신한다

### T-221: `kor-travel-geo` DB명·환경변수·Docker 이름·Prometheus scrape 계약 동기화

- [x] `kor-travel-geo` DB 기본값을 `kor_travel_geo`로 맞춘다
- [x] manager override 변수는 `KOR_TRAVEL_GEO_*`, API/UI 컨테이너 내부 변수는 `KTG_*`로 맞춘다
- [x] Docker service/container 이름을 `kor-travel-geo-*`로 맞춘다
- [x] 물리 데이터 디렉터리를 `/home/digitie/kor-travel-geo-data` 기준으로 맞춘다
- [x] RustFS bucket 기본값을 `kor-travel-geo`로 맞춘다
- [x] Prometheus scrape target에 `kor-travel-geo-api:12501/metrics`와 `kor-travel-geo-ui:12505/api/metrics`를 추가한다
- [x] 관련 문서와 테스트 fixture를 갱신한다

### T-222: 관측 target 개별 분리 및 포트 재배치

- [x] 단일 관측 target을 제거하고 `gra`, `cadv`, `prom` target으로 분리한다
- [x] 관측 target 분리 당시 dependency 순서를 `db -> storage -> gra -> cadv -> prom -> geo -> map -> ai -> main`으로 맞춘다
- [x] Grafana 공용 연계를 위해 `gra`를 RustFS 다음 target으로 배치한다
- [x] Grafana `12205`, cAdvisor `12301`, Prometheus `12401`, `kor-travel-geo` API/Web UI `12501`/`12505` 포트를 반영한다
- [x] CLI/API 테스트와 문서를 새 target/포트 기준으로 갱신한다

### T-223: 앱 target 흐름 재정렬 및 실제 컨테이너 빌드 편입

- [x] dependency 순서를 `db -> storage -> gra -> cadv -> prom -> geo -> conc -> map -> pinvi`로 조정한다
- [x] `kor-travel-concierge` target을 `conc`로 등록하고 API/MCP/Scheduler/Web UI compose service를 추가한다
- [x] `kor-travel-map` target을 `map`에 실제 API/Dagster/Web UI compose service로 연결한다
- [x] Pinvi target을 `pinvi`로 등록하고 `srv`, `main` 별칭을 제공한다
- [x] 공용 DB/RustFS 복구 스크립트에 `krtour_map_dagster` database와 `kor-travel-concierge` bucket 보정을 추가한다
- [x] API/CLI 테스트와 문서를 새 target 흐름에 맞춰 갱신한다

### T-224: 과거 서비스명과 공용 인프라 명칭 정리

- [x] Pinvi 전용 database, role, bucket, 환경변수 기본값을 `pinvi` 및 `PINVI_*` 기준으로 맞춘다
- [x] 공용 RustFS와 관측 컨테이너 이름을 `kor-travel-*` 기준으로 맞춘다
- [x] 문서, 테스트, 설정 파일의 과거 서비스명 잔여 표기를 제거한다
