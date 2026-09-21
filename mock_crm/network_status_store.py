"""Mock network-status lookup by ZIP code — the second mock "API" a chained
condition (e.g. COND-023) can call, after the first call fetches the
customer's zip_code from their CRM record.
"""

from functools import lru_cache

from etl.io_utils import load_json


class ZipNotFoundError(KeyError):
    """Raised when a zip_code has no matching network status entry."""


@lru_cache(maxsize=1)
def _index() -> dict[str, dict]:
    """Load network_status.json once and index it by zip_code."""
    records = load_json("network_status.json")
    return {r["zip_code"]: r for r in records}


def get_network_status(zip_code: str) -> dict:
    """Return the network status record for zip_code, or raise ZipNotFoundError."""
    try:
        return _index()[zip_code]
    except KeyError:
        raise ZipNotFoundError(zip_code) from None
