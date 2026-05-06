"""M5 failure analyzer — top-k worst-predicted series with structured context (B3.1).

Inputs:
  - `M5RunArtifacts` produced by `M5BenchmarkRunner.run()` (predictions + actuals
    + scales + per-series rewards aligned on `series_ids`).
  - `M5Fold` describing which day columns the test fold ran on, so failure
    snapshots can name the calendar window the agent should look at.

Output:
  - Ranked list of `M5FailureSnapshot`s, worst first, each carrying:
      * Parsed M5 hierarchy (item / dept / cat / store / state) — these are
        deterministically encoded in `series_id` per the M5 schema:
        `{cat}_{dept_n}_{item_n}_{state}_{store_n}_{validation|evaluation}`.
        We parse rather than re-read `sales_train_*.csv` so the analyzer
        stays pandas-free and runs in milliseconds over the full catalog.
      * Per-series RMSSE + reward (already in the artifact).
      * `peak_error_day_offset` + signed `peak_error_value` so the LLM
        cluster-labeler can say "underforecast on day 5" without opening
        the array.
      * `feature_gap_hints` — short string tags derived from numerical
        patterns (`under-forecast`, `over-forecast`, `zero-inflated`,
        `high-variance`). Cheap signal for clustering — not feature
        importance, just descriptive.
      * `text_signature` — one-line human-readable summary used as the
        embedding input for B3.2.

Pure-numpy + stdlib. No DB, no pandas, no LLM call.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import numpy as np

from ..datasets.m5_metric import M5Fold
from .m5 import M5RunArtifacts

# Canonical M5 series-id pattern. Captures the 5 hierarchy levels and
# the fold suffix. Suffix is optional — a few datasets ship without it.
#
# Examples (all valid):
#   HOBBIES_1_001_CA_1_validation
#   FOODS_3_827_TX_2_evaluation
#   HOUSEHOLD_2_516_WI_3
_SERIES_ID_RE = re.compile(
    r"^(?P<cat_id>[A-Z]+)_(?P<dept_n>\d+)_(?P<item_n>\d+)"
    r"_(?P<state_id>[A-Z]+)_(?P<store_n>\d+)"
    r"(?:_(?P<suffix>validation|evaluation))?$"
)


class M5FailureAnalyzerError(ValueError):
    """Raised on invariant violations the runner should have caught.

    The analyzer trusts `M5RunArtifacts` shape contracts (validated in
    `M5BenchmarkRunner._validate_pipeline_output`). This error fires only
    if a caller hands the analyzer hand-built artifacts that violate them.
    """


@dataclass(frozen=True)
class M5FailureSnapshot:
    """One worst-predicted series with structured context for clustering.

    `text_signature` is the single-line embedding input for B3.2. Format:
        "<series_id> [<cat>/<dept> @ <state>/<store>] rmsse=<x.xx>
         peak <±value> day <n> hints=[<tag>,<tag>]"
    """

    series_id: str
    item_id: str
    dept_id: str
    cat_id: str
    store_id: str
    state_id: str
    rmsse: float
    reward: float
    mean_actual: float
    mean_predicted: float
    peak_error_day_offset: int
    """0-indexed offset into the test fold (i.e., index into M5Fold.test)."""
    peak_error_day_label: str | None
    """The matching `d_<n>` column label from `M5Fold.test`, when fold passed."""
    peak_error_value: float
    """Signed: predicted - actual. Positive = over-forecast; negative = under."""
    feature_gap_hints: tuple[str, ...] = field(default_factory=tuple)
    text_signature: str = ""


def parse_m5_series_id(series_id: str) -> dict[str, str]:
    """Decode the M5 series-id hierarchy. Pure string parsing.

    Returns a dict with keys:
      item_id, dept_id, cat_id, store_id, state_id, suffix.
    `suffix` is "validation" / "evaluation" / "" (empty when omitted).

    Raises `M5FailureAnalyzerError` on a malformed id — the analyzer
    refuses to surface a snapshot it can't ground in the M5 hierarchy.
    """
    m = _SERIES_ID_RE.match(series_id)
    if m is None:
        raise M5FailureAnalyzerError(
            f"series_id {series_id!r} does not match the M5 schema "
            "(expected `<CAT>_<dept_n>_<item_n>_<STATE>_<store_n>"
            "[_validation|_evaluation]`).",
        )
    cat = m["cat_id"]
    dept = f"{cat}_{m['dept_n']}"
    item = f"{dept}_{m['item_n']}"
    state = m["state_id"]
    store = f"{state}_{m['store_n']}"
    return {
        "item_id": item,
        "dept_id": dept,
        "cat_id": cat,
        "store_id": store,
        "state_id": state,
        "suffix": m["suffix"] or "",
    }


def analyze_m5_failures(
    artifacts: M5RunArtifacts,
    *,
    fold: M5Fold | None = None,
    k: int = 10,
) -> list[M5FailureSnapshot]:
    """Return the `k` worst-predicted series, ranked by RMSSE descending.

    `fold` is optional — when provided, the snapshot's
    `peak_error_day_label` carries the matching `d_<n>` column name so
    the agent can cross-reference calendar events. Without `fold` only
    the integer offset is reported.

    Ties on RMSSE are broken by `series_id` ASC for determinism.
    """
    if k <= 0:
        raise M5FailureAnalyzerError(f"k must be >= 1; got {k}")

    n_series = len(artifacts.series_ids)
    if n_series == 0:
        return []

    n_test_days = artifacts.predictions.shape[1]
    if fold is not None and len(fold.test) != n_test_days:
        raise M5FailureAnalyzerError(
            f"fold.test has {len(fold.test)} day columns but artifacts have "
            f"{n_test_days} test-fold days — caller passed a mismatched fold.",
        )

    diff = artifacts.predictions - artifacts.actuals  # signed: pred - actual
    per_series_mse = np.mean(diff * diff, axis=1)
    rmsse_per_series = np.sqrt(per_series_mse / (artifacts.scales * artifacts.scales))

    # Rank: highest RMSSE first; secondary key = series_id ASC for stability.
    order = sorted(
        range(n_series),
        key=lambda i: (-rmsse_per_series[i], artifacts.series_ids[i]),
    )

    snapshots: list[M5FailureSnapshot] = []
    for i in order[:k]:
        sid = artifacts.series_ids[i]
        parts = parse_m5_series_id(sid)

        actual_row = artifacts.actuals[i]
        pred_row = artifacts.predictions[i]
        diff_row = diff[i]
        # `argmax(|diff|)` finds the day with the worst miss; then we re-read
        # the signed value so the sign communicates over- vs under-forecast.
        peak_offset = int(np.argmax(np.abs(diff_row)))
        peak_signed = float(diff_row[peak_offset])
        peak_label = fold.test[peak_offset] if fold is not None else None

        hints = _feature_gap_hints(actual_row, pred_row, diff_row)

        rmsse_val = float(rmsse_per_series[i])
        reward_val = float(artifacts.rewards.get(sid, np.exp(-rmsse_val)))
        mean_actual = float(np.mean(actual_row))
        mean_pred = float(np.mean(pred_row))

        sig = _text_signature(
            series_id=sid,
            cat=parts["cat_id"],
            dept=parts["dept_id"],
            state=parts["state_id"],
            store=parts["store_id"],
            rmsse=rmsse_val,
            peak_signed=peak_signed,
            peak_offset=peak_offset,
            hints=hints,
        )

        snapshots.append(
            M5FailureSnapshot(
                series_id=sid,
                item_id=parts["item_id"],
                dept_id=parts["dept_id"],
                cat_id=parts["cat_id"],
                store_id=parts["store_id"],
                state_id=parts["state_id"],
                rmsse=rmsse_val,
                reward=reward_val,
                mean_actual=mean_actual,
                mean_predicted=mean_pred,
                peak_error_day_offset=peak_offset,
                peak_error_day_label=peak_label,
                peak_error_value=peak_signed,
                feature_gap_hints=hints,
                text_signature=sig,
            )
        )
    return snapshots


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _feature_gap_hints(
    actual_row: np.ndarray,
    pred_row: np.ndarray,
    diff_row: np.ndarray,
) -> tuple[str, ...]:
    """Cheap descriptive tags for the embedding input. Order is stable.

    Tags surface bias and structure that show up in real M5 misses:
      under-forecast      — model systematically below actuals
      over-forecast       — model systematically above actuals
      zero-inflated       — actual is zero on >=70% of test days (intermittent)
      high-variance       — actual std/mean > 1.5 (spiky series)
      flat-prediction     — predicted std < 0.05 * actual mean (model gave up)
    """
    hints: list[str] = []
    n = max(diff_row.size, 1)
    mean_diff = float(np.mean(diff_row))
    actual_mean = float(np.mean(actual_row))
    actual_std = float(np.std(actual_row))
    pred_std = float(np.std(pred_row))
    zero_share = float(np.mean(actual_row == 0.0))

    # Bias: only flag when |mean_diff| is meaningful relative to the actuals.
    bias_threshold = max(0.5, 0.25 * actual_mean)
    if mean_diff <= -bias_threshold:
        hints.append("under-forecast")
    elif mean_diff >= bias_threshold:
        hints.append("over-forecast")

    if zero_share >= 0.7:
        hints.append("zero-inflated")
    if actual_mean > 0 and (actual_std / actual_mean) > 1.5:
        hints.append("high-variance")
    if actual_mean > 0 and pred_std < 0.05 * actual_mean:
        hints.append("flat-prediction")

    # `n` is unused but kept to make the windowing intent explicit; if a
    # caller adds rolling-window hints later the signature won't change.
    del n
    return tuple(hints)


def _text_signature(
    *,
    series_id: str,
    cat: str,
    dept: str,
    state: str,
    store: str,
    rmsse: float,
    peak_signed: float,
    peak_offset: int,
    hints: tuple[str, ...],
) -> str:
    sign = "+" if peak_signed >= 0 else "-"
    hints_str = ",".join(hints) if hints else "none"
    return (
        f"{series_id} [{cat}/{dept} @ {state}/{store}] "
        f"rmsse={rmsse:.2f} peak {sign}{abs(peak_signed):.2f} day {peak_offset} "
        f"hints=[{hints_str}]"
    )
