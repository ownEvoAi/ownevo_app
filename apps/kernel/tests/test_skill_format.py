"""Skill file parser + frontmatter validator — pure unit tests."""

from __future__ import annotations

from datetime import timedelta

import pytest
from ownevo_kernel.skills import (
    NEVER,
    SkillFormatError,
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
