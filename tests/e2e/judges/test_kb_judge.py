"""End-to-end: the knowledge-base judge (eval/offline/evaluators/jev.py), with the real Jev, on
answers whose verdict we know: one good answer and five bad ones. A judge that passes everything is
worthless, so this checks it can tell them apart. Run it after changing a judge's wording. Needs
TYPESAFE_API_KEY; no Neo4j or other model calls (the answers are written here, not by the agent)."""

import json
from pathlib import Path

import pytest

from eval.offline.evaluators.jev import kb_answer
from tests.e2e.markers import requires_typesafe_key

pytestmark = [pytest.mark.e2e, requires_typesafe_key]

KB = Path(__file__).resolve().parents[3] / "eval" / "offline" / "datasets" / "kb.jsonl"
EXAMPLES = {json.loads(line)["id"]: json.loads(line) for line in KB.read_text().splitlines() if line.strip()}


def judge(golden_id: str, answer: str, citations: list[str]) -> dict:
    """The judge's scores for this answer to that golden question, by key."""
    example = EXAMPLES[golden_id]
    result = kb_answer(example["inputs"], {"text": answer, "citations": citations}, example["outputs"])
    return {r["key"]: r["score"] for r in result["results"]}


def test_a_good_answer_passes():
    """The reference answer itself, written from KB-006: every fact stated, nothing wrong or made up."""
    scores = judge("kb-sim-fee-waiver", EXAMPLES["kb-sim-fee-waiver"]["outputs"]["reference"], ["KB-006", "R004"])
    assert scores["facts_covered"] == 1 and scores["no_contradiction"] and scores["supported"] and scores["usable"]


@pytest.mark.parametrize("golden_id, answer, citations", [
    ("kb-sim-fee-waiver", "The SIM replacement fee is waived for the first two replacements each calendar year.", ["KB-006", "R004"]),
    ("kb-dispute-60", "Yes, you can refund it yourself, and the customer has 6 months to file the dispute.", ["KB-013", "KB-019"]),
])
def test_wrong_facts_are_caught(golden_id, answer, citations):
    scores = judge(golden_id, answer, citations)
    assert scores["facts_covered"] < 0.5 and not scores["no_contradiction"] and not scores["supported"]


def test_a_vague_answer_is_unusable_but_not_wrong():
    """It states none of the key facts, but says nothing false either: the judge must keep those apart."""
    scores = judge("kb-outage-credit", "Customers can get a credit for outages. Check the policy for details.", ["KB-014"])
    assert scores["facts_covered"] == 0 and not scores["usable"]
    assert scores["no_contradiction"] and scores["supported"]


@pytest.mark.parametrize("golden_id, answer, citations", [
    ("kb-sim-fee-amount", "The SIM replacement fee is $25.", ["KB-006", "R004"]),
    ("kb-paypal", "Yes, customers can pay their bill with PayPal at any time.", ["KB-023"]),
])
def test_a_made_up_answer_to_an_unanswerable_question_is_caught(golden_id, answer, citations):
    scores = judge(golden_id, answer, citations)
    assert not scores["admits_gap"] and not scores["supported"]
