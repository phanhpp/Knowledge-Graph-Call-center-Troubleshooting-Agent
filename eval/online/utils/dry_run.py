"""Dry run of the online code checks on real conversations, before (or after) setting them up.

Fetches recent real conversations from LangSmith (read-only) with the same filter the online
evaluators use (root runs, not errored), runs each check in eval/online/ on them
locally, and prints what each would score. Nothing is sent back: no scores are written, no
evaluator is created, no trace's retention changes. It's LangSmith's "Test Code" button, on many
runs at once.

    uv run python eval/online/utils/dry_run.py
    uv run python eval/online/utils/dry_run.py --since 2026-10-12 --limit 300

Any run that scores 0 is listed: open it in LangSmith to see whether it's a real bug or a mistake
in the check.
"""

import argparse
import asyncio
import collections
import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path

CHECKS_DIR = Path(__file__).resolve().parents[1]  # eval/online/, where the check files are
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))  # the project root, so `config` (which loads .env) imports

import config  # noqa: E402,F401  (loads LANGSMITH_API_KEY from .env)
from langsmith import Client  # noqa: E402

CHECKS = ["citations_exist", "walk_has_checklist", "troubleshooting_customer_handled"]
PROJECT = "kg"


def load(name: str):
    """Load one check file on its own, as LangSmith runs it, and return its perform_eval."""
    spec = importlib.util.spec_from_file_location(name, CHECKS_DIR / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.perform_eval


async def fetch(since: datetime, limit: int) -> list:
    """The conversations the online evaluators would score: root runs, not errored.

    Uses client.runs.query (list_runs is deprecated): it is async, takes the project's ID rather
    than its name, and returns only the fields listed in `selects`.
    """
    client = Client()
    project_id = str(client.read_project(project_name=PROJECT).id)
    pages = client.runs.query(
        project_ids=[project_id],
        is_root=True,
        has_error=False,
        min_start_time=since,
        page_size=min(limit, 1000),
        selects=["ID", "OUTPUTS"],
    )
    runs = []
    async for run in pages:
        runs.append(run)
        if len(runs) >= limit:
            break
    return runs


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--since", default="2026-10-09", help="first day to fetch (YYYY-MM-DD); the current format started 2026-10-09")
    parser.add_argument("--limit", type=int, default=100, help="how many recent conversations to fetch")
    args = parser.parse_args()

    since = datetime.fromisoformat(args.since).replace(tzinfo=timezone.utc)
    runs = asyncio.run(fetch(since, args.limit))
    print(f"{len(runs)} real conversations in project {PROJECT!r} since {since.date()} (root runs, not errored)\n")

    for name in CHECKS:
        check = load(name)
        tally = collections.Counter()
        zeros = []
        for run in runs:
            result = check({"outputs": getattr(run, "outputs", None)})  # the same shape LangSmith passes in
            score = next(iter(result.values()), None)
            tally["not applicable" if score is None else f"score {score}"] += 1
            if score == 0:
                zeros.append(run.id)
        print(f"{name}: {dict(tally)}")
        for run_id in zeros:
            print(f"  scored 0: run {run_id}")


if __name__ == "__main__":
    main()
