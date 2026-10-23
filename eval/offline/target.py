"""Sends one golden example through the real agent, in the shape `langsmith.evaluate` expects.

An example is one message ({"message", "customer_id"}) or a conversation
({"turns": [{"text", "customer_id"}, ...]}). Each example gets its own new thread, so examples never
see each other; the turns of one conversation do see the earlier turns, as in Slack or the web app.

For each turn it returns what the evaluators look at:
  - the reply: text, citations, sources;
  - where the turn ended up: intent (the path), matched procedure, current customer, error;
  - model_calls: how many model calls (Claude and Jev) the turn made, counted by a callback.

Needs what the agent needs: Neo4j with the graph and articles loaded, and the model keys.
"""

import uuid
from functools import lru_cache
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langgraph.checkpoint.memory import InMemorySaver

from agent.supervisor import build_supervisor_graph
from agent.utils import consultant_message


class _ModelCalls(BaseCallbackHandler):
    """Counts the model calls in one turn. LangChain calls on_chat_model_start for chat models
    (Claude) and on_llm_start for other models (Jev), so counting both covers every call."""

    def __init__(self) -> None:
        self.count = 0

    def on_chat_model_start(self, *args: Any, **kwargs: Any) -> None:
        self.count += 1

    def on_llm_start(self, *args: Any, **kwargs: Any) -> None:
        self.count += 1


@lru_cache(maxsize=1)
def _graph():
    """The agent graph, built once and shared by every example (each example uses its own thread)."""
    return build_supervisor_graph(checkpointer=InMemorySaver())


def _turn(thread_id: str, text: str, customer_id: str | None) -> dict:
    """Send one consultant message on this thread and return what the evaluators look at."""
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
    """Run one example on a new thread. A single message returns one turn's output; a
    conversation returns {"turns": [one output per turn]}."""
    thread_id = str(uuid.uuid4())
    if "turns" in inputs:
        return {"turns": [_turn(thread_id, t["text"], t.get("customer_id")) for t in inputs["turns"]]}
    return _turn(thread_id, inputs["message"], inputs.get("customer_id"))
