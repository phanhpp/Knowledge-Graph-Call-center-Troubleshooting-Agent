"""Shared config loading — env vars via .env, never hardcoded credentials.

Only NEO4J_PASSWORD is required with no default; NEO4J_URI/USERNAME/DATABASE
fall back to the local Neo4j Desktop defaults already confirmed in this repo.
"""

from dataclasses import dataclass
from functools import lru_cache
import os
import sqlite3
from typing import Optional

from dotenv import load_dotenv
from langsmith import traceable

load_dotenv()


class MissingConfigError(RuntimeError):
    """Raised when a required environment variable is missing."""


def _require(name: str) -> str:
    """Return the env var's value, or raise MissingConfigError if unset/empty."""
    value = os.environ.get(name)
    if not value:
        raise MissingConfigError(
            f"Missing required env var '{name}' — add it to .env (see .env.example)."
        )
    return value


@dataclass(frozen=True)
class Neo4jConfig:
    uri: str
    username: str
    password: str
    database: str


def get_neo4j_config() -> Neo4jConfig:
    """Build Neo4jConfig from env vars, defaulting uri/username/database to
    the local Neo4j Desktop setup and requiring only NEO4J_PASSWORD."""
    return Neo4jConfig(
        uri=os.environ.get("NEO4J_URI", "bolt://localhost:7687"),
        username=os.environ.get("NEO4J_USERNAME", "neo4j"),
        password=_require("NEO4J_PASSWORD"),
        database=os.environ.get("NEO4J_DATABASE", "neo4j"),
    )


@lru_cache(maxsize=1)
def get_driver():
    """One Driver instance for the whole process — never create one per call/request."""
    from neo4j import GraphDatabase

    cfg = get_neo4j_config()
    return GraphDatabase.driver(cfg.uri, auth=(cfg.username, cfg.password))


@traceable(name="neo4j_connectivity", run_type="tool")
def _verify_neo4j() -> None:
    """Ask Neo4j to answer; raises if it can't (ServiceUnavailable, AuthError, ...).

    Traced on its own so that when it raises, LangSmith records this step as an errored run (which
    an alert can see), even though the caller catches the error and the turn ends normally.
    """
    get_driver().verify_connectivity()


def neo4j_unavailable_reason() -> Optional[str]:
    """None if Neo4j answers; otherwise a short reason it doesn't (not running, wrong password, ...).

    Asks the server for real (verify_connectivity), because creating the driver never connects.
    """
    try:
        _verify_neo4j()
    except Exception as e:  # ServiceUnavailable (not running), AuthError (bad password), ...
        return f"cannot reach Neo4j at {get_neo4j_config().uri}: {type(e).__name__}"
    return None


_OWN_LOGGERS = ("agent", "slack_bot")  # this project's packages


def setup_logging() -> None:
    """Print log lines to the terminal: this project's at LOG_LEVEL, every library's at INFO.

    LOG_LEVEL (default DEBUG) applies only to this project's own loggers. DEBUG shows everything,
    including each procedure step and Jev's confidence numbers; INFO shows one line per decision
    (new turn, intent, procedure, final action). Libraries (Neo4j, httpx, Slack) stay at INFO, so
    DEBUG here doesn't flood the terminal with their internals.
    """
    import logging

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-5s %(name)s: %(message)s")
    level = os.environ.get("LOG_LEVEL", "DEBUG").upper()
    for name in _OWN_LOGGERS:
        logging.getLogger(name).setLevel(level)


@lru_cache(maxsize=1)
def get_checkpointer():
    """One SQLite-backed checkpointer for the whole process, so saved conversations survive a restart.

    The file is CHECKPOINT_DB (default checkpoints.db). check_same_thread=False is safe because
    SqliteSaver guards the shared connection with its own lock, and the Slack handlers run on several
    threads. Give the graph a thread_id per conversation whenever it is compiled with this.
    """
    from langgraph.checkpoint.sqlite import SqliteSaver

    # `or`, not a default argument: a blank CHECKPOINT_DB= line would otherwise open a throwaway
    # in-memory database and silently lose every conversation.
    conn = sqlite3.connect(os.environ.get("CHECKPOINT_DB") or "checkpoints.db", check_same_thread=False)
    return SqliteSaver(conn)
