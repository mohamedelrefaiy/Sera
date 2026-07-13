"""CST-style signalling-figure renderer — compartment bands, a membrane, and typed directed edges.

This turns a curated pathway (`pathway_topology.CuratedPathway`) plus a deterministic layout
(`pathway_layout.layout`) into a self-contained SVG in the idiom of a Cell Signaling Technology wall
chart: a lipid-bilayer membrane with an embedded receptor at the top, a cytoplasmic cascade of
family-coloured protein pills wired by directed, typed edges (activation arrow → / inhibition bar ⊣ /
second-messenger production / translocation dash / transcription), and a nucleus band with the
transcription factors that drive the cytokine output.

Two honesty features are baked into the drawing, not narrated on top of it:

- **The focal gene** (the one the user asked about) gets a verdict-coloured ring, so the figure says
  which node this reconciliation is about and how its two layers landed.
- **Hit-status shading** (optional, from `node_status`): a curated node that is a confident hit in the
  screens is drawn solid; a context-only node (real biology, but not a hit here) is drawn faded. This
  is a second axis of truth the starburst never had — it separates "the cascade" from "what THIS
  screen actually moved", straight from the concordance table.

Everything is code-derived. The renderer draws only nodes/edges the curated topology asserts and only
statuses the concordance table reports; it invents nothing. Pure, deterministic, no external
fonts/scripts/URLs (arrowheads are inline paths, not `<marker>` refs), so it is golden-testable and
renders anywhere.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from .pathway_layout import Placed, layout
from .pathway_topology import COMPARTMENTS, CuratedPathway, TopoEdge

# --- palette (muted, professional, light-paper safe) -------------------------------------------
_PAPER = "#FBFAF7"
_INK = "#182023"
_FAINT = "#68777B"
_BAND_LABEL = "#9AA6AA"

# family → (fill, border, label-colour). Cool for enzymes/receptors, warm for adaptors/TF/messengers.
_FAMILY_STYLE: dict[str, tuple[str, str, str]] = {
    "receptor":         ("#8FA8BF", "#5B7690", "#FFFFFF"),
    "kinase":           ("#AEC8E0", "#6F97BD", "#1F2937"),
    "adaptor":          ("#E3CFA4", "#B79A63", "#3A2F1A"),
    "phospholipase":    ("#C9BCE0", "#8E79B8", "#241A33"),
    "gtpase":           ("#B6D3A8", "#7FA76D", "#22331B"),
    "second_messenger": ("#F0DCAE", "#CDB072", "#4A3D1A"),
    "tf":               ("#E6B891", "#C68A5C", "#3A2416"),
    "cytokine":         ("#D9A6C2", "#B56E96", "#331726"),
}
_FALLBACK_STYLE = ("#DCE3E6", "#9BAAB0", "#1F2937")

# compartment band tints (behind the nodes) — the "you are in the cytoplasm / nucleus" cue.
_BAND_TINT: dict[str, str] = {
    "extracellular": "#F4F1EA",
    "membrane":      "#EFE7D8",
    "cytoplasm":     "#FBFAF7",
    "nucleus":       "#EEF1F4",
    "output":        "#F5F0F3",
}

# edge-type → stroke colour. Direction/shape are drawn by geometry; colour reinforces meaning.
_EDGE_INK = {
    "activation":    "#3F4650",
    "inhibition":    "#9A4A4A",
    "production":    "#B79A63",
    "translocation": "#7A8290",
    "transcription": "#8A6D3B",
}

_MEM_HEAD = "#D9C9B0"
_MEM_TAIL = "#B9A888"

# canvas geometry (700×580). Band tops (extracellular, membrane, cytoplasm, nucleus, output) + a
# nominal band height for centring the thin bands; cytoplasm nodes spread down their own working area.
# The cytoplasm gets the lion's share of height because the cascade is a long linear chain.
_W = 700
_H = 600
_BAND_TOP = (44, 96, 150, 470, 526)
_BAND_H = 44
_CYTO_TOP, _CYTO_BOT = 150, 466
_ROW_GAP = 40                 # min vertical gap between adjacent cytoplasm depth rows

_PILL_W, _PILL_H = 74, 26


@dataclass(frozen=True)
class RenderResult:
    """The SVG plus the flat lists the honesty test pins: exactly which node ids and (src,dst,type)
    edges were drawn. Nothing is on screen that isn't in these lists, and these come only from the
    curated pathway."""
    svg: str
    node_ids: tuple[str, ...]
    edges: tuple[tuple[str, str, str], ...]


def _style(family: str) -> tuple[str, str, str]:
    return _FAMILY_STYLE.get(family, _FALLBACK_STYLE)


def _band_tops_for_layout() -> tuple[float, ...]:
    """Band tops handed to the layout (cytoplasm uses its working-area top)."""
    return (_BAND_TOP[0], _BAND_TOP[1], _CYTO_TOP, _BAND_TOP[3], _BAND_TOP[4])


def _depth_ranks(nodes, edges: tuple[TopoEdge, ...]) -> dict[str, int]:
    """Longest-path depth from the sources, for spreading cytoplasm nodes down the cascade.
    Deterministic Kahn relaxation over the DAG (self-loops ignored)."""
    ids = [n.id for n in nodes]
    succ: dict[str, list[str]] = {i: [] for i in ids}
    indeg: dict[str, int] = {i: 0 for i in ids}
    for e in edges:
        if e.src == e.dst or e.src not in indeg or e.dst not in indeg:
            continue
        succ[e.src].append(e.dst)
        indeg[e.dst] += 1
    depth = {i: 0 for i in ids}
    queue = [i for i in ids if indeg[i] == 0]     # declaration order → deterministic
    while queue:
        cur = queue.pop(0)
        for nxt in succ[cur]:
            if depth[cur] + 1 > depth[nxt]:
                depth[nxt] = depth[cur] + 1
            indeg[nxt] -= 1
            if indeg[nxt] == 0:
                queue.append(nxt)
    return depth


def _positions(pw: CuratedPathway) -> dict[str, Placed]:
    """Final (x, y) per node: barycenter x from the layout, plus depth-based y-spreading inside the
    cytoplasm so the cascade descends. Other bands keep their band centre."""
    placed = layout(pw.nodes, pw.edges, _W, _band_tops_for_layout(), _BAND_H)
    depth = _depth_ranks(pw.nodes, pw.edges)
    cyto = [p for p in placed if COMPARTMENTS[p.band] == "cytoplasm"]
    # Spread by DENSE RANK among the distinct depths present (not raw depth), so every occupied row
    # is evenly spaced and adjacent rows never collide regardless of gaps in the depth numbering.
    depths_present = sorted({depth[p.node.id] for p in cyto})
    rank = {d: i for i, d in enumerate(depths_present)}
    n_rows = max(len(depths_present), 1)
    usable = _CYTO_BOT - _CYTO_TOP - 24
    row_h = max(_ROW_GAP, usable / n_rows) if n_rows > 1 else usable
    out: dict[str, Placed] = {}
    for p in placed:
        if COMPARTMENTS[p.band] == "cytoplasm":
            y = _CYTO_TOP + 16 + rank[depth[p.node.id]] * row_h
            out[p.node.id] = Placed(node=p.node, band=p.band, x=p.x, y=y)
        else:
            out[p.node.id] = p
    return out


def _boundary_point(x0, y0, x1, y1, hw, hh) -> tuple[float, float]:
    """Where the segment (x0,y0)->(x1,y1) exits the source pill's box (half-width hw, half-height hh),
    so an edge starts/ends at the pill's edge, not its centre."""
    dx, dy = x1 - x0, y1 - y0
    if dx == 0 and dy == 0:
        return x0, y0
    scale = min(
        hw / abs(dx) if dx else math.inf,
        hh / abs(dy) if dy else math.inf,
    )
    return x0 + dx * scale, y0 + dy * scale


