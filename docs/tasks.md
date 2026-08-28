# TASKS — 활성 작업

이 문서는 완료되지 않은 작업만 의존 순서대로 한 줄씩 나열한다. lane, 담당자 구분,
계층형 하위 작업은 사용하지 않는다. 완료·퇴역 이력은
[`docs/tasks-done.md`](tasks-done.md), 실행 근거는 [`docs/journal.md`](journal.md)가 정본이다.

- 진행 중: `[/]`
- 미진행: `[ ]`

## 작업 현황

| 태스크 ID | 작업 항목 | 상태 | 다음 완료 조건 |
|:---|:---|:---:|:---|
| **MAP-LIVE-FOLLOWUP** | Map/PinVi cross-repo live consumer acceptance 후속 | `[/]` | PinVi WebSocket/mutating loop·consumer reconciliation과 Map task/journal/manifest 교차 대조를 실제 pair에서 기록 |
| **BACKUP-FOLLOWUP** | 독립 standalone backup의 남은 운영 보강 | `[/]` | off-box 사본 자동화와 보존 정책. Alembic downgrade/이전 revision restore는 범위 밖 |
| **KTDCTL-UI-MIGRATION** | ktdctl CLI 기능의 UI 이관·운영 기능 격차 (1부 트랙 KUM-M1~M4 구현 완료) | `[/]` | 1부 잔여(KUM-M5·M6·M7)와 v3 문서 3부의 나머지 태스크 분해 |

## M05 재개 규율

- [/] `fa28a6e7…`는 Map `f90b7c28…`·PinVi `fdff06ba…`·Manager `b45f54d5…`의 registry/public-copy gate 뒤
  n150 isolated one-shot을 정확히 한 번 실행했다. launcher는 exit 1이었고 허용된 durable safe result는 없었지만,
  후속 `pin verify`가 exact pinset의 terminal 차단을 확인했다. HTTP 원문·container log·환경값·output leaf는
  읽거나 남기지 않으며, root registry가 조건 없이 차단한 같은 pinset·source pair·Manager source·output leaf는
  재실행하지 않는다. 다음 Manager source는 public-copy 검증 뒤 Map·PinVi·pinset snapshot을 고정하고, driver
  stdout/stderr를 버리며, exact root-owned schema 결과와 종료 후 동일 snapshot일 때만 수용한다. 그 외에는 시작
  snapshot의 exact pair를 unconditional terminal block으로 승격·재검증하고 `launcher-result.json`의
  `launcher_safe_result_unavailable` fixed envelope만 권위 결과로 쓴다. terminal 기록 Map `73150672…`·새 PinVi
  provenance와 이 source를 fresh atomic pinset으로 결박하고 CI·source-head 전문 적대 리뷰 두 건을 모두 통과하기
  전에는 n150 실행권이 없다.
- [/] `a3f6a8f3…`은 trusted installed-wheel project-root preflight failure로 terminal 차단됐다. 같은
  Map/PinVi pinset·Manager source·one-shot output leaf는 어떤 이유로도 재실행하지 않는다. installed `python -I`
  경로의 external registry 선택 회귀와 두 전문 적대 리뷰를 통과한 새 Manager source, 새 pair pinset만 다음
  isolated M04/M05 실행권을 가질 수 있다.
- [/] `22563762…`는 M04/M05 isolated one-shot에서 cleanup 성공 뒤 `runtime_http_failed` terminal로 차단됐다.
  HTTP 원문·container log·환경값을 읽거나 남기지 않는다. 다음 source는 transport·응답 형식 오류를 caller별
  fixed phase로 즉시 전파하고 PinVi `blocked`·미정의 receipt status도 각각 fixed terminal phase로 처리한다.
  M05 direct admission은 exact current registry pair의 unconditional block을 ledger·Docker mutation 전에
  확인하고, non-success result를 같은 pinset의 unconditional block으로 결박한다. 같은 pinset·Manager source·
  output leaf는 재실행하지 않고 Map `bbb29d17…`·새 PinVi pair·새 Manager source만 다음 실행권을 가진다.
