"""Tests for `clustering.label_eval.fixtures` (B3.5).

Pins:
  - 20-case cardinality (the W3 deliverable count)
  - cluster_id uniqueness
  - dominant_hint ∈ taxonomy
  - signature format (bracket header + rmsse + peak + day + hints)
  - failure-mode coverage (no hint left unrepresented)
  - ground-truth label length cap

If a future commit adds a 21st case or drops one, the cardinality fail
catches it. If a hint is removed from the taxonomy, the coverage test
flags it.
"""

from __future__ import annotations

import dataclasses
import re

import pytest
from ownevo_kernel.clustering.label_eval.fixtures import (
    LABELED_CLUSTER_CASES,
)

# Format: "<series>_validation [<cat>/<dept> @ <state>/<store>]
#         rmsse=<x.xx> peak <±N.NN> day <n> hints=[<tags>]"
# We don't pin every numeric tolerance — only the structural shape so a
# typo in a fixture is caught.
_SIG_RE = re.compile(
    r"^[A-Z0-9_]+_validation \[[A-Z]+/[A-Z0-9_]+ @ [A-Z]{2}/[A-Z]{2}_\d+\] "
    r"rmsse=\d+\.\d{2} peak [+-]\d+\.\d{2} day \d+ hints=\[[a-z\-,]+\]$"
)


_KNOWN_HINTS = {
    "under-forecast",
    "over-forecast",
    "zero-inflated",
    "high-variance",
    "flat-prediction",
    "mixed",
}


def test_exactly_20_cases():
    assert len(LABELED_CLUSTER_CASES) == 20


def test_cluster_ids_unique():
    ids = [c.cluster_id for c in LABELED_CLUSTER_CASES]
    assert len(ids) == len(set(ids))


def test_cluster_ids_match_judgment_pattern():
    """The judge schema's cluster_id pattern is `^[a-z0-9]([a-z0-9-]*[a-z0-9])?$`.
    Fixtures must conform so the judge can echo them back."""
    pattern = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$")
    for c in LABELED_CLUSTER_CASES:
        assert pattern.match(c.cluster_id), f"bad cluster_id: {c.cluster_id!r}"


def test_dominant_hints_in_taxonomy():
    for c in LABELED_CLUSTER_CASES:
        assert c.dominant_hint in _KNOWN_HINTS, (
            f"{c.cluster_id}: unknown dominant_hint {c.dominant_hint!r}"
        )


def test_member_signatures_format():
    for c in LABELED_CLUSTER_CASES:
        for sig in c.member_signatures:
            assert _SIG_RE.match(sig), (
                f"{c.cluster_id}: signature does not match expected format: {sig!r}"
            )


def test_at_least_three_members_per_case():
    for c in LABELED_CLUSTER_CASES:
        assert len(c.member_signatures) >= 3, (
            f"{c.cluster_id}: only {len(c.member_signatures)} members"
        )


def test_at_most_eight_members_per_case():
    """Soft cap — keeps the labeler prompt bounded. Bump this with the
    fixture if a future case legitimately needs more."""
    for c in LABELED_CLUSTER_CASES:
        assert len(c.member_signatures) <= 8, (
            f"{c.cluster_id}: {len(c.member_signatures)} members exceeds soft cap"
        )


def test_ground_truth_labels_nonempty_and_short():
    for c in LABELED_CLUSTER_CASES:
        assert c.ground_truth_label.strip()
        assert len(c.ground_truth_label) <= 80, (
            f"{c.cluster_id}: ground_truth_label too long "
            f"({len(c.ground_truth_label)} > 80)"
        )


def test_failure_mode_coverage():
    """Every dominant_hint we use should appear at least once across the
    20 fixtures so the per-hint slicing has signal."""
    seen = {c.dominant_hint for c in LABELED_CLUSTER_CASES}
    expected_minimum = {
        "under-forecast",
        "over-forecast",
        "zero-inflated",
        "flat-prediction",
        "high-variance",
    }
    missing = expected_minimum - seen
    assert not missing, f"failure modes uncovered: {missing}"


def test_dataclass_is_frozen():
    case = LABELED_CLUSTER_CASES[0]
    with pytest.raises(dataclasses.FrozenInstanceError):
        case.cluster_id = "mutated"  # type: ignore[misc]


def test_frozen_dataclass_hashable():
    """Frozen dataclasses are hashable; build a set to confirm."""
    s = {c for c in LABELED_CLUSTER_CASES}
    assert len(s) == len(LABELED_CLUSTER_CASES)


def test_under_and_over_forecast_both_represented():
    """The judge must learn the difference; both biases must be in the set."""
    hints = [c.dominant_hint for c in LABELED_CLUSTER_CASES]
    assert hints.count("under-forecast") >= 3
    assert hints.count("over-forecast") >= 3
