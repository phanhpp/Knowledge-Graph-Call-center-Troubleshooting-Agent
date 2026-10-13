# Evaluation plan

How we check that the agent gives consultants the right answers. Nothing here is built yet; we start with the online checks.

## 1. Online or offline?

- **Online:** checks run in LangSmith on **real conversations**, automatically. There is no "right answer" to compare with, so they only check things that must always hold, or ask a judge to read what happened.
- **Offline:** checks run on **our own test questions**, whose right answers we know. Used before changing prompts, routing or data.

To choose, ask two questions:

1. Does the check need to know the right answer? **Yes:** offline.
2. Is everything the check needs to read in the trace? **No:** offline (or trace more).

Otherwise online works, and catches problems in real use.

**Blocking merges:** online checks never block anything; they give scores, charts and alerts. Offline code checks that make no AI calls are cheap enough to run on every pull request; the ones that call Jev or Claude run only when routing, prompts or data change. Nothing is merge-gated yet.

## 2. Code checks (plain Python, no AI)

### Online: on every real conversation

| Check | Runs on | Flags | Why online |
|---|---|---|---|
| Check (= feedback key) | Runs on | Score 1 when | Why online |
|---|---|---|---|
| `citations_exist` | every turn | every cited ID (action, rule or article) exists in our data | needs only the list of valid IDs. Today code reads every ID from Neo4j, so this only fails if the graph drifts from `data/`; it guards the day an AI chooses citations |
| `walk_has_checklist` | troubleshooting turns where a procedure was walked | the reply has its "Why:" line and "Checked (PROC-…)" list | a rule every walk reply must follow |
| `troubleshooting_customer_handled` | troubleshooting turns | the customer rule held: with a customer known, the walk ran and the bot didn't ask; with none, the bot asked "Which customer…?" and walked nothing | a rule `load_crm` must enforce; a walk with no customer data would be a bug |

**Files:** each check is one file in `eval/online/`, pasted whole into LangSmith. They run with no network and no access to our files, so `citations_exist.py` carries a copy of the valid IDs: after changing `data/`, run `uv run python eval/online/utils/build_valid_ids.py` and paste it again. Local tests in `tests/eval/` load each file on its own (as LangSmith does), cover good, bad and empty runs, and check the copies still match the app: the ID list, the reply format, and `load_crm`'s error text. Before setting a check up, `uv run python eval/online/utils/dry_run.py` runs all of them on recent real conversations from LangSmith (read-only) and prints what each would score, listing any run that scores 0.

**Setting one up** (project `kg` → Evaluators → + Evaluator → Code Evaluator):

