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

### 2.1 runtime pin registry는 배포 트리 밖에 둔다 (설치 전 필수 준비)

Map·PinVi pinned revision은 소스코드 상수가 아니라 root 소유 JSON registry 파일에
있다(`docs/docker-management.md` 5.1의 `ktdctl pin` 절). trusted installer는
`/opt/kor-travel-docker-manager` 트리를 staging→commit으로 **통째 교체**하므로,
registry가 트리 안에 있으면 다음 release 설치가 회전 결과를 덮어쓴다.

설치 root(`/opt/kor-travel-docker-manager`)에서 실행하면 env가 없어도 기본값이 자동으로
트리 밖을 가리키므로 별도 설정이 필요 없다. 다른 경로를 쓰려면 두 env로 덮어쓰되,
백엔드와 CLI 양쪽 환경에 동일하게 넣어야 한다(값이 다르면 조회 API가 stale을 본다).

```bash
# 설치 root에서 도는 경우의 기본값(별도 설정 불필요)
/var/lib/kor-travel-docker-manager/runtime-pins.json          # registry, root 0600
/var/lib/kor-travel-docker-manager-public/runtime-pins.json   # 공개 사본, 0644

# 다른 경로를 쓰려면
KTDM_RUNTIME_PINS_FILE=<배포 트리 밖 경로>
KTDM_RUNTIME_PINS_PUBLIC_FILE=<배포 트리 밖, 비-root가 읽을 수 있는 경로>
```

**회전 요청 디렉터리**(대시보드가 요청을 남기는 자리)는 installer가
`/var/lib/kor-travel-docker-manager-requests`를 `root:root 0700`으로 만든다. backend를
비-root로 돌리는 호스트에서는 설치 후 소유자를 그 사용자로 바꾼다:

```bash
sudo chown <backend-user>:<backend-group> /var/lib/kor-travel-docker-manager-requests
```

**group-writable로 만들지 않는다**(`0770` 등). 무결성 검사가 그 디렉터리를 영구히
거부해 회전 요청 경로 전체가 잠긴다.

**공개 사본은 별도 트리에 둔다.** installer가 `/var/lib/kor-travel-docker-manager`를 매
설치마다 `0700 root:root`로 되돌리므로, 그 안에 사본을 두면 비-root 백엔드가 traverse조차
하지 못해 조회 API가 영구히 `unknown`이 된다(n150 실측). 사본에는 공개 저장소 커밋 SHA와
회전 메타뿐이라 비밀이 없다.

배포 트리 안(`/opt/kor-travel-docker-manager/...`) 경로로는 회전이 **거부된다** — 다음
release 설치가 회전 결과를 조용히 되돌리기 때문이다.

- registry 본체는 root `0600`이어야 한다(그룹·타인 접근 가능하면 회전이 거부된다).
  공개 사본은 비-root backend가 읽어야 하므로 `0644`이며 secret을 담지 않는다.
- 최초 1회만 저장소의 개발 기본값을 seed로 부트스트랩한다. 이후 M05 pair 회전은
  `pin rotate-pair`다.
  ```bash
  cd /opt/kor-travel-docker-manager
  sudo -n backend/.venv/bin/ktdctl pin init --confirm   # 기본 seed: config/runtime-pins.seed.json
  sudo -n backend/.venv/bin/ktdctl pin show
  sudo -n backend/.venv/bin/ktdctl pin verify
  ```
- **`pin verify`는 현재 pinset이 재시도 금지 상태이면 비정상 종료한다.** digest가 맞아도
  그 상태에서는 `rebuild-pinned`가 거부되므로, verify가 0을 반환할 때만 재구축을 시작한다.
- **백업·보존 대상**: 위 두 파일과 같은 디렉터리의 `runtime-pins.<digest>.json`
  보존본(= 회전 이력이자 `pin rollback`의 유일한 소스). git 밖에 있으므로 이 디렉터리가
  유실되면 롤백 소스도 함께 유실된다.
