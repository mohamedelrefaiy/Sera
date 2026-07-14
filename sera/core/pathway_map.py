"""Pathway-context map — where a gene sits in its signalling neighbourhood, and why the layers may
disagree.

The bench-notebook sketch draws *what happened* (arrows up/down). This draws the *biology*: the gene
as a node among its REAL pathway partners, coloured by verdict, with candidate mechanisms shown as
clearly-marked hypotheses. It answers "what wiring does this gene belong to, and what could explain
the mRNA/protein split" — a genuine biological read.

Design contract (why this is trustworthy, not decorative):

- **Partners are real, never invented.** They come from the enriched pathway's overlap members
  (`enrichr.Pathway.genes`, i.e. Enrichr's `row[5]`), not from the model's memory of who signals
  with whom. A gene in no enriched pathway gets NO neighbourhood — honest emptiness over a made-up map.
- **The verdict drives the focal node's colour** (discordant / replicated / one-sided / neither),
  read straight from the concordance row; it is never recomputed here.
- **Candidate mechanisms are hypotheses, drawn as hypotheses.** They are the decision brief's
  code-owned `EXPLANATION_CLASSES` for the verdict (post-transcriptional control, temporal feedback,
  …), rendered dashed and labelled "hypotheses" — the honest "why they might disagree", never a
  claimed mechanism, and only for a disagreement verdict.
- **Deterministic, pure, self-contained.** No LLM, no I/O, no globals. The SVG carries no external
  fonts/scripts/URLs, so it is testable by semantic tokens and renders anywhere.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from .decision_brief import EXPLANATION_CLASSES, EXPLANATION_LABELS
from .pathway_render import render_topology
from .pathway_topology import select_for

# Verdict → focal-node colour + a short plain word for the badge. Matches the app's verdict palette.
_VERDICT_STYLE: dict[str, tuple[str, str]] = {
    "discordant": ("#B5761E", "discordant"),
    "replicated": ("#2E7D5B", "replicated"),
    "mrna_only": ("#3F7DBF", "mRNA-only"),
    "protein_only": ("#B5568F", "protein-only"),
    "neither": ("#7C898D", "neither"),
}
_DEFAULT_STYLE = ("#7C898D", "unknown")

# Verdicts where the two layers can disagree — the only ones that get "why might they disagree"
# hypotheses. Agreement (replicated) and no-signal (neither) do not.
_DISAGREE = frozenset({"discordant", "mrna_only", "protein_only"})

_MAX_HYPOTHESES = 3     # candidate-mechanism hypotheses to surface (legibility)
_MAX_PARTNERS = 6       # partners to ring around the focal node (readability)

_PARTNER = "#516067"
_LINE = "#C7D0D3"
_INK = "#182023"
_FAINT = "#68777B"
_PAPER = "#FBFAF7"
_HYP = "#8A6D3B"


@dataclass(frozen=True)
class PathwayMap:
    """The whole biological-context figure: a code-owned spec plus a self-contained SVG. Immutable
    and JSON-serialisable — the agent ships it, the frontend drops it in, the golden test pins it.

    Two rendering styles share this spec. When the focal gene is in a CURATED pathway, `style` is
    "topology" and the figure is the CST-style compartment/cascade diagram; `topology_nodes`,
    `topology_edges`, and `provenance` carry exactly what was drawn and where it came from (the
    honesty test pins these). Otherwise `style` is "starburst" — the honest fallback: the gene ringed
    by its real Enrichr partners, with those topology fields empty."""
    focal_gene: str
    focal_verdict: str
    focal_colour: str
    pathway: str | None
    partners: tuple[str, ...]
    hypotheses: tuple[str, ...]
    caption: str
    svg: str
    style: str = "starburst"                       # "topology" | "starburst"
    topology_nodes: tuple[str, ...] = ()           # node ids actually drawn (curated only)
    topology_edges: tuple[tuple[str, str, str], ...] = ()   # (src, dst, type) actually drawn
    provenance: dict | None = None                 # {source_db, reactome_id, curator, curated_on, …}


@dataclass(frozen=True)
class _Draft:
    focal_gene: str
    focal_colour: str
    verdict_word: str
    pathway_label: str
    partners: tuple[str, ...]
    no_partners: bool
    hypotheses: tuple[str, ...]
    hypotheses_wrapped: tuple[tuple[str, ...], ...]


def _pretty_pathway(term: str) -> str:
    """Drop the Reactome accession suffix (' R-HSA-…') for a readable label; the full term stays in
    the spec's `pathway` field for provenance."""
    cut = term.split(" R-HSA-")[0].split(" WP")[0]
    return cut.strip() or term


