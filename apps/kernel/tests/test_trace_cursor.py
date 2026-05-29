"""Pure unit tests for the trace-list keyset cursor codec.

No DB — the encode/decode pair is exercised directly, so this runs in the
unit-only CI job. The end-to-end paging behaviour lives in
``test_api_traces.py`` (DB-gated).
"""

from __future__ import annotations

import base64
import uuid
from datetime import UTC, datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from ownevo_kernel.api.routes.traces import _decode_cursor, _encode_cursor


def test_cursor_round_trips() -> None:
    started_at = datetime(2026, 5, 28, 14, 30, 15, 123456, tzinfo=UTC)
    trace_id = uuid.uuid4()
    token = _encode_cursor(started_at, trace_id)
    decoded_at, decoded_id = _decode_cursor(token)
    assert decoded_at == started_at
    assert decoded_id == trace_id


def test_cursor_preserves_non_utc_offset() -> None:
    """started_at comes back from Postgres as an aware datetime; a non-UTC
    offset must survive the round-trip so the keyset comparison stays exact."""
    started_at = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone(timedelta(hours=-5)))
    token = _encode_cursor(started_at, uuid.uuid4())
    decoded_at, _ = _decode_cursor(token)
    assert decoded_at == started_at


def test_cursor_is_opaque_base64url() -> None:
    """The token is URL-safe base64 (no '+' or '/'), so it needs no extra
    escaping when passed back as a query param."""
    token = _encode_cursor(datetime.now(tz=UTC), uuid.uuid4())
    assert "+" not in token
    assert "/" not in token


def _b64(raw: str) -> str:
    return base64.urlsafe_b64encode(raw.encode()).decode()


@pytest.mark.parametrize(
    "bad",
    [
        "not-base64!!",        # invalid base64 alphabet
        "",                    # empty
        _b64("foobar"),        # valid base64 but no "|" separator
        _b64(f"not-a-date|{uuid.uuid4()}"),   # unparseable datetime half
        _b64(f"{datetime.now(tz=UTC).isoformat()}|not-a-uuid"),  # bad UUID half
    ],
)
def test_decode_rejects_malformed_cursor(bad: str) -> None:
    with pytest.raises(HTTPException) as excinfo:
        _decode_cursor(bad)
    assert excinfo.value.status_code == 400