- registry가 없으면 `rebuild-pinned`와 pin 조회는 fail-close하고, 조회 API는 값을
  추측하지 않고 `unknown`을 표시한다. 그 외 target 관리·컨테이너 제어·백업 조회 등
  나머지 기능은 영향받지 않는다(검증 완료).
- registry 파일은 root 소유 `0600`, 공개 사본은 `0644`여야 하며 group/other 쓰기 권한이
  있으면 읽기 자체가 거부된다. `config/runtime-pins.seed.json`은 추적되는 **읽기 전용
  seed**이며 회전 대상이 아니다.
- **seed도 root 소유여야 한다.** root가 사용자 소유 파일을 신뢰 입력으로 읽지 않기
  때문이다. trusted installer는 트리 전체를 `root:root`로 설치하므로 정상 경로에서는
  자동으로 만족한다. 수동으로 seed를 옮겨 왔다면 `install -o root -g root -m 0644`로
  배치한다(사용자 소유 seed는 `pin init`이 거부한다 — n150 실측).

#### 재시도 금지(terminal) pinset과 재구축 선행 절차

`rebuild-pinned`는 registry가 terminal로 등재한 pinset에 대해 **어떤 mutation보다 먼저**
거부한다. 2026-08-28 기준 동봉 seed는 현재 pinset을 terminal로 등재하고 있다(근거: PinVi
`docs/journal.md` 2026-08-27 — 해당 candidate는 역사 증거로 보존하며 재시도하지 않는다).
따라서 새 재구축은 **회전이 선행되어야 한다.**

```bash
cd /opt/kor-travel-docker-manager
sudo -n backend/.venv/bin/ktdctl pin show          # 현재 상태와 차단 여부 확인
sudo -n backend/.venv/bin/ktdctl pin rotate-pair \
  --map-revision <Map 40-hex 커밋> --pinvi-revision <PinVi 40-hex 커밋> \
  --reason "<직전 candidate의 terminal 사유와 그것을 고친 revision>" \
  --block-previous --confirm
sudo -n backend/.venv/bin/ktdctl pin verify        # 0이면 재구축 가능
sudo -n backend/.venv/bin/ktdctl pinvi-pair rebuild-pinned --confirm
```

M05 source pair는 role별 `pin rotate` 두 번으로 바꾸지 않는다. 첫 write가 intermediate pinset을
만들면 source pair 검증 실패가 one-shot ledger를 소비할 수 있으므로 `pin rotate-pair`만 사용한다.
`--reason`은 world-readable 공개 사본과 조회 API에 그대로 기록되므로 비밀을 적지 않는다.
`--block-previous`는 직전 pinset을 terminal로 등재해 재시도를 영구 차단한다 — 회전 사유가
"직전 candidate가 실패로 끝났다"인 경우의 표준 사용법이다. 의도적으로 `pin unblock`은
제공하지 않는다.

## 3. 신뢰된 운영 설치와 백엔드 (FastAPI, uvicorn :12901)

운영 설치는 외부 `get-pip.py`와 비고정 `pip install -e .`를 사용하지 않는다. 먼저 운영 호스트
밖에서 원하는 **머지된 commit의 clean git checkout**을 준비하고, 운영 호스트에는 root 소유·권한
제한된 오프라인 wheelhouse를 준비한다. 그 뒤 저장소의 trusted installer가 archive·wheelhouse
무결성·`.env` 권한을 확인하고 `/opt/kor-travel-docker-manager`에 설치한다.

### 3.1 Debian `poetry-core` build dependency를 포함한 wheelhouse 발행

