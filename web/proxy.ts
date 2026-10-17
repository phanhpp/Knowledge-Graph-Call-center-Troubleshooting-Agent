import { NextResponse, type NextRequest } from "next/server";

import { AUTH_COOKIE, isAuthorized } from "@/lib/auth";

/**
 * The password gate (Next 16's name for middleware): runs before every page and API request.
 *
 *   signed in (cookie matches WEB_PASSWORD)  -> continue
 *   not signed in, a page                    -> redirect to /login?next=<page>
 *   not signed in, an /api/ call             -> 401 (a redirect would hand fetch() an HTML page)
 *
 * /login and /api/login stay open (see the matcher), or nobody could sign in. Next's docs call
 * Proxy an "optimistic" check, so the agent proxy route checks the cookie again before it adds
 * the API key: this gate keeps strangers off the page, that check keeps them off the agent.
 */
export async function proxy(request: NextRequest) {
  if (await isAuthorized(request.cookies.get(AUTH_COOKIE)?.value, process.env.WEB_PASSWORD)) {
    return NextResponse.next();
  }
  if (request.nextUrl.pathname.startsWith("/api/")) {
    return NextResponse.json({ error: "Not signed in." }, { status: 401 });
  }
  const login = new URL("/login", request.url);
  login.searchParams.set("next", request.nextUrl.pathname + request.nextUrl.search);
  return NextResponse.redirect(login);
}

export const config = {
  // Everything except the sign-in page and its API, Next's own paths (assets, and in development
  // the hot-reload connection, which the page needs before it can hydrate), and the favicon.
  matcher: ["/((?!login|api/login|_next/|favicon.ico).*)"],
};
