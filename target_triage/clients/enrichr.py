"""Enrichr client: pathway enrichment over a gene list (the Hit-list view's replicated subset).

Live REST API (free, no key). Two calls: POST the gene list -> userListId, then GET enrichment
against a pathway library. Results cache to enrichr_cache.json keyed on the SORTED gene set, so a
repeated list (and the demo's default hit-list) resolves offline and instantly — mirroring the
Open Targets / ClinicalTrials caches. Returns the top pathways as immutable records.

Enrichr is inherently dynamic (enrichment depends on which genes the user pasted), so this is a
live-but-cached client rather than a fully precomputed artifact. The demo's default replicated set
is pre-warmed into the cache so the offline demo shows real pathways.
"""
from __future__ import annotations

import hashlib
import json
import os
import urllib.request
from dataclasses import dataclass

BASE = "https://maayanlab.cloud/Enrichr"
CACHE = os.path.join(os.path.dirname(__file__), "enrichr_cache.json")
DEFAULT_LIBRARY = "Reactome_2022"
TOP_N = 6


@dataclass(frozen=True)
class Pathway:
    term: str
    adj_p: float
    n_genes: int
    library: str
    genes: tuple[str, ...] = ()   # the overlapping input genes Enrichr reports for this pathway
                                  # (row[5]); kept so a pathway-context map can place a gene among
                                  # its REAL partners, never invented ones. Defaults to () for
                                  # backward compatibility with cache entries written before this.


def _key(genes: tuple[str, ...], library: str) -> str:
    norm = ",".join(sorted({g.strip().upper() for g in genes if g.strip()}))
    return hashlib.sha1(f"{library}:{norm}".encode()).hexdigest()[:16]


def _load_cache() -> dict:
    if os.path.exists(CACHE):
        return json.load(open(CACHE))
    return {}


def _save_cache(cache: dict) -> None:
    json.dump(cache, open(CACHE, "w"), indent=0)


def _add_list(genes: tuple[str, ...]) -> str:
    boundary = "----concordboundary"
    payload = "\n".join(sorted({g.strip().upper() for g in genes if g.strip()}))
    body = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"list\"\r\n\r\n{payload}\r\n"
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"description\"\r\n\r\nconcord\r\n"
        f"--{boundary}--\r\n"
    ).encode()
    req = urllib.request.Request(
        BASE + "/addList", data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return str(json.loads(r.read())["userListId"])


def _enrich(user_list_id: str, library: str) -> list[Pathway]:
    url = BASE + f"/enrich?userListId={user_list_id}&backgroundType={library}"
    with urllib.request.urlopen(url, timeout=20) as r:
        rows = json.loads(r.read()).get(library, [])
    # Enrichr row: [rank, term, p, zscore, combined, [genes], adj_p, ...]
    out = []
    for row in rows[:TOP_N]:
        members = tuple(str(g).upper() for g in row[5])
        out.append(Pathway(term=row[1], adj_p=round(float(row[6]), 8),
                           n_genes=len(members), library=library, genes=members))
    return out


def enrich(genes, library: str = DEFAULT_LIBRARY, progress=None) -> list[Pathway]:
    """Top enriched pathways for a gene list. Cached by sorted gene set. Returns [] on any
    failure (network, empty list) rather than raising — the Hit-list degrades to no chips."""
    genes = tuple(genes)
    if not genes:
        return []
    cache = _load_cache()
    k = _key(genes, library)
    if k in cache:
        # `genes` may be absent in entries written before the pathway-map change; tolerate that.
        return [Pathway(term=p["term"], adj_p=p["adj_p"], n_genes=p["n_genes"],
                        library=p["library"], genes=tuple(p.get("genes", ())))
                for p in cache[k]]
    try:
        uid = _add_list(genes)
        paths = _enrich(uid, library)
    except Exception as e:  # noqa: BLE001 — degrade to no enrichment, never crash the view
        if progress:
            progress(f"  enrichr error: {e}")
        return []
    cache[k] = [{"term": p.term, "adj_p": p.adj_p, "n_genes": p.n_genes, "library": p.library,
                 "genes": list(p.genes)} for p in paths]
    _save_cache(cache)
    return paths


if __name__ == "__main__":
    for p in enrich(["ITK", "BCL10", "VAV1", "ZAP70", "LAT", "CD3D", "LCP2", "PLCG1"]):
        print(f"{p.term[:55]:55s} adj_p={p.adj_p:.2e} n={p.n_genes}")
