"""Row-producers: real screen files -> canonical rows. Pure, deterministic, no model.

This module is where "two hardcoded loaders" becomes "N screens, one schema". Each adapter
knows one file's dialect and nothing else; all of them emit `CanonicalRow`. The concordance
core never learns a column name again.

A dialect is more than column names. The three bundled screens differ in every way a screen can
differ, which is exactly why they are a fair test of the abstraction:

    Schmidt   MAGeCK CRISPRi, protein (FACS). Readout axis is `phenotype` = "CD4+ IL2" -- cell
              type AND cytokine fused into one string, so it must be DECOMPOSED. No condition
              axis at all (a single sort), so condition is a stated constant.
              Sign: on-target IL2-on-IL2 lfc = -2.61. Matches the convention.

    Freimer   MAGeCK CRISPRi, protein (FACS). Readout axis is `screen` = "IL2" -- a bare cytokine,
              no cell type. UTF-8 BOM on the `id` header. Carries `Non-Targeting` control rows
              that are not genes. Sign: on-target rows are all POSITIVE (+1.80 .. +3.79) -- the
              screen is INVERTED relative to Schmidt, because it sorted marker-LOW rather than
              marker-high. Hazard 3 catches this.

    Zhu       Perturb-seq, mRNA. Already long-form (gene x cytokine x condition). Speaks DESeq2
              adj-p, not MAGeCK FDR. Its on-target rows were ALREADY dropped upstream by
              01_extract_cytokines.py, so it cannot self-calibrate -- see `zhu_rows`.

Adapters do not correct signs and do not drop on-target rows. They report what the file says.
Correction and exclusion are the caller's explicit, logged acts (ingest.py), so the manifest can
record that they happened.

Read-only: nothing here writes a file.
"""
from __future__ import annotations

import csv
import os
from typing import Iterator, Sequence

from ..core.canonical import CanonicalRow, Modality, SignifRegime, derive_ontarget

_PKG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCREENS = os.path.join(_PKG, "data", "external_screens")

SCHMIDT_CSV = os.path.join(_SCREENS, "Schmidt2022_CRISPRi_gene_phenotypes.csv")
FREIMER_CSV = os.path.join(_SCREENS, "Freimer2022_Screen.csv")

# Rows whose "gene" is a library control, not a perturbed gene. They carry no regulatory claim and
# would pollute both the sign calibration and the verdict table. Dropped by name, deliberately.
NON_GENE_IDS = frozenset({"Non-Targeting", "non-targeting", "NonTargeting", "safe-harbor"})


