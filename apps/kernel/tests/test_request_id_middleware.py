"""Tests for the request-id middleware.

The middleware attaches a stable id to every request (minting one or
adopting a sanitised inbound ``X-Request-Id``) and echoes it on the
response so a 500 body's ``error_id`` can be grepped against logs.
"""

from __future__ import annotations

import re

import httpx
from fastapi import FastAPI, Request
from httpx import ASGITransport
from ownevo_kernel.api._request_id import (
    REQUEST_ID_HEADER,
    RequestIdMiddleware,
    get_request_id,
)


def _app_that_echoes_request_id() -> FastAPI:
    app = FastAPI()
    app.add_middleware(RequestIdMiddleware)

    @app.get("/echo")
    async def echo(request: Request) -> dict[str, str | None]:
        return {"request_id": get_request_id(request)}

    return app


async def test_request_id_is_minted_when_absent() -> None:
    app = _app_that_echoes_request_id()
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://api.test"
    ) as client:
        resp = await client.get("/echo")
    assert resp.status_code == 200
    header_id = resp.headers[REQUEST_ID_HEADER]
    body_id = resp.json()["request_id"]
    assert header_id == body_id
    # Minted ids are 32-char hex (uuid4().hex).
    assert re.match(r"^[0-9a-f]{32}$", header_id)


async def test_request_id_inbound_is_passed_through() -> None:
    app = _app_that_echoes_request_id()
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://api.test"
    ) as client:
        resp = await client.get(
            "/echo",
            headers={REQUEST_ID_HEADER: "my-correlation-abc123"},
        )
    assert resp.headers[REQUEST_ID_HEADER] == "my-correlation-abc123"
    assert resp.json()["request_id"] == "my-correlation-abc123"


async def test_request_id_strips_disallowed_chars() -> None:
    """An inbound id with newline or whitespace inside the value is
    rejected and a fresh id is minted, so a hostile caller cannot inject
    header content or log noise."""
    app = _app_that_echoes_request_id()
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://api.test"
    ) as client:
        resp = await client.get(
            "/echo",
            # The strip happens at the HTTP layer for leading/trailing
            # whitespace; an interior space is forbidden by the grammar.
            headers={REQUEST_ID_HEADER: "bad id with spaces"},
        )
    minted = resp.headers[REQUEST_ID_HEADER]
    assert minted != "bad id with spaces"
    assert re.match(r"^[0-9a-f]{32}$", minted)


async def test_request_id_rejects_oversize_inbound() -> None:
    """Inbound ids over 128 chars are discarded — protects log fields
    that might index by id from being blown up by a hostile caller."""
    app = _app_that_echoes_request_id()
    too_long = "a" * 200
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://api.test"
    ) as client:
        resp = await client.get("/echo", headers={REQUEST_ID_HEADER: too_long})
    minted = resp.headers[REQUEST_ID_HEADER]
    assert minted != too_long
    assert len(minted) == 32
