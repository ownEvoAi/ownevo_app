"""`/api/integrations/{langsmith,copilot-studio}` — manage vendor credentials.

Backs the Settings → Integrations pages. Credentials are stored encrypted
at rest (`integration_credentials`, migration 0022) and are never returned
to the client — GET reports only whether one is configured and the last
connection-test result.

LangSmith stores one scalar secret (an API key); Copilot Studio stores a
structured Entra service-principal credential (tenant / client id /
secret / environment URL) sealed as a JSON blob into the same column.

The "test connection" action validates the stored credential against the
vendor with one cheap authenticated round-trip and records the outcome,
so the UI can show whether the credential still works without re-hitting
the vendor on every page load.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Literal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .._copilot_studio_credentials import (
    get_copilot_studio_credential,
    set_copilot_studio_credential,
)
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
_COPILOT_PROVIDER = "copilot_studio"

router = APIRouter(prefix="/api/integrations", tags=["integrations"])


class LangSmithCredentialSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # LangSmith keys are ~50 chars; 4096 is generous while blocking multi-MB payloads.
    api_key: str = Field(min_length=1, max_length=4096)


class LangSmithStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    configured: bool
    last_validated_at: str | None
    validation_status: str | None


class LangSmithTestResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ok", "invalid", "error"]
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

    422 on an empty key. 503 when the server-side master encryption key
    is not configured (`OWNEVO_CREDENTIALS_MASTER_KEY` unset). The key
    is sealed at rest before it touches the database.
    """
    key = body.api_key.strip()
    if not key:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="api_key must not be empty",
        )
    from ...secrets import CredentialsKeyMissingError

    try:
        await set_credential(conn, _PROVIDER, key)
    except CredentialsKeyMissingError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Credential encryption key is not configured on this server",
        ) from None
    return await get_langsmith_status(conn)


@router.delete("/langsmith", status_code=status.HTTP_204_NO_CONTENT)
async def delete_langsmith_credential(conn: ConnDep, _demo: DemoModeCheck) -> None:
    """Remove the stored LangSmith key. Idempotent (204 even if absent)."""
    await delete_credential(conn, _PROVIDER)


@router.post("/langsmith/test", response_model=LangSmithTestResult)
async def test_langsmith_credential(conn: ConnDep, _demo: DemoModeCheck) -> LangSmithTestResult:
    """Validate the stored key against LangSmith and record the result.

    404 when no key is configured. 503 when the master encryption key is
    not configured on the server. Otherwise returns 'ok' (authenticated
    read succeeded), 'invalid' (key rejected), or 'error' (network / API
    failure) and stamps `validation_status` for the Settings UI.
    """
    from ...secrets import CredentialsDecryptError, CredentialsKeyMissingError

    try:
        plaintext = await get_credential_plaintext(conn, _PROVIDER)
    except CredentialsKeyMissingError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Credential encryption key is not configured on this server",
        ) from None
    except CredentialsDecryptError:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Stored credential could not be decrypted — re-enter the key in Settings",
        ) from None
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


# ---------------------------------------------------------------------------
# Copilot Studio — structured Entra service-principal credential
# ---------------------------------------------------------------------------


def _require_https(value: str | None) -> str | None:
    """Reject non-HTTPS URLs to prevent SSRF via http:// or other schemes.

    `environment_url` and `authority_host` are used verbatim to construct
    Power Platform API endpoints and Entra token-request URLs; an
    `http://` value would allow an admin to redirect those calls to an
    internal network address. HTTPS-only is the minimum required gate.
    """
    if value is None:
        return value
    stripped = value.strip()
    if stripped and not stripped.lower().startswith("https://"):
        raise ValueError("URL must start with https://")
    return stripped


class CopilotStudioCredentialSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # GUIDs and an org URL; 4096 each is generous while blocking junk payloads.
    tenant_id: str = Field(min_length=1, max_length=4096)
    client_id: str = Field(min_length=1, max_length=4096)
    client_secret: str = Field(min_length=1, max_length=4096)
    environment_url: str = Field(min_length=1, max_length=4096)
    # Optional override for sovereign clouds (US Gov / China). Omitted →
    # the adapter's public-cloud default.
    authority_host: str | None = Field(default=None, max_length=4096)

    @field_validator("environment_url", mode="before")
    @classmethod
    def _validate_environment_url(cls, v: str) -> str:
        result = _require_https(v)
        assert result is not None  # field is required
        return result

    @field_validator("authority_host", mode="before")
    @classmethod
    def _validate_authority_host(cls, v: str | None) -> str | None:
        return _require_https(v)


class CopilotStudioStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    configured: bool
    last_validated_at: str | None
    validation_status: str | None


class CopilotStudioTestResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ok", "invalid", "error"]
    detail: str | None = None


@router.get("/copilot-studio", response_model=CopilotStudioStatus)
async def get_copilot_studio_status(conn: ConnDep) -> CopilotStudioStatus:
    """Report whether a Copilot Studio credential is configured + last test."""
    s = await get_credential_status(conn, _COPILOT_PROVIDER)
    return CopilotStudioStatus(
        configured=s.configured,
        last_validated_at=s.last_validated_at.isoformat() if s.last_validated_at else None,
        validation_status=s.validation_status,
    )


