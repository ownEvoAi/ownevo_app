"""Hand-authored SimulationPlan fixtures for the 3 A3.1 workflows.

These exist so the renderer (`sim_render`), replay-equivalence tests, and
end-to-end execution tests can run **without** an LLM in the loop. They
also serve as the structural ground truth for the live snapshot tests in
`test_nl_gen_sim_generator.py` — when `OWNEVO_ANTHROPIC_LIVE=1`, the LLM's
output is asserted to have the same *shape* as these fixtures (same set of
event field names, n_steps in a reasonable range, init_state + step both
present), not byte-equal.

The plan bodies here are intentionally simple — enough physics to drive a
non-trivial trajectory + a hidden-truth label, no more. Real plans
generated during W4-W6 by the loop will be richer; these are the floor.
"""

from __future__ import annotations

from ..sim_plan import EventField, SimulationPlan

# ---------------------------------------------------------------------------
# Demand prediction — weekly demand readings with seasonality + a hidden
# `alert_correct_label` indicating whether the agent should fire a markdown.
# ---------------------------------------------------------------------------

DEMAND_PREDICTION_SIM_PLAN = SimulationPlan(
    workflow_spec_id="supply-chain-demand-forecast",
    description=(
        "Synthetic weekly demand readings across 12 SKUs in 3 regions with "
        "annual + holiday seasonality and supplier price-build noise; emits "
        "a hidden alert_correct_label that is True when the next-4-week "
        "rolling demand drops by more than 25% (the agent should fire a "
        "markdown alert)."
    ),
    n_steps_default=52,
    seed_default=42,
    imports=["math"],
    init_state_code='''\
skus = [f"SKU_{i:03d}" for i in range(12)]
regions = ["PNW", "SE", "MW"]
categories = ["winter_apparel", "footwear", "outdoor_gear"]
base_demand = {
    sku: rng.uniform(50, 200) for sku in skus
}
sku_to_region = {
    sku: rng.choice(regions) for sku in skus
}
sku_to_category = {
    sku: rng.choice(categories) for sku in skus
}
return {
    "skus": skus,
    "regions": regions,
    "categories": categories,
    "base_demand": base_demand,
    "sku_to_region": sku_to_region,
    "sku_to_category": sku_to_category,
}
''',
    step_code='''\
sku = rng.choice(state["skus"])
region = state["sku_to_region"][sku]
category = state["sku_to_category"][sku]
base = state["base_demand"][sku]

# Annual + holiday seasonality. Weeks 47-52 dip (post-holiday markdown season).
annual = 1.0 + 0.25 * math.sin(2 * math.pi * step_index / 52)
holiday_dip = 0.7 if step_index % 52 >= 47 else 1.0
seasonal = annual * holiday_dip

# Supplier price-build noise.
noise = rng.gauss(0, 0.08)

demand_units = max(0, int(base * seasonal * (1 + noise)))

# Hidden ground truth: a markdown alert is correct when this week's demand is
# below 65% of the SKU's rolling base — the agent should have flagged it.
alert_correct_label = demand_units < 0.65 * base

return {
    "step_index": step_index,
    "week_index": step_index,
    "sku_id": sku,
    "region": region,
    "category": category,
    "demand_units": demand_units,
    "alert_correct_label": alert_correct_label,
}
''',
    event_fields=[
        EventField(name="step_index", type="int"),
        EventField(name="week_index", type="int"),
        EventField(name="sku_id", type="str"),
        EventField(name="region", type="str"),
        EventField(name="category", type="str"),
        EventField(name="demand_units", type="int"),
        EventField(
            name="alert_correct_label",
            type="bool",
            description=(
                "Hidden ground truth: True if the agent should have fired a "
                "markdown alert this week."
            ),
        ),
    ],
)

# ---------------------------------------------------------------------------
# Credit risk — synthetic loan applications with hidden default labels.
# ---------------------------------------------------------------------------

