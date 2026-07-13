"""Concordance builder: cross the mRNA screen against the protein screen into a verdict.

This is Sera's keystone. For each (gene, cytokine, condition) it asks two yes/no
questions — is the knockdown a hit on the mRNA side? on the protein side? — and, crucially,
whether the two sides AGREE ON DIRECTION. It lands in one of FIVE boxes:

    replicated    both hit, SAME direction        (two independent assays agree — trust it)
    discordant    both hit, OPPOSITE direction     (mRNA/protein decoupling — a real result,
                                                     e.g. TSC1: lowers IL2 mRNA, raises protein)
    mrna_only     mRNA hit, protein not
    protein_only  protein hit, mRNA not
    neither       neither hits

Why a 5th box (and only one): direction conflict is possible ONLY when both screens fire.
mrna_only / protein_only / neither each have at most one firing screen, so cross-screen sign
cannot disagree. Calling a sign-flip "replicated" would be the exact overclaim the tool's
doctrine forbids — so we split "replicated" honestly rather than bury the conflict in a note.

Direction is canonicalized by BIOLOGY, not by a column's label: on a single axis where
`+` = "gene promotes the cytokine", the mRNA side reads `z < 0` (knockdown lowers mRNA →
promoter) and the protein side reads `lfc < 0` (knockdown depletes IL2-high cells → promoter).
See docs/planning/CONCORD_PLAN.md and the Fable design consult (2026-07-09).

Pure + deterministic: takes the two loaded sides, returns immutable records. No I/O here
beyond the small loaders; no Claude call. Thresholds come from config, never hardcoded.
"""
from __future__ import annotations

import csv
import os
from dataclasses import dataclass

import yaml

_CORE = os.path.dirname(os.path.abspath(__file__))
_PKG = os.path.dirname(_CORE)                 # sera/
_APP = os.path.dirname(_PKG)                  # application/
_CONFIG = os.path.join(_APP, "config.yaml")

# The five verdicts. Order matters only for display grouping.
REPLICATED, DISCORDANT, MRNA_ONLY, PROTEIN_ONLY, NEITHER = (
    "replicated", "discordant", "mrna_only", "protein_only", "neither")


