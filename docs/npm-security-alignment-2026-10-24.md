# 2026-10-24 npm 보안 갱신과 버전 정합

소유자 요청 범위는 PinVi·Transport·Map·Geo·Weather·Manager의 npm 직접/전이 의존성 갱신과 CI 확인 후 머지다. Python 의존성과 운영 배포는 이번 변경에 포함하지 않는다.

| 공통 항목 | 버전 |
| --- | --- |
| Next.js / 사용하는 Next ESLint 패키지 | 16.4.3 |
| React / React DOM | 19.3.1 |
| TypeScript | 5.9.3 |
| Vitest / Vite | 5.0.5 / 8.3.5 |
| Playwright | 1.64.1 |
| Zod 직접 의존성 | 4.6.7 |
| CI Node / npm | 22.23.1 / 12.0.1 |

호환성에 필요한 차이는 유지한다. `eslint-config-next`는 React ESLint 플러그인의 peer 범위에 맞춰 ESLint 9.39.5를 사용하고, 별도 플러그인 조합을 검증하는 Map은 ESLint 10.13.0을 유지한다. TypeScript 6/7 대신 타입 생성기와 린터가 함께 지원하는 5.9.3을 쓴다. TanStack Table은 현재 사용 API의 8.21.3, react-hook-form은 안정 7.91.0, resolvers는 peer 충돌 없는 5.9.2다. 공개 라이브러리의 peer 범위는 앱의 정확한 핀과 별도로 보존한다.

PinVi 모바일은 Expo SDK 57.0.31과 React Native 0.86.3을 기준으로 SDK 모듈 패치를 정합화했다. NativeWind 4의 Tailwind 3.4.19와 웹의 Tailwind 4.3.3은 서로 다른 컴파일러 계약이다. 웹과 모바일에 같은 Tailwind major를 강제하지 않는다. Expo CLI의 Zod와 URI 디코더, Tailwind 하위 선택자 파서는 별도 보안 패치를 소비한다. PinVi의 React 타입은 SDK 허용 범위의 `@types/react` 19.2.18·`@types/react-dom` 19.2.7을 사용한다. 웹의 React 실행 버전은 19.3.1을 공유한다.

Geo는 다른 소비 앱과 동일한 Common UI dev.6 아티팩트를 사용하며 출처와 SHA256은 `kor-travel-geo-ui/vendor/PROVENANCE.md`에 남긴다. 다른 로컬·Git·vendored 제공자 패키지는 registry 최신 버전으로 치환하지 않는다.

Map은 보안 패치된 `@redocly/openapi-core` 1.34.22가 자체 호환 minimatch를 사용하므로 이전 강제 override와 vendor 보정 스크립트를 제거했다. 실제 OpenAPI 산출물 비교와 npm 트리 검증은 유지한다. Next 16.4의 optimizer 모듈 이동에 맞춰 Sharp ABI 검증의 import만 변경했으며 이미지 변환은 계속 실행한다. Playwright Docker 이미지는 테스트 패키지와 같은 1.64.1과 검증된 manifest digest를 사용한다.

Map의 React Doctor는 기존 CI 계약인 0.9.1로 정확히 고정한다. 0.9.18은 새 진단 44건(복잡도·중복·ref·hydration·생성 파일)을 추가한다. 이번 보안 패치를 위해 소스 규칙을 무력화하거나 넓은 UI 리팩터링을 섞지 않으며, 생성된 MapLibre 배포 파일만 검사에서 제외한다. 새 Doctor 규칙 도입은 별도 작업이다.

## 남은 상류 취약점

PinVi의 Expo 개발 CLI 경로에는 `node-forge`의 [GHSA-86w9-cpqp-85rv](https://github.com/advisories/GHSA-86w9-cpqp-85rv)가 남는다. 2026-10-24 조회 시 최신 1.4.0도 영향 범위이며 수정 릴리스가 없다. Expo의 코드 서명 인증서 처리에 사용된다. 이 항목을 숨기는 audit 제외나 강제 SDK 하향은 하지 않는다. 앱 웹 요청 처리 경로와 CLI 서명 작업을 구분하며, 이 변경을 전체 취약점 0건으로 보고하지 않는다. 수정 릴리스가 나오면 SDK 호환성을 확인해 다시 갱신한다.

## 검증 범위와 결과 정본

- WSL Node 22.22.2·npm 12.0.1: 이 저장소의 프론트엔드/공용 패키지 기존 테스트 47개, 사용하는 타입 검사·린트·빌드를 통과했다. 기존 경고를 오류 0건과 혼동하지 않는다.
- 여섯 저장소 프론트엔드 고유 테스트는 합계 1,657개다. Transport 두 앱의 Alpine Docker 신규 설치·355개 테스트·빌드, Map Docker deps의 신규 설치·트리·실제 이미지 변환도 통과했다.
- 전이 의존성까지 포함한 전체 audit는 갱신 전 78개 항목에서 PinVi의 상류 미수정 원인 6개 항목으로 줄었다. 다른 6개 설치 루트는 0건이다. 로컬 vendor 파일의 SHA512도 모든 잠금 파일과 일치한다.
- 이 기록을 정리하기 전 기능 검증 후보는 `3bd2a6f817559c390a26b88510cdf4da6e824998`다. 최종 문서 정산 뒤의 HEAD와 CI terminal 결과는 [PR 체크](https://github.com/digitie/kor-travel-docker-manager/pull/549/checks)가 정본이다. 실행되지 않은 검사를 통과로 세지 않는다.
- N150 브라우저 수용 범위는 고정된 후보 소스의 신규 설치·빌드 및 로그인 화면 390/1440px 렌더링과 브라우저 오류 검사다. Manager의 미인증 세션 응답은 합성 응답을 사용한다. 상류 API 인증·운영 데이터·모바일 네이티브 바이너리 수용과 구분한다. 실제 실행 SHA와 결과는 [PR](https://github.com/digitie/kor-travel-docker-manager/pull/549)에 정산한다.
- James/Popper 두 독립 사전 리뷰의 지적을 반영했다. 최종 CI 이후의 독립 확인과 브라우저 결과 확인을 머지 전 게이트로 유지한다. 운영 배포는 별도다.
