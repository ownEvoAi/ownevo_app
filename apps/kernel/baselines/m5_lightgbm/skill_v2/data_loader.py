"""
---
id: m5.baseline.v2.data_loader
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
from datetime import date

import numpy as np

from ownevo_kernel.datasets import M5Catalog, M5Fold

from .. import RawSeriesData

_METADATA_COLS = ("item_id", "dept_id", "cat_id", "store_id", "state_id")


def load(
    catalog: M5Catalog,
    fold: M5Fold,
    *,
    series_ids: list[str] | None = None,
) -> RawSeriesData:
    """Read sales + calendar; slice to train/val/test fold; surface
    per-series metadata and per-day day-of-week for the LightGBM features.

    `dollar_volume` is None in v1 — joining weekly prices through the
    calendar's `wm_yr_wk` index is a feature-engineering ladder; the
    LightGBM model already gets non-trivial lift from lag-28 + DOW +
    cat_id without it. Wired in a later iteration.
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
    metadata: list[dict[str, str]] = []

    with catalog.sales_train.path.open() as f:
        reader = csv.reader(f)
        header = next(reader)
        col_idx = {c: i for i, c in enumerate(header)}
        id_idx = col_idx["id"]
        meta_idx = {c: col_idx[c] for c in _METADATA_COLS if c in col_idx}
        for row in reader:
            sid = row[id_idx]
            if requested is not None and sid not in requested:
                continue
            keep_ids.append(sid)
            train_rows.append([float(row[i]) for i in train_idx])
            val_rows.append([float(row[i]) for i in val_idx])
            test_rows.append([float(row[i]) for i in test_idx])
            metadata.append({c: row[meta_idx[c]] for c in meta_idx})

    if requested is not None:
        order = {sid: i for i, sid in enumerate(series_ids or ())}
        sort_key = sorted(range(len(keep_ids)), key=lambda j: order[keep_ids[j]])
        keep_ids = [keep_ids[j] for j in sort_key]
        train_rows = [train_rows[j] for j in sort_key]
        val_rows = [val_rows[j] for j in sort_key]
        test_rows = [test_rows[j] for j in sort_key]
        metadata = [metadata[j] for j in sort_key]

    val_dow, test_dow = _calendar_dow(catalog, fold)

    return RawSeriesData(
        series_ids=keep_ids,
        train_actuals=np.asarray(train_rows, dtype=np.float64),
        validation_actuals=np.asarray(val_rows, dtype=np.float64),
        test_actuals=np.asarray(test_rows, dtype=np.float64),
        dollar_volume=None,
        metadata=metadata,
        val_dow=val_dow,
        test_dow=test_dow,
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


def _calendar_dow(catalog: M5Catalog, fold: M5Fold) -> tuple[np.ndarray, np.ndarray]:
    """Map each `d_N` column in the val + test windows to weekday 0..6.

    Reads `calendar.csv` once, builds a dict `d_N → weekday`, then looks
    up the fold columns. Falls back to a position-based DOW (0 for the
    first val day, etc.) when the calendar row for a `d_N` is missing —
    which is the case for the synthetic test fixtures that ship a
    minimal calendar to keep tests fast.
    """
    d_to_dow: dict[str, int] = {}
    with catalog.calendar.path.open() as f:
        reader = csv.DictReader(f)
        for row in reader:
            d_label = row.get("d", "")
            iso = row.get("date", "")
            if not d_label or not iso:
                continue
            try:
                y, m, day = (int(p) for p in iso.split("-"))
                d_to_dow[d_label] = date(y, m, day).weekday()
            except ValueError:
                continue

    val_dow = np.asarray(
        [d_to_dow.get(c, _fallback_dow(c)) for c in fold.validation],
        dtype=np.int64,
    )
    test_dow = np.asarray(
        [d_to_dow.get(c, _fallback_dow(c)) for c in fold.test],
        dtype=np.int64,
    )
    return val_dow, test_dow


def _fallback_dow(d_label: str) -> int:
    """Position-based DOW for synthetic fixtures missing calendar rows.

    Real M5 always has the row; this exists so tests with a 1-row
    calendar fixture still produce well-formed feature vectors.
    """
    if d_label.startswith("d_"):
        try:
            return int(d_label[2:]) % 7
        except ValueError:
            return 0
    return 0
