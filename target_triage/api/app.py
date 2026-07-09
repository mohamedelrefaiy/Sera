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
import shutil
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

from collections import Counter

from ..core.data import load_perturbations
from ..core.ranking import significant_records
from ..core.shortlist import SPOTLIGHT, compute_shortlist
from . import runlog
from .replay import replay_stream

# app.py lives at target_triage/api/app.py; the served frontend is bundled inside
# the package at target_triage/frontend/ — two dirname() hops (api -> target_triage).
_WEB_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "frontend",
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


@app.get("/api/funnel")
def funnel() -> dict:
    """Deterministic funnel counts (no key). Every number is SOURCED from the loader
    or the verified shortlist — none is typed by hand. The frontend draws the narrowing
    from these, so 'perturbations -> shortlist' can never show a fabricated tally.

    Note the distinction the funnel must respect: the screen has 33,983 perturbation x
    condition ROWS, but 11,526 unique GENES — the first funnel gene-bar is loaded_genes,
    never the row count."""
    recs = load_perturbations()
    sig = significant_records(recs)
    tally = Counter(r["verdict"] for r in _SHORTLIST)
    return {
        "loaded_genes": len(recs),
        "significant": len(sig),
        "verdicts": {
            "REJECT": tally.get("REJECT", 0),
            "PROMOTE (weak)": tally.get("PROMOTE (weak)", 0),
            "PROMOTE": tally.get("PROMOTE", 0),
            "PROMOTE (corroborated)": tally.get("PROMOTE (corroborated)", 0),
        },
        "survived": sum(v for k, v in tally.items() if k.startswith("PROMOTE")),
        "obvious_tcr_in_ranked": sum(1 for r in _SHORTLIST if r["is_obvious_tcr"]),
    }


# How many background (unlabelled) cloud points to ship. The full screen is ~7k
# significant genes; a volcano only needs enough to show the cloud's shape, and a
# lighter payload keeps the inline SVG snappy. Shortlist genes are ALWAYS kept
# (never sampled out) so no labelled point is ever dropped.
VOLCANO_CLOUD_CAP = 900


def _volcano_point(record) -> dict | None:
    """One volcano point for a gene: its strongest-effect significant, on-target
    condition. x = on-target effect size, y = downstream breadth (this screen's
    honest impact axis — it carries no per-perturbation p-value). Returns None if
    the gene has no usable measurement."""
    best = None
    for pert in record.by_condition.values():
        if pert.significant and not pert.offtarget:
            if best is None or abs(pert.effect_size) > abs(best.effect_size):
                best = pert
    if best is None:
        return None
    return {
        "gene": record.gene,
        "effect": round(best.effect_size, 2),
        "downstream": best.n_downstream,          # int OR None (screen may lack breadth)
        "condition": best.condition,
    }


@app.get("/api/volcano")
def volcano(cloud_cap: int = VOLCANO_CLOUD_CAP) -> dict:
    """Screen-wide volcano data. Every significant gene is one point; shortlist genes
    carry their verdict so the frontend can label + colour them, and the anonymous
    background is evenly downsampled to keep the payload light. No number is typed by
    hand — all effect/breadth values come straight from the loaded screen.

    Axes the frontend should draw:
      x = on-target effect size (|effect| grows with knockdown strength)
      y = downstream genes moved (breadth of transcriptional impact)
    """
    verdict_by_gene = {r["gene"]: r["verdict"] for r in _SHORTLIST}

    labelled: list[dict] = []
    background: list[dict] = []
    for rec in significant_records(load_perturbations()):
        pt = _volcano_point(rec)
        if pt is None:
            continue
        verdict = verdict_by_gene.get(pt["gene"])
        if verdict is not None:
            labelled.append({**pt, "verdict": verdict})
        else:
            background.append(pt)

    total_background = len(background)
    cap = max(0, cloud_cap)
    if cap and total_background > cap:
        step = total_background / cap  # even stride keeps the cloud's shape unbiased
        background = [background[int(i * step)] for i in range(cap)]

    return {
        "total_significant": total_background + len(labelled),
        "background": background,
        "background_sampled_from": total_background,
        "labelled": labelled,
    }


CHAT_TIMEOUT_S = 90  # a full triage run streams within this; else we fail cleanly


