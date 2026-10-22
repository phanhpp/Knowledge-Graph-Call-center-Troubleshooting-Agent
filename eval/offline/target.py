"""Runs one golden example through the real agent graph, end to end, for `langsmith.evaluate`.

An example's inputs are either one message ({"message", "customer_id"}) or a conversation
({"turns": [{"text", "customer_id"}, ...]}). Each runs on a fresh thread of an in-process graph
with an in-memory checkpointer, so follow-ups see the earlier turns, exactly as in Slack or the web
app. Needs what the agent needs: Neo4j with the graph and articles loaded, and the model keys.

The output of a turn is what the evaluators read: the reply (text, citations, sources), the state
the turn ended in (intent, matched procedure, current customer, error), and how many model calls it
made (Claude and Jev), counted with a LangChain callback.
"""

import uuid
from functools import lru_cache
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langgraph.checkpoint.memory import InMemorySaver

from agent.supervisor import build_supervisor_graph
from agent.utils import consultant_message


class _ModelCalls(BaseCallbackHandler):
    """Counts model calls (chat models and other LLMs, which covers Claude and Jev) in one run."""

    def __init__(self) -> None:
        self.count = 0

    def on_chat_model_start(self, *args: Any, **kwargs: Any) -> None:
        self.count += 1

    def on_llm_start(self, *args: Any, **kwargs: Any) -> None:
        self.count += 1


@lru_cache(maxsize=1)
def _graph():
    """One compiled graph for the whole run; every example gets its own thread."""
    return build_supervisor_graph(checkpointer=InMemorySaver())


def _turn(thread_id: str, text: str, customer_id: str | None) -> dict:
    """Send one consultant message and return what the evaluators read."""
    calls = _ModelCalls()
    state = _graph().invoke(
        {"messages": [consultant_message(text, customer_id)]},
        {"configurable": {"thread_id": thread_id}, "callbacks": [calls]},
    )
    reply = state["messages"][-1]
    kwargs = reply.additional_kwargs
    return {
        "text": str(reply.content),
        "citations": list(kwargs.get("citations", [])),
        "sources": list(kwargs.get("sources", [])),
        "intent": state.get("intent"),
        "matched_procedure_id": state.get("matched_procedure_id"),
        "customer_id": state.get("customer_id"),
        "error": state.get("error"),
        "model_calls": calls.count,
    }


def target(inputs: dict) -> dict:
    """One example: a single message -> one turn's output; a conversation -> {"turns": [...]}."""
    thread_id = str(uuid.uuid4())
    if "turns" in inputs:
        return {"turns": [_turn(thread_id, t["text"], t.get("customer_id")) for t in inputs["turns"]]}
    return _turn(thread_id, inputs["message"], inputs.get("customer_id"))
