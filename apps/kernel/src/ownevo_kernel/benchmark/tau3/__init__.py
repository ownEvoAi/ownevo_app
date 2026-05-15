"""τ³-bench (Sierra tau2-bench) integration — P1.5 of the τ³ test plan.

`SandboxedTauBenchRunner` runs the tau-bench retail/airline/telecom
domains through `ownevo-sandbox-tau3:0.1.0` and reports per-task
rewards as a `BenchmarkResult` so the existing gate flow
(`run_gate` / `persist_gate_run`) consumes it without modification.

Companion docs:
- `docs/BENCHMARK_ARCHITECTURE.md` — multi-benchmark substrate design
- `docs/HARNESS.md` — proposer / agent / gate principles
"""

from .failure_analyzer import (
    FAILURE_REWARD_THRESHOLD,
    Tau3FailureAnalyzerError,
    Tau3FailureSnapshot,
    analyze_tau3_failures,
)
from .runner import (
    SandboxedTauBenchRunner,
    Tau3SandboxError,
)

__all__ = [
    "FAILURE_REWARD_THRESHOLD",
    "SandboxedTauBenchRunner",
    "Tau3FailureAnalyzerError",
    "Tau3FailureSnapshot",
    "Tau3SandboxError",
    "analyze_tau3_failures",
]
