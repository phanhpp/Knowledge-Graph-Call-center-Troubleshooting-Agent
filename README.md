# Super Agent (knowledge-graph clone)

A copilot for call-centre consultants. The consultant types a customer's problem, and the agent replies with what to tell or do next. It only advises and never changes anything. It is built on synthetic data, a Neo4j graph and LangGraph.

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

- **The API only checks.** It answers true or false. **Nothing is executed.** The action is text that tells the consultant what to do.
- **YES** stops the procedure and gives that step's action. **NO** moves to the next step. On the last step, NO returns that step's fallback action (`action_if_false_id` in `data/procedures.json`). 
- **Here the API check is simulated.** The agent compares the customer's record in `mock_crm/` with the condition. Some conditions need two lookups, for example ZIP code first and then the outage in that ZIP.
- One kind of condition is different. It asks Claude to read the customer's latest ticket instead of checking a field.
- A **Rule** is a business policy governing how an action may be carried out: an approval it needs, an amount or time limit, something to never disclose, or a hard block. For example, "Tier 2 escalations require a documented troubleshooting summary". An action can be governed by several rules, and the answer cites each one.

## What comes back

The reply is the agent's last message: its text for the consultant, with `citations` on the message (the action's ID, then the IDs of any rules governing it). `reply_of(state)` in `agent/state.py` reads both.

In `data/procedures.json`, every step has `action_if_true_id` and the **last step of every procedure also has** `action_if_false_id`**.** 

Three cases are easy to confuse:


| Case                    | When it happens                                                                                                     | What is returned                                                                                                                                                 | Where                                                    |
| ----------------------- | ------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------- |
| **Message B**           | The agent can't find the matching procedure. For example, `match_procedure` returned an ID the graph does not have (the graph and `procedures.json` have drifted). | "I couldn't find a matching procedure for this request. Try rephrasing, or I can search the knowledge base instead." (`citations` is empty and `error` says why) | `handle_fetch_error`, `agent/procedural_executor.py:109` |
| **NO on the last step** | The procedure was found and every step was checked. The last step's condition is false.                             | fall back to 'action_if_false' defined for each procedure                                                                                                        | `resolve`, `agent/procedural_executor.py:162`            |
| **Unknown customer ID** | The `customer_id` is not in the CRM. This is checked first, before Claude is called. | "I couldn't find a customer with ID 'CUST-9999'. Check the customer ID and try again." (`citations` is empty and `error` says why) | `handle_crm_error`, `agent/supervisor.py` |
| **Neo4j down**          | Neo4j is not reachable (not started, wrong password). Checked right after the customer, before any paid model call. | "The knowledge graph is offline, so I can't answer right now. Ask whoever runs the agent to start Neo4j, then try again." (`error` gives the technical reason) | `check_neo4j`, `agent/supervisor.py` |




## How it is stored in Neo4j

```
(Step)-[:HAS_CONDITION]->(Condition)-[:VALIDATED_BY]->(API)
(Step)-[:HAS_ACTION {outcome: true | false}]->(Action)-[:GOVERNED_BY]->(Rule)
(Step)-[:NEXT_STEP]->(Step)
(Article)-[:CITES_RULE]->(Rule)          knowledge-base articles
```

- There is no `Procedure` node. A procedure is every `Step` with the same `procedureId`, joined in order by `NEXT_STEP`.
- `API` nodes describe how a condition would be checked in production. The agent does not read them. It reads the steps, actions and rules from Neo4j and the full condition from `data/conditions.json`.



## The whole design

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
                          consultant: message + customer_id
                                         |
                                         v
                    inject_crm: load the customer's record (mock_crm/crm_store.py)
                    unknown customer_id: reply "I couldn't find a customer..." and stop
                                         |
                                         v
                    check_neo4j: is Neo4j reachable?
                    no: reply "The knowledge graph is offline..." and stop
                                         |
                                         v
        classify_intent (Jev): what does the consultant want done? (not the topic)
                                         |
  +--------------------------------------+------+
  | troubleshooting                             | open_ended
  v                                             |
  match_procedure (Jev): which of the 20        |
    procedures, by name + when_to_use,          |
    or "none" -> knowledge base instead ------->|
  |                                             |
  v                                             v
  procedural_executor  (troubleshooting)        graph_rag_chain  (open-ended)
  --------------------------------------        -----------------------------
  start at Step 1                               embed the query (OpenAI)
  |                                             |
  v                                             v
  fetch_step                                    search the Article nodes in Neo4j
    Neo4j: the step's condition, next             by meaning (vector index) and
      step and actions                            by keywords (fulltext index),
    conditions.json: the full condition           keep the best 5
  |                                             |
  v                                             v
  check the condition                           for each article, follow CITES_RULE
    field or chain -> mock_crm                    to the Rules it cites
      (CRM record + lookup files)               |
    ticket text -> Claude reads the             v
      customer's latest ticket                  Claude writes the answer from
  |                                               those articles
  +- YES -----------------> resolve             |
  +- NO, more steps ------> next step,          |
  |                        back to fetch_step   |
  +- NO, last step -------> resolve             |
  |                                             |
  v                                             |
  resolve                                       |
    Neo4j: the action's text and the            |
      Rules that govern it                      |
  |                                             |
  +---------------------------------------------+
                       |
                       v
          answer + citations
          (cites the action and its rules, or the articles and their rules)
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

