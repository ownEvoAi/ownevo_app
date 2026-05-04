# ownEvo make targets — thin wrappers over `uv run`.
#
# Targets here are user-facing entrypoints from `docs/PLAN.md`. Each one
# delegates to a Python script under `apps/kernel/scripts/` so the bulk
# of the logic stays Python-side and testable.

.PHONY: help test lint m5-baseline m5-baseline-no-db sandbox-image-m5

help:
	@printf 'targets:\n'
	@printf '  test              run the full kernel + trace-format test suite\n'
	@printf '  lint              ruff over the workspace\n'
	@printf '  m5-baseline       run the Day-1 M5 baseline (W2.6); records to DB if\n'
	@printf '                    OWNEVO_DATABASE_URL is set\n'
	@printf '  m5-baseline-no-db same, but skip DB writes even when the URL is set\n'
	@printf '  sandbox-image-m5  build the M5 sandbox Docker image (W2.6 #11c)\n'
	@printf '\nenv:\n'
	@printf '  OWNEVO_M5_DIR        path to M5 CSVs (default ./data/m5)\n'
	@printf '  OWNEVO_DATABASE_URL  postgres URL; unset = no DB recording\n'
	@printf '  OWNEVO_M5_SANDBOX    set to 1 to run baseline through LocalDockerSandbox\n'

test:
	uv run pytest

lint:
	uv run ruff check .

# ----------------------------------------------------------------------------
# M5 (W2.6)
# ----------------------------------------------------------------------------

# `M5_ARGS=...` lets callers pass extra flags through, e.g.
#   make m5-baseline M5_ARGS='--workflow-id=m5-demand-prediction-test'
M5_ARGS ?=

m5-baseline:
	cd apps/kernel && uv run python scripts/m5_baseline.py $(M5_ARGS)

m5-baseline-no-db:
	cd apps/kernel && uv run python scripts/m5_baseline.py --no-db $(M5_ARGS)

# ----------------------------------------------------------------------------
# Sandbox images (W2.6 #11c)
# ----------------------------------------------------------------------------

# Image tag is version-pinned so the test suite + scripts/m5_baseline.py
# both reference the exact image the Dockerfile produced. Bump in lockstep
# with the pinned versions inside Dockerfile.m5.
M5_SANDBOX_IMAGE ?= ownevo-sandbox-m5:0.1.0

sandbox-image-m5:
	docker build \
	    -f apps/kernel/sandbox/Dockerfile.m5 \
	    -t $(M5_SANDBOX_IMAGE) \
	    .
