"""Builds the conversations suite (datasets/conversations.jsonl): several turns in one thread.

    uv run python -m eval.offline.build.conversations

Each conversation runs on one thread, so later turns see the earlier ones, as in Slack or the web
app. For every turn the example gives the expected path, the procedure (for troubleshooting) and the
current customer after it. Explanations, and a knowledge-base follow-up, also give 2–3 key facts
the reply should state, taken from what the earlier reply showed (the walk's checklist, which comes
from the customer's CRM record) or from a cited rule's wording (data/rules.json).

  walk-why             a walk, then "why?"
  walk-rule            a walk citing a rule, then "what does that rule say?"
  switch-customers     customer A, then B, then back to A: "why did you tell me to escalate?"
                       The explanation must stay on A, not borrow B's facts
  kb-why-cited         a knowledge-base answer, then "why did you cite KB-006?"
  kb-follow-up         a knowledge-base answer, then a follow-up question on the same topic
  one-customer         three turns on one customer: a walk, "why?", then a new problem without
                       naming the customer again (the current customer must carry over)
  short-follow-ups     a walk, then "how come?" (only clear next to the answer before it), then
                       "hmm ok so" (unclear: asked to rephrase)
  kb-depends-on-earlier  a fee question, then "and after the first year?": rewritten to stand on its
                       own before the knowledge base is searched
  walk-same-problem    a roaming walk, then "same problem for CUST-1002": rewritten, so the roaming
                       procedure is matched for the new customer

Re-running overwrites conversations.jsonl, so edit this script, not the file.
"""

import json
from pathlib import Path

from eval.offline.oracle import expected_walk, procedures
from mock_crm.crm_store import get_customer

OUT = Path(__file__).resolve().parents[1] / "datasets" / "conversations.jsonl"


def walk(text: str, customer_id: str, procedure_id: str) -> tuple[dict, dict]:
    """A troubleshooting turn: the input, and what's expected after it."""
    return ({"text": text, "customer_id": customer_id},
            {"path": "troubleshooting", "procedure_id": procedure_id, "customer_after": customer_id})


def explain(text: str, customer_after: str | None, facts: list[str], customer_id: str | None = None,
            about_customer: str | None = None) -> tuple[dict, dict]:
    """An explanation turn: no procedure, and the customer stays as it was (or becomes the named one)."""
    expected = {"path": "explain", "procedure_id": None, "customer_after": customer_after, "facts": facts}
    if about_customer:
        expected["about_customer"] = about_customer
    return {"text": text, "customer_id": customer_id}, expected


def general(text: str, facts: list[str] | None = None) -> tuple[dict, dict]:
    """A knowledge-base turn with no customer."""
    expected = {"path": "open_ended", "procedure_id": None, "customer_after": None}
    if facts:
        expected["facts"] = facts
    return {"text": text, "customer_id": None}, expected


