"""Tests for slack_bot/chat.py: AgentChat, with a fake graph and a fake checkpointer (no network)."""

import threading
import time
from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage

from slack_bot.chat import AgentChat


class FakeGraph:
    """Stands in for the compiled supervisor graph: keeps a customer per thread and records calls."""

    def __init__(self, pause=0.0):
        """`pause` makes invoke slow, so a test can see whether two calls overlap."""
        self.saved = {}
        self.invocations = []
        self.pause = pause
        self.active = 0
        self.max_active = 0
        self._guard = threading.Lock()

    def get_state(self, config):
        """The saved values for the thread in `config`."""
        return SimpleNamespace(values=dict(self.saved.get(config["configurable"]["thread_id"], {})))

    def invoke(self, update, config):
        """Record the call, remember the customer, and return a reply message."""
        with self._guard:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        time.sleep(self.pause)
        thread_id = config["configurable"]["thread_id"]
        self.invocations.append((thread_id, update))
        named = update["messages"][0].additional_kwargs.get("customer_id")
        if named:  # as check_customer does: a named customer becomes the current one
            self.saved.setdefault(thread_id, {})["customer_id"] = named
        with self._guard:
            self.active -= 1
        return {"messages": [update["messages"][0], AIMessage("Answer", additional_kwargs={"citations": ["ACT-015", "R012"]})]}


class FakeCheckpointer:
    """Records which conversations were deleted, and forgets their saved customer."""

    def __init__(self, graph):
        """Bind to the fake graph whose saved state a delete should clear."""
        self.graph = graph
        self.deleted = []

    def delete_thread(self, thread_id):
        """Record the deletion and clear that thread's saved state."""
        self.deleted.append(thread_id)
        self.graph.saved.pop(thread_id, None)


def make_chat(pause=0.0):
    """An AgentChat over a fake graph and checkpointer."""
    graph = FakeGraph(pause)
    checkpointer = FakeCheckpointer(graph)
    return AgentChat(graph, checkpointer), graph, checkpointer


def test_send_returns_the_reply_text_and_citations():
    """The result is {"answer", "citations"}, read from the agent's last message."""
    chat, _, _ = make_chat()
    assert chat.send("t1", "roaming", "CUST-1005") == {"answer": "Answer", "citations": ["ACT-015", "R012"]}


def test_send_gives_the_graph_the_message_stamped_with_the_customer():
    """The consultant's text goes in as a HumanMessage stamped with the customer ID it named; the
    customer is not sent as a separate field."""
    chat, graph, _ = make_chat()
    chat.send("t1", "roaming", "CUST-1005")

    thread_id, update = graph.invocations[0]
    assert thread_id == "t1"
    assert list(update) == ["messages"]
    assert isinstance(update["messages"][0], HumanMessage)
    assert update["messages"][0].content == "roaming"
    assert update["messages"][0].additional_kwargs == {"customer_id": "CUST-1005"}


def test_a_follow_up_sends_no_customer_so_the_saved_one_is_kept():
    """Without a customer ID the message is not stamped: the graph keeps the saved customer."""
    chat, graph, _ = make_chat()
    chat.send("t1", "roaming", "CUST-1005")
    chat.send("t1", "why?", None)

    assert graph.invocations[1][1]["messages"][0].additional_kwargs == {}
    assert chat.known_customer("t1") == "CUST-1005"


def test_known_customer_is_none_until_a_conversation_has_one():
    """A new conversation has no customer yet."""
    chat, _, _ = make_chat()
    assert chat.known_customer("t1") is None
    chat.send("t1", "roaming", "CUST-1005")
    assert chat.known_customer("t1") == "CUST-1005"


def test_the_same_customer_again_keeps_the_conversation():
    """Repeating the same customer ID must not wipe the conversation."""
    chat, _, checkpointer = make_chat()
    chat.send("t1", "roaming", "CUST-1005")
    chat.send("t1", "and again", "CUST-1005")

    assert checkpointer.deleted == []


def test_a_different_customer_switches_without_losing_the_conversation():
    """Naming another customer makes them the current one; nothing saved is deleted."""
    chat, graph, checkpointer = make_chat()
    chat.send("t1", "roaming", "CUST-1005")
    chat.send("t1", "roaming", "CUST-1002")

    assert checkpointer.deleted == []
    assert chat.known_customer("t1") == "CUST-1002"
    assert graph.invocations[1][1]["messages"][0].additional_kwargs["customer_id"] == "CUST-1002"


def test_coming_back_to_an_earlier_customer_switches_back():
    """A -> B -> A in one conversation: each switch only changes the current customer."""
    chat, _, checkpointer = make_chat()
    chat.send("t1", "roaming", "CUST-1005")
    chat.send("t1", "bill", "CUST-1002")
    chat.send("t1", "why?", "CUST-1005")

    assert checkpointer.deleted == []
    assert chat.known_customer("t1") == "CUST-1005"


def test_conversations_are_kept_apart_by_thread_id():
    """Switching customer in one conversation must not touch another."""
    chat, _, checkpointer = make_chat()
    chat.send("t1", "roaming", "CUST-1005")
    chat.send("t2", "roaming", "CUST-1002")
    chat.send("t2", "roaming", "CUST-1001")

    assert checkpointer.deleted == []
    assert chat.known_customer("t1") == "CUST-1005"
    assert chat.known_customer("t2") == "CUST-1001"


def test_two_messages_on_one_conversation_never_run_at_the_same_time():
    """Two quick messages in one DM are queued one after the other, not run together."""
    chat, graph, _ = make_chat(pause=0.05)
    workers = [threading.Thread(target=chat.send, args=("t1", f"m{i}", "CUST-1005")) for i in range(4)]
    for w in workers:
        w.start()
    for w in workers:
        w.join()

    assert graph.max_active == 1
    assert len(graph.invocations) == 4
