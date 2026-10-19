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


| Check (= feedback key)             | Scores (decided in its code)                                                                                        | Score 1 when                                                                                                                                       | Why online                                                                                                                                                             |
| ---------------------------------- | ------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `citations_exist`                  | troubleshooting and knowledge-base turns whose reply cites something (explanations and fixed messages cite nothing) | every cited ID (action, rule or article) exists in our data                                                                                        | needs only the list of valid IDs. Today code reads every ID from Neo4j, so this only fails if the graph drifts from `data/`; it guards the day an AI chooses citations |
| `walk_has_checklist`               | troubleshooting turns where a procedure was walked                                                                  | the reply has its "Why:" line and "Checked (PROC-…)" list                                                                                          | a rule every walk reply must follow                                                                                                                                    |
| `troubleshooting_customer_handled` | troubleshooting turns                                                                                               | the customer rule held: with a customer known, the walk ran and the bot didn't ask; with none, the bot asked "Which customer…?" and walked nothing | a rule `load_crm` must enforce; a walk with no customer data would be a bug                                                                                            |


**Files:** each check is one file in `eval/online/`, pasted whole into LangSmith. They run with no network and no access to our files, so `citations_exist.py` carries a copy of the valid IDs: after changing `data/`, run `uv run python eval/online/utils/build_valid_ids.py` and paste it again. Local tests in `tests/eval/` load each file on its own (as LangSmith does), cover good, bad and empty runs, and check the copies still match the app: the ID list, the reply format, and `load_crm`'s error text. Before setting a check up, `uv run python eval/online/utils/dry_run.py` runs all of them on recent real conversations from LangSmith (read-only) and prints what each would score, listing any run that scores 0.

**Setting one up** (project `kg` → Evaluators → + Evaluator → Code Evaluator):


