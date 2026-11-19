# 공통 산출물 채택 출처

고정 catalog/policy C는 kor-travel-common main 병합 PR #71(d644632ea9eaf2f6b6baa49305d65fae5bbc0bd4)이다. pack은 PR #70 병합 P(0e2a3f5aa16323df0d009360802661c9bc496dc6)의 main docs run artifact에서 받았고, SRI가 catalog와 같음을 확인했다. 운영 적용은 별도다.

pack 생성 SOURCE_SHA: 0e2a3f5aa16323df0d009360802661c9bc496dc6
고정 catalog commit: d644632ea9eaf2f6b6baa49305d65fae5bbc0bd4
정책 commit: d644632ea9eaf2f6b6baa49305d65fae5bbc0bd4
main 발행 근거: kor-travel-common Actions artifact 14500111373 (common-release-packs-0e2a3f5…)
producer: 0e2a3f5aa16323df0d009360802661c9bc496dc6

- @kor-travel/tokens 0.1.4 sha512-k6StI02tH/iRxKimczN0btU+MS9AvDZLlR/6Bn6xQHhZ5Mb79pkyIALd4RkoLT2GP3qlwR89YEgOaBiBxACDpA==
- @kor-travel/ui 0.2.3 sha512-upv+JtiVcF+zg3C/KcZy9UQckx0n3vc0n4ANr3/elWM8PQ4srljxgTQmaBK4IxulEj8pd8gc7mnG3M67oFIQUQ==

기존 직접 의존성만 갱신한다(toast는 Manager가 쓰지 않는다).
lock은 Node26/npm12 재생성과 정본 guard 대조 후 수용한다.
