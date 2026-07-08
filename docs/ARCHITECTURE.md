# "Target Triage" — Marson Perturb-seq prototype architecture

**Locked dataset (2026-07-06, after 4-agent research):** Alex Marson genome-scale CD4+ T-cell Perturb-seq.
**Dual target:** a usable Builder tool whose output is a reproducible, wet-lab-testable finding
that could win Gladstone's $10k "advance the field" award.

> Pivoted from Corces ChromBPNet. Reason: the Corces lab already ships a hosted variant-effect app
> (vep.corces.gladstone.org, 22M precomputed scores) — building on it risks looking like a wrapper
> around the judges' own tool. Marson data is fresh, under-mined, causal, and GPU-free.

---

## THE REFRAMING — this is a general instrument, not a T-cell script (2026-07-07)

Read the whole document below through this lens: **the T-cell screen is the *proof domain*, not the
product.** What we actually built is a domain-agnostic method — `prioritize -> falsify -> corroborate`,
with an agent reasoning over computed facts and a positive-control gate proving it works — that happens
to be demonstrated on Marson Perturb-seq. Everything downstream of ingest is an instance of that method.

This is not aspirational. `src/target_triage/data.py` already maps ANY screen's columns onto immutable
internal records via a `ScreenSchema`, and `eval/test_reusable.py` PROVES the identical pipeline recovers
known biology on two structurally different screens — Marson (Perturb-seq; has effect-breadth, boolean
significance, 3 conditions) and Schmidt2022 (MAGeCK CRISPRi; no breadth column, FDR-derived significance).
That green two-screen control **is** the general-instrument claim, empirically, today.

**How to pitch it (and how NOT to).** Pitch the *mechanism*, never "point it at anything":

> A screen-agnostic instrument: add a schema and a positive-control eval; if the controls reproduce, the
> same falsify -> corroborate loop runs — and if they don't, it refuses to load.

That single sentence is simultaneously the credibility guarantee and the safety guarantee (it's our
controls-first discipline promoted into the headline). Avoid "a judgment layer for all of science" /
"point it at anything" — that is the Galaxy/Terra graveyard pitch (a substrate with no opinion), it starts
a breadth fight with general AI-co-scientist tools we can't win, and "anything" is exactly the phrase a
dual-use reviewer flags.

**The autonomous loop is the verifier loop iterated — there is no second loop to build.** The autonomous
mode is `verify()` run inside a *bounded* `for`, where the agent chooses the next surviving candidate to
try to falsify. The ordering is load-bearing for trust: every step's only power is to REJECT a candidate,
never to generate one, so the loop can only *shrink* the shortlist against fixed thresholds — it cannot
amplify into novel capability, because no step emits anything actionable. Demo-safe minimum: fixed N (never
`while`), seed from a non-obvious overlay-mover, "propose next experiment" = a threshold-stress `reverify`
(purely in-silico re-analysis), end on a printed `loop complete: N verified, K promoted, M rejected`.

### Target architecture (post-hackathon — the "Tuesday" refactor, NOT a Monday task)

A domain-agnostic **core** + per-domain **plugins**. Ship the *words* of this now (the code already proves
them); build the formal `Candidate`/`Protocol` refactor only after submission.

- **Core (never varies per domain):** the DTOs (`Candidate`, `Scored`, `Check`, `Verdict` +
  GATE/SCORE/BONUS taxonomy — already domain-agnostic in `verify.py`), the `prioritize -> falsify ->
  corroborate` orchestration, the controls-gate runner, the plugin loader, the view-update protocol, and
  the bounded autonomous loop.
- **A plugin supplies exactly four surfaces** (all pure, typed as `Protocol`s): (1) a **schema/loader**
  (`ScreenSchema` today), (2) **scorer(s)** (`ranking.py`), (3) **refutation checks** (the `check_*` in
  `verify.py`), (4) **held-out evidence source(s)** (`evidence.py` + Open Targets) — plus its **positive
  controls** (the IDs that MUST recover).
