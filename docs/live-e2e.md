# 운영 대시보드 live e2e(n150)

운영 배포 뒤 로그인이 필요한 대시보드 화면을 n150에서 실제 브라우저로 자동 확인한다. 러너는
`scripts/n150-live-e2e.sh`, 시험은 `frontend/e2e-live/*.live.spec.ts`, 설정은
`frontend/playwright.live.config.ts`다. CI의 격리 회귀(`frontend/playwright.config.ts`, mock backend)와는
별개이며 CI에서 돌지 않는다.

## 1. 인증 구조와 계정

- 대시보드 로그인은 **단일 관리자 계정**이다. backend env `KTDM_ADMIN_USERNAME`과 `KTDM_ADMIN_PASSWORD_HASH`
  (PBKDF2) 한 쌍만 있고, 세션 cookie의 `sub`가 이 아이디와 같아야 유효하다(`services/auth_service.py`).
  대시보드의 "관리자 설정"에서 비밀번호를 바꾸면 root `.env`의 hash가 다시 쓰인다. 다중 계정·역할은 없다.
- 그래서 e2e 전용 계정을 만드는 정식 경로(CLI·API·화면)는 없다. 자격 파일에는 **운영 관리자 아이디·비밀번호**를
  넣는다. Manager 세션은 컨테이너를 멈추고 다시 만들 수 있는 권한이므로, 시험이 운영을 바꾸지 않는 것은 계정이
  아니라 시험 fixture(`frontend/e2e-live/live-fixtures.ts`)가 지킨다 — 로그인·로그아웃 밖의 GET·HEAD 아닌 요청은
  브라우저에서 끊고, 그런 요청이 하나라도 있었으면 그 시험을 실패시킨다.
- 로그인·로그아웃은 실제 세션(`admin_sessions`)과 로그인 감사 이력을 남긴다. 각 시험은 끝에서 UI로 로그아웃해
  세션을 회수한다(시험이 중간에 실패하면 그 세션은 8시간 TTL로 만료된다).

### 대안 검토 — e2e 전용 읽기 비밀번호(제안만, 구현하지 않음)

`KTDM_E2E_USERNAME`·`KTDM_E2E_PASSWORD_HASH`를 하나 더 받아 그 세션을 읽기 전용으로 두는 방식을 검토했다.

- 얻는 것: 자격 파일이 새어도 컨테이너 조작·설정 변경 권한이 아니다.
- 드는 것: 모든 변경 route(HTTP·WebSocket 로그 포함)에 역할 검사를 넣어야 하고, 하나라도 빠지면 그대로 권한
  상승이다. 읽기 전용이어도 컨테이너 inspect(env는 이름·값 패턴으로 가린다)·로그 스트림처럼 운영 정보를 넓게 보는
  읽기가 있어 "읽기 전용 = 무해"가 아니다 — 그 계정에 무엇을 보일지 다시 정해야 한다. 로그인 표면이 둘로 늘어 무차별 대입 표적도 하나 는다.
- 판단: 지금은 fixture의 변경 차단으로 운영을 지키고, 이 변경은 소유자 결정 뒤 별도 작업으로 한다.

## 2. 소유자가 한 번 할 일 — 자격 파일

n150에서 소유자가 직접 친다. 비밀번호는 `read -rsp`로 받아 화면·명령 이력에 남지 않고(`IFS=`를 붙여 앞뒤 공백도 그대로 받는다), `printf`는 bash 내장이라
프로세스 목록에도 실리지 않는다. 대상 URL은 대시보드의 **공개 https origin**이다 — 운영 번들의 backend 주소·CORS·
cookie가 그 origin 기준이라 loopback(`:12905`)으로는 로그인하지 못한다.

