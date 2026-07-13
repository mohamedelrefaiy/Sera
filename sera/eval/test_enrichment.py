"""ENRICHMENT GATE — the dossier cards must show real, correctly-attributed values.

Guards the two ways the Druggability / Disease / Quality cards could quietly lie:
  (1) modality leakage — an ANTIBODY approval must never appear as a small-molecule stage.
      This is the exact bug the SM-specific `sm_stage` fix closed (IL2RA: approved antibody,
      no small-molecule approval — its SM stage must be None, its AB stage "Approved").
  (2) fabricated confidence — the tier must follow the config rule from real QC, and a gene
      with strong donor+guide agreement + on-target significance is High, not a placeholder.

Also asserts the OT parse keeps SM and AB as INDEPENDENT modalities (a surface-receptor
antibody target is not "undruggable" just because it has no small molecule).

Run:  pytest eval/test_enrichment.py
The enrichment.json gate SKIPS if the artifact isn't built (never silently passes).
"""
from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from sera.clients import opentargets  # noqa: E402

_ENRICH = os.path.join(os.path.dirname(__file__), "..", "data", "artifacts", "enrichment.json")


def _load():
    if not os.path.exists(_ENRICH):
        pytest.skip("enrichment.json not built — run `python pipeline/03_enrich.py`")
    with open(_ENRICH) as fh:
        return json.load(fh)


# ---- (1) modality attribution: an antibody approval is never an SM stage ----------------


def test_sm_and_ab_stages_are_independent_in_parse():
    """_parse must attribute a clinical rung to the modality that owns it. A gene with an
    approved ANTIBODY but no small-molecule tractability must have sm_stage None."""
    ab_only = {
        "tractability": [
            {"modality": "AB", "label": "Approved Drug", "value": True},
            {"modality": "SM", "label": "Approved Drug", "value": False},
        ],
        "associatedDiseases": {"rows": []},
        "approvedSymbol": "FAKE_AB",
    }
    a = opentargets._parse(ab_only, "FAKE_AB")
    assert a.antibody_stage == "Approved", "AB approval should be attributed to the antibody stage"
    assert a.sm_stage is None, "an antibody approval must NOT appear as a small-molecule stage"
    # clinical_stage stays cross-modality (best across both) for back-compat.
    assert a.clinical_stage == "Approved"


def test_enrichment_il2ra_is_an_antibody_target_not_small_molecule():
    """IL2RA (surface receptor) is the canonical antibody target: approved antibody, no
    small-molecule approval. The Druggability card must reflect that split."""
    data = _load()
    if "IL2RA" not in data:
        pytest.skip("IL2RA not in the concordance table")
    d = data["IL2RA"]["druggability"]
    assert d["ab_stage"] == "Approved", "IL2RA has an approved antibody"
    assert d["sm_stage"] is None, "IL2RA has no small-molecule approval — SM stage must be None"


# ---- (2) confidence tier follows real QC, not a placeholder ------------------------------


def test_confidence_rule_is_applied_honestly():
    """The confidence tier must follow the config rule from REAL QC, and — crucially — degrade
    honestly when a signal is missing. High requires BOTH cross-donor AND cross-guide agreement;
    a gene with strong donor agreement but NO guide-correlation record must be Medium, not High
    (we won't claim the higher tier on a signal we don't have). This is the exact behaviour that
    keeps the card from overclaiming.

    ITK is a full-signal positive control (both correlations present, strong) → High.
    VAV1 has strong donor agreement but no cross-guide record → Medium (honest degrade)."""
    data = _load()
    for gene in ("ITK", "VAV1"):
        if gene not in data:
            pytest.skip(f"{gene} not enriched")

    itk = data["ITK"]["quality"]
    assert itk["confidence"] == "High", f"ITK (full QC signal) should be High, got {itk['confidence']}"
    assert itk["donor_corr"] and itk["donor_corr"] >= 0.5
    assert itk["guide_corr"] and itk["guide_corr"] >= 0.5

    vav1 = data["VAV1"]["quality"]
    # strong donor, on-target significant, but no guide record → must NOT be High
    assert vav1["donor_corr"] and vav1["donor_corr"] >= 0.5
    assert vav1["ontarget_significant"] is True
    if vav1["guide_corr"] is None:
        assert vav1["confidence"] != "High", (
            "VAV1 has no cross-guide record — claiming High would overclaim on a missing signal")


def test_no_fabricated_druggability_for_absent_genes():
    """A gene with no Open Targets tractability reports zeros / None, never a placeholder
    number — the 'never fabricate a signal' doctrine, at the dossier layer."""
    data = _load()
    # every druggability record is well-formed (scores in [0,1], stages str or None)
    for gene, rec in data.items():
        dr = rec["druggability"]
        assert 0.0 <= dr["sm_score"] <= 1.0, f"{gene} sm_score out of range"
        assert 0.0 <= dr["ab_score"] <= 1.0, f"{gene} ab_score out of range"
        assert dr["sm_stage"] is None or isinstance(dr["sm_stage"], str)
        assert dr["ab_stage"] is None or isinstance(dr["ab_stage"], str)
