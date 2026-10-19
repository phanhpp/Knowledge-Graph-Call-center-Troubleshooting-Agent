"use client";

/*
 * A cited ID's full text, fetched when someone expands a source: GET /sources/{id}
 * (agent/routes/sources.py, through the proxy route). Replies carry only short excerpts, so saved
 * conversations stay small; the whole article comes from here.
 *
 * Cached per ID for the page's lifetime: the data only changes with a redeploy, and expanding the
 * same source again (or in another reply) shouldn't refetch.
 */

import { useEffect, useState } from "react";

import { resolveAgentUrl } from "@/lib/agent";
import type { Source } from "@/lib/state";

/** A full source: the short one's fields, with the whole text, plus a category or a rule's type. */
export interface FullSource extends Source {
  label: string;
}

export type FullSourceLookup =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "done"; source: FullSource }
  | { status: "missing" }
  | { status: "error" };

const cache = new Map<string, FullSource>();

/** For tests: forget everything fetched so far. */
export function clearFullSourceCache() {
  cache.clear();
}

export function useFullSource(id: string | null): FullSourceLookup {
  const [base] = useState(resolveAgentUrl);
  const [lookup, setLookup] = useState<FullSourceLookup>(() => {
    const cached = id ? cache.get(id) : undefined;
    return cached ? { status: "done", source: cached } : { status: id ? "loading" : "idle" };
  });

  useEffect(() => {
    if (!id) {
      setLookup({ status: "idle" });
      return;
    }
    const cached = cache.get(id);
    if (cached) {
      setLookup({ status: "done", source: cached });
      return;
    }
    let cancelled = false;
    setLookup({ status: "loading" });
    fetch(`${base}/sources/${encodeURIComponent(id)}`, { cache: "no-store" })
      .then(async (res) => {
        if (cancelled) return;
        if (res.status === 404) return setLookup({ status: "missing" });
        if (!res.ok) return setLookup({ status: "error" });
        const source = (await res.json()) as FullSource;
        cache.set(id, source);
        if (!cancelled) setLookup({ status: "done", source });
      })
      .catch(() => {
        if (!cancelled) setLookup({ status: "error" });
      });
    return () => {
      cancelled = true;
    };
  }, [base, id]);

  return lookup;
}
