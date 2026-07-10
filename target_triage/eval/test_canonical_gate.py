"""NODE A GATE — the ingestion agent may never move the hub's numbers (brief §1.6).

Concord's whole claim is *agents on the rim, deterministic instrument at the hub*. This file is
the proof. It asserts that routing the two known screens through the agent's canonical schema
reproduces the concordance the core already computes -- and that the three hazards fire on
deliberately broken inputs rather than passing quietly.

The acceptance rule, stated in the brief and enforced here:

    if adding the agents changes any of the hub's numbers, the agents are wrong.

Six gates:

  (a) ROUND-TRIP IDENTITY (the critical one). Schmidt + Zhu, read through the canonical schema
      rather than the hardcoded loaders, must reproduce the live concordance table exactly:
      every verdict, and the ~24 both-hit population at IL2/Stim48hr.

  (b) The mapping path and the hand-written adapter agree row-for-row on the same file. Two
      independent readings of one screen; any disagreement means one of them is lying.

  (c) SIGN-FLIP INJECTION. Invert a screen's effect column and Hazard 3 must catch and correct
      it. Freimer needs no injection -- it is genuinely inverted in the repo -- so it is tested
      as the real case, and Schmidt is inverted synthetically as the controlled one.

  (d) MIXED-REGIME INJECTION. A file offering both `adj_p_value` and `lfsr` must select one by
      policy, record what it discarded, and never pool them (Hazard 2).

  (e) AMBIGUOUS-COLUMN INJECTION. A low-confidence mapping must raise NeedsConfirmation for a
      human, and an unfindable axis must raise rather than be guessed.

  (f) The agent cannot smuggle `is_ontarget`, a non-finite number, or an out-of-range probability
      past the validator -- the properties the core relies on and never re-checks.

The model is never called here. Every test drives the deterministic proposer and the validator,
so this gate runs on a keyless clone and in CI. What it proves about the model is structural: the
model's ONLY output is a ColumnMapping, and a ColumnMapping cannot express any of the failures
above without being caught.

Run as a gate:    python target_triage/eval/test_canonical_gate.py    (prints PASS/FAIL)
Run under pytest: pytest target_triage/eval/test_canonical_gate.py
"""
from __future__ import annotations

import csv
import json
import os
import sys
import tempfile
from dataclasses import replace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from target_triage.core.canonical import (  # noqa: E402
    Modality, SchemaViolation, SignifRegime, apply_sign_correction, check_sign_convention,
    choose_regime, derive_ontarget, drop_ontarget, validate)
from target_triage.core.concordance import (  # noqa: E402
    Config, MrnaEffect, ProteinEffect, _direction, classify)
from target_triage.rim.adapters import (  # noqa: E402
    FREIMER_CSV, SCHMIDT_CSV, freimer_rows, schmidt_rows, zhu_rows)
from target_triage.rim.ingest import (  # noqa: E402
    EXPERIMENT_CONSTANTS, AxisExtract, NeedsConfirmation, SignifCombine, _parse_mapping,
    ingest_csv, profile_csv, propose_heuristic, regimes_in, user_prompt)

_APP = os.path.join(os.path.dirname(__file__), "..", "..")
_MRNA = os.path.join(_APP, "target_triage", "data", "artifacts", "cytokine_mrna_effects.parquet")
_CONC = os.path.join(_APP, "target_triage", "data", "artifacts", "concordance.parquet")

# The brief's numbers. These are the hub's, measured before the agents existed.
BOTH_HIT_STIM48 = 24
POSITIVE_CONTROLS = {"ITK": "replicated", "BCL10": "replicated", "VAV1": "replicated",
                     "TSC1": "discordant"}


def _need(path: str):
    if not os.path.exists(path):
        pytest.skip(f"artifact missing: {path} — run the pipeline first")


