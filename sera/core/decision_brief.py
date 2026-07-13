"""The decision brief — Sera's scientific center.

Given a reconciliation that already has a code-computed verdict, answer the question the verdict
alone does not: *given this disagreement, what do we still not know, and which feasible experiment
resolves it?*

Design contract (why this module looks the way it does):

- **Deterministic and pure.** No LLM, no filesystem, no globals. Every set the brief draws from is
  closed and owned by this module; nothing here authors gene IDs, verdicts, explanation classes, or
  citations. The endpoint resolves the inputs (row, screen context, stored claims) and hands them
  in; `build_decision_brief` only reasons over them. This is what makes the golden test a *control*
  rather than a mock.

- **The verdict is never recomputed.** It rides in on `ConcordanceSnapshot.verdict`, straight from
  `core.concordance.classify`. Recomputing it here would create a second source of truth that could
  silently drift from the deterministic core.

- **Post-transcriptional regulation is a hypothesis, not the verdict.** A `protein_only` (or
  `discordant`) result means the two assays disagree under the conditions measured — it does NOT
  establish a mechanism. The explanation classes always retain at least one technical/contextual
  alternative so an attractive molecular story cannot crowd out "the screens just aren't comparable".

- **Comparability comes from the canonical adapter, not provenance.json.** The Schmidt protein screen
  has NO time axis (single FACS readout copied across conditions), so a Stim48hr reconciliation
  compares 48-hour RNA against an untimed protein sort. That is the headline caveat, and it is real.

- **Citations are background for a hypothesis, never support for the verdict.** The stored TSC1
  claims cite TSC2/mTOR papers; they motivate the mTOR hypothesis, they do not prove TSC1 -> IL-2.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# --- closed sets the code owns ---------------------------------------------------------------

#: Every comparability conclusion the audit may reach. Categorical on purpose — a transparent
#: checklist verdict is scientifically safer for a hackathon than a fake numeric score.
COMPARABILITY: tuple[str, ...] = (
    "comparable", "partially_comparable", "context_mismatch", "insufficient_metadata")

#: What the brief concludes about which screen to trust. `neither_yet` is the honest default before
#: any validation experiment has run — the whole point is that the disagreement is unresolved.
TRUST: tuple[str, ...] = ("trust_protein", "trust_mrna", "neither_yet", "both_agree")

#: Competing explanation classes per verdict. The LLM (if ever involved downstream) may only SELECT
#: and phrase these; it never adds a member. Each discordant/one-sided set keeps >=1 technical or
#: contextual alternative so mechanism is never the only story on offer.
EXPLANATION_CLASSES: dict[str, tuple[str, ...]] = {
    "discordant": (
        "post_transcriptional_or_secretion", "temporal_feedback",
        "context_mismatch", "perturbation_strength", "assay_artifact"),
    "protein_only": (
        "translation_stability_secretion", "transcript_sensitivity",
        "timing_mismatch", "protein_screen_false_positive"),
    "mrna_only": (
        "no_propagation_to_protein", "protein_buffering_or_delay",
        "protein_assay_insensitive", "mrna_screen_false_positive"),
    "replicated": ("confirmatory",),
    "neither": ("concordant_absence",),
}

#: Explanation classes that are TECHNICAL/CONTEXTUAL (not a molecular-mechanism story). At least one
#: of these must survive into every multi-explanation brief — the guard against mechanistic
#: storytelling. Keyed by class name so the check is closed-set, not a substring heuristic.
_TECHNICAL_CLASSES: frozenset[str] = frozenset({
    "context_mismatch", "perturbation_strength", "assay_artifact",
    "transcript_sensitivity", "timing_mismatch", "protein_screen_false_positive",
    "protein_assay_insensitive", "mrna_screen_false_positive", "concordant_absence",
})

#: Human-readable, code-owned label for each explanation class. No LLM authors these.
EXPLANATION_LABELS: dict[str, str] = {
    "post_transcriptional_or_secretion":
        "Genuine post-transcriptional or secretion control (protein-level regulation the transcript "
        "screen cannot see)",
    "temporal_feedback":
        "Temporal feedback — transcript and protein are out of phase at the sampled time",
    "context_mismatch":
        "Biological context mismatch — the two screens were run in non-identical cell states",
    "perturbation_strength":
        "Perturbation-strength difference — knockdown depth differs between the screens",
    "assay_artifact":
        "Assay-specific artifact in one screen (e.g. FACS gating or a transcript mapping issue)",
    "translation_stability_secretion":
        "Translation, protein-stability or secretion mechanism",
    "transcript_sensitivity":
        "Insufficient transcript-screen sensitivity to a real but small mRNA change",
    "timing_mismatch":
        "Timing mismatch — the transcript change may precede or lag the sampled window",
    "protein_screen_false_positive":
        "Protein-screen false positive",
    "no_propagation_to_protein":
        "The transcript change does not propagate to protein",
    "protein_buffering_or_delay":
        "Protein buffering or a delayed protein response",
    "protein_assay_insensitive":
        "The protein assay lacks sensitivity to the change",
    "mrna_screen_false_positive":
        "mRNA-screen false positive",
    "confirmatory":
        "Both screens agree — validation here is confirmatory",
    "concordant_absence":
        "Concordant absence — neither screen sees an effect",
}

#: Readout tokens a scientist may declare available. Closed enum, not free-form strings.
READOUTS: tuple[str, ...] = ("qpcr", "elisa", "facs", "western")

#: The verdict-INDEPENDENT advancement stance. The reconciliation verdict answers "do the two screens
#: agree?"; this answers the different question "is this worth advancing as a program?" — which needs
#: the target dossier (druggability, disease genetics, QC), NOT the screen agreement alone. Closed on
#: purpose: a code-owned stance the LLM may render but never author. `unknown` is a SENTINEL for a
#: missing dossier and is deliberately NOT a member — absence of input is not a judgment.
RECOMMENDATION: tuple[str, ...] = ("advance", "validate_first", "hold_weak_target", "deprioritize")
_RECOMMENDATION_UNKNOWN = "unknown"

#: A target is "tractable" (has a real drug handle) if either modality clears this score or already
#: has a clinical stage. Set at 0.5, not lower: a weak near-baseline score (e.g. an antibody score of
#: ~0.33, which even control-like genes carry, or an antibody route for an intracellular target) is
#: NOT a viable program path. The bar separates "a genuine handle" from "a weak signal", not "some"
#: from "none".
_TRACTABLE_SCORE = 0.5
#: A target has "disease rationale" if immune-disease genetics clear this score AND name a disease.
_DISEASE_SCORE = 0.3

#: The minimum donors a decisive paired experiment needs by default.
_MIN_DONORS = 3
SCHEMA_VERSION = 1


# --- inputs (the endpoint resolves these; the builder never reads files) ---------------------

@dataclass(frozen=True)
class ConcordanceSnapshot:
    """One reconciliation's code-computed result — the fields of `core.concordance.Concordance`
    that the brief needs, handed in by the endpoint. The verdict is authoritative and never
    recomputed here."""
    gene: str
    cytokine: str
    condition: str
    verdict: str
    rna_tested: bool
    protein_tested: bool
    rna_promotes: bool | None
    prot_promotes: bool | None
    rna_screen_id: str
    protein_screen_id: str


@dataclass(frozen=True)
class ScreenPairContext:
    """How the two screens were run, from `rim/adapters.py` (the authoritative context source, NOT
    provenance.json which drops the mRNA cell type / condition). `prot_has_time_axis` is False for a
    single-readout FACS screen like Schmidt — the fact that makes a Stim48hr reconciliation compare
    48-hour RNA against an untimed protein sort."""
    rna_cell_type: str | None
    rna_condition: str | None
    rna_regime: str
    prot_cell_type: str
    prot_condition: str
    prot_regime: str
    prot_has_time_axis: bool


@dataclass(frozen=True)
class GroundedClaim:
    """A stored, pre-resolved literature claim. Reused verbatim, never regenerated. `supports` names
    what the citation actually backs — for TSC1 that is `mtor_background`, NOT the IL-2 verdict."""
    text: str
    label: str          # e.g. "HYPOTHESIS — not used in verdict"
    db: str             # e.g. "pubmed"
    accession: str      # e.g. "12172553"
    title: str
    supports: str       # e.g. "mtor_background" — the claim's actual scope, never "verdict"


@dataclass(frozen=True)
class ExperimentConstraints:
    """A scientist's bench constraints, as closed enums / ints. Drives feasibility, never silently
    downgrades a decisive experiment into a non-decisive one."""
    readouts: tuple[str, ...]
    donors: int
    days: int

    def __post_init__(self) -> None:
        bad = tuple(r for r in self.readouts if r not in READOUTS)
        if bad:
            raise ValueError(f"unknown readout(s) {bad}; allowed: {READOUTS}")
        if self.donors < 0 or self.days < 0:
            raise ValueError("donors and days must be non-negative")


@dataclass(frozen=True)
class TargetDossier:
    """The verdict-INDEPENDENT target-quality facts, resolved from `enrichment.json` by the endpoint.
    Mirrors that artifact's fields exactly; the brief only reasons over them, never fetches them.

    Small-molecule and antibody druggability are tracked SEPARATELY (a target can be undruggable by
    chemistry yet have an antibody handle) — same discipline as the dossier. `*_stage` is a clinical
    stage string (e.g. 'Approved', 'Phase II') or None when no compound has reached the clinic.
    `qc_confidence` is the screen's own confidence tier ('High' / 'Low')."""
    sm_score: float
    sm_stage: str | None
    ab_score: float
    ab_stage: str | None
    disease_score: float
    top_disease: str | None
    qc_confidence: str

    @property
    def tractable(self) -> bool:
        """A real drug handle exists: either modality clears the score OR already has a clinical
        stage. Kept as a property so the selector reads as a scientific statement, not a threshold."""
        return (self.sm_score >= _TRACTABLE_SCORE or self.ab_score >= _TRACTABLE_SCORE
                or bool(self.sm_stage) or bool(self.ab_stage))

    @property
    def has_disease_rationale(self) -> bool:
        """Immune-disease genetics support the target: the association clears the score AND names a
        disease (a score with no disease is not actionable rationale)."""
        return self.disease_score >= _DISEASE_SCORE and bool(self.top_disease)


