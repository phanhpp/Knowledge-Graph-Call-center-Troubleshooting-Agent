"""Tests for the direct-message handler in slack_bot/app.py, with a fake Slack client and fake chat."""

import pytest

from slack_bot.app import handle_direct_message, is_direct_message_from_user
from slack_bot.formatting import (
    ERROR_REPLY,
    MISSING_CUSTOMER_ID_DM,
    MISSING_QUESTION_DM,
    NOT_ALLOWED,
    WORKING,
)


class FakeClient:
    """Records what the bot posts and edits, and hands back a fixed message timestamp."""

    def __init__(self):
        """Start with nothing posted or edited."""
        self.posted = []
        self.updated = []

    def chat_postMessage(self, **kwargs):
        """Record a new message and return a response holding its timestamp."""
        self.posted.append(kwargs)
        return {"ts": "900.1"}

    def chat_update(self, **kwargs):
        """Record an edit to an existing message."""
        self.updated.append(kwargs)


class FakeChat:
    """Stands in for AgentChat: a customer per thread, recorded sends, a fixed reply or an error."""

    def __init__(self, known=None, reply=None, error=None):
        """`known` maps thread id -> customer; `reply` is what send returns; `error` is raised instead."""
        self.known = known or {}
        self.reply = reply or {"answer": "Escalate to Tier 2", "citations": ["ACT-015", "R012"]}
        self.error = error
        self.sent = []

    def known_customer(self, thread_id):
        """The customer saved for this thread, or None."""
        return self.known.get(thread_id)

    def send(self, thread_id, text, customer_id):
        """Record the call, then return the reply or raise the error."""
        self.sent.append((thread_id, text, customer_id))
        if self.error:
            raise self.error
        return self.reply


def dm(text, **extra):
    """A minimal direct-message event."""
    return {"channel": "D1", "ts": "100.1", "user": "U1", "text": text, "channel_type": "im", **extra}


# --- the first message of a conversation ---


def test_first_message_with_a_customer_runs_the_agent_and_edits_the_reply_in():
    """The bot posts "Checking...", sends (thread, question, customer) to the chat, then edits the
    message into the answer with its sources. A top-level DM message is answered at the top level."""
    client, chat = FakeClient(), FakeChat()

    handle_direct_message(dm("CUST-1005 customer cannot activate roaming"), client, chat)

    assert chat.sent == [("slack-D1", "customer cannot activate roaming", "CUST-1005")]
    assert client.posted == [{"channel": "D1", "text": WORKING}]
    assert client.updated == [
        {"channel": "D1", "ts": "900.1", "text": "Escalate to Tier 2\n\n_Sources: ACT-015, R012_"}
    ]


def test_first_message_without_a_customer_asks_for_one():
    """A new conversation has no customer, so the bot asks for the ID and does not run the agent."""
    client, chat = FakeClient(), FakeChat()

    handle_direct_message(dm("customer cannot activate roaming"), client, chat)

    assert chat.sent == []
    assert client.posted == [{"channel": "D1", "text": MISSING_CUSTOMER_ID_DM}]


def test_a_customer_id_with_no_question_asks_for_the_question():
    """An ID alone has no question to answer."""
    client, chat = FakeClient(), FakeChat()

    handle_direct_message(dm("CUST-1005"), client, chat)

    assert chat.sent == []
    assert client.posted == [{"channel": "D1", "text": MISSING_QUESTION_DM}]


# --- follow-ups ---


def test_a_follow_up_needs_no_customer_id():
    """Once the conversation has a customer, the message is sent with customer None."""
    client, chat = FakeClient(), FakeChat(known={"slack-D1": "CUST-1005"})

    handle_direct_message(dm("why did it end at Tier 2?"), client, chat)

    assert chat.sent == [("slack-D1", "why did it end at Tier 2?", None)]


def test_a_blank_follow_up_asks_for_a_question():
    """A message that is only whitespace or punctuation has nothing to answer."""
    client, chat = FakeClient(), FakeChat(known={"slack-D1": "CUST-1005"})

    handle_direct_message(dm("  :  "), client, chat)

    assert chat.sent == []
    assert client.posted[0]["text"] == MISSING_QUESTION_DM


def test_a_reply_in_a_thread_is_answered_in_that_thread_as_its_own_conversation():
    """A message inside a thread posts into it, and is a different conversation from the DM itself."""
    client, chat = FakeClient(), FakeChat()

    handle_direct_message(dm("CUST-1002 roaming", thread_ts="50.5"), client, chat)

    assert chat.sent[0][0] == "slack-D1-50.5"
    assert client.posted == [{"channel": "D1", "text": WORKING, "thread_ts": "50.5"}]


def test_naming_a_new_customer_is_passed_on():
    """A message with an ID goes to the chat with that ID, even if the conversation has a customer
    (AgentChat decides to start fresh)."""
    client, chat = FakeClient(), FakeChat(known={"slack-D1": "CUST-1005"})

    handle_direct_message(dm("CUST-1002 roaming"), client, chat)

    assert chat.sent == [("slack-D1", "roaming", "CUST-1002")]


# --- failure and access ---


def test_agent_failure_becomes_an_error_message_not_a_crash():
    """If the agent raises, the "Checking..." message is edited into ERROR_REPLY."""
    client, chat = FakeClient(), FakeChat(error=RuntimeError("Neo4j is down"))

    handle_direct_message(dm("CUST-1005 roaming"), client, chat)

    assert client.updated == [{"channel": "D1", "ts": "900.1", "text": ERROR_REPLY}]


def test_a_user_outside_the_allow_list_is_refused_without_running_the_agent():
    """Only listed users may use the bot when a list is set."""
    client, chat = FakeClient(), FakeChat()

    handle_direct_message(dm("CUST-1005 roaming", user="U9"), client, chat, allowed_users=["U1"])

    assert chat.sent == []
    assert client.posted == [{"channel": "D1", "text": NOT_ALLOWED}]


def test_a_listed_user_is_answered():
    """A user on the list gets an answer."""
    client, chat = FakeClient(), FakeChat()

    handle_direct_message(dm("CUST-1005 roaming", user="U1"), client, chat, allowed_users=["U1"])

    assert chat.sent


def test_no_allow_list_means_everyone_is_answered():
    """With no list set, any user is answered."""
    client, chat = FakeClient(), FakeChat()

    handle_direct_message(dm("CUST-1005 roaming", user="U9"), client, chat)

    assert chat.sent


# --- is_direct_message_from_user ---


def test_a_persons_direct_message_is_accepted():
    """A plain message in a direct-message channel is answered."""
    assert is_direct_message_from_user({"channel_type": "im", "text": "hi"})


@pytest.mark.parametrize(
    "extra",
    [
        {"channel_type": "channel"},
        {"channel_type": "im", "bot_id": "B1"},
        {"channel_type": "im", "subtype": "message_changed"},
        {"channel_type": "im", "subtype": "bot_message"},
        {},
    ],
)
def test_other_messages_are_ignored(extra):
    """Channel chatter, the bot's own posts, and edits or deletions are all ignored."""
    assert not is_direct_message_from_user({"text": "hi", **extra})
