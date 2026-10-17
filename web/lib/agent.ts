/**
 * Browser-side agent config. (From the hitl-py web app.)
 *
 * window is only read inside a function, so this module is safe to import anywhere;
 * only client components may call resolveAgentUrl().
 */

/** Same-origin path served by app/api/langgraph/[...path]/route.ts. */
export const AGENT_API_PATH = "/api/langgraph";

/**
 * Where the browser sends agent traffic. Call this from a client component only (inside a
 * useState initializer or useMemo), never at module scope.
 *
 * `?agentServer=<url>` bypasses the proxy and talks to an agent server directly — useful for
 * pointing a local page at a local `langgraph dev`. It sends no API key, so it only works
 * against a server that needs none.
 */
export function resolveAgentUrl(): string {
  // A "use client" component is still server-rendered before it hydrates, so this runs once
  // with no window. The value is unused then (useStream connects from an effect); it only has
  // to not throw.
  if (typeof window === "undefined") return AGENT_API_PATH;

  const override = new URLSearchParams(window.location.search).get("agentServer");
  if (override && override !== "local") return override;
  return `${window.location.origin}${AGENT_API_PATH}`;
}

/** The graph's name: the key under "graphs" in the repo's langgraph.json. useStream's assistantId. */
export const ASSISTANT_ID = "super_agent";
