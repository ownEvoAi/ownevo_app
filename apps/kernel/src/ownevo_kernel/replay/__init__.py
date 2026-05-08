"""Replay loops (W5.4 — 7-day M5 demo).

Drives the substrate end-to-end (gate → audit → cluster-derived eval-case
growth → LLM-judge admit) over N simulated cycles so the demo loop has a
visibly climbing lift curve plus an audit trail and an eval set that grew
from clusters.

The orchestration is **substrate-real**: every iteration row, proposal
row, audit_entry, eval_case, and approval row is committed to the actual
database. The score signal is **synthetic**: a `SyntheticBenchmarkRunner`
returns deterministic improving rewards per cycle so the loop runs in
seconds without a sandbox / Anthropic / LightGBM. The W6 full-M5 30-day
replay swaps the runner; everything else stays.
"""

from .seven_day import (
    CycleSummary,
    ReplayConfig,
    ReplayReport,
    run_seven_day_replay,
)

__all__ = [
    "CycleSummary",
    "ReplayConfig",
    "ReplayReport",
    "run_seven_day_replay",
]
