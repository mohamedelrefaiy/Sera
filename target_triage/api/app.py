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
from fastapi.responses import RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from collections import Counter

from ..core.controls import run_controls_gate
from ..core.data import load_screen
from ..core.ranking import significant_records
from ..core.schema import MARSON, REGISTRY, ScreenSchema
from ..core.shortlist import compute_shortlist
from . import runlog
from . import sessions as sessions_store
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


# The peripheral-agent layer's receipt: how each screen was read (Node A) and every cited
# hypothesis (Node B). Serves the Agent Activity rail and the Sources section. Optional artifact —
# absent on a clone that never ran pipeline/06_ingest.py, in which case the rail shows an honest
# empty state rather than fabricated activity.
_PROVENANCE: dict = {}
_PROVENANCE_JSON = os.path.join(_ARTIFACTS, "provenance.json")


def _load_provenance() -> dict:
    if not os.path.exists(_PROVENANCE_JSON):
        return {}
    import json
    with open(_PROVENANCE_JSON) as fh:
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
    _PROVENANCE.update(_load_provenance())
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


# The condition the decision brief anchors on, most→least preferred. Stim48hr is the demo anchor;
# fall back through the earlier conditions, then to whatever the gene actually has.
_BRIEF_ANCHOR_ORDER = ("Stim48hr", "Stim8hr", "Rest")


def _parse_constraints(readouts: str | None, donors: int | None, days: int | None):
    """Turn the three optional query params into ExperimentConstraints, or None if none were given.

    `readouts` is a comma-separated closed-enum list (qpcr,elisa,facs,western). An unknown token or a
    negative count raises ValueError inside ExperimentConstraints — the caller turns that into an
    explicit 400 rather than silently building an unconstrained brief that would look feasible."""
    if readouts is None and donors is None and days is None:
        return None
    from ..core.decision_brief import ExperimentConstraints
    ros = tuple(t.strip().lower() for t in (readouts or "").split(",") if t.strip())
    return ExperimentConstraints(readouts=ros, donors=donors or 0, days=days or 0)


def _decision_brief_for(hits: list[dict], constraints=None) -> dict | None:
    """Build the decision brief for a gene's anchor condition, serialised to a plain dict.

    Degrades to None (never raises) so a missing provenance artifact or an unexpected verdict shows
    the frontend's fallback rather than 500-ing the whole gene view. The verdict inside the brief is
    the code-computed value from the row — this endpoint never recomputes it. `constraints`, when
    present, drives feasibility — an infeasible ask is reported as infeasible, never downgraded."""
    import dataclasses

    from ..core.brief_resolver import resolve_claims, resolve_context, resolve_snapshot
    from ..core.decision_brief import build_decision_brief

    by_cond = {r["condition"]: r for r in hits}
    anchor = next((c for c in _BRIEF_ANCHOR_ORDER if c in by_cond), None)
    if anchor is None:
        anchor = hits[0]["condition"]
    row = by_cond[anchor]
    try:
        snapshot = resolve_snapshot(row, _PROVENANCE)
        context = resolve_context(row, _PROVENANCE)
        claims = resolve_claims(row["gene"], row["cytokine"], anchor, _PROVENANCE)
        brief = build_decision_brief(snapshot, context, claims, constraints)
        return dataclasses.asdict(brief)
    except (ValueError, KeyError, AssertionError):
        return None


@app.get("/api/concordance/{gene}")
def concordance_gene(gene: str, cytokine: str | None = None,
                     readouts: str | None = None, donors: int | None = None,
                     days: int | None = None) -> dict:
    """One gene's verdict across all conditions (for the Single-gene view's condition tabs),
    plus its dossier enrichment (quality / druggability / disease) and the anchor-condition
    decision brief. `enrichment` and `decision_brief` are null when their inputs aren't available —
    the frontend then shows 'not available' / falls back to its template, never a fake.

    Optional `readouts` (comma-separated: qpcr,elisa,facs,western), `donors`, and `days` constrain
    the discriminating experiment; an infeasible set is reported as infeasible in the brief, never
    silently downgraded. A malformed constraint (unknown readout, negative count) is a 400."""
    rows = _require_concordance()
    g = gene.upper()
    hits = [r for r in rows if r["gene"] == g]
    if cytokine:
        hits = [r for r in hits if r["cytokine"] == cytokine.upper()]
    if not hits:
        raise HTTPException(status_code=404, detail=f"{gene} not in the concordance table")
    try:
        constraints = _parse_constraints(readouts, donors, days)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
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
            "enrichment": _ENRICHMENT.get(g),
            "decision_brief": _decision_brief_for(hits, constraints)}


