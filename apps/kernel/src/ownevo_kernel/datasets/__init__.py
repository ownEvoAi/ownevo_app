"""Dataset loaders + metrics for canonical workflows."""

from .m5 import (
    EXPECTED_FILES,
    FileMetadata,
    M5Catalog,
    M5DatasetError,
    M5SampleSubset,
    load_m5,
    make_sample_subset,
)
from .m5_metric import (
    M5Fold,
    compute_wrmsse_weights_and_scales,
    make_held_out_fold,
    rmse,
    wrmsse,
)

__all__ = [
    "EXPECTED_FILES",
    "FileMetadata",
    "M5Catalog",
    "M5DatasetError",
    "M5Fold",
    "M5SampleSubset",
    "compute_wrmsse_weights_and_scales",
    "load_m5",
    "make_held_out_fold",
    "make_sample_subset",
    "rmse",
    "wrmsse",
]
