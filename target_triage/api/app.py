"""FastAPI backend: the surface a scientist's browser talks to.

Two clean paths:
  DETERMINISTIC (no API cost, always works, offline-reproducible):
    GET /api/screens           — the registered screens the picker offers
    GET /api/controls          — that screen's controls-first gate (shown BEFORE picks)
    GET /api/shortlist         — the ranked + verified + annotated table
    GET /api/target/{gene}     — one gene's full evidence + verdict
    GET /api/funnel            — the narrowing, sourced from the loader
    GET /api/volcano           — screen-wide points on the axes THAT screen can plot
  AGENT (live Claude, streamed; the interrogation layer):
    POST /api/chat             — SSE stream of the agent's tool calls + reasoning

Every deterministic route takes `?screen=` and defaults to Marson. Each registered
screen's shortlist is computed once at startup and cached (screens are static), so
the table loads instantly whichever screen the picker selects. An unknown screen is
a 404 — never a silent fall-back to Marson, which would show one screen's biology
under another's name.

The screen supplies its own controls, obvious-hit set, and impact axis; this module
assumes none of them. See docs/adr/0001-screens-declare-their-own-capabilities.md.

Run:  python -m target_triage.serve      (see serve.py)
"""
from __future__ import annotations

import asyncio
import json
import math
import os
import shutil
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

from collections import Counter

from ..core.controls import run_controls_gate
from ..core.data import load_screen
from ..core.ranking import significant_records
from ..core.schema import MARSON, REGISTRY, ScreenSchema
from ..core.shortlist import compute_shortlist
from . import runlog
from .replay import replay_stream

# app.py lives at target_triage/api/app.py; the served frontend is bundled inside
# the package at target_triage/frontend/ — two dirname() hops (api -> target_triage).
_WEB_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "frontend",
)

# One deterministic table per registered screen, computed once at startup. Both
# screens together cost well under a second against the warm Open Targets cache, so
# precomputing beats lazy caching: no invalidation logic, and every request is instant
# whichever screen the picker selects.
_SHORTLISTS: dict[str, list[dict]] = {}

# Concord's concordance table (the 2x2(+1) verdicts), loaded once from the precomputed
# parquet artifact. Concord is served alongside Target Triage from the same process; this
# cache stays empty (and its routes 503) if the artifact hasn't been built — the
# deterministic Target Triage paths never depend on it.
_CONCORDANCE: list[dict] = []
_ARTIFACTS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "artifacts")
_CONCORDANCE_PARQUET = os.path.join(_ARTIFACTS, "concordance.parquet")
_ENRICHMENT_JSON = os.path.join(_ARTIFACTS, "enrichment.json")

# Per-gene dossier enrichment (quality QC + druggability + disease), loaded once. Empty if the
# enrichment artifact hasn't been built — the dossier cards then show "not available", never a
# fabricated value.
_ENRICHMENT: dict[str, dict] = {}

# Precomputed grounded Claude explanations, keyed "GENE|CYTOKINE|CONDITION". Empty if not built;
# the frontend then uses its deterministic template — so the demo never depends on a live call.
_EXPLANATIONS: dict[str, dict] = {}
_EXPLANATIONS_JSON = os.path.join(_ARTIFACTS, "explanations_cache.json")


def _load_enrichment() -> dict[str, dict]:
    if not os.path.exists(_ENRICHMENT_JSON):
        return {}
    import json
    with open(_ENRICHMENT_JSON) as fh:
        return json.load(fh)


def _load_explanations() -> dict[str, dict]:
    if not os.path.exists(_EXPLANATIONS_JSON):
        return {}
    import json
    with open(_EXPLANATIONS_JSON) as fh:
        return json.load(fh)


# The ground-truth panel: canonical IL-2 regulators recovered by Concord + the aggregate Spearman.
_GROUND_TRUTH: dict = {}
_GROUND_TRUTH_JSON = os.path.join(_ARTIFACTS, "ground_truth.json")


def _load_ground_truth() -> dict:
    if not os.path.exists(_GROUND_TRUTH_JSON):
        return {}
    import json
    with open(_GROUND_TRUTH_JSON) as fh:
        return json.load(fh)


def _load_concordance() -> list[dict]:
    """Read the concordance artifact into plain JSON-able dicts, or [] if absent.

    Kept dependency-light: pandas is only imported here (the artifact is optional), so the
    core deterministic paths never require it. NaN/NA are normalised to None so the JSON is
    valid (a bare NaN is not legal JSON)."""
    if not os.path.exists(_CONCORDANCE_PARQUET):
        return []
    import math

    import pandas as pd

    df = pd.read_parquet(_CONCORDANCE_PARQUET)
    records = df.to_dict("records")
    clean: list[dict] = []
    for r in records:
        row = {}
        for k, v in r.items():
            if v is None or (isinstance(v, float) and math.isnan(v)):
                row[k] = None
            elif hasattr(v, "item"):        # numpy scalar -> python scalar
                row[k] = v.item()
            else:
                row[k] = v
        clean.append(row)
    return clean


