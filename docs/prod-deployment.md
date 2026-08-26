# 운영(prod) 배포 가이드

이 문서는 `kor-travel-docker-manager`를 운영 호스트에 배포·실행하는 절차를 다룬다. **민감한 접속
정보(호스트 IP, SSH 계정, 도메인)는 이 문서에 적지 않는다.** 실제 값은 gitignore된
`docs/prod-access.local.md` / 루트 `.env` / `frontend/.env.production` 에만 둔다.

## 1. 작업 원칙

- 운영 환경에서의 모든 작업(배포, docker-manager 실행, 컨테이너 관리, 검증)은 운영 호스트에 **SSH로
  접속한 뒤** 수행한다. 로컬 WSL은 dev 환경이다. 접속 정보는 `docs/prod-access.local.md` 참고.
- dev 기본 네트워크는 host 모드(`KTDM_DOCKER_NETWORK_MODE=host`)이며 운영도 동일하게 둘 수 있다.

## 2. 소스·설정 전달

운영 호스트에는 추적된 런타임 소스만 전달한다. `.env`, `frontend/.env.production`,
`frontend/.env.local`, `docker-compose.override.yml`, `*.local.md`와 같은 운영·민감 파일은
rsync 대상에 포함하지 않는다. 비밀 설정은 운영 호스트에서 별도로 안전하게 준비한다. 둘 중 하나:

- **rsync**(소스 디렉터리만 복사, GitHub 인증 불필요):
  ```bash
  rsync -az backend/src/ \
    <user>@<prod-host>:~/kor-travel-docker-manager/backend/src/
  rsync -az frontend/src/ \
    <user>@<prod-host>:~/kor-travel-docker-manager/frontend/src/
  ```
- **git clone** 후 운영 호스트의 추적 파일을 갱신하고 `.env` / `frontend/.env.production`을
  별도로 안전하게 준비한다. `--delete`와 저장소 루트 전체 동기화는 사용하지 않는다. 신규
  설치는 아래 trusted installer를 우선하고, 이 rsync 절차는 기존 rsync 배포본을 갱신할 때만 쓴다.

## 3. 신뢰된 운영 설치와 백엔드 (FastAPI, uvicorn :12901)

운영 설치는 외부 `get-pip.py`와 비고정 `pip install -e .`를 사용하지 않는다. 먼저 운영 호스트
밖에서 원하는 **머지된 commit의 clean git checkout**을 준비하고, 운영 호스트에는 root 소유·권한
제한된 오프라인 wheelhouse를 준비한다. 그 뒤 저장소의 trusted installer가 archive·wheelhouse
무결성·`.env` 권한을 확인하고 `/opt/kor-travel-docker-manager`에 설치한다.

```bash
# SOURCE_ROOT는 non-root 소유의 clean checkout이며 정확한 머지 commit을 가리켜야 한다.
git -C <SOURCE_ROOT> status --porcelain=v1       # 빈 출력이어야 함
sudo -n /usr/bin/bash <SOURCE_ROOT>/scripts/install-ktdm-trusted-release \
  --env-file /opt/kor-travel-docker-manager/.env \
  --wheelhouse /var/lib/kor-travel-docker-manager/wheelhouse \
  <SOURCE_ROOT>
```

installer는 `--no-index` wheelhouse에서 `backend/.venv`를 만들고 `ktdctl`을 설치한다. `.env`는
installer가 새로 전달하지 않으며 운영 호스트에서 별도로 준비한 canonical 파일을 사용한다. 백엔드는
그 루트 `.env`를 로드해 `KTDM_CORS_ALLOW_ORIGINS`와 `KTDM_PROD_URL_*`를 적용한다.

```bash
cd /opt/kor-travel-docker-manager/backend
nohup setsid env PYTHONPATH=src .venv/bin/python \
  -m uvicorn kor_travel_docker_manager.main:app --host 0.0.0.0 --port 12901 \
  > /tmp/ktdm_backend.log 2>&1 &
```

