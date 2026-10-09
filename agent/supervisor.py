"""Supervisor — the top-level graph: load the customer's record, pick a path, run it.

Flow of one turn:

    START -> inject_crm --- unknown customer_id ---> handle_crm_error -> END
                |
                v
           check_neo4j ---- Neo4j not reachable: reply "offline" ---> END
                |
                v
          classify_intent (Jev; which path, from the messages)
                |
      +---------+------------------------+------------------+
      v                                  v                  v
  match_procedure (Jev; which one)  graph_rag_chain    explain_answer      chosen by route(), not by the model
      |              |                   ^                  |
      | matched      +---- no match -----+                  |
      v                                  |                  |
  procedural_executor                    |                  |
      |                                  |                  |
      +----------------------------------+------------------+
                                         v
                                        END

Each path returns its reply as an AIMessage (see agent/utils.py reply()), which LangGraph adds to
`messages`. That is the only place the answer lives; its citations are on the message.

With a checkpointer and a thread_id, the state is saved between turns (see agent/state.py), which is
what makes a follow-up work: the customer need not be repeated, Claude sees the earlier messages, and
`explain_answer` can read the current customer's last result in `troubleshooting_results`. One
conversation can cover several customers; `customer_id` is the one currently being discussed.
Without a checkpointer every call stands alone.

Jev (TypeSafe, a classifier: typed answers with probabilities, no generated text) is called in
classify_intent and match_procedure; it needs TYPESAFE_API_KEY. Claude is called in three places:
explain_answer, agent/ticket_interpreter.py (inside the walk, only for ticket_text conditions) and
agent/graph_rag_chain.py (to write the answer). Everything else is plain code.
"""

import logging
from functools import lru_cache
from typing import Literal, Optional

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import SystemMessage
from langchain_typesafe import Choice, TypeSafeClassifier
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from agent.graph_rag_chain import graph_rag_chain
from agent.procedural_executor import build_procedural_executor_graph, procedure_name
from agent.state import SuperAgentState
from agent.utils import reply
from config import get_driver, neo4j_unavailable_reason
from etl.io_utils import load_json
from mock_crm.crm_store import CustomerNotFoundError, get_customer

logger = logging.getLogger(__name__)

_EXPLAIN_MODEL = "claude-sonnet-5"

NOTHING_TO_EXPLAIN = "There is nothing earlier in this conversation to explain yet. Ask your question first."

_ACTION_DETAILS_CYPHER = """
MATCH (a:Action {actionId: $action_id})
OPTIONAL MATCH (a)-[:GOVERNED_BY]->(r:Rule)
RETURN a.text AS text, collect({id: r.ruleId, description: r.description, detail: r.detail}) AS rules
"""


# Thresholds for Jev's answers. Starting values: tune them on a labelled set of messages. Kept
# cautious on purpose, since walking the wrong procedure does more harm than a KB answer.
_MIN_TROUBLESHOOTING_PROBABILITY = 0.5  # below this, a troubleshooting pick becomes open_ended
_MIN_PROCEDURE_CONFIDENCE = 0.5  # below this, no procedure is walked

_NO_PROCEDURE = "none"


@lru_cache(maxsize=1)
def _classifier() -> TypeSafeClassifier:
    """The Jev client, made once and reused.

    lru_cache on a no-argument function means: run it on the first call, then return the same result.
    Lazy, because TypeSafeClassifier() raises without TYPESAFE_API_KEY, so building it at import would
    break importing this module (unit tests, Slack bot). Reused, because it holds an HTTP connection pool.
    """
    return TypeSafeClassifier()


@lru_cache(maxsize=1)
def _procedures() -> list[dict]:
    """procedures.json, loaded once (lru_cache: read from disk on the first call, then reused)."""
    return load_json("procedures.json")


