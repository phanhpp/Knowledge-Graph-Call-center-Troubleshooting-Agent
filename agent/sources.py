"""The text behind each cited ID, for showing a reply's sources (the web app's Sources panel).

A reply's `citations` are bare IDs (ACT-015, R012, KB-006). `sources_for` turns them into what a
person can read: an action's text, a rule's wording, an article's title and opening. It reads the
same data/*.json files the graph is loaded from (etl/load_to_neo4j.py, etl/load_articles.py), so the
text matches what Neo4j holds; etl/audit_graph.py checks the two haven't drifted.
"""

from functools import lru_cache

from etl.io_utils import load_json

ARTICLE_EXCERPT_CHARS = 300  # an article's opening only: the full text would bloat every saved reply


@lru_cache(maxsize=1)
def _sources() -> dict[str, dict]:
    """Every citable ID mapped to its source, read once per process."""
    by_id = {}
    for action in load_json("actions.json"):
        by_id[action["action_id"]] = {"id": action["action_id"], "kind": "action", "title": action["text"], "text": ""}
    for rule in load_json("rules.json"):
        by_id[rule["rule_id"]] = {"id": rule["rule_id"], "kind": "rule", "title": rule["description"], "text": rule["detail"]}
    for article in load_json("kb_articles.json"):
        content = article["content"]
        excerpt = content if len(content) <= ARTICLE_EXCERPT_CHARS else content[:ARTICLE_EXCERPT_CHARS].rstrip() + "…"
        by_id[article["article_id"]] = {"id": article["article_id"], "kind": "article", "title": article["title"], "text": excerpt}
    return by_id


def sources_for(ids: list[str]) -> list[dict]:
    """The source of each ID in `ids`, in the same order: {"id", "kind", "title", "text"}.

    kind is "action" (title = the action), "rule" (title = what it requires, text = its detail) or
    "article" (title = its title, text = its opening). An unknown ID is skipped.
    """
    known = _sources()
    return [dict(known[i]) for i in ids if i in known]