## 4. 프론트엔드 (Next.js, :12905)

```bash
cd /opt/kor-travel-docker-manager/frontend
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
curl -s -o /dev/null -w '%{http_code}\n' \
  -H 'Origin: https://manager.<domain>' \
  http://127.0.0.1:12901/api/v1/containers # 허용 Origin이지만 인증 없으면 401
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:12905/   # 200
```

공개 라우팅 완료 후에는 브라우저에서 `https://manager.<domain>`에 접속해 관리자 로그인 → 대시보드와
컨테이너 상태 표시 → 로그아웃 → 로그인 화면 전환을 확인한다. 로그아웃 뒤 WebSocket 재연결 루프가
없는지도 확인한다. API curl은 인증 없는 경계 확인용이며, 인증된 컨테이너 목록 검증을 대신하지 않는다.

## 7. concierge UI는 prod에서 프로덕션 빌드로 구동 (중요)

`kor-travel-concierge-ui`는 canonical Compose에서 프로덕션 빌드(`next build` + `next start`)로만 구동한다.
dev 모드는 원격/리버스 프록시 접속에서 HMR WebSocket 실패와 hydration 정지를 만들 수 있어 운영 경계에서
허용하지 않는다. 컨테이너 시작은 password hash·session secret·proxy secret이 비었거나 secret 길이가 짧으면
fail-close하고, 통과할 때만 build 뒤 `next start`로 전환한다.

Manager의 C6c는 raw Compose와 resolved Compose 양쪽에서 다음을 강제한다.

- `kor-travel-concierge-ui`에 `env_file`이 없고, UI environment가 정확한 allowlist다. provider/LLM/search 키가
  섞인 Concierge 전체 `.env`는 browser-facing UI process에 전달하지 않는다.
- BFF `BACKEND_ORIGIN`은 canonical loopback API 주소에 고정하며 public API base는 빈 same-origin BFF다.
- API와 UI의 `KTC_ADMIN_PROXY_SECRET`은 단 하나의 Manager root source
  `KOR_TRAVEL_CONCIERGE_UI_ADMIN_PROXY_SECRET`를 같이 사용한다.
- UI credential source는 Manager root `.env`의 `KOR_TRAVEL_CONCIERGE_UI_*`와
  `KOR_TRAVEL_CONCIERGE_BACKEND_API_KEY`뿐이다. backend key는
  `KOR_TRAVEL_CONCIERGE_API_KEYS` comma-list의 exact member여야 한다. browser에 필요한 지도 키는 전용
  `KOR_TRAVEL_CONCIERGE_UI_VWORLD_SERVICE_KEY`로 분리한다.
- API의 `KOR_TRAVEL_CONCIERGE_APP_ENV=production` 및
  `KOR_TRAVEL_CONCIERGE_API_AUTH_ENABLED=true`도 root authority로 이관한다. source `.env`가 이 둘 중 하나를
  local/false로 주면 이관 명령은 API가 unauthenticated default로 내려가는 것을 막기 위해 중단한다.

남아 있는 legacy `docker-compose.override.yml`가 있다면 수동 `docker compose` 명령이나 삭제를 하지 않는다.
Manager의 canonical release 배포 뒤 root에서 아래 공식 경로를 한 번 실행한다.

```bash
sudo -n /opt/kor-travel-docker-manager/backend/.venv/bin/ktdctl \
  compose-boundary retire-legacy-override --confirm
```

첫 명령은 root-only로 알려진 Geo backup 값과 Concierge UI source만 raw 파싱하고, 값 충돌·symlink·비정규 파일·
잘못된 API key membership을 fail-close한다. candidate root `.env`를 원자적으로 갱신한 뒤 canonical Compose를
출력 없이 raw/resolved C6c 경계까지 검증하고, 성공한 경우에만 override를 owner-only archive로 rename한다. 같은
C6c global mutation lock을 계속 보유한 채 API/MCP/scheduler/UI 정확한 네 service만 canonical single-file source로
force-recreate한다. production의 일반 `ensure`는 허용되지 않으므로 이 단계에 사용하지 않는다. archive 뒤 재생성이
실패하면 root `.env`와 archive는 의도적으로 유지된다. 원인을 해소한 뒤 아래 Manager retry만 사용한다.

