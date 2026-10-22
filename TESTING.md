# Testing

How Super Agent is tested, layer by layer: what each suite covers, what it needs, and how to run it. The README has the short version.

| Layer | Tool | Where | Tests | Needs |
|---|---|---|---|---|
| Agent, data, Slack bot | pytest | `tests/` | 231 (206 fast + 25 end-to-end) | Neo4j with the graph loaded; end-to-end also Claude, Jev and OpenAI keys |
| Web app logic | Vitest | `web/lib/*.test.ts` | 40 | nothing (no browser, no server) |
| Web app in a browser | Playwright | `web/e2e/` | 23 | nothing: a fake agent replays recorded traffic |
| A running server | smoke script | `deploy/smoke.py` | 15 checks | a local or deployed agent server (real model calls) |
| Every pull request | GitHub Actions | `.github/workflows/ci.yml` | 2 jobs | runs in CI, no secrets; required before merging to `main` |
| Data and graph | GitHub Actions | `.github/workflows/data-integrity.yml` | 1 job | runs in CI with a throwaway Neo4j, on data pull requests |
| Production traffic | LangSmith online evaluators | `eval/` | 3 code checks + 7 Jev judges | runs in LangSmith on every real conversation |

## Running them

```bash
uv run pytest -m "not e2e"        # fast suite: unit and integration tests (Neo4j with the graph loaded)
uv run pytest                     # everything, including the 25 end-to-end tests (real model calls)
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

| File | Tests | Covers |
|---|---|---|
| `test_validate.py` | 7 | `etl.validate`: IDs and fields match across the data files; every procedure can finish (no cycles, all steps reachable, a fallback action on the last step) |
| `test_condition_evaluator.py` | 21 | field and chain conditions checked against the mock CRM and lookup APIs |
| `test_procedural_executor.py` | 16 | the troubleshooting walk: fetching steps, advancing or resolving, the reply format ("Why:" and the checked steps), ticket conditions read by Claude. Pins the infinite-loop bug fixed by routing with a `Command` |
| `test_supervisor.py` | 40 | the main graph: customer checks, the Neo4j check, routing with a fake Jev, `load_crm`, and `explain`: the history labels, a label copied by Claude removed, never claiming an earlier answer was wrong |
| `test_graph_rag_chain.py` | 3 | the knowledge-base path: hybrid search, graph grounding, the answer and its citations (needs keys) |
| `test_sources.py` | 12 | the text behind each cited ID (short on replies, full on request), and `reply()`: citations unchanged, sources added, the run id is the turn's **root** run |
| `test_http_app.py` | 11 | the custom routes: `/customers` and `/sources` (200 and 404), `/feedback` (rejects a bad run id or score; newest vote wins) with a fake LangSmith client |
| `test_config.py` | 3 | the Neo4j check: a down Neo4j gives a reason and is recorded as an **errored traced step**, so alerts see it |
| `eval/test_online_code_evaluators.py` | 17 | the online code evaluators, loaded by path as LangSmith runs them: good, bad and empty runs; their copies of the valid IDs and the error text still match the app; one test on the real graph's output |
| `slack_bot/` (8 files) | 76 | message parsing, Slack formatting, access control (members channel, fails closed), conversation threads, saved DM conversations, the `/agent` and DM handlers, Bolt wiring. All with fake Slack events: no network |
| `e2e/troubleshooting/` | 6 | the full graph with real models: field, chain and ticket conditions |
| `e2e/open_ended/` | 3 | knowledge-base questions, including the fee question an earlier classifier misrouted |
| `e2e/follow_ups/` | 11 | `explain`, multi-customer conversations, conversations saved and resumed |
| `e2e/errors/` | 5 | unknown customer IDs stop before any paid model call |

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
| `agent` | an **import check with only the deployed dependencies** (`uv sync --no-dev`), proving the image LangSmith builds is complete; then the graph is validated and loaded into a throwaway Neo4j 5.26 and the fast pytest suite runs on **Python 3.12**, the deployment's version: 200 pass, 6 skip because they need API keys |

**`data-integrity.yml`, the data check**, on pull requests that touch `data/`, `etl/` or `mock_crm/` (or the workflow itself):

1. `etl.validate`: the JSON files are consistent and every procedure can finish.
2. Load the **base branch's** data into a throwaway Neo4j, then this pull request's data **on top**: the way the real graph gets updated.
3. `etl.audit_graph`: every step's `HAS_CONDITION`, `HAS_ACTION` and `NEXT_STEP` edges match `procedures.json` exactly.

Loading twice matters: a fresh, empty database can't have stale edges, so a single load would pass the audit whatever the loader does. Loading over existing data is what exercises the loader's stale-edge cleanup (`MERGE` alone only ever adds edges).

## In production

Testing doesn't stop at deployment. Every real conversation is scored in LangSmith: 3 code checks (citations exist, walk replies show their checklist, the customer rule held) and 7 Jev judges (made-up facts, contradictions, usefulness, retrieval relevance and knowledge gaps), with alerts to Slack and consultants' 👍 👎 on each answer. See `eval/eval_plan.md`.

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
