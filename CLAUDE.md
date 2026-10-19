# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project state

Implemented and deployed: the agent (`agent/`), the data pipeline (`data/`, `etl/`), the mock CRM, a Slack bot (`slack_bot/`), a Next.js web app (`web/`), online evaluators (`eval/`), and LangGraph Server serving (`langgraph.json`, LangSmith Deployment, Neo4j Aura). The README covers the architecture and how to run everything. Commands:

```bash
uv sync
uv run python -m etl.validate && uv run python -m etl.load_to_neo4j && uv run python -m etl.audit_graph && uv run python -m etl.load_articles
uv run pytest -m "not e2e"          # fast tests; `uv run pytest` adds the e2e tests (real Neo4j and model calls)
uv run langgraph dev                # the agent server on :2024
cd web && pnpm install && pnpm dev  # the web app on :4200; `pnpm typecheck`, `pnpm test`, `pnpm test:e2e`
```

`docs/` holds local planning notes and is git-ignored: references to `docs/…` below only resolve on the author's machine.

## What this project is

A personal project inspired by the published architecture of Vodafone/Fastweb's "Super Agent" (not affiliated) — an internal copilot for call-center consultants (never customer-facing) — built with synthetic data instead of real enterprise systems. Full context lives in `docs/overview.md`, `docs/input_plan.md`, and `docs/proposed_plan.md`; read those before making architectural decisions, since they encode specific reasoning about why this design was chosen over alternatives.

### Core architecture (from docs/overview.md)

Troubleshooting logic and policy are encoded as graph data in Neo4j, not as prompt text. Vodafone's original schema (`docs/overview.md`):

```
(:Step) -[:HAS_CONDITION]-> (:Condition)
(:Step) -[:HAS_ACTION]->    (:Action)
(:Step) -[:REQUIRES_API]->  (:API)
(:Step) -[:NEXT_STEP]->     (:Step)
(:Condition) -[:VALIDATED_BY]-> (:API)
(:Action) -[:GOVERNED_BY]-> (:Rule)
(:Action) -[:CITES]->       (:Document)
```

**This project's actual schema drops `(:Step)-[:REQUIRES_API]->(:API)`.** Our procedure steps check exactly one condition each (Vodafone's may check more, which is presumably why they keep both edges), so the API is only reachable via `(:Step)-[:HAS_CONDITION]->(:Condition)-[:VALIDATED_BY]->(:API)` — a second, direct Step→API edge would just be a duplicate fact that can drift out of sync with the first. Do not add `REQUIRES_API` back in without a real case where a step needs an API that isn't reachable through its condition.

A query enters through a Supervisor that (1) injects mock CRM context by customer ID and (2) classifies intent into one of two paths:
- **Procedural Executor** (troubleshooting): walks the `Step` chain via `NEXT_STEP`, checking each `Condition` against the injected CRM record via the mocked verification API reached through `VALIDATED_BY`, until an `Action` resolves or steps are exhausted.
- **Graph RAG Chain** (open-ended questions): vector search over KB articles, grounded/cited and policy-filtered against `Rule` nodes in Neo4j.

`Rule` nodes are separate graph nodes linked via `GOVERNED_BY`, not text embedded in prompts — policy is enforced structurally so it can't be silently ignored by the LLM.

**`Action` nodes are text for the human consultant, never an executed API call.** Per `docs/overview.md` (lines ~392, ~410): Super TOBi executes transactions via action tags to its own frontend; Super Agent — what this project models — "only guides humans — it never executes anything directly." Every `API` node in this graph, reached via `Condition-[:VALIDATED_BY]->API`, is read-only verification (checking whether a condition holds), never a mutation. Do not add an `(:Action)-[:EXECUTES]->(:API)`-style relationship or a mutating endpoint on an Action — that would be modelling Super TOBi's capability, not Super Agent's, and was explicitly decided against.

`Condition`, `Action`, and `API` are only worth modeling as separate nodes (rather than plain properties on `Step`) because they're generated as **shared, reusable libraries** and referenced by ID/endpoint from many procedure steps — see "Data dependency order" below. `verification_api` lives on the `Condition` entry, not the step, for the same reason: it's a fact about the condition (how do you verify it), not about which step happens to reference it. If you ever see a `Condition`/`Action` node with exactly one incoming `HAS_CONDITION`/`HAS_ACTION` relationship, or an `API` node with exactly one incoming `VALIDATED_BY`, that's a sign the library-reuse step didn't work as intended, not a schema problem.

### Deliberate divergence from Vodafone's real pipeline

