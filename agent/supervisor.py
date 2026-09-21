"""Supervisor — the top-level graph: load the customer's record, pick a path, run it.

Flow of one turn:

    START -> inject_crm --- unknown customer_id ---> handle_crm_error -> END
                |
                v
          classify_intent (Claude; reads the messages)
                |
      +---------+--------------+------------------+
      v                        v                  v
  procedural_executor    graph_rag_chain    explain_answer      chosen by route(), not by the model
      |                        |                  |
      +------------------------+------------------+
                               v
                              END

Each path returns its reply as an AIMessage (see agent/state.py reply()), which LangGraph adds to
`messages`. That is the only place the answer lives; its citations are on the message.

With a checkpointer and a thread_id, the state is saved between turns (see agent/state.py), which is
what makes a follow-up work: the customer need not be repeated, Claude sees the earlier messages, and
`explain_answer` can read the last walk's `step_trace`. Without a checkpointer every call stands alone.

Claude is called in four places: classify_intent, explain_answer, agent/ticket_interpreter.py (inside
the walk, only for ticket_text conditions) and agent/graph_rag_chain.py (to write the answer).
Everything else is plain code.
"""

from functools import lru_cache
from typing import Literal, Optional

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import SystemMessage
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from agent.graph_rag_chain import graph_rag_chain
from agent.procedural_executor import build_procedural_executor_graph
from agent.state import SuperAgentState, reply
from config import get_driver
from etl.io_utils import load_json
from mock_crm.crm_store import CustomerNotFoundError, get_customer

_CLASSIFIER_MODEL = "claude-sonnet-5"
_EXPLAIN_MODEL = "claude-sonnet-5"

NOTHING_TO_EXPLAIN = "There is nothing earlier in this conversation to explain yet. Ask your question first."

_ACTION_DETAILS_CYPHER = """
MATCH (a:Action {actionId: $action_id})
OPTIONAL MATCH (a)-[:GOVERNED_BY]->(r:Rule)
RETURN a.text AS text, collect({id: r.ruleId, description: r.description, detail: r.detail}) AS rules
"""


class IntentClassification(BaseModel):
    """The structured answer classify_intent asks Claude for."""

    intent: Literal["troubleshooting", "open_ended", "explain"] = Field(
        description=(
            "'troubleshooting' if the message describes a problem that matches one of the known procedures; "
            "'explain' if it asks about the PREVIOUS answer in this conversation (why it was given, what a "
            "cited rule or step means); otherwise 'open_ended'"
        )
    )
    matched_procedure_id: Optional[str] = Field(
        default=None, description="the matching procedure_id when intent is 'troubleshooting', else null"
    )


@lru_cache(maxsize=1)
def _procedures() -> list[dict]:
    """procedures.json, loaded once."""
    return load_json("procedures.json")


def _procedure_catalog() -> str:
    """Format every procedure's id/name/category, so classify_intent can pick one."""
    return "\n".join(f"- {p['procedure_id']}: {p['procedure_name']} ({p['category']})" for p in _procedures())


def _procedure_name(procedure_id: str) -> str:
    """The name of a procedure, or 'unknown' if the id is not in procedures.json."""
    return next((p["procedure_name"] for p in _procedures() if p["procedure_id"] == procedure_id), "unknown")


def inject_crm(state: SuperAgentState) -> dict:
    """Start a turn: reset what belongs to the previous turn, then look up the customer's CRM record.

    A conversation is saved between turns, so the previous turn's `error` is cleared here. Otherwise
    a stale error would send a good request to handle_crm_error. `step_trace` is deliberately not
    cleared: it is what a follow-up explains, and the next walk replaces it.

    An unknown customer_id does not raise: it sets `error`, and route_after_crm sends the request to
    handle_crm_error.
    """
    fresh_turn = {"error": None}
    try:
        crm_context = get_customer(state["customer_id"])
    except CustomerNotFoundError:
        return {**fresh_turn, "error": f"No CRM record found for customer_id '{state['customer_id']}'"}
    return {**fresh_turn, "crm_context": crm_context}


def route_after_crm(state: SuperAgentState) -> Literal["classify_intent", "handle_crm_error"]:
    """Stop early if inject_crm couldn't find the customer, before any Claude call is made."""
    return "handle_crm_error" if state.get("error") else "classify_intent"


def handle_crm_error(state: SuperAgentState) -> dict:
    """End the request with a clear message when the customer_id isn't in the CRM.

    `error` stays set, so a caller can tell this apart from a normal answer.
    """
    return reply(f"I couldn't find a customer with ID '{state['customer_id']}'. Check the customer ID and try again.")


