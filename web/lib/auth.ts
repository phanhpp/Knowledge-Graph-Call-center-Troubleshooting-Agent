/**
 * The shared-password gate. One password (WEB_PASSWORD, a server env var) for everyone; per-user
 * sign-in is a later step.
 *
 * Logging in (app/api/login/route.ts) sets an httpOnly cookie holding a hash of the password, and
 * proxy.ts lets a request through only when the cookie matches. The cookie never holds the password
 * itself, and can't be made without knowing it. Changing WEB_PASSWORD logs everyone out.
 *
 * Web Crypto only (no node:crypto), so this runs wherever proxy.ts runs.
 */

export const AUTH_COOKIE = "sa_auth";
export const AUTH_MAX_AGE_SECONDS = 60 * 60 * 24 * 30; // 30 days

/** The cookie value for `password`: hex SHA-256 of a fixed prefix plus the password. */
export async function authToken(password: string): Promise<string> {
  const bytes = new TextEncoder().encode(`super-agent-web:${password}`);
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

/** Compare without stopping at the first difference, so timing doesn't reveal how much matched. */
function sameString(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

/** Whether `cookieValue` proves the holder knows `password`. No password configured: nobody gets in. */
export async function isAuthorized(cookieValue: string | undefined, password: string | undefined): Promise<boolean> {
  if (!password || !cookieValue) return false;
  return sameString(cookieValue, await authToken(password));
}

/** Whether a typed password is the right one. */
export async function passwordMatches(typed: unknown, password: string | undefined): Promise<boolean> {
  if (!password || typeof typed !== "string") return false;
  return sameString(await authToken(typed), await authToken(password));
}
