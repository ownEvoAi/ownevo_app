"""Skill registry — DB-backed integration tests.

Exercises the full register / lookup / re-register-as-new-version flow.
Skipped when `OWNEVO_DATABASE_URL` is unset (see test_db.py for setup).
"""

from __future__ import annotations

import json
import os
import uuid

import asyncpg
import pytest
from ownevo_kernel.db import ENV_VAR, migrate
from ownevo_kernel.skills import (
    SkillFormatError,
    get_head,
    list_versions,
    register_skill,
)

pytestmark = pytest.mark.skipif(
    ENV_VAR not in os.environ,
    reason=f"{ENV_VAR} not set; skipping integration tests",
)


def _admin_url() -> str:
    base = os.environ[ENV_VAR]
    return base.rsplit("/", 1)[0] + "/postgres"


@pytest.fixture
async def db():
    """Create a fresh database, migrate, yield connection, drop."""
    dbname = f"ownevo_test_{uuid.uuid4().hex[:12]}"
    admin = await asyncpg.connect(_admin_url())
    try:
        await admin.execute(f'CREATE DATABASE "{dbname}"')
    finally:
        await admin.close()

    base = os.environ[ENV_VAR]
    test_url = base.rsplit("/", 1)[0] + f"/{dbname}"
    conn = await asyncpg.connect(test_url)
    try:
        await migrate(conn)
        yield conn
    finally:
        await conn.close()
        admin = await asyncpg.connect(_admin_url())
        try:
            await admin.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname=$1 AND pid<>pg_backend_pid()",
                dbname,
            )
            await admin.execute(f'DROP DATABASE "{dbname}"')
        finally:
            await admin.close()


SKILL_V1 = """\
---
id: m5-feature-engineer
kind: python
created_by: agent:claude-sonnet-4-6
capability_tags: [forecasting]

retention:
  remembers: []
  refetches:
    - source: m5_calendar_features
      stale_after: 24h
      reason: holiday flags update daily
---

def engineer_features(df):
    return df
"""

SKILL_V2 = """\
---
id: m5-feature-engineer
kind: python
created_by: agent:claude-opus-4-7
capability_tags: [forecasting, feature-engineering]

retention:
  remembers: []
  refetches:
    - source: m5_calendar_features
      stale_after: 24h
      reason: holiday flags update daily
    - source: m5_price_history
      stale_after: 1h
      reason: prices change with promotions
---

def engineer_features(df, price_df):
    return df.merge(price_df, on='item_id')
"""


# ---------------------------------------------------------------------------
# Happy-path registration
# ---------------------------------------------------------------------------


async def test_first_registration_creates_skill_and_version(db: asyncpg.Connection):
    result = await register_skill(db, SKILL_V1)
    assert result.skill_id == "m5-feature-engineer"
    assert result.version_seq == 1
    assert isinstance(result.version_id, uuid.UUID)

    head = await get_head(db, "m5-feature-engineer")
    assert head is not None
    assert head.kind == "python"
    assert head.version_id == result.version_id
    assert head.version_seq == 1
    assert head.created_by == "agent:claude-sonnet-4-6"
    assert "engineer_features" in head.content


async def test_retention_block_persisted_as_jsonb(db: asyncpg.Connection):
    """The eval-case generator queries `retention_block` JSONB directly."""
    result = await register_skill(db, SKILL_V1)
    block = await db.fetchval(
        "SELECT retention_block FROM skill_versions WHERE id = $1",
        result.version_id,
    )
    # asyncpg returns jsonb as a JSON string by default — decode for the assertion.
    parsed = json.loads(block) if isinstance(block, str) else block
    assert parsed["id"] == "m5-feature-engineer"
    assert parsed["retention"]["refetches"][0]["source"] == "m5_calendar_features"


# ---------------------------------------------------------------------------
# Re-registration → new version with parent link
# ---------------------------------------------------------------------------


