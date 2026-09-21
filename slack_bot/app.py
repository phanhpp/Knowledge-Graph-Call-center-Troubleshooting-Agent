"""Slack front end: the /agent command (one question) and direct messages (a conversation).

Why Socket Mode: the bot dials out to Slack over a websocket, so it needs no public URL, webhook
or tunnel and runs fine on a laptop.

Two ways in:

    /agent CUST-1005 customer cannot activate roaming
        One question, answered privately (only the person who typed it sees the reply). No memory.
        Slack gives a command 3 seconds to be acknowledged, so on_agent calls ack("Checking...") first.

    a direct message to the bot
        A conversation. The first message names the customer ("CUST-1005 customer cannot activate
        roaming"); follow-ups need no ID ("why?", "what does that rule say?"). The conversation is
        saved (SQLite, see config.get_checkpointer), one per DM, or one per thread inside the DM.
        Naming a different customer starts a fresh conversation for that customer.

Bolt acknowledges message events by itself, then runs the handler in a background thread (a pool of
5), so a slow answer never makes Slack retry. Two messages in the same conversation are run one at a
time (AgentChat holds a lock per conversation).

Access: SLACK_ALLOWED_USERS (optional) limits both ways in to those Slack user IDs, and
SLACK_ALLOWED_CHANNELS (optional) limits /agent to those channels. DMs work anywhere in the
workspace, so without SLACK_ALLOWED_USERS anyone who can message the bot can look up any customer.
"""

import logging
import os
from typing import Callable, Iterable

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from slack_bot.chat import AgentChat
from slack_bot.formatting import (
    ERROR_REPLY,
    MISSING_CUSTOMER_ID,
    MISSING_CUSTOMER_ID_DM,
    MISSING_QUESTION,
    MISSING_QUESTION_DM,
    NOT_ALLOWED,
    WORKING,
    WRONG_CHANNEL,
    format_reply,
)
from slack_bot.parsing import parse_message
from slack_bot.threads import thread_id_for

logger = logging.getLogger(__name__)

# ask(query, customer_id) -> {"answer", "citations"}: one question, no memory (used by /agent)
Ask = Callable[[str, str], dict]

# "ephemeral" = only the person who typed the command sees the reply; "in_channel" = everyone
# in the channel sees it, and sees the typed command too.
REPLY_VISIBILITY = "ephemeral"


def handle_command(
    command: dict, respond, ask: Ask, allowed_channels: Iterable[str] = (), allowed_users: Iterable[str] = ()
) -> None:
    """Answer one /agent command. Every reply replaces the "Checking..." message.

    `command` is Slack's command payload (its `text` is what came after "/agent", `channel_id` is
    where it was typed and `user_id` who typed it). `respond` posts to the command's response_url.
    If `allowed_users` or `allowed_channels` is not empty, the command only works for those users or
    in those channels. If the agent raises, the reply is ERROR_REPLY and the exception is logged.
    """

    def reply(text: str) -> None:
        """Replace the "Checking..." message with `text`."""
        respond(text=text, replace_original=True, response_type=REPLY_VISIBILITY)

    users = set(allowed_users)
    if users and command.get("user_id") not in users:
        reply(NOT_ALLOWED)
        return
    channels = set(allowed_channels)
    if channels and command.get("channel_id") not in channels:
        reply(WRONG_CHANNEL)
        return

    parsed = parse_message(command.get("text", ""))
    if parsed.customer_id is None:
        reply(MISSING_CUSTOMER_ID)
        return
    if not parsed.query:
        reply(MISSING_QUESTION)
        return

    try:
        answer = format_reply(ask(parsed.query, parsed.customer_id))
    except Exception:
        logger.exception("Command failed: %r", command.get("text"))
        answer = ERROR_REPLY
    reply(answer)


def is_direct_message_from_user(event: dict) -> bool:
    """True for a plain message a person typed in a direct message to the bot.

    Drops the bot's own posts (`bot_id`), edits and deletions (`subtype`), and anything that is not
    a direct message, so the bot never answers itself or reacts to channel chatter.
    """
    return event.get("channel_type") == "im" and not event.get("bot_id") and not event.get("subtype")


