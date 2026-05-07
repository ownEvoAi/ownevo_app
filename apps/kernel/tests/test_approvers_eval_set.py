"""Tests for `approvers.eval_set` (W5.2).

Pins:
  * Cardinality: exactly 30 records.
  * Bucket distribution: 6 records per bucket × 5 buckets.
  * Domain coverage: each of the 6 domains appears once per bucket.
  * Pair-id uniqueness + format (`<bucket>:<domain>`).
  * `expected_admit` is True only for the admit bucket.
  * Each record has a non-empty rationale (for future maintainers).
  * Every `ProposalContext` is validly constructed (no `__post_init__`
    failures slip through).
  * The shared cluster_label / cluster_summary / skill_id / metric
    metadata is consistent across all six buckets for each domain
    (only the `explanation` varies — the bucket is what's being
    evaluated).
"""

from __future__ import annotations

from collections import Counter

from ownevo_kernel.approvers.eval_set import (
    JUDGE_EVAL_SET,
    JUDGE_SMOKE_SET,
    JudgeEvalPair,
)

_EXPECTED_BUCKETS = {
    "admit-structural-correct",
    "reject-vague-positive",
    "reject-wrong-direction",
    "reject-handwavy-change",
    "reject-missing-cluster",
}

_EXPECTED_DOMAINS = {
    "demand-prediction",
    "credit-risk",
    "supplier-risk",
    "fraud-review",
    "clinical-eligibility",
    "content-moderation",
}


def test_eval_set_cardinality_is_30():
    assert len(JUDGE_EVAL_SET) == 30


def test_each_record_is_a_judge_eval_pair():
    assert all(isinstance(r, JudgeEvalPair) for r in JUDGE_EVAL_SET)


# ---------------------------------------------------------------------------
# Bucket distribution
# ---------------------------------------------------------------------------


def test_five_buckets_with_six_records_each():
    counts = Counter(r.bucket_id for r in JUDGE_EVAL_SET)
    assert set(counts.keys()) == _EXPECTED_BUCKETS
    for bucket, n in counts.items():
        assert n == 6, f"bucket {bucket!r} has {n} records, expected 6"


def test_admit_bucket_has_six_admits():
    admits = [r for r in JUDGE_EVAL_SET if r.expected_admit]
    assert len(admits) == 6
    assert {r.bucket_id for r in admits} == {"admit-structural-correct"}


def test_reject_buckets_have_24_rejects():
    rejects = [r for r in JUDGE_EVAL_SET if not r.expected_admit]
    assert len(rejects) == 24
    assert {r.bucket_id for r in rejects} == (
        _EXPECTED_BUCKETS - {"admit-structural-correct"}
    )


# ---------------------------------------------------------------------------
# Pair-id uniqueness + format
# ---------------------------------------------------------------------------


def test_pair_ids_unique():
    ids = [r.pair_id for r in JUDGE_EVAL_SET]
    assert len(ids) == len(set(ids)), "duplicate pair_ids"


def test_pair_id_format_matches_bucket_and_domain():
    """`pair_id` is `<bucket-id>:<domain-slug>` so the runner can
    recover the bucket from the pair_id alone if a downstream consumer
    drops `bucket_id`."""
    for r in JUDGE_EVAL_SET:
        assert ":" in r.pair_id
        bucket, domain = r.pair_id.split(":", 1)
        assert bucket == r.bucket_id
        assert domain in _EXPECTED_DOMAINS


# ---------------------------------------------------------------------------
# Domain coverage
# ---------------------------------------------------------------------------


def test_each_domain_appears_once_per_bucket():
    """A domain-specific bias in the judge should show up across
    multiple rows in different buckets — pin that every domain × bucket
    combination is represented."""
    seen: set[tuple[str, str]] = set()
    for r in JUDGE_EVAL_SET:
        _, domain = r.pair_id.split(":", 1)
        seen.add((r.bucket_id, domain))
    assert len(seen) == 30  # 5 buckets × 6 domains
    assert {d for (_, d) in seen} == _EXPECTED_DOMAINS


# ---------------------------------------------------------------------------
# Per-record integrity
# ---------------------------------------------------------------------------


def test_every_record_has_rationale():
    """The rationale is documentation for future maintainers — pin
    that no record was added without one."""
    for r in JUDGE_EVAL_SET:
        assert r.rationale, f"pair {r.pair_id} has empty rationale"


def test_every_context_is_valid():
    """`ProposalContext.__post_init__` runs at instantiation; if any
    record had an empty field this would already have failed at
    import. Pin it explicitly so a refactor that breaks the
    constructor doesn't silently accept empty fields."""
    for r in JUDGE_EVAL_SET:
        assert r.context.proposal_id.strip()
        assert r.context.cluster_label.strip()
        assert r.context.cluster_summary.strip()
        assert r.context.skill_id.strip()
        assert r.context.metric_name.strip()
        assert r.context.explanation.strip()
        assert r.context.metric_improvement_axis in (
            "lower-is-better",
            "higher-is-better",
        )


