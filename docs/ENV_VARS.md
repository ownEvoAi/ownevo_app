# Environment variables

**Authority:** when this doc disagrees with code, code wins — grep for the
variable name and update this doc to match.

Every environment variable read by the kernel, the web app, the sandbox
images, or the dev/benchmarking scripts. Grouped by what they configure.

---

## 1. Runtime — required for any deploy

| Name | Required? | Default | Read by | Notes |
|---|---|---|---|---|
| `OWNEVO_DATABASE_URL` | **yes** | (none) | `db.py`, `replay/thirty_day.py`, `apps/web/lib/api.ts` (server-side) | Postgres connection string. `db.ENV_VAR` constant. Tests fail fast if unset. |
| `ANTHROPIC_API_KEY` | **yes** for NL-gen + agent runs | (none) | `api/routes/nl_gen.py`, `api/routes/workflows.py` (eval-cases/generate, iterations/run) | Returns HTTP 400 from `/api/nl-gen/generate` if missing. |
| `ANTHROPIC_BASE_URL` | optional | (SDK cloud default) | `api/_anthropic_client.py` → every `AsyncAnthropic` site (`nl_gen`, `workflows`) | Point the kernel at a self-hosted endpoint (LMS Anthropic-compat, LiteLLM proxy) without touching code. Defensive: an empty string from `${VAR:-}` is wiped before reading (docker-compose interpolation trap). See [`local-model-testing.md`](local-model-testing.md). |
| `OWNEVO_KERNEL_API_URL` | optional | `http://localhost:8000` | `apps/web/lib/api.ts` | Where the Next.js server-side fetcher reaches the kernel. Override for Docker Compose or staging. |

## 2. Demo / production hardening

| Name | Required? | Default | Read by | Notes |
|---|---|---|---|---|
| `DEMO_MODE` | optional | unset (= off) | `api/deps.py` (`require_not_demo_mode`) | When set to `"true"` (case-insensitive), write endpoints return **HTTP 503**. Used on the public demo deploy. Web app reads its own `DEMO_MODE` to surface a banner — kernel + web set it independently. |
| `OWNEVO_CORS_ORIGINS` | optional | dev origins | `api/app.py` line 87 | Comma-separated allowed origins. Falls back to a dev default when unset. In production set with `flyctl secrets set OWNEVO_CORS_ORIGINS=https://ownevo-web.fly.dev,https://demo.ownevo.ai`. |

## 3. Local LLM backends (dev / dogfooding)

All read by the dev scripts in `apps/kernel/scripts/`, none by the kernel
HTTP API itself. See [`local-model-testing.md`](local-model-testing.md)
for which backend takes which combination.

| Name | Default | Used by | What it does |
|---|---|---|---|
| `OWNEVO_LLM_BASE_URL` | LM Studio default | `run_improvement_loop.py`, `nl_gen_demo_loop.py`, `probe_skill_quality.py` | Anthropic-compatible LLM base URL. |
| `OWNEVO_LLM_MODEL` | `qwen/qwen3-coder-30b` | same | Model id to send. |
| `OWNEVO_LLM_API_FORMAT` | `anthropic` | `run_improvement_loop.py`, `probe_tool_calling.py` | `anthropic` (LM Studio `/v1/messages`) or `openai` (Ollama / vLLM `/v1/chat/completions`). |
| `OWNEVO_LLM_API_KEY` | (unset) | same | API key for the LLM endpoint. Ignored by local Ollama / LM Studio. |
| `OWNEVO_LLM_HOST` | (machine-specific) | shell scripts (`tau3_local_loop.sh`, sweep helpers) | Host/port of the local LLM box. Used to build base URLs in shell. |
| `OWNEVO_LMSTUDIO_HOST` | `http://localhost:1234` | `run_lmstudio_sweep.sh` | LM Studio endpoint for sweep runs. |
| `OWNEVO_OLLAMA_HOST` | `http://localhost:11434` | `run_nl_gen_smoke.sh` | Ollama endpoint. |
| `OPENAI_API_KEY` | (unset) | `tau3_local_loop.sh`, `tau3_local_sweep.sh` | Set to `lm-studio` when proxying. |
| `OPENAI_API_BASE` / `OPENAI_BASE` | (unset) | tau3 scripts | OpenAI-compat base URL when proxying. |
| `ANTHROPIC_API_BASE` / `ANTHROPIC_BASE` | (unset) | tau3 scripts | Anthropic-compat base URL when proxying. |
| `ANTHROPIC_AUTH_TOKEN` | (unset) | tau3 scripts | Auth header when proxying Anthropic. |

## 4. Improvement loop / agent solver

| Name | Default | Used by | What it does |
|---|---|---|---|
| `OWNEVO_AGENT_MAX_ITERATIONS` | (per script) | `run_improvement_loop.py` | Caps the iteration count for ad-hoc loop runs. |
| `OWNEVO_M5_DIR` | `./data/m5` | M5 loader, `cluster_m5_failures.py`, `run_improvement_loop.py` | Path to M5 CSVs. |
| `OWNEVO_EVAL_EXTRA` | (unset) | `test_eval_runner_inspect_task.py` | Skip-gate for the Inspect AI integration test. Set to `1` and install `ownevo-kernel[eval]` to run it. |
| `OWNEVO_NL_GEN_LIVE_MODEL` | `claude-haiku-4-5-20251001` | NL-gen live tests (`test_nl_gen_*.py`) | Model id used by the live-API snapshot tests. |
| `OWNEVO_ANTHROPIC_LIVE` | unset (= off) | NL-gen live tests | Set to `1` to opt the live-API snapshot tests into actually hitting Anthropic. |

