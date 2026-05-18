"""Retail demand planning — supply chain VP discovery prompts."""

from __future__ import annotations

from ._types import DiscoveryQuestion

DISCOVERY_QUESTIONS: tuple[DiscoveryQuestion, ...] = (
    DiscoveryQuestion(
        kind="metric",
        question=(
            "Cost of overstock vs. cost of stockout — which dominates in "
            "your environment? If overstock is roughly $X per overstocked "
            "SKU-week (carrying cost + markdown) and stockout is roughly "
            "$Y per unit-day of lost sales (revenue + customer-trust "
            "impact), the metric should weight recall over precision by a "
            "factor of Y/X. Most retail teams land between 2× and 8× "
            "depending on category. Want me to calibrate at 3× as a "
            "default, or do you have a number?"
        ),
        options=("Avoid overstock", "Avoid stockouts", "Balanced (3x)"),
        rationale=(
            "Overstock vs. stockout sets the metric's recall/precision "
            "weighting. Treating it as a first-class input avoids baking "
            "an implicit assumption into the generated prompt."
        ),
    ),
    DiscoveryQuestion(
        kind="ambiguity",
        question=(
            "You said 'flag SKUs likely to need markdown' — likely for "
            "stockout, likely for slow-sell, or likely for seasonal-end? "
            "Each generates a different eval set. I'll generate three "
            "options and let you pick — or you can describe the "
            "operational moment the markdown decision happens."
        ),
        options=("Stockout risk", "Slow-sell risk", "Seasonal-end risk"),
        rationale=(
            "Markdown semantics drive which trajectory step the eval "
            "cases pin against; without disambiguation NL-gen guesses."
        ),
    ),
)
