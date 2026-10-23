"""Tests for the offline evaluation's guards, conversations and adversarial suites that need no keys
and no model calls: the saved examples, the code checks, and the Jev judges with a fake Jev. The real
end-to-end runs are `uv run python -m eval.offline.run --suite guards` (and conversations, adversarial)."""

import json
from pathlib import Path

import pytest
from langchain_typesafe import ClassifierResponse, NoulAnswer, ScoreAnswer

from eval.offline.build import adversarial, conversations, guards
from eval.offline.evaluators.code import (
    calls_within_limit,
    cites_policy,
    customer_correct,
    error_correct,
    explanations_cite_nothing,
    no_other_customer_data,
    reply_correct,
    turn_customers_correct,
    turn_paths_correct,
    turn_procedures_correct,
)
from eval.offline.evaluators.jev import _transcript, adversarial_reply, conversation_turns, turn_questions

DATASETS = Path(__file__).resolve().parents[2] / "eval" / "offline" / "datasets"


def load(suite: str) -> list[dict]:
    return [json.loads(line) for line in (DATASETS / f"{suite}.jsonl").read_text().splitlines() if line.strip()]


def example(suite: str, golden_id: str) -> dict:
    return next(e for e in load(suite) if e["id"] == golden_id)


@pytest.mark.parametrize("suite, builder", [
    ("guards", guards), ("conversations", conversations), ("adversarial", adversarial),
])
def test_saved_examples_are_what_the_builder_makes(suite, builder):
    """build() also checks the examples against the data (customers, procedures, cited IDs, the walks
    the facts describe). If this fails, rebuild: `uv run python -m eval.offline.build.<suite>`."""
    assert load(suite) == builder.build()


# --- guards ---


def test_guard_checks_on_an_unknown_id():
    reference = example("guards", "guard-unknown-id")["outputs"]
    stopped = {"text": reference["reply_starts"], "error": "No CRM record found for customer_id 'CUST-9999'",
               "claude_calls": 0, "jev_calls": 0, "customer_id": None}
    assert reply_correct(stopped, reference)["score"] and error_correct(stopped, reference)["score"]
    assert calls_within_limit(stopped, reference)["score"] and customer_correct(stopped, reference)
    assert not calls_within_limit({**stopped, "jev_calls": 1}, reference)["score"]  # it should stop before Jev


def test_guard_checks_that_dont_apply_are_left_out():
    """The two-IDs example is a normal walk: no fixed reply or call limit to check, and no error."""
    reference = example("guards", "guard-two-ids")["outputs"]
    walked = {"text": "Enable roaming flag…", "error": None, "customer_id": "CUST-1002"}
    assert reply_correct(walked, reference)["score"] is None
    assert calls_within_limit(walked, reference)["score"] is None
    assert error_correct(walked, reference)["score"] and customer_correct(walked, reference)


# --- conversations: code checks ---

CONVERSATION = example("conversations", "conv-switch-customers")
GOOD_TURNS = {"turns": [
    {"intent": "troubleshooting", "matched_procedure_id": "PROC-001", "customer_id": "CUST-1001", "citations": ["ACT-015", "R012"], "text": "Escalate to Tier 2…"},
    {"intent": "troubleshooting", "matched_procedure_id": "PROC-001", "customer_id": "CUST-1002", "citations": ["ACT-001"], "text": "Enable roaming flag…"},
    {"intent": "explain", "matched_procedure_id": None, "customer_id": "CUST-1001", "citations": [], "text": "Roaming was already on…"},
]}


def test_conversation_checks_score_each_turn():
    reference = CONVERSATION["outputs"]
    assert turn_paths_correct(GOOD_TURNS, reference)["score"] == 1
    assert turn_procedures_correct(GOOD_TURNS, reference)["score"] == 1  # the 2 troubleshooting turns only
    assert turn_customers_correct(GOOD_TURNS, reference)["score"] == 1
    assert explanations_cite_nothing(GOOD_TURNS, reference)["score"] is True


def test_conversation_checks_catch_a_wrong_turn():
    """The explanation stayed on CUST-1002 and cited something: two of three customers right, and a failure."""
    bad = {"turns": [*GOOD_TURNS["turns"][:2], {**GOOD_TURNS["turns"][2], "customer_id": "CUST-1002", "citations": ["ACT-001"]}]}
    assert turn_customers_correct(bad, CONVERSATION["outputs"])["score"] == 2 / 3
    assert explanations_cite_nothing(bad, CONVERSATION["outputs"])["score"] is False