Vodafone's production ETL parses unstructured Word/PDF docs via a 5-stage LangGraph ReAct pipeline (parse → extract → validate). This project skips that entirely: procedures, rules, CRM records, and KB articles are generated directly as constrained JSON via LLM prompts (see the prompts in `docs/input_plan.md`), then checked with a plain Python validation script instead of ReAct agents. The resulting Neo4j schema and downstream agent behavior are meant to be identical either way — this is a substitution for input-side complexity only, and should be treated as intentional, not as a gap to "fix" by building the ReAct pipeline unless asked.

**Vector search is Neo4j-native, not a separate vector store.** This is a settled decision, not an open option: `CREATE VECTOR INDEX` + the Cypher 25 `SEARCH` clause (GA since Neo4j 2026.02, verified via Neo4j's own docs), with retrieval done through the official `neo4j-graphrag` Python package's `HybridCypherRetriever` (vector + fulltext + a `retrieval_query` Cypher fragment that does the "graph grounding" step — pulling `Rule` nodes for citations). No Pinecone/Chroma/FAISS, and no `langchain-neo4j` `Neo4jVector` wrapper either — `docs/neo4j_vectorindex.md` explains why and has the current reference patterns. Requires Neo4j 2026.02+ and `neo4j-graphrag>=1.18.0`.

### Data dependency order

When generating or modifying the synthetic datasets described in `docs/input_plan.md`, IDs and field names must match across files exactly — this is called out as the most likely bug source. Generate/update in this order:
1. **CRM records** — defines the field vocabulary (e.g. `roaming_flag`, `device.device_roaming_compatible`)
2. **Condition library** (`conditions.json`) — `field` values must reference CRM fields exactly; each entry also carries its own `verification_api`, generated once and referenced by ID from many procedure steps
3. **Action library** (`actions.json`) — generated once, referenced by ID from procedure steps and from rules' `applies_to_action_ids`
4. **Procedures** — each step references a `condition_id` and `action_if_true_id`/`action_if_false_id` from steps 2-3, never inline condition/action text
5. **Rules** — `applies_to_action_ids` reference step 3's action IDs
6. **KB articles** — reference rule IDs from step 5

### Project layout

```
data/      — crm_records.json, conditions.json, actions.json, procedures.json, rules.json, kb_articles.json
etl/       — validate.py, load_to_neo4j.py, load_articles.py, audit_graph.py
agent/     — state.py (graph state only), supervisor.py, procedural_executor.py, graph_rag_chain.py, ticket_interpreter.py,
             data_model.py (Reply), utils.py (reply / reply_of), sources.py, graph.py + http_app.py + routes/ (served by LangGraph Server)
mock_crm/  — crm_store.py, condition_evaluator.py, lookup stores
slack_bot/ — the Slack front end (Bolt, Socket Mode)
web/       — the Next.js web app (proxy route to the agent, password gate, Vitest + Playwright)
eval/      — evaluation plan and the online code evaluators
```

ETL loads via Python driver batch writes (`UNWIND $rows AS row CALL (row) {...} IN TRANSACTIONS`), not `apoc.load.json` — the data is already Python-validated local JSON, not something Neo4j needs to fetch itself. Uniqueness/node-key constraints on every `MERGE`-targeted label are created before import (see `docs/proposed_plan.md` Phase 2).

Stack: LangGraph (plain nodes — no `deepagents`, see below), Neo4j (graph + native vector index) + `neo4j-graphrag` (retrieval), LangSmith (tracing/eval from day one), Anthropic models.

**The Procedural Executor (`agent/procedural_executor.py`) is almost entirely deterministic, with one deliberate exception.** Conditions come in 3 kinds — see `docs/data_schema.md` — `field_check` (one API) and `chain_check` (a *declared*, ordered multi-call chain, e.g. zip → outage-by-zip) are both still code, no agent framework, since the call sequence is always known in advance: nothing to Plan/parallelize, nothing ambiguous to Replan around. `ticket_text` is the exception: interpreting a free-text ticket description is language understanding, not a lookup, so `agent/ticket_interpreter.py` genuinely calls an LLM there — and it's also the one place Replanning is real (a low-confidence first pass retries once with more evidence), not decorative. `advance_or_resolve()` (the routing after either evaluator sets `condition_result`) returns a `Command`, not a plain conditional edge, because looping back to `fetch_step` requires advancing `current_step_id` first — a conditional edge can pick the next node but can't also update state, which caused a real infinite-loop bug during implementation (see `tests/test_procedural_executor.py`).

## TODO

- **Ask what the problem is when a message is unclear.** Done for the customer: a message naming no customer is fine (a general question needs none); a named ID is checked first (`check_customer`), and troubleshooting with no customer yet replies "Which customer is this about?" (`load_crm`). Not done: asking a clarifying question when the problem itself is unclear, instead of routing it to the knowledge base.
