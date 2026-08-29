# ktdctl → UI 이관 및 운영 기능 격차 설계 (v4)

## 범위와 비목표

이 문서는 `ktdctl`이 운영 정본인 경계를 설명하는 설계·이행 문서다. 일반 UI 이관 항목은
여전히 설계 초안일 수 있지만, 아래 v4 M05 execution identity 계약은 구현·검증 중인 정본이다. 목적: (1) `ktdctl`이 이미 할 수 있는
일 중 UI에 아직 없는 것을 찾아 이관 후보를 정리하고, (2) 운영에 필요하다고 지목된 영역
— GitHub source pull+build, git revision/계약 정합, git 이력 조회, Docker 이미지
업데이트, 백업, 설정/secret 변경, 기타 — 의 격차와 안전장치를 제안하며, (3) v2에서
추가: 반복되는 pin 회전 작업의 설정파일화, 비전문 관리자 편의성 중심의 우선순위
재정렬, 구조 리팩토링 필요성 평가, (4) **v3에서 추가**: kor-travel-map·pinvi·본
저장소 세 곳의 2026-08-25~28 커밋 교차 감사에서 발굴된 이슈와 개선 포인트, 그리고
전체 작업의 태스크 단위 분해를 다룬다.

**비목표**: 이 문서는 구현하지 않는다. 제안된 CLI 서브커맨드, API 엔드포인트, UI 화면은
전부 미착수 상태이며, 표기된 서명·경로·플래그는 설계 초안이지 확정 계약이 아니다.
map·pinvi 저장소 쪽 태스크(3부)는 본 저장소에서 실행할 수 없으므로, 착수 시 해당
저장소의 tasks 문서로 미러링하는 것을 전제로 여기 기록만 한다.

## 개정 이력

- **v4 (2026-08-29)**: M05 control-plane terminal 재발 방지 구현을 반영했다. runtime
  pin mutation(`init`, `publish-generation`, `rotate`, `rotate-pair`, `apply-pending`,
  `rollback`, `block`)은 모두 `ktdctl`의 host-global mutation lock 안에서 대상 read·검증·
  derive·write·대기 요청 정리까지 원자화하며, 실행 중 one-shot과 병렬인 외부 write는 거절한다. 검증된 launcher inherited-lock의
  terminal fallback만 예외다. 또한 Map·PinVi·Manager의 문서 전용 병합은 candidate
  source tuple·provenance·pinset을 다시 만들지 않고, 코드·Compose·계약·빌드 입력 변경만
  새 CI·전문 리뷰·one-shot 후보를 만든다.
  PR synchronize CI가 생성되지 않으면 `workflow_dispatch`로 같은 exact head의 read-only
  CI를 재실행한다. 이는 runtime mutation이나 candidate 재결박 권한을 주지 않는다.

- **v1**: 보안/안정성 우선의 초판(ktdctl 인벤토리 + 7개 운영 영역 격차 분석).
- **v2**: 오너 방향 재지정("보안·안정성보다는 **비전문가의 관리 편의성·직관성** 중심")
  에 따라 전 항목 재평가. 5개 분석 축(웹 UI 재점검 / pin 하드코딩의 설정파일화 / 기능
  격차 재검증 / 비전문가 직관성 / 구조 리팩토링)을 독립 조사 후, 사실 정확성·지시
  정합 두 전문 리뷰를 반영. 열린 질문 7건은 오너가 전부 결정(문서 말미 표).
- **v3 (2026-08-28)**: 오너 지시로 (1) P1 트레이드오프를 항목별 상세 서술로 확장,
  (2) kor-travel-map(3일 99커밋)·pinvi(25커밋)·본 저장소(실질 94커밋)의 2026-08-25~28
  커밋 전수를 저장소별 전담 조사로 분석해 계약·pinning·결박 관련 이슈를 발굴·반영,
  (3) **발굴 이슈와 P1 계열 개선을 문서 1순위(1부)로 재편**, (4) 전 항목에
  "무엇이 왜 문제인가 / 지금 어떤 불합리를 만드는가 / 수정 후 무엇이 개선되는가"
  상세 서술 추가, (5) 전체 작업을 태스크 단위로 분해(3부), (6) map·pinvi 쪽
  수정사항도 동급 상세로 포함. 기존 라인 인용 전체는 `f0edac7`에서 재검증했다(오류
  0건 — 단 사실 서술 정정 3건은 1부·2부에 반영).
- **v4 (2026-08-29)**: M05 one-shot terminal 반복을 문서 SHA 변경으로 우회하던 구조를
  해소한다. v5 Map·PinVi source pinset은 역사적 materialization identity로 보존하고,
  trusted installed Manager revision을 더한 v6 execution identity를 execution ledger,
  terminal block, public generation binding, PinVi admission과 Map attestation의 새 실행
  권한으로 분리한다.

---

## v4 M05 실행 identity — `ktdctl`이 실제로 수행하는 결박

v5 `pinset_sha256`은 Map·PinVi revision 두 개로만 계산한다. 그래서 Manager의 실제
bug fix가 새 trusted release에 들어가도 동일 source pair는 과거 terminal block 때문에
새 one-shot을 만들 수 없다. Map/PinVi 문서 commit을 새 SHA로 만들어 pinset을 바꾸면
코드 변화와 무관한 CI·전문 리뷰·PR·rebuild/E2E를 반복하고 기록도 왜곡한다.

해결은 v5 pinset을 바꾸는 것이 아니다. `ktdctl`은 v5 source pinset, canonical
Manager repository URL, trusted installed Manager revision으로 v6
`execution_identity_sha256`을 계산한다. 이 값만 새 Manager implementation으로의
M05 execution namespace·one-shot ledger·terminal block을 결정한다.

Manager revision은 CLI 인자·UI 요청·환경변수에서 절대 받지 않는다. clean trusted
install root의 `.ktdm-source-revision`과 `.ktdm-release-manifest.json`을 root
no-follow 검사로 함께 읽어 exact match할 때만 입력으로 쓴다.

- `ktdctl pin migrate-execution-v6 --confirm`은 v5 registry의 history와 blocked
  pinset을 변경 없이 legacy audit으로 보존하고 v6 execution registry를 만든다. current
  v5 source가 terminal이어도 그 기록에는 Manager revision이 없으므로, 임의의 과거 release에
  terminal을 귀속하지 않는다. 현재 trusted release의 v6 execution 하나만 미차단으로 만들고,
  legacy terminal은 v5 source audit에 그대로 둔다. 새 execution의 terminal은 실제 v6 실행
  결과로만 기록한다.
- `ktdctl pin rebind-execution --expected-manager-revision <40-hex> --confirm`의
  revision은 trusted installed revision과의 TOCTOU 확인값일 뿐이다. terminal current와
  다른 trusted Manager revision일 때만 Map/PinVi source를 건드리지 않고 새 execution
  identity를 만든다.
- `pin verify`와 read-only UI/API는 v5 source pinset, Manager revision, v6 execution
  identity, legacy v5 terminal 수, v6 execution terminal 수를 분리 표시한다. v6 success
  gate는 구 `pinset_binding`만으로 green을 판단하지 않는다.
- PinVi admission·activation receipt와 Map attestation은 Map SHA, PinVi SHA, v5 source
  pinset, Manager SHA, v6 execution identity를 모두 exact 대조한다. 한 필드라도 다르면
  fail-close한다.
- 문서-only Map/PinVi merge는 위 execution identity를 바꾸지 않고 즉시 병합한다. raw
  E2E forensic은 M05 완주 전까지 gitignored local 분석 파일에만 기록한다.

---

# 1부 — 최우선: pin·결박 구조의 문제 진단과 개선 설계

v3 교차 감사의 결론을 한 문장으로 요약하면: **Manager가 이미 기계(durable state)로
보유한 진실을 두 sibling 저장소가 Markdown으로 수기 복제하고 있고, Manager가 코드로
강제할 수 있는 운영 규약을 세 저장소의 사람이 문서 규율로 지키고 있다.** 아래 진단
6건이 그 구체적 증거이고, 이어지는 설계(P1 확장 + P10)가 해법이다.

## 1.1 문제 진단 — 무엇이 왜 문제이고, 지금 어떤 불합리를 만드는가

### 진단 1. pin이 소스코드 상수라서 값 1-2개 변경이 릴리스 사이클로 증폭된다

- **무엇이 문제인가**: Map/PinVi의 pinned revision(40-hex 2개)과 그 파생 digest가
  `services/pinned_runtime_release.py:169,174,183`의 코드 상수이고, Map revision의
  중복본이 `services/map_application_300.py:26-28`(`MAP_APPLICATION_300_SOURCE_COMMIT`)
  에 하나 더 있다.
- **왜 문제인가**: pin은 이 시스템에서 가장 자주 바뀌는 값이다. 실측(2026-08-25~28,
  3.5일): **회전 15회**(Map 9·PinVi 5·동시 1). 최근 200 커밋 중 42개가 "pin"을
  언급하고, `pinned_runtime_release.py`는 도입 후 23커밋을 겪었다. 가장 자주 바뀌는
  값이 가장 바꾸기 비싼 곳(코드+테스트+배포본)에 있다.
- **지금 만드는 불합리**: 회전 1회 = 의미상 40-hex 1-2개 변경이 → 코드 2파일 +
  테스트 1-2파일의 하드코딩 기대값(실측 `db77fcd`는 테스트 두 파일 합계 86줄) +
  journal/tasks 기록 = **전형 5개(고립 회전 10건 실측 중앙값 5, 범위 2-6) 파일 수정
  + PR + 배포 + 백엔드 재기동**으로 증폭된다. prod의 canonical execution root는
  trusted installer가 staging→commit으로 통째 교체하는
  `/opt/kor-travel-docker-manager`이므로(`docs/prod-deployment.md` — rsync 갱신은
  legacy 경로로 강등), pin이 코드 상수인 한 회전마다 전체 release 설치 사이클이
  강제된다. `pinset_sha256`은 계산 가능한 파생값인데 사람이 손으로 재기재하고
  (`__post_init__` `:141-145`의 재계산 대조가 실수를 잡아주긴 한다), Map revision은
  이원 관리라 어긋나면 rebuild가 candidate admission(`map_application_300.py:471-476`)
  에서야 늦게 fail한다.
- **수정 후 개선**: pin registry 파일(설계 §1.2)로 옮기면 회전 = **명령 1개**
  (`ktdctl pin rotate`)가 되고, digest는 자동 계산, 중복 상수는 소멸, Manager 재기동
  불필요(캐시 설계 §1.2-a′), 릴리스 설치도 불필요해진다. 비전문 관리자 기준 "GitHub
  에서 SHA 복사 → 명령 하나"가 현행 "5-6파일 편집 + digest 수동 계산 + 테스트 기대값
  갱신 + PR + 설치 + 재기동"을 대체한다.

### 진단 2. 회전의 blast radius가 Manager를 넘어 세 저장소에 걸친다

- **무엇이 문제인가**: pin 회전 1회가 Manager 저장소 밖에서도 수기 작업을 강제한다.
- **왜 문제인가 (실측)**: 같은 3일 창에서 —
  - **kor-travel-map**: non-merge 74커밋 중 **pinset·candidate 상태 부기(簿記) 전용
    커밋 19건**(+1,752/-1,330줄). 그중 2건(`4ba025d6`, `dd2e2a4a`)은 diff가 **순수
    SHA 문자열 치환뿐**이다. 08-27 하루에는 11시간 동안 pinset이 10회 회전했고(8분
    간격 3연속 포함) 사람이 매번 따라 적었다. map은 심지어 **Manager의 미머지 draft
    PR head SHA를 6회 전사**했다 — 제3 저장소가 Manager 내부 상태를 수동 복제하는
    구조다.
  - **pinvi**: 25커밋 중 10건이 docs-only. `docs/resume.md`(5,472줄, 294개 엔트리)
    상단 120줄에만 **축약 SHA 14개**가 등장하며, 그 값 전부(양 저장소 revision,
    pinset, application/Dagster/alembic head, image digest)는 **Manager의 generation
    manifest(v6)/journal(v8)이 이미 durable하게 보유한 상태의 수기 사본**이다. 회전
    1회당 pinvi 쪽 추가 비용 = 계약 JSON 1 + 테스트(`_UPSTREAM_COMMIT` 2곳) 1 + 문서
    3 = **5파일**.
- **지금 만드는 불합리**: 실제 회전 비용은 "Manager 4-6파일"이 아니라 **pair 전체
  9-11파일 + 제3 저장소 문서 부기**다. 같은 사실이 세 곳에 세 번 적히므로 어긋날
  자유도만 늘어난다(실제로 pinvi journal이 "재결박했다"고 기록한 시점에 contract
  파일은 아직 옛 값이던 사례가 관측됐다 — 재결박 커밋이 문서 커밋과 분리돼 있었다).
- **수정 후 개선**: Manager가 회전 이력·generation 결과의 **단일 진실 공급원 API**
  (§1.3 P10-2)를 제공하면 sibling 문서의 값 전사는 참조 1줄로 대체되고, pinvi의 계약
  JSON 대조는 CI 자동 검증(3부 KUM-PV-2)으로 바뀐다. P1의 ROI 논거는 v2 주장보다
  실제로 **더 강하다**.

