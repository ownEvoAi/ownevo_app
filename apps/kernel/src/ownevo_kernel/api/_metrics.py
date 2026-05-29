"""Dependency-free Prometheus metrics for the kernel API.

Exposes a small set of operational gauges in the Prometheus text exposition
format (``text/plain; version=0.0.4``) so a scraper can answer "is the kernel
healthy and how loaded is it" without us pulling in ``prometheus_client``.
The metric set is deliberately about *our* operability — pool saturation,
sandbox admission pressure, DB reachability, uptime — not the product's own
AgentEvent observability (that is for customers, via the trace format).

``render_metrics`` is pure: it takes already-collected values and returns the
exposition text, so it is unit-testable without a running app or DB. The route
in ``app.py`` collects the values (pool stats, a cheap ``SELECT 1``) and hands
them here.
"""

from __future__ import annotations

import math

from ..jobs.metrics import REPORTED_STATUSES

# Prometheus exposition format version. Scrapers key on this content-type.
CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"


def _escape_label(v: str) -> str:
    """Escape a Prometheus label value per the text exposition format spec."""
    return v.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _fmt(value: float) -> str:
    """Render a metric value. Integers print without a trailing ``.0`` so the
    output reads cleanly; floats keep up to millisecond resolution."""
    if isinstance(value, bool):  # bool is an int subclass — guard first
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    # Non-finite values: map to Prometheus-canonical spelling so scrapers
    # don't reject the entire exposition block with a parse error.
    if not math.isfinite(value):
        if math.isnan(value):
            return "NaN"
        return "+Inf" if value > 0 else "-Inf"
    if float(value).is_integer():
        return str(int(value))
    return f"{value:.3f}"


def _metric(lines: list[str], name: str, help_text: str, mtype: str, value: float) -> None:
    lines.append(f"# HELP {name} {help_text}")
    lines.append(f"# TYPE {name} {mtype}")
    lines.append(f"{name} {_fmt(value)}")


def _labeled_metric(
    lines: list[str],
    name: str,
    help_text: str,
    mtype: str,
    samples: list[tuple[dict[str, str], float]],
) -> None:
    """Emit one metric family with labelled samples: a single HELP/TYPE pair
    followed by one ``name{label="v",...} value`` line per sample."""
    lines.append(f"# HELP {name} {help_text}")
    lines.append(f"# TYPE {name} {mtype}")
    for labels, value in samples:
        label_str = ",".join(
            f'{k}="{_escape_label(v)}"' for k, v in labels.items()
        )
        lines.append(f"{name}{{{label_str}}} {_fmt(value)}")


def render_metrics(
    *,
    uptime_seconds: float,
    db_up: bool,
    pool_size: int | None,
    pool_idle: int | None,
    sandbox_max_concurrent: int,
    jobs_by_status: dict[str, int] | None = None,
) -> str:
    """Render the kernel's operational metrics as Prometheus text.

    ``pool_size`` / ``pool_idle`` are None when no pool is attached (e.g. the
    lifespan has not run); the pool gauges are then omitted rather than
    reported as zero, so a scraper can tell "pool absent" from "pool empty".
    ``pool_in_use`` is derived as ``size - idle`` when both are known.

    ``jobs_by_status`` maps a job status to its deployment-wide row count; when
    provided, an ``ownevo_jobs{status="..."}`` gauge is emitted per status (so
    the alert series ``ownevo_jobs{status="failed"}`` always exists, even at 0).
    None (no pool, or the cross-workspace count failed) omits the block — same
    "absent vs zero" treatment as the pool gauges.
    """
    lines: list[str] = []

    _metric(lines, "ownevo_up", "1 if the API process is serving.", "gauge", True)
    _metric(
        lines,
        "ownevo_uptime_seconds",
        "Seconds since the API process started.",
        "gauge",
        uptime_seconds,
    )
    _metric(
        lines,
        "ownevo_db_up",
        "1 if a SELECT 1 against the connection pool succeeded.",
        "gauge",
        db_up,
    )

    if pool_size is not None:
        _metric(
            lines,
            "ownevo_db_pool_size",
            "Connections currently held by the asyncpg pool.",
            "gauge",
            pool_size,
        )
    if pool_idle is not None:
        _metric(
            lines,
            "ownevo_db_pool_idle",
            "Idle connections available in the asyncpg pool.",
            "gauge",
            pool_idle,
        )
    if pool_size is not None and pool_idle is not None:
        _metric(
            lines,
            "ownevo_db_pool_in_use",
            "Connections checked out of the asyncpg pool right now.",
            "gauge",
            max(0, pool_size - pool_idle),
        )

    _metric(
        lines,
        "ownevo_sandbox_max_concurrent",
        "Process-global cap on concurrent sandbox runs.",
        "gauge",
        sandbox_max_concurrent,
    )

    if jobs_by_status is not None:
        _labeled_metric(
            lines,
            "ownevo_jobs",
            "Durable job-queue rows by status "
            "(queued/running are backlog/in-flight; failed is cumulative).",
            "gauge",
            [
                ({"status": s}, jobs_by_status.get(s, 0))
                for s in REPORTED_STATUSES
            ],
        )

    # Trailing newline: Prometheus tolerates its absence, but most exporters
    # emit one and some line-based tooling expects it.
    return "\n".join(lines) + "\n"
