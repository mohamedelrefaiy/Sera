"""Reactome client: a gene's REAL pathways and their REAL member genes, live + disk-cached.

The purpose is the "agents on the rim" honesty contract applied to genes we did NOT curate by hand.
For a gene outside the curated topology and outside the screens, the app still must not invent a
neighbourhood. This client RETRIEVES structured, cited facts from Reactome so the model never
authors them:

  gene symbol
    -> GET /data/mapping/UniProt/<gene>/pathways   (the human pathways that contain the gene)
    -> GET /data/participants/<stId>/referenceEntities  (that pathway's REAL member genes)

It returns immutable `RemotePathway` records carrying the Reactome stable id (the citation) and the
real member symbols. It is deliberately the SAME SHAPE that `enrichr.Pathway` exposes to the pathway
map (`.term`, `.genes`), so the honest STARBURST renderer — a gene ringed by its real partners, with
NO invented directed edges — can consume it unchanged.

What this client does NOT do, on purpose: it does not synthesise directed, typed edges. Reactome
models a pathway as reactions over complexes and sets; reducing that to trustworthy src->dst arrows
is not reliably automatable, and a wrong arrow is exactly what the contract forbids. So the curated
CST cascade stays hand-transcribed (`core/pathway_topology.py`); the web path draws the honest
starburst only. Members are facts we can stand behind; edges are not.

Live REST API (free, no key). Results cache to reactome_cache.json keyed on the UPPER-cased gene, so
a repeated lookup resolves offline and instantly — mirroring the enrichr / opentargets caches.
Degrades to `None` (no pathway found / network error) rather than raising: the caller then says
plainly it could not place the gene, and invents nothing.
"""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from dataclasses import dataclass

BASE = "https://reactome.org/ContentService"
CACHE = os.path.join(os.path.dirname(__file__), "reactome_cache.json")
SPECIES = "9606"          # Homo sapiens; we only place human genes
_TIMEOUT = 20
_MAX_MEMBERS = 7          # partners (incl. the focal gene) kept for the starburst ring
# A useful "neighbourhood" pathway is SPECIFIC, not a catch-all. Reactome pathways containing a gene
# span from ~3 members (too sparse to ring) to ~380 (generic buckets like "Affinity selection of
# immunoglobulins" that contain the gene but are not its signalling identity). Prefer a pathway whose
# size falls in this band; only if none do, fall back to the one closest to it.
_MIN_PATHWAY_SIZE = 6
_MAX_PATHWAY_SIZE = 60
# Reactome's ContentService rejects the default urllib User-Agent with 403; a named UA is accepted.
# Sending it means a real transport failure surfaces as an error we can log, not a silent "no
# pathway" that would masquerade as an honest empty result.
_UA = "sera-target-triage/0.1 (+https://reactome.org/ContentService)"


@dataclass(frozen=True)
class RemotePathway:
    """One real Reactome pathway a gene belongs to, plus that pathway's real member genes.

    Field names mirror `enrichr.Pathway` (`term`, `genes`) so the starburst renderer consumes either
    without special-casing. `stid` is the citation — the stable Reactome accession the figure and the
    agent's narration must point at, never a fabricated id."""
    term: str                       # human pathway name, e.g. "Interleukin-6 signaling"
    stid: str                       # Reactome stable id, e.g. "R-HSA-1059683" (the citation)
    genes: tuple[str, ...] = ()     # REAL member gene symbols of this pathway (incl. the focal gene)

    @property
    def n_genes(self) -> int:
        return len(self.genes)


def _load_cache() -> dict:
    if os.path.exists(CACHE):
        try:
            return json.load(open(CACHE))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_cache(cache: dict) -> None:
    try:
        json.dump(cache, open(CACHE, "w"), indent=0)
    except OSError:
        pass   # a cache write failure must never break a lookup


def _get_json(url: str):
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
        return json.loads(r.read())


def _pathways_for_gene(gene: str) -> list[dict]:
    """Reactome pathways (human) that contain the gene. Returns [] if none / on any error."""
    url = f"{BASE}/data/mapping/UniProt/{urllib.parse.quote(gene)}/pathways?species={SPECIES}"
    data = _get_json(url)
    if not isinstance(data, list):
        return []
    # keep only real, non-disease human pathways with a stable id
    return [p for p in data
            if isinstance(p, dict) and p.get("stId") and not p.get("isInDisease")]


def _members_of_pathway(stid: str) -> tuple[str, ...]:
    """The REAL member gene symbols of a pathway, from its participating reference entities."""
    url = f"{BASE}/data/participants/{urllib.parse.quote(stid)}/referenceEntities"
    data = _get_json(url)
    if not isinstance(data, list):
        return ()
    genes: set[str] = set()
    for e in data:
        if not isinstance(e, dict):
            continue
        gn = e.get("geneName")
        # geneName is usually a list; take the first symbol. Skip entities with no gene symbol
        # (small molecules, complexes) — the starburst rings PROTEIN partners.
        sym = gn[0] if isinstance(gn, list) and gn else (gn if isinstance(gn, str) else None)
        if sym:
            genes.add(str(sym).strip().upper())
    return tuple(sorted(genes))


