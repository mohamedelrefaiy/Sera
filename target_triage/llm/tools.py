"""Agent tools: expose the deterministic pipeline to Claude via the Agent SDK.

Each @tool is a thin, honest wrapper around a pure src/ function. The agent calls
them, reads the numbers, and REASONS over the results — the tool calls are the
inspectable agent loop that is the project's "Claude Use" headline. The tools never
decide the verdict narrative; they return computed facts and let the agent argue.

Data is loaded once at import into module-level immutable state (the screen is
static), so repeated tool calls are cheap.
"""
from __future__ import annotations

import json

from claude_agent_sdk import create_sdk_mcp_server, tool

from ..core.data import CONDITIONS, load_perturbations
from ..core.evidence import load_evidence
from ..core.ranking import rank_by_impact, significant_records
from ..core.schema import MARSON
from ..core.verify import Thresholds, verify
from ..clients import clinicaltrials, opentargets

# --- load-once immutable state -------------------------------------------------
_RECORDS = load_perturbations()
_SIGNIFICANT = significant_records(_RECORDS)
_BY_GENE = {r.gene: r for r in _RECORDS}
_EVIDENCE = load_evidence(primary=MARSON.name)   # Marson is primary; never self-corroborate
_RANKED = rank_by_impact(_SIGNIFICANT)
_RAW_RANK = {s.gene: i + 1 for i, s in enumerate(_RANKED)}

# Obvious TCR machinery — high raw impact but not actionable drug targets. The
# agent is TOLD this set so it can reason about reproduction-vs-novelty. Declared by
# the screen, not by this module: one screen's obvious hit is another's positive
# control. The agent layer is Marson-only today, hence MARSON here.
OBVIOUS_TCR = MARSON.obvious

# Canonical condition order so the live condition-context figure always draws
# Rest -> Stim8hr -> Stim48hr regardless of dict insertion order.
_COND_ORDER = {c: i for i, c in enumerate(CONDITIONS)}

# The overlay (actionable) rank per gene — the RIGHT axis of the rank-shift figure.
# Computed once from the deterministic shortlist and memoized; the lazy import
# inside breaks the tools<->shortlist import cycle (shortlist imports _condition_rows
# from this module). First call costs ~0.5s against the warm OT cache (no network);
# every later call is a dict lookup, so the value cannot drift within a process.
_ACTIONABLE_RANK: dict[str, int] | None = None


def _actionable_rank() -> dict[str, int]:
    """Gene -> actionable (overlay) rank, memoized. Returns {} if the shortlist
    can't be computed, so figures degrade to raw-rank-only rather than fabricate."""
    global _ACTIONABLE_RANK
    if _ACTIONABLE_RANK is None:
        try:
            from ..core.shortlist import compute_shortlist  # lazy: breaks the import cycle
            _ACTIONABLE_RANK = {r["gene"]: r["rank"] for r in compute_shortlist()}
        except Exception:  # noqa: BLE001 — never let a figure detail crash a tool call
            _ACTIONABLE_RANK = {}
    return _ACTIONABLE_RANK


def _condition_rows(record) -> list[dict]:
    """Per-condition figure data for a gene, in canonical order. n_downstream stays
    int OR None (None = this screen has no breadth signal) — never coerced to 0, so
    the figure can draw an honest 'N/A' bar instead of implying a measured zero."""
    return [
        {"condition": c,
         "n_downstream": p.n_downstream,
         "effect_size": round(p.effect_size, 2),
         "n_cells": p.n_cells,
         "significant": p.significant,
         "offtarget": p.offtarget}
        for c, p in sorted(record.by_condition.items(),
                           key=lambda kv: _COND_ORDER.get(kv[0], 99))
    ]


def _text(payload) -> dict:
    return {"content": [{"type": "text", "text": json.dumps(payload, indent=2)}]}


# --- view-update protocol ------------------------------------------------------
# State-changing tools return both agent-readable facts AND a view_update the
# frontend applies. The API extracts view_update from the tool result text (it is
# embedded under a "__view_update__" key) and forwards it to the browser, so the
# agent's tool call IS the UI action — the chat is the control surface, not a sidecar.

def _view(agent_facts: dict, view_update: dict) -> dict:
    payload = {**agent_facts, "__view_update__": view_update}
    return {"content": [{"type": "text", "text": json.dumps(payload, indent=2)}]}


