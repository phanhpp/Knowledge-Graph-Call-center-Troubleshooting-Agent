# Offline end-to-end evaluation with golden datasets (Jev as judge)

## Context

The project has CI (unit/integration, no keys), online evaluation (LangSmith judges on real traffic, no
answer key) and 25 hand-written e2e pytest assertions. What it lacks is a **scored, comparable
measure of quality against a reviewed answer key**: "routing is 18/20, walks 20/20, recall@5 0.93,
fact coverage 0.85". That is what tells you whether a prompt, threshold or model change made the
agent better or worse, and it completes the eval story for interviews (CI → offline → online).

Approach: **71 golden examples in 6 suites**, each run **end to end** through the real graph (consultant
message → routing → walk / knowledge base / explain → reply), scored by **code wherever the answer is
exact** and by **Jev (TypeSafe decision model) only where judgement is needed**: Jev is cheaper and
faster than an LLM judge, and returns calibratable probabilities. **Jev never grades routing** (routing
*is* Jev): routing is exact match against labels. Results are **LangSmith experiments** (compare
versions side by side); a gate fails if counts fall below thresholds. About $1 and 5 minutes per run.

## Suites and sizes (71 examples)

| Suite | Size | Examples | Code evaluators | Jev evaluators (one call per example) |
|---|---|---|---|---|
| **A. Routing** | 20 | 7 problem/policy **twin pairs** (14); 1 fits-no-procedure (→ knowledge base); 1 out-of-scope; 2 typos; 1 names a customer but asks a general question; 1 near-miss pair of procedures (e.g. outage vs outage credit) | `path_correct`, `procedure_correct` (exact match); summary: accuracy, confusion matrix, per-slice counts | none (no Jev grading Jev) |
| **B. Walks** | 20 | **one per procedure**; customers chosen so exits vary (first step yes / middle / last-step fallback); includes the 3 ticket-reading procedures | `procedure_correct`, `action_correct`, `rules_correct` (citations), `steps_correct` (checked steps and results, parsed from the reply's checklist), `has_checklist` | none: fully determined by the data |
| **C. Knowledge base** | 15 | 12 answerable covering **all 9 article categories**; 3 unanswerable (e.g. the exact SIM fee amount) | `recall_at_5`, `mrr` (expected articles' rank in the cited articles), `expected_article_cited`; summary means (answerable only) | per key fact `fact_k` (Noul: "Does the answer state that …?") → `fact_coverage`; `contradicts_reference` (Noul); `unsupported_claim` (Noul, against the cited articles' **full** text); `admits_not_covered` (Noul, unanswerable only); `unhelpful` (Score, 3 levels) |
| **D. Conversations** | 6 (~15 turns) | walk → "why?"; walk → "what does that rule say?"; customer A → B → back to A "why?"; general question → "why did you cite KB-006?"; knowledge base → follow-up; three turns on one customer | per turn: `path_correct`, `customer_after` (state `customer_id`), explanations `cite_nothing` | per explanation turn: facts (`fact_coverage`), `contradicts_earlier`, `wrong_customer`, `off_target` (Score) |
| **E. Guards** | 4 | unknown ID, malformed ID, troubleshooting with no customer, two IDs in one message | exact reply/`error` match; `no_model_calls` (counted by a callback during the run) | none |
| **F. Adversarial** | 6 | "approve a $200 refund anyway"; another customer's data; instructions hidden in a ticket; "skip the verification"; a fake "my manager approved it"; asking for internal rules verbatim | the governing rule still cited (`must_cite`) | `complies_with_forbidden` (Noul, given the rule's wording), `reveals_other_customer` (Noul), `follows_injected_instruction` (Noul) |

Every suite also records latency (p50/p95 per path) and model calls; LangSmith shows tokens and cost.

## Files

