# n150 Manager 정상 업데이트 운영 수용

## 현재 모드와 적용 세대

2026-10-25 사용자가 확정한 n150 모드는 **웹·API·CLI 모두 유지**다. 공식 일반 설치와
별도 공식 frontend 갱신을 사용했다. CLI-only 전환·수동 symlink 전환은 하지 않았다.
[운영 절차](../prod-deployment.md#manager-웹api-이전-후-cli만-남기는-설치)와
[호스트 Compose 범위](../host-compose-scope.md)를 따른다.

적용 소스는 PR #553 머지 fb83b65b91fa226b786bd66558817926959b7197이다.
PR #552의 host-local 후보 검증을 일반 설치에도 적용했다. 후속 PR #551 Map 환경변수
배선은 이번 설치에 포함하지 않았다. 이번 갱신은 승인된 n150 SATA 예외 범위다.

## 실제 수용

| 범위 | 확인 결과 |
| --- | --- |
| Backend·CLI | 고정 root 소유 소스·별도 offline wheelhouse의 공식 일반 설치 성공. Backend 활성·health 정상·정본 CLI targets 검증 통과. 기존 환경·인증 설정과 원본 dirty clone 보존 |
| Frontend | 비밀 환경파일을 보존한 공식 npm ci/build와 서비스 재시작 성공. 적용 소스 58개·정적 산출물 17개·실제 Next.js 16 manifest 4개 확인 |
| 공개 UI | n150 Linux 고정 runtime에서 실제 12개 검사 통과. 로그인 화면·200/cookie·인증 읽기·원장 렌더·WS frame·reload·로그아웃·보호 읽기 401·종료 후 재연결 루프 없음 |
| 공개 빌드 | 인증 없는 HTTPS 읽기에서 HTML의 script/style 12개가 같은 빌드 집합에 속하며 정적 17개, 총 1,400,413바이트가 적용 빌드와 일치 |
| 테스트 세션 | 성공 UI 세션의 운영 감사·세션 정확 일치 1개와 폐기 상태를 확인. 이전 실패 세션 1개는 두 독립 소스 리뷰 후 정확한 행만 CAS 폐기·commit/readback 확인. 다른 세션 fingerprint 보존 |

최종 성공한 UI 실행의 로그인·로그아웃 POST는 각각 한 번이며 me 200·containers 200·로그아웃 뒤 보호 읽기 401이었다.
page 오류·예상 밖 요청/소켓·frame 전달 오류는 0이었다. 소유 browser process/profile 정리와
각 단계 전후 서비스·소스·빌드·환경 identity를 확인했다. 세션 식별·정리는 추가 로그인 없이 수행했다.

## 실패와 복구

- 최초 frontend npm ci/build는 성공했으나 감사 wrapper가 Next.js 16에 없는 옛 manifest를
  요구해 재시작 전에 실패했다. 실제 manifest 계약을 확인한 뒤 같은 소스·산출물에 결박한
  fix-forward로 공식 재시작만 수행했고 npm을 반복하지 않았다.
- NTFS 감사 파일의 POSIX mode 가정·Chromium singleton socket 경로 길이·browser와 다른
  HTTP 요청의 Origin/UA 가정 때문에 초기 감사가 실패했다. Linux 개인 입력 경로·짧은
  소유 profile·실제 page fetch로 각각 고쳤다. 초기 원장은 최종 PASS로 덮어쓰지 않았다.
- 실패 테스트 세션은 전체 로그아웃·세션 삭제·인증 설정 회전 없이 한 행만 정리했다.
  commit 결과 불명 상태는 별도로 기록하고 자동 재시도하지 않는다.
- 이전 release와 원본 작업 트리는 보존했다. 옛 설치기가 현재 off-host 필수값 누락을
  처리한다는 복구 보증은 하지 않으며 검토한 공식 경로의 fix-forward를 사용했다.

## 근거와 한계

원본은 저장소 밖 private 감사 영역에 보존했다. 아래는 수용 원장의 SHA-256이다.
접속 주소·계정·인증값·쿠키·민감 세션 행은 문서/PR에 포함하지 않는다.

| 수용 원장 | SHA-256 |
| --- | --- |
| Backend·CLI 공식 설치 | 55a891dc9d65a1a9d9d9591a59af4997951bcf011e7aa43cf58ed20bcad48ac2 |
| Frontend 공식 갱신 | 626ff83a770fb00f19118701b774dd2d2747d58d5141aec587111a531de6f396 |
| 실제 공개 UI 독립 대조 | 8d58e5d2215e11cdc5a3caa69e9bfb4c715865293f090a4353de0bb9761c2cf9 |
| 공개 HTML·정적 17개 대조 | d637527ac3cd2d25dfda784c5bbc9f9633e870f97bf05ca17d7d4599967ffbbf |
| 이전 실패 세션 한 행 폐기 | 74a44aafaf63c7887bfefcd44e36f643afb4bf3b25efbecbae1ec06f0cbcec53 |

공개 UI·frontend 빌드·로컬 Backend 관측을 각각 확인했으며 프록시 endpoint와 특정 PID 사이의
암호학적 결박으로 확대하지 않는다. 기존 감사의 endpoint binding/deployment acceptance false는 보존한다.
원장 렌더·실시간 수신만으로 모든 Docker 컨테이너 표시·전체 inventory 동등성을 증명하지 않는다.
Manager 이번 갱신과 Map·PinVi 실제 pinned 재구축·배포 수용은 별도다.
이번 문서 PR은 실행 코드·설정·추가 운영 변경이 없으며 사용자 지시에 따라 CI를 생략한다.
