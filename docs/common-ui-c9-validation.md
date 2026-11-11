# 공통 후보9 최종 소비자 검증

## 결과와 변경 범위

Docker Manager 전담 범위의 구현·최종 브라우저 검증을 마쳤다. Transport는 별도 전담에
인계한 뒤 수정하지 않았다. 원본 dirty checkout은 보존한다.

부모 integration-9-review의 UI0.2.0/tokens0.1.1 tgz·원장·lock을 같은 bytes로
동기화했다. 중앙 정책 workflow는 common `2c1182e765bb2bc53e01e0962cf2a73683891e1d`를
사용한다. Lightning CSS nativecss.fix.4는 그대로다. vendor에는 현재 사용하는 세 tgz만 있다.

Playwright는 지정한 비특권 포트의 별도 Next 서버를 직접 실행하고 HTTP 준비 상태를
확인한다. 기존 서버 재사용은 금지한다. CORS 응답은 실제 프로젝트 baseURL을 따른다.
브라우저는 별도 UI 주소 외 외부 HTTP를 차단하며 운영 API·WebSocket은 mock으로 대체한다.
제품 인증·세션·운영 API 정책과 Next root는 변경하지 않았다.

## 검증 근거

| 항목 | 결과와 범위 |
|---|---|
| C9 로컬·정본 의존성 검사 | 통과. source 검사·ledger/lock/vendor 및 실제 설치 파일 일치 |
| C9 Linux 타입·ESLint | 통과. 경고0 |
| C9 React Doctor0.9.22 | 전체 소스 `--scope full --blocking warning`, 오류0·경고0 |
| 단위 테스트 | C8 기준16파일54개 통과. C9 제품 소스·공통 실행 파일과 동일 |
| clean 설치 | n150의 별도 물리 디렉터리에서 C8 `npm ci` 477개 통과 |
| production build | 같은 n150 C8 소스의 성공 산출물 재사용. C9 신규 빌드로 보고하지 않음 |
| C9 Chromium E2E | n150 데스크톱·모바일2개 통과, 건너뜀0·실패0·재시도 성공0 |
| 임시 프로세스 | 선택한 임시 포트 종료 확인. 운영 UI 포트 접속·운영 프로세스 종료 없음 |

제품 소스53개 해시가 동일하다. UI94개·tokens25개 파일 중 각각
`check-dependencies.mjs`만 C8→C9에서 달라졌으며 나머지117개 실행·CSS·메타데이터 파일은
같다. C9 실제 설치 파일119개도 모두 pack과 일치한다. 따라서 checker 교체만으로
실행 산출물을 다시 만들지 않았다. 로컬 node_modules symlink의 설치를 반복하거나
제품 Next root를 임시 디렉터리로 바꾸지 않았다.

기존 stdout Ready 대기 실패 로그를 보존하고 HTTP 준비 검사로 전환했다.
이후 첫 브라우저 시도는 로그인 뒤 원장 로딩을5초 안에 기다리지 못했다. mock API200과
로딩 화면을 확인했고 제품 코드는 바꾸지 않았다. 테스트90초·단언15초로 설정한
후속 실행은 약51초에 두 화면을 모두 통과했다. 첫 실패 로그·trace도 별도 보존한다.

확인 범위는 공통 LoginForm 로그인·로그아웃, 네 VirtualTable(원장·마운트·백업·고정 버전),
확인 창 승인 전 실행 차단·취소·승인·호출 버튼 초점 복귀, Radix 모달/탭/Escape,
RHF 설정 행 삭제 후 값 보존·입력 초점, 모바일 메뉴 높이, Pretendard 적용과 pageerror0이다.

원문 근거는 저장소 밖 `validation/manager-c9-final-20261112/`와 부모의
`validation/kor-travel-docker-manager-c8-gates.json`,
`validation/kor-travel-docker-manager-c9-refresh.json`,
`validation/common-c8-c9-runtime-equivalence.json`에 보존한다. 민감 운영 컨텍스트는
로컬 전용 파일이며 커밋하지 않는다.

## 남은 통합·운영 gate

Draft PR #588의 최종 SHA에 대해 부모가 두 독립 소비자 리뷰와 원격 CI를 확인한다.
공통 최종 freeze, PR 머지와 부모 go 전에는 운영 배포를 실행하지 않는다.
실제 세션·공개 도메인·실제 Docker 운영 수용은 mock E2E 결과로 대체하지 않는다.
backend 무변경이므로 backend 테스트나 DB·RustFS 작업은 실행하지 않았다.
배포 형태·비밀 보존·스모크·복구 절차는 [배포 계획](common-ui-deployment.md)을 따른다.
