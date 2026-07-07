"""FastAPI backend: the surface a scientist's browser talks to.

Two clean paths:
  DETERMINISTIC (no API cost, always works, offline-reproducible):
    GET /api/shortlist         — the ranked + verified + annotated table
    GET /api/target/{gene}     — one gene's full evidence + verdict
  AGENT (live Claude, streamed; the interrogation layer):
    POST /api/chat             — SSE stream of the agent's tool calls + reasoning

The shortlist is computed once at startup and cached in memory (the screen is
static), so the table loads instantly on every request.

Run:  python -m target_triage.serve      (see serve.py)
"""
from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

from .shortlist import SPOTLIGHT, compute_shortlist

_WEB_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "web"
)

# Computed once at startup — the deterministic table.
_SHORTLIST: list[dict] = []


@asynccontextmanager
async def _lifespan(app: FastAPI):
    global _SHORTLIST
    _SHORTLIST = compute_shortlist()
    yield


app = FastAPI(title="Target Triage", version="0.1.0", lifespan=_lifespan)


@app.get("/api/shortlist")
def shortlist(limit: int = 50, condition: str | None = None,
              min_druggable: float = 0.0, promoted_only: bool = False) -> dict:
    """The ranked, verified shortlist. Filters are applied server-side so the
    table stays honest (rank reflects the full set; filters only narrow the view)."""
    rows = _SHORTLIST
    if condition:
        rows = [r for r in rows if r["best_condition"] == condition]
    if min_druggable > 0:
        rows = [r for r in rows if r["druggable_score"] >= min_druggable]
    if promoted_only:
        rows = [r for r in rows if r["verdict"].startswith("PROMOTE")]
    return {
        "total": len(_SHORTLIST),
        "shown": min(limit, len(rows)),
        "spotlight": list(SPOTLIGHT.keys()),
        "rows": rows[:limit],
    }


@app.get("/api/target/{gene}")
def target(gene: str) -> dict:
    row = next((r for r in _SHORTLIST if r["gene"] == gene.upper()), None)
    if row is None:
        raise HTTPException(status_code=404, detail=f"{gene} not in the shortlist")
    return row


CHAT_TIMEOUT_S = 90  # a full triage run streams within this; else we fail cleanly


@app.post("/api/chat")
async def chat(body: dict) -> StreamingResponse:
    """Stream the live agent as Server-Sent Events, incrementally. Each event is one
    JSON line: {type: tool_call|text|error|done, ...}. Imported lazily so the
    deterministic paths never depend on an API key being present.

    If no credentials are configured we fail fast with a clear event rather than
    hanging — the frontend then falls back to the deterministic shortlist."""
    task = (body or {}).get("message", "").strip()
    if not task:
        raise HTTPException(status_code=400, detail="empty message")

    if not _has_credentials():
        async def no_key():
            yield _sse({"type": "error",
                        "detail": "No Anthropic credentials configured. The shortlist "
                                  "works without the agent; set ANTHROPIC_API_KEY to enable chat."})
            yield _sse({"type": "done"})
        return StreamingResponse(no_key(), media_type="text/event-stream")

    from .agent import run_triage  # lazy: only the chat path needs the SDK

    queue: asyncio.Queue = asyncio.Queue()

    def on_message(msg):
        for ev in _message_to_events(msg):
            queue.put_nowait(ev)

    async def drive():
        try:
            await run_triage(task, on_message=on_message)
        except Exception as e:  # noqa: BLE001
            queue.put_nowait({"type": "error", "detail": str(e)})
        finally:
            queue.put_nowait({"type": "done"})

    async def event_stream():
        worker = asyncio.create_task(drive())
        try:
            while True:
                try:
                    ev = await asyncio.wait_for(queue.get(), timeout=CHAT_TIMEOUT_S)
                except asyncio.TimeoutError:
                    yield _sse({"type": "error", "detail": "agent timed out"})
                    break
                yield _sse(ev)
                if ev.get("type") == "done":
                    break
        finally:
            worker.cancel()

    return StreamingResponse(event_stream(), media_type="text/event-stream")


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event)}\n\n"


def _has_credentials() -> bool:
    """True if the SDK is likely to authenticate (env key or a logged-in CLI)."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        return True
    # a logged-in `claude` CLI stores creds under ~/.claude; treat as available
    return os.path.exists(os.path.expanduser("~/.claude/.credentials.json"))


def _message_to_events(message) -> list[dict]:
    """Translate SDK messages into small JSON events the frontend renders.

    Two directions matter:
      - AssistantMessage: the agent's tool CALLS (shown live) and prose.
      - UserMessage w/ ToolResultBlock: tool RESULTS — we sniff each for a
        __view_update__ block and forward it as a view_update event so the
        agent's action actually changes the scientist's table/drawer."""
    from claude_agent_sdk import (
        AssistantMessage, TextBlock, ToolResultBlock, ToolUseBlock, UserMessage,
    )

    events: list[dict] = []
    if isinstance(message, AssistantMessage):
        for block in message.content:
            if isinstance(block, ToolUseBlock):
                events.append({"type": "tool_call",
                               "tool": block.name.split("__")[-1],
                               "input": block.input or {}})
            elif isinstance(block, TextBlock):
                events.append({"type": "text", "text": block.text})
    elif isinstance(message, UserMessage):
        for block in getattr(message, "content", []) or []:
            if isinstance(block, ToolResultBlock):
                vu = _extract_view_update(block.content)
                if vu is not None:
                    events.append({"type": "view_update", "update": vu})
    return events


def _extract_view_update(content) -> dict | None:
    """A tool result's text may embed {'__view_update__': {...}}. Pull it out."""
    text = None
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                text = part.get("text")
                break
    if not text:
        return None
    try:
        return json.loads(text).get("__view_update__")
    except (json.JSONDecodeError, AttributeError):
        return None


# Static frontend last, so /api/* wins routing.
if os.path.isdir(_WEB_DIR):
    app.mount("/", StaticFiles(directory=_WEB_DIR, html=True), name="web")
