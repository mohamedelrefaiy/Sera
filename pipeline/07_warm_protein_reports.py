"""Phase 4 · step 07 — warm the PROTEIN MINI-REPORT caches for the demo genes.

The `protein_report` card assembles three retrievals per gene: protein identity + best structure
(UniProt / RCSB / AlphaFold), the structure's 3D coordinates (the PDB file the viewer loads), and a
cited "what we know" literature panel (PubMed + a Haiku summarise call). Identity/structure are a
fast cached lookup, but the literature panel is a live ~3s PubMed+LLM round-trip — too slow to run
in front of an audience. This script precomputes all three so a demo turn serves instant, cached,
deterministic panels.

Three caches, three degradation stories:

  IDENTITY + STRUCTURE   resolve_protein() -> clients/protein_cache.json. Needs the NETWORK only
                         (no API key). Warmed for every gene we can reach.
  STRUCTURE COORDINATES  the .pdb text the viewer renders -> data/artifacts/structure_cache/*.pdb.
                         Same fetch the /api/structure endpoint does, done ahead of time so the
                         viewer paints offline during the demo. Network only.
  LITERATURE             summarise_literature(use_cache=False) -> data/artifacts/literature_cache.json.
                         Needs Anthropic credentials. If absent, this step is SKIPPED (the card falls
                         back to a live call, or shows the honest 'ask again' note on replay) — a
                         keyless clone still warms identity + structure.

Every fact written is the SAME code-retrieved, unforgeable fact the live tool produces — warming is
pure acceleration, never a shortcut around the never-invent contract. A gene that fails one surface
still warms the others; a gene not in the screens is skipped with a note.

Run:  python pipeline/07_warm_protein_reports.py                     # default demo gene set
      python pipeline/07_warm_protein_reports.py --genes ITK TSC1 IL2RA
      python pipeline/07_warm_protein_reports.py --no-literature     # identity + structure only
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
_APP = os.path.dirname(_HERE)
if _APP not in sys.path:
    sys.path.insert(0, _APP)

from sera.clients.protein import ProteinRecord, resolve_protein  # noqa: E402
from sera.llm.literature import (  # noqa: E402
    LiteraturePanel, summarise_literature, write_cache)

# The demo gene set — the same genes annotated for the demo in core/explanation.py (chips + the
# example discordant / protein-only cases), so every gene a presenter is likely to click is warm.
DEFAULT_GENES: tuple[str, ...] = (
    "ITK", "BCL10", "VAV1", "TSC1", "LCP2", "VPS37B", "ZNF250", "IL2RA", "NFKB2")

_STRUCTURE_CACHE_DIR = os.path.join(_APP, "sera", "data", "artifacts", "structure_cache")


def _has_credentials() -> bool:
    """Mirror pipeline/04_explanations.py: an API key, a logged-in claude CLI, or stored creds."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        return True
    if os.path.exists(os.path.expanduser("~/.claude/.credentials.json")):
        return True
    import shutil
    return shutil.which("claude") is not None


def _warm_structure_coords(record: ProteinRecord, progress=print) -> bool:
    """Fetch + cache the coordinate file for a record's structure, exactly as /api/structure does.
    Returns True if coordinates are on disk afterward. Fixed RCSB/AlphaFold host; degrades to False
    (the card then shows its static reference) rather than raising."""
    st = record.structure
    if not st:
        return False
    os.makedirs(_STRUCTURE_CACHE_DIR, exist_ok=True)
    path = os.path.join(_STRUCTURE_CACHE_DIR, f"{st.source}_{st.identifier}.pdb")
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return True
    try:
        with urllib.request.urlopen(st.viewer_url, timeout=30) as r:  # noqa: S310 — fixed host
            if r.status != 200:
                return False
            text = r.read().decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        progress(f"      structure {st.identifier}: fetch failed ({e})")
        return False
    with open(path, "w") as fh:
        fh.write(text)
    return True


