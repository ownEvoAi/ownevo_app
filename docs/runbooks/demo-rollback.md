# Demo rollback runbook

*W7 slice 12 (PLAN row 7.1.13). For when a bad skill version goes
live in the demo workspace and the lift chart goes negative the day
before the YC video record.*

This runbook re-points a skill's HEAD at an earlier `version_seq`. It
**does not** delete the bad version — `skill_versions` is append-only
history. The rollback only changes which row `skills.head_version_id`
points at, and writes an append-only audit entry recording the action.

**Time budget:** 5 minutes start to finish, including the lift-chart
verification.

---

## When to run this

Run when **all** of these are true:

1. The Health page lift chart for the demo workflow has gone
   *negative* after the most recent gate-passed iteration. (The
   regression gate is supposed to prevent this — if you're running
   the rollback, something slipped through, e.g., a metric drift on
   live data that the gate's eval set didn't catch.)
2. The bad iteration has already been deployed (`proposal.state =
   'deployed'`). If it's still `gate-passed` (awaiting human approval),
   reject it through the workspace UI instead — that's the supported
   path and writes the right audit entry.
3. The demo is live or imminent. Routine regressions are not a
   rollback case; they are a "fix the loop" case.

If any of those is false: don't run this. Triage instead.

---

## Procedure

### 1. Identify the regression in the lift chart

Open `/workspaces/acme/workflows/<wfId>` in the workspace UI and read
off:

- The newest iteration's `iteration_index` (the dot at the right of
  the chart) and its `val_score`.
- The most recent **prior** iteration where `val_score >=` the
  current chart maximum. That's the version you'll roll back to.
- Click an annotated dot (any dot where `has_approved_proposal` is
  true) to confirm which `skill_id` and `version_seq` that gate-pass
  produced. The proposal detail page surfaces both.

If the prior known-good version isn't obvious, open the per-skill
detail page (`/workspaces/acme/skills/<skill_id>`) — the version
history sidebar shows every version with its `created_by` and
`diff_summary`. Pick the version with the highest `val_score` in the
lift chart whose `version_seq` matches an entry in that history.

### 2. Dry-run the revert

```bash
make revert-skill \
    SKILL=m5.baseline.v1.feature_engineer \
    TO_VERSION=8 \
    REASON="Lift dropped 14pp after iter 47 deployed; reverting to known-good v8 ahead of YC record." \
    DRY_RUN=1
```

Expected output:

```
DRY RUN: would revert m5.baseline.v1.feature_engineer from v9 → v8
  reason: Lift dropped 14pp after iter 47 deployed; reverting to known-good v8 ahead of YC record.
  no DB writes performed.
```

If the output says **`no-op`** (current head already at the target),
the rollback isn't needed — re-check step 1.

If the output says **`error: skill not found`** or **`has no
version_seq=N`**, the inputs are wrong. Don't proceed; recheck the
skill id and version_seq from the UI.

### 3. Apply the revert

Drop the `DRY_RUN=1` flag and re-run:

```bash
make revert-skill \
    SKILL=m5.baseline.v1.feature_engineer \
    TO_VERSION=8 \
    REASON="Lift dropped 14pp after iter 47 deployed; reverting to known-good v8 ahead of YC record."
```

Expected output:

```
reverted m5.baseline.v1.feature_engineer: v9 → v8 (audit seq 312)
```

The audit entry is written inside the same DB transaction as the
`UPDATE skills SET head_version_id = ...`, so you cannot end up with
a re-pointed HEAD and no audit record.

### 4. Recompute the lift chart

The lift chart reads from the `iterations` table directly — it does
**not** cache against `skills.head_version_id`. So the chart will
reflect the rollback on the next page load, but the *new* HEAD won't
have a fresh iteration row until the next gate run. That's correct:
the chart shows what actually ran on which day; the rollback is
visible in the audit trail and on the skill detail page.

To produce a new gate run on the rolled-back HEAD before the demo
record:

```bash
# Re-run the gate against the HEAD we just rolled back to.
# This produces a fresh iterations row; the lift chart will show a
# new dot at the chart's current x position with the recovered score.
make m5-bootstrap-loop LOOP_ARGS='--max-iterations 1 --no-seed'
```

Reload `/workspaces/acme/workflows/<wfId>` — the new iteration's
`val_score` should match (within ±1pp) the val_score of the version
you rolled back to. If it doesn't, the rollback resolved a different
problem than you expected — investigate before recording.

### 5. Verify the audit entry

Open `/workspaces/acme/audit` and confirm the new row at the top is
`proposal-rolled-back`. Expand it; the payload contains
`rollback_kind = "skill-head-revert"`, `from_version_seq`,
`to_version_seq`, `reason`, and `applied_at`. The audit chain stays
contiguous — the verify-chain button continues to return `valid:
true`.

---

## What this does NOT do

- **Does not delete or modify** the rolled-back-from version row in
  `skill_versions`. That history stays intact for the audit chain
  and any future forensic re-derivation.
- **Does not roll back proposals**. The bad-version proposal stays
  in `state='deployed'`; reviewers querying the proposal detail
  surface still see it. The new audit entry is the canonical record
  that the deployed version was un-deployed.
- **Does not pause the loop**. The next gate run will start from the
  rolled-back HEAD as parent. If the underlying problem isn't fixed,
  the agent may re-propose the same bad change. **Triage the root
  cause before re-enabling autonomous loop runs.**
- **Does not roll back across multiple skills**. The rollback is
  scoped to one `skill_id`. If the regression is multi-skill,
  invoke this runbook per-skill in dependency order (data flow
  upstream → downstream).

---

## Phase-2 follow-ups (out of W7 scope)

- A "Revert" button on the skill detail page that wraps the same SQL
  + audit write behind a confirmation dialog. (Right now the revert
  is operator-only via `make`.)
- A new `audit_kind = 'skill-rolled-back'` enum value distinct from
  `proposal-rolled-back`. The current entry uses `proposal-rolled-back`
  with `payload.rollback_kind = "skill-head-revert"` to disambiguate
  — works for now, distinct enums are cleaner.
- Multi-skill atomic rollback. Today the script reverts one skill per
  invocation; correlated rollbacks need a wrapper.
