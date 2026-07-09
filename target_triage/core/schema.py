"""ScreenSchema — how to read ANY screen's columns into our internal records.

This is what makes Target Triage a reusable INSTRUMENT rather than a Marson-specific
analysis: the pipeline (rank, verify, agent, UI) all consume GeneRecord and know
nothing about column names. A ScreenSchema maps one screen's actual columns onto
that internal shape. Marson is just one registered schema; any screen with a gene
column, an effect column, and a significance signal can be added.

Signals are OPTIONAL where screens genuinely differ:
  - breadth (downstream-gene count) — Perturb-seq has it; a MAGeCK screen does not.
    When absent, ranking falls back to effect size and breadth-based checks report N/A.
  - significance — either an explicit boolean column, OR derived from an FDR column
    against a threshold (fdr_col + fdr_max). A schema declares which.
The tool NEVER fabricates a signal a screen lacks; it degrades honestly.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

# schema.py lives at target_triage/core/schema.py; data/ is bundled inside the
# package at target_triage/data/ — two dirname() hops (core -> target_triage).
_PKG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DATA = os.path.join(_PKG, "data")


@dataclass(frozen=True)
class ScreenSchema:
    """Declarative mapping from a screen's columns to our internal record fields.

    Required: gene_col, effect_col, and a significance signal (sig_col OR fdr_col).
    Everything else is optional and degrades honestly when absent.
    """

    name: str
    path: str
    gene_col: str
    effect_col: str
    # significance: exactly one of these paths is used
    sig_col: str | None = None            # explicit bool-ish column ("True"/"False")
    fdr_col: str | None = None            # derive significance from FDR < fdr_max
    fdr_max: float = 0.10
    # optional signals
    condition_col: str | None = None      # if absent, the whole screen is one condition
    breadth_col: str | None = None        # downstream-gene count; None = no breadth signal
    offtarget_col: str | None = None      # off-target confound flag; None = assume clean
    ensembl_col: str | None = None        # for Open Targets lookup; None = symbol only
    ncells_col: str | None = None         # cell count for power check; None = unknown
    # the scientist's positive controls for THIS screen (the controls-first gate)
    controls: tuple[str, ...] = ()
    # Genes already textbook for THIS assay. Ranked down so novelty floats up. A gene
    # obvious in one screen may be another's positive control (Marson's TCR signalosome
    # IS Schmidt's control set), so this can never be a global constant.
    obvious: frozenset[str] = frozenset()
    # Demo anchors for THIS screen: a gene mapped to a clinical-stage, checkable molecule.
    spotlight: dict[str, dict[str, str]] = field(default_factory=dict)
    description: str = ""
    # which internal signals this screen actually provides
    has_breadth: bool = False
    has_conditions: bool = False
    # The quantity this screen can honestly plot against effect size. A screen with
    # breadth but no per-perturbation p-value (Marson) plots breadth; a screen with an
    # FDR but no breadth (Schmidt2022) plots -log10(FDR). The chart reads this; it never
    # assumes an axis. See docs/adr/0001-screens-declare-their-own-capabilities.md.
    impact_axis: str = "breadth"          # "breadth" | "neg_log10_fdr"


# ---- registered built-in screens (each is a worked proof the tool is reusable) ----

MARSON = ScreenSchema(
    name="marson",
    path=os.path.join(_DATA, "marson_perturbseq", "DE_stats.suppl_table.csv"),
    gene_col="target_contrast_gene_name",
    effect_col="ontarget_effect_size",
    sig_col="ontarget_significant",
    condition_col="culture_condition",
    breadth_col="n_downstream",
    offtarget_col="offtarget_flag",
    ensembl_col="target_contrast",
    ncells_col="n_cells_target",
    controls=("RASA2", "IL2RA", "CTLA4", "FOXP3", "TNFAIP3"),
    # The TCR signalosome: real hits, but nobody needs a screen to find them.
    obvious=frozenset({
        "CD3E", "CD3D", "CD3G", "CD247", "LAT", "ZAP70", "PLCG1", "LCP2",
        "VAV1", "CD28", "LCK", "FYN", "ITK", "CD2", "CD5",
    }),
    spotlight={
        "PTPN2": {"compound": "ABBV-CLS-484",
                  "note": "druggable phosphatase brake on T-cell activation"},
        "CBLB": {"compound": "NX-1607",
                 "note": "context-dependent brake on T-cell activation, MS-linked"},
    },
    description="Marson genome-scale CD4+ T-cell Perturb-seq (Zhu et al. 2025)",
    has_breadth=True,
    has_conditions=True,
    impact_axis="breadth",             # no per-perturbation p-value; breadth is the honest axis
)

# Schmidt 2022 — a DIFFERENT assay format (MAGeCK CRISPRi): FDR-based significance,
# NO downstream-breadth column, condition = the 'phenotype' readout. Proves the same
# instrument runs on a genuinely different screen, degrading where signals are absent.
SCHMIDT2022 = ScreenSchema(
    name="schmidt2022",
    path=os.path.join(_DATA, "external_screens", "Schmidt2022_CRISPRi_gene_phenotypes.csv"),
    gene_col="id",
    effect_col="neg|lfc",              # log-fold-change of the readout on knockdown
    fdr_col="neg|fdr",
    fdr_max=0.10,
    condition_col="phenotype",         # e.g. "CD4+ IL2", "CD8+ IFNG"
    breadth_col=None,                  # MAGeCK screens have no downstream-gene count
    offtarget_col=None,
    ensembl_col=None,
    ncells_col=None,
    # Schmidt's own positive regulators of T-cell activation — the TCR signalosome,
    # which a cytokine-production screen must recover. (PTPN2/CBLB are brakes and are
    # NOT significant hits here — correctly, so they'd be the wrong controls.)
    controls=("VAV1", "LCP2", "ZAP70", "CD3D", "LAT"),
    # Deliberately EMPTY, and it must stay disjoint from `controls` above. Marson's
    # obvious set IS this screen's control set; importing it would penalise the very
    # genes that prove the screen loaded correctly. A cytokine-production screen has no
    # established "too obvious to report" list of its own.
    obvious=frozenset(),
    # PTPN2/CBLB are brakes and are not significant hits here; Marson's anchors are
    # meaningless on this screen.
    spotlight={},
    description="Schmidt & Steinhart 2022 CRISPRi CD4+/CD8+ cytokine screen (Science)",
    has_breadth=False,
    has_conditions=True,
    impact_axis="neg_log10_fdr",       # no breadth; the classic volcano axis
)

REGISTRY: dict[str, ScreenSchema] = {s.name: s for s in (MARSON, SCHMIDT2022)}


def get_schema(name: str) -> ScreenSchema:
    if name not in REGISTRY:
        raise KeyError(f"unknown screen '{name}'; known: {sorted(REGISTRY)}")
    return REGISTRY[name]
