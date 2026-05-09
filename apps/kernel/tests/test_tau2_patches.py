"""tau2_patches resilience shims — pure unit tests.

Mocks tau2 modules into sys.modules so the patch functions can run on a
host without tau2 installed, then exercises the shim json.loads on the
inputs that caused 10–17% infra_errors on the retail-test split (P0.4).

Key contracts pinned here:
  * empty-string / empty-bytes input → {} (not JSONDecodeError)
  * ```json ... ``` fenced output → extracted and parsed
  * non-greedy _braces_re: stops at first complete {…}, not last } in line
  * both shims are idempotent (second call is a no-op)
"""

from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

_PATCHES_PATH = Path(__file__).resolve().parents[1] / "sandbox" / "tau2_patches.py"


def _load_patches_module():
    spec = importlib.util.spec_from_file_location("_tau2_patches_test", _PATCHES_PATH)
    assert spec and spec.loader, f"could not load {_PATCHES_PATH}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


@pytest.fixture()
def patches():
    return _load_patches_module()


@pytest.fixture(autouse=True)
def _clean_fake_tau2():
    """Remove injected fake tau2 modules after each test."""
    yield
    for key in list(sys.modules):
        if key == "tau2" or key.startswith("tau2."):
            del sys.modules[key]


def _inject_fake_nl():
    fake = types.SimpleNamespace(_ownevo_nl_resilience_applied=False, json=json)
    sys.modules.setdefault("tau2", types.ModuleType("tau2"))
    sys.modules.setdefault("tau2.evaluator", types.ModuleType("tau2.evaluator"))
    sys.modules["tau2.evaluator.evaluator_nl_assertions"] = fake
    return fake


def _inject_fake_llm():
    fake = types.SimpleNamespace(_ownevo_args_patch_applied=False, json=json)
    sys.modules.setdefault("tau2", types.ModuleType("tau2"))
    sys.modules.setdefault("tau2.utils", types.ModuleType("tau2.utils"))
    sys.modules["tau2.utils.llm_utils"] = fake
    return fake


# ---------------------------------------------------------------------------
# NL-evaluator resilience shim (_ResilientJsonShim)
# ---------------------------------------------------------------------------


def test_nl_shim_empty_string_returns_empty_dict(patches):
    fake = _inject_fake_nl()
    patches._patch_nl_evaluator_resilience()
    assert fake.json.loads("") == {}


def test_nl_shim_clean_json_passes_through(patches):
    fake = _inject_fake_nl()
    patches._patch_nl_evaluator_resilience()
    assert fake.json.loads('{"score": 1}') == {"score": 1}


def test_nl_shim_json_fence_with_lang_tag(patches):
    """LLM wraps JSON in ```json ... ``` — common Sonnet response format."""
    fake = _inject_fake_nl()
    patches._patch_nl_evaluator_resilience()
    assert fake.json.loads('```json\n{"score": 0}\n```') == {"score": 0}


def test_nl_shim_json_fence_without_lang_tag(patches):
    fake = _inject_fake_nl()
    patches._patch_nl_evaluator_resilience()
    assert fake.json.loads('```\n{"score": 1}\n```') == {"score": 1}


def test_nl_shim_prose_prefix_with_embedded_json(patches):
    """LLM prepends prose before the object — _braces_re fallback picks it up."""
    fake = _inject_fake_nl()
    patches._patch_nl_evaluator_resilience()
    result = fake.json.loads('Here is my evaluation: {"score": 1}')
    assert result == {"score": 1}


def test_nl_shim_non_greedy_stops_at_first_object(patches):
    """_braces_re must be non-greedy.

    Greedy r"\\{.*\\}" with DOTALL captures from first { to last }, producing
    '{"a": "b"} extra text {"c": "d"}' as the match — invalid JSON.
    Non-greedy r"\\{.*?\\}" captures only '{"a": "b"}' and returns the first object.
    This test would fail if _braces_re were reverted to greedy.
    """
    fake = _inject_fake_nl()
    patches._patch_nl_evaluator_resilience()
    result = fake.json.loads('{"a": "b"} extra {"c": "d"}')
    assert result == {"a": "b"}


def test_nl_shim_idempotent(patches):
    fake = _inject_fake_nl()
    patches._patch_nl_evaluator_resilience()
    shim_first = fake.json
    patches._patch_nl_evaluator_resilience()
    assert fake.json is shim_first


# ---------------------------------------------------------------------------
# Tool-call args resilience shim (_SafeJsonShim)
# ---------------------------------------------------------------------------


def test_args_shim_empty_string_returns_empty_dict(patches):
    """Anthropic/Haiku emit arguments="" for no-arg tools → must not raise."""
    fake = _inject_fake_llm()
    patches._patch_tool_call_args_resilience()
    assert fake.json.loads("") == {}


def test_args_shim_empty_bytes_returns_empty_dict(patches):
    fake = _inject_fake_llm()
    patches._patch_tool_call_args_resilience()
    assert fake.json.loads(b"") == {}


def test_args_shim_clean_json_passes_through(patches):
    fake = _inject_fake_llm()
    patches._patch_tool_call_args_resilience()
    assert fake.json.loads('{"arg": 42}') == {"arg": 42}


def test_args_shim_idempotent(patches):
    fake = _inject_fake_llm()
    patches._patch_tool_call_args_resilience()
    shim_first = fake.json
    patches._patch_tool_call_args_resilience()
    assert fake.json is shim_first
