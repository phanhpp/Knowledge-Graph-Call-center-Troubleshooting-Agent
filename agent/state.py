"""State schemas for the Super Agent LangGraph.

SuperAgentState is the top-level Supervisor state. It extends MessagesState, which brings the
`messages` key: the conversation, added to by LangGraph's `add_messages` reducer. The agent's reply
lives there and only there: each path returns it as an AIMessage (see agent/utils.py reply()), with
its citations in the message's `additional_kwargs`. There is no separate `answer` field.

With a checkpointer and a thread_id the whole state is saved between calls. That is what lets a
follow-up see the earlier messages and the current customer. The other fields are handed from node
to node within one turn, and `error`, which could go stale, is reset at the start of a turn (see
check_customer in agent/supervisor.py).

One conversation can cover several customers and general questions. `customer_id` is the current
customer: the last valid ID named. A consultant message is stamped with the customer it is about
(`additional_kwargs["customer_id"]`): the ID it names (stamped by the caller, see
agent/utils.py consultant_message), or, for a troubleshooting follow-up naming none, the current
customer (stamped by load_crm). An unstamped message was a general question. Each reply belongs to
the consultant message before it. So `messages` alone records what was asked and answered, and for
whom; explain_answer reads it from there.

ProceduralExecutorState is the procedure-walk subgraph's own state. It shares `messages` with the
Supervisor so the walk can return its reply. That is safe with a reducer here: `add_messages` merges
by message id, so the messages the subgraph hands back are not added a second time. Its
`step_trace` (every step, in full) is private to the walk: it is not in SuperAgentState, so it is
never saved; resolve turns it into the reply's checklist.
"""

from typing import Literal, Optional

from langgraph.graph import MessagesState


class SuperAgentState(MessagesState, total=False):
    """Top-level Supervisor state, shared by every node in the main graph.

    `messages` comes from MessagesState: the consultant's messages and the agent's replies.
    """

    # kept between turns
    customer_id: str  # the customer currently being discussed (the last ID named)

    # this turn
    crm_context: dict  # the customer's record, loaded by load_crm (troubleshooting only)
    # the path classify_intent chose; the last three end the turn with a fixed reply
    intent: Literal["troubleshooting", "open_ended", "explain", "irrelevant", "unclear", "injection"]
    matched_procedure_id: Optional[str]  # which procedure to walk: set by match_procedure, else None
    error: Optional[str]  # set when a node fails, so the request stops with a message


class ProceduralExecutorState(MessagesState, total=False):
    """The troubleshooting subgraph's state: keys shared with the Supervisor, plus its loop fields.

    `messages` (from MessagesState) is shared: the walk returns its reply through it.
    """

    # shared with the Supervisor
    matched_procedure_id: Optional[str]  # in: which procedure to walk
    crm_context: dict  # in: the customer's record
    error: Optional[str]  # out: set if no step or no action was found

    # the walk's own fields
    current_step_id: int  # the step being checked (unset means step 1)
    current_condition: dict  # that step's condition, from conditions.json
    action_if_true_id: Optional[str]  # the action on YES
    action_if_false_id: Optional[str]  # the action on NO (last step only)
    next_step_id: Optional[int]  # where NO goes (None on the last step)
    condition_result: Optional[bool]  # this step's answer: True is YES
    step_trace: list[dict]  # every step checked, in full; becomes the reply's checklist

