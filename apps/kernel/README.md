# kernel

Python kernel — agent runtime, eval harness, failure clustering, regression gate, background jobs.

This is where the IP lives. See `../../CLAUDE.md` and `../../../ownevo_docs/ownEvo_MVP.md`.

Planned modules (Week 1-2 lift):
- `types.py` — typed `AgentEvent` and core domain types
- `evolution/` — tracker → reflector → curator → proposer (greenfield; 4-stage pattern shaped by `startup2026/core/agentos_harness/evolution/`; see `docs/SPIKE-RESULT.md`)
- `trace/` — OTel intake + Langfuse client
- `skills/` — skill registry + retention contracts
- `eval/` — Inspect AI integration
- `gate/` — 3-step regression gate runner

## A4.4 local model verdicts (from-fixtures gate, OpenAI-compat path)

Tested via `scripts/run_ollama_sweep.sh` against Ollama at `OWNEVO_OLLAMA_HOST`
(OpenAI `/v1` direct, no proxy). `OLLAMA_CONTEXT_LENGTH=65536` set server-side.

**3/3 pass (all workflows):**
- `qwen3.5:27b` — demand 0.60 ✅, credit 0.42 ✅, contract 1.00 ✅  ← recommended local model
- `qwen3:30b-instruct` — demand 0.60 ✅, credit 0.42 ✅, contract 1.00 ✅ (earlier run)

**LM Studio (`scripts/run_lmstudio_sweep.sh`, 32k context via load API):**
- `granite-4.1-8b` — demand 0.80 ✅, credit 0.42 ✅, contract 0.83 ✅

See `infra/litellm/ollama.yaml` for the LiteLLM proxy config used in hybrid
NL-gen (frontier) + local agent runs.
