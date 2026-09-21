"""Skip guards shared by the end-to-end tests. A test that needs a live API key is skipped when
that key is missing, so the suite still runs on a machine without keys."""

import os

import pytest

import config  # noqa: F401  (importing it loads .env, so the keys below are visible)

requires_anthropic_key = pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="needs a live Claude call (classify_intent)",
)

requires_openai_key = pytest.mark.skipif(
    not os.environ.get("OPENAI_API_KEY"),
    reason="needs a live OpenAI call (query embedding)",
)