- **Hard invariant, enforced by the loader:** a plugin does not register unless its positive-control eval
  passes. Generality is allowed *only where a control can gate it*. T-cells is simply the first plugin.
- **The autonomous loop needs NO new plugin method.** "Propose next candidate" = pop the highest-scoring
  not-yet-tested survivor. Keep candidate-selection a *policy* the loop owns (default: greedy-by-score;
  agent may reorder), never a plugin hook — a `propose_next()` on the plugin would let a domain author
  smuggle heuristics *past* the verifier, destroying the exact trust property. Selection policy, never a
  validation bypass.

### Safety guardrails worth building into the core (proportionate — not security theater)

1. **Output-type boundary (the one that matters).** The pipeline output type is `Ranking | Verdict |
   Evidence` — numbers/records over rows — and nothing else. No plugin surface may return a sequence, an
   edit, a construct, or a synthesis spec. The `@dataclass(frozen=True)` return types already have exactly
   this shape; formalizing it keeps `prioritize -> falsify -> corroborate` definitionally on the *analysis*
   side of the analysis-vs-design line, no matter what plugin loads.
2. **Plugin allow-listing — don't regress what exists.** `schema.REGISTRY` is curated and `get_schema`
   already raises on unknown names. Plugins load from a vetted registry, never by arbitrary path or
   auto-discovery. This is the line between "an instrument with known domains" and "point it at anything."
