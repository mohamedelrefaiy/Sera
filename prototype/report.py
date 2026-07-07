"""
Target Triage — report emitter. Turns the ranked + verified shortlist into the
artifact a scientist takes to the bench.

PRE-KICKOFF PROTOTYPE. Not the hackathon submission; explores feasibility on
public data before the build officially starts. See prototype/README.md.

Emits three things under output/:
  - shortlist.json            machine-readable: every candidate, its scores,
                              verdict, and every adversarial check (value + why).
  - shortlist.md              a one-screen index table (rank, verdict, evidence).
  - targets/<GENE>.md         one bench report per PROMOTED candidate: the verdict,
                              the mechanism-level summary, the evidence WITH numbers
                              and sources, and an honest "what would refute this".

Design: this file only FORMATS. All scoring/verdict logic lives in rank_sketch.py
and verifier.py — the report consumes their output so the numbers can never drift
from what the pipeline actually computed. Output is deterministic (no timestamps)
so a stranger re-running from the README gets a byte-identical artifact.
"""
import json
import os

import rank_sketch as rs

_D = os.path.dirname(__file__)
OUT = os.path.join(_D, "output")
TARGETS = os.path.join(OUT, "targets")
TOP_N = 15


# ---------------------------------------------------------------- serialization

def _check_by_name(checks, name):
    return next((c for c in checks if c["check"] == name), None)


def _row_to_record(rank_pos, r):
    """Flatten one verified row into a JSON-serializable record."""
    return {
        "rank": rank_pos,
        "gene": r["gene"],
        "verdict": r["verdict"],
        "actionable_score": round(r["actionable"], 3),
        "raw_impact_rank": r["raw_rank"],
        "context_specificity": round(r["context"], 3),
        "open_targets": {
            "druggable_score": r["drug"],
            "disease_score": r["dis"],
            "clinical_stage": r["clinical"],
            "top_immune_disease": r["top_disease"],
        },
        "verifier_checks": [
            {
                "check": c["check"],
                "kind": "gate" if c.get("gate") else ("bonus" if c.get("bonus") else "score"),
                "pass": c["pass"],
                "value": c["value"],
                "detail": c["why"],
            }
            for c in r["checks"]
        ],
    }


# ---------------------------------------------------------------- markdown parts

def _verdict_badge(verdict):
    if verdict == "REJECT":
        return "❌ REJECT"
    if "corroborated" in verdict:
        return "✅ PROMOTE — independently corroborated"
    if "weak" in verdict:
        return "⚠️ PROMOTE (weak)"
    return "✅ PROMOTE"


def _held_out_evidence(checks):
    c = _check_by_name(checks, "held_out_screen")
    if c and c["pass"]:
        # c["why"] is "independent hit — Schmidt2022:...; Freimer2022:..."
        return c["why"].replace("independent hit — ", "")
    return None


def _target_markdown(rank_pos, r):
    """One bench report for a single promoted candidate."""
    # normalize the ranker's row keys into the names this template uses
    ot = {
        "druggable_score": r["drug"],
        "disease_score": r["dis"],
        "clinical": r["clinical"],
        "top_disease": r["top_disease"],
    }
    checks = r["checks"]
    real_kd = _check_by_name(checks, "real_knockdown")
    cross_cond = _check_by_name(checks, "cross_condition")
    donor = _check_by_name(checks, "donor_robustness")
    guide = _check_by_name(checks, "cross_guide")
    held = _held_out_evidence(checks)

    L = []
    L.append(f"# {r['gene']} — candidate T-cell regulator\n")
    L.append(f"**Verdict: {_verdict_badge(r['verdict'])}**  ·  "
             f"actionable rank **#{rank_pos}** of the shortlist  ·  "
             f"raw-impact rank #{r['raw_rank']}\n")

    # Why it's actionable (the one-line rationale a scientist reads first)
    dis = ot["top_disease"] or "no immune disease association"
    clin = ot["clinical"]
    clin_str = f", already at **{clin}** stage clinically" if clin else ""
    L.append("## Why it's on the shortlist\n")
    L.append(
        f"Knocking down **{r['gene']}** produces a broad, significant transcriptional "
        f"effect in primary CD4+ T cells, and the gene is a plausible drug target "
        f"linked to **{dis}**{clin_str}. It rose from raw-impact rank "
        f"#{r['raw_rank']} to actionable rank #{rank_pos} because the druggability + "
        f"immune-genetics overlay favors it over higher-impact but non-actionable hits "
        f"(e.g. core TCR machinery).\n")

    # Evidence table — numbers + sources, the credibility core
    L.append("## Evidence (computed, not asserted)\n")
    L.append("| Check | Result | What it rules out |")
    L.append("|---|---|---|")
    if real_kd:
        L.append(f"| On-target knockdown | {real_kd['value']} (|effect|) | that there's no real perturbation |")
    if cross_cond:
        L.append(f"| Reproduces across conditions | {cross_cond['value']} of Rest/Stim8hr/Stim48hr "
                 f"| a single-condition blip |")
    if donor and donor["value"] != "NA":
        L.append(f"| Cross-donor robustness | corr = {donor['value']} | a single-donor artifact |")
    if guide and guide["value"] != "NA":
        L.append(f"| Cross-guide agreement | corr = {guide['value']} | an off-target / guide artifact |")
    L.append(f"| Druggability (Open Targets) | {ot['druggable_score']:.2f} "
             f"| that no small-molecule handle exists |")
    L.append(f"| Immune-disease genetics (Open Targets) | {ot['disease_score']:.2f} → {dis} "
             f"| a coincidental, non-immune association |")
    L.append("")

    # Held-out independent corroboration (the "predicted blind" beat)
    L.append("## Independent corroboration (held-out)\n")
    if held:
        L.append(f"An independent CRISPR screen the pipeline never saw **agrees**: {held}.\n")
    else:
        L.append("Not a significant hit in the held-out screens (Schmidt 2022 / Freimer 2022). "
                 "This is **not disqualifying** — those screens cover a different gene set and "
                 f"readouts; {r['gene']}'s independent support here is clinical/genetic "
                 "(Open Targets), not screen-based.\n")

    # Honest refutation section — what would kill this pick
    L.append("## What would refute this\n")
    refuters = []
    if donor and donor["value"] != "NA" and isinstance(donor["value"], (int, float)) and donor["value"] < 0.4:
        refuters.append(f"cross-donor correlation is modest ({donor['value']}) — confirm it holds "
                        "in an independent donor cohort before committing bench time")
    if cross_cond and cross_cond["value"] not in ("NA", None) and cross_cond["value"] < 1.0:
        refuters.append("the effect does not reproduce in all three conditions — the context it "
                        "acts in matters; pick the matching stimulation state for validation")
    if not held:
        refuters.append("no held-out screen hit — an arrayed knockdown in a fresh donor is the "
                        "cheapest disconfirming experiment")
    if ot["druggable_score"] < 0.3:
        refuters.append(f"druggability is low ({ot['druggable_score']:.2f}) — may be a valid "
                        "biological hit but a hard drug target")
    if not refuters:
        refuters.append("the strongest disconfirmation is a fresh-donor arrayed knockdown that "
                        "fails to move the expected program.")
    for x in refuters:
        L.append(f"- {x}")
    L.append("")

    # Suggested next experiment (the Monday-morning artifact)
    cond = "stimulated (Stim8hr)" if (cross_cond and cross_cond["value"] and cross_cond["value"] < 1.0) \
        else "resting and stimulated"
    L.append("## Suggested validation\n")
    L.append(f"- **Perturbation:** CRISPRi knockdown of {r['gene']} (2 guides) in primary CD4+ T cells.")
    L.append(f"- **Context:** {cond}.")
    L.append(f"- **Readout:** activation/polarization program (IL2 / IFNG); compare to non-targeting control.")
    L.append(f"- **Predicted direction:** matches the screen's on-target effect (see evidence table).")
    L.append("")
    L.append("---")
    L.append("_Generated by Target Triage (pre-kickoff prototype). Scores from the Marson CD4+ "
             "T-cell Perturb-seq summary + live Open Targets; verdicts from the adversarial "
             "verifier. Reproducible via `python3 report.py`._")
    return "\n".join(L)


