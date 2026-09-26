# 운영(prod) 배포 가이드

> **ADR-51(2026-09-26) 이후 배포는 마이그레이션 전진이다.** `rebuild-pinned`는 DB를 보존하고
> 멱등 one-shot으로 head까지 올린다. 같은 pair는 수렴만 하고, DB를 지우는 길은
> `rebuild-pinned --restart --reason "..." --confirm` 하나다. 영속 상태는 state root의
> `deploy-status.json`(in_progress/committed) 하나이며 v8 journal은 더 쓰지 않는다.
> `deploy-status.json`이 없는 호스트는 기준선 없는 전체 경로를 한 번 돈다 — 남아 있는 v6
> manifest·v8 journal을 넘겨받지 않는다(carry-over 없음, ADR-51 B3).
> 아래의 파기형·journal·resume 서술은 그 이전 설계의 기록이다.

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

**회전 요청 디렉터리**(대시보드가 요청을 남기는 자리)는 installer가 **최초 1회만**
`/var/lib/kor-travel-docker-manager-requests`를 `root:root 0700`으로 만든다. backend를
비-root로 돌리는 호스트에서는 설치 후 소유자를 그 사용자로 바꾼다:

```bash
sudo chown <backend-user>:<backend-group> /var/lib/kor-travel-docker-manager-requests
```

이 chown은 **한 번만** 하면 된다. 이후 설치는 이미 있는 디렉터리의 소유권을 보존하고
안전성(symlink 아님, group/other 쓰기 금지)만 검증한다 — 예전에는 `install -d`가 매
설치마다 소유자를 root로 되돌려, 비-root backend 호스트에서 업그레이드 때마다 회전 요청
경로가 침묵 회귀했다(GM-04).

**group-writable로 만들지 않는다**(`0770` 등). 무결성 검사가 그 디렉터리를 영구히
거부해 회전 요청 경로 전체가 잠긴다 — installer도 group/other 쓰기가 열린 기존
디렉터리를 발견하면 설치를 중단한다.

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
  유실되면 롤백 소스도 함께 유실된다. `KTDM_OFFBOX_HOST` 등을 설정했다면
  `ktdctl offbox-sync run`이 이 디렉터리를 6개 role 백업과 함께 원격으로 옮기고
  재검증한다(`docs/docker-management.md` "offbox-sync" 참고) — 로컬 디스크 유실이
  이 소스까지 함께 삼키는 시나리오를 없앤다.
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

#### Map 단일 LOGIN 자격증명은 **운영자가** `.env`에 넣는다 (ADR-100)

ADR-100이 Map의 LOGIN role 셋(`ktm_feature_migrator` / `ktm_feature_api_runtime` /
`ktm_feature_dagster_runtime`)을 `ktm_feature_service` 하나로 합쳤다. 새 compose는 그
하나의 자격증명 쌍을 **모든** Map service에 먹인다 — db-role-bootstrap,
application-schema one-shot, api, dagster, dagster-daemon.

```
KOR_TRAVEL_MAP_SERVICE_PASSWORD=<48자 영숫자>
KOR_TRAVEL_MAP_PG_DSN=postgresql+asyncpg://ktm_feature_service:<같은 값>@127.0.0.1:12700/kor_travel_map
```

**Manager는 이 값을 만들지 않는다.** M05를 폐기하면서 `.env`에 role 자격증명을 심고 그
해시로 재개를 게이팅하던 경로가 함께 사라졌다(`compose_service.py`의
`prewrite_admission` 주석). 그 전까지는 Manager가 넣었으므로 운영자가 신경 쓸 일이
아니었고, 사라진 뒤에도 이 문서가 그것을 이어받지 못했다.

두 값이 없으면 재구축은 **`prejournal_failure` / stage `prebuild_snapshot`** 으로
죽는다. compose가 `${KOR_TRAVEL_MAP_PG_DSN:?...}`를 쓰므로 resolved 문서를 만드는
단계에서 막히는 것인데, 봉인된 실패는 stage 한 단어만 남기므로 원인이 보이지 않는다
(2026-09-23 실측). `.env`에 키가 있는지부터 본다:

