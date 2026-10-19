import { NextResponse } from "next/server";

import { AUTH_COOKIE, AUTH_MAX_AGE_SECONDS, authToken, passwordMatches } from "@/lib/auth";

/**
 * POST { password } -> sets the sign-in cookie if it is WEB_PASSWORD (lib/auth.ts).
 * DELETE            -> signs out (clears the cookie).
 */
export async function POST(request: Request) {
  const password = process.env.WEB_PASSWORD;
  if (!password) {
    return NextResponse.json({ error: "WEB_PASSWORD is not set on the server." }, { status: 500 });
  }

  const body = (await request.json().catch(() => null)) as { password?: unknown } | null;
  if (!(await passwordMatches(body?.password, password))) {
    return NextResponse.json({ error: "Wrong password." }, { status: 401 });
  }

  const response = NextResponse.json({ ok: true });
  response.cookies.set(AUTH_COOKIE, await authToken(password), {
    httpOnly: true, // page scripts can't read it
    secure: process.env.NODE_ENV === "production", // https only once deployed
    sameSite: "lax",
    path: "/",
    maxAge: AUTH_MAX_AGE_SECONDS,
  });
  return response;
}

export async function DELETE() {
  const response = NextResponse.json({ ok: true });
  response.cookies.delete(AUTH_COOKIE);
  return response;
}
