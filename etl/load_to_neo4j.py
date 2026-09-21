"""Phase 2 ETL — load validated Phase 1 JSON data into Neo4j.

Run as: uv run python -m etl.load_to_neo4j

Flow (main()):
    1. Connect and verify_connectivity() against the live Neo4j instance.
    2. Load conditions/actions/rules/procedures JSON from data/ (assumes
       etl.validate has already passed — this script does not re-validate).
    3. Inside one session, run the load in dependency order so every MATCH/
       MERGE only ever touches nodes that already exist:
         create_constraints()  -> uniqueness/NODE KEY constraints first
         load_conditions()     -> Condition nodes (properties only), then
                                  ordered VALIDATED_BY->API edges (1 edge for
                                  a plain verification_api, N ordered edges
                                  for a verification_chain; 0 for ticket_text)
         load_actions()        -> Action nodes
         load_rules()          -> Rule nodes
         load_governed_by()    -> Action-GOVERNED_BY->Rule edges
         load_steps()          -> Step nodes + HAS_CONDITION/HAS_ACTION edges
         load_next_steps()     -> NEXT_STEP edges (2nd pass: needs every Step
                                  to already exist, since it MATCHes both ends)
         print_counts()        -> final sanity-check counts
    4. Close the driver.

Every *_CYPHER query uses `CALL { WITH row ... } IN TRANSACTIONS`, which is
only legal inside an implicit/auto-commit transaction — so every one of these
goes through session.run() directly, never session.execute_write()/
execute_read() or driver.execute_query() (those wrap the query in a managed
transaction and Neo4j rejects the construct).
"""

import sys

from config import get_driver, get_neo4j_config
from etl.io_utils import load_json

CONSTRAINTS = [
    # Composite UNIQUE, not NODE KEY: NODE KEY is Enterprise-only (bundles an
    # existence check we don't need — the loader always sets both properties
    # together, since they're the MERGE key itself). Composite UNIQUE alone
    # is Community-Edition-compatible, verified against the official Cypher
    # manual, and this is our only constraint that would otherwise force
    # Enterprise — removing it makes the whole schema portable to Community
    # or AuraDB Free.
    "CREATE CONSTRAINT step_key IF NOT EXISTS FOR (s:Step) REQUIRE (s.procedureId, s.stepId) IS UNIQUE",
    "CREATE CONSTRAINT condition_id IF NOT EXISTS FOR (c:Condition) REQUIRE c.conditionId IS UNIQUE",
    "CREATE CONSTRAINT action_id IF NOT EXISTS FOR (a:Action) REQUIRE a.actionId IS UNIQUE",
    "CREATE CONSTRAINT api_endpoint IF NOT EXISTS FOR (api:API) REQUIRE api.endpoint IS UNIQUE",
    "CREATE CONSTRAINT rule_id IF NOT EXISTS FOR (r:Rule) REQUIRE r.ruleId IS UNIQUE",
    "CREATE CONSTRAINT article_id IF NOT EXISTS FOR (a:Article) REQUIRE a.articleId IS UNIQUE",
]

LOAD_CONDITION_NODES_CYPHER = """
UNWIND $rows AS row
CALL { WITH row
  MERGE (c:Condition {conditionId: row.condition_id})
  SET c.field = row.field, c.operator = row.operator, c.value = row.value,
      c.description = row.description, c.kind = row.kind,
      c.interpretationQuestion = row.interpretation_question
} IN TRANSACTIONS OF 500 ROWS ON ERROR CONTINUE REPORT STATUS AS status
RETURN status.transactionId AS transactionId, status.committed AS committed, status.errorMessage AS errorMessage
"""

LOAD_CONDITION_APIS_CYPHER = """
UNWIND $rows AS row
CALL { WITH row
  MATCH (c:Condition {conditionId: row.condition_id})
  MERGE (api:API {endpoint: row.endpoint})
  MERGE (c)-[r:VALIDATED_BY]->(api)
  SET r.order = row.order
} IN TRANSACTIONS OF 500 ROWS ON ERROR CONTINUE REPORT STATUS AS status
RETURN status.transactionId AS transactionId, status.committed AS committed, status.errorMessage AS errorMessage
"""

LOAD_ACTIONS_CYPHER = """
UNWIND $rows AS row
CALL { WITH row
  MERGE (a:Action {actionId: row.action_id})
  SET a.text = row.text, a.category = row.category, a.isResolution = row.is_resolution
} IN TRANSACTIONS OF 500 ROWS ON ERROR CONTINUE REPORT STATUS AS status
RETURN status.transactionId AS transactionId, status.committed AS committed, status.errorMessage AS errorMessage
"""

LOAD_RULES_CYPHER = """
UNWIND $rows AS row
CALL { WITH row
  MERGE (r:Rule {ruleId: row.rule_id})
  SET r.description = row.description, r.constraintType = row.constraint_type, r.detail = row.detail
} IN TRANSACTIONS OF 200 ROWS ON ERROR CONTINUE REPORT STATUS AS status
RETURN status.transactionId AS transactionId, status.committed AS committed, status.errorMessage AS errorMessage
"""

