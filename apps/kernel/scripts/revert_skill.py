"""W7 slice 12 (PLAN row 7.1.13) — demo rollback runbook backing script.

Reverts a skill's `head_version_id` to a prior `version_seq` and writes
an append-only audit entry recording the rollback. Used by
`make revert-skill SKILL=<id> TO_VERSION=<n>` and documented in
`docs/runbooks/demo-rollback.md`.

The rollback does NOT delete the bad version row. The `skill_versions`
table stays an immutable history; the rollback just re-points
`skills.head_version_id` at an earlier row. A subsequent gate run can
either advance again or be reverted further.

Why this is a one-off script and not an API endpoint
----------------------------------------------------
Rollbacks are operator actions on production demo state. The MVP
contract is "human in the loop"; routing this through the web UI would
require a confirmation flow + entitlements that aren't worth the W7
budget. The runbook is an operator playbook; this script is its
mechanical action. A future "Revert" button on the skill detail page
can wrap the same SQL.

Exit codes
----------
0  rollback completed; audit entry seq printed
2  unknown skill / version / DB not configured
3  no-op (head was already at the target version) — exits clean
4  concurrent head update detected (head moved between read and write —
   safer to abort than overwrite a newer head + lie in the audit log)
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import UTC, datetime

import asyncpg

from ownevo_kernel.audit.writer import append_audit_entry
from ownevo_kernel.db import ENV_VAR


async def _run(args: argparse.Namespace) -> int:
    db_url = os.environ.get(ENV_VAR)
    if not db_url:
        print(f"error: {ENV_VAR} not set; rollback requires a live DB.",
              file=sys.stderr)
        return 2

    conn = await asyncpg.connect(db_url)
    try:
        skill = await conn.fetchrow(
            "SELECT id, kind::text AS kind, head_version_id, workflow_id "
            "FROM skills WHERE id = $1",
            args.skill,
        )
        if skill is None:
            print(f"error: skill not found: {args.skill}", file=sys.stderr)
            return 2

        target = await conn.fetchrow(
            """
            SELECT id, version_seq, content
            FROM skill_versions
            WHERE skill_id = $1 AND version_seq = $2
            """,
            args.skill,
            args.to_version,
        )
        if target is None:
            print(
                f"error: skill {args.skill} has no version_seq={args.to_version}",
                file=sys.stderr,
            )
            return 2

        current = await conn.fetchrow(
            """
            SELECT id, version_seq
            FROM skill_versions
            WHERE id = $1
            """,
            skill["head_version_id"],
        )

        if current is not None and current["version_seq"] == target["version_seq"]:
            print(
                f"no-op: skill {args.skill} head is already v{target['version_seq']}; "
                "nothing to do.",
            )
            return 3

        from_seq = current["version_seq"] if current is not None else None

        if args.dry_run:
            print(
                f"DRY RUN: would revert {args.skill} from "
                f"v{from_seq} → v{target['version_seq']}",
            )
            print(f"  reason: {args.reason}")
            print("  no DB writes performed.")
            return 0

        prior_head_id = skill["head_version_id"]
        async with conn.transaction():
            # Optimistic concurrency: only flip the head if it still
            # matches what we observed before the txn started. If a
            # gate-pass deploy advanced the head between our read and
            # this UPDATE, abort instead of silently overwriting it
            # (which would also write a stale `from_version_seq` to
            # the audit log).
            update_result = await conn.execute(
                "UPDATE skills "
                "SET head_version_id = $1, latest_proposed_version_id = $1 "
                "WHERE id = $2 AND head_version_id IS NOT DISTINCT FROM $3",
                target["id"],
                args.skill,
                prior_head_id,
            )
            # asyncpg returns a status string like 'UPDATE 1'; parse the count.
            updated = int(update_result.rsplit(" ", 1)[-1])
            if updated != 1:
                print(
                    f"abort: skill {args.skill} head changed since read "
                    f"(was v{from_seq}); rerun revert with the current head.",
                    file=sys.stderr,
                )
                return 4
            entry = await append_audit_entry(
                conn,
                kind="proposal-rolled-back",
                actor=args.actor,
                related_id=target["id"],
                payload={
                    "rollback_kind": "skill-head-revert",
                    "skill_id": args.skill,
                    "from_version_seq": from_seq,
                    "to_version_seq": target["version_seq"],
                    "reason": args.reason,
                    "applied_at": datetime.now(tz=UTC).isoformat(),
                },
            )

        print(
            f"reverted {args.skill}: v{from_seq} → v{target['version_seq']} "
            f"(audit seq {entry.seq})",
        )
        return 0
    finally:
        await conn.close()


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__.split("\n\n", 1)[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--skill", required=True,
        help="Skill id (e.g., m5.baseline.v1.feature_engineer).",
    )
    p.add_argument(
        "--to-version", required=True, type=int,
        help="version_seq to roll back to (must exist in skill_versions).",
    )
    p.add_argument(
        "--reason", required=True,
        help=(
            "Free-text reason recorded in the audit payload. "
            "Required so the rollback isn't anonymous."
        ),
    )
    p.add_argument(
        "--actor", default="human:operator",
        help=(
            "audit_entries.actor value. Default 'human:operator'; pass "
            "your real id when running for real."
        ),
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="Print the planned rollback without writing.",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
