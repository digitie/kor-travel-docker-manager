# ktdctl → UI 이관 및 운영 기능 격차 설계

## 범위와 비목표

이 문서는 **설계 문서**다. 코드 변경은 전혀 없다. 목적은 두 가지다: (1) `ktdctl`이
이미 할 수 있는 일 중 UI에 아직 없는 것을 찾아 이관 후보를 정리하고, (2) 운영에
필요하다고 지목된 영역 — GitHub source pull+build, git revision/계약 정합, git 이력
조회, Docker 이미지 업데이트, 백업, 설정/secret 변경, 기타 — 에 대해 `ktdctl`/API/UI가
각각 무엇을 갖고 있고 무엇이 없는지, 없다면 무엇을 어떤 안전장치와 함께 추가해야 하는지
제안한다.

**비목표**: 이 문서는 구현하지 않는다. 여기 제안된 CLI 서브커맨드, API 엔드포인트,
UI 화면은 전부 미착수 상태이며, 표기된 서명·경로·플래그는 설계 초안이지 확정 계약이
아니다. 이 저장소는 production mutation을 host-wide lock, `--confirm` 플래그,
`KTDM_DEPLOYMENT_ENVIRONMENT`/`KTDM_DEPLOYMENT_LIFECYCLE` 조합으로 엄격히 게이트하는
문화를 갖고 있고, 이 문서는 그 문화를 존중한다 — "버튼 하나로 `git pull && docker
build`"류 제안은 의도적으로 배제했다.

**작성 경위**: 초안은 조사 전담 서브에이전트가 작성했다. 이후 서로 다른 두 관점 —
보안/blast-radius 리뷰어와 완결성/실현가능성 리뷰어 — 가 실제 코드를 대조해 독립적으로
검증했다. 두 리뷰는 초안의 핵심 근거 중 하나(§"기존 비대칭" 서술)가 **존재하지 않는
함수를 인용한 잘못된 근거**였다는 점, `diff-pinned` 제안이 실제로는 read-only가 아니라는
점, `image rebuild-service` 제안이 그 잘못된 근거 위에 세워져 있었다는 점을 일치되게
지적했다. 이 문서는 그 지적을 반영해 초안을 고쳐 쓴 최종본이다. 리뷰가 언급한 세부
근거(파일:라인)는 아래 각 절에 인용해 남긴다.

---

## 현재 상태 인벤토리

`ktdctl`은 `build_parser()`(`backend/src/kor_travel_docker_manager/cli.py:260-418`)가
등록하는 서브커맨드로 구성된다. 리뷰 결과 **이 표는 HEAD 기준으로 정확하고 누락이
없다** — 실제 `add_parser` 호출과 대조해 12개 leaf 명령이 전부 확인됐다. 짧은 별칭
(`db`, `storage`, `gra`, `cadv`, `prom`, `geo`, `conc`, `map`, `pinvi`, `srv`, `all`
등, `config/docker-targets.yml` 기반)은 `main()`에서 `DIRECT_ENSURE_ALIASES`로 즉시
`ensure`로 치환된다.

| ktdctl 명령 | 하는 일 | API 노출 | UI 노출 |
|---|---|---|---|
| `targets [--json]` | target 목록·의존 순서·해석된 service 목록 | `GET /api/v1/targets` | 간접적 — `ContainerDetailModal`의 영향 범위 계산에만 사용 |
| `status [target] [--json]` | `docker compose ps` 결과 | `GET /api/v1/containers`, `WS /api/v1/ws/status`가 사실상 동등 기능 | 컨테이너 테이블 |
| `ensure <target> [--build] [--recreate] [--stream] [--json]` | target의 `depends_on` 폐포를 위상정렬 순서로 `docker compose up -d`(+`init_steps`) 실행 | `POST /api/v1/targets/{target}/ensure` — production에서 두 지점에서 전면 차단(`compose_service.py:4472`, `:4483`, T-044) | `ContainerDetailModal`의 `IS_DEV`(빌드타임 분기) 전용 버튼. 서버가 이미 차단하므로 실행돼도 409 |
| `logs <name> [-f] [--tail N] [--json]` | compose/service 로그 | `GET /containers/{id}/logs`, `WS /ws/logs/{id}` | 실시간 로그 모달 |
| `action <container> start\|stop\|restart` | 컨테이너 제어 | `POST /containers/{id}/action` | Start/Stop/Restart 버튼 |
| `inspect <container> [--json]` | inspect 요약 | `GET /containers/{id}/inspect` | `ContainerDetailModal` 5개 탭 |
| `pinvi-pair rebuild-pinned --confirm [--json]` | Map 4종+PinVi 3종 destructive 재구축(고정 commit fetch → paired build → 3 DB reset → 7 runtime 기동), `rehearsal/rebuildable` 조합 아니면 거부 | 없음 | 없음 |
| `compose-boundary stage-legacy-override / retire-legacy-override / activate-concierge --confirm` | legacy Compose override 이관(1회성) | 없음 | 없음 |
| `db-backup create/list/gc` | pg_dump 백업 생성/조회/정리 | `list`만 `GET /api/v1/backups?role=`(docstring에 create/gc 미노출을 명시) | `BackupHistoryPanel`(읽기 전용) |

추가로 `GET /health`(`main.py:202`)가 있으며 어느 표에도 없어 누락되기 쉽다.

역방향 격차(설계상 자연스러움): config 편집, 메트릭 이력, 인증, 공개 API 키, 로그인
감사는 API/UI 전용이며 `ktdctl`에 동등 CLI가 없다.

`ContainerConfigUpdate`(`api/routes.py:37-46`)는 `ports`/`env`/`volumes`/`networks`를
받지만, **`volumes`는 편집 가능한 필드가 아니라 echo-only 계약이다** — 호출자는 현재
값을 그대로 되돌려줘야 하고, 값이 바뀌면 `save_compose_config`가
`ComposeCandidateContractError("compose candidate volume configuration is immutable
through the Manager API")`로 거부한다(`compose_service.py` ~137-148). `image` 필드는
스키마에 아예 없다.

**컨테이너 단위 조작과 target 재생성의 실제 관계** (초안의 "비대칭" 서술을 정정):
`control_container`(`docker_service.py:676`), `update_container_config`(`:903`),
`reset_container_config`(`:1256`)는 모두 C6c host-wide lock을 획득하고
`assert_environment_snapshot_matches_c6c_lock` → `assert_manager_mutation_allowed`를
거친다 — 즉 이들은 **C6c 보호를 우회하는 게 아니라 그 안에서 실행된다**. `ensure_target`
만 이 위에 **production 전용 추가 하드스톱**을 두 지점에서 건다(`compose_service.py:4472`,
`:4483`, T-044). `assert_c6c_mutation_allowed`라는 함수는 저장소 어디에도 존재하지
않는다(`docs/journal.md`·`docs/tasks-done.md`의 서술과 `ContainerDetailModal.tsx:22`의
낡은 주석에만 등장) — 초안은 이 실재하지 않는 함수를 근거로 "개별 조작은 넓게 허용된다"는
결론을 냈고, 이 결론 위에 (d) `image rebuild-service`를 세웠다. 정정된 사실은 다르다:
config/reset/action이 production에서 이미 허용되는 이유는 "느슨해서"가 아니라 **바이트
단위로 동일한 이미지를 재사용하고, 볼륨 그래프가 불변이며, 실패 시 복원되는 트랜잭션이기
때문**이다(config는 `_validate_compose_candidate` + 실패 시 restore, `env` 값은
`validate_env_entry`가 리터럴 `scheme://user:pass@` 삽입을 거부). 이 성질은 이미지를
재빌드해 교체하는 작업에는 적용되지 않는다 — 코드가 바뀌기 때문이다. 아래 (d)는 이
정정된 전제로 다시 썼다.

---

## (a)+(b) source 상태·정합성 — 통합 설계

초안은 (a) "GitHub pull+build"와 (b) "git revision 정합성"을 별도 절로 다루면서
서로 다른 두 가지 `source status` 설계(로컬 checkout `git rev-parse`만 vs. 실행 중
이미지의 OCI label 비교)를 냈다. 두 리뷰 모두 이 불일치를 지적했고, 하나의 명령으로
합치는 게 맞다는 데 일치했다.

**Today**: `pinvi-pair rebuild-pinned`만 있고, hardcoded commit SHA + root-owned bare
fetch, `rehearsal/rebuildable` 조합 전용이다. Geo/Concierge/Manager 자신을 포함한
일반형 "GitHub에서 pull"은 없다(의도된 부재 — root-owned bare fetch, HTTPS 전용,
clean-tree 강제, 임의 URL/브랜치 파라미터 없음이 기존 설계다). `inspect_c6c_image_source_revision`
(`c6c_deployment.py:6709`)이 이미지 provenance를 검증하는 함수로 존재하긴 하지만, 이는
**fail-closed 검증기**이지 조회 함수가 아니다 — 불변 sha256 image ID를 요구하고
(`_validate_image_id`, `:6650`, mutable 태그 명시적 거부), label이 없거나 40-hex가
아니면 예외를 던진다(`_SOURCE_REVISION_PATTERN`, `:1594`). 지금은 방금 빌드한 candidate
이미지에만 호출된다(`compose_service.py:5368-5378`) — **이 저장소는 실행 중인 Map/PinVi
이미지에 `org.opencontainers.image.revision` label을 아예 찍지 않는다**
(`docker-compose.yml`에 `labels:` 항목이 0개이고, 이미지는
`${KOR_TRAVEL_MAP_API_IMAGE:-kor-travel-map-api:latest-main}`처럼 sibling 저장소가
빌드한 태그 참조로 들어온다). 즉 "이미 있는 검증기를 그대로 읽기용으로 쓴다"는 초안의
전제는 틀렸다 — 새 non-raising wrapper와 label이 없을 때의 `unknown` 경로가 필요하다.

**Gap — 통합 명령 제안**: `ktdctl source-status [--role <role>] [--json]`(하이픈 표기로
통일, `db-backup`/`pinvi-pair`/`compose-boundary`와 일관). **읽기 전용, `--confirm`
불필요**, 모든 `KTDM_DEPLOYMENT_ENVIRONMENT`에서 실행 가능:
- Map/PinVi 7개 runtime: 컨테이너 → image ID를 resolve한 뒤 새 non-raising wrapper로
  OCI revision label을 조회, `current_pinned_runtime_release()`의 pinned SHA와 비교 →
  MATCH/DRIFT/`unknown`(label 부재 시). 원시 label을 그대로 반환하지 않고 기존 redaction
  (`_sanitize_labels`/`_is_sensitive_key`, `docker_service.py:182-242`)을 재사용하고,
  절대 checkout 경로는 응답에 포함하지 않는다(경로 노출이 유일한 실질적 정보 유출 지점).
- Geo/Concierge sibling checkout: `git rev-parse HEAD` + clean/dirty만(네트워크 접근
  없음, 로컬 파일시스템 read만).
- Manager 자신(`--self`): trusted installer가 현재 git commit을 provenance manifest에
  기록하도록 설치 스크립트를 확장해야 조회 가능하다 — 지금은 기록하지 않으므로
  `unknown`으로 fail-close 보고한다. 이 확장 자체는 순수 개선이라 열린 질문이 아니라
  **채택 권고**로 승격한다(아래 우선순위 참고).

**성능/DoS 주의**: 호출당 Map/PinVi 7회 inspect + Geo/Concierge git subprocess가
발생한다. 자동 새로고침 KPI 카드로 만들지 말고 **TTL 캐시(수 분) + 수동 새로고침 +
single-flight guard**를 요구한다 — 없으면 데몬에 부하를 주는 self-inflicted DoS 벡터가
된다.

**UI 노출 제안**: 새 "배포 정합성" 패널(예: `AdminSettingsPanel` 세 번째 섹션, 또는
커맨드 팔레트에서 여는 독립 패널) — `GET /api/v1/source-status`가 집계 결과를 표로
렌더링. **읽기 전용, 액션 버튼 없음.**

**일반형 GitHub pull+build(Geo/Concierge/Manager 자신)**: 이번 사이클에서도 만들지
않는다. 이는 기능 격차가 아니라 **의도된 경계**로 재확인한다 — Manager는 이 세
저장소의 자격증명을 갖고 있지 않고, "fetch해서 신뢰"가 아니라 "검증하고 거부"가 이
프로젝트의 일관된 원칙이다. 오너가 재론하지 않는 한 영구 범위 밖으로 기록한다(이건
"아직 안 만들었다"가 아니라 "안 만들기로 했다"는 명시적 결정이다).

**Risk**: `source-status` 자체는 낮음(read-only, redaction 재사용, TTL 캐시 전제).
`rebuild-pinned` 실행은 여전히 매우 높음(3 DB destructive reset) — **CLI/SSH 전용
유지**.

---

## (c) git 이력/diff 조회 — 재분류: read-only 아님

초안은 `pinvi-pair diff-pinned`(root-owned bare mirror에서 `git log old..new`)를
"read-only, 최우선 후보"로 분류했다. 두 리뷰 모두 이것이 **틀렸다**고 지적했다.

**왜 read-only가 아닌가**: `pinned_runtime_sources.py:298-301`의 fetch는
`--no-tags <canonical_url> <revision>` — **정확히 하나의 revision만**, refspec도
브랜치 참조도 없이 받는다. 기존 pin과 새 pin은 서로 다른 release-digest 디렉터리 아래
서로 다른 bare repo에 들어간다(`pinned_runtime_source_paths()`, `:130-147`) — 한
저장소가 `old..new` 양끝을 동시에 갖는 경우가 없다. 게다가 이 디렉터리는 root가
`_run_root_git(["init","--bare",...])`로 만들고 `0700`으로 잠그며
(`_validate_private_directory`, `:284-293`), FastAPI 백엔드는 root로 실행되지 않으므로
**API/UI 경로에서는 이 디렉터리를 읽을 수조차 없다**. 마지막으로 이 mirror는
`assert_pinned_runtime_rebuild_allowed`(rehearsal 전용) 아래에서만 생성되므로 production
호스트에는 보통 아예 존재하지 않는다. 즉 diff를 보려면 **추가 네트워크 fetch가
필요**하고, 이는 read가 아니라 mutation-adjacent 네트워크 작업이다.

**범위도 좁다**: 이 설계는 Map/PinVi pinned-pair 재구축 직전 diff만 다룬다. 오너가
요청한 "git 업데이트 내역 보기"는 더 일반적인데, Geo/Concierge/Manager 자신의 이력은
전혀 다루지 않는다 — 그쪽은 이미 `git log`/GitHub UI로 충분히 해결되므로 새로 만들
가치가 낮다는 것이 진짜 결론이다.

**GitHub egress에 대한 정정**: 초안은 "production host가 처음으로 능동적 GitHub
HTTPS egress를 낸다"고 썼는데 **이미 사실이 아니다** — `rebuild-pinned`가 이미
`pinned_runtime_sources.py:298`에서 root로 GitHub에 HTTPS fetch를 수행하고 있고,
`_run_root_git`(`:445-473`)이 `core.hooksPath=/dev/null`,
`protocol.file.allow=never`, `protocol.ext.allow=never`, `credential.helper=`로,
`_root_git_environment`(`:488-497`)가 `GIT_CONFIG_NOSYSTEM`,
`GIT_CONFIG_GLOBAL=/dev/null`, `GIT_TERMINAL_PROMPT=0`,
`GIT_ALLOW_PROTOCOL=https`로 이미 강하게 하드닝돼 있다. fetch 뒤
`cat-file -e <sha>^{commit}`(`:302`) 검증이 실질적 방어선이다 — content-addressed
SHA라 악의적 remote가 내용을 바꿔치기할 수 없다. **진짜 위험은 연결 자체가 아니라
트리거와 빈도다**: 지금은 드물게, root로, host lock 아래, rehearsal 게이트, 사람이
TTY에서 시작한다. 이를 HTTP로 트리거 가능하게 만드는 순간 "인증된 웹 요청 하나가 root
서브프로세스의 네트워크 소켓을 연다"는 새로운 위험 등급이 생긴다.

**Gap(재설계안)**: `diff-pinned`를 만들려면 **기존 pinned mirror가 아닌, Manager가
소유하는 별도 scratch bare repo**에 양쪽 revision을 받아야 한다. 그리고:
- CLI/SSH 트리거 전용, **HTTP 핸들러로 절대 노출하지 않는다.**
- `_run_root_git`의 하드닝을 재사용하되, 현재 빠져 있는
  `-c fetch.fsckObjects=true -c transfer.fsckObjects=true`를 추가한다(트리거 빈도가
  올라가는 만큼 미검증 객체를 더 자주 받게 되므로).
- 가능하면 fetch에 `_drop_privileges`(`:500`, 현재 root fetch 경로는 이를 쓰지 않음)를
  적용한다.
- 호스트 방화벽에서 github.com만 허용하는 것을 검토한다.
- 모든 fetch를 journal에 남긴다.

**UI 노출**: diff 결과 자체는 CLI 실행 후 산출물을 (b)의 "배포 정합성" 패널에서
읽기 전용으로 보여주는 것까지만 — fetch를 트리거하는 버튼은 만들지 않는다.

**Risk**: 중간(fetch 자체는 이미 하드닝된 패턴 재사용, 그러나 트리거 표면 확대는
실질적 등급 변화). **우선순위에서 1군에서 제외하고 "정책 결정 + CLI 전용 재설계" 군으로
내린다.**

---

## (d) Docker 이미지 업데이트 — 권장하지 않음(정정)

**Today**: 일반형 없음. `ContainerConfigUpdate`에 `image` 필드가 없다. 이미지가 바뀌는
유일한 경로는 `rebuild-pinned`(rehearsal 전용)와 `ensure --build`(production 전면
차단)뿐이다.

**초안의 논리가 무너지는 지점**: 초안은 "개별 컨테이너 action/config/reset이 이미
production에서 C6c 보호 대상까지 허용되는 비대칭"을 근거로 "비-C6c 서비스의 단일 이미지
재빌드도 같은 패턴으로 production에서 `--confirm` 하에 허용하자"고 제안했다. 위
인벤토리 절에서 정정했듯 그 전제 자체가 틀렸다 — config/reset은 C6c lock **안에서**
실행되는, 이미지가 바이트 단위로 동일하게 유지되는 되돌릴 수 있는 작업이고, 이미지
재빌드는 실행 코드 자체를 바꾸는 되돌릴 수 없는 작업이다. 같은 패턴이 아니다.

추가로 실무적 결함이 세 가지 더 있다:
- **"기존 `ensure`의 후보 검증 경로 재사용"이 불가능하다.** `ensure`는 production에서
  검증 경로에 도달하기 전에 이미 하드스톱으로 막힌다(`compose_service.py:4472`,
  `:4483`). 이걸 우회해 재사용하려면 production compose mutation 전체를 지키는 단일
  신뢰 토큰(`_MANAGED_COMPOSE_MUTATION_CAPABILITY`)을 새로 발급해야 한다 — 이건 "작은
  추가"가 아니라 핵심 방어선에 새 발급 경로를 여는 것이다.
- **provenance/rollback이 없다.** pinned SHA, root-owned bare fetch, `cat-file -e`
  검증, PR #73의 content-addressed active/rollback reference, generation manifest
  v6 — 이 모든 장치는 production 코드에 출처와 롤백을 보장하기 위해 존재한다.
  `rebuild-service`는 prod 디스크에 있는 아무 상태에서나 빌드하고, revision을 기록하지
  않으며, 롤백 참조를 남기지 않는다. prod는 `.git` 없는 rsync 배포본이라 빌드 대상
  트리 자체가 부분 동기화 상태일 수 있다.
- **`--no-deps`가 실제 `init_steps`를 조용히 건너뛴다.** `ensure`는 대상 서비스를 올린
  뒤 target 순서대로 `init_steps_for_target`을 실행한다. `config/docker-targets.yml`은
  실제로 비어 있지 않은 `init_steps`를 여러 target에 정의하고 있다(240, 265, 345행) —
  이 설계가 말이 되려면 `init_steps`가 빈 target으로 한정해야 하는데, 초안은 이 제약을
  전혀 언급하지 않았다.
- "비-C6c"도 작은 집합이 아니다 — 전용 PostgreSQL 4개, RustFS, geo API/UI, concierge
  API/MCP/UI, Grafana/Prometheus/cAdvisor가 전부 해당한다.

**결론**: `image rebuild-service`는 **로드맵에서 제외한다.** 재론한다면 "검증된 SHA
기준 빌드 + active/rollback 참조 보유"로 다시 설계해야 하는데, 그건 결국
`rebuild-pinned`이고 CLI/SSH 영역에 남아야 한다.

---

## (e) 백업

**Today**: `db-backup create/list/gc`가 6개 role을 전부 커버한다. API는 `list`(읽기
전용)만 있고, create/gc는 문서화된 "표준 mutation 경계"로 CLI 전용이다. **restore는
CLI에도 없다** — `routes.py:100-101`이 "Restore isn't implemented anywhere yet (CLI
or API)"라고 명시한다. 초안은 이를 "복원은 영구 CLI 전용"이라 서술했는데 정확히는
**아직 어디에도 구현되지 않았다** — CLI 전용이 아니라 부재다. 이 정정은 사소해 보이지만
로드맵 우선순위에 영향을 준다: "CLI로는 가능하다"는 안전망이 없다는 뜻이다.

