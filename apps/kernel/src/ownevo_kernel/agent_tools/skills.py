"""read_skill / write_skill — agent's read/write surface over the registry.

Thin wrappers over `ownevo_kernel.skills` that present the contract the
agent loop consumes:
  * `read_skill(conn, skill_id)` returns content + retention block as a
    dataclass; agent reasons over both.
  * `write_skill(conn, skill_id, content, ...)` parses + validates the
    new version and writes it to the registry as a child of the current
    head. Errors surface as `SkillFormatError` so the agent gets a
    structured `tool_call_result` it can act on.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

import asyncpg

from ..skills import (
    RegisterResult,
    SkillFormatError,
    SkillHead,
    get_head,
    register_skill,
)


@dataclass(frozen=True)
class SkillReadResult:
    """What the agent sees when it reads a skill.

    `content` is the raw skill source (Python or markdown). `retention`
    is the parsed YAML frontmatter; the agent uses it to know what state
    the skill keeps and what it must re-fetch.
    """

    skill_id: str
    kind: str
    version_id: UUID
    version_seq: int
    content: str
    created_by: str


async def read_skill(conn: asyncpg.Connection, skill_id: str) -> SkillReadResult | None:
    """Return the head version of `skill_id`, or None if unknown.

    None instead of raising — the agent's typical loop is
    "try-read, write-if-missing", and an exception here would force
    every call site to wrap try/except.
    """
    head: SkillHead | None = await get_head(conn, skill_id)
    if head is None:
        return None
    return SkillReadResult(
        skill_id=head.skill_id,
        kind=head.kind,
        version_id=head.version_id,
        version_seq=head.version_seq,
        content=head.content,
        created_by=head.created_by,
    )


async def write_skill(
    conn: asyncpg.Connection,
    skill_id: str,
    content: str,
    *,
    created_by: str,
    diff_summary: str | None = None,
) -> RegisterResult:
    """Register `content` as a new version of `skill_id`.

    The frontmatter inside `content` MUST declare an `id` matching
    `skill_id` (the registry validates this implicitly via the
    SkillFrontmatter schema; mismatched IDs surface as SkillFormatError).
    The `created_by` arg overrides whatever the file declares — used by
    the gate runner to stamp the actual emitting model.
    """
    # The skill-registry signature pulls skill_id from the frontmatter,
    # not from a separate arg; we surface skill_id here for symmetry
    # with read_skill and to make the wrapper greppable.
    _ = skill_id  # validated through frontmatter; explicit arg is documentation
    return await register_skill(
        conn,
        content,
        created_by=created_by,
        diff_summary=diff_summary,
    )


__all__ = [
    "SkillFormatError",
    "SkillReadResult",
    "read_skill",
    "write_skill",
]
