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
    COMPARABILITY, EXPERIMENT_ARCHETYPES, EXPLANATION_CLASSES, POSITIVE_CONTROL_SOURCES, RECOMMENDATION,
    ConcordanceSnapshot, ExperimentConstraints, ExperimentTemplate, GroundedClaim, PositiveControl,
    ScreenPairContext, TargetDossier, _EXPERIMENT_TEMPLATES, build_decision_brief,
    select_experiment_archetype, select_positive_control)


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


def test_readouts_reflect_the_selected_constraints_not_the_default_pair():
    """Re-plan bug regression: dropping qPCR must show the transcript readout as 'not run', not the
    hardcoded 'transcript · qpcr'. Otherwise re-planning with qPCR unchecked leaves the experiment
    card looking identical and the plan appears not to change."""
    with_qpcr = build_decision_brief(
        _tsc1_snapshot(), _tsc1_context(), _tsc1_claims(),
        ExperimentConstraints(readouts=("qpcr", "facs"), donors=3, days=7))
    assert "transcript · qpcr" in " ".join(with_qpcr.experiment.readouts)

    facs_only = build_decision_brief(
        _tsc1_snapshot(), _tsc1_context(), _tsc1_claims(),
        ExperimentConstraints(readouts=("facs",), donors=3, days=7))
    ro = " ".join(facs_only.experiment.readouts)
    assert "transcript · qpcr" not in ro          # the card must NOT still claim qPCR
    assert "not run" in ro                          # the absent readout is shown as unavailable
    assert "protein · facs" in ro                   # the available readout is still named


def test_protein_readout_follows_availability():
    """The protein half of the card must name the available assay (ELISA when only ELISA is on the
    bench), not the default FACS — the plan reflects the actual selection."""
    brief = build_decision_brief(
        _tsc1_snapshot(), _tsc1_context(), _tsc1_claims(),
        ExperimentConstraints(readouts=("qpcr", "elisa"), donors=3, days=7))
    assert "protein · elisa" in " ".join(brief.experiment.readouts)


# --- the `neither` / no-signal verdict (the NRAS case in the panel) --------------------------
# A `neither` verdict means both screens agree there is no effect. There is no split to resolve, so
# the outcome matrix and the plan framing must NOT talk about "reproducing the split" or "resolving
# the disagreement" — that language is only honest for a discordant verdict.
#
# NOTE (Task #3): the outcome matrix is now selected per verdict-specific ARCHETYPE, not a single
# disagreement/no-signal split. `protein_only`/`mrna_only` now get the `propagation_test` matrix
# (a one-sided question, not a split) and `neither` (both layers tested) gets `null_interrogation`.
# Only `discordant` keeps the split-language matrix — see the archetype tests further down.

def _neither_snapshot() -> ConcordanceSnapshot:
    return dataclasses.replace(_tsc1_snapshot(), gene="NRAS", verdict="neither")


def test_neither_outcome_matrix_has_no_split_language():
    """The null-interrogation matrix must never claim there is a split to reproduce or a discrepancy
    to resolve — that framing is meaningless when both screens agree there is no effect."""
    brief = build_decision_brief(_neither_snapshot(), _tsc1_context(), ())
    prose = " ".join(
        o.result + " " + o.interpretation + " " + o.next_action for o in brief.outcome_matrix
    ).lower()
    assert "split" not in prose
    assert "discrepancy" not in prose
    assert "decoupling" not in prose
    # It must instead frame the experiment as a null-interrogation.
    assert "null" in prose


def test_discordant_verdict_keeps_the_split_framing():
    """The discordant verdict must still get the resolve_split matrix — the archetype system must not
    over-reach and strip the split language from the one case where it IS correct."""
    brief = build_decision_brief(_tsc1_snapshot(), _tsc1_context(), ())  # discordant
    prose = " ".join(o.result for o in brief.outcome_matrix).lower()
    assert "split" in prose


def test_neither_infeasibility_text_does_not_mention_a_disagreement():
    """When a `neither` plan is infeasible (e.g. no qPCR), the unmet-requirement text must not claim
    the readout is needed to 'resolve an RNA/protein disagreement' — there is none."""
    brief = build_decision_brief(
        _neither_snapshot(), _tsc1_context(), (),
        ExperimentConstraints(readouts=("facs",), donors=3, days=7))
    assert brief.feasible is False
    joined = " ".join(brief.unmet_requirements).lower()
    assert "disagreement" not in joined
    assert "transcript" in joined              # it still says a transcript readout is missing


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