```bash
sudo install -m 600 -o root -g root /dev/null /root/.ktdm-e2e.env
IFS= read -rp 'Manager 대시보드 공개 URL(https://…): ' ktdm_url
IFS= read -rp 'Manager 관리자 아이디: ' ktdm_user
IFS= read -rsp 'Manager 관리자 비밀번호(표시되지 않음): ' ktdm_pw; echo
{
  printf "KTDM_LIVE_WEB_URL=%s\n" "$ktdm_url"
  printf "KTDM_LIVE_USERNAME='%s'\n" "$ktdm_user"
  printf "KTDM_LIVE_PASSWORD='%s'\n" "$ktdm_pw"
} | sudo tee /root/.ktdm-e2e.env >/dev/null
unset ktdm_url ktdm_user ktdm_pw
sudo stat -c '%U:%G %a' /root/.ktdm-e2e.env   # root:root 600
```

허용 키는 위 셋(모두 필수)뿐이다. 다른 키·중복 키·형식 오류가 있으면 러너는 행 번호만 알리고 멈춘다. 값을 감싼
따옴표 한 쌍은 벗긴다(위 명령처럼 감싸 두면 비밀번호 앞뒤가 따옴표여도 그대로 읽힌다). 대시보드에서 관리자 비밀번호를
바꾸면 이 파일도 같은 명령으로 다시 쓴다.

## 3. 실행

검증할 리비전의 소스 트리(git checkout 또는 배포 export)에서 실행한다. 러너가 n150 test gate(`~/gate-slot.sh`,
I/O 가드 포함)를 스스로 거치므로 gate로 다시 감싸지 않는다.

```bash
sudo -v                                      # 자격 파일은 sudo -n으로 읽는다
scripts/n150-live-e2e.sh --list              # 수집만(6 tests: 3건 × desktop·mobile)
scripts/n150-live-e2e.sh                     # 전체 실행 + 요약
scripts/n150-live-e2e.sh --project desktop --grep '수집 경보'
```

시험 범위(읽기 전용):

| 시험 | 확인하는 것 |
| --- | --- |
| 로그인 → 대시보드 → 로그아웃 | 실제 로그인 200, 서비스 원장 행에 운영 `/containers` 응답의 컨테이너 이름(8자 이상)이 보임, 상태 WebSocket이 붙어 프레임을 2개 이상 받고 12초 뒤에도 열려 있으며 재연결을 되풀이하지 않음(2회 이하), 가로 넘침 없음, 로그아웃 뒤 새로고침해도 로그인 화면 |
| 수집 경보 패널 | 배포 직후라 첫 평가 전이면 최대 5분 기다린 뒤, 운영 `/plane-alerts` 응답 그대로(패널은 60초마다 다시 읽으므로 마지막 응답 기준): 감시 켜짐·설정/평가 오류 없음·정지(stale) 아님·host가 `KTDM_LIVE_EXPECT_HOST`(기본 n150), 요약 문구·역할(alert/status), 진행 중 경보 수, 점검 항목 전부("이름 · 상태 — 설명"), **공용 Dagster role 연결 수가 측정돼 `N/60(비율)`**(n150 기준 한도 60, 다른 host는 `N/한도`), n150에서 **공항 통계 사전집계 갱신 "해당 없음"**, 외부 발송이 꺼져 있으면 **"외부 발송은 꺼져 있습니다"와 Telegram이 보내지 않는 까닭**(켜져 있으면 채널·최근 실패), 발송 문구에 Telegram bot 토큰 값 모양 없음 |
| 로그인하지 않은 브라우저 | 로그인 화면만 보이고 원장·경보 패널이 없음, cookie 없이 backend `/plane-alerts`·`/containers`를 부르면 401/403 |

러너가 지키는 것:

- 자격 파일은 root 소유 일반 파일이고 mode가 0600(또는 0400)이어야 하며, 경로에 심볼릭 링크가 끼지 않고, 담은
  디렉터리도 root 소유에 group·other 쓰기가 없어야 한다. 아니면 읽지 않고 멈춘다(경로는 `KTDM_LIVE_E2E_CREDENTIALS`로
  바꿀 수 있다). 대상 URL은 loopback이 아니면 https여야 한다.
