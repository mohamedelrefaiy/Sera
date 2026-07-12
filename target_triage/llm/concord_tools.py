"""Concord agent tools: expose the mRNA×protein reconciliation to Claude via the Agent SDK.

The Concord chat is a REAL reasoning agent, not a fixed router: it reads the message, decides
whether to reconcile a gene, pull its evidence, or just answer, and calls the matching tool. Each
@tool is a thin wrapper around the DETERMINISTIC concordance artifact — the tool returns the
code-computed verdict and the words-only `plain` summary; the agent NARRATES, it never recomputes
or overrides the verdict (the "agents on the rim" invariant, see docs/architecture/CONCORD_AGENT_BUILD.md).

Data is loaded once at import into immutable module state (the artifact is static), so repeated tool
calls are cheap. Gene arguments are validated against the concordance table — a symbol not in the
screens returns an honest "not in the screens", never a fabricated verdict (the same closed-set gate
the router used).
"""
from __future__ import annotations

import json
import os

from claude_agent_sdk import create_sdk_mcp_server, tool

from ..core.explanation import build_record

# --- load-once immutable state -------------------------------------------------
_HERE = os.path.dirname(os.path.abspath(__file__))
_ARTIFACTS = os.path.join(os.path.dirname(_HERE), "data", "artifacts")
_CONCORDANCE_PARQUET = os.path.join(_ARTIFACTS, "concordance.parquet")
_ENRICHMENT_JSON = os.path.join(_ARTIFACTS, "enrichment.json")
_GROUND_TRUTH_JSON = os.path.join(_ARTIFACTS, "ground_truth.json")


def _load_concordance() -> list[dict]:
    """Read the concordance artifact into JSON-able dicts, or [] if absent. NaN -> None so the
    payload is valid JSON. Mirrors api/app.py's loader so the agent sees the SAME rows the
    deterministic endpoints serve."""
    if not os.path.exists(_CONCORDANCE_PARQUET):
        return []
    import math

    import pandas as pd

    df = pd.read_parquet(_CONCORDANCE_PARQUET)
    clean: list[dict] = []
    for r in df.to_dict("records"):
        row = {}
        for k, v in r.items():
            if v is None or (isinstance(v, float) and math.isnan(v)):
                row[k] = None
            elif hasattr(v, "item"):
                row[k] = v.item()
            else:
                row[k] = v
        clean.append(row)
    return clean


def _load_enrichment() -> dict[str, dict]:
    if not os.path.exists(_ENRICHMENT_JSON):
        return {}
    with open(_ENRICHMENT_JSON) as fh:
        return json.load(fh)


def _load_ground_truth() -> dict:
    if not os.path.exists(_GROUND_TRUTH_JSON):
        return {}
    with open(_GROUND_TRUTH_JSON) as fh:
        return json.load(fh)


_CONCORDANCE = _load_concordance()
_ENRICHMENT = _load_enrichment()
_GROUND_TRUTH = _load_ground_truth()
_GENES = {r["gene"] for r in _CONCORDANCE}          # the ONLY genes a tool may resolve
_BY_GENE: dict[str, list[dict]] = {}
for _r in _CONCORDANCE:
    _BY_GENE.setdefault(_r["gene"], []).append(_r)


def _text(payload) -> dict:
    return {"content": [{"type": "text", "text": json.dumps(payload, indent=2)}]}


def _view(agent_facts: dict, view_update: dict) -> dict:
    """A tool result that ALSO carries a UI action. The API extracts `__view_update__` from the
    tool-result text and forwards it to the browser, so the agent's tool call IS the UI action —
    same protocol as llm/tools.py."""
    payload = {**agent_facts, "__view_update__": view_update}
    return {"content": [{"type": "text", "text": json.dumps(payload, indent=2)}]}


