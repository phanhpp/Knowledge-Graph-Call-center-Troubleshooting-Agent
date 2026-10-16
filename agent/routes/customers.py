"""A customer's CRM record, for the web app's customer card.

    GET /customers/{customer_id}   the record (200), or {"error": ...} (404) for an unknown ID

A route rather than graph state: `crm_context` is set only on troubleshooting turns and never
cleared, so after switching customer with a general question it still holds the previous
customer's record. This reads the CRM fresh every time, the same way the graph does
(mock_crm/crm_store.py).
"""

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from mock_crm.crm_store import CustomerNotFoundError, get_customer


async def customer(request: Request) -> JSONResponse:
    """The record for the customer ID in the path."""
    customer_id = request.path_params["customer_id"]
    try:
        return JSONResponse(get_customer(customer_id))
    except CustomerNotFoundError:
        return JSONResponse({"error": f"no customer with ID {customer_id!r}"}, status_code=404)


routes = [Route("/customers/{customer_id}", customer)]