CREDIT_RISK_SIM_PLAN = SimulationPlan(
    workflow_spec_id="credit-risk-line-recalibration",
    description=(
        "Synthetic loan applications with credit score, income, debt-to-income "
        "ratio, and loan amount; emits a hidden default_label drawn from a "
        "logistic-style risk function so the agent's classifier has a "
        "learnable signal."
    ),
    n_steps_default=200,
    seed_default=7,
    imports=["math"],
    init_state_code='''\
counter = {"applicant_seq": 0}
return counter
''',
    step_code='''\
state["applicant_seq"] += 1
applicant_id = f"APP_{state['applicant_seq']:05d}"

credit_score = int(rng.gauss(680, 60))
credit_score = max(300, min(850, credit_score))

income = max(15000, int(rng.gauss(72000, 25000)))
loan_amount = int(rng.uniform(5000, 80000))
dti = round(loan_amount / max(income, 1), 3)

# Logistic-style hidden risk: low credit + high DTI → higher default
# probability. The agent's job is to learn this and classify accordingly.
risk_logit = -0.012 * (credit_score - 680) + 4.0 * (dti - 0.5)
default_prob = 1.0 / (1.0 + math.exp(-risk_logit))
default_label = rng.random() < default_prob

return {
    "step_index": step_index,
    "applicant_id": applicant_id,
    "credit_score": credit_score,
    "income": income,
    "loan_amount": loan_amount,
    "dti_ratio": dti,
    "default_label": default_label,
}
''',
    event_fields=[
        EventField(name="step_index", type="int"),
        EventField(name="applicant_id", type="str"),
        EventField(name="credit_score", type="int"),
        EventField(name="income", type="int"),
        EventField(name="loan_amount", type="int"),
        EventField(name="dti_ratio", type="float"),
        EventField(
            name="default_label",
            type="bool",
            description="Hidden ground truth: did this applicant default within 24 months?",
        ),
    ],
)

# ---------------------------------------------------------------------------
# Contract review — synthetic contract clauses with hidden problematic flags.
# ---------------------------------------------------------------------------

CONTRACT_REVIEW_SIM_PLAN = SimulationPlan(
    workflow_spec_id="union-contract-review",
    description=(
        "Synthetic contract clauses across non-compete, IP-assignment, "
        "termination, and severance categories; ~30% are flagged with a "
        "hidden is_problematic label for clauses that exceed the company's "
        "approved boundaries (e.g. >18-month non-compete, >$50k severance "
        "ask, IP-assignment outside scope)."
    ),
    n_steps_default=80,
    seed_default=13,
    imports=[],
    init_state_code='''\
clause_types = [
    "non_compete",
    "ip_assignment",
    "termination",
    "severance",
    "indemnification",
]
contract_seq = {"n": 0}
return {"clause_types": clause_types, "contract_seq": contract_seq}
''',
    step_code='''\
state["contract_seq"]["n"] += 1
contract_id = f"C_{state['contract_seq']['n']:04d}"
clause_type = rng.choice(state["clause_types"])

if clause_type == "non_compete":
    duration_months = rng.randint(3, 36)
    is_problematic = duration_months > 18
    text = f"Employee shall not compete for {duration_months} months."
    severity = "high" if is_problematic else "low"
elif clause_type == "ip_assignment":
    out_of_scope = rng.random() < 0.25
    is_problematic = out_of_scope
    text = (
        "Pre-existing IP carved out."
        if not out_of_scope
        else "All IP including pre-existing assigned to company."
    )
    severity = "high" if is_problematic else "low"
elif clause_type == "termination":
    notice_days = rng.choice([14, 30, 60, 90])
    is_problematic = notice_days < 30
    text = f"Either party may terminate with {notice_days} days notice."
    severity = "medium" if is_problematic else "low"
elif clause_type == "severance":
    severance_amount = rng.randint(0, 100_000)
    is_problematic = severance_amount > 50_000
    text = f"Severance payable: ${severance_amount:,}."
    severity = "high" if is_problematic else "low"
else:  # indemnification
    unlimited_cap = rng.random() < 0.2
    is_problematic = unlimited_cap
    text = (
        "Indemnification capped at fees paid."
        if not unlimited_cap
        else "Unlimited indemnification."
    )
    severity = "high" if is_problematic else "low"

return {
    "step_index": step_index,
    "contract_id": contract_id,
    "clause_type": clause_type,
    "clause_text": text,
    "severity": severity,
    "is_problematic": is_problematic,
}
''',
    event_fields=[
        EventField(name="step_index", type="int"),
        EventField(name="contract_id", type="str"),
        EventField(name="clause_type", type="str"),
        EventField(name="clause_text", type="str"),
        EventField(name="severity", type="str"),
        EventField(
            name="is_problematic",
            type="bool",
            description="Hidden ground truth: should this clause have been flagged?",
        ),
    ],
)


SIM_PLAN_FIXTURES = {
    "demand-prediction": DEMAND_PREDICTION_SIM_PLAN,
    "credit-risk": CREDIT_RISK_SIM_PLAN,
    "contract-review": CONTRACT_REVIEW_SIM_PLAN,
}


__all__ = [
    "DEMAND_PREDICTION_SIM_PLAN",
    "CREDIT_RISK_SIM_PLAN",
    "CONTRACT_REVIEW_SIM_PLAN",
    "SIM_PLAN_FIXTURES",
]
