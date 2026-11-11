# 공통 UI 변경 배포 계획

## 현재 상태

이 문서는 배포 계획이며 운영 배포 결과가 아니다. 공통 패키지는 통합 검증 후보다.
필수 테스트·독립 적대 리뷰2건·CI·PR 머지와 부모의 go 후에만 순차 배포한다.
실제 접속·계정·도메인·환경변수 값은 로컬 런북 정본에서 읽고 출력하거나 커밋하지 않는다.

## 설치 형태와 변경 범위

정본 `docs/prod-deployment.md` §3·§4와 `scripts/install-ktdm-trusted-release`를 확인했다.
Manager 자체 UI는 Docker 이미지가 아니라 systemd `ktdm-frontend`의 Next.js 산출물이다.
API는 `ktdm-backend`로 운영하며, 이 소비자 변경에는 backend 코드 변경이 없다.
Manager UI 전용 Dockerfile·이미지 태그는 현재 저장소에 없다. 임의로 Docker 배포 경로를 만들지 않는다.
2026-11-12 실제 frontend 유닛이 loaded/active이며 trusted-release 링크가 존재함을 읽기 전용으로 확인했다.
실행 계정·경로·환경 파일의 실제 값은 저장소 밖 권한0600 로컬 기록에만 보존했다.
부모가 머지 SHA와 당시 운영 컨텍스트를 다시 확인한 뒤 기존 UI만 재빌드·재시작한다.
DB·RustFS·다른 target의 시작/정지/재구축·마이그레이션은 실행하지 않는다.

## 승인 후 실행 순서

1. 머지된 정확한40자리 SHA, 운영 frontend 유닛의 실행 계정·경로·npm 경로,
   현재 소스 revision·이전 빌드 복구 경로를 읽기 전용으로 확인한다.
2. 운영 `.env`·`frontend/.env.production`·로컬 override·설정 파일은 보존한다.
   추적 파일만 exact SHA로 갱신하고 운영 비밀을 로컬 테스트 환경으로 복사하지 않는다.
3. 운영 frontend 디렉터리에서 `npm ci`, `npm run check:dependencies`,
   `npm run type-check`, `npm run lint -- --max-warnings=0`, `npm run doctor`, `npm run test`를 확인한다.
   공통 vendor tgz·원장·lock의 SHA512가 승인한 후보와 같아야 한다.
4. 운영 `NEXT_PUBLIC_BACKEND_URL`을 출력하지 않고 유지한 채 `npm run build`한다.
   해당 값은 번들에 들어가므로 WSL 검증용 번들을 운영에 그대로 복사하지 않는다.
5. 빌드 성공 뒤 `sudo systemctl restart ktdm-frontend`만 실행한다.
   UI 변경만으로 trusted backend installer나 Compose mutation을 실행하지 않는다.
   backend 설치가 필요해지면 별도 범위를 확인하고 §3의 exact SHA installer 경로를 따른다.
6. API `/health`, UI의 정상 HTTP 응답, 공개 도메인의 로그인→서비스 원장→
   중지 항목 토글·상세 탭·빠른 명령→로그아웃→로그인 화면 전환을 확인한다.
   브라우저 콘솔 오류·인증 재연결 루프·로그아웃 뒤 보호 읽기 재개가 없어야 한다.
   검증을 위해 실제 컨테이너 중지나 설정 변경을 실행하지 않는다.
7. 실패하면 원인을 기록하고 운영 비밀을 보존한 기존 frontend 복구 경로로 이전 SHA를
   재빌드·재시작한다. DB·RustFS 또는 자동 이미지 rollback을 사용하지 않는다.

## 현재 검증과 운영 수용의 구분

최종 n150의 격리 Playwright는 mock API를 사용해 공통 UI·키보드·반응형·승인 전 실행 차단을 확인한다.
이 결과를 n150 공개 도메인·실제 세션·실제 Docker 운영 수용으로 보고하지 않는다.
운영 수용은 부모의 go와 실제 배포 후 위 절차에서 따로 기록한다.