# --- outputs (all immutable) -----------------------------------------------------------------

@dataclass(frozen=True)
class Experiment:
    perturbation: str
    guides: tuple[str, ...]
    controls: tuple[str, ...]
    conditions: str
    readouts: tuple[str, ...]
    timecourse: str | None


@dataclass(frozen=True)
class OutcomeRow:
    result: str
    interpretation: str
    next_action: str


@dataclass(frozen=True)
class DecisionBrief:
    """The whole answer, immutable and JSON-serialisable. Consumed by the endpoint, the golden test,
    and (later) the frontend — one source of truth for the reconciliation's scientific text."""
    schema_version: int
    snapshot: ConcordanceSnapshot
    comparability: str
    comparability_reasons: tuple[str, ...]
    trust_assessment: str
    evidence_limits: tuple[str, ...]
    explanations: tuple[str, ...]
    explanation_labels: tuple[str, ...]
    experiment: Experiment
    feasible: bool
    unmet_requirements: tuple[str, ...]
    adaptations: tuple[str, ...]
    remaining_uncertainty: str
    outcome_matrix: tuple[OutcomeRow, ...]
    decision: str
    recommendation: str            # one of RECOMMENDATION, or 'unknown' when no dossier was supplied
    recommendation_rationale: str  # verdict-independent, prose (no raw dossier scores leak here)
    target_dossier: TargetDossier | None
    citations: tuple[GroundedClaim, ...]
    provenance: dict = field(default_factory=dict)


