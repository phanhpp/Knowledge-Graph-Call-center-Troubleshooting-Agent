"""Open-ended path: answer a question from the knowledge-base articles, with citations.

Flow of one graph_rag_chain() call:

    the consultant's latest message, as the agent reads it (a follow-up like "and after the first
    year?" arrives already rewritten to stand on its own: agent/supervisor.py rewrite_question)
      |
      1. embed the query                       OpenAI text-embedding-3-small (1536 numbers)
      |
      2. hybrid search over Article nodes      two searches in Neo4j, run together:
      |                                          - vector index   kb_article_embedding
      |                                              finds articles close in meaning to the query
      |                                          - fulltext index kb_article_fulltext
      |                                              finds articles that share words with the query
      |                                              (sent an escaped copy: _fulltext_query)
      |                                        Each search's scores are divided by its own best
      |                                        score (its best hit becomes 1.0), the two lists are
      |                                        merged, an article found by both keeps its higher
      |                                        score, and the top 5 are kept. This is
      |                                        neo4j-graphrag's default "naive" ranker; we do not
      |                                        set another one.
      |
      3. graph grounding                       _RETRIEVAL_QUERY runs right after the search, in the
      |                                        same Cypher call: for each of the 5 articles it
      |                                        follows CITES_RULE to the Rules that article cites.
      |
      4. format for Claude                     _format_article turns each row into the text Claude
      |                                        reads (id, title, body, cited rule IDs) and keeps
      |                                        the IDs in metadata for the citations.
      |
      5. Claude writes the answer              claude-sonnet-5, from those 5 articles only.
      |
      6. return the answer as the reply, citing each article id followed by its rule ids

Why both searches: the vector search finds a paraphrase ("cancel my contract" matches "early
termination fees"), and the fulltext search finds exact terms the embedding can blur (a plan name
like "Unlimited Plus", or "eSIM"). An article that ranks well in either one is kept.

Rules only appear here as IDs: they are returned in `citations` and shown to Claude by ID. They
do not filter or change which articles are found.

Needs, before the first call:
  - Neo4j 5.18.1 or newer. neo4j-graphrag refuses to build a retriever on an older server.
  - the two indexes and the Article nodes: `uv run python -m etl.load_articles`
  - OPENAI_API_KEY (query embedding) and ANTHROPIC_API_KEY (the answer) in .env

The retriever and Claude client are built on the first call, not at import time.
agent/supervisor.py imports this module unconditionally, so building them at import crashed every
caller with an OpenAIError whenever OPENAI_API_KEY was unset.
"""

import re
from functools import lru_cache

import neo4j
from neo4j_graphrag.embeddings import OpenAIEmbeddings
from neo4j_graphrag.generation import GraphRAG
from neo4j_graphrag.llm import AnthropicLLM
from neo4j_graphrag.retrievers import HybridCypherRetriever
from neo4j_graphrag.types import RetrieverResult, RetrieverResultItem
from langsmith import traceable
from langsmith.wrappers import wrap_anthropic

from agent.state import SuperAgentState
from agent.utils import as_seen, reply
from config import get_driver

# Appended by neo4j-graphrag after the hybrid search; `node` is one matched Article and `score`
# is its ranked score.
_RETRIEVAL_QUERY = """
MATCH (node)
OPTIONAL MATCH (node)-[:CITES_RULE]->(r:Rule)
RETURN node.title AS title,
       node.content AS content,
       node.articleId AS articleId,
       collect(DISTINCT r.ruleId) AS ruleIds,
       score
"""

_MAX_ANSWER_TOKENS = 1024

# Characters the full-text index reads as Lucene query syntax: field:value, (groups), "phrases", wild*cards…
_LUCENE_SYNTAX = re.compile(r'([+\-!(){}\[\]^"~*?:\\/]|&&|\|\|)')


