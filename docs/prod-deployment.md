# 운영(prod) 배포 가이드

이 문서는 `kor-travel-docker-manager`를 운영 호스트에 배포·실행하는 절차를 다룬다. **민감한 접속
정보(호스트 IP, SSH 계정, 도메인)는 이 문서에 적지 않는다.** 실제 값은 gitignore된
`docs/prod-access.local.md` / 루트 `.env` / `frontend/.env.production` 에만 둔다.

## 1. 작업 원칙

- 운영 환경에서의 모든 작업(배포, docker-manager 실행, 컨테이너 관리, 검증)은 운영 호스트에 **SSH로
  접속한 뒤** 수행한다. 로컬 WSL은 dev 환경이다. 접속 정보는 `docs/prod-access.local.md` 참고.
- dev 기본 네트워크는 host 모드(`KTDM_DOCKER_NETWORK_MODE=host`)이며 운영도 동일하게 둘 수 있다.

## 2. 소스·설정 전달

운영 호스트로 소스와 gitignore된 설정(`.env`, `frontend/.env.production`)을 전달한다. 둘 중 하나:

- **rsync**(설정까지 함께 복사, GitHub 인증 불필요):
  ```bash
  rsync -a \
    --exclude=".git/" --exclude="node_modules/" --exclude="*_venv/" --exclude=".next/" \
    --exclude=".codegraph/" --exclude="backend/logs/" --exclude="*.db" \
    ./ <user>@<prod-host>:~/kor-travel-docker-manager/
  ```
- **git clone** 후 `.env` / `frontend/.env.production` 을 별도로 안전하게 전달(scp 등).

## 3. 백엔드 (FastAPI, uvicorn :12901)

운영 호스트에 `python3-venv`가 없고 sudo가 제한될 수 있으므로, `ensurepip` 없이 venv를 만든 뒤 pip을
부트스트랩한다.

```bash
cd ~/kor-travel-docker-manager/backend
python3 -m venv --without-pip ktd_venv          # ensurepip 없이 venv 생성(sudo 불필요)
curl -fsSL https://bootstrap.pypa.io/get-pip.py -o /tmp/get-pip.py
ktd_venv/bin/python /tmp/get-pip.py               # venv에 pip 부트스트랩
ktd_venv/bin/pip install -e .                     # 런타임 의존성 설치
# 기동 (백그라운드 상주)
nohup setsid env PYTHONPATH=src ktd_venv/bin/python \
  -m uvicorn kor_travel_docker_manager.main:app --host 0.0.0.0 --port 12901 \
  > /tmp/ktdm_backend.log 2>&1 &
```

`python3-venv`를 설치할 수 있는 환경이면 `sudo apt install python3.x-venv` 후 일반 venv를 써도 된다.
백엔드는 루트 `.env`를 로드해 `KTDM_CORS_ALLOW_ORIGINS`(운영 대시보드 Origin)와
`KTDM_PROD_URL_*`(서비스별 공개 URL)을 적용한다.

## 4. 프론트엔드 (Next.js, :12905)

```bash
cd ~/kor-travel-docker-manager/frontend
npm ci
npm run build      # .env.production 의 NEXT_PUBLIC_BACKEND_URL 이 번들에 인라인됨
nohup setsid npm run start > /tmp/ktdm_frontend.log 2>&1 &   # next start -p 12905
```

`NEXT_PUBLIC_*`은 빌드 타임에 인라인되므로 운영 호스트에서 빌드해야 운영 API 주소가 반영된다.

## 5. 공개 도메인 라우팅 (네트워크 인프라 — 저장소 밖)

운영 공개 도메인은 DDNS로 공인 IP에 연결된다. 게이트웨이/리버스 프록시(또는 포트포워딩)에서 아래를
운영 호스트의 앱 포트로 라우팅해야 외부 접근이 완성된다.

| 공개 도메인 | → 운영 호스트 포트 |
|---|---|
| `manager.<domain>` (대시보드) | `:12905` |
| `manager-api.<domain>` (API) | `:12901` |

