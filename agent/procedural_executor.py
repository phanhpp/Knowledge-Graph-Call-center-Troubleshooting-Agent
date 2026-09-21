"""Procedural Executor — deterministic Step-chain traversal for almost every
condition, with one deliberate exception for free-text interpretation.

Design decision (confirmed over Vodafone's LLM Compiler pattern): most
Conditions are validated by one or a few *declared* API calls (see
mock_crm/condition_evaluator.py — a `verification_chain` handles the
multi-call case, e.g. fetch a zip code, then check network status for it).
Because the call sequence is always known in advance from conditions.json,
there is never a DAG to plan or genuine ambiguity to Replan around — a
plain Python function walks the chain. evaluate_field_condition() IS the
Joiner, for every field/chain condition.

The one exception is `kind: "ticket_text"` conditions (see
agent/ticket_interpreter.py): interpreting a customer's free-text ticket
description isn't a lookup, it's language understanding, which deterministic
code genuinely can't do. That's the only place an LLM enters this executor,
and the only place "Replanning" is real rather than decorative.

Flow:
    fetch_step -> route_after_fetch
        no matching Step found  -> handle_fetch_error -> END (e.g. matched_procedure_id
                                    was hallucinated/wrong/None — never crash on it)
        field_check/chain_check -> evaluate_field_condition  --\\
        ticket_text             -> interpret_ticket_condition --+--> advance_or_resolve
                                                                       condition true                  -> resolve -> END
                                                                       condition false, more steps     -> advance current_step_id, loop back to fetch_step
                                                                       condition false, terminal step  -> resolve (false-branch catch-all action) -> END
"""

from typing import Literal

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from agent.state import ProceduralExecutorState
from agent.ticket_interpreter import build_ticket_interpreter_graph
from config import get_driver
from mock_crm.condition_evaluator import get_condition, resolve_condition

FETCH_STEP_CYPHER = """
MATCH (s:Step {procedureId: $procedure_id, stepId: $step_id})
MATCH (s)-[:HAS_CONDITION]->(c:Condition)
OPTIONAL MATCH (s)-[:NEXT_STEP]->(nxt:Step)
OPTIONAL MATCH (s)-[ha:HAS_ACTION]->(a:Action)
RETURN c.conditionId AS conditionId,
       nxt.stepId AS nextStepId,
       collect({outcome: ha.outcome, actionId: a.actionId}) AS actions
"""

RESOLVE_ACTION_CYPHER = """
MATCH (a:Action {actionId: $action_id})
OPTIONAL MATCH (a)-[:GOVERNED_BY]->(r:Rule)
RETURN a.text AS text, collect(r.ruleId) AS ruleIds
"""


def fetch_step(state: ProceduralExecutorState) -> dict:
    """Load the current Step's Condition (full definition, from
    conditions.json via get_condition — Neo4j only stores flat properties,
    not a nested verification_chain), candidate Actions, and next_step.

    current_step_id defaults to 1 on the loop's first entry (nothing has set
    it yet); every subsequent call arrives with it already advanced to
    next_step_id by advance_or_resolve's Command.
    """
    step_id = state.get("current_step_id") or 1
    driver = get_driver()
    with driver.session() as session:
        record = session.run(
            FETCH_STEP_CYPHER,
            procedure_id=state["matched_procedure_id"],
            step_id=step_id,
        ).single()

    if record is None:
        return {"error": f"Step {step_id} not found for procedure {state['matched_procedure_id']}"}

    action_if_true_id = None
    action_if_false_id = None
    for a in record["actions"]:
        if a["outcome"] == "true":
            action_if_true_id = a["actionId"]
        elif a["outcome"] == "false":
            action_if_false_id = a["actionId"]

    return {
        "current_step_id": step_id,
        "current_condition": get_condition(record["conditionId"]),
        "action_if_true_id": action_if_true_id,
        "action_if_false_id": action_if_false_id,
        "next_step_id": record["nextStepId"],
        "steps_visited": [step_id],
    }


def route_after_fetch(state: ProceduralExecutorState) -> Literal["evaluate_field_condition", "interpret_ticket_condition", "handle_fetch_error"]:
    """Check for a fetch_step failure first (e.g. matched_procedure_id was
    hallucinated/None and no Step matched) before dispatching by condition
    kind — state["current_condition"] is never set on that path, so reading
    it without this check crashes with KeyError instead of failing gracefully.
    """
    if state.get("error"):
        return "handle_fetch_error"
    if state["current_condition"].get("kind") == "ticket_text":
        return "interpret_ticket_condition"
    return "evaluate_field_condition"


