"""Tests for the offline evaluation's walks suite that need no keys and no model calls: the oracle
(answer key maker), the saved answer key, the code evaluators and the gate. The real end-to-end run,
with model calls, is `uv run python -m eval.offline.run --suite walks`."""

import json
from pathlib import Path

import pytest

from agent.procedural_executor import reply_text
from eval.offline.evaluators.code import action_correct, checked_steps, has_checklist, rules_correct, steps_correct
from eval.offline.oracle import expected_walk, procedures
from eval.offline.run import gate

WALKS = Path(__file__).resolve().parents[2] / "eval" / "offline" / "datasets" / "walks.jsonl"


def walks() -> list[dict]:
    return [json.loads(line) for line in WALKS.read_text().splitlines() if line.strip()]


# --- the oracle ---


def test_oracle_matches_the_known_roaming_walk():
    """PROC-001 for CUST-1001 is a walk we already know from a real reply: three NOs, then the
    Tier 2 fallback."""
    walk = expected_walk("PROC-001", "CUST-1001")

    assert walk["action_id"] == "ACT-015"
    assert walk["rule_ids"] == ["R012"]
    assert walk["steps"] == [[1, False], [2, False], [3, False]]
    assert walk["exit"] == "fallback"


def test_oracle_stops_at_the_first_yes():
    """CUST-1002 has roaming off, so step 1 is YES and the walk stops there."""
    walk = expected_walk("PROC-001", "CUST-1002")

    assert walk["steps"] == [[1, True]] and walk["exit"] == "first-yes"


def test_a_ticket_with_no_label_is_refused():
    """Reading a ticket is judgement, so the oracle refuses to guess: it needs a human label."""
    with pytest.raises(ValueError, match="needs a human label"):
        expected_walk("PROC-017", "CUST-1015")


def test_no_ticket_on_file_is_no_without_a_label():
    """With no ticket on file, a ticket condition is NO without a label, as in the agent."""
    assert expected_walk("PROC-017", "CUST-1001")["steps"][0] == [1, False]


# --- the answer key ---


def test_walks_cover_every_procedure_once():
    """walks.jsonl has exactly one walk for each of the 20 procedures."""
    assert sorted(e["outputs"]["procedure_id"] for e in walks()) == sorted(procedures())


def test_walks_exits_vary():
    """Walks end in all three ways: step 1 YES, a later step YES, every step NO."""
    exits = {e["outputs"]["exit"] for e in walks()}
    assert exits == {"first-yes", "later-yes", "fallback"}


def test_walks_use_a_different_customer_each():
    """No customer is used twice, so the suite covers 20 different CRM records."""
    customers = [e["inputs"]["customer_id"] for e in walks()]
    assert len(set(customers)) == len(customers)


def test_walks_answer_key_still_matches_the_data():
    """The saved answers still match what the oracle says today. If this fails, data/ changed:
    rebuild with `uv run python -m eval.offline.build.walks` and review the changed walks again."""
    for example in walks():
        expected = expected_walk(example["outputs"]["procedure_id"], example["inputs"]["customer_id"], example["metadata"]["ticket_labels"])
        for key in ("action_id", "rule_ids", "steps", "exit"):
            assert example["outputs"][key] == expected[key], f"{example['id']}: {key} is stale"


# --- the evaluators ---

TRACE = [
    {"procedure_id": "PROC-001", "step_id": 1, "description": "Roaming is disabled on the account", "result": False, "evidence": "roaming_flag = True"},
    {"procedure_id": "PROC-001", "step_id": 2, "description": "Customer's plan is inactive", "result": True, "evidence": "latest ticket: plan_status: inactive"},
]
REPLY = reply_text("Direct customer to reactivate plan before roaming works", TRACE)


def test_checklist_parser_reads_the_agents_real_reply_format():
    """The reply is made with the agent's own reply_text, so if the agent's reply format changes,
    this test fails. The evidence can contain colons ("latest ticket: …"), which the parser must
    not mistake for the yes/no."""
    assert checked_steps(REPLY) == [[1, False], [2, True]]


def test_walk_evaluators():
    """Each check passes on a matching reply and fails on a wrong one."""
    outputs = {"text": REPLY, "citations": ["ACT-002", "R004"]}
    reference = {"action_id": "ACT-002", "rule_ids": ["R004"], "steps": [[1, False], [2, True]]}

    assert action_correct(outputs, reference) and rules_correct(outputs, reference)
    assert steps_correct(outputs, reference) and has_checklist(outputs, reference)
    assert not action_correct({"citations": ["ACT-015"]}, reference)
    assert not rules_correct({"citations": ["ACT-002"]}, reference)
    assert not steps_correct({"text": "Escalate to Tier 2"}, reference)
    assert not has_checklist({"text": "Escalate to Tier 2"}, reference)


# --- the gate ---


def test_gate_fails_below_a_threshold_and_passes_at_it():
    """18 of 20 fails a threshold of 19; it passes a threshold of 18."""
    summary = {"suite": "walks", "examples": 20, "checks": {"action_correct": {"passed": 18, "total": 20}}}

    assert gate(summary, {"walks": {"action_correct": 19}}) == ["walks.action_correct: 18/20 passed, needs 19/20"]
    assert gate(summary, {"walks": {"action_correct": 18}}) == []


def test_gate_applies_as_a_rate_with_repetitions():
    """With 3 repetitions of 20 examples, 57 of 60 is the same rate as 19 of 20, so it passes."""
    summary = {"suite": "walks", "examples": 20, "repetitions": 3, "checks": {"action_correct": {"passed": 57, "total": 60}}}
    assert gate(summary, {"walks": {"action_correct": 19}}) == []
    summary["checks"]["action_correct"]["passed"] = 56
    assert gate(summary, {"walks": {"action_correct": 19}}) == ["walks.action_correct: 56/60 passed, needs 19/20"]


def test_gate_flags_a_check_that_wasnt_measured():
    """A check with a threshold but no scores fails the gate, rather than passing silently."""
    summary = {"suite": "walks", "examples": 20, "checks": {}}
    assert gate(summary, {"walks": {"action_correct": 19}}) == ["walks.action_correct: not measured"]


def test_gate_asks_for_a_rerun_when_runs_errored():
    """A crashed run (e.g. an API outage) isn't scored as a wrong answer, but the gate still fails,
    since the suite wasn't fully measured."""
    summary = {"suite": "walks", "examples": 20, "errored": {"walk-PROC-003": "529 Overloaded"},
               "checks": {"action_correct": {"passed": 19, "total": 19}}}
    assert gate(summary, {"walks": {"action_correct": 19}}) == ["walks: 1 run(s) errored, not measured: rerun"]
