"""Tests for `sandbox/mock_sim.py` — Track 9.0.2 Slice B.

What we pin:

  1. Protocol conformance — MockSimSandbox satisfies the
     `SandboxRuntime` runtime_checkable Protocol; `isinstance` check
     passes so the type-narrowing at call sites works.
  2. Fixed-response shape — every `.run(...)` returns the same canned
     SandboxResult; cursor doesn't advance.
  3. Sequence-response shape — `.run(...)` walks the sequence; past
     the end, `default_response` (explicit or implicit) applies.
  4. Default-fields fill — partial response dicts get harmless
     defaults (ok / empty output / 10ms duration); error entries
     surface SandboxResult's own __post_init__ invariant when `error`
     is missing.
  5. None script → permissive default — `from_script(None)` returns a
     sandbox that always emits the implicit OK reply (useful for
     tests that need Protocol conformance only).
  6. Run args are accepted but ignored — passing different `code` /
     `timeout_seconds` / `memory_mb` doesn't change the output, only
     the script does.
"""

from __future__ import annotations

import pytest
from ownevo_kernel.sandbox import (
    MockSimSandbox,
    SandboxResult,
    SandboxRuntime,
)

# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


def test_mock_sim_sandbox_satisfies_sandbox_runtime_protocol() -> None:
    """`SandboxRuntime` is `@runtime_checkable`. A MockSimSandbox
    instance must pass `isinstance` so call sites that type-narrow on
    the Protocol accept it."""
    sandbox = MockSimSandbox.from_script(None)
    assert isinstance(sandbox, SandboxRuntime)


# ---------------------------------------------------------------------------
# Fixed-response shape
# ---------------------------------------------------------------------------


async def test_fixed_response_returns_same_result_every_call() -> None:
    sandbox = MockSimSandbox.from_script(
        {"status": "ok", "output": "0.65", "duration_ms": 50},
    )
    results = [
        await sandbox.run(code="ignored", timeout_seconds=30, memory_mb=512)
        for _ in range(3)
    ]
    assert all(r.status == "ok" for r in results)
    assert all(r.output == "0.65" for r in results)
    assert all(r.duration_ms == 50 for r in results)
    # All three must be the same object — fixed_response is reused.
    assert results[0] is results[1] is results[2]


# ---------------------------------------------------------------------------
# Sequence-response shape
# ---------------------------------------------------------------------------


async def test_sequence_walks_in_order_then_falls_back_to_default() -> None:
    sandbox = MockSimSandbox.from_script(
        {
            "sequence": [
                {"status": "ok", "output": "step-0"},
                {"status": "ok", "output": "step-1"},
                {"status": "ok", "output": "step-2"},
            ],
            "default_response": {"status": "ok", "output": "default"},
        },
    )
    outputs = []
    for _ in range(5):  # exceed sequence length to exercise default
        r = await sandbox.run(code="x", timeout_seconds=1, memory_mb=1)
        outputs.append(r.output)
    assert outputs == ["step-0", "step-1", "step-2", "default", "default"]


async def test_sequence_without_default_repeats_last_entry() -> None:
    """Convenience: omit `default_response` and the sandbox repeats
    the final sequence entry. Matches the natural reading of "after
    the script ends, the agent just keeps doing the last thing."""
    sandbox = MockSimSandbox.from_script(
        {
            "sequence": [
                {"status": "ok", "output": "first"},
                {"status": "ok", "output": "last"},
            ],
        },
    )
    outputs = []
    for _ in range(4):
        r = await sandbox.run(code="x", timeout_seconds=1, memory_mb=1)
        outputs.append(r.output)
    assert outputs == ["first", "last", "last", "last"]


async def test_empty_sequence_with_default_returns_default() -> None:
    sandbox = MockSimSandbox.from_script(
        {
            "sequence": [],
            "default_response": {"status": "ok", "output": "fallback"},
        },
    )
    r = await sandbox.run(code="x", timeout_seconds=1, memory_mb=1)
    assert r.output == "fallback"


