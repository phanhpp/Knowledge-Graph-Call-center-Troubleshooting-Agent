"""Tests for the custom HTTP routes (agent/http_app.py): the customer card and feedback.

Run on Starlette's TestClient, so no server and no LangSmith call: the feedback routes get a fake
client that records what would have been written.
"""

import uuid
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from agent.http_app import app
from agent.routes import feedback

RUN = str(uuid.uuid4())


@pytest.fixture
def client():
    return TestClient(app)


class _FakeLangSmith:
    """Stands in for langsmith.AsyncClient: records feedback, serves it back for /votes."""

    def __init__(self, existing=()):
        self.created = []
        self.existing = list(existing)

    async def create_feedback(self, **kwargs):
        self.created.append(kwargs)
        return SimpleNamespace(id=uuid.uuid4())

    async def read_project(self, project_name):
        return SimpleNamespace(id=uuid.UUID(int=1))

    async def list_feedback(self, run_ids, feedback_key):
        for item in self.existing:
            yield item


@pytest.fixture
def langsmith(monkeypatch):
    fake = _FakeLangSmith()
    monkeypatch.setattr(feedback, "_feedback_client", fake)
    monkeypatch.setattr(feedback, "_project_id", None)
    return fake


# --- customers ---


def test_customer_returns_the_crm_record(client):
    response = client.get("/customers/CUST-1001")

    assert response.status_code == 200
    assert response.json()["customer_id"] == "CUST-1001"
    assert "roaming_flag" in response.json()


def test_unknown_customer_is_404(client):
    response = client.get("/customers/CUST-9999")

    assert response.status_code == 404
    assert "CUST-9999" in response.json()["error"]


# --- sources ---


def test_source_returns_the_full_text(client):
    response = client.get("/sources/KB-006")

    assert response.status_code == 200
    assert response.json()["title"] == "SIM Replacement Process and Fees"
    assert len(response.json()["text"]) > 300


def test_unknown_source_is_404(client):
    response = client.get("/sources/KB-999")

    assert response.status_code == 404
    assert "KB-999" in response.json()["error"]


# --- feedback ---


def test_a_vote_is_written_with_the_fixed_metric_name(client, langsmith):
    """The browser says which run and which way; the server decides the metric name."""
    response = client.post("/feedback/t1", json={"run_id": RUN, "score": 1, "message_id": "m1"})

    assert response.status_code == 200
    (written,) = langsmith.created
    assert written["run_id"] == RUN and written["score"] == 1
    assert written["key"] == feedback.FEEDBACK_KEY
    assert written["source_info"] == {"thread_id": "t1", "message_id": "m1"}


@pytest.mark.parametrize(
    "body",
    [
        {"run_id": "not-a-uuid", "score": 1},
        {"run_id": RUN, "score": 2},
        {"run_id": RUN, "score": True},  # a bool would pass `in (0, 1)`, so it is checked by type
        {"score": 1},
    ],
)
def test_bad_votes_are_rejected(client, langsmith, body):
    assert client.post("/feedback/t1", json=body).status_code == 400
    assert langsmith.created == []


def test_votes_with_no_run_ids_is_empty(client, langsmith):
    assert client.get("/feedback/t1/votes").json() == {"votes": {}}


def test_votes_returns_the_newest_vote_per_run(client, langsmith):
    """A changed vote is a new row; the newest is the one the user last chose."""
    langsmith.existing = [
        SimpleNamespace(run_id=RUN, score=1, comment=None, created_at="2026-10-16T10:00:00"),
        SimpleNamespace(run_id=RUN, score=0, comment=None, created_at="2026-10-16T10:05:00"),
    ]

    assert client.get(f"/feedback/t1/votes?run_ids={RUN}").json() == {"votes": {RUN: {"score": 0}}}
