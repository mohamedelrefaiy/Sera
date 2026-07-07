"""Adversarial verifier: for each candidate, TRY TO REFUTE IT with computed checks.

Every check is a hypothesis with a COMPUTED verdict (pass/fail + a number), not an
opinion. Checks are typed:
  GATE  — a failure REJECTS the candidate (real KD, clean, powered, donor/guide robust)
  SCORE — a failure weakens but does not reject (cross-condition consistency)
  BONUS — can only lift (independent-screen corroboration; the held-out beat)

Pure: takes a GeneRecord + Evidence, returns an immutable Verdict. No I/O.
"""
from __future__ import annotations

from dataclasses import dataclass

from .data import GeneRecord
from .evidence import Evidence, ScreenHit

GATE, SCORE, BONUS = "gate", "score", "bonus"


@dataclass(frozen=True)
class Thresholds:
    """Every gate's cutoff, as data. Defaults are the calibrated values; the agent
    can hand the scientist a stricter/looser set to re-verify a candidate on demand
    ('re-verify NRAS with donor_corr >= 0.3'). Immutable — a re-verify makes a NEW
    Thresholds, it never mutates the shared default."""

    min_cells: float = 100.0      # below this the on-target effect is low-powered
    min_effect: float = 2.0       # |ontarget_effect_size| floor for a "real" knockdown
    min_downstream: int = 5       # a condition "reproduces" only with real breadth
    min_donor_corr: float = 0.10  # below (or negative) = donor-driven artifact
    min_guide_corr: float = 0.10  # below = the two guides disagree


DEFAULT = Thresholds()


@dataclass(frozen=True)
class Check:
    name: str
    kind: str            # GATE | SCORE | BONUS
    passed: bool
    value: float | int | str
    detail: str


@dataclass(frozen=True)
class Verdict:
    gene: str
    verdict: str         # REJECT | PROMOTE | PROMOTE (weak) | PROMOTE (corroborated)
    reason: str
    checks: tuple[Check, ...]


def _sig_clean(record: GeneRecord):
    return [
        p for p in record.by_condition.values()
        if p.significant and not p.offtarget
    ]


def check_real_knockdown(record: GeneRecord, t: Thresholds) -> Check:
    sig = [p for p in _sig_clean(record) if abs(p.effect_size) >= t.min_effect]
    best = max((abs(p.effect_size) for p in sig), default=0.0)
    return Check("real_knockdown", GATE, len(sig) > 0, round(best, 1),
                 f"{len(sig)} condition(s) with a significant clean KD (|eff|>={t.min_effect})")


def check_enough_cells(record: GeneRecord, t: Thresholds) -> Check:
    counts = [p.n_cells for p in _sig_clean(record) if p.n_cells is not None]
    if not counts:
        # the screen has no cell-count column — can't judge power, so don't gate on it
        return Check("enough_cells", GATE, True, "NA",
                     "no cell-count column in this screen (not gated)")
    peak = max(counts)
    return Check("enough_cells", GATE, peak >= t.min_cells, int(peak),
                 f"peak n_cells among significant conditions = {int(peak)} (floor {int(t.min_cells)})")


def check_donor_robustness(record: GeneRecord, ev: Evidence, t: Thresholds) -> Check:
    corr = ev.donor_corr.get(record.gene)
    if corr is None:
        return Check("donor_robustness", GATE, True, "NA",
                     "no cross-donor data for this gene (not gated)")
    return Check("donor_robustness", GATE, corr >= t.min_donor_corr, round(corr, 2),
                 f"best cross-donor corr = {corr:.2f} (floor {t.min_donor_corr}); low/neg = donor artifact")


def check_cross_guide(record: GeneRecord, ev: Evidence, t: Thresholds) -> Check:
    corr = ev.guide_corr.get(record.gene)
    if corr is None:
        return Check("cross_guide", GATE, True, "NA",
                     "no cross-guide data for this gene (not gated)")
    return Check("cross_guide", GATE, corr >= t.min_guide_corr, round(corr, 2),
                 f"best cross-guide corr = {corr:.2f} (floor {t.min_guide_corr}); low = guides disagree")


def check_cross_condition(record: GeneRecord, t: Thresholds) -> Check:
    sig = _sig_clean(record)
    # A screen with no breadth signal (n_downstream is None) can't judge "real breadth"
    # per condition — so we count a condition as reproduced if it's significant at all.
    has_breadth = any(p.n_downstream is not None for p in record.by_condition.values())
    if has_breadth:
        reproduced = [p.condition for p in sig
                      if p.n_downstream is not None and p.n_downstream >= t.min_downstream]
    else:
        reproduced = [p.condition for p in sig]
    frac = len(reproduced) / max(len(record.by_condition), 1)
    note = "" if has_breadth else " (breadth signal N/A for this screen — counting significance)"
    return Check("cross_condition", SCORE, frac >= 1 / 3, round(frac, 2),
                 f"reproduces in {len(reproduced)}/{len(record.by_condition)} conditions "
                 f"({', '.join(reproduced) or 'none'}){note}")


def check_held_out(record: GeneRecord, ev: Evidence) -> Check:
    hits: tuple[ScreenHit, ...] = ev.screen_hits.get(record.gene, ())
    if hits:
        summary = "; ".join(
            f"{h.screen}:{h.readout}({h.direction},FDR={h.fdr})" for h in hits
        )
        return Check("held_out_screen", BONUS, True, len(hits),
                     f"independent hit — {summary}")
    return Check("held_out_screen", BONUS, False, 0,
                 "not a significant hit in Schmidt2022 or Freimer2022 (not disqualifying)")


def verify(record: GeneRecord, ev: Evidence, thresholds: Thresholds = DEFAULT) -> Verdict:
    """Verify a candidate against thresholds (defaults are the calibrated set).
    Pass a custom Thresholds to re-verify on demand — e.g. a stricter donor floor."""
    checks = (
        check_real_knockdown(record, thresholds),
        check_enough_cells(record, thresholds),
        check_donor_robustness(record, ev, thresholds),
        check_cross_guide(record, ev, thresholds),
        check_cross_condition(record, thresholds),
        check_held_out(record, ev),
    )
    failed_gates = [c for c in checks if c.kind == GATE and not c.passed]
    if failed_gates:
        return Verdict(record.gene, "REJECT",
                       "failed gate: " + ", ".join(c.name for c in failed_gates), checks)

    cc = next(c for c in checks if c.name == "cross_condition")
    corroborated = any(c.kind == BONUS and c.passed for c in checks)
    if corroborated and isinstance(cc.value, (int, float)) and cc.value > 0:
        verdict = "PROMOTE (corroborated)"
    elif cc.passed:
        verdict = "PROMOTE"
    else:
        verdict = "PROMOTE (weak)"
    return Verdict(record.gene, verdict, f"passed all gates; cross-condition={cc.value}", checks)
