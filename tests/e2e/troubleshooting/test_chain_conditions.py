"""End-to-end: PROC-016 ("Reported service outage in customer's area"), whose first step is a chain
condition: read the customer's ZIP code, then look up the network status for that ZIP. Needs
Neo4j and TYPESAFE_API_KEY (classify_intent, match_procedure)."""

import pytest

from agent.state import reply_of
from etl.io_utils import load_json
from tests.e2e.markers import requires_anthropic_key, requires_typesafe_key

pytestmark = [pytest.mark.e2e, requires_anthropic_key, requires_typesafe_key]

ACTION_TEXT = {a["action_id"]: a["text"] for a in load_json("actions.json")}


def test_outage_in_the_customers_zip_answers_at_step_1(ask):
    """CUST-1001 lives in ZIP 94103, which has an active outage, so step 1 answers YES."""
    result = ask("Reported service outage in customer's area", "CUST-1001")
    assert result["intent"] == "troubleshooting"
    assert result["matched_procedure_id"] == "PROC-016"
    assert reply_of(result).text.splitlines()[0] == ACTION_TEXT["ACT-006"]
    assert reply_of(result).citations == ["ACT-006"]
