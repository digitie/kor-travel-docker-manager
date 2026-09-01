# 범용 관리툴 감사 — 개선 태스크 (GM 트랙)

2026-09-01, main `9916b33` 기준 전체 코드 분석에서 나온 개선 태스크다. 6개 관점
(백엔드 핵심·pin/CLI 계열·API/관측성·프론트엔드·범용성·운영 흐름) 병렬 분석으로 72개
발견을 수집하고, 중복 병합·우선순위화로 20개 태스크로 정리한 뒤, P1/P2 전건을 적대적
검증자가 코드 라인 단위로 재확인했다(기각 0건 — 단 다수가 REVISED로 구현 세부 정정을
받았고, 그 정정이 각 태스크의 **검증 노트**다. 구현 시 검증 노트가 본문보다 우선한다).

작업 브랜치: `refactor/general-mgmt-improvements` (main에서 분기, 완료 후 main 머지).
진행 규율: 우선순위 순 순차 진행, 태스크마다 커밋·원격 push, 주기적 rebase, 의미 단위마다
전문 적대 리뷰어 2명, n150 live E2E 가능 항목은 live로(M05 사이클 lock 경합 주의),
불가 항목은 mock.

- 상태: `[ ]` 미착수 / `[/]` 진행 중 / `[x]` 완료 / `[-]` 보류(사유 명기)

## 태스크 목록

| ID | 상태 | 심각도 | 규모 | 분류 | 검증 | E2E | 제목 |
|:---|:---:|:---:|:---:|:---|:---:|:---|:---|
| GM-01 | `[ ]` | P1 | M | correctness | REVISED | mock | pin rotate/apply-pending/rollback이 v6 execution registry를 갱신하지 않아 복구 불능 stale 상태를 만든다 |
| GM-02 | `[ ]` | P1 | S | correctness | CONFIRMED | mock | 성능 차트에서 6/24/72시간을 선택해도 첫 WS 프레임 도착 즉시 최근 1시간으로 붕괴 |
| GM-03 | `[ ]` | P1 | M | operability | CONFIRMED | n150 live | Manager 자신의 systemd 유닛 부재 — 재부팅·크래시 후 관리면 전체가 수동 개입 전까지 다운 |
| GM-04 | `[ ]` | P1 | M | correctness | REVISED | n150 live | trusted installer가 실행 중인 서비스 발밑의 /opt 트리를 교체하고, 매 설치마다 요청 디렉터리 소유권을 리셋한다 |
| GM-05 | `[ ]` | P1 | M | security | REVISED | mock | 로그인 rate limit이 프록시 뒤에서 단일 전역 버킷으로 붕괴 — 외부인이 관리자 로그인을 지속 봉쇄 가능 |
| GM-06 | `[ ]` | P1 | M | operability | REVISED | mock | 예외 분류가 영어 문장 문자열 비교에 의존하고, CLI --json은 오류 정보를 버리거나 계약을 절반만 지킨다 |
| GM-07 | `[ ]` | P1 | L | correctness | REVISED | n150 live | 백업 복원 CLI 부재 — restore-plan까지만 있고 실제 복구·리허설 경로는 수동 문서 절차뿐 |
| GM-08 | `[ ]` | P2 | M | operability | CONFIRMED | mock | off-box 백업 사본 부재 + pin registry 보존본이 어떤 백업 자동화에도 포함되지 않음 |
| GM-09 | `[ ]` | P2 | S | correctness | REVISED | mock | 신뢰 경로·글로벌 락·root 게이트 상수의 다중 정의 통일 — drift 시 host-wide 락이 조용히 무력화 |
| GM-10 | `[ ]` | P2 | M | correctness | CONFIRMED | mock | root-safe atomic write/fsync 프리미티브 12벌 복제 — execution registry는 디렉터리 fsync 누락으로 crash 시 v6 rename 유실 가능 |
| GM-11 | `[ ]` | P2 | M | correctness | REVISED | mock | docker-targets.yml 스키마 검증 부재 — 오타 하나로 CLI/API 전체가 raw KeyError로 죽고, containers 목록은 손 복사 전이 폐포 |
| GM-12 | `[ ]` | P2 | M | ux | REVISED | mock | API 오류 표면 4종 분열 — app 예외 핸들러로 단일 envelope을 강제하고 프론트 조회 오류 표시를 통일 |
| GM-13 | `[ ]` | P2 | M | operability | REVISED | mock | 백업 API 견고화 — manifest 1개 손상이 목록 전체를 409로 지우고, 재기동 후 이중 pg_dump를 막는 가드가 없다 |
| GM-14 | `[ ]` | P2 | S | operability | REVISED | mock | async 핸들러 안의 동기 SQLite 감사 기록이 event loop 전체를 정지시킬 수 있음 |
| GM-15 | `[ ]` | P2 | M | operability | CONFIRMED | mock | 상태 broadcast가 클라이언트 직렬 전송 — 느린 소켓 하나가 모든 탭의 상태 갱신을 무기한 정지 |
| GM-16 | `[ ]` | P2 | M | observability | CONFIRMED | mock | 모든 백엔드 로그가 두 번씩 기록되고, 요청 상관관계 ID가 없어 UI 오류와 로그·감사를 이을 수 없다 |
| GM-17 | `[ ]` | P2 | L | generality | REVISED | mock | compose candidate 검증의 Map/PinVi 하드코딩 완화 — 14개 서비스 존재 강제와 bind allowlist를 설정으로 외부화 |
| GM-18 | `[ ]` | P2 | M | generality | REVISED | mock | 백업 role과 pinned pair role이 백엔드·프론트 다층 하드코딩 — config 파생으로 전환 |
| GM-19 | `[ ]` | P2 | S | dead-code | REVISED | 불필요 | 죽은 코드 일괄 제거 — 구 C6c 경로 ~650줄, 미사용 프론트 의존성, 무소비 port_policy, 무참조 API key 게이트 |
| GM-20 | `[ ]` | P2 | M | complexity | CONFIRMED | 불필요 | 서비스 계층 분리 1단계 — errors/capabilities 모듈 신설과 프라이빗 크로스 import·순환 의존 해소 |

## GM-01: pin rotate/apply-pending/rollback이 v6 execution registry를 갱신하지 않아 복구 불능 stale 상태를 만든다

- **심각도/규모**: P1 / M · **분류**: correctness · **검증**: REVISED · **E2E**: mock
- **관련 파일**: backend/src/kor_travel_docker_manager/cli.py; backend/src/kor_travel_docker_manager/services/runtime_pin_registry.py; backend/src/kor_travel_docker_manager/services/runtime_execution_registry.py; backend/src/kor_travel_docker_manager/services/runtime_pair_rotation.py

**문제**: `pin rotate`(cli.py:767), `pin apply-pending`(cli.py:1233), `pin rollback`(cli.py:1326)은 v5 source registry만 바꾸는 rotate_runtime_pin/rollback_runtime_pin(runtime_pin_registry.py:1111, 1231)을 호출하고 v6 execution registry를 건드리지 않는다(rotate-pair 경로만 cli.py:792/822에서 rotate_pair_with_execution 사용 — grep으로 확인). migrate-execution-v6를 마친 호스트에서 이 명령이 성공하면 execution.current.source_pinset이 새 pinset과 어긋나고 복구 경로가 전부 막힌다: `pin rebind-execution`은 pinset 불일치로 거부(runtime_execution_registry.py:527-531), `pin migrate-execution-v6`는 registry 존재로 거부(cli.py:622), 같은 pair의 rotate-pair 재시도는 "would not change any revision"으로 거부(runtime_pin_registry.py:1050-1051). apply-pending은 UI 요청 승인의 정식 흐름(docs/docker-management.md 222행)이라 mainline 조작만으로 root registry 수동 수술이 필요한 상태에 빠진다.

**개선안**: 1단계(즉시): v6 execution registry가 존재하는 호스트에서 단일 role 회전 명령 3종을 fail-close로 거부하고 `pin rotate-pair`를 안내한다. 2단계: durable intent + v5/v6 동시 publish를 이미 소유한 rotate_pair_with_execution(runtime_pair_rotation.py:221)에 role 하나만 바뀐 pair를 넘기도록 _cmd_pin_rotate/_cmd_pin_apply_pending_locked/_cmd_pin_rollback을 통일한다. 회귀 테스트: v6 존재 호스트에서 rotate 후 `pin verify`가 0을 반환하고 rebind가 불필요함을 고정.

**실익**: 운영자가 UI 요청 승인 한 번으로 rebuild 전체가 잠기고 root 파일 수술이 필요해지는 사고를 원천 차단한다. pin verify의 "migrate or rebind" 안내가 실제 실행 가능한 안내가 된다.

**검증 노트** (구현 시 본문보다 우선):

문제 자체는 라인 단위로 전부 사실이다: cli.py:767/1233/1326은 v5만 쓰는 rotate_runtime_pin/rollback_runtime_pin(runtime_pin_registry.py:1111-1149, 1231-1279)을 호출하고, v6 가드는 CLI·mutation lock(cli.py:885-972)·registry 함수 어디에도 없다. 결과 경로도 확인됨 — rebind 거부(runtime_execution_registry.py:526-531 "execution rebind source pinset differs"), migrate 거부(cli.py:622), 같은 pair rotate-pair 거부(runtime_pin_registry.py:1050-1051), rebuild fail-close(compose_service.py:693-708 "missing, stale, or terminal"), 그리고 pin verify의 "migrate or rebind" 안내(cli.py:543-549)는 실제로 둘 다 거부되는 죽은 안내다. API는 terminal pinset일 때만 요청 기록을 거부하므로(routes.py:531-547) v6 호스트에서 UI 단일 role 요청→apply-pending이 실제로 이 상태를 만든다.

그러나 두 가지 수정이 필요하다.

(1) "복구 경로가 전부 막힌다/root 수동 수술 필요"는 과장이다. 메인라인 복구 경로가 둘 있다: (a) `pin rollback --to <이전 pinset>` — 잘못된 회전이 이전 registry를 보존하므로(write_runtime_pin_registry preserve_previous=True 기본, runtime_pin_registry.py:936, 945-953) v5를 stale execution의 pinset으로 되돌리면 current_matches가 다시 참이 된다(runtime_execution_registry.py:230-236은 pinset·revision·manager revision만 비교). 단 --block-previous를 썼으면 rollback이 차단 pinset 거부(1250-1251)로 막힌다. (b) 완전히 새로운 pair로의 `rotate-pair`는 staleness 전제조건이 없어 항상 v5/v6를 재동기화한다(runtime_pair_rotation.py:221-284, rotate_execution_source_binding은 "no change"일 때만 거부 — 565-566). 즉 원하던 pinset도 rollback→rotate-pair 두 명령으로 도달 가능하고 파일 수술은 불필요하다. 진짜 결함은 "복구 불능"이 아니라 "시스템의 자체 안내(verify의 migrate-or-rebind, apply-pending 에러 경로)가 이 복구를 전혀 가리키지 않고, --block-previous 시 rollback 경로가 닫혀 승인한 pinset이 도달 불능이 된다"이다. severity P1은 rebuild 전면 봉쇄+오도 안내 근거로 유지 가능.

(2) improvement 수정 필요 3건: (a) 1단계 fail-close는 API 측과 짝지어야 한다 — routes.py:528-530의 원칙("적용 불가능한 요청을 대기 중으로 남기면 그 자체가 거짓말")대로, v6 호스트에서 apply-pending만 거부하면 프로덕션 전체(migrate가 표준 절차, prod-deployment.md:121-122)에서 2-step UI 흐름(docker-management.md:252-258)이 전부 죽은 요청을 쌓는다. 요청 기록 게이트도 함께 바꾸거나 1단계를 건너뛰고 2단계로 직행해야 한다. (b) 2단계 통일 시 terminal 가드를 보존해야 한다: rotate_runtime_pin은 현재 pinset terminal이면 단일 role 회전을 거부하지만(runtime_pin_registry.py:1135-1138, docstring 1121-1126의 M05 pair-incomplete 근거), build_runtime_pin_pair_rotation에는 이 가드가 없다 — role 하나만 바뀐 pair를 그대로 rotate_pair_with_execution에 넘기면 terminal pinset에서 단일 role 탈출이 열려 routes.py:528-547이 의존하는 규율이 깨진다. (c) rollback 통일 시 rotate_execution_source_binding은 v6가 이미 target과 일치하면 "execution source binding did not change"(565-566)로 거부한다 — 이는 정확히 stale 치유형 rollback 사례이므로 idempotent 성공으로 처리해야 하고, rollback은 preserved.release_version(1269)을 쓰는 반면 pair rotation은 current.release_version(1099)을 쓰는 차이도 흡수해야 한다.

회귀 테스트 제안과 effort M은 타당하다(기존 env-var 기반 fixture로 저렴하게 재현 가능 — test_runtime_execution_registry.py:300-366 참조). 더 값싼 대안인 rebind 완화는 v6 감사 계약(rebind는 동일 source pinset의 Manager release 교체 전용)을 무너뜨리므로 기각 — durable intent transaction으로의 통일이 기존 rotate-pair 수정(journal.md:333-337)과 일관된 올바른 방향이다.


## GM-02: 성능 차트에서 6/24/72시간을 선택해도 첫 WS 프레임 도착 즉시 최근 1시간으로 붕괴

- **심각도/규모**: P1 / S · **분류**: correctness · **검증**: CONFIRMED · **E2E**: mock
- **관련 파일**: frontend/src/components/DashboardClient.tsx

**문제**: combinedChartData 메모(DashboardClient.tsx:645-663)는 wsMetricsPoints가 1개라도 생기면 히스토리+실시간 병합 후 무조건 마지막 360포인트로 자른다(659-661행 `merged.slice(merged.length - 360)`, 10초 샘플 기준 1시간 — 코드로 확인). 차트 모달이 열려 있고 WS가 연결돼 있으면 ~10초 안에 실시간 포인트가 들어오므로, 기간 셀렉터(1454-1464행)에서 24시간을 골라도 헤더는 '최근 24시간'이라 쓰면서 실제로는 1시간치만 그린다. 운영자가 '어젯밤 CPU 스파이크'를 확인하려는 핵심 시나리오가 스스로 무효화되고, 24시간을 보고 '문제 없었다'고 오판할 수 있다.

**개선안**: 포인트 상한을 chartHours 비례(chartHours*360)로 계산하거나 상한을 시간 기준으로 바꿔 `timestamp >= now - chartHours`로 오래된 쪽만 잘라낸다. 실시간 append는 유지한다. 단위 테스트: 히스토리 8,640포인트 + WS 1포인트 병합 시 선택 기간의 길이가 보존되는지 검증.

**실익**: 장애 회고의 핵심 기능이 실제로 동작하게 된다. 반나절 수정으로 P1 오판 경로가 닫히는 최고 효율 항목.

**검증 노트** (구현 시 본문보다 우선):

