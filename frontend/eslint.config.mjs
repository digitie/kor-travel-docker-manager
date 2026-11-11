import { defineConfig, globalIgnores } from 'eslint/config';
import nextPlugin from '@next/eslint-plugin-next';
import tseslint from 'typescript-eslint';
import reactHooks from 'eslint-plugin-react-hooks';
import reactX from 'eslint-plugin-react-x';
import reactDom from 'eslint-plugin-react-dom';
import jsxA11y from 'eslint-plugin-jsx-a11y-x';

export default defineConfig([
  { files: ['**/*.{ts,tsx}'], languageOptions: { parser: tseslint.parser } },
  nextPlugin.configs.recommended,
  nextPlugin.configs['core-web-vitals'],
  reactX.configs.recommended,
  reactDom.configs.recommended,
  reactHooks.configs.flat.recommended,
  { plugins: { 'jsx-a11y-x': jsxA11y }, rules: {
    'jsx-a11y-x/alt-text': 'error',
    'jsx-a11y-x/aria-props': 'error',
    'jsx-a11y-x/aria-proptypes': 'error',
    'jsx-a11y-x/aria-unsupported-elements': 'error',
    'jsx-a11y-x/role-has-required-aria-props': 'error',
    'jsx-a11y-x/role-supports-aria-props': 'error',
    // 기존 프로젝트는 React Compiler를 사용하지 않는다. 기존 Hooks 검증은 유지한다.
    'react-hooks/preserve-manual-memoization': 'off',
    'react-hooks/purity': 'off',
    'react-hooks/refs': 'off',
    'react-hooks/set-state-in-effect': 'off',
    // 같은 Compiler 규칙을 react-x가 중복 실행하지 않도록 기존 정책에 맞춘다.
    'react-x/set-state-in-effect': 'off',
  } },
  globalIgnores(['.next/**', 'out/**', 'build/**', 'next-env.d.ts']),
]);