**API/UI 노출이 단순 정책 결정이 아닌 이유**: `docs/docker-management.md:955-961`이
이미 실질적 장벽을 명시한다 — 백엔드와 CLI가 `Path.home()`을 다르게 resolve해서 API가
만드는 dump는 `/root/backups`(root 소유 0600)에 생기고, 이는 운영자의 cron이 관리하는
경로와 다르다. 문서는 "root service가 같은 경로에 dump를 생성하도록 확장할 때는 동일
UID 또는 명시적 shared group/ACL을 먼저 정하고, root 소유 0700/0600 artifact를
operator가 읽을 수 있다고 가정하지 않는다"고 이미 못박아 뒀다. 이 UID/ACL 결정 없이
API로 create를 열면 운영자의 `gc --keep 7` cron이 API가 만든 파일을 지우지 못해
보존이 조용히 멈추고, 디스크가 차서 5개 PostgreSQL 인스턴스가 전부 쓰기를 거부하는
사태로 이어질 수 있다.

`gc`는 `create`보다 더 위험하다 — `gc_standalone_backups`는 `_role_lock`을 잡지 않는다
(`create`만 잡음). 즉 UI에서 gc를 누르면 4시간짜리 create와 경합할 수 있다. 복원
경로도 off-box 사본도 없는 상태에서, 로컬 유일 사본을 지우는 버튼을 브라우저에 두는
것은 이 문서 전체에서 위험 대비 효용이 가장 나쁜 항목이다. 또한 `create`는 현재
**동기 처리**라 최대 14400초(role별로 최대 3배까지 누적 가능)를 AnyIO 스레드풀 스레드
하나가 그대로 차지한다 — API로 열려면 반드시 비동기 job(202 + job id + 폴링)이어야
하고, 절대 inline 호출이면 안 된다.

