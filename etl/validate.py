"""Phase 1 data validation gate.

Run as: uv run python -m etl.validate

Loads whichever of the six data files exist and runs the checks that are
possible with what's available — so this can be run incrementally as each
file is authored, not just once at the end. Checks that depend on a
not-yet-created file are reported as skipped, not failed.
"""

import sys

from etl.io_utils import load_json
from mock_crm.condition_evaluator import CHAIN_SOURCES

VALID_CRM_FIELDS = {
    "customer_id",
    "account_status",
    "plan_name",
    "plan_status",
    "roaming_flag",
    "device.imei",
    "device.model",
    "device.device_roaming_compatible",
    "billing.current_balance",
    "billing.payment_method",
    "billing.last_payment_status",
    "sim_status",
    "contract_end_date",
    "tenure_months",
    "recent_tickets",
    "service_area.zip_code",
}

VALID_OPERATORS = {"==", "!=", ">", "<", ">=", "<="}

REQUIRED_FIELDS = {
    "crm": ["customer_id", "name", "account_status", "plan_name", "plan_status"],
    "action": ["action_id", "text", "category", "is_resolution"],
    "procedure": ["procedure_id", "procedure_name", "category", "steps"],
    "rule": ["rule_id", "description", "applies_to_action_ids", "constraint_type", "detail"],
    "article": ["article_id", "title", "category", "content", "related_rule_ids"],
}
# "condition" is intentionally not here — conditions come in three shapes
# (field_check / chain_check / ticket_text), each with different required
# fields; see CONDITION_REQUIRED_FIELDS + check_condition_required_fields.

CONDITION_REQUIRED_FIELDS = {
    "field_check": ["condition_id", "field", "operator", "value", "description", "verification_api"],
    "chain_check": ["condition_id", "description", "operator", "value", "verification_chain"],
    "ticket_text": ["condition_id", "kind", "description", "interpretation_question"],
}


def _condition_kind(c: dict) -> str:
    """field_check (single API call, the original shape) / chain_check
    (ordered multi-call verification_chain) / ticket_text (free-text
    interpretation — no field/operator/value, evaluated by an LLM, not code)."""
    if c.get("kind") == "ticket_text":
        return "ticket_text"
    if "verification_chain" in c:
        return "chain_check"
    return "field_check"


def _try_load(filename: str) -> tuple[list[dict] | None, str | None]:
    """Load a data file if it exists; otherwise return (None, skip-message).

    Never raises — lets main() run whichever checks are currently possible
    instead of failing outright when Phase 1 is only partially authored.
    """
    try:
        return load_json(filename), None
    except FileNotFoundError:
        return None, f"{filename} not found yet — skipping checks that depend on it"


def check_no_null_required_fields(entity_type: str, records: list[dict]) -> list[str]:
    """Flag any record missing one of REQUIRED_FIELDS[entity_type]."""
    errors = []
    required = REQUIRED_FIELDS[entity_type]
    for r in records:
        for field in required:
            if r.get(field) is None:
                errors.append(f"{entity_type} {r.get('condition_id') or r.get('action_id') or r.get('customer_id') or r.get('procedure_id') or r.get('rule_id') or r.get('article_id') or '?'}: missing required field '{field}'")
    return errors


def check_unique_ids(entity_type: str, records: list[dict], id_field: str) -> list[str]:
    """Flag any duplicate value of id_field across records.

    A silent duplicate ID here would become a silently-merged Neo4j node at
    load time, so this runs on every ID-bearing entity type, including
    (procedure_id, step_id) pairs within a single procedure's steps.
    """
    seen: dict = {}
    errors = []
    for r in records:
        key = r.get(id_field)
        if key in seen:
            errors.append(f"{entity_type}: duplicate {id_field} '{key}'")
        seen[key] = True
    return errors


def check_condition_required_fields(conditions: list[dict]) -> list[str]:
    """Kind-aware required-field check — field_check/chain_check/ticket_text
    each need different fields (replaces the generic required-fields check
    for this entity type, which can't express that)."""
    errors = []
    for c in conditions:
        kind = _condition_kind(c)
        for field in CONDITION_REQUIRED_FIELDS[kind]:
            if c.get(field) is None:
                errors.append(f"condition {c.get('condition_id', '?')} ({kind}): missing required field '{field}'")
    return errors


