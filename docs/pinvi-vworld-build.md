# PinVi 지도 공개 키의 빌드 연결

운영 배포 전 공개 UI에서 PinVi 지도는 키 미설정 fallback을 표시했다. 설치 Manager의
정본 공개 키는 존재했으나 실제 Web image/runtime에는 해당 키가 없었다.
원래 Manager Compose와 raw/resolved 빌드 검증은 PinVi Web의 API 주소만 전달했다.
PinVi Dockerfile은 이미 같은 이름의 ARG를 build/runtime 양쪽 ENV에서 소비한다.

이번 변경은 pinvi-web build.args의 NEXT_PUBLIC_VWORLD_API_KEY를 정본의 같은 변수에
연결하고, 기존 두 검증의 정확한 source wiring/허용 인자 집합을 같은 선언과 맞춘다.
새 대체 key나 비밀값, 권한, 인증 설정, Weather flag, 포트와 target은 추가하지 않는다.
Dockerfile의 최종 runtime ENV가 같은 build argument를 소비하므로 Compose runtime에
독립된 값을 중복 선언하지 않는다. 키를 비워 두는 기존 비운영 구성도 그대로다.

실제 Compose 렌더에서 빈 값/명시 fixture 값의 전달, 누락·다른 원천 거부와 provenance
검증을 시험했다. 수정 전에는 새5회귀 중4실패·1통과였고, 수정 후 관련11회귀가
통과했다. 실제 키는 시험·보고서·로그에 넣지 않았다. focused Ruff도 통과했다.

Manager 일반 설치·공식 pair rebuild 및 새 image의 실제 key 존재/공개 지도 렌더는 아직
수용하지 않았다. PinVi Feature 목록422는 이 수정과 별도 원인 조사다. 이 패치가 목록
오류까지 해결하거나 현재 운영 지도가 이미 정상이라는 뜻이 아니다.