```bash
for k in KOR_TRAVEL_MAP_SERVICE_PASSWORD KOR_TRAVEL_MAP_PG_DSN; do
  sudo grep -q "^$k=" /opt/kor-travel-docker-manager/.env && echo "$k: 설정됨" || echo "$k: 없음"
done
```

특수문자 없는 영숫자를 쓴다 — DSN에 그대로 들어가므로 URL 인코딩이 필요해지면 두 값이
갈릴 수 있다. 값을 바꿀 때는 `.env`를 먼저 백업하고(`cp -a`), `journal`이 없는 시점에만
바꾼다: `map_runtime_ready` 이후에 `.env`가 바뀌면 그 pinset은 영구 재개 불가가 된다.

퇴역한 키 셋(`KOR_TRAVEL_MAP_APPLICATION_FRESH_MIGRATE_FENCE_DIR`,
`..._FRESH_FINALIZE_FENCE_DIR`, `KOR_TRAVEL_MAP_APPLICATION_FINAL_PERMIT_DIR`)은
ADR-101에서 소비자가 사라졌다. `KOR_TRAVEL_MAP_DAGSTER_STORAGE_PERMIT_DIR`와
`KOR_TRAVEL_MAP_DAGSTER_STORAGE_CONFIG_SHA256`은 ADR-51 D-3에서 compose가 더 참조하지
않는다(Map M1 이후 storage one-shot이 읽지 않는다). 모두 남아 있어도 무해하므로 굳이 지우지
않는다.

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

v5 terminal은 source materialization의 감사 기록이다. v6 execution registry를 도입한 뒤에는
`ktdctl pin migrate-execution-v6 --confirm`과 `ktdctl pin verify`가 현재 trusted Manager
implementation의 실행 가능 여부를 정한다. v5 기록에는 Manager revision이 없으므로 과거 v6
execution으로 이관하거나 추측하지 않으며, 새 v6 execution이 실제로 terminal이 된 경우에만 그
execution을 다시 거부한다.

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

installer는 `/opt/kor-travel-docker-manager` 트리를 통째로 교체하고 구 트리를 삭제한다.
그 트리에서 서비스가 실행 중이면 설치 순간부터 재기동까지 반파손 상태로 돈다(특히 Next.js는
route 번들을 요청 시점에 lazy 해석한다). 그래서 installer는 **APP_ROOT 트리에서 실행 중인
프로세스(cwd·exe·open fd 기준)를 preflight로 탐지해 fail-close**한다 — 먼저 서비스를
중지(`sudo systemctl stop ktdm-backend ktdm-frontend`)하는 것이 표준 순서다. 위험을 감수하고
실행 중 교체를 강행하려면 `--allow-live`를 준다. 설치 뒤 백엔드를 곧바로 올리려면
`--restart-backend`를 함께 주면 commit 직후 `systemctl restart ktdm-backend`가 수행된다
(프론트엔드는 clean checkout이라 아래 §4의 build가 선행돼야 하므로 자동화하지 않는다).

백엔드는 systemd 유닛으로 구동한다. installer가 `deploy/systemd/ktdm-backend.service`를
`/etc/systemd/system/`에 설치·enable하므로(재기동은 하지 않는다), **설치 직후에는 옛
코드가 계속 돌고 있다** — 새 release 반영은 명시적 재기동이다:

```bash
sudo systemctl restart ktdm-backend
sudo systemctl status ktdm-backend --no-pager   # active (running) 확인
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:12901/health   # 200
```

재부팅·크래시 복구는 systemd가 소유한다(`Restart=on-failure`, enable됨). 로그는
journald(`journalctl -u ktdm-backend`)와 백엔드 자체의 월간 로테이션 파일
(`backend/logs/`) 양쪽에 남는다 — 과거의 `/tmp` 로그는 tmpfs라 재부팅(진단이 가장
필요한 순간) 직후 증발했다.

유닛이 아직 없는 호스트(첫 설치 전, rehearsal 등)의 폴백만 nohup을 쓴다:

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

