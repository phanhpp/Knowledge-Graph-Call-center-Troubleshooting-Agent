"use client";

import { citationsOf, sourcesOf } from "@/lib/message";

const KIND_LABEL = { action: "Action", rule: "Rule", article: "Article" } as const;

/**
 * What the selected reply is based on: each cited ID with its text (additional_kwargs.sources, written
 * by the agent from data/*.json). Clicking a citation chip selects its reply and highlights the ID here.
 */
export function SourcesPanel({
  message,
  highlight,
}: {
  message: { id?: string; additional_kwargs?: Record<string, unknown> } | null;
  highlight: string | null;
}) {
  const citations = citationsOf(message);
  const sources = sourcesOf(message);

  return (
    <section aria-label="Sources panel" className="rounded-xl border border-border bg-surface p-3">
      <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-text-tertiary">Sources</h2>

      {!message && <p className="text-sm text-text-secondary">The sources of a reply appear here.</p>}

      {message && citations.length === 0 && (
        <p className="text-sm text-text-secondary">This reply cites nothing: an explanation builds on earlier answers.</p>
      )}

      {/* Replies saved before sources existed carry only their IDs. */}
      {message && citations.length > 0 && sources.length === 0 && (
        <p className="font-mono text-sm">{citations.join(", ")}</p>
      )}

      <ul className="space-y-2">
        {sources.map((source) => (
          <li
            key={source.id}
            data-testid={`source-${source.id}`}
            className={`rounded-lg border p-2 text-sm ${source.id === highlight ? "border-primary bg-primary-light" : "border-border"}`}
          >
            <div className="mb-0.5 flex items-center gap-2 text-xs text-text-tertiary">
              <span className="rounded bg-surface-tertiary px-1">{KIND_LABEL[source.kind]}</span>
              <span className="font-mono">{source.id}</span>
            </div>
            <p className="font-medium">{source.title}</p>
            {source.text && <p className="mt-0.5 text-text-secondary">{source.text}</p>}
          </li>
        ))}
      </ul>
    </section>
  );
}
