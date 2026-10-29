import { describe, expect, it } from "vitest";

import { safeNext } from "@/lib/safe-next";

/* After signing in: back to the page you came from, never to another site. */

const ORIGIN = "https://super-agent.example";

describe("where signing in sends you", () => {
  it("goes back to a page on this site", () => {
    expect(safeNext("/threads/abc?customer=CUST-1001", ORIGIN)).toBe("/threads/abc?customer=CUST-1001");
  });

  it("goes home when there is nowhere to go back to", () => {
    expect(safeNext(null, ORIGIN)).toBe("/");
    expect(safeNext("", ORIGIN)).toBe("/");
  });

  it.each([
    ["another site", "https://evil.example/login"],
    ["a protocol-relative URL", "//evil.example"],
    ["a backslash, which browsers read as a slash", "/\\evil.example"],
    ["a tab, which browsers drop", "/\t/evil.example"],
    ["a script URL", "javascript:alert(1)"],
  ])("never leaves the site: %s", (_, next) => {
    expect(safeNext(next, ORIGIN)).toBe("/");
  });
});
