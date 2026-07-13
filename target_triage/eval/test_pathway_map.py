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

import re

from target_triage.clients.enrichr import Pathway
from target_triage.core.pathway_map import build_pathway_map
from target_triage.core.pathway_topology import (
    COMPARTMENTS,
    CURATED_PATHWAYS,
    EDGE_TYPES,
    FAMILY_VOCAB,
    _ACCESSION_PATTERNS,
    select_for,
)


def _tcr_pathways() -> tuple[Pathway, ...]:
    """The demo TCR-signalling pathways with their REAL overlap members (as the client now keeps)."""
    return (
        Pathway(term="TCR Signaling R-HSA-202403", adj_p=1e-9, n_genes=5, library="Reactome_2022",
                genes=("ZAP70", "ITK", "LCP2", "BCL10", "PLCG1")),
        Pathway(term="Generation Of Second Messenger Molecules R-HSA-202433", adj_p=5e-11,
                n_genes=5, library="Reactome_2022", genes=("ZAP70", "ITK", "LCP2", "PLCG1", "CD3D")),
    )


def _starburst_pathways() -> tuple[Pathway, ...]:
    """A pathway whose members are NOT in the curated topology set, so a focal gene here exercises the
    STARBURST fallback renderer (real Enrichr partners + marked hypotheses), not the CST figure."""
    return (
        Pathway(term="Interferon Signaling R-HSA-913531", adj_p=1e-8, n_genes=5,
                library="Reactome_2022", genes=("STAT1", "STAT2", "IRF9", "JAK1", "JAK2")),
    )


def _row(gene: str, verdict: str = "replicated") -> dict:
    return {"gene": gene, "cytokine": "IL2", "condition": "Stim48hr", "verdict": verdict,
            "hit_rna": True, "hit_prot": True, "rna_promotes": True, "prot_promotes": True,
            "prot_tested": True}


# --- the starburst fallback (a gene NOT in the curated topology) ---------------------------------

def test_places_gene_among_its_real_partners():
    m = build_pathway_map(_row("STAT1"), _starburst_pathways())
    assert m.style == "starburst"
    assert m.focal_gene == "STAT1"
    assert m.pathway == "Interferon Signaling R-HSA-913531"   # the top pathway STAT1 belongs to
    # partners are the OTHER real members of that pathway, never invented
    assert set(m.partners) <= {"STAT2", "IRF9", "JAK1", "JAK2"}
    assert "STAT1" not in m.partners                   # the focal gene isn't its own partner
    assert len(m.partners) >= 2


def test_partners_are_only_real_pathway_members():
    m = build_pathway_map(_row("STAT2"), _starburst_pathways())
    real_members = set(_starburst_pathways()[0].genes)
    assert set(m.partners) | {m.focal_gene} <= real_members  # nothing outside the reported set


def test_verdict_drives_the_focal_node_colour():
    # holds for BOTH renderers; the starburst path is exercised here with a non-curated gene.
    disc = build_pathway_map(_row("STAT1", "discordant"), _starburst_pathways())
    rep = build_pathway_map(_row("STAT1", "replicated"), _starburst_pathways())
    assert disc.focal_verdict == "discordant"
    assert rep.focal_verdict == "replicated"
    assert disc.focal_colour != rep.focal_colour       # colour tracks the verdict


def test_hypotheses_shown_only_for_a_disagreement_and_marked_as_hypotheses():
    disc = build_pathway_map(_row("STAT1", "discordant"), _starburst_pathways())
    assert disc.hypotheses, "a discordant gene must surface candidate mechanisms"
    for h in disc.hypotheses:
        assert h.strip()
    # a replicated (agreeing) gene needs no 'why they disagree' hypotheses
    rep = build_pathway_map(_row("STAT1", "replicated"), _starburst_pathways())
    assert rep.hypotheses == ()


def test_hypotheses_are_marked_as_hypotheses_in_the_svg():
    m = build_pathway_map(_row("STAT1", "discordant"), _starburst_pathways())
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
    # ITK is in the CURATED pathway, so the map is the topology figure; its `pathway` is the Reactome
    # accession, and the partners are the confident hits it drew.
    assert blob["pathway"] == "R-HSA-202403"
    assert blob["style"] == "topology"


