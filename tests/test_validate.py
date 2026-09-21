"""Unit tests for etl/validate.py's check_* functions, using deliberately
broken synthetic fixtures — not the real data/*.json files, since those are
already known-good (etl.validate passes against them). These tests exist
specifically to prove each check actually *catches* the bug it claims to,
not just that real data happens to pass.
"""

from etl.validate import check_procedure_resolvable


def procedure(steps: list[dict]) -> dict:
    """Build a minimal procedure dict for a single test."""
    return {"procedure_id": "PROC-TEST", "procedure_name": "test", "category": "billing", "steps": steps}


def test_well_formed_procedure_has_no_errors():
    """Sanity check: a normal 1 -> 2 -> terminal chain passes clean."""
    proc = procedure([
        {"step_id": 1, "next_step": 2},
        {"step_id": 2, "next_step": 3},
        {"step_id": 3, "next_step": None},
    ])
    assert check_procedure_resolvable([proc]) == []


def test_detects_a_two_step_cycle():
    """step 1 -> 2 -> 1 -> ... never reaches a terminal step — exactly the
    failure mode that produced a real GraphRecursionError at runtime."""
    proc = procedure([
        {"step_id": 1, "next_step": 2},
        {"step_id": 2, "next_step": 1},
    ])
    errors = check_procedure_resolvable([proc])
    assert len(errors) == 1
    assert "cycle detected" in errors[0]


def test_detects_a_self_loop():
    """step 1 points at itself — the smallest possible cycle."""
    proc = procedure([
        {"step_id": 1, "next_step": 1},
    ])
    errors = check_procedure_resolvable([proc])
    assert any("cycle detected" in e for e in errors)


def test_detects_an_orphaned_step():
    """step 3 is never reached: the chain from step 1 goes straight to the
    terminal step 2, skipping over 3 entirely — near-certainly a typo'd
    next_step somewhere else that should have pointed at 3."""
    proc = procedure([
        {"step_id": 1, "next_step": 2},
        {"step_id": 2, "next_step": None},
        {"step_id": 3, "next_step": None},
    ])
    errors = check_procedure_resolvable([proc])
    assert len(errors) == 1
    assert "step(s) [3]" in errors[0]
    assert "never reached" in errors[0]


def test_missing_step_1_is_flagged():
    """A procedure with no step_id 1 has nowhere to start the walk from."""
    proc = procedure([
        {"step_id": 2, "next_step": None},
    ])
    errors = check_procedure_resolvable([proc])
    assert any("no step_id 1" in e for e in errors)


def test_longer_cycle_not_involving_step_1_is_still_caught():
    """1 -> 2 -> 3 -> 2 -- step 1 is fine, but 2/3 loop forever afterward."""
    proc = procedure([
        {"step_id": 1, "next_step": 2},
        {"step_id": 2, "next_step": 3},
        {"step_id": 3, "next_step": 2},
    ])
    errors = check_procedure_resolvable([proc])
    assert any("cycle detected" in e for e in errors)


def test_multiple_procedures_checked_independently():
    """One broken procedure shouldn't affect reporting on a separate, valid one."""
    good = procedure([{"step_id": 1, "next_step": None}])
    good["procedure_id"] = "PROC-GOOD"
    bad = procedure([{"step_id": 1, "next_step": 1}])
    bad["procedure_id"] = "PROC-BAD"
    errors = check_procedure_resolvable([good, bad])
    assert len(errors) == 1
    assert "PROC-BAD" in errors[0]
