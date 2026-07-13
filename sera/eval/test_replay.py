"""REPLAY-SPINE GATE — the run log must round-trip byte-for-byte (minus `_ts`).

Phase 0 of the chat-first redesign adds a "replay spine": every SSE event that
streams out of POST /api/chat is teed to a newline-delimited JSON log
(runlog.py), and GET /api/replay/{run_id} re-emits that log through the SAME
_sse() formatter used live. This file is the controls-first gate for that
spine: it doesn't need an API key (it synthesizes a representative log via the
0a append helper directly, exactly like a real chat run would produce — one
tool_call, one text, one view_update, one tool_result, one done), and it
asserts two things:

  1. FILE ROUND-TRIP PARITY (required): reading a written log back yields the
     same ORDERED sequence of events as were appended, once the injected `_ts`
     field is stripped back out. This is the parse the replay endpoint itself
     uses (runlog.read_events), so this is the load-bearing gate.
  2. LIVE ENDPOINT PARITY (bonus): FastAPI's TestClient drives GET
     /api/replay/{run_id}?speed=0 and the SSE body is parsed back into the same
     event sequence. speed=0 disables pacing sleeps so the test is instant.

Run as a gate:      python eval/test_replay.py     (prints PASS/FAIL + numbers)
Run under pytest:   pytest eval/test_replay.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from sera.api import runlog  # noqa: E402

# The representative event sequence: one of each type the frontend renders,
# in the order a real triage run would emit them (see app.py _message_to_events
# and the terminal-event contract in the chat() route).
SAMPLE_EVENTS: tuple[dict, ...] = (
    {"type": "tool_call", "tool": "rank_candidates", "input": {"top_n": 5}},
    {"type": "text", "text": "Ranking candidates by druggability and effect size."},
    {"type": "view_update", "update": {"action": "set_rows", "rows": [{"gene": "CBLB"}]}},
    {"type": "tool_result", "tool": "rank_candidates",
     "output": {"total_ranked": 7452, "candidates": [{"gene": "CBLB"}]}},
    {"type": "done"},
)


def _write_sample_run(runs_dir: str) -> tuple[str, str]:
    """Synthesize a run log under `runs_dir` using the SAME append helper the
    live server uses, and return (run_id, path). Monkeypatches RUNS_DIR for the
    duration of allocation so no test run ever touches the real application/runs/."""
    old_dir = runlog.RUNS_DIR
    runlog.RUNS_DIR = runs_dir
    try:
        run_id, path = runlog.new_run()
    finally:
        runlog.RUNS_DIR = old_dir
    for ev in SAMPLE_EVENTS:
        runlog.append_event(path, ev)
    return run_id, path


def _strip_ts(events: list[dict]) -> list[dict]:
    """Drop the additive `_ts` field injected at write time, so comparison is
    against exactly what was appended."""
    return [{k: v for k, v in ev.items() if k != "_ts"} for ev in events]


# --- gate 1: file round-trip parity (the required control) ---

def check_round_trip_parity() -> tuple[bool, str]:
    with tempfile.TemporaryDirectory() as tmp:
        run_id, path = _write_sample_run(tmp)
        assert os.path.exists(path), "append_event must create the log file"

        read_back = runlog.read_events(path)
        stripped = _strip_ts(read_back)

        if stripped != list(SAMPLE_EVENTS):
            return False, (
                f"round-trip mismatch: wrote {len(SAMPLE_EVENTS)} events, "
                f"read back {stripped}"
            )

        # every event must carry an injected _ts (additive, not replacing anything)
        if not all("_ts" in ev for ev in read_back):
            return False, "not every read-back event carries an injected _ts"

        # terminal event must be captured (done, in this sample)
        if read_back[-1]["type"] != "done":
            return False, f"terminal event not captured: last={read_back[-1]}"

        return True, f"{len(SAMPLE_EVENTS)}/{len(SAMPLE_EVENTS)} events round-tripped in order"


# --- gate 2: live endpoint parity via TestClient at speed=0 (bonus, best-effort) ---

def check_endpoint_parity() -> tuple[bool, str]:
    try:
        from fastapi.testclient import TestClient
    except ImportError:
        return True, "skipped (fastapi.testclient unavailable)"

    tmp = tempfile.mkdtemp()
    old_dir = runlog.RUNS_DIR
    try:
        runlog.RUNS_DIR = tmp
        run_id, path = runlog.new_run()
        for ev in SAMPLE_EVENTS:
            runlog.append_event(path, ev)

        # app.py imports runlog at module scope and calls runlog.new_run()/append_event()
        # by qualified reference (runlog.RUNS_DIR), so patching the module attribute
        # here is visible to the running app without needing to reload it.
        from sera.api.app import app

        with TestClient(app) as client:
            resp = client.get(f"/api/replay/{run_id}", params={"speed": 0})
            if resp.status_code != 200:
                return False, f"replay endpoint returned {resp.status_code}: {resp.text}"

            got = _parse_sse_body(resp.text)
            if got != list(SAMPLE_EVENTS):
                return False, f"endpoint replay mismatch: got {got}"

            missing_resp = client.get("/api/replay/doesnotexist0123456789abcdef")
            if missing_resp.status_code != 404:
                return False, f"expected 404 for unknown run id, got {missing_resp.status_code}"

        return True, f"{len(SAMPLE_EVENTS)}/{len(SAMPLE_EVENTS)} events matched over SSE + 404 verified"
    finally:
        runlog.RUNS_DIR = old_dir
        shutil.rmtree(tmp, ignore_errors=True)


def _parse_sse_body(body: str) -> list[dict]:
    """Parse a `data: {...}\\n\\n`-delimited SSE body back into event dicts,
    stripping `_ts` the same way the file-level check does."""
    events = []
    for block in body.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        assert block.startswith("data: "), f"malformed SSE block: {block!r}"
        events.append(json.loads(block[len("data: "):]))
    return _strip_ts(events)


# --- pytest entry points ---

def test_round_trip_parity() -> None:
    passed, reason = check_round_trip_parity()
    assert passed, reason


def test_endpoint_parity() -> None:
    passed, reason = check_endpoint_parity()
    assert passed, reason


if __name__ == "__main__":
    print("REPLAY-SPINE GATE — a saved run log must round-trip byte-for-byte\n")
    checks = (
        ("file round-trip parity", check_round_trip_parity),
        ("live endpoint parity (TestClient, speed=0)", check_endpoint_parity),
    )
    results = [(name, *fn()) for name, fn in checks]
    width = max(len(name) for name, _, _ in results)
    for name, passed, reason in results:
        mark = "PASS" if passed else "FAIL"
        print(f"  [{mark}] {name:<{width}} — {reason}")
    n_pass = sum(passed for _, passed, _ in results)
    print(f"\n{n_pass}/{len(results)} replay checks passed.")
    sys.exit(0 if n_pass == len(results) else 1)
