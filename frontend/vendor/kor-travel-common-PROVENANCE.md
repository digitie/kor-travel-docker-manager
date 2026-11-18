# 공통 산출물 채택 출처

고정 catalog/policy C는 main 병합 P(2b0c02a2018c2c8c791bc6d6c7b5a2290579e09e)의 선행 commit이다. 실제 R/P pack bytes 동일성이 확인됐다. 운영 적용은 별도다.

pack 생성 SOURCE_SHA: 706aaa7821588dfca72f660c9161f56be2d0445f
고정 catalog commit: 5c1b3857e9af060d30ec776157760fb9e9c13ded
정책 commit: 5c1b3857e9af060d30ec776157760fb9e9c13ded
main 발행 근거: https://github.com/digitie/kor-travel-common/actions/runs/44313659808/artifacts/14430019630
producer: 706aaa7821588dfca72f660c9161f56be2d0445f

- @kor-travel/tokens 0.1.3 sha512-m1WsuJw1DHYxAkPsJpAIrFN5P/Udb+Ln/sZHbrmPJpItQDHx+wuf8QEFgSrBsk5yW+NAX5CQnIQZRkPIFwO97A==
- @kor-travel/ui 0.2.2 sha512-/odxLZlFlMj0IB8oPMyjE77kj1MtZO46m/lyHiCTW/gYFq7o6XUSrSPOC7rm7cyEpbjD8dAnCqcz7zmE2GWF+Q==

기존 직접 의존성만 갱신한다. Pinvi 모바일은 tokens-only를 유지한다.
lock은 Node26/npm12 재생성과 정본 guard 대조 후 수용한다.