def inject_crm(state: SuperAgentState) -> dict:
    """Start a turn: reset what belongs to the previous turn, then look up the customer's CRM record.

    A conversation is saved between turns, so the previous turn's `error` is cleared here. Otherwise
    a stale error would send a good request to handle_crm_error. `troubleshooting_results` is
    deliberately not cleared: it is what a follow-up explains, and a customer's next walk replaces
    only their own entry.

    An unknown customer_id does not raise: it sets `error`, and route_after_crm sends the request to
    handle_crm_error.
    """
    fresh_turn = {"error": None}
    messages = state.get("messages") or []
    logger.info("New turn for %s: %r", state["customer_id"], messages[-1].content if messages else "")
    try:
        crm_context = get_customer(state["customer_id"])
    except CustomerNotFoundError:
        logger.info("Turn stopped: no CRM record for %s", state["customer_id"])
        return {**fresh_turn, "error": f"No CRM record found for customer_id '{state['customer_id']}'"}
    return {**fresh_turn, "crm_context": crm_context}


def route_after_crm(state: SuperAgentState) -> Literal["check_neo4j", "handle_crm_error"]:
    """Stop early if inject_crm couldn't find the customer, before any paid call is made."""
    return "handle_crm_error" if state.get("error") else "check_neo4j"


def handle_crm_error(state: SuperAgentState) -> dict:
    """End the request with a clear message when the customer_id isn't in the CRM.

    `error` stays set, so a caller can tell this apart from a normal answer.
    """
    return reply(f"I couldn't find a customer with ID '{state['customer_id']}'. Check the customer ID and try again.")


NEO4J_DOWN = (
    "The knowledge graph is offline, so I can't answer right now. "
    "Ask whoever runs the agent to start Neo4j, then try again."
)


def check_neo4j(state: SuperAgentState) -> Command[Literal["classify_intent", "__end__"]]:
    """Stop the turn here if Neo4j is not reachable; otherwise go on to classify_intent.

    Every path needs Neo4j (the procedure walk, the knowledge-base search, explain), so without it
    the turn would only fail later, after paying for the Jev calls, with a generic error. If Neo4j
    is down, this replies NEO4J_DOWN, sets `error` to the technical reason (also logged for whoever
    runs the agent) and ends the turn. A Command does both in one node: the update, and where to go.
    """
    reason = neo4j_unavailable_reason()
    if reason:
        logger.error("Turn stopped: %s", reason)
        return Command(update={**reply(NEO4J_DOWN), "error": reason}, goto=END)
    return Command(goto="classify_intent")


def _latest_message(state: SuperAgentState):
    """The consultant's new message: the last one in `messages` when a turn starts.

    The two Jev calls are given only this, not the whole conversation. Measured live, the latest
    message classifies as well on its own, and it keeps earlier customers' messages (one conversation
    can cover several) and an ever-growing history out of each call.
    """
    return state["messages"][-1]


def classify_intent(state: SuperAgentState) -> dict:
    """Decide which path the consultant's latest message takes, with one Jev Choice.

    It is judged on what the consultant wants done, not on the topic: most procedures have a KB
    article on the same topic (cancellation, SIM replacement, outage credit). The procedure list is
    deliberately not shown here; seeing "Contract cancellation request" is what made the old
    classifier send a fee question to that procedure. match_procedure picks the procedure later.

    Returns {"intent": ..., "matched_procedure_id": None}. The id is cleared every turn (the state is
    saved between turns), and only match_procedure sets it.
    """
    response = _classifier().invoke({
        "state": _latest_message(state),
        "questions": {
            # A Choice: Jev picks one label and returns a probability for every label.
            "path": Choice(
                instructions=(
                    "A call-centre consultant is talking to an assistant while handling a customer. "
                    "Classify the consultant's message by what they want done, not by its topic: "
                    "most topics (cancellation, roaming, SIM cards, refunds, outages) come up both as a "
                    "customer's problem and as a general question."
                ),
                criteria={
                    "troubleshooting": (
                        "The consultant is working a case for this customer right now: the customer has "
                        "a problem (something not working, an unexpected charge, a suspension, a lockout) "
                        "or a request the consultant must act on (cancel, replace a SIM, change plan, get "
                        "a credit). The right answer depends on this customer's account."
                    ),
                    "open_ended": (
                        "The consultant wants general information: fees, prices, rates, rules, timelines, "
                        "eligibility, or how a process works. The answer would be the same for any "
                        "customer, even when the topic matches a common customer problem."
                    ),
                    "explain": (
                        "The consultant asks about an earlier answer from the assistant: why it was "
                        "given, what a step, action or cited rule means, or where it came from."
                    ),
                },
            ),
        },
    })
    path = response.choices["path"]  # e.g. path.choice = "troubleshooting"
    # The chosen label's probability, e.g. {"troubleshooting": 0.96, "open_ended": 0.03, "explain": 0.01}.
    # Used instead of path.confidence, which measures how concentrated the whole distribution is.
    troubleshooting_probability = path.probabilities.get("troubleshooting", 0.0)

    intent = path.choice
    # When unsure, answer from the knowledge base instead of walking a procedure.
    if intent == "troubleshooting" and troubleshooting_probability < _MIN_TROUBLESHOOTING_PROBABILITY:
        intent = "open_ended"
    # There is nothing to explain on the first message. Checked on the saved conversation, which Jev
    # doesn't see.
    if intent == "explain" and len(state["messages"]) < 2:
        intent = "open_ended"
    logger.info("Intent: %s", intent)
    logger.debug(
        "  Jev: path=%s, probabilities %s",
        path.choice, {label: round(p, 2) for label, p in path.probabilities.items()},
    )
    return {"intent": intent, "matched_procedure_id": None}


