"""NL-gen meta-eval (A4.6 — D7).

The W4 quality gate above the structural validators: a Claude judge
reads (description, WorkflowSpec, SimulationPlan, EvalCaseSet,
MetricDefinition) and decides whether the bundle is fit for the
agent loop. Catches semantic gaps that pass A4.1's `extra='forbid'`
+ A4.2's direction lock — e.g. the sim is structurally valid but
the entities don't match the description, or the metric family
contradicts the documented past-miss.

Public surface:

  * `MetaEvalJudgment` — the typed verdict (3 dimensions + overall).
  * `judge_artifacts(client, description, spec, plan, case_set, metric)`
    — single-turn Anthropic tool-use; returns `MetaEvalJudgment`.
  * `corruptions` module — recipes that take a good bundle and
    produce a structurally-valid but semantically-wrong bundle.
    Drives the eval set's "bad" pairs.
  * `META_EVAL_SET` — 10 (description, good, bad, ground_truth) pairs
    (3 from production fixtures + 7 minimal new ones).
  * `run_meta_eval(client, ...)` — runs the judge across the set
    and reports judge-vs-human agreement.

Validation in W5 (A5.5): agreement ≥0.7. Until then, the eval set
is authored and the judge runs end-to-end (the A4.6 deliverable per
PLAN.md), but the threshold isn't a CI gate.

`judge_artifacts` and `run_meta_eval` live in the `agent` extra
(anthropic dep). They are imported lazily so installs without that
extra don't fail at import time. The schema, corruptions, eval set,
and the runner's data classes are kernel-runtime.
"""

from .corruptions import (
    Bundle,
    CorruptionDimension,
    CorruptionResult,
    flip_metric_direction,
    set_trivial_threshold,
    set_unreachable_threshold,
    swap_eval_cases,
    swap_metric_family_to_opposing,
    swap_sim_plan,
)
from .eval_set import META_EVAL_SET, MetaEvalPair
from .judgment import (
    SCHEMA_VERSION,
    DimensionVerdict,
    MetaEvalDimension,
    MetaEvalJudgment,
    OverallVerdict,
    dimension_score,
)
from .preview_fixtures import PREVIEW_JUDGMENT_FIXTURES

__all__ = [
    # Schema
    "SCHEMA_VERSION",
    "DimensionVerdict",
    "OverallVerdict",
    "MetaEvalDimension",
    "MetaEvalJudgment",
    "dimension_score",
    # Corruptions
    "Bundle",
    "CorruptionDimension",
    "CorruptionResult",
    "swap_sim_plan",
    "swap_eval_cases",
    "swap_metric_family_to_opposing",
    "set_unreachable_threshold",
    "set_trivial_threshold",
    "flip_metric_direction",
    # Eval set
    "MetaEvalPair",
    "META_EVAL_SET",
    # Preview fixtures (W5.5 UI badge)
    "PREVIEW_JUDGMENT_FIXTURES",
    # Judge — lazy (agent extra)
    "DEFAULT_MODEL",
    "DEFAULT_MAX_TOKENS",
    "SIM_BODY_PREVIEW_CHARS",
    "TOOL_NAME",
    "TOOL_DESCRIPTION",
    "SYSTEM_PROMPT",
    "MetaEvalJudgmentValidationError",
    "NoMetaEvalToolUseError",
    "MetaEvalSpecIdMismatchError",
    "judge_artifacts",
    # Runner — lazy (agent extra; imports judge)
    "MetaEvalRecord",
    "MetaEvalReport",
    "PairRole",
    "run_meta_eval",
]


_JUDGE_LAZY_NAMES = {
    "DEFAULT_MODEL",
    "DEFAULT_MAX_TOKENS",
    "SIM_BODY_PREVIEW_CHARS",
    "TOOL_NAME",
    "TOOL_DESCRIPTION",
    "SYSTEM_PROMPT",
    "MetaEvalJudgmentValidationError",
    "NoMetaEvalToolUseError",
    "MetaEvalSpecIdMismatchError",
    "judge_artifacts",
}

_RUNNER_LAZY_NAMES = {
    "MetaEvalRecord",
    "MetaEvalReport",
    "PairRole",
    "run_meta_eval",
}


def __getattr__(name: str):  # pragma: no cover - thin lazy-import shim
    if name in _JUDGE_LAZY_NAMES:
        from . import judge

        return getattr(judge, name)
    if name in _RUNNER_LAZY_NAMES:
        from . import runner

        return getattr(runner, name)
    raise AttributeError(name)
