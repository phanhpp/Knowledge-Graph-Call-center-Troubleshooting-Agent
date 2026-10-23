"""Builds the routing suite (datasets/routing.jsonl): does each message go to the right place?

    uv run python -m eval.offline.build.routing

Routing is two Jev decisions (agent/supervisor.py):
  1. classify_intent picks the path: troubleshooting (a customer's case), open_ended (a general
     question, answered from the knowledge base) or explain (about an earlier answer);
  2. for troubleshooting, match_procedure picks one of the 20 procedures, or none (then the
     knowledge base answers instead).
Each example gives the right path and procedure; the checks compare them exactly, so Jev never
grades its own routing.

The examples target the hard cases, by slice:
  - procedure-pair: 7 pairs of procedures that are easy to confuse. Each procedure's when_to_use
    in data/procedures.json names its neighbour ("If ..., use PROC-014"); each pair has one
    message for each side.
  - policy-twin: the same topic as a pair member, asked as a general question ("how much does a
    replacement SIM cost?"), which must go to the knowledge base, not the procedure.
  - general-with-customer: a general question that still names a customer.
  - near-miss: a message that once went to the wrong procedure.
  - typo: misspelt messages, as typed in a hurry on a call.
  - no-procedure: a real problem that no procedure covers.
  - off-topic: nothing to do with the customer or the knowledge base.

A troubleshooting message only reaches match_procedure when it names a customer, so each one has a
customer whose CRM record fits the message (the "fits" note). Re-running overwrites routing.jsonl,
so edit this script, not the file.
"""

import json
from pathlib import Path

from eval.offline.oracle import procedures
from mock_crm.crm_store import get_customer

OUT = Path(__file__).resolve().parents[1] / "datasets" / "routing.jsonl"

TROUBLESHOOTING, GENERAL = "troubleshooting", "open_ended"

# (id, slice, pair, message, customer, path, procedure, what the example rests on)
ROUTES = [
    # --- 7 procedure pairs: one message for each side ---
    ("roaming-activate", "procedure-pair", "roaming", "customer is flying to Bali on Saturday and can't get roaming switched on",
     "CUST-1021", TROUBLESHOOTING, "PROC-001", "roaming off on the account"),
    ("roaming-no-bars", "procedure-pair", "roaming", "customer is in Paris right now and their phone shows no bars, emergency calls only",
     "CUST-1003", TROUBLESHOOTING, "PROC-014", "roaming on; already abroad, so PROC-014 not PROC-001"),
    ("bill-higher", "procedure-pair", "bill", "customer says this month's bill is way higher than normal and they don't know why",
     "CUST-1009", TROUBLESHOOTING, "PROC-003", "balance $71.40; no single line item named, so PROC-003"),
    ("bill-line-item", "procedure-pair", "bill", "customer says the $12 data add-on on their latest bill is wrong and wants it credited",
     "CUST-1030", TROUBLESHOOTING, "PROC-004", "balance $102.40; one line item contested, so PROC-004"),
    ("sim-not-detected", "procedure-pair", "sim", "customer's phone keeps saying 'invalid SIM' since this morning",
     "CUST-1059", TROUBLESHOOTING, "PROC-006", "SIM blocked; the current SIM isn't accepted, so PROC-006"),
    ("sim-replacement", "procedure-pair", "sim", "customer's SIM card snapped in half and they need a new one",
     "CUST-1050", TROUBLESHOOTING, "PROC-007", "SIM active; asks for a new SIM, so PROC-007"),
    ("outage-now", "procedure-pair", "outage", "customer has had no mobile data at home all day, and their neighbours have the same problem",
     "CUST-1051", TROUBLESHOOTING, "PROC-016", "active outage at their zip code; service down now, so PROC-016"),
    ("outage-credit", "procedure-pair", "outage", "customer lost service for three days in last week's outage and wants compensation on their bill",
     "CUST-1007", TROUBLESHOOTING, "PROC-017", "active outage at their zip code; asks for a credit, so PROC-017"),
    ("login-locked", "procedure-pair", "login", "customer locked themselves out of the app after entering the wrong password five times",
     "CUST-1048", TROUBLESHOOTING, "PROC-018", "account active; no reset yet, so PROC-018"),
    ("login-after-reset", "procedure-pair", "login", "customer reset their password this morning but the app still rejects their login",
     "CUST-1019", TROUBLESHOOTING, "PROC-020", "account active; already reset, so PROC-020"),
    ("fraud-charge", "procedure-pair", "fraud", "customer sees a $300 card payment to us they never made and thinks their card was stolen",
     "CUST-1023", TROUBLESHOOTING, "PROC-009", "fraud-risk flag on the account; a payment, so PROC-009"),
    ("account-takeover", "procedure-pair", "fraud", "customer says someone logged into their account last night and changed the contact phone number",
     "CUST-1026", TROUBLESHOOTING, "PROC-019", "account details changed, not a payment, so PROC-019"),
    ("plan-change", "procedure-pair", "plan", "customer wants to downgrade to a cheaper plan with less data",
     "CUST-1033", TROUBLESHOOTING, "PROC-011", "plan active; wants a change, so PROC-011"),
    ("plan-not-active", "procedure-pair", "plan", "customer ordered the Business Pro plan three days ago and it still isn't active",
     "CUST-1057", TROUBLESHOOTING, "PROC-012", "Business Pro pending; already ordered, so PROC-012"),
    # --- the same topics as general questions: the knowledge base answers ---
    ("sim-fee-question", "policy-twin", "sim", "how much does a replacement SIM cost, and when is the fee waived?",
     None, GENERAL, None, "KB-006 SIM Replacement Process and Fees"),
    ("roaming-timing-question", "policy-twin", "roaming", "how long does roaming usually take to start working after it's enabled?",
     None, GENERAL, None, "KB-017 Roaming Activation Requirements"),
    ("outage-credit-question", "policy-twin", "outage", "how is the bill credit worked out when a customer is hit by an outage?",
     None, GENERAL, None, "KB-014 Outage Service Credit Policy"),
    ("dispute-timeline-question", "policy-twin", "bill", "how long does a billing dispute usually take to resolve?",
     None, GENERAL, None, "KB-013 Billing Dispute Resolution Timeline"),
    ("rollover-for-customer", "general-with-customer", None, "customer is asking how data rollover works, do unused GB carry over to next month?",
     "CUST-1029", GENERAL, None, "the answer is the same for any customer; KB-002 Data Rollover Policy"),
    # --- other hard cases ---
    ("login-no-reset-yet", "near-miss", None, "customer can't log in to their online account anymore and hasn't tried resetting the password",
     "CUST-1040", TROUBLESHOOTING, "PROC-018", "went to PROC-020 once: mentioning the reset, even to deny it, pulled it there"),
    ("typo-autopay", "typo", None, "customers auto paymnt keeps gettin declined evry month",
     "CUST-1041", TROUBLESHOOTING, "PROC-008", "last card payment failed"),
    ("typo-prepaid", "typo", None, "prepaid custmer says there credit ran out way to fast this week",
     "CUST-1036", TROUBLESHOOTING, "PROC-010", "prepaid, balance $0"),
    ("voicemail", "no-procedure", None, "customer's voicemail stopped working yesterday, callers just hear a busy tone",
     "CUST-1055", TROUBLESHOOTING, None, "a real problem, but no procedure covers voicemail"),
    ("off-topic", "off-topic", None, "can you help me write an email to my team lead asking for Friday off?",
     None, GENERAL, None, "not about a customer or the knowledge base"),
]


