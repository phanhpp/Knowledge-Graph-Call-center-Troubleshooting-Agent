# Slack setup for `python -m slack_bot`

**Slack is optional.** The agent works without it (`graph.invoke(...)`, the tests). Only `python -m slack_bot` needs it.

You have to create your own Slack app. The tokens are secrets, so they cannot be shared or committed.

**Create a new app for this repo, even if you already have one for another project (for example llm_wiki).** A token identifies one app. If two programs connect with the same app token, Slack sends each command to either of them, with no fixed pattern, so commands would randomly go to the wrong program. Using the same workspace, and even the same channel, is fine.

Budget about ten minutes. Most of the steps fail silently if you skip one, so use the checklist at the bottom.

## What you end up with

Two values in your `.env`, and one optional one:

```
SLACK_BOT_TOKEN=xoxb-...              # shows the app is installed in your workspace
SLACK_APP_TOKEN=xapp-...              # connects the bot to Slack (Socket Mode)
SLACK_ALLOWED_CHANNELS=C0123ABCDEF    # optional: limit /agent to these channels
SLACK_ALLOWED_USERS=U0123ABCDEF       # optional: only these people may use the bot
```

> **The two tokens come from two different pages.** `xapp-` is an *app-level* token that you generate under Basic Information. `xoxb-` is a *bot* token that only exists after you install the app. They are not interchangeable, and the bot refuses to start if they are swapped.

- The **app token** belongs to the app, not to one workspace. Without it the bot receives nothing.
- The **bot token** belongs to one workspace. The bot needs it to start, and to post and edit messages in a direct message. Replies to `/agent` do not use it: Slack sends a one-time reply URL with each command.

## Steps

**1. Create the app.**
[api.slack.com/apps](https://api.slack.com/apps) -> *Create New App* -> *From scratch*. Give it its own name (for example `Super Agent`) and pick your workspace.

**2. Turn on Socket Mode.**
*Settings -> Socket Mode* -> switch it on.

Then *Basic Information -> App-Level Tokens -> Generate Token and Scopes*, and add the scope `connections:write`. Copy the `xapp-...` value now into `SLACK_APP_TOKEN`. You cannot see it again.

Socket Mode is what makes this work on a laptop: the bot connects out to Slack, so Slack never needs to reach you. No public URL, no webhook, no tunnel.

**3. Create the command.**
*Features -> Slash Commands -> Create New Command*.

| Field | Value |
|---|---|
| Command | `/agent` |
| Short description | `Ask the Super Agent` |
| Usage hint | `[customer ID] [question]` |

If the form asks for a Request URL, enter any https URL (for example `https://example.com/slack/events`). With Socket Mode on, the command arrives over the websocket instead.

The name `/agent` is created here, in Slack's settings, not in the code. In the code, `@app.command("/agent")` in `slack_bot/app.py` only tells Bolt (the Python library that receives Slack's messages) which function to run for it. The two names must match. What you type after `/agent` is plain text. Slack does not read it: `slack_bot/parsing.py` finds the customer ID in it.

**4. Turn on direct messages.**
This is what lets a consultant hold a conversation with the bot.

- *Features -> Event Subscriptions* -> switch on -> *Subscribe to bot events* -> add `message.im`.
- *Features -> App Home -> Show Tabs* -> switch on **Messages Tab**. If Slack shows a checkbox to let users send messages from that tab, tick it.

**5. Add the scopes.**
*Features -> OAuth & Permissions -> Scopes -> Bot Token Scopes* should list:

| Scope | Why |
|---|---|
| `commands` | the `/agent` command (check it is there; add it if it is missing) |
| `chat:write` | post "Checking..." in a direct message and edit it into the answer |
| `im:history` | read the messages people send in a direct message (needed for `message.im`) |

**6. Install to the workspace.**
Everything so far only described the app. Installing is you granting it the permissions.

*OAuth & Permissions -> Install to Workspace*, then approve. **Now** the bot token exists: copy the **Bot User OAuth Token** (`xoxb-...`) from that page into `SLACK_BOT_TOKEN`.

If you change the command or the scopes later, Slack shows a reinstall banner. Reinstalling gives a new `xoxb-` token, so update `.env`.

**7. Optional: limit who can use it.**
Right-click the channel -> *View channel details*. The ID (`C0123ABCDEF`) is at the bottom. Put it in `SLACK_ALLOWED_CHANNELS`, with commas between several. This limits `/agent` only. Without it, `/agent` works in any channel or direct message.

To limit *people*, put Slack user IDs in `SLACK_ALLOWED_USERS` (click a person -> *View full profile* -> the three dots -> *Copy member ID*). It applies to `/agent` and to direct messages. **A direct message works from anywhere in the workspace, so without this list anyone who can message the bot can look up any customer.**

## Run it

Neo4j must be running with the graph and articles loaded, and the Anthropic and OpenAI keys must be in `.env` (see the main README).

```bash
uv run python -m slack_bot
```

It prints `Connected as @... in ...` and keeps running. In Slack, type:

```
/agent CUST-1005 customer cannot activate roaming
```

You should see "Checking...", then it is replaced by `Escalate to Tier 2 - unresolved by standard checks` and `Sources: ACT-015, R012`. Only you see the reply. It is a one-off: the bot does not remember it.

For a conversation, open the bot in the app list and send it a direct message:

```
CUST-1005 customer cannot activate roaming      -> the answer
what does that rule say?                        -> a follow-up: no customer ID needed
why did it end up at Tier 2?                    -> another follow-up
CUST-1002 customer cannot activate roaming      -> a different customer starts a fresh conversation
```

A reply inside a thread in the direct message is a separate conversation, so you can keep several cases going.

## When nothing happens

Almost always one of these, in likelihood order:

1. **The bot is not running.** Slack shows an error when you press Enter. Start it, and check that Socket Mode is on.
2. **Tokens swapped or missing.** The bot exits with a message. `SLACK_APP_TOKEN` starts `xapp-`, and `SLACK_BOT_TOKEN` starts `xoxb-`.
3. **The app token has the wrong scope.** It needs `connections:write`. A scope cannot be added to an existing token, so generate a new one (step 2).
4. **A command or scope was changed but the app was not reinstalled.** Look for the reinstall banner.
5. **The reply says the command only works in one channel.** You set `SLACK_ALLOWED_CHANNELS`. Use the command there, or add this channel's ID.
6. **You see "Checking..." and nothing else.** The answer never arrived. Look at the terminal where the bot runs.
7. **The command errors in one particular channel.** Invite the app there with `/invite @your-app-name`.
8. **Direct messages get no reply.** Check that the Messages Tab is on (step 4), that `message.im` is subscribed, that `im:history` and `chat:write` are listed (step 5), and that you reinstalled after adding them.
9. **The bot says "You are not set up to use this bot".** Your user ID is not in `SLACK_ALLOWED_USERS`.

## How this differs from llm_wiki's setup

| | llm_wiki | this repo |
|---|---|---|
| How you talk to the bot | You type any message in one channel | `/agent` for one question, or a direct message for a conversation |
| Slack events subscribed | `message.channels` or `message.groups` | `message.im` |
| Bot scopes | `chat:write`, and a `*:history` scope | `commands`, `chat:write`, `im:history` |
| Channel ID | required (it listens to that channel only) | optional (a limit on where `/agent` works) |
| Must invite the bot to the channel | yes | not known; only if a channel gives an error |
| Replies | in a thread, kept | `/agent`: private, not kept after a reload. Direct message: normal messages, kept |