trusted installer는 source의 backend wheel을 먼저 오프라인 build하므로, wheelhouse에는 runtime
wheel뿐 아니라 build backend인 `poetry-core`도 있어야 한다. 운영 호스트에 이미 root-owned
`/opt/kor-travel-docker-manager/.wheelhouse`가 있고 target destination이 아직 없을 때만 destination을
**한 번** 발행한다. 이 최초 bootstrap에서는 user-owned source checkout의 파일을 `sudo`가 직접
실행해서는 안 된다. 먼저 root operator가 out-of-band release attestation으로 exact merged commit과
각 tool의 SHA-256을 승인하고, 아래처럼 exact Git blob을 root-owned temporary file로 복사·hash 검증한
뒤에만 실행한다.

```bash
# 아래 세 값은 release attestation에서 얻는다. working tree나 같은 clone에서 계산하지 않는다.
SOURCE_ROOT=<absolute-clean-checkout>
SOURCE_COMMIT=<exact-40-hex-merged-commit>
PROVISION_SCRIPT_SHA256=<attested-sha256-of-provision-script>
INSTALLER_SCRIPT_SHA256=<attested-sha256-of-installer-script>
ROOT_STAGE_PARENT=/var/lib/kor-travel-docker-manager/trusted-tool-bootstrap
WHEELHOUSE_DESTINATION="/var/lib/kor-travel-docker-manager/wheelhouse-${SOURCE_COMMIT:0:12}"

set -euo pipefail
test "$(/usr/bin/git -C "${SOURCE_ROOT}" rev-parse HEAD)" = "${SOURCE_COMMIT}"
test -z "$(/usr/bin/git -C "${SOURCE_ROOT}" status --porcelain=v1)"
sudo -n /usr/bin/install -d -o root -g root -m 0700 "${ROOT_STAGE_PARENT}"
test "$(sudo -n /usr/bin/stat -c '%u:%a:%F' "${ROOT_STAGE_PARENT}")" = '0:700:directory'
test ! -e "${WHEELHOUSE_DESTINATION}"

stage_merged_tool() {
  local relative_path="$1"
  local expected_sha256="$2"
  local stage actual
  stage="$(sudo -n /usr/bin/mktemp -p "${ROOT_STAGE_PARENT}" '.ktdm-tool.XXXXXXXX.py')"
  if ! env -i PATH=/usr/bin:/bin LANG=C.UTF-8 LC_ALL=C.UTF-8 \
    GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null \
    /usr/bin/git --no-replace-objects -C "${SOURCE_ROOT}" --no-pager \
      show --no-textconv "${SOURCE_COMMIT}:${relative_path}" \
    | sudo -n /usr/bin/tee "${stage}" >/dev/null; then
    sudo -n /usr/bin/rm -f -- "${stage}"
    return 1
  fi
  if ! sudo -n /usr/bin/chown root:root "${stage}" \
    || ! sudo -n /usr/bin/chmod 0600 "${stage}" \
    || ! test "$(sudo -n /usr/bin/stat -c '%u:%a:%h:%F' "${stage}")" = '0:600:1:regular file'; then
    sudo -n /usr/bin/rm -f -- "${stage}"
    return 1
  fi
  actual="$(sudo -n /usr/bin/sha256sum "${stage}" | /usr/bin/awk '{print $1}')"
  if [[ "${actual}" != "${expected_sha256}" ]]; then
    sudo -n /usr/bin/rm -f -- "${stage}"
    return 1
  fi
  printf '%s\n' "${stage}"
}

STAGED_PROVISION=''
STAGED_INSTALLER=''
cleanup_staged_tools() {
  [[ -z "${STAGED_PROVISION}" ]] || sudo -n /usr/bin/rm -f -- "${STAGED_PROVISION}"
  [[ -z "${STAGED_INSTALLER}" ]] || sudo -n /usr/bin/rm -f -- "${STAGED_INSTALLER}"
}
trap cleanup_staged_tools EXIT
STAGED_PROVISION="$(stage_merged_tool \
  scripts/provision-ktdm-offline-wheelhouse.py "${PROVISION_SCRIPT_SHA256}")"
STAGED_INSTALLER="$(stage_merged_tool \
  scripts/install-ktdm-trusted-release "${INSTALLER_SCRIPT_SHA256}")"

sudo -n /usr/bin/env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin LANG=C.UTF-8 LC_ALL=C.UTF-8 \
  /usr/bin/python3 -I -S "${STAGED_PROVISION}" \
  --source-wheelhouse /opt/kor-travel-docker-manager/.wheelhouse \
  --destination-wheelhouse "${WHEELHOUSE_DESTINATION}"
```

