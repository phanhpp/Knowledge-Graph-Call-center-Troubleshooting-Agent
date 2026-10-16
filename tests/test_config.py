"""Tests for config's Neo4j check: a down Neo4j is reported, and recorded in LangSmith as an errored step."""

from langsmith import tracing_context
from neo4j.exceptions import ServiceUnavailable

import config


class _Driver:
    """Stands in for the Neo4j driver: answers, or raises like a stopped Neo4j."""

    def __init__(self, up: bool):
        self.up = up

    def verify_connectivity(self):
        if not self.up:
            raise ServiceUnavailable("Couldn't connect to localhost:7687")


def test_neo4j_down_gives_the_reason(monkeypatch):
    """The turn doesn't crash: check_neo4j gets a reason and replies "offline"."""
    monkeypatch.setattr(config, "get_driver", lambda: _Driver(up=False))

    assert config.neo4j_unavailable_reason() == f"cannot reach Neo4j at {config.get_neo4j_config().uri}: ServiceUnavailable"


def test_neo4j_up_gives_no_reason(monkeypatch):
    monkeypatch.setattr(config, "get_driver", lambda: _Driver(up=True))

    assert config.neo4j_unavailable_reason() is None


def test_neo4j_down_is_traced_as_an_errored_step(monkeypatch):
    """The connectivity check is its own traced step, and it errors when Neo4j is down, so a
    LangSmith alert on errored runs can see it (the caller still catches the error)."""
    monkeypatch.setattr(config, "get_driver", lambda: _Driver(up=False))
    runs = []

    with tracing_context(enabled="local"):  # records the run here; sends nothing
        try:
            config._verify_neo4j(langsmith_extra={"on_end": runs.append})
        except ServiceUnavailable:
            pass

    assert [run.name for run in runs] == ["neo4j_connectivity"]
    assert "ServiceUnavailable" in runs[0].error
