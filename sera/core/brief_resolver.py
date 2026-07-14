"""Resolve loaded artifacts into the decision brief's typed inputs.

The brief itself is pure and knows nothing about files or dicts. This module is the bridge: it turns
the concordance row (as the API already loads it) and the provenance receipt into a
`ConcordanceSnapshot`, a `ScreenPairContext`, and the stored `GroundedClaim`s. Keeping it separate
from the endpoint makes the mapping testable on its own and keeps `app.py` thin.

Two honesty rules encoded here:

- **The screen context is authoritative from the canonical adapter, not just provenance.** Provenance
  stores the mRNA screen's cell type / condition as null (Zhu's on-target rows were consumed upstream),
  but the adapter knows the screen is CD4 T cells and the condition is the concordance row's own
  condition. We supply those known constants so the comparability audit fires the REAL caveat
  (timed RNA vs an untimed protein readout) rather than collapsing to "insufficient_metadata".

- **A protein screen with a constant (non-column) condition has NO time axis.** That is exactly the
  Schmidt case, and it is what makes a Stim48hr reconciliation a 48h-RNA-vs-untimed-protein compare.
"""
from __future__ import annotations

from typing import Any

from .decision_brief import (
    ConcordanceSnapshot, GroundedClaim, PositiveControl, ScreenPairContext, TargetDossier,
    select_positive_control)

# The adapter's known constant for the Zhu mRNA screen — CD4 T cells. Provenance stores null (the
# on-target rows that would name it were dropped upstream), so we supply the adapter's fact.
_RNA_CELL_TYPE_DEFAULT = "CD4"


def _screen_by_modality(provenance: dict, modality: str) -> dict | None:
    """The first registered screen of the given modality ('rna' | 'protein'), or None."""
    for s in provenance.get("screens", ()):
        if (s.get("mapping") or {}).get("modality") == modality:
            return s
    return None


def _regime(screen: dict | None, default: str) -> str:
    if not screen:
        return default
    return ((screen.get("mapping") or {}).get("significance") or {}).get("regime") or default


def _prot_has_time_axis(protein_screen: dict | None) -> bool:
    """True only if the protein screen's condition varies by a data column. A stated constant
    (`condition.column is None`) means a single sort with no time axis — the Schmidt case."""
    if not protein_screen:
        return False
    cond = (protein_screen.get("mapping") or {}).get("condition") or {}
    return cond.get("column") is not None


def resolve_snapshot(row: dict[str, Any], provenance: dict) -> ConcordanceSnapshot:
    """Build the snapshot from one concordance row (the dict the API already loads). The verdict and
    direction flags are the code-computed values — passed through, never recomputed."""
    rna = _screen_by_modality(provenance, "rna")
    prot = _screen_by_modality(provenance, "protein")
    return ConcordanceSnapshot(
        gene=row["gene"],
        cytokine=row["cytokine"],
        condition=row["condition"],
        verdict=row["verdict"],
        rna_tested=bool(row.get("rna_tested", True)),
        protein_tested=bool(row.get("prot_tested", True)),
        rna_promotes=row.get("rna_promotes"),
        prot_promotes=row.get("prot_promotes"),
        rna_screen_id=(rna or {}).get("screen_id", "mrna_screen"),
        protein_screen_id=(prot or {}).get("screen_id", "protein_screen"),
    )


def resolve_context(row: dict[str, Any], provenance: dict) -> ScreenPairContext:
    """Build the screen-pair context. The RNA condition is the row's own condition (the adapter
    preserves it); the RNA cell type falls back to the adapter's known CD4 constant when provenance
    is null. The protein screen is treated as having no time axis when its condition is a constant."""
    rna = _screen_by_modality(provenance, "rna")
    prot = _screen_by_modality(provenance, "protein")

    prot_cond = "Stimulated"
    prot_cell = "CD4"
    if prot:
        cond = (prot.get("mapping") or {}).get("condition") or {}
        prot_cond = cond.get("constant") or prot_cond
        # protein cell type may be a stated constant; if it's column-derived we don't have the row
        # here, so fall back to the known CD4 constant rather than guessing.
        cell = (prot.get("mapping") or {}).get("cell_type") or {}
        prot_cell = cell.get("constant") or prot_cell

    return ScreenPairContext(
        rna_cell_type=_RNA_CELL_TYPE_DEFAULT,        # adapter's known fact (provenance stores null)
        rna_condition=row["condition"],              # adapter preserves the RNA condition
        rna_regime=_regime(rna, "deseq2_adjp"),
        prot_cell_type=prot_cell,
        prot_condition=prot_cond,
        prot_regime=_regime(prot, "benjamini_fdr"),
        prot_has_time_axis=_prot_has_time_axis(prot),
    )


