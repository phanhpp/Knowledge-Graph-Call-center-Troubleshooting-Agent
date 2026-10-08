"""End-to-end: a customer_id that is not in the CRM. The request must stop before any Claude call,
so none of these tests needs an API key or Neo4j."""

import pytest

from agent.state import reply_of

pytestmark = pytest.mark.e2e


def test_unknown_customer_gets_a_clear_message(ask):
    """The answer names the missing ID, nothing is cited, and `error` says why."""
    result = ask("Customer cannot activate roaming", "CUST-9999")
    assert "CUST-9999" in reply_of(result).text
    assert reply_of(result).citations == []
    assert "CUST-9999" in result["error"]


def test_unknown_customer_is_not_reported_as_a_missing_procedure(ask):
    """The message must not be the 'couldn't find a matching procedure' one, which would hide
    the real cause."""
    result = ask("Customer cannot activate roaming", "CUST-9999")
    assert "matching procedure" not in reply_of(result).text


def test_unknown_customer_stops_before_the_intent_is_classified(ask):
    """`intent` is never set, which shows classify_intent (a paid Jev call) was skipped."""
    result = ask("Customer cannot activate roaming", "CUST-9999")
    assert "intent" not in result


def test_unknown_customer_also_stops_an_open_ended_question(ask):
    """A bad customer_id stops open-ended questions too, not only troubleshooting ones."""
    result = ask("What are the early termination fees?", "CUST-9999")
    assert "CUST-9999" in reply_of(result).text
    assert "intent" not in result


def test_empty_customer_id_gets_the_same_clear_message(ask):
    """An empty customer_id is handled like any other unknown ID."""
    result = ask("Customer cannot activate roaming", "")
    assert "couldn't find a customer" in reply_of(result).text
    assert reply_of(result).citations == []
