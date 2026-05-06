"""Tests for `analyze_m5_failures` (B3.1).

Synthetic-fixture only — no DB, no pandas, no LightGBM. We construct
`M5RunArtifacts` directly so we can pin every expected field of every
snapshot.
"""

from __future__ import annotations

import numpy as np
import pytest
from ownevo_kernel.benchmark import (
    M5FailureAnalyzerError,
    M5FailureSnapshot,
    M5RunArtifacts,
    analyze_m5_failures,
    parse_m5_series_id,
)
from ownevo_kernel.datasets import M5Fold

# ---------------------------------------------------------------------------
# Series-id parser
# ---------------------------------------------------------------------------


def test_parse_canonical_validation_id() -> None:
    parts = parse_m5_series_id("HOBBIES_1_001_CA_1_validation")
    assert parts == {
        "item_id": "HOBBIES_1_001",
        "dept_id": "HOBBIES_1",
        "cat_id": "HOBBIES",
        "store_id": "CA_1",
        "state_id": "CA",
        "suffix": "validation",
    }


def test_parse_evaluation_id() -> None:
    parts = parse_m5_series_id("FOODS_3_827_TX_2_evaluation")
    assert parts["item_id"] == "FOODS_3_827"
    assert parts["dept_id"] == "FOODS_3"
    assert parts["cat_id"] == "FOODS"
    assert parts["store_id"] == "TX_2"
    assert parts["state_id"] == "TX"
    assert parts["suffix"] == "evaluation"


def test_parse_id_without_suffix() -> None:
    parts = parse_m5_series_id("HOUSEHOLD_2_516_WI_3")
    assert parts["item_id"] == "HOUSEHOLD_2_516"
    assert parts["store_id"] == "WI_3"
    assert parts["suffix"] == ""


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "not-an-m5-id",
        "HOBBIES_1_001",  # no store
        "HOBBIES_1_001_CA",  # incomplete store
        "lowercase_1_001_CA_1_validation",  # cat must be uppercase
        "HOBBIES_1_001_CA_1_extras",  # bad suffix
    ],
)
def test_parse_rejects_malformed(bad: str) -> None:
    with pytest.raises(M5FailureAnalyzerError, match="does not match the M5 schema"):
        parse_m5_series_id(bad)


# ---------------------------------------------------------------------------
# analyze_m5_failures
# ---------------------------------------------------------------------------


def _make_artifacts(
    *,
    n_test_days: int = 6,
    series_specs: list[tuple[str, np.ndarray, np.ndarray, float, float]] | None = None,
) -> M5RunArtifacts:
    """Build an artifact directly, one row per series.

    `series_specs` items are `(series_id, actuals, predictions, weight, scale)`.
    """
    assert series_specs is not None and len(series_specs) > 0
    series_ids = tuple(s[0] for s in series_specs)
    actuals = np.stack([s[1] for s in series_specs])
    preds = np.stack([s[2] for s in series_specs])
    weights = np.array([s[3] for s in series_specs], dtype=np.float64)
    scales = np.array([s[4] for s in series_specs], dtype=np.float64)
    diff = preds - actuals
    rmsse = np.sqrt(np.mean(diff * diff, axis=1) / (scales * scales))
    rewards = {sid: float(np.exp(-r)) for sid, r in zip(series_ids, rmsse, strict=True)}
    return M5RunArtifacts(
        predictions=preds,
        actuals=actuals,
        series_ids=series_ids,
        weights=weights,
        scales=scales,
        rmse=float(np.sqrt(np.mean(diff * diff))),
        wrmsse=0.0,  # not exercised
        rewards=rewards,
    )


def test_returns_empty_for_empty_artifacts() -> None:
    arts = M5RunArtifacts(
        predictions=np.zeros((0, 5)),
        actuals=np.zeros((0, 5)),
        series_ids=(),
        weights=np.zeros((0,)),
        scales=np.zeros((0,)),
        rmse=0.0,
        wrmsse=0.0,
        rewards={},
    )
    assert analyze_m5_failures(arts) == []