def check_condition_fields_valid(conditions: list[dict]) -> list[str]:
    """Flag any field_check condition whose `field` isn't a real CRM record
    field path. chain_check/ticket_text conditions don't have a top-level
    `field` (their fields live inside verification_chain, or not at all)."""
    return [
        f"condition {c['condition_id']}: field '{c['field']}' not in VALID_CRM_FIELDS"
        for c in conditions
        if _condition_kind(c) == "field_check" and c["field"] not in VALID_CRM_FIELDS
    ]


def check_condition_operators_valid(conditions: list[dict]) -> list[str]:
    """Flag any field_check/chain_check condition using an unsupported
    operator. ticket_text conditions have no operator — an LLM judgment call
    isn't a comparison."""
    return [
        f"condition {c['condition_id']}: operator '{c['operator']}' not supported"
        for c in conditions
        if _condition_kind(c) != "ticket_text" and c.get("operator") not in VALID_OPERATORS
    ]


def check_verification_api_consistency(conditions: list[dict]) -> list[str]:
    """Flag any CRM field checked via more than one distinct verification_api.

    Only applies to field_check conditions (the single-call shape) — this is
    the check that actually protects API-node dedup at load time: MERGE only
    unifies byte-identical endpoint strings, so two conditions on the same
    field with slightly different wording would silently produce two API
    nodes instead of one shared one.
    """
    by_field: dict[str, set[str]] = {}
    for c in conditions:
        if _condition_kind(c) != "field_check":
            continue
        by_field.setdefault(c["field"], set()).add(c["verification_api"])
    errors = []
    for field, apis in by_field.items():
        if len(apis) > 1:
            errors.append(f"field '{field}' has {len(apis)} distinct verification_api values: {sorted(apis)}")
    return errors


def check_chain_conditions_valid(conditions: list[dict]) -> list[str]:
    """For chain_check conditions: at least 2 steps (else it should just be a
    plain verification_api), every source is known (crm, or one of
    CHAIN_SOURCES — the same dispatch table the real evaluator uses, so a
    condition referencing a source the executor can't actually handle is
    caught here, not at runtime), every external-source step's param_from
    resolves to an earlier step's provides, and every crm step reads a real
    CRM field path."""
    errors = []
    for c in conditions:
        if _condition_kind(c) != "chain_check":
            continue
        chain = c["verification_chain"]
        cid = c["condition_id"]
        if len(chain) < 2:
            errors.append(f"condition {cid}: verification_chain has only {len(chain)} step(s) — use verification_api instead")
        provided_so_far: set[str] = set()
        for i, step in enumerate(chain, start=1):
            source = step.get("source")
            if source == "crm":
                if step.get("reads") not in VALID_CRM_FIELDS:
                    errors.append(f"condition {cid} chain step {i}: reads '{step.get('reads')}' not in VALID_CRM_FIELDS")
            elif source in CHAIN_SOURCES:
                if step.get("param_from") not in provided_so_far:
                    errors.append(f"condition {cid} chain step {i}: param_from '{step.get('param_from')}' not provided by an earlier step")
            else:
                errors.append(f"condition {cid} chain step {i}: unknown source '{source}' (not 'crm' or one of {sorted(CHAIN_SOURCES)})")
            provided_so_far.add(step.get("provides"))
    return errors


def check_procedure_references(procedures: list[dict], condition_ids: set[str], action_ids: set[str]) -> list[str]:
    """Flag any step whose condition_id/action_if_true_id/action_if_false_id
    doesn't resolve to a real entry in the Condition/Action libraries."""
    errors = []
    for p in procedures:
        for s in p["steps"]:
            pid, sid = p["procedure_id"], s["step_id"]
            if s["condition_id"] not in condition_ids:
                errors.append(f"{pid} step {sid}: condition_id '{s['condition_id']}' not found")
            if s["action_if_true_id"] not in action_ids:
                errors.append(f"{pid} step {sid}: action_if_true_id '{s['action_if_true_id']}' not found")
            afi = s.get("action_if_false_id")
            if afi is not None and afi not in action_ids:
                errors.append(f"{pid} step {sid}: action_if_false_id '{afi}' not found")
    return errors