def fetch(gene: str, progress=None) -> RemotePathway | None:
    """The most relevant real Reactome pathway for `gene`, with its real member genes — or None.

    Cached by upper-cased gene. Returns None (never raises) when the gene maps to no human pathway,
    when the chosen pathway yields no member genes, or on any network failure — so the caller degrades
    to an honest 'could not place this gene', inventing nothing.

    Selection is deterministic and code-owned: among the gene's pathways, pick the one with the most
    retrievable member genes (the richest real neighbourhood), breaking ties by stable id. Within that
    pathway, partners are ORDERED by cross-pathway connectivity — how many of the gene's OTHER Reactome
    pathways each partner also appears in — so the gene's real signalling hubs (e.g. JAK1, STAT5 for
    STAT3) rank above incidental co-members (histones, etc.) that Reactome lists but that aren't part
    of the gene's signalling story. That score is a COUNT of real co-occurrences, not an invented
    relevance judgement, so the honesty contract holds: the model never chooses here; it only narrates
    what this returns."""
    from collections import Counter

    g = (gene or "").strip().upper()
    if not g:
        return None

    cache = _load_cache()
    if g in cache:
        entry = cache[g]
        if entry is None:
            return None
        return RemotePathway(term=entry["term"], stid=entry["stid"],
                             genes=tuple(entry.get("genes", ())))

    try:
        pathways = _pathways_for_gene(g)
    except Exception as e:  # noqa: BLE001 — degrade to "not found", never crash the view
        if progress:
            progress(f"  reactome error (pathways): {e}")
        return None

    # Two passes, no extra requests beyond the members already fetched. First, gather every pathway's
    # members once and accumulate how often each partner co-occurs with the gene across ALL its
    # pathways — the connectivity score that measures who the gene's real signalling hubs are.
    connectivity: Counter = Counter()
    candidates: list[tuple[str, str, tuple[str, ...]]] = []   # (stid, term, members)
    for p in pathways:
        stid = p["stId"]
        try:
            members = _members_of_pathway(stid)
        except Exception as e:  # noqa: BLE001
            if progress:
                progress(f"  reactome error (members {stid}): {e}")
            continue
        connectivity.update(m for m in members if m != g)
        # only keep a pathway that actually contains the focal gene AND has partners to ring it with
        if g in members and len(members) >= 2:
            candidates.append((stid, str(p.get("displayName") or stid), members))

    # Now pick the pathway. Prefer a SPECIFIC one (size in-band, not a 3-member stub or a 380-member
    # catch-all), and among those the one whose members are the gene's most-connected neighbourhood —
    # summed partner connectivity, so a JAK/STAT cytokine pathway outranks an incidental cofactor
    # complex of the same size. Both terms are real co-occurrence counts, never invented relevance.
    def _score(item: tuple[str, str, tuple[str, ...]]) -> tuple:
        stid, _term, members = item
        n = len(members)
        in_band = _MIN_PATHWAY_SIZE <= n <= _MAX_PATHWAY_SIZE
        distance = 0 if in_band else min(abs(n - _MIN_PATHWAY_SIZE), abs(n - _MAX_PATHWAY_SIZE))
        centrality = sum(connectivity[m] for m in members if m != g)
        # in-band beats out-of-band; then most-central neighbourhood; out-of-band ranked by closeness
        # to the band; stable id last for determinism.
        return (1 if in_band else 0, centrality, -distance, stid)

    best: RemotePathway | None = None
    if candidates:
        best_stid, best_term, best_members = max(candidates, key=_score)
        # rank partners by connectivity (desc), tie-break alphabetically for determinism; keep the
        # focal gene first, then the top partners up to the cap. The score is a real co-occurrence
        # count — the figure surfaces the gene's signalling hubs, never a fabricated ordering.
        partners = sorted((m for m in best_members if m != g),
                          key=lambda m: (-connectivity[m], m))
        kept = (g, *partners[: _MAX_MEMBERS - 1])
        best = RemotePathway(term=best_term, stid=best_stid, genes=kept)

    cache[g] = (None if best is None
                else {"term": best.term, "stid": best.stid, "genes": list(best.genes)})
    _save_cache(cache)
    return best


if __name__ == "__main__":
    for sym in ("STAT3", "EGFR", "NOTAREALGENE"):
        pw = fetch(sym)
        if pw is None:
            print(f"{sym:14s} -> no pathway found (degrades honestly)")
        else:
            print(f"{sym:14s} -> {pw.term} ({pw.stid}) n={pw.n_genes} :: {', '.join(pw.genes[:8])}")
