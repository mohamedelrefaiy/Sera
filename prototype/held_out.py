"""
Held-out screen check (verifier Tier C3) — the "predicted blind, independent screen agrees" beat.

PRE-KICKOFF PROTOTYPE. Not the hackathon submission; explores feasibility on public data
before the build officially starts at kickoff (Tue Jul 7, 12pm ET). See prototype/README.md.

Two INDEPENDENT CD4+ T-cell CRISPR screens the Marson Perturb-seq pipeline does NOT use as input:
  - Schmidt & Steinhart 2022 (Science) — CRISPRi/a, readouts: CD4+ IL2, CD8+ IFNG, etc.
  - Freimer 2022 — arrayed screens, readouts: IL2RA, CTLA4, etc.
Both are MAGeCK output: `id` = gene, `neg|fdr` (KO reduces readout) and `pos|fdr` (KO boosts),
and a phenotype/screen label. A gene is a HELD-OUT HIT if it is significant (FDR < threshold)
in either direction, in either screen.

This is a BONUS signal, not a gate: a gene the Marson screen flags that an independent screen
ALSO flags is strong corroboration; absence is not disqualifying (the screens cover different genes).
"""
import csv, os

DATA = os.path.join(os.path.dirname(__file__), "..", "data", "external_screens")
SCHMIDT = os.path.join(DATA, "Schmidt2022_CRISPRi_gene_phenotypes.csv")
FREIMER = os.path.join(DATA, "Freimer2022_Screen.csv")

FDR = 0.10               # significance floor for calling a held-out hit


def _f(x, d=1.0):
    try: return float(x)
    except (TypeError, ValueError): return d


def _load_screen(path, id_col, label_col):
    """Return {gene: [(readout, direction, fdr), ...]} for significant genes."""
    hits = {}
    with open(path) as fh:
        for r in csv.DictReader(fh):
            gene = (r.get(id_col) or "").strip()
            if not gene:
                continue
            neg, pos = _f(r.get("neg|fdr")), _f(r.get("pos|fdr"))
            best = min(neg, pos)
            if best < FDR:
                # neg = KO REDUCES the readout (positive regulator);
                # pos = KO BOOSTS the readout (negative regulator / brake)
                direction = "KO_reduces" if neg <= pos else "KO_boosts"
                hits.setdefault(gene, []).append(
                    (r.get(label_col, "?"), direction, round(best, 4)))
    return hits


# Freimer's id column carries a BOM ("﻿id"); handle both.
def load_held_out():
    schmidt = _load_screen(SCHMIDT, "id", "phenotype")
    freimer = _load_screen(FREIMER, "﻿id", "screen")
    return {"Schmidt2022": schmidt, "Freimer2022": freimer}


def check_held_out(gene, screens):
    """C3 [BONUS]: is this gene an independent hit? Returns a verifier-style result."""
    found = []
    for name, hits in screens.items():
        if gene in hits:
            for readout, direction, fdr in hits[gene]:
                found.append(f"{name}:{readout}({direction},FDR={fdr})")
    return {"check": "held_out_screen", "gate": False, "bonus": True,
            "pass": len(found) > 0, "value": len(found),
            "why": ("independent hit — " + "; ".join(found)) if found
                   else "not a significant hit in Schmidt2022 or Freimer2022 (not disqualifying)"}


if __name__ == "__main__":
    screens = load_held_out()
    print(f"Schmidt2022 significant genes: {len(screens['Schmidt2022'])} | "
          f"Freimer2022: {len(screens['Freimer2022'])}\n")
    print("HELD-OUT SCREEN CHECK (C3) — does an independent screen corroborate the pick?\n")
    for g in ["CBLB", "PTPN2", "TNFAIP3", "RASA2", "CD5", "DGKA", "PTPN22", "A1BG"]:
        res = check_held_out(g, screens)
        mark = "CORROBORATED" if res["pass"] else "no independent hit"
        print(f"  {g:8s} [{mark:18s}] {res['why']}")
