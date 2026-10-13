"""Shared test setup: send the tests' traces to their own LangSmith project.

The `kg` project holds real conversations, and the online evaluators score them. Test runs there
would skew the charts and cost Jev calls, so tests trace to `kg-tests` instead. This runs before
any test imports config, and config's load_dotenv() never overwrites a variable already set.
"""

import os

os.environ["LANGSMITH_PROJECT"] = "kg-tests"
