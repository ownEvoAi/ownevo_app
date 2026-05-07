"""Cluster-label LLM eval (B3.5 — D4).

The W3 Track B quality gate above the production `Labeler`: a Claude
judge reads (members, ground_truth_label, candidate_label) and decides
whether the candidate is semantically equivalent to the human-authored
ground-truth. Catches "the labeler hallucinated 'pet supplies' for a
cluster of canned-goods under-forecasts" — failure modes that pass
`pipeline.py`'s `[:120]` length cap + non-empty check but still ship
junk to the demo cluster card.

Public surface:

  * `ClusterLabelJudgment` — the typed verdict (binary agree/disagree
    + rationale + echoed cluster_id).
  * `LabeledClusterCase` + `LABELED_CLUSTER_CASES` — 20 hand-authored
    fixtures spanning the M5 failure-mode taxonomy.
  * `judge_label_match(client, case, candidate_label)` — single-turn
    Anthropic tool-use; returns `ClusterLabelJudgment`.
  * `run_cluster_label_eval(client, label_fn, ...)` — runs labeler +
    judge across the fixture set and reports judge-vs-human agreement.
  * `wrap_sync_labeler(labeler)` — adapt a sync `Labeler` (e.g.
    `AnthropicLabeler`) to the async `LabelFn` shape the runner expects.

W3 Track B exit criterion: agreement ≥ 0.7 on the `LABELED_CLUSTER_CASES`
fixture set, judged by sonnet 4.6, labelled by haiku 4.5. The CLI
(`scripts/cluster_label_eval.py`) wires `--require-agreement` so the
nightly workflow fails CI when the gate misses.

`judge_label_match` and `run_cluster_label_eval` live in the `agent`
extra (anthropic dep). They are imported lazily so installs without
that extra don't fail at import time. The schema, fixtures, and the
runner's data classes are kernel-runtime.
"""

from .fixtures import LABELED_CLUSTER_CASES, LabeledClusterCase
from .judgment import (
    SCHEMA_VERSION,
    ClusterLabelJudgment,
    LabelVerdict,
    verdict_score,
)

__all__ = [
    # Schema
    "SCHEMA_VERSION",
    "LabelVerdict",
    "ClusterLabelJudgment",
    "verdict_score",
    # Fixtures
    "LabeledClusterCase",
    "LABELED_CLUSTER_CASES",
    # Judge — lazy (agent extra)
    "DEFAULT_MODEL",
    "DEFAULT_MAX_TOKENS",
    "TOOL_NAME",
    "TOOL_DESCRIPTION",
    "SYSTEM_PROMPT",
    "ClusterLabelEvalError",
    "ClusterLabelJudgmentValidationError",
    "NoClusterLabelToolUseError",
    "ClusterLabelIdMismatchError",
    "judge_label_match",
    # Runner — lazy (agent extra; imports judge)
    "LabelFn",
    "wrap_sync_labeler",
    "ClusterLabelEvalRecord",
    "ClusterLabelEvalReport",
    "run_cluster_label_eval",
]


_JUDGE_LAZY_NAMES = {
    "DEFAULT_MODEL",
    "DEFAULT_MAX_TOKENS",
    "TOOL_NAME",
    "TOOL_DESCRIPTION",
    "SYSTEM_PROMPT",
    "ClusterLabelEvalError",
    "ClusterLabelJudgmentValidationError",
    "NoClusterLabelToolUseError",
    "ClusterLabelIdMismatchError",
    "judge_label_match",
}

_RUNNER_LAZY_NAMES = {
    "LabelFn",
    "wrap_sync_labeler",
    "ClusterLabelEvalRecord",
    "ClusterLabelEvalReport",
    "run_cluster_label_eval",
}


def __getattr__(name: str):  # pragma: no cover - thin lazy-import shim
    if name in _JUDGE_LAZY_NAMES:
        from . import judge

        return getattr(judge, name)
    if name in _RUNNER_LAZY_NAMES:
        from . import runner

        return getattr(runner, name)
    raise AttributeError(name)
