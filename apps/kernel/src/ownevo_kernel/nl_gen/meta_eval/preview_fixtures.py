"""Pre-computed `MetaEvalJudgment` fixtures for the W5.5 UI preview route.

`/api/nl-gen/preview` ships the four NL-gen artifacts (spec, sim plan,
eval case set, metric definition) plus a `MetaEvalJudgment` so the
web UI can render the W5.5 coverage badge without paying for a live
Anthropic call on every page render.

The judgments here are hand-authored against the production NL-gen
fixtures (`nl_gen/fixtures/*`) — those bundles are known-good (every
schema validator passes, every `known_past_misses` phrase has eval-case
coverage, the metric direction matches the past-miss framing). A
genuine judge run on these bundles produces all-pass / `good` overall
in calibration testing; we hard-code that verdict here so the preview
is deterministic and offline-safe.

When the live `POST /api/nl-gen/generate` flow lands (W6), these
fixtures stop being load-bearing — they're a deterministic stand-in
for the badge UI to render against, not the long-term source.

Why hand-author rather than cache a real judge call:

  * **Offline / air-gapped dev**: anyone running `make web-dev`
    without `ANTHROPIC_API_KEY` should see the same demo data.
  * **CI**: the preview endpoint is exercised by tests that must not
    consume API tokens (project policy: CI does not consume API keys).
  * **Audit-trail honesty**: a hard-coded judgment is labeled as such
    via `provenance="preview-fixture"` on the API response so the UI
    can show a "demo data" banner if it ever ships beyond preview.
"""

from __future__ import annotations

from ..fixtures import FIXTURES as _NL_GEN_FIXTURES
from .judgment import MetaEvalDimension, MetaEvalJudgment

# ---------------------------------------------------------------------------
# Demand prediction
# ---------------------------------------------------------------------------

_DEMAND_PREDICTION_JUDGMENT = MetaEvalJudgment(
    workflow_spec_id="demand-prediction",
    sim_coverage=MetaEvalDimension(
        verdict="pass",
        rationale=(
            "Sim instantiates every load-bearing entity from the description: "
            "the 8,400-SKU catalog, the 142 stores, supplier price-build, "
            "regional variance, and the SAP + NOAA data sources. Both named "
            "personas (analyst Monday review, VP daily triage) appear."
        ),
    ),
    eval_case_coverage=MetaEvalDimension(
        verdict="pass",
        rationale=(
            "All three documented past-misses ('missed PNW winter boot spike', "
            "'underweight promotional uplift', 'over-reacted to Southeast "
            "hurricane evacuation') have at least one matching eval case; "
            "balanced True/False classes (≥3 each) and four explicit "
            "regression checks for the false-alert direction."
        ),
    ),
    metric_alignment=MetaEvalDimension(
        verdict="pass",
        rationale=(
            "Composite of precision + recall + discount-bp accuracy is "
            "aligned with the past-miss asymmetry (recall captures missed "
            "spikes; precision captures the 'don't fire false alerts' "
            "regression). Threshold 0.75 is achievable on the seasonal "
            "baseline + non-trivial."
        ),
    ),
    overall_verdict="good",
    overall_rationale=(
        "Three-pass bundle ready for the agent loop. Sim covers the "
        "described entities, eval set exercises every named past-miss + "
        "regression direction, metric direction matches the description's "
        "framing (more recall on missed markdowns than overall accuracy)."
    ),
)

# ---------------------------------------------------------------------------
# Credit risk
# ---------------------------------------------------------------------------