LOAD_GOVERNED_BY_CYPHER = """
UNWIND $rows AS row
CALL { WITH row
  MERGE (r:Rule {ruleId: row.rule_id})
  MERGE (a:Action {actionId: row.action_id})
  MERGE (a)-[:GOVERNED_BY]->(r)
} IN TRANSACTIONS OF 500 ROWS ON ERROR CONTINUE REPORT STATUS AS status
RETURN status.transactionId AS transactionId, status.committed AS committed, status.errorMessage AS errorMessage
"""

LOAD_STEPS_CYPHER = """
UNWIND $rows AS row
CALL { WITH row
  MERGE (st:Step {procedureId: row.procedure_id, stepId: row.step_id})
  SET st.description = row.description
  WITH st, row
  OPTIONAL MATCH (st)-[oldCond:HAS_CONDITION]->(:Condition)
  DELETE oldCond
  WITH st, row
  OPTIONAL MATCH (st)-[oldAct:HAS_ACTION]->(:Action)
  DELETE oldAct
  WITH st, row
  MERGE (c:Condition {conditionId: row.condition_id})
  MERGE (st)-[:HAS_CONDITION]->(c)
  MERGE (atrue:Action {actionId: row.action_if_true_id})
  MERGE (st)-[:HAS_ACTION {outcome: 'true'}]->(atrue)
  FOREACH (_ IN CASE WHEN row.action_if_false_id IS NOT NULL THEN [1] ELSE [] END |
    MERGE (afalse:Action {actionId: row.action_if_false_id})
    MERGE (st)-[:HAS_ACTION {outcome: 'false'}]->(afalse)
  )
} IN TRANSACTIONS OF 200 ROWS ON ERROR CONTINUE REPORT STATUS AS status
RETURN status.transactionId AS transactionId, status.committed AS committed, status.errorMessage AS errorMessage
"""

LOAD_NEXT_STEP_CYPHER = """
UNWIND $rows AS row
CALL { WITH row
  MATCH (a:Step {procedureId: row.procedure_id, stepId: row.step_id})
  MATCH (b:Step {procedureId: row.procedure_id, stepId: row.next_step})
  OPTIONAL MATCH (a)-[oldNext:NEXT_STEP]->(:Step)
  DELETE oldNext
  WITH a, b
  MERGE (a)-[:NEXT_STEP]->(b)
} IN TRANSACTIONS OF 200 ROWS ON ERROR CONTINUE REPORT STATUS AS status
RETURN status.transactionId AS transactionId, status.committed AS committed, status.errorMessage AS errorMessage
"""


def create_constraints(session) -> None:
    """Create all uniqueness/NODE KEY constraints (idempotent, IF NOT EXISTS)."""
    for stmt in CONSTRAINTS:
        session.run(stmt).consume()
    print(f"Created/confirmed {len(CONSTRAINTS)} constraints")


def _run_batch(session, label: str, cypher: str, rows: list[dict]) -> None:
    """Run one UNWIND $rows batch query and report any batch that failed to commit.

    With ON ERROR CONTINUE, a partial failure does NOT raise in Python — the
    query still returns normally, just with status.committed=False rows — so
    this must be checked explicitly rather than relying on an exception.
    """
    if not rows:
        print(f"{label}: 0 rows, skipping")
        return
    result = session.run(cypher, rows=rows)
    records = list(result)
    failed = [r for r in records if r["committed"] is False]
    print(f"{label}: {len(rows)} rows -> {len(records)} batch(es), {len(failed)} failed batch(es)")
    for r in failed:
        print(f"    FAILED batch {r['transactionId']}: {r['errorMessage']}")


def load_conditions(session, conditions: list[dict]) -> None:
    """MERGE one Condition node per entry (properties only), then a separate
    pass for its ordered VALIDATED_BY->API edge(s).

    A plain `verification_api` condition becomes a 1-element chain (order 1);
    a `verification_chain` condition becomes N ordered edges; a `ticket_text`
    condition gets no API edges at all. API is MERGEd on `endpoint` in every
    case, so conditions that hit the same endpoint (e.g. two chains that both
    call the same network-status lookup) collapse onto one shared API node.
    """
    node_rows = [{
        "condition_id": c["condition_id"],
        "field": c.get("field"),
        "operator": c.get("operator"),
        "value": c.get("value"),
        "description": c.get("description"),
        "kind": c.get("kind") or ("chain_check" if "verification_chain" in c else "field_check"),
        "interpretation_question": c.get("interpretation_question"),
    } for c in conditions]
    _run_batch(session, "condition nodes", LOAD_CONDITION_NODES_CYPHER, node_rows)

    api_rows = []
    for c in conditions:
        if "verification_chain" in c:
            api_rows.extend(
                {"condition_id": c["condition_id"], "order": i, "endpoint": step["call"]}
                for i, step in enumerate(c["verification_chain"], start=1)
            )
        elif "verification_api" in c:
            api_rows.append({"condition_id": c["condition_id"], "order": 1, "endpoint": c["verification_api"]})
        # ticket_text conditions contribute no API rows
    _run_batch(session, "condition VALIDATED_BY->API edges", LOAD_CONDITION_APIS_CYPHER, api_rows)


