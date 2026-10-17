/**
 * The message formats, browser side (agent/utils.py is the Python side):
 *   - a consultant message names its customer in additional_kwargs.customer_id (absent for a
 *     general question);
 *   - a reply carries additional_kwargs.citations (IDs), .sources (their text) and .run_id (the
 *     LangSmith run that produced it, for 👍👎).
 * These helpers are the only browser code that knows those formats.
 */
import { HumanMessage } from "langchain";

import type { Source } from "@/lib/state";

/** Anything with LangChain's additional_kwargs: a BaseMessage instance or a plain message object. */
type WithKwargs = { additional_kwargs?: Record<string, unknown> } | null | undefined;

const CUSTOMER_ID = /^CUST-\d{4}$/;

/** A typed customer ID, cleaned up ("cust-1001 " -> "CUST-1001"), or null if it isn't one. */
export function normalizeCustomerId(typed: string): string | null {
  const id = typed.trim().toUpperCase();
  return CUSTOMER_ID.test(id) ? id : null;
}

/** The consultant's message, stamped with its customer (as agent/utils.py consultant_message). */
export function humanMessage(text: string, customerId?: string | null): HumanMessage {
  return new HumanMessage({ content: text, additional_kwargs: customerId ? { customer_id: customerId } : {} });
}

/** The IDs a reply cites (empty for explanations and fixed messages). */
export function citationsOf(message: WithKwargs): string[] {
  const value = message?.additional_kwargs?.citations;
  return Array.isArray(value) ? value.filter((v): v is string => typeof v === "string") : [];
}

/** The text behind each cited ID. Replies saved before sources existed have none. */
export function sourcesOf(message: WithKwargs): Source[] {
  const value = message?.additional_kwargs?.sources;
  return Array.isArray(value) ? (value as Source[]) : [];
}

/** The LangSmith run that produced a reply, or undefined (tracing was off, or a consultant message). */
export function runIdOf(message: WithKwargs): string | undefined {
  const value = message?.additional_kwargs?.run_id;
  return typeof value === "string" && value ? value : undefined;
}

/**
 * Each message's additional_kwargs, by message id, from the thread's saved state (GET /threads/{id}/state).
 *
 * Why: a reply that arrives live through the stream comes without its additional_kwargs (see
 * lib/use-saved-kwargs.ts), while the saved state has them, under the same id. Rendering reads the
 * saved copy first.
 */
export function savedKwargs(stateMessages: readonly unknown[] | undefined): Map<string, Record<string, unknown>> {
  const byId = new Map<string, Record<string, unknown>>();
  for (const message of stateMessages ?? []) {
    const m = message as { id?: unknown; additional_kwargs?: unknown };
    if (typeof m.id === "string" && m.additional_kwargs && typeof m.additional_kwargs === "object") {
      byId.set(m.id, m.additional_kwargs as Record<string, unknown>);
    }
  }
  return byId;
}

/** A message's id and additional_kwargs, the saved copy first (see savedKwargs). */
export function withSavedKwargs(
  message: { id?: string; additional_kwargs?: Record<string, unknown> },
  saved: Map<string, Record<string, unknown>>,
): { id?: string; additional_kwargs?: Record<string, unknown> } {
  const fromState = message.id ? saved.get(message.id) : undefined;
  return { id: message.id, additional_kwargs: fromState ?? message.additional_kwargs };
}

/** The customer a consultant message named, if any. */
export function customerOf(message: WithKwargs): string | undefined {
  const value = message?.additional_kwargs?.customer_id;
  return typeof value === "string" && value ? value : undefined;
}
