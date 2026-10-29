# Testing

How Super Agent is tested, layer by layer: what each suite covers, what it needs, and how to run it. The README has the short version.

| Layer | Tool | Where | Tests | Needs |
|---|---|---|---|---|
| Agent, data, Slack bot | pytest | the rest of `tests/` | 259 (234 fast + 25 end-to-end) | Neo4j with the graph loaded; end-to-end also Claude, Jev and OpenAI keys |
| The evaluators | pytest | `tests/eval/`, `tests/e2e/judges/` | 68 (62 fast + 6 end-to-end) | nothing; the end-to-end ones a Jev key |
| Web app logic | Vitest | `web/lib/*.test.ts` | 40 | nothing (no browser, no server) |
| Web app in a browser | Playwright | `web/e2e/` | 23 | nothing: a fake agent replays recorded traffic |
| A running server | smoke script | `deploy/smoke.py` | 15 checks | a local or deployed agent server (real model calls) |
| Every pull request | GitHub Actions | `.github/workflows/ci.yml` | 2 jobs | runs in CI, no secrets; required before merging to `main` |
| Data and graph | GitHub Actions | `.github/workflows/data-integrity.yml` | 1 job | runs in CI with a throwaway Neo4j, on data pull requests |

## Running them

```bash
uv run pytest -m "not e2e"        # fast suite: unit and integration tests (Neo4j with the graph loaded)
uv run pytest                     # everything, including the 31 end-to-end tests (real model calls)
uv run pytest tests/slack_bot     # one area

cd web
pnpm typecheck                    # next typegen && tsc --noEmit: type errors, without running anything
pnpm test                         # Vitest
pnpm test:e2e                     # production build, then Playwright in Chromium

uv run langgraph dev &            # then, from the repo root:
uv run python deploy/smoke.py                       # against the local server
uv run python deploy/smoke.py --url <deployment>    # against a deployment (sends LANGSMITH_API_KEY)
```

- **Tests never pollute production traces.** `tests/conftest.py` sends every test run to the LangSmith project `kg-tests`; the online evaluators score only `kg`.
- **Missing keys skip, not fail.** Tests that call Claude, Jev or OpenAI are marked with `requires_*_key` (`tests/e2e/markers.py`) and skip when the key isn't set.

## pytest (`tests/`)

### Agent, data, Slack bot

| File | Tests | Covers |
|---|---|---|
| `test_supervisor.py` | 68 | the main graph: customer and Neo4j checks, routing and the follow-up rewrite (fake Jev, fake Claude), Jev retries, the explain agent |
| `test_explain_tools.py` | 14 | the explain agent's tools can read procedures, rules and articles, and nothing else |
| `test_procedural_executor.py` | 16 | the troubleshooting walk and its reply format |
| `test_graph_rag_chain.py` | 6 | the knowledge-base search and answer (4 need keys); a question with query syntax (`:`) still searched; a follow-up searched as rewritten |
| `test_sources.py` | 12 | the text behind each cited ID; the run id for 👍 👎 |
| `test_config.py` | 3 | a down Neo4j is reported, and alerts can see it |
| `test_validate.py` | 7 | the data files agree with each other; every procedure can finish |
| `test_condition_evaluator.py` | 21 | conditions checked against the mock CRM |
| `test_http_app.py` | 11 | the custom routes: `/customers`, `/sources`, `/feedback` |
| `slack_bot/` (8 files) | 76 | the Slack bot, with fake Slack events |
| `e2e/troubleshooting/` | 6 | walks, with real models |
| `e2e/open_ended/` | 3 | knowledge-base questions, with real models |
| `e2e/follow_ups/` | 11 | follow-ups and saved conversations, with real models |
| `e2e/errors/` | 5 | unknown customer IDs stop before any paid model call |

### The evaluators

These test the eval, not the agent, with no model calls (except 6 with the real Jev). The offline eval works in 4 steps, and each step has its own tests:

```
1. Golden data    ──►  2. Code checks      ──►  3. Jev judges       ──►  4. The gate
the right answers      score exact things       score what needs         compares the scores
for 81 examples        (path, rules, MRR…)      reading (facts…)         with thresholds.yaml
```

