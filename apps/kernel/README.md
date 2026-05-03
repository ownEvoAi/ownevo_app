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
