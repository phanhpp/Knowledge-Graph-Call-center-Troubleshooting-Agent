"use client";

/*
 * Each reply's additional_kwargs (citations, sources, run_id), read from the thread's saved state.
 *
 * Why not from the stream: with @langchain/react 1.2.4 the hook streams over the newer protocol
 * (/threads/{id}/commands + /threads/{id}/stream/events), and a reply written by a graph node (not
 * token-streamed by a model) arrives there with an EMPTY additional_kwargs. The hook's own copy of the
 * state (stream.values) reuses that same object, so it is empty too. The server's saved thread has
 * them: that's why the chips appeared only after a reload. Verified in the browser.
 *
 * So: read GET /threads/{id}/state once when the thread opens and again each time a turn finishes.
 * One small request per turn; the text itself still streams live.
 */

import type { Client } from "@langchain/langgraph-sdk";
import { useEffect, useState } from "react";

import { savedKwargs } from "@/lib/message";

export function useSavedKwargs(client: Client, threadId: string | null, isLoading: boolean): Map<string, Record<string, unknown>> {
  const [saved, setSaved] = useState<Map<string, Record<string, unknown>>>(() => new Map());

  useEffect(() => {
    if (!threadId || isLoading) return; // mid-turn the state is still changing; read it when the turn ends
    let cancelled = false;
    client.threads
      .getState(threadId)
      .then((state) => {
        if (!cancelled) setSaved(savedKwargs((state.values as { messages?: unknown[] } | undefined)?.messages));
      })
      .catch(() => {}); // without it the replies still show; only chips, sources and 👍 wait for a reload
    return () => {
      cancelled = true;
    };
  }, [client, threadId, isLoading]);

  return saved;
}