- 시험 전에 브라우저로 사전 로그인(`frontend/e2e-live/login-preflight.ts`, globalSetup)을 한 번만 해 보고 세션을 바로
  회수한다. 자격이 틀리면(401)·잠겨 있으면(429)·origin이 거부되면(403) 실행 전체를 멈춘다 — 틀린 자격으로 시험마다
  실패해 운영 관리자 로그인(10분 5회)을 잠그지 않게 한다. `--list`는 사전 로그인을 하지 않는다.
- `bash -x`로 실행하면 자격을 읽기 전에 거부한다. 값은 docker argv에 싣지 않고 환경변수 이름만 넘긴다 — 다만 실행
  중에는 docker 그룹(=root 상당)이 `docker inspect`로 컨테이너 env를 볼 수 있다. Node 내려받기·`npm` 설치 단계와
  브라우저 프로세스에는 자격 env를 넘기지 않는다. Playwright 출력·요약에서는 비밀번호 값(URL·base64 인코딩 포함)을
  `***`로 가리고, 끝나면 결과 디렉터리의 글 파일(`error-context.md`·`report.json` 등)에서도 지운다. trace와 실패 시
  페이지 접근성 스냅샷(`PLAYWRIGHT_NO_COPY_PROMPT=1`)은 끄고(단언 실패가 싣는 스냅샷은 Playwright 1.65에서도 남는다 — 그래서 끝의 결과 파일 비밀값 제거가 최종 방어이고, 실행이 끊겨도 EXIT trap에서 돈다), 로그인 helper는 제출 뒤 비밀번호 칸을 비운다.
- 설정은 `frontend/playwright.live.config.ts`로 고정이고 인자는 `--list`·`--project desktop|mobile`·`--grep`·
  `--grep-invert`·`--max-failures`만 받는다. 쓰기 스펙(`*.write.live.spec.ts`)은 `KTDM_LIVE_E2E_WRITE=1`을 줄 때만
  수집한다 — 지금은 하나도 없다.
- 공식 Playwright 이미지(`frontend/package.json` 버전)에서 `.nvmrc`와 같은 Node를 SHA-256 검증해 쓰고, 컨테이너에는
  `frontend/`만 싣는다(backend·config·배포 트리의 `.env`는 보이지 않는다). `npm ci`는 install script 없이 돌고, 같은
  저장소 러너가 겹치면 차례로 돈다. docker는 `/usr/bin/docker`를 쓴다. 변경 차단은 context 단위(새 탭·popup 포함)이고
  service worker는 막는다.
- 끝에 `live_e2e_summary 통과=… 실패=… flaky=… 건너뜀=… 소요=…초`와 실패한 시험의 위치·첫 오류 줄을 낸다. 실패
  스크린샷은 `frontend/test-results/live/`(0700, gitignore)에 남는다. 종료 코드는 Playwright 것이다.

## 4. 실패 읽기

- `live 사전 로그인 실패(HTTP 401)`·`로그인 응답` 401: 자격 파일의 아이디·비밀번호가 운영과 다르다(대시보드에서 비밀번호를 바꿨는지 확인). 실패가
  5회 쌓이면 10분 동안 429다.
- `로그인 응답` 403: `KTDM_LIVE_WEB_URL`이 backend `KTDM_FRONTEND_ORIGINS`에 없는 origin이다.
- `첫 평가를 마치지 않았습니다`: 배포 뒤 5분이 지나도 수집 감시가 한 번도 평가하지 못했다.
- `수집 감시가 평가를 멈췄습니다`·`host`: [수집 감시](plane-watchdog.md) 절차로 backend 로그를 본다.
- `role 연결 수를 측정하지 못했습니다`: 점검 항목 설명(측정 실패 사유)을 그대로 읽는다.
- `읽기 전용 live 시험이 운영 변경 요청을 보내려 했습니다`: 화면이 사용자 조작 없이 변경 요청을 보냈다는 뜻이다 —
  제품 결함으로 본다.
