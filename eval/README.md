# Evaluation


|        | Online (`online/`)                      | Offline (`offline/`)                      |
| ------ | --------------------------------------- | ----------------------------------------- |
| Scores | real conversations, live                | golden examples with known answers        |
| When   | automatically, in LangSmith             | before a change ships: you run `run.py`   |
| How    | code checks + Jev judges, no answer key | each reply compared with its right answer |
| Result | scores on traces, alerts to Slack       | a LangSmith experiment + a pass/fail gate |


## Layout

```
eval/
├── online/
│   ├── citations_exist.py                    online code checks: paste each into LangSmith
│   ├── walk_has_checklist.py
│   ├── troubleshooting_customer_handled.py
│   └── utils/
│       ├── dry_run.py                        try the checks on real traces, locally
│       └── build_valid_ids.py                refresh citations_exist.py's IDs after data/ changes
└── offline/
    ├── datasets/*.jsonl                      golden examples, one file per suite
    ├── build_walks.py, build_routing.py,     write those files
    │   build_kb.py
    ├── oracle.py                             works out a walk's right answer from data/
    ├── target.py                             runs one example through the real agent
    ├── evaluators/code.py                    the code checks for each suite
    ├── evaluators/jev.py                     the Jev judges, for answers code can't check
    ├── run.py                                runs a suite as a LangSmith experiment
    └── thresholds.yaml                       minimum passes per check, for --gate
```



## Online

- **Code checks (3):** LangSmith → `kg` project → Rules → add a code evaluator, paste the file. Filter: Is Trace = true, not errored.
- **Jev judges (7):** set up in the LangSmith UI, so no files here.
- **Alerts:** posted to Slack. A private channel must invite the LangSmith app.

```bash
uv run python eval/online/utils/dry_run.py          # what each check would score on recent traces
uv run python eval/online/utils/build_valid_ids.py  # after changing data/, then paste citations_exist.py again
```



## Offline

```bash
uv run python -m eval.offline.run --suite walks --tag baseline   # one suite → experiment "walks-baseline-…"
uv run python -m eval.offline.run --suite all --gate             # every suite; exit 1 if below thresholds.yaml
uv run pytest tests/eval                                         # no keys needed
```

Needs Neo4j (graph and articles loaded) and the model keys, like the agent. Makes real model calls.

### How a suite is chosen

```
                         run.py --suite <name>
                                  │
                                  │ looks up <name> in 3 files
       ┌──────────────────────────┼──────────────────────────┐
       ▼                          ▼                          ▼
 datasets/<name>.jsonl     evaluators/code.py         thresholds.yaml
 the examples +            SUITES["<name>"]           "<name>:" section
 right answers             + evaluators/jev.py        minimum passes
                           JUDGES["<name>"]
                           which checks to run
       │                          │                          │
       │ run.py uploads it        │                          │
       ▼                          │                          │
 LangSmith dataset                │                          │
       │                          │                          │
       └────────────┐             │                          │
                    ▼             │                          │
               target.py          │                          │
         runs each example        │                          │
         through the real         │                          │
         agent graph              │                          │
         (same for every suite)   │                          │
                    │             │                          │
                    ▼             ▼                          │
          reply + state ──► checks compare it                │
                            with the right answer            │
                                  │                          │
                                  ▼                          ▼
                     LangSmith experiment + table ──► gate pass / fail
```

- Only `run.py` knows the suite name. `target.py` just runs the graph.
- A new suite = a JSONL file + a `SUITES` entry (and `JUDGES`, if Jev judges it) + a `thresholds.yaml` section.



### How a run works (walks example)

```
 walks.jsonl  (20 golden examples, in git)
     │   each one: a consultant message + a customer ID + the right answer
     │
     │  run.py uploads it to the LangSmith dataset "super-agent-offline-walks"
     ▼
 target.py  sends the message to the REAL agent (a new conversation each time)
     │
     ▼
 ┌───────────────────────── the agent ─────────────────────────┐
 │  classify_intent   Jev picks the path: troubleshooting /    │
 │                    open_ended / explain                     │
 │  match_procedure   Jev picks 1 of the 20 procedures         │
 │  walk the steps    mock CRM answers each step's question    │
 │                    (Claude reads the ticket, if a step      │
 │                    needs one)                               │
 │  reply             action + rules + "Checked: 1. … no"      │
 └─────────────────────────────────────────────────────────────┘
     │
     ▼
 code checks  compare the reply with the right answer (exact match, no AI)
     │
     ▼
 LangSmith experiment  +  a table in the terminal  +  gate (pass/fail)
```

