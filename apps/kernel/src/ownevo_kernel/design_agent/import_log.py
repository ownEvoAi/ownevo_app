"""Import-discovery log — the persisted record of trace-import authoring.

The trace-import counterpart to `log.py`. Where the written-description
surface persists a `DesignAgentLog` (discovery transcript + ambiguity
report) on `workflows.design_agent_log`, the trace-import surface
persists a `DesignAgentImportLog` on `workflows.design_agent_import_log`.

The import log carries one extra piece of state the authoring path has
no analogue for: the **reverse-discovery turn**. Before the dimension
interview runs, the design agent reads the imported traces (and any
exported agent definition) and infers a one-to-two-sentence "this agent
does X" summary; the reviewer confirms it, corrects it, or skips it. That
inference + the reviewer's decision is the auditable record of what the
imported agent was understood to do — distinct from the negotiated
success criteria captured by the discovery transcript.

Both the column write and the audit-chain mirror use the dedicated
`design-agent-negotiation-import` kind so import-originated negotiation
stays queryable apart from the written-description path's
`design-agent-negotiation` rows.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..audit.writer import append_audit_entry
from ..types import AuditKind
from .ambiguity import AmbiguityReport
from .log import DESIGN_AGENT_ACTOR, DesignAgentLogEntry

if TYPE_CHECKING:
    import asyncpg


class ReverseDiscoveryRecord(BaseModel):
    """The reverse-discovery turn and the reviewer's response to it.

    `inferred_summary` is the "this agent does X" sentence the design
    agent produced from the imported traces (and optional exported
    definition). `basis` / `source` echo how it was derived. `decision`
    records what the reviewer did with it: confirmed it verbatim,
    corrected it (then `final_definition` holds the corrected text), or
    skipped the turn entirely (`final_definition` is None).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    inferred_summary: str = Field(min_length=1, max_length=4096)
    basis: Literal["traces", "definition+traces"]
    source: Literal["llm", "fallback"]
    decision: Literal["confirmed", "corrected", "skipped"]
    final_definition: str | None = Field(default=None, max_length=16_384)

    @model_validator(mode="after")
    def _validate_decision_consistency(self) -> "ReverseDiscoveryRecord":
        if self.decision == "corrected" and not self.final_definition:
            raise ValueError(
                "final_definition is required when decision is 'corrected'"
            )
        if self.decision == "skipped" and self.final_definition is not None:
            raise ValueError(
                "final_definition must be None when decision is 'skipped'"
            )
        return self


class DesignAgentImportLog(BaseModel):
    """Persisted record of one trace-import discovery session."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    reverse_discovery: ReverseDiscoveryRecord | None = None
    discovery_transcript: tuple[DesignAgentLogEntry, ...] = Field(
        default_factory=tuple,
        max_length=20,
    )
    ambiguity_report: AmbiguityReport | None = None

    def is_empty(self) -> bool:
        """True when there is nothing worth persisting.

        The import-generate route skips the write entirely in this case so
        a bare generate (no reverse-discovery turn, no answered questions)
        doesn't leave an empty JSONB blob or audit rows behind.
        """
        return (
            self.reverse_discovery is None
            and not self.discovery_transcript
            and self.ambiguity_report is None
        )


async def persist_design_agent_import_log(
    conn: asyncpg.Connection,
    *,
    workflow_id: str,
    log: DesignAgentImportLog,
    actor: str = DESIGN_AGENT_ACTOR,
    related_id: UUID | None = None,
) -> None:
    """Write the import log to its column + the audit chain.

    Writes happen in sequence (each `append_audit_entry` takes an
    advisory lock so concurrent writers serialise correctly):

      1. UPDATE workflows.design_agent_import_log JSONB column.
      2. One `design-agent-negotiation-import` row for the
         reverse-discovery turn (if present).
      3. One `design-agent-negotiation-import` row per discovery Q/A.
      4. One `design-agent-ambiguity` row for the ambiguity report
         (if present), reusing the authoring path's ambiguity kind.

    The caller owns the transaction — typically the same transaction that
    just INSERTed the workflows row.
    """
    status = await conn.execute(
        """
        UPDATE workflows
           SET design_agent_import_log = $1::jsonb
         WHERE id = $2
        """,
        log.model_dump_json(),
        workflow_id,
    )
    if status == "UPDATE 0":
        raise ValueError(
            f"workflow {workflow_id!r} not found; design_agent_import_log not written"
        )

    if log.reverse_discovery is not None:
        rd = log.reverse_discovery
        await append_audit_entry(
            conn,
            kind=AuditKind.DESIGN_AGENT_NEGOTIATION_IMPORT,
            actor=actor,
            related_id=related_id,
            payload={
                "workflow_id": workflow_id,
                "phase": "reverse-discovery",
                "inferred_summary": rd.inferred_summary,
                "basis": rd.basis,
                "source": rd.source,
                "decision": rd.decision,
                "final_definition": rd.final_definition,
            },
        )

    for entry in log.discovery_transcript:
        await append_audit_entry(
            conn,
            kind=AuditKind.DESIGN_AGENT_NEGOTIATION_IMPORT,
            actor=actor,
            related_id=related_id,
            payload={
                "workflow_id": workflow_id,
                "phase": "negotiation",
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
                "findings": [f.model_dump() for f in log.ambiguity_report.findings],
            },
        )


def load_design_agent_import_log(
    raw: str | dict | bytes | None,
) -> DesignAgentImportLog | None:
    """Inverse of `DesignAgentImportLog.model_dump_json` for callers that
    read the JSONB column straight off the row."""
    if raw is None:
        return None
    if isinstance(raw, str):
        return DesignAgentImportLog.model_validate_json(raw)
    if isinstance(raw, (bytes, bytearray)):
        return DesignAgentImportLog.model_validate_json(raw.decode("utf-8"))
    return DesignAgentImportLog.model_validate(raw)


__all__ = [
    "DesignAgentImportLog",
    "ReverseDiscoveryRecord",
    "load_design_agent_import_log",
    "persist_design_agent_import_log",
]
