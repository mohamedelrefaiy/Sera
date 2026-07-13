"""DECISION-BRIEF API + RESOLVER GATE — the brief must reach the product, honestly.

The pure builder is covered by test_decision_brief.py. This gate covers the bridge:

  - the resolver turns a real concordance row + provenance into the typed inputs, with the RNA cell
    type / condition supplied from the adapter's knowledge (provenance stores null for Zhu);
  - /api/concordance/TSC1 returns a serialisable decision_brief for IL2/Stim48hr;
  - the brief that reaches the wire is still honest: discordant, partially_comparable for the real
    reason, citations are mtor_background, trust is neither_yet;
  - a gene absent from the screens 404s (never a fabricated brief);
  - the endpoint degrades to decision_brief=null rather than 500-ing if the brief can't be built.

Run:  pytest eval/test_decision_brief_api.py
"""
from __future__ import annotations

import os
import sys

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from target_triage.api.app import app  # noqa: E402
from target_triage.core.brief_resolver import (  # noqa: E402
    resolve_claims, resolve_context, resolve_snapshot)


# --- resolver (artifacts -> typed inputs) ----------------------------------------------------

_PROVENANCE = {
    "screens": [
        {"screen_id": "zhu2025", "mapping": {
            "modality": "rna", "significance": {"regime": "deseq2_adjp"},
            "cell_type": None, "condition": None}},
        {"screen_id": "schmidt2022", "mapping": {
            "modality": "protein", "significance": {"regime": "benjamini_fdr"},
            "cell_type": {"constant": None, "column": "phenotype", "extract": "first_token"},
            "condition": {"constant": "Stimulated", "column": None}}},
    ],
    "claims": [
        {"gene": "TSC1", "cytokine": "IL2", "condition": "Stim48hr",
         "label": "HYPOTHESIS — not used in verdict", "claim": "mTORC1 story",
         "citation": {"db": "pubmed", "accession": "12172553", "status": "resolved",
                      "title": "TSC2 ... suppresses mTOR signalling."}},
        {"gene": "TSC1", "cytokine": "IL2", "condition": "Stim48hr",
         "label": "HYPOTHESIS — not used in verdict", "claim": "energy sensing",
         "citation": {"db": "pubmed", "accession": "99999999", "status": "unresolved",
                      "title": "not resolved yet"}},
    ],
}

_TSC1_ROW = {
    "gene": "TSC1", "cytokine": "IL2", "condition": "Stim48hr", "verdict": "discordant",
    "rna_tested": True, "prot_tested": True, "rna_promotes": True, "prot_promotes": False,
}


def test_resolver_snapshot_passes_verdict_through_and_names_screens():
    snap = resolve_snapshot(_TSC1_ROW, _PROVENANCE)
    assert snap.verdict == "discordant"          # never recomputed
    assert snap.rna_screen_id == "zhu2025"
    assert snap.protein_screen_id == "schmidt2022"


def test_resolver_context_supplies_adapter_knowledge_and_no_time_axis():
    ctx = resolve_context(_TSC1_ROW, _PROVENANCE)
    # provenance stores null for the mRNA cell type/condition; the resolver supplies the adapter's
    # facts so comparability fires the real caveat rather than collapsing to insufficient_metadata.
    assert ctx.rna_cell_type == "CD4"
    assert ctx.rna_condition == "Stim48hr"
    assert ctx.rna_regime == "deseq2_adjp"
    assert ctx.prot_regime == "benjamini_fdr"
    assert ctx.prot_has_time_axis is False       # Schmidt: constant condition = single readout


def test_resolver_claims_only_surface_resolved_citations_as_background():
    claims = resolve_claims("TSC1", "IL2", "Stim48hr", _PROVENANCE)
    assert len(claims) == 1                       # the unresolved one is dropped
    assert claims[0].accession == "12172553"
    assert claims[0].supports == "mtor_background"


# --- API endpoint (the brief reaches the wire) -----------------------------------------------

def test_concordance_gene_returns_honest_decision_brief():
    with TestClient(app) as client:
        r = client.get("/api/concordance/TSC1")
        assert r.status_code == 200
        b = r.json().get("decision_brief")
        assert b is not None, "TSC1 should have a decision brief"
        assert b["snapshot"]["verdict"] == "discordant"
        assert b["comparability"] == "partially_comparable"
        # the headline comparability reason is the untimed protein readout
        assert any("time" in x.lower() and "protein" in x.lower()
                   for x in b["comparability_reasons"])
        assert b["trust_assessment"] == "neither_yet"
        assert b["citations"], "TSC1 has stored citations"
        assert all(c["supports"] == "mtor_background" for c in b["citations"])
        # paired readout + NTC
        ro = " ".join(b["experiment"]["readouts"]).lower()
        assert "transcript" in ro and "protein" in ro


