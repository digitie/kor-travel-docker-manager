# Pinvi 사용자별 요청 상한 설정 전달

Pinvi #653과 PR #680의 영구 사용자별 상한 설정을 Manager 소유 Compose의 pinvi-api에 전달한다. 기본값은 빈 JSON 객체이며 운영 계정 UUID와 실제 값은 저장하지 않는다.

## 범위

- root 환경의 PINVI_RATE_LIMIT_USER_LIMIT_OVERRIDES를 같은 이름으로 pinvi-api에 전달한다.
- 설정이 없거나 비면 {}를 전달하고, JSON 문자열은 그대로 보존한다.
- UUID·양의 정수 검증과 인증·정책별 카운터는 Pinvi가 소유한다.
- 기존 allowed 우회 6건 변경, 호스트 설치, 배포, 재시작과 prod 검증은 수행하지 않는다. 최종 운영 적용·검증은 Claude Code 담당이다.

## 검증 계획

실제 정본 서비스 조각을 Docker Compose config로 렌더해 미설정·빈 값·복수 사용자 JSON을 검증한다. Docker daemon 조작 없이 WSL에서 실행하고 두 독립 적대 리뷰와 최종 CI 뒤 병합한다. Pinvi의 실제 WSL UI 검증은 Pinvi PR에서 기록한다.