def resolve_claims(gene: str, cytokine: str, condition: str,
                   provenance: dict) -> tuple[GroundedClaim, ...]:
    """The stored, pre-resolved literature claims for this (gene, cytokine, condition). Reused
    verbatim — never regenerated. Each is background for a hypothesis; `supports` is fixed to
    `mtor_background` because that is what the stored TSC1 citations (TSC2/mTOR papers) actually
    back — never the verdict. Only claims whose citation is RESOLVED are surfaced."""
    out: list[GroundedClaim] = []
    g = gene.upper()
    for c in provenance.get("claims", ()):
        if (c.get("gene") or "").upper() != g:
            continue
        if cytokine and (c.get("cytokine") or "").upper() != cytokine.upper():
            continue
        if condition and c.get("condition") and c.get("condition") != condition:
            continue
        cit = c.get("citation") or {}
        if cit.get("status") != "resolved" or not cit.get("accession"):
            continue                                  # never surface an unresolved citation
        out.append(GroundedClaim(
            text=c.get("claim", ""),
            label=c.get("label", "HYPOTHESIS — not used in verdict"),
            db=cit.get("db", "pubmed"),
            accession=str(cit.get("accession")),
            title=cit.get("title", ""),
            supports="mtor_background",               # the claim's real scope, never "verdict"
        ))
    return tuple(out)


def resolve_dossier(enrichment: dict | None) -> TargetDossier | None:
    """Map a gene's `enrichment.json` record to a `TargetDossier`, or None when absent.

    Returns None (not a zeroed dossier) when the enrichment is missing, so the brief's advancement
    recommendation degrades to the honest 'unknown' sentinel rather than a fabricated 'deprioritise'.
    Reads only the fields the axis needs; the raw scores flow through, the brief owns their meaning."""
    if not enrichment:
        return None
    drug = enrichment.get("druggability") or {}
    dis = enrichment.get("disease") or {}
    qual = enrichment.get("quality") or {}
    return TargetDossier(
        sm_score=float(drug.get("sm_score") or 0.0),
        sm_stage=drug.get("sm_stage"),
        ab_score=float(drug.get("ab_score") or 0.0),
        ab_stage=drug.get("ab_stage"),
        disease_score=float(dis.get("score") or 0.0),
        top_disease=dis.get("top_disease"),
        qc_confidence=qual.get("confidence") or "Low",
    )


def resolve_positive_control(
    rows: list[dict[str, Any]],
    ground_truth: dict,
    cytokine: str,
    condition: str,
    focal_gene: str | None = None,
) -> PositiveControl | None:
    """Map the ground-truth artifact's curated regulator list onto `select_positive_control`.

    `ground_truth["positive_regulators"]` is a LIST OF DICTS (not gene strings) — each entry looks
    like `{"gene": "VAV1", "role": ..., "verdict": ..., ...}`. The gene symbol must be pulled out of
    each dict; passing the dicts straight through would silently match nothing (a dict is never `in`
    a curated-genes membership test the way a string is), so this extraction is not optional
    boilerplate — it is the fix for that exact gotcha.

    `focal_gene` (the gene the brief is about) is passed through so the selector never picks the gene
    under test as its own positive control — that would be a circular, false claim."""
    curated_genes = [d["gene"] for d in ground_truth.get("positive_regulators", [])]
    return select_positive_control(rows, curated_genes, cytokine, condition, focal_gene=focal_gene)