이 라우팅이 없으면 대시보드(prod 빌드)가 API(`manager-api.*`)에 닿지 못한다. 라우팅 설정은 라우터/프록시
인프라 영역이며 이 저장소 범위 밖이다.

## 6. 검증

```bash
curl -s http://127.0.0.1:12901/health                  # {"status":"healthy",...}
curl -s http://127.0.0.1:12901/api/v1/containers | head # 관리 컨테이너 상태 목록(JSON)
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:12905/   # 200
```

공개 라우팅 완료 후에는 `https://manager.<domain>` 에서 대시보드가 로드되고 컨테이너 상태가 표시되는지
확인한다.

## 7. concierge UI는 prod에서 프로덕션 빌드로 구동 (중요)

`kor-travel-concierge-ui`는 베이스 compose에서 `npm run dev`(Next dev 모드)로 정의돼 있다. dev 모드는
**원격/리버스 프록시 접속 시 HMR WebSocket 실패와 함께 hydration이 되지 않아 모든 인터랙티브 컴포넌트가
멈춘다**(드롭다운/폼이 동작하지 않음). 따라서 prod에서는 **프로덕션 빌드(`next build` + `next start`)**로
구동해야 한다.

Manager mutation은 single-file compose boundary를 강제하므로 prod 호스트의
**`docker-compose.override.yml`**, `COMPOSE_FILE`, service `extends`로 command를 바꾸지 않는다. 운영 전에는
아래 command를 canonical `docker-compose.yml`에 반영한 배포 revision을 사용한다:

```yaml
services:
  kor-travel-concierge-ui:
    command:
      - sh
      - -c
      - npm run build && npm run start -- -H 0.0.0.0 -p 12605
```

- 적용: Manager의 canonical compose revision으로
  `docker compose up -d --no-deps --force-recreate kor-travel-concierge-ui`를 실행한다. 컨테이너 시작 시
  `next build`(~1–2분) 후 `next start`로 서빙한다.
- `NEXT_PUBLIC_*`(예: `NEXT_PUBLIC_VWORLD_API_KEY`)는 prod `.env`에 있어야 빌드 시 번들에 인라인된다.
- dev HMR이 필요한 revision은 canonical compose의 command를 `npm run dev`로 명시한다. 한 manager mutation
  안에서 prod/dev 파일을 합성하지 않는다.

## 8. Map↔PinVi C6c compatible pair 배포

n150의 gitignore된 manager `.env`에는 다음 값을 모두 명시한다. 값이 없거나 mode가 맞지 않으면
`ktdctl pinvi-pair deploy`는 첫 API container를 변경하기 전에 종료한다.

```dotenv
KTDM_DEPLOYMENT_ENVIRONMENT=production
COMPOSE_PROJECT_NAME=kor-travel-prod
PINVI_ENVIRONMENT=production
KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED=true
KOR_TRAVEL_MAP_API_OPS_READ_TOKEN=<공백 없는 32자 이상 secret>
KOR_TRAVEL_MAP_API_OPS_CANCEL_TOKEN=<read와 다른 공백 없는 32자 이상 secret>
KOR_TRAVEL_MAP_API_CONTAINER_PORT=12701
PINVI_KOR_TRAVEL_MAP_ADMIN_BASE_URL=http://127.0.0.1:12701
KTDM_C6C_CONTRACT_GENERATION=c6c-ops-v1
KOR_TRAVEL_MAP_UI_ADMIN_USERNAME=<Map UI 관리자 이름>
KOR_TRAVEL_MAP_UI_ADMIN_PASSWORD_HASH=<Map UI PBKDF2 해시>
KOR_TRAVEL_MAP_UI_SESSION_SECRET=<공백 없는 32자 이상 session secret>
KTDM_C6C_MAP_UI_ADMIN_PASSWORD=<16자 이상 Map UI 관리자 비밀번호>
KTDM_C6C_PINVI_ADMIN_EMAIL=<PinVi admin email>
KTDM_C6C_PINVI_ADMIN_PASSWORD=<16자 이상 PinVi admin 비밀번호>
KTDM_C6C_CANCEL_PROBE_JOB_ID=<owned typed-failure UUID fixture>
```

