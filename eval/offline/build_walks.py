"""Builds the walks suite (datasets/walks.jsonl): one troubleshooting example per procedure.

    uv run python -m eval.offline.build_walks

Each walk below is a hand-checked (procedure, customer, message) triple. The customer is chosen so:
  - the message fits their CRM record ("prepaid" goes to a prepaid customer, "service was suspended"
    to the suspended account, "plan hasn't started" to a pending plan) and their ticket, if any;
  - the 20 customers differ: payment method, account, plan and SIM status, tenure, device;
  - the walks end in different ways: step 1 finds the cause / a later step does / every step says NO.
The expected action, rules and steps are not typed in here: the oracle works them out from data/.

The 3 procedures that read the customer's latest ticket carry a human label: does that ticket
answer the condition's question? Re-running overwrites walks.jsonl, so edit this script, not the file.
"""

import json
from pathlib import Path

from eval.offline.oracle import expected_walk, procedures, ticket_conditions_checked
from mock_crm.crm_store import get_customer

OUT = Path(__file__).resolve().parent / "datasets" / "walks.jsonl"

# (procedure, customer, what the consultant types, ticket labels, the record facts the message rests on)
WALKS = [
    ("PROC-001", "CUST-1002", "customer called back, roaming still won't switch on and they fly out tomorrow",
     {}, "roaming off; open ticket: roaming still not working after we enabled it"),
    ("PROC-002", "CUST-1025", "customer is disputing the roaming charges from their trip to Italy on last month's bill, they already raised it once and were told to wait for this statement",
     {}, "open billing-dispute ticket: agreed to wait for the next statement; balance $8.75"),
    ("PROC-003", "CUST-1020", "customer says this month's bill is higher than usual and they can't work out why",
     {}, "nothing explains it: balance $42.50, last payment succeeded, account active"),
    ("PROC-004", "CUST-1035", "customer wants the $4.99 premium SMS charge on their latest bill refunded, they say it's a mistake",
     {"COND-028": False}, "balance $75, credit card; latest ticket only asked about plan status, no refund request"),
    ("PROC-005", "CUST-1010", "customer's service was suspended after their card payment failed, they want it back on today",
     {}, "the only suspended account; card payment failed; open ticket disputing the suspension"),
    ("PROC-006", "CUST-1022", "new customer just put the SIM in their Xiaomi 13 and the phone says No SIM",
     {}, "1 month tenure, Xiaomi 13, SIM not activated"),
    ("PROC-007", "CUST-1046", "customer lost their phone yesterday and needs a replacement SIM for their new one",
     {}, "SIM still active (not blocked yet), account active"),
    ("PROC-008", "CUST-1006", "customer's automatic monthly bank payment keeps failing with the same error",
     {}, "bank transfer, last payment failed; open ticket: payment error keeps appearing"),
    ("PROC-009", "CUST-1033", "customer sees a payment on their account they didn't make and thinks it's fraud",
     {}, "no fraud-risk flag, last payment succeeded"),
    ("PROC-010", "CUST-1027", "prepaid customer's credit is already down to zero, they think it ran out far too fast",
     {}, "prepaid, balance $0"),
    ("PROC-011", "CUST-1052", "customer has been with us almost six years and wants to move from Senior Saver to a plan with more data",
     {}, "Senior Saver, 70 months tenure, plan active, last payment succeeded"),
    ("PROC-012", "CUST-1028", "customer switched to Family Share 20GB last week and it still hasn't become active",
     {}, "Family Share 20GB pending, payment pending"),
    ("PROC-013", "CUST-1038", "customer wants to cancel their contract today, they're switching to another provider",
     {}, "no device instalments owed, balance $42.50, 30 months tenure"),
    ("PROC-014", "CUST-1060", "customer landed in Tokyo this morning and their OnePlus 12 shows no bars, emergency calls only",
     {}, "OnePlus 12 supports roaming bands; roaming off"),
    ("PROC-015", "CUST-1008", "customer's Motorola Edge keeps freezing and lost service since last night's software update",
     {}, "Motorola Edge supports roaming bands; SIM not activated"),
    ("PROC-016", "CUST-1017", "customer has had no signal or mobile data at home since this morning, there might be an outage in their area",
     {}, "active outage at their zip code"),
    ("PROC-017", "CUST-1044", "customer wants a credit on their bill for the days the outage in their area left them without service",
     {"COND-024": True}, "active outage at their zip code; latest ticket: still no signal after the fix"),
    ("PROC-018", "CUST-1040", "customer is locked out of their online account and can't log in anymore",
     {}, "the only closed account; no tickets"),
    ("PROC-019", "CUST-1053", "customer got an email saying their account email was changed, and they didn't do it",
     {"COND-027": False}, "latest ticket is a routine card decline, not suspicious access; last payment failed"),
    ("PROC-020", "CUST-1054", "customer reset their password yesterday but the app still won't let them log in",
     {}, "no tickets, account active, SIM not activated"),
]


def build() -> list[dict]:
    """Turn WALKS into golden examples, with the oracle filling in the expected action, rules, steps
    and exit. Stops with an error if a procedure is missing, a customer is used twice, or a walk's
    ticket labels don't match the tickets it actually reads."""
    assert sorted(p for p, *_ in WALKS) == sorted(procedures()), "one walk per procedure"
    assert len({c for _, c, *_ in WALKS}) == len(WALKS), "every walk uses a different customer"
    examples = []
    for procedure_id, customer_id, message, ticket_labels, fits in WALKS:
        walk = expected_walk(procedure_id, customer_id, ticket_labels)
        read = ticket_conditions_checked(procedure_id, walk["steps"]) if get_customer(customer_id)["recent_tickets"] else []
        assert sorted(ticket_labels) == sorted(read), f"{procedure_id}: labels {sorted(ticket_labels)}, but the walk reads {read}"
        examples.append({
            "id": f"walk-{procedure_id}",
            "inputs": {"message": message, "customer_id": customer_id},
            "outputs": {
                "path": "troubleshooting",
                "procedure_id": procedure_id,
                "action_id": walk["action_id"],
                "rule_ids": walk["rule_ids"],
                "steps": walk["steps"],
                "exit": walk["exit"],
            },
            "metadata": {"suite": "walks", "exit": walk["exit"], "ticket_labels": ticket_labels, "fits": fits},
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