def check_next_step_resolves(procedures: list[dict]) -> list[str]:
    """Flag any non-null next_step that isn't a real step_id in the same procedure."""
    errors = []
    for p in procedures:
        step_ids = {s["step_id"] for s in p["steps"]}
        for s in p["steps"]:
            ns = s.get("next_step")
            if ns is not None and ns not in step_ids:
                errors.append(f"{p['procedure_id']} step {s['step_id']}: next_step '{ns}' not a real step in this procedure")
    return errors


def check_terminal_steps(procedures: list[dict], actions_by_id: dict) -> list[str]:
    """Flag procedures without exactly one terminal step (next_step: null),
    and terminal steps whose action_if_false_id is missing or isn't marked
    is_resolution:true."""
    errors = []
    for p in procedures:
        terminal = [s for s in p["steps"] if s.get("next_step") is None]
        pid = p["procedure_id"]
        if len(terminal) != 1:
            errors.append(f"{pid}: expected exactly 1 terminal step (next_step: null), found {len(terminal)}")
            continue
        t = terminal[0]
        afi = t.get("action_if_false_id")
        if not afi:
            errors.append(f"{pid} step {t['step_id']}: terminal step missing action_if_false_id")
            continue
        action = actions_by_id.get(afi)
        if action is None:
            continue  # already reported by check_procedure_references
        if not action.get("is_resolution"):
            errors.append(f"{pid} step {t['step_id']}: action_if_false_id '{afi}' is not marked is_resolution:true")
    return errors


def check_procedure_resolvable(procedures: list[dict]) -> list[str]:
    """Walk each procedure's NEXT_STEP chain from step 1, flagging:
    - a cycle (a step revisited before reaching the terminal step) — exactly
      the failure mode that produces GraphRecursionError at runtime, i.e.
      "a procedure with no way to resolve"
    - a step never reached by that walk — near-certainly a typo'd next_step
      pointer elsewhere skipping over it

    This is the automated, PR-gate answer to "a procedure with no way to
    resolve": pure JSON, no Neo4j needed, so it can run on every PR that
    touches data/ for free.
    """
    errors = []
    for p in procedures:
        pid = p["procedure_id"]
        steps_by_id = {s["step_id"]: s for s in p["steps"]}
        if 1 not in steps_by_id:
            errors.append(f"{pid}: no step_id 1 to start the chain from")
            continue

        visited: list[int] = []
        current = 1
        # A well-formed procedure can never need more hops than it has
        # steps — anything beyond that is unambiguously a cycle.
        max_hops = len(p["steps"]) + 1
        for _ in range(max_hops):
            if current in visited:
                errors.append(f"{pid}: cycle detected — step {current} revisited via chain {visited + [current]}")
                break
            visited.append(current)
            step = steps_by_id.get(current)
            if step is None:
                break  # invalid next_step — already reported by check_next_step_resolves
            next_step = step.get("next_step")
            if next_step is None:
                break  # reached a terminal step cleanly
            current = next_step
        else:
            errors.append(f"{pid}: chain from step 1 never reached a terminal step within {max_hops} hops")

        orphaned = set(steps_by_id) - set(visited)
        if orphaned:
            errors.append(f"{pid}: step(s) {sorted(orphaned)} never reached by the NEXT_STEP chain from step 1 — check for a typo'd next_step pointer")
    return errors


def check_rule_action_references(rules: list[dict], action_ids: set[str]) -> list[str]:
    """Flag any rule's applies_to_action_ids entry that isn't a real action_id."""
    errors = []
    for r in rules:
        for aid in r["applies_to_action_ids"]:
            if aid not in action_ids:
                errors.append(f"rule {r['rule_id']}: applies_to_action_ids references unknown action_id '{aid}'")
    return errors