이 도구는 network·PyPI·user-writable 입력을 사용하지 않는다. source wheel과 모든 ancestor가
root-owned/non-writable인지, Debian `python3-poetry-core`가 설치됐고 `dpkg --verify`가 깨끗한지를
확인한 뒤 설치된 Debian package에서 pure-Python `poetry_core-<version>-py3-none-any.whl`를 만든다.
발행 directory에는 source wheel SHA와 생성 wheel SHA만 담은 비밀 비포함 provenance manifest가 함께
생기며, temporary directory를 fsync한 뒤 atomic publish한다. 이 manifest는 발행 시점의 audit record이며
installer의 wheel snapshot 자체를 대체하지 않는다. 기존 destination을 덮어쓰지 않고, crash 뒤 남은
`.wheelhouse.stage.*` 또는 이미 있는 destination이 있으면 자동 삭제·재발행하지 않고 중단해 조사한다.
source wheelhouse에 filename·version·대소문자가 무엇이든 `poetry-core` candidate가 이미 있으면 Debian
provenance wheel과 pip 선택이 섞이지 않도록 역시 중단한다.

기본 `/var/lib/kor-travel-docker-manager/wheelhouse`가 다른 release·operator의 artifact로 이미 존재하고
같은 issuance provenance를 증명할 수 없다면, 이를 삭제·수정·채택하지 않는다. 위 예시처럼 exact merged
commit을 포함한 새 root-owned destination을 선택하고, provisioner와 installer에 같은 explicit path를
전달한다. 이 경로도 pre-existing이면 새 이름을 정해 다시 root operator attestation을 받아야 한다.

wheel을 인터넷에서 내려받거나, home/user-writable 경로에서 복사하거나, `pip install`로 wheelhouse를
수정해서는 안 된다. `dpkg --verify` 실패, package metadata 불일치, source/destination 권한 drift도
모두 installer 재시도보다 먼저 해결해야 할 fail-close 조건이다.

