"""Hand-authored EvalCaseSet fixtures for the 3 A4.1 workflows.

These are the structural ground-truth the schema, generator, and replay
tests check against — same role as `sim_plans.py` for A3.2:

  * Schema-only round-trip tests prove the fixtures parse and re-serialize.
  * Replay-equivalence tests run each fixture's cases against the matched
    `SIM_PLAN_FIXTURES` plan and assert every case `passes` (the
    `expected_value` matches what the rendered sim actually produces at
    `(sim_seed, target_step_index, target_label_field)`).
  * Generator tests use these as the scripted tool-output payload.

Determining `expected_value` for each case: `scripts/regen_eval_fixtures.py`
runs the rendered sim at the chosen seeds and prints the actual labels at
each step index (kept under `apps/kernel/scripts/` alongside the sim-plan
schema regen scripts). The values below were observed against the
A3.2-frozen sim plans on 2026-05-05 — re-running that probe should
reproduce them bit-identically because the rendered sim is deterministic.

Each set covers:
  * Both `expected_value` classes (≥3 True and ≥3 False, per the
    `EvalCaseSet._balanced_classes` validator),
  * Every `known_past_misses` phrase (`provenance.kind="derived"`,
    `source` set to the verbatim phrase),
  * 2-3 alternate seeds (so a deterministic seed flake doesn't dominate),
  * `is_test_fold=True` on ~20% of cases (held-out evaluation).
"""

from __future__ import annotations

from ..eval_case_set import EvalCaseSet, GeneratedEvalCase
from ..spec import Provenance

# ---------------------------------------------------------------------------
# Demand prediction
# ---------------------------------------------------------------------------
# Hidden label: `alert_correct_label` — True when the agent should fire a
# markdown alert (demand < 65% of base). Sparse True (mostly weeks 47-51,
# the post-holiday dip in the sim).

DEMAND_PREDICTION_EVAL_CASE_SET = EvalCaseSet(
    workflow_spec_id="supply-chain-demand-forecast",
    simulation_plan_workflow_id="supply-chain-demand-forecast",
    cases=[
        # Past miss 1 — verbatim phrase
        GeneratedEvalCase(
            case_id="winter-boot-spike-week-47",
            provenance=Provenance(
                kind="derived",
                source="missed the 2025 Pacific NW winter boot spike by 4 weeks",
            ),
            sim_seed=42,
            n_steps=52,
            target_step_index=47,
            target_label_field="alert_correct_label",
            expected_value=True,
            rationale=(
                "Week 47 is when the post-holiday dip begins; the agent must "
                "fire a markdown alert here, not 4 weeks late."
            ),
        ),
        GeneratedEvalCase(
            case_id="post-holiday-dip-week-48",
            provenance=Provenance(
                kind="inferred",
                source="supply-chain holiday markdown pattern",
            ),
            sim_seed=42,
            n_steps=52,
            target_step_index=48,
            target_label_field="alert_correct_label",
            expected_value=True,
            rationale="Mid-dip week; agent should still be in markdown mode.",
        ),
        GeneratedEvalCase(
            case_id="dip-tail-week-51",
            provenance=Provenance(
                kind="inferred",
                source="supply-chain holiday markdown pattern",
            ),
            sim_seed=42,
            n_steps=52,
            target_step_index=51,
            target_label_field="alert_correct_label",
            expected_value=True,
            rationale="End of dip; alert must persist through full window.",
        ),
        GeneratedEvalCase(
            case_id="pre-dip-week-46-no-alert",
            provenance=Provenance(
                kind="inferred",
                source="supply-chain seasonal baseline pattern",
            ),
            sim_seed=42,
            n_steps=52,
            target_step_index=46,
            target_label_field="alert_correct_label",
            expected_value=False,
            rationale="One week before dip starts; no markdown warranted.",
        ),
        GeneratedEvalCase(
            case_id="early-spring-week-10-no-alert",
            provenance=Provenance(
                kind="inferred",
                source="supply-chain seasonal baseline pattern",
            ),
            sim_seed=42,
            n_steps=52,
            target_step_index=10,
            target_label_field="alert_correct_label",
            expected_value=False,
            rationale="Early-spring baseline week; demand near base, no alert.",
        ),
        GeneratedEvalCase(
            case_id="summer-week-20-no-alert",
            provenance=Provenance(
                kind="inferred",
                source="supply-chain seasonal baseline pattern",
            ),
            sim_seed=42,
            n_steps=52,
            target_step_index=20,
            target_label_field="alert_correct_label",
            expected_value=False,
            rationale="Mid-year baseline; agent must not over-fire alerts.",
        ),
        # Past miss 2 — verbatim phrase
        GeneratedEvalCase(
            case_id="bundled-sku-uplift-no-spurious-alert",
            provenance=Provenance(
                kind="derived",
                source="underweight promotional uplift on bundled SKUs",
            ),
            sim_seed=13,
            n_steps=52,
            target_step_index=25,
            target_label_field="alert_correct_label",
            expected_value=False,
            rationale=(
                "Mid-year baseline; the past-miss is about underweighting "
                "promo uplift, but the agent still must not fire a markdown "
                "alert when demand is at baseline."
            ),
            is_test_fold=True,
        ),
        GeneratedEvalCase(
            case_id="alt-seed-7-week-49-alert",
            provenance=Provenance(
                kind="inferred",
                source="supply-chain holiday markdown pattern",
            ),
            sim_seed=7,
            n_steps=52,
            target_step_index=49,
            target_label_field="alert_correct_label",
            expected_value=True,
            rationale="Holiday-dip alert under alternate seed.",
        ),
        GeneratedEvalCase(
            case_id="alt-seed-7-week-5-no-alert",
            provenance=Provenance(
                kind="inferred",
                source="supply-chain seasonal baseline pattern",
            ),
            sim_seed=7,
            n_steps=52,
            target_step_index=5,
            target_label_field="alert_correct_label",
            expected_value=False,
            rationale="Early-year baseline under alternate seed; no alert.",
        ),
        GeneratedEvalCase(
            case_id="alt-seed-13-week-50-alert",
            provenance=Provenance(
                kind="inferred",
                source="supply-chain holiday markdown pattern",
            ),
            sim_seed=13,
            n_steps=52,
            target_step_index=50,
            target_label_field="alert_correct_label",
            expected_value=True,
            rationale="Late-dip alert; another seed validates determinism.",
            is_test_fold=True,
        ),
        GeneratedEvalCase(
            case_id="alt-seed-13-week-15-no-alert",
            provenance=Provenance(
                kind="inferred",
                source="supply-chain seasonal baseline pattern",
            ),
            sim_seed=13,
            n_steps=52,
            target_step_index=15,
            target_label_field="alert_correct_label",
            expected_value=False,
            rationale="Spring baseline under alternate seed; no alert.",
        ),
        GeneratedEvalCase(
            case_id="alt-seed-7-week-30-no-alert",
            provenance=Provenance(
                kind="inferred",
                source="supply-chain seasonal baseline pattern",
            ),
            sim_seed=7,
            n_steps=52,
            target_step_index=30,
            target_label_field="alert_correct_label",
            expected_value=False,
            rationale="Late-summer baseline; no alert across both seeds.",
        ),
    ],
)


