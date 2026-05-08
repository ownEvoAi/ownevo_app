"""Smoke tests for `scripts/cluster_nl_gen_failures.py` (W5.3).

We don't drive the full async DB path here — that's covered by manual
runs against a postgres instance. These tests pin:

  * `--strategy` arg-parser semantics (defaults, choices, `--require-clusters`
    rejection of non-positive ints).
  * Each stub strategy produces the failure population it claims.
  * The end-to-end `main_async` smoke against the 3 fixtures in `--no-db`
    mode lands ≥3 clusters and exit 0.
  * `--require-clusters N` returns exit 5 when the threshold isn't met.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

_KERNEL_ROOT = Path(__file__).resolve().parents[1]
if str(_KERNEL_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(_KERNEL_ROOT / "scripts"))

import cluster_nl_gen_failures as cli  # noqa: E402
from ownevo_kernel.nl_gen.eval_case_set import GeneratedEvalCase  # noqa: E402
from ownevo_kernel.nl_gen.fixtures import EVAL_CASE_SET_FIXTURES  # noqa: E402
from ownevo_kernel.nl_gen.spec import Provenance  # noqa: E402

# ---------------------------------------------------------------------------
# Argparse
# ---------------------------------------------------------------------------


def test_default_args_parse():
    args = cli._parse_args([])
    assert args.strategy == "miss-derived-and-train-fps"
    assert args.real is False
    assert args.no_db is False
    assert args.require_clusters is None
    assert args.workflow_id == cli.DEFAULT_WORKFLOW_ID


def test_strategy_choices_pinned():
    """The CLI's strategy set is part of the public contract — anything
    we drop here breaks downstream wrappers / make targets."""
    expected = {
        "always-false",
        "always-true",
        "miss-derived",
        "miss-derived-and-train-fps",
    }
    assert set(cli._STRATEGIES) == expected


def test_unknown_strategy_rejected():
    with pytest.raises(SystemExit):
        cli._parse_args(["--strategy", "no-such-thing"])


def test_require_clusters_zero_rejected():
    with pytest.raises(SystemExit):
        cli._parse_args(["--require-clusters", "0"])


def test_require_clusters_negative_rejected():
    with pytest.raises(SystemExit):
        cli._parse_args(["--require-clusters", "-3"])


def test_require_clusters_non_int_rejected():
    with pytest.raises(SystemExit):
        cli._parse_args(["--require-clusters", "three"])


def test_positive_int_helper_rejects_garbage():
    with pytest.raises(argparse.ArgumentTypeError):
        cli._positive_int("nope")


# ---------------------------------------------------------------------------
# Stub strategies — verify each produces the failure population it claims
# ---------------------------------------------------------------------------


def _case(
    *,
    case_id: str,
    expected: bool,
    kind: str = "derived",
    is_test_fold: bool = False,
) -> GeneratedEvalCase:
    return GeneratedEvalCase(
        case_id=case_id,
        provenance=Provenance(kind=kind, source="s"),  # type: ignore[arg-type]
        sim_seed=0,
        n_steps=10,
        target_step_index=0,
        target_label_field="x",
        expected_value=expected,
        rationale="r",
        is_test_fold=is_test_fold,
    )


def test_strategy_always_false_predicts_false():
    c = _case(case_id="c1", expected=True)
    assert cli._strategy_always_false(c) is False


def test_strategy_always_true_predicts_true():
    c = _case(case_id="c1", expected=False)
    assert cli._strategy_always_true(c) is True


def test_strategy_miss_derived_flips_only_derived():
    derived_true = _case(case_id="c1", expected=True, kind="derived")
    derived_false = _case(case_id="c2", expected=False, kind="derived")
    inferred_true = _case(case_id="c3", expected=True, kind="inferred")
    inferred_false = _case(case_id="c4", expected=False, kind="inferred")
    assert cli._strategy_miss_derived(derived_true) is False
    assert cli._strategy_miss_derived(derived_false) is True
    assert cli._strategy_miss_derived(inferred_true) is True
    assert cli._strategy_miss_derived(inferred_false) is False


def test_strategy_miss_derived_and_train_fps_mixes_both_modes():
    fn = cli._strategy_miss_derived_and_train_fps
    # Derived flip
    assert fn(_case(case_id="d", expected=True, kind="derived")) is False
    # Inferred False on train fold → predicts True (FP)
    assert (
        fn(_case(case_id="i", expected=False, kind="inferred", is_test_fold=False)) is True
    )
    # Inferred False on test fold → ground-truth (no FP injected on test fold)
    assert (
        fn(_case(case_id="i2", expected=False, kind="inferred", is_test_fold=True))
        is False
    )
    # Inferred True (any fold) → ground-truth (passes)
    assert (
        fn(_case(case_id="i3", expected=True, kind="inferred")) is True
    )


# ---------------------------------------------------------------------------
# End-to-end: main_async --no-db over the 3 fixtures
# ---------------------------------------------------------------------------


def _run_main(argv: list[str]) -> tuple[int, str]:
    """Run `main_async` with `argv` and capture stdout."""
    args = cli._parse_args(argv)
    f = io.StringIO()
    with redirect_stdout(f):
        rc = asyncio.run(cli.main_async(args))
    return rc, f.getvalue()


def test_main_async_default_no_db_lands_three_or_more_clusters():
    """W5.3 spec gate (smoke): ≥3 clusters from the 3 fixture EvalCaseSets."""
    rc, output = _run_main(["--no-db"])
    assert rc == 0
    payload = _parse_json(output)
    assert payload["signal"] == "ok"
    assert payload["n_clusters"] >= 3
    # Every cluster has ≥1 sample so the labeler had something to chew on.
    for cluster in payload["clusters"]:
        assert cluster["size"] >= 1
        assert cluster["samples"]
    # Per-workflow breakdown carries all 3 NL-gen fixture ids.
    per_wf = payload["per_workflow_failure_counts"]
    expected_ids = {cs.workflow_spec_id for cs in EVAL_CASE_SET_FIXTURES.values()}
    assert set(per_wf) == expected_ids


def test_main_async_require_clusters_passes_when_met():
    rc, _ = _run_main(["--no-db", "--require-clusters", "3"])
    assert rc == 0


def test_main_async_require_clusters_fails_when_unmet():
    rc, _ = _run_main(["--no-db", "--require-clusters", "999"])
    assert rc == 5


def test_main_async_always_false_strategy_lands_three_clusters():
    """always-false → one cluster per workflow (false-negative population)."""
    rc, output = _run_main(["--no-db", "--strategy", "always-false"])
    assert rc == 0
    payload = _parse_json(output)
    assert payload["n_clusters"] == 3


def test_main_async_pretty_emits_indented_json():
    rc, output = _run_main(["--no-db", "--pretty"])
    assert rc == 0
    assert output.startswith("{\n")
    assert '  "signal":' in output


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_json(output: str) -> dict:
    """Pull the trailing JSON object out of stdout (the script may have
    printed structured-log lines on stderr, but stdout is JSON-only)."""
    import json

    return json.loads(output.strip())
