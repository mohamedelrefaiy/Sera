"""REUSABILITY GATE — the same instrument must work on more than one screen.

This is the proof that Sera is a TOOL, not a one-off Marson analysis: the
identical pipeline (schema-mapped load -> controls gate) recovers known biology on
two structurally different screens.
  - Marson: Perturb-seq, has breadth, boolean significance, 3 conditions.
  - Schmidt2022: MAGeCK CRISPRi, NO breadth column, FDR-derived significance, 2 readouts.
If the controls gate passes on both, the abstraction holds and the tool is reusable.

Run:  pytest eval/test_reusable.py
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from sera.core.controls import run_controls_gate  # noqa: E402
from sera.core.data import load_screen  # noqa: E402
from sera.core.schema import MARSON, SCHMIDT2022  # noqa: E402


@pytest.mark.parametrize("schema", [MARSON, SCHMIDT2022], ids=lambda s: s.name)
def test_controls_gate_passes_on_each_screen(schema):
    gate = run_controls_gate(schema)
    failed = [r.gene for r in gate.results if not r.passed]
    assert gate.passed, f"{schema.name}: controls failed -> {failed}"


def test_schmidt_has_no_breadth_and_degrades():
    """The abstraction must handle a screen with a missing signal, not crash."""
    records = load_screen(SCHMIDT2022)
    assert len(records) > 1000
    # every Schmidt perturbation has n_downstream = None (no breadth column)
    sample = next(iter(records))
    assert all(p.n_downstream is None for p in sample.by_condition.values())


def test_marson_still_has_breadth():
    records = load_screen(MARSON)
    by = {r.gene: r for r in records}
    rasa2 = by["RASA2"]
    assert any(p.n_downstream is not None and p.n_downstream > 0
               for p in rasa2.by_condition.values())


# ---- impact axis: each screen declares the quantity it can honestly plot ----
# See docs/adr/0001-screens-declare-their-own-capabilities.md. Marson has breadth but
# no per-perturbation p-value; Schmidt2022 has an FDR but no breadth. Neither screen's
# axis is assumed by the chart — it is read from the schema.


def test_each_screen_declares_an_impact_axis():
    assert MARSON.impact_axis == "breadth"
    assert SCHMIDT2022.impact_axis == "neg_log10_fdr"


def test_marson_carries_breadth_and_no_fdr():
    """Marson reports no FDR. The field must be None, never 0.0 (which would plot as
    infinitely significant)."""
    by_gene = {r.gene: r for r in load_screen(MARSON)}
    perts = list(by_gene["RASA2"].by_condition.values())
    assert all(p.fdr is None for p in perts), "Marson has no FDR column"
    assert any(p.n_downstream is not None for p in perts)


def test_schmidt_carries_fdr_and_no_breadth():
    """Schmidt's impact axis is -log10(FDR), so the raw float must survive the loader."""
    by_gene = {r.gene: r for r in load_screen(SCHMIDT2022)}
    perts = list(by_gene["ZAP70"].by_condition.values())
    assert all(p.n_downstream is None for p in perts), "Schmidt has no breadth column"
    assert any(isinstance(p.fdr, float) for p in perts), "FDR float must be retained"
    assert all(p.fdr is None or 0.0 <= p.fdr <= 1.0 for p in perts)


def test_significance_still_derives_from_the_retained_fdr():
    """Splitting out the float must not change what 'significant' means."""
    for rec in load_screen(SCHMIDT2022):
        for p in rec.by_condition.values():
            if p.fdr is not None:
                assert p.significant == (p.fdr < SCHMIDT2022.fdr_max)
