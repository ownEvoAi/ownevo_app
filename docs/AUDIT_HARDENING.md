# Audit hardening — append-only log + SHA-256 hash chain

**Authority:** when this doc disagrees with
`apps/kernel/src/ownevo_kernel/audit/writer.py` or
`apps/kernel/src/ownevo_kernel/api/routes/audit.py`, the code wins —
update this doc to match.

`audit_entries` is the spine of the system: every state change in
proposals, iterations, skills, approvals, and gate runs writes an
entry. This doc covers the **append-only guarantee** and the
**SHA-256 hash chain** that gives the log tamper-evidence.

---

## 1. Three layers of append-only

The append-only property is enforced at **three layers**, defense in depth:

| Layer | Where | What it stops |
|---|---|---|
| **DB trigger (layer 1)** | Migration 0001 installs a trigger on `audit_entries` that raises on `UPDATE` or `DELETE`. | App-code bugs and SQL typos. |
| **Role-level grants (layer 2)** | Migration 0010 ships a `REVOKE UPDATE, DELETE ON audit_entries FROM <app_role>;` template. **Operators must edit-and-run it** with the real role name. | A bypassed trigger or an app that runs as superuser. |
| **Hash chain (layer 3)** | Migration 0009 adds `parent_hash` + `entry_hash`. Any tampering detectable via `POST /api/audit/verify`. | Direct manual mutation by anyone with DB write access. |

Layers 1 and 2 prevent mutation from happening; layer 3 makes it detectable if it does. The chain is the only one of the three that survives a malicious DBA.

See [`MIGRATIONS.md`](MIGRATIONS.md) for the exact migration text.

## 2. Canonical JSON

Every entry's hash commits to a **canonical JSON** representation of its content:

```python
canonical = json.dumps(
    {
        "actor": actor,
        "created_at": created_at.isoformat(),
        "kind": kind,
        "parent_hash": parent_hash,
        "payload": payload,
        "related_id": str(related_id) if related_id else None,
        "seq": seq,
    },
    sort_keys=True,
    separators=(",", ":"),
).encode("utf-8")
entry_hash = hashlib.sha256(canonical).hexdigest()
```

Three things make this reproducible across versions:

1. **Sorted keys** — different json libraries or insertion orders produce the same bytes.
2. **No whitespace** — `separators=(",", ":")` eliminates indent ambiguity.
3. **ISO-format timestamps from Python** — never from `NOW()` in SQL. The same Python `datetime` always renders the same string.

The chain field `parent_hash` is included; `entry_hash` itself is excluded (it would be circular).

## 3. The chain

Each entry's `parent_hash` is the `entry_hash` of the previous hashed entry. The **first** hashed entry uses a sentinel:

```python
_GENESIS_HASH = "0" * 64   # SHA-256 all-zeros
```

A fresh DB starts with `parent_hash = _GENESIS_HASH` for the first audit entry written *after* migration 0009. Entries written *before* 0009 have NULL hashes (the "pre-hash epoch") and are skipped by the chain — they exist and are exportable, they just don't carry hash continuity.

### Why "previous hashed entry" rather than "previous entry"

The writer resolves `parent_hash` by:

```sql
SELECT entry_hash FROM audit_entries
WHERE entry_hash IS NOT NULL
ORDER BY seq DESC LIMIT 1
```

So a DB that already has pre-hash entries skips them and chains the new entry to the most recent *hashed* one. After the first post-0009 entry is written, every subsequent entry chains normally.

## 4. The `verify` endpoint

`POST /api/audit/verify` runs the structural check:

- **`hash_chain_entries`** — count of entries with a non-NULL `entry_hash`.
- **`hash_chain_valid`** — every entry recomputes correctly:
  - `entry_hash == compute_entry_hash(canonical_fields)`
  - `parent_hash == previous_entry.entry_hash` (or `_GENESIS_HASH` for the first)

Both conditions must hold for the whole chain. The endpoint short-circuits on the first failure and returns the seq number of the offending entry. The endpoint is `POST` (not `GET`) because verification is non-trivially expensive on long chains — we don't want it served from the browser cache or surfaced by GET-friendly probes.

Edge cases:

| State | `hash_chain_entries` | `hash_chain_valid` |
|---|---|---|
| Empty DB (just migrated) | 0 | true (empty chain is valid by definition) |
| Pre-0009 DB (all entries have NULL hashes) | 0 | true |
| Mid-transition (some pre-0009 + some post-0009 entries) | (post-0009 count) | true if post-0009 chain is consistent |
| Tampered: someone UPDATEd an entry | n | false; offending `seq` returned |
| Tampered: someone DELETEd an entry | n-1 | false (next entry's `parent_hash` no longer matches) |

## 5. Canonical export

`export_audit_log(conn)` returns all entries in `seq` order. `to_canonical_json(entries)` serialises them to bytes using the same canonical-JSON rules as the per-entry hash. Customers can:

```python
entries = await export_audit_log(conn)
bytes_ = to_canonical_json(entries)
# Sign or hash bytes_ externally; archive verbatim.
```

The bytes are stable: a second export of an unchanged DB produces the same bytes. That's what makes "customer-owned audit log" exportable in a meaningful sense — the customer can pin the bytes and re-verify later.

## 6. Threat model

What the design covers:

- **Bug-class mutation** — silently `UPDATE audit_entries SET payload = ...` (layer 1 trigger).
- **Superuser-shell mutation** — `psql` as a privileged user (layer 2 grants, after operator runs the REVOKE).
- **Malicious DBA tampering** — detectable post-hoc by verify-chain (layer 3 hash chain).
- **Inconsistent state across replicas** — same canonical-bytes export means two replicas with identical chains produce identical exports.

What it does *not* cover:

- **Suppression at insert time** — a malicious app role can choose not to call `append_audit_entry`. The chain only proves what *was* written wasn't tampered with; it can't prove what *should have been* written.
- **Wall-clock spoofing** — `created_at` is supplied from Python, so an attacker with code-execution on the kernel host can set it freely. The chain captures the hash but doesn't bind the timestamp to an external clock.
- **Cryptographic non-repudiation** — entries aren't signed, just hashed. Anyone with DB read can re-run the verifier; nobody has a signing key whose absence would betray forgery.

Phase-2 hardening — Merkle-root + signed exports — would close (2) and (3). Tracked in [`ARCHITECTURE.md`](ARCHITECTURE.md) §9 as "Audit-chain crypto upgrade."

## 7. Operating notes

- **Don't ever modify the trigger** without simultaneously updating layer 2 grants and the verifier. The three layers presume each other.
- **Don't backfill hashes on pre-0009 rows.** The pre-hash epoch is intentional — backfilling would require trusting the data is still valid, which is what the chain is supposed to prove.
- **Post-deploy step for production**: after `make db-migrate` applies 0010, the operator must run the REVOKE manually with the real role name. Verify with `\dp audit_entries` in `psql`; the privileges column should show no `arwd` permissions for the app role, only insert+select.

Related:

- [`MIGRATIONS.md`](MIGRATIONS.md) — 0009 and 0010 detail
- [`SCHEMA.md`](SCHEMA.md) — `audit_entries` columns
- [`ARCHITECTURE.md`](ARCHITECTURE.md) §2 — audit log's role in the loop
