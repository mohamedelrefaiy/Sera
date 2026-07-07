"""
Target Triage — adversarial VERIFIER (Upgrade 2 design + runnable CSV-tier prototype).
SCRATCHPAD, not committed project code.

The verifier's job: for each candidate the ranker proposes, TRY TO REFUTE IT.
Each check is an adversarial hypothesis with a COMPUTED verdict (pass/fail + a number),
not an LLM opinion. A candidate is PROMOTED only if it survives the gating checks.
Rejected candidates are kept and shown — that's how the teeth are provable.

DESIGN — checks are TIERED to the data actually loaded:

  TIER A — computable from the 4.6 MB DE_stats summary CSV (implemented below):
    A1 real_knockdown     : is there a significant on-target KD at all?         [GATE]
    A2 not_offtarget      : is the effect free of the off-target confound flag? [GATE]
    A3 enough_cells       : n_cells_target above a floor (not a low-power fluke) [GATE]
    A4 cross_condition    : does the effect reproduce across >1 condition, or is
                            it a single-condition blip? (consistency, not proof) [SCORE]

  TIER B — REAL, from small precomputed tables in the emdann GitHub repo (implemented):
    B1 donor_robustness   : mean cross-donor correlation of the DE effect. A real
                            regulator agrees across donors; a single-donor artifact
                            has low/negative correlation.  source: DE_donor_robustness_
                            correlation_summary.csv (donor_correlation_mean/_min) [GATE]
    B2 cross_guide        : do the 2 guides for this gene agree? source:
                            DE_by_guide.correlation_results.csv (correlation)      [GATE]
  (These replace the 16.8 GB h5ad — the repo publishes them per gene x condition.)

  TIER C — external, needs Open Targets + a 2nd screen (spec only; opentargets.py has data):
    C1 disease_is_immune  : is the top disease genuinely immune, not a coincidental
                            rare-syndrome association? (already filtered in overlay)  [GATE]
    C2 druggable_handle   : does a real small-molecule handle exist?                  [SCORE]
    C3 held_out_screen    : is the gene an independent hit (BioGRID-ORCS: Shifrut/
                            Schmidt/Belk)? THE held-out prediction beat.              [BONUS]

A candidate's verdict = REJECT if any GATE fails; else PROMOTE with a confidence
built from the SCORE/BONUS checks. The verifier reports every check + its number.
"""
import csv, math, collections, os
from held_out import load_held_out, check_held_out

_D = os.path.dirname(__file__)
CSV = os.path.join(_D, "..", "data", "marson_perturbseq", "DE_stats.suppl_table.csv")
DONOR_CSV = os.path.join(_D, "..", "data", "robustness", "DE_donor_robustness_correlation_summary.csv")
GUIDE_CSV = os.path.join(_D, "..", "data", "robustness", "DE_by_guide_correlation_results.csv")
CONDITIONS = ["Rest", "Stim8hr", "Stim48hr"]

# --- thresholds (a judge can see and argue every one) ---
MIN_CELLS = 100          # A3: below this, on-target effect is low-powered
MIN_KD_EFFECT = 2.0      # A1: |ontarget_effect_size| floor for a "real" knockdown
MIN_DONOR_CORR = 0.10    # B1: mean cross-donor correlation floor (below = donor artifact)
MIN_GUIDE_CORR = 0.10    # B2: cross-guide correlation floor (below = guides disagree)


def f(x, d=0.0):
    try: return float(x)
    except (TypeError, ValueError): return d


def load_by_gene(path):
    by = collections.defaultdict(dict)
    for r in csv.DictReader(open(path)):
        by[r["target_contrast_gene_name"]][r["culture_condition"]] = r
    return by


def load_robustness():
    """Load the precomputed cross-donor and cross-guide correlation tables.
    Returns (donor[gene][cond], guide[gene][cond]) of correlation floats."""
    donor = collections.defaultdict(dict)
    for r in csv.DictReader(open(DONOR_CSV)):
        donor[r["target_name"]][r["condition"]] = r
    guide = collections.defaultdict(dict)
    for r in csv.DictReader(open(GUIDE_CSV)):
        guide[r["target"]][r["culture_condition"]] = r
    return donor, guide


# ---------------- TIER A checks (implemented, run on the CSV) ----------------

def check_real_knockdown(rows):
    """A1 [GATE]: at least one condition has a significant on-target KD."""
    sig = [r for r in rows.values()
           if r["ontarget_significant"] == "True"
           and abs(f(r["ontarget_effect_size"])) >= MIN_KD_EFFECT]
    best = max((abs(f(r["ontarget_effect_size"])) for r in sig), default=0.0)
    return {"check": "real_knockdown", "gate": True,
            "pass": len(sig) > 0, "value": round(best, 1),
            "why": f"{len(sig)}/{len(rows)} conditions have a significant KD (|eff|>={MIN_KD_EFFECT})"}


def check_not_offtarget(rows):
    """A2 [GATE]: the significant conditions are not off-target-flagged."""
    flagged = [c for c, r in rows.items()
               if r["ontarget_significant"] == "True" and r["offtarget_flag"] == "True"]
    return {"check": "not_offtarget", "gate": True,
            "pass": len(flagged) == 0, "value": len(flagged),
            "why": f"{len(flagged)} significant condition(s) carry the off-target flag"}


