"""Thumbs up/down on an answer, written to LangSmith.

This cannot live in the graph. `reply()` (agent/utils.py) records each reply's run id on the
message while the turn runs, and the turn then finishes; the vote arrives later — seconds
later, or after a reload, or never — when no run is executing. And the only other
way into this process is the graph endpoints, every one of which STARTS a run, so using
one to record a vote would invoke the model just to write a row.

    GET  /feedback/{thread_id}/votes?run_ids=<a,b,c>
         Votes already recorded, so a reload shows them instead of offering a second
         click that would write a duplicate row.

    POST /feedback/{thread_id}
         One vote. Calls the LangSmith SDK's `create_feedback`, which is why the browser
         cannot do this itself: it would need the API key.

The split that matters: the browser says WHAT happened, never WHERE to write it. The
metric name and the project UUID are both resolved here, so a caller cannot invent metric
names or file feedback into another project.
"""

import uuid

from langsmith import AsyncClient
from langsmith.utils import get_tracer_project
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route


FEEDBACK_KEY = "user_score"
MAX_COMMENT_CHARS = 1000
# One page of LangSmith feedback; longer threads simply show the first page as voted.
MAX_FEEDBACK_LOOKUP = 100

_feedback_client: AsyncClient | None = None
_project_id: str | None = None


def _get_feedback_client() -> AsyncClient:
    """One client per process. Constructing it reads LANGSMITH_API_KEY."""
    global _feedback_client
    if _feedback_client is None:
        _feedback_client = AsyncClient()
    return _feedback_client


async def _project_uuid() -> str:
    """The tracing project's UUID, which `create_feedback` calls `session_id`.

    "Session" is LangSmith's old word for a project, so this is NOT the conversation
    thread. It is the same value for every user and every thread, which is why one
    module-level variable is enough: the first vote after a restart pays for one lookup
    and every vote after it reads the variable.

    The name comes from `get_tracer_project()` rather than os.environ, because
    HOSTED_LANGSERVE_PROJECT_NAME outranks LANGSMITH_PROJECT — reading the env var
    directly gives the wrong project on a deployment, which is the one place it matters.
    """
    global _project_id
    if _project_id is None:
        project = await _get_feedback_client().read_project(
            project_name=get_tracer_project()
        )
        _project_id = str(project.id)
    return _project_id


def _valid_uuid(value: object) -> str | None:
    """Return `value` if it is a UUID string, else None. The browser supplies these."""
    if not isinstance(value, str):
        return None
    try:
        uuid.UUID(value)
    except ValueError:
        return None
    return value


async def feedback_votes(request: Request) -> JSONResponse:
    """Votes already recorded for these runs, so the page can show them after a reload.

    Without this the buttons come back looking unvoted, and a second click writes a
    SECOND feedback row on the same run — LangSmith then holds both a 1 and a 0 with
    nothing saying which is current. A deterministic `feedback_id` does not solve it:
    LangSmith dedupes a repeat id rather than overwriting, so the changed vote is
    dropped while the write still reports success. Verified.

    `run_ids` is a comma-separated list; anything that is not a UUID is skipped.
    """
    raw = (request.query_params.get("run_ids") or "").split(",")
    run_ids = [v for v in (_valid_uuid(r.strip()) for r in raw if r.strip()) if v]
    if not run_ids:
        return JSONResponse({"votes": {}})

    votes: dict[str, dict] = {}
    try:
        async for item in _get_feedback_client().list_feedback(
            run_ids=run_ids[:MAX_FEEDBACK_LOOKUP], feedback_key=[FEEDBACK_KEY]
        ):
            if item.score is None:
                continue
            # Several rows can exist for one run (a vote written before this route
            # existed, say). The newest is the one the user last chose.
            current = votes.get(str(item.run_id))
            if current is None or str(item.created_at) > current["created_at"]:
                votes[str(item.run_id)] = {
                    "score": int(item.score),
                    "comment": item.comment,
                    "created_at": str(item.created_at),
                }
    except Exception:
        # A reload that cannot reach LangSmith should still render the chat. Showing
        # the buttons as unvoted is a better failure than showing nothing.
        return JSONResponse({"votes": {}})

    return JSONResponse({"votes": {k: {"score": v["score"]} for k, v in votes.items()}})


async def feedback(request: Request) -> JSONResponse:
    """Record a thumbs up or down against the run that produced an answer.

    The browser sends what happened — which run, which way the thumb pointed — and never
    where to write it: the metric name and the project are both resolved here. Without
    that split, a caller could invent metric names or file feedback into another project.

    Known limitation: any well-formed run id is accepted, so this can be pointed at
    another run in the same workspace. The deployment sits behind access control. To
    close it properly, check the posted run_id against the `run_id`s on this thread's replies
    — the thread id is already in the path for that reason.
    """
    thread_id = request.path_params["thread_id"]
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "expected a JSON body"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "expected a JSON object"}, status_code=400)

    run_id = _valid_uuid(body.get("run_id"))
    if run_id is None:
        return JSONResponse({"error": "run_id must be a UUID"}, status_code=400)
    # Defaults to run_id: they are equal for a root run, and sending it is what lets
    # LangSmith batch and background the write instead of blocking this request.
    trace_id = _valid_uuid(body.get("trace_id")) or run_id

    # `is True`/`is False` would also pass a bool through `in (0, 1)`, so check the type.
    score = body.get("score")
    if isinstance(score, bool) or score not in (0, 1):
        return JSONResponse({"error": "score must be 0 or 1"}, status_code=400)

    comment = body.get("comment")
    comment = comment.strip()[:MAX_COMMENT_CHARS] if isinstance(comment, str) else ""

    try:
        created = await _get_feedback_client().create_feedback(
            run_id=run_id,
            trace_id=trace_id,
            session_id=await _project_uuid(),
            key=FEEDBACK_KEY,
            score=score,
            comment=comment or None,
            source_info={"thread_id": thread_id, "message_id": body.get("message_id")},
        )
    except Exception as error:
        # A vote that vanishes silently is worse than one that reports a problem: the UI
        # reverts the button so the user can see it did not take.
        return JSONResponse(
            {"error": "could not record feedback", "detail": str(error)},
            status_code=502,
        )

    return JSONResponse({"ok": True, "feedbackId": str(created.id)})


routes = [
    Route("/feedback/{thread_id}", feedback, methods=["POST"]),
    Route("/feedback/{thread_id}/votes", feedback_votes),
]
