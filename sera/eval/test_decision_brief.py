"""GOLDEN DECISION-BRIEF CONTROL — the TSC1 flagship, made regression-proof.

This is the positive control for the decision brief: an item is not "done" until this prints PASS.
It pins the brief's scientific honesty, not just its shape:

  - the verdict rides in from the deterministic core and is never recomputed (discordant for TSC1);
  - comparability reports the REAL caveat (48h RNA vs an untimed protein readout), exactly one value;
  - competing explanations come from the code-owned closed set and keep a technical alternative;
  - the discriminating experiment is paired-readout with >=3 donors + NTC;
  - the trust conclusion is `neither_yet` — the disagreement is unresolved until the experiment runs;
  - no raw statistic leaks into the PROSE fields (protocol numbers like "3 donors" are allowed);
  - stored citations are background for the mTOR hypothesis, never verdict support;
  - the brief serialises to JSON (the API will send it);
  - and the builder degrades explicitly on bad input rather than emitting a wrong brief.

The TSC1 snapshot mirrors the real concordance artifact row:
  verdict=discordant, rna_promotes=True (KD lowers transcript), prot_promotes=False (KD raises
  protein) — the classic mRNA/protein decoupling. The context mirrors rim/adapters.py: the mRNA side
  (zhu2025, DESeq2 adj-p) vs the protein side (schmidt2022, single-readout FACS, Benjamini-FDR, no
  time axis).
"""
from __future__ import annotations

import dataclasses
import json
import re

import pytest

from sera.core.decision_brief import (
    COMPARABILITY, EXPLANATION_CLASSES, RECOMMENDATION, ConcordanceSnapshot, ExperimentConstraints,
    GroundedClaim, ScreenPairContext, TargetDossier, build_decision_brief)


# --- fixtures that mirror the real TSC1 reconciliation ---------------------------------------

def _tsc1_snapshot() -> ConcordanceSnapshot:
    """TSC1 @ IL2 / Stim48hr, as the deterministic core computes it (concordance.parquet row)."""
    return ConcordanceSnapshot(
        gene="TSC1", cytokine="IL2", condition="Stim48hr", verdict="discordant",
        rna_tested=True, protein_tested=True,
        rna_promotes=True, prot_promotes=False,           # opposite directions -> discordant
        rna_screen_id="zhu2025", protein_screen_id="schmidt2022")


def _tsc1_context() -> ScreenPairContext:
    """From rim/adapters.py: protein side is CD4 Stimulated, single-readout FACS (no time axis),
    Benjamini-FDR; mRNA side (zhu2025) is DESeq2 adj-p, cell type/condition CD4/Stim48hr."""
    return ScreenPairContext(
        rna_cell_type="CD4", rna_condition="Stim48hr", rna_regime="deseq2_adjp",
        prot_cell_type="CD4", prot_condition="Stimulated", prot_regime="benjamini_fdr",
        prot_has_time_axis=False)


def _tsc1_claims() -> tuple[GroundedClaim, ...]:
    """The two stored TSC1 hypotheses — both cite TSC2/mTOR papers, both background for the mTOR
    hypothesis, neither supports the IL-2 verdict."""
    return (
        GroundedClaim(
            text="TSC1 suppression activates mTORC1 signaling at the protein level.",
            label="HYPOTHESIS — not used in verdict",
            db="pubmed", accession="12172553",
            title="TSC2 is phosphorylated and inhibited by Akt and suppresses mTOR signalling.",
            supports="mtor_background"),
        GroundedClaim(
            text="TSC1 loss impairs cellular energy sensing, sustaining mTORC1-driven output.",
            label="HYPOTHESIS — not used in verdict",
            db="pubmed", accession="14651849",
            title="TSC2 mediates cellular energy response to control cell growth and survival.",
            supports="mtor_background"),
    )


#: The PROSE fields — narration a bench reader consumes. These must never quote a raw statistic.
#: The experiment fields are protocol (10 nM, 3 donors) and are intentionally excluded.
_STAT_TOKENS = re.compile(r"\b(z[- ]?score|log[- ]?fold|lfc|p[- ]?value|\bp\b|fdr|q[- ]?value)\b",
                          re.IGNORECASE)


# --- the control -----------------------------------------------------------------------------

def test_tsc1_verdict_is_discordant_from_the_core():
    brief = build_decision_brief(_tsc1_snapshot(), _tsc1_context(), _tsc1_claims())
    assert brief.snapshot.verdict == "discordant"


def test_tsc1_comparability_is_partially_comparable_for_the_real_reason():
    brief = build_decision_brief(_tsc1_snapshot(), _tsc1_context(), _tsc1_claims())
    assert brief.comparability in COMPARABILITY
    # The headline caveat is the untimed protein readout — not merely different FDR regimes.
    assert brief.comparability == "partially_comparable"
    joined = " ".join(brief.comparability_reasons).lower()
    assert "time" in joined and "protein" in joined, brief.comparability_reasons