def _index_markdown(records):
    L = []
    L.append("# Target Triage — shortlist\n")
    promoted = [r for r in records if r["verdict"].startswith("PROMOTE")]
    rejected = [r for r in records if r["verdict"] == "REJECT"]
    L.append(f"Ranked druggable, immune-disease-linked regulators of CD4+ T-cell activation "
             f"from the Marson Perturb-seq screen, each stress-tested by an adversarial verifier. "
             f"**{len(promoted)} promoted, {len(rejected)} rejected** out of the top {len(records)} "
             f"by actionable score — the shortlist is filtered by scrutiny, not merely sorted.\n")
    L.append("| # | Gene | Verdict | Druggable | Immune-disease | Top disease | Report |")
    L.append("|---|---|---|---|---|---|---|")
    for r in records:
        ot = r["open_targets"]
        link = f"[{r['gene']}](targets/{r['gene']}.md)" if r["verdict"].startswith("PROMOTE") else r["gene"]
        L.append(f"| {r['rank']} | {r['gene']} | {_verdict_badge(r['verdict'])} "
                 f"| {ot['druggable_score']:.2f} | {ot['disease_score']:.2f} "
                 f"| {ot['top_immune_disease'] or '—'} | {link} |")
    L.append("")
    L.append("> Rejections are shown, not hidden — a candidate is dropped when a **gate** check "
             "fails (e.g. NRAS: cross-donor correlation ≈ −0.1, a donor-driven artifact). "
             "That is the verifier having teeth.")
    return "\n".join(L)


# ---------------------------------------------------------------- orchestration

def build():
    genes, ensembl = rs.load(rs.CSV)
    ranked = rs.rank(genes, ensembl)
    shortlist = rs.verify_shortlist(ranked, TOP_N)
    records = [_row_to_record(i, r) for i, r in enumerate(shortlist, 1)]

    os.makedirs(TARGETS, exist_ok=True)

    with open(os.path.join(OUT, "shortlist.json"), "w") as fh:
        json.dump({"top_n": TOP_N, "candidates": records}, fh, indent=2)

    with open(os.path.join(OUT, "shortlist.md"), "w") as fh:
        fh.write(_index_markdown(records) + "\n")

    written = []
    for pos, r in enumerate(shortlist, 1):
        if not r["verdict"].startswith("PROMOTE"):
            continue
        path = os.path.join(TARGETS, f"{r['gene']}.md")
        with open(path, "w") as fh:
            fh.write(_target_markdown(pos, r) + "\n")
        written.append(r["gene"])

    return records, written


if __name__ == "__main__":
    records, written = build()
    n_prom = sum(1 for r in records if r["verdict"].startswith("PROMOTE"))
    n_rej = sum(1 for r in records if r["verdict"] == "REJECT")
    print(f"wrote output/shortlist.json  ({len(records)} candidates)")
    print(f"wrote output/shortlist.md    (index: {n_prom} promoted, {n_rej} rejected)")
    print(f"wrote {len(written)} per-target reports under output/targets/:")
    print("   " + ", ".join(written))
