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

State each node needs (r = reads, w = writes):

    fetch_step                  r: matched_procedure_id, current_step_id (unset means step 1)
                                w: current_step_id, current_condition, action_if_true_id,
                                   action_if_false_id, next_step_id
                                   (or only `error` if the procedure has no such step)
                                   On the first step it also empties step_trace, so the trace
                                   always describes the latest walk.
    route_after_fetch           r: error, current_condition
    handle_fetch_error          w: messages (the reply)
    evaluate_field_condition    r: current_condition, crm_context, current_step_id,
                                   matched_procedure_id, step_trace
    interpret_ticket_condition  w: condition_result, step_trace
    advance_or_resolve          r: condition_result, next_step_id
                                w: current_step_id (only when it loops back to fetch_step)
    resolve                     r: condition_result, action_if_true_id, action_if_false_id,
                                   step_trace, current_step_id (only for its error message)
                                w: messages (the reply), step_trace (the action given is added to
                                   the last entry); on the error path, a fallback reply and `error`

step_trace: one entry per step checked, {procedure_id, step_id, condition_id, description, result,
evidence}, with `action_id` added to the last one. It records what was true at that moment, so a
follow-up ("why did you say that?") can be answered without re-checking the customer's data.

Redundant:
    condition_result  the same as step_trace[-1]["result"]. Kept as the direct hand-off to
                      advance_or_resolve and resolve, which is simpler than reading the list.
Every other field is read by a later node.
"""

from typing import Literal, Optional

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from agent.state import ProceduralExecutorState, reply
from agent.ticket_interpreter import build_ticket_interpreter_graph
from config import get_driver
from mock_crm.condition_evaluator import get_condition, get_nested_field, resolve_condition

FETCH_STEP_CYPHER = """
MATCH (s:Step {procedureId: $procedure_id, stepId: $step_id})
MATCH (s)-[:HAS_CONDITION]->(c:Condition)
OPTIONAL MATCH (s)-[:NEXT_STEP]->(nxt:Step)
OPTIONAL MATCH (s)-[ha:HAS_ACTION]->(a:Action)
RETURN c.conditionId AS conditionId,
       nxt.stepId AS nextStepId,
       collect({outcome: ha.outcome, actionId: a.actionId}) AS actions
"""

NO_ANSWER = "I couldn't produce an answer for this request."

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
    next_step_id by advance_or_resolve's Command. That first entry also
    empties step_trace: the Supervisor keeps the previous walk's trace in state
    between turns, and a new walk (even one that fails right here) must not
    inherit it.
    """
    first_entry = state.get("current_step_id") is None
    fresh_trace = {"step_trace": []} if first_entry else {}
    step_id = state.get("current_step_id") or 1
    driver = get_driver()
    with driver.session() as session:
        record = session.run(
            FETCH_STEP_CYPHER,
            procedure_id=state["matched_procedure_id"],
            step_id=step_id,
        ).single()

    if record is None:
        return {**fresh_trace, "error": f"Step {step_id} not found for procedure {state['matched_procedure_id']}"}

    action_if_true_id = None
    action_if_false_id = None
    for a in record["actions"]:
        if a["outcome"] == "true":
            action_if_true_id = a["actionId"]
        elif a["outcome"] == "false":
            action_if_false_id = a["actionId"]

    return {
        **fresh_trace,
        "current_step_id": step_id,
        "current_condition": get_condition(record["conditionId"]),
        "action_if_true_id": action_if_true_id,
        "action_if_false_id": action_if_false_id,
        "next_step_id": record["nextStepId"],
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
    return reply(
        "I couldn't find a matching procedure for this request. Try rephrasing, or I can search the knowledge base instead."
    )


def _with_trace(state: ProceduralExecutorState, result: bool, evidence: Optional[str]) -> dict:
    """The state update for a checked step: the result, plus one more entry in `step_trace`.

    `step_trace` is what a follow-up "why did you say that?" is answered from. `evidence` is the
    fact the check used (for example "roaming_flag = True"), or None when there is no single value.
    """
    condition = state["current_condition"]
    entry = {
        "procedure_id": state["matched_procedure_id"],
        "step_id": state["current_step_id"],
        "condition_id": condition["condition_id"],
        "description": condition["description"],
        "result": result,
        "evidence": evidence,
    }
    return {"condition_result": result, "step_trace": [*state.get("step_trace", []), entry]}


def _field_evidence(crm_context: dict, condition: dict) -> Optional[str]:
    """The CRM value a plain field check looked at, like "roaming_flag = True".

    None for a chain condition, which reads several values.
    """
    if "verification_chain" in condition:
        return None
    return f"{condition['field']} = {get_nested_field(crm_context, condition['field'])}"


def evaluate_field_condition(state: ProceduralExecutorState) -> dict:
    """Deterministic evaluation — resolve_condition() handles both a single
    verification_api and a multi-call verification_chain. This IS the Joiner."""
    condition = state["current_condition"]
    result = resolve_condition(state["crm_context"], condition)
    return _with_trace(state, result, _field_evidence(state["crm_context"], condition))


def interpret_ticket_condition(state: ProceduralExecutorState) -> dict:
    """Route ticket_text conditions to the LLM-based interpreter — the one
    condition type structured field checks can't answer. Uses the
    customer's most recent ticket."""
    tickets = state["crm_context"].get("recent_tickets", [])
    if not tickets:
        return _with_trace(state, False, "no tickets on file")
    graph = build_ticket_interpreter_graph()
    result = graph.invoke({
        "ticket": tickets[0],
        "interpretation_question": state["current_condition"]["interpretation_question"],
    })
    return _with_trace(state, result["relevant"], f"latest ticket: {tickets[0]['description']}")


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
    catch-all) and return it as the reply, citing the action and its governing Rules.

    Also records the action given on the last step_trace entry, so the trace says what happened
    from the procedure to the action.
    """
    action_id = state["action_if_true_id"] if state["condition_result"] else state["action_if_false_id"]
    if action_id is None:
        return {**reply(NO_ANSWER), "error": f"Terminal step {state['current_step_id']} has no action_if_false_id"}

    driver = get_driver()
    with driver.session() as session:
        record = session.run(RESOLVE_ACTION_CYPHER, action_id=action_id).single()

    rule_ids = [r for r in record["ruleIds"] if r is not None]
    trace = state.get("step_trace", [])
    if trace:
        trace = [*trace[:-1], {**trace[-1], "action_id": action_id}]
    return {**reply(record["text"], [action_id, *rule_ids]), "step_trace": trace}


def build_procedural_executor_graph():
    """Compile fetch_step -> route_after_fetch -> (evaluate_field_condition
    | interpret_ticket_condition | handle_fetch_error) -> advance_or_resolve -> (loop | resolve).

    checkpointer=False: a walk needs no pause/resume and no memory of its own, so even when the
    Supervisor saves its state, the walk writes no checkpoints of its own.
    """
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
    return builder.compile(checkpointer=False)
