"""Composed demo gate: resolve identity, then enforce budget + quota.

Routes that expose live LLM calls in demo mode (currently
``/api/design-agent/*`` and ``/api/nl-gen/generate``) take this
dependency. The gate is a no-op when ``DEMO_MODE`` is off, so the same
handler runs unchanged for local dev and production deploys.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request, Response

from ._demo_budget import get_budget_status, raise_budget_exhausted
from ._demo_identity import DemoIdentity, resolve_demo_identity
from ._demo_quota import get_quota_status, raise_quota_exhausted
from .deps import is_demo_mode


async def gate_demo_routes(
    request: Request,
    response: Response,
) -> DemoIdentity | None:
    """Combined demo gate.

    Returns the resolved :class:`DemoIdentity` when ``DEMO_MODE=true`` so
    the handler can record actual token usage after the LLM call.
    Returns ``None`` when ``DEMO_MODE`` is off — handlers should treat
    that as "no quota accounting needed."

    Connection acquisition is lazy so the gate stays a no-op (and never
    touches ``app.state.pool``) for non-demo deploys, even when wired
    into a route. That keeps existing test fixtures that don't bind a
    pool unaffected.

    Raises:
        HTTPException 502: if today's ``demo_budget_state`` row is
            flipped to ``exhausted``.
        HTTPException 429: if the visitor's daily token quota is
            exhausted.
    """
    if not is_demo_mode():
        return None
    pool = request.app.state.pool
    async with pool.acquire() as conn:
        identity = await resolve_demo_identity(request, response, conn)
        budget = await get_budget_status(conn)
        if budget.exhausted:
            raise_budget_exhausted()
        quota = await get_quota_status(conn, identity)
        if quota.exhausted:
            raise_quota_exhausted(quota, identity)
        return identity


DemoGateDep = Annotated[DemoIdentity | None, Depends(gate_demo_routes)]