def handle_direct_message(event: dict, client, chat: AgentChat, allowed_users: Iterable[str] = ()) -> None:
    """Answer one direct message as part of a saved conversation.

    The reply goes in the same place: the DM, or the thread inside it. The first message of a
    conversation must name the customer. Later ones need not, because the conversation remembers it.
    The bot posts "Checking...", runs the agent, and edits that message into the answer. If the agent
    raises, the message becomes ERROR_REPLY and the exception is logged.
    """
    channel = event["channel"]
    thread_ts = event.get("thread_ts")  # None for a top-level message in the DM

    def post(text: str):
        """Post `text` in the DM (or its thread) and return Slack's response."""
        args = {"channel": channel, "text": text}
        if thread_ts:
            args["thread_ts"] = thread_ts
        return client.chat_postMessage(**args)

    users = set(allowed_users)
    if users and event.get("user") not in users:
        post(NOT_ALLOWED)
        return

    parsed = parse_message(event.get("text", ""))
    thread_id = thread_id_for(channel, thread_ts)
    if parsed.customer_id is None and chat.known_customer(thread_id) is None:
        post(MISSING_CUSTOMER_ID_DM)
        return
    if not parsed.query:
        post(MISSING_QUESTION_DM)
        return

    placeholder = post(WORKING)
    try:
        answer = format_reply(chat.send(thread_id, parsed.query, parsed.customer_id))
    except Exception:
        logger.exception("Direct message failed: %r", event.get("text"))
        answer = ERROR_REPLY
    client.chat_update(channel=channel, ts=placeholder["ts"], text=answer)


def build_app(
    bot_token: str,
    ask: Ask,
    chat: AgentChat,
    allowed_channels: Iterable[str] = (),
    allowed_users: Iterable[str] = (),
) -> App:
    """Build the Bolt app with its two listeners: the /agent command, and direct messages.

    Nothing here touches the network (the token is not checked at construction), so the wiring
    can be built in a test. serve() checks the token explicitly.
    """
    app = App(token=bot_token, token_verification_enabled=False)
    channels = frozenset(allowed_channels)
    users = frozenset(allowed_users)

    @app.command("/agent")
    def on_agent(ack, command: dict, respond) -> None:
        """Acknowledge within Slack's 3-second limit, then answer in the background thread."""
        ack(WORKING)
        handle_command(command, respond, ask, channels, users)

    @app.event("message")
    def on_message(event: dict, client) -> None:
        """A message event: answer it only if it is a person's direct message to the bot."""
        if is_direct_message_from_user(event):
            handle_direct_message(event, client, chat, users)

    return app


def serve() -> None:
    """Start the bot and keep running until stopped. Run with `uv run python -m slack_bot`.

    Reads SLACK_BOT_TOKEN (xoxb-...) and SLACK_APP_TOKEN (xapp-...) from the environment or .env,
    and exits with a message if either is missing or the two are swapped. SLACK_ALLOWED_USERS and
    SLACK_ALLOWED_CHANNELS (both optional, IDs separated by commas) limit who can use the bot and
    where /agent works.
    """
    import config  # noqa: F401  (loads .env)
    from langchain_core.messages import HumanMessage

    from agent.state import reply_of
    from agent.supervisor import build_supervisor_graph
    from config import get_checkpointer

    bot_token = os.environ.get("SLACK_BOT_TOKEN", "")
    app_token = os.environ.get("SLACK_APP_TOKEN", "")
    if not bot_token.startswith("xoxb-"):
        raise SystemExit("SLACK_BOT_TOKEN must be set and start with 'xoxb-' (see slack_bot/slack_setup.md).")
    if not app_token.startswith("xapp-"):
        raise SystemExit("SLACK_APP_TOKEN must be set and start with 'xapp-' (see slack_bot/slack_setup.md).")

    def id_list(name: str) -> set[str]:
        """The comma-separated IDs in an environment variable, ignoring blanks."""
        return {i.strip() for i in os.environ.get(name, "").split(",") if i.strip()}

    channels, users = id_list("SLACK_ALLOWED_CHANNELS"), id_list("SLACK_ALLOWED_USERS")

    logging.basicConfig(level=logging.INFO)
    one_shot = build_supervisor_graph()  # /agent: no saved memory
    checkpointer = get_checkpointer()
    chat = AgentChat(build_supervisor_graph(checkpointer=checkpointer), checkpointer)  # DMs: saved

    def ask(query: str, customer_id: str) -> dict:
        """Run one question through the graph with no memory; return the reply as {"answer", "citations"}."""
        state = one_shot.invoke({"messages": [HumanMessage(query)], "customer_id": customer_id})
        reply = reply_of(state)
        return {"answer": reply.text, "citations": reply.citations}

    app = build_app(bot_token, ask, chat, channels, users)
    auth = app.client.auth_test()
    where = f"only in channel(s) {', '.join(sorted(channels))}" if channels else "in any channel"
    print(
        f"Connected as @{auth['user']} in {auth['team']}. "
        f"Use /agent CUST-1001 <question> {where}, or send it a direct message for a conversation."
    )
    SocketModeHandler(app, app_token).start()
