"""Skill file parser + frontmatter validator — pure unit tests."""

from __future__ import annotations

from datetime import timedelta

import pytest
from ownevo_kernel.skills import (
    NEVER,
    SkillFormatError,
    build_skill_content,
    parse_skill,
    parse_stale_duration,
)

# ---------------------------------------------------------------------------
# Stale-duration parsing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, expected",
    [
        ("1h", timedelta(hours=1)),
        ("24h", timedelta(hours=24)),
        ("7d", timedelta(days=7)),
        ("30d", timedelta(days=30)),
        ("60s", timedelta(seconds=60)),
        ("5m", timedelta(minutes=5)),
        ("2w", timedelta(weeks=2)),
        ("never", NEVER),
        ("  24H  ", timedelta(hours=24)),  # whitespace + uppercase tolerated
    ],
)
def test_parse_stale_duration(text: str, expected: timedelta):
    assert parse_stale_duration(text) == expected


@pytest.mark.parametrize(
    "bad",
    ["forever", "1y", "h", "24", "", "1.5h"],
)
def test_parse_stale_duration_rejects_garbage(bad: str):
    with pytest.raises(ValueError):
        parse_stale_duration(bad)


# ---------------------------------------------------------------------------
# Markdown frontmatter
# ---------------------------------------------------------------------------


SUPPLIER_NEGOTIATION_MD = """\
---
id: supplier-negotiation
kind: instruction
created_by: nl-gen
capability_tags: [supply-chain, negotiation]

retention:
  remembers:
    - field: supplier_id
      reason: identifies the negotiation thread
  refetches:
    - source: supplier_doc:lead_time_days
      stale_after: 24h
      reason: lead time changes daily
---

# Supplier Negotiation Skill

When the user asks about a supplier, ...
"""


def test_parse_markdown_skill():
    rec = parse_skill(SUPPLIER_NEGOTIATION_MD)
    assert rec.frontmatter.id == "supplier-negotiation"
    assert rec.frontmatter.kind == "instruction"
    assert rec.frontmatter.created_by == "nl-gen"
    assert rec.frontmatter.capability_tags == ["supply-chain", "negotiation"]
    assert len(rec.frontmatter.retention.remembers) == 1
    assert rec.frontmatter.retention.remembers[0].field == "supplier_id"
    assert len(rec.frontmatter.retention.refetches) == 1
    assert rec.frontmatter.retention.refetches[0].stale_after == "24h"
    assert "Supplier Negotiation Skill" in rec.body
    assert rec.raw_frontmatter["id"] == "supplier-negotiation"


# ---------------------------------------------------------------------------
# Python docstring frontmatter
# ---------------------------------------------------------------------------


M5_FEATURE_PY = '''\
"""
---
id: m5-feature-engineer
kind: python
created_by: agent:claude-sonnet-4-6
capability_tags: [forecasting, feature-engineering]

retention:
  remembers:
    - field: feature_pipeline_version
      reason: stable identifier across the run
  refetches:
    - source: m5_calendar_features
      stale_after: 24h
      reason: holiday/event flags update daily
    - source: m5_price_history
      stale_after: 1h
      reason: price changes mid-day during promotions
---
"""

import lightgbm as lgb
import pandas as pd

def engineer_features(df, calendar_df, price_df):
    return df
'''


def test_parse_python_skill():
    rec = parse_skill(M5_FEATURE_PY)
    assert rec.frontmatter.id == "m5-feature-engineer"
    assert rec.frontmatter.kind == "python"
    assert rec.frontmatter.created_by == "agent:claude-sonnet-4-6"
    assert len(rec.frontmatter.retention.refetches) == 2
    assert {r.source for r in rec.frontmatter.retention.refetches} == {
        "m5_calendar_features",
        "m5_price_history",
    }
    # Body should start with imports, frontmatter docstring stripped
    assert rec.body.lstrip().startswith("import lightgbm")


# ---------------------------------------------------------------------------
# Stateless declaration
# ---------------------------------------------------------------------------