def _schmidt_mapping():
    """The mapping a proposer produces for Schmidt: cytokine and cell type both come from the
    fused `phenotype` column, via the two named extraction rules."""
    return propose_heuristic(
        profile_csv(SCHMIDT_CSV), "schmidt2022", modality=Modality.PROTEIN,
        cytokine_col="phenotype", cytokine_extract=AxisExtract.LAST_TOKEN,
        cell_type_col="phenotype", cell_type_extract=AxisExtract.FIRST_TOKEN,
        condition_const="Stimulated")


def _il2_only(rec: dict) -> bool:
    return rec.get("phenotype") == "CD4+ IL2"


def _freimer_mapping(screen_id: str = "freimer2022_il2"):
    return propose_heuristic(
        profile_csv(FREIMER_CSV), screen_id, modality=Modality.PROTEIN,
        cytokine_col="screen", condition_const="Stimulated", cell_type_const="CD4")


# ---- (a) ROUND-TRIP IDENTITY: the canonical path must reproduce the live table ----------


def _verdicts_via_canonical() -> dict[tuple[str, str], str]:
    """Build the IL2 verdicts from CANONICAL rows only -- no hardcoded loader anywhere."""
    import pandas as pd
    cfg = Config.load()

    prot_rows = drop_ontarget(ingest_csv(SCHMIDT_CSV, _schmidt_mapping(),
                                         row_filter=_il2_only).rows)
    mrna_df = pd.read_parquet(_MRNA)
    mrna_rows = drop_ontarget(zhu_rows(mrna_df[mrna_df.cytokine == "IL2"].to_dict("records"),
                                       cytokine="IL2"))

    prot = {r.gene: ProteinEffect(gene=r.gene, lfc=r.effect_size, q=round(r.signif_value, 6),
                                  promotes=_direction(r.effect_size)) for r in prot_rows}
    out: dict[tuple[str, str], str] = {}
    for r in mrna_rows:
        m = MrnaEffect(gene=r.gene, condition=r.condition, z=r.effect_size, q=r.signif_value,
                       promotes=_direction(r.effect_size))
        c = classify(m, prot.get(r.gene), cfg, gene=r.gene, cytokine="IL2", condition=r.condition)
        out[(r.gene, r.condition)] = c.verdict
    return out


def test_roundtrip_identity_reproduces_every_live_verdict():
    """THE gate. Harmonizing the two known screens through the agent's schema must reproduce the
    hub's concordance table verdict-for-verdict. If it does not, the canonical abstraction has
    changed the science, and every downstream number is suspect."""
    _need(_MRNA)
    _need(_CONC)
    import pandas as pd

    canon = _verdicts_via_canonical()
    live = pd.read_parquet(_CONC)
    assert len(canon) == len(live), (
        f"canonical path produced {len(canon)} rows, live table has {len(live)}")

    mismatches = [(r.gene, r.condition, r.verdict, canon.get((r.gene, r.condition)))
                  for r in live.itertuples(index=False)
                  if canon.get((r.gene, r.condition)) != r.verdict]
    assert not mismatches, (
        f"{len(mismatches)} verdicts changed when read through the canonical schema "
        f"(first 5: {mismatches[:5]}). The ingestion agent moved the hub's numbers.")


def test_roundtrip_preserves_the_both_hit_population():
    """The ~24 both-hit genes at IL2/Stim48hr are the brief's headline number."""
    _need(_MRNA)
    canon = _verdicts_via_canonical()
    both = sum(1 for (g, cond), v in canon.items()
               if cond == "Stim48hr" and v in ("replicated", "discordant"))
    assert both == BOTH_HIT_STIM48, (
        f"both-hit at Stim48hr = {both} via the canonical path, expected {BOTH_HIT_STIM48}")