```bash
# 위에서 hash 대조해 root staging한 installer만 실행한다. source checkout은 code 실행 입력이 아니다.
sudo -n /usr/bin/env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin LANG=C.UTF-8 LC_ALL=C.UTF-8 \
  /usr/bin/bash "${STAGED_INSTALLER}" \
  --env-file /opt/kor-travel-docker-manager/.env \
  --wheelhouse "${WHEELHOUSE_DESTINATION}" \
  --expected-source-revision "${SOURCE_COMMIT}" \
  "${SOURCE_ROOT}"
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

### 3.x 백업 산출물을 UI와 cron이 공유할 때 (선택)

`POST /api/v1/backups/{role}`이 생기면서 백업을 만드는 주체가 UI와 cron 둘이 된다. 두
주체가 서로의 산출물을 읽고 지우려면 **디렉터리** 쓰기 권한이 필요하다(unlink는 파일이
아니라 디렉터리 권한이다).

```bash
sudo groupadd ktdm-backup
sudo usermod -aG ktdm-backup <backend-user>
sudo usermod -aG ktdm-backup <cron-user>
sudo chgrp -R ktdm-backup "$KTDM_BACKUP_ROOT"
# 디렉터리만 2770. `-R 2770`은 dump 파일까지 group-writable로 만들어 0640 정책과 어긋난다.
sudo find "$KTDM_BACKUP_ROOT" -type d -exec chmod 2770 {} +
sudo find "$KTDM_BACKUP_ROOT" -type f -exec chmod 0640 {} +
# .env에 그룹 이름을 선언한다.
#   KTDM_BACKUP_SHARED_GROUP=ktdm-backup
```

**보조 그룹 변경은 backend 프로세스를 재기동해야 반영된다.** 선언하지 않으면 기존
`0700`/`0600` 그대로이고, 전제가 깨져 있으면 백업이 시작되지 않고 복구 명령과 함께
거부한다.

### 3.y 관리자 비밀번호 변경

이제 대시보드의 "인증 및 공개 API 키" 패널에서 바꾼다. `.env`의
`KTDM_ADMIN_PASSWORD_HASH` **한 줄만** 다시 쓰고, 재기동 없이 즉시 적용되며 진행 중인
세션은 끊기지 않는다.

> **미종결 pinned rebuild journal이 있는 동안 비밀번호를 바꾸면 그 rebuild의 재개가
> 영구 차단된다.** resume이 journal의 `environment_sha256`을 현재 `.env` 바이트와
> 대조하기 때문이다. 이 위험은 손으로 `.env`를 고칠 때도 똑같이 있었고 UI가 만든 것이
> 아니다 — 다만 이제는 화면이 먼저 막는다.

backend가 journal을 **항상 볼 수 있는 것은 아니다.** journal은 `rebuild-pinned`를 실행한
프로세스의 `$HOME` 아래 `0700` 디렉터리에 있고 그 명령은 root를 요구한다. 확인할 수
없으면 API가 `unverifiable`을 반환하고, 화면은 명시 문구 입력을 요구한다. 그때 운영자가
SSH에서 확인할 것:

```bash
sudo ls -l ~root/.local/state/kor-travel-docker-manager/<COMPOSE_PROJECT_NAME>/pinned-runtime-rebuild-v8-*.json
```

`.env`가 root `0600`인데 backend가 비-root로 돌면 이 기능은 `ENV_NOT_WRITABLE`로
거부한다. **권한을 완화하지 마라** — 그 권한이 이 파일의 유일한 보호다. backend를 해당
소유자 권한으로 재기동하거나 SSH에서 해시를 직접 교체한다.

### 3.z host mutation lease 디렉터리는 부팅 시점에 만든다

`KTDM_DEPLOYMENT_ENVIRONMENT=production`에서 **모든** Compose mutation은
`/run/lock/kor-travel-docker-manager/global-mutation.lock` 하나를 지난다
(`c6c_global_mutation_lock_path`). 비운영에서는 이 lease가 `$HOME/.local/state/...`로 간다.

**단, rebuild lease는 환경과 무관하다.** `pinned_runtime_rebuild_lock_path()`는 조건 없이
`/run/lock/kor-travel-docker-manager/pinned-runtime-rebuild.lock`을 반환한다. rehearsal
호스트도 이 디렉터리를 쓰므로 이 절은 비운영 호스트에도 적용된다.

Debian 계열의 `/run/lock`은 `1777` sticky다(n150 실측 `drwxrwxrwt root:root`). Manager가
런타임에 이 디렉터리를 처음 만드는 구조라면, 재부팅 직후 비특권 로컬 사용자가 같은 이름을
선점할 수 있다. 그러면 소유자·mode 검증이 실패해 **모든 컨테이너 mutation이 다음
재부팅까지 거부**되고, sticky bit 때문에 정리도 root만 할 수 있다. `/run/lock`은 tmpfs라
디렉터리가 재부팅마다 사라지므로, 이 창은 부팅할 때마다 다시 열린다.

**이 창은 코드로 닫히지 않는다.** 런타임 검증을 아무리 조여도 "먼저 만든 쪽이 이긴다"는
성질은 남는다. 부팅 시점에 이미 존재하게 만드는 것만이 닫는다.

`scripts/install-ktdm-trusted-release`가 설치 말미에 아래를 자동으로 수행한다. 사람이
기억해야 하는 절차가 아니다.

```bash
install -o root -g root -m 0644 \
  /opt/kor-travel-docker-manager/deploy/tmpfiles.d/kor-travel-docker-manager.conf \
  /usr/lib/tmpfiles.d/kor-travel-docker-manager.conf
