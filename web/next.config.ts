import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Development only: the dev server calls itself localhost, and blocks its own resources (hot
  // reload) for other hosts. Playwright and the browser checks open 127.0.0.1.
  allowedDevOrigins: ["127.0.0.1"],
  // The agent is reached through app/api/langgraph/[...path]/route.ts, not a rewrite: the
  // route handler adds the LangSmith key server-side so it never reaches the browser.
};

export default nextConfig;
