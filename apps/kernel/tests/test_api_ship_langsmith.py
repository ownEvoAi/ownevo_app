"""Integration tests for POST /api/proposals/{id}/ship-langsmith.

The langsmith adapter is mocked at the `push_fix` boundary (no network,
no account); the assertions target the route's preconditions, the audit
entry it writes, and idempotency. A credentials master key is set per
test so the LangSmith key can be stored encrypted.
"""

from __future__ import annotations

import os

import asyncpg
import httpx
import pytest
from ownevo_kernel.db import ENV_VAR

pytestmark = pytest.mark.skipif(
    ENV_VAR not in os.environ,
    reason=f"{ENV_VAR} not set; skipping ship-langsmith tests",
)


@pytest.fixture(autouse=True)
def _master_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from ownevo_kernel.secrets import generate_master_key

    monkeypatch.setenv("OWNEVO_CREDENTIALS_MASTER_KEY", generate_master_key())


async def _seed_deployed_proposal(
    db: asyncpg.Connection,
    *,
    wf_id: str = "wf-ship",
    origin: str | None = "langsmith",
    prompt_id: str | None = "demand-forecast",
    state: str = "deployed",
) -> str:
    await db.execute(
        "INSERT INTO workflows (id, description, spec, origin) "
        "VALUES ($1, 'ship test', '{}'::jsonb, $2)",
        wf_id,
        origin,
    )
    await db.execute(
        "INSERT INTO skills (id, kind, workflow_id, langsmith_prompt_id) "
        "VALUES ('skill.ship', 'python'::skill_kind, $1, $2)",
        wf_id,
        prompt_id,
    )
    version_id = await db.fetchval(
        """
        INSERT INTO skill_versions (skill_id, version_seq, content, created_by)
        VALUES ('skill.ship', 1, 'Always cross-check the holiday calendar.', 'human:test')
        RETURNING id
        """,
    )
    await db.execute(
        "UPDATE skills SET deployed_version_id = $1 WHERE id = 'skill.ship'",
        version_id,
    )
    iteration_id = await db.fetchval(
        """
        INSERT INTO iterations (workflow_id, iteration_index, state, val_score,
                                best_ever_score_after, ended_at)
        VALUES ($1, 0, 'gate-pass'::iteration_state, 0.9, 0.9, now())
        RETURNING id
        """,
        wf_id,
    )
    proposal_id = await db.fetchval(
        """
        INSERT INTO proposals (iteration_id, skill_id, proposed_content,
                               plain_language_summary, state, eval_score)
        VALUES ($1, 'skill.ship', 'Always cross-check the holiday calendar.',
                'Fix holiday markdown false-negatives', $2::proposal_state, 0.9)
        RETURNING id
        """,
        iteration_id,
        state,
    )
    return str(proposal_id)


async def _set_credential(db: asyncpg.Connection) -> None:
    from ownevo_kernel.api._integration_credentials import set_credential

    await set_credential(db, "langsmith", "lsv2_pt_testkey")


def _mock_push(monkeypatch, *, commit_hash="abc123", url=None):
    from ownevo_kernel.middleware import langsmith_push

    url = url or f"https://smith.langchain.com/prompts/demand-forecast/{commit_hash}"

    def fake_push(**kwargs):
        return langsmith_push.PushResult(
            prompt_id=kwargs["prompt_id"], commit_url=url, commit_hash=commit_hash
        )

    monkeypatch.setattr(langsmith_push, "push_fix", fake_push)


async def test_ship_happy_path_writes_audit(
    api_client: httpx.AsyncClient, db: asyncpg.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    pid = await _seed_deployed_proposal(db)
    await _set_credential(db)
    _mock_push(monkeypatch, commit_hash="commit789")

    resp = await api_client.post(f"/api/proposals/{pid}/ship-langsmith", json={})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["commit_hash"] == "commit789"
    assert body["prompt_id"] == "demand-forecast"
    assert body["already_shipped"] is False

    audit = await db.fetchrow(
        "SELECT kind::text AS kind, payload FROM audit_entries "
        "WHERE kind = 'fix-shipped-langsmith' AND related_id = $1",
        __import__("uuid").UUID(pid),
    )
    assert audit is not None


async def test_ship_is_idempotent(
    api_client: httpx.AsyncClient, db: asyncpg.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    pid = await _seed_deployed_proposal(db)
    await _set_credential(db)
    _mock_push(monkeypatch, commit_hash="first")

    r1 = await api_client.post(f"/api/proposals/{pid}/ship-langsmith", json={})
    assert r1.status_code == 200

    # A second push with a different mocked hash must NOT fire — the
    # existing audit row short-circuits.
    _mock_push(monkeypatch, commit_hash="second")
    r2 = await api_client.post(f"/api/proposals/{pid}/ship-langsmith", json={})
    assert r2.status_code == 200
    assert r2.json()["already_shipped"] is True
    assert r2.json()["commit_hash"] == "first"

    count = await db.fetchval(
        "SELECT count(*) FROM audit_entries WHERE kind = 'fix-shipped-langsmith' "
        "AND related_id = $1",
        __import__("uuid").UUID(pid),
    )
    assert count == 1


async def test_ship_404_for_unknown(api_client: httpx.AsyncClient) -> None:
    import uuid

    resp = await api_client.post(
        f"/api/proposals/{uuid.uuid4()}/ship-langsmith", json={}
    )
    assert resp.status_code == 404


async def test_ship_422_non_langsmith_origin(
    api_client: httpx.AsyncClient, db: asyncpg.Connection
) -> None:
    pid = await _seed_deployed_proposal(db, origin=None)
    await _set_credential(db)
    resp = await api_client.post(f"/api/proposals/{pid}/ship-langsmith", json={})
    assert resp.status_code == 422


async def test_ship_422_no_prompt_binding(
    api_client: httpx.AsyncClient, db: asyncpg.Connection
) -> None:
    pid = await _seed_deployed_proposal(db, prompt_id=None)
    await _set_credential(db)
    resp = await api_client.post(f"/api/proposals/{pid}/ship-langsmith", json={})
    assert resp.status_code == 422


async def test_ship_422_not_deployed(
    api_client: httpx.AsyncClient, db: asyncpg.Connection
) -> None:
    pid = await _seed_deployed_proposal(db, state="gate-passed")
    await _set_credential(db)
    resp = await api_client.post(f"/api/proposals/{pid}/ship-langsmith", json={})
    assert resp.status_code == 422


async def test_ship_424_no_credential(
    api_client: httpx.AsyncClient, db: asyncpg.Connection
) -> None:
    pid = await _seed_deployed_proposal(db)
    # No credential set.
    resp = await api_client.post(f"/api/proposals/{pid}/ship-langsmith", json={})
    assert resp.status_code == 424
