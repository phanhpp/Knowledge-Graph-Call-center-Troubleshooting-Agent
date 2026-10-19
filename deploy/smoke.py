"""Smoke test for a running LangGraph Server: local (`langgraph dev`) or the LangSmith deployment.

    uv run python deploy/smoke.py                                  # http://127.0.0.1:2024
    uv run python deploy/smoke.py --url https://<id>.us.langgraph.app   # sends LANGSMITH_API_KEY

One real conversation, so it calls Jev and Claude, and it writes one 👍 to LangSmith:
  1. troubleshooting with a customer: the reply has citations, sources and a run id, and the
     customer ID survived the trip over HTTP;
  2. "why?" on the same thread: explain sees the history, and cites nothing;
  3. the custom routes: GET /customers/{id}, POST /feedback/{thread}, GET /feedback/{thread}/votes.
Prints PASS/FAIL per check and exits non-zero if any failed.
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config  # noqa: E402,F401  (loads .env: LANGSMITH_API_KEY)
from langgraph_sdk import get_client  # noqa: E402

ASSISTANT = "super_agent"
CUSTOMER = "CUST-1001"
failures = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name}{f'  ({detail})' if detail else ''}")
    if not ok:
        failures.append(name)


def human(text: str, customer_id: str | None = None) -> dict:
    """A consultant message as the web app sends it (agent/utils.py consultant_message, over HTTP)."""
    return {"type": "human", "content": text, "additional_kwargs": {"customer_id": customer_id} if customer_id else {}}


async def main(url: str) -> None:
    local = "127.0.0.1" in url or "localhost" in url
    api_key = None if local else os.environ.get("LANGSMITH_API_KEY")
    client = get_client(url=url, api_key=api_key)
    thread = await client.threads.create()
    thread_id = thread["thread_id"]
    print(f"server {url}, thread {thread_id}\n")

    # 1. A walk for a named customer.
    state = await client.runs.wait(thread_id, ASSISTANT, input={"messages": [human("customer cannot activate roaming", CUSTOMER)]})
    sent, answer = state["messages"][-2], state["messages"][-1]
    kwargs = answer.get("additional_kwargs", {})
    check("customer ID survived HTTP", sent.get("additional_kwargs", {}).get("customer_id") == CUSTOMER)
    check("current customer set", state.get("customer_id") == CUSTOMER, str(state.get("customer_id")))
    check("walk reply cites something", bool(kwargs.get("citations")), str(kwargs.get("citations")))
    check("sources match citations", [s["id"] for s in kwargs.get("sources", [])] == kwargs.get("citations"))
    check("reply carries a run id", bool(kwargs.get("run_id")), str(kwargs.get("run_id")))
    print(f"      reply: {answer['content'].splitlines()[0]}")

    # 2. A follow-up on the same thread: explain reads the saved conversation.
    state = await client.runs.wait(thread_id, ASSISTANT, input={"messages": [human("why did it end up at Tier 2?")]})
    follow = state["messages"][-1]
    check("follow-up answered from history", state.get("intent") == "explain", str(state.get("intent")))
    check("explanation cites nothing", follow.get("additional_kwargs", {}).get("citations") == [])
    print(f"      reply: {follow['content'][:100]}…")

    # 3. The custom routes (agent/http_app.py).
    headers = {"X-Api-Key": api_key} if api_key else {}
    async with httpx.AsyncClient(base_url=url.rstrip("/"), headers=headers, timeout=30) as http:
        customer = await http.get(f"/customers/{CUSTOMER}")
        check("GET /customers/{id}", customer.status_code == 200 and customer.json().get("customer_id") == CUSTOMER)
        missing = await http.get("/customers/CUST-9999")
        check("GET /customers/{unknown} is 404", missing.status_code == 404)

        run_id = kwargs.get("run_id")
        if run_id:
            vote = await http.post(f"/feedback/{thread_id}", json={"run_id": run_id, "score": 1, "message_id": answer.get("id")})
            check("POST /feedback", vote.status_code == 200, vote.text[:120])
            votes = await http.get(f"/feedback/{thread_id}/votes", params={"run_ids": run_id})
            check("GET /feedback/votes reads it back", votes.json().get("votes", {}).get(run_id, {}).get("score") == 1, votes.text[:120])
            print(f"      👍 written on run {run_id}: open it in LangSmith to see `user_score`")

    print(f"\n{'all checks passed' if not failures else f'{len(failures)} failed: ' + ', '.join(failures)}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default="http://127.0.0.1:2024")
    asyncio.run(main(parser.parse_args().url))
