/**
 * Where to go after signing in: `next` from the login URL (proxy.ts sets it to the page you came
 * from), but only if it is a page on this site. Anything else goes to "/".
 *
 * Checking that it starts with "/" isn't enough: browsers read "/\evil.example" as
 * "//evil.example" (another site) and drop tabs from URLs. So `next` is resolved the way the
 * browser would resolve it, and kept only if it stays on this origin.
 */
export function safeNext(next: string | null, origin: string): string {
  if (!next) return "/";
  try {
    const url = new URL(next, origin);
    return url.origin === origin ? url.pathname + url.search + url.hash : "/";
  } catch {
    return "/";
  }
}