| Setting   | Value                                                                                                                                                                                                                                                                                                                                     |
| --------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Name      | the check's name above (it becomes the feedback key)                                                                                                                                                                                                                                                                                      |
| Filter    | root runs (traces), not errored: the same for all three. In the UI: **Is Trace = true**, which is saved as `eq(is_root, true)` (see section 4). No run-name filter (with `name = LangGraph` the rules didn't run) and no output-key filter (see below); each check's code decides which turns it scores and returns no score for the rest |
| Code      | paste the file; click **Test Code** on a recent run before saving                                                                                                                                                                                                                                                                         |
| Sampling  | 1.0 (code checks cost nothing to run)                                                                                                                                                                                                                                                                                                     |
| Backfill  | from 2026-10-13 (only possible when creating): earlier runs in `kg` are mostly test runs                                                                                                                                                                                                                                                  |
| Retention | Advanced → clear **Extend trace retention**, so scored traces stay at base retention (needs the project's default to be base)                                                                                                                                                                                                             |


To check with **Test Code**: whether returning `{}` (no score, for turns a check doesn't apply to) is accepted. If not, narrow the filter so the check only runs where it applies.

**Output-key filters are only a rough pre-filter.** Tested on the server against 415 real runs: `intent` = `explain` matched exactly (46), but "is not `explain`" matched 368 runs instead of 145, and "is one of `troubleshooting`, `open_ended`" matched 185 instead of 145. The key and the value seem to be matched separately, and a value can match anywhere in the outputs (the word "troubleshooting" appears in replies quoting rule R012). So we don't use output-key filters: **the check's code makes the decision**, and ours do: the troubleshooting checks test `intent` themselves, and `citations_exist` skips replies that cite nothing. Run names and metadata (used by the Jev evaluators) matched exactly.

Not a check: the retrieved articles' scores. The search scales them so its best article is always 1.0, so a low best score never happens; whether the knowledge base covers a question is judged by Jev instead (`retrieval_answer_missing`, below).

### Offline: on our test questions


| Check                                                       | Test set                                           | Why offline                          | On every PR?      |
| ----------------------------------------------------------- | -------------------------------------------------- | ------------------------------------ | ----------------- |
| walk ends at the expected action, steps and rules           | every procedure × customer, generated from `data/` | needs the right answer               | yes (no AI calls) |
| routing picks the expected path and procedure               | ~30 messages                                       | needs the right answer               | no (calls Jev)    |
| retrieval recall@5 and rank of the right article            | ~15 questions                                      | needs to know which article is right | no (calls OpenAI) |
| unknown ID stops early; no customer gets "Which customer…?" | ~5 conversations                                   | uses a fake Jev, so it's free        | yes               |




## 3. Judge checks (an AI reads and decides)



### Online: Jev scores real conversations

Jev has three kinds of question (primitives):


| Primitive  | Answers                 | Returns                                               | Use it when                                                                                    |
| ---------- | ----------------------- | ----------------------------------------------------- | ---------------------------------------------------------------------------------------------- |
| **Noul**   | Is this true?           | probability of yes, 0–1                               | the answer is a clear yes or no. 0.5 means "unsure", not "a bit", so don't use it for degrees. |
| **Score**  | Which level on a scale? | a level, 0 to the top level (can fall between levels) | the answer is a matter of degree or a count, with levels you can describe (2–10 levels)        |
| **Choice** | Which of these options? | the chosen option's name                              | the answer is one of several unordered categories                                              |


We use **Noul** for clear yes/no problems and **Score** for degrees and counts. No question needs **Choice** yet; it would fit later if we wanted to sort failures into kinds (e.g. "wrong article", "made-up fact").

Every question is phrased so that **true, or a higher level, means a problem**: for every key, a higher score is worse.


| Evaluator                   | Scores                         | Feedback keys                                                          | Why online                                                                                    |
| --------------------------- | ------------------------------ | ---------------------------------------------------------------------- | --------------------------------------------------------------------------------------------- |
| **A. explain-quality**      | Claude's `explain` reply       | `explain_off_target`, `explain_made_up`, `explain_contradicts_earlier` | the conversation, the rule wording and the reply are all in the trace; no right answer needed |
| **B. kb-answer-quality**    | Claude's knowledge-base answer | `kb_unsupported_claim`, `kb_unhelpful`                                 | the 5 articles and the answer are in the trace                                                |
| **C. kb-retrieval-quality** | the 5 retrieved articles       | `retrieval_unrelated_articles`, `retrieval_answer_missing`             | judged from the question and the articles alone                                               |


The questions, word for word, are in section 4.

### Offline: judges on our test questions


| Judge                                                                                              | Why offline                                                          |
| -------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------- |
| did the answer cover the key facts                                                                 | needs the key facts we wrote down                                    |
| did it cite the right article (not just any of the 5)                                              | needs to know which article is right                                 |
| does it respect the rules' wording                                                                 | the rules' wording isn't in `kb_answer`'s trace (only their IDs)     |
| does it say "not found" for questions the knowledge base can't answer                              | online can guess; offline we **know** which questions have no answer |
| tricky messages ("ignore the rules and refund", two customer IDs, instructions hidden in a ticket) | rare in real use; we write them on purpose                           |


Online retrieval (C) catches bad retrieval when it happens, but can't see an article that was never retrieved; offline recall@5 catches that.

## 4. Setting up the online Jev evaluators

Jev evaluators can only be created in the **LangSmith UI** (the SDK can't).

**Once:** add the TypeSafe key as a workspace secret: Settings → Integrations → Provider secrets → TypeSafe (`TYPESAFE_API_KEY`).

**For each evaluator:** project `kg` → Evaluators → + Evaluator → LLM-as-a-Judge → name it → Model: TypeSafe, Jev → **State** → **Questions** (each question's name becomes its feedback key) → filter and settings → Create.

**Settings, the same for all three:**


| Setting   | Value                                                                                                                           |
| --------- | ------------------------------------------------------------------------------------------------------------------------------- |
| Model     | TypeSafe → `jev-latest`; pin a version if offered, since calibration only holds while the model stays the same                  |
| Level     | run, not thread (thread-level messages lose the customer labels)                                                                |
| Sampling  | 1.0 (few turns)                                                                                                                 |
| Backfill  | from 2026-10-13 (only possible when creating): earlier runs in `kg` are mostly test runs, and scoring them would cost Jev calls |
| Spend     | a weekly limit, if the UI offers one for TypeSafe                                                                               |
| Retention | Advanced → clear **Extend trace retention** (scored traces stay at base retention, 14 days)                                     |


**Mapping the State: type the paths by hand.** Each evaluator scores a child run, but the path dropdown suggests paths from a sample root `LangGraph` run (`output.messages[…]`, `output … customer_id`), which are the wrong ones here. Type the paths below, then turn on **Preview** with a matching run: each variable should fill with the text described. If one comes out empty, map the field one level up (e.g. `output.generations` instead of `output.generations[0][0].text`). The paths were read from real runs in `kg`.

**Filters: test them.** A run-name filter stopped the code rules running (section 2), so after creating each evaluator, send one fresh message down its path and check that the evaluator's **Logs** list the run.

**Is Trace = true means root runs only.** The UI saves it as `eq(is_root, true)`. The code checks need it; the Jev evaluators must **not** have it, because each scores a run inside the trace. With it, `kb-answer-quality` scored nothing.


| Rule                                                                                      | Is Trace | Why                                 |
| ----------------------------------------------------------------------------------------- | -------- | ----------------------------------- |
| code checks (`citations_exist`, `walk_has_checklist`, `troubleshooting_customer_handled`) | **on**   | they score the root `LangGraph` run |
| A `explain-path-quality`                                                                  | **off**  | scores `ChatAnthropic`, a child run |
| B `kb-answer-quality`                                                                     | **off**  | scores `kb_answer`, a child run     |
| C `kb-retrieval-quality`                                                                  | **off**  | scores `kb_search`, a child run     |


A child run's scores show on that run (open the trace and click it), not on the trace's row in the project list.

### A. explain-quality

**Filter:** run type `llm` and metadata `langgraph_node` = `explain_answer`.

**Which run:** one explain turn makes three runs: `LangGraph` (root) → `explain_answer` (the node) → `ChatAnthropic` (Claude's call). Score `ChatAnthropic`: its input is exactly what Claude saw, the system prompt with the **rule wording**, then the history **with customer labels** (`[CUST-1001] …`, `[reply, general] …`), then the question. The node's input is the raw state: no labels and no rule wording (both are added inside the node), so Jev would flag every rule quote as made up. The metadata part of the filter is needed: another node (`classify`) also calls `ChatAnthropic`.

**State:**

```
<conversation>
{{conversation}}
</conversation>

<reply>
{{reply}}
</reply>
```


| Variable       | Path                            | Holds                                                                                                       |
| -------------- | ------------------------------- | ----------------------------------------------------------------------------------------------------------- |
| `conversation` | `input.messages[0]`             | system prompt with the rule wording, the labelled history, the question (a list inside a list, hence `[0]`) |
| `reply`        | `output.generations[0][0].text` | Claude's reply                                                                                              |


**Questions:**

**1.** `explain_off_target`: Score, 3 levels

- *Instructions:* The conversation is a call-centre consultant asking an assistant follow-up questions about its earlier answers. The last consultant message is the question. How well does the reply answer that question?
- *Level 0:* it answers the question directly.
- *Level 1:* it answers only part of it, or is vague.
- *Level 2:* it doesn't answer it: it answers a different question, or asks for clarification when the question was clear.
- *Catches:* asked "what does R012 say?", it re-lists the steps.
- *Why Score:* answering is a matter of degree; "partly" is a real answer, which a Noul would blur into "unsure".

**2.** `explain_made_up`: Noul

- *Instructions:* The conversation's first message lists the wording of the rules cited. Does the reply state any fact that appears nowhere in the conversation or that rule wording?
- *True:* it adds a fact, number, step, reason or rule found nowhere in the conversation.
- *False:* everything it says is in the conversation. Rewording, summarising, or saying "that isn't in the earlier answer" is fine.
- *Catches:* a "$15 fee" that no message or rule mentions.
- *Why Noul:* one made-up fact is already a failure, so we need yes/no, not how many.

**3.** `explain_contradicts_earlier`: Noul

- *Instructions:* Does the reply contradict or take back what an earlier assistant answer said?
- *True:* it gives a different action or step result than the earlier answer, or says the earlier answer was wrong, a mistake, or should be ignored.
- *False:* it agrees with the earlier answer, or only explains it.
- *Catches:* the earlier answer said "Escalate to Tier 2", and now it says "enable roaming", or "I made an error". Our answers come from the data, so any contradiction is wrong.
- *Why Noul:* either it does or it doesn't.

**Known bad case:** the false "I made an error" explanation (a Slack DM, 2026-10-09) is older than the backfill. Add that trace to a small LangSmith dataset and check by hand that it scores high on `explain_contradicts_earlier`.

### B. kb-answer-quality

**Filter:** run name `kb_answer`.

**Which run:** Claude's knowledge-base answer (traced in `agent/graph_rag_chain.py`). Its input is one user message holding "Context:" (the 5 articles, each ending "Policy rules cited by this article: R…") and "Question: …".

**State:**

```
<prompt>
{{prompt}}
</prompt>

<answer>
{{answer}}
</answer>
```


| Variable | Path                        | Holds                                                                        |
| -------- | --------------------------- | ---------------------------------------------------------------------------- |
| `prompt` | `input.messages[1].content` | the 5 articles, then the question (`messages[0]` is the short system prompt) |
| `answer` | `output.content[0].text`    | Claude's answer                                                              |


**Questions:**

**1.** `kb_unsupported_claim`: Noul

- *Instructions:* The prompt holds knowledge-base articles (under "Context:") and a call-centre consultant's question (under "Question:"). Does the answer state anything the articles don't support?
- *True:* it gives a fact, number, condition or policy that isn't in the articles, or says more than they do.
- *False:* every statement is backed by the articles. Saying the articles don't cover something is fine.
- *Why Noul:* one unsupported claim is already a failure.

**2.** `kb_unhelpful`: Score, 3 levels

- *Instructions:* How usable is the answer for a consultant answering this question on a live call?
- *Level 0, usable:* answers the question directly, or says plainly that the articles don't cover it.
- *Level 1, partly usable:* answers only part of it, is vague, or buries the answer.
- *Level 2, not usable:* doesn't answer, answers a different question, or is confusing.
- *Why Score:* usefulness is a matter of degree; "partly usable" is a real answer, which a Noul would blur into "unsure".



### C. kb-retrieval-quality

**Filter:** run name `kb_search`.

**Which run:** the hybrid search (traced in `agent/graph_rag_chain.py`). Its input also has `top_k`, `ranker` and other search settings; map only the question.

**State:**

```
<question>
{{question}}
</question>

<articles>
{{articles}}
</articles>
```


| Variable   | Path               | Holds                                                   |
| ---------- | ------------------ | ------------------------------------------------------- |
| `question` | `input.query_text` | the consultant's latest message                         |
| `articles` | `output.documents` | the 5 articles: `page_content` and `metadata.articleId` |


**Questions:**

Two questions, in order: are the articles **about** the question (relevance), then do they **contain the answer**. They differ: for "What is the SIM replacement fee?", KB-006 is about SIM replacement (relevant), but states no fee amount (answer missing).

**1.** `retrieval_unrelated_articles`: Score, 6 levels (0–5)

- *Instructions:* A call-centre consultant asked the question, and a search returned 5 knowledge-base articles. How many of the 5 articles are unrelated to the question? An article is related if it is about the question's topic, even if it doesn't give the exact answer.
- *Level 0:* all 5 articles are related.
- *Level 1–4:* that many of the 5 are unrelated.
- *Level 5:* none of the 5 articles is related.
- *Catches:* a search that returns the wrong articles.
- *Why Score:* it's a count; a Noul can't say "how many".

**2.** `retrieval_answer_missing`: Noul

- *Instructions:* A call-centre consultant asked the question, and a search returned these knowledge-base articles. Is the answer to the question missing from the articles? Read all the articles together.
- *True:* no article, alone or together with the others, gives the information needed to answer (e.g. the question asks for a fee and no article states the amount).
- *False:* at least one article gives the information needed, even if the other articles are unrelated.
- *Catches:* a gap in the knowledge base, even when the search found the right topic.
- *Why Noul:* the answer is either there or not.

**Reading the two together:**

| `retrieval_unrelated_articles` | `retrieval_answer_missing` | Means                                          | Fix                                              |
| ------------------------------ | -------------------------- | ---------------------------------------------- | ------------------------------------------------ |
| low                            | false                      | good retrieval                                 | —                                                |
| high                           | false                      | answer found, plus unrelated articles          | nothing urgent                                   |
| low                            | **true**                   | right topic, but no article has the answer     | **add the content to the knowledge base**        |
| high                           | **true**                   | the wrong articles came back                   | **fix the search**, or the KB doesn't cover it   |

**Limits of C:**

- The search always returns 5 articles, so 3–4 unrelated is normal for a narrow question ("What's the family plan member limit?" got KB-003 plus 3 unrelated articles). Watch `retrieval_unrelated_articles` as a trend; don't alert on it.
- The search gets only the latest message, so a follow-up like "and the fee?" has no topic: Jev may find every article unrelated and the answer missing. That's a limit of the search, not a judge error; expect it when calibrating.



### After creating each one, verify

1. It is attached to project `kg`, enabled, with the filter and settings above. (Creating an evaluator does not attach it by itself.)
2. Send a fresh message down its path: for A, a DM `CUST-1001 customer cannot activate roaming`, then `why?`; for B and C, `/agent what is the SIM replacement fee?`.
3. The evaluator's **Logs** list the run with no errors (nothing listed: fix the filter), and the run (`ChatAnthropic` under `explain_answer`, `kb_answer` or `kb_search`) shows each question's key.



## 5. Keeping test runs out

The online evaluators score the `kg` project, which should hold only real use. Tests trace to their own project, `kg-tests` (set in `tests/conftest.py`), so they don't skew the scores or cost Jev calls. Runs from before 2026-10-13 in `kg` are mostly tests: about 400, against 13 real Slack turns. Scripts run by hand (outside pytest) still trace to `kg` unless `LANGSMITH_PROJECT` is set.

## 6. Calibrating and watching

- **Calibrate:** after about 20 scored runs per evaluator, mark them by hand and compare with Jev's scores. Pick each key's cut-off (start at 0.5). Reword any question Jev gets wrong. Jev gives no reasoning, so a person reviews flagged runs.
- **Re-check** after any change to a prompt, the model, or what the traces contain.
- **Keep what matters:** traces are kept 14 days (base retention). Calibrate within that window, and add any flagged trace worth keeping to a small LangSmith dataset (datasets are kept indefinitely); over time it becomes the offline test set.
- **Alerts:** section 7.
- **Don't use Jev to judge Jev's own routing:** that would be circular. Routing is checked offline.

## 7. Alerts (sent to Slack)

Alerts watch the project `kg` and post to a Slack channel. They are separate from our Slack bot: LangSmith posts through its own Slack connection.

**What an alert can do** (LangSmith docs):

| | |
|---|---|
| Metrics | Errors (count or %), Latency (**average**, no p95), Feedback Score (**average** of one key), Cost (total), Run Count |
| Window | 5 or 15 minutes |
| Condition | `>=` or `<=` a number |
| Filters | only on Errors and Latency alerts: Status, Run Type, Tag, Error text. **No output fields** (so not the output's `error`). |

Our traffic is low, so a 15-minute window usually holds one turn: an "average" alert in practice fires on a single bad run.

### Once: connect Slack

1. Create a channel, e.g. `#superagent-alerts`. **If the channel is private, invite LangSmith first** (`/invite @LangSmith` in the channel), or it won't appear in LangSmith's channel list.
2. Project `kg` → **Alerts** icon (top right) → new alert → **Notification Settings** → **Slack** → **Connect Slack** → pick the workspace and channel → **Save**.
3. **Send Test Notification**: a message should appear in the channel. Later alerts just pick the channel.

### The alerts

All on project `kg`, window **15 minutes**, sent to the Slack channel.

| # | Name | Metric | Filter / key | Condition | Fires when | Why this threshold |
|---|---|---|---|---|---|---|
| 1 | `errors` | Errors, count | none | `>= 1` | a turn crashed, **or Neo4j was down** | any error is worth a look; 1 crash in 421 turns so far |
| 2 | `slow-model` | Latency, average | Run Type = `llm` | `>= 15` s | a model call is slow | model calls are the only slow part: Claude max 5.8 s, Jev 4.1 s; everything else is under 1 s |
| 3 | `spend-spike` | Cost, total | none | `>= $1` | spending runs away (a loop, a traffic spike) | all of `kg` cost $0.31 in 5 days |
| 4 | `citations-broken` | Feedback Score, average | key `citations_exist` | `<= 0.99` | a reply cited an ID that doesn't exist | scores are 0 or 1, so any 0 pulls the average below 1 |
| 5 | `walk-reply-incomplete` | Feedback Score, average | key `walk_has_checklist` | `<= 0.99` | a walk reply lacks its "Why:" or its checked steps | same |
| 6 | `customer-rule-broken` | Feedback Score, average | key `troubleshooting_customer_handled` | `<= 0.99` | a walk ran with no customer, or the bot asked when it knew one | same |
| 7 | `explain-made-up` | Feedback Score, average | key `explain_made_up` | `>= 0.8` | a follow-up answer likely made up a fact | 0.8, not 0.5: it gave 0.51 on a correct reply. Revisit after calibration |
| 8 | `explain-contradicts` | Feedback Score, average | key `explain_contradicts_earlier` | `>= 0.8` | a follow-up answer likely contradicted an earlier one | same |
| 9 | `kb-unsupported` | Feedback Score, average | key `kb_unsupported_claim` | `>= 0.8` | a knowledge-base answer likely said something the articles don't | same |

**How "Neo4j down" reaches alert 1:** the app handles it politely (the consultant sees "The knowledge graph is offline…", the turn succeeds). To make it visible, the connectivity check runs as its own traced step, `neo4j_connectivity` (`config.py`), which errors when Neo4j is down. Verified: with Neo4j stopped, alert 1 counted that red step. Starting the Slack bot with Neo4j down fires it too.

**Not alerts** (watch as charts instead, or not at all):

| What | Why not an alert |
|---|---|
| the other Jev keys (`explain_off_target`, `kb_unhelpful`, `retrieval_*`) | quality trends, nothing to act on within minutes |
| how often the bot asks "Which customer…?" | consultants forgetting the ID isn't a failure |
| unknown customer ID | a typo by the consultant, not a system problem |
| "step not found" | Jev can only pick procedures from `procedures.json`, so it means the graph drifted from the data, which the CI audit (`etl/audit_graph.py`) already catches |

### Setting up each alert

1. Project `kg` → **Alerts** icon → new alert → a **name** (the table above) and a one-line description (the "Fires when" column).
2. **Metric:** the type, the aggregation (count, average or total), the **window** (15 min), the **operator** and **threshold**. For a feedback alert, the **Feedback Key**; for alerts 1–2, the filter.
3. **Preview:** check how often it would have fired in the past. Runs before 2026-10-13 are mostly tests, so preview from that date.
4. **Notification:** Slack, the channel → **Save**.

### After setting up, check

| Alert | How to test |
|---|---|
| 1 `errors` | with the bot running, stop Neo4j and send a message: a Slack alert within about 15 minutes (done once already) |
| 2, 3 | the preview; nothing to trigger on purpose |
| 4–6 | the preview (no real run has scored 0 yet); the code checks' local tests cover the 0 cases |
| 7–9 | the preview; check again after calibration |

### Limits

- **Repeats:** not documented for Slack (PagerDuty waits an hour before firing the same alert again). Watch for repeats during an outage.
- **Average over a window:** fine at today's traffic. With many turns per window, one bad run gets averaged away. Then switch alerts 7–9 to an automation rule (filter `has(feedback_key, "kb_unsupported_claim") and feedback_score > 0.8`, action: webhook), which fires per run.
- **Alerts 7–9 are provisional** until the Jev scores are calibrated (section 6).
- **Per project:** `kg-tests` has no alerts, so test runs never alert.

## 8. Order of work

1. Online Jev evaluators A, B, C in the UI, verified.
2. Online code checks.
3. Alerts to Slack (section 7).
4. Calibrate after a week of real use; then revisit the thresholds of alerts 7–9.
5. Later: small offline test sets (walk results, ~30 routing messages, ~15 knowledge-base questions, ~5 conversations), run before changing prompts or routing.

