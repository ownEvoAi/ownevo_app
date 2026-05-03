"""Skill registry — read/write the `skills` + `skill_versions` tables.

Behavior contract:

  * First registration of a `skill_id` creates both the `skills` row
    (with `kind`, `capability_tags`) and a `skill_versions` row at
    `version_seq = 1`.
  * Subsequent registrations of the same `skill_id` insert a new
    `skill_versions` row with `version_seq = max + 1` and link
    `parent_version_id` to the previous head, then advance
    `skills.head_version_id`. `capability_tags` is refreshed to the
    new version's tags on every re-registration; `kind` is locked at
    first registration and a mismatch raises `SkillFormatError`.
  * The whole register is one transaction.

The registry stores the raw frontmatter dict (not the validated Pydantic
model) in `skill_versions.retention_block`. The eval-case generator walks
`retention_block['retention']['refetches']` directly without needing to
re-parse, and the schema is queryable via JSONB ops.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from uuid import UUID

import asyncpg

from .format import SkillFormatError, SkillRecord, parse_skill


@dataclass(frozen=True)
class RegisterResult:
    skill_id: str
    version_id: UUID
    version_seq: int


async def register_skill(
    conn: asyncpg.Connection,
    content: str,
    *,
    created_by: str | None = None,
    diff_summary: str | None = None,
) -> RegisterResult:
    """Parse + validate the skill file, then INSERT/UPDATE.

    `created_by` overrides the frontmatter `created_by` if provided; this
    is how the gate runner stamps "agent:claude-opus-4-7" even when the
    skill file says something else.
    """
    record = parse_skill(content)
    return await _insert_record(
        conn,
        record=record,
        content=content,
        created_by=created_by or record.frontmatter.created_by,
        diff_summary=diff_summary,
    )


async def _insert_record(
    conn: asyncpg.Connection,
    *,
    record: SkillRecord,
    content: str,
    created_by: str,
    diff_summary: str | None,
) -> RegisterResult:
    fm = record.frontmatter
    async with conn.transaction():
        existing = await conn.fetchrow(
            "SELECT kind::text AS kind, head_version_id FROM skills WHERE id = $1",
            fm.id,
        )

        if existing is None:
            await conn.execute(
                """
                INSERT INTO skills (id, kind, capability_tags)
                VALUES ($1, $2::skill_kind, $3)
                """,
                fm.id,
                fm.kind,
                list(fm.capability_tags),
            )
            parent_version_id: UUID | None = None
            next_seq = 1
        else:
            if existing["kind"] != fm.kind:
                raise SkillFormatError(
                    f"kind mismatch for skill {fm.id!r}: "
                    f"existing={existing['kind']}, new={fm.kind}",
                )
            parent_version_id = existing["head_version_id"]
            current_max = await conn.fetchval(
                "SELECT COALESCE(MAX(version_seq), 0) FROM skill_versions WHERE skill_id = $1",
                fm.id,
            )
            next_seq = current_max + 1
            # Refresh capability_tags on every registration so they don't
            # drift across versions.
            await conn.execute(
                "UPDATE skills SET capability_tags = $2 WHERE id = $1",
                fm.id,
                list(fm.capability_tags),
            )

        version_id: UUID = await conn.fetchval(
            """
            INSERT INTO skill_versions (
                skill_id, parent_version_id, version_seq,
                content, retention_block, diff_summary, created_by
            )
            VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7)
            RETURNING id
            """,
            fm.id,
            parent_version_id,
            next_seq,
            content,
            json.dumps(record.raw_frontmatter),
            diff_summary,
            created_by,
        )

        await conn.execute(
            "UPDATE skills SET head_version_id = $2 WHERE id = $1",
            fm.id,
            version_id,
        )

        return RegisterResult(
            skill_id=fm.id,
            version_id=version_id,
            version_seq=next_seq,
        )


@dataclass(frozen=True)
class SkillHead:
    skill_id: str
    kind: str
    version_id: UUID
    version_seq: int
    content: str
    created_by: str


async def get_head(conn: asyncpg.Connection, skill_id: str) -> SkillHead | None:
    """Return the current head version of `skill_id`, or None if unknown."""
    row = await conn.fetchrow(
        """
        SELECT s.id AS skill_id,
               s.kind::text AS kind,
               sv.id AS version_id,
               sv.version_seq AS version_seq,
               sv.content AS content,
               sv.created_by AS created_by
        FROM skills s
        JOIN skill_versions sv ON sv.id = s.head_version_id
        WHERE s.id = $1
        """,
        skill_id,
    )
    if row is None:
        return None
    return SkillHead(
        skill_id=row["skill_id"],
        kind=row["kind"],
        version_id=row["version_id"],
        version_seq=row["version_seq"],
        content=row["content"],
        created_by=row["created_by"],
    )


async def list_versions(conn: asyncpg.Connection, skill_id: str) -> list[dict]:
    """All versions of a skill, oldest first. Used for diffs in the UI."""
    rows = await conn.fetch(
        """
        SELECT id, version_seq, parent_version_id, created_at, created_by, diff_summary
        FROM skill_versions
        WHERE skill_id = $1
        ORDER BY version_seq ASC
        """,
        skill_id,
    )
    return [dict(r) for r in rows]
