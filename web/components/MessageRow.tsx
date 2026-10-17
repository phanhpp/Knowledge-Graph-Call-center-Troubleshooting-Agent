"use client";

import { AIMessage, HumanMessage, type BaseMessage } from "langchain";

import { FeedbackControls } from "@/components/FeedbackControls";
import { Markdown } from "@/components/Markdown";
import { citationsOf, customerOf } from "@/lib/message";
import type { Feedback } from "@/lib/use-feedback";

/**
 * One message. A component per message, not inline JSX in a map: anything per-message that needs
 * hooks later can only live in a child component.
 */
export function MessageRow({
  message,
  kwargs,
  selected,
  feedback,
  onCite,
}: {
  message: BaseMessage;
  /** The message's id and additional_kwargs, read from the saved state first (lib/message.ts). */
  kwargs: { id?: string; additional_kwargs?: Record<string, unknown> };
  selected: boolean;
  feedback: Feedback;
  onCite: (id: string) => void;
}) {
  if (HumanMessage.isInstance(message)) {
    const customer = customerOf(kwargs);
    return (
      <div className="flex justify-end" data-testid="human-message">
        <div className="max-w-[80%] rounded-xl bg-primary-light px-3 py-2 text-sm">
          {customer && <span className="mr-2 font-mono text-xs text-text-tertiary">{customer}</span>}
          {message.text}
        </div>
      </div>
    );
  }

  if (!AIMessage.isInstance(message)) return null;

  const citations = citationsOf(kwargs);
  const canRate = feedback.runFor(message.id) !== undefined;

  return (
    <div className="group flex" data-testid="ai-message">
      <div className={`max-w-[85%] space-y-2 rounded-xl border px-3 py-2 text-sm ${selected ? "border-primary" : "border-border"}`}>
        <Markdown>{message.text}</Markdown>

        {citations.length > 0 && (
          <div className="flex flex-wrap items-center gap-1" aria-label="Sources">
            <span className="text-xs text-text-tertiary">Sources:</span>
            {citations.map((id) => (
              <button
                key={id}
                type="button"
                className="rounded border border-border bg-surface-secondary px-1.5 py-0.5 font-mono text-xs hover:border-primary"
                onClick={() => onCite(id)}
              >
                {id}
              </button>
            ))}
          </div>
        )}

        {canRate && message.id && (
          <FeedbackControls vote={feedback.voteFor(message.id)} onVote={(score, comment) => feedback.vote(message.id!, score, comment)} />
        )}
      </div>
    </div>
  );
}
