"""Three-step regression gate (W2.2).

The gate is the trust mechanism: an agent-proposed skill change is
admitted iff it (1) keeps every previously-passing case passing, (2)
improves val_score over best-ever, and (3) doesn't crash. The gate
self-test (W2.2a) validates this contract against synthetic skills so
that "approved improvement" actually means improvement.

`run_gate` is a pure async function over the `BenchmarkRunner`
Protocol — no DB writes, no audit log. `persist_gate_run` is the
DB-writing wrapper: same call but threads the decision into
`iterations` + `proposals` + `audit_entries` atomically (W2.2 follow-up,
unblocks W4 unattended replay).
"""

from .persistence import PersistedGateRun, persist_gate_run
from .result import GateDecision, GateResult
from .runner import run_gate

__all__ = [
    "GateDecision",
    "GateResult",
    "PersistedGateRun",
    "persist_gate_run",
    "run_gate",
]