### 진단 3. pinset의 생애 상태(untried/committed/terminal)가 기계 어디에도 없다

- **무엇이 문제인가**: "terminal(실패 종결) 판정된 pinset은 영구 재시도 금지"라는
  핵심 운영 규약이 **세 저장소의 문서에 수기로만** 존재한다 — map `docs/tasks.md`의
  차단 목록(10개 digest 수기 누적, 회전마다 1개씩 증가), pinvi `docs/tasks.md`의
  terminal 7건 나열("재실행하지 않는다"가 문서 전반에 12회 반복), Manager 코드의
  d9 legacy 상수.
- **왜 문제인가**: 규약을 어겨도 기계가 막지 않는다. 실측 사례로, 동일 공식 재개를
  1회 더 실행한 것만으로 두 전문 적대 리뷰가 "세 번째 재시도 금지" 판정을 내려야
  했다 — 사람이 세는 실행 횟수가 유일한 방어선이다. **결정적 증거: 현행 Manager
  main의 pin(`pinset cbb577d3…`, `pinned_runtime_release.py:183`)은 pinvi journal과
  map 차단 목록이 이미 terminal(재시도 금지 역사 증거)로 선언한 pinset인데, Manager
  코드만 봐서는 이를 알 방법이 전혀 없다.**
- **지금 만드는 불합리 (d9 상수 축적)**: v2는 `pinned_runtime_release.py`의 d9
  상수를 "역사적 고정값(전환 대상 아님)"으로 분류했는데 **이는 정정 대상이다**.
  `:38,41,44`의 `_D9_LEGACY_ROLE_TOPOLOGY_*` 3종(pinset sha + Map/PinVi revision)은
  `98e0ccc`(08-27)에서 신설됐고 값은 직전 live pin을 그대로 냉동한 것이다. 즉
  **실패한 rebuild 위를 회전이 지나갈 때마다 "차단 pinset" 상수가 코드에 새로
  축적**되는 구조이며(소비처: `is_d9_legacy_pinvi_role_topology_retry` `:104-119` →
  `compose_service.py:5546-5556` admission fail-close), pin registry가 "현행 pin"만
  담아서는 이 축적이 제거되지 않는다.
- **수정 후 개선**: pinset lifecycle을 registry의 1급 개념으로 만들면(§1.3 P10-1) —
  (i) terminal pinset 재실행을 `rebuild-pinned`가 자동 거부(사람의 기억 → 기계의
  게이트), (ii) d9류 상수 신설이 `pin block` 한 명령으로 대체(코드 churn 소멸),
  (iii) map/pinvi의 수기 차단 목록이 API 조회로 대체, (iv) "지금 pin이 이미 죽은
  pinset"이라는 현행 상태가 화면에 드러난다.

### 진단 4. 실패 진단이 stderr 문자열과 owner-only 파일에 갇혀 있다

- **무엇이 문제인가**: rebuild 실패의 원인 분류가 두 개의 취약한 경로에 의존한다.
  - Manager는 PinVi bootstrap 스크립트의 **stderr 문구 9개를 문자열 그대로** 분류
    키로 파싱한다(`compose_service.py:478-497`). PinVi가 문구를 한 글자만 바꿔도
    Manager의 분류는 `unclassified`로 추락한다. 정작 PinVi는 typed JSON 진단
    (`pinvi.role-topology-diagnostic.v1`: stdout 1줄 고정, status 4종 + reason enum
    10종 + 오류 enum 3종, `PINVI_ROLE_TOPOLOGY_VERIFY_ONLY=1`)을 이미 제공하는데
    (`f1d8e04c`) Manager는 소비하지 않는다(저장소 전수 grep 0건). verifier 호출 순서
    계약도 pinvi journal 산문으로만 존재한다.
  - builder 실패는 `569d823`이 stdout/stderr를 `subprocess.DEVNULL`로 버리고
    `7fe0369`가 분류를 **receipt 파일 존재 관측**으로 교체한 이후, root 소유
    receipt 2종(`api-candidate-build.json`/`paired-candidate-build.json`,
    `compose_service.py:2595-2634`)이 **유일한 증거**다. 실제로 08-27의 한 실패는
    당시 "`api_receipt_missing`"으로 기록됐다가 나중에 "generic builder 오류였고 그
    분류는 사후 진단에서만 가능했다"고 소급 정정됐다 — Manager가 내보낸 실패 신호가
    원인 특정에 부족했다는 뜻이다.
- **왜 문제인가 / 불합리**: 실패 1건마다 "원인을 아는 사람"이 root 셸로 파일을
  열어봐야 하고, 그 결과를 다시 sibling 문서에 수기로 옮긴다(map의 durable failure
  receipt 부재 지적 → 이후 terminal enum 도입으로 일부 해소된 흐름이 이 3일 창에
  그대로 관측된다). 비전문 관리자에게는 완전한 블랙박스다.
- **수정 후 개선**: (i) typed JSON 소비로 이관하면 문자열 결합이 끊기고 reason
  enum이 그대로 화면 배지가 된다(§1.3 P10-3). (ii) receipt·terminal 분류를 P2
  generation API가 노출하면(2부 P2-4) "왜 멈췄나"가 SSH 없이 보인다.

### 진단 5. 실행해야만 아는 실패 — preflight 가능한 결손이 사후에 발견된다

- **무엇이 문제인가**: 이 3일 창의 실제 blocker 3건이 전부 "실행 전에 알 수 있었던"
  결손이다.
  1. exact `python@sha256:…` base image가 n150 image store에 없어 candidate build가
     fail-close(builder는 `--pull=false` + exact base inspect 강제). map 저장소는
     manual pull을 금지하고 "**trusted Manager preflight가 digest-pinned base를
     provision·재관측**"할 것을 명시 요구했다.
  2. 오프라인 wheelhouse에 build dependency(`poetry-core`) wheel이 없어 trusted
     installer가 활성화 전 fail-close — 설치를 시도해야만 결손이 드러났다.
  3. legacy `docker-compose.override.yml`의 존재가 승인된 rebuild 전체를 막았다
     (single-file Compose 계약 위반) — 읽기 전용 topology 조사로만 원인을 알아냈다.
- **왜 문제인가 / 불합리**: 각 실패는 pinset 1개를 소모하고(재시도 금지 규약),
  sibling 문서 부기 1라운드를 만들고, 사람의 반나절을 쓴다. 셋 다 read-only 검사로
  사전에 알 수 있는 정보다.
- **수정 후 개선**: 배포 정합성 패널에 readiness 행 4종(base image N중 M / wheelhouse
  wheel N중 M(빌드 의존 포함) / Compose 입력 single-file 여부 / sibling 필수 파일
  존재·모드)을 read-only로 추가하면(§1.3 P10-4) "누르기 전에 실패를 안다". 이것이
  이 패널의 최고 가치다.

### 진단 6. 관측 설계(P2)의 전제가 틀려 있었다 — backend는 현재 읽을 수 없다

- **무엇이 문제인가**: v2의 P2는 "mode 게이트 없는 `pinned_runtime_state_root()`
  기반으로 조회 route를 구성하면 된다"고 했다. **실제 제약은 mode 게이트가 아니라
  권한 모델이다.** state root는 root 실행 기준
  `/root/.local/state/kor-travel-docker-manager`(`pinned_runtime_generation.py:167`,
  rebuild는 root 강제)이고 디렉터리 `0700`·파일 `0600`인 데다, 리더 함수 자체가
  **호출 프로세스 uid·파일 mode 불일치 시 fail-close**한다(`_validate_state_parent`
  `:3201-3213`, `_validate_private_file_stat` `:3216-3224`). 비-root uvicorn은
  권한상으로도, 검증 로직상으로도 manifest/journal을 읽지 못한다.
- **지금 만드는 불합리**: "이미 존재하는 상태의 노출은 싸다"는 v2의 우선순위 논거가
  이 지점에서 무너진다 — P9-1단계의 "~200줄 신규, 기존 코드 무변경" 추정도 같이
  틀린다.
- **수정 후 개선**: P1-(c)가 pin registry를 위해 설계한 **root-side world-readable
  publisher**(root가 실행하는 rotate/rebuild가 secret 없는 사본을 backend 가독
  경로에 원자적으로 기록)를 P2도 공유한다. publisher를 1회만 만들면 pin·generation·
  journal 노출이 전부 그 위에 선다. 우선순위 함의: **P2는 P1보다 싸지 않고 P1의
  publisher에 의존한다**(2부 P2-2, 3부 KUM-M3에 반영).

## 1.2 설계 — P1. 반복 pin 회전의 설정파일화 (v2 승인 Q1 + v3 확장)

### 설계 (a) — pin registry 파일 (root 소유 0600, self-describing)

```json
{
  "schema": "kor-travel-docker-manager.runtime-pin-registry.v1",
  "release_version": 5,
  "sources": [
    {"role": "map",   "url": "https://github.com/digitie/kor-travel-map.git", "revision": "<40-hex>"},
    {"role": "pinvi", "url": "https://github.com/digitie/pinvi.git",          "revision": "<40-hex>"}
  ],
  "rotated_at": "...", "rotated_by": "...", "reason": "...",
  "pinset_sha256": "<rotate 시 자동 계산되어 기록>",

  "supersedes": {"pinset_sha256": "<직전>", "terminal_class": "<직전이 죽은 이유>"},
  "history": [{"pinset_sha256": "...", "rotated_at": "...", "rotated_by": "...", "reason": "...", "supersedes": {}}],
  "blocked_pinsets": [{"pinset_sha256": "...", "map_revision": "...", "pinvi_revision": "...", "phase": "...", "reason": "...", "blocked_at": "..."}]
}
```

하단 3개 필드가 **[v3 확장]**이다(근거: 진단 2·3). `supersedes`는 "직전 pinset이 왜
죽었고 무엇으로 대체되는가"를 구조화해 map이 문서 3곳에 손으로 쓰던 내용을 대체하고,
`history`는 회전 체인의 이력 피드(P10-2), `blocked_pinsets`는 terminal pinset 영구
차단 목록(P10-1)이다. d9 legacy 상수 3종은 이 목록의 첫 항목들로 이관돼 코드에서
삭제된다.

- 로딩은 기존 선례를 그대로 탄다: `services/registry.py:16-35`가 이미 env 오버라이드
  + 구조 검증 + `lru_cache`로 `config/docker-targets.yml`에 동일한 일을 한다.
  `current_pinned_runtime_release()`(`pinned_runtime_release.py:187-190`)의 시그니처를
  유지한 채 내부만 파일 로드로 바꾸면 **rebuild 경로의 소비처는
  `compose_service.py:6018` 한 곳**이라 전파가 작다.
- **검증은 전부 유지**: `PinnedRuntimeSourceSpec.__post_init__`(`:56-63`)의 canonical
  URL 강제·40-hex 검증, `PinnedRuntimeRelease.__post_init__`(`:129-145`)의 role
  순서·digest 재계산 대조가 파일 파싱 직후 그대로 실행된다. **URL은 파일에 있어도
  코드의 `CANONICAL_RUNTIME_SOURCE_URLS`와 불일치하면 fail-close** — 파일로 옮겨도
  "임의 저장소를 가리키게 조작"은 코드 수정 없이는 불가능하다. 이것이 이 전환의 안전성
  핵심 논거다.
- `pinset_sha256`은 파일에 기록하되 로드 시 항상 재계산 대조(부분 편집·truncation 방어).
- `MAP_APPLICATION_300_SOURCE_COMMIT` 상수는 삭제하고 `release.source_for("map").revision`
  참조로 통합(진단 1의 이원 관리 hazard 소멸).
- **[v3] 범위 밖 pin의 명시**: `docker-compose.yml:158,262,300`의 postgis base digest
  3중 중복(같은 파일 `:9,:93,:228,:1377`은 floating tag라 파일 내부 불일치)과 Map
  base image digest(sibling Map 저장소 Dockerfile 2개에서 동적으로 읽음,
  `compose_service.py:3194-3245`)는 이 registry의 범위 밖이며 별도 정리
  대상으로만 기록한다(3부 KUM-M15와 인접하나 독립).

### 설계 (a′) — 배포·캐시·부트스트랩 (구현 착수의 전제)

- **파일 위치는 배포 트리 밖의 prod-local 경로다.** **[v3 근거 갱신]** prod의
  canonical execution root는 trusted installer가 staging→commit으로 통째 교체하는
  `/opt/kor-travel-docker-manager`다(rsync 배포본 갱신은 legacy 경로로 강등,
  `docs/prod-deployment.md`). registry가 그 트리 안이면 다음 release 설치가 회전
  결과를 덮는다 — 트리 교체형 배포라 위험은 rsync 시절보다 오히려 크다.
  `services/registry.py:17-20`의 env 오버라이드 선례로 `KTDM_RUNTIME_PINS_FILE`
  경로를 지정하고(개발 기본값은 저장소 내 `config/runtime-pins.json`, prod는 `/opt`
  트리 밖 운영 경로), 런북의 보존 파일 목록에 등재한다.
