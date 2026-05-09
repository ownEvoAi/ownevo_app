"""τ³-bench (Sierra tau2-bench) integration — P1.5 of the τ³ test plan.

`SandboxedTauBenchRunner` runs the tau-bench retail/airline/telecom
domains through `ownevo-sandbox-tau3:0.1.0` and reports per-task
rewards as a `BenchmarkResult` so the existing gate flow
(`run_gate` / `persist_gate_run`) consumes it without modification.

Companion docs:
- `docs/TAU3_LOCAL_TESTPLAN.md` — phase plan and migration steps
- `docs/BENCHMARK_ARCHITECTURE.md` — multi-benchmark substrate design
- `docs/HARNESS.md` — proposer / agent / gate principles
"""

from .runner import (
    SandboxedTauBenchRunner,
    Tau3SandboxError,
)

__all__ = [
    "SandboxedTauBenchRunner",
    "Tau3SandboxError",
]