@router.post(
    "/copilot-studio",
    response_model=CopilotStudioStatus,
    status_code=status.HTTP_200_OK,
)
async def set_copilot_studio_credential_route(
    body: CopilotStudioCredentialSet,
    conn: ConnDep,
    _demo: DemoModeCheck,
) -> CopilotStudioStatus:
    """Store (encrypt) the Copilot Studio service-principal credential.

    503 when the server-side master encryption key is not configured
    (`OWNEVO_CREDENTIALS_MASTER_KEY` unset). The credential is sealed as a
    JSON blob at rest before it touches the database.
    """
    from ...middleware.copilot_studio import CopilotStudioCredentials
    from ...middleware.copilot_studio.auth import DEFAULT_AUTHORITY_HOST
    from ...secrets import CredentialsKeyMissingError

    cred = CopilotStudioCredentials(
        tenant_id=body.tenant_id.strip(),
        client_id=body.client_id.strip(),
        client_secret=body.client_secret.strip(),
        environment_url=body.environment_url.strip(),
        authority_host=(body.authority_host or DEFAULT_AUTHORITY_HOST).strip(),
    )
    try:
        await set_copilot_studio_credential(conn, cred)
    except CredentialsKeyMissingError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Credential encryption key is not configured on this server",
        ) from None
    return await get_copilot_studio_status(conn)


@router.delete("/copilot-studio", status_code=status.HTTP_204_NO_CONTENT)
async def delete_copilot_studio_credential(conn: ConnDep, _demo: DemoModeCheck) -> None:
    """Remove the stored Copilot Studio credential. Idempotent (204 even if absent)."""
    await delete_credential(conn, _COPILOT_PROVIDER)


@router.post("/copilot-studio/test", response_model=CopilotStudioTestResult)
async def test_copilot_studio_credential(
    conn: ConnDep, _demo: DemoModeCheck
) -> CopilotStudioTestResult:
    """Validate the stored credential against Entra and record the result.

    404 when no credential is configured. 503 when the master encryption
    key is not configured on the server. Otherwise returns 'ok' (Entra
    minted a token), 'invalid' (service principal rejected), or 'error'
    (network / API failure) and stamps `validation_status` for the UI.
    """
    from ...middleware.copilot_studio import (
        CopilotStudioAuthError,
        CopilotStudioError,
        verify_connection,
    )

    cred = await load_copilot_credential_or_raise(conn)

    try:
        await verify_connection(cred)
    except CopilotStudioAuthError as exc:
        await record_validation(conn, _COPILOT_PROVIDER, "invalid")
        return CopilotStudioTestResult(status="invalid", detail=str(exc)[:200])
    except CopilotStudioError as exc:
        await record_validation(conn, _COPILOT_PROVIDER, "error")
        return CopilotStudioTestResult(status="error", detail=str(exc)[:200])

    await record_validation(conn, _COPILOT_PROVIDER, "ok")
    return CopilotStudioTestResult(status="ok")


async def load_copilot_credential_or_raise(conn: ConnDep):  # noqa: ANN202
    """Decrypt the stored Copilot Studio credential or raise the right HTTP error.

    503 when the master key is unset, 500 when the blob can't be decrypted
    or is malformed, 404 when no credential is configured. Shared by the
    test-connection and export-definition endpoints.
    """
    from ...middleware.copilot_studio import CopilotStudioConfigError
    from ...secrets import CredentialsDecryptError, CredentialsKeyMissingError

    try:
        cred = await get_copilot_studio_credential(conn)
    except CredentialsKeyMissingError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Credential encryption key is not configured on this server",
        ) from None
    except CredentialsDecryptError:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Stored credential could not be decrypted — re-enter it in Settings",
        ) from None
    except CopilotStudioConfigError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Stored credential is malformed — re-enter it in Settings: {exc}",
        ) from None
    if cred is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No Copilot Studio credential configured",
        )
    return cred


class CopilotStudioDefinitionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # The Power Platform solution (unmanaged) packaging the agent to export.
    solution_name: str = Field(min_length=1, max_length=256)


class CopilotStudioDefinitionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # The extracted instruction text, or None when the solution carries no
    # recognisable agent definition (best-effort extraction — see MAPPING.md).
    agent_definition: str | None
    found: bool


@router.post("/copilot-studio/export-definition", response_model=CopilotStudioDefinitionResult)
async def export_copilot_studio_definition(
    body: CopilotStudioDefinitionRequest,
    conn: ConnDep,
    _demo: DemoModeCheck,
) -> CopilotStudioDefinitionResult:
    """Export a solution and extract the agent's instruction text.

    Backs the trace-import connect flow: the extracted definition grounds
    the design agent's reverse-discovery turn ("this agent appears to do
    X"). 404 when no credential is configured, 401 when the service
    principal is rejected, 404 when the solution doesn't exist, 502 on a
    Power Platform / network failure. A solution with no recognisable
    definition returns `found=false` (not an error) — the caller falls
    back to the trace-only summary.
    """
    from ...middleware.copilot_studio import (
        CopilotStudioAuthError,
        CopilotStudioError,
        CopilotStudioNotFoundError,
        CopilotStudioRateLimitError,
        export_solution,
        extract_agent_definition,
    )

    cred = await load_copilot_credential_or_raise(conn)

    try:
        solution_zip = await export_solution(cred, solution_name=body.solution_name)
    except CopilotStudioAuthError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=str(exc)[:200]) from exc
    except CopilotStudioNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)[:200]) from exc
    except CopilotStudioRateLimitError as exc:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc)[:200]) from exc
    except CopilotStudioError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=str(exc)[:200]) from exc

    definition = extract_agent_definition(solution_zip)
    return CopilotStudioDefinitionResult(
        agent_definition=definition,
        found=definition is not None,
    )
