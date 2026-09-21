"""State schemas for the Super Agent LangGraph.

SuperAgentState is the top-level Supervisor state. ProceduralExecutorState
extends it with fields the troubleshooting subgraph needs internally
(current position in the step chain, the last-fetched condition, the loop
audit trail) that the Supervisor and Graph RAG path never touch.
"""

from typing import Annotated, Literal, Optional, TypedDict
import operator


class SuperAgentState(TypedDict, total=False):
    """Top-level Supervisor state, shared by every node in the main graph."""

    query: str
    customer_id: str
    crm_context: dict
    intent: Literal["troubleshooting", "open_ended"]
    matched_procedure_id: Optional[str]
    answer: Optional[str]
    citations: list[str]
    error: Optional[str]


class ProceduralExecutorState(SuperAgentState, total=False):
    """SuperAgentState plus the troubleshooting subgraph's internal loop fields."""

    current_step_id: int
    current_condition: dict
    action_if_true_id: Optional[str]
    action_if_false_id: Optional[str]
    next_step_id: Optional[int]
    condition_result: Optional[bool]
    steps_visited: Annotated[list[int], operator.add]
