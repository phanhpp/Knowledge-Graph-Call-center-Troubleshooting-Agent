import { describe, expect, it } from "vitest";

import { authToken, isAuthorized, passwordMatches } from "@/lib/auth";

/* The shared-password gate: the cookie proves the holder knew the password, without containing it. */

describe("the sign-in cookie", () => {
  it("lets in the holder of the right password's cookie", async () => {
    expect(await isAuthorized(await authToken("s3cret"), "s3cret")).toBe(true);
  });

  it("refuses a cookie made from another password", async () => {
    expect(await isAuthorized(await authToken("guess"), "s3cret")).toBe(false);
  });

  it("refuses a forged or missing cookie", async () => {
    expect(await isAuthorized("deadbeef", "s3cret")).toBe(false);
    expect(await isAuthorized(undefined, "s3cret")).toBe(false);
  });

  it("doesn't contain the password", async () => {
    const token = await authToken("s3cret");
    expect(token).not.toContain("s3cret");
    expect(token).toMatch(/^[0-9a-f]{64}$/);
  });

  it("lets nobody in when no password is configured", async () => {
    expect(await isAuthorized(await authToken(""), undefined)).toBe(false);
    expect(await isAuthorized(await authToken(""), "")).toBe(false);
  });
});

describe("the typed password", () => {
  it("matches only the configured one", async () => {
    expect(await passwordMatches("s3cret", "s3cret")).toBe(true);
    expect(await passwordMatches("S3CRET", "s3cret")).toBe(false);
    expect(await passwordMatches(undefined, "s3cret")).toBe(false);
    expect(await passwordMatches("anything", undefined)).toBe(false);
  });
});
