# Target Triage — shortlist

Ranked druggable, immune-disease-linked regulators of CD4+ T-cell activation from the Marson Perturb-seq screen, each stress-tested by an adversarial verifier. **12 promoted, 3 rejected** out of the top 15 by actionable score — the shortlist is filtered by scrutiny, not merely sorted.

## 🔦 Demo spotlight — predicted blind, already in the clinic

These did not make the top 15 by raw actionable score, and the tool says so — but each is a druggable brake on T-cell activation. The trial status below is pulled **live from ClinicalTrials.gov** for the named inhibitor (Open Targets has no curated drug for these targets yet), so the "already in the clinic" beat is machine-verified, not asserted.

| Gene | Verdict | Actionable rank | Druggable | Live trial status (ClinicalTrials.gov) |
|---|---|---|---|---|
| [PTPN2](targets/PTPN2.md) | ✅ PROMOTE | #42 of 7195 | 0.50 | **Phase 1** · ABBV-CLS-484 · [NCT04777994](https://clinicaltrials.gov/study/NCT04777994) · recruiting |
| [CBLB](targets/CBLB.md) | ✅ PROMOTE — independently corroborated | #24 of 7195 | 0.33 | **Phase 1** · NX-1607 · [NCT05107674](https://clinicaltrials.gov/study/NCT05107674) · recruiting |

## Top 15 by actionable score

| # | Gene | Verdict | Druggable | Immune-disease | Top disease | Report |
|---|---|---|---|---|---|---|
| 1 | NRAS | ❌ REJECT | 0.50 | 0.81 | RAS-associated autoimmune leukoproliferative disease | NRAS |
| 2 | NFKB2 | ❌ REJECT | 0.33 | 0.88 | immunodeficiency, common variable, 10 | NFKB2 |
| 3 | STAT3 | ✅ PROMOTE — independently corroborated | 0.50 | 0.78 | autoimmune disease | [STAT3](targets/STAT3.md) |
| 4 | CSK | ✅ PROMOTE | 0.50 | 0.35 | systemic lupus erythematosus | [CSK](targets/CSK.md) |
| 5 | PTPRC | ✅ PROMOTE — independently corroborated | 0.33 | 0.91 | immunodeficiency 104 | [PTPRC](targets/PTPRC.md) |
| 6 | MALT1 | ✅ PROMOTE — independently corroborated | 0.50 | 0.84 | combined immunodeficiency due to MALT1 deficiency | [MALT1](targets/MALT1.md) |
| 7 | PSMB9 | ❌ REJECT | 0.67 | 0.76 | Immunodeficiency by defective expression of HLA class 1 | PSMB9 |
| 8 | AHR | ✅ PROMOTE — independently corroborated | 0.67 | 0.43 | ulcerative colitis | [AHR](targets/AHR.md) |
| 9 | RIPK1 | ✅ PROMOTE | 0.67 | 0.87 | immunodeficiency 57 | [RIPK1](targets/RIPK1.md) |
| 10 | PARK7 | ✅ PROMOTE | 0.33 | 0.69 | ulcerative colitis | [PARK7](targets/PARK7.md) |
| 11 | UBE2L3 | ✅ PROMOTE — independently corroborated | 0.17 | 0.45 | systemic lupus erythematosus | [UBE2L3](targets/UBE2L3.md) |
| 12 | STAT6 | ✅ PROMOTE | 0.50 | 0.74 | asthma | [STAT6](targets/STAT6.md) |
| 13 | PSMB8 | ✅ PROMOTE | 0.67 | 0.84 | proteosome-associated autoinflammatory syndrome | [PSMB8](targets/PSMB8.md) |
| 14 | RAC2 | ✅ PROMOTE — independently corroborated | 0.17 | 0.82 | immunodeficiency 73b with defective neutrophil chemotaxis and lymphopenia | [RAC2](targets/RAC2.md) |
| 15 | NCKAP1L | ✅ PROMOTE | 0.00 | 0.87 | immunodeficiency 72 with autoinflammation | [NCKAP1L](targets/NCKAP1L.md) |

> Rejections are shown, not hidden — a candidate is dropped when a **gate** check fails (e.g. NRAS: cross-donor correlation ≈ −0.1, a donor-driven artifact). That is the verifier having teeth.
