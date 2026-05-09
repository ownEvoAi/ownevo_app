"""Approval service — proposal state-machine transitions from gate-passed.

Implements the human/LLM-judge/autonomous approve + reject transitions
locked in `docs/STATE_MACHINES.md`. Every transition writes a row in
`approvals` plus an `audit_entries` entry; reject + comment also seeds
an eval case (provenance=rejected-feedback) so the rejection comment
becomes a regression test the gate checks on the next iteration.

Used by:
  * `apps/kernel/src/ownevo_kernel/api/` — FastAPI endpoints (W2.5)
  * Future autonomous deploy paths (workflow.mode='autonomous')
  * Future LLM-judge variant (benchmark approval automation)
"""

from .deploy import deploy_proposal, rollback_proposal
from .service import (
    ApprovalStateError,
    ProposalNotFoundError,
    approve_proposal,
    reject_proposal,
)

__all__ = [
    "ApprovalStateError",
    "ProposalNotFoundError",
    "approve_proposal",
    "deploy_proposal",
    "reject_proposal",
    "rollback_proposal",
]
