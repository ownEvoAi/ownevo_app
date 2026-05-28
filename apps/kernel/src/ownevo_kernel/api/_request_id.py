"""Request-id middleware.

Every request gets a short hex id stashed on ``request.state.request_id``
and echoed back on the response as ``X-Request-Id``. The id is the
correlation key tying a 500 response payload, the access log line, and
the structured error log emitted by the global exception handler so an
operator who sees one of them can grep the others.

If the inbound request carries an ``X-Request-Id`` header, it is used
verbatim as long as it is a plausible token (alphanumeric, dashes,
underscores, 1-128 chars); anything outside that grammar is discarded
and a fresh id is minted, so a hostile caller cannot inject log-line or
header content.
"""

from __future__ import annotations

import re
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

REQUEST_ID_HEADER = "X-Request-Id"
_MAX_ID_LENGTH = 128
_ALLOWED_ID_RE = re.compile(r"^[A-Za-z0-9_\-]+$")


def _new_request_id() -> str:
    """Compact 32-char hex (uuid4 without the dashes)."""
    return uuid.uuid4().hex


def _sanitize_inbound(raw: str | None) -> str | None:
    if raw is None:
        return None
    candidate = raw.strip()
    if not candidate or len(candidate) > _MAX_ID_LENGTH:
        return None
    if not _ALLOWED_ID_RE.match(candidate):
        return None
    return candidate


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Attach a stable id to every request and response."""

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(  # type: ignore[override]
        self,
        request: Request,
        call_next,
    ) -> Response:
        inbound = _sanitize_inbound(request.headers.get(REQUEST_ID_HEADER))
        request_id = inbound or _new_request_id()
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = request_id
        return response


def get_request_id(request: Request) -> str | None:
    """Return the request id stashed by the middleware, if any."""
    return getattr(request.state, "request_id", None)


__all__ = [
    "REQUEST_ID_HEADER",
    "RequestIdMiddleware",
    "get_request_id",
]
