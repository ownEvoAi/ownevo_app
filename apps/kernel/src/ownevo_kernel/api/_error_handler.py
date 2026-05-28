"""Global exception handler for the kernel API.

FastAPI's default behaviour for an uncaught exception inside a route is
to surface a bare Starlette 500 with the literal body
``Internal Server Error`` and no structure. There is nothing in the
response body that an operator can correlate with the traceback emitted
to stdout, and the traceback itself is the plain ``logging`` default
format — useless for grepping in production.

This handler:

  * mints a short ``error_id`` (or reuses the request id when the
    request-id middleware ran);
  * logs the full traceback at ERROR with structured fields
    (``request_id``, ``error_id``, ``method``, ``path``,
    ``exc_class``) so a JSON log shipper has something to index on;
  * returns a small JSON body that carries the same ``error_id`` so the
    operator who sees the customer's screenshot can grep the logs by id.

``HTTPException`` and ``RequestValidationError`` are left to FastAPI's
own handlers — they already return structured 4xx bodies.
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING

from fastapi.responses import JSONResponse

from ._request_id import REQUEST_ID_HEADER, get_request_id

if TYPE_CHECKING:
    from fastapi import FastAPI, Request

_log = logging.getLogger("ownevo_kernel.api.errors")


async def unhandled_exception_handler(
    request: Request,
    exc: Exception,
) -> JSONResponse:
    """Convert any uncaught exception into a structured 500."""
    request_id = get_request_id(request)
    error_id = request_id or uuid.uuid4().hex
    _log.error(
        "unhandled exception in route",
        exc_info=exc,
        extra={
            "request_id": request_id,
            "error_id": error_id,
            "method": request.method,
            "path": request.url.path,
            "exc_class": type(exc).__name__,
        },
    )
    response = JSONResponse(
        status_code=500,
        content={
            "error": "internal_server_error",
            "error_id": error_id,
            "detail": (
                "The kernel hit an unexpected error. "
                "Quote the error_id when reporting this."
            ),
        },
    )
    # The middleware already adds X-Request-Id, but install it here too so
    # the response carries the id even if some upstream consumer reads it
    # off the JSONResponse object before the middleware writes its copy.
    if request_id is not None:
        response.headers[REQUEST_ID_HEADER] = request_id
    return response


def install_exception_handler(app: FastAPI) -> None:
    """Register the global handler on ``app``. Idempotent."""
    app.add_exception_handler(Exception, unhandled_exception_handler)


__all__ = ["install_exception_handler", "unhandled_exception_handler"]
