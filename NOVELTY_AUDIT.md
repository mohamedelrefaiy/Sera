# Novelty Audit — Zhu et al. 2025 genome-scale CD4+ T cell Perturb-seq

**Question.** For four proposed hackathon analyses, does the output constitute new analytical work, or would we be re-serving a conclusion the authors already computed and released?

**Date:** 2026-07-09
**Auditor:** due-diligence pass, adversarial posture.
**Artifacts examined:** `emdann/GWT_perturbseq_analysis_2025` @ `master` (cloned, all 223 files); `s3://genome-scale-tcell-perturb-seq/marson2025_data/` (bucket listed over HTTP; `GWCD4i.DE_stats.h5ad`, 16,786,240,107 bytes, read remotely via HTTP range requests — **not** worked around with CSVs).

**Two corrections to the brief's premises, established below:** the release *does* contain the Schmidt & Steinhart 2022 screen (`metadata/SchmidtSteinhart2022_CRISPRi_screen_gene_phenotypes.csv`) and *does* already correlate it against the perturb-seq data; and the release *does* already implement connectivity-map-style external-signature scoring (`Perturb2StateModel`, ElasticNetCV). Both facts cut against the candidates they were assumed to support.

---

## Verdicts

| # | Candidate | Verdict | One-line basis |
|---|---|---|---|
| 1 | NL query: "regulators of cytokine X in condition Y" | **A — REPACKAGING** | Produced verbatim by `get_DE_results_long()` + `nlargest('zscore')` at the authors' own FDR<0.01, over all 34 cytokines × 3 conditions. |
| 2 | Inverse target design (rank KDs matching a phenotype goal) | **C — NEW WORK** (narrowly; and see caveat) | Composite ranking exists in no released table (max \|ρ\|=0.11 vs. every column of `DE_stats.suppl_table.csv`) and is near-orthogonal to the authors' own polarization model (ρ=−0.076). |
| 3 | Signature reversal / connectivity map | **A — REPACKAGING** | The paper *is* a connectivity-map paper. `Perturb2StateModel.fit(X=(genes×perts), y=external_signature)` → signed ElasticNetCV coefficients; 3,994 + 5,449 ranked regulators released. |
| 4 | Cross-dataset concordance vs. Schmidt & Steinhart 2022 | **C — NEW WORK** (but *not* for the brief's stated reason) | Schmidt is in the repo and is already correlated in `FACS_comparison_full.ipynb`; what is absent is the **per-gene** replicate/context-specific call. That call has content: aggregate ρ=−0.0099 (p=0.41). |

**Net: two of four are repackaging.** Candidates #1 and #3 re-serve released conclusions. Do not build them as headline features.

---

## Evidence

### The shape of the release (this drives everything)

`DE_stats.suppl_table.csv` is **not** the effect matrix. It has 33,983 rows (one per perturbation×condition) and **19 columns, none of which is a gene**:

```
index, target_contrast_gene_name, culture_condition, target_contrast, chunk,
n_cells_target, n_up_genes, n_down_genes, n_total_de_genes, ontarget_effect_size,
ontarget_significant, target_baseMean, offtarget_flag, n_total_genes_category,
ontarget_effect_category, n_downstream, crossdonor_correlation_mean,
crossdonor_correlation_min, crossguide_correlation
```

It is a per-perturbation *report card*. The per-gene effects live only in `GWCD4i.DE_stats.h5ad` (S3, 16.8 GB), verified by direct read:

```
layers/: adj_p_value, baseMean, lfcSE, log_fc, p_value, zscore
  each: shape=(33983, 10282) dtype=float64 chunks=None compression=None
var/:   gene_ids, gene_name          (10,282 genes)
varm/:  measured_genes_stats_{Rest,Stim8hr,Stim48hr}
obs:    28 columns — a SUPERSET of the CSV (adds donor_correlation_hits_mean,
        guide_correlation_signif, neighboring_gene_KD, low_target_gex, ...)
conditions: Rest 11,287 | Stim8hr 11,415 | Stim48hr 11,281
```

Because the layers are **contiguous and uncompressed**, element `(i,j)` sits at `offset + (i*10282 + j)*8`. I pulled the IFNG/IL4/IL2/IL13 columns across `zscore`/`log_fc`/`adj_p_value` with 1,593 coalesced HTTP range requests (~8.4 GB transferred, 11m46s). **All numbers below are from the real matrix.**

Method, for the record (`3_DE_analysis/`): PyDESeq2 via pertpy, design `~ log10_n_cells + donor_id + target`, contrast = each target vs. `NTC`, **no LFC shrinkage**, FDR computed per-contrast (not global), `zscore = log_fc / lfcSE` clipped at +50 (one-sided — a real asymmetry). Note `merge_DE_results.py` writes `CD4i_final.merged_DE_results.h5ad`; **no script in the repo emits the published `GWCD4i.DE_stats.h5ad`**, and every notebook loads from `/mnt/oak/users/emma/...`. The repo is therefore not runnable end-to-end as released.

---

### Candidate #1 — NL query for cytokine regulators → **A, REPACKAGING**

The recipe is released. `src/5_cytokine_regulators/cytokine_regulators_overview.ipynb`, cell 14:

```python
cytokines_de_df = get_DE_results_long(adata_de, genes=cytokines,
                                      effect_estimates=['zscore','log_fc','baseMean'])
cytokines_de_df = cytokines_de_df[
    cytokines_de_df['gene_name'] != cytokines_de_df['target_contrast_gene_name']]  # drop on-target
unique_regulators = cytokines_de_df[cytokines_de_df['adj_p_value'] < 0.01]['target_contrast_gene_name'].unique()
```

with `get_DE_results_long` (`DE_analysis_utils.py`) supplying `significant = adj_p_value < signif_alpha`, and cell 26 looping `nlargest(n_top,'zscore')` / `nsmallest` **per cytokine, per condition**. Coverage is all **34 cytokines** (`immune_effector_genes.csv`, `Category=='Cytokine'`, minus a hardcoded `exclude=['IL17F','IL6']`) × **3 conditions**.

I ran their recipe verbatim on the released h5ad. "Regulators of IFNG in Stim48hr", FDR<0.01, on-target excluded → **59 regulators**:

| KD raises IFNG | z | KD lowers IFNG (required for IFNG) | z |
|---|---|---|---|
| ATP2A2 | +7.59 | **TBX21** | **−7.13** |
| IL4R | +7.07 | STAT5B | −6.31 |
| MAP1LC3B | +6.77 | TWNK | −5.34 |
| GPR183 | +6.74 | PRKAR1A | −5.09 |
| RIDA | +6.55 | MRPL39 | −4.98 |

`TBX21` (T-bet, the master Th1 transcription factor) is the top negative regulator, with `STAT5B`/`STAT3` behind it. The pipeline is correct — which is exactly why the null findings elsewhere in this audit can be trusted.

**Why A and not C.** It is true that no released CSV holds this table: zero CSV headers name a cytokine, and `src/5_cytokine_regulators/results/` **does not exist** in the release (the notebook writes only figures; `cytokines_de_df` is discarded). But bucket A has two clauses, and this satisfies the second: *produced verbatim by a released script*, applied to a released artifact, at the authors' own threshold. Reproducing it took ~20 lines and no methodological choice of ours. **The value added is access, not knowledge.** An LLM front-end over a released `nlargest` is a nicer interface to their conclusion.

---

### Candidate #2 — Inverse target design → **C, NEW WORK** (narrow)

Worked example, as specified. **Goal: more IFNG, less IL4** (Th1 skew), condition **Stim48hr**. Quality gate uses the paper's own `.obs` flags: `ontarget_significant`, drop `distal_offtarget_flag`, `n_cells_target ≥ 100` → 11,281 → **6,918** perturbations. Score = `(z_IFNG − z_IL4)/√2`.

**Top 15 knockdowns:**

| rank | KD | score | z_IFNG | z_IL4 | q_IFNG | q_IL4 | n_downstream |
|---|---|---|---|---|---|---|---|
| 1 | **IL4R** | 7.856 | 7.071 | −4.039 | 1.6e-09 | 3.5e-03 | 728 |
| 2 | CPSF6 | 6.836 | 5.573 | −4.094 | 2.8e-06 | 1.3e-03 | 1389 |
| 3 | NSG1 | 5.553 | 4.466 | −3.387 | 1.9e-02 | NaN | 23 |
| 4 | ZBTB45 | 5.249 | 4.496 | −2.927 | 6.7e-03 | 0.639 | 25 |
| 5 | IFI27L1 | 5.116 | 3.890 | −3.345 | 4.5e-02 | 0.106 | 42 |
| 6 | ATP2A2 | 5.103 | 7.585 | +0.368 | 4.7e-12 | 0.864 | 1565 |
| 7 | DNAJC24 | 5.042 | 2.594 | −4.537 | 0.117 | 1.1e-03 | 463 |
| 8 | LYPLA1 | 4.918 | 5.163 | −1.792 | 1.0e-03 | 0.965 | 11 |
| 9 | *IL4* | 4.758 | 1.078 | −5.651 | 1.000 | 1.0e-04 | 2 |
| 10 | SUPT20H | 4.747 | 1.779 | −4.935 | 0.192 | 1.6e-05 | 3311 |

Bottom (Th2 skew): `CNOT11` (−4.48), `RNF115` (−4.21), **`TRAF6`** (−3.95, z_IFNG=−4.32 — TRAF6 is required for NF-κB-driven IFNG; correct sign).

Two controls fall out unprompted: **`IL4R` ranks #1** (block the IL-4 receptor → cells cannot receive Th2 signal → IFNG up, IL4 down: the canonical Th1/Th2 antagonism), and **`IL4` itself ranks #9** driven entirely by its own on-target knockdown (`z_IL4=−5.65`) — a positive control for guide efficacy, *not* a regulator. Any product must strip on-target effects, exactly as the authors' `gene_name != target_contrast_gene_name` filter does.

**Is this ranking already obtainable from a released table?** No.

Spearman ρ of our score against **every** numeric column of `DE_stats.suppl_table.csv`, on the identical filtered Stim48hr set (n=6,918):

| column | ρ | p |
|---|---|---|
| n_total_de_genes | +0.1091 | 9.2e-20 |
| n_up_genes | +0.1100 | 4.6e-20 |
| n_down_genes | +0.0972 | 5.4e-16 |
| n_downstream | +0.1091 | 9.2e-20 |
| ontarget_effect_size | +0.0183 | 0.13 |
| crossdonor_correlation_mean | −0.0771 | 0.0089 |
| target_baseMean | +0.0179 | 0.14 |

Max \|ρ\| = **0.11**. The composite is not a relabelling of any released summary statistic.

**Counter-test against the authors' own model.** They released `polarization_prediction_condition_comparison_regulator_coefficients.csv`. Comparing our Th1-skew score to their `coef_mean` for `dataset_key = ota_Stim48hr` (2,517 shared genes):

```
Spearman = -0.0760   p = 1.4e-04   n = 2517
```

Sign is as predicted (their signature is Th2-vs-Th1, so Th1-ward = negative coefficient) and it is significant — but it explains **0.6% of variance**. Per-gene, they diverge sharply: `IL4R` sits at their `coef_rank=0.991` (they find it), yet `IFI27L1` (our #5) sits at `coef_rank=0.027` (they rank it oppositely). The two quantities genuinely differ: **their coefficient asks "does this KD shift the whole 10,282-gene Th2/Th1 program?"; ours asks "does this KD raise IFNG and lower IL4 transcript?"** A gene can do one without the other.

**Verdict C, with the caveat stated plainly.** The *inputs* are fully released — the score is a two-column slice of `layers['zscore']` plus a subtraction, with no new estimation. What does not exist anywhere in the release is a table where an arbitrary user-specified phenotype vector is scored. Novelty here rests on **user-supplied goal weights defining the composite**, not on new statistics. That is a thinner claim than "new knowledge," and it is only defensible because ρ=−0.076 demonstrates the result is not the polarization table in disguise. **If the product hardcodes IFNG-up/IL4-down, it collapses toward A** — it becomes one more precomputable column. The novelty is the *arbitrary axis*, and it is unvalidated: nothing in the paper certifies that this composite predicts a cellular phenotype.

---

### Candidate #3 — Signature reversal / connectivity map → **A, REPACKAGING**

**The brief's premise is wrong.** It classified #3 as C because it "requires an input the release does not contain (a user-supplied external signature)." The release contains the machinery that consumes exactly such an input, and demonstrates it twice.

`environment.yaml:14` pulls `git+https://github.com/emdann/pert2state_model.git@main`. Both `4_polarization_signatures/pert2state_polarization.ipynb` and `7_1k1k_analysis/1k1k_pert2state_model.ipynb` call it identically:

```python
y_target   = signature[perturb_layer].copy()          # per-gene zscore of an EXTERNAL DE signature
X_perturbs = sc.get.obs_df(input_adata_de, [...], layer='zscore').T   # (n_genes, n_perturbations)
X_perturbs_masked = mask_cis_effects(X_perturbs)
p2t_model = Perturb2StateModel(pca_transform=True, n_pcs=n_pcs, positive=False)
p2t_model.fit(X_perturbs_masked, y_target)
```

whose docstring states: `X: Shape (n_genes, n_perturbations)`; `y: ... from the physiological/unknown condition. Shape (n_genes,)`. Internally: `StandardScaler` → `TruncatedSVD(60)` → `ElasticNetCV(l1_ratio=..., alphas=..., cv=4)`, with per-perturbation weights recovered as `coefs = np.matmul(pca.components_.T, mod.coef_)`.

That is connectivity-map scoring: an external per-gene signature as response, perturbation effect vectors as predictors, **signed** coefficients (`positive=False`) whose negative values mean *this knockdown opposes the signature*. The notebooks label the axis literally — `'Top negative regulators\nof aging signature'` vs `'Top positive regulators'`.

External signatures used: **Ota 2021** (Th2-vs-Th1) and **Hollbacher 2021** (replication); **Yazar 2022 / OneK1K** (CD4T aging); **Arce 2024** activation as negative control. Released outputs rank thousands of perturbations, not a curated shortlist:

- `polarization_prediction_condition_comparison_regulator_coefficients.csv` — 23,172 rows, **3,994 unique regulators**, 6 dataset keys (`ota_|activation_` × `Rest|Stim8hr|Stim48hr`)
- `aging_prediction_condition_comparison_regulator_coefficients.csv` — 10,763 rows, **5,449 unique regulators**
- columns: `coef_mean, coef_sem, coef_rank, regulator, known_regulators, dataset_key, regulator_type, celltype, signature`

Perturbations entering the model are selected by a data-driven filter (`n_total_de_genes >= 10`), not a hand-picked list.

**Verdict A, stated plainly.** The paper already does this. Swapping their `y_target` for a different DE vector is *using a released general-purpose function as intended* — it is not a different analysis. The only genuine gap is packaging: `Perturb2StateModel` lives in a separate repo, no fitted `.pkl` is shipped, input paths are hardcoded to `/mnt/oak/`, and the coefficients are necessarily signature-specific (they *are* the answer for one signature, so no "apply released model to my vector" path exists — a new fit is required each time). **That is an engineering convenience, not new knowledge.** Building it and calling it novel analysis would be a misrepresentation. It remains legitimate as *infrastructure*, provided it is described as such.

---

### Candidate #4 — Cross-dataset concordance vs. Schmidt & Steinhart 2022 → **C, NEW WORK** (right answer, wrong reason)

**The brief's premise is again wrong.** Schmidt is *not* a dataset the paper did not use. It ships in the repo:

- `metadata/SchmidtSteinhart2022_CRISPRi_screen_gene_phenotypes.csv` — 37,878 rows, MAGeCK output (`neg|lfc, neg|fdr, pos|lfc, pos|fdr, ...`), `phenotype ∈ {CD4+ IL2, CD8+ IFNG}`
- `metadata/Schmidt2022_hits_Supplementary_table_2.xlsx` — 132,529 rows: `Gene, CRISPRa_or_i, CD4_or_CD8, Cytokine, LFC, zscore, FDR, Hit, Hit_Type`
- also `Freimer2022_Screen.csv`, `Arce2025_Screen.csv`

and `src/3_DE_analysis/FACS_comparison_full.ipynb` **already correlates it**, with a matched-vs-mismatched bootstrap null:

```python
corr_all, p_value = scipy.stats.pearsonr(pl_df['lfc'], pl_df[target])
```

So the "join to an unused dataset" justification collapses. (Separately: `cytokine_regulators_overview.ipynb` loads `Schmidt_facs_df` and — confirmed by counting every occurrence of the variable — **never uses it**.)

**What is genuinely absent** is any *per-gene* verdict. `FACS_comparison_full.ipynb` writes **no CSV**; its outputs are figures plus an aggregate Pearson r. A repo-wide scan of every CSV header for `schmidt|crispra|replicat|concord|facs` returns **zero matches**. No `replicated` / `context_specific` column exists anywhere.

I built that call. Perturb-seq `z_IL2` (Stim48hr, on-target-significant) joined to Schmidt `CD4+ IL2`, 6,864 shared genes:

```
Spearman(perturbseq z_IL2, Schmidt CRISPRi IL2 lfc) = -0.0099   p = 0.414   n = 6864
```

Per-gene 2×2 at FDR<0.1 on both sides:

| | Schmidt hit | Schmidt not |
|---|---|---|
| **perturb-seq hit** | **24** | **141** |
| perturb-seq not | 192 | 6,507 |

- **Replicated (both):** METAP2, VPS8, ATP1B3, CPSF6, ELOF1, **ITK**, **BCL10**, **TSC1** — canonical TCR-signalling nodes, so the overlap is real biology.
- **Context-specific (perturb-seq only):** VPS37B, LETM2, BLOC1S2, ZIK1, ZNF277, SRC, MRC2, UCP2.

**Verdict C.** Not because the data was missing — it ships — but because the paper computed an *aggregate* correlation and never emitted the per-gene call. And that call carries real content **precisely because the aggregate correlation is ≈0**: mRNA abundance and secreted-protein FACS readout are measuring different biology (post-transcriptional control, translation, secretion). A per-gene replicate/context-specific table is not derivable from "ρ ≈ 0"; it is the thing ρ ≈ 0 conceals. This is the strongest novelty claim of the four, and it is an honest one.

---

## Recommendation

**Build #4. Build #2 only with an arbitrary user-specified axis. Do not headline #1 or #3.**

**#4 is the real work.** It is the only candidate whose output cannot be produced by any released script on any released artifact, and whose scientific content is *non-obvious* — the near-zero aggregate correlation (ρ=−0.0099, p=0.41) is itself the finding that makes a per-gene table necessary. It answers a question a bench immunologist actually asks ("my hit came from a FACS screen — does it hold at the transcript level, or is it context-specific?"), and it generalizes: Schmidt CD8+IFNG, Freimer IL2RA, and Arce2025 all ship in the same repo and none has a per-gene concordance table. The framing must be honest: **the paper did use Schmidt; we are refining an aggregate correlation into a per-gene call.**

**#2 is defensible but thin, and only in one form.** Its novelty lives entirely in letting the user define the phenotype axis. Evidence it is not a repackaging: max \|ρ\|=0.11 against every released summary column, and ρ=−0.076 against the authors' own polarization coefficients. Evidence it is not much: the score is a two-column slice and a subtraction. **Hardcode the axis and it degenerates to bucket A.** It is also unvalidated — nothing in the release certifies that a transcript-level IFNG↑/IL4↓ composite predicts a cell-level phenotype, and the ρ=−0.076 against their polarization model is a warning, not a reassurance. Ship it with on-target effects stripped, with `ontarget_significant`/`distal_offtarget_flag`/`n_cells_target` gating, and with that caveat visible.

**#1 is repackaging. Say so.** It is a released script (`get_DE_results_long` + `nlargest`) at a released threshold (FDR<0.01) over a released matrix. That no CSV happens to contain the output is an accident of what the authors chose to save, not evidence of new analysis. It is genuinely useful — the answer takes ~20 lines and a 16.8 GB download that most users will not do — so ship it as **convenience infrastructure**, an access layer, and label it that way. Do not claim it as a finding.

**#3 is repackaging, and the most dangerous of the four to mislabel.** The paper *is* a connectivity-map paper: ElasticNetCV, external signature as response, signed direction-interpretable coefficients, 3,994 + 5,449 regulators released. A "bring-your-own-signature" scorer is a packaging improvement over an external dependency with hardcoded `/mnt/oak/` paths. That has real utility. It is not new analytical work, and presenting it to judges as novel invites exactly the objection that any reviewer who opens `pert2state_polarization.ipynb` will raise.

### Two hazards the audit surfaced that any build must handle

1. **On-target contamination.** `IL4` ranked #9 in the Th1-skew list purely on its own knockdown. Every candidate that ranks perturbations by their effect on a gene must exclude `target == gene`. The authors do this; a naive implementation will not.
2. **Two parallel significance regimes.** The release carries both DESeq2 `adj_p_value < 0.1` and MASH `lfsr < 0.05`, and different released artifacts use different ones (`K562_comparison.suppl_table.csv` reports `n_degs_MASH_*`; the main analysis notebook comments MASH out). `zscore` is additionally clipped at +50 but **not** at −50. Pick one regime, state it, and do not mix numbers across artifacts that used the other.
