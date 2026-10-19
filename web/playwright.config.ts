import { defineConfig, devices } from "@playwright/test";

/**
 * End-to-end tests against a FAKE agent. (Setup from an earlier human-in-the-loop project of mine.)
 *
 * Every agent call is intercepted and answered from fixtures recorded off the real server
 * (e2e/record-fixtures.mjs), so these need no agent, no API key and no model call, and give the
 * same answer every run. They do start the real Next.js server: the password gate, the pages and
 * what the browser renders and sends are all real.
 */
export const E2E_PASSWORD = "e2e-password";

export default defineConfig({
  testDir: "./e2e",
  testMatch: /.*\.spec\.ts/, // record-fixtures.mjs is a manual tool, not a test
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? "github" : "list",
  use: { baseURL: "http://127.0.0.1:4300", trace: "on-first-retry" },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  /*
   * A production build (`next start`) on its own port, so it never clashes with `pnpm dev` on 4200,
   * and tests what ships. The build is a separate step (the test:e2e script); chaining it here would
   * leave `next start` as a grandchild Playwright can't stop. Next's binary is run directly under
   * node for the same reason: a package-manager wrapper would survive teardown.
   */
  webServer: {
    command: "node node_modules/next/dist/bin/next start --port 4300",
    url: "http://127.0.0.1:4300/login",
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
    // The agent proxy route needs a URL to start; the browser-side mock means it's never reached.
    env: { AGENT_SERVER_URL: "http://127.0.0.1:9/unused", WEB_PASSWORD: E2E_PASSWORD },
  },
});
