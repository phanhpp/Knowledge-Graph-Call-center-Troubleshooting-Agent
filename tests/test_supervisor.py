"""Unit tests for the supervisor's nodes and routing.

Claude and Jev are replaced by fakes, so these need no API keys. Two tests (marked below) read Neo4j. The
full graph, with real Claude and Neo4j calls, is covered in tests/e2e/.
"""

from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_typesafe import ChoiceAnswer, ClassifierResponse, NoulAnswer
from langgraph.graph import END

from agent.procedural_executor import build_procedural_executor_graph
from agent.state import reply_of
from agent.supervisor import (
    NEO4J_DOWN,
    NOTHING_TO_EXPLAIN,
    check_neo4j,
    _walk_facts,
    build_supervisor_graph,
    classify_intent,
    explain_answer,
    handle_crm_error,
    inject_crm,
    match_procedure,
    route,
    route_after_crm,
    route_after_match,
)
from mock_crm.crm_store import get_customer


class FakeClaude:
    """Stands in for ChatAnthropic: records what it was shown and returns a fixed result."""

    def __init__(self, result):
        """Remember the result to return (an object with .text)."""
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


class FakeJev:
    """Stands in for the TypeSafe classifier: records each request and returns fixed answers."""

    def __init__(self, **answers):
        """Remember the answers to return, keyed by question id."""
        self.response = ClassifierResponse(model="fake-jev", answers=answers)
        self.requests = []

    def invoke(self, request):
        """Record the request (state and questions) and return the fixed answers."""
        self.requests.append(request)
        return self.response


def use_jev(monkeypatch, **answers) -> FakeJev:
    """Make classify_intent and match_procedure use a FakeJev returning `answers`."""
    fake = FakeJev(**answers)
    monkeypatch.setattr("agent.supervisor._classifier", lambda: fake)
    return fake


def choice(label: str, confidence: float = 0.9) -> ChoiceAnswer:
    """A Choice answer picking `label` with the given confidence."""
    return ChoiceAnswer(type="choice", choice=label, probabilities={label: confidence}, confidence=confidence)


def noul(probability: float) -> NoulAnswer:
    """A Noul answer: the probability of yes."""
    return NoulAnswer(type="noul", noul=probability)


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


def test_inject_crm_keeps_the_troubleshooting_results():
    """Each customer's last result is what a follow-up explains, so a new turn must not clear it."""
    saved = {"CUST-1001": {"procedure_id": "PROC-001"}}
    assert "troubleshooting_results" not in inject_crm({"customer_id": "CUST-1001", "troubleshooting_results": saved})


# --- route_after_crm / handle_crm_error ---


def test_route_after_crm_continues_to_the_neo4j_check_when_no_error():
    """With no error in state, the request carries on to classify_intent."""
    assert route_after_crm({"crm_context": {"customer_id": "CUST-1001"}}) == "check_neo4j"


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


# --- check_neo4j ---


def test_check_neo4j_lets_the_turn_continue_when_neo4j_answers(monkeypatch):
    """Neo4j reachable: nothing written, and the turn goes on to classification."""
    monkeypatch.setattr("agent.supervisor.neo4j_unavailable_reason", lambda: None)

    command = check_neo4j({})

    assert command.goto == "classify_intent"
    assert not command.update


def test_check_neo4j_ends_the_turn_when_neo4j_is_down(monkeypatch):
    """Neo4j down: the reply is NEO4J_DOWN, `error` says why, and the turn ends here."""
    monkeypatch.setattr("agent.supervisor.neo4j_unavailable_reason", lambda: "cannot reach Neo4j at bolt://x: ServiceUnavailable")

    command = check_neo4j({})

    assert command.goto == END
    assert "ServiceUnavailable" in command.update["error"]
    assert reply_of(command.update) == (NEO4J_DOWN, [])


def test_with_neo4j_down_no_classifier_is_called(monkeypatch):
    """Through the whole graph: Neo4j down means the reply is NEO4J_DOWN and Jev is never asked."""
    monkeypatch.setattr("agent.supervisor.neo4j_unavailable_reason", lambda: "cannot reach Neo4j")
    monkeypatch.setattr("agent.supervisor._classifier", lambda: pytest.fail("Jev was called"))

    result = build_supervisor_graph().invoke({"messages": [HumanMessage("roaming")], "customer_id": "CUST-1005"})

    assert reply_of(result).text == NEO4J_DOWN
    assert "intent" not in result


