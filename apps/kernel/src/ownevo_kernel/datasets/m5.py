"""M5 forecasting dataset loader.

The M5 dataset (Walmart hierarchical sales) is the canonical demo
workflow per CLAUDE.md. Four CSV files:

  sales_train_validation.csv  — 30,490 series × 1,913 days of unit sales
  sell_prices.csv             — weekly prices per (store_id, item_id)
  calendar.csv                — date / event / SNAP-day metadata
  sample_submission.csv       — submission template (defines the test horizon)

Scope of this loader:
  * Discover the four files in `data_dir`.
  * Surface high-level metadata (column names, row counts, date range).
  * Provide a small in-memory sample slice for fast eval setup.

What this loader DOES NOT do:
  * Pull pandas in as a kernel dep — agent-generated feature pipelines
    bring their own pandas install in the sandbox. Keeping the kernel
    pandas-free shrinks the production image and avoids pinning fights.
  * Fetch from Kaggle. The user is expected to drop the four CSVs into
    `data_dir` themselves; the loader's only job is to validate they're
    there and report what was found.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

EXPECTED_FILES: dict[str, str] = {
    "sales_train": "sales_train_validation.csv",
    "sell_prices": "sell_prices.csv",
    "calendar": "calendar.csv",
    "sample_submission": "sample_submission.csv",
}


class M5DatasetError(Exception):
    """Raised when the M5 data dir is missing files or has the wrong shape."""


@dataclass(frozen=True)
class FileMetadata:
    path: Path
    columns: list[str]
    row_count: int


@dataclass(frozen=True)
class M5Catalog:
    """Discovered M5 dataset. Paths + per-file metadata.

    Agent code in the sandbox reads from `path` directly with whatever
    library it likes (pandas, polars, duckdb). The kernel uses this
    object to validate setup, log what was found, and slice subsets
    for fast eval cycles.
    """

    data_dir: Path
    sales_train: FileMetadata
    sell_prices: FileMetadata
    calendar: FileMetadata
    sample_submission: FileMetadata

    @property
    def files(self) -> dict[str, FileMetadata]:
        return {
            "sales_train": self.sales_train,
            "sell_prices": self.sell_prices,
            "calendar": self.calendar,
            "sample_submission": self.sample_submission,
        }

    def date_range(self) -> tuple[str, str]:
        """First and last `date` value in `calendar.csv` (ISO strings).
        Used by the eval harness to pick replay windows."""
        return _calendar_range(self.calendar.path)


@dataclass(frozen=True)
class M5SampleSubset:
    """A small, in-memory slice of the dataset for fast iteration cycles.

    Holds only the first N item_ids worth of sales rows, calendar dates
    spanning the slice, and prices touching those items. Used by the
    eval harness to keep regression-gate cycle times under a few seconds.
    """

    item_ids: list[str]
    sales_rows: list[dict[str, str]]
    calendar_rows: list[dict[str, str]]
    price_rows: list[dict[str, str]] = field(default_factory=list)


def load_m5(data_dir: Path | str) -> M5Catalog:
    """Discover the M5 data files in `data_dir`. Raises `M5DatasetError`
    on a missing file so the caller surfaces a clear setup error."""
    root = Path(data_dir)
    if not root.is_dir():
        raise M5DatasetError(f"M5 data_dir does not exist: {root}")

    metas: dict[str, FileMetadata] = {}
    missing: list[str] = []
    for key, fname in EXPECTED_FILES.items():
        path = root / fname
        if not path.is_file():
            missing.append(fname)
            continue
        metas[key] = _scan_csv(path)

    if missing:
        raise M5DatasetError(
            f"M5 data_dir {root} is missing required files: {missing}",
        )

    return M5Catalog(
        data_dir=root,
        sales_train=metas["sales_train"],
        sell_prices=metas["sell_prices"],
        calendar=metas["calendar"],
        sample_submission=metas["sample_submission"],
    )


def make_sample_subset(catalog: M5Catalog, *, num_items: int = 100) -> M5SampleSubset:
    """Return the first `num_items` distinct item_ids' worth of rows.

    Reads the CSVs once linearly — O(rows), no random access. Designed
    for sandbox unit-cost evaluation runs (regression-gate iteration);
    NOT a substitute for pandas-side feature engineering.
    """
    sales_rows: list[dict[str, str]] = []
    item_ids: list[str] = []
    seen: set[str] = set()

    with catalog.sales_train.path.open() as f:
        reader = csv.DictReader(f)
        for row in reader:
            iid = row.get("item_id", "")
            if iid not in seen:
                if len(seen) >= num_items:
                    continue
                seen.add(iid)
                item_ids.append(iid)
            sales_rows.append(row)

    with catalog.calendar.path.open() as f:
        calendar_rows = list(csv.DictReader(f))

    price_rows: list[dict[str, str]] = []
    if catalog.sell_prices.row_count:
        item_set = set(item_ids)
        with catalog.sell_prices.path.open() as f:
            for row in csv.DictReader(f):
                if row.get("item_id") in item_set:
                    price_rows.append(row)

    return M5SampleSubset(
        item_ids=item_ids,
        sales_rows=sales_rows,
        calendar_rows=calendar_rows,
        price_rows=price_rows,
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _scan_csv(path: Path) -> FileMetadata:
    with path.open() as f:
        reader = csv.reader(f)
        try:
            columns = next(reader)
        except StopIteration:
            return FileMetadata(path=path, columns=[], row_count=0)
        # Linear scan for the row count — O(file). M5 is < 50MB, fine for
        # one-time setup; not in the hot path.
        row_count = sum(1 for _ in reader)
    return FileMetadata(path=path, columns=columns, row_count=row_count)


def _calendar_range(path: Path) -> tuple[str, str]:
    first: str | None = None
    last: str | None = None
    with path.open() as f:
        reader = csv.DictReader(f)
        for row in reader:
            d = row.get("date")
            if not d:
                continue
            if first is None:
                first = d
            last = d
    if first is None or last is None:
        raise M5DatasetError(f"calendar.csv has no `date` rows: {path}")
    return first, last