# How long a single grounded explanation may stream before we give up and fall back to cache.
EXPLANATION_TIMEOUT_S = 45


# The closed set of things the chat can DO. The router LLM must return one of these verbatim;
# anything else (or no key) falls back to a deterministic keyword classifier. Each maps to a
# scoped reply the frontend renders — the LLM chooses the route, it never authors the answer.
_INTENTS = ("reconcile", "druggability", "genetics", "quality",
            "plan", "known_biology", "answer", "help")

# Keyword → intent, for the deterministic fallback (and as the LLM's guardrail). First match wins;
# order matters (more specific phrases first).
_INTENT_KEYWORDS: tuple[tuple[tuple[str, ...], str], ...] = (
    # Conversational / definitional questions — answered in prose, not a rendered view. Checked
    # FIRST so "what does discordant mean" doesn't get swallowed by the 'discordant' reconcile keyword.
    (("what does", "what is", "what's", "explain", "how does", "why do", "why does", "what do you",
      "difference between", "mean", "meaning", "how do you", "what can you", "who are you", "how are you"),
     "answer"),
    (("known biology", "ground truth", "recover", "canonical", "positive regulator"), "known_biology"),
    (("validation plan", "validate", "experiment", "protocol", "bench", "assay", "how to test",
      "how do i test", "wet lab", "wet-lab"), "plan"),
    (("druggable", "druggability", "drug", "tractab", "small molecule", "antibody",
      "clinical", "open targets"), "druggability"),
    (("genetics", "gwas", "disease", "autoimmune", "immune-linked", "association"), "genetics"),
    (("quality", "qc", "confidence", "donor", "guide", "off-target", "off target",
      "knockdown", "reliable", "trust"), "quality"),
    (("reconcile", "concordance", "concordant", "discordant", "verdict", "mrna", "protein",
      "screens", "compare"), "reconcile"),
)


def _gene_set() -> set[str]:
    """Every gene symbol present in the concordance table — the ONLY genes the router may return.
    Built fresh from the cache each call (cheap; the table is small) so it can never go stale."""
    return {r["gene"] for r in _CONCORDANCE}


def _extract_gene(text: str, genes: set[str]) -> str | None:
    """Pull the first token that IS a real concordance gene out of free text. Deterministic and
    safe: it can only ever return a symbol that exists in the table, never invent one."""
    import re
    # Gene symbols are alnum runs (often with a trailing digit); check longest tokens first so
    # 'PTPN2' wins over a stray 'PT'. Uppercase to match the table's canonical casing.
    tokens = sorted(set(re.findall(r"[A-Za-z][A-Za-z0-9]{1,}", text)), key=len, reverse=True)
    for tok in tokens:
        if tok.upper() in genes:
            return tok.upper()
    return None


def _classify_keywords(text: str) -> str:
    """Deterministic intent from keywords. Defaults to 'reconcile' when a gene is present but no
    facet keyword matched (asking about a gene with no qualifier means 'reconcile it')."""
    low = text.lower()
    for phrases, intent in _INTENT_KEYWORDS:
        if any(p in low for p in phrases):
            return intent
    return "reconcile"