@tool(
    "reconcile_gene",
    "Reconcile ONE gene's mRNA (Perturb-seq) vs protein (FACS) CRISPR screens into the "
    "code-computed concordance verdict (replicated / discordant / mRNA-only / protein-only / "
    "neither), per activation condition. Returns the verdict plus a WORDS-ONLY summary (transcript "
    "up/down, protein up/down, strength) you narrate from — never quote the raw statistics. Call "
    "this whenever the user names a gene or asks whether the two screens agree about one.",
    {"gene": str},
)
async def reconcile_gene(args):
    gene = (args.get("gene") or "").strip().upper()
    hits = _BY_GENE.get(gene)
    if not hits:
        return _text({"gene": gene, "error": "not in the screens",
                      "note": "Only genes present in both CRISPR screens can be reconciled."})
    # Words-only summaries per condition (reuse the experimentalist-voice record builder), plus the
    # verdict the CODE computed — the agent narrates, it does not decide.
    by_condition = {}
    for r in hits:
        rec = build_record(r, gene)
        by_condition[r["condition"]] = {"verdict": r["verdict"], "plain": rec["plain"]}
    # pick the most actionable condition to focus (Stim48hr is the canonical demo condition)
    focus_cond = "Stim48hr" if "Stim48hr" in by_condition else hits[0]["condition"]
    return _view(
        {"gene": gene, "cytokine": hits[0]["cytokine"], "by_condition": by_condition,
         "focus_condition": focus_cond,
         "note": "Verdict computed by code. Narrate the `plain` summary in plain language — do NOT "
                 "quote z-scores, log-fold-changes, p-values or FDRs; those live in the figure."},
        {"action": "reconcile", "gene": gene, "condition": focus_cond},
    )


@tool(
    "gene_evidence",
    "Pull the actionability dossier for a gene: druggability (small-molecule AND antibody "
    "modality, tracked separately), best immune-disease genetic association, and the QC confidence "
    "tier. Use when the user asks whether a gene is a viable drug target, its disease links, or how "
    "reliable the hit is. Independent of the verdict — use it to judge whether a hit is worth chasing.",
    {"gene": str},
)
async def gene_evidence(args):
    gene = (args.get("gene") or "").strip().upper()
    if gene not in _GENES:
        return _text({"gene": gene, "error": "not in the screens"})
    en = _ENRICHMENT.get(gene)
    if not en:
        return _text({"gene": gene, "enrichment": None,
                      "note": "No enrichment record for this gene — say the dossier is unavailable, "
                              "do not invent druggability or disease links."})
    return _view({"gene": gene, "enrichment": en},
                 {"action": "evidence", "gene": gene})


@tool(
    "known_biology",
    "Answer whether Concord recovers KNOWN IL-2 biology — the validation question, corpus-wide (not "
    "one gene). Returns how many canonical IL-2 regulators the two screens recover, how many are "
    "protein-only (the post-transcriptional ones a transcriptome-only search misses), how the known "
    "brake TSC1 lands, and the point that the aggregate correlation is ~zero and hides this "
    "structure. Call this for 'does it recover known biology?', 'is it validated?', 'does it work?'.",
    {},
)
async def known_biology(args):
    gt = _GROUND_TRUTH
    if not gt:
        return _text({"error": "ground-truth artifact not built",
                      "note": "Say the validation panel isn't available; do not invent recovery numbers."})
    s = gt.get("summary", {})
    n_pos = s.get("n_positive"); n_rec = s.get("n_recovered")
    n_po = s.get("n_protein_only"); n_rep = s.get("n_replicated")
    # WORDS the agent narrates from — no z/lfc here. The panel (view_update) shows the numbers.
    plain = {
        "recovered_summary": f"{n_rec} of {n_pos} canonical IL-2 positive regulators show up as hits",
        "protein_only_finding": (
            f"{n_po} of those are protein-only — the post-transcriptional regulators a "
            "transcript-only screen would miss entirely (this is the payoff)"),
        "replicated_count": f"{n_rep} are replicated (both screens agree)",
        "known_brake": (
            "the canonical brake TSC1 comes out discordant — it lowers the transcript but raises the "
            "protein, so a transcript-only read would have called it backwards"),
        "aggregate_point": (
            "across all shared genes the two screens correlate at essentially zero, so a naive "
            "average would hide every one of these hits — the structure only appears gene by gene"),
        "takeaway": "the recovery of the protein-only regulators is the evidence Concord works",
    }
    return _view(
        {"plain": plain,
         "note": "Narrate from `plain` in plain language for a bench scientist. Do NOT quote the "
                 "z-scores, log-fold-changes, or the raw correlation number; the panel shows those."},
        {"action": "known_biology"},
    )


def build_concord_server():
    """The in-process MCP server hosting Concord's tools."""
    return create_sdk_mcp_server(
        name="concord",
        version="0.1.0",
        tools=[reconcile_gene, gene_evidence, known_biology],
    )


CONCORD_ALLOWED_TOOLS = [
    "mcp__concord__reconcile_gene",
    "mcp__concord__gene_evidence",
    "mcp__concord__known_biology",
]
