"""Writing and reading the agent's reply.

A reply is stored as one AIMessage in `messages`, with its citations in the message's
`additional_kwargs`. These two functions are the only code that knows that format:

    reply(text, citations)  used by graph nodes to put the answer into state
    reply_of(state)         used outside the graph (the Slack bot, tests) to get it back out
"""

from langchain_core.messages import AIMessage

from agent.data_model import Reply


def reply(text: str, citations: list[str] | None = None) -> dict:
    """The state update for the agent's reply: one AIMessage, with its citations on it.

    Every path returns this (or merges it into a larger update) instead of writing an `answer` field.
    """
    return {"messages": [AIMessage(content=text, additional_kwargs={"citations": list(citations or [])})]}


def reply_of(state: dict) -> Reply:
    """The agent's latest reply in `state` (the last message): its text and its citations."""
    message = state["messages"][-1]
    return Reply(str(message.content), list(message.additional_kwargs.get("citations", [])))
