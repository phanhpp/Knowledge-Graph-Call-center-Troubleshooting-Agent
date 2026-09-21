"""Mock fraud-gateway risk-flag lookup by customer_id — a chain-condition
data source (e.g. COND-026).
"""

from functools import lru_cache

from etl.io_utils import load_json


class CustomerNotFoundError(KeyError):
    """Raised when a customer_id has no matching fraud-gateway record."""


@lru_cache(maxsize=1)
def _index() -> dict[str, dict]:
    """Load fraud_gateway.json once and index it by customer_id."""
    records = load_json("fraud_gateway.json")
    return {r["customer_id"]: r for r in records}


def get_fraud_status(customer_id: str) -> dict:
    """Return the fraud-gateway record for customer_id, or raise CustomerNotFoundError."""
    try:
        return _index()[customer_id]
    except KeyError:
        raise CustomerNotFoundError(customer_id) from None
