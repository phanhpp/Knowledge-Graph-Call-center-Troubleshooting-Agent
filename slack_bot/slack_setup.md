# Slack setup for `python -m slack_bot`

**Slack is optional.** The agent works without it (`graph.invoke(...)`, the tests). Only `python -m slack_bot` needs it.

You have to create your own Slack app. The tokens are secrets, so they cannot be shared or committed.

**Create a new app for this repo, even if you already have one for another project (for example llm_wiki).** A token identifies one app. If two programs connect with the same app token, Slack sends each command to either of them, with no fixed pattern, so commands would randomly go to the wrong program. Using the same workspace, and even the same channel, is fine.

Budget about fifteen minutes. Most of the steps fail silently if you skip one, so use the checklist at the bottom.

## What you end up with

Two required values in your `.env`, and up to three optional ones:

```
SLACK_BOT_TOKEN=xoxb-...              # required: lets the bot post and edit messages
SLACK_APP_TOKEN=xapp-...              # required: opens the connection to Slack (Socket Mode)
SLACK_MEMBERS_CHANNEL=C0123ABCDEF     # recommended: only members of this channel may use the bot
SLACK_ALLOWED_USERS=U0123ABCDEF       # optional: these people may use it too
SLACK_ALLOWED_CHANNELS=C0123ABCDEF    # optional: channels where /agent may be typed
```

> **The two tokens come from two different pages.** `xapp-` is an *app-level* token that you generate under Basic Information. `xoxb-` is a *bot* token that only exists after you install the app. They are not interchangeable, and the bot refuses to start if they are swapped.

- The **app token** belongs to the app, not to one workspace. Without it the bot receives nothing.
- The **bot token** belongs to one workspace. The bot needs it to start, to post and edit messages in a direct message, and to read the members channel. Replies to `/agent` do not use it: Slack sends a one-time reply URL with each command.



## Who can use the bot

The bot shows customer data, so decide this before anyone else can reach it.

**Recommended: one channel decides.** Set `SLACK_MEMBERS_CHANNEL` to your consultants' channel (Private channel), for example `#support-consultants`. Whoever is a member of that channel may use the bot, through `/agent` and through direct messages. You manage access in Slack itself:

- add someone to the channel to give them access;
- remove them to take it away.

The bot re-reads the member list every 5 minutes, so a change takes up to 5 minutes to apply.

This is the only way to limit **direct messages** by channel. A direct message isn't sent in any channel, so there is no channel to check. What the bot checks instead is whether the sender is a member of the channel.

**The other two settings:**

- `SLACK_ALLOWED_USERS` adds people by user ID, on top of the channel's members. Use it for someone who shouldn't be in the channel, such as a supervisor. Used alone, without a members channel, it is the complete list of who may use the bot.
- `SLACK_ALLOWED_CHANNELS` is about **where** `/agent` **may be typed**, not who may use the bot. It does not affect direct messages.

**With neither** `SLACK_MEMBERS_CHANNEL` **nor** `SLACK_ALLOWED_USERS` **set, anyone in the workspace can DM the bot and look up any customer.** The bot says so when it starts.

All three settings take **IDs, not names**. A channel can be renamed, but its ID never changes. Step 7 shows where to find them.

## Steps

**1. Create the app.**
[api.slack.com/apps](https://api.slack.com/apps) -> *Create New App* -> *From scratch*. Give it its own name (for example `Super Agent`) and pick your workspace.

**2. Turn on Socket Mode.**
*Settings -> Socket Mode* -> switch it on.

Then *Basic Information -> App-Level Tokens -> Generate Token and Scopes*, and add the scope `connections:write`. Copy the `xapp-...` value now into `SLACK_APP_TOKEN`. You cannot see it again.

Socket Mode is what makes this work on a laptop: the bot connects out to Slack, so Slack never needs to reach you. No public URL, no webhook, no tunnel.

**3. Create the command.**
*Features -> Slash Commands -> Create New Command*.


| Field             | Value                      |
| ----------------- | -------------------------- |
| Command           | `/agent`                   |
| Short description | `Ask the Super Agent`      |
| Usage hint        | `[customer ID] [question]` |


The name `/agent` is created here, in Slack's settings, not in the code. In the code, `@app.command("/agent")` in `slack_bot/app.py` only tells Bolt (the Python library that receives Slack's messages) which function to run for it. The two names must match. What you type after `/agent` is plain text. Slack does not read it: `slack_bot/parsing.py` finds the customer ID in it.

**4. Turn on direct messages.**
This is what lets a consultant hold a conversation with the bot.

In Slack's settings, **"im" means a direct message** (short for "instant message"). `message.im` is the event Slack sends the bot when someone direct-messages it, and `im:history` (step 5) is the permission to read what they wrote.

Note: With Socket Mode on, the command arrives over the Socket Mode connection so NO NEED to specify a Request URL (withut socket model, Slack need an URL to post events to)

- *Features -> Event Subscriptions* -> switch on -> *Subscribe to bot events* -> add `message.im`.
- *Features -> App Home -> Show Tabs* -> switch on  **Messages Tab**. If Slack shows a checkbox to let users send messages from that tab, tick it.

**5. Add the scopes.**
*Features -> OAuth & Permissions -> Scopes -> Bot Token Scopes* should list:


| Scope           | Why                                                                         |
| --------------- | --------------------------------------------------------------------------- |
| `commands`      | the `/agent` command (check it is there; add it if it is missing)           |
| `chat:write`    | post "Checking..." in a direct message and edit it into the answer          |
| `im:history`    | read the messages people send in a direct message (needed for `message.im`) |
| `channels:read` | read who is in the members channel, if it is **public**                     |
| `groups:read`   | read who is in the members channel, if it is **private**                    |


You only need the `*:read` scope that matches your members channel. Skip both if you don't set `SLACK_MEMBERS_CHANNEL`.

**6. Install to the workspace.**
Everything so far only described the app. Installing is you granting it the permissions.

*OAuth & Permissions -> Install to Workspace*, then approve. **Now** the bot token exists: copy the **Bot User OAuth Token** (`xoxb-...`) from that page into `SLACK_BOT_TOKEN`.

If you change the command or the scopes later, Slack shows a reinstall banner. Reinstalling gives a new `xoxb-` token, so update `.env`.

**7. Set who can use it.** (See [Who can use the bot](#who-can-use-the-bot).)

- **Members channel.** Open the channel, click its name at the top, and find the channel ID (`C0123ABCDEF`) at the bottom of the About tab. Put it in `SLACK_MEMBERS_CHANNEL`; only one channel is allowed. Then add the bot to that channel: type `/invite @your-app-name` in it. A private channel requires this, and it does no harm for a public one.
- **Extra people (optional).** Click a person -> *View full profile* -> the three dots -> *Copy member ID* (`U0123ABCDEF`). Put the IDs in `SLACK_ALLOWED_USERS`, with commas between them.
- **Where** `/agent` **works (optional).** Put channel IDs in `SLACK_ALLOWED_CHANNELS`, with commas between them. Without it, `/agent` works in any channel or direct message.



## Run it

Neo4j must be running with the graph and articles loaded, and the Anthropic, OpenAI and TypeSafe keys must be in `.env` (see the main README).

```bash
uv run python -m slack_bot
```

It prints who has access, for example `Access: the 12 members of C0123ABCDEF.`, then `Connected as @... in ...`, and keeps running. If it can't read the members channel, it stops with a message instead of starting.

In Slack, type:

```
/agent CUST-1001 customer cannot activate roaming
```

You should see "Checking...", then it is replaced by `Escalate to Tier 2 - unresolved by standard checks`, the reason, the three steps checked, and `Sources: ACT-015, R012`. Only you see the reply. It is a one-off: the bot does not remember it.

For a conversation, open the bot in the app list and send it a direct message:

```
CUST-1005 customer cannot activate roaming      -> the answer
what does that rule say?                        -> a follow-up: no customer ID needed
why did it end up at Tier 2?                    -> another follow-up
CUST-1002 customer cannot activate roaming      -> switches to CUST-1002; CUST-1005's answer is kept
```

A reply inside a thread in the direct message is a separate conversation, so you can keep several cases going.

## When nothing happens

Almost always one of these, in likelihood order:

1. **The bot is not running.** Slack shows an error when you press Enter. Start it, and check that Socket Mode is on.
2. **Tokens swapped or missing.** The bot exits with a message. `SLACK_APP_TOKEN` starts `xapp-`, and `SLACK_BOT_TOKEN` starts `xoxb-`.
3. **The app token has the wrong scope.** It needs `connections:write`. A scope cannot be added to an existing token, so generate a new one (step 2).
4. **A command or scope was changed but the app was not reinstalled.** Look for the reinstall banner.
5. **The bot stops at start with "Could not read the members of SLACK_MEMBERS_CHANNEL".** Check, in this order:
  - the channel ID is right (it starts with `C`, not `#`);
  - the matching scope is added (`channels:read` for a public channel, `groups:read` for a private one) and the app was reinstalled;
  - the bot is in the channel (`/invite @your-app-name`).
6. **The bot says "You are not set up to use this bot".** You are not in the members channel, and your user ID is not in `SLACK_ALLOWED_USERS`. If you were only just added to the channel, wait up to 5 minutes.
7. **The reply says the command only works in one channel.** You set `SLACK_ALLOWED_CHANNELS`. Use the command there, or add this channel's ID.
8. **You see "Checking..." and nothing else.** The answer never arrived. Look at the terminal where the bot runs.
9. **The command errors in one particular channel.** Invite the app there with `/invite @your-app-name`.
10. **Direct messages get no reply.** Check that the Messages Tab is on (step 4), that `message.im` is subscribed, that `im:history` and `chat:write` are listed (step 5), and that you reinstalled after adding them.



## How this differs from llm_wiki's setup


|                                    | llm_wiki                               | this repo                                                                                            |
| ---------------------------------- | -------------------------------------- | ---------------------------------------------------------------------------------------------------- |
| How you talk to the bot            | You type any message in one channel    | `/agent` for one question, or a direct message for a conversation                                    |
| Slack events subscribed            | `message.channels` or `message.groups` | `message.im`                                                                                         |
| Bot scopes                         | `chat:write`, and a `*:history` scope  | `commands`, `chat:write`, `im:history`, and `channels:read` or `groups:read` for the members channel |
| What the channel ID is for         | the one channel it listens to          | `SLACK_MEMBERS_CHANNEL`: whose members may use the bot. The bot doesn't read messages there.         |
| Must invite the bot to the channel | yes                                    | yes, to the members channel (required if it is private)                                              |
| Replies                            | in a thread, kept                      | `/agent`: private, not kept after a reload. Direct message: normal messages, kept                    |