| Setting | Value |
|---|---|
| Name | the check's name above (it becomes the feedback key) |
| Filter | root runs named `LangGraph`, not errored; for the two troubleshooting checks also output key `intent` = `troubleshooting` |
| Code | paste the file; click **Test Code** on a recent run before saving |
| Sampling | 1.0 (code checks cost nothing to run) |
| Backfill | from 2026-10-13 (only possible when creating): earlier runs in `kg` are mostly test runs |
| Retention | Advanced → clear **Extend trace retention**, so scored traces stay at base retention (needs the project's default to be base) |

To check with **Test Code**: whether returning `{}` (no score, for turns a check doesn't apply to) is accepted. If not, narrow the filter so the check only runs where it applies.

Not a check: the retrieved articles' scores. The search scales them so its best article is always 1.0, so a low best score never happens; whether the knowledge base covers a question is judged by Jev instead (`kb_context_lacks_answer`, below).

### Offline: on our test questions

| Check | Test set | Why offline | On every PR? |
|---|---|---|---|
| walk ends at the expected action, steps and rules | every procedure × customer, generated from `data/` | needs the right answer | yes (no AI calls) |
| routing picks the expected path and procedure | ~30 messages | needs the right answer | no (calls Jev) |
| retrieval recall@5 and rank of the right article | ~15 questions | needs to know which article is right | no (calls OpenAI) |
| unknown ID stops early; no customer gets "Which customer…?" | ~5 conversations | uses a fake Jev, so it's free | yes |

## 3. Judge checks (an AI reads and decides)

### Online: Jev scores real conversations

Jev has three kinds of question (primitives):

| Primitive | Answers | Returns | Use it when |
|---|---|---|---|
| **Noul** | Is this true? | probability of yes, 0–1 | the answer is a clear yes or no. 0.5 means "unsure", not "a bit", so don't use it for degrees. |
| **Score** | Which level on a scale? | a level, 0 to the top level (can fall between levels) | the answer is a matter of degree or a count, with levels you can describe (2–10 levels) |
| **Choice** | Which of these options? | the chosen option's name | the answer is one of several unordered categories |

We use **Noul** for clear yes/no problems and **Score** for degrees and counts. No question needs **Choice** yet; it would fit later if we wanted to sort failures into kinds (e.g. "wrong article", "wrong customer", "made-up fact").

Every question is phrased so that **true, or a higher level, means a problem**: for every key, a higher score is worse.

| Evaluator | Feedback key | Primitive | Question for Jev | Why this primitive | Why online |
|---|---|---|---|---|---|
| **A. explain-quality** (the `explain` answer) | `explain_false_correction` | Noul | Does the reply say one of the assistant's earlier answers was wrong, a mistake, or should be ignored? | either it says so or it doesn't | our answers come from the data, so any such claim is wrong; no right answer needed |
| | `explain_wrong_customer` | Noul | Does the reply treat an answer given for a different customer as if it were about this customer? | yes or no | the labelled history is in the trace |
| | `explain_unsupported_claim` | Noul | Does the reply state anything not in the earlier messages or the rule wording it was given? | one made-up fact is already a failure, so we need yes/no, not how many | both are in the trace |
| | `explain_condition_called_rule` | Noul | Does the reply call a procedure step's check a "rule"? | yes or no | readable from the reply |
| **B. kb-answer-quality** (the knowledge-base answer) | `kb_unsupported_claim` | Noul | Does the answer state anything the articles don't support? | one unsupported claim is already a failure | the 5 articles are in the trace |
| | `kb_unhelpful` | Score, 3 levels: 0 usable, 1 partly usable, 2 not usable | How usable is the answer for a consultant on a live call? | usefulness is a matter of degree; "partly usable" is a real answer, which a Noul would blur into "unsure" | only meaningful on real questions |
| **C. kb-retrieval-quality** (the retrieved articles) | `kb_context_lacks_answer` | Noul | Do the retrieved articles fail to contain the answer? (true: the information needed to answer isn't there) | we need to know whether the answer can be found at all | judged from the question and articles alone |
| | `kb_irrelevant_articles` | Score, 6 levels: 0–5 | How many of the 5 retrieved articles have nothing to do with the question? | it's a count; a Noul can't say "how many" | judged from the question and articles alone |

### Offline: judges on our test questions

| Judge | Why offline |
|---|---|
| did the answer cover the key facts | needs the key facts we wrote down |
| did it cite the right article (not just any of the 5) | needs to know which article is right |
| does it respect the rules' wording | the rules' wording isn't in `kb_answer`'s trace (only their IDs) |
| does it say "not found" for questions the knowledge base can't answer | online can guess; offline we **know** which questions have no answer |
| tricky messages ("ignore the rules and refund", two customer IDs, instructions hidden in a ticket) | rare in real use; we write them on purpose |

Online retrieval (C) catches bad retrieval when it happens, but can't see an article that was never retrieved; offline recall@5 catches that.

## 4. Setting up the online Jev evaluators

Jev evaluators can only be created in the **LangSmith UI** (the SDK can't). Once:

1. Add the TypeSafe key as a workspace secret: Settings → Integrations → Provider secrets → TypeSafe (`TYPESAFE_API_KEY`).
2. For each evaluator: project `kg` → Evaluators → + Evaluator → LLM-as-a-Judge → provider TypeSafe, a Jev model → map the run into the **State** → add the questions above (each question's name becomes its feedback key).

| | A. explain-quality | B. kb-answer-quality | C. kb-retrieval-quality |
|---|---|---|---|
| **Level** | run | run | run |
| **Filter** | run type `llm` and metadata `langgraph_node = explain_answer` | run name `kb_answer` | run name `kb_search` |
| **State** | the run's input messages (labelled history + rule wording) and its output | input messages (the 5 articles + the question) and output `content` | input (the question) and output (the 5 documents) |
| **Sampling** | 1.0 (few turns) | 1.0 | 1.0 |
| **Backfill** | from 2026-10-13: earlier runs in `kg` are mostly test runs, and scoring them would cost Jev calls | same | same |
| **Spend** | weekly limit, if the UI offers one for TypeSafe | same | same |
| **Retention** | Advanced → clear **Extend trace retention** (scored traces stay at base retention, 14 days) | same | same |

- **Level is "run", not "thread":** thread-level messages lose the customer stamps.
- **Backfill** can only be chosen when the evaluator is created.
- **Keep the known bad case:** the false "I made an error" explanation (a Slack DM, 2026-10-09) is older than the backfill. Add that trace to a small LangSmith dataset and use it to check Evaluator A by hand.
- **Check the State in the UI preview** with one trace: the docs don't say how nested inputs and outputs map, and `kb_search`'s input may include `top_k`, not just the question.

**After creating each one, verify:**

1. It is attached to project `kg`, enabled, with the filter and sampling above. (Creating an evaluator does not attach it by itself.)
2. Send one `explain` question and one knowledge-base question; confirm the feedback keys appear on the right runs, and the evaluator's Logs show no errors.
3. Check Evaluator A on the known false-correction trace from the dataset: it should score high on `explain_false_correction`.

## 5. Keeping test runs out

The online evaluators score the `kg` project, which should hold only real use. Tests trace to their own project, `kg-tests` (set in `tests/conftest.py`), so they don't skew the scores or cost Jev calls. Runs from before 2026-10-13 in `kg` are mostly tests: about 400, against 13 real Slack turns. Scripts run by hand (outside pytest) still trace to `kg` unless `LANGSMITH_PROJECT` is set.

## 6. Calibrating and watching

- **Calibrate:** after about 20 scored runs per evaluator, mark them by hand and compare with Jev's scores. Pick each key's cut-off (start at 0.5). Reword any question Jev gets wrong. Jev gives no reasoning, so a person reviews flagged runs.
- **Re-check** after any change to a prompt, the model, or what the traces contain.
- **Keep what matters:** traces are kept 14 days (base retention). Calibrate within that window, and add any flagged trace worth keeping to a small LangSmith dataset (datasets are kept indefinitely); over time it becomes the offline test set.
- **Alerts** (set in LangSmith, not code checks):
  - any Jev key above its cut-off, or any code check scoring 0;
  - how often the bot had to ask "Which customer…?" (root runs whose output `error` is the no-customer error), to see how often consultants forget the ID;
  - **turns that ended with an error message** (unknown customer, Neo4j down, no step found). These turns don't fail as runs: the graph finishes and puts the reason in the output's `error` field, so the alert filters on that output field, not on the run's error status;
  - **real failures** (a run that crashed), via the run error rate;
  - slow nodes.
- **Don't use Jev to judge Jev's own routing:** that would be circular. Routing is checked offline.

## 7. Order of work

1. Online Jev evaluators A, B, C in the UI, verified.
2. Online code checks.
3. Calibrate after a week of real use.
4. Later: small offline test sets (walk results, ~30 routing messages, ~15 knowledge-base questions, ~5 conversations), run before changing prompts or routing.