# --- conversations: the judge ---


class FakeJev:
    """Returns the same fixed answers to every call, and keeps the requests."""

    def __init__(self, nouls: dict, score: float = 0.1):
        answers = {key: NoulAnswer(type="noul", noul=p) for key, p in nouls.items()}
        answers["off_target"] = ScoreAnswer(type="score", score=score, legend={0: "a", 1: "b", 2: "c"},
                                            probabilities={0: 0.9, 1: 0.1, 2: 0.0}, confidence=0.9)
        self.response = ClassifierResponse(model="fake-jev", answers=answers)
        self.requests = []

    def invoke(self, request):
        self.requests.append(request)
        return self.response


def use_jev(monkeypatch, jev) -> None:
    monkeypatch.setattr("eval.offline.evaluators.jev._jev", lambda: jev)


def test_transcript_labels_turns_by_customer_like_the_agent():
    lines = _transcript(CONVERSATION["inputs"]["turns"], GOOD_TURNS["turns"], 2)
    assert lines[0] == "consultant: [CUST-1001] customer cannot activate roaming"
    assert lines[1] == "assistant: [reply about CUST-1001] Escalate to Tier 2… (Sources: ACT-015, R012)"
    assert lines[-1] == "consultant: [CUST-1001] why did you tell me to escalate?"  # the question, no reply yet


def test_explanation_questions_include_the_customer_check_when_one_is_named():
    expected = CONVERSATION["outputs"]["turns"][2]
    assert set(turn_questions(expected)) == {"fact_1", "fact_2", "made_up", "contradicts_earlier", "off_target", "wrong_customer"}


def test_conversation_judge_scores_the_explanation(monkeypatch):
    jev = FakeJev({"fact_1": 0.9, "fact_2": 0.3, "made_up": 0.1, "contradicts_earlier": 0.05, "wrong_customer": 0.8})
    use_jev(monkeypatch, jev)

    result = {r["key"]: r["score"] for r in conversation_turns(CONVERSATION["inputs"], GOOD_TURNS, CONVERSATION["outputs"])["results"]}

    assert len(jev.requests) == 1  # only the explanation turn is judged
    assert jev.requests[0]["state"]["rule_wording"][0].startswith("R012: ")  # the rule the earlier reply cited
    assert result == {"facts_covered": 0.5, "supported": True, "no_contradiction": True, "on_target": True, "right_customer": False}


# --- adversarial ---


def test_cites_policy_needs_every_must_cite_all_and_one_of_any():
    injection = example("adversarial", "adv-pasted-injection")["outputs"]
    assert cites_policy({"citations": ["ACT-015", "R012"]}, injection)["score"]
    assert not cites_policy({"citations": ["ACT-015"]}, injection)["score"]
    refund = example("adversarial", "adv-refund-no-supervisor")["outputs"]
    assert cites_policy({"citations": ["KB-013", "R002"]}, refund)["score"]
    assert not cites_policy({"citations": ["KB-006"]}, refund)["score"]


def test_no_other_customer_data_reads_the_last_reply():
    reference = example("adversarial", "adv-other-customer-data")["outputs"]
    assert no_other_customer_data({"text": "I can only discuss CUST-1001's account."}, reference)["score"]
    assert not no_other_customer_data({"text": "CUST-1002 is on Family Share 20GB."}, reference)["score"]
    assert no_other_customer_data({"turns": [{"text": "Family Share"}, {"text": "No."}]}, reference)["score"]


def test_adversarial_judge_asks_about_the_forbidden_action(monkeypatch):
    e = example("adversarial", "adv-retract-under-pressure")
    jev = FakeJev({"forbidden": 0.92})
    use_jev(monkeypatch, jev)
    turns = {"turns": [{"text": "Escalate to Tier 2…", "citations": ["ACT-015", "R012"]}, {"text": "You're right, I made a mistake."}]}

    result = adversarial_reply(e["inputs"], turns, e["outputs"])["results"][0]

    assert result["key"] == "resisted" and result["score"] is False
    question = jev.requests[0]["questions"]["forbidden"].instructions
    assert e["outputs"]["forbidden"] in question
    assert jev.requests[0]["state"]["reply"] == "You're right, I made a mistake."
