"""Supervisor — CRM injection + intent classification, then routes to the
troubleshooting or open-ended path.

The only two LLM calls in this whole project live in classify_intent() here
and inside graph_rag_chain() (Phase 6) — the troubleshooting path
(procedural_executor) is fully deterministic, by design (see its module
docstring).
"""

from typing import Literal, Optional

from langchain_anthropic import ChatAnthropic
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from agent.graph_rag_chain import graph_rag_chain
from agent.procedural_executor import build_procedural_executor_graph
from agent.state import SuperAgentState
from etl.io_utils import load_json
from mock_crm.crm_store import CustomerNotFoundError, get_customer

_CLASSIFIER_MODEL = "claude-sonnet-5"


class IntentClassification(BaseModel):
    intent: Literal["troubleshooting", "open_ended"] = Field(
        description="'troubleshooting' if the query matches one of the known procedures below, else 'open_ended'"
    )
    matched_procedure_id: Optional[str] = Field(
        default=None, description="the matching procedure_id when intent is 'troubleshooting', else null"
    )


def _procedure_catalog() -> str:
    """Format every procedure's id/name/category as few-shot context for classify_intent()."""
    procedures = load_json("procedures.json")
    return "\n".join(f"- {p['procedure_id']}: {p['procedure_name']} ({p['category']})" for p in procedures)


def _article_catalog() -> str:
    """Format every KB article's title/category as few-shot context for classify_intent()."""
    articles = load_json("kb_articles.json")
    return "\n".join(f"- {a['title']} ({a['category']})" for a in articles)


def inject_crm(state: SuperAgentState) -> dict:
    """Look up the customer's CRM record by customer_id and attach it to state."""
    try:
        crm_context = get_customer(state["customer_id"])
    except CustomerNotFoundError:
        return {"error": f"No CRM record found for customer_id '{state['customer_id']}'"}
    return {"crm_context": crm_context}


def classify_intent(state: SuperAgentState) -> dict:
    """LLM call: troubleshooting (+ which procedure) vs. open_ended.

    Few-shot on procedure names vs. KB article titles, per docs/proposed_plan.md
    Phase 4 Step 15 — deterministic routing after this, not LLM-guessed.
    """
    # No explicit temperature — claude-sonnet-5 rejects it ("temperature is
    # deprecated for this model"), unlike older Claude models.
    llm = ChatAnthropic(model=_CLASSIFIER_MODEL).with_structured_output(IntentClassification)
    prompt = (
        "You are routing a telecom support consultant's query to either a known "
        "troubleshooting procedure or an open-ended knowledge lookup.\n\n"
        f"Known procedures:\n{_procedure_catalog()}\n\n"
        f"Known knowledge-base topics (context only, not a target to return):\n{_article_catalog()}\n\n"
        f"Consultant query: {state['query']}"
    )
    result: IntentClassification = llm.invoke(prompt)
    return {"intent": result.intent, "matched_procedure_id": result.matched_procedure_id}


def route(state: SuperAgentState) -> Literal["procedural_executor", "graph_rag_chain"]:
    """Deterministic routing off the already-classified intent (not LLM-guessed here)."""
    return "procedural_executor" if state["intent"] == "troubleshooting" else "graph_rag_chain"


def build_supervisor_graph(checkpointer=None):
    """START -> inject_crm -> classify_intent -> [route] -> procedural_executor | graph_rag_chain -> END.

    checkpointer is optional here (e.g. a SqliteSaver) — left to the caller
    (Phase 7 end-to-end wiring) so this module stays testable standalone.
    """
    builder = StateGraph(SuperAgentState)
    builder.add_node("inject_crm", inject_crm)
    builder.add_node("classify_intent", classify_intent)
    builder.add_node("procedural_executor", build_procedural_executor_graph())
    builder.add_node("graph_rag_chain", graph_rag_chain)

    builder.add_edge(START, "inject_crm")
    builder.add_edge("inject_crm", "classify_intent")
    builder.add_conditional_edges(
        "classify_intent",
        route,
        ["procedural_executor", "graph_rag_chain"],
    )
    builder.add_edge("procedural_executor", END)
    builder.add_edge("graph_rag_chain", END)
    return builder.compile(checkpointer=checkpointer)
