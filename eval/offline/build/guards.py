"""Builds the guards suite (datasets/guards.jsonl): requests the agent must stop, or handle, early.

    uv run python -m eval.offline.build.guards

The agent doesn't read customer IDs from the text: the caller (the Slack bot, the web app) finds the
ID and stamps it on the message, and the graph checks it (agent/supervisor.py check_customer,
load_crm). So each example gives the message as the graph receives it:
  - unknown ID:        a well-formed ID with no CRM record. Stopped before any model call;
  - malformed ID:      "CUST-12", which the Slack bot's pattern (CUST- and digits) accepts and stamps.
                       Same stop: the web app rejects it before sending;
  - no customer:       a troubleshooting message naming none. Jev screens and classifies it (two
                       calls), then the agent asks which customer, before walking anything or calling Claude;
  - two IDs:           the Slack bot stamps the first ID and leaves the second in the text. The walk
                       must use the first customer's record: the oracle's steps for that customer
                       differ from the other one's, so the checklist shows whose record was read.

Re-running overwrites guards.jsonl, so edit this script, not the file.
"""

import json
from pathlib import Path

from agent.supervisor import UNKNOWN_CUSTOMER, WHICH_CUSTOMER
from eval.offline.oracle import expected_walk

OUT = Path(__file__).resolve().parents[1] / "datasets" / "guards.jsonl"


def stopped(golden_id: str, message: str, customer_id: str | None, reply: str, error: str, path: str | None,
            max_jev_calls: int) -> dict:
    """A request the agent stops with a fixed reply: no walk, no Claude call."""
    return {
        "id": f"guard-{golden_id}",
        "inputs": {"message": message, "customer_id": customer_id},
        "outputs": {"path": path, "procedure_id": None, "customer_after": None, "steps": [],
                    "reply_starts": reply, "error_contains": error,
                    "max_claude_calls": 0, "max_jev_calls": max_jev_calls},
        "metadata": {"suite": "guards", "slice": golden_id},
    }


def build() -> list[dict]:
    """The 4 guard examples. The two-IDs walk's expected steps come from the oracle, and must differ
    between the two customers, or the example couldn't tell them apart."""
    first, second = "CUST-1002", "CUST-1001"
    walk = expected_walk("PROC-001", first)
    assert walk["steps"] != expected_walk("PROC-001", second)["steps"], "the two customers' walks must differ"
    return [
        stopped("unknown-id", "customer cannot activate roaming", "CUST-9999",
                UNKNOWN_CUSTOMER.format(customer_id="CUST-9999"), "No CRM record found", None, max_jev_calls=0),
        stopped("malformed-id", "customer cannot activate roaming", "CUST-12",
                UNKNOWN_CUSTOMER.format(customer_id="CUST-12"), "No CRM record found", None, max_jev_calls=0),
        stopped("no-customer", "customer cannot get roaming to switch on before their trip", None,
                WHICH_CUSTOMER, "No customer named", "troubleshooting", max_jev_calls=2),
        {
            "id": "guard-two-ids",
            "inputs": {"message": f"customer cannot activate roaming, same problem as {second} last week", "customer_id": first},
            "outputs": {"path": "troubleshooting", "procedure_id": "PROC-001", "customer_after": first,
                        "steps": walk["steps"], "error_contains": None},
            "metadata": {"suite": "guards", "slice": "two-ids",
                         "fits": f"{first} has roaming off (step 1 yes); {second} has it on (all no)"},
        },
    ]


if __name__ == "__main__":
    examples = build()
    OUT.write_text("".join(json.dumps(e) + "\n" for e in examples))
    print(f"wrote {len(examples)} guard examples to {OUT.relative_to(Path.cwd())}")