문제 재현을 라인 단위로 확인함. (1) DashboardClient.tsx:646 `if (wsMetricsPoints.length === 0) return queryChartData;` — WS 포인트가 1개라도 생기면 659-661행 `if (merged.length > 360) return merged.slice(merged.length - 360)`이 무조건 적용된다. (2) 샘플 주기 10초는 backend metrics_collector.py:372 `await asyncio.sleep(10)`으로 확인, metrics_service.get_recent_metrics(53-80행)는 다운샘플링 없이 raw row를 반환하므로 24시간이면 ~8,640포인트가 실제로 온다. (3) WS 포인트 유입은 455-479행 — 모달이 열려 있으면 상시 status WS 프레임마다 append되므로 ~10초 내 붕괴가 맞다. (4) 기간 셀렉터 onChange(1457행)는 setChartHours만 호출하고 wsMetricsPoints를 비우지 않으며(비움은 187/827/1473행 open/close/Escape에서만), 헤더 1444행은 `최근 ${chartHours}시간`을 그대로 표기 — '24시간이라 쓰고 1시간만 그림' 시나리오 정확함. 개선안도 타당하나 구현 시 주의 3가지: (a) chartHours*360 상한 방식이 안전하며, memo deps [queryChartData, wsMetricsPoints](663행)에 chartHours 추가 필요. (b) 시간 기준 컷오프 방식을 고르면 timestamp가 zone 표기 없는 UTC 문자열("%Y-%m-%d %H:%M:%S", metrics_service.py:72)이라 naive Date 파싱 시 KST에서 9시간 오차로 차트가 비는 함정이 있음 — 사전식 문자열 비교나 'Z' 부착 필수. (c) frontend/package.json에 jest/vitest 등 테스트 하니스가 전무하고 프로젝트 자체 *.test.* 파일도 없음 — 제안된 단위 테스트는 병합 로직을 순수 함수로 추출 + 러너 도입이 선행돼야 하며, 이 부분이 effort S의 대부분을 차지함(e2e: mock 폴백도 repo에 playwright.config 없음). 계약(보안 게이트/lock/M05/ADR) 저촉 없음 — 순수 프론트 표시 로직.


## GM-03: Manager 자신의 systemd 유닛 부재 — 재부팅·크래시 후 관리면 전체가 수동 개입 전까지 다운

- **심각도/규모**: P1 / M · **분류**: operability · **검증**: CONFIRMED · **E2E**: n150 live
- **관련 파일**: docs/prod-deployment.md; deploy/tmpfiles.d/kor-travel-docker-manager.conf; scripts/install-ktdm-trusted-release; scripts/run-standalone-backup.sh

**문제**: 운영 기동이 `nohup setsid ... uvicorn > /tmp/ktdm_backend.log &`(docs/prod-deployment.md:240-243)과 `nohup setsid npm run start`(:389)뿐이고 deploy/에는 tmpfiles.d 유닛 하나만 있다. 관리 대상 컨테이너는 restart: unless-stopped로 살아나지만 Manager 자신은 재부팅 시 아무도 다시 띄우지 않는다. journal.md:4115에 pkill 권한 부족으로 재기동이 조용히 실패해 옛 코드가 계속 떠 있던 실사고 기록이 있다. 부수 문제로 로그가 /tmp에 있어 재부팅(진단이 가장 필요한 순간) 직후 증발하고, cron 백업 로그는 `>>` append로 영구 누적된다(run-standalone-backup.sh:9-11).

**개선안**: deploy/systemd/에 ktdm-backend.service·ktdm-frontend.service(Restart=on-failure, WantedBy=multi-user.target, WorkingDirectory=/opt/kor-travel-docker-manager)를 추가하고, trusted installer가 tmpfiles 유닛과 같은 방식(root 소유 검증 후 install + daemon-reload)으로 설치·갱신하게 한다. prod-deployment.md의 nohup/pkill 절차를 systemctl restart로 교체한다. journald 편입으로 /tmp 로그 증발이 함께 해소되며, cron 백업 로그에는 deploy/logrotate.d 설정을 추가한다.

**실익**: 재부팅·크래시 자동 복구, '재기동했다고 믿었는데 옛 코드가 떠 있는' 실사고 유형 제거, 로그 보존·로테이션까지 동시 해소. GM-04의 재기동 훅 선행 조건이다.

**검증 노트** (구현 시 본문보다 우선):

문제 기술 전부 실측 일치: prod-deployment.md:240-243 `nohup setsid env PYTHONPATH=src .venv/bin/python -m uvicorn ... > /tmp/ktdm_backend.log 2>&1 &`, :389 `nohup setsid npm run start > /tmp/ktdm_frontend.log 2>&1 &`, deploy/에는 tmpfiles.d 유닛 하나뿐, journal.md:4115-4116 pkill 실사고 기록 정확 인용, journal.md:4119가 n150 /tmp=7.5GB tmpfs임을 확인(재부팅 시 로그 증발 실재), run-standalone-backup.sh:9-11 crontab 권고가 `>>` append 무로테이션, docker-compose.yml은 관리 대상에 restart: unless-stopped. 계약 충돌 없음: installer의 install_host_lease_tmpfiles(scripts/install-ktdm-trusted-release:1415-1455, 조기 적용 :616-643)가 제안과 동일한 root 소유 검증 후 install 패턴이고, ADR-41/prod-deployment.md:379는 backend root 실행을 현상으로 인정하며 NONROOT-BACKEND와 독립, decisions.md:2389 "systemd unit이 없어 재기동 불필요"는 결정이 아니라 현상 기술. 구현 시 반영할 비차단 nit 4건: (1) pkill은 prod-deployment.md에 없음 — 재기동 절차는 교체가 아니라 신설, (2) WorkingDirectory는 유닛별로(backend는 backend/, frontend는 frontend/; .env는 main.py:32-34 get_env_path()라 cwd 무관), (3) prod-deployment.md:367 "유일한 /opt 밖 산출물"과 decisions.md:2389 ⑤가 거짓이 되므로 동시 갱신 필요 + 설치 후 systemctl restart 전까지 옛 코드가 돈다는 점 명시(GM-04 전 단계), (4) frontend 유닛은 User= 비root 지정 — 기본 root는 현행 대비 권한 확대. effort M 현실적, cron @reboot 등 값싼 대안은 크래시 복구·조용한 재기동 실패 유형을 못 덮음.


## GM-04: trusted installer가 실행 중인 서비스 발밑의 /opt 트리를 교체하고, 매 설치마다 요청 디렉터리 소유권을 리셋한다

- **심각도/규모**: P1 / M · **분류**: correctness · **검증**: REVISED · **E2E**: n150 live
- **관련 파일**: scripts/install-ktdm-trusted-release; docs/prod-deployment.md; scripts/verify-frontend-toolchain.sh

**문제**: commit 경로가 `mv -T "${APP_ROOT}" "${ROLLBACK}"` 후 새 트리로 교체하고(install-ktdm-trusted-release:1369-1372 — 코드로 확인) reconcile에서 구 트리를 shutil.rmtree로 삭제한다(:429-444). 백엔드 uvicorn과 next start는 그 트리에서 실행 중일 수 있고 next는 route 번들을 요청 시점에 lazy 해석하므로(verify-frontend-toolchain.sh:16-19가 경고), 설치 순간부터 수동 재기동까지 서비스는 반파손 상태로 돈다. installer도 문서도 '설치 전 중지/설치 후 재기동'을 강제하지 않는다. 별도로 `install -d -o root -g root -m 0700 "${REQUEST_ROOT}"`(:763)가 기존 디렉터리에도 소유자를 재적용해, 비-root backend 호스트(tasks.md:16의 활성 태스크)에서 업그레이드마다 pin 회전 요청이 소리 없이 503으로 돌아간다.

**개선안**: (1) preflight에서 /proc 스캔으로 cwd·exe·open fd가 APP_ROOT 아래인 프로세스를 탐지해 fail-close하고 --allow-live 명시 승인만 우회를 허용한다. (2) commit 직후 재기동 훅을 추가한다(GM-03의 systemd 도입 후 systemctl restart). (3) REQUEST_ROOT는 최초 생성 시에만 root:root 0700으로 만들고, 기존 존재 시 소유권을 보존한 채 안전성(symlink 아님, group/other 쓰기 없음)만 검증한다.

**실익**: release 설치가 실행 중 서비스를 몰래 부수는 창과, 예정된 non-root backend 전환이 매 release마다 되돌아가는 함정이 함께 제거된다.

**검증 노트** (구현 시 본문보다 우선):

문제 1은 라인 단위로 정확하다: install-ktdm-trusted-release:1369-1372의 `mv -T "${APP_ROOT}" "${ROLLBACK}"` → 새 트리 교체, :1401-1403의 즉시 reconcile → reconcile_committed(:429-444)가 remove_tree_by_identity → shutil.rmtree(:390)로 구 트리를 삭제하며, installer 전체에 fuser/lsof//proc 류 실행-중 프로세스 가드가 없다(grep 부재 확인). 서비스는 실제로 그 트리에서 돈다(prod-deployment.md:238-243 uvicorn nohup, :383-390 next start; systemd 없음). 문서는 중지/재기동을 강제하지 않을 뿐 아니라 decisions.md:2388-2390이 "⑤ Manager용 systemd unit이 없어 재기동이 필요 없고"라고 적극적으로 주장한다 — 태스크는 이 stale 문구 정정도 files에 포함해야 한다. 수정 필요 사항: (a) 문제 2의 "소리 없이 503"은 과장이다 — 소유권 리셋은 의도된 동작으로 세 곳에 문서화돼 있고(installer:756-762 주석에 정확한 chown 명령, prod-deployment.md:51-57, runtime-pin-registry.md:494-498), 실패는 routes.py:566-573(409 RUNTIME_PIN_REQUEST_UNREADABLE)·:598-613(503 + 정확한 복구 명령 포함 메시지)·감사 기록(:445-453)·상태 API "unreadable"(:309-312)로 시끄럽게 fail-close한다. 현재 backend는 root로 돌아(runtime_pin_request.py docstring 10-15행, prod-deployment.md:379) 미래 태스크(tasks.md:16)에 대한 함정이지 현재 장애가 아니다. "문서화된 매-설치 수동 chown 단계가 non-root 전환 후 반복 회귀 함정"으로 다시 기술할 것. (b) improvement (2)는 프론트엔드가 단순 재기동 불가라는 점을 놓쳤다 — staging 트리는 clean checkout이라 node_modules/.next가 없어 restart 전에 npm ci + next build가 선행돼야 하며(prod-deployment.md:386-389), GM-03 의존도 명시적 전제다. (c) improvement (1)/(3)은 견고하다: (1)은 저장소 철학("사람의 기억이 아니라 설치가 책임진다", installer:1407-1413)과 일치하고, (3)은 backend 자체 무결성 검사(_open_verified_request_file :237-241, :255-258)와 정합하며 /var/lib가 root 0755라 기존 디렉터리는 root가 만든 것이므로 소유권 보존이 신뢰 모델을 깨지 않는다. GNU install -d가 기존 디렉터리에 mode/owner를 재적용함은 WSL 실측으로 확인(0755→0700 재적용, 내용 보존). effort M은 (1)+(3)에는 타당하나 (2)의 frontend build 처리까지 포함하면 상한이다.


## GM-05: 로그인 rate limit이 프록시 뒤에서 단일 전역 버킷으로 붕괴 — 외부인이 관리자 로그인을 지속 봉쇄 가능

- **심각도/규모**: P1 / M · **분류**: security · **검증**: REVISED · **E2E**: mock
- **관련 파일**: backend/src/kor_travel_docker_manager/services/auth_service.py; backend/src/kor_travel_docker_manager/api/websocket.py; backend/src/kor_travel_docker_manager/api/admin.py

**문제**: check_login_rate_limit(auth_service.py:229)은 client_ip_hash로 실패를 집계하는데(코드로 확인), _client_ip(:453-463)는 신뢰 프록시(기본 loopback CIDR + 시크릿)일 때만 X-Forwarded-For를 믿는다. websocket.py:46-50의 자체 주석이 인정하듯 현재 prod는 신뢰 CIDR이 loopback 전용이라 공개 트래픽 전체가 HAProxy 엣지 IP 하나로 해시된다. 인터넷의 아무나 10분 창에 잘못된 로그인 5회를 보내면 진짜 관리자의 로그인과 비밀번호 변경(admin.py:125가 같은 버킷 공유)이 함께 429로 잠기고, 반복하면 관리 대시보드 접근을 사실상 무기한 봉쇄할 수 있다.

**개선안**: (1) 운영 문서에서 KTDM_TRUSTED_PROXY_CIDRS/SECRET 설정을 필수 절차로 승격하고, 배포 readiness 점검에 'X-Forwarded-For가 오는데 신뢰 프록시 설정이 없다' misconfiguration 감지를 추가해 화면·감사에 노출한다. (2) 코드 측에서는 신뢰되지 않은 동일 IP로 client_hash가 수렴할 때 attempted_username(또는 UA 해시)을 보조 키로 섞어 전역 버킷을 분리하거나, 최소한 429 응답과 감사 행에 'shared-IP bucket' 사실을 명시한다.

**실익**: 외부인이 관리자를 프로덕션 관리 UI에서 잠가버리는 DoS 경로가 사라지고, 어떤 프록시 토폴로지에 놓여도 rate limit이 의도대로 동작하는 범용 도구가 된다.

**검증 노트** (구현 시 본문보다 우선):