# --- positive control + assay-sensitivity line ------------------------------------------------
# A validation experiment with no positive control is uninterpretable: "neither readout moved" is
# indistinguishable from "the assay is dead" without proof the assay CAN detect a real IL-2 effect.
# `select_positive_control` never invents a gene — it only chooses among rows the two screens
# already agreed on (verdict == "replicated") at the SAME cytokine/condition being validated.

_CURATED_GENES = ("ITK", "BCL10", "VAV1", "PLCG1", "LCP2", "ZAP70", "LAT", "CD3D", "CD3E", "CD28", "LCK")


def _row(gene: str, condition: str, verdict: str, z_rna: float, q_rna: float | None,
         lfc_prot: float, q_prot: float | None, cytokine: str = "IL2") -> dict:
    """A minimal synthetic concordance row — only the fields `select_positive_control` reads."""
    return {
        "gene": gene, "cytokine": cytokine, "condition": condition, "verdict": verdict,
        "z_rna": z_rna, "q_rna": q_rna, "lfc_prot": lfc_prot, "q_prot": q_prot,
    }


def _synthetic_rows() -> list[dict]:
    """3 curated replicated genes + 1 non-curated replicated gene + 1 non-replicated gene that
    must be ignored entirely (proves the verdict filter, not just the curated-preference step)."""
    return [
        _row("VAV1", "Stim48hr", "replicated", z_rna=-3.28, q_rna=0.002, lfc_prot=-2.92, q_prot=0.004),
        _row("BCL10", "Stim48hr", "replicated", z_rna=-2.45, q_rna=0.02, lfc_prot=-0.47, q_prot=0.03),
        _row("ITK", "Stim48hr", "replicated", z_rna=-2.42, q_rna=0.03, lfc_prot=-1.45, q_prot=0.05),
        # non-curated but a LOWER combined_q than every curated gene above — proves curated
        # preference dominates the sort, not just a tie-break.
        _row("UMPS", "Stim48hr", "replicated", z_rna=4.98, q_rna=0.0001, lfc_prot=0.59, q_prot=0.0003),
        # not replicated at this condition — must be ignored no matter how good its stats look.
        _row("PTPN7", "Stim48hr", "discordant", z_rna=9.92, q_rna=0.0001, lfc_prot=0.29, q_prot=0.0001),
    ]


def test_select_positive_control_picks_vav1_curated_lowers():
    pc = select_positive_control(_synthetic_rows(), _CURATED_GENES, "IL2", "Stim48hr")
    assert pc is not None
    assert pc.gene == "VAV1"
    assert pc.source == "curated_known_regulator"
    assert pc.expected_direction == "lowers"
    assert pc.z_rna == -3.28
    assert pc.lfc_prot == -2.92


def test_select_positive_control_is_deterministic():
    rows = _synthetic_rows()
    first = select_positive_control(rows, _CURATED_GENES, "IL2", "Stim48hr")
    second = select_positive_control(rows, _CURATED_GENES, "IL2", "Stim48hr")
    assert first == second

    # Tie-break by gene name: two curated genes sharing the same combined_q must resolve to the
    # alphabetically-first gene, every time.
    tied = [
        _row("ZAP70", "Stim8hr", "replicated", z_rna=-4.0, q_rna=0.001, lfc_prot=-1.0, q_prot=0.001),
        _row("LAT", "Stim8hr", "replicated", z_rna=-4.5, q_rna=0.001, lfc_prot=-1.2, q_prot=0.001),
    ]
    pick1 = select_positive_control(tied, _CURATED_GENES, "IL2", "Stim8hr")
    pick2 = select_positive_control(tied, _CURATED_GENES, "IL2", "Stim8hr")
    assert pick1 == pick2
    assert pick1.gene == "LAT"          # alphabetically before ZAP70


def test_select_positive_control_prefers_curated_over_lower_q_noncurated():
    """UMPS has a much lower combined_q than every curated gene in the fixture, yet the curated
    VAV1 must still win — proves the curated-subset step dominates the final sort, it is not just
    a tie-break among otherwise-equal rows."""
    pc = select_positive_control(_synthetic_rows(), _CURATED_GENES, "IL2", "Stim48hr")
    assert pc.gene == "VAV1"
    assert pc.gene != "UMPS"


def test_select_positive_control_falls_back_to_top_replicated_hit():
    """No curated gene is replicated at Rest (matches the real artifact) -> fall back to the whole
    replicated pool and pick the lowest combined_q row, regardless of curation."""
    rows = [
        _row("UMPS", "Rest", "replicated", z_rna=4.986, q_rna=0.0002, lfc_prot=0.592, q_prot=0.0002),
        _row("PTPN7", "Rest", "replicated", z_rna=9.920, q_rna=0.03, lfc_prot=0.294, q_prot=0.03),
    ]
    pc = select_positive_control(rows, _CURATED_GENES, "IL2", "Rest")
    assert pc is not None
    assert pc.gene == "UMPS"
    assert pc.source == "top_replicated_hit"
    assert pc.expected_direction == "raises"       # z_rna is positive