def _select_pathway(gene: str, pathways):
    """The most-enriched pathway the gene is actually a member of, and that pathway's OTHER members
    as partners. Returns (Pathway|None, partners). Members are real (from the overlap list)."""
    g = gene.upper()
    for p in pathways:                      # pathways arrive most-significant first
        members = tuple(m.upper() for m in (p.genes or ()))
        if g in members:
            partners = tuple(m for m in members if m != g)[:_MAX_PARTNERS]
            return p, partners
    return None, ()


def _hypotheses(verdict: str) -> tuple[str, ...]:
    """Candidate mechanisms for a disagreement verdict, from the decision brief's closed set. Plain
    labels; the caller marks them as hypotheses — never invented, never presented as fact."""
    if verdict not in _DISAGREE:
        return ()
    classes = EXPLANATION_CLASSES.get(verdict, ())[:_MAX_HYPOTHESES]
    return tuple(EXPLANATION_LABELS[c] for c in classes)


def _wrap(text: str, width: int = 46) -> tuple[str, ...]:
    """Greedy word-wrap for hypothesis lines (SVG text doesn't wrap). Replaces '&' so the SVG stays
    valid without entity encoding."""
    words = text.replace("&", "and").split()
    lines: list[str] = []
    cur = ""
    for w in words:
        if len(cur) + len(w) + 1 > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return tuple(lines[:2])   # at most two lines per hypothesis, for a tidy panel


# --- self-contained SVG rendering ------------------------------------------------------------

