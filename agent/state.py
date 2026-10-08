"""State schemas for the Super Agent LangGraph.

SuperAgentState is the top-level Supervisor state. It extends MessagesState, which brings the
`messages` key: the conversation, added to by LangGraph's `add_messages` reducer. The agent's reply
lives there and only there: each path returns it as an AIMessage (see reply()), with its citations
in the message's `additional_kwargs`. There is no separate `answer` field.

With a checkpointer and a thread_id the whole state is saved between calls. That is what lets a
follow-up see the earlier messages, the current customer, and each customer's last procedure result.
The other fields are handed from node to node within one turn, and `error`, which could go stale, is
reset at the start of a turn (see inject_crm in agent/supervisor.py).

One conversation can cover several customers: `customer_id` is the one currently being discussed
(the last ID named), and `troubleshooting_results` keeps a result per customer, so coming back to
an earlier customer still finds what their last walk found.

ProceduralExecutorState is the procedure-walk subgraph's own state. It shares `messages` with the
Supervisor so the walk can return its reply. That is safe with a reducer here: `add_messages` merges
by message id, so the messages the subgraph hands back are not added a second time. Its
`step_trace` (every step, in full) is private to the walk: it is not in SuperAgentState, so it is
never saved; the walk hands back only the concise `troubleshooting_results` entry.
"""

from typing import Annotated, Literal, NamedTuple, Optional, TypedDict

from langchain_core.messages import AIMessage
from langgraph.graph import MessagesState


def merge_results(old: Optional[dict], new: Optional[dict]) -> dict:
    """Reducer for `troubleshooting_results`: a walk's update replaces only its own customer's entry."""
    return {**(old or {}), **(new or {})}


class SuperAgentState(MessagesState, total=False):
    """Top-level Supervisor state, shared by every node in the main graph.

    `messages` comes from MessagesState: the consultant's messages and the agent's replies.
    """

    # kept between turns
    customer_id: str  # the customer currently being discussed (the last ID named)
    # customer_id -> what that customer's last walk found, for explain_answer:
    #   {"procedure_id", "action_id", "rule_ids", "checks": ["<step>: yes/no (<value>)", ...]}
    # None if their last walk failed. Written by the walk's resolve / handle_fetch_error.
    troubleshooting_results: Annotated[dict[str, Optional[dict]], merge_results]

    # this turn
    crm_context: dict  # the customer's record, loaded by inject_crm
    intent: Literal["troubleshooting", "open_ended", "explain"]  # the path classify_intent chose
    matched_procedure_id: Optional[str]  # which procedure to walk: set by match_procedure, else None
    error: Optional[str]  # set when a node fails, so the request stops with a message


class ProceduralExecutorState(MessagesState, total=False):
    """The troubleshooting subgraph's state: keys shared with the Supervisor, plus its loop fields.

    `messages` (from MessagesState) is shared: the walk returns its reply through it.
    """

    # shared with the Supervisor
    matched_procedure_id: Optional[str]  # in: which procedure to walk
    crm_context: dict  # in: the customer's record
    troubleshooting_results: Annotated[dict[str, Optional[dict]], merge_results]  # out: this walk's result
    error: Optional[str]  # out: set if no step or no action was found

    # the walk's own fields
    current_step_id: int  # the step being checked (unset means step 1)
    current_condition: dict  # that step's condition, from conditions.json
    action_if_true_id: Optional[str]  # the action on YES
    action_if_false_id: Optional[str]  # the action on NO (last step only)
    next_step_id: Optional[int]  # where NO goes (None on the last step)
    condition_result: Optional[bool]  # this step's answer: True is YES
    step_trace: list[dict]  # every step checked, in full; becomes the reply's checklist and the result


def reply(text: str, citations: list[str] | None = None) -> dict:
    """The state update for the agent's reply: one AIMessage, with its citations on it.

    Every path returns this (or merges it into a larger update) instead of writing an `answer` field.
    """
    return {"messages": [AIMessage(content=text, additional_kwargs={"citations": list(citations or [])})]}


class Reply(NamedTuple):
    """The agent's reply: its text and the IDs it cites."""

    text: str
    citations: list[str]


def reply_of(state: dict) -> Reply:
    """The agent's latest reply in `state` (the last message): its text and its citations."""
    message = state["messages"][-1]
    return Reply(str(message.content), list(message.additional_kwargs.get("citations", [])))
