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


# --- the cache layer: resolve offline, tolerate corruption, never crash a write ------------------

def test_cache_hit_returns_without_touching_the_network(monkeypatch, tmp_path):
    """A gene already in the cache must resolve from disk — the client must NOT call _get_json again
    (the offline/instant promise). Prove it by making the network raise if touched."""
    cache_file = tmp_path / "reactome_cache.json"
    cache_file.write_text(json.dumps({
        "STAT3": {"term": "Interleukin-7 signaling", "stid": "R-HSA-1266695",
                  "genes": ["STAT3", "JAK1", "IL7R"]}}))
    monkeypatch.setattr(reactome, "CACHE", str(cache_file))

    def explode(url):
        raise AssertionError("network was hit on a cache HIT — offline promise broken")
    monkeypatch.setattr(reactome, "_get_json", explode)

    pw = reactome.fetch("stat3")            # lower-case in → upper-cased key lookup
    assert pw is not None
    assert pw.stid == "R-HSA-1266695"
    assert pw.genes == ("STAT3", "JAK1", "IL7R")


def test_cache_hit_of_a_stored_none_returns_none_without_network(monkeypatch, tmp_path):
    """A cached negative result (gene mapped to no pathway) must return None from disk, not re-query.
    Otherwise every un-placeable gene re-hits the API on every view."""
    cache_file = tmp_path / "reactome_cache.json"
    cache_file.write_text(json.dumps({"NOTAREALGENE": None}))
    monkeypatch.setattr(reactome, "CACHE", str(cache_file))

    def explode(url):
        raise AssertionError("network hit on a cached-None")
    monkeypatch.setattr(reactome, "_get_json", explode)
    assert reactome.fetch("NOTAREALGENE") is None


def test_corrupt_cache_file_degrades_to_empty_not_a_crash(monkeypatch, tmp_path):
    """A truncated / non-JSON cache file must not crash the client — _load_cache swallows the parse
    error and returns {}, so the lookup proceeds as a cold cache instead of 500-ing the view."""
    cache_file = tmp_path / "reactome_cache.json"
    cache_file.write_text("{ this is not valid json ")
    monkeypatch.setattr(reactome, "CACHE", str(cache_file))
    assert reactome._load_cache() == {}          # corrupt → empty, no exception

    # and a full fetch over a corrupt cache still works (falls through to the network stub)
    monkeypatch.setattr(reactome, "_get_json", lambda url: (
        [{"stId": "R-HSA-1266695", "displayName": "Interleukin-7 signaling", "isInDisease": False}]
        if "/mapping/UniProt/" in url
        else [{"geneName": ["STAT3"]}, {"geneName": ["JAK1"]}, {"geneName": ["IL7R"]}]))
    pw = reactome.fetch("STAT3")
    assert pw is not None and pw.stid == "R-HSA-1266695"


def test_cache_write_failure_never_breaks_the_lookup(monkeypatch):
    """If the cache is unwritable (read-only dir, disk full), _save_cache must swallow the OSError so
    a lookup still returns its result. A cache write failure must never surface to the caller."""
    import builtins
    real_open = builtins.open

    def write_fails(file, mode="r", *a, **k):
        if "w" in mode:
            raise OSError("disk full")
        return real_open(file, mode, *a, **k)
    monkeypatch.setattr("builtins.open", write_fails)

    # _save_cache catches the OSError internally and returns None — no exception escapes.
    assert reactome._save_cache({"X": None}) is None


def test_fetch_degrades_to_none_when_the_pathways_call_raises(monkeypatch, tmp_path):
    """A network failure on the pathways lookup must degrade to None (honest 'could not place it'),
    never raise up into the view. Exercises fetch()'s `except Exception` around _pathways_for_gene."""
    monkeypatch.setattr(reactome, "CACHE", str(tmp_path / "reactome_cache.json"))

    def dead_network(url):
        raise ConnectionError("reactome unreachable")
    monkeypatch.setattr(reactome, "_get_json", dead_network)

    seen = []
    assert reactome.fetch("STAT3", progress=seen.append) is None
    # the degrade path reports the error through the progress callback, not by raising
    assert any("reactome error (pathways)" in m for m in seen)


