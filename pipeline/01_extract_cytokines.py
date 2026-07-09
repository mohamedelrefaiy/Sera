"""Phase 0 · step 01 — extract the per-cytokine mRNA effect table from the 16.8 GB h5ad.

Concord's mRNA side needs, for each (perturbed gene, condition), the effect of that
knockdown on each cytokine's mRNA. That is a handful of COLUMNS (the cytokines) of the
33,983 x 10,282 DE matrix, across the layers we care about. We do NOT need the whole file.

The h5ad is an HDF5 container. `var/gene_name` holds symbols (IL2 lives at a column index),
`obs/target_contrast_gene_name` + `obs/culture_condition` are categorical row labels. So:

    effect of KD(gene G) on cytokine C mRNA in condition cond
      = layers['zscore'][ row where (target==G, cond==cond), col where gene_name==C ]

This script slices only the cytokine columns across the requested layers, joins the obs
labels, applies the on-target self-perturbation exclusion (brief §3.4 hazard 1), and writes
a compact tidy parquet (a few MB). Output columns:

    gene, cytokine, condition, z_rna, q_rna, log_fc, p_value   (one row per gene×cytokine×cond)

Two source modes (auto-detected, override with --source):
  remote  slice columns over HTTPS range requests (no download; ~1 min/column).
  local   read from data/raw/GWCD4i.DE_stats.h5ad if 00_download.py --download was run (faster).

HDF5 is ROW-major, so a single column is scattered across the file. To keep remote reads
efficient we fetch the cytokine columns in ROW-BLOCKS (a contiguous byte span per block spans
all columns of those rows) rather than striding one column at a time.

Run:  python pipeline/01_extract_cytokines.py                 # remote, IL2 (+ config extras)
      python pipeline/01_extract_cytokines.py --source local  # from a downloaded copy
      python pipeline/01_extract_cytokines.py --cytokines IL2  # just the primary anchor (fastest)
"""
from __future__ import annotations

import argparse
import os
import time

import numpy as np
import pandas as pd
import yaml

_HERE = os.path.dirname(os.path.abspath(__file__))
_APP = os.path.dirname(_HERE)
_CONFIG = os.path.join(_APP, "config.yaml")

# Layers we extract. The verdict needs zscore + adj_p_value; log_fc/p_value are cheap to
# carry along and useful for the dossier. baseMean/lfcSE are omitted (not shown in the UI).
_LAYERS = ("zscore", "adj_p_value", "log_fc", "p_value")


def _cfg() -> dict:
    with open(_CONFIG) as fh:
        return yaml.safe_load(fh)


def _dec(arr) -> np.ndarray:
    """Decode an HDF5 string/bytes array to a numpy array of python str."""
    return np.array([x.decode() if isinstance(x, (bytes, bytearray)) else str(x) for x in arr])


def _categorical(obs_group, key: str) -> np.ndarray:
    """Read an AnnData categorical obs column (categories + codes) into a str array.

    AnnData stores categoricals as a group {categories, codes}; a plain string dataset
    is read directly. We handle both so the loader doesn't assume one encoding."""
    node = obs_group[key]
    if hasattr(node, "keys") and "categories" in node:      # categorical group
        cats = _dec(node["categories"][:])
        codes = node["codes"][:]
        return cats[codes]
    return _dec(node[:])                                     # plain string dataset


def _open(source: str, cfg: dict):
    """Return an open h5py.File over the remote URL (range requests) or a local copy."""
    import h5py
    if source == "local":
        path = os.path.join(_APP, "data", "raw", "GWCD4i.DE_stats.h5ad")
        if not os.path.exists(path):
            raise FileNotFoundError(
                f"{path} not found — run `python pipeline/00_download.py --download` first, "
                "or use --source remote.")
        return h5py.File(path, "r"), None
    # remote: fsspec gives h5py a range-capable file object over plain HTTPS.
    import fsspec
    url = cfg["data"]["h5ad_url"]
    fobj = fsspec.filesystem("https").open(url, block_size=8 * 1024 * 1024)
    return h5py.File(fobj, "r"), fobj


def _cytokine_columns(h5, symbol_col: str, wanted: list[str]) -> dict[str, int]:
    """Map each wanted cytokine symbol to its column index in var/gene_name.

    A cytokine absent from var is reported and skipped — we never fabricate a column."""
    symbols = _dec(h5["var"][symbol_col][:])
    index = {s: i for i, s in enumerate(symbols)}
    cols: dict[str, int] = {}
    for c in wanted:
        if c in index:
            cols[c] = index[c]
        else:
            print(f"  WARN cytokine {c} not found in var/{symbol_col} — skipped")
    return cols