@app.post("/api/chat")
async def chat(body: dict) -> StreamingResponse:
    """Stream the live agent as Server-Sent Events, incrementally. Each event is one
    JSON line: {type: tool_call|text|view_update|tool_result|error|done, ...}.
    Imported lazily so the deterministic paths never depend on an API key being
    present.

    If no credentials are configured we fail fast with a clear event rather than
    hanging — the frontend then falls back to the deterministic shortlist.

    Every event is ALSO teed to a run log (see runlog.py) so the run can be
    replayed later via GET /api/replay/{run_id} — one code path renders both
    live and replayed events. The run id is returned as the `X-Run-Id` response
    header (chosen over embedding it in the first event: a header is available
    to the client the instant headers arrive, before any SSE event is parsed,
    and it keeps the event payloads themselves byte-identical to what a replay
    will later re-emit)."""
    task = (body or {}).get("message", "").strip()
    if not task:
        raise HTTPException(status_code=400, detail="empty message")

    run_id, run_log_path = runlog.new_run()
    headers = {"X-Run-Id": run_id}

    if not _has_credentials():
        async def no_key():
            for ev in ({"type": "error",
                        "detail": "No Anthropic credentials configured. The shortlist "
                                  "works without the agent; set ANTHROPIC_API_KEY to enable chat."},
                       {"type": "done"}):
                runlog.append_event(run_log_path, ev)
                yield _sse(ev)
        return StreamingResponse(no_key(), media_type="text/event-stream", headers=headers)

    from ..agent import run_triage  # lazy: only the chat path needs the SDK

    queue: asyncio.Queue = asyncio.Queue()

    # Persist the tool_use_id -> short-name map ACROSS messages: a ToolResultBlock
    # carries only the id of the ToolUseBlock that produced it (no name), so labelling
    # a tool_result event requires remembering the name we saw on the earlier call.
    tool_names: dict[str, str] = {}

    def on_message(msg):
        for ev in _message_to_events(msg, tool_names):
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
                    ev = {"type": "error", "detail": "agent timed out"}
                    runlog.append_event(run_log_path, ev)
                    yield _sse(ev)
                    done_ev = {"type": "done"}
                    runlog.append_event(run_log_path, done_ev)
                    yield _sse(done_ev)
                    break
                runlog.append_event(run_log_path, ev)
                yield _sse(ev)
                if ev.get("type") == "done":
                    break
        finally:
            worker.cancel()

    return StreamingResponse(event_stream(), media_type="text/event-stream", headers=headers)


@app.get("/api/replay/{run_id}")
async def replay(run_id: str, speed: float = 1.0) -> StreamingResponse:
    """Re-stream a saved run's event log as SSE at recorded pacing. See replay.py
    for the pacing/404 contract; kept as a thin route so the replay logic itself
    stays independently testable and importable from eval/test_replay.py."""
    return await replay_stream(run_id, speed=speed)


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event)}\n\n"


def _has_credentials() -> bool:
    """True if the SDK is likely to authenticate. Either an env key, or a `claude`
    CLI on PATH — the CLI manages its own auth (env key, ~/.claude creds file, or the
    macOS Keychain), so its mere presence is the reliable signal. If it turns out to
    be logged out, the SDK surfaces that as a normal error event in the stream."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        return True
    if os.path.exists(os.path.expanduser("~/.claude/.credentials.json")):
        return True
    return shutil.which("claude") is not None


def _message_to_events(message, tool_names: dict[str, str] | None = None) -> list[dict]:
    """Translate SDK messages into small JSON events the frontend renders.

    Two directions matter:
      - AssistantMessage: the agent's tool CALLS (shown live) and prose. We also
        remember each call's id -> short name in `tool_names` so a later result
        can be labelled (a ToolResultBlock carries only the id, never the name).
      - UserMessage w/ ToolResultBlock: tool RESULTS. A stateful tool's result
        embeds __view_update__ -> forward as a view_update event (re-renders the
        table). An ANALYSIS tool's result (rank_candidates / verify_candidate) is
        plain JSON -> forward as a tool_result event so figures can read the real
        numbers. The two are mutually exclusive (else-branch), so no double-emit."""
    from claude_agent_sdk import (
        AssistantMessage, TextBlock, ToolResultBlock, ToolUseBlock, UserMessage,
    )

    events: list[dict] = []
    if isinstance(message, AssistantMessage):
        for block in message.content:
            if isinstance(block, ToolUseBlock):
                short = block.name.split("__")[-1]
                if tool_names is not None:
                    tool_names[block.id] = short
                events.append({"type": "tool_call", "tool": short,
                               "input": block.input or {}})
            elif isinstance(block, TextBlock):
                events.append({"type": "text", "text": block.text})
    elif isinstance(message, UserMessage):
        for block in getattr(message, "content", []) or []:
            if isinstance(block, ToolResultBlock):
                vu = _extract_view_update(block.content)
                if vu is not None:
                    events.append({"type": "view_update", "update": vu})
                else:
                    out = _parse_tool_result(block.content)
                    if out is not None:
                        name = (tool_names or {}).get(block.tool_use_id, "")
                        events.append({"type": "tool_result", "tool": name, "output": out})
    return events


def _parse_tool_result(content) -> dict | None:
    """Parse a plain (_text) analysis-tool result's JSON payload. Returns the dict
    only if it is JSON AND is not a view-update wrapper (those go the other branch);
    returns None for a non-JSON SDK error string, so a malformed result is dropped
    rather than streamed as garbage."""
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
        obj = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None
    return obj if isinstance(obj, dict) and "__view_update__" not in obj else None


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