@pytest.mark.parametrize("gene,expected", sorted(POSITIVE_CONTROLS.items()))
def test_roundtrip_preserves_the_positive_controls(gene, expected):
    """ITK/BCL10/VAV1 replicate and TSC1 stays honestly discordant -- through the agent's schema."""
    _need(_MRNA)
    canon = _verdicts_via_canonical()
    assert canon.get((gene, "Stim48hr")) == expected, (
        f"{gene} @Stim48hr = {canon.get((gene, 'Stim48hr'))!r} via canonical path, "
        f"expected {expected!r}")


# ---- (b) two independent readings of one file must agree --------------------------------


def test_mapping_path_agrees_with_handwritten_adapter():
    """The mapping-driven reader and the hand-written adapter are independent implementations of
    'read Schmidt'. Any disagreement means one of them is wrong, and the agent path is the one
    that would ship."""
    via_mapping = ingest_csv(SCHMIDT_CSV, _schmidt_mapping(), row_filter=_il2_only).rows
    via_adapter = schmidt_rows("CD4+ IL2")

    a = {(r.gene, r.cytokine): (r.effect_size, r.signif_value, r.cell_type, r.is_ontarget)
         for r in via_mapping}
    b = {(r.gene, r.cytokine): (r.effect_size, r.signif_value, r.cell_type, r.is_ontarget)
         for r in via_adapter}
    assert set(a) == set(b), "the two readers disagree on WHICH rows the screen contains"
    diff = {k for k in a if a[k] != b[k]}
    assert not diff, f"{len(diff)} rows differ between mapping and adapter, e.g. {list(diff)[:3]}"


def test_mageck_significance_folds_both_one_sided_tests():
    """MAGeCK reports two one-sided FDRs. Mapping only `neg|fdr` would discard every gene that is
    significant only in the other direction -- measured: 590 of 663 protein hits at q<0.10, i.e.
    an entire quadrant of the 2x2, silently. The proposer must find both and fold them with min()."""
    m = _schmidt_mapping()
    assert m.signif_combine is SignifCombine.MIN_OF, (
        f"combine={m.signif_combine} -- a single MAGeCK FDR column loses ~89% of protein hits")
    assert set(m.signif_cols) == {"neg|fdr", "pos|fdr"}, m.signif_cols

    rows = ingest_csv(SCHMIDT_CSV, m, row_filter=_il2_only).rows
    hits = sum(1 for r in rows if r.signif_value < 0.10)
    assert hits > 600, f"only {hits} protein hits at q<0.10; expected ~663 via min(neg,pos)"


# ---- (c) SIGN-FLIP: the real inverted screen, and a synthetic injection ------------------


def test_freimer_is_genuinely_inverted_and_is_corrected():
    """Freimer is not a fixture -- it is really inverted in the repo (it sorted marker-LOW, so a
    knockdown that depletes the cytokine ENRICHES the sorted population). Its on-target rows are
    positive where Schmidt's are negative. Hazard 3 must catch it from the data alone."""
    raw = freimer_rows("IL2")                       # adapter reports the file faithfully
    src = check_sign_convention(raw, "freimer2022")
    assert src.confident and src.inverted, f"expected an inverted screen, got: {src.detail}"
    assert src.n_positive > 0 and src.n_negative == 0

    result = ingest_csv(FREIMER_CSV, _freimer_mapping(),
                        row_filter=lambda r: r.get("screen") == "IL2")

    assert result.sign_corrected, "an inverted screen was ingested without correcting its sign"
    assert result.source_report.sign_check.inverted, "the manifest must record the source evidence"
    assert not result.report.sign_check.inverted, "post-correction rows are still inverted"
    on = [r for r in result.rows if r.is_ontarget]
    assert on and all(r.effect_size < 0 for r in on), (
        "after correction, knocking a gene down must LOWER its own product")


