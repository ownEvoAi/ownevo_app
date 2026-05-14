"""Contract-review fixture — aligned with the Priya Anand persona +
union-contract-review failure-mode taxonomy."""

from __future__ import annotations

from ownevo_format import (
    AlertList,
    DocumentReader,
    KanbanBoard,
    MetricCards,
    SideBySideView,
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

CONTRACT_REVIEW_DESCRIPTION = (
    "Review proposed changes to our union contracts before each negotiation "
    "round. Flag risk clauses, jurisdictional carve-outs, and grievance "
    "precedent that the new language would breach.\n\n"
    "Pull current contract text from our document management system. Pull "
    "grievance history from the labour-relations case database. The labour "
    "relations counsel reviews flagged clauses weekly; a flag is correct if "
    "it identifies a clause that legal subsequently redlines or escalates.\n\n"
    "Past misses: we missed a state-specific overtime carve-out in the 2024 "
    "Western region renewal, missed a grievance precedent from 18 months "
    "earlier on shift-bidding language, and proposed a 30-day notification "
    "window that breached the existing 60-day requirement."
)

CONTRACT_REVIEW_SPEC = WorkflowSpec(
    id="union-contract-review",
    domain="legal",
    environment=WorkflowEnvironment(
        entities=[
            Entity(
                name="contract_clause",
                description="One numbered clause within a union contract.",
                fields=[
                    EntityField(name="clause_id", type="string"),
                    EntityField(name="topic", type="category"),
                    EntityField(name="jurisdiction", type="category"),
                    EntityField(name="text", type="string"),
                ],
            ),
            Entity(
                name="clause_flag",
                description="A risk flag on a proposed clause change.",
                fields=[
                    EntityField(name="clause_id", type="string"),
                    EntityField(name="risk_type", type="category"),
                    EntityField(name="severity", type="category"),
                    EntityField(name="rationale", type="string"),
                ],
            ),
        ],
        data_sources=[
            DataSource(
                id="dms_contracts",
                description="Current contract text under version control.",
                entity="contract_clause",
                provenance=Provenance(
                    kind="derived",
                    source="Pull current contract text from our document management system",
                ),
            ),
            DataSource(
                id="grievance_db",
                description="Historical grievance cases and outcomes.",
                provenance=Provenance(
                    kind="derived",
                    source="Pull grievance history from the labour-relations case database",
                ),
            ),
        ],
        env_generators=[
            EnvGenerator(
                name="jurisdictional_rule_set",
                description="State + federal labour rules per jurisdiction.",
                provenance=Provenance(
                    kind="inferred", source="labour-law contract review domain pattern"
                ),
            ),
            EnvGenerator(
                name="grievance_precedent_corpus",
                description="Synthetic grievance cases with outcome bands.",
            ),
        ],
        personas=[
            Persona(
                role="Labour relations counsel",
                cadence="weekly",
                description="Reviews flagged clauses, escalates to legal review.",
                provenance=Provenance(
                    kind="derived",
                    source="The labour relations counsel reviews flagged clauses weekly",
                ),
            ),
            Persona(
                role="Negotiation lead",
                cadence="per-round",
                description="Pulls flagged clauses ahead of each negotiation round.",
                provenance=Provenance(
                    kind="inferred",
                    source="union contract negotiation domain pattern",
                ),
            ),
        ],
        seasonality=["renewal-cycle"],
    ),
    tools=[
        AgentTool(
            name="flag_clause_risk",
            description="Flags a clause as risk + risk type + severity.",
            inputs=[
                ToolParam(name="clause_id", type="string"),
                ToolParam(name="risk_type", type="category"),
                ToolParam(name="severity", type="category"),
                ToolParam(name="rationale", type="string"),
            ],
            provenance=Provenance(
                kind="derived",
                source="Flag risk clauses, jurisdictional carve-outs, and grievance precedent",
            ),
        ),
        AgentTool(
            name="fetch_clause_text",
            description="Returns the current text of a numbered clause.",
            inputs=[ToolParam(name="clause_id", type="string")],
            outputs=[ToolParam(name="text", type="string")],
            provenance=Provenance(
                kind="derived",
                source="Pull current contract text from our document management system",
            ),
        ),
        AgentTool(
            name="search_grievance_precedent",
            description="Returns grievance cases matching a clause + topic.",
            inputs=[
                ToolParam(name="topic", type="category"),
                ToolParam(name="years_back", type="int"),
            ],
            outputs=[ToolParam(name="cases", type="string")],
            provenance=Provenance(
                kind="derived",
                source="Pull grievance history from the labour-relations case database",
            ),
        ),
        AgentTool(
            name="check_jurisdictional_rules",
            description="Returns conflicting jurisdictional rules for a clause.",
            inputs=[
                ToolParam(name="jurisdiction", type="category"),
                ToolParam(name="topic", type="category"),
            ],
            outputs=[ToolParam(name="conflicts", type="string")],
            provenance=Provenance(
                kind="inferred",
                source="labour-law jurisdictional carve-out review pattern",
            ),
        ),
    ],
    known_past_misses=[
        "missed a state-specific overtime carve-out in the 2024 Western region renewal",
        "missed a grievance precedent from 18 months earlier on shift-bidding language",
        "proposed a 30-day notification window that breached the existing 60-day requirement",
    ],
    reviewer=ReviewerSpec(
        role="Labour relations counsel",
        cadence="weekly",
        description="Reviews flagged clauses, escalates to legal redline.",
        provenance=Provenance(
            kind="derived",
            source="The labour relations counsel reviews flagged clauses weekly",
        ),
    ),
    success_criterion=SuccessCriterionStub(
        direction="maximize",
        target_metric_name="clause_flag_precision_recall",
        description=(
            "A flag is correct if it identifies a clause that legal "
            "subsequently redlines or escalates. Composite of precision and "
            "recall over flagged-clause set, weighted by clause severity."
        ),
    ),
    ui=UILayout(
        layout="tabs",
        tabs=[
            UITab(
                name="Review",
                primitives=[
                    MetricCards(
                        type="MetricCards",
                        fields=[
                            "clause_coverage_pct",
                            "flagged_clauses_pending",
                            "high_severity_count",
                        ],
                    ),
                    DocumentReader(
                        type="DocumentReader",
                        source="dms_contracts",
                        annotations_source="clause_flags",
                    ),
                    SideBySideView(
                        type="SideBySideView",
                        left_source="current_clause",
                        right_source="proposed_clause",
                        diff_mode="text",
                    ),
                    AlertList(
                        type="AlertList",
                        source="clause_flags",
                        severity_field="severity",
                        title_field="clause_id",
                    ),
                    # Per-case outcome kanban — layer-D resolver fills
                    # from `iteration_case_outputs` (PLAN 8.4.10).
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


__all__ = ["CONTRACT_REVIEW_SPEC", "CONTRACT_REVIEW_DESCRIPTION"]
