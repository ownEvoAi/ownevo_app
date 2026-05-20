"""Design-agent log — the persisted record of authoring-time discovery.

Two pieces of state get persisted alongside the WorkflowSpec at
generation time:

  * **Discovery transcript** — the Q/A pairs from the conversation the
    operator ran on `/workflows/new/design`. Each entry carries the
    `question_index`, the `kind` (metric / ambiguity / trigger /
    surface / premise), the question text the design agent asked, and
    the operator's answer (or `null` for a skipped question).

  * **Ambiguity report** — the post-generation scan output from
    `design_agent.ambiguity.analyze_workflow`. Optional; absent when
    the operator either skipped the ambiguity check or no findings
    surfaced.

The full `DesignAgentLog` is stored as JSONB on
`workflows.design_agent_log` so a future Audit-tab read can render it
without joining against per-row audit entries. The audit chain still
gets one row per discovery Q/A (`design-agent-negotiation`) plus one
combined row for the ambiguity report (`design-agent-ambiguity`) so
the chain itself remains queryable as the canonical source.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from ..audit.writer import append_audit_entry
from ..types import AuditKind
from .ambiguity import AmbiguityReport
from .dimensions import DesignDimension
from .prompts._types import DiscoveryQuestionKind

if TYPE_CHECKING:
    import asyncpg

DESIGN_AGENT_ACTOR = "design-agent"
"""Audit actor for design-agent-driven entries. Mirrors the convention
in `iteration_runner` (`_ITERATION_ACTOR`) and lets the audit tab
filter or color-code by author."""


class DesignAgentLogEntry(BaseModel):
    """One question + answer pair from the discovery conversation.

    The LLM-driven interviewer (Sept 2026) routes its output through
    seven `DesignDimension`s, each of which downstream NL-gen
    generators read to shape the WorkflowSpec / SimulationPlan /
    MetricDefinition / EvalCaseSet they emit. The pre-LLM hardcoded
    walker only knew about five `DiscoveryQuestionKind`s. Both shapes
    are persisted side-by-side: `dimension` is the new canonical
    identifier (used by `nl_gen.design_brief_context`), `kind` stays
    around so the existing audit-tab and skill-detail UI keeps reading
    its color-coded chip.

    `chosen_option` carries the option the operator clicked (verbatim
    label from the LLM-emitted brief, or a hardcoded option label from
    the fallback path). `answer` is the operator's typed elaboration —
    these can both be set; the generators concatenate them.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    question_index: int = Field(ge=0)
    kind: DiscoveryQuestionKind
    question: str = Field(min_length=1, max_length=4096)
    answer: str | None = Field(
        default=None,
        max_length=4096,
        description=(
            "Operator's response. `null` means the operator skipped the "
            "question — the design agent records the skip so a future "
            "reader can see that the question was offered but declined."
        ),
    )
    # New fields (back-compat: persisted JSONB rows from before the LLM
    # interviewer carry only kind+question+answer; these stay None).
    dimension: DesignDimension | None = Field(
        default=None,
        description=(
            "Which DesignDimension this Q/A covers. Drives downstream "
            "NL-gen generator targeting — generators read only the "
            "dimensions they care about. None for legacy entries."
        ),
    )
    chosen_option: str | None = Field(
        default=None,
        max_length=4096,
        description=(
            "Verbatim label of the option the operator picked, when the "
            "question carried options. Distinct from `answer` (which is "
            "the operator's free-text elaboration)."
        ),
    )


class DesignAgentLog(BaseModel):
    """Persisted record of one authoring-time discovery session."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    discovery_transcript: tuple[DesignAgentLogEntry, ...] = Field(
        default_factory=tuple,
        max_length=20,
    )
    ambiguity_report: AmbiguityReport | None = None


async def persist_design_agent_log(
    conn: asyncpg.Connection,
    *,
    workflow_id: str,
    log: DesignAgentLog,
    actor: str = DESIGN_AGENT_ACTOR,
    related_id: UUID | None = None,
) -> None:
    """Write the log to `workflows.design_agent_log` + the audit chain.

    Three writes happen in sequence (each `append_audit_entry` takes
    an advisory lock so concurrent writers serialise correctly):

      1. UPDATE workflows.design_agent_log JSONB column.
      2. One `design-agent-negotiation` audit row per Q/A entry.
      3. One `design-agent-ambiguity` audit row carrying the ambiguity
         report (if present).

    The caller owns the transaction — typically this is the same
    transaction that just INSERTed the workflows row. Atomicity of the
    workflow-row write + log writes is the caller's responsibility.
    """
    status = await conn.execute(
        """
        UPDATE workflows
           SET design_agent_log = $1::jsonb
         WHERE id = $2
        """,
        log.model_dump_json(),
        workflow_id,
    )
    if status == "UPDATE 0":
        raise ValueError(f"workflow {workflow_id!r} not found; design_agent_log not written")

    for entry in log.discovery_transcript:
        await append_audit_entry(
            conn,
            kind=AuditKind.DESIGN_AGENT_NEGOTIATION,
            actor=actor,
            related_id=related_id,
            payload={
                "workflow_id": workflow_id,
                "question_index": entry.question_index,
                "kind": entry.kind,
                "dimension": entry.dimension,
                "question": entry.question,
                "answer": entry.answer,
                "chosen_option": entry.chosen_option,
                "skipped": entry.answer is None and entry.chosen_option is None,
            },
        )

    if log.ambiguity_report is not None:
        await append_audit_entry(
            conn,
            kind=AuditKind.DESIGN_AGENT_AMBIGUITY,
            actor=actor,
            related_id=related_id,
            payload={
                "workflow_id": workflow_id,
                "workflow_spec_id": log.ambiguity_report.workflow_spec_id,
                "high_severity_count": log.ambiguity_report.high_severity_count,
                "findings": [
                    f.model_dump()
                    for f in log.ambiguity_report.findings
                ],
            },
        )


def load_design_agent_log(raw: str | dict | None) -> DesignAgentLog | None:
    """Inverse of `DesignAgentLog.model_dump_json` for callers that read
    the JSONB column straight off the row.

    `raw` is either:
      * `None` — column is NULL, return None
      * `str` — JSON text (asyncpg returns JSONB as text by default)
      * `dict` — already-decoded mapping
    """
    if raw is None:
        return None
    if isinstance(raw, str):
        return DesignAgentLog.model_validate_json(raw)
    if isinstance(raw, (bytes, bytearray)):
        return DesignAgentLog.model_validate_json(raw.decode("utf-8"))
    return DesignAgentLog.model_validate(raw)


__all__ = [
    "DESIGN_AGENT_ACTOR",
    "DesignAgentLog",
    "DesignAgentLogEntry",
    "load_design_agent_log",
    "persist_design_agent_log",
]
