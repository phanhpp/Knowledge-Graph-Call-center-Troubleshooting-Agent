# Super Agent (Vodafone Clone) - Knowledge-Graph Copilot

## Overview

Super Agent is a clone of Vodafone’s enterprise copilot for call-center consultants. When a consultant types a customer's problem, the agent injects live customer context, identifies intent, and either walks a deterministic procedural flowchart or generates a grounded answer via a GraphRAG pipeline over a Neo4j knowledge graph.  

**Key Features**:

- CRM State Injection: Injects customer records (`crm_context`) directly into state before execution.
- Intent Classification & Matching: Uses custom classifiers (*Jev*) for request intent routing and matching queries against 20 operational procedures.
- Slack Frontend: Provides real-time interactive communication via Slack WebSocket (Socket Mode).
- GraphRAG Engine with Hybrid Search & Graph Grounding: Performs dense vector and sparse keyword search over policy articles, then traverses Neo4j graph relationships to attach the governing rules (`(Article)-[:CITES_RULE]->(Rule)`).



## The design

**Build** (run once, and again whenever `data/` changes):

```
  data/*.json   synthetic data, the source of truth
      |
      +--> etl/validate.py       checks IDs, links and cycles (needs no Neo4j)
      |
      +--> etl/load_to_neo4j.py  writes the Step, Condition, Action, API and Rule nodes and their links
      |
      +--> etl/load_articles.py  writes Article nodes with embeddings (OpenAI), linked to Rules

  etl/audit_graph.py checks that Neo4j still matches the JSON. GitHub Actions runs validate, load and
  audit on every pull request that touches data/, etl/ or mock_crm/.
```