STATELESS_SKILL = """\
---
id: pure-formatter
kind: instruction
created_by: human:founder
retention:
  remembers: []
  refetches: []
  stateless: true
---

# Pure Formatter

Format input as JSON.
"""


def test_stateless_skill():
    rec = parse_skill(STATELESS_SKILL)
    assert rec.frontmatter.retention.stateless is True
    assert rec.frontmatter.retention.refetches == []


# ---------------------------------------------------------------------------
# Validation failures
# ---------------------------------------------------------------------------


def test_no_frontmatter_raises():
    with pytest.raises(SkillFormatError, match="No frontmatter"):
        parse_skill("# Just a markdown body, no frontmatter\n")


# ---------------------------------------------------------------------------
# Bare-Python-frontmatter Postel's-law fallback (Phase-3 agent failure mode)
# ---------------------------------------------------------------------------


# Reproduction of the qwen3-coder-30b output that bounced 5x in Phase 3:
# YAML frontmatter at the top, trailing `---` separator, then code body.
# Missing the `"""` docstring wrapper and the leading `---` marker.
_BARE_PY_FRONTMATTER = """\
id: m5.baseline.v1.feature_engineer
kind: python
created_by: agent:qwen3-coder-30b
capability_tags:
  - m5
  - baseline
  - feature_engineer
retention:
  stateless: true
---

from __future__ import annotations

import pandas as pd

def engineer(raw, fold):
    return pd.DataFrame({"y": [1, 2, 3]})
"""


def test_parse_skill_accepts_bare_python_frontmatter():
    """The Phase-3 agent failure mode: YAML at the top + trailing `---`
    but no docstring wrapper. Parser auto-accepts when the leading text
    is a valid YAML mapping with `id` + `kind`."""
    rec = parse_skill(_BARE_PY_FRONTMATTER)
    assert rec.frontmatter.id == "m5.baseline.v1.feature_engineer"
    assert rec.frontmatter.kind == "python"
    assert rec.frontmatter.retention.stateless is True
    # Body keeps everything after the `---\n` separator.
    assert "from __future__ import annotations" in rec.body
    assert "def engineer" in rec.body


def test_parse_skill_bare_frontmatter_with_leading_blank_lines():
    """Leading whitespace before the bare frontmatter still parses."""
    rec = parse_skill("\n\n\n" + _BARE_PY_FRONTMATTER)
    assert rec.frontmatter.id == "m5.baseline.v1.feature_engineer"


def test_parse_skill_bare_fallback_rejects_arbitrary_leading_text():
    """The fallback ONLY accepts text that loads as a YAML mapping with
    `id` + `kind`. Random comments / banners followed by a `---` don't
    match — they fall through to the canonical "No frontmatter" error."""
    bad = """\
# Random module banner
# Some commentary
---

def something(): pass
"""
    with pytest.raises(SkillFormatError, match="No frontmatter"):
        parse_skill(bad)


def test_parse_skill_bare_fallback_rejects_yaml_without_id_or_kind():
    """If the YAML at the top is missing `id` OR `kind`, the fallback
    refuses to claim the content — the canonical error fires instead.
    Avoids swallowing files where the leading YAML-looking block is
    actually unrelated configuration."""
    no_kind = """\
id: foo
created_by: x
retention:
  stateless: true
---

body
"""
    with pytest.raises(SkillFormatError, match="No frontmatter"):
        parse_skill(no_kind)

    no_id = """\
kind: python
created_by: x
retention:
  stateless: true
---

body
"""
    with pytest.raises(SkillFormatError, match="No frontmatter"):
        parse_skill(no_id)


# ---------------------------------------------------------------------------
# Half-wrapped Python frontmatter (Phase-3 v3 agent failure mode)
# ---------------------------------------------------------------------------


# Reproduction of the qwen3-coder-30b output from Phase 3 v3 (2026-05-04):
# with PR #26's kickoff prompt carrying the explicit `"""..."""` example,
# the agent now adds the OPENING docstring marker but consistently forgets
# the CLOSING one — 8/8 calls in run v3.
_HALFWRAP_PY_FRONTMATTER = '''\
"""
---
id: m5.baseline.v1.model_trainer
kind: python
created_by: agent:qwen3-coder-30b
capability_tags:
  - m5
  - baseline
  - model_trainer
retention:
  stateless: true
---

from __future__ import annotations

import lightgbm as lgb

def train(features, raw, fold):
    return None
'''


