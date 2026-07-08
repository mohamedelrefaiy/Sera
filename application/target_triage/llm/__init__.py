"""LLM surface — the Claude-facing adapters over the domain core.

`tools.py` holds the @tool wrappers the agent calls; `prompt.py` holds the system
prompt, default task, and SDK options. This layer adapts core/ for the model; core/
knows nothing about Claude. The agent loop (agent/) consumes what is assembled here.
"""
from __future__ import annotations

from .prompt import DEFAULT_TASK, SYSTEM_PROMPT, build_options
from .tools import ALLOWED_TOOLS, build_server

__all__ = [
    "DEFAULT_TASK",
    "SYSTEM_PROMPT",
    "build_options",
    "ALLOWED_TOOLS",
    "build_server",
]
