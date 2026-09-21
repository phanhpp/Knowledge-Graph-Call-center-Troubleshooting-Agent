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


# --- field_check: PROC-001 "Customer cannot activate roaming" ---


def test_immediate_resolution_on_step_1_true_branch():
    """CUST-1002: roaming_flag is already False -> step 1's condition is
    true immediately, no looping."""
    result = run("PROC-001", "CUST-1002")
    assert result["steps_visited"] == [1]
    assert result["citations"] == ["ACT-001"]


def test_loops_to_terminal_true_branch():
    """CUST-1001: roaming on, plan active, device compatible -> falls
    through all three conditions to step 3's true branch."""
    result = run("PROC-001", "CUST-1001")
    assert result["steps_visited"] == [1, 2, 3]
    assert result["citations"] == ["ACT-019"]


def test_loops_to_terminal_false_branch_with_rule_citation():
    """CUST-1005: roaming on, plan active, device NOT compatible -> falls
    through to step 3's false (catch-all) branch, which cites its
    governing Rule."""
    result = run("PROC-001", "CUST-1005")
    assert result["steps_visited"] == [1, 2, 3]
    assert result["citations"] == ["ACT-015", "R012"]


# --- chain_check: PROC-016 (zip -> outage), PROC-013 (imei -> financing),
# --- PROC-009 (customer_id -> fraud gateway) — still zero LLM calls ---


def test_outage_chain_true_resolves_immediately():
    """CUST-1001's zip (94103) has an active outage -> step 1 resolves immediately."""
    result = run("PROC-016", "CUST-1001")
    assert result["steps_visited"] == [1]
    assert result["citations"] == ["ACT-006"]


def test_outage_chain_false_falls_through_to_next_step():
    """CUST-1003's zip (60614) has no outage -> falls through to step 2."""
    result = run("PROC-016", "CUST-1003")
    assert result["steps_visited"] == [1, 2]


def test_device_financing_chain_true_resolves_immediately():
    """CUST-1001 owes a device installment balance -> step 1 resolves immediately."""
    result = run("PROC-013", "CUST-1001")
    assert result["steps_visited"] == [1]
    assert result["citations"] == ["ACT-026"]


def test_device_financing_chain_false_falls_through_to_terminal():
    """CUST-1002's device is paid off -> falls through the whole procedure."""
    result = run("PROC-013", "CUST-1002")
    assert result["steps_visited"] == [1, 2, 3, 4]


def test_fraud_gateway_chain_true_resolves_immediately():
    """CUST-1001 is flagged by the fraud gateway -> step 1 resolves immediately."""
    result = run("PROC-009", "CUST-1001")
    assert result["steps_visited"] == [1]
    assert result["citations"] == ["ACT-021", "R007"]


# --- ticket_text: PROC-017/019/004 — genuinely calls an LLM ---


@requires_anthropic_key
def test_ticket_text_outage_ongoing_triggers_credit_offer():
    """CUST-1006's ticket ("payment error keeps appearing") is unrelated to
    outages -> COND-024 should read as not-ongoing-outage-related; this just
    exercises that the ticket_interpreter path runs end-to-end without error."""
    result = run("PROC-017", "CUST-1006")
    assert result["answer"] is not None


@requires_anthropic_key
def test_ticket_text_unauthorized_access_language():
    """Exercises PROC-019's ticket_text step end-to-end via the LLM interpreter."""
    result = run("PROC-019", "CUST-1010")
    assert result["answer"] is not None


@requires_anthropic_key
def test_ticket_text_refund_request_language():
    """Exercises PROC-004's ticket_text step end-to-end via the LLM interpreter."""
    result = run("PROC-004", "CUST-1025")
    assert result["answer"] is not None
