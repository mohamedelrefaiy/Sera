# Concord pipeline — h5ad → cytokine-effect parquet

The **mRNA side** of Concord's concordance (the effect of each knockdown on a cytokine's
mRNA) lives in a 16.8 GB h5ad that is *not* bundled in the repo. These scripts fetch just
the cytokine columns and write a compact parquet the deterministic core consumes. Everything
here is **build-time only** — the served app never touches h5py/parquet on the request path.

## Setup (one time)

The heavy deps live in a **separate** optional group so the core app stays stdlib-light:

```bash
# from application/
uv pip install --python .venv/bin/python -e ".[pipeline]"
# or: pip install -e "application/[pipeline]"
```

## Steps

### 00 — verify the source is fetchable (fast, pulls a few hundred bytes)
```bash
python pipeline/00_download.py
```
Confirms the public S3 object responds (HTTP 200), reports its size, advertises
`Accept-Ranges: bytes`, and begins with the HDF5 magic signature. Prints `PASS`/`FAIL`.

Optional full local copy (only if you prefer local slicing over remote range requests):
```bash
python pipeline/00_download.py --download        # 16.8 GB → data/raw/
```

### 01 — extract the cytokine columns → parquet
```bash
python pipeline/01_extract_cytokines.py --cytokines IL2         # primary anchor, fastest
python pipeline/01_extract_cytokines.py                          # IL2 + config extras
python pipeline/01_extract_cytokines.py --source local          # from a downloaded copy
```
Writes `target_triage/data/artifacts/cytokine_mrna_effects.parquet` with one row per
(gene × cytokine × condition): `gene, cytokine, condition, z_rna, q_rna, log_fc, p_value`.
On-target self-perturbation rows (`gene == cytokine`) are excluded (brief §3.4 hazard 1).

## Notes / verified facts (2026-07-09)

- **The h5ad is range-capable**, so `01` slices columns over plain HTTPS without downloading
  16.8 GB. HDF5 is row-major, so the extractor reads **row-blocks** (contiguous byte spans),
  not strided single columns — a strided column read refetches scattered bytes and is far slower.
- Source object: `s3://genome-scale-tcell-perturb-seq/marson2025_data/GWCD4i.DE_stats.h5ad`
  (public, no-sign-request), served at the `https://…s3.amazonaws.com/…` URL in `config.yaml`.
- Structure: matrix is `(perturbation × condition) rows × (downstream gene) cols`, layers
  `zscore / adj_p_value / log_fc / p_value / lfcSE / baseMean`. Gene **symbols** are in
  `var/gene_name` (IL2 at col 2377); `var/_index` is Ensembl. Row labels:
  `obs/target_contrast_gene_name` + `obs/culture_condition` ∈ {Rest, Stim8hr, Stim48hr}.
- Spot-check that confirms the join is right: at IL2 / Stim48hr, `ITK z=−2.42`, `BCL10 z=−2.45`,
  `TSC1 z=−2.52` (all adj_p < 0.1) — the canonical TCR hits the brief expects to replicate.
  `LCP2` is mRNA-n.s. here (a genuine protein-only candidate); `IL2` self-KD is n.s. (excluded).

All thresholds, cytokine scope, and the source URL are in `application/config.yaml`.
