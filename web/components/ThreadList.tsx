"use client";

import type { Client } from "@langchain/langgraph-sdk";
import { useEffect, useState } from "react";

interface ThreadSummary {
  id: string;
  title: string;
  customer?: string;
  updatedAt: string;
}

type SavedMessage = { type?: string; content?: unknown; additional_kwargs?: { customer_id?: string } };

/** A thread's first consultant message (and its customer) makes its title. */
function summarize(thread: { thread_id: string; updated_at: string; values?: unknown }): ThreadSummary {
  const messages = ((thread.values as { messages?: SavedMessage[] } | undefined)?.messages ?? []) as SavedMessage[];
  const first = messages.find((m) => m.type === "human");
  const text = typeof first?.content === "string" ? first.content : "New conversation";
  return {
    id: thread.thread_id,
    title: text.length > 48 ? `${text.slice(0, 48)}…` : text,
    customer: first?.additional_kwargs?.customer_id,
    updatedAt: thread.updated_at,
  };
}

/** The saved conversations on the agent server, newest first. Refetched whenever refreshKey changes. */
export function ThreadList({
  client,
  activeId,
  refreshKey,
  onOpen,
  onNew,
}: {
  client: Client;
  activeId: string | null;
  refreshKey: string;
  onOpen: (id: string) => void;
  onNew: () => void;
}) {
  const [threads, setThreads] = useState<ThreadSummary[]>([]);

  useEffect(() => {
    let cancelled = false;
    client.threads
      .search({ limit: 30, sortBy: "updated_at", sortOrder: "desc" })
      .then((found) => {
        if (!cancelled) setThreads(found.map(summarize));
      })
      .catch(() => {}); // the list is a convenience; the chat works without it
    return () => {
      cancelled = true;
    };
  }, [client, refreshKey]);

  return (
    <nav aria-label="Conversations" className="hidden w-64 shrink-0 flex-col border-r border-border bg-surface-secondary md:flex">
      <div className="p-3">
        <button type="button" onClick={onNew} className="w-full rounded-md border border-border bg-surface px-3 py-2 text-sm font-medium hover:border-primary">
          + New conversation
        </button>
      </div>
      <ul className="flex-1 overflow-y-auto px-2 pb-3">
        {threads.map((thread) => (
          <li key={thread.id}>
            <button
              type="button"
              onClick={() => onOpen(thread.id)}
              aria-current={thread.id === activeId ? "true" : undefined}
              className={`w-full rounded-md px-2 py-2 text-left text-sm hover:bg-surface ${thread.id === activeId ? "bg-surface font-medium" : ""}`}
            >
              {thread.customer && <span className="block font-mono text-xs text-text-tertiary">{thread.customer}</span>}
              <span className="block truncate">{thread.title}</span>
            </button>
          </li>
        ))}
      </ul>
    </nav>
  );
}
