"use client";

import { citationsOf, sourcesOf } from "@/lib/message";
import type { Source } from "@/lib/state";
import { useFullSource } from "@/lib/use-full-source";

const KIND_LABEL = { action: "Action", rule: "Rule", article: "Article" } as const;

/**
 * What the selected reply is based on: each cited ID with its text (additional_kwargs.sources, written
 * by the agent from data/*.json). A source expands in place to its full text, fetched on demand
 * (lib/use-full-source.ts): replies only carry an article's opening. Clicking a citation chip
 * highlights its source and expands it.
 */
export function SourcesPanel({
  message,
  highlight,
  expandedId,
  onToggle,
}: {
  message: { id?: string; additional_kwargs?: Record<string, unknown> } | null;
  highlight: string | null;
  expandedId: string | null;
  onToggle: (id: string) => void;
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
      {message && citations.length > 0 && sources.length === 0 && <p className="font-mono text-sm">{citations.join(", ")}</p>}

      <ul className="space-y-2">
        {sources.map((source) => (
          <SourceCard
            key={source.id}
            source={source}
            highlighted={source.id === highlight}
            expanded={source.id === expandedId}
            onToggle={() => onToggle(source.id)}
          />
        ))}
      </ul>
    </section>
  );
}

/** One source. Its own component so the full-text fetch (a hook) runs per source. */
function SourceCard({ source, highlighted, expanded, onToggle }: { source: Source; highlighted: boolean; expanded: boolean; onToggle: () => void }) {
  return (
    <li data-testid={`source-${source.id}`} className={`rounded-lg border p-2 text-sm ${highlighted ? "border-primary bg-primary-light" : "border-border"}`}>
      <button type="button" aria-expanded={expanded} onClick={onToggle} className="w-full text-left" title={expanded ? "Show less" : "Show the full text"}>
        <span className="mb-0.5 flex items-center gap-2 text-xs text-text-tertiary">
          <span className="rounded bg-surface-tertiary px-1">{KIND_LABEL[source.kind]}</span>
          <span className="font-mono">{source.id}</span>
          <span className="ml-auto" aria-hidden>
            {expanded ? "▾" : "▸"}
          </span>
        </span>
        <span className="block font-medium">{source.title}</span>
      </button>

      {expanded ? <FullText source={source} /> : source.text && <p className="mt-0.5 text-text-secondary">{source.text}</p>}

      {expanded && (
        <button type="button" onClick={onToggle} className="mt-1 text-xs text-text-tertiary hover:text-text">
          Show less
        </button>
      )}
    </li>
  );
}

/** The full text: the saved excerpt while it loads, and if it can't be fetched. */
function FullText({ source }: { source: Source }) {
  const lookup = useFullSource(source.id);

  if (lookup.status !== "done") {
    return (
      <div className="mt-0.5 space-y-1">
        {source.text && <p className="text-text-secondary">{source.text}</p>}
        <p className="text-xs text-text-tertiary">{lookup.status === "loading" ? "Loading the full text…" : "Couldn't load the full text."}</p>
      </div>
    );
  }

  const full = lookup.source;
  return (
    <div className="mt-0.5 space-y-1" data-testid={`full-${source.id}`}>
      {full.label && <p className="text-xs uppercase tracking-wide text-text-tertiary">{full.label}</p>}
      {full.text && <p className="whitespace-pre-wrap text-text-secondary">{full.text}</p>}
    </div>
  );
}
