"""read_skill / write_skill — DB-backed integration tests.

Exercises the read/write surface the agent uses to evolve skills.
Round-trip: write_skill(v1) → read_skill returns v1 →
write_skill(v2) → read_skill returns v2 with parent linkage.
"""

from __future__ import annotations

import os

import asyncpg
import pytest
from ownevo_kernel.agent_tools import read_skill, write_skill
from ownevo_kernel.agent_tools.skills import SkillFormatError
from ownevo_kernel.db import ENV_VAR

# `db` fixture lives in apps/kernel/tests/conftest.py.
pytestmark = pytest.mark.skipif(
    ENV_VAR not in os.environ,
    reason=f"{ENV_VAR} not set",
)


SKILL_V1 = """\
---
id: m5-feature-engineer
kind: python
created_by: agent:claude-opus-4-7
capability_tags: [forecasting]
retention:
  remembers: []
  refetches: []
  stateless: true
---

def engineer(df):
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
  refetches: []
  stateless: true
---

def engineer(df, prices):
    return df.merge(prices, on='item_id')
"""


async def test_read_skill_returns_none_when_unknown(db: asyncpg.Connection):
    assert await read_skill(db, "no-such-skill") is None


async def test_write_then_read_round_trip(db: asyncpg.Connection):
    result = await write_skill(
        db,
        skill_id="m5-feature-engineer",
        content=SKILL_V1,
        created_by="agent:test",
    )
    assert result.version_seq == 1

    head = await read_skill(db, "m5-feature-engineer")
    assert head is not None
    assert head.version_seq == 1
    assert head.created_by == "agent:test"
    assert "def engineer" in head.content


async def test_write_v2_advances_head(db: asyncpg.Connection):
    """Agent's typical loop: read → propose change → write. The new
    version becomes head; v1 is reachable via list_versions but not
    via read_skill."""
    await write_skill(db, "m5-feature-engineer", SKILL_V1, created_by="agent:test")
    v2 = await write_skill(
        db,
        "m5-feature-engineer",
        SKILL_V2,
        created_by="agent:test",
        diff_summary="add prices join",
    )
    assert v2.version_seq == 2

    head = await read_skill(db, "m5-feature-engineer")
    assert head is not None
    assert head.version_id == v2.version_id
    assert head.version_seq == 2
    assert "merge(prices" in head.content


async def test_write_with_malformed_frontmatter_raises(db: asyncpg.Connection):
    """Bad input → SkillFormatError. The agent gets it as a structured
    tool_call_result error and can correct itself."""
    with pytest.raises(SkillFormatError):
        await write_skill(
            db,
            "x",
            "no frontmatter here, just body\n",
            created_by="agent:test",
        )


async def test_write_with_mismatched_skill_id_raises(db: asyncpg.Connection):
    """skill_id arg must match frontmatter id — mismatch raises before any
    DB write so the agent can't silently overwrite the wrong skill."""
    with pytest.raises(SkillFormatError, match="does not match frontmatter"):
        await write_skill(
            db,
            "wrong-id",
            SKILL_V1,  # frontmatter declares id: m5-feature-engineer
            created_by="agent:test",
        )


async def test_write_with_kind_change_rejected(db: asyncpg.Connection):
    """Re-registering a Python skill as instruction must be rejected —
    silently flipping the kind would corrupt the FK shape downstream."""
    await write_skill(db, "m5-feature-engineer", SKILL_V1, created_by="agent:test")
    flipped = SKILL_V1.replace("kind: python", "kind: instruction")
    with pytest.raises(SkillFormatError, match="kind mismatch"):
        await write_skill(db, "m5-feature-engineer", flipped, created_by="agent:test")