def test_freimer_pooled_readouts_give_a_unanimous_calibration():
    """Pooling Freimer's three readouts (IL2, IL2RA, CTLA4) yields three on-target rows, all
    positive -- a unanimous signal, not a single-observation coin flip."""
    pooled = tuple(r for cy in ("IL2", "IL2RA", "CTLA4") for r in freimer_rows(cy))
    chk = check_sign_convention(pooled, "freimer2022", min_ontarget=3)
    assert chk.confident and chk.inverted
    assert (chk.n_positive, chk.n_negative) == (3, 0), (chk.n_positive, chk.n_negative)


def test_injected_sign_flip_on_a_correct_screen_is_caught():
    """Controlled injection: take Schmidt (known correct), invert its effect column, and assert
    Hazard 3 flags it. Then correct it and assert the original values return exactly."""
    good = schmidt_rows("CD4+ IL2")
    assert not check_sign_convention(good, "schmidt2022").inverted

    flipped = tuple(replace(r, effect_size=0.0 - r.effect_size) for r in good)
    chk = check_sign_convention(flipped, "schmidt2022")
    assert chk.confident and chk.inverted, f"injected inversion not detected: {chk.detail}"

    restored = apply_sign_correction(flipped, chk)
    assert [r.effect_size for r in restored] == [r.effect_size for r in good], (
        "correcting an injected flip did not restore the original effect column")


def test_sign_check_refuses_when_on_target_rows_disagree():
    """A split calibration signal is not a majority vote -- it is an unreliable standard. The check
    must return confident=False so the caller stops, rather than guessing a polarity."""
    rows = list(schmidt_rows("CD4+ IL2"))[:5]
    a = derive_ontarget(replace(rows[0], gene="IL2", cytokine="IL2", effect_size=-2.0))
    b = derive_ontarget(replace(rows[1], gene="IL2", cytokine="IL2", effect_size=+2.0))
    chk = check_sign_convention((a, b), "conflicted")
    assert not chk.confident and not chk.ok, "disagreeing on-target rows must refuse, not vote"
    with pytest.raises(SchemaViolation, match="cannot correct sign"):
        apply_sign_correction((a, b), chk)


def test_zhu_cannot_self_calibrate_and_says_so():
    """The mRNA artifact had its on-target rows removed upstream (Hazard 1 at extraction). The
    sign check must report confident=False -- 'I could not check' -- rather than the false comfort
    of inverted=False, which would read as 'I checked and it was fine'."""
    _need(_MRNA)
    import pandas as pd
    df = pd.read_parquet(_MRNA)
    rows = zhu_rows(df[df.cytokine == "IL2"].to_dict("records"), cytokine="IL2")
    chk = check_sign_convention(rows, "zhu2025")
    assert chk.n_ontarget == 0, "the mRNA artifact should carry no on-target rows"
    assert not chk.confident, "a screen with no calibration standard must not report a verdict"
    assert "refusing to guess" in chk.detail


# ---- (d) MIXED-REGIME INJECTION (Hazard 2) ----------------------------------------------


def _write_csv(path: str, header: list[str], rows: list[list]):
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)


