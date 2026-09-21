# Slack bot

Use the Super Agent from Slack with one command:

```
/agent CUST-1005 customer cannot activate roaming
```

It replies with the answer and the sources (`_Sources: ACT-015, R012_`). Only the person who typed the command sees the reply, because it contains customer information. Slack does not keep private replies after you reload the app.

If the customer ID or the question is missing, it replies with an example. An unknown ID gets "I couldn't find a customer with ID ...".

**Setup:** [slack_setup.md](slack_setup.md) has the steps, the two tokens explained, and a checklist for when nothing happens.

## A conversation, in a direct message

Send the bot a direct message. The first message names the customer; follow-ups do not need to:

```
CUST-1005 customer cannot activate roaming
what does that rule say?
why did it end up at Tier 2?
```

The conversation is saved (in `checkpoints.db`), so the agent remembers the customer, what was said, and which steps its last answer checked. A different customer ID starts a fresh conversation. A reply in a thread inside the DM is a separate conversation.

`/agent` stays a one-off: it does not remember anything, and Slack does not allow slash commands in threads.

Set `SLACK_ALLOWED_USERS` to limit who can use the bot. Direct messages work from anywhere in the workspace.

## Run

```bash
uv run python -m slack_bot
```

It needs the same things as the agent: Neo4j running with the graph and articles loaded, and the Anthropic and OpenAI keys in `.env`.

## Tests

`tests/slack_bot/` has tests that use no network and no Slack workspace: parsing, formatting, the command and direct-message handlers, the saved-conversation wrapper (including the customer switch and the per-conversation lock), and Bolt's own routing with fake events. They cannot tell you whether your tokens, command setup or scopes are right. Only running the bot can.

## Microsoft Teams

Not built. A Teams bot needs a public HTTPS endpoint and an Azure Bot registration, and Teams has no equivalent of Socket Mode. That is far more setup than Slack.
