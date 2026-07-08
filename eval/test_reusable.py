"""REUSABILITY GATE — the same instrument must work on more than one screen.

This is the proof that Target Triage is a TOOL, not a one-off Marson analysis: the
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

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from target_triage.core.controls import run_controls_gate  # noqa: E402
from target_triage.core.data import load_screen  # noqa: E402
from target_triage.core.schema import MARSON, SCHMIDT2022  # noqa: E402


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
