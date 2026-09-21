"""Shared fixture for the end-to-end tests."""

import pytest
from langchain_core.messages import HumanMessage

from agent.supervisor import build_supervisor_graph


@pytest.fixture(scope="session")
def ask():
    """Return ask(query, customer_id) -> the final state of the full supervisor graph.

    The graph is compiled once for the whole test session.
    """
    graph = build_supervisor_graph()

    def _ask(query: str, customer_id: str) -> dict:
        """Run one request through the whole graph and return the final state."""
        return graph.invoke({"messages": [HumanMessage(query)], "customer_id": customer_id})

    return _ask
