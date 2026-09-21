"""Safe, no-eval() evaluation of a Condition against a CRM record.

The deterministic "Joiner" the Procedural Executor calls once per step —
see agent/procedural_executor.py's module docstring for why no LLM/agent
is involved in this decision, for either shape of condition here:
  - evaluate_condition()       — the original single field/operator/value check
  - evaluate_chain_condition() — a multi-call verification_chain (e.g. fetch
    a zip code, then check network status for that zip) — still fully
    deterministic, since the chain itself is declared in advance in
    conditions.json, not inferred at runtime.
resolve_condition() dispatches between the two. ticket_text conditions are
NOT handled here — see agent/ticket_interpreter.py for why that one shape
genuinely needs an LLM.
"""

from functools import lru_cache
import operator as _operator

from etl.io_utils import load_json
from mock_crm.device_financing_store import get_device_financing
from mock_crm.fraud_gateway_store import get_fraud_status
from mock_crm.network_status_store import get_network_status

# Every non-"crm" chain source, keyed by its `source` string: a lookup
# function taking one key (the value named by that step's `param_from`) and
# returning a dict the step's `reads` key is pulled from. Adding a new
# external mock source means adding one entry here plus its *_store.py.
CHAIN_SOURCES = {
    "network_status": get_network_status,
    "device_financing": get_device_financing,
    "fraud_gateway": get_fraud_status,
}

OPERATORS = {
    "==": _operator.eq,
    "!=": _operator.ne,
    ">": _operator.gt,
    "<": _operator.lt,
    ">=": _operator.ge,
    "<=": _operator.le,
}


class FieldNotFoundError(KeyError):
    """Raised when a condition's field path doesn't resolve on a CRM record."""


class UnsupportedOperatorError(ValueError):
    """Raised when a condition's operator isn't one of the supported set."""


class TypeCoercionError(TypeError):
    """Raised when two values can't be reasonably compared."""


def get_nested_field(record: dict, field_path: str):
    """Dotted-path dict lookup, e.g. 'device.device_roaming_compatible'.

    Never uses getattr/eval — plain dict traversal. Raises FieldNotFoundError
    on a missing key so a typo'd condition.field fails loudly, not silently
    as None.
    """
    value = record
    for part in field_path.split("."):
        if not isinstance(value, dict) or part not in value:
            raise FieldNotFoundError(field_path)
        value = value[part]
    return value


def _to_bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered == "true":
            return True
        if lowered == "false":
            return False
    raise TypeCoercionError(f"cannot coerce {value!r} to bool")


def _coerce(actual, expected):
    """Bool-first, then numeric, then string fallback.

    String fallback also correctly handles ISO 'YYYY-MM-DD' date comparisons
    for free, since that format sorts correctly under plain string </>.
    """
    if isinstance(actual, bool) or isinstance(expected, bool):
        return _to_bool(actual), _to_bool(expected)
    if isinstance(actual, (int, float)) or isinstance(expected, (int, float)):
        try:
            return float(actual), float(expected)
        except (TypeError, ValueError) as e:
            raise TypeCoercionError(f"cannot coerce {actual!r}/{expected!r} to float") from e
    return str(actual), str(expected)


def evaluate_condition(customer_record: dict, condition: dict) -> bool:
    """Evaluate a single field/operator/value condition against a CRM record."""
    op = condition["operator"]
    if op not in OPERATORS:
        raise UnsupportedOperatorError(op)

    actual = get_nested_field(customer_record, condition["field"])
    if isinstance(actual, list):
        # e.g. recent_tickets — compare against its length, so conditions on
        # a list field must use an integer value (operator: >, value: 0).
        actual = len(actual)

    actual, expected = _coerce(actual, condition["value"])
    return OPERATORS[op](actual, expected)


def evaluate_chain_condition(customer_record: dict, condition: dict) -> bool:
    """Walk a condition's verification_chain, threading each step's
    `provides` value into later steps that need it, then compare the final
    provided value like evaluate_condition() does.

    Each step's `source` says where to read from: "crm" reads a field off
    the already-injected customer_record; anything in CHAIN_SOURCES looks up
    that mock external API by a key carried over from an earlier step
    (named by that step's `param_from`).
    """
    context: dict = {}
    for step in condition["verification_chain"]:
        source = step["source"]
        if source == "crm":
            value = get_nested_field(customer_record, step["reads"])
        elif source in CHAIN_SOURCES:
            key = context[step["param_from"]]
            value = CHAIN_SOURCES[source](key)[step["reads"]]
        else:
            raise ValueError(f"unknown verification_chain source: {source!r}")
        context[step["provides"]] = value

    final_value = context[condition["verification_chain"][-1]["provides"]]
    actual, expected = _coerce(final_value, condition["value"])
    return OPERATORS[condition["operator"]](actual, expected)


def resolve_condition(customer_record: dict, condition: dict) -> bool:
    """Dispatch to evaluate_condition or evaluate_chain_condition based on
    the condition's shape. Does NOT handle kind == "ticket_text" — that's
    agent/ticket_interpreter.py's job, since it needs an LLM, not this
    module's plain field/chain lookups."""
    if "verification_chain" in condition:
        return evaluate_chain_condition(customer_record, condition)
    return evaluate_condition(customer_record, condition)


@lru_cache(maxsize=1)
def _condition_library() -> dict[str, dict]:
    """Load conditions.json once and index it by condition_id."""
    conditions = load_json("conditions.json")
    return {c["condition_id"]: c for c in conditions}


def get_condition(condition_id: str) -> dict:
    """Return the full condition definition (field/operator/value,
    verification_chain, or ticket_text fields) by condition_id.

    Neo4j's Condition node only stores flat, browsable properties (it can't
    hold a nested verification_chain) — this is the authoritative source the
    Procedural Executor actually evaluates against, same principle as
    mock_crm/crm_store.py reading crm_records.json directly rather than
    reconstructing a customer record from graph properties.
    """
    return _condition_library()[condition_id]
