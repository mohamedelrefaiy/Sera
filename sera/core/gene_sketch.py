"""Gene sketch — the bench-notebook cartoon of one reconciliation.

The decision brief argues; this *draws*. Given a concordance row, it produces the cartoon a
scientist would sketch to explain a result: knock out the gene → what the transcript did → what the
protein did → the cytokine output, with the two layer-arrows diverging when the screens disagree.

Design contract (why this is trustworthy, not decorative):

- **Every arrow is code-derived.** Direction comes from `rna_promotes` / `prot_promotes` (a gene
  that promotes the cytokine goes DOWN when knocked down; a brake goes UP). The sketch can never
  draw an arrow the row does not support — the same "agents on the rim" discipline as the verdict.
- **Deterministic and pure.** No LLM, no I/O, no globals. The SVG is generated here so it is
  testable (semantic tokens, not pixels) and self-contained (no external fonts, scripts, or URLs),
  which lets the agent ship it whole and a strict host render it.
- **An untested layer is drawn as "not measured", never faked.** A protein-only / mRNA-only or
  protein-untested row shows the missing side honestly.
- **No raw statistics in the prose.** The headline says "went up / went down / diverge", never a
  z-score or fold change — the numbers live in the effect figure, the sketch carries the meaning.
"""
from __future__ import annotations

from dataclasses import dataclass

# The five verdicts the sketch understands (mirrors core.concordance). An unknown verdict is
# rejected rather than drawn wrong.
_VERDICTS: frozenset[str] = frozenset(
    {"replicated", "discordant", "mrna_only", "protein_only", "neither"})

# Palette (matches the frontend's assay colours: mRNA green, protein pink, ink/faint greys). Kept
# here so the SVG is self-contained and the sketch reads the same inside and outside the app.
_MRNA = "#00A88B"
_PROT = "#D34D91"
_INK = "#182023"
_FAINT = "#68777B"
_LINE = "#C7D0D3"
_PAPER = "#FBFAF7"

_DIR_WORD = {"up": "went up", "down": "went down", "flat": "no measurable change",
             "untested": "was not measured"}
_ROLE = {True: "activator", False: "brake", None: "unclear"}
_CYTO_PRETTY = {"IL2": "IL-2"}

_STAMP = {
    "discordant": ("the two layers DIVERGE — decoupled", "#B5761E"),
    "replicated": ("both layers AGREE — replicated", "#2E7D5B"),
    "mrna_only": ("transcript only — bridge to protein", "#B5761E"),
    "protein_only": ("protein only — a transcript screen misses it", "#B5761E"),
    "neither": ("neither layer moves — low yield", "#68777B"),
}


@dataclass(frozen=True)
class GeneSketch:
    """The whole sketch: a code-owned spec plus a rendered, self-contained SVG. Immutable and
    JSON-serialisable — the agent ships it, the frontend drops it in, the golden test pins it."""
    gene: str
    cytokine: str
    condition: str
    verdict: str
    rna_direction: str        # "up" | "down" | "flat" | "untested"
    protein_direction: str    # "up" | "down" | "flat" | "untested"
    rna_label: str
    protein_label: str
    role: str                 # plain role read: "brake" | "activator" | "unclear"
    headline: str
    svg: str


@dataclass(frozen=True)
class _Draft:
    """Rendering-time values derived once, passed to the SVG builder so it stays a pure function of
    a small, explicit struct."""
    gene: str
    cytokine_pretty: str
    condition: str
    rna_direction: str
    protein_direction: str
    rna_word: str
    prot_word: str
    stamp: str
    stamp_colour: str


def _direction(promotes, tested: bool, is_hit: bool) -> str:
    """Direction of the layer's arrow when the gene is knocked DOWN. A promoter (promotes=True) loses
    output when removed → 'down'; a brake (promotes=False) → 'up'. Not tested / not a hit → honest
    'untested' / 'flat'."""
    if not tested:
        return "untested"
    if not is_hit or promotes is None:
        return "flat"
    return "down" if promotes else "up"


