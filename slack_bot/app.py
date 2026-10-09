"""The Slack bot. Consultants reach the agent in two ways:

1. The /agent command, for a quick one-off question.

       /agent CUST-1005 customer cannot activate roaming

   Only the person who typed it sees the answer, and Slack drops the answer when the page reloads.
   Nothing is remembered: every /agent starts from zero, so a follow-up like "why?" will not work.

2. A direct message (DM) to the bot, for a conversation.

       CUST-1005 customer cannot activate roaming
       why?
       what does that rule say?

   A message naming a customer ID is about that customer (the ID is checked); a message naming none
   is a general question, or a troubleshooting follow-up for the current customer (if troubleshooting
   has no customer yet, the agent asks which one). The conversation is saved (in SQLite, see
   config.get_checkpointer), one per DM, or one per thread inside the DM. Naming a different customer
   switches to them; the conversation is kept, each message stamped with its customer.

How it connects: Socket Mode. The bot opens a connection to Slack itself, so it needs no public URL
and runs on a laptop.

Timing: Slack wants a reply within 3 seconds, and the agent can take longer. For /agent the bot
answers "Checking..." at once, then replaces it with the answer. For a DM, Bolt (the Slack library)
confirms receipt by itself and runs the handler in the background (up to 5 at a time). Messages in
the same conversation are answered one at a time.

Who can use it (all optional, set in .env):
  SLACK_MEMBERS_CHANNEL   members of this channel may use /agent and DMs (see slack_bot/access.py).
  SLACK_ALLOWED_USERS     these Slack user IDs may too, e.g. someone outside that channel.
  SLACK_ALLOWED_CHANNELS  channels where /agent may be typed.
With neither of the first two set, anyone in the workspace can DM the bot and look up any customer.
"""

import logging
import os
from typing import Callable, Iterable, Optional

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from slack_bot.access import Access
from slack_bot.chat import AgentChat
from slack_bot.formatting import (
    ERROR_REPLY,
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

# The function behind /agent: ask(question, customer_id) -> {"answer": text, "citations": [ids]}.
# It runs the agent once, with no memory. Passed in (not imported) so tests can use a fake.
Ask = Callable[[str, Optional[str]], dict]

# Who sees an /agent reply: "ephemeral" = only the person who typed it;
# "in_channel" = everyone in the channel, together with the command they typed.
REPLY_VISIBILITY = "ephemeral"


def handle_command(
    command: dict, respond, ask: Ask, allowed_channels: Iterable[str] = (), access: Access = Access()
) -> None:
    """Answer one /agent command, replacing the "Checking..." message with the answer.

    In order: check the user (`access`) and the channel are allowed (an empty list means no limit), read the
    customer ID (if any) and question from the text, run `ask`, and post the answer. A message with
    an ID but no question gets a short message saying what to type instead. If the agent fails, the reply is ERROR_REPLY and the
    error is logged.

    `command` is what Slack sends: `text` (everything after "/agent"), `user_id` (who typed it) and
    `channel_id` (where). `respond` is Slack's function for replying to that command.
    """

    def reply(text: str) -> None:
        """Show `text` in place of the "Checking..." message."""
        respond(text=text, replace_original=True, response_type=REPLY_VISIBILITY)

    if not access.allows(command.get("user_id")):
        reply(NOT_ALLOWED)
        return
    channels = set(allowed_channels)
    if channels and command.get("channel_id") not in channels:
        reply(WRONG_CHANNEL)
        return

    parsed = parse_message(command.get("text", ""))
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
    """True only for a new message a person typed to the bot in a DM.

    Slack sends the bot other message events too. These are ignored: messages outside a DM
    (`channel_type` is not "im" i.e. instant message in Slack), the bot's own messages (`bot_id`, so it
    never answers itself), and edits or deletions (`subtype`).
    """
    return event.get("channel_type") == "im" and not event.get("bot_id") and not event.get("subtype")


def handle_direct_message(event: dict, client, chat: AgentChat, access: Access = Access()) -> None:
    """Answer one DM as the next turn of a saved conversation.

    In order: check the user is allowed (`access`), read the customer ID and question from the text, post
    "Checking...", run the agent with the conversation's history, then edit "Checking..." into the
    answer. The reply goes where the message was: the DM, or the thread inside it.

    A message need not name a customer: the agent asks for one only when troubleshooting needs it.
    If the agent fails, the answer is ERROR_REPLY and the error is logged.
    """
    channel = event["channel"]
    thread_ts = event.get("thread_ts")  # set if the message is in a thread; None if not

    def post(text: str):
        """Post `text` where the message came from (the DM, or its thread); return Slack's response."""
        args = {"channel": channel, "text": text}
        if thread_ts:
            args["thread_ts"] = thread_ts
        return client.chat_postMessage(**args)

    if not access.allows(event.get("user")):
        post(NOT_ALLOWED)
        return

    parsed = parse_message(event.get("text", ""))
    thread_id = thread_id_for(channel, thread_ts)  # which saved conversation this message belongs to
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
    access: Access = Access(),
) -> App:
    """Create the Slack app and connect the two ways in: /agent goes to handle_command, DMs to
    handle_direct_message.

    Makes no network calls (the token is not checked here), so tests can build it without Slack.
    serve() checks the token.
    """
    app = App(token=bot_token, token_verification_enabled=False)
    channels = frozenset(allowed_channels)

    @app.command("/agent")
    def on_agent(ack, command: dict, respond) -> None:
        """Reply "Checking..." straight away (Slack allows 3 seconds), then work out the answer."""
        ack(WORKING)
        handle_command(command, respond, ask, channels, access)

    @app.event("message")
    def on_message(event: dict, client) -> None:
        """Slack sends every message the bot can see; answer only a person's DM to the bot."""
        if is_direct_message_from_user(event):
            handle_direct_message(event, client, chat, access)

    return app