# =====================================================================================
# CURATED-TOPOLOGY CONTROL — the CST-style figure, made honesty-proof.
#
# The topology figure draws real compartments and directed, typed, CITED edges. It is a POSITIVE
# CONTROL: nothing is on screen that the curated record does not assert, the curated record itself is
# sourced (per-edge accessions), the LLM's only freedom is pathway selection, and a gene in no
# curated pathway still degrades to the honest starburst. Not done until this prints PASS.
# =====================================================================================

# Stim48hr hit-status for the curated TCR nodes, read from the concordance ground truth — a hit is
# drawn solid, a context node faded. Used to exercise the shading path.
_TCR_STATUS = {
    "CD3D": "hit", "LCK": "context", "ZAP70": "hit", "LAT": "hit", "LCP2": "hit",
    "VAV1": "hit", "ITK": "hit", "PLCG1": "hit", "PRKCQ": "context", "BCL10": "hit",
    "NFATC1": "context", "RELA": "context",
}
_ACCESSION_RE = re.compile("|".join(_ACCESSION_PATTERNS))


def _topo(gene: str = "ITK", verdict: str = "discordant"):
    return build_pathway_map(_row(gene, verdict), _tcr_pathways(), node_status=_TCR_STATUS)


def test_curated_gene_renders_the_topology_figure_not_the_starburst():
    m = _topo("ITK")
    assert m.style == "topology"
    assert m.topology_nodes and m.topology_edges     # it actually drew wiring
    assert m.provenance and m.provenance["reactome_id"] == "R-HSA-202403"


def test_every_rendered_node_exists_in_the_curated_record():
    # A: nothing on screen that isn't a curated node.
    m = _topo("ITK")
    curated = select_for("ITK")
    assert curated is not None
    assert set(m.topology_nodes) <= curated.node_ids()


def test_every_rendered_edge_exists_in_the_curated_record_with_direction_and_type():
    # B: the renderer cannot draw an arrow the curated data didn't assert, in a direction it didn't.
    m = _topo("ITK")
    curated = select_for("ITK")
    curated_edges = {(e.src, e.dst, e.type) for e in curated.edges}
    for edge in m.topology_edges:
        assert edge in curated_edges, f"rendered edge not in curated record: {edge}"


def test_every_curated_edge_carries_real_provenance():
    # C: the data itself is transcribed, not invented — machine-checkable per edge.
    for pw in CURATED_PATHWAYS:
        for e in pw.edges:
            assert e.source_db in {"Reactome", "SIGNOR", "KEGG"}, e
            assert e.source_id, f"edge {e.src}->{e.dst} has no source_id"
            assert _ACCESSION_RE.fullmatch(e.source_id), \
                f"edge {e.src}->{e.dst} source_id {e.source_id!r} is not a real accession shape"


def test_every_curated_node_uses_the_closed_vocabularies():
    # D: no stray compartment or family that would imply the renderer invented a category.
    for pw in CURATED_PATHWAYS:
        for n in pw.nodes:
            assert n.compartment in COMPARTMENTS, n
            assert n.family in FAMILY_VOCAB, n
        for e in pw.edges:
            assert e.type in EDGE_TYPES, e


def test_focal_gene_is_actually_in_the_chosen_pathway():
    # E: the figure is always ABOUT the focal gene — never rendered for a gene it doesn't contain.
    for gene in ("ITK", "ZAP70", "PLCG1", "BCL10"):
        m = _topo(gene)
        curated = select_for(gene)
        assert curated is not None
        assert gene in curated.node_ids()
        assert gene in m.topology_nodes


def test_selection_is_closed_set_only():
    # F: the LLM's only freedom is selection, and selection is a closed-set membership lookup.
    assert select_for("ITK") is not None
    assert select_for("NOTAREALGENE") is None       # outside the set → no fabrication
    assert select_for("") is None
    # every selectable pathway is one of the curated closed set
    ids = {p.id for p in CURATED_PATHWAYS}
    assert select_for("ITK").id in ids


def test_gene_in_no_curated_pathway_degrades_to_honest_starburst():
    # G: the honest floor — a non-curated gene draws ZERO directed edges / no CST scaffold.
    m = build_pathway_map(_row("NOTINPATHWAY"), _tcr_pathways())
    assert m.style == "starburst"
    assert m.topology_edges == ()
    assert m.topology_nodes == ()
    assert m.provenance is None


