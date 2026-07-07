"""CLI entry: run the Target Triage agent end-to-end.

    python -m target_triage                 # run the agent on the default task
    python -m target_triage "your task..."  # run on a custom task

The agent streams its tool calls and reasoning to stdout so the run is watchable —
this is the demo surface. Requires ANTHROPIC_API_KEY (or a logged-in claude CLI).
"""
from __future__ import annotations

import asyncio
import sys

from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock, ToolUseBlock

from .agent import DEFAULT_TASK, run_triage


def _print_message(message) -> None:
    """Human-watchable stream: show tool calls and the agent's prose."""
    if isinstance(message, AssistantMessage):
        for block in message.content:
            if isinstance(block, ToolUseBlock):
                args = ", ".join(f"{k}={v}" for k, v in (block.input or {}).items())
                print(f"\n  → {block.name.split('__')[-1]}({args})")
            elif isinstance(block, TextBlock):
                print(block.text, end="")
    elif isinstance(message, ResultMessage):
        print("\n\n— run complete —")


def main() -> int:
    task = " ".join(sys.argv[1:]).strip() or DEFAULT_TASK
    print("Target Triage — agent run")
    print(f"task: {task}\n")
    try:
        asyncio.run(run_triage(task, on_message=_print_message))
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
