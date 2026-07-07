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

from .data import load_perturbations
from .evidence import load_evidence
from .ranking import rank_by_impact, significant_records
from .verify import Thresholds, verify
from .clients import clinicaltrials, opentargets

# --- load-once immutable state -------------------------------------------------
_RECORDS = load_perturbations()
_SIGNIFICANT = significant_records(_RECORDS)
_BY_GENE = {r.gene: r for r in _RECORDS}
_EVIDENCE = load_evidence()
_RANKED = rank_by_impact(_SIGNIFICANT)
_RAW_RANK = {s.gene: i + 1 for i, s in enumerate(_RANKED)}

# Obvious TCR machinery — high raw impact but not actionable drug targets. The
# agent is TOLD this set so it can reason about reproduction-vs-novelty.
OBVIOUS_TCR = {
    "CD3E", "CD3D", "CD3G", "CD247", "LAT", "ZAP70", "PLCG1", "LCP2",
    "VAV1", "CD28", "LCK", "FYN", "ITK", "CD2", "CD5",
}


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
        "impact": round(s.impact, 3) if s else None,
        "context_specificity": round(s.context_specificity, 3) if s else None,
        "best_condition": s.best_condition if s else None,
        "is_obvious_tcr": gene in OBVIOUS_TCR,
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
    from .shortlist import compute_shortlist
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
    "Re-run adversarial verification on a gene with CUSTOM thresholds, then update its "
    "evidence in the view. Use when the scientist wants to stress-test a pick ('re-verify "
    "NRAS with a stricter donor cutoff'). Any omitted threshold keeps its default. "
    "Returns the new verdict + checks so they can see whether it still survives.",
    {"gene": str, "min_donor_corr": float, "min_guide_corr": float,
     "min_effect": float, "min_cells": float, "min_downstream": int},
)
async def reverify(args):
    gene = (args.get("gene") or "").strip().upper()
    if gene not in _BY_GENE:
        return _text({"gene": gene, "error": "not in screen"})
    d = Thresholds()
    t = Thresholds(
        min_cells=float(args.get("min_cells") or d.min_cells),
        min_effect=float(args.get("min_effect") or d.min_effect),
        min_downstream=int(args.get("min_downstream") or d.min_downstream),
        min_donor_corr=float(args.get("min_donor_corr") or d.min_donor_corr),
        min_guide_corr=float(args.get("min_guide_corr") or d.min_guide_corr),
    )
    row = _row_for(gene, t)
    row["thresholds"] = {"min_donor_corr": t.min_donor_corr, "min_guide_corr": t.min_guide_corr,
                         "min_effect": t.min_effect, "min_cells": t.min_cells,
                         "min_downstream": t.min_downstream}
    return _view(row, {"action": "focus", "gene": gene, "row": row})


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