async def _route_with_llm(text: str, genes: set[str]) -> tuple[str | None, str] | None:
    """Ask Claude to classify (gene, intent) — returns None on any failure so the caller falls
    back to the deterministic path. The model is constrained to the CLOSED intent set and is told
    to return only a symbol; we still VALIDATE the gene against `genes` afterwards, so a
    hallucinated symbol is dropped rather than trusted."""
    try:
        from claude_agent_sdk import (
            AssistantMessage, ClaudeAgentOptions, ClaudeSDKClient, TextBlock)
    except Exception:  # noqa: BLE001 — SDK missing in a keyless clone
        return None

    system = (
        "You are a router for a gene-evidence tool. Given a user's message, return ONLY a compact "
        "JSON object: {\"gene\": <SYMBOL or null>, \"intent\": <one of "
        + "|".join(_INTENTS) + ">}. "
        "gene = the HGNC gene symbol the user is asking about, uppercased, or null if none. "
        "intent meanings: reconcile=compare the mRNA vs protein screens / the verdict for a gene; "
        "druggability=is it a drug target / tractability; genetics=disease/GWAS association; "
        "quality=is the hit reliable (QC, donor/guide agreement, knockdown); "
        "plan=how to validate it at the bench; known_biology=does the tool recover known biology "
        "(corpus-wide, gene-independent); "
        "answer=a conversational or definitional question that wants a WRITTEN explanation rather "
        "than a data view (e.g. 'what does discordant mean?', 'why do mRNA and protein disagree?', "
        "'how does this tool work?', 'what can you do?'); "
        "help=an empty/greeting message with no real question. "
        "Prefer a specific data intent (reconcile/druggability/genetics/quality/plan) when the user "
        "names a gene AND asks about its evidence; use 'answer' for general/definitional questions. "
        "Return the JSON and nothing else. Do NOT invent a gene symbol."
    )
    options = ClaudeAgentOptions(system_prompt=system, model="claude-haiku-4-5-20251001",
                                 max_turns=1, allowed_tools=[])
    acc = ""
    try:
        async def run():
            nonlocal acc
            async with ClaudeSDKClient(options=options) as client:
                await client.query(text[:500])
                async for message in client.receive_response():
                    if isinstance(message, AssistantMessage):
                        for block in message.content:
                            if isinstance(block, TextBlock) and block.text:
                                acc += block.text
        await asyncio.wait_for(run(), timeout=ROUTE_TIMEOUT_S)
    except Exception:  # noqa: BLE001 — any failure → deterministic fallback
        return None

    # Parse the model's JSON leniently (it may wrap it in prose or fences).
    import re
    m = re.search(r"\{.*\}", acc, re.DOTALL)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
    except Exception:  # noqa: BLE001
        return None
    intent = obj.get("intent")
    if intent not in _INTENTS:
        intent = "reconcile"
    raw_gene = obj.get("gene")
    # VALIDATE: only accept a gene the table actually contains. This is the gate — the LLM's gene
    # is a suggestion, not an authority.
    gene = raw_gene.upper() if isinstance(raw_gene, str) else None
    if gene not in genes:
        gene = None
    return (gene, intent)


ROUTE_TIMEOUT_S = 12  # a one-shot classification is fast; fail over to keywords well before this


@app.get("/api/route")
async def route(q: str = "") -> dict:
    """Classify a free-text chat message into {gene, intent} so the frontend can render a SCOPED
    reply instead of dumping the full reconciliation for every message.

    Contract (the frontend depends on it):
      - `intent` is always one of _INTENTS.
      - `gene` is either a symbol that EXISTS in the concordance table, or null. Never invented.
      - `known` mirrors whether `gene` resolved to a real table entry.
      - `source` is 'llm' or 'keywords' (which classifier decided), for transparency.

    The LLM only ROUTES; the deterministic views it routes to are what actually answer. If there's
    no API key (or the call fails/times out), a keyword classifier does the same job offline, so
    the composer always works."""
    text = (q or "").strip()
    if not text:
        return {"gene": None, "intent": "help", "known": False, "source": "empty",
                "note": "Type a gene symbol or a question about one."}

    genes = _gene_set()
    llm = await _route_with_llm(text, genes) if _has_credentials() else None
    if llm is not None:
        gene, intent = llm
        # The LLM may miss the symbol even when it's clearly in the text — backstop with extraction.
        if gene is None:
            gene = _extract_gene(text, genes)
        return {"gene": gene, "intent": intent, "known": gene is not None, "source": "llm"}

    # Deterministic fallback.
    gene = _extract_gene(text, genes)
    intent = _classify_keywords(text)
    # 'known_biology' and 'answer' are gene-independent (corpus-wide / conversational), so they're
    # valid with no gene. The gene-specific views need a gene; without one, degrade to 'answer' so
    # the user still gets a written reply instead of a dead-end 'help'.
    if gene is None and intent not in ("known_biology", "answer", "help"):
        intent = "answer"
    return {"gene": gene, "intent": intent, "known": gene is not None, "source": "keywords"}


