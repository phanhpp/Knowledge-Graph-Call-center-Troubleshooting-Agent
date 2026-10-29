"""Runs the offline evaluation: sends each golden example through the real agent and scores the reply.

    uv run python -m eval.offline.run --suite walks --tag baseline
    uv run python -m eval.offline.run --suite all --gate     # fail if a score is below thresholds.yaml

For each suite (--suite all runs them in turn):

                 ┌─────────────────────────────────────────┐
                 │      datasets/<suite>.jsonl             │
                 │   (the golden examples)                 │
                 └────────────────────┬────────────────────┘
                                      │
                                      ▼
                 ┌─────────────────────────────────────────┐
                 │          sync_dataset()                 │
                 │ Syncs examples with LangSmith Dataset   │
                 └────────────────────┬────────────────────┘
                                      │
                                      ▼
                 ┌─────────────────────────────────────────┐
                 │             run_suite()                 │
                 │   Calls target.py (the agent), scored   │
                 │   by code checks and Jev judges         │
                 └────────────────────┬────────────────────┘
                                      │
                                      ▼
                 ┌─────────────────────────────────────────┐
                 │           Save Local Summary            │
                 │  Writes eval/offline/results/*.json     │
                 └────────────────────┬────────────────────┘
                                      │
                                      ▼
                 ┌─────────────────────────────────────────┐
                 │            print_summary()              │
                 │    Prints results table to console      │
                 └────────────────────┬────────────────────┘
                                      │
                      ┌───────────────┴───────────────┐
                      │ Is --gate flag passed?        │
                      └───────┬───────────────┬───────┘
                              │               │
                     NO       │               │ YES
                     ▼                        ▼
              ┌──────────────┐      ┌──────────────────┐
              │ Exit Cleanly │      │   gate() Check   │
              └──────────────┘      └────────┬─────────┘
                                             │
                                ┌────────────┴────────────┐
                                ▼                         ▼
                        [ Scores Pass ]           [ Below thresholds.yaml,
                                │                   or a run errored ]
                                ▼                         ▼
                         ✅ Exit Code 0            ❌ Exit Code 1

  - sync_dataset: an example keeps the same LangSmith ID every time (made from its golden ID), so
    editing it in the file updates it in LangSmith, and deleting it from the file deletes it there too.
  - run_suite: the code checks are in evaluators/code.py, the Jev judges in evaluators/jev.py. The
    LangSmith experiment is named `<suite>-<tag>`: open two side by side to compare versions. The
    summary counts how many examples passed each check, which failed and where they went instead,
    how each slice did (e.g. routing's "typo" examples), and the latency.
  - gate: each check against its pass mark in thresholds.yaml. CI runs the same, with --gate.

If a run crashes (e.g. a model API returns 529 Overloaded), there is no reply to score; if a judge
crashes, there is no score. Both are listed as "errored", not counted as wrong answers: an outage is
not a quality drop. The gate still fails and asks you to rerun, since part of the suite wasn't measured.

A score is True/False or a number from 0 to 1. It counts as passed at 1 (True, or perfect), and the
mean is kept too, for scores like MRR. A score of None means the check doesn't apply to that example
(e.g. recall on a question the articles can't answer) and is left out.

Needs what the agent needs: Neo4j with the graph and articles loaded, and the model keys. It makes
real model calls, so it costs money. Experiments are traced to their own LangSmith projects, never
to `kg`, so the online evaluators don't score them.
"""

import argparse
import json
import statistics
import subprocess
import sys
import uuid
from collections import defaultdict
from pathlib import Path

import yaml

import config  # noqa: F401  (loads .env)
from eval.offline.evaluators.code import SUITES as CODE_EVALUATORS
from eval.offline.evaluators.jev import JUDGES
from eval.offline.target import target

HERE = Path(__file__).resolve().parent
DATASETS = HERE / "datasets"
RESULTS = HERE / "results"
ALL_SUITES = ["routing", "walks", "kb", "conversations", "guards", "adversarial"]