The reply is built in plain code (`reply_text` in `agent/procedural_executor.py`), not by an LLM: the action is policy text and reaches the consultant word for word. For `CUST-1005`, whose device does not support roaming, step 3 answers YES and the reply is the device action (`ACT-027`).

The terminal running the agent logs the same walk. `LOG_LEVEL` in `.env` sets how much: `DEBUG` (the default) logs every step and Jev's confidence numbers, `INFO` one line per decision.

Note: Rules just for citations, it's doesn't affect how agent traverse the graph.

**Routing** (`classify_intent`, then `match_procedure`, in `agent/supervisor.py`). Jev answers two questions about the same conversation in one request. Both judge what the consultant wants done, not the topic: most procedures have a knowledge-base article on the same topic (cancellation, SIM replacement, outage credit). The procedure list is not shown at this point.

```
            state["messages"]  (whole conversation; last = new message)
                            |
              one Jev request, two questions
          +-----------------+------------------+
          v                                    v
  path: Choice                           own_situation: Noul
  "what does the consultant              "does the answer depend on THIS
   want done?" (not the topic)            customer's account?"
          |                                    |
  returns a label + confidence           returns P(yes), 0 to 1
  e.g. troubleshooting, 0.92             e.g. 0.87
          +-----------------+------------------+
                            v
                 rules in plain code:
  troubleshooting  AND confidence >= 0.5  AND P(yes) >= 0.5  --> troubleshooting --> match_procedure
  troubleshooting  but either check fails                    --> open_ended      --> knowledge base
  explain          on the very first message                 --> open_ended      --> knowledge base
  otherwise                                                  --> the label as given
```

- **Why two questions.** A Choice always picks one of its labels, so its confidence only says how sure Jev is among the three. The Noul asks the yes/no distinction on its own, as an independent check before anything is walked. For "What are the early termination fees if I cancel my contract?", the Choice may lean to troubleshooting because of "cancel", but P(yes) should be low (the fee is the same for every customer), so it goes to the knowledge base.
- **`match_procedure`** is one Choice over the 20 procedures, each described by its name and `when_to_use` (`data/procedures.json`), plus "none". "none", or a confidence below 0.5, sends the message to the knowledge base instead of walking the nearest procedure.
- The 0.5 thresholds are starting values, set at the top of `agent/supervisor.py`. Tune them on a labelled set.

**Slack** (how a command reaches the agent). Slack never talks to Neo4j or the agent directly. Only the bot process talks to Slack, and it calls the agent as a normal Python function:

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

A direct message goes the same way, through `slack_bot/chat.py`: the DM (or a thread inside it) is the `thread_id`, and the graph is compiled with the SQLite checkpointer, so the messages, the current customer and each customer's last troubleshooting result are saved between messages. A message naming a different customer switches to them; the conversation is kept.

## Follow-up questions

The agent is compiled with a checkpointer, and each conversation has its own `thread_id`, so the state is saved between messages:
- `messages`: the whole conversation;
- `customer_id`: the customer currently being discussed (the last ID named);
- `troubleshooting_results`: each customer's last procedure result, kept short: procedure, action, rules, and one line per step checked.

One conversation can cover several customers. Naming a different customer switches to them and keeps everything; coming back to an earlier customer finds their result again. The walk's full `step_trace` stays inside the procedure subgraph and is never saved (see `agent/state.py`). A third path, `explain`, answers questions about an earlier answer:

```
 message 1  "CUST-1001 customer cannot activate roaming"   -> procedure walk -> reply: ACT-015, R012
            saved: the reply, and troubleshooting_results["CUST-1001"]
 message 2  "what does that rule say?"    (no customer ID) -> explain: the rule's wording from Neo4j
 message 3  "why did it end up at Tier 2?"                  -> explain: the steps and values from CUST-1001's result
 message 4  "CUST-1005 customer cannot activate roaming"   -> switch to CUST-1005, new walk; CUST-1001's result is kept
 message 5  "CUST-1001 why?"                                -> back to CUST-1001: explain from CUST-1001's result
```

