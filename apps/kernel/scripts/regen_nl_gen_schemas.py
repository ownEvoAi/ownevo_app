"""Regenerate the frozen NL-gen JSON schemas.

Writes:
  apps/kernel/src/ownevo_kernel/nl_gen/schemas/workflow_spec.v1.0.json
  apps/kernel/src/ownevo_kernel/nl_gen/schemas/simulation_plan.v1.0.json

Run after an intentional schema change. The CI guard
(`tests/test_nl_gen_schema_freeze.py`) compares Pydantic's live
`model_json_schema()` output against these snapshots and fails on any drift
— so you must:

  1. Bump `nl_gen.spec.SCHEMA_VERSION` (and / or `nl_gen.sim_plan.SCHEMA_VERSION`)
     and the matching `Literal["..."]` annotation on the model field.
  2. Re-run this script.
  3. Re-test the W7 UI rendering against the new schema.

If you're seeing an unexpected diff in CI without intending to change the
schema, do NOT regenerate — inspect what drifted (often a docstring edit
that bled into a description field, or a default-value tweak) and roll
back the model change instead.

NOTE — cross-package dependency: `WorkflowSpec` inlines all `UIView`
variants (from `packages/trace-format/`) into its JSON schema. A change to
`ui_views.py` breaks BOTH `test_workflow_spec_schema_matches_frozen_snapshot`
(here) AND `test_ui_view_schema_matches_frozen_snapshot` (trace-format).
Run BOTH regen scripts when changing UIView:

    cd packages/trace-format && uv run python scripts/regen_schemas.py
    cd apps/kernel && uv run --extra agent python scripts/regen_nl_gen_schemas.py

Invoke from `apps/kernel/`:

    uv run --extra agent python scripts/regen_nl_gen_schemas.py

(`agent` extra not strictly required to run the regen — kept for parity
with how the live API tests are invoked.)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from ownevo_kernel.nl_gen import SimulationPlan, WorkflowSpec
from ownevo_kernel.nl_gen.sim_plan import SCHEMA_VERSION as SIM_PLAN_VERSION
from ownevo_kernel.nl_gen.spec import SCHEMA_VERSION as WORKFLOW_SPEC_VERSION

_SCHEMAS_DIR = (
    Path(__file__).resolve().parent.parent
    / "src"
    / "ownevo_kernel"
    / "nl_gen"
    / "schemas"
)


def _write(path: Path, schema: dict) -> None:
    """Write `schema` to `path` as deterministic JSON."""
    text = json.dumps(schema, indent=2, sort_keys=True) + "\n"
    path.write_text(text)
    print(f"  {path.relative_to(_SCHEMAS_DIR.parent.parent.parent)}: {len(text)} bytes")


def main() -> int:
    _SCHEMAS_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Writing snapshots to {_SCHEMAS_DIR.relative_to(_SCHEMAS_DIR.parent.parent.parent)}/ …")
    _write(_SCHEMAS_DIR / f"workflow_spec.v{WORKFLOW_SPEC_VERSION}.json", WorkflowSpec.model_json_schema())
    _write(_SCHEMAS_DIR / f"simulation_plan.v{SIM_PLAN_VERSION}.json", SimulationPlan.model_json_schema())
    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