systemd-tmpfiles --create /usr/lib/tmpfiles.d/kor-travel-docker-manager.conf
```

실패하면 release는 유지한 채 `host lease boot provisioning requires attention`을 stderr로
보고한다. 설치 뒤 확인:

```bash
ls -l /usr/lib/tmpfiles.d/kor-travel-docker-manager.conf
sudo ls -la /run/lock/kor-travel-docker-manager     # -ld가 아니라 -la로 본다
```

`ls -la`인 이유: tmpfiles의 `d` 타입은 기존 디렉터리의 **소유자와 mode만 바로잡고 내용은
지우지 않는다.** 이미 선점된 호스트에서 `--create`를 돌리면 디렉터리는 `drwx------ root
root`로 깨끗해 보이지만 침입자가 심어 둔 파일이 남는다. lock fd 검증(`st_uid == 0`,
`nlink == 1`, `0600`)이 fail-close로 잡고 installer도 root 아닌 항목을 발견하면 보고하지만,
눈으로도 확인한다.

정상 상태는 다음과 같다.

```
drwx------ 2 root root ...  .
drwxrwxrwt 5 root root ...  ..
-rw------- 1 root root ...  global-mutation.lock
-rw------- 1 root root ...  pinned-runtime-rebuild.lock
```

**trusted installer를 쓰지 않는 호스트**(2절의 rsync 배포본, rehearsal 등)에는 `deploy/`
트리도 installer도 없다. 그런 호스트에서는 위 두 명령을 저장소 체크아웃에서 한 번 직접
실행한다. 유닛 자체는 release와 무관하므로 재설치할 필요가 없다.

> **백엔드는 계속 root로 돈다.** 이 절은 lease 디렉터리의 생성 시점만 다룬다. 전용 서비스
> 계정 전환은 `docs/tasks.md`의 `NONROOT-BACKEND`로 분리했다 — ADR-41의 "기각한 설계"
> 절에 왜 환경변수 기반 seam이 계약이 되지 못하는지 기록해 두었다.

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
- API와 UI는 raw Compose의 `${KTDM_DOCKER_NETWORK_MODE:-host}` 및 resolved `host` network를 같이 유지한다.
  API는 loopback BFF가 요구하는 `ktc.cli api --host 0.0.0.0 --port 12601`, UI는 auth guard 뒤
  `npm run build && exec npm run start` production command와 `12605` port를 정확히 유지해야 한다.
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
trusted installer가 고정하는 `/opt/kor-travel-docker-manager`는 canonical execution root이며, legacy home
checkout은 Compose file·cwd·env-file source가 될 수 없다. Manager의 canonical release 배포 뒤에는 legacy
입력을 먼저 protected C6c state로 한 번 snapshot한다.

snapshot source의 final `docker-compose.override.yml`와 그 고정된 sibling Concierge `.env`는 모두 root 소유
`0600`, regular file, hard-link 없음이어야 한다. user-writable parent는 source artifact를 남길 수는 있어도
Manager runtime의 입력이 될 수 없으며, stage 전 local 운영 runbook의 owner-only 준비 절차로 이 precondition을
만족시킨다. source path와 secret 값·hash는 이 문서나 명령 출력에 남기지 않는다.

```bash
sudo -n /opt/kor-travel-docker-manager/backend/.venv/bin/ktdctl \
  compose-boundary stage-legacy-override \
  --source <legacy-override-absolute-path> --confirm
