"""End-to-end: a conversation moving between paths. Needs Neo4j, ANTHROPIC_API_KEY, TYPESAFE_API_KEY (and
OPENAI_API_KEY for the knowledge-base test)."""

import pytest

from agent.utils import reply_of
from tests.e2e.conversation import Conversation
from tests.e2e.markers import requires_anthropic_key, requires_typesafe_key, requires_openai_key

pytestmark = [pytest.mark.e2e, requires_anthropic_key, requires_typesafe_key]


def test_a_new_problem_for_the_same_customer_needs_no_id(chat_graph):
    """After one walk, a different problem is sent with no customer ID. It runs its own procedure
    and the customer's saved result now describes that walk."""
    chat = Conversation(chat_graph, "new-problem")
    chat.say("Customer cannot activate roaming", customer_id="CUST-1001")

    result = chat.say("Reported service outage in customer's area")

    assert result["intent"] == "troubleshooting"
    assert result["matched_procedure_id"] == "PROC-016"
    assert chat.saved["troubleshooting_results"]["CUST-1001"]["procedure_id"] == "PROC-016"
    assert chat.saved["customer_id"] == "CUST-1001"


def test_a_good_message_after_an_unknown_customer_is_not_blocked(chat_graph):
    """The failed first turn leaves an error in the saved state. The next turn must not inherit it."""
    chat = Conversation(chat_graph, "recover")
    failed = chat.say("Customer cannot activate roaming", customer_id="CUST-9999")
    assert "CUST-9999" in reply_of(failed).text

    result = chat.say("Customer cannot activate roaming", customer_id="CUST-1002")

    assert result["error"] is None
    assert reply_of(result).citations == ["ACT-001"]


def test_two_conversations_do_not_mix(chat_graph):
    """Different thread ids keep their own customer and messages."""
    a = Conversation(chat_graph, "thread-a")
    b = Conversation(chat_graph, "thread-b")

    a.say("Customer cannot activate roaming", customer_id="CUST-1001")
    b.say("Customer cannot activate roaming", customer_id="CUST-1002")

    assert a.saved["customer_id"] == "CUST-1001"
    assert b.saved["customer_id"] == "CUST-1002"
    assert len(a.saved["messages"]) == len(b.saved["messages"]) == 2
    assert a.saved["troubleshooting_results"]["CUST-1001"]["action_id"] == "ACT-015"
    assert b.saved["troubleshooting_results"]["CUST-1002"]["action_id"] == "ACT-001"


def test_each_turn_adds_exactly_one_reply_with_its_citations(chat_graph):
    """After each turn the conversation holds the consultant's message and one agent reply, with the
    citations on the reply. No message is added twice (the walk shares `messages` with the
    Supervisor, and add_messages must merge them by id)."""
    chat = Conversation(chat_graph, "messages")
    chat.say("Customer cannot activate roaming", customer_id="CUST-1001")
    chat.say("what does that rule say?")

    messages = chat.saved["messages"]
    assert [m.type for m in messages] == ["human", "ai", "human", "ai"]
    assert messages[1].additional_kwargs["citations"] == ["ACT-015", "R012"]


@requires_openai_key
def test_a_knowledge_question_inside_a_conversation(chat_graph):
    """A knowledge-base question after a walk goes to the open-ended path and cites an article."""
    chat = Conversation(chat_graph, "kb-in-chat")
    chat.say("Customer cannot activate roaming", customer_id="CUST-1001")

    result = chat.say("What is the SIM replacement fee?")

    assert result["intent"] == "open_ended"
    assert "KB-006" in reply_of(result).citations