**부수적으로 발견된 기존 `gc` 결함**(이번 설계와 무관하게 이미 존재, 더 넓게 노출하기
전에 고쳐야 함): 선택 로직이 manifest **내용** 기준이라 manifest가 다른 dump 파일을
가리키면 엉뚱한 파일이 지워질 수 있고, manifest 없는 orphan `.dump` 파일은 영원히
수거되지 않는다(`root.glob("*.manifest")` 기준 선택).

**결론(초안 대비 강화)**:
- `create`: UID/ACL 결정 **및** 비동기 job 설계가 선행되지 않으면 착수하지 않는다.
- `gc`: "정책 결정" 군에서 내려 **영구 CLI 전용** 군으로 옮긴다(role-lock 부재로 인한
  경합, 복원 부재와 결합한 비가역성 때문).
- `restore`: 부재를 정확히 기록만 하고, 로드맵에 넣더라도 CLI/SSH 시작을 전제한다.

**Risk**: `create`(재설계 후) 낮음~중간. `gc`(API 노출) 높음. `restore` 미정 — 만들면
높음.

---

## (f) 설정/secret 변경

**Today**: ports/env/networks는 API+UI로 성숙해 있다(volumes는 위에서 정정했듯
echo-only). production에서도 개별 config/reset은 C6c 보호 대상 포함 전부 허용된다 —
이는 위에서 정정한 대로 "느슨함"이 아니라 "이미지 불변·트랜잭션 롤백 보장" 때문이다.
`compose-boundary` 3종은 1회성 마이그레이션이라 반복 투자 가치가 낮다. `CLAUDE.md`가
언급하는 T-045(Map UI credential rotation)는 최신 `docs/tasks-done.md`에서 이미
퇴역 처리됐다(ADR-34/ADR-39로 대체) — `CLAUDE.md` 자체가 이 점에서 낡아 있다(별도
문서 동기화 과제, 이 설계 문서의 범위 밖).

