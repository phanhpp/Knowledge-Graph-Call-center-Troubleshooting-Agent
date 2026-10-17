import { expect, test } from "@playwright/test";

import { mockAgent, WALK_REPLY, WALK_THREAD } from "./mock-agent";
import { signIn } from "./sign-in";

/* 👍👎 on a reply: what is posted, and what comes back after a reload. Fake agent (mock-agent.ts). */

test.beforeEach(async ({ page }) => signIn(page));

const runId = WALK_REPLY.additional_kwargs.run_id as string;

test("👍 posts the reply's run id and score 1", async ({ page }) => {
  const agent = await mockAgent(page);
  await page.goto(`/?thread=${WALK_THREAD}`);

  const reply = page.getByTestId("ai-message").first();
  await reply.hover();
  await reply.getByRole("button", { name: "Good answer" }).click();

  await expect.poll(() => agent.votes.length).toBe(1);
  expect(agent.votes[0]).toMatchObject({ run_id: runId, score: 1, message_id: WALK_REPLY.id });
  await expect(reply.getByRole("button", { name: "Good answer" })).toBeDisabled();
});

test("a vote recorded earlier shows after a reload", async ({ page }) => {
  const agent = await mockAgent(page, { recordedVotes: { [runId]: { score: 1 } } });
  await page.goto(`/?thread=${WALK_THREAD}`);

  const thumbsUp = page.getByTestId("ai-message").first().getByRole("button", { name: "Good answer" });
  await expect(thumbsUp).toBeDisabled();
  await expect(thumbsUp).toHaveAttribute("title", "Thanks — feedback recorded");
  expect(agent.votes).toHaveLength(0);
});

test("a vote that fails says so and offers a retry", async ({ page }) => {
  await mockAgent(page, { feedbackStatus: 502 });
  await page.goto(`/?thread=${WALK_THREAD}`);

  const reply = page.getByTestId("ai-message").first();
  await reply.hover();
  await reply.getByRole("button", { name: "Good answer" }).click();
  await expect(reply.getByRole("button", { name: /Couldn.t send — retry/ })).toBeVisible();
});
