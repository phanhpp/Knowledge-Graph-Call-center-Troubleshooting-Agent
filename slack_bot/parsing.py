"""Split the text after `/agent` into a customer ID and a question. Pure functions: no Slack calls,
no agent calls."""

import re
from typing import NamedTuple, Optional

_CUSTOMER_ID = re.compile(r"\bCUST-\d+\b", re.IGNORECASE)
_SEPARATORS = " :,;-–—"


class ParsedMessage(NamedTuple):
    """What a command contains: the customer ID (None if absent) and the rest as the question."""

    customer_id: Optional[str]
    query: str


def parse_message(text: str) -> ParsedMessage:
    """Find the customer ID in `text` and treat everything else as the question.

    The ID is usually first (`CUST-1005 customer cannot activate roaming`), but it may be anywhere
    in the text and any case ("cust-1005" becomes "CUST-1005"). If there are several IDs, the first
    one is used and the others stay in the question text. A missing ID gives customer_id=None, and
    text with only an ID gives an empty query, so the caller can tell the consultant what to add.
    """
    text = text or ""
    match = _CUSTOMER_ID.search(text)
    if match is None:
        return ParsedMessage(customer_id=None, query=" ".join(text.split()).strip(_SEPARATORS))

    rest = f"{text[:match.start()]} {text[match.end():]}"
    return ParsedMessage(
        customer_id=match.group(0).upper(),
        query=" ".join(rest.split()).strip(_SEPARATORS),
    )
