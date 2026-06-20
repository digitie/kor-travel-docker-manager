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
| **T-013** | 운영(prod) 공개 주소 `.env` 주입 및 CORS 환경변수화 | `[x]` | 2026-06-20 | 도메인 비노출, `KTDM_CORS_ALLOW_ORIGINS`, 프론트 환경파일 분리 |
| **T-014** | Docker host 네트워크 전환·컨테이너=호스트 포트·서비스 prod URL·pinvi-dagster·tripmate 정리 | `[x]` | 2026-06-20 | `KTDM_DOCKER_NETWORK_MODE=host`, 12802, `KTDM_PROD_URL_*`, `ktd_venv` |
| **T-015** | 프론트 Tailwind v4 + StyleSeed 전면 전환·전역 오류 복구 boundary | `[x]` | 2026-06-20 | geo PR #391 반영, `@theme` 토큰, `DESIGN-RULES.md` |
| **T-012** | 대시보드 상세 패널 확장 | `[ ]` | - | inspect, mounts, networks, redacted env를 UI에 연결 |
| **T-220** | `kor-travel-concierge` provider 상세 구현 및 과거 명칭 제거 | `[x]` | 2026-06-13 | 공식 프로젝트명 전환 완료 |
| **T-221** | `kor-travel-geo` DB명·환경변수·Docker 이름·Prometheus scrape 계약 동기화 | `[x]` | 2026-06-13 | `kor_travel_geo`, `KOR_TRAVEL_GEO_*`, `KTG_*`, `kor-travel-geo-*` 기준 반영 |
| **T-222** | 관측 target 개별 분리 및 포트 재배치 | `[x]` | 2026-06-13 | `gra`, `cadv`, `prom` 분리 및 새 포트 반영 |
| **T-223** | 앱 target 흐름 재정렬 및 실제 컨테이너 빌드 편입 | `[x]` | 2026-06-13 | `geo -> conc -> map -> pinvi`, `srv` 별칭 반영 |
| **T-224** | 과거 서비스명과 공용 인프라 명칭 정리 | `[x]` | 2026-06-15 | PinVi 및 `kor-travel-*` 기준 반영 |

---

## 진행 순서

1. `tasks.md`와 `tasks-done.md`를 최신 완료/미완료 상태로 정리한다.
2. `kor-travel-concierge`는 `conc`, PinVi는 `srv` 별칭을 기준으로 안내한다.
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

### T-013: 운영(prod) 공개 주소 `.env` 주입 및 CORS 환경변수화

- [x] 백엔드 CORS 허용 Origin을 `KTDM_CORS_ALLOW_ORIGINS`(콤마 구분, 기본 `*`)로 제어하고 기동 시 루트 `.env`를 로드한다
- [x] 프론트엔드 백엔드 주소를 `.env.development`/`.env.production`로 분리하고 `.env.local` 섀도잉을 제거한다
- [x] 실제 운영 도메인은 gitignore된 `.env`/`frontend/.env.production`에만 두고 `.env.example`은 플레이스홀더로 문서화한다
- [x] 백엔드 ruff·CORS 파싱, 프론트 type-check·prod 빌드(인라인) 검증

### T-014: Docker host 네트워크 전환·컨테이너=호스트 포트·서비스 prod URL·pinvi-dagster·tripmate 정리

- [x] dev 기본 네트워크를 `network_mode: ${KTDM_DOCKER_NETWORK_MODE:-host}`로 전환하고 인프라/앱이 호스트 정규 포트에 직접 바인딩하도록 맞춘다
- [x] 서비스 간 참조(DSN/RustFS/내부 API/Dagster)를 `127.0.0.1:<포트>`로, Prometheus scrape·rustfs-init 엔드포인트도 동기화한다
- [x] geo/concierge/map/pinvi 컨테이너 내부 포트를 호스트 포트와 동일하게 통일한다
- [x] `pinvi-dagster`(12802)를 compose/registry/`pinvi` target에 추가하고 PinVi `apps/etl/Dockerfile`을 신규 작성한다
- [x] 관리 16개 서비스의 prod 공개 URL을 `KTDM_PROD_URL_*`(.env, 비노출)·`prod_url_env`로 주입해 대시보드 `public_url`로 표시한다
- [x] tripmate 로컬 잔재 정리(`pinvi_metrics.db` 개명, `ktd_venv` 재생성)
- [x] `docker compose config`·백엔드 ruff·프론트 type-check/build 검증 및 문서 동기화

### T-015: 프론트 Tailwind v4 + StyleSeed 전면 전환·전역 오류 복구 boundary

- [x] `kor-travel-geo` PR #391의 오류 복구 boundary(error/global-error/AppErrorPanel/error-recovery)를 매니저에 반영
- [x] Tailwind v4 전환(`@import`+`@theme`, `@tailwindcss/postcss`, autoprefixer/tailwind.config 제거)
- [x] `kor-travel-geo-ui/docs/DESIGN-RULES.md`의 StyleSeed 라이트 토큰을 `@theme`에 정의
- [x] `DashboardClient`·`AppErrorPanel`을 Pure Black → StyleSeed 토큰으로 전면 리스타일
- [x] `docs/DESIGN-RULES.md` 포팅, `DESIGN.md` superseded 안내, ADR-17, 프론트 type-check/build 검증

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
- [x] PinVi target을 `pinvi`로 등록하고 `srv`, `main` 별칭을 제공한다
- [x] 공용 DB/RustFS 복구 스크립트에 `krtour_map_dagster` database와 `kor-travel-concierge` bucket 보정을 추가한다
- [x] API/CLI 테스트와 문서를 새 target 흐름에 맞춰 갱신한다

### T-224: 과거 서비스명과 공용 인프라 명칭 정리

- [x] PinVi 전용 database, role, bucket, 환경변수 기본값을 `pinvi` 및 `PINVI_*` 기준으로 맞춘다
- [x] 공용 RustFS와 관측 컨테이너 이름을 `kor-travel-*` 기준으로 맞춘다
- [x] 문서, 테스트, 설정 파일의 과거 서비스명 잔여 표기를 제거한다
