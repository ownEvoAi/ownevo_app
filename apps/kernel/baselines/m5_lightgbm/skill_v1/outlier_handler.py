"""
---
id: m5.baseline.v1.outlier_handler
kind: python
created_by: bootstrap-2026-W2.6
capability_tags:
  - m5
  - baseline
  - outlier_handler
retention:
  stateless: true
---
"""

from __future__ import annotations

import numpy as np

from .. import RawSeriesData

# Cap each per-series training cell at the 99th percentile of that series'
# own training window. Holiday spikes are real demand; v1 is conservative
# (single-pass, per-series). The agent will likely replace this with a
# domain-aware imputer.
_CLIP_PERCENTILE = 99.0


def handle(raw: RawSeriesData) -> RawSeriesData:
    """Clip per-series training spikes; drop zero-scale series.

    Validation and test arrays are NOT modified — only training-time
    data is cleaned. Held-out evaluation sees the raw actuals. Metadata
    + dollar_volume are filtered in lockstep so downstream
    feature-engineering aligns with the kept rows.

    Filter contract: every output series satisfies
    ``np.mean(np.diff(train_actuals)**2) > 0`` — i.e. WRMSSE-scale > 0,
    matching what ``_compute_weights_and_scales`` enforces. A naive
    ``np.std > 0`` check coincides on dense fixtures but diverges on
    real M5 because the 99th-percentile clip below can collapse a
    sparse-demand series (~1% non-zero days) to all zeros — its
    99th-percentile *is* zero, and ``np.minimum(series, 0)`` zeros
    every cell. We skip the clip when the cap is non-positive and
    verify scale > 0 post-clip as a defense.
    """
    if raw.train_actuals.size == 0:
        return raw

    train = raw.train_actuals.astype(np.float64, copy=False)

    # Conditional clip: only when the per-series 99th percentile is > 0.
    # For sparse-demand series whose 99th percentile is 0, clipping
    # would zero the entire row and break the scale > 0 invariant.
    clip_caps = np.percentile(train, _CLIP_PERCENTILE, axis=1, keepdims=True)
    can_clip = clip_caps[:, 0] > 0
    train_clipped = train.copy()
    if can_clip.any():
        train_clipped[can_clip] = np.minimum(train[can_clip], clip_caps[can_clip])

    # Filter by post-clip RMSSE scale > 0 (the strict M5 requirement
    # _compute_weights_and_scales enforces). Catches:
    #   (a) raw all-flat series (scale=0 pre-clip)
    #   (b) any series the conditional-clip path failed to skip
    diffs = np.diff(train_clipped, axis=1)
    scales = np.sqrt(np.mean(diffs * diffs, axis=1))
    keep_mask = scales > 0
    kept_indices = np.where(keep_mask)[0].tolist()

    series_ids = [raw.series_ids[i] for i in kept_indices]
    metadata = [raw.metadata[i] for i in kept_indices]
    dollar_volume = (
        raw.dollar_volume[keep_mask] if raw.dollar_volume is not None else None
    )

    return RawSeriesData(
        series_ids=series_ids,
        train_actuals=train_clipped[keep_mask],
        validation_actuals=raw.validation_actuals[keep_mask],
        test_actuals=raw.test_actuals[keep_mask],
        dollar_volume=dollar_volume,
        metadata=metadata,
        val_dow=raw.val_dow,
        test_dow=raw.test_dow,
    )
