import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { clearFullSourceCache, useFullSource } from "@/lib/use-full-source";

/* A source's full text, fetched on expand and cached for the page's lifetime. */

const KB006 = { id: "KB-006", kind: "article", title: "SIM Replacement Process and Fees", text: "A standard SIM replacement fee applies…", label: "sim" };

function stubFetch(status = 200, body: unknown = KB006) {
  const fetchMock = vi.fn().mockResolvedValue({ ok: status === 200, status, json: async () => body });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

beforeEach(() => clearFullSourceCache());
afterEach(() => vi.unstubAllGlobals());

describe("useFullSource", () => {
  it("fetches the source through the proxy route", async () => {
    const fetchMock = stubFetch();
    const { result } = renderHook(() => useFullSource("KB-006"));

    await waitFor(() => expect(result.current).toEqual({ status: "done", source: KB006 }));
    expect(fetchMock.mock.calls[0][0]).toMatch(/\/api\/langgraph\/sources\/KB-006$/);
  });

  it("fetches each source once per page", async () => {
    const fetchMock = stubFetch();
    const first = renderHook(() => useFullSource("KB-006"));
    await waitFor(() => expect(first.result.current.status).toBe("done"));

    const second = renderHook(() => useFullSource("KB-006"));
    expect(second.result.current.status).toBe("done"); // straight from the cache, no loading flash
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("reports an unknown source and a failed fetch apart", async () => {
    stubFetch(404, { error: "no source" });
    const missing = renderHook(() => useFullSource("KB-999"));
    await waitFor(() => expect(missing.result.current.status).toBe("missing"));

    stubFetch(502, { error: "agent down" });
    const failed = renderHook(() => useFullSource("R012"));
    await waitFor(() => expect(failed.result.current.status).toBe("error"));
  });

  it("asks for nothing without an id", () => {
    const fetchMock = stubFetch();
    const { result } = renderHook(() => useFullSource(null));
    expect(result.current.status).toBe("idle");
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
