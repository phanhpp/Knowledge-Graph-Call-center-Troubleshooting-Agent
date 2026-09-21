"""Unit tests for mock_crm/condition_evaluator.py.

Covers evaluate_condition() (single field/operator/value check) against a
fixture record, then evaluate_chain_condition()/resolve_condition() against
the real COND-023 (multi-call chain) condition and real CRM/network_status
data — same pattern as tests/test_procedural_executor.py, since the chain
evaluator's whole point is to work against genuine mock data sources, not a
synthetic fixture.
"""

import pytest

from mock_crm.condition_evaluator import (
    FieldNotFoundError,
    TypeCoercionError,
    UnsupportedOperatorError,
    evaluate_chain_condition,
    evaluate_condition,
    get_condition,
    resolve_condition,
)
from mock_crm.crm_store import get_customer

RECORD = {
    "customer_id": "CUST-9001",
    "account_status": "active",
    "plan_name": "Unlimited Plus",
    "plan_status": "inactive",
    "roaming_flag": False,
    "device": {
        "imei": "356938035643999",
        "model": "iPhone 14",
        "device_roaming_compatible": True,
    },
    "billing": {
        "current_balance": 42.5,
        "payment_method": "credit_card",
        "last_payment_status": "success",
    },
    "sim_status": "active",
    "contract_end_date": "2027-03-15",
    "tenure_months": 12,
    "recent_tickets": [
        {"date": "2026-08-20", "issue": "roaming activation", "resolved": False},
        {"date": "2026-07-01", "issue": "billing question", "resolved": True},
    ],
}


def cond(field, operator, value):
    """Build a minimal field_check condition dict for a single assertion."""
    return {"field": field, "operator": operator, "value": value}


def test_exact_string_match():
    """Plain string equality/inequality on an enum-like field."""
    assert evaluate_condition(RECORD, cond("plan_status", "==", "inactive")) is True
    assert evaluate_condition(RECORD, cond("plan_status", "==", "active")) is False


def test_bool_vs_bool():
    """Both sides already real Python bools — no coercion needed."""
    assert evaluate_condition(RECORD, cond("roaming_flag", "==", False)) is True
    assert evaluate_condition(RECORD, cond("roaming_flag", "==", True)) is False


def test_bool_vs_string_false():
    """The concrete case called out in the design: CRM value is a real bool,
    condition value is a case-insensitive 'true'/'false' string."""
    assert evaluate_condition(RECORD, cond("roaming_flag", "==", "false")) is True
    assert evaluate_condition(RECORD, cond("roaming_flag", "==", "FALSE")) is True
    assert evaluate_condition(RECORD, cond("roaming_flag", "==", "true")) is False


def test_numeric_greater_than_on_tenure_months():
    """Numeric > comparison on an integer CRM field."""
    assert evaluate_condition(RECORD, cond("tenure_months", ">", 6)) is True
    assert evaluate_condition(RECORD, cond("tenure_months", ">", 24)) is False


def test_numeric_less_than_and_gte_on_balance():
    """Numeric </>= comparisons on a float CRM field."""
    assert evaluate_condition(RECORD, cond("billing.current_balance", "<", 50)) is True
    assert evaluate_condition(RECORD, cond("billing.current_balance", ">=", 42.5)) is True
    assert evaluate_condition(RECORD, cond("billing.current_balance", ">=", 43)) is False


def test_nested_two_level_lookup():
    """Dotted-path lookup into a nested object (device.*, billing.*)."""
    assert evaluate_condition(RECORD, cond("device.device_roaming_compatible", "==", True)) is True
    assert evaluate_condition(RECORD, cond("billing.payment_method", "==", "credit_card")) is True


def test_missing_key_raises_field_not_found():
    """A typo'd top-level field path fails loudly, not silently as None."""
    with pytest.raises(FieldNotFoundError):
        evaluate_condition(RECORD, cond("plan_statuz", "==", "active"))


def test_missing_nested_key_raises_field_not_found():
    """A typo'd nested field path also fails loudly."""
    with pytest.raises(FieldNotFoundError):
        evaluate_condition(RECORD, cond("device.nonexistent_field", "==", True))


def test_unsupported_operator_raises():
    """An operator outside the supported set is rejected, never silently ignored."""
    with pytest.raises(UnsupportedOperatorError):
        evaluate_condition(RECORD, cond("tenure_months", "~=", 12))


