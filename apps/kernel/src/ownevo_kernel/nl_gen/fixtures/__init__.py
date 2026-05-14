"""Hand-authored WorkflowSpec fixtures for the 3 PLAN.md A3.1 workflows.

Used by:
  * Schema-only round-trip + structural-shape tests (no LLM, no API)
  * Snapshot tests for `workflow_spec_generator` (live API): the generated
    output is compared against these structurally — we don't expect Claude
    to match field-for-field, but we expect the same shape (≥3 tools, ≥1
    persona, ui block exercises domain-appropriate primitives).

Demand-prediction `description` is verbatim from
`www/preview/s26-rk7p3/03-new-workflow-step1.html` — the textarea content the
mock ships with. The other two are fresh prose written in the same voice.

The failure-mode taxonomy for `known_past_misses` ground-truth is
documented in the companion design mocks.
"""

from .contract_review import CONTRACT_REVIEW_DESCRIPTION, CONTRACT_REVIEW_SPEC
from .credit_risk import CREDIT_RISK_DESCRIPTION, CREDIT_RISK_SPEC
from .demand_prediction import DEMAND_PREDICTION_DESCRIPTION, DEMAND_PREDICTION_SPEC
from .eval_case_sets import (
    CONTRACT_REVIEW_EVAL_CASE_SET,
    CREDIT_RISK_EVAL_CASE_SET,
    DEMAND_PREDICTION_EVAL_CASE_SET,
    EVAL_CASE_SET_FIXTURES,
)
from .metrics import (
    CONTRACT_REVIEW_METRIC,
    CREDIT_RISK_METRIC,
    DEMAND_PREDICTION_METRIC,
    METRIC_FIXTURES,
)
from .sim_plans import (
    CONTRACT_REVIEW_SIM_PLAN,
    CREDIT_RISK_SIM_PLAN,
    DEMAND_PREDICTION_SIM_PLAN,
    SIM_PLAN_FIXTURES,
)

FIXTURES = {
    "demand-prediction": DEMAND_PREDICTION_SPEC,
    "credit-risk": CREDIT_RISK_SPEC,
    "contract-review": CONTRACT_REVIEW_SPEC,
}

DESCRIPTIONS = {
    "demand-prediction": DEMAND_PREDICTION_DESCRIPTION,
    "credit-risk": CREDIT_RISK_DESCRIPTION,
    "contract-review": CONTRACT_REVIEW_DESCRIPTION,
}

__all__ = [
    "DEMAND_PREDICTION_SPEC",
    "CREDIT_RISK_SPEC",
    "CONTRACT_REVIEW_SPEC",
    "DEMAND_PREDICTION_DESCRIPTION",
    "CREDIT_RISK_DESCRIPTION",
    "CONTRACT_REVIEW_DESCRIPTION",
    "FIXTURES",
    "DESCRIPTIONS",
    "DEMAND_PREDICTION_SIM_PLAN",
    "CREDIT_RISK_SIM_PLAN",
    "CONTRACT_REVIEW_SIM_PLAN",
    "SIM_PLAN_FIXTURES",
    "DEMAND_PREDICTION_EVAL_CASE_SET",
    "CREDIT_RISK_EVAL_CASE_SET",
    "CONTRACT_REVIEW_EVAL_CASE_SET",
    "EVAL_CASE_SET_FIXTURES",
    "DEMAND_PREDICTION_METRIC",
    "CREDIT_RISK_METRIC",
    "CONTRACT_REVIEW_METRIC",
    "METRIC_FIXTURES",
]
