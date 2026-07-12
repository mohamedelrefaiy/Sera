"""The agent loop — Claude orchestrates the Target Triage tools.

This is the project's "Claude Use" headline: Claude is not handed a finished
shortlist. It ranks candidates, adversarially verifies the ones worth trusting,
cross-checks them against Open Targets + ClinicalTrials.gov, and reasons over the
numbers to produce a mechanism-annotated, scrutiny-survived shortlist — showing
its work, including the candidates it rejects.

This module is only the DRIVER: it owns the SDK client lifecycle and streams
messages. The prompt and tool/allow-list config it consumes are passed in via
`options` (see llm/concord_prompt.build_concord_options) so the model surface is
configured by the caller, not this driver.
"""
from __future__ import annotations

from claude_agent_sdk import ClaudeSDKClient

__all__ = ["run_triage"]


async def run_triage(task: str, on_message=None, options=None) -> list:
    """Run the agent on a task, returning all messages. on_message(msg) streams them.

    `options` (a ClaudeAgentOptions) selects WHICH agent runs — e.g. the Concord
    reconciliation agent from llm/concord_prompt.build_concord_options(). The driver is
    agent-agnostic; the caller supplies the prompt + tools, so `options` is required."""
    if options is None:
        raise ValueError(
            "run_triage requires `options` (a ClaudeAgentOptions); the caller supplies the "
            "agent's prompt + tools, e.g. build_concord_options()."
        )
    messages: list = []
    async with ClaudeSDKClient(options=options) as client:
        await client.query(task)
        async for message in client.receive_response():
            messages.append(message)
            if on_message is not None:
                on_message(message)
    return messages
