import type { Page } from "@playwright/test";

import { E2E_PASSWORD } from "../playwright.config";

/** Sign in through the real login API; the cookie lands in the page's browser context. */
export async function signIn(page: Page) {
  const response = await page.request.post("/api/login", { data: { password: E2E_PASSWORD } });
  if (!response.ok()) throw new Error(`sign-in failed: ${response.status()}`);
}