화면은 재구축 상태를 이유로 변경을 막지 않는다. 종전에는 재구축 journal이 `.env` 해시를
동결하고 재개 때 대조했으므로, 미종결 journal이 있으면 화면이 변경을 막거나 명시 문구
입력을 요구했다. ADR-51 뒤 배포는 재개하지 않고 처음부터 다시 돌며 `.env`를 동결하지
않으므로 막을 것이 없다 — 그 가드(`GET /api/v1/admin/password/preflight`)와 승인 입력은
ADR-51 B3에서 지웠다. 다만 `.env` 파일을 다시 쓰는 것이므로, rehearsal·production에서는
그 재작성이 host 변경 lock(3.z)을 잡는다(ADR-51 C-2). 재구축·M05·설치·다른 변경이 lock을
쥔 동안에는 409 `MANAGER_MUTATION_ACTIVE`로 거절되고 `.env`는 그대로다 — 끝난 뒤 다시
시도한다.

`.env`가 root `0600`인데 backend가 비-root로 돌면 이 기능은 `ENV_NOT_WRITABLE`로
거부한다. **권한을 완화하지 마라** — 그 권한이 이 파일의 유일한 보호다. backend를 해당
소유자 권한으로 재기동하거나 SSH에서 해시를 직접 교체한다.

### 3.z host mutation lease 디렉터리는 부팅 시점에 만든다

`KTDM_DEPLOYMENT_ENVIRONMENT`가 `local`이 아니면(`production`·`rehearsal`·미지정 모두)
**모든** Manager mutation은 host 변경 lock
`/run/lock/kor-travel-docker-manager/global-mutation.lock`(G) 하나를 지난다
(`manager_mutation_lock_path`, ADR-51 C-2·C-3). 경로는 `.env` 파일 값으로 정하고 프로세스
환경으로 채우지 않는다 — 단 프로세스 환경의 모드가 명시적으로 local이 아니면 G다(더 엄격하게만). UI의 컨테이너 조작·설정·초기화,
관리자 비밀번호 변경, `ktdctl compose-boundary` stage/retire/activate, `ktdctl pin`
mutator, 재구축·M05·installer launcher가 전부 같은 lock이다(일부러 뺀 것 — 백업·offbox
동기화·핀 요청 제안·airport 컨테이너 — 은 `docs/decisions.md` ADR-51 "C 범위"). 경합이면
기다리지 않고 거절한다 — API는 409 `MANAGER_MUTATION_ACTIVE`, CLI는 종료 코드 2다.
재구축(1~2시간)·M05(그보다 길다)·설치가 도는 동안 화면 변경이 전부 409인 것은 설계다.
반대로 화면 요청·비밀번호 변경이 G를 몇 ms 쥔 바로 그 순간 시작한 launcher(chain17 등)는
기다리지 않고 실패하므로 다시 돌린다.
`$HOME/.local/state/...` 개발 lock은 비root 개발용 `local`에만 남는다. 경로 override
(`KTDM_C6C_DEPLOYMENT_LOCK`)는 ADR-51 C-3에서 없앴다.

G를 잡는 backend는 root여야 한다(디렉터리 `0700 root:root`). 설치 뒤에는
`sudo systemctl restart ktdm-backend`를 곧바로 한다 — 재기동 전의 backend는 rehearsal에서
여전히 `$HOME` lock을 잡는다.

재구축도 G 하나만 잡는다 — 따로 잡던 `pinned-runtime-rebuild.lock`(P)은 ADR-51 C-3에서
없앴다. launcher가 G를 쥐고 fd를 물려주면 `rebuild-pinned`는 그 fd를 검증해 그대로 쓴다.
rehearsal 호스트도 이 디렉터리를 쓰므로 이 절은 비운영 호스트에도 적용된다.

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
보고한다. **installer의 종료 코드는 release 설치 결과만 나타낸다** — lease provisioning
실패는 종료 코드에 반영되지 않으므로, 자동화는 stderr 또는 아래 확인 명령으로 판단한다.

installer는 **시작 시점에도** 이미 설치된 유닛이 있으면 한 번 적용한다. 설치 절차의 첫
단계가 이 lease를 잡는 것이라, 선점된 호스트에서는 구제책(맨 끝의 유닛 설치)이 자신이
막으려는 실패 뒤에 갇히기 때문이다. 유닛이 한 번이라도 설치된 호스트는 이 조기 적용으로
스스로 복구되고, 최초 설치 호스트는 preflight 오류가 실측 소유자·mode와 복구 명령을
함께 보고한다.

설치 뒤 확인:

```bash
ls -l /usr/lib/tmpfiles.d/kor-travel-docker-manager.conf
sudo ls -la /run/lock/kor-travel-docker-manager     # -ld가 아니라 -la로 본다
```

`ls -la`인 이유: tmpfiles의 `d` 타입은 기존 디렉터리의 **소유자와 mode만 바로잡고 내용은
지우지 않는다.** 이미 선점된 호스트에서 `--create`를 돌리면 디렉터리는 `drwx------ root
root`로 깨끗해 보이지만 침입자가 심어 둔 파일이 남는다. lock fd 검증(`st_uid == 0`,
`nlink == 1`, `0600`)이 fail-close로 잡고 installer도 root 아닌 항목을 발견하면 보고하지만,
눈으로도 확인한다.

설치 직후 정상 상태는 **빈 디렉터리**다. tmpfiles는 디렉터리만 만든다.

```
drwx------ 2 root root ...  .
drwxrwxrwt 5 root root ...  ..
```

lock 파일은 나중에 생긴다 — `global-mutation.lock`은 첫 mutation 때 처음 온 획득자가
만든다. tmpfs라 재부팅하면 사라진다. 즉 **비어 있다고 해서 설치가 실패한 것이 아니다.**
가동 중 상태는 이렇다.

```
drwx------ 2 root root ...  .
drwxrwxrwt 5 root root ...  ..
-rw------- 1 root root ...  global-mutation.lock
```

ADR-51 C-3 이전 release가 남긴 `pinned-runtime-rebuild.lock`이 보일 수 있다. 이제 아무도
잡지 않으므로 `sudo lslocks`에 보이지 않을 때 root로 지워도 되고, 재부팅하면 사라진다.

`root` 이외가 소유한 항목이 보이면 선점된 것이다.

**trusted installer를 쓰지 않는 호스트**(2절의 rsync 배포본, rehearsal 등)에는 `deploy/`
트리도 installer도 없다. 그런 호스트에서는 위 두 명령을 저장소 체크아웃에서 한 번 직접
실행한다. 유닛 자체는 release와 무관하므로 재설치할 필요가 없다.

**`/opt/kor-travel-docker-manager` 밖에 남는 설치 산출물**(release rollback이 되돌리지
않는 것들): `/etc`의 세 파일 — 이 tmpfiles 유닛,
`/etc/systemd/system/ktdm-backend.service`·`ktdm-frontend.service`(3절·4절),
`/etc/logrotate.d/kor-travel-docker-manager`(백업 로그 로테이션, `KTDM_BACKUP_ROOT` 선언
시) — 과 `/var/lib`의 상태 트리 — `/var/lib/kor-travel-docker-manager`(state root·
registry·archive)와 `/var/lib/kor-travel-docker-manager-requests`(회전 요청). 이전
release로 내려가도 이들은 그대로 남고 다음 설치가 갱신한다(state/registry는 의도된
영속 상태다). tmpfiles 유닛을 완전히 제거하려면 다음과 같이 한다.

```bash
sudo rm -f /usr/lib/tmpfiles.d/kor-travel-docker-manager.conf
sudo rm -rf /run/lock/kor-travel-docker-manager   # 진행 중인 mutation이 없을 때만
```

제거하면 lease 디렉터리는 다시 런타임에 생성되고 부팅마다 선점 창이 열린다.

> **백엔드는 계속 root로 돈다.** 이 절은 lease 디렉터리의 생성 시점만 다룬다. 전용 서비스
> 계정 전환은 `docs/tasks.md`의 `NONROOT-BACKEND`로 분리했다 — ADR-41의 "기각한 설계"
> 절에 왜 환경변수 기반 seam이 계약이 되지 못하는지 기록해 두었다.

## 4. 프론트엔드 (Next.js, :12905)

```bash
cd <프론트엔드 배포 디렉터리>
npm ci
npm run build      # .env.production 의 NEXT_PUBLIC_BACKEND_URL 이 번들에 인라인됨
sudo systemctl restart ktdm-frontend
```

`NEXT_PUBLIC_*`은 빌드 타임에 인라인되므로 운영 호스트에서 빌드해야 운영 API 주소가 반영된다.