def match_procedure(state: SuperAgentState) -> dict:
    """Pick the procedure to walk, for a message classify_intent called troubleshooting.

    "none", or a pick below the confidence threshold, leaves matched_procedure_id as None, and
    route_after_match sends the message to the knowledge base instead of walking the nearest
    procedure.

    With 20 procedures every one is offered. If the list grows to the hundreds, narrow it first
    (hybrid search over the procedures) and let Jev choose among the top few.
    """
    # One label per procedure, described by its name and when_to_use (procedures.json), e.g.
    #   "PROC-016": "Reported service outage in customer's area. The customer reports no service ...
    #                If they are asking for a credit or compensation for an outage, use PROC-017."
    # when_to_use says how a procedure differs from its neighbours, which the names alone do not.
    criteria = {p["procedure_id"]: f"{p['procedure_name']}. {p['when_to_use']}" for p in _procedures()}
    criteria[_NO_PROCEDURE] = "None of these procedures fits the situation described."

    response = _classifier().invoke({
        "state": _latest_message(state),
        "questions": {
            # A Choice: Jev returns one procedure id (or "none") and a confidence (0 to 1).
            "procedure": Choice(
                instructions=(
                    "A call-centre consultant is handling a customer's problem or request. Which "
                    "troubleshooting procedure fits the situation described in the consultant's message? "
                    "Match the specific symptom or request, not just the topic."
                ),
                criteria=criteria,
            ),
        },
    })
    pick = response.choices["procedure"]  # e.g. pick.choice = "PROC-016", pick.confidence = 0.81

    logger.debug("  Jev: procedure=%s (confidence %.2f)", pick.choice, pick.confidence)
    if pick.choice == _NO_PROCEDURE or pick.confidence < _MIN_PROCEDURE_CONFIDENCE:
        logger.info("Procedure: none fits, so the knowledge base answers")
        return {"matched_procedure_id": None}
    logger.info("Procedure: %s \"%s\"", pick.choice, procedure_name(pick.choice))
    return {"matched_procedure_id": pick.choice}


def route(state: SuperAgentState) -> Literal["match_procedure", "explain_answer", "graph_rag_chain"]:
    """Deterministic routing off the already-classified intent (not LLM-guessed here)."""
    if state["intent"] == "troubleshooting":
        return "match_procedure"
    if state["intent"] == "explain":
        return "explain_answer"
    return "graph_rag_chain"


def route_after_match(state: SuperAgentState) -> Literal["procedural_executor", "graph_rag_chain"]:
    """Walk the matched procedure; with no match, answer from the knowledge base instead."""
    return "procedural_executor" if state.get("matched_procedure_id") else "graph_rag_chain"