# ---------------------------------------------------------------------------
# Credit risk
# ---------------------------------------------------------------------------
# Hidden label: `default_label` — drawn from a logistic of credit-score and
# DTI. Roughly 60/40 True/False at the default seed; alternate seeds
# similar.

CREDIT_RISK_EVAL_CASE_SET = EvalCaseSet(
    workflow_spec_id="credit-risk-line-recalibration",
    simulation_plan_workflow_id="credit-risk-line-recalibration",
    cases=[
        # Past miss 1 — verbatim phrase
        GeneratedEvalCase(
            case_id="hospitality-concentration-default",
            provenance=Provenance(
                kind="derived",
                source="underweighted hospitality-sector concentration in Q3 2024",
            ),
            sim_seed=7,
            n_steps=200,
            target_step_index=0,
            target_label_field="default_label",
            expected_value=True,
            rationale=(
                "First applicant under default seed — defaults TRUE; the "
                "past-miss frames the agent must not underweight high-risk "
                "concentration."
            ),
        ),
        GeneratedEvalCase(
            case_id="non-default-clean-applicant",
            provenance=Provenance(
                kind="inferred",
                source="credit-risk DTI threshold pattern",
            ),
            sim_seed=7,
            n_steps=200,
            target_step_index=1,
            target_label_field="default_label",
            expected_value=False,
            rationale="Low-risk applicant under default seed; no default.",
        ),
        GeneratedEvalCase(
            case_id="default-mid-trajectory",
            provenance=Provenance(
                kind="inferred",
                source="credit-risk logistic-default pattern",
            ),
            sim_seed=7,
            n_steps=200,
            target_step_index=2,
            target_label_field="default_label",
            expected_value=True,
            rationale="Mid-suite default sample; logistic produces True here.",
        ),
        GeneratedEvalCase(
            case_id="non-default-low-dti",
            provenance=Provenance(
                kind="inferred",
                source="credit-risk DTI threshold pattern",
            ),
            sim_seed=7,
            n_steps=200,
            target_step_index=11,
            target_label_field="default_label",
            expected_value=False,
            rationale="Low-DTI applicant; classifier should clear.",
        ),
        GeneratedEvalCase(
            case_id="default-medium-trajectory",
            provenance=Provenance(
                kind="inferred",
                source="credit-risk logistic-default pattern",
            ),
            sim_seed=7,
            n_steps=200,
            target_step_index=8,
            target_label_field="default_label",
            expected_value=True,
            rationale="Another default-True sample to broaden suite signal.",
            is_test_fold=True,
        ),
        # Past miss 2 — verbatim phrase
        GeneratedEvalCase(
            case_id="rate-shock-line-too-high",
            provenance=Provenance(
                kind="derived",
                source="held lines too high through the spring rate-shock",
            ),
            sim_seed=42,
            n_steps=200,
            target_step_index=0,
            target_label_field="default_label",
            expected_value=True,
            rationale=(
                "Alternate seed first applicant — defaults TRUE; the agent "
                "must not hold lines high through rate-shock periods."
            ),
        ),
        GeneratedEvalCase(
            case_id="alt-seed-low-risk-applicant",
            provenance=Provenance(
                kind="inferred",
                source="credit-risk DTI threshold pattern",
            ),
            sim_seed=42,
            n_steps=200,
            target_step_index=2,
            target_label_field="default_label",
            expected_value=False,
            rationale="Low-risk applicant under alternate seed; no default.",
        ),
        # Past miss 3 — verbatim phrase
        GeneratedEvalCase(
            case_id="stale-dpd-band-non-default",
            provenance=Provenance(
                kind="derived",
                source=(
                    "missed three early-stage delinquencies where DPD bands "
                    "were stuck on stale data"
                ),
            ),
            sim_seed=42,
            n_steps=200,
            target_step_index=11,
            target_label_field="default_label",
            expected_value=False,
            rationale=(
                "Past-miss is about stale DPD bands; this clean applicant "
                "must NOT be flagged as default just because the agent's "
                "DPD logic was stale."
            ),
        ),
        GeneratedEvalCase(
            case_id="alt-seed-default-applicant-step4",
            provenance=Provenance(
                kind="inferred",
                source="credit-risk logistic-default pattern",
            ),
            sim_seed=42,
            n_steps=200,
            target_step_index=4,
            target_label_field="default_label",
            expected_value=True,
            rationale="Default sample under alternate seed.",
        ),
        GeneratedEvalCase(
            case_id="seed-99-default-applicant",
            provenance=Provenance(
                kind="inferred",
                source="credit-risk logistic-default pattern",
            ),
            sim_seed=99,
            n_steps=200,
            target_step_index=0,
            target_label_field="default_label",
            expected_value=True,
            rationale="Third seed sanity check — default TRUE.",
        ),
        GeneratedEvalCase(
            case_id="seed-99-non-default-applicant",
            provenance=Provenance(
                kind="inferred",
                source="credit-risk DTI threshold pattern",
            ),
            sim_seed=99,
            n_steps=200,
            target_step_index=1,
            target_label_field="default_label",
            expected_value=False,
            rationale="Third seed clean applicant — no default.",
            is_test_fold=True,
        ),
        GeneratedEvalCase(
            case_id="seed-99-non-default-step4",
            provenance=Provenance(
                kind="inferred",
                source="credit-risk DTI threshold pattern",
            ),
            sim_seed=99,
            n_steps=200,
            target_step_index=4,
            target_label_field="default_label",
            expected_value=False,
            rationale="Third seed second clean sample.",
        ),
    ],
)