def serve() -> None:
    """Start the bot; it runs until stopped. Run with `uv run python -m slack_bot`.

    Needs two tokens in .env: SLACK_BOT_TOKEN (starts "xoxb-", lets the bot post) and SLACK_APP_TOKEN
    (starts "xapp-", opens the Socket Mode connection). Stops with a message if either is missing or
    they are swapped, or if Neo4j is not reachable. Reads the optional access settings: SLACK_MEMBERS_CHANNEL (one channel ID), and
    SLACK_ALLOWED_USERS and SLACK_ALLOWED_CHANNELS (comma-separated IDs). If SLACK_MEMBERS_CHANNEL is
    set but its members can't be read, it stops with a message instead of starting.

    Builds the agent twice: one copy with no memory for /agent, and one that saves conversations
    for DMs.
    """
    import config  # noqa: F401  (loads .env)

    from agent.utils import consultant_message, reply_of
    from agent.supervisor import build_supervisor_graph
    from config import get_checkpointer, neo4j_unavailable_reason, setup_logging

    bot_token = os.environ.get("SLACK_BOT_TOKEN", "")
    app_token = os.environ.get("SLACK_APP_TOKEN", "")
    if not bot_token.startswith("xoxb-"):
        raise SystemExit("SLACK_BOT_TOKEN must be set and start with 'xoxb-' (see slack_bot/slack_setup.md).")
    if not app_token.startswith("xapp-"):
        raise SystemExit("SLACK_APP_TOKEN must be set and start with 'xapp-' (see slack_bot/slack_setup.md).")
    reason = neo4j_unavailable_reason()
    if reason:
        raise SystemExit(f"Not starting: {reason}. Start the database in Neo4j Desktop and try again.")

    def id_list(name: str) -> set[str]:
        """The comma-separated IDs in an environment variable, ignoring blanks."""
        return {i.strip() for i in os.environ.get(name, "").split(",") if i.strip()}

    channels, users = id_list("SLACK_ALLOWED_CHANNELS"), id_list("SLACK_ALLOWED_USERS")

    setup_logging()  # LOG_LEVEL in .env: DEBUG (default) shows each step, INFO one line per decision
    one_shot = build_supervisor_graph()  # for /agent: no memory, each command stands alone
    checkpointer = get_checkpointer()  # saves conversations in SQLite
    chat = AgentChat(build_supervisor_graph(checkpointer=checkpointer), checkpointer)  # for DMs: remembers

    def ask(query: str, customer_id: Optional[str]) -> dict:
        """Answer one /agent question from scratch (no memory of earlier commands).

        customer_id is the one the command names, or None (a general question). Returns
        {"answer": text, "citations": [ids]}, the shape format_reply expects.
        """
        state = one_shot.invoke({"messages": [consultant_message(query, customer_id)]})
        reply = reply_of(state)
        return {"answer": reply.text, "citations": reply.citations}

    access = Access(users, os.environ.get("SLACK_MEMBERS_CHANNEL", "").strip() or None)
    app = build_app(bot_token, ask, chat, channels, access)
    access.client = app.client
    if access.members_channel:
        try:
            count = len(access.fetch_members())
        except Exception as e:
            raise SystemExit(
                f"Could not read the members of SLACK_MEMBERS_CHANNEL={access.members_channel}: {e}. "
                "Check the channel ID, the channels:read (or groups:read) scope, and that the bot is in the "
                "channel (see slack_bot/slack_setup.md)."
            )
        print(f"Access: the {count} members of {access.members_channel}" + (f", plus {len(users)} listed users." if users else "."))
    elif users:
        print(f"Access: {len(users)} listed users.")
    else:
        print("Access: open to everyone in the workspace (set SLACK_MEMBERS_CHANNEL or SLACK_ALLOWED_USERS to limit it).")
    auth = app.client.auth_test()
    where = f"only in channel(s) {', '.join(sorted(channels))}" if channels else "in any channel"
    print(
        f"Connected as @{auth['user']} in {auth['team']}. "
        f"Use /agent CUST-1001 <question> {where}, or send it a direct message for a conversation."
    )
    SocketModeHandler(app, app_token).start()
