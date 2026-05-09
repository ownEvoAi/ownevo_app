# ownEvo make targets — thin wrappers over `uv run`.
#
# Targets here are user-facing entrypoints from `docs/PLAN.md`. Each one
# delegates to a Python script under `apps/kernel/scripts/` so the bulk
# of the logic stays Python-side and testable.

.PHONY: help test lint m5-baseline m5-baseline-no-db sandbox-image-m5 \
        api web-dev web-build seed-approval-demo \
        seed-m5-baseline m5-bootstrap-loop eval-replay nl-gen-smoketest \
        meta-eval m5-cluster-failures cluster-label-eval \
        llm-judge-approver-eval nl-gen-cluster-failures \
        m5-replay-7day m5-replay-30day m5-replay-bootstrap nl-gen-demo-loop revert-skill

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
	@printf '  nl-gen-smoketest    A4.4 quality gate: live NL-gen + agent solver per case\n'
	@printf '                      WORKFLOW=...; SMOKE_ARGS supports --from-fixtures /\n'
	@printf '                      --max-cases / --model / --include-outcomes / --pretty.\n'
	@printf '                      W5.5: --meta-eval-gate runs the meta-eval judge after\n'
	@printf '                      NL-gen and gates the agent solver on overall=good\n'
	@printf '                      (--meta-eval-min-aggregate-score / --meta-eval-model).\n'
	@printf '  meta-eval           A4.6 NL-gen quality judge over the 10-pair eval set\n'
	@printf '  m5-cluster-failures B3.1+B3.2+B3.3: top-k worst M5 series → cluster →\n'
	@printf '                      eval-cases (CLUSTER_ARGS=...; default uses stub embedder\n'
	@printf '                      and clusterer; pass --real for ST + UMAP + HDBSCAN +\n'
	@printf '                      Anthropic)\n'
	@printf '  cluster-label-eval  B3.5: judge labeler vs hand-labeled fixtures; reports\n'
	@printf '                      agreement (LABEL_EVAL_ARGS=... pass flags; default sonnet\n'
	@printf '                      judge + haiku labeler; --require-agreement for gate)\n'
	@printf '  llm-judge-approver-eval  W5.2: LLM-judge stub approver vs 30 hand-labeled\n'
	@printf '                      (proposal, explanation) pairs; reports judge-vs-human\n'
	@printf '                      agreement + per-bucket slicing\n'
	@printf '                      (LLM_JUDGE_APPROVER_ARGS=... pass flags; default opus 4.7\n'
	@printf '                      judge; --require-agreement 0.85 for the W5.2 gate)\n'
	@printf '  nl-gen-cluster-failures  W5.3: drive NL-gen fixtures through a stub agent →\n'
	@printf '                      cluster failures (NL_GEN_CLUSTER_ARGS=... pass flags;\n'
	@printf '                      --strategy controls failure pattern; --real flips to live\n'
	@printf '                      Anthropic; --require-clusters N gates)\n'
	@printf '  m5-replay-7day      W5.4: 7-cycle synthetic M5 replay — climbing lift curve,\n'
	@printf '                      audit log + eval-set growth (REPLAY_ARGS=...; --reset for\n'
	@printf '                      clean re-runs; --require-climbing / --require-audit-entries\n'
	@printf '                      / --require-eval-growth gate)\n'
	@printf '  nl-gen-demo-loop    W6 (row 6.1): NL-gen end-to-end demo loop — agent solver\n'
	@printf '                      → cluster failures → propose instruction edit → cycle\n'
	@printf '                      (DEMO_LOOP_ARGS=...; --workflow / --cycles / --pretty /\n'
	@printf '                      --include-instructions / --require-climbing /\n'
	@printf '                      --require-lift / --require-meets-target gates;\n'
	@printf '                      requires ANTHROPIC_API_KEY)\n'
	@printf '  m5-replay-30day     W6 (TODO-8): 30-day M5 replay across parallel conditions\n'
	@printf '                      A=frozen / C=loop autonomous / D=loop gated\n'
	@printf '                      (REPLAY_30_ARGS=...; --conditions a,c,d --max-iterations N\n'
	@printf '                      --halt-on-error / --reset / --require-lift gate)\n'
	@printf '  m5-replay-bootstrap one-shot: create DB + apply migrations + seed workflows\n'
	@printf '                      for `make m5-replay-30day` (REPLAY_BOOTSTRAP_ARGS=...;\n'
	@printf '                      --workflow-prefix --conditions --skill-version v1|v2)\n'
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
