# PinVi 관리자 Feature 읽기의 전용 credential 배선

## 현재 상태와 범위

운영 PinVi 목록422의 소스 원인은 기존 Map service token을 관리자 BFF 권한으로 쓸 수 없다는 경계다. Manager555 지도 키 build wiring과 분리한다. 이 변경은 Map의 전용 scope pinvi:admin-features:read 및 정확 GET /v1/service/admin-features와 /{feature_id}를 소비할 배선만 소유한다. Map API와 PinVi client 구현은 별도 PR이다.

현재는 소스 checkpoint다. 기존 legacy/ops/admin proxy credential의 권한을 넓히거나 전역 관리자 secret을 PinVi에 전달하지 않는다. 새 원문 생성·파일 쓰기·운영 provision·pair 회전·이미지 변경은0이다. 만료 프레임워크나 별도 인증 체계를 추가하지 않는다.

## 하나의 원문과 두 소비 위치

정본 root env에는 PINVI_KOR_TRAVEL_MAP_ADMIN_FEATURES_READ_TOKEN 원문 한 개만 둔다. 기존 effective environment와 frozen Compose 경계가 여기서 SHA-256을 파생해 KOR_TRAVEL_MAP_API_PINVI_ADMIN_FEATURES_READ_TOKEN_SHA256을 Map API에만 전달한다. 원문은 PinVi ordinary API 한 곳에만 전달한다. UI·Dagster·bootstrap·labels·command·다른 env로 전달하지 않는다.

원문이 없으면 digest도 비워 비활성을 유지한다. digest만 외부에서 넣거나 원문과 다른 digest를 선언하면 거부한다. 원문은 32~4096자의 공백 없는 printable ASCII이며 운영 생성은 별도의 무작위 URL-safe 값을 사용한다. legacy·ops·admin proxy·기존 scoped/cache-target·수동 생성 credential과 같은 값은 거부한다. 키별 상한과 서버의 비활성/교체 digest 거부는 해당 API PR에서도 검증한다.

새 원문은 C6c config repr에 나타나지 않는다. 기존 민감 키 마스킹과 protected-reference 검증을 그대로 적용한다. raw/resolved source는 자기 정본 변수만 참조하고, UI 편집에서는 새 두 키의 변경·추가·삭제를 저장 단계에 거부한다. runtime inspection도 활성 credential의 정확한 두 위치와 값을 요구한다. 미 provision 상태의 이전 runtime에는 빈 신규 키 존재를 강제하지 않지만, 비어 있지 않은 신규 값은 거부한다.

## 명시 provision과 배포 순서

409→418 중간 Map 배포는 현재 PinVi와 기존 secret/config를 그대로 쓴다. 새 Manager 배선과 credential은 최종 새 Map·PinVi pair 회전 직전에만 적용한다. 미리 root env를 고치거나 중간 코드가 모르는 설정을 추가하지 않는다.

후속 private provision은 정본 get_env_path의 root 소유 파일을 대상으로 원 파일 hash와 키 부재 또는 동일값을 비교하는 CAS로 원문 한 키만 추가한다. 기존 reader/writer를 우선 사용하고 파일 권한·다른 키·원문 bytes를 보존한다. 값/개별 secret hash를 UI·로그·registry에 남기지 않는다. 이 checkpoint에는 해당 운영 writer를 실행하지 않았다.

source pair나 registry schema에 새 secret 필드를 넣지 않는다. Manager 설치 뒤 실행 metadata는 기존 공식 pin rebind-execution 절차만 사용하며 source pair·image·기존 blocked 기록은 보존한다. 두 API PR과 Manager의 두 소스 리뷰·CI·격리 실제 API/PinVi 검증 뒤 공식 설치·최종 pair 전환을 조율하고, 실제 공개 지도·관리 목록/선택·로그아웃을 재검증한다.

## 검증

원 소스에서 신규 파생/비활성/digest-only·기존 credential 승격 부정15개가 실패했다. 구현 뒤 전용 회귀23개가 통과했다. 첫 구현의 UI override 부정2개는 더 앞선 리터럴 차단에 걸려 기대 메시지만 달랐으며 원문을 보존했다. 새 시험은 리터럴 차단과 다른 변수로의 보간·추가·삭제 차단을 각각 검증한다. 관련 넓은 회귀·정확 CI·두 리뷰는 진행 중이다.

회귀는 원문→digest, 입력 불변, 부재/잘못된 입력, 기존 credential 재사용 거부, API 두 소비 위치, frozen render, masking/repr, config override와 runtime 누출·활성값 부재를 검증한다. 실제 운영 토큰이나 DB fixture는 사용하지 않는다.
