"""Tests for the offline evaluation's routing suite that need no keys and no model calls: the saved
examples are still what build_routing makes, and they're well formed. The real end-to-end run,
with model calls, is `uv run python -m eval.offline.run --suite routing`."""

import json
from pathlib import Path

from eval.offline.build_routing import build
from eval.offline.run import went

ROUTING = Path(__file__).resolve().parents[2] / "eval" / "offline" / "datasets" / "routing.jsonl"


def routes() -> list[dict]:
    return [json.loads(line) for line in ROUTING.read_text().splitlines() if line.strip()]


def test_routing_file_is_what_build_routing_makes():
    """build() also checks the examples (procedures and customers exist, pairs cover two procedures,
    no repeats). If this fails, routing.jsonl was edited by hand or not rebuilt: run build_routing."""
    assert routes() == build()


def test_every_procedure_pair_has_one_message_per_side():
    pairs: dict[str, list[str]] = {}
    for e in routes():
        if e["metadata"]["slice"] == "procedure-pair":
            pairs.setdefault(e["metadata"]["pair"], []).append(e["outputs"]["procedure_id"])
    assert len(pairs) == 7
    assert all(len(ids) == 2 and len(set(ids)) == 2 for ids in pairs.values())


def test_general_questions_expect_no_procedure():
    """A general question goes to the knowledge base, so no procedure may be walked."""
    general = [e for e in routes() if e["outputs"]["path"] == "open_ended"]
    assert general and all(e["outputs"]["procedure_id"] is None for e in general)


def test_a_miss_shows_where_the_message_went():
    expected = {"path": "troubleshooting", "procedure_id": "PROC-018"}
    outputs = {"intent": "troubleshooting", "matched_procedure_id": "PROC-020"}
    assert went(outputs, expected) == "expected troubleshooting / PROC-018, got troubleshooting / PROC-020"
    assert went({"intent": "open_ended"}, {"path": "troubleshooting", "procedure_id": None}) == (
        "expected troubleshooting / no procedure, got open_ended / no procedure"
    )
