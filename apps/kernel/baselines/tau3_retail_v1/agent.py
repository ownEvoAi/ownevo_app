"""
---
id: tau3.retail.baseline.v1.agent
kind: python
created_by: P1.5-M4
capability_tags:
  - tau3
  - retail
  - baseline
  - agent
retention:
  stateless: true
---

Improvement-loop contract (informational; not enforced by SKILL_FORMAT
schema, which only validates retention.stateless today):

  improvement_target: tau3_retail_test_val_score
  invariants:
    - Class name MUST stay `HarnessAgent` (runner imports by name)
    - MUST subclass tau2.agent.llm_agent.LLMAgent
    - generate_next_message MUST return AssistantMessage with content
      or tool_calls (tau2's strict validator rejects empty)
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import cast

from tau2.agent.base_agent import (
    ValidAgentInputMessage,
    is_valid_agent_history_message,
)
from tau2.agent.llm_agent import LLMAgent
from tau2.data_model.message import (
    AssistantMessage,
    Message,
    MultiToolMessage,
    SystemMessage,
)
from tau2.utils.llm_utils import generate

# AGENT_MODEL is set by SandboxedTauBenchRunner via the sandbox env.
# Used as fallback when self.llm isn't passed by tau2's runner. Read at
# import time matches the auto-harness template — proposer iterations
# can reassign or wrap this.
AGENT_MODEL: str = os.environ.get("AGENT_MODEL", "")

AGENT_INSTRUCTION = """
You are a helpful assistant that completes tasks according to the <policy> provided below.
""".strip()


@dataclass
class HarnessState:
    """Per-conversation agent state.

    The proposer is expected to add fields here as it learns about
    domain-specific structure (e.g., NeoSigma's `_product_data_fetched`
    flag — see `ownevo_docs/competitors/neosigma.md` notes for prior-art
    patterns). v1 is the minimum needed to satisfy tau2's strict
    message-history invariants.
    """

    messages: list[Message] = field(default_factory=list)


class HarnessAgent(LLMAgent):
    """The skill the τ³ improvement loop optimizes.

    The retention contract (frontmatter above) defines what the gate
    enforces. The proposer can:
      - Edit AGENT_INSTRUCTION (the system prompt body)
      - Add fields to HarnessState
      - Wrap or reshape generate_next_message
      - Add helper methods on this class

    The proposer must not:
      - Change the class name HarnessAgent (the runner's import path
        depends on it)
      - Break the LLMAgent superclass contract (used by tau2.run_domain)
    """

    @property
    def system_prompt(self) -> str:
        if self.domain_policy:
            return (
                "<instructions>\n"
                f"{AGENT_INSTRUCTION}\n"
                "</instructions>\n"
                "<policy>\n"
                f"{self.domain_policy}\n"
                "</policy>"
            )
        return AGENT_INSTRUCTION

    def get_init_state(
        self,
        message_history: list[Message] | None = None,
    ) -> HarnessState:
        if message_history is None:
            message_history = []
        assert all(is_valid_agent_history_message(m) for m in message_history)
        return HarnessState(messages=list(message_history))

    def generate_next_message(
        self,
        message: ValidAgentInputMessage,
        state: HarnessState,
    ) -> tuple[AssistantMessage, HarnessState]:
        if isinstance(message, MultiToolMessage):
            state.messages.extend(message.tool_messages)
        else:
            state.messages.append(message)

        system = SystemMessage(role="system", content=self.system_prompt)
        reasoning_effort = os.environ.get("AGENT_REASONING_EFFORT", "")
        generate_kwargs = (
            {"reasoning_effort": reasoning_effort} if reasoning_effort else {}
        )
        generate_kwargs.update(self.llm_args or {})
        response = cast(
            AssistantMessage,
            generate(
                model=self.llm or AGENT_MODEL,
                tools=self.tools,
                messages=[system, *state.messages],
                **generate_kwargs,
            ),
        )
        state.messages.append(response)
        return response, state
