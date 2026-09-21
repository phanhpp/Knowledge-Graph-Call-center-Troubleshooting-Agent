"""Fixtures for the follow-up tests."""

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from agent.supervisor import build_supervisor_graph


@pytest.fixture
def chat_graph():
    """A supervisor graph that saves conversations in memory. A fresh one for every test."""
    return build_supervisor_graph(checkpointer=InMemorySaver())
