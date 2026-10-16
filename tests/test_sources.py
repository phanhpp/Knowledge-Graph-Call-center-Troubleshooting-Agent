"""Tests for the text behind cited IDs (agent/sources.py) and how reply() attaches it."""

from langsmith import tracing_context
from langsmith.run_helpers import traceable

from agent.sources import ARTICLE_EXCERPT_CHARS, sources_for
from agent.utils import reply
from etl.io_utils import load_json


def test_each_kind_of_id_gets_its_text():
    """An action is its text; a rule is what it requires plus its detail; an article its title and opening."""
    action, rule, article = sources_for(["ACT-015", "R012", "KB-006"])

    assert action == {"id": "ACT-015", "kind": "action", "title": "Escalate to Tier 2 - unresolved by standard checks", "text": ""}
    assert rule == {
        "id": "R012",
        "kind": "rule",
        "title": "Tier 2 escalations require a documented troubleshooting summary",
        "text": "Every Tier 2 escalation must include a written summary of the steps already attempted",
    }
    assert article["kind"] == "article" and article["title"] == "SIM Replacement Process and Fees"
    assert len(article["text"]) <= ARTICLE_EXCERPT_CHARS + 1  # the opening, plus "…" when cut


def test_order_is_kept_and_unknown_ids_are_skipped():
    assert [s["id"] for s in sources_for(["R012", "NOPE-1", "ACT-015"])] == ["R012", "ACT-015"]


def test_every_id_in_the_data_has_a_source():
    """Nothing citable is missing: every action, rule and article in data/ resolves."""
    ids = [a["action_id"] for a in load_json("actions.json")]
    ids += [r["rule_id"] for r in load_json("rules.json")]
    ids += [k["article_id"] for k in load_json("kb_articles.json")]

    assert [s["id"] for s in sources_for(ids)] == ids


def test_a_source_is_a_copy_not_the_cached_entry():
    """Changing a returned source must not change what the next reply gets."""
    sources_for(["R012"])[0]["title"] = "changed"

    assert sources_for(["R012"])[0]["title"] != "changed"


def test_reply_keeps_citations_and_adds_their_sources():
    """`citations` stays a list of IDs (the Slack bot and the citations_exist check read it)."""
    kwargs = reply("Escalate", ["ACT-015", "R012"])["messages"][0].additional_kwargs

    assert kwargs["citations"] == ["ACT-015", "R012"]
    assert [s["id"] for s in kwargs["sources"]] == ["ACT-015", "R012"]


def test_reply_citing_nothing_has_no_sources():
    kwargs = reply("Which customer is this about?")["messages"][0].additional_kwargs

    assert kwargs["citations"] == [] and kwargs["sources"] == []


def test_reply_has_no_run_id_outside_a_traced_run():
    assert "run_id" not in reply("x")["messages"][0].additional_kwargs


def test_reply_inside_a_traced_run_records_the_root_run():
    """Inside a turn, the reply carries the turn's root run id, which is where a 👍👎 is filed."""
    seen = {}

    @traceable(name="turn")
    def turn():
        @traceable(name="node")
        def node():
            return reply("x")["messages"][0].additional_kwargs["run_id"]

        seen["run_id"] = node()

    with tracing_context(enabled="local"):  # records locally; sends nothing
        turn(langsmith_extra={"on_end": lambda run: seen.setdefault("root", str(run.id))})

    assert seen["run_id"] == seen["root"]
