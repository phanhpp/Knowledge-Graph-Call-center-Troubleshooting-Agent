"""Phase 6 ETL — create the KB article vector/fulltext indexes and load
Article nodes with embeddings.

Run as: uv run python -m etl.load_articles

Adjusted for the actual live Neo4j 5.17.0 instance, not the 2026.02+ target
docs/neo4j_vectorindex.md otherwise assumes (same situation as
etl/load_to_neo4j.py's Cypher): the `WITH [a.category]` filterable-properties
clause on CREATE VECTOR INDEX is a 2026.01+ feature and doesn't parse on
5.17, so it's omitted here. Category filtering still works — neo4j-graphrag's
retrievers auto-fall-back to post-filtering via the older
db.index.vector.queryNodes() procedure on servers before 2026.01, no code
change needed on the retrieval side for that.

Requires OPENAI_API_KEY in .env — neo4j_graphrag.embeddings.OpenAIEmbeddings
reads it from the environment directly, same as ChatAnthropic reads
ANTHROPIC_API_KEY.
"""

import sys
import time

from config import get_driver, get_neo4j_config
from etl.io_utils import load_json

CREATE_VECTOR_INDEX = """
CREATE VECTOR INDEX kb_article_embedding IF NOT EXISTS
FOR (a:Article) ON (a.embedding)
OPTIONS { indexConfig: { `vector.dimensions`: 1536, `vector.similarity_function`: 'cosine' } }
"""

CREATE_FULLTEXT_INDEX = """
CREATE FULLTEXT INDEX kb_article_fulltext IF NOT EXISTS
FOR (a:Article) ON EACH [a.content]
"""

LOAD_ARTICLE_CYPHER = """
UNWIND $rows AS row
MERGE (a:Article {articleId: row.article_id})
SET a.title = row.title, a.content = row.content, a.category = row.category, a.embedding = row.embedding
"""

# Stale-edge-safe: delete old CITES_RULE edges before recreating, same
# lesson as etl/load_to_neo4j.py's load_steps() — otherwise editing an
# article's related_rule_ids and re-running would leave old citations
# dangling alongside the new ones.
LOAD_CITES_RULE_CYPHER = """
UNWIND $rows AS row
MATCH (a:Article {articleId: row.article_id})
OPTIONAL MATCH (a)-[oldCite:CITES_RULE]->(:Rule)
DELETE oldCite
WITH a, row
UNWIND row.rule_ids AS rule_id
MATCH (r:Rule {ruleId: rule_id})
MERGE (a)-[:CITES_RULE]->(r)
"""


def create_indexes(session) -> None:
    """Create the vector + fulltext indexes (idempotent) and poll both to ONLINE."""
    session.run(CREATE_VECTOR_INDEX).consume()
    session.run(CREATE_FULLTEXT_INDEX).consume()

    for _ in range(30):
        rows = list(session.run(
            "SHOW INDEXES YIELD name, state, populationPercent "
            "WHERE name IN ['kb_article_embedding', 'kb_article_fulltext'] "
            "RETURN name, state, populationPercent"
        ))
        if len(rows) == 2 and all(r["state"] == "ONLINE" for r in rows):
            print("Both indexes ONLINE.")
            return
        time.sleep(1)
    raise RuntimeError("Indexes did not reach ONLINE state in time — check `SHOW INDEXES` manually.")


def embed_articles(articles: list[dict]) -> list[list[float]]:
    """Embed each article's content via OpenAI text-embedding-3-small (1536-dim,
    matching the index's declared vector.dimensions)."""
    from neo4j_graphrag.embeddings import OpenAIEmbeddings

    embedder = OpenAIEmbeddings(model="text-embedding-3-small")
    embeddings = [embedder.embed_query(a["content"]) for a in articles]
    for emb in embeddings:
        assert len(emb) == 1536, f"unexpected embedding dimension: {len(emb)}"
    return embeddings


def load_articles(session, articles: list[dict], embeddings: list[list[float]]) -> None:
    """MERGE Article nodes with their embeddings."""
    rows = [
        {
            "article_id": a["article_id"],
            "title": a["title"],
            "content": a["content"],
            "category": a["category"],
            "embedding": emb,
        }
        for a, emb in zip(articles, embeddings)
    ]
    session.run(LOAD_ARTICLE_CYPHER, rows=rows).consume()
    print(f"Loaded {len(rows)} Article nodes.")


def load_cites_rule(session, articles: list[dict]) -> None:
    """Link each Article to the Rules it cites, from related_rule_ids."""
    rows = [{"article_id": a["article_id"], "rule_ids": a.get("related_rule_ids", [])} for a in articles]
    session.run(LOAD_CITES_RULE_CYPHER, rows=rows).consume()
    total_edges = sum(len(a.get("related_rule_ids", [])) for a in articles)
    print(f"Linked CITES_RULE for {len(rows)} articles ({total_edges} edges).")


def main() -> int:
    cfg = get_neo4j_config()
    driver = get_driver()
    try:
        driver.verify_connectivity()
    except Exception as e:
        print(f"Could not connect to Neo4j at {cfg.uri} ({e})")
        return 1

    articles = load_json("kb_articles.json")

    with driver.session(database=cfg.database) as session:
        create_indexes(session)
        embeddings = embed_articles(articles)
        load_articles(session, articles, embeddings)
        load_cites_rule(session, articles)

        count = session.run("MATCH (a:Article) RETURN count(a) AS c").single()["c"]
        print(f"\nFinal Article count: {count}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
