"""Builds the knowledge-base suite (datasets/kb.jsonl): general questions the articles answer, or don't.

    uv run python -m eval.offline.build_kb

Each question is something a consultant asks on a call, answered from the knowledge-base articles
(the agent's open_ended path). For each one:
  - articles:  the article that holds the answer. The search must find it among its 5 (recall@5),
               and the higher the better (MRR);
  - facts:     2 or 3 key facts, taken from that article's text. Jev checks the answer states each;
  - reference: a short right answer, which Jev checks the answer doesn't contradict.

12 questions are answerable and cover all 9 article categories. 3 are not: the articles touch the
topic but don't give the detail asked (an exact fee, a price), or don't mention it at all. There
the agent should say the articles don't cover it, not make something up.

Re-running overwrites kb.jsonl, so edit this script, not the file.
"""

import json
from pathlib import Path

from etl.io_utils import load_json

OUT = Path(__file__).resolve().parent / "datasets" / "kb.jsonl"

# (id, question, the article holding the answer, key facts, reference answer)
ANSWERABLE = [
    ("roaming-zone-b", "How are roaming charges billed in Zone B?", "KB-001",
     ["voice calls and SMS are charged at 1.5 times the standard rate",
      "data is billed in 10MB blocks rather than continuously"],
     "Zone B (Eastern Europe, parts of Asia-Pacific and Latin America) applies a 1.5x multiplier to "
     "standard voice and SMS rates, and bills data per 10MB block."),
    ("termination-fee", "What are the early termination fees if a customer cancels their contract early?", "KB-005",
     ["the fee goes down each month and reaches zero at the end of the 24-month term",
      "the fee is based on the device subsidy still remaining, not a flat penalty",
      "the fee is waived automatically once the customer has 24 months of tenure"],
     "On a 24-month contract the fee decreases each month and reaches zero at the end of the term. It is "
     "calculated from the remaining device subsidy, so it varies by device. It is waived automatically at "
     "24 months' tenure; before that a waiver needs supervisor approval and isn't guaranteed."),
    ("data-rollover", "Which plans let unused data roll over, and for how long?", "KB-002",
     ["only postpaid plans of 10GB or more roll unused data over",
      "rolled-over data lasts for the next billing cycle only and is then forfeited",
      "prepaid plans and plans under 10GB don't roll data over"],
     "Postpaid plans of 10GB or more roll unused data into the next cycle only; anything not used then is "
     "forfeited. Prepaid plans and plans under 10GB have no rollover."),
    ("warranty-claim", "How do warranty claims work for a phone with a manufacturing defect?", "KB-009",
     ["a manufacturing defect is repaired at no cost if the phone has no physical damage, liquid exposure or unauthorised repairs",
      "the consultant must confirm the phone is still within its warranty or protection plan before referring it for repair",
      "a phone that fails repeatedly with the same defect should be escalated for a replacement"],
     "A manufacturing defect is repaired free under the manufacturer's warranty (typically 12 months), "
     "unless physical damage, liquid exposure or unauthorised repairs void it. Confirm the phone is still "
     "covered before referring it. Repeated failures with the same defect are escalated for a replacement."),
    ("sim-fee-waiver", "When is the SIM replacement fee waived?", "KB-006",
     ["the first replacement in any rolling 12-month window is free",
      "a second replacement within that window costs the standard fee unless a supervisor approves a waiver",
      "the 12-month window runs from the most recent replacement, not the calendar year"],
     "The first SIM replacement in any rolling 12-month window is free. A second one in the same window "
     "costs the standard fee unless a supervisor approves a waiver. The window runs from the last "
     "replacement, not the calendar year."),
    ("dispute-60", "Can I refund a $60 billing dispute myself, and how long does the customer have to file it?", "KB-013",
     ["a dispute of $50 or more needs supervisor approval before the refund is issued",
      "a billing dispute must be filed within 90 days of the statement date"],
     "No: disputes of $50 or more need supervisor approval before refunding. The customer must file "
     "within 90 days of the statement date the charge appeared on (30 days for roaming charges)."),
    ("suspension-partial-payment", "Does a partial payment lift an account suspension?", "KB-015",
     ["no, the full outstanding balance must be paid before the account is reactivated",
      "reactivation happens once the payment has cleared, not when it is submitted",
      "a bank transfer can take one to three business days to clear"],
     "No. A suspended account is reactivated only once the full outstanding balance is paid and the "
     "payment has cleared; a bank transfer can take one to three business days to clear."),
    ("failed-charge-retry", "If a customer updates their card after a payment failed, is the failed charge retried automatically?", "KB-023",
     ["no, a new payment attempt with the new method has to be triggered explicitly",
      "a new payment method applies from the next scheduled charge"],
     "No. Updating the payment method doesn't retry the failed charge; a new attempt must be triggered "
     "explicitly. The new method applies from the next scheduled charge."),
    ("fraud-escalation", "What has to happen once I escalate a suspected fraud case?", "KB-012",
     ["the escalation must be logged with a case ID within one hour",
      "no billing changes, plan changes or password resets may be made while the case is open",
      "normal servicing resumes only after the fraud team gives written clearance"],
     "Log it with a case ID within one hour. While the case is open, make no billing changes, plan "
     "changes or password resets, even for a verified caller. Resume normal servicing only after the fraud "
     "team gives written clearance."),
    ("outage-credit", "How much service credit does a customer get for an outage?", "KB-014",
     ["the credit is proportional to how long the outage lasted",
      "the credit is capped at one full billing cycle's charges",
      "the outage must be confirmed by network operations as affecting the customer's area"],
     "A credit proportional to the outage's duration, capped at one full billing cycle's charges. Network "
     "operations must confirm the outage in the customer's area; affected accounts are usually credited "
     "automatically in the next billing cycle."),
    ("reset-lost-phone", "Can I send a password reset by email if the customer has lost their phone?", "KB-016",
     ["password resets are only sent by SMS to the phone number on file, not by email",
      "if that phone can't be reached, the caller must go through the in-person or extended verification path"],
     "No. After identity verification, a reset is only sent by SMS to the number on file. If that phone is "
     "lost, route the caller to the in-person or extended verification path instead."),
    ("failed-verification", "If a caller fails identity verification, can I confirm whether they have an account with us?", "KB-011",
     ["no account information may be shared, not even whether an account exists",
      "the consultant should offer the standard alternative verification path",
      "a suspected social-engineering attempt should be logged and escalated through the fraud process"],
     "No. Share nothing, not even whether an account exists; say verification couldn't be completed and "
     "offer the standard alternative verification path. Log and escalate a suspected social-engineering "
     "attempt through the fraud process."),
]