**진짜 격차**: `.env`가 쥔 secret(Map UI password hash/session secret, PinVi role
password, API token 등)의 회전이 SSH+편집기 수작업뿐이다.

**"새 값을 절대 출력하지 않는다"는 원칙은 human credential에는 치명적이다.**
`KTDM_ADMIN_PASSWORD_HASH`(`auth_service.py:72`)와
`KTDM_SESSION_SECRET`(`auth_service.py:412`)처럼 사람이 다시 입력해야 하는 credential을
회전시키면서 새 값을 한 번도 보여주지 않으면 **영구적으로 복구 불가능한 lockout**이다.
설계는 두 클래스를 구분해야 한다:
- **machine secret**(다른 서비스가 프로그램적으로 읽는 값): 절대 출력하지 않는다(원안 유지).
- **human credential**(관리자가 다시 입력해야 하는 값): 회전 직후 한 번, operator TTY에만
  출력하고 log/journal에는 절대 남기지 않는다 — 이는 이미 이 저장소의
  `공개 API 키 생성` 흐름(`AdminSettingsPanel`의 "생성된 키는 지금 한 번만 표시됩니다")과
  같은 패턴이다.

**self-lockout 재분석**: `KTDM_SESSION_SECRET` 회전은 다음 Manager 재기동 시점에
모든 관리자 세션을 무효화한다. 재기동은 (g)에서 보듯 SSH 전용이다. 즉 회전 직후부터
재기동 전까지는 **`.env`의 새 값이 아니라 실행 중 프로세스의 옛 값이 여전히 유효하다.**
초안이 제안한 "마지막 회전 시각"만 보여주는 읽기 전용 패널은 이 상태를 오도한다 —
"회전됨"이라고 표시하지만 실제로는 옛 secret이 여전히 살아 있는 상태일 수 있다.
대신 **`.env` 값의 다이제스트와 실행 중 프로세스가 로드한 값의 다이제스트를 비교해
"재기동 대기 중" 상태를 보여주는 편**이 정확하다.

