# Evaluation

The agent is scored in two ways:

|  | Online (`online/`) | Offline (`offline/`) |
| --- | --- | --- |
| What it scores | Real conversations, as they happen | Golden examples with known, reviewed answers |
| When | Automatically, in LangSmith | Before a change ships: you run `run.py` |
| How | Code checks + Jev judges, with no answer key | Each reply compared with its golden answer |
| Result | Scores on each trace, alerts to Slack | A LangSmith experiment, a pass/fail gate |

## Layout

```
eval/
├── online/
│   ├── citations_exist.py                    online code checks: paste each file into LangSmith
│   ├── walk_has_checklist.py
│   ├── troubleshooting_customer_handled.py
│   └── utils/
│       ├── dry_run.py                        try the checks on real traces, locally
│       └── build_valid_ids.py                refresh citations_exist.py's ID list after data/ changes
└── offline/
    ├── datasets/walks.jsonl                  golden examples (reviewed, in git)
    ├── build_walks.py                        writes walks.jsonl
    ├── oracle.py                             works out a walk's correct answer from data/
    ├── target.py                             runs one example through the real agent
    ├── evaluators/code.py                    exact checks against the golden answer
    ├── run.py                                runs a suite as a LangSmith experiment
    └── thresholds.yaml                       minimum passes per check, for --gate
```

## Online

- **Code checks (3):** in LangSmith, open the `kg` project → Rules → add an online code evaluator and paste the file. Filter: Is Trace = true, not errored.
- **Jev judges (7):** set up in the LangSmith UI as decision-model evaluators, so there are no files for them.
- **Alerts:** LangSmith alerts post to Slack. A private channel must invite the LangSmith app first.
- What each check and judge scores: the main README's Evaluation section.

```bash
uv run python eval/online/utils/dry_run.py          # what each code check would score on recent traces
uv run python eval/online/utils/build_valid_ids.py  # after changing data/, then paste citations_exist.py again
```

## Offline

```bash
uv run python -m eval.offline.run --suite walks --tag baseline   # one suite, experiment "walks-baseline-…"
uv run python -m eval.offline.run --suite all --gate             # exits 1 if a check is below thresholds.yaml
uv run pytest tests/eval                                         # no keys: oracle, answer key, checks, gate
```

`run.py` needs what the agent needs (Neo4j with the graph and articles loaded, and the model keys) and makes real model calls.

### How a run works (the walks suite)

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

- **Results:** the table (pass counts, failing examples, latency) is also saved to `results/<suite>-<tag>.json`, which isn't in git.
- **Gate:** `--gate` fails if a check is below its threshold in `thresholds.yaml`.
- **Crashes:** a run that crashed (e.g. Jev returning 529 Overloaded) is listed as errored, not counted as a wrong answer, and the gate asks you to rerun.
- **Comparing versions:** open both experiments in LangSmith (Datasets & Experiments → the dataset → Compare).

### Walks suite (20 examples)

One troubleshooting walk per procedure. Each walk has a consultant message and a customer, chosen so the message fits that customer's CRM record. The 20 customers are all different, and the walks end in all three ways: step 1 finds the cause, a later step does, or every step says NO.

**The 6 checks:**

| Check | Question |
| --- | --- |
| `path_correct` | Did Jev choose "troubleshooting"? |
| `procedure_correct` | Did Jev choose the right procedure? |
| `action_correct` | Did the walk end at the right action? |
| `rules_correct` | Did it cite exactly the right rules? |
| `steps_correct` | Did it check the right steps, in order, with the right yes/no? |
| `has_checklist` | Does the reply show "Why:" and the steps it checked? |

**Where the right answers come from:** `oracle.py` works out the right answers, and `build_walks.py` writes them into `walks.jsonl`. When an eval runs, the oracle isn't used: the agent's reply is compared with the answers already saved in the file.

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

**Why an oracle:**

- **By hand is slow and error-prone:** 20 walks, up to 4 steps each, and each step means looking up the CRM record (some lookups take two hops, e.g. postcode → outage list).
- **Copying the agent's replies isn't an answer key:** any bug would be saved as the right answer, and the test would always pass.
- **The oracle follows `procedures.json` itself:** it's separate code, so it won't share the agent's mistakes, and it lets CI catch answers that went stale after `data/` changed.

For the 3 procedures that read a ticket, the oracle can't judge the ticket's text, so a human label gives that step's answer.

**To change a walk:** edit `WALKS` in `build_walks.py`, run `uv run python -m eval.offline.build_walks`, and review the diff of `walks.jsonl`.

### Coming next

Routing (20), knowledge base (15, scored by Jev judges), conversations (6), guards (4) and adversarial (6).
