"""Regenerate trace-format JSON schema snapshots.

Writes:
  packages/trace-format/schemas/agent_event.v1.0.json
  packages/trace-format/schemas/ui_primitives.v1.0.json

CI guard (`tests/test_schema_freeze.py`) compares Pydantic's live
`json_schema()` output against these snapshots and fails on any drift.
Run this script *only* after an intentional schema change — bump the
SPEC.md version line + the agent_event.py docstring to match, and
re-test the W7 UI rendering against the new schema.

Invoke from `packages/trace-format/`:

    uv run python scripts/regen_schemas.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from ownevo_format import AgentEventAdapter
from ownevo_format.agent_event import SCHEMA_VERSION
from ownevo_format.ui_primitives import UIPrimitive
from pydantic import TypeAdapter

_SCHEMAS_DIR = Path(__file__).resolve().parent.parent / "schemas"


def _write(path: Path, schema: dict) -> None:
    text = json.dumps(schema, indent=2, sort_keys=True) + "\n"
    path.write_text(text)
    print(f"  {path.relative_to(_SCHEMAS_DIR.parent)}: {len(text)} bytes")


def main() -> int:
    _SCHEMAS_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Writing snapshots to {_SCHEMAS_DIR.relative_to(_SCHEMAS_DIR.parent)}/ …")
    _write(_SCHEMAS_DIR / f"agent_event.v{SCHEMA_VERSION}.json", AgentEventAdapter.json_schema())
    _write(
        _SCHEMAS_DIR / f"ui_primitives.v{SCHEMA_VERSION}.json",
        TypeAdapter(UIPrimitive).json_schema(),
    )
    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