| Step | Its tests check | If it were wrong | Key tests | Tests |
|---|---|---|---|---|
| 1. Golden data | the right answers are right (the oracle recomputes the walks from `data/`), and each dataset file is what its build script makes | a good agent fails, or a broken one passes | `test_walks_answer_key_still_matches_the_data`, `test_oracle_matches_the_known_roaming_walk`, `test_kb_file_is_what_its_build_script_makes` (one like it per dataset) | 16 |
| 2. Code checks | each check passes a good reply, fails a bad one, and skips one it doesn't apply to | wrong scores, e.g. the right article at #2 scored 0 instead of 0.5 MRR | `test_recall_and_mrr_find_the_article_in_search_order`, `test_walk_evaluators`, `test_checklist_parser_reads_the_agents_real_reply_format` | 13 |
| 3. Jev judges | each judge asks Jev the right questions about what the agent saw, and turns its probabilities into scores (fake Jev); the real Jev gets 6 answers with a known verdict right | a made-up fact passes, or a good answer fails | `test_answerable_questions_ask_about_each_fact_and_contradiction`, `test_unanswerable_questions_ask_whether_it_admits_the_gap`, `test_a_good_answer_passes` (real Jev) | 10, + 6 real |
| 4. The gate | `run.py --gate` exits 1 below a pass mark, and asks for a rerun when runs errored | a quality drop passes, or an outage looks like one | `test_gate_fails_below_a_threshold_and_passes_at_it`, `test_gate_applies_as_a_rate_with_repetitions`, `test_gate_asks_for_a_rerun_when_runs_errored` | 6 |

The files go by suite, not by step: `eval/test_offline_walks.py` (14), `eval/test_offline_routing.py` (4), `eval/test_offline_kb.py` (12), `eval/test_offline_behaviour.py` (15: guards, conversations, adversarial) and `e2e/judges/` (6, real Jev). The online eval's code checks have their own: `eval/test_online_code_evaluators.py` (17).

## Vitest (`web/lib/`)

Each file sits next to the code it tests; no browser and no agent server (jsdom, stubbed `fetch`).

| File | Tests | Covers |
|---|---|---|
| `auth.test.ts` | 6 | the password gate: the right password passes; a wrong or forged cookie fails; the cookie never contains the password; no password configured lets nobody in |
| `message.test.ts` | 9 | the message sent to the agent (customer ID in `additional_kwargs`, cleaned up); reading citations, sources and run ids; the saved state winning over a streamed reply |
| `use-feedback.test.ts` | 10 | 👍 👎: the vote posted with the right run id; marked sent or failed; read back after a reload; an in-flight vote never overwritten |
| `panel-size.test.ts` | 7 | panel sizes: kept within limits, saved and restored, safe when browser storage is blocked |
| `use-full-source.test.ts` | 4 | a source's full text: fetched through the proxy, cached per page, a 404 told apart from a failure |
| `agent.test.ts` | 4 | where the browser sends agent traffic; the graph name exists in `langgraph.json` |

## Playwright (`web/e2e/`)

A real production build (`next start`) in Chromium. The agent is fake: `mock-agent.ts` serves **traffic recorded from the real server** (`fixtures/flow.json`), so the tests need no agent, no keys and no model calls, and give the same result every run. The password gate, the pages, and what the browser renders and sends are all real.

| File | Tests | Covers |
|---|---|---|
| `login.spec.ts` | 5 | a visitor is sent to `/login`; a wrong password is refused; the right one opens the chat; the agent API refuses no cookie and a forged one (real server, no mock) |
| `chat.spec.ts` | 8 | a walk shows its chips, sources and the customer card; an explanation has no chips; a chip highlights its source; a knowledge-base answer lists its articles; sending puts the customer ID in the message (and cleans up `cust-1001`); a general question carries none; a malformed ID blocks sending; saved conversations open on click |
| `feedback.spec.ts` | 3 | 👍 posts the reply's run id and score; an earlier vote shows after a reload; a failed vote offers a retry |
| `errors.spec.ts` | 2 | the agent unreachable (502) shows an error instead of "Working on it…" forever; an unknown customer shows on the card |
| `panels.spec.ts` | 5 | dragging widens the side panel and the width survives a reload; arrow keys resize; the customer card resizes; a source expands to its full text and collapses; a chip expands its source |

