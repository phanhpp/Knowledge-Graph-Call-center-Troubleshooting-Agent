import { expect, test, type Locator, type Page } from "@playwright/test";

import { FULL_SOURCES, KB_THREAD, mockAgent, WALK_THREAD } from "./mock-agent";
import { signIn } from "./sign-in";

/* The resizable side panel and expanding a source to its full text. Fake agent (mock-agent.ts). */

test.beforeEach(async ({ page }) => signIn(page));

async function drag(page: Page, handle: Locator, dx: number, dy: number) {
  const box = (await handle.boundingBox())!;
  const x = box.x + box.width / 2;
  const y = box.y + box.height / 2;
  await page.mouse.move(x, y);
  await page.mouse.down();
  await page.mouse.move(x + dx, y + dy, { steps: 8 });
  await page.mouse.up();
}

const widthOf = async (locator: Locator) => (await locator.boundingBox())!.width;
const heightOf = async (locator: Locator) => (await locator.boundingBox())!.height;

test("dragging the side panel's edge widens it, and the width survives a reload", async ({ page }) => {
  await mockAgent(page);
  await page.goto(`/?thread=${WALK_THREAD}`);
  const panel = page.getByRole("complementary");
  const before = await widthOf(panel);

  await drag(page, page.getByRole("separator", { name: "Resize the side panel" }), -200, 0);
  await expect.poll(() => widthOf(panel)).toBeGreaterThan(before + 190);
  const after = await widthOf(panel);

  await page.reload();
  await expect.poll(() => widthOf(panel)).toBeCloseTo(after, 0);
});

test("the arrow keys resize too", async ({ page }) => {
  await mockAgent(page);
  await page.goto(`/?thread=${WALK_THREAD}`);
  const panel = page.getByRole("complementary");
  const before = await widthOf(panel);

  await page.getByRole("separator", { name: "Resize the side panel" }).focus();
  await page.keyboard.press("ArrowLeft");
  await expect.poll(() => widthOf(panel)).toBeCloseTo(before + 24, 0);
});

test("dragging between the customer card and the sources resizes the card", async ({ page }) => {
  await mockAgent(page);
  await page.goto(`/?thread=${WALK_THREAD}`);
  const card = page.getByTestId("customer-card-area");
  const before = await heightOf(card);

  await drag(page, page.getByRole("separator", { name: "Resize the customer card" }), 0, -120);
  await expect.poll(() => heightOf(card)).toBeLessThan(before - 110);
});

test("a source expands to its full text and collapses again", async ({ page }) => {
  await mockAgent(page);
  await page.goto(`/?thread=${KB_THREAD}`);
  const article = FULL_SOURCES.get("KB-006")!;
  const ending = article.text.slice(-60); // well past the excerpt saved on the reply
  const card = page.getByTestId("source-KB-006");

  await expect(card).not.toContainText(ending);
  await card.getByRole("button", { name: /SIM Replacement Process and Fees/ }).click();
  await expect(page.getByTestId("full-KB-006")).toContainText(ending);
  await expect(page.getByTestId("full-KB-006")).toContainText(article.label);

  await card.getByRole("button", { name: "Show less" }).click();
  await expect(card).not.toContainText(ending);
});

test("clicking a citation chip expands its source", async ({ page }) => {
  await mockAgent(page);
  await page.goto(`/?thread=${WALK_THREAD}`);
  const rule = FULL_SOURCES.get("R012")!;

  await page.getByTestId("ai-message").first().getByRole("button", { name: "R012" }).click();
  const card = page.getByTestId("source-R012");
  await expect(card.getByRole("button", { expanded: true })).toBeVisible();
  await expect(page.getByTestId("full-R012")).toContainText(rule.label);
  await expect(card).toHaveClass(/border-primary/);
});