# ── Chat sessions ────────────────────────────────────────────────────────────────────────────
# Persist a chat as an ordered list of turn descriptors (see sessions.py) so the left-nav can show
# recent conversations and re-open them. The descriptors are replayed by the browser through its
# own renderer; this API only stores/serves them.

@app.post("/api/sessions")
def create_session(body: dict | None = None) -> dict:
    """Create a new (empty) chat session. Optional {title}. Returns the session summary."""
    title = (body or {}).get("title", "") if body else ""
    s = sessions_store.create(title)
    return {"id": s["id"], "title": s["title"], "created": s["created"],
            "updated": s["updated"], "turn_count": 0}


@app.get("/api/sessions")
def list_sessions(limit: int = 50) -> dict:
    """Recent chat sessions, newest-updated first (summaries only, no turns)."""
    return {"sessions": sessions_store.list_sessions(limit=limit)}


@app.get("/api/sessions/{session_id}")
def get_session(session_id: str) -> dict:
    """One session's full turn list, for replay."""
    s = sessions_store.get(session_id)
    if s is None:
        raise HTTPException(status_code=404, detail="session not found")
    return s


@app.patch("/api/sessions/{session_id}")
def patch_session(session_id: str, body: dict) -> dict:
    """Append a turn descriptor ({turn: {...}}) or set the title ({title: ...})."""
    body = body or {}
    if "turn" in body and isinstance(body["turn"], dict):
        summary = sessions_store.append_turn(session_id, body["turn"])
        if summary is None:
            raise HTTPException(status_code=404, detail="session not found")
        return summary
    if "title" in body:
        if not sessions_store.set_title(session_id, str(body["title"])):
            raise HTTPException(status_code=404, detail="session not found")
        return {"id": session_id, "title": str(body["title"])[:60]}
    raise HTTPException(status_code=400, detail="body must include 'turn' or 'title'")


@app.delete("/api/sessions/{session_id}")
def delete_session(session_id: str) -> dict:
    """Remove a session. Idempotent-ish: a missing session returns 404."""
    if not sessions_store.delete(session_id):
        raise HTTPException(status_code=404, detail="session not found")
    return {"deleted": session_id}


# What the agent may say when ANSWERING a conversational question. Grounded in the tool's real
# definitions so it explains the method honestly and refuses to invent per-gene numbers (for a
# specific gene's numbers the router sends the user to a data view, not here).
_ANSWER_SYSTEM = (
    "You are the assistant inside Concord, a gene-evidence reconciliation tool for immunology "
    "target discovery. Answer the user's question in 2-4 short sentences of plain prose — no "
    "markdown headings, no bullet lists. Be precise and grounded.\n\n"
    "What Concord does: for a candidate gene it compares TWO independent CRISPR screens measuring "
    "the same IL-2 phenotype — an mRNA screen (Zhu 2025 Perturb-seq, transcriptome z-scores) and a "
    "protein screen (Schmidt 2022 FACS, log-fold-change) — and assigns a DETERMINISTIC verdict, "
    "computed by code, never by an LLM. The five verdicts: 'replicated' = both screens are "
    "significant and agree on direction (strongest hit); 'discordant' = both significant but they "
    "DISAGREE on direction (a real mRNA/protein decoupling); 'mrna_only' = only the mRNA screen "
    "fires; 'protein_only' = only the protein screen fires (the gene was detected only by the "
    "protein screen under these conditions — post-transcriptional regulation is one hypothesis for "
    "that gap, not the verdict, and a transcriptome-only search would miss it either way); "
    "'neither' = concordant absence. It also surfaces "
    "Open Targets druggability + disease genetics and can draft a bench validation plan.\n\n"
    "Rules: the verdict and all numbers are deterministic — the agent narrates and cites, it does "
    "not decide. If asked for a SPECIFIC gene's numbers or verdict, briefly say to ask to reconcile "
    "that gene (e.g. 'reconcile TSC1') rather than guessing. NEVER invent gene symbols, effect "
    "sizes, p-values, or citations. If you don't know, say so."
)

