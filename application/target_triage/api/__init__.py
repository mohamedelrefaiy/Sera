"""Web layer — the FastAPI surface a scientist's browser talks to.

`app.py` defines the routes (deterministic shortlist + streamed agent chat) and
mounts the static web/ app; `serve.py` is the uvicorn launcher. Re-exporting `app`
here lets `uvicorn.run("target_triage.api:app")` resolve the package attribute,
so the entry point stays stable regardless of which module defines the instance.
"""
from __future__ import annotations

from .app import app

__all__ = ["app"]
