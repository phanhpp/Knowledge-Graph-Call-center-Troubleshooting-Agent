"""Tests that Bolt itself routes Slack events to our handlers.

Fake events go in through Bolt's own dispatch, exactly as the websocket would deliver them. Every
network call (the token check, posting, editing, the command's reply URL) is replaced, so no Slack
workspace is needed. What these cannot show is whether your tokens, scopes or command setup are
right: only running the bot can.
"""

import time
from types import SimpleNamespace
from unittest import mock

import pytest
from slack_bolt.request import BoltRequest
from slack_sdk import WebClient
from slack_sdk.webhook import WebhookClient

from slack_bot.app import build_app


class Response(dict):
    """A dict that also has `.headers`, like slack_sdk's SlackResponse (Bolt reads it when authorizing)."""

    headers = {"x-oauth-scopes": "commands,chat:write,im:history"}


class FakeChat:
    """Stands in for AgentChat: no customer yet, records what is sent, returns a fixed reply."""

    def __init__(self):
        """Start with nothing sent."""
        self.sent = []

    def known_customer(self, thread_id):
        """No conversation has a customer yet."""
        return None

    def send(self, thread_id, text, customer_id):
        """Record the call and return a fixed reply."""
        self.sent.append((thread_id, text, customer_id))
        return {"answer": "Escalate to Tier 2", "citations": ["ACT-015", "R012"]}


def wait_for(condition, timeout=3.0):
    """Wait until condition() is true (handlers run on a background thread). True if it happened."""
    end = time.time() + timeout
    while time.time() < end:
        if condition():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def slack():
    """A Bolt app wired to a fake chat and a slow fake agent, with Slack's network calls faked.

    Yields an object with the app, the fake chat, what was posted, edited and sent to a reply URL,
    and deliver(body) to feed Bolt one event.
    """
    posted, edited, replies = [], [], []
    chat = FakeChat()

    def ask(query, customer_id):
        """A slow agent, to show the command is acknowledged before it finishes."""
        time.sleep(0.3)
        return {"answer": "Escalate to Tier 2", "citations": ["ACT-015", "R012"]}

    with mock.patch.object(
        WebClient, "auth_test", lambda self, **kw: Response(ok=True, user_id="UBOT", bot_id="BBOT", team_id="T1", url="https://x.slack.com/", user="bot", team="acme")
    ), mock.patch.object(
        WebClient, "chat_postMessage", lambda self, **kw: posted.append(kw) or Response(ok=True, ts="900.1")
    ), mock.patch.object(
        WebClient, "chat_update", lambda self, **kw: edited.append(kw) or Response(ok=True)
    ), mock.patch.object(
        WebhookClient, "send_dict", lambda self, body, **kw: replies.append(body) or mock.Mock(status_code=200)
    ):
        app = build_app("xoxb-fake", ask, chat)

        def deliver(body):
            """Feed Bolt one event or command, as the socket connection would, and return its response."""
            return app.dispatch(BoltRequest(body=body, mode="socket_mode"))

        yield SimpleNamespace(app=app, chat=chat, posted=posted, edited=edited, replies=replies, deliver=deliver)


def event_body(event):
    """The envelope Slack wraps around a message event."""
    return {
        "token": "t", "team_id": "T1", "api_app_id": "A1", "type": "event_callback", "event_id": "Ev1", "event_time": 1,
        "authorizations": [{"team_id": "T1", "user_id": "UBOT", "is_bot": True}], "event": event,
    }


def command_body(text):
    """The payload Slack sends for a slash command."""
    return {
        "token": "t", "team_id": "T1", "team_domain": "acme", "channel_id": "C1", "channel_name": "x", "user_id": "U1",
        "user_name": "u", "command": "/agent", "text": text, "api_app_id": "A1", "is_enterprise_install": "false",
        "response_url": "https://hooks.slack.com/commands/T1/1/abc", "trigger_id": "tr",
    }


def test_a_direct_message_reaches_the_chat_and_is_answered_in_place(slack):
    """A person's DM is handed to the conversation, and the "Checking..." message is edited into the answer."""
    slack.deliver(event_body({
        "type": "message", "channel_type": "im", "channel": "D1", "ts": "1.1", "user": "U1", "text": "CUST-1005 roaming",
    }))

    assert wait_for(lambda: slack.edited)
    assert slack.chat.sent == [("slack-D1", "roaming", "CUST-1005")]
    assert slack.posted[0]["text"] == "Checking..."
    assert slack.edited[0]["text"] == "Escalate to Tier 2\n\n_Sources: ACT-015, R012_"


def test_a_message_in_a_channel_is_ignored(slack):
    """Channel chatter is not for the bot, even if it contains a customer ID."""
    response = slack.deliver(event_body({
        "type": "message", "channel_type": "channel", "channel": "C1", "ts": "1.1", "user": "U1", "text": "CUST-1005 hi",
    }))
    time.sleep(0.3)

    assert response.status == 200
    assert slack.chat.sent == []
    assert slack.posted == []


def test_the_bots_own_direct_message_is_ignored(slack):
    """The bot must never answer its own posts."""
    slack.deliver(event_body({
        "type": "message", "channel_type": "im", "channel": "D1", "ts": "1.1", "bot_id": "BBOT", "text": "CUST-1005 hi",
    }))
    time.sleep(0.3)

    assert slack.chat.sent == []
    assert slack.posted == []


def test_the_slash_command_is_acknowledged_at_once_and_answered_later(slack):
    """Slack gives a command 3 seconds. The acknowledgement must come back long before the (slow)
    agent finishes, and the answer then replaces "Checking..." through the command's reply URL, privately."""
    started = time.time()
    response = slack.deliver(command_body("CUST-1005 customer cannot activate roaming"))
    acknowledged_after = time.time() - started

    assert response.status == 200
    assert response.body == "Checking..."
    assert acknowledged_after < 0.25  # the fake agent takes 0.3 seconds
    assert wait_for(lambda: slack.replies)
    assert slack.replies[0]["text"] == "Escalate to Tier 2\n\n_Sources: ACT-015, R012_"
    assert slack.replies[0]["replace_original"] is True
    assert slack.replies[0]["response_type"] == "ephemeral"