**재사용할 기존 패턴**: `pinvi_database_role_credentials.py:73+`의
`ensure_pinned_runtime_pinvi_role_credentials`는 이미 하드닝된 `.env` writer다 —
trusted-root 경로 검증(89-92), 파일 바이트에 대한 `hmac.compare_digest` 사전조건
(97-103), 중복 대입 탐지(105), atomic apply. 다만 이 함수의 불변식은 **"한 번만
생성하고 절대 덮어쓰지 않는다"**(부분 상태는 fail-close)이며, `secret rotate`는 정확히
그 반대(덮어쓰기가 목적)다 — 그러니 이 함수를 그대로 재사용하지 말고, **검증 로직만**
가져와 새로 설계한다. 또한 `docs/journal.md`에 T-045가 이미
`ktdctl map-ui-auth rotate`라는 프로토타입을 만들었던 기록이 있다 — 일반형 allowlist를
새로 설계하기보다 그 프로토타입을 확장하는 쪽이 낫다.

**Gap(수정 제안)**: `ktdctl secret rotate <secret-name> --confirm` — 명시
allowlist된 키만, machine/human 클래스 구분, human 클래스는 회전 직후 1회 TTY 출력,
회전과 재기동을 분리된 확인 단계로 나눔(기존 `compose-boundary stage`/`retire` 패턴과
동일), 모든 회전을 감사 테이블에 기록(아래 "감사 로깅" 참고).

