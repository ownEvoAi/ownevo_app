"""Background job machinery — the durable job queue plus the process-level
maintenance (startup reaper) that runs once at kernel boot.
"""

from .orphan_reaper import REAPER_ACTOR, REAPER_REASON, reap_orphaned_iterations
from .queue import (
    DEFAULT_MAX_ATTEMPTS,
    claim_next_job,
    complete_job,
    enqueue_job,
    fail_job,
    heartbeat_job,
    requeue_stale_jobs,
)
from .worker import JobHandler, JobWorker

__all__ = [
    "reap_orphaned_iterations",
    "REAPER_ACTOR",
    "REAPER_REASON",
    "DEFAULT_MAX_ATTEMPTS",
    "enqueue_job",
    "claim_next_job",
    "heartbeat_job",
    "complete_job",
    "fail_job",
    "requeue_stale_jobs",
    "JobWorker",
    "JobHandler",
]