def _arrowhead(x, y, ang, colour, open_tip=False) -> str:
    """A small triangle at (x,y) pointing along `ang` (radians). Open-tip → hollow (production)."""
    size = 7.5
    bx, by = x - size * math.cos(ang), y - size * math.sin(ang)
    perp = ang + math.pi / 2
    w = 4.0
    p1 = (bx + w * math.cos(perp), by + w * math.sin(perp))
    p2 = (bx - w * math.cos(perp), by - w * math.sin(perp))
    fill = "none" if open_tip else colour
    return (f'<path d="M{x:.1f} {y:.1f} L{p1[0]:.1f} {p1[1]:.1f} L{p2[0]:.1f} {p2[1]:.1f} Z" '
            f'fill="{fill}" stroke="{colour}" stroke-width="1.2"/>')


def _inhibition_bar(x, y, ang, colour) -> str:
    """A short perpendicular bar at (x,y) — the ⊣ inhibition head."""
    perp = ang + math.pi / 2
    w = 6.5
    x1, y1 = x + w * math.cos(perp), y + w * math.sin(perp)
    x2, y2 = x - w * math.cos(perp), y - w * math.sin(perp)
    return f'<path d="M{x1:.1f} {y1:.1f} L{x2:.1f} {y2:.1f}" stroke="{colour}" stroke-width="2.6"/>'


