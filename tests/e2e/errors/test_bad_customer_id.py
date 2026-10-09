"""End-to-end: a customer ID that is not in the CRM, and troubleshooting with no customer named.
An unknown ID must stop the request before any paid call, so those tests need no API key or Neo4j."""

import pytest

from agent.supervisor import WHICH_CUSTOMER
from agent.utils import reply_of
from tests.e2e.markers import requires_typesafe_key

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


@requires_typesafe_key
def test_troubleshooting_with_no_customer_named_asks_which_one(ask):
    """No customer named and none current: troubleshooting needs one, so the agent asks (Needs Neo4j
    and TYPESAFE_API_KEY: the message is classified first)."""
    result = ask("Customer cannot activate roaming", None)
    assert reply_of(result).text == WHICH_CUSTOMER
    assert reply_of(result).citations == []
