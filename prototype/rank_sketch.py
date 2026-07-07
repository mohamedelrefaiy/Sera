"""
Target Triage — v1 ranking sketch (SCRATCHPAD, not committed project code).

Purpose: turn the 4.6 MB DE_stats summary CSV into a ranked shortlist of
druggable, disease-linked, high-impact T-cell regulators.

The pipeline, in plain terms:
  1. GATE   — keep only real, significant on-target knockdowns (drop noise like A1BG).
  2. SCORE  — per (gene, condition): impact = effect breadth, weighted by KD strength.
  3. FOLD   — collapse a gene's 3 conditions into one score + a context-specificity flag.
  4. OVERLAY— reweight by LIVE Open Targets druggability + disease-genetics (the NOVELTY step).
  5. RANK   — sort. Show raw-impact rank vs actionable rank side by side.
  6. VERIFY — run the adversarial verifier on the surfaced shortlist so each candidate
              a scientist sees carries a PROMOTE/REJECT verdict + the evidence behind it.

Everything here is transparent arithmetic — a judge can read it and agree.
The overlay now uses REAL Open Targets scores (see opentargets.py), not a stub.
The verdict comes from verifier.py (real computed checks), not an LLM opinion.
"""
import csv, math, collections, os
import opentargets as ot
import verifier as vf

CSV = os.path.join(os.path.dirname(__file__), "..", "data", "marson_perturbseq",
                   "DE_stats.suppl_table.csv")
CONDITIONS = ["Rest", "Stim8hr", "Stim48hr"]
# Annotate the FULL significant set — NO raw-impact / breadth pre-filter. The overlay's
# whole job is to rescue modest-impact genes that are druggable+disease-linked, so it must
# see every gene. Batched + cached (see opentargets.batch_annotate) makes ~7k feasible.

# The one judgment Open Targets does NOT provide: "is this the obvious TCR machinery?"
# In the real tool the Claude analysis agent makes this call; here it's an explicit set.
OBVIOUS = {"CD3E","CD3D","CD3G","CD247","LAT","ZAP70","PLCG1","LCP2","VAV1","CD28",
           "LCK","FYN","ITK","CD2","CD5"}


def f(x, default=0.0):
    try: return float(x)
    except (TypeError, ValueError): return default


def load(path):
    """Return ({gene: {condition: row}}, {gene: ensembl_id}), significant on-target KDs only."""
    genes = collections.defaultdict(dict)
    ensembl = {}
    for r in csv.DictReader(open(path)):
        if r["ontarget_significant"] != "True":        # GATE 1: must be a real KD
            continue
        if r["offtarget_flag"] == "True":              # GATE 2: drop off-target-confounded
            continue
        g = r["target_contrast_gene_name"]
        genes[g][r["culture_condition"]] = r
        ensembl[g] = r["target_contrast"]              # Ensembl ID for Open Targets
    return genes, ensembl


def impact(row):
    """Per-condition impact: effect breadth, gently weighted by KD strength.
    log1p tames the huge dynamic range (5 vs 1027 downstream genes)."""
    breadth = f(row["n_downstream"])                   # how many genes moved
    kd = abs(f(row["ontarget_effect_size"]))           # knockdown strength
    return math.log1p(breadth) * (1.0 + 0.05 * kd)


def fold(cond_rows):
    """Collapse a gene's conditions into (score, context_specificity)."""
    scores = {c: impact(r) for c, r in cond_rows.items()}
    best = max(scores.values())
    # context specificity: how uneven is the effect across conditions? (CBLB scores high)
    if len(scores) > 1:
        lo, hi = min(scores.values()), max(scores.values())
        context = (hi - lo) / hi if hi > 0 else 0.0
    else:
        context = 0.0
    return best, context, scores


def overlay(base, ann):
    """The NOVELTY step, driven by LIVE Open Targets scores (0..1 each).
    A gene must be BOTH a plausible drug target AND genetically disease-linked
    to survive; unknown/undruggable genes are damped, not zeroed."""
    drug = ann.get("druggable_score", 0.0)
    dis = ann.get("disease_score", 0.0)
    # smooth multipliers in ~[0.5 .. 1.5], so a strong biology signal isn't erased
    # by a target that is merely moderately druggable, but druggable+disease wins.
    mult = (0.5 + drug) * (0.5 + dis)
    return base * mult


