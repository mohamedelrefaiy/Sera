"""LLM surface — the Claude-facing adapters over the domain core.

Concord's model surface lives in `concord_prompt.py` (system prompt + SDK options) and
`concord_tools.py` (the @tool wrappers). Both are imported directly by their consumers
(api/app.py, the agent driver), so this package deliberately re-exports nothing —
importing `target_triage.llm` must stay side-effect-free and must not pull in the SDK.
"""
from __future__ import annotations

__all__: list[str] = []
