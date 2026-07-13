"""GOLDEN HIT-LIST ENRICHMENT CONTROL — the shared-biology answer, made regression-proof.

This is the positive control for the pathway-enrichment tool: an item is not "done" until this
prints PASS. It pins the tool's honesty, not just its shape:

  - the hit list is CODE-DERIVED from the concordance rows (the replicated subset), never authored
    by the agent — the agent may only narrate the terms code returns;
  - enrichment terms come from the code-owned enrichr client (real Reactome terms + q-values), and
    the builder never invents, renames, or reorders a pathway;
  - the plain summary a bench reader consumes leaks no raw statistic;
  - an empty result degrades to an honest "no enriched pathways", never a fabricated one;
  - the whole result serialises to JSON (the API/agent will send it).

The demo TCR-signaling set (ITK/BCL10/VAV1/ZAP70/LAT/CD3D/LCP2/PLCG1) is pre-warmed into
enrichr_cache.json, so this control runs OFFLINE against real cached Reactome terms — that is what
makes it a control rather than a mock.
"""
from __future__ import annotations

import dataclasses
import json
import re

from sera.core.hitlist_enrichment import PathwayTerm, build_hitlist_enrichment


# --- fixtures mirroring the real artifact + the code-owned enrichr client -------------------

def _rows() -> tuple[dict, ...]:
    """Concordance rows: a replicated TCR-signaling core (the demo hit list) plus non-replicated
    rows the builder must EXCLUDE from the hit list."""
    rep = ("ITK", "BCL10", "VAV1", "ZAP70", "LAT", "CD3D", "LCP2", "PLCG1")
    rows = [{"gene": g, "cytokine": "IL2", "condition": "Stim48hr", "verdict": "replicated"}
            for g in rep]
    # noise that must NOT enter the hit list:
    rows += [
        {"gene": "TSC1", "cytokine": "IL2", "condition": "Stim48hr", "verdict": "discordant"},
        {"gene": "FOXP3", "cytokine": "IL2", "condition": "Stim48hr", "verdict": "neither"},
        {"gene": "IL2RA", "cytokine": "IL2", "condition": "Stim48hr", "verdict": "protein_only"},
    ]
    return tuple(rows)


def _real_enrich(genes, library="Reactome_2022"):
    """The actual code-owned enrichr client, resolved from the pre-warmed cache (offline)."""
    from sera.clients import enrichr
    return enrichr.enrich(genes, library=library)


_STAT_TOKENS = re.compile(r"\b(z[- ]?score|log[- ]?fold|lfc|p[- ]?value|\bp\b|fdr|q[- ]?value)\b",
                          re.IGNORECASE)


# --- the control -----------------------------------------------------------------------------

def test_hitlist_is_the_code_derived_replicated_set():
    result = build_hitlist_enrichment(_rows(), _real_enrich)
    assert set(result.genes) == {"ITK", "BCL10", "VAV1", "ZAP70", "LAT", "CD3D", "LCP2", "PLCG1"}
    # the non-replicated genes are excluded — the agent cannot smuggle them in
    assert "TSC1" not in result.genes and "FOXP3" not in result.genes and "IL2RA" not in result.genes
    assert result.n_genes == 8


def test_genes_are_sorted_and_deduped():
    dupes = _rows() + _rows()          # same rows twice
    result = build_hitlist_enrichment(dupes, _real_enrich)
    assert list(result.genes) == sorted(set(result.genes))
    assert result.n_genes == 8


def test_pathways_are_real_reactome_terms_from_the_client():
    result = build_hitlist_enrichment(_rows(), _real_enrich)
    assert result.pathways, "the pre-warmed TCR set must yield cached pathways"
    terms = " ".join(p.term for p in result.pathways)
    # the demo set's signature biology — TCR signaling — must be present, code-owned not invented
    assert "TCR Signaling" in terms
    for p in result.pathways:
        assert isinstance(p, PathwayTerm)
        assert p.term and p.n_genes >= 1
        assert 0.0 <= p.adj_p <= 1.0


def test_top_pathway_summary_names_a_real_term_not_an_invented_one():
    result = build_hitlist_enrichment(_rows(), _real_enrich)
    # the headline the agent narrates must be one of the code-returned terms, verbatim
    assert result.top_term in {p.term for p in result.pathways}


def test_summary_prose_leaks_no_raw_statistics():
    result = build_hitlist_enrichment(_rows(), _real_enrich)
    hit = _STAT_TOKENS.search(result.plain_summary)
    assert hit is None, f"raw statistic leaked into hit-list prose: {hit and hit.group(0)!r}"


def test_empty_enrichment_degrades_honestly():
    """No pathways back (network down / nothing significant) → honest empty, never a fabricated hit."""
    result = build_hitlist_enrichment(_rows(), lambda genes, library="Reactome_2022": [])
    assert result.pathways == ()
    assert result.top_term is None
    assert "no" in result.plain_summary.lower()   # e.g. "no enriched pathways"


def test_no_replicated_rows_is_honest():
    only_noise = ({"gene": "TSC1", "cytokine": "IL2", "condition": "Stim48hr",
                   "verdict": "discordant"},)
    result = build_hitlist_enrichment(only_noise, _real_enrich)
    assert result.genes == ()
    assert result.pathways == ()
    assert result.n_genes == 0


def test_result_serialises_to_json():
    result = build_hitlist_enrichment(_rows(), _real_enrich)
    blob = json.loads(json.dumps(dataclasses.asdict(result)))
    assert blob["n_genes"] == 8
    assert isinstance(blob["pathways"], list)
