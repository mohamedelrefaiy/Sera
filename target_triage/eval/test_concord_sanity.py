"""CONCORD SANITY GATE — the controls-first gate for the concordance join (brief §3.6).

Nothing in Concord is "done" until this passes: the join of the mRNA screen (Zhu) and the
protein screen (Schmidt) must recover the concordance we already know before we trust any
novel verdict. If the canonical TCR-signaling genes don't come out replicated, the join is
wrong — and the tool must say so rather than hand back a plausible-looking table.

Assertions follow the house discipline: assert against the LOADER / built table, never a
rounded UI payload; state the failure each gate prevents; degrade honestly.

Four gates, mirroring brief §3.6:
  (a) on-target self-perturbation is excluded (a gene never judges its own cytokine).
  (b) aggregate Spearman(z_rna_IL2, schmidt_lfc_IL2) is NEAR ZERO — the average hides the
      structure; a strong correlation would mean the join collapsed the two screens.
  (c) ITK / BCL10 / VAV1 are REPLICATED at IL2 (the positive-control genes), and TSC1 is
      NOT falsely called replicated (it is direction-discordant — mTOR brake).
  (d) the replicated + discordant counts at Stim48hr are in the brief's ballpark (~24 total
      by a direction-agnostic 2x2), which the direction-aware split separates honestly.

Spearman is computed with a numpy-only helper (rank-then-Pearson) so the gate adds no heavy
test dependency — numpy is already a pipeline dep.

Run as a gate:    python eval/test_concord_sanity.py     (prints PASS/FAIL + numbers)
Run under pytest: pytest eval/test_concord_sanity.py

NOTE: this gate needs the artifacts built first:
    python pipeline/01_extract_cytokines.py --source local
    python pipeline/02_build_concordance.py
If the artifacts are absent, the gate SKIPS (never silently passes).
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from target_triage.core.concordance import Config, load_protein  # noqa: E402

_APP = os.path.join(os.path.dirname(__file__), "..", "..")
_MRNA = os.path.join(_APP, "target_triage", "data", "artifacts", "cytokine_mrna_effects.parquet")
_CONC = os.path.join(_APP, "target_triage", "data", "artifacts", "concordance.parquet")

# Positive controls: canonical TCR-signaling positive regulators that lower IL2 in BOTH
# screens. These MUST come out replicated or the join is wrong. TSC1 is deliberately absent
# — it is a direction-discordant mTOR brake, the honest exception, checked separately.
POSITIVE_CONTROLS = ("ITK", "BCL10", "VAV1")

# Spearman tolerance for the aggregate near-zero check (brief measured ~-0.01 for IL2).
SPEARMAN_ABS_MAX = 0.15


def _spearman(xs, ys) -> float:
    """Spearman rho = Pearson correlation of the ranks. numpy-only (no scipy dependency)."""
    import numpy as np
    x = np.asarray(xs, dtype="float64")
    y = np.asarray(ys, dtype="float64")
    rx = np.argsort(np.argsort(x))          # ranks (ties broken by position; fine at this scale)
    ry = np.argsort(np.argsort(y))
    rx = rx - rx.mean()
    ry = ry - ry.mean()
    denom = np.sqrt((rx ** 2).sum() * (ry ** 2).sum())
    return float((rx * ry).sum() / denom) if denom else 0.0


def _need(path: str):
    if not os.path.exists(path):
        pytest.skip(f"artifact missing: {path} — run the pipeline (see module docstring)")


def _load_conc():
    _need(_CONC)
    import pandas as pd
    return pd.read_parquet(_CONC)


# ---- (a) self-perturbation exclusion --------------------------------------------------


def test_no_self_perturbation_in_mrna():
    """The IL2-KD-on-IL2 row must never enter the table: a gene lowering its own transcript
    is guide efficacy, not regulation (brief §3.4 hazard 1)."""
    _need(_MRNA)
    import pandas as pd
    df = pd.read_parquet(_MRNA)
    self_rows = df[df["gene"] == df["cytokine"]]
    assert len(self_rows) == 0, f"{len(self_rows)} self-perturbation rows leaked into the mRNA table"


def test_no_self_perturbation_in_concordance():
    df = _load_conc()
    self_rows = df[df["gene"] == df["cytokine"]]
    assert len(self_rows) == 0, "a gene appears as its own cytokine readout in the concordance table"


# ---- (b) aggregate correlation is near zero (the average hides the structure) ----------


def test_aggregate_spearman_is_near_zero():
    """Spearman(z_rna_IL2, schmidt_lfc_IL2) over shared genes must be ~0. A strong
    correlation would mean the join accidentally aligned the two screens (or reused one for
    both sides); the near-zero average is WHY per-gene concordance is worth surfacing."""
    _need(_MRNA)
    import pandas as pd

    cfg = Config.load()
    mrna = pd.read_parquet(_MRNA)
    mrna = mrna[(mrna["cytokine"] == cfg.primary_cytokine) & (mrna["condition"] == "Stim48hr")]
    prot = load_protein(cfg)
    shared = [(r.z_rna, prot[r.gene].lfc) for r in mrna.itertuples(index=False) if r.gene in prot]
    assert len(shared) > 1000, f"too few shared genes to correlate ({len(shared)})"
    zs, lfcs = zip(*shared)
    rho = _spearman(zs, lfcs)
    assert abs(rho) < SPEARMAN_ABS_MAX, (
        f"aggregate Spearman={rho:.3f} is not near zero (|rho|>={SPEARMAN_ABS_MAX}); "
        "the two screens should barely correlate on average — the join may be wrong")


# ---- (c) positive controls replicate; TSC1 is honestly discordant ----------------------


@pytest.mark.parametrize("gene", POSITIVE_CONTROLS)
def test_positive_control_replicates_at_stim48hr(gene):
    """The canonical TCR positive regulators must be REPLICATED at IL2/Stim48hr. If they
    aren't, the join failed to recover known biology and nothing below it is trustworthy."""
    df = _load_conc()
    row = df[(df["gene"] == gene) & (df["condition"] == "Stim48hr")]
    assert not row.empty, f"{gene} absent from the concordance table at Stim48hr"
    verdict = row.iloc[0]["verdict"]
    assert verdict == "replicated", f"{gene} @Stim48hr is '{verdict}', expected 'replicated'"