def test_list_field_compared_via_length():
    """A list-valued field (recent_tickets) is compared against its length."""
    assert evaluate_condition(RECORD, cond("recent_tickets", ">", 0)) is True
    assert evaluate_condition(RECORD, cond("recent_tickets", "==", 2)) is True
    assert evaluate_condition(RECORD, cond("recent_tickets", ">", 5)) is False


def test_numeric_string_coercion():
    """A condition value given as a numeric string still compares correctly."""
    assert evaluate_condition(RECORD, cond("billing.current_balance", "==", "42.5")) is True
    assert evaluate_condition(RECORD, cond("tenure_months", "<", "24")) is True


def test_iso_date_string_comparison():
    """ISO YYYY-MM-DD strings sort correctly under plain string </>, so no
    date parsing is needed for this mock."""
    assert evaluate_condition(RECORD, cond("contract_end_date", ">", "2026-01-01")) is True
    assert evaluate_condition(RECORD, cond("contract_end_date", "<", "2027-01-01")) is False


def test_not_equal_on_enum_field():
    """!= on an enum-like field."""
    assert evaluate_condition(RECORD, cond("sim_status", "!=", "blocked")) is True
    assert evaluate_condition(RECORD, cond("sim_status", "!=", "active")) is False


def test_uncoercible_types_raise_type_coercion_error():
    """A CRM bool compared against a non-bool-like string can't be coerced."""
    with pytest.raises(TypeCoercionError):
        evaluate_condition(RECORD, cond("roaming_flag", "==", "not-a-bool"))


# --- Chain conditions (verification_chain) — real data, not the fixture above ---
# COND-023: fetch the customer's zip_code from CRM, then check network_status
# for that zip. See mock_crm/network_status_store.py for the backing data.


def test_chain_condition_true_when_zip_has_active_outage():
    """CUST-1001's service_area.zip_code (94103) has outage_active: true in
    network_status.json."""
    crm = get_customer("CUST-1001")
    assert crm["service_area"]["zip_code"] == "94103"
    assert evaluate_chain_condition(crm, get_condition("COND-023")) is True


def test_chain_condition_false_when_zip_has_no_outage():
    """CUST-1003's zip_code (60614) has outage_active: false."""
    crm = get_customer("CUST-1003")
    assert crm["service_area"]["zip_code"] == "60614"
    assert evaluate_chain_condition(crm, get_condition("COND-023")) is False


# COND-025: fetch the customer's IMEI from CRM, then check device_financing
# for a remaining installment balance. COND-026: fetch customer_id from CRM,
# then check fraud_gateway for a risk flag. Both exercise CHAIN_SOURCES
# dispatch beyond network_status, proving it generalizes.


def test_chain_condition_true_when_device_has_financing_balance():
    """CUST-1001's device (by IMEI) has a remaining_balance > 0 in
    device_financing.json."""
    crm = get_customer("CUST-1001")
    assert evaluate_chain_condition(crm, get_condition("COND-025")) is True


def test_chain_condition_false_when_device_fully_paid_off():
    """CUST-1002's device has remaining_balance == 0."""
    crm = get_customer("CUST-1002")
    assert evaluate_chain_condition(crm, get_condition("COND-025")) is False


def test_chain_condition_true_when_fraud_gateway_flags_customer():
    """CUST-1001 is flagged risk_flag: true in fraud_gateway.json."""
    crm = get_customer("CUST-1001")
    assert evaluate_chain_condition(crm, get_condition("COND-026")) is True


def test_chain_condition_false_when_fraud_gateway_clears_customer():
    """CUST-1002 is not flagged."""
    crm = get_customer("CUST-1002")
    assert evaluate_chain_condition(crm, get_condition("COND-026")) is False


def test_resolve_condition_dispatches_field_check_and_chain_check():
    """resolve_condition() picks the right evaluator based on the
    condition's shape, without the caller needing to know which."""
    field_check = get_condition("COND-001")  # roaming_flag == false
    chain_check = get_condition("COND-023")  # zip -> network status chain
    crm = get_customer("CUST-1002")  # roaming_flag: false -> COND-001 true

    assert resolve_condition(crm, field_check) is evaluate_condition(crm, field_check)
    assert resolve_condition(crm, chain_check) in (True, False)  # doesn't raise; exercises the chain branch
