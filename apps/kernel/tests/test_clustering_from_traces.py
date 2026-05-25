"""Tests for the production-trace → failure-cluster extractor.

Two layers: pure extraction (snapshot building from event dicts, no DB,
no clustering deps) and a DB-backed end-to-end run with stubbed
pipeline Protocols (no sentence-transformers / UMAP / HDBSCAN).
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid

import asyncpg
import numpy as np
import pytest
from ownevo_kernel.clustering.from_traces import (
    _SEV_LOGICAL,
    _SEV_SANDBOX,
    cluster_production_failures,
    extract_failure_snapshots,
)
from ownevo_kernel.clustering.types import RawClusterAssignment
from ownevo_kernel.db import ENV_VAR

# ---------------------------------------------------------------------------
# Pure extraction (no DB)
# ---------------------------------------------------------------------------


def _tool_error_event(
    *, name: str, error: str, error_class: str | None = None
) -> dict:
    return {
        "type": "tool_call_result",
        "call_id": "toolu_1",
        "name": name,
        "status": "error",
        "output": None,
        "duration_ms": 5,
        "error": error,
        "error_class": error_class,
    }


def _ok_event() -> dict:
    return {
        "type": "tool_call_result",
        "call_id": "toolu_ok",
        "name": "fetch",
        "status": "ok",
        "output": {"ok": True},
        "duration_ms": 3,
        "error": None,
        "error_class": None,
    }


def test_extract_skips_traces_without_errors() -> None:
    rows = [(uuid.uuid4(), [_ok_event()])]
    assert extract_failure_snapshots(rows) == []


def test_extract_builds_one_snapshot_per_failing_trace() -> None:
    tid = uuid.uuid4()
    rows = [(tid, [_ok_event(), _tool_error_event(name="db", error="boom")])]
    out = extract_failure_snapshots(rows)
    assert len(out) == 1
    assert out[0][0] == tid
    assert "tool=db" in out[0][1].text_signature
    assert "logical-error" in out[0][1].text_signature


def test_extract_first_error_wins() -> None:
    rows = [
        (
            uuid.uuid4(),
            [
                _tool_error_event(name="first", error="e1"),
                _tool_error_event(name="second", error="e2"),
            ],
        )
    ]
    out = extract_failure_snapshots(rows)
    assert "tool=first" in out[0][1].text_signature


def test_sandbox_error_more_severe_than_logical() -> None:
    logical = extract_failure_snapshots(
        [(uuid.uuid4(), [_tool_error_event(name="t", error="bad")])]
    )[0][1]
    sandbox = extract_failure_snapshots(
        [(uuid.uuid4(), [_tool_error_event(name="t", error="bad", error_class="Timeout")])]
    )[0][1]
    assert logical.rmsse == _SEV_LOGICAL
    assert sandbox.rmsse == _SEV_SANDBOX
    assert sandbox.rmsse > logical.rmsse


def test_signature_truncates_long_error() -> None:
    long_err = "x" * 500
    out = extract_failure_snapshots(
        [(uuid.uuid4(), [_tool_error_event(name="t", error=long_err)])]
    )
    # 80-char cap on the message portion.
    assert out[0][1].text_signature.count("x") == 80


# ---------------------------------------------------------------------------
# DB-backed end-to-end with stubbed pipeline Protocols
# ---------------------------------------------------------------------------

pytestmark = pytest.mark.skipif(
    ENV_VAR not in os.environ,
    reason=f"{ENV_VAR} not set; skipping DB-backed extractor tests",
)


class _HashEmbedder:
    def embed(self, texts):  # noqa: ANN001
        out = np.zeros((len(texts), 384), dtype=np.float32)
        for i, t in enumerate(texts):
            h = hashlib.sha256(t.encode("utf-8")).digest()
            rng = np.random.default_rng(int.from_bytes(h[:4], "big") & 0x7FFFFFFF)
            v = rng.normal(size=384).astype(np.float32)
            v /= np.linalg.norm(v) + 1e-9
            out[i] = v
        return out


class _IdentityReducer:
    def reduce(self, embeddings):  # noqa: ANN001
        return embeddings


class _SingleClusterer:
    """Assign every input to one cluster (label 0), count-agnostic.

    Deterministic and independent of how many snapshots the extractor
    produced — exercises the persist path with a single survivor.
    """

    def cluster(self, reduced):  # noqa: ANN001
        n = reduced.shape[0]
        return RawClusterAssignment(
            labels=np.zeros(n, dtype=np.int64),
            persistence={0: 0.7},
        )


class _StubLabeler:
    def label(self, texts, idx):  # noqa: ANN001
        return f"prod-cluster-{idx}"


async def _seed_workflow(db: asyncpg.Connection, wf_id: str) -> None:
    await db.execute(
        "INSERT INTO workflows (id, description, spec) "
        "VALUES ($1, 'extractor test', '{}'::jsonb) ON CONFLICT DO NOTHING",
        wf_id,
    )


async def _insert_production_trace(
    db: asyncpg.Connection, *, wf_id: str, events: list[dict]
) -> uuid.UUID:
    return await db.fetchval(
        """
        INSERT INTO traces (workflow_id, iteration_id, events, started_at, ended_at)
        VALUES ($1, NULL, $2::jsonb, now(), now())
        RETURNING id
        """,
        wf_id,
        json.dumps(events),
    )


async def test_clusters_production_failures_end_to_end(db: asyncpg.Connection) -> None:
    await _seed_workflow(db, "wf-extract")
    # Five failing traces (min_inputs gate is 5) → one cluster; plus a
    # clean trace that must be ignored.
    failing_ids = []
    for _ in range(5):
        failing_ids.append(
            await _insert_production_trace(
                db, wf_id="wf-extract",
                events=[_tool_error_event(name="forecast", error="NaN in input")],
            )
        )
    await _insert_production_trace(db, wf_id="wf-extract", events=[_ok_event()])

    persisted = await cluster_production_failures(
        db,
        "wf-extract",
        embedder=_HashEmbedder(),
        reducer=_IdentityReducer(),
        clusterer=_SingleClusterer(),
        labeler=_StubLabeler(),
    )
    assert persisted  # at least one cluster persisted

    rows = await db.fetch(
        "SELECT label, cluster_size, sample_trace_ids FROM failure_clusters "
        "WHERE workflow_id = $1",
        "wf-extract",
    )
    assert len(rows) == 1
    assert rows[0]["cluster_size"] == 5  # clean trace excluded
    # sample_trace_ids point back at the contributing production traces.
    assert set(rows[0]["sample_trace_ids"]) == set(failing_ids)


async def test_no_failures_returns_empty(db: asyncpg.Connection) -> None:
    await _seed_workflow(db, "wf-clean")
    await _insert_production_trace(db, wf_id="wf-clean", events=[_ok_event()])
    persisted = await cluster_production_failures(
        db,
        "wf-clean",
        embedder=_HashEmbedder(),
        reducer=_IdentityReducer(),
        clusterer=_SingleClusterer(),
        labeler=_StubLabeler(),
    )
    assert persisted == []


async def test_eval_traces_are_excluded(db: asyncpg.Connection) -> None:
    # A trace with iteration_id set is an eval trace and must not feed
    # the production extractor.
    await _seed_workflow(db, "wf-evalonly")
    iter_id = await db.fetchval(
        """
        INSERT INTO iterations (workflow_id, iteration_index, state, val_score,
                                best_ever_score_after, ended_at)
        VALUES ($1, 0, 'gate-pass'::iteration_state, 0.5, 0.5, now())
        RETURNING id
        """,
        "wf-evalonly",
    )
    await db.execute(
        """
        INSERT INTO traces (workflow_id, iteration_id, events, started_at, ended_at)
        VALUES ($1, $2, $3::jsonb, now(), now())
        """,
        "wf-evalonly",
        iter_id,
        json.dumps([_tool_error_event(name="x", error="boom")]),
    )
    persisted = await cluster_production_failures(
        db,
        "wf-evalonly",
        embedder=_HashEmbedder(),
        reducer=_IdentityReducer(),
        clusterer=_SingleClusterer(),
        labeler=_StubLabeler(),
    )
    assert persisted == []
