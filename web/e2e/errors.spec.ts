import { expect, test } from "@playwright/test";

import { mockAgent } from "./mock-agent";
import { signIn } from "./sign-in";

/* When the agent can't be reached, the page says so instead of going blank. Fake agent. */

test.beforeEach(async ({ page }) => signIn(page));

test("a 502 from the agent proxy shows an error", async ({ page }) => {
  await mockAgent(page, { commandStatus: 502 });
  await page.goto("/");

  await page.getByLabel("Message").fill("What is the SIM replacement fee?");
  await page.getByRole("button", { name: "Send" }).click();

  await expect(page.getByRole("alert").filter({ hasText: "Couldn't reach the agent" })).toBeVisible();
  await expect(page.getByRole("button", { name: "+ New conversation" })).toBeVisible();
});

test("an unknown customer in the field shows on the card", async ({ page }) => {
  await mockAgent(page);
  await page.goto("/");

  await page.getByLabel("Customer", { exact: true }).fill("CUST-9999");
  await expect(page.getByRole("region", { name: "Customer card" })).toContainText("No customer CUST-9999 in the CRM.");
});