async def warm(genes: list[str], *, do_literature: bool, progress=print) -> dict:
    """Warm identity+structure (always) and literature (if enabled) for each gene. Returns a small
    per-gene status dict for the final summary."""
    # Use sera_tools._GENES — the SAME closed set the protein_report tool gates on, loaded at
    # import from the concordance parquet. NOT api.app._gene_set(): that reads _CONCORDANCE, which is
    # populated by a FastAPI startup event, so it is EMPTY in a standalone script (no server running).
    from sera.llm.sera_tools import _GENES as screen_genes
    panels: list[LiteraturePanel] = []
    status: dict[str, dict] = {}

    for gene in genes:
        g = gene.strip().upper()
        st: dict = {"in_screen": g in screen_genes, "identity": False,
                    "structure": False, "coords": False, "literature": None}
        if not st["in_screen"]:
            progress(f"  {g}: not in the screens — skipped")
            status[g] = st
            continue

        # identity + structure (network only; writes clients/protein_cache.json)
        record = resolve_protein(g)                          # cached write happens inside
        st["identity"] = bool(record.resolved and record.accession)
        st["structure"] = record.structure is not None
        acc = record.accession or "—"
        sid = record.structure.identifier if record.structure else "—"
        progress(f"  {g}: identity acc={acc} · structure={sid}")

        # structure coordinates (writes data/artifacts/structure_cache/*.pdb)
        if record.structure:
            st["coords"] = _warm_structure_coords(record, progress)
            progress(f"      coords {'cached' if st['coords'] else 'unavailable'}")

        # literature (needs credentials; forces a live call so it regenerates, then we cache it)
        if do_literature:
            try:
                panel = await summarise_literature(g, use_cache=False)
                st["literature"] = len(panel.findings) if panel.resolved else 0
                if panel.resolved and panel.findings:
                    panels.append(panel)
                progress(f"      literature: {st['literature']} cited finding(s)")
            except Exception as e:  # noqa: BLE001 — one gene's lit failure never sinks the run
                st["literature"] = None
                progress(f"      literature: FAILED ({e}) — card falls back to a live call")

        status[g] = st

    if panels:
        path = write_cache(panels)
        progress(f"\n[write] {path}  ({len(panels)} literature panels)")
    return status


def main() -> int:
    ap = argparse.ArgumentParser(description="Warm the protein mini-report caches for the demo.")
    ap.add_argument("--genes", nargs="*", default=list(DEFAULT_GENES))
    ap.add_argument("--no-literature", action="store_true",
                    help="warm identity + structure only (skip the credentialed literature step)")
    args = ap.parse_args()

    do_literature = not args.no_literature
    if do_literature and not _has_credentials():
        print("[warm] no Anthropic credentials — warming identity + structure only. "
              "The literature panel will fall back to a live call at demo time. Set "
              "ANTHROPIC_API_KEY or log in with the claude CLI to precompute literature.")
        do_literature = False

    print(f"WARMING PROTEIN MINI-REPORT CACHES — {len(args.genes)} demo genes"
          f"{' (identity + structure only)' if not do_literature else ''}\n")
    status = asyncio.run(warm([g.upper() for g in args.genes], do_literature=do_literature))

    # summary — a presenter can see at a glance which genes are demo-ready.
    print("\nSUMMARY")
    width = max((len(g) for g in status), default=4)
    ready = 0
    for g, s in status.items():
        if not s["in_screen"]:
            print(f"  [SKIP] {g:<{width}} not in the screens")
            continue
        lit = "n/a" if s["literature"] is None else f"{s['literature']} cited"
        ident_ok = s["identity"] and s["structure"] and s["coords"]
        lit_ok = (not do_literature) or (s["literature"] is not None and s["literature"] > 0)
        mark = "READY" if (ident_ok and lit_ok) else "PARTIAL"
        if mark == "READY":
            ready += 1
        print(f"  [{mark:<7}] {g:<{width}} identity={'y' if s['identity'] else 'n'} "
              f"structure={'y' if s['structure'] else 'n'} coords={'y' if s['coords'] else 'n'} "
              f"literature={lit}")
    n = sum(1 for s in status.values() if s["in_screen"])
    print(f"\n{ready}/{n} demo genes fully warm.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
