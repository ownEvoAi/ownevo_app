# tau3_retail_v1 — τ³-bench retail baseline skill

The agent the improvement loop optimizes against Sierra's tau-bench
retail domain (114 tasks split into 74 train / 40 test).

## What this is

A thin wrapper over `tau2.agent.llm_agent.LLMAgent` that:
- Reads the domain policy injected by tau2 at agent-construction time
- Lets the LLM reason about task instructions + tool calls + user replies
- Returns assistant messages (with content or tool_calls) per turn

Same shape as the reference auto-harness's `agent/templates/tau_bench.py` so
the iteration patterns documented in `prior-art notes` (multi-order ID
directive, [COMPUTED] annotations, _product_data_fetched flag, device
reboot sequencing) are directly applicable as proposer hypotheses.

## What the loop is allowed to change

Per the `retention` block in `agent.py`:

| Allowed | Not allowed |
|---|---|
| `AGENT_INSTRUCTION` (system prompt body) | Class name `HarnessAgent` |
| Adding fields to `HarnessState` | Breaking `LLMAgent` superclass contract |
| Wrapping `generate_next_message` | Returning empty AssistantMessage (no content + no tool_calls) |
| Adding helper methods | |

## How the gate scores it

`SandboxedTauBenchRunner` runs this skill against the 40-task retail
test split via tau2's `run_domain`. `BenchmarkResult.val_score` is the
arithmetic mean of per-task rewards (0.0 or 1.0 for completion). Infra
errors count as 0.0 in val_score so the agent can't game the score by
crashing tasks.

## Reference baselines

| Task agent | val_score on retail test (40 tasks) |
|---|---|
| Published reference (GPT-5.4, no loop) | 0.560 |
| **ownEvo P1 baseline (Sonnet 4.6 + Haiku user sim)** | **0.800** |
| qwen3-coder:30b on Ollama | 0.000 (writes fail) |
| qwen3-coder-30b on LMS | 0.000 (writes fail, cleaner reads 94%) |
| ministral-3-14b-reasoning on LMS | 0.000 (Jinja template incompatible) |
| gemma4:26b on Ollama | killed mid-run; first 2 tasks max_steps |

See `docs/TAU3_LOCAL_TESTPLAN.md` for the full sanity-test history.