def _render_svg(m: _Draft) -> str:
    """A node-graph on paper: the focal gene at centre-left, its real pathway partners ringed around
    it (edges = shared pathway), a verdict badge, and — for a disagreement — a dashed 'hypotheses'
    panel of candidate mechanisms. Handwriting-leaning, fully self-contained."""
    font = "'Bradley Hand','Segoe Print','Comic Sans MS',cursive"
    mono = "monospace"
    W = 640
    H = 330 if m.hypotheses else 270
    # Centre the graph BELOW the header band (subtitle baseline y=52) so the top partner node clears
    # it: top node centre = cy - ring = 76, its circle top edge = 56, just under the subtitle.
    cx, cy, r = 150, 168, 30
    ring = 92

    parts: list[str] = [
        f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" font-family="{font}" '
        f'role="img" aria-label="Pathway-context map for {m.focal_gene}">',
        f'<rect x="0" y="0" width="{W}" height="{H}" rx="12" fill="{_PAPER}"/>',
        f'<text x="24" y="34" font-size="16" fill="{_INK}" font-weight="700">'
        f'{m.focal_gene} in context</text>',
        f'<text x="24" y="52" font-size="11.5" fill="{_FAINT}">{m.pathway_label}</text>',
    ]

    n = len(m.partners)
    # Offset the ring by half a step so NO partner sits at 12 o'clock, where it would collide with
    # the centred subtitle; nodes fan out to the sides instead.
    step = 360.0 / max(n, 1)
    positions = []
    for i, g in enumerate(m.partners):
        ang = (-90 + step / 2 + i * step) * math.pi / 180.0
        px = cx + ring * math.cos(ang)
        py = cy + ring * math.sin(ang)
        positions.append((g, px, py))
    for _g, px, py in positions:
        parts.append(f'<path d="M{cx} {cy} L{px:.0f} {py:.0f}" stroke="{_LINE}" '
                     f'stroke-width="1.5" fill="none"/>')
    for g, px, py in positions:
        parts.append(f'<circle cx="{px:.0f}" cy="{py:.0f}" r="20" fill="#fff" '
                     f'stroke="{_PARTNER}" stroke-width="1.5"/>')
        parts.append(f'<text x="{px:.0f}" y="{py + 4:.0f}" text-anchor="middle" font-size="10.5" '
                     f'fill="{_INK}" font-family="{mono}">{g}</text>')

    parts += [
        f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="{m.focal_colour}" fill-opacity="0.14" '
        f'stroke="{m.focal_colour}" stroke-width="2.5"/>',
        f'<text x="{cx}" y="{cy + 5}" text-anchor="middle" font-size="13" fill="{_INK}" '
        f'font-weight="700" font-family="{mono}">{m.focal_gene}</text>',
        f'<text x="{cx}" y="{cy + r + 16}" text-anchor="middle" font-size="10.5" '
        f'fill="{m.focal_colour}" font-weight="700">{m.verdict_word}</text>',
    ]

    if m.no_partners:
        parts.append(f'<text x="300" y="{cy}" font-size="12.5" fill="{_FAINT}">'
                     f'{m.focal_gene} is not in an enriched pathway here — no neighbourhood to map.'
                     f'</text>')

    if m.hypotheses:
        hx, hy = 330, 78
        parts.append(f'<text x="{hx}" y="{hy}" font-size="10.5" fill="{_HYP}" font-weight="700" '
                     f'font-family="{mono}">WHY THE LAYERS MAY DISAGREE — HYPOTHESES</text>')
        parts.append(f'<line x1="{hx}" y1="{hy + 7}" x2="{W - 24}" y2="{hy + 7}" stroke="{_HYP}" '
                     f'stroke-width="1" stroke-dasharray="3 3" opacity="0.5"/>')
        for i, lines in enumerate(m.hypotheses_wrapped):
            yy = hy + 26 + i * 30
            parts.append(f'<circle cx="{hx + 5}" cy="{yy - 4}" r="2.5" fill="none" stroke="{_HYP}" '
                         f'stroke-width="1.3" stroke-dasharray="1.5 1.5"/>')
            for j, line in enumerate(lines):
                parts.append(f'<text x="{hx + 16}" y="{yy + j * 13}" font-size="11" fill="{_INK}" '
                             f'font-family="{mono}">{line}</text>')

    parts.append('</svg>')
    return "".join(parts)


def _build_topology_map(
    gene: str, verdict: str, colour: str, verdict_word: str,
    node_status: dict[str, str] | None,
) -> PathwayMap | None:
    """CST-style figure when the focal gene is in a CURATED pathway. Returns None if it is not, so
    the caller degrades to the starburst. Everything drawn is code-owned: the curated topology and
    (optionally) the concordance hit-status. The LLM only selected the gene; it authored nothing."""
    curated = select_for(gene)
    if curated is None:
        return None
    result = render_topology(
        curated, focal_gene=gene, focal_colour=colour, verdict_word=verdict_word,
        node_status=node_status)
    # Partners are the pathway CONTEXT — every real curated member the figure drew, minus the focal
    # gene itself — not just the confident hits. Hit-vs-context is still distinguished visually in the
    # SVG via `node_status` shading; the partner list names the neighbourhood, which is the whole
    # curated cascade. Ordered as the curated record lists them, for a stable, deterministic list.
    partners = tuple(g for g in result.node_ids if g != gene)
    provenance = {
        "source_db": "Reactome",
        "reactome_id": curated.reactome_id,
        "term": curated.term,
        "curator": curated.curator,
        "curated_on": curated.curated_on,
        "n_nodes": len(result.node_ids),
        "n_edges": len(result.edges),
    }
    label = curated.term
    caption = (
        f"{gene} in the {label} cascade — signal direction follows the curated arrows from the "
        f"membrane through cytoplasmic and nuclear effectors. Filled nodes are confident hits in "
        f"these screens; open nodes are pathway context (real members, not hits here). Every edge is "
        f"a curated, cited interaction (Reactome {curated.reactome_id}), never inferred."
    )
    return PathwayMap(
        focal_gene=gene, focal_verdict=verdict, focal_colour=colour,
        pathway=curated.reactome_id, partners=partners, hypotheses=(),
        caption=caption, svg=result.svg, style="topology",
        topology_nodes=result.node_ids, topology_edges=result.edges,
        provenance=provenance)


