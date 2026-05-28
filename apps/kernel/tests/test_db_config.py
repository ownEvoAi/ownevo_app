"""Env-var parsing for pool sizing + per-connection statement_timeout.

These run in unit-only CI (no live Postgres) — the actual end-to-end
behaviour against a real connection lives in
``test_db_statement_timeout.py``.
"""

from __future__ import annotations

import pytest
from ownevo_kernel.db import (
    DEFAULT_POOL_MAX_SIZE,
    DEFAULT_POOL_MIN_SIZE,
    DEFAULT_STATEMENT_TIMEOUT_MS,
    POOL_MAX_SIZE_ENV,
    POOL_MIN_SIZE_ENV,
    STATEMENT_TIMEOUT_MS_ENV,
    open_pool,
    pool_max_size_from_env,
    pool_min_size_from_env,
    statement_timeout_ms_from_env,
)


def test_defaults_when_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(POOL_MIN_SIZE_ENV, raising=False)
    monkeypatch.delenv(POOL_MAX_SIZE_ENV, raising=False)
    monkeypatch.delenv(STATEMENT_TIMEOUT_MS_ENV, raising=False)
    assert pool_min_size_from_env() == DEFAULT_POOL_MIN_SIZE
    assert pool_max_size_from_env() == DEFAULT_POOL_MAX_SIZE
    assert statement_timeout_ms_from_env() == DEFAULT_STATEMENT_TIMEOUT_MS


def test_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(POOL_MIN_SIZE_ENV, "3")
    monkeypatch.setenv(POOL_MAX_SIZE_ENV, "25")
    monkeypatch.setenv(STATEMENT_TIMEOUT_MS_ENV, "5000")
    assert pool_min_size_from_env() == 3
    assert pool_max_size_from_env() == 25
    assert statement_timeout_ms_from_env() == 5000


def test_statement_timeout_zero_disables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``0`` is a valid opt-out, separate from "unset" (which means
    default). Lets a long-running script process opt out without
    touching the value of the env var inherited from a parent shell."""
    monkeypatch.setenv(STATEMENT_TIMEOUT_MS_ENV, "0")
    assert statement_timeout_ms_from_env() == 0


def test_blank_value_falls_back_to_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Docker-compose's ``${VAR:-}`` interpolation passes an empty string
    when the var is unset on the host; treat that the same as missing
    rather than crashing on int() of an empty string."""
    monkeypatch.setenv(POOL_MIN_SIZE_ENV, "")
    monkeypatch.setenv(POOL_MAX_SIZE_ENV, "  ")
    monkeypatch.setenv(STATEMENT_TIMEOUT_MS_ENV, "")
    assert pool_min_size_from_env() == DEFAULT_POOL_MIN_SIZE
    assert pool_max_size_from_env() == DEFAULT_POOL_MAX_SIZE
    assert statement_timeout_ms_from_env() == DEFAULT_STATEMENT_TIMEOUT_MS


@pytest.mark.parametrize(
    "env_var, value, match",
    [
        (POOL_MIN_SIZE_ENV, "not-a-number", "not a valid integer"),
        (POOL_MAX_SIZE_ENV, "9.5", "not a valid integer"),
        (POOL_MIN_SIZE_ENV, "-1", "must be >= 0"),
        (POOL_MAX_SIZE_ENV, "-2", "must be >= 0"),
        (STATEMENT_TIMEOUT_MS_ENV, "abc", "not a valid integer"),
        (STATEMENT_TIMEOUT_MS_ENV, "-1", "must be >= 0"),
    ],
)
def test_invalid_values_rejected(
    monkeypatch: pytest.MonkeyPatch, env_var: str, value: str, match: str
) -> None:
    monkeypatch.setenv(env_var, value)
    parser = {
        POOL_MIN_SIZE_ENV: pool_min_size_from_env,
        POOL_MAX_SIZE_ENV: pool_max_size_from_env,
        STATEMENT_TIMEOUT_MS_ENV: statement_timeout_ms_from_env,
    }[env_var]
    with pytest.raises(ValueError, match=match):
        parser()


def test_pool_min_zero_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pool min=0 parses cleanly as a non-negative int but is rejected
    here so the first request after boot doesn't pay the connect handshake."""
    monkeypatch.setenv(POOL_MIN_SIZE_ENV, "0")
    with pytest.raises(ValueError, match="must be >= 1"):
        pool_min_size_from_env()


def test_pool_max_zero_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(POOL_MAX_SIZE_ENV, "0")
    with pytest.raises(ValueError, match="must be >= 1"):
        pool_max_size_from_env()


async def test_open_pool_rejects_min_greater_than_max() -> None:
    """The min > max guard fires at open_pool, before asyncpg sees the
    config — so a bad combination fails before a real connect attempt."""
    with pytest.raises(ValueError, match="min_size .* > max_size"):
        await open_pool("postgresql://unused", min_size=5, max_size=2)


async def test_open_pool_rejects_min_greater_than_max_via_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same guard fires when the inconsistent pair comes from env vars,
    not explicit arguments — the env-read path feeds the same check."""
    monkeypatch.setenv(POOL_MIN_SIZE_ENV, "5")
    monkeypatch.setenv(POOL_MAX_SIZE_ENV, "2")
    with pytest.raises(ValueError, match="min_size .* > max_size"):
        await open_pool("postgresql://unused")