# --- route ---


@pytest.mark.parametrize(
    "intent, expected",
    [
        ("troubleshooting", "match_procedure"),
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
        "inject_crm", "handle_crm_error", "check_neo4j", "classify_intent", "match_procedure",
        "procedural_executor", "graph_rag_chain", "explain_answer",
    }
    assert expected <= nodes


def test_unknown_customer_is_answered_in_the_messages_without_calling_claude():
    """Through the whole graph: an unknown customer gets the message as the agent's reply."""
    result = build_supervisor_graph().invoke({"messages": [HumanMessage("roaming")], "customer_id": "CUST-9999"})

    assert [m.type for m in result["messages"]] == ["human", "ai"]
    assert "CUST-9999" in result["messages"][-1].content
    assert "intent" not in result


# --- classify_intent, with a fake Jev ---


def test_classify_shows_jev_the_whole_conversation_but_not_the_procedures(monkeypatch):
    """Jev classifies every message, and the procedure list is not part of the path question."""
    fake = use_jev(monkeypatch, path=choice("open_ended"), own_situation=noul(0.1))
    messages = conversation("roaming", "Escalate", "why?")

    classify_intent({"messages": messages, "customer_id": "CUST-1005"})

    request = fake.requests[0]
    assert request["state"] == messages
    assert "PROC-" not in str(request["questions"])


def test_classify_confident_troubleshooting_leaves_the_procedure_to_match_procedure(monkeypatch):
    """A confident troubleshooting answer stands, with no procedure picked yet."""
    use_jev(monkeypatch, path=choice("troubleshooting", 0.9), own_situation=noul(0.9))

    result = classify_intent({"messages": conversation("no signal at home"), "customer_id": "CUST-1005"})

    assert result == {"intent": "troubleshooting", "matched_procedure_id": None}


def test_classify_troubleshooting_with_low_confidence_becomes_open_ended(monkeypatch):
    """When Jev is unsure of the path, the message goes to the knowledge base, not a walk."""
    use_jev(monkeypatch, path=choice("troubleshooting", 0.3), own_situation=noul(0.9))

    result = classify_intent({"messages": conversation("cancel?"), "customer_id": "CUST-1005"})

    assert result["intent"] == "open_ended"


def test_classify_troubleshooting_about_no_one_in_particular_becomes_open_ended(monkeypatch):
    """A general question (the fee case) is open_ended even if the path answer said troubleshooting."""
    use_jev(monkeypatch, path=choice("troubleshooting", 0.9), own_situation=noul(0.2))

    result = classify_intent(
        {"messages": conversation("What are the early termination fees if I cancel my contract?"), "customer_id": "CUST-1001"}
    )

    assert result["intent"] == "open_ended"


def test_classify_clears_a_procedure_from_an_earlier_turn(monkeypatch):
    """matched_procedure_id is reset every turn; only match_procedure sets it."""
    use_jev(monkeypatch, path=choice("explain"), own_situation=noul(0.5))

    result = classify_intent(
        {"messages": conversation("roaming", "Escalate", "why?"), "customer_id": "CUST-1005", "matched_procedure_id": "PROC-001"}
    )

    assert result["matched_procedure_id"] is None


def test_classify_explain_on_a_first_message_becomes_open_ended(monkeypatch):
    """'explain' when nothing came before is corrected in code."""
    use_jev(monkeypatch, path=choice("explain"), own_situation=noul(0.5))

    result = classify_intent({"messages": conversation("what does R012 say?"), "customer_id": "CUST-1005"})

    assert result["intent"] == "open_ended"


def test_classify_explain_is_allowed_after_an_earlier_answer(monkeypatch):
    """With an earlier exchange, 'explain' stands."""
    use_jev(monkeypatch, path=choice("explain"), own_situation=noul(0.5))

    result = classify_intent({"messages": conversation("roaming", "Escalate", "why?"), "customer_id": "CUST-1005"})

    assert result["intent"] == "explain"


# --- match_procedure, with a fake Jev ---


def test_match_offers_every_procedure_with_when_to_use_and_a_none_option(monkeypatch):
    """Each procedure is an option described by its name and when_to_use, and "none" is an option."""
    fake = use_jev(monkeypatch, procedure=choice("PROC-016"))

    match_procedure({"messages": conversation("no signal at home"), "customer_id": "CUST-1005"})

    options = fake.requests[0]["questions"]["procedure"].criteria
    assert {f"PROC-{n:03d}" for n in range(1, 21)} | {"none"} == set(options)
    assert "PROC-017" in options["PROC-016"]  # when_to_use names its neighbour


def test_match_a_confident_pick_is_walked(monkeypatch):
    """A confident pick becomes the procedure to walk."""
    use_jev(monkeypatch, procedure=choice("PROC-016", 0.9))

    result = match_procedure({"messages": conversation("no signal at home"), "customer_id": "CUST-1005"})

    assert result == {"matched_procedure_id": "PROC-016"}


@pytest.mark.parametrize("answer", [choice("none", 0.9), choice("PROC-016", 0.3)])
def test_match_none_or_an_unsure_pick_walks_nothing(monkeypatch, answer):
    """"none", or a pick below the threshold, leaves no procedure to walk."""
    use_jev(monkeypatch, procedure=answer)

    result = match_procedure({"messages": conversation("something odd"), "customer_id": "CUST-1005"})

    assert result == {"matched_procedure_id": None}


@pytest.mark.parametrize(
    "matched, expected",
    [("PROC-016", "procedural_executor"), (None, "graph_rag_chain")],
)
def test_route_after_match_falls_back_to_the_knowledge_base(matched, expected):
    """A matched procedure is walked; with no match the knowledge base answers instead."""
    assert route_after_match({"matched_procedure_id": matched}) == expected


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
    monkeypatch.setattr("agent.supervisor._walk_facts", lambda result: ("FACTS ABOUT THE WALK", ["ACT-015", "R012"]))
    messages = conversation("roaming", "Escalate", "why?")

    result = explain_answer(
        {"messages": messages, "customer_id": "CUST-1005", "troubleshooting_results": {"CUST-1005": {"x": 1}}}
    )

    shown = fake.shown[0]
    assert "FACTS ABOUT THE WALK" in shown[0].content
    assert shown[1:] == messages
    assert reply_of(result) == ("Because step 1 said NO.", ["ACT-015", "R012"])


def test_explain_uses_only_the_current_customers_result(monkeypatch):
    """With two customers in one conversation, explain is given the current customer's result only,
    and its prompt names that customer."""
    fake = FakeClaude(SimpleNamespace(text="..."))
    monkeypatch.setattr("agent.supervisor.ChatAnthropic", fake)
    given = []
    monkeypatch.setattr("agent.supervisor._walk_facts", lambda result: given.append(result) or ("", []))
    results = {"CUST-1001": {"who": "A"}, "CUST-1002": {"who": "B"}}

    explain_answer({"messages": conversation("a", "b", "why?"), "customer_id": "CUST-1002", "troubleshooting_results": results})

    assert given == [{"who": "B"}]
    assert "customer CUST-1002" in fake.shown[0][0].content


def test_explain_after_a_non_walk_answer_says_no_procedure_was_run(monkeypatch):
    """With no result for the customer (the last answer came from the knowledge base), the facts say so."""
    fake = FakeClaude(SimpleNamespace(text="It came from KB-005."))
    monkeypatch.setattr("agent.supervisor.ChatAnthropic", fake)

    result = explain_answer({"messages": conversation("fees?", "See KB-005", "why?"), "customer_id": "CUST-1005"})

    assert "no procedure was run" in fake.shown[0][0].content
    assert reply_of(result)[1] == []


# --- _walk_facts ---


def test_walk_facts_with_no_result_is_empty():
    """No result (no walk yet, or the last walk failed) means no facts and no citations."""
    assert _walk_facts(None) == ("", [])


def test_walk_facts_reads_the_action_and_its_rule_from_neo4j():
    """(Needs Neo4j.) For the real CUST-1001 walk, the facts name the procedure, each step's result
    and value, the action given, and the wording of the rule governing it."""
    result = build_procedural_executor_graph().invoke(
        {"matched_procedure_id": "PROC-001", "crm_context": get_customer("CUST-1001")}
    )["troubleshooting_results"]["CUST-1001"]

    facts, cited = _walk_facts(result)

    assert 'Procedure PROC-001 ("Customer cannot activate roaming")' in facts
    assert "- Roaming is disabled on the account: no (roaming_flag = True)" in facts
    assert 'Action given: ACT-015 "Escalate to Tier 2 - unresolved by standard checks"' in facts
    assert "R012: Tier 2 escalations require a documented troubleshooting summary" in facts
    assert cited == ["ACT-015", "R012"]
