"""`/api/integrations/langsmith` — manage the LangSmith API credential.

Backs the Settings → Integrations → LangSmith page. The API key is
stored encrypted at rest (`integration_credentials`, migration 0020)
and is never returned to the client — GET reports only whether one is
configured and the last connection-test result.

The "test connection" action validates the stored key against LangSmith
with one cheap authenticated read and records the outcome, so the UI can
show whether the key still works without re-hitting the vendor on every
page load.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict

from .._integration_credentials import (
    delete_credential,
    get_credential_plaintext,
    get_credential_status,
    record_validation,
    set_credential,
)
from ..deps import ConnDep, DemoModeCheck

_log = logging.getLogger(__name__)

_PROVIDER = "langsmith"

router = APIRouter(prefix="/api/integrations", tags=["integrations"])


class LangSmithCredentialSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_key: str


class LangSmithStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    configured: bool
    last_validated_at: str | None
    validation_status: str | None


class LangSmithTestResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str  # 'ok' | 'invalid' | 'error'
    detail: str | None = None


@router.get("/langsmith", response_model=LangSmithStatus)
async def get_langsmith_status(conn: ConnDep) -> LangSmithStatus:
    """Report whether a LangSmith key is configured + its last test result."""
    s = await get_credential_status(conn, _PROVIDER)
    return LangSmithStatus(
        configured=s.configured,
        last_validated_at=s.last_validated_at.isoformat() if s.last_validated_at else None,
        validation_status=s.validation_status,
    )


@router.post("/langsmith", response_model=LangSmithStatus, status_code=status.HTTP_200_OK)
async def set_langsmith_credential(
    body: LangSmithCredentialSet,
    conn: ConnDep,
    _demo: DemoModeCheck,
) -> LangSmithStatus:
    """Store (encrypt) the LangSmith API key.

    422 on an empty key. The key is sealed via the credentials master
    key before it touches the database; a 500 here means the master key
    isn't configured (see secrets/encrypted_field.py).
    """
    key = body.api_key.strip()
    if not key:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="api_key must not be empty",
        )
    await set_credential(conn, _PROVIDER, key)
    return await get_langsmith_status(conn)


@router.delete("/langsmith", status_code=status.HTTP_204_NO_CONTENT)
async def delete_langsmith_credential(conn: ConnDep, _demo: DemoModeCheck) -> None:
    """Remove the stored LangSmith key. Idempotent (204 even if absent)."""
    await delete_credential(conn, _PROVIDER)


@router.post("/langsmith/test", response_model=LangSmithTestResult)
async def test_langsmith_credential(
    conn: ConnDep, _demo: DemoModeCheck
) -> LangSmithTestResult:
    """Validate the stored key against LangSmith and record the result.

    404 when no key is configured. Otherwise returns 'ok' (authenticated
    read succeeded), 'invalid' (key rejected), or 'error' (network / API
    failure) and stamps `validation_status` for the Settings UI.
    """
    plaintext = await get_credential_plaintext(conn, _PROVIDER)
    if plaintext is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No LangSmith credential configured",
        )

    from ...middleware.langsmith_push import (
        LangSmithAuthError,
        LangSmithPushError,
        verify_api_key,
    )

    try:
        await asyncio.to_thread(verify_api_key, api_key=plaintext)
    except LangSmithAuthError as exc:
        await record_validation(conn, _PROVIDER, "invalid")
        return LangSmithTestResult(status="invalid", detail=str(exc)[:200])
    except LangSmithPushError as exc:
        await record_validation(conn, _PROVIDER, "error")
        return LangSmithTestResult(status="error", detail=str(exc)[:200])

    await record_validation(conn, _PROVIDER, "ok")
    return LangSmithTestResult(status="ok")
