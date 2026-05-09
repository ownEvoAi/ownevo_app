"""SandboxedTauBenchRunner — pure unit tests, no Docker required.

Pins the __post_init__ validation contracts and the runner's
attribute shape. No sandbox execution involved.
"""

from __future__ import annotations

import pytest
from ownevo_kernel.benchmark.tau3.runner import SandboxedTauBenchRunner
from ownevo_kernel.sandbox import LocalDockerSandbox


def _sandbox() -> LocalDockerSandbox:
    return LocalDockerSandbox(network="bridge")


# ---------------------------------------------------------------------------
# user_model defaulting
# ---------------------------------------------------------------------------


def test_user_model_defaults_to_agent_model():
    r = SandboxedTauBenchRunner(
        domain="retail",
        split="test",
        agent_model="anthropic/claude-sonnet-4-6",
        sandbox=_sandbox(),
    )
    assert r.user_model == "anthropic/claude-sonnet-4-6"


def test_explicit_user_model_preserved():
    r = SandboxedTauBenchRunner(
        domain="retail",
        split="test",
        agent_model="anthropic/claude-sonnet-4-6",
        user_model="anthropic/claude-haiku-4-5-20251001",
        sandbox=_sandbox(),
    )
    assert r.user_model == "anthropic/claude-haiku-4-5-20251001"


# ---------------------------------------------------------------------------
# skill_override_dir validation
# ---------------------------------------------------------------------------


def test_skill_override_dir_nonexistent_raises(tmp_path):
    missing = tmp_path / "no_such_dir"
    with pytest.raises(ValueError, match="must be an existing directory"):
        SandboxedTauBenchRunner(
            domain="retail",
            split="test",
            agent_model="anthropic/claude-sonnet-4-6",
            sandbox=_sandbox(),
            skill_override_dir=missing,
        )


def test_skill_override_dir_without_agent_py_raises(tmp_path):
    (tmp_path / "other.py").write_text("")
    with pytest.raises(ValueError, match="must contain agent.py"):
        SandboxedTauBenchRunner(
            domain="retail",
            split="test",
            agent_model="anthropic/claude-sonnet-4-6",
            sandbox=_sandbox(),
            skill_override_dir=tmp_path,
        )


def test_skill_override_dir_with_agent_py_accepted(tmp_path):
    (tmp_path / "agent.py").write_text("class HarnessAgent: pass\n")
    r = SandboxedTauBenchRunner(
        domain="retail",
        split="test",
        agent_model="anthropic/claude-sonnet-4-6",
        sandbox=_sandbox(),
        skill_override_dir=tmp_path,
    )
    assert r.skill_override_dir == tmp_path.resolve()


def test_skill_override_dir_resolves_to_absolute(tmp_path):
    (tmp_path / "agent.py").write_text("")
    r = SandboxedTauBenchRunner(
        domain="retail",
        split="test",
        agent_model="anthropic/claude-sonnet-4-6",
        sandbox=_sandbox(),
        skill_override_dir=tmp_path,
    )
    assert r.skill_override_dir.is_absolute()
