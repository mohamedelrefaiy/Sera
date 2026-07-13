"""GOLDEN PATHWAY-MAP CONTROL — the biological-context figure, made regression-proof.

The pathway map places a gene in its signalling neighbourhood: the gene as a node among its REAL
pathway partners (from code-owned enrichment), coloured by verdict, with candidate mechanisms drawn
as clearly-marked hypotheses. It is the "where does this gene sit, and why might the layers
disagree" figure — a genuine biological read, not a box-and-arrow flow-chart.

It is a POSITIVE CONTROL. An item is not done until this prints PASS. It pins the honesty:

  - partner genes are the ACTUAL pathway members Enrichr reported (never invented neighbours);
  - the focal gene is highlighted and its verdict drives its colour;
  - the pathway shown is the code-selected top pathway the gene belongs to;
  - candidate mechanisms are drawn as HYPOTHESES (marked, dashed) from the decision brief's
    closed set — never as established fact, and only for a disagreement verdict;
  - the SVG is self-contained (no external fonts/scripts/URLs) and carries the semantic tokens;
  - a gene in NO enriched pathway degrades honestly (no fabricated neighbourhood);
  - the spec serialises to JSON.
"""
from __future__ import annotations

import dataclasses
import json

from target_triage.clients.enrichr import Pathway
from target_triage.core.pathway_map import build_pathway_map


def _tcr_pathways() -> tuple[Pathway, ...]:
    """The demo TCR-signalling pathways with their REAL overlap members (as the client now keeps)."""
    return (
        Pathway(term="TCR Signaling R-HSA-202403", adj_p=1e-9, n_genes=5, library="Reactome_2022",
                genes=("ZAP70", "ITK", "LCP2", "BCL10", "PLCG1")),
        Pathway(term="Generation Of Second Messenger Molecules R-HSA-202433", adj_p=5e-11,
                n_genes=5, library="Reactome_2022", genes=("ZAP70", "ITK", "LCP2", "PLCG1", "CD3D")),
    )


def _row(gene: str, verdict: str = "replicated") -> dict:
    return {"gene": gene, "cytokine": "IL2", "condition": "Stim48hr", "verdict": verdict,
            "hit_rna": True, "hit_prot": True, "rna_promotes": True, "prot_promotes": True,
            "prot_tested": True}


def test_places_gene_among_its_real_partners():
    m = build_pathway_map(_row("ITK"), _tcr_pathways())
    assert m.focal_gene == "ITK"
    assert m.pathway == "TCR Signaling R-HSA-202403"   # the top pathway ITK belongs to
    # partners are the OTHER real members of that pathway, never invented
    assert set(m.partners) <= {"ZAP70", "LCP2", "BCL10", "PLCG1"}
    assert "ITK" not in m.partners                     # the focal gene isn't its own partner
    assert len(m.partners) >= 2


def test_partners_are_only_real_pathway_members():
    m = build_pathway_map(_row("ZAP70"), _tcr_pathways())
    real_members = set(_tcr_pathways()[0].genes)
    assert set(m.partners) | {m.focal_gene} <= real_members  # nothing outside the reported set


def test_verdict_drives_the_focal_node_colour():
    disc = build_pathway_map(_row("ITK", "discordant"), _tcr_pathways())
    rep = build_pathway_map(_row("ITK", "replicated"), _tcr_pathways())
    assert disc.focal_verdict == "discordant"
    assert rep.focal_verdict == "replicated"
    assert disc.focal_colour != rep.focal_colour       # colour tracks the verdict


def test_hypotheses_shown_only_for_a_disagreement_and_marked_as_hypotheses():
    disc = build_pathway_map(_row("ITK", "discordant"), _tcr_pathways())
    assert disc.hypotheses, "a discordant gene must surface candidate mechanisms"
    for h in disc.hypotheses:
        assert h.strip()
    # a replicated (agreeing) gene needs no 'why they disagree' hypotheses
    rep = build_pathway_map(_row("ITK", "replicated"), _tcr_pathways())
    assert rep.hypotheses == ()


def test_hypotheses_are_marked_as_hypotheses_in_the_svg():
    m = build_pathway_map(_row("ITK", "discordant"), _tcr_pathways())
    low = m.svg.lower()
    assert "hypothesis" in low or "hypotheses" in low   # never presented as fact


def test_gene_in_no_pathway_degrades_honestly():
    m = build_pathway_map(_row("NOTINPATHWAY"), _tcr_pathways())
    assert m.pathway is None
    assert m.partners == ()
    # honest emptiness: says plainly there's no enriched pathway / neighbourhood, invents nothing
    low = m.caption.lower()
    assert "not in an enriched pathway" in low and "no shared neighbourhood" in low


def test_svg_is_self_contained_and_semantic():
    m = build_pathway_map(_row("ITK", "discordant"), _tcr_pathways())
    svg = m.svg
    assert svg.startswith("<svg") and svg.rstrip().endswith("</svg>")
    for bad in ("<script", "xlink:href", "url(", "src=", 'href="http', "@import", "<image"):
        assert bad not in svg, f"pathway-map SVG not self-contained: {bad!r}"
    assert "ITK" in svg
    assert "TCR Signaling" in svg


def test_spec_serialises_to_json():
    m = build_pathway_map(_row("ITK", "discordant"), _tcr_pathways())
    blob = json.loads(json.dumps(dataclasses.asdict(m)))
    assert blob["focal_gene"] == "ITK"
    assert blob["pathway"] == "TCR Signaling R-HSA-202403"
    assert isinstance(blob["partners"], list) and blob["partners"]
