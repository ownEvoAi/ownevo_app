"""Tests for the global exception handler.

A route that raises an uncaught exception must surface as a structured
JSON 500 carrying an ``error_id`` that matches the request id, with a
parallel ERROR log line carrying the traceback and structured fields.
"""

from __future__ import annotations

import logging
import re

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport
from ownevo_kernel.api._error_handler import install_exception_handler
from ownevo_kernel.api._request_id import REQUEST_ID_HEADER, RequestIdMiddleware


def _app_with_boom_routes() -> FastAPI:
    app = FastAPI()
    install_exception_handler(app)
    app.add_middleware(RequestIdMiddleware)

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("kaboom — simulated unhandled error")

    @app.get("/teapot")
    async def teapot() -> None:
        raise HTTPException(status_code=418, detail="i am a teapot")

    @app.get("/ok")
    async def ok() -> dict[str, str]:
        return {"status": "ok"}

    return app


@pytest.mark.asyncio
async def test_unhandled_exception_returns_structured_500(
    caplog: pytest.LogCaptureFixture,
) -> None:
    app = _app_with_boom_routes()
    caplog.set_level(logging.ERROR, logger="ownevo_kernel.api.errors")
    # ASGITransport defaults to raise_app_exceptions=True, which would
    # re-raise the original RuntimeError out of httpx instead of letting
    # our exception handler convert it. Disable so the handler can
    # produce its structured 500.
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://api.test",
    ) as client:
        resp = await client.get("/boom")

    assert resp.status_code == 500
    body = resp.json()
    assert body["error"] == "internal_server_error"
    assert "detail" in body
    error_id = body["error_id"]
    # error_id matches the response's X-Request-Id (correlation across
    # body + header + log line).
    assert resp.headers[REQUEST_ID_HEADER] == error_id

    # Exactly one ERROR log line, carrying the traceback + structured fields.
    errors = [
        r for r in caplog.records
        if r.name == "ownevo_kernel.api.errors" and r.levelno == logging.ERROR
    ]
    assert len(errors) == 1
    record = errors[0]
    assert record.exc_info is not None  # full traceback attached
    assert record.error_id == error_id  # type: ignore[attr-defined]
    assert record.request_id == error_id  # type: ignore[attr-defined]
    assert record.method == "GET"  # type: ignore[attr-defined]
    assert record.path == "/boom"  # type: ignore[attr-defined]
    assert record.exc_class == "RuntimeError"  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_http_exception_passes_through_unchanged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """HTTPException is FastAPI's own 4xx escape hatch; the global
    handler must NOT swallow it into a 500."""
    app = _app_with_boom_routes()
    caplog.set_level(logging.ERROR, logger="ownevo_kernel.api.errors")
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://api.test"
    ) as client:
        resp = await client.get("/teapot")

    assert resp.status_code == 418
    assert resp.json() == {"detail": "i am a teapot"}
    # No error-log line for an intentional 4xx.
    assert not [
        r for r in caplog.records
        if r.name == "ownevo_kernel.api.errors" and r.levelno >= logging.ERROR
    ]


@pytest.mark.asyncio
async def test_error_id_format_when_request_id_unavailable() -> None:
    """If the request-id middleware is not installed, the handler still
    mints a fresh error_id and includes it in the body — the response
    just won't carry a matching X-Request-Id header."""
    app = FastAPI()
    install_exception_handler(app)  # no RequestIdMiddleware

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("standalone")

    async with httpx.AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://api.test",
    ) as client:
        resp = await client.get("/boom")
    assert resp.status_code == 500
    body = resp.json()
    assert re.match(r"^[0-9a-f]{32}$", body["error_id"])
    assert REQUEST_ID_HEADER not in resp.headers
