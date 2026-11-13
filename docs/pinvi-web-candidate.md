# Pinvi web 단독 후보 적용

## 불변식

이 경로는 이미 생성한 immutable web 이미지 하나만 선택한다. Map·Pinvi API·코드 서버·DB·one-shot은 명령에 넣지 않는다. pair pin과 committed 배포 기록, Map component current는 읽기만 한다. 기존 `pinvi-pair rebuild-pinned`, `apply-component-ui`, Map `apply-candidate`, adopt/recreate 의미는 바꾸지 않는다.

정본 선언은 `targets.pinvi.ui_candidate`의 실제 `pinvi-web` 역할과 canonical source URL, `service_releases.pinvi.groups.ui`다. 새 일반 selector나 `pinvi-ui` 같은 가짜 역할을 만들지 않는다. 선택은 `runtime_topology`의 `pinvi_web` slot과도 일치해야 한다.

## 진입과 현재 기준선

SDK와 catalog를 함께 검토·병합·정식 설치한 뒤 기존 공식 명령을 사용한다. source clone은 root 소유의 canonical Git clone이고 승인 merged SHA가 origin/main에 포함되어야 한다. CLI는 fetch/build/pull/retag를 수행하지 않는다.

~~~bash
ktdctl service-release apply-candidate pinvi --group ui \
  --source-clone "$PINVI_APPROVED_SOURCE_CLONE" --source-sha "$PINVI_MERGED_SHA" \
  --image-id "$PINVI_APPROVED_WEB_IMAGE_ID" --dry-run --json
# 동일 승인 tuple의 실제 적용은 위 명령에서 --dry-run만 --confirm으로 바꾼다.
~~~

Map과 Pinvi의 기존 component current가 모두 있어야 한다. 미등록이면 자동 등록하지 않고 실패한다. 초기 Pinvi web 등록이 필요한 경우 운영자는 실제 singleton/healthy 이미지와 committed pair의 web ID를 먼저 대조한 다음 기존 adopt 절차로 등록한다. 관측되지 않은 drift를 `adopt --replace-current`로 덮어 이 경로를 통과시키지 않는다. 최초 current와 pair web ID가 다르면 후보 적용은 거절된다.

Map UI current는 과거 pair receipt보다 전진했을 수 있다. 같은 Compose project의 기존 catalog에 맞는 current를 읽고 실제 singleton/health/immutable ID와 OCI metadata가 일치한 경우에만 그 UI ID를 비선택 기준선으로 사용한다. 실제 컨테이너가 current와 다르거나 current가 없으면 거절한다. 모든 Map runtime을 과거 이미지로 되돌리거나 최신이라는 이유만으로 live drift를 채택하지 않는다.

다른 paired runtime·companion은 기존 committed receipt의 완전성·owner/reference 관계를 그대로 검사한다. frozen Compose에 원래 companion 검사를 적용한 뒤 검증된 Map UI current ID를 유지한다. pair pin·receipt·Map pointer를 다시 쓰거나 새 receipt를 꾸미지 않는다.

## 이미지와 환경

후보 ID·OCI revision/source는 승인 tuple과 일치해야 한다. source label만으로 전체 빌드 bytes를 증명했다고 주장하지 않는다. 운영자는 별도 실제 merged normal build 증거를 확인한다. 이 경로는 존재하지 않는 producer flag나 자체 normal-build receipt를 요구하거나 생성하지 않는다.

공개 입력은 현재 web 이미지 `Config.Env`의 모든 `NEXT_PUBLIC_*` 값과 후보 baked 값의 집합 동등성으로 대조한다. 현재 web effective 환경도 그 baked 값과 같아야 한다. 필수 API URL·VWorld 입력이 비어 있거나 차이가 있으면 mutation 전에 거절한다. 값은 결과/오류에 출력하지 않는다. 실제 root config와의 정합·승인된 입력 provenance는 운영자의 build 증거와 함께 따로 확인한다.

기존 component 환경 보존 helper를 재사용해 현재 effective 값을 유지하고 검증한 후보의 `PINVI_SOURCE_REVISION`만 전진시킨다. 후보 기본값을 합치고 raw 달러를 기존 Compose 경계에서 처리하며 env_file을 재독해 인증/세션/키를 바꾸지 않는다. API·코드 서버 환경은 수정하지 않는다.

## 실행·실패·검증 경계

기존 host-wide G를 source 재확인부터 자식 종료·사후 관측·current 발행까지 사용한다. render는 메모리에서 고정하며 기존 G FD 전달 helper로 `up -d --no-deps --no-build --pull never --force-recreate --wait`와 `pinvi-web` 하나만 실행한다. 새 락·일반 배포 프레임워크는 없다.

전체 비선택 container identity/config/mount/network, pair 기준선/pin, 모든 관측 current pointer, 선택 UI 환경을 실행 전·후와 발행 직전에 확인한다. health와 불변식이 실패하면 Pinvi current를 발행하지 않고 원 실패를 보존한다. 이미 실행된 web 교체를 무변경 성공이나 자동 rollback으로 표현하지 않는다.

단위 회귀는 등록/역할·current drift·source OCI·baked/effective 입력·비선택 변화 거절과 전진한 Map UI 보존/단일 web 명령을 검증한다. 실제 Engine·SDK 설치·운영 적용과 정상 계정 수용은 이 소스 검증에 포함하지 않는다. 프런트엔드·의존성·C11·이미지는 이 변경에서 수정/재생성하지 않는다. 실제 적용 뒤 별도 정상 로그인/로그아웃·지도 bitmap decode·반응형 수용이 필요하다.