문제 자체는 라인 단위로 전부 사실이다. check_login_rate_limit은 client_ip_hash 단일 키다(auth_service.py:238 `client_hash = _client_ip_hash(request)`, :247/:259 where 절). auth.py:34가 검증(auth.py:52 `verify_admin_password`) 이전에 429를 던지므로 잠긴 버킷에서는 관리자의 올바른 비밀번호도 도달하지 못해 '성공 시 리셋'(auth_service.py:244-255) 탈출구가 공개 경로에서 막힌다. admin.py:125도 같은 버킷 공유 확인. prod 토폴로지 전제도 저장소 스스로 인정한다: websocket.py:46-50, docs/docker-management.md:83-87("신뢰 프록시 CIDR이 loopback 전용이라 X-Forwarded-For를 신뢰하지 않는다"), journal.md:5006-5008("운영 로그에서 실제로 모든 외부 접속이 라우터 IP로 관측됨"). docs/prod-deployment.md에는 KTDM_TRUSTED_PROXY_CIDRS/SECRET 언급이 0건이고 .env.example:19-23은 loopback 기본+secret 선택이므로 개선안 (1)의 문서 격상·misconfiguration 감지는 실제 공백이며, 감지를 꽂을 readiness 표면도 이미 있다(routes.py:412 GET /deployment-readiness, services/deployment_readiness.py). 단 개선안 (2)는 수정이 필요하다. (a) UA 해시 보조 키는 역효과다 — UA는 공격자 통제 입력이라 요청마다 UA를 바꾸면 시도당 새 버킷이 생겨 T-021/AUTH-6(docs/tasks-done.md:568-571)이 의도적으로 도입한 durable brute-force 한도가 완전히 무력화된다. (b) attempted_username 보조 키는 의도적 DoS를 못 막는다 — 공격자가 기본 관리자명 "admin"(auth_service.py:48-49 기본값)을 그대로 쓰면 (공유IP, admin) 버킷이 여전히 진짜 관리자를 잠근다. 무작위 username 스팸 노이즈 필터일 뿐 DoS 해결책으로 팔면 안 된다. (c) 반면 '429 응답·감사 행에 shared-IP bucket 명시' 폴백과 개선안 (1)은 견고하고 ADR(decisions.md:604 신뢰 프록시에서만 X-Forwarded-* 반영)과도 정합한다. 더 값싼 1차 해법은 코드 변경 0인 prod 설정이다: KTDM_TRUSTED_PROXY_CIDRS=<프록시IP>/32 + KTDM_TRUSTED_PROXY_SECRET(auth_service.py:499-515가 이미 지원, docker-management.md:86-87이 이미 필수 조건으로 명시) — 광역 CIDR(예: /24)은 LAN 피어의 XFF 위조로 rate limit 우회가 되므로 exact /32+secret을 문서에 못 박아야 한다. 심각도도 소폭 과장: 봉쇄는 WAN 경로 한정이고 LAN 직결 클라이언트는 자기 IP 버킷이며 운영자는 SSH/loopback 복구 수단을 유지한다(원격 관리자 DoS로는 여전히 실질 P1급). 권고 수정: (2)에서 UA 키 혼합 삭제, username 혼합은 '노이즈 필터'로 강등, 1차 해법을 prod 프록시 설정(+HAProxy 엣지 per-소스IP 제한, docker-management.md:88-90이 이미 연결 수 제한을 엣지에 위임)으로 명시. effort M은 readiness 감지+화면 노출+테스트 포함이면 현실적이고, (2) 키 혼합을 빼면 S~M로 줄어든다.


## GM-06: 예외 분류가 영어 문장 문자열 비교에 의존하고, CLI --json은 오류 정보를 버리거나 계약을 절반만 지킨다

- **심각도/규모**: P1 / M · **분류**: operability · **검증**: REVISED · **E2E**: mock
- **관련 파일**: backend/src/kor_travel_docker_manager/services/compose_service.py; backend/src/kor_travel_docker_manager/services/c6c_deployment.py; backend/src/kor_travel_docker_manager/cli.py

**문제**: DeploymentContractError raise가 세 서비스 파일에만 508곳인데 계층이 평평해 분류가 메시지 문자열 매칭으로 이뤄진다: compose_service.py:5285는 `str(exc) == "PinVi sealed role topology is noncanonical"`으로 terminal(재시도 금지) 여부를 정하고, cli.py:1006은 `"no backup" in str(exc)`, cli.py:1242는 `"pair rotation" in str(exc)`로 exit code를 정한다. 문구 하나를 다듬으면 terminal이 재시도 가능으로 조용히 오분류된다. `ktdctl pinvi-pair --json`은 실패 시 {"status":"failed","classification":"unclassified"}만 내고 메시지를 버리며(cli.py:210-218), cli.py:996-997이 문서화한 '--json은 어떤 경로에서도 stdout에 JSON만' 계약을 `pin rotate`(774-776)·`rotate-pair`(830-832)·block·rollback·init·verify는 어겨 stderr 텍스트만 내고 stdout이 비어 `| jq` 스크립트가 죽는다.

**개선안**: DeploymentContractError에 기계 판독 code 필드를 표준화(ComposeCandidateContractError의 기존 패턴 재사용)하고 raise 사이트를 점진적으로 code 지정으로 옮긴다. 5285의 문자열 비교는 전용 서브클래스(PinviTopologyNoncanonicalError)로, cli.py의 두 in-str 매칭은 code 판정으로 대체한다. CLI에는 공통 오류 helper를 두어 --json이면 모든 pin/pinvi-pair 명령이 실패 시에도 stdout에 {"status":"failed","code":...,"detail":비밀 없는 메시지}를 내게 한다. GM-12(API envelope)와 GM-20(errors.py 분리)의 선행 작업이다.

**실익**: n150 운영자와 자동화가 JSON만으로 terminal/재시도 가능/설정 오류를 구분해 다음 조치(pin rotate vs 재실행 vs env 수정)를 정할 수 있고, 문구 수정이 판정 로직을 깨는 사고가 사라진다.

**검증 노트** (구현 시 본문보다 우선):

방향(기계 판독 code 표준화 + CLI 공통 오류 helper)은 견고하고 ADR 철학과도 정합하나("typed error class는 production raw log를 보존하지 않고도 exact test fixture와 코드 수정으로 연결된다" — docs/decisions.md:1320), 세 가지를 고쳐야 한다.

[1] compose_service.py:5285의 귀결 주장이 틀렸다. `str(exc) == "PinVi sealed role topology is noncanonical"`(5285)은 문자열 비교가 맞지만, else 분기도 `role_topology_unavailable` code로 PinviRoleLifecycleBlock을 만들어 `_PinviRoleLifecycleError`를 raise하고(5281-5294), admission 검사는 code와 무관하게 `journal.pinvi_role_lifecycle_block is not None`이면 차단한다(5993-6003). 즉 문구가 바뀌어도 terminal→재시도 가능 오분류는 일어나지 않고, receipt code가 noncanonical↔unavailable로 왜곡되는 진단 오류만 남는다. 태스크가 놓친 진짜 terminal 결정 문자열 매칭은 `_pinvi_lifecycle_diagnostic`(5074-5081, `f"; pinvi_role:{code})" in message` 접미사 파싱)과 `_pinvi_role_topology_block`(5091-5095, diagnostic == "role_topology_noncanonical"일 때만 terminal block 생성)이다 — 여기서는 메시지 조립 형식(예: `f"({diagnostic})"` 괄호, 5410/5420/5444)이 어긋나면 open/seal 단계 terminal이 조용히 비terminal로 강등된다. 개선안의 예시 근거를 이 지점으로 교체해야 한다.

[2] cli.py:1242 `"pair rotation" in str(exc)`는 exit code를 정하지 않는다 — 어느 분기든 return 2이고(1249), 매칭은 해소 힌트 출력 여부만 정한다(1243-1248). exit code를 메시지로 정하는 곳은 cli.py:1006 하나다(확인됨; 매칭 문구는 standalone_backup.py:532/540). 사실관계 축소 필요.

[3] pinvi-pair --json이 메시지를 "버리는" 것은 결함이 아니라 테스트로 고정된 의도적 보안 결정이다. test_docker_manager_cli.py:504-520 `test_cli_rebuild_pinned_runtime_hides_unclassified_contract_error_in_json`은 "sensitive unexpected contract detail"이 stdout에 없고 stderr도 비어 있음을 단언한다. compose_service.py:5273 주석("verifier 원문과 reason enum은 receipt·CLI에 보존하지 않는다")도 같은 결이다. 따라서 개선안의 "detail: 비밀 없는 메시지" 필드는 unclassified 오류에 raw str(exc)를 넣는 순간 이 계약·테스트를 깬다 — helper는 typed code만 내보내고, detail은 secret-free가 보장된 typed 서브클래스에 한정하도록 수정해야 한다(참고로 show-pending:1049와 restore-plan:1001은 이미 str(exc)를 JSON에 넣고 있어 전례가 혼재한다 — 이 불일치 정리도 태스크 범위에 명시할 것).

부수 정정: (a) "세 서비스 파일에만 508곳"은 부정확 — `raise DeploymentContractError`는 13개 파일 998곳이고, 태스크가 지목한 세 파일 합은 compose_service 245 + c6c_deployment 193 + cli 7 = 445다. 계층도 완전 평평하지 않다(ComposeCandidateContractError.code, ComposePostMutationContractError.code, PinnedRuntimePrejournalFailure.stage, _PinviRoleLifecycleError.role_topology_block 등 구조화 서브클래스 실존 — 개선안의 "기존 패턴 재사용"은 유효). (b) "cli.py:996-997이 문서화한 계약"은 같은 파일 내 지역 주석이지 docs의 외부 계약이 아니다 — "계약 위반"보다 "내부 관례 불일치"가 정확하며, 불일치 자체는 7개 명령(rotate 774-776, rotate-pair 830-832, block 854-856, rollback 1331-1333, init 433-435, verify 485-487, show 474-476)에서 실재함을 확인했다. (c) `| jq`는 빈 stdout에서 죽지 않는다(exit 0) — `jq -e`나 후속 파싱이 실패하는 것이 정확한 표현. effort M은 범위(공통 helper + 3개 판정 지점 교체 + code 필드 표준)에 현실적이고, lock 규율·M05 동결과의 충돌은 없다(출력 계층 변경이며 code 필드는 additive).


## GM-07: 백업 복원 CLI 부재 — restore-plan까지만 있고 실제 복구·리허설 경로는 수동 문서 절차뿐

- **심각도/규모**: P1 / L · **분류**: correctness · **검증**: REVISED · **E2E**: n150 live
- **관련 파일**: backend/src/kor_travel_docker_manager/services/standalone_backup.py; backend/src/kor_travel_docker_manager/cli.py; docs/docker-management.md

**문제**: standalone_backup.py는 create/list/gc/restore-plan만 구현하고 '복원 자체는 아직 구현하지 않는다'고 명시한다(:479-484, :522 — 코드로 확인). docs/docker-management.md:1160-1162도 복원 CLI 부재를 인정한다. 여섯 role의 백업이 매일 쌓이지만 이 도구로 복원 리허설을 한 번도 해본 적 없는 백업이며, 장애 시 운영자는 각 프로젝트의 수동 pg_restore 절차를 처음 밟게 된다. 검증 안 된 백업은 백업이 아니다.

**개선안**: restore-plan을 게이트로 삼는 `ktdctl db-backup restore <role> --confirm`(blocking finding 있으면 거부)과, 운영 DB를 건드리지 않는 scratch 리허설 모드(임시 DB에 pg_restore 후 TOC/row sanity 확인 뒤 drop)를 구현한다. journal 2026-08-03의 _rehearse_database_restore/restore_database_backup 유산 코드 경험을 재사용한다. 후속: cron에 리허설을 걸어 복구 가능성을 상시 감시하고, 결과를 GET /api/v1/backups에 노출한다.

**실익**: 백업이 '복원 가능함이 증명된 백업'이 되고 장애 시 MTTR이 수동 문서 절차 대비 크게 줄어든다.

**검증 노트** (구현 시 본문보다 우선):

핵심 공백 주장은 코드로 확인됨: standalone_backup.py:481 "복원 자체는 아직 구현하지 않는다", :522 "복원 **계획**만 만든다", cli.py:1666-1714에 create/list/gc/restore-plan만 존재, docs/docker-management.md:1158-1162 "복원 CLI가 없다", routes.py:162-163 "Restore itself is still unimplemented". decisions.md:1659-1660(F1D ADR)이 "data recovery는 final-schema backup 또는 source/ETL 재적재 workflow로 분리된다"고 명시하므로 독립 restore CLI는 C6c/rebuild-pinned 계약과 충돌하지 않는다. 그러나 수정 필요 5건: (1) "여섯 role의 백업이 매일" — docker-management.md:1148-1152상 cron은 geo_dagster/concierge/pinvi 3개 role뿐이고 geo는 앱 레벨 백업 정본, Map 두 role은 kor-travel-map #148 소유로 의도적 제외. (2) 인용한 유산 코드 _rehearse_database_restore/restore_database_backup은 현재 트리에 없다(grep: docs/journal.md에만 존재; 커밋 21dddba "remove legacy pair orchestration"에서 제거). 재사용할 진짜 선례는 태스크가 누락한 T-055 커밋 d262a6f — ktdctl db-backup restore --confirm + _STANDALONE_RESTORE_CAPABILITY 2중 방어 + --expected-schema-revision fail-close를 구현·병합 후 v5 리팩터에서 제거된 코드다. (3) 게이트 설계 결함: HEAD_MISMATCH는 의도적 non-blocking이라(standalone_backup.py:627-636, journal 2026-08-28 KUM-M13 "schema revision 불일치는 차단이 아니다") "blocking finding 거부"만으로는 schema 역행 복원이 --confirm 하나로 통과 — T-055의 --expected-schema-revision 명시 opt-in 패턴이 필요. (4) ktdctl-ui-migration.md:801-804 [v3] 경계 누락: restore 범위는 "전용 DB 데이터 복원"이며 pinned generation 롤백 수단이 아님을 명문화해야 하고, 같은 문서 :800이 요구하는 "role별 정지/기동 절차 설계"(복원 중 writer 처리)가 improvement에 없다 — 실제 어려운 부분이다. (5) cron 상시 리허설은 실측 비용 재검토 필요: map_application pg_restore 단독 ~97분(journal 2026-08-03), 스트리밍 archive라 --jobs 병렬화 불가, scratch DB는 인스턴스 볼륨 2배 디스크(geo 33GB급) — role별 opt-in/주기 설계가 빠졌다. 참고로 이 공백은 미인지가 아니라 오너가 의도적으로 순서를 미룬 로드맵 확정 항목이다(journal 2026-08-28 "오너 결정에 따라 파괴적 복원은 뒤로 미루고", ktdctl-ui-migration.md:792 "로드맵 편입 확정 Q6") — effort L은 그 문서의 300-500줄+절차 설계 추정과 부합해 현실적이다.


## GM-08: off-box 백업 사본 부재 + pin registry 보존본이 어떤 백업 자동화에도 포함되지 않음

- **심각도/규모**: P2 / M · **분류**: operability · **검증**: CONFIRMED · **E2E**: mock
- **관련 파일**: docs/docker-management.md; docs/prod-deployment.md; scripts/run-standalone-backup.sh; backend/src/kor_travel_docker_manager/services/standalone_backup.py

**문제**: docker-management.md:1163-1165가 인정하듯 백업은 KTDM_BACKUP_ROOT 로컬 경로뿐이라 호스트 디스크 유실 시 DB와 백업이 함께 사라진다(tasks.md:13에 진행 중 등재). 추가로 prod-deployment.md:82-84가 '백업·보존 대상'으로 명시한 runtime-pins.json과 runtime-pins.<digest>.json 보존본(pin rollback의 유일한 소스, git 밖)은 standalone backup 대상 6개 role 어디에도 포함되지 않아 유실 시 롤백 경로가 통째로 사라진다.

**개선안**: systemd timer(GM-03 이후) 또는 cron 기반 rsync/scp off-box 전송 + 원격 `sha256sum -c` 대조를 결선하고, 전송 대상에 /var/lib/kor-travel-docker-manager/runtime-pins*.json과 public 사본 디렉터리를 반드시 포함시킨다. 전송 성공/최신성을 manifest에 기록해 대시보드 백업 패널에서 보이게 한다. 이미 진행 중 태스크이므로 pin registry 포함이 설계에서 빠지지 않게 하는 것이 핵심이다.

