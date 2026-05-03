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

# Drop series with this little training movement — they generate
# zero-scale RMSSE denominators and sink the WRMSSE aggregate.
_MIN_TRAIN_STD = 1e-6

# Cap each per-series training cell at the 99th percentile of that series'
# own training window. Holiday spikes are real demand; v1 is conservative
# (single-pass, per-series). The agent will likely replace this with a
# domain-aware imputer.
_CLIP_PERCENTILE = 99.0


def handle(raw: RawSeriesData) -> RawSeriesData:
    """Clip per-series training spikes; drop zero-movement series.

    The validation/test arrays are NOT modified — only training-time data
    is cleaned. Held-out evaluation sees the raw actuals.
    """
    if raw.train_actuals.size == 0:
        return raw

    keep_mask = np.std(raw.train_actuals, axis=1) > _MIN_TRAIN_STD
    train = raw.train_actuals[keep_mask]
    val = raw.validation_actuals[keep_mask]
    test = raw.test_actuals[keep_mask]
    series_ids = [sid for sid, k in zip(raw.series_ids, keep_mask, strict=True) if k]
    dollar_volume = (
        raw.dollar_volume[keep_mask] if raw.dollar_volume is not None else None
    )

    if train.size == 0:
        return RawSeriesData(
            series_ids=series_ids,
            train_actuals=train,
            validation_actuals=val,
            test_actuals=test,
            dollar_volume=dollar_volume,
        )

    clip_caps = np.percentile(train, _CLIP_PERCENTILE, axis=1, keepdims=True)
    train_clipped = np.minimum(train, clip_caps)

    return RawSeriesData(
        series_ids=series_ids,
        train_actuals=train_clipped,
        validation_actuals=val,
        test_actuals=test,
        dollar_volume=dollar_volume,
    )