# --- the builder -----------------------------------------------------------------------------

def _comparability(snap: ConcordanceSnapshot, ctx: ScreenPairContext) -> tuple[str, tuple[str, ...]]:
    """Deterministic comparability audit from the screen context. Categorical verdict + reasons.

    The dominant, real caveat: the protein screen has no time axis, so comparing it against a timed
    RNA condition (Stim8hr/Stim48hr) is 'timed RNA vs an untimed protein readout'. Missing mRNA-side
    metadata degrades further. Different significance regimes is a secondary, honest caveat."""
    reasons: list[str] = []
    verdict = "comparable"

    timed_rna = (ctx.rna_condition or "").lower() not in ("", "rest", "unstim", "unstimulated")
    if not ctx.prot_has_time_axis and timed_rna:
        reasons.append(
            f"{ctx.rna_condition} RNA is compared against a single-readout protein screen with no "
            "time axis (its effect is the same across conditions) — the timepoints are not matched")
        verdict = "partially_comparable"

    if ctx.rna_cell_type is None or ctx.rna_condition is None:
        reasons.append("the mRNA screen's cell type / activation condition is unspecified at source")
        # Missing metadata is a stronger caveat than a timepoint mismatch, but never downgrade a
        # verdict that is already the more-severe context_mismatch.
        if verdict != "context_mismatch":
            verdict = "insufficient_metadata"

    if (ctx.rna_cell_type and ctx.prot_cell_type
            and ctx.rna_cell_type.lower() != ctx.prot_cell_type.lower()):
        reasons.append(
            f"cell types differ: mRNA={ctx.rna_cell_type} vs protein={ctx.prot_cell_type}")
        verdict = "context_mismatch"

    if ctx.rna_regime != ctx.prot_regime:
        reasons.append(
            f"significance was called under different regimes ({ctx.rna_regime} vs {ctx.prot_regime})")
        if verdict == "comparable":
            verdict = "partially_comparable"

    if not reasons:
        reasons.append("cell type, condition, and significance regime line up across the two screens")
    return verdict, tuple(reasons)


