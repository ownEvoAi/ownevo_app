# ownEvo make targets — thin wrappers over `uv run`.
#
# Targets here are user-facing entrypoints into the build. Each one
# delegates to a Python script under `apps/kernel/scripts/` so the bulk
# of the logic stays Python-side and testable.

.PHONY: help help-all setup doctor smoke fly-smoke fly-bootstrap \
        test lint m5-baseline m5-baseline-no-db sandbox-image-m5 sandbox-image-tau3 tau3-register tau3-baseline tau3-ingest tau3-loop tau3-replay \
        api web-dev web-build seed-approval-demo seed-demo seed-demo-with-iter \
        seed-m5-baseline m5-bootstrap-loop eval-replay nl-gen-smoketest \
        meta-eval m5-cluster-failures cluster-label-eval \
        llm-judge-approver-eval nl-gen-cluster-failures \
        m5-replay-7day m5-replay-30day m5-replay-bootstrap nl-gen-demo-loop revert-skill \
        dev-up dev-down dev-logs dev-ps \
        fly-migrate fly-deploy-kernel fly-deploy-web fly-seed fly-logs fly-ssh

# The default help shows the common everyday targets. `make help-all`
# dumps every target including dogfooding + benchmark internals.
help:
	@printf 'ownEvo — common targets. Run `make help-all` for the full list.\n'
	@printf '\n  ${BOLD}Getting started${RESET}\n'
	@printf '    setup               one-shot fresh-machine install (uv + node + .env)\n'
	@printf '    doctor              preflight checks (tools, .env, fly auth)\n'
	@printf '\n  ${BOLD}Local stack${RESET}\n'
	@printf '    dev-up              start postgres + kernel + web via docker compose\n'
	@printf '    dev-down            stop and remove all compose services\n'
	@printf '    dev-logs            tail logs from all compose services\n'
	@printf '    api                 run kernel API directly (uvicorn :8000, no docker)\n'
	@printf '    web-dev             run Next.js dev server directly (:3000, no docker)\n'
	@printf '\n  ${BOLD}Seed${RESET}\n'
	@printf '    seed-demo           seed credit-risk + contract-review workflows\n'
	@printf '    seed-demo-with-iter same, plus run one iteration (costs ~$$0.30)\n'
	@printf '\n  ${BOLD}Verify${RESET}\n'
	@printf '    test                run the full test suite\n'
	@printf '    lint                ruff over the workspace\n'
	@printf '    smoke               smoke-test localhost (health + workflows + audit)\n'
	@printf '\n  ${BOLD}Fly.io deploy${RESET}\n'
	@printf '    fly-bootstrap       one-shot first-time deploy (interactive)\n'
	@printf '    fly-deploy-kernel   deploy the kernel app\n'
	@printf '    fly-deploy-web      deploy the web app\n'
	@printf '    fly-seed            run seed_demo.py inside the kernel container\n'
	@printf '    fly-smoke           smoke-test the live demo (health + workflows + web)\n'
	@printf '    fly-logs            tail logs from both apps\n'
	@printf '    fly-ssh             open a shell on the kernel machine\n'
	@printf '\n  ${BOLD}Docs${RESET}\n'
	@printf '    docs/DEPLOYMENT.md         full deployment guide\n'
	@printf '    docs/runbooks/fly-deploy.md first-time Fly.io step-by-step\n'

