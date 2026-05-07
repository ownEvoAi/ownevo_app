"""Hand-authored cluster-label eval fixtures (B3.5).

20 `LabeledClusterCase` fixtures spanning the M5 failure-mode taxonomy:
under-forecast / over-forecast / zero-inflated / high-variance /
flat-prediction × FOODS / HOUSEHOLD / HOBBIES × CA / TX / WI.

Why synthetic over replay-captured: the eval is testing the *labeler*,
not the rest of the pipeline. Synthetic fixtures pin a known-correct
answer per cluster, are deterministic, and don't bind us to a specific
M5 fixture or sentence-transformers checkout. The W3 Track B exit
criterion is "labeler agrees with humans on what these clusters are
about" — and these 20 cases sample the failure-mode space the M5
analyzer actually emits.

`text_signature` format mirrors `m5_failure_analyzer._text_signature`:
    "<series_id> [<cat>/<dept> @ <state>/<store>] rmsse=<x.xx>
     peak <±value> day <n> hints=[<tag>,<tag>]"

Series IDs follow the M5 convention `<dept>_<item>_<store>_validation`
(parsed by `M5_SERIES_ID_RE` in `m5_failure_analyzer.py`).

Ground-truth labels are short noun phrases (≤8 words) — the same shape
the production `AnthropicLabeler` is told to emit. They are NOT canonical
strings the judge has to match exactly: the judge's job is semantic
equivalence, not regex match. "CA grocery weekend under-forecasts" and
"weekend snack under-forecasts in California" should both be `agree`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class LabeledClusterCase:
    """One hand-labeled cluster fixture.

    `member_signatures` are the strings the labeler sees (matches what
    the production pipeline passes to `Labeler.label`). The labeler
    proposes a candidate label; the judge then decides whether the
    candidate is semantically equivalent to `ground_truth_label`.

    `domain_context` is a one-line operator-readable framing of the
    workflow ("M5 retail demand forecasting"). The labeler doesn't see
    this directly, but the judge does — it grounds the verdict.

    `dominant_hint` records the failure-mode tag that drives the
    cluster's identity. Used in tests + per-bucket agreement slicing
    in the runner aggregate.
    """

    cluster_id: str
    domain_context: str
    member_signatures: tuple[str, ...]
    ground_truth_label: str
    dominant_hint: Literal[
        "under-forecast",
        "over-forecast",
        "zero-inflated",
        "high-variance",
        "flat-prediction",
        "mixed",
    ]
    notes: str | None = None


_M5_DOMAIN = "M5 retail demand forecasting (Walmart sales, 28-day horizon)"


LABELED_CLUSTER_CASES: tuple[LabeledClusterCase, ...] = (
    LabeledClusterCase(
        cluster_id="ca-snack-weekend-under",
        domain_context=_M5_DOMAIN,
        member_signatures=(
            "FOODS_3_001_CA_1_validation [FOODS/FOODS_3 @ CA/CA_1] "
            "rmsse=2.43 peak -8.20 day 5 hints=[under-forecast,high-variance]",
            "FOODS_3_002_CA_1_validation [FOODS/FOODS_3 @ CA/CA_1] "
            "rmsse=2.71 peak -9.50 day 12 hints=[under-forecast]",
            "FOODS_3_007_CA_2_validation [FOODS/FOODS_3 @ CA/CA_2] "
            "rmsse=2.18 peak -7.10 day 6 hints=[under-forecast,high-variance]",
            "FOODS_3_011_CA_2_validation [FOODS/FOODS_3 @ CA/CA_2] "
            "rmsse=2.55 peak -7.80 day 13 hints=[under-forecast]",
            "FOODS_3_014_CA_3_validation [FOODS/FOODS_3 @ CA/CA_3] "
            "rmsse=2.32 peak -6.90 day 5 hints=[under-forecast]",
        ),
        ground_truth_label="CA snack under-forecasts on weekends",
        dominant_hint="under-forecast",
        notes=(
            "Days 5/6 (Sat/Sun) and 12/13 (next Sat/Sun) align — weekly "
            "spike pattern; CA stores; FOODS_3 (snacks). The shared mode "
            "is weekend under-forecasting on California snack SKUs."
        ),
    ),
    LabeledClusterCase(
        cluster_id="tx-cleaning-over",
        domain_context=_M5_DOMAIN,
        member_signatures=(
            "HOUSEHOLD_1_104_TX_1_validation [HOUSEHOLD/HOUSEHOLD_1 @ TX/TX_1] "
            "rmsse=1.98 peak +5.40 day 3 hints=[over-forecast]",
            "HOUSEHOLD_1_127_TX_1_validation [HOUSEHOLD/HOUSEHOLD_1 @ TX/TX_1] "
            "rmsse=2.15 peak +6.80 day 9 hints=[over-forecast,flat-prediction]",
            "HOUSEHOLD_1_133_TX_2_validation [HOUSEHOLD/HOUSEHOLD_1 @ TX/TX_2] "
            "rmsse=2.04 peak +5.90 day 4 hints=[over-forecast]",
            "HOUSEHOLD_1_141_TX_2_validation [HOUSEHOLD/HOUSEHOLD_1 @ TX/TX_2] "
            "rmsse=1.87 peak +4.80 day 11 hints=[over-forecast]",
        ),
        ground_truth_label="TX cleaning supply over-forecasts",
        dominant_hint="over-forecast",
        notes=(
            "HOUSEHOLD_1 in M5 is broadly cleaning supplies + paper goods. "
            "All members are TX stores, all over-forecast. Mid-week peaks "
            "(day 3/4/9/11) — no weekend pattern."
        ),
    ),
    LabeledClusterCase(
        cluster_id="wi-hobbies-zero-inflated",
        domain_context=_M5_DOMAIN,
        member_signatures=(
            "HOBBIES_2_005_WI_1_validation [HOBBIES/HOBBIES_2 @ WI/WI_1] "
            "rmsse=3.21 peak -3.40 day 14 hints=[zero-inflated,under-forecast]",
            "HOBBIES_2_012_WI_1_validation [HOBBIES/HOBBIES_2 @ WI/WI_1] "
            "rmsse=3.05 peak -2.90 day 21 hints=[zero-inflated]",
            "HOBBIES_2_018_WI_2_validation [HOBBIES/HOBBIES_2 @ WI/WI_2] "
            "rmsse=2.88 peak -3.20 day 14 hints=[zero-inflated]",
            "HOBBIES_2_021_WI_3_validation [HOBBIES/HOBBIES_2 @ WI/WI_3] "
            "rmsse=3.42 peak -3.80 day 28 hints=[zero-inflated,under-forecast]",
        ),
        ground_truth_label="WI slow-moving hobby zero-inflated demand",
        dominant_hint="zero-inflated",
        notes=(
            "Zero-inflated hint = many zero-sale days punctuated by 2-4 "
            "unit spikes. Sparse-demand SKUs (HOBBIES_2 = small accessories). "
            "All three WI stores represented."
        ),
    ),
    LabeledClusterCase(
        cluster_id="produce-flat-high-var",
        domain_context=_M5_DOMAIN,
        member_signatures=(
            "FOODS_1_023_CA_1_validation [FOODS/FOODS_1 @ CA/CA_1] "
            "rmsse=2.62 peak +4.20 day 7 hints=[flat-prediction,high-variance]",
            "FOODS_1_031_TX_1_validation [FOODS/FOODS_1 @ TX/TX_1] "
            "rmsse=2.45 peak -4.50 day 12 hints=[flat-prediction,high-variance]",
            "FOODS_1_044_WI_1_validation [FOODS/FOODS_1 @ WI/WI_1] "
            "rmsse=2.78 peak +5.10 day 19 hints=[flat-prediction,high-variance]",
            "FOODS_1_052_CA_2_validation [FOODS/FOODS_1 @ CA/CA_2] "
            "rmsse=2.51 peak -4.80 day 22 hints=[flat-prediction,high-variance]",
        ),
        ground_truth_label="Flat predictions on volatile fresh produce",
        dominant_hint="flat-prediction",
        notes=(
            "FOODS_1 = fresh produce in M5. Predictions are flat-lined "
            "(low variance) but actuals swing high — the model failed to "
            "learn the high-variance pattern. Spans all three states; "
            "category is the unifier."
        ),
    ),
    LabeledClusterCase(
        cluster_id="snack-holiday-spike-under",
        domain_context=_M5_DOMAIN,
        member_signatures=(
            "FOODS_3_088_TX_3_validation [FOODS/FOODS_3 @ TX/TX_3] "
            "rmsse=4.12 peak -18.60 day 21 hints=[under-forecast,high-variance]",
            "FOODS_3_091_CA_4_validation [FOODS/FOODS_3 @ CA/CA_4] "
            "rmsse=3.98 peak -16.40 day 21 hints=[under-forecast,high-variance]",
            "FOODS_3_104_WI_2_validation [FOODS/FOODS_3 @ WI/WI_2] "
            "rmsse=4.32 peak -19.80 day 22 hints=[under-forecast,high-variance]",
            "FOODS_3_117_TX_2_validation [FOODS/FOODS_3 @ TX/TX_2] "
            "rmsse=3.85 peak -15.20 day 21 hints=[under-forecast,high-variance]",
        ),
        ground_truth_label="Holiday spike under-forecasts on snack foods",
        dominant_hint="under-forecast",
        notes=(
            "Day 21-22 cluster on the peak — a single-event spike (the "
            "model misses the holiday). High peak magnitudes (>15 units) "
            "+ all states represented. Generic snack/holiday failure mode."
        ),
    ),
    LabeledClusterCase(
        cluster_id="paper-goods-promo-over",
        domain_context=_M5_DOMAIN,
        member_signatures=(
            "HOUSEHOLD_2_201_CA_1_validation [HOUSEHOLD/HOUSEHOLD_2 @ CA/CA_1] "
            "rmsse=2.31 peak +9.40 day 8 hints=[over-forecast,flat-prediction]",
            "HOUSEHOLD_2_215_CA_2_validation [HOUSEHOLD/HOUSEHOLD_2 @ CA/CA_2] "
            "rmsse=2.18 peak +8.10 day 8 hints=[over-forecast]",
            "HOUSEHOLD_2_222_CA_3_validation [HOUSEHOLD/HOUSEHOLD_2 @ CA/CA_3] "
            "rmsse=2.42 peak +9.90 day 8 hints=[over-forecast]",
            "HOUSEHOLD_2_234_CA_4_validation [HOUSEHOLD/HOUSEHOLD_2 @ CA/CA_4] "
            "rmsse=2.05 peak +7.80 day 8 hints=[over-forecast,flat-prediction]",
        ),
        ground_truth_label="Paper goods over-forecast after promo end",
        dominant_hint="over-forecast",
        notes=(
            "HOUSEHOLD_2 = paper goods (toilet paper, towels). Day 8 peak "
            "across all 4 CA stores — looks like the model stayed elevated "
            "after a promo ended (over-predicted on the post-promo dropoff)."
        ),
    ),
    LabeledClusterCase(
        cluster_id="ca-toy-seasonal-under",
        domain_context=_M5_DOMAIN,
        member_signatures=(
            "HOBBIES_1_058_CA_1_validation [HOBBIES/HOBBIES_1 @ CA/CA_1] "
            "rmsse=3.45 peak -11.20 day 25 hints=[under-forecast,high-variance]",
            "HOBBIES_1_062_CA_2_validation [HOBBIES/HOBBIES_1 @ CA/CA_2] "
            "rmsse=3.18 peak -9.80 day 24 hints=[under-forecast,high-variance]",
            "HOBBIES_1_071_CA_3_validation [HOBBIES/HOBBIES_1 @ CA/CA_3] "
            "rmsse=3.62 peak -12.40 day 25 hints=[under-forecast,high-variance]",
            "HOBBIES_1_080_CA_4_validation [HOBBIES/HOBBIES_1 @ CA/CA_4] "
            "rmsse=3.38 peak -10.50 day 26 hints=[under-forecast,high-variance]",
        ),
        ground_truth_label="CA toy seasonal under-forecasts late horizon",
        dominant_hint="under-forecast",
        notes=(
            "HOBBIES_1 = toys. Day 24-26 peak across all CA stores. "
            "Seasonal toy spike at the end of the 28-day horizon — "
            "training data didn't include the full seasonality cycle."
        ),
    ),
    LabeledClusterCase(
        cluster_id="beverage-flat-line",
        domain_context=_M5_DOMAIN,
        member_signatures=(
            "FOODS_2_311_TX_1_validation [FOODS/FOODS_2 @ TX/TX_1] "
            "rmsse=1.85 peak +3.20 day 14 hints=[flat-prediction]",
            "FOODS_2_322_TX_2_validation [FOODS/FOODS_2 @ TX/TX_2] "
            "rmsse=1.92 peak -3.10 day 17 hints=[flat-prediction]",
            "FOODS_2_338_TX_3_validation [FOODS/FOODS_2 @ TX/TX_3] "
            "rmsse=1.88 peak +2.90 day 11 hints=[flat-prediction]",
            "FOODS_2_345_CA_1_validation [FOODS/FOODS_2 @ CA/CA_1] "
            "rmsse=1.79 peak -2.80 day 19 hints=[flat-prediction]",
        ),
        ground_truth_label="Beverage flat-line predictions miss daily swings",
        dominant_hint="flat-prediction",
        notes=(
            "FOODS_2 = beverages. Both TX and CA. Low rmsse (1.8-1.9 — "
            "predictions are close on average) but flat-prediction hint "
            "indicates the model emits constants and misses daily swings."
        ),
    ),
    LabeledClusterCase(
        cluster_id="wi-grocery-zero-weekday",
        domain_context=_M5_DOMAIN,
        member_signatures=(
            "FOODS_3_502_WI_1_validation [FOODS/FOODS_3 @ WI/WI_1] "
            "rmsse=2.84 peak -2.10 day 9 hints=[zero-inflated]",
            "FOODS_3_516_WI_1_validation [FOODS/FOODS_3 @ WI/WI_1] "
            "rmsse=2.91 peak -1.80 day 16 hints=[zero-inflated]",
            "FOODS_3_523_WI_2_validation [FOODS/FOODS_3 @ WI/WI_2] "
            "rmsse=3.02 peak -2.40 day 9 hints=[zero-inflated]",
            "FOODS_3_534_WI_3_validation [FOODS/FOODS_3 @ WI/WI_3] "
            "rmsse=2.78 peak -1.90 day 16 hints=[zero-inflated]",
        ),
        ground_truth_label="WI snack zero-inflated weekday demand",
        dominant_hint="zero-inflated",
        notes=(
            "Day 9, 16 = mid-week (Wed). Sparse weekday sales on Wisconsin "
            "snack SKUs — model is confused by the zero-runs and over-predicts "
            "small positive values on actual zero days."
        ),
    ),
    LabeledClusterCase(
        cluster_id="ca4-store-under",
        domain_context=_M5_DOMAIN,
        member_signatures=(
            "FOODS_3_204_CA_4_validation [FOODS/FOODS_3 @ CA/CA_4] "
            "rmsse=2.96 peak -8.40 day 7 hints=[under-forecast]",
            "HOUSEHOLD_1_211_CA_4_validation [HOUSEHOLD/HOUSEHOLD_1 @ CA/CA_4] "
            "rmsse=2.84 peak -7.20 day 12 hints=[under-forecast]",
            "FOODS_2_220_CA_4_validation [FOODS/FOODS_2 @ CA/CA_4] "
            "rmsse=2.75 peak -6.80 day 18 hints=[under-forecast]",
            "HOBBIES_1_232_CA_4_validation [HOBBIES/HOBBIES_1 @ CA/CA_4] "
            "rmsse=2.62 peak -5.90 day 22 hints=[under-forecast]",
        ),
        ground_truth_label="CA_4 store-wide systematic under-forecast",
        dominant_hint="under-forecast",
        notes=(
            "Mixed categories (FOODS_3, HOUSEHOLD_1, FOODS_2, HOBBIES_1) "
            "but every member is CA_4. Store-level systematic bias — "
            "store_id encoding gap, not category-driven."
        ),
    ),
    LabeledClusterCase(
        cluster_id="frozen-summer-spike",
        domain_context=_M5_DOMAIN,
        member_signatures=(
            "FOODS_2_407_TX_1_validation [FOODS/FOODS_2 @ TX/TX_1] "
            "rmsse=3.65 peak -14.20 day 6 hints=[under-forecast,high-variance]",
            "FOODS_2_412_TX_2_validation [FOODS/FOODS_2 @ TX/TX_2] "
            "rmsse=3.42 peak -12.80 day 6 hints=[under-forecast,high-variance]",
            "FOODS_2_419_TX_3_validation [FOODS/FOODS_2 @ TX/TX_3] "
            "rmsse=3.78 peak -15.40 day 7 hints=[under-forecast,high-variance]",
            "FOODS_2_428_CA_3_validation [FOODS/FOODS_2 @ CA/CA_3] "
            "rmsse=3.51 peak -13.10 day 6 hints=[under-forecast,high-variance]",
        ),
        ground_truth_label="Summer frozen dessert under-forecasts",
        dominant_hint="under-forecast",
        notes=(
            "FOODS_2 again (beverages + frozen). Day 6-7 peak with high "
            "magnitudes (>12 units) — summer-heatwave demand spike that "
            "training data didn't capture. TX heavy + one CA store."
        ),
    ),
    LabeledClusterCase(
        cluster_id="electronics-flat-tx",
        domain_context=_M5_DOMAIN,
        member_signatures=(
            "HOBBIES_1_603_TX_1_validation [HOBBIES/HOBBIES_1 @ TX/TX_1] "
            "rmsse=2.04 peak +1.80 day 14 hints=[flat-prediction]",
            "HOBBIES_1_614_TX_2_validation [HOBBIES/HOBBIES_1 @ TX/TX_2] "
            "rmsse=1.96 peak -1.60 day 17 hints=[flat-prediction]",
            "HOBBIES_1_628_TX_3_validation [HOBBIES/HOBBIES_1 @ TX/TX_3] "
            "rmsse=2.11 peak +2.00 day 21 hints=[flat-prediction]",
        ),
        ground_truth_label="TX hobby flat predictions across stores",
        dominant_hint="flat-prediction",
        notes=(
            "HOBBIES_1 = toys / electronics. All TX. Tight peaks (~2 "
            "units), flat-prediction tag — model emits a constant."
        ),
    ),
    LabeledClusterCase(
        cluster_id="ca3-paper-goods-over",
        domain_context=_M5_DOMAIN,
        member_signatures=(
            "HOUSEHOLD_2_705_CA_3_validation [HOUSEHOLD/HOUSEHOLD_2 @ CA/CA_3] "
            "rmsse=2.47 peak +6.80 day 11 hints=[over-forecast]",
            "HOUSEHOLD_2_712_CA_3_validation [HOUSEHOLD/HOUSEHOLD_2 @ CA/CA_3] "
            "rmsse=2.31 peak +5.90 day 11 hints=[over-forecast]",
            "HOUSEHOLD_2_719_CA_3_validation [HOUSEHOLD/HOUSEHOLD_2 @ CA/CA_3] "
            "rmsse=2.62 peak +7.20 day 12 hints=[over-forecast,flat-prediction]",
            "HOUSEHOLD_2_724_CA_3_validation [HOUSEHOLD/HOUSEHOLD_2 @ CA/CA_3] "
            "rmsse=2.18 peak +5.40 day 11 hints=[over-forecast]",
        ),
        ground_truth_label="CA_3 paper goods store-localised over-forecast",
        dominant_hint="over-forecast",
        notes=(
            "All CA_3, all HOUSEHOLD_2. Day 11-12 — store-localised + "
            "category-localised over-forecasting. Different from cluster "
            "`paper-goods-promo-over` (which spans all CA stores on day 8)."
        ),
    ),
    LabeledClusterCase(
        cluster_id="back-to-school-under",
        domain_context=_M5_DOMAIN,
        member_signatures=(
            "HOBBIES_1_801_CA_1_validation [HOBBIES/HOBBIES_1 @ CA/CA_1] "
            "rmsse=4.05 peak -22.40 day 14 hints=[under-forecast,high-variance]",
            "HOBBIES_1_812_TX_1_validation [HOBBIES/HOBBIES_1 @ TX/TX_1] "
            "rmsse=3.82 peak -19.80 day 14 hints=[under-forecast,high-variance]",
            "HOBBIES_1_823_WI_1_validation [HOBBIES/HOBBIES_1 @ WI/WI_1] "
            "rmsse=4.18 peak -23.10 day 15 hints=[under-forecast,high-variance]",
            "HOBBIES_1_834_CA_2_validation [HOBBIES/HOBBIES_1 @ CA/CA_2] "
            "rmsse=3.95 peak -20.50 day 14 hints=[under-forecast,high-variance]",
        ),
        ground_truth_label="Back-to-school spike under-forecasts on hobby items",
        dominant_hint="under-forecast",
        notes=(
            "Day 14-15 peak, high magnitude (>19 units), all states. "
            "Model misses the back-to-school surge mid-horizon."
        ),
    ),
    LabeledClusterCase(
        cluster_id="weekend-grocery-high-var",
        domain_context=_M5_DOMAIN,
        member_signatures=(
            "FOODS_3_905_CA_1_validation [FOODS/FOODS_3 @ CA/CA_1] "
            "rmsse=2.88 peak +7.40 day 5 hints=[high-variance]",
            "FOODS_3_911_TX_1_validation [FOODS/FOODS_3 @ TX/TX_1] "
            "rmsse=2.74 peak -6.80 day 6 hints=[high-variance]",
            "FOODS_3_918_WI_1_validation [FOODS/FOODS_3 @ WI/WI_1] "
            "rmsse=2.92 peak +7.10 day 12 hints=[high-variance]",
            "FOODS_3_924_CA_2_validation [FOODS/FOODS_3 @ CA/CA_2] "
            "rmsse=2.81 peak -6.50 day 13 hints=[high-variance]",
        ),
        ground_truth_label="Weekend snack high-variance prediction errors",
        dominant_hint="high-variance",
        notes=(
            "Mixed signs (+/-) — not a directional bias, just high "
            "variance. Days 5/6 + 12/13 align on weekends. All three "
            "states. Peak magnitudes ~7 units — large but symmetric."
        ),
    ),
    LabeledClusterCase(
        cluster_id="black-friday-toy-over",
        domain_context=_M5_DOMAIN,
        member_signatures=(
            "HOBBIES_2_1001_TX_1_validation [HOBBIES/HOBBIES_2 @ TX/TX_1] "
            "rmsse=2.94 peak +12.80 day 26 hints=[over-forecast,high-variance]",
            "HOBBIES_2_1015_TX_2_validation [HOBBIES/HOBBIES_2 @ TX/TX_2] "
            "rmsse=2.81 peak +11.40 day 26 hints=[over-forecast,high-variance]",
            "HOBBIES_2_1024_TX_3_validation [HOBBIES/HOBBIES_2 @ TX/TX_3] "
            "rmsse=3.12 peak +13.60 day 27 hints=[over-forecast,high-variance]",
        ),
        ground_truth_label="Post-Black-Friday hobby over-forecast in TX",
        dominant_hint="over-forecast",
        notes=(
            "Day 26-27, all TX, HOBBIES_2 (small accessories). Over-forecast "
            "right after a Black-Friday peak — model carried elevated "
            "predictions into post-event days."
        ),
    ),
    LabeledClusterCase(
        cluster_id="bakery-zero-weekday",
        domain_context=_M5_DOMAIN,
        member_signatures=(
            "FOODS_1_1112_CA_1_validation [FOODS/FOODS_1 @ CA/CA_1] "
            "rmsse=3.18 peak -2.80 day 9 hints=[zero-inflated,under-forecast]",
            "FOODS_1_1124_CA_2_validation [FOODS/FOODS_1 @ CA/CA_2] "
            "rmsse=3.05 peak -2.40 day 16 hints=[zero-inflated]",
            "FOODS_1_1138_TX_1_validation [FOODS/FOODS_1 @ TX/TX_1] "
            "rmsse=3.34 peak -3.10 day 9 hints=[zero-inflated,under-forecast]",
            "FOODS_1_1147_TX_2_validation [FOODS/FOODS_1 @ TX/TX_2] "
            "rmsse=2.92 peak -2.60 day 23 hints=[zero-inflated]",
        ),
        ground_truth_label="Bakery zero-inflated weekday demand",
        dominant_hint="zero-inflated",
        notes=(
            "FOODS_1 = fresh / bakery. Mid-week peaks (day 9, 16, 23 — "
            "Wed/Thu). Sparse weekday demand on perishable bakery items."
        ),
    ),
    LabeledClusterCase(
        cluster_id="novel-sku-flat",
        domain_context=_M5_DOMAIN,
        member_signatures=(
            "FOODS_3_1201_CA_1_validation [FOODS/FOODS_3 @ CA/CA_1] "
            "rmsse=2.45 peak +1.80 day 14 hints=[flat-prediction]",
            "HOUSEHOLD_1_1218_TX_1_validation [HOUSEHOLD/HOUSEHOLD_1 @ TX/TX_1] "
            "rmsse=2.62 peak +2.10 day 14 hints=[flat-prediction]",
            "HOBBIES_1_1232_WI_1_validation [HOBBIES/HOBBIES_1 @ WI/WI_1] "
            "rmsse=2.54 peak +1.90 day 14 hints=[flat-prediction]",
            "FOODS_2_1248_CA_2_validation [FOODS/FOODS_2 @ CA/CA_2] "
            "rmsse=2.71 peak +2.20 day 14 hints=[flat-prediction]",
        ),
        ground_truth_label="Novel SKU flat predictions across categories",
        dominant_hint="flat-prediction",
        notes=(
            "Mixed cats (FOODS_3, HOUSEHOLD_1, HOBBIES_1, FOODS_2), mixed "
            "states. The unifier is `flat-prediction` + day 14 (mid-horizon) "
            "— novel SKU effect: not enough lag history at day 14, model "
            "defaults to a flat baseline."
        ),
    ),
    LabeledClusterCase(
        cluster_id="hh1-promo-over-balanced",
        domain_context=_M5_DOMAIN,
        member_signatures=(
            "HOUSEHOLD_1_1305_CA_1_validation [HOUSEHOLD/HOUSEHOLD_1 @ CA/CA_1] "
            "rmsse=2.18 peak +5.40 day 4 hints=[over-forecast]",
            "HOUSEHOLD_1_1316_CA_2_validation [HOUSEHOLD/HOUSEHOLD_1 @ CA/CA_2] "
            "rmsse=2.26 peak +5.80 day 4 hints=[over-forecast]",
            "HOUSEHOLD_1_1322_WI_1_validation [HOUSEHOLD/HOUSEHOLD_1 @ WI/WI_1] "
            "rmsse=2.04 peak +4.90 day 4 hints=[over-forecast]",
            "HOUSEHOLD_1_1334_WI_2_validation [HOUSEHOLD/HOUSEHOLD_1 @ WI/WI_2] "
            "rmsse=2.34 peak +6.10 day 5 hints=[over-forecast]",
        ),
        ground_truth_label="Cleaning supply over-forecast first weekend",
        dominant_hint="over-forecast",
        notes=(
            "Day 4-5 (start-of-fold weekend), CA + WI, HOUSEHOLD_1. "
            "Distinct from `tx-cleaning-over` (TX-only, mid-week). "
            "Subtler cluster — judge has to read store_id + day_offset."
        ),
    ),
    LabeledClusterCase(
        cluster_id="cross-state-flat-low-volume",
        domain_context=_M5_DOMAIN,
        member_signatures=(
            "HOBBIES_2_1408_CA_1_validation [HOBBIES/HOBBIES_2 @ CA/CA_1] "
            "rmsse=2.78 peak +1.40 day 8 hints=[flat-prediction,zero-inflated]",
            "HOBBIES_2_1419_TX_2_validation [HOBBIES/HOBBIES_2 @ TX/TX_2] "
            "rmsse=2.85 peak +1.30 day 15 hints=[flat-prediction,zero-inflated]",
            "HOBBIES_2_1431_WI_1_validation [HOBBIES/HOBBIES_2 @ WI/WI_1] "
            "rmsse=2.92 peak +1.50 day 22 hints=[flat-prediction,zero-inflated]",
            "HOBBIES_2_1444_CA_3_validation [HOBBIES/HOBBIES_2 @ CA/CA_3] "
            "rmsse=2.81 peak +1.20 day 8 hints=[flat-prediction,zero-inflated]",
        ),
        ground_truth_label="Low-volume hobby flat-line over zero days",
        dominant_hint="flat-prediction",
        notes=(
            "Combo of flat-prediction + zero-inflated on HOBBIES_2 (small "
            "accessories). Tight peaks (~1.3 units) — model emits a small "
            "constant on series that should be 0 most days."
        ),
    ),
)


def _validate_fixtures(cases: tuple[LabeledClusterCase, ...]) -> None:
    """Module-import-time invariants on the fixture set.

    Fail loudly if a fixture is malformed — these are hand-written and
    drift is hard to catch otherwise. Tests pin the same shape so a
    regression in fixtures.py shows up as a unit-test fail rather than
    a silent eval-set miscount.
    """
    if len(cases) != 20:
        raise ValueError(f"expected 20 hand-labeled cases, got {len(cases)}")
    seen_ids: set[str] = set()
    for case in cases:
        if case.cluster_id in seen_ids:
            raise ValueError(f"duplicate cluster_id: {case.cluster_id}")
        seen_ids.add(case.cluster_id)
        if len(case.member_signatures) < 3:
            raise ValueError(
                f"{case.cluster_id}: need ≥3 member signatures (got "
                f"{len(case.member_signatures)})",
            )
        if not case.ground_truth_label.strip():
            raise ValueError(f"{case.cluster_id}: empty ground_truth_label")
        if len(case.ground_truth_label) > 80:
            raise ValueError(
                f"{case.cluster_id}: ground_truth_label too long "
                f"({len(case.ground_truth_label)} chars > 80)",
            )


_validate_fixtures(LABELED_CLUSTER_CASES)


__all__ = [
    "LabeledClusterCase",
    "LABELED_CLUSTER_CASES",
]
