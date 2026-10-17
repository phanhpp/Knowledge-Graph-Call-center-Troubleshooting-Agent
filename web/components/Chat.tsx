"use client";

import { useStream } from "@langchain/react";
import { AIMessage } from "langchain";
import { useMemo, useState } from "react";

import { CustomerCard } from "@/components/CustomerCard";
import { MessageRow } from "@/components/MessageRow";
import { SourcesPanel } from "@/components/SourcesPanel";
import { ThreadList } from "@/components/ThreadList";
import { ASSISTANT_ID, resolveAgentUrl } from "@/lib/agent";
import { citationsOf, humanMessage, normalizeCustomerId, withSavedKwargs } from "@/lib/message";
import type { AgentState } from "@/lib/state";
import { useCustomer } from "@/lib/use-customer";
import { useFeedback } from "@/lib/use-feedback";
import { useSavedKwargs } from "@/lib/use-saved-kwargs";

const EXAMPLES: { customer: string; text: string }[] = [
  { customer: "CUST-1001", text: "customer cannot activate roaming" },
  { customer: "", text: "What is the SIM replacement fee?" },
  { customer: "", text: "What's the family plan member limit?" },
];

/**
 * One conversation: the thread list, the chat, and the side panels for the customer and sources.
 *
 * One useStream per thread (rebuilt by ChatShell when the thread changes). It talks to the agent
 * through the proxy route, which adds the API key server-side.
 */
export function Chat({
  threadId,
  onThreadId,
  onOpenThread,
  onNewThread,
}: {
  threadId: string | null;
  onThreadId: (id: string) => void;
  onOpenThread: (id: string) => void;
  onNewThread: () => void;
}) {
  const [apiUrl] = useState(resolveAgentUrl);
  const stream = useStream<AgentState>({ apiUrl, assistantId: ASSISTANT_ID, threadId, onThreadId });
  const messages = stream.messages;
  // Citations, sources and run ids come from the thread's saved state: streamed replies arrive
  // without them (lib/use-saved-kwargs.ts).
  const saved = useSavedKwargs(stream.client, threadId, stream.isLoading);
  const views = useMemo(() => messages.map((m) => withSavedKwargs(m, saved)), [messages, saved]);
  const feedback = useFeedback(views, threadId);

  const [text, setText] = useState("");
  const [customerInput, setCustomerInput] = useState("");
  const typedCustomer = normalizeCustomerId(customerInput);
  const customerInvalid = customerInput.trim() !== "" && !typedCustomer;

  // The card follows the customer typed in the field, else the one the conversation is about.
  const customer = useCustomer(typedCustomer ?? stream.values?.customer_id ?? null);

  // The reply whose sources the panel shows: the one clicked, else the latest that cites something.
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [highlight, setHighlight] = useState<string | null>(null);
  const latestCiting = [...messages].reverse().find((m) => AIMessage.isInstance(m) && citationsOf(withSavedKwargs(m, saved)).length > 0);
  const selectedMessage = messages.find((m) => m.id === selectedId) ?? latestCiting ?? null;
  const selected = selectedMessage ? withSavedKwargs(selectedMessage, saved) : null;

  function send(body: string, customerId: string | null) {
    const trimmed = body.trim();
    if (!trimmed || stream.isLoading) return;
    // Only the stream's messages are rendered: useStream adds this one optimistically.
    void stream.submit({ messages: [humanMessage(trimmed, customerId)] });
    setText("");
    setSelectedId(null);
    setHighlight(null);
  }

  return (
    <div className="flex h-full">
      <ThreadList client={stream.client} activeId={threadId} refreshKey={`${threadId}:${stream.isLoading}`} onOpen={onOpenThread} onNew={onNewThread} />

      <main className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center justify-between border-b border-border px-5 py-3">
          <div>
            <h1 className="text-sm font-semibold">Super Agent</h1>
            <p className="text-xs text-text-tertiary">Troubleshooting walks and knowledge-base answers, with their sources</p>
          </div>
          <button
            type="button"
            className="text-xs text-text-tertiary hover:text-text"
            onClick={() => void fetch("/api/login", { method: "DELETE" }).then(() => window.location.assign("/login"))}
          >
            Sign out
          </button>
        </header>

        <section className="flex-1 space-y-4 overflow-y-auto px-5 py-4" aria-label="Conversation">
          {messages.length === 0 && !stream.isLoading && (
            <div className="mx-auto mt-10 max-w-md space-y-3 text-center">
              <p className="text-sm text-text-secondary">
                Name the customer for a troubleshooting question; leave it empty for a general one.
              </p>
              <div className="flex flex-col gap-2">
                {EXAMPLES.map((example) => (
                  <button
                    key={example.text}
                    type="button"
                    className="rounded-lg border border-border px-3 py-2 text-left text-sm hover:bg-surface-secondary"
                    onClick={() => {
                      setCustomerInput(example.customer);
                      send(example.text, example.customer || null);
                    }}
                  >
                    {example.customer && <span className="mr-2 font-mono text-xs text-text-tertiary">{example.customer}</span>}
                    {example.text}
                  </button>
                ))}
              </div>
            </div>
          )}

          {messages.map((message) => (
            <MessageRow
              key={message.id}
              message={message}
              kwargs={withSavedKwargs(message, saved)}
              selected={message.id === selected?.id}
              feedback={feedback}
              onCite={(id) => {
                setSelectedId(message.id ?? null);
                setHighlight(id);
              }}
            />
          ))}

          {stream.isLoading && (
            <p className="text-sm text-text-tertiary" role="status">
              <span className="animate-[blink_1.2s_infinite]">Working on it…</span>
            </p>
          )}

          {stream.error != null && (
            <p role="alert" className="rounded-md border border-error/30 bg-error/5 px-3 py-2 text-sm text-error">
              Couldn&apos;t reach the agent. {stream.error instanceof Error ? stream.error.message : String(stream.error)}
            </p>
          )}
        </section>

        <form
          className="space-y-2 border-t border-border px-5 py-3"
          onSubmit={(event) => {
            event.preventDefault();
            if (!customerInvalid) send(text, typedCustomer);
          }}
        >
          <div className="flex items-center gap-2">
            <label htmlFor="customer" className="text-xs text-text-tertiary">
              Customer
            </label>
            <input
              id="customer"
              value={customerInput}
              onChange={(event) => setCustomerInput(event.target.value)}
              placeholder="CUST-1001 (empty for a general question)"
              aria-invalid={customerInvalid}
              className="w-72 rounded-md border border-border bg-surface px-2 py-1 font-mono text-xs focus:outline-none focus:ring-1 focus:ring-primary"
            />
            {customerInvalid && <span className="text-xs text-error">A customer ID looks like CUST-1001.</span>}
          </div>
          <div className="flex gap-2">
            <input
              value={text}
              onChange={(event) => setText(event.target.value)}
              placeholder="Describe the customer's problem, or ask a question…"
              aria-label="Message"
              className="min-w-0 flex-1 rounded-md border border-border bg-surface px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary"
            />
            <button
              type="submit"
              disabled={stream.isLoading || !text.trim() || customerInvalid}
              className="rounded-md bg-primary-dark px-4 py-2 text-sm font-medium text-white disabled:opacity-40"
            >
              Send
            </button>
          </div>
        </form>
      </main>

      <aside className="hidden w-80 shrink-0 flex-col gap-4 overflow-y-auto border-l border-border bg-surface-secondary p-4 lg:flex">
        <CustomerCard lookup={customer} />
        <SourcesPanel message={selected} highlight={highlight} />
      </aside>
    </div>
  );
}
