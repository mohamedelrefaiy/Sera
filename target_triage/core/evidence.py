"""Evidence boundary: load the precomputed robustness + independent-screen data.

These small public tables (from the emdann analysis repo + two published CRISPR
screens) let the verifier compute REAL cross-donor / cross-guide robustness and
held-out corroboration without touching the 16.8 GB per-gene h5ad. Loaded once
into immutable maps; the verifier reads them, never mutates.
"""
from __future__ import annotations

import csv
import os
from dataclasses import dataclass, field

# evidence.py lives at target_triage/core/evidence.py; data/ is bundled inside the
# package at target_triage/data/ — two dirname() hops (core -> target_triage).
_PKG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DATA = os.path.join(_PKG, "data")
DONOR_CSV = os.path.join(_DATA, "robustness", "DE_donor_robustness_correlation_summary.csv")
GUIDE_CSV = os.path.join(_DATA, "robustness", "DE_by_guide_correlation_results.csv")
SCHMIDT_CSV = os.path.join(_DATA, "external_screens", "Schmidt2022_CRISPRi_gene_phenotypes.csv")
FREIMER_CSV = os.path.join(_DATA, "external_screens", "Freimer2022_Screen.csv")

HELD_OUT_FDR = 0.10  # significance floor for calling an independent-screen hit


@dataclass(frozen=True)
class ScreenHit:
    screen: str
    readout: str
    direction: str   # "KO_reduces" (positive regulator) or "KO_boosts" (brake)
    fdr: float


@dataclass(frozen=True)
class Evidence:
    """All external evidence, keyed by gene symbol. Read-only."""

    donor_corr: dict[str, float] = field(default_factory=dict)   # gene -> best mean cross-donor corr
    guide_corr: dict[str, float] = field(default_factory=dict)   # gene -> best cross-guide corr
    screen_hits: dict[str, tuple[ScreenHit, ...]] = field(default_factory=dict)


def _f(value: str, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _load_donor() -> dict[str, float]:
    best: dict[str, float] = {}
    with open(DONOR_CSV, newline="") as fh:
        for r in csv.DictReader(fh):
            gene = r["target_name"]
            corr = _f(r["donor_correlation_mean"], -1.0)
            if gene not in best or corr > best[gene]:
                best[gene] = corr
    return best


def _load_guide() -> dict[str, float]:
    best: dict[str, float] = {}
    with open(GUIDE_CSV, newline="") as fh:
        for r in csv.DictReader(fh):
            gene = r["target"]
            corr = _f(r.get("correlation", ""), -1.0)
            if gene not in best or corr > best[gene]:
                best[gene] = corr
    return best


def _load_screen(path: str, name: str, id_col: str, label_col: str) -> dict[str, list[ScreenHit]]:
    hits: dict[str, list[ScreenHit]] = {}
    with open(path, newline="") as fh:
        for r in csv.DictReader(fh):
            gene = (r.get(id_col) or "").strip()
            if not gene:
                continue
            neg, pos = _f(r.get("neg|fdr", ""), 1.0), _f(r.get("pos|fdr", ""), 1.0)
            best = min(neg, pos)
            if best < HELD_OUT_FDR:
                direction = "KO_reduces" if neg <= pos else "KO_boosts"
                hits.setdefault(gene, []).append(
                    ScreenHit(name, r.get(label_col, "?"), direction, round(best, 4))
                )
    return hits


def load_evidence() -> Evidence:
    donor = _load_donor()
    guide = _load_guide()
    # Freimer's id column carries a BOM; the loader keys on the literal header.
    schmidt = _load_screen(SCHMIDT_CSV, "Schmidt2022", "id", "phenotype")
    freimer = _load_screen(FREIMER_CSV, "Freimer2022", "﻿id", "screen")

    merged: dict[str, tuple[ScreenHit, ...]] = {}
    for src in (schmidt, freimer):
        for gene, lst in src.items():
            merged[gene] = merged.get(gene, ()) + tuple(lst)

    return Evidence(donor_corr=donor, guide_corr=guide, screen_hits=merged)
