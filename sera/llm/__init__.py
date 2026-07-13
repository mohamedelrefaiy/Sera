"""LLM surface — the Claude-facing adapters over the domain core.

Sera's model surface lives in `sera_prompt.py` (system prompt + SDK options) and
`sera_tools.py` (the @tool wrappers). Both are imported directly by their consumers
(api/app.py, the agent driver), so this package deliberately re-exports nothing —
importing `sera.llm` must stay side-effect-free and must not pull in the SDK.
"""
from __future__ import annotations

__all__: list[str] = []