@asynccontextmanager
async def _lifespan(app: FastAPI):
    for name, schema in REGISTRY.items():
        _SHORTLISTS[name] = compute_shortlist(schema)
    _CONCORDANCE.extend(_load_concordance())
    _ENRICHMENT.update(_load_enrichment())
    _EXPLANATIONS.update(_load_explanations())
    _GROUND_TRUTH.update(_load_ground_truth())
    yield


app = FastAPI(title="Target Triage", version="0.1.0", lifespan=_lifespan)


def _resolve(screen: str | None) -> ScreenSchema:
    """`?screen=` -> schema, or 404. The single gate: no route may silently fall back
    to Marson, which would present one screen's biology under another's name."""
    if screen is None:
        return MARSON
    if screen not in REGISTRY:
        raise HTTPException(
            status_code=404,
            detail=f"unknown screen '{screen}'; registered: {sorted(REGISTRY)}",
        )
    return REGISTRY[screen]


def _rows(schema: ScreenSchema) -> list[dict]:
    """The precomputed shortlist for a screen (computed on demand if the lifespan
    hasn't run — e.g. a direct import in a test)."""
    if schema.name not in _SHORTLISTS:
        _SHORTLISTS[schema.name] = compute_shortlist(schema)
    return _SHORTLISTS[schema.name]


@app.get("/api/screens")
def screens() -> dict:
    """What the picker offers. Each screen advertises the signals it carries, so the
    UI can label honestly rather than assume a Marson-shaped screen."""
    return {
        "default": MARSON.name,
        "screens": [
            {
                "name": s.name,
                "description": s.description,
                "impact_axis": s.impact_axis,
                "has_breadth": s.has_breadth,
                "has_conditions": s.has_conditions,
                "controls": list(s.controls),
                "spotlight": list(s.spotlight),
            }
            for s in REGISTRY.values()
        ],
    }


@app.get("/api/controls")
def controls(screen: str | None = None) -> dict:
    """The controls-first gate for a screen. The UI must render this BEFORE a single
    novel pick: if a screen cannot recover its own known biology, nothing below it is
    trustworthy, and the tool says so rather than handing back a plausible table."""
    schema = _resolve(screen)
    gate = run_controls_gate(schema)
    return {
        "screen": gate.screen,
        "passed": gate.passed,
        "summary": gate.summary,
        "results": [
            {"gene": r.gene, "found": r.found, "passed": r.passed,
             "best_effect": r.best_effect, "condition": r.condition, "reason": r.reason}
            for r in gate.results
        ],
    }


@app.get("/api/shortlist")
def shortlist(screen: str | None = None, limit: int = 50, condition: str | None = None,
              min_druggable: float = 0.0, promoted_only: bool = False) -> dict:
    """The ranked, verified shortlist. Filters are applied server-side so the
    table stays honest (rank reflects the full set; filters only narrow the view)."""
    schema = _resolve(screen)
    all_rows = _rows(schema)
    rows = all_rows
    if condition:
        rows = [r for r in rows if r["best_condition"] == condition]
    if min_druggable > 0:
        rows = [r for r in rows if r["druggable_score"] >= min_druggable]
    if promoted_only:
        rows = [r for r in rows if r["verdict"].startswith("PROMOTE")]
    return {
        "screen": schema.name,
        "total": len(all_rows),
        "shown": min(limit, len(rows)),
        "spotlight": list(schema.spotlight),
        "rows": rows[:limit],
    }


@app.get("/api/target/{gene}")
def target(gene: str, screen: str | None = None) -> dict:
    schema = _resolve(screen)
    row = next((r for r in _rows(schema) if r["gene"] == gene.upper()), None)
    if row is None:
        raise HTTPException(
            status_code=404, detail=f"{gene} not in the {schema.name} shortlist")
    return row


# ---- Concord: cross-modality concordance verdicts (served alongside Target Triage) -----
# The 2x2(+1) verdict per (gene, cytokine, condition): replicated / discordant / mrna_only /
# protein_only / neither. Read from the precomputed concordance.parquet — no live compute.
# A 503 (not a silent empty list) is returned if the artifact hasn't been built, so a caller
# knows the difference between "no hits" and "pipeline not run".

