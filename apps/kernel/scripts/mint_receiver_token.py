"""Mint an OTLP receiver token.

Usage:

    OWNEVO_DATABASE_URL=postgres://... \\
    uv run --package ownevo-kernel --extra api python apps/kernel/scripts/mint_receiver_token.py \\
        --label "acme-langsmith-prod" --workflow demand-prediction

Prints the plaintext token to stdout **exactly once**. The plaintext is
not stored anywhere — only its SHA-256 hash lands in `receiver_tokens`.
If you lose it, revoke the row and mint a new one.

The token is the value to put in the customer's OTLP collector's
`Authorization: Bearer …` header. The label is recorded on the row so
``make list-receiver-tokens`` (and incident-response forensics) can map
a row back to who got it.

Pass `--workflow <workflow_id>` to bind the token to one workflow —
every batch authenticated by it will land with `traces.workflow_id`
set to that workflow. Omit the flag for a workflow-agnostic token; the
collector must then pass `?workflow_id=` per request.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

import asyncpg
from ownevo_kernel.middleware.otel_receiver import mint_token
from ownevo_kernel.tenant_session import DEFAULT_WORKSPACE_ID, set_workspace


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--label",
        required=True,
        help="Human-readable label (e.g. 'acme-langsmith-prod'). Recorded on the row.",
    )
    parser.add_argument(
        "--workflow",
        default=None,
        help=(
            "Optional workflow id to bind this token to. "
            "Workflow-bound tokens are restricted to writing into that workflow; "
            "workflow-agnostic tokens require ?workflow_id= per request."
        ),
    )
    return parser.parse_args(argv)


async def _insert(
    *,
    token_hash: str,
    label: str,
    workflow_id: str | None,
) -> str:
    db_url = os.environ.get("OWNEVO_DATABASE_URL")
    if not db_url:
        print("error: OWNEVO_DATABASE_URL not set.", file=sys.stderr)
        sys.exit(2)

    conn = await asyncpg.connect(db_url)
    try:
        await set_workspace(conn, DEFAULT_WORKSPACE_ID)
        if workflow_id is not None:
            exists = await conn.fetchval(
                "SELECT 1 FROM workflows WHERE id = $1",
                workflow_id,
            )
            if not exists:
                print(
                    f"error: workflow {workflow_id!r} not found.",
                    file=sys.stderr,
                )
                sys.exit(3)
        row = await conn.fetchrow(
            """
            INSERT INTO receiver_tokens (token_hash, workflow_id, label)
            VALUES ($1, $2, $3)
            RETURNING id::text AS id
            """,
            token_hash,
            workflow_id,
            label,
        )
        if row is None:
            raise RuntimeError("INSERT ... RETURNING returned no row")
        return row["id"]
    finally:
        await conn.close()


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    plaintext, token_hash = mint_token()
    token_id = asyncio.run(
        _insert(
            token_hash=token_hash,
            label=args.label,
            workflow_id=args.workflow,
        )
    )
    print(plaintext)
    print(
        f"\n  id={token_id}  label={args.label}  workflow={args.workflow or '(any)'}",
        file=sys.stderr,
    )
    print(
        "  store this token now — the plaintext is not recoverable.",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
