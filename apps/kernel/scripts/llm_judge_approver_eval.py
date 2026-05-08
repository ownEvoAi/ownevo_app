"""CLI entrypoint for `make llm-judge-approver-eval` (W5.2).

Runs the W5.2 LLM-judge stub approver (`approvers/llm_judge`) across
the 30-case hand-labeled fixture set (`LABELED_APPROVAL_CASES`) and
emits a JSON report with per-case records + aggregate judge-vs-human
agreement + per-bucket slicing.

Usage:
    uv run --directory apps/kernel --extra agent python \\
        scripts/llm_judge_approver_eval.py
    uv run --directory apps/kernel --extra agent python \\
        scripts/llm_judge_approver_eval.py --judge-model claude-opus-4-7 \\
        --concurrency 6 --pretty --require-agreement 0.85

By default exit 0 unless an exception fires. `--require-agreement N`
opts into the W5.2 gate behavior — exits 1 if agreement < N.

Cost surface — printed to stderr before any API call:
  * 30 judge calls (opus 4.7 by default).
  * Total: ~$0.40 per run on default model + 30-case set.
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

from ownevo_kernel.approvers.llm_judge.fixtures import (  # noqa: E402
    LABELED_APPROVAL_CASES,
)
from ownevo_kernel.approvers.llm_judge.judge import (  # noqa: E402
    DEFAULT_MAX_TOKENS,
    DEFAULT_MODEL,
)
from ownevo_kernel.approvers.llm_judge.runner import (  # noqa: E402
    run_llm_judge_approver_eval,
)


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
        prog="llm-judge-approver-eval",
        description=(
            "W5.2 LLM-judge stub approver eval. Runs the judge "
            f"(default {DEFAULT_MODEL}) across the 30-case hand-labeled "
            "LABELED_APPROVAL_CASES fixture set and emits a JSON report "
            "with judge-vs-human agreement, per-bucket slicing, and "
            "verdict distribution. Exit 0 unless --require-agreement "
            "is set + missed."
        ),
    )
    parser.add_argument(
        "--judge-model",
        default=DEFAULT_MODEL,
        help=(
            f"Anthropic model id for the judge. Default {DEFAULT_MODEL!r} "
            "(W5.2 calibration anchor; opus is strictly stronger than "
            "the agent loop's solver tier)."
        ),
    )
    parser.add_argument(
        "--max-tokens",
        type=_positive_int,
        default=DEFAULT_MAX_TOKENS,
        help=(
            f"Per-judge-call output cap. Default {DEFAULT_MAX_TOKENS}; "
            "the judge writes 3 per-element quotes + a rationale, so "
            "1.5k headroom is generous."
        ),
    )
    parser.add_argument(
        "--concurrency",
        type=_positive_int,
        default=1,
        help=(
            "Number of judge calls to run in parallel. Default 1. "
            "Bump to 4-8 for faster CI runs (6 is reasonable on the "
            "default account quota)."
        ),
    )
    parser.add_argument(
        "--max-retries-per-call",
        type=_non_negative_int,
        default=0,
        help=(
            "Retries on LLMJudgeApprovalJudgmentValidationError per "
            "judge call. Default 0 (strict). Bump to 1 for cheap "
            "insurance against transient malformed-JSON returns."
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
            "Include the per-case records (with full judgments) in the "
            "JSON output. Default omits to keep stdout small; the "
            "aggregate + per-bucket breakdown is still printed."
        ),
    )
    parser.add_argument(
        "--require-agreement",
        type=_agreement_threshold,
        default=None,
        help=(
            "Minimum judge-vs-human agreement to exit 0. Default unset. "
            "Set to 0.85 to enforce the W5.2 exit criterion on a local "
            "gate run."
        ),
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print the JSON output (2-space indent).",
    )
    return parser.parse_args(argv)


def _print_config(ns: argparse.Namespace) -> None:
    n = len(LABELED_APPROVAL_CASES)
    print(
        f"[llm-judge-approver-eval] cases={n} "
        f"judge_model={ns.judge_model!r} concurrency={ns.concurrency}",
        file=sys.stderr,
    )
    if not ns.anthropic_base_url:
        print(
            f"[llm-judge-approver-eval] live Anthropic API: {n} judge "
            "calls; ANTHROPIC_API_KEY required.",
            file=sys.stderr,
        )


def _make_async_client(base_url: str | None):
    """Anthropic client with sensible defaults for local routing.

    When ``base_url`` is set (LM Studio at :1234, LiteLLM proxy, etc.)
    the SDK still validates that *some* auth header is present even
    though the local server typically ignores it. Default the api_key
    to ``"local"`` in that case so callers don't have to remember to
    set ``ANTHROPIC_API_KEY=anything`` to satisfy the SDK validator —
    bit during the 2026-05-08 W5.2 local-judge run. Cloud route
    (``base_url is None``) keeps the SDK's normal env-var discovery
    so ``ANTHROPIC_API_KEY`` works as before.
    """
    from anthropic import AsyncAnthropic

    if base_url:
        return AsyncAnthropic(
            base_url=base_url,
            api_key=os.environ.get("ANTHROPIC_API_KEY", "local"),
        )
    return AsyncAnthropic()


async def _async_main(ns: argparse.Namespace) -> int:
    _print_config(ns)

    if (
        not os.environ.get("ANTHROPIC_API_KEY")
        and not ns.anthropic_base_url
    ):
        print(
            "[llm-judge-approver-eval] ANTHROPIC_API_KEY is unset and "
            "--anthropic-base-url was not passed. Aborting before any "
            "live call.",
            file=sys.stderr,
        )
        return 2

    started = time.perf_counter()
    async with _make_async_client(ns.anthropic_base_url) as judge_client:
        report = await run_llm_judge_approver_eval(
            judge_client,
            judge_model=ns.judge_model,
            judge_max_tokens=ns.max_tokens,
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

    if ns.require_agreement is not None:
        if report.agreement < ns.require_agreement:
            print(
                f"[llm-judge-approver-eval] agreement "
                f"{report.agreement:.3f} < required "
                f"{ns.require_agreement:.3f}",
                file=sys.stderr,
            )
            return 1
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    ns = _parse_args(argv)
    return asyncio.run(_async_main(ns))


if __name__ == "__main__":
    sys.exit(main())
