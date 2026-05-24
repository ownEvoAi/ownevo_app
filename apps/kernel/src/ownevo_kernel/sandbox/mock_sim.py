"""MockSimSandbox — deterministic scripted `SandboxRuntime`.

Track 9.0.2 Slice B. The benchmark-path counterpart to
`eval_runner/mock_solver.py`. When a benchmark workflow (M5 / τ³) sets
`workflows.sim_tier='mock'`, the code-execution sandbox swaps from
`LocalDockerSandbox.run(code, ...)` (real Docker) to
`MockSimSandbox.run(code, ...)` (canned `SandboxResult`).

Why this exists separately from MockAgentSolver: the M5 and τ³ baseline
runners (`benchmark/m5_sandbox.py`, `benchmark/tau3/runner.py`) and the
agent's `run_pipeline` tool all consume code through the `SandboxRuntime`
Protocol — not through `agent_solver`. Mocking ONE of the two layers
isn't enough to make benchmark workflows iterate cheaply; the sandbox
side is the load-bearing surface there.

Scripting model (`MockSimConfig.sandbox_script`):

  Two shapes supported, picked by the script's structure:

  A) **Single fixed response.** Every `.run(...)` call returns this
     same SandboxResult. Use when you want the sandbox to be a
     "perfect agent code" stub — always status='ok' with a known
     output. Schema:

         {"status": "ok", "output": "0.65", "duration_ms": 50}

  B) **Indexed sequence.** A list of canned results consumed in order.
     The Nth `.run(...)` call returns `sequence[N]`. Past the end,
     `default_response` applies (defaults to the last entry). Use when
     you want to script a multi-step benchmark run with varying
     outputs per step. Schema:

         {
           "sequence": [
             {"status": "ok", "output": "0.50", "duration_ms": 40},
             {"status": "ok", "output": "0.65", "duration_ms": 40},
             {"status": "error", "error_class": "Timeout",
              "error": "wall-clock exceeded", "duration_ms": 60000}
           ],
           "default_response": {"status": "ok", "output": "0.80", "duration_ms": 40}
         }

  Either shape: omitted fields default to the same harmless values
  (`output=""`, `stderr=""`, `exit_code=0`, `duration_ms=10`,
  `error=None`, `error_class=None`) — the only required field for an
  'ok' entry is the `status`; for 'error' entries `error` is also
  required (the `SandboxResult` dataclass enforces this in
  `__post_init__`).

Determinism: the `sequence` index is per-instance, so a fresh
MockSimSandbox starts at sequence[0]. Reusing one instance across
calls is deterministic; throwing it away after each `run` gives the
fixed `sequence[0]` every time.

This is NOT a recording / replay system — that's Track 9.0.3
(`ReplaySim`). MockSim is for scripting cheap deterministic runs,
not for replaying captured production traces.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .types import SandboxResult

_DEFAULT_OK: dict[str, Any] = {
    "status": "ok",
    "output": "",
    "stderr": "",
    "exit_code": 0,
    "duration_ms": 10,
    "error": None,
    "error_class": None,
}


def _coerce_response(entry: dict[str, Any]) -> SandboxResult:
    """Build a `SandboxResult` from a partial dict, filling defaults.

    The script JSON is allowed to omit fields the user doesn't care
    about — e.g. an 'ok' entry only needs `status` + maybe `output` +
    `duration_ms`. Defaults match what a successful, fast run would
    look like so a sparse script doesn't accidentally model a crash.

    Raises:
        ValueError: missing required fields after merging defaults
            (caught by `SandboxResult.__post_init__`'s own invariants:
            `error` must be set when `status='error'`, etc).
    """
    merged = {**_DEFAULT_OK, **entry}
    return SandboxResult(
        status=merged["status"],
        output=merged["output"],
        stderr=merged["stderr"],
        exit_code=merged["exit_code"],
        duration_ms=merged["duration_ms"],
        error=merged["error"],
        error_class=merged["error_class"],
    )


@dataclass
class MockSimSandbox:
    """SandboxRuntime that returns canned results from a script.

    Implements the `SandboxRuntime` Protocol via `run(...)`. Pass the
    `MockSimConfig.sandbox_script` payload directly:

        sandbox = MockSimSandbox.from_script(mock_config.sandbox_script)
        result = await sandbox.run(code="ignored", timeout_seconds=30, memory_mb=512)

    Stateful across calls (the sequence cursor advances). Construct a
    fresh instance per "run" if you want byte-identical behaviour
    across runs that each issue several `.run()` calls.
    """

    fixed_response: SandboxResult | None = None
    sequence: list[SandboxResult] = field(default_factory=list)
    default_response: SandboxResult | None = None
    _cursor: int = 0

    @classmethod
    def from_script(cls, script: dict[str, Any] | None) -> MockSimSandbox:
        """Parse a `MockSimConfig.sandbox_script` payload into a sandbox.

        Accepts None (returns a sandbox that always emits the implicit
        `_DEFAULT_OK` reply — useful for tests that only need
        SandboxRuntime Protocol conformance without a real script).

        Detects shape from the keys:
          * `sequence` present → sequence mode (with optional
            `default_response`).
          * Otherwise → treats the dict itself as a single fixed
            response.
        """
        if script is None:
            return cls(fixed_response=_coerce_response({}))
        if "sequence" in script:
            entries = script.get("sequence") or []
            if not isinstance(entries, list):
                raise ValueError(
                    "MockSim script: `sequence` must be a list of "
                    f"response dicts, got {type(entries).__name__}",
                )
            seq = [_coerce_response(e) for e in entries]
            default_raw = script.get("default_response")
            default = (
                _coerce_response(default_raw)
                if isinstance(default_raw, dict)
                else (seq[-1] if seq else _coerce_response({}))
            )
            return cls(sequence=seq, default_response=default)
        # Single-fixed-response shape.
        return cls(fixed_response=_coerce_response(script))

    async def run(
        self,
        code: str,
        *,
        timeout_seconds: float,
        memory_mb: int,
    ) -> SandboxResult:
        """SandboxRuntime Protocol entry point.

        `code`, `timeout_seconds`, and `memory_mb` are accepted to
        match the Protocol but ignored — the response is fully
        determined by the script. The signature parity is what lets
        MockSimSandbox drop in wherever LocalDockerSandbox does.
        """
        del code, timeout_seconds, memory_mb  # accepted for Protocol parity, unused

        if self.fixed_response is not None:
            return self.fixed_response

        if self._cursor < len(self.sequence):
            result = self.sequence[self._cursor]
            self._cursor += 1
            return result

        if self.default_response is not None:
            return self.default_response

        # Should be unreachable: from_script always sets either
        # fixed_response or default_response. Surfaces a clear error
        # if someone constructs a MockSimSandbox manually with neither.
        raise RuntimeError(
            "MockSimSandbox: sequence exhausted and no default_response "
            "configured. Either provide `default_response` in the "
            "script or use the `sequence` shape with enough entries "
            "for the test's call count.",
        )


__all__ = [
    "MockSimSandbox",
]