def _edge_svg(e: TopoEdge, pos: dict[str, Placed]) -> str:
    """One directed, typed edge as an SVG path + head. Self-loops (translocation marker) draw a small
    curved arrow beside the node to read as 'moves in place'."""
    src, dst = pos.get(e.src), pos.get(e.dst)
    if src is None or dst is None:
        return ""
    ink = _EDGE_INK.get(e.type, _EDGE_INK["activation"])
    hw, hh = _PILL_W / 2 + 2, _PILL_H / 2 + 2

    if e.src == e.dst:   # translocation self-marker — a little loop to the node's right
        cx, cy = src.x + hw, src.y
        loop = (f'<path d="M{cx:.1f} {cy - 8:.1f} C {cx + 26:.1f} {cy - 16:.1f}, '
                f'{cx + 26:.1f} {cy + 16:.1f}, {cx:.1f} {cy + 8:.1f}" fill="none" '
                f'stroke="{ink}" stroke-width="1.5" stroke-dasharray="5 3"/>')
        head = _arrowhead(cx, cy + 8, math.radians(135), ink)
        return loop + head

    # Leave the SOURCE from its bottom edge and enter the DESTINATION at its top edge, both vertically
    # — so every edge in the downward cascade meets its pills head-on and the arrowhead lands
    # perpendicular to the node it enters (the CST idiom). Horizontal offset between the two pills is
    # absorbed by an S-curve whose control points stay vertical at each end, never a diagonal kink.
    down = dst.y >= src.y
    y0 = src.y + (hh if down else -hh)
    y1 = dst.y - (hh if down else -hh)
    x0, x1 = src.x, dst.x
    span = abs(y1 - y0)
    off = max(16.0, min(span * 0.55, 70.0)) * (1 if down else -1)
    c1x, c1y = x0, y0 + off
    c2x, c2y = x1, y1 - off
    dash = ' stroke-dasharray="5 3"' if e.type == "translocation" else ""
    path = (f'<path d="M{x0:.1f} {y0:.1f} C {c1x:.1f} {c1y:.1f}, {c2x:.1f} {c2y:.1f}, '
            f'{x1:.1f} {y1:.1f}" fill="none" stroke="{ink}" stroke-width="1.6"{dash}/>')
    # the curve arrives vertically (c2 shares the destination's x), so the head points straight down
    # (or up) into the pill's edge — perpendicular, never at a diagonal.
    head_ang = math.pi / 2 if down else -math.pi / 2
    if e.type == "inhibition":
        head = _inhibition_bar(x1, y1, head_ang, ink)
    elif e.type == "production":
        head = _arrowhead(x1, y1, head_ang, ink, open_tip=True)
    else:
        head = _arrowhead(x1, y1, head_ang, ink)
    return path + head