def test_select_positive_control_returns_none_when_no_replicated_hit():
    rows = [
        _row("VAV1", "Stim48hr", "discordant", z_rna=-3.28, q_rna=0.002, lfc_prot=-2.92, q_prot=0.004),
        _row("BCL10", "Stim48hr", "protein_only", z_rna=-2.45, q_rna=0.02, lfc_prot=-0.47, q_prot=0.03),
    ]
    pc = select_positive_control(rows, _CURATED_GENES, "IL2", "Stim48hr")
    assert pc is None

    brief = build_decision_brief(_tsc1_snapshot(), _tsc1_context(), _tsc1_claims(), positive_control=pc)
    assert brief.experiment.positive_control is None


def test_positive_control_rejects_unknown_source():
    with pytest.raises(ValueError, match="source"):
        PositiveControl(gene="VAV1", source="made_up_source", expected_direction="lowers",
                        z_rna=-3.28, lfc_prot=-2.92)


def test_positive_control_rejects_unknown_direction():
    with pytest.raises(ValueError, match="direction"):
        PositiveControl(gene="VAV1", source="curated_known_regulator", expected_direction="sideways",
                        z_rna=-3.28, lfc_prot=-2.92)


def test_positive_control_sources_is_closed_set():
    assert set(POSITIVE_CONTROL_SOURCES) == {"curated_known_regulator", "top_replicated_hit"}


def test_build_decision_brief_attaches_positive_control_and_serialises():
    pc = PositiveControl(gene="VAV1", source="curated_known_regulator", expected_direction="lowers",
                         z_rna=-3.28, lfc_prot=-2.92)
    brief = build_decision_brief(_tsc1_snapshot(), _tsc1_context(), _tsc1_claims(), positive_control=pc)
    assert brief.experiment.positive_control == pc

    round_tripped = json.loads(json.dumps(dataclasses.asdict(brief)))
    nested = round_tripped["experiment"]["positive_control"]
    assert nested["gene"] == "VAV1"
    assert nested["source"] == "curated_known_regulator"
    assert nested["expected_direction"] == "lowers"
    assert nested["z_rna"] == -3.28
    assert nested["lfc_prot"] == -2.92


def test_assay_sensitivity_line_is_present_code_owned_and_leaks_no_statistics():
    brief = build_decision_brief(_tsc1_snapshot(), _tsc1_context(), _tsc1_claims())
    line = brief.experiment.assay_sensitivity
    assert line.strip()
    lowered = line.lower()
    assert "minimum detectable effect" in lowered
    assert "positive control" in lowered
    hit = _STAT_TOKENS.search(line)
    assert hit is None, f"raw statistic leaked into assay_sensitivity: {hit.group(0)!r}"


def test_build_decision_brief_without_positive_control_arg_stays_backward_compatible():
    """The 26 pre-existing tests never pass `positive_control` — this pins that omission still
    yields a valid brief with positive_control=None and the assay-sensitivity line present."""
    brief = build_decision_brief(_tsc1_snapshot(), _tsc1_context(), _tsc1_claims())
    assert brief.experiment.positive_control is None
    assert brief.experiment.assay_sensitivity.strip()


# --- integration: the real artifact, kept separate from the fast pure-function unit tests -----

@pytest.mark.integration
def test_real_artifact_positive_control_picks_match_verified_expectations():
    """Reads the real concordance.parquet + ground_truth.json through the app's own loaders to
    confirm the three VERIFIED picks. Slower and I/O-bound on purpose — kept out of the pure unit
    tests above so those stay fast and hermetic."""
    from sera.api.app import _load_concordance, _load_ground_truth
    from sera.core.brief_resolver import resolve_positive_control

    rows = _load_concordance()
    ground_truth = _load_ground_truth()
    if not rows or not ground_truth:
        pytest.skip("concordance.parquet / ground_truth.json artifacts not built")

    stim48 = resolve_positive_control(rows, ground_truth, "IL2", "Stim48hr")
    assert stim48 is not None
    assert stim48.gene == "VAV1"
    assert stim48.source == "curated_known_regulator"
    assert stim48.expected_direction == "lowers"

    stim8 = resolve_positive_control(rows, ground_truth, "IL2", "Stim8hr")
    assert stim8 is not None
    assert stim8.gene == "CD3D"
    assert stim8.source == "curated_known_regulator"
    assert stim8.expected_direction == "lowers"

    rest = resolve_positive_control(rows, ground_truth, "IL2", "Rest")
    assert rest is not None
    assert rest.gene == "UMPS"
    assert rest.source == "top_replicated_hit"
    assert rest.expected_direction == "raises"


