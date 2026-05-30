"""Credit-risk fixture — fresh prose in the same voice as the demand-prediction
mock textarea. Used for A3.1 PLAN.md's "credit risk" snapshot."""

from __future__ import annotations

from ownevo_format import (
    AlertList,
    KanbanBoard,
    MetricCards,
    TableView,
    TimeSeriesChart,
)

from ..spec import (
    AgentTool,
    DataSource,
    Entity,
    EntityField,
    EnvGenerator,
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

CREDIT_RISK_DESCRIPTION = (
    "Recalibrate credit lines monthly across our 22,000-SMB portfolio. Flag "
    "accounts where utilization, days-past-due, or sector exposure suggest "
    "the line should be reduced or frozen, and propose a new limit.\n\n"
    "Pull repayment history and DPD bands from the core banking system. "
    "Pull external credit signals from Dun & Bradstreet. The chief risk "
    "officer reviews proposed line changes weekly; a recommendation is "
    "correct if the account does not breach the new limit within 90 days "
    "and does not default within 180 days.\n\n"
    "Past misses: we underweighted hospitality-sector concentration in Q3 "
    "2024 and held lines too high through the spring rate-shock; we also "
    "missed three early-stage delinquencies where DPD bands were stuck on "
    "stale data."
)

CREDIT_RISK_SPEC = WorkflowSpec(
    id="credit-risk-line-recalibration",
    domain="credit-risk",
    environment=WorkflowEnvironment(
        entities=[
            Entity(
                name="account",
                description="An SMB borrower with one or more credit lines.",
                fields=[
                    EntityField(name="account_id", type="string"),
                    EntityField(name="sector", type="category"),
                    EntityField(name="current_limit", type="float"),
                    EntityField(name="utilization_pct", type="float"),
                    EntityField(name="dpd_band", type="category"),
                ],
            ),
            Entity(
                name="line_change_proposal",
                description="A proposed new credit limit for an account.",
                fields=[
                    EntityField(name="account_id", type="string"),
                    EntityField(name="proposed_limit", type="float"),
                    EntityField(name="action", type="category"),
                    EntityField(name="rationale", type="string"),
                ],
            ),
        ],
        data_sources=[
            DataSource(
                id="core_banking",
                description="Repayment history, DPD bands, current limits.",
                entity="account",
                provenance=Provenance(
                    kind="derived",
                    source="Pull repayment history and DPD bands from the core banking system",
                ),
            ),
            DataSource(
                id="dnb_external",
                description="External credit signals and sector indicators.",
                provenance=Provenance(
                    kind="derived",
                    source="Pull external credit signals from Dun & Bradstreet",
                ),
            ),
        ],
        env_generators=[
            EnvGenerator(
                name="synthetic_smb_portfolio",
                description="22,000 SMB accounts spread across 14 sectors.",
            ),
            EnvGenerator(
                name="rate_shock_scenarios",
                description="Macro-rate movements + sector-specific sensitivity.",
                provenance=Provenance(
                    kind="inferred", source="credit-risk macro stress pattern"
                ),
            ),
        ],
        personas=[
            Persona(
                role="Credit analyst",
                cadence="monthly",
                description=(
                    "Runs the recalibration job at month-end, reviews "
                    "automated drops/freezes before they reach the CRO."
                ),
                provenance=Provenance(
                    kind="inferred", source="credit-risk recalibration domain pattern"
                ),
            ),
            Persona(
                role="Chief Risk Officer",
                cadence="weekly",
                description="Approves or rejects proposed line changes.",
                provenance=Provenance(
                    kind="derived",
                    source="The chief risk officer reviews proposed line changes weekly",
                ),
            ),
        ],
        seasonality=["quarterly", "rate-cycle"],
    ),
    tools=[
        AgentTool(
            name="propose_line_change",
            description="Recommends a new credit limit and action.",
            inputs=[
                ToolParam(name="account_id", type="string"),
                ToolParam(name="proposed_limit", type="float"),
                ToolParam(name="action", type="category"),
                ToolParam(name="rationale", type="string"),
            ],
            provenance=Provenance(
                kind="derived",
                source="Flag accounts where utilization, days-past-due, or sector exposure suggest the line should be reduced or frozen",
            ),
        ),
        AgentTool(
            name="query_repayment_history",
            description="Returns weekly repayment + DPD history for an account.",
            inputs=[
                ToolParam(name="account_id", type="string"),
                ToolParam(name="months_back", type="int"),
            ],
            outputs=[ToolParam(name="repayment_series", type="string")],
            provenance=Provenance(
                kind="derived",
                source="Pull repayment history and DPD bands from the core banking system",
            ),
        ),
        AgentTool(
            name="fetch_sector_exposure",
            description="Aggregate exposure for the account's sector.",
            inputs=[ToolParam(name="sector", type="category")],
            outputs=[ToolParam(name="exposure_pct", type="float")],
            provenance=Provenance(
                kind="derived",
                source="sector exposure suggest the line should be reduced",
            ),
        ),
        AgentTool(
            name="fetch_dnb_signal",
            description="External credit signal from Dun & Bradstreet.",
            inputs=[ToolParam(name="account_id", type="string")],
            outputs=[ToolParam(name="signal_score", type="float")],
            provenance=Provenance(
                kind="derived",
                source="Pull external credit signals from Dun & Bradstreet",
            ),
        ),
    ],
    known_past_misses=[
        "underweighted hospitality-sector concentration in Q3 2024",
        "held lines too high through the spring rate-shock",
        "missed three early-stage delinquencies where DPD bands were stuck on stale data",
    ],
    reviewer=ReviewerSpec(
        role="Chief Risk Officer",
        cadence="weekly",
        description="Approves or rejects proposed line changes.",
        provenance=Provenance(
            kind="derived",
            source="The chief risk officer reviews proposed line changes weekly",
        ),
    ),
    success_criterion=SuccessCriterionStub(
        direction="maximize",
        target_metric_name="line_recalibration_composite",
        description=(
            "A recommendation is correct if the account does not breach the "
            "new limit within 90 days and does not default within 180 days. "
            "Composite of breach-rate, default-rate, and over-tightening "
            "false-positive rate."
        ),
    ),
    ui=UILayout(
        layout="tabs",
        tabs=[
            UITab(
                name="Portfolio",
                views=[
                    MetricCards(
                        type="MetricCards",
                        fields=[
                            "portfolio_default_rate",
                            "lines_proposed_to_change",
                            "concentration_top_sector_pct",
                        ],
                    ),
                    TimeSeriesChart(
                        type="TimeSeriesChart",
                        x="month",
                        y=["default_rate", "utilization_avg"],
                        group_by="sector",
                    ),
                    TableView(
                        type="TableView",
                        source="line_change_proposals",
                        columns=[
                            "account_id",
                            "sector",
                            "current_limit",
                            "proposed_limit",
                            "action",
                        ],
                    ),
                    AlertList(
                        type="AlertList",
                        source="recalibration_alerts",
                        severity_field="severity",
                        title_field="account_id",
                    ),
                    # Per-case outcome kanban — layer-D resolver fills
                    # from `iteration_case_outputs` ().
                    KanbanBoard(
                        type="KanbanBoard",
                        source="case-outputs",
                        column_field="passed",
                        card_title_field="case_id",
                    ),
                ],
            ),
        ],
    ),
)


__all__ = ["CREDIT_RISK_SPEC", "CREDIT_RISK_DESCRIPTION"]
