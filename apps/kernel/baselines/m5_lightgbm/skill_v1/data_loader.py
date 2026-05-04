"""
---
id: m5.baseline.v1.data_loader
kind: python
created_by: bootstrap-2026-W2.6
capability_tags:
  - m5
  - baseline
  - data_loader
retention:
  stateless: true
---
"""

from __future__ import annotations

import csv

import numpy as np
from ownevo_kernel.datasets import M5Catalog, M5Fold

from .. import RawSeriesData


def load(
    catalog: M5Catalog,
    fold: M5Fold,
    *,
    series_ids: list[str] | None = None,
) -> RawSeriesData:
    """Read sales_train_validation.csv and slice to the train/val/test fold.

    Series order is preserved from the CSV. When `series_ids` is given,
    the loader returns rows in the requested order — not the CSV order —
    so the gate's regression-suite re-scoring lines up with prior runs.

    `dollar_volume` is None in v1 — joining weekly prices through the
    calendar's `wm_yr_wk` index is feasible but pandas-friendly; the
    LightGBM iteration will pull it in. Uniform weights are used for
    WRMSSE in the meantime.
    """
    if series_ids is not None and len(series_ids) == 0:
        raise ValueError(
            "series_ids must be None (load all series) or a non-empty list; "
            "got an empty list — this would produce a 1D ndarray downstream."
        )
    train_idx, val_idx, test_idx = _fold_column_indices(catalog, fold)
    requested: set[str] | None = set(series_ids) if series_ids is not None else None

    keep_ids: list[str] = []
    train_rows: list[list[float]] = []
    val_rows: list[list[float]] = []
    test_rows: list[list[float]] = []

    with catalog.sales_train.path.open() as f:
        reader = csv.reader(f)
        header = next(reader)
        id_col = header.index("id")
        for row in reader:
            sid = row[id_col]
            if requested is not None and sid not in requested:
                continue
            keep_ids.append(sid)
            train_rows.append([float(row[i]) for i in train_idx])
            val_rows.append([float(row[i]) for i in val_idx])
            test_rows.append([float(row[i]) for i in test_idx])

    if requested is not None:
        order = {sid: i for i, sid in enumerate(series_ids or ())}
        sort_key = sorted(range(len(keep_ids)), key=lambda j: order[keep_ids[j]])
        keep_ids = [keep_ids[j] for j in sort_key]
        train_rows = [train_rows[j] for j in sort_key]
        val_rows = [val_rows[j] for j in sort_key]
        test_rows = [test_rows[j] for j in sort_key]

    return RawSeriesData(
        series_ids=keep_ids,
        train_actuals=np.asarray(train_rows, dtype=np.float64),
        validation_actuals=np.asarray(val_rows, dtype=np.float64),
        test_actuals=np.asarray(test_rows, dtype=np.float64),
        dollar_volume=None,
    )


def _fold_column_indices(
    catalog: M5Catalog,
    fold: M5Fold,
) -> tuple[list[int], list[int], list[int]]:
    cols = catalog.sales_train.columns
    name_to_idx = {c: i for i, c in enumerate(cols)}
    return (
        [name_to_idx[c] for c in fold.train],
        [name_to_idx[c] for c in fold.validation],
        [name_to_idx[c] for c in fold.test],
    )
