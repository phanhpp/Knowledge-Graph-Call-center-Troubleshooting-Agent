# Slack bot

Consultants can reach the Super Agent from Slack in two ways:

| | `/agent` command | Direct message (DM) to the bot |
|---|---|---|
| Use it for | a quick one-off question | a conversation with follow-ups |
| Example | `/agent CUST-1005 customer cannot activate roaming` | `CUST-1005 customer cannot activate roaming`, then `why?` |
| Remembers earlier messages | no, every command starts from zero | yes, saved in `checkpoints.db` |
| Who sees the answer | only you, and Slack drops it when the app reloads | you, as normal messages that stay |
| Customer ID | in every command | in the first message only |

Every answer ends with its sources, for example `_Sources: ACT-015, R012_`.

**Setup:** [slack_setup.md](slack_setup.md) has the steps, what the two tokens are for, and a checklist for when nothing happens.

## 1. `/agent`: one question, one answer

```
/agent CUST-1005 customer cannot activate roaming
```

The answer is visible only to you, because it contains customer information.

`/agent` has no memory, for two reasons:
- **The answer doesn't stay.** Slack drops private replies when the app reloads. If the agent remembered the conversation, it would remember answers you can no longer see.
- **There's nowhere to keep a conversation.** A slash command has no thread to continue in, and Slack does not allow slash commands inside threads.

If the customer ID or the question is missing, the bot replies with an example. An unknown ID gets "I couldn't find a customer with ID ...".

## 2. A direct message: a conversation

Open a DM with the bot. The first message names the customer; follow-ups do not need to:

```
CUST-1005 customer cannot activate roaming
what does that rule say?
why did it end up at Tier 2?
```

The agent remembers what was said, the customer currently being discussed, and each customer's last troubleshooting result.

- One DM is one conversation.
- A thread inside the DM is a separate conversation, so you can work two cases side by side.
- Naming a different customer ID switches to that customer. The conversation is kept, so you can come back to an earlier customer and ask "why?" about their answer.

## How the bot connects: Socket Mode

**Why.** Slack normally delivers events by calling a public HTTPS URL on your server. With Socket Mode, the bot opens the connection to Slack itself, and Slack sends commands and messages down that connection. So the bot:
- needs no public URL, no tunnel (like ngrok) and no open port;
- runs on a laptop, or behind a company firewall.

The trade-off: the bot only works while `uv run python -m slack_bot` is running somewhere. Also, a Socket Mode app can't be listed in Slack's public app directory, which an internal tool doesn't need.

**How.** It uses two tokens, both from the Slack app settings (details in [slack_setup.md](slack_setup.md)):
- `SLACK_APP_TOKEN` (`xapp-...`) opens the Socket Mode connection.
- `SLACK_BOT_TOKEN` (`xoxb-...`) lets the bot post and edit messages.

In the code, `SocketModeHandler(app, app_token).start()` in `app.py` opens the connection and keeps it open.

## Who can use it

The bot shows customer data, so limit who can reach it. All settings go in `.env` and take **IDs, not names**, because a channel can be renamed but its ID never changes.

| Setting | What it does | Applies to |
|---|---|---|
| `SLACK_MEMBERS_CHANNEL` (recommended) | members of this **private** channel may use the bot | `/agent` and DMs |
| `SLACK_ALLOWED_USERS` | these user IDs may use it too, e.g. a supervisor outside the channel | `/agent` and DMs |
| `SLACK_ALLOWED_CHANNELS` | channels where `/agent` may be typed | `/agent` only |

**Why a channel decides who can DM the bot.** A DM isn't sent in any channel, so a channel limit has nothing to check. The bot checks whether the **sender is a member** of the members channel instead. Access is then managed in Slack: add a consultant to the channel and they can use the bot, remove them and they can't. The member list is re-read every 5 minutes (`slack_bot/access.py`). If Slack can't be reached, the bot keeps the last list it read, and lets nobody in if it has none yet.

Make the members channel **private**. Anyone can join a public channel on their own, which would let anyone give themselves access.

With neither `SLACK_MEMBERS_CHANNEL` nor `SLACK_ALLOWED_USERS` set, anyone in the workspace can DM the bot and look up any customer. The bot prints who has access when it starts. Setup steps and the scopes it needs are in [slack_setup.md](slack_setup.md).

## Run

```bash
uv run python -m slack_bot
```

It needs the same things as the agent: Neo4j running with the graph and articles loaded, and the Anthropic, OpenAI and TypeSafe keys in `.env`. It also needs the two Slack tokens.

## Tests

`tests/slack_bot/` has tests that use no network and no Slack workspace. They cover:
- parsing and formatting;
- the command and DM handlers;
- the saved-conversation wrapper, including switching customer and answering one message at a time per conversation;
- Bolt's routing, with fake events.

They can't tell you whether your tokens, command setup or scopes are right. Only running the bot can.

## Microsoft Teams

Not built. A Teams bot needs a public HTTPS endpoint and an Azure Bot registration, and Teams has no equivalent of Socket Mode. That's far more setup than Slack.
