# ownEvo OTel Emitter — Span Conventions (Track 17.2.3)

The ownEvo kernel emits OpenTelemetry spans for its own analysis events to
any customer-configured OTLP backend (Datadog, Honeycomb, Jaeger, etc.).
This document is the authoritative reference for span names, attribute keys,
and semantic conventions.

---

## Overview

The emitter is opt-in: it does nothing unless `OWNEVO_OTEL_ENDPOINT` is set.
When enabled, every significant kernel event produces one OTLP span exported
via HTTP/protobuf to the configured endpoint.

```
OWNEVO_OTEL_ENDPOINT=https://api.honeycomb.io/v1/traces  # OTLP HTTP endpoint
OWNEVO_OTEL_HEADERS=x-honeycomb-team=abc123,...           # comma-separated key=value
OWNEVO_OTEL_SERVICE_NAME=ownevo                           # defaults to "ownevo"
```

Spans are sent synchronously (non-blocking via a background thread-pool
exporter) so they never add latency to the critical path.

---

## Resource attributes (on every span)

| Attribute | Type | Example | Notes |
|-----------|------|---------|-------|
| `service.name` | string | `"ownevo"` | Set from `OWNEVO_OTEL_SERVICE_NAME` |
| `service.version` | string | `"0.1.0"` | Kernel package version |
| `ownevo.schema.version` | string | `"1.4"` | WorkflowSpec schema version |

---

## Span name conventions

All ownEvo span names follow the pattern `ownevo.<domain>.<event>` in
`snake_case`.  The domain is a single noun identifying the kernel subsystem.

| Span name | Emitted by | Description |
|-----------|-----------|-------------|
| `ownevo.cluster.created` | `clustering/` | One `cluster_production_failures` run completed |
| `ownevo.proposal.generated` | `evolution/` | An instruction-edit proposal was produced |
| `ownevo.approval.recorded` | `approvals/` | A proposal was approved or rejected by a reviewer |
| `ownevo.gate.passed` | `gate/` | Regression gate passed — proposal cleared for deployment |
| `ownevo.gate.blocked` | `gate/` | Regression gate blocked the proposal |
| `ownevo.iteration.started` | `iteration_runner` | One improvement-loop iteration began |
| `ownevo.iteration.completed` | `iteration_runner` | One improvement-loop iteration finished |
| `ownevo.eval.run` | `eval_runner/` | An eval run completed across a full eval-case set |
| `ownevo.eval.case.graded` | `eval_runner/` | A single eval case was graded |
| `ownevo.skill.deployed` | `approvals/deploy` | A skill version was deployed to production |
| `ownevo.skill.rolled_back` | `approvals/deploy` | A skill deployment was rolled back |
| `ownevo.trace.ingested` | `api/routes/otel_ingest` | An OTLP trace batch was accepted |
| `ownevo.trigger.fired` | `triggers/` | A trigger dispatched its action |
| `ownevo.design.completed` | `design_agent/` | A workflow design session finished |
| `ownevo.sandbox.run` | `sandbox/` | One agent-code sandbox execution completed |

---

## Span attribute reference

### Common attributes (present on most spans)

| Attribute | Type | Example |
|-----------|------|---------|
| `ownevo.workflow.id` | string | `"wf_abc123"` |
| `ownevo.workflow.name` | string | `"demand-forecasting"` |
| `ownevo.event.kind` | string | `"cluster.created"` |

### `ownevo.cluster.created`

| Attribute | Type | Example | Notes |
|-----------|------|---------|-------|
| `ownevo.workflow.id` | string | — | — |
| `ownevo.cluster.count` | int | `5` | Number of clusters persisted |
| `ownevo.failure.count` | int | `42` | Number of failure traces clustered |
| `ownevo.cluster.algorithm` | string | `"hdbscan"` | Clustering algorithm used |
| `ownevo.cluster.embedder` | string | `"all-MiniLM-L6-v2"` | Embedding model |

### `ownevo.proposal.generated`

| Attribute | Type | Example | Notes |
|-----------|------|---------|-------|
| `ownevo.workflow.id` | string | — | — |
| `ownevo.iteration.id` | string | — | — |
| `ownevo.proposal.id` | string | — | — |
| `ownevo.proposal.kind` | string | `"instruction"` | `"instruction"` \| `"python"` \| `"composite"` |
| `ownevo.proposal.cluster_count` | int | `3` | How many clusters drove this proposal |

### `ownevo.approval.recorded`

| Attribute | Type | Example | Notes |
|-----------|------|---------|-------|
| `ownevo.workflow.id` | string | — | — |
| `ownevo.proposal.id` | string | — | — |
| `ownevo.approval.decision` | string | `"approved"` | `"approved"` \| `"rejected"` |
| `ownevo.approval.approver_type` | string | `"human"` | `"human"` \| `"llm-judge"` \| `"autonomous"` |

### `ownevo.gate.passed` / `ownevo.gate.blocked`

| Attribute | Type | Example | Notes |
|-----------|------|---------|-------|
| `ownevo.workflow.id` | string | — | — |
| `ownevo.proposal.id` | string | — | — |
| `ownevo.iteration.id` | string | — | — |
| `ownevo.gate.result` | string | `"pass"` | `"pass"` \| `"regression"` \| `"no-improvement"` \| `"sandbox-error"` |
| `ownevo.gate.baseline_score` | float | `0.72` | Baseline eval score |
| `ownevo.gate.candidate_score` | float | `0.81` | Candidate eval score |
| `ownevo.gate.delta` | float | `+0.09` | Score delta (candidate − baseline) |