production compatible-pair 계약은 Map API host bind와 PinVi의 Map base URL을 각각 정확히 `12701`과
`http://127.0.0.1:12701`로 고정한다. 두 값이 서로 일치해도 다른 포트면 첫 container mutation 전에
중단한다. 비표준 포트는 local/development에서 두 값을 함께 맞춘 경우에만 허용한다.

Map UI의 username·PBKDF2 hash·session secret은 local/production 모두 기본값 없는 `:?` 보간으로
명시해야 한다. hash는 `pbkdf2_sha256` 형식과 100,000회 이상의 반복 수를 사용하고, session secret은
32자 이상이며 위치와 종류를 불문하고 Python `str.isspace()`가 인식하는 모든 Unicode 공백 문자를
포함하지 않아야 한다. UI smoke의 평문 비밀번호
`KTDM_C6C_MAP_UI_ADMIN_PASSWORD`는 로그인 검증을 수행하는 manager process에서만 사용하며 compose
service 환경에는 주입하지 않는다. username은 비밀값이 아닌 로그인 identity이며 runtime 값과 smoke
로그인 값이 하나의 source를 공유하고 Map UI의 정확한 Env path에서 frozen 값과 같아야 한다. 같은 username
문자열이 다른 서비스의 일반 scalar에 나타나는 것은 허용하지만, 환경변수 이름
`KOR_TRAVEL_MAP_UI_ADMIN_USERNAME`을 Map UI 밖에 주입하는 것은 거부한다.

실제 secret은 화면·shell history·로그에 출력하지 않는다. `docker-compose.override.yml`을 포함한 어떤
서비스도 manager 루트 `.env`를 `env_file`로 읽으면 안 된다. Map API·PinVi API·Map UI는 base
compose의 명시 보간만 사용한다. frozen snapshot과 rollback 문서는 Map UI hash·session secret을 최초
environment snapshot에서만 해석한다. ops token·hash·session secret·평문 smoke credential 같은 confidential
값은 허용된 Env path 밖 모든 scalar 노출을 거부한다.

n150 read-only preflight에서는 일반 scalar의 username 문자열 일치를 secret 누출로 오인한 false-positive를
확인했으며 container mutation은 없었다. 위 identity/confidential 분리 반영 뒤 root 권한이 필요한 Map UI
비밀번호 회전과 cross-repo smoke·실제 로그인을 다시 수행하기 전까지 production 전환은 완료로 보지 않는다.

최초 설치에서 manifest가 없으면 capture가 같은 host lock 안에서 base dependency, Map API,
Map UI/Dagster, PinVi API, PinVi Web/Dagster 순으로 전체 토폴로지를 단계 bootstrap한다.
merged compose, canonical runtime image, UI auth, runtime secret 격리를 모두 통과한 뒤 최초 v4를
원자 기록한다. 실패하면 Map runtime 네 service와 PinVi API를 중지하고 이 capture가 새로 만든
container만 제거한다. Map dependent provenance가 없는 v1/v2/v3와 알 수 없는 manifest는
덮어쓰지 않고 거부한다.

```bash
ktdctl pinvi-pair capture --verified-compatible --build
ktdctl pinvi-pair deploy --build
```

첫 번째 명령은 manifest가 없을 때만 사용할 수 있다. `--build`는 Map·PinVi 각
build context가 exact Git worktree root이고 clean인지 검증한다. manager는 각 lowercase 40자
`HEAD`를 `KOR_TRAVEL_MAP_GIT_COMMIT`/`PINVI_SOURCE_REVISION`으로 직접 파생하고
`PINVI_BUILD_ENVIRONMENT=production`을 강제한다. 이 세 값을 `.env`에 stale 값으로 고정하지
않는다. 명시한 값이 파생값과 다르거나 두 worktree 중 하나라도 dirty이면 첫 container
mutation 전에 중단한다.