def build() -> list[dict]:
    """Turn ROUTES into golden examples. Stops with an error if an ID is reused, a message repeats, a
    procedure or customer doesn't exist, a troubleshooting message has no customer, or a pair
    doesn't cover two different procedures."""
    ids = [r[0] for r in ROUTES]
    assert len(set(ids)) == len(ids), "an example ID is used twice"
    assert len({r[3] for r in ROUTES}) == len(ROUTES), "a message is used twice"
    pairs: dict[str, set] = {}
    examples = []
    for golden_id, slice_name, pair, message, customer_id, path, procedure_id, fits in ROUTES:
        if procedure_id is not None:
            assert procedure_id in procedures(), f"{golden_id}: unknown procedure {procedure_id}"
        if customer_id is not None:
            get_customer(customer_id)  # raises if the customer doesn't exist
        if path == TROUBLESHOOTING:
            assert customer_id, f"{golden_id}: troubleshooting needs a customer, or it never reaches match_procedure"
        if slice_name == "procedure-pair":
            pairs.setdefault(pair, set()).add(procedure_id)
        examples.append({
            "id": f"route-{golden_id}",
            "inputs": {"message": message, "customer_id": customer_id},
            "outputs": {"path": path, "procedure_id": procedure_id},
            "metadata": {"suite": "routing", "slice": slice_name, "pair": pair, "fits": fits},
        })
    for pair, procedure_ids in pairs.items():
        assert len(procedure_ids) == 2, f"pair {pair} must cover two different procedures, has {procedure_ids}"
    return examples


if __name__ == "__main__":
    examples = build()
    OUT.write_text("".join(json.dumps(e) + "\n" for e in examples))
    slices: dict[str, int] = {}
    for e in examples:
        slices[e["metadata"]["slice"]] = slices.get(e["metadata"]["slice"], 0) + 1
    print(f"wrote {len(examples)} routing examples to {OUT.relative_to(Path.cwd())}; slices: {slices}")
