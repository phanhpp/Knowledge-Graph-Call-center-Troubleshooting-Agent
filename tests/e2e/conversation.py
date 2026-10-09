"""A helper for the follow-up tests: one saved conversation (one thread_id) on a compiled graph."""

from agent.utils import consultant_message


class Conversation:
    """One conversation with the agent. Several can share one graph, each with its own thread_id."""

    def __init__(self, graph, thread_id: str):
        """Bind a compiled supervisor graph (built with a checkpointer) to one thread_id."""
        self.graph = graph
        self.config = {"configurable": {"thread_id": thread_id}}

    def say(self, text: str, customer_id: str | None = None) -> dict:
        """Send one consultant message and return the final state.

        Give customer_id when the message names a customer (as the Slack bot stamps it). Leave it
        out for a general question, or a follow-up for the current customer.
        """
        return self.graph.invoke({"messages": [consultant_message(text, customer_id)]}, self.config)

    @property
    def saved(self) -> dict:
        """The state saved for this conversation right now."""
        return self.graph.get_state(self.config).values