help-all: help
	@printf '\n  ${BOLD}Benchmarks (M5)${RESET}\n'
	@printf '    m5-baseline         run the Day-1 M5 baseline\n'
	@printf '    m5-baseline-no-db   same, but skip DB writes\n'
	@printf '    m5-bootstrap-loop   one round of the BL.3 improvement loop\n'
	@printf '    m5-cluster-failures top-k worst M5 series → cluster → eval-cases\n'
	@printf '    m5-replay-7day      7-cycle synthetic M5 replay (W5.4)\n'
	@printf '    m5-replay-30day     30-day M5 replay across parallel conditions\n'
	@printf '    m5-replay-bootstrap one-shot DB + migrations + seed for m5-replay-30day\n'
	@printf '    seed-m5-baseline    bootstrap seed — workflow row + 6 baseline skills\n'
	@printf '\n  ${BOLD}Benchmarks (τ³)${RESET}\n'
	@printf '    tau3-register       bootstrap seed — τ³-retail workflow + cases\n'
	@printf '    tau3-baseline       run Day-1 τ³ baseline (sandboxed Sonnet 4.6)\n'
	@printf '    tau3-ingest         backfill iterations from tau2 results.json files\n'
	@printf '    tau3-loop           one improvement-loop iteration on τ³-retail\n'
	@printf '    tau3-replay         reproduce B-LOCAL config — qwen3.6-35b-a3b LMS\n'
	@printf '\n  ${BOLD}Sandbox images${RESET}\n'
	@printf '    sandbox-image-m5    build the M5 sandbox Docker image\n'
	@printf '    sandbox-image-tau3  build the τ³ sandbox Docker image\n'
	@printf '\n  ${BOLD}NL-gen / quality gates${RESET}\n'
	@printf '    nl-gen-smoketest        A4.4 quality gate: live NL-gen + agent solver\n'
	@printf '    nl-gen-demo-loop        W6 (row 6.1): NL-gen end-to-end demo loop\n'
	@printf '    nl-gen-cluster-failures W5.3: drive fixtures through stub agent → cluster\n'
	@printf '    meta-eval               A4.6: NL-gen quality judge over the 10-pair set\n'
	@printf '    eval-replay             A4.3: replay an NL-gen workflow + emit metric\n'
	@printf '    cluster-label-eval      B3.5: judge labeler vs hand-labeled fixtures\n'
	@printf '    llm-judge-approver-eval W5.2: LLM-judge stub approver eval\n'
	@printf '\n  ${BOLD}Operator tools${RESET}\n'
	@printf '    revert-skill        re-point skills.head_version_id; SKILL=<id> TO_VERSION=<n> REASON="..."\n'
	@printf '    seed-approval-demo  insert one gate-passed proposal for manual UI test\n'
	@printf '    web-build           production build of the Next.js app\n'
	@printf '    fly-migrate         run pending migrations on the live Fly Postgres\n'
	@printf '    dev-ps              show status of all compose services\n'
	@printf '\n  ${BOLD}Env${RESET}\n'
	@printf '    OWNEVO_M5_DIR          path to M5 CSVs (default ./data/m5)\n'
	@printf '    OWNEVO_DATABASE_URL    postgres URL; required for api / seed targets\n'
	@printf '    OWNEVO_M5_SANDBOX      set to 1 to run baseline through LocalDockerSandbox\n'
	@printf '    OWNEVO_KERNEL_API_URL  override the kernel URL the web app talks to\n'
	@printf '    OWNEVO_LLM_BASE_URL    Anthropic-compat LLM base URL (default LM Studio)\n'
	@printf '    OWNEVO_LLM_MODEL       LLM model id (default qwen/qwen3-coder-30b)\n'
	@printf '    OWNEVO_LLM_API_KEY     LLM API key (ignored by local backends)\n'

BOLD  := $(shell [ -t 1 ] && printf '\033[1m' || true)
RESET := $(shell [ -t 1 ] && printf '\033[0m' || true)

test:
	uv run pytest

lint:
	uv run ruff check .

# ----------------------------------------------------------------------------
# Onboarding + preflight + smoke
# ----------------------------------------------------------------------------

# One-shot fresh-machine install (idempotent). brew + uv + node + npm i +
# uv sync + sandbox dirs + .env bootstrap.
setup:
	./scripts/setup.sh

