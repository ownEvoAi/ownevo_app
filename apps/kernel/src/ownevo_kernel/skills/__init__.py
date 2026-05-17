"""Skill registry — YAML-frontmatter parsing + DB read/write.

A skill is the agent's instruction (or Python module) plus its
**retention contract** — a typed YAML frontmatter block declaring what
the agent remembers (``remembers:``), what it refetches (``refetches:``
with stale-after durations), and whether it carries state at all
(``stateless: bool``). See ``docs/SKILL_FORMAT.md`` for the format spec.

Surface area:

- Format / parsing (``.format``)
    - ``parse_skill(content)`` — frontmatter + body → ``SkillRecord``
    - ``build_skill_content(frontmatter, body)`` — inverse for round-trip
    - ``SkillFormatError`` — wraps Pydantic + YAML errors

- Registry / persistence (``.registry``)
    - ``register_skill(...)`` — inserts a new ``skill_versions`` row,
      advances ``skills.latest_proposed_version_id`` (NOT
      ``head_version_id`` — only the gate runner advances HEAD; see
      migration 0003)
    - ``get_head(...)`` / ``list_versions(...)`` — read paths

- Retention duration parsing (``.retention``)
    - ``parse_stale_duration("PT2H")`` → timedelta
    - ``NEVER`` — sentinel for "never expires"

Parser accepts Markdown skills (leading ``---\\n...\\n---\\n`` block)
and Python skills (module docstring whose contents are ``---\\n...\\n---``).
Both produce the same ``SkillRecord``. Nested structures in frontmatter
are allowed but constrained by the strict Pydantic schemas (``extra="forbid"``).
"""

from .format import (
    Retention,
    RetentionRefetch,
    RetentionRemember,
    SkillFormatError,
    SkillFrontmatter,
    SkillRecord,
    build_skill_content,
    parse_skill,
)
from .registry import RegisterResult, SkillHead, get_head, list_versions, register_skill
from .retention import NEVER, parse_stale_duration

__all__ = [
    "NEVER",
    "RegisterResult",
    "Retention",
    "RetentionRefetch",
    "RetentionRemember",
    "SkillFormatError",
    "SkillFrontmatter",
    "SkillHead",
    "SkillRecord",
    "build_skill_content",
    "get_head",
    "list_versions",
    "parse_skill",
    "parse_stale_duration",
    "register_skill",
]
