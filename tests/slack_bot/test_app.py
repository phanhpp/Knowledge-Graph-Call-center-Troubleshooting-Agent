"""Tests for slack_bot/app.py: the /agent command handler, driven by a fake `respond` and a fake agent."""

from slack_bot.access import Access
from slack_bot.app import REPLY_VISIBILITY, build_app, handle_command
from slack_bot.formatting import ERROR_REPLY, MISSING_CUSTOMER_ID, MISSING_QUESTION, NOT_ALLOWED, WRONG_CHANNEL


class FakeRespond:
    """Stands in for Bolt's `respond`: records every reply the bot sends to the command."""

    def __init__(self):
        """Start with no replies."""
        self.replies = []

    def __call__(self, **kwargs):
        """Record one reply."""
        self.replies.append(kwargs)

    @property
    def texts(self):
        """The text of each reply, in order."""
        return [r["text"] for r in self.replies]


def make_ask(result=None, error=None):
    """Return a fake ask(query, customer_id) that records its calls, then returns or raises."""
    calls = []

    def ask(query, customer_id):
        """The fake agent."""
        calls.append((query, customer_id))
        if error:
            raise error
        return result

    ask.calls = calls
    return ask


def command(text, channel_id="C1"):
    """A minimal /agent command payload."""
    return {"command": "/agent", "text": text, "channel_id": channel_id, "user_id": "U1"}


# --- handle_command ---


def test_answers_the_command_with_the_answer_and_sources():
    """The agent gets (question, customer_id), and the reply is the answer plus its sources."""
    respond = FakeRespond()
    ask = make_ask({"answer": "Escalate to Tier 2", "citations": ["ACT-015", "R012"]})

    handle_command(command("CUST-1005 customer cannot activate roaming"), respond, ask)

    assert ask.calls == [("customer cannot activate roaming", "CUST-1005")]
    assert respond.texts == ["Escalate to Tier 2\n\n_Sources: ACT-015, R012_"]


def test_reply_replaces_the_checking_message_and_is_private():
    """The reply replaces "Checking..." and uses REPLY_VISIBILITY (private to the person who typed it)."""
    respond = FakeRespond()

    handle_command(command("CUST-1005 roaming"), respond, make_ask({"answer": "ok", "citations": []}))

    assert respond.replies[0]["replace_original"] is True
    assert respond.replies[0]["response_type"] == REPLY_VISIBILITY == "ephemeral"


def test_missing_customer_id_gets_an_example_and_no_agent_call():
    """Without a customer ID the bot explains what to type, and the agent is not run."""
    respond, ask = FakeRespond(), make_ask({"answer": "unused", "citations": []})

    handle_command(command("customer cannot activate roaming"), respond, ask)

    assert ask.calls == []
    assert respond.texts == [MISSING_CUSTOMER_ID]


def test_bare_command_gets_the_missing_id_example():
    """`/agent` with nothing after it is answered with the example, not an error."""
    respond, ask = FakeRespond(), make_ask({"answer": "unused", "citations": []})

    handle_command(command(""), respond, ask)

    assert ask.calls == []
    assert respond.texts == [MISSING_CUSTOMER_ID]


def test_missing_question_gets_an_example_and_no_agent_call():
    """A customer ID with no question gets its own message, and the agent is not run."""
    respond, ask = FakeRespond(), make_ask({"answer": "unused", "citations": []})

    handle_command(command("CUST-1005"), respond, ask)

    assert ask.calls == []
    assert respond.texts == [MISSING_QUESTION]


def test_agent_failure_becomes_an_error_message_not_a_crash():
    """If the agent raises, the reply is ERROR_REPLY and nothing propagates to Slack."""
    respond, ask = FakeRespond(), make_ask(error=RuntimeError("Neo4j is down"))

    handle_command(command("CUST-1005 roaming"), respond, ask)

    assert respond.texts == [ERROR_REPLY]


def test_unknown_customer_message_is_passed_through_to_the_user():
    """The agent's own 'couldn't find a customer' answer is what the user sees."""
    message = "I couldn't find a customer with ID 'CUST-9999'. Check the customer ID and try again."
    respond = FakeRespond()
    ask = make_ask({"answer": message, "citations": [], "error": "No CRM record"})

    handle_command(command("CUST-9999 roaming"), respond, ask)

    assert respond.texts == [message]


# --- channel allow-list ---


def test_no_allow_list_means_any_channel_works():
    """With no allowed channels set, the command works everywhere."""
    respond, ask = FakeRespond(), make_ask({"answer": "ok", "citations": []})

    handle_command(command("CUST-1005 roaming", channel_id="CANY"), respond, ask)

    assert ask.calls


def test_allowed_channel_works():
    """A command typed in a listed channel is answered."""
    respond, ask = FakeRespond(), make_ask({"answer": "ok", "citations": []})

    handle_command(command("CUST-1005 roaming", channel_id="C1"), respond, ask, allowed_channels=["C1", "C2"])

    assert ask.calls


def test_other_channel_is_refused_without_running_the_agent():
    """A command typed outside the listed channels gets WRONG_CHANNEL, and the agent is not run."""
    respond, ask = FakeRespond(), make_ask({"answer": "unused", "citations": []})

    handle_command(command("CUST-1005 roaming", channel_id="C9"), respond, ask, allowed_channels=["C1"])

    assert ask.calls == []
    assert respond.texts == [WRONG_CHANNEL]


# --- user allow-list ---


def test_a_user_outside_the_allow_list_is_refused_without_running_the_agent():
    """Only listed users may use /agent when a list is set."""
    respond, ask = FakeRespond(), make_ask({"answer": "unused", "citations": []})

    handle_command(command("CUST-1005 roaming"), respond, ask, access=Access(["U2"]))

    assert ask.calls == []
    assert respond.texts == [NOT_ALLOWED]


def test_a_listed_user_is_answered():
    """A user on the list gets an answer."""
    respond, ask = FakeRespond(), make_ask({"answer": "ok", "citations": []})

    handle_command(command("CUST-1005 roaming"), respond, ask, access=Access(["U1"]))

    assert ask.calls


# --- build_app ---


def test_build_app_needs_no_network():
    """Building the app must not contact Slack (the token is only checked later, in serve())."""
    app = build_app("xoxb-not-a-real-token", make_ask({"answer": "x", "citations": []}), chat=object(), allowed_channels=["C1"])
    assert app is not None