def _headline(gene: str, cyto: str, verdict: str, rna: str, prot: str, role: str) -> str:
    """One plain sentence a bench reader consumes — no statistic, ever."""
    c = _CYTO_PRETTY.get(cyto, cyto)
    if verdict == "discordant":
        return (f"Knock out {gene} and the two layers disagree: the {c} transcript "
                f"{_DIR_WORD[rna]} while the protein {_DIR_WORD[prot]}.")
    if verdict == "replicated":
        return (f"Knock out {gene} and both layers agree — transcript and protein "
                f"{_DIR_WORD[rna]} together, so this reads as a {role}.")
    if verdict == "mrna_only":
        return (f"Knock out {gene} and only the transcript moves ({_DIR_WORD[rna]}); "
                "the protein side is the open question.")
    if verdict == "protein_only":
        return (f"Knock out {gene} and only the protein moves ({_DIR_WORD[prot]}); "
                "a transcript-only screen would miss it.")
    return f"Knock out {gene} and neither layer moves much for {c} here."


# --- self-contained SVG rendering ------------------------------------------------------------

def _arrow(cx: float, cy: float, direction: str, colour: str) -> str:
    """A short hand-drawn-style vertical arrow (up/down), a flat dash, or a dotted 'untested' mark,
    centred at (cx, cy). Pure geometry, no external refs."""
    if direction == "up":
        return (f'<path d="M{cx} {cy+16} L{cx} {cy-16}" stroke="{colour}" stroke-width="3" '
                f'stroke-linecap="round" fill="none"/>'
                f'<path d="M{cx-6} {cy-8} L{cx} {cy-17} L{cx+6} {cy-8}" stroke="{colour}" '
                f'stroke-width="3" stroke-linecap="round" stroke-linejoin="round" fill="none"/>')
    if direction == "down":
        return (f'<path d="M{cx} {cy-16} L{cx} {cy+16}" stroke="{colour}" stroke-width="3" '
                f'stroke-linecap="round" fill="none"/>'
                f'<path d="M{cx-6} {cy+8} L{cx} {cy+17} L{cx+6} {cy+8}" stroke="{colour}" '
                f'stroke-width="3" stroke-linecap="round" stroke-linejoin="round" fill="none"/>')
    if direction == "flat":
        return (f'<path d="M{cx-12} {cy} L{cx+12} {cy}" stroke="{_FAINT}" stroke-width="3" '
                f'stroke-linecap="round" fill="none"/>')
    # untested — dotted, no arrowhead
    return (f'<path d="M{cx-12} {cy} L{cx+12} {cy}" stroke="{_FAINT}" stroke-width="2.5" '
            f'stroke-linecap="round" stroke-dasharray="2 5" fill="none"/>')


