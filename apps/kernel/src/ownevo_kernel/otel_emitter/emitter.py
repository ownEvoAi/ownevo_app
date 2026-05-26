"""OTel emitter — core span construction and export (Track 17.2.3).

`OtelEmitter` wraps the OpenTelemetry SDK to emit ownEvo analysis events
as OTLP spans.  Each `emit_*` method corresponds to one row in the span
name table in ``docs/OTEL_EMITTER_CONVENTIONS.md``.

The emitter is:

* **Opt-in** — a no-op when `OtelEmitterConfig.endpoint` is unset.
* **Non-blocking** — spans are exported via the SDK's `BatchSpanProcessor`,
  which queues them and flushes on a background thread.
* **Configurable** — endpoint, headers, service name, and timeout are all
  env-var driven.
* **Testable** — the constructor accepts an injected `SpanExporter` so
  unit tests can capture spans without a network call.

Error handling: export failures are swallowed (the OTel SDK logs them
internally) so an unreachable backend never crashes the kernel.

Dependencies: requires ``opentelemetry-sdk>=1.36`` and
``opentelemetry-exporter-otlp-proto-http>=1.36``.  These are in the
``otel-emit`` optional extra.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from opentelemetry.sdk.trace.export import SpanExporter

_log = logging.getLogger(__name__)

# Env-var names (documented in OTEL_EMITTER_CONVENTIONS.md).
_ENV_ENDPOINT = "OWNEVO_OTEL_ENDPOINT"
_ENV_HEADERS = "OWNEVO_OTEL_HEADERS"
_ENV_SERVICE = "OWNEVO_OTEL_SERVICE_NAME"
_ENV_TIMEOUT = "OWNEVO_OTEL_TIMEOUT_SECONDS"

_DEFAULT_SERVICE = "ownevo"
_DEFAULT_TIMEOUT = 5


@dataclass
class OtelEmitterConfig:
    """Configuration for the OTel emitter."""

    endpoint: str | None = None
    """OTLP HTTP endpoint.  Empty / None disables the emitter."""

    headers: dict[str, str] = field(default_factory=dict)
    """Extra HTTP headers sent with every export request."""

    service_name: str = _DEFAULT_SERVICE
    """``service.name`` resource attribute."""

    timeout_seconds: int = _DEFAULT_TIMEOUT
    """Per-export HTTP timeout in seconds."""

    @classmethod
    def from_env(cls) -> OtelEmitterConfig:
        """Build config from environment variables."""
        endpoint = os.environ.get(_ENV_ENDPOINT, "").strip() or None

        raw_headers = os.environ.get(_ENV_HEADERS, "")
        headers: dict[str, str] = {}
        for part in raw_headers.split(","):
            part = part.strip()
            if "=" in part:
                k, _, v = part.partition("=")
                headers[k.strip()] = v.strip()

        service_name = os.environ.get(_ENV_SERVICE, _DEFAULT_SERVICE).strip() or _DEFAULT_SERVICE

        try:
            timeout_seconds = int(os.environ.get(_ENV_TIMEOUT, str(_DEFAULT_TIMEOUT)))
        except ValueError:
            timeout_seconds = _DEFAULT_TIMEOUT

        return cls(
            endpoint=endpoint,
            headers=headers,
            service_name=service_name,
            timeout_seconds=timeout_seconds,
        )


def _parse_version() -> str:
    """Return the ownevo-kernel package version, or 'unknown' if not installed."""
    try:
        from importlib.metadata import version
        return version("ownevo-kernel")
    except Exception:
        return "unknown"


class _NullEmitter:
    """Drop-in replacement used when the OTel emitter is disabled."""

    def __getattr__(self, name: str) -> Any:
        # All emit_* methods return None; any attribute access is a no-op callable.
        def _noop(*args: Any, **kwargs: Any) -> None:
            return None

        return _noop


class OtelEmitter:
    """Emits ownEvo analysis events as OTLP spans.

    Instantiate once and reuse.  In production, use the singleton returned
    by `get_emitter()`.  In tests, inject a custom `SpanExporter` via the
    `exporter` parameter to capture emitted spans.

    When `config.endpoint` is None, all `emit_*` calls are no-ops.
    """

    def __init__(
        self,
        config: OtelEmitterConfig | None = None,
        *,
        exporter: SpanExporter | None = None,
    ) -> None:
        self._config = config or OtelEmitterConfig.from_env()
        self._tracer = None
        self._sdk_available = True

        if not self._config.endpoint and exporter is None:
            # Emitter explicitly disabled.
            self._tracer = None
            return

        try:
            self._tracer = self._build_tracer(exporter)
        except ImportError:
            self._sdk_available = False
            _log.warning(
                "otel-emitter: opentelemetry-sdk not installed — emitter disabled. "
                "Install ownevo-kernel[otel-emit] to enable span export."
            )
            self._tracer = None

    def _build_tracer(self, exporter: SpanExporter | None = None):  # type: ignore[return]
        """Build and configure an OTel TracerProvider + tracer.

        Deferred import so the module can be imported without the SDK
        installed (the emitter just becomes a no-op in that case).
        """
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        resource = Resource.create({
            "service.name": self._config.service_name,
            "service.version": _parse_version(),
            "ownevo.schema.version": "1.4",
        })
        provider = TracerProvider(resource=resource)

        if exporter is not None:
            # Injected exporter (tests): use synchronous processor for
            # deterministic capture.
            from opentelemetry.sdk.trace.export import SimpleSpanProcessor
            provider.add_span_processor(SimpleSpanProcessor(exporter))
        elif self._config.endpoint:
            try:
                from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                    OTLPSpanExporter,
                )

                otlp_exporter = OTLPSpanExporter(
                    endpoint=self._config.endpoint,
                    headers=self._config.headers,
                    timeout=self._config.timeout_seconds,
                )
                provider.add_span_processor(BatchSpanProcessor(otlp_exporter))
            except ImportError as exc:
                raise ImportError(
                    "opentelemetry-exporter-otlp-proto-http is required for OTLP export. "
                    "Install ownevo-kernel[otel-emit]."
                ) from exc

        # Use the provider directly instead of the global singleton so that
        # multiple OtelEmitter instances (e.g., in tests) each maintain their
        # own isolated tracer without conflicting over the process-wide
        # `opentelemetry.trace` global.
        return provider.get_tracer("ownevo.kernel", _parse_version())

    def _span(self, name: str, attributes: dict[str, Any]) -> None:
        """Create a completed span with `attributes` and mark it OK."""
        if self._tracer is None:
            return
        try:
            from opentelemetry.trace import SpanKind, StatusCode

            with self._tracer.start_as_current_span(
                name,
                kind=SpanKind.INTERNAL,
            ) as span:
                for k, v in attributes.items():
                    if v is not None:
                        span.set_attribute(k, v)
                span.set_status(StatusCode.OK)
        except Exception:  # noqa: BLE001
            _log.debug("otel-emitter: failed to emit span %r", name, exc_info=True)

    def _span_error(self, name: str, attributes: dict[str, Any], exc: Exception) -> None:
        """Create a completed span with ERROR status and an exception event."""
        if self._tracer is None:
            return
        try:
            from opentelemetry.trace import SpanKind, StatusCode

            with self._tracer.start_as_current_span(name, kind=SpanKind.INTERNAL) as span:
                for k, v in attributes.items():
                    if v is not None:
                        span.set_attribute(k, v)
                span.record_exception(exc)
                span.set_status(StatusCode.ERROR, str(exc))
        except Exception:  # noqa: BLE001
            _log.debug("otel-emitter: failed to emit error span %r", name, exc_info=True)

    # ------------------------------------------------------------------
    # emit_* public surface
    # ------------------------------------------------------------------

    def emit_cluster_created(
        self,
        *,
        workflow_id: str,
        cluster_count: int,
        failure_count: int,
        algorithm: str = "hdbscan",
        embedder: str = "all-MiniLM-L6-v2",
    ) -> None:
        """Emit ``ownevo.cluster.created``."""
        self._span(
            "ownevo.cluster.created",
            {
                "ownevo.event.kind": "cluster.created",
                "ownevo.workflow.id": workflow_id,
                "ownevo.cluster.count": cluster_count,
                "ownevo.failure.count": failure_count,
                "ownevo.cluster.algorithm": algorithm,
                "ownevo.cluster.embedder": embedder,
            },
        )

    def emit_proposal_generated(
        self,
        *,
        workflow_id: str,
        proposal_id: str,
        iteration_id: str,
        kind: str,
        cluster_count: int = 0,
    ) -> None:
        """Emit ``ownevo.proposal.generated``."""
        self._span(
            "ownevo.proposal.generated",
            {
                "ownevo.event.kind": "proposal.generated",
                "ownevo.workflow.id": workflow_id,
                "ownevo.iteration.id": iteration_id,
                "ownevo.proposal.id": proposal_id,
                "ownevo.proposal.kind": kind,
                "ownevo.proposal.cluster_count": cluster_count,
            },
        )

    def emit_approval_recorded(
        self,
        *,
        workflow_id: str,
        proposal_id: str,
        decision: str,
        approver_type: str,
    ) -> None:
        """Emit ``ownevo.approval.recorded``."""
        self._span(
            "ownevo.approval.recorded",
            {
                "ownevo.event.kind": "approval.recorded",
                "ownevo.workflow.id": workflow_id,
                "ownevo.proposal.id": proposal_id,
                "ownevo.approval.decision": decision,
                "ownevo.approval.approver_type": approver_type,
            },
        )

    def emit_gate_passed(
        self,
        *,
        workflow_id: str,
        proposal_id: str,
        iteration_id: str,
        baseline_score: float,
        candidate_score: float,
    ) -> None:
        """Emit ``ownevo.gate.passed``."""
        self._span(
            "ownevo.gate.passed",
            {
                "ownevo.event.kind": "gate.passed",
                "ownevo.workflow.id": workflow_id,
                "ownevo.proposal.id": proposal_id,
                "ownevo.iteration.id": iteration_id,
                "ownevo.gate.result": "pass",
                "ownevo.gate.baseline_score": baseline_score,
                "ownevo.gate.candidate_score": candidate_score,
                "ownevo.gate.delta": round(candidate_score - baseline_score, 6),
            },
        )

    def emit_gate_blocked(
        self,
        *,
        workflow_id: str,
        proposal_id: str,
        iteration_id: str,
        result: str,
        baseline_score: float,
        candidate_score: float,
    ) -> None:
        """Emit ``ownevo.gate.blocked``."""
        self._span(
            "ownevo.gate.blocked",
            {
                "ownevo.event.kind": "gate.blocked",
                "ownevo.workflow.id": workflow_id,
                "ownevo.proposal.id": proposal_id,
                "ownevo.iteration.id": iteration_id,
                "ownevo.gate.result": result,
                "ownevo.gate.baseline_score": baseline_score,
                "ownevo.gate.candidate_score": candidate_score,
                "ownevo.gate.delta": round(candidate_score - baseline_score, 6),
            },
        )

    def emit_iteration_started(
        self,
        *,
        workflow_id: str,
        iteration_id: str,
        iteration_index: int,
    ) -> None:
        """Emit ``ownevo.iteration.started``."""
        self._span(
            "ownevo.iteration.started",
            {
                "ownevo.event.kind": "iteration.started",
                "ownevo.workflow.id": workflow_id,
                "ownevo.iteration.id": iteration_id,
                "ownevo.iteration.index": iteration_index,
            },
        )

    def emit_iteration_completed(
        self,
        *,
        workflow_id: str,
        iteration_id: str,
        iteration_index: int,
        state: str,
        duration_ms: int,
    ) -> None:
        """Emit ``ownevo.iteration.completed``."""
        self._span(
            "ownevo.iteration.completed",
            {
                "ownevo.event.kind": "iteration.completed",
                "ownevo.workflow.id": workflow_id,
                "ownevo.iteration.id": iteration_id,
                "ownevo.iteration.index": iteration_index,
                "ownevo.iteration.state": state,
                "ownevo.iteration.duration_ms": duration_ms,
            },
        )

    def emit_eval_run(
        self,
        *,
        workflow_id: str,
        case_count: int,
        pass_count: int,
        score: float,
        fold: str,
    ) -> None:
        """Emit ``ownevo.eval.run``."""
        self._span(
            "ownevo.eval.run",
            {
                "ownevo.event.kind": "eval.run",
                "ownevo.workflow.id": workflow_id,
                "ownevo.eval.case_count": case_count,
                "ownevo.eval.pass_count": pass_count,
                "ownevo.eval.score": score,
                "ownevo.eval.fold": fold,
            },
        )

    def emit_eval_case_graded(
        self,
        *,
        workflow_id: str,
        case_id: str,
        passed: bool,
        score: float,
    ) -> None:
        """Emit ``ownevo.eval.case.graded``."""
        self._span(
            "ownevo.eval.case.graded",
            {
                "ownevo.event.kind": "eval.case.graded",
                "ownevo.workflow.id": workflow_id,
                "ownevo.eval.case_id": case_id,
                "ownevo.eval.passed": passed,
                "ownevo.eval.score": score,
            },
        )

    def emit_skill_deployed(
        self,
        *,
        workflow_id: str,
        skill_id: str,
        version: int,
        kind: str,
    ) -> None:
        """Emit ``ownevo.skill.deployed``."""
        self._span(
            "ownevo.skill.deployed",
            {
                "ownevo.event.kind": "skill.deployed",
                "ownevo.workflow.id": workflow_id,
                "ownevo.skill.id": skill_id,
                "ownevo.skill.version": version,
                "ownevo.skill.kind": kind,
            },
        )

    def emit_skill_rolled_back(
        self,
        *,
        workflow_id: str,
        skill_id: str,
        version: int,
        kind: str,
    ) -> None:
        """Emit ``ownevo.skill.rolled_back``."""
        self._span(
            "ownevo.skill.rolled_back",
            {
                "ownevo.event.kind": "skill.rolled_back",
                "ownevo.workflow.id": workflow_id,
                "ownevo.skill.id": skill_id,
                "ownevo.skill.version": version,
                "ownevo.skill.kind": kind,
            },
        )

    def emit_trace_ingested(
        self,
        *,
        workflow_id: str | None,
        event_count: int,
        warning_count: int,
        has_failure: bool,
    ) -> None:
        """Emit ``ownevo.trace.ingested``."""
        attrs: dict[str, Any] = {
            "ownevo.event.kind": "trace.ingested",
            "ownevo.trace.event_count": event_count,
            "ownevo.trace.warning_count": warning_count,
            "ownevo.trace.has_failure": has_failure,
        }
        if workflow_id:
            attrs["ownevo.workflow.id"] = workflow_id
        self._span("ownevo.trace.ingested", attrs)

    def emit_trigger_fired(
        self,
        *,
        workflow_id: str,
        trigger_id: str,
        trigger_kind: str,
        trigger_action: str,
        trigger_status: str,
    ) -> None:
        """Emit ``ownevo.trigger.fired``."""
        self._span(
            "ownevo.trigger.fired",
            {
                "ownevo.event.kind": "trigger.fired",
                "ownevo.workflow.id": workflow_id,
                "ownevo.trigger.id": trigger_id,
                "ownevo.trigger.kind": trigger_kind,
                "ownevo.trigger.action": trigger_action,
                "ownevo.trigger.status": trigger_status,
            },
        )

    def emit_design_completed(
        self,
        *,
        workflow_id: str,
        domain: str,
    ) -> None:
        """Emit ``ownevo.design.completed``."""
        self._span(
            "ownevo.design.completed",
            {
                "ownevo.event.kind": "design.completed",
                "ownevo.workflow.id": workflow_id,
                "ownevo.workflow.domain": domain,
            },
        )

    def emit_sandbox_run(
        self,
        *,
        workflow_id: str,
        exit_code: int,
        duration_ms: int,
        error_class: str | None = None,
    ) -> None:
        """Emit ``ownevo.sandbox.run``."""
        attrs: dict[str, Any] = {
            "ownevo.event.kind": "sandbox.run",
            "ownevo.workflow.id": workflow_id,
            "ownevo.sandbox.exit_code": exit_code,
            "ownevo.sandbox.duration_ms": duration_ms,
        }
        if error_class:
            attrs["ownevo.sandbox.error_class"] = error_class
        self._span("ownevo.sandbox.run", attrs)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_singleton: OtelEmitter | None = None


def get_emitter() -> OtelEmitter:
    """Return the module-level OtelEmitter singleton.

    Initialised lazily on first call from `OtelEmitterConfig.from_env()`.
    Call `reset_emitter()` in tests to inject a fresh instance.
    """
    global _singleton
    if _singleton is None:
        _singleton = OtelEmitter()
    return _singleton


def reset_emitter(emitter: OtelEmitter | None = None) -> None:
    """Replace the singleton (used in tests)."""
    global _singleton
    _singleton = emitter
