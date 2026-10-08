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
    route_after_fetch           r: error, current_condition
    handle_fetch_error          r: error, crm_context
                                w: messages (the reply), troubleshooting_results (None for this customer)
    evaluate_field_condition    r: current_condition, crm_context, current_step_id,
                                   matched_procedure_id, step_trace
    interpret_ticket_condition  w: condition_result, step_trace
    advance_or_resolve          r: condition_result, next_step_id
                                w: current_step_id (only when it loops back to fetch_step)
    resolve                     r: condition_result, action_if_true_id, action_if_false_id,
                                   step_trace, crm_context, current_step_id
                                w: messages (the reply), troubleshooting_results (this customer's
                                   result); on the error path, a fallback reply, `error`, and None
                                   as this customer's result

step_trace: one entry per step checked, {procedure_id, step_id, condition_id, description, result,
evidence}. It is private to the walk (not in the Supervisor's state), so it starts empty on every
walk and is never saved. resolve turns it into the reply's checklist and into the concise
troubleshooting_results entry that a follow-up ("why did you say that?") is answered from.

Redundant:
    condition_result  the same as step_trace[-1]["result"]. Kept as the direct hand-off to
                      advance_or_resolve and resolve, which is simpler than reading the list.
Every other field is read by a later node.
"""

import logging
from functools import lru_cache
from typing import Literal, Optional

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from agent.state import ProceduralExecutorState, reply
from agent.ticket_interpreter import build_ticket_interpreter_graph
from config import get_driver
from etl.io_utils import load_json
from mock_crm.condition_evaluator import get_condition, get_nested_field, resolve_condition

# INFO: the walk's start and the action it ends with. DEBUG: every step checked, with the value it
# used, e.g. "PROC-001 step 1: Roaming is disabled on the account -> NO (roaming_flag = True)".
# The level is set by LOG_LEVEL (see config.setup_logging).
logger = logging.getLogger(__name__)

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


@lru_cache(maxsize=1)
def _procedure_names() -> dict[str, str]:
    """procedure_id -> procedure_name, from procedures.json (lru_cache: read once, then reused)."""
    return {p["procedure_id"]: p["procedure_name"] for p in load_json("procedures.json")}


def procedure_name(procedure_id: str) -> str:
    """The name of a procedure, or 'unknown' if the id is not in procedures.json."""
    return _procedure_names().get(procedure_id, "unknown")


def fetch_step(state: ProceduralExecutorState) -> dict:
    """Load the current Step's Condition (full definition, from
    conditions.json via get_condition — Neo4j only stores flat properties,
    not a nested verification_chain), candidate Actions, and next_step.

    current_step_id defaults to 1 on the loop's first entry (nothing has set
    it yet); every subsequent call arrives with it already advanced to
    next_step_id by advance_or_resolve's Command.
    """
    first_entry = state.get("current_step_id") is None
    step_id = state.get("current_step_id") or 1
    driver = get_driver()
    with driver.session() as session:
        record = session.run(
            FETCH_STEP_CYPHER,
            procedure_id=state["matched_procedure_id"],
            step_id=step_id,
        ).single()

    if record is None:
        logger.warning("%s step %s: not found in Neo4j", state["matched_procedure_id"], step_id)
        return {"error": f"Step {step_id} not found for procedure {state['matched_procedure_id']}"}
    if first_entry:
        logger.info("Walking %s \"%s\"", state["matched_procedure_id"], procedure_name(state["matched_procedure_id"]))

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
    step — e.g. matched_procedure_id names a procedure that is not in the
    graph (match_procedure only returns ids from procedures.json, so this
    means the graph and the JSON have drifted). Returns a consultant-facing message
    instead of crashing or silently returning nothing."""
    logger.warning("No walk: %s", state.get("error"))
    return {
        **reply("I couldn't find a matching procedure for this request. Try rephrasing, or I can search the knowledge base instead."),
        **_no_result(state),
    }


