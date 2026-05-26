"""`/api/workflows/{workflow_id}/triggers` — trigger management REST API (Track 17.1).

Endpoints
---------
GET    /api/workflows/{id}/triggers                  list all triggers for a workflow
POST   /api/workflows/{id}/triggers                  create a new trigger
GET    /api/workflows/{id}/triggers/{trigger_id}     get one trigger
PATCH  /api/workflows/{id}/triggers/{trigger_id}     update a trigger
DELETE /api/workflows/{id}/triggers/{trigger_id}     delete a trigger
POST   /api/workflows/{id}/triggers/{trigger_id}/fire  manually fire a trigger

GET    /api/workflows/{id}/triggers/{trigger_id}/fires  list fire history

POST   /api/triggers/webhook/{trigger_id}            inbound webhook endpoint
       (no workflow-id prefix — callers use the trigger_id directly)

POST   /api/workflows/{id}/metric-samples            record a metric sample
       (used by the threshold trigger)
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict

from ...triggers.dispatcher import DispatchResult, TriggerDispatcher
from ...triggers.models import (
    TriggerAction,
    TriggerDefinition,
    TriggerFire,
    TriggerKind,
    parse_trigger_config,
)
from ...triggers.registry import TriggerRegistry
from ...triggers.webhook import WebhookError, validate_webhook_signature
from ..deps import ConnDep

_log = logging.getLogger(__name__)

router = APIRouter(tags=["triggers"])


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class TriggerCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    kind: TriggerKind
    action: TriggerAction = "run_clustering"
    config: dict[str, Any]
    enabled: bool = True


class TriggerUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    action: TriggerAction | None = None
    config: dict[str, Any] | None = None
    enabled: bool | None = None


class TriggerResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    workflow_id: str
    name: str
    kind: TriggerKind
    action: TriggerAction
    config: dict[str, Any]
    enabled: bool
    last_fired_at: str | None
    fire_count: int


class TriggerFireResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    trigger_id: str
    workflow_id: str
    fired_at: str
    action: TriggerAction
    status: str
    error_message: str | None
    payload_summary: str | None


class MetricSampleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metric_name: str
    value: float
    source: str | None = None


def _definition_to_response(t: TriggerDefinition) -> TriggerResponse:
    return TriggerResponse(
        id=str(t.id),
        workflow_id=str(t.workflow_id),
        name=t.name,
        kind=t.kind,
        action=t.action,
        config=t.config,
        enabled=t.enabled,
        last_fired_at=t.last_fired_at.isoformat() if t.last_fired_at else None,
        fire_count=t.fire_count,
    )


def _fire_to_response(f: TriggerFire) -> TriggerFireResponse:
    return TriggerFireResponse(
        id=str(f.id),
        trigger_id=str(f.trigger_id),
        workflow_id=str(f.workflow_id),
        fired_at=f.fired_at.isoformat(),
        action=f.action,
        status=f.status,
        error_message=f.error_message,
        payload_summary=f.payload_summary,
    )


# ---------------------------------------------------------------------------
# Workflow-scoped trigger CRUD
# ---------------------------------------------------------------------------


@router.get(
    "/api/workflows/{workflow_id}/triggers",
    response_model=list[TriggerResponse],
)
async def list_triggers(
    workflow_id: str,
    conn: ConnDep,
    include_disabled: bool = False,
) -> list[TriggerResponse]:
    """List all triggers for a workflow."""
    rows = await TriggerRegistry.list_for_workflow(
        conn, workflow_id, include_disabled=include_disabled
    )
    return [_definition_to_response(r) for r in rows]


@router.post(
    "/api/workflows/{workflow_id}/triggers",
    response_model=TriggerResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_trigger(
    workflow_id: str,
    body: TriggerCreateRequest,
    conn: ConnDep,
) -> TriggerResponse:
    """Create a new trigger for a workflow."""
    # Validate that the workflow exists.
    exists = await conn.fetchval(
        "SELECT 1 FROM workflows WHERE id = $1", workflow_id
    )
    if not exists:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"workflow {workflow_id!r} not found",
        )

    # Validate kind-specific config against the Pydantic model.
    try:
        parse_trigger_config(body.kind, body.config)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"invalid config for kind={body.kind!r}: {exc}",
        ) from exc

    row = await TriggerRegistry.create(
        conn,
        workflow_id=workflow_id,
        name=body.name,
        kind=body.kind,
        action=body.action,
        config=body.config,
        enabled=body.enabled,
    )
    return _definition_to_response(row)


@router.get(
    "/api/workflows/{workflow_id}/triggers/{trigger_id}",
    response_model=TriggerResponse,
)
async def get_trigger(
    workflow_id: str,
    trigger_id: str,
    conn: ConnDep,
) -> TriggerResponse:
    row = await TriggerRegistry.get(conn, trigger_id)
    if row is None or str(row.workflow_id) != workflow_id:
        raise HTTPException(status_code=404, detail="trigger not found")
    return _definition_to_response(row)


@router.patch(
    "/api/workflows/{workflow_id}/triggers/{trigger_id}",
    response_model=TriggerResponse,
)
async def update_trigger(
    workflow_id: str,
    trigger_id: str,
    body: TriggerUpdateRequest,
    conn: ConnDep,
) -> TriggerResponse:
    existing = await TriggerRegistry.get(conn, trigger_id)
    if existing is None or str(existing.workflow_id) != workflow_id:
        raise HTTPException(status_code=404, detail="trigger not found")

    # When config is being updated, validate it against the (possibly new) kind.
    if body.config is not None:
        kind = existing.kind  # kind is immutable
        try:
            parse_trigger_config(kind, body.config)
        except Exception as exc:
            raise HTTPException(
                status_code=422,
                detail=f"invalid config for kind={kind!r}: {exc}",
            ) from exc

    updated = await TriggerRegistry.update(
        conn,
        trigger_id,
        name=body.name,
        action=body.action,
        config=body.config,
        enabled=body.enabled,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="trigger not found")
    return _definition_to_response(updated)


@router.delete(
    "/api/workflows/{workflow_id}/triggers/{trigger_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_trigger(
    workflow_id: str,
    trigger_id: str,
    conn: ConnDep,
) -> None:
    existing = await TriggerRegistry.get(conn, trigger_id)
    if existing is None or str(existing.workflow_id) != workflow_id:
        raise HTTPException(status_code=404, detail="trigger not found")
    await TriggerRegistry.delete(conn, trigger_id)


# ---------------------------------------------------------------------------
# Manual fire
# ---------------------------------------------------------------------------


@router.post(
    "/api/workflows/{workflow_id}/triggers/{trigger_id}/fire",
    response_model=DispatchResult,
)
async def manual_fire_trigger(
    workflow_id: str,
    trigger_id: str,
    conn: ConnDep,
    request: Request,
) -> DispatchResult:
    """Manually fire a trigger (for testing / on-demand use)."""
    existing = await TriggerRegistry.get(conn, trigger_id)
    if existing is None or str(existing.workflow_id) != workflow_id:
        raise HTTPException(status_code=404, detail="trigger not found")

    pool = request.app.state.pool
    dispatcher = TriggerDispatcher(pool)
    result = await dispatcher.dispatch(existing, payload_summary="manual fire")
    return result


# ---------------------------------------------------------------------------
# Fire history
# ---------------------------------------------------------------------------


@router.get(
    "/api/workflows/{workflow_id}/triggers/{trigger_id}/fires",
    response_model=list[TriggerFireResponse],
)
async def list_trigger_fires(
    workflow_id: str,
    trigger_id: str,
    conn: ConnDep,
    limit: int = 50,
) -> list[TriggerFireResponse]:
    existing = await TriggerRegistry.get(conn, trigger_id)
    if existing is None or str(existing.workflow_id) != workflow_id:
        raise HTTPException(status_code=404, detail="trigger not found")
    fires = await TriggerRegistry.list_fires(conn, trigger_id, limit=limit)
    return [_fire_to_response(f) for f in fires]


# ---------------------------------------------------------------------------
# Inbound webhook endpoint (kind-agnostic HTTP POST)
# ---------------------------------------------------------------------------


@router.post(
    "/api/triggers/webhook/{trigger_id}",
    response_model=DispatchResult,
    status_code=status.HTTP_200_OK,
)
async def receive_webhook(
    trigger_id: str,
    request: Request,
    conn: ConnDep,
    x_ownevo_signature: str | None = Header(default=None),
    x_ownevo_timestamp: str | None = Header(default=None),
) -> DispatchResult:
    """Receive an inbound HMAC-signed webhook and dispatch the trigger.

    The route validates the HMAC signature before reading the body so a
    bad signature rejects cheaply without consuming a large payload.
    """
    trigger = await TriggerRegistry.get(conn, trigger_id)
    if trigger is None:
        raise HTTPException(status_code=404, detail="trigger not found")
    if trigger.kind != "webhook":
        raise HTTPException(
            status_code=400,
            detail=f"trigger {trigger_id!r} is kind={trigger.kind!r}, not 'webhook'",
        )
    if not trigger.enabled:
        raise HTTPException(status_code=400, detail="trigger is disabled")

    from ...triggers.models import WebhookConfig
    try:
        cfg = WebhookConfig.model_validate(trigger.config)
    except Exception as exc:
        _log.error("webhook: invalid config for trigger %s: %s", trigger_id, exc)
        raise HTTPException(status_code=500, detail="trigger misconfigured") from exc

    body = await request.body()

    # Prefer the configured header name over the default.
    sig_header_value = request.headers.get(cfg.signature_header, x_ownevo_signature)

    try:
        validate_webhook_signature(
            body=body,
            signature_header_value=sig_header_value,
            timestamp_header_value=x_ownevo_timestamp,
            hmac_secret=cfg.hmac_secret,
            signature_header_name=cfg.signature_header,
            max_age_seconds=cfg.max_age_seconds,
        )
    except WebhookError as exc:
        raise HTTPException(status_code=exc.http_status, detail=str(exc)) from exc

    pool = request.app.state.pool
    dispatcher = TriggerDispatcher(pool)
    return await dispatcher.dispatch(
        trigger,
        payload_summary=f"webhook: {len(body)} bytes",
    )


# ---------------------------------------------------------------------------
# Metric samples (for threshold triggers)
# ---------------------------------------------------------------------------


@router.post(
    "/api/workflows/{workflow_id}/metric-samples",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def record_metric_sample(
    workflow_id: str,
    body: MetricSampleRequest,
    conn: ConnDep,
) -> None:
    """Record a metric sample for threshold trigger evaluation."""
    exists = await conn.fetchval(
        "SELECT 1 FROM workflows WHERE id = $1", workflow_id
    )
    if not exists:
        raise HTTPException(status_code=404, detail=f"workflow {workflow_id!r} not found")

    await TriggerRegistry.record_metric_sample(
        conn,
        workflow_id=workflow_id,
        metric_name=body.metric_name,
        value=body.value,
        source=body.source,
    )
