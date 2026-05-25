"""Build the eval-case "input pool" from a workflow's connector data sources.

A workflow can declare data sources backed by an uploaded file (kind="upload")
or an MCP server (kind="mcp"), in addition to the simulator-backed default.
This module renders those declared sources into a compact, real-data context
block that eval-case generation grounds cases in — so a generated case can draw
on the actual columns and sample rows of an uploaded spreadsheet, the title and
sections of an uploaded document, or a connected MCP server.

The block is fed to `generate_eval_case_set` the same way `design_brief_block`
is, and instructs the model to tag any case that draws on a source with that
source's `input_source` id (see `GeneratedEvalCase.input_source`).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from uuid import UUID

from ..data_ingest import SPREADSHEET_KINDS, get_upload, get_upload_content
from .spec import WorkflowSpec

if TYPE_CHECKING:
    import asyncpg

# How many sample rows to surface per source. Enough to ground a case in real
# values without bloating the generator prompt.
_MAX_SAMPLE_ROWS = 5


async def build_input_pool_block(
    conn: asyncpg.Connection,
    workflow_spec: WorkflowSpec,
    *,
    max_rows: int = _MAX_SAMPLE_ROWS,
) -> str | None:
    """Render declared upload/MCP data sources as a generator context block.

    Returns None when the workflow declares no connector data sources, so the
    caller can skip appending anything (the default simulator-only workflow is
    unchanged).
    """
    connectors = [
        ds for ds in workflow_spec.environment.data_sources if ds.kind != "standard"
    ]
    if not connectors:
        return None

    lines = [
        "Connector data sources (the input pool). These are real data the "
        "workflow's agent can read at run time. When a case draws on one of "
        "them, set its `input_source` to the source id listed here so the "
        "replay harness provides that data:",
    ]
    for ds in connectors:
        lines.append(await _describe_source(conn, ds, max_rows=max_rows))
    return "\n".join(lines)


async def _describe_source(conn, ds, *, max_rows: int) -> str:
    if ds.kind == "upload":
        return await _describe_upload(conn, ds, max_rows=max_rows)
    if ds.kind == "mcp":
        return await _describe_mcp(conn, ds)
    return f"- {ds.id} ({ds.kind}): {ds.description}"  # pragma: no cover


async def _describe_upload(conn, ds, *, max_rows: int) -> str:
    if not ds.upload_id:
        return f"- {ds.id} (upload): no upload_id declared"
    upload = await get_upload(conn, UUID(ds.upload_id))
    if upload is None:
        return f"- {ds.id} (upload): upload {ds.upload_id} not found"

    if upload.kind in SPREADSHEET_KINDS:
        content = await get_upload_content(conn, upload.id) or {}
        rows = content.get("rows", [])[:max_rows]
        columns = [c.get("name") for c in upload.schema_.get("columns", [])]
        return (
            f"- {ds.id} (upload `{upload.name}`, {upload.row_count} rows). "
            f"Columns: {columns}. Sample rows: {json.dumps(rows)}"
        )

    # Document upload.
    meta = upload.schema_
    headings = [s.get("heading") for s in (await _doc_sections(conn, upload.id))]
    return (
        f"- {ds.id} (upload `{upload.name}`, document). "
        f"Title: {meta.get('title')!r}. Sections: {headings[:10]}"
    )


async def _doc_sections(conn, upload_id) -> list[dict]:
    content = await get_upload_content(conn, upload_id) or {}
    sections = content.get("sections", [])
    return sections if isinstance(sections, list) else []


async def _describe_mcp(conn, ds) -> str:
    if not ds.mcp_server_id:
        return f"- {ds.id} (mcp): no mcp_server_id declared"
    # Imported lazily: the MCP client is its own subsystem and not every
    # eval-case generation touches it.
    from ..mcp_client import registry as mcp_registry

    server = await mcp_registry.get_server(conn, UUID(ds.mcp_server_id))
    if server is None:
        return f"- {ds.id} (mcp): server {ds.mcp_server_id} not found"
    return (
        f"- {ds.id} (mcp server `{server.name}`, provider {server.provider}). "
        "Tools are listed + invoked at run time via mcp_call."
    )


__all__ = ["build_input_pool_block"]
