"""Extra HTTP routes served alongside the graph, on the same LangGraph Server.

Registered as `"http": {"app": "./agent/http_app.py:app"}` in `langgraph.json`. That setting takes
ONE app, so every custom route hangs off this Starlette object; the routes live in agent/routes/:

    routes/customers.py   GET /customers/{id}: the CRM record, for the customer card
    routes/feedback.py    thumbs up/down on a reply, written to LangSmith
    routes/sources.py     GET /sources/{id}: a cited ID's full text, for expanding a source

They exist for the same reason: the browser can't do them itself. Feedback needs the LangSmith
key, the CRM and the source data live on the server, and a graph endpoint would start a run just
to answer. The browser reaches them like the built-in routes, through the web app's proxy route.

Starlette rather than FastAPI: the LangGraph server already ships it, and these routes need no
validation library.
"""

from starlette.applications import Starlette

from agent.routes import customers, feedback, sources

app = Starlette(routes=[*customers.routes, *feedback.routes, *sources.routes])
