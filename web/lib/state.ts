/**
 * The agent's state as the browser sees it (useStream<AgentState>), and what a reply carries.
 *
 * Mirrors agent/state.py's SuperAgentState, but only the fields the page reads. The server reports
 * the full schema: curl -s localhost:2024/assistants/super_agent/schemas
 */
import type { BaseMessage } from "langchain";

export interface AgentState {
  messages: BaseMessage[];
  /** The customer currently being discussed: the last customer ID a message named. */
  customer_id?: string;
  intent?: "troubleshooting" | "open_ended" | "explain";
  /** Why the latest turn ended early (unknown customer, Neo4j down, ...), or null. */
  error?: string | null;
}

/** One cited ID with its text, from a reply's additional_kwargs.sources (agent/sources.py). */
export interface Source {
  id: string;
  kind: "action" | "rule" | "article";
  title: string;
  text: string;
}