def test_proposal_ids_unique():
    """Each pair gets a distinct (synthetic) proposal_id so a
    downstream consumer using proposal_id as the join key can't
    collide rows."""
    ids = [r.context.proposal_id for r in JUDGE_EVAL_SET]
    assert len(ids) == len(set(ids))


# ---------------------------------------------------------------------------
# Cross-bucket consistency: same domain → same metadata
# ---------------------------------------------------------------------------


def test_domain_metadata_consistent_across_buckets():
    """For each domain, only the `explanation` should vary across the
    five buckets — cluster_label / cluster_summary / skill_id /
    metric_name / metric_improvement_axis must match. If a future edit
    accidentally changes the cluster label in one bucket, the buckets
    would no longer be testing the judge's response to one domain."""
    by_domain: dict[str, list[JudgeEvalPair]] = {}
    for r in JUDGE_EVAL_SET:
        _, domain = r.pair_id.split(":", 1)
        by_domain.setdefault(domain, []).append(r)

    for domain, records in by_domain.items():
        assert len(records) == 5
        first = records[0].context
        for r in records[1:]:
            assert r.context.cluster_label == first.cluster_label, (
                f"{domain}: cluster_label drifted in bucket {r.bucket_id}"
            )
            assert r.context.cluster_summary == first.cluster_summary, (
                f"{domain}: cluster_summary drifted in bucket {r.bucket_id}"
            )
            assert r.context.skill_id == first.skill_id, (
                f"{domain}: skill_id drifted in bucket {r.bucket_id}"
            )
            assert r.context.metric_name == first.metric_name, (
                f"{domain}: metric_name drifted in bucket {r.bucket_id}"
            )
            assert (
                r.context.metric_improvement_axis
                == first.metric_improvement_axis
            ), (
                f"{domain}: metric_improvement_axis drifted in bucket "
                f"{r.bucket_id}"
            )


# ---------------------------------------------------------------------------
# Bucket-specific structural sanity (helps catch a record landing in
# the wrong bucket on a future edit)
# ---------------------------------------------------------------------------


def test_admit_explanations_reference_their_cluster():
    """Bucket A explanations all reference the cluster (verbatim or
    paraphrase). Smoke-test that the cluster_label substring appears
    in each admit-bucket explanation — if a future record drops the
    reference, the bucket label is wrong."""
    for r in JUDGE_EVAL_SET:
        if r.bucket_id != "admit-structural-correct":
            continue
        assert r.context.cluster_label in r.context.explanation, (
            f"admit pair {r.pair_id}: cluster label "
            f"{r.context.cluster_label!r} not in explanation"
        )


def test_missing_cluster_explanations_omit_cluster_label():
    """Bucket E explanations DO NOT name the cluster — that's the
    point. Smoke-test the inverse of the admit check."""
    for r in JUDGE_EVAL_SET:
        if r.bucket_id != "reject-missing-cluster":
            continue
        assert r.context.cluster_label not in r.context.explanation, (
            f"missing-cluster pair {r.pair_id}: cluster label "
            f"{r.context.cluster_label!r} unexpectedly appears in explanation"
        )


# ---------------------------------------------------------------------------
# Smoke set — 3 admit + 2 reject per PLAN.md § Week 5 5.2 smoke spec
# ---------------------------------------------------------------------------


def test_smoke_set_cardinality_is_5():
    assert len(JUDGE_SMOKE_SET) == 5


def test_smoke_set_three_admits_two_rejects():
    """PLAN.md: '5 hand-crafted proposals → judge admits 3, rejects 2'."""
    n_admit = sum(1 for r in JUDGE_SMOKE_SET if r.expected_admit)
    n_reject = sum(1 for r in JUDGE_SMOKE_SET if not r.expected_admit)
    assert n_admit == 3
    assert n_reject == 2


def test_smoke_set_includes_vague_positive_adversarial():
    """The vague-but-positive bucket is the headline adversarial case
    the smoke MUST exercise — the W5.2 PLAN names it explicitly
    ('Adversarial test: vague-but-positive → rejected'). Pin that it
    survives any future edit to the smoke subset."""
    buckets = {r.bucket_id for r in JUDGE_SMOKE_SET}
    assert "reject-vague-positive" in buckets


def test_smoke_set_includes_wrong_direction_adversarial():
    """Wrong-direction is the highest-leverage reject case (silent
    drift of the lift curve). Pin it in the smoke subset alongside
    vague-but-positive."""
    buckets = {r.bucket_id for r in JUDGE_SMOKE_SET}
    assert "reject-wrong-direction" in buckets


def test_smoke_set_records_are_subset_of_eval_set():
    """The smoke subset is a literal subset of the 30-record eval set
    (same records, smaller list) so the smoke loop and the calibrated
    grade share ground truth — no drift between the two."""
    smoke_ids = {r.pair_id for r in JUDGE_SMOKE_SET}
    eval_ids = {r.pair_id for r in JUDGE_EVAL_SET}
    assert smoke_ids.issubset(eval_ids)