def classify_intent(state: SuperAgentState) -> dict:
    """PLACEHOLDER: replace this with your own classification. Ask Claude which path a message takes.

    Contract (keep this, whatever the implementation):
      reads   state["messages"] (the conversation; the last one is the new message) and customer_id
      returns {"intent": "troubleshooting" | "open_ended" | "explain",
               "matched_procedure_id": the procedure to walk, or None unless troubleshooting}

    This version shows Claude the procedure list (it must pick a procedure id from a closed set) and
    the whole conversation (each conversation is one case, so it stays short). If it picks 'explain'
    but there is no earlier message, that is corrected to open_ended here, in code.
    """
    # No explicit temperature — claude-sonnet-5 rejects it ("temperature is
    # deprecated for this model"), unlike older Claude models.
    llm = ChatAnthropic(model=_CLASSIFIER_MODEL).with_structured_output(IntentClassification)
    system = SystemMessage(
        "You are routing a telecom support consultant's latest message to a known troubleshooting "
        "procedure, to an explanation of the previous answer, or to an open-ended knowledge lookup.\n\n"
        f"Known procedures:\n{_procedure_catalog()}"
    )
    result: IntentClassification = llm.invoke([system, *state["messages"]])

    intent = result.intent
    if intent == "explain" and len(state["messages"]) < 2:
        intent = "open_ended"
    return {
        "intent": intent,
        "matched_procedure_id": result.matched_procedure_id if intent == "troubleshooting" else None,
    }


def route(state: SuperAgentState) -> Literal["procedural_executor", "explain_answer", "graph_rag_chain"]:
    """Deterministic routing off the already-classified intent (not LLM-guessed here)."""
    if state["intent"] == "troubleshooting":
        return "procedural_executor"
    if state["intent"] == "explain":
        return "explain_answer"
    return "graph_rag_chain"


def _walk_facts(trace: list[dict]) -> tuple[str, list[str]]:
    """The last procedure walk as text for Claude, and the IDs it cited (action, then its rules).

    Reads the steps from `trace` and looks up, in Neo4j, the action given and the rules that limit
    it. Returns ("", []) when there was no walk.
    """
    if not trace:
        return "", []
    lines = [f"Procedure {trace[0]['procedure_id']} (\"{_procedure_name(trace[0]['procedure_id'])}\"). Steps checked, in order:"]
    for step in trace:
        answer = "YES" if step["result"] else "NO"
        evidence = f" ({step['evidence']})" if step.get("evidence") else ""
        lines.append(f"- Step {step['step_id']}: \"{step['description']}\" -> {answer}{evidence}")

    action_id = trace[-1].get("action_id")
    if not action_id:
        return "\n".join(lines), []
    with get_driver().session() as session:
        record = session.run(_ACTION_DETAILS_CYPHER, action_id=action_id).single()
    rules = [r for r in record["rules"] if r["id"]]
    lines.append(f"Action given: {action_id} \"{record['text']}\"")
    if rules:
        lines.append("Rules that limit it:")
        lines += [f"- {r['id']}: {r['description']}. {r['detail']}" for r in rules]
    return "\n".join(lines), [action_id, *(r["id"] for r in rules)]


def explain_answer(state: SuperAgentState) -> dict:
    """Answer a follow-up about the previous answer ("why?", "what does R012 say?").

    Claude gets the conversation plus facts gathered in plain code: what the last procedure walk
    checked (from `step_trace`) and the wording of the action and rules, from Neo4j. It answers from
    those only, and says so if they do not contain the answer. With no earlier message it returns
    NOTHING_TO_EXPLAIN without calling Claude.

    The reply cites the action and rules behind the last walk.
    """
    if len(state["messages"]) < 2:
        return reply(NOTHING_TO_EXPLAIN)

    facts, cited = _walk_facts(state.get("step_trace", []))
    system = SystemMessage(
        "You are helping a call-centre consultant. They are asking a follow-up question about your "
        "previous answer. Answer it from the conversation and the facts below ONLY. If they do not "
        "contain the answer, say so plainly. Be brief.\n\n"
        f"Facts about the last procedure walk in this conversation:\n{facts or '(no procedure was run)'}"
    )
    llm = ChatAnthropic(model=_EXPLAIN_MODEL, thinking={"type": "disabled"})
    response = llm.invoke([system, *state["messages"]])
    return reply(str(response.text), cited)


def build_supervisor_graph(checkpointer=None):
    """Compile the supervisor graph (flow in the module docstring).

    Pass a checkpointer (see config.get_checkpointer) to save conversations between calls, and give
    each call a `thread_id` in its config. Without one, every call stands alone.
    """
    builder = StateGraph(SuperAgentState)
    builder.add_node("inject_crm", inject_crm)
    builder.add_node("handle_crm_error", handle_crm_error)
    builder.add_node("classify_intent", classify_intent)
    builder.add_node("procedural_executor", build_procedural_executor_graph())
    builder.add_node("graph_rag_chain", graph_rag_chain)
    builder.add_node("explain_answer", explain_answer)

    builder.add_edge(START, "inject_crm")
    builder.add_conditional_edges(
        "inject_crm",
        route_after_crm,
        ["classify_intent", "handle_crm_error"],
    )
    builder.add_edge("handle_crm_error", END)
    builder.add_conditional_edges(
        "classify_intent",
        route,
        ["procedural_executor", "explain_answer", "graph_rag_chain"],
    )
    builder.add_edge("procedural_executor", END)
    builder.add_edge("graph_rag_chain", END)
    builder.add_edge("explain_answer", END)
    return builder.compile(checkpointer=checkpointer)
