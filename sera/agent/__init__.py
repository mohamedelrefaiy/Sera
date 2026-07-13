"""Agent layer — the driver that lets Claude orchestrate the pipeline.

`loop.py` owns the SDK client lifecycle and streams messages; the caller passes the
prompt + tool config in via `options` (see llm/sera_prompt.build_sera_options).
"""
from __future__ import annotations

from .loop import run_triage

__all__ = ["run_triage"]