**실익**: 단일 호스트 장애가 데이터·백업·pin 롤백 소스를 동시에 삼키는 시나리오가 제거된다.

**검증 노트** (구현 시 본문보다 우선):

모든 인용이 라인 단위로 정확하다. (1) docker-management.md:1163-1165 "외부(오프박스) 사본 자동화가 없다... rsync/scp 대상·주기·sha256 대조 검증은 별도 결선이 필요하다" 실재, 저장소 전체 grep에서 전송 자동화 부재 확인. (2) tasks.md:13 "[/] standalone backup 운영 보강 — off-box 사본 자동화" 진행 중 등재, journal.md:2733-2735가 off-box AC 미결을 명시. (3) prod-deployment.md:82-84 "백업·보존 대상: ...runtime-pins.<digest>.json 보존본(= 회전 이력이자 pin rollback의 유일한 소스). git 밖" 실재. (4) '유일한 소스'는 코드로도 사실: runtime_pin_registry.py:1252-1255의 rollback_runtime_pin은 _preserved_copy_path만 읽고, PinRotation history에는 revision이 없어 보존본 유실 시 재구성 불가. (5) standalone_backup.py:41-57의 6개 role(geo/geo_dagster/concierge/map_application/map_dagster/pinvi)은 전부 pg_dump 대상이고 pin registry 파일은 어디에도 없으며 run-standalone-backup.sh도 db-backup create/gc만 호출. (6) 결정적으로 decisions.md:2529-2530(ADR-40 트레이드오프)이 "보존본이 git처럼 분산 백업되지 않으므로 백업 대상 등재가 필요하다"고 이미 자인 — 태스크는 프로젝트 스스로 약속한 후속이다. 계약 충돌 없음: 전송은 읽기 전용이라 mutation 경계(생성·GC CLI 전용) 유지, registry payload는 비밀 없음(--reason은 world-readable 공개 사본에 기록되는 계약), _read_manifest(standalone_backup.py:896-946)는 지정 키만 읽고 여분 필드를 거부하지 않아 전송 상태 기록이 fail-close 파싱을 깨지 않는다. 경미한 참고: 공개 사본(/var/lib/kor-travel-docker-manager-public/)은 publish_runtime_pins로 재생성 가능한 파생 상태라 '반드시'는 과함(포함해도 무해); registry·보존본은 root 0600이라 전송 작업이 root 실행 또는 root staging 필요; manifest 갱신은 기존 role lock을 지켜야 한다. effort M은 원격 sha256 대조·대시보드 노출 포함 시 현실적.


## GM-09: 신뢰 경로·글로벌 락·root 게이트 상수의 다중 정의 통일 — drift 시 host-wide 락이 조용히 무력화

- **심각도/규모**: P2 / S · **분류**: correctness · **검증**: REVISED · **E2E**: mock
- **관련 파일**: backend/src/kor_travel_docker_manager/cli.py; backend/src/kor_travel_docker_manager/services/c6c_deployment.py; backend/src/kor_travel_docker_manager/services/compose_service.py; backend/src/kor_travel_docker_manager/services/runtime_pin_request.py; backend/src/kor_travel_docker_manager/services/runtime_pin_registry.py; backend/src/kor_travel_docker_manager/services/pinned_runtime_generation.py; backend/src/kor_travel_docker_manager/services/registry.py; backend/src/kor_travel_docker_manager/api/routes.py

**문제**: (1) global mutation lock 경로 `/run/lock/kor-travel-docker-manager/global-mutation.lock`과 상속 FD env가 cli.py:81-82와 c6c_deployment.py:112/2436/1838에 별도 리터럴로 존재하는데, _runtime_pin_mutation_lock(cli.py:939-949)은 lock 파일 FileNotFoundError를 개발 환경으로 간주해 락 없이 진행하므로 한쪽 리터럴만 바뀌면 rebuild와 pin 회전이 직렬화 없이 동시 mutation한다 — 락 부재가 통과 경로라 어긋남이 관측되지 않는다. (2) _running_from_trusted_install_root 3개 구현이 서로 다른 판정을 낸다(runtime_pin_registry.py:556-577 sys.prefix 특례, runtime_pin_request.py:187-191 project root만, pinned_runtime_generation.py:3933-3941 __file__ prefix) — wheel 직접 실행 시 backend가 쓴 회전 요청을 root CLI가 다른 경로에서 찾는 latent 불일치. (3) _require_pinned_runtime_rebuild_root(compose_service.py:653, c6c_deployment.py:2210)와 get_project_root(compose_service.py:711, registry.py:8)가 문자 그대로 2벌. (4) `sudo -n backend/.venv/bin/ktdctl ...` 운영 안내 리터럴이 7개 파일 14곳에 하드코딩돼 UI/API로 그대로 노출된다.

**개선안**: services/trusted_install.py를 신설해 TRUSTED_INSTALL_ROOT/STATE_ROOT/REQUEST_ROOT 상수, 가장 견고한 설치 판정(__file__ 기반 + sys.prefix 특례), GLOBAL_MUTATION_LOCK_PATH/LOCK_FD_ENV, require_pinned_runtime_rebuild_root(), 운영 명령 생성 함수(pin_verify_command() 등 절대경로 기반, 개발 체크아웃 fallback 포함)를 모으고 전 소비처가 import한다. get_project_root는 registry.py 정본으로 통일한다. 동일성 회귀 테스트로 cli/c6c가 같은 락 상수 객체를 참조함을 고정한다.

**실익**: host-wide mutation 직렬화가 상수 drift로 무력화되는 경로, 설치 형태에 따라 모듈마다 다른 경로를 보는 사고, UI가 보여주는 복사-실행 명령의 부정확성이 한 번에 제거된다. GM-20 파일 분리의 기반이기도 하다.

**검증 노트** (구현 시 본문보다 우선):

핵심 4개 하위 주장 모두 라인 단위로 실재 확인: (1) lock 경로 리터럴 중복 cli.py:81 ≡ c6c_deployment.py:111-113, FD env 중복 cli.py:82 ≡ c6c_deployment.py:1837-1839, 그리고 cli.py:944-949 `except FileNotFoundError: ... yield`가 락 부재를 통과 경로로 만든다(테스트 test_runtime_execution_registry.py:399가 이 통과를 고정). 두 상수 동일성 테스트는 없음. (2) `_running_from_trusted_install_root` 3벌 확인 — runtime_pin_registry.py:562(sys.prefix 특례), runtime_pin_request.py:189(get_project_root만), pinned_runtime_generation.py:3937(`__file__` prefix). registry.py:556-561 주석이 wheel 실행에서 get_project_root가 `.venv/lib`을 내는 버그를 명시하며 registry에만 고쳐져 있어, request 쪽 latent 불일치 주장을 코드가 뒷받침. `_TRUSTED_*_ROOT` 리터럴도 4개 모듈(runtime_execution_registry.py:40-42, runtime_pin_registry.py:50-56, runtime_pair_rotation.py:41, runtime_pin_request.py:61-62)에 중복. (3) `_require_pinned_runtime_rebuild_root`(compose_service.py:653-657 ≡ c6c_deployment.py:2210-2214)와 `get_project_root`(compose_service.py:711-716 ≡ registry.py:8-13) 문자 그대로 2벌, 둘 다 사용 중. 다만 수정 필요 3건: (a) "7개 파일 14곳"은 과소 — 실제 22곳(backend 13 + frontend RuntimePinPanel.tsx 8, BackupHistoryPanel.tsx 1). frontend TSX는 Python 모듈을 import할 수 없으므로 "전 소비처가 import" 및 "UI 명령 부정확성 한 번에 제거"는 그대로는 불성립 — API가 명령 문자열을 내려주게 하든가 frontend를 명시적으로 scope 밖으로 빼야 함(전자면 effort는 S가 아니라 M). (b) lock 경로·FD env 리터럴은 scripts/run-pinned-rebuild-once:32-33,84·run-m05-isolated-e2e-once·install-ktdm-trusted-release에도 존재하는데 이 launcher들은 검증 전 프로젝트 코드를 import하지 않으려 의도적으로 `python3 -I -S`로 돌므로 import 통일 불가 — 스크립트 텍스트 대 상수 동일성 회귀 테스트로 닫아야 하며 태스크가 이를 누락. (c) FD env 이름 drift는 조용하지 않다 — cli.py:910에서 상속 텍스트가 비면 직접 open으로 떨어지고 launcher가 flock을 쥐고 있어 BlockingIOError→fail-closed(cli.py:964-968); 조용한 무력화는 lock *경로* drift에만 성립하므로 문제 기술을 그 범위로 좁혀야 함. 계약 충돌 없음: ADR-40(docs/decisions.md:2479)은 pinned revision 소유권에 관한 것이지 경로 상수 중복을 요구하지 않으며, 판정 통일은 registry.py:562에 이미 있는 수정을 request로 확산하는 방향이라 보안 게이트를 약화하지 않는다(pinned_runtime_generation.py:3049 사용처도 True 확대가 의도와 일치).


## GM-10: root-safe atomic write/fsync 프리미티브 12벌 복제 — execution registry는 디렉터리 fsync 누락으로 crash 시 v6 rename 유실 가능

- **심각도/규모**: P2 / M · **분류**: correctness · **검증**: CONFIRMED · **E2E**: mock
- **관련 파일**: backend/src/kor_travel_docker_manager/services/runtime_execution_registry.py; backend/src/kor_travel_docker_manager/services/runtime_pin_registry.py; backend/src/kor_travel_docker_manager/services/pinned_runtime_generation.py; backend/src/kor_travel_docker_manager/services/compose_service.py; backend/src/kor_travel_docker_manager/services/docker_service.py; backend/src/kor_travel_docker_manager/services/standalone_backup.py; backend/src/kor_travel_docker_manager/services/pinvi_database_role_credentials.py; backend/src/kor_travel_docker_manager/services/legacy_override_retirement.py

**문제**: mkstemp 기반 atomic write 12곳, _fsync_directory 5곳 이상, _insecure_mode_allowed 3곳, TOCTOU-safe fd 검증 읽기 3-4곳이 각자 구현돼 이미 동작이 어긋났다: pin registry의 _atomic_write_json(runtime_pin_registry.py:745-786)은 교체 후 디렉터리 fsync를 하지만 execution registry의 _write(runtime_execution_registry.py:453-468)는 os.replace 후 디렉터리 fsync가 없어(코드로 확인) crash 시 v6 registry rename이 유실될 수 있다. O_NOFOLLOW/O_CLOEXEC 적용 여부도 구현마다 달라(docker_service.py:84, compose_service.py:2551/3242, pinned_runtime_generation.py:3682/3871/3991, legacy_override_retirement.py:1278, pinvi_database_role_credentials.py:416, standalone_backup.py:893) 같은 '안전 쓰기'가 파일마다 다른 보안 속성을 가진다. insecure-mode env 파싱도 `.strip()=="1"` vs `=="1"`로 미묘하게 다르다.

**개선안**: services/secure_state_file.py에 atomic_write_json/atomic_write_bytes(path, payload, mode, *, no_follow, dir_fsync=True), fsync_directory(path|fd), insecure_mode_allowed(env_name), open_verified_readonly(path, owner_policy)를 모으고 pin/execution/request/pair-rotation/generation/backup 모듈을 치환한다. execution registry의 fsync 누락은 이 작업의 첫 커밋으로 즉시 수정한다. O_NOFOLLOW가 필요한 root 상태 디렉터리는 명시 플래그로 요구해 정책을 코드에 드러낸다.

**실익**: crash-durability와 symlink 방어가 최상 구현으로 상향 평준화되고(fsync 결함 즉시 해소), 새 상태 파일 추가 시 보일러플레이트 재작성이 사라지며 감사 지점이 한 곳이 된다.

**검증 노트** (구현 시 본문보다 우선):

핵심 결함은 라인 단위로 사실이다. runtime_execution_registry.py:453-468 `_write`는 mkstemp→os.fsync(handle.fileno())→chmod→os.replace 후 디렉터리 fsync가 없다. 대조군인 runtime_pin_registry.py:777-786(`directory_fd = os.open(str(parent), os.O_RDONLY)` 후 `os.fsync(directory_fd)`)과 runtime_pair_rotation.py:114-118은 디렉터리 fsync를 수행하므로 execution registry만 빠진 것이 맞다. 호출부도 보상하지 않는다: cli.py:634(migrate-execution-v6), 665(rebind-execution), 705(block-execution)는 journal 없이 `write_runtime_execution_registry`만 호출한다 — 특히 705는 terminal block 기록이라 crash 시 rename 유실이 '차단 해제'라는 fail-open으로 나타나 태스크 서술보다 오히려 심각하다. 단 rotate-pair 경로(runtime_pair_rotation.py:277-283)는 durable intent+재시도("intent는 남고 retry가 target 전체를 다시 publish한다")로 부분 완화되어 있으므로 무완화 노출은 CLI 3개 경로다. 복제 수치도 정확하다: mkstemp 정확히 12곳(admin_password_service:330, map_application_300:1613/1728/1779, compose_service:754, pinvi_database_role_credentials:383, legacy_override_retirement:1254, runtime_execution_registry:456, runtime_pair_rotation:104, runtime_pin_registry:763, runtime_pin_request:307, standalone_backup:877), `_fsync_directory` 사설 구현 5곳+`_fsync_directory_descriptor`(pinvi_bootstrap_credential:859). insecure-mode 파싱 차이도 확인: runtime_pin_registry.py:637 `os.environ.get(ENV, "").strip() == "1"` vs runtime_execution_registry.py:371·runtime_pair_rotation.py:60 `os.environ.get(ENV) == "1"`. O_NOFOLLOW 정책 4종 혼재 확인(bare: runtime_execution_registry:304; getattr(...,0) 무음 폴백: legacy_override_retirement:449; getattr(...,None)+하드 에러: pinvi_database_role_credentials:254-256; hasattr 게이트: c6c_deployment:2143). standalone_backup.py:876-889는 dir fsync와 mode 설정이 둘 다 없다. 계약 충돌 없음: decisions.md에 파일 I/O 사설 구현을 요구하는 ADR이 없고 durable receipt/journal ADR들은 오히려 fsync 의존적이라 정합하며, M05 동결은 frozen-input 규율이지 모듈 동결이 아니고 m05_isolated_harness.py는 파일 목록에서 제외돼 있어 적절하다. 실행 시 주의 2건(태스크 취지 '최상 구현으로 상향 평준화'와 일치하므로 REVISED까지는 아님): (1) pinned_runtime_generation.py:3756-3780 `_write_public_json`은 dir_fd 상대 O_EXCL|O_NOFOLLOW+fsync(directory fd)로 제안된 path 기반 시그니처보다 강하다 — 프리미티브에 dir_fd 변형을 추가하거나 이 writer는 치환에서 제외해야 하강 평준화를 피한다. (2) runtime_pin_registry `_atomic_write_json`의 `directory_mode` 파라미터(745-751)를 공용 시그니처가 수용해야 한다. 첫 커밋의 즉시 fsync 수정은 cli.py 3개 경로(특히 block-execution) 기준으로 검증하는 것이 맞고, effort M은 12곳+기존 테스트 보정 기준으로 현실적이다.