### `ownevo.iteration.started` / `ownevo.iteration.completed`

| Attribute | Type | Example | Notes |
|-----------|------|---------|-------|
| `ownevo.workflow.id` | string | — | — |
| `ownevo.iteration.id` | string | — | — |
| `ownevo.iteration.index` | int | `7` | 1-based iteration counter |
| `ownevo.iteration.state` | string | `"gate-pass"` | Terminal state (completed only) |
| `ownevo.iteration.duration_ms` | int | `45000` | Wall time in milliseconds (completed only) |

### `ownevo.eval.run`

| Attribute | Type | Example | Notes |
|-----------|------|---------|-------|
| `ownevo.workflow.id` | string | — | — |
| `ownevo.eval.case_count` | int | `20` | Cases evaluated |
| `ownevo.eval.pass_count` | int | `16` | Cases that passed |
| `ownevo.eval.score` | float | `0.80` | Overall score (0–1) |
| `ownevo.eval.fold` | string | `"test"` | `"train"` \| `"test"` |

### `ownevo.eval.case.graded`

| Attribute | Type | Example | Notes |
|-----------|------|---------|-------|
| `ownevo.workflow.id` | string | — | — |
| `ownevo.eval.case_id` | string | — | — |
| `ownevo.eval.passed` | bool | `true` | — |
| `ownevo.eval.score` | float | `1.0` | Per-case score |

### `ownevo.skill.deployed` / `ownevo.skill.rolled_back`

| Attribute | Type | Example | Notes |
|-----------|------|---------|-------|
| `ownevo.workflow.id` | string | — | — |
| `ownevo.skill.id` | string | — | — |
| `ownevo.skill.version` | int | `3` | Deployed version number |
| `ownevo.skill.kind` | string | `"instruction"` | `"instruction"` \| `"python"` \| `"composite"` |

### `ownevo.trace.ingested`

| Attribute | Type | Example | Notes |
|-----------|------|---------|-------|
| `ownevo.workflow.id` | string | — | May be absent for unbound batches |
| `ownevo.trace.event_count` | int | `18` | Events in the accepted batch |
| `ownevo.trace.warning_count` | int | `0` | Warnings (unmappable spans) |
| `ownevo.trace.has_failure` | bool | `true` | Batch contained a failed tool call |

### `ownevo.trigger.fired`

| Attribute | Type | Example | Notes |
|-----------|------|---------|-------|
| `ownevo.workflow.id` | string | — | — |
| `ownevo.trigger.id` | string | — | — |
| `ownevo.trigger.kind` | string | `"cron"` | `"webhook"` \| `"cron"` \| `"threshold"` \| `"slack"` \| `"email"` \| `"calendar"` |
| `ownevo.trigger.action` | string | `"run_clustering"` | — |
| `ownevo.trigger.status` | string | `"ok"` | `"ok"` \| `"error"` |

### `ownevo.sandbox.run`

| Attribute | Type | Example | Notes |
|-----------|------|---------|-------|
| `ownevo.workflow.id` | string | — | — |
| `ownevo.sandbox.exit_code` | int | `0` | — |
| `ownevo.sandbox.duration_ms` | int | `1200` | — |
| `ownevo.sandbox.error_class` | string | `"Timeout"` | Present on failure only: `"Timeout"` \| `"OOM"` \| `"Crash"` |

---

## Span status conventions

| Condition | OTel status | Notes |
|-----------|-------------|-------|
| Action completed successfully | `OK` | — |
| Retryable transient error | `ERROR` | Set `error.type` attribute |
| Gating decision (not an error) | `OK` | Gate block is an expected outcome, not a failure |
| Unexpected exception | `ERROR` | `exception.message` event added to span |

---

## Error events

When a span's status is `ERROR`, the emitter adds a structured exception event::

```
Event name: "exception"
Attributes:
  exception.type:    <Python exception class name>
  exception.message: <str(exc)>
```

---

## Configuration reference

| Env var | Default | Description |
|---------|---------|-------------|
| `OWNEVO_OTEL_ENDPOINT` | *(none)* | OTLP HTTP endpoint. Empty = emitter disabled |
| `OWNEVO_OTEL_HEADERS` | *(none)* | Comma-separated `key=value` pairs added to every export |
| `OWNEVO_OTEL_SERVICE_NAME` | `"ownevo"` | `service.name` resource attribute |
| `OWNEVO_OTEL_TIMEOUT_SECONDS` | `5` | Per-export HTTP timeout |

---

## Conformance

The CI job `test-otel-emitter-conformance` (see `.github/workflows/ci.yml`)
runs `apps/kernel/tests/test_otel_emitter_conformance.py`, which:

1. Instantiates each event type via `OtelEmitter.emit_*` with a fake exporter.
2. Asserts that every required attribute listed in this document is present on
   the exported span.
3. Asserts that span names match the `ownevo.<domain>.<event>` pattern.
4. Asserts that `service.name` and `ownevo.event.kind` are set on every span.

Add new event types to both this document and the conformance test.