```bash
sudo -n /opt/kor-travel-docker-manager/backend/.venv/bin/ktdctl \
  compose-boundary activate-concierge --confirm
```

retry 역시 legacy override가 없는지와 raw/resolved C6c 경계를 먼저 다시 확인하며, 수동 `docker compose`·override
restore·일반 `ensure`로 대체하지 않는다. 성공 뒤 실제 공개 브라우저에서 Concierge 로그인→BFF 동작→로그아웃을 검증한다.

## 8. F1D pinned runtime generation v6/journal v8 재구축

> 현재 rebuild protocol은 이전 compatible-pair, cache-target, standalone DB backup mutation과 Map UI 회전의
> **공개 CLI 운영 경로**를 모두 퇴역시켰다. 과거 v1–v4 manifest·journal·backup은 실행 근거가 아니며,
> 아래 비운영 `rebuild-pinned --confirm`만 새 generation을 만드는 정본이다.

### 8.1 비운영 pinned runtime generation 재구축

실제 운영 환경에는 이 절을 적용하지 않는다. typed 환경 pair
`KTDM_DEPLOYMENT_ENVIRONMENT=rehearsal` 및 `KTDM_DEPLOYMENT_LIFECYCLE=rebuildable`를 frozen canonical
environment에서 함께 명시한 비운영 환경만 다음 command를 실행할 수 있다. `local/development`,
`rehearsal/rebuildable`, `production/operational` 외 조합과 production 환경에 flag만 추가한 조합은 모두
mutation 전에 거부한다.

```bash
sudo -n /opt/kor-travel-docker-manager/backend/.venv/bin/ktdctl \
  pinvi-pair rebuild-pinned --confirm
```

이 command는 추적된 exact Map·PinVi commit만 Git archive build source로 쓰며 `.env` checkout HEAD,
old image, old manifest를 candidate authority로 쓰지 않는다.
먼저 Map 네 service와 PinVi 세 service의 immutable candidate image ID, source revision, Map application/Dagster와
PinVi의 expected schema head를 owner-only journal에 고정한다. Map Dagster head는 source pin의 추정값이 아니라
candidate Dagster image의 head-inspection command 출력으로 attest한다. candidate artifact 하나라도 없으면 database를
건드리지 않는다.

후속 phase에서만 Manager가 frozen resolved Compose의 Map application·Map Dagster·PinVi database identity를
검증해 세 database를 새로 만든다. reset 직후 PinVi DB identity를 v8 journal에 기록하고, committed resume은
Map application·Dagster metadata·PinVi 세 DB identity와 두 PostgreSQL container image를 다시 실측한다.
Dagster metadata LOGIN role은 privilege/membership, connection limit, password expiry와 role/database-local
setting 잔여가 canonical해야 permit을 발행한다. Map API entrypoint와 Map Dagster migration-only command가 candidate-attested
각 head까지 migration을 적용·검증한다. Map Dagster command는 `dagster instance migrate` 후 strict single-row
`public.alembic_version`을 같은 candidate image의 reported head와 대조한다. PinVi migration+admin credential-file one-shot CLI가 `pinvi_head`까지
적용한 뒤 일곱 runtime을 같은 generation으로 기동한다. Map·PinVi Web·PinVi Dagster와 durable journal/log에는
credential을 전달하거나 기록하지 않는다. F1J fixture smoke, authenticated UI contract, schema/image attestation이
모두 성공하면 single active v6 generation manifest와 pinset별 v8 journal을 commit한다. 실패 뒤 재개할 때는
durable phase와 exact DB/operation receipt만 사용하며 `databases_recreated` 이후 세 DB를 자동 reset하지 않는다.
candidate runtime을 모두 중지한다. source/ETL 재적재는 committed
뒤 별도 workflow다.
