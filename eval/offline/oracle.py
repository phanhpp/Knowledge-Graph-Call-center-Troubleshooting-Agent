"""The expected outcome of a troubleshooting walk, computed from data/ alone: the answer key for the
walks suite.

Independent of the agent's walk code (agent/procedural_executor.py, Neo4j): it re-implements only
the traversal rule, straight from data/procedures.json:

    start at step 1; a step's condition YES -> that step's action, stop;
    NO -> the step's next_step; NO on the last step -> its action_if_false_id.

The condition values come from the mock CRM (mock_crm/), the stand-in for the real CRM APIs: it is
the source of truth for the customer's facts, so reusing it is not checking the agent against itself.
A ticket condition is the exception: whether a free-text ticket answers its question is judgement,
so its value is a human label given with the example (no ticket on file is NO, as in the agent).

The rules are those whose applies_to_action_ids list the action (data/rules.json), which is what the
graph's GOVERNED_BY edges are loaded from.
"""

from functools import lru_cache

from etl.io_utils import load_json
from mock_crm.condition_evaluator import get_condition, resolve_condition
from mock_crm.crm_store import get_customer


@lru_cache(maxsize=1)
def procedures() -> dict[str, dict]:
    """data/procedures.json by procedure_id."""
    return {p["procedure_id"]: p for p in load_json("procedures.json")}


@lru_cache(maxsize=1)
def rules_for_action() -> dict[str, list[str]]:
    """action_id -> the IDs of the rules governing it, sorted."""
    by_action: dict[str, list[str]] = {}
    for rule in load_json("rules.json"):
        for action_id in rule["applies_to_action_ids"]:
            by_action.setdefault(action_id, []).append(rule["rule_id"])
    return {action: sorted(ids) for action, ids in by_action.items()}


def is_ticket_condition(condition_id: str) -> bool:
    return get_condition(condition_id).get("kind") == "ticket_text"


def condition_value(customer: dict, condition_id: str, ticket_labels: dict[str, bool]) -> bool:
    """One condition for one customer: from the mock CRM, or a ticket condition's human label."""
    condition = get_condition(condition_id)
    if condition.get("kind") != "ticket_text":
        return resolve_condition(customer, condition)
    if not customer.get("recent_tickets"):
        return False  # as in the agent: no ticket on file answers NO, without a model
    if condition_id not in ticket_labels:
        raise ValueError(f"{customer['customer_id']} has a ticket, so {condition_id} needs a human label")
    return ticket_labels[condition_id]


def expected_walk(procedure_id: str, customer_id: str, ticket_labels: dict[str, bool] | None = None) -> dict:
    """The walk's expected result for this procedure and customer.

    Returns {"procedure_id", "action_id", "rule_ids", "steps": [[step_id, result], ...], "exit"},
    where exit is "first-yes" (step 1 found the cause), "later-yes" or "fallback" (every step NO).
    """
    procedure = procedures()[procedure_id]
    customer = get_customer(customer_id)
    steps = {step["step_id"]: step for step in procedure["steps"]}
    labels = ticket_labels or {}

    checked: list[list] = []
    step = steps[1]
    while True:
        result = condition_value(customer, step["condition_id"], labels)
        checked.append([step["step_id"], result])
        if result:
            action_id = step["action_if_true_id"]
            break
        if step.get("next_step") is None:
            action_id = step["action_if_false_id"]
            break
        step = steps[step["next_step"]]

    if not checked[-1][1]:
        exit_kind = "fallback"
    else:
        exit_kind = "first-yes" if len(checked) == 1 else "later-yes"
    return {
        "procedure_id": procedure_id,
        "action_id": action_id,
        "rule_ids": rules_for_action().get(action_id, []),
        "steps": checked,
        "exit": exit_kind,
    }


def ticket_conditions_checked(procedure_id: str, steps: list[list]) -> list[str]:
    """The ticket conditions among the steps a walk checked."""
    by_id = {step["step_id"]: step for step in procedures()[procedure_id]["steps"]}
    return [by_id[step_id]["condition_id"] for step_id, _ in steps if is_ticket_condition(by_id[step_id]["condition_id"])]
