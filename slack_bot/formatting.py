"""What the bot posts to Slack: the agent's answer as a Slack message, plus the fixed messages."""

import re

WORKING = "Checking..."
MISSING_QUESTION = "Please add your question after the customer ID, for example: `/agent CUST-1001 customer cannot activate roaming`"
WRONG_CHANNEL = "This command can only be used in the channel set up for the Super Agent."
MISSING_QUESTION_DM = "Please add your question after the customer ID, for example: `CUST-1001 customer cannot activate roaming`"
NOT_ALLOWED = "You are not set up to use this bot."
NO_ANSWER = "I couldn't produce an answer for this request."
ERROR_REPLY = "Something went wrong while answering. Please try again."


def to_slack_markdown(text: str) -> str:
    """Convert Claude's Markdown to Slack's: `**bold**` becomes `*bold*`, and `# Heading` lines
    become bold lines, since Slack has no headings."""
    text = re.sub(r"\*\*(.+?)\*\*", r"*\1*", text)
    return re.sub(r"^#{1,6}\s+(.+)$", r"*\1*", text, flags=re.MULTILINE)


def format_reply(result: dict) -> str:
    """Build the Slack message for a finished agent run: the answer, then the citation IDs.

    `result` is the final state of the supervisor graph. A run with no answer gives NO_ANSWER.
    """
    answer = result.get("answer")
    if not answer:
        return NO_ANSWER
    reply = to_slack_markdown(answer)
    citations = result.get("citations") or []
    if citations:
        reply += f"\n\n_Sources: {', '.join(citations)}_"
    return reply
