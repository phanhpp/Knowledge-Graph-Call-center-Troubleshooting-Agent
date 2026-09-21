"""End-to-end: follow-ups that ask about the previous answer. The walk runs first (PROC-001 for
CUST-1005 ends at "Escalate to Tier 2", limited by rule R012), then a follow-up is sent with no
customer ID. Needs Neo4j and ANTHROPIC_API_KEY."""

import pytest

from agent.state import reply_of
from tests.e2e.conversation import Conversation
from tests.e2e.markers import requires_anthropic_key

pytestmark = [pytest.mark.e2e, requires_anthropic_key]


@pytest.fixture
def after_the_walk(chat_graph):
    """A conversation in which the roaming walk for CUST-1005 has already been answered."""
    chat = Conversation(chat_graph, "explain-thread")
    first = chat.say("Customer cannot activate roaming", customer_id="CUST-1005")
    assert reply_of(first).citations == ["ACT-015", "R012"]
    return chat


def test_follow_up_asks_what_a_cited_rule_says(after_the_walk):
    """'What does that rule say?' is answered from the rule's wording in Neo4j, with no customer ID."""
    result = after_the_walk.say("what does that rule say?")

    assert result["intent"] == "explain"
    assert "summary" in reply_of(result).text.lower()
    assert reply_of(result).citations == ["ACT-015", "R012"]


def test_follow_up_asks_why_the_walk_ended_where_it_did(after_the_walk):
    """'Why?' is answered from the steps the walk checked and the values it used."""
    result = after_the_walk.say("why did it end up at Tier 2?")

    answer = reply_of(result).text.lower()
    assert result["intent"] == "explain"
    assert "device" in answer
    assert "plan" in answer


def test_follow_up_can_be_repeated(after_the_walk):
    """Asking twice still explains the walk: the second question does not lose the trace, so it can
    still say which value the last check looked at."""
    after_the_walk.say("why did it end up at Tier 2?")
    result = after_the_walk.say("what value did the last check look at?")

    assert result["intent"] == "explain"
    assert "device" in reply_of(result).text.lower()


def test_explaining_on_a_first_message_does_not_crash(chat_graph):
    """With nothing earlier there is nothing to explain, but the request still gets an answer."""
    result = Conversation(chat_graph, "first-message").say("what does R012 say?", customer_id="CUST-1005")

    assert reply_of(result).text
