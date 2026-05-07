"""CLI entrypoint for `make judge-eval` (W5.2).

Runs the LLM-judge stub approver across the 30-pair eval set
(`JUDGE_EVAL_SET`) and emits a JSON report with per-pair records +
aggregate judge-vs-human agreement + per-bucket correctness +
per-check verdict distribution.

Usage:
    uv run --directory apps/kernel --extra agent python \\
        scripts/judge_eval.py
    uv run --directory apps/kernel --extra agent python \\
        scripts/judge_eval.py --model claude-haiku-4-5 --concurrency 4

By default exit 0 unless an exception fires. `--require-agreement N`
opts into the W5.2 gate behavior — exits 1 if agreement < N (typically
0.85). Default is unset (no gate) so a developer can iterate on the
judge prompt without the gate masking the actual numbers.

Cost surface — printed to stderr before any API call:
  * 30 judge calls (one per pair). Default model is opus 4.7.
  * Each call ~1-2k input tokens (the explanation + minimal context)
    + a few hundred output tokens (3 per-check rationales + overall).
  * Total: ~$0.10-$0.30 per run on opus 4.7 (cheaper than meta-eval
    because the prompt is smaller — no full bundles JSON).
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

from ownevo_kernel.approvers.eval_set import (  # noqa: E402
    JUDGE_EVAL_SET,
    JUDGE_SMOKE_SET,
)
from ownevo_kernel.approvers.llm_judge import (  # noqa: E402
    DEFAULT_MAX_TOKENS,
    DEFAULT_MODEL,
)
from ownevo_kernel.approvers.runner import run_judge_eval  # noqa: E402


def _positive_int(value: str) -> int:
    i = int(value)
    if i <= 0:
        raise argparse.ArgumentTypeError(
            f"must be a positive integer, got {value!r}"
        )
    return i


def _non_negative_int(value: str) -> int:
    i = int(value)
    if i < 0:
        raise argparse.ArgumentTypeError(
            f"must be a non-negative integer, got {value!r}"
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
        prog="judge-eval",
        description=(
            "W5.2 LLM-judge stub approver runner. Runs the Claude judge "
            "across the 30-pair JUDGE_EVAL_SET and emits a JSON report "
            "with judge-vs-human agreement + per-bucket correctness + "
            "per-check verdict distribution. Exit 0 unless "
            "--require-agreement is set + missed."
        ),
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=(
            f"Anthropic model id for the judge. Default {DEFAULT_MODEL!r} "
            "(calibration anchor for the W5.2 ≥0.85 agreement gate)."
        ),
    )
    parser.add_argument(
        "--max-tokens",
        type=_positive_int,
        default=DEFAULT_MAX_TOKENS,
        help=(
            f"Per-call output cap. Default {DEFAULT_MAX_TOKENS}; the judge "
            "writes 3 per-check rationales + an overall_rationale, which "
            "fits comfortably under 3k."
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
        "--max-retries-per-call",
        type=_non_negative_int,
        default=0,
        help=(
            "Retries on JudgmentValidationError per judge call. Default "
            "0 (strict). Set to 1 for live runs against opus 4.7 — the "
            "model occasionally returns malformed JSON in the string-"
            "wrapped payload (~5-10%% of calls); a single retry empirically "
            "resolves it."
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
            "(no gate). Set to 0.85 in CI to enforce the W5.2 target."
        ),
    )
    parser.add_argument(
        "--smoke",
        action="store_true",
        help=(
            "Run the 5-record smoke subset (3 admit + 2 reject — vague-"
            "positive + wrong-direction) instead of the full 30-record "
            "set. Cheap iteration loop for prompt edits (~$0.02 per run "
            "on opus 4.7); the full set is the calibrated grade."
        ),
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print the JSON output (2-space indent).",
    )
    return parser.parse_args(argv)


def _print_config(ns: argparse.Namespace) -> None:
    eval_set = JUDGE_SMOKE_SET if ns.smoke else JUDGE_EVAL_SET
    label = "smoke" if ns.smoke else "full"
    print(
        f"[judge-eval] set={label} pairs={len(eval_set)} "
        f"model={ns.model!r} concurrency={ns.concurrency}",
        file=sys.stderr,
    )
    if not ns.anthropic_base_url:
        print(
            f"[judge-eval] live Anthropic API: "
            f"{len(eval_set)} judge calls; "
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
            "[judge-eval] ANTHROPIC_API_KEY is unset and "
            "--anthropic-base-url was not passed. Aborting before "
            "any live call.",
            file=sys.stderr,
        )
        return 2

    client = _make_client(ns.anthropic_base_url)

    eval_set = JUDGE_SMOKE_SET if ns.smoke else JUDGE_EVAL_SET
    started = time.perf_counter()
    report = await run_judge_eval(
        client,
        eval_set=eval_set,
        model=ns.model,
        max_tokens=ns.max_tokens,
        concurrency=ns.concurrency,
        max_retries_per_call=ns.max_retries_per_call,
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

    if (
        ns.require_agreement is not None
        and report.agreement < ns.require_agreement
    ):
        print(
            f"[judge-eval] agreement {report.agreement:.3f} < "
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
