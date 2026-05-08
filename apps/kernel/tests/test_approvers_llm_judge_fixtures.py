"""Fixture invariant tests for the W5.2 approver eval set.

The fixtures are hand-authored — these tests pin the contract that
the CLI + runner depend on (30 cases, four buckets at the spec'd
counts, unique ids, ground-truth verdict matches the bucket's intent).
"""

from __future__ import annotations

from collections import Counter

from ownevo_kernel.approvers.llm_judge.fixtures import (
    LABELED_APPROVAL_CASES,
)


def test_thirty_cases_total():
    assert len(LABELED_APPROVAL_CASES) == 30


def test_unique_case_ids():
    ids = [c.case_id for c in LABELED_APPROVAL_CASES]
    assert len(set(ids)) == len(ids)


def test_bucket_distribution():
    distribution = Counter(c.bucket for c in LABELED_APPROVAL_CASES)
    assert distribution["structural"] == 10
    assert distribution["vague-but-positive"] == 8
    assert distribution["structural-but-wrong-direction"] == 6
    assert distribution["hand-wavy"] == 6


def test_ground_truth_matches_bucket():
    """`structural` is the only admit bucket; everything else is reject."""
    for case in LABELED_APPROVAL_CASES:
        if case.bucket == "structural":
            assert case.ground_truth_verdict == "admit", case.case_id
        else:
            assert case.ground_truth_verdict == "reject", case.case_id


def test_admit_reject_split():
    distribution = Counter(c.ground_truth_verdict for c in LABELED_APPROVAL_CASES)
    assert distribution["admit"] == 10
    assert distribution["reject"] == 20


def test_metric_direction_only_up_or_down():
    for case in LABELED_APPROVAL_CASES:
        assert case.metric_direction_expected in ("up", "down"), case.case_id


def test_explanation_is_non_empty():
    for case in LABELED_APPROVAL_CASES:
        assert case.explanation.strip(), case.case_id


def test_proposal_summary_is_non_empty():
    for case in LABELED_APPROVAL_CASES:
        assert case.proposal_summary.strip(), case.case_id


def test_cluster_name_is_non_empty():
    for case in LABELED_APPROVAL_CASES:
        assert case.cluster_name.strip(), case.case_id


def test_case_ids_are_kebab_case():
    """Lowercase letters / digits / hyphens; load-bearing for kebab-id
    pattern in the schema's proposal_id constraint (matches actor:id
    formats in the audit log)."""
    import re

    pattern = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$")
    for case in LABELED_APPROVAL_CASES:
        assert pattern.match(case.case_id), case.case_id