def test_mixed_regime_file_selects_one_by_policy_and_records_the_discard():
    """A file carrying BOTH `adj_p_value` and `lfsr` must pick one by the recorded policy, name
    what it discarded, and never pool them. An adj-p of 0.09 and an lfsr of 0.09 are different
    claims; averaging or silently preferring one is how a mixed-regime table gets built."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "mixed.csv")
        _write_csv(path, ["gene", "cytokine", "log2fc", "adj_p_value", "lfsr"],
                   [["IL2", "IL2", "-1.5", "0.001", "0.002"],
                    ["ITK", "IL2", "-2.0", "0.01", "0.02"],
                    ["FOO", "IL2", "0.5", "0.9", "0.8"]])

        found = regimes_in(["gene", "cytokine", "log2fc", "adj_p_value", "lfsr"])
        assert SignifRegime.DESEQ2_ADJP in found and SignifRegime.MASH_LFSR in found, found
        assert choose_regime(found) is SignifRegime.DESEQ2_ADJP, "policy prefers DESeq2 adj-p"

        m = propose_heuristic(profile_csv(path), "mixed", modality=Modality.RNA,
                              cytokine_col="cytokine", condition_const="Rest",
                              cell_type_const="CD4")
        assert m.signif_regime is SignifRegime.DESEQ2_ADJP
        assert SignifRegime.MASH_LFSR in m.regimes_discarded, (
            "the discarded regime must be recorded, not silently dropped")

        rows = ingest_csv(path, m).rows
        # every value must come from adj_p_value, never lfsr, never a blend
        by_gene = {r.gene: r.signif_value for r in rows}
        assert by_gene["ITK"] == 0.01, f"ITK significance {by_gene['ITK']} is not the adj_p value"
        assert all(r.signif_regime is SignifRegime.DESEQ2_ADJP for r in rows)


def test_validator_rejects_a_table_that_mixes_regimes():
    """Belt and braces: even if a mapping somehow produced two regimes, the gate refuses."""
    rows = list(schmidt_rows("CD4+ IL2"))[:10]
    rows[3] = replace(rows[3], signif_regime=SignifRegime.MASH_LFSR)
    with pytest.raises(SchemaViolation, match="Hazard 2"):
        validate(tuple(rows), "schmidt2022")


# ---- (e) AMBIGUOUS COLUMN: confirm, never guess ------------------------------------------


def test_unfindable_axis_raises_rather_than_being_guessed():
    """A file with no recognisable effect column must fail loudly. Guessing which column is the
    effect size is precisely the silent error the whole gate exists to prevent."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "opaque.csv")
        _write_csv(path, ["thing", "measurement_a", "measurement_b"],
                   [["IL2", "1.0", "0.5"], ["ITK", "2.0", "0.1"]])
        with pytest.raises(SchemaViolation, match="could not locate"):
            propose_heuristic(profile_csv(path), "opaque", modality=Modality.RNA,
                              cytokine_const="IL2", condition_const="Rest", cell_type_const="CD4")


def test_low_confidence_mapping_is_surfaced_for_confirmation():
    """Below CONFIRM_THRESHOLD the mapping goes to a human. The model does not get to resolve its
    own ambiguity by lowering the bar."""
    m = replace(_schmidt_mapping(), confidence=0.40, proposer="claude",
                rationale="unsure which column is the effect size")
    with pytest.raises(NeedsConfirmation) as exc:
        ingest_csv(SCHMIDT_CSV, m, row_filter=_il2_only)
    assert exc.value.mapping.confidence == 0.40
    assert "0.40" in str(exc.value)

    # ...and an explicit human override is what lets it through, not a retry.
    ok = ingest_csv(SCHMIDT_CSV, m, row_filter=_il2_only, require_confirmation=False)
    assert len(ok.rows) > 0


# ---- (f) the agent cannot smuggle anything past the validator ----------------------------


def test_agent_cannot_supply_is_ontarget():
    """`is_ontarget` is derived from the data. A row claiming otherwise is rejected -- so a bad
    mapping can neither hide a contamination row nor mislabel a real regulatory row as one."""
    rows = list(schmidt_rows("CD4+ IL2"))[:5]
    rows[0] = replace(rows[0], is_ontarget=True)          # a lie: gene != cytokine
    with pytest.raises(SchemaViolation, match="contradicts the data"):
        validate(tuple(rows), "schmidt2022")


def test_validator_rejects_non_finite_and_out_of_range_values():
    """NaN would poison every threshold comparison silently; a 'p-value' of 3.4 means the wrong
    column was mapped to significance."""
    rows = list(schmidt_rows("CD4+ IL2"))[:5]

    nan_rows = list(rows)
    nan_rows[1] = replace(nan_rows[1], effect_size=float("nan"))
    with pytest.raises(SchemaViolation, match="not finite"):
        validate(tuple(nan_rows), "schmidt2022")

    bad_p = list(rows)
    bad_p[2] = replace(bad_p[2], signif_value=3.4)
    with pytest.raises(SchemaViolation, match=r"outside \[0,1\]"):
        validate(tuple(bad_p), "schmidt2022")


