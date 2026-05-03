"""Skill file parser + frontmatter validator.

A skill file is a YAML frontmatter block + body. Two delimiter conventions
per `docs/SKILL_FORMAT.md`:

  * Markdown skill — leading `---\\n...\\n---\\n` block
  * Python skill — module docstring whose contents are `---\\n...\\n---`

`parse_skill(content)` returns a `SkillRecord`:
  - `frontmatter`: the validated `SkillFrontmatter` Pydantic model
  - `raw_frontmatter`: the parsed-but-unvalidated dict (what gets stored
    in `skill_versions.retention_block` so the eval-case generator can
    walk `retention.refetches` directly)
  - `body`: the rest of the file (executable Python or markdown)

`SkillFormatError` is the only exception that escapes the parser.
Pydantic validation errors get wrapped so the caller doesn't have to know
about Pydantic internals.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .retention import parse_stale_duration

# ---------------------------------------------------------------------------
# Frontmatter schema (mirrors SKILL_FORMAT.md)
# ---------------------------------------------------------------------------


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RetentionRemember(_Strict):
    field: str
    reason: str = ""


class RetentionRefetch(_Strict):
    source: str
    stale_after: str  # validated by parse_stale_duration below
    reason: str = ""

    @field_validator("stale_after")
    @classmethod
    def _validate_stale(cls, v: str) -> str:
        # Raise here so the error message is attached to the right field.
        parse_stale_duration(v)
        return v


class Retention(_Strict):
    remembers: list[RetentionRemember] = Field(default_factory=list)
    refetches: list[RetentionRefetch] = Field(default_factory=list)
    stateless: bool = False


class SkillFrontmatter(_Strict):
    id: str
    kind: Literal["python", "instruction", "composite"]
    created_by: str
    capability_tags: list[str] = Field(default_factory=list)
    retention: Retention


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


class SkillFormatError(ValueError):
    """Skill file failed parsing or validation. The registry surfaces this
    as a `tool_call_result` error so the agent gets actionable feedback."""


@dataclass(frozen=True)
class SkillRecord:
    frontmatter: SkillFrontmatter
    raw_frontmatter: dict[str, Any]
    body: str


# Markdown frontmatter: `---\n...\n---\n`
_MD_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?(.*)\Z", re.DOTALL)

# Python frontmatter: a leading docstring `"""\n---\n...\n---\n"""`
_PY_RE = re.compile(
    r'\A\s*(?:"""|\'\'\')\s*\n---\s*\n(.*?)\n---\s*\n(?:"""|\'\'\')\s*\n?(.*)\Z',
    re.DOTALL,
)


def parse_skill(content: str) -> SkillRecord:
    """Parse a skill file into validated frontmatter + body.

    Raises `SkillFormatError` on any failure (no frontmatter, malformed
    YAML, schema-violation, unknown stale_after).
    """
    frontmatter_text, body = _split(content)
    try:
        raw = yaml.safe_load(frontmatter_text)
    except yaml.YAMLError as e:
        raise SkillFormatError(f"Frontmatter is not valid YAML: {e}") from e

    if not isinstance(raw, dict):
        raise SkillFormatError(
            f"Frontmatter must be a YAML mapping, got {type(raw).__name__}",
        )

    try:
        fm = SkillFrontmatter.model_validate(raw)
    except ValidationError as e:
        # Surface Pydantic's location info but in a single short message.
        details = "; ".join(
            f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in e.errors()
        )
        raise SkillFormatError(f"Frontmatter validation failed: {details}") from e

    return SkillRecord(frontmatter=fm, raw_frontmatter=raw, body=body)


def _split(content: str) -> tuple[str, str]:
    """Return `(frontmatter_text, body)` or raise."""
    m = _PY_RE.match(content)
    if m is not None:
        return m.group(1), m.group(2)
    m = _MD_RE.match(content)
    if m is not None:
        return m.group(1), m.group(2)
    raise SkillFormatError(
        "No frontmatter found. Expected leading `---` block (markdown) or "
        "`\"\"\"\\n---\\n...\\n---\\n\"\"\"` docstring (python).",
    )