def _pill_svg(p: Placed, *, focal: bool, focal_colour: str, status: str | None) -> str:
    """One node pill. `status` ∈ {'hit', 'context', None}: a hit is solid, a context node is faded
    (real biology, not a hit in these screens). The focal node gets a verdict-coloured ring."""
    fill, border, label_ink = _style(p.node.family)
    label = p.node.label or p.node.id
    faded = status == "context"
    opacity = "0.3" if faded else "1"
    x, y = p.x - _PILL_W / 2, p.y - _PILL_H / 2
    parts = [
        f'<rect x="{x:.1f}" y="{y:.1f}" width="{_PILL_W}" height="{_PILL_H}" rx="13" '
        f'fill="{fill}" stroke="{border}" stroke-width="1.3" opacity="{opacity}"/>',
    ]
    if focal:
        parts.append(
            f'<rect x="{x - 4:.1f}" y="{y - 4:.1f}" width="{_PILL_W + 8}" height="{_PILL_H + 8}" '
            f'rx="16" fill="none" stroke="{focal_colour}" stroke-width="2.6"/>')
    fs = 11 if len(label) <= 9 else 9.5
    parts.append(
        f'<text x="{p.x:.1f}" y="{p.y + 3.5:.1f}" text-anchor="middle" font-size="{fs}" '
        f'fill="{label_ink}" font-weight="600" opacity="{opacity}">{label}</text>')
    return "".join(parts)


def _membrane_svg() -> str:
    """A lipid bilayer across the membrane band: two rows of phospholipid 'lollipops' (head circle +
    two tails) meeting in the middle. Self-contained; no external image."""
    top = _BAND_TOP[1]
    mid = top + _BAND_H / 2
    tint = _BAND_TINT["membrane"]
    parts = [f'<rect x="0" y="{top}" width="{_W}" height="{_BAND_H}" fill="{tint}"/>']
    step = 12
    for xi in range(8, _W - 8, step):
        if 300 < xi < 400:      # skip under the receptor footprint so it parts the bilayer
            continue
        parts.append(f'<circle cx="{xi}" cy="{top + 7:.0f}" r="3.4" fill="{_MEM_HEAD}" '
                     f'stroke="{_MEM_TAIL}" stroke-width="0.8"/>')
        parts.append(f'<path d="M{xi - 2} {top + 10:.0f} L{xi - 1} {mid:.0f} M{xi + 2} {top + 10:.0f} '
                     f'L{xi + 1} {mid:.0f}" stroke="{_MEM_TAIL}" stroke-width="1"/>')
        parts.append(f'<circle cx="{xi}" cy="{top + _BAND_H - 7:.0f}" r="3.4" fill="{_MEM_HEAD}" '
                     f'stroke="{_MEM_TAIL}" stroke-width="0.8"/>')
        parts.append(f'<path d="M{xi - 2} {top + _BAND_H - 10:.0f} L{xi - 1} {mid:.0f} '
                     f'M{xi + 2} {top + _BAND_H - 10:.0f} L{xi + 1} {mid:.0f}" '
                     f'stroke="{_MEM_TAIL}" stroke-width="1"/>')
    return "".join(parts)


def _bands_svg() -> str:
    """Tinted strips behind the nodes for extracellular / cytoplasm / nucleus / output, each with a
    faint right-aligned label so a reader knows which compartment they're looking at."""
    parts = []
    strips = [
        ("extracellular", 0, _BAND_TOP[1]),
        ("cytoplasm", _BAND_TOP[1] + _BAND_H, _BAND_TOP[3]),
        ("nucleus", _BAND_TOP[3], _BAND_TOP[4]),
        ("output", _BAND_TOP[4], _H),
    ]
    for name, y0, y1 in strips:
        parts.append(f'<rect x="0" y="{y0}" width="{_W}" height="{y1 - y0}" '
                     f'fill="{_BAND_TINT[name]}"/>')
        parts.append(f'<text x="{_W - 12}" y="{y0 + 15}" text-anchor="end" font-size="9.5" '
                     f'fill="{_BAND_LABEL}" font-family="monospace" '
                     f'letter-spacing="0.5">{name.upper()}</text>')
    parts.append(f'<rect x="8" y="{_BAND_TOP[3] + 2}" width="{_W - 16}" '
                 f'height="{_BAND_TOP[4] - _BAND_TOP[3] - 4}" rx="14" fill="none" '
                 f'stroke="#C7CFD8" stroke-width="1.2"/>')
    return "".join(parts)


