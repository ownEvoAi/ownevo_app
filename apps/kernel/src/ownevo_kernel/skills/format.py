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

# Postel's-law fallback #1 — half-wrapped Python frontmatter.
# Phase-3 v3 (2026-05-04) on qwen3-coder-30b: with a kickoff prompt
# carrying the canonical `"""\n---\n...\n---\n"""` example explicit-
# ly, the agent **adds the opening `"""` but forgets the closing
# one** in 8/8 write_skill calls. Shape:
#
#     """
#     ---
#     id: ...
#     kind: python
#     ---
#
#     <code>
#
# Same as the canonical `_PY_RE` minus the trailing `"""` requirement.
# The captured frontmatter still goes through `_looks_like_skill_-
# frontmatter` for safety.
_PY_HALFWRAP_RE = re.compile(
    r'\A\s*(?:"""|\'\'\')\s*\n---\s*\n(.*?)\n---\s*\n(.*)\Z',
    re.DOTALL,
)

# Postel's-law fallback #2 — bare Python frontmatter.
# Phase-3 v1+v2 (2026-05-04) on qwen3-coder-30b: agents routinely
# emit Python skill bodies with the YAML frontmatter at the top but
# **no docstring wrapper at all** — the model strips both the `"""`
# and the leading `---`, leaving:
#
#     id: m5.baseline.v1.feature_engineer
#     kind: python
#     ...
#     ---
#
#     <code>
#
# This regex captures that shape: the leading run up to the first
# `\n---\n` is the candidate frontmatter, the rest is the body. The
# parser still validates that the captured text loads as a YAML mapping
# with `id` + `kind` keys before accepting (so e.g. `# random comment\n
# foo bar\n---\nbody` doesn't sneak through).
_PY_BARE_RE = re.compile(r"\A\s*(\S.*?)\n---\s*\n(.*)\Z", re.DOTALL)


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
    """Return `(frontmatter_text, body)` or raise.

    Order matters: the canonical Python-docstring and Markdown-fence
    shapes are tried first and accepted strictly. Only when both miss
    do the Postel's-law fallbacks engage (half-wrapped Python first,
    then bare Python), and they only accept text whose candidate
    frontmatter parses as a YAML mapping with at least `id` + `kind`.
    """
    m = _PY_RE.match(content)
    if m is not None:
        return m.group(1), m.group(2)
    m = _MD_RE.match(content)
    if m is not None:
        return m.group(1), m.group(2)
    m = _PY_HALFWRAP_RE.match(content)
    if m is not None:
        candidate, body = m.group(1), m.group(2)
        if _looks_like_skill_frontmatter(candidate):
            return candidate, body
    m = _PY_BARE_RE.match(content)
    if m is not None:
        candidate, body = m.group(1), m.group(2)
        if _looks_like_skill_frontmatter(candidate):
            return candidate, body
    raise SkillFormatError(
        "No frontmatter found. Expected leading `---` block (markdown) or "
        "`\"\"\"\\n---\\n...\\n---\\n\"\"\"` docstring (python).",
    )


# ---------------------------------------------------------------------------
# Canonical content builder — inverse of parse_skill
# ---------------------------------------------------------------------------


def build_skill_content(
    *,
    skill_id: str,
    kind: str,
    body: str,
    capability_tags: list[str] | None = None,
    retention: dict[str, Any] | None = None,
    created_by: str,
) -> str:
    """Construct canonical skill text from structured fields.

    The agent's `write_skill` tool takes structured fields rather than a
    pre-serialized YAML+body string — the kernel constructs the canonical
    file here. Output round-trips through `parse_skill` (validation is
    still single-source-of-truth in the parser).

    For ``kind="python"`` returns the canonical docstring shape:

        \"\"\"
        ---
        id: ...
        kind: python
        ...
        ---
        \"\"\"

        <body>

    For ``kind="instruction"`` / ``"composite"`` returns the canonical
    Markdown shape with a leading `---` fence.
    """
    fm: dict[str, Any] = {
        "id": skill_id,
        "kind": kind,
        "created_by": created_by,
    }
    if capability_tags:
        fm["capability_tags"] = list(capability_tags)
    fm["retention"] = retention if retention is not None else {"stateless": True}

    yaml_text = yaml.safe_dump(fm, sort_keys=False, default_flow_style=False)
    body_text = body.rstrip("\n") + "\n"

    if kind == "python":
        return f'"""\n---\n{yaml_text}---\n"""\n\n{body_text}'
    return f"---\n{yaml_text}---\n\n{body_text}"


def _looks_like_skill_frontmatter(text: str) -> bool:
    """Cheap pre-check: does ``text`` parse as a YAML mapping with
    at least ``id`` and ``kind`` keys? Used by the bare-frontmatter
    fallback to avoid swallowing arbitrary leading text that happens
    to sit before a `\\n---\\n` separator.
    """
    try:
        parsed = yaml.safe_load(text)
    except yaml.YAMLError:
        return False
    return isinstance(parsed, dict) and "id" in parsed and "kind" in parsed
