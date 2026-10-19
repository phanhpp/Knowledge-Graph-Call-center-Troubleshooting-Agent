import type { NextRequest } from "next/server";

import { AUTH_COOKIE, isAuthorized } from "@/lib/auth";

// Proxy between the browser and the LangGraph agent server (copied from an earlier human-in-the-loop project of mine).
// A next.config rewrite cannot add the X-Api-Key a hosted agent needs, which is why
// this is a route handler.

// ============================================================================
// SERVER CONFIGURATION (TOP OF FILE)
// ============================================================================

// Uses full Node.js environment (required for streaming fetch & duplex support).
export const runtime = "nodejs";

// Disables static caching so every AI call is live and unique.
export const dynamic = "force-dynamic";

// Allows long-running AI agent turns to execute for up to 5 minutes before timing out.
export const maxDuration = 300;

// ============================================================================
// HEADER ALLOWLISTS (SANITIZATION)
// ============================================================================

// Headers copied over from the browser. Unlisted headers (like 'host' and 'cookie')
// are dropped so browser metadata doesn't confuse the Python server or leak sessions.
const FORWARDED_REQUEST_HEADERS = ["content-type", "accept", "last-event-id"];

// Response headers returned to the browser to ensure event streams (SSE) are typed correctly.
const FORWARDED_RESPONSE_HEADERS = ["content-type", "content-disposition", "etag"];

// ============================================================================
// THE 5 EXECUTION STEPS INSIDE PROXY()
// ============================================================================

async function proxy(req: NextRequest, ctx: RouteContext<"/api/langgraph/[...path]">) {
  // --------------------------------------------------------------------------
  // STEP 0: Signed in?
  // --------------------------------------------------------------------------
  // proxy.ts already turns away anyone without the sign-in cookie, but Next calls that an
  // optimistic check. This is the one that matters: nothing reaches the agent, with our
  // API key attached, unless the caller knows the password.
  if (!(await isAuthorized(req.cookies.get(AUTH_COOKIE)?.value, process.env.WEB_PASSWORD))) {
    return Response.json({ error: "Not signed in." }, { status: 401 });
  }

  // --------------------------------------------------------------------------
  // STEP 1: Validate the Target Server URL
  // --------------------------------------------------------------------------
  // Reads AGENT_SERVER_URL from .env. If missing, stops immediately and returns
  // a clear 500 error instead of failing opaquely later.
  const base = process.env.AGENT_SERVER_URL;
  if (!base) {
    return Response.json(
      { error: "AGENT_SERVER_URL is not set. Copy .env.local.example to .env.local." },
      { status: 500 },
    );
  }

  // --------------------------------------------------------------------------
  // STEP 2: Rebuild the Destination URL
  // --------------------------------------------------------------------------
  // Extracts [...path] segments (e.g., ["threads", "abc", "state"]), joins them
  // with slashes, and appends query strings. This strips the '/api/langgraph' prefix:
  // /api/langgraph/threads/abc/state?limit=10 -> http://127.0.0.1:2024/threads/abc/state?limit=10
  const { path } = await ctx.params;
  const search = req.nextUrl.search;
  const upstreamUrl = `${base.replace(/\/$/, "")}/${(path ?? []).join("/")}${search}`;

  // --------------------------------------------------------------------------
  // STEP 3: Sanitize Headers & Inject Security Credentials
  // --------------------------------------------------------------------------
  const headers = new Headers();
  for (const name of FORWARDED_REQUEST_HEADERS) {
    const value = req.headers.get(name);
    if (value) headers.set(name, value);
  }

  // Inject secret API key on the server side (never exposed to client browser).
  // Kept inside an 'if' check so local `langgraph dev` works without needing a key.
  const apiKey = process.env.LANGSMITH_API_KEY;
  if (apiKey) headers.set("X-Api-Key", apiKey);

  // Check if the incoming request carries a payload body.
  const hasBody = req.method !== "GET" && req.method !== "HEAD";

  // --------------------------------------------------------------------------
  // STEP 4: Execute the Upstream Call
  // --------------------------------------------------------------------------
  // Makes the request to the upstream Python server with key safeguards.
  let upstream: Response;
  try {
    upstream = await fetch(upstreamUrl, {
      method: req.method,
      headers,
      body: hasBody ? req.body : undefined,
      // duplex: "half" allows Node.js to stream live request payload chunks to the Python server
      ...(hasBody ? { duplex: "half" } : {}),
      redirect: "manual", // Don't silently follow redirects
      cache: "no-store", // Ensure we always fetch live data from the agent
      // Aborts the backend Python task if the user closes their tab/cancels
      signal: req.signal,
    } as RequestInit);
  } catch (error) {
    // Returns a clear 502 error if the target Python agent server is offline
    return Response.json(
      {
        error: "Could not reach the agent server.",
        upstream: base,
        detail: error instanceof Error ? error.message : String(error),
      },
      { status: 502 },
    );
  }

  // --------------------------------------------------------------------------
  // STEP 5: Pipe the Response Stream Back
  // --------------------------------------------------------------------------
  const responseHeaders = new Headers();
  for (const name of FORWARDED_RESPONSE_HEADERS) {
    const value = upstream.headers.get(name);
    if (value) responseHeaders.set(name, value);
  }

  // Anti-buffering directives:
  // - x-accel-buffering: "no" tells Nginx reverse proxies NOT to batch data
  // - cache-control: "no-transform" stops CDNs from altering or delaying the stream
  responseHeaders.set("cache-control", "no-cache, no-transform");
  responseHeaders.set("x-accel-buffering", "no");

  // Returns upstream.body directly without awaiting it. Text tokens stream
  // live to the UI as they are produced instead of arriving all at once.
  return new Response(upstream.body, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers: responseHeaders,
  });
}

// ============================================================================
// METHOD EXPORTS (BOTTOM OF FILE)
// ============================================================================

// Next.js App Router requires exporting functions named after specific HTTP verbs.
// Pointing all six to proxy() ensures full compatibility with the LangGraph SDK.
export const GET = proxy;
export const POST = proxy;
export const PUT = proxy;
export const PATCH = proxy;
export const DELETE = proxy;
export const OPTIONS = proxy;
