"""Shared config loading — env vars via .env, never hardcoded credentials.

Only NEO4J_PASSWORD is required with no default; NEO4J_URI/USERNAME/DATABASE
fall back to the local Neo4j Desktop defaults already confirmed in this repo.
"""

from dataclasses import dataclass
from functools import lru_cache
import os

from dotenv import load_dotenv

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