두 번째 명령은 host-wide lock 안에서
현재 active set과 필수 서비스의 running/healthy를 확인한 다음 현재 Map UI container를 inspect해
username·hash·session secret이 frozen environment와 정확히 같은지 확인하고, login→보호 화면→logout→
재차단 lifecycle을 통과한 뒤에만 다섯 runtime을 함께 중지한다. 이후 Map API, signed 권한
smoke, Map UI·Dagster web·daemon, PinVi API, UI auth를 단계 실행한다. build/recreate는 `--no-deps`로
다섯 runtime에 적용한다. 다섯 candidate image는 runtime `up`과 분리된 Compose build 단계에서 먼저 만들고, 기존 container를
중지·재생성하기 전에 immutable image ID와 `org.opencontainers.image.revision`을 파생한
`HEAD`와 비교한다. build context는 live checkout이 아니라 각 `HEAD`의 일회성 Git archive라서
build 도중 변경·원복한 파일과 ignored 파일은 image input이 될 수 없다. PinVi는
`io.pinvi.build.environment=production`도 함께 확인한다. raw/resolved build mapping은 이
snapshot context와 저장소 내부의 지정 Dockerfile, exact provenance arg만 허용하며 external
Dockerfile·additional context·secret·target은 거부한다. Map
read 200 envelope·무토큰 401·존재하지 않는 import-job cancel 404·cancel token의 non-cancel mutation
403 가운데 하나라도 다르면 새 pair를 활성화하지 않는다. PinVi owned cancel fixture는 정확한
409 `PIPELINE_CANCELLATION_IN_PROGRESS`, 502 `DAGSTER_TERMINATE_FAILED`,
503 `DAGSTER_UNAVAILABLE`와 canonical details/retryability/양의 `Retry-After`만 허용하고 429·generic 오류는
거부한다. details의 canonical import root ID도 `KTDM_C6C_CANCEL_PROBE_JOB_ID`와 같아야 한다. 모든 중간 실패는 시작 시점 active pair를
복구해 전체 계약을 재검사하며, 복구도 실패하면 다섯 runtime을 중지하고 operator 조치를 요구한다. 최종
readiness는 `ps --all` 존재 여부가 아니라 모든 필수 service의 running/healthy 상태를 요구한다. runtime
inspect는 실제 값을 출력하지 않고 `.Config` 전체의 안전 scalar를 순회해 ops token이 두 API의 정확한
Env path에만, Map UI username Env 이름과 exact 값이 Map UI의 정확한 path에 있는지 검사한다. hash·session
secret과 평문 UI smoke 비밀번호가 허용 path 밖 어떤 container scalar에도 존재하면 실패한다.

owned cancel POST는 deploy/bootstrap/rollback 각 transaction에서 정확히 한 번만 수행한다. 첫 typed 결과를
final verification과 같은 transaction의 recovery에 재사용하고, 응답 유실이나 DTO 불일치로 결과가
불확실하면 두 번째 POST 없이 fail-close한다. full detail은 attempt/member/Dagster run lifecycle과 structured
error/timestamp/commit 보존 경고를 모두 검사한다. attempt 생성 전 canonical 409는 exact root와
`cancellation: null`만 허용한다. Map 권한 smoke도 tokenless read, cancel-token read, read-token cancel,
cancel-token schedule mutation의 HTTP status와 RFC7807 code를 함께 검증한다.

