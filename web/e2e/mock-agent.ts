import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import type { Page, Route } from "@playwright/test";

/*
 * A fake agent server, built from traffic recorded off the real one (e2e/record-fixtures.mjs). Every
 * shape here (the saved state, the run.start command, the thread list) was copied from an actual
 * run, not invented. (Pattern from the hitl-py web app.)
 *
 * What it serves: each conversation's SAVED state. The live event stream is held open and silent,
 * because the page takes citations, sources and run ids from the saved state anyway
 * (lib/use-saved-kwargs.ts), and a model's wording changes every run. What the tests check is real:
 * what the page renders from server state, and exactly what it sends when you click.
 */

type Exchange = { method: string; path: string; requestBody: string | null; status: number; body: string };

const FLOW: Exchange[] = JSON.parse(readFileSync(resolve(__dirname, "fixtures/flow.json"), "utf8"));

function lastBody(match: (e: Exchange) => boolean): any {
  const found = FLOW.filter(match).at(-1);
  if (!found) throw new Error("fixture missing: re-run e2e/record-fixtures.mjs");
  return JSON.parse(found.body);
}

function threadIdOf(path: string): string {
  return path.split("/")[4]; // /api/langgraph/threads/<id>/...
}

/** The final saved state of each recorded conversation, by thread id. */
const STATES = new Map<string, any>();
for (const e of FLOW) if (e.path.endsWith("/state")) STATES.set(threadIdOf(e.path), JSON.parse(e.body));

/** The troubleshooting conversation (a walk, then "why?") and the knowledge-base one. */
export const WALK_THREAD = [...STATES].find(([, s]) => s.values.customer_id === "CUST-1001")![0];
export const KB_THREAD = [...STATES].find(([, s]) => !s.values.customer_id)![0];

const WALK_STATE = STATES.get(WALK_THREAD);
/** The walk reply, as saved: its run id is what a 👍 on it must post. */
export const WALK_REPLY = WALK_STATE.values.messages[1];

const CUSTOMER = lastBody((e) => e.path.endsWith("/customers/CUST-1001"));
const COMMAND_OK = lastBody((e) => e.path.endsWith("/commands"));

/* Full sources (GET /sources/{id}), built from the repo's data files the same way agent/sources.py
   full_source builds them, so expanding a source shows the real text. */
const DATA = resolve(__dirname, "../../data");
const readData = (file: string) => JSON.parse(readFileSync(resolve(DATA, file), "utf8")) as Record<string, string>[];
export const FULL_SOURCES = new Map<string, { id: string; kind: string; title: string; text: string; label: string }>();
for (const a of readData("actions.json")) FULL_SOURCES.set(a.action_id, { id: a.action_id, kind: "action", title: a.text, text: "", label: a.category });
for (const r of readData("rules.json")) FULL_SOURCES.set(r.rule_id, { id: r.rule_id, kind: "rule", title: r.description, text: r.detail, label: r.constraint_type.replace(/_/g, " ") });
for (const k of readData("kb_articles.json")) FULL_SOURCES.set(k.article_id, { id: k.article_id, kind: "article", title: k.title, text: k.content, label: k.category });

/* The thread list: just the two recorded conversations, newest first. */
const THREADS = [KB_THREAD, WALK_THREAD].map((id) => ({
  thread_id: id,
  created_at: "2026-10-17T00:00:00Z",
  updated_at: "2026-10-17T00:00:00Z",
  metadata: { graph_id: "super_agent" },
  status: "idle",
  values: STATES.get(id).values,
}));

export interface MockAgent {
  /** Every JSON-RPC body the page sent to /commands, in order. */
  commands: Record<string, any>[];
  /** Every vote the page posted. */
  votes: Record<string, any>[];
}

export interface MockOptions {
  /** Votes already in LangSmith, by run id. */
  recordedVotes?: Record<string, { score: 0 | 1 }>;
  /** Status for POST /feedback (e.g. 502 to see a vote fail). */
  feedbackStatus?: number;
  /** Status for /commands (e.g. 502: the agent proxy couldn't reach the agent). */
  commandStatus?: number;
}

export async function mockAgent(page: Page, options: MockOptions = {}): Promise<MockAgent> {
  const commands: Record<string, any>[] = [];
  const votes: Record<string, any>[] = [];

  await page.route("**/api/langgraph/**", async (route: Route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const json = (body: unknown, status = 200) =>
      route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });

    if (path.endsWith("/threads/search")) return json(THREADS);
    if (path.endsWith("/state")) {
      return json(STATES.get(threadIdOf(path)) ?? { values: { messages: [] }, next: [], tasks: [], metadata: {} });
    }
    if (path.endsWith("/history")) {
      const state = STATES.get(threadIdOf(path));
      return json(state ? [state] : []);
    }
    if (path.endsWith("/customers/CUST-1001")) return json(CUSTOMER);
    if (path.includes("/customers/")) return json({ error: "no customer with that ID" }, 404);
    if (path.includes("/sources/")) {
      const found = FULL_SOURCES.get(decodeURIComponent(path.split("/").at(-1) ?? ""));
      return found ? json(found) : json({ error: "no source with that ID" }, 404);
    }
    if (path.endsWith("/votes")) return json({ votes: options.recordedVotes ?? {} });
    if (path.includes("/feedback/") && request.method() === "POST") {
      votes.push(JSON.parse(request.postData() ?? "{}"));
      const status = options.feedbackStatus ?? 200;
      return json(status === 200 ? { ok: true, feedbackId: "f1" } : { error: "could not record feedback" }, status);
    }
    if (path.endsWith("/commands")) {
      const body = JSON.parse(request.postData() ?? "{}");
      commands.push(body);
      const status = options.commandStatus ?? 200;
      if (status !== 200) return json({ error: "Could not reach the agent server." }, status);
      return json({ ...COMMAND_OK, id: body.id });
    }
    // The live event stream: held open and silent (see the top of this file).
    if (path.endsWith("/stream/events")) return route.fulfill({ status: 200, contentType: "text/event-stream", body: "" });

    return json({});
  });

  return { commands, votes };
}