`explain` gathers the facts in plain code (the current customer's result, plus the action and rule text from Neo4j) and Claude words the answer from those only. It is given only the current customer's result, so one customer's facts don't end up in another's answer. A result records what was true when the answer was given. It is not a cache: every new question re-checks the customer's data (see `docs/lessons.md`).

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

from agent.state import reply_of
from agent.supervisor import build_supervisor_graph

graph = build_supervisor_graph()
result = graph.invoke({"messages": [HumanMessage("Customer cannot activate roaming")], "customer_id": "CUST-1005"})
text, citations = reply_of(result)
print(text, citations)
```

Claude features need `ANTHROPIC_API_KEY`. Embeddings need `OPENAI_API_KEY`. Routing (`classify_intent`, `match_procedure`) needs `TYPESAFE_API_KEY`. Tests that call any of them are skipped when the key is missing.

## Status

- **Works:** the data, validation, the Neo4j load, the mock CRM, the troubleshooting path, the open-ended path, and follow-up questions in a saved conversation.
- **Routing uses Jev, but has not been run live yet** (see TODO below).
- **Slack bot:** `/agent` and direct messages are written and tested with fake Slack events, but never run against a real workspace — that needs your Slack tokens (see `slack_bot/slack_setup.md`).
- **Not built:** Microsoft Teams (needs a public HTTPS endpoint and an Azure Bot registration, no Socket Mode equivalent). Follow-ups can explain and search, but cannot take new facts ("actually roaming is on"): conditions are checked against the CRM data only.

## TODO

**Routing (`classify_intent` + `match_procedure`)** — built with Jev, tested only with a fake classifier. To finish:
- Add `TYPESAFE_API_KEY` to `.env` and run the e2e tests. The old Claude classifier sent "What are the early termination fees if I cancel my contract?" to the "Contract cancellation request" procedure. `tests/e2e/open_ended/test_knowledge_questions.py::test_a_fee_question_that_sounds_like_a_procedure_goes_to_the_knowledge_base` is still marked `xfail`; remove the marker once it passes.
- Tune the three thresholds at the top of `agent/supervisor.py` (all 0.5 for now) on a labelled set: for each procedure, one case message and one policy question on the same topic, plus follow-ups and messages that fit no procedure.
- If the procedure list grows to the hundreds, narrow it first with hybrid search over the procedures, and let Jev choose among the top few.

**Ticket conditions** — discussed, not implemented. CRM stays read-only for now; the CRM only records what already happened, it doesn't decide how to interpret the current message.
- `COND-024` ("issue still ongoing") can become a plain field check on `recent_tickets[-1].resolved` today — no data change needed.
- `COND-027`/`COND-028` (fraud, refund intent) have no structured field to check yet. Either add booleans to `recent_tickets` (e.g. `suspected_fraud`, `refund_requested`), or keep them as the `ticket_text` LLM fallback — structured field first, LLM only when it's missing/generic.
- HITL fallback: when the LLM is low-confidence, pause with `interrupt()` and ask the consultant instead of guessing, rather than silently treating it as NO. Needs the walk compiled with a real checkpointer (currently `checkpointer=False`, which blocks interrupts) and a Slack DM resume path that checks whether a conversation is paused before starting a new turn.

**Trimming long conversations** — not done. Every Jev and Claude call sends the whole conversation, which grows when one conversation covers several customers. Use `trim_messages` (`langchain_core.messages`) on what each call is given, not on what is saved. `SummarizationMiddleware` does not fit: like all middleware it only runs inside `create_agent`, and the supervisor is a plain `StateGraph`.

**Phase 7B — Eval & Observability** (`docs/proposed_plan.md`, not started):
- Two LangSmith datasets: troubleshooting (query + customer_id → expected procedure/action) and open-ended (question → expected article/rule IDs).
- Code evaluators: `procedure_resolved`, `correct_path_routed`, `citation_present`, `no_hallucinated_rule`.
- LLM-judge evaluators (trend tracking, non-blocking): `resolution_relevance`, `citation_faithfulness`, `policy_compliance`.
- LangSmith alerts: tool error rate, Neo4j returning null, a procedure not terminating, Graph RAG with no citations.
- `baselines.json` seeded from 20-30 real queries, flag anything over 3x the median latency.
- No `eval/` directory exists yet.

**Slack** — never run against a real workspace; needs your tokens.

## Repo map

```
data/        synthetic JSON (the source of truth)
etl/         validate the JSON, load it into Neo4j, audit the graph, embed KB articles
mock_crm/    the fake CRM and lookup APIs (see mock_crm/README.md)
agent/       supervisor, procedural executor (the walk above), ticket reader, open-ended search
slack_bot/   the Slack front end (see slack_bot/README.md)
tests/       unit and integration tests; tests/e2e/ has the full-graph tests, grouped as
             troubleshooting/, open_ended/ and errors/; tests/slack_bot/ has the Slack tests
```