def _walk_facts(result: Optional[dict]) -> tuple[str, list[str]]:
    """Turn one customer's last troubleshooting result into facts explain_answer can give Claude, so
    a follow-up like "why?" is answered from what actually happened, not from Claude's guess.

    `result` is that customer's entry in `troubleshooting_results`, written by the walk's resolve:
    {"procedure_id", "action_id", "rule_ids", "checks": ["<step>: yes/no (<value>)", ...]}. It holds
    only IDs for the action and rules, so their wording is looked up in Neo4j here.

    Returns (facts, cited):
      facts  plain text for Claude's prompt, e.g.
               Procedure PROC-001 ("Customer cannot activate roaming"). Steps checked, in order:
               - Roaming is disabled on the account: no (roaming_flag = True)
               ...
               Action given: ACT-015 "Escalate to Tier 2 ..."
               Rules governing it:
               - R012: ...
      cited  the IDs the explanation cites: the action, then its rules, e.g. ["ACT-015", "R012"]

    Returns ("", []) when the customer has no result (no walk yet, or their last walk failed).
    """
    if not result:
        return "", []
    lines = [f"Procedure {result['procedure_id']} (\"{procedure_name(result['procedure_id'])}\"). Steps checked, in order:"]
    lines += [f"- {check}" for check in result["checks"]]

    action_id = result["action_id"]
    with get_driver().session() as session:
        record = session.run(_ACTION_DETAILS_CYPHER, action_id=action_id).single()
    rules = [r for r in record["rules"] if r["id"]]
    lines.append(f"Action given: {action_id} \"{record['text']}\"")
    if rules:
        lines.append("Rules governing it:")
        lines += [f"- {r['id']}: {r['description']}. {r['detail']}" for r in rules]
    else:
        lines.append("Rules governing it: none")  # said outright, so Claude can't pass a step off as a rule
    return "\n".join(lines), [action_id, *(r["id"] for r in rules)]


def explain_answer(state: SuperAgentState) -> dict:
    """Answer a follow-up about an earlier answer ("why?", "what does R012 say?").

    Claude gets the conversation plus facts gathered in plain code about the CURRENT customer only:
    their last troubleshooting result (from `troubleshooting_results`) and the wording of its action
    and rules, from Neo4j. A conversation can cover several customers, so the prompt names the
    customer and the facts never include another customer's walk. Claude answers from those only,
    and says so if they do not contain the answer. With no earlier message it returns
    NOTHING_TO_EXPLAIN without calling Claude.

    The reply cites the action and rules behind that customer's last walk.
    """
    if len(state["messages"]) < 2:
        return reply(NOTHING_TO_EXPLAIN)

    customer_id = state["customer_id"]
    facts, cited = _walk_facts(state.get("troubleshooting_results", {}).get(customer_id))
    system = SystemMessage(
        "You are helping a call-centre consultant. They are asking a follow-up question about an "
        f"earlier answer for customer {customer_id}. The conversation may also mention other "
        f"customers; answer only about {customer_id}. Answer from the conversation and the facts "
        "below ONLY. If they do not contain the answer, say so plainly. Be brief. A rule is a business "
        "policy governing the action, listed under 'Rules governing it'; a step's condition is a check, "
        "not a rule. If no rule governs the action, say so plainly; never call anything else a rule.\n\n"
        f"Facts about {customer_id}'s last procedure walk:\n{facts or '(no procedure was run for this customer)'}"
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
    builder.add_node("check_neo4j", check_neo4j)  # routes itself (returns a Command)
    builder.add_node("classify_intent", classify_intent)
    builder.add_node("match_procedure", match_procedure)
    builder.add_node("procedural_executor", build_procedural_executor_graph())
    builder.add_node("graph_rag_chain", graph_rag_chain)
    builder.add_node("explain_answer", explain_answer)

    builder.add_edge(START, "inject_crm")
    builder.add_conditional_edges(
        "inject_crm",
        route_after_crm,
        ["check_neo4j", "handle_crm_error"],
    )
    builder.add_edge("handle_crm_error", END)
    builder.add_conditional_edges(
        "classify_intent",
        route,
        ["match_procedure", "explain_answer", "graph_rag_chain"],
    )
    builder.add_conditional_edges(
        "match_procedure",
        route_after_match,
        ["procedural_executor", "graph_rag_chain"],
    )
    builder.add_edge("procedural_executor", END)
    builder.add_edge("graph_rag_chain", END)
    builder.add_edge("explain_answer", END)
    return builder.compile(checkpointer=checkpointer)