def handle_fetch_error(state: ProceduralExecutorState) -> dict:
    """Graceful terminal response when fetch_step couldn't find the current
    step — e.g. classify_intent said 'troubleshooting' but matched_procedure_id
    was wrong, hallucinated, or None. Returns a consultant-facing message
    instead of crashing or silently returning nothing."""
    return {
        "answer": "I couldn't find a matching procedure for this request. Try rephrasing, or I can search the knowledge base instead.",
        "citations": [],
    }


def evaluate_field_condition(state: ProceduralExecutorState) -> dict:
    """Deterministic evaluation — resolve_condition() handles both a single
    verification_api and a multi-call verification_chain. This IS the Joiner."""
    result = resolve_condition(state["crm_context"], state["current_condition"])
    return {"condition_result": result}


def interpret_ticket_condition(state: ProceduralExecutorState) -> dict:
    """Route ticket_text conditions to the LLM-based interpreter — the one
    condition type structured field checks can't answer. Uses the
    customer's most recent ticket."""
    tickets = state["crm_context"].get("recent_tickets", [])
    if not tickets:
        return {"condition_result": False}
    graph = build_ticket_interpreter_graph()
    result = graph.invoke({
        "ticket": tickets[0],
        "interpretation_question": state["current_condition"]["interpretation_question"],
    })
    return {"condition_result": result["relevant"]}


def advance_or_resolve(state: ProceduralExecutorState) -> Command[Literal["fetch_step", "resolve"]]:
    """Shared routing after condition_result is known, regardless of which
    evaluator computed it.

    A plain conditional edge can only pick the next node, it can't also
    update state — and looping back to fetch_step requires advancing
    current_step_id to next_step_id first, or fetch_step would just
    re-fetch the same step forever (this bit us once — see
    tests/test_procedural_executor.py).
    """
    if state["condition_result"]:
        return Command(goto="resolve")
    if state.get("next_step_id") is not None:
        return Command(update={"current_step_id": state["next_step_id"]}, goto="fetch_step")
    return Command(goto="resolve")


def resolve(state: ProceduralExecutorState) -> dict:
    """Pick the winning Action (true-branch, or the terminal false-branch
    catch-all) and format the final answer with its governing Rule citations."""
    action_id = state["action_if_true_id"] if state["condition_result"] else state["action_if_false_id"]
    if action_id is None:
        return {"error": f"Terminal step {state['current_step_id']} has no action_if_false_id"}

    driver = get_driver()
    with driver.session() as session:
        record = session.run(RESOLVE_ACTION_CYPHER, action_id=action_id).single()

    rule_ids = [r for r in record["ruleIds"] if r is not None]
    return {"answer": record["text"], "citations": [action_id, *rule_ids]}


def build_procedural_executor_graph():
    """Compile fetch_step -> route_after_fetch -> (evaluate_field_condition
    | interpret_ticket_condition | handle_fetch_error) -> advance_or_resolve -> (loop | resolve)."""
    builder = StateGraph(ProceduralExecutorState)
    builder.add_node("fetch_step", fetch_step)
    builder.add_node("evaluate_field_condition", evaluate_field_condition)
    builder.add_node("interpret_ticket_condition", interpret_ticket_condition)
    builder.add_node("handle_fetch_error", handle_fetch_error)
    builder.add_node("advance_or_resolve", advance_or_resolve)
    builder.add_node("resolve", resolve)

    builder.add_edge(START, "fetch_step")
    builder.add_conditional_edges(
        "fetch_step",
        route_after_fetch,
        ["evaluate_field_condition", "interpret_ticket_condition", "handle_fetch_error"],
    )
    builder.add_edge("evaluate_field_condition", "advance_or_resolve")
    builder.add_edge("interpret_ticket_condition", "advance_or_resolve")
    builder.add_edge("handle_fetch_error", END)
    # No static/conditional edge from advance_or_resolve — its Command
    # return value provides the dynamic routing (to "fetch_step" or "resolve").
    builder.add_edge("resolve", END)
    return builder.compile()
