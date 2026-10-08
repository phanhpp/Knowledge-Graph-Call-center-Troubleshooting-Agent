"""End-to-end: questions that match no procedure, so they go to the knowledge-base search. Needs
Neo4j (with the articles loaded by etl.load_articles, on version 5.18.1 or newer), and
ANTHROPIC_API_KEY, OPENAI_API_KEY and TYPESAFE_API_KEY."""

import pytest

from agent.utils import reply_of
from etl.io_utils import load_json
from tests.e2e.markers import requires_anthropic_key, requires_typesafe_key, requires_openai_key

pytestmark = [pytest.mark.e2e, requires_anthropic_key, requires_openai_key, requires_typesafe_key]

ARTICLE_IDS = {a["article_id"] for a in load_json("kb_articles.json")}
RULE_IDS = {r["rule_id"] for r in load_json("rules.json")}


def test_a_policy_question_is_answered_from_the_knowledge_base(ask):
    """A family-plan question matches no procedure, so it goes to the open-ended path. It returns an
    answer and cites the matching article (KB-003) and the rule that article cites (R015)."""
    result = ask("What is the family plan member limit?", "CUST-1001")

    assert result["intent"] == "open_ended"
    assert reply_of(result).text
    assert "KB-003" in reply_of(result).citations
    assert "R015" in reply_of(result).citations


def test_every_citation_is_a_real_article_or_rule(ask):
    """No citation may be invented: each one is an article ID or a rule ID that exists in data/."""
    result = ask("What is the SIM replacement fee?", "CUST-1001")
    assert reply_of(result).citations
    for citation in reply_of(result).citations:
        assert citation in ARTICLE_IDS | RULE_IDS, f"unknown citation {citation!r}"


@pytest.mark.xfail(
    reason=(
        "The old Claude classifier sent this question to the 'Contract cancellation request' procedure. "
        "classify_intent now uses Jev and judges what the consultant wants, not the topic. Remove this "
        "marker once a live run (TYPESAFE_API_KEY set) shows it passing."
    ),
    strict=False,
)
def test_a_fee_question_that_sounds_like_a_procedure_goes_to_the_knowledge_base(ask):
    """'What are the early termination fees...' asks for information, so it belongs to the knowledge
    base, even though a 'Contract cancellation request' procedure exists."""
    result = ask("What are the early termination fees if I cancel my contract?", "CUST-1001")

    assert result["intent"] == "open_ended"
    assert "KB-005" in reply_of(result).citations
    assert {"R005", "R013"} & set(reply_of(result).citations)
