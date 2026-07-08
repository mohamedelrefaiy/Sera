# Built with Claude: Life Sciences — My Plan

**Track:** Builder · **Team:** Solo · **Bandwidth:** Heavy but not full-time
**Strength:** Strong in genomics / computational biology
**PRIMARY GOAL: The Gladstone Institutes Award ($10k) + a Builder-track prize — dual target.**
**SUBMISSION DEADLINE: Monday, July 13, 2026 — 9:00 PM ET** (submit early evening, not the last hour)

---

## Strategy: a Builder tool whose OUTPUT is a real Gladstone finding

Stay in the **Builder track** (working software, named user) BUT make the tool's output a
**reproducible scientific finding on a Gladstone dataset** — so the same project competes for
both a Builder prize AND the Gladstone Award.

**The Gladstone Award** is hand-selected by Gladstone scientists for the project with "the most
potential to advance the field." It rewards research merit, not a slick UI. So the tool is the
vehicle; the *finding* is what wins it. The tool must be genuinely usable AND its result must be
something a Gladstone scientist would take seriously.

### Failure mode to avoid: falling between two stools
- Too thin to be a real finding → loses Gladstone.
- A finding with no usable software → loses Builder.
The plan forces BOTH to a defensible bar.

### DATASET LOCKED: Marson CD4+ T-cell Perturb-seq (decided 2026-07-06, after 4-agent research)
Full architecture, the finding, and risk analysis live in **ARCHITECTURE.md**. In short:
- **Tool — "Target Triage":** a Claude Code agent mines the genome-scale CD4+ T-cell Perturb-seq to
  rank druggable regulators of T-cell activation, and an **adversarial verifier agent tries to break
  each candidate** before it reaches a wet-lab shortlist.
- **Named user:** *a Marson-lab (or any T-cell) immunologist deciding which handful of thousands of
  screen hits to validate at the bench next — and trust that shortlist.*
- **The finding:** reproduce known controls (RASA2, IL2RA, CTLA4) then surface 3–5 druggable,
  disease-linked, scrutiny-survived candidates. Reproducible from README.
- **Claude Code as the engine:** analysis agent fans out over perturbations → scores gene programs →
  overlays druggable-genome + GWAS → **adversarial verifier agent refutes weak hits** → writes report.
- **Why not Corces:** the Corces lab already ships a variant-effect web app (vep.corces.gladstone.org),
  so building on ChromBPNet risks looking like a wrapper around the judges' own tool. Marson data is
  fresh, causal, GPU-free, laptop-friendly (pre-computed pseudobulk + DE tables).

**Why the verifier + adversarial agent loop is the headline:** it's the recurring hackathon-WINNING
pattern AND doubles as scientific rigor — it earns the 25% "Claude Use" and signals research
credibility to the Gladstone judges at the same time. One build, two criteria.

### The two upgrades that take the CONCEPT from 8/10 → 10/10 (core, committed)
The base idea is strong but has two soft spots a judge can poke. These close them — build as CORE:
1. **Held-out prediction** (kills "reproduction, not discovery"): the tool flags a target with no
   knowledge of the clinic, you commit on camera, THEN reveal it's independently supported (Open
   Targets / a 2nd T-cell CRISPR screen / already in trials). "We predicted it and were right."
2. **Computation-grounded verifier** (kills "trust me, it's rigorous"): the skeptic agent reports a
   REAL number (permutation test + donor-holdout p-value) and a REAL external DB cross-check
   (Open Targets), not an LLM opinion — so "survived scrutiny" is something a judge can re-run.
Together: even a modest honest build wins, because the finding is novel AND externally checkable.
Stretch only if ahead: (3) works on any screen — re-run live on a 2nd dataset; (4) wet-lab experiment card.

Backup datasets if Marson wrangling slips: Pollard Zoonomia/HARs (trivial data, safe) or Corces.

### Winning habits the research surfaced (bake these in)
- **Eval/controls FIRST.** Build the positive-control check (RASA2/IL2RA move the right programs) as
  the thing your tool must pass BEFORE building the tool around it. Winners spec + eval before coding.
- **Domain depth > coding polish.** 4 of 5 recent winners were non-developers solving a problem they
  understood. Your comp-bio strength is the moat.
- **Commit incrementally from July 7** (a single end-of-week bulk commit draws DQ suspicion).
- **"Explain why," never just a score** — all 3 labs named interpretability as the unmet need.
- **Honest about limitations** (donor-robustness, context confounds) signals credibility to scientists.

## Idea filter (score any candidate /25; cut below ~18)
| Filter | Question |
|---|---|
| Nameable user | Can you describe the exact person (ideally a Gladstone scientist) in one sentence? |
| Demo-able in 3 min | Can you show the whole value in 180 seconds? |
| Solo-buildable in 6 days | Can one person finish a working version? |
| Non-trivial Claude use | Does Claude do multi-step / agentic work (runs the analysis, not just chats)? |
| **Real finding** | **Would a Gladstone scientist call the output a credible, reproducible result?** |

---

## Judging (Builder) — what to optimize
- **Demo — 30%** (largest single weight): working, compelling, watchable 3-min video.
- **Impact — 25%**: real user, real benefit, fits the problem statement.
- **Claude Use — 25%**: creative Claude Code use — agents/skills/MCP, surprise the judges. NOT a basic wrapper.
- **Depth & Execution — 20%**: pushed past first idea, sound engineering, real craft.

## Rules (do not violate)
- **New work only** — start from scratch during the hackathon. (Planning/reading before kickoff = OK.)
- **Fully open source** — backend, frontend, models, all of it, approved license (MIT).
- Team size max 2.
- No code/data/assets you don't have rights to.

