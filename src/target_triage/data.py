"""Data boundary: load the Marson CD4+ T-cell Perturb-seq DE summary.

This is the single place raw CSV enters the system. Everything downstream works
on immutable frozen records — no module mutates a perturbation row in place.

The DE summary is a per-(gene, condition) table of knockdown effects. Columns we
rely on (validated against the suppl table): target_contrast_gene_name (symbol),
culture_condition (Rest/Stim8hr/Stim48hr), target_contrast (Ensembl id),
n_cells_target, ontarget_effect_size (negative = knockdown), ontarget_significant,
offtarget_flag, n_downstream (# downstream DE genes = effect breadth).
"""
from __future__ import annotations

import csv
import os
from dataclasses import dataclass

CONDITIONS = ("Rest", "Stim8hr", "Stim48hr")

# Resolve the committed data path relative to the repo, not the CWD.
_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DE_STATS_CSV = os.path.join(
    _REPO, "data", "marson_perturbseq", "DE_stats.suppl_table.csv"
)


@dataclass(frozen=True)
class Perturbation:
    """One (gene, condition) knockdown measurement. Immutable by construction."""

    gene: str
    condition: str
    ensembl_id: str
    n_cells: float
    effect_size: float          # ontarget_effect_size; negative = knockdown
    significant: bool           # ontarget_significant
    offtarget: bool             # offtarget_flag
    n_downstream: int           # effect breadth


@dataclass(frozen=True)
class GeneRecord:
    """A gene's measurements across all conditions it was tested in."""

    gene: str
    ensembl_id: str
    by_condition: dict[str, Perturbation]  # condition -> Perturbation (read-only by convention)


def _to_float(value: str, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_bool(value: str) -> bool:
    return value == "True"


def load_perturbations(path: str = DE_STATS_CSV) -> tuple[GeneRecord, ...]:
    """Load the DE summary into immutable GeneRecords, one per gene.

    Raises FileNotFoundError with an actionable message if the committed data is
    missing — fail fast at the boundary rather than producing an empty shortlist.
    """
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"DE summary not found at {path}. Expected the committed Marson suppl "
            f"table under data/marson_perturbseq/."
        )

    by_gene: dict[str, dict[str, Perturbation]] = {}
    ensembl: dict[str, str] = {}
    with open(path, newline="") as fh:
        for row in csv.DictReader(fh):
            gene = row["target_contrast_gene_name"]
            cond = row["culture_condition"]
            pert = Perturbation(
                gene=gene,
                condition=cond,
                ensembl_id=row["target_contrast"],
                n_cells=_to_float(row["n_cells_target"]),
                effect_size=_to_float(row["ontarget_effect_size"]),
                significant=_to_bool(row["ontarget_significant"]),
                offtarget=_to_bool(row["offtarget_flag"]),
                n_downstream=int(_to_float(row["n_downstream"])),
            )
            by_gene.setdefault(gene, {})[cond] = pert
            ensembl[gene] = pert.ensembl_id

    return tuple(
        GeneRecord(gene=g, ensembl_id=ensembl[g], by_condition=dict(conds))
        for g, conds in by_gene.items()
    )
