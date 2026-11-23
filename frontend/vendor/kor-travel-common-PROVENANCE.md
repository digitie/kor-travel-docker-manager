# 공통 산출물 채택 출처

고정 catalog/policy C는 kor-travel-common main 병합 PR #82(ade7b3eb50c0197caad4a1c98fcb97cffbc2a8a4)이다. pack은 그 main docs run artifact에서 받았고(PR #81 병합 fe141143cc8ba75e8fd0b6134e1c9e518e294fa2의 내용 포함), SHA256SUMS와 SRI가 catalog와 같음을 확인했다. 운영 적용은 별도다.

pack 생성 SOURCE_SHA: ade7b3eb50c0197caad4a1c98fcb97cffbc2a8a4
고정 catalog commit: ade7b3eb50c0197caad4a1c98fcb97cffbc2a8a4
정책 commit: ade7b3eb50c0197caad4a1c98fcb97cffbc2a8a4
main 발행 근거: kor-travel-common Actions artifact 14784066261 (common-release-packs-ade7b3e…, run 45097026288)
candidate: field-focus-ui-0.2.5-main-fe141143cc8b

- @kor-travel/tokens 0.1.6 sha512-y9uEIo9dTG/XlohG+kQs9goL10tsEVgHLwUA81XMskts1oZJaim+7UBsG773l/Q1W54MUOFWF1BLsmlFNjGrAA==
- @kor-travel/ui 0.2.5 sha512-GKICXj4/I4pZQHvbrLy4qL7Bbtn757soe7g8M9phZrNXOsSXtU5bFhharNAWpjD6/qXxSyVWtfTxrZqsgAk2hA==

기존 직접 의존성만 갱신한다(toast 0.1.5는 같은 release에 있지만 Manager가 쓰지 않는다).
ui 0.2.5·tokens 0.1.6의 의존 그래프는 0.2.3·0.1.4와 같아 lock은 두 항목의 version·resolved·integrity와 peer만 바뀐다.