# (id, question, the article on the topic (it doesn't hold the answer), reference answer)
UNANSWERABLE = [
    ("sim-fee-amount", "What's the exact dollar amount of the SIM replacement fee?", "KB-006",
     "The articles say a standard SIM replacement fee applies, but don't give the amount."),
    ("zone-c-price", "How much does the Zone C daily roaming pass cost per day?", "KB-001",
     "The articles say Zone C uses a fixed daily fee, but don't give the price."),
    ("paypal", "Can customers pay their bill with PayPal?", None,
     "The articles don't say whether PayPal is accepted."),
]


def build() -> list[dict]:
    """Turn the questions into golden examples. Stops with an error if an ID or question repeats, an
    article doesn't exist, or the answerable questions don't cover every article category."""
    articles = {a["article_id"]: a for a in load_json("kb_articles.json")}
    rows = [(q[0], q[1]) for q in ANSWERABLE + UNANSWERABLE]
    assert len({r[0] for r in rows}) == len(rows), "an example ID is used twice"
    assert len({r[1] for r in rows}) == len(rows), "a question is used twice"

    examples = []
    for golden_id, question, article_id, facts, reference in ANSWERABLE:
        assert article_id in articles, f"{golden_id}: unknown article {article_id}"
        assert 2 <= len(facts) <= 3, f"{golden_id}: give 2 or 3 key facts"
        examples.append({
            "id": f"kb-{golden_id}",
            "inputs": {"message": question, "customer_id": None},
            "outputs": {"path": "open_ended", "procedure_id": None, "answerable": True,
                        "articles": [article_id], "facts": facts, "reference": reference},
            "metadata": {"suite": "kb", "slice": "answerable", "category": articles[article_id]["category"]},
        })
    for golden_id, question, topic_article, reference in UNANSWERABLE:
        assert topic_article is None or topic_article in articles, f"{golden_id}: unknown article {topic_article}"
        examples.append({
            "id": f"kb-{golden_id}",
            "inputs": {"message": question, "customer_id": None},
            "outputs": {"path": "open_ended", "procedure_id": None, "answerable": False,
                        "articles": [], "facts": [], "reference": reference},
            "metadata": {"suite": "kb", "slice": "unanswerable", "topic_article": topic_article},
        })

    covered = {e["metadata"]["category"] for e in examples if e["outputs"]["answerable"]}
    missing = {a["category"] for a in articles.values()} - covered
    assert not missing, f"no answerable question for categories {sorted(missing)}"
    return examples


if __name__ == "__main__":
    examples = build()
    OUT.write_text("".join(json.dumps(e) + "\n" for e in examples))
    answerable = sum(e["outputs"]["answerable"] for e in examples)
    print(f"wrote {len(examples)} questions to {OUT.relative_to(Path.cwd())}: {answerable} answerable, {len(examples) - answerable} not")
