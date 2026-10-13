"""Online code evaluator `troubleshooting_customer_handled`: paste this whole file into LangSmith.

Filter: root runs named `LangGraph`, not errored. Only troubleshooting turns get a score
(decided in the code below, not by the filter).
Checks the rule load_crm must enforce: troubleshooting needs a customer.
  - customer known (output `customer_id` set): the walk may run; asking "Which customer…?" is a bug.
  - no customer known: the bot must ask "Which customer…?" and must not walk a procedure.
Score: 1 if the rule held, 0 if not. No score for other paths (a general question needs no
customer) or a run without outputs.

Standard library only; no network.
"""

# Must match the `error` that load_crm sets in agent/supervisor.py (a test checks this).
NO_CUSTOMER_ERROR = "No customer named for troubleshooting"


def perform_eval(run):
    outputs = run.get("outputs") or {}
    if outputs.get("intent") != "troubleshooting":
        return {}
    customer_known = bool(outputs.get("customer_id"))
    asked = outputs.get("error") == NO_CUSTOMER_ERROR
    walked = bool(outputs.get("matched_procedure_id"))
    if customer_known:
        held = not asked
    else:
        held = asked and not walked
    return {"troubleshooting_customer_handled": int(held)}