ANSWER_TIMEOUT_S = 45


@app.get("/api/answer")
async def answer(q: str = "") -> StreamingResponse:
    """Stream a GROUNDED conversational answer to a definitional/'how does this work' question as
    SSE — the 'answer the question' half of the agentic split (vs. running a data view). Same event
    shape as /api/explanation: {type:'token',text} … {type:'done'}, or {type:'error',detail,fallback}.

    This path deliberately carries NO per-gene numbers — it explains the method/biology. A question
    about a specific gene's data is routed to a deterministic view instead, so this endpoint can
    never fabricate a verdict or an effect size."""
    text = (q or "").strip()
    if not text:
        async def empty():
            yield _sse({"type": "error", "detail": "empty question", "fallback": None})
            yield _sse({"type": "done"})
        return StreamingResponse(empty(), media_type="text/event-stream")

    # A deterministic fallback sentence when the live call can't run — still useful, never blank.
    fallback = ("I compare a gene's mRNA and protein CRISPR screens and give a deterministic "
                "verdict — replicated, discordant, mRNA-only, protein-only, or neither — plus "
                "druggability, disease genetics, and a validation plan. Ask me to reconcile a gene "
                "like TSC1 to see it.")

    def fail(detail: str):
        async def one():
            yield _sse({"type": "error", "detail": detail, "fallback": fallback})
            yield _sse({"type": "done"})
        return StreamingResponse(one(), media_type="text/event-stream")

    if not _has_credentials():
        return fail("No Anthropic credentials configured.")

    async def event_stream():
        try:
            from claude_agent_sdk import (
                AssistantMessage, ClaudeAgentOptions, ClaudeSDKClient, TextBlock)
        except Exception as e:  # noqa: BLE001
            yield _sse({"type": "error", "detail": f"SDK unavailable: {e}", "fallback": fallback})
            yield _sse({"type": "done"})
            return

        options = ClaudeAgentOptions(system_prompt=_ANSWER_SYSTEM,
                                     model="claude-haiku-4-5-20251001",
                                     max_turns=1, allowed_tools=[])
        emitted = False
        try:
            async def run():
                nonlocal emitted
                async with ClaudeSDKClient(options=options) as client:
                    await client.query(text[:500])
                    async for message in client.receive_response():
                        if isinstance(message, AssistantMessage):
                            for block in message.content:
                                if isinstance(block, TextBlock) and block.text:
                                    emitted = True
                                    yield {"type": "token", "text": block.text}

            agen = run()
            while True:
                try:
                    ev = await asyncio.wait_for(agen.__anext__(), timeout=ANSWER_TIMEOUT_S)
                except StopAsyncIteration:
                    break
                except asyncio.TimeoutError:
                    yield _sse({"type": "error", "detail": "answer timed out", "fallback": fallback})
                    break
                yield _sse(ev)
            if not emitted:
                yield _sse({"type": "error", "detail": "empty response", "fallback": fallback})
        except Exception as e:  # noqa: BLE001
            yield _sse({"type": "error", "detail": str(e), "fallback": fallback})
        finally:
            yield _sse({"type": "done"})

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.get("/api/explanation/{gene}/{cytokine}/{condition}")
async def explanation(gene: str, cytokine: str, condition: str) -> StreamingResponse:
    """Stream a GROUNDED Claude explanation for one (gene, cytokine, condition) live, as SSE.

    Events are one JSON line each: {type: "token", text} while generating, then {type: "done"};
    or {type: "error", detail, fallback} if the live call can't run. Per the demo contract the
    call is LIVE every time (nothing new is written to the cache), and if the live call is
    impossible or fails, the payload carries `fallback` = the EXISTING cached paragraph (or null
    if none was precomputed) so the frontend can show real prior text rather than a blank panel.

    The prompt + record shape come from core.explanation — the SAME source of truth the offline
    precompute uses, so the grounding contract (numbers-only) holds identically on this path."""
    g, cyt, cond = gene.upper(), cytokine.upper(), condition
    rows = _require_concordance()
    match = next((r for r in rows if r["gene"] == g and r["cytokine"] == cyt
                  and r["condition"] == cond), None)
    if match is None:
        raise HTTPException(status_code=404,
                            detail=f"{g}/{cyt}/{cond} not in the concordance table")

    cached = _EXPLANATIONS.get(f"{g}|{cyt}|{cond}")
    cached_text = cached.get("explanation") if cached else None

    from ..core.explanation import MODEL, SYSTEM_PROMPT, build_record, user_prompt
    record = build_record(match, g)

    def fail(detail: str):
        async def one():
            yield _sse({"type": "error", "detail": detail, "fallback": cached_text})
            yield _sse({"type": "done"})
        return StreamingResponse(one(), media_type="text/event-stream")

    if not _has_credentials():
        return fail("No Anthropic credentials configured — serving the cached explanation.")

    async def event_stream():
        try:
            from claude_agent_sdk import (
                AssistantMessage, ClaudeAgentOptions, ClaudeSDKClient, TextBlock)
        except Exception as e:  # noqa: BLE001 — SDK missing in a keyless clone
            yield _sse({"type": "error", "detail": f"SDK unavailable: {e}",
                        "fallback": cached_text})
            yield _sse({"type": "done"})
            return

        options = ClaudeAgentOptions(system_prompt=SYSTEM_PROMPT, model=MODEL,
                                     max_turns=1, allowed_tools=[])
        emitted = False
        try:
            async def run():
                nonlocal emitted
                async with ClaudeSDKClient(options=options) as client:
                    await client.query(user_prompt(record))
                    async for message in client.receive_response():
                        if isinstance(message, AssistantMessage):
                            for block in message.content:
                                if isinstance(block, TextBlock) and block.text:
                                    emitted = True
                                    yield {"type": "token", "text": block.text}

            agen = run()
            while True:
                try:
                    ev = await asyncio.wait_for(agen.__anext__(), timeout=EXPLANATION_TIMEOUT_S)
                except StopAsyncIteration:
                    break
                except asyncio.TimeoutError:
                    yield _sse({"type": "error", "detail": "explanation timed out",
                                "fallback": cached_text})
                    break
                yield _sse(ev)
            # If the model produced nothing at all, hand back the cache rather than a blank panel.
            if not emitted:
                yield _sse({"type": "error", "detail": "empty response",
                            "fallback": cached_text})
        except Exception as e:  # noqa: BLE001 — any live failure degrades to the cached text
            yield _sse({"type": "error", "detail": str(e), "fallback": cached_text})
        finally:
            yield _sse({"type": "done"})

    return StreamingResponse(event_stream(), media_type="text/event-stream")


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