## Required to submit
- 3-minute demo video (YouTube/Loom)
- GitHub repo (public, open source)
- Written summary (100–200 words)

---

## Timeline (backward-planned from the deadline)

| Phase | When | Definition of Done |
|---|---|---|
| **P0 · Prep + de-risk data** | Mon Jul 6 (today) | Setup/reading only (no submission code). Download the pre-computed Marson pseudobulk + DE tables (`aws s3 --no-sign-request`), and PROVE you can load them + pull one perturbation's DE. Skim the Zhu 2025 preprint + the emdann analysis repo so you know what a real finding looks like. LOCK the novel question (druggable-genome + GWAS overlay). List the control regulators (RASA2, IL2RA, CTLA4, FOXP3, TNFAIP3). Read Claude Agent SDK / skills docs (the agent loop is the headline). Discord joined + role requested. |
| **P1 · Kickoff + eval-first scaffold** | Tue Jul 7 (12PM kickoff) | Watch kickoff. git init fresh repo (first legal code, MIT). **Build the CONTROL EVAL FIRST:** a check that scores whether known regulators move known programs in the expected direction. Then the skeleton analysis that makes one control pass. Commit incrementally. |
| **P2 · Analysis agent works end-to-end** | Wed Jul 8 | Claude Code analysis agent fans out over perturbations → scores gene programs → ranks candidates. Controls (RASA2/IL2RA) reproduce. Attend Claude Science session 12PM. |
| **P3 · Computation-grounded verifier + tool (Upgrade 2)** | Thu Jul 9 | Add the ADVERSARIAL VERIFIER that reports REAL numbers, not opinions: permutation/label-shuffle test + donor-holdout → empirical p-value, AND an Open Targets DB cross-check. It must REJECT failures and keep them visible (teeth). Wrap in a real UI/CLI a scientist runs untouched. Output = shortlist + per-target report (p-value, external evidence, challenges survived + rejected). Reproducible README. |
| **P4 · Held-out prediction + validate (Upgrade 1)** | Fri Jul 10 | Add the druggable-genome + immune-GWAS overlay for non-obvious candidates. THEN the 10/10 beat: confirm the top pick against an independent source the pipeline never used (Open Targets tractability / a 2nd T-cell CRISPR screen / trial status) — "we predicted it and were right." Harden: honest limitations. Gladstone session 12PM; office hours 5PM. |
| **P5 · Polish + FREEZE** | Sat Jul 11 | Fix demo path only. README with exact reproduction steps. Written summary (100–200w) stating the FINDING (controls reproduced + N candidates surviving scrutiny). Feature freeze Sat night. |
| **P6 · Demo video** | Sun Jul 12 | Script + record + edit 3-min video: named user → problem → live run → **verifier catching a bad hit (with a real p-value)** → **the held-out reveal: "we flagged GENE-X blind, it's already in trials"** → why it matters → reproducibility. Lead with the result. Full dry-run submission. Submittable Sun night. |
| **P7 · BUFFER + submit** | Mon Jul 13 | Fix breakage. Final submit by early evening (deadline 9PM ET). |

### Four rules that win this (dual-target)
1. **The output must be a finding, not a feature.** Gladstone judges research merit. Every phase asks: would a Gladstone scientist trust this result?
2. **Reproducibility is a first-class deliverable.** A README that lets a stranger re-run the analysis is what makes it "advance the field." Bake it in by P3, don't bolt it on.
3. **Submit Sunday-complete, polish Monday.** Monday is insurance, not build time.
4. **Video = a full day**, and it must land BOTH stories: the usable tool (Builder) AND the finding (Gladstone). It's 30% and the only thing round-1 judges see.

---

## Key event schedule (all ET)
- Tue Jul 7: 12PM kickoff · 12:30PM hacking begins · 5–6PM office hours
- Wed Jul 8: 12–1PM Claude Science overview (Alexander Tarashansky) · 5–6PM office hours
- Thu Jul 9: 5–6PM office hours
- Fri Jul 10: 12–1PM Gladstone session (Sukrit Silas) · 5–6PM office hours
- Sat/Sun Jul 11–12: hacking
- **Mon Jul 13: 9PM submission deadline**
- Jul 14–15: async judging · Jul 16: 12PM top 6 announced, 1:30PM closing / top 3

## Resources to read (P0)
- **Claude Science Get Started + Docs** (this is your engine — read first)
- Claude Code Best Practices · Building Agents with the Claude Agent SDK · Building Effective Agents
- Claude Code / Claude API docs · MCP Docs · Agent Skills Docs
- **The source lab's paper/preprint for your chosen dataset** — so you know what a real finding looks like:
  - Corces: Corces Resources page (pre-trained brain models) + ChromBPNet docs; https://docpollard.org for Pollard
  - Marson: CD4+ T cell Perturb-seq code + preprint (linked in the guide)
  - Pollard: Zoonomia constraint scores + Human Accelerated Regions
- Discord: https://anthropic.com/discord (request Hackathon Participant role in #hackathon-access)

## Gladstone labs (your judges' world)
- **M.R. Corces** (Neurological) — neurodegeneration genomics (Alzheimer's/Parkinson's), ATAC-seq, ChromBPNet
- **Alex Marson** (Genomic Immunology) — T-cell circuits, CRISPR, immune gene editing
- **Katie Pollard** (Data Science) — genome evolution/regulation, comparative genomics, statistics
- Gladstone = 5 institutes (Cardiovascular, Data Science & Biotech, Genomic Immunology, Infectious Disease, Neurological), 30+ labs, 600 scientists.
