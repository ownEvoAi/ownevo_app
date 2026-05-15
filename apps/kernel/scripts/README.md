# Kernel scripts

Operational and developer-facing scripts that live alongside the kernel
package. None of these ship as part of the kernel API; they are run via
`uv run --directory apps/kernel python scripts/<name>.py` or via `make`
targets.

When you add a script: drop its purpose into the right section below and,
if it's user-facing, wire a `make` target.

---

## Loop & benchmarks (the headline runners)

| Script | `make` target | What it does |
|---|---|---|
| `run_improvement_loop.py` | — | Bootstrap improvement-loop entrypoint. Drives one or more iterations against a workflow with a real Anthropic / local-LLM agent. The script most people run when debugging the loop end-to-end. |
| `run_tau3_loop.py` | `tau3-loop` | τ³ improvement-loop entrypoint. Same shape as `run_improvement_loop` but pointed at τ³-bench tasks. |
| `nl_gen_demo_loop.py` | `nl-gen-demo-loop` | W6 NL-gen end-to-end demo loop — generates a workflow from an NL description, runs N iterations, prints lift. |
| `m5_baseline.py` | `m5-baseline` | Day-1 M5 baseline run. Establishes the val_score the improvement loop has to beat. |
| `m5_replay_7day.py` | `m5-replay-7day` | 7-day M5 replay — short rolling-horizon sanity check. |
| `m5_replay_30day.py` | `m5-replay-30day` | 30-day M5 replay across parallel conditions (A: frozen baseline, C: loop autonomous, D: loop gated). Condition B (static frontier LLM) deferred. |
| `m5_replay_bootstrap.py` | — | One-shot bootstrap for `make m5-replay-30day` — seeds workflow rows + first baseline. |
| `tau3_baseline.py` | `tau3-baseline` | τ³-retail Day-1 baseline runner. |
| `tau3_ingest.py` | `tau3-ingest` | Ingest a τ³-bench trace directory into the DB. |
| `tau3_register.py` | `tau3-register` | Bootstrap seed for the τ³-retail workflow. |
| `tau3_inspect_task.py` | `tau3-inspect-task` | Re-analyze τ³ per-task traces from the DB. |

## NL-gen quality gates

| Script | `make` target | What it does |
|---|---|---|
| `nl_gen_smoketest.py` | `nl-gen-smoketest` | End-to-end NL-gen smoketest — generates the four artifacts from a known prompt and asserts schema validity. The A4.4 quality gate. |
| `meta_eval.py` | `meta-eval` | A4.6 NL-gen quality judge — LLM-judge agreement check on generated artifacts vs hand-labeled references. Gate threshold ≥0.7. |
| `regen_nl_gen_schemas.py` | — | Regenerate the frozen JSON schemas in `nl_gen/schemas/` after editing the Pydantic models. CI checks the committed schemas match. |

## Failure clustering

| Script | `make` target | What it does |
|---|---|---|
| `cluster_m5_failures.py` | `m5-cluster-failures` | M5 failure clustering pipeline end-to-end: embed → UMAP → HDBSCAN → LLM label. Idempotent (see migration 0002 fingerprint). |
| `cluster_nl_gen_failures.py` | — | Same pipeline but pointed at NL-gen failures (W5.3). |
| `cluster_label_eval.py` | `cluster-label-eval` | B3.5 — evaluates the cluster labeller against hand-labeled clusters. |

## Approver + eval probes

| Script | `make` target | What it does |
|---|---|---|
| `llm_judge_approver_eval.py` | `llm-judge-approver-eval` | W5.2 — evaluates the LLM-judge approver against human approval decisions. Drives the 0.85 agreement threshold. |
| `eval_replay.py` | `eval-replay` | A4.3 — replay an eval-case set against a stored skill version. Used to verify a proposed skill's actual lift before approval. |

## Local-model probes (dogfooding)

The probes hit local Ollama / LM Studio backends to sanity-check tool-calling and skill-codegen quality. See [`../../../docs/local-model-testing.md`](../../../docs/local-model-testing.md) for the full picture.

| Script | What it does |
|---|---|
| `probe_anthropic_models.py` | Probe which Anthropic models a given API key can call. |
| `probe_tool_calling.py` | Quick tool-call sanity check for a local model. |
| `probe_skill_quality.py` | Does the model produce structurally valid skill files? |
| `sweep_probes.py` | Drive `probe_tool_calling` + `probe_skill_quality` across many models. |
| `_sweep_parse_log.py` | Parse a `nl_gen_smoketest` JSONL log and emit one summary table row. |
| `sweep_candidates_smoke.txt` | List of model IDs for the smoke sweep. |
| `sweep_candidates_full.txt` | List of model IDs for the full sweep. |
| `run_lmstudio_sweep.sh` | Full `--from-fixtures` gate sweep against all models loaded in LM Studio (OpenAI-compat `/v1/chat/completions`). |
| `run_ollama_sweep.sh` | Same, against an Ollama daemon. |
| `run_nl_gen_smoke.sh` | A4.4 NL-gen smoketest dogfood against local Ollama via a LiteLLM Anthropic-compat proxy. |
| `tau3_local_loop.sh` | τ³-retail P2 loop driven by a local model. Loop agent local, task agent + simulator stay on cloud Anthropic. |
| `tau3_local_sweep.sh` | Sequential local-model diagnostic sweep through the P2 loop. Each model runs under its own `--workflow-id` so its gate history is independent. |
| `tau3_sonnet_loop.sh` | Sonnet 4.6 P2 improvement loop for τ³-retail — N gate cycles unattended (the cloud anchor used to compare local models against). |

## DB lifecycle

| Script | `make` target | What it does |
|---|---|---|
| `migrate.py` | `db-migrate` | Apply pending migrations in order. See [`../../../docs/MIGRATIONS.md`](../../../docs/MIGRATIONS.md). |
| `revert_skill.py` | `revert-skill` | Re-point a skill HEAD at an earlier `version_seq`. Demo rollback runbook backing script. See [`../../../docs/runbooks/demo-rollback.md`](../../../docs/runbooks/demo-rollback.md). |

## Demo seeds

| Script | What it does |
|---|---|
| `seed_demo.py` | Insert sample workflows so the workspace UI has something to show on a fresh DB. |
| `seed_approval_demo.py` | Seed a demo proposal in `gate-passed` state for manual W2.5 testing. |
| `seed_m5_baseline.py` | Bootstrap seed for the M5 demand-prediction workflow (BL.1). |

---

## Conventions

- **Top docstring is the README entry.** Anything readable from `ast.get_docstring()` ends up easy to surface. New scripts: lead with a one-liner.
- **`make` target naming follows the script name.** `run_improvement_loop.py` → no make target (it's developer-only); `m5_replay_30day.py` → `make m5-replay-30day`.
- **Async + Postgres connect:** all DB-using scripts read `OWNEVO_DATABASE_URL`. See [`../../../docs/ENV_VARS.md`](../../../docs/ENV_VARS.md).
- **Exit codes:** loop runners exit 0 on success, 1 on infra failure, 2 on benchmark failure (e.g. baseline missing). Scripts that diverge document their exit codes in their docstring.

If you add a script that doesn't fit a section above, prefer adding a new section here over hiding it.
