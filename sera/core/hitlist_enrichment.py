"""Hit-list pathway enrichment — the shared-biology answer for the replicated hit set.

Sera reconciles genes one at a time, but the *set* of replicated hits (both screens agree) has
shared biology worth naming: "these hits cluster in TCR signaling", not just "here are 43 genes".
This module answers that, deterministically and on the rim's terms:

- **The hit list is code-derived, never agent-authored.** `build_hitlist_enrichment` takes the
  concordance rows the API already loads and selects the `replicated` subset itself. The agent may
  narrate the resulting pathways; it can never add a gene to the list or name a pathway that isn't
  in the code-computed result.

- **Enrichment is delegated to the code-owned enrichr client**, injected as `enrich_fn` so this
  module stays pure and the golden test can drive it offline against the pre-warmed cache. The
  client returns real Reactome terms with real q-values; this module reshapes, it never fabricates.

- **The prose summary leaks no raw statistic** (same discipline as the decision brief). The number
  of genes and the number of pathways are protocol-ish counts, allowed; q-values are not.

- **Empty degrades honestly.** No replicated genes, or no pathways back, yields an explicit "nothing
  to report" — never an invented pathway.
"""
from __future__ import annotations

from dataclasses import dataclass

#: The verdict that defines a hit for enrichment: both screens agree (replicated). Closed on purpose
#: — only this verdict enters the shared-biology set; discordant/one-sided/neither are excluded.
_HIT_VERDICT = "replicated"


@dataclass(frozen=True)
class PathwayTerm:
    """One enriched pathway, reshaped from the enrichr client's `Pathway` record. Immutable; the
    agent narrates `term`, it never invents one."""
    term: str
    adj_p: float
    n_genes: int


@dataclass(frozen=True)
class HitlistEnrichment:
    """The whole shared-biology answer, immutable and JSON-serialisable. Consumed by the endpoint,
    the golden test, and the agent tool — one source of truth for the hit-list's pathway story."""
    genes: tuple[str, ...]
    n_genes: int
    pathways: tuple[PathwayTerm, ...]
    top_term: str | None
    plain_summary: str
    library: str


def _replicated_genes(rows) -> tuple[str, ...]:
    """The sorted, de-duplicated set of genes whose verdict is `replicated`. Code owns this — it is
    the hit list, not something the agent supplies."""
    genes = {(r.get("gene") or "").strip().upper()
             for r in rows if r.get("verdict") == _HIT_VERDICT and (r.get("gene") or "").strip()}
    return tuple(sorted(genes))


def build_hitlist_enrichment(
    rows,
    enrich_fn,
    library: str = "Reactome_2022",
) -> HitlistEnrichment:
    """Assemble the hit-list enrichment. Pure given `enrich_fn`: no globals, no I/O of its own.

    `rows` are concordance rows (the dicts the API already loads). `enrich_fn(genes, library)` is the
    code-owned enrichr client, injected so this stays testable offline. The verdict-based hit
    selection and the closed pathway list are code-owned; the agent may only phrase them."""
    genes = _replicated_genes(rows)
    if not genes:
        return HitlistEnrichment(
            genes=(), n_genes=0, pathways=(), top_term=None, library=library,
            plain_summary="No replicated hits at this condition, so there is no shared-biology set "
                          "to summarise.")

    raw = enrich_fn(genes, library=library) or []
    pathways = tuple(
        PathwayTerm(term=p.term, adj_p=round(float(p.adj_p), 8), n_genes=int(p.n_genes))
        for p in raw)

    if not pathways:
        return HitlistEnrichment(
            genes=genes, n_genes=len(genes), pathways=(), top_term=None, library=library,
            plain_summary=f"The {len(genes)} replicated hits did not return an enriched pathway "
                          "(the lookup was empty or unavailable) — no shared theme to report here.")

    top = pathways[0].term
    others = len(pathways) - 1
    tail = f", alongside {others} related pathway{'s' if others != 1 else ''}" if others else ""
    plain = (f"The {len(genes)} replicated hits cluster in “{top}”{tail} — a shared "
             "biological theme the two screens agree on, which a single-gene view would miss.")

    return HitlistEnrichment(
        genes=genes, n_genes=len(genes), pathways=pathways, top_term=top, library=library,
        plain_summary=plain)