**UI 노출**: rotate 버튼은 만들지 않는다(원안 유지, 강화된 이유로). "재기동 대기 중"
다이제스트 비교 상태만 읽기 전용으로 노출한다.

**Risk**: rotation 실행 자체는 여전히 높음(self-lockout, 연쇄 재기동 필요) — CLI 전용
유지. 상태 표시는 낮음.

---

## 새로 추가: 이미 읽을 수 있는 pinned-runtime generation manifest 노출

두 리뷰 중 하나가 초안에 없던 항목을 찾아냈고, **이번 문서에서 1순위 후보로 승격한다.**
`services/pinned_runtime_generation.py`는 generation 상태(예: `manifest_committing`
등), 활성 generation의 `image_ids`, `schema_heads`를 담은 durable JSON manifest를
관리한다(`pinned_runtime_manifest_path()`, `:308-311`). 이 디렉터리는
**root가 아니라 현재 Manager 소유자 권한으로 0700**이다
(`ensure_pinned_runtime_state_directory`, `:342-349`) — 즉 **백엔드 프로세스가 이미
읽을 수 있다.** "지금 어떤 generation이 살아 있는가"라는, 오너가 원하는 것과 정확히
같은 질문에 답하면서도 `source-status`보다 구현 비용이 낮고 새 wrapper나 unknown 처리도
필요 없다.