# The order verdicts should be presented in (most→least actionable). The frontend reads this.
_VERDICT_ORDER = ("replicated", "discordant", "protein_only", "mrna_only", "neither")


def _require_concordance() -> list[dict]:
    if not _CONCORDANCE:
        raise HTTPException(
            status_code=503,
            detail="concordance artifact not built — run "
                   "`python pipeline/02_build_concordance.py` first.")
    return _CONCORDANCE


@app.get("/api/concordance")
def concordance(condition: str | None = None, cytokine: str | None = None,
                verdict: str | None = None, limit: int = 500) -> dict:
    """The concordance table, optionally filtered by condition / cytokine / verdict.

    Rows are ordered by verdict actionability (replicated first) then by the stronger of the
    two significance values, so the most trustworthy concordant hits surface at the top."""
    rows = _require_concordance()
    if cytokine:
        rows = [r for r in rows if r["cytokine"] == cytokine.upper()]
    if condition:
        rows = [r for r in rows if r["condition"] == condition]
    if verdict:
        rows = [r for r in rows if r["verdict"] == verdict]

    order = {v: i for i, v in enumerate(_VERDICT_ORDER)}

    def _rank(r: dict) -> tuple:
        # best available q across the two sides; None sorts last
        qs = [q for q in (r.get("q_rna"), r.get("q_prot")) if q is not None]
        best_q = min(qs) if qs else 1.0
        return (order.get(r["verdict"], 99), best_q)

    ordered = sorted(rows, key=_rank)
    tally: dict[str, int] = {}
    for r in rows:
        tally[r["verdict"]] = tally.get(r["verdict"], 0) + 1
    return {
        "total": len(rows),
        "shown": min(limit, len(ordered)),
        "verdict_order": list(_VERDICT_ORDER),
        "counts": tally,
        "rows": ordered[:limit],
    }


@app.get("/api/concordance/{gene}")
def concordance_gene(gene: str, cytokine: str | None = None) -> dict:
    """One gene's verdict across all conditions (for the Single-gene view's condition tabs),
    plus its dossier enrichment (quality / druggability / disease). `enrichment` is null when
    the enrichment artifact isn't built — the frontend then shows 'not available', not a fake."""
    rows = _require_concordance()
    g = gene.upper()
    hits = [r for r in rows if r["gene"] == g]
    if cytokine:
        hits = [r for r in hits if r["cytokine"] == cytokine.upper()]
    if not hits:
        raise HTTPException(status_code=404, detail=f"{gene} not in the concordance table")
    # Attach the grounded explanation (if precomputed) to each condition's row. The frontend
    # prefers it and falls back to its deterministic template when absent.
    by_condition = {}
    for r in hits:
        row = dict(r)
        key = f"{g}|{r['cytokine']}|{r['condition']}"
        exp = _EXPLANATIONS.get(key)
        if exp:
            row["explanation"] = exp["explanation"]
        by_condition[r["condition"]] = row
    return {"gene": g, "cytokine": hits[0]["cytokine"], "by_condition": by_condition,
            "enrichment": _ENRICHMENT.get(g)}


@app.get("/api/enrichr")
def enrichr(genes: str = "", library: str = "Reactome_2022") -> dict:
    """Pathway enrichment over a comma-separated gene list (the Hit-list's replicated subset).
    Live Enrichr, cached by gene set — returns [] on failure so the view degrades to no chips,
    never an error. Imported lazily so the deterministic paths don't depend on the client."""
    gene_list = [g.strip().upper() for g in genes.split(",") if g.strip()]
    if not gene_list:
        return {"genes": [], "pathways": []}
    from ..clients import enrichr as enrichr_client
    paths = enrichr_client.enrich(gene_list, library=library)
    return {
        "genes": gene_list,
        "library": library,
        "pathways": [{"term": p.term, "adj_p": p.adj_p, "n_genes": p.n_genes} for p in paths],
    }


@app.get("/api/ground_truth")
def ground_truth() -> dict:
    """The ground-truth panel: how Concord recovers canonical IL-2 regulators + the aggregate
    Spearman. 503 if the artifact isn't built (never a fabricated claim)."""
    if not _GROUND_TRUTH:
        raise HTTPException(
            status_code=503,
            detail="ground-truth artifact not built — run `python pipeline/05_ground_truth.py`.")
    return _GROUND_TRUTH


