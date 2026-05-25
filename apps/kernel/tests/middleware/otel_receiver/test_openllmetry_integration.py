"""OpenLLMetry → receiver → clustering integration test (13.0.1 acceptance c).

A real traceloop-instrumented agent (offline, mocked LLM transport)
emits OTLP-protobuf to `POST /api/otel/v1/traces`; the decoded
AgentEvents land in the bound workflow's traces; the production-failure
extractor then clusters the broken-tool runs into ≥1 failure cluster.

Skipped unless the `otel-integration` extra is installed (traceloop-sdk)
and OWNEVO_DATABASE_URL is set. The clustering pipeline Protocols are
stubbed (no sentence-transformers / UMAP / HDBSCAN) so the test is
deterministic and dependency-light beyond traceloop itself.
"""

from __future__ import annotations

import hashlib
import os
import uuid

import httpx
import numpy as np
import pytest
from ownevo_kernel.db import ENV_VAR

# Skip the whole module unless traceloop (the otel-integration extra) is
# importable. importorskip raises Skipped at collection time, so the
# lazy imports of the sample helper inside the fixtures are never
# reached when the extra is absent.
pytest.importorskip("traceloop.sdk", reason="otel-integration extra not installed")

pytestmark = pytest.mark.skipif(
    ENV_VAR not in os.environ,
    reason=f"{ENV_VAR} not set; skipping OpenLLMetry integration test",
)


# --- stubbed clustering Protocols (no heavy ML deps) -----------------------


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
    def cluster(self, reduced):  # noqa: ANN001
        from ownevo_kernel.clustering.types import RawClusterAssignment

        n = reduced.shape[0]
        return RawClusterAssignment(
            labels=np.zeros(n, dtype=np.int64),
            persistence={0: 0.7},
        )


class _StubLabeler:
    def label(self, texts, idx):  # noqa: ANN001
        return f"openllmetry-cluster-{idx}"


@pytest.fixture(scope="module")
def span_exporter():
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    from ._openllmetry_sample import init_traceloop

    exporter = InMemorySpanExporter()
    init_traceloop(exporter)
    return exporter


async def test_openllmetry_agent_failures_cluster_end_to_end(
    api_client: httpx.AsyncClient,
    db,
    span_exporter,
) -> None:
    from ._openllmetry_sample import run_broken_agent

    # Seed a workflow + a receiver token bound to it.
    await db.execute(
        "INSERT INTO workflows (id, description, spec) "
        "VALUES ('wf-openllmetry', 'OpenLLMetry import', '{}'::jsonb)",
    )
    from ownevo_kernel.middleware.otel_receiver.auth import mint_token

    plaintext, token_hash = mint_token()
    await db.execute(
        "INSERT INTO receiver_tokens (token_hash, label, workflow_id) "
        "VALUES ($1, 'openllmetry-test', 'wf-openllmetry')",
        token_hash,
    )

    # Run the instrumented agent enough times to clear the min_inputs=5
    # clustering gate. Each run is one trace with a failed forecast tool.
    n_runs = 6
    trace_ids: set[str] = set()
    for _ in range(n_runs):
        pb = run_broken_agent(span_exporter)
        resp = await api_client.post(
            "/api/otel/v1/traces",
            content=pb,
            headers={
                "Authorization": f"Bearer {plaintext}",
                "Content-Type": "application/x-protobuf",
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # content_delta + tool_call_start + tool_call_result
        assert body["accepted_events"] == 3
        trace_ids.update(body["created_trace_ids"] + body["appended_trace_ids"])

    assert len(trace_ids) == n_runs

    # Each trace landed bound to the workflow with a tool error event.
    bound = await db.fetchval(
        "SELECT count(*) FROM traces WHERE workflow_id = 'wf-openllmetry'",
    )
    assert bound == n_runs

    # The production-failure extractor clusters the broken-tool runs.
    from ownevo_kernel.clustering.from_traces import cluster_production_failures

    persisted = await cluster_production_failures(
        db,
        "wf-openllmetry",
        embedder=_HashEmbedder(),
        reducer=_IdentityReducer(),
        clusterer=_SingleClusterer(),
        labeler=_StubLabeler(),
    )
    assert persisted, "expected ≥1 failure cluster from the OpenLLMetry runs"

    rows = await db.fetch(
        "SELECT cluster_size, sample_trace_ids FROM failure_clusters "
        "WHERE workflow_id = 'wf-openllmetry'",
    )
    assert len(rows) >= 1
    assert sum(r["cluster_size"] for r in rows) == n_runs
    # The cluster's sample traces are the ones we ingested.
    clustered = {str(t) for r in rows for t in r["sample_trace_ids"]}
    assert clustered == {str(uuid.UUID(t)) for t in trace_ids}