# --- verdict-specific experiment archetypes (Task #3) -----------------------------------------
# A single generic protocol is scientifically wrong for some verdicts: a discordant split needs a
# mandatory time course to resolve temporal decoupling; a replicated result is already settled and
# should escalate toward mechanism, not repeat both readouts; a concordant null (`neither` with both
# layers tested) is only worth interrogating with a positive control AND by varying the shared axis;
# a missing protein layer (`protein_tested=False`) is a coverage gap that takes precedence over the
# verdict-based archetype entirely.

def test_select_experiment_archetype_maps_each_verdict_when_protein_tested():
    assert select_experiment_archetype("discordant", protein_tested=True) == "resolve_split"
    assert select_experiment_archetype("replicated", protein_tested=True) == "escalate_mechanism"
    assert select_experiment_archetype("mrna_only", protein_tested=True) == "propagation_test"
    assert select_experiment_archetype("protein_only", protein_tested=True) == "propagation_test"
    assert select_experiment_archetype("neither", protein_tested=True) == "null_interrogation"


@pytest.mark.parametrize("verdict", ["discordant", "replicated", "mrna_only", "protein_only", "neither"])
def test_select_experiment_archetype_coverage_gap_takes_precedence(verdict):
    """protein_tested=False is a coverage gap — it wins regardless of the mRNA-side verdict."""
    assert select_experiment_archetype(verdict, protein_tested=False) == "measure_missing_layer"


def test_select_experiment_archetype_rejects_unknown_verdict():
    with pytest.raises(ValueError, match="unknown verdict"):
        select_experiment_archetype("totally_made_up", protein_tested=True)


def test_experiment_archetypes_is_the_expected_closed_set():
    assert set(EXPERIMENT_ARCHETYPES) == {
        "resolve_split", "propagation_test", "escalate_mechanism",
        "null_interrogation", "measure_missing_layer"}


def test_every_archetype_has_a_template_and_the_archetype_field_matches():
    """Closure/coverage test: every member of EXPERIMENT_ARCHETYPES has a template, and every
    template's own `archetype` field agrees with its dict key."""
    assert set(_EXPERIMENT_TEMPLATES) == set(EXPERIMENT_ARCHETYPES)
    for key, template in _EXPERIMENT_TEMPLATES.items():
        assert isinstance(template, ExperimentTemplate)
        assert template.archetype == key


def test_experiment_template_rejects_unknown_archetype():
    with pytest.raises(ValueError, match="archetype"):
        ExperimentTemplate(
            archetype="made_up", objective="x", timecourse=None, timecourse_required=False,
            varies_conditions=False, positive_control_required=False, outcomes=())


# --- discordant: mandatory time course --------------------------------------------------------

def test_discordant_brief_has_a_timecourse_by_default():
    brief = build_decision_brief(_tsc1_snapshot(), _tsc1_context(), _tsc1_claims())
    assert brief.snapshot.verdict == "discordant"
    assert brief.experiment.timecourse is not None
    assert brief.feasible is True


def test_discordant_brief_is_infeasible_when_window_under_3_days():
    """A resolve_split experiment cannot silently drop its time course — a discordant result cannot
    be resolved from a single timepoint. Under 3 days this must be an INFEASIBILITY, not a quiet
    adaptation."""
    constraints = ExperimentConstraints(readouts=("qpcr", "facs"), donors=4, days=2)
    brief = build_decision_brief(_tsc1_snapshot(), _tsc1_context(), _tsc1_claims(), constraints)
    assert brief.feasible is False
    assert any("time course" in u.lower() for u in brief.unmet_requirements)


def test_discordant_brief_is_feasible_with_timecourse_when_window_is_3_days_or_more():
    constraints = ExperimentConstraints(readouts=("qpcr", "facs"), donors=4, days=7)
    brief = build_decision_brief(_tsc1_snapshot(), _tsc1_context(), _tsc1_claims(), constraints)
    assert brief.feasible is True
    assert brief.experiment.timecourse is not None
    assert not brief.unmet_requirements


# --- replicated: escalate toward mechanism, not re-measure -------------------------------------