def _f(value, default: float) -> float:
    """Parse a float, falling back rather than raising. A malformed cell becomes the stated default
    (1.0 for an FDR = 'not significant'; 0.0 for an effect = 'no effect'), which is the conservative
    direction: it can only ever make a row LESS of a hit, never more."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _mageck_effect_and_q(rec: dict) -> tuple[float, float]:
    """The (effect, significance) pair from one MAGeCK row, in canonical terms.

    MAGeCK runs two one-sided tests and reports ONE log-fold-change (verified: `neg|lfc` ==
    `pos|lfc` in every row of both bundled files). Significance is the more significant of the two
    one-sided FDRs -- min(neg|fdr, pos|fdr) -- because a row is a hit if it moves the readout in
    EITHER direction. Which direction is then read off the lfc SIGN, never off which FDR won.
    """
    lfc = _f(rec.get("neg|lfc"), 0.0)
    q = min(_f(rec.get("neg|fdr"), 1.0), _f(rec.get("pos|fdr"), 1.0))
    return lfc, q


def _read_csv(path: str) -> Iterator[dict]:
    """Read a screen CSV, stripping a UTF-8 BOM if present. Freimer has one on its `id` header,
    which would otherwise make the gene column literally named '\\ufeffid' and silently unmappable.
    `utf-8-sig` consumes the BOM when present and is a no-op when absent."""
    with open(path, newline="", encoding="utf-8-sig") as fh:
        yield from csv.DictReader(fh)


# ---------------------------------------------------------------------------------------
# Schmidt 2022 -- protein (FACS), phenotype = "<cell type> <cytokine>"
# ---------------------------------------------------------------------------------------


def split_phenotype(phenotype: str) -> tuple[str, str]:
    """"CD4+ IL2" -> ("CD4", "IL2"). Decompose a fused readout label into its two real axes.

    Sera reconciles PER CYTOKINE, so a cytokine fused to a cell type is unusable: comparing
    Schmidt's "CD4+ IL2" against Zhu's "IL2" as opaque strings would find no overlap at all, and
    comparing a CD8+ readout against a CD4+ one would be a cross-cell-type claim silently dressed
    up as a replicate. Splitting makes both the match and the mismatch explicit.

    Raises on an unparseable label rather than guessing -- a readout axis we cannot decompose is a
    screen we cannot place.
    """
    parts = phenotype.strip().split()
    if len(parts) != 2:
        raise ValueError(f"cannot decompose phenotype {phenotype!r} into '<cell_type> <cytokine>'; "
                         "refusing to guess which token is which")
    cell_type, cytokine = parts
    return cell_type.rstrip("+"), cytokine


def schmidt_rows(phenotype: str = "CD4+ IL2", *, path: str = SCHMIDT_CSV,
                 screen_id: str = "schmidt2022") -> tuple[CanonicalRow, ...]:
    """Schmidt's protein screen for one phenotype, as canonical rows.

    Schmidt is a single FACS sort with NO activation-condition axis. Rather than invent one or
    leave the axis empty (which the validator would reject), we state the constant: every row is
    `condition="Stimulated"`, because the sort was performed on stimulated cells. The core then
    joins this constant protein effect against each of the mRNA screen's three conditions -- which
    is correct, expected, and exactly what pipeline/02_build_concordance.py already does today.
    """
    cell_type, cytokine = split_phenotype(phenotype)
    rows: list[CanonicalRow] = []
    for rec in _read_csv(path):
        if (rec.get("phenotype") or "").strip() != phenotype:
            continue
        gene = (rec.get("id") or "").strip()
        if not gene or gene in NON_GENE_IDS:
            continue
        lfc, q = _mageck_effect_and_q(rec)
        rows.append(derive_ontarget(CanonicalRow(
            screen_id=screen_id, gene=gene, cytokine=cytokine, condition="Stimulated",
            cell_type=cell_type, modality=Modality.PROTEIN,
            effect_size=lfc, signif_value=round(q, 6),
            signif_regime=SignifRegime.BENJAMINI_FDR,
        )))
    return tuple(rows)


# ---------------------------------------------------------------------------------------
# Freimer 2022 -- protein (FACS), screen = bare cytokine, effect column INVERTED
# ---------------------------------------------------------------------------------------


def freimer_rows(cytokine: str = "IL2", *, path: str = FREIMER_CSV,
                 screen_id: str = "freimer2022",
                 cell_type: str = "CD4") -> tuple[CanonicalRow, ...]:
    """Freimer's protein screen for one cytokine readout, as canonical rows -- UNCORRECTED.

    Two dialect quirks the adapter handles, and one it deliberately does not.

    Handles: the BOM'd `id` header (via `_read_csv`), and the `Non-Targeting` control rows, which
    are library controls rather than perturbed genes and are dropped by name.

    Does NOT handle: the sign. Freimer's on-target rows (IL2-KD on IL2, etc.) come out POSITIVE,
    meaning its effect column runs opposite to the canonical convention. This adapter reports the
    file faithfully and lets Hazard 3 (`core.canonical.check_sign_convention`) detect the inversion
    and `apply_sign_correction` fix it -- a logged, auditable act. An adapter that quietly negated
    the column would make the correction invisible to the provenance log, which is the one thing
    this architecture exists to provide.

    `cell_type` is a stated parameter, not read from the file: Freimer's CSV records no cell type.
    Defaulting to CD4 is a claim about the experiment, so it lives in the signature where a
    reviewer can see and change it -- never inferred from the data.
    """
    rows: list[CanonicalRow] = []
    for rec in _read_csv(path):
        if (rec.get("screen") or "").strip() != cytokine:
            continue
        gene = (rec.get("id") or "").strip()
        if not gene or gene in NON_GENE_IDS:
            continue
        lfc, q = _mageck_effect_and_q(rec)
        rows.append(derive_ontarget(CanonicalRow(
            screen_id=screen_id, gene=gene, cytokine=cytokine, condition="Stimulated",
            cell_type=cell_type, modality=Modality.PROTEIN,
            effect_size=lfc, signif_value=round(q, 6),
            signif_regime=SignifRegime.BENJAMINI_FDR,
        )))
    return tuple(rows)


# ---------------------------------------------------------------------------------------
# Zhu 2025 -- mRNA (Perturb-seq), already long-form, DESeq2 adj-p
# ---------------------------------------------------------------------------------------


def zhu_rows(mrna_records: Sequence[dict], *, cytokine: str | None = None,
             screen_id: str = "zhu2025", cell_type: str = "CD4") -> tuple[CanonicalRow, ...]:
    """The mRNA side as canonical rows, from the already-extracted long-form records.

    `mrna_records` are dicts with keys gene, cytokine, condition, z_rna, q_rna -- the schema of
    artifacts/cytokine_mrna_effects.parquet. Takes records rather than a path so this module stays
    pure and pandas-free; the caller reads the parquet.

    IMPORTANT -- this screen CANNOT self-calibrate its sign. `01_extract_cytokines.py` applies
    Hazard 1 during extraction, so the artifact contains ZERO on-target rows and
    `check_sign_convention` returns `confident=False` on it. That is the honest answer, not a bug:
    the calibration standard was consumed upstream. The convention for this screen is instead
    established at the source (a DESeq2 z-score where negative = knockdown lowers the transcript,
    matching SIGN_CONVENTION) and corroborated by the existing sanity gate's positive controls --
    ITK/BCL10/VAV1 come out `replicated`, which they could not if this screen's sign were flipped.

    `ingest.py` therefore treats Zhu as a PRE-CALIBRATED screen and records that in the manifest,
    rather than pretending a check ran that could not run.
    """
    rows: list[CanonicalRow] = []
    for rec in mrna_records:
        if cytokine is not None and rec["cytokine"] != cytokine:
            continue
        gene = str(rec["gene"]).strip()
        if not gene or gene in NON_GENE_IDS:
            continue
        rows.append(derive_ontarget(CanonicalRow(
            screen_id=screen_id, gene=gene, cytokine=str(rec["cytokine"]),
            condition=str(rec["condition"]), cell_type=cell_type, modality=Modality.RNA,
            effect_size=float(rec["z_rna"]), signif_value=float(rec["q_rna"]),
            signif_regime=SignifRegime.DESEQ2_ADJP,
        )))
    return tuple(rows)


# The registry an ingestion run dispatches through once a mapping has been validated. Adding an
# Nth screen means adding one adapter here -- not touching the core.
ADAPTERS = {
    "schmidt2022": schmidt_rows,
    "freimer2022": freimer_rows,
    "zhu2025": zhu_rows,
}
