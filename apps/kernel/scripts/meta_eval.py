"""CLI entrypoint for `make meta-eval` (A4.6).

Runs the LLM-as-judge across the 10-pair meta-eval set
(`META_EVAL_SET`) and emits a JSON report with per-pair records +
aggregate judge-vs-human agreement.

Usage:
    uv run --directory apps/kernel --extra agent python \\
        scripts/meta_eval.py
    uv run --directory apps/kernel --extra agent python \\
        scripts/meta_eval.py --model claude-haiku-4-5 --concurrency 4

By default exit 0 unless an exception fires. `--require-agreement N`
opts into the W5 (A5.5) gate behavior — exits 1 if agreement < N
(typically 0.7). Default is unset (no gate) because A4.6's contract
is "judge runs + emits scores", not "agreement gate".

Cost surface — printed to stderr before any API call:
  * 20 judge calls (10 pairs × 2 sides). Default model is opus 4.7.
  * Each call ~5-8k input tokens (mostly the artifacts JSON) + a few
    hundred output tokens.
  * Total: ~$0.50-$1.00 per run on opus 4.7.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Sequence

_KERNEL_ROOT = Path(__file__).resolve().parents[1]
if str(_KERNEL_ROOT) not in sys.path:
    sys.path.insert(0, str(_KERNEL_ROOT))

from ownevo_kernel.nl_gen.meta_eval.eval_set import META_EVAL_SET  # noqa: E402
from ownevo_kernel.nl_gen.meta_eval.judge import (  # noqa: E402
    DEFAULT_MAX_TOKENS,
    DEFAULT_MODEL,
)
from ownevo_kernel.nl_gen.meta_eval.runner import run_meta_eval  # noqa: E402


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
        prog="meta-eval",
        description=(
            "A4.6 meta-eval runner. Runs the Claude judge across the 10-pair "
            "META_EVAL_SET and emits a JSON report with judge-vs-human "
            "agreement + per-dimension verdict distribution + per-recipe "
            "correctness. Exit 0 unless --require-agreement is set + missed."
        ),
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=(
            f"Anthropic model id for the judge. Default {DEFAULT_MODEL!r} "
            "(calibration anchor for the W5 ≥0.7 agreement gate)."
        ),
    )
    parser.add_argument(
        "--max-tokens",
        type=_positive_int,
        default=DEFAULT_MAX_TOKENS,
        help=(
            f"Per-call output cap. Default {DEFAULT_MAX_TOKENS}; the judge "
            "writes 3 per-dimension rationales + an overall_rationale, "
            "which can run a few hundred chars each."
        ),
    )
    parser.add_argument(
        "--concurrency",
        type=_positive_int,
        default=1,
        help=(
            "Number of judge calls to run in parallel. Default 1 "
            "(sequential, simplest, cheapest against rate limits)."
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
            "Include the per-pair records (with full judgments) in the "
            "JSON output. Default omits to keep stdout small; the "
            "aggregate is still printed."
        ),
    )
    parser.add_argument(
        "--require-agreement",
        type=_agreement_threshold,
        default=None,
        help=(
            "Minimum judge-vs-human agreement to exit 0. Default unset "
            "(A4.6 has no gate). Set to 0.7 in CI to enforce the W5 "
            "(A5.5) target."
        ),
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print the JSON output (2-space indent).",
    )
    return parser.parse_args(argv)


def _print_config(ns: argparse.Namespace) -> None:
    print(
        f"[meta-eval] pairs={len(META_EVAL_SET)} (×2 sides = "
        f"{2 * len(META_EVAL_SET)} judge calls) model={ns.model!r} "
        f"concurrency={ns.concurrency}",
        file=sys.stderr,
    )
    if not ns.anthropic_base_url:
        print(
            "[meta-eval] live Anthropic API: "
            f"{2 * len(META_EVAL_SET)} judge calls; "
            "ANTHROPIC_API_KEY required.",
            file=sys.stderr,
        )


def _make_client(base_url: str | None):
    from anthropic import AsyncAnthropic

    if base_url:
        return AsyncAnthropic(base_url=base_url)
    return AsyncAnthropic()


async def _async_main(ns: argparse.Namespace) -> int:
    _print_config(ns)
    if (
        not os.environ.get("ANTHROPIC_API_KEY")
        and not ns.anthropic_base_url
    ):
        print(
            "[meta-eval] ANTHROPIC_API_KEY is unset and "
            "--anthropic-base-url was not passed. Aborting before "
            "any live call.",
            file=sys.stderr,
        )
        return 2

    client = _make_client(ns.anthropic_base_url)

    started = time.perf_counter()
    report = await run_meta_eval(
        client,
        model=ns.model,
        max_tokens=ns.max_tokens,
        concurrency=ns.concurrency,
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
                f"[meta-eval] agreement {report.agreement:.3f} < "
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
