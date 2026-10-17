"use client";

import { useCallback, useState } from "react";

import { Chat } from "@/components/Chat";

/** The open conversation's id, from the URL (?thread=<id>), so a reload or a shared link reopens it. */
function threadFromUrl(): string | null {
  if (typeof window === "undefined") return null;
  return new URLSearchParams(window.location.search).get("thread");
}

/** Write the id into the URL without a navigation (no reload, nothing re-rendered by Next). */
function threadToUrl(id: string | null) {
  const url = new URL(window.location.href);
  if (id) url.searchParams.set("thread", id);
  else url.searchParams.delete("thread");
  window.history.replaceState(null, "", url);
}

/**
 * Keeps the open conversation in the URL and rebuilds the chat when it changes.
 * (The rebuild rule is from the hitl-py web app.)
 *
 * Two kinds of change, handled differently:
 *   - the user opens another conversation or starts a new one: rebuild <Chat>, or it keeps
 *     showing the previous thread's messages;
 *   - the server names the thread it just created (null -> id on the first message): do NOT
 *     rebuild, or the run in flight would be thrown away with everything it had streamed.
 */
export function ChatShell() {
  const [threadId, setThreadId] = useState<string | null>(threadFromUrl);
  const [mountKey, setMountKey] = useState(0);

  const remember = useCallback((id: string | null) => {
    setThreadId(id);
    threadToUrl(id);
  }, []);

  const open = useCallback(
    (id: string | null) => {
      remember(id);
      setMountKey((k) => k + 1);
    },
    [remember],
  );

  return <Chat key={mountKey} threadId={threadId} onThreadId={remember} onOpenThread={open} onNewThread={() => open(null)} />;
}