def _row_for(gene: str, thresholds: Thresholds = Thresholds()) -> dict | None:
    """Compute one gene's full ledger row (verdict + checks) — the shape the UI renders."""
    record = _BY_GENE.get(gene)
    if record is None:
        return None
    s = next((x for x in _RANKED if x.gene == gene), None)
    v = verify(record, _EVIDENCE, thresholds)
    return {
        "gene": gene,
        "verdict": v.verdict,
        "raw_rank": _RAW_RANK.get(gene),
        "actionable_rank": _actionable_rank().get(gene),  # right axis of the rank-shift figure
        "impact": round(s.impact, 3) if s else None,
        "context_specificity": round(s.context_specificity, 3) if s else None,
        "best_condition": s.best_condition if s else None,
        "is_obvious_tcr": gene in OBVIOUS_TCR,
        "by_condition": _condition_rows(record),          # per-condition bars (context figure)
        "checks": [
            {"check": c.name, "kind": c.kind, "pass": c.passed,
             "value": c.value, "detail": c.detail}
            for c in v.checks
        ],
    }


@tool(
    "rank_candidates",
    "Rank genes by knockdown impact (effect breadth x strength) in the CD4+ T-cell "
    "Perturb-seq screen. Returns the top N with raw rank, impact, context specificity, "
    "and whether each is obvious TCR machinery (reproduction) vs a potential novel target.",
    {"top_n": int},
)
async def rank_candidates(args):
    top_n = int(args.get("top_n") or 25)
    rows = []
    for s in _RANKED[:top_n]:
        rows.append({
            "gene": s.gene, "raw_rank": _RAW_RANK[s.gene],
            "impact": round(s.impact, 2),
            "context_specificity": round(s.context_specificity, 2),
            "best_condition": s.best_condition,
            "is_obvious_tcr_machinery": s.gene in OBVIOUS_TCR,
        })
    return _text({"top_n": top_n, "total_ranked": len(_RANKED), "candidates": rows})


@tool(
    "verify_candidate",
    "Adversarially verify one gene: run gate/score/bonus checks (real knockdown, "
    "power, cross-donor & cross-guide robustness, cross-condition, independent-screen "
    "corroboration) and return the verdict (REJECT / PROMOTE / PROMOTE weak / "
    "PROMOTE corroborated) with every check's number. Use this before trusting any pick.",
    {"gene": str},
)
async def verify_candidate(args):
    gene = args["gene"]
    record = _BY_GENE.get(gene)
    if record is None:
        return _text({"gene": gene, "error": "not in screen"})
    v = verify(record, _EVIDENCE)
    return _text({
        "gene": gene, "verdict": v.verdict, "reason": v.reason,
        "raw_rank": _RAW_RANK.get(gene),
        "checks": [
            {"check": c.name, "kind": c.kind, "pass": c.passed,
             "value": c.value, "detail": c.detail}
            for c in v.checks
        ],
    })


@tool(
    "check_open_targets",
    "Query Open Targets (live, cached) for a gene: small-molecule druggability score, "
    "best immune-disease genetic-association score + the disease, and clinical stage. "
    "Independent of the screen — use it to judge whether a verified hit is actionable.",
    {"gene": str},
)
async def check_open_targets(args):
    gene = args["gene"]
    record = _BY_GENE.get(gene)
    if record is None:
        return _text({"gene": gene, "error": "not in screen"})
    ann = opentargets.annotate_many([(gene, record.ensembl_id)]).get(gene)
    return _text({
        "gene": gene, "druggable_score": ann.druggable_score,
        "disease_score": ann.disease_score, "top_immune_disease": ann.top_disease,
        "clinical_stage": ann.clinical_stage,
    })


@tool(
    "check_clinical_trials",
    "Verify a compound live on ClinicalTrials.gov: returns the most-advanced phase, "
    "NCT id, and recruiting status, or found=false. Use to check whether a target's "
    "named inhibitor is already in the clinic (the 'predicted blind, clinic agrees' beat).",
    {"compound": str},
)
async def check_clinical_trials(args):
    t = clinicaltrials.lookup(args["compound"])
    return _text({
        "compound": t.compound, "found": t.found, "phase": t.phase_label,
        "nct_id": t.nct_id, "status": t.status, "title": t.title,
    })


# --- STATEFUL tools: the agent drives the scientist's view ---------------------

