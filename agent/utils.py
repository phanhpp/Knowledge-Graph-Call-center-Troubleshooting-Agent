"""The message formats: the consultant's message going in, and the agent's reply coming out.

A consultant message names the customer it is about in its `additional_kwargs` (absent for a
general question). A reply is stored as one AIMessage in `messages`; its `additional_kwargs` hold
its citations (the IDs), their sources (the text behind each ID, for the web app's Sources panel)
and the run that produced it (for 👍👎 feedback). These functions are the only code that knows
those formats:

    consultant_message(text, customer_id)  used by callers (the Slack bot, tests) to send a message
    reply(text, citations)                 used by graph nodes to put the answer into state
    reply_of(state)                        used outside the graph to get the answer back out
"""

from typing import Optional

from langchain_core.messages import AIMessage, HumanMessage
from langsmith.run_helpers import get_current_run_tree

from agent.data_model import Reply
from agent.sources import sources_for


def consultant_message(text: str, customer_id: Optional[str] = None) -> HumanMessage:
    """The consultant's message, stamped with the customer ID it names (none for a general question).

    The graph checks a named ID exists (check_customer) and makes it the current customer.
    """
    return HumanMessage(text, additional_kwargs={"customer_id": customer_id} if customer_id else {})


def reply(text: str, citations: list[str] | None = None, looked_up: list[str] | None = None) -> dict:
    """The state update for the agent's reply: one AIMessage, with its citations on it.

    Every path returns this (or merges it into a larger update) instead of writing an `answer` field.
    On the message:
      - `citations`: the cited IDs, unchanged (the Slack bot and the citations_exist check read them);
      - `sources`: the text behind each ID (agent/sources.py), saved with the message so a reload
        still has it;
      - `run_id`: the turn's root run in LangSmith, so a 👍👎 on this reply lands on the run that
        produced it. Read here because it exists only while the turn runs; inside any node, even in
        the walk's subgraph, the trace id is the turn's root. Absent when tracing is off.
      - `looked_up`: an explanation's files it read (agent/explain_tools.py), e.g.
        ["articles/KB-006.md"], so a judge can see what it was based on. Absent on other replies.
    """
    ids = list(citations or [])
    kwargs = {"citations": ids, "sources": sources_for(ids)}
    if looked_up:
        kwargs["looked_up"] = list(looked_up)
    run = get_current_run_tree()
    if run is not None:
        kwargs["run_id"] = str(run.trace_id)
    return {"messages": [AIMessage(content=text, additional_kwargs=kwargs)]}


def reply_of(state: dict) -> Reply:
    """The agent's latest reply in `state` (the last message): its text and its citations."""
    message = state["messages"][-1]
    return Reply(str(message.content), list(message.additional_kwargs.get("citations", [])))
