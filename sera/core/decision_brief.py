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

#: How a positive control's gene was chosen. Closed on purpose: `curated_known_regulator` means the
#: pick is a canonical IL-2 regulator BOTH screens already agreed on (the strongest possible proof
#: the assay works); `top_replicated_hit` is the honest fallback when no curated gene was replicated
#: at this condition — still a real, code-selected agreement, just not a textbook regulator.
POSITIVE_CONTROL_SOURCES: tuple[str, ...] = ("curated_known_regulator", "top_replicated_hit")

#: The two directions a positive control's expected effect can move. Derived from the sign of its
#: own z_rna (a fact already computed by the deterministic core, never re-derived here).
_POSITIVE_CONTROL_DIRECTIONS: tuple[str, ...] = ("lowers", "raises")

#: A fixed, code-owned sentence — never data-derived — that names the assay-sensitivity discipline a
#: validation plan must satisfy: a flat result is uninterpretable unless the assay is proven to read
#: a known effect first. Reused verbatim on every experiment card.
ASSAY_SENSITIVITY_STATEMENT = (
    "State the minimum detectable effect for each readout and confirm the positive control clears "
    "it — a flat result is only informative if the assay demonstrably reads a known effect.")

#: The verdict-specific decisive-experiment archetypes. A single generic protocol is scientifically
#: wrong across verdicts: a discordant split needs a mandatory time course (temporal decoupling is
#: the likeliest cause and a single timepoint cannot resolve it); a replicated result is already
#: settled and should escalate toward mechanism, not repeat both readouts; a one-sided result is a
#: propagation question; a concordant null is only worth interrogating by varying the shared axis
#: under a positive control; and a genuinely untested layer is a coverage gap, not a verdict question
#: at all. Closed on purpose — `select_experiment_archetype` and `_EXPERIMENT_TEMPLATES` are the only
#: code that may pick or author one.
EXPERIMENT_ARCHETYPES: tuple[str, ...] = (
    "resolve_split", "propagation_test", "escalate_mechanism",
    "null_interrogation", "measure_missing_layer")


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
class PositiveControl:
    """A code-chosen POSITIVE control for a validation experiment: perturb a gene the two screens
    ALREADY agree moves at this exact cytokine/condition, so its expected direction is known before
    the bench work starts. Without this, a flat result on the gene under test is uninterpretable —
    "neither readout changed" could mean a true null OR a dead assay, and there is no way to tell
    them apart. `source` and `expected_direction` are both closed sets, validated below; nothing
    here is fabricated — `select_positive_control` only ever picks a REAL replicated row."""
    gene: str
    source: str
    expected_direction: str
    z_rna: float
    lfc_prot: float

    def __post_init__(self) -> None:
        if self.source not in POSITIVE_CONTROL_SOURCES:
            raise ValueError(
                f"unknown positive-control source {self.source!r}; "
                f"allowed: {POSITIVE_CONTROL_SOURCES}")
        if self.expected_direction not in _POSITIVE_CONTROL_DIRECTIONS:
            raise ValueError(
                f"unknown positive-control direction {self.expected_direction!r}; "
                f"allowed: {_POSITIVE_CONTROL_DIRECTIONS}")


@dataclass(frozen=True)
class Experiment:
    perturbation: str
    guides: tuple[str, ...]
    controls: tuple[str, ...]
    conditions: str
    readouts: tuple[str, ...]
    timecourse: str | None
    positive_control: PositiveControl | None = None
    assay_sensitivity: str = ASSAY_SENSITIVITY_STATEMENT
    objective: str = ""


@dataclass(frozen=True)
class OutcomeRow:
    result: str
    interpretation: str
    next_action: str