def test_validator_rejects_an_empty_ingest():
    """An agent that mapped nothing must not report success."""
    with pytest.raises(SchemaViolation, match="zero canonical rows"):
        validate((), "empty")


def test_unmapped_columns_are_reported_not_dropped():
    """Every column the mapping did not use is carried into the report (and thence the manifest).
    A column that vanishes without a trace is a column nobody can audit."""
    result = ingest_csv(SCHMIDT_CSV, _schmidt_mapping(), row_filter=_il2_only)
    assert result.report.unmapped_columns, "no unmapped columns recorded for a 16-column file"
    assert "pos|lfc" in result.report.unmapped_columns
    assert "" in result.report.unmapped_columns, (
        "Schmidt's unnamed leading index column must be reported as unmapped, not ignored")


# ---- (g) what a live model actually does — pinned so the findings cannot rot ---------------
#
# These encode behaviour observed from real Claude proposals while building Node A. Each is a
# property of the SYSTEM (prompt + parser + gate), so it is testable without calling the model.


def test_the_model_may_not_supply_an_experiment_constant():
    """Asked to map Schmidt, Claude proposed condition_const="unstimulated" -- three runs, varying
    the capitalization. The sort was on STIMULATED cells, and a varying constant would silently make
    the join key nondeterministic. Both errors are schema-VALID, so no validator can catch them.

    The defence is therefore structural, not a check: a constant is a claim about the EXPERIMENT,
    not about the columns, so the model is never asked for one and any it volunteers is discarded in
    favour of the caller's. The discard is recorded in the rationale, which lands in the manifest.
    """
    payload = json.dumps({
        "gene_col": "id", "effect_col": "neg|lfc", "signif_cols": ["neg|fdr", "pos|fdr"],
        "signif_combine": "min_of", "signif_regime": "benjamini_fdr",
        "cytokine_col": "phenotype", "cytokine_extract": "last_token",
        "cell_type_col": "phenotype", "cell_type_extract": "first_token",
        "condition_const": "unstimulated",          # confabulated: not in any column
        "cell_type_const": "CD8",                   # confabulated: contradicts the phenotype column
        "confidence": 0.9, "rationale": "looks like MAGeCK",
    })
    m = _parse_mapping(payload, "schmidt2022", Modality.PROTEIN,
                       {"condition_const": "Stimulated"})
    assert m.condition_const == "Stimulated", "the model's experiment constant overrode the human's"
    assert m.cell_type_const is None, "a volunteered constant leaked in for a column-backed axis"
    assert "discarded model-supplied experiment constants" in m.rationale, (
        "the manifest must record that the model tried to state an experiment fact")

    # ...and the prompt must not even ask for them, or the model will keep answering.
    prompt = user_prompt(profile_csv(SCHMIDT_CSV), "schmidt2022", Modality.PROTEIN)
    for field in EXPERIMENT_CONSTANTS:
        assert field not in prompt, f"the prompt still solicits {field!r} from the model"


def test_mageck_lfc_columns_are_interchangeable_so_either_choice_is_equivalent():
    """A live Claude proposal chose `pos|lfc` where the heuristic chose `neg|lfc`, with the correct
    rationale ("identical values"). That is only safe if it is TRUE of the data -- a different
    choice producing the same answer by luck is exactly what this project refuses to accept.

    Verified here on both bundled MAGeCK screens: the two columns never differ, in any row. If a
    future screen breaks this, the mapping choice starts to matter and this test says so first.
    """
    for path, key, want in ((SCHMIDT_CSV, "phenotype", "CD4+ IL2"), (FREIMER_CSV, "screen", "IL2")):
        with open(path, newline="", encoding="utf-8-sig") as fh:
            rows = [r for r in csv.DictReader(fh) if (r.get(key) or "").strip() == want]
        assert rows, f"no rows for {want} in {os.path.basename(path)}"
        differing = [r for r in rows if r["neg|lfc"] != r["pos|lfc"]]
        assert not differing, (
            f"{os.path.basename(path)}: {len(differing)} rows where neg|lfc != pos|lfc -- the "
            "effect-column choice is no longer arbitrary and the mapping must pin it")