def check_enough_cells(rows):
    """A3 [GATE]: the significant effects are backed by enough cells (not low-power)."""
    ncells = [f(r["n_cells_target"]) for r in rows.values()
              if r["ontarget_significant"] == "True"]
    peak = max(ncells, default=0.0)
    return {"check": "enough_cells", "gate": True,
            "pass": peak >= MIN_CELLS, "value": int(peak),
            "why": f"peak n_cells among significant conditions = {int(peak)} (floor {MIN_CELLS})"}


def check_cross_condition(rows):
    """A4 [SCORE]: does the effect reproduce across conditions, or is it a 1-off blip?
    Score = fraction of conditions that are significant with real breadth."""
    reproduced = [c for c, r in rows.items()
                  if r["ontarget_significant"] == "True" and f(r["n_downstream"]) >= 5]
    frac = len(reproduced) / len(CONDITIONS)
    return {"check": "cross_condition", "gate": False,
            "pass": frac >= 1/3, "value": round(frac, 2),
            "why": f"reproduces in {len(reproduced)}/{len(CONDITIONS)} conditions "
                   f"({', '.join(reproduced) or 'none'})"}


TIER_A = [check_real_knockdown, check_not_offtarget, check_enough_cells, check_cross_condition]


# ---------------- TIER B checks (REAL, from the repo's robustness tables) ----------------

def check_donor_robustness(gene, donor):
    """B1 [GATE]: does the effect agree ACROSS DONORS? Refutes 'single-donor artifact'.
    Uses the best (max) mean cross-donor correlation across this gene's conditions."""
    rows = donor.get(gene, {})
    best = max((f(r["donor_correlation_mean"], -1) for r in rows.values()), default=None)
    if best is None:
        return {"check": "donor_robustness", "gate": True, "pass": True,   # unknown -> don't block
                "value": "NA", "why": "no cross-donor data for this gene (not gated)"}
    return {"check": "donor_robustness", "gate": True,
            "pass": best >= MIN_DONOR_CORR, "value": round(best, 2),
            "why": f"best cross-donor corr = {best:.2f} (floor {MIN_DONOR_CORR}); "
                   f"low/neg = donor-driven artifact"}


def check_cross_guide(gene, guide):
    """B2 [GATE]: do the two guides for this gene AGREE? Refutes 'guide off-target/artifact'.
    Uses the best cross-guide correlation across conditions."""
    rows = guide.get(gene, {})
    vals = [f(r["correlation"], -1) for r in rows.values() if r.get("correlation") not in (None, "")]
    if not vals:
        return {"check": "cross_guide", "gate": True, "pass": True,
                "value": "NA", "why": "no cross-guide data for this gene (not gated)"}
    best = max(vals)
    return {"check": "cross_guide", "gate": True,
            "pass": best >= MIN_GUIDE_CORR, "value": round(best, 2),
            "why": f"best cross-guide corr = {best:.2f} (floor {MIN_GUIDE_CORR}); "
                   f"low = the two guides disagree"}


def verify(gene, by_gene, donor=None, guide=None, screens=None):
    """Run Tier-A (+ Tier-B robustness + Tier-C3 held-out if given); verdict + per-check results."""
    rows = by_gene.get(gene)
    if not rows:
        return {"gene": gene, "verdict": "REJECT", "reason": "not in screen", "checks": []}

    results = [chk(rows) for chk in TIER_A]
    if donor is not None and guide is not None:
        results.append(check_donor_robustness(gene, donor))
        results.append(check_cross_guide(gene, guide))
    if screens is not None:
        results.append(check_held_out(gene, screens))          # C3 bonus

    gates = [r for r in results if r.get("gate")]
    scores = [r for r in results if not r.get("gate")]

    failed_gate = [r for r in gates if not r["pass"]]
    if failed_gate:
        verdict = "REJECT"
        reason = "failed gate: " + ", ".join(r["check"] for r in failed_gate)
    else:
        corroborated = any(r.get("bonus") and r["pass"] for r in results)
        cc = next((r["value"] for r in scores if r["check"] == "cross_condition"), "NA")
        if corroborated and cc and cc != "NA" and cc > 0:
            verdict = "PROMOTE (corroborated)"
        elif all(r["pass"] for r in scores if not r.get("bonus")):
            verdict = "PROMOTE"
        else:
            verdict = "PROMOTE (weak)"
        reason = f"passed all gates; cross-condition={cc}"
    return {"gene": gene, "verdict": verdict, "reason": reason, "checks": results}


if __name__ == "__main__":
    by = load_by_gene(CSV)
    donor, guide = load_robustness()
    screens = load_held_out()

    # Run the verifier on a mix: our heroes, a control, and some genes that SHOULD fail.
    panel = ["CBLB", "PTPN2", "TNFAIP3", "RASA2", "CD5",
             "DGKA", "PTPN22", "A1BG"]

    print("VERIFIER — Tier A (DE summary) + Tier B (donor/guide robustness) "
          "+ Tier C3 (held-out screens)\n")
    for g in panel:
        v = verify(g, by, donor, guide, screens)
        print(f"{g:8s}  ->  {v['verdict']:<22}  ({v['reason']})")
        for c in v["checks"]:
            mark = "PASS" if c["pass"] else "FAIL"
            kind = "gate " if c.get("gate") else ("bonus" if c.get("bonus") else "score")
            print(f"     [{mark}] {kind} {c['check']:16s} val={str(c['value']):>6}  {c['why']}")
        print()