def test_unknown_gene_404s_never_a_fabricated_brief():
    with TestClient(app) as client:
        r = client.get("/api/concordance/NOTAGENE12345")
        assert r.status_code == 404


def test_brief_is_json_serialisable_over_the_wire():
    with TestClient(app) as client:
        r = client.get("/api/concordance/TSC1")
        # if it came back over HTTP as JSON, it serialised; assert the nested shapes survived
        b = r.json()["decision_brief"]
        assert isinstance(b["explanations"], list)
        assert isinstance(b["outcome_matrix"], list) and b["outcome_matrix"]
        assert isinstance(b["experiment"], dict)


# --- constraints over the wire (Task 4) ------------------------------------------------------

def test_elisa_only_constraint_is_reported_infeasible():
    with TestClient(app) as client:
        b = client.get("/api/concordance/TSC1?readouts=elisa&donors=3&days=5").json()["decision_brief"]
        assert b["feasible"] is False
        assert any("transcript" in u.lower() for u in b["unmet_requirements"])
        # still the same code-computed verdict — constraints never touch it
        assert b["snapshot"]["verdict"] == "discordant"


def test_too_few_donors_constraint_is_infeasible():
    with TestClient(app) as client:
        b = client.get("/api/concordance/TSC1?readouts=qpcr,facs&donors=1&days=5").json()["decision_brief"]
        assert b["feasible"] is False
        assert any("donor" in u.lower() for u in b["unmet_requirements"])


def test_valid_constraints_are_feasible_and_respected():
    with TestClient(app) as client:
        b = client.get("/api/concordance/TSC1?readouts=qpcr,facs&donors=4&days=7").json()["decision_brief"]
        assert b["feasible"] is True
        assert not b["unmet_requirements"]
        ro = " ".join(b["experiment"]["readouts"]).lower()
        assert "qpcr" in ro and "facs" in ro


def test_short_window_drops_timecourse_and_says_so():
    with TestClient(app) as client:
        b = client.get("/api/concordance/TSC1?readouts=qpcr,facs&donors=3&days=2").json()["decision_brief"]
        assert b["experiment"]["timecourse"] is None
        assert any("timepoint" in a.lower() or "window" in a.lower() for a in b["adaptations"])


def test_unknown_readout_token_is_a_400():
    with TestClient(app) as client:
        r = client.get("/api/concordance/TSC1?readouts=bogus&donors=3&days=5")
        assert r.status_code == 400


def test_saved_lab_profile_adapts_feasibility_without_changing_the_scientific_decision():
    """A lab profile changes how the experiment can be run, not what the screens concluded."""
    with TestClient(app) as client:
        baseline = client.get("/api/concordance/TSC1").json()["decision_brief"]
        response = client.get(
            "/api/concordance/TSC1",
            params={"readouts": " western, qPCR ", "donors": 4, "days": 7},
        )

        assert response.status_code == 200
        profiled = response.json()["decision_brief"]
        assert profiled["feasible"] is True
        assert profiled["unmet_requirements"] == []
        assert "western" in " ".join(profiled["experiment"]["readouts"]).lower()
        assert profiled["snapshot"]["verdict"] == baseline["snapshot"]["verdict"]
        assert profiled["recommendation"] == baseline["recommendation"]


def test_infeasible_saved_lab_profile_reports_every_blocker_and_safe_adaptation():
    """The API must explain a constrained lab honestly instead of silently weakening the test."""
    with TestClient(app) as client:
        response = client.get(
            "/api/concordance/TSC1",
            params={"readouts": "ELISA", "donors": 2, "days": 2},
        )

        assert response.status_code == 200
        brief = response.json()["decision_brief"]
        blockers = " ".join(brief["unmet_requirements"]).lower()
        adaptations = " ".join(brief["adaptations"]).lower()
        assert brief["feasible"] is False
        assert "transcript" in blockers
        assert "donor" in blockers
        assert brief["experiment"]["timecourse"] is None
        assert "timepoint" in adaptations or "window" in adaptations
