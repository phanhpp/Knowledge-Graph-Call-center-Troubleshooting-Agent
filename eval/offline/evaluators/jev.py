"""Jev judges: for answers code can't check exactly, Jev reads the answer and says how likely "yes" is.

One Jev call per example asks all of its questions. Jev reads:
    the consultant's question, the reference answer, the agent's answer, and the articles the
    search returned, exactly as Claude saw them when writing the answer: the full text, then
    "Policy rules cited by this article: R…". (Without that line, an answer naming the rule looked
    unsupported: Jev must judge against what the model was given, nothing less.)

The questions (Noul = the probability that the answer is yes; Score = a level on a scale):
    fact_1 … fact_n  Noul   Does the answer state that <key fact>?            (answerable only)
    contradicts      Noul   Does it contradict the reference answer?          (answerable only)
    admits_gap       Noul   Does it say the articles don't give this?         (unanswerable only)
    unsupported      Noul   Does it state anything the articles don't support?
    unhelpful        Score  0 usable, 1 partly usable, 2 not usable on a live call

A probability of 0.5 or more counts as yes. The scores sent to LangSmith are named so that higher is
better, like the code checks: facts_covered (the share of key facts stated), no_contradiction,
supported, usable, admits_gap. Each score's comment keeps Jev's raw numbers, for calibration.
The unsupported and unhelpful wording matches the live LangSmith judges (kb_unsupported_claim,
kb_unhelpful), so an offline score and an online score mean the same thing.
"""

from functools import lru_cache

from langchain_typesafe import Noul, NoulCriteria, Score

from etl.io_utils import load_json

YES = 0.5  # a Noul probability at or above this counts as yes


def _jev():
    """The agent's own Jev client: built once, retried when Jev is busy (agent/supervisor.py)."""
    from agent.supervisor import _classifier

    return _classifier()


@lru_cache(maxsize=1)
def _kb_articles() -> dict[str, dict]:
    """data/kb_articles.json by article ID (what the graph's Article nodes and CITES_RULE edges are loaded from)."""
    return {a["article_id"]: a for a in load_json("kb_articles.json")}


def _articles(outputs: dict) -> list[dict]:
    """The articles the search returned, in search order, as Claude saw them (agent/graph_rag_chain.py
    _format_article): the full text, then the rules the article cites."""
    articles = []
    for article_id in [c for c in outputs.get("citations", []) if c.startswith("KB-")][:5]:
        article = _kb_articles().get(article_id)
        if article:
            text = article["content"]
            if article.get("related_rule_ids"):
                text += f"\nPolicy rules cited by this article: {', '.join(article['related_rule_ids'])}"
            articles.append({"id": article_id, "title": article["title"], "text": text})
    return articles


def kb_questions(reference: dict) -> dict:
    """The questions Jev answers about one knowledge-base answer (see the module docstring)."""
    questions = {}
    for n, fact in enumerate(reference["facts"], start=1):
        questions[f"fact_{n}"] = Noul(
            instructions=f"Does the answer state that {fact}? Saying the same thing in other words counts.",
            criteria=NoulCriteria(
                true="The answer gives this fact, in any wording.",
                false="The answer leaves it out, or says something different.",
            ),
        )
    if reference["answerable"]:
        questions["contradicts"] = Noul(
            instructions="Does the answer contradict the reference answer?",
            criteria=NoulCriteria(
                true="It states something that conflicts with the reference: a different number, limit, time, condition or rule.",
                false="Everything it says agrees with the reference, or is something the reference doesn't address. Leaving something out is not a contradiction.",
            ),
        )
    else:
        questions["admits_gap"] = Noul(
            instructions="The articles don't give the information this question asks for. Does the answer say so, instead of giving a specific answer?",
            criteria=NoulCriteria(
                true="It says plainly that the articles don't give this, possibly adding what they do say.",
                false="It gives a specific answer (an amount, a yes or no) as if the articles stated it, or doesn't address the question.",
            ),
        )
    questions["unsupported"] = Noul(
        instructions="The articles are the knowledge-base articles the answer was written from. Does the answer state anything the articles don't support?",
        criteria=NoulCriteria(
            true="It gives a fact, number, condition or policy that isn't in the articles, or says more than they do.",
            false="Every statement is backed by the articles. Saying the articles don't cover something is fine.",
        ),
    )
    questions["unhelpful"] = Score(
        instructions="How usable is the answer for a consultant answering this question on a live call?",
        criteria=[
            "Usable: answers the question directly, or says plainly that the articles don't cover it.",
            "Partly usable: answers only part of it, is vague, or buries the answer.",
            "Not usable: doesn't answer, answers a different question, or is confusing.",
        ],
    )
    return questions


KB_KEYS = ["facts_covered", "supported", "usable", "no_contradiction", "admits_gap"]


def kb_answer(inputs: dict, outputs: dict, reference_outputs: dict) -> dict:
    """Judge one knowledge-base answer with one Jev call; returns several scores (None = doesn't apply)."""
    reference = reference_outputs
    state = {
        "question": inputs["message"],
        "reference_answer": reference["reference"],
        "answer": outputs.get("text", ""),
        "articles": _articles(outputs),
    }
    try:
        response = _jev().invoke({"state": state, "questions": kb_questions(reference)})
    except Exception as error:  # Jev still failing after its retries: mark it, so run.py asks for a rerun
        return {"results": [{"key": key, "score": None, "comment": f"Jev judge failed: {error!r}", "extra": {"error": True}}
                            for key in KB_KEYS]}
    nouls, scores = response.nouls, response.scores

    fact_yes = [nouls[f"fact_{n}"].noul for n in range(1, len(reference["facts"]) + 1)]
    unsupported = nouls["unsupported"].noul
    unhelpful = scores["unhelpful"].score
    results = [
        {"key": "facts_covered",
         "score": sum(p >= YES for p in fact_yes) / len(fact_yes) if fact_yes else None,
         "comment": ", ".join(f"fact_{n} {p:.2f}" for n, p in enumerate(fact_yes, start=1)) or None},
        {"key": "supported", "score": unsupported < YES, "comment": f"unsupported {unsupported:.2f}"},
        {"key": "usable", "score": unhelpful < 0.5, "comment": f"unhelpful level {unhelpful:.2f} (0 usable … 2 not usable)"},
    ]
    if reference["answerable"]:
        contradicts = nouls["contradicts"].noul
        results.append({"key": "no_contradiction", "score": contradicts < YES, "comment": f"contradicts {contradicts:.2f}"})
        results.append({"key": "admits_gap", "score": None})
    else:
        admits = nouls["admits_gap"].noul
        results.append({"key": "no_contradiction", "score": None})
        results.append({"key": "admits_gap", "score": admits >= YES, "comment": f"admits_gap {admits:.2f}"})
    return {"results": results}


# Which judges run for each suite (run.py adds them to the code checks).
JUDGES = {
    "kb": [kb_answer],
}
