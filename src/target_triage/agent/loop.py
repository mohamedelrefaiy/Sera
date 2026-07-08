"""The agent loop — Claude orchestrates the Target Triage tools.

This is the project's "Claude Use" headline: Claude is not handed a finished
shortlist. It ranks candidates, adversarially verifies the ones worth trusting,
cross-checks them against Open Targets + ClinicalTrials.gov, and reasons over the
numbers to produce a mechanism-annotated, scrutiny-survived shortlist — showing
its work, including the candidates it rejects.

This module is only the DRIVER: it owns the SDK client lifecycle and streams
messages. The prompt, default task, and tool/allow-list config it consumes live in
llm/ (see llm/prompt.py, llm/tools.py) so the model surface is edited independently.

Run:  python -m target_triage  (see __main__.py)
"""
from __future__ import annotations

from claude_agent_sdk import ClaudeSDKClient

from ..llm.prompt import DEFAULT_TASK, SYSTEM_PROMPT, build_options

__all__ = ["run_triage", "build_options", "DEFAULT_TASK", "SYSTEM_PROMPT"]


async def run_triage(task: str, on_message=None) -> list:
    """Run the agent on a task, returning all messages. on_message(msg) streams them."""
    options = build_options()
    messages: list = []
    async with ClaudeSDKClient(options=options) as client:
        await client.query(task)
        async for message in client.receive_response():
            messages.append(message)
            if on_message is not None:
                on_message(message)
    return messages
