import path from "node:path";
import { defineConfig } from "vitest/config";

export default defineConfig({
  // tsconfig sets jsx: "preserve" for Next, which leaves raw JSX in the output.
  // Vite 8 transforms with Oxc, so the JSX runtime is configured here for the
  // test run only. Next's own build is unaffected.
  oxc: {
    jsx: { runtime: "automatic", importSource: "react" },
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname),
    },
  },
  test: {
    environment: "node",
    include: ["tests/**/*.test.ts", "tests/**/*.test.tsx"],
  },
});