@tool(
    "set_view",
    "Update the shortlist the scientist is looking at: filter by condition "
    "(Rest/Stim8hr/Stim48hr), minimum druggability (0..1), or promoted-only. Use this "
    "when the scientist asks to narrow or change what's shown (e.g. 'only resting-state "
    "regulators', 'druggable ones'). This RE-RENDERS their table.",
    {"condition": str, "min_druggable": float, "promoted_only": bool},
)
async def set_view(args):
    from ..core.shortlist import compute_shortlist
    condition = (args.get("condition") or "").strip() or None
    min_drug = float(args.get("min_druggable") or 0.0)
    promoted = bool(args.get("promoted_only"))

    rows = compute_shortlist()
    if condition:
        rows = [r for r in rows if r["best_condition"] == condition]
    if min_drug > 0:
        rows = [r for r in rows if r["druggable_score"] >= min_drug]
    if promoted:
        rows = [r for r in rows if r["verdict"].startswith("PROMOTE")]

    summary = {"shown": len(rows), "top": [r["gene"] for r in rows[:8]],
               "filters": {"condition": condition, "min_druggable": min_drug,
                           "promoted_only": promoted}}
    return _view(summary, {"action": "set_rows", "rows": rows[:50],
                           "filters": summary["filters"]})


@tool(
    "focus_gene",
    "Bring one gene into focus in the scientist's view and open its evidence — even if "
    "it ranks far down the list. Use when the scientist names a gene ('pull PTPN2', "
    "'show me CBLB'). Returns its verdict + every check, and opens its drawer.",
    {"gene": str},
)
async def focus_gene(args):
    gene = (args.get("gene") or "").strip().upper()
    row = _row_for(gene)
    if row is None:
        return _text({"gene": gene, "error": "not in screen"})
    return _view(row, {"action": "focus", "gene": gene, "row": row})


@tool(
    "reverify",
    "Re-run adversarial verification on a gene, overriding ONLY the thresholds you pass. "
    "Set just the one(s) the scientist named — e.g. to stress-test with a stricter donor "
    "cutoff, pass only min_donor_corr. EVERY OTHER THRESHOLD KEEPS ITS CALIBRATED DEFAULT; "
    "do not pass values you were not asked to change (passing 0 is treated as 'unset'). "
    "Returns the new verdict + checks and reports exactly which thresholds changed, so the "
    "comparison to the default verdict is apples-to-apples.",
    {"gene": str, "min_donor_corr": float, "min_guide_corr": float,
     "min_effect": float, "min_cells": float, "min_downstream": int},
)
async def reverify(args):
    gene = (args.get("gene") or "").strip().upper()
    if gene not in _BY_GENE:
        return _text({"gene": gene, "error": "not in screen"})
    d = Thresholds()

    def override(key, default, cast):
        """Use the arg only if present and positive; 0/absent -> keep the default.
        This guarantees a re-verify changes ONLY what the scientist asked for."""
        v = args.get(key)
        return cast(v) if (v is not None and float(v) > 0) else default

    t = Thresholds(
        min_cells=override("min_cells", d.min_cells, float),
        min_effect=override("min_effect", d.min_effect, float),
        min_downstream=override("min_downstream", d.min_downstream, int),
        min_donor_corr=override("min_donor_corr", d.min_donor_corr, float),
        min_guide_corr=override("min_guide_corr", d.min_guide_corr, float),
    )
    changed = {k: getattr(t, k) for k in
               ("min_donor_corr", "min_guide_corr", "min_effect", "min_cells", "min_downstream")
               if getattr(t, k) != getattr(d, k)}

    row = _row_for(gene, t)
    row["thresholds"] = changed                  # only the deltas — honest banner
    row["reverified"] = True
    return _view({**row, "changed_thresholds": changed or "none (same as default)"},
                 {"action": "focus", "gene": gene, "row": row})


def build_server():
    """Create the in-process MCP server hosting all Target Triage tools."""
    return create_sdk_mcp_server(
        name="target_triage",
        version="0.1.0",
        tools=[rank_candidates, verify_candidate, check_open_targets, check_clinical_trials,
               set_view, focus_gene, reverify],
    )


ALLOWED_TOOLS = [
    "mcp__target_triage__rank_candidates",
    "mcp__target_triage__verify_candidate",
    "mcp__target_triage__check_open_targets",
    "mcp__target_triage__check_clinical_trials",
    "mcp__target_triage__set_view",
    "mcp__target_triage__focus_gene",
    "mcp__target_triage__reverify",
]