## GM-11: docker-targets.yml 스키마 검증 부재 — 오타 하나로 CLI/API 전체가 raw KeyError로 죽고, containers 목록은 손 복사 전이 폐포

- **심각도/규모**: P2 / M · **분류**: correctness · **검증**: REVISED · **E2E**: mock
- **관련 파일**: backend/src/kor_travel_docker_manager/services/registry.py; config/docker-targets.yml; backend/src/kor_travel_docker_manager/services/docker_service.py; backend/src/kor_travel_docker_manager/cli.py

**문제**: load_targets_config(registry.py:29-34)는 최상위 3개 키의 타입만 확인한다. geo.depends_on을 'promx'로 한 글자만 바꾸면 list_targets()가 raw KeyError로 죽고, cli.py:77-79가 import 시점에 이를 호출하므로 ktdctl 전 명령과 backend 기동이 원인 불명 traceback으로 실패한다(실험으로 확인됨). 컨테이너 필수 필드 누락은 요청 시점 KeyError(docker_service.py:561), alias 충돌·YAML 중복 키·targets.<id>.containers의 미등록 id는 경고 없이 조용히 무시된다. 추가로 targets.<id>.containers는 depends_on 폐포에서 기계적으로 유도 가능한 목록인데 손 복사돼(map 14개:406-420, pinvi 18개:443-461, all 21개:481-502) 새 컨테이너 하나에 최대 4곳 갱신이 필요하고, 누락은 대시보드 '기타' 버킷으로만 조용히 드러난다.

**개선안**: load_targets_config에 참조 무결성(depends_on/include→targets 존재, targets.containers→containers 존재, alias 유일성)과 컨테이너 필수 필드(display_name/role/connection/expected_ports) 검증을 추가하고, _UniqueKeySafeLoader(compose_service.py:1067)를 재사용해 YAML 중복 키를 거부한다. 위반 시 "docker-targets.yml targets.geo.depends_on: unknown target 'promx'"처럼 파일·경로·값을 짚는 메시지로 기동 시 fail-close한다. containers 목록은 registry.get_target이 depends_on 폐포 + target 고유분으로 계산(resolved_containers)해 API에 포함하고, 이행기에는 손 목록과 계산 결과의 불일치를 기동 오류로 만들어 기존 파일을 검증한다.

**실익**: 새 타깃 추가라는 이 도구의 핵심 확장 행위에서 편집 실수가 즉시, 지목된 메시지로 드러나고 편집 지점이 1곳으로 준다. 범용 관리툴의 config-as-source-of-truth 원칙이 실제로 강제된다.

**검증 노트** (구현 시 본문보다 우선):

검증 절반은 사실로 확인, 유도(resolved_containers) 절반은 전제가 틀려 수정 필요.

[확인된 사실] (1) registry.py:29-34는 최상위 3키의 타입만 검사한다. (2) geo.depends_on을 'promx'로 바꾼 실험 재현: services_for_target의 `services.extend(_targets()[target_name].get("services", []))`(registry.py:130)에서 raw `KeyError: 'promx'`, cli.py:77-79의 모듈 레벨 `DIRECT_ENSURE_ALIASES = {... for target in list_targets() ...}` 때문에 ktdctl 전 명령이 import 시점에 죽는다. (3) docker_service.py:556-561은 `spec["display_name"]`, `spec["role"]`, `spec["connection"]`, `spec["expected_ports"]`를 raw 접근해 필드 누락 시 요청 시점 KeyError. (4) alias 충돌은 registry.py:60 `aliases[...] = target`로 조용히 last-wins, PyYAML safe_load는 중복 키 허용, targets.<id>.containers의 미등록 id는 유일한 소비자인 frontend DashboardClient.tsx:295 `.includes()`에서 조용히 미매치('기타' 버킷 :303-304). (5) 손 목록 map 14개(yml:406-420)/pinvi 18개(443-461)/all 21개(481-502), 새 컨테이너 최대 4곳 갱신 — 모두 사실.

[수정 필요 1 — backend 기동 실패 주장은 거짓] 같은 typo 설정으로 `import kor_travel_docker_manager.main`은 IMPORT_OK, exit 0으로 성공했다(실험). main.py는 registry를 직접 참조하지 않고(grep 무매치), registry.py:64 `MANAGED_CONTAINERS = load_targets_config()["containers"]`는 typo에 걸리지 않는다. backend는 정상 기동하고 GET /api/v1/targets(routes.py:155) 등 요청 시점에만 500이 난다. "ktdctl 전 명령 + backend 기동 실패"는 "ktdctl 전 명령 즉사 + API는 요청별 500"으로 정정해야 한다.

[수정 필요 2 — 핵심: containers는 depends_on 폐포에서 기계적으로 유도 불가] 실제 계산 결과, 손 목록은 폐포 합집합과 일치하지 않는다: infra target(storage/gra/cadv/prom)은 자기 컨테이너 1개만 적고(폐포면 storage에 geo-postgresql이 포함돼야 함), 앱 target(geo/conc/map/pinvi)은 정확히 "폐포 − {grafana, cadvisor, prometheus}"다. 원인은 depends_on이 논리 의존이 아니라 기동 순서 선형화라서(geo `depends_on: [prom]` yml:326, conc :357) 모니터링 스택이 모든 앱 폐포에 끼기 때문. 따라서 개선안의 이행기 게이트("손 목록과 계산 결과 불일치를 기동 오류로")는 현재의 올바른 파일에서 즉시 fail-close 오탐으로 관리자를 기동 불능으로 만든다. 참고로 순수 폐포 유도로 목록을 교체해도 대시보드의 narrowest-target 배정(DashboardClient.tsx:296-302)은 결과가 동일함을 확인했으므로, 올바른 이행은 "동등성 검증"이 아니라 "손 목록을 계산값으로 교체"이거나 유도 규칙에 모니터링 제외를 명시하는 것이다. (frontend 주석 :289-291도 이 목록을 전이 폐포로 오인하고 있다.)

[수정 필요 3 — _UniqueKeySafeLoader 재사용은 순환 import] compose_service.py:188이 registry를 import하므로 registry가 compose_service.py:1067의 로더를 역으로 import할 수 없다. 공유 모듈로 이동하거나 복제해야 한다.

[계약/대안/effort] fail-close 스키마 검증 자체는 C6c 락 규율·ADR과 충돌 없고 T-047의 fail-close 철학과 정합한다. 더 값싼 대안(검증만 하고 유도는 포기)도 성립하나, 유도부의 전제 수정을 포함하면 effort M은 타당하다. 관련 테스트는 backend/tests에 전무함을 확인.


## GM-12: API 오류 표면 4종 분열 — app 예외 핸들러로 단일 envelope을 강제하고 프론트 조회 오류 표시를 통일

- **심각도/규모**: P2 / M · **분류**: ux · **검증**: REVISED · **E2E**: mock
- **관련 파일**: backend/src/kor_travel_docker_manager/api/routes.py; backend/src/kor_travel_docker_manager/main.py; frontend/src/lib/api.ts; frontend/src/lib/errors.ts; frontend/src/components/SourceStatusPanel.tsx; frontend/src/components/AdminSettingsPanel.tsx; frontend/src/components/BackupHistoryPanel.tsx; frontend/src/components/RuntimePinPanel.tsx; frontend/src/components/ContainerDetailModal.tsx

**문제**: 같은 라우터 안에서 detail이 (a) 평문 str(exc)(routes.py:699/743/791/812), (b) 대문자 토큰('BACKUP_JOB_NOT_FOUND':241, 'AUTH_REQUIRED'), (c) code 없는 dict(:110-118), (d) code+한국어 dict(:454-457)로 갈리고, 동일한 3단 예외→상태코드 매핑이 4개 라우트에 복사돼 새 라우트에서 누락 시 500 스택트레이스가 나간다. 서비스 계층은 계약 위반을 예외로, 하위 프로세스 실패를 {"success": False} dict로 반환하는 이중 채널이라 모든 라우트가 try/except 뒤 분기를 한 번 더 쓴다. 프론트는 api.ts:32-49의 shape 스니핑으로 연명하고, 조회 실패는 humanizeError를 안 거쳐 raw JSON을 노출한다(SourceStatusPanel.tsx:215-219 등, AdminSettingsPanel은 성공/실패가 같은 회색 문단에 섞이고 ContainerDetailModal은 원인을 고정 문구로 뭉갠다).

**개선안**: GM-06의 code 필드를 전제로 @app.exception_handler 3종(DeploymentContractError/ComposeCandidateContractError/ComposePostMutationContractError)을 한 번만 등록해 {code, message, detail} envelope을 강제하고 라우트별 try/except 사다리를 제거한다. dict 채널은 공용 _raise_if_failed helper로 통일한다. 프론트는 조회 실패도 <InlineError error={humanizeError(error, '…')} />로 치환하고, AdminSettingsPanel의 message: string을 HumanError | null로 바꿔 성공 메시지와 분리하며, ContainerDetailModal은 humanizeError의 title+접힌 raw를 보여준다.

**실익**: 오류→HTTP 매핑 정책이 단일 지점이 되어 프론트가 code 기반으로 안정 분기·번역하고, 운영자가 raw JSON 해독이나 원인 지워진 고정 문구 앞에서 SSH로 도망가는 일이 줄어든다.

**검증 노트** (구현 시 본문보다 우선):

문제 기술은 라인 단위로 전부 사실이다. (a) routes.py:699/743/791/812 모두 `raise HTTPException(status_code=409, detail=str(exc))` — 평문 str(exc) 확인. (b) routes.py:241 `detail="BACKUP_JOB_NOT_FOUND"`, auth_service.py:160 `detail="AUTH_REQUIRED"` — 대문자 토큰 확인. (c) routes.py:110-118 `_config_failure_detail`은 code 없는 dict 확인. (d) routes.py:454 `detail: dict[str, Any] = {"code": code, "message": message}` — code+한국어 dict 확인. 3단 예외 매핑(post-mutation→500, candidate→409, base→409)은 ensure_target(690-699), control_container(734-743), update_container_config(782-791), reset_container_config(803-812) 4곳에 실제 복사돼 있다. dict 이중 채널도 확인: 같은 라우트들이 try/except 뒤 `if not result.get("success")` 분기를 반복하고 logs(757)/inspect(767)는 그것만 쓴다. 프론트도 확인: api.ts:32-49 parseErrorBody shape 스니핑, SourceStatusPanel.tsx:214-219·287-291·395-399가 error.message 원문 노출, AdminSettingsPanel.tsx:395가 keyState.message로 성공("공개 API 키를 생성했습니다")과 오류를 같은 text-secondary 회색 문단에 렌더, ContainerDetailModal.tsx:289-297이 원인을 "컨테이너가 실행 중이 아니거나 Docker 데몬에 연결할 수 없습니다" 고정 문구로 뭉갠다. ADR 충돌 없음(decisions.md에 오류 envelope을 고정한 ADR 없음; C6c exact-dict 계약은 manifest/journal 응답이지 오류 detail이 아니다). 상태코드가 보존되므로 보안 게이트·lock 규율도 무관하다.

수정 필요 3건: (1) envelope 형태를 명시해야 한다 — "{code, message, detail} envelope"을 최상위 새 형태로 읽으면 오히려 5번째 표면이 생긴다. api/auth.py·api/admin.py는 files 목록에 없어 AUTH_REQUIRED와 admin 라우트의 FastAPI 기본 `{"detail": ...}` 형태가 그대로 남고, test_api.py의 exact-dict 단언 ~20곳(462, 490, 534, 999, 1054 등 `response.json()["detail"] == {...}`)과 parseErrorBody(api.ts:40-47, `body.detail.code/message`를 읽음)가 전부 깨진다. 핸들러는 `{"detail": {code, message, ...}}` 형태를 유지하도록 못박아야 기존 테스트·프론트 파서와 수렴한다. (2) "라우트별 try/except 사다리를 제거한다"는 과장 — ensure_target의 `except ValueError → 404`(routes.py:700-701)는 남아야 한다(DeploymentContractError가 ValueError의 하위클래스라 순서 의존이지만, 미지 target의 bare ValueError는 base-class 핸들러 3종이 잡지 못하고, bare ValueError 전역 핸들러는 너무 광범위하다). update_container_config의 `ContainerConfigValidationError → 422`(routes.py:780-781)도 마찬가지 — 이 클래스는 docker_service.py:280에서 ValueError만 상속하므로 4번째 핸들러를 추가하거나 라우트 catch를 유지해야 한다. (3) 사소한 과장: 매핑 누락 시 "500 스택트레이스가 나간다"는 부정확 — main.py의 FastAPI는 debug 미설정이라 응답 본문은 "Internal Server Error"뿐이고 traceback은 로그로만 간다(불투명 500이라는 본질은 맞다). 부수: base DeploymentContractError(c6c_deployment.py:1854-1855)에는 code 속성이 없어 GM-06 선행이 실제로 필수이며, BackupHistoryPanel.tsx:342는 이미 조회 실패에 humanizeError를 쓰고 있어 "채택이 mutation에만 있다"는 일반화도 약간 과하다. 이 수정들을 반영하면 effort M은 현실적이다(테스트 형태 보존 시 백엔드 테스트 수정 최소).


## GM-13: 백업 API 견고화 — manifest 1개 손상이 목록 전체를 409로 지우고, 재기동 후 이중 pg_dump를 막는 가드가 없다

- **심각도/규모**: P2 / M · **분류**: operability · **검증**: REVISED · **E2E**: mock
- **관련 파일**: backend/src/kor_travel_docker_manager/api/routes.py; backend/src/kor_travel_docker_manager/services/standalone_backup.py; backend/src/kor_travel_docker_manager/services/job_runner.py

**문제**: (1) GET /api/v1/backups는 '무엇이 남았는지의 권위'를 자처하지만(routes.py:160-165) list_standalone_backups(standalone_backup.py:404-416)는 manifest 하나라도 읽기 실패·형식 위반·role 불일치(896-933)면 예외를 던지고 라우트가 통째로 409로 바꾼다(:176). geo 백업 세트를 map 디렉터리에 복사하는 흔한 실수 하나로, 장애 중 가장 필요한 순간에 멀쩡한 백업 전체 목록이 사라진다. (2) job_runner 자신의 docstring(:15-18)이 경고하듯 프로세스 종료로 flock이 풀려도 컨테이너 안 pg_dump는 계속 도는데 API에 가드가 없어, 재기동 직후 같은 role 백업을 다시 시작하면 같은 DB에 pg_dump 2개가 붙는다(geo 실측 20분+). 재기동으로 job 기록이 사라지면 폴링 UI는 404 BACKUP_JOB_NOT_FOUND(routes.py:241)만 받아 '잘못된 id'와 '기록 소실(작업은 계속 중)'을 구분할 수 없다.

