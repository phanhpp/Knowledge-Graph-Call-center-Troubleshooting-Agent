"""Sends one golden example through the real agent, in the shape `langsmith.evaluate` expects.

An example is one message ({"message", "customer_id"}) or a conversation
({"turns": [{"text", "customer_id"}, ...]}). Each example gets its own new thread, so examples never
see each other; the turns of one conversation do see the earlier turns, as in Slack or the web app.

For each turn it returns what the evaluators look at:
  - the reply: text, citations, sources, and for an explanation the files it read (looked_up);
  - where the turn ended up: intent (the path), matched procedure, current customer, error;
  - claude_calls, jev_calls: how many model calls the turn made, counted by a callback (see
    _ModelCalls for what it can and can't see).

Needs what the agent needs: Neo4j with the graph and articles loaded, and the model keys.
"""

import threading
import uuid
from functools import lru_cache
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langgraph.checkpoint.memory import InMemorySaver

from agent.supervisor import build_supervisor_graph
from agent.utils import consultant_message


class _ModelCalls(BaseCallbackHandler):
    """Counts the model calls in one turn, through LangChain's callbacks:
      - Claude via LangChain (explain, reading a ticket): on_chat_model_start;
      - Jev: TypeSafeClassifier runs as a Runnable, which LangChain reports through on_chain_start
        (not on_llm_start), named "TypeSafeClassifier" with run_type "llm". The retry wrapper
        around it reports the same name as a plain chain, so only run_type "llm" is counted. Each
        retry of a Jev call counts.
    Not counted: the knowledge-base answer, which neo4j-graphrag sends to Claude with its own client,
    outside LangChain. So these counts suit checks like "stopped before any model call"; they are not
    a bill."""

    def __init__(self) -> None:
        self.claude = 0
        self.jev = 0

    def on_chat_model_start(self, *args: Any, **kwargs: Any) -> None:
        self.claude += 1

    def on_chain_start(self, serialized: Any, inputs: Any, **kwargs: Any) -> None:
        if kwargs.get("name") == "TypeSafeClassifier" and kwargs.get("run_type") == "llm":
            self.jev += 1


_GRAPH_LOCK = threading.Lock()


@lru_cache(maxsize=1)
def _build_graph():
    return build_supervisor_graph(checkpointer=InMemorySaver())


def _graph():
    """The agent graph, built once and shared by every example (each example uses its own thread).

    Built under a lock: evaluate() runs examples in parallel threads, and lru_cache alone lets two
    threads that arrive together each build their own graph, with its own in-memory checkpointer.
    A conversation could then save turn 1 in one and read turn 2 from the other, and lose its
    history: "why?" became a first message, and the current customer was gone."""
    with _GRAPH_LOCK:
        return _build_graph()


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
        "looked_up": list(kwargs.get("looked_up", [])),
        "intent": state.get("intent"),
        "matched_procedure_id": state.get("matched_procedure_id"),
        "customer_id": state.get("customer_id"),
        "error": state.get("error"),
        "claude_calls": calls.claude,
        "jev_calls": calls.jev,
    }


def target(inputs: dict) -> dict:
    """Run one example on a new thread. A single message returns one turn's output; a
    conversation returns {"turns": [one output per turn]}."""
    thread_id = str(uuid.uuid4())
    if "turns" in inputs:
        return {"turns": [_turn(thread_id, t["text"], t.get("customer_id")) for t in inputs["turns"]]}
    return _turn(thread_id, inputs["message"], inputs.get("customer_id"))
