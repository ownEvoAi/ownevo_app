"""CLI entrypoint for `make cluster-label-eval` (B3.5).

Runs the production cluster labeler + sonnet judge across the 20-case
hand-labeled fixture set (`LABELED_CLUSTER_CASES`) and emits a JSON
report with per-case records + aggregate judge-vs-human agreement +
per-failure-mode slicing.

Usage:
    uv run --directory apps/kernel --extra agent python \\
        scripts/cluster_label_eval.py
    uv run --directory apps/kernel --extra agent python \\
        scripts/cluster_label_eval.py --judge-model claude-opus-4-7 \\
        --concurrency 4 --pretty

By default exit 0 unless an exception fires. `--require-agreement N`
opts into the W3 Track B gate behavior — exits 1 if agreement < N
(0.7 is the deliverable target; the nightly workflow passes 0.7).

Cost surface — printed to stderr before any API call:
  * 20 labeler calls (haiku 4.5; ~$0.01 each).
  * 20 judge calls (sonnet 4.6; ~$0.05 each).
  * Total: ~$1.20 per run on default models.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from collections.abc import Sequence
from pathlib import Path

_KERNEL_ROOT = Path(__file__).resolve().parents[1]
if str(_KERNEL_ROOT) not in sys.path:
    sys.path.insert(0, str(_KERNEL_ROOT))

from ownevo_kernel.clustering.label_eval.fixtures import (  # noqa: E402
    LABELED_CLUSTER_CASES,
)
from ownevo_kernel.clustering.label_eval.judge import (  # noqa: E402
    DEFAULT_MAX_TOKENS,
    DEFAULT_MODEL,
)
from ownevo_kernel.clustering.label_eval.runner import (  # noqa: E402
    run_cluster_label_eval,
    wrap_sync_labeler,
)

_DEFAULT_LABELER_MODEL = "claude-haiku-4-5-20251001"


def _positive_int(value: str) -> int:
    i = int(value)
    if i <= 0:
        raise argparse.ArgumentTypeError(
            f"must be a positive integer, got {value!r}"
        )
    return i


def _agreement_threshold(value: str) -> float:
    f = float(value)
    if not 0.0 <= f <= 1.0:
        raise argparse.ArgumentTypeError(
            f"--require-agreement must be in [0.0, 1.0]; got {value!r}"
        )
    return f


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="cluster-label-eval",
        description=(
            "B3.5 cluster-label eval. Runs the production labeler "
            "(haiku 4.5) + a separate judge (sonnet 4.6) across the "
            "20-case hand-labeled LABELED_CLUSTER_CASES fixture set. "
            "Emits a JSON report with judge-vs-human agreement, "
            "per-failure-mode slicing, and verdict distribution. "
            "Exit 0 unless --require-agreement is set + missed."
        ),
    )
    parser.add_argument(
        "--judge-model",
        default=DEFAULT_MODEL,
        help=(
            f"Anthropic model id for the judge. Default {DEFAULT_MODEL!r}. "
            "Must be different from --labeler-model (D4 contract)."
        ),
    )
    parser.add_argument(
        "--labeler-model",
        default=_DEFAULT_LABELER_MODEL,
        help=(
            f"Anthropic model id for the labeler. Default "
            f"{_DEFAULT_LABELER_MODEL!r} (production default)."
        ),
    )
    parser.add_argument(
        "--max-tokens",
        type=_positive_int,
        default=DEFAULT_MAX_TOKENS,
        help=(
            f"Per-judge-call output cap. Default {DEFAULT_MAX_TOKENS}; "
            "the judge writes one ≤400-char rationale plus the verdict, "
            "so 1k is generous."
        ),
    )
    parser.add_argument(
        "--concurrency",
        type=_positive_int,
        default=1,
        help=(
            "Number of (label + judge) pairs to run in parallel. "
            "Default 1. Bump to 4-8 for faster CI runs."
        ),
    )
    parser.add_argument(
        "--max-retries-per-call",
        type=int,
        default=0,
        help=(
            "Retries on ClusterLabelJudgmentValidationError per judge call. "
            "Default 0 (strict). Bump to 1 for cheap insurance against "
            "transient malformed-JSON returns."
        ),
    )
    parser.add_argument(
        "--anthropic-base-url",
        default=None,
        help=(
            "Anthropic-compatible /v1/messages base URL (LM Studio, "
            "LiteLLM proxy). Default uses the Anthropic API directly."
        ),
    )
    parser.add_argument(
        "--include-records",
        action="store_true",
        help=(
            "Include the per-case records (with full judgments + "
            "candidate labels) in the JSON output. Default omits to "
            "keep stdout small; the aggregate is still printed."
        ),
    )
    parser.add_argument(
        "--require-agreement",
        type=_agreement_threshold,
        default=None,
        help=(
            "Minimum judge-vs-human agreement to exit 0. Default unset. "
            "Set to 0.7 in the nightly workflow to enforce the W3 "
            "Track B exit criterion."
        ),
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print the JSON output (2-space indent).",
    )
    return parser.parse_args(argv)


def _print_config(ns: argparse.Namespace) -> None:
    n = len(LABELED_CLUSTER_CASES)
    print(
        f"[cluster-label-eval] cases={n} judge_model={ns.judge_model!r} "
        f"labeler_model={ns.labeler_model!r} concurrency={ns.concurrency}",
        file=sys.stderr,
    )
    if not ns.anthropic_base_url:
        print(
            f"[cluster-label-eval] live Anthropic API: {n} labeler + "
            f"{n} judge calls; ANTHROPIC_API_KEY required.",
            file=sys.stderr,
        )


def _make_async_client(base_url: str | None):
    from anthropic import AsyncAnthropic

    if base_url:
        return AsyncAnthropic(base_url=base_url)
    return AsyncAnthropic()


def _make_sync_client(base_url: str | None):
    from anthropic import Anthropic

    if base_url:
        return Anthropic(base_url=base_url)
    return Anthropic()


def _make_label_fn(labeler_model: str, base_url: str | None):
    """Build the async LabelFn the runner expects.

    Default wires the production `AnthropicLabeler` (sync, haiku 4.5)
    and adapts it to async via `wrap_sync_labeler`. Tests monkeypatch
    this factory to substitute a canned label producer."""
    from ownevo_kernel.clustering.default_impl import AnthropicLabeler

    sync_client = _make_sync_client(base_url)
    labeler = AnthropicLabeler(client=sync_client, model=labeler_model)
    return wrap_sync_labeler(labeler)


async def _async_main(ns: argparse.Namespace) -> int:
    _print_config(ns)

    if ns.judge_model == ns.labeler_model:
        # D4 (eng-review): "different model from the labeler" — refuse
        # before any API call so a misconfigured CI doesn't silently
        # produce a self-judging report.
        print(
            f"[cluster-label-eval] --judge-model and --labeler-model are "
            f"both {ns.judge_model!r}; D4 requires different models. Aborting.",
            file=sys.stderr,
        )
        return 2

    if (
        not os.environ.get("ANTHROPIC_API_KEY")
        and not ns.anthropic_base_url
    ):
        print(
            "[cluster-label-eval] ANTHROPIC_API_KEY is unset and "
            "--anthropic-base-url was not passed. Aborting before any "
            "live call.",
            file=sys.stderr,
        )
        return 2

    judge_client = _make_async_client(ns.anthropic_base_url)
    label_fn = _make_label_fn(ns.labeler_model, ns.anthropic_base_url)

    started = time.perf_counter()
    report = await run_cluster_label_eval(
        judge_client,
        label_fn,
        judge_model=ns.judge_model,
        judge_max_tokens=ns.max_tokens,
        concurrency=ns.concurrency,
        max_retries_per_call=ns.max_retries_per_call,
        labeler_label_for_log=ns.labeler_model,
    )
    wall_seconds = time.perf_counter() - started

    payload = report.to_dict()
    payload["wall_seconds"] = round(wall_seconds, 3)
    if not ns.include_records:
        payload.pop("records", None)

    output = (
        json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=True)
        if ns.pretty
        else json.dumps(payload, sort_keys=True, ensure_ascii=True)
    )
    print(output, flush=True)

    if ns.require_agreement is not None:
        if report.agreement < ns.require_agreement:
            print(
                f"[cluster-label-eval] agreement {report.agreement:.3f} < "
                f"required {ns.require_agreement:.3f}",
                file=sys.stderr,
            )
            return 1
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    ns = _parse_args(argv)
    return asyncio.run(_async_main(ns))


if __name__ == "__main__":
    sys.exit(main())