**개선안**: (1) 목록 경로에서 manifest 단위로 예외를 잡아 {state:'unreadable', filename, reason} 행으로 degrade하고 나머지 정상 manifest는 반환한다. 비-200은 디렉터리 자체를 못 읽을 때(503)로 한정하고, gc/restore-plan 등 mutation 경로는 fail-close 유지. (2) submit 전에 role lock 아래에서 'manifest 없는 최근 dump 파일'(진행 중 create의 시그니처)을 감지해 409 + 전용 code로 거부하고, job payload와 조회 응답에 runner 프로세스 epoch(기동 시각)을 실어 프론트가 404를 '재기동으로 기록 소실'로 번역하게 한다. 장기: SQLite에 job 기록 영속화.

**실익**: 장애 중 백업 존재 여부를 못 보는 상황과, 배포 직후 프로덕션 DB에 이중 dump 부하를 거는 사고가 함께 사라진다.

**검증 노트** (구현 시 본문보다 우선):

문제 기술은 대부분 정확하나 개선안 (2)의 탐지 메커니즘이 틀렸고, 404 번역은 이미 구현돼 있다.

[확인된 부분] (1) routes.py:170-176 `except StandaloneBackupError as exc: raise HTTPException(status_code=409...)`가 role 루프 안에서 즉시 raise하므로 manifest 하나가 목록 전체를 지운다. standalone_backup.py:413-415는 list comprehension이라 `_read_manifest`(896-934)의 읽기 실패(:904)·형식 위반(:919)·role 불일치(:931-933) 하나가 전체를 전파한다. "geo 세트를 map 디렉터리에 복사" 시나리오는 :931-933에서 정확히 재현됨. 프론트 영향도 실재: BackupHistoryPanel.tsx:354-357에서 표 전체 소실, :98-102의 freshness 배지도 같은 엔드포인트라 함께 죽는다. test_api.py:1253-1263이 현행 409를 고정하지만 문서화된 fail-close 계약(docker-management.md:1127-1129)은 gc에 대한 것이고 개선안이 gc/restore-plan fail-close를 유지하므로 계약 충돌 없음. (2) 이중 pg_dump 위험도 실재하며 코드가 자인한다(job_runner.py:15-18, docker-management.md:1119-1121, standalone_backup.py:270-274 docstring) — flock(:285, :670-709)은 프로세스 종료로 풀리고 API submit 경로(routes.py:202-206)에 가드 없음.

[수정 필요 1 — 핵심 결함] "manifest 없는 최근 dump 파일 = 진행 중 create의 시그니처"는 성립하지 않는다. pg_dump는 컨테이너 안 /tmp/{filename}에 쓰고(standalone_backup.py:294-315 `--file container_tmp`), 호스트에는 docker cp(:319-323)→os.replace(:327) 이후에야 dump가 나타난다. 따라서 가장 긴 위험 구간(geo 실측 879초~22분의 pg_dump 단계)에 호스트는 완전히 깨끗해서 제안된 가드가 정확히 그 구간에서 무력하다. 호스트의 "manifest 없는 dump" 창은 :327~:399(manifest 쓰기) 사이 몇 분뿐이고, 그 파일은 gc가 orphan으로 수거하는 대상(:443-451)이기도 하다. 올바른 시그니처는 컨테이너/DB 쪽이다: role lock 아래에서 pg_stat_activity의 application_name='pg_dump' AND datname=<db> 조회(또는 컨테이너 안 pg_dump 프로세스+잔존 /tmp dump 확인) 후 전용 code로 409. psql 1회라 더 싸고, geo/geo_dagster처럼 컨테이너를 공유하는 role(:75-76)을 datname 필터로 오차단하지 않으며, cron발 dump도 잡는다.

[수정 필요 2 — 이미 처리된 부분] "프론트가 404를 재기동 소실로 번역하게 한다"는 이미 사실상 구현돼 있다: frontend/src/lib/errors.ts:110-113이 BACKUP_JOB_NOT_FOUND를 "관리도구가 재기동되면 진행 기록은 사라집니다. 실제로 남은 백업은 아래 목록이 정본입니다"로 번역한다. job id는 POST 202 응답과 GET .../jobs(latest, BackupHistoryPanel.tsx:111-123)에서만 오므로 UI가 '잘못된 id'를 만들 경로가 없어 404 ≈ 재기동 소실이며, epoch 필드는 한계효용이 낮다. epoch보다는 [수정 1]의 submit 가드가 실제 사고(404 후 버튼 재활성화 → 재클릭 → 이중 dump)를 막는 본체다. 남는 실질 gap은 힌트 문구가 "작업이 컨테이너에서 계속 돌고 있을 수 있다"를 말하지 않는 것 정도로, 문자열 수정이면 된다.

[effort] 목록 degrade(라우트 한정, list_standalone_backups는 gc/restore-plan 공용이므로 서비스 함수 분리 또는 플래그 필요) + 프론트 타입/필터 + submit 가드(psql 조회) + 테스트로 M 추정은 현실적. epoch/SQLite 영속화를 빼면 S+~M.


## GM-14: async 핸들러 안의 동기 SQLite 감사 기록이 event loop 전체를 정지시킬 수 있음

- **심각도/규모**: P2 / S · **분류**: operability · **검증**: REVISED · **E2E**: mock
- **관련 파일**: backend/src/kor_travel_docker_manager/api/routes.py; backend/src/kor_travel_docker_manager/services/auth_service.py; backend/src/kor_travel_docker_manager/database.py

**문제**: post_backup은 asyncio.create_task 때문에 async def인데(routes.py:188-196) 그 안에서 record_login_audit_event를 동기 호출한다(:209). 이 함수는 INSERT commit + prune SELECT/DELETE/commit(auth_service.py:287-334)을 수행하고, DB는 metrics collector가 10초마다 쓰는 동일한 단일 SQLite 파일(database.py:9-15, WAL 미설정)이다. 잠금 경합 시 sqlite3 기본 busy timeout(5초)만큼 event loop 전체 — /health, 모든 WebSocket, broadcast — 가 얼어붙는다. 또 job이 이미 시작된 뒤 감사 쓰기가 실패하면 클라이언트는 500을 받아 '백업이 안 시작됐다'고 오판한다.

**개선안**: async 핸들러에서는 await asyncio.to_thread(record_login_audit_event, ...)로 감사 기록을 스레드풀로 내리고, 성공 응답 뒤의 감사 실패는 500 승격 대신 logger.critical + 응답 경고 필드로 처리한다. 엔진에 WAL + busy_timeout PRAGMA를 걸어 근본 경합도 줄인다.

**실익**: 디스크/DB가 잠깐 느려져도 API·WebSocket 전체가 함께 멈추지 않고, 이미 시작된 백업이 실패로 보고되는 혼선이 사라진다.

**검증 노트** (구현 시 본문보다 우선):

사실로 확인된 것: (1) post_backup은 routes.py의 유일한 async 핸들러 감사 호출자다 — routes.py:182 `async def post_backup`, :209 `record_login_audit_event(...)` 동기 호출. 다른 감사 호출부(auth.py:31/92, admin.py 전부, routes.py:461/641 runtime-pin)는 전부 sync `def`라 threadpool에서 돈다. (2) record_login_audit_event는 INSERT+commit 후 _prune_login_audit_events의 SELECT/DELETE/commit까지 수행한다(auth_service.py:287-334). (3) database.py:12-15 엔진에 WAL/busy_timeout PRAGMA가 전혀 없다(backend/src 전체 grep 0건). pysqlite 기본 busy timeout 5초도 맞다. (4) submit 성공(:206) 뒤 감사 예외를 잡는 try/except가 없어 job이 이미 도는 채로 500이 나가는 것도 맞다 — 이를 고정하는 테스트·ADR·문서 계약은 없고(backend/tests grep, docs/decisions.md·docker-management.md 확인), 95200f0의 '감사 fail-open'은 셸 스크립트(scripts/install-ktdm-trusted-release) 건이라 무관하다. M05 동결은 pin/execution identity 영역이라 이 수정과 충돌하지 않는다.

수정해야 할 것 두 가지. 첫째, **경합 상대 지목이 틀렸다**: metrics collector는 asyncio task로 event loop 위에서 돌고(metrics_collector.py:330 `asyncio.create_task(self._collect_loop())`), save_metric을 async collect_metrics 안에서 **동기 호출**한다(:521). 같은 스레드의 두 동기 호출은 시간상 겹칠 수 없으므로 post_backup 감사 쓰기와 collector 쓰기는 서로 잠금 경합이 불가능하다. 실제 경합 상대는 threadpool 쪽이다: sync def 핸들러의 감사/세션 쓰기와 websocket 인가의 `asyncio.to_thread(_websocket_authorize, ...)` SELECT(websocket.py:269,393,495). 둘째, **개선안의 커버리지가 부족하다**: loop를 실제로 가장 자주 멈추는 것은 post_backup(드문 호출)이 아니라 collector 자신이다 — save_metric(:521)은 10초마다 컨테이너당 1회, cleanup_old_metrics(:357, :365)는 시작 시+약 1시간마다 30일치 대량 DELETE(+commit)를 **loop 위에서 동기로** 실행한다. 이쪽은 경합 없이도 fsync 시간만큼 loop를 세운다. 'event loop 정지 방지'가 목표라면 이 두 호출의 to_thread 이관이 개선안에 포함되어야 한다. 셋째(경미), WAL 제안 주의: DB 파일이 repo 루트(database.py:9, 개발 시 /mnt/f drvfs/9p)에 있어 WAL의 shm/mmap이 drvfs에서 실패할 수 있다 — busy_timeout PRAGMA는 무조건 안전하지만 WAL은 fallback 또는 개발 환경 실측이 필요하다(운영 n150 native fs는 문제없음). 넷째(대안 제시), 500 완화는 계약 위반이 아니지만, 더 엄격한 감사를 원하면 submit **이전에** 'requested' 감사 행을 쓰고 감사 실패 시 mutation 자체를 거부하는 fail-close 설계도 가능하다 — 어느 쪽이든 문서화하면 된다. effort S는 좁은 수정 기준으로 현실적이고, collector 커버리지와 WAL fallback까지 넣으면 S~M.


## GM-15: 상태 broadcast가 클라이언트 직렬 전송 — 느린 소켓 하나가 모든 탭의 상태 갱신을 무기한 정지

- **심각도/규모**: P2 / M · **분류**: operability · **검증**: CONFIRMED · **E2E**: mock
- **관련 파일**: backend/src/kor_travel_docker_manager/api/websocket.py

**문제**: ConnectionManager.broadcast(websocket.py:305-318)는 active_connections를 순회하며 await connection.send_text를 타임아웃 없이 직렬 실행한다. TCP 버퍼가 가득 찬 죽은-그러나-닫히지-않은 피어(노트북 절전, 네트워크 단절)가 하나 있으면 uvicorn write flow control 때문에 그 send가 커널 keepalive 타임아웃까지 매달리고, 그동안 status_broadcast_loop(:324-337) 전체가 정지해 다른 모든 탭의 상태 갱신이 함께 멈춘다. 모니터링 도구에서 가장 치명적인 실패 모드인데, 재연결/백프레셔 계약이 accept/close 경로에는 정교하게 있으면서 fan-out 경로에만 없다.

**개선안**: 클라이언트별 send를 asyncio.wait_for(send, 2~5초)로 감싸 TimeoutError 시 해당 연결을 drop하거나, 연결마다 bounded queue + 전용 sender task(가득 차면 oldest drop)를 두어 느린 소비자를 격리한다. 매달린 send를 흉내내는 단위 테스트로 회귀를 막는다.

**실익**: 네트워크가 나쁜 클라이언트 하나가 관제 화면 전체를 조용히 얼리는 실패 모드가 제거된다.

**검증 노트** (구현 시 본문보다 우선):

문제 사실 확인: broadcast(websocket.py:313-315)는 `for connection in list(self.active_connections): await connection.send_text(payload)`로 타임아웃 없는 직렬 전송이고, 유일한 격리 장치인 `except Exception`(:316)은 예외를 던지는 소켓만 잡는다 — wedged transport는 예외 없이 websockets legacy의 `drain()`에 park되므로 이 guard가 절대 발동하지 않는다. status_broadcast_loop(:334)가 이를 직접 await하므로 공유 루프 전체가 정지한다는 주장도 정확하다. 스택 확인: pyproject.toml `uvicorn ^0.28.0`/`websockets ^12.0`, 파일 자체 주석(:83)이 legacy websockets_impl을 명시. 반박 각도 검증: (a) uvicorn 기본 ws ping(20s/20s)이 hang을 bound한다는 반론은 성립하지 않는다 — keepalive_ping의 `await self.ping()`이 pong 타임아웃을 arm하기 전에 같은 `_drain_lock`/`drain()`에 park된다. (b) accept/close 경로엔 `_WS_HANDSHAKE_TIMEOUT_SECONDS`(:71,:138,:155)와 `_REJECT_CLOSE_TIMEOUT_SECONDS`(:76)가 있어 "fan-out에만 없다"는 비대칭 서술도 정확. (c) 기존 테스트 test_broadcast_isolates_failing_connection(test_ws_contract.py:311)은 raise하는 send만 커버하고 hang하는 send는 미커버. (d) 계약 충돌 없음 — C7 close 코드 계약(:24-29)은 거절/인증 코드 규정이고, M05는 PinVi/Map pin/rebuild 규율로 무관하며, decisions.md에 ws 백프레셔 ADR 없음. (e) effort M 타당: wrapper는 작지만 hanging-send 회귀 테스트 + docs 동기화 포함하면 M. 경미한 정정 2건(verdict 불변): ① "커널 keepalive 타임아웃"이 아니라 죽은 peer는 TCP 재전송 타임아웃(tcp_retries2, ~13-30분), zero-window인 살아있는 peer는 문자 그대로 무기한 — 오히려 주장을 강화. ② 구현 시 active_connections에서 제거만 하면 ws_status handler가 zombie가 된다(receive() park + 60초 재인가 계속 성공) — drop 시 소켓 abort/close까지 해야 한다.


## GM-16: 모든 백엔드 로그가 두 번씩 기록되고, 요청 상관관계 ID가 없어 UI 오류와 로그·감사를 이을 수 없다

- **심각도/규모**: P2 / M · **분류**: observability · **검증**: CONFIRMED · **E2E**: mock
- **관련 파일**: backend/src/kor_travel_docker_manager/main.py; backend/src/kor_travel_docker_manager/services/auth_service.py

