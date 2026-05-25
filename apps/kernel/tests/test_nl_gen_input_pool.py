"""Eval-case input-pool builder — grounds case generation in connector data.

DB-backed (uploads + MCP servers live in the DB) but no parsing libs needed:
uploads are seeded directly via create_upload.
"""

from __future__ import annotations

import os

import asyncpg
import pytest
from ownevo_kernel.data_ingest import UploadKind, create_upload
from ownevo_kernel.db import ENV_VAR
from ownevo_kernel.nl_gen.fixtures import FIXTURES
from ownevo_kernel.nl_gen.input_pool import build_input_pool_block
from ownevo_kernel.nl_gen.spec import DataSource, WorkflowSpec

pytestmark = pytest.mark.skipif(
    ENV_VAR not in os.environ,
    reason=f"{ENV_VAR} not set; skipping input-pool tests",
)


def _spec_with_sources(*sources: DataSource) -> WorkflowSpec:
    base = FIXTURES["credit-risk"]
    env = base.environment.model_copy(update={"data_sources": list(sources)})
    return base.model_copy(update={"environment": env})


async def test_no_connectors_returns_none(db: asyncpg.Connection) -> None:
    spec = _spec_with_sources(DataSource(id="core", description="sim source"))
    assert await build_input_pool_block(db, spec) is None


async def test_spreadsheet_upload_surfaces_columns_and_rows(
    db: asyncpg.Connection,
) -> None:
    upload = await create_upload(
        db,
        name="dnb.csv",
        kind=UploadKind.CSV,
        content_type="text/csv",
        size_bytes=20,
        sha256="h",
        schema={
            "columns": [
                {"name": "duns", "dtype": "object"},
                {"name": "score", "dtype": "int64"},
            ]
        },
        row_count=2,
        content={"rows": [{"duns": "D1", "score": 80}, {"duns": "D2", "score": 55}]},
    )
    spec = _spec_with_sources(
        DataSource(id="dnb_external", kind="upload", upload_id=str(upload.id))
    )
    block = await build_input_pool_block(db, spec)
    assert block is not None
    assert "input pool" in block
    assert "dnb_external" in block
    # Real columns + sample values are surfaced so the generator can ground cases.
    assert "duns" in block and "score" in block
    assert "D1" in block


async def test_document_upload_surfaces_title_and_sections(
    db: asyncpg.Connection,
) -> None:
    upload = await create_upload(
        db,
        name="policy.pdf",
        kind=UploadKind.PDF,
        content_type="application/pdf",
        size_bytes=20,
        sha256="h2",
        schema={"title": "Credit Policy", "page_count": 4},
        row_count=None,
        content={
            "text": "body",
            "sections": [{"level": 1, "heading": "Underwriting"}],
            "tables": [],
        },
    )
    spec = _spec_with_sources(
        DataSource(id="policy_doc", kind="upload", upload_id=str(upload.id))
    )
    block = await build_input_pool_block(db, spec)
    assert "policy_doc" in block
    assert "Credit Policy" in block
    assert "Underwriting" in block


async def test_missing_upload_is_noted_not_fatal(db: asyncpg.Connection) -> None:
    spec = _spec_with_sources(
        DataSource(
            id="ghost",
            kind="upload",
            upload_id="00000000-0000-0000-0000-000000000000",
        )
    )
    block = await build_input_pool_block(db, spec)
    assert "ghost" in block
    assert "not found" in block


async def test_mcp_source_names_server(db: asyncpg.Connection) -> None:
    from ownevo_kernel.mcp_client import (
        AuthKind,
        MCPServerRegistration,
        Transport,
        registry,
    )

    # MCP registry needs the credentials master key to seal the (absent) secret;
    # set one for the duration of the registration.
    from ownevo_kernel.secrets import generate_master_key

    os.environ.setdefault("OWNEVO_CREDENTIALS_MASTER_KEY", generate_master_key())
    server = await registry.register_server(
        db,
        MCPServerRegistration(
            name="acme-slack",
            provider="slack",
            endpoint_url="https://mcp.test/slack",
            transport=Transport.STREAMABLE_HTTP,
            auth_kind=AuthKind.NONE,
            auth_secret=None,
        ),
    )
    spec = _spec_with_sources(
        DataSource(id="slack_src", kind="mcp", mcp_server_id=str(server.id))
    )
    block = await build_input_pool_block(db, spec)
    assert "slack_src" in block
    assert "acme-slack" in block