def test_parse_skill_accepts_half_wrapped_python_frontmatter():
    """The Phase-3 v3 agent failure mode: opening `\"\"\"` present,
    closing `\"\"\"` missing. Parser auto-accepts when the YAML between
    the two `---` markers is a valid mapping with `id` + `kind`."""
    rec = parse_skill(_HALFWRAP_PY_FRONTMATTER)
    assert rec.frontmatter.id == "m5.baseline.v1.model_trainer"
    assert rec.frontmatter.kind == "python"
    assert rec.frontmatter.retention.stateless is True
    # Body keeps everything after the closing `---\n`, never the
    # leading `"""`.
    assert "from __future__ import annotations" in rec.body
    assert "def train" in rec.body
    assert '"""' not in rec.body


def test_parse_skill_half_wrap_with_single_quotes():
    """Single-quote docstring marker (`'''`) handled identically to `\"\"\"`."""
    text = _HALFWRAP_PY_FRONTMATTER.replace('"""', "'''")
    rec = parse_skill(text)
    assert rec.frontmatter.id == "m5.baseline.v1.model_trainer"


def test_parse_skill_half_wrap_rejects_yaml_without_id_or_kind():
    """Half-wrap fallback also gates on `id` + `kind`. A docstring opener
    followed by a `---`-delimited block that *doesn't* parse as a skill
    frontmatter falls through — the canonical "No frontmatter" error fires."""
    bad = '''\
"""
---
foo: bar
baz: qux
---

body
'''
    with pytest.raises(SkillFormatError, match="No frontmatter"):
        parse_skill(bad)


def test_parse_skill_canonical_python_still_works():
    # Regression — the canonical Python-docstring frontmatter shape must
    # still parse identically to before the Postel's-law fallback was added.
    rec = parse_skill(M5_FEATURE_PY)
    assert rec.frontmatter.id == "m5-feature-engineer"
    assert rec.body.lstrip().startswith("import lightgbm")


def test_parse_skill_canonical_markdown_still_works():
    """Regression — canonical Markdown frontmatter unchanged."""
    rec = parse_skill(STATELESS_SKILL)
    assert rec.frontmatter.id == "pure-formatter"
    assert rec.frontmatter.kind == "instruction"


def test_unknown_kind_rejected():
    bad = """\
---
id: x
kind: not-a-kind
created_by: x
retention:
  remembers: []
  refetches: []
  stateless: true
---

body
"""
    with pytest.raises(SkillFormatError, match="kind"):
        parse_skill(bad)


def test_extra_field_rejected():
    """`extra='forbid'` keeps the schema honest — typos in the frontmatter
    don't silently no-op."""
    bad = """\
---
id: x
kind: instruction
created_by: x
typoed_field: value
retention:
  remembers: []
  refetches: []
  stateless: true
---

body
"""
    with pytest.raises(SkillFormatError, match="typoed_field"):
        parse_skill(bad)


def test_invalid_stale_after_rejected():
    bad = """\
---
id: x
kind: instruction
created_by: x
retention:
  remembers: []
  refetches:
    - source: foo
      stale_after: forever
      reason: typo for `never`
---

body
"""
    with pytest.raises(SkillFormatError, match="stale_after"):
        parse_skill(bad)


def test_missing_retention_rejected():
    """Retention is required — eval-case generator depends on it."""
    bad = """\
---
id: x
kind: instruction
created_by: x
---

body
"""
    with pytest.raises(SkillFormatError, match="retention"):
        parse_skill(bad)


def test_malformed_yaml_rejected():
    """Unparseable YAML in the frontmatter — surfaces as SkillFormatError,
    not a raw yaml.YAMLError."""
    bad = """\
---
id: x
kind: instruction
: : not yaml
---

body
"""
    with pytest.raises(SkillFormatError, match="not valid YAML"):
        parse_skill(bad)


