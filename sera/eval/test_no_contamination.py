"""CONTAMINATION GATE — a screen may never corroborate itself, nor be judged by
another screen's biology.

Selecting a screen as PRIMARY must remove it from the held-out evidence set. If it
stayed, `check_held_out` would award a BONUS for a gene being a significant hit in
the very file under analysis, and verify() would escalate the verdict to
"PROMOTE (corroborated)" — the tool announcing independent confirmation of a gene
against its own data. That is the exact failure the computation-grounded verifier
exists to prevent.

Likewise, "obvious hits" and "spotlight" genes are properties of a SCREEN, not of the
tool. Marson's obvious set is the TCR signalosome; those same genes are Schmidt2022's
declared positive controls. Applying Marson's set to Schmidt would penalise the genes
Schmidt uses to prove it loaded correctly.

See docs/adr/0001-screens-declare-their-own-capabilities.md.

Run:  pytest eval/test_no_contamination.py
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from sera.core.evidence import load_evidence  # noqa: E402
from sera.core.schema import MARSON, REGISTRY, SCHMIDT2022  # noqa: E402


# ---- 1. a primary screen is never its own held-out evidence ----


def test_schmidt_primary_is_excluded_from_its_own_evidence():
    ev = load_evidence(primary="schmidt2022")
    sources = {hit.screen for hits in ev.screen_hits.values() for hit in hits}
    assert "Schmidt2022" not in sources, "Schmidt corroborated itself"
    assert "Freimer2022" in sources, "the remaining held-out screen must survive"


def test_marson_primary_keeps_both_external_screens():
    """Marson is not an external evidence screen, so nothing is excluded."""
    ev = load_evidence(primary="marson")
    sources = {hit.screen for hits in ev.screen_hits.values() for hit in hits}
    assert sources == {"Schmidt2022", "Freimer2022"}


def test_zap70_loses_its_corroboration_when_schmidt_is_primary():
    """ZAP70 is a Schmidt control and a Schmidt hit. Under Schmidt-primary it must not
    be corroborated BY Schmidt. Any surviving hit must come from Freimer."""
    ev = load_evidence(primary="schmidt2022")
    for hit in ev.screen_hits.get("ZAP70", ()):
        assert hit.screen != "Schmidt2022"


# ---- 2. obvious hits and spotlight are declared per screen ----


def test_each_screen_declares_its_own_obvious_hits():
    assert "ZAP70" in MARSON.obvious, "TCR signalosome is obvious in Marson"
    assert "ZAP70" not in SCHMIDT2022.obvious, (
        "ZAP70 is a declared positive control of Schmidt2022 — penalising it would "
        "bury the gene that proves the screen loaded"
    )


def test_a_screens_controls_are_never_its_obvious_hits():
    """The two sets must not intersect on ANY registered screen: a gene cannot both
    prove the screen loaded and be ranked down for being unsurprising."""
    for name, schema in REGISTRY.items():
        overlap = set(schema.controls) & set(schema.obvious)
        assert not overlap, f"{name}: controls also marked obvious -> {sorted(overlap)}"


def test_spotlight_is_per_screen_not_global():
    assert "PTPN2" in MARSON.spotlight
    # PTPN2/CBLB are brakes and are not significant hits in Schmidt (see schema.py).
    assert "PTPN2" not in SCHMIDT2022.spotlight


@pytest.mark.parametrize("schema", [MARSON, SCHMIDT2022], ids=lambda s: s.name)
def test_spotlight_genes_are_annotated_with_a_compound(schema):
    for gene, meta in schema.spotlight.items():
        assert meta.get("compound"), f"{schema.name}:{gene} has no checkable molecule"


# ---- 3. end-to-end: the shortlist itself must be uncontaminated ----


def test_schmidt_shortlist_never_corroborates_itself():
    """The bug this file exists to prevent, asserted on the real pipeline: no gene may
    reach PROMOTE (corroborated) on the strength of the screen it was ranked from."""
    from sera.core.shortlist import compute_shortlist

    rows = compute_shortlist(SCHMIDT2022, annotate=False)
    assert rows, "Schmidt must produce a shortlist at all"

    corroborated = [r["gene"] for r in rows if r["verdict"] == "PROMOTE (corroborated)"]
    ev = load_evidence(primary="schmidt2022")
    for gene in corroborated:
        sources = {h.screen for h in ev.screen_hits[gene]}
        assert "Schmidt2022" not in sources, f"{gene} corroborated by its own screen"


def test_schmidt_controls_are_not_ranked_down_as_obvious():
    """Schmidt's positive controls must not be penalised by another screen's biology."""
    from sera.core.shortlist import compute_shortlist

    rows = {r["gene"]: r for r in compute_shortlist(SCHMIDT2022, annotate=False)}
    for gene in SCHMIDT2022.controls:
        assert gene in rows, f"{gene} (a declared control) fell out of the shortlist"
        assert not rows[gene]["is_obvious_tcr"], f"{gene} penalised as obvious"
