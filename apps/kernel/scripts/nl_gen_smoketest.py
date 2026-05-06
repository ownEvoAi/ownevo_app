"""End-to-end NL-gen smoketest CLI for `make nl-gen-smoketest` (A4.4).

The Phase-2 quality gate: per PLAN.md A4.4, "all 3 workflows pass
`make nl-gen-smoketest WORKFLOW=<name>`. If even one fails, slip Phase 2."

Per-workflow flow:

  1. Acquire the artifact quartet — either by regenerating live
     (default) via `nl_gen.generate_full_pipeline`, or by loading the
     hand-authored fixtures (`--from-fixtures`).
  2. Drive `eval_runner.run_with_agent` — Claude predicts the redacted
     bool label per case (single-turn forced tool-use; default model
     is haiku).
  3. Score via the workflow's MetricDefinition.
  4. Emit one JSON line per workflow with `meets_target` + the metric
     value + costs.

Exit code:

  * 0 — every requested workflow's report `meets_target=True`.
  * 1 — at least one missed (the Phase-2-slip signal).
  * 2 — argparse error or precondition failure.

Modes:

  * Default (regenerate live): full A4.4 gate. Cost ~16 calls per
    workflow (4 NL-gen + 12 agent predictions on a 12-case fixture).
  * `--from-fixtures`: skip NL-gen, use the hand-authored quartet.
    Useful for fast inner-loop dev on the agent prompt without
    burning generation cost. Still hits the live agent for predictions.
  * `--max-cases N`: cap cases per workflow (passes through to a
    bounded subset for quick smoke).
  * `--anthropic-base-url`: route through a local LLM proxy that
    speaks the Anthropic /v1/messages shape (LM Studio, LiteLLM).

Live API key is read by the AsyncAnthropic client from
`ANTHROPIC_API_KEY` (or whatever `--anthropic-base-url` requires).
The CLI prints the cost-relevant config block to stderr before any
API call so the operator sees what they're about to spend.
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

from ownevo_kernel.eval_runner import EvalRunReport, run_with_agent  # noqa: E402
from ownevo_kernel.eval_runner.agent_solver import (  # noqa: E402
    DEFAULT_MODEL as AGENT_DEFAULT_MODEL,
)
from ownevo_kernel.eval_runner import (  # noqa: E402
    TokenBudget,
    TokenBudgetExceededError,
)
from ownevo_kernel.nl_gen import EvalCaseSet, generate_full_pipeline  # noqa: E402
from ownevo_kernel.nl_gen.fixtures import (  # noqa: E402
    DESCRIPTIONS,
    EVAL_CASE_SET_FIXTURES,
    FIXTURES,
    METRIC_FIXTURES,
    SIM_PLAN_FIXTURES,
)

WORKFLOW_CHOICES = sorted(FIXTURES.keys())
_NL_GEN_STAGES = 4  # workflow_spec → sim_plan → eval_case_set → metric_definition


def _positive_int(value: str) -> int:
    """argparse type that rejects zero and negative values with a clean error."""
    i = int(value)
    if i <= 0:
        raise argparse.ArgumentTypeError(f"must be a positive integer, got {value!r}")
    return i


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="nl-gen-smoketest",
        description=(
            "A4.4 quality gate. Replays each requested workflow end-to-end: "
            "(optionally) regenerate the artifact quartet via live NL-gen, "
            "drive a Claude agent solver per eval case, score via the "
            "workflow's metric. Exit 0 iff every workflow meets target."
        ),
    )
    parser.add_argument(
        "--workflow",
        required=True,
        choices=[*WORKFLOW_CHOICES, "all"],
        help=(
            "Which workflow to smoke. `all` runs every fixture and exits 0 "
            "only if every one meets target."
        ),
    )
    parser.add_argument(
        "--from-fixtures",
        action="store_true",
        help=(
            "Skip live NL-gen — use the hand-authored fixture quartet. "
            "Default is to regenerate via the live API (the canonical "
            "A4.4 gate run)."
        ),
    )
    parser.add_argument(
        "--max-cases",
        type=int,
        default=None,
        help=(
            "Cap eval cases per workflow (truncate from the end of the "
            "case set). Useful for fast inner-loop dev. Note: dropping "
            "below MIN_CLASS_COUNT=3 of either class will trip the "
            "EvalCaseSet validator on re-validation."
        ),
    )
    parser.add_argument(
        "--model",
        default=AGENT_DEFAULT_MODEL,
        help=(
            f"Anthropic model id for the agent solver. Default {AGENT_DEFAULT_MODEL!r}."
        ),
    )
    parser.add_argument(
        "--nl-gen-model",
        default=None,
        help=(
            "Optional override for the NL-gen pipeline (only used when "
            "regenerating). Default is each generator's configured "
            "model (opus 4.7)."
        ),
    )
    parser.add_argument(
        "--anthropic-base-url",
        default=None,
        help=(
            "Anthropic-compatible /v1/messages base URL (LM Studio, "
            "LiteLLM proxy). Default uses the Anthropic API directly. "
            "Applied to both NL-gen and agent solver unless "
            "--nl-gen-base-url is also set."
        ),
    )
    parser.add_argument(
        "--nl-gen-base-url",
        default=None,
        help=(
            "Override base URL for the NL-gen pipeline only. Lets you "
            "route NL-gen through one endpoint (e.g. Anthropic direct) "
            "and the agent solver through another (e.g. a local LiteLLM "
            "proxy). When omitted, --anthropic-base-url applies to both."
        ),
    )
    parser.add_argument(
        "--nl-gen-direct",
        action="store_true",
        help=(
            "Force NL-gen to use the real Anthropic API directly, even "
            "when --anthropic-base-url is set for the agent solver. "
            "Shorthand for the hybrid pattern: frontier NL-gen + local "
            "agent solver."
        ),
    )
    parser.add_argument(
        "--openai-base-url",
        default=None,
        help=(
            "OpenAI-compatible /v1 base URL for the agent solver (Ollama "
            "direct: http://<host>:11434/v1, LM Studio: "
            "http://<host>:1234/v1). When set, the agent solver uses "
            "AsyncOpenAI + chat.completions instead of AsyncAnthropic. "
            "No API key needed for local endpoints — set "
            "OPENAI_API_KEY=dummy or leave unset. NL-gen still uses the "
            "Anthropic client (or --from-fixtures to skip it entirely)."
        ),
    )
    parser.add_argument(
        "--include-outcomes",
        action="store_true",
        help=(
            "Include the per-case outcomes (with agent rationales) in "
            "the JSON output. Default omits to keep stdout small."
        ),
    )
    parser.add_argument(
        "--max-tokens-per-workflow",
        type=_positive_int,
        default=None,
        help=(
            "A4.5 cost guardrail. Cap on cumulative input+output tokens "
            "per workflow (across all agent-solver calls). When the cap "
            "is crossed the run aborts with a non-zero exit code; the "
            "operator sees what was spent before the abort fired. Cap "
            "is gross-token, not billable-token (cache reads not "
            "deducted). Default unset (no cap)."
        ),
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print the per-workflow JSON output (2-space indent).",
    )
    return parser.parse_args(argv)


async def _materialize_quartet(
    workflow_id: str,
    *,
    client,
    nl_gen_client,
    from_fixtures: bool,
    nl_gen_model: str | None,
):
    """Acquire (spec, plan, case_set, metric) for `workflow_id`.

    Returns a 4-tuple. From-fixtures path is sync; regenerate path is async.
    `nl_gen_client` may differ from `client` when --nl-gen-base-url is set.
    """
    if from_fixtures:
        return (
            FIXTURES[workflow_id],
            SIM_PLAN_FIXTURES[workflow_id],
            EVAL_CASE_SET_FIXTURES[workflow_id],
            METRIC_FIXTURES[workflow_id],
        )
    description = DESCRIPTIONS[workflow_id]
    pipeline = await generate_full_pipeline(
        nl_gen_client, description, model=nl_gen_model
    )
    return (
        pipeline.workflow_spec,
        pipeline.simulation_plan,
        pipeline.eval_case_set,
        pipeline.metric_definition,
    )


def _truncate_case_set(case_set, max_cases: int | None):
    """Drop trailing cases until at most `max_cases` remain.

    Re-validates via `EvalCaseSet.model_validate` so the balanced-classes
    rule still fires; raises a clear message if the cap dropped one
    class below the minimum.
    """
    if max_cases is None or max_cases >= len(case_set.cases):
        return case_set
    truncated = case_set.model_copy(
        update={"cases": list(case_set.cases[:max_cases])}
    )
    # Re-validate via a fresh round-trip — model_copy bypasses validators.
    return EvalCaseSet.model_validate(truncated.model_dump())


async def _smoke_one(
    workflow_id: str,
    *,
    client,
    nl_gen_client,
    openai_client,
    from_fixtures: bool,
    max_cases: int | None,
    model: str,
    nl_gen_model: str | None,
    max_tokens_per_workflow: int | None,
) -> tuple[EvalRunReport, float, TokenBudget | None]:
    """Run the gate for one workflow; return (report, wall_seconds, budget).

    The budget is returned so the CLI can surface usage even on the
    happy path (under-cap runs print spend; over-cap runs surface it
    on the typed exception).
    """
    started = time.perf_counter()
    spec, plan, case_set, metric = await _materialize_quartet(
        workflow_id,
        client=client,
        nl_gen_client=nl_gen_client,
        from_fixtures=from_fixtures,
        nl_gen_model=nl_gen_model,
    )
    case_set = _truncate_case_set(case_set, max_cases)
    budget = (
        TokenBudget(max_tokens=max_tokens_per_workflow)
        if max_tokens_per_workflow is not None
        else None
    )
    report = await run_with_agent(
        case_set, plan, spec, metric,
        client=client, model=model,
        openai_client=openai_client,
        budget=budget,
    )
    return report, time.perf_counter() - started, budget


def _serialize(
    report: EvalRunReport,
    *,
    wall_seconds: float,
    workflow_id: str,
    pretty: bool,
    include_outcomes: bool,
    budget: TokenBudget | None,
) -> str:
    payload = report.to_dict()
    if not include_outcomes:
        payload.pop("outcomes", None)
    payload["workflow_id"] = workflow_id
    payload["wall_seconds"] = round(wall_seconds, 3)
    if budget is not None:
        payload["token_budget"] = {
            "max_tokens": budget.max_tokens,
            "used_input": budget.used_input,
            "used_output": budget.used_output,
            "used_total": budget.used_total,
            "n_calls": budget.n_calls,
        }
    if pretty:
        return json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=True)
    return json.dumps(payload, sort_keys=True, ensure_ascii=True)


def _print_config(ns: argparse.Namespace, workflows: list[str]) -> None:
    """Stderr-only banner so the operator sees the cost surface."""
    mode = "from-fixtures" if ns.from_fixtures else "live-regenerate"
    print(
        f"[nl-gen-smoketest] mode={mode} model={ns.model!r} "
        f"workflows={workflows} max_cases={ns.max_cases}",
        file=sys.stderr,
    )
    if not ns.from_fixtures:
        print(
            "[nl-gen-smoketest] live NL-gen: "
            f"{_NL_GEN_STAGES * len(workflows)} pipeline calls + agent predictions; "
            "ANTHROPIC_API_KEY required.",
            file=sys.stderr,
        )


def _make_client(base_url: str | None):
    """Lazy-import AsyncAnthropic so the CLI imports without the agent extra."""
    from anthropic import AsyncAnthropic

    if base_url:
        return AsyncAnthropic(base_url=base_url)
    return AsyncAnthropic()


def _make_openai_client(base_url: str):
    """AsyncOpenAI client for Ollama / LM Studio direct calls."""
    from openai import AsyncOpenAI

    return AsyncOpenAI(base_url=base_url, api_key="dummy")


async def _async_main(ns: argparse.Namespace) -> int:
    workflows = WORKFLOW_CHOICES if ns.workflow == "all" else [ns.workflow]
    _print_config(ns, workflows)

    # Preflight: agent solver needs either Anthropic auth or --openai-base-url.
    openai_client = None
    if ns.openai_base_url:
        openai_client = _make_openai_client(ns.openai_base_url)
    elif (
        not os.environ.get("ANTHROPIC_API_KEY")
        and not ns.anthropic_base_url
    ):
        print(
            "[nl-gen-smoketest] ANTHROPIC_API_KEY is unset and neither "
            "--anthropic-base-url nor --openai-base-url was passed. "
            "Aborting before any live call.",
            file=sys.stderr,
        )
        return 2

    # Preflight: NL-gen pipeline (live mode) also needs Anthropic auth.
    if (
        not ns.from_fixtures
        and not os.environ.get("ANTHROPIC_API_KEY")
        and not ns.anthropic_base_url
        and not getattr(ns, "nl_gen_base_url", None)
        and not getattr(ns, "nl_gen_direct", False)
    ):
        print(
            "[nl-gen-smoketest] ANTHROPIC_API_KEY is unset and live NL-gen "
            "is active (--from-fixtures not passed). Aborting.",
            file=sys.stderr,
        )
        return 2

    client = _make_client(ns.anthropic_base_url)
    if ns.nl_gen_direct:
        nl_gen_client = _make_client(None)
    elif ns.nl_gen_base_url:
        nl_gen_client = _make_client(ns.nl_gen_base_url)
    else:
        nl_gen_client = client

    all_met = True
    for workflow_id in workflows:
        try:
            report, wall, budget = await _smoke_one(
                workflow_id,
                client=client,
                nl_gen_client=nl_gen_client,
                openai_client=openai_client,
                from_fixtures=ns.from_fixtures,
                max_cases=ns.max_cases,
                model=ns.model,
                nl_gen_model=ns.nl_gen_model,
                max_tokens_per_workflow=ns.max_tokens_per_workflow,
            )
        except TokenBudgetExceededError as exc:
            print(
                json.dumps(
                    {
                        "workflow_id": workflow_id,
                        "error": "token_budget_exceeded",
                        "message": str(exc),
                        "max_tokens": exc.max_tokens,
                        "used_input": exc.used_input,
                        "used_output": exc.used_output,
                        "used_total": exc.used_total,
                        "n_calls": exc.n_calls,
                        "last_label": exc.last_label,
                    },
                    sort_keys=True,
                    ensure_ascii=True,
                ),
                flush=True,
            )
            return 3
        if not report.meets_target:
            all_met = False
        print(
            _serialize(
                report,
                wall_seconds=wall,
                workflow_id=workflow_id,
                pretty=ns.pretty,
                include_outcomes=ns.include_outcomes,
                budget=budget,
            )
        )

    return 0 if all_met else 1


def main(argv: Sequence[str] | None = None) -> int:
    ns = _parse_args(argv)
    return asyncio.run(_async_main(ns))


if __name__ == "__main__":
    sys.exit(main())
