"""Run the offline evaluation: golden examples end to end through the agent, as LangSmith experiments.

    uv run python -m eval.offline.run --suite walks --tag baseline
    uv run python -m eval.offline.run --suite all --gate            # fail if below eval/offline/thresholds.yaml

For each suite it:
  1. syncs datasets/<suite>.jsonl to the LangSmith dataset `super-agent-offline-<suite>` (an example's
     LangSmith id is derived from its golden id, so an edited example updates in place, and examples
     removed from the file are removed from the dataset);
  2. runs every example through the real graph (target.py) and scores it (evaluators/), as an
     experiment named `<suite>-<tag>`: compare experiments side by side in LangSmith;
  3. prints how many examples passed each check, the failing ones, and latency; writes
     results/<suite>-<tag>.json; with --gate, exits non-zero if a check is below its threshold.

Needs Neo4j (graph and articles loaded) and the model keys, like the agent; costs real model calls.
Experiments trace to their own projects, never to `kg`, so the online evaluators don't score them.
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
from eval.offline.target import target

HERE = Path(__file__).resolve().parent
DATASETS = HERE / "datasets"
RESULTS = HERE / "results"
ALL_SUITES = ["routing", "walks", "kb", "conversations", "guards", "adversarial"]


def load_examples(suite: str) -> list[dict]:
    path = DATASETS / f"{suite}.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def langsmith_id(suite: str, golden_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"super-agent-offline/{suite}/{golden_id}"))


def sync_dataset(client, suite: str, examples: list[dict]) -> str:
    """Make the LangSmith dataset match the JSONL file; returns the dataset name."""
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
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    except Exception:
        return "unknown"


def run_suite(suite: str, tag: str, repetitions: int, concurrency: int) -> dict:
    from langsmith import Client, evaluate

    client = Client()
    examples = load_examples(suite)
    dataset = sync_dataset(client, suite, examples)
    evaluators = CODE_EVALUATORS.get(suite, [])
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
    failures: dict[str, list[str]] = defaultdict(list)
    latencies = []
    for row in results:
        golden_id = (row["example"].metadata or {}).get("golden_id", str(row["example"].id))
        run = row["run"]
        if run.end_time and run.start_time:
            latencies.append((run.end_time - run.start_time).total_seconds())
        for result in row["evaluation_results"]["results"]:
            total[result.key] += 1
            if result.score:
                passed[result.key] += 1
            else:
                failures[result.key].append(golden_id)

    summary = {
        "suite": suite,
        "tag": tag,
        "experiment": results.experiment_name,
        "examples": len(examples),
        "repetitions": repetitions,
        "checks": {key: {"passed": passed[key], "total": total[key]} for key in total},
        "failures": {key: sorted(set(ids)) for key, ids in failures.items()},
        "latency_s": {
            "p50": round(statistics.median(latencies), 2) if latencies else None,
            "p95": round(sorted(latencies)[max(0, int(len(latencies) * 0.95) - 1)], 2) if latencies else None,
        },
    }
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / f"{suite}-{tag}.json").write_text(json.dumps(summary, indent=2))
    return summary


def print_summary(summary: dict) -> None:
    print(f"\n{summary['suite']}: experiment {summary['experiment']} ({summary['examples']} examples x {summary['repetitions']})")
    for key, counts in summary["checks"].items():
        failing = summary["failures"].get(key, [])
        print(f"  {key:24} {counts['passed']:>3}/{counts['total']:<3}{'  failing: ' + ', '.join(failing) if failing else ''}")
    print(f"  latency p50 {summary['latency_s']['p50']}s, p95 {summary['latency_s']['p95']}s")


def gate(summary: dict, thresholds: dict) -> list[str]:
    """Checks below their threshold. A threshold is the minimum examples passing, out of the suite's
    examples; with repetitions it applies as a rate."""
    problems = []
    for key, minimum in (thresholds.get(summary["suite"]) or {}).items():
        counts = summary["checks"].get(key)
        if counts is None:
            problems.append(f"{summary['suite']}.{key}: not measured")
            continue
        required_rate = minimum / summary["examples"]
        rate = counts["passed"] / counts["total"] if counts["total"] else 0
        if rate < required_rate:
            problems.append(f"{summary['suite']}.{key}: {counts['passed']}/{counts['total']} passed, needs {minimum}/{summary['examples']}")
    return problems


def main() -> None:
    parser = argparse.ArgumentParser(description="Offline end-to-end evaluation (eval/offline_eval_plan.md)")
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