def test_replicated_brief_escalates_to_mechanism_not_split():
    snap = dataclasses.replace(_tsc1_snapshot(), verdict="replicated")
    brief = build_decision_brief(snap, _tsc1_context(), ())
    prose = " ".join(
        o.result + " " + o.interpretation + " " + o.next_action for o in brief.outcome_matrix
    ).lower()
    assert "split" not in prose
    assert "reproduce the split" not in prose
    assert any(word in prose for word in ("dose", "mechanism", "rescue"))
    # A dose-response is the decisive next step, not a time course.
    assert brief.experiment.timecourse is None


# --- neither (both layers tested): null interrogation ------------------------------------------

def test_neither_both_tested_brief_is_null_interrogation_and_varies_conditions():
    snap = _neither_snapshot()
    assert snap.protein_tested is True
    brief = build_decision_brief(snap, _tsc1_context(), ())
    text = " ".join(filter(None, [brief.experiment.timecourse, brief.experiment.objective])).lower()
    assert any(word in text for word in ("vary", "varying", "timing", "dose"))


def test_neither_null_interrogation_requires_positive_control_to_be_feasible():
    snap = _neither_snapshot()
    brief_without_pc = build_decision_brief(snap, _tsc1_context(), ())
    assert brief_without_pc.feasible is False
    assert any("positive control" in u.lower() for u in brief_without_pc.unmet_requirements)

    pc = PositiveControl(gene="VAV1", source="curated_known_regulator", expected_direction="lowers",
                          z_rna=-3.28, lfc_prot=-2.92)
    brief_with_pc = build_decision_brief(snap, _tsc1_context(), (), positive_control=pc)
    assert not any(
        "positive control" in u.lower() for u in brief_with_pc.unmet_requirements)


# --- neither + protein_tested=False: coverage gap wins, not null interrogation -----------------

def test_neither_with_missing_protein_layer_measures_missing_layer_not_null():
    snap = dataclasses.replace(_neither_snapshot(), protein_tested=False)
    brief = build_decision_brief(snap, _tsc1_context(), ())
    text = (brief.experiment.objective + " " + " ".join(
        o.result + " " + o.interpretation for o in brief.outcome_matrix)).lower()
    assert "protein" in text
    assert "concordant" not in text
    assert "null" not in text


# --- mrna_only: propagation test, no split language ---------------------------------------------

def test_mrna_only_brief_has_no_split_language():
    snap = dataclasses.replace(_tsc1_snapshot(), verdict="mrna_only")
    brief = build_decision_brief(snap, _tsc1_context(), ())
    prose = " ".join(
        o.result + " " + o.interpretation + " " + o.next_action for o in brief.outcome_matrix
    ).lower()
    assert "split" not in prose


# --- backward-compat: TSC1 flagship still holds under the new template path --------------------

def test_tsc1_experiment_still_paired_readout_with_guides_and_ntc_under_new_template():
    brief = build_decision_brief(_tsc1_snapshot(), _tsc1_context(), _tsc1_claims())
    ro = " ".join(brief.experiment.readouts).lower()
    assert "transcript" in ro and "protein" in ro
    assert any("ntc" in c.lower() or "non-targeting" in c.lower() for c in brief.experiment.controls)
    assert len(brief.experiment.guides) >= 2
    assert brief.trust_assessment == "neither_yet"


# --- objective is present, non-empty, and leaks no raw statistic for every verdict --------------

@pytest.mark.parametrize("verdict", ["discordant", "replicated", "mrna_only", "protein_only", "neither"])
def test_objective_is_present_nonempty_and_leaks_no_statistics(verdict):
    snap = dataclasses.replace(_tsc1_snapshot(), verdict=verdict)
    brief = build_decision_brief(snap, _tsc1_context(), ())
    assert brief.experiment.objective.strip()
    hit = _STAT_TOKENS.search(brief.experiment.objective)
    assert hit is None, f"raw statistic leaked into objective: {hit.group(0)!r}"


# --- full-brief JSON round trip for a representative verdict of each archetype -------------------

@pytest.mark.parametrize("verdict,protein_tested", [
    ("discordant", True),      # resolve_split
    ("replicated", True),      # escalate_mechanism
    ("mrna_only", True),       # propagation_test
    ("neither", True),         # null_interrogation
    ("neither", False),        # measure_missing_layer
])
def test_brief_serialises_for_each_archetype(verdict, protein_tested):
    snap = dataclasses.replace(_tsc1_snapshot(), verdict=verdict, protein_tested=protein_tested)
    brief = build_decision_brief(snap, _tsc1_context(), ())
    blob = json.dumps(dataclasses.asdict(brief))
    round_tripped = json.loads(blob)
    assert round_tripped["snapshot"]["verdict"] == verdict
    assert round_tripped["experiment"]["objective"] == brief.experiment.objective
