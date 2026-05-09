"""τ³ retail baseline skill — v1.

This baseline is the agent the improvement loop edits. It's a thin
wrapper over tau2's `LLMAgent` that takes the domain policy + tools
from tau2 and lets the LLM reason. The proposer's job is to add
state, context-construction logic, or domain heuristics on top of
this minimum viable agent.

Mirrors the auto-harness `agent/templates/tau_bench.py` shape so the
NeoSigma prior-art improvements (notes_jit.txt) are directly
applicable as proposer hypotheses.
"""

from .agent import HarnessAgent, AGENT_INSTRUCTION

__all__ = ["HarnessAgent", "AGENT_INSTRUCTION"]