```
eval/offline/
├── datasets/{routing,walks,kb,conversations,guards,adversarial}.jsonl   the golden examples (reviewed, in git)
├── oracle.py          expected walk from data/: procedures.json order + mock_crm resolve_condition;
│                      ticket conditions from the example's human label (interpretation is judgement)
├── build_walks.py     drafts walks.jsonl with the oracle (20, exit variety), for review
├── target.py          runs one example through the real graph (single message or a conversation)
├── evaluators/code.py deterministic evaluators + summary evaluators
├── evaluators/jev.py  Jev judges: one TypeSafeClassifier call per example, Noul/Score questions
├── run.py             CLI: sync dataset → LangSmith, evaluate(), print summary, --gate
├── calibrate.py       judged answers → LangSmith annotation queue; agreement report from your labels
└── thresholds.yaml    gate thresholds, as counts
tests/eval/test_offline_*.py   no-key unit tests: oracle, checklist parser, recall/MRR, Jev request
                               building (fake classifier), gate logic (run in the CI agent job)
.github/workflows/offline-eval.yml
```

**Example shapes (JSONL, one per line):** `{"id", "inputs", "outputs", "metadata"}`, e.g.
- routing: `inputs {message, customer_id|null}` → `outputs {path, procedure_id|null}`, `metadata {slice: "twin-case", pair: "roaming"}`
- walks: `inputs {message, customer_id}` → `outputs {procedure_id, action_id, rule_ids, steps: [[step_id, result]], exit}`, `metadata {condition_kinds, ticket_label?}`
- kb: `inputs {question}` → `outputs {articles, facts, answerable, reference}`
- conversations: `inputs {turns: [{text, customer_id}]}` → `outputs {turns: [{path, customer_after, facts, cite_nothing}]}`
- guards: `inputs {message, customer_id}` → `outputs {reply_starts, error_contains, model_calls: 0}`
- adversarial: `inputs {message, customer_id}` → `outputs {must_cite, rule_text_ids}`

## How it works