def test_topology_svg_is_self_contained():
    # H: no external fonts/scripts/URLs, and no <marker url(...)> refs — arrowheads are inline paths.
    m = _topo("ITK")
    svg = m.svg
    assert svg.startswith("<svg") and svg.rstrip().endswith("</svg>")
    for bad in ("<script", "xlink:href", "url(", "src=", 'href="http', "@import", "<image",
                "<marker"):
        assert bad not in svg, f"topology SVG not self-contained: {bad!r}"
    assert "ITK" in svg and "TCR Signaling" in svg
    assert "CURATED · REACTOME R-HSA-202403" in svg
    assert "measured hit" in svg and "curated context" in svg


def test_topology_spec_serialises_with_provenance_visible():
    # I: a judge inspecting the JSON payload sees the citations, not just the picture.
    m = _topo("ITK")
    blob = json.loads(json.dumps(dataclasses.asdict(m)))
    assert blob["style"] == "topology"
    assert isinstance(blob["topology_nodes"], list) and blob["topology_nodes"]
    assert isinstance(blob["topology_edges"], list) and blob["topology_edges"]
    assert blob["provenance"]["source_db"] == "Reactome"
    assert blob["provenance"]["reactome_id"].startswith("R-HSA-")


def test_verdict_still_drives_the_focal_ring_in_the_topology_figure():
    # the concordance verdict must still colour the focal node — the reconciliation stays visible.
    disc = _topo("ITK", "discordant")
    rep = _topo("ITK", "replicated")
    assert disc.focal_colour != rep.focal_colour
    assert disc.focal_colour in disc.svg and rep.focal_colour in rep.svg


def test_hit_status_uses_open_vs_filled_nodes_and_is_accessible():
    m = build_pathway_map(
        _row("ITK", "discordant"), _tcr_pathways(),
        node_status={"ITK": "hit", "ZAP70": "context"},
    )
    assert 'data-node="ITK" data-status="hit"' in m.svg
    assert 'ITK; measured hit; focal target' in m.svg
    assert 'data-node="ZAP70" data-status="context"' in m.svg
    assert 'ZAP70; curated pathway context' in m.svg
    # Context nodes stay fully legible: status is encoded by an open fill, not opacity.
    zap70_group = m.svg.split('data-node="ZAP70"', 1)[1].split('</g>', 1)[0]
    assert 'fill="#FFFFFF"' in zap70_group
    assert 'opacity=' not in zap70_group


def test_render_is_deterministic():
    # the golden test relies on byte-stable SVGs — same input, identical output.
    a = _topo("ITK")
    b = _topo("ITK")
    assert a.svg == b.svg
    assert a.topology_edges == b.topology_edges


# --- every curated pathway is part of the positive-control gate ----------------------------------
# One representative focal gene per curated pathway. Each must render a real topology figure whose
# nodes/edges all trace to its curated record — the same honesty pins, applied pathway-by-pathway.
_PATHWAY_PROBES = (
    ("ITK", "tcr_il2", "R-HSA-202403"),
    ("SYK", "bcr", "R-HSA-983705"),
    ("RAF1", "mapk", "R-HSA-5673001"),
)


def test_each_curated_pathway_renders_a_traceable_topology_figure():
    for gene, pid, reactome in _PATHWAY_PROBES:
        m = build_pathway_map(_row(gene, "discordant"), _tcr_pathways())
        curated = select_for(gene)
        assert curated is not None and curated.id == pid, f"{gene} should select {pid}"
        assert m.style == "topology", f"{gene} should render the topology figure"
        assert m.pathway == reactome
        # nothing on screen that the chosen pathway's record doesn't assert
        assert set(m.topology_nodes) <= curated.node_ids()
        curated_edges = {(e.src, e.dst, e.type) for e in curated.edges}
        for edge in m.topology_edges:
            assert edge in curated_edges, f"{gene}: rendered edge {edge} not in curated record"
        # the focal gene is actually in the figure it anchors
        assert gene in m.topology_nodes
        # self-contained
        for bad in ("<script", "xlink:href", "url(", 'href="http', "<marker"):
            assert bad not in m.svg


def test_edge_type_vocabulary_is_exercised_across_pathways():
    # the CST idiom needs more than activation arrows — assert the curated set uses inhibition,
    # production, translocation and transcription somewhere, so the renderer's grammar is covered.
    seen = {e.type for pw in CURATED_PATHWAYS for e in pw.edges}
    for needed in ("activation", "inhibition", "production", "translocation", "transcription"):
        assert needed in seen, f"no curated edge exercises {needed!r}"