def test_non_mapping_yaml_rejected():
    """Frontmatter that parses to a non-dict (top-level list, scalar) is
    a structural error, not a validation error."""
    bad = """\
---
- a
- b
---

body
"""
    with pytest.raises(SkillFormatError, match="must be a YAML mapping"):
        parse_skill(bad)


# ---------------------------------------------------------------------------
# build_skill_content — inverse of parse_skill, used by the structured
# write_skill tool to construct canonical skill text from agent fields.
# ---------------------------------------------------------------------------


def test_build_skill_content_python_round_trip():
    """The canonical Python shape built from structured fields parses
    back to those exact fields. This is the contract the structured
    write_skill tool relies on."""
    text = build_skill_content(
        skill_id="m5.baseline.v1.feature_engineer",
        kind="python",
        body="from __future__ import annotations\n\ndef engineer(df, prices, calendar):\n    return df\n",
        capability_tags=["m5", "feature-engineering"],
        retention={"stateless": True},
        created_by="agent:claude-opus-4-7",
    )
    rec = parse_skill(text)
    assert rec.frontmatter.id == "m5.baseline.v1.feature_engineer"
    assert rec.frontmatter.kind == "python"
    assert rec.frontmatter.created_by == "agent:claude-opus-4-7"
    assert rec.frontmatter.capability_tags == ["m5", "feature-engineering"]
    assert rec.frontmatter.retention.stateless is True
    assert "def engineer" in rec.body
    assert "from __future__ import annotations" in rec.body


def test_build_skill_content_instruction_round_trip():
    """Markdown skill kinds use the `---` fence shape, not the docstring."""
    text = build_skill_content(
        skill_id="supplier-negotiation",
        kind="instruction",
        body="When the user asks about a supplier, ...\n",
        capability_tags=["supply-chain"],
        retention={
            "remembers": [{"field": "supplier_id", "reason": "thread id"}],
            "refetches": [],
        },
        created_by="human:founder",
    )
    rec = parse_skill(text)
    assert rec.frontmatter.id == "supplier-negotiation"
    assert rec.frontmatter.kind == "instruction"
    assert len(rec.frontmatter.retention.remembers) == 1
    # Markdown shape: no docstring wrapper.
    assert '"""' not in text


def test_build_skill_content_default_retention_is_stateless():
    """Omitting retention defaults to stateless — the common case for
    pure-function skills."""
    text = build_skill_content(
        skill_id="x",
        kind="python",
        body="def f(): pass",
        created_by="agent:test",
    )
    rec = parse_skill(text)
    assert rec.frontmatter.retention.stateless is True


def test_build_skill_content_omits_capability_tags_when_empty():
    """Empty capability_tags should not appear in the frontmatter (clean
    output) but the parsed result still has an empty list."""
    text = build_skill_content(
        skill_id="x",
        kind="python",
        body="def f(): pass",
        capability_tags=[],
        retention={"stateless": True},
        created_by="agent:test",
    )
    assert "capability_tags" not in text
    rec = parse_skill(text)
    assert rec.frontmatter.capability_tags == []


def test_build_skill_content_body_trailing_newline_normalized():
    """Body trailing newlines normalize to exactly one. Stable canonical output."""
    base_args = dict(
        skill_id="x",
        kind="python",
        retention={"stateless": True},
        created_by="agent:test",
    )
    no_newline = build_skill_content(body="def f(): pass", **base_args)
    one_newline = build_skill_content(body="def f(): pass\n", **base_args)
    many_newlines = build_skill_content(body="def f(): pass\n\n\n", **base_args)
    assert no_newline == one_newline == many_newlines


def test_build_skill_content_with_refetches():
    """Refetch retention with stale_after gets validated by the parser."""
    text = build_skill_content(
        skill_id="m5.feature",
        kind="python",
        body="def f(): pass",
        retention={
            "refetches": [
                {"source": "m5_calendar", "stale_after": "24h", "reason": "daily"},
            ],
        },
        created_by="agent:test",
    )
    rec = parse_skill(text)
    assert len(rec.frontmatter.retention.refetches) == 1
    assert rec.frontmatter.retention.refetches[0].stale_after == "24h"


