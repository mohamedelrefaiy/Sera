"""Replay a saved run log as SSE, at recorded pacing — the same wire format the
live /api/chat stream produces, so the frontend's canvas renderer is ONE code
path for "watching it happen" and "watching it happen again."

Reuses `_sse()` from app.py rather than duplicating the formatter: both live and
replayed events must serialize identically, or the frontend would need two
parsers.
"""
from __future__ import annotations

import asyncio

from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from . import runlog

MAX_PACING_SLEEP_S = 2.0  # cap each inter-event gap so replay never stalls
DEFAULT_SPEED = 1.0


async def replay_stream(run_id: str, speed: float = DEFAULT_SPEED) -> StreamingResponse:
    """Stream a saved run back as SSE. 404s cleanly if the run id is unknown.

    Pacing: each event carries a `_ts` (wall-clock seconds) stamped at write time
    in runlog.append_event. Replay sleeps for the delta between consecutive
    events, divided by `speed` (so speed=2.0 replays twice as fast), capped at
    MAX_PACING_SLEEP_S so one slow gap doesn't stall the whole replay. speed=0
    disables sleeping entirely (used by tests — replay completes instantly).
    """
    from .app import _sse  # local import: avoids a circular import at module load

    path = runlog.run_path(run_id)
    try:
        events = runlog.read_events(path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"no run logged for id {run_id}")

    async def event_stream():
        prev_ts: float | None = None
        for ev in events:
            ts = ev.get("_ts")
            if speed > 0 and prev_ts is not None and ts is not None:
                delta = max(0.0, (ts - prev_ts) / speed)
                await asyncio.sleep(min(delta, MAX_PACING_SLEEP_S))
            prev_ts = ts if ts is not None else prev_ts
            yield _sse(ev)

    return StreamingResponse(event_stream(), media_type="text/event-stream")
