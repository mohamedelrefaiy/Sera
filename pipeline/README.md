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

### 02 — build the concordance verdicts
```bash
python pipeline/02_build_concordance.py
```
Crosses the mRNA parquet with the Schmidt protein screen → `concordance.parquet` (the 2×2(+1)
verdict per gene × condition). Prints the canonical-gene check (ITK/BCL10/TSC1).

### 03 — enrich the dossier cards
```bash
python pipeline/03_enrich.py
```
Per-gene quality QC + druggability (SM + antibody, Open Targets) + disease → `enrichment.json`.
Re-fetches Open Targets for genes whose cache entry predates the antibody modality.

### 04 — precompute grounded explanations (needs Anthropic credentials)
```bash
python pipeline/04_explanations.py
```
Cached Claude (Haiku 4.5) "why this verdict" for the demo genes → `explanations_cache.json`.
No-ops cleanly without credentials (the frontend template fallback covers every gene).

### 05 — build the ground-truth panel
```bash
python pipeline/05_ground_truth.py
```
How Concord recovers canonical IL-2 regulators + the aggregate Spearman → `ground_truth.json`.

### 07 — warm the protein mini-report caches (structure needs network; literature needs credentials)
```bash
python pipeline/07_warm_protein_reports.py                  # all three caches, demo gene set
python pipeline/07_warm_protein_reports.py --no-literature  # identity + structure only (keyless)
```
Precomputes the `protein_report` card's three retrievals for the demo genes so a demo turn is
instant: identity + best structure → `clients/protein_cache.json`, the 3D coordinate files →
`data/artifacts/structure_cache/`, and the cited literature panel → `literature_cache.json`.
Prints a per-gene READY/PARTIAL summary. Warms identity + structure without credentials (network
only); the literature step no-ops cleanly without them (the card falls back to a live call).

**Full rebuild:** `00 → 01 → 02 → 03 → 04 → 05 → 07`. Steps 02–07 need only the parquet from 01, so
a keyless offline build (skipping 04 and the literature half of 07) still produces a working app.

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
