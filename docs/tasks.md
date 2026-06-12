# TASKS — 개발 태스크 백로그

이 문서는 `tripmate-manager`의 진행 중/대기 작업만 관리한다. 완료된 작업은
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
| **T-220** | `krtour-ai-agent` provider 상세 구현 및 명칭 전환 | `[ ]` | - | `T-221` 착수 전 선행 작업 |
| **T-221** | 사용자 지정 후속 작업 1 | `[ ]` | - | `T-220` 완료 후 착수, 세부 범위 확정 필요 |
| **T-222** | 사용자 지정 후속 작업 2 | `[ ]` | - | `T-221` 완료 후 착수, 세부 범위 확정 필요 |
| **T-223** | 사용자 지정 후속 작업 3 | `[ ]` | - | `T-222` 완료 후 착수, 세부 범위 확정 필요 |

---

## 진행 순서

1. `tasks.md`와 `tasks-done.md`를 최신 완료/미완료 상태로 정리한다.
2. `T-220`에서 `tripmate-agent` 잔여 명칭을 `krtour-ai-agent` 기준으로 정리하고,
   TripMate main과 AI agent의 직접 의존 관계를 끊는다.
3. `T-221`, `T-222`, `T-223`을 순서대로 진행한다.
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

### T-220: `krtour-ai-agent` provider 상세 구현 및 명칭 전환

- [ ] `config/docker-targets.yml`의 `ai` target을 `krtour-ai-agent` 기준으로 정리
- [ ] `tripmate-agent` 호환 별칭은 필요한 범위에서만 유지하고 새 공식 별칭을 우선한다
- [ ] 통합 DB 기본값을 `krtour_ai_agent` database 기준으로 정리하고 기존 `tripmate_agent`
      환경변수는 호환 입력으로만 처리한다
- [ ] `tripmate` target이 `krtour-ai-agent`에 직접 의존하지 않도록 문서와 target 설명을 정리
- [ ] `krtour-map`과 `krtour-ai-agent` 간 provider 관계만 남도록 아키텍처/포트/관리 문서를 동기화
- [ ] 관련 테스트와 설정 검증을 갱신한다

### T-221: 사용자 지정 후속 작업 1

- [ ] `T-220` PR 머지 후 세부 범위를 확정한다
- [ ] 작업 전 `main` 기준 rebase를 수행한다

### T-222: 사용자 지정 후속 작업 2

- [ ] `T-221` 완료 후 세부 범위를 확정한다
- [ ] 작업 전 `main` 기준 rebase를 수행한다

### T-223: 사용자 지정 후속 작업 3

- [ ] `T-222` 완료 후 세부 범위를 확정한다
- [ ] 작업 전 `main` 기준 rebase를 수행한다