**The table shows** pass counts, each miss ("expected … got …"), passes per slice, and latency. It's also saved to `results/<suite>-<tag>.json` (not in git).

**To compare two versions:** LangSmith → Datasets & Experiments → the dataset → Compare.

### Walks suite (20)

One walk per procedure. Each message fits its customer's CRM record, and all 20 customers are different.


| Check               | Question                                                       |
| ------------------- | -------------------------------------------------------------- |
| `path_correct`      | Did Jev choose "troubleshooting"?                              |
| `procedure_correct` | Did Jev choose the right procedure?                            |
| `action_correct`    | Did the walk end at the right action?                          |
| `rules_correct`     | Did it cite exactly the right rules?                           |
| `steps_correct`     | Did it check the right steps, in order, with the right yes/no? |
| `has_checklist`     | Does the reply show "Why:" and the steps it checked?           |


**The right answers come from** `oracle.py`**:**

```
 building the suite (only when the walks change)
   WALKS: customer + message  ──►  oracle.py  ──►  walks.jsonl  (answers written in, in git)

 every eval run
   walks.jsonl ──► real agent ──► reply ──► compare with the answers in walks.jsonl
                                            (oracle not used)

 CI tests (no keys)
   oracle.py works the answers out again ──► still the same as walks.jsonl?
                                             no → data/ changed → rebuild the suite
```

Why an oracle:

- By hand: slow and easy to get wrong (20 walks, up to 4 steps, CRM lookups each).
- Copying the agent's replies: its bugs would become the "right" answers.
- The oracle is separate code, so it won't share the agent's mistakes, and CI can re-check it.

Ticket steps can't be computed, so they use a human label.

**To change a walk:** edit `WALKS` in `build_walks.py`, then run `uv run python -m eval.offline.build_walks`.

### Routing suite (24)

Does each message reach the right path and procedure? 

Checks: `path_correct`, `procedure_correct`.


| Slice                   | #   | Example                                                              |
| ----------------------- | --- | -------------------------------------------------------------------- |
| `procedure-pair`        | 14  | "can't switch roaming on" (PROC-001) vs "abroad, no bars" (PROC-014) |
| `policy-twin`           | 4   | "how much does a replacement SIM cost?" → knowledge base             |
| `general-with-customer` | 1   | a general question that names a customer → knowledge base            |
| `near-miss`             | 1   | "can't log in … hasn't tried resetting the password" → PROC-018      |
| `typo`                  | 2   | "customers auto paymnt keeps gettin declined" → PROC-008             |
| `no-procedure`          | 1   | "voicemail stopped working" → knowledge base                         |
| `off-topic`             | 1   | "help me write an email to my team lead" → knowledge base            |


**Baseline:** 24/24 on both checks, the same in 3 repeated runs. The near-miss used to go to PROC-020, which was fixed by rewording the two procedures' `when_to_use`.

**To change an example:** edit `ROUTES` in `build_routing.py`, then run `uv run python -m eval.offline.build_routing`.

### Knowledge-base suite (15)

General questions answered from the articles: 12 the articles answer (all 9 categories), 3 they don't (e.g. "the exact SIM fee amount": the article mentions the fee, not the amount). Each answerable question has its article and 2–3 key facts taken from the article's text.


| Check              | Type | Question                                                    |
| ------------------ | ---- | ----------------------------------------------------------- |
| `path_correct`     | code | Did it go to the knowledge base?                            |
| `recall_at_5`      | code | Was the right article among the 5 the search returned?      |
| `mrr`              | code | How high? 1 if first, 0.5 if second… (mean = MRR)           |
| `facts_covered`    | Jev  | Share of the key facts the answer states                    |
| `no_contradiction` | Jev  | Does it agree with the reference answer?                    |
| `supported`        | Jev  | Is everything it says in the articles?                      |
| `usable`           | Jev  | Could a consultant use it on a live call?                   |
| `admits_gap`       | Jev  | Unanswerable only: does it say the articles don't cover it? |


**How a question is scored:**