def rank(genes, ensembl):
    # score raw impact for every significant gene, keep the raw_rank for the demo beat
    scored = []
    for g, cond_rows in genes.items():
        base, context, _ = fold(cond_rows)
        scored.append({"gene": g, "raw": base, "context": context})
    scored.sort(key=lambda x: -x["raw"])
    for i, r in enumerate(scored):
        r["raw_rank"] = i + 1

    # annotate the FULL set via Open Targets (batched + cached)
    pairs = [(r["gene"], ensembl[r["gene"]]) for r in scored]
    print(f"annotating all {len(pairs)} significant genes via Open Targets "
          f"(batched, cached)...")
    ann_by_sym = ot.batch_annotate(pairs, chunk=40, progress=print)

    for r in scored:
        ann = ann_by_sym.get(r["gene"], {})
        r["drug"] = ann.get("druggable_score", 0.0)
        r["dis"] = ann.get("disease_score", 0.0)
        r["clinical"] = ann.get("clinical")
        r["top_disease"] = ann.get("top_disease")
        obvious_penalty = 0.15 if r["gene"] in OBVIOUS else 1.0
        r["actionable"] = overlay(r["raw"], ann) * obvious_penalty

    scored.sort(key=lambda x: -x["actionable"])
    return scored


def verify_shortlist(ranked, top_n):
    """Attach an adversarial verdict to the top-N actionable candidates.

    Ranking decides ORDER (impact x druggability); the verifier decides TRUST
    (does the evidence survive refutation?). We only verify the surfaced
    shortlist — the few genes a scientist would actually look at — mirroring how
    the real Claude agent ranks broadly, then scrutinizes the handful that matter.
    Mutates a copy of each row (adds 'verdict' + 'checks'); returns the new list.
    """
    by = vf.load_by_gene(vf.CSV)
    donor, guide = vf.load_robustness()
    screens = vf.load_held_out()

    out = []
    for r in ranked[:top_n]:
        v = vf.verify(r["gene"], by, donor, guide, screens)
        out.append({**r, "verdict": v["verdict"], "checks": v["checks"]})
    return out


def _evidence_line(checks):
    """One-line human summary of the checks that carry weight (donor/guide/held-out)."""
    parts = []
    for c in checks:
        if c["check"] == "donor_robustness" and c["value"] != "NA":
            parts.append(f"donor {c['value']}")
        elif c["check"] == "cross_guide" and c["value"] != "NA":
            parts.append(f"guide {c['value']}")
        elif c["check"] == "held_out_screen":
            parts.append(f"held-out {'hit' if c['pass'] else 'none'}")
    return ", ".join(parts) or "-"


if __name__ == "__main__":
    genes, ensembl = load(CSV)
    ranked = rank(genes, ensembl)
    print(f"\n{len(genes)} genes had >=1 significant on-target KD; ALL annotated via "
          f"Open Targets, then re-ranked by the overlay.\n")

    TOP_N = 15
    print(f"verifying the top {TOP_N} actionable candidates (adversarial checks)...")
    shortlist = verify_shortlist(ranked, TOP_N)

    print(f"\nACTIONABLE SHORTLIST (top {TOP_N}) — ranked, then verified. "
          f"This is the tool's output.")
    print(f"{'#':>3} {'gene':<9} {'action':>7} {'rawRank':>7} {'verdict':<22} "
          f"{'drug':>5} {'dis':>5} {'evidence':<24} top disease")
    print("-"*118)
    for i, r in enumerate(shortlist, 1):
        print(f"{i:>3} {r['gene']:<9} {r['actionable']:>7.1f} "
              f"{r['raw_rank']:>7} {r['verdict']:<22} "
              f"{r['drug']:>5.2f} {r['dis']:>5.2f} {_evidence_line(r['checks']):<24} "
              f"{(r['top_disease'] or '')[:22]}")

    promoted = [r for r in shortlist if r["verdict"].startswith("PROMOTE")]
    rejected = [r for r in shortlist if r["verdict"] == "REJECT"]
    print(f"\n{len(promoted)} promoted, {len(rejected)} rejected out of the top {TOP_N}. "
          f"The verifier can and does drop candidates — the shortlist is filtered, not just sorted.")

    print("\nwhere our anchors / demo target land (with verdict):")
    pos = {r["gene"]: i+1 for i, r in enumerate(ranked)}
    by = vf.load_by_gene(vf.CSV)
    donor, guide = vf.load_robustness()
    screens = vf.load_held_out()
    for g in ["PTPN2", "CBLB", "RASA2", "TNFAIP3", "CD3E"]:
        if g in pos:
            r = next(x for x in ranked if x["gene"] == g)
            v = vf.verify(g, by, donor, guide, screens)
            print(f"  {g:<9} actionable #{pos[g]:<4} raw #{r['raw_rank']:<5} "
                  f"{v['verdict']:<22} (drug {r['drug']:.2f}, dis {r['dis']:.2f}, "
                  f"{r['top_disease']})")
        else:
            print(f"  {g:<9} not in the annotated pool")