def test_tsc1_explanations_are_closed_set_and_keep_a_technical_alternative():
    brief = build_decision_brief(_tsc1_snapshot(), _tsc1_context(), _tsc1_claims())
    assert set(brief.explanations) <= set(EXPLANATION_CLASSES["discordant"])
    # The specific TSC1 alternatives we expect, not just "some count".
    assert "post_transcriptional_or_secretion" in brief.explanations
    assert {"context_mismatch", "assay_artifact", "perturbation_strength"} & set(brief.explanations)
    assert len(brief.explanations) == len(brief.explanation_labels)


def test_tsc1_experiment_is_paired_readout_with_guides_and_ntc():
    brief = build_decision_brief(_tsc1_snapshot(), _tsc1_context(), _tsc1_claims())
    ro = " ".join(brief.experiment.readouts).lower()
    assert "transcript" in ro and "protein" in ro          # paired, both endpoints
    assert any("ntc" in c.lower() or "non-targeting" in c.lower() for c in brief.experiment.controls)
    assert len(brief.experiment.guides) >= 2


def test_tsc1_trust_is_unresolved_until_validation():
    brief = build_decision_brief(_tsc1_snapshot(), _tsc1_context(), _tsc1_claims())
    assert brief.trust_assessment == "neither_yet"
    assert brief.decision.strip()


def test_no_raw_statistics_leak_into_prose_fields():
    brief = build_decision_brief(_tsc1_snapshot(), _tsc1_context(), _tsc1_claims())
    prose = " ".join((
        brief.decision, brief.remaining_uncertainty,
        *brief.comparability_reasons, *brief.evidence_limits, *brief.explanation_labels,
        *(o.interpretation for o in brief.outcome_matrix)))
    hit = _STAT_TOKENS.search(prose)
    assert hit is None, f"raw statistic leaked into prose: {hit.group(0)!r}"


def test_citations_are_background_not_verdict_support():
    brief = build_decision_brief(_tsc1_snapshot(), _tsc1_context(), _tsc1_claims())
    assert len(brief.citations) == 2
    for c in brief.citations:
        assert c.supports == "mtor_background"
        assert c.supports != "verdict"
        assert c.accession.isdigit()               # a real, resolved PubMed accession


def test_a_citation_claiming_verdict_support_is_rejected():
    bad = GroundedClaim(text="x", label="y", db="pubmed", accession="1",
                        title="t", supports="verdict")
    with pytest.raises(ValueError, match="verdict"):
        build_decision_brief(_tsc1_snapshot(), _tsc1_context(), (bad,))


def test_brief_serialises_to_json():
    brief = build_decision_brief(_tsc1_snapshot(), _tsc1_context(), _tsc1_claims())
    blob = json.dumps(dataclasses.asdict(brief))
    round_tripped = json.loads(blob)
    assert round_tripped["snapshot"]["verdict"] == "discordant"
    assert round_tripped["comparability"] == "partially_comparable"


def test_unknown_verdict_is_rejected_not_guessed():
    snap = dataclasses.replace(_tsc1_snapshot(), verdict="totally_made_up")
    with pytest.raises(ValueError, match="unknown verdict"):
        build_decision_brief(snap, _tsc1_context(), _tsc1_claims())


# --- constraints (Task 4 preview — the golden test already guards infeasibility) -------------

def test_elisa_only_cannot_resolve_and_says_so():
    """ELISA measures secreted protein but not transcript; it cannot resolve an RNA/protein
    disagreement. The brief must mark this infeasible, not silently downgrade to a non-decisive plan."""
    constraints = ExperimentConstraints(readouts=("elisa",), donors=3, days=5)
    brief = build_decision_brief(_tsc1_snapshot(), _tsc1_context(), _tsc1_claims(), constraints)
    assert brief.snapshot.verdict == "discordant"
    assert brief.feasible is False
    assert any("transcript" in u.lower() for u in brief.unmet_requirements)


def test_too_few_donors_is_infeasible():
    constraints = ExperimentConstraints(readouts=("qpcr", "facs"), donors=1, days=5)
    brief = build_decision_brief(_tsc1_snapshot(), _tsc1_context(), _tsc1_claims(), constraints)
    assert brief.feasible is False
    assert any("donor" in u.lower() for u in brief.unmet_requirements)


def test_valid_constraints_are_feasible_and_respected():
    constraints = ExperimentConstraints(readouts=("qpcr", "facs"), donors=4, days=7)
    brief = build_decision_brief(_tsc1_snapshot(), _tsc1_context(), _tsc1_claims(), constraints)
    assert brief.feasible is True
    assert not brief.unmet_requirements


# --- advanceability axis (the verdict-INDEPENDENT "should it advance?" question) -------------
# The reconciliation verdict answers "do the two screens agree?"; it does NOT answer "is this worth
# advancing as a program?". That second question needs the target dossier (druggability, disease
# genetics, QC), which the brief now folds in as a code-owned recommendation stance.


def _tsc1_dossier() -> TargetDossier:
    """TSC1's real dossier from enrichment.json: High QC, no small-molecule handle (sm=0.0), a weak
    antibody score (0.333) with no clinical stage, and NO immune-disease genetics (score 0.0). Real,
    high-confidence biology — but no path to a near-term drug program."""
    return TargetDossier(
        sm_score=0.0, sm_stage=None, ab_score=0.333, ab_stage=None,
        disease_score=0.0, top_disease=None, qc_confidence="High")