```
 question ──► real agent ──► search finds 5 articles ──► Claude writes the answer
                                     │                           │
                     code checks ◄───┘                           └───► ONE Jev call
                     recall_at_5: right article in the 5?             Jev reads: question, reference,
                     mrr: how high? (1st = 1, 2nd = 0.5)               answer, the 5 articles as Claude
                                                                       saw them
                                                                       → facts_covered, no_contradiction,
                                                                         supported, usable, admits_gap
```

Jev gives a probability of "yes" for each question, and 0.5 or more counts as yes. It must see the articles **exactly as Claude saw them**, including "Policy rules cited by this article". Without that line, an answer naming a rule looked made up. Each score's comment keeps Jev's raw numbers. The wording of `supported` and `usable` matches the live LangSmith judges.

**Baseline (3 runs):** recall 36/36, MRR 0.96, facts 0.94, supported 45/45, no contradictions, unanswerable admitted 9/9.

What the misses mean:

- **MRR 0.96, not 1:** for the fee question, the search ranks the cancellation article first and the fee article (which has the answer) second, every time. It's found and the answer is right, just not at the top: a small, real ranking weakness.
- **Facts 0.94, not 1:** in 4 of 36 answers, Jev said a key fact was missing. Reading them, it really was, e.g. a suspension answer never said bank transfers take 1–3 days. So Claude left a detail out; the judge was right.
- **2 close calls:** for 2 of those facts, Jev's "stated" probability was 0.39 and 0.45. It was right, but unsure. Step 5 (calibration) checks whether 0.5 is the right line.

### How Jev performed as a judge

`tests/e2e/judges/` tests **the judge, not the agent**: it gives Jev answers whose verdict we already know, and checks that Jev scores them right. Run it after changing a judge's wording.

| Answer given to Jev | Jev's numbers | Right? |
| --- | --- | --- |
| the agent's real answers (3 runs) | facts stated: median 0.99 (the low ones were real misses), contradicts 0.02–0.12, unsupported 0.07–0.37, admits the gap 0.98–0.99 | ✅ pass |
| wrong facts ("the first two replacements are free") | facts stated 0.02–0.04, contradicts 0.99, unsupported 0.99 | ✅ caught |
| vague ("check the policy for details") | facts stated 0.02–0.04, but contradicts 0.04, unsupported 0.11 | ✅ "not usable, but not wrong" |
| made up for an unanswerable question ("the fee is $25") | admits the gap 0.01, unsupported 0.98–0.99 | ✅ caught |

What this means: Jev clearly tells good answers from bad ones, with no borderline scores on these cases, and it doesn't confuse "vague" with "wrong". So a high knowledge-base score reflects the answers, not a judge that says yes to everything.

**To change a question:** edit `ANSWERABLE` / `UNANSWERABLE` in `build_kb.py`, then run `uv run python -m eval.offline.build_kb`.

### Coming next

Conversations (6), guards (4), adversarial (6).

## When Jev fails

TypeSafe's API is in early access and sometimes unstable. We saw:

- **529 Overloaded** on 7 of 20 messages in one run, because its servers were busy for everyone;
- **connection failures** in 2 of 72 runs, where the handshake was dropped or never answered, before Jev saw the question.

So the agent (`_JEV_RETRY_ON` in `agent/supervisor.py`) tries each Jev call up to **3 times**, about 0.5 s then 1 s apart, with a **10 s timeout** per try. The error classes come from the `langchain-typesafe` package.


| Error (`langchain_typesafe.client`)                           | When                               | Retried? |
| ------------------------------------------------------------- | ---------------------------------- | -------- |
| `TypeSafeInternalServerError`                                 | 5xx, incl. 529 Overloaded          | ✅        |
| `TypeSafeRateLimitError`                                      | 429, too many requests             | ✅        |
| `TypeSafeAPIConnectionError`                                  | no answer, e.g. connection dropped | ✅        |
| `TypeSafeAPITimeoutError`                                     | no answer within 10 s              | ✅        |
| `TypeSafeAuthenticationError`                                 | 401, wrong key                     | ❌        |
| `TypeSafeBadRequestError`, `TypeSafeUnprocessableEntityError` | 400, 422, bad request              | ❌        |
| `TypeSafePermissionDeniedError`, `TypeSafeNotFoundError`      | 403, 404                           | ❌        |


❌ means it would fail the same way every time, so it fails at once. If a run still fails after 3 tries, the eval lists it as **errored**, not wrong, and the gate asks for a rerun.