# ---------------------------------------------------------------------------
# Default-fields fill / SandboxResult invariants
# ---------------------------------------------------------------------------


async def test_minimal_ok_entry_fills_defaults() -> None:
    """An entry with only `status: 'ok'` should produce a valid
    SandboxResult — empty output, 10ms duration, exit 0, no error."""
    sandbox = MockSimSandbox.from_script({"status": "ok"})
    r = await sandbox.run(code="", timeout_seconds=1, memory_mb=1)
    assert r.status == "ok"
    assert r.output == ""
    assert r.stderr == ""
    assert r.exit_code == 0
    assert r.duration_ms == 10
    assert r.error is None
    assert r.error_class is None


def test_error_entry_without_error_field_fails_construction() -> None:
    """SandboxResult.__post_init__ requires `error` when
    `status='error'`. MockSimSandbox.from_script should NOT shield
    callers from that invariant — a malformed script needs to fail
    loudly at sandbox build time, not silently emit a non-conforming
    SandboxResult later."""
    with pytest.raises(ValueError, match="error required"):
        MockSimSandbox.from_script({"status": "error"})


async def test_error_entry_with_error_field_constructs_cleanly() -> None:
    sandbox = MockSimSandbox.from_script(
        {
            "status": "error",
            "error": "timeout after 60s",
            "error_class": "Timeout",
            "duration_ms": 60000,
        },
    )
    r = await sandbox.run(code="", timeout_seconds=60, memory_mb=512)
    assert r.status == "error"
    assert r.error == "timeout after 60s"
    assert r.error_class == "Timeout"
    assert r.duration_ms == 60000


# ---------------------------------------------------------------------------
# None script → permissive default
# ---------------------------------------------------------------------------


async def test_none_script_returns_ok_default_every_call() -> None:
    sandbox = MockSimSandbox.from_script(None)
    for _ in range(3):
        r = await sandbox.run(code="x", timeout_seconds=1, memory_mb=1)
        assert r.status == "ok"
        assert r.output == ""


# ---------------------------------------------------------------------------
# Run args are accepted but ignored
# ---------------------------------------------------------------------------


async def test_run_args_are_ignored() -> None:
    """The Protocol takes `code`, `timeout_seconds`, `memory_mb` for
    signature parity with LocalDockerSandbox. MockSim doesn't execute
    code — varying the args must not change the output."""
    sandbox = MockSimSandbox.from_script(
        {"status": "ok", "output": "fixed"},
    )
    r1 = await sandbox.run(code="print(1)", timeout_seconds=1.0, memory_mb=64)
    r2 = await sandbox.run(
        code="raise RuntimeError('boom')", timeout_seconds=3600.0, memory_mb=8192,
    )
    assert r1.output == r2.output == "fixed"
    assert r1.status == r2.status == "ok"


# ---------------------------------------------------------------------------
# Construction-error guard
# ---------------------------------------------------------------------------


def test_sequence_not_a_list_raises() -> None:
    """A script with `sequence` that isn't a list is operator error;
    fail loudly at build time so it doesn't surface later as an
    AttributeError mid-run."""
    with pytest.raises(ValueError, match="must be a list"):
        MockSimSandbox.from_script({"sequence": "not-a-list"})


async def test_manual_sandbox_with_no_fallbacks_raises_on_exhaustion() -> None:
    """Defensive: a manually-constructed MockSimSandbox(sequence=[X])
    with no fixed_response and no default_response exhausts after one
    call. The next call must raise rather than silently returning
    None (which would crash a caller expecting SandboxResult)."""
    sandbox = MockSimSandbox(
        sequence=[
            SandboxResult(
                status="ok", output="once", stderr="", exit_code=0,
                duration_ms=1, error=None, error_class=None,
            ),
        ],
    )
    await sandbox.run(code="", timeout_seconds=1, memory_mb=1)
    with pytest.raises(RuntimeError, match="sequence exhausted"):
        await sandbox.run(code="", timeout_seconds=1, memory_mb=1)