def _explanations(verdict: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Code-selected competing explanations for the verdict, plus their labels. Always retains at
    least one technical/contextual class when the verdict admits one (guards mechanistic
    storytelling). Selection is deterministic: the full closed set, ordered as defined."""
    classes = EXPLANATION_CLASSES.get(verdict, ())
    if len(classes) > 1 and not any(c in _TECHNICAL_CLASSES for c in classes):
        # Defensive: the closed sets are authored to always include a technical class, but never emit
        # a multi-explanation brief without one.
        raise AssertionError(f"verdict {verdict!r} has no technical alternative in its class set")
    labels = tuple(EXPLANATION_LABELS[c] for c in classes)
    return classes, labels


def _trust(snap: ConcordanceSnapshot) -> tuple[str, str]:
    """Trust conclusion + plain-language decision. Before any validation, a real disagreement is
    'neither_yet' — the honest state the discriminating experiment exists to resolve."""
    v = snap.verdict
    g = snap.gene
    if v == "replicated":
        return "both_agree", (
            f"Both screens agree on {g}; the reconciliation is confirmatory. Validate to reproduce "
            "in your hands, then advance.")
    if v == "discordant":
        return "neither_yet", (
            f"The two screens disagree on {g}'s direction, so neither reading is yet trustworthy on "
            "its own. Run the discriminating experiment below before committing to an endpoint.")
    if v == "protein_only":
        return "neither_yet", (
            f"{g} was detected only by the protein screen under these conditions. Whether that is a "
            "real protein-level effect or a one-sided artifact is unresolved — confirm before trusting.")
    if v == "mrna_only":
        return "neither_yet", (
            f"{g} was flagged only by the mRNA screen here. Whether the transcript change reaches "
            "protein is the open question — bridge to protein before trusting.")
    return "neither_yet", (
        f"Neither screen sees {g} doing much at this condition; validating is low-yield.")


def _recommendation(snap: ConcordanceSnapshot, dossier: TargetDossier | None) -> tuple[str, str]:
    """The verdict-INDEPENDENT advancement stance + a plain rationale that does NOT quote raw scores.

    This is the answer to 'should it advance?', which is a DIFFERENT question from 'do the screens
    agree?'. It folds the target dossier (druggability, disease genetics) into a code-owned stance so
    an attractive reconciliation cannot smuggle a weak drug target onto the 'advance' pile.

    Decision order (most-decisive first):
      - no dossier          -> 'unknown'          (honest: absence of input, never a fabricated call)
      - verdict 'neither'   -> 'deprioritize'     (no signal to chase, regardless of dossier)
      - not tractable AND no disease genetics -> 'hold_weak_target' (unresolved signal; no program)
      - replicated + tractable + disease + High QC -> 'advance'     (the only green light)
      - otherwise           -> 'validate_first'   (worth resolving the disagreement before committing)
    """
    g = snap.gene
    if dossier is None:
        return _RECOMMENDATION_UNKNOWN, (
            f"No target dossier is available for {g}, so the advancement call can't be made here — "
            "pull the druggability and disease-genetics evidence before deciding.")

    if snap.verdict == "neither":
        return "deprioritize", (
            f"Neither screen sees {g} move at this condition, so there is nothing to advance — "
            "deprioritise it.")

    if not dossier.tractable and not dossier.has_disease_rationale:
        return "hold_weak_target", (
            f"{g} is biologically interesting but still unresolved: the screens disagree, and it "
            "has no tractable drug handle (neither a small-molecule nor an antibody route) or "
            "immune-disease genetics behind it. Validate the split if the mechanism matters, but "
            "do not advance it as a target program yet.")

    strong_qc = (dossier.qc_confidence or "").lower() == "high"
    if (snap.verdict == "replicated" and dossier.tractable
            and dossier.has_disease_rationale and strong_qc):
        disease = dossier.top_disease or "an immune disease"
        return "advance", (
            f"Both screens agree on {g}, it is a tractable target with genetics linking it to "
            f"{disease}, and the screen quality is high — this one is ready to advance.")

    # Everything in between: there is a reason to care (a handle or disease link), but the screen
    # disagreement / one-sidedness is unresolved. Resolve it first.
    reason = "the two screens don't yet agree" if snap.verdict in ("discordant", "protein_only",
                                                                    "mrna_only") else "the evidence is not yet decisive"
    return "validate_first", (
        f"{g} is worth pursuing — there is a drug handle or a disease link — but {reason}, so run "
        "the discriminating experiment below and let the result decide before committing.")


def _experiment_and_outcomes(
    snap: ConcordanceSnapshot,
    constraints: ExperimentConstraints | None,
) -> tuple[Experiment, tuple[OutcomeRow, ...], bool, tuple[str, ...], tuple[str, ...], str]:
    """The discriminating experiment, its outcome matrix, and feasibility under constraints.

    The default is a paired-readout arrayed experiment: 2 guides + NTC, >=3 donors, matched cells +
    stimulation, transcript AND protein from the SAME wells, short timecourse. Constraints adapt it
    but NEVER silently make it non-decisive — an infeasible ask returns feasible=False with the
    unmet requirements spelled out."""
    g = snap.gene
    # A paired RNA+protein readout is what makes the experiment decisive for an RNA/protein
    # disagreement. Default to the strongest available pair.
    default_transcript = "qpcr"
    default_protein = "facs"
    controls = ["non-targeting control (NTC)"]

    unmet: list[str] = []
    adaptations: list[str] = []
    remaining = ""

    avail = set(constraints.readouts) if constraints else {default_transcript, default_protein}
    has_transcript = "qpcr" in avail
    has_protein = bool({"facs", "elisa", "western"} & avail)

    if constraints is not None:
        if not has_transcript:
            unmet.append(
                "a transcript readout (qPCR) is required to resolve an RNA/protein disagreement; "
                "none available")
        if not has_protein:
            unmet.append(
                "a protein readout (FACS/ELISA/Western) is required; none available")
        if constraints.donors < _MIN_DONORS:
            unmet.append(
                f"{_MIN_DONORS} donors are needed to separate biology from donor variation; "
                f"only {constraints.donors} available")

    # Choose the actual protein readout from what's available (prefer FACS for single-cell paired
    # measurement, else ELISA for secreted cytokine, else Western).
    protein_readout = next((r for r in ("facs", "elisa", "western") if r in avail), default_protein)
    readouts = (f"transcript · {default_transcript}", f"protein · {protein_readout}")

    # Timecourse: a short course only if there's time for it. A multi-timepoint arm needs a window;
    # drop it and say so if the deadline is tight.
    timecourse: str | None = "short time course (e.g. 24h and 48h) if feasible"
    if constraints is not None and constraints.days < 3:
        timecourse = None
        adaptations.append("dropped the multi-timepoint course — the available window is under 3 days")
        remaining = "without a timecourse a temporal-feedback explanation cannot be excluded"

    feasible = not unmet

    experiment = Experiment(
        perturbation=f"Arrayed CRISPRi against {g}",
        guides=(f"{g}-sg1", f"{g}-sg2"),
        controls=tuple(controls),
        conditions="matched stimulated CD4+ T cells (same cells and stimulation as the screens)",
        readouts=readouts,
        timecourse=timecourse,
    )

    # Outcome matrix — the point is different predictions under competing explanations.
    outcomes = (
        OutcomeRow(
            result="Both readouts reproduce the split in matched wells",
            interpretation="Supports a real molecular decoupling under matched conditions",
            next_action="Begin mechanism work on the leading hypothesis"),
        OutcomeRow(
            result="Transcript and protein now move together",
            interpretation="The original discrepancy was contextual or temporal, not molecular",
            next_action="Treat as context-dependent; reconcile at the matched condition"),
        OutcomeRow(
            result="Neither readout reproduces",
            interpretation="The original signal may be technical",
            next_action="Deprioritise or redesign the screen"),
        OutcomeRow(
            result="Only one guide reproduces",
            interpretation="Possible guide-specific artifact",
            next_action="Do not advance until a second guide agrees"),
    )
    return experiment, outcomes, feasible, tuple(unmet), tuple(adaptations), remaining


def build_decision_brief(
    snapshot: ConcordanceSnapshot,
    screen_context: ScreenPairContext,
    claims: tuple[GroundedClaim, ...] = (),
    constraints: ExperimentConstraints | None = None,
    dossier: TargetDossier | None = None,
) -> DecisionBrief:
    """Assemble the decision brief. Pure and deterministic: no I/O, no LLM, no globals.

    `snapshot` carries the code-computed verdict (never recomputed here). `screen_context` comes from
    the canonical adapter. `claims` are stored, pre-resolved literature entries reused as background
    for a hypothesis — this function never fabricates or upgrades them to verdict support. `dossier`
    (optional) carries the verdict-independent target-quality facts; when absent, the advancement
    recommendation is the honest sentinel 'unknown' rather than a fabricated stance."""
    if snapshot.verdict not in EXPLANATION_CLASSES:
        raise ValueError(
            f"unknown verdict {snapshot.verdict!r}; expected one of {tuple(EXPLANATION_CLASSES)}")

    comparability, comp_reasons = _comparability(snapshot, screen_context)
    explanations, explanation_labels = _explanations(snapshot.verdict)
    trust, decision = _trust(snapshot)
    recommendation, rec_rationale = _recommendation(snapshot, dossier)
    experiment, outcomes, feasible, unmet, adaptations, remaining = _experiment_and_outcomes(
        snapshot, constraints)

    evidence_limits: list[str] = list(comp_reasons)
    if not snapshot.rna_tested:
        evidence_limits.append("the mRNA screen did not assay this gene")
    if not snapshot.protein_tested:
        evidence_limits.append("the protein screen did not assay this gene")

    # Citations ride along as background support for a hypothesis, never as verdict support. Guard
    # the invariant loudly rather than trust upstream.
    for c in claims:
        if c.supports == "verdict":
            raise ValueError(
                f"citation {c.accession} claims to support the verdict; citations are background for "
                "hypotheses only, never verdict support")

    return DecisionBrief(
        schema_version=SCHEMA_VERSION,
        snapshot=snapshot,
        comparability=comparability,
        comparability_reasons=comp_reasons,
        trust_assessment=trust,
        evidence_limits=tuple(evidence_limits),
        explanations=explanations,
        explanation_labels=explanation_labels,
        experiment=experiment,
        feasible=feasible,
        unmet_requirements=unmet,
        adaptations=adaptations,
        remaining_uncertainty=remaining,
        outcome_matrix=outcomes,
        decision=decision,
        recommendation=recommendation,
        recommendation_rationale=rec_rationale,
        target_dossier=dossier,
        citations=tuple(claims),
        provenance={
            "rna_screen_id": snapshot.rna_screen_id,
            "protein_screen_id": snapshot.protein_screen_id,
            "rna_regime": screen_context.rna_regime,
            "protein_regime": screen_context.prot_regime,
        },
    )
