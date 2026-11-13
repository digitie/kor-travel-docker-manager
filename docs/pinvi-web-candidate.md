# Pinvi web 단독 후보 적용

## 불변식

이 경로는 이미 생성한 immutable web 이미지 하나만 선택한다. Map·Pinvi API·코드 서버·DB·one-shot은 명령에 넣지 않는다. pair pin과 committed 배포 기록, Map component current는 읽기만 한다. 기존 pair 재구축·apply-component-ui·recreate와 기존 current 등록 의미는 유지한다. Pinvi 최초 기본 root 준비만 아래 조건으로 허용한다. Map과 Pinvi apply-candidate는 동일하게 검증된 비선택 UI current의 전진을 관측한다.

정본 선언은 `targets.pinvi.ui_candidate`의 실제 `pinvi-web` 역할과 canonical source URL, `service_releases.pinvi.groups.ui`다. 새 일반 selector나 `pinvi-ui` 같은 가짜 역할을 만들지 않는다. 선택은 `runtime_topology`의 `pinvi_web` slot과도 일치해야 한다.

## 진입과 현재 기준선

SDK와 catalog를 함께 검토·병합·정식 설치한 뒤 기존 공식 명령을 사용한다. source clone은 root 소유의 canonical Git clone이고 승인 merged SHA가 origin/main에 포함되어야 한다. CLI는 fetch/build/pull/retag를 수행하지 않는다.

~~~bash
ktdctl service-release apply-candidate pinvi --group ui \
  --source-clone "$PINVI_APPROVED_SOURCE_CLONE" --source-sha "$PINVI_MERGED_SHA" \
  --image-id "$PINVI_APPROVED_WEB_IMAGE_ID" --dry-run --json
# 동일 승인 tuple의 실제 적용은 위 명령에서 --dry-run만 --confirm으로 바꾼다.
~~~

선택 Pinvi의 component current는 있어야 한다. 비선택 Map은 검증된 current가 있거나 아래의 정확한 legacy 부재 조건을 충족해야 한다. 후보 적용에서 자동 adopt하지 않는다. 초기 Pinvi web 등록이 필요한 경우 운영자는 실제 singleton/healthy 이미지와 committed pair의 web ID를 먼저 대조한 다음 기존 adopt 절차로 등록한다. 관측되지 않은 drift를 `adopt --replace-current`로 덮어 이 경로를 통과시키지 않는다. 최초 current와 pair web ID가 다르면 후보 적용은 거절된다.

Map 또는 Pinvi UI current는 과거 pair receipt보다 전진했을 수 있다. 같은 Compose project의 기존 catalog에 맞는 current를 읽고 실제 singleton/health/immutable ID와 OCI metadata가 일치한 경우에만 그 UI ID를 양방향 비선택 기준선으로 사용한다. 선택 UI의 최초 receipt 일치 검사는 current ID로 덮어쓰지 않는다. 실제 컨테이너가 존재하는 current와 다르면 거절한다. Map current 부재는 아래의 원 pair 일치 조건에서만 다룬다. 모든 Map runtime을 과거 이미지로 되돌리거나 최신이라는 이유만으로 live drift를 채택하지 않는다.

다른 paired runtime·companion은 기존 committed receipt의 완전성·owner/reference 관계를 그대로 검사한다. frozen Compose에 원래 companion 검사를 적용한 뒤 검증된 Map·Pinvi UI current ID를 유지한다. pair pin·receipt·Map pointer를 다시 쓰거나 새 receipt를 꾸미지 않는다.

## 이미지와 환경

후보 ID·OCI revision/source는 승인 tuple과 일치해야 한다. source label만으로 전체 빌드 bytes를 증명했다고 주장하지 않는다. 운영자는 별도 실제 merged normal build 증거를 확인한다. 이 경로는 존재하지 않는 producer flag나 자체 normal-build receipt를 요구하거나 생성하지 않는다.

공개 입력은 현재 web 이미지 `Config.Env`의 모든 `NEXT_PUBLIC_*` 값과 후보 baked 값의 집합 동등성으로 대조한다. 현재 web effective 환경도 그 baked 값과 같아야 한다. 필수 API URL·VWorld 입력이 비어 있거나 차이가 있으면 mutation 전에 거절한다. 값은 결과/오류에 출력하지 않는다. 실제 root config와의 정합·승인된 입력 provenance는 운영자의 build 증거와 함께 따로 확인한다.

기존 component 환경 보존 helper를 재사용해 현재 effective 값을 유지하고 검증한 후보의 `PINVI_SOURCE_REVISION`만 전진시킨다. 후보 기본값을 합치고 raw 달러를 기존 Compose 경계에서 처리하며 env_file을 재독해 인증/세션/키를 바꾸지 않는다. API·코드 서버 환경은 수정하지 않는다.

## 실행·실패·검증 경계

기존 host-wide G를 source 재확인부터 자식 종료·사후 관측·current 발행까지 사용한다. render는 메모리에서 고정하며 기존 G FD 전달 helper로 `up -d --no-deps --no-build --pull never --force-recreate --wait`와 `pinvi-web` 하나만 실행한다. 새 락·일반 배포 프레임워크는 없다.

