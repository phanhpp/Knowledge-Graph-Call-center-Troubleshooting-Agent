"""Read-only tools for the explain step's agent (agent/supervisor.py explain_answer), so an
explanation can say what a procedure, rule or article actually says, reading only what the question
needs.

The agent sees a small virtual folder, built in memory from data/ (what the graph is loaded from):

    procedures/PROC-007.md  "# PROC-007: <name>", when to use it, then every step: what it checks,
                            the action if yes (and the rules governing it), where no goes
    rules/R004.md           "# R004: <description>", then the rule's detail
    articles/KB-006.md      "# KB-006: <title>", then the article's text

No tool opens a file or touches the disk, so nothing else exists for the agent: the CRM, .env and the
code are not hidden, they are simply not there, and any other path is "not found". grep returns the
matching sentences, not whole files, so the agent can find a detail without reading everything.
"""

import re
from functools import lru_cache
from typing import Literal

from langchain_core.tools import tool

from etl.io_utils import load_json

_MAX_MATCHES = 20  # grep results per call
_MAX_PATTERN = 200  # characters
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def _procedure_file(procedure: dict, conditions: dict, actions: dict, rules_for: dict) -> str:
    """One procedure as text: what each step checks, and where yes and no lead. Without it, the agent
    guessed at steps the walk never reached ("it would then check for a credit")."""
    def action(action_id: str) -> str:
        rules = f" (rules: {', '.join(rules_for[action_id])})" if rules_for.get(action_id) else ""
        return f"{action_id} {actions[action_id]}{rules}"

    lines = [f"# {procedure['procedure_id']}: {procedure['procedure_name']}", f"When to use: {procedure['when_to_use']}", ""]
    for step in procedure["steps"]:
        no = f"go to step {step['next_step']}" if step.get("next_step") else f"{action(step['action_if_false_id'])} (the fallback)"
        lines += [f"{step['step_id']}. Check: {conditions[step['condition_id']]} ({step['condition_id']})",
                  f"   yes: {action(step['action_if_true_id'])}", f"   no: {no}"]
    return "\n".join(lines)


@lru_cache(maxsize=1)
def files() -> dict[str, str]:
    """The only files the explain agent can read: one per procedure, rule and article, built in memory."""
    conditions = {c["condition_id"]: c["description"] for c in load_json("conditions.json")}
    actions = {a["action_id"]: a["text"] for a in load_json("actions.json")}
    rules_for: dict[str, list[str]] = {}
    for r in load_json("rules.json"):
        for action_id in r["applies_to_action_ids"]:
            rules_for.setdefault(action_id, []).append(r["rule_id"])
    procedures = {f"procedures/{p['procedure_id']}.md": _procedure_file(p, conditions, actions, rules_for)
                  for p in load_json("procedures.json")}
    rules = {f"rules/{r['rule_id']}.md": f"# {r['rule_id']}: {r['description']}\n{r['detail']}"
             for r in load_json("rules.json")}
    articles = {f"articles/{a['article_id']}.md": f"# {a['article_id']}: {a['title']}\n{a['content']}"
                for a in load_json("kb_articles.json")}
    return procedures | rules | articles


def _paths(folder: str) -> list[str]:
    return [path for path in files() if folder == "all" or path.startswith(f"{folder}/")]


@tool
def ls(folder: Literal["procedures", "rules", "articles"]) -> str:
    """List the files in procedures/, rules/ or articles/: one line per file, its path and title."""
    return "\n".join(f"{path}  {files()[path].splitlines()[0].removeprefix('# ')}" for path in _paths(folder))


@tool
def read_file(path: str) -> str:
    """Read one file, e.g. "procedures/PROC-007.md", "rules/R004.md" or "articles/KB-006.md"."""
    return files().get(path, f"No such file: {path}. The files are procedures/<ID>.md, rules/<ID>.md and articles/<ID>.md (see ls).")


@tool
def grep(pattern: str, folder: Literal["procedures", "rules", "articles", "all"] = "all") -> str:
    """Find the sentences matching a regular expression (case-insensitive) in the procedures, rules
    and/or articles. Returns "path: sentence" lines, at most 20."""
    if len(pattern) > _MAX_PATTERN:
        return f"Pattern too long (at most {_MAX_PATTERN} characters)."
    try:
        regex = re.compile(pattern, re.IGNORECASE)
    except re.error as error:
        return f"Invalid pattern: {error}"
    hits = [f"{path}: {sentence}"
            for path in _paths(folder)
            for line in files()[path].splitlines()
            for sentence in _SENTENCE_END.split(line)
            if regex.search(sentence)]
    if not hits:
        return "No matches."
    more = f"\n… and {len(hits) - _MAX_MATCHES} more: narrow the pattern." if len(hits) > _MAX_MATCHES else ""
    return "\n".join(hits[:_MAX_MATCHES]) + more


TOOLS = [ls, read_file, grep]


def files_read(messages: list) -> list[str]:
    """The files an agent run actually saw, in order: each read_file that returned the file (not a
    wrong path, nor a call blocked by the call limit), and each file a grep result came from (ls
    shows only titles, so it doesn't count)."""
    asked = {call["id"]: call["args"].get("path")
             for message in messages if message.type == "ai"
             for call in (getattr(message, "tool_calls", None) or []) if call["name"] == "read_file"}
    seen: list[str] = []
    for message in messages:
        if message.type != "tool":
            continue
        if message.name == "read_file":
            path = asked.get(message.tool_call_id)
            if path in files() and message.content == files()[path]:
                seen.append(path)
        elif message.name == "grep":
            seen += [line.split(": ", 1)[0] for line in str(message.content).splitlines() if line.split(": ", 1)[0] in files()]
    return list(dict.fromkeys(seen))
