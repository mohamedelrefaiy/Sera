"""GROUND-TRUTH GATE — the money demo's claim must stay true and honest.

The "known biology" panel asserts that Sera independently recovers the canonical IL-2
regulators. This gate makes that claim non-hand-wavy and regression-proof:
  - the canonical positive regulators are RECOVERED (a hit on at least one side, not 'neither'),
  - the strongest ones (ITK/BCL10/VAV1) are replicated,
  - the known brake TSC1 is discordant (not falsely 'replicated'),
  - and the aggregate Spearman is near zero (the average that hides the per-gene structure).

If the panel ever regressed to overclaiming (e.g. calling everything replicated, or losing the
near-zero aggregate), this gate fails. SKIPS if the artifact isn't built.

Run:  pytest eval/test_ground_truth.py
"""
from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

_GT = os.path.join(os.path.dirname(__file__), "..", "data", "artifacts", "ground_truth.json")


def _load():
    if not os.path.exists(_GT):
        pytest.skip("ground_truth.json not built — run `python pipeline/05_ground_truth.py`")
    with open(_GT) as fh:
        return json.load(fh)


def test_most_canonical_regulators_are_recovered():
    """Sera must recover the large majority of textbook IL-2 positive regulators — a low
    recovery rate would mean the join is missing known biology."""
    d = _load()
    s = d["summary"]
    frac = s["n_recovered"] / s["n_positive"]
    assert frac >= 0.8, (
        f"only {s['n_recovered']}/{s['n_positive']} canonical regulators recovered "
        f"({frac:.0%}) — the join is missing known biology")


def test_strongest_regulators_replicate():
    """ITK / BCL10 / VAV1 are the cleanest cross-modality positives — they must be replicated."""
    d = _load()
    by_gene = {p["gene"]: p for p in d["positive_regulators"]}
    for g in ("ITK", "BCL10", "VAV1"):
        assert g in by_gene, f"{g} missing from the ground-truth positives"
        assert by_gene[g]["verdict"] == "replicated", (
            f"{g} is {by_gene[g]['verdict']}, expected replicated")


def test_tsc1_brake_is_discordant_not_replicated():
    """The canonical brake TSC1 (lowers transcript, raises protein) must surface as discordant —
    calling it replicated would be the overclaim the panel exists to avoid."""
    d = _load()
    by_gene = {b["gene"]: b for b in d["brakes"]}
    assert "TSC1" in by_gene, "TSC1 missing from the ground-truth brakes"
    assert by_gene["TSC1"]["verdict"] == "discordant"


def test_aggregate_spearman_stays_near_zero():
    """The near-zero aggregate is the panel's headline ('the average hides the structure'). If
    it drifted large, the join would be wrong and the whole premise unsupported."""
    d = _load()
    assert abs(d["aggregate_spearman"]) < 0.15, (
        f"aggregate Spearman {d['aggregate_spearman']} is not near zero — premise broken")


def test_protein_only_recoveries_are_reported_honestly():
    """Some canonical positives come out protein_only (post-transcriptional). The panel must
    report a non-zero count of these — that's the honest 'a transcriptome-only search misses
    these' story, not everything collapsed into replicated."""
    d = _load()
    assert d["summary"]["n_protein_only"] >= 1, (
        "no protein-only recoveries — the panel would be hiding the post-transcriptional story")