async def test_re_register_creates_new_version_linked_to_parent(db: asyncpg.Connection):
    v1 = await register_skill(db, SKILL_V1)
    v2 = await register_skill(db, SKILL_V2, diff_summary="add price-history join")

    assert v2.version_seq == 2
    assert v2.version_id != v1.version_id

    versions = await list_versions(db, "m5-feature-engineer")
    assert [v["version_seq"] for v in versions] == [1, 2]
    assert versions[1]["parent_version_id"] == v1.version_id
    assert versions[1]["diff_summary"] == "add price-history join"

    # HEAD still points at v1 — re-registration alone is not a gate-pass,
    # so the validated-state pointer doesn't move (TODO-31). The agent's
    # "latest write" pointer does advance.
    head = await get_head(db, "m5-feature-engineer")
    assert head is not None
    assert head.version_id == v1.version_id
    latest_proposed = await db.fetchval(
        "SELECT latest_proposed_version_id FROM skills WHERE id = $1",
        "m5-feature-engineer",
    )
    assert latest_proposed == v2.version_id


async def test_bootstrap_seeds_both_pointers_at_v1(db: asyncpg.Connection):
    """First registration has no gate-pass yet — HEAD must be set so that
    `read_skill` / `get_head` returns something on the bootstrap iteration."""
    v1 = await register_skill(db, SKILL_V1)
    row = await db.fetchrow(
        "SELECT head_version_id, latest_proposed_version_id "
        "FROM skills WHERE id = $1",
        "m5-feature-engineer",
    )
    assert row["head_version_id"] == v1.version_id
    assert row["latest_proposed_version_id"] == v1.version_id


async def test_subsequent_register_chains_parent_off_latest_proposed(
    db: asyncpg.Connection,
):
    """v3's parent must be v2 even if v2 was gate-rejected (HEAD still at v1).
    Without this, every rejected version reparents v3 onto v1 and the
    version graph forks into a fan rather than a linear chain."""
    v1 = await register_skill(db, SKILL_V1)
    v2 = await register_skill(db, SKILL_V2)

    # HEAD still v1 (no gate-pass), but a third register should chain off v2.
    v3_content = SKILL_V2.replace(
        "kind: python", "kind: python"
    ).replace(
        "agent:claude-opus-4-7", "agent:claude-opus-4-7-take-3"
    )
    await register_skill(db, v3_content)
    versions = await list_versions(db, "m5-feature-engineer")
    by_seq = {v["version_seq"]: v for v in versions}
    assert by_seq[3]["parent_version_id"] == v2.version_id
    assert by_seq[2]["parent_version_id"] == v1.version_id


async def test_capability_tags_refresh_on_re_register(db: asyncpg.Connection):
    """Tags drift across versions; we re-set them to the latest declaration."""
    await register_skill(db, SKILL_V1)
    await register_skill(db, SKILL_V2)
    tags = await db.fetchval(
        "SELECT capability_tags FROM skills WHERE id = $1",
        "m5-feature-engineer",
    )
    assert sorted(tags) == ["feature-engineering", "forecasting"]


# ---------------------------------------------------------------------------
# Failure paths
# ---------------------------------------------------------------------------


async def test_kind_mismatch_rejected(db: asyncpg.Connection):
    """A re-registration that flips python→instruction is a programmer
    error; the registry refuses rather than corrupting the ER diagram."""
    await register_skill(db, SKILL_V1)

    flipped = SKILL_V1.replace("kind: python", "kind: instruction")
    with pytest.raises(SkillFormatError, match="kind mismatch"):
        await register_skill(db, flipped)


async def test_register_returns_format_error_on_bad_input(db: asyncpg.Connection):
    """Caller can catch SkillFormatError and surface it via tool_call_result."""
    with pytest.raises(SkillFormatError):
        await register_skill(db, "# no frontmatter\n")


async def test_created_by_override(db: asyncpg.Connection):
    """The gate runner stamps the actual model that emitted the skill,
    overriding whatever the file declares."""
    result = await register_skill(
        db,
        SKILL_V1,
        created_by="agent:claude-opus-4-7-during-iteration-42",
    )
    head = await get_head(db, "m5-feature-engineer")
    assert head is not None
    assert head.created_by == "agent:claude-opus-4-7-during-iteration-42"
    assert head.version_id == result.version_id


# ---------------------------------------------------------------------------
# Empty state
# ---------------------------------------------------------------------------


async def test_get_head_returns_none_for_unknown_skill(db: asyncpg.Connection):
    assert await get_head(db, "no-such-skill") is None


async def test_list_versions_empty_for_unknown_skill(db: asyncpg.Connection):
    assert await list_versions(db, "no-such-skill") == []
