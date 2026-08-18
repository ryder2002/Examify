import path from "node:path";
import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    // Playwright suites are run by `npm run e2e`, not Vitest. Keeping the
    // directories separate prevents @playwright/test from being evaluated as
    // a unit-test module (and keeps the default test command deterministic).
    exclude: ["**/node_modules/**", "**/dist/**", "**/e2e/**", "**/*.spec.ts"],
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname),
    },
  },
});
