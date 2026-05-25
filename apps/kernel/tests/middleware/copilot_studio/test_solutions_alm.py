"""Unit tests for the Solutions ALM export + definition extraction.

`export_solution` is tested against a mocked `httpx` transport (token +
ExportSolution); `extract_agent_definition` is pure and tested against
in-memory solution zips. We assert: a base64 zip round-trips out of the
export call; the instruction text is pulled from a nested bot component;
and an unreadable or definition-free zip yields None (never an error).
"""

from __future__ import annotations

import base64
import io
import json
import zipfile

import pytest

pytest.importorskip("httpx", reason="httpx (api extra) not installed")

import httpx  # noqa: E402
from ownevo_kernel.middleware.copilot_studio import (  # noqa: E402
    CopilotStudioCredentials,
    CopilotStudioError,
    export_solution,
    extract_agent_definition,
)

_CREDS = CopilotStudioCredentials(
    tenant_id="t",
    client_id="c",
    client_secret="s",
    environment_url="https://org.crm.dynamics.com",
)


def _make_solution_zip(*files: tuple[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, content in files:
            z.writestr(name, content)
    return buf.getvalue()


def _client(handler) -> httpx.AsyncClient:  # noqa: ANN001
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# --- export_solution -------------------------------------------------------


async def test_export_solution_returns_decoded_zip() -> None:
    zip_bytes = _make_solution_zip(("solution.xml", "<x/>"))
    encoded = base64.b64encode(zip_bytes).decode()

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/oauth2/v2.0/token"):
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        assert req.url.path.endswith("/ExportSolution")
        return httpx.Response(200, json={"ExportSolutionFile": encoded})

    async with _client(handler) as c:
        out = await export_solution(_CREDS, solution_name="MyAgent", http_client=c)
    assert out == zip_bytes


async def test_export_solution_missing_file_field_errors() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/oauth2/v2.0/token"):
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        return httpx.Response(200, json={})

    async with _client(handler) as c:
        with pytest.raises(CopilotStudioError):
            await export_solution(_CREDS, solution_name="MyAgent", http_client=c)


# --- extract_agent_definition ----------------------------------------------


def test_extract_pulls_nested_instruction() -> None:
    component = {
        "schemaName": "bot_x",
        "data": {"components": [{"instructions": "You are a forecasting assistant."}]},
    }
    zip_bytes = _make_solution_zip(
        ("solution.xml", "<x/>"),
        ("botcomponents/bot_x.json", json.dumps(component)),
    )
    assert extract_agent_definition(zip_bytes) == "You are a forecasting assistant."


def test_extract_prefers_instruction_key_order() -> None:
    # `instructions` wins over `description` when both are present.
    component = {"description": "fallback", "instructions": "primary"}
    zip_bytes = _make_solution_zip(("b.json", json.dumps(component)))
    assert extract_agent_definition(zip_bytes) == "primary"


def test_extract_returns_none_for_bad_zip() -> None:
    assert extract_agent_definition(b"not a zip file at all") is None


def test_extract_returns_none_when_no_definition() -> None:
    zip_bytes = _make_solution_zip(
        ("solution.xml", "<x/>"),
        ("meta.json", json.dumps({"version": "1.0"})),
    )
    assert extract_agent_definition(zip_bytes) is None


def test_extract_ignores_non_json_entries() -> None:
    zip_bytes = _make_solution_zip(
        ("readme.txt", "instructions: not json"),
        ("b.json", json.dumps({"systemPrompt": "Be concise."})),
    )
    assert extract_agent_definition(zip_bytes) == "Be concise."