- **캐시 무효화**: `registry.py`의 `lru_cache` 선례는 "불변 파일" 전제라 그대로 쓸 수
  없다. 로드 시 mtime+digest 검사로 재로드하거나 캐시 없이 매 호출 로드한다(rebuild
  시작 시 1회 + 조회 API뿐이라 성능 무의미). 따라서 **`pin rotate`는 실행 중 Manager에
  재기동 없이 즉시 반영된다** — 현행 대비 핵심 개선점이다.
- **부트스트랩과 부재 시 동작**: 최초 1회 `ktdctl pin init`(현행 코드 상수 값으로 파일
  생성)을 제공하고, 이후 **파일 부재·파싱 실패·digest 불일치는 전부 fail-close**다(상수
  폴백 없음 — 폴백이 있으면 "파일이 진실"이라는 단일성이 깨진다).
- **[v3] world-readable 사본 기제는 새 발명이 아니다**: trusted installer가 이미
  `.ktdm-source-revision`·`.ktdm-release-manifest.json`을 root:root `0644`로 앱
  루트에 쓰는 선례(`scripts/install-ktdm-trusted-release:932-934,1140-1145`)를
  답습한다.

### 설계 (b) — `ktdctl pin` 서브커맨드 패밀리

- `ktdctl pin init --confirm` — 최초 부트스트랩(위).
- `ktdctl pin show [--json]` — registry 내용 + digest + 회전 메타 + **[v3]** lifecycle
  (`history`·`blocked_pinsets` 포함). 읽기 전용.
- `ktdctl pin verify [--json]` — digest·canonical URL과 registry/generation public copy strict
  대조. 읽기 전용. pair 회전 직후의 완전한 이전 committed generation 또는 registry가 exact로
  차단한 terminal generation은 `pending_rebuild`로 분리한다. `pinned-rebuild/preflight`도 같은
  `match|pending_rebuild` gate를 써서 서로 다른 실행 안내를 내지 않는다.
- `ktdctl pin rotate --role map|pinvi --revision <40-hex> --reason "..." --confirm` —
  검증 → digest 자동 계산 → atomic write → 이전 파일을
  `runtime-pins.<old-digest>.json`으로 보존(회전 이력 = 롤백 소스) → backend용
  world-readable 사본 갱신(아래 (c)) → journal 기록. root 요구(rotate하는 사람은
  어차피 rebuild도 root로 실행). **[v3]** 직전 pinset에 terminal journal이 있으면
  `supersedes`를 자동 채우고 `blocked_pinsets`에 자동 등재한다.
- **[v3]** `ktdctl pin block <pinset-digest> --reason "..." --confirm` — terminal 판정
  pinset을 `blocked_pinsets`에 수동 등재. `rebuild-pinned`는 차단 pinset(또는 이미
  terminal journal이 있는 pinset)으로의 실행을 **자동 거부**한다 — map이 문서로
  지키던 "재시도 금지" 규약의 기계화이며, "1 pinset = 1회 candidate 실행" 리듬(실측
  운영 패턴)의 강제도 이 메커니즘이 담당한다.
- `ktdctl pin rollback --to <pinset-digest> --confirm` — 보존 파일로 원복. **현재는
  존재하지 않는 기능**(git revert + 재배포가 유일한 롤백). **[v3 제약] 무제한 롤백은
  교차 저장소 운영 규약("terminal pinset 영구 재시도 금지")과 정면 충돌한다** — 롤백
  대상은 `blocked_pinsets`에 없는 pinset(미실행이거나 committed evidence 보유)으로
  제한하고, terminal pinset으로의 rollback은 fail-close한다.

### 설계 (c) — API/UI: 읽기 주체가 둘이므로 로더도 둘이다

- **root 로더**(rebuild 경로): (a)의 registry 파일을 직접 읽는다 — 소비처
  `compose_service.py:6018`.
- **backend 로더**(조회 API): registry는 root 0600이라 backend가 못 읽으므로,
  `pin rotate`/`init`의 atomic 시퀀스가 **secret 없는 world-readable 사본**(내용 전부
  공개 저장소의 commit SHA + 메타)을 backend가 읽을 수 있는 경로에 함께 쓴다. 사본에도
  `pinset_sha256`을 넣어 backend 로더가 재계산 대조하고, 사본 부재·stale 시
  `unknown`으로 fail-close 표시한다. **[v3] 이 publisher 기제는 P2(generation/journal
  노출)와 공유한다 — 진단 6 참조. 1회 구현, 3용도(pin·manifest·journal).**
- `GET /api/v1/runtime-pins` — role별 revision, pinset digest, 회전 메타, **[v3]**
  `history[]`와 `blocked_pinsets[]`, 그리고 **현재 committed generation digest와의
  일치 여부**(P2와 결합: "registry엔 X, 살아 있는 generation은 Y = 회전 후 rebuild
  대기 중"을 그대로 보여준다). **[v3]** Manager 자신의 release provenance(P3 `--self`
  리더)도 같은 payload에 실어, map이 Manager draft PR SHA를 전사하던 관행을 참조로
  대체한다.
- **쓰기(UI rotate)**: registry가 root 소유인 한 API 프로세스는 물리적으로 쓸 수
  없고, 이 경계가 가장 값싼 안전장치다. 따라서 UI 회전은 2-step 승인 모델로 간다 —
  "UI는 회전 **요청**(새 SHA + reason)을 audit row로 기록(`api/admin.py:54-63`의 감사
  패턴)하고, 실제 적용은 SSH에서 `ktdctl pin apply-pending --confirm`". **오너 승인됨
  (Q4)** — 읽기 전용에서 멈추지 않고 이 2-step 모델까지 구현한다.

### 트레이드오프 (정직하게)

"pin 변경 = 코드 변경"이라는 현행 등식이 깨지면서, 그 등식에 무임승차하던 보호 장치
4가지가 함께 사라진다. 항목별로 잃는 것의 실체와 보상 설계를 병기한다.

1. **PR review = pin 승인이던 암묵적 게이트.** 현재는 pin을 바꾸려면 반드시 커밋 →
   PR → 머지를 거치므로 "누군가 diff를 볼 기회"가 구조적으로 강제되고, 브랜치 보호 등
   GitHub 인프라를 공짜로 쓴다. 전환 후에는 `pin rotate` 한 명령으로 타인의 눈을 거치지
   않고 pin이 바뀔 수 있다 — 40-hex 형식 검증은 남지만 "그 커밋이 배포해도 되는
   커밋인가"라는 사람 판단을 두 번 시키던 구조는 사라진다.
   *보상*: 실측상 최근 회전 PR들은 사실상 1인 셀프 머지였다 — 잃는 게이트는 명목상
   존재했지 실질 작동한 적이 거의 없다. **[v3 추가 실측]** 나아가 3.5일간 회전 15건 중
   5건(`2babcd4` 13파일/3,919줄, `da49ec7` 21파일/4,118줄, `9d98db4`, `db5182e`,
   `c7cda1c`)은 **대형 rebuild 기능 커밋 안에 매몰**돼 있었다 — 리뷰 대상이 pin이
   아니라 수천 줄 diff였으므로 이 5건에서 게이트는 명목상으로도 작동 불가능했다.
   registry 전환은 pin 변경을 항상 독립 이벤트로 만들어 이 매몰 자체를 구조적으로
   제거한다. 대신 `--confirm` + root 권한 + `--reason` 필수 + journal·audit 기록으로
   의도 표명을 강제한다.
2. **git 이력 = pin 이력.** 현재는 `git log` 하나로 언제·누가·왜·어떤 SHA로 회전했는지
   전부 나오고, `git revert`·blame·bisect가 pin 이력에 그대로 적용된다. 전환 후 파일은
   git 밖(prod는 배포 트리 밖)에 있으므로 git 도구로 pin 이력을 조회·복원할 수 없다.
   *보상*: rotate가 이전 파일을 `runtime-pins.<old-digest>.json`으로 자동 보존하고
   journal에 기록하며, **[v3]** `history[]`가 회전 체인을 registry 자체에 담는다.
   롤백은 오히려 개선이다 — 현재는 git revert 후 전체 재배포+재기동이 유일한
   롤백인데, 전환 후엔 `pin rollback` 한 명령이다(단 terminal 제한 — 설계 (b)). 보존
   파일은 git처럼 분산 백업되지 않으므로 해당 디렉터리를 백업 대상에 등재해야
   한다(P5와 연결).
3. **테스트가 현재 pin 값을 고정하는 성질.** 현재는 테스트의 SHA 기대값이 코드와
   어긋나면 CI가 즉시 실패해 "한쪽만 바꾸는" 실수를 기계가 잡는다 — 특히
   `MAP_APPLICATION_300_SOURCE_COMMIT` 중복본의 유일한 방어선이 테스트 한 줄이다. 전환
   후 테스트는 파일의 특정 값을 알 수 없으므로 값 고정 검증이 불가능하다.
   *보상*: 이 손실은 목적 달성의 증거다 — 회전마다 기대값 86줄을 고치던 churn 소멸과
   같은 동전의 양면이다. 테스트는 "값 고정"에서 "구조 검증"(스키마·40-hex·canonical
   URL·digest 재계산 로직)으로 재작성하고, 값의 무결성은 로드 시 digest 재계산 대조 +
   `pin verify`가 런타임에 담당한다. 중복 상수 자체가 삭제되므로 "두 값이 어긋나는"
   사고 클래스는 아예 소멸한다. **[v3 한정]** 단, 소멸하는 것은
   `test_pinned_runtime_release.py`의 값 고정 churn이다 —
   `test_pinned_runtime_rebuild.py`의 legacy pinset 재구성 fixture(값이 아니라 시나리오
   성격, `db77fcd`에서 +38/-5)는 P1 이후에도 잔존한다. "테스트 churn 전부 소멸"이
   아니라 "값 고정 churn 소멸"로 정확히 읽어야 한다.
4. **코드 = 배포본 단일성. 4가지 중 구조적으로 가장 실질적인 손실이다.** 현재는 같은
   커밋을 배포하면 어느 호스트든 동일하게 동작하고, "prod에서 뭐가 도는가"의 답이
   "배포된 커밋이 뭔가"와 같으며, 재해 복구도 코드 재배포만으로 pin까지 복원된다. 전환
   후 동작은 코드 + 호스트 로컬 파일의 함수가 된다 — 같은 코드를 배포해도 registry
   파일이 다르면 다르게 동작하고, 재배포만으로 pin이 복원되지 않으며, 파일 부재·손상이라는
   (현재는 불가능한) 새 장애 모드와 신규 호스트 셋업의 `pin init` 필수 단계가 생긴다.
   *보상*: 부재·파싱 실패·digest 불일치 전부 fail-close(조용히 잘못 돌지 않고 명시적
   정지), canonical URL은 코드에 남아 파일 조작으로 임의 저장소를 가리킬 수 없다. 다만
   이는 위험의 관리이지 단일성의 회복이 아니다 — 이 잔여 위험은 수용하고 간다.

**남는 실질 손실 1건**: 코드 상수는 프로세스 실행 중 절대 변하지 않지만, 파일이면
rebuild 도중 교체가 이론상 가능하다 — 단 rebuild는 시작 시 release를 한 번
읽고(`compose_service.py:6018`) 전 과정이 그 digest로 키잉된 journal에 결박되므로,
도중 교체는 다른 digest의 별개 상태 공간으로 갈라질 뿐 진행 중 작업을 오염시키지
않는다(v3 재검증: journal 파일명의 pinset 키잉과
`_assert_pinned_runtime_journal_matches_candidate_input`의 pinset+revision+digest 전수
대조 확인 — 주장 유지). 실해는 없지만 "읽는 시점에 따라 값이 다를 수 있다"는 성질
자체는 남는다.

이 전환은 P6(admin password의 `.env` 쓰기)과 함께 "호스트 로컬 상태" 표면을 넓히는
방향이라는 점을 인지한다 — P6이 경계 완화를 단일 allowlist 키로 한정한 것과 같은
정신으로, P1의 호스트 로컬 상태는 registry 파일 + digest 보존 파일로 한정한다.

## 1.3 P10. 교차 저장소 감사에서 발굴된 추가 설계 (v3 신규)

### P10-1. pinset lifecycle registry — 가장 강한 신규 발견

- **관측(사실)**: 진단 3 전체. 요약 — terminal 재시도 금지 규약이 세 저장소 수기
  문서에만 존재하고, 현행 Manager pin이 이미 terminal 선언된 pinset이며, d9 상수가
  코드에 축적 중이다.
- **설계**: §1.2-(a)의 `blocked_pinsets`/`supersedes` + §1.2-(b)의 `pin block` +
  `rebuild-pinned`의 terminal 자동 거부 + `GET /runtime-pins`의 lifecycle 반환.
- **개선 효과**: 사람이 지키던 불변식이 기계 게이트가 된다. map `docs/tasks.md`의
  차단 목록 줄은 삭제 가능해지고, d9류 코드 상수 신설은 명령 1개로 대체되며,
  rehearsal rebuild 버튼(P8/Q5)의 안전 전제가 마련된다.

### P10-2. 회전 이력 피드 — sibling 수기 부기의 대체물