def test_top_k_ordered_by_rmsse_descending() -> None:
    actuals = np.array([1.0, 1.0, 1.0, 1.0, 1.0, 1.0])
    arts = _make_artifacts(
        series_specs=[
            ("HOBBIES_1_001_CA_1_validation", actuals, actuals, 1.0, 1.0),  # perfect
            ("HOBBIES_1_002_CA_1_validation", actuals, actuals + 5.0, 1.0, 1.0),  # bad
            ("HOBBIES_1_003_CA_1_validation", actuals, actuals + 1.0, 1.0, 1.0),  # ok
        ],
    )
    snaps = analyze_m5_failures(arts, k=2)
    assert [s.series_id for s in snaps] == [
        "HOBBIES_1_002_CA_1_validation",
        "HOBBIES_1_003_CA_1_validation",
    ]
    assert snaps[0].rmsse > snaps[1].rmsse


def test_k_caps_returned_count_and_clamps_to_n_series() -> None:
    actuals = np.array([1.0, 1.0, 1.0, 1.0])
    arts = _make_artifacts(
        series_specs=[
            ("HOBBIES_1_001_CA_1_validation", actuals, actuals + 2.0, 1.0, 1.0),
        ],
    )
    snaps = analyze_m5_failures(arts, k=10)
    assert len(snaps) == 1


def test_k_must_be_positive() -> None:
    arts = _make_artifacts(
        series_specs=[
            (
                "HOBBIES_1_001_CA_1_validation",
                np.array([1.0]),
                np.array([1.0]),
                1.0,
                1.0,
            ),
        ],
    )
    with pytest.raises(M5FailureAnalyzerError, match="k must be"):
        analyze_m5_failures(arts, k=0)


def test_ties_broken_by_series_id_ascending() -> None:
    actuals = np.array([1.0, 1.0, 1.0])
    # Two series with identical RMSSE — analyzer must order by series_id ASC.
    arts = _make_artifacts(
        series_specs=[
            ("HOBBIES_1_002_CA_1_validation", actuals, actuals + 3.0, 1.0, 1.0),
            ("HOBBIES_1_001_CA_1_validation", actuals, actuals + 3.0, 1.0, 1.0),
        ],
    )
    snaps = analyze_m5_failures(arts, k=2)
    assert [s.series_id for s in snaps] == [
        "HOBBIES_1_001_CA_1_validation",
        "HOBBIES_1_002_CA_1_validation",
    ]


def test_peak_error_offset_and_signed_value() -> None:
    actuals = np.array([1.0, 1.0, 1.0, 1.0, 1.0])
    preds = np.array([1.0, 1.0, 1.0, -3.0, 1.0])  # huge under-forecast at index 3
    arts = _make_artifacts(
        series_specs=[("HOBBIES_1_001_CA_1_validation", actuals, preds, 1.0, 1.0)],
    )
    [snap] = analyze_m5_failures(arts, k=1)
    assert snap.peak_error_day_offset == 3
    assert snap.peak_error_value == pytest.approx(-4.0)
    assert snap.peak_error_day_label is None  # no fold provided


def test_peak_error_label_when_fold_provided() -> None:
    actuals = np.array([1.0, 1.0, 1.0])
    preds = np.array([1.0, 5.0, 1.0])
    arts = _make_artifacts(
        series_specs=[("HOBBIES_1_001_CA_1_validation", actuals, preds, 1.0, 1.0)],
    )
    fold = M5Fold(train=("d_1", "d_2"), validation=("d_3",), test=("d_4", "d_5", "d_6"))
    [snap] = analyze_m5_failures(arts, fold=fold, k=1)
    assert snap.peak_error_day_offset == 1
    assert snap.peak_error_day_label == "d_5"


def test_fold_size_mismatch_rejected() -> None:
    actuals = np.array([1.0, 1.0, 1.0])
    arts = _make_artifacts(
        series_specs=[("HOBBIES_1_001_CA_1_validation", actuals, actuals, 1.0, 1.0)],
    )
    fold = M5Fold(train=(), validation=(), test=("d_1", "d_2"))  # 2 != 3
    with pytest.raises(M5FailureAnalyzerError, match="day columns"):
        analyze_m5_failures(arts, fold=fold)


