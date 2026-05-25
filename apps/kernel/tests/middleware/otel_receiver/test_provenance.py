"""Provenance auto-tagging: workflow.origin + skills.langsmith_prompt_id.

Two layers: mapper-level detection (no DB) and route-level application
(POST → workflow/skill rows updated).
"""

from __future__ import annotations

import os

import asyncpg
import httpx
import pytest
from ownevo_kernel.db import ENV_VAR
from ownevo_kernel.middleware.otel_receiver import decode_otlp_payload
from ownevo_kernel.middleware.otel_receiver.auth import mint_token

from ._fixture_helpers import assistant_text_messages, make_span, str_attr, wrap_batch

_TRACE = "00000000000000000000000000000aa1"
_SPAN = "00000000000000a1"


def _chat_span_with(attrs_extra: list[dict]) -> dict:
    base = [
        str_attr("gen_ai.operation.name", "chat"),
        str_attr("gen_ai.response.model", "gpt-4o"),
        {"key": "gen_ai.output.messages", "value": assistant_text_messages("hi")},
    ]
    return wrap_batch(
        [
            make_span(
                span_id=_SPAN,
                trace_id=_TRACE,
                name="gen_ai.chat",
                attributes=base + attrs_extra,
            ),
        ],
    )


# --- mapper-level detection (no DB) ----------------------------------------


def test_detects_langsmith_origin_from_signature_attr() -> None:
    payload = _chat_span_with([str_attr("langsmith.session.name", "demo-sess")])
    batch = decode_otlp_payload(payload)
    assert batch.detected_origin == "langsmith"


def test_detects_origin_from_langsmith_prefix() -> None:
    payload = _chat_span_with([str_attr("langsmith.anything.custom", "x")])
    batch = decode_otlp_payload(payload)
    assert batch.detected_origin == "langsmith"


def test_no_origin_without_signature() -> None:
    payload = _chat_span_with([])
    batch = decode_otlp_payload(payload)
    assert batch.detected_origin is None


def test_detects_prompt_id() -> None:
    payload = _chat_span_with([str_attr("langsmith.prompt.name", "demand-forecast")])
    batch = decode_otlp_payload(payload)
    assert batch.detected_prompt_ids == {"demand-forecast"}


def test_origin_detected_from_resource_attrs() -> None:
    payload = _chat_span_with([])
    payload["resourceSpans"][0]["resource"]["attributes"] = [
        str_attr("langsmith.project.name", "acme"),
    ]
    batch = decode_otlp_payload(payload)
    assert batch.detected_origin == "langsmith"


# --- route-level application (DB) ------------------------------------------

pytestmark = pytest.mark.skipif(
    ENV_VAR not in os.environ,
    reason=f"{ENV_VAR} not set; skipping route-level provenance tests",
)


async def _seed_workflow_with_skill(
    db: asyncpg.Connection, wf_id: str, skill_id: str
) -> None:
    await db.execute(
        "INSERT INTO workflows (id, description, spec) "
        "VALUES ($1, 'prov test', '{}'::jsonb)",
        wf_id,
    )
    await db.execute(
        "INSERT INTO skills (id, kind, workflow_id) "
        "VALUES ($1, 'python'::skill_kind, $2)",
        skill_id,
        wf_id,
    )


async def _bound_token(db: asyncpg.Connection, wf_id: str) -> str:
    plaintext, token_hash = mint_token()
    await db.execute(
        "INSERT INTO receiver_tokens (token_hash, label, workflow_id) "
        "VALUES ($1, 'prov-test', $2)",
        token_hash,
        wf_id,
    )
    return plaintext


async def test_route_auto_tags_origin_and_binds_prompt(
    api_client: httpx.AsyncClient, db: asyncpg.Connection,
) -> None:
    await _seed_workflow_with_skill(db, "wf-prov", "skill.prov")
    token = await _bound_token(db, "wf-prov")
    payload = _chat_span_with(
        [
            str_attr("langsmith.session.name", "s"),
            str_attr("langsmith.prompt.name", "demand-forecast"),
        ],
    )

    resp = await api_client.post(
        "/api/otel/v1/traces",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text

    origin = await db.fetchval("SELECT origin FROM workflows WHERE id = 'wf-prov'")
    assert origin == "langsmith"
    prompt_id = await db.fetchval(
        "SELECT langsmith_prompt_id FROM skills WHERE id = 'skill.prov'"
    )
    assert prompt_id == "demand-forecast"


async def test_route_does_not_overwrite_existing_origin(
    api_client: httpx.AsyncClient, db: asyncpg.Connection,
) -> None:
    await _seed_workflow_with_skill(db, "wf-prov2", "skill.prov2")
    # Pre-set a different origin + binding; ingest must not clobber them.
    await db.execute("UPDATE workflows SET origin = 'copilot_studio' WHERE id = 'wf-prov2'")
    await db.execute(
        "UPDATE skills SET langsmith_prompt_id = 'manual-pin' WHERE id = 'skill.prov2'"
    )
    token = await _bound_token(db, "wf-prov2")
    payload = _chat_span_with(
        [
            str_attr("langsmith.session.name", "s"),
            str_attr("langsmith.prompt.name", "auto-detected"),
        ],
    )
    resp = await api_client.post(
        "/api/otel/v1/traces",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text

    assert (
        await db.fetchval("SELECT origin FROM workflows WHERE id = 'wf-prov2'")
        == "copilot_studio"
    )
    assert (
        await db.fetchval("SELECT langsmith_prompt_id FROM skills WHERE id = 'skill.prov2'")
        == "manual-pin"
    )