def _legend_svg() -> str:
    """A small key: edge glossary so the arrow grammar is self-explanatory. Quiet — thin border,
    monospace. A compact horizontal strip pinned to the bottom of the OUTPUT band, full clear of the
    cascade above it."""
    w = 366
    x, y = (_W - w) / 2, _H - 26
    rows = [
        ("activation", _EDGE_INK["activation"], "arrow"),
        ("inhibition", _EDGE_INK["inhibition"], "bar"),
        ("2nd-messenger", _EDGE_INK["production"], "open"),
        ("translocation", _EDGE_INK["translocation"], "dash"),
    ]
    parts = [f'<rect x="{x:.0f}" y="{y}" width="{w}" height="18" rx="5" fill="#FFFFFF" '
             f'fill-opacity="0.9" stroke="#C7CFD8" stroke-width="1"/>']
    for i, (name, ink, kind) in enumerate(rows):
        ry = y + 10
        rx = x + 10 + i * 92
        dash = ' stroke-dasharray="4 2"' if kind == "dash" else ""
        parts.append(f'<line x1="{rx}" y1="{ry}" x2="{rx + 16}" y2="{ry}" stroke="{ink}" '
                     f'stroke-width="1.6"{dash}/>')
        if kind == "bar":
            parts.append(f'<line x1="{rx + 16}" y1="{ry - 4}" x2="{rx + 16}" y2="{ry + 4}" '
                         f'stroke="{ink}" stroke-width="2.4"/>')
        elif kind == "open":
            parts.append(f'<circle cx="{rx + 16}" cy="{ry}" r="2.6" fill="none" stroke="{ink}" '
                         f'stroke-width="1.3"/>')
        else:
            parts.append(f'<path d="M{rx + 16} {ry} l-4 -2.5 v5 z" fill="{ink}"/>')
        parts.append(f'<text x="{rx + 21}" y="{ry + 3}" font-size="8.5" fill="{_FAINT}" '
                     f'font-family="monospace">{name}</text>')
    return "".join(parts)


def render_topology(
    pw: CuratedPathway,
    *,
    focal_gene: str,
    focal_colour: str,
    verdict_word: str,
    node_status: dict[str, str] | None = None,
) -> RenderResult:
    """Draw the whole CST-style figure. `node_status` maps a gene id → 'hit' | 'context' (a hit is
    solid, context is faded); absent genes render solid. Returns the SVG and the flat node/edge lists
    the honesty test pins."""
    status = {k.upper(): v for k, v in (node_status or {}).items()}
    focal = focal_gene.strip().upper()
    pos = _positions(pw)

    font = "'Inter','Helvetica Neue',Arial,sans-serif"
    parts: list[str] = [
        f'<svg viewBox="0 0 {_W} {_H}" xmlns="http://www.w3.org/2000/svg" font-family="{font}" '
        f'role="img" aria-label="Signalling-pathway map: {pw.term} around {focal}">',
        f'<rect x="0" y="0" width="{_W}" height="{_H}" rx="12" fill="{_PAPER}"/>',
    ]
    parts.append(_bands_svg())
    parts.append(_membrane_svg())

    parts.append(f'<text x="16" y="26" font-size="15" fill="{_INK}" font-weight="700">'
                 f'{focal} in {pw.term}</text>')
    parts.append(f'<text x="16" y="41" font-size="10.5" fill="{_FAINT}">'
                 f'signal flows down: membrane to cytoplasm to nucleus to output '
                 f'· focal node ringed ({verdict_word})</text>')

    drawn_edges: list[tuple[str, str, str]] = []
    for e in pw.edges:
        svg = _edge_svg(e, pos)
        if svg:
            parts.append(svg)
            drawn_edges.append((e.src, e.dst, e.type))

    drawn_ids: list[str] = []
    for n in pw.nodes:
        p = pos.get(n.id)
        if p is None:
            continue
        st = status.get(n.id)
        parts.append(_pill_svg(p, focal=(n.id == focal), focal_colour=focal_colour, status=st))
        drawn_ids.append(n.id)

    parts.append(_legend_svg())
    parts.append('</svg>')
    return RenderResult(svg="".join(parts), node_ids=tuple(drawn_ids), edges=tuple(drawn_edges))