프론트엔드 유닛은 계정명·경로가 host 민감 정보라 템플릿
(`deploy/systemd/ktdm-frontend.service.template`)이며, installer가 root 소유 `.env`의
아래 키로 렌더링해 설치한다. 두 필수 키가 없으면 유닛을 건너뛰고 경고만 남긴다.

```bash
# .env (root 0600)
KTDM_FRONTEND_SERVICE_USER=<프론트엔드를 소유·실행할 비root 계정>
KTDM_FRONTEND_APP_DIR=<frontend 디렉터리 절대 경로>
KTDM_FRONTEND_NPM=<npm 절대 경로, 생략 시 /usr/local/bin/npm>
```

프론트엔드는 root 권한이 전혀 필요 없다 — 과거 nohup 방식이 root로 띄우던 것은
불필요한 권한 확대였고, 유닛은 반드시 비root 계정으로 지정한다. 유닛 미설치 호스트의
폴백: `nohup setsid npm run start > /tmp/ktdm_frontend.log 2>&1 &`

## 5. 공개 도메인 라우팅 (네트워크 인프라 — 저장소 밖)

운영 공개 도메인은 DDNS로 공인 IP에 연결된다. 게이트웨이/리버스 프록시(또는 포트포워딩)에서 아래를
운영 호스트의 앱 포트로 라우팅해야 외부 접근이 완성된다.

| 공개 도메인 | → 운영 호스트 포트 |
|---|---|
| `manager.<domain>` (대시보드) | `:12905` |
| `manager-api.<domain>` (API) | `:12901` |

이 라우팅이 없으면 대시보드(prod 빌드)가 API(`manager-api.*`)에 닿지 못한다. 라우팅 설정은 라우터/프록시
인프라 영역이며 이 저장소 범위 밖이다.

### 5.1 신뢰 프록시 설정 (필수 — 로그인 rate limit 정상 동작)

엣지 프록시(HAProxy 등) 뒤에서 백엔드는 모든 공개 트래픽을 프록시의 소켓 IP 하나로 본다.
로그인 rate limit은 client IP별로 실패를 집계하는데, 프록시를 신뢰하도록 설정하지 않으면
**모든 WAN 클라이언트가 같은 버킷을 공유**한다 — 인터넷의 아무나 10분 창에 잘못된 로그인
5회를 보내면 진짜 관리자의 로그인·비밀번호 변경이 함께 429로 잠긴다(외부 DoS). 그래서 아래
두 값은 **선택이 아니라 필수**다.

```bash
# .env — 엣지 프록시의 IP를 exact /32 로, 그리고 secret 헤더를 함께 쓴다.
KTDM_TRUSTED_PROXY_CIDRS=<프록시 IP>/32
KTDM_TRUSTED_PROXY_SECRET=<프록시가 주입하는 헤더 시크릿>
```

- **exact /32(IPv6는 /128)를 쓴다.** `/24` 같은 광역 CIDR은 그 대역의 다른 LAN 피어가
  `X-Forwarded-For`를 위조해 rate limit을 우회하게 한다.
- **secret을 반드시 함께 쓴다.** CIDR만으로는 host 네트워크의 로컬 프로세스가 loopback
  출처로 `X-Forwarded-*`를 위조할 수 있다. 프록시가 매 요청에 이 시크릿 헤더를 주입하고,
  백엔드는 그 일치까지 확인해야 XFF를 신뢰한다.
- 엣지(HAProxy 등)에도 소스 IP별 연결 수 제한을 두어 이 저장소의 durable 로그인 한도와
  이중으로 방어한다.

설정이 빠져 있으면 대시보드의 **배포 사전 점검**(`login_rate_limit_proxy` 체크)이
production에서 `warn`으로 노출하고, rate-limit 429 감사 행에는 `shared_ip_bucket` 사실이
기록된다.

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
필수 contract를 다시 확인하고, production mode와 마찬가지로 host 변경 lock(3.z의 G)으로
`rebuild-pinned`·pin 회전·M05·installer와 직렬화한다(ADR-51 C-2). 다른 변경이 G를 쥐고 있으면 기다리지 않고
`cannot acquire the Manager mutation lock: ...`으로 종료 코드 2를 낸다. 둘 이외의 mode/lifecycle과
caller-supplied project root는 mutation 전에 거부한다.

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
rehearsal/rebuildable과 production 모두 Manager 변경 락 G(`global-mutation.lock`)를
계속 보유한 채 API/MCP/scheduler/UI 정확한 네 service만 canonical single-file source로 force-recreate한다.
production의 일반 `ensure`는 허용되지 않으므로 이 단계에 사용하지 않는다. archive 뒤 재생성이 실패하면 root
`.env`와 archive는 의도적으로 유지된다. 원인을 해소한 뒤 아래 Manager retry만 사용한다.

