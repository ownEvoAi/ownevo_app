"""Tests for `ReplaySimConfig` — Track 9.0.3.

What we pin:

  * Required fields — `source_iteration_id` is mandatory (a replay
    config that doesn't say what to replay against is meaningless).
  * Fallback validation — only the three documented values are
    accepted; typos become loud errors at parse time, not silent
    "treated as 'error'" surprises.
  * Default fallback is 'error' — safe-by-default. Silent degradation
    would hide real correctness gaps during validation.
  * JSON round-trip is stable so workflows.replay_sim_config jsonb
    doesn't churn between read and write.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from ownevo_kernel.sim_tier import ReplaySimConfig
from pydantic import ValidationError


def test_source_iteration_id_required() -> None:
    """A replay config without source_iteration_id can't point at
    anything to replay against. Pydantic should reject construction."""
    with pytest.raises(ValidationError, match="source_iteration_id"):
        ReplaySimConfig()  # type: ignore[call-arg]


def test_default_fallback_is_error() -> None:
    """Silent fallback hides correctness gaps. The safe default makes
    the operator explicitly opt into mock/real degradation."""
    config = ReplaySimConfig(source_iteration_id=uuid4())
    assert config.fallback == "error"


@pytest.mark.parametrize("fallback", ["error", "mock", "real"])
def test_accepted_fallback_values(fallback: str) -> None:
    config = ReplaySimConfig(source_iteration_id=uuid4(), fallback=fallback)  # type: ignore[arg-type]
    assert config.fallback == fallback


def test_invalid_fallback_rejected() -> None:
    """Typos like 'errror' or 'fallback' would currently be silently
    coerced to 'error' at runtime if the field were loosely typed.
    The Literal['error', 'mock', 'real'] makes them loud."""
    with pytest.raises(ValidationError):
        ReplaySimConfig(
            source_iteration_id=uuid4(),
            fallback="invalid-mode",  # type: ignore[arg-type]
        )


def test_json_round_trip_is_stable() -> None:
    """workflows.replay_sim_config is jsonb. Read-modify-write must
    preserve the payload byte-for-byte so audit chain canonicalization
    doesn't flag unchanged configs as edits."""
    iter_id = uuid4()
    original = ReplaySimConfig(source_iteration_id=iter_id, fallback="mock")
    payload = original.model_dump(mode="json")
    restored = ReplaySimConfig.model_validate(payload)
    assert restored == original
    assert restored.source_iteration_id == iter_id
