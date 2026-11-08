import coreWebVitals from "eslint-config-next/core-web-vitals";

const config = [
  ...coreWebVitals,
  {
    // React Compiler 도입은 별도 작업이며 기존 Hooks 규칙은 유지한다.
    rules: {
      "react-hooks/preserve-manual-memoization": "off",
      "react-hooks/purity": "off",
      "react-hooks/refs": "off",
      "react-hooks/set-state-in-effect": "off",
    },
  },
];

export default config;