#: Code-owned outcome matrices, keyed by whether the verdict is a *disagreement to resolve* or a
#: *no-signal* result. The LLM never authors these rows. Two matrices because the experiment is a
#: different question in each case: for discordant / one-sided verdicts the question is "does the
#: split reproduce?"; for `neither` there is no split — the only honest question is "was the absence
#: real?", and the whole point of the brief is that this is low-yield.
_OUTCOMES_DISAGREEMENT: tuple[OutcomeRow, ...] = (
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

_OUTCOMES_NO_SIGNAL: tuple[OutcomeRow, ...] = (
    OutcomeRow(
        result="Neither readout moves versus control",
        interpretation="Confirms the screens' agreement — this gene does nothing here",
        next_action="Deprioritise at this condition; spend the bench time elsewhere"),
    OutcomeRow(
        result="A readout moves that both screens missed",
        interpretation="A possible screen false negative, or an off-condition effect",
        next_action="Re-check the condition and guide before reading anything into it"),
    OutcomeRow(
        result="Only one guide shows an effect",
        interpretation="Almost certainly a guide-specific artifact given no screen signal",
        next_action="Do not pursue unless a second guide independently agrees"),
)

#: escalate_mechanism (replicated): the reconciliation is already settled, so the decisive question
#: moves from "do the layers agree" to "does the effect matter" — dose-response, on-target rescue,
#: and magnitude on secreted IL-2. There is no split to reproduce here; asserting one would be wrong.
_OUTCOMES_ESCALATE: tuple[OutcomeRow, ...] = (
    OutcomeRow(
        result="Effect scales with knockdown dose",
        interpretation="Supports a genuine, on-target mechanism rather than an assay ceiling effect",
        next_action="Proceed to rescue and secreted-IL-2 magnitude work"),
    OutcomeRow(
        result="Dose-response is flat across knockdown levels",
        interpretation="The confirmatory result may not reflect a graded, on-target mechanism",
        next_action="Re-open the reconciliation before committing further bench time"),
    OutcomeRow(
        result="Rescue with a resistant construct fails to restore the phenotype",
        interpretation="Points to an off-target effect rather than the gene itself",
        next_action="Do not advance until an independent guide and rescue agree"),
)

#: propagation_test (mrna_only / protein_only): a one-sided verdict, not a split — the question is
#: whether the single-layer effect reaches the other layer, framed by direction of propagation.
_OUTCOMES_PROPAGATION: tuple[OutcomeRow, ...] = (
    OutcomeRow(
        result="The effect now appears on both readouts",
        interpretation="The single-layer signal does propagate under matched, paired measurement",
        next_action="Treat as a real cross-layer effect and proceed to mechanism"),
    OutcomeRow(
        result="The effect stays confined to the originally-flagged layer",
        interpretation="A genuine single-layer effect that does not reach the other layer here",
        next_action="Characterise the buffering or sensitivity gap before advancing"),
    OutcomeRow(
        result="Neither readout moves in the paired experiment",
        interpretation="The original single-screen signal may not reproduce under matched conditions",
        next_action="Deprioritise or re-check the original screen call"),
)

#: null_interrogation (neither, both layers tested): a concordant null is low-yield to chase, and
#: only interrogable at all if the assay is PROVEN sensitive first (positive control) and the shared
#: timing/dose/cell-state axis is varied — re-testing the identical condition tells you nothing a
#: null didn't already tell you.
_OUTCOMES_NULL_INTERROGATION: tuple[OutcomeRow, ...] = (
    OutcomeRow(
        result="A readout moves at a varied timepoint, dose, or cell state",
        interpretation="Overturns the concordant null — both screens missed a real, off-condition effect",
        next_action="This becomes the new headline finding; characterise the condition that revealed it"),
    OutcomeRow(
        result="Nothing moves anywhere, and the positive control clears its expected effect",
        interpretation="The null is confirmed specifically at the conditions actually tested",
        next_action="Deprioritise with confidence; the assay worked and still saw nothing"),
    OutcomeRow(
        result="The positive control itself fails to move",
        interpretation="Inconclusive — the assay is not demonstrably sensitive here",
        next_action="Fix assay sensitivity before drawing any conclusion about the gene"),
)

#: measure_missing_layer (protein_tested is False, any verdict): a coverage gap, not a verdict
#: question — the honest experiment is to measure the layer that was never assayed.
_OUTCOMES_MEASURE_MISSING_LAYER: tuple[OutcomeRow, ...] = (
    OutcomeRow(
        result="The previously-untested protein layer moves with the perturbation",
        interpretation="The effect reaches the phenotype the mRNA-side result predicted",
        next_action="Treat as a supported cross-layer effect and proceed to mechanism"),
    OutcomeRow(
        result="The protein layer stays flat",
        interpretation="The effect appears confined to transcript and does not reach protein",
        next_action="Characterise as transcript-only before advancing further"),
    OutcomeRow(
        result="Neither layer moves in the paired experiment",
        interpretation="The originally-flagged signal is not supported once both layers are measured",
        next_action="Deprioritise or re-check the original screen call"),
)


@dataclass(frozen=True)
class ExperimentTemplate:
    """A code-owned, per-archetype template for the decisive experiment. The LLM may render this
    prose; it never authors it. `timecourse` is the archetype's DEFAULT timecourse text (constraints
    may still drop or, for `resolve_split`, turn its absence into an infeasibility — see
    `_experiment_and_outcomes`)."""
    archetype: str
    objective: str
    timecourse: str | None
    timecourse_required: bool
    varies_conditions: bool
    positive_control_required: bool
    outcomes: tuple[OutcomeRow, ...]

    def __post_init__(self) -> None:
        if self.archetype not in EXPERIMENT_ARCHETYPES:
            raise ValueError(
                f"unknown archetype {self.archetype!r}; allowed: {EXPERIMENT_ARCHETYPES}")


#: The closed, code-owned template per archetype. `select_experiment_archetype` picks the key;
#: nothing downstream may pick a different template than the one keyed to its own archetype.
_EXPERIMENT_TEMPLATES: dict[str, ExperimentTemplate] = {
    "resolve_split": ExperimentTemplate(
        archetype="resolve_split",
        objective=(
            "Resolve why the transcript and protein layers disagree — a single timepoint cannot "
            "distinguish a real molecular decoupling from a temporal mismatch between the two screens."),
        timecourse="time course across 8h / 24h / 48h — transcript and protein can decouple transiently",
        timecourse_required=True,
        varies_conditions=False,
        positive_control_required=False,
        outcomes=_OUTCOMES_DISAGREEMENT,
    ),
    "propagation_test": ExperimentTemplate(
        archetype="propagation_test",
        objective=(
            "Test whether the single-layer effect flagged by one screen reaches the other layer under "
            "matched, paired measurement."),
        timecourse="short time course (e.g. 24h and 48h) if feasible",
        timecourse_required=False,
        varies_conditions=False,
        positive_control_required=False,
        outcomes=_OUTCOMES_PROPAGATION,
    ),
    "escalate_mechanism": ExperimentTemplate(
        archetype="escalate_mechanism",
        objective=(
            "The reconciliation is already settled — both screens agree. Advance toward mechanism and "
            "relevance with a dose-response, an on-target rescue, and the magnitude on secreted IL-2, "
            "rather than re-measuring both layers again."),
        timecourse=None,
        timecourse_required=False,
        varies_conditions=False,
        positive_control_required=False,
        outcomes=_OUTCOMES_ESCALATE,
    ),
    "null_interrogation": ExperimentTemplate(
        archetype="null_interrogation",
        objective=(
            "Both screens agree this gene does nothing at this condition, so validation is low-yield "
            "unless the concordant null itself is in doubt. If run, it must prove the assay works with "
            "a positive control and vary the shared timing, dose, or cell state — re-testing the "
            "identical condition cannot tell you anything the null didn't already say."),
        timecourse=(
            "vary the shared axis — earlier/later timepoints, stimulation dose, cell state — a null "
            "only holds where both screens actually looked"),
        timecourse_required=False,
        varies_conditions=True,
        positive_control_required=True,
        outcomes=_OUTCOMES_NULL_INTERROGATION,
    ),
    "measure_missing_layer": ExperimentTemplate(
        archetype="measure_missing_layer",
        objective=(
            "The protein layer was never measured for this gene — a coverage gap, not a verdict "
            "question. Measure the untested layer directly rather than reasoning further from the "
            "single layer that was assayed."),
        timecourse="short time course (e.g. 24h and 48h) if feasible",
        timecourse_required=False,
        varies_conditions=False,
        positive_control_required=False,
        outcomes=_OUTCOMES_MEASURE_MISSING_LAYER,
    ),
}


def select_experiment_archetype(verdict: str, protein_tested: bool) -> str:
    """Pick the code-owned experiment archetype for this verdict. A pure, standalone function — safe
    to call before `build_decision_brief` validates the verdict, so it validates defensively too.

    A missing protein layer (`protein_tested is False`) is a COVERAGE GAP and takes precedence over
    every verdict-based archetype: a gene the protein screen never looked at needs its protein layer
    measured regardless of what the mRNA side found."""
    if protein_tested is False:
        return "measure_missing_layer"
    if verdict == "discordant":
        return "resolve_split"
    if verdict == "replicated":
        return "escalate_mechanism"
    if verdict == "neither":
        return "null_interrogation"
    if verdict in ("mrna_only", "protein_only"):
        return "propagation_test"
    raise ValueError(f"unknown verdict {verdict!r}")


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


def select_positive_control(
    rows: list[dict],
    curated_genes: list[str] | tuple[str, ...] | set[str],
    cytokine: str,
    condition: str,
) -> PositiveControl | None:
    """Choose a POSITIVE control from rows the two screens ALREADY agree on at this exact
    cytokine/condition — never a gene under test, never an invented one. Pure and deterministic:
    given the same `rows`, the same gene is picked every time.

    Algorithm (exact, do not reorder):
      1. Filter to `cytokine`/`condition` rows with verdict == 'replicated' (both screens see a
         real, detectable effect here). No replicated row -> no honest positive control -> None.
      2. Score each candidate by combined_q = (q_rna or 1.0) + (q_prot or 1.0) — missing q-values
         are treated as the weakest possible significance, never as a free pass.
      3. Prefer a CURATED known regulator when one was replicated here (the strongest possible
         proof: a textbook IL-2 gene the assay can detect). Only fall back to the full replicated
         pool when no curated gene qualifies.
      4. Sort the chosen pool by (combined_q ascending, gene name ascending) and take the first —
         the gene-name tie-break makes the pick fully deterministic even on an exact tie.
    """
    curated = set(curated_genes)
    candidates = [r for r in rows
                  if r.get("cytokine") == cytokine and r.get("condition") == condition
                  and r.get("verdict") == "replicated"]
    if not candidates:
        return None

    def combined_q(row: dict) -> float:
        return (row.get("q_rna") if row.get("q_rna") is not None else 1.0) + (
            row.get("q_prot") if row.get("q_prot") is not None else 1.0)

    curated_hits = [r for r in candidates if r["gene"] in curated]
    if curated_hits:
        pool, source = curated_hits, "curated_known_regulator"
    else:
        pool, source = candidates, "top_replicated_hit"

    winner = sorted(pool, key=lambda r: (combined_q(r), r["gene"]))[0]
    z_rna = float(winner["z_rna"])
    return PositiveControl(
        gene=winner["gene"],
        source=source,
        expected_direction="lowers" if z_rna < 0 else "raises",
        z_rna=z_rna,
        lfc_prot=float(winner["lfc_prot"]),
    )


def _experiment_and_outcomes(
    snap: ConcordanceSnapshot,
    constraints: ExperimentConstraints | None,
    positive_control: PositiveControl | None = None,
) -> tuple[Experiment, tuple[OutcomeRow, ...], bool, tuple[str, ...], tuple[str, ...], str]:
    """The discriminating experiment, its outcome matrix, and feasibility under constraints.

    The default is a paired-readout arrayed experiment: 2 guides + NTC, >=3 donors, matched cells +
    stimulation, transcript AND protein from the SAME wells, short timecourse. Constraints adapt it
    but NEVER silently make it non-decisive — an infeasible ask returns feasible=False with the
    unmet requirements spelled out. `positive_control` (optional, default None so every existing
    caller is unaffected) rides straight onto the `Experiment`; this function never selects one
    itself — that selection is the endpoint's job via `select_positive_control`."""
    g = snap.gene
    # A paired RNA+protein readout is what makes the experiment decisive for an RNA/protein
    # disagreement. Default to the strongest available pair.
    default_transcript = "qpcr"
    default_protein = "facs"
    controls = ["non-targeting control (NTC)"]
    # `neither` is a no-signal verdict: there is no split to resolve, so the experiment is a
    # confirmatory sanity check, not a discriminating one. The unmet-requirement text and the
    # outcome matrix both branch on this — the disagreement framing would be wrong here.
    no_signal = snap.verdict == "neither"

    # The verdict-specific archetype selects the template this experiment is built from — the
    # objective, default timecourse, and outcome matrix are all code-owned per archetype, never
    # authored downstream. A missing protein layer takes precedence over the verdict entirely.
    archetype = select_experiment_archetype(snap.verdict, snap.protein_tested)
    template = _EXPERIMENT_TEMPLATES[archetype]

    unmet: list[str] = []
    adaptations: list[str] = []
    remaining = ""

    avail = set(constraints.readouts) if constraints else {default_transcript, default_protein}
    has_transcript = "qpcr" in avail
    has_protein = bool({"facs", "elisa", "western"} & avail)

    if constraints is not None:
        if not has_transcript:
            unmet.append(
                "a transcript readout (qPCR) is required to confirm the transcript level; "
                "none available"
                if no_signal else
                "a transcript readout (qPCR) is required to resolve an RNA/protein disagreement; "
                "none available")
        if not has_protein:
            unmet.append(
                "a protein readout (FACS/ELISA/Western) is required; none available")
        if constraints.donors < _MIN_DONORS:
            unmet.append(
                f"{_MIN_DONORS} donors are needed to separate biology from donor variation; "
                f"only {constraints.donors} available")

    # The readout line must reflect what the scientist actually selected, not the default pair —
    # otherwise re-planning with qPCR unchecked leaves the card showing "transcript · qpcr" and the
    # plan looks unchanged (contradicting the infeasibility banner). Show "not run" for an absent
    # readout rather than a readout that isn't on the bench.
    transcript_readout = "qpcr" if has_transcript else "not run"
    # Choose the actual protein readout from what's available (prefer FACS for single-cell paired
    # measurement, else ELISA for secreted cytokine, else Western).
    protein_readout = next((r for r in ("facs", "elisa", "western") if r in avail), "not run")
    readouts = (f"transcript · {transcript_readout}", f"protein · {protein_readout}")

    # Timecourse: default comes from the archetype's template, not a single hardcoded sentence — a
    # dose-response archetype (escalate_mechanism) has no default timecourse at all, and a
    # discordant split needs one that survives the constraints check below.
    timecourse: str | None = template.timecourse
    if constraints is not None and constraints.days < 3 and timecourse is not None:
        if template.timecourse_required:
            # A discordant split cannot be resolved from a single timepoint — dropping the time
            # course here would silently downgrade a mandatory arm into a non-decisive one. This is
            # an infeasibility, not an adaptation.
            unmet.append(
                "a time course is required to resolve the layer disagreement, but the available "
                "window is under 3 days")
        else:
            timecourse = None
            adaptations.append(
                "dropped the multi-timepoint course — the available window is under 3 days")
            remaining = "without a timecourse a temporal-feedback explanation cannot be excluded"

    # A null-interrogation without a positive control is not decisive: "nothing moved" would be
    # indistinguishable from a dead assay. This function never selects a positive control itself —
    # it only requires one be present when the archetype demands it.
    if template.positive_control_required and positive_control is None:
        unmet.append(
            "a positive control is required for a null interrogation and is missing — without it, "
            "a flat result cannot be told apart from a dead assay")

    feasible = not unmet

    experiment = Experiment(
        perturbation=f"Arrayed CRISPRi against {g}",
        guides=(f"{g}-sg1", f"{g}-sg2"),
        controls=tuple(controls),
        conditions="matched stimulated CD4+ T cells (same cells and stimulation as the screens)",
        readouts=readouts,
        timecourse=timecourse,
        positive_control=positive_control,
        objective=template.objective,
    )

    return experiment, template.outcomes, feasible, tuple(unmet), tuple(adaptations), remaining


def build_decision_brief(
    snapshot: ConcordanceSnapshot,
    screen_context: ScreenPairContext,
    claims: tuple[GroundedClaim, ...] = (),
    constraints: ExperimentConstraints | None = None,
    dossier: TargetDossier | None = None,
    positive_control: PositiveControl | None = None,
) -> DecisionBrief:
    """Assemble the decision brief. Pure and deterministic: no I/O, no LLM, no globals.

    `snapshot` carries the code-computed verdict (never recomputed here). `screen_context` comes from
    the canonical adapter. `claims` are stored, pre-resolved literature entries reused as background
    for a hypothesis — this function never fabricates or upgrades them to verdict support. `dossier`
    (optional) carries the verdict-independent target-quality facts; when absent, the advancement
    recommendation is the honest sentinel 'unknown' rather than a fabricated stance. `positive_control`
    (optional, default None) is the endpoint's already-selected proof the assay can detect a known
    IL-2 effect — this function never selects one itself, it only threads it onto the experiment."""
    if snapshot.verdict not in EXPLANATION_CLASSES:
        raise ValueError(
            f"unknown verdict {snapshot.verdict!r}; expected one of {tuple(EXPLANATION_CLASSES)}")

    comparability, comp_reasons = _comparability(snapshot, screen_context)
    explanations, explanation_labels = _explanations(snapshot.verdict)
    trust, decision = _trust(snapshot)
    recommendation, rec_rationale = _recommendation(snapshot, dossier)
    experiment, outcomes, feasible, unmet, adaptations, remaining = _experiment_and_outcomes(
        snapshot, constraints, positive_control)

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