def _slice_columns(h5, layer: str, col_idx: list[int]) -> np.ndarray:
    """Read ONLY the given columns of a layer. Returns (n_rows, n_cols) float array.

    h5py must receive the column list in ascending order (fancy indexing requirement); we
    read sorted and then reorder back to the caller's requested order. Reading only the
    wanted columns — not `[:, :]` — is what keeps this cheap: a whole-matrix read pulls the
    entire 16.8 GB (each row spans all 10,282 columns), which stalls over a remote file. On
    a LOCAL file h5py touches only the requested column slabs, so this is a seconds-scale read.

    NOTE: for many cytokines over a REMOTE (fsspec) file this is still I/O-heavy because HDF5
    is row-major; the intended remote-friendly path is to download once (00_download.py
    --download) and slice with --source local. See pipeline/README.md."""
    ds = h5["layers"][layer]
    order = np.argsort(col_idx)                    # ascending indices for h5py
    sorted_idx = list(np.array(col_idx)[order])
    sub = ds[:, sorted_idx]                        # (n_rows, n_cols) — only wanted columns
    inverse = np.argsort(order)                    # restore caller's column order
    return np.asarray(sub)[:, inverse]


def extract(source: str, cytokines: list[str], exclude_self: bool, progress=print) -> pd.DataFrame:
    """Build the tidy per-(gene, cytokine, condition) mRNA-effect table.

    Vectorized: each layer's cytokine block is melted with pandas (no per-row Python loop),
    then the four layers are concatenated column-wise on the shared (gene, cytokine, cond) key."""
    cfg = _cfg()
    d = cfg["data"]
    t0 = time.time()

    h5, fobj = _open(source, cfg)
    try:
        progress(f"[extract] source={source}  layers={_LAYERS}  cytokines={cytokines}")
        cols = _cytokine_columns(h5, d["h5ad_var_symbol_col"], cytokines)
        if not cols:
            raise RuntimeError("no requested cytokine columns found — nothing to extract")
        col_names = list(cols)               # preserves request order
        col_idx = [cols[c] for c in col_names]

        obs = h5["obs"]
        perturbed = _categorical(obs, d["h5ad_obs_gene_col"])         # (n_rows,)
        condition = _categorical(obs, d["h5ad_obs_condition_col"])    # (n_rows,)

        layer_data = {}
        for layer in _LAYERS:
            progress(f"  slicing layer {layer} ...")
            layer_data[layer] = _slice_columns(h5, layer, col_idx)    # (n_rows, n_cyto)
    finally:
        h5.close()
        if fobj is not None:
            fobj.close()

    n_rows = len(perturbed)
    out_name = {"zscore": "z_rna", "adj_p_value": "q_rna", "log_fc": "log_fc", "p_value": "p_value"}

    # Vectorized melt: build one long frame per layer keyed by (row_i, cytokine), then merge.
    row_idx = np.repeat(np.arange(n_rows), len(col_names))
    cyto_col = np.tile(np.array(col_names), n_rows)
    base = pd.DataFrame({
        "gene": perturbed[row_idx],
        "cytokine": cyto_col,
        "condition": condition[row_idx],
    })
    for layer, arr in layer_data.items():
        base[out_name[layer]] = arr.reshape(-1)   # C-order flatten matches (row, cyto) tiling

    if exclude_self:
        before = len(base)
        base = base[base["gene"] != base["cytokine"]].reset_index(drop=True)
        progress(f"  excluded {before - len(base)} self-perturbation rows (brief §3.4 hazard 1)")

    progress(f"[extract] {len(base):,} rows in {time.time() - t0:.1f}s")
    return base


def main() -> int:
    cfg = _cfg()
    default_cytokines = [cfg["cytokines"]["primary"], *cfg["cytokines"].get("experimental", []),
                         *cfg["cytokines"].get("extract_also", [])]

    ap = argparse.ArgumentParser(description="Extract per-cytokine mRNA effects from the h5ad.")
    ap.add_argument("--source", choices=["remote", "local"], default="remote")
    ap.add_argument("--cytokines", nargs="*", default=default_cytokines,
                    help="cytokine symbols to extract (default: config primary + experimental + extras)")
    ap.add_argument("--out", default=None, help="output parquet path (default: config artifacts dir)")
    args = ap.parse_args()

    exclude_self = bool(cfg["hazards"].get("exclude_self_perturbation", True))
    df = extract(args.source, args.cytokines, exclude_self)

    out = args.out or os.path.join(
        _APP, "target_triage", "data", "artifacts", "cytokine_mrna_effects.parquet")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    df.to_parquet(out, index=False)
    print(f"[write] {out}  ({len(df):,} rows, {df['cytokine'].nunique()} cytokines, "
          f"{df['condition'].nunique()} conditions)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
