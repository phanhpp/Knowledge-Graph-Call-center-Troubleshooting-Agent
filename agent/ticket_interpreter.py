"""Ticket interpreter — the one genuinely LLM-necessary evaluation path.

Structured CRM fields (roaming_flag, plan_status, ...) are always answerable
by a deterministic field/operator/value check (mock_crm/condition_evaluator.py)
— no amount of chaining changes that, see its module docstring. 

This module exists for the one condition type that isn't: interpreting a free-text
ticket description, which a plain `field == value` check cannot do.

This is also the one place "Replanning" (Vodafone's LLM Compiler fallback
loop) is real rather than decorative: the first pass sees only the ticket's
free text; if the LLM isn't confident, the second (and final) pass also gets
the ticket's structured `resolved` flag as extra grounding evidence. Every
other condition in this project never needs this because the deterministic
path is never ambiguous to begin with.
"""

from typing import Literal, Optional, TypedDict

from langchain_anthropic import ChatAnthropic
from langgraph.graph import START, StateGraph
from langgraph.types import Command
from pydantic import BaseModel, Field

_MODEL = "claude-sonnet-5"


class TicketInterpreterState(TypedDict, total=False):
    ticket: dict
    interpretation_question: str
    broadened: bool
    relevant: Optional[bool]


class _Classification(BaseModel):
    relevant: bool = Field(
        description="whether the ticket description answers the interpretation question affirmatively"
    )
    confidence: Literal["high", "low"] = Field(
        description="'low' if the free text alone is too vague to be sure"
    )


def classify(state: TicketInterpreterState) -> Command[Literal["classify", "__end__"]]:
    """LLM call: interpret the ticket's free text.

    Retries once — with the ticket's structured `resolved` flag added as
    extra grounding evidence — if the first pass isn't confident. Capped at
    one retry: the second pass's result is always final, confident or not.
    """
    broadened = state.get("broadened", False)
    ticket = state["ticket"]
    context = f"Ticket description: {ticket['description']}"
    if broadened:
        context += f"\nTicket marked resolved in our system: {ticket['resolved']}"

    # No explicit temperature — claude-sonnet-5 rejects it ("temperature is
    # deprecated for this model"), unlike older Claude models.
    # "nostream": an internal call, not a reply. Without the tag LangGraph streams its output to clients
    # as a chat message of its own (an empty bubble in the web app).
    llm = ChatAnthropic(model=_MODEL, tags=["nostream"]).with_structured_output(_Classification)
    result: _Classification = llm.invoke(f"{state['interpretation_question']}\n\n{context}")

    if result.confidence == "high" or broadened:
        return Command(update={"relevant": result.relevant}, goto="__end__")
    return Command(update={"broadened": True}, goto="classify")


def build_ticket_interpreter_graph():
    """classify -> (confident: done) | (unsure: broaden context, retry classify once)."""
    builder = StateGraph(TicketInterpreterState)
    builder.add_node("classify", classify)
    builder.add_edge(START, "classify")
    return builder.compile()
