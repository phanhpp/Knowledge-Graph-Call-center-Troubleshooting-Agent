"""Saved conversations for Slack direct messages: send a message to the agent on one conversation."""

import threading
from typing import Optional

from langchain_core.messages import HumanMessage

from agent.state import reply_of


class AgentChat:
    """Talks to the supervisor graph (compiled with a checkpointer), one conversation per thread id.

    A conversation is one case. Its customer is remembered, so only the first message needs the
    customer ID. If a later message names a different customer, the conversation is deleted and
    started again for that customer, so two cases never mix.
    """

    def __init__(self, graph, checkpointer):
        """`graph` is build_supervisor_graph(checkpointer=checkpointer); both are needed, the second
        to delete a conversation."""
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
        """The customer this conversation is about, or None if it has none yet."""
        return self.graph.get_state(self._config(thread_id)).values.get("customer_id")

    def send(self, thread_id: str, text: str, customer_id: Optional[str]) -> dict:
        """Send one consultant message and return the agent's reply as {"answer", "citations"}.

        Pass customer_id on the first message (and to switch customer). Leave it None to carry on with
        the customer the conversation already has.
        """
        with self._lock_for(thread_id):
            saved = self.known_customer(thread_id)
            if customer_id and saved and customer_id != saved:
                self.checkpointer.delete_thread(thread_id)
            update = {"messages": [HumanMessage(text)]}
            if customer_id:
                update["customer_id"] = customer_id
            state = self.graph.invoke(update, self._config(thread_id))
        reply = reply_of(state)
        return {"answer": reply.text, "citations": reply.citations}