def test_tsc1_is_not_falsely_replicated():
    """TSC1 lowers IL2 mRNA but RAISES IL2 protein (mTOR brake). Calling it 'replicated'
    would be the overclaim the doctrine forbids — it must be 'discordant'."""
    df = _load_conc()
    row = df[(df["gene"] == "TSC1") & (df["condition"] == "Stim48hr")]
    assert not row.empty, "TSC1 absent at Stim48hr"
    verdict = row.iloc[0]["verdict"]
    assert verdict == "discordant", (
        f"TSC1 @Stim48hr is '{verdict}', expected 'discordant' (mRNA down, protein up)")


# ---- (d) the both-hit population is in the brief's ballpark ----------------------------


def test_replicated_plus_discordant_count_is_about_24_at_stim48hr():
    """The brief predicted ~24 'replicated' at IL2/Stim48hr using a direction-AGNOSTIC 2x2.
    The direction-aware model splits that both-hit population into replicated + discordant;
    their SUM should land in the brief's ballpark (a tolerance band, not an exact count)."""
    df = _load_conc()
    s48 = df[df["condition"] == "Stim48hr"]
    both_hit = len(s48[s48["verdict"].isin(("replicated", "discordant"))])
    assert 12 <= both_hit <= 40, (
        f"both-hit (replicated+discordant) at Stim48hr = {both_hit}, "
        "outside the brief's ~24 ballpark — the join or thresholds may be off")


# ---- (e) honesty invariants from the code review — untested ≠ negative, no guessed sign ----


def test_untested_protein_is_distinct_from_tested_negative():
    """A gene absent from Schmidt's library was NEVER assayed on the protein side — the record
    must mark it prot_tested=False, distinct from a gene tested and found negative. Presenting
    an unmeasured gene as a confident negative is the exact overclaim the doctrine forbids."""
    df = _load_conc()
    assert "prot_tested" in df.columns, "concordance record lost the prot_tested field"
    untested = df[df["lfc_prot"].isna()]
    assert len(untested) > 0, "expected some genes outside Schmidt's protein library"
    assert (untested["prot_tested"] == False).all(), (  # noqa: E712 — pandas boolean mask
        "a gene with no protein measurement is not flagged prot_tested=False")
    tested = df[df["lfc_prot"].notna()]
    assert (tested["prot_tested"] == True).all(), (  # noqa: E712
        "a gene WITH a protein measurement is wrongly flagged untested")


