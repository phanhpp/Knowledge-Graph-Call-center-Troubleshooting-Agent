/*
 * Records REAL agent traffic once, so the e2e fixtures are not guesses. (Pattern from an earlier human-in-the-loop project of mine.)
 *
 * Run it by hand, with both servers up (`uv run langgraph dev` in the repo root, `pnpm dev` here):
 *
 *     node e2e/record-fixtures.mjs
 *
 * It signs in, runs a troubleshooting turn for CUST-1001 and a "why?" follow-up, then a
 * knowledge-base question in a new conversation, and writes every /api/langgraph exchange to
 * e2e/fixtures/flow.json. The specs replay those, so CI needs no agent, no key and no model call.
 *
 * NOT part of the suite: it makes real Jev, OpenAI and Claude calls. Re-run it when the reply
 * shape or the server protocol changes (stale fixtures pass while the real app breaks).
 */

import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { chromium } from "@playwright/test";

const HERE = dirname(fileURLToPath(import.meta.url));
const OUT = resolve(HERE, "fixtures");
const BASE = process.env.E2E_BASE_URL ?? "http://localhost:4200";
const PASSWORD = process.env.WEB_PASSWORD ?? "dev";

/** One captured exchange. `path` keeps only the path and query, so the host never matters. */
const captured = [];

const browser = await chromium.launch();
const page = await browser.newPage();

page.on("response", async (response) => {
  const url = new URL(response.url());
  if (!url.pathname.startsWith("/api/langgraph")) return;
  const request = response.request();
  let body = "";
  try {
    body = await response.text();
  } catch {
    body = ""; // a stream still open when the page closed: keep the shape, drop the body
  }
  captured.push({
    method: request.method(),
    path: url.pathname + url.search,
    requestBody: request.postData() ?? null,
    status: response.status(),
    contentType: response.headers()["content-type"] ?? "application/json",
    body,
  });
  process.stdout.write(`  ${request.method()} ${url.pathname} -> ${response.status()}\n`);
});

console.log("signing in…");
await page.goto(`${BASE}/login`);
await page.getByLabel("Password").fill(PASSWORD);
await page.getByRole("button", { name: "Sign in" }).click();
await page.getByRole("button", { name: "+ New conversation" }).waitFor();

console.log("troubleshooting turn for CUST-1001…");
// Scoped to the conversation area: the sidebar's saved conversations have the same text.
const conversation = page.getByRole("region", { name: "Conversation" });
await conversation.getByRole("button", { name: /customer cannot activate roaming/ }).click();
await page.getByRole("button", { name: "R012" }).waitFor({ timeout: 90_000 });

console.log('follow-up "why?"…');
await page.getByLabel("Message").fill("why did it end up at Tier 2?");
await page.keyboard.press("Enter");
await page.getByTestId("ai-message").nth(1).waitFor({ timeout: 90_000 });
await page.waitForTimeout(3000); // let the post-turn state read land

console.log("knowledge-base question in a new conversation…");
await page.getByRole("button", { name: "+ New conversation" }).click();
await conversation.getByRole("button", { name: "What is the SIM replacement fee?" }).click();
await page.getByRole("button", { name: "KB-006" }).waitFor({ timeout: 90_000 });
await page.waitForTimeout(3000);

mkdirSync(OUT, { recursive: true });
writeFileSync(resolve(OUT, "flow.json"), JSON.stringify(captured, null, 2));
console.log(`\nwrote ${captured.length} exchanges to fixtures/flow.json`);

await browser.close();
