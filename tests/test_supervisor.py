"""Unit tests for the supervisor's nodes and routing.

Claude and Jev are replaced by fakes, so these need no API keys. Two tests (marked below) read Neo4j. The
full graph, with real Claude and Neo4j calls, is covered in tests/e2e/.
"""

from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableLambda
from langchain_typesafe import ChoiceAnswer, ClassifierResponse
from langchain_typesafe.client import (
    TypeSafeAPIConnectionError,
    TypeSafeAPITimeoutError,
    TypeSafeAuthenticationError,
    TypeSafeInternalServerError,
    TypeSafeRateLimitError,
)
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END

from agent.procedural_executor import build_procedural_executor_graph
from agent.utils import consultant_message, reply_of
from agent.supervisor import (
    NEO4J_DOWN,
    NOTHING_TO_EXPLAIN,
    WHICH_CUSTOMER,
    check_customer,
    check_neo4j,
    load_crm,
    _explain_history,
    _rule_wording,
    _JEV_TIMEOUT,
    _classifier,
    _with_jev_retry,
    build_supervisor_graph,
    classify_intent,
    explain_answer,
    match_procedure,
    route,
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


def choice(label: str, probability: float = 0.9) -> ChoiceAnswer:
    """A Choice answer picking `label`, with that probability (and the same confidence)."""
    return ChoiceAnswer(type="choice", choice=label, probabilities={label: probability}, confidence=probability)


def conversation(*texts):
    """Alternating consultant / agent messages, starting with the consultant."""
    return [HumanMessage(t) if i % 2 == 0 else AIMessage(t) for i, t in enumerate(texts)]


# --- check_customer: checks a named ID, and only a named one ---


def test_check_customer_makes_a_named_known_customer_current():
    """A message naming a known customer: the ID is checked and becomes the current customer."""
    command = check_customer({"messages": [consultant_message("roaming", "CUST-1001")]})

    assert command.goto == "check_neo4j"
    assert command.update == {"error": None, "customer_id": "CUST-1001"}


def test_check_customer_stops_on_an_unknown_id_with_a_reply():
    """A named ID that is not in the CRM ends the turn with a reply naming it, and sets error."""
    command = check_customer({"messages": [consultant_message("roaming", "CUST-9999")]})

    assert command.goto == END
    assert "CUST-9999" in command.update["error"]
    text, citations = reply_of(command.update)
    assert "CUST-9999" in text and "matching procedure" not in text
    assert citations == []


def test_check_customer_does_not_check_a_message_naming_no_customer():
    """No ID, nothing to check (it may be a general question); the current customer is kept."""
    command = check_customer({"messages": [consultant_message("what is the SIM fee?")], "customer_id": "CUST-1001"})

    assert command.goto == "check_neo4j"
    assert command.update == {"error": None}


def test_check_customer_clears_the_previous_turns_error():
    """A stale error from the last turn must not linger into this one."""
    command = check_customer({"messages": [consultant_message("hi")], "error": "old"})
    assert command.update["error"] is None


# --- load_crm: the CRM record, for troubleshooting only ---


def test_load_crm_loads_the_current_customers_record():
    """The current customer's record goes into crm_context, and the walk goes on."""
    command = load_crm({"messages": [consultant_message("roaming", "CUST-1001")], "customer_id": "CUST-1001"})

    assert command.goto == "match_procedure"
    assert command.update["crm_context"]["customer_id"] == "CUST-1001"
    assert "messages" not in command.update  # already stamped


def test_load_crm_stamps_a_follow_up_with_the_current_customer():
    """A troubleshooting message naming no customer is about the current one: it is stamped with
    them, keeping its id, so the saved copy is replaced rather than duplicated."""
    message = HumanMessage("she is abroad now", id="m1")

    command = load_crm({"messages": [message], "customer_id": "CUST-1005"})

    (stamped,) = command.update["messages"]
    assert stamped.id == "m1"
    assert stamped.additional_kwargs["customer_id"] == "CUST-1005"


def test_load_crm_with_no_customer_asks_which_one():
    """Troubleshooting needs a customer: with none named yet, it asks instead of guessing."""
    command = load_crm({"messages": [consultant_message("roaming not working")]})

    assert command.goto == END
    assert reply_of(command.update).text == WHICH_CUSTOMER
    assert command.update["error"]


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

    result = build_supervisor_graph().invoke({"messages": [consultant_message("roaming", "CUST-1005")]})

    assert reply_of(result).text == NEO4J_DOWN
    assert "intent" not in result


# --- route ---


@pytest.mark.parametrize(
    "intent, expected",
    [
        ("troubleshooting", "load_crm"),
        ("open_ended", "graph_rag_chain"),
        ("explain", "explain_answer"),
    ],
)
def test_route_picks_the_path_from_the_intent(intent, expected):
    """The classified intent decides the path, with no model involved."""
    assert route({"intent": intent}) == expected


# --- graph wiring ---


def test_supervisor_graph_contains_every_node():
    """The compiled graph has the customer and Neo4j checks and all three paths."""
    nodes = set(build_supervisor_graph().get_graph().nodes)
    expected = {
        "check_customer", "check_neo4j", "classify_intent", "load_crm", "match_procedure",
        "procedural_executor", "graph_rag_chain", "explain_answer",
    }
    assert expected <= nodes


def test_unknown_customer_is_answered_in_the_messages_without_calling_claude():
    """Through the whole graph: an unknown customer gets the message as the agent's reply."""
    result = build_supervisor_graph().invoke({"messages": [consultant_message("roaming", "CUST-9999")]})

    assert [m.type for m in result["messages"]] == ["human", "ai"]
    assert "CUST-9999" in result["messages"][-1].content
    assert "intent" not in result


# --- retrying Jev when it's busy ---


def flaky_jev(*errors):
    """A stand-in Jev that raises each of `errors` in turn, then answers "ok". Returns it and its call log."""
    calls = []

    def call(_request):
        calls.append(1)
        if len(calls) <= len(errors):
            raise errors[len(calls) - 1]
        return "ok"

    return RunnableLambda(call), calls


@pytest.fixture
def no_wait(monkeypatch):
    """Retry without waiting, so the tests stay fast."""
    monkeypatch.setattr("agent.supervisor._JEV_WAIT", {"initial": 0, "max": 0, "jitter": 0})


def test_busy_jev_is_retried_until_it_answers(no_wait):
    """529 Overloaded, then 429 Too Many Requests, then an answer: the call succeeds on the 3rd try."""
    jev, calls = flaky_jev(TypeSafeInternalServerError(529, None, {}), TypeSafeRateLimitError(429, None, {}))
    assert _with_jev_retry(jev).invoke({}) == "ok"
    assert len(calls) == 3


def test_jev_busy_three_times_gives_up(no_wait):
    """After 3 tries the error reaches the caller, so the turn fails instead of waiting forever."""
    busy = TypeSafeInternalServerError(529, None, {})
    jev, calls = flaky_jev(busy, busy, busy)
    with pytest.raises(TypeSafeInternalServerError):
        _with_jev_retry(jev).invoke({})
    assert len(calls) == 3


@pytest.mark.parametrize("error", [
    TypeSafeAPIConnectionError("Unable to connect to the TypeSafe API."),  # e.g. dropped while connecting
    TypeSafeAPITimeoutError(10.0),  # no answer in time
])
def test_a_failed_connection_to_jev_is_retried(no_wait, error):
    """Jev never answered, so trying again is safe: the second try succeeds."""
    jev, calls = flaky_jev(error)
    assert _with_jev_retry(jev).invoke({}) == "ok"
    assert len(calls) == 2


def test_jev_client_gives_up_on_a_call_after_10_seconds(monkeypatch):
    """The timeout reaches the real client, so 3 tries can't keep a consultant waiting 90 s."""
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    _classifier.cache_clear()
    try:
        assert _JEV_TIMEOUT == 10
        assert _classifier().bound.timeout == _JEV_TIMEOUT
    finally:
        _classifier.cache_clear()  # don't leave a client with the fake key for other tests


def test_a_bad_jev_key_is_not_retried(no_wait):
    """A wrong key fails the same way every time, so it fails at once."""
    jev, calls = flaky_jev(TypeSafeAuthenticationError(401, None, {}))
    with pytest.raises(TypeSafeAuthenticationError):
        _with_jev_retry(jev).invoke({})
    assert len(calls) == 1


# --- classify_intent, with a fake Jev ---


def test_classify_shows_jev_only_the_latest_message_and_not_the_procedures(monkeypatch):
    """Jev is given the new message only (not the conversation), and no procedure list."""
    fake = use_jev(monkeypatch, path=choice("open_ended"))
    messages = conversation("roaming", "Escalate", "why?")

    classify_intent({"messages": messages, "customer_id": "CUST-1005"})

    request = fake.requests[0]
    assert request["state"] == messages[-1]
    assert list(request["questions"]) == ["path"]
    assert "PROC-" not in str(request["questions"])


def test_classify_confident_troubleshooting_leaves_the_procedure_to_match_procedure(monkeypatch):
    """A confident troubleshooting answer stands, with no procedure picked yet."""
    use_jev(monkeypatch, path=choice("troubleshooting", 0.9))

    result = classify_intent({"messages": conversation("no signal at home"), "customer_id": "CUST-1005"})

    assert result == {"intent": "troubleshooting", "matched_procedure_id": None}


def test_classify_troubleshooting_with_low_probability_becomes_open_ended(monkeypatch):
    """When Jev is unsure of the path, the message goes to the knowledge base, not a walk."""
    use_jev(monkeypatch, path=choice("troubleshooting", 0.3))

    result = classify_intent({"messages": conversation("cancel?"), "customer_id": "CUST-1005"})

    assert result["intent"] == "open_ended"


def test_classify_threshold_uses_the_troubleshooting_probability_not_confidence(monkeypatch):
    """Confidence measures how concentrated the distribution is; the decision uses the chosen
    label's probability. Here confidence is high but troubleshooting is only 40% likely."""
    spread = ChoiceAnswer(
        type="choice", choice="troubleshooting", confidence=0.9,
        probabilities={"troubleshooting": 0.4, "open_ended": 0.35, "explain": 0.25},
    )
    use_jev(monkeypatch, path=spread)

    result = classify_intent({"messages": conversation("cancel?"), "customer_id": "CUST-1001"})

    assert result["intent"] == "open_ended"


def test_classify_clears_a_procedure_from_an_earlier_turn(monkeypatch):
    """matched_procedure_id is reset every turn; only match_procedure sets it."""
    use_jev(monkeypatch, path=choice("explain"))

    result = classify_intent(
        {"messages": conversation("roaming", "Escalate", "why?"), "customer_id": "CUST-1005", "matched_procedure_id": "PROC-001"}
    )

    assert result["matched_procedure_id"] is None


def test_classify_explain_on_a_first_message_becomes_open_ended(monkeypatch):
    """'explain' when nothing came before is corrected in code."""
    use_jev(monkeypatch, path=choice("explain"))

    result = classify_intent({"messages": conversation("what does R012 say?"), "customer_id": "CUST-1005"})

    assert result["intent"] == "open_ended"


def test_classify_explain_is_allowed_after_an_earlier_answer(monkeypatch):
    """With an earlier exchange, 'explain' stands."""
    use_jev(monkeypatch, path=choice("explain"))

    result = classify_intent({"messages": conversation("roaming", "Escalate", "why?"), "customer_id": "CUST-1005"})

    assert result["intent"] == "explain"


# --- match_procedure, with a fake Jev ---


def test_match_offers_every_procedure_with_when_to_use_and_a_none_option(monkeypatch):
    """Each procedure is an option described by its name and when_to_use, and "none" is an option."""
    fake = use_jev(monkeypatch, procedure=choice("PROC-016"))

    messages = conversation("roaming", "Escalate", "no signal at home")
    match_procedure({"messages": messages, "customer_id": "CUST-1005"})

    assert fake.requests[0]["state"] == messages[-1]  # the new message only
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


def said(text, customer):
    """A consultant message, stamped with its customer (as the Slack bot or load_crm does)."""
    return HumanMessage(text, additional_kwargs={"customer_id": customer})


def answered(text, *citations):
    """An agent reply with its citations, as reply() makes it."""
    return AIMessage(text, additional_kwargs={"citations": list(citations)})


A_THEN_B_THEN_A = [
    said("customer cannot activate roaming", "CUST-1001"),
    answered("Escalate to Tier 2", "ACT-015", "R012"),
    said("customer cannot activate roaming", "CUST-1005"),
    answered("The device does not support roaming", "ACT-027"),
    said("why did you say escalate", "CUST-1001"),
]


def test_explain_shows_claude_each_message_labelled_with_its_customer(monkeypatch):
    """The bug this fixes: A, then B, then a "why?" about A. Every message tells Claude whose it
    was, replies carry their sources, and the prompt names the current customer."""
    fake = FakeClaude(SimpleNamespace(text="Because all three checks said no."))
    monkeypatch.setattr("agent.supervisor.ChatAnthropic", fake)
    monkeypatch.setattr("agent.supervisor._rule_wording", lambda ids: "- R012: Tier 2 needs a summary.")

    result = explain_answer({"messages": A_THEN_B_THEN_A, "customer_id": "CUST-1001"})

    system, *history = fake.shown[0]
    assert [m.content for m in history] == [
        "[CUST-1001] customer cannot activate roaming",
        "[reply about CUST-1001] Escalate to Tier 2\n\nSources: ACT-015, R012",
        "[CUST-1005] customer cannot activate roaming",
        "[reply about CUST-1005] The device does not support roaming\n\nSources: ACT-027",
        "[CUST-1001] why did you say escalate",
    ]
    assert "customer CUST-1001" in system.content
    assert "R012: Tier 2 needs a summary." in system.content


def test_explain_cites_nothing(monkeypatch):
    """An explanation is not a new source; the answer it explains already showed its sources."""
    monkeypatch.setattr("agent.supervisor.ChatAnthropic", FakeClaude(SimpleNamespace(text="...")))
    monkeypatch.setattr("agent.supervisor._rule_wording", lambda ids: "")

    result = explain_answer({"messages": A_THEN_B_THEN_A, "customer_id": "CUST-1001"})

    assert reply_of(result).citations == []


def test_explain_with_no_cited_rule_says_none(monkeypatch):
    """With no rule cited in the window, the prompt says so, so nothing else gets called a rule."""
    fake = FakeClaude(SimpleNamespace(text="..."))
    monkeypatch.setattr("agent.supervisor.ChatAnthropic", fake)
    messages = [said("roaming", "CUST-1005"), answered("Device not compatible", "ACT-027"), said("what does that rule say?", "CUST-1005")]
    monkeypatch.setattr("agent.supervisor._rule_wording", lambda ids: "")

    explain_answer({"messages": messages, "customer_id": "CUST-1005"})

    assert "Rules cited in the conversation:\n(none)" in fake.shown[0][0].content


# --- _explain_history and _rule_wording ---


def test_explain_history_keeps_the_last_messages_and_starts_on_a_consultant_message():
    """Only the last `limit` messages are kept, and a reply cut off from its question is dropped
    (Claude needs the conversation to start with the consultant)."""
    history, cited = _explain_history(A_THEN_B_THEN_A, limit=4)

    assert [m.content for m in history] == [
        "[CUST-1005] customer cannot activate roaming",
        "[reply about CUST-1005] The device does not support roaming\n\nSources: ACT-027",
        "[CUST-1001] why did you say escalate",
    ]
    assert cited == ["ACT-027"]


def test_explain_history_leaves_a_message_with_no_customer_id_unlabelled():
    """A message with no customer ID may be a general question or a follow-up like "why?" about a
    customer's answer, so it and its reply get no label (a "[general]" label once misled Claude)."""
    messages = [*A_THEN_B_THEN_A[:2], HumanMessage("why did it end up at Tier 2?"), AIMessage("All three checks said no")]

    history, _ = _explain_history(messages, limit=10)

    assert [m.content for m in history[2:]] == ["why did it end up at Tier 2?", "All three checks said no"]


def test_explain_history_does_not_label_a_saved_reply_twice():
    """A reply saved with a label in its text (copied by Claude, or added by an older version) is
    labelled once, from its stamp."""
    messages = [
        said("why?", "CUST-1001"),
        AIMessage("[reply about CUST-1001] Because all three checks said no"),
        HumanMessage("and the fee?"),
        AIMessage("[reply, general] [reply, general] It is waived once a year"),
    ]

    history, _ = _explain_history(messages, limit=10)

    assert [m.content for m in history] == [
        "[CUST-1001] why?",
        "[reply about CUST-1001] Because all three checks said no",
        "and the fee?",
        "It is waived once a year",
    ]


def test_explain_removes_a_label_claude_copies_into_its_reply(monkeypatch):
    """Earlier replies in the history start with "[reply about ...]", and Claude sometimes starts its
    own the same way; the label must not reach the consultant or the saved conversation."""
    fake = FakeClaude(SimpleNamespace(text="[reply about CUST-1001] Because all three checks said no."))
    monkeypatch.setattr("agent.supervisor.ChatAnthropic", fake)
    monkeypatch.setattr("agent.supervisor._rule_wording", lambda ids: "")

    result = explain_answer({"messages": A_THEN_B_THEN_A, "customer_id": "CUST-1001"})

    assert reply_of(result).text == "Because all three checks said no."


def test_explain_focuses_on_a_named_customer_or_on_what_the_question_refers_to(monkeypatch):
    """A question naming a customer is answered about that customer; one naming none gets no
    customer forced on it (it is about the answer it refers to)."""
    fake = FakeClaude(SimpleNamespace(text="..."))
    monkeypatch.setattr("agent.supervisor.ChatAnthropic", fake)
    monkeypatch.setattr("agent.supervisor._rule_wording", lambda ids: "")
    earlier = [said("roaming", "CUST-1001"), answered("Escalate", "ACT-015")]

    explain_answer({"messages": [*earlier, said("why?", "CUST-1001")]})
    explain_answer({"messages": [*earlier, HumanMessage("why did you cite KB-006?")]})

    named, unnamed = fake.shown[0][0].content, fake.shown[1][0].content
    assert "The question is about customer CUST-1001" in named
    assert "names no customer" in unnamed and "CUST-1001" not in unnamed


def test_rule_wording_with_nothing_cited_needs_no_neo4j():
    """No IDs: no query, empty wording."""
    assert _rule_wording([]) == ""


def test_rule_wording_reads_only_the_rules_from_neo4j():
    """(Needs Neo4j.) Of the cited IDs, only rules have wording; actions and articles match nothing."""
    wording = _rule_wording(["ACT-015", "R012", "KB-005"])

    assert wording == "- R012: Tier 2 escalations require a documented troubleshooting summary. " \
                      "Every Tier 2 escalation must include a written summary of the steps already attempted"


def test_a_walk_saves_its_reply_but_not_its_step_trace(monkeypatch):
    """(Needs Neo4j.) Through the whole graph with a saved conversation: the walk's reply is saved,
    and step_trace, private to the walk, is not."""
    use_jev(monkeypatch, path=choice("troubleshooting"), procedure=choice("PROC-001"))
    graph = build_supervisor_graph(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "t"}}

    graph.invoke({"messages": [consultant_message("customer cannot activate roaming", "CUST-1001")]}, config)

    saved = graph.get_state(config).values
    assert "step_trace" not in saved
    assert saved["messages"][-1].additional_kwargs["citations"] == ["ACT-015", "R012"]
    assert saved["messages"][0].additional_kwargs["customer_id"] == "CUST-1001"
