"""Sera — rank + adversarially verify druggable T-cell regulators.

Built for the "Built with Claude: Life Sciences" hackathon (Builder track).
New work, MIT-licensed. The pre-kickoff prototype/ dir is separate exploration.

Layout (read top to bottom = the dependency direction):
    core/     deterministic pipeline (data, ranking, verify, shortlist, controls)
    clients/  read-only external evidence (Open Targets, ClinicalTrials.gov)
    llm/      Claude-facing adapters (@tool wrappers, system prompt, SDK options)
    agent/    the driver loop that lets Claude orchestrate the tools
    api/      FastAPI web surface + uvicorn launcher

Import the layer you need directly (e.g. `from sera.core import
compute_shortlist`). This root stays import-light on purpose: pulling in core/ or
llm/ loads the screen CSVs at import, so a bare `import sera` should not.
"""
from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
