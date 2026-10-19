import { expect, test } from "@playwright/test";

import { E2E_PASSWORD } from "../playwright.config";

/* The password gate, against the real Next server (no mock: proxy.ts and the routes are real). */

test("a fresh visitor is sent to the sign-in page", async ({ page }) => {
  await page.goto("/");
  await expect(page).toHaveURL(/\/login\?next=%2F$/);
  await expect(page.getByRole("button", { name: "Sign in" })).toBeVisible();
});

test("a wrong password is refused", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("Password").fill("not-the-password");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("alert").filter({ hasText: "Wrong password." })).toBeVisible();
  await expect(page).toHaveURL(/\/login/);
});

test("the right password opens the chat", async ({ page }) => {
  await page.goto("/login?next=%2F");
  await page.getByLabel("Password").fill(E2E_PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("button", { name: "+ New conversation" })).toBeVisible();
});

test("the agent API refuses a request without the sign-in cookie", async ({ request }) => {
  const response = await request.get("/api/langgraph/threads/search");
  expect(response.status()).toBe(401);
});

test("the agent API refuses a forged cookie", async ({ request }) => {
  const response = await request.get("/api/langgraph/info", { headers: { cookie: "sa_auth=deadbeef" } });
  expect(response.status()).toBe(401);
});
