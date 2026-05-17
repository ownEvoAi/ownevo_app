"""Agent SDK middleware adapters.

The kernel's `agent_tools/` module provides 5 functions the coding
agent uses (`read_skill`, `write_skill`, `run_pipeline`, `read_metrics`,
`analyze_failures`). The kernel itself is SDK-agnostic — these
adapters wrap the kernel functions in the shape each Agent SDK
expects, so the kernel stays in one piece while we add framework
support over time.

Wave 1 (shipped): ``claude_sdk`` (Anthropic Python SDK manual loop).
Wave 2 (planned): Mastra, LangGraph, OpenAI Agents SDK.

Adapter contract — to add a new SDK:

1. Create a sibling subpackage (e.g. ``mastra/``) that mirrors
   ``claude_sdk/``'s layout: ``tool_definitions.py`` (typed wrappers
   around the 5 kernel tools), ``loop.py`` (drives the SDK's agent
   loop and emits ``AgentEvent``s via a ``TraceCollector``).
2. Tool definitions **must not** expose internal kwargs to the model
   (e.g. ``include_test_fold=True`` — see
   ``docs/TRAIN_TEST_DISCIPLINE.md``). The schema the model sees
   should carry only the args the model is supposed to set.
3. Every tool call writes a ``tool_call_start`` / ``tool_call_result``
   AgentEvent pair through the trace session. Crashes are
   ``ToolCallResult(status="error", error_class=...)``.
4. The kernel state machine (gate / approvals / audit) is the source
   of truth — adapters never short-circuit a gate run or write
   audit rows directly. Anything that mutates state goes through the
   kernel's own service functions.
5. Add a fixture test under ``apps/kernel/tests/`` that round-trips
   one full iteration through the adapter (no live LLM calls; record
   + replay the SDK's wire format).

BL.3 (claude_sdk only) introduced a context-window-compaction step so
long agent loops don't blow past the model's input limit; this is
SDK-specific plumbing and lives in ``claude_sdk/compaction.py``.
"""
