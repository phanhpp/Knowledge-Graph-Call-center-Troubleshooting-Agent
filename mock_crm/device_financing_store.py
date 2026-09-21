"""Mock device-financing lookup by IMEI — a chain-condition data source
(e.g. COND-025), reached after a first call fetches the customer's IMEI
from their CRM record.
"""

from functools import lru_cache

from etl.io_utils import load_json


class ImeiNotFoundError(KeyError):
    """Raised when an IMEI has no matching financing record."""


@lru_cache(maxsize=1)
def _index() -> dict[str, dict]:
    """Load device_financing.json once and index it by imei."""
    records = load_json("device_financing.json")
    return {r["imei"]: r for r in records}


def get_device_financing(imei: str) -> dict:
    """Return the financing record for imei, or raise ImeiNotFoundError."""
    try:
        return _index()[imei]
    except KeyError:
        raise ImeiNotFoundError(imei) from None
