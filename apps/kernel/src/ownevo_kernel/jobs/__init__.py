"""Background job utilities — startup reapers and other process-level
maintenance that runs once at kernel boot.
"""

from .orphan_reaper import REAPER_ACTOR, REAPER_REASON, reap_orphaned_iterations

__all__ = ["reap_orphaned_iterations", "REAPER_ACTOR", "REAPER_REASON"]
