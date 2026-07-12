"""Shortlist service: the deterministic data path behind the UI.

Produces the ranked + verified + externally-annotated shortlist as plain
serializable dicts. NO Claude API call — pure computation over the screen plus
cached Open Targets scores. This is what the web app's table loads instantly and
what a judge sees on `git clone` without any key. The agent (chat) is a separate,
optional layer on top.

Ranking decides ORDER (impact x druggability overlay); verify decides TRUST.
Both are shown, including rejected candidates.
"""
from __future__ import annotations

from .data import CONDITIONS, load_screen
from .evidence import load_evidence
from .ranking import rank_by_impact, significant_records
from .schema import MARSON, ScreenSchema
from .verify import verify
from ..clients import opentargets

# Canonical condition order (Rest -> Stim8hr -> Stim48hr) so per-condition figure data
# always draws in time order regardless of dict insertion order.
_COND_ORDER = {c: i for i, c in enumerate(CONDITIONS)}


def _condition_rows(record) -> list[dict]:
    """Per-condition figure data for a gene, in canonical order. n_downstream stays
    int OR None (None = this screen has no breadth signal) — never coerced to 0, so
    the figure can draw an honest 'N/A' bar instead of implying a measured zero.

    Relocated from llm/tools.py when the Target Triage agent layer was removed; the
    logic is unchanged (a pure reshape of record.by_condition), so the deterministic
    shortlist keeps its per-condition bars with no dependency on the agent layer."""
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

# Back-compat: the Marson anchors, which used to live here as a global. Screens now
# declare their own (ScreenSchema.spotlight) — see docs/adr/0001-*.md. Prefer
# `schema.spotlight`; this alias exists only for callers not yet threading a screen.
SPOTLIGHT = MARSON.spotlight


def _overlay(impact: float, drug: float, disease: float, obvious: bool) -> float:
    """Actionable score: raw impact reweighted by druggability x disease genetics,
    damping obvious TCR machinery so novelty (druggable+disease) floats up."""
    mult = (0.5 + drug) * (0.5 + disease)
    penalty = 0.15 if obvious else 1.0
    return impact * mult * penalty


def compute_shortlist(schema: ScreenSchema = MARSON, annotate: bool = True,
                      progress=None) -> list[dict]:
    """Return every significant gene as a scored, verified, annotated dict, sorted
    by actionable score (descending). Deterministic; uses cached OT scores.

    The screen supplies its own biology: which genes are already obvious (ranked down)
    and which external screens remain genuinely held out. Nothing here is Marson-specific.
    """
    records = load_screen(schema)
    sig = significant_records(records)
    by_gene = {r.gene: r for r in records}
    # The screen under analysis is never its own corroboration.
    evidence = load_evidence(primary=schema.name)

    ranked = rank_by_impact(sig)
    raw_rank = {s.gene: i + 1 for i, s in enumerate(ranked)}

    ann = {}
    if annotate:
        pairs = [(s.gene, by_gene[s.gene].ensembl_id) for s in ranked]
        ann = opentargets.annotate_many(pairs, progress=progress)

    rows: list[dict] = []
    for s in ranked:
        a = ann.get(s.gene)
        drug = a.druggable_score if a else 0.0
        disease = a.disease_score if a else 0.0
        obvious = s.gene in schema.obvious
        v = verify(by_gene[s.gene], evidence)
        rows.append({
            "gene": s.gene,
            "verdict": v.verdict,
            "actionable_score": round(_overlay(s.impact, drug, disease, obvious), 3),
            "raw_rank": raw_rank[s.gene],
            "impact": round(s.impact, 3),
            "context_specificity": round(s.context_specificity, 3),
            "best_condition": s.best_condition,
            "druggable_score": drug,
            "disease_score": disease,
            "top_disease": a.top_disease if a else None,
            "clinical_stage": a.clinical_stage if a else None,
            "is_obvious_tcr": obvious,
            "by_condition": _condition_rows(by_gene[s.gene]),  # per-condition bars (offline figure)
            "checks": [
                {"check": c.name, "kind": c.kind, "pass": c.passed,
                 "value": c.value, "detail": c.detail}
                for c in v.checks
            ],
        })

    rows.sort(key=lambda r: r["actionable_score"], reverse=True)
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    return rows


def get_target(gene: str, rows: list[dict] | None = None,
               schema: ScreenSchema = MARSON) -> dict | None:
    """Full record for one gene (from a precomputed shortlist or a fresh compute)."""
    rows = rows if rows is not None else compute_shortlist(schema)
    return next((r for r in rows if r["gene"] == gene), None)
