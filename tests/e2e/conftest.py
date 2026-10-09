"""Shared fixture for the end-to-end tests."""

import pytest

from agent.supervisor import build_supervisor_graph
from agent.utils import consultant_message


@pytest.fixture(scope="session")
def ask():
    """Return ask(query, customer_id) -> the final state of the full supervisor graph.

    The graph is compiled once for the whole test session.
    """
    graph = build_supervisor_graph()

    def _ask(query: str, customer_id: str | None) -> dict:
        """Run one request through the whole graph and return the final state. The message is
        stamped with customer_id, as the Slack bot does when the message names one."""
        return graph.invoke({"messages": [consultant_message(query, customer_id)]})

    return _ask
