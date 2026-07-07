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
from .verify import verify
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


def build_server():
    """Create the in-process MCP server hosting all Target Triage tools."""
    return create_sdk_mcp_server(
        name="target_triage",
        version="0.1.0",
        tools=[rank_candidates, verify_candidate, check_open_targets, check_clinical_trials],
    )


ALLOWED_TOOLS = [
    "mcp__target_triage__rank_candidates",
    "mcp__target_triage__verify_candidate",
    "mcp__target_triage__check_open_targets",
    "mcp__target_triage__check_clinical_trials",
]