def _render_svg(g: _Draft) -> str:
    """A notebook-style cartoon on paper: [KO gene] → transcript layer (arrow) / protein layer
    (arrow) → cytokine. Handwriting feel via a cursive-leaning font stack; fully self-contained."""
    font = "'Bradley Hand','Segoe Print','Comic Sans MS',cursive"
    W, H = 560, 320
    # Header occupies y<=56; the diagram starts below it so nothing overlaps the title/subtitle.
    x_ko, y_ko = 100, 96
    x_layer = 100
    y_rna, y_prot = 176, 246
    x_arrow = 300
    x_cyto, y_cyto = 470, 211

    parts: list[str] = [
        f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" '
        f'font-family="{font}" role="img" aria-label="Bench sketch of {g.gene} on '
        f'{g.cytokine_pretty}">',
        f'<rect x="0" y="0" width="{W}" height="{H}" rx="12" fill="{_PAPER}"/>',
        f'<text x="24" y="34" font-size="17" fill="{_INK}" font-weight="700">'
        f'{g.gene} · {g.cytokine_pretty} sketch</text>',
        f'<text x="24" y="52" font-size="11.5" fill="{_FAINT}">knock it out, follow both layers '
        f'({g.condition})</text>',
        f'<rect x="{x_ko-58}" y="{y_ko-20}" width="116" height="40" rx="9" fill="none" '
        f'stroke="{_INK}" stroke-width="2"/>',
        f'<text x="{x_ko}" y="{y_ko+5}" text-anchor="middle" font-size="14" fill="{_INK}">'
        f'knock out {g.gene}</text>',
        f'<path d="M{x_ko} {y_ko+20} L{x_ko} {y_rna-40}" stroke="{_LINE}" stroke-width="2" '
        f'fill="none"/>',
        # transcript chip + arrow
        f'<rect x="{x_layer-58}" y="{y_rna-20}" width="150" height="40" rx="9" fill="#fff" '
        f'stroke="{_MRNA}" stroke-width="2"/>',
        f'<text x="{x_layer+17}" y="{y_rna-2}" text-anchor="middle" font-size="13" fill="{_INK}">'
        f'transcript</text>',
        f'<text x="{x_layer+17}" y="{y_rna+13}" text-anchor="middle" font-size="9.5" '
        f'fill="{_FAINT}" font-family="monospace">Perturb-seq</text>',
        _arrow(x_arrow, y_rna, g.rna_direction, _MRNA),
        f'<text x="{x_arrow+22}" y="{y_rna+4}" font-size="11.5" fill="{_MRNA}">{g.rna_word}</text>',
        # protein chip + arrow
        f'<rect x="{x_layer-58}" y="{y_prot-20}" width="150" height="40" rx="9" fill="#fff" '
        f'stroke="{_PROT}" stroke-width="2"/>',
        f'<text x="{x_layer+17}" y="{y_prot-2}" text-anchor="middle" font-size="13" fill="{_INK}">'
        f'protein</text>',
        f'<text x="{x_layer+17}" y="{y_prot+13}" text-anchor="middle" font-size="9.5" '
        f'fill="{_FAINT}" font-family="monospace">FACS</text>',
        _arrow(x_arrow, y_prot, g.protein_direction, _PROT),
        f'<text x="{x_arrow+22}" y="{y_prot+4}" font-size="11.5" fill="{_PROT}">{g.prot_word}</text>',
        # converge to the cytokine node
        f'<path d="M{x_arrow+70} {y_rna} C {x_cyto-40} {y_rna}, {x_cyto-40} {y_cyto}, '
        f'{x_cyto-18} {y_cyto}" stroke="{_LINE}" stroke-width="2" fill="none"/>',
        f'<path d="M{x_arrow+70} {y_prot} C {x_cyto-40} {y_prot}, {x_cyto-40} {y_cyto}, '
        f'{x_cyto-18} {y_cyto}" stroke="{_LINE}" stroke-width="2" fill="none"/>',
        f'<circle cx="{x_cyto}" cy="{y_cyto}" r="30" fill="none" stroke="{_INK}" stroke-width="2"/>',
        f'<text x="{x_cyto}" y="{y_cyto+5}" text-anchor="middle" font-size="14" fill="{_INK}">'
        f'{g.cytokine_pretty}</text>',
        f'<text x="24" y="{H-16}" font-size="12" fill="{g.stamp_colour}" font-weight="700">'
        f'{g.stamp}</text>',
        '</svg>',
    ]
    return "".join(parts)


def build_gene_sketch(row: dict) -> GeneSketch:
    """Assemble the sketch from one concordance row. Pure and deterministic: no I/O, no LLM.

    Directions are read from `rna_promotes` / `prot_promotes` (never recomputed); an untested or
    non-hit layer is drawn honestly. Raises on an unknown verdict rather than drawing it wrong."""
    verdict = row.get("verdict")
    if verdict not in _VERDICTS:
        raise ValueError(f"unknown verdict {verdict!r}; expected one of {tuple(sorted(_VERDICTS))}")

    gene = str(row.get("gene", "")).upper()
    cyto = str(row.get("cytokine", ""))
    cyto_pretty = _CYTO_PRETTY.get(cyto, cyto)
    condition = str(row.get("condition", ""))

    rna_dir = _direction(row.get("rna_promotes"), bool(row.get("rna_tested", True)),
                         bool(row.get("hit_rna")))
    prot_dir = _direction(row.get("prot_promotes"), bool(row.get("prot_tested", True)),
                          bool(row.get("hit_prot")))
    role = _ROLE.get(row.get("rna_promotes"), "unclear")

    rna_label = f"transcript {_DIR_WORD[rna_dir]}"
    prot_label = ("protein was not measured" if prot_dir == "untested"
                  else f"protein {_DIR_WORD[prot_dir]}")

    headline = _headline(gene, cyto, verdict, rna_dir, prot_dir, role)
    stamp, stamp_colour = _STAMP[verdict]

    draft = _Draft(
        gene=gene, cytokine_pretty=cyto_pretty, condition=condition,
        rna_direction=rna_dir, protein_direction=prot_dir,
        rna_word=_DIR_WORD[rna_dir], prot_word=_DIR_WORD[prot_dir],
        stamp=stamp, stamp_colour=stamp_colour)
    svg = _render_svg(draft)

    return GeneSketch(
        gene=gene, cytokine=cyto, condition=condition, verdict=verdict,
        rna_direction=rna_dir, protein_direction=prot_dir,
        rna_label=rna_label, protein_label=prot_label, role=role,
        headline=headline, svg=svg)