**제안**: `GET /api/v1/pinned-runtime/manifest`(읽기 전용) — 현재 committed
generation의 phase, image_ids, schema_heads를 그대로 노출(secret 없음, 이미
non-sensitive JSON). UI는 (b)의 "배포 정합성" 패널 상단에 이 요약을 배치.

**Risk**: 낮음. 이번 사이클 최우선 후보로 (b)보다 앞에 둔다.

---

## (g) 기타 운영 격차

1. **Manager 자기 배포 상태/재기동**: 자기 자신을 서빙 중인 프로세스가 스스로
   재기동하면 그 요청 자체가 끊긴다 — **영구히 SSH 전용**이 맞는 경계다(격차가
   아니라 의도된 경계).
2. **`compose-boundary` 3종**: legacy override 이관이 끝나면 점차 쓸모가 줄어드는
   1회성 코드다. UI 투자 가치 낮음.
3. **Docker 디스크 사용량/정리**: `c6c_image_retention.py`는 pinned generation
   이미지만 관리하고, 반복된 `ensure --build`가 쌓는 dangling image/build cache는
   아무도 관리하지 않는다. `ktdctl docker disk-usage`(읽기 전용, `docker system df`
   래퍼)와 `ktdctl docker gc --confirm`(비보호 이미지만 prune)을 제안한다. 읽기
   전용 쪽은 **TTL 캐시 + 수동 새로고침 + single-flight guard**를 전제로 UI KPI
   카드에 넣을 가치가 있다 — 전제 없이 자동 새로고침으로 만들면 데몬에 부하를 주는
   self-inflicted DoS다.
4. **감사 로깅(교차 항목)**: 이 문서가 제안하는 새 mutation(특히 `secret rotate`,
   `db-backup create`)은 전부 기존 로그인/API-key 감사(`admin.py:56-81`)와 동일한
   수준의 durable audit row가 필요하다. 초안에는 이 요구사항이 없었다 — 모든 신규
   mutation 제안에 공통 전제로 추가한다.
