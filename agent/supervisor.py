"""Supervisor — the top-level graph: load the customer's record, pick a path, run it.

Flow of one turn:

    START -> check_customer --- the message names an unknown ID ---> reply "I couldn't find..." -> END
                |                (a message naming no customer is not checked)
                v
           check_neo4j ---- Neo4j not reachable: reply "offline" ---> END
                |
                v
          classify_intent (Jev; which path, from the latest message)
                |
      +---------+------------------------------+------------------+
      v                                        v                  v
  load_crm: the customer's CRM record    graph_rag_chain    explain_answer      chosen by route(), not by the model
      |  (no customer yet: reply "Which customer...?" -> END)  ^                  |
      v                                        |                  |
  match_procedure (Jev; which one) --- no match ---+                  |
      | matched                                |                  |
      v                                        |                  |
  procedural_executor                          |                  |
      |                                        |                  |
      +----------------------------------------+------------------+
                                               v
                                              END

Each path returns its reply as an AIMessage (see agent/utils.py reply()), which LangGraph adds to
`messages`. That is the only place the answer lives; its citations are on the message.

With a checkpointer and a thread_id, the state is saved between turns (see agent/state.py), which is
what makes a follow-up work: the customer need not be repeated, and `explain_answer` reads the
earlier messages. One conversation can cover several customers and general questions. A message is
stamped with the customer it names (by the caller, the Slack bot) or, for a troubleshooting turn,
with the current customer (by load_crm); an unstamped message was a general question. `customer_id`
is the current customer: the last one named. Without a checkpointer every call stands alone.

Jev (TypeSafe, a classifier: typed answers with probabilities, no generated text) is called in
classify_intent and match_procedure; it needs TYPESAFE_API_KEY. Claude is called in three places:
explain_answer, agent/ticket_interpreter.py (inside the walk, only for ticket_text conditions) and
agent/graph_rag_chain.py (to write the answer). Everything else is plain code.
"""

import logging
from functools import lru_cache
from typing import Literal

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
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

_EXPLAIN_HISTORY = 10  # how many recent messages (all customers) explain_answer gives Claude

