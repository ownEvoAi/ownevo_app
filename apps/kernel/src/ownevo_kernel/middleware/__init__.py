"""Agent SDK middleware adapters.

The kernel's `agent_tools/` module provides 5 functions the coding
agent uses (`read_skill`, `write_skill`, `run_pipeline`, `read_metrics`,
`analyze_failures`). The kernel itself is SDK-agnostic — these
adapters wrap the kernel functions in the shape each Agent SDK
expects, so the kernel stays in one piece while we add framework
support over time.

Wave 1: `claude_sdk` (Anthropic Python SDK manual loop). Wave 2 (per
PLAN.md): Mastra, LangGraph, OpenAI Agents SDK.
"""
