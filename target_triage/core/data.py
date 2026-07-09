"""Data boundary: load ANY screen into our internal records via a ScreenSchema.

This is the single place raw CSV enters the system. Everything downstream works
on immutable frozen records — no module mutates a perturbation row in place, and
NOTHING downstream knows a screen's column names. A ScreenSchema (see schema.py)
maps a given screen's columns onto Perturbation/GeneRecord; that is what lets the
same instrument run on Marson, Schmidt2022, or a scientist's own screen.

Signals a screen may or may not carry are represented honestly:
  - n_downstream is int OR None (None = this screen has no breadth signal).
  - fdr is float OR None (None = this screen reports no FDR — NOT "FDR of zero").
  - offtarget defaults to False when the screen has no off-target flag.
  - condition defaults to a single "all" bucket when the screen has no condition col.

breadth and fdr are the two candidate impact axes; a screen declares which one it can
honestly plot (ScreenSchema.impact_axis). Neither field ever stands in for the other.
"""
from __future__ import annotations

import csv
import os
from dataclasses import dataclass

from .schema import MARSON, ScreenSchema

CONDITIONS = ("Rest", "Stim8hr", "Stim48hr")  # Marson's; kept for back-compat references
DE_STATS_CSV = MARSON.path


@dataclass(frozen=True)
class Perturbation:
    """One (gene, condition) knockdown measurement. Immutable by construction."""

    gene: str
    condition: str
    ensembl_id: str
    n_cells: float | None            # None if the screen has no cell-count column
    effect_size: float               # negative = knockdown reduces the readout
    significant: bool
    offtarget: bool
    n_downstream: int | None         # effect breadth; None if the screen has none
    fdr: float | None = None         # raw FDR; None if the screen reports none


@dataclass(frozen=True)
class GeneRecord:
    """A gene's measurements across all conditions it was tested in."""

    gene: str
    ensembl_id: str
    by_condition: dict[str, Perturbation]  # condition -> Perturbation (read-only by convention)


def _to_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _fdr(row: dict, schema: ScreenSchema) -> float | None:
    """The row's raw FDR, or None when the screen reports none.

    None is not 1.0. A missing FDR means 'this screen cannot speak to significance
    this way'; an FDR of 1.0 means 'reported, and not significant'. Collapsing the two
    would let a breadth-only screen plot as maximally significant."""
    if schema.fdr_col is None:
        return None
    return _to_float(row.get(schema.fdr_col), 1.0)


def _significant(row: dict, schema: ScreenSchema, fdr: float | None) -> bool:
    """Significance from an explicit bool column, or derived from FDR < fdr_max."""
    if schema.sig_col is not None:
        return row.get(schema.sig_col) == "True"
    if fdr is not None:
        return fdr < schema.fdr_max
    return False


def load_screen(schema: ScreenSchema = MARSON) -> tuple[GeneRecord, ...]:
    """Load a screen into immutable GeneRecords using its schema. Fails fast if the
    file is missing — better an actionable error than a silently empty shortlist.

    When a gene appears in multiple rows of one condition (e.g. Schmidt lists the
    same gene under one phenotype once), the first row wins; keeps loading total."""
    if not os.path.exists(schema.path):
        raise FileNotFoundError(
            f"Screen '{schema.name}' data not found at {schema.path}."
        )

    by_gene: dict[str, dict[str, Perturbation]] = {}
    ensembl: dict[str, str] = {}
    with open(schema.path, newline="") as fh:
        for row in csv.DictReader(fh):
            gene = (row.get(schema.gene_col) or "").strip()
            if not gene:
                continue
            cond = (row.get(schema.condition_col) or "all").strip() if schema.condition_col else "all"
            eid = (row.get(schema.ensembl_col) or "").strip() if schema.ensembl_col else ""
            breadth = (
                int(_to_float(row.get(schema.breadth_col)))
                if schema.breadth_col else None
            )
            fdr = _fdr(row, schema)
            pert = Perturbation(
                gene=gene,
                condition=cond,
                ensembl_id=eid,
                n_cells=_to_float(row.get(schema.ncells_col)) if schema.ncells_col else None,
                effect_size=_to_float(row.get(schema.effect_col)),
                significant=_significant(row, schema, fdr),
                offtarget=(row.get(schema.offtarget_col) == "True") if schema.offtarget_col else False,
                n_downstream=breadth,
                fdr=fdr,
            )
            by_gene.setdefault(gene, {}).setdefault(cond, pert)
            if eid:
                ensembl[gene] = eid

    return tuple(
        GeneRecord(gene=g, ensembl_id=ensembl.get(g, ""), by_condition=dict(conds))
        for g, conds in by_gene.items()
    )


def load_marson() -> tuple[GeneRecord, ...]:
    """The Marson screen — the default proof dataset.

    Named for what it loads. It takes no `path`: an earlier signature accepted one and
    silently ignored it, so a caller reading it would reasonably assume screen-switching
    worked. Any other screen goes through load_screen(schema)."""
    return load_screen(MARSON)


# Deprecated alias. Callers that are genuinely Marson-only (the agent layer, the Marson
# biology-fidelity evals) should say load_marson(); anything else takes a ScreenSchema.
load_perturbations = load_marson
