"""Online code evaluator `walk_has_checklist`: paste this whole file into LangSmith.

Filter: root runs named `LangGraph`, not errored. Only troubleshooting turns get a score
(decided in the code below, not by the filter).
Score: 1 if the walk's reply has its "Why:" line and its "Checked (PROC-…)" list with at least one
numbered step, 0 if not. No score when no procedure was walked (the message fell back to the
knowledge base) or the turn ended with an error.

Standard library only; no network.
"""

import re


def _fields(message):
    """A saved message's fields, whether stored flat or in LangChain's {"kwargs": {...}} form."""
    if not isinstance(message, dict):
        return {}
    return message.get("kwargs", message)


def perform_eval(run):
    outputs = run.get("outputs") or {}
    walked = outputs.get("intent") == "troubleshooting" and outputs.get("matched_procedure_id")
    messages = outputs.get("messages") or []
    if not walked or outputs.get("error") or not messages:
        return {}
    text = str(_fields(messages[-1]).get("content") or "")
    has_checklist = (
        re.search(r"(?m)^Why: ", text) is not None
        and re.search(r"(?m)^Checked \(PROC-", text) is not None
        and re.search(r"(?m)^\d+\. ", text) is not None
    )
    return {"walk_has_checklist": int(has_checklist)}
