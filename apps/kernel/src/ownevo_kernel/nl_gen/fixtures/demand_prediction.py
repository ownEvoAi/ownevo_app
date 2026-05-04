"""Demand-prediction fixture — verbatim from www/preview/s26-rk7p3/03-new-workflow-step1.html."""

from __future__ import annotations

from ownevo_format import (
    AlertList,
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

DEMAND_PREDICTION_DESCRIPTION = (
    "Forecast weekly demand at SKU-store level for our 8,400 SKU catalog "
    "across 142 stores. Flag SKUs likely to need markdown within 4 weeks. "
    "Account for seasonality, supplier price-build, and regional variance.\n\n"
    "Pull historical sales from SAP ERP. Pull current weather forecasts from "
    "NOAA for cold-weather categories. The supply chain VP reviews markdown "
    "alerts daily; an alert is correct if it lands within 6 weeks of the "
    "actual markdown event and the recommended discount is within 5pp of "
    "what we ultimately ran.\n\n"
    "Past misses: we missed the 2025 Pacific NW winter boot spike by 4 "
    "weeks, and we routinely underweight promotional uplift on bundled SKUs."
)
"""Verbatim from www/preview/s26-rk7p3/03-new-workflow-step1.html. The user's
description lives on `workflows.description`, not in the spec — this is the
input the generator sees, exposed here so live tests can drive it."""

DEMAND_PREDICTION_SPEC = WorkflowSpec(
    id="supply-chain-demand-forecast",
    domain="supply-chain",
    environment=WorkflowEnvironment(
        entities=[
            Entity(
                name="sku",
                description="A stock-keeping unit in the catalog.",
                fields=[
                    EntityField(name="sku_id", type="string"),
                    EntityField(name="category", type="category"),
                    EntityField(name="region", type="category"),
                ],
            ),
            Entity(
                name="markdown_alert",
                description="A flag that an SKU is likely to need markdown.",
                fields=[
                    EntityField(name="sku_id", type="string"),
                    EntityField(name="severity", type="category"),
                    EntityField(name="recommended_discount_bp", type="int"),
                    EntityField(name="weeks_ahead", type="int"),
                ],
            ),
        ],
        data_sources=[
            DataSource(
                id="sap_sales",
                description="Historical weekly sales by SKU and store.",
                entity="sku",
                provenance=Provenance(
                    kind="derived", source="Pull historical sales from SAP ERP"
                ),
            ),
            DataSource(
                id="noaa_weather",
                description="Forecast deltas, normalized to anomaly score.",
                provenance=Provenance(
                    kind="derived",
                    source="Pull current weather forecasts from NOAA",
                ),
            ),
        ],
        env_generators=[
            EnvGenerator(
                name="synthetic_sku_catalog",
                description="8,400 SKUs across 6 categories, mirrors real distribution.",
            ),
            EnvGenerator(
                name="supplier_behaviour",
                description="12 suppliers, 3-12wk lead times, monthly price drift.",
                provenance=Provenance(
                    kind="derived",
                    source="Account for seasonality, supplier price-build",
                ),
            ),
            EnvGenerator(
                name="weather_generator",
                description="NOAA-shaped anomalies; PNW cold snaps, SE hurricanes.",
                provenance=Provenance(
                    kind="derived",
                    source=(
                        "Pull current weather forecasts from NOAA for "
                        "cold-weather categories"
                    ),
                ),
            ),
        ],
        personas=[
            Persona(
                role="Supply chain analyst",
                cadence="Monday 9:00 PT",
                description=(
                    "Opens markdown report each Monday, asks the agent to "
                    "forecast next 4 weeks per region, drills into anything "
                    "flagged high severity."
                ),
                provenance=Provenance(
                    kind="inferred", source="supply chain forecasting domain pattern"
                ),
            ),
            Persona(
                role="Supply Chain VP",
                cadence="daily",
                description=(
                    "Reviews fired markdown alerts daily, approves/rejects "
                    "each, comments form new eval cases."
                ),
                provenance=Provenance(
                    kind="derived",
                    source="The supply chain VP reviews markdown alerts daily",
                ),
            ),
        ],
        seasonality=["weekly", "quarterly", "holiday"],
    ),
    tools=[
        AgentTool(
            name="forecast_demand",
            description="Returns a forecast with confidence intervals.",
            inputs=[
                ToolParam(name="sku", type="string"),
                ToolParam(name="region", type="category"),
                ToolParam(name="weeks_ahead", type="int"),
            ],
            outputs=[
                ToolParam(name="forecast", type="float"),
                ToolParam(name="ci_low", type="float"),
                ToolParam(name="ci_high", type="float"),
            ],
            provenance=Provenance(
                kind="derived",
                source="Forecast weekly demand at SKU-store level",
            ),
        ),
        AgentTool(
            name="fire_markdown_alert",
            description="Notifies the supply chain VP. Logged for audit.",
            inputs=[
                ToolParam(name="sku", type="string"),
                ToolParam(name="region", type="category"),
                ToolParam(name="severity", type="category"),
                ToolParam(name="recommended_discount_bp", type="int"),
            ],
            provenance=Provenance(
                kind="derived",
                source="Flag SKUs likely to need markdown within 4 weeks",
            ),
        ),
        AgentTool(
            name="query_sap_sales",
            description="Historical sales by week.",
            inputs=[
                ToolParam(name="sku", type="string"),
                ToolParam(name="region", type="category"),
                ToolParam(name="start_date", type="date"),
                ToolParam(name="end_date", type="date"),
            ],
            outputs=[ToolParam(name="weekly_units", type="float")],
            provenance=Provenance(
                kind="derived", source="Pull historical sales from SAP ERP"
            ),
        ),
        AgentTool(
            name="fetch_noaa_weather",
            description="Forecast deltas from NOAA, normalized to anomaly score.",
            inputs=[
                ToolParam(name="region", type="category"),
                ToolParam(name="weeks_ahead", type="int"),
            ],
            outputs=[ToolParam(name="anomaly_score", type="float")],
            provenance=Provenance(
                kind="derived", source="Pull current weather forecasts from NOAA"
            ),
        ),
    ],
    known_past_misses=[
        "missed the 2025 Pacific NW winter boot spike by 4 weeks",
        "underweight promotional uplift on bundled SKUs",
    ],
    reviewer=ReviewerSpec(
        role="Supply Chain VP",
        cadence="daily",
        description="Reviews fired markdown alerts; approves or rejects each.",
        provenance=Provenance(
            kind="derived",
            source="The supply chain VP reviews markdown alerts daily",
        ),
    ),
    success_criterion=SuccessCriterionStub(
        direction="maximize",
        target_metric_name="markdown_window_composite",
        description=(
            "An alert is correct if it lands within 6 weeks of the actual "
            "markdown event and the recommended discount is within 5pp of "
            "what we ultimately ran. Composite of precision, recall, and "
            "discount-bp accuracy."
        ),
    ),
    ui=UILayout(
        layout="tabs",
        tabs=[
            UITab(
                name="Forecast",
                primitives=[
                    MetricCards(
                        type="MetricCards",
                        fields=[
                            "forecast_accuracy",
                            "markdown_risk_count",
                            "$_at_risk",
                        ],
                    ),
                    TimeSeriesChart(
                        type="TimeSeriesChart",
                        x="week",
                        y=["forecast", "actual"],
                        group_by="region",
                    ),
                    TableView(
                        type="TableView",
                        source="skus",
                        columns=["sku", "region", "forecast", "alert_severity"],
                    ),
                    AlertList(
                        type="AlertList",
                        source="markdown_alerts",
                        severity_field="severity",
                        title_field="sku_id",
                    ),
                ],
            ),
        ],
    ),
)


__all__ = ["DEMAND_PREDICTION_SPEC", "DEMAND_PREDICTION_DESCRIPTION"]