_RULES_CYPHER = """
MATCH (r:Rule) WHERE r.ruleId IN $ids
RETURN r.ruleId AS id, r.description AS description, r.detail AS detail
ORDER BY id
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


UNKNOWN_CUSTOMER = "I couldn't find a customer with ID '{customer_id}'. Check the customer ID and try again."
WHICH_CUSTOMER = (
    "Which customer is this about? Start your message with their customer ID, "
    "for example: CUST-1001 customer cannot activate roaming"
)


def _named_customer(state: SuperAgentState) -> Optional[str]:
    """The customer ID the consultant's new message names, or None.

    The caller (the Slack bot) finds the ID in the text and stamps it on the message as
    `additional_kwargs["customer_id"]`; load_crm also stamps a troubleshooting message with the
    current customer. An unstamped message names no customer: it may be a general question.
    """
    messages = state.get("messages") or []
    return messages[-1].additional_kwargs.get("customer_id") if messages else None


def check_customer(state: SuperAgentState) -> Command[Literal["check_neo4j", "__end__"]]:
    """Start a turn: if the new message names a customer, check the ID exists and make it current.

    A message without an ID is not checked: it may be a general question, which needs no customer.
    The CRM record itself is loaded later, and only for troubleshooting (load_crm). This runs before
    any paid call, so a wrong ID costs nothing. It also clears the previous turn's `error`, since
    the state is saved between turns.
    """
    named = _named_customer(state)
    logger.info("New turn%s: %r", f" for {named}" if named else " (no customer named)", state["messages"][-1].content)
    if named is None:
        return Command(update={"error": None}, goto="check_neo4j")
    try:
        get_customer(named)
    except CustomerNotFoundError:
        logger.info("Turn stopped: no CRM record for %s", named)
        return Command(
            update={**reply(UNKNOWN_CUSTOMER.format(customer_id=named)), "error": f"No CRM record found for customer_id '{named}'"},
            goto=END,
        )
    return Command(update={"error": None, "customer_id": named}, goto="check_neo4j")


def load_crm(state: SuperAgentState) -> Command[Literal["match_procedure", "__end__"]]:
    """Troubleshooting only: load the current customer's CRM record, which the walk checks.

    With no customer named yet in this conversation, it asks for one instead: troubleshooting
    always needs a customer. A message that named no customer but follows up the current one
    ("she is abroad now") is stamped with that customer here, so the history shows whose walk it was.
    """
    customer_id = state.get("customer_id")
    if not customer_id:
        logger.info("Turn stopped: troubleshooting with no customer named")
        return Command(update={**reply(WHICH_CUSTOMER), "error": "No customer named for troubleshooting"}, goto=END)
    update = {"crm_context": get_customer(customer_id)}
    latest = state["messages"][-1]
    if not latest.additional_kwargs.get("customer_id"):
        stamped = {**latest.additional_kwargs, "customer_id": customer_id}
        update["messages"] = [latest.model_copy(update={"additional_kwargs": stamped})]
    return Command(update=update, goto="match_procedure")


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


def route(state: SuperAgentState) -> Literal["load_crm", "explain_answer", "graph_rag_chain"]:
    """Deterministic routing off the already-classified intent (not LLM-guessed here)."""
    if state["intent"] == "troubleshooting":
        return "load_crm"
    if state["intent"] == "explain":
        return "explain_answer"
    return "graph_rag_chain"


def route_after_match(state: SuperAgentState) -> Literal["procedural_executor", "graph_rag_chain"]:
    """Walk the matched procedure; with no match, answer from the knowledge base instead."""
    return "procedural_executor" if state.get("matched_procedure_id") else "graph_rag_chain"


def _explain_history(messages: list, limit: int) -> tuple[list, list[str]]:
    """The last `limit` messages, each labelled with its customer, for explain_answer's prompt.

    Consultant messages become "[CUST-1001] <text>" and replies "[reply about CUST-1001] <text>",
    a reply belonging to the consultant message before it. A message stamped with no customer was a
    general question: "[general] <text>" and "[reply, general] <text>". A reply's citations are added as
    "Sources: ..." (Claude does not see `additional_kwargs`). The window starts on a consultant
    message, which Claude requires. The saved messages are not changed.

    Returns (the labelled messages, every ID the replies in the window cite).
    """
    window = messages[-limit:]
    while window and window[0].type != "human":
        window = window[1:]
    labelled, cited = [], []
    customer = None
    for message in window:
        if message.type == "human":
            customer = message.additional_kwargs.get("customer_id")
            labelled.append(HumanMessage(f"[{customer or 'general'}] {message.content}"))
        else:
            citations = list(message.additional_kwargs.get("citations", []))
            sources = f"\n\nSources: {', '.join(citations)}" if citations else ""
            label = f"reply about {customer}" if customer else "reply, general"
            labelled.append(AIMessage(f"[{label}] {message.content}{sources}"))
            cited += [c for c in citations if c not in cited]
    return labelled, cited


def _rule_wording(ids: list[str]) -> str:
    """The wording of every rule among `ids` (the IDs cited in the conversation), from Neo4j.

    Replies carry rule IDs only (e.g. "Sources: ACT-015, R012"), so "what does that rule say?"
    needs the text looked up. Non-rule IDs (actions, articles) simply match nothing.
    """
    if not ids:
        return ""
    with get_driver().session() as session:
        rows = session.run(_RULES_CYPHER, ids=ids).data()
    return "\n".join(f"- {r['id']}: {r['description']}. {r['detail']}" for r in rows)


def explain_answer(state: SuperAgentState) -> dict:
    """Answer a follow-up about an earlier answer ("why?", "what does R012 say?"), from the
    conversation itself.

    Claude gets the last _EXPLAIN_HISTORY messages, each labelled with the customer it was about, or
    as general (see _explain_history), so it can follow a conversation that moves between customers
    and general questions. A troubleshooting reply already holds everything a "why?" needs: the
    action, why the walk stopped, and every step checked with its value. The only thing added is the
    wording of the rules those messages cite, from Neo4j.

    If the question names a customer, Claude answers only about that customer's messages. If it
    names none, Claude answers about the earlier answer it refers to, usually the latest one: no
    customer is forced on it. Either way it is told never to claim an earlier answer was wrong.
    With no earlier message it returns NOTHING_TO_EXPLAIN without calling Claude.

    The reply cites nothing: an explanation is not a new source, and the answer it explains
    already showed its sources.
    """
    if len(state["messages"]) < 2:
        return reply(NOTHING_TO_EXPLAIN)

    named = _named_customer(state)
    if named:
        focus = (
            f"The question is about customer {named}: answer only from {named}'s messages, and never "
            f"take a reply about another customer, or a general reply, as one about {named}."
        )
    else:
        focus = (
            "The question names no customer: answer about the earlier answer it refers to, usually "
            "the most recent one."
        )
    history, cited = _explain_history(state["messages"], _EXPLAIN_HISTORY)
    rules = _rule_wording(cited)
    system = SystemMessage(
        "You are helping a call-centre consultant. They are asking a follow-up question about an "
        "earlier answer. The conversation below can cover several customers and general questions: "
        "each consultant message starts with the customer ID it was about, or [general], and each "
        "reply starts with '[reply about <customer ID>]' or '[reply, general]'. "
        f"{focus} Answer from the conversation and the rule wording below ONLY; if they do not "
        "contain the answer, say so plainly, and never guess or claim an earlier answer was wrong. "
        "Be brief. A rule is a business policy governing an action, listed below; a step's "
        "condition is a check, not a rule. If no rule governs the action, say so plainly; never call "
        "anything else a rule.\n\n"
        f"Rules cited in the conversation:\n{rules or '(none)'}"
    )
    llm = ChatAnthropic(model=_EXPLAIN_MODEL, thinking={"type": "disabled"})
    response = llm.invoke([system, *history])
    return reply(str(response.text))


def build_supervisor_graph(checkpointer=None):
    """Compile the supervisor graph (flow in the module docstring).

    Pass a checkpointer (see config.get_checkpointer) to save conversations between calls, and give
    each call a `thread_id` in its config. Without one, every call stands alone.
    """
    builder = StateGraph(SuperAgentState)
    builder.add_node("check_customer", check_customer)  # routes itself (returns a Command)
    builder.add_node("check_neo4j", check_neo4j)  # routes itself
    builder.add_node("classify_intent", classify_intent)
    builder.add_node("load_crm", load_crm)  # routes itself
    builder.add_node("match_procedure", match_procedure)
    builder.add_node("procedural_executor", build_procedural_executor_graph())
    builder.add_node("graph_rag_chain", graph_rag_chain)
    builder.add_node("explain_answer", explain_answer)

    builder.add_edge(START, "check_customer")
    builder.add_conditional_edges(
        "classify_intent",
        route,
        ["load_crm", "explain_answer", "graph_rag_chain"],
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
