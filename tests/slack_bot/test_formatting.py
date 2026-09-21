"""Tests for slack_bot/formatting.py: turning an agent result into a Slack message."""

from slack_bot.formatting import NO_ANSWER, format_reply, to_slack_markdown


def test_reply_is_the_answer_followed_by_the_sources():
    """The citation IDs go on their own line after the answer."""
    reply = format_reply({"answer": "Escalate to Tier 2", "citations": ["ACT-015", "R012"]})
    assert reply == "Escalate to Tier 2\n\n_Sources: ACT-015, R012_"


def test_no_citations_means_no_sources_line():
    """A reply without citations (such as an error message) has no Sources line."""
    reply = format_reply({"answer": "I couldn't find a customer with ID 'CUST-9999'.", "citations": []})
    assert "Sources" not in reply


def test_missing_citations_key_is_fine():
    """A result with no `citations` key at all does not crash."""
    assert format_reply({"answer": "Hello"}) == "Hello"


def test_no_answer_gives_the_fallback_message():
    """A run that ended without an answer (for example `answer` is None) gets NO_ANSWER."""
    assert format_reply({"answer": None, "citations": []}) == NO_ANSWER
    assert format_reply({}) == NO_ANSWER
    assert format_reply({"error": "Terminal step 3 has no action_if_false_id"}) == NO_ANSWER


def test_double_asterisk_bold_becomes_slack_bold():
    """Claude writes **bold**; Slack wants *bold*."""
    assert to_slack_markdown("The fee is **$50** today") == "The fee is *$50* today"


def test_markdown_headings_become_bold_lines():
    """Slack has no headings, so `## Title` becomes a bold line."""
    assert to_slack_markdown("## Fees\nText") == "*Fees*\nText"


def test_plain_text_is_unchanged():
    """Text with no Markdown passes through as it is."""
    assert to_slack_markdown("Escalate to Tier 2 - unresolved by standard checks") == (
        "Escalate to Tier 2 - unresolved by standard checks"
    )
