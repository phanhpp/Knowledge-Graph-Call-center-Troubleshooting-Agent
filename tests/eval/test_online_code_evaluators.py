"""Local tests for the online code evaluators in eval/online/, before they are pasted into LangSmith.

Each evaluator file is loaded by path, on its own, as LangSmith runs it: this also proves it imports
nothing from the project. The run dicts are shaped like the real root `LangGraph` runs in the `kg`
project (outputs: intent, matched_procedure_id, error, messages with type/content/additional_kwargs).
"""

import importlib.util
from pathlib import Path

from langchain_core.messages import HumanMessage
from langchain_typesafe import ChoiceAnswer, ClassifierResponse

from agent.procedural_executor import reply_text
from agent.supervisor import build_supervisor_graph, load_crm
from agent.utils import consultant_message

ONLINE = Path(__file__).resolve().parents[2] / "eval" / "online"


def load(name: str, folder: Path = ONLINE):
    """Load one file from eval/online/ (or a subfolder) as a standalone module."""
    spec = importlib.util.spec_from_file_location(name, folder / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


citations_exist = load("citations_exist").perform_eval
walk_has_checklist = load("walk_has_checklist").perform_eval
troubleshooting_customer_handled = load("troubleshooting_customer_handled").perform_eval


def run(*, intent="troubleshooting", customer="CUST-1001", procedure="PROC-001", error=None, reply="", citations=()):
    """A root run as LangSmith passes it: a dict, with the reply as the last saved message."""
    messages = [
        {"type": "human", "content": "customer cannot activate roaming", "additional_kwargs": {"customer_id": customer} if customer else {}},
        {"type": "ai", "content": reply, "additional_kwargs": {"citations": list(citations)}},
    ]
    outputs = {"intent": intent, "customer_id": customer, "matched_procedure_id": procedure, "error": error, "messages": messages}
    return {"outputs": outputs}


WALK_TRACE = [
    {"procedure_id": "PROC-001", "step_id": 1, "description": "Roaming is disabled on the account", "result": False, "evidence": "roaming_flag = True"},
]
WALK_REPLY = reply_text("Escalate to Tier 2 - unresolved by standard checks", WALK_TRACE)


# --- citations_exist ---


def test_citations_exist_passes_known_ids_of_every_kind():
    """Action, rule and article IDs are all valid citations."""
    assert citations_exist(run(citations=["ACT-015", "R012", "KB-006"])) == {"citations_exist": 1}


def test_citations_exist_fails_an_unknown_id():
    assert citations_exist(run(citations=["ACT-015", "R999"])) == {"citations_exist": 0}


def test_citations_exist_gives_no_score_to_a_reply_that_cites_nothing():
    """Explanations and fixed messages ("Which customer…?") cite nothing: nothing to check."""
    assert citations_exist(run(citations=[])) == {}


def test_citations_exist_reads_langchains_kwargs_form():
    """A message stored as {"kwargs": {...}} is read the same way."""
    stored = {"outputs": {"messages": [{"kwargs": {"content": "x", "additional_kwargs": {"citations": ["R999"]}}}]}}
    assert citations_exist(stored) == {"citations_exist": 0}


def test_citations_exist_gives_no_score_without_a_reply():
    """An errored run (outputs None) or a run with no messages is not scored, not crashed on."""
    assert citations_exist({"outputs": None}) == {}
    assert citations_exist({"outputs": {"messages": []}}) == {}


def test_citations_exist_list_matches_data():
    """The copied list must equal what build_valid_ids.py reads from data/; if this fails, rerun it."""
    builder = load("build_valid_ids", ONLINE / "utils")
    assert load("citations_exist").VALID_IDS == set(builder.valid_ids())


# --- walk_has_checklist ---


def test_walk_has_checklist_passes_the_real_reply_format():
    """Built with the app's own reply_text, so a change to the reply format shows up here."""
    assert walk_has_checklist(run(reply=WALK_REPLY)) == {"walk_has_checklist": 1}


def test_walk_has_checklist_fails_an_action_alone():
    assert walk_has_checklist(run(reply="Escalate to Tier 2 - unresolved by standard checks")) == {"walk_has_checklist": 0}


def test_walk_has_checklist_skips_turns_without_a_walk():
    """No score for the knowledge-base fallback, an error, or another path."""
    assert walk_has_checklist(run(procedure=None, reply="an article answer")) == {}
    assert walk_has_checklist(run(error="Step 1 not found", reply="I couldn't find…")) == {}
    assert walk_has_checklist(run(intent="explain", reply="Because…")) == {}
    assert walk_has_checklist({"outputs": None}) == {}


# --- troubleshooting_customer_handled: troubleshooting needs a customer ---

NO_CUSTOMER = load_crm({"messages": [HumanMessage("roaming not working")]}).update["error"]  # the app's real error


def test_customer_handled_customer_known_and_walked():
    assert troubleshooting_customer_handled(run(reply=WALK_REPLY)) == {"troubleshooting_customer_handled": 1}


def test_customer_handled_no_customer_asked_and_did_not_walk():
    result = run(customer=None, procedure=None, error=NO_CUSTOMER)
    assert troubleshooting_customer_handled(result) == {"troubleshooting_customer_handled": 1}


def test_customer_handled_fails_a_walk_with_no_customer():
    """A procedure walked with no customer data is the bug this check exists for."""
    result = run(customer=None, procedure="PROC-001", reply=WALK_REPLY)
    assert troubleshooting_customer_handled(result) == {"troubleshooting_customer_handled": 0}


def test_customer_handled_fails_when_the_guard_did_not_fire():
    """No customer, but no "Which customer…?" either."""
    result = run(customer=None, procedure=None, reply="Something else")
    assert troubleshooting_customer_handled(result) == {"troubleshooting_customer_handled": 0}


def test_customer_handled_fails_asking_when_a_customer_was_known():
    result = run(customer="CUST-1001", procedure=None, error=NO_CUSTOMER)
    assert troubleshooting_customer_handled(result) == {"troubleshooting_customer_handled": 0}


def test_customer_handled_skips_general_questions_and_empty_runs():
    """General questions need no customer, so they are never scored."""
    assert troubleshooting_customer_handled(run(intent="open_ended", customer=None, procedure=None)) == {}
    assert troubleshooting_customer_handled({"outputs": None}) == {}


def test_customer_handled_error_text_matches_the_app():
    """The evaluator's copy of the error must equal what load_crm really sets."""
    assert load("troubleshooting_customer_handled").NO_CUSTOMER_ERROR == NO_CUSTOMER


class _FakeJev:
    """Routes every message to troubleshooting and PROC-001, without calling Jev."""

    def invoke(self, request):
        """Answer whichever question was asked."""
        key = "path" if "path" in request["questions"] else "procedure"
        label = "troubleshooting" if key == "path" else "PROC-001"
        answer = ChoiceAnswer(type="choice", choice=label, probabilities={label: 1.0}, confidence=1.0)
        return ClassifierResponse(model="fake-jev", answers={key: answer})


def test_customer_handled_passes_on_the_real_graphs_output(monkeypatch):
    """(Needs Neo4j.) The real graph, a troubleshooting message with no customer: its actual output
    passes the check, so the check reads the fields the app really produces."""
    monkeypatch.setattr("agent.supervisor._classifier", lambda: _FakeJev())

    outputs = build_supervisor_graph().invoke({"messages": [consultant_message("roaming not working")]})

    assert troubleshooting_customer_handled({"outputs": outputs}) == {"troubleshooting_customer_handled": 1}
