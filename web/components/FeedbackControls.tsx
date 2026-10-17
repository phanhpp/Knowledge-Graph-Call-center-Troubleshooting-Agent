"use client";

import { useState } from "react";

import { ThumbsDownIcon, ThumbsUpIcon } from "./icons";
import type { Vote } from "@/lib/use-feedback";

/**
 * Thumbs up/down on one answer.
 *
 * Rendered only where the agent recorded a run for the message, so it cannot appear
 * mid-stream and the caller never has to work out which message ended the turn — see
 * lib/use-feedback.ts.
 *
 * Thumbs up sends immediately. Thumbs down opens a note box first, because "why" is the
 * part worth reading later. The note is optional, and every way out of the box still
 * sends the vote: Send with empty text, Escape, and clicking the thumb again all record
 * the score on its own. A click that records nothing would be the worst outcome here.
 */
export function FeedbackControls({
  vote,
  onVote,
}: {
  vote?: Vote;
  onVote: (score: 0 | 1, comment?: string) => void;
}) {
  const [noteOpen, setNoteOpen] = useState(false);
  const [note, setNote] = useState("");

  const settled = vote?.status === "pending" || vote?.status === "sent";

  const base =
    "inline-flex items-center gap-1 rounded-md px-1.5 py-1 text-xs text-text-tertiary hover:text-text hover:bg-surface-secondary disabled:hover:bg-transparent disabled:cursor-not-allowed transition-colors";

  function send(comment?: string) {
    setNoteOpen(false);
    setNote("");
    onVote(0, comment);
  }

  return (
    <div className="space-y-1.5">
      {/* Hidden until hover, like ForkControls — but a recorded vote stays visible, so
          you can see what you said without hunting for it. */}
      <div
        className={`flex items-center gap-1 transition-opacity ${
          vote ? "opacity-100" : "opacity-0 group-hover:opacity-100 focus-within:opacity-100"
        }`}
      >
        <button
          type="button"
          className={base}
          disabled={settled}
          aria-label="Good answer"
          title={settled ? "Thanks — feedback recorded" : "Good answer"}
          onClick={() => onVote(1)}
        >
          <ThumbsUpIcon className="size-3" filled={vote?.score === 1} />
        </button>

        <button
          type="button"
          className={base}
          disabled={settled}
          aria-label="Bad answer"
          title={settled ? "Thanks — feedback recorded" : "Bad answer"}
          onClick={() => (noteOpen ? send() : setNoteOpen(true))}
        >
          <ThumbsDownIcon className="size-3" filled={vote?.score === 0} />
        </button>

        {vote?.status === "failed" && (
          <button
            type="button"
            className="text-xs text-error hover:underline"
            onClick={() => onVote(vote.score)}
          >
            Couldn&apos;t send — retry
          </button>
        )}
      </div>

      {noteOpen && !settled && (
        <form
          className="flex items-center gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            send(note.trim() || undefined);
          }}
        >
          <input
            autoFocus
            value={note}
            onChange={(event) => setNote(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Escape") send();
            }}
            placeholder="What went wrong? (optional)"
            className="min-w-0 flex-1 rounded-md border border-border bg-surface px-2 py-1 text-xs text-text placeholder:text-text-tertiary focus:outline-none focus:ring-1 focus:ring-border"
          />
          <button
            type="submit"
            className="rounded-md px-2 py-1 text-xs text-text-tertiary hover:text-text hover:bg-surface-secondary transition-colors"
          >
            Send
          </button>
        </form>
      )}
    </div>
  );
}
