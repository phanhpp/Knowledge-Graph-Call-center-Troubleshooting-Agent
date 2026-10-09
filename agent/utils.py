"""The message formats: the consultant's message going in, and the agent's reply coming out.

A consultant message names the customer it is about in its `additional_kwargs` (absent for a
general question). A reply is stored as one AIMessage in `messages`, with its citations in its
`additional_kwargs`. These functions are the only code that knows those formats:

    consultant_message(text, customer_id)  used by callers (the Slack bot, tests) to send a message
    reply(text, citations)                 used by graph nodes to put the answer into state
    reply_of(state)                        used outside the graph to get the answer back out
"""

from typing import Optional

from langchain_core.messages import AIMessage, HumanMessage

from agent.data_model import Reply


def consultant_message(text: str, customer_id: Optional[str] = None) -> HumanMessage:
    """The consultant's message, stamped with the customer ID it names (none for a general question).

    The graph checks a named ID exists (check_customer) and makes it the current customer.
    """
    return HumanMessage(text, additional_kwargs={"customer_id": customer_id} if customer_id else {})


def reply(text: str, citations: list[str] | None = None) -> dict:
    """The state update for the agent's reply: one AIMessage, with its citations on it.

    Every path returns this (or merges it into a larger update) instead of writing an `answer` field.
    """
    return {"messages": [AIMessage(content=text, additional_kwargs={"citations": list(citations or [])})]}


def reply_of(state: dict) -> Reply:
    """The agent's latest reply in `state` (the last message): its text and its citations."""
    message = state["messages"][-1]
    return Reply(str(message.content), list(message.additional_kwargs.get("citations", [])))
