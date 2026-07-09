"""Phase 1 · step 02 — build the concordance artifact (the core asset).

Crosses the two sides into the 2x2(+1) verdict for every (gene, cytokine, condition):
  - mRNA side: cytokine_mrna_effects.parquet (from 01_extract_cytokines.py)
  - protein side: Schmidt CD4+ IL2, loaded via core.concordance.load_protein

Writes concordance.parquet with the record schema in docs/planning/CONCORD_PLAN.md §3.
The protein side has NO condition axis (Schmidt is a single FACS readout), so a gene's
protein effect is constant across the three mRNA conditions — that is expected and correct.

Deterministic, no API/Claude. Run:  python pipeline/02_build_concordance.py
"""
from __future__ import annotations

import os
import sys

import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
_APP = os.path.dirname(_HERE)
# Make the target_triage package importable when run from application/ (pipeline/ is a
# sibling of the package, not inside it), without requiring an editable install.
if _APP not in sys.path:
    sys.path.insert(0, _APP)

from target_triage.core.concordance import (  # noqa: E402 — path set above
    Config, MrnaEffect, classify, load_protein)

_MRNA = os.path.join(_APP, "target_triage", "data", "artifacts", "cytokine_mrna_effects.parquet")
_OUT = os.path.join(_APP, "target_triage", "data", "artifacts", "concordance.parquet")


def build(cytokine: str | None = None, progress=print) -> pd.DataFrame:
    """Build the concordance table for one cytokine (default: config primary = IL2)."""
    cfg = Config.load()
    cyto = cytokine or cfg.primary_cytokine

    if not os.path.exists(_MRNA):
        raise FileNotFoundError(
            f"{_MRNA} not found — run `python pipeline/01_extract_cytokines.py` first.")

    mrna_df = pd.read_parquet(_MRNA)
    mrna_df = mrna_df[mrna_df["cytokine"] == cyto]
    if mrna_df.empty:
        raise ValueError(f"no mRNA rows for cytokine {cyto} in {_MRNA}")

    protein = load_protein(cfg)          # {gene: ProteinEffect} for the configured phenotype
    progress(f"[build] cytokine={cyto}  mRNA rows={len(mrna_df):,}  protein genes={len(protein):,}")

    rows = []
    for rec in mrna_df.itertuples(index=False):
        m = MrnaEffect(gene=rec.gene, condition=rec.condition, z=rec.z_rna, q=rec.q_rna,
                       promotes=rec.z_rna < 0)
        p = protein.get(rec.gene)        # None if the gene wasn't in Schmidt's library
        c = classify(m, p, cfg, gene=rec.gene, cytokine=cyto, condition=rec.condition)
        rows.append(c.__dict__)

    df = pd.DataFrame(rows)
    counts = df["verdict"].value_counts().to_dict()
    progress(f"[build] {len(df):,} rows  verdicts={counts}")
    return df


def main() -> int:
    df = build()
    os.makedirs(os.path.dirname(_OUT), exist_ok=True)
    df.to_parquet(_OUT, index=False)
    print(f"[write] {_OUT}  ({len(df):,} rows)")

    # Quick acceptance echo: the canonical genes at IL2/Stim48hr (brief §3.6).
    at = df[df["condition"] == "Stim48hr"].set_index("gene")
    print("\n[check] IL2 / Stim48hr verdicts for canonical genes:")
    for g in ["ITK", "BCL10", "TSC1", "VAV1", "LCP2"]:
        if g in at.index:
            r = at.loc[g]
            print(f"  {g:6s} {r['verdict']:12s} "
                  f"z_rna={r['z_rna']} q_rna={r['q_rna']} lfc_prot={r['lfc_prot']} q_prot={r['q_prot']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
