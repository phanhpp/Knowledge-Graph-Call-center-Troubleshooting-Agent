"""Mock CRM — JSON-file-backed customer lookup by customer_id.

Loaded once and cached; this is the "CRM injection" data source. The
Supervisor's check_customer() node uses it to check that a named ID exists,
and load_crm() (troubleshooting only) loads the record the Procedural
Executor's condition evaluator reads from.
"""

from functools import lru_cache

from etl.io_utils import load_json


class CustomerNotFoundError(KeyError):
    """Raised when a customer_id has no matching CRM record."""


@lru_cache(maxsize=1)
def _index() -> dict[str, dict]:
    """Load crm_records.json once and index it by customer_id."""
    records = load_json("crm_records.json")
    return {r["customer_id"]: r for r in records}


def get_customer(customer_id: str) -> dict:
    """Return the CRM record for customer_id, or raise CustomerNotFoundError."""
    try:
        return _index()[customer_id]
    except KeyError:
        raise CustomerNotFoundError(customer_id) from None


def reload() -> None:
    """Clear the cached index — for test isolation or after data changes."""
    _index.cache_clear()