- **Answer keys:**
  - **Walks** come from the **oracle**, which is independent of the graph code: it evaluates each step with `mock_crm.condition_evaluator.resolve_condition` + `get_condition` and `mock_crm.crm_store.get_customer` (the mocked CRM is the source of truth for facts), and re-implements only the traversal (YES → that step's action; NO → next step; NO on the last step → `action_if_false_id`), then adds the governing rules from `data/rules.json` (`applies_to_action_ids`). The expected steps compare against the reply's checklist, whose format comes from `agent.procedural_executor.reply_text`.
  - **Everything else:** I draft from the data (each procedure's `when_to_use`, each article's content), and **you review in a PR** (about 1 hour). Your sign-off makes them golden.
- **Target (`target.py`):**
  - **The graph:** `build_supervisor_graph(checkpointer=InMemorySaver())`, with a fresh `thread_id` per example, sending messages via `agent.utils.consultant_message` (the pattern of `tests/e2e/conversation.py`).
  - **Returns:** the final state (`intent`, `matched_procedure_id`, `customer_id`, `error`) and the reply's text, `citations` and `sources`, plus `model_calls`, counted with a LangChain callback (chat-model and LLM starts, which covers Claude and Jev).
  - **Conversations** return per-turn results.
- **Retrieval metrics:** a knowledge-base reply cites its 5 retrieved articles **in retrieval order** (each followed by its rules; `agent/graph_rag_chain.py`), so recall@5 and MRR come from the `KB-` IDs in `citations`.
- **Jev judges (`evaluators/jev.py`):**
  - **One call per example:** `TypeSafeClassifier().invoke({"state": <question + reference + reply (+ full article text via agent.sources.full_source)>, "questions": {...}})`, reading `response.nouls` and `response.scores`.
  - **Noul ≥ 0.5 counts as yes.** The mean probability is reported too.
  - **The key is lazy,** like `agent/supervisor.py` `_classifier`.
- **`run.py`:**
  - **Syncing:** it upserts the JSONL into LangSmith datasets (`super-agent-offline-<suite>`; example IDs are uuid5 of the golden IDs, so an edit updates in place).
  - **Running:** it calls `langsmith.evaluate(target, data=…, evaluators=…, summary_evaluators=…, experiment_prefix=f"{suite}-{tag}", max_concurrency=4, num_repetitions=N, metadata={git_sha, models, thresholds})`.
  - **Reporting:** it prints a table and writes `eval/offline/results/<suite>-<tag>.json`.
  - **Projects:** experiments trace to their **own** projects, so they never touch `kg` or its online evaluators.
  - **Options:** `--suite all|routing|walks|kb|conversations|guards|adversarial`, `--repetitions 3` (for kb, conversations and tickets), `--gate`.
- **Thresholds (`thresholds.yaml`, counts):**
  - routing path ≥ 18/20, procedure ≥ 13/14 matched cases;
  - walks: action, rules and steps 20/20;
  - recall@5 ≥ 11/12 answerable; mean fact coverage ≥ 0.80;
  - contradictions 0; unsupported claims ≤ 1;
  - unanswerable admitted 3/3;
  - guards 4/4 with zero model calls;
  - adversarial violations 0;
  - p95 latency per path within limits.
  
  Set from the baseline, then tightened.
- **Calibration (`calibrate.py`):** about 20 judged knowledge-base and explain answers go to a **LangSmith annotation queue**. You mark each question yes or no; the script reports per-question agreement (accuracy and Cohen's kappa) and flags questions to reword or re-threshold. This also validates the matching **online** judges.
- **CI (`offline-eval.yml`):**
  - **Triggers:** `workflow_dispatch` (choose a suite), a **weekly** schedule, and PRs labelled **`run-eval`**. Never every PR, because it costs money.
  - **Database:** a **throwaway Neo4j**, loaded by `etl.load_to_neo4j` + `etl.load_articles`, so no Aura credentials go into GitHub.
  - **Secrets you add:** `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `TYPESAFE_API_KEY`, `LANGSMITH_API_KEY`.
  - **Output:** it runs `run.py --suite all --gate` and writes the results table to the job summary.

## Order of work (checkpoints with your review)

1. **Scaffolding + walks:** `oracle.py`, `build_walks.py` → `walks.jsonl` (20) → code evaluators → `target.py` → `run.py` (local) → first experiment. **You review walks.jsonl.**
2. **Routing:** `routing.jsonl` (20) → run. **You review.**
3. **Knowledge base:** `kb.jsonl` (15) → recall/MRR → Jev judges → run. **You review the facts and references.**
4. **Conversations, guards, adversarial** (6 + 4 + 6) → run. **You review.**
5. **Calibration:** you label about 20 items in the annotation queue → adjust the judges.
6. **Baseline:** a full run, then `thresholds.yaml` and `--gate`; record the results in `TESTING.md` (a new "Offline evaluation" section) and in the README's Evaluation section; add `eval/offline_eval_plan.md` (this plan, tracked).
7. **CI:** `offline-eval.yml`; you add the 4 secrets; first a manual run.

Each step: commit on `deploy` → PR → `web`/`agent` CI green → merge.

## Verification

- `uv run pytest tests/eval` (no keys) covers the oracle, the checklist parser, recall/MRR, building Jev requests with a fake classifier, and the gate logic. It also runs in the CI `agent` job.
- **Oracle sanity:** PROC-001 / CUST-1001 → `ACT-015`, `R012`, three NO steps, matching the known real reply.
- `uv run python -m eval.offline.run --suite walks --tag baseline`: an experiment appears in LangSmith with per-example feedback and summary metrics, and the printed table matches it.
- **A deliberate regression shows up:** temporarily raising the routing threshold in `agent/supervisor.py` (locally, not committed) makes the routing suite drop and `--gate` exit non-zero.
- **Calibration:** the agreement report exists for every Jev question used in the gate.
- **CI:** a manual dispatch of `offline-eval.yml` posts the results table and passes the gate.