# Preflight checks before deploying or running the stack.
#   make doctor          # all checks (default)
#   make doctor MODE=dev # just dev-stack checks (no flyctl)
#   make doctor MODE=deploy # just deploy-track checks
MODE ?= all
doctor:
	./scripts/doctor.sh --$(MODE)

# Smoke-test a running kernel. Defaults to localhost:8000; override via
# SMOKE_URL for a remote target.
#   make smoke
#   make smoke SMOKE_URL=https://ownevo-kernel.fly.dev
SMOKE_URL ?= http://localhost:8000
smoke:
	./scripts/smoke.sh $(SMOKE_URL)

# Smoke-test the live Fly demo (kernel + web).
fly-smoke:
	./scripts/smoke.sh https://ownevo-kernel.fly.dev --web https://ownevo-web.fly.dev

# ----------------------------------------------------------------------------
# Fly.io — one-shot first-time bootstrap
# ----------------------------------------------------------------------------

# Walks the 8 steps of docs/runbooks/fly-deploy.md interactively. Pass
# --no-seed to skip the (paid) demo seed; --dry-run to preview commands
# without running them.
#   make fly-bootstrap
#   make fly-bootstrap BOOTSTRAP_ARGS=--no-seed
#   make fly-bootstrap BOOTSTRAP_ARGS=--dry-run
BOOTSTRAP_ARGS ?=
fly-bootstrap:
	./scripts/fly_bootstrap.sh $(BOOTSTRAP_ARGS)

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

# Track 9.0.2 — runs MockAgentSolver across iterations against a
# scripted accuracy curve. Zero LLM calls, no DB, no Docker. Asserts
# observed val_score matches the curve and the whole run fits a 30s
# wall-clock budget. See scripts/sim_mock_smoketest.py.
sim-mock-smoketest:
	cd apps/kernel && uv run python scripts/sim_mock_smoketest.py

# Track 9.0.3 — end-to-end replay roundtrip. Seeds a captured iteration,
# drives run_with_replay_agent against it, asserts predictions replay
# byte-identically and the fixture's uncovered cases land in `missing`.
# DB-backed; requires OWNEVO_DATABASE_URL.
sim-replay-smoketest:
	cd apps/kernel && uv run python scripts/sim_replay_smoketest.py

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

# τ³-bench sandbox image — P1.5 / M2a. Bakes tau2 + LiteLLM + the
# kernel into a python:3.12-slim base. tau2 is pinned to the same
# git rev the reference auto-harness uses for prior-art parity.
TAU3_SANDBOX_IMAGE ?= ownevo-sandbox-tau3:0.1.0

sandbox-image-tau3:
	docker build \
	    -f apps/kernel/sandbox/Dockerfile.tau3 \
	    -t $(TAU3_SANDBOX_IMAGE) \
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
# Demo rollback (W7 slice 12 / PLAN row 7.1.13)
#
# Re-points skills.head_version_id at a prior version_seq and writes an
# append-only audit entry. Documented in docs/runbooks/demo-rollback.md.
# Required: SKILL=<id> TO_VERSION=<n> REASON="..."
# Optional: ACTOR=<id> (default human:operator); DRY_RUN=1 to preview.
# ----------------------------------------------------------------------------
SKILL ?=
TO_VERSION ?=
REASON ?=
ACTOR ?= human:operator
DRY_RUN ?=

revert-skill:
	@if [ -z "$(SKILL)" ] || [ -z "$(TO_VERSION)" ] || [ -z "$(REASON)" ]; then \
	  printf 'usage: make revert-skill SKILL=<id> TO_VERSION=<n> REASON="..."  [ACTOR=...] [DRY_RUN=1]\n'; \
	  exit 2; \
	fi
	cd apps/kernel && uv run python scripts/revert_skill.py \
	    --skill '$(SKILL)' --to-version '$(TO_VERSION)' \
	    --reason '$(REASON)' --actor '$(ACTOR)' \
	    $(if $(DRY_RUN),--dry-run,)

