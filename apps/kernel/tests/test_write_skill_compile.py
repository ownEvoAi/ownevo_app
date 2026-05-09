"""write_skill compile-check — pure unit tests, no DB required.

Pins that write_skill(conn=None, ...) raises SkillFormatError for
syntactically broken Python BEFORE touching the database (conn is never
dereferenced when the error fires), and that the reported line number
is body-relative (not offset by the YAML frontmatter header).

This is the regression guard for the compile(parsed.body, ...) fix in
agent_tools/skills.py: a SyntaxError on line 3 of the skill body must
appear as "line 3" in the error, not "line 11" (which compile(content, ...)
would report due to the ~8-line frontmatter docstring).
"""

from __future__ import annotations

import pytest
from ownevo_kernel.agent_tools.skills import SkillFormatError, write_skill


# Bare-frontmatter format (same style as the DB-backed tests).
# Body lines: 1=blank, 2=def solve, 3=def inner(: ← SyntaxError here
_SYNTAX_ERROR_SKILL = """\
---
id: tau3-retail-agent
kind: python
created_by: agent:claude-sonnet-4-6
capability_tags: [tau3]
retention:
  remembers: []
  refetches: []
  stateless: true
---

def solve(tools, context):
    def inner(:
        pass
"""

_VALID_SKILL = """\
---
id: tau3-retail-agent
kind: python
created_by: agent:claude-sonnet-4-6
capability_tags: [tau3]
retention:
  remembers: []
  refetches: []
  stateless: true
---

def solve(tools, context):
    return None
"""

_INSTRUCTION_SKILL = """\
---
id: tau3-retail-agent
kind: instruction
created_by: agent:claude-sonnet-4-6
capability_tags: [tau3]
retention:
  remembers: []
  refetches: []
  stateless: true
---

Resolve the customer's request in under 4 tool calls.
"""


async def test_syntax_error_raises_skill_format_error():
    """SyntaxError in body → SkillFormatError before any DB call."""
    with pytest.raises(SkillFormatError, match=r"SyntaxError on line \d"):
        await write_skill(
            None,  # conn=None is safe: error fires before register_skill(conn, ...)
            skill_id="tau3-retail-agent",
            content=_SYNTAX_ERROR_SKILL,
            created_by="agent:test",
        )


async def test_syntax_error_line_number_is_body_relative():
    """Line number in the error message must be relative to the body, not
    the full file with frontmatter. parse_skill strips the leading blank
    line after the closing ---, so the body is:
      line 1: def solve(tools, context):
      line 2: def inner(:   ← SyntaxError here
    The error must say 'line 2'.

    This would fail if compile(content, ...) were used instead of
    compile(parsed.body, ...) because the frontmatter docstring adds ~8
    lines of offset, reporting a much higher line number.
    """
    with pytest.raises(SkillFormatError) as exc_info:
        await write_skill(
            None,
            skill_id="tau3-retail-agent",
            content=_SYNTAX_ERROR_SKILL,
            created_by="agent:test",
        )
    assert "line 2" in str(exc_info.value), (
        f"expected 'line 2' in error (body-relative); got: {exc_info.value!r}"
    )


async def test_instruction_skill_skips_compile_check():
    """instruction-kind skills are natural language — no compile gate.
    A 'SyntaxError'-looking body must not raise SkillFormatError.
    The function reaches register_skill(None, ...) which raises AttributeError
    (conn=None), but NOT SkillFormatError with 'SyntaxError' in the message."""
    try:
        await write_skill(
            None,
            skill_id="tau3-retail-agent",
            content=_INSTRUCTION_SKILL,
            created_by="agent:test",
        )
    except SkillFormatError as exc:
        assert "SyntaxError" not in str(exc), (
            f"instruction skill triggered compile check unexpectedly: {exc}"
        )
    except Exception:
        pass  # AttributeError from conn=None reaching register_skill is expected
