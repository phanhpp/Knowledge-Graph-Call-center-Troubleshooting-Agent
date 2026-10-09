"""Plain data types shared by the agent and its callers (not graph state; that is agent/state.py)."""

from typing import NamedTuple


class Reply(NamedTuple):
    """The agent's reply: its text and the IDs it cites."""

    text: str
    citations: list[str]
