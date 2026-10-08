"""End-to-end: a conversation saved on disk survives a restart. Two separate graphs (two
"processes") share one SQLite file. Needs Neo4j, ANTHROPIC_API_KEY and TYPESAFE_API_KEY."""

import sqlite3

import pytest
from langgraph.checkpoint.sqlite import SqliteSaver

from agent.supervisor import build_supervisor_graph
from agent.utils import reply_of
from tests.e2e.conversation import Conversation
from tests.e2e.markers import requires_anthropic_key, requires_typesafe_key

pytestmark = [pytest.mark.e2e, requires_anthropic_key, requires_typesafe_key]


def graph_on(path):
    """A supervisor graph whose conversations are saved in the SQLite file at `path`."""
    return build_supervisor_graph(checkpointer=SqliteSaver(sqlite3.connect(path, check_same_thread=False)))


def test_a_follow_up_works_after_a_restart(tmp_path):
    """Turn 1 on one graph; the follow-up on a brand-new graph over the same file still knows the
    customer, the walk and the answer."""
    file = tmp_path / "conversations.db"
    Conversation(graph_on(file), "restart").say("Customer cannot activate roaming", customer_id="CUST-1005")

    after_restart = Conversation(graph_on(file), "restart")
    result = after_restart.say("what does that rule say?")

    assert result["intent"] == "explain"
    assert "summary" in reply_of(result).text.lower()
    assert [m.type for m in after_restart.saved["messages"]] == ["human", "ai", "human", "ai"]
    assert after_restart.saved["customer_id"] == "CUST-1005"


def test_the_procedure_walk_writes_no_checkpoints_of_its_own(tmp_path):
    """The walk's subgraph is compiled with checkpointer=False, so the file holds only the
    Supervisor's checkpoints (one namespace, the empty one)."""
    file = tmp_path / "conversations.db"
    Conversation(graph_on(file), "on-disk").say("Customer cannot activate roaming", customer_id="CUST-1005")

    with sqlite3.connect(file) as conn:
        namespaces = {row[0] for row in conn.execute("SELECT DISTINCT checkpoint_ns FROM checkpoints")}

    assert file.exists()
    assert namespaces == {""}