# ----------------------------------------------------------------------------
# Bootstrap improvement loop (BL.1 + BL.3 — pre-W3, PLAN.md v3.8)
# ----------------------------------------------------------------------------

# `LOOP_ARGS=...` passes flags through to scripts/run_improvement_loop.py.
# Common: `--llm-model`, `--llm-base-url`, `--max-iterations`, `--no-seed`.
LOOP_ARGS ?=

seed-m5-baseline:
	cd apps/kernel && uv run python scripts/seed_m5_baseline.py

# Demo seed — sample workflows so the UI has something to show without
# running NL-gen first. Idempotent. PLAN row 8.4.2.
#
# `make seed-demo`               seeds workflows + eval cases only.
# `make seed-demo-with-iter`     also runs one iteration per workflow so
#                                the operator pages light up immediately
#                                (requires ANTHROPIC_API_KEY; ~1 min/workflow).
seed-demo:
	cd apps/kernel && uv run python scripts/seed_demo.py

seed-demo-with-iter:
	cd apps/kernel && uv run python scripts/seed_demo.py --with-iterations

# τ³-retail bootstrap seed (P1.5 / M5). Idempotent — safe to re-run.
# Registers the tau3-retail-v1 workflow + tau3.retail.baseline.v1.agent
# skill + 40 retail-test eval cases. Each tau-bench task ID becomes one
# eval_case row so the gate's regression check has something to lock.
TAU3_REGISTER_ARGS ?=
tau3-register:
	cd apps/kernel && uv run python scripts/tau3_register.py $(TAU3_REGISTER_ARGS)

# τ³-retail Day-1 baseline run (P1.5 / M6). Sandboxed; uses
# Sonnet 4.6 + Haiku by default. Writes one iterations row at gate-pass
# unless TAU3_BASELINE_ARGS includes --no-db. Validates the kernel
# migration matched P1's auto-harness baseline within ±5pp.
TAU3_BASELINE_ARGS ?=
tau3-baseline:
	cd apps/kernel && uv run python scripts/tau3_baseline.py $(TAU3_BASELINE_ARGS)

# τ³ trace-dir ingest (P1.5 / M8). Backfill helper — read tau2 results.json
# files into the iterations table without re-running tau2. Pass paths via
# TAU3_INGEST_ARGS or use the script directly with --results <paths>.
TAU3_INGEST_ARGS ?=
tau3-ingest:
	cd apps/kernel && uv run python scripts/tau3_ingest.py $(TAU3_INGEST_ARGS)

# τ³ improvement loop, one iteration (P1.5 / M9). Mirrors m5-bootstrap-loop's
# shape but for the τ³-retail workflow. Loop agent is qwen3-coder:30b on
# Ollama by default (free, TODO-19 validated lift driver); task agent is
# cloud Sonnet 4.6 by default (Day-1 baseline = 0.8000). Override either
# via TAU3_LOOP_ARGS.
TAU3_LOOP_ARGS ?=
tau3-loop:
	cd apps/kernel && uv run --extra agent python scripts/run_tau3_loop.py $(TAU3_LOOP_ARGS)

# τ³ B-LOCAL reproduction: winning local config from ownevo_docs/mvp-execution/tau3-results-2026-Q3.md.
# Runs 5 cycles of qwen3.6-35b-a3b LMS as proposer + task agent + user sim.
# Requires: OWNEVO_LLM_HOST pointing at LM Studio with qwen3.6-35b-a3b loaded at
# ctx=65536 with froggeric v13 template; Postgres up; sandbox image built.
# Override OWNEVO_TAU3_CYCLES (default 5) or TAU3_LOOP_ARGS to adjust.
TAU3_REPLAY_CYCLES ?= 5
tau3-replay:
	OWNEVO_TAU3_CYCLES=$(TAU3_REPLAY_CYCLES) \
	bash apps/kernel/scripts/tau3_local_loop.sh \
	    "qwen/qwen3.6-35b-a3b" lms-anthropic "replay_b_local" anthropic \
	    "anthropic/qwen/qwen3.6-35b-a3b" "anthropic/qwen/qwen3.6-35b-a3b"

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

