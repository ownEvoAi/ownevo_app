"""Trace capture pipeline.

A trace is one agent run's stream of typed `AgentEvent`s, persisted as a
JSONB array in `traces.events` (one row per agent run — see
`docs/SCHEMA.md` for the table).

Two ways to interact:

- ``trace_session(conn, workflow_id, ...)`` — async context manager. Use
  this from any code path that drives a single agent run. Auto-creates
  the trace_id, finalises on context exit (even on exception), and gives
  you `session.make_event(...)` + `session.record(...)` to append.
- ``TraceCollector`` — the class behind ``trace_session``. Use directly
  only if you need finer-grained lifecycle control (e.g. partial flush
  to disk for a multi-hour run). Normal code uses ``trace_session``.

Concurrency contract: one ``TraceCollector`` per agent run. Events
recorded inside the context are appended to an in-memory list; they are
**not** flushed per-event. If the process dies mid-run, the trace row is
never written. That's intentional: a partial trace would lie to the gate
about which cases the agent saw.

For the event schema itself (variants, fields, version), see
``packages/trace-format/SPEC.md``.
"""

from .collector import TraceCollector, trace_session

__all__ = ["TraceCollector", "trace_session"]
