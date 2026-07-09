"""VERIFIER GATE — the adversarial verifier must have teeth.

A verifier that never rejects is theater. This eval asserts it REJECTS noise and
PROMOTES real regulators, on real data — the credibility layer judges must trust.

  - A1BG (housekeeping noise, no real KD) MUST be REJECTED on a gate.
  - CBLB, RASA2 (known regulators) MUST be PROMOTED.
  - At least one gene in a demo panel must actually be rejected (teeth exist).

Run:    python eval/test_verify.py        (prints verdicts + numbers)
        pytest eval/test_verify.py
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from target_triage.core.data import load_marson  # noqa: E402
from target_triage.core.evidence import load_evidence  # noqa: E402
from target_triage.core.verify import verify  # noqa: E402

PANEL = ("CBLB", "RASA2", "CD5", "TNFAIP3", "DGKA", "A1BG")


def _verdicts():
    by_gene = {r.gene: r for r in load_marson()}
    ev = load_evidence()
    return {g: verify(by_gene[g], ev) for g in PANEL if g in by_gene}, by_gene


def test_noise_is_rejected():
    verdicts, _ = _verdicts()
    assert verdicts["A1BG"].verdict == "REJECT", verdicts["A1BG"].reason


@pytest.mark.parametrize("gene", ["CBLB", "RASA2"])
def test_known_regulator_promoted(gene):
    verdicts, _ = _verdicts()
    assert verdicts[gene].verdict.startswith("PROMOTE"), verdicts[gene].reason


def test_verifier_has_teeth():
    verdicts, _ = _verdicts()
    assert any(v.verdict == "REJECT" for v in verdicts.values()), \
        "verifier rejected nothing in the panel — no teeth"


if __name__ == "__main__":
    verdicts, _ = _verdicts()
    print("VERIFIER GATE — adversarial checks on real data\n")
    for gene in PANEL:
        v = verdicts.get(gene)
        if v is None:
            print(f"  {gene:8s} not in screen")
            continue
        print(f"  {gene:8s} -> {v.verdict:<24} ({v.reason})")
        for c in v.checks:
            mark = "PASS" if c.passed else "FAIL"
            print(f"       [{mark}] {c.kind:5s} {c.name:16s} val={str(c.value):>6}  {c.detail}")
        print()
    # gate summary
    a1bg_ok = verdicts["A1BG"].verdict == "REJECT"
    heroes_ok = all(verdicts[g].verdict.startswith("PROMOTE") for g in ("CBLB", "RASA2"))
    teeth = any(v.verdict == "REJECT" for v in verdicts.values())
    ok = a1bg_ok and heroes_ok and teeth
    print(f"A1BG rejected: {a1bg_ok} | heroes promoted: {heroes_ok} | has teeth: {teeth}")
    print("GATE:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)