## 5. Per-surface model overrides

Each LLM-calling kernel surface has a hardcoded `DEFAULT_MODEL` (matched to its quality/cost profile) and an `OWNEVO_*_MODEL` env var that overrides it. Caller-passed `model=` arguments still win over both. Pair these with `ANTHROPIC_BASE_URL` (§1) to send the calls to a local backend; see [`local-model-testing.md`](local-model-testing.md) for the surface→protocol map and validated local picks.

The four NL-gen generators (`workflow_spec`, `sim`, `metric`, `eval`) also accept a `max_retries` kwarg (default 2 → 3 attempts total). On `ValidationError`, the generator sends the pydantic errors back as a `tool_result` with `is_error=True` so the model can correct on the next turn. Cloud frontier models pass on attempt 1 (zero cost); local models benefit from the retries. The pipeline-level `generate_full_pipeline(..., max_retries=N)` threads this through all four steps uniformly.

| Name | Default | Surface | Notes |
|---|---|---|---|
| `OWNEVO_NL_GEN_MODEL` | `claude-opus-4-7` | `nl_gen/{workflow_spec,sim,metric,eval}_generator.py` | Covers all four forced-tool generators in the NL-gen pipeline. Read at module import; also read fresh at request time by `api/routes/nl_gen.py` for backward compat. |
| `OWNEVO_INSTRUCTION_PROPOSER_MODEL` | `claude-sonnet-4-6` | `nl_gen/instruction_proposer.py` | The evolution loop's instruction-edit proposer. Prompt-cache hit rate matters more than peak quality here. |
| `OWNEVO_META_EVAL_MODEL` | `claude-opus-4-7` | `nl_gen/meta_eval/judge.py` | NL-gen quality judge — calibration anchor for the W5 ≥0.7 agreement gate. Use the strongest model available. |
| `OWNEVO_AGENT_SOLVER_MODEL` | `claude-haiku-4-5-20251001` | `eval_runner/agent_solver.py` | Per-case classifier. Haiku is the cheap default — fine for single-tool classifiers. **For multi-tool workflows (3+ tools, multi-step reasoning) override to `claude-sonnet-4-6`** — Haiku produces scattered, low-signal failures that don't cluster (proposer returns `gate-blocked-no-improvement`); Sonnet gives structured failures the loop can grip on. Empirically: 50% → 100% val_score on a 6-tool retail workflow with only ~$0.15-0.25/iteration delta. Caller can also pass an `openai_client` to switch protocol. |
| `OWNEVO_CLUSTER_LABEL_MODEL` | `claude-sonnet-4-6` | `clustering/default_impl.py` | The cluster-labeller. Cluster labels are short factual strings. |
| `OWNEVO_CLUSTER_JUDGE_MODEL` | `claude-opus-4-7` | `clustering/label_eval/judge.py` | The label-judge for the labeller. Must be a different model than the labeller (D4). |
| `OWNEVO_APPROVER_MODEL` | `claude-opus-4-7` | `approvers/llm_judge/judge.py` | LLM-as-judge approver — the W5.2 ≥0.85 calibration anchor. |
| `OWNEVO_LOOP_MODEL` | `claude-opus-4-7` | `middleware/claude_sdk/runner.py` | Default model for the kernel improvement loop's manual agentic loop. The τ³-tested local pick is `qwen/qwen3.6-35b-a3b` via LMS Anthropic-compat. |

## 6. τ³-bench / sandbox image

| Name | Default | Used by | What it does |
|---|---|---|---|
| `AGENT_MODEL` | (none) | `benchmark/tau3/runner.py` line 140, `sandbox/tau2_patches.py` | Model id the τ³ task agent uses. Wired through the sandbox env. |
| `USER_MODEL` | `=AGENT_MODEL` | `benchmark/tau3/runner.py` line 141 | Model id the simulated user uses; defaults to whatever `AGENT_MODEL` is. |
| `TAU2_DATA_DIR` | `/tau2_data` (in image) | `benchmark/tau3/runner.py`, `sandbox/Dockerfile.tau3` | tau2 reads this at module import; the Docker image bakes it as `ENV TAU2_DATA_DIR=/tau2_data`. |

## 7. Web app (Next.js)

| Name | Default | Notes |
|---|---|---|
| `NODE_ENV` | `development` | Standard Next.js. |
| `OWNEVO_KERNEL_API_URL` | `http://localhost:8000` | See §1. |
| `DEMO_MODE` | unset | When set to `"true"`, the web app renders the demo banner. Set independently of the kernel `DEMO_MODE`; the kernel enforces, the web informs. |

---

## How to set them

- **Local dev:** export in your shell (`~/.zshrc`) or use a `.env` file (gitignored — see `CLAUDE.local.md` for the convention).
- **Fly.io deploys:** secrets via `flyctl secrets set NAME=VALUE -a ownevo-kernel` (kernel) and `-a ownevo-web` (web). Non-secret vars go in `fly.toml`'s `[env]` block. See [`runbooks/fly-deploy.md`](runbooks/fly-deploy.md).
- **CI:** set in workflow YAML or repository secrets. The test suite needs none of them — tests that touch live APIs are gated by `OWNEVO_ANTHROPIC_LIVE=1` or `OWNEVO_EVAL_EXTRA=1`.

## Discovering more

If you add a new env-var read, **update this table in the same PR.** Quick audit:

```bash
grep -rohE '(OWNEVO_[A-Z_]+|ANTHROPIC_[A-Z_]+|OPENAI_[A-Z_]+|TAU2_[A-Z_]+)' apps/ packages/ Makefile fly.toml | sort -u
```