3. **Human-in-the-loop seam (reserve, don't build).** Keep "propose experiment" a distinct output type from
   "verdict," so IF the 6-12mo "sim-execute the next experiment" vision ever lands, requiring sign-off is
   trivial. For now, "propose = threshold-stress reverify" keeps everything in-silico. Explicitly do NOT
   build sequence/hazard screening, synthesis interdiction, or approval-gating on read-only DB lookups —
   disproportionate for a defensive analysis tool, and it muddies the pitch.

### The honest open question (name it, don't paper over it)

Two screens proves the **schema mapping** generalizes; it does NOT prove the **refutation logic** does. Our
gates (`donor_robustness`, `cross_guide`, `cross_condition`) are Perturb-seq-shaped; Schmidt2022 is still
genomics reusing the same vocabulary. A genuinely different field (chemistry, structure, proteomics) needs
an *entirely different refutation catalog*, and nothing yet shows authoring one is cheap — the exact
assumption that sank the general-platform graveyard (framework easy, domain content hard). Cheapest test:
a tiny **non-biological toy plugin** (rank integers, GATE = is-prime, evidence = a lookup table). If the
core runs green on genes AND on integers, it's domain-agnostic by construction. That is the first
post-hackathon build — it de-risks the whole generalization roadmap for an afternoon's work.

> This section is a lens over the rest of the document. The detailed pipeline, upgrades, and risks below
> are all instances of the general method; the Marson-specific numbers remain the *proof*, not the scope.

---

## The core insight (what all research converged on)
Gladstone's real bottleneck is NOT prediction — it's the **funnel from genome-scale results down to
a short, trustworthy, wet-lab-testable shortlist.** Their AI lead (Theodoris): *"AI allows us to
prioritize the astronomical number of options and focus on the most promising ones to test."*
Meanwhile the recurring hackathon-WINNING pattern is the **verifier + adversarial agent loop.**
In a science context these are the SAME THING: an adversarial agent that stress-tests each candidate
is both the 25% "Claude Use" showcase AND the credibility signal that wins the research prize.

## The named user
> *"A scientist in the Marson lab (or any T-cell immunologist) staring at a genome-scale
> Perturb-seq screen, who needs to decide which handful of the thousands of hits to actually
> validate at the bench next — and trust that shortlist."*

## What the tool is — "Target Triage"
A Claude Code agent mines the Marson CD4+ T-cell Perturb-seq to **rank druggable regulators of
T-cell activation/polarization**, then an **adversarial verifier agent tries to break each candidate**
before it reaches the shortlist. Output: a ranked, mechanism-annotated, scrutiny-survived shortlist
of targets worth validating — with the reasoning and the failed challenges shown. CLI + light web view.

---

## THE TWO UPGRADES THAT MAKE THIS A 10/10 CONCEPT (core, committed)
The base idea scores ~8: strong, but with two structural soft spots a judge can poke. These two
upgrades close them. Build them as CORE, not stretch.

### Upgrade 1 — The held-out prediction (kills "reproduction, not discovery")
Don't just output a ranked list — **make one falsifiable prediction and confirm it against a source
the tool never saw.** Concretely:
- Hold out an **independent validation source** the pipeline does NOT use as input: e.g. Open Targets
  (genetics + tractability), a SECOND published T-cell CRISPR screen (Shifrut/Marson 2018, Schmidt
  2022 CRISPRa screens), or drugs already in immune-disease trials.
- The tool surfaces a candidate → you **commit to the prediction on camera** → THEN reveal it is
  independently supported (in trials / hit in the other screen / high Open Targets score).
- Demo money-line: *"The tool flagged GENE-X with no knowledge of the clinic. It turns out GENE-X is
  in Phase II for [disease]. We predicted it; the world already agrees."*
- This converts "here's a list" into "we predicted X and were right" — the thing a judge remembers.

### Upgrade 2 — The computation-grounded verifier (kills "trust me, it's rigorous")
The skeptic agent must produce **numbers and external cross-checks a judge can re-run**, not LLM
opinions. Each candidate must survive:
- A **real statistic** the agent computes: donor-holdout (does the effect hold leaving each donor out?)
  and a **permutation / label-shuffle test** → an empirical p-value or robustness score.
- An **external database query** the agent runs: Open Targets / OpenCelliD / a druggable-genome list →
  independent evidence, not the model's memory.
- "Survived scrutiny" then MEANS: p < threshold across donor holdouts AND independent DB support.
  The verifier reports the number and the source, and REJECTS candidates that fail (must show teeth —
  keep the rejected ones visible in the output so judges see it actually kills things).

**Why these two, together, are the 10:** even a modest, honest execution now wins, because the finding
is genuinely novel (held-out confirmation) AND externally checkable (real stats + real DB) — nothing
soft left for a judge to poke. This is also the strongest possible "Claude Use": the agent orchestrates
real computation and real data, then reasons over the results.

### Stretch (only if ahead of schedule — do NOT let these threaten the demo)
- **Upgrade 3 — works on any screen:** abstract the loader so the tool re-runs live on a 2nd
  Perturb-seq dataset → "an instrument the lab keeps," not a one-off analysis.
- **Upgrade 4 — wet-lab experiment card:** emit a real experiment spec for the top pick (guide RNA,
  cell type, readout, predicted direction, rough cost/time) → the artifact a scientist acts on Monday.

## Architecture — the agent loop is the headline
1. **Ingest** (native, not toy): load pre-computed pseudobulk + DE tables
   (`GWCD4i.pseudobulk_merged.h5ad`, `GWCD4i.DE_stats.h5ad`) across Rest / Stim-8hr / Stim-48hr.
2. **Analysis agent** (Claude Code, fan-out over perturbations): for each knockdown, score its
   effect on T-cell activation/polarization programs (IL2, IFNG, cytokine gene sets) via
   decoupler/gseapy; flag context-dependent (resting vs stimulated) regulators.
3. **Overlay for novelty:** cross-reference hits against the **druggable genome** + **immune-disease
   GWAS** genes — surface targets the preprint did NOT emphasize. (This is the "advance the field" bit.)
4. **Adversarial verifier agent — computation-grounded (Upgrade 2):** for each top candidate, a second
   Claude agent tries to REFUTE it using REAL checks it runs, not opinions: (a) donor-holdout — does the
   effect survive leaving each donor out? (b) permutation/label-shuffle test → empirical p-value;
   (c) external DB query (Open Targets / druggable genome) for independent support. Reports the number +
   source. REJECTS failures and keeps them visible so the teeth are provable.
5. **Held-out confirmation (Upgrade 1):** for the top surviving pick, check it against an independent
   source the pipeline never used as input (Open Targets tractability, a 2nd T-cell CRISPR screen,
   trial status). This is the "we predicted it and were right" beat.
6. **Output:** shortlist with per-target report — mechanism, program affected, the p-value/robustness
   number, the external evidence, and the challenges it survived (plus the rejected ones). Reproducible
   from README.

## The finding (what wins the Gladstone Award)
- **Positive controls (must reproduce):** known T-cell regulators move known programs in the expected
  direction — IL2RA, CTLA4, PDCD1, FOXP3, TNFAIP3, and **RASA2** (a Marson-lab CAR-T target). If the
  tool recovers these, the verifier layer has teeth and the judges trust it instantly.
- **The novel bit:** 3–5 druggable, disease-linked regulators surfaced + scrutiny-survived, each a
  bench-testable hypothesis, ideally context-specific (a resting-vs-stimulated distinction the bulk
  analyses miss).
- **Reproducible:** README lets a stranger re-run and get the same shortlist.

Finding statement shape (for summary + video):
> "Across the genome-scale CD4+ T-cell screen, Target Triage reproduces known regulators
> (RASA2, IL2RA) as controls, then prioritizes N druggable, disease-linked candidates — each one
> having survived a permutation test, donor-holdout, and an independent database cross-check. Our top
> pick, GENE-X, was flagged with no knowledge of the clinic — and is already in [trials/another screen].
> We predicted it; the evidence agrees."

---

## Data & tools — VERIFIED 2026-07-07 by direct S3 inspection (CORRECTS earlier assumptions)
- **Preprint:** Zhu et al., bioRxiv Dec 2025 — https://www.biorxiv.org/content/10.64898/2025.12.23.696273v1
- **Analysis code:** https://github.com/emdann/GWT_perturbseq_analysis_2025
- **Bucket (public, no auth):** `s3://genome-scale-tcell-perturb-seq/marson2025_data/` — listable over plain HTTPS:
  `https://genome-scale-tcell-perturb-seq.s3.amazonaws.com/?list-type=2&prefix=marson2025_data/`
  (no aws CLI needed; download any object with `curl https://genome-scale-tcell-perturb-seq.s3.amazonaws.com/<key>`).

### ⚠️ CORRECTION: the .h5ad files are NOT laptop-friendly
The earlier plan said "grab the pre-computed pseudobulk, GB-scale, laptop-friendly." WRONG. Verified sizes:
- `GWCD4i.pseudobulk_merged.h5ad` = **44.6 GB** · `GWCD4i.DE_stats.h5ad` = **16.8 GB**
- per-donor raw cell files `D{1-4}_{Rest,Stim8hr,Stim48hr}.assigned_guide.h5ad` = **~120–170 GB EACH** (1.84 TB total).
These do NOT load on a laptop. DO NOT plan to download them.

### ✅ THE REAL DATA PATH: three small supplementary CSVs = the whole tool
Under `marson2025_data/suppl_tables/` (downloaded + inspected, all tiny):
- **`DE_stats.suppl_table.csv` (4.6 MB) — THE CORE TABLE.** 33,983 rows = one perturbation × condition.
  Columns: `target_contrast_gene_name` (gene symbol), `culture_condition` (Rest/Stim8hr/Stim48hr),
  `target_contrast` (Ensembl ID), `n_cells_target`, `n_up_genes`, `n_down_genes`, `n_total_de_genes`,
  `ontarget_effect_size` (knockdown strength, negative = KD), `ontarget_significant` (bool),
  `offtarget_flag`, `ontarget_effect_category`, `n_downstream` (# downstream DE genes = effect breadth).
  11,526 unique target genes; 21,216 rows with significant on-target KD; ~11.3k rows per condition.
- **`sgrna_library_metadata.suppl_table.csv` (9.5 MB)** — 26,504 guides; guide→gene map + genomic context
  (`sgRNA`, `target_gene_name`, `target_gene_id`, TSS distance, nearby genes, off-target alignment).
- **`sample_metadata.suppl_table.csv` (<1 MB)** — 12 samples (4 donors × 3 conditions) with donor demographics.
- NOTE (corrected): the README mentions `Th1Th2_validation_summary.suppl_table.csv`, but that file is NOT in
  the S3 bucket (only 3 CSVs are). The robustness + held-out data we actually use comes from the emdann GitHub
  repo instead (see the "PROTOTYPE VALIDATED" section below) — a better source.

### IMPORTANT NUANCE about DE_stats.csv
This suppl table is a per-perturbation SUMMARY (counts of DE genes + on-target effect), NOT the full
per-gene log-fold-change matrix. The full per-gene DE z-scores live in the 16.8 GB `DE_stats.h5ad`
(n_obs=33,983 perturbations × n_vars=10,282 genes). DECISION NEEDED: for gene-PROGRAM scoring (IL2/IFNG
signatures) we need per-gene z-scores → either (a) stream/subset the big h5ad remotely (hdf5 partial read
over S3, or download once to a workstation), or (b) build v1 on the summary table alone (rank by
n_downstream / effect breadth + on-target significance), which is enough for a working demo + controls.
Recommend: v1 on the CSV (fast, laptop-native), then layer per-gene program scores if time/compute allow.

- **Local libs status (this machine):** python3 = /opt/miniconda3 present; anndata/scanpy/mudata NOT installed;
  aws CLI NOT installed. v1 (CSV + repo tables + Open Targets over stdlib urllib) needs NO extra installs —
  the prototype ran on stdlib only. `pip install scanpy anndata mudata decoupler gseapy scipy` is ONLY needed
  if we later reach into the per-gene 16.8 GB h5ad for program scoring (stretch).
- **For the two upgrades (VALIDATED sources):** Open Targets GraphQL (free, no key — batched+cached in
  `prototype/ot_cache.json`); cross-donor/cross-guide robustness from the emdann repo (`data/robustness/`);
  held-out corroboration from Schmidt2022 + Freimer2022 (`data/external_screens/`). Permutation test remains
  a stretch (needs per-gene z-scores from the big h5ad).

## VERIFIED control biology (queried DE_stats.csv 2026-07-07) — the truth-test WORKS
All anchors present with sensible, strong signals (on-target effect size; effect breadth `n_downstream`):
- **RASA2**: sig all 3 conditions, effect ~-14 (strong KD), broad downstream (Rest n=699). Best control — WORKS.
- **CD5**: strongest effects (eff ~-25 to -28), huge downstream at Stim48hr (1200). Great high-signal control.
- **CBLB**: massive context-dependence — Stim8hr n_downstream=1027 vs Rest=5. Perfect "context-specific" story.
- **IL2RA, FOXP3, SOCS1, DGKA, DGKZ, TNFAIP3, CTLA4, PDCD1**: all present, sig, expected direction.
- TCEB2 not found under that symbol (curated as ELOB — check alias). Note: gene symbols, not Ensembl, in the name col.
- Sanity: top perturbations by downstream breadth in Stim8hr = TCR-proximal machinery (CD3E/D/G, LAT, ZAP70,
  PLCG1, LCP2, VAV1) — exactly the known TCR signalosome. The data behaves correctly; a naive ranking already
  surfaces real T-cell biology. NOVELTY must therefore come from the druggable+disease overlay, not raw ranking.

## PROTOTYPE VALIDATED (2026-07-07) — the whole pipeline proven on real data, laptop-native
A pre-kickoff prototype (in `prototype/`, throwaway — NOT the submission) ran the full pipeline end-to-end
on real public data. Everything below is confirmed by running code, not assumed.

### The tool is FULLY laptop-native — v1 needs NO big files
The entire v1 (ranking + 3-tier verifier) runs on tiny public tables. The 44 GB / 16.8 GB / 1.84 TB
`.h5ad` files are NOT needed. Data now lives in the repo under `data/` (see `data/README.md`):
- `data/marson_perturbseq/DE_stats.suppl_table.csv` (4.6 MB) — core ranking + Tier-A checks
- `data/robustness/` — precomputed cross-donor + cross-guide correlations (from emdann repo, < 1 MB) — Tier B
- `data/external_screens/` — Schmidt2022 + Freimer2022 independent CRISPR screens — Tier C3 held-out check

### The ranking (v1) — validated
5 steps: gate (significant on-target KD only) → score (effect breadth `n_downstream`, log1p, ×KD strength)
→ fold across 3 conditions (+ context-specificity) → **Open Targets overlay** → rank. Findings:
- Open Targets GraphQL schema verified live: `tractability` (SM/AB/PR modalities) + `associatedDiseases`
  `datatypeScores[genetic_association]`. `knownDrugs` is NOT a field (now `drugAndClinicalCandidates`).
- Disease score MUST be filtered to immune-relevant diseases — raw "best genetic_association" surfaces
  rare developmental syndromes (CREBBP/NSD1/TRIP12). Immune filter fixed this; a real version should use
  EFO ontology descendants of "immune system disease" rather than keyword matching.
- NO raw-impact pre-filter — annotate the FULL significant set (~7,195 genes, batched+cached ~3 min).
  Pre-filtering by impact discards the exact modest-impact-but-druggable genes the overlay exists to rescue.
- The overlay works: **PTPN2 raw #812 → actionable #45; CBLB raw #186 → #26; CD3E (obvious) #46 → #1405.**

### The 3-tier adversarial verifier — VALIDATED, has real teeth
Each check is a computed pass/fail + number a judge can re-run, NOT an LLM opinion. Tiered to available data:
- **Tier A** (from the 4.6 MB CSV): A1 real_knockdown, A2 not_offtarget, A3 enough_cells [gates];
  A4 cross_condition [score].
- **Tier B** (from the repo robustness tables — REPLACES the 16.8 GB h5ad): B1 donor_robustness,
  B2 cross_guide [gates on cross-donor / cross-guide Pearson correlation ≥ 0.10].
- **Tier C** (external): C1 disease_is_immune, C2 druggable_handle [Open Targets]; C3 held_out_screen
  [BONUS — corroboration by Schmidt2022 / Freimer2022].
Verdict = REJECT if any gate fails; else PROMOTE / PROMOTE (weak) / PROMOTE (corroborated). Proven behavior:
- A1BG (noise) → REJECT. DGKA/PTPN22 (strong KD, 0/3 downstream reproduction) → PROMOTE (weak).
- Heroes → PROMOTE (corroborated) with real numbers (CBLB donor 0.75 / guide 0.92; PTPN2 0.59 / 0.86).
- The checks emit numbers; the real tool's **Claude agent turns them into the verdict + narrative** (the 25% Claude Use).

### HERO DECISION (locked): CBLB is the primary demo hero; PTPN2 is secondary
The data chose CBLB — it lights up EVERY tier:
- Ranking: raw #186 → actionable #26 (druggable + MS genetics). Context-specific (Stim8hr 1027 vs Rest 5 downstream).
- Verifier: PROMOTE (corroborated) — donor 0.75, guide 0.92, AND independent-screen hits in 2 Schmidt readouts.
- Clinical beat: NX-1607 (Nurix) in Phase 1a/1b with clinical activity → the "already in the clinic" line is real.
- **PTPN2 caveat (important):** genuine hit (top ~12%, druggable phosphatase, T1D genetics, ABBV-CLS-484 Phase 1)
  BUT it is NOT a hit in Schmidt2022 or Freimer2022 — so its held-out evidence is CLINICAL + Open Targets
  genetics, not screen-based. Lead the demo with CBLB (screen-corroborated); use PTPN2 as the "modest raw
  impact rescued by the overlay" story, backed by its Phase-1 drug rather than an independent screen.
- Other strong actionable hits surfaced (real immunology, not syndrome noise): NRAS, NFKB2, STAT3, PTPRC,
  MALT1 (approved), STAT6, INPP5D, RIPK1, PSMB8/9.

## THE RISKS — de-risk in P0/P1
1. **Don't fall into "reproduction, not advance."** The preprint is thorough. Novelty MUST come from a
   specific overlay (druggable genome + disease GWAS) or a context-specific angle. Lock the exact
   novel question on Day 0.
2. **`.h5mu` / MuData handling has a learning curve** — prove you can load the pseudobulk + DE tables
   and pull one perturbation's DE on Day 0.
3. **Scope the input.** Work from pre-computed pseudobulk/DE, not the 22M-cell raw object. Never plan
   anything needing a cluster.
4. **The verifier agent must have real teeth** — if it never rejects anything, judges see through it.
   Design concrete refutation checks (donor-robustness, control-direction, context-confound).

## Validation anchors (your truth set)
| Regulator | Expected effect | Note |
|---|---|---|
| IL2RA | activation program | canonical T-cell |
| CTLA4 / PDCD1 | checkpoint / activation | immunotherapy targets |
| FOXP3 | Treg program | lineage master regulator |
| RASA2 | enhanced activation | Marson-lab CAR-T target — best control |
| TNFAIP3 | NF-kB / activation | autoimmune GWAS gene |

## LOCKED BUILD DECISIONS (2026-07-07)
- **v1 = CSV-only.** Rank on `DE_stats.suppl_table.csv` (effect breadth `n_downstream` + `ontarget_significant`
  + context Rest/Stim8hr/Stim48hr) × the druggable + immune-GWAS overlay. Fully laptop-native, no big download,
  controls already verified. Per-gene program scoring (from the 16.8 GB h5ad) is STRETCH only.
- **Held-out reveal target = PTPN2.** Verified in data: sig all 3 conditions, broad downstream (Rest n=413),
  top ~12% of significant Stim8hr perturbations by breadth. Druggable phosphatase + autoimmune GWAS + KO-enhances
  + already Phase 1 (ABBV-CLS-484). The "flagged blind → already in the clinic" beat is REAL.
- **Narrative nuance (verified):** raw effect-breadth ranking alone surfaces the TCR signalosome (reproduction).
  PTPN2 is top-12%, not #1 — it's the DRUGGABLE+DISEASE OVERLAY that floats PTPN2/CBLB/TNFAIP3 to the top of the
  *actionable* list. This IS the novelty argument; the demo should show raw-rank vs overlay-rank side by side.
- **Two-target demo option:** CBLB (top 2.4% breadth, huge context-dependence, NX-1607 Phase 1a/1b) as the big
  obvious win the tool nails; PTPN2 as the subtler blind prediction. Shows power AND discernment.
- The 3 suppl CSVs are already downloaded to `scratchpad/marson_data/` (DE_stats.csv, sgrna_library.csv,
  sample_metadata.csv) — usable immediately for prototyping the ranking logic before kickoff.

## Backup datasets (if Marson wrangling slips)
- **Pollard HAR/Zoonomia** — trivial data (BEDs + bedtools), guaranteed same-day result, but higher
  novelty risk. Safe fallback.
- **Corces ChromBPNet** — strong controls (BIN1→microglia) via vep.corces.gladstone.org, but wrapper risk.
