"""Three-step regression gate (W2.2).

The gate is the trust mechanism: an agent-proposed skill change is
admitted iff it (1) keeps every previously-passing case passing, (2)
improves val_score over best-ever, and (3) doesn't crash. The gate
self-test (W2.2a) validates this contract against synthetic skills so
that "approved improvement" actually means improvement.

`run_gate` is a pure async function over the `BenchmarkRunner`
Protocol — no DB writes, no audit log. The DB-writing wrapper that
creates iterations + proposals + audit entries lives in PR #8 (M5
Day-1 baseline) once the iteration table is being driven end-to-end.
"""

from .result import GateDecision, GateResult
from .runner import run_gate

__all__ = [
    "GateDecision",
    "GateResult",
    "run_gate",
]
