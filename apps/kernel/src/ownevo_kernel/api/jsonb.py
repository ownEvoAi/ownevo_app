"""JSONB column decode helpers shared across route modules.

asyncpg returns `jsonb` columns as Python `str` unless a codec is set
on the connection. The route layer wants dicts/lists, not strings —
hence these tiny helpers. Extracted from per-route copies (one each
in proposals.py, skills.py, traces.py, workflows.py) so future codec
changes only touch one place.
"""

from __future__ import annotations

import json
from typing import Any


def decode_jsonb_obj(value: Any) -> dict[str, Any] | None:
    """Decode a `jsonb` column expected to be an object.

    Returns None when the column is null. asyncpg surfaces jsonb as
    `str` unless a codec is registered; this normalizes both cases.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return json.loads(value)
    return value


def decode_jsonb_array(value: Any) -> list[Any] | None:
    """Decode a `jsonb` column expected to be an array.

    Returns None when the column is null. Same str/dict handling as
    `decode_jsonb_obj`.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return json.loads(value)
    return value