```

stage는 Docker/Compose를 실행하지 않고 final file을 `O_NOFOLLOW` descriptor와 `fstat`으로 검증해
owner-only pending snapshot에 원자적으로 복사한다. 이미 같은 snapshot이면 idempotent이고 내용이 다르면
fail-close한다. home source는 rename·delete하지 않으며 stage 뒤 Manager가 다시 읽지 않는다.
legacy Concierge UI의 `env_file`은 구형 상대 문자열 한 항목 또는 Compose 장형 mapping 한 항목만 허용한다.
장형 mapping은 override 위치에서 계산한 **정확한** sibling Concierge `.env`를 `path`로, boolean `required: true`를
가져야 한다. `format`은 Compose raw mode를 뜻하는 정확한 `raw` 값일 때만 추가로 허용한다. 임의 absolute path,
다른 추가 key·format·optional source는 허용하지 않는다.

staged Concierge source에 `API_KEYS`, `APP_ENV`, `API_AUTH_ENABLED`가 **아예 선언되지 않은** 경우에만 각각의
canonical root `KOR_TRAVEL_CONCIERGE_*` 값을 사용한다. 이는 legacy UI source가 API runtime 값을 소유하지 않았던
기존 토폴로지의 호환 범위이며, 세 값을 source에서 선언했다면 빈 값도 포함해 source 값 자체가 필수다. 나머지
`KTC_*` UI 인증·session·proxy·origin 값은 staged source에 모두 있어야 하며 root fallback을 허용하지 않는다.
최종 유효 API key-set, backend key membership, `production`, authentication-enabled 검증은 source/root의 출처와
관계없이 동일하게 통과해야 한다.

n150의 pinned rebuild 정본은 `KTDM_DEPLOYMENT_ENVIRONMENT=rehearsal`와
`KTDM_DEPLOYMENT_LIFECYCLE=rebuildable`을 함께 쓰므로, 이 상태에서는 deployment mode를 수동으로
`production`으로 바꾸지 않는다. stage·retire는 이 exact rehearsal/rebuildable·PinVi production·Map principal
필수 contract를 다시 확인하고, 다음 `rebuild-pinned`와 동일한 root-owned host lease로 직렬화한다. production
mode라면 기존 fixed C6c global lock을 사용한다. 둘 이외의 mode/lifecycle과 caller-supplied project root는
mutation 전에 거부한다.

stage 성공 뒤 root에서 아래 retire 공식 경로를 한 번 실행한다.

```bash
sudo -n /opt/kor-travel-docker-manager/backend/.venv/bin/ktdctl \
  compose-boundary retire-legacy-override --confirm
```

retire는 protected pending snapshot의 root-only로 알려진 Geo backup 값과 Concierge UI source만 raw 파싱하고,
값 충돌·symlink·비정규 파일·잘못된 API key membership을 fail-close한다. 위 API auth 세 값의 허용된 root fallback도
candidate에 새 값을 쓰지 않고 existing root authority를 재검증할 뿐이다. candidate root `.env`를 원자적으로
갱신한 뒤 canonical `/opt` Compose에서 Concierge API/MCP/scheduler/UI와 그 전이 `depends_on` 서비스, 실제로 참조한
top-level secret/network/volume/config만 추린 root-owned 일시 projection을 출력 없이 raw/resolved C6c 경계까지
검증한다. 같은 projection만 정확한 네 Concierge service의 recreate에도 사용하므로, 이번 retire와 무관한 Map/PinVi
candidate의 아직 준비되지 않은 explicit credential guard가 Concierge 경로를 막거나 반대로 runtime 입력으로 섞일 수 없다.
projection은 trusted canonical source에서 매번 만들고 즉시 제거하며, caller/home source가 경로나 내용을 지정할 수 없다.
성공한 경우에만 **같은
protected state filesystem 안에서** pending directory를 owner-only archive로 rename한다. canonical
rehearsal/rebuildable에서는 pinned-runtime rebuild host lease(production에서는 fixed C6c global mutation lock)를
계속 보유한 채 API/MCP/scheduler/UI 정확한 네 service만 canonical single-file source로 force-recreate한다.
production의 일반 `ensure`는 허용되지 않으므로 이 단계에 사용하지 않는다. archive 뒤 재생성이 실패하면 root
`.env`와 archive는 의도적으로 유지된다. 원인을 해소한 뒤 아래 Manager retry만 사용한다.

```bash
sudo -n /opt/kor-travel-docker-manager/backend/.venv/bin/ktdctl \
  compose-boundary activate-concierge --confirm
