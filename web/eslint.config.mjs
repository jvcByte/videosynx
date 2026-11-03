import { dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { FlatCompat } from "@eslint/eslintrc";

// eslint-config-next still ships in the eslintrc format, so it goes through
// FlatCompat rather than being spread directly.
const compat = new FlatCompat({
  baseDirectory: dirname(fileURLToPath(import.meta.url)),
});

const config = [
  {
    // Every .next* directory here is generated output -- several builds each
    // write their own types, and linting them reports on code nobody wrote.
    ignores: [
      ".next/**",
      ".next-e2e/**",
      ".next-auth/**",
      ".next-probe/**",
      "node_modules/**",
      "playwright-report/**",
      "test-results/**",
      "next-env.d.ts",
    ],
  },
  ...compat.extends("next/core-web-vitals", "next/typescript"),
];

export default config;
