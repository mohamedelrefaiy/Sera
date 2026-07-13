"""GOLDEN GENE-SKETCH CONTROL — the bench-notebook cartoon, made regression-proof.

The sketch is the decision brief's "napkin drawing": knockout → what the transcript did → what the
protein did → the cytokine, as a hand-drawn-style cartoon a scientist would sketch to explain a
result. It is a POSITIVE CONTROL, not decoration — an item is not done until this prints PASS.

It pins the sketch's honesty, not just its shape:

  - every arrow direction is CODE-DERIVED from the concordance row (rna_promotes / prot_promotes /
    verdict) — the sketch can never draw an arrow the data does not support;
  - a discordant gene's two layer-arrows point OPPOSITE ways (that is the whole story);
  - the gene, cytokine, and verdict shown are the row's own values, never invented;
  - an untested protein layer is drawn as "not measured", never as a fabricated arrow;
  - the rendered SVG is self-contained (no external refs) and carries the semantic tokens;
  - the spec serialises to JSON (the agent ships it).

TSC1 @ Stim48hr mirrors the real artifact row: verdict=discordant, rna_promotes=True (KD lowers the
transcript), prot_promotes=False (KD raises the protein) — the classic decoupling.
"""
from __future__ import annotations

import dataclasses
import json

from sera.core.gene_sketch import build_gene_sketch


def _tsc1_row() -> dict:
    return {
        "gene": "TSC1", "cytokine": "IL2", "condition": "Stim48hr", "verdict": "discordant",
        "hit_rna": True, "hit_prot": True, "z_rna": -2.525, "lfc_prot": 0.809,
        "rna_promotes": True, "prot_promotes": False, "prot_tested": True,
    }


def test_sketch_uses_the_rows_own_identity():
    s = build_gene_sketch(_tsc1_row())
    assert s.gene == "TSC1"
    assert s.cytokine == "IL2"
    assert s.verdict == "discordant"


def test_discordant_layers_point_opposite_ways():
    """The signature of a discordant result: transcript and protein arrows diverge. KD lowers the
    transcript (down) and raises the protein (up)."""
    s = build_gene_sketch(_tsc1_row())
    assert s.rna_direction == "down"      # rna_promotes=True => knockdown lowers transcript
    assert s.protein_direction == "up"    # prot_promotes=False => knockdown raises protein
    assert s.rna_direction != s.protein_direction


def test_sketch_headline_is_plain_and_leaks_no_statistic():
    s = build_gene_sketch(_tsc1_row())
    assert s.headline.strip()
    low = s.headline.lower()
    for tok in ("z-score", "z score", "log", "fold", "p-value", "q-value", "fdr"):
        assert tok not in low, f"statistic leaked into sketch headline: {tok!r}"


def test_untested_protein_layer_is_drawn_as_not_measured():
    row = dict(_tsc1_row(), prot_tested=False, hit_prot=False, prot_promotes=None, lfc_prot=None,
               verdict="mrna_only")
    s = build_gene_sketch(row)
    assert s.protein_direction == "untested"
    assert "not measured" in s.protein_label.lower()


def test_replicated_layers_agree():
    row = dict(_tsc1_row(), verdict="replicated", rna_promotes=True, prot_promotes=True,
               lfc_prot=-0.9)
    s = build_gene_sketch(row)
    assert s.rna_direction == s.protein_direction   # same direction => they agree


def test_rendered_svg_is_self_contained_and_semantic():
    s = build_gene_sketch(_tsc1_row())
    svg = s.svg
    assert svg.startswith("<svg") and svg.rstrip().endswith("</svg>")
    # self-contained: no external RESOURCES a strict host would fetch/block. The SVG xmlns is the
    # namespace identifier (never fetched), so we check for real fetch points instead.
    for bad in ("<script", "xlink:href", "url(", 'src=', 'href="http', "@import", "<image"):
        assert bad not in svg, f"sketch SVG is not self-contained: {bad!r}"
    # semantic tokens a viewer must see
    assert "TSC1" in svg
    assert "IL-2" in svg or "IL2" in svg


def test_spec_serialises_to_json():
    s = build_gene_sketch(_tsc1_row())
    blob = json.loads(json.dumps(dataclasses.asdict(s)))
    assert blob["gene"] == "TSC1"
    assert blob["rna_direction"] == "down" and blob["protein_direction"] == "up"
    assert isinstance(blob["svg"], str) and blob["svg"]


def test_unknown_verdict_is_rejected_not_guessed():
    row = dict(_tsc1_row(), verdict="totally_made_up")
    try:
        build_gene_sketch(row)
    except ValueError as e:
        assert "verdict" in str(e).lower()
    else:
        raise AssertionError("expected build_gene_sketch to reject an unknown verdict")
