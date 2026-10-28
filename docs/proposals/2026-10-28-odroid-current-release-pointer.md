# odroid 서비스 "현재 release" 표지 통일 제안 — **Codex 합의 필요**

> **상태: 제안(미적용, 코드 없음).** odroid 배포를 맡는 Codex와 합의하기 전에는 odroid의 어떤 파일·
> 컨테이너·compose도 바꾸지 않는다. 이 문서 작성 중 odroid에서는 읽기 전용 조회만 했다(2026-10-28 03:00Z).
> weather 한정 선행 제안 `digitie/kor-travel-weather` `docs/reports/2026-10-27-odroid-weather-current-release-pointer.md`
> (#183)를 odroid의 모든 서비스(weather·geo·transport)로 넓힌 것이다. 합의 논의는 이 저장소 이슈에서 한다.

## 1. 문제

odroid(192.168.1.13)에는 **지금 운영 중인 release가 무엇인지 적힌 곳이 서비스마다 다르거나 없다.**

| 서비스 | 지금 "현재 release"가 남는 곳 | 재생성하면 |
|---|---|---|
| weather | 서비스마다 다르다: api·web·migrate는 `/opt/kor-travel-odroid/compose.yml`에 `sha256:` ID를 **제자리 수정**, dagster code-server는 `weather176…/images.yml` overlay, compose 기본값은 weather130 | 어떤 `-f` 조합으로도 전부를 맞게 되살릴 수 없다(§4) |
| geo | `geo-pg18-…/images.yml` overlay(컨테이너 label에만 기록) | overlay를 빠뜨리면 `93f435f5-thin`으로 조용히 되돌아간다 |
| transport | 자체 저장소 배포(`deploy-prod.sh` + `.release-sha`, 실행 때 넘기는 `BACKEND_RUNTIME_IMAGE`) | `.env.prod`의 값은 `:latest`라 앱 디렉터리에서 그냥 `up`하면 `:latest`로 바뀐다 |

공통 결과: 마지막 배포가 무엇이었는지는 **실행 중인 컨테이너의 compose label과 디렉터리 이름을 사람이 대조해서만** 알 수 있고,
그 대조를 빠뜨린 재생성은 오류 없이 옛 이미지로 돌아간다. 2026-10-27~28 실제로 geo가 compose 기본값과 다른 release로 돌고,
weather173(Codex)·weather176(Claude) 배포가 서로의 release를 모른 채 끼어들었으며, 여러 에이전트가 label에서 overlay를 재구성해야 했다.

## 2. 제안 요약

**서비스마다 표지(pointer) 하나, release 디렉터리마다 `images.yml` + `manifest.json`, 재생성 명령은 하나.**
compose 기본값·디렉터리 이름·`.release-sha`·컨테이너 label은 "현재"를 읽는 근거로 쓰지 않는다.

### 2.1 표지

```
/srv/ktdm-releases/current/weather   -> /srv/ktdm-releases/weather<PR>-<sha12>-<YYYYMMDD>[-rN]
/srv/ktdm-releases/current/geo       -> /srv/ktdm-releases/geo<PR>-<sha12>-<YYYYMMDD>[-rN]
/srv/ktdm-releases/current/transport -> /srv/ktdm-releases/transport<PR>-<sha12>-<YYYYMMDD>[-rN]
```

- symlink, **절대 경로**(Manager 설치 root `/opt/kor-travel-docker-manager -> /srv/ktdm-releases/ktdm-release-<sha>`와 같은 규약).
- 갱신은 원자적으로: `ln -sfn "$NEW" current/.weather.tmp && mv -T current/.weather.tmp current/weather`
  (rename(2)라 읽는 쪽은 옛 대상이나 새 대상 하나만 본다).
- `current/`는 `root:root 0755`. Manager installer GC는 `ktdm-release-*`만 지우므로 `current/`와 서비스 release는 건드리지 않는다.
  서비스 release GC를 나중에 둔다면 **표지 대상과 그 `previous` 체인 N개는 지우지 않는다**를 규칙으로 고정한다.

### 2.2 release 디렉터리 내용

| 파일 | 내용 | 권한 |
|---|---|---|
| `images.yml` | 그 서비스의 **모든** compose service의 `image:`만 담은 overlay(ui·web·migrate 포함 — 빠진 service가 있으면 기본값으로 떨어진다) | `0644` |
| `manifest.json` | 아래 스키마 | `0644` |
| (그 밖) | 빌드 로그·source tar·rollback 기록 등 기존 산출물 | 지금처럼 `0600` |

```json
{
  "schema": "ktdm.odroid-release.v1",
  "service": "weather",
  "repo": "digitie/kor-travel-weather",
  "pr": 176,
  "source_sha": "<40 hex>",
  "images": {
    "kor-travel-weather-api": {"ref": "kor-travel-weather-api:rel-<sha12>", "id": "sha256:<64 hex>"}
  },
  "built_at": "<UTC ISO8601>",
  "deployed_at": "<UTC ISO8601, 표지를 옮긴 시각>",
  "deployer": "codex | claude | owner",
  "previous": "<직전 표지 대상 디렉터리 이름>",
  "health": {"checked_at": "<UTC>", "result": "ok"}
}
```

- `images.yml`의 `image:`는 **이미지 ID(`sha256:…`)** 로 적는다. 로컬 빌드라 RepoDigest가 없고 tag(`latest-main`, `rel-…`)는
  다시 붙을 수 있다(실측: r6·r7 tag가 같은 ID). 사람이 읽을 tag는 `manifest.json`의 `ref`에 둔다.
- release 디렉터리 자체가 `0700`이면 비-root가 `current/<svc>/images.yml`을 못 읽는다 → 디렉터리 `0711` 또는 "읽기도 sudo로"를 고른다(§8 D5).

### 2.3 재생성 명령은 하나(서비스별)

odroid의 weather·geo는 **한 compose project(`kor-travel-odroid`)** 를 공유하므로 overlay를 서비스별로 따로 주면 서로를 되돌린다.
그래서 호스트 wrapper 하나가 **모든 표지의 overlay를 고정 순서로** 붙인다(제안 이름 `/usr/local/sbin/ktdm-odroid-compose`, 내용은 아래뿐):

```bash
#!/bin/bash
set -euo pipefail
C=/srv/ktdm-releases/current
args=(-p kor-travel-odroid --project-directory /opt/kor-travel-odroid -f /opt/kor-travel-odroid/compose.yml)
for s in geo weather; do                     # kor-travel-odroid project에 속한 서비스, 고정 순서
  f="$C/$s/images.yml"
  [ -f "$f" ] || { echo "표지 없음: $C/$s — 재생성 금지" >&2; exit 2; }
  args+=(-f "$f")
done
exec docker compose "${args[@]}" "$@"
```

정본 재생성:

```bash
ktdm-odroid-compose up -d --no-deps <해당 서비스의 compose service들>     # weather·geo
<transport 정본 명령 — §5>                                                 # transport
```

`docker compose -f /opt/kor-travel-odroid/compose.yml up …`을 직접 치는 것은 금지한다(runbook 문구).

### 2.4 배포 절차(모든 서비스 공통)

1. **잠금**: `flock /srv/ktdm-releases/current/.lock` — 같은 project의 동시 배포(weather173/176 같은)를 막는다.
2. **drift 점검(시작 전, 멈춤)**: 대상 서비스의 각 compose service에 대해 실행 중 컨테이너의 `docker inspect -f '{{.Image}}'`가
   `current/<svc>/manifest.json`의 `images.<service>.id`와 같은지 본다. 하나라도 다르면 **멈추고** 마지막 배포자와 확인한다.
   compose label(`config_files`)은 wrapper를 쓰면 symlink 경로만 남으므로 비교 근거로 쓰지 않는다 — **이미지 ID로만 비교한다.**
3. 새 release 디렉터리에 `images.yml`·`manifest.json`(health 전 필드 제외)을 둔다.
4. 새 overlay로 재생성: 표지는 아직 옮기지 않고, wrapper가 **대상 서비스 한 자리만** 후보 디렉터리의 `images.yml`로 바꿔 끼우게 한다
   (예: `KTDM_CANDIDATE_weather=<new dir> ktdm-odroid-compose up -d --no-deps …`; 다른 서비스는 계속 표지를 읽는다. 구현은 합의 뒤).
5. health 확인(서비스 runbook의 기존 health 기준).
6. **health 통과 뒤에만** 표지를 원자 교체하고 `manifest.json`에 `deployed_at`·`health`·`previous`를 채운다.
7. health 실패 → 표지는 그대로, 옛 표지로 정본 재생성해 되돌린다. 표지와 실행이 어긋난 채 끝나면 다음 배포의 2단계가 멈춰 준다.

### 2.5 되돌리기

`previous`(또는 지정 디렉터리)로 표지를 원자 교체 → 정본 재생성 → health. 옛 release 디렉터리와 그 이미지는 지우지 않는다.
`docker image prune` 계열은 표지 대상과 `previous` 체인의 이미지 ID를 제외해야 한다.

## 3. compose 기본값 제거(선택, 권장)

overlay를 빠뜨렸을 때 **조용히 되돌아가는 대신 크게 실패**하게 만든다.

- `/opt/kor-travel-odroid/compose.yml`의 weather·geo service에서 `image:` 줄을 **지운다.** compose는 병합 뒤 검증하므로
  overlay 없이 실행하면 `service "…" has neither an image nor a build context specified`로 멈춘다.
  (`image: ${VAR:?}` 방식은 쓰지 않는다 — 보간이 병합 전에 일어나 overlay가 있어도 실패한다.)
- 대가: `docker compose -f compose.yml ps/logs`도 실패한다 → 조회도 wrapper로 한다. 이것이 의도다(우회 경로가 없다).
- transport는 `docker-compose.shared.yml`의 `${BACKEND_RUNTIME_IMAGE:-kor-travel-transport-backend:latest}` 기본값을 지우고
  표지의 `images.yml`(transport project용 overlay)로 넘긴다 — transport 저장소 변경이므로 그쪽 합의가 필요하다.

## 4. 현재 상태(이전 출발점, 2026-10-28 03:00Z 읽기 전용 조회)

| 서비스 / compose service | 실행 이미지 (ID 앞 12) | 어디서 왔나 | compose 기본값 | 최신 release 디렉터리 |
|---|---|---|---|---|
| weather api | `e90a35a2781b` (tag `rel-a7075a6bcc5f-20261028-api-web-r6/r7`) | compose.yml 제자리 수정(overlay 없음) | 같은 ID(제자리 수정 결과) | `weather-api-web-20261028-r7` — `images.yml` 없음, `api-web-rollback-image.json`만 |
| weather web | `6d2ba2c5cd62` (같은 r6/r7) | compose.yml 제자리 수정 | 같은 ID | 위와 같음 |
| weather migrate | (one-shot, 미실행) | — | `sha256:a61b06a271e5` | 소속 release 미확인 |
| weather dagster code-server | `e6e3a048d271` (`kor-travel-weather-dagster:rel-e39362a38032`) | `weather176-e39362a38032-20261027/images.yml` | `codex-local/weather130-dagster:40bcce6c` | weather176 (weather177은 `images-web.yml`만) |
| geo api | `458d57fd3943` (`rel-8f95fff36388`) | `geo-pg18-8f95fff36388-20261028/images.yml` | `kor-travel-geo-api:93f435f5-thin` | `geo-pg18-8f95fff36388-20261028` |
| geo dagster code-server | `2990472a7e8b` (`rel-8f95fff36388`) | 같은 overlay | `kor-travel-geo-dagster:93f435f5-thin` | 같음 |
| geo ui | `f90b46a048a6` (`latest-main`) | compose 기본값(overlay 없음, 가변 tag) | `kor-travel-geo-ui:latest-main` | 없음 |
| transport backend·dagster code-server | `47c95fe6f123` (`rel-4d49610b2083`) | 자체 배포(실행 시 env) | `${BACKEND_RUNTIME_IMAGE:-…:latest}`, `.env.prod`는 `:latest` | `transport127-operations-20261024`(images.yml 없음) |
| transport frontend·admin-web | `a26e7441d360`·`cb8834ecee81` (tag 없는 로컬 빌드) | 자체 배포 | build 이름 | 없음 |
| (Manager 소유) dagster daemon·webserver | `kor-travel-dagster-host:11a71574…` | Manager 렌더(`config_files=-`) | compose.yml에 옛 `73c524b1…`·`930a4426…` 항목 잔존 | Manager 자체 symlink |

관찰된 위험(지금 그대로 재생성하면):

- **weather는 어떤 단일 조합도 맞지 않는다.** weather176 overlay로 weather 전체를 재생성하면 api·web·migrate가
  `rel-e39362a38032`/`b045e7f7…`로 되돌아가고, overlay 없이 dagster code-server를 재생성하면 weather130 이미지로 되돌아간다.
- **transport 표지 불일치**: `.release-sha`·`.staged-release-sha` = `3f5160a0…`(10-27 20:06)인데 실행 backend tag는
  `rel-4d49610b2083`(= `.transport-admin-release-sha`, 10-27 09:41). tag 규약 차이인지 실제 불일치인지 **Codex 확인 필요**.
- geo ui는 가변 tag(`latest-main`)라 재생성 시 무엇이 뜰지 정해져 있지 않다.
- compose.yml에 Manager 소유 dagster-host 항목이 옛 이미지로 남아 있어 두 소유자가 같은 이름을 정의한다.

## 5. transport 편입

transport는 별도 project(`kor-travel-transport`, 앱 디렉터리 `/home/digitie/apps/kor-travel-transport`)라 wrapper를 따로 둔다.

- `deploy-prod.sh`는 빌드 뒤 `/srv/ktdm-releases/transport<PR>-<sha12>-<YYYYMMDD>/`에 transport project용 `images.yml`
  (backend·dagster code-server·frontend·admin-web 전부, ID로)과 `manifest.json`을 쓰고, §2.4의 2·6단계(drift 점검·health 뒤 표지 교체)를 한다.
- 정본 재생성은 `ktdm-transport-compose up -d …` 하나: 기존 `-f docker-compose.yml -f docker-compose.shared.yml
  -f /opt/kor-travel-odroid/transport/transport.override.yml` 뒤에 `-f /srv/ktdm-releases/current/transport/images.yml`을 붙인다.
  `transport-admin` stack(`docker-compose.transport-admin.yml`)도 같은 표지의 overlay를 쓴다.
- `.release-sha`·`.staged-release-sha`·`.transport-admin-release-sha`는 **표지에서 파생한 사본**으로 격하하거나 지운다(D6).
- `/srv/ktdm-releases`는 root 소유이므로 digitie로 도는 배포의 표지 쓰기 경로(sudo 한 줄 vs 전용 helper)를 정한다(D5).

## 6. Manager가 쓰는 법

- **읽기 정본**: Manager 호스트-로컬 도구(회전·재구축 스크립트, 진단, 상태 스냅샷)는 odroid 서비스의 "배포된 release"를
  `current/<svc>/manifest.json`에서만 읽는다. compose 기본값·label·디렉터리 이름 추정은 쓰지 않는다.
- **drift 신호**: Manager 상태 수집이 실행 이미지 ID ≠ manifest ID를 발견하면 경고로 띄운다(차단은 배포 도구 몫).
  Manager 범용성 원칙에 따라 서비스 이름을 코드에 박지 않고 `current/*`를 열거한다.
- **Manager 자신은 대상이 아니다**: Manager는 이미 `/opt/kor-travel-docker-manager` symlink라는 같은 모양의 표지가 있다.
  다만 compose.yml의 dagster-host 잔존 항목은 정리 대상이다(D7).
- 코드 변경(manifest 읽기·경고)은 합의 뒤 별도 PR로 한다. 이 PR은 문서만이다.

## 7. 도입 순서(합의 뒤, 첫 적용은 Codex)

1. 위 §4 표에서 "실행 중"을 정답으로 보고, 서비스마다 **이미지 교체 없는** 시작 release 디렉터리를 만든다
   (`<svc>-adopt-<YYYYMMDD>/images.yml`에 현재 실행 ID, `manifest.json`의 `previous`는 비움). 표지를 그곳으로 처음 만든다.
   transport는 `.release-sha` 불일치를 먼저 확인한다.
2. wrapper 두 개를 설치하고 `ktdm-odroid-compose config`가 현재 실행 ID와 같은 image를 내는지 대조한다(재생성 없이).
3. weather의 compose.yml 제자리 ID 수정과 Codex rollout 도구(`canonical_api_compose.py` 계열)를 "compose.yml 수정" 대신
   "release 디렉터리 + 표지"로 바꾼다.
4. (선택) compose.yml에서 weather·geo `image:` 줄을 지운다(§3). 지운 뒤 `ktdm-odroid-compose config`를 다시 대조한다.
5. 양쪽 runbook(Codex 배포 절차, 이 저장소 `prod-deployment.md` odroid 절, 각 서비스 저장소)에 같은 문구를 넣는다.

## 8. 합의가 필요한 점

- **D1 정본 방식**: (a) 이 제안 — 표지 + release overlay, compose.yml은 정적, 또는 (b) Codex r4/r7이 이미 쓰는
  "compose.yml 제자리 수정을 정본으로"(overlay 금지). (b)는 재생성 명령이 단순하지만, 여러 서비스·에이전트가 한 파일을
  고치고 이력은 `.bak` 파일뿐이며 서비스별 되돌리기가 없다. **권고: (a).** 어느 쪽이든 **둘을 섞지 않는 것**이 핵심이다(지금 weather가 섞여 있다).
- **D2 표지 형태**: 절대 symlink(권고) vs `current/<svc>.txt`(대상 이름 한 줄, `mv` 원자 교체). Codex 도구가 경로를 따라가기 쉬운 쪽.
- **D3 `images.yml`의 image 표기**: ID(`sha256:`, 권고) vs `rel-<sha12>` tag.
- **D4 drift 점검**: 멈춤(권고) vs 경고. 잠금 범위는 project 단위(권고) vs 서비스 단위.
- **D5 권한**: release 디렉터리 `0711` + `images.yml`·`manifest.json` `0644`(권고) vs 모든 읽기를 sudo로; transport(digitie) 배포의 표지 쓰기 경로.
- **D6 transport 마커**: `.release-sha` 3종을 표지 파생 사본으로 둘지 지울지, 그리고 지금의 `3f5160a0` vs `rel-4d49610b2083` 불일치의 해석.
- **D7 compose 기본값**: weather·geo `image:` 줄 삭제(§3)를 할지, Manager 소유 dagster-host 잔존 항목을 compose.yml에서 뺄지.
- **D8 geo ui**: `latest-main` 가변 tag를 geo release에 포함해 ID로 고정할지(권고).

---
작성: Claude(2026-10-28). 근거 조회는 n150 경유 odroid 읽기 전용(`docker ps`/`inspect`, `ls`, release 디렉터리의 `images.yml`·JSON 키). 비밀값은 읽거나 옮기지 않았다.
