"""The replay spine: persist every SSE event to a newline-delimited JSON log so a
chat run can be re-streamed later through the exact same renderer that draws it
live (see replay.py). This module owns the on-disk format; app.py only calls
`new_run()` once per request and `append_event()` per event — it never touches
the file directly.

Format: one JSON object per line (JSONL), in emission order, each carrying an
additive `_ts` field (wall-clock `time.time()`, seconds) stamped at write time.
`_ts` is monotonic-enough for replay pacing (inter-event deltas) and is ignored
by the browser, which only recognizes the event's own `type` field.

Nothing here mutates a passed-in event dict — `append_event` builds a new dict
with `_ts` added rather than doing `event["_ts"] = ...` in place, so callers can
safely reuse the same event object (e.g. also yielding it to the SSE stream)
without it silently growing a field.
"""
from __future__ import annotations

import json
import os
import sys
import time
import uuid

# runs/ lives under application/ (this file is application/sera/api/runlog.py,
# so three dirname() hops up gets to application/).
RUNS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "runs",
)


def new_run() -> tuple[str, str]:
    """Allocate a fresh run id and its log path. Returns (run_id, path).

    The id is `uuid4().hex` — filesystem-safe (hex digits only, no separators to
    escape) and collision-safe enough for a hackathon-scale run volume. Caller is
    responsible for creating the file (first `append_event` call does this via
    the directory-creation below).
    """
    run_id = uuid.uuid4().hex
    os.makedirs(RUNS_DIR, exist_ok=True)
    path = os.path.join(RUNS_DIR, f"{run_id}.jsonl")
    return run_id, path


def append_event(path: str, event: dict) -> None:
    """Append one event as one JSON line, flushed immediately so a crash mid-run
    still leaves a valid, replayable prefix (no buffered line ever gets lost).

    Stamps an additive `_ts` (wall-clock seconds) without mutating `event` — a new
    dict is written out, the caller's original is untouched.

    On write failure (disk full, permissions, etc.) this must NOT break the live
    stream: the error is caught explicitly and a warning is printed to stderr so
    the failure is visible, but no exception propagates to the SSE loop.
    """
    stamped = {**event, "_ts": time.time()}
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(stamped) + "\n")
            f.flush()
            os.fsync(f.fileno())
    except OSError as e:
        print(f"[runlog] WARNING: failed to append event to {path}: {e}",
              file=sys.stderr)


def read_events(path: str) -> list[dict]:
    """Read back a run log as an ordered list of event dicts (including `_ts`).
    Raises FileNotFoundError if the run id doesn't exist — callers translate that
    into a 404 (see replay.py) or let it surface in tests.

    Malformed lines (partial write from a crash) are skipped rather than raising,
    since the append contract only guarantees a valid PREFIX survives a crash —
    a trailing truncated line is expected in that failure mode, not corruption to
    fail loudly on.
    """
    events: list[dict] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return events


def run_path(run_id: str) -> str:
    """The expected log path for a run id, whether or not it exists yet."""
    return os.path.join(RUNS_DIR, f"{run_id}.jsonl")
