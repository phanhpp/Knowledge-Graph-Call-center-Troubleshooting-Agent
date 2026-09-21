"""Live-graph audit — diffs Neo4j's actual Step relationships against
procedures.json (the source of truth), to catch drift MERGE-based loading
can silently leave behind.

Run as: uv run python -m etl.audit_graph

This is the check that would have caught the real bug this project hit:
editing a step's condition_id in procedures.json and reloading left the
*old* HAS_CONDITION edge dangling alongside the new one, since MERGE only
adds relationships, never retracts them. etl/load_to_neo4j.py's loader now
deletes stale edges before recreating them (see its load_steps() docstring),
but this script exists so that guarantee is verified, not just assumed —
and so a future change to the loader that reintroduces the bug gets caught
here instead of by luck.

Deliberately three separate queries, not one combined query with three
OPTIONAL MATCHes off the same Step — combining relationship types in one
query creates a Cartesian product between them, which would inflate/corrupt
exactly the duplicate-edge counts this audit exists to catch correctly.
"""

import sys

from config import get_driver, get_neo4j_config
from etl.io_utils import load_json

CONDITIONS_CYPHER = """
MATCH (s:Step)
OPTIONAL MATCH (s)-[:HAS_CONDITION]->(c:Condition)
RETURN s.procedureId AS procedureId, s.stepId AS stepId, collect(c.conditionId) AS conditionIds
"""

ACTIONS_CYPHER = """
MATCH (s:Step)
OPTIONAL MATCH (s)-[ha:HAS_ACTION]->(a:Action)
RETURN s.procedureId AS procedureId, s.stepId AS stepId,
       collect({outcome: ha.outcome, actionId: a.actionId}) AS actions
"""

NEXT_STEP_CYPHER = """
MATCH (s:Step)
OPTIONAL MATCH (s)-[:NEXT_STEP]->(nxt:Step)
RETURN s.procedureId AS procedureId, s.stepId AS stepId, collect(nxt.stepId) AS nextStepIds
"""


def _expected_steps(procedures: list[dict]) -> dict[tuple[str, int], dict]:
    """Index procedures.json by (procedure_id, step_id) for O(1) lookup."""
    expected = {}
    for p in procedures:
        for s in p["steps"]:
            expected[(p["procedure_id"], s["step_id"])] = s
    return expected


def audit_has_condition(session, expected: dict) -> list[str]:
    """Every Step should have exactly one HAS_CONDITION edge, pointing at
    the condition_id procedures.json currently specifies."""
    errors = []
    for record in session.run(CONDITIONS_CYPHER):
        key = (record["procedureId"], record["stepId"])
        step = expected.get(key)
        if step is None:
            continue  # a Step not in current procedures.json — not this audit's job
        actual = [c for c in record["conditionIds"] if c is not None]
        want = [step["condition_id"]]
        if actual != want:
            errors.append(f"{key[0]} step {key[1]}: HAS_CONDITION -> {actual}, expected {want}")
    return errors


def audit_has_action(session, expected: dict) -> list[str]:
    """Every Step should have exactly one outcome:'true' HAS_ACTION edge,
    plus exactly one outcome:'false' edge iff terminal (none otherwise)."""
    errors = []
    for record in session.run(ACTIONS_CYPHER):
        key = (record["procedureId"], record["stepId"])
        step = expected.get(key)
        if step is None:
            continue
        actual_true = sorted(a["actionId"] for a in record["actions"] if a["outcome"] == "true")
        actual_false = sorted(a["actionId"] for a in record["actions"] if a["outcome"] == "false")
        want_true = [step["action_if_true_id"]]
        want_false = [step["action_if_false_id"]] if step.get("action_if_false_id") else []
        if actual_true != want_true:
            errors.append(f"{key[0]} step {key[1]}: HAS_ACTION(true) -> {actual_true}, expected {want_true}")
        if actual_false != want_false:
            errors.append(f"{key[0]} step {key[1]}: HAS_ACTION(false) -> {actual_false}, expected {want_false}")
    return errors


def audit_next_step(session, expected: dict) -> list[str]:
    """Non-terminal steps should have exactly one outgoing NEXT_STEP edge
    matching next_step; terminal steps should have zero."""
    errors = []
    for record in session.run(NEXT_STEP_CYPHER):
        key = (record["procedureId"], record["stepId"])
        step = expected.get(key)
        if step is None:
            continue
        actual = [n for n in record["nextStepIds"] if n is not None]
        want = [step["next_step"]] if step.get("next_step") is not None else []
        if actual != want:
            errors.append(f"{key[0]} step {key[1]}: NEXT_STEP -> {actual}, expected {want}")
    return errors


def main() -> int:
    """Diff live Neo4j against procedures.json; print PASS/FAIL, exit non-zero on drift."""
    cfg = get_neo4j_config()
    driver = get_driver()
    try:
        driver.verify_connectivity()
    except Exception as e:
        print(f"Could not connect to Neo4j at {cfg.uri} ({e})")
        return 1

    procedures = load_json("procedures.json")
    expected = _expected_steps(procedures)

    with driver.session(database=cfg.database) as session:
        results = [
            ("HAS_CONDITION matches procedures.json", audit_has_condition(session, expected)),
            ("HAS_ACTION matches procedures.json", audit_has_action(session, expected)),
            ("NEXT_STEP matches procedures.json", audit_next_step(session, expected)),
        ]

    print("=== etl.audit_graph results ===")
    any_failed = False
    for name, errors in results:
        if errors:
            any_failed = True
            print(f"[FAIL] {name} ({len(errors)} issue(s))")
            for e in errors:
                print(f"    - {e}")
        else:
            print(f"[PASS] {name}")

    if any_failed:
        print("\nGraph drift detected — re-run etl.load_to_neo4j, or the loader has a bug.")
        return 1
    print("\nLive graph matches procedures.json exactly.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
