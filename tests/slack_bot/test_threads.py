"""Tests for slack_bot/threads.py: which saved conversation a direct message belongs to."""

from slack_bot.threads import thread_id_for


def test_a_top_level_message_belongs_to_the_dms_main_conversation():
    """Messages typed straight into the DM share one conversation."""
    assert thread_id_for("D123", None) == "slack-D123"


def test_a_reply_in_a_thread_is_its_own_conversation():
    """A thread inside the DM is a separate conversation, so several cases can run side by side."""
    assert thread_id_for("D123", "111.1") == "slack-D123-111.1"


def test_different_threads_and_channels_get_different_ids():
    """No two conversations share an id."""
    ids = {thread_id_for("D1", None), thread_id_for("D1", "1.1"), thread_id_for("D1", "2.2"), thread_id_for("D2", None)}
    assert len(ids) == 4