# ----------------------------------------------------------------------------
# NL-gen smoketest (A4.4 — Phase-2 quality gate)
# ----------------------------------------------------------------------------

# `WORKFLOW=...` selects the fixture trio. Default `all` runs every
# workflow and exits 0 only if every one meets target.
# `SMOKE_ARGS=...` passes flags through to scripts/nl_gen_smoketest.py
# (e.g. SMOKE_ARGS='--from-fixtures --max-cases 3').
SMOKE_ARGS ?=

nl-gen-smoketest:
	cd apps/kernel && uv run --extra agent python scripts/nl_gen_smoketest.py \
	    --workflow $(WORKFLOW) $(SMOKE_ARGS)

# ----------------------------------------------------------------------------
# Meta-eval (A4.6 — NL-gen quality judge)
# ----------------------------------------------------------------------------

# `META_EVAL_ARGS=...` passes flags through to scripts/meta_eval.py
# (e.g. META_EVAL_ARGS='--model claude-haiku-4-5 --concurrency 4 --pretty').
META_EVAL_ARGS ?=

meta-eval:
	cd apps/kernel && uv run --extra agent python scripts/meta_eval.py \
	    $(META_EVAL_ARGS)

# ----------------------------------------------------------------------------
# Failure clustering (B3.1 + B3.2 + B3.3)
# ----------------------------------------------------------------------------

# `CLUSTER_ARGS=...` passes flags through, e.g.
#   make m5-cluster-failures CLUSTER_ARGS='--top-k 30 --pretty'
#   make m5-cluster-failures CLUSTER_ARGS='--real'   (uses sentence-transformers + UMAP + HDBSCAN + Anthropic)
CLUSTER_ARGS ?=

m5-cluster-failures:
	cd apps/kernel && uv run python scripts/cluster_m5_failures.py $(CLUSTER_ARGS)

# ----------------------------------------------------------------------------
# Cluster-label LLM eval (B3.5)
# ----------------------------------------------------------------------------

# `LABEL_EVAL_ARGS=...` passes flags through to scripts/cluster_label_eval.py
# (e.g. LABEL_EVAL_ARGS='--concurrency 4 --require-agreement 0.7 --pretty').
LABEL_EVAL_ARGS ?=

cluster-label-eval:
	cd apps/kernel && uv run --extra agent python scripts/cluster_label_eval.py \
	    $(LABEL_EVAL_ARGS)

# ----------------------------------------------------------------------------
# LLM-judge stub approver eval (W5.2)
# ----------------------------------------------------------------------------

# `LLM_JUDGE_APPROVER_ARGS=...` passes flags through, e.g.
#   make llm-judge-approver-eval LLM_JUDGE_APPROVER_ARGS='--require-agreement 0.85 --concurrency 6 --pretty'
LLM_JUDGE_APPROVER_ARGS ?=

llm-judge-approver-eval:
	cd apps/kernel && uv run --extra agent python scripts/llm_judge_approver_eval.py \
	    $(LLM_JUDGE_APPROVER_ARGS)

# ----------------------------------------------------------------------------
# NL-gen failure clustering (W5.3)
# ----------------------------------------------------------------------------

# `NL_GEN_CLUSTER_ARGS=...` passes flags through, e.g.
#   make nl-gen-cluster-failures NL_GEN_CLUSTER_ARGS='--require-clusters 3 --pretty'
#   make nl-gen-cluster-failures NL_GEN_CLUSTER_ARGS='--real'  (live Anthropic)
NL_GEN_CLUSTER_ARGS ?=

nl-gen-cluster-failures:
	cd apps/kernel && uv run python scripts/cluster_nl_gen_failures.py \
	    $(NL_GEN_CLUSTER_ARGS)

