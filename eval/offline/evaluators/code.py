"""Code evaluators: exact checks against the golden answer. No model, so they're free and never vary.

Each one is a plain function `(outputs, reference_outputs) -> bool`, the shape `langsmith.evaluate`
accepts: outputs is what the agent returned (target.py), reference_outputs is the golden answer. The
function's name becomes the score's name in LangSmith. SUITES says which checks run for each suite.
"""

import re

# A walk reply's checklist line, as agent/procedural_executor.py writes it:
#   "2. Customer's plan is inactive: no (plan_status = active)"
# Non-greedy up to the first ": yes"/": no", since the evidence in brackets can contain colons.
_CHECK_LINE = re.compile(r"^(\d+)\. .+?: (yes|no)(?: \(.*\))?$")


def checked_steps(reply_text: str) -> list[list]:
    """The [step_id, result] pairs listed in a walk reply's "Checked (…)" section, in order."""
    steps = []
    for line in reply_text.splitlines():
        match = _CHECK_LINE.match(line.strip())
        if match:
            steps.append([int(match.group(1)), match.group(2) == "yes"])
    return steps


def has_checklist(outputs: dict, reference_outputs: dict) -> bool:
    """The reply shows why the walk stopped and every step it checked."""
    text = outputs.get("text", "")
    return bool(re.search(r"(?m)^Why: ", text) and re.search(r"(?m)^Checked \(PROC-", text) and checked_steps(text))


def path_correct(outputs: dict, reference_outputs: dict) -> bool:
    """Routing chose the expected path (troubleshooting / open_ended / explain)."""
    return outputs.get("intent") == reference_outputs["path"]


def procedure_correct(outputs: dict, reference_outputs: dict) -> bool:
    """Routing chose the expected procedure (None when the knowledge base should answer)."""
    return outputs.get("matched_procedure_id") == reference_outputs.get("procedure_id")


def action_correct(outputs: dict, reference_outputs: dict) -> bool:
    """The walk ended at the expected action: a walk reply cites its action first."""
    citations = outputs.get("citations", [])
    return bool(citations) and citations[0] == reference_outputs["action_id"]


def rules_correct(outputs: dict, reference_outputs: dict) -> bool:
    """The reply cites exactly the rules governing that action (order doesn't matter)."""
    return sorted(outputs.get("citations", [])[1:]) == sorted(reference_outputs["rule_ids"])


def steps_correct(outputs: dict, reference_outputs: dict) -> bool:
    """The walk checked the expected steps, in order, with the expected results."""
    return checked_steps(outputs.get("text", "")) == reference_outputs["steps"]


SUITES = {
    "walks": [path_correct, procedure_correct, action_correct, rules_correct, steps_correct, has_checklist],
}