전체 비선택 container identity/PID/config/mount/network, pair 기준선/pin, 모든 관측 current pointer, 선택 UI 환경을 실행 전·후와 발행 직전에 확인한다. health와 불변식이 실패하면 Pinvi current를 발행하지 않고 원 실패를 보존한다. 이미 실행된 web 교체를 무변경 성공이나 자동 rollback으로 표현하지 않는다.

단위 회귀는 등록/역할·current drift·source OCI·baked/effective 입력·비선택 변화 거절과 전진한 Map UI 보존/단일 web 명령을 검증한다. 실제 Engine·SDK 설치·운영 적용과 정상 계정 수용은 이 소스 검증에 포함하지 않는다. 프런트엔드·의존성·C11·이미지는 이 변경에서 수정/재생성하지 않는다. 실제 적용 뒤 별도 정상 로그인/로그아웃·지도 bitmap decode·반응형 수용이 필요하다.

## 초기 Map 호환성과 양방향 후속 실행

새 Pinvi catalog가 있지만 Pinvi current가 아직 없는 초기 상태에서 Map apply-candidate는 정확한 web-only catalog임을 확인하고 원래 pair inventory 검사를 유지한다. 실제 Pinvi web이 committed ID 그대로인 경우만 계획할 수 있다. 이미 전진한 web의 current가 없거나 pointer가 dangling/invalid/stale이면 거절하며 자동 adopt하지 않는다. 일반 compose-args/resolve/recreate는 이 후보 전용 호환 분기를 쓰지 않으므로 설치 직후 아래 공식 bootstrap을 먼저 마친다.

Pinvi 단독 적용 뒤에도 같은 Map dry-run이 정상 동작하고, Map current가 전진한 경우의 Pinvi 후보도 정상 동작해야 한다. 기존 pair receipt와 companion owner/reference를 그대로 읽고, 검증한 두 UI current만 frozen 구성에 복원한다. 원 B의 단방향 거절 반례는 이 대칭 경로의 회귀다. stale resolved overlay를 허용해 해결하지 않는다.

공식 초기 등록은 canonical pair 기록·pins와 기존 Map current 또는 아래의 강한 legacy 부재 관측을 검증한 뒤 기존 adopt API/CLI를 사용한다. 아직 Pinvi current가 없고 실제 web ID·OCI revision이 committed 기준선과 일치하는 경우에 한한다. replace-current, source SHA 강제 지정, staging metadata를 이용한 current 생성은 하지 않는다. 기존 G 안에서 사전/사후 관측과 공식 adopt_release를 함께 수행할 수 있으며 이 helper의 재진입 G 계약을 유지한다. 등록은 metadata만 발행하고 runtime을 재생성하지 않는다. 실제 호스트의 root와 현재 pair/image tuple은 운영자가 새로 대조해야 하며 이 문서의 예시는 운영 관측 결과가 아니다.

## Map CURRENT가 없던 기존 호스트의 초기 등록

Map pointer가 정확히 없는 경우만 후보 전용 planning view에서 sibling Map overlay를 생략한다. 등록된 Map catalog는 ui-only이고 같은 project/working_dir와 실제 Map UI 역할을 가리켜야 한다. Map catalog를 지우거나 역할을 바꿔 우회할 수 없다. 일반 compose-args/resolve/recreate는 여전히 원 current 조건을 사용한다.

이때 실제 Map singleton은 committed pair의 immutable ID 그대로여야 한다. canonical OCI source와 pair revision, 양의 정수 PID, running과 설정된 healthy 상태를 확인한다. Map current가 전진한 경우처럼 live ID로 기대값을 덮어쓰지 않는다. PID·ENV/config·mount/network는 기존 전체 nonselected 대조로 고정하며 Map CURRENT의 정확한 부재와 pair/health/source를 실행 전·후·발행 직전에 다시 검사한다. dangling/invalid/stale current와 도중 나타난 pointer는 실패다. Map 표식·manifest·receipt를 꾸미거나 Map runtime을 재생성하지 않는다.

기본 release root가 아예 없는 호스트에서 기존 adopt API는 **Pinvi ui-only 실제 web 역할**에 한해서만 trusted parent와 정상 own runtime을 관측한 뒤 기본 root를 준비한다. 사용자 지정 absent root·다른 서비스·unsafe/dangling 기존 root에는 이 생성 예외를 적용하지 않는다. Map 디렉터리나 current를 만들지 않고 관측한 old Pinvi metadata만 replace-current 없이 발행한다. root 생성 전 own runtime 관측도 발행 직전 비교까지 유지한다. 초기 등록 실패 후 남을 수 있는 빈 root/불완전 leaf를 정상 current라고 보고하거나 자동 삭제하지 않는다.

운영자는 같은 기존 G 구간에서 pair/live Map 강한 관측과 old web 관측을 먼저 하고 공식 Pinvi adopt와 사후 검증을 수행한다. 이후 같은 승인 tuple의 dry-run/confirm으로 web 하나만 적용한다. 이 소스 회귀는 fake Engine이며 실제 SDK 설치·root 등록·배포·정상 계정 수용 완료를 뜻하지 않는다.
