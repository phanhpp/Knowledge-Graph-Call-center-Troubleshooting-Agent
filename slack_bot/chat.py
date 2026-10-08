"""Saved conversations for Slack direct messages: send a message to the agent on one conversation."""

import threading
from typing import Optional

from langchain_core.messages import HumanMessage

from agent.state import reply_of


class AgentChat:
    """Talks to the supervisor graph (compiled with a checkpointer), one conversation per thread id.

    A conversation (one thread id) is saved by the graph's checkpointer, so it survives across
    messages and restarts. It can cover several customers: the last customer named is the current
    one, so a follow-up needs no ID. Naming a different customer switches to them and keeps
    everything said so far; the agent keeps each customer's last troubleshooting result, so coming
    back to an earlier customer still works.
    """

    def __init__(self, graph, checkpointer):
        """`graph` is build_supervisor_graph(checkpointer=checkpointer): the graph saves and loads
        each conversation through that checkpointer."""
        self.graph = graph
        self.checkpointer = checkpointer
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    def _lock_for(self, thread_id: str) -> threading.Lock:
        """The lock for one conversation. Two quick messages must not run on the same one at once."""
        with self._guard:
            return self._locks.setdefault(thread_id, threading.Lock())

    @staticmethod
    def _config(thread_id: str) -> dict:
        """The LangGraph config that selects one saved conversation."""
        return {"configurable": {"thread_id": thread_id}}

    def known_customer(self, thread_id: str) -> Optional[str]:
        """The customer currently being discussed in this conversation, or None if none yet."""
        return self.graph.get_state(self._config(thread_id)).values.get("customer_id")

    def send(self, thread_id: str, text: str, customer_id: Optional[str]) -> dict:
        """Send one consultant message and return the agent's reply as {"answer", "citations"}.

        Pass customer_id on the first message, and to switch to another customer (the conversation
        is kept). Leave it None to carry on with the current customer.
        """
        with self._lock_for(thread_id):
            update = {"messages": [HumanMessage(text)]}
            if customer_id:
                update["customer_id"] = customer_id
            state = self.graph.invoke(update, self._config(thread_id))
        reply = reply_of(state)
        return {"answer": reply.text, "citations": reply.citations}
