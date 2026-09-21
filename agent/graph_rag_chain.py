"""Open-ended path — Phase 6. HybridCypherRetriever (vector + fulltext +
graph grounding) + GraphRAG synthesis, per docs/proposed_plan.md Phase 6 and
docs/neo4j_vectorindex.md.

The retrieval_query below *is* the Neo4j grounding step, not a separate
post-processing pass: for each matched Article it pulls the Rule nodes it
cites (via CITES_RULE, linked at etl/load_articles.py load time) in the
same call that does the vector/fulltext search.

Built lazily on first call (driver/retriever/rag), not at import time —
verified this matters: agent/supervisor.py imports this module
unconditionally, so eagerly constructing OpenAIEmbeddings() at import time
crashed *every* caller of agent.supervisor with OpenAIError whenever
OPENAI_API_KEY was unset, even for code paths that never touch the
open-ended path at all. Cached with lru_cache so it's still only built once
per process, same "one Driver per app" principle as config.get_driver().

Requires OPENAI_API_KEY (embedder) and ANTHROPIC_API_KEY (synthesis) in .env
— but only once graph_rag_chain() is actually called, not on import.
"""

from functools import lru_cache

from neo4j_graphrag.embeddings import OpenAIEmbeddings
from neo4j_graphrag.generation import GraphRAG
from neo4j_graphrag.llm import AnthropicLLM
from neo4j_graphrag.retrievers import HybridCypherRetriever

from agent.state import SuperAgentState
from config import get_driver

_RETRIEVAL_QUERY = """
MATCH (node)
OPTIONAL MATCH (node)-[:CITES_RULE]->(r:Rule)
RETURN node.title AS title,
       node.content AS content,
       node.articleId AS articleId,
       collect(DISTINCT r.ruleId) AS ruleIds,
       score
"""


@lru_cache(maxsize=1)
def _get_rag() -> GraphRAG:
    """Build the retriever + GraphRAG pipeline once, on first actual use."""
    retriever = HybridCypherRetriever(
        driver=get_driver(),
        vector_index_name="kb_article_embedding",
        fulltext_index_name="kb_article_fulltext",
        retrieval_query=_RETRIEVAL_QUERY,
        embedder=OpenAIEmbeddings(model="text-embedding-3-small"),
    )
    return GraphRAG(retriever=retriever, llm=AnthropicLLM(model_name="claude-sonnet-5"))


def graph_rag_chain(state: SuperAgentState) -> dict:
    """Vector + fulltext search over KB Articles, grounded by their cited
    Rules, synthesized into an answer."""
    response = _get_rag().search(
        query_text=state["query"],
        retriever_config={"top_k": 5},
        return_context=True,
    )

    # NOTE: the exact shape of response.retriever_result.items[i].metadata
    # hasn't been empirically verified yet — no OPENAI_API_KEY was available
    # while writing this, so this couldn't be run against the real API.
    # Confirm this parsing once a live run is possible; adjust if
    # `metadata` isn't a flat dict of the retrieval_query's RETURN columns.
    citations: list[str] = []
    if response.retriever_result is not None:
        for item in response.retriever_result.items:
            metadata = getattr(item, "metadata", {}) or {}
            article_id = metadata.get("articleId")
            if article_id:
                citations.append(article_id)
            for rule_id in metadata.get("ruleIds") or []:
                if rule_id and rule_id not in citations:
                    citations.append(rule_id)

    return {"answer": response.answer, "citations": citations}