def load_examples(suite: str) -> list[dict]:
    """The golden examples of one suite, read from datasets/<suite>.jsonl."""
    path = DATASETS / f"{suite}.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def langsmith_id(suite: str, golden_id: str) -> str:
    """A fixed LangSmith ID for a golden example: the same golden ID always gives the same UUID."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"super-agent-offline/{suite}/{golden_id}"))


def sync_dataset(client, suite: str, examples: list[dict]) -> str:
    """Make the LangSmith dataset match the JSONL file: add new examples, update changed ones,
    delete removed ones. Returns the dataset's name."""
    name = f"super-agent-offline-{suite}"
    if client.has_dataset(dataset_name=name):
        dataset = client.read_dataset(dataset_name=name)
    else:
        dataset = client.create_dataset(name, description=f"Super Agent offline eval: the {suite} suite (eval/offline/datasets/{suite}.jsonl)")
    existing = {str(e.id) for e in client.list_examples(dataset_id=dataset.id)}
    rows = [
        {"id": langsmith_id(suite, e["id"]), "inputs": e["inputs"], "outputs": e["outputs"], "metadata": {**e.get("metadata", {}), "golden_id": e["id"]}}
        for e in examples
    ]
    new = [r for r in rows if r["id"] not in existing]
    changed = [r for r in rows if r["id"] in existing]
    gone = existing - {r["id"] for r in rows}
    if new:
        client.create_examples(dataset_id=dataset.id, examples=new)
    if changed:
        client.update_examples(dataset_id=dataset.id, updates=changed)
    if gone:
        client.delete_examples(example_ids=list(gone))
    print(f"dataset {name}: {len(new)} added, {len(changed)} updated, {len(gone)} removed")
    return name


def git_sha() -> str:
    """The current commit, saved with each experiment so a score can be traced back to the code."""
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    except Exception:
        return "unknown"


def went(outputs: dict, expected: dict) -> str:
    """Where a failing example went, next to where it should have gone, e.g.
    "expected troubleshooting / PROC-018, got troubleshooting / PROC-020"."""
    def place(path, procedure_id):
        return f"{path} / {procedure_id or 'no procedure'}"
    return (f"expected {place(expected.get('path'), expected.get('procedure_id'))}, "
            f"got {place(outputs.get('intent'), outputs.get('matched_procedure_id'))}")


def run_suite(suite: str, tag: str, repetitions: int, concurrency: int) -> dict:
    """Run one suite as a LangSmith experiment. Returns its summary: how many passed each check,
    which examples failed, which errored, and latency."""
    from langsmith import Client, evaluate

    client = Client()
    examples = load_examples(suite)
    dataset = sync_dataset(client, suite, examples)
    evaluators = CODE_EVALUATORS.get(suite, []) + JUDGES.get(suite, [])
    results = evaluate(
        target,
        data=dataset,
        evaluators=evaluators,
        experiment_prefix=f"{suite}-{tag}",
        description=f"Super Agent offline eval, {suite} suite, tag {tag}",
        max_concurrency=concurrency,
        num_repetitions=repetitions,
        metadata={"suite": suite, "tag": tag, "git_sha": git_sha()},
        client=client,
    )

    passed: dict[str, int] = defaultdict(int)
    total: dict[str, int] = defaultdict(int)
    score_sum: dict[str, float] = defaultdict(float)
    fractional: set[str] = set()  # keys with 0-1 scores (e.g. mrr), whose mean is worth showing
    failures: dict[str, list[str]] = defaultdict(list)
    errored: dict[str, str] = {}
    missed: dict[str, str] = {}  # golden id -> the checks it failed (and where it went, for routing)
    slices: dict[str, list[int]] = defaultdict(lambda: [0, 0])  # slice -> [passed every check, runs]
    latencies = []
    for row in results:
        golden_id = (row["example"].metadata or {}).get("golden_id", str(row["example"].id))
        run = row["run"]
        if run.error:
            errored[golden_id] = run.error.strip().splitlines()[-1][:200]
            continue
        if run.end_time and run.start_time:
            latencies.append((run.end_time - run.start_time).total_seconds())
        failed_keys = []
        for result in row["evaluation_results"]["results"]:
            if (result.extra or {}).get("error"):
                errored[golden_id] = f"{result.key}: {result.comment}"[:200]
                continue
            if result.score is None:
                continue  # the check doesn't apply to this example
            total[result.key] += 1
            score_sum[result.key] += float(result.score)
            if isinstance(result.score, float):
                fractional.add(result.key)
            if result.score >= 1:
                passed[result.key] += 1
            else:
                failures[result.key].append(golden_id)
                failed_keys.append(result.key)
        passed_all = not failed_keys
        if failed_keys:
            where = ""
            if {"path_correct", "procedure_correct"} & set(failed_keys):
                where = f" ({went(run.outputs or {}, row['example'].outputs or {})})"
            missed[golden_id] = ", ".join(failed_keys) + where
        slice_name = (row["example"].metadata or {}).get("slice")
        if slice_name:
            slices[slice_name][0] += passed_all
            slices[slice_name][1] += 1

    summary = {
        "suite": suite,
        "tag": tag,
        "experiment": results.experiment_name,
        "examples": len(examples),
        "repetitions": repetitions,
        "checks": {
            key: {"passed": passed[key], "total": total[key], "mean": round(score_sum[key] / total[key], 3)}
            | ({"fractional": True} if key in fractional else {})
            for key in total
        },
        "failures": {key: sorted(set(ids)) for key, ids in failures.items()},
        "errored": dict(sorted(errored.items())),
        "missed": dict(sorted(missed.items())),
        "slices": {name: {"passed": counts[0], "total": counts[1]} for name, counts in slices.items()},
        "latency_s": {
            "p50": round(statistics.median(latencies), 2) if latencies else None,
            "p95": round(sorted(latencies)[max(0, int(len(latencies) * 0.95) - 1)], 2) if latencies else None,
        },
    }
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / f"{suite}-{tag}.json").write_text(json.dumps(summary, indent=2))
    return summary


