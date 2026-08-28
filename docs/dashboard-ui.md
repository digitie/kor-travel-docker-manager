# 대시보드 UI — 에이전트 레퍼런스

**대상 독자**: 프론트엔드를 고치는 에이전트와 운영자.
**정본 관계**: 설계 의도와 우선순위는 [`ktdctl-ui-migration.md`](ktdctl-ui-migration.md)
P0·P7, 디자인 토큰과 룩앤필은 [`design-system.md`](design-system.md)와
[`DESIGN-RULES.md`](DESIGN-RULES.md). **이 문서는 "화면이 무엇을 약속하는가"와
"고칠 때 무엇을 깨뜨리면 안 되는가"를 다룬다.**

구현: `frontend/src/components/`, `frontend/src/lib/`

---

## 0. 이 UI의 설계 원칙

이 대시보드의 사용자는 **비전문 관리자**다. 그 전제에서 나온 규칙이 아래 셋이고,
새 화면을 추가할 때도 그대로 적용한다.

1. **개발자 어휘를 화면에 그대로 내보내지 않는다.** Docker 상태 원문, 내부 role
   식별자, raw JSON, sha256 다이제스트는 사람 말로 번역하고 원문은 `title`이나 접힌
   상세로 보존한다. 버리지는 않는다 — 운영자가 붙여넣어야 할 때가 있다.
2. **파괴적이거나 범위가 넓은 조작은 누르기 전에 범위를 보여 준다.** 이 시스템의
   컨테이너는 서로 의존하므로 하나를 멈추면 딸려 멈추는 것이 있다. 그 사실을 실행
   후에 알게 하면 안 된다.
3. **할 수 없는 일은 처음부터 할 수 없게 보인다.** 서버가 거부할 입력을 편집하게
   해 놓고 저장 시점에 거절하는 것은 함정이다. 읽기 전용으로 렌더하고 이유를 쓴다.

---

## 1. 오류 표시 계층 — 새 mutation을 추가할 때 반드시 쓴다

`window.alert()`로 raw 응답 본문을 띄우지 않는다. 경로는 셋이다.

```
apiJson 실패
  └─ ApiError (lib/api.ts)          응답 본문을 파싱해 code / serverMessage / raw 보존
       └─ humanizeError(err, 동작명)  (lib/errors.ts) → { title, hint, raw }
            └─ errorToast(...)        (components/Toast.tsx) → ToastStack
```

- `ApiError`는 FastAPI의 `{"detail": ...}`를 문자열·객체 양쪽 형태로 파싱한다.
  **파싱에 실패해도 예외를 던지지 않는다** — 오류 표시 경로가 다시 오류를 내면 화면이
  아무 말도 못 하게 된다.
- `lib/errors.ts`의 `CODE_MESSAGES`는 백엔드 계약 코드
  (`services/c6c_deployment.py`와 `api/routes.py`의 `RUNTIME_PIN_*`가 소유)의 한국어
  번역이다. 새 계약 코드를 백엔드에 추가했다면 여기에도 추가한다. **매핑이 없어도 상태
  코드 기반 문구로 떨어지므로 표시가 깨지지는 않는다.**
- 모달 안에서는 토스트 대신 `RuntimePinPanel`의 `InlineError`처럼 그 자리에 표시한다.
  토스트 스택은 `DashboardClient`가 소유하므로 모달이 자기 실패를 화면 구석으로 보내면
  방금 누른 버튼 옆이 비어 보인다. 문구는 여전히 `humanizeError`가 만든다.
- 성공 알림은 `successToast(제목, 설명)`. 성공은 6초 뒤 자동으로 사라지고 **실패는
  사람이 닫을 때까지 남는다**(읽기 전에 사라지면 안 된다).
- 실패 토스트의 "자세히"에 원문이 접혀 있다. 이것이 운영자가 이슈에 붙여넣을 값이다.

**새 mutation 체크리스트**: `onSuccess`에 `pushToast(successToast(...))`,
`onError`에 `pushToast(errorToast(humanizeError(err, '동작명')))`. `alert()` 금지.

