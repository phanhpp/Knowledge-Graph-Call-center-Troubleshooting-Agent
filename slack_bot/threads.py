"""Which saved conversation a Slack direct message belongs to."""


def thread_id_for(channel: str, thread_ts: str | None) -> str:
    """The conversation id (LangGraph `thread_id`) for a message in a direct-message channel.

    A top-level message belongs to the DM's main conversation, `slack-<channel>`. A reply inside a
    thread in that DM is a conversation of its own, `slack-<channel>-<thread_ts>`, so a consultant can
    keep several cases going side by side.
    """
    return f"slack-{channel}-{thread_ts}" if thread_ts else f"slack-{channel}"
