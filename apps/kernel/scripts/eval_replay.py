"""CLI entrypoint for `make eval-replay WORKFLOW=...` (A4.3).

Replays the A4.1 eval cases for one (or all) NL-gen fixtures against
the matched A3.2 sim, scores via the A4.2 metric, and prints a
JSON-encoded `EvalRunReport` to stdout. Exit code reflects the gate's
view: 0 if every requested workflow `meets_target`, 1 otherwise.

Usage:
    uv run --directory apps/kernel python scripts/eval_replay.py \\
        --workflow demand-prediction
    uv run --directory apps/kernel python scripts/eval_replay.py \\
        --workflow all

Output is one JSON object per line (NDJSON when `--workflow all`),
sorted-keys + ASCII-escaped so the audit chain can canonicalize the
stream verbatim. `--pretty` re-emits the same content as 2-space
indented JSON for human inspection — never use `--pretty` when
piping into the audit chain.

Why this CLI is the A4.3 deliverable: PLAN.md says
"`make eval-replay WORKFLOW=demand-prediction` runs the loop end-to-end
and emits a score." This script IS that loop end-to-end against the
A4.1 fixtures — workflow → sim → eval cases → metric → score —
without an agent in the loop. When A4.4+ wires an actual agent, the
script grows a `--agent` flag that swaps `run_replay` for an
Inspect-AI-driven path; the JSON shape stays stable so the gate's
downstream consumers don't move.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Sequence

from ownevo_kernel.eval_runner import EvalRunReport, run_replay
from ownevo_kernel.nl_gen.fixtures import (
    EVAL_CASE_SET_FIXTURES,
    FIXTURES,
    METRIC_FIXTURES,
    SIM_PLAN_FIXTURES,
)

WORKFLOW_CHOICES = sorted(FIXTURES.keys())


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="eval-replay",
        description=(
            "Replay an NL-gen workflow's eval cases against its rendered "
            "sim and emit the metric score. Exit 0 iff every requested "
            "workflow meets its metric's target."
        ),
    )
    parser.add_argument(
        "--workflow",
        required=True,
        choices=[*WORKFLOW_CHOICES, "all"],
        help=(
            "Which fixture trio to replay. `all` runs every workflow and "
            "exits 0 only if every one meets its target."
        ),
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help=(
            "Pretty-print the JSON output (2-space indent). Do NOT use "
            "when piping into the audit chain — the chain canonicalizes "
            "sorted-keys compact JSON."
        ),
    )
    parser.add_argument(
        "--include-outcomes",
        action="store_true",
        help=(
            "Include the per-case outcomes array in the JSON output. "
            "Default omits it to keep the stdout small for `make eval-replay`."
        ),
    )
    return parser.parse_args(argv)


def _run_one(workflow_id: str) -> EvalRunReport:
    spec = FIXTURES[workflow_id]
    plan = SIM_PLAN_FIXTURES[workflow_id]
    case_set = EVAL_CASE_SET_FIXTURES[workflow_id]
    metric = METRIC_FIXTURES[workflow_id]
    return run_replay(case_set, plan, spec, metric)


def _serialize(report: EvalRunReport, *, pretty: bool, include_outcomes: bool) -> str:
    payload = report.to_dict()
    if not include_outcomes:
        payload.pop("outcomes", None)
    if pretty:
        return json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=True, default=str)
    return json.dumps(payload, sort_keys=True, ensure_ascii=True, default=str)


def main(argv: Sequence[str] | None = None) -> int:
    ns = _parse_args(argv)

    workflows = WORKFLOW_CHOICES if ns.workflow == "all" else [ns.workflow]

    all_met = True
    for workflow_id in workflows:
        try:
            report = _run_one(workflow_id)
        except Exception as exc:  # noqa: BLE001
            print(
                json.dumps(
                    {"workflow_spec_id": workflow_id, "error": str(exc), "meets_target": False},
                    sort_keys=True,
                    ensure_ascii=True,
                ),
                flush=True,
                file=sys.stderr,
            )
            all_met = False
            continue
        if not report.meets_target:
            all_met = False
        print(
            _serialize(
                report,
                pretty=ns.pretty,
                include_outcomes=ns.include_outcomes,
            ),
            flush=True,
        )

    return 0 if all_met else 1


if __name__ == "__main__":
    sys.exit(main())
