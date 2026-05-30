"""A3.4 schema-freeze guards for trace-format.

Tag: `v1.0-frozen-2026-W3`. Locks `AgentEvent` and `UIView` JSON
schemas at v1.0 so the W7 UI rendering and the M5 agent loop develop
against a stable contract. Drift detection: live `json_schema()` vs the
checked-in snapshots at `packages/trace-format/schemas/`.

If a test here fails:

  * **Unintentional drift** — roll back the model change. Often it's a
    docstring edit that bled into a description field, or a default-value
    tweak.
  * **Intentional change** — bump SPEC.md version line + the
    `agent_event.py` docstring to match, regenerate via
    `uv run python scripts/regen_schemas.py` from
    `packages/trace-format/`, and re-test the W7 UI surface (M5 agent
    loop trace ingestion + the proposal-detail page that reads
    UIView blocks).
"""

from __future__ import annotations

import difflib
import json
from pathlib import Path

import pytest
from ownevo_format import AgentEventAdapter
from ownevo_format.agent_event import SCHEMA_VERSION
from ownevo_format.ui_views import UIView
from pydantic import TypeAdapter

_SCHEMAS_DIR = Path(__file__).resolve().parent.parent / "schemas"


def _canonical(schema: dict) -> str:
    return json.dumps(schema, indent=2, sort_keys=True) + "\n"


def _assert_matches(name: str, live: dict, snapshot_path: Path) -> None:
    if not snapshot_path.is_file():
        pytest.fail(
            f"snapshot missing: {snapshot_path}\n"
            "Run `uv run python scripts/regen_schemas.py` from "
            "`packages/trace-format/` to create it."
        )
    live_text = _canonical(live)
    frozen_text = snapshot_path.read_text()
    if live_text == frozen_text:
        return
    diff = "\n".join(
        difflib.unified_diff(
            frozen_text.splitlines(),
            live_text.splitlines(),
            fromfile=f"{snapshot_path.name} (frozen)",
            tofile=f"{name} (live)",
            lineterm="",
            n=3,
        )
    )
    pytest.fail(
        f"\ntrace-format schema drift detected for {name} vs "
        f"{snapshot_path.name}.\n\n"
        "If this drift is unintentional, roll back the model change.\n"
        "If intentional: bump SPEC.md version + docstring, then run\n"
        "`uv run python scripts/regen_schemas.py` from "
        "`packages/trace-format/`.\n\n"
        f"Diff (frozen → live):\n{diff}\n"
    )


def test_agent_event_schema_matches_frozen_snapshot():
    _assert_matches(
        "AgentEventAdapter.json_schema()",
        AgentEventAdapter.json_schema(),
        _SCHEMAS_DIR / f"agent_event.v{SCHEMA_VERSION}.json",
    )


def test_ui_view_schema_matches_frozen_snapshot():
    _assert_matches(
        "TypeAdapter(UIView).json_schema()",
        TypeAdapter(UIView).json_schema(),
        _SCHEMAS_DIR / f"ui_views.v{SCHEMA_VERSION}.json",
    )