# ---------------------------------------------------------------------------
# Contract review
# ---------------------------------------------------------------------------
# Hidden label: `is_problematic` — True for clauses exceeding company
# boundaries (>18mo non-compete, out-of-scope IP, <30-day notice, severance
# >50k, unlimited indemnity caps). Roughly 30% True.

CONTRACT_REVIEW_EVAL_CASE_SET = EvalCaseSet(
    workflow_spec_id="union-contract-review",
    simulation_plan_workflow_id="union-contract-review",
    cases=[
        # Past miss 1 — verbatim phrase
        GeneratedEvalCase(
            case_id="overtime-carveout-flagged",
            provenance=Provenance(
                kind="derived",
                source=(
                    "missed a state-specific overtime carve-out in the 2024 "
                    "Western region renewal"
                ),
            ),
            sim_seed=13,
            n_steps=80,
            target_step_index=3,
            target_label_field="is_problematic",
            expected_value=True,
            rationale=(
                "First problematic clause under default seed — covers the "
                "overtime carve-out past-miss class."
            ),
        ),
        GeneratedEvalCase(
            case_id="clean-clause-step-0",
            provenance=Provenance(
                kind="inferred",
                source="contract-review baseline-clause pattern",
            ),
            sim_seed=13,
            n_steps=80,
            target_step_index=0,
            target_label_field="is_problematic",
            expected_value=False,
            rationale="First clause is within bounds; agent must not flag.",
        ),
        # Past miss 2 — verbatim phrase
        GeneratedEvalCase(
            case_id="grievance-precedent-flagged",
            provenance=Provenance(
                kind="derived",
                source=(
                    "missed a grievance precedent from 18 months earlier on "
                    "shift-bidding language"
                ),
            ),
            sim_seed=13,
            n_steps=80,
            target_step_index=5,
            target_label_field="is_problematic",
            expected_value=True,
            rationale=(
                "Problematic clause that ties back to grievance-precedent "
                "review failure mode."
            ),
        ),
        GeneratedEvalCase(
            case_id="clean-clause-step-1",
            provenance=Provenance(
                kind="inferred",
                source="contract-review baseline-clause pattern",
            ),
            sim_seed=13,
            n_steps=80,
            target_step_index=1,
            target_label_field="is_problematic",
            expected_value=False,
            rationale="Within-bounds clause; agent must not over-flag.",
        ),
        GeneratedEvalCase(
            case_id="clean-clause-step-2",
            provenance=Provenance(
                kind="inferred",
                source="contract-review baseline-clause pattern",
            ),
            sim_seed=13,
            n_steps=80,
            target_step_index=2,
            target_label_field="is_problematic",
            expected_value=False,
            rationale="Within-bounds clause under default seed.",
            is_test_fold=True,
        ),
        # Past miss 3 — verbatim phrase
        GeneratedEvalCase(
            case_id="notification-window-30-day-breach",
            provenance=Provenance(
                kind="derived",
                source=(
                    "proposed a 30-day notification window that breached the "
                    "existing 60-day requirement"
                ),
            ),
            sim_seed=42,
            n_steps=80,
            target_step_index=2,
            target_label_field="is_problematic",
            expected_value=True,
            rationale=(
                "Past-miss directly: notification window below the 60-day "
                "requirement is the canonical termination-clause violation "
                "the agent must catch."
            ),
        ),
        GeneratedEvalCase(
            case_id="alt-seed-clean-clause",
            provenance=Provenance(
                kind="inferred",
                source="contract-review baseline-clause pattern",
            ),
            sim_seed=42,
            n_steps=80,
            target_step_index=0,
            target_label_field="is_problematic",
            expected_value=False,
            rationale="Within-bounds clause under alternate seed.",
        ),
        GeneratedEvalCase(
            case_id="alt-seed-clean-clause-step1",
            provenance=Provenance(
                kind="inferred",
                source="contract-review baseline-clause pattern",
            ),
            sim_seed=42,
            n_steps=80,
            target_step_index=1,
            target_label_field="is_problematic",
            expected_value=False,
            rationale="Second clean clause under alternate seed.",
        ),
        GeneratedEvalCase(
            case_id="alt-seed-problematic-clause-step6",
            provenance=Provenance(
                kind="inferred",
                source="contract-review excessive-bound pattern",
            ),
            sim_seed=42,
            n_steps=80,
            target_step_index=6,
            target_label_field="is_problematic",
            expected_value=True,
            rationale="Excessive-bound clause under alternate seed.",
            is_test_fold=True,
        ),
        GeneratedEvalCase(
            case_id="seed-7-problematic-step1",
            provenance=Provenance(
                kind="inferred",
                source="contract-review excessive-bound pattern",
            ),
            sim_seed=7,
            n_steps=80,
            target_step_index=1,
            target_label_field="is_problematic",
            expected_value=True,
            rationale="Third seed problematic clause sanity check.",
        ),
        GeneratedEvalCase(
            case_id="seed-7-clean-clause-step0",
            provenance=Provenance(
                kind="inferred",
                source="contract-review baseline-clause pattern",
            ),
            sim_seed=7,
            n_steps=80,
            target_step_index=0,
            target_label_field="is_problematic",
            expected_value=False,
            rationale="Third seed clean clause sanity check.",
        ),
        GeneratedEvalCase(
            case_id="seed-7-clean-clause-step2",
            provenance=Provenance(
                kind="inferred",
                source="contract-review baseline-clause pattern",
            ),
            sim_seed=7,
            n_steps=80,
            target_step_index=2,
            target_label_field="is_problematic",
            expected_value=False,
            rationale="Third seed second clean clause.",
        ),
    ],
)


EVAL_CASE_SET_FIXTURES = {
    "demand-prediction": DEMAND_PREDICTION_EVAL_CASE_SET,
    "credit-risk": CREDIT_RISK_EVAL_CASE_SET,
    "contract-review": CONTRACT_REVIEW_EVAL_CASE_SET,
}


__all__ = [
    "DEMAND_PREDICTION_EVAL_CASE_SET",
    "CREDIT_RISK_EVAL_CASE_SET",
    "CONTRACT_REVIEW_EVAL_CASE_SET",
    "EVAL_CASE_SET_FIXTURES",
]