- [/] M05 fresh Manager source는 backend Ruff·pytest와 frontend type-check·build를 PR에서 모두 통과해야 한다.
  CI는 FastAPI `TestClient` 수집에 필요한 `httpx==0.28.1`을 명시 설치한다. 원격 CI workflow가 없거나 green이 아닌
  source는 trusted release·pin rotate-pair·n150 E2E 후보가 될 수 없다.

## 공통 진행 규율

1. 작업 시작과 PR merge 직전에 `origin/main`을 fetch하고 필요하면 rebase한다.
2. 검증 가능한 작은 단위로 커밋하고 remote PR branch에 자주 push한다.
3. 코드 PR은 서로 다른 관점의 전문 적대 리뷰 2건을 통과해야 한다.
4. push 전에 staged file·일반 비밀·프로젝트별 민감 문자열 감사를 실행한다.
5. 완료된 항목은 이 파일에 `[x]`로 쌓지 않고 즉시 `tasks-done.md`로 옮긴다.
6. n150 배포와 Playwright live E2E는 `docs/deploy-runbook.local.md`를 정본으로 수행한다.

## MAP-LIVE-FOLLOWUP — Map/PinVi cross-repo live consumer acceptance

H300의 이번 수락은 schema/bootstrap·runtime provenance·login setup/protected view와
data-independent UI 11개에 한정했다. logout 후 재차단, PinVi WebSocket/mutating loop,
consumer reconciliation은 별도 운영 acceptance로 남긴다. 구현·실행 정본은
[Map 저장소 `docs/tasks.md`](https://github.com/digitie/kor-travel-map/blob/main/docs/tasks.md)의
`T-VN-41C`·`T-VN-41F1D-D2`이며, 이 Manager task는 cross-repo 결과와 exact pair를 함께 기록한다.

- [x] Map UI의 현재 socket close와 `/login` redirect, ticket/lease WebSocket wire를 실제 브라우저로
  확인했다.
- [x] Map UI에서 logout 뒤 `/ops/datasets` protected route 재진입이 `/login`으로 재차단되는지
  확인했다.
- [x] PinVi exact pair에서 logout 뒤 `/admin/features` protected route 재진입이 `/admin/login`으로
  재차단되는지 확인했다. 이 검증은 `/auth/logout` 204를 확인하고 application row를 쓰지 않았다.
- [/] Map #1083 merge `9c64e862…`와 PinVi #487 squash merge `97d2f924…`를 새 v5 pinset
  `cbb577d3…`으로 candidate build·attestation에 반영한다. `872e3262…` candidate와 d9의
  `map_runtime_ready` journal은 historical failure evidence로 보존하며 새 pinset이 재사용하지 않는다. 두 전문 적대 리뷰·검증·trusted
  deployment 전에는 rebuild를 호출하거나 수동 Docker/Compose/SQL·DB/journal/permit 조작을 하지 않는다.
  trusted 첫 실행은 candidate journal·receipt·DB/runtime mutation 전에 paired builder에서 fail-close했고,
  원문을 저장·전달·파싱하지 않고 owner-only candidate receipt 상태로만 고정 failure class를 남기는 후속
  Manager 진단 PR이 merged·배포되기 전에는 재시도하지 않는다.
  n150 read-only 확인으로 Map immutable Python base image cache 부재를 원인으로 확정했다. Manager는
  materialized API/Dagster Dockerfile의 exact digest base만 trusted preflight에서 pull·재관측하며,
  이 변경과 Map execution journal의 fresh source pinset 없이는 같은 candidate를 재실행하지 않는다.
- [/] 원인은 M05가 요구하는 role topology와 #477의 root 단일 DSN 배선 불일치로 분리됐다. PinVi
  #488 merge commit `93296aee5d47676e6b9b79303bf417c598a273ac`은 Manager가 허용하는 exact loopback
  endpoint에서만 role bootstrap을 실행하도록 보정했다. Manager 후보는 root·runtime·schema owner·migration
  owner·migrator identity를 분리하고 open → bootstrap → seal lifecycle을 강제한다. 이전 Manager release pin은
  Map #1066 merge `14d18230e5a9ff21caf26d6abe37aed1e4944685`와 PinVi #488 commit을 함께 고정한
  pinset `d9aded44779114ed0595d3a4fb50908efb56b57c85148faf3083b0087a35e898`이다. n150의
  canonical root `.env` role credential tuple은 fresh 최초 admission에서만 원자 초기화할 수 있고, 현재는
  official rebuild가 raw C6c·external readiness를 통과해 Map source fetch까지 도달한 완결된 정본이다.
  후속 Manager PR은 caller 경로 override가 아닌 trusted
  `/opt` root pair와 root-owned host lease를 사용하며, exact rebuildable admission·C6c token을 **쓰기 전** 통과한
  경우에만 여섯 값을 원자 초기화한다. 현재 pinset의 `map_runtime_ready` v8 resume에는 기존 environment SHA와
  candidate raw/resolved Compose SHA를 모두 receipt로 남기는 한 번의 role-source rebind만 허용한다. 다른 phase·
  digest·partial/blank/duplicate는 덮어쓰기·회전·journal 재사용 추측 없이 거부하며, full 기존 값은 재사용만 한다.
  Manager #229은 이 보정을 merge했고 두 전문 적대 리뷰와 backend gate를 통과했다. 다만 clean release의
  official installer는 root-owned source wheelhouse에 `poetry-core`가 없어 activation 전에 fail-close했다.
  staging/rollback 외 canonical `.env`, Docker/Compose, candidate, journal, runtime, DB에는 변경이 없었다.
  따라서 다음 재개 전에는 검증된 Debian package에서 이 build dependency를 포함한 새 root-owned wheelhouse를
  atomic 발행하는 후속 Manager PR을 merge·배포해야 하며, 그 전에는 rebuild를 재시도하지 않는다. 기본
  wheelhouse 경로가 다른 provenance의 pre-existing root artifact라면 이를 삭제·수정·채택하지 않고, exact
  merged commit을 포함한 새 destination을 provisioner와 installer에 같은 explicit path로 넘긴다.
- [/] Manager #230/#231의 trusted offline wheelhouse issuance와 installer release deployment 뒤 공식
  rebuild는 raw C6c prebuild에서 `pinvi-db-runtime-role` source bind 부재를 발견해 fail-close했다.
  candidate source materialize, paired image build, journal, Docker/Compose runtime, one-shot, 세 DB reset은
  시작하지 않았다. n150의 `PINVI_REPO_DIR` checkout은 clean canonical origin이지만 PinVi
  `25505e056…`에 머물러, Manager pin `93296aee…`가 요구하는
  `infra/postgres/bootstrap-pinvi-runtime-role.sh`가 없다. source checkout HEAD는 candidate authority가
  아니어도 raw bind precondition이므로, WIP를 reset·복사하지 않고 source owner가 같은 path를 exact approved
  release로 수렴한 뒤에만 동일 official rebuild를 한 번 재개한다. manual Docker/Compose/SQL, bind/guard
  완화, role credential 삭제·회전은 금지한다.
- [/] role source precondition을 수렴한 다음 official rebuild는 Map PR #1066의 deleted head
  `cc81081…`를 canonical GitHub에서 exact fetch하는 단계에서 fail-close했다. local tree와 canonical remote의
  exact-SHA fetch가 검증된 merge `14d18230…`의 tree가 identical임을 확인했으므로, Manager는 deleted PR head가
  아니라 이 머지 커밋만 새 source authority로 회전한다. 이 pin rotation이 trusted deployment·candidate
  build·attestation을 통과하기 전에는 rebuild를 다시 호출하거나 기존 `map_runtime_ready` journal을 새 pinset의
  근거로 재사용하지 않는다.
- [/] Manager #233의 trusted deployment 뒤 `d9aded…` pinset으로 실행한 official rebuild는 Map paired
  build, Map application/Dagster schema와 Map runtime 준비을 끝내 v8 journal `map_runtime_ready`까지 도달했다.
  Manager #234는 lifecycle stage와 allowlisted 비밀 비포함 failure code를 도입했고 trusted release에 반영됐다.
  그 뒤 모든 읽기 전용 preflight를 마친 뒤 승인된 official rebuild를 정확히 한 번 실행한 결과는
  `role_topology_noncanonical`이었다. journal은 `map_runtime_ready`의 같은 generation으로 남았고
  `pinvi-admin-bootstrap` one-shot 증거는 없다. 이 candidate는 `committed`가 아니므로 Map/PinVi live
  acceptance나 M01~M05 activation의 근거가 아니다. n150 candidate·journal·DB·role·Compose에는 수동 변경을
  하지 않으며 d9 command도 재실행하지 않는다. 후속 Manager PR은 동일 pinset에서 확정된
  `pinvi_role_open` 또는 `pinvi_role_seal`의 `role_topology_noncanonical`만 비밀 비포함 terminal receipt로
  v8 journal에 기록하고, 다음 admission을 source materialize·role credential write·build·DB mutation보다 앞서
  차단한다. receipt 이전의 이미 알려진 d9 historical journal은 소급 수정하지 않고 exact pinset·Map/PinVi source
  revision·`map_runtime_ready` policy로 같은 pre-mutation admission에서 차단한다. Compose argv, lifecycle,
  root secret mount, cleanup과 journal/candidate 입력은 바꾸지 않는다.
  raw stderr, DSN, role, 비밀번호, path, 자동 retry, topology/권한 완화, journal 소급 기록은 금지한다. 해당
  PR의 두 전문 적대 리뷰·CI·trusted deployment 전에는 rebuild를 재실행하지 않는다.
- [x] n150 candidate가 빈 artifact path 설정 때문에 DB reset 전에 fail-close한 것을 확인했다. 기본
  preflight는 현재 pinset state root에서 네 fence/permit mount directory를 도출해 사용하도록 Manager #222로
  보정·병합·배포했다. 실제 빈 `.env`와 Compose resolution 회귀를 포함한 587개 backend 테스트, 전문 적대 리뷰
  2건을 통과했다.
- [/] Manager #223을 trusted `/opt` release로 배포한 뒤 root-only retirement는 project-root override를
  blanket 거부해 mutation 전 fail-close했다. home checkout의 Compose file은 user-writable이므로 그 거부만
  풀거나 home root를 Compose 입력으로 허용하면 P0가 된다. 후속 Manager PR은 `/opt`를 canonical execution
  root로 유지하고, root-owned `0600` final legacy override와 고정 sibling Concierge `.env`만
  `compose-boundary stage-legacy-override --source <absolute-path> --confirm`으로 protected C6c state에
  descriptor-safe snapshot한다. retire는 이 staged pair만 raw allowlist·backend key/API key-set membership·
  production/authentication API guard·host network·API/UI production command/port·atomic root `.env`·실제
  raw/resolved C6c config를 통과할 때 같은 state filesystem 안에서 archive하고, 해당 deployment lock 안에서
  Concierge API/MCP/scheduler/UI를 재생성한다. 실제 n150은 `rehearsal/rebuildable` pinned rebuild mode이므로
  stage/retire는 그 exact mode·PinVi production·Map principal-required contract와 pinned-runtime rebuild host
  lease를 공유하며, production으로 수동 전환하지 않는다. production mode에서는 fixed C6c global mutation
  lock을 사용한다. archive durability 불확실성은 candidate `.env`를 되돌리지 않는 typed
  failure로 남긴다. archive 뒤 재생성만 실패하면 `compose-boundary activate-concierge --confirm`으로 같은 계약을
  재검증한 뒤 재시도한다. home source는 rename/delete/Compose 실행 대상이 아니며, 그 전에는 override를 수동
  삭제하거나 rebuild를 재시도하지 않는다.
- [/] #224 merged trusted release를 n150에 공식 installer로 반영했고 source 권한 precondition도 정렬했다.
  공식 stage는 Docker/DB/runtime/root `.env` mutation 전에, 실제 legacy Compose가 사용하는 장형 `env_file`
  mapping을 구형 문자열 allowlist가 거부하는 P1을 확인했다. 장형은 override 위치에서 계산한 exact sibling
  `.env`와 boolean `required: true`, 필요 시 exact `format: raw`만 수용하며, home Compose 실행·source 변환·수동
  삭제를 추가하지 않는다.
- [/] #226의 exact raw 장형 수용 후보를 배포한 공식 stage는 protected pending snapshot을 성공적으로 만들었다.
  이어 retire는 archive·candidate root `.env` write·Docker/Compose/DB/runtime mutation 전에 legacy UI source에
  없던 API auth 세 값(`API_KEYS`, `APP_ENV`, `API_AUTH_ENABLED`)을 요구해 fail-close했다. 후속 후보는 이 세 값이
  **미선언**일 때에만 이미 있는 canonical root authority를 다시 검증해 사용하고, source에 선언된 빈 값과 모든
  `KTC_*` UI 값 누락은 계속 fail-close한다. raw/resolved C6c, API key-set membership, production/authentication
  guard, trusted `/opt` execution root, home source 비재사용은 바꾸지 않는다.
- [x] #228이 Concierge API/MCP/scheduler/UI와 transitive dependency·실참조 top-level entity만 든 root-owned
  temporary projection으로 full Compose의 무관한 PinVi credential guard를 분리했다. trusted release 배포 뒤
  sanctioned stage·retire가 성공해 legacy override를 protected archive로 옮기고 canonical Concierge 네 service를
  재생성했다. 이어 공개 HTTP와 n150 Linux 실제 브라우저에서 login → authenticated BFF → logout → BFF 재차단을
  확인했다. Map/PinVi source·값·runtime은 이 retirement 경로에 유입되지 않으며 projection은 보존되지 않는다.
- [ ] PinVi WebSocket/mutating loop와 consumer reconciliation의 성공·실패 증거를 기록한다.
- [ ] Map 저장소 `T-VN-41C`·`T-VN-41F1D-D2` 완료 기록과 Manager journal/manifest를 교차 대조한다.

## BACKUP-FOLLOWUP — standalone backup 운영 보강

Issue #177의 create/list/gc, cron, API/UI 구현과 n150 Geo Dagster·Concierge·PinVi 실증은 완료됐다.
남은 항목은 pair rebuild와 독립한다.

- [ ] off-box 사본 자동화와 보존 정책을 별도 설계한다.

Geo application DB의 첫 standalone 대용량 backup 실측 완료 이력은
[`docs/tasks-done.md`](tasks-done.md)의 `BACKUP-FOLLOWUP-GEO-INITIAL`에 보관한다.

## KTDCTL-UI-MIGRATION — ktdctl CLI 기능의 UI 이관·운영 기능 격차 설계

`ktdctl`이 이미 갖고 있는 기능 중 UI로 이관할 만한 것과, GitHub source pull+build·
git revision/계약 정합·git 이력 조회·Docker 이미지 업데이트·백업·설정/secret 변경 등
운영에 필요한 기능 중 `ktdctl`/API/UI 어디에도 없는 격차를 조사했다. 조사 서브에이전트가
초안을 작성한 뒤 보안/blast-radius 리뷰어와 완결성/실현가능성 리뷰어가 각각 독립적으로
실제 코드와 대조 검증했고, 두 리뷰가 일치되게 지적한 정정(초안이 존재하지 않는
`assert_c6c_mutation_allowed`를 근거로 삼았던 점, `diff-pinned` 제안이 실제로는
read-only가 아니었던 점, `image rebuild-service` 제안이 잘못된 전제 위에 있었던 점 등)을
반영해 최종본을 만들었다. 정본은 [`docs/ktdctl-ui-migration.md`](ktdctl-ui-migration.md)다.

이번 라운드는 **설계·문서화·태스크 등록까지만** 진행했다 — 코드 변경은 전혀 없다. 문서의
"우선순위 권고"와 "열린 질문" 절에 따라 오너가 각 항목(특히 `db-backup create` API/UI
노출, git 이력 조회를 위한 GitHub egress 확대, `secret rotate` 착수 여부, `db-backup
restore` 로드맵 포함 여부)을 결정하면 승인된 항목만 별도 구현 태스크로 분리한다.

- [x] `ktdctl` CLI 전체 서브커맨드와 API/UI 노출 여부 인벤토리를 작성하고 실제 코드로 검증했다.
- [x] 7개 운영 영역 각각에 대해 Today/Gap/UI 노출안/Risk를 정리했다.
- [x] 보안/blast-radius 리뷰와 완결성/실현가능성 리뷰를 각각 독립 수행하고 확인된 지적을 최종본에 반영했다.
- [x] 오너가 설계 문서의 열린 질문 7건에 전부 답했다(2026-08-28, 문서 말미 "오너 결정
  사항" 표 참조 — pin registry·백업 create UI·비밀번호 폼·2-step rotate·rehearsal
  rebuild 버튼·restore 로드맵 승인, CLAUDE.md 동기화는 별도 작업).
- [x] v3 개정(2026-08-28): kor-travel-map·pinvi·본 저장소의 08-25~28 커밋 전수를
  교차 감사해 계약·pinning·결박 이슈를 발굴·반영했다 — 문제 진단 6건(1부), pinset
  lifecycle registry(`blocked_pinsets`·`history`·`pin block`·terminal rollback 제한),
  P2 권한 모델 정정(root-side publisher 의존), P6 rebuild journal 충돌 위험, preflight
  readiness·typed 진단 소비·계약 소유 경계 명문화, 태스크 분해(3부: KUM-M1~18,
  KUM-MAP-1~4, KUM-PV-1~4).
- [x] 1부 트랙 구현 완료(2026-08-28, ADR-40): KUM-M1(registry 파일화 + `ktdctl pin`
  패밀리 + 중복 상수 제거), KUM-M2(pinset lifecycle·terminal 자동 거부·rollback 제한·
  d9 상수 이관), KUM-M3(root-side world-readable publisher), KUM-M4(`GET
  /api/v1/runtime-pins` + 배포 버전 고정 패널). 전문 적대 리뷰 2건 반영,
  n150 격리 live E2E 15항목 통과, backend 751 tests.
- [ ] 1부 잔여: KUM-M5(UI 2-step pin rotate), KUM-M6(typed 진단 소비 이관),
  KUM-M7(preflight readiness 노출).
- [ ] 나머지 3부 태스크 분해를 기준으로 승인 항목을 구현 태스크로 분리한다(신규 제안
  항목은 분리 시 오너 확정 — KUM-M17은 별도 결정 사안).

## T-VN-M05-HARNESS — M05 격리 harness와 catalog reset (PR #243/#250)

이 항목들은 `fix/m05-pinvi-topology-preflight` / `fix/m05-isolated-e2e-harness`
브랜치가 관리하던 목록이다. main 병합 시 태스크 문서가 통째로 갈라져 여기로 보존한다.

**pin 회전은 Map Compose digest 고정 PR 이후로 미룬다(오너 결정, 2026-08-28).**
그 전까지 registry는 회전하지 않으며, 병합된 코드는 재구축을 실행하지 않는 상태로
머문다 — 현재 pinset은 registry가 terminal로 차단 중이다.

- [/] T-VN-M05-CATALOG-TEMPLATE0 — n150에서 단 한 번 실행한 `68d99705…`·`285618c0…`·`37932169…`·`31fe73ad…`·`b22bfb8c…`·`89330403…`·`c6c73cdf…` candidate는 terminal로 보존하고 재시도하지 않는다. `c6c73cdf…`은 `map_runtime_ready` 뒤 `role_catalog_reset_failed/foreign_membership`으로 끝났고 raw stderr·catalog row는 읽지 않았다.
- [/] T-VN-M05-NEW-CANDIDATE — `6269138f…`와 `53d4639f…`는 모두 재실행하지 않는다. 후자는 trusted release가 launcher 파일을 실행 가능하게 설치하지 않아 admission 이전에 끝났고 durable output·ledger·raw stderr가 없다. installer는 launcher mode `0755`을 명시적으로 보존한다. PinVi `7d66523a…`·Map `9c64e862…`의 `9835cfcc…`도 실행하지 않는다. PinVi `323e3ba8…`·Map `9c64e862…`의 `2d6d5ad5…`에 결박된 Manager `d7a048a1…`은 n150에서 한 번 admission했고 `/usr/sbin/ss` 부재로 `driver_contract_failed`가 되어 재실행하지 않는다. `66bb373d…`은 Map `fresh-init` profile resource를 profile 없는 cleanup으로 누락해 `runtime_cleanup_failed`가 되어 재실행하지 않으며, 남은 정확한 transaction resource는 owner label을 재검증한 뒤 정리했다. `bc704aef…`은 cleanup은 완결했으나 generic `runtime_command_failed`가 Map 세 command 중 어디인지 구별하지 못해 재실행하지 않는다. `aa78b4ec…`은 cleanup을 완결하고 Map `fresh-init` 명령까지 고정했으나 그 내부 실패 분류가 없어 재실행하지 않는다. `bea60f5…`은 cleanup을 완결했으나 첫 allowlist 밖 예외가 `unclassified`로 끝나 재실행하지 않는다. `6c888a5…`은 cleanup을 완결했으나 Alembic runtime contract 예외가 더 세분화되지 않아 재실행하지 않는다. `29fbcdd…`은 cleanup을 완결했으나 Map `9c64e862…`의 baseline artifact 정적 검증은 통과했고, baseline이 고정한 PostGIS immutable image와 Compose의 부동 태그 image identity가 달라 `baseline_reference_invalid`가 되어 재실행하지 않는다. Manager/PinVi runtime override로 우회하지 않는다. 새 candidate는 Map Compose가 baseline PostGIS digest를 직접 사용하도록 고정한 committed revision을 PinVi pair와 pinset에 다시 결박한 뒤에만 사용한다. harness는 trusted installer와 같은 global mutation lock을 종료까지 유지하고, pinset별 root-owned `O_NOFOLLOW|O_EXCL` ledger claim+directory fsync로 output path를 바꿔도 재실행을 막는다.
- [ ] T-VN-M05-ACTIVATION — Map Compose PostGIS digest 고정 PR의 committed revision과 PinVi pair는 `pin rotate-pair`의 단일 registry replace로 재결박한다. terminal current에서 role별 intermediate pinset은 생성 자체가 거부되고, source pair preflight는 ledger claim보다 먼저 실행된다. final candidate에서만 n150 isolated M04/M05 live mutating E2E와 activation attestation을 실행한다.
- [/] T-VN-M05-ISOLATED-HARNESS — host-network canonical runtime을 변경하지 않고, exact Map/PinVi source·bridge network·fresh volume·loopback binding만 쓰는 root-only M04/M05 isolated harness를 추가한다. receipt schema는 여섯 image ID·두 source·Map full OpenAPI를 PinVi consumer와 결박했고, Docker mutation driver와 fresh source snapshot·cleanup receipt를 이어서 결선한다. pair의 admin/full/service/user historical source object는 canonical 원격에서 exact raw/canonical SHA로 먼저 검증하며, M04 UI 승인→Map 승인→candidate rebind→PinVi receipt→M05 attestation을 실제 실행한다. host loopback HTTP는 ambient proxy를 타지 않으며, generic Map image fixture에는 owner credential을 주입하지 않고 `ktm_feature_dagster_runtime`의 provider 적재·candidate procedure 경로만 쓴다. Docker mutation 전 `(harness, pinset, Manager revision)` ledger claim과 global lock을 고정하고, 증적은 production activation과 분리한다.
- [ ] T-VN-41F1D-D1 — 최종 격리 리허설과 provenance attestation을 기록한다.
- [ ] T-VN-41F1D-D2 — data-dependent Map/PinVi admin live E2E와 receipt 승격을 완료한다.
- [ ] T-VN-41C — relay, reconciliation, consumer enable paired acceptance를 완료한다.
- [ ] T-VN-41F1D-E — 이전 generation 퇴역과 v6/v8 attestation 전환을 완료한다.
- [ ] T-VN-H43 — production backup의 정기 dump, SHA-256, 보존, rollback 기준선을 확정한다.
- [ ] T-VN-H49 — 분할 인스턴스 backup의 주기 실행, bounded retention, off-box 증거를 완료한다.
