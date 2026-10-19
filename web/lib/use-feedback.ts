"use client";

/*
 * Thumbs up/down on a reply, recorded against the LangSmith run that produced it.
 * (From an earlier human-in-the-loop project of mine; there the run ids sat in a separate state list, here each reply
 * carries its own.)
 *
 * The browser can't work out run ids. The agent writes each reply's run id onto the reply itself
 * as the turn runs (additional_kwargs.run_id, agent/utils.py reply()), so they are saved with the
 * conversation and survive a reload.
 *
 * So this hook does three small things: look up the run for a message, read back votes already
 * recorded, and POST a new vote (agent/routes/feedback.py, through the proxy route).
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import { resolveAgentUrl } from "@/lib/agent";
import { runIdOf } from "@/lib/message";

/* A vote is live state, not graph state: it is in flight, landed, or it failed. */
export type VoteStatus = "pending" | "sent" | "failed";

export interface Vote {
  score: 0 | 1;
  status: VoteStatus;
}

export interface Feedback {
  /** The run that produced this message, or undefined if it can't be rated. */
  runFor: (messageId: string | undefined) => string | undefined;
  /** The vote on a message, for rendering its buttons. */
  voteFor: (messageId: string | undefined) => Vote | undefined;
  vote: (messageId: string, score: 0 | 1, comment?: string) => void;
}

/* Live writes and reads: never something a cache should answer. */
const NO_STORE: RequestInit = { cache: "no-store" };

type Rated = { id?: string; additional_kwargs?: Record<string, unknown> };

export function useFeedback(messages: readonly Rated[], threadId: string | null): Feedback {
  const [base] = useState(resolveAgentUrl);
  const [votes, setVotes] = useState<Record<string, Vote>>({});

  /* message id -> run id. Keyed on the ids so a re-render with the same messages (every streamed
     token) doesn't rebuild it and re-fire the votes fetch. */
  const key = messages.map((m) => `${m.id}:${runIdOf(m) ?? ""}`).join("|");
  const runs = useMemo(() => {
    const byMessage = new Map<string, string>();
    for (const message of messages) {
      const runId = runIdOf(message);
      if (message.id && runId) byMessage.set(message.id, runId);
    }
    return byMessage;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  /*
   * Votes already in LangSmith. Without this a reload shows the buttons unvoted, and clicking
   * again writes a second row on the same run.
   *
   * Only fills messages with no local vote: ingestion is backgrounded, so a fetch right after
   * voting can legitimately not see it yet, and overwriting would flip a "sent" button back to empty.
   */
  useEffect(() => {
    const ids = [...runs.values()];
    if (!threadId || ids.length === 0) return;
    let cancelled = false;

    void fetch(`${base}/feedback/${encodeURIComponent(threadId)}/votes?run_ids=${ids.join(",")}`, NO_STORE)
      .then((res) => (res.ok ? res.json() : null))
      .then((data: { votes?: Record<string, { score: 0 | 1 }> } | null) => {
        if (cancelled || !data?.votes) return;
        setVotes((current) => {
          const next = { ...current };
          for (const [messageId, runId] of runs) {
            const recorded = data.votes?.[runId];
            if (recorded && !next[messageId]) next[messageId] = { score: recorded.score, status: "sent" };
          }
          return next;
        });
      })
      .catch(() => {});

    return () => {
      cancelled = true;
    };
  }, [base, threadId, runs]);

  const runFor = useCallback((messageId: string | undefined) => (messageId ? runs.get(messageId) : undefined), [runs]);

  const voteFor = useCallback((messageId: string | undefined) => (messageId ? votes[messageId] : undefined), [votes]);

  const vote = useCallback(
    (messageId: string, score: 0 | 1, comment?: string) => {
      const runId = runs.get(messageId);
      if (!runId || !threadId) return;

      setVotes((current) => ({ ...current, [messageId]: { score, status: "pending" } }));

      void fetch(`${base}/feedback/${encodeURIComponent(threadId)}`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ message_id: messageId, run_id: runId, score, comment }),
        ...NO_STORE,
      })
        .then((res) => {
          /* res.ok matters: a rejected vote still resolves the promise, and without this a 400
             would read as recorded. */
          setVotes((current) => ({ ...current, [messageId]: { score, status: res.ok ? "sent" : "failed" } }));
        })
        .catch(() => {
          /* A vote that silently disappears is worse than one that visibly didn't take: the row
             shows it and the user can click again. */
          setVotes((current) => ({ ...current, [messageId]: { score, status: "failed" } }));
        });
    },
    [base, runs, threadId],
  );

  return { runFor, voteFor, vote };
}