**Re-record the fixtures** when the reply shape or the server protocol changes: with `uv run langgraph dev` and `pnpm dev` running, `node e2e/record-fixtures.mjs` drives the real stack once (real model calls) and rewrites `fixtures/flow.json`. Stale fixtures would pass while the real app breaks.

## Smoke test (`deploy/smoke.py`)

One real conversation against a running server, local or deployed, run after every deployment. 15 checks:

1. A walk for `CUST-1001`: the customer ID survives the trip over HTTP; the reply cites `ACT-015` and `R012`, with matching sources and a run id.
2. "why?" on the same thread: answered from the saved conversation, citing nothing.
3. The custom routes: `/customers` (200 and 404), a 👍 written and read back.
4. Against a deployment: every custom route **refuses a request without the API key** (LangGraph Server leaves added routes open unless `enable_custom_route_auth` is set).

## CI

Two GitHub Actions workflows, both without secrets.

**`ci.yml`, the code check**, on every pull request and every push to `main`. Both jobs must pass before a pull request can merge into `main`.

| Job | Runs |
|---|---|
| `web` | `pnpm install --frozen-lockfile`, `pnpm typecheck`, `pnpm test` (Vitest), then `pnpm test:e2e` (production build + Playwright against the fake agent). A failed run keeps the Playwright traces as an artifact |
| `agent` | an **import check with only the deployed dependencies** (`uv sync --no-dev`), proving the image LangSmith builds is complete; then the graph is validated and loaded into a throwaway Neo4j 5.26 and the fast pytest suite runs on **Python 3.12**, the deployment's version: 289 pass, 7 skip because they need API keys |

**`data-integrity.yml`, the data check**, on pull requests that touch `data/`, `etl/` or `mock_crm/` (or the workflow itself):

1. `etl.validate`: the JSON files are consistent and every procedure can finish.
2. Load the **base branch's** data into a throwaway Neo4j, then this pull request's data **on top**: the way the real graph gets updated.
3. `etl.audit_graph`: every step's `HAS_CONDITION`, `HAS_ACTION` and `NEXT_STEP` edges match `procedures.json` exactly.

Loading twice matters: a fresh, empty database can't have stale edges, so a single load would pass the audit whatever the loader does. Loading over existing data is what exercises the loader's stale-edge cleanup (`MERGE` alone only ever adds edges).

## Evaluation

Evaluation is separate from testing: the offline eval scores the agent on golden examples before a change ships, and the online eval scores every real conversation. See the README's Evaluation section and `eval/README.md`. The offline eval runs in CI too (`offline-eval.yml`), on pull requests that change the agent, its data, the mock CRM, the loaders or the eval.

## Bugs pinned by a test

| Bug | Caught by |
|---|---|
| The walk looping forever (routing couldn't advance the step) | `test_procedural_executor.py` |
| A reloaded graph keeping stale edges | `etl.audit_graph` after loading twice, in the data-integrity CI |
| Explanations treating a follow-up as a "general" question, and Claude copying history labels into its reply | `test_supervisor.py` |
| A down Neo4j invisible to alerts | `test_config.py` |
| A 👍 filed on the wrong run | `test_sources.py` (the run id is the root run) |
| Live replies missing their citations, sources and run id | `message.test.ts`, `chat.spec.ts` |
| An unreachable agent leaving the page on "Working on it…" | `errors.spec.ts` |
| A vote overwritten while still being sent | `use-feedback.test.ts` |
| Custom routes answering without the API key | `deploy/smoke.py` |
| A Python 3.12-only crash hidden by 3.14 | the CI `agent` job runs the suite on 3.12 |
| One busy moment at Jev (529 Overloaded) or a dropped connection failing a consultant's message | `test_supervisor.py` (retried up to 3 times, 10 s timeout) |
| "Why did you cite KB-006?" answered without the article (and the citation disowned) | `test_supervisor.py` (the agent reads it) |
| A knowledge-base question with a colon ("Policy question: …") failing the whole search | `test_graph_rag_chain.py` (query syntax escaped) |
| A follow-up ("and after the first year?") searched and matched as typed, without the question before it | `test_supervisor.py`, `test_graph_rag_chain.py` (every step reads the rewrite) |
| A Jev outage counted as wrong answers in the offline eval | `eval/test_offline_walks.py` (errored runs ask for a rerun) |
