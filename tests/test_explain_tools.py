"""Unit tests for the explain agent's read-only tools (agent/explain_tools.py): they see the procedures,
rules and articles and nothing else, and keep their answers small. No Neo4j, no model calls."""

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from pydantic import ValidationError

from agent.explain_tools import files, files_read, grep, ls, read_file
from etl.io_utils import load_json


def test_the_folder_holds_every_procedure_rule_and_article_and_nothing_else():
    assert len([p for p in files() if p.startswith("procedures/")]) == len(load_json("procedures.json"))
    assert len([p for p in files() if p.startswith("rules/")]) == len(load_json("rules.json"))
    assert len([p for p in files() if p.startswith("articles/")]) == len(load_json("kb_articles.json"))
    assert all(p.startswith(("procedures/", "rules/", "articles/")) for p in files())


def test_read_file_returns_a_rule_or_an_article():
    assert read_file.invoke({"path": "rules/R004.md"}).startswith("# R004: SIM replacement fee waived")
    assert "rolling 12-month window" in read_file.invoke({"path": "articles/KB-006.md"})


def test_a_procedure_file_shows_every_step_and_where_yes_and_no_lead():
    """So "why didn't it check X?" can be answered from the procedure, not guessed."""
    assert read_file.invoke({"path": "procedures/PROC-007.md"}).splitlines() == [
        "# PROC-007: Customer requests SIM replacement",
        "When to use: The customer asks for a new or replacement SIM card: lost, stolen, damaged, or a "
        "different size. If the current SIM is simply not detected by the phone, use PROC-006.",
        "",
        "1. Check: SIM card is blocked (COND-011)",
        "   yes: ACT-018 Issue replacement SIM card and waive the replacement fee (rules: R004)",
        "   no: go to step 2",
        "2. Check: Account is suspended (COND-014)",
        "   yes: ACT-023 Reactivate suspended account after payment confirmation (rules: R010)",
        "   no: ACT-018 Issue replacement SIM card and waive the replacement fee (rules: R004) (the fallback)",
    ]


@pytest.mark.parametrize("path", [
    "../.env", ".env", "data/crm_records.json", "agent/supervisor.py", "/etc/passwd", "rules/../.env", "articles/KB-999.md",
])
def test_read_file_finds_nothing_outside_the_folder(path):
    """There are no real files behind the tool, so any other path is simply not there."""
    assert read_file.invoke({"path": path}).startswith("No such file")


def test_ls_lists_titles_and_only_takes_the_two_folders():
    assert ls.invoke({"folder": "articles"}).splitlines()[0] == "articles/KB-001.md  KB-001: Roaming Pricing Structure by Region"
    with pytest.raises(ValidationError):
        ls.invoke({"folder": "data"})


def test_grep_returns_matching_sentences_not_whole_files():
    hits = grep.invoke({"pattern": "rolling 12-month", "folder": "articles"}).splitlines()
    assert hits and all(h.startswith("articles/KB-006.md: ") for h in hits)
    assert all(len(h) < 400 for h in hits)  # a sentence, not the ~1,300-character article


def test_grep_keeps_to_its_folder_caps_its_results_and_survives_a_bad_pattern():
    assert grep.invoke({"pattern": "supervisor", "folder": "rules"}).startswith("rules/")
    many = grep.invoke({"pattern": "the"}).splitlines()
    assert len(many) == 21 and many[-1].startswith("… and")  # 20 matches, then how many more
    assert grep.invoke({"pattern": "("}).startswith("Invalid pattern")
    assert grep.invoke({"pattern": "x" * 201}).startswith("Pattern too long")
    assert grep.invoke({"pattern": "no such phrase anywhere"}) == "No matches."


def test_files_read_counts_only_what_the_agent_actually_saw():
    """A read that returned the file counts; a wrong path or a call blocked by the limit doesn't; a
    grep result counts for the files its sentences came from."""
    def call(path, call_id):
        return {"name": "read_file", "args": {"path": path}, "id": call_id}

    messages = [
        AIMessage("", tool_calls=[call("articles/KB-006.md", "1"), call("nowhere.md", "2"), call("rules/R004.md", "3")]),
        ToolMessage(files()["articles/KB-006.md"], tool_call_id="1", name="read_file"),
        ToolMessage("No such file: nowhere.md.", tool_call_id="2", name="read_file"),
        ToolMessage("Tool call limit exceeded.", tool_call_id="3", name="read_file"),
        ToolMessage("rules/R001.md: Refunds over $50 require supervisor approval", tool_call_id="4", name="grep"),
    ]
    assert files_read(messages) == ["articles/KB-006.md", "rules/R001.md"]