**Answer** (every request). "Claude", "OpenAI" and "Jev" (TypeSafe's classifier) mark the model calls:

```
  consultant: message (+ customer ID, if it names one)
                      |
                      v
check_customer: only if the message names a customer ID,
  check it exists (mock_crm/crm_store.py); unknown: reply
  "I couldn't find a customer..." and stop
                      |
                      v
check_neo4j: is Neo4j reachable?
  no: reply "The knowledge graph is offline..." and stop
                      |
                      v
classify_intent (*MODEL: Jev*): what does consultant want done?
                      |
        +-------------+-------------+--------------------------------+
        |                           |                                |
 troubleshooting                open_ended                        explain
        |                           |                                |
        v                           |                                v
load_crm: customer's CRM record     |                              explain_answer
  (troubleshooting only); no        |                              (*MODEL: Claude*):
  customer yet: ask which one       |                              last 10 messages,
        |                           |                              each labelled with
        v                           v                              its customer or as
match_procedure (*MODEL: Jev*)      |                              general, + wording
  which of 20 procedures;           |                              of the rules cited
  "none" -> knowledge base -------->+                                |
        |                           |                                |
        v                           v                                |
procedural_executor             graph_rag_chain                      |
-------------------             ---------------                      |
start at step 1                 embed question (*MODEL: OpenAI*)     |
        |                           |                                |
        v                           v                                |
fetch_step                      search Article nodes in Neo4j        |
  Neo4j: step details             vector & fulltext search (top 5)   |
  conditions.json: condition        |                                |
        |                           v                                |
        v                       follow CITES_RULE to linked Rules    |
check the condition                 |                                |
  field/chain -> mock_crm           v                                |
  ticket text -> *MODEL: Claude*  *MODEL: Claude* writes answer      |
        |                           |                                |
        +-- YES -------> resolve    |                                |
        +-- NO (more) -> next step -+                                |
        |                (loop back)|                                |
        +-- NO (last) -> resolve    |                                |
                             |      |                                |
                             v      |                                |
                          resolve   |                                |
                             |      |                                |
                             +<-----+<-------------------------------+
                             |
                             v
answer + citations: action and rules, or articles and rules
  (an explanation cites none)

```

Example: customer `CUST-1001` asks "Customer cannot activate roaming". The answer is NO at steps 1, 2 and 3, so the agent replies with the action, why, and each step checked, citing `["ACT-015", "R012"]`:

```
Escalate to Tier 2 - unresolved by standard checks

Why: none of the 3 checks found the cause, so this is the procedure's fallback action.
Checked (PROC-001 "Customer cannot activate roaming"):
1. Roaming is disabled on the account: no (roaming_flag = True)
2. Customer's plan is inactive: no (plan_status = active)
3. Device does not support roaming bands in the destination country: no (device.device_roaming_compatible = True)
```



## What's in the citations

Every reply carries `citations`: the IDs of what the answer came from. They depend on which path answered:


| Reply comes from                                                                          | Citations                                                                                              | Example                               | Who produces them                                                                                      |
| ----------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------ | ------------------------------------- | ------------------------------------------------------------------------------------------------------ |
| **Troubleshooting walk** (`resolve`)                                                      | the action given, then the rules governing it                                                          | `ACT-015, R012`; `ACT-027` (no rules) | code: the action ID from the step's `HAS_ACTION` link, the rule IDs via `GOVERNED_BY`, read from Neo4j |
| **Knowledge base** (`graph_rag_chain`)                                                    | each retrieved article, followed by the rules it cites (a rule cited by several articles appears once) | `KB-006, R004, KB-005, R005, R013, …` | code: from the search results, read from Neo4j                                                         |
| **Explain**                                                                               | nothing                                                                                                | —                                     | an explanation is not a new source; the answer it explains already showed its sources                  |
| **Fixed messages** (unknown customer, Neo4j down, "Which customer…?", no procedure found) | nothing                                                                                                | —                                     | —                                                                                                      |


## GraphRAG Pipeline Architecture

```markdown
               Consultant Input: "What is the SIM replacement fee?"
                                        |
                                        v
                            1. Generate Embeddings
                 OpenAI text-embedding-3-small (1,536 dims)
                                        |
                                        v
                             2. Hybrid Search Engine
               Dual search across 25 Article nodes in Neo4j
                                        |
                       +----------------+----------------+
                       |                                 |
                 Vector Index                     Fulltext Index
             (Semantic Meaning)                  (Exact Keywords)
                       |                                 |
                       +----------------+----------------+
                                        |
                                        v
                          Scale & Merge Score Lists
                       Keep Top 5 Relevant Articles
                                        |
                                        v
                             3. Graph Grounding
              Traverse (Article)-[:CITES_RULE]->(Rule) in Neo4j
                     Extract linked governing Rule IDs
                                        |
                                        v
                            4. Answer Synthesis
                Claude Sonnet generates answer using ONLY
                      retrieved Articles & Rules
                                        |
                                        v
                            5. Consultant Response
                Deliver answer citing Article IDs + Rule IDs

```



## The core idea

A **procedure** is a list of **steps** for one known problem. Each step asks one yes/no **condition** about the customer. An API checks the condition.

```
Procedure PROC-001  "Customer cannot activate roaming"

  Step 1  Condition: "Is roaming disabled?"     API check: GET /account/{customer_id}/roaming-status
            YES -> Action: "Enable roaming flag and inform customer of 10-minute activation delay"   STOP
            NO  -> go to Step 2

  Step 2  Condition: "Is the customer's plan inactive?"
            YES -> Action: "Direct customer to reactivate plan before roaming works"                 STOP
            NO  -> go to Step 3

  Step 3  Condition: "Does the device NOT support the destination's roaming bands?"   (last step)
            YES -> Action: "Explain that the device does not support the roaming bands ..."          STOP
            NO  -> Action: "Escalate to Tier 2 - unresolved by standard checks"
                   (every check passed, so nothing explains the problem: escalate, never "no action needed")
                   (only the last step has an action for NO, `action_if_false_id`, so a procedure always ends with an answer)
```

- **The API only checks** if a condition is true or false. The action is text that tells the consultant what to do.
- **YES** stops the procedure and gives that step's action. **NO** moves to the next step. On the last step, NO returns that step's fallback action (`action_if_false_id`)
- 3 types of conditions:
  - Field check: Reads a single CRM value to evaluate simple conditions directly (e.g., checking if roaming is disabled).
  - Chain check: Runs several lookups in a row, where one answer feeds the next (e.g. outage data is by zip code, so reads the customer's zip code from the CRM first, then looks up outages for that zip)
  - Ticket check: Uses an LLM to interpret free-text customer tickets, as unstructured human language cannot be parsed by static code.
- A **Rule** is a business policy governing how an action may be carried out: an approval it needs, an amount or time limit, something to never disclose, or a hard block. For example, "Tier 2 escalations require a documented troubleshooting summary". An action can be governed by several rules, and the answer cites each one.
- The final reply is the agent's last message: text with `citations` on the message (the action's ID, then the IDs of any rules governing it).



### Error handling


| Case                                | When it happens                                                                                                                                                    | What is returned                                                                                                                                                 | Where                                                    |
| ----------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------- |
| **Message B**                       | The agent can't find the matching procedure. For example, `match_procedure` returned an ID the graph does not have (the graph and `procedures.json` have drifted). | "I couldn't find a matching procedure for this request. Try rephrasing, or I can search the knowledge base instead." (`citations` is empty and `error` says why) | `handle_fetch_error`, `agent/procedural_executor.py:109` |
| **NO on the last step**             | The procedure was found and every step was checked. The last step's condition is false.                                                                            | fall back to 'action_if_false' defined for each procedure                                                                                                        | `resolve`, `agent/procedural_executor.py:162`            |
| **Unknown customer ID**             | The message names a customer ID that is not in the CRM. Checked first, before any paid model call; a message naming no customer is not checked.                    | "I couldn't find a customer with ID 'CUST-9999'. Check the customer ID and try again." (`citations` is empty and `error` says why)                               | `check_customer`, `agent/supervisor.py`                  |
| **No customer for troubleshooting** | A troubleshooting message, and no customer named yet in this conversation. A general question needs none.                                                          | "Which customer is this about? Start your message with their customer ID, …" (`error` says why)                                                                  | `load_crm`, `agent/supervisor.py`                        |
| **Neo4j down**                      | Neo4j is not reachable (not started, wrong password). Checked right after the customer, before any paid model call.                                                | "The knowledge graph is offline, so I can't answer right now. Ask whoever runs the agent to start Neo4j, then try again." (`error` gives the technical reason)   | `check_neo4j`, `agent/supervisor.py`                     |




### How it is stored in Neo4j

```
(Step)-[:HAS_CONDITION]->(Condition)-[:VALIDATED_BY]->(API)
(Step)-[:HAS_ACTION {outcome: true | false}]->(Action)-[:GOVERNED_BY]->(Rule)
(Step)-[:NEXT_STEP]->(Step)
(Article)-[:CITES_RULE]->(Rule)          knowledge-base articles
```

- There is no `Procedure` node. A procedure is every `Step` with the same `procedureId`, joined in order by `NEXT_STEP`.
- `API` nodes describe how a condition would be checked in production. The agent does not read them. It reads the steps, actions and rules from Neo4j and the full condition from `data/conditions.json`.



## Jev

**Jev** (TypeSafe's classification model, via the `langchain-typesafe` package). Instead of writing prose like a chat LLM, Jev answers a question you define with structured output that code can use straight away: a label, a probability, or a score. You give it some input (text or messages) and one or more questions; it answers them all in one request.

Why it is used for routing:

- **Code can act on it directly.** The answer is a label plus numbers, so there is nothing to parse, and no risk of an answer outside the allowed labels.
- **Probabilities, so you choose the cut-off.** Code can say "walk a procedure only if troubleshooting is at least 50% likely", which is what makes a cautious fallback possible.
- **Fast and small.** In our logs a call takes about 0.3 s, and its output is a few numbers rather than generated text.

Its three kinds of question:


| Kind     | Asks                    | Returns                                                      | Use it for                                 |
| -------- | ----------------------- | ------------------------------------------------------------ | ------------------------------------------ |
| `Noul`   | Is this true?           | the probability of yes, 0 to 1                               | a clear yes/no `if` in code                |
| `Choice` | Which of these options? | the chosen label, a probability per label, a confidence      | sending each option down its own code path |
| `Score`  | Which level on a scale? | a score, the level descriptions, probabilities, a confidence | a spectrum, checked against a threshold    |


A `Noul` of 0.5 means yes and no are equally likely, not "medium", so use it only for a clearly defined yes/no; use `Score` for a level. This project uses only `Choice`, in `classify_intent` and `match_procedure`. Jev is an external service (`api.typesafe.ai`, `TYPESAFE_API_KEY`): the consultant's message is sent to it. The package is still in beta.

## Routing

 (`classify_intent`, then `match_procedure`, in `agent/supervisor.py`). Each is one Jev Choice, given only the consultant's latest message. It judges what the consultant wants done, not the topic: most procedures have a knowledge-base article on the same topic (cancellation, SIM replacement, outage credit). The procedure list is not shown at this point.

```
  the consultant's latest message only (not the conversation)
  |
  v
  one Jev Choice: what does the consultant want done? (not the topic)
  returns a label and a probability for each label,
  e.g. troubleshooting 0.96, open_ended 0.03, explain 0.01
  |
  v
  rules in plain code:
  troubleshooting, P(troubleshooting) >= 0.5  --> troubleshooting  --> match_procedure
  troubleshooting, P(troubleshooting) <  0.5  --> open_ended       --> knowledge base
  explain, on the first message               --> open_ended       --> knowledge base
  explain, after an earlier answer            --> explain          --> explain_answer
  open_ended                                  --> open_ended       --> knowledge base
```

- **Only the latest message.** Measured live, it classifies as well as the whole conversation, and it keeps earlier customers' messages and a growing history out of the Jev calls. The "nothing to explain on the first message" rule checks the saved conversation, which is still complete.
- **The probability, not the confidence.** The decision uses the chosen label's probability. Jev's `confidence` measures how concentrated the whole distribution is, which is harder to read: for "and the fee?" after a cancellation, confidence was 0.54 while open_ended's probability was 0.69.
- **The fee question** ("What are the early termination fees if I cancel my contract?") goes to the knowledge base (open_ended 0.92), the case the old Claude classifier got wrong.
- `match_procedure` is one Choice over the 20 procedures, each described by its name and `when_to_use` (`data/procedures.json`), plus "none". "none", or a confidence below 0.5, sends the message to the knowledge base instead of walking the nearest procedure.
- The 0.5 thresholds are starting values, set at the top of `agent/supervisor.py`. Tune them on a labelled set.



## **Slack** (how a command reaches the agent)

 Slack never talks to Neo4j or the agent directly. Only the bot process talks to Slack, and it calls the agent as a normal Python function:

```
 Slack workspace
   |  consultant types: /agent CUST-1005 customer cannot activate roaming
   |
   |  (1) Slack pushes the command over a websocket that the bot opened itself (app token, xapp-)
   v
 slack_bot/app.py       one Python process on your machine: uv run python -m slack_bot
   |                    acknowledges within Slack's 3 seconds ("Checking..."), then splits the text
   |                    into customer_id "CUST-1005" and the question
   |
   |  (2) ask(question, customer_id)          an ordinary function call in the same process
   v
 agent/supervisor.py    the same graph as above
   |
   +--> Neo4j           steps, actions, rules, KB articles (the URI in .env)
   +--> data/*.json     CRM records and conditions
   +--> Claude, OpenAI  the language-model calls
   |
   |  (3) returns {"answer": ..., "citations": [...]}
   v
 slack_bot/app.py       formats it as a Slack message
   |
   |  (4) sends it to the reply URL Slack included with the command, replacing "Checking..."
   v
 Slack workspace        only the person who typed the command sees the reply
```

A direct message goes the same way, through `slack_bot/chat.py`: the DM (or a thread inside it) is the `thread_id`, and the graph is compiled with the SQLite checkpointer, so the messages (each stamped with its customer) and the current customer are saved between messages. A message naming a different customer switches to them; the conversation is kept.

## Follow-up questions

The agent is compiled with a checkpointer, and each conversation has its own `thread_id`, so the state is saved between messages:

- `messages`: the whole conversation. A consultant message is stamped with the customer it is about (`additional_kwargs["customer_id"]`): the ID it names (stamped by the Slack bot), or the current customer for a troubleshooting follow-up naming none (stamped by `load_crm`). An unstamped message was a general question. Each reply belongs to the consultant message before it;
- `customer_id`: the customer currently being discussed (the last ID named).

A message naming a customer ID is checked (it must exist) and that customer becomes the current one. A message naming none is not checked: a general question needs no customer, and a troubleshooting follow-up uses the current customer (with none yet, the agent asks which one). The CRM record is loaded only for troubleshooting. One conversation can cover several customers and general questions; naming a different customer switches to them and keeps everything. The walk's full `step_trace` stays inside the procedure subgraph and is never saved (see `agent/state.py`). A third path, `explain`, answers questions about an earlier answer:

```
 message 1  "CUST-1001 customer cannot activate roaming"   -> procedure walk -> reply: ACT-015, R012
 message 2  "what does that rule say?"    (no customer ID) -> explain: R012's wording from Neo4j
 message 3  "why did it end up at Tier 2?"                  -> explain: the steps and values in the reply to message 1
 message 4  "CUST-1005 customer cannot activate roaming"   -> switch to CUST-1005, new walk
 message 5  "CUST-1001 why?"                                -> back to CUST-1001: explain from CUST-1001's messages
```

`explain` gives Claude the last 10 messages, each labelled with its customer (`[CUST-1001] ...`, `[reply about CUST-1001] ...`) or as general (`[general] ...`, `[reply, general] ...`), with each reply's sources, plus the wording of the rules those messages cite, fetched from Neo4j. A troubleshooting reply already contains the action, why the walk stopped, and every step with its value, so the conversation is the whole record. If the question names a customer, Claude answers only about that customer; if it names none, it answers about the earlier answer it refers to, usually the latest. Either way it is told never to claim an earlier answer was wrong. Without the labels, an answer for CUST-1005 once looked like CUST-1001's, and Claude "corrected" an answer it never gave. An answer records what was true when it was given. It is not a cache: every new question re-checks the customer's data, because the CRM, not an earlier answer, is the current truth.

Without a checkpointer every call is a single question (that is what `/agent` and `graph.invoke(...)` do). The Slack bot saves conversations in `checkpoints.db` (`config.get_checkpointer()`, SQLite).

## Run it

You need Python 3.11+, [uv](https://docs.astral.sh/uv/) and a local Neo4j 5.18.1 or newer (`neo4j-graphrag` refuses older ones). Developed on 5.26.7. Copy `.env.example` to `.env` and fill it in.

```bash
uv sync
uv run python -m etl.validate        # checks data/ (no Neo4j, no keys)
uv run python -m etl.load_to_neo4j   # builds the graph (Neo4j)
uv run python -m etl.audit_graph     # checks the graph matches data/ (Neo4j)
uv run python -m etl.load_articles   # embeds KB articles (Neo4j + OPENAI_API_KEY)
uv run pytest                        # 163 tests (needs the graph and articles loaded)
uv run pytest -m "not e2e"           # skip the end-to-end tests
uv run python -m slack_bot           # start the Slack bot: /agent and direct messages (needs Slack tokens, see slack_bot/slack_setup.md)
```

```python
from langchain_core.messages import HumanMessage

from agent.utils import reply_of
from agent.supervisor import build_supervisor_graph

graph = build_supervisor_graph()
result = graph.invoke({"messages": [HumanMessage("Customer cannot activate roaming")], "customer_id": "CUST-1005"})
text, citations = reply_of(result)
print(text, citations)
```

Claude features need `ANTHROPIC_API_KEY`. Embeddings need `OPENAI_API_KEY`. Routing (`classify_intent`, `match_procedure`) needs `TYPESAFE_API_KEY`. Tests that call any of them are skipped when the key is missing.

## Status

- **Works:** the data, validation, the Neo4j load, the mock CRM, the troubleshooting path, the open-ended path, and follow-up questions in a saved conversation.
- **Routing uses Jev** and passes the end-to-end tests live, including the fee question the old Claude classifier misrouted. The thresholds are not tuned yet (see TODO below).
- **Slack bot:** `/agent` and direct messages are written and tested with fake Slack events, but never run against a real workspace — that needs your Slack tokens (see `slack_bot/slack_setup.md`).
- **Not built:** Microsoft Teams (needs a public HTTPS endpoint and an Azure Bot registration, no Socket Mode equivalent). Follow-ups can explain and search, but cannot take new facts ("actually roaming is on"): conditions are checked against the CRM data only.



## TODO

**Routing (**`classify_intent` **+** `match_procedure`**)** :

- Tune the three thresholds at the top of `agent/supervisor.py` (all 0.5 for now) on a labelled set: for each procedure, one case message and one policy question on the same topic, plus follow-ups and messages that fit no procedure.
- If the procedure list grows to the hundreds, narrow it first with hybrid search over the procedures, and let Jev choose among the top few.

**Ticket conditions** — discussed, not implemented. CRM stays read-only for now; the CRM only records what already happened, it doesn't decide how to interpret the current message.

- `COND-024` ("issue still ongoing") can become a plain field check on `recent_tickets[-1].resolved` today — no data change needed.
- `COND-027`/`COND-028` (fraud, refund intent) have no structured field to check yet. Either add booleans to `recent_tickets` (e.g. `suspected_fraud`, `refund_requested`), or keep them as the `ticket_text` LLM fallback — structured field first, LLM only when it's missing/generic.
- HITL fallback: when the LLM is low-confidence, pause with `interrupt()` and ask the consultant instead of guessing, rather than silently treating it as NO. Needs the walk compiled with a real checkpointer (currently `checkpointer=False`, which blocks interrupts) and a Slack DM resume path that checks whether a conversation is paused before starting a new turn.

**Saved conversations grow forever** — not handled. No model call needs trimming: the Jev calls and the knowledge-base search get only the latest message, `explain` gets the last 10, and the ticket reader gets only the ticket. But the saved conversation in `checkpoints.db` has no limit, and grows faster than the message count, because LangGraph saves the state after every step (a 4-turn DM had 26 checkpoints). Options: end a conversation after inactivity (e.g. 2 hours) and delete its old checkpoints, or keep only the latest checkpoint per conversation.

**Phase 7B — Eval & Observability** — not started, except that the knowledge-base path is now traced (`kb_search`: the 5 retrieved articles; `kb_answer`: Claude's prompt and answer).

- **Offline:** golden datasets, run as LangSmith experiments: routing (case messages and policy-question twins per procedure), the walk (every procedure × customer, generated from `data/`), tickets, knowledge-base questions (with articles that must be retrieved), multi-turn conversations, and adversarial messages. Deterministic evaluators gate merges (routing accuracy, walk matches the data, citations exist, retrieval recall@5, customer handling); LLM judges are tracked over time (grounding, completeness, explain quality).
- **Online:** Jev decision-model evaluators on live traces, set up in the LangSmith UI: `explain` quality on the explain call (false corrections, customer focus, grounding, rule vs condition) and knowledge-base grounding on `kb_answer`; plus code evaluators and alerts (errors, Neo4j, routing drift, latency).
- No `eval/` directory exists yet.

**Slack** — never run against a real workspace; needs your tokens.

## File structure

```
.
├── README.md, CLAUDE.md        project overview; guidance for Claude Code
├── config.py                   .env loading, the Neo4j driver, the Neo4j check, logging setup, the SQLite checkpointer
├── pyproject.toml, uv.lock     dependencies (uv)
├── .env.example                every setting, with comments; copy to .env
├── .github/workflows/
│   └── data-integrity.yml      CI, on pull requests touching data/, etl/ or mock_crm/: validate,
│                               load a fresh Neo4j, audit the graph
│
├── data/                       synthetic JSON, the source of truth
│   ├── crm_records.json        60 customers
│   ├── conditions.json         the yes/no checks (field, chain or ticket)
│   ├── actions.json            what to tell the consultant to do
│   ├── procedures.json         20 procedures: steps that point at conditions and actions
│   ├── rules.json              policies governing actions
│   ├── kb_articles.json        knowledge-base articles for open-ended questions
│   └── network_status.json, device_financing.json, fraud_gateway.json
│                               small lookup tables behind the chain checks
│
├── etl/                        build the graph from data/
│   ├── validate.py             checks the JSON (IDs match across files, every procedure can finish)
│   ├── load_to_neo4j.py        loads steps, conditions, actions, rules and APIs into Neo4j
│   ├── load_articles.py        embeds the KB articles and loads them (OpenAI)
│   ├── audit_graph.py          checks the live graph still matches data/
│   └── io_utils.py             load_json, shared by the above
│
├── mock_crm/                   the fake CRM and lookup APIs (see mock_crm/README.md)
│   ├── crm_store.py            get_customer
│   ├── condition_evaluator.py  checks a field or chain condition against the CRM
│   └── network_status_store.py, device_financing_store.py, fraud_gateway_store.py
│
├── agent/                      the LangGraph agent
│   ├── supervisor.py           the main graph: customer, Neo4j check, routing (Jev), explain
│   ├── procedural_executor.py  the troubleshooting subgraph: walks a procedure step by step
│   ├── ticket_interpreter.py   Claude reads a customer's ticket (ticket checks only)
│   ├── graph_rag_chain.py      open-ended path: hybrid search over KB articles, Claude answers
│   ├── state.py                graph state only (SuperAgentState, ProceduralExecutorState)
│   ├── data_model.py           Reply (an answer's text and citations)
│   └── utils.py                reply() writes an answer into state, reply_of() reads it back
│
├── slack_bot/                  the Slack front end (see slack_bot/README.md, slack_setup.md)
│   ├── __main__.py             uv run python -m slack_bot
│   ├── app.py                  /agent and direct-message handlers, startup checks
│   ├── access.py               who may use the bot (members channel, allowed users)
│   ├── chat.py                 saved DM conversations (AgentChat)
│   ├── parsing.py              finds the customer ID in a message
│   ├── formatting.py           turns an answer into a Slack message, plus fixed messages
│   └── threads.py              which saved conversation a DM belongs to
│
└── tests/
    ├── test_*.py               unit and integration tests (validate, condition evaluator, executor,
    │                           supervisor, graph RAG)
    ├── slack_bot/              Slack tests, with fake Slack events (no network)
    └── e2e/                    full-graph tests with real Neo4j and model calls, grouped as
                                troubleshooting/, open_ended/, follow_ups/ and errors/
```

`checkpoints.db` (saved conversations) and `.env` are created locally and git-ignored.