```

retry 역시 pending stage가 없는지와 raw/resolved C6c 경계를 먼저 다시 확인하며, 수동 `docker compose`·legacy
source restore·일반 `ensure`로 대체하지 않는다. 성공 뒤 실제 공개 브라우저에서 Concierge 로그인→BFF 동작→로그아웃을 검증한다.

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

다만 raw C6c prebuild transaction은 source materialize보다 먼저 canonical Compose의 read-only
bind graph를 검사한다. 따라서 `PINVI_REPO_DIR`가 가리키는 **source-owner checkout**에는 현재
pinset이 요구하는 `infra/postgres/bootstrap-pinvi-runtime-role.sh`가 regular file로 존재해야 한다.
이 조건은 checkout HEAD를 candidate authority로 승격하지 않는다. 그것은 이후 root-owned exact
source materialization이 계속 소유한다. command 전에 source owner는 canonical `origin`, clean worktree,
그리고 Manager가 고정한 PinVi revision에서 이 tracked file이 worktree에 존재하는지만 확인한다.
조건이 맞지 않으면 script를 복사하거나 Compose bind/Manager guard를 완화하지 말고, WIP를 파괴하지 않는
clean approved release checkout으로 source deployment를 먼저 수렴한다. 그 뒤에만 아래 공식 command를
한 번 실행한다.

fresh root `.env`에 PinVi runtime·schema owner·migration owner·migrator role의 여섯 값이 **모두
미선언**이면, 이 명령은 caller의 `KOR_TRAVEL_DOCKER_MANAGER_*` 경로 override가 아니라 trusted
`/opt/kor-travel-docker-manager`의 root-owned Compose·`.env` pair만 고정해 읽는다. root-owned
pinned-runtime host lease 안에서 exact `rehearsal/rebuildable`·PinVi production·Map principal-required와
모든 C6c operation token을 **쓰기 전에** 확인한 뒤에만 서로 다른 role명과 무작위 password를 원자적으로
추가하고 `0600` mode를 유지한 새 environment snapshot을 잡는다. 일부 선언·공백·중복·역할/비밀번호 재사용·
형식 오류·경로/파일 identity drift는 기존 값을 추측·덮어쓰기·회전하지 않고 candidate, journal, runtime, DB
mutation 전에 거부한다. pinned root `.env` 값은 dotenv/caller 환경 보간을 적용하지 않는 literal authority이며,
이미 완전한 여섯 값은 같은 원문을 frozen Compose snapshot에 명시적으로 결박해 재사용만 한다. credential 원문은
CLI 결과·journal·log에 기록하지 않는다.

현재 pinset의 v8 resume journal이 정확히 `map_runtime_ready`이고 여섯 값이 미선언이었던 경우는 예외적인
공식 재개 경계다. 이 경우에만 `.env`에는 이전 environment SHA만을 담은 비밀 비포함 marker를 같은 원자 write로
남기고, candidate raw/resolved Compose를 다시 검증한 뒤 journal에는 이전/현재 environment SHA와 이전/현재
resolved Compose SHA를 모두 담은 단 한 번의 rebind receipt를 기록한다. 다른 phase·다른 digest·이미 rebind한
journal은 쓰기 전에 거부한다. marker 또는 receipt는 credential 원문을 포함하지 않으며, write 직후 crash도 같은
marker로 재개 판정을 다시 검증한다.
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