```bash
sudo -n /opt/kor-travel-docker-manager/backend/.venv/bin/ktdctl \
  compose-boundary activate-concierge --confirm
```

retry 역시 pending stage가 없는지와 raw/resolved C6c 경계를 먼저 다시 확인하며, 수동 `docker compose`·legacy
source restore·일반 `ensure`로 대체하지 않는다. 성공 뒤 실제 공개 브라우저에서 Concierge 로그인→BFF 동작→로그아웃을 검증한다.

## 8. pinned runtime 배포 (ADR-51 마이그레이션 전진)

> 현재 배포 protocol은 이전 compatible-pair, cache-target, standalone DB backup mutation과 Map UI 회전의
> **공개 CLI 운영 경로**를 모두 퇴역시켰다. 과거 v1–v4 manifest와 v5–v8 rebuild journal·tombstone은 실행
> 근거가 아니며, 아래 비운영 `rebuild-pinned`만 generation을 배포하는 정본이다. 파기형 v8 journal·resume
> 설계의 근거는 `docs/decisions.md` ADR-51과 `docs/journal.md`에 남아 있다.

### 8.1 비운영 pinned runtime 배포

실제 운영 환경에는 이 절을 적용하지 않는다. typed 환경 pair
`KTDM_DEPLOYMENT_ENVIRONMENT=rehearsal` 및 `KTDM_DEPLOYMENT_LIFECYCLE=rebuildable`를 frozen canonical
environment에서 함께 명시한 비운영 환경만 다음 command를 실행할 수 있다. `local/development`,
`rehearsal/rebuildable`, `production/operational` 외 조합과 production 환경에 flag만 추가한 조합은 모두
mutation 전에 거부한다.

```bash
# 일반 배포: DB를 보존하고 head까지 전진한다. 같은 pair면 수렴만 한다.
sudo -n /opt/kor-travel-docker-manager/backend/.venv/bin/ktdctl \
  pinvi-pair rebuild-pinned --confirm
# 유일한 파기 경로: Map application·Map Dagster·PinVi DB를 지우고 빈 DB에서 다시 만든다.
sudo -n /opt/kor-travel-docker-manager/backend/.venv/bin/ktdctl \
  pinvi-pair rebuild-pinned --restart --reason "<한 줄 사유>" --confirm
# 복원 등으로 DB identity가 비파괴로 바뀌었을 때: 지금 떠 있는 DB를 새 기준으로 받아들인다.
sudo -n /opt/kor-travel-docker-manager/backend/.venv/bin/ktdctl \
  pinvi-pair rebuild-pinned --adopt-live-databases --reason "<한 줄 사유>" --confirm
```

이 command는 추적된 exact Map·PinVi commit만 Git archive build source로 쓰며 `.env` checkout HEAD,
old image, old manifest를 candidate authority로 쓰지 않는다.

순서는 다음과 같다.

1. **admission** — root, host-global mutation lock(G) 하나 안에서 runtime pin registry
   snapshot을 읽는다(ADR-51 C-3부터 pinned rebuild lease는 없다). 조건 없는 차단·낡은
   execution 결박은 결과의 `warnings`에만 남고, 대기 중인 pair 회전 intent만 거부한다.
2. **candidate** — exact source를 materialize하고, Map sealed builder image(pinset tag, 이미 있으면
   재사용)와 Manager가 build하는 Map UI·PinVi image의 ID를 attest한다. 세 schema head(Map application·Map
   Dagster·PinVi)는 candidate image에서 관측한다. 여기까지는 DB를 건드리지 않는다.
3. **판정** — 두 PostgreSQL을 frozen Compose에 맞춰 기동한 뒤 state root의 `deploy-status.json`과
   비교한다. `committed`이고 같은 pair·같은 image·같은 DB identity·같은 head면 빌드·migration·정지 없이
   떠 있어야 할 것만 맞추고 끝난다(`outcome: converged`). 지난 배포가 본 DB identity와 지금 DB가 다르면
   아무것도 바꾸기 전에 거부한다 — `--adopt-live-databases` 또는 `--restart`로만 넘어간다.
