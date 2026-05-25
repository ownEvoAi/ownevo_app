"""Solutions ALM export — pull a Copilot Studio agent's definition out.

Copilot Studio agents are packaged as Power Platform **solutions**. The
documented Dataverse `ExportSolution` action returns the solution as a
base64-encoded zip; the agent's instructions live inside it as a bot
component. ownEvo exports that definition so the trace-import design
agent can open discovery with "this agent appears to do X" grounded in
the agent's *stated* instructions, not only its observed traces (the
`agent_definition` the reverse-discovery turn already consumes).

Component-completeness caveat (per Microsoft's own ALM docs): Solutions
export does not guarantee every component round-trips. We extract the
instruction text best-effort and treat a miss as "no definition
available" rather than an error — the design agent falls back to the
trace-only summary, which is never blocked on this.
"""

from __future__ import annotations

import base64
import io
import json
import zipfile

import httpx

from .errors import (
    CopilotStudioError,
    CopilotStudioNetworkError,
)
from .evaluation_api import (
    CopilotStudioCredentials,
    TokenCache,
    _raise_for_status,
)

# Dataverse Web API version carrying the ExportSolution action.
_DATAVERSE_API_VERSION = "v9.2"
_EXPORT_TIMEOUT_SECONDS = 120.0  # solution export can be slow for large agents

# JSON keys a Copilot Studio bot component uses to hold its instruction /
# system-prompt text. Checked in order; the first non-empty string wins.
_INSTRUCTION_KEYS = ("instructions", "systemPrompt", "content", "description")


def _export_endpoint(environment_url: str) -> str:
    return (
        f"{environment_url.rstrip('/')}/api/data/{_DATAVERSE_API_VERSION}/ExportSolution"
    )


async def export_solution(
    credentials: CopilotStudioCredentials,
    *,
    solution_name: str,
    managed: bool = False,
    token_cache: TokenCache | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> bytes:
    """Export a Power Platform solution and return the decoded zip bytes.

    Calls the documented `ExportSolution` Dataverse action. Raises a
    typed adapter error on auth / not-found / network failure, or
    `CopilotStudioError` when the response is missing the file field.
    """
    cache = token_cache or TokenCache(credentials, http_client=http_client)
    bearer = await cache.token()

    body = {"SolutionName": solution_name, "Managed": managed}
    endpoint = _export_endpoint(credentials.environment_url)

    owns_client = http_client is None
    client = http_client or httpx.AsyncClient(timeout=_EXPORT_TIMEOUT_SECONDS)
    try:
        resp = await client.post(
            endpoint,
            json=body,
            headers={"Authorization": f"Bearer {bearer}"},
        )
    except httpx.TimeoutException as exc:
        raise CopilotStudioNetworkError(f"Solution export timed out: {exc}") from exc
    except httpx.HTTPError as exc:
        raise CopilotStudioNetworkError(f"Could not reach Power Platform: {exc}") from exc
    finally:
        if owns_client:
            await client.aclose()

    _raise_for_status(resp)

    payload = resp.json()
    encoded = payload.get("ExportSolutionFile")
    if not encoded:
        raise CopilotStudioError("ExportSolution returned no ExportSolutionFile")
    try:
        return base64.b64decode(encoded)
    except (ValueError, TypeError) as exc:
        raise CopilotStudioError("ExportSolution file was not valid base64") from exc


def extract_agent_definition(solution_zip: bytes) -> str | None:
    """Best-effort pull of the agent's instruction text from a solution zip.

    Scans the zip for JSON bot-component files and returns the first
    instruction-bearing text found (see `_INSTRUCTION_KEYS`). Returns
    None when the zip is unreadable or carries no recognisable definition
    — the caller treats that as "fall back to the trace-only summary",
    never as a hard failure.
    """
    try:
        archive = zipfile.ZipFile(io.BytesIO(solution_zip))
    except zipfile.BadZipFile:
        return None

    texts: list[str] = []
    for entry in archive.namelist():
        if not entry.lower().endswith(".json"):
            continue
        try:
            raw = archive.read(entry)
        except (KeyError, RuntimeError):
            continue
        found = _instruction_from_json(raw)
        if found:
            texts.append(found)

    if not texts:
        return None
    # Multiple bot components (e.g. topics + system instructions) concatenate
    # in discovery order; the design agent reads the whole thing as context.
    return "\n\n".join(texts)


def _instruction_from_json(raw: bytes) -> str | None:
    """Return the first non-empty instruction string in a JSON document.

    Walks the parsed structure recursively so the key can sit at any
    nesting depth (bot components nest the definition under topic /
    component wrappers that vary by export version).
    """
    try:
        doc = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return None
    return _search_instruction(doc)


def _search_instruction(node: object) -> str | None:
    if isinstance(node, dict):
        for key in _INSTRUCTION_KEYS:
            value = node.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        for value in node.values():
            found = _search_instruction(value)
            if found:
                return found
    elif isinstance(node, list):
        for item in node:
            found = _search_instruction(item)
            if found:
                return found
    return None
