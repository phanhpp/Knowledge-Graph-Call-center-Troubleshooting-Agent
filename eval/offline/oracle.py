"""The answer key for the walks suite: for a procedure and a customer, what should the walk do?

It works this out from data/ alone, without running the agent:

    start at step 1. If the step's condition is YES, the answer is that step's action: stop.
    If it's NO, go to the next step. If the last step is NO too, the answer is that step's
    fallback action (action_if_false_id).

Where each fact comes from:
  - A normal condition (e.g. "roaming is off") is checked in the mock CRM (mock_crm/), the stand-in
    for the real CRM that the agent also calls. The CRM is the source of truth for facts, and the walk
    logic here is written separately from the agent's, so this is not the agent grading itself.
  - A ticket condition (e.g. "does the ticket say the issue is still happening?") needs a person to
    read free text, so its answer is a human label stored with the example. A customer with no ticket
    on file gets NO, as in the agent.
  - The rules for an action are those in data/rules.json that list it in applies_to_action_ids (the
    same data the graph's GOVERNED_BY edges are built from).
"""

from functools import lru_cache

from etl.io_utils import load_json
from mock_crm.condition_evaluator import get_condition, resolve_condition
from mock_crm.crm_store import get_customer


@lru_cache(maxsize=1)
def procedures() -> dict[str, dict]:
    """Every procedure in data/procedures.json, by ID (e.g. "PROC-001")."""
    return {p["procedure_id"]: p for p in load_json("procedures.json")}


@lru_cache(maxsize=1)
def rules_for_action() -> dict[str, list[str]]:
    """For each action, the IDs of the rules that govern it, e.g. {"ACT-016": ["R001", "R003", "R006"]}."""
    by_action: dict[str, list[str]] = {}
    for rule in load_json("rules.json"):
        for action_id in rule["applies_to_action_ids"]:
            by_action.setdefault(action_id, []).append(rule["rule_id"])
    return {action: sorted(ids) for action, ids in by_action.items()}


def is_ticket_condition(condition_id: str) -> bool:
    """True if answering this condition means reading a ticket's free text (kind "ticket_text")."""
    return get_condition(condition_id).get("kind") == "ticket_text"


def condition_value(customer: dict, condition_id: str, ticket_labels: dict[str, bool]) -> bool:
    """Is this condition YES for this customer? A normal condition is checked in the mock CRM; a
    ticket condition uses its human label (and is NO when the customer has no ticket)."""
    condition = get_condition(condition_id)
    if condition.get("kind") != "ticket_text":
        return resolve_condition(customer, condition)
    if not customer.get("recent_tickets"):
        return False  # as in the agent: no ticket on file answers NO, without a model
    if condition_id not in ticket_labels:
        raise ValueError(f"{customer['customer_id']} has a ticket, so {condition_id} needs a human label")
    return ticket_labels[condition_id]


def expected_walk(procedure_id: str, customer_id: str, ticket_labels: dict[str, bool] | None = None) -> dict:
    """What the walk should do for this procedure and customer.

    ticket_labels holds the human answer for each ticket condition the walk reaches,
    e.g. {"COND-024": True}. Returns:
        action_id: the action the walk should end at
        rule_ids:  the rules that govern that action
        steps:     each step checked, in order, with its answer, e.g. [[1, False], [2, True]]
        exit:      "first-yes" (step 1 found the cause), "later-yes" (a later step did) or
                   "fallback" (every step said NO)
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
    """The ticket conditions this walk reached: the ones that need a human label."""
    by_id = {step["step_id"]: step for step in procedures()[procedure_id]["steps"]}
    return [by_id[step_id]["condition_id"] for step_id, _ in steps if is_ticket_condition(by_id[step_id]["condition_id"])]
