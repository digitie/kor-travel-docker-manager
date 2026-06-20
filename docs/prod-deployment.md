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