**문제**: main.py:76-89에서 'kor_travel_docker_manager' 로거에 콘솔·월간파일 핸들러를 붙인 뒤 92행에서 root logger에 같은 핸들러 객체를 대입한다(코드로 확인). 패키지 로거의 propagate가 True라 하위 모든 로그 레코드가 패키지 레벨과 root 레벨에서 각각 핸들링되어 콘솔·파일에 정확히 2회씩 찍힌다 — 볼륨 2배, 발생 빈도·계수 판단 전부 왜곡. 또 미들웨어는 CORS 하나뿐(:197-203)이고 로그 포맷에 요청 식별자가 없어, ensure_target이 500을 내면 UI가 받은 오류와 로그의 예외 스택을 시각 근사로만 연결해야 한다. LoginAuditEvent의 audit_event_id(auth_service.py:288-304)도 로그 라인과 연결되지 않는다.

**개선안**: 핸들러를 root logger에만 붙이고 패키지 로거는 레벨만 설정해 propagate에 맡긴다(caplog 기반 '레코드당 emit 1회' 회귀 테스트 추가). uuid4 기반 X-Request-ID 발급/전파 ASGI 미들웨어를 추가하고 contextvar + logging.Filter로 모든 레코드에 request_id를 주입하며, 오류 응답 detail과 record_login_audit_event의 detail_json에 같은 id를 실어 UI 오류 → 감사 행 → 로그 라인이 한 키로 조인되게 한다. 겸사겸사 MonthlyRotatingFileHandler.doRollover의 무조건 os.remove(main.py:56-58)를 append-rename으로 바꿔 --reload 다중 프로세스에서의 아카이브 유실도 막는다.

**실익**: 로그 디스크 사용량이 절반이 되고 발생 횟수·순서를 신뢰할 수 있으며, mutation 실패 시 스크린샷의 id 하나로 로그·감사를 즉시 추적할 수 있다 — 범용 관리툴의 기본 소양.

**검증 노트** (구현 시 본문보다 우선):

전부 라인 단위로 검증됨. (1) 이중 로깅: main.py:76-89에서 패키지 로거에 핸들러 부착 후 92행 `logging.getLogger().handlers = logger.handlers`로 root가 동일 리스트 공유, backend 전체에 propagate=False 없음(grep 0건). 프로젝트 venv에서 동일 설정 재현 실행 결과 패키지 하위 로거 레코드가 정확히 2회 emit됨을 실측 확인. 모든 서비스 모듈이 getLogger(__name__)이라 전 레코드가 대상. (2) 상관관계 ID 부재: 미들웨어는 CORS 하나뿐(main.py:197-203), backend/src의 request_id grep 히트는 전부 runtime-pin 회전 요청 도메인 객체(routes.py:589 등)로 HTTP 상관관계와 무관. ensure_target 500 detail(routes.py:690-711)에 조인 키 없음. (3) audit_event_id는 auth_service.py:290에서 생성, 350행 목록 API에서만 노출되고 로그·응답 어디에도 연결 안 됨. (4) doRollover(main.py:56-58)의 기존 아카이브 os.remove는 다중 프로세스 중첩 시 실제로 아카이브를 파괴하는 경쟁 맞음(두 번째 프로세스의 rename은 FileNotFoundError로 handleError행). 계약 충돌 없음: M05는 Map·PinVi pin/rebuild 규율(docs/docker-management.md:269)로 로깅과 무관하고, root는 이미 현재도 핸들러를 갖고 있어 root 단독 부착은 서드파티 캡처 동작을 바꾸지 않음. 더 싼 대안(logger.propagate=False 한 줄)은 중복만 고치고 상관관계 ID는 못 하므로 반박 사유 아님. effort M 현실적. 구현 시 참고(태스크 무효화 아님): 프론트 JS가 헤더를 읽으려면 CORSMiddleware에 expose_headers=["X-Request-ID"] 추가 필요, HTTPException detail 일괄 주입은 라우트별 수정 대신 전역 exception handler가 필요.


## GM-17: compose candidate 검증의 Map/PinVi 하드코딩 완화 — 14개 서비스 존재 강제와 bind allowlist를 설정으로 외부화

- **심각도/규모**: P2 / L · **분류**: generality · **검증**: REVISED · **E2E**: mock
- **관련 파일**: backend/src/kor_travel_docker_manager/services/c6c_deployment.py; backend/src/kor_travel_docker_manager/services/compose_service.py; backend/src/kor_travel_docker_manager/services/database_runtime.py; config/docker-targets.yml; docker-compose.yml

**문제**: 모든 mutation 경로(개발 모드의 평범한 ktdctl ensure 포함)의 transaction 캡처가 (1) _CANDIDATE_REQUIRED_PROTECTED_SERVICES(c6c_deployment.py:392-409)의 Map/PinVi 14개 서비스가 compose 문서에 하나라도 없으면 무조건 거부하고(3709-3714, 4143), (2) bind는 _CANDIDATE_ALLOWED_OPERATOR_BINDS(1708-1832)에 (service, container_path, ro)→exact host source로 등재된 것만 허용한다(7255-7261). 그래서 Map/PinVi를 뺀 구성이나 여섯 번째 프로젝트의 pgdata bind 하나에도 backend 코드 수정 + trusted release 재설치가 필요하다. /home/digitie/..., /mnt/f/dev/kor-travel-geo/data/juso 같은 개인 경로가 docker-compose.yml(44-45, 145, 212)과 코드에 이중 하드코딩돼 다른 호스트에 이식되지 않고, 한쪽만 고치면 fail-close한다. '범용 Docker target 관리 도구'의 공통 경로가 특정 두 프로젝트의 존재를 전제하는 핵심 저해 요인이다.

**개선안**: (1) required-set 강제를 pinned-rebuild 경로와 production 모드로 한정하고, 일반 ensure/save는 '해당 서비스가 존재하면 계약 검증, 없으면 통과'로 바꾼다. (2) bind allowlist를 trusted 설치본에 포함되는 root-owned 선언 파일(config/compose-binds.yml 또는 docker-targets.yml 컨테이너 정의의 binds: 섹션)로 옮기고 코드에는 검증 규칙(manager 파일 노출 금지, protected value leak, 존재/유형 검사)만 남긴다 — 신뢰 모델은 '설치본의 데이터' 그대로 유지된다. /home/digitie류 기본값은 ${VAR:?} 필수화로 제거한다. 후속(별도 L 태스크): ADR-40 확장으로 기대 롤 그래프·membership baseline(database_runtime.py:64-176)·응답 스키마까지 sealed paired candidate의 계약 데이터로 옮겨 앱 스키마 변경과 Manager 릴리스를 분리한다.

**실익**: 새 타깃·새 호스트 추가가 config 작업이 되고, 부분 스택 운용과 타 프로젝트 재사용 시 백엔드 포크가 필요 없어진다 — 이 분석의 핵심 렌즈인 범용성의 최대 병목 해소.

**검증 노트** (구현 시 본문보다 우선):

핵심 사실은 라인 단위로 확인됨: (1) 14개 required-set(c6c_deployment.py:392-409)은 raw 4143-4148·resolved 3709-3714에서 무조건 강제되고, dev `ensure`도 ensure_target(compose_service.py:4637-4703)→_capture_transaction_unlocked(3519)→validate_compose_candidate_protected_values(4057)로 도달한다(require_api_wiring:4124는 required-set을 우회 못 함). (2) bind는 _CANDIDATE_ALLOWED_OPERATOR_BINDS(1708-1832) 밖이면 7255-7261 "bind is not in the canonical baseline"으로 거부 — 새 bind 키 하나에 backend 수정이 필요하다는 주장 사실. (3) 개인 경로 이중 하드코딩(docker-compose.yml:44-45,145,212 vs c6c_deployment.py:1713/1718/1723/1783/1788)과 database_runtime.py:64-176 롤 그래프 상수도 사실. 개선안은 ADR(decisions.md:699-712)이 allowlist의 '존재'만 요구하고 privileged host filesystem actor를 threat model 밖으로 명시하므로 root-owned 설치 데이터로 옮겨도 계약 위반이 아니며, verified root-owned 패턴 선례가 풍부하다(cli.py:1135, legacy_override_retirement.py:459, map_application_300.py:2351). 수정 필요 4건: (a) "다른 호스트에 이식되지 않는다"는 과장 — allowlist 값의 ${VAR:-default}는 _expand_env_path(6993+)가 effective env(.env+process env, compose_service.py:2450+)로 확장되고 7262-7269가 resolved 경로를 비교하므로, 등재된 bind는 KOR_TRAVEL_GEO_PGDATA 등 env 설정만으로 코드 수정 없이 이식된다. 실제 문제는 '커밋된 개인 기본값', '기본값 한쪽만 수정 시 fail-close', '신규 bind 키'로 한정해 기술해야 한다. (b) docker-targets.yml binds: 섹션 옵션은 현재 로더가 KOR_TRAVEL_DOCKER_MANAGER_TARGETS_FILE 경로 override 허용 + 소유권/권한 검증 전무(registry.py:16-35)라 그대로 옮기면 보안 회귀 — pin registry식 root-owned 검증과 production override 거부를 명시 요건으로 승격해야 "신뢰 모델 유지" 전제가 성립한다. (c) 실질 required는 14가 아니라 15개 — _PINVI_DB_INIT_SERVICE는 frozenset에 없지만 _validate_pinvi_db_init_identity(4140)와 무조건 인덱싱 루프(4176-4193 services[service_name])가 부재를 거부한다. (d) production ensure는 이미 원천 거부(compose_service.py:4653-4657)라 "production 모드로 한정"은 ensure에 한해 공허 — required-set 유지는 pinned-rebuild와 production save/mutation 경로에 실질 적용되고, dev 완화는 문서 전역 보호 이름/값 스캔(3644-3675)을 무조건 유지한 채 다수 cross-service validator를 존재-조건부로 바꾸는 광범위 감사가 필요하다(effort L 추정은 타당, 그 이하로 축소 불가).


## GM-18: 백업 role과 pinned pair role이 백엔드·프론트 다층 하드코딩 — config 파생으로 전환

- **심각도/규모**: P2 / M · **분류**: generality · **검증**: REVISED · **E2E**: mock
- **관련 파일**: backend/src/kor_travel_docker_manager/services/standalone_backup.py; backend/src/kor_travel_docker_manager/services/pinned_runtime_release.py; backend/src/kor_travel_docker_manager/cli.py; backend/src/kor_travel_docker_manager/api/routes.py; frontend/src/lib/api.ts; frontend/src/components/BackupHistoryPanel.tsx; frontend/src/components/RuntimePinPanel.tsx; config/docker-targets.yml

**문제**: 새 프로젝트 DB의 백업을 붙이려면 standalone_backup.py의 BackupRole Literal·BACKUP_ROLES·_ROLE_CONFIG(41-93: 컨테이너 env/기본 컨테이너명/DB명), frontend api.ts:186의 role union, BackupHistoryPanel.tsx:20-33의 ROLES 목록·보존 힌트까지 최소 3파일 코드 수정이 필요하다. 같은 정보가 docker-targets.yml connection 문자열에 비구조화 텍스트로 중복돼 정본이 둘이다. pinned pair role도 마찬가지: 정본인 pinned_runtime_release.py:31-32 canonical map 외에 cli.py:1569 argparse choices, routes.py:93 Literal, frontend api.ts:375/407 union, RuntimePinPanel.tsx:18-19/390 라벨·select가 각각 리터럴 복제라, role 하나 추가·개명에 4개 층 5곳 이상을 맞춰야 하고 하나라도 빠지면 그 층에서만 조용히 거부/비표시된다.

**개선안**: docker-targets.yml 컨테이너 정의에 구조화 필드 databases: [{backup_role, db}]를 추가하고 backend가 BACKUP_ROLES/_ROLE_CONFIG를 파생한다(cli.py:1675 choices는 이미 BACKUP_ROLES 참조라 자동 반영). 프론트는 GET /api/v1/backups 응답(또는 별도 roles 목록)에서 select와 보존 힌트를 구성해 하드코딩을 제거하고, connection 문자열의 DB 나열도 이 필드에서 생성한다. pinned role은 PINNED_ROLES = tuple(canonical map)을 단일 정본으로 CLI choices/API 검증에 쓰고, 프론트는 pins 응답 sources(RuntimePinPanel.tsx:321이 이미 순회)에서 role 목록·라벨을 파생한다. GM-11의 config 검증 위에서 작업한다.

**실익**: 이 도구에서 가장 자주 반복될 확장 작업(새 DB 백업, pinned 대상 추가)이 backend 한 곳 또는 config 등록만으로 끝나고, 층간 불일치 버그군이 사라진다.

**검증 노트** (구현 시 본문보다 우선):

사실관계는 대부분 라인 단위로 확인됨: standalone_backup.py:41-48 `BackupRole = Literal[...]`, 50-57 `BACKUP_ROLES`, 74-93 `_ROLE_CONFIG`(env/컨테이너명/DB명) 하드코딩; cli.py:1675/1688/1695/1709 `choices=BACKUP_ROLES`(자동 반영 주장 정확); api.ts:186 role union; BackupHistoryPanel.tsx:18-26 `ROLE_OPTIONS`; pinned 쪽 cli.py:1569 `choices=["map", "pinvi"]`, routes.py:93 `role: Literal["map", "pinvi"]`, api.ts:375/407 union, RuntimePinPanel.tsx:17-20 `ROLE_LABELS`·390 `(['map', 'pinvi'] as const)`; docker-targets.yml:36/44/52/60 connection 문자열의 "(DBs: ...)" 비구조화 중복 — 모두 실재한다. 그러나 4가지 수정 필요. (1) pinned 절반의 확장성 편익이 과장: 단일 정본 tuple은 이미 존재한다(pinned_runtime_release.py:28 `RUNTIME_SOURCE_ROLES: Final[tuple[...]] = ("map", "pinvi")`) — 새 PINNED_ROLES 도입이 아니라 cli.py:1569·routes.py:93 두 곳 배선이 전부다. 더구나 pair 구조가 계약에 동결돼 있어(ADR-40, docs/decisions.md:2508-2511 "digest 계산 규칙은 kor-travel-map attestation과 공유하는 계약이므로 한 바이트도 바꾸지 않았다"; cli.py:1585-1613 rotate-pair/block의 --map-revision·--pinvi-revision 고정 인자; api.ts:383-385 BlockedPinset의 map_revision/pinvi_revision 필드) role 추가·개명은 리터럴 정리로 싸지는 작업이 아니라 pinset digest·attestation·registry 호환을 깨는 아키텍처 변경이다. "pinned 대상 추가가 config 등록만으로 끝난다"는 benefit은 삭제해야 한다. (2) "조용히 비표시" 주장 일부 과장: RuntimePinPanel.tsx:323 `ROLE_LABELS[source.role] ?? source.role` fallback으로 미등록 role도 표에 표시되고, BackupHistoryPanel의 'all' 목록 렌더링은 ROLE_OPTIONS에 gating되지 않아 미등록 role manifest도 보인다. 실제 드리프트 지점은 select/filter/생성 버튼과 routes.py:93의 422 거부다. (3) 백업 절반은 config 파생보다 싼 대안이 있다: 백엔드는 이미 단일 파일 정본(Literal·tuple·_ROLE_CONFIG가 한 모듈에 인접, CLI·API 모두 BACKUP_ROLES 파생 — routes.py:166/197/228/236)이므로, 남은 실질 드리프트는 프론트 2곳뿐이다. GET /api/v1/backups(또는 별도 endpoint)에 roles 목록을 실어 프론트가 파생하면 config 스키마 변경·GM-11 의존 없이 층간 불일치가 해소된다. _ROLE_CONFIG를 docker-targets.yml `databases:` 필드로 옮기는 안은 env override 변수명(standalone_backup.py:74-93, 658-667에서 적용; geo만 의도적 리터럴 고정)을 담을 자리가 없어 스키마가 더 커져야 하고, 백업 대상 DB명이 코드 소유에서 검증 안 된 repo YAML 소유로 넘어간다(registry.py:24-27은 소유권 검증 없는 yaml.safe_load — GM-11 전제라 해도 순이득이 불분명). connection 문자열 생성은 표시용 텍스트라 cosmetic이다. (4) 누락된 4번째 층: scripts/run-standalone-backup.sh:20-26의 cron role allowlist(H49 정책)와 BackupHistoryPanel.tsx:30-34 `EXPECTED_INTERVAL_HOURS`(그 wrapper의 cron 주기 미러, 태스크가 말한 "보존 힌트"가 아니라 신선도 힌트)는 정책이라 config에서 파생 불가 — 주기 백업 경로는 개선 후에도 수동 편집이 남는다. 요약: 문제는 실재하고 프론트 파생·pin 리터럴 배선은 타당하나, 정본을 config가 아닌 backend 코드에 두고 API로 role 목록을 서빙하는 방향으로 improvement를 축소·수정해야 하며, pinned 확장성 benefit은 철회해야 한다. 수정된 범위면 effort M은 현실적(원안 범위는 M 상한 초과 소지).