def _no_result(state: ProceduralExecutorState) -> dict:
    """Mark this customer as having no current result, so a "why?" can't explain an older walk."""
    return {"troubleshooting_results": {state["crm_context"]["customer_id"]: None}}


def _with_trace(state: ProceduralExecutorState, result: bool, evidence: Optional[str]) -> dict:
    """The state update for a checked step: the result, plus one more entry in `step_trace`.

    resolve turns `step_trace` into the reply's checklist and the saved result. `evidence` is the
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
    logger.debug(
        "%s step %s: %s -> %s%s",
        entry["procedure_id"], entry["step_id"], entry["description"],
        "YES" if result else "NO", f" ({evidence})" if evidence else "",
    )
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
        logger.debug("  -> no, go on to step %s", state["next_step_id"])
        return Command(update={"current_step_id": state["next_step_id"]}, goto="fetch_step")
    return Command(goto="resolve")


def _check_line(entry: dict) -> str:
    """One step as a line of the checklist: "<what was checked>: yes/no (<the value it used>)"."""
    evidence = f" ({entry['evidence']})" if entry.get("evidence") else ""
    return f"{entry['description']}: {'yes' if entry['result'] else 'no'}{evidence}"


def reply_text(action_text: str, trace: list[dict]) -> str:
    """The reply the consultant reads: the action first, then why, then every step checked.

    Plain code, no LLM: the action is policy text from the graph and must reach the consultant
    word for word. The explanation comes from `trace`, which records what was actually checked.

        Escalate to Tier 2 - unresolved by standard checks

        Why: none of the 3 checks found the cause, so this is the procedure's fallback action.
        Checked (PROC-001 "Customer cannot activate roaming"):
        1. Roaming is disabled on the account: no (roaming_flag = True)
        2. ...
    """
    if not trace:
        return action_text
    last = trace[-1]
    if last["result"]:
        why = f"step {last['step_id']} found the cause, so the walk stopped there."
    else:
        why = f"none of the {len(trace)} checks found the cause, so this is the procedure's fallback action."
    procedure_id = last["procedure_id"]
    lines = [action_text, "", f"Why: {why}", f"Checked ({procedure_id} \"{procedure_name(procedure_id)}\"):"]
    lines += [f"{entry['step_id']}. {_check_line(entry)}" for entry in trace]
    return "\n".join(lines)


def resolve(state: ProceduralExecutorState) -> dict:
    """Pick the winning Action (true-branch, or the terminal false-branch catch-all) and reply with
    it, the reason, and the steps checked (see reply_text), citing the action and its governing Rules.

    Also saves this customer's result in troubleshooting_results: the concise version of the walk
    (procedure, action, rules, one line per step) that explain_answer reads for a follow-up.
    """
    action_id = state["action_if_true_id"] if state["condition_result"] else state["action_if_false_id"]
    if action_id is None:
        logger.warning("Step %s has no action for this result", state["current_step_id"])
        return {
            **reply(NO_ANSWER),
            **_no_result(state),
            "error": f"Terminal step {state['current_step_id']} has no action_if_false_id",
        }

    driver = get_driver()
    with driver.session() as session:
        record = session.run(RESOLVE_ACTION_CYPHER, action_id=action_id).single()

    rule_ids = [r for r in record["ruleIds"] if r is not None]
    trace = state.get("step_trace", [])
    result = {
        "procedure_id": state["matched_procedure_id"],
        "action_id": action_id,
        "rule_ids": rule_ids,
        "checks": [_check_line(entry) for entry in trace],
    }
    logger.info(
        "%s ended at step %s (%s): %s \"%s\"%s",
        state["matched_procedure_id"], state["current_step_id"],
        "yes, cause found" if state["condition_result"] else "no more steps, fallback",
        action_id, record["text"], f" (rules: {', '.join(rule_ids)})" if rule_ids else "",
    )
    return {
        **reply(reply_text(record["text"], trace), [action_id, *rule_ids]),
        "troubleshooting_results": {state["crm_context"]["customer_id"]: result},
    }


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
