# 공통 산출물 채택 출처

고정 catalog/policy C는 kor-travel-common main 병합 PR #84(309b881f8fd6d657b448f43b6fc61939fde742c2)이다. pack은 PR #83 병합(05d7f91a4151a69d72b8bf1dc0007291c6ab2a61)의 main docs run artifact에서 받았고, SHA256SUMS와 SRI가 catalog와 같음을 확인했다. 운영 적용은 별도다.

pack 생성 SOURCE_SHA: 05d7f91a4151a69d72b8bf1dc0007291c6ab2a61
고정 catalog commit: 309b881f8fd6d657b448f43b6fc61939fde742c2
정책 commit: 309b881f8fd6d657b448f43b6fc61939fde742c2
main 발행 근거: kor-travel-common Actions artifact 14803131043 (common-release-packs-05d7f91…, run 45141922011)
candidate: touch-focus-ui-0.2.6-main-05d7f91a4151

- @kor-travel/tokens 0.1.7 sha512-B3wQbS8Jn61znGfMjNM4xzVl8fx/8ud0ZIaOwJGHJNBBV+lBpt0QfrOTD+k+KSlFDjp+moyvTCLnuIzvE3Pqqg==
- @kor-travel/ui 0.2.6 sha512-29bcozPJl8nS+IP5uL1rjs1KjMQQpBo9X80bYtcGnIh5pfQtNrWqgqum/K33vx04w2eP4Zi4uQszCS8FeLHSKg==

기존 직접 의존성만 갱신한다(toast 0.1.6은 같은 release에 있지만 Manager가 쓰지 않는다).
ui 0.2.6·tokens 0.1.7의 의존 그래프는 0.2.5·0.1.6과 같아(tgz package.json 차이는 version과 ui peer tokens뿐) lock은 두 항목의 version·resolved·integrity와 peer만 바뀐다.