## GM-19: 죽은 코드 일괄 제거 — 구 C6c 경로 ~650줄, 미사용 프론트 의존성, 무소비 port_policy, 무참조 API key 게이트

- **심각도/규모**: P2 / S · **분류**: dead-code · **검증**: REVISED · **E2E**: 불필요
- **관련 파일**: backend/src/kor_travel_docker_manager/services/c6c_deployment.py; backend/src/kor_travel_docker_manager/services/compose_service.py; backend/src/kor_travel_docker_manager/api/security.py; backend/src/kor_travel_docker_manager/main.py; frontend/src/components/DashboardClient.tsx; frontend/package.json; config/docker-targets.yml

**문제**: (1) src/tests 전체 grep으로 무참조가 확인된 c6c_deployment.py의 구세대 공개 함수 ~650줄: validate_resolved_compose_secret_isolation(3332-3676), run_map_ops_smoke(4344-4460), run_ui_auth_smoke(5448-5478)+유일 호출처 run_map_ui_auth_preflight(5387-5447), validate_compose_env_file_isolation(4047-4108), require_local_c6c_image(6856-6878) 및 compose_service.py의 구 C6c 빌드용 git export(763-928, tarfile.extractall 포함 — 보안 민감 죽은 코드)와 get_c6c_deployment_lock_path(1486). (2) DashboardClient.tsx:62-64의 react-hook-form/zod 더미 참조가 두 라이브러리를 번들에 강제 포함(실사용 0건, 폼 검증은 자체 configValidation.ts). (3) docker-targets.yml port_policy/port_band(3-17)를 소비하는 코드 0건 — 정책처럼 보이나 어겨도 아무 일 없음. (4) require_public_api_key(security.py:15-27)는 부착 라우트가 0개인데 admin UI는 키 발급/폐기 전체 흐름을 제공해 가짜 보안 어포던스이고, 실제 무인증 표면인 GET /metrics(main.py:217-224)는 0.0.0.0:12901에서 인프라 토폴로지를 LAN에 노출한다.

**개선안**: 무참조 함수와 죽은 진입점에서만 도달하는 map-dataset 검증 서브트리를 삭제하고, 프론트 더미 import 3줄과 package.json 의존성을 제거한다. port_policy는 삭제하고 docs/ports.md를 유일 정본으로 남기거나 expected_ports 교차 검증으로 승격(작업 중 결정). require_public_api_key는 /metrics에 선택 적용(KTDM_METRICS_REQUIRE_KEY=1)해 첫 소비처를 만들거나 키 관리 패널을 flag 뒤로 숨겨 decisions.md open 항목을 종결한다. GM-20 파일 분리 전에 수행해 이동 대상 코드량을 줄인다.

**실익**: 두 거대 파일 합계 ~650줄 감소로 적대 리뷰 표면이 실제 실행 경로로 좁혀지고, tarfile.extractall 같은 보안 민감 죽은 코드의 오재사용 위험과 가짜 보안 어포던스, 번들 수십 KB가 함께 사라진다.

**검증 노트** (구현 시 본문보다 우선):

핵심 주장은 전부 사실로 확인 — 단 두 곳의 범위 오류를 고쳐야 한다. (1) compose_service.py 763-928 전체를 죽은 코드로 지목한 것은 과대 범위다. `_resolve_repository_path`(804-822)는 살아 있다: `_map_source_environment_contract_version`(1413)이 1425에서 호출하고("repository = _resolve_repository_path(environment.get(\"KOR_TRAVEL_MAP_REPO_DIR\", ...)"), 이 함수는 live 경로인 `_validate_pinned_runtime_candidate_build_contract`가 5930에서 호출하며 test_f1d_compose_contract.py:2139이 검증한다. 실제 죽은 부분은 `_clean_repository_revision`(763-801), `_c6c_source_snapshot_environment`(824-862), `_export_git_tree`(865-926, 920의 `archive.extractall(target)` 포함)로 한정해야 한다 — tarfile.extractall 보안 민감 죽은 코드라는 지적 자체는 유효. (2) improvement의 "죽은 진입점에서만 도달하는 map-dataset 검증 서브트리"는 완전 무참조가 아니다: `_validate_map_dataset_identity`(c6c_deployment.py:5678)는 src에서 죽은 `_validate_map_dataset_row`(5718)만이 호출하지만, test_c6c_cancel_probe_resume.py:73-104(`assert c6c._validate_map_dataset_identity(identity)`)가 직접 단위 테스트하므로 삭제 시 해당 테스트도 함께 제거해야 한다. 나머지는 라인 단위로 전부 확인: 5개 c6c 함수(3332/4047/4344/5387+5448/6856, 합계 ~639줄)는 repo 전체 grep에서 정의와 5451의 내부 호출 1건 외 참조 0건이고, live 후속 세대(validate_resolved_compose_candidate_protected_values, run_pinvi_canonical_smoke, validate_current_map_ui_auth_runtime)만 compose_service.py:24-65에서 import된다. get_c6c_deployment_lock_path(1486)는 호출처 0(내부 `_capture_c6c_deployment_lock_snapshot`은 1595에서 live). DashboardClient.tsx:21-22+62-64가 react-hook-form/zod의 유일 사용처(package.json:18,20). port_policy/port_band는 config/docker-targets.yml에만 존재하고 registry.py:29-35는 containers/targets/dependency_order만 검증. require_public_api_key(security.py:15-27)는 import 0건, admin.py:55-97+AdminSettingsPanel.tsx:79-385의 키 발급/폐기 UI는 실재, /metrics(main.py:217-224)는 무인증이며 prod는 0.0.0.0:12901(docs/prod-deployment.md:241), decisions.md:625에 open 항목 실재. Prometheus는 127.0.0.1:12901을 scrape(config/prometheus/prometheus.yml:22)하므로 KTDM_METRICS_REQUIRE_KEY 기본 off 설계는 기존 scrape를 깨지 않는다. 계약 충돌 없음(삭제 대상은 모두 구세대·무호출이라 보안 게이트/lock/M05 동결에 영향 없고, decisions.md:625는 종결을 권유하는 open 항목). effort S는 기계적 삭제엔 타당하나 /metrics 게이트+테스트와 port_policy 처분 결정이 겹치면 S~M 경계다.


## GM-20: 서비스 계층 분리 1단계 — errors/capabilities 모듈 신설과 프라이빗 크로스 import·순환 의존 해소

- **심각도/규모**: P2 / M · **분류**: complexity · **검증**: CONFIRMED · **E2E**: 불필요
- **관련 파일**: backend/src/kor_travel_docker_manager/services/c6c_deployment.py; backend/src/kor_travel_docker_manager/services/compose_service.py; backend/src/kor_travel_docker_manager/services/docker_service.py; backend/src/kor_travel_docker_manager/services/metrics_collector.py; backend/src/kor_travel_docker_manager/main.py

**문제**: compose_service(8,029줄)는 c6c_deployment(7,846줄)에서 _MANAGED_COMPOSE_MUTATION_CAPABILITY 등 프라이빗 10개를 import하고(24-65), docker_service는 _PINVI_POSTGRES_INITDB_ARGS·_capture_compose_environment_snapshot을 import(14-34)하며 compose_service._capture_transaction_unlocked(997)/_capture_candidate_transaction_unlocked(1059) 같은 프라이빗 메서드를 직접 호출한다. main.py도 _PROMETHEUS_CONTENT_TYPE을 import하고, metrics_collector↔docker_service는 함수 내부 지연 import(545, 830)로 순환을 우회한다. 기반 예외 DeploymentContractError가 7,846줄 파일 안에 있어 leaf 모듈 전부가 거대 모듈 import 비용과 결합을 떠안는다. 이 암묵 결합이 두 거대 파일 분해의 선행 차단물이다 — 경계가 API로 고정되기 전에는 어떤 분리도 프라이빗 참조를 끊어먹는다.

**개선안**: 1단계(이 태스크): services/errors.py(DeploymentContractError 계열 — GM-06의 code 필드와 함께 이동)와 services/capabilities.py(mutation capability 센티널)를 신설해 하위 의존을 역전시키고, docker_service가 쓰는 transaction 캡처 2종을 ComposeService의 공개 메서드로 승격하며, metrics_collector 순환은 docker client 접근을 콜백/프로토콜 주입으로 끊는다. 후속(각각 별도 태스크로 이어서): (a) c6c_deployment 4분할 — c6c_smoke.py(4344-6533의 HTTP+fixture+검증기 ~2,190줄이 첫 후보)/c6c_locks.py/c6c_config.py/compose_candidate_contract.py, (b) compose_service 3분할 — compose_transaction.py/map_application_300_execution.py/pinned_rebuild_workflow.py, (c) rebuild_pinned_runtime(6461-7833, 1,373줄)의 phase-handler 테이블화와 root/finalize 쌍둥이 ~400줄(6212-6459) 파라미터화.

**실익**: 모듈 경계가 공개 API로 고정되어 '프라이빗을 고쳤는데 다른 파일이 깨지는' 결합이 제거되고, 범용 관리툴 코어(타깃 up/ps/logs)와 M05 전용 워크플로를 갈라내는 후속 분해 전체가 가능해진다.

**검증 노트** (구현 시 본문보다 우선):

모든 사실 주장을 라인 단위로 검증했고 전부 정확하다. (1) 파일 크기: compose_service.py 8,029줄, c6c_deployment.py 7,846줄, docker_service.py 1,360줄, metrics_collector.py 944줄 — 정확 일치. (2) 프라이빗 크로스 import: compose_service.py:24-65에서 밑줄 이름 정확히 10개(_MANAGED_COMPOSE_MUTATION_CAPABILITY, _MAP_APPLICATION_FRESH_300_SERVICE, _MAP_APPLICATION_FRESH_FINALIZE_SERVICE, _MAP_RUNTIME_SERVICES, _PINNED_RUNTIME_REBUILD_MUTATION_CAPABILITY, _PINVI_ADMIN_BOOTSTRAP_SERVICE, _PINVI_API_SERVICE, _PINVI_DB_RUNTIME_ROLE_SERVICE, _assert_candidate_single_file_boundary, _expand_env_path); docker_service.py:16의 _PINVI_POSTGRES_INITDB_ARGS, :29의 _capture_compose_environment_snapshot, :997의 compose_service._capture_transaction_unlocked(, :1059의 compose_service._capture_candidate_transaction_unlocked( — 라인 번호까지 일치(추가로 :15에 _MANAGED_COMPOSE_MUTATION_CAPABILITY도 import, 태스크는 오히려 과소 기술). main.py:24-25가 _PROMETHEUS_CONTENT_TYPE을 import해 :222에서 사용. (3) 순환: metrics_collector.py:13이 docker_service를 모듈 레벨 import하고 :380에서 docker_service._get_client() 프라이빗 호출; docker_service.py:545/830이 "순환 참조 방지" 주석과 함께 지연 import — 주장 그대로. (4) 기반 예외: DeploymentContractError는 c6c_deployment.py:1854에 있고, database_runtime·pinned_runtime_* 등 15개+ leaf 모듈이 이 이름 하나만을 위해 7,846줄 모듈을 import한다(grep으로 22개 파일 확인). (5) 계약 안전성: capability 게이트는 c6c_deployment.py:2039/2048의 `is` identity 비교라 센티널을 단일 인스턴스로 유지한 채 capabilities.py로 옮기면 게이트가 그대로 보존된다 — 밑줄 privacy는 이미 두 모듈이 넘고 있어 실질 보안 약화 없음. M05 동결은 frozen env/Compose bytes의 런타임 규율(decisions.md:1681,1715)이지 소스 레이아웃 동결이 아니고, 관련 ADR 위반 없음. (6) 후속 분해 지도도 정확: rebuild_pinned_runtime은 compose_service.py:6461에서 시작해 다음 def가 7834 — 정확히 1,373줄; root/finalize 6개 메서드는 6212-6459에 실재(단, 이 범위는 248줄이라 merged_from의 "~400줄"은 다른 쌍둥이 포함 추정치로 약간 부풀려짐 — 후속 태스크 범위라 본 태스크 판정에 영향 없음). (7) 더 값싼 대안 부재: 이름만 공개로 바꾸는 in-place 방식은 leaf의 거대 모듈 결합·순환·분해 차단을 하나도 못 풀고 나중에 옮길 때 다시 깨진다. effort M은 c6c_deployment/compose_service에 재수출 별칭을 남기면 테스트(23개 파일 321회 참조) churn 없이 가능해 현실적. 구현 시 유의점 2가지: (a) _capture_*_unlocked의 "_unlocked"는 lock 선보유 규율 표기다 — docker_service.py:951이 c6c_deployment_lock_from_environment()를 잡은 뒤에만 호출하므로, 공개 승격 시 lock snapshot을 인자로 요구하거나 선행조건을 시그니처/문서에 명시해 lock 규율을 보존해야 한다; (b) MANAGED_CONTAINERS는 이미 registry.py 소유(docker_service.py:35가 재수출)라 metrics_collector는 registry에서 직접 가져오고 docker client accessor만 주입하면 순환이 최소 비용으로 끊긴다.

