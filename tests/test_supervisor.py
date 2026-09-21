"""Unit tests for the supervisor's nodes and routing.

Claude is replaced by a fake, so these need no API keys. Two tests (marked below) read Neo4j. The
full graph, with real Claude and Neo4j calls, is covered in tests/e2e/.
"""

from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from agent.procedural_executor import build_procedural_executor_graph
from agent.state import reply_of
from agent.supervisor import (
    NOTHING_TO_EXPLAIN,
    IntentClassification,
    _walk_facts,
    build_supervisor_graph,
    classify_intent,
    explain_answer,
    handle_crm_error,
    inject_crm,
    route,
    route_after_crm,
)
from mock_crm.crm_store import get_customer


class FakeClaude:
    """Stands in for ChatAnthropic: records what it was shown and returns a fixed result."""

    def __init__(self, result):
        """Remember the result to return (an IntentClassification, or an object with .text)."""
        self.result = result
        self.shown = []

    def __call__(self, *args, **kwargs):
        """Act as the ChatAnthropic constructor."""
        return self

    def with_structured_output(self, schema):
        """Return self; the structured result is fixed."""
        return self

    def invoke(self, messages):
        """Record the messages Claude was shown and return the fixed result."""
        self.shown.append(messages)
        return self.result


def conversation(*texts):
    """Alternating consultant / agent messages, starting with the consultant."""
    return [HumanMessage(t) if i % 2 == 0 else AIMessage(t) for i, t in enumerate(texts)]


# --- inject_crm ---


def test_inject_crm_attaches_the_customer_record():
    """A known customer_id puts that customer's CRM record in state."""
    result = inject_crm({"customer_id": "CUST-1001"})
    assert result["crm_context"]["customer_id"] == "CUST-1001"
    assert result["error"] is None


def test_inject_crm_unknown_customer_sets_error_instead_of_raising():
    """An unknown customer_id returns an error entry and no CRM record."""
    result = inject_crm({"customer_id": "CUST-9999"})
    assert "CUST-9999" in result["error"]
    assert "crm_context" not in result


def test_inject_crm_empty_customer_id_is_treated_as_unknown():
    """An empty customer_id is a lookup miss like any other, not a crash."""
    assert "error" in inject_crm({"customer_id": ""})


# --- a new turn starts clean (a saved conversation carries the previous turn's leftovers) ---


def test_inject_crm_clears_the_previous_turns_error():
    """A stale error from the last turn must not send this turn to handle_crm_error."""
    assert inject_crm({"customer_id": "CUST-1001", "error": "old"})["error"] is None


def test_inject_crm_keeps_the_step_trace():
    """The last walk's trace is what a follow-up explains, so a new turn must not clear it."""
    assert "step_trace" not in inject_crm({"customer_id": "CUST-1001", "step_trace": [{"step_id": 1}]})


# --- route_after_crm / handle_crm_error ---


def test_route_after_crm_continues_to_classification_when_no_error():
    """With no error in state, the request carries on to classify_intent."""
    assert route_after_crm({"crm_context": {"customer_id": "CUST-1001"}}) == "classify_intent"


def test_route_after_crm_stops_when_error_is_set():
    """An error from inject_crm sends the request to handle_crm_error."""
    assert route_after_crm({"error": "No CRM record found"}) == "handle_crm_error"


def test_handle_crm_error_message_names_the_customer_id():
    """The reply tells the consultant which ID was not found, and cites nothing."""
    text, citations = reply_of(handle_crm_error({"customer_id": "CUST-9999"}))
    assert "CUST-9999" in text
    assert citations == []


def test_handle_crm_error_message_differs_from_the_no_procedure_message():
    """A missing customer must not be reported as a missing procedure."""
    assert "matching procedure" not in reply_of(handle_crm_error({"customer_id": "CUST-9999"}))[0]


# --- route ---


@pytest.mark.parametrize(
    "intent, expected",
    [
        ("troubleshooting", "procedural_executor"),
        ("open_ended", "graph_rag_chain"),
        ("explain", "explain_answer"),
    ],
)
def test_route_picks_the_path_from_the_intent(intent, expected):
    """The classified intent decides the path, with no model involved."""
    assert route({"intent": intent}) == expected


# --- graph wiring ---


def test_supervisor_graph_contains_every_node():
    """The compiled graph has the error node and all three paths."""
    nodes = set(build_supervisor_graph().get_graph().nodes)
    expected = {
        "inject_crm", "handle_crm_error", "classify_intent",
        "procedural_executor", "graph_rag_chain", "explain_answer",
    }
    assert expected <= nodes


def test_unknown_customer_is_answered_in_the_messages_without_calling_claude():
    """Through the whole graph: an unknown customer gets the message as the agent's reply."""
    result = build_supervisor_graph().invoke({"messages": [HumanMessage("roaming")], "customer_id": "CUST-9999"})

    assert [m.type for m in result["messages"]] == ["human", "ai"]
    assert "CUST-9999" in result["messages"][-1].content
    assert "intent" not in result


# --- classify_intent (a placeholder), with a fake Claude ---


def test_classify_shows_claude_the_whole_conversation(monkeypatch):
    """Claude is shown the procedure list, then every message, the last being the new one."""
    fake = FakeClaude(IntentClassification(intent="open_ended"))
    monkeypatch.setattr("agent.supervisor.ChatAnthropic", fake)
    messages = conversation("roaming", "Escalate", "why?")

    classify_intent({"messages": messages, "customer_id": "CUST-1005"})

    shown = fake.shown[0]
    assert "PROC-001" in shown[0].content
    assert shown[1:] == messages