def test_hint_under_forecast() -> None:
    actuals = np.full(10, 10.0)
    preds = np.full(10, 5.0)  # consistently below
    arts = _make_artifacts(
        series_specs=[("HOBBIES_1_001_CA_1_validation", actuals, preds, 1.0, 1.0)],
    )
    [snap] = analyze_m5_failures(arts, k=1)
    assert "under-forecast" in snap.feature_gap_hints
    assert "over-forecast" not in snap.feature_gap_hints


def test_hint_over_forecast() -> None:
    actuals = np.full(10, 10.0)
    preds = np.full(10, 15.0)
    arts = _make_artifacts(
        series_specs=[("HOBBIES_1_001_CA_1_validation", actuals, preds, 1.0, 1.0)],
    )
    [snap] = analyze_m5_failures(arts, k=1)
    assert "over-forecast" in snap.feature_gap_hints


def test_hint_zero_inflated() -> None:
    actuals = np.array([0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 5.0, 0.0])
    preds = np.full(10, 1.0)
    arts = _make_artifacts(
        series_specs=[("HOBBIES_1_001_CA_1_validation", actuals, preds, 1.0, 1.0)],
    )
    [snap] = analyze_m5_failures(arts, k=1)
    assert "zero-inflated" in snap.feature_gap_hints


def test_hint_high_variance() -> None:
    actuals = np.array([0.0, 0.0, 100.0, 0.0, 0.0, 0.0, 50.0, 0.0, 0.0, 0.0])
    preds = np.full(10, np.mean(actuals))  # match mean, but spiky actuals
    arts = _make_artifacts(
        series_specs=[("HOBBIES_1_001_CA_1_validation", actuals, preds, 1.0, 1.0)],
    )
    [snap] = analyze_m5_failures(arts, k=1)
    assert "high-variance" in snap.feature_gap_hints


def test_hint_flat_prediction() -> None:
    rng = np.random.default_rng(0)
    actuals = rng.uniform(5.0, 15.0, size=10)
    preds = np.full(10, 10.0)  # zero std → flat
    arts = _make_artifacts(
        series_specs=[("HOBBIES_1_001_CA_1_validation", actuals, preds, 1.0, 1.0)],
    )
    [snap] = analyze_m5_failures(arts, k=1)
    assert "flat-prediction" in snap.feature_gap_hints


def test_text_signature_contains_key_fields() -> None:
    actuals = np.array([1.0, 1.0, 1.0, 1.0])
    preds = np.array([3.0, 3.0, 3.0, 3.0])
    arts = _make_artifacts(
        series_specs=[("HOBBIES_1_001_CA_1_validation", actuals, preds, 1.0, 1.0)],
    )
    [snap] = analyze_m5_failures(arts, k=1)
    sig = snap.text_signature
    assert "HOBBIES_1_001_CA_1_validation" in sig
    assert "HOBBIES" in sig
    assert "CA/CA_1" in sig
    assert "rmsse=" in sig
    assert "peak +" in sig  # over-forecast → positive peak
    assert "over-forecast" in sig


def test_snapshot_aligns_reward_with_artifact() -> None:
    actuals = np.array([1.0, 1.0, 1.0])
    preds = np.array([1.5, 1.5, 1.5])
    arts = _make_artifacts(
        series_specs=[("HOBBIES_1_001_CA_1_validation", actuals, preds, 1.0, 1.0)],
    )
    [snap] = analyze_m5_failures(arts, k=1)
    assert snap.reward == pytest.approx(arts.rewards[snap.series_id])
    # Reward = exp(-rmsse), so rmsse > 0 implies reward < 1.
    assert snap.reward < 1.0
    assert snap.rmsse > 0.0


def test_returns_typed_snapshots() -> None:
    actuals = np.array([1.0, 2.0, 3.0])
    preds = np.array([1.5, 2.5, 3.5])
    arts = _make_artifacts(
        series_specs=[("HOBBIES_1_001_CA_1_validation", actuals, preds, 1.0, 1.0)],
    )
    [snap] = analyze_m5_failures(arts, k=1)
    assert isinstance(snap, M5FailureSnapshot)
    assert snap.mean_actual == pytest.approx(2.0)
    assert snap.mean_predicted == pytest.approx(2.5)