full 409의 unresolved count는 음수가 아닌 exact `pending|cancel_failed` member 개수다. root member 자체가
resolved여도 child가 unresolved일 수 있고, CAS 전이 중 모든 member가 잠시 resolved되어 count가 0일 수도
있다. retryable detail은 모든 `cancel_failed` member가 matching Dagster run의 exact `cancel_failed`와
retryable error를 가져야 하며 `already_terminal` 대체 증거를 허용하지 않는다. in-progress의 definitive
tracking drift는 member `cancel_failed`와 run `cancelled` 조합을 허용한다.
runless in-progress 실패는 definitive code만 허용하고, 실패 snapshot이 남은 run-backed member/run은
retryable/definitive policy group이 일치해야 한다. resolved run-backed 상태는 member의
`cancelled|done|failed`를 Dagster `CANCELED|SUCCESS|FAILURE`에 정확히 대응한다. feature-load root의
failed/SUCCESS tracking 예외는 동일 run의 `provider_feature_load` child 성공 추적 증거가 있을 때만 허용한다.
이 definitive shape는 `409 PIPELINE_CANCELLATION_UNSAFE`+`failed`, termination timeout은
`503 DAGSTER_TERMINATION_TIMEOUT`+`retryable` pair로 보존하며, non-retryable 409에는
`Retry-After`를 요구하거나 허용하지 않는다.

failed detail은 definitive top error를 유지하면서 exact run-backed retryable member/run과 definitive
mismatch member를 함께 보존할 수 있다. `status`별 `finished_at`/`error`, retry lineage, frozen termination
flag, engine timestamp lifecycle이 DB 정본과 다르면 배포를 중단한다. `Retry-After`가 존재하지만 garbage·0·
음수이거나 ASCII decimal 1..300 범위를 벗어난 경우도 “헤더 없음”으로 취급하지 않고 실패한다. `+5`,
앞뒤 공백, Unicode digit, 301도 거부한다. Compose `kill`의 signal option
값은 service에서 제외하며 service-less/project-wide 또는 unknown option scope는 다섯 runtime을 포함한다고 본다.
`build --pull`, `run --rm`, `rm -s/--stop`은 command별 boolean flag로 해석한다. `compose config`의
`-o/--output` 분리·inline·누락 형식은 모두 write-capable mutation으로 분류해 host lock과 전용 capability를
요구하며, `--format json` 등 명시한 read-only option만 lock 없이 허용한다. bootstrap created cleanup이나
stopped-service 복원에서 예외가 나면 배포 응답은 operator-required 상태로 수렴한다.

일반 non-API config update/reset/create도 파일 쓰기와 recreate 전에 candidate compose 전체를 검사한다.
Map runtime 네 service와 PinVi API의 exact build mapping 외 위치에서 보호 이름·현재 값을
environment, label, command, build arg로
참조하거나 non-root `env_file`에 alias로 넣으면 typed 409로 거부하고 파일/container 불변을 보장한다.

rollback은 단일 image/tag를 받지 않으며 manifest의 immutable pair만 복원한다.

```bash
ktdctl pinvi-pair rollback
```

기본 manifest와 mode 0600 lock은
`~/.local/state/kor-travel-docker-manager/<COMPOSE_PROJECT_NAME>/`의 고정
`compatible-pair-v4.json`/`deployment.lock`에 함께 저장한다. production은
root·manifest·lock path override를
모두 거부하므로 동일 Compose project가 다른 host lock을 선택할 수 없다. manifest version은 정확한 integer,
active/rollback은 각각 Map runtime 네 image ID, `map_source_revision`, `pinvi_image_id`,
`pinvi_source_revision`, `contract_generation`, `recorded_at` exact 9개 필드만 갖는다.
`recorded_at`은 offset ISO 8601이어야 하며 parent fsync 실패 시 이전 snapshot을 원자 복원한다. manifest와
capture/deploy/rollback은 같은 filesystem lock과 contract generation을 사용한다.
rollback은 다섯 image environment override의 canonical single-file contract를 stop 전에 검증하고
Map API 복원·signed smoke와 Map dependent exact revision 검증 뒤 PinVi를 복원한다. 이후 Map/PinVi canonical 조회,
Map UI 로그인·보호 화면·로그아웃, PinVi Web login shell과 runtime 격리가 모두 통과해야 commit한다.
