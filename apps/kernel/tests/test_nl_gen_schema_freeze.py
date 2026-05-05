"""A3.4 schema-freeze guards.

The W3 schema-freeze ritual locks the NL-gen output schemas at v1.0 so
that W4-W6 content iterations + W7 UI rendering develop against a stable
contract. Drift detection works by comparing Pydantic's live
`model_json_schema()` against snapshots checked in to
`apps/kernel/src/ownevo_kernel/nl_gen/schemas/`.

Tag: `v1.0-frozen-2026-W3`.

If a test in this file fails:

  * **Unintentional drift** (you didn't mean to change the schema): roll
    back the model change. Often it's a docstring edit that bled into a
    description field or a default-value tweak.
  * **Intentional change**: bump `nl_gen.spec.SCHEMA_VERSION` (and / or
    `nl_gen.sim_plan.SCHEMA_VERSION`) and the matching `Literal[...]`
    annotation, regenerate the snapshot
    (`uv run python scripts/regen_nl_gen_schemas.py`), and re-test the
    W7 UI rendering against the new schema.
"""

from __future__ import annotations

import difflib
import json
from pathlib import Path

import pytest
from ownevo_kernel.nl_gen import SimulationPlan, WorkflowSpec
from ownevo_kernel.nl_gen.sim_plan import SCHEMA_VERSION as SIM_PLAN_SCHEMA_VERSION
from ownevo_kernel.nl_gen.spec import SCHEMA_VERSION as WORKFLOW_SPEC_SCHEMA_VERSION

_SCHEMAS_DIR = (
    Path(__file__).resolve().parent.parent
    / "src"
    / "ownevo_kernel"
    / "nl_gen"
    / "schemas"
)


# ---------------------------------------------------------------------------
# Constants pinned at v1.0
# ---------------------------------------------------------------------------


def test_workflow_spec_schema_version_is_one_zero():
    assert WORKFLOW_SPEC_SCHEMA_VERSION == "1.0"


def test_simulation_plan_schema_version_is_one_zero():
    assert SIM_PLAN_SCHEMA_VERSION == "1.0"


# ---------------------------------------------------------------------------
# Diff guards — model_json_schema() vs frozen snapshot
# ---------------------------------------------------------------------------


def _canonical(schema: dict) -> str:
    """Match the regen script's canonical form: sorted keys + 2-space indent + trailing newline."""
    return json.dumps(schema, indent=2, sort_keys=True) + "\n"


def _assert_matches_snapshot(model_cls, snapshot_path: Path) -> None:
    live = _canonical(model_cls.model_json_schema())
    if not snapshot_path.is_file():
        pytest.fail(
            f"snapshot missing: {snapshot_path}\n"
            "Run `uv run python scripts/regen_nl_gen_schemas.py` to create it."
        )
    frozen = snapshot_path.read_text()
    if live == frozen:
        return
    diff = "\n".join(
        difflib.unified_diff(
            frozen.splitlines(),
            live.splitlines(),
            fromfile=f"{snapshot_path.name} (frozen)",
            tofile=f"{model_cls.__name__}.model_json_schema() (live)",
            lineterm="",
            n=3,
        )
    )
    pytest.fail(
        f"\nNL-gen schema drift detected for {model_cls.__name__} vs "
        f"{snapshot_path.name}.\n\n"
        "If this drift is unintentional, roll back the model change.\n"
        "If intentional: bump SCHEMA_VERSION + Literal annotation, then\n"
        "run `uv run python scripts/regen_nl_gen_schemas.py` from "
        "`apps/kernel/`.\n\n"
        "Diff (frozen → live):\n"
        f"{diff}\n"
    )


def test_workflow_spec_schema_matches_frozen_snapshot():
    _assert_matches_snapshot(WorkflowSpec, _SCHEMAS_DIR / f"workflow_spec.v{WORKFLOW_SPEC_SCHEMA_VERSION}.json")


def test_simulation_plan_schema_matches_frozen_snapshot():
    _assert_matches_snapshot(
        SimulationPlan, _SCHEMAS_DIR / f"simulation_plan.v{SIM_PLAN_SCHEMA_VERSION}.json"
    )


# ---------------------------------------------------------------------------
# Fixtures still round-trip at v1.0
# ---------------------------------------------------------------------------


def test_all_workflow_spec_fixtures_pin_v1_0():
    """The 3 hand-authored WorkflowSpec fixtures all serialize at the
    frozen schema version. If a fixture lags behind the bump, the live
    snapshot tests will silently encode old shape into the audit trail."""
    from ownevo_kernel.nl_gen.fixtures import FIXTURES

    assert len(FIXTURES) >= 3, f"Expected ≥3 WorkflowSpec fixtures, got {len(FIXTURES)}"
    for fixture_id, spec in FIXTURES.items():
        assert spec.schema_version == "1.0", (
            f"fixture {fixture_id!r} schema_version is {spec.schema_version!r}, "
            "expected '1.0'"
        )


def test_all_sim_plan_fixtures_pin_v1_0():
    from ownevo_kernel.nl_gen.fixtures import SIM_PLAN_FIXTURES

    assert len(SIM_PLAN_FIXTURES) >= 3, f"Expected ≥3 SimulationPlan fixtures, got {len(SIM_PLAN_FIXTURES)}"
    for fixture_id, plan in SIM_PLAN_FIXTURES.items():
        assert plan.schema_version == "1.0", (
            f"sim plan fixture {fixture_id!r} schema_version is "
            f"{plan.schema_version!r}, expected '1.0'"
        )
