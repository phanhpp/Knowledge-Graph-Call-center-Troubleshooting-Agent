"""End-to-end: PROC-001 ("Customer cannot activate roaming"), whose conditions are plain field
checks on the customer's CRM record. Needs Neo4j and ANTHROPIC_API_KEY (classify_intent)."""

import pytest

from agent.state import reply_of
from etl.io_utils import load_json
from tests.e2e.markers import requires_anthropic_key

pytestmark = [pytest.mark.e2e, requires_anthropic_key]

ACTION_TEXT = {a["action_id"]: a["text"] for a in load_json("actions.json")}
QUERY = "Customer cannot activate roaming"


def test_yes_at_the_first_step_stops_there(ask):
    """CUST-1002 has roaming off, so step 1 answers YES: the procedure stops and returns ACT-001."""
    result = ask(QUERY, "CUST-1002")
    assert result["intent"] == "troubleshooting"
    assert result["matched_procedure_id"] == "PROC-001"
    assert reply_of(result).text == ACTION_TEXT["ACT-001"]
    assert reply_of(result).citations == ["ACT-001"]


def test_yes_at_the_last_step(ask):
    """CUST-1001 says NO at steps 1 and 2, then YES at step 3 (device supports roaming)."""
    result = ask(QUERY, "CUST-1001")
    assert reply_of(result).text == ACTION_TEXT["ACT-019"]
    assert reply_of(result).citations == ["ACT-019"]


def test_no_at_every_step_returns_the_fallback_action_and_its_rule(ask):
    """CUST-1005 says NO at all three steps, so the last step's fallback action (ACT-015) is
    returned, together with the rule that limits it (R012)."""
    result = ask(QUERY, "CUST-1005")
    assert reply_of(result).text == ACTION_TEXT["ACT-015"]
    assert reply_of(result).citations == ["ACT-015", "R012"]