# ----------------------------------------------------------------------------
# 7-day M5 replay (W5.4)
# ----------------------------------------------------------------------------

# `REPLAY_ARGS=...` passes flags through to scripts/m5_replay_7day.py:
#   make m5-replay-7day REPLAY_ARGS='--reset --require-climbing --pretty'
#   make m5-replay-7day REPLAY_ARGS='--cycles 14'
# DB-required: OWNEVO_DATABASE_URL must point at a migrated database.
REPLAY_ARGS ?=

m5-replay-7day:
	cd apps/kernel && uv run python scripts/m5_replay_7day.py $(REPLAY_ARGS)

# ----------------------------------------------------------------------------
# 30-day M5 replay across parallel conditions (W6 — TODO-8)
# ----------------------------------------------------------------------------

# `REPLAY_30_ARGS=...` passes flags through to scripts/m5_replay_30day.py:
#   make m5-replay-30day REPLAY_30_ARGS='--conditions a,c,d --max-iterations 30 --pretty'
#   make m5-replay-30day REPLAY_30_ARGS='--reset --require-lift 0.05'
#   make m5-replay-30day REPLAY_30_ARGS='--halt-on-error -- --m5-dir /data/m5'
# DB-required: OWNEVO_DATABASE_URL must point at a migrated database.
# Each condition's iterations spawn run_improvement_loop subprocesses; the
# loop's env vars (OWNEVO_M5_DIR, OWNEVO_LLM_BASE_URL, etc.) propagate
# through.
REPLAY_30_ARGS ?=

m5-replay-30day:
	cd apps/kernel && uv run python scripts/m5_replay_30day.py $(REPLAY_30_ARGS)

# ----------------------------------------------------------------------------
# One-shot bootstrap for `make m5-replay-30day` (W6 follow-up)
# ----------------------------------------------------------------------------

# Creates the target DB (from $OWNEVO_DATABASE_URL), applies migrations, and
# seeds workflow rows for the chosen conditions with the chosen skill version
# as the parent baseline. Idempotent — re-run on an existing DB is a no-op.
#
# `REPLAY_BOOTSTRAP_ARGS=...` passes flags through to scripts/m5_replay_bootstrap.py:
#   make m5-replay-bootstrap REPLAY_BOOTSTRAP_ARGS='--workflow-prefix m5-30day-v7 --conditions a,c,d --skill-version v2'
#   make m5-replay-bootstrap REPLAY_BOOTSTRAP_ARGS='--drop-first --workflow-prefix m5-30day-v8'
# DB-required: OWNEVO_DATABASE_URL must point at the target DB URL (the DB
# itself need not exist yet; bootstrap creates it via the admin URL it
# derives by replacing dbname with 'postgres').
REPLAY_BOOTSTRAP_ARGS ?=

m5-replay-bootstrap:
	cd apps/kernel && uv run python scripts/m5_replay_bootstrap.py $(REPLAY_BOOTSTRAP_ARGS)

# ----------------------------------------------------------------------------
# NL-gen end-to-end demo loop (W6 — PLAN.md row 6.1)
# ----------------------------------------------------------------------------

# `DEMO_LOOP_ARGS=...` passes flags through to scripts/nl_gen_demo_loop.py:
#   make nl-gen-demo-loop DEMO_LOOP_ARGS='--pretty'
#   make nl-gen-demo-loop DEMO_LOOP_ARGS='--workflow credit-risk --cycles 5'
#   make nl-gen-demo-loop DEMO_LOOP_ARGS='--require-climbing --require-lift 0.1'
# Requires ANTHROPIC_API_KEY (loaded from .env or shell).
DEMO_LOOP_ARGS ?=

nl-gen-demo-loop:
	cd apps/kernel && uv run python scripts/nl_gen_demo_loop.py $(DEMO_LOOP_ARGS)

