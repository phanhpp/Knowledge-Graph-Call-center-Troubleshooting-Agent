"""The full text behind a cited ID, for expanding a source in the web app's Sources panel.

    GET /sources/{source_id}   {"id", "kind", "title", "text", "label"} (200), or {"error": ...} (404)

Fetched on demand: a reply only carries short excerpts (agent/sources.py sources_for), so saved
conversations stay small; the whole article comes from here when someone wants to read it.
"""

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from agent.sources import full_source


async def source(request: Request) -> JSONResponse:
    """The full source for the ID in the path."""
    source_id = request.path_params["source_id"]
    found = full_source(source_id)
    if found is None:
        return JSONResponse({"error": f"no source with ID {source_id!r}"}, status_code=404)
    return JSONResponse(found)


routes = [Route("/sources/{source_id}", source)]
