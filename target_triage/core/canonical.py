"""Canonical screen schema + the deterministic gate that stands between an agent and the core.

Concord's thesis is *agents on the rim, deterministic instrument at the hub*. This module is
the rim's inner wall. An ingestion agent (agents/ingest.py) may PROPOSE how an arbitrary
screen file maps onto the canonical axes below; nothing it proposes reaches the concordance
builder until the pure code here VALIDATES it. Agent proposes; validator disposes.

The canonical schema is the entire generalization mechanism. Schmidt (FACS protein) and Zhu
(Perturb-seq mRNA) stop being two hardcoded loaders and become two row-producers for one tidy
long-form table. Adding an Nth screen is then a mapping problem, not a code problem.

NOT to be confused with core/schema.py (ScreenSchema). That is the sibling mapping layer for
the *Target Triage* ranking instrument: it reads one screen's columns into a GeneRecord to rank
genes WITHIN that screen. It has no cytokine, modality, significance-regime or sign axis --
because ranking inside one screen holds all of those constant. Concord reconciles ACROSS
screens, where exactly those things silently differ, so it needs axes for them. (Concretely:
ScreenSchema reads Schmidt's `phenotype` column as one opaque condition, "CD4+ IL2"; Concord
must decompose the same column into cell_type=CD4 x cytokine=IL2, because reconciliation happens
per cytokine.) Same question, two instruments, two answers.

Three hazards are enforced here, not trusted to the model:

  Hazard 1  on-target self-perturbation. A gene knocking down its own transcript is guide
            efficacy, not regulation. `is_ontarget` marks those rows so the core drops them.

  Hazard 2  one significance regime per screen, never mixed. A DESeq2 adj-p of 0.09 and a
            MASH lfsr of 0.09 are not the same claim; pooling them is meaningless. A file
            offering both must pick one, record the choice, and discard the other.

  Hazard 3  effect-sign inversion. THE silent verdict-flipper. Under the canonical convention
            (`positive = increases readout`) an inverted effect column turns every `replicated`
            into `discordant` and vice versa, with no error, no warning, and a table that looks
            perfectly plausible. `check_sign_convention` catches it using the screen's OWN
            on-target rows -- the same rows Hazard 1 throws away. See that function's docstring
            for why no external ground truth is needed. (This is not hypothetical: the bundled
            Freimer screen is genuinely inverted relative to Schmidt.)

Stdlib-only and side-effect-free: builds and checks records, reads no files, calls no model.
The caller owns I/O. That is what makes this the gate rather than another thing to trust.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace
from enum import Enum
from typing import Iterable, Sequence

# ---------------------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------------------


class Modality(str, Enum):
    """What was physically measured. Two `rna` screens agreeing are replicates; an `rna` and a
    `protein` screen agreeing is concordance across the central dogma -- a different and stronger
    claim. The core must tell them apart, so the modality travels on the row."""

    RNA = "rna"
    PROTEIN = "protein"
    PHENOTYPE = "phenotype"


class SignifRegime(str, Enum):
    """The statistical dialect a screen speaks. Hazard 2: exactly one per screen, never mixed.

    These are NOT interchangeable. `deseq2_adjp` and `benjamini_fdr` are tail probabilities
    (small = significant); `mash_lfsr` is a local false-SIGN rate; `zscore` and `log2fc` are
    effect magnitudes with no probability interpretation at all. A cutoff of 0.10 means something
    different in each. Recording the regime lets the core refuse to compare across dialects
    rather than quietly return a number.
    """

    DESEQ2_ADJP = "deseq2_adjp"
    BENJAMINI_FDR = "benjamini_fdr"
    MASH_LFSR = "mash_lfsr"
    ZSCORE = "zscore"
    LOG2FC = "log2fc"

    @property
    def is_probability(self) -> bool:
        """True when the value is a tail probability / error rate: small means significant, and
        anything outside [0, 1] means the wrong column was mapped."""
        return self in (SignifRegime.DESEQ2_ADJP, SignifRegime.BENJAMINI_FDR,
                        SignifRegime.MASH_LFSR)


# Preference order when a file carries more than one regime (Hazard 2 policy). Earlier wins.
# DESeq2 adj-p leads because it is the regime the existing mRNA side already speaks; preferring
# it keeps a multi-regime file comparable to the artifact the core was validated against.
REGIME_PREFERENCE: tuple[SignifRegime, ...] = (
    SignifRegime.DESEQ2_ADJP,
    SignifRegime.BENJAMINI_FDR,
    SignifRegime.MASH_LFSR,
    SignifRegime.ZSCORE,
    SignifRegime.LOG2FC,
)


def choose_regime(available: Iterable[SignifRegime]) -> SignifRegime:
    """Hazard 2's policy, applied deterministically: given every regime a file offers, pick one.

    The choice is code's, not the model's -- an agent may only report which regimes it FOUND.
    Raises if a file offers none, because a screen with no significance signal cannot be
    thresholded and must not be silently admitted with a default.
    """
    found = {r for r in available}
    if not found:
        raise SchemaViolation("no significance regime found; a screen without a significance "
                              "signal cannot be thresholded and is never admitted with a default")
    for preferred in REGIME_PREFERENCE:
        if preferred in found:
            return preferred
    raise SchemaViolation(f"unrecognised significance regimes: {sorted(r.value for r in found)}")


# The axes every canonical row must carry. Anything absent is a hard validation failure --
# never imputed, never defaulted. A missing axis means the mapping is wrong, not that the value
# happens to be zero.
REQUIRED_AXES: tuple[str, ...] = (
    "screen_id", "gene", "cytokine", "condition", "cell_type",
    "modality", "effect_size", "signif_value", "signif_regime",
)

# The sign convention, stated once, in one place, so no adapter can quietly disagree with it.
# Every effect_size in the canonical table obeys this. Hazard 3 exists to enforce it.
SIGN_CONVENTION = "positive = perturbation INCREASES the readout"


# ---------------------------------------------------------------------------------------
# The canonical row
# ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class CanonicalRow:
    """One measurement: the effect of perturbing `gene` on `cytokine`, in one context.

    Immutable by construction. `effect_size` obeys SIGN_CONVENTION; `signif_value` is
    interpreted in `signif_regime` and in no other. `is_ontarget` is DERIVED, never supplied by
    an agent -- `derive_ontarget` is the only way to set it truthfully.

    Example (synthetic):
        CanonicalRow(screen_id="demo_screen", gene="ITK", cytokine="IL2",
                     condition="Stim48hr", cell_type="CD4", modality=Modality.RNA,
                     effect_size=-2.310, signif_value=0.0004,
                     signif_regime=SignifRegime.DESEQ2_ADJP, is_ontarget=False,
                     qc=(("crossdonor_corr", 0.71),))
    """

    screen_id: str
    gene: str
    cytokine: str
    condition: str
    cell_type: str
    modality: Modality
    effect_size: float
    signif_value: float
    signif_regime: SignifRegime
    is_ontarget: bool = False
    qc: tuple[tuple[str, float], ...] = ()   # passthrough QC pairs; a tuple keeps the row hashable


def derive_ontarget(row: CanonicalRow) -> CanonicalRow:
    """Set `is_ontarget` from the data itself (gene == cytokine), never from a proposal.

    Hazard 1's input. A perturbed gene that IS the readout gene measures how well the guide
    worked, not how that gene regulates anything. Deriving this in code -- rather than trusting
    an agent-supplied column -- means a mapping error can never smuggle a contamination row past
    the exclusion, nor mislabel a real regulatory row as contamination.
    """
    return replace(row, is_ontarget=row.gene.upper() == row.cytokine.upper())


# ---------------------------------------------------------------------------------------
# Hazard 3 -- effect-sign inversion
# ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class SignCheck:
    """Verdict of the on-target sign calibration. `inverted=True` means the screen's effect
    column runs opposite to SIGN_CONVENTION and every sign must be flipped before use."""

    screen_id: str
    n_ontarget: int
    n_negative: int          # on-target rows with effect_size < 0 (the expected direction)
    n_positive: int          # on-target rows with effect_size > 0 (the inverted direction)
    inverted: bool
    confident: bool          # False -> ambiguous; the caller must hard-fail, never guess
    detail: str

    @property
    def ok(self) -> bool:
        """Safe to proceed: the polarity is known (either already correct, or correctable)."""
        return self.confident


def check_sign_convention(rows: Sequence[CanonicalRow], screen_id: str,
                          min_ontarget: int = 1) -> SignCheck:
    """Decide whether a screen's effect column is inverted, using that screen's own on-target rows.

    The insight: knocking a gene down must lower that gene's own product. Under SIGN_CONVENTION
    (`positive = increases readout`) the on-target rows -- where the perturbed gene IS the readout
    -- must therefore be systematically NEGATIVE. If they come out systematically POSITIVE, the
    effect column is inverted.

    This needs no external ground truth. Hazard 1 discards the on-target rows as contamination;
    Hazard 3 reads them on the way out as an internal calibration standard. Same rows, two jobs.

    Why not use all rows? Because across a genome-scale screen the effect column is ~symmetric
    about zero (measured: 49.9% negative in Schmidt CD4+ IL2), so a population-level sign
    statistic is pure noise. Only the on-target rows carry polarity, and they carry it cleanly
    (measured: Schmidt's IL2-on-IL2 lfc = -2.61; Freimer's three on-target rows are all POSITIVE,
    +1.80 to +3.79 -- a genuinely inverted screen sitting in the repo, not a synthetic fixture).

    Ambiguity is never resolved by guessing. Too few on-target rows, or on-target rows that
    disagree with each other, yield `confident=False` and the caller must stop. A screen whose
    polarity we cannot establish is a screen whose verdicts we cannot trust.
    """
    on = [r for r in rows if r.is_ontarget and r.effect_size != 0.0]
    n_neg = sum(1 for r in on if r.effect_size < 0)
    n_pos = sum(1 for r in on if r.effect_size > 0)
    n = len(on)

    if n < min_ontarget:
        return SignCheck(screen_id, n, n_neg, n_pos, inverted=False, confident=False,
                         detail=(f"only {n} usable on-target row(s) (need >= {min_ontarget}); "
                                 "sign convention cannot be established -- refusing to guess"))

    # Unanimity is the bar. A split vote means the calibration standard itself is unreliable, so
    # we decline rather than take a majority of a signal that should never disagree.
    if n_neg and n_pos:
        return SignCheck(screen_id, n, n_neg, n_pos, inverted=False, confident=False,
                         detail=(f"on-target rows disagree ({n_neg} negative, {n_pos} positive); "
                                 "polarity ambiguous -- refusing to guess"))

    inverted = n_pos > 0
    detail = (f"{n} on-target row(s) all {'positive' if inverted else 'negative'}; "
              + (f"effect column is INVERTED relative to the convention ({SIGN_CONVENTION}) "
                 "-- signs will be flipped"
                 if inverted else
                 f"effect column matches the convention ({SIGN_CONVENTION})"))
    return SignCheck(screen_id, n, n_neg, n_pos, inverted=inverted, confident=True, detail=detail)


def apply_sign_correction(rows: Sequence[CanonicalRow],
                          check: SignCheck) -> tuple[CanonicalRow, ...]:
    """Flip every effect sign iff the check says the column is inverted. Refuses on low
    confidence -- an unresolved polarity must stop the pipeline, not pick a direction."""
    if not check.confident:
        raise SchemaViolation(f"[{check.screen_id}] cannot correct sign: {check.detail}")
    if not check.inverted:
        return tuple(rows)
    # `-x` would map 0.0 to -0.0, which still compares == 0.0 but prints as a negative zero;
    # `0.0 - x` keeps a true zero at zero and flips everything else, so an exactly-zero effect
    # stays exactly zero and remains direction-undefined rather than becoming a signed value.
    return tuple(replace(r, effect_size=0.0 - r.effect_size) for r in rows)


# ---------------------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------------------


class SchemaViolation(ValueError):
    """The agent's output failed the canonical contract. Raised, never warned: a screen that does
    not validate does not enter the core."""


@dataclass(frozen=True)
class ValidationReport:
    """What the gate saw. Carried into the provenance log so a reviewer can audit the run."""

    screen_id: str
    n_rows: int
    regime: SignifRegime
    modality: Modality
    sign_check: SignCheck
    n_ontarget: int
    unmapped_columns: tuple[str, ...] = ()   # logged, never guessed away (brief 1.5)


def validate(rows: Sequence[CanonicalRow], screen_id: str,
             unmapped_columns: Iterable[str] = ()) -> ValidationReport:
    """The deterministic gate. Everything an agent proposed must survive this to reach the core.

    Each check names the failure it prevents:
      * non-empty                 -- an agent that mapped nothing must not report success
      * every REQUIRED_AXES set   -- a missing axis is a broken mapping, not a zero
      * screen_id agrees          -- rows from another screen must not ride along
      * ONE regime (Hazard 2)     -- pooling an adj-p with an lfsr compares incomparable claims
      * ONE modality              -- an rna/protein mix inside one screen means the readout axis
                                     was mis-mapped
      * finite numbers            -- NaN/inf silently poison every threshold comparison
      * probability regimes in [0,1] -- a "p-value" of 3.4 means the wrong column was mapped
      * is_ontarget is DERIVED    -- recomputed here and compared; a proposal cannot smuggle it
      * sign convention (Hazard 3) -- established from on-target rows, or refuse

    Returns the report on success; raises SchemaViolation on any failure. It never repairs the
    data -- repair is the caller's explicit, logged act (see `apply_sign_correction`).
    """
    if not rows:
        raise SchemaViolation(f"[{screen_id}] produced zero canonical rows -- nothing to validate")

    for i, r in enumerate(rows):
        for axis in REQUIRED_AXES:
            v = getattr(r, axis, None)
            if v is None or (isinstance(v, str) and not v.strip()):
                raise SchemaViolation(f"[{screen_id}] row {i}: required axis '{axis}' is empty; "
                                      "an unmapped axis is a broken mapping, never a default")
        if r.screen_id != screen_id:
            raise SchemaViolation(f"[{screen_id}] row {i}: screen_id='{r.screen_id}' does not "
                                  "match the screen being validated")

    regimes = {r.signif_regime for r in rows}
    if len(regimes) != 1:
        raise SchemaViolation(
            f"[{screen_id}] Hazard 2 violated: {len(regimes)} significance regimes in one screen "
            f"({sorted(x.value for x in regimes)}). One regime per screen -- an adj-p and an lfsr "
            "of the same magnitude are not the same claim and must never be pooled.")
    regime = regimes.pop()

    modalities = {r.modality for r in rows}
    if len(modalities) != 1:
        raise SchemaViolation(
            f"[{screen_id}] {len(modalities)} modalities in one screen "
            f"({sorted(x.value for x in modalities)}); the readout axis was likely mis-mapped.")
    modality = modalities.pop()

    for i, r in enumerate(rows):
        if not math.isfinite(r.effect_size):
            raise SchemaViolation(f"[{screen_id}] row {i} ({r.gene}): effect_size={r.effect_size} "
                                  "is not finite; it would silently poison every comparison")
        if not math.isfinite(r.signif_value):
            raise SchemaViolation(f"[{screen_id}] row {i} ({r.gene}): "
                                  f"signif_value={r.signif_value} is not finite")
        if regime.is_probability and not (0.0 <= r.signif_value <= 1.0):
            raise SchemaViolation(
                f"[{screen_id}] row {i} ({r.gene}): signif_value={r.signif_value} is outside "
                f"[0,1] but the regime '{regime.value}' is a probability -- the wrong column was "
                "almost certainly mapped to significance")

    # is_ontarget must be what the data says, not what a proposal claimed.
    for i, r in enumerate(rows):
        if derive_ontarget(r).is_ontarget != r.is_ontarget:
            raise SchemaViolation(
                f"[{screen_id}] row {i} ({r.gene}/{r.cytokine}): is_ontarget={r.is_ontarget} "
                "contradicts the data. It is derived (gene == cytokine), never supplied.")

    sign = check_sign_convention(rows, screen_id)
    if not sign.ok:
        raise SchemaViolation(f"[{screen_id}] Hazard 3 unresolved: {sign.detail}")

    return ValidationReport(
        screen_id=screen_id, n_rows=len(rows), regime=regime, modality=modality,
        sign_check=sign, n_ontarget=sum(1 for r in rows if r.is_ontarget),
        unmapped_columns=tuple(unmapped_columns),
    )


def drop_ontarget(rows: Sequence[CanonicalRow]) -> tuple[CanonicalRow, ...]:
    """Hazard 1: remove self-perturbation rows AFTER they have served as the sign-calibration
    standard. Order matters -- validate (which reads them) then drop (which discards them)."""
    return tuple(r for r in rows if not r.is_ontarget)
