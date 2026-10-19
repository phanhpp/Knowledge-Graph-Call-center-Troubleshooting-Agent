import { expect, test } from "@playwright/test";

import { KB_THREAD, mockAgent, WALK_THREAD } from "./mock-agent";
import { signIn } from "./sign-in";

/* What the page renders from a saved conversation, and what it sends. Fake agent (mock-agent.ts). */

test.beforeEach(async ({ page }) => signIn(page));

test("a troubleshooting reply shows its citations, sources and the customer", async ({ page }) => {
  await mockAgent(page);
  await page.goto(`/?thread=${WALK_THREAD}`);

  const replies = page.getByTestId("ai-message");
  await expect(replies.first()).toContainText("Escalate to Tier 2");
  await expect(replies.first().getByRole("button", { name: "ACT-015" })).toBeVisible();
  await expect(replies.first().getByRole("button", { name: "R012" })).toBeVisible();

  const sources = page.getByRole("region", { name: "Sources panel" });
  await expect(sources.getByTestId("source-R012")).toContainText("Every Tier 2 escalation must include a written summary");

  const card = page.getByRole("region", { name: "Customer card" });
  await expect(card).toContainText("CUST-1001");
  await expect(card).toContainText("Jane Doe");
});

test("an explanation cites nothing", async ({ page }) => {
  await mockAgent(page);
  await page.goto(`/?thread=${WALK_THREAD}`);

  const explanation = page.getByTestId("ai-message").nth(1);
  await expect(explanation).toBeVisible();
  await expect(explanation.getByRole("button", { name: /^(ACT|R|KB)-?\d+/ })).toHaveCount(0);
});

test("clicking a citation highlights its source", async ({ page }) => {
  await mockAgent(page);
  await page.goto(`/?thread=${WALK_THREAD}`);

  await page.getByTestId("ai-message").first().getByRole("button", { name: "R012" }).click();
  await expect(page.getByTestId("source-R012")).toHaveClass(/border-primary/);
  await expect(page.getByTestId("source-ACT-015")).not.toHaveClass(/border-primary/);
});

test("a knowledge-base answer lists its articles' titles", async ({ page }) => {
  await mockAgent(page);
  await page.goto(`/?thread=${KB_THREAD}`);

  await expect(page.getByTestId("ai-message").first().getByRole("button", { name: "KB-006" })).toBeVisible();
  await expect(page.getByTestId("source-KB-006")).toContainText("SIM Replacement Process and Fees");
  await expect(page.getByRole("region", { name: "Customer card" })).toContainText("No customer yet");
});

test("sending names the customer in the message, cleaned up", async ({ page }) => {
  const agent = await mockAgent(page);
  await page.goto("/");

  await page.getByLabel("Customer", { exact: true }).fill(" cust-1001 ");
  await page.getByLabel("Message").fill("customer cannot activate roaming");
  await page.getByRole("button", { name: "Send" }).click();

  await expect.poll(() => agent.commands.length).toBeGreaterThan(0);
  const [message] = agent.commands[0].params.input.messages;
  expect(message).toMatchObject({ type: "human", content: "customer cannot activate roaming", additional_kwargs: { customer_id: "CUST-1001" } });
  expect(agent.commands[0].params.assistant_id).toBe("super_agent");
});

test("a general question names no customer", async ({ page }) => {
  const agent = await mockAgent(page);
  await page.goto("/");

  await page.getByLabel("Message").fill("What is the SIM replacement fee?");
  await page.getByRole("button", { name: "Send" }).click();

  await expect.poll(() => agent.commands.length).toBeGreaterThan(0);
  expect(agent.commands[0].params.input.messages[0].additional_kwargs).toEqual({});
});

test("a malformed customer ID blocks sending", async ({ page }) => {
  const agent = await mockAgent(page);
  await page.goto("/");

  await page.getByLabel("Customer", { exact: true }).fill("CUST-1");
  await page.getByLabel("Message").fill("customer cannot activate roaming");
  await expect(page.getByText("A customer ID looks like CUST-1001.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Send" })).toBeDisabled();
  expect(agent.commands).toHaveLength(0);
});

test("the saved conversations are listed and open on click", async ({ page }) => {
  await mockAgent(page);
  await page.goto("/");

  const list = page.getByRole("navigation", { name: "Conversations" });
  await list.getByRole("button", { name: /What is the SIM replacement fee/ }).click();
  await expect(page).toHaveURL(new RegExp(`thread=${KB_THREAD}`));
  await expect(page.getByTestId("ai-message").first().getByRole("button", { name: "KB-006" })).toBeVisible();
});
