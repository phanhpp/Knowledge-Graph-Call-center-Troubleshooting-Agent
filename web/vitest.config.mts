import { fileURLToPath } from "node:url";

import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

/**
 * Unit tests for lib/: no browser, no agent server, no API key. Every test builds its own inputs
 * or stubs `fetch`. (Setup from the hitl-py web app.)
 *
 * The `@/` alias is repeated here: Vitest doesn't read tsconfig's `paths`.
 */
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { "@": fileURLToPath(new URL(".", import.meta.url)) },
  },
  test: {
    // The hooks call useState/useEffect and read window.location, so they need a DOM.
    environment: "jsdom",
    globals: true,
    include: ["lib/**/*.test.ts", "lib/**/*.test.tsx"],
  },
});
