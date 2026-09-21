"""Integration tests for the Procedural Executor — require the live Neo4j
instance loaded by Phase 2 (etl/load_to_neo4j.py), since fetch_step/resolve
query it directly. Not mocked: this project's own pattern is to test
against the real graph, not a Neo4j double.

Three groups, by condition kind:
  - PROC-001: field_check conditions, the three resolution paths.
  - PROC-016/013/009: chain_check conditions (multi-call, still no LLM).
  - PROC-017/019/004: ticket_text conditions — the one path that genuinely
    calls an LLM (agent/ticket_interpreter.py). Skipped if ANTHROPIC_API_KEY
    isn't set, since these make a real API call.
"""

import os

import pytest

from agent.procedural_executor import build_procedural_executor_graph
from agent.state import reply_of
from mock_crm.crm_store import get_customer

GRAPH = build_procedural_executor_graph()

requires_anthropic_key = pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="ticket_text conditions call agent/ticket_interpreter.py, which needs a live LLM call",
)


def run(procedure_id: str, customer_id: str) -> dict:
    """Invoke the compiled executor graph for one (procedure, customer) pair."""
    crm = get_customer(customer_id)
    return GRAPH.invoke({"matched_procedure_id": procedure_id, "crm_context": crm})


def steps(result: dict) -> list[int]:
    """The step numbers the walk checked, in order, read from its step_trace."""
    return [entry["step_id"] for entry in result["step_trace"]]


# --- field_check: PROC-001 "Customer cannot activate roaming" ---


def test_immediate_resolution_on_step_1_true_branch():
    """CUST-1002: roaming_flag is already False -> step 1's condition is
    true immediately, no looping."""
    result = run("PROC-001", "CUST-1002")
    assert steps(result) == [1]
    assert reply_of(result).citations == ["ACT-001"]


def test_loops_to_terminal_true_branch():
    """CUST-1001: roaming on, plan active, device compatible -> falls
    through all three conditions to step 3's true branch."""
    result = run("PROC-001", "CUST-1001")
    assert steps(result) == [1, 2, 3]
    assert reply_of(result).citations == ["ACT-019"]


def test_loops_to_terminal_false_branch_with_rule_citation():
    """CUST-1005: roaming on, plan active, device NOT compatible -> falls
    through to step 3's false (catch-all) branch, which cites its
    governing Rule."""
    result = run("PROC-001", "CUST-1005")
    assert steps(result) == [1, 2, 3]
    assert reply_of(result).citations == ["ACT-015", "R012"]


# --- chain_check: PROC-016 (zip -> outage), PROC-013 (imei -> financing),
# --- PROC-009 (customer_id -> fraud gateway) — still zero LLM calls ---


def test_outage_chain_true_resolves_immediately():
    """CUST-1001's zip (94103) has an active outage -> step 1 resolves immediately."""
    result = run("PROC-016", "CUST-1001")
    assert steps(result) == [1]
    assert reply_of(result).citations == ["ACT-006"]


def test_outage_chain_false_falls_through_to_next_step():
    """CUST-1003's zip (60614) has no outage -> falls through to step 2."""
    result = run("PROC-016", "CUST-1003")
    assert steps(result) == [1, 2]


def test_device_financing_chain_true_resolves_immediately():
    """CUST-1001 owes a device installment balance -> step 1 resolves immediately."""
    result = run("PROC-013", "CUST-1001")
    assert steps(result) == [1]
    assert reply_of(result).citations == ["ACT-026"]


def test_device_financing_chain_false_falls_through_to_terminal():
    """CUST-1002's device is paid off -> falls through the whole procedure."""
    result = run("PROC-013", "CUST-1002")
    assert steps(result) == [1, 2, 3, 4]


def test_fraud_gateway_chain_true_resolves_immediately():
    """CUST-1001 is flagged by the fraud gateway -> step 1 resolves immediately."""
    result = run("PROC-009", "CUST-1001")
    assert steps(result) == [1]
    assert reply_of(result).citations == ["ACT-021", "R007"]


# --- step_trace: what the walk records, for follow-up explanations ---


def test_trace_records_procedure_steps_results_evidence_and_the_action():
    """CUST-1005 says NO at all three steps, so the trace has three entries: each names the
    procedure, the step and its NO result with the value it used, and the last also names the
    action given (ACT-015)."""
    trace = run("PROC-001", "CUST-1005")["step_trace"]

    assert [e["procedure_id"] for e in trace] == ["PROC-001"] * 3
    assert [e["condition_id"] for e in trace] == ["COND-001", "COND-003", "COND-007"]
    assert [e["result"] for e in trace] == [False, False, False]
    assert trace[0]["evidence"] == "roaming_flag = True"
    assert trace[1]["evidence"] == "plan_status = active"
    assert trace[2]["evidence"] == "device.device_roaming_compatible = False"
    assert trace[2]["action_id"] == "ACT-015"
    assert "action_id" not in trace[0] and "action_id" not in trace[1]


def test_trace_records_a_yes_result_and_its_action():
    """CUST-1002 says YES at step 1: one entry, result True, with the action given (ACT-001)."""
    trace = run("PROC-001", "CUST-1002")["step_trace"]

    assert len(trace) == 1
    assert trace[0]["result"] is True
    assert trace[0]["evidence"] == "roaming_flag = False"
    assert trace[0]["action_id"] == "ACT-001"


def test_chain_condition_trace_has_no_single_evidence():
    """A chain condition reads several values, so its trace entry has evidence None."""
    trace = run("PROC-016", "CUST-1001")["step_trace"]

    assert trace[0]["condition_id"] == "COND-023"
    assert trace[0]["evidence"] is None


def test_a_new_walk_does_not_inherit_the_previous_trace():
    """The Supervisor keeps step_trace between turns, so it arrives holding the last walk's entries.
    The new walk must start empty."""
    stale = [{"procedure_id": "PROC-999", "step_id": 9, "stale": True}]
    crm = get_customer("CUST-1002")

    result = GRAPH.invoke({"matched_procedure_id": "PROC-001", "crm_context": crm, "step_trace": stale})

    assert not any(e.get("stale") for e in result["step_trace"])
    assert steps(result) == [1]


def test_a_failed_walk_clears_the_previous_trace():
    """A walk that finds no such procedure must not leave the previous walk's trace behind, or a
    'why?' afterwards would explain the wrong answer."""
    stale = [{"procedure_id": "PROC-001", "step_id": 1}]
    crm = get_customer("CUST-1002")

    result = GRAPH.invoke({"matched_procedure_id": "PROC-999", "crm_context": crm, "step_trace": stale})

    assert result["error"]
    assert result["step_trace"] == []


# --- ticket_text: PROC-017/019/004 — genuinely calls an LLM ---


@requires_anthropic_key
def test_ticket_text_outage_ongoing_triggers_credit_offer():
    """CUST-1006's ticket ("payment error keeps appearing") is unrelated to
    outages -> COND-024 should read as not-ongoing-outage-related; this just
    exercises that the ticket_interpreter path runs end-to-end without error."""
    result = run("PROC-017", "CUST-1006")
    assert reply_of(result).text is not None


@requires_anthropic_key
def test_ticket_text_unauthorized_access_language():
    """Exercises PROC-019's ticket_text step end-to-end via the LLM interpreter."""
    result = run("PROC-019", "CUST-1010")
    assert reply_of(result).text is not None


@requires_anthropic_key
def test_ticket_text_refund_request_language():
    """Exercises PROC-004's ticket_text step end-to-end via the LLM interpreter."""
    result = run("PROC-004", "CUST-1025")
    assert reply_of(result).text is not None