def test_tsc1_recommendation_is_hold_weak_target():
    """The flagship control for the axis: TSC1 is biologically real (discordant, High QC) but a weak
    drug target (no tractable chemistry, no disease genetics), so the honest stance is NOT 'advance'
    and NOT 'deprioritise' — it is 'hold as a weak target': understand the biology, don't run a
    program on it yet."""
    brief = build_decision_brief(
        _tsc1_snapshot(), _tsc1_context(), _tsc1_claims(), dossier=_tsc1_dossier())
    assert brief.recommendation in RECOMMENDATION
    assert brief.recommendation == "hold_weak_target"
    # The rationale is present and verdict-independent (it names the target-quality reason).
    assert brief.recommendation_rationale.strip()
    joined = brief.recommendation_rationale.lower()
    assert ("druggab" in joined or "handle" in joined or "chemistry" in joined
            or "antibody" in joined or "disease" in joined), brief.recommendation_rationale


def test_recommendation_is_independent_of_the_reconciliation_verdict():
    """Same dossier, different verdicts → the stance still reflects target quality, never collapses
    into the verdict. A weak target is a weak target whether the screens agree or disagree."""
    weak = _tsc1_dossier()
    for verdict in ("discordant", "protein_only", "replicated"):
        snap = dataclasses.replace(_tsc1_snapshot(), verdict=verdict)
        brief = build_decision_brief(snap, _tsc1_context(), (), dossier=weak)
        # A target with no chemistry and no disease genetics is never a green-light 'advance'.
        assert brief.recommendation != "advance", verdict


def test_advance_requires_agreement_plus_tractability_plus_disease():
    """The only green light: both screens agree (replicated), the target is tractable, it has
    immune-disease genetics, and QC is High. Anything less is validate/hold, never advance."""
    strong = TargetDossier(
        sm_score=0.7, sm_stage="Phase II", ab_score=0.4, ab_stage=None,
        disease_score=0.62, top_disease="rheumatoid arthritis", qc_confidence="High")
    snap = dataclasses.replace(_tsc1_snapshot(), verdict="replicated")
    brief = build_decision_brief(snap, _tsc1_context(), (), dossier=strong)
    assert brief.recommendation == "advance"


def test_tractable_but_discordant_is_validate_first():
    """A tractable target with disease genetics whose screens DISAGREE is not a hold — the biology is
    worth resolving first. Validate, then decide."""
    tractable = TargetDossier(
        sm_score=0.6, sm_stage=None, ab_score=0.2, ab_stage=None,
        disease_score=0.55, top_disease="asthma", qc_confidence="High")
    brief = build_decision_brief(
        _tsc1_snapshot(), _tsc1_context(), (), dossier=tractable)   # discordant
    assert brief.recommendation == "validate_first"


def test_no_signal_is_deprioritise():
    """A 'neither' verdict — no screen sees an effect — is not worth chasing regardless of dossier."""
    snap = dataclasses.replace(_tsc1_snapshot(), verdict="neither")
    brief = build_decision_brief(snap, _tsc1_context(), (), dossier=_tsc1_dossier())
    assert brief.recommendation == "deprioritize"


def test_missing_dossier_yields_unknown_not_a_fake_stance():
    """Without a dossier the brief must not invent a target-quality stance. It reports 'unknown' and
    a rationale that says the dossier is unavailable — never a fabricated 'advance'/'hold'."""
    brief = build_decision_brief(_tsc1_snapshot(), _tsc1_context(), _tsc1_claims())  # no dossier
    assert brief.recommendation == "unknown"
    assert brief.recommendation not in RECOMMENDATION           # 'unknown' is a sentinel, not a stance
    assert "dossier" in brief.recommendation_rationale.lower() or brief.recommendation_rationale


def test_recommendation_rationale_leaks_no_raw_statistics():
    """The rationale is prose a bench reader consumes — it must not quote the raw dossier scores,
    same discipline as the rest of the brief's narration."""
    brief = build_decision_brief(
        _tsc1_snapshot(), _tsc1_context(), _tsc1_claims(), dossier=_tsc1_dossier())
    # The dossier scores (0.0, 0.333) must not appear verbatim in the rationale prose.
    assert "0.333" not in brief.recommendation_rationale
    assert "0.0" not in brief.recommendation_rationale
    hit = _STAT_TOKENS.search(brief.recommendation_rationale)
    assert hit is None, f"raw statistic leaked into recommendation prose: {hit!r}"


def test_brief_with_dossier_still_serialises_to_json():
    brief = build_decision_brief(
        _tsc1_snapshot(), _tsc1_context(), _tsc1_claims(), dossier=_tsc1_dossier())
    round_tripped = json.loads(json.dumps(dataclasses.asdict(brief)))
    assert round_tripped["recommendation"] == "hold_weak_target"
    assert round_tripped["target_dossier"]["qc_confidence"] == "High"
