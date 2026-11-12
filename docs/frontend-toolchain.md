# 프론트엔드 도구·검증 계약

Manager frontend는 private package anchor `frontend/package.json`을 사용한다. 저장소 `.nvmrc`와 anchor `engines.node`는 Node22.23.1, `packageManager`와 `engines.npm`은 npm12.0.1이다. `frontend/.npmrc`의 engine-strict를 유지한다. 공개 라이브러리 manifest·C11 vendor·원장은 변경하지 않는다.

개발 shell 또는 격리된 빌드 runtime에서 저장소 selector의 Node를 선택하고 다음을 실행한다.

~~~bash
cd frontend
npm install --global "$(node -p 'require("./package.json").packageManager')"
npm ci --engine-strict
npm run check:dependencies
npm run type-check
npm run lint
npm run doctor
npm test
npm run build
npm run test:e2e
~~~

이 명령 목록은 현재 검사 계약이다. CI의 step 상대순서를 바꾸지 않았다. 설치는 peer/lifecycle 우회 옵션을 추가하지 않는다. lint는 로컬/CI 모두 warning0, Doctor는 full scope/blocking warning이다. 실행 Node/npm이 다르면 `scripts/verify-frontend-toolchain.sh`는 `--fix`라도 node_modules 삭제 전에 실패한다. 이 스크립트의 정상 판정이 실제 app gate·중앙 canonical 검사를 대신하지 않는다.

운영 호스트의 global Node/npm은 이 변경으로 갱신하지 않는다. 위 global 설치 명령은 선택된 개발·CI/격리 runtime 안의 npm을 위한 것이다. 운영 UI를 새로 배포할 때는 별도 준비된 private pinned Node22.23.1/npm12.0.1 runtime으로 새 소스·의존성·빌드 및 공식 UI 기동 명령을 모두 맞춰야 한다. 현재 서비스 runtime을 이미 맞췄다고 주장하지 않는다. 설치·빌드·Service UI 적용은 부모의 슬롯/단계 GO 뒤에만 수행한다.

## C11 현재 수용과 과거 후보 표기

C11 최초 릴리스 UI0.2.0/tokens0.1.1 및 patched engine bytes는 불변이다. 소비 원장의 integration-11-review/CANDIDATE 표기는 당시 동결 입력의 역사이며 새 후보 pack을 뜻하지 않는다. 최종 공통 정본과 6개 앱의 실제 수용은 [Common 최종 병합 영수증](https://github.com/digitie/kor-travel-common/blob/678a5bcf0150ac0386bfa86106e7e0f4f77e8639/docs/reviews/adversarial/evidence/2026-11-12-frontend-deployment-receipt.json), [Common PR50](https://github.com/digitie/kor-travel-common/pull/50)에 연결한다. 이 문서에서 원장·pack을 다시 쓰거나 재발행하지 않는다.

당시 Manager frontend 수용은 9910b9 계열 소스의 실제 설치/새 UI와 로그인·공통 표·Pretendard·모바일·로그아웃 증거다. 후속 SDK508 설치는 별도 단계이며 frontend 새 도구 변경의 운영 수용으로 소급하지 않는다. d289619 source의 원격 기본 unit54·브라우저2·Doctor0/type/lint/build와 backend3979PASS/40SKIP/1warning은 최초 PR CI에서 확인했다. 최종 guard 연결 source의 정확 CI와 새 운영 적용은 별도 gate다. native device/EAS/store 및 제외된 Map 후속 작업을 완료로 표시하지 않는다.

Common PR51의 실제 병합 36e4d285ea926b9d8c20da4221ade86ebf74c04d를 중앙 workflow의 uses/common-ref에 함께 고정한다. frontend가 유일한 private anchor라 추가 manifest 입력은 없고, 이 UI는 systemd 기반이므로 production Node Dockerfile 입력도 없다. 공개 C11 7packs/버전은 바꾸지 않는다. 중앙 정책 데이터는 최신 main을 별도 비교하며, 최종 source의 원2 검토·정확 CI·전용 runtime 운영 적용은 구분한다.

## 입력 부품과 회귀

세 모듈은 C11의 Input/NativeSelect/Textarea/Field를 사용한다. NativeSelect는 실제 select DOM과 option 값, 빈 값 및 onChange 이벤트를 보존한다. Input은 React19의 ref prop을 전달하므로 RHF 등록과 명령창 focus ref를 그대로 사용한다. runtime pin의 textarea는 rows 기반 native sizing을 유지한다. form API를 새로 발명하지 않으며 비밀번호 safeParse·RHF·config 도메인 검증·submit payload/권한은 기존 로직 그대로다.

필수 검증은 기존 unit 전체와 관련 입력 회귀, 기존 실제 브라우저 시나리오의 값·focus·행 DOM identity·readonly/disabled·선택값·명령 검색이다. 운영 API에 비밀번호/키/설정/pin을 실제로 쓰는 검증을 추가하지 않는다. 미실행 gate를 기존 C11 54개 결과로 대신하지 않는다. CI 순서/needs/fail-fast 최적화는 여전히 마지막 단계이며 이번 변경에서 다루지 않는다.
