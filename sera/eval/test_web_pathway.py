"""POSITIVE CONTROL — the web-pathway skill, made honesty-proof and offline-deterministic.

For a gene we did NOT curate and that is NOT in the screens, the app may still place it in context by
RETRIEVING its real pathway from Reactome. This control pins the "agents on the rim" contract on that
path — the same bar the curated topology meets, applied to retrieved data:

  - the pathway and its members are what the CLIENT returned (never invented by the tool/model);
  - the figure is cited to the Reactome stable id (the accession travels into the spec);
  - it draws the honest STARBURST — real partners, ZERO directed edges — because the API cannot give
    trustworthy edges; the curated CST cascade stays hand-transcribed;
  - a gene the client cannot place degrades to an honest 'no pathway found', inventing nothing;
  - the client itself parses only REAL response fields and never fabricates a member or an id.

It is a POSITIVE CONTROL: an item is not done until this prints PASS. The network is STUBBED so the
control is deterministic and runs offline — the live path is exercised separately by the client's
__main__ self-test.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from sera.clients import reactome
from sera.clients.reactome import RemotePathway


# --- the client: parse REAL response shapes, never fabricate --------------------------------------

# A trimmed but faithful copy of the two Reactome ContentService payloads the client consumes.
_PATHWAYS_JSON = [
    {"stId": "R-HSA-1266695", "displayName": "Interleukin-7 signaling", "isInDisease": False},
    {"stId": "R-HSA-9006934", "displayName": "Signaling by Receptor Tyrosine Kinases",
     "isInDisease": False},
    {"stId": "R-HSA-000000", "displayName": "A disease pathway", "isInDisease": True},   # dropped
]
_MEMBERS_JSON = {
    "R-HSA-1266695": [
        {"geneName": ["STAT3"]}, {"geneName": ["JAK1"]}, {"geneName": ["IL7R"]},
        {"geneName": ["IL2RG"]}, {"geneName": ["STAT5A"]},
        {"displayName": "ATP [cytosol]"},                 # no geneName → skipped (small molecule)
    ],
    "R-HSA-9006934": [{"geneName": ["STAT3"]}, {"geneName": ["GRB2"]}],
}


@pytest.fixture
def stub_reactome(monkeypatch, tmp_path):
    """Stub the client's two HTTP calls and point its cache at a temp file, so the control is offline
    and does not touch the real reactome_cache.json."""
    monkeypatch.setattr(reactome, "CACHE", str(tmp_path / "reactome_cache.json"))

    def fake_get_json(url: str):
        if "/mapping/UniProt/" in url:
            return _PATHWAYS_JSON
        for stid, members in _MEMBERS_JSON.items():
            if f"/participants/{stid}/" in url:
                return members
        return []   # unknown -> the real API's empty shape

    monkeypatch.setattr(reactome, "_get_json", fake_get_json)


def test_client_returns_the_richest_real_pathway_with_real_members(stub_reactome):
    pw = reactome.fetch("STAT3")
    assert pw is not None
    # richest neighbourhood wins: IL-7 signaling (5 members) over RTK (2)
    assert pw.stid == "R-HSA-1266695"
    assert pw.term == "Interleukin-7 signaling"
    # members are exactly the REAL gene symbols in the payload — no small molecules, no invented genes
    assert set(pw.genes) == {"STAT3", "JAK1", "IL7R", "IL2RG", "STAT5A"}
    assert "ATP" not in pw.genes and "ATP [CYTOSOL]" not in pw.genes
    assert "STAT3" in pw.genes                       # the focal gene is in its own pathway


def test_client_degrades_to_none_for_a_gene_with_no_pathway(stub_reactome, monkeypatch):
    # a gene the mapping call returns nothing for → honest None, never a fabricated pathway
    monkeypatch.setattr(reactome, "_get_json", lambda url: [])
    assert reactome.fetch("NOTAREALGENE") is None


def test_client_never_keeps_a_pathway_that_lacks_the_focal_gene(stub_reactome, monkeypatch):
    # if a pathway's members do not include the focal gene, it must be dropped, not drawn.
    monkeypatch.setattr(reactome, "_get_json", lambda url: (
        [{"stId": "R-HSA-1266695", "displayName": "Interleukin-7 signaling", "isInDisease": False}]
        if "/mapping/UniProt/" in url else [{"geneName": ["JAK1"]}, {"geneName": ["IL7R"]}]))
    assert reactome.fetch("STAT3") is None            # STAT3 absent from members → no figure


# --- the tool: retrieved-only, cited, honest-empty ------------------------------------------------

def _run(coro):
    return asyncio.run(coro)


def _call_web_pathway(gene: str):
    from sera.llm.sera_tools import web_pathway_map
    res = _run(web_pathway_map.handler({"gene": gene}))
    return json.loads(res["content"][0]["text"])


def test_tool_draws_the_starburst_from_retrieved_members_cited_to_reactome(monkeypatch):
    # the tool must render exactly what the client returned, cited to the stable id, no edges.
    fake = RemotePathway(term="Interleukin-7 signaling", stid="R-HSA-1266695",
                         genes=("STAT3", "JAK1", "IL7R", "IL2RG", "STAT5A"))
    monkeypatch.setattr("sera.clients.reactome.fetch", lambda gene, progress=None: fake)

    out = _call_web_pathway("STAT3")
    vu = out["__view_update__"]
    assert vu["action"] == "pathway_map"
    pm = vu["pathway_map"]
    # cited to the Reactome accession
    assert "R-HSA-1266695" in pm["pathway"]
    assert out["reactome_id"] == "R-HSA-1266695" and out["source"] == "Reactome"
    # honest starburst: real partners, ZERO directed edges, no curated provenance
    assert pm["style"] == "starburst"
    assert pm["topology_edges"] == [] and pm["topology_nodes"] == []
    assert pm["provenance"] is None
    # every partner shown is a REAL retrieved member (minus the focal gene), never invented
    assert set(pm["partners"]) <= {"JAK1", "IL7R", "IL2RG", "STAT5A"}
    assert "STAT3" not in pm["partners"]
    # the SVG is self-contained (no external fonts/scripts/URLs)
    for bad in ("<script", "xlink:href", "url(", 'href="http', "@import", "<image"):
        assert bad not in pm["svg"]
    assert "STAT3" in pm["svg"]


def test_tool_says_so_plainly_when_nothing_is_found(monkeypatch):
    monkeypatch.setattr("sera.clients.reactome.fetch", lambda gene, progress=None: None)
    out = _call_web_pathway("NOTAREALGENE")
    assert "__view_update__" not in out               # no figure invented
    assert out["error"] == "no pathway found"
    assert "invent" in out["note"].lower()            # the model is told to fabricate nothing


def test_tool_never_claims_a_screen_verdict_for_an_offscreen_gene(monkeypatch):
    # an off-screen gene has NO verdict here; the focal node must read 'unknown', not a real verdict.
    fake = RemotePathway(term="Interleukin-7 signaling", stid="R-HSA-1266695",
                         genes=("STAT3", "JAK1", "IL7R"))
    monkeypatch.setattr("sera.clients.reactome.fetch", lambda gene, progress=None: fake)
    pm = _call_web_pathway("STAT3")["__view_update__"]["pathway_map"]
    assert pm["focal_verdict"] == ""                  # never fabricates 'replicated'/'discordant'/…


def test_caption_does_not_claim_hits_or_layer_agreement_for_a_retrieved_gene(monkeypatch):
    # the figure caption for a NOT-measured gene must not say it shares a pathway "with the other
    # hits" or that "its two layers agree" — it has no hits and no measured layers here.
    fake = RemotePathway(term="Interleukin-7 signaling", stid="R-HSA-1266695",
                         genes=("STAT3", "JAK1", "IL7R", "IL2RG"))
    monkeypatch.setattr("sera.clients.reactome.fetch", lambda gene, progress=None: fake)
    pm = _call_web_pathway("STAT3")["__view_update__"]["pathway_map"]
    cap = pm["caption"].lower()
    assert "with the other hits" not in cap
    assert "two layers agree" not in cap and "layers disagree" not in cap
    # it must say plainly this is retrieved context, not a measured result
    assert "retrieved" in cap and "not measured in these screens" in cap
    # and nowhere in the SVG should the misleading "shares … with the other hits" label appear
    assert "with the other hits" not in pm["svg"].lower()