def _agent_activity() -> list[dict]:
    """Derive the Agent Activity timeline from the provenance receipt — one event per real
    decision the peripheral agents made, never a scripted animation.

    Node A (ingestion) contributes, per screen: profiled -> mapping validated -> (if the source
    was sign-inverted) sign corrected. Node B (interpretation) contributes one event per cited
    hypothesis. Every event is backed by a field in provenance.json, so the rail is a view of the
    receipt, not a decoration. Empty list when the artifact isn't built.
    """
    if not _PROVENANCE:
        return []
    events: list[dict] = []
    for s in _PROVENANCE.get("screens", []):
        sid = s.get("screen_id", "screen")
        m = s.get("mapping", {})
        sig = m.get("significance", {})
        n_rows = s.get("validation", {}).get("n_rows")
        events.append({
            "node": "A", "kind": "ingest", "screen": sid,
            "title": f"{sid} · mapped",
            "detail": (f"gene={m.get('gene')} · effect={m.get('effect_size')} · "
                       f"{'+'.join(sig.get('columns', [])) or '—'} ({sig.get('combine','')})"),
            "proposer": s.get("proposer", ""),
        })
        haz = s.get("hazards", {})
        if haz.get("h3_sign_corrected"):
            events.append({
                "node": "A", "kind": "sign", "screen": sid,
                "title": f"{sid} · sign corrected",
                "detail": haz.get("h3_source_evidence", "effect column was inverted"),
                "proposer": s.get("proposer", ""),
            })
        events.append({
            "node": "A", "kind": "validate", "screen": sid,
            "title": f"{sid} · validated" + (f" · {n_rows:,} rows" if n_rows else ""),
            "detail": (f"{len(s.get('unmapped_columns', []))} columns unmapped (recorded) · "
                       f"regime {sig.get('regime', s.get('validation', {}).get('regime',''))}"),
            "proposer": s.get("proposer", ""),
        })
    for c in _PROVENANCE.get("claims", []):
        cit = c.get("citation", {})
        events.append({
            "node": "B", "kind": "cite", "screen": None, "gene": c.get("gene"),
            "title": f"{c.get('gene')} · hypothesis cited",
            "detail": f"PMID {cit.get('accession')} [{cit.get('status')}] · {cit.get('title','')}",
            "url": cit.get("url"),
        })
    return events


