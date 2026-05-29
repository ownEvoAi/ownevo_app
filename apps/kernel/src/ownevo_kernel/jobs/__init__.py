"""Background job machinery — the durable job queue plus the process-level
maintenance (startup reaper) that runs once at kernel boot.
"""

from .metrics import REPORTED_STATUSES, aggregate_job_counts
from .orphan_reaper import REAPER_ACTOR, REAPER_REASON, reap_orphaned_iterations
from .queue import (
    DEFAULT_MAX_ATTEMPTS,
    claim_next_job,
    complete_job,
    count_jobs_by_status,
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
    "aggregate_job_counts",
    "REPORTED_STATUSES",
    "DEFAULT_MAX_ATTEMPTS",
    "count_jobs_by_status",
    "enqueue_job",
    "claim_next_job",
    "heartbeat_job",
    "complete_job",
    "fail_job",
    "requeue_stale_jobs",
    "JobWorker",
    "JobHandler",
]
