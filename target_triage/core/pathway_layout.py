"""Deterministic banded layout for a small curated signalling graph.

The compartment gives each node its band (the y-axis); this module solves only the x-ordering within
each band so a downward-flowing cascade reads cleanly with few edge crossings. It is a constrained,
pure-Python Sugiyama: bands are the fixed layers, and a fixed number of barycenter sweeps orders
nodes left-to-right by the average slot of their already-placed neighbours, with the node's
declaration index as a deterministic tie-break. No graphviz, no randomness, no convergence test — the
same input yields byte-identical coordinates, so the golden SVG test stays stable.

Pure and self-contained: it takes the curated nodes/edges and canvas geometry, returns positioned
nodes. It never invents a node or edge; it only places what the curated topology already asserts.
"""
from __future__ import annotations

from dataclasses import dataclass

from .pathway_topology import COMPARTMENTS, TopoEdge, TopoNode

_SWEEPS = 4          # barycenter passes; enough for ≤20 nodes, fixed for determinism
_MARGIN_X = 70       # left/right gutter so end pills and labels never clip


@dataclass(frozen=True)
class Placed:
    """A curated node given a pixel position and its band index. Immutable; the renderer reads it."""
    node: TopoNode
    band: int          # index into COMPARTMENTS (0 = extracellular … 4 = output)
    x: float
    y: float


def _band_index(compartment: str) -> int:
    return COMPARTMENTS.index(compartment)


def _band_centres(band_tops: tuple[float, ...], band_h: float) -> tuple[float, ...]:
    """Vertical centre of each band, so a node sits in the middle of its compartment strip."""
    return tuple(t + band_h / 2 for t in band_tops)


def _initial_slots(nodes: tuple[TopoNode, ...]) -> dict[str, int]:
    """First-pass slot per node: its running index WITHIN its band, in declaration order. Declaration
    order in the curated pathway is therefore the layout's deterministic starting point — list the
    cascade proximal→distal and it reads top→bottom, left→right before any sweep runs."""
    per_band: dict[int, int] = {}
    slot: dict[str, int] = {}
    for n in nodes:
        b = _band_index(n.compartment)
        slot[n.id] = per_band.get(b, 0)
        per_band[b] = per_band.get(b, 0) + 1
    return slot


def _neighbours(edges: tuple[TopoEdge, ...]) -> dict[str, list[str]]:
    """Undirected adjacency (both endpoints), ignoring self-loops like a translocation marker — a
    self-edge carries no ordering information. Used to pull a node toward its neighbours' slots."""
    adj: dict[str, list[str]] = {}
    for e in edges:
        if e.src == e.dst:
            continue
        adj.setdefault(e.src, []).append(e.dst)
        adj.setdefault(e.dst, []).append(e.src)
    return adj


def _barycenter_order(
    nodes: tuple[TopoNode, ...], edges: tuple[TopoEdge, ...]
) -> dict[str, int]:
    """Order nodes within each band to reduce crossings. Fixed sweeps; each node's key is the mean
    slot of its neighbours (across all bands, since a small banded DAG mostly connects adjacent
    bands), tie-broken by declaration index so the result is deterministic."""
    decl_index = {n.id: i for i, n in enumerate(nodes)}
    band_of = {n.id: _band_index(n.compartment) for n in nodes}
    bands: dict[int, list[str]] = {}
    for n in nodes:
        bands.setdefault(band_of[n.id], []).append(n.id)

    slot = _initial_slots(nodes)
    adj = _neighbours(edges)

    for sweep in range(_SWEEPS):
        order = sorted(bands.keys())
        if sweep % 2 == 1:
            order = list(reversed(order))
        for b in order:
            members = bands[b]

            def key(nid: str) -> tuple[float, int]:
                nbrs = adj.get(nid, ())
                if nbrs:
                    bary = sum(slot[m] for m in nbrs) / len(nbrs)
                else:
                    bary = float(slot[nid])
                return (bary, decl_index[nid])

            members_sorted = sorted(members, key=key)
            bands[b] = members_sorted
            for i, nid in enumerate(members_sorted):
                slot[nid] = i
    return slot


def layout(
    nodes: tuple[TopoNode, ...],
    edges: tuple[TopoEdge, ...],
    width: float,
    band_tops: tuple[float, ...],
    band_h: float,
) -> tuple[Placed, ...]:
    """Position every curated node. `band_tops` gives the top y of each of the five compartment bands
    (same order as COMPARTMENTS); `band_h` is a band's height. x is spread evenly across the usable
    width by the node's within-band slot after barycenter ordering. Deterministic."""
    centres = _band_centres(band_tops, band_h)
    slot = _barycenter_order(nodes, edges)

    counts: dict[int, int] = {}
    for n in nodes:
        counts[_band_index(n.compartment)] = counts.get(_band_index(n.compartment), 0) + 1

    placed: list[Placed] = []
    usable = width - 2 * _MARGIN_X
    for n in nodes:
        b = _band_index(n.compartment)
        k = counts[b]
        s = slot[n.id]
        x = _MARGIN_X + (s + 0.5) * (usable / k) if k else width / 2
        y = centres[b]
        placed.append(Placed(node=n, band=b, x=x, y=y))
    return tuple(placed)