def test_fetch_skips_a_pathway_whose_members_call_raises(monkeypatch, tmp_path):
    """If ONE pathway's member lookup fails mid-scan, that pathway is skipped (logged via progress)
    and the other candidates still resolve — a single flaky members call must not sink the whole
    lookup. Exercises fetch()'s `except Exception` around _members_of_pathway."""
    monkeypatch.setattr(reactome, "CACHE", str(tmp_path / "reactome_cache.json"))

    def flaky(url):
        if "/mapping/UniProt/" in url:
            return [
                {"stId": "R-HSA-BAD", "displayName": "Flaky pathway", "isInDisease": False},
                {"stId": "R-HSA-1266695", "displayName": "Interleukin-7 signaling",
                 "isInDisease": False},
            ]
        if "/participants/R-HSA-BAD/" in url:
            raise TimeoutError("members call timed out")
        if "/participants/R-HSA-1266695/" in url:
            return [{"geneName": ["STAT3"]}, {"geneName": ["JAK1"]}, {"geneName": ["IL7R"]}]
        return []
    monkeypatch.setattr(reactome, "_get_json", flaky)

    seen = []
    pw = reactome.fetch("STAT3", progress=seen.append)
    assert pw is not None and pw.stid == "R-HSA-1266695"     # the healthy pathway still wins
    assert any("reactome error (members R-HSA-BAD)" in m for m in seen)


# --- the tool: guard the empty gene, the un-buildable map, and the registration ------------------

def test_tool_guards_an_empty_or_blank_gene(monkeypatch):
    """web_pathway_map with a blank gene must ask which gene, not call Reactome. Prove no fetch fires."""
    def explode(*a, **k):
        raise AssertionError("fetch called for a blank gene")
    monkeypatch.setattr("sera.clients.reactome.fetch", explode)
    for blank in ("", "   ", "\t"):
        out = _call_web_pathway(blank)
        assert out["error"] == "no gene given"
        assert "gene" in out["note"].lower()          # tells the model to ask which gene
        assert "__view_update__" not in out


def test_tool_degrades_when_the_map_cannot_be_built(monkeypatch):
    """If build_pathway_map raises for a retrieved row (e.g. an unexpected shape), the tool must return
    an honest 'could not build' note and invent nothing — never 500 the view. Exercises the
    _web_pathway_view except (ValueError, KeyError) branch."""
    fake = RemotePathway(term="Interleukin-7 signaling", stid="R-HSA-1266695",
                         genes=("STAT3", "JAK1", "IL7R"))
    monkeypatch.setattr("sera.clients.reactome.fetch", lambda gene, progress=None: fake)

    def boom(row, pathways, **kw):
        raise ValueError("unexpected pathway shape")
    monkeypatch.setattr("sera.core.pathway_map.build_pathway_map", boom)

    out = _call_web_pathway("STAT3")
    assert "__view_update__" not in out               # no figure drawn from a failed build
    assert out["error"] == "could not build a web pathway map"
    assert "invent nothing" in out["note"].lower()


def test_web_pathway_map_is_registered_in_allowed_tools():
    """A future refactor must not silently drop web_pathway_map from the agent's allowed-tools list —
    same guard every other sera tool carries. Without this, the tool would vanish with nothing failing."""
    from sera.llm import sera_tools
    assert "mcp__sera__web_pathway_map" in sera_tools.SERA_ALLOWED_TOOLS


def test_retrieved_gene_draws_the_starburst_even_if_it_is_a_curated_topology_node(monkeypatch):
    """Honesty-contract regression: a gene RETRIEVED from Reactome was NOT measured in these screens,
    but it may coincidentally be a curated topology NODE. LTBR is exactly that — a real curated node
    that is NOT in the screens. Without the force-starburst guard, web_pathway_map would fall through
    to the curated CST cascade and stamp the false caption 'confident hits in these screens' (plus
    directed edges) on a gene the screens never saw. The retrieved path must FORCE the honest
    starburst: no directed edges, no curated provenance, caption says the gene was NOT measured here."""
    from sera.core.pathway_topology import select_for

    focal = "LTBR"
    assert select_for(focal) is not None, "LTBR must be a curated node for this test to be meaningful"

    fake = RemotePathway(term="TNFR2 non-canonical NF-kB pathway", stid="R-HSA-5668541",
                         genes=(focal, "TRAF2", "TRAF3", "NFKB2", "RELB"))
    monkeypatch.setattr("sera.clients.reactome.fetch", lambda gene, progress=None: fake)

    pm = _call_web_pathway(focal)["__view_update__"]["pathway_map"]
    # the honest starburst, NOT the curated cascade
    assert pm["style"] == "starburst", "retrieved gene fell through to the curated topology"
    assert pm["topology_edges"] == [] and pm["topology_nodes"] == []
    assert pm["provenance"] is None
    # and never the false 'confident hits in these screens' claim for an unmeasured gene
    assert "confident hits in these screens" not in pm["caption"].lower()
    assert "confident hits in these screens" not in pm["svg"].lower()
    assert "not measured in these screens" in pm["caption"].lower()
