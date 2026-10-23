"""Tests for the offline evaluation's knowledge-base suite that need no keys and no model calls: the
saved questions, the search checks (recall@5, MRR), the Jev judge with a fake Jev, and the gate's
mean thresholds. The real end-to-end run, with model calls, is
`uv run python -m eval.offline.run --suite kb`."""

import json
from pathlib import Path

from langchain_typesafe import ClassifierResponse, NoulAnswer, ScoreAnswer

from eval.offline.build.kb import build
from eval.offline.evaluators.code import mrr, recall_at_5
from eval.offline.evaluators.jev import kb_answer, kb_questions
from eval.offline.run import gate

KB = Path(__file__).resolve().parents[2] / "eval" / "offline" / "datasets" / "kb.jsonl"


def questions() -> list[dict]:
    return [json.loads(line) for line in KB.read_text().splitlines() if line.strip()]


def example(golden_id: str) -> dict:
    return next(e for e in questions() if e["id"] == golden_id)


# --- the saved questions ---


def test_kb_file_is_what_its_build_script_makes():
    """build() also checks the questions (articles exist, all 9 categories covered, no repeats). If
    this fails, kb.jsonl was edited by hand or not rebuilt: run `uv run python -m eval.offline.build.kb`."""
    assert questions() == build()


def test_unanswerable_questions_have_no_article_or_facts():
    unanswerable = [e for e in questions() if not e["outputs"]["answerable"]]
    assert len(unanswerable) == 3
    assert all(e["outputs"]["articles"] == [] and e["outputs"]["facts"] == [] for e in unanswerable)


# --- recall@5 and MRR, read from the reply's citations ---

CITED = {"citations": ["KB-005", "R005", "KB-006", "R004", "KB-021", "KB-022", "KB-009"]}


def test_recall_and_mrr_find_the_article_in_search_order():
    """Rules are cited between articles; only the KB- IDs count, in the order the search returned."""
    reference = {"articles": ["KB-006"]}
    assert recall_at_5(CITED, reference)["score"] == 1.0
    assert mrr(CITED, reference)["score"] == 0.5  # second article


def test_recall_and_mrr_when_the_article_is_missing():
    reference = {"articles": ["KB-014"]}
    assert recall_at_5(CITED, reference)["score"] == 0.0
    assert mrr(CITED, reference)["score"] == 0.0


def test_search_checks_dont_apply_to_unanswerable_questions():
    assert recall_at_5(CITED, {"articles": []})["score"] is None
    assert mrr(CITED, {"articles": []})["score"] is None


# --- the Jev judge, with a fake Jev ---


class FakeJev:
    """Returns fixed answers, and keeps the request it was sent."""

    def __init__(self, nouls: dict, unhelpful: float = 0.1):
        answers = {key: NoulAnswer(type="noul", noul=p) for key, p in nouls.items()}
        answers["unhelpful"] = ScoreAnswer(type="score", score=unhelpful, legend={0: "a", 1: "b", 2: "c"},
                                           probabilities={0: 0.9, 1: 0.1, 2: 0.0}, confidence=0.9)
        self.response = ClassifierResponse(model="fake-jev", answers=answers)
        self.request = None

    def invoke(self, request):
        self.request = request
        return self.response


def use_jev(monkeypatch, jev) -> None:
    monkeypatch.setattr("eval.offline.evaluators.jev._jev", lambda: jev)


def scores(result: dict) -> dict:
    return {r["key"]: r["score"] for r in result["results"]}


def test_answerable_questions_ask_about_each_fact_and_contradiction():
    reference = example("kb-sim-fee-waiver")["outputs"]
    asked = kb_questions(reference)
    assert set(asked) == {"fact_1", "fact_2", "fact_3", "contradicts", "unsupported", "unhelpful"}
    assert reference["facts"][0] in asked["fact_1"].instructions


def test_unanswerable_questions_ask_whether_it_admits_the_gap():
    asked = kb_questions(example("kb-paypal")["outputs"])
    assert set(asked) == {"admits_gap", "unsupported", "unhelpful"}


def test_judge_turns_probabilities_into_scores(monkeypatch):
    """0.5 or more counts as yes: 2 of 3 facts stated, no contradiction, nothing unsupported."""
    e = example("kb-sim-fee-waiver")
    jev = FakeJev({"fact_1": 0.94, "fact_2": 0.71, "fact_3": 0.12, "contradicts": 0.03, "unsupported": 0.08})
    use_jev(monkeypatch, jev)
    outputs = {"text": "The first replacement in 12 months is free.", "citations": ["KB-006", "R004"]}

    result = scores(kb_answer(e["inputs"], outputs, e["outputs"]))

    assert result == {"facts_covered": 2 / 3, "supported": True, "usable": True,
                      "no_contradiction": True, "admits_gap": None}
    state = jev.request["state"]
    assert state["question"] == e["inputs"]["message"] and state["answer"] == outputs["text"]
    assert [a["id"] for a in state["articles"]] == ["KB-006"]  # the cited articles, as Claude saw them:
    assert "rolling 12-month window" in state["articles"][0]["text"]  # the full text,
    assert state["articles"][0]["text"].endswith("Policy rules cited by this article: R004")  # then its rules


def test_judge_on_an_unanswerable_question(monkeypatch):
    e = example("kb-sim-fee-amount")
    use_jev(monkeypatch, FakeJev({"admits_gap": 0.2, "unsupported": 0.9}, unhelpful=1.4))

    result = scores(kb_answer(e["inputs"], {"text": "It's $25.", "citations": []}, e["outputs"]))

    assert result == {"facts_covered": None, "supported": False, "usable": False,
                      "no_contradiction": None, "admits_gap": False}


def test_a_failed_judge_is_marked_as_an_error_not_a_score(monkeypatch):
    """So run.py lists it as errored (rerun), instead of as a wrong answer or as "doesn't apply"."""
    class BrokenJev:
        def invoke(self, request):
            raise ConnectionError("Jev down")

    use_jev(monkeypatch, BrokenJev())
    e = example("kb-paypal")
    results = kb_answer(e["inputs"], {"text": "...", "citations": []}, e["outputs"])["results"]
    assert results and all(r["extra"] == {"error": True} and r["score"] is None for r in results)


# --- the gate with mean thresholds and checks that don't apply to every example ---


def test_gate_counts_only_the_examples_a_check_applies_to():
    """recall_at_5 applies to the 12 answerable questions of 15: 11 of 12 passes a threshold of 11."""
    summary = {"suite": "kb", "examples": 15, "repetitions": 1,
               "checks": {"recall_at_5": {"passed": 11, "total": 12, "mean": 0.92}}}
    assert gate(summary, {"kb": {"recall_at_5": 11}}) == []
    assert gate(summary, {"kb": {"recall_at_5": 12}}) == ["kb.recall_at_5: 11/12 passed, needs 12/12"]


def test_gate_with_a_mean_threshold():
    summary = {"suite": "kb", "examples": 15, "checks": {"mrr": {"passed": 9, "total": 12, "mean": 0.83}}}
    assert gate(summary, {"kb": {"mrr": {"mean": 0.8}}}) == []
    assert gate(summary, {"kb": {"mrr": {"mean": 0.9}}}) == ["kb.mrr: mean 0.83, needs 0.9"]
