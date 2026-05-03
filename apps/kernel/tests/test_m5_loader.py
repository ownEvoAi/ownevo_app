"""M5 dataset loader — pure unit tests against synthetic CSV fixtures.

The loader is path-and-shape only (no pandas dep, no actual M5 download).
Tests build tiny CSVs that mirror the column structure of the real
dataset, then exercise discovery, metadata, sample subsetting, and
error paths.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from ownevo_kernel.datasets import (
    EXPECTED_FILES,
    M5DatasetError,
    load_m5,
    make_sample_subset,
)

# ---------------------------------------------------------------------------
# Synthetic dataset fixture
# ---------------------------------------------------------------------------

# 3 series × 3 days. Column shape mirrors the real M5 dataset.
_SALES_HEADER = "id,item_id,dept_id,cat_id,store_id,state_id,d_1,d_2,d_3"
_SALES_ROWS = [
    "FOODS_1_001_CA_1_validation,FOODS_1_001,FOODS_1,FOODS,CA_1,CA,1,0,2",
    "FOODS_1_002_CA_1_validation,FOODS_1_002,FOODS_1,FOODS,CA_1,CA,0,3,1",
    "HOBBIES_1_001_TX_1_validation,HOBBIES_1_001,HOBBIES_1,HOBBIES,TX_1,TX,5,4,6",
]

_PRICES = [
    "store_id,item_id,wm_yr_wk,sell_price",
    "CA_1,FOODS_1_001,11101,2.50",
    "CA_1,FOODS_1_002,11101,3.00",
    "TX_1,HOBBIES_1_001,11101,1.99",
]

_CALENDAR = [
    "date,wm_yr_wk,weekday,wday,month,year,d,event_name_1,event_type_1",
    "2011-01-29,11101,Saturday,1,1,2011,d_1,,",
    "2011-01-30,11101,Sunday,2,1,2011,d_2,,",
    "2011-01-31,11101,Monday,3,1,2011,d_3,,",
]

_SAMPLE_SUBMISSION = [
    "id,F1,F2,F3",
    "FOODS_1_001_CA_1_validation,0,0,0",
    "FOODS_1_002_CA_1_validation,0,0,0",
]


@pytest.fixture
def m5_dir(tmp_path: Path) -> Path:
    (tmp_path / "sales_train_validation.csv").write_text(
        _SALES_HEADER + "\n" + "\n".join(_SALES_ROWS) + "\n",
    )
    (tmp_path / "sell_prices.csv").write_text("\n".join(_PRICES) + "\n")
    (tmp_path / "calendar.csv").write_text("\n".join(_CALENDAR) + "\n")
    (tmp_path / "sample_submission.csv").write_text("\n".join(_SAMPLE_SUBMISSION) + "\n")
    return tmp_path


# ---------------------------------------------------------------------------
# Discovery + metadata
# ---------------------------------------------------------------------------


def test_load_m5_discovers_all_four_files(m5_dir: Path):
    catalog = load_m5(m5_dir)
    assert catalog.data_dir == m5_dir
    assert set(catalog.files.keys()) == set(EXPECTED_FILES.keys())
    for meta in catalog.files.values():
        assert meta.path.is_file()
        assert meta.row_count >= 0


def test_load_m5_records_columns_and_row_counts(m5_dir: Path):
    catalog = load_m5(m5_dir)
    assert catalog.sales_train.row_count == 3
    assert "item_id" in catalog.sales_train.columns
    assert catalog.calendar.row_count == 3
    assert catalog.sell_prices.row_count == 3
    assert catalog.sample_submission.row_count == 2


def test_date_range_from_calendar(m5_dir: Path):
    catalog = load_m5(m5_dir)
    first, last = catalog.date_range()
    assert first == "2011-01-29"
    assert last == "2011-01-31"


# ---------------------------------------------------------------------------
# Sample subset
# ---------------------------------------------------------------------------


def test_make_sample_subset_limits_items(m5_dir: Path):
    catalog = load_m5(m5_dir)
    subset = make_sample_subset(catalog, num_items=2)
    assert len(subset.item_ids) == 2
    # Items appear in file order.
    assert subset.item_ids == ["FOODS_1_001", "FOODS_1_002"]
    # Sales rows for those items only.
    assert len(subset.sales_rows) == 2
    assert all(r["item_id"] in subset.item_ids for r in subset.sales_rows)
    # Prices filtered to the sampled items.
    assert all(r["item_id"] in subset.item_ids for r in subset.price_rows)
    assert len(subset.price_rows) == 2


def test_make_sample_subset_returns_all_when_num_items_exceeds_total(m5_dir: Path):
    catalog = load_m5(m5_dir)
    subset = make_sample_subset(catalog, num_items=999)
    assert len(subset.item_ids) == 3
    assert len(subset.sales_rows) == 3


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


def test_load_m5_missing_file_raises(tmp_path: Path):
    # Create three of four files; loader should report the missing one.
    (tmp_path / "sales_train_validation.csv").write_text(_SALES_HEADER + "\n")
    (tmp_path / "calendar.csv").write_text("\n".join(_CALENDAR) + "\n")
    (tmp_path / "sample_submission.csv").write_text("\n".join(_SAMPLE_SUBMISSION) + "\n")
    with pytest.raises(M5DatasetError, match="sell_prices.csv"):
        load_m5(tmp_path)


def test_load_m5_wrong_dir_raises(tmp_path: Path):
    with pytest.raises(M5DatasetError, match="does not exist"):
        load_m5(tmp_path / "no-such-dir")


def test_calendar_with_no_dates_raises(tmp_path: Path):
    """Mirrors a corrupt calendar.csv missing the date column."""
    (tmp_path / "sales_train_validation.csv").write_text(_SALES_HEADER + "\n")
    (tmp_path / "sell_prices.csv").write_text("\n".join(_PRICES) + "\n")
    (tmp_path / "calendar.csv").write_text("wm_yr_wk\n11101\n")  # no `date` column
    (tmp_path / "sample_submission.csv").write_text("\n".join(_SAMPLE_SUBMISSION) + "\n")
    catalog = load_m5(tmp_path)
    with pytest.raises(M5DatasetError, match="no `date` rows"):
        catalog.date_range()
