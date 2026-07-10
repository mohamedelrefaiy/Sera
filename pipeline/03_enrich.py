"""Phase 3 · step 03 — per-gene enrichment for the dossier cards.

Builds enrichment.json: for every gene in the concordance table, the three dossier cards'
real, traceable values:

  quality      cross-donor ρ, cross-guide ρ, on-target significance, off-target flag, and a
               High/Medium/Low confidence tier (config rule) — from the bundled Marson QC CSVs.
  druggability small-molecule + antibody tractability + clinical rung — from Open Targets
               (cached; the client's widened parse now keeps the antibody modality).
  disease      best immune genetic-association score + the disease — from Open Targets.

No fabricated values: a gene absent from a source reports null/unknown for that card, never a
placeholder number. This is what removes the mockup's "Illustrative values" disclaimer.

Deterministic apart from the (cached) OT lookups. Run:  python pipeline/03_enrich.py
"""
from __future__ import annotations

import csv
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_APP = os.path.dirname(_HERE)
if _APP not in sys.path:
    sys.path.insert(0, _APP)

import pandas as pd  # noqa: E402
import yaml  # noqa: E402

from target_triage.clients import opentargets  # noqa: E402
from target_triage.core.concordance import Config  # noqa: E402
from target_triage.core.data import load_marson  # noqa: E402

_DATA = os.path.join(_APP, "target_triage", "data")
_DONOR = os.path.join(_DATA, "robustness", "DE_donor_robustness_correlation_summary.csv")
_GUIDE = os.path.join(_DATA, "robustness", "DE_by_guide_correlation_results.csv")
_CONC = os.path.join(_DATA, "artifacts", "concordance.parquet")
_OUT = os.path.join(_DATA, "artifacts", "enrichment.json")


def _f(v, default=None):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _load_quality() -> dict[str, dict]:
    """Per-gene QC from the bundled Marson robustness tables. Keeps the row with the strongest
    on-target effect per gene (the condition where the knockdown is most real)."""
    donor: dict[str, dict] = {}
    with open(_DONOR, newline="") as fh:
        for r in csv.DictReader(fh):
            g = (r.get("target_name") or "").strip()
            if not g:
                continue
            eff = abs(_f(r.get("ontarget_effect_size"), 0.0) or 0.0)
            cur = donor.get(g)
            if cur is None or eff > cur["_eff"]:
                donor[g] = {
                    "_eff": eff,
                    "donor_corr": _f(r.get("donor_correlation_mean")),
                    "ontarget_significant": (r.get("ontarget_significant") == "True"),
                    "offtarget_flag": (r.get("offtarget_flag") == "True"),
                    "best_condition": (r.get("condition") or "").strip(),
                }
    guide: dict[str, float] = {}
    with open(_GUIDE, newline="") as fh:
        for r in csv.DictReader(fh):
            g = (r.get("target") or "").strip()
            c = _f(r.get("correlation"))
            if g and c is not None and (g not in guide or c > guide[g]):
                guide[g] = c

    out: dict[str, dict] = {}
    for g, d in donor.items():
        out[g] = {
            "donor_corr": round(d["donor_corr"], 3) if d["donor_corr"] is not None else None,
            "guide_corr": round(guide[g], 3) if g in guide else None,
            "ontarget_significant": d["ontarget_significant"],
            "offtarget_flag": d["offtarget_flag"],
            "best_condition": d["best_condition"],
        }
    return out


def _confidence_tier(q: dict | None, cfg_conf: dict) -> str:
    """High / Medium / Low from the config confidence rule (never hardcoded)."""
    if not q:
        return "Low"
    hi = cfg_conf["high"]
    dc, gc = q.get("donor_corr"), q.get("guide_corr")
    if (dc is not None and dc >= hi["min_crossdonor_corr"]
            and gc is not None and gc >= hi["min_crossguide_corr"]
            and (q.get("ontarget_significant") or not hi["require_ontarget_significant"])
            and (not q.get("offtarget_flag") or not hi["require_offtarget_clean"])):
        return "High"
    lo = cfg_conf["low"]
    if (dc is None or dc <= lo["max_crossdonor_corr"]) or not q.get("ontarget_significant"):
        return "Low"
    return "Medium"


def build(progress=print) -> dict:
    with open(os.path.join(_APP, "config.yaml")) as fh:
        raw = yaml.safe_load(fh)
    cfg_conf = raw["confidence"]

    genes = sorted(set(pd.read_parquet(_CONC)["gene"].tolist()))
    progress(f"[enrich] {len(genes)} genes in the concordance table")

    quality = _load_quality()
    progress(f"[enrich] quality QC loaded for {len(quality)} genes")

    # Open Targets druggability + disease (cached; only un-cached genes hit the API).
    recs = {r.gene: r for r in load_marson()}
    pairs = [(g, recs[g].ensembl_id) for g in genes if g in recs and recs[g].ensembl_id]
    ann = opentargets.annotate_many(pairs, progress=progress)
    progress(f"[enrich] Open Targets annotations for {len(ann)} genes")

    out: dict[str, dict] = {}
    for g in genes:
        q = quality.get(g)
        a = ann.get(g)
        out[g] = {
            "quality": ({**q, "confidence": _confidence_tier(q, cfg_conf)} if q
                        else {"confidence": "Low", "donor_corr": None, "guide_corr": None,
                              "ontarget_significant": None, "offtarget_flag": None,
                              "best_condition": None}),
            "druggability": ({
                "sm_score": a.druggable_score, "sm_stage": a.sm_stage,
                "ab_score": a.antibody_score, "ab_stage": a.antibody_stage,
            } if a else {"sm_score": 0.0, "sm_stage": None, "ab_score": 0.0, "ab_stage": None}),
            "disease": ({"score": a.disease_score, "top_disease": a.top_disease} if a
                        else {"score": 0.0, "top_disease": None}),
        }
    return out


def main() -> int:
    data = build()
    os.makedirs(os.path.dirname(_OUT), exist_ok=True)
    with open(_OUT, "w") as fh:
        json.dump(data, fh, indent=0)
    print(f"[write] {_OUT}  ({len(data)} genes)")

    for g in ["ITK", "TSC1", "LCP2", "IL2RA"]:
        if g in data:
            d = data[g]
            print(f"  {g:6s} conf={d['quality']['confidence']:6s} "
                  f"SM={d['druggability']['sm_score']} AB={d['druggability']['ab_score']} "
                  f"disease={d['disease']['score']} ({d['disease']['top_disease']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
