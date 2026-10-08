"""End-to-end: PROC-001 ("Customer cannot activate roaming"), whose conditions are plain field
checks on the customer's CRM record. Needs Neo4j and TYPESAFE_API_KEY (classify_intent, match_procedure)."""

import pytest

from agent.state import reply_of
from etl.io_utils import load_json
from tests.e2e.markers import requires_anthropic_key, requires_typesafe_key

pytestmark = [pytest.mark.e2e, requires_anthropic_key, requires_typesafe_key]

ACTION_TEXT = {a["action_id"]: a["text"] for a in load_json("actions.json")}
QUERY = "Customer cannot activate roaming"


def first_line(result) -> str:
    """The reply's first line: the action. The lines after it explain why and list the steps checked."""
    return reply_of(result).text.splitlines()[0]


def test_yes_at_the_first_step_stops_there(ask):
    """CUST-1002 has roaming off, so step 1 answers YES: the procedure stops and returns ACT-001."""
    result = ask(QUERY, "CUST-1002")
    assert result["intent"] == "troubleshooting"
    assert result["matched_procedure_id"] == "PROC-001"
    assert first_line(result) == ACTION_TEXT["ACT-001"]
    assert reply_of(result).citations == ["ACT-001"]


def test_yes_at_the_last_step_names_the_cause(ask):
    """CUST-1005 says NO at steps 1 and 2, then YES at step 3: the device does not support roaming,
    so the reply is the device action (ACT-027)."""
    result = ask(QUERY, "CUST-1005")
    assert first_line(result) == ACTION_TEXT["ACT-027"]
    assert reply_of(result).citations == ["ACT-027"]


def test_no_at_every_step_escalates_with_its_rule(ask):
    """CUST-1001 has roaming on, an active plan and a compatible device: no check finds the cause,
    so the fallback action (ACT-015, escalate) is returned with the rule governing it (R012)."""
    result = ask(QUERY, "CUST-1001")
    assert first_line(result) == ACTION_TEXT["ACT-015"]
    assert reply_of(result).citations == ["ACT-015", "R012"]