def test_direction_is_never_guessed_for_a_zero_effect():
    """_direction must return None (undefined) for exactly zero — never silently default a
    zero-but-significant effect to 'brake'. A wrong sign here would flip a verdict."""
    from target_triage.core.concordance import _direction
    assert _direction(-1.5) is True, "negative effect must read as 'promotes'"
    assert _direction(2.0) is False, "positive effect must read as 'brake'"
    assert _direction(0.0) is None, "exactly-zero effect must be direction-undefined, not a guess"


def test_classify_marks_an_unassayed_protein_side_untested():
    """Unit-level: classify() with prot=None records prot_tested=False and does not invent a
    protein hit/direction — the mRNA side alone decides the verdict."""
    from target_triage.core.concordance import Config, MrnaEffect, classify
    cfg = Config.load()
    m = MrnaEffect(gene="FAKE", condition="Stim48hr", z=-3.0, q=0.001, promotes=True)
    c = classify(m, None, cfg, gene="FAKE", cytokine="IL2", condition="Stim48hr")
    assert c.prot_tested is False and c.rna_tested is True
    assert c.hit_prot is False and c.prot_promotes is None
    assert c.verdict == "mrna_only", "an mRNA hit with the protein side untested is mrna_only"


# ---- gate runner (prints PASS/FAIL + the numbers) --------------------------------------


def _run_gate() -> bool:
    import pandas as pd

    if not (os.path.exists(_MRNA) and os.path.exists(_CONC)):
        print("CONCORD SANITY GATE — SKIPPED (artifacts not built; see module docstring)")
        return True

    cfg = Config.load()
    conc = pd.read_parquet(_CONC)
    mrna = pd.read_parquet(_MRNA)
    prot = load_protein(cfg)

    print("CONCORD SANITY GATE — the concordance join must recover known biology\n")
    ok = True

    # (a)
    a1 = len(mrna[mrna["gene"] == mrna["cytokine"]]) == 0
    a2 = len(conc[conc["gene"] == conc["cytokine"]]) == 0
    print(f"  [{'PASS' if a1 and a2 else 'FAIL'}] self-perturbation excluded (mRNA + concordance)")
    ok = ok and a1 and a2

    # (b)
    m48 = mrna[(mrna.cytokine == cfg.primary_cytokine) & (mrna.condition == "Stim48hr")]
    shared = [(r.z_rna, prot[r.gene].lfc) for r in m48.itertuples(index=False) if r.gene in prot]
    rho = _spearman(*zip(*shared))
    b = abs(rho) < SPEARMAN_ABS_MAX
    print(f"  [{'PASS' if b else 'FAIL'}] aggregate Spearman(z_rna, schmidt_lfc) = {rho:+.3f} "
          f"(|rho| < {SPEARMAN_ABS_MAX}; near-zero expected)")
    ok = ok and b

    # (c)
    at = conc[conc.condition == "Stim48hr"].set_index("gene")
    for g in POSITIVE_CONTROLS:
        v = at.loc[g]["verdict"] if g in at.index else "ABSENT"
        p = v == "replicated"
        print(f"  [{'PASS' if p else 'FAIL'}] {g:6s} @Stim48hr = {v} (expected replicated)")
        ok = ok and p
    tsc1 = at.loc["TSC1"]["verdict"] if "TSC1" in at.index else "ABSENT"
    pt = tsc1 == "discordant"
    print(f"  [{'PASS' if pt else 'FAIL'}] TSC1   @Stim48hr = {tsc1} (expected discordant, not replicated)")
    ok = ok and pt

    # (d)
    both = len(at[at["verdict"].isin(("replicated", "discordant"))])
    d = 12 <= both <= 40
    print(f"  [{'PASS' if d else 'FAIL'}] both-hit at Stim48hr = {both} (brief ~24 ballpark)")
    ok = ok and d

    print(f"\n{'PASS' if ok else 'FAIL'} — Concord sanity gate")
    return ok


if __name__ == "__main__":
    sys.exit(0 if _run_gate() else 1)
