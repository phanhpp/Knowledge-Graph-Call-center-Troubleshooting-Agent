"""Integration tests for the Graph RAG Chain (Phase 6) — real queries
against the live vector/fulltext indexes and a real Anthropic synthesis
call. Requires OPENAI_API_KEY (embeddings) and ANTHROPIC_API_KEY (synthesis)
and the KB articles already loaded via `uv run python -m etl.load_articles`
— skipped otherwise, same guard pattern as the ticket_text tests. The last
two tests need no keys: the full-text escaping, and which text is searched.
"""

import os
from types import SimpleNamespace

import pytest
from langchain_core.messages import HumanMessage

from agent.graph_rag_chain import _fulltext_query, graph_rag_chain
from agent.utils import reply_of
from etl.io_utils import load_json

requires_keys = pytest.mark.skipif(
    not (os.environ.get("OPENAI_API_KEY") and os.environ.get("ANTHROPIC_API_KEY")),
    reason="graph_rag_chain needs a live OpenAI embedding call and Anthropic synthesis call",
)

ALL_RULE_IDS = {r["rule_id"] for r in load_json("rules.json")}


def search(question: str):
    """Run the chain on one question and return its reply (`.text` and `.citations`)."""
    return reply_of(graph_rag_chain({"messages": [HumanMessage(question)]}))


@requires_keys
def test_returns_an_answer_for_a_known_topic():
    """A query matching a real KB topic (early termination fees, KB-005)
    should come back with a non-empty answer."""
    result = search("What are the early termination fees if I cancel my contract?")
    assert result.text


@requires_keys
def test_citations_include_the_expected_rule():
    """KB-005 ('Early Termination Fees and Waivers') cites R005 and R013 —
    a directly matching query should surface at least one of them."""
    result = search("What are the early termination fees if I cancel my contract?")
    assert any(c in ("R005", "R013") for c in result.citations)


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
        result = search(query)
        rule_citations = [c for c in result.citations if c.startswith("R") and c[1:].isdigit()]
        for rid in rule_citations:
            assert rid in ALL_RULE_IDS, f"hallucinated rule id {rid!r} for query {query!r}"


@requires_keys
def test_a_question_with_query_syntax_still_gets_an_answer():
    """A colon or brackets mean query syntax to the full-text index; sent as typed, they failed the
    whole search. Rewritten follow-ups often contain a colon."""
    result = search("Policy question: can I approve a $200 refund myself (without a supervisor)?")
    assert result.text and "KB-019" in result.citations


# --- no keys: the search's input, with a fake retriever ---


def test_the_full_text_query_escapes_query_syntax_and_operators():
    assert _fulltext_query('Policy question: (a) "b" c* AND d') == 'policy question\\: \\(a\\) \\"b\\" c\\* and d'


def test_the_search_reads_a_follow_ups_rewrite(monkeypatch):
    """A follow-up is searched as rewritten, not as typed."""
    seen = []

    class FakeRag:
        def search(self, query_text, **kwargs):
            seen.append(query_text)
            return SimpleNamespace(retriever_result=None, answer="From KB-005.")

    monkeypatch.setattr("agent.graph_rag_chain._get_rag", lambda: FakeRag())
    follow_up = HumanMessage("and after the first year?", additional_kwargs={
        "standalone": "Is there a fee to cancel a contract after the first year?"})

    graph_rag_chain({"messages": [follow_up]})

    assert seen == ["Is there a fee to cancel a contract after the first year?"]