5. **Push 알림**: 컨테이너 다운/백업 실패를 능동적으로 알리는 경로가 없다. 이미 있는
   Grafana로 해결하는 것이 자연스럽고, 이번 설계 범위 밖으로 유지한다(단, Grafana가
   실제로 Manager를 스크레이핑하는지는 별도로 확인이 필요하다 — 이 문서는 그 전제를
   검증하지 않았다).

---

## 우선순위 권고(리뷰 반영 후 재정렬)

**먼저 만들 가치 있음(낮은 리스크, 이미 접근 가능한 상태 기반)**
1. **pinned-runtime generation manifest 읽기 전용 노출** — 새로 발견한 항목, 이미
   backend가 읽을 수 있는 0700 자기 소유 디렉터리, 구현 비용 최저.
2. `ktdctl source-status`(통합안) + `GET /api/v1/source-status` 읽기 전용 패널 —
   단, 새 non-raising wrapper·redaction 재사용·TTL 캐시가 전제.
3. `ktdctl docker disk-usage` 읽기 전용 카드 — TTL 캐시·수동 새로고침·single-flight
   전제.
4. trusted installer에 git commit provenance 기록 추가(`source-status --self`
   전제조건) — 순수 개선, 열린 질문에서 승격.

**가치는 있으나 정책 결정 + 상당한 재설계가 먼저 필요함**
5. `db-backup create`의 API/UI 노출 — UID/ACL 결정 **및** 비동기 job 설계 둘 다 선행.
6. git 이력/diff 조회(구 `diff-pinned`) — 별도 Manager 소유 scratch mirror로 재설계,
   CLI/SSH 트리거 전용(HTTP 핸들러 금지), `fsckObjects` 추가, 결과만 UI에서 읽기 전용
   표시.

**신중, 당장 만들지 않기를 권고**
7. `secret rotate` — CLI 전용, human/machine 클래스 구분 재설계, T-045 프로토타입
   기반. UI는 "재기동 대기" 상태 표시까지만.
8. `db-backup gc`의 API/UI 노출 — role-lock 부재로 인한 경합 위험 + 복원 부재 결합,
   영구 CLI 전용으로 하향.
9. `db-backup restore` — **아직 존재하지 않음**(CLI 전용이 아니라 부재). 로드맵에
   넣더라도 CLI/SSH 시작 전제.

**로드맵에서 제외**
10. `image rebuild-service` — 정정된 전제(§d)에서 코히런트하지 않음. 재론한다면
    검증된 SHA+rollback 참조를 갖춘 `rebuild-pinned` 계열로 다시 설계해야 하며, 그건
    CLI/SSH 영역이다.
11. `pinvi-pair rebuild-pinned` 실행 버튼 자체 — UI에 절대 넣지 않는다.
12. `compose-boundary` 3종 — 1회성 레거시, 투자 보류.
13. Geo/Concierge/Manager 자신에 대한 일반형 GitHub pull+build — 의도된 영구 범위 밖
    결정으로 기록(재검토는 오너 판단).

---

## 오너가 결정할 열린 질문

1. `db-backup create`를 API/UI에 노출할지 — UID/ACL 결정과 비동기 job 설계가 둘 다
   선행돼야 하는 큰 작업이다. 이 투자를 이번에 할지.
2. git 이력/diff 조회를 위해 Manager가 별도 scratch mirror로 GitHub HTTPS fetch를
   (CLI 트리거로 한정해서라도) 늘리는 것을 허용할지. 기존 `rebuild-pinned`의 egress는
   이미 실재하고 하드닝돼 있으므로, 질문은 "egress를 처음 허용하는가"가 아니라
   "트리거 빈도/표면을 늘리는가"다.
3. `secret rotate`를 이번 사이클에서 만들지 — T-045 프로토타입(`ktdctl
   map-ui-auth rotate`)을 확장하는 형태로. human/machine 클래스 구분과 TTY 1회 출력
   설계에 동의하는지.
4. `db-backup restore`를 로드맵에 넣을지 — 지금은 CLI/API 어디에도 없다는 것을
   전제로 논의를 시작해야 한다.
5. `docker gc --confirm`(비보호 이미지 prune)을 CLI에 추가할지, 읽기 전용
   `disk-usage`만으로 충분한지.
6. 이 문서가 발견한 CLAUDE.md의 두 지점(퇴역한 `pinvi-pair capture` 언급, 퇴역한
   T-045 언급)을 별도 문서 동기화 작업으로 처리할지 — 이 설계 문서의 범위 밖이라
   여기서는 사실만 기록한다.