4. **전진** — `deploy-status.json`을 `in_progress`로 쓰고 runtime과 one-shot writer를 멈춘다.
   `--restart`면 여기서 세 DB를 지운다. 없는 DB만 만들고, Map application schema one-shot·Dagster storage
   migration·PinVi admin bootstrap을 멱등으로 돌린 뒤 각 head를 Manager가 DB에서 직접 읽어 candidate
   head와 대조한다. PinVi C6c canonical smoke와 전 서비스 readiness·image·secret isolation을 확인하면
   `deploy-status.json`을 `committed`로 바꾼다(`outcome: deployed`). 커밋이 남기는 기록은 이 파일
   하나다 — v6 manifest는 ADR-51 D-2부터 쓰지 않는다.
5. **실패** — runtime과 one-shot writer를 멈추고 남은 PinVi bootstrap credential을 정리한 뒤 원래 오류를
   낸다. DB는 자동으로 지우지 않는다. 상태는 `in_progress`로 남고 다음 실행이 처음부터 다시 돈다 —
   모든 단계가 다시 돌려도 안전하다.

`deploy-status.json`이 없는 호스트는 기준선 없는 전체 경로를 한 번 돈다. state root에 남은 v6
manifest·v8 journal·legacy tombstone은 넘겨받지도 고치지도 않는다(ADR-51 B3). 결과 JSON은
launcher·`chain17`이 읽는 `success`·`phase`(항상 `committed`)·`pinset_sha256`·`schema_heads` 키를 유지한다.

v6 manifest(`pinned-runtime-generation-v6.json`)는 ADR-51 D-1부터 이 Manager의 어떤 reader도 읽지
않고, D-2부터는 커밋도 쓰지 않는다 — M05 driver는 committed `deploy-status.json`을 대조하고
`in_progress`면 거부한다(실패한 배포 뒤에는 한 번 commit될 때까지 M05가 멈춘다). 배포 상태를 사람이
보려면 `sudo -n cat <state_root>/deploy-status.json`이나 rebuild `result.json`을 읽는다 — 공개 view·
`pin publish-generation`은 없어졌다. `.env`의 `KTDM_PINNED_RUNTIME_PUBLIC_ROOT`는 더 아무 의미가
없다(남아 있어도 무해). source/ETL 재적재는 committed 뒤 별도 workflow다.

호스트에 남은 아래 파일은 아무것도 읽지도 쓰지도 않는다. 지우는 것은 **선택**이며, D-1 이전 Manager로
되돌릴 일이 더는 없다고 판단한 뒤에만 한다(그 release의 M05 driver는 private v6 파일을 읽는다 —
`runtime-pin-registry.md` §1-2). 두어도 판정은 바뀌지 않는다.

- `<state_root>/pinned-runtime-generation-v6.json` (private)
- `/var/lib/kor-travel-docker-manager-public/pinned-runtime-generation-v6.json` (공개)
- `/var/lib/kor-travel-docker-manager-public/pinned-runtime-rebuild-v8.json` (공개)
- private `pinned-runtime-rebuild-v8-*.json`, `legacy-tombstone-v8-*.json`, v2–v7 artifact

`/var/lib/kor-travel-docker-manager-public` 디렉터리 자체는 지우지 않는다 — runtime-pins·
runtime-executions 공개 사본이 그 안에 있다.

ADR-51 D-3부터는 `<state_root>/map-application-300-artifacts/`(pinset별 Dagster storage permit 마운트
원천)와 `<state_root>/map-application-300-candidate/`(pinset별 영수증 디렉터리)도 아무것도 읽지도 쓰지도
않는다. 지우는 것은 역시 **선택**이며, D-3 설치 검증(재구축·M05)이 끝나고 D-3 이전 Manager와 M1 이전
Map pinset의 조합으로 되돌릴 일이 없다고 판단한 뒤에만 한다 — 그 조합의 storage one-shot은 pinset별
`permit.json`을 읽는데, Manager는 그것을 다시 발급하지 않는다.