def build_pathway_map(row: dict, pathways, node_status: dict[str, str] | None = None,
                      force_starburst: bool = False) -> PathwayMap:
    """Assemble the pathway-context map. Pure and deterministic: no I/O, no LLM.

    `row` is a concordance row (verdict is read, never recomputed). `pathways` are `enrichr.Pathway`
    records WITH their overlap members, most-significant first. `node_status` (optional) maps a gene
    id → 'hit' | 'context', from the concordance table, so the curated figure can shade the cascade
    by what the screens actually moved.

    Preference order: if the focal gene is in a CURATED pathway, draw the CST-style topology figure
    (real compartments + directed, cited edges). Otherwise degrade honestly to the starburst — the
    gene ringed by its real Enrichr partners, with candidate mechanisms as marked hypotheses.

    `force_starburst` skips the curated-topology branch entirely. It exists for the RETRIEVED-gene
    path (web_pathway_map): a gene we pull from Reactome was NOT measured in these screens, yet it may
    coincidentally be a curated topology NODE (e.g. GRB2). Drawing the curated cascade for it would
    stamp the false 'confident hits in these screens' caption on a gene the screens never saw. When
    the caller knows the gene is off-screen retrieved context, it forces the honest starburst."""
    gene = str(row.get("gene", "")).upper()
    verdict = str(row.get("verdict", ""))
    colour, verdict_word = _VERDICT_STYLE.get(verdict, _DEFAULT_STYLE)

    if not force_starburst:
        topo = _build_topology_map(gene, verdict, colour, verdict_word, node_status)
        if topo is not None:
            return topo

    pw, partners = _select_pathway(gene, pathways)
    pathway_term = pw.term if pw else None
    hypotheses = _hypotheses(verdict)   # hypotheses stand on the verdict, not on pathway membership

    # An empty verdict means the gene was NOT measured in these screens (e.g. a gene we retrieved from
    # an external source). It has no hits and no two-layer result here, so the caption must NOT claim
    # it "shares a pathway with the other hits" or that "its layers agree" — both would be false.
    retrieved = verdict == ""

    if pw is None:
        caption = (f"{gene} could not be placed in a pathway, so there is no neighbourhood to map."
                   if retrieved else
                   f"{gene} is not in an enriched pathway for this hit set, so there is no shared "
                   "neighbourhood to map — its verdict still stands on its own.")
        pathway_label = "no pathway found for this gene"
    elif retrieved:
        label = _pretty_pathway(pw.term)
        caption = (f"{gene} sits in {label} among {', '.join(partners) or 'no other members'}. This "
                   "neighbourhood is retrieved context — {gene} was not measured in these screens, so "
                   "there is no verdict and no mRNA/protein result to show here.").format(gene=gene)
        pathway_label = f"retrieved context · {label}"
    else:
        label = _pretty_pathway(pw.term)
        caption = (f"{gene} sits in {label} alongside {', '.join(partners) or 'no other hits'}. "
                   + ("Because its two layers disagree, the panel lists candidate mechanisms as "
                      "hypotheses — not conclusions." if hypotheses else
                      "Its two layers agree, so the neighbourhood is shown without disagreement "
                      "hypotheses."))
        pathway_label = f"shares {label} with the other hits"

    draft = _Draft(
        focal_gene=gene, focal_colour=colour, verdict_word=verdict_word,
        pathway_label=pathway_label, partners=partners, no_partners=(pw is None),
        hypotheses=hypotheses,
        hypotheses_wrapped=tuple(_wrap(h) for h in hypotheses))
    svg = _render_svg(draft)

    return PathwayMap(
        focal_gene=gene, focal_verdict=verdict, focal_colour=colour,
        pathway=pathway_term, partners=partners, hypotheses=hypotheses,
        caption=caption, svg=svg)