@app.get("/api/agent_activity")
def agent_activity() -> dict:
    """The Agent Activity rail: a timeline of the peripheral agents' real decisions, plus the
    provenance the rail links to. Honest empty state when the receipt isn't built (200 with an
    empty list, so the rail renders 'no run yet' rather than erroring)."""
    return {
        "built": bool(_PROVENANCE),
        "sign_convention": _PROVENANCE.get("sign_convention"),
        "n_screens": len(_PROVENANCE.get("screens", [])),
        "n_claims": len(_PROVENANCE.get("claims", [])),
        "events": _agent_activity(),
    }


@app.get("/api/sources/{gene}")
def sources(gene: str) -> dict:
    """Node B's cited hypotheses for one gene — what the Sources section under the verdict shows.

    Returns only RETRIEVAL-grounded, cited claims (the interpretation agent emits nothing else).
    Empty list when the gene has no hypotheses (a replicated gene, or one with no retrieved
    papers) — the section then shows why, never a fabricated mechanism."""
    g = gene.upper()
    claims = [c for c in _PROVENANCE.get("claims", []) if str(c.get("gene", "")).upper() == g]
    return {"gene": g, "claims": claims}


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

    # The chat endpoint runs the Concord mRNA×protein reconciliation agent (its own tools +
    # prompt). The former Target Triage shortlist agent has been removed; `run_triage` is the
    # agent-agnostic driver, so building Concord's options here is the only agent it serves.
    from ..llm.concord_prompt import build_concord_options
    agent_options = build_concord_options()

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
            await run_triage(task, on_message=on_message, options=agent_options)
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


# Concord is the default landing page. An explicit "/" route is matched BEFORE the catch-all
# StaticFiles mount below, so the bare root redirects to Concord instead of serving Target
# Triage's index.html. Both apps still coexist: Target Triage remains reachable at /index.html,
# and every /api/* route and /concord-app.html is unchanged.
@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse(url="/concord-app.html")


# No-cache for the HTML shell so a reload ALWAYS gets the latest JS/CSS. StaticFiles emits an etag
# but no Cache-Control, and browsers (incl. the preview pane) then hold the parsed JS across reloads
# — the "I fixed it but the tab shows the old behaviour" trap. A middleware sets the header on the
# way OUT, which works no matter whether the route or the StaticFiles mount produced the response
# (an explicit route was tried first but the Mount("/") shadowed it — middleware sidesteps that).
@app.middleware("http")
async def _no_cache_html(request, call_next):
    response = await call_next(request)
    path = request.url.path
    if path.endswith(".html") or path == "/":
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
    return response


# Static frontend last, so /api/* and the explicit routes above win routing.
if os.path.isdir(_WEB_DIR):
    app.mount("/", StaticFiles(directory=_WEB_DIR, html=True), name="web")
