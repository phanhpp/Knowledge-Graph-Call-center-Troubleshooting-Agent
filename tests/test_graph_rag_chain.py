"""Integration tests for the Graph RAG Chain (Phase 6) — real queries
against the live vector/fulltext indexes and a real Anthropic synthesis
call. Requires OPENAI_API_KEY (embeddings) and ANTHROPIC_API_KEY (synthesis)
and the KB articles already loaded via `uv run python -m etl.load_articles`
— skipped otherwise, same guard pattern as the ticket_text tests.
"""

import os

import pytest

from agent.graph_rag_chain import graph_rag_chain
from etl.io_utils import load_json

requires_keys = pytest.mark.skipif(
    not (os.environ.get("OPENAI_API_KEY") and os.environ.get("ANTHROPIC_API_KEY")),
    reason="graph_rag_chain needs a live OpenAI embedding call and Anthropic synthesis call",
)

ALL_RULE_IDS = {r["rule_id"] for r in load_json("rules.json")}


@requires_keys
def test_returns_an_answer_for_a_known_topic():
    """A query matching a real KB topic (early termination fees, KB-005)
    should come back with a non-empty answer."""
    result = graph_rag_chain({"query": "What are the early termination fees if I cancel my contract?"})
    assert result["answer"]


@requires_keys
def test_citations_include_the_expected_rule():
    """KB-005 ('Early Termination Fees and Waivers') cites R005 and R013 —
    a directly matching query should surface at least one of them."""
    result = graph_rag_chain({"query": "What are the early termination fees if I cancel my contract?"})
    assert any(c in ("R005", "R013") for c in result["citations"])


@requires_keys
def test_no_hallucinated_rule_citations():
    """Every rule-shaped citation ('R' + digits) across a handful of
    different queries must be a real rule_id from rules.json — never made up."""
    queries = [
        "What is the data rollover policy?",
        "What are the SIM replacement fees?",
        "What's the family plan member limit?",
    ]
    for query in queries:
        result = graph_rag_chain({"query": query})
        rule_citations = [c for c in result["citations"] if c.startswith("R") and c[1:].isdigit()]
        for rid in rule_citations:
            assert rid in ALL_RULE_IDS, f"hallucinated rule id {rid!r} for query {query!r}"