CONVERSATIONS = {
    # CUST-1001: roaming on, plan active, device compatible -> PROC-001's 3 checks all no -> Tier 2 (ACT-015)
    "walk-why": [
        walk("customer cannot activate roaming", "CUST-1001", "PROC-001"),
        explain("why?", "CUST-1001", [
            "none of the checks found the cause, so the procedure's fallback action applies",
            "roaming is already enabled on the account",
            "the plan is active and the device supports roaming",
        ]),
    ],
    # CUST-1032: SIM blocked -> PROC-007 step 1 yes -> replacement SIM (ACT-018), governed by R004
    "walk-rule": [
        walk("customer needs a replacement SIM card", "CUST-1032", "PROC-007"),
        explain("what does that rule say?", "CUST-1032", [
            "the replacement fee is waived only for the first replacement in 12 months",
            "a second replacement within 12 months costs the standard fee unless a supervisor approves a waiver",
        ]),
    ],
    # CUST-1001 escalates (all no); CUST-1002 has roaming off (step 1 yes: enable roaming)
    "switch-customers": [
        walk("customer cannot activate roaming", "CUST-1001", "PROC-001"),
        walk("customer cannot activate roaming", "CUST-1002", "PROC-001"),
        explain("why did you tell me to escalate?", "CUST-1001", [
            "roaming is already enabled for this customer",
            "none of the checks found the cause",
        ], customer_id="CUST-1001", about_customer="CUST-1001"),
    ],
    "kb-why-cited": [
        general("When is the SIM replacement fee waived?"),
        explain("why did you cite KB-006?", None, [
            "KB-006 is the article about SIM replacement and its fees",
            "the answer about when the fee is waived comes from it",
        ]),
    ],
    "kb-follow-up": [
        general("How much service credit does a customer get for an outage?"),
        general("And what if network operations hasn't confirmed the outage yet?", facts=[
            "the consultant should escalate to network operations to confirm the outage",
            "the credit should not be promised or denied before the outage is confirmed",
        ]),
    ],
    # CUST-1044: ticket says still no signal -> PROC-017 step 1 yes (ACT-006); device not roaming
    # compatible -> PROC-014 step 1 yes (ACT-027)
    "one-customer": [
        walk("customer wants a credit on their bill for the outage", "CUST-1044", "PROC-017"),
        explain("why?", "CUST-1044", [
            "the customer's latest ticket says the problem is still happening",
        ]),
        ({"text": "they're abroad now and the phone shows no bars at all", "customer_id": None},
         {"path": "troubleshooting", "procedure_id": "PROC-014", "customer_after": "CUST-1044"}),
    ],
    # CUST-1002: roaming off -> PROC-001 step 1 yes -> enable roaming (ACT-001)
    "short-follow-ups": [
        walk("customer can't turn roaming on before their trip", "CUST-1002", "PROC-001"),
        explain("how come?", "CUST-1002", [
            "roaming is switched off on the customer's account",
        ]),
        ({"text": "hmm ok so", "customer_id": None},
         {"path": "unclear", "procedure_id": None, "customer_after": "CUST-1002"}),
    ],
    # KB-005: the fee decreases each month and reaches zero only at the end of the 24-month term
    "kb-depends-on-earlier": [
        general("Is there a fee to cancel a contract in the first year?"),
        general("and after the first year?", facts=[
            "cancelling after the first year can still carry an early termination fee",
            "the fee keeps decreasing each month and reaches zero only at the end of the 24-month term",
        ]),
    ],
    # CUST-1001 escalates (all no); CUST-1002 has roaming off (step 1 yes: enable roaming). The caller
    # stamps CUST-1002 on the message, as the web app and the Slack bot do with an ID in the text.
    "walk-same-problem": [
        walk("customer cannot activate roaming", "CUST-1001", "PROC-001"),
        walk("same problem for CUST-1002", "CUST-1002", "PROC-001"),
    ],
}

# The walks the facts above describe, checked against the data: if data/ changes, rebuild and re-read.
FACT_SOURCES = [
    ("PROC-001", "CUST-1001", None, [[1, False], [2, False], [3, False]]),
    ("PROC-001", "CUST-1002", None, [[1, True]]),
    ("PROC-007", "CUST-1032", None, [[1, True]]),
    ("PROC-017", "CUST-1044", {"COND-024": True}, [[1, True]]),
    ("PROC-014", "CUST-1044", None, [[1, True]]),
]


def build() -> list[dict]:
    """Turn CONVERSATIONS into golden examples. Stops with an error if a procedure or customer doesn't
    exist, or a walk the facts rely on no longer ends the way they say."""
    for procedure_id, customer_id, labels, steps in FACT_SOURCES:
        assert expected_walk(procedure_id, customer_id, labels)["steps"] == steps, f"{procedure_id}/{customer_id} changed"
    examples = []
    for golden_id, turns in CONVERSATIONS.items():
        for turn_in, expected in turns:
            if turn_in["customer_id"]:
                get_customer(turn_in["customer_id"])  # raises if the customer doesn't exist
            if expected.get("procedure_id"):
                assert expected["procedure_id"] in procedures(), f"{golden_id}: unknown procedure"
        examples.append({
            "id": f"conv-{golden_id}",
            "inputs": {"turns": [t for t, _ in turns]},
            "outputs": {"turns": [e for _, e in turns]},
            "metadata": {"suite": "conversations", "slice": golden_id, "turns": len(turns)},
        })
    return examples


if __name__ == "__main__":
    examples = build()
    OUT.write_text("".join(json.dumps(e) + "\n" for e in examples))
    print(f"wrote {len(examples)} conversations ({sum(len(e['inputs']['turns']) for e in examples)} turns) to {OUT.relative_to(Path.cwd())}")