def _fulltext_query(text: str) -> str:
    """The question as the full-text index should read it: special characters escaped, and lower-cased
    so AND / OR / NOT stay words, not operators (the index lower-cases words anyway). Sent as typed, a
    question with a colon ("Policy question: can I…?") failed the whole search."""
    return _LUCENE_SYNTAX.sub(r"\\\1", text.lower())


def _format_article(record: neo4j.Record) -> RetrieverResultItem:
    """Turn one retrieved row into the text Claude reads plus the IDs to cite."""
    rule_ids = [r for r in record["ruleIds"] if r]
    content = f"[{record['articleId']}] {record['title']}\n{record['content']}"
    if rule_ids:
        content += f"\nPolicy rules cited by this article: {', '.join(rule_ids)}"
    return RetrieverResultItem(
        content=content,
        metadata={"articleId": record["articleId"], "ruleIds": rule_ids, "score": record["score"]},
    )

# Helper method for processing output sent to Langsmith for tracing
def _as_documents(result: RetrieverResult) -> dict:
    """How the search appears in LangSmith: the retrieved articles as documents (a retriever run)."""
    return {
        "documents": [
            {"page_content": item.content, "type": "Document", "metadata": item.metadata or {}}
            for item in result.items
        ]
    }


@lru_cache(maxsize=1)
def _get_rag() -> GraphRAG:
    """Build the retriever + GraphRAG pipeline once, on first actual use.

    neo4j-graphrag is not LangChain, so LangSmith does not trace its steps on its own. Two are
    traced by hand, as children of the graph_rag_chain run: the search (`kb_search`, a retriever
    run showing the 5 articles) and Claude's answer (`kb_answer`, the full prompt with the articles
    and the reply). A judge of whether the answer is grounded in the articles reads `kb_answer`.
    """
    retriever = HybridCypherRetriever(
        driver=get_driver(),
        vector_index_name="kb_article_embedding",
        fulltext_index_name="kb_article_fulltext",
        retrieval_query=_RETRIEVAL_QUERY,
        embedder=OpenAIEmbeddings(model="text-embedding-3-small"),
        result_formatter=_format_article,
    )
    search = retriever.search

    def search_safely(query_text: str, **kwargs) -> RetrieverResult:
        """The vector search embeds the question as written; the full-text index gets an escaped copy."""
        query_vector = retriever.embedder.embed_query(query_text)
        return search(query_text=_fulltext_query(query_text), query_vector=query_vector, **kwargs)

    retriever.search = traceable(name="kb_search", run_type="retriever", process_outputs=_as_documents)(
        search_safely
    )
    # thinking disabled: neo4j-graphrag only accepts a reply whose first block is text, and this
    # model sometimes opens with a thinking block, which made the call fail.
    llm = AnthropicLLM(
        model_name="claude-sonnet-5",
        model_params={"max_tokens": _MAX_ANSWER_TOKENS, "thinking": {"type": "disabled"}},
    )
    llm.client = wrap_anthropic(llm.client, chat_name="kb_answer")
    return GraphRAG(retriever=retriever, llm=llm)


def graph_rag_chain(state: SuperAgentState) -> dict:
    """Search the KB articles, then have Claude answer from them.

    The query text is the latest message in state["messages"], as the agent reads it (as_seen): a
    follow-up's standalone rewrite, else the text typed. The earlier messages are not passed.

    Returns the reply (see agent/utils.py reply()). Its citations are each retrieved article's ID
    followed by the IDs of the rules it cites (a rule shared by several articles is listed once).
    This covers all 5 retrieved articles, not only the ones Claude relied on.
    """
    response = _get_rag().search(
        query_text=str(as_seen(state["messages"][-1]).content),
        retriever_config={"top_k": 5},
        return_context=True,
    )

    citations: list[str] = []
    # Append article ids and rules id to citations
    if response.retriever_result is not None:
        for item in response.retriever_result.items:
            metadata = item.metadata or {}
            article_id = metadata.get("articleId")
            if article_id:
                citations.append(article_id)
            for rule_id in metadata.get("ruleIds", []):
                if rule_id not in citations:
                    citations.append(rule_id)

    return reply(response.answer, citations)