---

## 2. 라벨 규약

| 대상 | 헬퍼 | 원문 보존 |
|---|---|---|
| 컨테이너 상태 | `statusLabel(status)` | `title` 속성 |
| 컨테이너 role | `roleLabel(role)` | `title` 속성 |
| 컨테이너 표시명·아이콘 | `getContainerPresentation(container)` | — |

셋 다 `components/DashboardClient.tsx` 상단에 있다. 새 상태값·role이 백엔드에 생기면
매핑을 추가한다. **매핑에 없으면 원문을 그대로 보여 주므로 화면이 비지는 않는다.**

---

## 3. 조작 UI의 확인 규약

| 조작 | 확인 방식 | 근거 |
|---|---|---|
| start | 없음 | 되돌리기 쉽고 영향이 없다 |
| stop / restart | 영향받는 target 목록을 세어 확인 | 의존 서비스가 딸려 멈춘다 |
| 설정 저장 | 변경 diff 미리보기 | 무엇이 바뀌는지 보여 준다 |
| 설정 원복 | 문구 확인 | 컨테이너가 재생성된다 |
| target 전체 재시작 | 대상 이름을 나열해 확인 | 순차 재시작이라 중간 실패가 가능하다 |
| `ensure`(target 재생성) | 대상 서비스 수 + DB 포함 시 경고 | `ContainerDetailModal.runEnsure`가 원형 |

**target 단위 일괄 재시작**은 신규 엔드포인트 없이 컨테이너 제어 API를 순차 호출한다.
각 호출이 기존 C6c 락을 그대로 통과하므로 서버 계약을 우회하지 않는다. 중간 실패는
컨테이너별 토스트로 알리고 나머지는 계속 진행한다 — 하나 실패했다고 나머지를 중단하면
"절반만 재시작된 상태"의 원인을 알기 더 어려워진다.

---

## 4. 앱별 상태 그룹 — 전이 폐포 함정

`GET /api/v1/targets`의 `containers`는 **target이 직접 소유한 목록이 아니라
`depends_on` 전이 폐포**다. 그래서 공용 인프라(PostgreSQL, RustFS, Prometheus…)가 여러
target에 중복 등장하고, "첫 매치"를 쓰면 `dependency_order`가 좁은 것부터 나열된다는
우연에 기대게 된다.

**컨테이너마다 가장 좁은(= `containers` 길이가 최소인) target에 한 번만 배정한다.**
`containerGroups`와 `detailTarget`이 같은 규칙을 쓴다. 새로 그룹을 만들 일이 있으면
이 규칙을 복제하지 말고 기존 계산을 재사용하라.

---

## 5. CLI 전용 작업의 화면 표현

이 관리도구에는 의도적으로 CLI에만 남긴 작업이 있다(파괴적 재구축, pin 회전, secret
회전, 백업 정리 등 — 근거는 `ktdctl-ui-migration.md` P8). 비전문 관리자에게는 "SSH에서
뭘 치라는 건지"가 그 자체로 장벽이므로, **CLI 전용 결정마다 실행할 명령 원형을 화면에
둔다.**

`components/CopyableCommand.tsx`를 쓴다. 자체 복사 버튼을 새로 만들지 마라.

```tsx
<CopyableCommand
  command="sudo -n backend/.venv/bin/ktdctl pin verify"
  label="고정 상태 점검"
  hint="0을 반환해야 재구축을 시작할 수 있습니다."
/>
```

`navigator.clipboard`는 비보안 컨텍스트에서 없을 수 있다. 실패해도 명령 텍스트는 그대로
보이므로 조용히 무시한다 — 복사 실패로 오류 토스트를 띄우지 않는다.

### 5-1. 요청-적용 2-step — 화면이 할 수 있는 최대치

pin 회전처럼 **backend가 물리적으로 실행할 수 없는** 조작은 CopyableCommand만 두고
끝내지 않는다. 화면은 *무엇을 바꾸고 싶은지*를 기록하고(요청), 적용은 root CLI가 한다.
`RuntimePinPanel`의 "버전 변경 요청" 폼이 원형이며 계약은
[`runtime-pin-registry.md`](runtime-pin-registry.md) §7-1이다.