def test_classify_troubleshooting_keeps_the_procedure_id(monkeypatch):
    """A troubleshooting pick keeps its procedure id."""
    fake = FakeClaude(IntentClassification(intent="troubleshooting", matched_procedure_id="PROC-016"))
    monkeypatch.setattr("agent.supervisor.ChatAnthropic", fake)

    result = classify_intent({"messages": conversation("outage"), "customer_id": "CUST-1005"})

    assert result == {"intent": "troubleshooting", "matched_procedure_id": "PROC-016"}


@pytest.mark.parametrize("intent", ["open_ended", "explain"])
def test_classify_other_intents_have_no_procedure(monkeypatch, intent):
    """Only a troubleshooting pick carries a procedure id, even if Claude returned one."""
    fake = FakeClaude(IntentClassification(intent=intent, matched_procedure_id="PROC-001"))
    monkeypatch.setattr("agent.supervisor.ChatAnthropic", fake)

    result = classify_intent({"messages": conversation("a", "b", "c"), "customer_id": "CUST-1005"})

    assert result["matched_procedure_id"] is None


def test_classify_explain_on_a_first_message_becomes_open_ended(monkeypatch):
    """Claude picking 'explain' when nothing came before is corrected in code."""
    monkeypatch.setattr("agent.supervisor.ChatAnthropic", FakeClaude(IntentClassification(intent="explain")))

    result = classify_intent({"messages": conversation("what does R012 say?"), "customer_id": "CUST-1005"})

    assert result["intent"] == "open_ended"


def test_classify_explain_is_allowed_after_an_earlier_answer(monkeypatch):
    """With an earlier exchange, 'explain' stands."""
    monkeypatch.setattr("agent.supervisor.ChatAnthropic", FakeClaude(IntentClassification(intent="explain")))

    result = classify_intent({"messages": conversation("roaming", "Escalate", "why?"), "customer_id": "CUST-1005"})

    assert result["intent"] == "explain"


# --- explain_answer ---


def test_explain_with_nothing_earlier_does_not_call_claude(monkeypatch):
    """A first message has nothing to explain: fixed text, and Claude is never created."""
    def boom(*args, **kwargs):
        raise AssertionError("Claude must not be called")

    monkeypatch.setattr("agent.supervisor.ChatAnthropic", boom)

    result = explain_answer({"messages": conversation("why?"), "customer_id": "CUST-1005"})

    assert reply_of(result) == (NOTHING_TO_EXPLAIN, [])


def test_explain_gives_claude_the_facts_and_the_conversation(monkeypatch):
    """The system message carries the walk's facts; the conversation follows it. The reply cites the
    action and rules behind the walk."""
    fake = FakeClaude(SimpleNamespace(text="Because step 1 said NO."))
    monkeypatch.setattr("agent.supervisor.ChatAnthropic", fake)
    monkeypatch.setattr("agent.supervisor._walk_facts", lambda trace: ("FACTS ABOUT THE WALK", ["ACT-015", "R012"]))
    messages = conversation("roaming", "Escalate", "why?")

    result = explain_answer({"messages": messages, "customer_id": "CUST-1005", "step_trace": [{"x": 1}]})

    shown = fake.shown[0]
    assert "FACTS ABOUT THE WALK" in shown[0].content
    assert shown[1:] == messages
    assert reply_of(result) == ("Because step 1 said NO.", ["ACT-015", "R012"])


def test_explain_after_a_non_walk_answer_says_no_procedure_was_run(monkeypatch):
    """With no step trace (the last answer came from the knowledge base), the facts say so."""
    fake = FakeClaude(SimpleNamespace(text="It came from KB-005."))
    monkeypatch.setattr("agent.supervisor.ChatAnthropic", fake)

    result = explain_answer({"messages": conversation("fees?", "See KB-005", "why?"), "customer_id": "CUST-1005"})

    assert "no procedure was run" in fake.shown[0][0].content
    assert reply_of(result)[1] == []


# --- _walk_facts ---


def test_walk_facts_with_no_walk_is_empty():
    """No trace means no facts and no citations."""
    assert _walk_facts([]) == ("", [])


def test_walk_facts_without_an_action_lists_only_the_steps():
    """A walk that ended without an action (an error path) has steps but nothing to cite."""
    trace = [{"procedure_id": "PROC-001", "step_id": 1, "description": "Roaming is disabled", "result": False, "evidence": "roaming_flag = True"}]

    facts, cited = _walk_facts(trace)

    assert 'Step 1: "Roaming is disabled" -> NO (roaming_flag = True)' in facts
    assert "Action given" not in facts
    assert cited == []


def test_walk_facts_reads_the_action_and_its_rule_from_neo4j():
    """(Needs Neo4j.) For the real CUST-1005 walk, the facts name the procedure, each step's result
    and value, the action given, and the wording of the rule that limits it."""
    trace = build_procedural_executor_graph().invoke(
        {"matched_procedure_id": "PROC-001", "crm_context": get_customer("CUST-1005")}
    )["step_trace"]

    facts, cited = _walk_facts(trace)

    assert 'Procedure PROC-001 ("Customer cannot activate roaming")' in facts
    assert "NO (roaming_flag = True)" in facts
    assert 'Action given: ACT-015 "Escalate to Tier 2 - unresolved by standard checks"' in facts
    assert "R012: Tier 2 escalations require a documented troubleshooting summary" in facts
    assert cited == ["ACT-015", "R012"]