def test_a_low_confidence_live_proposal_stops_the_run_rather_than_guessing():
    """Observed live: Claude returned confidence 0.72 on Schmidt because choosing `neg|lfc` over
    `pos|lfc` "assumes the biological direction of interest is depletion" -- a real ambiguity it
    could not resolve from a column profile. Escalating was correct. The run must stop, and only an
    explicit human override may let it through."""
    m = replace(_schmidt_mapping(), confidence=0.72, proposer="claude",
                rationale="effect_col choice assumes the direction of interest is depletion")
    with pytest.raises(NeedsConfirmation):
        ingest_csv(SCHMIDT_CSV, m, row_filter=_il2_only)
    accepted = ingest_csv(SCHMIDT_CSV, m, row_filter=_il2_only, require_confirmation=False)
    assert accepted.report.n_rows > 0
    # the low confidence stays on the record even once a human accepts it
    assert accepted.mapping.confidence == 0.72


# ---- gate runner -------------------------------------------------------------------------


def _run_gate() -> bool:
    """Print the numbers, PASS/FAIL. Mirrors the house style of test_concord_sanity.py."""
    print("NODE A GATE — the ingestion agent must not move the hub's numbers\n")
    if not (os.path.exists(_MRNA) and os.path.exists(_CONC)):
        print("  SKIPPED (artifacts not built)")
        return True

    import pandas as pd
    ok = True

    canon = _verdicts_via_canonical()
    live = pd.read_parquet(_CONC)
    mismatch = sum(1 for r in live.itertuples(index=False)
                   if canon.get((r.gene, r.condition)) != r.verdict)
    a = mismatch == 0 and len(canon) == len(live)
    print(f"  [{'PASS' if a else 'FAIL'}] round-trip identity: {len(canon):,} rows, "
          f"{mismatch} verdict mismatches vs the live table")
    ok = ok and a

    both = sum(1 for (g, c), v in canon.items()
               if c == "Stim48hr" and v in ("replicated", "discordant"))
    b = both == BOTH_HIT_STIM48
    print(f"  [{'PASS' if b else 'FAIL'}] both-hit @Stim48hr = {both} (expected {BOTH_HIT_STIM48})")
    ok = ok and b

    for gene, expected in sorted(POSITIVE_CONTROLS.items()):
        got = canon.get((gene, "Stim48hr"))
        p = got == expected
        print(f"  [{'PASS' if p else 'FAIL'}] {gene:6s} @Stim48hr = {got} (expected {expected})")
        ok = ok and p

    src = check_sign_convention(freimer_rows("IL2"), "freimer2022")
    c = src.confident and src.inverted
    print(f"  [{'PASS' if c else 'FAIL'}] Hazard 3 on the real inverted screen (Freimer): "
          f"inverted={src.inverted} confident={src.confident}")
    ok = ok and c

    zhu_chk = check_sign_convention(
        zhu_rows(pd.read_parquet(_MRNA).query("cytokine=='IL2'").to_dict("records"),
                 cytokine="IL2"), "zhu2025")
    d = not zhu_chk.confident
    print(f"  [{'PASS' if d else 'FAIL'}] Zhu honestly reports it cannot self-calibrate "
          f"(confident={zhu_chk.confident})")
    ok = ok and d

    print(f"\n{'PASS' if ok else 'FAIL'} — Node A gate")
    return ok


if __name__ == "__main__":
    sys.exit(0 if _run_gate() else 1)