# ----------------------------------------------------------------------------
# Docker Compose — full stack (postgres + kernel API + web)
# ----------------------------------------------------------------------------

dev-up:
	docker compose up --build -d

dev-down:
	docker compose down

dev-logs:
	docker compose logs -f

dev-ps:
	docker compose ps

# ----------------------------------------------------------------------------
# Fly.io deployment (TODO-42 — docs/runbooks/fly-deploy.md)
# ----------------------------------------------------------------------------

# Run pending SQL migrations against the live Fly Postgres instance.
fly-migrate:
	flyctl ssh console -a ownevo-kernel -C \
	    "uv run --package ownevo-kernel --extra api python apps/kernel/scripts/migrate.py"

# Deploy kernel API to Fly.io (runs migrations automatically via release_command).
fly-deploy-kernel:
	flyctl deploy --config fly.toml --remote-only

# Deploy Next.js web app to Fly.io.
# Must cd into apps/web because flyctl resolves the build context against
# cwd, not the fly.toml's directory — without the cd, the remote builder
# uploads the repo root as context and `COPY package*.json ./` in the web
# Dockerfile fails to find the lockfile.
# See https://github.com/superfly/flyctl/issues/752.
fly-deploy-web:
	cd apps/web && flyctl deploy --remote-only

# Seed the live demo DB (requires ANTHROPIC_API_KEY set as a Fly secret).
fly-seed:
	flyctl ssh console -a ownevo-kernel -C \
	    "uv run --package ownevo-kernel --extra api --extra agent python apps/kernel/scripts/seed_demo.py --with-iterations"

# Tail logs from both apps.
fly-logs:
	flyctl logs -a ownevo-kernel & flyctl logs -a ownevo-web; wait

# Open a shell on the kernel machine.
fly-ssh:
	flyctl ssh console -a ownevo-kernel

# ----------------------------------------------------------------------------
# Live demo operator tooling (signed-invite mint + revoke + budget gate)
# ----------------------------------------------------------------------------
# These targets are only useful on a deploy that runs with DEMO_MODE=true
# and a token-quota gate on the design-agent + NL-gen routes. Mint
# targets require OWNEVO_DEMO_SIGNING_KEY exported locally. Revoke and
# budget targets require OWNEVO_DATABASE_URL pointed at the demo
# Postgres (or the local dev DB for testing).
#
#   make demo-invite LABEL="acme-pilot" TIER=unlimited DAYS=60
#   make demo-revoke JTI=abcd1234
#   make demo-budget-cap NOTE="hit \$5 console cap at 14:32 UTC"
#   make demo-budget-clear

DEMO_BASE_URL ?= https://demo.ownevo.ai
TIER          ?= elevated
DAYS          ?= 30

demo-invite:
	@if [ -z "$(LABEL)" ]; then echo "usage: make demo-invite LABEL=... [TIER=elevated|unlimited] [DAYS=N] [DEMO_BASE_URL=...]"; exit 2; fi
	uv run --package ownevo-kernel --extra api python apps/kernel/scripts/mint_demo_invite.py \
	    --label "$(LABEL)" --tier $(TIER) --days $(DAYS) --base-url $(DEMO_BASE_URL)

demo-revoke:
	@if [ -z "$(JTI)" ]; then echo "usage: make demo-revoke JTI=... [LABEL=...] [REASON=...]"; exit 2; fi
	uv run --package ownevo-kernel --extra api python apps/kernel/scripts/demo_admin.py revoke \
	    --jti "$(JTI)" $(if $(LABEL),--label "$(LABEL)") $(if $(REASON),--reason "$(REASON)")

demo-budget-cap:
	uv run --package ownevo-kernel --extra api python apps/kernel/scripts/demo_admin.py budget-cap \
	    $(if $(NOTE),--note "$(NOTE)")

demo-budget-clear:
	uv run --package ownevo-kernel --extra api python apps/kernel/scripts/demo_admin.py budget-clear
