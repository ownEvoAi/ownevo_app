"""Credit risk recalibration — chief risk officer discovery prompts."""

from __future__ import annotations

from ._types import DiscoveryQuestion

DISCOVERY_QUESTIONS: tuple[DiscoveryQuestion, ...] = (
    DiscoveryQuestion(
        kind="metric",
        question=(
            "Through-cycle PD vs. point-in-time PD — which framework "
            "should the recalibration target? PIT reacts faster but "
            "loads cyclical noise. TTC is smoother but lags turning "
            "points. Most teams run PIT for capital decisions and TTC "
            "for pricing decisions. Which decision is this agent feeding? "
            "If both, I will generate two distinct workflow specs."
        ),
        options=(
            "Point-in-time (PIT)",
            "Through-the-cycle (TTC)",
            "Both, run in parallel",
        ),
        rationale=(
            "PIT vs. TTC is the framework choice every model risk "
            "committee makes quarterly. Naming it at authoring time "
            "records the deliberate choice in the audit chain."
        ),
    ),
    DiscoveryQuestion(
        kind="ambiguity",
        question=(
            "You said 'flag segments where the model has drifted by more "
            "than 50bps' — drift measured against the prior month's "
            "calibration, a rolling 12-month baseline, or the original "
            "through-cycle anchor? Regulators expect the anchor; "
            "operational teams often want month-over-month for early "
            "warning. Want both with different severity thresholds, or "
            "pick one?"
        ),
        options=(
            "Month-over-month (operational)",
            "Rolling 12-month baseline",
            "Through-cycle anchor (regulatory)",
        ),
        rationale=(
            "Drift baseline determines what the eval set considers a "
            "true positive. The three baselines produce qualitatively "
            "different eval cases."
        ),
    ),
)