- **관측(사실)**: 진단 2 전체. 요약 — 11시간 10회 회전을 map·pinvi가 손으로 따라
  적었고, 적는 값 전부가 Manager가 이미 보유한 상태다.
- **설계**: `GET /runtime-pins`의 `history[]` + P2 generation 응답의 명시적 스키마
  고정(아래 P2-4) + Manager 자기 release provenance 동봉(P3 `--self` 리더).
- **개선 효과**: "현재/과거 pinset과 그 결과"의 단일 진실 공급원이 Manager API로
  옮겨진다. sibling 문서는 값 전사 대신 참조 1줄("현재 pinset은 Manager
  `/runtime-pins` 참조")로 대체 가능하다. 3일 실측 기준 map 부기 19커밋·pinvi
  docs-only 커밋의 상당수가 소멸 대상이다.

### P10-3. typed 진단 소비와 문자열 결합 해소

- **관측(사실)**: 진단 4 전반부. 추가 관측 — PinVi bootstrap 스크립트는 DB endpoint를
  `app-postgres:5432|127.0.0.1:12800` 리터럴 allowlist로 고정하는데(`93296aee`),
  Manager는 `PINVI_DB_PORT`를 변수로 다룬다(`database_runtime.py:57`,
  `c6c_deployment.py:67,530,542,550`). Manager UI의 설정 편집 모달이 이 키를 편집
  가능하게 노출하면 bootstrap이 exit 2로 fail-close한다.
- **설계**: (i) `compose_service.py:478-497`의 stderr 문자열 파싱을
  `pinvi.role-topology-diagnostic.v1` typed JSON 소비로 이관하고 reason enum 10종을
  P2 요약 배지의 입력으로 쓴다. (ii) sealed verifier 호출을 rebuild journal의 1급
  phase로 편입한다(호출 순서 계약의 성문화 — pinvi 쪽 짝 태스크 KUM-PV-3).
  (iii) `PINVI_DB_PORT` 등 sibling이 리터럴로 결박한 env 키를 설정 편집 모달에서
  **read-only 표기**한다(P7-H와 같은 계열).
- **개선 효과**: PinVi의 문구 수정이 Manager 분류를 깨뜨리는 결합이 사라지고, 실패
  reason이 enum 그대로 화면에 뜨며, UI발 계약 위반(포트 변경) 경로가 봉쇄된다.

#### P10-3 정정 (2026-08-28, PinVi 소스 실측)

설계 (i)의 전제가 틀렸다. PinVi `infra/postgres/bootstrap-pinvi-runtime-role.sh`를 직접
읽어 확인한 사실:

- `pinvi.role-topology-diagnostic.v1`은 **`PINVI_ROLE_TOPOLOGY_VERIFY_ONLY=1`일 때만**
  stdout 한 줄로 나오고 언제나 exit 0이다. Manager가 문자열로 분류하는 9문구는 **일반
  부트스트랩 실행**의 stderr이며, 그 경로에는 typed envelope이 아예 없다(exit 2=입력
  검증, 1=endpoint 미준비, 3=topology/소유권 거부).
- 따라서 **"stderr 파싱을 typed JSON 소비로 이관"은 Manager만으로는 불가능하다.** 두
  진단은 같은 실패의 다른 표현이 아니라 **서로 다른 실행**의 출력이다. 이관은 PinVi가
  일반 실행에서도 envelope을 내보내야 성립하며, 그 작업이 KUM-PV-3다.
- 고정 revision `97d2f924…`의 스크립트에는 `PINVI_ROLE_CATALOG_RESET_ONLY` 처리가
  **없다**. Manager는 그 모드를 `-e`로 주입하는데, 변수는 조용히 무시되고 일반
  부트스트랩이 실행된 뒤 Manager가 `{}` 결과를 읽고 fail-close한다. 데이터는 안전하지만
  **의도하지 않은 부트스트랩이 한 번 돌고 pinset 하나가 소모된다.** → readiness 검사
  `pinvi_role_bootstrap_modes`로 실행 전에 잡는다(KUM-M6에서 구현).
- Manager의 `PINVI_ROLE_CATALOG_RESET_DIAGNOSTICS`에는 스크립트가 결코 쓰지 않는
  `target_not_isolated`가 있다(그 실패 경로는 결과 파일을 쓰지 않고 exit 3). 받아들이는
  집합이 넓기만 한 것이라 fail-close 결함은 아니므로 **좁히지 않고 기록만 한다** —
  좁히면 다른 revision에서 거짓 거부가 될 수 있다.

**그래서 KUM-M6의 실제 범위**: (iii) 계약 결박 env의 read-only화와 위 readiness 검사는
Manager 단독으로 완료. (i)의 typed 이관과 (ii)의 verifier phase 편입은 KUM-PV-3 선행.

### P10-4. preflight readiness 노출 — "실행 전에 실패를 아는" 패널

- **관측(사실)**: 진단 5의 blocker 3건.
- **설계**: 배포 정합성 패널에 read-only 행 4종 — (i) 필수 base image N중 M
  present(`docker image inspect`, Map API·Dagster Dockerfile의 digest는 기존 판독
  로직 `compose_service.py:3194-3245` 재사용), (ii) 오프라인 wheelhouse 요구 wheel
  N중 M(빌드 의존 포함), (iii) Compose 입력 single-file 여부(legacy override 존재
  검사 — P8b의 `compose-boundary` read-only 배지), (iv) sibling 필수 파일
  존재/모드(`../pinvi/infra/postgres/bootstrap-pinvi-runtime-role.sh`
  (`docker-compose.yml:325`), `../kor-travel-map/scripts/database-credential-preflight.sh`
  (`:815`)).
- **개선 효과**: pinset 1개·반나절을 태우고 알던 결손을 화면에서 미리 안다. map이
  명시 요구한 "Manager preflight의 base provision·재관측"의 관측 절반을 충족한다
  (provision 자체는 rebuild 내부 작업 — 이미 M05 PR 계열에서 진행).

### P10-5. 계약 소유 경계의 명문화 — Manager가 하는 것과 하지 않는 것

- **관측(사실)**: 이 3일 창 최대의 계약 변경인 Map admin provenance identity 결박
  (opaque `feature_id` + `feature_uuid` 이중 축 fail-close 대조, map
  `8a170735`/`645d1c0b`)의 당사자는 **Map(생산)과 PinVi(attestation)뿐**이다 —
  Manager는 그 계약이 담긴 이미지를 만드는 배달 채널이지 당사자가 아니다. 반면 map이
  Manager를 소비자로 명시한 계약은 **frozen Compose receipt와 C6c cancel-probe
  fixture** 두 가지다(map `docs/integration-map.md`). 또:
  - **manifest/journal JSON의 키 집합·버전은 공개 계약이다**: map
    `scripts/lib/c7_prod_attestation.py`(1,133줄)가 Manager 내부 자료구조
    (`PinnedRuntimeGeneration.to_payload()` 등)의 키 집합을 **exact-dict로 결박**한다
    — 키 하나만 추가돼도 map의 production attestation이 fail-close한다.
  - **PinVi M05 production activation은 Manager compose 계약 확장이 선행 조건이다**:
    activation receipt·attestation·lease 등 env 9종 + bind mount 8종을 PinVi 쪽
    compose가 요구하는데 Manager compose에는 전무하다(현재는
    `RECONCILIATION_ENABLED=false`라 통과). PinVi의
    `activation_generation`(해시체인 5파일)은 Manager pinned generation과 별개
    카운터이며 두 값의 정합을 보는 화면이 없다. `PINVI_API/WEB/DAGSTER_IMAGE_DIGEST`
    주입 주체도 미정인데, rebuild가 이미 그 digest를 아는 Manager가 자연스러운
    소유자다.
  - map `docs/backup-restore.md`는 **backup artifact를 pinned rebuild의 선행 gate·
    rollback 근거·복원점으로 인정하지 않는다** — P5(backup)와 Q6(restore)의 적용
    범위는 "전용 DB의 데이터 보호"이지 "pinned generation의 롤백 수단"이 아니라는
    경계가 명문화됐다.
- **설계**: 이 문서와 후속 구현 문서에 다음을 명문화한다 — (i) Manager가 소비·검증
  하는 교차 저장소 계약은 frozen Compose receipt·C6c cancel-probe fixture이며 Map
  provenance identity는 Manager 표면이 아니다. (ii) manifest/journal 문서는 그대로
  통과시키고 `summary` 등 가공은 **API 응답 envelope에만** 넣는다(문서 스키마 변경은
  map 동시 PR 없이 불가). (iii) M05 compose 확장·digest 주입·이중 generation 표시는
  범위 밖·선행 조건 인지로 기록한다(3부 KUM-M17에 등재만).
- **개선 효과**: "Manager가 어디까지 책임지는가"가 문서로 고정돼, 구현자가 문서
  스키마에 요약 키를 넣는 사고(즉시 map attestation 파손)나 Manager 밖 계약을
  Manager에 구현하는 범위 침범을 방지한다.

#### P10-5 보정 — M05 isolated direct Compose admission

전문 보안 적대 리뷰가 PinVi의 기존 isolated Compose gate가 root UID와 호출자가 설정할 수 있는
environment marker만 신뢰한다는 P1을 발견했다. 이는 `ktdctl`만 pinning·pair 결박·one-shot을
소유한다는 계약과 모순된다. Manager root driver는 이제 private runtime directory에 `0600` admission을
만들고, exact transaction project·current pinset·Manager·Map·PinVi source revision을 함께 결박한다.
PinVi는 그 파일을 no-follow로 읽어 exact schema와 결박을 확인할 때만 isolated mutation을 허용하며,
legacy environment marker는 거부한다. Manager는 이 tuple을 clean child environment로 전달하고 PinVi는
root EUID에서 `/usr/bin/python3 -I`로 검증하므로 PATH shim·임의 environment는 admission을 대체하지
못한다. admission은 private one-shot 입력일 뿐 API·UI 공개 표면이나
manifest/journal schema를 바꾸지 않는다.

---

# 2부 — UI 이관과 운영 기능 격차 (v2 본문 + v3 정정)

## 현재 상태 인벤토리 (HEAD `b964958` 재검증 — 변경 0건, [v3] `f0edac7` 인용 재검증)

v1 작성 이후 M05 계열 PR(#238–#242)이 merge됐지만 `git log b467c02..HEAD -- cli.py
api/`는 빈 결과다 — **CLI/API 표면은 v1 표와 동일**하다(M05 PR들은 전부 rebuild 내부
변경: pin 회전, builder 실패 분류, base-image preflight). `ktdctl`은
`build_parser()`(`backend/src/kor_travel_docker_manager/cli.py:260-418`)가 등록하는
**13개 leaf 명령**(기본 6 + `pinvi-pair rebuild-pinned` + `compose-boundary` 3 +
`db-backup` 3)으로 구성된다.

| ktdctl 명령 | 하는 일 | API 노출 | UI 노출 |
|---|---|---|---|
| `targets [--json]` | target 목록·의존 순서·해석된 service 목록 | `GET /api/v1/targets` | 간접적 — `ContainerDetailModal`의 영향 범위 계산에만 사용 |
| `status [target] [--json]` | `docker compose ps` 결과 | `GET /api/v1/containers`, `WS /api/v1/ws/status`가 사실상 동등 | 컨테이너 테이블 |
| `ensure <target> [--build] [--recreate] [--stream] [--json]` | target의 `depends_on` 폐포를 위상정렬 순서로 `docker compose up -d`(+`init_steps`) 실행 | `POST /api/v1/targets/{target}/ensure` — production에서 두 지점 하드스톱(`compose_service.py:4472`, `:4483`, T-044) | `ContainerDetailModal`의 `IS_DEV` 전용 버튼(서버가 차단, 409) |
| `logs <name> [-f] [--tail N] [--json]` | compose/service 로그 | `GET /containers/{id}/logs`, `WS /ws/logs/{id}` | 실시간 로그 모달 |
| `action <container> start\|stop\|restart` | 컨테이너 제어 | `POST /containers/{id}/action` | Start/Stop/Restart 버튼 |
| `inspect <container> [--json]` | inspect 요약 | `GET /containers/{id}/inspect` | `ContainerDetailModal` 5개 탭 |
| `pinvi-pair rebuild-pinned --confirm [--json]` | Map·PinVi 7개 runtime destructive 재구축([v3 명확화] Manager가 **build**하는 image는 Map UI+PinVi 3종의 4개뿐 — Map API·Dagster image는 sealed paired candidate가 공급하며 generation 결박 대상은 7 runtime 전부), `rehearsal/rebuildable` 조합 아니면 거부 | 없음 | 없음 |
| `compose-boundary stage/retire/activate --confirm` | legacy Compose override 이관(1회성) | 없음 | 없음 |
| `db-backup create/list/gc` | pg_dump 백업 생성/조회/정리 | `list`만 `GET /api/v1/backups?role=` | `BackupHistoryPanel`(읽기 전용) |

그 외 API 전체 표면: `api/admin.py`(login-audit-events, public-api-keys GET/POST/DELETE),
`api/auth.py`(login/logout/me), `main.py:202`의 `GET /health`. **`GET /health`는 어느
UI에도 표시되지 않는다** — "관리도구 자신이 정상인가"는 비전문 운영자의 첫 질문인데
답할 화면이 없다(→ P7-D).

`ContainerConfigUpdate`(`api/routes.py:37-46`)의 `volumes`는 편집 필드가 아니라
echo-only 계약이다(값 변경 시 `ComposeCandidateContractError`로 거부). `image` 필드는
스키마에 없다.

**컨테이너 단위 조작과 target 재생성의 실제 관계**(v1 정정 유지):
`control_container`/`update_container_config`/`reset_container_config`는 C6c host-wide
lock **안에서** 실행되고, `ensure_target`만 production 전용 하드스톱을 추가로 갖는다.
config/reset이 production에서 허용되는 실질 이유는 바이트 동일 이미지 재사용·볼륨 그래프
불변·실패 시 restore 트랜잭션이다 — 코드가 바뀌는 작업(이미지 재빌드)에는 이 성질이
적용되지 않는다.

**CLI/API 계층 중복 없음(구조 건강)**: `cli.py`(431줄)와 `routes.py`(261줄)는 로직을
중복하지 않고 동일 서비스 함수를 공유한다(`ensure_target`, `control_container`,
`list_standalone_backups`, `list_targets` 모두 양쪽에서 같은 함수 호출). 중복은 표현
계층(exit code vs HTTP detail)뿐이다. 의미: **"ktdctl→UI 이관"은 대부분 신규 route +
기존 서비스 함수 호출로 끝나며, CLI 리팩토링이 선행 조건이 아니다.**

## P0. UI 화면 지도 — "무엇이 Web UI가 되는가"의 한눈 답

| 화면/패널 | 상태 | 요구하는 신규 백엔드 |
|---|---|---|
| 컨테이너 테이블(상태·메트릭·로그·제어) | 현존 → **개선**(P7: 라벨 한국어화·그룹 뷰·미리보기) | 없음 |
| 설정 편집 모달(ports/env/networks) | 현존 → **개선**(P7-H 볼륨 읽기 전용화, [v3] 계약 고정 키 read-only — P10-3) | 없음 |
| 백업 이력 패널 | 현존 → **확장**(P5: freshness 배지·생성 버튼·복원 안내) | 생성 버튼만 job_runner 필요 |
| 인증 설정 패널(API 키·감사) | 현존 → **확장**(P6: 비밀번호 변경 폼) | `POST /admin/password` |
| Manager 자기 상태 카드 | **신규**(P7-D) | 없음(`GET /health` 기존, [v3] installer provenance 리더 ~10줄) |
| 배포 정합성 패널(pin·generation·drift·[v3] readiness) | **신규**(P1-c, P2, P3, P10-4) | 읽기 전용 route + root-side publisher(KUM-M3) |
| 디스크 사용량 카드 | **신규**(P8b) | `docker system df` wrapper |
| CLI 명령 카드(전 CLI-전용 작업 공통) | **신규**(P7-E) | 없음 |

실행(mutation)이 UI로 들어오는 것은 백업 생성(P5)·비밀번호 변경(P6)·2-step pin 회전
요청(P1-c)·rehearsal 한정 rebuild 버튼(P8)이고 — 넷 다 오너 승인됨(문서 말미 결정
사항 참조) — 나머지는 전부 읽기 전용 관측이다. P9-3단계(프론트 구조 추출)의 착수
트리거 "신규 패널 2개 이상"은 이 표의 신규 4행 기준으로 센다.

## P2. 이미 존재하는 상태의 노출 — release·manifest·journal

- **왜 문제인가**: pin 회전이 지배적 chore인데 **현재 pin이 뭔지 보는 방법이 소스코드
  열람뿐이고**(SSH도 아닌 git checkout 필요), 지난 재구축이 어디까지 갔고 왜 멈췄는지는
  root 셸에서 journal 파일을 열어야 안다. 실행자와 관측자가 다른 사람일 수 있는데
  관측자용 경로가 없다.
- **[v3 실측 보강]**: 최근 3일 n150에서 manifest(성공 세대)는 **한 번도 안 바뀌었고
  journal만 12회 바뀌었다** — "성공한 세대보다 실패/진행 journal이 더 실용적"이라는
  v2 판단이 실측으로 확인됐다.

1. **`GET /api/v1/pinned-runtime/release`** — **Q1 승인으로 별도 endpoint를 만들지
   않는다**: P1-(c)의 `GET /runtime-pins`가 이 역할을 흡수한다.
2. **manifest + rebuild journal 통합 노출 — `GET /api/v1/pinned-runtime/generation`**
   (응답 키: `manifest` / `journal` / `summary`). `read_manifest`
   (`pinned_runtime_generation.py:2810`)에 더해 `read_rebuild_journal`(`:2818`)도
   노출한다.
   **[v3 정정 — 전제 재설계]**: v2의 "mode 게이트 없는 진입점으로 우회" 서술은
   불충분하다. 진단 6대로 **비-root backend는 state root(디렉터리 0700·파일 0600 +
   리더의 uid/mode fail-close 검증)를 읽을 수 없다.** 이 route는 P1-(c)의 root-side
   world-readable publisher를 전제하며, **P1보다 싸지 않고 P1의 publisher에
   의존한다**(KUM-M3). rehearsal 스코프 정정(v2)은 유지 — 상태 디렉터리가 없는
   호스트에서는 "세대 기록 없음"을 정직하게 표시한다.
3. **평이한 언어 요약 계층은 설계 요건이다.** 실제 phase 값은
   `application_bootstrap_intent_durable` 같은 내부 상태기계 이름이고 image_ids는
   sha256 다이제스트다 — 그대로 노출하면 비전문가에게 무의미하다. 요건: **"재구축
   진행 중 (n/전체 단계) / 정상 커밋됨 / 운영자 개입 필요"** 수준의 한국어 요약
   배지(`summary` 키) + 원시 값은 접힌 상세로. **[v3] 요약의 최우선 입력은 phase가
   아니라 terminal 분류다** — map/pinvi가 실제로 소비·전사한 것은 terminal enum
   (`role_catalog_reset_failed/target_not_isolated` 등)이었다. 응답에
   `terminal: {class, subclass, pinset_sha256}`를 1급 필드로 노출한다.
4. **[v3 신규] 노출 대상 확장 — state root 아래 신규 산출물.** 08-25~27 rebuild 내부
   변경이 추가한 산출물이 현재 화면·API 어디에도 없다:
   - **builder receipt 2종**(`api-candidate-build.json`/`paired-candidate-build.json`,
     경로 헬퍼 `compose_service.py:2595-2634`) — 진단 4대로 build 실패의 **유일한
     증거**다. `builder_failure_class`(`api_receipt_missing`/`paired_receipt_missing`/
     `api_candidate_rejected`/`paired_builder_rejected`/`unclassified`)를 요약 배지로.
   - **fence/permit 디렉터리 5종**(fresh-root-fence, fresh-finalize-fence,
     application-final-permit, dagster-storage-permit, results) — "어느 fence까지
     발급됐나"는 28단계 phase보다 사람에게 읽기 쉽다.
   - **journal 신규 필드 중 행동 결정 필드 2개**: `pinvi_role_lifecycle_block`
     (있으면 해당 pinset 영구 차단 = 운영자 개입 필수, `pinned_runtime_generation.py:1419`),
     `pinvi_role_credential_environment_rebind`(rebind 1회 소진 여부, `:1387-1415`).
   - **manifest/journal 버전 자체**: v5/v7→v6/v8 이동이 이 3일 창 안에서 일어났고
     (`73d8519`, phase 13개→28개) 앞으로도 오를 수 있다. 응답에
     `manifest_version`/`journal_version`을 싣고 미지원 버전은 `unsupported`로
     fail-close 표시한다.
5. **[v3 경고 — 문서 스키마는 교차 저장소 공개 계약]**: P10-5대로 manifest/journal
   JSON의 키 집합·버전은 map의 attestation이 exact-dict로 결박한다. 노출 API는 문서를
   그대로 통과시키고 가공(`summary` 등)은 **응답 envelope에만** 넣는다. 문서 스키마
   변경은 map 동시 PR 없이 불가.

## P3. source 상태·정합성 — `source-status`

v1의 통합 설계(단일 `ktdctl source-status` + `GET /api/v1/source-status`)를 유지한다.
세부 사실관계(실행 이미지에 OCI revision label이 없어 non-raising wrapper와 `unknown`
경로가 필요, redaction 재사용, 절대 경로 비노출, TTL 캐시+수동 새로고침+single-flight)도
전부 유지. 변경·보강점:

- **목적 재정의(v2)**: "감사"가 아니라 **"지금 뭐가 돌고 있나"를 사람 말로 보여주기**다.
  MATCH/DRIFT/unknown 영어 토큰과 raw SHA 비교는 비전문가에게 무의미하다 —
  **"최신 상태입니다 / 업데이트가 필요합니다(재구축 요청) / 확인할 수 없습니다"** +
  다음 행동 안내로 번역하고, SHA는 접힌 상세로 둔다.
- Geo/Concierge sibling checkout의 `git rev-parse` + clean/dirty는 v1 그대로.
  **[v3 정정] Manager 자신의 `--self`는 "provenance 기록 확장"이 아니라 "리더
  추가"다** — trusted installer가 이미 `.ktdm-source-revision`과
  `.ktdm-release-manifest.json`(`manager_source_revision` 포함)을 root:root `0644`로
  앱 루트에 쓰고 있다(`scripts/install-ktdm-trusted-release:932-934,1140-1145`).
  backend가 그대로 읽을 수 있으므로 필요한 것은 **~10줄의 리더**뿐이고, P7-D 카드에
  즉시 편입 가능하다. 기록이 없는 legacy rsync 배포본에서는 `unknown`으로 표시한다.
- **[v3 신규 행 후보 — 계약 drift의 조기 관측]**: (i) **Map 이미지 실행 경계** —
  기대 계약이 이 3일 창에서 정반대로 뒤집혔다(`Cmd` 지정 →
  `Entrypoint == ["/app/docker/api-entrypoint.sh"]` + `Cmd == None`,
  `c6c_deployment.py:37-38`). Map이 Dockerfile을 바꾸면 rebuild가 런타임 검증에서야
  늦게 실패하므로 "기대/실제" 행으로 조기 관측한다. (ii) **Map Dockerfile 구조
  계약**(`FROM` 정확 2줄·양 stage 동일 digest, `compose_service.py:3194-3245`) 충족
  여부 — Map 저장소만 봐서는 이 제약을 알 수 없다. (iii) **환경 완결성 카드** —
  compose 필수(`:?`) 변수 대비 `.env` 미설정·폐기 변수 잔존 점검. 실측 결함:
  `.env.example`에 운영자 공급 6종(`PINVI_APP_DB_USER` 등 PinVi role credential)이
  누락돼 있고 폐기된 `PINVI_DOCKER_DATABASE_URL`은 잔존한다(`fcd1720`/`153df73`발
  drift — 별도 즉시 수정 태스크 KUM-M15).
- 일반형 GitHub pull+build(Geo/Concierge/Manager 자신)는 **영구 범위 밖 결정** 유지.
  근거(v1에서 이월): Manager는 이 세 저장소의 자격증명을 갖지 않으며, "fetch해서
  신뢰"가 아니라 "검증하고 거부"가 이 프로젝트의 일관된 원칙이다.

## P4. git 이력 조회 — 대체안: GitHub compare 링크 (v1 설계 전면 교체)

v1은 `diff-pinned`(별도 scratch mirror + fetch)를 설계했다. v2 재검토 결론:
**비전문가는 commit diff를 읽지 않으며, diff를 호스트에서 계산할 필요가 애초에 없다.**
pinned SHA(P1/P2로 노출)와 실행 중 revision(P3)이 있으면
`github.com/<repo>/compare/<old>...<new>` **외부 링크만 렌더링**하면 된다 — fetch 0회,
신규 백엔드 0줄, 브라우저에서 GitHub이 diff를 보여준다.

v1의 scratch-mirror 설계는 "오프라인/air-gap 환경에서 diff가 필요해지는 경우"의
예비안으로만 남긴다(현재 니즈 없음). 예비안 요약(재론 시 상세 재설계 전제): 기존
pinned mirror는 per-pinset·단일 revision fetch·root 0700·rehearsal 전용이라 재사용
불가 → Manager 소유 별도 scratch bare에 양쪽 revision을 fetch하되 CLI/SSH 트리거
전용(HTTP 핸들러 금지), 기존 `_run_root_git` 하드닝 재사용 + `fetch.fsckObjects`
추가, fetch마다 journal 기록. GitHub egress 자체는 `rebuild-pinned`가 이미 수행 중이라
쟁점은 "처음 허용"이 아니라 "트리거 표면 확대"다.

## P5. 백업 (v1 대폭 개정 — usability-first 재배치)

### create — "백업 버튼"은 비전문가 관리도구의 핵심 기능 (승격, 승인됨 Q2)

- **왜 문제인가**: 주간·비상시 반복 작업 중 UI화 효용이 가장 큰 항목인데 CLI 전용이다.
  비전문 관리자가 "지금 백업 하나 떠 둬야겠다"는 가장 자연스러운 욕구를 SSH 없이
  충족할 수 없다.
- **구현 명세** (v1이 "선행 과제"로 부른 것들은 하지 않을 이유가 아니라 명세다):
  - **비동기 job 인프라(신규)**: 현재 저장소에 job 추상화가 전무하다 — `routes.py` 전
    핸들러가 sync `def`이고 create는 기본 timeout 14,400초(geo 실측 879초~22분)다.
    최소 설계: `services/job_runner.py` — job id → `{kind, state, started_at,
    result|error}`, `asyncio.create_task(asyncio.to_thread(fn))` 실행(기존
    `metrics_collector.py:19-23`·`main.py:129-148` lifespan 패턴 조합), kind별
    single-flight는 `_role_lock`(`standalone_backup.py:315`) 재사용. API:
    `POST /api/v1/backups/{role}` → 202 + job id,
    `GET /api/v1/backups/{role}/jobs/{id}` 폴링(경로에 role을 포함해 `/backups/{role}`과의
    세그먼트 충돌 회피), 기존 status WS에 완료 이벤트 편승. 핵심 효용: **"버튼 누르고
    브라우저 닫아도 되는 백업"**.
  - **UID/ACL**: dump 소유권은 **shared group + setgid 디렉터리 방식으로 확정(Q2)** —
    backend와 cron 프로세스 구성을 바꾸지 않고 백업 디렉터리를 공유 그룹 소유로 두어
    양쪽 모두 읽기·삭제할 수 있게 한다.
  - UI: role 선택 + **"geo는 수 시간이 걸릴 수 있습니다"** 예고 + 경과 표시(role별 실측
    소요가 이미 문서에 있어 예상 시간 하드코딩 가능) + 실행 중 버튼 비활성.
    가드레일은 일반 확인 다이얼로그면 충분하다 — create는 파괴적이지 않다.

### freshness 배지 — 백엔드 0줄

- **왜 문제인가**: `scripts/run-standalone-backup.sh:7-11`이 cron 주기를 확정해 뒀는데
  `BackupHistoryPanel`은 생성 시각만 표시한다 — "마지막 백업이 26시간 전이면 cron이
  죽은 것"을 운영자가 암산해야 한다.
- **설계·효과**: 기존 `GET /api/v1/backups` 응답만으로 프론트 단독 구현. role별 기대
  주기 상수 + "마지막 백업 N시간 전" + 임계 초과 시 경고색. "cron 죽음"이라는 조용한
  장애가 화면에서 즉시 보인다. "crontab 설치 여부" 감지 자체는 신규 코드 + UID 문제가
  있어 보류 — freshness 배지만으로 실질 효용의 대부분을 얻는다.

### gc — CLI 전용 유지 (근거는 usability 기반)

usability 관점에서도 승격하지 않는다: gc는 cron 성격의 chore이지 화면에서 즉흥적으로
누를 일이 아니고, restore 부재 상태에서 유일 사본 삭제 버튼은 "실수 복구 불가 = 최악의
UX"다. UI 대체: `BackupHistoryPanel`에 보존 개수·최고령 표시 + 복사 가능한
`ktdctl db-backup gc` 명령 카드(→ P7-E).

**gc 결함 수선(한 곳에서 확정)**: `gc_standalone_backups`(`standalone_backup.py:
277-299`)의 `_role_lock` 부재(4시간 create와 경합 가능)와 orphan `.dump`
미수거·manifest 내용 기반 오삭제 가능성은 **UI 노출 여부와 무관한 독립 버그 수정**이다
(lock은 3줄). job_runner(KUM-M9)에 편승해 함께 고치면 되고, job_runner를 하지 않아도
단독 착수 가능하다. 이 문서에서 이 수선을 언급하는 다른 절은 모두 이 문단을 가리킨다.

### restore — 정정: "CLI 전용"이 아니라 "부재" (로드맵 편입 확정 Q6)

- **왜 문제인가**: restore는 CLI에도 없다(`routes.py:100-101` "Restore isn't
  implemented anywhere yet"). create 버튼을 만들수록 restore 부재가 더 위험해진다 —
  버튼이 안전감의 착시를 만든다("백업이 있으니 복구 가능하겠지"가 현재는 거짓).
- **설계**: 최소 조치(프론트 전용): `BackupHistoryPanel`에 "복원은 아직 미구현 —
  절차는 runbook 참조" 명시 + 백업 행에 향후 복원 명령 원형 복사 버튼. 구현 순서는
  CLI 먼저. 규모 추정: `standalone_backup.py`(573줄)와 대칭인 restore 서비스 + CLI
  서브커맨드로 **대략 300-500줄 + role별 정지/기동 절차 설계**.
- **[v3] 적용 경계 명시**: map `docs/backup-restore.md`는 backup을 pinned rebuild의
  롤백 수단으로 인정하지 않는다(P10-5). restore의 범위는 **전용 DB 데이터의 복원**
  이며 pinned generation의 롤백은 별개 경로(`pin rollback` + rebuild)다 — 이 경계를
  restore runbook에 명시한다.

## P6. 설정/secret 변경

### 관리자 비밀번호 변경 폼 — UI로 승격 (승인됨 Q3)

- **왜 문제인가**: "관리자 비밀번호 변경"은 모든 웹앱의 표준 UX이고 없는 쪽이
  이상하다. 현재는 SSH + 해시 생성 + `.env` 수동 편집 + 재기동이라는, 비전문가에게
  사실상 불가능한 절차다.
- **가능 근거**: `KTDM_ADMIN_PASSWORD_HASH`는 `verify_admin_password`가 **호출 시마다
  `os.environ`에서 읽으므로**(`auth_service.py:72` — 코드베이스 유일 읽기 지점), API
  핸들러가 자기 프로세스의 `os.environ`을 갱신하면 재기동 없이 즉시 적용된다. 세션
  검증은 password hash를 건드리지 않으므로 진행 중 세션도 죽지 않는다.
- **설계**: `POST /api/v1/admin/password` — 현재 비밀번호 재검증(그 자체가 typed
  confirmation) → 새 hash 생성(`hash_password_for_env`, `auth_service.py:52`) → `.env`
  atomic 갱신 + `os.environ` 동시 갱신 → audit row(`admin.py:54-63` 감사 패턴).
- **P1 경계 논거와의 관계(정직하게)**: P1-(c)는 "API 프로세스가 registry 파일을
  물리적으로 못 쓴다"를 안전장치로 드는데, 이 항목은 같은 API 프로세스에 **`.env`
  쓰기 능력**을 부여한다 — `.env`는 machine secret 전부가 든 파일이므로 실질적 경계
  완화다. 한정 조건: (i) 쓰기는 **`KTDM_ADMIN_PASSWORD_HASH` 단일 키 allowlist**로
  제한하고 임의 key=value 쓰기는 구현하지 않는다(경로·파일 바이트 사전조건 검증은
  `pinvi_database_role_credentials.py`의 **검증 로직만** 차용 — 함수 재사용 금지).
  (ii) prod `.env`의 소유·퍼미션이 backend 쓰기를 허용하는 구성인지가 전제조건이며
  구현 시 확인한다. (iii) 이 완화로도 pin registry는 여전히 root 전용이다.
- **[v3 신규 위험 — rebuild resume과의 상호작용]**: pinned rebuild의 재개(admission)는
  `.env` 파일 **바이트 digest**를 journal의 `environment_sha256`과 대조하고, 불일치는
  좁은 rebind 예외(`phase == map_runtime_ready` + rebind 미소진)를 빼면 거부다
  (`compose_service.py:5506-5538`, `:5573-5594`). 즉 **미종결 rebuild journal이 있는
  동안 UI에서 비밀번호를 바꾸면 그 rebuild의 재개가 영구 차단될 수 있다.** 구현 요건:
  `POST /admin/password`는 미종결 resume journal 존재 시 (i) 쓰기를 거부하거나
  (ii) "진행 중 재구축이 무효화됩니다" 명시 경고 + typed confirmation을 요구한다.
  이 축은 P1 registry와 무관하게 **현행 `.env` 수동 편집에도 이미 존재하는 위험**
  이므로 runbook에도 병기한다.

### 나머지 secret 회전 — CLI 전용 유지

usability 렌즈로 재도출해도 결론은 같다: session secret 회전은 "적용 = 전 관리자 세션
즉시 무효화 + SSH 재기동"이라 **UI 버튼을 눌러도 그 자리에서 완결되지 않는 작업**이고,
machine secret(PinVi role password, API token)은 비전문가가 화면에서 회전할 동기
자체가 없다(값을 쓰는 곳이 전부 기계다). v1 설계 유지: `ktdctl secret rotate` CLI 전용,
human/machine 클래스 구분(machine은 절대 비출력, human은 TTY 1회 출력), 회전과 재기동
분리, T-045 프로토타입(`ktdctl map-ui-auth rotate`) 기반. UI는 "재기동 대기 중"
다이제스트 비교 상태 표시 + **해소 명령(SSH 재기동 명령 원형) 복사 버튼**(P7-E)까지.

## P7. 비전문가 직관성 개선 — 프론트 전용 quick wins

**백엔드 0줄로 가능한** 개선 목록. 현 대시보드는 "관측은 친절, 조작은 전무"이며 그
관측조차 개발자 어휘다. 각 항목에 문제/효과를 병기한다.

- **A. 오류 humanize.** *문제*: `apiJson`이 실패 response body 원문을 그대로
  `ApiError.message`에 넣고(`frontend/src/lib/api.ts:194-195`) `DashboardClient`가
  `alert()`로 띄운다(`:563`, `:566`, `:580`, `:583`) — 비전문가가 raw JSON
  `{"detail":{"code":"COMPOSE_CANDIDATE_..."}}`를 브라우저 alert로 보게 되고, 오류가
  "내가 뭘 잘못했나"가 아니라 "시스템이 고장났나"로 읽힌다. *설계·효과*: detail
  code→한국어 설명 매핑 + "자세히" 접기로 원문 보존 + toast/panel 교체.
  `ContainerDetailModal.tsx:203-208`이 이미 모범 패턴(raw detail 숨기고 평이한 문구)
  이므로 전역화하는 것뿐이다.
- **B. 라벨 한국어화.** *문제*: 상태 칸이 raw 영어(`running`/`not_created`,
  `DashboardClient.tsx:948-950`)인데 같은 화면 KPI는 한국어("실행 중")라 어휘가
  이중이다. role 칸도 내부 식별자(`map-api`, `metrics-exporter`) 그대로다. *설계·효과*:
  `getContainerPresentation`(`:157-193`)에 이미 한국어 표시명이 있으므로 재사용해
  화면 어휘를 단일화한다. 백업 role 필터(`geo_dagster` 등)와 열 이름(`alembic`,
  `SHA-256`)도 설명 라벨로.
- **C. target 그룹 뷰.** *문제*: 21개 컨테이너가 평면 테이블 하나라 "PinVi 쪽이 지금
  정상인가"에 답하려면 행을 눈으로 골라 세야 한다. *설계·효과*: `GET /targets`가 의존
  구조를 이미 반환하므로 앱 단위(지오코더/컨시어지/지도/PinVi/공용 인프라) 섹션 접기 +
  섹션 헤더에 "모두 정상 / 1개 중지됨" 요약이 프론트 전용으로 가능하다.
- **D. Manager 자기 상태 카드.** *문제*: "관리도구 자신이 정상인가"라는 첫 질문에
  답할 화면이 없다(`GET /health`는 UI 미표시). *설계·효과*: health 카드 + **[v3]**
  installer provenance 리더(P3 `--self`)로 "지금 설치된 Manager 버전"도 함께 표시.
- **E. 복사 가능한 CLI 명령 카드.** *문제*: CLI 전용으로 남는 작업은 비전문가에게
  "SSH에서 뭘 치라는 건지"부터가 장벽이다. *설계·효과*: 해당 작업마다 "이 명령을
  SSH에서 실행" 원형 복사 카드를 둔다 — **CLI-전용 정책과 usability를 동시에 만족하는
  최저비용 수단**이며 이 문서의 CLI-전용 결정 전부에 공통 적용한다. 대상: `db-backup
  gc`, (향후) restore, secret rotate, `rebuild-pinned`, `pin rotate`, Manager 재기동,
  그리고 **sibling 앱 이미지 갱신**(Geo/Concierge 등의 `ensure --build` — P8 참고).
- **F. mutation 미리보기 전역화.** *문제*: stop/restart가 확인 없이 즉시 실행되고
  (`:548-550`) reset은 문구뿐인 confirm(`:1720`)이라, 의존 서비스가 딸려 멈추는 것을
  누르기 전엔 알 수 없다. *설계·효과*: 이미 두 곳에 모범 패턴이 있다 —
  `ContainerDetailModal.runEnsure`의 영향 서비스 나열 confirm(`:93-109`)과 설정 모달의
  변경 diff 미리보기(`DashboardClient.tsx:1669-1713`). 이를 stop/restart/reset에
  확장한다 — stop은 "의존 서비스 N개 영향"을 이미 로드된 targets 데이터에서 계산
  가능하다.
- **G. 기존 API 파라미터의 UI 노출.** *문제*: 메트릭 기간은 `hours`를 지원하는데 UI가
  1로 고정(`:594`)돼 있고, 로그 tail 선택/복사·커맨드 팔레트(현재 4항목, `:792-817`)
  도 백엔드 능력 대비 축소돼 있다. *설계·효과*: 이미 있는 파라미터를 노출만 한다.
- **H. 볼륨 필드 읽기 전용화.** *문제*: 편집 input과 `+ 추가` 버튼이 활성이지만
  (`:1521-1560`) 서버가 불변 계약으로 거부한다 — 편집을 다 하고 저장할 때에야 거부
  경고(`:1563-1568`)를 보는 함정 UX다. *설계·효과*: "편집 후 경고"를 "처음부터 읽기
  전용 렌더"로 바꾼다. **[v3]** 같은 원리로 sibling이 리터럴 결박한 env 키
  (`PINVI_DB_PORT` 등 — P10-3)도 read-only 표기한다.
- **I. target 단위 일괄 재시작.** *문제*: start/stop/restart가 컨테이너 1개씩뿐이라
  "geo 전체 재시작"이 수동 N회 클릭이다. *설계·효과*: 클라이언트 순차 호출로 구현
  가능(각 호출이 기존 C6c 락 통과), 영향 목록 확인 다이얼로그 필수. 신규 엔드포인트
  불요이므로 0군에 배치한다.

## P8. 제외 확정 항목

- **`image rebuild-service`** — 제외 유지. 정직한 usability 비용 명시: 이 결정으로
  **Geo/Concierge/공용 인프라의 이미지 갱신은 UI에 어떤 경로도 없이 남는다**(수용된
  격차). 기각의 실제 근거는 안전(unpinned 배포·rollback 부재)과 정확성(`--no-deps`가
  `init_steps`를 조용히 건너뛰어 반쪽 기동 — 이것 자체가 나쁜 UX)이다. usability
  보완재: P3의 "업데이트 필요" 표시가 "언제 CLI를 돌려야 하는지"를 알려주고, P7-E
  카드가 실행할 `ensure --build` 명령 원형을 제공한다.
- **`rebuild-pinned` production 실행 버튼** — 제외 유지. 단 두 갈래 분리: (i) **진행
  관측 UI는 만든다**(P2-3·4의 요약 계층). (ii) 서버가 이미 `rehearsal/rebuildable`
  조합이 아니면 거부하므로 **rehearsal 환경 한정 버튼**은 기존 `IS_DEV` + 서버 거부
  이중 패턴으로 성립 가능하다 — **오너 승인됨(Q5)**: typed
  confirmation("rebuild-pinned" 타이핑) + 기존 서버측 environment 게이트 하에 이
  버튼을 추가한다. 현재 n150이 rehearsal/rebuildable 모드로 운용 중이므로 실호스트에서
  실제로 동작하는 버튼이다. **[v3 선행 조건]** typed confirmation만으로는 교차 저장소
  운영 규약(1 pinset = 1회 실행, terminal 재시도 금지)을 지킬 수 없다 — 버튼 활성화의
  서버측 선행 조건으로 **P10-1의 blocked/terminal pinset 자동 거부 + "이 pinset 실행
  이력 0회" 게이트**를 요구한다(KUM-M2 선행). production 모드 호스트에서 필요한 것은
  여전히 버튼이 아니라 전제조건 체크리스트(락 상태·pinned SHA·generation phase —
  전부 P1/P2 데이터) + 붙여넣을 SSH 명령(P7-E)이다.
- **`compose-boundary` 3종** — mutation의 UI화는 투자 보류 유지. **[v3 보강]** 단 이
  3일 창에 6커밋·~4,000줄이 투입된 활성 작업이었고, 실측 사건에서 legacy override
  존재가 승인된 rebuild 전체를 막은 P1 blocker였다(진단 5-③). 이관 진행 상태
  (staged/retired/activated)는 root-only 증거 디렉터리(`.retired-compose-overrides/`)
  에만 있어 화면에서 볼 수 없으므로, **"Compose 입력이 single-file인가" read-only
  배지**를 배포 정합성 패널에 넣는다(P10-4의 (iii)).
- **일반형 GitHub pull+build** — 영구 범위 밖(무변경, 근거는 P3).
- **Manager 자기 재기동** — 기술적 필연으로 SSH 전용(무변경). "재기동 필요" 감지 시
  정확한 SSH 명령을 표시(P7-E).

## P8b. 교차 요건·승격 항목 (제외 아님 — 활성 tier의 출처)

- **Docker disk-usage 카드 — 승격**: 비전문가가 시스템을 죽이는 가장 그럴듯한 경로가
  "디스크 참"이다. `docker system df` wrapper 신규(현재 저장소에 호출 0건) + KPI 카드
  + 임계 경고(85% 등). TTL 캐시·수동 새로고침·single-flight 전제(v1 유지). raw
  수치보다 "정리 시 약 N GB 확보 가능" 요약을 우선한다.
- **감사 로깅 공통 전제**(v1 유지): 이 문서의 신규 mutation 전부(`pin rotate`, backup
  create, password change)에 `admin.py:54-63` 패턴의 durable audit row.
- **Push 알림** — Grafana로 해결, 범위 밖(v1 유지, Grafana가 Manager를 실제 스크레이핑
  하는지는 미검증 전제로 명시).

## P9. 구조 리팩토링 평가

**결론: 전면 리팩토링 불필요.** UI 로드맵을 막는 것은 두 god-module(compose_service.py
7,503줄 / c6c_deployment.py 7,675줄)의 "크기"가 아니라 4개의 구체적 결손이다:
(1) job runner 부재, (2) DashboardClient 단일 컴포넌트(195-1770줄이 한 함수 —
useState 22개, raw WS effect 2개, 인라인 모달 4개), (3) pin이 소스코드 상수(→ P1),
(4) read-only facade 함수 몇 개의 부재. god-module 내용 대부분(rebuild 오케스트레이션
약 1,290줄 단일 메서드 `:6014-7307`, smoke 검증기 약 2,000줄)은 UI 로드맵이 **건드릴
필요가 없는** 코드다. 서비스 생태계는 이미 21개 모듈로 잘 추출돼 있고
(`standalone_backup.py` 573줄, `pinned_runtime_release.py` 190줄 등), 문서의 신규
엔드포인트가 필요로 하는 함수는 대부분 그 작은 모듈들에 있다.

**점진 계획 — 5단계, 각각 단독 배포 가능:**

1. **읽기 전용 facade + route**: `services/deployment_status.py` 신설 —
   manifest/journal 노출 + `inspect_c6c_image_source_revision`의 non-raising wrapper.
   P2·P3 해제. **[v3 정정]** v2의 "~200줄 신규, 기존 코드 무변경"은 성립하지 않는다 —
   진단 6대로 root-side publisher(rotate/rebuild 시 world-readable 사본 기록)가
   필요해 rebuild 경로에 사본 기록 훅이 추가된다. P1-(c) publisher와 공유 설계로
   1회만 만든다(KUM-M3).
2. **`services/job_runner.py`** + backup create 202-비동기 노출. gc 결함 수선(P5)도
   이때 편승. ~150줄 신규.
3. **프론트 추출**: status/log WS effect → 훅 2개, 인라인 모달 4개 → 파일 분리(기존
   `BackupHistoryPanel` 선례 패턴). 동작 무변경 순수 이동, DashboardClient 잔여
   ~600줄. **신규 패널이 2개 이상 되기 전에 선행**(P0 표의 신규 4행 기준) — 지금
   구조에 더하면 2,300줄+로 가고 ESC-스택 effect(`:285-302`)에 분기가 계속 늘어난다.
4. **pin registry 데이터화**(P1). 소비처 1곳이라 파급 최소.
5. **(선택, 로드맵 무관) god-module 기계적 분할**: rebuild 오케스트레이션 →
   `pinned_runtime_orchestrator.py`, smoke 검증기 → `c6c_smoke.py`. **UI 로드맵의 어떤
   항목도 이 단계를 선행조건으로 요구하지 않는다** — 유지보수성 투자로만 정당화되므로
   맨 뒤이고, 생략해도 로드맵은 완주된다.

안전 트레이드오프: 1·3·4단계는 위험 중립~감소(4단계는 "코드 수정으로 pin 회전"이라는
현재의 오류 유발 경로를 검증된 데이터 경로로 대체). 2단계만 실질 트레이드오프 — 장시간
mutation의 HTTP 트리거화이며 UID/ACL 결정(Q2) 선행. 5단계는 15k 라인 이동이라 리뷰
부담 대비 UI 효용이 0이다.

---

# 3부 — 실행 계획: 태스크 분해 (v3 신규)

각 태스크는 단독 PR로 완결 가능하게 자른다. `[선행: X]`는 착수 의존이다. map·pinvi
저장소 태스크는 본 저장소에서 실행 불가하므로 착수 시 해당 저장소 tasks 문서로
미러링한다.

## Manager 저장소 (KUM-M*)

| ID | 내용 | 근거 절 | 선행 |
|---|---|---|---|
| KUM-M1 | pin registry 파일화: `runtime-pins.json` 스키마+로더(검증 유지)+`KTDM_RUNTIME_PINS_FILE`+캐시(mtime+digest)+`pin init/show/verify/rotate/rollback`+중복 상수 삭제+테스트 재작성(값 고정→구조 검증) | §1.2 (a)(a′)(b), Q1 | — |
| KUM-M2 | pinset lifecycle: `blocked_pinsets`/`history`/`supersedes` 필드, `pin block`, rotate 시 terminal 자동 등재, `rebuild-pinned`의 terminal/재실행 자동 거부, rollback의 terminal 제한, d9 상수 3종 이관·삭제 | §1.3 P10-1·2, 진단 3 | KUM-M1 |
| KUM-M3 | root-side world-readable **pin registry** publisher: rotate/init가 pin의 secret-free 사본을 backend 가독 경로에 원자 기록, backend 로더의 digest 재계산+stale fail-close (**완료 2026-08-28**) | §1.2 (c), 진단 6 | KUM-M1 |
| KUM-M4 | 조회 API 2종: `GET /runtime-pins`(lifecycle·history·generation 일치 여부·Manager provenance 동봉), `GET /pinned-runtime/generation`(manifest/journal 원본+envelope summary+terminal 분류+receipt/fence 상태+버전 명시) + 배포 정합성 패널 UI. **2026-08-28 정정**: public v6/v8 publisher·strict reader·`ktdctl pin publish-generation`·API raw/envelope까지 완료. UI panel 및 P2-4의 builder receipt/fence 관측은 남은 범위다 | P2, §1.3 P10-2 | KUM-M3 |
| KUM-M5 | UI 2-step pin rotate: 회전 요청 폼→audit row 기록, `pin apply-pending --confirm` CLI, 대기 중 요청 표시 (**완료 2026-08-28** — 요청 저장소는 registry와 다른 트리의 backend-writable 파일이며 어떤 로드 경로도 읽지 않는다) | §1.2 (c), Q4 | KUM-M1, KUM-M4 |
| KUM-M6 | typed 진단 소비: stderr 9문구 파싱(`compose_service.py:478-497`)을 `pinvi.role-topology-diagnostic.v1` 소비로 이관, reason enum→P2 배지, verifier 호출의 journal phase 편입 (**부분 완료 2026-08-28** — (iii) 계약 결박 env read-only화와 `pinvi_role_bootstrap_modes` readiness 검사는 완료. (i)(ii)는 P10-3 정정대로 KUM-PV-3 선행이며 Manager 단독으로는 불가능) | §1.3 P10-3 | (pinvi 짝: KUM-PV-3) |
| KUM-M7 | preflight readiness 노출: base image present / wheelhouse 완결성 / single-file Compose / sibling 필수 파일 — read-only 행 4종 (**완료 2026-08-28** — wheelhouse는 검사 불가로 판정해 이유와 함께 `unavailable_checks`로 노출. 화면은 pin 패널이 아니라 `SourceStatusPanel`에 붙였다: pin 패널이 M5로 mutation 패널이 됐기 때문) | §1.3 P10-4, 진단 5 | KUM-M4(패널) |
| KUM-M8 | `source-status` + compare 링크 + installer provenance 리더(~10줄) + Map entrypoint/Dockerfile 계약 drift 행 + 환경 완결성 카드 | P3, P4 | — |
| KUM-M9 | `services/job_runner.py` + backup create 202 비동기 + shared group/setgid + gc 결함 수선(lock 3줄 등) (**완료 2026-08-28** — job 기록은 권위가 아니고 디스크 manifest가 권위라는 점을 코드·문서에 명시. shutdown은 진행 중 job을 취소하지 않는다) | P5, P9-2, Q2 | — |
| KUM-M10 | 관리자 비밀번호 변경 폼: `POST /admin/password`(단일 키 allowlist, atomic) + **미종결 rebuild journal 가드(거부 또는 경고+typed confirm)** + audit (**완료 2026-08-28** — 가드는 3갈래다: 증명된 미종결은 우회 불가, 확인 불가는 명시 승인. backend가 root의 `0700` journal을 늘 볼 수 있는 것은 아니라는 사실을 설계에 반영했다) | P6, Q3 | — |
| KUM-M11 | 프론트 quick wins 일괄: P7-A~I(오류 humanize, 라벨 한국어화, 그룹 뷰, health 카드, CLI 명령 카드, 미리보기 전역화, 파라미터 노출, 볼륨/계약 키 read-only, 일괄 재시작) | P7 | — |
| KUM-M12 | 프론트 구조 추출(WS 훅 2개·모달 4개 분리) — 신규 패널 2개 이상 전 선행 (**완료 2026-08-28** — 라벨·아이콘·포맷터와 타입을 `lib/containerPresentation.ts`·`lib/format.ts`로 이관해 표시 규약을 찾을 수 있는 자리에 뒀다(2,138 → 1,968줄). **JSX 본문은 의도적으로 쪼개지 않았다**: 서른 개 가까운 state를 공유하는 하나의 컴포넌트라 하위 컴포넌트로 뜯으려면 그 state를 전부 prop으로 꿰어야 하고, 순수 리팩터가 행동 변경 위험을 안게 된다) | P9-3 | — |
| KUM-M13 | `db-backup restore` CLI(~300-500줄, role별 정지/기동 절차 설계, 적용 경계 runbook 명시) + 이후 UI 안내 (**1단계 완료 2026-08-28 — 오너 결정으로 읽기 전용 `restore-plan`만 먼저**. 목록에 백업이 보이는 것과 복원할 수 있는 것이 다르므로, 파괴적 명령을 그 거짓 안전감 위에 얹지 않는다. digest 재계산·크기·schema revision 대조를 하고 차단/참고를 구분한다. 파괴적 `restore`는 이 계획이 실제로 무엇을 잡는지 본 뒤 결정) | P5, Q6 | KUM-M9 권장 |
| KUM-M14 | rehearsal 한정 rebuild 버튼: typed confirmation + 서버 environment 게이트 + terminal/실행이력 게이트 (**완료 2026-08-28 — 오너 결정으로 버튼이 아니라 게이트된 CLI 카드**. `rebuild-pinned`는 root를 요구해 HTTP 요청으로 실행할 수 없고, 실행 가능하게 만드는 것은 편의가 아니라 경계 제거다. `GET /api/v1/pinned-rebuild/preflight`가 차단 사유를 판정하고 화면은 실행할 명령만 준다) | P8, Q5 | **KUM-M2 필수** |
| KUM-M15 | `.env.example` drift 수정: PinVi role credential 6종 추가, 폐기 `PINVI_DOCKER_DATABASE_URL` 제거 (docs-only, 즉시 가능) | P3 [v3] | — |
| KUM-M16 | CLAUDE.md 낡은 지점 동기화 (별도 작은 PR) | Q7 | — |
| KUM-M17 | (기록만 — 착수는 별도 결정) M05 활성화 선행 조건: Manager compose의 M05 env/mount 확장, `PINVI_*_IMAGE_DIGEST` 주입, pinned↔activation generation 병렬 표시 | §1.3 P10-5 | 오너 별도 결정 |
| KUM-M18 | disk-usage 카드(`docker system df` wrapper + TTL 캐시) | P8b | — |

## kor-travel-map 저장소 (KUM-MAP-*)

| ID | 내용 | 근거 | 선행 |
|---|---|---|---|
| KUM-MAP-1 | pinset·candidate 수기 부기 축소: `docs/tasks.md`의 차단 목록·회전 기록 규약을 "Manager `/runtime-pins`·`/pinned-runtime/generation` 참조" 1줄 규약으로 개정. terminal 판정 근거는 Manager journal 링크로 대체 | 진단 2·3, P10-2 | KUM-M2·M4 배포 |
| KUM-MAP-2 | Manager draft PR SHA 전사 중단: runbook에서 "Manager release는 installed provenance(API 동봉 값) 참조" 규약화 | 진단 2, P10-2 | KUM-M4 배포 |
| KUM-MAP-3 | attestation 계약 명문화: `c7_prod_attestation.py`의 exact-dict 결박이 Manager 문서 스키마의 공개 계약임을 map `docs/integration-map.md`에 상호 참조로 명시(Manager 쪽은 P10-5에서 완료). manifest/journal 버전 인상 시 동시 PR 절차 정의 | P10-5 | — |
| KUM-MAP-4 | (제안) `rebuild-pinned` 실패 시 사람이 문서에 적던 "다음 후보 준비" 체크리스트를 Manager readiness 행(KUM-M7)과 대응시키는 runbook 갱신 | P10-4 | KUM-M7 배포 |

## pinvi 저장소 (KUM-PV-*)

| ID | 내용 | 근거 | 선행 |
|---|---|---|---|
| KUM-PV-1 | resume/journal/tasks의 generation 값 수기 복제(축약 SHA 14개 등) 축소: "committed generation은 Manager `/pinned-runtime/generation` 참조" 규약으로 개정 | 진단 2, P10-2 | KUM-M4 배포 |
| KUM-PV-2 | pair provenance 계약 자동 대조: `contracts/kor-travel-map-m05-pair-provenance-v1.json`·테스트 `_UPSTREAM_COMMIT`의 값이 Manager generation manifest와 일치하는지 CI에서 대조(수기 복사→자동 검증, 회전 1회당 5파일 churn의 실수 위험 제거) | 진단 2 | KUM-M3·M4 배포 |
| KUM-PV-3 | sealed role topology verifier 호출 순서 계약을 journal 산문에서 execplan/계약 문서로 승격하고, typed JSON(`pinvi.role-topology-diagnostic.v1`)을 유일 소비 표면으로 선언(stderr 문구는 비계약 명시 — Manager KUM-M6과 짝) | P10-3, 진단 4 | — |
| KUM-PV-4 | (기록만) M05 activation의 Manager 의존 명시: compose env/mount 요구·image digest 주입 주체를 pinvi 문서에서 "Manager 계약 확장 대기"로 상호 참조 | P10-5 | KUM-M17 결정 |

**의존 요약**: KUM-M1 → M2 → (M14), M1 → M3 → M4 → (M5, M7, MAP-1·2, PV-1·2).
M6·M8~M13·M15·M18은 상호 독립. 프론트 신규 패널이 2개 이상 되기 전 KUM-M12 선행.

## 진행안 — 1부 트랙 실행 규약 (2026-08-28 착수)

1부 트랙(KUM-M1~M4 코어 + M7)은 아래 규약으로 진행한다.

- **브랜치**: `feat/pin-registry-part1`을 `main`에서 분기해 그 위에서만 작업하고, 완주
  뒤 `main`에 머지한다. 작업 중 `origin/main`을 자주 fetch·rebase한다.
- **PR**: 착수 즉시 draft PR을 올리고 작은 단위로 자주 커밋·push해 진행을 공개한다.
  완주 시점에 ready 전환 후 머지한다.
- **적대 리뷰**: 전문 리뷰어 서브에이전트 **2인**이 서로 다른 각도(계약·fail-close
  정합성 / 운영·회귀·사용성 관점)로 독립 리뷰하고, 확인된 지적은 전부 반영한 뒤에만
  머지한다.
- **테스트**: 격리 환경에서 n150 live E2E를 우선한다. live 실행이 불가능하거나 파괴적
  경로(실제 `rebuild-pinned` 등)는 mock/단위 테스트로 대체하고, 무엇을 mock으로
  대체했는지 저널에 명시한다.
- **범위 경계**: 이번 라운드는 pin registry·lifecycle·publisher·조회 API와 그
  readiness 노출까지다. typed 진단 이관(M6)·운영 기능 트랙(M9·M10·M13)은 후속 라운드로
  분리한다. **M5(UI 2-step pin rotate)는 오너 지시로 이 라운드에 편입해 완료했다**
  (2026-08-28) — 계약은 `runtime-pin-registry.md` §7-1이 정본이다.

---

## 우선순위 권고 (usability-first, v3 태스크 매핑)

**0군 — 프론트 전용, 백엔드 0줄 (즉시 착수 가능, 정책 결정 불요)**
1. 오류 humanize + 라벨 한국어화 (P7-A·B) — 코드 변경 최소, 체감 최대. [KUM-M11]
2. 백업 freshness 배지 (P5) / Manager health 카드 (P7-D) / target 그룹 뷰 (P7-C).
   [KUM-M11]
3. 복사 가능한 CLI 명령 카드 (P7-E) — CLI-전용 정책 전체의 usability 보완재. [KUM-M11]
4. mutation 미리보기 전역화 (P7-F) / 볼륨·계약 키 읽기 전용화 (P7-H+P10-3) / 기존
   파라미터 노출 (P7-G) / target 단위 일괄 재시작 (P7-I). [KUM-M11]
   — `.env.example` drift 수정 [KUM-M15]도 이 군(docs-only, 즉시).

**1군 — 저비용 백엔드 read-only**
5. `source-status` + "사람 말" 정합성 패널 + GitHub compare 링크 + installer
   provenance 리더 + 계약 drift 행 (P3·P4). [KUM-M8]
6. disk-usage 카드 (P8b). [KUM-M18]

**2군 — 구조 투자 (pin·결박 트랙: 1부의 1순위 작업)**
7. **pin registry + lifecycle + publisher + 조회 API/패널** — 시간 절감 총량 최대이며
   sibling 부기 대체의 전제. [KUM-M1 → M2 → M3 → M4, 승인 Q1 + v3 확장]
8. preflight readiness 행 (P10-4). [KUM-M7]
9. typed 진단 소비 이관 (P10-3). [KUM-M6, pinvi KUM-PV-3과 짝]
10. UI 2-step pin rotate. [KUM-M5, 승인 Q4]
11. rehearsal 한정 rebuild 버튼 — **terminal 게이트(KUM-M2) 선행 필수**. [KUM-M14,
    승인 Q5]

**2군 — 운영 기능 트랙**
12. job_runner + 백업 create 버튼 + gc 결함 수선. [KUM-M9, 승인 Q2]
13. 관리자 비밀번호 변경 폼 + rebuild journal 가드. [KUM-M10, 승인 Q3]
14. 프론트 구조 추출(신규 패널 2개 이상 전). [KUM-M12]
15. `db-backup restore` — CLI 우선. [KUM-M13, 승인 Q6]

**3군 — CLI 전용 유지 / 제외 (v1 결론 유지분)**
16. secret rotate(비밀번호 외 전부) — CLI 전용 + 상태 표시 + 해소 명령 카드 (P6).
17. `db-backup gc` — CLI 전용(결함 수선은 P5의 단일 문단 참조).
18. `image rebuild-service` / `compose-boundary` mutation / 일반형 pull+build /
    production rebuild 버튼 — 제외 (P8; compose-boundary의 read-only 배지는 P10-4로
    1군·2군 트랙에 편입).

**교차 저장소 (Manager 쪽 배포 후 착수)**
19. map: KUM-MAP-1~4 / pinvi: KUM-PV-1~4 — 3부 표 참조.

## 오너 결정 사항 (2026-08-28 확정)

v2의 열린 질문 7건에 오너가 전부 답했다. 결정은 아래와 같고, 위 우선순위·각 절에
반영돼 있다.

| # | 질문 | 결정 |
|---|---|---|
| Q1 | pin registry 전환(P1) | **승인** — "PR review = pin 승인" 게이트를 CLI(`--confirm`+root+reason+감사기록)로 대체하는 트레이드오프 수용 |
| Q2 | 백업 create UI화(job_runner) | **승인** — dump 소유권은 **shared group + setgid 디렉터리** 방식(기존 프로세스 구성 무변경) |
| Q3 | 관리자 비밀번호 변경 폼 | **승인** — backend의 `.env` 단일 키(`KTDM_ADMIN_PASSWORD_HASH`) 쓰기 경계 완화 수용 |
| Q4 | UI pin 회전 범위 | **2-step rotate까지** — UI 요청 기록 + SSH `pin apply-pending --confirm` 적용 |
| Q5 | rehearsal 한정 rebuild 버튼 | **버튼 추가** — typed confirmation + 기존 서버측 environment 게이트 유지. 현 n150이 rehearsal 모드라 실동작 버튼임을 인지하고 승인 |
| Q6 | restore 로드맵 | **포함** — CLI 우선, ~300-500줄 + role별 정지/기동 절차 설계 |
| Q7 | CLAUDE.md 낡은 지점 동기화 | **별도 작업으로 진행** — 이 설계 PR과 분리한 작은 docs-only PR |

이로써 v2의 미결 정책 결정은 없다. **[v3 주기]** v3에서 추가된 1부(진단·P10)와 3부
태스크 분해는 기존 승인(Q1~Q6) 범위 안의 설계 구체화(KUM-M2·M10·M14의 가드 등) 또는
신규 제안(KUM-M6·M7·M15·M17·M18, 교차 저장소 태스크)이며, 신규 제안의 채택 여부는
`docs/tasks.md`의 `KTDCTL-UI-MIGRATION` 태스크에서 구현 태스크를 분리할 때 항목별로
확정한다. KUM-M17(M05 활성화 선행 조건)은 범위 자체가 별도 오너 결정 사안이다.
