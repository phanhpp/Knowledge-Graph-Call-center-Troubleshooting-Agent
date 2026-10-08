"""End-to-end: PROC-017 ("Customer requests service credit for outage"), whose first step asks
Claude to read the customer's latest ticket and say whether the issue is still ongoing. Needs
Neo4j and TYPESAFE_API_KEY (classify_intent, match_procedure) and ANTHROPIC_API_KEY (the ticket reader)."""

import pytest

from agent.utils import reply_of
from etl.io_utils import load_json
from tests.e2e.markers import requires_anthropic_key, requires_typesafe_key

pytestmark = [pytest.mark.e2e, requires_anthropic_key, requires_typesafe_key]

ACTION_TEXT = {a["action_id"]: a["text"] for a in load_json("actions.json")}
QUERY = "Customer requests service credit for outage"


def test_ticket_says_issue_is_still_ongoing(ask):
    """CUST-1002's latest ticket says roaming still isn't working, so step 1 answers YES."""
    result = ask(QUERY, "CUST-1002")
    assert result["matched_procedure_id"] == "PROC-017"
    assert reply_of(result).text.splitlines()[0] == ACTION_TEXT["ACT-006"]
    assert reply_of(result).citations == ["ACT-006"]


def test_ticket_says_issue_is_resolved_so_the_walk_continues(ask):
    """CUST-1005's latest ticket says the device works now, so step 1 answers NO. The balance
    check at step 2 also says NO, so the last step's fallback action (ACT-024, limited by R011)
    is returned."""
    result = ask(QUERY, "CUST-1005")
    assert result["matched_procedure_id"] == "PROC-017"
    assert reply_of(result).text.splitlines()[0] == ACTION_TEXT["ACT-024"]
    assert reply_of(result).citations == ["ACT-024", "R011"]
