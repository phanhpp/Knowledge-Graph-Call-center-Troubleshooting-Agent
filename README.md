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

  Step 3  Condition: "Does the device support roaming bands?"                 (last step)
            YES -> Action: "Confirm device is roaming-compatible; no further action needed"          STOP
            NO  -> Action: "Escalate to Tier 2 - unresolved by standard checks"
                   (only the last step has an action for NO, `action_if_false_id`, so a procedure always ends with an answer)
```

- **The API only checks.** It answers true or false. **Nothing is executed.** The action is text that tells the consultant what to do.
- **YES** stops the procedure and gives that step's action. **NO** moves to the next step. On the last step, NO returns that step's fallback action (`action_if_false_id` in `data/procedures.json`). 
- **Here the API check is simulated.** The agent compares the customer's record in `mock_crm/` with the condition. Some conditions need two lookups, for example ZIP code first and then the outage in that ZIP.
- One kind of condition is different. It asks Claude to read the customer's latest ticket instead of checking a field.
- A **Rule** is a policy limit on an action, such as "Tier 2 escalations require a documented troubleshooting summary". If an action has one, the answer cites it.

## What comes back

The reply is the agent's last message: its text for the consultant, with `citations` on the message (the action's ID, then the IDs of any rules that limit it). `reply_of(state)` in `agent/state.py` reads both.

In `data/procedures.json`, every step has `action_if_true_id` and the **last step of every procedure also has** `action_if_false_id`**.** 

Three cases are easy to confuse:


| Case                    | When it happens                                                                                                     | What is returned                                                                                                                                                 | Where                                                    |
| ----------------------- | ------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------- |
| **Message B**           | The agent can't find the matching procedure. For example, `classify_intent` returned a wrong or empty procedure ID. | "I couldn't find a matching procedure for this request. Try rephrasing, or I can search the knowledge base instead." (`citations` is empty and `error` says why) | `handle_fetch_error`, `agent/procedural_executor.py:109` |
| **NO on the last step** | The procedure was found and every step was checked. The last step's condition is false.                             | fall back to 'action_if_false' defined for each procedure                                                                                                        | `resolve`, `agent/procedural_executor.py:162`            |
| **Unknown customer ID** | The `customer_id` is not in the CRM. This is checked first, before Claude is called. | "I couldn't find a customer with ID 'CUST-9999'. Check the customer ID and try again." (`citations` is empty and `error` says why) | `handle_crm_error`, `agent/supervisor.py` |




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

**Answer** (every request). "Claude" and "OpenAI" mark the language-model calls:

```
                          consultant: message + customer_id
                                         |
                                         v
                    inject_crm: load the customer's record (mock_crm/crm_store.py)
                    unknown customer_id: reply "I couldn't find a customer..." and stop
                                         |
                                         v
            classify_intent (Claude): reads the messages and the procedure list
                                         |
  +--------------------------------------+------+
  | troubleshooting + procedure id              | open_ended
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

Example: customer `CUST-1005` asks "Customer cannot activate roaming". The answer is NO at steps 1, 2 and 3, so the agent returns `Escalate to Tier 2 - unresolved by standard checks` with citations `["ACT-015", "R012"]`.

Note: Rules just for citations, it's doesn't affect how agent traverse the graph.

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

A direct message goes the same way, through `slack_bot/chat.py`: the DM (or a thread inside it) is the `thread_id`, and the graph is compiled with the SQLite checkpointer, so the messages, the customer and the last trace are saved between messages. A message naming a different customer deletes that conversation and starts again.

## Follow-up questions

A conversation is one case. The agent is compiled with a checkpointer, and each conversation has its own `thread_id`, so the state is saved between messages: `messages` (the whole conversation), `customer_id`, and `step_trace` (what the last procedure walk checked, with the action it gave). A third path, `explain`, answers questions about the previous answer:

```
 message 1  "CUST-1005 customer cannot activate roaming"   -> procedure walk -> reply: ACT-015, R012
            saved: the reply, and step_trace (each step, YES or NO, the value it used, the action)
 message 2  "what does that rule say?"    (no customer ID) -> explain: the rule's wording from Neo4j
 message 3  "why did it end up at Tier 2?"                  -> explain: the steps and values from step_trace
 message 4  "What is the SIM replacement fee?"              -> knowledge-base search (the latest message only)
 message 5  "Reported service outage in customer's area"    -> a new walk; step_trace now describes it
```

`explain` gathers the facts in plain code (the trace, plus the action and rule text from Neo4j) and Claude words the answer from those only. The trace records what was true when the answer was given. It is not a cache: every new question re-checks the customer's data.

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
from agent.supervisor import build_supervisor_graph

graph = build_supervisor_graph()
result = graph.invoke({"query": "Customer cannot activate roaming", "customer_id": "CUST-1005"})
print(result["answer"], result["citations"])
```

Claude features need `ANTHROPIC_API_KEY`. Embeddings need `OPENAI_API_KEY`. Tests that call either are skipped when the key is missing.

## Status

- **Works:** the data, validation, the Neo4j load, the mock CRM, the troubleshooting path, the open-ended path, and follow-up questions in a saved conversation.
- **`classify_intent` is a placeholder.** It works, but it is meant to be replaced. It shows Claude the procedure list and the conversation, and it can misroute a policy question that sounds like a procedure (for example early termination fees).
- **Slack bot:** `/agent` and direct messages are written and tested with fake Slack events, but not yet run against a real workspace. That needs your Slack tokens (see `slack_bot/slack_setup.md`).
- **Not built:** Microsoft Teams (it needs a public HTTPS endpoint and an Azure Bot registration). Follow-ups can explain and search, but cannot take new facts ("actually roaming is on"): conditions are checked against the CRM data only.

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

