"""Seven minimal good fixtures for the A4.6 meta-eval set.

Each `MinimalBundle` is the four-tuple (WorkflowSpec, SimulationPlan,
EvalCaseSet, MetricDefinition) plus the original description. A
builder constructs the bundle from a compact `_FixtureSpec` dataclass
so each domain-specific fixture stays under ~70 LOC.

Domains covered:

  1. supplier-late-shipment-risk (supply-chain)
  2. fraud-card-decline-review (credit-risk)
  3. clinical-trial-eligibility (other)
  4. insurance-claim-triage (other)
  5. hr-policy-violation-review (labour)
  6. content-moderation-escalation (support)
  7. manufacturing-defect-detection (other)

These join the 3 production fixtures (demand-prediction, credit-risk-
line-recalibration, union-contract-review) to make the 10-description
eval set the A4.6 deliverable calls for.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from ownevo_format import AlertList, MetricCards

from ...eval_case_set import EvalCaseSet, GeneratedEvalCase
from ...metric_def import MetricDefinition, MetricFamily
from ...sim_plan import EventField, SimulationPlan
from ...spec import (
    AgentTool,
    DataSource,
    Domain,
    Entity,
    EntityField,
    EnvGenerator,
    FieldType,
    Persona,
    Provenance,
    ReviewerSpec,
    SuccessCriterionStub,
    ToolParam,
    UILayout,
    UITab,
    WorkflowEnvironment,
    WorkflowSpec,
)


@dataclass(frozen=True)
class MinimalBundle:
    """Bundle = (description, spec, plan, case_set, metric).

    Public surface for the eval set assembler in `eval_set.py`. The
    description is carried explicitly so the judge can see it (the
    spec doesn't carry it; per A3.1's contract the description lives
    on the workflow row, not in the spec JSON).
    """

    description: str
    spec: WorkflowSpec
    plan: SimulationPlan
    case_set: EvalCaseSet
    metric: MetricDefinition


@dataclass(frozen=True)
class _FixtureSpec:
    """Compact authoring shape — the builder turns this into a MinimalBundle."""

    workflow_id: str
    description: str
    domain: Domain
    primary_entity: str
    primary_entity_fields: list[tuple[str, FieldType]]
    data_source_id: str
    data_source_description: str
    env_generator_name: str
    env_generator_description: str
    persona_role: str
    persona_cadence: str
    persona_description: str
    tools: list[tuple[str, str]]  # (name, description)
    past_misses: list[str]
    reviewer_role: str
    reviewer_description: str
    success_metric_name: str
    success_description: str
    success_direction: Literal["maximize", "minimize"]
    metric_family: MetricFamily
    target_value: float
    metric_description: str
    metric_rationale: str
    label_field: str
    case_rationales: list[tuple[bool, str]] = field(default_factory=list)


def _build_bundle(s: _FixtureSpec) -> MinimalBundle:
    """Assemble a structurally-valid MinimalBundle from `_FixtureSpec`.

    Sim `step_code` is plausible-looking pseudocode that emits the bool
    `label_field` — not a calibrated simulator. Eval cases target step
    indices 1..len(case_rationales) with seed=case_index*7+1; cases
    don't cover the same (seed, step_index) twice so case_ids stay
    unique.
    """
    # ----- WorkflowSpec ----------------------------------------------------
    primary_provenance = Provenance(
        kind="derived",
        source=f"workflow described as: {s.description.split('.')[0][:120]}",
    )
    spec = WorkflowSpec(
        id=s.workflow_id,
        domain=s.domain,
        environment=WorkflowEnvironment(
            entities=[
                Entity(
                    name=s.primary_entity,
                    description=f"Primary record for the {s.workflow_id} workflow.",
                    fields=[
                        EntityField(name=n, type=t)
                        for n, t in s.primary_entity_fields
                    ],
                ),
            ],
            data_sources=[
                DataSource(
                    id=s.data_source_id,
                    description=s.data_source_description,
                    entity=s.primary_entity,
                    provenance=primary_provenance,
                ),
            ],
            env_generators=[
                EnvGenerator(
                    name=s.env_generator_name,
                    description=s.env_generator_description,
                    provenance=Provenance(
                        kind="inferred",
                        source=f"{s.domain} domain pattern for {s.workflow_id}",
                    ),
                ),
            ],
            personas=[
                Persona(
                    role=s.persona_role,
                    cadence=s.persona_cadence,
                    description=s.persona_description,
                    provenance=primary_provenance,
                ),
            ],
        ),
        tools=[
            AgentTool(
                name=name,
                description=desc,
                inputs=[ToolParam(name="record_id", type="string")],
                outputs=[ToolParam(name="result", type="string")],
                provenance=primary_provenance,
            )
            for name, desc in s.tools
        ],
        known_past_misses=list(s.past_misses),
        reviewer=ReviewerSpec(
            role=s.reviewer_role,
            cadence=s.persona_cadence,
            description=s.reviewer_description,
            provenance=primary_provenance,
        ),
        success_criterion=SuccessCriterionStub(
            direction=s.success_direction,
            target_metric_name=s.success_metric_name,
            description=s.success_description,
        ),
        ui=UILayout(
            layout="tabs",
            tabs=[
                UITab(
                    name="Operate",
                    views=[
                        MetricCards(
                            type="MetricCards",
                            fields=["pending_count", "flagged_today", "throughput"],
                        ),
                        AlertList(
                            type="AlertList",
                            source=s.data_source_id,
                            severity_field="severity",
                            title_field="record_id",
                        ),
                    ],
                ),
            ],
        ),
    )

    # ----- SimulationPlan --------------------------------------------------
    init_state_code = (
        f"return {{'records': [], 'next_id': 0, 'workflow': {s.workflow_id!r}}}"
    )
    step_code = (
        # Plausible-looking step body. Toggles label_field based on rng to
        # produce mixed True/False outcomes across sim_seeds — meta-eval
        # judge reads this as a string, not an executable.
        f"record_id = state['next_id']\n"
        f"state['next_id'] += 1\n"
        f"score = rng.random()\n"
        f"label = score > 0.5\n"
        f"event = {{'step_index': step_index, 'record_id': record_id, "
        f"{s.label_field!r}: label, 'score': float(score)}}\n"
        "return event"
    )
    plan = SimulationPlan(
        workflow_spec_id=s.workflow_id,
        description=(
            f"Stub simulator for {s.workflow_id}: emits one record per step "
            f"with a {s.label_field} bool decision."
        ),
        n_steps_default=50,
        seed_default=42,
        imports=[],
        init_state_code=init_state_code,
        step_code=step_code,
        event_fields=[
            EventField(name="step_index", type="int"),
            EventField(name="record_id", type="int"),
            EventField(name=s.label_field, type="bool"),
            EventField(name="score", type="float"),
        ],
    )

    # ----- EvalCaseSet -----------------------------------------------------
    if len(s.case_rationales) != 10:
        raise ValueError(
            f"{s.workflow_id}: case_rationales must have exactly 10 entries; "
            f"got {len(s.case_rationales)}"
        )
    true_count = sum(1 for v, _ in s.case_rationales if v)
    if true_count < 3 or (10 - true_count) < 3:
        raise ValueError(
            f"{s.workflow_id}: case_rationales must be balanced ≥3/≥3 "
            f"(got True={true_count}, False={10 - true_count})"
        )
    cases = [
        GeneratedEvalCase(
            case_id=f"case-{idx:02d}",
            provenance=Provenance(
                kind="derived" if idx < len(s.past_misses) else "inferred",
                source=(
                    s.past_misses[idx]
                    if idx < len(s.past_misses)
                    else f"{s.domain} replay seed {idx}"
                ),
            ),
            sim_seed=idx * 7 + 1,
            n_steps=20,
            target_step_index=(idx % 10) + 5,
            target_label_field=s.label_field,
            expected_value=expected,
            rationale=rationale,
        )
        for idx, (expected, rationale) in enumerate(s.case_rationales)
    ]
    case_set = EvalCaseSet(
        workflow_spec_id=s.workflow_id,
        simulation_plan_workflow_id=s.workflow_id,
        cases=cases,
    )

    # ----- MetricDefinition -------------------------------------------------
    metric = MetricDefinition(
        workflow_spec_id=s.workflow_id,
        name=s.success_metric_name,
        family=s.metric_family,
        direction=s.success_direction,
        lower_bound=0.0,
        upper_bound=1.0,
        target_value=s.target_value,
        description=s.metric_description,
        rationale=s.metric_rationale,
        provenance=Provenance(
            kind="derived",
            source=s.past_misses[0] if s.past_misses else s.success_description,
        ),
    )

    return MinimalBundle(
        description=s.description,
        spec=spec,
        plan=plan,
        case_set=case_set,
        metric=metric,
    )


# ===========================================================================
# 7 fixture specs
# ===========================================================================


_SUPPLIER_LATE = _FixtureSpec(
    workflow_id="supplier-late-shipment-risk",
    description=(
        "Predict which supplier shipments are at risk of arriving late so "
        "procurement can pre-empt stockouts. Pull purchase orders from our "
        "ERP and historical on-time-delivery records from the supplier "
        "scorecard system. The procurement lead reviews flagged shipments "
        "every morning; a flag is correct if the shipment arrives more than "
        "two days late.\n\nPast misses: we missed a Tier-2 supplier whose "
        "lead-time variance had crept up over the last quarter, missed a "
        "weather-related port-closure window in the Gulf, and over-flagged "
        "a Tier-1 supplier whose delays were inside the contractual buffer."
    ),
    domain="supply-chain",
    primary_entity="purchase_order",
    primary_entity_fields=[
        ("po_id", "string"),
        ("supplier_id", "string"),
        ("expected_arrival", "date"),
        ("late_risk", "category"),
    ],
    data_source_id="erp_purchase_orders",
    data_source_description="Open purchase orders with expected arrival.",
    env_generator_name="supplier_lead_time_history",
    env_generator_description="Historical on-time vs late deliveries per supplier.",
    persona_role="Procurement lead",
    persona_cadence="daily-morning",
    persona_description="Reviews flagged shipments before procurement standup.",
    tools=[
        ("flag_late_shipment", "Flag a shipment as at-risk."),
        ("fetch_supplier_history", "Returns last-90-days on-time stats."),
        ("check_route_disruption", "Returns active port/weather alerts on the route."),
    ],
    past_misses=[
        "missed a Tier-2 supplier whose lead-time variance had crept up over the last quarter",
        "missed a weather-related port-closure window in the Gulf",
        "over-flagged a Tier-1 supplier whose delays were inside the contractual buffer",
    ],
    reviewer_role="Procurement lead",
    reviewer_description="Approves or dismisses each at-risk flag.",
    success_metric_name="late-shipment-recall",
    success_description=(
        "Recall on actually-late shipments — missing a stockout is more "
        "costly than over-flagging."
    ),
    success_direction="maximize",
    metric_family="recall",
    target_value=0.65,
    metric_description=(
        "Fraction of actually-late shipments that the agent flagged ahead "
        "of arrival. Maximizes recall over precision because stockouts "
        "cost more than dismissed false alarms."
    ),
    metric_rationale=(
        "Past-miss is dominated by missing late shipments, so recall is "
        "the binding error mode."
    ),
    label_field="is_late",
    case_rationales=[
        (True, "Tier-2 supplier with creeping lead-time variance — past miss."),
        (True, "Gulf-port weather disruption window — past miss."),
        (False, "Tier-1 within contractual buffer — over-flag in past."),
        (True, "First-time supplier with no historical baseline."),
        (True, "Container ship with reported port congestion."),
        (True, "Supplier with prior consecutive-late streak."),
        (False, "Same-region supplier with stable on-time history."),
        (False, "Domestic LTL shipment with carrier-tracked ETA."),
        (False, "Standard reorder from preferred supplier, no signals."),
        (True, "Cross-border shipment hitting customs holiday."),
    ],
)


_FRAUD_DECLINE = _FixtureSpec(
    workflow_id="fraud-card-decline-review",
    description=(
        "Review borderline credit-card decline decisions to reduce false "
        "declines on legitimate transactions. Pull the decline event + "
        "30-day cardholder history + merchant risk score. The fraud "
        "operations analyst reviews flagged borderline declines hourly; "
        "a reversal is correct if the cardholder retries successfully "
        "within 24 hours and no chargeback follows.\n\nPast misses: we "
        "wrongly held declines on travel-abroad first-card-uses, declined "
        "a series of small recurring subscriptions that the cardholder "
        "had pre-authorized, and reversed a decline on a card later "
        "confirmed compromised."
    ),
    domain="credit-risk",
    primary_entity="decline_event",
    primary_entity_fields=[
        ("event_id", "string"),
        ("card_id", "string"),
        ("amount", "float"),
        ("merchant_risk", "category"),
    ],
    data_source_id="auth_decline_stream",
    data_source_description="Real-time stream of card-auth decline events.",
    env_generator_name="cardholder_history_synthetic",
    env_generator_description="Synthetic 30-day spend patterns per cardholder profile.",
    persona_role="Fraud operations analyst",
    persona_cadence="hourly",
    persona_description="Reviews borderline declines to overturn or hold.",
    tools=[
        ("recommend_reversal", "Recommends overturning a decline."),
        ("fetch_card_history", "Returns 30-day cardholder spend pattern."),
        ("check_merchant_risk", "Returns merchant risk score + recent fraud flags."),
    ],
    past_misses=[
        "wrongly held declines on travel-abroad first-card-uses",
        "declined a series of small recurring subscriptions the cardholder had pre-authorized",
        "reversed a decline on a card later confirmed compromised",
    ],
    reviewer_role="Fraud operations analyst",
    reviewer_description="Decides reversal vs hold for each flagged decline.",
    success_metric_name="reversal-precision",
    success_description=(
        "Precision on reversals — overturning a decline on a compromised "
        "card costs more than holding a legitimate one."
    ),
    success_direction="maximize",
    metric_family="precision",
    target_value=0.75,
    metric_description=(
        "Of reversals the agent recommends, the fraction that the "
        "cardholder retries successfully and that produce no chargeback."
    ),
    metric_rationale=(
        "Past-miss includes reversing a compromised-card decline; "
        "false-reversal cost dominates false-hold cost."
    ),
    label_field="should_reverse",
    case_rationales=[
        (True, "Travel-abroad first card use — past miss for false-hold."),
        (True, "Pre-authorized recurring subscription — past miss for false-hold."),
        (False, "Cardholder reported compromise; reversing would be loss."),
        (True, "Cardholder's pattern matches usual gas-station auth."),
        (False, "Decline on stolen-card report from issuer this morning."),
        (True, "Long-time customer, well-trusted merchant, modest amount."),
        (False, "High-risk merchant + unusual time-of-day."),
        (False, "Decline on velocity-flagged $5k retry-attempt sequence."),
        (True, "Cardholder paid before from this merchant, same bin."),
        (True, "Routine grocery-store amount within normal envelope."),
    ],
)


_CLINICAL_ELIGIBILITY = _FixtureSpec(
    workflow_id="clinical-trial-eligibility",
    description=(
        "Screen patient records for inclusion / exclusion criteria for a "
        "Phase-2 oncology trial. Pull EHR records and lab panels via the "
        "clinical data exchange. The trial coordinator reviews flagged "
        "candidates each weekday; a flag is correct if the candidate "
        "passes the manual chart review and consents.\n\nPast misses: "
        "we missed a candidate whose creatinine clearance was flagged in "
        "an earlier note but normal in the latest panel, missed a "
        "candidate excluded by an interaction with their current "
        "anticoagulant, and surfaced a candidate whose biopsy stage was "
        "outside the protocol band."
    ),
    domain="other",
    primary_entity="candidate_record",
    primary_entity_fields=[
        ("patient_id", "string"),
        ("dx_stage", "category"),
        ("creatinine_clearance", "float"),
        ("med_list", "string"),
    ],
    data_source_id="ehr_exchange",
    data_source_description="EHR + lab panels via the clinical data exchange.",
    env_generator_name="trial_protocol_corpus",
    env_generator_description="Active trial protocol inclusion/exclusion criteria.",
    persona_role="Trial coordinator",
    persona_cadence="weekday-morning",
    persona_description="Confirms eligibility on each flagged candidate.",
    tools=[
        ("flag_eligible_candidate", "Flag a patient as a likely-eligible candidate."),
        ("fetch_lab_panel", "Returns most recent lab panel for a patient."),
        ("check_med_interactions", "Returns trial-medication interactions for the patient's current meds."),
    ],
    past_misses=[
        "missed a candidate whose creatinine clearance was flagged in an earlier note but normal in the latest panel",
        "missed a candidate excluded by an interaction with their current anticoagulant",
        "surfaced a candidate whose biopsy stage was outside the protocol band",
    ],
    reviewer_role="Trial coordinator",
    reviewer_description="Final eligibility call after manual chart review.",
    success_metric_name="eligibility-balanced-accuracy",
    success_description=(
        "Balanced accuracy on eligibility decisions — the eligible-candidate "
        "class is sparse, so pass-rate would mask one-sided behavior."
    ),
    success_direction="maximize",
    metric_family="balanced_accuracy",
    target_value=0.55,
    metric_description=(
        "Mean of recall and specificity on eligibility decisions; the "
        "balanced view captures both missed-eligible and false-positive cost."
    ),
    metric_rationale=(
        "Class imbalance (eligible candidates are rare) would let pass-rate "
        "look good while one class is ignored."
    ),
    label_field="is_eligible",
    case_rationales=[
        (True, "Earlier-flagged-then-normalized creatinine — past miss."),
        (False, "Anticoagulant interaction — past miss for false-eligible."),
        (False, "Biopsy stage outside protocol band — past miss for false-eligible."),
        (True, "Newly diagnosed Stage IIB matching protocol exactly."),
        (True, "Stable Stage IIIA, no exclusion meds, recent labs clean."),
        (False, "Active CKD-3 — creatinine clearance below floor."),
        (True, "Post-treatment relapse, performance status clean."),
        (False, "Previously enrolled in conflicting trial within window."),
        (False, "Stage IV — outside the inclusion band."),
        (True, "Recently consented Stage IIIB, panel normal, no interactions."),
    ],
)


_INSURANCE_TRIAGE = _FixtureSpec(
    workflow_id="insurance-claim-triage",
    description=(
        "Triage incoming auto-insurance claims by suspected complexity + "
        "fraud risk. Pull claim filings from the FNOL system and prior "
        "claim history from the policyholder file. The claims supervisor "
        "reviews each AI-routed claim within four hours; a routing is "
        "correct if the claim closes in the routed track without "
        "escalation.\n\nPast misses: we routed a clean rear-end fender "
        "bender to special investigations because of a noisy keyword, "
        "missed an organized-fraud ring whose claims looked individually "
        "clean, and routed a total-loss claim to standard adjusters when "
        "a senior was needed."
    ),
    domain="other",
    primary_entity="auto_claim",
    primary_entity_fields=[
        ("claim_id", "string"),
        ("loss_type", "category"),
        ("est_severity", "float"),
        ("policyholder_id", "string"),
    ],
    data_source_id="fnol_intake",
    data_source_description="First-notice-of-loss claim filings.",
    env_generator_name="claim_history_synthetic",
    env_generator_description="Synthetic policyholder history with fraud-ring patterns.",
    persona_role="Claims supervisor",
    persona_cadence="continuous",
    persona_description="Confirms or overrides each AI-routed claim.",
    tools=[
        ("route_claim", "Routes a claim to standard / senior / SIU."),
        ("fetch_claim_history", "Returns the policyholder's past 5-year history."),
        ("check_fraud_ring_signals", "Returns shared-VIN / repeat-shop signals."),
    ],
    past_misses=[
        "routed a clean rear-end fender bender to special investigations because of a noisy keyword",
        "missed an organized-fraud ring whose claims looked individually clean",
        "routed a total-loss claim to standard adjusters when a senior was needed",
    ],
    reviewer_role="Claims supervisor",
    reviewer_description="Confirms triage routing within four hours of intake.",
    success_metric_name="triage-f1",
    success_description=(
        "F1 on the SIU-route class — both false-route and missed-fraud cost "
        "money, neither dominates."
    ),
    success_direction="maximize",
    metric_family="f1",
    target_value=0.55,
    metric_description=(
        "Harmonic mean of precision and recall on the SIU routing decision; "
        "balances false routing (operational cost) with missed fraud (loss cost)."
    ),
    metric_rationale=(
        "Past-misses include both false routing (rear-end → SIU) and missed "
        "fraud (organized ring) — neither error mode dominates, so F1 is "
        "the safe family."
    ),
    label_field="needs_siu",
    case_rationales=[
        (False, "Clean rear-end fender bender — past miss for false-SIU."),
        (True, "Organized-ring shared-VIN signal — past miss for missed-fraud."),
        (True, "Total-loss with conflicting odometer — needs senior + SIU."),
        (False, "Standard windshield comprehensive claim, low severity."),
        (False, "Pet-impact claim, photographs match police report."),
        (True, "Repeat shop + repeat policyholder + parking-lot loss."),
        (True, "Late-reported high-value theft, policy near binding window."),
        (False, "Hail damage cluster matching regional storm event."),
        (False, "Comprehensive glass claim with same-day estimate."),
        (True, "Soft-tissue injury claim, attorney letter on day one."),
    ],
)


_HR_POLICY = _FixtureSpec(
    workflow_id="hr-policy-violation-review",
    description=(
        "Review reported HR incidents and flag likely policy violations "
        "for People Operations. Pull incident reports from the HR case "
        "system and policy text from the employee handbook repository. "
        "The People Ops partner reviews flagged incidents weekly; a flag "
        "is correct if it survives the formal investigation outcome.\n\n"
        "Past misses: we flagged a manager-employee disagreement that "
        "wasn't actually a policy violation, missed a series of micro-"
        "aggression reports that pattern-matched only when read together, "
        "and missed a remote-work expense pattern that violated a recent "
        "policy update."
    ),
    domain="labour",
    primary_entity="incident_report",
    primary_entity_fields=[
        ("incident_id", "string"),
        ("category", "category"),
        ("reporter_role", "category"),
        ("text", "string"),
    ],
    data_source_id="hr_case_intake",
    data_source_description="Reported incidents from the HR case system.",
    env_generator_name="policy_handbook_corpus",
    env_generator_description="Versioned policy text + recent update changelog.",
    persona_role="People Ops partner",
    persona_cadence="weekly",
    persona_description="Triages flagged incidents into formal investigation queues.",
    tools=[
        ("flag_violation", "Flag an incident as a likely policy violation."),
        ("fetch_policy_text", "Returns relevant policy + version."),
        ("search_related_incidents", "Returns related incidents in the last 12 months."),
    ],
    past_misses=[
        "flagged a manager-employee disagreement that wasn't actually a policy violation",
        "missed a series of micro-aggression reports that pattern-matched only when read together",
        "missed a remote-work expense pattern that violated a recent policy update",
    ],
    reviewer_role="People Ops partner",
    reviewer_description="Confirms each flagged violation before formal investigation.",
    success_metric_name="violation-precision",
    success_description=(
        "Precision on flagged violations — false flags carry investigation "
        "cost + employee-trust cost; recall is improved by the human-review "
        "queue rather than the agent."
    ),
    success_direction="maximize",
    metric_family="precision",
    target_value=0.70,
    metric_description=(
        "Of incidents the agent flagged as violations, the fraction confirmed "
        "by formal investigation outcome."
    ),
    metric_rationale=(
        "Past-miss includes a false-flag on a manager-employee disagreement; "
        "false-flag cost (investigation + trust) dominates."
    ),
    label_field="is_violation",
    case_rationales=[
        (False, "Manager-employee disagreement — past miss for false-flag."),
        (True, "Micro-aggression pattern across 3 reporters — past miss for missed-pattern."),
        (True, "Remote-work expense pattern under updated policy — past miss."),
        (True, "Time-tracking falsification corroborated by access logs."),
        (False, "Reporter-only complaint with no corroborating signal."),
        (True, "Confidentiality breach, screenshot evidence attached."),
        (False, "Workload-fairness gripe — out of policy scope."),
        (True, "Conflict-of-interest disclosure not filed within window."),
        (False, "Process-grievance about meeting cadence — not a violation."),
        (True, "Vendor-relationship rule-of-50 breach with disclosure miss."),
    ],
)


_CONTENT_MOD = _FixtureSpec(
    workflow_id="content-moderation-escalation",
    description=(
        "Triage user-generated posts that auto-moderation flagged into "
        "the human-review queue or auto-resolve. Pull the post text plus "
        "the user's recent moderation history. The trust & safety reviewer "
        "handles escalations within thirty minutes; a routing is correct "
        "if the human reviewer's call matches the AI's escalation "
        "decision.\n\nPast misses: we auto-resolved a coded-language "
        "harassment pattern that matched only on slang context, escalated "
        "a sarcastic in-joke between two longstanding mutual followers, "
        "and missed a brigading pattern across coordinated low-history "
        "accounts."
    ),
    domain="support",
    primary_entity="moderation_event",
    primary_entity_fields=[
        ("event_id", "string"),
        ("post_text", "string"),
        ("user_account_age_days", "int"),
        ("auto_score", "float"),
    ],
    data_source_id="auto_moderation_queue",
    data_source_description="Posts flagged by the automoderator pipeline.",
    env_generator_name="user_moderation_history",
    env_generator_description="Per-user 90-day moderation history + community context.",
    persona_role="Trust & safety reviewer",
    persona_cadence="continuous",
    persona_description="Handles escalated moderation events; resolves within 30 min.",
    tools=[
        ("escalate_to_human", "Routes the event to the human-review queue."),
        ("fetch_user_history", "Returns the user's 90-day moderation timeline."),
        ("search_related_events", "Returns coordinated/brigading signals across users."),
    ],
    past_misses=[
        "auto-resolved a coded-language harassment pattern that matched only on slang context",
        "escalated a sarcastic in-joke between two longstanding mutual followers",
        "missed a brigading pattern across coordinated low-history accounts",
    ],
    reviewer_role="Trust & safety reviewer",
    reviewer_description="Final call on escalated moderation events.",
    success_metric_name="escalation-recall",
    success_description=(
        "Recall on events the human reviewer would escalate — missing "
        "harassment is more costly than over-escalating."
    ),
    success_direction="maximize",
    metric_family="recall",
    target_value=0.65,
    metric_description=(
        "Fraction of human-reviewer-escalated events that the agent also "
        "escalated. Maximizes recall over precision because missed "
        "harassment carries trust-and-safety cost."
    ),
    metric_rationale=(
        "Past-miss includes auto-resolving coded-language harassment; "
        "the missed-escalation cost dominates the false-escalation cost."
    ),
    label_field="should_escalate",
    case_rationales=[
        (True, "Coded-language harassment matching slang context — past miss."),
        (False, "Sarcastic in-joke between mutuals — past miss for false-escalate."),
        (True, "Brigading pattern across coordinated low-history accounts — past miss."),
        (True, "First-time-poster with overt harassment text."),
        (False, "Long-history user, single benign reply, no signals."),
        (True, "Coordinated reply spam from new accounts on a thread."),
        (False, "Heated political debate within community-policy bounds."),
        (True, "Targeted misgendering with prior moderation history."),
        (False, "Repeated-but-benign tag-spam from contest winner."),
        (True, "Off-platform doxxing-link inside an in-platform reply."),
    ],
)


_MFG_DEFECT = _FixtureSpec(
    workflow_id="manufacturing-defect-detection",
    description=(
        "Predict which production runs are at risk of defect-rate spikes "
        "before the QA bay catches them. Pull line telemetry from the OT "
        "data historian and recent QA hold logs from the MES. The plant "
        "QA lead reviews flagged runs each shift; a flag is correct if "
        "the run's later QA-bay sampling exceeds the defect-rate "
        "threshold.\n\nPast misses: we missed a torque-tool drift that "
        "showed up only after a tool change, missed a humidity-sensitive "
        "spike on a polymer line during a HVAC anomaly, and over-flagged "
        "a stable line whose telemetry noise was within tolerance."
    ),
    domain="other",
    primary_entity="production_run",
    primary_entity_fields=[
        ("run_id", "string"),
        ("line_id", "category"),
        ("part_number", "string"),
        ("est_defect_rate", "float"),
    ],
    data_source_id="ot_historian",
    data_source_description="Line telemetry archive (1Hz for ten days).",
    env_generator_name="qa_hold_history",
    env_generator_description="MES QA-hold log keyed by run + part number.",
    persona_role="Plant QA lead",
    persona_cadence="per-shift",
    persona_description="Reviews flagged runs each shift; pulls a sample if needed.",
    tools=[
        ("flag_at_risk_run", "Flag a run as at-risk before QA-bay sampling."),
        ("fetch_telemetry_window", "Returns 60-min telemetry window for a run."),
        ("check_recent_holds", "Returns last-30-day hold history for the line."),
    ],
    past_misses=[
        "missed a torque-tool drift that showed up only after a tool change",
        "missed a humidity-sensitive spike on a polymer line during a HVAC anomaly",
        "over-flagged a stable line whose telemetry noise was within tolerance",
    ],
    reviewer_role="Plant QA lead",
    reviewer_description="Confirms or pulls a sample for each at-risk run.",
    success_metric_name="defect-recall",
    success_description=(
        "Recall on actually-defective runs — missing a defect spike costs "
        "more than over-flagging a stable line."
    ),
    success_direction="maximize",
    metric_family="recall",
    target_value=0.60,
    metric_description=(
        "Fraction of actually-defective runs that the agent flagged before "
        "QA-bay sampling. Maximizes recall over precision."
    ),
    metric_rationale=(
        "Past-miss is dominated by missed defect spikes (torque drift + "
        "humidity sensitivity); recall is the binding error mode."
    ),
    label_field="is_defective",
    case_rationales=[
        (True, "Torque-tool drift after tool change — past miss."),
        (True, "HVAC anomaly + polymer-line humidity spike — past miss."),
        (False, "Stable line with telemetry noise within tolerance — past over-flag."),
        (True, "First run on a new shift with cold-start variance."),
        (False, "Long-running line with multi-day stable variance."),
        (True, "Coolant-flow alarm spike on the assembly line."),
        (True, "Machine-vision rejection rate climbing across the shift."),
        (False, "Routine product changeover, no telemetry deviation."),
        (False, "Standard run within historical control limits."),
        (True, "Vendor-supplied substrate batch flagged on inbound check."),
    ],
)


_ALL_FIXTURE_SPECS = [
    _SUPPLIER_LATE,
    _FRAUD_DECLINE,
    _CLINICAL_ELIGIBILITY,
    _INSURANCE_TRIAGE,
    _HR_POLICY,
    _CONTENT_MOD,
    _MFG_DEFECT,
]


MINIMAL_BUNDLES: dict[str, MinimalBundle] = {
    s.workflow_id: _build_bundle(s) for s in _ALL_FIXTURE_SPECS
}
"""Public dict — workflow_id → MinimalBundle. Built once at import time."""


MINIMAL_DESCRIPTIONS: dict[str, str] = {
    s.workflow_id: s.description for s in _ALL_FIXTURE_SPECS
}
"""Public dict — workflow_id → original NL description. Convenience handle
for callers that only need the description."""


__all__ = [
    "MinimalBundle",
    "MINIMAL_BUNDLES",
    "MINIMAL_DESCRIPTIONS",
]