_CREDIT_RISK_JUDGMENT = MetaEvalJudgment(
    workflow_spec_id="credit-risk",
    sim_coverage=MetaEvalDimension(
        verdict="pass",
        rationale=(
            "Sim plan instantiates the applicant entity, the credit-bureau "
            "data source, and the underwriter persona. All three named "
            "decision tools (decline, refer, approve) are present."
        ),
    ),
    eval_case_coverage=MetaEvalDimension(
        verdict="partial",
        rationale=(
            "Two of three documented past-misses have explicit cases "
            "('thin-file young applicant', 'self-employed income volatility'); "
            "the 'small-business co-borrower' miss is implied by case #7 "
            "but not directly named. Class balance is healthy."
        ),
    ),
    metric_alignment=MetaEvalDimension(
        verdict="pass",
        rationale=(
            "Recall-weighted F-beta with β=2.0 matches the past-miss "
            "asymmetry (false-negatives are the costly direction in this "
            "domain). Threshold 0.70 is non-trivial on the bureau-baseline."
        ),
    ),
    overall_verdict="good",
    overall_rationale=(
        "Bundle is safe to feed the agent loop. Sim + metric alignment "
        "are clean; the partial on eval coverage is a benign omission "
        "(the small-business co-borrower miss is structurally similar to "
        "the self-employed case already exercised). Worth surfacing as a "
        "minor gap in the badge so the reviewer can add a case if "
        "they want fuller coverage."
    ),
)

# ---------------------------------------------------------------------------
# Contract review
# ---------------------------------------------------------------------------

_CONTRACT_REVIEW_JUDGMENT = MetaEvalJudgment(
    workflow_spec_id="contract-review",
    sim_coverage=MetaEvalDimension(
        verdict="pass",
        rationale=(
            "Sim covers all five clause types from the description "
            "(jurisdictional carve-outs, grievance precedent, timeline, "
            "wage progression, healthcare contribution). Both legal and "
            "labour-relations personas instantiated."
        ),
    ),
    eval_case_coverage=MetaEvalDimension(
        verdict="pass",
        rationale=(
            "Every clause type has at least one positive + one regression "
            "case; the named past-misses (jurisdictional carve-out missed, "
            "grievance precedent overlooked, unrealistic timeline proposed) "
            "are each covered by a dedicated case."
        ),
    ),
    metric_alignment=MetaEvalDimension(
        verdict="pass",
        rationale=(
            "Clause-coverage F1 matches the spec's framing of 'every "
            "clause must be acknowledged or escalated'. Threshold 0.95 is "
            "high but matches the legal-domain expectation that misses "
            "are catastrophic."
        ),
    ),
    overall_verdict="good",
    overall_rationale=(
        "All-pass bundle. Sim, eval coverage, and metric all aligned "
        "with the description's emphasis on coverage-not-accuracy as "
        "the load-bearing dimension."
    ),
)

# ---------------------------------------------------------------------------
# Public dict — keyed identically to FIXTURES / SIM_PLAN_FIXTURES /
# EVAL_CASE_SET_FIXTURES / METRIC_FIXTURES so the API endpoint can
# do a single-key lookup across all five.
# ---------------------------------------------------------------------------

PREVIEW_JUDGMENT_FIXTURES: dict[str, MetaEvalJudgment] = {
    "demand-prediction": _DEMAND_PREDICTION_JUDGMENT,
    "credit-risk": _CREDIT_RISK_JUDGMENT,
    "contract-review": _CONTRACT_REVIEW_JUDGMENT,
}

# Module-import-time invariant: every workflow_id in the production
# fixtures has a matching judgment here. If a 4th workflow lands in
# `nl_gen/fixtures/__init__.py` and this dict isn't updated, the
# preview API would 500 on a request for that id — fail loudly at
# import time instead.

if set(PREVIEW_JUDGMENT_FIXTURES) != set(_NL_GEN_FIXTURES):
    raise AssertionError(
        f"PREVIEW_JUDGMENT_FIXTURES keys {set(PREVIEW_JUDGMENT_FIXTURES)} "
        f"diverged from nl_gen FIXTURES keys {set(_NL_GEN_FIXTURES)}; "
        f"add a judgment here whenever a new fixture lands in nl_gen/fixtures/."
    )

for _wid, _judgment in PREVIEW_JUDGMENT_FIXTURES.items():
    if _judgment.workflow_spec_id != _wid:
        raise AssertionError(
            f"Preview judgment for {_wid!r} has workflow_spec_id="
            f"{_judgment.workflow_spec_id!r}; should match the dict key."
        )

__all__ = ["PREVIEW_JUDGMENT_FIXTURES"]
