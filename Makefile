# ownEvo make targets — thin wrappers over `uv run`.
#
# Targets here are user-facing entrypoints from `docs/PLAN.md`. Each one
# delegates to a Python script under `apps/kernel/scripts/` so the bulk
# of the logic stays Python-side and testable.

.PHONY: help test lint m5-baseline m5-baseline-no-db sandbox-image-m5 \
        api web-dev web-build seed-approval-demo \
        seed-m5-baseline m5-bootstrap-loop eval-replay

help:
	@printf 'targets:\n'
	@printf '  test                run the full kernel + trace-format test suite\n'
	@printf '  lint                ruff over the workspace\n'
	@printf '  m5-baseline         run the Day-1 M5 baseline (W2.6); records to DB if\n'
	@printf '                      OWNEVO_DATABASE_URL is set\n'
	@printf '  m5-baseline-no-db   same, but skip DB writes even when the URL is set\n'
	@printf '  sandbox-image-m5    build the M5 sandbox Docker image (W2.6 #11c)\n'
	@printf '  api                 run the kernel REST API (uvicorn) on :8000 (W2.5)\n'
	@printf '  web-dev             run the Next.js dev server on :3000 (W2.5)\n'
	@printf '  web-build           production build of the Next.js app (W2.5)\n'
	@printf '  seed-approval-demo  insert one gate-passed proposal for manual UI test\n'
	@printf '  seed-m5-baseline    bootstrap seed — workflow row + 6 baseline skills (BL.1)\n'
	@printf '  m5-bootstrap-loop   one round of the BL.3 improvement loop (LM Studio default)\n'
	@printf '  eval-replay         A4.3: replay an NL-gen workflow and emit metric score\n'
	@printf '                      WORKFLOW={demand-prediction|credit-risk|contract-review|all}\n'
	@printf '\nenv:\n'
	@printf '  OWNEVO_M5_DIR          path to M5 CSVs (default ./data/m5)\n'
	@printf '  OWNEVO_DATABASE_URL    postgres URL; required for api / seed targets\n'
	@printf '  OWNEVO_M5_SANDBOX      set to 1 to run baseline through LocalDockerSandbox\n'
	@printf '  OWNEVO_KERNEL_API_URL  override the kernel URL the web app talks to\n'
	@printf '  OWNEVO_LLM_BASE_URL    Anthropic-compat LLM base URL (default LM Studio)\n'
	@printf '  OWNEVO_LLM_MODEL       LLM model id (default qwen/qwen3-coder-30b)\n'
	@printf '  OWNEVO_LLM_API_KEY     LLM API key (ignored by local backends)\n'

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

# ----------------------------------------------------------------------------
# Approval REST API + web scaffold (W2.5)
# ----------------------------------------------------------------------------

# Reload-on-change uvicorn dev server. Production deployment runs the same
# `ownevo_kernel.api.app:app` ASGI target without --reload.
api:
	uv run --package ownevo-kernel --extra api \
	    uvicorn ownevo_kernel.api.app:app --reload --port 8000

web-dev:
	cd apps/web && npm run dev

web-build:
	cd apps/web && npm run build

seed-approval-demo:
	uv run --package ownevo-kernel python apps/kernel/scripts/seed_approval_demo.py

# ----------------------------------------------------------------------------
# Bootstrap improvement loop (BL.1 + BL.3 — pre-W3, PLAN.md v3.8)
# ----------------------------------------------------------------------------

# `LOOP_ARGS=...` passes flags through to scripts/run_improvement_loop.py.
# Common: `--llm-model`, `--llm-base-url`, `--max-iterations`, `--no-seed`.
LOOP_ARGS ?=

seed-m5-baseline:
	cd apps/kernel && uv run python scripts/seed_m5_baseline.py

m5-bootstrap-loop:
	cd apps/kernel && uv run python scripts/run_improvement_loop.py $(LOOP_ARGS)

# ----------------------------------------------------------------------------
# NL-gen eval replay (A4.3)
# ----------------------------------------------------------------------------

# `WORKFLOW=...` selects the fixture trio to replay. `all` runs every
# fixture and exits 0 only if every one meets its metric's target.
# `EVAL_ARGS=...` passes flags through to scripts/eval_replay.py
# (e.g. EVAL_ARGS='--pretty --include-outcomes').
WORKFLOW ?= all
EVAL_ARGS ?=

eval-replay:
	cd apps/kernel && uv run python scripts/eval_replay.py \
	    --workflow $(WORKFLOW) $(EVAL_ARGS)