def check_kb_rule_references(articles: list[dict], rule_ids: set[str]) -> list[str]:
    """Flag any article's related_rule_ids entry that isn't a real rule_id."""
    errors = []
    for a in articles:
        for rid in a.get("related_rule_ids", []):
            if rid not in rule_ids:
                errors.append(f"article {a['article_id']}: related_rule_ids references unknown rule_id '{rid}'")
    return errors


def main() -> int:
    """Load whichever data files exist, run every check that's currently
    possible, print a PASS/FAIL/SKIP summary, and exit non-zero on any FAIL."""
    crm, crm_skip = _try_load("crm_records.json")
    conditions, cond_skip = _try_load("conditions.json")
    actions, act_skip = _try_load("actions.json")
    procedures, proc_skip = _try_load("procedures.json")
    rules, rule_skip = _try_load("rules.json")
    articles, art_skip = _try_load("kb_articles.json")

    results: list[tuple[str, list[str]]] = []
    skipped: list[str] = [m for m in (crm_skip, cond_skip, act_skip, proc_skip, rule_skip, art_skip) if m]

    if crm is not None:
        results.append(("crm required fields", check_no_null_required_fields("crm", crm)))
        results.append(("crm unique customer_id", check_unique_ids("crm", crm, "customer_id")))

    if conditions is not None:
        results.append(("condition required fields", check_condition_required_fields(conditions)))
        results.append(("condition unique id", check_unique_ids("condition", conditions, "condition_id")))
        results.append(("condition fields valid", check_condition_fields_valid(conditions)))
        results.append(("condition operators valid", check_condition_operators_valid(conditions)))
        results.append(("condition verification_api consistency", check_verification_api_consistency(conditions)))
        results.append(("condition chains valid", check_chain_conditions_valid(conditions)))

    if actions is not None:
        results.append(("action required fields", check_no_null_required_fields("action", actions)))
        results.append(("action unique id", check_unique_ids("action", actions, "action_id")))

    if procedures is not None and conditions is not None and actions is not None:
        condition_ids = {c["condition_id"] for c in conditions}
        action_ids = {a["action_id"] for a in actions}
        actions_by_id = {a["action_id"]: a for a in actions}
        results.append(("procedure required fields", check_no_null_required_fields("procedure", procedures)))
        results.append(("procedure unique id", check_unique_ids("procedure", procedures, "procedure_id")))
        results.append(("procedure step (procedure_id, step_id) unique", [
            e for p in procedures
            for e in check_unique_ids(f"procedure {p['procedure_id']} steps", p["steps"], "step_id")
        ]))
        results.append(("procedure condition/action references resolve", check_procedure_references(procedures, condition_ids, action_ids)))
        results.append(("procedure next_step resolves within procedure", check_next_step_resolves(procedures)))
        results.append(("procedure terminal step correctness", check_terminal_steps(procedures, actions_by_id)))
        results.append(("procedure resolvable (no cycles, no orphaned steps)", check_procedure_resolvable(procedures)))

    if rules is not None:
        results.append(("rule required fields", check_no_null_required_fields("rule", rules)))
        results.append(("rule unique id", check_unique_ids("rule", rules, "rule_id")))
        if actions is not None:
            action_ids = {a["action_id"] for a in actions}
            results.append(("rule action references resolve", check_rule_action_references(rules, action_ids)))

    if articles is not None:
        results.append(("article required fields", check_no_null_required_fields("article", articles)))
        results.append(("article unique id", check_unique_ids("article", articles, "article_id")))
        if rules is not None:
            rule_ids = {r["rule_id"] for r in rules}
            results.append(("article rule references resolve", check_kb_rule_references(articles, rule_ids)))

    print("=== etl.validate results ===")
    any_failed = False
    for name, errors in results:
        if errors:
            any_failed = True
            print(f"[FAIL] {name} ({len(errors)} issue(s))")
            for e in errors:
                print(f"    - {e}")
        else:
            print(f"[PASS] {name}")

    for s in skipped:
        print(f"[SKIP] {s}")

    if any_failed:
        print("\nValidation FAILED — fix data before loading into Neo4j.")
        return 1
    print("\nValidation passed for everything checked so far.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
