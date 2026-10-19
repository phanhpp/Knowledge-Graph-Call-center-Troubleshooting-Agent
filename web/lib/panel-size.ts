"use client";

/*
 * Panel sizes the user drags (the side panel's width, the customer card's height), remembered in this
 * browser. A per-viewer convenience, so localStorage: nothing about it belongs on the server. Every
 * storage access is guarded (private mode or blocked storage throws), and a bad stored value falls
 * back to the default.
 */

import { useCallback, useEffect, useState } from "react";

/** `value` kept within [min, max]. */
export function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max);
}

/** The size stored under `key`, clamped; `fallback` if there is none, it isn't a number, or storage is blocked. */
export function readStoredSize(key: string, fallback: number, min: number, max: number): number {
  try {
    const raw = window.localStorage.getItem(key);
    const value = raw === null ? Number.NaN : Number(raw);
    return Number.isFinite(value) ? clamp(value, min, max) : fallback;
  } catch {
    return fallback;
  }
}

/**
 * A size in pixels that survives a reload: [size, setSize (clamps and stores), reset].
 *
 * Starts at `initial` and reads the stored value after mounting, so the server-rendered HTML and the
 * first client render agree (reading storage during render would be a hydration mismatch).
 */
export function usePanelSize(key: string, initial: number, min: number, max: number) {
  const [size, setSize] = useState(initial);

  useEffect(() => {
    setSize(readStoredSize(key, initial, min, max));
  }, [key, initial, min, max]);

  const update = useCallback(
    (next: number) => {
      const value = Math.round(clamp(next, min, max));
      setSize(value);
      try {
        window.localStorage.setItem(key, String(value));
      } catch {
        // the size still applies for this page view
      }
    },
    [key, min, max],
  );

  const reset = useCallback(() => update(initial), [update, initial]);

  return [size, update, reset] as const;
}
