"""write_skill compile-check — pure unit tests, no DB required.

Pins that write_skill(conn=None, ...) raises SkillFormatError for
syntactically broken Python BEFORE touching the database (conn is never
dereferenced when the error fires), and that the reported line number
is file-relative (frontmatter offset + body-relative line from compile).

This lets the agent edit the right line in the full skill file without
having to mentally re-add the frontmatter offset.
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


async def test_syntax_error_line_number_is_file_relative():
    """Line number in the error message must be file-relative (frontmatter
    offset + body-relative exc.lineno) so the agent can jump straight to
    the right line in the full skill text.

    _SYNTAX_ERROR_SKILL has 10 frontmatter newlines (---…---\n) + 1 blank
    separator line before the body, so:
      body line 1 (file line 12): def solve(tools, context):
      body line 2 (file line 13):     def inner(:  ← SyntaxError
    The error must say 'line 13'.
    """
    with pytest.raises(SkillFormatError) as exc_info:
        await write_skill(
            None,
            skill_id="tau3-retail-agent",
            content=_SYNTAX_ERROR_SKILL,
            created_by="agent:test",
        )
    assert "line 13" in str(exc_info.value), (
        f"expected 'line 13' in error (file-relative); got: {exc_info.value!r}"
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
