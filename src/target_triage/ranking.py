"""Pure ranking logic: score a gene's knockdown impact across conditions.

No I/O, no mutation — takes GeneRecords, returns new immutable ScoredGene tuples.
The druggability/disease overlay lives in the agent layer (it needs live API data);
this module answers only "how broad and real is this knockdown's effect?".
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from .data import GeneRecord, Perturbation


@dataclass(frozen=True)
class ScoredGene:
    gene: str
    ensembl_id: str
    impact: float               # best per-condition impact
    context_specificity: float  # 0 = uniform across conditions, ~1 = one condition only
    best_condition: str


def condition_impact(pert: Perturbation) -> float:
    """Effect breadth, gently weighted by knockdown strength.

    log1p tames the huge dynamic range in downstream-gene counts (5 vs 1000+),
    so a gene with a moderate-but-real effect isn't buried by one outlier.
    """
    breadth = math.log1p(max(pert.n_downstream, 0))
    strength = 1.0 + 0.05 * abs(pert.effect_size)
    return breadth * strength


def score_gene(record: GeneRecord) -> ScoredGene:
    """Collapse a gene's conditions into one impact + a context-specificity flag."""
    impacts = {
        cond: condition_impact(pert) for cond, pert in record.by_condition.items()
    }
    best_condition = max(impacts, key=impacts.get)
    best = impacts[best_condition]
    lo, hi = min(impacts.values()), max(impacts.values())
    context = (hi - lo) / hi if hi > 0 else 0.0
    return ScoredGene(
        gene=record.gene,
        ensembl_id=record.ensembl_id,
        impact=best,
        context_specificity=context,
        best_condition=best_condition,
    )


def rank_by_impact(records: tuple[GeneRecord, ...]) -> tuple[ScoredGene, ...]:
    """Rank all genes by raw knockdown impact (descending). Pure."""
    scored = [score_gene(r) for r in records]
    scored.sort(key=lambda s: s.impact, reverse=True)
    return tuple(scored)


def significant_records(records: tuple[GeneRecord, ...]) -> tuple[GeneRecord, ...]:
    """Keep only genes with at least one significant, non-off-target knockdown."""
    kept = tuple(
        r
        for r in records
        if any(
            p.significant and not p.offtarget for p in r.by_condition.values()
        )
    )
    return kept