이 패턴을 복제할 때 지킬 것:

- **요청 폼은 서버가 받을 수 있을 때만 보여 준다.** `status !== 'ok'`이거나 이미 대기
  요청이 있으면 폼을 렌더링하지 않는다. 눌러 본 뒤에야 거부를 알게 하면 안 된다.
- **아무것도 바뀌지 않았다고 명시한다.** 버튼 문구는 "적용"이 아니라 "변경 요청 기록"이고,
  폼과 대기 카드 양쪽에 적용 명령을 함께 둔다.
- **서버 검증을 화면에서 복제하되 대체하지 않는다.** 40-hex 검사는 즉시 피드백을 위한
  것이고, 판정은 백엔드가 다시 한다.
- **공개되는 입력은 그 사실을 입력칸 옆에 쓴다.** 회전 사유는 world-readable 사본에
  그대로 실리므로 비밀을 적지 말라고 명시한다.
- **무효가 된 요청을 적용 가능한 것처럼 보이지 않게 한다.** 백엔드가 `stale`을 주면
  카드 색과 문구를 바꾸고 적용 명령 대신 폐기 명령을 준다.

---

## 6. 패널 추가 규약

기존 패널: `AdminSettingsPanel`, `BackupHistoryPanel`, `RuntimePinPanel`,
`ContainerDetailModal`.

새 패널을 만들 때 `RuntimePinPanel.tsx`를 원형으로 삼는다. 지켜야 할 것:

- `'use client'`, `ops-modal` 클래스, `role="dialog"` + `aria-modal` + `aria-labelledby`.
- ESC로 닫기(자체 `useEffect` keydown 핸들러). **`DashboardClient`의 ESC 스택은 기존
  4개 모달만 관리한다** — 새 패널은 자기 ESC를 스스로 처리한다.
- `useQuery({ retry: false })`. 400/409류 영구 오류를 재시도하면 `isLoading`이 수 초
  유지돼 "로딩 중"으로 오해하게 만든다.
- 진입점은 두 곳에 등록한다: `AppShell`의 내비게이션과 커맨드 팔레트(⌘K).
- 읽기 전용 패널은 제목에 "(읽기 전용)"을 쓰고, mutation이 CLI 전용이면 그 명령을
  `CopyableCommand`로 함께 준다. 요청-적용 2-step(§5-1)을 붙이면 그 패널은 더 이상
  읽기 전용이 아니므로 제목의 "(읽기 전용)"을 떼고 무엇이 기록되는지 부제에 쓴다.

---

## 7. 상태 표시의 정직성 규약

값을 모를 때 그럴듯한 값을 보여 주지 않는다. 백엔드가 `unknown`/`degraded`/`stale`을
반환하면 화면도 그대로 "확인 필요"로 표시하고, 확인 방법(SSH 명령)을 함께 준다.
`RuntimePinPanel`이 이 패턴의 원형이다 — 자세한 상태 의미는
[`runtime-pin-registry.md`](runtime-pin-registry.md) 7절.

---

## 8. 이 영역을 고칠 때의 체크리스트

- [ ] 새 mutation에 `alert()`를 쓰지 않았고 성공·실패 토스트가 있다.
- [ ] 새로 노출한 서버 값에 개발자 어휘(원문 상태, raw digest, JSON)가 그대로 있지 않다.
- [ ] 파괴적·광범위 조작에 범위를 보여 주는 확인이 있다.
- [ ] 서버가 거부할 입력은 편집 가능하게 두지 않았다.
- [ ] 새 패널이 ESC·포커스·`aria-*`를 갖췄고 진입점 두 곳에 등록됐다.
- [ ] `npm run type-check`, `npm run lint`, `npm run build` 통과 (WSL에서 실행).
- [ ] 화면 변경은 n150 live에서 확인했다(Playwright는 n150 Linux 우선).
