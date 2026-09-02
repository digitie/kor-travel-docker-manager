// 컴포넌트 렌더 테스트 전역 설정. `vitest.config.mts`의 `test.setupFiles`가 이 파일을
// 각 테스트 파일 실행 전에 한 번씩 로드한다.
//
// `@testing-library/jest-dom/vitest`를 import하면 `toBeInTheDocument()` 같은 DOM
// matcher가 Vitest의 `expect`에 전역으로 추가된다(jest-dom 자체 README의 "With Vitest"
// 절이 명시하는 방식 — jest용 `@testing-library/jest-dom` 단독 import와는 별개 경로다).
import '@testing-library/jest-dom/vitest';
