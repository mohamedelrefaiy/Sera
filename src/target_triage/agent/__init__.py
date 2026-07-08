"""Agent layer — the driver that lets Claude orchestrate the pipeline.

`loop.py` owns the SDK client lifecycle and streams messages; the prompt and tool
config it uses live in llm/. This package re-exports the stable public surface so
`from target_triage.agent import run_triage, build_options, DEFAULT_TASK` keeps
resolving even though build_options/DEFAULT_TASK are now defined in llm/prompt.py.
"""
from __future__ import annotations

from .loop import DEFAULT_TASK, SYSTEM_PROMPT, build_options, run_triage

__all__ = ["run_triage", "build_options", "DEFAULT_TASK", "SYSTEM_PROMPT"]
