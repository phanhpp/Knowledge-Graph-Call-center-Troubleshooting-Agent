"""Shared helper for reading the Phase 1 JSON data files under data/.

Used by etl/validate.py, etl/load_to_neo4j.py, and mock_crm/crm_store.py so
none of them duplicate path-resolution or missing-file handling.
"""

import json
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def load_json(filename: str) -> list[dict]:
    """Load one data/*.json file as a list of dicts.

    Raises FileNotFoundError with a Phase-1 pointer (not a bare traceback)
    if the file doesn't exist yet.
    """
    path = DATA_DIR / filename
    if not path.exists():
        raise FileNotFoundError(f"{path} not found — run Phase 1 generation first")
    with path.open(encoding="utf-8") as f:
        return json.load(f)