@app.get("/api/funnel")
def funnel(screen: str | None = None) -> dict:
    """Deterministic funnel counts (no key). Every number is SOURCED from the loader
    or the verified shortlist — none is typed by hand. The frontend draws the narrowing
    from these, so 'perturbations -> shortlist' can never show a fabricated tally.

    Note the distinction the funnel must respect: a screen has many perturbation x
    condition ROWS but fewer unique GENES (Marson: 33,983 rows, 11,526 genes). The
    first funnel gene-bar is loaded_genes, never the row count."""
    schema = _resolve(screen)
    rows = _rows(schema)
    recs = load_screen(schema)
    sig = significant_records(recs)
    tally = Counter(r["verdict"] for r in rows)
    return {
        "screen": schema.name,
        "loaded_genes": len(recs),
        "significant": len(sig),
        "verdicts": {
            "REJECT": tally.get("REJECT", 0),
            "PROMOTE (weak)": tally.get("PROMOTE (weak)", 0),
            "PROMOTE": tally.get("PROMOTE", 0),
            "PROMOTE (corroborated)": tally.get("PROMOTE (corroborated)", 0),
        },
        "survived": sum(v for k, v in tally.items() if k.startswith("PROMOTE")),
        "obvious_tcr_in_ranked": sum(1 for r in rows if r["is_obvious_tcr"]),
    }


# How many background (unlabelled) cloud points to ship. The full screen is ~7k
# significant genes; a volcano only needs enough to show the cloud's shape, and a
# lighter payload keeps the inline SVG snappy. Shortlist genes are ALWAYS kept
# (never sampled out) so no labelled point is ever dropped.
VOLCANO_CLOUD_CAP = 900


# An FDR of exactly 0.0 (MAGeCK reports these) has no finite -log10. Clamp to the
# smallest float the table can distinguish, so the point plots at the top of the axis
# instead of vanishing to infinity or being dropped.
_MIN_FDR = 1e-10


def _impact(pert, schema: ScreenSchema) -> float | None:
    """The y-value this screen can honestly plot. None when the signal is absent —
    never 0.0, which would read as 'measured, and nil'. See ADR-0001."""
    if schema.impact_axis == "breadth":
        return float(pert.n_downstream) if pert.n_downstream is not None else None
    if schema.impact_axis == "neg_log10_fdr":
        if pert.fdr is None:
            return None
        return round(-math.log10(max(pert.fdr, _MIN_FDR)), 3)
    return None


def _volcano_point(record, schema: ScreenSchema) -> dict | None:
    """One volcano point for a gene: its strongest-effect significant, on-target
    condition. x = effect size; y = whatever impact axis THIS screen declares (Marson
    has no per-perturbation p-value, so it plots breadth; Schmidt2022 has no breadth,
    so it plots -log10(FDR)). Returns None if the gene has no usable measurement."""
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
        "impact": _impact(best, schema),         # float OR None; axis named in the payload
        "downstream": best.n_downstream,         # int OR None (screen may lack breadth)
        "condition": best.condition,
    }


# How the frontend should label each declared axis. The chart reads this rather than
# hardcoding "downstream genes moved" — a caption that would be a lie on any screen
# without breadth.
_AXIS_LABELS: dict[str, dict[str, str]] = {
    "breadth": {
        "y_label": "downstream genes moved",
        # log1p, not log: a knockdown that moved zero downstream genes reports 0, which
        # is a real measurement (not a missing signal) and has no log.
        "y_scale": "log1p",
        "note": "this screen carries no per-perturbation p-value, "
                "so breadth of transcriptional impact is the honest y-axis",
    },
    "neg_log10_fdr": {
        "y_label": "−log10(FDR)",
        "y_scale": "linear",
        "note": "this screen reports no downstream breadth, "
                "so significance is the honest y-axis (the classic volcano)",
    },
}


@app.get("/api/volcano")
def volcano(screen: str | None = None, cloud_cap: int = VOLCANO_CLOUD_CAP) -> dict:
    """Screen-wide volcano data. Every significant gene is one point; shortlist genes
    carry their verdict so the frontend can label + colour them, and the anonymous
    background is evenly downsampled to keep the payload light. No number is typed by
    hand — every value comes straight from the loaded screen.

    Axes the frontend should draw:
      x = effect size (|effect| grows with knockdown strength)
      y = `impact`, whose meaning is named by `impact_axis` / `y_label`. The screen
          declares it; this route never assumes one. See ADR-0001.
    """
    schema = _resolve(screen)
    verdict_by_gene = {r["gene"]: r["verdict"] for r in _rows(schema)}

    labelled: list[dict] = []
    background: list[dict] = []
    for rec in significant_records(load_screen(schema)):
        pt = _volcano_point(rec, schema)
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
        "screen": schema.name,
        "impact_axis": schema.impact_axis,
        **_AXIS_LABELS[schema.impact_axis],
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
