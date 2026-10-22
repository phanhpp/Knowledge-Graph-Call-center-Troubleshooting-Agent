"""Drafts the walks suite (eval/offline/datasets/walks.jsonl): one example per procedure, for review.

    uv run python -m eval.offline.build_walks

For each procedure it computes, with the oracle, how the walk ends for every customer, then picks one
customer so the suite's exits vary (step 1 finds the cause / a later step does / every step says NO).
For the 3 ticket-reading procedures it picks a customer whose ticket is actually read, so the suite
exercises the one model-based check; only unambiguous tickets are labelled (TICKET_LABELS below).

The result is a DRAFT: review it (the messages, the chosen customers, the ticket labels) before it
becomes the answer key. Re-running overwrites walks.jsonl, so edit this script, not the file.
"""

import json
from pathlib import Path

from eval.offline.oracle import expected_walk, procedures, ticket_conditions_checked
from etl.io_utils import load_json

OUT = Path(__file__).resolve().parent / "datasets" / "walks.jsonl"

# What a consultant would type for each procedure's typical case.
MESSAGES = {
    "PROC-001": "customer is trying to turn on roaming before their trip and it won't activate",
    "PROC-002": "customer is disputing the roaming charges on last month's bill from their trip to Spain",
    "PROC-003": "customer says this month's bill is much higher than usual and they don't recognise some charges",
    "PROC-004": "customer wants the premium SMS line item on their bill removed and refunded",
    "PROC-005": "customer's service was cut off after a missed payment",
    "PROC-006": "customer's phone says No SIM even though the SIM card is inserted",
    "PROC-007": "customer lost their phone and needs a replacement SIM card",
    "PROC-008": "customer's automatic monthly payment keeps getting declined",
    "PROC-009": "customer sees a charge on their account they never made and thinks it's fraud",
    "PROC-010": "prepaid customer's credit ran out much faster than expected",
    "PROC-011": "customer wants to upgrade to a bigger data plan",
    "PROC-012": "customer switched to a new plan last week and it still hasn't started",
    "PROC-013": "customer wants to cancel their contract today",
    "PROC-014": "customer is abroad and their phone shows no bars, emergency calls only",
    "PROC-015": "customer's phone keeps crashing since the latest software update",
    "PROC-016": "customer has no signal or mobile data at home, maybe an outage in their area",
    "PROC-017": "customer is asking for a credit on their bill because of last week's outage",
    "PROC-018": "customer is locked out of their online account after too many failed logins",
    "PROC-019": "customer thinks someone else logged into their account and changed their email",
    "PROC-020": "customer reset their password but the app still won't let them log in",
}

# Human labels for ticket conditions: does the customer's latest ticket answer the condition's
# question? Only clear-cut tickets; ambiguous ones ("not sure if this was ever fully resolved") are
# left out so the answer key never rests on a judgement call.
#   COND-024: is the issue still happening?   COND-027: suspected unauthorised access?
#   COND-028: specifically asking for a refund or billing credit?
TICKET_LABELS: dict[str, dict[str, bool]] = {
    "CUST-1002": {"COND-024": True, "COND-027": False, "COND-028": False},   # "roaming still isn't working"
    "CUST-1005": {"COND-024": False, "COND-027": False, "COND-028": False},  # "device works fine now"
    "CUST-1006": {"COND-024": True, "COND-027": False, "COND-028": False},   # "payment error keeps appearing"
    "CUST-1032": {"COND-024": True, "COND-027": False, "COND-028": False},   # "replacement SIM still won't unlock"
    "CUST-1035": {"COND-024": False, "COND-027": False, "COND-028": False},  # "just wanted to know their plan status"
    "CUST-1044": {"COND-024": True, "COND-027": False, "COND-028": False},   # "still shows no signal"
    "CUST-1053": {"COND-024": True, "COND-027": False, "COND-028": False},   # "declined again this morning"
}

EXIT_ORDER = ["first-yes", "later-yes", "fallback"]


def candidates(procedure_id: str) -> list[dict]:
    """Every customer's walk for this procedure, skipping tickets without a clear label."""
    walks = []
    for customer in load_json("crm_records.json"):
        customer_id = customer["customer_id"]
        try:
            walk = expected_walk(procedure_id, customer_id, TICKET_LABELS.get(customer_id))
        except ValueError:
            continue  # a ticket the walk would read, with no clear label
        walk["customer_id"] = customer_id
        walk["tickets_read"] = [c for c in ticket_conditions_checked(procedure_id, walk["steps"]) if customer["recent_tickets"]]
        walks.append(walk)
    return walks


def pick(procedure_id: str, wanted_exit: str) -> dict:
    """One customer for this procedure: the wanted exit if possible, a ticket read if it's a ticket procedure."""
    walks = candidates(procedure_id)
    has_ticket_step = any(c["tickets_read"] for c in walks)
    if has_ticket_step:
        walks = [w for w in walks if w["tickets_read"]] or walks
        # Prefer a ticket that answers YES when there is one, so the suite checks both answers.
        says_yes = [w for w in walks if any(TICKET_LABELS[w["customer_id"]][c] for c in w["tickets_read"])]
        if says_yes:
            return says_yes[0]
    for exit_kind in [wanted_exit] + [e for e in EXIT_ORDER if e != wanted_exit]:
        matching = [w for w in walks if w["exit"] == exit_kind]
        if matching:
            return matching[0]
    raise ValueError(f"no usable customer for {procedure_id}")


def build() -> list[dict]:
    examples = []
    for n, procedure_id in enumerate(sorted(procedures())):
        walk = pick(procedure_id, EXIT_ORDER[n % len(EXIT_ORDER)])
        labels = TICKET_LABELS.get(walk["customer_id"], {})
        examples.append({
            "id": f"walk-{procedure_id}",
            "inputs": {"message": MESSAGES[procedure_id], "customer_id": walk["customer_id"]},
            "outputs": {
                "path": "troubleshooting",
                "procedure_id": procedure_id,
                "action_id": walk["action_id"],
                "rule_ids": walk["rule_ids"],
                "steps": walk["steps"],
                "exit": walk["exit"],
            },
            "metadata": {
                "suite": "walks",
                "exit": walk["exit"],
                "ticket_labels": {c: labels[c] for c in walk["tickets_read"]},
            },
        })
    return examples


if __name__ == "__main__":
    examples = build()
    OUT.write_text("".join(json.dumps(e) + "\n" for e in examples))
    exits = {}
    for e in examples:
        exits[e["outputs"]["exit"]] = exits.get(e["outputs"]["exit"], 0) + 1
    print(f"wrote {len(examples)} walks to {OUT.relative_to(Path.cwd())}; exits: {exits}")
    for e in examples:
        tickets = e["metadata"]["ticket_labels"]
        print(f"  {e['id']}: {e['inputs']['customer_id']} -> {e['outputs']['action_id']} {e['outputs']['rule_ids']} ({e['outputs']['exit']}){'  ticket ' + str(tickets) if tickets else ''}")
