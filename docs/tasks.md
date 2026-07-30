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
| **T-031** | Map↔PinVi C6c ops read/cancel principal 배포 결선 | `[/]` | - | 구현·기존 live 충족, T-045 회전과 새 official deploy 미완료 |
| **T-045** | Map UI credential rotation을 `ktdctl`의 audited production workflow로 제품화 | `[/]` | - | 값 비노출·원자 갱신·UI-only recreate·복구·감사 |
| **T-046** | `pinvi-pair deploy`/`capture`의 `--wait-timeout` 하드코딩 제거 (issue #88) | `[/]` | - | 마이그레이션 수반 배포·bootstrap의 오발동 rollback 방지, n150 실제 마이그레이션 배포 검증 대기 |

---

## 진행 순서

1. `tasks.md`와 `tasks-done.md`를 최신 완료/미완료 상태로 정리한다.
2. `kor-travel-concierge`는 `conc`, PinVi는 `srv` 별칭을 기준으로 안내한다.
3. 다음 앱 target 추가 시 `config/docker-targets.yml`, `docker-compose.yml`, 포트 문서, API/CLI 테스트를 함께 갱신한다.
4. 병행 작업 충돌을 줄이기 위해 각 PR 전후로 `main` rebase를 수행한다.

---

## 태스크 세부 내역

### T-031: Map↔PinVi C6c ops read/cancel principal 배포 결선

- [x] read/cancel principal을 Map API와 PinVi API에만 결선하고 Map UI·Dagster·daemon,
      PinVi Web·Dagster에는 전달하지 않는 최소 권한 계약을 raw/resolved/runtime 단계에서
      고정했다.
- [x] production 일반 `ensure`와 container action/config/reset/direct Compose 경로에서
      Map runtime 4종과 PinVi API 변경을 차단하고, 전역 lock을 소유하는
      `pinvi-pair capture/deploy/rollback`만 다섯 immutable image 세대를 변경하도록 했다.
- [x] compatible-pair manifest v4가 active/rollback의 Map API·UI·Dagster web·daemon과
      PinVi API image ID, clean source revision, contract generation을 함께 기록한다.
      mixed generation은 시작 세대 복구 또는 다섯 runtime halt로 수렴한다.
- [x] Map UI username·PBKDF2 hash·session secret을 canonical Compose와 exact runtime에
      결박하고 manager smoke 평문은 container에 주입하지 않는 회귀 계약을 고정했다.
- [x] PR #54~#57, #64, #67, #69, #73의 리뷰·CI를 통과했다. 2026-07-26 C7 공식
      gate에서 read-auth `7/7`, KMA active/cap/empty 각 `2/2`, schedule-write `2/2`,
      POI-cache-causal `2/2`, `BLOCKED` 0건, 상태 복구와 active target 0을 확인했다.
- [x] 2026-07-27 compatible-pair에서 C6c principal smoke와 targeted live를 통과했다.
- [ ] 현재 canonical Manager `.env`의 Map UI hash/session은 running UI와 일치하지만
      manager smoke 평문은 그 PBKDF2 hash를 검증하지 못한다. 따라서 새 official deploy
      preflight는 container mutation 전에 중단된다. 수정은 T-045가 소유한다.
- [ ] n150에서 Map UI password hash와 session secret을 함께 회전하고, 새 manager smoke
      평문↔hash 일치, 이전 session 무효화, login→`/ops/datasets` 보호 GET→logout→재차단을
      확인한다.
- [ ] 회전 뒤 최신 exact Map·Manager·PinVi 조합으로 official compatible-pair deploy와
      cross-repo smoke·targeted live를 다시 통과한 뒤 완료 이력으로 옮긴다.

### T-045: Map UI credential rotation을 `ktdctl`의 audited production workflow로 제품화

- [ ] production에서만 실행되는 전용 `ktdctl` command를 추가하고 C6c 전역 lock,
      canonical manager checkout/Compose/`.env`, 실행 중 Map UI identity와 immutable image를
      mutation 전에 fail-closed로 검증한다.
- [ ] 새 password 평문, PBKDF2 hash, session secret을 argv·stdout/stderr·audit·child
      environment·Docker metadata에 노출하지 않는다. PBKDF2 format과 평문↔hash 일치를
      독립 검증하고 hash와 session secret은 항상 함께 회전한다.
- [ ] canonical `.env`의 Map UI hash/session 두 항목만 원자 교체하고 같은 immutable image로
      Map UI service만 `--no-deps --force-recreate --no-build --pull never --wait` 재생성한다.
      다른 project container와 manifest/image generation은 변경하지 않는다.
- [ ] 새 login→`/ops/datasets` 보호 GET→logout→재차단, 회전 전 session 거부를 검증한 뒤
      durable audit를 commit한다. forward 실패 시 이전 hash/image와 새로운 recovery session
      secret으로 UI를 복구해 partial-forward session까지 무효화하고 실제 복구 상태를 정직하게
      기록한다.
- [ ] crash/signal/재실행 recovery journal, foreign container/name collision, `.env` drift,
      Compose/runtime drift, auth 실패, rollback 실패의 음성 회귀를 추가한다.
- [ ] 단일 적대적 리뷰, focused/backend 전체 테스트, Ruff, strict mypy, canonical Compose gate,
      CI green을 통과한 별도 코드 PR을 병합한다.
- [ ] n150에서 전용 command로 실제 회전하고 official compatible-pair deploy, C6c principal
      smoke, C7 targeted live를 통과한 뒤 T-031과 함께 완료 이력으로 옮긴다.

### T-046: `pinvi-pair deploy`/`capture`의 `--wait-timeout` 하드코딩 제거 (issue #88)

kor-travel-map API는 uvicorn 기동 전에 `alembic upgrade head`를 실행한다. `_run_up_stage`가
`docker compose up --wait --wait-timeout 120`을 하드코딩했는데, `CREATE INDEX CONCURRENTLY`
등 non-transactional DDL을 쓰는 긴 마이그레이션(실측 8~18분)은 120초를 넘겨 deploy가 실패로
판정되고 `_recover_previous_pair` rollback이 발동한다 — **마이그레이션이 진행 중인 컨테이너를
그대로 뜯어** durable한 부분 적용 상태를 남긴다. kor-travel-map T-VN-H35(prod alembic
0063→0069) 실행 중 발견되어 배포가 중단됐다.

- [x] `_run_up_stage`가 `wait_timeout: int` 파라미터를 받아 하드코딩 `"120"` 대신 실제
      compose `--wait-timeout` 인자로 쓴다. `deploy_compatible_pinvi_pair` → CLI
      `pinvi-pair deploy --wait-timeout <seconds>`까지 전체 경로를 관통하며, 기본값(120)은
      바뀌지 않아 기존 호출은 회귀 없다.
- [x] **적대적 리뷰(1명)가 발견한 공백을 함께 수정한다**: `pinvi-pair capture`(clean
      bootstrap)도 5개 활성화 단계에서 같은 하드코딩 `wait=True`를 쓰고, 그중
      `bootstrap_map_api`는 정확히 같은 alembic 선행 실행 패턴이다 — 최초 bootstrap은
      전체 마이그레이션 이력을 처음부터 실행할 수 있어 증분 배포보다 오래 걸릴 가능성이
      크다. `capture_compatible_pinvi_pair`에도 같은 `wait_timeout` 파라미터와 CLI
      `--wait-timeout`을 추가하고, 검증 로직은 `_validate_c6c_wait_timeout` 공유 helper로
      중복 없이 통일했다.
- [x] `wait_timeout`은 int·1~3600초 범위만 허용하고(`bool`은 `isinstance(x, int)`가 `True`라
      별도 배제), lock 진입·subprocess 호출보다 먼저 검증한다. rollback/recovery 경로
      (`_recover_previous_pair`, `rollback_compatible_pinvi_pair`)는 의도적으로 그대로
      두어 기본 120초를 유지한다 — rollback 대상은 이미 마이그레이션이 끝난 옛 image라
      진짜 hang을 빠르게 판별하는 쪽이 더 안전하다.
- [x] 회귀 테스트 다수 추가(threading·기본값 유지·경계값 1/3600·잘못된 타입/범위 거부·
      `_run_up_stage`가 실제로 만드는 compose 인자·`_activate_pair_sequentially`의 세 단계
      모두 동일 값 사용·`capture`의 11개 `up --wait` 단계 모두 동일 값 사용). backend
      1067 passed(기존 1049 + 신규 18), ruff 기존 9건 유지, 변경 파일 mypy clean.
- [ ] n150에서 실제 긴 마이그레이션을 수반하는 `pinvi-pair deploy --wait-timeout <n>`
      (또는 `capture`)을 실행해 오발동 rollback 없이 통과하는 것을 확인한 뒤 완료 이력으로
      옮긴다.