def load_actions(session, actions: list[dict]) -> None:
    """MERGE one Action node per entry."""
    _run_batch(session, "actions", LOAD_ACTIONS_CYPHER, actions)


def load_rules(session, rules: list[dict]) -> None:
    """MERGE one Rule node per entry (properties only, no relationships)."""
    _run_batch(session, "rules", LOAD_RULES_CYPHER, rules)


def load_governed_by(session, rules: list[dict]) -> None:
    """Link each Rule to every Action it governs.

    Flattens each rule's applies_to_action_ids list into one
    (rule_id, action_id) row per pair first, since Cypher has no native way
    to UNWIND a nested array field.
    """
    rows = [
        {"rule_id": r["rule_id"], "action_id": aid}
        for r in rules
        for aid in r["applies_to_action_ids"]
    ]
    _run_batch(session, "GOVERNED_BY", LOAD_GOVERNED_BY_CYPHER, rows)


def load_steps(session, procedures: list[dict]) -> None:
    """MERGE one Step node per step across all procedures, plus its
    HAS_CONDITION edge and its HAS_ACTION edge(s).

    Flattens procedures.json's nested steps into one row per step first.
    action_if_false_id is None for every non-terminal step, so the Cypher's
    FOREACH only adds the {outcome:'false'} edge on terminal steps.

    Deletes any existing HAS_CONDITION/HAS_ACTION edge from the Step before
    recreating it from the current row — plain MERGE only adds edges, it
    never retracts one. Without this, editing a step's condition_id (or an
    action id) in procedures.json and reloading leaves the *old* edge
    dangling alongside the new one, so the step ends up pointing at two
    Conditions at once. Hit this for real while adding the chain/ticket_text
    conditions — see git history / tests/test_procedural_executor.py.
    """
    rows = []
    for p in procedures:
        for s in p["steps"]:
            rows.append({
                "procedure_id": p["procedure_id"],
                "step_id": s["step_id"],
                "description": s["description"],
                "condition_id": s["condition_id"],
                "action_if_true_id": s["action_if_true_id"],
                "action_if_false_id": s.get("action_if_false_id"),
            })
    _run_batch(session, "steps+HAS_CONDITION+HAS_ACTION", LOAD_STEPS_CYPHER, rows)


def load_next_steps(session, procedures: list[dict]) -> None:
    """Link consecutive Steps via NEXT_STEP.

    Must run after load_steps(), since it MATCHes (not MERGEs) both Step
    ends — a typo'd next_step then fails visibly instead of silently
    creating a stub Step. Terminal steps (next_step is None) are filtered
    out before the query runs. Deletes any existing outgoing NEXT_STEP edge
    from the source Step first, same reason as load_steps().
    """
    rows = [
        {"procedure_id": p["procedure_id"], "step_id": s["step_id"], "next_step": s["next_step"]}
        for p in procedures
        for s in p["steps"]
        if s.get("next_step") is not None
    ]
    _run_batch(session, "NEXT_STEP", LOAD_NEXT_STEP_CYPHER, rows)


def print_counts(session) -> None:
    """Print final per-label node counts and NEXT_STEP relationship count."""
    counts = {
        "Step": "MATCH (n:Step) RETURN count(n) AS c",
        "Condition": "MATCH (n:Condition) RETURN count(n) AS c",
        "Action": "MATCH (n:Action) RETURN count(n) AS c",
        "API": "MATCH (n:API) RETURN count(n) AS c",
        "Rule": "MATCH (n:Rule) RETURN count(n) AS c",
        "NEXT_STEP rels": "MATCH ()-[r:NEXT_STEP]->() RETURN count(r) AS c",
    }
    print("\n=== Final counts ===")
    for label, cypher in counts.items():
        c = session.run(cypher).single()["c"]
        print(f"{label}: {c}")


def main() -> int:
    """Entry point: connect, load Phase 1 data in dependency order, report counts.

    See the module docstring for the full step-by-step flow.
    """
    cfg = get_neo4j_config()
    driver = get_driver()
    try:
        driver.verify_connectivity()
    except Exception as e:
        print(f"Could not connect to Neo4j at {cfg.uri} — is Neo4j Desktop running? ({e})")
        return 1

    conditions = load_json("conditions.json")
    actions = load_json("actions.json")
    rules = load_json("rules.json")
    procedures = load_json("procedures.json")

    with driver.session(database=cfg.database) as session:
        create_constraints(session)
        load_conditions(session, conditions)
        load_actions(session, actions)
        load_rules(session, rules)
        load_governed_by(session, rules)
        load_steps(session, procedures)
        load_next_steps(session, procedures)
        print_counts(session)

    driver.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