def _f(value, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class Config:
    """The tunables the builder honors, loaded from config.yaml (never hardcoded)."""

    q_rna: float
    q_prot: float
    primary_cytokine: str
    schmidt_csv: str
    schmidt_phenotype: str

    @staticmethod
    def load(path: str = _CONFIG) -> "Config":
        with open(path) as fh:
            c = yaml.safe_load(fh)
        return Config(
            q_rna=float(c["thresholds"]["q_rna"]),
            q_prot=float(c["thresholds"]["q_prot"]),
            primary_cytokine=c["cytokines"]["primary"],
            schmidt_csv=os.path.join(_APP, c["data"]["schmidt_csv"]),
            schmidt_phenotype=c["data"]["schmidt_phenotype_il2"],
        )


@dataclass(frozen=True)
class ProteinEffect:
    """One gene's effect on a cytokine's PROTEIN, from Schmidt (MAGeCK, two one-sided tests).

    lfc is the single MAGeCK log-fold-change; q is min(neg_fdr, pos_fdr) — the more
    significant one-sided test. `promotes` is the canonical biology: True when knockdown
    LOWERS the cytokine (lfc < 0), False when it raises it, None when lfc is exactly 0
    (direction undefined — never guessed)."""

    gene: str
    lfc: float
    q: float                    # min(neg_fdr, pos_fdr)
    promotes: bool | None       # lfc < 0 → promotes; > 0 → brake; == 0 → None (undefined)


@dataclass(frozen=True)
class MrnaEffect:
    """One (gene, condition) effect on a cytokine's mRNA, from the Zhu parquet."""

    gene: str
    condition: str
    z: float
    q: float                 # DESeq2 adj_p_value
    promotes: bool | None    # z < 0 → promotes; > 0 → brake; == 0 → None (undefined)


@dataclass(frozen=True)
class Concordance:
    """The verdict for one (gene, cytokine, condition), with the numbers behind it."""

    gene: str
    cytokine: str
    condition: str
    verdict: str
    hit_rna: bool
    hit_prot: bool
    z_rna: float | None
    q_rna: float | None
    lfc_prot: float | None
    q_prot: float | None
    # canonical directions (None = that side is not a hit, so its direction is undefined)
    rna_promotes: bool | None
    prot_promotes: bool | None
    # Was each side actually ASSAYED? A gene absent from a screen's library was never measured —
    # which is NOT the same as measured-and-not-a-hit. Kept distinct so the UI can say "untested"
    # rather than present a confident negative for a gene the protein screen never saw. (The
    # mRNA screen covers the whole transcriptome, so rna_tested is effectively always True here;
    # the protein screen's library is smaller, so prot_tested varies.)
    rna_tested: bool = True
    prot_tested: bool = True


def load_protein(cfg: Config) -> dict[str, ProteinEffect]:
    """Load the Schmidt protein side for the configured cytokine phenotype.

    The CSV has a leading blank index column; the gene symbol is in `id`. MAGeCK reports one
    lfc and two one-sided FDRs (neg = KO depletes cytokine-high cells; pos = KO enriches). We
    take q = min(neg_fdr, pos_fdr) as the hit test and the lfc SIGN as the direction."""
    out: dict[str, ProteinEffect] = {}
    with open(cfg.schmidt_csv, newline="") as fh:
        for r in csv.DictReader(fh):
            if (r.get("phenotype") or "").strip() != cfg.schmidt_phenotype:
                continue
            gene = (r.get("id") or "").strip()
            if not gene:
                continue
            lfc = _f(r.get("neg|lfc"), 0.0)       # neg|lfc == pos|lfc in MAGeCK; one number
            q = min(_f(r.get("neg|fdr"), 1.0), _f(r.get("pos|fdr"), 1.0))
            out[gene] = ProteinEffect(gene=gene, lfc=lfc, q=round(q, 6),
                                      promotes=_direction(lfc))
    return out


def _direction(value: float) -> bool | None:
    """Canonical direction from a signed effect: True = promotes the cytokine (KD lowers it,
    value < 0), False = brake (KD raises it, value > 0), None = exactly zero (undefined, so a
    zero-but-significant effect is never silently mislabelled as a brake)."""
    if value < 0:
        return True
    if value > 0:
        return False
    return None


def classify(mrna: MrnaEffect | None, prot: ProteinEffect | None, cfg: Config,
             gene: str, cytokine: str, condition: str) -> Concordance:
    """The 2x2(+1) verdict for one (gene, cytokine, condition). Pure.

    A side is a "hit" when it was ASSAYED and its q is below the configured threshold. When both
    hit, the verdict depends on whether their canonical directions agree (replicated) or not
    (discordant). A side that was never assayed (prot is None — the gene isn't in that screen's
    library) is recorded as untested, NOT as a tested-negative: `prot_tested=False` lets the UI
    say "protein untested" rather than present a confident negative the screen never measured."""
    rna_tested = mrna is not None
    prot_tested = prot is not None
    hit_rna = rna_tested and mrna.q < cfg.q_rna
    hit_prot = prot_tested and prot.q < cfg.q_prot

    rna_dir = _direction(mrna.z) if hit_rna else None       # z < 0 promotes (KD lowers mRNA)
    prot_dir = prot.promotes if hit_prot else None          # ProteinEffect.promotes: None if lfc==0

    if hit_rna and hit_prot:
        # both hit: same non-None direction -> replicated; a genuine sign disagreement ->
        # discordant. If either direction is undefined (exactly-zero effect), fall back to
        # 'replicated' only when they match, else discordant — never guess a sign.
        verdict = REPLICATED if (rna_dir is not None and rna_dir == prot_dir) else DISCORDANT
    elif hit_rna:
        verdict = MRNA_ONLY
    elif hit_prot:
        verdict = PROTEIN_ONLY
    else:
        verdict = NEITHER

    return Concordance(
        gene=gene, cytokine=cytokine, condition=condition,
        verdict=verdict, hit_rna=hit_rna, hit_prot=hit_prot,
        z_rna=round(mrna.z, 3) if mrna else None,
        q_rna=round(mrna.q, 6) if mrna else None,
        lfc_prot=round(prot.lfc, 3) if prot else None,
        q_prot=round(prot.q, 6) if prot else None,
        rna_promotes=rna_dir, prot_promotes=prot_dir,
        rna_tested=rna_tested, prot_tested=prot_tested,
    )