def print_summary(summary: dict) -> None:
    """Print a suite's summary as a small table."""
    print(f"\n{summary['suite']}: experiment {summary['experiment']} ({summary['examples']} examples x {summary['repetitions']})")
    for key, counts in summary["checks"].items():
        failing = summary["failures"].get(key, [])
        mean = f"  mean {counts['mean']:.2f}" if counts.get("fractional") else ""
        print(f"  {key:24} {counts['passed']:>3}/{counts['total']:<3}{mean}{'  failing: ' + ', '.join(failing) if failing else ''}")
    for golden_id, where in summary.get("missed", {}).items():
        print(f"  missed                   {golden_id}: {where}")
    if summary.get("slices"):
        print("  by slice (all checks)    " + ", ".join(f"{name} {c['passed']}/{c['total']}" for name, c in summary["slices"].items()))
    for golden_id, error in summary.get("errored", {}).items():
        print(f"  errored (not scored)     {golden_id}: {error}")
    print(f"  latency p50 {summary['latency_s']['p50']}s, p95 {summary['latency_s']['p95']}s")


def gate(summary: dict, thresholds: dict) -> list[str]:
    """What fails the gate: errored runs, and checks below their threshold. A threshold is either:
      - a count: how many examples must pass, out of those the check applies to (e.g. 19 of 20).
        With --repetitions it applies as a rate: 57 of 60 passes a threshold of 19 of 20;
      - {"mean": x}: the average score must reach x (for 0-1 scores like MRR)."""
    problems = []
    if summary.get("errored"):
        problems.append(f"{summary['suite']}: {len(summary['errored'])} run(s) errored, not measured: rerun")
    for key, threshold in (thresholds.get(summary["suite"]) or {}).items():
        counts = summary["checks"].get(key)
        if counts is None:
            problems.append(f"{summary['suite']}.{key}: not measured")
            continue
        if isinstance(threshold, dict):
            if counts["mean"] < threshold["mean"]:
                problems.append(f"{summary['suite']}.{key}: mean {counts['mean']:.2f}, needs {threshold['mean']}")
            continue
        applicable = round(counts["total"] / summary.get("repetitions", 1))  # examples the check applies to
        rate = counts["passed"] / counts["total"] if counts["total"] else 0
        if rate < threshold / applicable:
            problems.append(f"{summary['suite']}.{key}: {counts['passed']}/{counts['total']} passed, needs {threshold}/{applicable}")
    return problems


def main() -> None:
    """Read the command line, run the suites, print their summaries and apply the gate."""
    parser = argparse.ArgumentParser(description="Offline end-to-end evaluation with golden datasets")
    parser.add_argument("--suite", default="all", help=f"one of {', '.join(ALL_SUITES)}, or all")
    parser.add_argument("--tag", default="dev", help="names the experiment: <suite>-<tag>")
    parser.add_argument("--repetitions", type=int, default=1, help="run each example N times (for model variance)")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--gate", action="store_true", help="exit 1 if any check is below eval/offline/thresholds.yaml")
    args = parser.parse_args()

    suites = [s for s in ALL_SUITES if (DATASETS / f"{s}.jsonl").exists()] if args.suite == "all" else [args.suite]
    thresholds = yaml.safe_load((HERE / "thresholds.yaml").read_text()) or {}
    problems = []
    for suite in suites:
        summary = run_suite(suite, args.tag, args.repetitions, args.concurrency)
        print_summary(summary)
        problems += gate(summary, thresholds)
    if args.gate:
        print("\ngate:", "passed" if not problems else "FAILED")
        for problem in problems:
            print("  " + problem)
        sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
