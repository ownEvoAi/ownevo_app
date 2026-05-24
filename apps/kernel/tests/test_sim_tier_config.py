"""Tests for `sim_tier.py` — Track 9.0.2.

Pins the wire-format invariants of `SimTier` + `MockSimConfig`:

  * SimTier values match the DB CHECK constraint in migration 0018
    ('real', 'mock', 'replay'). Drift between the Python enum and the
    SQL CHECK would silently let a row land in a tier no Python code
    knows how to dispatch.
  * MockSimConfig validates accuracy values in [0, 1] — out-of-range
    floats should be caught at parse time, not at solver runtime
    where they'd produce nonsensical case counts.
  * accuracy_for() returns the curve when in range, default when past.
  * JSON round-trip is byte-stable so the workflows.mock_sim_config
    jsonb column doesn't churn between read and write.
"""

from __future__ import annotations

import pytest
from ownevo_kernel.sim_tier import MockSimConfig, SimTier
from pydantic import ValidationError

# ---------------------------------------------------------------------------
# SimTier ↔ DB CHECK parity
# ---------------------------------------------------------------------------


def test_sim_tier_string_values_match_db_check_constraint() -> None:
    """Migration 0018: `CHECK (sim_tier IN ('real', 'mock', 'replay'))`.
    A drift would let an enum value land in a workflow row that no
    iteration_runner branch handles."""
    assert {t.value for t in SimTier} == {"real", "mock", "replay"}


def test_sim_tier_str_subclass() -> None:
    """StrEnum makes `SimTier.MOCK == 'mock'` true — that's what lets
    `row['sim_tier'] == SimTier.MOCK` work without an explicit cast."""
    assert SimTier.MOCK == "mock"
    assert SimTier.REAL == "real"


# ---------------------------------------------------------------------------
# MockSimConfig validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("accuracy", [-0.1, 1.1, 2.0, -1.0])
def test_accuracy_out_of_range_rejected(accuracy: float) -> None:
    with pytest.raises(ValidationError, match="out of range"):
        MockSimConfig(accuracy_per_iteration=[0.5, accuracy, 0.8])


@pytest.mark.parametrize("accuracy", [-0.1, 1.1, 2.0])
def test_default_accuracy_out_of_range_rejected(accuracy: float) -> None:
    with pytest.raises(ValidationError):
        MockSimConfig(default_accuracy=accuracy)


def test_negative_seed_rejected() -> None:
    with pytest.raises(ValidationError):
        MockSimConfig(seed=-1)


def test_empty_curve_is_valid_uses_default() -> None:
    """A config with no curve at all is fine — every iteration uses
    `default_accuracy`. Useful when an operator wants a flat-accuracy
    mock workflow without authoring per-iteration values."""
    config = MockSimConfig(default_accuracy=0.7)
    assert config.accuracy_for(0) == 0.7
    assert config.accuracy_for(100) == 0.7


# ---------------------------------------------------------------------------
# accuracy_for() lookup semantics
# ---------------------------------------------------------------------------


def test_accuracy_for_in_curve_range() -> None:
    config = MockSimConfig(
        accuracy_per_iteration=[0.5, 0.6, 0.7],
        default_accuracy=0.9,
    )
    assert config.accuracy_for(0) == 0.5
    assert config.accuracy_for(1) == 0.6
    assert config.accuracy_for(2) == 0.7


def test_accuracy_for_past_curve_uses_default() -> None:
    config = MockSimConfig(
        accuracy_per_iteration=[0.5, 0.6],
        default_accuracy=0.9,
    )
    assert config.accuracy_for(2) == 0.9
    assert config.accuracy_for(99) == 0.9


def test_accuracy_for_negative_iteration_uses_default() -> None:
    """Negative iteration_index can only mean the runner mis-wired
    something. accuracy_for degrades to default rather than raising
    so `sim_tier='mock'` runs don't hard-fail the loop over what is
    arguably a programmer error — the audit trail will still mark
    the prediction as mock-sourced."""
    config = MockSimConfig(default_accuracy=0.8)
    assert config.accuracy_for(-1) == 0.8


# ---------------------------------------------------------------------------
# JSON round-trip
# ---------------------------------------------------------------------------


def test_json_round_trip_is_stable() -> None:
    """workflows.mock_sim_config is jsonb — the round-trip must
    preserve every field byte-for-byte, otherwise an audit-chain
    canonicalization comparing before-vs-after would spuriously
    flag every read-modify-write as a change."""
    original = MockSimConfig(
        accuracy_per_iteration=[0.5, 0.65, 0.77, 0.80],
        default_accuracy=0.85,
        seed=123,
        sandbox_script={"status": "ok", "output": "fixed"},
    )
    payload = original.model_dump()
    restored = MockSimConfig.model_validate(payload)
    assert restored == original
